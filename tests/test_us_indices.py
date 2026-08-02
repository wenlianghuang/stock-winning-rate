"""Tests for US index snapshot parsing and weekly bars."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from us_indices import (  # noqa: E402
    US_MARKET_INDICES,
    US_WEEKLY_INDICES,
    IndexSnapshot,
    build_us_week_block,
    fetch_index_snapshot,
    fetch_us_market_indices_payload,
    index_week_from_closes,
    parse_chart_daily_closes,
    week_return_pct,
)


def _yahoo_payload(*, price: float, previous: float, ts: int = 1_783_718_915) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "chartPreviousClose": previous,
                        "currency": "USD",
                        "regularMarketTime": ts,
                    }
                }
            ]
        }
    }


def _chart_bars_payload(closes_by_day: dict[str, float]) -> dict:
    """Build Yahoo chart payload from {YYYY-MM-DD: close} (UTC noon)."""
    from datetime import datetime, timezone

    timestamps: list[int] = []
    closes: list[float] = []
    for day, close in sorted(closes_by_day.items()):
        y, m, d = (int(x) for x in day.split("-"))
        timestamps.append(
            int(datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp())
        )
        closes.append(close)
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


class UsIndicesTests(unittest.TestCase):
    def test_us_market_indices_order(self) -> None:
        symbols = [item["symbol"] for item in US_MARKET_INDICES]
        self.assertEqual(symbols, ["^DJI", "^IXIC", "^SOX"])

    def test_weekly_indices_are_ixic_sox(self) -> None:
        keys = [item["key"] for item in US_WEEKLY_INDICES]
        self.assertEqual(keys, ["IXIC", "SOX"])

    @patch("us_indices.requests.Session")
    def test_fetch_index_snapshot_computes_change(self, session_cls: MagicMock) -> None:
        response = MagicMock()
        response.json.return_value = _yahoo_payload(price=52_637.01, previous=52_900.07)
        response.raise_for_status.return_value = None

        session = MagicMock()
        session.get.return_value = response
        session_cls.return_value = session

        snap = fetch_index_snapshot("^DJI", name="道瓊", short_name="道瓊")
        self.assertEqual(snap.symbol, "^DJI")
        self.assertAlmostEqual(snap.price or 0, 52_637.01)
        self.assertAlmostEqual(snap.change_pct or 0, -0.497, places=2)

    @patch("us_indices.fetch_us_market_indices")
    def test_fetch_us_market_indices_payload(self, fetch_all: MagicMock) -> None:
        fetch_all.return_value = [
            IndexSnapshot("^DJI", "道瓊", "道瓊", 1.0, 1.0, 0.0, "USD", ""),
            IndexSnapshot("^IXIC", "那斯達克", "那斯達克", 2.0, 2.0, 0.0, "USD", ""),
            IndexSnapshot("^SOX", "費半", "費半", 3.0, 3.0, 0.0, "USD", ""),
        ]

        payload = fetch_us_market_indices_payload()
        self.assertEqual(len(payload["indices"]), 3)
        self.assertEqual(payload["indices"][0]["symbol"], "^DJI")
        self.assertIn("fetched_at", payload)

    def test_week_return_and_index_week_from_closes(self) -> None:
        self.assertEqual(week_return_pct(100.0, 110.0), 10.0)
        closes = {
            "2026-07-27": 100.0,
            "2026-07-28": 102.0,
            "2026-07-31": 108.0,
            "2026-08-01": 120.0,  # outside window
        }
        block = index_week_from_closes(
            closes,
            symbol="^IXIC",
            name="那斯達克綜合指數",
            short_name="那斯達克",
            start="2026-07-27",
            end="2026-07-31",
        )
        assert block is not None
        self.assertEqual(block["session_count"], 3)
        self.assertEqual(block["week_return_pct"], 8.0)
        self.assertEqual(block["first_session"], "2026-07-27")
        self.assertEqual(block["last_session"], "2026-07-31")

    def test_index_week_requires_two_sessions(self) -> None:
        block = index_week_from_closes(
            {"2026-07-28": 100.0},
            symbol="^SOX",
            name="費半",
            short_name="費半",
            start="2026-07-27",
            end="2026-07-31",
        )
        self.assertIsNone(block)

    def test_parse_chart_daily_closes(self) -> None:
        payload = _chart_bars_payload(
            {"2026-07-27": 20000.0, "2026-07-31": 20200.0}
        )
        closes = parse_chart_daily_closes(payload)
        self.assertEqual(closes["2026-07-27"], 20000.0)
        self.assertEqual(closes["2026-07-31"], 20200.0)

    @patch("us_indices.fetch_index_week_bars")
    def test_build_us_week_block(self, fetch_bars: MagicMock) -> None:
        def _side_effect(symbol: str, start, end, session=None):  # noqa: ANN001
            if symbol == "^IXIC":
                return {
                    "2026-07-27": 100.0,
                    "2026-07-31": 103.0,
                }
            return {
                "2026-07-27": 5000.0,
                "2026-07-31": 4900.0,
            }

        fetch_bars.side_effect = _side_effect
        block = build_us_week_block("2026-07-27", "2026-07-31")
        self.assertTrue(block["available"])
        self.assertEqual(block["indices"]["IXIC"]["week_return_pct"], 3.0)
        self.assertEqual(block["indices"]["SOX"]["week_return_pct"], -2.0)


if __name__ == "__main__":
    unittest.main()
