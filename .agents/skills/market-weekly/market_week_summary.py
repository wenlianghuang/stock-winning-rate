"""Build summary JSON for market weekly UI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SUMMARY_VERSION = 4


def summary_path(week_end: str, root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[3]
    return base / "reports" / "market" / week_end / "tw_market_weekly.summary.json"


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
    max_chunks: int = 6,
    max_chars: int = 500,
    stop_at_subheads: bool = True,
) -> str:
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if any(kw in line for kw in keywords) and (
            line.strip().startswith("#") or re.match(r"^#{1,3}\s", line) or "、" in line[:4]
            or re.match(r"^##?\s*[一二三四五六七]", line)
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
        is_major = bool(
            re.match(r"^##?\s*[一二三四五六七]、", stripped)
            or (
                re.match(r"^##\s+", stripped)
                and not stripped.startswith("###")
                and any(
                    kw in stripped
                    for kw in ("大盤", "權值", "類股", "交叉", "下週", "觀察", "免責")
                )
            )
        )
        is_any_heading = bool(re.match(r"^#{1,3}\s", line))
        if is_major or (stop_at_subheads and is_any_heading):
            break
        if line.strip():
            chunks.append(line.strip())
        if len(chunks) >= max_chunks:
            break
    # Preserve line breaks so UI can render Markdown lists / headings.
    return "\n".join(chunks)[:max_chars]


def _extract_scenarios_full(body: str) -> str:
    """Keep fuller next-week block for UI (primary reader value)."""
    return _extract_section(
        body,
        ("下週", "情境"),
        max_chunks=80,
        max_chars=3500,
        stop_at_subheads=False,
    )


def _us_summary(facts: dict[str, Any]) -> dict[str, Any]:
    us = facts.get("us") or {}
    indices = us.get("indices") or {}
    ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else None
    sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else None
    alignment = us.get("alignment") or {}
    return {
        "available": bool(us.get("available")),
        "ixic_week_return_pct": (ixic or {}).get("week_return_pct"),
        "sox_week_return_pct": (sox or {}).get("week_return_pct"),
        "ixic_vs_taiex": alignment.get("ixic_vs_taiex"),
        "sox_vs_tw_semi": alignment.get("sox_vs_tw_semi"),
        "gaps": us.get("gaps") or {},
        "tw_semi_week_return_pct": us.get("tw_semi_week_return_pct"),
    }


def build_market_week_summary(
    facts: dict[str, Any],
    body: str,
) -> dict[str, Any]:
    market = facts.get("market") or {}
    week_ret = market.get("week_return_pct")
    ret_f = float(week_ret) if isinstance(week_ret, (int, float)) else None
    return {
        "version": SUMMARY_VERSION,
        "kind": "market_weekly",
        "week_start": facts.get("week_start"),
        "week_end": facts.get("week_end"),
        "trading_days": facts.get("trading_days") or [],
        "market": {
            "week_return_pct": week_ret,
            "tone": _tone(ret_f),
            "last_close": market.get("last_close"),
            "close_in_week_range_pct": market.get("close_in_week_range_pct"),
            "headline": _extract_section(body, ("大盤",)),
        },
        "leaders": {
            "top": facts.get("leaders", {}).get("top") or [],
            "bottom": facts.get("leaders", {}).get("bottom") or [],
        },
        "sectors": {
            "universe": (facts.get("sectors") or {}).get("universe"),
            "strong": (facts.get("sectors") or {}).get("strong") or [],
            "weak": (facts.get("sectors") or {}).get("weak") or [],
        },
        "us": _us_summary(facts),
        "scenarios": _extract_scenarios_full(body),
        "cross": _extract_section(body, ("交叉", "對帳")),
        "watch": _extract_section(body, ("觀察",)),
        "anchors": facts.get("anchors") or [],
        "news_titles": list(facts.get("news_titles") or []),
        "news_items": [
            {
                "title": item.get("title"),
                "stock_id": item.get("stock_id"),
                "name": item.get("name"),
                "related_sector": item.get("related_sector"),
                "date": item.get("date"),
            }
            for item in (facts.get("news_items") or [])[:12]
            if isinstance(item, dict) and item.get("title")
        ],
    }


def write_summary_json(
    facts: dict[str, Any],
    body: str,
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_market_week_summary(facts, body)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
