"""Build summary JSON for market daily UI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SUMMARY_VERSION = 1


def summary_path(trade_date: str, root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[3]
    return base / "reports" / "market" / trade_date / "tw_market_daily.summary.json"


def _tone(value: float | None) -> str:
    if value is None:
        return "neutral"
    if value > 0.3:
        return "bullish"
    if value < -0.3:
        return "bearish"
    return "neutral"


def _extract_section(
    body: str,
    keywords: tuple[str, ...],
    *,
    max_chunks: int = 8,
    max_chars: int = 800,
) -> str:
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if any(kw in line for kw in keywords) and (
            line.strip().startswith("#")
            or re.match(r"^##?\s*[一二三四五]", line)
            or any(line.strip().startswith(p) for p in ("##", "#", "**"))
        ):
            start = i
            break
    if start is None:
        for i, line in enumerate(lines):
            if any(kw in line for kw in keywords):
                start = i
                break
    if start is None:
        return ""
    chunks: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        is_major = bool(re.match(r"^##?\s*[一二三四五]、", stripped))
        if is_major:
            break
        if stripped:
            chunks.append(stripped)
        if len(chunks) >= max_chunks:
            break
    # Preserve line breaks so UI can render Markdown lists / headings.
    return "\n".join(chunks)[:max_chars]


def _us_summary(facts: dict[str, Any]) -> dict[str, Any]:
    us = facts.get("us") or {}
    indices = us.get("indices") or {}
    ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else None
    sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else None
    alignment = us.get("alignment") or {}
    return {
        "available": bool(us.get("available")),
        "as_of": us.get("as_of") or facts.get("us_as_of"),
        "cutover_passed": us.get("cutover_passed", facts.get("us_cutover_passed")),
        "ixic_day_return_pct": (ixic or {}).get("day_return_pct"),
        "sox_day_return_pct": (sox or {}).get("day_return_pct"),
        "ixic_session_date": (ixic or {}).get("session_date"),
        "sox_session_date": (sox or {}).get("session_date"),
        "ixic_vs_taiex": alignment.get("ixic_vs_taiex"),
        "sox_vs_tsmc": alignment.get("sox_vs_tsmc"),
        "gaps": us.get("gaps") or {},
    }


def build_market_day_summary(
    facts: dict[str, Any],
    body: str,
) -> dict[str, Any]:
    market = facts.get("market") or {}
    day_ret = market.get("day_return_pct")
    ret_f = float(day_ret) if isinstance(day_ret, (int, float)) else None
    inst = facts.get("institutional") or {}
    return {
        "version": SUMMARY_VERSION,
        "kind": "market_daily",
        "trade_date": facts.get("trade_date"),
        "for_session": facts.get("for_session"),
        "bias_hint": facts.get("bias_hint"),
        "market": {
            "day_return_pct": day_ret,
            "tone": _tone(ret_f),
            "close": market.get("close"),
            "close_in_day_range_pct": market.get("close_in_day_range_pct"),
            "headline": _extract_section(body, ("今日", "結構")),
        },
        "volume": facts.get("volume") or {},
        "institutional": {
            "available": inst.get("available"),
            "consensus": inst.get("consensus"),
            "consensus_label": inst.get("consensus_label"),
            "foreign_net": inst.get("foreign_net"),
            "trust_net": inst.get("trust_net"),
            "dealer_net": inst.get("dealer_net"),
            "total_net": inst.get("total_net"),
            "unit": inst.get("unit") or "twd",
        },
        "technical": facts.get("technical") or {},
        "tsmc": facts.get("tsmc") or {},
        "us": _us_summary(facts),
        "bias": _extract_section(body, ("偏誤", "開盤"), max_chunks=20, max_chars=1600),
        "dashboard": _extract_section(body, ("儀表", "觀察"), max_chunks=12, max_chars=900),
        "external": _extract_section(body, ("外盤", "美股")),
        "anchors": facts.get("anchors") or [],
    }


def write_summary_json(
    facts: dict[str, Any],
    body: str,
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_market_day_summary(facts, body)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
