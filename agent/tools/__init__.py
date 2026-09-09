"""Phase 0 typed tools. CLI, FastAPI, and the Phase 1 MCP server call these."""

from __future__ import annotations

from typing import Any

__all__ = [
    "BuildChipFactsInput",
    "BuildChipFactsResult",
    "DigestItem",
    "DraftDigestInput",
    "DraftDigestResult",
    "FetchChipsInput",
    "FetchChipsResult",
    "HoldingsInput",
    "HoldingsResult",
    "LastTradingDateInput",
    "LastTradingDateResult",
    "MarketChatInput",
    "MarketChatResult",
    "MarketDailyInput",
    "MarketDailyResult",
    "PositionGateInput",
    "PositionGateResult",
    "ReportGateInput",
    "ReportGateResult",
    "SendDigestInput",
    "SendDigestResult",
    "answer_market_chat",
    "build_chip_facts",
    "draft_digest",
    "fetch_chips",
    "get_holdings",
    "get_last_trading_date",
    "run_market_daily",
    "run_position_gate",
    "run_report_gate",
    "send_digest",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "BuildChipFactsInput": ("agent.tools.chips", "BuildChipFactsInput"),
    "BuildChipFactsResult": ("agent.tools.chips", "BuildChipFactsResult"),
    "FetchChipsInput": ("agent.tools.chips", "FetchChipsInput"),
    "FetchChipsResult": ("agent.tools.chips", "FetchChipsResult"),
    "build_chip_facts": ("agent.tools.chips", "build_chip_facts"),
    "fetch_chips": ("agent.tools.chips", "fetch_chips"),
    "LastTradingDateInput": ("agent.tools.dates", "LastTradingDateInput"),
    "LastTradingDateResult": ("agent.tools.dates", "LastTradingDateResult"),
    "get_last_trading_date": ("agent.tools.dates", "get_last_trading_date"),
    "DigestItem": ("agent.tools.digest", "DigestItem"),
    "DraftDigestInput": ("agent.tools.digest", "DraftDigestInput"),
    "DraftDigestResult": ("agent.tools.digest", "DraftDigestResult"),
    "SendDigestInput": ("agent.tools.digest", "SendDigestInput"),
    "SendDigestResult": ("agent.tools.digest", "SendDigestResult"),
    "draft_digest": ("agent.tools.digest", "draft_digest"),
    "send_digest": ("agent.tools.digest", "send_digest"),
    "MarketChatInput": ("agent.tools.market", "MarketChatInput"),
    "MarketChatResult": ("agent.tools.market", "MarketChatResult"),
    "MarketDailyInput": ("agent.tools.market", "MarketDailyInput"),
    "MarketDailyResult": ("agent.tools.market", "MarketDailyResult"),
    "answer_market_chat": ("agent.tools.market", "answer_market_chat"),
    "run_market_daily": ("agent.tools.market", "run_market_daily"),
    "HoldingsInput": ("agent.tools.position", "HoldingsInput"),
    "HoldingsResult": ("agent.tools.position", "HoldingsResult"),
    "PositionGateInput": ("agent.tools.position", "PositionGateInput"),
    "PositionGateResult": ("agent.tools.position", "PositionGateResult"),
    "get_holdings": ("agent.tools.position", "get_holdings"),
    "run_position_gate": ("agent.tools.position", "run_position_gate"),
    "ReportGateInput": ("agent.tools.report", "ReportGateInput"),
    "ReportGateResult": ("agent.tools.report", "ReportGateResult"),
    "run_report_gate": ("agent.tools.report", "run_report_gate"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module

    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value
