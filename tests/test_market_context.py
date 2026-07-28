"""Tests for TAIEX market context (single-fetch + local per-date slice)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "tw-stock-report"
SCRIPT = SKILL_DIR / "fetch_chip_report.py"
sys.path.insert(0, str(SKILL_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_chip_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_chip_report"] = module
    spec.loader.exec_module(module)
    return module


def _fake_taiex_rows(n: int = 30, start: str = "2026-05-01") -> dict[str, dict]:
    """Synthetic ascending closes for MA / period-return checks."""
    from datetime import date, timedelta

    day = date.fromisoformat(start)
    rows: dict[str, dict] = {}
    close = 20000.0
    for _ in range(n):
        # skip weekends so lookback_dates-like sequences stay weekday-ish
        while day.weekday() >= 5:
            day += timedelta(days=1)
        spread = 50.0
        rows[day.isoformat()] = {
            "close": close,
            "spread": spread,
        }
        close += 20.0
        day += timedelta(days=1)
    return rows


class MarketContextFromRowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def test_empty_rows_returns_blank_columns(self) -> None:
        empty = self.mod.market_context_from_rows({}, "2026-06-01", ["2026-06-01"])
        self.assertEqual(empty, {col: "" for col in self.mod.MARKET_COLUMNS})

    def test_ma_and_change_pct(self) -> None:
        rows = _fake_taiex_rows(25)
        dates = sorted(rows)
        trade_date = dates[-1]
        lookback = dates[-5:]
        ctx = self.mod.market_context_from_rows(rows, trade_date, lookback)

        closes = [float(rows[d]["close"]) for d in dates if d <= trade_date]
        expected_ma5 = round(sum(closes[-5:]) / 5, 2)
        expected_ma20 = round(sum(closes[-20:]) / 20, 2)
        last = closes[-1]
        self.assertEqual(ctx["大盤收盤"], last)
        self.assertEqual(ctx["大盤MA5"], expected_ma5)
        self.assertEqual(ctx["大盤MA20"], expected_ma20)
        # spread=50, prev = last - 50
        self.assertEqual(ctx["大盤漲跌幅_%"], round(50.0 / (last - 50.0) * 100, 2))

        first = float(rows[lookback[0]]["close"])
        self.assertEqual(
            ctx["大盤區間漲跌幅_%"],
            round((last - first) / first * 100, 2),
        )

    def test_full_range_slice_matches_per_day_subset(self) -> None:
        """Backfill-style one fetch must match per-day fetch window results."""
        full = _fake_taiex_rows(30)
        dates = sorted(full)
        mid = dates[20]
        lookback = dates[16:21]  # 5-day chip lookback ending at mid

        from_full = self.mod.market_context_from_rows(full, mid, lookback)
        # Simulate old per-day fetch: only rows from lookback/MA start onward
        ma_start_idx = max(0, dates.index(mid) - 19)
        subset = {d: full[d] for d in dates[ma_start_idx:] if d <= mid}
        from_subset = self.mod.market_context_from_rows(subset, mid, lookback)
        self.assertEqual(from_full, from_subset)

    def test_no_look_ahead_ignores_future_closes(self) -> None:
        rows = _fake_taiex_rows(10)
        dates = sorted(rows)
        early = dates[4]
        lookback = dates[2:5]
        ctx = self.mod.market_context_from_rows(rows, early, lookback)
        self.assertEqual(ctx["大盤收盤"], float(rows[early]["close"]))
        # MA5 needs 5 closes on/before early
        closes = [float(rows[d]["close"]) for d in dates if d <= early]
        self.assertEqual(ctx["大盤MA5"], round(sum(closes[-5:]) / 5, 2))


if __name__ == "__main__":
    unittest.main()
