"""triage_watchlist: scoring rules, ranking, tool I/O, and two-phase orchestrator plans."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.orchestrator import (  # noqa: E402
    AgentContext,
    build_plan,
    classify_intent,
    run_agent,
)
from agent.policy import (  # noqa: E402
    DIGEST_NA,
    GATE_EST_SEC,
    INTENT_WATCHLIST_SCAN,
    ROLE_CHAT,
    ROLE_DATA,
    fit_picks_to_budget,
    notify_may_include_triage_line,
    role_for_intent,
    tool_allowed_for_role,
    validate_plan_schema,
)
from agent.tools._types import EXIT_BAD_ARGS, EXIT_FAILED, EXIT_OK  # noqa: E402
from agent.tools.triage import (  # noqa: E402
    MIN_PICK_SCORE,
    flags_score,
    rank_rows,
    score_facts,
    summary_line,
    triage_watchlist,
)

TRADE_DATE = "2026-10-22"


def _facts(**overrides) -> dict:
    base = {
        "stock_id": "2330",
        "stock_name": "台積電",
        "chip_regime": "mixed",
        "breakout_20d_high": "no",
        "breakdown_20d_low": "no",
        "divergences": [],
        "foreign_direction": 0,
        "foreign_streak_dir": 0,
        "foreign_streak_days": 0,
        "ma5_cross_ma10": "none",
        "ma5_cross_recency": "none",
        "volume_anomaly": "normal",
        "volume_price_divergence": "none",
    }
    base.update(overrides)
    return base


def _codes(flags: list[dict]) -> list[str]:
    return [item["code"] for item in flags]


class ScoreFactsTests(unittest.TestCase):
    def test_no_csv_is_flagged_for_a_look(self) -> None:
        flags = score_facts(None, None, held=True)
        self.assertEqual(_codes(flags), ["no_csv"])
        self.assertGreaterEqual(flags_score(flags), MIN_PICK_SCORE)

    def test_unchanged_day_has_no_flags(self) -> None:
        self.assertEqual(score_facts(_facts(), _facts(), held=True), [])

    def test_strong_regime_flip_outweighs_mild_flip(self) -> None:
        strong = score_facts(
            _facts(chip_regime="distribution"), _facts(chip_regime="accumulation"), held=False
        )
        mild = score_facts(_facts(chip_regime="mixed"), _facts(chip_regime="accumulation"), held=False)
        self.assertEqual(_codes(strong), ["regime_flip"])
        self.assertGreater(flags_score(strong), flags_score(mild))
        self.assertIn("籌碼偏多轉籌碼偏空", strong[0]["label"])

    def test_unknown_regime_is_not_a_flip(self) -> None:
        flags = score_facts(_facts(chip_regime="unknown"), _facts(chip_regime="accumulation"), held=False)
        self.assertNotIn("regime_flip", _codes(flags))

    def test_only_new_divergences_count(self) -> None:
        prev = _facts(divergences=["price_down_margin_up"])
        today = _facts(divergences=["price_down_margin_up", "price_up_foreign_sell"])
        flags = score_facts(today, prev, held=False)
        self.assertEqual(_codes(flags), ["divergence:price_up_foreign_sell"])

    def test_foreign_streak_break(self) -> None:
        prev = _facts(foreign_streak_dir=1, foreign_streak_days=4)
        today = _facts(foreign_direction=-1, foreign_streak_dir=-1, foreign_streak_days=1)
        flags = score_facts(today, prev, held=False)
        self.assertEqual(_codes(flags), ["foreign_streak_break"])
        self.assertIn("連買 4 日後轉賣超", flags[0]["label"])

    def test_short_streak_does_not_count(self) -> None:
        prev = _facts(foreign_streak_dir=1, foreign_streak_days=2)
        today = _facts(foreign_direction=-1)
        self.assertEqual(score_facts(today, prev, held=False), [])

    def test_held_distribution_only_for_holdings(self) -> None:
        today = _facts(chip_regime="distribution")
        prev = _facts(chip_regime="distribution")
        self.assertEqual(_codes(score_facts(today, prev, held=True)), ["held_distribution"])
        self.assertEqual(score_facts(today, prev, held=False), [])

    def test_breakdown_and_breakout(self) -> None:
        down = score_facts(_facts(breakdown_20d_low="yes"), _facts(), held=False)
        up = score_facts(_facts(breakout_20d_high="yes"), _facts(), held=False)
        self.assertEqual(_codes(down), ["breakdown_20d"])
        self.assertEqual(_codes(up), ["breakout_20d"])

    def test_single_weak_signal_stays_below_pick_threshold(self) -> None:
        flags = score_facts(
            _facts(ma5_cross_ma10="golden", ma5_cross_recency="today"), _facts(), held=False
        )
        self.assertEqual(_codes(flags), ["ma5_cross_today"])
        self.assertLess(flags_score(flags), MIN_PICK_SCORE)

    def test_no_baseline_alone_is_not_picked(self) -> None:
        flags = score_facts(_facts(), None, held=True)
        self.assertEqual(_codes(flags), ["no_baseline"])
        self.assertLess(flags_score(flags), MIN_PICK_SCORE)


class RankRowsTests(unittest.TestCase):
    def test_caps_at_top_k_and_skips_low_scores(self) -> None:
        rows = [
            {"stock_id": "1111", "score": 5, "held": False},
            {"stock_id": "2222", "score": 3, "held": False},
            {"stock_id": "3333", "score": 2, "held": False},
            {"stock_id": "4444", "score": 1, "held": True},
        ]
        self.assertEqual(rank_rows(rows, 2), ["1111", "2222"])
        self.assertEqual(rank_rows(rows, 8), ["1111", "2222", "3333"])
        self.assertEqual(rank_rows(rows, 0), [])

    def test_holdings_break_ties(self) -> None:
        rows = [
            {"stock_id": "1111", "score": 3, "held": False},
            {"stock_id": "2222", "score": 3, "held": True},
        ]
        self.assertEqual(rank_rows(rows, 1), ["2222"])

    def test_summary_line_for_quiet_stock(self) -> None:
        line = summary_line(
            {
                "stock_id": "2330",
                "stock_name": "台積電",
                "has_csv": True,
                "has_baseline": True,
                "prev_trade_date": "2026-10-21",
                "chip_regime": "accumulation",
                "flags": [],
            }
        )
        self.assertEqual(line, "台積電（2330）：籌碼偏多，和 2026-10-21 相比沒有新條件")


class TriageToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _csv(self, day: str, stock_id: str) -> None:
        path = self.root / "reports" / "stock" / day / stock_id / f"tw_stock_{stock_id}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub\n", encoding="utf-8")

    def _run(self, facts_by_key: dict, **kwargs):
        def fake_facts(csv_path: Path):
            day = csv_path.parent.parent.name
            stock_id = csv_path.parent.name
            return facts_by_key.get((day, stock_id))

        with patch("agent.tools.triage._facts_from_csv", side_effect=fake_facts):
            return triage_watchlist(trade_date=TRADE_DATE, root=self.root, **kwargs)

    def test_picks_changed_stocks_and_lists_missing(self) -> None:
        for day in ("2026-10-21", TRADE_DATE):
            self._csv(day, "2330")
            self._csv(day, "2409")
        result = self._run(
            {
                ("2026-10-21", "2330"): _facts(chip_regime="accumulation"),
                (TRADE_DATE, "2330"): _facts(chip_regime="distribution"),
                ("2026-10-21", "2409"): _facts(stock_id="2409", stock_name="友達"),
                (TRADE_DATE, "2409"): _facts(stock_id="2409", stock_name="友達"),
            },
            stocks=["2330", "2409", "6669"],
            held=["2330"],
            top_k=3,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, EXIT_OK)
        self.assertEqual(result.picks, ["2330", "6669"])
        self.assertEqual(result.skipped, ["2409"])
        self.assertEqual(result.missing, ["6669"])
        by_id = {row["stock_id"]: row for row in result.rows}
        self.assertEqual(by_id["2330"]["prev_chip_regime"], "accumulation")
        self.assertIn("持有中且籌碼偏空", by_id["2330"]["summary"])
        self.assertIn("和 2026-10-21 相比沒有新條件", by_id["2409"]["summary"])

    def test_baseline_skips_gap_days(self) -> None:
        self._csv("2026-10-15", "2330")
        self._csv(TRADE_DATE, "2330")
        self._csv("2026-10-30", "2330")
        result = self._run(
            {
                ("2026-10-15", "2330"): _facts(),
                (TRADE_DATE, "2330"): _facts(),
                ("2026-10-30", "2330"): _facts(chip_regime="distribution"),
            },
            stocks=["2330"],
            held=[],
        )
        self.assertEqual(result.rows[0]["prev_trade_date"], "2026-10-15")
        self.assertEqual(result.picks, [])

    def test_top_k_is_capped(self) -> None:
        result = self._run({}, stocks=["2330"], held=[], top_k=99)
        self.assertEqual(result.top_k, 8)
        self.assertTrue(any("上限" in note for note in result.notes))

    def test_watchlist_source(self) -> None:
        watchlist = self.root / "watchlist.txt"
        watchlist.write_text("# 註解\n2330\n2454  # 聯發科\n", encoding="utf-8")
        result = self._run({}, source="watchlist", watchlist=watchlist, held=[])
        self.assertEqual(sorted(row["stock_id"] for row in result.rows), ["2330", "2454"])

    def test_empty_universe_is_bad_args(self) -> None:
        result = self._run({}, source="holdings", held=[])
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, EXIT_BAD_ARGS)

    def test_writes_nothing(self) -> None:
        self._csv(TRADE_DATE, "2330")
        before = sorted(p for p in self.root.rglob("*"))
        self._run({(TRADE_DATE, "2330"): _facts()}, stocks=["2330"], held=[])
        self.assertEqual(sorted(p for p in self.root.rglob("*")), before)


class TriagePolicyTests(unittest.TestCase):
    HOLDINGS = {
        "2330": {"avg_cost": 600, "shares": 1000},
        "2409": {"avg_cost": 30, "shares": 1000},
    }

    def test_held_pick_costs_two_gates(self) -> None:
        kept, dropped = fit_picks_to_budget(
            ["2330", "2454"],
            self.HOLDINGS,
            remaining_calls=32,
            remaining_sec=GATE_EST_SEC * 2,
        )
        self.assertEqual(kept, ["2330"])
        self.assertEqual(dropped, ["2454"])

    def test_call_budget_keeps_reserve(self) -> None:
        kept, dropped = fit_picks_to_budget(
            ["2454", "3037"], {}, remaining_calls=3, remaining_sec=10_000
        )
        self.assertEqual(kept, ["2454"])
        self.assertEqual(dropped, ["3037"])

    def test_order_is_preserved_once_budget_runs_out(self) -> None:
        kept, dropped = fit_picks_to_budget(
            ["2330", "2454"],
            self.HOLDINGS,
            remaining_calls=32,
            remaining_sec=GATE_EST_SEC * 1.5,
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, ["2330", "2454"])

    def test_triage_lines_only_for_skipped(self) -> None:
        self.assertTrue(notify_may_include_triage_line(picked=False))
        self.assertFalse(notify_may_include_triage_line(picked=True))

    def test_scan_runs_as_data_role(self) -> None:
        self.assertEqual(role_for_intent(INTENT_WATCHLIST_SCAN), ROLE_DATA)
        self.assertTrue(tool_allowed_for_role("triage_watchlist", ROLE_DATA)[0])
        self.assertTrue(tool_allowed_for_role("triage_watchlist", ROLE_CHAT)[0])
        self.assertFalse(tool_allowed_for_role("run_report_gate", ROLE_DATA)[0])

    def test_schema_accepts_scan_intent(self) -> None:
        errors = validate_plan_schema(
            {"intent_kind": INTENT_WATCHLIST_SCAN, "steps": [{"tool": "triage_watchlist"}]}
        )
        self.assertEqual(errors, [])


def _ok(**extra):
    payload = {"ok": True, "exit_code": EXIT_OK, "error": None}
    payload.update(extra)
    return payload


def _triage_payload(stocks: list[str], picks: list[str]):
    return _ok(
        rows=[
            {
                "stock_id": sid,
                "stock_name": "",
                "picked": sid in picks,
                "score": 3 if sid in picks else 0,
                "summary": f"{sid}：fixture{'（選中）' if sid in picks else '，沒有新條件'}",
            }
            for sid in stocks
        ],
        picks=list(picks),
        skipped=[sid for sid in stocks if sid not in picks],
        missing=[],
        trade_date=TRADE_DATE,
    )


def _many_holdings(n: int) -> dict:
    return {str(1101 + i): {"avg_cost": 50, "shares": 1000} for i in range(n)}


class TwoPhaseOrchestratorTests(unittest.TestCase):
    def _dispatch(self, calls: list, picks: list[str] | None, *, triage_ok: bool = True):
        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "triage_watchlist":
                if not triage_ok:
                    return {"ok": False, "exit_code": EXIT_FAILED, "error": "boom"}
                stocks = [str(s) for s in args.get("stocks") or []]
                chosen = stocks[: args.get("top_k", 3)] if picks is None else picks
                return _triage_payload(stocks, chosen)
            if tool == "fetch_chips":
                return _ok(csv_paths=[])
            if tool in ("run_report_gate", "run_position_gate"):
                return _ok(stock_id=args.get("stock_id"), trade_date=TRADE_DATE, md_path="/tmp/x.md")
            if tool == "draft_digest":
                return _ok(subject="draft", md_path="/tmp/digest.md")
            raise AssertionError(f"unexpected tool {tool}")

        return fake

    def _ctx(self, holdings: dict, **overrides) -> AgentContext:
        data = dict(
            intent="幫我處理今天持股",
            trade_date=TRADE_DATE,
            holdings=holdings,
            csv_missing=set(),
        )
        data.update(overrides)
        return AgentContext(**data)

    def test_twelve_holdings_gate_only_top_k(self) -> None:
        calls: list = []
        holdings = _many_holdings(12)
        ctx = self._ctx(holdings, triage_top_k=3)
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls, None))
        tools = [tool for tool, _ in calls]
        self.assertEqual(tools.count("run_report_gate"), 3)
        self.assertEqual(tools.count("run_position_gate"), 3)
        triage_args = next(args for tool, args in calls if tool == "triage_watchlist")
        self.assertEqual(triage_args["top_k"], 3)
        self.assertEqual(len(triage_args["stocks"]), 12)
        self.assertTrue(run.ok)
        digest_args = next(args for tool, args in calls if tool == "draft_digest")
        lines = [item for item in digest_args["items"] if "分流未深挖" in item["markdown"]]
        self.assertEqual(len(lines), 9)

    def test_nothing_changed_runs_no_gate_but_drafts_digest(self) -> None:
        calls: list = []
        ctx = self._ctx(_many_holdings(3))
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls, []))
        tools = [tool for tool, _ in calls]
        self.assertNotIn("run_report_gate", tools)
        self.assertNotIn("run_position_gate", tools)
        self.assertIn("draft_digest", tools)
        self.assertTrue(any("沒有需要深挖" in note for note in run.notes))
        self.assertTrue(run.ok)

    def test_budget_trims_picks(self) -> None:
        calls: list = []
        holdings = _many_holdings(3)
        ctx = self._ctx(holdings, max_elapsed_sec=GATE_EST_SEC * 2 + 10)
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls, list(holdings)))
        tools = [tool for tool, _ in calls]
        self.assertEqual(tools.count("run_report_gate"), 1)
        self.assertTrue(any("budget 不足" in note for note in run.notes))

    def test_triage_failure_falls_back_to_all_holdings(self) -> None:
        calls: list = []
        ctx = self._ctx(_many_holdings(2))
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls, None, triage_ok=False))
        tools = [tool for tool, _ in calls]
        self.assertEqual(tools.count("run_report_gate"), 2)
        self.assertTrue(any("分流失敗" in note for note in run.notes))

    def test_picked_but_failed_gate_stays_out_of_digest(self) -> None:
        calls: list = []

        def fake(tool: str, args: dict):
            calls.append((tool, dict(args)))
            if tool == "triage_watchlist":
                return _triage_payload(["1101", "1102"], ["1101"])
            if tool == "run_report_gate":
                return {"ok": False, "exit_code": EXIT_FAILED, "error": "gate", "stock_id": "1101"}
            if tool == "draft_digest":
                return _ok(subject="draft", md_path="/tmp/digest.md")
            raise AssertionError(tool)

        ctx = self._ctx(_many_holdings(2))
        run_agent(ctx.intent, ctx, dispatch=fake)
        digest_args = next(args for tool, args in calls if tool == "draft_digest")
        self.assertEqual([item["stock_id"] for item in digest_args["items"]], ["1102"])

    def test_dry_run_expands_plan_with_picks(self) -> None:
        calls: list = []
        holdings = _many_holdings(4)
        ctx = self._ctx(holdings, dry_run=True)
        run = run_agent(ctx.intent, ctx, dispatch=self._dispatch(calls, ["1102"]))
        self.assertEqual([tool for tool, _ in calls], ["triage_watchlist"])
        plan_tools = [step.tool for step in run.plan.steps]
        self.assertEqual(
            plan_tools,
            ["triage_watchlist", "run_report_gate", "run_position_gate", "draft_digest"],
        )
        report = next(step for step in run.plan.steps if step.tool == "run_report_gate")
        self.assertEqual(report.args["stock_id"], "1102")
        self.assertIn("分流選中", report.reason)


class WatchlistScanIntentTests(unittest.TestCase):
    def test_classify_scan(self) -> None:
        for text in ("今天哪幾檔要注意", "掃一下觀察名單", "幫我分流 2330 2454"):
            self.assertEqual(classify_intent(text).kind, INTENT_WATCHLIST_SCAN, text)

    def test_scan_plan_never_includes_gates(self) -> None:
        plan = build_plan(
            classify_intent("今天哪幾檔要注意"),
            trade_date=TRADE_DATE,
            holdings={"2330": {"avg_cost": 600, "shares": 1000}},
            csv_missing={"2330"},
            daily_facts_present=False,
        )
        self.assertEqual([step.tool for step in plan.steps], ["fetch_chips", "triage_watchlist"])
        self.assertEqual(plan.steps[1].args["stocks"], ["2330"])
        self.assertEqual(plan.digest_status, DIGEST_NA)

    def test_scan_watchlist_when_asked(self) -> None:
        plan = build_plan(
            classify_intent("掃一下觀察名單"),
            trade_date=TRADE_DATE,
            holdings={"2330": {"avg_cost": 600, "shares": 1000}},
            csv_missing=set(),
            daily_facts_present=False,
        )
        triage = plan.steps[-1]
        self.assertEqual(triage.args["source"], "watchlist")
        self.assertNotIn("stocks", triage.args)
        self.assertEqual(triage.args["held"], ["2330"])

    def test_scan_run_stops_at_table(self) -> None:
        calls: list = []

        def fake(tool: str, args: dict):
            calls.append(tool)
            if tool == "triage_watchlist":
                return _triage_payload(["2330", "2454"], ["2330"])
            raise AssertionError(tool)

        ctx = AgentContext(
            intent="幫我分流 2330 2454",
            trade_date=TRADE_DATE,
            holdings={},
            csv_missing=set(),
        )
        run = run_agent(ctx.intent, ctx, dispatch=fake)
        self.assertEqual(calls, ["triage_watchlist"])
        self.assertEqual(run.role, ROLE_DATA)
        self.assertTrue(run.ok)


class TriageApiTests(unittest.TestCase):
    def _client(self):
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")
        return TestClient(create_app())

    def test_passes_ids_and_held_to_tool(self) -> None:
        client = self._client()
        from agent.tools.triage import TriageResult

        result = TriageResult(ok=True, exit_code=EXIT_OK, trade_date=TRADE_DATE, picks=["2330"])
        with patch("agent.tools.triage.triage_watchlist", return_value=result) as mocked:
            response = client.get(
                "/triage",
                params={"ids": "2330,2409", "held": "2330", "date": TRADE_DATE, "top_k": 2},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["picks"], ["2330"])
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["stocks"], ["2330", "2409"])
        self.assertEqual(kwargs["held"], ["2330"])
        self.assertEqual(kwargs["top_k"], 2)

    def test_fetch_fills_missing_csv_with_finmind(self) -> None:
        client = self._client()
        from agent.tools.chips import FetchChipsResult
        from agent.tools.triage import TriageResult

        result = TriageResult(ok=True, exit_code=EXIT_OK, trade_date=TRADE_DATE, notes=[])
        fetched = FetchChipsResult(ok=True, exit_code=EXIT_OK)
        with (
            patch("stock_paths.find_stock_csv", return_value=None),
            patch("agent.tools.chips.fetch_chips", return_value=fetched) as chips,
            patch("agent.tools.triage.triage_watchlist", return_value=result),
        ):
            response = client.get(
                "/triage",
                params={"ids": "2330,2409", "date": TRADE_DATE, "fetch": "true"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(chips.call_args.args[0].stocks, ["2330", "2409"])
        self.assertEqual(chips.call_args.args[0].trade_date, TRADE_DATE)

    def test_rejects_bad_ids(self) -> None:
        client = self._client()
        self.assertEqual(client.get("/triage", params={"ids": "abc"}).status_code, 400)
        self.assertEqual(client.get("/triage", params={"ids": "2330", "date": "1022"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
