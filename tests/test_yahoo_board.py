"""Yahoo concept-board facts from fixture payloads. No network."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "tw-stock-report"))

from yahoo_board import assemble_yahoo_board_facts, chart_bars  # noqa: E402


class YahooBoardTests(unittest.TestCase):
    def test_chart_bars_use_taipei_dates(self) -> None:
        ts = int(datetime(2026, 10, 22, 9, 0, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [ts, ts + 86400],
                        "indicators": {"quote": [{"close": [105.0, None]}]},
                    }
                ]
            }
        }
        self.assertEqual(chart_bars(payload), [("2026-10-22", 105.0)])

    def test_assemble_matches_institutional_page_shape(self) -> None:
        bars = [
            ("2026-10-13", 100.0),
            ("2026-10-14", 101.0),
            ("2026-10-15", 102.0),
            ("2026-10-16", 103.0),
            ("2026-10-20", 104.0),
            ("2026-10-21", 105.0),
            ("2026-10-22", 107.92),
        ]
        market = [(day, 1000.0) for day, _close in bars]
        facts = assemble_yahoo_board_facts(
            stock_id="3017",
            stock_name="奇鋐",
            bars=bars,
            market_bars=market,
            trades={
                "foreignDiffVolK": 70,
                "investmentTrustDiffVolK": 212,
                "dealerDiffVolK": 2,
            },
            major_net=55,
            trade_date="2026-10-22",
        )
        assert facts is not None
        self.assertEqual(facts["foreign_net_lots"], 70)
        self.assertEqual(facts["trust_net_lots"], 212)
        self.assertEqual(facts["dealer_net_lots"], 2)
        self.assertEqual(facts["major_net_lots"], 55)
        self.assertEqual(facts["today_change_pct"], 2.78)
        self.assertEqual(facts["chip_regime"], "accumulation")
        self.assertEqual(facts["rs_today"], "outperform")
        self.assertEqual(facts["source"], "yahoo")
        self.assertNotIn("margin", facts)

    def test_missing_price_returns_none(self) -> None:
        facts = assemble_yahoo_board_facts(
            stock_id="3017",
            stock_name="奇鋐",
            bars=[("2026-10-23", 105.0)],
            market_bars=[],
            trades=None,
            major_net=None,
            trade_date="2026-10-22",
        )
        self.assertIsNone(facts)

    def test_week_window_sums_five_sessions(self) -> None:
        from yahoo_board import week_window

        bars = [
            ("2026-10-14", 100.0),
            ("2026-10-15", 101.0),
            ("2026-10-16", 102.0),
            ("2026-10-19", 103.0),
            ("2026-10-20", 104.0),
            ("2026-10-21", 108.0),
            ("2026-10-22", 110.0),
        ]
        market = [(day, 1000.0) for day, _close in bars]
        foreign = {
            "2026-10-16": 10,
            "2026-10-19": 20,
            "2026-10-20": 30,
            "2026-10-21": 40,
            "2026-10-22": 50,
        }
        flows = {
            day: {"foreign": lots, "trust": 1, "dealer": -1}
            for day, lots in foreign.items()
        }
        week = week_window(bars, market, {}, trade_date="2026-10-22", flows_by_date=flows)
        assert week is not None
        self.assertEqual(week["trade_date"], "2026-10-22")
        self.assertEqual(len(week["sessions"]), 5)
        self.assertEqual(week["week_return_pct"], 8.91)
        self.assertEqual(week["week_foreign_net_lots"], 150)
        self.assertEqual(week["week_trust_net_lots"], 5)
        self.assertEqual(week["week_dealer_net_lots"], -5)
        self.assertEqual(week["rs_period"], "outperform")
