"""Deterministic portfolio construction (facts) from per-stock ChipFacts.

Mirrors ``chip_signals`` / ``position_signals``: Python owns every verdict —
which candidates are eligible, how they score, which ones are selected, and the
exact weights (summing to 100). The LLM layer (portfolio-gate) only writes the
narrative on top of these facts, so the numbers can be mechanically validated.

Two modes:
- **beginner**: ETF core + blue-chip satellites under risk profiles
  (conservative / balanced / aggressive); chips + fundamentals; at most one
  stock per sector with soft valuation-style diversity.
- **theme**: user-chosen independent themes (pick 1–3); rank by chip health
  plus fundamentals (PER/PBR/yield/revenue YoY); allow same-sector names within
  a theme; valuation-bucket diversity soft-caps; fusion themes share slots.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chip_signals import base_rate_forward_edge

PROFILES = ("conservative", "balanced", "aggressive")
MODES = ("beginner", "theme")

PROFILE_LABEL = {
    "conservative": "保守型",
    "balanced": "穩健型",
    "aggressive": "積極型",
}

# theme id → display (fallbacks; full catalog lives in portfolio_theme_universe.json)
DEFAULT_THEME_META: dict[str, dict[str, str]] = {
    "financials": {
        "label": "金融",
        "style": "defensive",
        "risk_hint": "相對防禦、偏息收；仍受利率與信用循環影響",
    },
    "dividend": {
        "label": "高股息",
        "style": "defensive",
        "risk_hint": "以息收為主；股價波動通常低於純題材股，但不保證配息維持",
    },
    "telecom": {
        "label": "電信",
        "style": "defensive",
        "risk_hint": "現金流較穩、波動相對低；成長性通常有限",
    },
    "consumer": {
        "label": "食品消費",
        "style": "defensive",
        "risk_hint": "防禦型消費需求較穩，但漲幅空間通常有限",
    },
    "ai": {
        "label": "AI",
        "style": "growth",
        "risk_hint": "成長／題材導向，估值與景氣敏感度高",
    },
    "semiconductor": {
        "label": "半導體",
        "style": "growth",
        "risk_hint": "製程與景氣循環影響大，波動通常高於金融／電信",
    },
    "servers": {
        "label": "伺服器組裝",
        "style": "growth",
        "risk_hint": "與雲端／AI 資本支出連動強，訂單與毛利波動大",
    },
    "pcb": {
        "label": "載板／PCB",
        "style": "cyclical",
        "risk_hint": "與電子週期、載板供需連動；題材熱時波動放大",
    },
    "thermal": {
        "label": "散熱",
        "style": "cyclical",
        "risk_hint": "題材／景氣循環色彩較濃，波動通常高於金融",
    },
    "shipping": {
        "label": "航運",
        "style": "cyclical",
        "risk_hint": "運價與景氣循環敏感，波動通常很大",
    },
    "green_energy": {
        "label": "綠能／儲能",
        "style": "growth",
        "risk_hint": "政策與題材驅動強，營運能見度與估值波動都偏高",
    },
    "biotech": {
        "label": "生技",
        "style": "growth",
        "risk_hint": "研發／授權事件驅動，個股非系統性風險高",
    },
}


@dataclass(frozen=True)
class ProfileTemplate:
    """Rule-fixed shape of a portfolio for a given risk profile."""

    profile: str
    label: str
    risk_label: str
    min_holdings: int
    max_holdings: int
    max_single_weight: int  # 單檔權重上限（%）
    max_etf_picks: int
    etf_core_min_weight: int  # ETF 核心至少佔比（%）
    allow_high_volatility: bool  # 是否允許納入高波動個股
    allow_shipping: bool  # 是否允許景氣循環/高波動族群（航運等）
    one_liner: str


PROFILE_TEMPLATES: dict[str, ProfileTemplate] = {
    "conservative": ProfileTemplate(
        profile="conservative",
        label="保守型",
        risk_label="低風險",
        min_holdings=2,
        max_holdings=3,
        max_single_weight=50,
        max_etf_picks=2,
        etf_core_min_weight=60,
        allow_high_volatility=False,
        allow_shipping=False,
        one_liner="以市值型與高股息 ETF 為主，波動小、求穩不求快。",
    ),
    "balanced": ProfileTemplate(
        profile="balanced",
        label="穩健型",
        risk_label="中風險",
        min_holdings=3,
        max_holdings=5,
        max_single_weight=35,
        max_etf_picks=2,
        etf_core_min_weight=40,
        allow_high_volatility=False,
        allow_shipping=False,
        one_liner="ETF 打底再搭配少數龍頭股，兼顧成長與分散。",
    ),
    "aggressive": ProfileTemplate(
        profile="aggressive",
        label="積極型",
        risk_label="中高風險",
        min_holdings=4,
        max_holdings=6,
        max_single_weight=25,
        max_etf_picks=2,
        etf_core_min_weight=25,
        allow_high_volatility=True,
        allow_shipping=True,
        one_liner="ETF 為基底，納入趨勢較強的龍頭股追求較高報酬，波動也較大。",
    ),
}

# 各風險屬性對不同類別的基礎分（愈高愈優先選入）。
CATEGORY_BASE_SCORE: dict[str, dict[str, int]] = {
    "conservative": {"broad_etf": 40, "dividend_etf": 42, "blue_chip": 8},
    "balanced": {"broad_etf": 34, "dividend_etf": 32, "blue_chip": 20},
    "aggressive": {"broad_etf": 26, "dividend_etf": 22, "blue_chip": 28},
}

VOLATILITY_LEVEL_VALUE = {"low": 1, "normal": 2, "high": 3, "unknown": 2}
VOLATILITY_LEVEL_LABEL = {"low": "低", "medium": "中", "high": "高"}

# 產業英文代碼 → 可讀中文（報告與表格顯示用）。
SECTOR_LABEL = {
    "etf_broad": "市值型 ETF",
    "etf_dividend": "高股息 ETF",
    "etf_theme": "主題 ETF",
    "semiconductor": "半導體",
    "electronics_manufacturing": "電子代工",
    "computer_hardware": "電腦/伺服器",
    "electronic_components": "電子零組件",
    "optics": "光學",
    "pcb": "電路板",
    "panel": "面板",
    "network_equipment": "網通設備",
    "optoelectronics": "光電",
    "financials": "金融",
    "thermal": "散熱",
    "telecom": "電信",
    "food": "食品",
    "green_energy": "綠能／儲能",
    "biotech": "生技",
    "plastics": "塑化",
    "steel": "鋼鐵",
    "shipping": "航運",
}

# 主題模式：類別基礎分（籌碼＋基本面共同排序）。
THEME_CATEGORY_BASE_SCORE = {
    "theme_etf": 18,
    "dividend_etf": 14,
    "broad_etf": 10,
    "theme_stock": 22,
    "blue_chip": 20,
}


@dataclass(frozen=True)
class ThemeTemplate:
    """Rule-fixed shape for theme / fusion sleeves."""

    min_holdings: int = 3
    max_holdings: int = 6
    max_single_weight: int = 30
    max_etf_picks: int = 2
    etf_soft_min_weight: int = 0  # 0 = 不強制 ETF；有 ETF 則自然競爭
    max_stocks_per_theme_single: int = 4
    max_stocks_per_theme_fusion: int = 2
    one_liner: str = (
        "依選定主題綜合籌碼與基本面（本益比／殖利率／營收年增）選股配權；"
        "袖口型組合，非全市場分散。"
    )


THEME_TEMPLATE = ThemeTemplate()


def sector_label(sector: str) -> str:
    return SECTOR_LABEL.get(sector, sector or "—")

# 高波動個股在各屬性下的扣分（保守型幾乎直接排除）。
HIGH_VOL_PENALTY = {"conservative": 30, "balanced": 10, "aggressive": 3}


@dataclass
class PortfolioCandidate:
    """A universe entry paired with its deterministic ChipFacts (or missing)."""

    stock_id: str
    name: str
    asset_class: str  # "etf" | "stock"
    category: str  # broad_etf | dividend_etf | blue_chip | theme_etf | theme_stock
    sector: str
    beginner_core: bool = False
    defensive: bool = False
    themes: list[str] = field(default_factory=list)
    facts: object | None = None  # ChipFacts, None when snapshot CSV is absent
    fundamentals: object | None = None  # FundamentalFacts | None
    close_price: float | None = None

    @property
    def is_etf(self) -> bool:
        return self.asset_class == "etf"

    @property
    def data_available(self) -> bool:
        return self.facts is not None


@dataclass
class PortfolioHolding:
    stock_id: str
    name: str
    asset_class: str
    category: str
    sector: str
    role: str  # "core" | "satellite" | "theme"
    weight_pct: int
    score: int
    close_price: float | None = None
    allocation_twd: int | None = None
    est_shares: int | None = None
    rationale_tags: list[str] = field(default_factory=list)
    chip_summary: str = ""
    themes: list[str] = field(default_factory=list)


@dataclass
class PortfolioFacts:
    profile: str
    profile_label: str
    risk_label: str
    trade_date: str
    holdings: list[PortfolioHolding]
    num_holdings: int
    max_single_weight: int
    etf_weight_pct: int
    top_sector: str
    top_sector_weight_pct: int
    expected_volatility_level: str  # low | medium | high
    diversification_ok: bool
    amount_twd: int | None = None
    warnings: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    mode: str = "beginner"  # beginner | theme
    themes: list[str] = field(default_factory=list)
    theme_labels: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _chip_health_score(facts, profile: str) -> int:
    """Deterministic chip/technical health, sign-aware and profile-aware."""
    if facts is None:
        return 0
    score = 0

    regime = getattr(facts, "chip_regime", "unknown")
    if regime == "accumulation":
        score += 8
    elif regime == "distribution":
        score -= 8

    ma_stack = getattr(facts, "ma_stack", "unknown")
    if ma_stack == "bullish_stack":
        score += 6
    elif ma_stack == "bearish_stack":
        score -= 6

    slope = getattr(facts, "ma20_slope", "unknown")
    if slope == "rising":
        score += 4
    elif slope == "falling":
        score -= 4

    price_trend = getattr(facts, "price_trend", "unknown")
    if price_trend == "up":
        score += 3
    elif price_trend == "down":
        score -= 3

    rs = getattr(facts, "rs_period", "unknown")
    if rs == "outperform":
        score += 5
    elif rs == "underperform":
        score -= 3

    # 趨勢強度只在方向明確時放大；空頭排列下的強趨勢是扣分
    trend_strength = getattr(facts, "trend_strength", "unknown")
    if trend_strength == "strong":
        if ma_stack == "bullish_stack":
            score += 3
        elif ma_stack == "bearish_stack":
            score -= 3

    rsi_zone = getattr(facts, "rsi_zone", "unknown")
    if rsi_zone == "overbought":
        score -= 4 if profile == "conservative" else 2
    elif rsi_zone == "oversold":
        score += 2

    consensus = getattr(facts, "institutional_consensus", "unknown")
    if consensus == "bullish":
        score += 3
    elif consensus == "bearish":
        score -= 3

    return score


def _base_rate_score(facts, base_rates: dict | None) -> int:
    if facts is None or not base_rates:
        return 0
    edge = base_rate_forward_edge(facts, base_rates)
    if edge is None:
        return 0
    # 1% 相對大盤超額 → 3 分，上限 ±6（與中量級 chip 因子相當）
    return max(-6, min(6, round(edge * 3)))


def _volatility_penalty(facts, profile: str) -> int:
    if facts is None:
        return 0
    regime = getattr(facts, "volatility_regime", "unknown")
    if regime == "high":
        return -HIGH_VOL_PENALTY[profile]
    if regime == "low" and profile == "conservative":
        return 3
    return 0


def _candidate_score(candidate: PortfolioCandidate, profile: str, base_rates: dict | None) -> int:
    base = CATEGORY_BASE_SCORE[profile].get(candidate.category, 0)
    if candidate.beginner_core:
        base += 4
    if candidate.defensive and profile == "conservative":
        base += 6
    elif candidate.defensive and profile == "balanced":
        base += 2
    health = _chip_health_score(candidate.facts, profile)
    rate = _base_rate_score(candidate.facts, base_rates)
    vol = _volatility_penalty(candidate.facts, profile)
    fund = 0
    if not candidate.is_etf:
        from fundamental_signals import fundamental_score

        fund = fundamental_score(
            getattr(candidate, "fundamentals", None),
            defensive_bias=(profile == "conservative"),
            themes=["financials"] if candidate.defensive else [],
        )
        # 新手組合：籌碼為主、基本面為輔
        fund = int(round(fund * 0.7))
    return base + health + rate + vol + fund


def _is_eligible(candidate: PortfolioCandidate, template: ProfileTemplate) -> tuple[bool, str]:
    if not candidate.data_available:
        return False, "無當日資料（未抓取 CSV）"
    facts = candidate.facts
    if not candidate.is_etf:
        vol = getattr(facts, "volatility_regime", "unknown")
        if vol == "high" and not template.allow_high_volatility:
            return False, "波動偏高，不適合此風險屬性"
        if candidate.sector == "shipping" and not template.allow_shipping:
            return False, "景氣循環/高波動族群，不納入此風險屬性"
        if template.profile == "conservative":
            if getattr(facts, "chip_regime", "unknown") == "distribution":
                return False, "籌碼偏空，保守型不納入"
            if getattr(facts, "day_trade_intensity", "unknown") == "high":
                return False, "當沖熱度偏高，保守型不納入"
    return True, ""


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _select_holdings(
    candidates: list[PortfolioCandidate],
    template: ProfileTemplate,
    scores: dict[str, int],
) -> tuple[list[PortfolioCandidate], list[dict]]:
    """Pick ETF core + one stock per sector, ranked by score."""
    excluded: list[dict] = []
    eligible: list[PortfolioCandidate] = []
    for cand in candidates:
        ok, reason = _is_eligible(cand, template)
        if ok:
            eligible.append(cand)
        elif cand.data_available:
            excluded.append({"stock_id": cand.stock_id, "name": cand.name, "reason": reason})

    eligible.sort(key=lambda c: scores[c.stock_id], reverse=True)

    etfs = [c for c in eligible if c.is_etf]
    stocks = [c for c in eligible if not c.is_etf]

    selected: list[PortfolioCandidate] = []

    # ETF 核心：優先各挑 1 檔市值型 + 高股息，湊到 max_etf_picks
    picked_etf_categories: set[str] = set()
    for cand in etfs:
        if len(selected) >= template.max_etf_picks:
            break
        if cand.category not in picked_etf_categories:
            selected.append(cand)
            picked_etf_categories.add(cand.category)
    for cand in etfs:
        if len(selected) >= template.max_etf_picks:
            break
        if cand not in selected:
            selected.append(cand)

    # 衛星個股：每個 sector 至多 1 檔；估值風格再軟性分散（避免全擠高本益比）
    used_sectors: set[str] = set()
    bucket_counts: dict[str, int] = {}
    max_per_bucket = 2
    deferred: list[PortfolioCandidate] = []
    for cand in stocks:
        if len(selected) >= template.max_holdings:
            break
        if cand.sector in used_sectors:
            continue
        bucket = _valuation_bucket(cand)
        if bucket != "unknown" and bucket_counts.get(bucket, 0) >= max_per_bucket:
            deferred.append(cand)
            continue
        selected.append(cand)
        used_sectors.add(cand.sector)
        if bucket != "unknown":
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    for cand in deferred:
        if len(selected) >= template.max_holdings:
            break
        if cand.sector in used_sectors:
            continue
        selected.append(cand)
        used_sectors.add(cand.sector)

    return selected, excluded


# ---------------------------------------------------------------------------
# Weight allocation
# ---------------------------------------------------------------------------


def _water_fill(weights: dict[str, float], cap: float) -> dict[str, float]:
    """Scale weights to sum 100 with no item exceeding ``cap`` (water-filling)."""
    ids = list(weights)
    result = {k: max(weights[k], 0.0) for k in ids}
    total = sum(result.values())
    if total <= 0:
        equal = 100.0 / len(ids)
        return {k: min(equal, cap) for k in ids}
    result = {k: v / total * 100.0 for k, v in result.items()}

    for _ in range(len(ids) + 2):
        capped = {k: v for k, v in result.items() if v >= cap - 1e-9}
        if not capped:
            break
        overflow = sum(result[k] - cap for k in capped)
        for k in capped:
            result[k] = cap
        free = [k for k in ids if k not in capped]
        free_total = sum(result[k] for k in free)
        if not free or free_total <= 0 or overflow <= 1e-9:
            break
        for k in free:
            result[k] += overflow * (result[k] / free_total)
    return result


def _enforce_etf_core(
    weights: dict[str, float],
    etf_ids: set[str],
    etf_min: float,
) -> dict[str, float]:
    if not etf_ids or etf_min <= 0:
        return weights
    etf_total = sum(weights[k] for k in weights if k in etf_ids)
    non_etf_total = sum(weights[k] for k in weights if k not in etf_ids)
    if etf_total >= etf_min or non_etf_total <= 0:
        return weights
    target_non_etf = 100.0 - etf_min
    etf_scale = etf_min / etf_total if etf_total > 0 else 0.0
    non_scale = target_non_etf / non_etf_total
    out: dict[str, float] = {}
    for k, v in weights.items():
        out[k] = v * (etf_scale if k in etf_ids else non_scale)
    return out


def _round_to_100(weights: dict[str, float]) -> dict[str, int]:
    ints = {k: int(v) for k, v in weights.items()}
    remainder = 100 - sum(ints.values())
    if remainder:
        # 把餘數加到小數部分最大的項目上，維持總和 100
        order = sorted(weights, key=lambda k: weights[k] - int(weights[k]), reverse=True)
        for k in order[: max(remainder, 0)]:
            ints[k] += 1
    return ints


def _allocate_weights(
    selected: list[PortfolioCandidate],
    template: ProfileTemplate,
    scores: dict[str, int],
) -> dict[str, int]:
    if not selected:
        return {}
    ids = [c.stock_id for c in selected]
    etf_ids = {c.stock_id for c in selected if c.is_etf}

    min_score = min(scores[i] for i in ids)
    shift = (-min_score + 1) if min_score <= 0 else 0
    raw = {i: float(scores[i] + shift) for i in ids}

    cap = float(template.max_single_weight)
    weights = _water_fill(raw, cap)
    weights = _enforce_etf_core(weights, etf_ids, float(template.etf_core_min_weight))
    weights = _water_fill(weights, cap)
    return _round_to_100(weights)


# ---------------------------------------------------------------------------
# Rationale + assembly
# ---------------------------------------------------------------------------


def _rationale_tags(candidate: PortfolioCandidate, role: str) -> list[str]:
    tags: list[str] = []
    if candidate.category == "broad_etf":
        tags.append("市值型 ETF：一次買進整個台股大盤，最分散")
    elif candidate.category == "dividend_etf":
        tags.append("高股息 ETF：領息為主、波動通常較小")
    elif candidate.category == "theme_etf":
        tags.append("主題 ETF：一籃子曝險該主題")
    elif role == "core":
        tags.append("核心持股")
    elif role == "theme":
        tags.append("主題持股")
    else:
        tags.append("龍頭權值股")

    facts = candidate.facts
    if facts is not None:
        if getattr(facts, "chip_regime", "unknown") == "accumulation":
            tags.append("籌碼偏多（法人/價格同步偏多）")
        if getattr(facts, "ma_stack", "unknown") == "bullish_stack":
            tags.append("均線多頭排列")
        if getattr(facts, "rs_period", "unknown") == "outperform":
            tags.append("近期強於大盤")
        if getattr(facts, "institutional_consensus", "unknown") == "bullish":
            tags.append("三大法人一致買超")
        if getattr(facts, "volatility_regime", "unknown") == "low":
            tags.append("波動偏低、相對抗震")
    if candidate.defensive:
        tags.append("防禦型（相對穩定）")

    fund = getattr(candidate, "fundamentals", None)
    fund_tags = list(getattr(fund, "tags", []) or []) if fund is not None else []
    tags.extend(fund_tags[:3])
    return tags


def _chip_summary(facts) -> str:
    if facts is None:
        return "無當日資料"
    parts: list[str] = []
    anchors = list(getattr(facts, "anchors", []) or [])
    return "；".join(anchors[:3]) if anchors else "、".join(parts)


def _expected_volatility_level(holdings: list[PortfolioHolding], candidates_by_id: dict) -> str:
    if not holdings:
        return "medium"
    total_w = 0
    acc = 0.0
    for h in holdings:
        cand = candidates_by_id.get(h.stock_id)
        facts = cand.facts if cand else None
        regime = getattr(facts, "volatility_regime", "unknown") if facts else "unknown"
        value = VOLATILITY_LEVEL_VALUE.get(regime, 2)
        # ETF 本質較分散，波動視為偏低一級（不低於 1）
        if cand and cand.is_etf:
            value = max(1, value - 1)
        acc += value * h.weight_pct
        total_w += h.weight_pct
    avg = acc / total_w if total_w else 2.0
    if avg <= 1.6:
        return "low"
    if avg >= 2.4:
        return "high"
    return "medium"


def build_portfolio_facts(
    candidates: list[PortfolioCandidate],
    *,
    profile: str,
    base_rates: dict | None = None,
    amount_twd: int | None = None,
    trade_date: str = "",
) -> PortfolioFacts:
    if profile not in PROFILE_TEMPLATES:
        raise ValueError(f"未知的風險屬性：{profile}（可用：{', '.join(PROFILES)}）")
    template = PROFILE_TEMPLATES[profile]

    scores = {c.stock_id: _candidate_score(c, profile, base_rates) for c in candidates}
    selected, excluded = _select_holdings(candidates, template, scores)
    weights = _allocate_weights(selected, template, scores)

    candidates_by_id = {c.stock_id: c for c in candidates}
    holdings: list[PortfolioHolding] = []
    for cand in selected:
        weight = weights.get(cand.stock_id, 0)
        role = "core" if cand.is_etf else "satellite"
        allocation = None
        est_shares = None
        if amount_twd:
            allocation = int(round(amount_twd * weight / 100.0))
            if cand.close_price and cand.close_price > 0:
                est_shares = int(allocation // cand.close_price)
        holdings.append(
            PortfolioHolding(
                stock_id=cand.stock_id,
                name=cand.name,
                asset_class=cand.asset_class,
                category=cand.category,
                sector=cand.sector,
                role=role,
                weight_pct=weight,
                score=scores[cand.stock_id],
                close_price=cand.close_price,
                allocation_twd=allocation,
                est_shares=est_shares,
                rationale_tags=_rationale_tags(cand, role),
                chip_summary=_chip_summary(cand.facts),
                themes=list(cand.themes),
            )
        )
    holdings.sort(key=lambda h: h.weight_pct, reverse=True)

    etf_weight = sum(h.weight_pct for h in holdings if h.asset_class == "etf")
    sector_weight: dict[str, int] = {}
    for h in holdings:
        sector_weight[h.sector] = sector_weight.get(h.sector, 0) + h.weight_pct
    top_sector, top_sector_weight = ("", 0)
    if sector_weight:
        top_sector, top_sector_weight = max(sector_weight.items(), key=lambda kv: kv[1])

    warnings = _build_warnings(holdings, template, etf_weight, selected=selected)
    diversification_ok = not any(w.startswith("分散") for w in warnings) and len(holdings) >= template.min_holdings

    facts = PortfolioFacts(
        profile=profile,
        profile_label=template.label,
        risk_label=template.risk_label,
        trade_date=trade_date,
        holdings=holdings,
        num_holdings=len(holdings),
        max_single_weight=template.max_single_weight,
        etf_weight_pct=etf_weight,
        top_sector=top_sector,
        top_sector_weight_pct=top_sector_weight,
        expected_volatility_level=_expected_volatility_level(holdings, candidates_by_id),
        diversification_ok=diversification_ok,
        amount_twd=amount_twd,
        warnings=warnings,
        excluded=excluded,
        mode="beginner",
    )
    facts.anchors = _build_anchors(facts, template)
    return facts


# ---------------------------------------------------------------------------
# Theme mode: chips + fundamentals (+ theme universe)
# ---------------------------------------------------------------------------


def normalize_themes(themes: list[str] | tuple[str, ...] | None) -> list[str]:
    if not themes:
        raise ValueError("主題模式須至少指定一個 themes")
    seen: set[str] = set()
    out: list[str] = []
    for raw in themes:
        tid = str(raw).strip().lower()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    if not out:
        raise ValueError("主題模式須至少指定一個 themes")
    return out


def theme_slug(themes: list[str]) -> str:
    return "theme_" + "_".join(sorted(normalize_themes(themes)))


def resolve_theme_meta(
    themes: list[str],
    catalog: dict[str, dict] | None = None,
) -> list[dict[str, str]]:
    cat = catalog or DEFAULT_THEME_META
    metas: list[dict[str, str]] = []
    for tid in themes:
        raw = cat.get(tid) or DEFAULT_THEME_META.get(tid) or {
            "label": tid,
            "style": "growth",
            "risk_hint": "主題袖口，波動視標的而定",
        }
        metas.append(
            {
                "id": tid,
                "label": str(raw.get("label", tid)),
                "style": str(raw.get("style", "growth")),
                "risk_hint": str(raw.get("risk_hint", "")),
            }
        )
    return metas


def theme_profile_label(metas: list[dict[str, str]]) -> str:
    labels = [m["label"] for m in metas]
    if len(labels) == 1:
        return f"{labels[0]}主題"
    return "＋".join(labels) + "主題"


def theme_risk_label(metas: list[dict[str, str]]) -> str:
    styles = {m["style"] for m in metas}
    if styles <= {"defensive"}:
        return "中低風險（主題集中）"
    if "cyclical" in styles or "growth" in styles:
        if "defensive" in styles:
            return "中高風險（防禦＋題材融合）"
        return "中高風險（主題集中）"
    return "中風險（主題集中）"


def theme_one_liner(metas: list[dict[str, str]]) -> str:
    labels = "、".join(m["label"] for m in metas)
    hints = "；".join(m["risk_hint"] for m in metas if m.get("risk_hint"))
    base = (
        f"這是「{labels}」袖口組合：在主題候選池內綜合籌碼與基本面"
        "（本益比／殖利率／營收年增）挑選相對較佳者。"
    )
    return f"{base} {hints}".strip() if hints else base


def _theme_candidate_score(
    candidate: PortfolioCandidate,
    base_rates: dict | None,
) -> int:
    base = THEME_CATEGORY_BASE_SCORE.get(candidate.category, 12)
    health = _chip_health_score(candidate.facts, "aggressive")
    rate = _base_rate_score(candidate.facts, base_rates)
    vol = _volatility_penalty(candidate.facts, "aggressive")
    fund = 0
    if not candidate.is_etf:
        from fundamental_signals import fundamental_score

        fund = fundamental_score(
            getattr(candidate, "fundamentals", None),
            themes=list(candidate.themes),
        )
    return base + health + rate + vol + fund


def _theme_is_eligible(candidate: PortfolioCandidate) -> tuple[bool, str]:
    if not candidate.data_available:
        return False, "無當日資料（未抓取 CSV）"
    return True, ""


def _valuation_bucket(candidate: PortfolioCandidate) -> str:
    fund = getattr(candidate, "fundamentals", None)
    if fund is None:
        return "unknown"
    bucket = getattr(fund, "valuation_bucket", None)
    if callable(bucket):
        return str(bucket())
    return "unknown"


def _select_theme_holdings(
    candidates: list[PortfolioCandidate],
    themes: list[str],
    scores: dict[str, int],
    template: ThemeTemplate = THEME_TEMPLATE,
) -> tuple[list[PortfolioCandidate], list[dict]]:
    """Pick theme ETFs + top stocks per theme with valuation-bucket diversity."""
    theme_set = set(themes)
    excluded: list[dict] = []
    pool: list[PortfolioCandidate] = []
    for cand in candidates:
        if theme_set.isdisjoint(set(cand.themes)):
            continue
        ok, reason = _theme_is_eligible(cand)
        if ok:
            pool.append(cand)
        elif cand.data_available:
            excluded.append({"stock_id": cand.stock_id, "name": cand.name, "reason": reason})

    pool.sort(key=lambda c: scores[c.stock_id], reverse=True)

    selected: list[PortfolioCandidate] = []
    selected_ids: set[str] = set()

    etfs = [c for c in pool if c.is_etf]
    for cand in etfs:
        if len(selected) >= template.max_etf_picks:
            break
        selected.append(cand)
        selected_ids.add(cand.stock_id)

    fusion = len(themes) > 1
    per_theme_cap = (
        template.max_stocks_per_theme_fusion if fusion else template.max_stocks_per_theme_single
    )
    per_theme_count = {t: 0 for t in themes}
    # 第一輪：同主題內同估值風格至多 1 檔，強制風格多元
    max_per_bucket = 1
    per_theme_bucket_count: dict[str, dict[str, int]] = {t: {} for t in themes}

    stocks = [c for c in pool if not c.is_etf]
    for cand in stocks:
        if len(selected) >= template.max_holdings:
            break
        if cand.stock_id in selected_ids:
            continue
        matched = [t for t in themes if t in cand.themes]
        if not matched:
            continue
        target = next((t for t in matched if per_theme_count[t] < per_theme_cap), None)
        if target is None:
            continue
        bucket = _valuation_bucket(cand)
        bucket_counts = per_theme_bucket_count[target]
        if bucket != "unknown" and bucket_counts.get(bucket, 0) >= max_per_bucket:
            continue
        selected.append(cand)
        selected_ids.add(cand.stock_id)
        per_theme_count[target] += 1
        if bucket != "unknown":
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    # 第二輪：名額不足時略過估值桶限制，仍受 per_theme_cap
    for cand in stocks:
        if len(selected) >= template.max_holdings:
            break
        if cand.stock_id in selected_ids:
            continue
        matched = [t for t in themes if t in cand.themes]
        target = next((t for t in matched if per_theme_count[t] < per_theme_cap), None)
        if target is None:
            continue
        selected.append(cand)
        selected_ids.add(cand.stock_id)
        per_theme_count[target] += 1

    return selected, excluded


def _allocate_theme_weights(
    selected: list[PortfolioCandidate],
    scores: dict[str, int],
    template: ThemeTemplate = THEME_TEMPLATE,
) -> dict[str, int]:
    if not selected:
        return {}
    ids = [c.stock_id for c in selected]
    min_score = min(scores[i] for i in ids)
    shift = (-min_score + 1) if min_score <= 0 else 0
    raw = {i: float(scores[i] + shift) for i in ids}
    # 持股少時放寬單檔上限，否則無法配滿 100%（例如 3 檔 × 30% = 90%）
    n = len(ids)
    floor_cap = int((100 + n - 1) // n)  # ceil(100/n)
    cap = float(max(template.max_single_weight, floor_cap))
    weights = _water_fill(raw, cap)
    if template.etf_soft_min_weight > 0:
        etf_ids = {c.stock_id for c in selected if c.is_etf}
        weights = _enforce_etf_core(weights, etf_ids, float(template.etf_soft_min_weight))
        weights = _water_fill(weights, cap)
    return _round_to_100(weights)


def _fundamentals_coverage_note(
    selected: list[PortfolioCandidate],
) -> str | None:
    stocks = [c for c in selected if not c.is_etf]
    if not stocks:
        return None
    covered = sum(
        1
        for c in stocks
        if bool(getattr(getattr(c, "fundamentals", None), "available", False))
    )
    if covered == 0:
        return "基本面資料皆缺，本次僅依籌碼評分（可稍後重跑以寫入 fundamentals 快取）"
    if covered < len(stocks):
        return f"基本面覆蓋 {covered}/{len(stocks)} 檔；缺資料者僅依籌碼評分"
    return None


def _build_theme_warnings(
    holdings: list[PortfolioHolding],
    themes: list[str],
    template: ThemeTemplate,
    *,
    selected: list[PortfolioCandidate] | None = None,
) -> list[str]:
    warnings: list[str] = []
    if len(holdings) < template.min_holdings:
        warnings.append(
            f"可用標的不足：僅選出 {len(holdings)} 檔（建議至少 {template.min_holdings} 檔），"
            "請先抓取主題候選池個股 CSV 後重跑"
        )
    if len(themes) > 1:
        warnings.append(
            "融合主題組合：不同主題風險特性可能不同，請把這筆錢視為袖口而非全倉。"
        )
    else:
        warnings.append(
            "單一主題組合產業集中度高，不具全市場分散效果，適合作為既有部位的袖口配置。"
        )
    note = _fundamentals_coverage_note(selected or [])
    if note:
        warnings.append(note)
    return warnings


def build_theme_portfolio_facts(
    candidates: list[PortfolioCandidate],
    *,
    themes: list[str],
    base_rates: dict | None = None,
    amount_twd: int | None = None,
    trade_date: str = "",
    theme_catalog: dict[str, dict] | None = None,
) -> PortfolioFacts:
    themes_n = normalize_themes(themes)
    unknown = [t for t in themes_n if t not in (theme_catalog or DEFAULT_THEME_META) and t not in DEFAULT_THEME_META]
    # Allow unknown theme ids if candidates carry them; only error if none match later.
    metas = resolve_theme_meta(themes_n, theme_catalog)
    template = THEME_TEMPLATE
    label = theme_profile_label(metas)
    risk = theme_risk_label(metas)
    slug = theme_slug(themes_n)

    scores = {c.stock_id: _theme_candidate_score(c, base_rates) for c in candidates}
    selected, excluded = _select_theme_holdings(candidates, themes_n, scores, template)
    if not selected and not unknown:
        # still return empty-ish facts with warnings
        pass
    weights = _allocate_theme_weights(selected, scores, template)

    candidates_by_id = {c.stock_id: c for c in candidates}
    holdings: list[PortfolioHolding] = []
    for cand in selected:
        weight = weights.get(cand.stock_id, 0)
        role = "theme"
        allocation = None
        est_shares = None
        if amount_twd:
            allocation = int(round(amount_twd * weight / 100.0))
            if cand.close_price and cand.close_price > 0:
                est_shares = int(allocation // cand.close_price)
        holdings.append(
            PortfolioHolding(
                stock_id=cand.stock_id,
                name=cand.name,
                asset_class=cand.asset_class,
                category=cand.category,
                sector=cand.sector,
                role=role,
                weight_pct=weight,
                score=scores[cand.stock_id],
                close_price=cand.close_price,
                allocation_twd=allocation,
                est_shares=est_shares,
                rationale_tags=_rationale_tags(cand, role),
                chip_summary=_chip_summary(cand.facts),
                themes=[t for t in themes_n if t in cand.themes],
            )
        )
    holdings.sort(key=lambda h: h.weight_pct, reverse=True)

    etf_weight = sum(h.weight_pct for h in holdings if h.asset_class == "etf")
    sector_weight: dict[str, int] = {}
    for h in holdings:
        sector_weight[h.sector] = sector_weight.get(h.sector, 0) + h.weight_pct
    top_sector, top_sector_weight = ("", 0)
    if sector_weight:
        top_sector, top_sector_weight = max(sector_weight.items(), key=lambda kv: kv[1])

    warnings = _build_theme_warnings(holdings, themes_n, template, selected=selected)
    if unknown:
        warnings.append(f"未知主題代碼（仍會嘗試匹配候選池）：{', '.join(unknown)}")

    facts = PortfolioFacts(
        profile=slug,
        profile_label=label,
        risk_label=risk,
        trade_date=trade_date,
        holdings=holdings,
        num_holdings=len(holdings),
        max_single_weight=max(
            template.max_single_weight,
            int((100 + max(len(holdings), 1) - 1) // max(len(holdings), 1)),
        ) if holdings else template.max_single_weight,
        etf_weight_pct=etf_weight,
        top_sector=top_sector,
        top_sector_weight_pct=top_sector_weight,
        expected_volatility_level=_expected_volatility_level(holdings, candidates_by_id),
        diversification_ok=False,  # 主題袖口先天集中
        amount_twd=amount_twd,
        warnings=warnings,
        excluded=excluded,
        mode="theme",
        themes=themes_n,
        theme_labels=[m["label"] for m in metas],
    )
    facts.anchors = _build_theme_anchors(facts, metas)
    return facts


def _build_theme_anchors(facts: PortfolioFacts, metas: list[dict[str, str]]) -> list[str]:
    anchors = [
        f"模式：主題組合 — {facts.profile_label}（{facts.risk_label}）",
        theme_one_liner(metas),
        f"共 {facts.num_holdings} 檔，單檔上限 {facts.max_single_weight}%，"
        f"ETF 佔 {facts.etf_weight_pct}%",
        f"預期波動：{VOLATILITY_LEVEL_LABEL.get(facts.expected_volatility_level, '中')}",
    ]
    for h in facts.holdings:
        tag0 = h.rationale_tags[0] if h.rationale_tags else "主題持股"
        anchors.append(f"{h.name}（{h.stock_id}）{h.weight_pct}%：{tag0}")
    return anchors


def _build_warnings(
    holdings: list[PortfolioHolding],
    template: ProfileTemplate,
    etf_weight: int,
    *,
    selected: list[PortfolioCandidate] | None = None,
) -> list[str]:
    warnings: list[str] = []
    if len(holdings) < template.min_holdings:
        warnings.append(
            f"可用標的不足：僅選出 {len(holdings)} 檔（建議至少 {template.min_holdings} 檔），"
            "請補齊候選池資料（尤其 ETF）後重跑"
        )
    if not any(h.asset_class == "etf" for h in holdings):
        warnings.append(
            "本組合無 ETF 核心（可能缺 ETF 當日資料），已改以大型權值股替代；"
            "建議先抓取 0050/0056/00878/006208 資料以符合新手核心配置"
        )
    elif etf_weight < template.etf_core_min_weight:
        warnings.append(
            f"ETF 核心佔比 {etf_weight}% 低於建議 {template.etf_core_min_weight}%"
        )
    note = _fundamentals_coverage_note(selected or [])
    if note:
        warnings.append(note)
    return warnings


def _build_anchors(facts: PortfolioFacts, template: ProfileTemplate) -> list[str]:
    anchors = [
        f"風險屬性：{facts.profile_label}（{facts.risk_label}）— {template.one_liner}",
        f"共 {facts.num_holdings} 檔，單檔上限 {facts.max_single_weight}%，"
        f"ETF 核心佔 {facts.etf_weight_pct}%",
        f"預期波動：{VOLATILITY_LEVEL_LABEL.get(facts.expected_volatility_level, '中')}",
    ]
    for h in facts.holdings:
        anchors.append(f"{h.name}（{h.stock_id}）{h.weight_pct}%：{h.rationale_tags[0]}")
    return anchors


# ---------------------------------------------------------------------------
# Rendering (beginner-facing Markdown; numbers owned by Python)
# ---------------------------------------------------------------------------


def _fmt_twd(value: int | None) -> str:
    return f"{value:,} 元" if value is not None else "—"


# ETF 類別理由本身已具資訊量；個股則優先顯示籌碼型理由（跳過通用標）。
_GENERIC_STOCK_TAGS = frozenset({"龍頭權值股", "主題持股", "核心持股"})


def _table_reason(holding: PortfolioHolding) -> str:
    tags = holding.rationale_tags
    if not tags:
        return ""
    if holding.asset_class == "etf":
        return tags[0]
    for tag in tags:
        if tag not in _GENERIC_STOCK_TAGS:
            return tag
    return tags[0]


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _holding_role_label(holding: PortfolioHolding) -> str:
    if holding.role == "core":
        return "核心 ETF"
    if holding.role == "theme":
        return "主題持股"
    return "衛星個股"


def _facts_one_liner(facts: PortfolioFacts) -> str:
    if facts.mode == "theme":
        metas = [
            {"label": lab, "risk_hint": "", "style": ""}
            for lab in facts.theme_labels
        ]
        # Rebuild from defaults for richer hint when possible
        resolved = resolve_theme_meta(facts.themes)
        return theme_one_liner(resolved if resolved else metas)
    template = PROFILE_TEMPLATES.get(facts.profile)
    return template.one_liner if template else ""


def build_portfolio_markdown(facts: PortfolioFacts) -> str:
    vol_label = VOLATILITY_LEVEL_LABEL.get(facts.expected_volatility_level, "中")
    if facts.mode == "theme":
        title = f"## {facts.profile_label}組合建議（{facts.risk_label}）"
        etf_label = "ETF 佔比"
    else:
        title = f"## {facts.profile_label}投資組合建議（{facts.risk_label}）"
        etf_label = "ETF 核心"
    lines: list[str] = [
        title,
        f"**資料日期：** {facts.trade_date or '—'}　**預期波動：** {vol_label}　"
        f"**持股數：** {facts.num_holdings} 檔　**{etf_label}：** {facts.etf_weight_pct}%",
        "",
        f"> {_facts_one_liner(facts)}",
        "",
    ]
    if facts.mode == "theme" and facts.theme_labels:
        lines.append(f"**主題：** {'、'.join(facts.theme_labels)}")
        lines.append("")
    if facts.amount_twd:
        lines.append(f"**試算金額：** {_fmt_twd(facts.amount_twd)}（下表金額與估計股數依權重換算，僅供參考）")
        lines.append("")

    lines.append("### 持股清單")
    has_amount = facts.amount_twd is not None
    headers = ["股票", "角色", "權重", "白話理由"]
    if has_amount:
        headers[3:3] = ["投入金額", "約可買"]
    rows: list[list[str]] = []
    for h in facts.holdings:
        role = _holding_role_label(h)
        reason = _table_reason(h)
        row = [f"{h.name}（{h.stock_id}）", role, f"{h.weight_pct}%", reason]
        if has_amount:
            shares = f"約 {h.est_shares} 股" if h.est_shares else "—"
            row[3:3] = [_fmt_twd(h.allocation_twd), shares]
        rows.append(row)
    lines.append(_md_table(headers, rows))
    lines.append("")

    lines.append("### 每檔為什麼選它")
    for h in facts.holdings:
        tags = "、".join(h.rationale_tags)
        lines.append(f"- **{h.name}（{h.stock_id}）** {h.weight_pct}%：{tags}")
    lines.append("")

    lines.append("### 這個組合的風險")
    lines.append(
        f"- 整體風險等級：**{facts.risk_label}**，預期波動 **{vol_label}**。"
        "波動代表帳面上下起伏的幅度，波動愈高、短期漲跌愈大。"
    )
    if facts.mode == "theme":
        lines.append(
            f"- 主題集中：最大單一產業為「{sector_label(facts.top_sector)}」"
            f"約 {facts.top_sector_weight_pct}%；此為袖口曝險，不是全市場分散。"
        )
    else:
        lines.append(
            f"- 分散程度：{'良好' if facts.diversification_ok else '偏集中，請留意'}；"
            f"最大單一產業為「{sector_label(facts.top_sector)}」約 {facts.top_sector_weight_pct}%。"
        )
    for warning in facts.warnings:
        lines.append(f"- 注意：{warning}")
    lines.append("")

    if facts.mode == "theme":
        lines.append("### 部位紀律卡")
        lines.extend(
            [
                "- **當袖口、不當全倉：** 這筆錢只負責主題曝險，勿把既有部位忽略。",
                "- **下跌先看理由：** 主題退潮或籌碼轉弱時，檢視是否仍值得持有。",
                "- **分批進場：** 可分 2～3 次買進，降低買在題材高點的風險。",
                "- **定期檢視：** 每季檢視主題是否仍成立、權重是否過度偏移。",
                "- **只投閒錢：** 這筆錢短期內不會用到，才適合投入股市。",
            ]
        )
    else:
        lines.append("### 新手紀律卡（最重要）")
        lines.extend(
            [
                "- **下跌別急著賣：** 短期帳面虧損是正常波動，先看當初買進的理由是否還在。",
                "- **分批進場：** 可分 2～3 次買進，不要一次全押，降低買在高點的風險。",
                "- **定期檢視：** 每季（約 3 個月）檢視一次即可，不需要每天盯盤。",
                "- **再平衡：** 當某檔權重明顯偏離上表時，適度調回原配置。",
                "- **只投閒錢：** 這筆錢短期內不會用到，才適合投入股市。",
            ]
        )
    lines.append("")

    lines.append("### 免責聲明")
    lines.append(
        "> 本組合由系統依規則與歷史資料自動產生，僅供教育與研究參考，"
        "不構成任何個別投資建議或買賣邀約。投資有風險，過去表現不代表未來，"
        "請依自身財務狀況審慎評估或諮詢合格理財顧問。"
    )
    return "\n".join(lines).strip() + "\n"


def build_portfolio_tables_markdown(facts: PortfolioFacts) -> str:
    """Deterministic block (header + holdings table). Numbers owned by Python.

    Used by portfolio-gate: the agy narrative is appended after this block, so
    the weights/amounts a reader sees are never authored by the LLM.
    """
    vol_label = VOLATILITY_LEVEL_LABEL.get(facts.expected_volatility_level, "中")
    title = (
        f"## {facts.profile_label}主題組合配置（{facts.risk_label}）"
        if facts.mode == "theme"
        else f"## {facts.profile_label}投資組合配置（{facts.risk_label}）"
    )
    etf_label = "ETF 佔比" if facts.mode == "theme" else "ETF 核心"
    lines: list[str] = [
        title,
        f"**資料日期：** {facts.trade_date or '—'}　**預期波動：** {vol_label}　"
        f"**持股數：** {facts.num_holdings} 檔　**{etf_label}：** {facts.etf_weight_pct}%",
        f"**資料來源：** 系統依規則自動配置（權重、金額、股數以本表為準，敘述勿更動數字）",
        "",
    ]
    if facts.mode == "theme" and facts.theme_labels:
        lines.insert(2, f"**主題：** {'、'.join(facts.theme_labels)}")
    if facts.amount_twd:
        lines.append(f"**試算金額：** {_fmt_twd(facts.amount_twd)}")
        lines.append("")

    lines.append("### 持股配置")
    has_amount = facts.amount_twd is not None
    headers = ["股票", "角色", "產業", "權重"]
    if has_amount:
        headers.extend(["投入金額", "約可買"])
    rows: list[list[str]] = []
    for h in facts.holdings:
        role = _holding_role_label(h)
        row = [f"{h.name}（{h.stock_id}）", role, sector_label(h.sector), f"{h.weight_pct}%"]
        if has_amount:
            shares = f"約 {h.est_shares} 股" if h.est_shares else "—"
            row.extend([_fmt_twd(h.allocation_twd), shares])
        rows.append(row)
    lines.append(_md_table(headers, rows))
    return "\n".join(lines).strip() + "\n"


def merge_portfolio_report_body(facts: PortfolioFacts, agy_body: str) -> str:
    """Combine the deterministic table block with the agy narrative body."""
    tables = build_portfolio_tables_markdown(facts)
    narrative = agy_body.strip()
    if not narrative:
        return tables
    return f"{tables}\n\n---\n\n{narrative}\n"


def portfolio_facts_summary_for_prompt(facts: PortfolioFacts) -> str:
    """LLM-facing deterministic summary (for portfolio-gate)."""
    vol_label = VOLATILITY_LEVEL_LABEL.get(facts.expected_volatility_level, "中")
    if facts.mode == "theme":
        lines = [
            "【組合系統事實（權重/檔數/角色/金額由系統決定，敘述勿更動數字）】",
            f"- 模式：主題組合；名稱：{facts.profile_label}（{facts.risk_label}）",
            f"- 主題：{'、'.join(facts.theme_labels) or '—'} — {_facts_one_liner(facts)}",
            f"- 持股數：{facts.num_holdings}；單檔上限 {facts.max_single_weight}%；"
            f"ETF 佔比 {facts.etf_weight_pct}%；預期波動 {vol_label}；"
            f"最大單一產業 {sector_label(facts.top_sector)} 約 {facts.top_sector_weight_pct}%",
            "- 重要：此為主題袖口，非新手全倉分散組合；敘述須說明主題集中風險。",
            "- 各持股（名稱｜代碼｜權重｜角色｜產業｜可用理由，敘述須對齊、不可與此矛盾）：",
        ]
    else:
        template = PROFILE_TEMPLATES[facts.profile]
        lines = [
            "【組合系統事實（權重/檔數/角色/金額由系統決定，敘述勿更動數字）】",
            f"- 風險屬性：{facts.profile_label}（{facts.risk_label}）— {template.one_liner}",
            f"- 持股數：{facts.num_holdings}；單檔上限 {facts.max_single_weight}%；"
            f"ETF 核心 {facts.etf_weight_pct}%；預期波動 {vol_label}；"
            f"最大單一產業 {sector_label(facts.top_sector)} 約 {facts.top_sector_weight_pct}%",
            "- 各持股（名稱｜代碼｜權重｜角色｜產業｜可用理由，敘述須對齊、不可與此矛盾）：",
        ]
    for h in facts.holdings:
        role = _holding_role_label(h).replace(" ", "")
        lines.append(
            f"  - {h.name}（{h.stock_id}）｜{h.weight_pct}%｜{role}｜{sector_label(h.sector)}"
            f"｜{'／'.join(h.rationale_tags)}"
        )
    if facts.warnings:
        lines.append("- 系統警示（須在風險段誠實揭露）：" + "；".join(facts.warnings))
    return "\n".join(lines)


def write_portfolio_facts_json(path, facts: PortfolioFacts) -> None:
    Path(path).write_text(
        json.dumps(asdict(facts), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_universe(path: Path) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    candidates = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(candidates, list):
        raise ValueError(f"universe 格式錯誤：{path}")
    return [c for c in candidates if isinstance(c, dict) and c.get("id")]


def load_theme_catalog(path: Path) -> dict[str, dict]:
    """Return theme_id → {label, style, risk_hint} from a theme universe JSON."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    themes = raw.get("themes") if isinstance(raw, dict) else None
    if not isinstance(themes, dict):
        return dict(DEFAULT_THEME_META)
    out: dict[str, dict] = {}
    for tid, meta in themes.items():
        if not isinstance(meta, dict):
            continue
        out[str(tid)] = {
            "label": str(meta.get("label", tid)),
            "style": str(meta.get("style", "growth")),
            "risk_hint": str(meta.get("risk_hint", "")),
        }
    return out or dict(DEFAULT_THEME_META)


def available_theme_ids(path: Path | None = None) -> list[str]:
    if path and Path(path).exists():
        return sorted(load_theme_catalog(path))
    return sorted(DEFAULT_THEME_META)
