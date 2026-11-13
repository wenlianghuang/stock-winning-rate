"""Tests for report-call accuracy vs gate stability."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import calibrate_thresholds as calib  # noqa: E402
import eval_calls  # noqa: E402


def _facts(regime: str) -> dict:
    return {
        "stock_id": "2330",
        "trade_date": "2026-06-01",
        "chip_regime": regime,
    }


def _outcome(excess: float | None, *, status: str = "resolved") -> dict:
    slot: dict = {"status": status}
    if excess is not None:
        slot["excess_return_pct"] = excess
    return {
        "stock_id": "2330",
        "trade_date": "2026-06-01",
        "horizons": {"5d": slot},
    }


def _round(passed: bool, codes: list[str] | None = None, *, agy_exit: int = 0) -> dict:
    return {
        "round": 1,
        "passed": passed,
        "issue_codes": codes or [],
        "agy_exit_code": agy_exit,
    }


class DirectionTests(unittest.TestCase):
    def test_epsilon_matches_calibrate(self) -> None:
        self.assertEqual(eval_calls.EPSILON_PCT, calib.EPSILON_PCT)

    def test_regime_maps_to_call(self) -> None:
        self.assertEqual(eval_calls.direction_call("accumulation"), "偏多")
        self.assertEqual(eval_calls.direction_call("distribution"), "偏空")
        self.assertEqual(eval_calls.direction_call("mixed"), "中性")
        self.assertEqual(eval_calls.direction_call(None), "中性")

    def test_score_bands(self) -> None:
        self.assertEqual(eval_calls.score_direction("偏多", 1.2), "hit")
        self.assertEqual(eval_calls.score_direction("偏多", -0.8), "miss")
        self.assertEqual(eval_calls.score_direction("偏空", -1.0), "hit")
        self.assertEqual(eval_calls.score_direction("偏空", 0.8), "miss")
        self.assertEqual(eval_calls.score_direction("偏多", 0.5), "flat")
        self.assertEqual(eval_calls.score_direction("偏多", -0.5), "flat")
        self.assertEqual(eval_calls.score_direction("中性", 3.0), "abstain")
        self.assertEqual(eval_calls.score_direction("偏多", None), "pending")


class CaseTests(unittest.TestCase):
    def test_judgment_miss_on_passed_gate(self) -> None:
        case = eval_calls._case(
            stock_id="2330",
            trade_date="2026-06-01",
            facts=_facts("distribution"),
            run=[_round(False, ["missing_watch"]), _round(True)],
            md_exists=True,
            excess=1.4,
            audit_reason=None,
            epsilon=0.5,
        )
        self.assertEqual(case["call"], "偏空")
        self.assertEqual(case["verdict"], "miss")
        self.assertEqual(case["failures"], ["judgment"])
        self.assertFalse(case["round1_passed"])
        self.assertTrue(case["final_passed"])
        self.assertEqual(case["rounds"], 2)

    def test_writing_failure_when_last_round_fails(self) -> None:
        case = eval_calls._case(
            stock_id="2330",
            trade_date="2026-06-01",
            facts=_facts("accumulation"),
            run=[_round(False, ["fact_foreign_direction"])],
            md_exists=False,
            excess=None,
            audit_reason=None,
            epsilon=0.5,
        )
        self.assertEqual(case["verdict"], "pending")
        self.assertEqual(case["failures"], ["writing"])
        self.assertEqual(case["issue_codes"], ["fact_foreign_direction"])

    def test_operational_when_agy_missing_and_no_pass(self) -> None:
        case = eval_calls._case(
            stock_id="2330",
            trade_date="2026-06-01",
            facts=_facts("accumulation"),
            run=[_round(False, [], agy_exit=10)],
            md_exists=False,
            excess=2.0,
            audit_reason="exit_10_agy_missing",
            epsilon=0.5,
        )
        self.assertIn("operational", case["failures"])
        self.assertNotIn("writing", case["failures"])
        self.assertEqual(case["verdict"], "hit")
        self.assertNotIn("judgment", case["failures"])

    def test_recovered_run_drops_operational(self) -> None:
        case = eval_calls._case(
            stock_id="2330",
            trade_date="2026-06-01",
            facts=_facts("accumulation"),
            run=[_round(True)],
            md_exists=True,
            excess=1.0,
            audit_reason="exit_20_csv_missing",
            epsilon=0.5,
        )
        self.assertEqual(case["failures"], [])
        self.assertIsNone(case["operational_reason"])

    def test_accuracy_excludes_abstain_flat_pending(self) -> None:
        rows = [
            {"verdict": "hit", "failures": [], "rounds": 1, "round1_passed": True, "final_passed": True, "chip_regime": "accumulation"},
            {"verdict": "miss", "failures": ["judgment"], "rounds": 1, "round1_passed": True, "final_passed": True, "chip_regime": "distribution"},
            {"verdict": "abstain", "failures": [], "rounds": 1, "round1_passed": False, "final_passed": True, "chip_regime": "mixed"},
            {"verdict": "flat", "failures": [], "rounds": 2, "round1_passed": False, "final_passed": True, "chip_regime": "accumulation"},
            {"verdict": "pending", "failures": ["writing"], "rounds": 1, "round1_passed": False, "final_passed": False, "chip_regime": "distribution"},
        ]
        payload = eval_calls.summarize(
            rows, horizon="5d", epsilon=0.5, trade_date=None, stock=None
        )
        self.assertEqual(payload["accuracy"]["scored"], 2)
        self.assertEqual(payload["accuracy"]["accuracy"], 0.5)
        self.assertEqual(payload["accuracy"]["abstain"], 1)
        self.assertEqual(payload["stability"]["runs"], 5)
        self.assertEqual(payload["stability"]["round1_pass"], 2)
        self.assertEqual(payload["stability"]["final_pass"], 4)
        self.assertEqual(payload["failure_counts"]["judgment"], 1)
        self.assertEqual(payload["failure_counts"]["writing"], 1)


class TreeTests(unittest.TestCase):
    def test_evaluate_joins_facts_log_and_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "reports" / "stock" / "2026-06-01" / "2330"
            folder.mkdir(parents=True)
            (folder / "tw_stock_2330.facts.json").write_text(
                json.dumps(_facts("distribution")),
                encoding="utf-8",
            )
            (folder / "tw_stock_2330.md").write_text("# report\n", encoding="utf-8")
            log = {
                "round": 1,
                "passed": True,
                "issue_codes": [],
                "agy_exit_code": 0,
            }
            (folder / "tw_stock_2330.gate.log").write_text(
                json.dumps(log, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            outcome_dir = root / "reports" / "outcomes"
            outcome_dir.mkdir()
            (outcome_dir / "outcomes.jsonl").write_text(
                json.dumps(_outcome(1.5), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            audit_dir = root / "reports" / "agent" / "2026-06-02"
            audit_dir.mkdir(parents=True)
            (audit_dir / "run_x.jsonl").write_text(
                json.dumps(
                    {
                        "tool": "run_report_gate",
                        "stock_id": "2409",
                        "exit_code": 20,
                        "skipped": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = eval_calls.evaluate(root)
            by_id = {row["stock_id"]: row for row in payload["rows"]}
            self.assertEqual(by_id["2330"]["verdict"], "miss")
            self.assertEqual(by_id["2330"]["failures"], ["judgment"])
            self.assertEqual(by_id["2409"]["failures"], ["operational"])
            self.assertEqual(by_id["2409"]["operational_reason"], "exit_20_csv_missing")

            fetch_only = root / "reports" / "stock" / "2026-06-03" / "2330"
            fetch_only.mkdir(parents=True)
            (fetch_only / "tw_stock_2330.facts.json").write_text(
                json.dumps(
                    {"stock_id": "2330", "trade_date": "2026-06-03", "chip_regime": "distribution"}
                ),
                encoding="utf-8",
            )

            again = eval_calls.evaluate(root)
            keys = {(row["stock_id"], row["trade_date"]) for row in again["rows"]}
            self.assertNotIn(("2330", "2026-06-03"), keys)
            self.assertIn(("2330", "2026-06-01"), keys)

            code = eval_calls.main(["--root", str(root), "--no-write", "--stock", "2330"])
            self.assertEqual(code, 0)
            self.assertEqual(payload["accuracy"]["accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
