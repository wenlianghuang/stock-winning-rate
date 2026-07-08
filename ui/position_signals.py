"""Deterministic position facts computed from holding cost + CSV close/MA20.

Mirrors ``chip_signals`` but for the *position* dimension: Python owns the
verdicts (profit/loss bucket, distance to breakeven, cost vs MA20, suggested
bias), so the operation-scenario narrative can be mechanically gated on the
actual holding — not just generic boilerplate that ignores the cost basis.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from fact_checks import _slice_after_keywords

# 損益分桶門檻（%）。可依個人停利/停損習慣調整。
PROFIT_LARGE_PCT = 15.0
PROFIT_SMALL_PCT = 3.0
LOSS_SMALL_PCT = -3.0
LOSS_LARGE_PCT = -8.0

_ACTION_SECTION_KEYWORDS = ("操作情境", "操作", "情境")

# 操作情境須將分析錨定在部位損益上（否則視為泛泛而談）
POSITION_CONTEXT_KEYWORDS = (
    "獲利",
    "虧損",
    "損益",
    "成本",
    "均價",
    "套牢",
    "停利",
    "停損",
    "本金",
    "解套",
)
PROFIT_PROTECT_KEYWORDS = (
    "停利",
    "移動停損",
    "移動停利",
    "獲利了結",
    "分批",
    "續抱",
    "鎖利",
    "保護獲利",
    "落袋",
)
PROFIT_SMALL_KEYWORDS = (
    "加碼",
    "停利",
    "續抱",
    "回吐",
    "回檔",
    "獲利了結",
    "抱緊",
)
RISK_CONTROL_KEYWORDS = (
    "停損",
    "減碼",
    "出場",
    "攤平",
    "調節",
    "認賠",
)
TRIGGER_KEYWORDS = ("觸發", "條件", "若", "一旦", "則")
BREAKEVEN_ACTION_KEYWORDS = ("出場", "加碼", "減碼", "停損", "觀望", "解套")

PNL_BUCKET_LABEL = {
    "profit_large": "大幅獲利（逾 15%）",
    "profit_small": "小幅獲利（3%~15%）",
    "breakeven": "損益兩平附近（±3%）",
    "loss_small": "小幅虧損（-3%~-8%）",
    "loss_large": "大幅虧損（逾 -8%）",
    "unknown": "無法試算（缺收盤價）",
}

POSITION_BIAS_LABEL = {
    "protect_gains": "偏向保護獲利（留意停利/回吐）",
    "neutral": "中性（依市場面與觸發條件操作）",
    "cautious": "偏向謹慎（虧損擴大須設防線）",
    "defensive": "偏向防禦（嚴守停損紀律）",
    "unknown": "資料不足",
}

FactIssue = tuple[str, str]


@dataclass
class PositionFacts:
    stock_id: str
    stock_name: str
    avg_cost: float
    shares: int
    close_price: float | None
    unrealized_pnl_pct: float | None
    pnl_bucket: str
    cost_vs_ma20: str  # "above" | "below" | "at" | "unknown"
    breakeven_move_pct: float | None  # 價格須變動多少 % 才回到成本（正=須上漲）
    position_bias: str
    required_action_hint: str = ""
    anchors: list[str] = field(default_factory=list)


def _to_float(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("+", "")
    if not text or text in {"—", "-", "NA", "N/A", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _pnl_bucket(pnl_pct: float | None) -> str:
    if pnl_pct is None:
        return "unknown"
    if pnl_pct >= PROFIT_LARGE_PCT:
        return "profit_large"
    if pnl_pct >= PROFIT_SMALL_PCT:
        return "profit_small"
    if pnl_pct > LOSS_SMALL_PCT:
        return "breakeven"
    if pnl_pct > LOSS_LARGE_PCT:
        return "loss_small"
    return "loss_large"


def _cost_vs_ma20(avg_cost: float, ma20: float | None) -> str:
    if ma20 is None or ma20 <= 0 or avg_cost <= 0:
        return "unknown"
    diff_pct = (avg_cost - ma20) / ma20 * 100.0
    if diff_pct > 0.3:
        return "above"
    if diff_pct < -0.3:
        return "below"
    return "at"


def _position_bias(bucket: str) -> str:
    return {
        "profit_large": "protect_gains",
        "profit_small": "neutral",
        "breakeven": "neutral",
        "loss_small": "cautious",
        "loss_large": "defensive",
        "unknown": "unknown",
    }[bucket]


def _required_action_hint(bucket: str) -> str:
    return {
        "profit_large": "停利 / 移動停損 / 獲利了結 / 分批 / 續抱（擇一並說明）",
        "profit_small": "加碼條件 / 停利 / 續抱 / 回吐風險（擇一）",
        "breakeven": "明確的出場或加碼觸發價（含條件）",
        "loss_small": "停損 / 減碼 / 攤平前提（擇一）",
        "loss_large": "停損 / 減碼 / 出場等防禦手段",
        "unknown": "",
    }[bucket]


def build_position_facts(
    row: dict,
    *,
    stock_id: str,
    stock_name: str,
    avg_cost: float,
    shares: int,
) -> PositionFacts:
    close_price = _to_float(row.get("收盤價"))
    ma20 = _to_float(row.get("MA20"))

    if close_price is not None and avg_cost > 0:
        pnl_pct = (close_price - avg_cost) / avg_cost * 100.0
        breakeven_move = (avg_cost - close_price) / close_price * 100.0
    else:
        pnl_pct = None
        breakeven_move = None

    bucket = _pnl_bucket(pnl_pct)
    cost_ma20 = _cost_vs_ma20(avg_cost, ma20)
    bias = _position_bias(bucket)

    facts = PositionFacts(
        stock_id=stock_id,
        stock_name=stock_name,
        avg_cost=avg_cost,
        shares=shares,
        close_price=close_price,
        unrealized_pnl_pct=round(pnl_pct, 2) if pnl_pct is not None else None,
        pnl_bucket=bucket,
        cost_vs_ma20=cost_ma20,
        breakeven_move_pct=round(breakeven_move, 2) if breakeven_move is not None else None,
        position_bias=bias,
        required_action_hint=_required_action_hint(bucket),
    )
    facts.anchors = _build_anchors(facts)
    return facts


def _build_anchors(facts: PositionFacts) -> list[str]:
    anchors: list[str] = []
    if facts.unrealized_pnl_pct is not None:
        anchors.append(
            f"未實現損益 {facts.unrealized_pnl_pct:+.2f}%"
            f"（{PNL_BUCKET_LABEL[facts.pnl_bucket]}）"
        )
    if facts.breakeven_move_pct is not None and facts.pnl_bucket != "breakeven":
        if facts.breakeven_move_pct > 0:
            anchors.append(f"距損益兩平：須再上漲約 {facts.breakeven_move_pct:.1f}%")
        elif facts.breakeven_move_pct < 0:
            anchors.append(
                f"獲利緩衝：跌 {abs(facts.breakeven_move_pct):.1f}% 才回到成本"
            )
    if facts.cost_vs_ma20 == "above":
        anchors.append("持股均價高於 MA20（月線在成本下方，中期反壓偏重）")
    elif facts.cost_vs_ma20 == "below":
        anchors.append("持股均價低於 MA20（成本在月線下方，中期仍有支撐）")
    anchors.append(POSITION_BIAS_LABEL[facts.position_bias])
    return anchors


def position_facts_summary_for_prompt(facts: PositionFacts) -> str:
    lines = [
        "【部位狀態（系統試算，操作情境須對齊此狀態）】",
    ]
    if facts.unrealized_pnl_pct is not None:
        lines.append(
            f"- 未實現損益：{facts.unrealized_pnl_pct:+.2f}%"
            f"（分類：{PNL_BUCKET_LABEL[facts.pnl_bucket]}）"
        )
    else:
        lines.append("- 未實現損益：無法試算（缺收盤價）")
    if facts.breakeven_move_pct is not None and facts.pnl_bucket != "breakeven":
        if facts.breakeven_move_pct > 0:
            lines.append(f"- 距損益兩平：須再上漲約 {facts.breakeven_move_pct:.1f}%")
        elif facts.breakeven_move_pct < 0:
            lines.append(
                f"- 獲利緩衝：須下跌約 {abs(facts.breakeven_move_pct):.1f}% 才回到成本"
            )
    if facts.cost_vs_ma20 != "unknown":
        pos = {"above": "高於", "below": "低於", "at": "貼近"}[facts.cost_vs_ma20]
        lines.append(f"- 持股均價{pos} MA20（月線）")
    lines.append(f"- 系統傾向：{POSITION_BIAS_LABEL[facts.position_bias]}")
    if facts.required_action_hint:
        lines.append(f"- 操作情境至少須明確提及：{facts.required_action_hint}")
    return "\n".join(lines)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def run_position_checks(body: str, facts: PositionFacts | None) -> list[FactIssue]:
    """Bucket-aware position-decision checks as ``(code, message)`` tuples."""
    if facts is None or facts.unrealized_pnl_pct is None:
        return []

    text = body.strip()
    action_region = _slice_after_keywords(text, _ACTION_SECTION_KEYWORDS)
    region = action_region if action_region.strip() else text
    bucket = facts.pnl_bucket
    issues: list[FactIssue] = []

    if not _contains_any(region, POSITION_CONTEXT_KEYWORDS):
        issues.append(
            (
                "position_scenario_unanchored",
                "操作情境未錨定部位損益（須提及獲利/虧損/成本/均價/套牢等），"
                "而非泛泛而談",
            )
        )

    if bucket == "profit_large":
        if not _contains_any(region, PROFIT_PROTECT_KEYWORDS):
            issues.append(
                (
                    "position_profit_no_protection",
                    f"部位已大幅獲利（{facts.unrealized_pnl_pct:+.1f}%），"
                    "操作情境須提出停利/移動停損/獲利了結，或明確論證續抱理由",
                )
            )
    elif bucket == "profit_small":
        if not _contains_any(region, PROFIT_SMALL_KEYWORDS):
            issues.append(
                (
                    "position_profit_no_plan",
                    f"部位小幅獲利（{facts.unrealized_pnl_pct:+.1f}%），"
                    "操作情境須討論加碼條件/停利/續抱或獲利回吐風險",
                )
            )
    elif bucket == "breakeven":
        if not (
            _contains_any(region, TRIGGER_KEYWORDS)
            and _contains_any(region, BREAKEVEN_ACTION_KEYWORDS)
        ):
            issues.append(
                (
                    "position_breakeven_no_trigger",
                    "部位接近損益兩平，操作情境須給出明確的出場或加碼觸發條件",
                )
            )
    elif bucket in {"loss_small", "loss_large"}:
        if not _contains_any(region, RISK_CONTROL_KEYWORDS):
            issues.append(
                (
                    "position_loss_no_risk_control",
                    f"部位未實現損益約 {facts.unrealized_pnl_pct:.1f}%（虧損），"
                    "操作情境須提出停損/減碼/出場等具體防禦手段",
                )
            )

    return issues


def write_position_facts_json(path, facts: PositionFacts) -> None:
    from pathlib import Path

    Path(path).write_text(
        json.dumps(asdict(facts), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
