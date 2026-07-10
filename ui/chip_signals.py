"""Deterministic chip signals (facts) computed from a stock CSV row + history.

This is the *harness* layer: Python owns the factual verdicts (directions,
streaks, divergences, regime labels) so the LLM only has to explain them. The
same facts feed both the agy prompt and the fact-consistency validation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


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


def _to_int(raw: object) -> int | None:
    value = _to_float(raw)
    return int(round(value)) if value is not None else None


def _sign(value: float | None, *, deadband: float = 0.0) -> int:
    if value is None:
        return 0
    if value > deadband:
        return 1
    if value < -deadband:
        return -1
    return 0


def _fmt_lots(value: int | None) -> str:
    if value is None:
        return "資料未取得"
    return f"{value:,} 張"


def _trailing_streak(history: list[dict], key: str) -> tuple[int, int]:
    """Return (direction, count) of the trailing same-sign run in ``key``."""
    if not history:
        return 0, 0
    signs = [_sign(_to_float(row.get(key))) for row in history]
    last = signs[-1]
    if last == 0:
        return 0, 0
    count = 0
    for sign in reversed(signs):
        if sign == last:
            count += 1
        else:
            break
    return last, count


DIRECTION_LABEL = {1: "買超", -1: "賣超", 0: "中性"}

INSTITUTIONAL_LABEL = {
    "bullish": "三大法人一致買超",
    "bearish": "三大法人一致賣超",
    "mixed": "三大法人方向分歧",
    "neutral": "三大法人買賣中性",
    "unknown": "法人資料不足",
}

CHIP_REGIME_LABEL = {
    "accumulation": "籌碼偏多（法人/價格同步偏多）",
    "distribution": "籌碼偏空（賣壓或量價背離）",
    "mixed": "籌碼中性混雜",
    "unknown": "籌碼型態資料不足",
}

# 短中線技術面對齊（MA5 vs MA20），純均線位置、不涉回測
MA_ALIGNMENT_LABEL = {
    "bullish": "短中線同步偏多（收盤站上 MA5 與 MA20）",
    "bearish": "短中線同步偏空（收盤跌破 MA5 與 MA20）",
    "short_rebound": "短線反彈、中期仍弱（站上 MA5 但仍在 MA20 下方）",
    "short_pullback": "短線回檔、中期仍強（跌破 MA5 但仍在 MA20 上方）",
    "neutral": "短中線方向不明（均線糾結或貼近）",
    "unknown": "均線資料不足",
}

# 三均線改為兩兩對齊（B）：MA5 vs MA10（更短線）、MA10 vs MA20（短中線）
MA_SHORT_ALIGN_LABEL = {
    "bullish": "短線偏多（收盤站上 MA5 與 MA10）",
    "bearish": "短線偏空（收盤跌破 MA5 與 MA10）",
    "short_rebound": "更短線轉強、短線仍弱（站上 MA5 但仍在 MA10 下方）",
    "short_pullback": "更短線回檔、短線仍強（跌破 MA5 但仍在 MA10 上方）",
    "neutral": "短線方向不明（MA5/MA10 糾結或貼近）",
    "unknown": "MA5/MA10 資料不足",
}

MA_MID_ALIGN_LABEL = {
    "bullish": "短中線偏多（收盤站上 MA10 與 MA20）",
    "bearish": "短中線偏空（收盤跌破 MA10 與 MA20）",
    "short_rebound": "短線反彈、中期仍弱（站上 MA10 但仍在 MA20 下方）",
    "short_pullback": "短線回檔、中期仍強（跌破 MA10 但仍在 MA20 上方）",
    "neutral": "短中線方向不明（MA10/MA20 糾結或貼近）",
    "unknown": "MA10/MA20 資料不足",
}

INTENSITY_LABEL = {
    "high": "偏高",
    "normal": "正常",
    "low": "偏低",
    "unknown": "資料不足",
}

MARKET_TREND_LABEL = {
    "up": "大盤偏多（區間上漲）",
    "down": "大盤偏空（區間下跌）",
    "flat": "大盤區間震盪",
    "unknown": "大盤資料不足",
}

RS_LABEL = {
    "outperform": "強於大盤",
    "underperform": "弱於大盤",
    "inline": "與大盤同步",
    "unknown": "相對強弱資料不足",
}


def _position_from_deviation(deviation: float | None) -> str:
    if deviation is None:
        return "unknown"
    if deviation > 0.3:
        return "above"
    if deviation < -0.3:
        return "below"
    return "at"


def _ma_alignment(ma5_position: str, ma20_position: str) -> str:
    """短中線技術面對齊：僅用 MA5 與 MA20 的相對位置，不涉回測。"""
    if ma5_position == "unknown" or ma20_position == "unknown":
        return "unknown"
    if ma5_position == "above" and ma20_position == "above":
        return "bullish"
    if ma5_position == "below" and ma20_position == "below":
        return "bearish"
    if ma5_position == "above" and ma20_position == "below":
        return "short_rebound"
    if ma5_position == "below" and ma20_position == "above":
        return "short_pullback"
    return "neutral"


def _ma_pair_alignment(short_position: str, long_position: str) -> str:
    """兩均線對齊（僅用 above/below/at/unknown，不涉回測）。"""
    if short_position == "unknown" or long_position == "unknown":
        return "unknown"
    if short_position == "above" and long_position == "above":
        return "bullish"
    if short_position == "below" and long_position == "below":
        return "bearish"
    if short_position == "above" and long_position == "below":
        return "short_rebound"
    if short_position == "below" and long_position == "above":
        return "short_pullback"
    return "neutral"


def _relative_strength(stock_pct: float | None, market_pct: float | None, *, band: float) -> str:
    if stock_pct is None or market_pct is None:
        return "unknown"
    diff = stock_pct - market_pct
    if diff > band:
        return "outperform"
    if diff < -band:
        return "underperform"
    return "inline"


@dataclass
class ChipFacts:
    stock_id: str
    stock_name: str
    trade_date: str

    # 當日方向（張）
    foreign_net_lots: int | None
    trust_net_lots: int | None
    dealer_net_lots: int | None
    major_net_lots: int | None
    major_available: bool

    today_change_pct: float | None
    close_vs_ma5_pct: float | None
    close_vs_ma10_pct: float | None
    close_vs_ma20_pct: float | None
    margin_today_delta_lots: int | None
    volume_today_lots: int | None
    day_trade_ratio_pct: float | None

    # 區間趨勢
    lookback_days: int | None
    period_return_pct: float | None
    foreign_cum_lots: int | None
    major_cum_lots: int | None
    margin_delta_lots: int | None
    short_delta_lots: int | None
    avg_volume_lots: int | None
    avg_day_trade_ratio_pct: float | None
    borrow_sell_today_lots: int | None

    # 規則化判定（v1）
    foreign_direction: int  # -1/0/1 當日
    foreign_streak_dir: int
    foreign_streak_days: int
    price_trend: str  # "up" | "down" | "flat" | "unknown"
    ma5_position: str  # "above" | "below" | "at" | "unknown"
    ma10_position: str  # "above" | "below" | "at" | "unknown"
    ma20_position: str  # "above" | "below" | "at" | "unknown"
    ma_alignment: str  # bullish/bearish/short_rebound/short_pullback/neutral/unknown（legacy: MA5 vs MA20）
    ma_short_alignment: str  # MA5 vs MA10
    ma_mid_alignment: str  # MA10 vs MA20
    divergences: list[str] = field(default_factory=list)

    # 規則化判定（v2）
    institutional_consensus: str = "unknown"
    major_foreign_divergence: bool = False
    margin_short_regime: str = "unknown"
    day_trade_intensity: str = "unknown"
    borrow_pressure: str = "unknown"
    volume_anomaly: str = "unknown"
    chip_regime: str = "unknown"

    # 大盤脈絡（v3：加權指數 TAIEX）
    market_close: float | None = None
    market_change_pct: float | None = None
    market_period_return_pct: float | None = None
    market_trend: str = "unknown"  # "up" | "down" | "flat" | "unknown"
    market_ma5_position: str = "unknown"  # above/below/at/unknown
    market_ma20_position: str = "unknown"
    rs_today: str = "unknown"  # outperform/underperform/inline/unknown（vs 大盤）
    rs_period: str = "unknown"

    anchors: list[str] = field(default_factory=list)


def _institutional_consensus(
    foreign: int | None,
    trust: int | None,
    dealer: int | None,
) -> str:
    signs = [_sign(value) for value in (foreign, trust, dealer) if value is not None]
    if not signs:
        return "unknown"
    active = [sign for sign in signs if sign != 0]
    if not active:
        return "neutral"
    if all(sign > 0 for sign in active):
        return "bullish"
    if all(sign < 0 for sign in active):
        return "bearish"
    return "mixed"


def _margin_short_regime(
    margin_delta: int | None,
    short_delta: int | None,
) -> str:
    margin_sign = _sign(margin_delta, deadband=50)
    short_sign = _sign(short_delta, deadband=10)
    if margin_sign == 0 and short_sign == 0:
        return "neutral"
    if margin_sign > 0 and short_sign <= 0:
        return "margin_up"
    if margin_sign < 0 and short_sign >= 0:
        return "margin_down"
    if short_sign > 0 and margin_sign <= 0:
        return "short_up"
    if margin_sign > 0 and short_sign > 0:
        return "margin_up_short_up"
    if margin_sign < 0 and short_sign < 0:
        return "margin_down_short_down"
    return "mixed"


MARGIN_SHORT_LABEL = {
    "neutral": "融資券餘額變化不大",
    "margin_up": "融資餘額增加（散戶槓桿偏多）",
    "margin_down": "融資餘額減少",
    "short_up": "融券餘額增加（空方力道增）",
    "margin_up_short_up": "融資、融券同步增加（多空雙增，籌碼換手）",
    "margin_down_short_down": "融資、融券同步減少",
    "mixed": "融資券變化方向混雜",
    "unknown": "融資券資料不足",
}


def _day_trade_intensity(
    today_ratio: float | None,
    avg_ratio: float | None,
) -> str:
    if today_ratio is None:
        return "unknown"
    if avg_ratio is not None and avg_ratio > 0:
        if today_ratio >= avg_ratio * 1.25 or today_ratio >= 45:
            return "high"
        if today_ratio <= avg_ratio * 0.75:
            return "low"
        return "normal"
    if today_ratio >= 40:
        return "high"
    if today_ratio <= 15:
        return "low"
    return "normal"


def _borrow_pressure(history: list[dict], today_borrow: int | None) -> str:
    if today_borrow is None:
        return "unknown"
    if not history:
        if today_borrow >= 500:
            return "high"
        if today_borrow <= 50:
            return "low"
        return "neutral"
    recent = [
        _to_int(row.get("借券賣出_張"))
        for row in history[-3:]
        if _to_int(row.get("借券賣出_張")) is not None
    ]
    if not recent:
        return "neutral"
    avg_recent = sum(recent) / len(recent)
    if today_borrow >= max(avg_recent * 1.4, avg_recent + 200):
        return "high"
    if today_borrow <= min(avg_recent * 0.6, max(avg_recent - 100, 0)):
        return "low"
    return "neutral"


BORROW_LABEL = {
    "high": "借券賣出偏高（空方壓力增）",
    "low": "借券賣出偏低",
    "neutral": "借券賣出正常",
    "unknown": "借券資料不足",
}


def _volume_anomaly(
    today_volume: int | None,
    avg_volume: int | None,
) -> str:
    if today_volume is None or avg_volume is None or avg_volume <= 0:
        return "unknown"
    ratio = today_volume / avg_volume
    if ratio >= 1.5:
        return "spike"
    if ratio <= 0.65:
        return "shrink"
    return "normal"


VOLUME_ANOMALY_LABEL = {
    "spike": "成交量明顯放大",
    "shrink": "成交量明顯萎縮",
    "normal": "成交量接近區間均值",
    "unknown": "成交量資料不足",
}


def _detect_chip_regime(
    *,
    foreign_direction: int,
    foreign_cum: int | None,
    institutional_consensus: str,
    price_trend: str,
    divergences: list[str],
    major_foreign_divergence: bool,
    margin_short_regime: str,
) -> str:
    score = 0
    if foreign_cum is not None:
        if foreign_cum >= 300:
            score += 1
        elif foreign_cum <= -300:
            score -= 1
    if institutional_consensus == "bullish":
        score += 1
    elif institutional_consensus == "bearish":
        score -= 1
    if price_trend == "up" and foreign_direction > 0:
        score += 1
    elif price_trend == "down" and foreign_direction < 0:
        score -= 1
    if "price_up_foreign_sell" in divergences:
        score -= 2
    if "price_down_margin_up" in divergences:
        score -= 1
    if major_foreign_divergence:
        score -= 1
    if margin_short_regime in {"margin_up", "margin_up_short_up"} and price_trend == "down":
        score -= 1

    if score >= 2:
        return "accumulation"
    if score <= -2:
        return "distribution"
    if score == 0 and not divergences:
        return "mixed"
    return "mixed"


def build_chip_facts(row: dict, history: list[dict] | None = None) -> ChipFacts:
    history = history or []

    stock_id = str(row.get("代碼", "")).strip()
    stock_name = str(row.get("名稱", "")).strip()
    trade_date = str(row.get("日期", "")).strip()

    foreign_net = _to_int(row.get("外資買賣超_張"))
    trust_net = _to_int(row.get("投信買賣超_張"))
    dealer_net = _to_int(row.get("自營商買賣超_張"))
    major_available = str(row.get("主力_擷取狀態", "")).strip().lower() == "ok"
    major_net = _to_int(row.get("主力買賣超_張")) if major_available else None

    today_change = _to_float(row.get("漲跌幅"))
    close_vs_ma5 = _to_float(row.get("收盤偏離MA5_%"))
    close_vs_ma10 = _to_float(row.get("收盤偏離MA10_%"))
    close_vs_ma20 = _to_float(row.get("收盤偏離MA20_%"))
    margin_today_delta = _to_int(row.get("融資增減_張"))
    volume_today = _to_int(row.get("成交量_張"))
    day_trade_ratio = _to_float(row.get("當沖佔成交量_%"))
    borrow_sell_today = _to_int(row.get("借券賣出_張"))

    lookback = _to_int(row.get("回看天數"))
    period_return = _to_float(row.get("區間漲跌幅_%"))
    foreign_cum = _to_int(row.get("區間外資累計_張"))
    major_cum = _to_int(row.get("區間主力累計_張"))
    margin_delta = _to_int(row.get("區間融資餘額淨變化_張"))
    short_delta = _to_int(row.get("區間融券餘額淨變化_張"))
    avg_volume = _to_int(row.get("區間成交量均值_張"))
    avg_day_trade_ratio = _to_float(row.get("區間當沖佔比均值_%"))

    foreign_direction = _sign(foreign_net)
    streak_dir, streak_days = _trailing_streak(history, "外資買賣超_張")

    if period_return is None:
        price_trend = "unknown"
    elif period_return > 2:
        price_trend = "up"
    elif period_return < -2:
        price_trend = "down"
    else:
        price_trend = "flat"

    if close_vs_ma5 is None:
        ma5_position = "unknown"
    elif close_vs_ma5 > 0.3:
        ma5_position = "above"
    elif close_vs_ma5 < -0.3:
        ma5_position = "below"
    else:
        ma5_position = "at"

    ma10_position = _position_from_deviation(close_vs_ma10)
    ma20_position = _position_from_deviation(close_vs_ma20)
    ma_alignment = _ma_alignment(ma5_position, ma20_position)
    ma_short_alignment = _ma_pair_alignment(ma5_position, ma10_position)
    ma_mid_alignment = _ma_pair_alignment(ma10_position, ma20_position)

    divergences = _detect_divergences(
        today_change=today_change,
        foreign_net=foreign_net,
        margin_today_delta=margin_today_delta,
        period_return=period_return,
        margin_delta=margin_delta,
    )

    institutional = _institutional_consensus(foreign_net, trust_net, dealer_net)
    major_foreign_div = (
        major_available
        and major_net is not None
        and foreign_net is not None
        and _sign(major_net) != 0
        and _sign(foreign_net) != 0
        and _sign(major_net) != _sign(foreign_net)
    )
    market_close = _to_float(row.get("大盤收盤"))
    market_change = _to_float(row.get("大盤漲跌幅_%"))
    market_period = _to_float(row.get("大盤區間漲跌幅_%"))
    market_dev_ma5 = _to_float(row.get("大盤收盤偏離MA5_%"))
    market_dev_ma20 = _to_float(row.get("大盤收盤偏離MA20_%"))

    if market_period is None:
        market_trend = "unknown"
    elif market_period > 2:
        market_trend = "up"
    elif market_period < -2:
        market_trend = "down"
    else:
        market_trend = "flat"
    market_ma5_position = _position_from_deviation(market_dev_ma5)
    market_ma20_position = _position_from_deviation(market_dev_ma20)

    # 個股當日漲跌% 由收盤與漲跌點數（spread）反推，供與大盤同基準比較
    close_price = _to_float(row.get("收盤價"))
    spread = _to_float(row.get("漲跌幅"))
    stock_today_pct: float | None = None
    if close_price is not None and spread is not None:
        prev_close = close_price - spread
        if prev_close:
            stock_today_pct = spread / prev_close * 100.0
    rs_today = _relative_strength(stock_today_pct, market_change, band=0.5)
    rs_period = _relative_strength(period_return, market_period, band=1.0)

    margin_short = _margin_short_regime(margin_delta, short_delta)
    day_trade = _day_trade_intensity(day_trade_ratio, avg_day_trade_ratio)
    borrow = _borrow_pressure(history, borrow_sell_today)
    volume_flag = _volume_anomaly(volume_today, avg_volume)
    chip_regime = _detect_chip_regime(
        foreign_direction=foreign_direction,
        foreign_cum=foreign_cum,
        institutional_consensus=institutional,
        price_trend=price_trend,
        divergences=divergences,
        major_foreign_divergence=major_foreign_div,
        margin_short_regime=margin_short,
    )

    facts = ChipFacts(
        stock_id=stock_id,
        stock_name=stock_name,
        trade_date=trade_date,
        foreign_net_lots=foreign_net,
        trust_net_lots=trust_net,
        dealer_net_lots=dealer_net,
        major_net_lots=major_net,
        major_available=major_available,
        today_change_pct=today_change,
        close_vs_ma5_pct=close_vs_ma5,
        close_vs_ma10_pct=close_vs_ma10,
        close_vs_ma20_pct=close_vs_ma20,
        margin_today_delta_lots=margin_today_delta,
        volume_today_lots=volume_today,
        day_trade_ratio_pct=day_trade_ratio,
        lookback_days=lookback,
        period_return_pct=period_return,
        foreign_cum_lots=foreign_cum,
        major_cum_lots=major_cum,
        margin_delta_lots=margin_delta,
        short_delta_lots=short_delta,
        avg_volume_lots=avg_volume,
        avg_day_trade_ratio_pct=avg_day_trade_ratio,
        borrow_sell_today_lots=borrow_sell_today,
        foreign_direction=foreign_direction,
        foreign_streak_dir=streak_dir,
        foreign_streak_days=streak_days,
        price_trend=price_trend,
        ma5_position=ma5_position,
        ma10_position=ma10_position,
        ma20_position=ma20_position,
        ma_alignment=ma_alignment,
        ma_short_alignment=ma_short_alignment,
        ma_mid_alignment=ma_mid_alignment,
        divergences=divergences,
        institutional_consensus=institutional,
        major_foreign_divergence=major_foreign_div,
        margin_short_regime=margin_short,
        day_trade_intensity=day_trade,
        borrow_pressure=borrow,
        volume_anomaly=volume_flag,
        chip_regime=chip_regime,
        market_close=market_close,
        market_change_pct=round(market_change, 2) if market_change is not None else None,
        market_period_return_pct=(
            round(market_period, 2) if market_period is not None else None
        ),
        market_trend=market_trend,
        market_ma5_position=market_ma5_position,
        market_ma20_position=market_ma20_position,
        rs_today=rs_today,
        rs_period=rs_period,
    )
    facts.anchors = _build_anchors(facts)
    return facts


def _detect_divergences(
    *,
    today_change: float | None,
    foreign_net: int | None,
    margin_today_delta: int | None,
    period_return: float | None,
    margin_delta: int | None,
) -> list[str]:
    flags: list[str] = []
    if today_change is not None and foreign_net is not None:
        if today_change > 0 and foreign_net < 0:
            flags.append("price_up_foreign_sell")
        elif today_change < 0 and foreign_net > 0:
            flags.append("price_down_foreign_buy")
    if period_return is not None and margin_delta is not None:
        if period_return < 0 and margin_delta > 0:
            flags.append("price_down_margin_up")
        elif (
            period_return > 0
            and margin_delta > 0
            and today_change is not None
            and today_change > 0
        ):
            flags.append("price_up_margin_up")
    return flags


DIVERGENCE_LABEL = {
    "price_up_foreign_sell": "當日價漲但外資賣超（量價背離，追高需留意）",
    "price_down_foreign_buy": "當日價跌但外資買超（可能逢低承接）",
    "price_down_margin_up": "區間價跌但融資餘額增加（散戶不認賠，籌碼偏壓）",
    "price_up_margin_up": "區間價漲且融資同步增加（追價籌碼偏浮動）",
}


def _build_anchors(facts: ChipFacts) -> list[str]:
    anchors: list[str] = []

    if facts.foreign_net_lots is not None:
        anchors.append(
            f"外資今日{DIRECTION_LABEL[facts.foreign_direction]}"
            + (
                f" {abs(facts.foreign_net_lots):,} 張"
                if facts.foreign_direction != 0
                else ""
            )
        )
    if facts.foreign_streak_days >= 2 and facts.foreign_streak_dir != 0:
        anchors.append(
            f"外資近 {facts.foreign_streak_days} 日連續"
            f"{DIRECTION_LABEL[facts.foreign_streak_dir]}"
        )
    if facts.institutional_consensus not in {"unknown", "neutral"}:
        anchors.append(INSTITUTIONAL_LABEL[facts.institutional_consensus])
    if facts.major_available and facts.major_net_lots is not None:
        direction = _sign(facts.major_net_lots)
        anchors.append(
            f"主力今日{DIRECTION_LABEL[direction]}"
            + (f" {abs(facts.major_net_lots):,} 張" if direction != 0 else "")
        )
    elif not facts.major_available:
        anchors.append("主力進出資料未取得")
    if facts.major_foreign_divergence:
        anchors.append("主力與外資方向背離（籌碼分歧）")

    if facts.ma5_position == "above" and facts.close_vs_ma5_pct is not None:
        anchors.append(f"收盤高於 MA5 約 {facts.close_vs_ma5_pct:.1f}%")
    elif facts.ma5_position == "below" and facts.close_vs_ma5_pct is not None:
        anchors.append(f"收盤低於 MA5 約 {abs(facts.close_vs_ma5_pct):.1f}%")

    if facts.ma10_position == "above" and facts.close_vs_ma10_pct is not None:
        anchors.append(f"收盤高於 MA10（10 日線）約 {facts.close_vs_ma10_pct:.1f}%")
    elif facts.ma10_position == "below" and facts.close_vs_ma10_pct is not None:
        anchors.append(
            f"收盤低於 MA10（10 日線）約 {abs(facts.close_vs_ma10_pct):.1f}%"
        )

    if facts.ma20_position == "above" and facts.close_vs_ma20_pct is not None:
        anchors.append(f"收盤高於 MA20（月線）約 {facts.close_vs_ma20_pct:.1f}%")
    elif facts.ma20_position == "below" and facts.close_vs_ma20_pct is not None:
        anchors.append(f"收盤低於 MA20（月線）約 {abs(facts.close_vs_ma20_pct):.1f}%")

    if facts.ma_alignment in {
        "bullish",
        "bearish",
        "short_rebound",
        "short_pullback",
    }:
        anchors.append(MA_ALIGNMENT_LABEL[facts.ma_alignment])

    if facts.ma_short_alignment in {
        "bullish",
        "bearish",
        "short_rebound",
        "short_pullback",
    }:
        anchors.append(MA_SHORT_ALIGN_LABEL[facts.ma_short_alignment])

    if facts.ma_mid_alignment in {
        "bullish",
        "bearish",
        "short_rebound",
        "short_pullback",
    }:
        anchors.append(MA_MID_ALIGN_LABEL[facts.ma_mid_alignment])

    if facts.price_trend in {"up", "down"} and facts.period_return_pct is not None:
        move = "上漲" if facts.price_trend == "up" else "下跌"
        days = f"{facts.lookback_days} 日" if facts.lookback_days else "區間"
        anchors.append(f"近 {days}{move} {abs(facts.period_return_pct):.1f}%")

    if facts.chip_regime in {"accumulation", "distribution"}:
        anchors.append(CHIP_REGIME_LABEL[facts.chip_regime])

    if facts.market_trend != "unknown" and facts.market_period_return_pct is not None:
        anchors.append(
            f"{MARKET_TREND_LABEL[facts.market_trend]}"
            f"（大盤區間 {facts.market_period_return_pct:+.1f}%）"
        )
    rs = facts.rs_period if facts.rs_period != "unknown" else facts.rs_today
    if rs in {"outperform", "underperform"}:
        anchors.append(f"個股{RS_LABEL[rs]}（相對強弱）")

    if facts.margin_short_regime not in {"neutral", "unknown"}:
        label = MARGIN_SHORT_LABEL.get(facts.margin_short_regime)
        if label:
            anchors.append(label)

    if facts.day_trade_intensity == "high":
        anchors.append("當沖佔比偏高（短線投機升溫）")
    if facts.volume_anomaly == "spike":
        anchors.append(VOLUME_ANOMALY_LABEL["spike"])
    elif facts.volume_anomaly == "shrink":
        anchors.append(VOLUME_ANOMALY_LABEL["shrink"])
    if facts.borrow_pressure == "high":
        anchors.append(BORROW_LABEL["high"])

    for flag in facts.divergences:
        label = DIVERGENCE_LABEL.get(flag)
        if label:
            anchors.append(label)

    return anchors


def facts_to_json(facts: ChipFacts) -> dict:
    return asdict(facts)


def facts_summary_for_prompt(facts: ChipFacts) -> str:
    """Human-readable, LLM-facing summary of the deterministic facts."""
    lines = [
        f"股票：{facts.stock_name}（{facts.stock_id}），資料日期 {facts.trade_date}",
        "",
        "【當日方向（系統判定，勿與之矛盾）】",
        f"- 外資：{DIRECTION_LABEL[facts.foreign_direction]}"
        f"（{_fmt_lots(facts.foreign_net_lots)}）",
        f"- 投信：{_fmt_lots(facts.trust_net_lots)}",
        f"- 自營商：{_fmt_lots(facts.dealer_net_lots)}",
        f"- 主力：{_fmt_lots(facts.major_net_lots) if facts.major_available else '資料未取得'}",
        f"- 三大法人共識：{INSTITUTIONAL_LABEL[facts.institutional_consensus]}",
    ]
    if facts.major_foreign_divergence:
        lines.append("- 主力 vs 外資：**方向背離**（趨勢段須說明）")
    if facts.today_change_pct is not None:
        lines.append(f"- 當日漲跌幅：{facts.today_change_pct:+.2f}%")
    if facts.ma5_position != "unknown" and facts.close_vs_ma5_pct is not None:
        pos = {"above": "高於", "below": "低於", "at": "貼近"}[facts.ma5_position]
        lines.append(f"- 收盤{pos} MA5（偏離 {facts.close_vs_ma5_pct:+.2f}%）")
    if facts.close_vs_ma10_pct is not None and facts.ma10_position != "unknown":
        pos = {"above": "高於", "below": "低於", "at": "貼近"}[facts.ma10_position]
        lines.append(f"- 收盤{pos} MA10（10 日線，偏離 {facts.close_vs_ma10_pct:+.2f}%）")
    if facts.ma20_position != "unknown" and facts.close_vs_ma20_pct is not None:
        pos = {"above": "高於", "below": "低於", "at": "貼近"}[facts.ma20_position]
        lines.append(f"- 收盤{pos} MA20（月線，偏離 {facts.close_vs_ma20_pct:+.2f}%）")
    if facts.ma_alignment != "unknown":
        lines.append(f"- 短中線技術面：{MA_ALIGNMENT_LABEL[facts.ma_alignment]}")
    if getattr(facts, "ma_short_alignment", "unknown") != "unknown":
        lines.append(f"- MA5 vs MA10：{MA_SHORT_ALIGN_LABEL[facts.ma_short_alignment]}")
    if getattr(facts, "ma_mid_alignment", "unknown") != "unknown":
        lines.append(f"- MA10 vs MA20：{MA_MID_ALIGN_LABEL[facts.ma_mid_alignment]}")
    if facts.day_trade_intensity != "unknown":
        lines.append(
            f"- 當沖熱度：{INTENSITY_LABEL[facts.day_trade_intensity]}"
            + (
                f"（今日 {facts.day_trade_ratio_pct:.1f}%）"
                if facts.day_trade_ratio_pct is not None
                else ""
            )
        )
    if facts.volume_anomaly != "unknown":
        lines.append(f"- 成交量：{VOLUME_ANOMALY_LABEL[facts.volume_anomaly]}")

    lines.extend(["", "【區間趨勢】"])
    if facts.foreign_streak_days >= 2 and facts.foreign_streak_dir != 0:
        lines.append(
            f"- 外資近 {facts.foreign_streak_days} 日連續"
            f"{DIRECTION_LABEL[facts.foreign_streak_dir]}"
        )
    trend_label = {
        "up": "偏多（區間上漲）",
        "down": "偏空（區間下跌）",
        "flat": "區間震盪",
        "unknown": "資料不足",
    }[facts.price_trend]
    if facts.period_return_pct is not None:
        lines.append(f"- 價格趨勢：{trend_label}（{facts.period_return_pct:+.2f}%）")
    else:
        lines.append(f"- 價格趨勢：{trend_label}")
    lines.append(f"- 外資區間累計：{_fmt_lots(facts.foreign_cum_lots)}")
    lines.append(f"- 主力區間累計：{_fmt_lots(facts.major_cum_lots)}")
    lines.append(f"- 融資餘額淨變化：{_fmt_lots(facts.margin_delta_lots)}")
    if facts.margin_short_regime != "unknown":
        lines.append(
            f"- 融資券型態：{MARGIN_SHORT_LABEL.get(facts.margin_short_regime, facts.margin_short_regime)}"
        )
    if facts.borrow_pressure != "unknown":
        lines.append(f"- 借券壓力：{BORROW_LABEL[facts.borrow_pressure]}")

    if facts.market_trend != "unknown" or facts.rs_period != "unknown":
        lines.extend(["", "【大盤脈絡（加權指數 TAIEX）】"])
        if facts.market_change_pct is not None:
            lines.append(f"- 大盤當日漲跌幅：{facts.market_change_pct:+.2f}%")
        if facts.market_period_return_pct is not None:
            lines.append(
                f"- 大盤趨勢：{MARKET_TREND_LABEL[facts.market_trend]}"
                f"（{facts.market_period_return_pct:+.2f}%）"
            )
        if facts.market_ma5_position != "unknown":
            pos = {"above": "站上", "below": "跌破", "at": "貼近"}[
                facts.market_ma5_position
            ]
            lines.append(f"- 大盤{pos} MA5")
        rs = facts.rs_period if facts.rs_period != "unknown" else facts.rs_today
        if rs != "unknown":
            lines.append(
                f"- 個股相對大盤：**{RS_LABEL[rs]}**"
                "（交叉/趨勢段須與此一致，勿把弱於大盤寫成抗跌）"
            )

    lines.extend(
        ["", f"【籌碼型態】{CHIP_REGIME_LABEL.get(facts.chip_regime, facts.chip_regime)}"]
    )

    if facts.divergences:
        lines.extend(["", "【背離/風險旗標】"])
        for flag in facts.divergences:
            lines.append(f"- {DIVERGENCE_LABEL.get(flag, flag)}")

    if facts.anchors:
        lines.extend(["", "【必須在正文中引用的關鍵事實 anchors（至少 2 條）】"])
        for anchor in facts.anchors:
            lines.append(f"- {anchor}")

    return "\n".join(lines)


def write_facts_json(path, facts: ChipFacts) -> None:
    from pathlib import Path

    Path(path).write_text(
        json.dumps(facts_to_json(facts), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
