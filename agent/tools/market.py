"""run_market_daily / answer_market_chat — Research / chat tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agent.tools._paths import ensure_paths
from agent.tools._types import (
    EXIT_BAD_ARGS,
    EXIT_FAILED,
    EXIT_OK,
    ToolResult,
    message_for_exit,
)

ensure_paths("market-daily", "tw-stock-report")

from market_daily_gate import run_gate  # type: ignore[import-not-found]  # noqa: E402
from market_day_chat import answer_market_day_chat  # type: ignore[import-not-found]  # noqa: E402
from market_day_signals import resolve_window_or_fail  # type: ignore[import-not-found]  # noqa: E402


@dataclass
class MarketDailyInput:
    as_of: str | None = None
    trade_date: str | None = None
    max_rounds: int = 6
    skip_agy: bool = False
    skip_fetch: bool = False
    skip_us: bool = False
    validate_only: bool = False


@dataclass
class MarketDailyResult(ToolResult):
    trade_date: str | None = None
    for_session: str | None = None
    md_path: str | None = None


@dataclass
class MarketChatInput:
    message: str
    trade_date: str | None = None
    facts: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    markdown: str | None = None
    has_holdings: bool = False
    holdings: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    use_llm: bool = True
    skip_tavily: bool = False


@dataclass
class MarketChatResult(ToolResult):
    reply: str | None = None
    payload: dict[str, Any] | None = None


def _catch_exit(exc: BaseException) -> int | None:
    if isinstance(exc, SystemExit):
        code = exc.code
        return code if isinstance(code, int) else EXIT_FAILED
    return None


def run_market_daily(
    inp: MarketDailyInput | None = None,
    **overrides: Any,
) -> MarketDailyResult:
    params = inp or MarketDailyInput()
    if overrides:
        data = asdict(params)
        data.update(overrides)
        params = MarketDailyInput(**data)

    try:
        window = resolve_window_or_fail(as_of=params.as_of, trade_date=params.trade_date)
    except Exception as exc:  # noqa: BLE001
        return MarketDailyResult(ok=False, exit_code=EXIT_BAD_ARGS, error=str(exc))

    try:
        code = run_gate(
            window=window,
            max_rounds=max(1, params.max_rounds),
            skip_agy=params.skip_agy,
            skip_fetch=params.skip_fetch,
            skip_us=params.skip_us,
            validate_only=params.validate_only,
        )
    except BaseException as exc:
        exit_code = _catch_exit(exc)
        if exit_code is not None:
            return MarketDailyResult(
                ok=False,
                exit_code=exit_code,
                error=message_for_exit(exit_code),
                trade_date=window.trade_date,
                for_session=window.for_session,
            )
        return MarketDailyResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=str(exc),
            trade_date=window.trade_date,
            for_session=window.for_session,
        )

    from market_day_signals import market_output_dir

    md_path = market_output_dir(window.trade_date) / "tw_market_daily.md"
    return MarketDailyResult(
        ok=code == EXIT_OK,
        exit_code=code,
        error=None if code == EXIT_OK else message_for_exit(code),
        trade_date=window.trade_date,
        for_session=window.for_session,
        md_path=str(md_path) if md_path.exists() else None,
    )


def answer_market_chat(
    inp: MarketChatInput | None = None,
    **overrides: Any,
) -> MarketChatResult:
    if inp is None and "message" not in overrides:
        return MarketChatResult(ok=False, exit_code=EXIT_BAD_ARGS, error="message 不可為空")
    params = inp or MarketChatInput(message=str(overrides.get("message", "")))
    if overrides:
        data = asdict(params)
        data.update(overrides)
        params = MarketChatInput(**data)
    try:
        payload = answer_market_day_chat(
            message=params.message,
            facts=params.facts,
            summary=params.summary,
            markdown=params.markdown,
            trade_date=params.trade_date,
            has_holdings=params.has_holdings,
            holdings=params.holdings,
            history=params.history,
            use_llm=params.use_llm,
            skip_tavily=params.skip_tavily,
        )
    except Exception as exc:  # noqa: BLE001
        return MarketChatResult(ok=False, exit_code=EXIT_FAILED, error=str(exc))
    return MarketChatResult(
        ok=True,
        exit_code=EXIT_OK,
        reply=str(payload.get("reply") or ""),
        payload=payload,
    )
