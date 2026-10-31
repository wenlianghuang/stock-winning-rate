"""Resolve the close-report window (Taipei trading-day 17:30 cutover)."""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8))
# Index close and market-wide 三大法人 are in well before this.
# 17:30 leaves room for FinMind's price row and Yahoo 權值法人.
CUTOVER_HOUR = 17
CUTOVER_MINUTE = 30
CUTOVER_LABEL = "17:30"


def _ensure_paths() -> None:
    root = Path(__file__).resolve().parents[3]
    for sub in (
        ".agents/skills/market-daily",
        ".agents/skills/tw-stock-report",
    ):
        text = str(root / sub)
        if text not in sys.path:
            sys.path.insert(0, text)


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI_TZ)


def _ensure_tz(now: dt.datetime) -> dt.datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=TAIPEI_TZ)
    return now.astimezone(TAIPEI_TZ)


@dataclass(frozen=True)
class CloseWindow:
    """The session whose close this report describes."""

    trade_date: str
    prior_trade_date: str | None
    lookback_days: list[str]
    resolved_as_of: str
    cutover_applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "prior_trade_date": self.prior_trade_date,
            "lookback_days": list(self.lookback_days),
            "resolved_as_of": self.resolved_as_of,
            "cutover_applied": self.cutover_applied,
            "cutover": CUTOVER_LABEL,
        }


def resolve_close_trade_date(
    now: dt.datetime,
    *,
    session: Any = None,
) -> tuple[dt.date, bool]:
    """On a trading day at/after 17:30 → that day. Otherwise the prior trading day."""
    _ensure_paths()
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


def resolve_close_window(
    now: dt.datetime | None = None,
    *,
    trade_date: str | None = None,
    session: Any = None,
) -> CloseWindow:
    _ensure_paths()
    from day_window import lookback_trading_days

    current = _ensure_tz(now or taipei_now())
    cutover_applied = True
    if trade_date:
        day = dt.date.fromisoformat(trade_date)
        cutover_applied = False
    else:
        day, cutover_applied = resolve_close_trade_date(current, session=session)

    lookback = lookback_trading_days(day, count=21, session=session)
    prior = lookback[-2] if len(lookback) >= 2 else None
    return CloseWindow(
        trade_date=day.isoformat(),
        prior_trade_date=prior,
        lookback_days=lookback,
        resolved_as_of=current.isoformat(),
        cutover_applied=cutover_applied,
    )


def parse_as_of(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if len(text) == 10:
        day = dt.date.fromisoformat(text)
        return dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=TAIPEI_TZ)
    parsed = dt.datetime.fromisoformat(text)
    return _ensure_tz(parsed)
