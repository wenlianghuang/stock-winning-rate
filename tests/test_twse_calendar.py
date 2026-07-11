"""Tests for TWSE official trading calendar."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "tw-stock-report"
sys.path.insert(0, str(SKILL_DIR))

from twse_calendar import (  # noqa: E402
    clear_schedule_cache,
    fetch_trading_dates,
    is_trading_day,
    market_traded_on,
    resolve_trade_date,
    resolve_lookback_dates,
    _parse_schedule_csv,
)

SAMPLE_CSV = '''"中華民國115年有價證券集中交易市場開（休）市日期表"
"日期","名稱","說明","備註(* : 市場無交易，僅辦理結算交割作業。o : 交易日。)"
"1月1日 (四)","中華民國開國紀念日","依規定放假1日。",""
"1月2日 (五)","國曆新年開始交易日","國曆新年開始交易。","o"
"2月11日 (三)","農曆春節前最後交易日","農曆春節前最後交易。","o"
"2月12日 (四)","市場無交易，僅辦理結算交割作業","","*"
"2月13日 (五)","市場無交易，僅辦理結算交割作業","","*"
"2月15日 (日)","農曆除夕及春節","依規定放假。",""
"2月16日 (一)","農曆除夕及春節","依規定放假。",""
"2月23日 (一)","農曆春節後開始交易日","農曆春節後開始交易。","o"
"5月1日 (五)","勞動節","依規定放假1日。",""
'''


def _mock_session() -> MagicMock:
    session = MagicMock()
    response = MagicMock()
    response.content = SAMPLE_CSV.encode("big5")
    response.raise_for_status = MagicMock()
    session.get.return_value = response
    return session


def _mock_session_with_taiex(*, volumes: dict[str, int | None]) -> MagicMock:
    session = MagicMock()

    def _get(url: str, params: dict[str, str] | None = None, **kwargs: object) -> MagicMock:
        response = MagicMock()
        if url == "https://www.twse.com.tw/holidaySchedule/holidaySchedule":
            response.content = SAMPLE_CSV.encode("big5")
            response.raise_for_status = MagicMock()
            return response

        if url == "https://api.finmindtrade.com/api/v4/data":
            trade_date = (params or {}).get("end_date", "")
            volume = volumes.get(trade_date)
            response.raise_for_status = MagicMock()
            if volume is None:
                response.json.return_value = {"status": 500, "msg": "probe failed"}
                return response
            response.json.return_value = {
                "status": 200,
                "data": (
                    [{"date": trade_date, "Trading_Volume": volume}]
                    if volume > 0
                    else []
                ),
            }
            return response

        raise AssertionError(f"unexpected url: {url}")

    session.get.side_effect = _get
    return session


class TwseCalendarTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_schedule_cache()

    def tearDown(self) -> None:
        clear_schedule_cache()

    def test_parse_schedule_csv(self) -> None:
        schedule = _parse_schedule_csv(SAMPLE_CSV, 2026)
        self.assertEqual(schedule.year, 2026)
        self.assertEqual(schedule.entries[dt.date(2026, 1, 1)].remark, "")
        self.assertEqual(schedule.entries[dt.date(2026, 1, 2)].remark, "o")
        self.assertEqual(schedule.entries[dt.date(2026, 2, 12)].remark, "*")

    def test_is_trading_day_uses_twse_schedule(self) -> None:
        session = _mock_session()
        self.assertTrue(is_trading_day(dt.date(2026, 1, 2), session=session))
        self.assertFalse(is_trading_day(dt.date(2026, 1, 1), session=session))
        self.assertFalse(is_trading_day(dt.date(2026, 2, 12), session=session))
        self.assertTrue(is_trading_day(dt.date(2026, 1, 5), session=session))

    def test_resolve_trade_date_on_holiday(self) -> None:
        session = _mock_session()
        resolved, note = resolve_trade_date("2026-01-01", session=session)
        self.assertEqual(resolved, "2025-12-31")
        self.assertIsNotNone(note)
        self.assertIn("非台股交易日", note or "")

    def test_resolve_trade_date_during_lunar_new_year(self) -> None:
        session = _mock_session()
        resolved, note = resolve_trade_date("2026-02-16", session=session)
        self.assertEqual(resolved, "2026-02-11")
        self.assertIsNotNone(note)

    def test_fetch_trading_dates_skips_weekends_and_holidays(self) -> None:
        session = _mock_session()
        dates = fetch_trading_dates(
            dt.date(2026, 1, 5),
            lookback_days=14,
            session=session,
        )
        self.assertIn("2026-01-02", dates)
        self.assertNotIn("2026-01-01", dates)
        self.assertNotIn("2026-01-03", dates)
        self.assertNotIn("2026-01-04", dates)
        self.assertIn("2026-01-05", dates)

    def test_resolve_lookback_dates(self) -> None:
        session = _mock_session()
        dates = resolve_lookback_dates("2026-01-05", 3, session=session)
        self.assertEqual(dates, ["2025-12-31", "2026-01-02", "2026-01-05"])

    def test_resolve_trade_date_walks_back_on_empty_taiex(self) -> None:
        session = _mock_session_with_taiex(
            volumes={
                "2026-01-05": 0,
                "2026-01-02": 120_000,
            }
        )
        resolved, note = resolve_trade_date("2026-01-05", session=session)
        self.assertEqual(resolved, "2026-01-02")
        self.assertIsNotNone(note)
        self.assertIn("加權指數無成交", note or "")

    def test_resolve_trade_date_keeps_calendar_on_probe_failure(self) -> None:
        session = _mock_session_with_taiex(
            volumes={
                "2026-01-05": None,
            }
        )
        resolved, note = resolve_trade_date("2026-01-05", session=session)
        self.assertEqual(resolved, "2026-01-05")
        self.assertIsNone(note)

    def test_market_traded_on_true_when_volume_positive(self) -> None:
        session = _mock_session_with_taiex(volumes={"2026-01-02": 1000})
        self.assertTrue(market_traded_on("2026-01-02", session=session))

    def test_market_traded_on_false_when_no_rows(self) -> None:
        session = _mock_session_with_taiex(volumes={"2026-01-05": 0})
        self.assertFalse(market_traded_on("2026-01-05", session=session))


if __name__ == "__main__":
    unittest.main()
