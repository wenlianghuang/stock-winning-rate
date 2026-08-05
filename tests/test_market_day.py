"""Tests for market-daily day window, bias rules, and validation."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".agents" / "skills" / "market-daily"
TWSE = ROOT / ".agents" / "skills" / "tw-stock-report"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(TWSE))
sys.path.insert(0, str(ROOT / "ui"))

from day_window import (  # noqa: E402
    CUTOVER_HOUR,
    CUTOVER_MINUTE,
    TAIPEI_TZ,
    resolve_day_window,
    resolve_trade_date,
    resolve_us_as_of,
)
from market_day_signals import (  # noqa: E402
    alignment_label,
    build_institutional_block,
    build_volume_block,
    compute_bias_hint,
    day_return_pct,
    institutional_consensus,
)
from validate_market_day import validate_market_day_report  # noqa: E402


def _weekday_trading(day: dt.date, *, session=None) -> bool:
    return day.weekday() < 5


def _prev_weekday(trade_date: str, *, session=None) -> str | None:
    day = dt.date.fromisoformat(trade_date) - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat()


def _fetch_weekdays(end_date: dt.date, lookback_days: int = 90, *, session=None):
    start = end_date - dt.timedelta(days=lookback_days)
    out = []
    cur = start
    while cur <= end_date:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return out


class TestDayReturn(unittest.TestCase):
    def test_day_return(self) -> None:
        self.assertEqual(day_return_pct(100.0, 101.0), 1.0)
        self.assertEqual(day_return_pct(100.0, 99.0), -1.0)
        self.assertIsNone(day_return_pct(0.0, 1.0))


class TestAlignment(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertEqual(alignment_label(1.0, 2.0), "一致")
        self.assertEqual(alignment_label(1.0, -2.0), "背離")
        self.assertEqual(alignment_label(None, 1.0), "unavailable")


class TestInstitutional(unittest.TestCase):
    def test_consensus(self) -> None:
        self.assertEqual(institutional_consensus(100, 50, 10), "bullish")
        self.assertEqual(institutional_consensus(-100, -50, -10), "bearish")
        self.assertEqual(institutional_consensus(100, -50, 10), "mixed")
        self.assertEqual(institutional_consensus(0, 0, 0), "neutral")

    def test_build_block(self) -> None:
        rows = [
            {"date": "2026-07-31", "name": "Foreign_Investor", "buy": 2000, "sell": 1000},
            {"date": "2026-07-31", "name": "Investment_Trust", "buy": 500, "sell": 100},
            {"date": "2026-07-31", "name": "Dealer_self", "buy": 100, "sell": 50},
            {"date": "2026-07-30", "name": "Foreign_Investor", "buy": 9, "sell": 1},
        ]
        block = build_institutional_block("2026-07-31", rows)
        self.assertTrue(block["available"])
        self.assertEqual(block["foreign_net"], 1000)
        self.assertEqual(block["trust_net"], 400)
        self.assertEqual(block["dealer_net"], 50)
        self.assertEqual(block["consensus"], "bullish")


class TestVolume(unittest.TestCase):
    def test_regime(self) -> None:
        lookback = [f"2026-07-{d:02d}" for d in range(1, 22)]
        rows = {day: {"Trading_Volume": 1000} for day in lookback[:-1]}
        rows[lookback[-1]] = {"Trading_Volume": 1500}
        block = build_volume_block(lookback[-1], lookback, rows)
        self.assertEqual(block["regime"], "expand")
        self.assertEqual(block["vs_avg5_ratio"], 1.5)


class TestBiasHint(unittest.TestCase):
    def test_bullish_blend(self) -> None:
        hint = compute_bias_hint(
            market={"day_return_pct": 1.2},
            volume={"regime": "expand"},
            institutional={"consensus": "bullish"},
            technical={"vs_ma5": "above"},
            us={"alignment": {"ixic_vs_taiex": "一致"}},
        )
        self.assertEqual(hint, "bullish")

    def test_neutral_default(self) -> None:
        hint = compute_bias_hint(
            market={"day_return_pct": 0.1},
            volume={"regime": "normal"},
            institutional={"consensus": "mixed"},
            technical={"vs_ma5": "near"},
            us={"alignment": {"ixic_vs_taiex": "unavailable"}},
        )
        self.assertEqual(hint, "neutral")


class TestDayWindow(unittest.TestCase):
    def test_before_cutover_uses_prior(self) -> None:
        now = dt.datetime(2026, 7, 31, 14, 59, tzinfo=TAIPEI_TZ)
        with (
            patch("twse_calendar.is_trading_day", side_effect=_weekday_trading),
            patch("twse_calendar.previous_trading_day", side_effect=_prev_weekday),
        ):
            day, applied = resolve_trade_date(now)
        self.assertEqual(day.isoformat(), "2026-07-30")
        self.assertTrue(applied)
        self.assertEqual(CUTOVER_HOUR, 15)
        self.assertEqual(CUTOVER_MINUTE, 0)

    def test_after_cutover_uses_today(self) -> None:
        now = dt.datetime(2026, 7, 31, 15, 0, tzinfo=TAIPEI_TZ)
        with patch("twse_calendar.is_trading_day", side_effect=_weekday_trading):
            day, applied = resolve_trade_date(now)
        self.assertEqual(day.isoformat(), "2026-07-31")
        self.assertTrue(applied)

    def test_forced_date(self) -> None:
        with (
            patch("twse_calendar.is_trading_day", side_effect=_weekday_trading),
            patch("twse_calendar.fetch_trading_dates", side_effect=_fetch_weekdays),
        ):
            window = resolve_day_window(trade_date="2026-07-31")
        self.assertEqual(window.trade_date, "2026-07-31")
        self.assertEqual(window.for_session, "2026-08-03")
        self.assertFalse(window.cutover_applied)
        self.assertIn("us_as_of", window.as_dict())


class TestUsAsOf(unittest.TestCase):
    def test_before_0530_uses_yesterday(self) -> None:
        now = dt.datetime(2026, 8, 4, 5, 29, tzinfo=TAIPEI_TZ)
        us_as_of, passed = resolve_us_as_of(now)
        self.assertEqual(us_as_of.isoformat(), "2026-08-03")
        self.assertFalse(passed)

    def test_at_0530_uses_today(self) -> None:
        now = dt.datetime(2026, 8, 4, 5, 30, tzinfo=TAIPEI_TZ)
        us_as_of, passed = resolve_us_as_of(now)
        self.assertEqual(us_as_of.isoformat(), "2026-08-04")
        self.assertTrue(passed)


class TestValidate(unittest.TestCase):
    def _facts(self) -> dict:
        return {
            "bias_hint": "neutral",
            "market": {"day_return_pct": -1.18, "close": 43119.75},
            "volume": {"vs_avg5_ratio": 0.9, "regime": "normal"},
            "technical": {"ma5": 43000.0, "ma20": 42500.0},
            "tsmc": {"day_return_pct": 1.2, "close": 2425.0, "available": True},
            "institutional": {
                "available": True,
                "consensus": "mixed",
                "foreign_net": -1000000,
            },
            "us": {
                "available": True,
                "indices": {
                    "IXIC": {"day_return_pct": 0.5},
                    "SOX": {"day_return_pct": -0.8},
                },
                "alignment": {
                    "ixic_vs_taiex": "背離",
                    "sox_vs_tsmc": "背離",
                },
            },
        }

    def test_passes_minimal_good_report(self) -> None:
        body = """
## 一、今日結構
加權日報酬 -1.18%，收在 43119.75 點附近，量能相對五日均量 0.9x，三大法人方向分歧，台積電日報酬 1.2%。盤面呈現權值撐住但廣基偏弱。

## 二、外盤改寫
- 那指日報酬 0.5% 與大盤 -1.18% 呈現背離
- 費半日報酬 -0.8% 與台積電 1.2% 呈現背離
本版未納入台指夜盤。

## 三、明日開盤偏誤
整體偏誤為中性，不宜強行解讀成單邊。
### 基準情境
結構連貫：量縮震盪。觸發條件：開盤區間震盪。開盤含義：高位整理。
可追蹤訊號：加權站穩 43119.75。否決條件：跌破 MA5。
### 尾部風險
結構連貫：外盤背離擴大。觸發：費半續弱。開盤含義：權值承壓。
可追蹤：2330。否決條件：費半轉強。

## 四、開盤儀表板
- 觀察加權開盤 → 若站穩收盤 → 較支持基準
- 觀察台積電 → 若走弱 → 支持尾部
- 觀察那指對帳 → 若背離收斂 → 支持基準
- 觀察量能 → 若急縮 → 偏震盪

## 五、免責聲明
僅供參考，不構成投資建議。
"""
        result = validate_market_day_report(body, self._facts())
        self.assertTrue(result.passed, result.summary_lines())

    def test_rejects_forbidden(self) -> None:
        body = "保證獲利 一定漲 " + ("觀察重點\n- a\n- b\n- c\n- d\n" * 20)
        result = validate_market_day_report(body, self._facts())
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
