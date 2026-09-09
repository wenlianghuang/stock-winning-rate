"""Phase 2 orchestrator: golden intents, policy, and mocked execution."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.orchestrator import (  # noqa: E402
    AgentContext,
    Plan,
    PlanStep,
    apply_policy,
    build_plan,
    classify_intent,
    format_run,
    main as agent_main,
    plan_from_dict,
    run_agent,
)
from agent.policy import (  # noqa: E402
    DIGEST_PENDING,
    INTENT_MARKET_OUTLOOK,
    INTENT_STOCK_DECISION,
    allow_position,
    allow_send_digest,
    needs_fetch_before,
    validate_plan_schema,
)
from agent.tools._types import EXIT_CSV_MISSING, EXIT_FAILED, EXIT_OK  # noqa: E402
from main import main as cli_main  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "agent"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _plan_from_fixture(payload: dict) -> Plan:
    classified = classify_intent(payload["intent"])
    ctx = payload["context"]
    return build_plan(
        classified,
        trade_date=ctx["trade_date"],
        holdings=ctx["holdings"],
        csv_missing=set(ctx["csv_missing"]),
        daily_facts_present=bool(ctx["daily_facts_present"]),
    )


def _ok(tool: str, **extra):
    payload = {"ok": True, "exit_code": EXIT_OK, "error": None}
    payload.update(extra)
    return payload


class GoldenIntentFixtureTests(unittest.TestCase):
    def _assert_fixture(self, filename: str) -> None:
        payload = _load_fixture(filename)
        classified = classify_intent(payload["intent"])
        self.assertEqual(classified.kind, payload["expect_intent_kind"])
        plan = _plan_from_fixture(payload)
        tools = [step.tool for step in plan.steps]
        self.assertEqual(tools, payload["expect_tools"])
        for forbidden in payload["forbid_tools"]:
            self.assertNotIn(forbidden, tools)
        self.assertEqual(plan.digest_status, payload["expect_digest_status"])
        notes = " ".join(plan.notes)
        for snippet in payload["expect_notes_any"]:
            self.assertIn(snippet, notes)

    def test_process_holdings_fixture(self) -> None:
        self._assert_fixture("process_holdings.json")

    def test_stock_without_holding_fixture(self) -> None:
        self._assert_fixture("stock_no_holding.json")
        plan = _plan_from_fixture(_load_fixture("stock_no_holding.json"))
        notes = " ".join(plan.notes)
        self.assertIn("2330", notes)
        self.assertTrue(any("不跑" in note and "position" in note for note in plan.notes))

    def test_market_outlook_fixture(self) -> None:
        self._assert_fixture("market_outlook.json")

    def test_market_outlook_reuses_existing_daily(self) -> None:
        classified = classify_intent("明天開盤怎麼看")
        plan = build_plan(
            classified,
            trade_date="2026-08-18",
            holdings={},
            csv_missing=set(),
            daily_facts_present=True,
        )
        self.assertEqual([step.tool for step in plan.steps], ["answer_market_chat"])
        self.assertTrue(any("不重跑" in note for note in plan.notes))


class PolicyTests(unittest.TestCase):
    def test_no_position_without_holding(self) -> None:
        allowed, reason = allow_position("2330", {"2409": {"avg_cost": 32.5, "shares": 500}})
        self.assertFalse(allowed)
        self.assertIn("2330", reason)
        self.assertIn("無持倉", reason)

    def test_send_digest_blocked_by_default(self) -> None:
        allowed, reason = allow_send_digest(approved=False)
        self.assertFalse(allowed)
        self.assertIn("blocked", reason)

    def test_needs_fetch_before_research(self) -> None:
        self.assertTrue(needs_fetch_before("run_report_gate", "2409", {"2409"}))
        self.assertFalse(needs_fetch_before("run_report_gate", "2409", set()))
        self.assertFalse(needs_fetch_before("answer_market_chat", "2409", {"2409"}))

    def test_policy_strips_position_and_self_approved_send(self) -> None:
        plan = Plan(
            intent="2330 要不要動",
            intent_kind=INTENT_STOCK_DECISION,
            trade_date="2026-08-18",
            steps=[
                PlanStep(
                    tool="run_position_gate",
                    args={"stock_id": "2330"},
                    reason="llm guessed",
                ),
                PlanStep(
                    tool="send_digest",
                    args={
                        "digest_date": "2026-08-18",
                        "subject": "x",
                        "main_detail_markdown": "y",
                        "approved": True,
                    },
                    reason="llm self-approve",
                ),
            ],
        )
        patched = apply_policy(plan, holdings={}, csv_missing=set(), approve_send=False)
        self.assertEqual(patched.steps, [])
        notes = " ".join(patched.notes)
        self.assertIn("無持倉", notes)
        self.assertIn("blocked", notes)
        self.assertEqual(patched.digest_status, DIGEST_PENDING)

    def test_policy_inserts_fetch_when_csv_missing(self) -> None:
        plan = Plan(
            intent="2330 要不要動",
            intent_kind=INTENT_STOCK_DECISION,
            trade_date="2026-08-18",
            steps=[
                PlanStep(
                    tool="run_report_gate",
                    args={"stock_id": "2330", "trade_date": "2026-08-18"},
                    reason="research",
                )
            ],
        )
        patched = apply_policy(plan, holdings={}, csv_missing={"2330"})
        self.assertEqual(patched.steps[0].tool, "fetch_chips")
        self.assertEqual(patched.steps[0].args["stocks"], ["2330"])
        self.assertEqual(patched.steps[1].tool, "run_report_gate")

    def test_plan_schema_rejects_unknown_tool(self) -> None:
        errors = validate_plan_schema(
            {
                "intent_kind": INTENT_MARKET_OUTLOOK,
                "steps": [{"tool": "rm_rf", "args": {}}],
            }
        )
        self.assertTrue(errors)
        self.assertTrue(any("rm_rf" in item for item in errors))
        with self.assertRaises(ValueError):
            plan_from_dict(
                {
                    "intent_kind": INTENT_MARKET_OUTLOOK,
                    "steps": [{"tool": "rm_rf", "args": {}}],
                }
            )


class ExecuteWithFakeDispatchTests(unittest.TestCase):
    def _dispatch(self, calls: list, csv_fail_once: bool = False):
        failed = {"report": csv_fail_once}

        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "fetch_chips":
                return _ok("fetch_chips", csv_paths=["/tmp/tw_stock_2409.csv"], trade_date="2026-08-18")
            if tool == "run_report_gate":
                if failed["report"]:
                    failed["report"] = False
                    return {
                        "ok": False,
                        "exit_code": EXIT_CSV_MISSING,
                        "error": "CSV 不存在",
                        "stock_id": args.get("stock_id"),
                    }
                return _ok(
                    "run_report_gate",
                    stock_id=args.get("stock_id"),
                    trade_date="2026-08-18",
                    md_path="/tmp/missing.md",
                )
            if tool == "run_position_gate":
                return _ok(
                    "run_position_gate",
                    stock_id=args.get("stock_id"),
                    md_path="/tmp/missing_position.md",
                )
            if tool == "draft_digest":
                self.assertTrue(args.get("items"))
                return _ok("draft_digest", subject="2026-08-18 台股籌碼日報｜fixture")
            if tool == "run_market_daily":
                return _ok("run_market_daily", trade_date="2026-08-18", md_path="/tmp/daily.md")
            if tool == "answer_market_chat":
                return _ok("answer_market_chat", reply="偏多但不要追高")
            if tool == "send_digest":
                self.fail("send_digest must not be dispatched")
            raise AssertionError(f"unexpected tool {tool}")

        return fake

    def test_process_holdings_executes_report_position_draft_not_send(self) -> None:
        calls: list = []
        ctx = AgentContext(
            intent="幫我處理今天持股",
            trade_date="2026-08-18",
            holdings={"2409": {"avg_cost": 32.5, "shares": 500000}},
            csv_missing={"2409"},
            daily_facts_present=False,
        )
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls))
        tools = [step.tool for step in run.steps if not step.skipped]
        self.assertEqual(
            tools,
            ["fetch_chips", "run_report_gate", "run_position_gate", "draft_digest"],
        )
        self.assertTrue(run.ok)
        self.assertEqual(run.digest_status, DIGEST_PENDING)
        self.assertNotIn("send_digest", tools)
        report = next(step for step in run.steps if step.tool == "run_report_gate")
        position = next(step for step in run.steps if step.tool == "run_position_gate")
        self.assertTrue(report.gate_passed)
        self.assertTrue(position.gate_passed)
        self.assertIn("待核准", " ".join(run.notes))

    def test_stock_without_holding_skips_position(self) -> None:
        calls: list = []
        ctx = AgentContext(
            intent="2330 要不要動",
            trade_date="2026-08-18",
            holdings={"2409": {"avg_cost": 32.5, "shares": 500000}},
            csv_missing={"2330"},
            daily_facts_present=False,
        )
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls))
        tools = [tool for tool, _args in calls]
        self.assertIn("fetch_chips", tools)
        self.assertIn("run_report_gate", tools)
        self.assertNotIn("run_position_gate", tools)
        self.assertTrue(any("2330" in note and "無持倉" in note for note in run.notes))
        self.assertIn("PASS", format_run(run))

    def test_market_outlook_runs_daily_then_chat(self) -> None:
        calls: list = []
        ctx = AgentContext(
            intent="明天開盤怎麼看",
            trade_date="2026-08-18",
            holdings={},
            csv_missing=set(),
            daily_facts_present=False,
        )
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls))
        self.assertEqual(
            [step.tool for step in run.steps],
            ["run_market_daily", "answer_market_chat"],
        )
        self.assertTrue(run.ok)
        self.assertEqual(run.digest_status, "not_applicable")

    def test_csv_missing_retries_fetch_once(self) -> None:
        calls: list = []
        ctx = AgentContext(
            intent="2330 要不要動",
            trade_date="2026-08-18",
            holdings={},
            csv_missing=set(),
            daily_facts_present=False,
        )
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls, csv_fail_once=True))
        tools = [tool for tool, _args in calls]
        self.assertEqual(tools.count("run_report_gate"), 2)
        self.assertEqual(tools.count("fetch_chips"), 1)
        self.assertTrue(run.ok)

    def test_tool_budget_stops_run(self) -> None:
        calls: list = []
        ctx = AgentContext(
            intent="幫我處理今天持股",
            trade_date="2026-08-18",
            holdings={"2409": {"avg_cost": 32.5, "shares": 500000}},
            csv_missing={"2409"},
            max_tool_calls=1,
        )
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls))
        self.assertFalse(run.ok)
        self.assertEqual(run.exit_code, EXIT_FAILED)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any("budget" in (step.skip_reason or "") for step in run.steps))

    def test_failed_report_skips_position(self) -> None:
        calls: list = []

        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "fetch_chips":
                return _ok("fetch_chips", csv_paths=["/tmp/x.csv"])
            if tool == "run_report_gate":
                return {"ok": False, "exit_code": EXIT_FAILED, "error": "gate", "stock_id": "2409"}
            if tool == "run_position_gate":
                self.fail("position must not run after failed report")
            if tool == "draft_digest":
                return _ok("draft_digest", subject="draft")
            raise AssertionError(tool)

        ctx = AgentContext(
            intent="幫我處理今天持股",
            trade_date="2026-08-18",
            holdings={"2409": {"avg_cost": 32.5, "shares": 500000}},
            csv_missing=set(),
        )
        run = run_agent(ctx.intent, ctx, dispatch=fake)
        self.assertFalse(run.ok)
        skipped = [step for step in run.steps if step.tool == "run_position_gate"]
        self.assertEqual(len(skipped), 1)
        self.assertTrue(skipped[0].skipped)


class CliTests(unittest.TestCase):
    def test_main_py_lists_agent(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = cli_main(["--list"])
        self.assertEqual(code, 0)
        self.assertIn("agent", buffer.getvalue())
        self.assertIn("orchestrator.py", buffer.getvalue())

    def test_dry_run_process_holdings_prints_plan(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = agent_main(
                [
                    "--dry-run",
                    "--date",
                    "2026-08-18",
                    "--",
                    "幫我處理今天持股",
                ]
            )
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("process_holdings", output)
        self.assertIn("run_report_gate", output)
        self.assertIn("run_position_gate", output)
        self.assertIn("draft_digest", output)
        self.assertIn("blocked", output)
        self.assertIn("dry-run", output)

    def test_dry_run_2330_explains_no_position(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = agent_main(["--dry-run", "--date", "2026-08-18", "2330 要不要動"])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("stock_decision", output)
        self.assertIn("run_report_gate", output)
        self.assertNotIn("run_position_gate  ", output.split("Plan:")[1].split("Notes:")[0])
        self.assertIn("無持倉", output)

    def test_plan_json_invalid_unknown_tool(self) -> None:
        buffer = io.StringIO()
        err = io.StringIO()
        with patch.object(sys, "stdout", buffer), patch.object(sys, "stderr", err):
            code = agent_main(
                [
                    "--plan-json",
                    str(FIXTURE_DIR / "bad_plan.json"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("plan JSON 無效", err.getvalue())

    def test_cli_json_dry_run_market(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = agent_main(
                ["--dry-run", "--json", "--date", "2026-08-18", "明天開盤怎麼看"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["intent_kind"], INTENT_MARKET_OUTLOOK)
        tools = [step["tool"] for step in payload["plan"]["steps"]]
        self.assertIn("answer_market_chat", tools)
        self.assertTrue(payload["dry_run"])


if __name__ == "__main__":
    unittest.main()
