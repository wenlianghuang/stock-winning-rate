"""Outcome review: frozen 5-day table, narrative skill, and a two-round gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "outcome-review"))

import eval_calls  # noqa: E402
import outcome_ledger  # noqa: E402
import outcome_prompts  # noqa: E402
import outcome_review_gate  # noqa: E402
import validate_outcome  # noqa: E402
from agent.orchestrator import build_plan, classify_intent  # noqa: E402
from agent.policy import (  # noqa: E402
    INTENT_MARKET_OUTLOOK,
    INTENT_OUTCOME_REVIEW,
    INTENT_STOCK_DECISION,
    ROLE_CHAT,
    Permissions,
)


def _miss_payload() -> dict:
    cases = [
        {
            "stock_id": "2330",
            "trade_date": "2026-08-18",
            "chip_regime": "distribution",
            "call": "偏空",
            "excess_return_pct": 1.8,
            "verdict": "miss",
            "failures": ["judgment"],
            "round1_passed": True,
            "final_passed": True,
            "rounds": 1,
            "issue_codes": [],
            "operational_reason": None,
        }
    ]
    return eval_calls.summarize(
        cases,
        horizon="5d",
        epsilon=0.5,
        trade_date="2026-08-18",
        stock="2330",
    )


def _ledger() -> dict:
    return outcome_ledger.build_ledger(_miss_payload(), scope="2026-08-18 2330")


def _good_body(ledger: dict) -> str:
    lines = ["## 有方向的判斷", "下面檢查的是判斷日當時的方向。"]
    for row in ledger["rows"]:
        if row.get("plain"):
            lines.append(row["plain"])
    lines.append(ledger["accuracy_sentence"])
    lines.extend(
        [
            "",
            "## 還不能計分",
            "這批沒有不能計分的列。",
            "",
            "## 該動哪一層",
            "這筆方向相反，是判斷日的分類規則不對。下一步改籌碼歸類。",
        ]
    )
    return "\n".join(lines) + "\n"


def _bad_body() -> str:
    return (
        "## 有方向的判斷\n"
        "2026-08-18 2330 這筆看多而且事後證明正確。\n\n"
        "## 還不能計分\n"
        "這批沒有不能計分的列。\n\n"
        "## 該動哪一層\n"
        "沒有要改的層。\n"
    )


def _plan(text: str, **kwargs):
    classified = classify_intent(text)
    return build_plan(
        classified,
        trade_date=kwargs.get("trade_date", "2026-11-18"),
        holdings={},
        csv_missing=set(),
        daily_facts_present=True,
        permissions=kwargs.get("permissions"),
        explicit_outcome_date=kwargs.get("explicit_outcome_date"),
    )


class WindowTests(unittest.TestCase):
    def test_latest_close_on_nov_17_scores_nov_10(self) -> None:
        dates = [
            "2026-11-02",
            "2026-11-03",
            "2026-11-04",
            "2026-11-05",
            "2026-11-06",
            "2026-11-09",
            "2026-11-10",
            "2026-11-11",
            "2026-11-12",
            "2026-11-13",
            "2026-11-16",
            "2026-11-17",
        ]
        found = outcome_ledger.entry_date_before_latest(dates)
        self.assertIsNotNone(found)
        assert found is not None
        entry, latest = found
        self.assertEqual(latest, "2026-11-17")
        self.assertEqual(entry, "2026-11-10")
        self.assertNotEqual(entry, "2026-11-12")

    def test_short_calendar_cannot_look_back(self) -> None:
        self.assertIsNone(
            outcome_ledger.entry_date_before_latest(
                ["2026-11-13", "2026-11-16", "2026-11-17"]
            )
        )

    def test_automatic_window_keeps_the_stable_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = outcome_review_gate.run_gate(
                root=Path(tmp),
                trade_date="2026-11-10",
                as_of="2026-11-17",
                include_date_in_path=False,
                payload=_miss_payload(),
                skip_agy=True,
            )
            facts = json.loads(outcome.facts_path.read_text(encoding="utf-8"))
        self.assertEqual(outcome.md_path.name, "outcome_review.md")
        self.assertIn("以 2026-11-17 收盤回看，對帳 2026-11-10 的判斷", facts["scope"])


class NarrativeTests(unittest.TestCase):
    def test_prompt_inlines_skill_and_table(self) -> None:
        ledger = _ledger()
        prompt = outcome_prompts.build_outcome_prompt(table=outcome_ledger.ledger_summary(ledger))
        self.assertIn("## 有方向的判斷", prompt)
        self.assertIn("不要再讀 skill 檔", prompt)
        self.assertNotIn("name: outcome-narrative", prompt)
        self.assertNotIn("# 事後對帳正文", prompt)
        self.assertIn("2026-08-18 2330 偏空", prompt)
        self.assertIn("+1.80%", prompt)
        self.assertIn("比大盤強", prompt)
        self.assertIn("人話：", prompt)
        self.assertIn(ledger["accuracy_sentence"], prompt)

    def test_rejects_flipped_bullish_claim(self) -> None:
        result = validate_outcome.validate_outcome_report(_bad_body(), _ledger())
        self.assertFalse(result.passed)
        codes = {issue.code for issue in result.issues}
        self.assertIn("flipped_call", codes)
        self.assertIn("flipped_verdict", codes)

    def test_accepts_miss_as_chip_classification(self) -> None:
        ledger = _ledger()
        plain = ledger["rows"][0]["plain"]
        self.assertIn("比大盤強", plain)
        self.assertIn("不是新的看多或看空", plain)
        self.assertNotIn("命中", plain)
        result = validate_outcome.validate_outcome_report(_good_body(ledger), ledger)
        self.assertTrue(result.passed, result.summary_lines())

    def test_plain_sentence_includes_the_price_path(self) -> None:
        payload = _miss_payload()
        payload["rows"][0].update(
            {
                "stock_return_pct": 0.36,
                "market_return_pct": -6.13,
                "entry_close": 27.7,
                "fwd_close": 27.8,
                "fwd_date": "2026-11-17",
            }
        )
        ledger = outcome_ledger.build_ledger(payload, scope="2026-08-18 2330", as_of="2026-11-17")
        plain = ledger["rows"][0]["plain"]
        self.assertIn("股價從 27.70 到 27.80", plain)
        self.assertIn("個股 +0.36%", plain)
        self.assertIn("加權 -6.13%", plain)
        self.assertIn("2026-11-17 只是用來對帳的收盤", plain)
        result = validate_outcome.validate_outcome_report(_good_body(ledger), ledger)
        self.assertTrue(result.passed, result.summary_lines())

    def test_pending_call_is_not_scored(self) -> None:
        cases = [
            {
                "stock_id": "2409",
                "trade_date": "2026-11-04",
                "chip_regime": "distribution",
                "call": "偏空",
                "excess_return_pct": None,
                "verdict": "pending",
                "failures": [],
                "round1_passed": True,
                "final_passed": True,
                "rounds": 1,
                "issue_codes": [],
                "operational_reason": None,
            }
        ]
        payload = eval_calls.summarize(
            cases, horizon="5d", epsilon=0.5, trade_date=None, stock=None
        )
        ledger = outcome_ledger.build_ledger(payload, scope="全部已產出的個股報告")
        body = (
            "## 有方向的判斷\n"
            "2026-11-04 2409 偏空，這筆命中。\n\n"
            "## 還不能計分\n"
            "尚未成熟 1。\n\n"
            "## 該動哪一層\n"
            "沒有要改的層。\n"
        )
        result = validate_outcome.validate_outcome_report(body, ledger)
        self.assertFalse(result.passed)
        self.assertTrue(any(issue.code == "no_scored" for issue in result.issues))
        clean = (
            "## 有方向的判斷\n"
            "還沒有可以計分的判斷。\n\n"
            "## 還不能計分\n"
            "2409 還沒走完 5 個交易日。尚未成熟 1。\n\n"
            "## 該動哪一層\n"
            "沒有要改的層。\n"
        )
        passed = validate_outcome.validate_outcome_report(clean, ledger)
        self.assertTrue(passed.passed, passed.summary_lines())

    def test_rejects_invented_percent(self) -> None:
        ledger = _ledger()
        body = _good_body(ledger).replace("+1.80%", "+1.80%，另外還有 9.90%")
        result = validate_outcome.validate_outcome_report(body, ledger)
        self.assertFalse(result.passed)
        self.assertTrue(any(issue.code == "unknown_percent" for issue in result.issues))


class GateTests(unittest.TestCase):
    def test_second_round_keeps_the_narrative(self) -> None:
        ledger = _ledger()
        calls = {"n": 0}

        def agy(prompt: str, timeout_sec: int = 0) -> tuple[str, int]:
            del prompt, timeout_sec
            calls["n"] += 1
            if calls["n"] == 1:
                return _bad_body(), 0
            return _good_body(ledger), 0

        with tempfile.TemporaryDirectory() as tmp:
            outcome = outcome_review_gate.run_gate(
                root=Path(tmp),
                trade_date="2026-08-18",
                stock="2330",
                payload=_miss_payload(),
                agy=agy,
            )
            text = outcome.md_path.read_text(encoding="utf-8")
        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.narrative_generated)
        self.assertEqual(calls["n"], 2)
        self.assertIn("相反", text)
        self.assertIn("不是新的看多或看空", text)
        self.assertIn("比大盤強", text)
        self.assertNotIn("這筆看多", text)
        self.assertNotIn("解讀未生成", text)

    def test_two_failures_publish_stats_only(self) -> None:
        def agy(prompt: str, timeout_sec: int = 0) -> tuple[str, int]:
            del prompt, timeout_sec
            return _bad_body(), 0

        with tempfile.TemporaryDirectory() as tmp:
            outcome = outcome_review_gate.run_gate(
                root=Path(tmp),
                payload=_miss_payload(),
                agy=agy,
            )
            text = outcome.md_path.read_text(encoding="utf-8")
            log = json.loads(outcome.log_path.read_text(encoding="utf-8"))
        self.assertEqual(outcome.exit_code, 1)
        self.assertFalse(outcome.narrative_generated)
        self.assertEqual(len(log), 2)
        self.assertIn("解讀未生成，這份只有規則對帳。", text)
        self.assertIn("報告傾向對帳", text)
        self.assertNotIn("看多", text)
        self.assertNotIn("事後證明正確", text)

    def test_skip_agy_skips_the_model(self) -> None:
        def agy(prompt: str, timeout_sec: int = 0) -> tuple[str, int]:
            raise AssertionError("skip-agy 不該呼叫模型")

        with tempfile.TemporaryDirectory() as tmp:
            outcome = outcome_review_gate.run_gate(
                root=Path(tmp),
                payload=_miss_payload(),
                skip_agy=True,
                agy=agy,
            )
            text = outcome.md_path.read_text(encoding="utf-8")
        self.assertEqual(outcome.exit_code, 0)
        self.assertFalse(outcome.narrative_generated)
        self.assertIn("未呼叫模型，這份只有規則對帳。", text)


class IntentTests(unittest.TestCase):
    def test_phrase_is_outcome_review_not_a_new_report(self) -> None:
        classified = classify_intent("上週個股判斷後來怎樣")
        self.assertEqual(classified.kind, INTENT_OUTCOME_REVIEW)
        plan = _plan("上週個股判斷後來怎樣")
        self.assertEqual([step.tool for step in plan.steps], ["run_outcome_review"])
        self.assertNotIn("trade_date", plan.steps[0].args)
        self.assertNotIn("stock", plan.steps[0].args)
        notes = " ".join(plan.notes)
        self.assertIn("最新收盤", notes)
        self.assertIn("5 個交易日", notes)

    def test_dated_sentence_filters_and_stock_code_is_not_a_new_decision(self) -> None:
        classified = classify_intent("2330 後來怎樣")
        self.assertEqual(classified.kind, INTENT_OUTCOME_REVIEW)
        self.assertNotEqual(classified.kind, INTENT_STOCK_DECISION)
        plan = _plan("2026-08-18 2330 後來怎樣")
        self.assertEqual(plan.steps[0].args["trade_date"], "2026-08-18")
        self.assertEqual(plan.steps[0].args["stock"], "2330")

    def test_open_question_stays_market_outlook(self) -> None:
        self.assertEqual(classify_intent("明天開盤怎麼看").kind, INTENT_MARKET_OUTLOOK)

    def test_chat_role_cannot_run_the_review(self) -> None:
        plan = _plan("上週個股判斷後來怎樣", permissions=Permissions(role=ROLE_CHAT))
        self.assertEqual(plan.steps, [])
        self.assertTrue(any("不允許" in note for note in plan.notes))

    def test_explicit_cli_date_filters_without_a_date_in_the_sentence(self) -> None:
        plan = _plan("上週個股判斷後來怎樣", explicit_outcome_date="2026-08-18")
        self.assertEqual(plan.steps[0].args["trade_date"], "2026-08-18")


if __name__ == "__main__":
    unittest.main()
