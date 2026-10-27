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

from yahoo_board import (  # noqa: E402
    assemble_yahoo_board_facts,
    chart_bars,
    credit_chip_inputs,
    foreign_cumulative,
    yahoo_chip_inputs_ready,
)


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
        self.assertIsNone(facts["margin_today_delta_lots"])
        self.assertIsNone(facts["margin_period_delta_lots"])
        self.assertIsNone(facts["foreign_cum_lots"])
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

    def test_credit_rows_match_margin_page_deltas(self) -> None:
        rows = [
            {"date": "2026-10-15T00:00:00+08:00", "financingTotalVolK": "3815", "shortTotalVolK": "21"},
            {"date": "2026-10-16T00:00:00+08:00", "financingTotalVolK": "3734", "shortTotalVolK": "40"},
            {"date": "2026-10-19T00:00:00+08:00", "financingTotalVolK": "3662", "shortTotalVolK": "27"},
            {"date": "2026-10-20T00:00:00+08:00", "financingTotalVolK": "3655", "shortTotalVolK": "29"},
            {"date": "2026-10-21T00:00:00+08:00", "financingTotalVolK": "3852", "shortTotalVolK": "17"},
            {"date": "2026-10-22T00:00:00+08:00", "financingTotalVolK": "3612", "shortTotalVolK": "24"},
        ]
        today, period_margin, period_short = credit_chip_inputs(
            rows,
            trade_date="2026-10-22",
            anchor_date="2026-10-15",
        )
        self.assertEqual(today, -240)
        self.assertEqual(period_margin, -203)
        self.assertEqual(period_short, 3)

    def test_foreign_cumulative_requires_every_session(self) -> None:
        rows = [
            {"date": "2026-10-16", "foreignDiffVolK": 10},
            {"date": "2026-10-19", "foreignDiffVolK": 20},
            {"date": "2026-10-20", "foreignDiffVolK": 30},
            {"date": "2026-10-21", "foreignDiffVolK": 40},
            {"date": "2026-10-22", "foreignDiffVolK": 300},
        ]
        dates = [row["date"] for row in rows]
        self.assertEqual(foreign_cumulative(rows, dates), 400)
        self.assertIsNone(foreign_cumulative(rows[:-1], dates))

    def test_five_session_foreign_sum_can_reach_accumulation(self) -> None:
        bars = [(f"2026-10-{day:02d}", 100.0) for day in (13, 14, 15, 16, 19, 20)]
        bars.append(("2026-10-21", 100.2))
        market = [(day, 1000.0) for day, _close in bars]
        shared = dict(
            stock_id="3017",
            stock_name="奇鋐",
            bars=bars,
            market_bars=market,
            trades={"foreignDiffVolK": 10, "investmentTrustDiffVolK": 0, "dealerDiffVolK": 0},
            major_net=0,
            trade_date="2026-10-21",
        )
        without = assemble_yahoo_board_facts(**shared)
        with_cum = assemble_yahoo_board_facts(**shared, foreign_cum=300)
        assert without is not None and with_cum is not None
        self.assertEqual(without["chip_regime"], "mixed")
        self.assertEqual(with_cum["chip_regime"], "accumulation")
        self.assertEqual(with_cum["foreign_cum_lots"], 300)

    def test_margin_increase_while_price_dips_marks_distribution(self) -> None:
        bars = [(f"2026-10-{day:02d}", 100.0) for day in (13, 14, 15, 16, 19, 20)]
        bars.append(("2026-10-21", 99.5))
        market = [(day, 1000.0) for day, _close in bars]
        shared = dict(
            stock_id="3017",
            stock_name="奇鋐",
            bars=bars,
            market_bars=market,
            trades={"foreignDiffVolK": -10, "investmentTrustDiffVolK": -5, "dealerDiffVolK": -1},
            major_net=0,
            trade_date="2026-10-21",
        )
        without = assemble_yahoo_board_facts(**shared)
        with_margin = assemble_yahoo_board_facts(
            **shared,
            margin_today_delta=80,
            margin_period_delta=200,
            short_period_delta=0,
        )
        assert without is not None and with_margin is not None
        self.assertEqual(without["chip_regime"], "mixed")
        self.assertEqual(with_margin["chip_regime"], "distribution")
        self.assertEqual(with_margin["margin_period_delta_lots"], 200)

    def test_old_sidecar_without_chip_inputs_is_not_ready(self) -> None:
        self.assertFalse(yahoo_chip_inputs_ready({"source": "yahoo", "chip_regime": "mixed"}))
        self.assertTrue(
            yahoo_chip_inputs_ready(
                {"foreign_cum_lots": None, "margin_period_delta_lots": None}
            )
        )
