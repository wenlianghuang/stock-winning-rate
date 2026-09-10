"""GET /market-daily/current returns the shared brief contract."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.schedule import CanonicalBrief  # noqa: E402


class _Window:
    trade_date = "2026-08-03"
    for_session = "2026-08-04"

    def as_dict(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "for_session": self.for_session,
            "us_cutover_passed": True,
        }


class MarketDailyCurrentApiTests(unittest.TestCase):
    def test_current_is_shared(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")

        brief = CanonicalBrief(
            trade_date="2026-08-03",
            for_session="2026-08-04",
            ready=True,
            us_available=True,
            facts={"us": {"available": True}},
            summary={},
            markdown="# brief",
            facts_path=None,
            md_path=None,
        )
        with patch(
            "market_day_signals.resolve_window_or_fail",
            return_value=_Window(),
        ), patch(
            "agent.schedule.load_canonical_brief",
            return_value=brief,
        ):
            client = TestClient(create_app())
            response = client.get("/market-daily/current")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["shared"])
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["brief"]["trade_date"], "2026-08-03")
        self.assertTrue(payload["brief"]["us_available"])


if __name__ == "__main__":
    unittest.main()
