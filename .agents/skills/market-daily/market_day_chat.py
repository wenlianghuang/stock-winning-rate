#!/usr/bin/env python3
"""Grounded Q&A over a finished market-daily brief (Phase 2)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[3]
MARKET_ROOT = ROOT / "reports" / "market"
US_TECH_ROOT = ROOT / "reports" / "us-tech"
QUOTA_ROOT = MARKET_ROOT / "_chat_quota"

Intent = Literal[
    "entry_advice",
    "position_advice",
    "factual_qa",
    "external_news",
    "out_of_scope",
]

FactualSlot = Literal[
    "foreign",
    "bias",
    "volume",
    "tsmc",
    "us",
    "technical",
]

ChatHistoryItem = dict[str, str]

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_TAVILY_DAILY_LIMIT = 5
MAX_MARKDOWN_CHARS = 3500
MAX_HISTORY_TURNS = 6
MAX_REPLY_CHARS = 2000
MAX_NEWS_BULLETS = 8
MAX_HOLDINGS = 20

ENTRY_PATTERNS = (
    r"進場",
    r"該不該買",
    r"要不要買",
    r"能不能買",
    r"可以買嗎",
    r"明天買",
    r"開倉",
    r"建倉",
    r"空手",
    r"是否買進",
    r"建議買",
    r"要不要進",
    r"該進嗎",
    r"做多嗎",
    r"加碼嗎",
)

POSITION_PATTERNS = (
    r"我的部位",
    r"持股怎麼",
    r"部位怎麼",
    r"停損",
    r"減碼",
    r"加碼我的",
    r"均價",
    r"張數",
    r"持有怎麼辦",
)

EXTERNAL_NEWS_PATTERNS = (
    r"新聞",
    r"剛出",
    r"突发",
    r"突發",
    r"盤後消息",
    r"今晚.+說",
    r"推特",
    r"twitter",
    r"x\.com",
    r"tavily",
    r"搜尋",
    r"google",
    r"外訊",
    r"頭條",
)

FACTUAL_SLOT_PATTERNS: tuple[tuple[FactualSlot, tuple[str, ...]], ...] = (
    (
        "foreign",
        (r"外資", r"投信", r"自營", r"三大法人", r"法人", r"foreign"),
    ),
    (
        "bias",
        (r"偏誤", r"偏多", r"偏空", r"開盤偏", r"明日開盤", r"明天開盤"),
    ),
    (
        "volume",
        (r"量能", r"成交量", r"均量", r"爆量", r"縮量"),
    ),
    (
        "tsmc",
        (r"台積電", r"2330", r"tsmc"),
    ),
    (
        "us",
        (r"那指", r"費半", r"那斯達克", r"nasdaq", r"sox", r"美股", r"外盤"),
    ),
    (
        "technical",
        (r"\bma5\b", r"\bma20\b", r"均線", r"技術", r"支撐", r"壓力"),
    ),
)

DISCLAIMER = "僅供參考，不構成投資建議。"

SYSTEM_POLICY = """你是台股開盤前戰術 brief 的助手。
規則（必須遵守）：
1. 只能使用使用者訊息中的 BRIEF CONTEXT／NEWS CONTEXT；不可發明數字、點位或標的。
2. 數字方向（漲跌、買超／賣超、量能）必須與 facts 一致；沒有的資訊就明說「本份 brief 沒有」。
3. 禁止下單指令與保證獲利：不可寫建議買進／賣出／進場／開倉／明天買／穩賺／一定漲。
4. 本產品回答的是「開盤偏誤與觀察條件」，不是二元進出場決策。
5. 用繁體中文、精簡回答（約 120～350 字）。
6. 結尾加一句：僅供參考，不構成投資建議。
""".strip()


def load_artifacts(trade_date: str) -> dict[str, Any]:
    out_dir = MARKET_ROOT / trade_date
    if not out_dir.is_dir():
        raise FileNotFoundError(f"找不到 reports/market/{trade_date}/")
    facts_file = out_dir / "tw_market_daily.facts.json"
    summary_file = out_dir / "tw_market_daily.summary.json"
    md_file = out_dir / "tw_market_daily.md"
    facts = (
        json.loads(facts_file.read_text(encoding="utf-8"))
        if facts_file.exists()
        else None
    )
    summary = (
        json.loads(summary_file.read_text(encoding="utf-8"))
        if summary_file.exists()
        else None
    )
    markdown = md_file.read_text(encoding="utf-8") if md_file.exists() else None
    return {"facts": facts, "summary": summary, "markdown": markdown}


def _compact_facts(facts: dict[str, Any] | None) -> dict[str, Any]:
    if not facts:
        return {}
    market = facts.get("market") or {}
    volume = facts.get("volume") or {}
    inst = facts.get("institutional") or {}
    technical = facts.get("technical") or {}
    tsmc = facts.get("tsmc") or {}
    us = facts.get("us") or {}
    indices = us.get("indices") or {}
    ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else {}
    sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else {}
    alignment = us.get("alignment") or {}
    return {
        "trade_date": facts.get("trade_date"),
        "for_session": facts.get("for_session"),
        "bias_hint": facts.get("bias_hint"),
        "market": {
            "close": market.get("close"),
            "day_return_pct": market.get("day_return_pct"),
            "close_in_day_range_pct": market.get("close_in_day_range_pct"),
        },
        "volume": {
            "vs_avg5_ratio": volume.get("vs_avg5_ratio"),
            "regime": volume.get("regime"),
        },
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
        "technical": {
            "vs_ma5": technical.get("vs_ma5"),
            "vs_ma20": technical.get("vs_ma20"),
            "ma5": technical.get("ma5"),
            "ma20": technical.get("ma20"),
        },
        "tsmc": {
            "close": tsmc.get("close"),
            "day_return_pct": tsmc.get("day_return_pct"),
        },
        "us": {
            "available": us.get("available"),
            "as_of": us.get("as_of"),
            "ixic_day_return_pct": ixic.get("day_return_pct"),
            "sox_day_return_pct": sox.get("day_return_pct"),
            "ixic_vs_taiex": alignment.get("ixic_vs_taiex"),
            "sox_vs_tsmc": alignment.get("sox_vs_tsmc"),
        },
        "anchors": (facts.get("anchors") or [])[:12],
    }


def build_chat_context(
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    markdown: str | None,
    *,
    max_markdown_chars: int = MAX_MARKDOWN_CHARS,
    extra_blocks: list[str] | None = None,
) -> str:
    """Compact read-only context for the local LLM."""
    summary = summary or {}
    compact = _compact_facts(facts)
    bias_text = (summary.get("bias") or "").strip()
    dashboard_text = (summary.get("dashboard") or "").strip()
    if summary.get("bias_hint") and not compact.get("bias_hint"):
        compact["bias_hint"] = summary.get("bias_hint")
    md = (markdown or "").strip()
    if len(md) > max_markdown_chars:
        md = md[: max_markdown_chars - 20] + "\n…(截斷)"

    parts = [
        "=== FACTS (compact JSON) ===",
        json.dumps(compact, ensure_ascii=False, indent=2),
    ]
    if bias_text:
        parts.extend(["=== SUMMARY bias ===", bias_text])
    if dashboard_text:
        parts.extend(["=== SUMMARY dashboard ===", dashboard_text])
    if md:
        parts.extend(["=== MARKDOWN (may be truncated) ===", md])
    for block in extra_blocks or []:
        if block.strip():
            parts.append(block.strip())
    return "\n".join(parts)


def classify_intent(message: str) -> Intent:
    text = (message or "").strip().lower()
    if not text:
        return "factual_qa"
    for pat in EXTERNAL_NEWS_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return "external_news"
    for pat in ENTRY_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return "entry_advice"
    for pat in POSITION_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return "position_advice"
    return "factual_qa"


def match_factual_slot(message: str) -> FactualSlot | None:
    text = (message or "").strip().lower()
    if not text:
        return None
    for slot, patterns in FACTUAL_SLOT_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, flags=re.IGNORECASE):
                return slot
    return None


def _bias_label(hint: str | None) -> str:
    if hint == "bullish":
        return "偏多"
    if hint == "bearish":
        return "偏空"
    return "中性"


def _extract_bias_hint(
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None,
) -> str | None:
    if summary and summary.get("bias_hint"):
        return str(summary["bias_hint"])
    if facts and facts.get("bias_hint"):
        return str(facts["bias_hint"])
    return None


def _fmt_pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f}%"


def _fmt_close(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _fmt_net_amount(value: Any, unit: str | None = "twd") -> str:
    """Format market-wide institutional net. Unit is TWD (元); display as 億元."""
    if not isinstance(value, (int, float)):
        return "—"
    # Legacy facts may still say "shares"; the FinMind total-market series is TWD.
    if unit in (None, "", "twd", "amount", "shares"):
        yi = float(value) / 1e8
        prefix = "+" if yi > 0 else ""
        return f"{prefix}{yi:,.2f} 億元"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _net_direction(value: Any) -> str:
    if not isinstance(value, (int, float)) or value == 0:
        return "中性"
    return "買超" if value > 0 else "賣超"


def build_factual_slot_reply(
    slot: FactualSlot,
    *,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None,
) -> str:
    facts = facts or {}
    summary = summary or {}
    trade_date = facts.get("trade_date") or summary.get("trade_date") or "—"
    lines: list[str] = []

    if slot == "foreign":
        inst = facts.get("institutional") or summary.get("institutional") or {}
        if not inst.get("available") and inst.get("foreign_net") is None:
            lines.append(f"本份 brief（{trade_date}）沒有三大法人／外資資料。")
        else:
            unit = inst.get("unit") or "twd"
            lines.extend(
                [
                    f"依 {trade_date} brief 法人籌碼：",
                    f"- 共識：{inst.get('consensus_label') or inst.get('consensus') or '—'}",
                    f"- 外資淨額：{_fmt_net_amount(inst.get('foreign_net'), unit)}"
                    f"（{_net_direction(inst.get('foreign_net'))}）",
                    f"- 投信淨額：{_fmt_net_amount(inst.get('trust_net'), unit)}"
                    f"（{_net_direction(inst.get('trust_net'))}）",
                    f"- 自營淨額：{_fmt_net_amount(inst.get('dealer_net'), unit)}"
                    f"（{_net_direction(inst.get('dealer_net'))}）",
                ]
            )
            if inst.get("total_net") is not None:
                lines.append(
                    f"- 三大法人合計：{_fmt_net_amount(inst.get('total_net'), unit)}"
                )

    elif slot == "bias":
        hint = _extract_bias_hint(facts, summary)
        label = _bias_label(hint)
        for_session = facts.get("for_session") or summary.get("for_session") or "—"
        lines.append(
            f"本份 brief（{trade_date} → {for_session}）開盤偏誤提示為**{label}**"
            f"（bias_hint={hint or 'neutral'}）。"
        )
        bias_text = (summary.get("bias") or "").strip()
        dashboard = (summary.get("dashboard") or "").strip()
        if bias_text:
            lines.extend(["", "【偏誤摘要】", bias_text[:900]])
        if dashboard:
            lines.extend(["", "【開盤儀表板】", dashboard[:700]])
        if not bias_text and not dashboard:
            lines.append("（summary 未抽出偏誤敘事；請參考 facts.bias_hint。）")

    elif slot == "volume":
        volume = facts.get("volume") or summary.get("volume") or {}
        market = facts.get("market") or summary.get("market") or {}
        lines.extend(
            [
                f"依 {trade_date} brief 量價：",
                f"- 大盤日報酬：{_fmt_pct(market.get('day_return_pct'))}",
                f"- 收盤：{_fmt_close(market.get('close'))}",
                f"- 量能 regime：{volume.get('regime') or '—'}",
                f"- 相對五日均量：{volume.get('vs_avg5_ratio') if volume.get('vs_avg5_ratio') is not None else '—'}x",
            ]
        )

    elif slot == "tsmc":
        tsmc = facts.get("tsmc") or summary.get("tsmc") or {}
        if not tsmc.get("available") and tsmc.get("close") is None:
            lines.append(f"本份 brief（{trade_date}）沒有台積電資料。")
        else:
            lines.extend(
                [
                    f"依 {trade_date} brief 台積電（2330）：",
                    f"- 收盤：{_fmt_close(tsmc.get('close'))}",
                    f"- 日報酬：{_fmt_pct(tsmc.get('day_return_pct'))}",
                ]
            )

    elif slot == "us":
        us = facts.get("us") or summary.get("us") or {}
        if not us.get("available"):
            lines.append(f"本份 brief（{trade_date}）美股指數資料不足。")
        else:
            indices = us.get("indices") or {}
            ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else {}
            sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else {}
            alignment = us.get("alignment") or {}
            ixic_ret = us.get("ixic_day_return_pct")
            if ixic_ret is None:
                ixic_ret = ixic.get("day_return_pct")
            sox_ret = us.get("sox_day_return_pct")
            if sox_ret is None:
                sox_ret = sox.get("day_return_pct")
            ixic_align = us.get("ixic_vs_taiex") or alignment.get("ixic_vs_taiex")
            sox_align = us.get("sox_vs_tsmc") or alignment.get("sox_vs_tsmc")
            lines.extend(
                [
                    f"依 {trade_date} brief 美股對帳（as_of {us.get('as_of') or '—'}）：",
                    f"- 那指日報酬：{_fmt_pct(ixic_ret)}（vs 台股：{ixic_align or '—'}）",
                    f"- 費半日報酬：{_fmt_pct(sox_ret)}（vs 台積電：{sox_align or '—'}）",
                ]
            )

    elif slot == "technical":
        technical = facts.get("technical") or summary.get("technical") or {}
        lines.extend(
            [
                f"依 {trade_date} brief 技術錨點：",
                f"- vs MA5：{technical.get('vs_ma5') or '—'}（MA5={_fmt_close(technical.get('ma5'))}）",
                f"- vs MA20：{technical.get('vs_ma20') or '—'}（MA20={_fmt_close(technical.get('ma20'))}）",
            ]
        )

    lines.extend(["", DISCLAIMER])
    return "\n".join(lines).strip()


def build_entry_template_reply(
    *,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    has_holdings: bool,
) -> str:
    """Deterministic reply for entry / buy questions (no binary decision)."""
    hint = _extract_bias_hint(facts, summary)
    label = _bias_label(hint)
    bias_text = ((summary or {}).get("bias") or "").strip()
    dashboard = ((summary or {}).get("dashboard") or "").strip()
    trade_date = (facts or summary or {}).get("trade_date") or "—"
    for_session = (facts or summary or {}).get("for_session") or "—"

    lines = [
        f"我無法替你決定「要不要進場／買進」。本份 brief（{trade_date} → {for_session}）"
        f"只描述開盤偏誤環境：目前結構提示為**{label}**（bias_hint={hint or 'neutral'}）。",
        "",
        "請改用可驗證條件來準備開盤，而不是二元下單決策：",
    ]
    if bias_text:
        lines.extend(["", "【基準／尾部情境摘要】", bias_text[:900]])
    if dashboard:
        lines.extend(["", "【開盤儀表板】", dashboard[:700]])
    if not bias_text and not dashboard:
        lines.append(
            "（本份 summary 未抽出偏誤／儀表板敘事；請參考 facts.bias_hint 與完整 markdown。）"
        )

    lines.append("")
    if not has_holdings:
        lines.append(
            "你目前沒有登記持股，問題較接近「要不要新建曝險」。"
            "是否進場還需要標的、部位大小與風險規則——那些不在本份 brief 範圍。"
        )
    else:
        lines.append(
            "你雖有持股紀錄，但本對話仍只依市場日報回答開盤環境；"
            "個別部位處置請走既有部位／持股分析流程，此處不提供加減碼指令。"
        )
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines).strip()


def _holding_lots(share_count: Any) -> str:
    try:
        shares = float(share_count)
    except (TypeError, ValueError):
        return "—"
    lots = shares / 1000.0
    return f"{lots:g} 張（{int(shares):,} 股）"


def build_position_template_reply(
    *,
    facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
) -> str:
    rows = list(holdings or [])[:MAX_HOLDINGS]
    hint = _extract_bias_hint(facts, summary)
    label = _bias_label(hint)
    trade_date = (facts or summary or {}).get("trade_date") or "—"

    if not rows:
        return (
            "你目前沒有登記持股。本對話不會編造成本或張數；"
            "若要建立持股資料，請先到持股設定登錄，再使用個股／部位分析流程。\n"
            f"今日 brief（{trade_date}）開盤偏誤環境為**{label}**，"
            f"但仍不構成進出場指令。\n\n{DISCLAIMER}"
        )

    lines = [
        f"依你登記的持股（共 {len(rows)} 檔）與 {trade_date} brief：",
        "",
        "【持股摘要】",
    ]
    for row in rows:
        stock_id = str(row.get("stock_id") or row.get("stockId") or "—")
        shares = row.get("share_count", row.get("shareCount"))
        avg = row.get("avg_cost", row.get("avgCost"))
        avg_s = f"{float(avg):.2f}" if isinstance(avg, (int, float)) else "—"
        lines.append(f"- {stock_id}：{_holding_lots(shares)}，均價 {avg_s}")

    bias_text = ((summary or {}).get("bias") or "").strip()
    dashboard = ((summary or {}).get("dashboard") or "").strip()
    lines.extend(
        [
            "",
            f"【市場環境】開盤偏誤提示 **{label}**（bias_hint={hint or 'neutral'}）。",
        ]
    )
    if bias_text:
        lines.extend(["", bias_text[:500]])
    if dashboard:
        lines.extend(["", "儀表板：" + dashboard[:400]])

    lines.extend(
        [
            "",
            "以上只整理持股事實與開盤環境，**不提供**加減碼／停損下單指令。"
            "若要針對單一標的做紀律討論，請走既有個股報告或部位分析流程。",
            "",
            DISCLAIMER,
        ]
    )
    return "\n".join(lines).strip()


def build_out_of_scope_reply() -> str:
    return (
        "這個問題超出本份開盤前 brief 與可用外訊資料範圍。"
        "請改問 brief 內的量價、三大法人、技術錨點、台積電或那指／費半對帳，"
        f"或明日開盤偏誤／儀表板條件。\n\n{DISCLAIMER}"
    )


def _resolve_us_tech_as_of(
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    trade_date: str | None,
) -> str | None:
    us = (facts or {}).get("us") or (summary or {}).get("us") or {}
    for key in ("as_of",):
        val = us.get(key)
        if isinstance(val, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", val):
            return val
    if facts and isinstance(facts.get("us_as_of"), str):
        return str(facts["us_as_of"])
    if trade_date and re.match(r"^\d{4}-\d{2}-\d{2}$", trade_date):
        return trade_date
    return None


def load_us_tech_news_bullets(
    as_of: str | None,
    *,
    query: str = "",
    limit: int = MAX_NEWS_BULLETS,
    root: Path | None = None,
) -> tuple[list[dict[str, str]], str | None]:
    """Load headlines from reports/us-tech/{date}_raw.json (or latest)."""
    base = root or US_TECH_ROOT
    if not base.is_dir():
        return [], None

    path: Path | None = None
    used_date: str | None = None
    if as_of:
        candidate = base / f"{as_of}_raw.json"
        if candidate.exists():
            path = candidate
            used_date = as_of
    if path is None:
        files = sorted(base.glob("*_raw.json"), reverse=True)
        if not files:
            return [], None
        path = files[0]
        used_date = path.name.replace("_raw.json", "")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], used_date

    items = payload.get("items") or []
    keywords = [
        w
        for w in re.split(r"\s+", query.lower())
        if len(w) >= 2 and w not in {"新聞", "剛出", "搜尋", "幫我", "什麼", "如何"}
    ]
    scored: list[tuple[int, dict[str, str]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        blob = f"{title} {item.get('summary') or ''} {item.get('category') or ''}".lower()
        score = 0
        for kw in keywords:
            if kw.lower() in blob:
                score += 2
        cat = str(item.get("category") or "").lower()
        if cat in {"tech", "semiconductor", "nasdaq", "market"}:
            score += 1
        scored.append(
            (
                score,
                {
                    "title": title[:200],
                    "link": str(item.get("link") or "")[:300],
                    "published": str(item.get("published") or "")[:40],
                    "feed": str(item.get("feed_name") or item.get("feed_id") or ""),
                },
            )
        )
    scored.sort(
        key=lambda x: (x[0], x[1].get("published") or ""),
        reverse=True,
    )
    bullets = [row for score, row in scored if score > 0][:limit]
    if not bullets:
        bullets = [row for _, row in scored[:limit]]
    return bullets, used_date


def tavily_daily_limit() -> int:
    raw = os.environ.get("TAVILY_DAILY_LIMIT", str(DEFAULT_TAVILY_DAILY_LIMIT))
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_TAVILY_DAILY_LIMIT


def _quota_path(day: str | None = None, *, quota_dir: Path | None = None) -> Path:
    key = day or date.today().isoformat()
    root = quota_dir or QUOTA_ROOT
    return root / f"{key}.json"


def get_tavily_quota_used(
    day: str | None = None, *, quota_dir: Path | None = None
) -> int:
    path = _quota_path(day, quota_dir=quota_dir)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("tavily_used") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def increment_tavily_quota(
    day: str | None = None, *, quota_dir: Path | None = None
) -> int:
    path = _quota_path(day, quota_dir=quota_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    used = get_tavily_quota_used(day, quota_dir=quota_dir) + 1
    payload = {
        "tavily_used": used,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return used


def call_tavily_search(
    query: str,
    *,
    api_key: str | None = None,
    max_results: int = 5,
    timeout_sec: float = 30,
) -> list[dict[str, str]]:
    key = api_key or os.environ.get("TAVILY_API_KEY") or ""
    if not key:
        raise RuntimeError("未設定 TAVILY_API_KEY")
    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max_results,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Tavily HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"無法連線 Tavily：{exc.reason}") from exc

    results: list[dict[str, str]] = []
    for item in body.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        results.append(
            {
                "title": title[:200],
                "link": str(item.get("url") or "")[:300],
                "published": "",
                "feed": "tavily",
                "snippet": str(item.get("content") or "")[:240],
            }
        )
    return results


def build_news_template_reply(
    *,
    bullets: list[dict[str, str]],
    sources_used: list[str],
    rss_date: str | None,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    quota_exhausted: bool = False,
) -> str:
    hint = _extract_bias_hint(facts, summary)
    label = _bias_label(hint)
    trade_date = (facts or summary or {}).get("trade_date") or "—"
    lines = [
        f"以下為外訊摘要（搭配 {trade_date} brief 開盤偏誤環境 **{label}**）。",
        "這不是即時新聞保證，也不構成進出場建議。",
        "",
    ]
    if bullets:
        origin = []
        if "rss" in sources_used:
            origin.append(f"us-tech RSS{f'（{rss_date}）' if rss_date else ''}")
        if "tavily" in sources_used:
            origin.append("Tavily")
        lines.append("來源：" + " + ".join(origin))
        lines.append("")
        for i, item in enumerate(bullets[:MAX_NEWS_BULLETS], start=1):
            title = item.get("title") or ""
            extra = item.get("snippet") or item.get("published") or ""
            lines.append(f"{i}. {title}")
            if extra:
                lines.append(f"   {extra[:180]}")
    else:
        lines.append("目前沒有可用的外訊條目。")
        if quota_exhausted:
            lines.append("今日外訊搜尋額度已用完；僅能依 brief／既有 RSS。")
        else:
            lines.append("可改問 brief 內量價、法人、偏誤或儀表板。")

    lines.extend(["", DISCLAIMER])
    return "\n".join(lines).strip()


def resolve_external_news(
    *,
    message: str,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    trade_date: str | None,
    skip_tavily: bool = False,
    us_tech_root: Path | None = None,
    quota_dir: Path | None = None,
    tavily_search=None,
) -> tuple[str, list[str], list[dict[str, str]]]:
    """Return (reply, sources_used, bullets)."""
    sources_used: list[str] = ["brief"]
    as_of = _resolve_us_tech_as_of(facts, summary, trade_date)
    bullets, rss_date = load_us_tech_news_bullets(
        as_of, query=message, root=us_tech_root
    )
    if bullets:
        sources_used.append("rss")
        reply = build_news_template_reply(
            bullets=bullets,
            sources_used=sources_used,
            rss_date=rss_date,
            facts=facts,
            summary=summary,
        )
        return reply, sources_used, bullets

    # RSS miss → optional Tavily
    api_key = os.environ.get("TAVILY_API_KEY") or ""
    if skip_tavily or not api_key:
        reply = build_news_template_reply(
            bullets=[],
            sources_used=sources_used,
            rss_date=rss_date,
            facts=facts,
            summary=summary,
        )
        return reply, sources_used, []

    limit = tavily_daily_limit()
    used = get_tavily_quota_used(quota_dir=quota_dir)
    if used >= limit:
        reply = build_news_template_reply(
            bullets=[],
            sources_used=sources_used,
            rss_date=rss_date,
            facts=facts,
            summary=summary,
            quota_exhausted=True,
        )
        return reply, sources_used, []

    search_fn = tavily_search or call_tavily_search
    try:
        tavily_items = search_fn(message)
        increment_tavily_quota(quota_dir=quota_dir)
    except RuntimeError as exc:
        reply = (
            f"外訊搜尋失敗：{exc}\n"
            "請改依 brief 內資料提問。\n\n"
            f"{DISCLAIMER}"
        )
        return reply, sources_used, []

    if tavily_items:
        sources_used.append("tavily")
    reply = build_news_template_reply(
        bullets=tavily_items,
        sources_used=sources_used,
        rss_date=rss_date,
        facts=facts,
        summary=summary,
    )
    return reply, sources_used, tavily_items


def build_chat_messages(
    *,
    message: str,
    context: str,
    intent: Intent,
    has_holdings: bool,
    history: list[ChatHistoryItem] | None = None,
) -> list[dict[str, str]]:
    holdings_note = (
        "使用者目前無登記持股（has_holdings=false）。"
        if not has_holdings
        else "使用者有登記持股（has_holdings=true），但仍不得在此給部位下單指令。"
    )
    intent_note = {
        "entry_advice": (
            "使用者在問進場／買賣決策。不可回答要或不要；"
            "只能說明 bias_hint、基準／尾部情境與儀表板觀察條件。"
        ),
        "position_advice": (
            "使用者在問個人部位。可引用 HOLDINGS；不可編造成本或加減碼指令。"
        ),
        "external_news": "依 NEWS CONTEXT 與 brief 摘要外訊；禁止買賣建議。",
        "out_of_scope": "問題超出 brief；說明沒有該資料，並引導回 brief 內問題。",
        "factual_qa": "依 BRIEF CONTEXT 回答事實或開盤偏誤相關問題。",
    }[intent]

    system = (
        f"{SYSTEM_POLICY}\n\n"
        f"意圖分類：{intent}\n"
        f"意圖處理：{intent_note}\n"
        f"{holdings_note}\n\n"
        f"BRIEF CONTEXT:\n{context}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for item in (history or [])[-MAX_HISTORY_TURNS:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:1500]})
    messages.append({"role": "user", "content": message.strip()})
    return messages


def call_ollama(
    messages: list[dict[str, str]],
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> str:
    chunks: list[str] = []
    for piece in iter_ollama_chat(
        messages,
        base_url=base_url,
        model=model,
        timeout_sec=timeout_sec,
    ):
        chunks.append(piece)
    content = "".join(chunks).strip()
    if not content:
        raise RuntimeError("Ollama 回傳空白內容")
    if len(content) > MAX_REPLY_CHARS:
        content = content[: MAX_REPLY_CHARS - 20] + "…(截斷)"
    if DISCLAIMER not in content:
        content = f"{content}\n\n{DISCLAIMER}"
    return content


def iter_ollama_chat(
    messages: list[dict[str, str]],
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
):
    """Yield content deltas from Ollama native streaming /api/chat."""
    base = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip(
        "/"
    )
    model_name = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    # Native Ollama streaming (NDJSON) flushes per token more reliably than
    # the OpenAI-compatible SSE wrapper under urllib.
    url = f"{base}/api/chat"
    payload = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.2},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_sec)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Ollama HTTP {exc.code}（model={model_name}）：{detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"無法連線 Ollama（{base}）。請確認已啟動 ollama serve，"
            f"並設定 OLLAMA_BASE_URL / OLLAMA_MODEL。原因：{exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Ollama 逾時（>{timeout_sec}s）") from exc

    total = 0
    try:
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("done") and not (chunk.get("message") or {}).get("content"):
                break
            message = chunk.get("message") or {}
            piece = message.get("content") or ""
            if not piece:
                continue
            if total >= MAX_REPLY_CHARS:
                continue
            remain = MAX_REPLY_CHARS - total
            if len(piece) > remain:
                piece = piece[:remain] + "…(截斷)"
            total += len(piece)
            yield piece
            if chunk.get("done"):
                break
    finally:
        resp.close()


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunk_text_for_sse(text: str, *, size: int = 48) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def prepare_market_day_chat(
    *,
    message: str,
    facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
    trade_date: str | None = None,
    has_holdings: bool = False,
    holdings: list[dict[str, Any]] | None = None,
    history: list[ChatHistoryItem] | None = None,
    use_llm: bool = True,
    skip_tavily: bool = False,
    us_tech_root: Path | None = None,
    quota_dir: Path | None = None,
    tavily_search=None,
) -> dict[str, Any]:
    """
    Build a chat plan.

    Returns either:
    - {"mode":"complete", "result": {...}}
    - {"mode":"ollama", "meta": {...}, "messages": [...]}
    """
    msg = (message or "").strip()
    if not msg:
        raise ValueError("message 不可為空")

    if facts is None and summary is None and markdown is None:
        if not trade_date:
            raise ValueError("需要 artifacts 或 trade_date")
        loaded = load_artifacts(trade_date)
        facts = loaded.get("facts")
        summary = loaded.get("summary")
        markdown = loaded.get("markdown")

    if not facts and not summary and not markdown:
        raise ValueError("找不到可對話的 brief artifacts")

    holding_rows = list(holdings or [])[:MAX_HOLDINGS]
    if holding_rows:
        has_holdings = True

    intent = classify_intent(msg)
    sources_used: list[str] = ["brief"]
    factual_slot: FactualSlot | None = None
    context = build_chat_context(facts, summary, markdown)
    resolved_trade = (facts or summary or {}).get("trade_date") or trade_date
    resolved_session = (facts or summary or {}).get("for_session")
    bias_hint = _extract_bias_hint(facts, summary)

    def _complete(reply: str, source: str) -> dict[str, Any]:
        return {
            "mode": "complete",
            "result": {
                "reply": reply,
                "intent": intent,
                "source": source,
                "sources_used": sources_used,
                "factual_slot": factual_slot,
                "has_holdings": has_holdings,
                "trade_date": resolved_trade,
                "for_session": resolved_session,
                "bias_hint": bias_hint,
            },
        }

    def _ollama(
        messages: list[dict[str, str]],
        source: str = "ollama",
        fallback_reply: str | None = None,
        fallback_source: str | None = None,
    ) -> dict[str, Any]:
        return {
            "mode": "ollama",
            "messages": messages,
            "fallback_reply": fallback_reply,
            "fallback_source": fallback_source,
            "meta": {
                "intent": intent,
                "source": source,
                "sources_used": sources_used,
                "factual_slot": factual_slot,
                "has_holdings": has_holdings,
                "trade_date": resolved_trade,
                "for_session": resolved_session,
                "bias_hint": bias_hint,
            },
        }

    if intent == "entry_advice":
        return _complete(
            build_entry_template_reply(
                facts=facts, summary=summary, has_holdings=has_holdings
            ),
            "template",
        )
    if intent == "position_advice":
        if holding_rows:
            sources_used.append("holdings")
        return _complete(
            build_position_template_reply(
                facts=facts, summary=summary, holdings=holding_rows
            ),
            "template",
        )
    if intent == "external_news":
        reply, sources_used, bullets = resolve_external_news(
            message=msg,
            facts=facts,
            summary=summary,
            trade_date=trade_date
            or str((facts or summary or {}).get("trade_date") or ""),
            skip_tavily=skip_tavily or not use_llm,
            us_tech_root=us_tech_root,
            quota_dir=quota_dir,
            tavily_search=tavily_search,
        )
        if use_llm and bullets:
            news_block = "=== NEWS CONTEXT ===\n" + "\n".join(
                f"- {b.get('title')}" for b in bullets[:MAX_NEWS_BULLETS]
            )
            ctx = build_chat_context(
                facts, summary, markdown, extra_blocks=[news_block]
            )
            messages = build_chat_messages(
                message=msg,
                context=ctx,
                intent=intent,
                has_holdings=has_holdings,
                history=history,
            )
            return _ollama(
                messages,
                source="ollama",
                fallback_reply=reply,
                fallback_source="news_template",
            )
        return _complete(reply, "news_template")
    if intent == "out_of_scope":
        return _complete(build_out_of_scope_reply(), "template")

    factual_slot = match_factual_slot(msg)
    if factual_slot is not None:
        return _complete(
            build_factual_slot_reply(factual_slot, facts=facts, summary=summary),
            "facts_template",
        )
    if not use_llm:
        hint = bias_hint
        anchors = (facts or {}).get("anchors") or []
        reply = (
            f"[dry-run] intent=factual_qa bias_hint={hint or 'neutral'}\n"
            + "\n".join(f"- {a}" for a in anchors[:8])
            + f"\n\n{DISCLAIMER}"
        )
        return _complete(reply, "dry_run")

    messages = build_chat_messages(
        message=msg,
        context=context,
        intent=intent,
        has_holdings=has_holdings,
        history=history,
    )
    return _ollama(messages, source="ollama")


def iter_market_day_chat_events(
    *,
    message: str,
    facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
    trade_date: str | None = None,
    has_holdings: bool = False,
    holdings: list[dict[str, Any]] | None = None,
    history: list[ChatHistoryItem] | None = None,
    use_llm: bool = True,
    skip_tavily: bool = False,
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
    us_tech_root: Path | None = None,
    quota_dir: Path | None = None,
    tavily_search=None,
):
    """Yield SSE event tuples: (event_name, payload_dict)."""
    try:
        plan = prepare_market_day_chat(
            message=message,
            facts=facts,
            summary=summary,
            markdown=markdown,
            trade_date=trade_date,
            has_holdings=has_holdings,
            holdings=holdings,
            history=history,
            use_llm=use_llm,
            skip_tavily=skip_tavily,
            us_tech_root=us_tech_root,
            quota_dir=quota_dir,
            tavily_search=tavily_search,
        )
    except Exception as exc:
        yield ("error", {"error": str(exc)})
        return

    if plan["mode"] == "complete":
        result = plan["result"]
        yield ("meta", {k: v for k, v in result.items() if k != "reply"})
        for piece in _chunk_text_for_sse(result["reply"]):
            yield ("token", {"text": piece})
        yield ("done", result)
        return

    meta = plan["meta"]
    yield ("meta", meta)
    collected: list[str] = []
    try:
        for piece in iter_ollama_chat(
            plan["messages"],
            base_url=ollama_base_url,
            model=ollama_model,
        ):
            collected.append(piece)
            yield ("token", {"text": piece})
    except Exception as exc:
        fallback = plan.get("fallback_reply")
        if fallback and not collected:
            meta_fb = {**meta, "source": plan.get("fallback_source") or "news_template"}
            yield ("meta", meta_fb)
            for piece in _chunk_text_for_sse(fallback):
                yield ("token", {"text": piece})
            yield ("done", {**meta_fb, "reply": fallback})
            return
        if collected:
            partial = "".join(collected).strip()
            if partial and DISCLAIMER not in partial:
                suffix = f"\n\n{DISCLAIMER}"
                collected.append(suffix)
                yield ("token", {"text": suffix})
            result = {
                **meta,
                "reply": "".join(collected).strip(),
                "error": str(exc),
            }
            yield ("error", {"error": str(exc), "partial": True})
            yield ("done", result)
            return
        yield ("error", {"error": str(exc)})
        return

    reply = "".join(collected).strip()
    if reply and DISCLAIMER not in reply:
        suffix = f"\n\n{DISCLAIMER}"
        reply = f"{reply}{suffix}"
        yield ("token", {"text": suffix})
    if not reply:
        fallback = plan.get("fallback_reply")
        if fallback:
            meta_fb = {**meta, "source": plan.get("fallback_source") or "news_template"}
            yield ("meta", meta_fb)
            for piece in _chunk_text_for_sse(fallback):
                yield ("token", {"text": piece})
            yield ("done", {**meta_fb, "reply": fallback})
            return
        yield ("error", {"error": "Ollama 回傳空白內容"})
        return
    yield (
        "done",
        {
            **meta,
            "reply": reply,
        },
    )


def answer_market_day_chat(
    *,
    message: str,
    facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
    trade_date: str | None = None,
    has_holdings: bool = False,
    holdings: list[dict[str, Any]] | None = None,
    history: list[ChatHistoryItem] | None = None,
    use_llm: bool = True,
    skip_tavily: bool = False,
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
    us_tech_root: Path | None = None,
    quota_dir: Path | None = None,
    tavily_search=None,
) -> dict[str, Any]:
    """Answer a grounded question. Prefer BFF-supplied artifacts; else load by trade_date."""
    plan = prepare_market_day_chat(
        message=message,
        facts=facts,
        summary=summary,
        markdown=markdown,
        trade_date=trade_date,
        has_holdings=has_holdings,
        holdings=holdings,
        history=history,
        use_llm=use_llm,
        skip_tavily=skip_tavily,
        us_tech_root=us_tech_root,
        quota_dir=quota_dir,
        tavily_search=tavily_search,
    )
    if plan["mode"] == "complete":
        return plan["result"]

    try:
        reply = call_ollama(
            plan["messages"],
            base_url=ollama_base_url,
            model=ollama_model,
        )
    except RuntimeError:
        fallback = plan.get("fallback_reply")
        if fallback:
            return {
                **plan["meta"],
                "reply": fallback,
                "source": plan.get("fallback_source") or "news_template",
            }
        raise
    return {**plan["meta"], "reply": reply}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Market daily grounded chat（Phase 2：facts template / RSS / Tavily）"
    )
    parser.add_argument(
        "--date",
        required=True,
        help="trade_date YYYY-MM-DD（讀 reports/market/{date}/）",
    )
    parser.add_argument("--message", "-m", required=True, help="使用者問題")
    parser.add_argument(
        "--has-holdings",
        action="store_true",
        help="標記使用者有持股（預設無持股）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不呼叫 Ollama；factual slot／news／policy 用模板",
    )
    parser.add_argument(
        "--skip-tavily",
        action="store_true",
        help="外訊路徑略過 Tavily（僅 RSS／brief）",
    )
    parser.add_argument("--model", default=None, help="覆寫 OLLAMA_MODEL")
    parser.add_argument("--base-url", default=None, help="覆寫 OLLAMA_BASE_URL")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    args = parser.parse_args(argv)

    try:
        result = answer_market_day_chat(
            message=args.message,
            trade_date=args.date,
            has_holdings=bool(args.has_holdings),
            use_llm=not args.dry_run,
            skip_tavily=bool(args.skip_tavily) or bool(args.dry_run),
            ollama_base_url=args.base_url,
            ollama_model=args.model,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"intent={result['intent']} source={result['source']} "
            f"sources={result.get('sources_used')}"
        )
        print(result["reply"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
