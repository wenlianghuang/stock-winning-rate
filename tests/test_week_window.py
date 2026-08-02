"""Tests for market-weekly Friday 17:30 week window cutover."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "market-weekly"
sys.path.insert(0, str(SKILL_DIR))

from week_window import (  # noqa: E402
    TAIPEI_TZ,
    resolve_week_window,
    target_week_friday,
    this_friday,
)


def _tp(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=TAIPEI_TZ)


def _weekdays_only(start: dt.date, end: dt.date, *, session=None) -> list[str]:
    days: list[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += dt.timedelta(days=1)
    return days


class TestWeekWindow(unittest.TestCase):
    def test_this_friday(self) -> None:
        self.assertEqual(this_friday(dt.date(2026, 7, 29)), dt.date(2026, 7, 31))
        self.assertEqual(this_friday(dt.date(2026, 7, 31)), dt.date(2026, 7, 31))
        self.assertEqual(this_friday(dt.date(2026, 8, 1)), dt.date(2026, 7, 31))

    def test_friday_before_cutover_uses_previous_week(self) -> None:
        friday, applied = target_week_friday(_tp(2026, 7, 31, 17, 29))
        self.assertEqual(friday, dt.date(2026, 7, 24))
        self.assertTrue(applied)

    def test_friday_at_cutover_uses_current_week(self) -> None:
        friday, applied = target_week_friday(_tp(2026, 7, 31, 17, 30))
        self.assertEqual(friday, dt.date(2026, 7, 31))
        self.assertTrue(applied)

    def test_wednesday_uses_previous_week(self) -> None:
        friday, _ = target_week_friday(_tp(2026, 7, 29, 10, 0))
        self.assertEqual(friday, dt.date(2026, 7, 24))

    def test_saturday_uses_current_week(self) -> None:
        friday, _ = target_week_friday(_tp(2026, 8, 1, 9, 0))
        self.assertEqual(friday, dt.date(2026, 7, 31))

    @patch("week_window.trading_days_in_range", side_effect=_weekdays_only)
    def test_resolve_before_cutover(self, _mock) -> None:
        window = resolve_week_window(_tp(2026, 7, 31, 17, 29))
        self.assertEqual(window.week_friday, dt.date(2026, 7, 24))
        self.assertEqual(window.week_monday, dt.date(2026, 7, 20))
        self.assertEqual(window.week_start, "2026-07-20")
        self.assertEqual(window.week_end, "2026-07-24")
        self.assertTrue(window.cutover_applied)

    @patch("week_window.trading_days_in_range", side_effect=_weekdays_only)
    def test_resolve_after_cutover(self, _mock) -> None:
        window = resolve_week_window(_tp(2026, 7, 31, 17, 30))
        self.assertEqual(window.week_end, "2026-07-31")
        self.assertEqual(window.week_start, "2026-07-27")
        self.assertTrue(window.cutover_applied)

    @patch("week_window.trading_days_in_range", side_effect=_weekdays_only)
    def test_wednesday_resolve(self, _mock) -> None:
        window = resolve_week_window(_tp(2026, 7, 29, 12, 0))
        self.assertEqual(window.week_monday, dt.date(2026, 7, 20))
        self.assertEqual(window.trading_days[-1], "2026-07-24")

    @patch("week_window.trading_days_in_range", side_effect=_weekdays_only)
    def test_explicit_week_end_skips_cutover(self, _mock) -> None:
        window = resolve_week_window(_tp(2026, 8, 1), week_end="2026-07-17")
        self.assertEqual(window.week_end, "2026-07-17")
        self.assertEqual(window.week_start, "2026-07-13")
        self.assertFalse(window.cutover_applied)


if __name__ == "__main__":
    unittest.main()
