"""TWSE official market open/close schedule (開休市日期表)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
import threading
from dataclasses import dataclass

import requests

TWSE_HOLIDAY_CSV_URL = "https://www.twse.com.tw/holidaySchedule/holidaySchedule"
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
MARKET_INDEX_ID = "TAIEX"
CHIP_READY_HOUR = 21
CHIP_READY_MINUTE = 30
TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8))

_DATE_COL_RE = re.compile(r"^(\d+)月(\d+)日")
_ROC_YEAR_RE = re.compile(r"(\d+)年")


@dataclass(frozen=True)
class ScheduleEntry:
    name: str
    description: str
    remark: str  # "" = 休市, "o" = 交易日, "*" = 僅結算交割


@dataclass(frozen=True)
class YearSchedule:
    year: int
    entries: dict[dt.date, ScheduleEntry]


_cache: dict[int, YearSchedule] = {}
_cache_lock = threading.Lock()


def _parse_schedule_csv(text: str, fallback_year: int) -> YearSchedule:
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("證交所開休市 CSV 為空")

    year = fallback_year
    roc_match = _ROC_YEAR_RE.search(rows[0][0])
    if roc_match:
        year = int(roc_match.group(1)) + 1911

    entries: dict[dt.date, ScheduleEntry] = {}
    for row in rows[2:]:
        if not row:
            continue
        date_match = _DATE_COL_RE.match(row[0].strip())
        if not date_match:
            continue
        month = int(date_match.group(1))
        day = int(date_match.group(2))
        entry_date = dt.date(year, month, day)
        name = row[1].strip() if len(row) > 1 else ""
        description = row[2].strip() if len(row) > 2 else ""
        remark = row[3].strip() if len(row) > 3 else ""
        entries[entry_date] = ScheduleEntry(
            name=name,
            description=description,
            remark=remark,
        )

    return YearSchedule(year=year, entries=entries)


def fetch_year_schedule(
    year: int,
    *,
    session: requests.Session | None = None,
) -> YearSchedule:
    with _cache_lock:
        cached = _cache.get(year)
        if cached is not None:
            return cached

    http = session or requests.Session()
    response = http.get(
        TWSE_HOLIDAY_CSV_URL,
        params={"response": "csv", "date": str(year)},
        timeout=30,
    )
    response.raise_for_status()
    schedule = _parse_schedule_csv(response.content.decode("big5"), year)
    with _cache_lock:
        _cache[year] = schedule
    return schedule


def clear_schedule_cache() -> None:
    with _cache_lock:
        _cache.clear()


def is_trading_day(day: dt.date, *, session: requests.Session | None = None) -> bool:
    schedule = fetch_year_schedule(day.year, session=session)
    entry = schedule.entries.get(day)
    if entry is not None:
        return entry.remark == "o"
    return day.weekday() < 5


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI_TZ)


def chip_reference_date(now: dt.datetime | None = None) -> dt.date:
    """Calendar date to resolve before applying TWSE trading-day rules."""
    current = now or taipei_now()
    ref = current.date()
    minutes = current.hour * 60 + current.minute
    if minutes < CHIP_READY_HOUR * 60 + CHIP_READY_MINUTE:
        ref -= dt.timedelta(days=1)
    return ref


def fetch_trading_dates(
    end_date: dt.date,
    lookback_days: int = 90,
    *,
    session: requests.Session | None = None,
) -> list[str]:
    start_date = end_date - dt.timedelta(days=lookback_days)
    result: list[str] = []
    current = start_date
    while current <= end_date:
        if is_trading_day(current, session=session):
            result.append(current.isoformat())
        current += dt.timedelta(days=1)
    return result


def previous_trading_day(
    trade_date: str,
    *,
    session: requests.Session | None = None,
) -> str | None:
    """Return the latest calendar trading day strictly before trade_date."""
    end = dt.datetime.strptime(trade_date, "%Y-%m-%d").date() - dt.timedelta(days=1)
    trading_dates = fetch_trading_dates(
        end,
        lookback_days=max(180, (end - dt.date(end.year, 1, 1)).days + 7),
        session=session,
    )
    if not trading_dates:
        return None
    return trading_dates[-1]


def fetch_taiex_trading_volume(
    trade_date: str,
    *,
    session: requests.Session | None = None,
    token: str | None = None,
) -> int | None:
    """Return TAIEX volume for a date, or None when the probe cannot be trusted."""
    http = session or requests.Session()
    headers: dict[str, str] = {}
    auth_token = (token if token is not None else os.environ.get("FINMIND_TOKEN", "")).strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        response = http.get(
            FINMIND_DATA_URL,
            params={
                "dataset": "TaiwanStockPrice",
                "data_id": MARKET_INDEX_ID,
                "start_date": trade_date,
                "end_date": trade_date,
            },
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None

    if payload.get("status") != 200:
        return None

    rows = payload.get("data") or []
    for row in rows:
        if str(row.get("date")) != trade_date:
            continue
        try:
            return int(row.get("Trading_Volume") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def market_traded_on(
    trade_date: str,
    *,
    session: requests.Session | None = None,
    token: str | None = None,
) -> bool | None:
    """Return True/False when TAIEX confirms trading, or None on probe failure."""
    volume = fetch_taiex_trading_volume(
        trade_date,
        session=session,
        token=token,
    )
    if volume is None:
        return None
    return volume > 0


def _resolve_calendar_trade_date(
    requested_date: str | None = None,
    *,
    now: dt.datetime | None = None,
    session: requests.Session | None = None,
) -> tuple[str, str | None]:
    """Return the latest TWSE calendar trading day on or before the reference date."""
    reference = (
        dt.datetime.strptime(requested_date, "%Y-%m-%d").date()
        if requested_date
        else chip_reference_date(now)
    )

    trading_dates = fetch_trading_dates(
        reference,
        lookback_days=max(180, (reference - dt.date(reference.year, 1, 1)).days + 7),
        session=session,
    )
    if not trading_dates:
        fallback = (reference - dt.timedelta(days=1)).isoformat()
        return fallback, f"無法取得證交所交易日曆，暫用 {fallback}"

    eligible = [d for d in trading_dates if d <= reference.isoformat()]
    resolved = eligible[-1] if eligible else trading_dates[-1]

    if resolved == reference.isoformat():
        return resolved, None

    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    ref_label = (
        f"指定日期 {requested_date}"
        if requested_date
        else (
            f"今日（{reference.isoformat()} 星期"
            f"{weekday_names[reference.weekday()]}）"
        )
    )
    note = f"{ref_label} 非台股交易日，已改用最近交易日 {resolved}"
    return resolved, note


def _apply_market_validation(
    trade_date: str,
    note: str | None,
    *,
    session: requests.Session | None = None,
    token: str | None = None,
) -> tuple[str, str | None]:
    """Walk back when the calendar says open but TAIEX shows no trading."""
    current = trade_date
    updated_note = note
    while True:
        traded = market_traded_on(current, session=session, token=token)
        if traded is None or traded:
            return current, updated_note

        previous = previous_trading_day(current, session=session)
        if previous is None or previous == current:
            return current, updated_note

        market_note = (
            f"{current} 加權指數無成交（可能為臨時休市），"
            f"已改用最近交易日 {previous}"
        )
        updated_note = market_note if updated_note is None else f"{updated_note}；{market_note}"
        current = previous


def resolve_trade_date(
    requested_date: str | None = None,
    *,
    now: dt.datetime | None = None,
    session: requests.Session | None = None,
    validate_market: bool = True,
    finmind_token: str | None = None,
) -> tuple[str, str | None]:
    """Return the latest trading day, using calendar rules plus optional TAIEX probe."""
    resolved, note = _resolve_calendar_trade_date(
        requested_date,
        now=now,
        session=session,
    )
    if not validate_market:
        return resolved, note

    return _apply_market_validation(
        resolved,
        note,
        session=session,
        token=finmind_token,
    )


def resolve_lookback_dates(
    trade_date: str,
    lookback_days: int,
    *,
    session: requests.Session | None = None,
) -> list[str]:
    """Return the last N trading days up to and including trade_date."""
    end = dt.datetime.strptime(trade_date, "%Y-%m-%d").date()
    calendar_span = max(lookback_days * 4, 30)
    trading_dates = fetch_trading_dates(
        end,
        lookback_days=calendar_span,
        session=session,
    )
    eligible = [d for d in trading_dates if d <= trade_date]
    if not eligible:
        return [trade_date]
    return eligible[-lookback_days:]
