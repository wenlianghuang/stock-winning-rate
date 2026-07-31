"""Deterministic position facts computed from holding cost + CSV close/MA20.

Mirrors ``chip_signals`` but for the *position* dimension: Python owns the
verdicts (profit/loss bucket, distance to breakeven, cost vs MA20, suggested
bias, scenario weights, margin maintenance / call distance), so the
operation-scenario narrative can be mechanically gated on the actual holding —
not just generic boilerplate.
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

# 融資單檔維持率估算（非券商整戶）。成數／追繳線可覆寫。
DEFAULT_FINANCING_RATIO = 0.6
DEFAULT_MARGIN_CALL_THRESHOLD_PCT = 130.0
# 距追繳（維持率百分點）：>40 safe / >20 watch / >5 tight / else critical
MARGIN_PRESSURE_SAFE_PP = 40.0
MARGIN_PRESSURE_WATCH_PP = 20.0
MARGIN_PRESSURE_TIGHT_PP = 5.0

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
# 融資壓力收緊時，須出現追繳距離／追繳價等錨點之一
CALL_DISTANCE_KEYWORDS = (
    "距追繳",
    "追繳價",
    "追繳線",
    "接近追繳",
    "低於追繳",
    "瀕臨追繳",
    "逼近追繳",
    "觸及追繳",
)
MAINT_RATE_TOLERANCE_PP = 3.0
CALL_DISTANCE_TOLERANCE_PP = 5.0
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

MARGIN_PRESSURE_LABEL = {
    "safe": "融資壓力寬鬆",
    "watch": "融資壓力需盯",
    "tight": "融資壓力收緊",
    "critical": "融資接近追繳／已低於追繳線",
    "unknown": "融資維持率無法試算",
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

PRIORITY_LABEL = {
    "cash_only": "僅現股部位",
    "margin_only": "僅融資部位",
    "margin_first": "優先處理融資腿（風險／虧損較急）",
    "cash_first": "優先處理現股腿（權重較大）",
    "balanced": "現股與融資並重，綜合調節",
}

_SYNTHESIS_KEYWORDS = (
    "綜合",
    "優先",
    "兩邊",
    "現股與融資",
    "融資與現股",
    "現股／融資",
    "現股/融資",
    "兩腿",
)
_CASH_SECTION_KEYWORDS = ("現股",)
_MARGIN_SECTION_KEYWORDS = ("融資", "融资")


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
    leg: str = "combined"  # cash | margin | combined
    # 融資單檔維持率估算（僅融資腿／含融資的綜合副本；現股為 unknown）
    financing_ratio: float | None = None
    margin_call_threshold_pct: float | None = None
    maintenance_rate_pct: float | None = None
    distance_to_call_pp: float | None = None  # 維持率 − 追繳線（百分點）
    margin_call_price: float | None = None
    distance_to_call_price_pct: float | None = None  # (close−call)/close×100
    margin_pressure_zone: str = "unknown"  # safe|watch|tight|critical|unknown
    scenario_plan: ScenarioPlan | None = None
    anchors: list[str] = field(default_factory=list)

    @property
    def margin_pressure_label(self) -> str:
        return MARGIN_PRESSURE_LABEL.get(
            self.margin_pressure_zone, MARGIN_PRESSURE_LABEL["unknown"]
        )


@dataclass
class DualPositionBundle:
    combined: PositionFacts
    cash: PositionFacts | None = None
    margin: PositionFacts | None = None
    priority: str = "balanced"
    priority_label: str = ""
    synthesis_hint: str = ""

    @property
    def uses_margin(self) -> bool:
        return self.margin is not None and self.margin.shares > 0

    @property
    def unrealized_pnl_pct(self) -> float | None:
        return self.combined.unrealized_pnl_pct

    @property
    def pnl_bucket(self) -> str:
        return self.combined.pnl_bucket

    @property
    def position_bias(self) -> str:
        return self.combined.position_bias

    @property
    def avg_cost(self) -> float:
        return self.combined.avg_cost

    @property
    def shares(self) -> int:
        return self.combined.shares

    @property
    def scenario_plan(self) -> ScenarioPlan | None:
        return self.combined.scenario_plan

    @property
    def anchors(self) -> list[str]:
        return self.combined.anchors

    @property
    def stock_id(self) -> str:
        return self.combined.stock_id

    @property
    def stock_name(self) -> str:
        return self.combined.stock_name


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


def _margin_pressure_zone(distance_to_call_pp: float | None) -> str:
    """Bucket personal margin pressure from distance-to-call (percentage points)."""
    if distance_to_call_pp is None:
        return "unknown"
    if distance_to_call_pp > MARGIN_PRESSURE_SAFE_PP:
        return "safe"
    if distance_to_call_pp > MARGIN_PRESSURE_WATCH_PP:
        return "watch"
    if distance_to_call_pp > MARGIN_PRESSURE_TIGHT_PP:
        return "tight"
    return "critical"


def compute_margin_maintenance(
    *,
    avg_cost: float,
    close_price: float | None,
    financing_ratio: float = DEFAULT_FINANCING_RATIO,
    call_threshold_pct: float = DEFAULT_MARGIN_CALL_THRESHOLD_PCT,
) -> dict[str, float | str | None]:
    """Single-name TW margin estimate (not whole-account 整戶維持率).

    維持率 ≈ close / (avg_cost × financing_ratio) × 100
    追繳價 ≈ avg_cost × financing_ratio × (call_threshold / 100)
    """
    empty: dict[str, float | str | None] = {
        "financing_ratio": None,
        "margin_call_threshold_pct": None,
        "maintenance_rate_pct": None,
        "distance_to_call_pp": None,
        "margin_call_price": None,
        "distance_to_call_price_pct": None,
        "margin_pressure_zone": "unknown",
    }
    if (
        close_price is None
        or close_price <= 0
        or avg_cost <= 0
        or financing_ratio <= 0
        or call_threshold_pct <= 0
    ):
        return empty

    loan_per_share = avg_cost * financing_ratio
    maintenance_rate_pct = close_price / loan_per_share * 100.0
    distance_to_call_pp = maintenance_rate_pct - call_threshold_pct
    margin_call_price = loan_per_share * (call_threshold_pct / 100.0)
    distance_to_call_price_pct = (close_price - margin_call_price) / close_price * 100.0
    zone = _margin_pressure_zone(distance_to_call_pp)
    return {
        "financing_ratio": float(financing_ratio),
        "margin_call_threshold_pct": float(call_threshold_pct),
        "maintenance_rate_pct": round(maintenance_rate_pct, 2),
        "distance_to_call_pp": round(distance_to_call_pp, 2),
        "margin_call_price": round(margin_call_price, 2),
        "distance_to_call_price_pct": round(distance_to_call_price_pct, 2),
        "margin_pressure_zone": zone,
    }


def _apply_margin_pressure_bias(bias: str, zone: str) -> str:
    """Tighten position bias when personal margin pressure rises."""
    if zone == "critical":
        return "defensive"
    if zone == "tight":
        if bias in {"protect_gains", "neutral", "unknown"}:
            return "cautious"
        return bias
    return bias


def _copy_margin_maintenance(target: PositionFacts, source: PositionFacts) -> None:
    """Attach margin-leg maintenance metrics onto combined (for plan / prompt)."""
    target.financing_ratio = source.financing_ratio
    target.margin_call_threshold_pct = source.margin_call_threshold_pct
    target.maintenance_rate_pct = source.maintenance_rate_pct
    target.distance_to_call_pp = source.distance_to_call_pp
    target.margin_call_price = source.margin_call_price
    target.distance_to_call_price_pct = source.distance_to_call_price_pct
    target.margin_pressure_zone = source.margin_pressure_zone


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

    # 個人融資壓力（與市場面 margin_momentum / 券資比分開）
    pressure = getattr(position_facts, "margin_pressure_zone", "unknown")
    if getattr(position_facts, "uses_margin", False) and pressure != "unknown":
        if pressure == "watch":
            scores["continuation"] += 4
            scores["rebound"] -= 2
        elif pressure == "tight":
            scores["continuation"] += 8
            scores["rebound"] -= 5
        elif pressure == "critical":
            scores["continuation"] += 12
            scores["rebound"] -= 8
        # 市場融資升溫 + 個人壓力高 → 再收緊
        if pressure in {"tight", "critical"} and margin_momentum == "heating":
            scores["continuation"] += 3
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
    leg: str = "combined",
    with_scenario_plan: bool = True,
    financing_ratio: float = DEFAULT_FINANCING_RATIO,
    call_threshold_pct: float = DEFAULT_MARGIN_CALL_THRESHOLD_PCT,
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

    margin_metrics: dict[str, float | str | None] = {
        "financing_ratio": None,
        "margin_call_threshold_pct": None,
        "maintenance_rate_pct": None,
        "distance_to_call_pp": None,
        "margin_call_price": None,
        "distance_to_call_price_pct": None,
        "margin_pressure_zone": "unknown",
    }
    if uses_margin:
        margin_metrics = compute_margin_maintenance(
            avg_cost=avg_cost,
            close_price=close_price,
            financing_ratio=financing_ratio,
            call_threshold_pct=call_threshold_pct,
        )
        bias = _apply_margin_pressure_bias(
            bias, str(margin_metrics["margin_pressure_zone"])
        )

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
        leg=leg,
        financing_ratio=margin_metrics["financing_ratio"],  # type: ignore[arg-type]
        margin_call_threshold_pct=margin_metrics["margin_call_threshold_pct"],  # type: ignore[arg-type]
        maintenance_rate_pct=margin_metrics["maintenance_rate_pct"],  # type: ignore[arg-type]
        distance_to_call_pp=margin_metrics["distance_to_call_pp"],  # type: ignore[arg-type]
        margin_call_price=margin_metrics["margin_call_price"],  # type: ignore[arg-type]
        distance_to_call_price_pct=margin_metrics["distance_to_call_price_pct"],  # type: ignore[arg-type]
        margin_pressure_zone=str(margin_metrics["margin_pressure_zone"]),
        required_action_hint=_required_action_hint(bucket, uses_margin=bool(uses_margin)),
    )
    if with_scenario_plan and chip_facts is not None:
        facts.scenario_plan = build_scenario_plan(facts, chip_facts, base_rates)
    facts.anchors = _build_anchors(facts)
    return facts


def _decide_priority(
    cash: PositionFacts | None,
    margin: PositionFacts | None,
) -> tuple[str, str]:
    if cash is None and margin is None:
        return "balanced", PRIORITY_LABEL["balanced"]
    if cash is None:
        return "margin_only", PRIORITY_LABEL["margin_only"]
    if margin is None:
        return "cash_only", PRIORITY_LABEL["cash_only"]

    margin_loss = margin.pnl_bucket in {"loss_small", "loss_large"}
    cash_profit = cash.pnl_bucket in {"profit_small", "profit_large"}
    if margin_loss or margin.position_bias in {"defensive", "cautious"}:
        return "margin_first", PRIORITY_LABEL["margin_first"]
    if cash_profit and margin.pnl_bucket == "breakeven":
        return "cash_first", PRIORITY_LABEL["cash_first"]
    if cash.shares >= margin.shares * 2 and not margin_loss:
        return "cash_first", PRIORITY_LABEL["cash_first"]
    if margin.shares >= cash.shares * 2:
        return "margin_first", PRIORITY_LABEL["margin_first"]
    return "balanced", PRIORITY_LABEL["balanced"]


def _synthesis_hint(
    priority: str,
    cash: PositionFacts | None,
    margin: PositionFacts | None,
) -> str:
    if priority == "cash_only" and cash is not None:
        return f"僅現股：依現股損益（{PNL_BUCKET_LABEL[cash.pnl_bucket]}）操作"
    if priority == "margin_only" and margin is not None:
        pressure = ""
        if margin.margin_pressure_zone not in {"unknown", "safe"}:
            pressure = f"；{margin.margin_pressure_label}"
        return (
            f"僅融資：依融資損益（{PNL_BUCKET_LABEL[margin.pnl_bucket]}）操作，"
            f"並嚴控追繳／減碼{pressure}"
        )
    cash_note = (
        f"現股 {cash.shares:,} 股／{PNL_BUCKET_LABEL[cash.pnl_bucket]}"
        if cash is not None
        else "無現股"
    )
    margin_note = (
        f"融資 {margin.shares:,} 股／{PNL_BUCKET_LABEL[margin.pnl_bucket]}"
        if margin is not None
        else "無融資"
    )
    return (
        f"{PRIORITY_LABEL[priority]}。"
        f"{cash_note}；{margin_note}。"
        "綜合結論須說明優先處理哪一腿與理由"
    )


def build_dual_position_facts(
    row: dict,
    *,
    stock_id: str,
    stock_name: str,
    cash_shares: int = 0,
    cash_avg_cost: float | None = None,
    margin_shares: int = 0,
    margin_avg_cost: float | None = None,
    combined_shares: int | None = None,
    combined_avg_cost: float | None = None,
    chip_facts=None,
    base_rates: dict | None = None,
    financing_ratio: float = DEFAULT_FINANCING_RATIO,
    call_threshold_pct: float = DEFAULT_MARGIN_CALL_THRESHOLD_PCT,
) -> DualPositionBundle:
    cash: PositionFacts | None = None
    margin: PositionFacts | None = None

    if cash_shares > 0 and cash_avg_cost is not None and cash_avg_cost > 0:
        cash = build_position_facts(
            row,
            stock_id=stock_id,
            stock_name=stock_name,
            avg_cost=float(cash_avg_cost),
            shares=int(cash_shares),
            chip_facts=chip_facts,
            base_rates=base_rates,
            uses_margin=False,
            leg="cash",
            with_scenario_plan=False,
        )

    if margin_shares > 0 and margin_avg_cost is not None and margin_avg_cost > 0:
        margin = build_position_facts(
            row,
            stock_id=stock_id,
            stock_name=stock_name,
            avg_cost=float(margin_avg_cost),
            shares=int(margin_shares),
            chip_facts=chip_facts,
            base_rates=base_rates,
            uses_margin=True,
            leg="margin",
            with_scenario_plan=False,
            financing_ratio=financing_ratio,
            call_threshold_pct=call_threshold_pct,
        )

    if combined_shares is None or combined_avg_cost is None:
        total = (cash_shares or 0) + (margin_shares or 0)
        if total <= 0:
            raise ValueError("現股與融資股數合計須 > 0")
        cash_value = float(cash_shares or 0) * float(cash_avg_cost or 0)
        margin_value = float(margin_shares or 0) * float(margin_avg_cost or 0)
        combined_shares = total
        combined_avg_cost = (cash_value + margin_value) / total

    combined = build_position_facts(
        row,
        stock_id=stock_id,
        stock_name=stock_name,
        avg_cost=float(combined_avg_cost),
        shares=int(combined_shares),
        chip_facts=chip_facts,
        base_rates=base_rates,
        # 綜合腿損益用加權均價；維持率不可用加權均價估算
        uses_margin=False,
        leg="combined",
        with_scenario_plan=False,
    )
    if margin is not None:
        combined.uses_margin = True
        combined.required_action_hint = _required_action_hint(
            combined.pnl_bucket, uses_margin=True
        )
        _copy_margin_maintenance(combined, margin)
        combined.position_bias = _apply_margin_pressure_bias(
            combined.position_bias, margin.margin_pressure_zone
        )
    if margin is not None and margin.position_bias == "defensive":
        combined.position_bias = "defensive"
    elif margin is not None and margin.position_bias == "cautious":
        if combined.position_bias in {"neutral", "protect_gains"}:
            combined.position_bias = "cautious"
    if chip_facts is not None:
        combined.scenario_plan = build_scenario_plan(combined, chip_facts, base_rates)
    combined.anchors = _build_anchors(combined)

    priority, priority_label = _decide_priority(cash, margin)
    synthesis = _synthesis_hint(priority, cash, margin)
    combined.anchors = [
        f"綜合優先序：{priority_label}",
        *combined.anchors,
    ]

    return DualPositionBundle(
        combined=combined,
        cash=cash,
        margin=margin,
        priority=priority,
        priority_label=priority_label,
        synthesis_hint=synthesis,
    )


def dual_position_facts_summary_for_prompt(bundle: DualPositionBundle) -> str:
    lines = [
        "【部位狀態（現股／融資分腿 + 綜合；操作情境須對齊）】",
        f"- 綜合優先序：{bundle.priority_label}",
        f"- 綜合提示：{bundle.synthesis_hint}",
        "",
        "【綜合部位（加權均價，情境權重以此為準）】",
    ]
    combined_summary = position_facts_summary_for_prompt(bundle.combined)
    lines.append(
        combined_summary.replace(
            "【部位狀態（系統試算，操作情境須對齊此狀態）】\n",
            "",
        )
    )

    if bundle.cash is not None:
        lines.extend(["", "【現股腿】"])
        cash_summary = position_facts_summary_for_prompt(bundle.cash)
        lines.append(
            cash_summary.replace(
                "【部位狀態（系統試算，操作情境須對齊此狀態）】\n",
                "",
            )
        )
    if bundle.margin is not None:
        lines.extend(["", "【融資腿】"])
        margin_summary = position_facts_summary_for_prompt(bundle.margin)
        lines.append(
            margin_summary.replace(
                "【部位狀態（系統試算，操作情境須對齊此狀態）】\n",
                "",
            )
        )
    return "\n".join(lines)


def _build_anchors(facts: PositionFacts) -> list[str]:
    anchors: list[str] = []
    if facts.uses_margin:
        anchors.append("此部位使用融資（須留意追繳／斷頭與減碼防禦）")
        if facts.maintenance_rate_pct is not None and facts.distance_to_call_pp is not None:
            anchors.append(
                f"融資維持率約 {facts.maintenance_rate_pct:.1f}%"
                f"（距追繳 {facts.distance_to_call_pp:+.1f}pp；"
                f"{facts.margin_pressure_label}；單檔估算非整戶）"
            )
            if facts.margin_call_price is not None:
                anchors.append(
                    f"估算追繳價約 {_fmt_price(facts.margin_call_price)}"
                    + (
                        f"（現價相對追繳價約 {facts.distance_to_call_price_pct:+.1f}%）"
                        if facts.distance_to_call_price_pct is not None
                        else ""
                    )
                )
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
    lines.append(
        "- 正文須依上述權重撰寫三種市場情境；標題格式為 "
        "`### 主線：…（%）`／`### 次線：…`／`### 尾線：…`，並標示百分比"
    )
    return "\n".join(lines)


def position_facts_summary_for_prompt(facts: PositionFacts) -> str:
    lines = [
        "【部位狀態（系統試算，操作情境須對齊此狀態）】",
    ]
    lines.append(
        f"- 是否使用融資：{'是（融資部位）' if facts.uses_margin else '否（現股）'}"
    )
    if (
        facts.uses_margin
        and facts.maintenance_rate_pct is not None
        and facts.distance_to_call_pp is not None
    ):
        ratio = facts.financing_ratio if facts.financing_ratio is not None else DEFAULT_FINANCING_RATIO
        threshold = (
            facts.margin_call_threshold_pct
            if facts.margin_call_threshold_pct is not None
            else DEFAULT_MARGIN_CALL_THRESHOLD_PCT
        )
        lines.append(
            f"- 融資維持率（單檔估算）：約 {facts.maintenance_rate_pct:.1f}%"
            f"（成數 {ratio:.0%}、追繳線 {threshold:.0f}%）"
        )
        lines.append(
            f"- 距追繳：維持率空間 {facts.distance_to_call_pp:+.1f}pp"
            f"（{facts.margin_pressure_label}）"
        )
        if facts.margin_call_price is not None:
            price_gap = (
                f"，現價相對追繳價約 {facts.distance_to_call_price_pct:+.1f}%"
                if facts.distance_to_call_price_pct is not None
                else ""
            )
            lines.append(
                f"- 估算追繳價：約 {_fmt_price(facts.margin_call_price)}{price_gap}"
            )
        lines.append(
            "- 註：以上為單檔簡化估算，非券商整戶維持率；敘事可引用數字但勿改寫"
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


def _margin_call_price_mentioned(text: str, price: float | None) -> bool:
    """Stricter than ``_price_mentioned``: avoid colliding with 20日高/低整數價."""
    if price is None or price <= 0:
        return False
    normalized = text.replace(",", "")
    precise = {f"{price:.2f}", f"{price:.1f}", _fmt_price(price)}
    if any(candidate and candidate in normalized for candidate in precise):
        return True
    # 整數價僅在「追繳」鄰近上下文才算命中
    rounded = str(int(round(price)))
    if rounded not in normalized:
        return False
    for match in re.finditer(re.escape(rounded), normalized):
        start = max(0, match.start() - 12)
        end = min(len(normalized), match.end() + 12)
        window = normalized[start:end]
        if "追繳" in window or "斷頭" in window:
            return True
    return False


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


def _run_bucket_checks(region: str, facts: PositionFacts) -> list[FactIssue]:
    """Bucket-aware checks on a text region for one leg."""
    if facts.unrealized_pnl_pct is None:
        return []

    issues: list[FactIssue] = []
    bucket = facts.pnl_bucket
    leg_prefix = {
        "cash": "現股",
        "margin": "融資",
        "combined": "部位",
    }.get(facts.leg, "部位")

    if not _contains_any(region, POSITION_CONTEXT_KEYWORDS):
        issues.append(
            (
                "position_scenario_unanchored",
                f"{leg_prefix}操作情境未錨定部位損益（須提及獲利/虧損/成本/均價/套牢等）",
            )
        )

    if bucket == "profit_large":
        if not _contains_any(region, PROFIT_PROTECT_KEYWORDS):
            issues.append(
                (
                    "position_profit_no_protection",
                    f"{leg_prefix}已大幅獲利（{facts.unrealized_pnl_pct:+.1f}%），"
                    "須提出停利/移動停損/獲利了結，或明確論證續抱理由",
                )
            )
        elif facts.technical_target_price is not None and not _mentions_target_level(
            region, facts
        ):
            issues.append(
                (
                    "position_target_level_missing",
                    f"{leg_prefix}系統停利參考為近20日高 {_fmt_price(facts.technical_target_price)}，"
                    "須引用該價位或近20日高/壓力區",
                )
            )
    elif bucket == "profit_small":
        if not _contains_any(region, PROFIT_SMALL_KEYWORDS):
            issues.append(
                (
                    "position_profit_no_plan",
                    f"{leg_prefix}小幅獲利（{facts.unrealized_pnl_pct:+.1f}%），"
                    "須討論加碼條件/停利/續抱或獲利回吐風險",
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
                    f"{leg_prefix}接近損益兩平，須給出明確的出場或加碼觸發條件",
                )
            )
    elif bucket in {"loss_small", "loss_large"}:
        if not _contains_any(region, RISK_CONTROL_KEYWORDS):
            issues.append(
                (
                    "position_loss_no_risk_control",
                    f"{leg_prefix}未實現損益約 {facts.unrealized_pnl_pct:.1f}%（虧損），"
                    "須提出停損/減碼/出場等具體防禦手段",
                )
            )
        elif facts.technical_stop_price is not None and not _mentions_stop_level(
            region, facts
        ):
            issues.append(
                (
                    "position_stop_level_missing",
                    f"{leg_prefix}系統停損參考為近20日低 {_fmt_price(facts.technical_stop_price)}，"
                    "須引用該價位或近20日低/前低",
                )
            )
        elif facts.take_profit_hint and not _mentions_target_level(region, facts):
            issues.append(
                (
                    "position_target_level_missing",
                    f"{leg_prefix}虧損須提及解套參考（均價 {_fmt_price(facts.avg_cost)}）"
                    "或上方壓力區",
                )
            )
    return issues


def _margin_risk_issues(text: str, region: str) -> list[FactIssue]:
    risk_region = _slice_after_keywords(text, _RISK_SECTION_KEYWORDS)
    margin_scope = f"{risk_region}\n{region}" if risk_region.strip() else text
    has_explicit_margin_risk = _contains_any(margin_scope, MARGIN_RISK_KEYWORDS)
    has_margin_and_defense = ("融資" in margin_scope or "融资" in margin_scope) and (
        _contains_any(margin_scope, ("減碼", "停損", "出場", "認賠"))
    )
    if has_explicit_margin_risk or has_margin_and_defense:
        return []
    return [
        (
            "position_margin_no_risk",
            "融資腿存在時，操作情境或風險提醒須談追繳／斷頭／維持率，"
            "或明確提出融資減碼／停損防禦",
        )
    ]


def _extract_maint_rate_claims(text: str) -> list[float]:
    """Numbers explicitly tied to 維持率 (avoid random % elsewhere)."""
    patterns = (
        r"維持率[^%\d]{0,20}约?\s*約?\s*(\d{2,3}(?:\.\d+)?)\s*%",
        r"维持率[^%\d]{0,20}约?\s*約?\s*(\d{2,3}(?:\.\d+)?)\s*%",
        r"維持率[^%\d]{0,20}约?\s*約?\s*(\d{2,3}(?:\.\d+)?)",
        r"维持率[^%\d]{0,20}约?\s*約?\s*(\d{2,3}(?:\.\d+)?)",
    )
    found: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            # 維持率合理區間；排除成數 60 這類誤抓時仍可能進來，靠容差比對
            if 50.0 <= value <= 500.0:
                found.append(value)
    return found


def _extract_call_distance_pp_claims(text: str) -> list[float]:
    """Extract 維持率空間（pp），勿與「現價相對追繳價 %」混淆。"""
    # 先遮罩價格距離用語，避免「現價距追繳約 +20.4%」被當成 pp
    masked = re.sub(
        r"現價(?:距追繳|相對追繳價)[^%\n]{0,30}[+\-]?\d+(?:\.\d+)?\s*%",
        " ",
        text,
    )
    patterns = (
        # 必須帶 pp／百分點；禁止把 % 當成 pp
        r"(?<!現價)距追繳[^%\d+\-]{0,24}([+\-]?\d+(?:\.\d+)?)\s*(?:pp|PP|百分點)",
        r"維持率空間[^%\d+\-]{0,16}([+\-]?\d+(?:\.\d+)?)\s*(?:pp|PP|百分點)?",
    )
    found: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, masked):
            try:
                found.append(float(match.group(1)))
            except ValueError:
                continue
    return found


def _extract_call_price_gap_claims(text: str) -> list[float]:
    """Extract 現價相對追繳價（%）。"""
    patterns = (
        r"現價距追繳[^%\d+\-]{0,20}([+\-]?\d+(?:\.\d+)?)\s*%",
        r"現價相對追繳價[^%\d+\-]{0,20}([+\-]?\d+(?:\.\d+)?)\s*%",
    )
    found: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            try:
                found.append(float(match.group(1)))
            except ValueError:
                continue
    return found


def _margin_maintenance_issues(
    text: str, region: str, facts: PositionFacts
) -> list[FactIssue]:
    """Numeric / pressure-zone gates on top of keyword margin risk."""
    if not facts.uses_margin or facts.margin_pressure_zone == "unknown":
        return []
    if facts.maintenance_rate_pct is None:
        return []

    risk_region = _slice_after_keywords(text, _RISK_SECTION_KEYWORDS)
    scope = f"{risk_region}\n{region}" if risk_region.strip() else text
    if not scope.strip():
        scope = text
    issues: list[FactIssue] = []
    expected_rate = float(facts.maintenance_rate_pct)
    zone = facts.margin_pressure_zone

    rate_claims = _extract_maint_rate_claims(scope)
    if rate_claims and not any(
        abs(claimed - expected_rate) <= MAINT_RATE_TOLERANCE_PP for claimed in rate_claims
    ):
        claimed = rate_claims[0]
        issues.append(
            (
                "position_maint_rate_mismatch",
                f"正文維持率約 {claimed:g}% 與系統試算 {expected_rate:.1f}% "
                f"相差超過 ±{MAINT_RATE_TOLERANCE_PP:.0f}pp，請改用系統數字"
                "（單檔估算）",
            )
        )

    if facts.distance_to_call_pp is not None:
        expected_dist = float(facts.distance_to_call_pp)
        dist_claims = _extract_call_distance_pp_claims(scope)
        if dist_claims and not any(
            abs(claimed - expected_dist) <= CALL_DISTANCE_TOLERANCE_PP
            for claimed in dist_claims
        ):
            claimed = dist_claims[0]
            issues.append(
                (
                    "position_call_distance_mismatch",
                    f"正文距追繳約 {claimed:g}pp 與系統試算 {expected_dist:+.1f}pp "
                    f"相差超過 ±{CALL_DISTANCE_TOLERANCE_PP:.0f}pp，請改用系統數字",
                )
            )

    if facts.distance_to_call_price_pct is not None:
        expected_gap = float(facts.distance_to_call_price_pct)
        gap_claims = _extract_call_price_gap_claims(scope)
        if gap_claims and not any(
            abs(claimed - expected_gap) <= CALL_DISTANCE_TOLERANCE_PP
            for claimed in gap_claims
        ):
            claimed = gap_claims[0]
            issues.append(
                (
                    "position_call_distance_mismatch",
                    f"正文「現價相對追繳價」約 {claimed:g}% 與系統試算 "
                    f"{expected_gap:+.1f}% 相差超過 ±{CALL_DISTANCE_TOLERANCE_PP:.0f}%，"
                    "請改用系統數字（勿與維持率空間 pp 混淆）",
                )
            )

    if zone in {"tight", "critical"}:
        has_call_anchor = (
            _contains_any(scope, CALL_DISTANCE_KEYWORDS)
            or _margin_call_price_mentioned(scope, facts.margin_call_price)
            or any(
                abs(c - expected_rate) <= MAINT_RATE_TOLERANCE_PP
                for c in _extract_maint_rate_claims(scope)
            )
            or (
                facts.distance_to_call_pp is not None
                and any(
                    abs(c - float(facts.distance_to_call_pp)) <= CALL_DISTANCE_TOLERANCE_PP
                    for c in _extract_call_distance_pp_claims(scope)
                )
            )
        )
        if not has_call_anchor:
            call_price_note = (
                f"或追繳價 {_fmt_price(facts.margin_call_price)}"
                if facts.margin_call_price is not None
                else ""
            )
            dist_note = (
                f"距追繳 {facts.distance_to_call_pp:+.1f}pp"
                if facts.distance_to_call_pp is not None
                else "距追繳空間"
            )
            issues.append(
                (
                    "position_call_distance_ignored",
                    f"融資壓力為「{facts.margin_pressure_label}」，"
                    f"須點出追繳線／{dist_note}{call_price_note}，"
                    f"或引用系統維持率 {expected_rate:.1f}%",
                )
            )

    if zone == "critical":
        plan = facts.scenario_plan
        primary_id = plan.primary_id if plan is not None else None
        rebound_as_main = bool(
            re.search(
                r"(主線[:：].{0,12}技術反彈|技術反彈.{0,12}（?主線|主線.{0,20}技術反彈)",
                scope,
            )
        )
        if rebound_as_main and primary_id != "rebound":
            issues.append(
                (
                    "position_margin_pressure_unanchored",
                    "融資接近追繳時不可把「技術反彈」標成主線；"
                    "請對齊系統主線（多為延續調節）並強調減碼／防禦",
                )
            )
        elif primary_id == "rebound":
            # 權重極端情況仍主線反彈時，敘事至少須防禦
            if not _contains_any(scope, ("減碼", "停損", "追繳", "斷頭", "防禦")):
                issues.append(
                    (
                        "position_margin_pressure_unanchored",
                        "融資接近追繳，即使討論反彈也須同時強調追繳／減碼防禦",
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
    issues = _run_bucket_checks(region, facts)
    issues.extend(_check_scenario_plan(region, facts))
    if facts.uses_margin:
        issues.extend(_margin_risk_issues(text, region))
        issues.extend(_margin_maintenance_issues(text, region, facts))
    return issues


def _has_heading_with_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^##\s+", stripped) and any(k in stripped for k in keywords):
            return True
    return False


def _leg_check_scope(
    text: str, action_region: str, keywords: tuple[str, ...]
) -> str:
    """Resolve scope for one leg's bucket checks.

    Prefer ``## 現股`` / ``## 融資`` headings. Otherwise use the whole
    action/scenario chapter when it mentions the leg — so early「現股／融資」
    in 部位現況 does not starve stop-level checks under 操作情境, and so
    the keyword line itself (often containing 近20日低) is included.
    """
    if _has_heading_with_keywords(text, keywords):
        sliced = _slice_after_keywords(text, keywords)
        if sliced.strip():
            return sliced

    if action_region.strip() and any(k in action_region for k in keywords):
        return action_region

    fallback = _slice_after_keywords(text, keywords)
    if fallback.strip():
        return fallback
    return action_region if action_region.strip() else text


def run_dual_position_checks(
    body: str, bundle: DualPositionBundle | None
) -> list[FactIssue]:
    """Checks for cash/margin legs + combined scenario plan + synthesis."""
    if bundle is None or bundle.combined.unrealized_pnl_pct is None:
        return []

    text = body.strip()
    action_region = _slice_after_keywords(text, _ACTION_SECTION_KEYWORDS)
    region = action_region if action_region.strip() else text
    issues: list[FactIssue] = []

    # Combined market-scenario weights (always)
    issues.extend(_check_scenario_plan(region, bundle.combined))

    both_legs = bundle.cash is not None and bundle.margin is not None

    if bundle.cash is not None:
        cash_region = _leg_check_scope(text, region, _CASH_SECTION_KEYWORDS)
        if both_legs and "現股" not in text:
            issues.append(
                (
                    "position_cash_section_missing",
                    "同時持有現股與融資時，正文須有「現股」專段對齊現股損益",
                )
            )
        else:
            issues.extend(_run_bucket_checks(cash_region, bundle.cash))

    if bundle.margin is not None:
        margin_region = _leg_check_scope(text, region, _MARGIN_SECTION_KEYWORDS)
        if both_legs and "融資" not in text and "融资" not in text:
            issues.append(
                (
                    "position_margin_section_missing",
                    "同時持有現股與融資時，正文須有「融資」專段對齊融資損益",
                )
            )
        else:
            issues.extend(_run_bucket_checks(margin_region, bundle.margin))
        issues.extend(_margin_risk_issues(text, margin_region))
        # 維持率／壓力以 combined（已複製融資腿數字 + 情境權重）為準
        issues.extend(_margin_maintenance_issues(text, region, bundle.combined))

    if both_legs:
        synth_region = _slice_after_keywords(text, ("綜合", "優先"))
        synth_scope = synth_region if synth_region.strip() else text
        if not _contains_any(synth_scope, _SYNTHESIS_KEYWORDS):
            issues.append(
                (
                    "position_synthesis_missing",
                    "現股與融資同時存在時，須有綜合結論（優先序／兩邊如何取捨）",
                )
            )
        elif bundle.priority == "margin_first" and not _contains_any(
            synth_scope, ("融資", "融资", "優先")
        ):
            issues.append(
                (
                    "position_synthesis_priority_mismatch",
                    f"系統優先序為「{bundle.priority_label}」，綜合結論須點出優先處理融資",
                )
            )

    return issues


def write_position_facts_json(path, facts: PositionFacts | DualPositionBundle) -> None:
    from pathlib import Path

    if isinstance(facts, DualPositionBundle):
        payload = {
            "version": 2,
            "priority": facts.priority,
            "priority_label": facts.priority_label,
            "synthesis_hint": facts.synthesis_hint,
            "combined": asdict(facts.combined),
            "cash": asdict(facts.cash) if facts.cash is not None else None,
            "margin": asdict(facts.margin) if facts.margin is not None else None,
            # Flat combined fields for older readers
            **asdict(facts.combined),
        }
    else:
        payload = asdict(facts)

    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
