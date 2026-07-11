"""Tests for US index snapshot parsing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from us_indices import (  # noqa: E402
    US_MARKET_INDICES,
    IndexSnapshot,
    fetch_index_snapshot,
    fetch_us_market_indices_payload,
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


class UsIndicesTests(unittest.TestCase):
    def test_us_market_indices_order(self) -> None:
        symbols = [item["symbol"] for item in US_MARKET_INDICES]
        self.assertEqual(symbols, ["^DJI", "^IXIC", "^SOX"])

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


if __name__ == "__main__":
    unittest.main()
