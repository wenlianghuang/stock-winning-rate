"""Build structured visual-summary JSON from facts + agy report body."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fact_checks import _slice_after_keywords

SUMMARY_VERSION = 2

MARKET_SECTIONS = {
    "today_chip": ("當日籌碼", "籌碼解讀"),
    "trend": ("趨勢", "近"),
    "news": ("新聞", "事件"),
    "cross": ("交叉", "對照", "背離", "一致"),
    "scenarios": ("情境推演", "情境", "交易日"),
    "watch": ("觀察重點",),
}

POSITION_SECTIONS = {
    "position_status": ("部位現況",),
    "market_summary": ("市場面",),
    "cross": ("交叉對照", "部位與市場"),
    "scenarios": ("操作情境", "情境推演"),
    "risk": ("風險", "紀律"),
}


def summary_json_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(".summary.json")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _paragraph_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if any(line.startswith(("-", "*")) or re.match(r"^\d+[.)]", line) for line in lines):
        return ""
    return _clean_text(" ".join(lines))


def extract_list_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^[-*]\s+(.+)$", stripped)
        if match:
            items.append(_clean_text(match.group(1)))
            continue
        match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if match:
            items.append(_clean_text(match.group(1)))
    return items


def extract_scenarios(text: str) -> list[dict[str, str]]:
    scenarios: list[dict[str, str]] = []
    for item in extract_list_items(text):
        title_match = re.match(r"\*\*(.+?)\*\*[：:]\s*(.+)", item)
        if title_match:
            scenarios.append(
                {
                    "title": title_match.group(1).strip(),
                    "content": title_match.group(2).strip(),
                }
            )
            continue
        scenarios.append({"content": item})
    if scenarios:
        return scenarios

    paragraph = _paragraph_text(text)
    if paragraph:
        return [{"content": paragraph}]
    return []


def parse_markdown_table(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return []

    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def _tone_for_value(value: str) -> str:
    lowered = value.lower()
    if any(token in lowered for token in ("偏多", "買超", "多頭", "向上", "強於", "放大", "累積")):
        return "bullish"
    if any(token in lowered for token in ("偏空", "賣超", "空頭", "向下", "弱於", "萎縮", "調節")):
        return "bearish"
    if any(token in lowered for token in ("中性", "同步", "正常", "平穩", "糾結", "分歧")):
        return "neutral"
    return "info"


def _signal_row(category: str, label: str, value: str) -> dict[str, str]:
    return {
        "category": category,
        "label": label,
        "value": value,
        "tone": _tone_for_value(value),
    }


def build_signal_matrix(facts) -> list[dict[str, str]]:
    from chip_signals import (
        CHIP_REGIME_LABEL,
        INSTITUTIONAL_LABEL,
        MA20_SLOPE_LABEL,
        MA_ALIGNMENT_LABEL,
        MA_STACK_LABEL,
        MA_CROSS_PAIR_LABEL,
        MA_CROSS_RECENCY_LABEL,
        RSI_ZONE_LABEL,
        MARGIN_MOMENTUM_LABEL,
        MARGIN_SHORT_RATIO_ZONE_LABEL,
        TREND_STRENGTH_LABEL,
        VOLATILITY_REGIME_LABEL,
        RS_LABEL,
        VOLUME_ANOMALY_LABEL,
        VOLUME_PRICE_DIVERGENCE_LABEL,
        VOLUME_TREND_LABEL,
    )

    rows: list[dict[str, str]] = []

    if facts.ma_stack and facts.ma_stack != "unknown":
        rows.append(
            _signal_row("technical", "均線排列", MA_STACK_LABEL[facts.ma_stack])
        )
    if (
        getattr(facts, "ma5_cross_ma10", "unknown") in {"golden", "death"}
        and getattr(facts, "ma5_cross_recency", "unknown") in {"today", "within_3d"}
    ):
        rows.append(
            _signal_row(
                "technical",
                "MA5/MA10交叉",
                f"{MA_CROSS_PAIR_LABEL[(facts.ma5_cross_ma10, 'ma5_ma10')]}"
                f"（{MA_CROSS_RECENCY_LABEL[facts.ma5_cross_recency]}）",
            )
        )
    if (
        getattr(facts, "ma10_cross_ma20", "unknown") in {"golden", "death"}
        and getattr(facts, "ma10_cross_recency", "unknown") in {"today", "within_3d"}
    ):
        rows.append(
            _signal_row(
                "technical",
                "MA10/MA20交叉",
                f"{MA_CROSS_PAIR_LABEL[(facts.ma10_cross_ma20, 'ma10_ma20')]}"
                f"（{MA_CROSS_RECENCY_LABEL[facts.ma10_cross_recency]}）",
            )
        )
    if facts.ma20_slope and facts.ma20_slope != "unknown":
        rows.append(
            _signal_row("technical", "月線斜率", MA20_SLOPE_LABEL[facts.ma20_slope])
        )
    if (
        getattr(facts, "rsi_zone", "unknown") != "unknown"
        and getattr(facts, "rsi_14", None) is not None
    ):
        rows.append(
            _signal_row(
                "technical",
                "RSI動能",
                f"{RSI_ZONE_LABEL[facts.rsi_zone]}（{facts.rsi_14:.1f}）",
            )
        )
    if (
        getattr(facts, "volatility_regime", "unknown") != "unknown"
        and getattr(facts, "atr_pct", None) is not None
    ):
        rows.append(
            _signal_row(
                "technical",
                "波動率",
                f"{VOLATILITY_REGIME_LABEL[facts.volatility_regime]}"
                f"（ATR {facts.atr_pct:.1f}%）",
            )
        )
    if (
        getattr(facts, "trend_strength", "unknown") != "unknown"
        and getattr(facts, "adx_14", None) is not None
    ):
        rows.append(
            _signal_row(
                "technical",
                "趨勢強度",
                f"{TREND_STRENGTH_LABEL[facts.trend_strength]}"
                f"（ADX {facts.adx_14:.1f}）",
            )
        )
    if facts.ma_alignment and facts.ma_alignment != "unknown":
        rows.append(
            _signal_row(
                "technical",
                "短中線對齊",
                MA_ALIGNMENT_LABEL[facts.ma_alignment],
            )
        )
    if facts.close_vs_ma20_pct is not None and facts.ma20_position != "unknown":
        pos = {"above": "站上", "below": "跌破", "at": "貼近"}[facts.ma20_position]
        rows.append(
            _signal_row(
                "technical",
                "月線位置",
                f"{pos} MA20（{facts.close_vs_ma20_pct:+.1f}%）",
            )
        )

    if facts.chip_regime and facts.chip_regime != "unknown":
        rows.append(
            _signal_row("chip", "籌碼型態", CHIP_REGIME_LABEL[facts.chip_regime])
        )
    if facts.institutional_consensus and facts.institutional_consensus != "unknown":
        rows.append(
            _signal_row(
                "chip",
                "法人共識",
                INSTITUTIONAL_LABEL[facts.institutional_consensus],
            )
        )
    if facts.major_foreign_divergence:
        rows.append(_signal_row("chip", "主力外資", "方向背離"))
    if (
        getattr(facts, "margin_short_ratio_zone", "unknown") != "unknown"
        and getattr(facts, "margin_short_ratio_pct", None) is not None
    ):
        rows.append(
            _signal_row(
                "chip",
                "券資比",
                f"{MARGIN_SHORT_RATIO_ZONE_LABEL[facts.margin_short_ratio_zone]}"
                f"（{facts.margin_short_ratio_pct:.1f}%）",
            )
        )
    if (
        getattr(facts, "margin_momentum", "unknown") != "unknown"
        and getattr(facts, "margin_momentum_pct", None) is not None
    ):
        rows.append(
            _signal_row(
                "chip",
                "融資動能",
                f"{MARGIN_MOMENTUM_LABEL[facts.margin_momentum]}"
                f"（{facts.margin_momentum_pct:+.1f}%）",
            )
        )
    if facts.volume_anomaly and facts.volume_anomaly != "unknown":
        rows.append(
            _signal_row("chip", "成交量", VOLUME_ANOMALY_LABEL[facts.volume_anomaly])
        )
    if getattr(facts, "volume_trend", "unknown") not in {"unknown", "stable"}:
        rows.append(
            _signal_row(
                "technical",
                "量能趨勢",
                VOLUME_TREND_LABEL[facts.volume_trend],
            )
        )
    if getattr(facts, "volume_price_divergence", "unknown") not in {
        "unknown",
        "none",
    }:
        rows.append(
            _signal_row(
                "technical",
                "價量關係",
                VOLUME_PRICE_DIVERGENCE_LABEL[facts.volume_price_divergence],
            )
        )

    rs = facts.rs_period if facts.rs_period != "unknown" else facts.rs_today
    if rs and rs != "unknown":
        rows.append(_signal_row("market", "相對大盤", RS_LABEL[rs]))
    if facts.market_trend and facts.market_trend != "unknown":
        trend_label = {"up": "大盤偏多", "down": "大盤偏空", "flat": "大盤盤整"}.get(
            facts.market_trend,
            facts.market_trend,
        )
        rows.append(_signal_row("market", "大盤趨勢", trend_label))

    return rows


def _to_int(raw: object) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"—", "-", "NA", "N/A"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_float(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "")
    if not text or text in {"—", "-", "NA", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_institutional_flow(history_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    flow: list[dict[str, Any]] = []
    for row in history_rows:
        date = str(row.get("日期", "")).strip()
        if not date:
            continue
        entry: dict[str, Any] = {"date": date}
        foreign = _to_int(row.get("外資買賣超_張"))
        trust = _to_int(row.get("投信買賣超_張"))
        dealer = _to_int(row.get("自營商買賣超_張"))
        if foreign is not None:
            entry["foreign"] = foreign
        if trust is not None:
            entry["trust"] = trust
        if dealer is not None:
            entry["dealer"] = dealer
        major_ok = str(row.get("主力_擷取狀態", "")).strip().lower() == "ok"
        if major_ok:
            major = _to_int(row.get("主力買賣超_張"))
            entry["major"] = major
        if len(entry) > 1:
            flow.append(entry)
    return flow


def build_key_metrics(facts, row: dict[str, str] | None = None) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    close = _to_float((row or {}).get("收盤價"))
    if close is not None:
        metrics["close"] = close
    if facts.today_change_pct is not None:
        metrics["today_change_pct"] = facts.today_change_pct
    if facts.period_return_pct is not None:
        metrics["period_return_pct"] = facts.period_return_pct
    if facts.foreign_net_lots is not None:
        metrics["foreign_net_lots"] = facts.foreign_net_lots
    if facts.trust_net_lots is not None:
        metrics["trust_net_lots"] = facts.trust_net_lots
    if facts.dealer_net_lots is not None:
        metrics["dealer_net_lots"] = facts.dealer_net_lots
    if facts.major_net_lots is not None and facts.major_available:
        metrics["major_net_lots"] = facts.major_net_lots
    if facts.volume_today_lots is not None:
        metrics["volume_today_lots"] = facts.volume_today_lots
    if facts.market_change_pct is not None:
        metrics["market_change_pct"] = facts.market_change_pct
    return metrics


def _extract_news_rows(body: str) -> list[dict[str, str]]:
    section = _slice_after_keywords(body, MARKET_SECTIONS["news"])
    rows = parse_markdown_table(section)
    news: list[dict[str, str]] = []
    for row in rows:
        news.append(
            {
                "date": row.get("日期", "").strip(),
                "title": row.get("標題", "").strip(),
                "category": row.get("分類", "").strip(),
                "summary": row.get("摘要", "").strip(),
            }
        )
    return [item for item in news if item["title"]]


def build_market_summary(
    facts,
    body: str,
    *,
    row: dict[str, str] | None = None,
    history_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    today_section = _slice_after_keywords(body, MARKET_SECTIONS["today_chip"])
    trend_section = _slice_after_keywords(body, MARKET_SECTIONS["trend"])
    cross_section = _slice_after_keywords(body, MARKET_SECTIONS["cross"])
    scenario_section = _slice_after_keywords(body, MARKET_SECTIONS["scenarios"])
    watch_section = _slice_after_keywords(body, MARKET_SECTIONS["watch"])

    return {
        "version": SUMMARY_VERSION,
        "stock_id": facts.stock_id,
        "stock_name": facts.stock_name,
        "trade_date": facts.trade_date,
        "signal_matrix": build_signal_matrix(facts),
        "key_metrics": build_key_metrics(facts, row),
        "institutional_flow": build_institutional_flow(history_rows or []),
        "news": _extract_news_rows(body),
        "narrative": {
            "today_chip": _paragraph_text(today_section) or None,
            "trend": _paragraph_text(trend_section) or None,
            "cross_points": extract_list_items(cross_section),
            "scenarios": extract_scenarios(scenario_section),
            "watch_items": extract_list_items(watch_section),
        },
        "anchors": list(facts.anchors or []),
    }


def build_position_summary(position_facts, body: str) -> dict[str, Any]:
    from position_signals import (
        DualPositionBundle,
        PNL_BUCKET_LABEL,
        POSITION_BIAS_LABEL,
    )

    status_section = _slice_after_keywords(body, POSITION_SECTIONS["position_status"])
    market_section = _slice_after_keywords(body, POSITION_SECTIONS["market_summary"])
    cross_section = _slice_after_keywords(body, POSITION_SECTIONS["cross"])
    scenario_section = _slice_after_keywords(body, POSITION_SECTIONS["scenarios"])
    risk_section = _slice_after_keywords(body, POSITION_SECTIONS["risk"])

    combined = (
        position_facts.combined
        if isinstance(position_facts, DualPositionBundle)
        else position_facts
    )

    scenario_plan: list[dict[str, Any]] = []
    if combined.scenario_plan:
        rank_labels = ("主線", "次線", "尾線")
        for index, item in enumerate(combined.scenario_plan.scenarios):
            rank = rank_labels[index] if index < len(rank_labels) else f"情境{index + 1}"
            scenario_plan.append(
                {
                    "rank": rank,
                    "label": item.label,
                    "weight_pct": item.weight_pct,
                    "action": item.action,
                    "trigger_hint": item.trigger_hint,
                }
            )

    def _leg_summary(leg) -> dict[str, Any] | None:
        if leg is None:
            return None
        return {
            "shares": leg.shares,
            "avg_cost": leg.avg_cost,
            "unrealized_pnl_pct": leg.unrealized_pnl_pct,
            "pnl_bucket": leg.pnl_bucket,
            "pnl_bucket_label": PNL_BUCKET_LABEL.get(leg.pnl_bucket, ""),
            "position_bias": leg.position_bias,
            "position_bias_label": POSITION_BIAS_LABEL.get(leg.position_bias, ""),
            "uses_margin": bool(leg.uses_margin),
            "maintenance_rate_pct": getattr(leg, "maintenance_rate_pct", None),
            "distance_to_call_pp": getattr(leg, "distance_to_call_pp", None),
            "margin_call_price": getattr(leg, "margin_call_price", None),
            "distance_to_call_price_pct": getattr(
                leg, "distance_to_call_price_pct", None
            ),
            "margin_pressure_zone": getattr(leg, "margin_pressure_zone", "unknown"),
            "margin_pressure_label": getattr(leg, "margin_pressure_label", ""),
        }

    payload: dict[str, Any] = {
        "version": SUMMARY_VERSION,
        "unrealized_pnl_pct": combined.unrealized_pnl_pct,
        "pnl_bucket": combined.pnl_bucket,
        "pnl_bucket_label": PNL_BUCKET_LABEL.get(combined.pnl_bucket, ""),
        "position_bias": combined.position_bias,
        "position_bias_label": POSITION_BIAS_LABEL.get(combined.position_bias, ""),
        "avg_cost": combined.avg_cost,
        "shares": combined.shares,
        "uses_margin": bool(getattr(combined, "uses_margin", False)),
        "maintenance_rate_pct": getattr(combined, "maintenance_rate_pct", None),
        "distance_to_call_pp": getattr(combined, "distance_to_call_pp", None),
        "margin_call_price": getattr(combined, "margin_call_price", None),
        "distance_to_call_price_pct": getattr(
            combined, "distance_to_call_price_pct", None
        ),
        "margin_pressure_zone": getattr(combined, "margin_pressure_zone", "unknown"),
        "margin_pressure_label": getattr(combined, "margin_pressure_label", ""),
        "scenario_plan": scenario_plan,
        "narrative": {
            "position_status": _paragraph_text(status_section) or None,
            "market_summary": _paragraph_text(market_section) or None,
            "cross_points": extract_list_items(cross_section),
            "scenarios": extract_scenarios(scenario_section),
            "risk_items": extract_list_items(risk_section),
        },
        "anchors": list(combined.anchors or []),
    }

    if isinstance(position_facts, DualPositionBundle):
        payload["priority"] = position_facts.priority
        payload["priority_label"] = position_facts.priority_label
        payload["synthesis_hint"] = position_facts.synthesis_hint
        payload["cash"] = _leg_summary(position_facts.cash)
        payload["margin"] = _leg_summary(position_facts.margin)

    return payload


def write_market_summary(
    csv_path: Path,
    facts,
    body: str,
    *,
    row: dict[str, str] | None = None,
    history_rows: list[dict[str, str]] | None = None,
) -> Path:
    payload = {
        "market": build_market_summary(
            facts, body, row=row, history_rows=history_rows
        )
    }
    path = summary_json_path(csv_path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def merge_position_summary(
    csv_path: Path,
    position_facts,
    body: str,
) -> Path:
    path = summary_json_path(csv_path)
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}

    payload["position"] = build_position_summary(position_facts, body)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_summary_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
