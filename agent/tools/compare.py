"""compare_concept_peer — read facts already on disk and score relative stance.

Does not fetch, does not call agy, and is not registered on MCP.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.tools._paths import ROOT, ensure_paths
from agent.tools._types import EXIT_BAD_ARGS, EXIT_CSV_MISSING, EXIT_FAILED, EXIT_OK, ToolResult


@dataclass
class ComparePeerInput:
    stock_id: str
    peer_id: str
    trade_date: str | None = None


@dataclass
class ComparePeerResult(ToolResult):
    stock_id: str | None = None
    peer_id: str | None = None
    trade_date: str | None = None
    label: str | None = None
    base_weaker: bool = False
    reasons: list[str] | None = None
    highlights: list[str] | None = None
    themes: list[str] | None = None
    stock_name: str | None = None
    peer_name: str | None = None


def _facts_file(stock_id: str, trade_date: str | None):
    ensure_paths()
    from stock_paths import find_stock_artifact

    return find_stock_artifact(
        stock_id,
        f"tw_stock_{stock_id}.facts.json",
        trade_date=trade_date,
        root=ROOT,
    )


def _load_facts(stock_id: str, trade_date: str | None) -> tuple[dict[str, Any] | None, str | None]:
    path = _facts_file(stock_id, trade_date)
    if path is None:
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    resolved = path.parent.parent.name if path.parent.name == stock_id else path.parent.name
    return payload, resolved


def _peer_allowed(stock_id: str, peer_id: str) -> str | None:
    ensure_paths("portfolio-gate")
    from concept_compare import find_concept_peers

    peers = find_concept_peers(stock_id)
    if not peers.get("in_universe"):
        return f"{stock_id} 不在概念股比較池"
    allowed = {
        str(peer.get("stock_id"))
        for group in peers.get("groups") or []
        for peer in group.get("peers") or []
    }
    if peer_id not in allowed:
        return f"{peer_id} 不是 {stock_id} 的同主題概念股"
    return None


def compare_concept_peer(inp: ComparePeerInput) -> ComparePeerResult:
    stock_id = (inp.stock_id or "").strip()
    peer_id = (inp.peer_id or "").strip()
    if not stock_id or not peer_id or stock_id == peer_id:
        return ComparePeerResult(
            ok=False,
            exit_code=EXIT_BAD_ARGS,
            error="stock_id 與 peer_id 必須是不同代號",
            stock_id=stock_id or None,
            peer_id=peer_id or None,
        )
    blocked = _peer_allowed(stock_id, peer_id)
    if blocked:
        return ComparePeerResult(
            ok=False,
            exit_code=EXIT_BAD_ARGS,
            error=blocked,
            stock_id=stock_id,
            peer_id=peer_id,
        )

    base_facts, base_date = _load_facts(stock_id, inp.trade_date)
    peer_date_key = inp.trade_date or base_date
    peer_facts, peer_date = _load_facts(peer_id, peer_date_key)
    if base_facts is None or peer_facts is None:
        missing = stock_id if base_facts is None else peer_id
        return ComparePeerResult(
            ok=False,
            exit_code=EXIT_CSV_MISSING,
            error=f"{missing} 沒有同日 facts",
            stock_id=stock_id,
            peer_id=peer_id,
            trade_date=inp.trade_date,
        )

    ensure_paths("portfolio-gate")
    from concept_compare import build_comparison, shared_themes_for

    try:
        comparison = build_comparison(
            base_facts=base_facts,
            peer_facts=peer_facts,
            shared_themes=shared_themes_for(stock_id, peer_id),
        )
    except Exception as exc:
        return ComparePeerResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=str(exc),
            stock_id=stock_id,
            peer_id=peer_id,
        )
    stance = comparison.get("stance") or {}
    base_side = comparison.get("base") if isinstance(comparison.get("base"), dict) else {}
    peer_side = comparison.get("peer") if isinstance(comparison.get("peer"), dict) else {}
    themes = [
        str(item.get("label") or item.get("id") or "").strip()
        for item in (comparison.get("shared_themes") or [])
        if isinstance(item, dict)
    ]
    return ComparePeerResult(
        ok=True,
        exit_code=EXIT_OK,
        stock_id=stock_id,
        peer_id=peer_id,
        trade_date=base_date or peer_date or inp.trade_date,
        label=str(stance.get("label") or "unknown"),
        base_weaker=bool(stance.get("base_weaker")),
        reasons=list(stance.get("reasons") or []),
        highlights=list(comparison.get("highlights") or []),
        themes=[theme for theme in themes if theme],
        stock_name=str(base_side.get("stock_name") or "").strip() or None,
        peer_name=str(peer_side.get("stock_name") or "").strip() or None,
    )
