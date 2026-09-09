"""get_last_trading_date — wrap TWSE calendar resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.tools._paths import ensure_paths
from agent.tools._types import EXIT_FAILED, EXIT_OK, ToolResult

ensure_paths("tw-stock-report")

from twse_calendar import chip_reference_date, resolve_trade_date  # type: ignore[import-not-found]  # noqa: E402


@dataclass
class LastTradingDateInput:
    date: str | None = None
    validate_market: bool = True


@dataclass
class LastTradingDateResult(ToolResult):
    reference_date: str | None = None
    trade_date: str | None = None
    note: str | None = None


def get_last_trading_date(
    inp: LastTradingDateInput | None = None,
    *,
    date: str | None = None,
    validate_market: bool = True,
) -> LastTradingDateResult:
    params = inp or LastTradingDateInput(date=date, validate_market=validate_market)
    try:
        reference = params.date or chip_reference_date().isoformat()
        trade_date, note = resolve_trade_date(
            reference,
            finmind_token=os.environ.get("FINMIND_TOKEN", ""),
            validate_market=params.validate_market,
        )
    except Exception as exc:  # noqa: BLE001
        return LastTradingDateResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=str(exc),
        )
    return LastTradingDateResult(
        ok=True,
        exit_code=EXIT_OK,
        reference_date=reference,
        trade_date=trade_date,
        note=note,
    )
