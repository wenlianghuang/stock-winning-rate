"""Phase 3: role allowlist, handoffs, JSONL audit replay, send_digest stays off."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.audit import (  # noqa: E402
    AuditLog,
    format_replay,
    gate_rounds_from_payload,
    handoffs_from_gate_rounds,
    load_audit,
    parse_gate_log,
    replay_audit,
    summarize_args,
)
from agent.orchestrator import (  # noqa: E402
    AgentContext,
    Plan,
    PlanStep,
    apply_policy,
    main as agent_main,
    run_agent,
)
from agent.policy import (  # noqa: E402
    ALLOWED_TOOLS,
    DIGEST_PENDING,
    INTENT_MARKET_OUTLOOK,
    INTENT_PROCESS_HOLDINGS,
    ROLE_CHAT,
    ROLE_ORCHESTRATOR,
    ROLE_RESEARCH,
    ROLE_VALIDATOR,
    Permissions,
    allow_mutate_holdings,
    allow_position_handoff,
    allow_send_digest,
    broker_tools_exposed,
    notify_may_include,
    role_for_intent,
    tool_allowed_for_role,
)
from agent.tools._types import EXIT_FAILED, EXIT_OK  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "agent"


def _ok(tool: str, **extra):
    payload = {"ok": True, "exit_code": EXIT_OK, "error": None}
    payload.update(extra)
    return payload


def _holdings_ctx(**overrides) -> AgentContext:
    data = dict(
        intent="幫我處理今天持股",
        trade_date="2026-08-18",
        holdings={"2409": {"avg_cost": 32.5, "shares": 500000}},
        csv_missing=set(),
        audit_enabled=True,
    )
    data.update(overrides)
    return AgentContext(**data)


class AllowlistTests(unittest.TestCase):
    def test_chat_cannot_send_digest(self) -> None:
        allowed, reason = tool_allowed_for_role("send_digest", ROLE_CHAT)
        self.assertFalse(allowed)
        self.assertIn("chat", reason)
        blocked, send_reason = allow_send_digest(
            approved=True, permissions=Permissions(role=ROLE_CHAT, allow_send_digest=True)
        )
        self.assertFalse(blocked)
        self.assertIn("chat", send_reason)

    def test_chat_cannot_run_position(self) -> None:
        allowed, reason = tool_allowed_for_role("run_position_gate", ROLE_CHAT)
        self.assertFalse(allowed)
        self.assertIn("不允許", reason)

    def test_market_outlook_defaults_to_chat(self) -> None:
        self.assertEqual(role_for_intent(INTENT_MARKET_OUTLOOK), ROLE_CHAT)
        self.assertEqual(role_for_intent(INTENT_PROCESS_HOLDINGS), ROLE_ORCHESTRATOR)

    def test_apply_policy_strips_send_for_chat_role(self) -> None:
        plan = Plan(
            intent="明天開盤怎麼看",
            intent_kind=INTENT_MARKET_OUTLOOK,
            trade_date="2026-08-18",
            steps=[
                PlanStep(tool="answer_market_chat", args={"message": "x"}, reason="chat"),
                PlanStep(
                    tool="send_digest",
                    args={
                        "digest_date": "2026-08-18",
                        "subject": "x",
                        "main_detail_markdown": "y",
                        "approved": True,
                    },
                    reason="llm",
                ),
            ],
        )
        patched = apply_policy(
            plan,
            holdings={},
            csv_missing=set(),
            permissions=Permissions(role=ROLE_CHAT),
        )
        self.assertEqual([step.tool for step in patched.steps], ["answer_market_chat"])
        self.assertTrue(any("chat" in note and "send_digest" in note for note in patched.notes))

    def test_no_broker_tools_in_allowlist(self) -> None:
        self.assertFalse(broker_tools_exposed())
        self.assertNotIn("place_order", ALLOWED_TOOLS)
        denied, reason = allow_mutate_holdings()
        self.assertFalse(denied)
        self.assertIn("下單", reason)

    def test_send_permission_off_even_if_approved(self) -> None:
        allowed, reason = allow_send_digest(
            approved=True, permissions=Permissions(allow_send_digest=False)
        )
        self.assertFalse(allowed)
        self.assertIn("權限關閉", reason)
        self.assertIn("blocked", reason)


class HandoffPolicyTests(unittest.TestCase):
    def test_position_requires_research_facts(self) -> None:
        holdings = {"2409": {"avg_cost": 32.5, "shares": 500}}
        allowed, reason = allow_position_handoff(
            "2409", holdings, report_ok=True, facts_ready=False, skip_research=False
        )
        self.assertFalse(allowed)
        self.assertIn("facts", reason)

    def test_skip_research_allows_position(self) -> None:
        holdings = {"2409": {"avg_cost": 32.5, "shares": 500}}
        allowed, reason = allow_position_handoff(
            "2409", holdings, report_ok=False, facts_ready=False, skip_research=True
        )
        self.assertTrue(allowed)
        self.assertIn("skip_research", reason)

    def test_notify_only_passed(self) -> None:
        self.assertTrue(notify_may_include(report_passed=True))
        self.assertFalse(notify_may_include(report_passed=False))


class AuditLogTests(unittest.TestCase):
    def test_summarize_args_omits_markdown(self) -> None:
        summarized = summarize_args(
            {"stock_id": "2409", "markdown": "LONG TEXT", "items": [{"stock_id": "2409"}]}
        )
        self.assertEqual(summarized["stock_id"], "2409")
        self.assertIn("omitted", summarized["markdown"])
        self.assertEqual(summarized["items_count"], 1)
        self.assertNotIn("items", summarized)

    def test_parse_gate_log_and_handoffs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tw_stock_2409.gate.log"
            path.write_text(
                json.dumps(
                    {
                        "round": 1,
                        "passed": False,
                        "issue_codes": ["missing_stock_id"],
                        "issues": ["報告未提及股票代碼 2409"],
                    }
                )
                + "\n"
                + json.dumps({"round": 2, "passed": True, "issue_codes": []})
                + "\n",
                encoding="utf-8",
            )
            rounds = parse_gate_log(path)
            self.assertEqual(len(rounds), 2)
            self.assertEqual(rounds[0]["issue_codes"], ["missing_stock_id"])
            events = handoffs_from_gate_rounds(
                rounds, research_actor=ROLE_RESEARCH, stock_id="2409"
            )
            pairs = [(event.frm, event.to) for event in events]
            self.assertEqual(
                pairs,
                [
                    (ROLE_RESEARCH, ROLE_VALIDATOR),
                    (ROLE_VALIDATOR, ROLE_RESEARCH),
                    (ROLE_RESEARCH, ROLE_VALIDATOR),
                ],
            )
            self.assertEqual(events[0].issue_codes, ["missing_stock_id"])

    def test_gate_rounds_from_payload_prefers_inline(self) -> None:
        rounds = gate_rounds_from_payload(
            {
                "gate_rounds": [
                    {"round": 1, "passed": False, "issue_codes": ["missing_stock_id"]}
                ]
            }
        )
        self.assertEqual(rounds[0]["issue_codes"], ["missing_stock_id"])


class ExecutePhase3Tests(unittest.TestCase):
    def _dispatch(self, calls: list, *, report_ok: bool = True, with_rounds: bool = False):
        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "fetch_chips":
                return _ok("fetch_chips", csv_paths=["/tmp/tw_stock_2409.csv"])
            if tool == "run_report_gate":
                payload = _ok(
                    "run_report_gate",
                    stock_id=args.get("stock_id"),
                    trade_date="2026-08-18",
                    md_path="/tmp/missing.md",
                )
                if not report_ok:
                    payload = {
                        "ok": False,
                        "exit_code": EXIT_FAILED,
                        "error": "gate",
                        "stock_id": args.get("stock_id"),
                    }
                if with_rounds:
                    payload["gate_rounds"] = [
                        {
                            "round": 1,
                            "passed": False,
                            "issue_codes": ["missing_stock_id"],
                        },
                        {"round": 2, "passed": report_ok, "issue_codes": []},
                    ]
                return payload
            if tool == "run_position_gate":
                return _ok(
                    "run_position_gate",
                    stock_id=args.get("stock_id"),
                    md_path="/tmp/missing_position.md",
                )
            if tool == "draft_digest":
                self.assertTrue(args.get("items"))
                return _ok("draft_digest", subject="2026-08-18 台股籌碼日報｜fixture")
            if tool == "send_digest":
                self.fail("send_digest must not be dispatched")
            raise AssertionError(tool)

        return fake

    def test_process_holdings_stops_at_draft_when_send_permission_off(self) -> None:
        calls: list = []
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "run.jsonl"
            ctx = _holdings_ctx(
                csv_missing={"2409"},
                audit_path=audit_path,
                permissions=Permissions(allow_send_digest=False),
            )
            run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls, with_rounds=True))
        tools = [step.tool for step in run.steps if not step.skipped]
        self.assertEqual(
            tools,
            ["fetch_chips", "run_report_gate", "run_position_gate", "draft_digest"],
        )
        self.assertNotIn("send_digest", tools)
        self.assertEqual(run.digest_status, DIGEST_PENDING)
        self.assertTrue(any("權限關閉" in note or "待核准" in note for note in run.notes))
        pairs = [(event.frm, event.to) for event in run.handoffs]
        self.assertIn((ROLE_RESEARCH, ROLE_VALIDATOR), pairs)
        self.assertIn((ROLE_VALIDATOR, ROLE_RESEARCH), pairs)

    def test_audit_jsonl_is_replayable(self) -> None:
        calls: list = []
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "run.jsonl"
            ctx = _holdings_ctx(
                csv_missing={"2409"},
                audit_path=audit_path,
                run_id="fixture1",
            )
            run = run_agent(
                ctx.intent, ctx, dispatch=self._dispatch(calls, with_rounds=True)
            )
            self.assertTrue(audit_path.is_file())
            records = load_audit(audit_path)
            replay = replay_audit(records)
        self.assertEqual(run.run_id, "fixture1")
        self.assertEqual(replay["run_id"], "fixture1")
        self.assertIn("run_report_gate", replay["tools"])
        self.assertIn("validate_research", replay["tools"])
        self.assertIn("draft_digest", replay["tools"])
        self.assertFalse(replay["send_digest_in_log"])
        self.assertTrue(replay["digest_blocked"])
        issue_rounds = replay["issue_rounds"]
        self.assertTrue(
            any(item.get("issue_codes") == ["missing_stock_id"] for item in issue_rounds)
        )
        self.assertTrue(
            any(item.get("result") == "ok" and item.get("actor") == ROLE_VALIDATOR for item in issue_rounds)
        )
        text = format_replay(replay)
        self.assertIn("missing_stock_id", text)
        self.assertIn("send_digest:", text)

    def test_failed_report_not_sent_to_notify(self) -> None:
        calls: list = []

        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "run_report_gate":
                return {
                    "ok": False,
                    "exit_code": EXIT_FAILED,
                    "error": "gate",
                    "stock_id": "2409",
                    "gate_rounds": [
                        {"round": 1, "passed": False, "issue_codes": ["missing_stock_id"]}
                    ],
                }
            if tool == "run_position_gate":
                self.fail("position must not run after failed report")
            if tool == "draft_digest":
                self.fail("notify must not receive a failed report")
            raise AssertionError(tool)

        ctx = _holdings_ctx(audit_enabled=False)
        run = run_agent(ctx.intent, ctx, dispatch=fake)
        self.assertFalse(run.ok)
        self.assertTrue(any(step.tool == "run_position_gate" and step.skipped for step in run.steps))
        self.assertTrue(any(step.tool == "draft_digest" and step.skipped for step in run.steps))
        self.assertEqual(run.digest_status, "skipped")

    def test_skip_research_runs_position_without_report_pass(self) -> None:
        calls: list = []

        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "run_report_gate":
                return {"ok": False, "exit_code": EXIT_FAILED, "error": "gate", "stock_id": "2409"}
            if tool == "run_position_gate":
                return _ok("run_position_gate", stock_id="2409")
            if tool == "draft_digest":
                self.fail("failed report must not go to notify")
            raise AssertionError(tool)

        ctx = _holdings_ctx(
            audit_enabled=False,
            skip_research=True,
            permissions=Permissions(skip_research=True),
        )
        run = run_agent(ctx.intent, ctx, dispatch=fake)
        tools = [tool for tool, _args in calls]
        self.assertIn("run_position_gate", tools)
        self.assertTrue(any(step.tool == "draft_digest" and step.skipped for step in run.steps))

    def test_chat_role_blocks_send_during_execution(self) -> None:
        calls: list = []

        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "send_digest":
                self.fail("chat must not dispatch send_digest")
            if tool == "answer_market_chat":
                return _ok("answer_market_chat", reply="偏多")
            raise AssertionError(tool)

        plan = Plan(
            intent="明天開盤怎麼看",
            intent_kind=INTENT_MARKET_OUTLOOK,
            trade_date="2026-08-18",
            steps=[
                PlanStep(tool="answer_market_chat", args={"message": "明天開盤怎麼看"}),
                PlanStep(
                    tool="send_digest",
                    args={
                        "digest_date": "2026-08-18",
                        "subject": "x",
                        "main_detail_markdown": "y",
                        "approved": True,
                    },
                ),
            ],
        )
        ctx = AgentContext(
            intent="明天開盤怎麼看",
            trade_date="2026-08-18",
            holdings={},
            csv_missing=set(),
            daily_facts_present=True,
            role=ROLE_CHAT,
        )
        run = run_agent(ctx.intent, ctx, dispatch=fake, plan=plan)
        self.assertNotIn("send_digest", [tool for tool, _args in calls])
        self.assertTrue(run.ok)


class ReplayCliTests(unittest.TestCase):
    def test_replay_cli_prints_issue_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            log = AuditLog("cli-replay", path)
            log.record_tool(
                tool="run_report_gate",
                args={"stock_id": "2409"},
                ok=False,
                issue_codes=["missing_stock_id"],
                actor=ROLE_RESEARCH,
                summary="gate 未通過",
            )
            log.record_validator_round(
                stock_id="2409",
                gate_round=1,
                issue_codes=["missing_stock_id"],
                passed=False,
                research_actor=ROLE_RESEARCH,
            )
            log.record_tool(
                tool="draft_digest",
                args={"digest_date": "2026-08-18"},
                ok=True,
                actor="notify",
                summary="draft",
            )
            buffer = io.StringIO()
            with patch.object(sys, "stdout", buffer):
                code = agent_main(["--replay", str(path)])
        self.assertEqual(code, 0)
        output = buffer.getvalue()
        self.assertIn("cli-replay", output)
        self.assertIn("missing_stock_id", output)
        self.assertIn("validate_research", output)
        self.assertIn("send_digest:", output)

    def test_role_flag_listed_in_help(self) -> None:
        buffer = io.StringIO()
        err = io.StringIO()
        with patch.object(sys, "stdout", buffer), patch.object(sys, "stderr", err):
            try:
                agent_main(["--help"])
            except SystemExit as exc:
                self.assertEqual(exc.code, 0)
        text = buffer.getvalue() + err.getvalue()
        self.assertIn("--role", text)
        self.assertIn("--replay", text)
        self.assertIn("--skip-research", text)


if __name__ == "__main__":
    unittest.main()
