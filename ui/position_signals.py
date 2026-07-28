"""Deterministic position facts computed from holding cost + CSV close/MA20.

Mirrors ``chip_signals`` but for the *position* dimension: Python owns the
verdicts (profit/loss bucket, distance to breakeven, cost vs MA20, suggested
bias, scenario weights), so the operation-scenario narrative can be mechanically
gated on the actual holding — not just generic boilerplate.
"""

from __future__ import annotations

import json
import re
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
MARGIN_RISK_KEYWORDS = (
    "追繳",
    "斷頭",
    "维持率",
    "維持率",
    "強制平倉",
    "强制平仓",
    "融資風險",
    "融资风险",
    "融資追繳",
    "融资追缴",
)
_RISK_SECTION_KEYWORDS = ("風險", "纪律", "紀律")

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

SCENARIO_LABELS = {
    "continuation": "延續調節",
    "range": "橫盤整理",
    "rebound": "技術反彈",
}

SCENARIO_TRIGGER_HINTS = {
    "continuation": "若外資續賣、收盤維持 MA10/MA20 下方",
    "range": "若法人分歧、量能接近區間均值",
    "rebound": "若站回 MA10 且量能溫和放大",
}

STOP_LEVEL_KEYWORDS = (
    "近20日低",
    "20日低",
    "前低",
    "區間低點",
    "支撐區",
    "停損參考",
)
TARGET_LEVEL_KEYWORDS = (
    "近20日高",
    "20日高",
    "前高",
    "壓力區",
    "停利參考",
    "解套參考",
)

FactIssue = tuple[str, str]


@dataclass
class ScenarioItem:
    id: str
    label: str
    weight_pct: int
    action: str
    trigger_hint: str
    is_primary: bool = False


@dataclass
class ScenarioPlan:
    scenarios: list[ScenarioItem]
    primary_id: str


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
    position_bias: str
    breakeven_move_pct: float | None = None  # 價格須變動多少 % 才回到成本（正=須上漲）
    cost_vs_20d_high: str = "unknown"
    high_20d: float | None = None
    low_20d: float | None = None
    technical_stop_price: float | None = None
    technical_target_price: float | None = None
    stop_loss_hint: str = ""
    take_profit_hint: str = ""
    required_action_hint: str = ""
    uses_margin: bool = False
    scenario_plan: ScenarioPlan | None = None
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


def _cost_vs_level(avg_cost: float, level: float | None) -> str:
    if level is None or level <= 0 or avg_cost <= 0:
        return "unknown"
    diff_pct = (avg_cost - level) / level * 100.0
    if diff_pct > 0.3:
        return "above"
    if diff_pct < -0.3:
        return "below"
    return "at"


def _fmt_price(price: float | None) -> str:
    if price is None:
        return ""
    if price == int(price):
        return str(int(price))
    return f"{price:.2f}".rstrip("0").rstrip(".")


def _build_trade_levels(
    *,
    avg_cost: float,
    close_price: float | None,
    high_20d: float | None,
    low_20d: float | None,
    pnl_bucket: str,
    atr_pct: float | None = None,
    atr_14: float | None = None,
    volatility_regime: str = "unknown",
) -> dict[str, object]:
    empty: dict[str, object] = {
        "technical_stop_price": None,
        "technical_target_price": None,
        "stop_loss_hint": "",
        "take_profit_hint": "",
    }
    if low_20d is None or low_20d <= 0:
        return empty

    stop_price = round(low_20d, 2)
    stop_hint = f"停損參考：收盤跌破近20日低 {_fmt_price(stop_price)}"
    if volatility_regime == "high" and atr_pct is not None:
        stop_hint += f"；波動偏高（ATR約 {atr_pct:.1f}%），停損宜保守"
        if close_price is not None and atr_14 is not None:
            atr_stop = round(max(close_price - 2 * atr_14, 0), 2)
            stop_hint += f"；2×ATR 參考 {_fmt_price(atr_stop)}"
    elif volatility_regime == "low":
        stop_hint += "；波動偏低，區間高低參考較可靠"

    if pnl_bucket in {"profit_large", "profit_small"} and high_20d is not None:
        target_price = round(high_20d, 2)
        target_hint = f"停利參考：近20日高 {_fmt_price(target_price)}"
    elif pnl_bucket in {"loss_small", "loss_large", "breakeven"}:
        target_price = round(avg_cost, 2)
        if high_20d is not None:
            target_hint = (
                f"解套參考：均價 {_fmt_price(target_price)}；"
                f"上方壓力近20日高 {_fmt_price(round(high_20d, 2))}"
            )
        else:
            target_hint = f"解套參考：均價 {_fmt_price(target_price)}"
    elif high_20d is not None:
        target_price = round(high_20d, 2)
        target_hint = f"壓力參考：近20日高 {_fmt_price(target_price)}"
    else:
        target_price = None
        target_hint = ""

    return {
        "technical_stop_price": stop_price,
        "technical_target_price": target_price,
        "stop_loss_hint": stop_hint,
        "take_profit_hint": target_hint,
    }


def _range_levels_from_chip_or_row(row: dict, chip_facts) -> tuple[float | None, float | None]:
    if chip_facts is not None:
        high = getattr(chip_facts, "high_20d", None)
        low = getattr(chip_facts, "low_20d", None)
        if high is not None and low is not None:
            return high, low
    high = _to_float(row.get("區間20日高"))
    low = _to_float(row.get("區間20日低"))
    return high, low


def _position_bias(bucket: str) -> str:
    return {
        "profit_large": "protect_gains",
        "profit_small": "neutral",
        "breakeven": "neutral",
        "loss_small": "cautious",
        "loss_large": "defensive",
        "unknown": "unknown",
    }[bucket]


def _required_action_hint(bucket: str, *, uses_margin: bool = False) -> str:
    base = {
        "profit_large": "停利 / 移動停損 / 獲利了結 / 分批 / 續抱（擇一並說明）",
        "profit_small": "加碼條件 / 停利 / 續抱 / 回吐風險（擇一）",
        "breakeven": "明確的出場或加碼觸發價（含條件）",
        "loss_small": "停損 / 減碼 / 攤平前提（擇一）",
        "loss_large": "停損 / 減碼 / 出場等防禦手段",
        "unknown": "",
    }[bucket]
    if uses_margin and base:
        return f"{base}；融資部位另須談追繳／斷頭／維持率或減碼防禦"
    if uses_margin:
        return "融資部位須談追繳／斷頭／維持率或減碼防禦"
    return base


def _normalize_weights(scores: dict[str, int]) -> dict[str, int]:
    """Map raw scores to integer percentages summing to 100."""
    floored = {key: max(1, value) for key, value in scores.items()}
    total = sum(floored.values())
    raw = {key: value * 100.0 / total for key, value in floored.items()}
    ints = {key: int(raw[key]) for key in raw}
    remainder = 100 - sum(ints.values())
    if remainder:
        primary_key = max(floored, key=floored.get)
        ints[primary_key] += remainder
    return ints


def _action_for_scenario(scenario_id: str, position_bias: str) -> str:
    """Recommended operation per market scenario, aligned with position bias."""
    actions: dict[tuple[str, str], str] = {
        ("continuation", "defensive"): "減碼 / 觀望 / 停損",
        ("continuation", "cautious"): "減碼 / 觀望",
        ("continuation", "neutral"): "觀望 / 減碼",
        ("continuation", "protect_gains"): "獲利了結 / 減碼 / 移動停損",
        ("range", "defensive"): "持有觀望 / 不攤平",
        ("range", "cautious"): "持有觀望 / 減碼條件",
        ("range", "neutral"): "持有觀望",
        ("range", "protect_gains"): "續抱 / 觀望 / 分批停利",
        ("rebound", "defensive"): "不加碼 / 觀望（等站穩均線再評估）",
        ("rebound", "cautious"): "不加碼 / 觀望",
        ("rebound", "neutral"): "觀望 / 加碼條件（須站穩 MA20）",
        ("rebound", "protect_gains"): "續抱 / 分批停利 / 不加碼",
    }
    return actions.get(
        (scenario_id, position_bias),
        "觀望",
    )


def build_scenario_plan(
    position_facts: PositionFacts,
    chip_facts,
    base_rates: dict | None = None,
) -> ScenarioPlan:
    """Deterministic 3-scenario weights (sum 100%) from chip + position signals.

    When ``base_rates`` is provided, the aggregate historical forward edge of the
    stock's current regimes nudges 續抱/反彈 vs 調節 weights so the realized
    hit-rates actually move the numbers (not just the prompt). Callers that omit
    ``base_rates`` (e.g. unit tests) get the pure chip/position weighting.
    """
    scores = {"continuation": 34, "range": 33, "rebound": 33}

    chip_regime = getattr(chip_facts, "chip_regime", "unknown")
    if chip_regime == "distribution":
        scores["continuation"] += 15
        scores["rebound"] -= 10
    elif chip_regime == "accumulation":
        scores["rebound"] += 15
        scores["continuation"] -= 10

    price_trend = getattr(chip_facts, "price_trend", "unknown")
    if price_trend == "down":
        scores["continuation"] += 10
        scores["rebound"] -= 5
    elif price_trend == "up":
        scores["rebound"] += 10
        scores["continuation"] -= 5

    ma_mid = getattr(chip_facts, "ma_mid_alignment", "unknown")
    ma_short = getattr(chip_facts, "ma_short_alignment", "unknown")
    if ma_mid == "bearish":
        scores["continuation"] += 8
    elif ma_mid == "bullish":
        scores["rebound"] += 8
    elif ma_mid == "short_rebound":
        scores["rebound"] += 5
        scores["continuation"] -= 3
    elif ma_mid == "short_pullback":
        scores["continuation"] += 5
        scores["rebound"] -= 3

    if ma_short == "bearish":
        scores["continuation"] += 5
    elif ma_short == "bullish":
        scores["rebound"] += 5

    consensus = getattr(chip_facts, "institutional_consensus", "unknown")
    if consensus == "bearish":
        scores["continuation"] += 8
    elif consensus == "bullish":
        scores["rebound"] += 8
    elif consensus == "mixed":
        scores["range"] += 6

    foreign_dir = getattr(chip_facts, "foreign_direction", 0)
    if foreign_dir < 0:
        scores["continuation"] += 4
    elif foreign_dir > 0:
        scores["rebound"] += 4

    volume_price_div = getattr(chip_facts, "volume_price_divergence", "unknown")
    if volume_price_div == "bearish_divergence":
        scores["continuation"] += 5
        scores["rebound"] -= 3
    elif volume_price_div == "confirming_up":
        scores["rebound"] += 5
        scores["continuation"] -= 3
    elif volume_price_div == "bullish_divergence":
        scores["rebound"] += 3
    elif volume_price_div == "confirming_down":
        scores["continuation"] += 3

    rsi_zone = getattr(chip_facts, "rsi_zone", "unknown")
    if rsi_zone == "oversold":
        scores["rebound"] += 5
        scores["continuation"] -= 3
    elif rsi_zone == "overbought":
        scores["continuation"] += 5
        scores["rebound"] -= 3

    volatility_regime = getattr(chip_facts, "volatility_regime", "unknown")
    if volatility_regime == "high":
        scores["range"] += 5
        scores["rebound"] -= 3
        scores["continuation"] += 2
    elif volatility_regime == "low":
        scores["rebound"] += 3
        scores["continuation"] -= 2

    trend_strength = getattr(chip_facts, "trend_strength", "unknown")
    if trend_strength == "weak":
        scores["range"] += 8
        scores["rebound"] -= 3
        scores["continuation"] -= 3
    elif trend_strength == "strong":
        scores["range"] -= 5
        if ma_mid in {"bullish", "short_pullback"}:
            scores["rebound"] += 5
            scores["continuation"] -= 3
        elif ma_mid in {"bearish", "short_rebound"}:
            scores["continuation"] += 5
            scores["rebound"] -= 3
        else:
            scores["continuation"] += 3

    margin_ratio_zone = getattr(chip_facts, "margin_short_ratio_zone", "unknown")
    if margin_ratio_zone == "high":
        scores["continuation"] += 4
        scores["rebound"] -= 2
    elif margin_ratio_zone == "low":
        scores["rebound"] += 3

    margin_momentum = getattr(chip_facts, "margin_momentum", "unknown")
    if margin_momentum == "heating":
        scores["continuation"] += 4
        scores["rebound"] -= 2
        if price_trend == "down":
            scores["continuation"] += 3
    elif margin_momentum == "cooling":
        scores["range"] += 4
        scores["continuation"] -= 2

    ma5_cross = getattr(chip_facts, "ma5_cross_ma10", "unknown")
    ma5_cross_recency = getattr(chip_facts, "ma5_cross_recency", "unknown")
    if ma5_cross == "golden" and ma5_cross_recency in {"today", "within_3d"}:
        scores["rebound"] += 5
        scores["continuation"] -= 3
    elif ma5_cross == "death" and ma5_cross_recency in {"today", "within_3d"}:
        scores["continuation"] += 5
        scores["rebound"] -= 3

    ma10_cross = getattr(chip_facts, "ma10_cross_ma20", "unknown")
    ma10_cross_recency = getattr(chip_facts, "ma10_cross_recency", "unknown")
    if ma10_cross == "golden" and ma10_cross_recency in {"today", "within_3d"}:
        scores["rebound"] += 4
    elif ma10_cross == "death" and ma10_cross_recency in {"today", "within_3d"}:
        scores["continuation"] += 4

    bias = position_facts.position_bias
    if bias == "defensive":
        scores["continuation"] += 10
        scores["rebound"] -= 6
    elif bias == "cautious":
        scores["continuation"] += 6
        scores["rebound"] -= 3
    elif bias == "protect_gains":
        scores["range"] += 6
        scores["rebound"] -= 4
        if getattr(chip_facts, "rsi_zone", "unknown") == "overbought":
            scores["range"] += 4
            scores["rebound"] -= 2

    if base_rates:
        from chip_signals import BASE_RATE_TILT_BAND, base_rate_forward_edge

        edge = base_rate_forward_edge(chip_facts, base_rates)
        # 中性帶內（噪音級）不動權重，與命中率表的傾向標籤一致
        if edge is not None and abs(edge) >= BASE_RATE_TILT_BAND:
            # 1% 相對基準超額 → 4 分傾斜，上限 ±8（與中量級 chip 因子相當）
            shift = max(-8, min(8, round(edge * 4)))
            # base rate 來自單一市場環境（多為牛市輪動）的條件機率，與技術結構
            # 衝突時讓技術/動能主導：偏多傾向套在空頭結構、偏空傾向套在多頭結構
            # 時砍半，避免命中率蓋過明確的趨勢判讀。
            ma_stack = getattr(chip_facts, "ma_stack", "unknown")
            if shift > 0 and (ma_stack == "bearish_stack" or bias == "defensive"):
                shift //= 2
            elif shift < 0 and ma_stack == "bullish_stack":
                shift = -(-shift // 2)
            if shift > 0:
                scores["rebound"] += shift
                scores["continuation"] -= shift
            elif shift < 0:
                scores["continuation"] += -shift
                scores["rebound"] -= -shift

    weights = _normalize_weights(scores)
    primary_id = max(weights, key=weights.get)
    bias_key = position_facts.position_bias

    items = [
        ScenarioItem(
            id=scenario_id,
            label=SCENARIO_LABELS[scenario_id],
            weight_pct=weights[scenario_id],
            action=_action_for_scenario(scenario_id, bias_key),
            trigger_hint=SCENARIO_TRIGGER_HINTS[scenario_id],
            is_primary=scenario_id == primary_id,
        )
        for scenario_id in ("continuation", "range", "rebound")
    ]
    items.sort(key=lambda item: item.weight_pct, reverse=True)
    return ScenarioPlan(scenarios=items, primary_id=primary_id)


def build_position_facts(
    row: dict,
    *,
    stock_id: str,
    stock_name: str,
    avg_cost: float,
    shares: int,
    chip_facts=None,
    base_rates: dict | None = None,
    uses_margin: bool = False,
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
    high_20d, low_20d = _range_levels_from_chip_or_row(row, chip_facts)
    cost_vs_high = _cost_vs_level(avg_cost, high_20d)
    trade_levels = _build_trade_levels(
        avg_cost=avg_cost,
        close_price=close_price,
        high_20d=high_20d,
        low_20d=low_20d,
        pnl_bucket=bucket,
        atr_pct=getattr(chip_facts, "atr_pct", None) if chip_facts else None,
        atr_14=getattr(chip_facts, "atr_14", None) if chip_facts else None,
        volatility_regime=(
            getattr(chip_facts, "volatility_regime", "unknown")
            if chip_facts
            else "unknown"
        ),
    )
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
        cost_vs_20d_high=cost_vs_high,
        high_20d=high_20d,
        low_20d=low_20d,
        technical_stop_price=trade_levels["technical_stop_price"],  # type: ignore[arg-type]
        technical_target_price=trade_levels["technical_target_price"],  # type: ignore[arg-type]
        stop_loss_hint=str(trade_levels["stop_loss_hint"]),
        take_profit_hint=str(trade_levels["take_profit_hint"]),
        breakeven_move_pct=round(breakeven_move, 2) if breakeven_move is not None else None,
        position_bias=bias,
        uses_margin=bool(uses_margin),
        required_action_hint=_required_action_hint(bucket, uses_margin=bool(uses_margin)),
    )
    if chip_facts is not None:
        facts.scenario_plan = build_scenario_plan(facts, chip_facts, base_rates)
    facts.anchors = _build_anchors(facts)
    return facts


def _build_anchors(facts: PositionFacts) -> list[str]:
    anchors: list[str] = []
    if facts.uses_margin:
        anchors.append("此部位使用融資（須留意追繳／斷頭與減碼防禦）")
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
    if facts.cost_vs_20d_high == "above" and facts.high_20d is not None:
        anchors.append(
            f"持股均價高於近20日高 {_fmt_price(facts.high_20d)}"
            "（解套須先過前高壓力）"
        )
    elif facts.cost_vs_20d_high == "below" and facts.high_20d is not None:
        anchors.append(
            f"持股均價低於近20日高 {_fmt_price(facts.high_20d)}"
            "（前高仍有參考壓力/停利區）"
        )
    if facts.stop_loss_hint:
        anchors.append(facts.stop_loss_hint)
    if facts.take_profit_hint:
        anchors.append(facts.take_profit_hint)
    anchors.append(POSITION_BIAS_LABEL[facts.position_bias])

    if facts.scenario_plan:
        primary = next(
            (s for s in facts.scenario_plan.scenarios if s.is_primary),
            facts.scenario_plan.scenarios[0],
        )
        anchors.append(
            f"操作主線：{primary.label}（{primary.weight_pct}%）"
            f"→ {primary.action}"
        )
    return anchors


def scenario_plan_summary_for_prompt(plan: ScenarioPlan) -> str:
    lines = [
        "",
        "【操作情境權重（系統判定，百分比勿修改）】",
    ]
    rank_labels = ("主線", "次線", "尾線")
    for index, item in enumerate(plan.scenarios):
        rank = rank_labels[index] if index < len(rank_labels) else f"情境{index + 1}"
        lines.append(
            f"- {rank}：{item.label}（{item.weight_pct}%）"
            f"→ 建議操作：{item.action}"
        )
        lines.append(f"  觸發參考：{item.trigger_hint}")
    lines.append("- 正文須依上述權重撰寫三種市場情境，並標示百分比與主線")
    return "\n".join(lines)


def position_facts_summary_for_prompt(facts: PositionFacts) -> str:
    lines = [
        "【部位狀態（系統試算，操作情境須對齊此狀態）】",
    ]
    lines.append(
        f"- 是否使用融資：{'是（融資部位）' if facts.uses_margin else '否（現股）'}"
    )
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
    if facts.high_20d is not None and facts.low_20d is not None:
        lines.append(
            f"- 近20日區間：低 {_fmt_price(facts.low_20d)} / 高 {_fmt_price(facts.high_20d)}"
        )
    if facts.cost_vs_20d_high != "unknown" and facts.high_20d is not None:
        pos = {"above": "高於", "below": "低於", "at": "貼近"}[facts.cost_vs_20d_high]
        lines.append(f"- 持股均價{pos}近20日高")
    if facts.stop_loss_hint:
        lines.append(f"- {facts.stop_loss_hint}")
    if facts.take_profit_hint:
        lines.append(f"- {facts.take_profit_hint}")
    lines.append(f"- 系統傾向：{POSITION_BIAS_LABEL[facts.position_bias]}")
    if facts.required_action_hint:
        lines.append(f"- 操作情境至少須明確提及：{facts.required_action_hint}")
    if facts.scenario_plan:
        lines.append(scenario_plan_summary_for_prompt(facts.scenario_plan))
    return "\n".join(lines)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _price_mentioned(text: str, price: float | None) -> bool:
    if price is None or price <= 0:
        return False
    normalized = text.replace(",", "")
    candidates = {
        f"{price:.2f}",
        f"{price:.1f}",
        str(int(round(price))),
        _fmt_price(price),
    }
    return any(candidate and candidate in normalized for candidate in candidates)


def _mentions_stop_level(text: str, facts: PositionFacts) -> bool:
    if _price_mentioned(text, facts.technical_stop_price):
        return True
    return _contains_any(text, STOP_LEVEL_KEYWORDS)


def _mentions_target_level(text: str, facts: PositionFacts) -> bool:
    if _price_mentioned(text, facts.technical_target_price):
        return True
    if facts.pnl_bucket in {"loss_small", "loss_large", "breakeven"}:
        if _price_mentioned(text, facts.avg_cost):
            return True
    return _contains_any(text, TARGET_LEVEL_KEYWORDS)


def _primary_action_forbids_add(primary_action: str) -> bool:
    """True when primary scenario action emphasizes adding (not 不加碼)."""
    first = primary_action.split("/")[0].strip()
    if first.startswith("不加碼"):
        return False
    return "加碼" in first


def _check_scenario_plan(region: str, facts: PositionFacts) -> list[FactIssue]:
    plan = facts.scenario_plan
    if plan is None:
        return []

    issues: list[FactIssue] = []
    for item in plan.scenarios:
        if item.label not in region:
            issues.append(
                (
                    "position_scenario_label_missing",
                    f"操作情境須包含市場情境「{item.label}」",
                )
            )
        weight_token = f"{item.weight_pct}%"
        if weight_token not in region:
            issues.append(
                (
                    "position_scenario_weight_missing",
                    f"操作情境須標示「{item.label}（{weight_token}）」",
                )
            )

    primary = next((s for s in plan.scenarios if s.is_primary), plan.scenarios[0])
    if "主線" not in region:
        issues.append(
            (
                "position_scenario_primary_unmarked",
                f"操作情境須標示主線（例如：{primary.label}（{primary.weight_pct}%，主線））",
            )
        )

    if facts.position_bias in {"defensive", "cautious"}:
        if _primary_action_forbids_add(primary.action):
            issues.append(
                (
                    "position_scenario_primary_add_forbidden",
                    f"部位偏防禦時，主線「{primary.label}」不可以加碼為主",
                )
            )
        primary_line = ""
        for line in region.splitlines():
            if primary.label in line and (
                "主線" in line or f"{primary.weight_pct}%" in line
            ):
                primary_line = line
                break
        if primary_line and re.search(r"加碼", primary_line) and not re.search(
            r"不加碼", primary_line
        ):
            issues.append(
                (
                    "position_scenario_primary_add_forbidden",
                    f"主線情境「{primary.label}」在防禦傾向下不可描述為加碼",
                )
            )

    return issues


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
        elif facts.technical_target_price is not None and not _mentions_target_level(
            region, facts
        ):
            issues.append(
                (
                    "position_target_level_missing",
                    f"系統停利參考為近20日高 {_fmt_price(facts.technical_target_price)}，"
                    "操作情境須引用該價位或近20日高/壓力區",
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
        elif facts.technical_stop_price is not None and not _mentions_stop_level(
            region, facts
        ):
            issues.append(
                (
                    "position_stop_level_missing",
                    f"系統停損參考為近20日低 {_fmt_price(facts.technical_stop_price)}，"
                    "操作情境須引用該價位或近20日低/前低",
                )
            )
        elif facts.take_profit_hint and not _mentions_target_level(region, facts):
            issues.append(
                (
                    "position_target_level_missing",
                    f"虧損部位須提及解套參考（均價 {_fmt_price(facts.avg_cost)}）"
                    "或上方壓力區",
                )
            )

    issues.extend(_check_scenario_plan(region, facts))

    if facts.uses_margin:
        risk_region = _slice_after_keywords(text, _RISK_SECTION_KEYWORDS)
        margin_scope = (
            f"{risk_region}\n{region}" if risk_region.strip() else text
        )
        has_explicit_margin_risk = _contains_any(margin_scope, MARGIN_RISK_KEYWORDS)
        has_margin_and_defense = ("融資" in margin_scope or "融资" in margin_scope) and (
            _contains_any(margin_scope, ("減碼", "停損", "出場", "認賠"))
        )
        if not (has_explicit_margin_risk or has_margin_and_defense):
            issues.append(
                (
                    "position_margin_no_risk",
                    "此部位標示為融資，操作情境或風險提醒須談追繳／斷頭／維持率，"
                    "或明確提出融資減碼／停損防禦",
                )
            )

    return issues


def write_position_facts_json(path, facts: PositionFacts) -> None:
    from pathlib import Path

    Path(path).write_text(
        json.dumps(asdict(facts), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
