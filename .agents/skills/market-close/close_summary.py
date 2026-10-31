"""Summary JSON for the close-report UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUMMARY_NAME = "tw_market_close.summary.json"


def summary_path(trade_date: str, root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[3]
    return base / "reports" / "market" / trade_date / SUMMARY_NAME


def build_close_summary(facts: dict[str, Any], body: str) -> dict[str, Any]:
    return {
        "version": 1,
        "kind": "tw_market_close",
        "trade_date": facts.get("trade_date"),
        "market": facts.get("market") or {},
        "volume": facts.get("volume") or {},
        "institutional": facts.get("institutional") or {},
        "leaders": facts.get("leaders") or [],
        "news_titles": list(facts.get("news_titles") or []),
        "news_items": facts.get("news_items") or [],
        "body_chars": len((body or "").strip()),
    }


def write_summary_json(facts: dict[str, Any], body: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_close_summary(facts, body)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
