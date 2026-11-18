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
    DayWindow,
    resolve_day_window,
    resolve_trade_date,
    resolve_us_as_of,
)
from market_day_signals import (  # noqa: E402
    alignment_label,
    build_institutional_block,
    build_market_day_facts,
    build_volume_block,
    compute_bias_hint,
    day_return_pct,
    institutional_consensus,
)
from tx_night import (  # noqa: E402
    build_tx_night_block,
    fetch_tx_night_block,
    parse_tx_csv,
    session_window_label,
)
from market_day_summary import build_market_day_summary  # noqa: E402
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
        self.assertEqual(block["unit"], "twd")


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


class TestMarketDaySummaryExtract(unittest.TestCase):
    def test_preserves_newlines_for_markdown_ui(self) -> None:
        body = """
## 三、明日開盤偏誤
**整體偏誤方向：中性**
### 1. 基準情境
- **觸發條件**：開盤區間震盪
### 2. 尾部風險
- **觸發條件**：費半續弱

## 四、開盤儀表板
1. **觀察加權** → 較支持基準
2. **觀察台積電** → 支持尾部
3. **觀察那指** → 支持基準
4. **觀察量能** → 偏震盪

## 五、免責聲明
僅供參考。
"""
        summary = build_market_day_summary(
            {"trade_date": "2026-08-05", "for_session": "2026-08-06", "bias_hint": "neutral"},
            body,
        )
        self.assertIn("\n", summary["bias"])
        self.assertIn("### 1. 基準情境", summary["bias"])
        self.assertIn("- **觸發條件**", summary["bias"])
        self.assertIn("\n", summary["dashboard"])
        self.assertIn("1. **觀察加權**", summary["dashboard"])
        self.assertIn("2. **觀察台積電**", summary["dashboard"])


TX_HEADER = (
    "交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,"
    "成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,"
    "是否因訊息面暫停交易,交易時段,價差對單式委託成交量"
)

WEEKDAY_TX_CSV = f"""{TX_HEADER}
2026/11/12,TX,202611,44166,44555,43057,43178,-958,-2.17%,87240,43128,113812,43176,43196,50440,37694,,一般,,
2026/11/12,TX,202612,44157,44591,43122,43211,-939,-2.13%,3754,43160,12788,43215,43238,50560,27382,,一般,
2026/11/13,TX,202611/202612,1,2,0,1,-,-,500,-,-,1,2,3,0,,盤後,1
2026/11/13,TX,202612,43336,44045,42422,43718,558,1.29%,1679,-,-,43691,43716,50560,27382,,盤後,
2026/11/13,TX,202611,43180,44034,42371,43676,548,1.27%,79972,-,-,43654,43674,50440,37694,,盤後,
"""

WEEKEND_TX_CSV = f"""{TX_HEADER}
2026/11/06,TX,202611,41000,41200,39000,39786,-2096,-5.02%,143277,39652,100,39780,39790,50440,37694,,一般,
2026/11/09,TX,202612,40000,41000,39900,40800,700,1.75%,100,-,-,40700,40800,50560,27382,,盤後,
2026/11/09,TX,202611,39800,40400,39700,40298,646,1.63%,69882,-,-,40290,40300,50440,37694,,盤後,
"""


class TestTxNight(unittest.TestCase):
    def test_weekday_front_month_uses_settlement_gap(self) -> None:
        block = build_tx_night_block(
            parse_tx_csv(WEEKDAY_TX_CSV),
            trade_date="2026-11-12",
            for_session="2026-11-13",
        )
        self.assertTrue(block["available"])
        self.assertEqual(block["contract_month"], "202611")
        self.assertEqual(block["close"], 43676)
        self.assertEqual(block["change"], 548)
        self.assertEqual(block["change_pct"], 1.27)
        self.assertEqual(block["volume"], 79972)
        self.assertEqual(block["session_window"], "2026-11-12 15:00–2026-11-13 05:00")
        self.assertEqual(block["prior_regular"]["settlement"], 43128)
        self.assertEqual(block["prior_regular"]["close"], 43178)

    def test_monday_row_is_friday_night_not_sunday(self) -> None:
        block = build_tx_night_block(
            parse_tx_csv(WEEKEND_TX_CSV),
            trade_date="2026-11-06",
            for_session="2026-11-09",
        )
        self.assertEqual(block["attribution_date"], "2026-11-09")
        self.assertEqual(session_window_label("2026-11-06"), "2026-11-06 15:00–2026-11-07 05:00")
        self.assertEqual(block["session_window"], "2026-11-06 15:00–2026-11-07 05:00")
        self.assertEqual(block["change"], 646)
        self.assertEqual(block["prior_regular"]["settlement"], 39652)
        self.assertNotIn("2026-11-08", block["session_window"])

    def test_retries_until_session_row_exists(self) -> None:
        calls = {"n": 0}

        def fetch(_start: str, _end: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return TX_HEADER + "\n"
            return WEEKDAY_TX_CSV

        slept: list[float] = []
        block = fetch_tx_night_block(
            trade_date="2026-11-12",
            for_session="2026-11-13",
            attempts=2,
            backoff_sec=5,
            sleep=slept.append,
            fetch_csv=fetch,
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(slept, [5])
        self.assertTrue(block["available"])
        self.assertEqual(block["change"], 548)

    def test_gives_up_without_raising(self) -> None:
        def fetch(_start: str, _end: str) -> str:
            raise RuntimeError("down")

        block = fetch_tx_night_block(
            trade_date="2026-11-12",
            for_session="2026-11-13",
            attempts=1,
            fetch_csv=fetch,
        )
        self.assertFalse(block["available"])
        self.assertIn("fetch_error", block["skip_reason"])

    def test_bias_hint_ignores_night_gap(self) -> None:
        window = DayWindow(
            trade_date="2026-11-12",
            for_session="2026-11-13",
            prior_trade_date="2026-11-11",
            lookback_days=["2026-11-11", "2026-11-12"],
            resolved_as_of="2026-11-13T05:30:00+08:00",
            cutover_applied=True,
            us_as_of="2026-11-13",
            us_cutover_passed=True,
        )

        def us_fetch(_as_of: str) -> dict:
            return {
                "available": True,
                "indices": {
                    "IXIC": {"day_return_pct": 1.1, "session_date": "2026-11-12"},
                    "SOX": {"day_return_pct": 0.4, "session_date": "2026-11-12"},
                },
            }

        with (
            patch("market_day_signals.fetch_taiex_rows", return_value={}),
            patch("market_day_signals.fetch_institutional_rows", return_value=[]),
            patch("market_day_signals.fetch_stock_day", return_value={}),
        ):
            facts = build_market_day_facts(
                window,
                us_attempts=1,
                us_backoff_sec=0,
                us_sleep=lambda _sec: None,
                us_fetch=us_fetch,
                tx_attempts=1,
                tx_backoff_sec=0,
                tx_sleep=lambda _sec: None,
                tx_fetch=lambda _start, _end: WEEKDAY_TX_CSV,
            )
        self.assertEqual(facts.bias_hint, "neutral")
        self.assertEqual(facts.tx_night["change"], 548)
        self.assertEqual(facts.tx_night["alignment_vs_ixic"], "一致")
        self.assertTrue(any("台指期盤後" in line for line in facts.anchors))


class TestTxNightValidation(unittest.TestCase):
    def test_requires_gap_citation(self) -> None:
        facts = {
            "tx_night": {
                "available": True,
                "change": 548,
                "change_pct": 1.27,
                "alignment_vs_ixic": "一致",
            }
        }
        result = validate_market_day_report("夜盤下跌，沒有數字。", facts)
        codes = {issue.code for issue in result.issues}
        self.assertIn("tx_direction", codes)
        self.assertIn("missing_tx_change", codes)
        self.assertIn("missing_tx_gap", codes)

    def test_unavailable_needs_explicit_sentence(self) -> None:
        result = validate_market_day_report("外盤只有那指。", {"tx_night": {"available": False}})
        self.assertIn("missing_tx_night", {issue.code for issue in result.issues})

    def test_legacy_facts_without_night_key(self) -> None:
        result = validate_market_day_report("沒有夜盤欄位。", {})
        self.assertNotIn("missing_tx_night", {issue.code for issue in result.issues})


class TestDailyNarrativeSkill(unittest.TestCase):
    def test_prompt_inlines_skill_body_and_facts(self) -> None:
        from market_day_prompts import build_market_day_prompt

        prompt = build_market_day_prompt(facts_summary="bias_hint=neutral")
        self.assertIn("## 三、明日開盤偏誤", prompt)
        self.assertIn("不得改寫 bias_hint", prompt)
        self.assertIn(
            "======= FACTS（唯讀）=======\nbias_hint=neutral\n======= END FACTS =======",
            prompt,
        )
        self.assertNotIn("name: daily-narrative", prompt)
        self.assertNotIn("# 開盤前正文", prompt)


if __name__ == "__main__":
    unittest.main()
