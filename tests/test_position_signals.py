"""Tests for deterministic position facts + bucket-aware checks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from position_signals import (  # noqa: E402
    build_position_facts,
    run_position_checks,
)


def _row(close: str, ma20: str = "50") -> dict[str, str]:
    return {
        "代碼": "2409",
        "名稱": "友達",
        "日期": "2026-07-06",
        "收盤價": close,
        "MA20": ma20,
    }


def _facts(avg_cost: float, close: str, ma20: str = "50"):
    return build_position_facts(
        _row(close, ma20),
        stock_id="2409",
        stock_name="友達",
        avg_cost=avg_cost,
        shares=500_000,
    )


class PositionBucketTests(unittest.TestCase):
    def test_profit_large_bucket(self) -> None:
        f = _facts(40.0, "50")  # +25%
        self.assertEqual(f.pnl_bucket, "profit_large")
        self.assertEqual(f.position_bias, "protect_gains")

    def test_loss_large_bucket(self) -> None:
        f = _facts(60.0, "50")  # -16.7%
        self.assertEqual(f.pnl_bucket, "loss_large")
        self.assertEqual(f.position_bias, "defensive")

    def test_breakeven_bucket(self) -> None:
        f = _facts(50.0, "50")  # 0%
        self.assertEqual(f.pnl_bucket, "breakeven")

    def test_cost_vs_ma20(self) -> None:
        self.assertEqual(_facts(60.0, "50", "55").cost_vs_ma20, "above")
        self.assertEqual(_facts(40.0, "50", "55").cost_vs_ma20, "below")


class PositionCheckTests(unittest.TestCase):
    def test_profit_large_requires_protection(self) -> None:
        f = _facts(40.0, "50")
        body = "## 操作情境\n- 續抱觀望，等待更高點\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        # 有「續抱」屬保護獲利關鍵字之一，且有錨定「獲利」缺失才報
        self.assertNotIn("position_profit_no_protection", codes)

    def test_profit_large_generic_fails(self) -> None:
        f = _facts(40.0, "50")
        body = "## 操作情境\n- 觀望，看看市場方向\n- 等待訊號\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_profit_no_protection", codes)
        self.assertIn("position_scenario_unanchored", codes)

    def test_loss_requires_risk_control(self) -> None:
        f = _facts(60.0, "50")
        body = "## 操作情境\n- 目前虧損，續抱等待反彈\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_loss_no_risk_control", codes)

    def test_loss_with_stop_passes(self) -> None:
        f = _facts(60.0, "50")
        body = "## 操作情境\n- 虧損擴大，若跌破前低則停損減碼\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_loss_no_risk_control", codes)

    def test_breakeven_needs_trigger(self) -> None:
        f = _facts(50.0, "50")
        body = "## 操作情境\n- 成本附近觀望\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_breakeven_no_trigger", codes)

    def test_no_close_price_skips(self) -> None:
        f = build_position_facts(
            {"代碼": "2409", "名稱": "友達", "MA20": "50"},
            stock_id="2409",
            stock_name="友達",
            avg_cost=50.0,
            shares=1000,
        )
        self.assertEqual(f.pnl_bucket, "unknown")
        self.assertEqual(run_position_checks("任意內容", f), [])


if __name__ == "__main__":
    unittest.main()
