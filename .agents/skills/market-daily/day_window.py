"""Resolve the market-daily report window (Taipei trading-day 15:00 cutover)."""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8))
# FinMind TaiwanStockTotalInstitutionalInvestors updates ~15:00 Taipei.
CUTOVER_HOUR = 15
CUTOVER_MINUTE = 0
# US regular close ~04:00 Taipei; wait until 05:30 before treating overnight as ready.
US_CUTOVER_HOUR = 5
US_CUTOVER_MINUTE = 30


def _ensure_twse_path() -> None:
    skill = Path(__file__).resolve().parent.parent / "tw-stock-report"
    text = str(skill)
    if text not in sys.path:
        sys.path.insert(0, text)


@dataclass(frozen=True)
class DayWindow:
    """Single trade_date facts window serving the next open (for_session)."""

    trade_date: str
    for_session: str
    prior_trade_date: str | None
    lookback_days: list[str]
    resolved_as_of: str
    cutover_applied: bool
    us_as_of: str
    us_cutover_passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "for_session": self.for_session,
            "prior_trade_date": self.prior_trade_date,
            "lookback_days": list(self.lookback_days),
            "resolved_as_of": self.resolved_as_of,
            "cutover_applied": self.cutover_applied,
            "us_as_of": self.us_as_of,
            "us_cutover_passed": self.us_cutover_passed,
        }


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI_TZ)


def _ensure_tz(now: dt.datetime) -> dt.datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=TAIPEI_TZ)
    return now.astimezone(TAIPEI_TZ)


def next_trading_day_after(
    day: dt.date,
    *,
    session: Any = None,
    max_horizon: int = 14,
) -> dt.date:
    _ensure_twse_path()
    from twse_calendar import is_trading_day

    cursor = day + dt.timedelta(days=1)
    for _ in range(max_horizon):
        if is_trading_day(cursor, session=session):
            return cursor
        cursor += dt.timedelta(days=1)
    raise ValueError(f"找不到 {day} 之後的交易日")


def resolve_trade_date(
    now: dt.datetime,
    *,
    session: Any = None,
) -> tuple[dt.date, bool]:
    """Return (trade_date, cutover_applied).

    On a TWSE trading day at/after 15:00 → that day.
    Otherwise → latest trading day strictly before today.
    """
    _ensure_twse_path()
    from twse_calendar import is_trading_day, previous_trading_day

    current = _ensure_tz(now)
    today = current.date()
    minutes = current.hour * 60 + current.minute
    cutover_minutes = CUTOVER_HOUR * 60 + CUTOVER_MINUTE

    if is_trading_day(today, session=session) and minutes >= cutover_minutes:
        return today, True

    prior = previous_trading_day(today.isoformat(), session=session)
    if prior is None:
        raise ValueError(f"找不到 {today} 之前的交易日")
    return dt.date.fromisoformat(prior), True


def resolve_us_as_of(now: dt.datetime) -> tuple[dt.date, bool]:
    """Return (us_as_of calendar date, cutover_passed).

    Taipei 05:30 = overnight US close considered settled.
    - at/after 05:30 → as_of = today（用最新已完成美股 session ≤ today）
    - before 05:30 → as_of = yesterday（仍用前一日美股）
    """
    current = _ensure_tz(now)
    today = current.date()
    minutes = current.hour * 60 + current.minute
    cutover_minutes = US_CUTOVER_HOUR * 60 + US_CUTOVER_MINUTE
    if minutes >= cutover_minutes:
        return today, True
    return today - dt.timedelta(days=1), False


def lookback_trading_days(
    trade_date: dt.date,
    *,
    count: int = 21,
    session: Any = None,
) -> list[str]:
    """Inclusive lookback ending at trade_date (oldest → newest)."""
    _ensure_twse_path()
    from twse_calendar import fetch_trading_dates

    days = fetch_trading_dates(
        trade_date,
        lookback_days=max(60, count * 3),
        session=session,
    )
    if not days or days[-1] != trade_date.isoformat():
        # trade_date might not be in calendar probe; keep ending at last <= trade_date
        days = [d for d in days if d <= trade_date.isoformat()]
    if len(days) < 2:
        raise ValueError(f"交易日 {trade_date} 附近 lookback 不足")
    return days[-count:]


def resolve_day_window(
    now: dt.datetime | None = None,
    *,
    trade_date: str | None = None,
    session: Any = None,
) -> DayWindow:
    """Resolve facts trade_date and the next open it serves."""
    current = _ensure_tz(now or taipei_now())
    cutover_applied = True

    if trade_date:
        day = dt.date.fromisoformat(trade_date)
        cutover_applied = False
    else:
        day, cutover_applied = resolve_trade_date(current, session=session)

    for_session = next_trading_day_after(day, session=session)
    lookback = lookback_trading_days(day, count=21, session=session)
    prior = lookback[-2] if len(lookback) >= 2 else None
    us_as_of, us_cutover_passed = resolve_us_as_of(current)

    return DayWindow(
        trade_date=day.isoformat(),
        for_session=for_session.isoformat(),
        prior_trade_date=prior,
        lookback_days=lookback,
        resolved_as_of=current.isoformat(),
        cutover_applied=cutover_applied,
        us_as_of=us_as_of.isoformat(),
        us_cutover_passed=us_cutover_passed,
    )
