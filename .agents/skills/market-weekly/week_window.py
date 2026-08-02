"""Resolve the market-weekly report window (Taipei Friday 17:30 cutover)."""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8))
CUTOVER_WEEKDAY = 4  # Friday
CUTOVER_HOUR = 17
CUTOVER_MINUTE = 30


def _ensure_twse_path() -> None:
    skill = Path(__file__).resolve().parent.parent / "tw-stock-report"
    text = str(skill)
    if text not in sys.path:
        sys.path.insert(0, text)


@dataclass(frozen=True)
class WeekWindow:
    """Calendar Mon–Fri week resolved for the market weekly report."""

    week_monday: dt.date
    week_friday: dt.date
    trading_days: list[str]
    week_start: str  # first trading day ISO
    week_end: str  # last trading day ISO
    resolved_as_of: str  # ISO datetime with offset
    cutover_applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "week_monday": self.week_monday.isoformat(),
            "week_friday": self.week_friday.isoformat(),
            "trading_days": list(self.trading_days),
            "week_start": self.week_start,
            "week_end": self.week_end,
            "resolved_as_of": self.resolved_as_of,
            "cutover_applied": self.cutover_applied,
        }


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI_TZ)


def _ensure_tz(now: dt.datetime) -> dt.datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=TAIPEI_TZ)
    return now.astimezone(TAIPEI_TZ)


def this_friday(day: dt.date) -> dt.date:
    """Return the Friday of the calendar week containing ``day`` (Mon–Sun)."""
    # Monday=0 … Sunday=6
    monday = day - dt.timedelta(days=day.weekday())
    return monday + dt.timedelta(days=4)


def friday_cutover(friday: dt.date) -> dt.datetime:
    return dt.datetime(
        friday.year,
        friday.month,
        friday.day,
        CUTOVER_HOUR,
        CUTOVER_MINUTE,
        tzinfo=TAIPEI_TZ,
    )


def target_week_friday(now: dt.datetime) -> tuple[dt.date, bool]:
    """Return (week_friday, cutover_applied).

    Before this week's Friday 17:30 → previous calendar week's Friday.
    From Friday 17:30 onward → this calendar week's Friday.
    """
    current = _ensure_tz(now)
    friday = this_friday(current.date())
    cutoff = friday_cutover(friday)
    if current < cutoff:
        return friday - dt.timedelta(days=7), True
    return friday, True


def trading_days_in_range(
    start: dt.date,
    end: dt.date,
    *,
    session: Any = None,
) -> list[str]:
    _ensure_twse_path()
    from twse_calendar import is_trading_day

    days: list[str] = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor, session=session):
            days.append(cursor.isoformat())
        cursor += dt.timedelta(days=1)
    return days


def resolve_week_window(
    now: dt.datetime | None = None,
    *,
    week_end: str | None = None,
    session: Any = None,
) -> WeekWindow:
    """Resolve the report week.

    If ``week_end`` is given (YYYY-MM-DD), skip cutover and use that date's
    calendar week (Mon–Fri containing week_end, clamped to trading days).
    """
    current = _ensure_tz(now or taipei_now())

    if week_end:
        end_day = dt.date.fromisoformat(week_end)
        friday = this_friday(end_day)
        monday = friday - dt.timedelta(days=4)
        days = trading_days_in_range(monday, friday, session=session)
        if not days:
            raise ValueError(f"指定週（{monday}～{friday}）沒有交易日")
        # Prefer the explicit week_end if it is a trading day in range
        if week_end in days:
            resolved_end = week_end
        else:
            resolved_end = days[-1]
        return WeekWindow(
            week_monday=monday,
            week_friday=friday,
            trading_days=days,
            week_start=days[0],
            week_end=resolved_end,
            resolved_as_of=current.isoformat(),
            cutover_applied=False,
        )

    friday, cutover_applied = target_week_friday(current)
    monday = friday - dt.timedelta(days=4)
    days = trading_days_in_range(monday, friday, session=session)
    if not days:
        raise ValueError(f"目標週（{monday}～{friday}）沒有交易日")
    return WeekWindow(
        week_monday=monday,
        week_friday=friday,
        trading_days=days,
        week_start=days[0],
        week_end=days[-1],
        resolved_as_of=current.isoformat(),
        cutover_applied=cutover_applied,
    )
