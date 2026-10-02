"""Tests for chart history rows (website price series, separate from chip lookback)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".agents" / "skills" / "tw-stock-report" / "fetch_chip_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_chip_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_chip_report"] = module
    spec.loader.exec_module(module)
    return module


class ChartHistoryRowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def test_build_chart_history_rows_extracts_price_only(self) -> None:
        datasets = {
            "price": {
                "2026-07-01": {
                    "open": 99.0,
                    "max": 101.0,
                    "min": 98.5,
                    "close": 100.0,
                    "Trading_Volume": 5000000,
                    "spread": 1.2,
                },
                "2026-07-02": {
                    "open": 100.5,
                    "max": 103.0,
                    "min": 100.0,
                    "close": 102.0,
                    "Trading_Volume": 6000000,
                    "spread": 2.0,
                },
            }
        }
        rows = self.mod.build_chart_history_rows(
            datasets,
            ["2026-07-01", "2026-07-02"],
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["日期"], "2026-07-01")
        self.assertEqual(rows[0]["開盤價"], 99.0)
        self.assertEqual(rows[0]["最高價"], 101.0)
        self.assertEqual(rows[0]["最低價"], 98.5)
        self.assertEqual(rows[0]["收盤價"], 100.0)
        self.assertEqual(rows[0]["成交量_張"], 5000)
        # FinMind spread=1.2 at close=100 → (1.2 / 98.8) * 100
        self.assertEqual(rows[0]["漲跌幅"], round(1.2 / 98.8 * 100, 2))
        self.assertEqual(rows[1]["收盤價"], 102.0)
        self.assertEqual(rows[1]["漲跌幅"], round(2.0 / 100.0 * 100, 2))

    def test_change_pct_from_spread_matches_market_formula(self) -> None:
        self.assertEqual(self.mod.change_pct_from_spread(20.0, 2500.0), 0.81)
        self.assertEqual(self.mod.change_pct_from_spread("", 100.0), "")
        self.assertEqual(self.mod.change_pct_from_spread(5.0, 5.0), "")

    def test_build_daily_row_stores_change_percent_not_points(self) -> None:
        datasets = {
            "price": {
                "2026-09-21": {
                    "date": "2026-09-21",
                    "open": 2480.0,
                    "max": 2510.0,
                    "min": 2470.0,
                    "close": 2500.0,
                    "Trading_Volume": 16_087_000,
                    "spread": 20.0,
                }
            },
            "inst": {},
            "margin": {},
            "sbl": {},
            "daytrade": {},
        }
        row = self.mod.build_daily_row("2330", "台積電", "2026-09-21", datasets)
        self.assertEqual(row["漲跌幅"], round(20.0 / 2480.0 * 100, 2))
        self.assertNotEqual(row["漲跌幅"], 20.0)

    def test_build_chart_history_rows_skips_missing_days(self) -> None:
        datasets = {"price": {"2026-07-02": {"close": 102.0, "Trading_Volume": 0}}}
        rows = self.mod.build_chart_history_rows(
            datasets,
            ["2026-07-01", "2026-07-02"],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["日期"], "2026-07-02")


if __name__ == "__main__":
    unittest.main()
