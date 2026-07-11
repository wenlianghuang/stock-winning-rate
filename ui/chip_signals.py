"""Deterministic chip signals (facts) computed from a stock CSV row + history.

This is the *harness* layer: Python owns the factual verdicts (directions,
streaks, divergences, regime labels) so the LLM only has to explain them. The
same facts feed both the agy prompt and the fact-consistency validation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


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

# 均線本身排列（MA5 vs MA10 vs MA20 數值大小），與收盤相對均線位置不同
MA_STACK_LABEL = {
    "bullish_stack": "均線多頭排列（MA5 > MA10 > MA20）",
    "bearish_stack": "均線空頭排列（MA5 < MA10 < MA20）",
    "mixed": "均線糾結（未形成明確多/空頭排列）",
    "unknown": "均線排列資料不足",
}

MA20_SLOPE_LABEL = {
    "rising": "月線（MA20）趨勢向上",
    "falling": "月線（MA20）趨勢向下",
    "flat": "月線（MA20）走勢平穩",
    "unknown": "月線斜率資料不足",
}

RSI_ZONE_LABEL = {
    "overbought": "RSI 偏高（動能過熱，留意回檔）",
    "oversold": "RSI 偏低（動能偏弱，留意反彈）",
    "neutral": "RSI 中性區",
    "unknown": "RSI 資料不足",
}

VOLATILITY_REGIME_LABEL = {
    "high": "波動偏高（ATR 擴大，停損宜保守）",
    "normal": "波動正常",
    "low": "波動偏低（區間參考較可靠）",
    "unknown": "波動資料不足",
}

TREND_STRENGTH_LABEL = {
    "strong": "趨勢明確（ADX 偏高，均線方向較可信）",
    "weak": "趨勢偏弱（ADX 偏低，易震盪盤整）",
    "neutral": "趨勢強度中性",
    "unknown": "ADX 資料不足",
}

MARGIN_SHORT_RATIO_ZONE_LABEL = {
    "high": "券資比偏高（融券相對融資壓力較大）",
    "low": "券資比偏低（融券壓力較小）",
    "neutral": "券資比中性",
    "unknown": "券資比資料不足",
}

MARGIN_MOMENTUM_LABEL = {
    "heating": "融資動能偏強（區間融資餘額增加，散戶槓桿升溫）",
    "cooling": "融資動能偏弱（區間融資餘額減少）",
    "stable": "融資動能平穩",
    "unknown": "融資動能資料不足",
}

MA_CROSS_LABEL = {
    "golden": "黃金交叉",
    "death": "死亡交叉",
    "none": "近3日無均線交叉",
    "unknown": "均線交叉資料不足",
}

MA_CROSS_PAIR_LABEL = {
    ("golden", "ma5_ma10"): "MA5 黃金交叉 MA10（短線均線上穿）",
    ("death", "ma5_ma10"): "MA5 死亡交叉 MA10（短線均線下穿）",
    ("golden", "ma10_ma20"): "MA10 黃金交叉 MA20（短中線均線上穿）",
    ("death", "ma10_ma20"): "MA10 死亡交叉 MA20（短中線均線下穿）",
}

MA_CROSS_RECENCY_LABEL = {
    "today": "當日",
    "within_3d": "近3日內",
    "none": "無",
    "unknown": "資料不足",
}

MA_CROSS_CSV_LABEL = {
    "黃金交叉": "golden",
    "死亡交叉": "death",
    "無": "none",
}

MA20_SLOPE_THRESHOLD_PCT = 0.5
MA5_PERIOD = 5
MA10_PERIOD = 10
MA20_PERIOD = 20
MA20_SLOPE_LAG = 5
MA_CROSS_LOOKBACK_DAYS = 3
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
ATR_PERIOD = 14
ATR_VOLATILITY_LOOKBACK = 20
ATR_VOLATILITY_HIGH_RATIO = 1.25
ATR_VOLATILITY_LOW_RATIO = 0.75
ATR_VOLATILITY_HIGH_PCT = 3.5
ATR_VOLATILITY_LOW_PCT = 1.5
ADX_PERIOD = 14
ADX_STRONG_THRESHOLD = 25.0
ADX_WEAK_THRESHOLD = 20.0
MARGIN_SHORT_RATIO_HIGH_PCT = 25.0
MARGIN_SHORT_RATIO_LOW_PCT = 8.0
MARGIN_MOMENTUM_HEATING_PCT = 3.0
MARGIN_MOMENTUM_COOLING_PCT = -3.0
RANGE_PERIOD = 20
RANGE_NEAR_BAND_PCT = 2.0

# 自適應校準（由 tools/calibrate_thresholds.py 產出，缺檔則 fallback 到上方常數）。
MIN_CALIBRATION_SAMPLE = 30


def _calibration_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "reports" / "calibration"


_STOCK_THRESHOLD_CACHE: dict[str, dict | None] = {}


def _load_stock_thresholds(stock_id: str) -> dict | None:
    """Per-stock percentile thresholds; None when absent or under-sampled."""
    if not stock_id:
        return None
    if stock_id in _STOCK_THRESHOLD_CACHE:
        return _STOCK_THRESHOLD_CACHE[stock_id]
    path = _calibration_dir() / f"{stock_id}.thresholds.json"
    data: dict | None = None
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict) and int(loaded.get("n_rsi", 0)) >= MIN_CALIBRATION_SAMPLE:
            data = loaded
    _STOCK_THRESHOLD_CACHE[stock_id] = data
    return data


def _threshold_value(thresholds: dict | None, key: str, default: float) -> float:
    if not thresholds:
        return default
    value = thresholds.get(key)
    return float(value) if isinstance(value, (int, float)) else default


def load_base_rates() -> dict | None:
    """Regime base rates from calibration; None when the file is absent."""
    path = _calibration_dir() / "base_rates.json"
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None

RANGE_POSITION_LABEL = {
    "near_high": "接近近20日高點（壓力區）",
    "near_low": "接近近20日低點（支撐區）",
    "mid": "處於近20日區間中段",
    "unknown": "區間高低資料不足",
}

BREAKOUT_LABEL = {
    "yes": "收盤突破近20日高（創區間新高）",
    "no": "未突破近20日高",
    "unknown": "突破判定資料不足",
}

BREAKDOWN_LABEL = {
    "yes": "收盤跌破近20日低（創區間新低）",
    "no": "未跌破近20日低",
    "unknown": "跌破判定資料不足",
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


def _moving_average(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ma_stack(ma5: float | None, ma10: float | None, ma20: float | None) -> str:
    """均線數值排列：MA5/MA10/MA20 的大小關係（非收盤相對位置）。"""
    if ma5 is None or ma10 is None or ma20 is None:
        return "unknown"
    if ma5 <= 0 or ma10 <= 0 or ma20 <= 0:
        return "unknown"
    if ma5 > ma10 > ma20:
        return "bullish_stack"
    if ma5 < ma10 < ma20:
        return "bearish_stack"
    return "mixed"


def _ma_series(closes: list[float], period: int) -> list[float | None]:
    series: list[float | None] = []
    for end in range(1, len(closes) + 1):
        window = closes[:end]
        if len(window) < period:
            series.append(None)
        else:
            series.append(sum(window[-period:]) / period)
    return series


def _cross_event_at_index(
    short_series: list[float | None],
    long_series: list[float | None],
    index: int,
) -> str:
    if index < 1:
        return "none"
    short_prev, long_prev = short_series[index - 1], long_series[index - 1]
    short_now, long_now = short_series[index], long_series[index]
    if None in (short_prev, long_prev, short_now, long_now):
        return "none"
    if short_prev <= long_prev and short_now > long_now:
        return "golden"
    if short_prev >= long_prev and short_now < long_now:
        return "death"
    return "none"


def _recent_ma_cross(
    closes: list[float],
    short_period: int,
    long_period: int,
    *,
    lookback_days: int = MA_CROSS_LOOKBACK_DAYS,
) -> dict[str, object]:
    unknown: dict[str, object] = {
        "cross": "unknown",
        "recency": "unknown",
        "days_ago": None,
    }
    min_len = max(short_period, long_period) + 1
    if len(closes) < min_len:
        return unknown

    short_series = _ma_series(closes, short_period)
    long_series = _ma_series(closes, long_period)
    last_index = len(closes) - 1
    start_index = max(1, last_index - lookback_days)

    for index in range(last_index, start_index - 1, -1):
        event = _cross_event_at_index(short_series, long_series, index)
        if event == "none":
            continue
        days_ago = last_index - index
        recency = "today" if days_ago == 0 else "within_3d"
        return {
            "cross": event,
            "recency": recency,
            "days_ago": days_ago,
        }

    return {"cross": "none", "recency": "none", "days_ago": None}


def _parse_ma_cross_csv(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return MA_CROSS_CSV_LABEL.get(text, "")


def _ma_cross_from_row(
    row: dict,
    closes: list[float],
    *,
    csv_key: str,
    short_period: int,
    long_period: int,
) -> dict[str, object]:
    parsed = _parse_ma_cross_csv(row.get(csv_key))
    if parsed == "golden":
        return {"cross": "golden", "recency": "today", "days_ago": 0}
    if parsed == "death":
        return {"cross": "death", "recency": "today", "days_ago": 0}
    return _recent_ma_cross(closes, short_period, long_period)


def _ma_crosses_from_row(row: dict, history: list[dict]) -> dict[str, object]:
    closes = _closes_from_row_and_history(row, history)
    ma5_ma10 = _ma_cross_from_row(
        row,
        closes,
        csv_key="MA5交叉MA10",
        short_period=MA5_PERIOD,
        long_period=MA10_PERIOD,
    )
    ma10_ma20 = _ma_cross_from_row(
        row,
        closes,
        csv_key="MA10交叉MA20",
        short_period=MA10_PERIOD,
        long_period=MA20_PERIOD,
    )
    return {
        "ma5_cross_ma10": ma5_ma10["cross"],
        "ma5_cross_recency": ma5_ma10["recency"],
        "ma10_cross_ma20": ma10_ma20["cross"],
        "ma10_cross_recency": ma10_ma20["recency"],
    }


def _ma20_slope_pct_from_closes(closes: list[float]) -> float | None:
    if len(closes) < MA20_PERIOD + MA20_SLOPE_LAG:
        return None
    ma_now = _moving_average(closes, MA20_PERIOD)
    ma_past = _moving_average(closes[:-MA20_SLOPE_LAG], MA20_PERIOD)
    if ma_now is None or ma_past is None or ma_past == 0:
        return None
    return round((ma_now - ma_past) / ma_past * 100, 2)


def _ma20_slope_label(slope_pct: float | None) -> str:
    if slope_pct is None:
        return "unknown"
    if slope_pct > MA20_SLOPE_THRESHOLD_PCT:
        return "rising"
    if slope_pct < -MA20_SLOPE_THRESHOLD_PCT:
        return "falling"
    return "flat"


def _rsi_wilder(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    if len(closes) < period + 1:
        return None
    avg_gain = 0.0
    avg_loss = 0.0
    for index in range(1, period + 1):
        delta = closes[index] - closes[index - 1]
        if delta > 0:
            avg_gain += delta
        else:
            avg_loss -= delta
    avg_gain /= period
    avg_loss /= period

    for index in range(period + 1, len(closes)):
        delta = closes[index] - closes[index - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def _rsi_zone(
    rsi: float | None,
    *,
    overbought: float = RSI_OVERBOUGHT,
    oversold: float = RSI_OVERSOLD,
) -> str:
    if rsi is None:
        return "unknown"
    if rsi >= overbought:
        return "overbought"
    if rsi <= oversold:
        return "oversold"
    return "neutral"


def _true_ranges(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[float]:
    if not highs or not lows or not closes:
        return []
    length = min(len(highs), len(lows), len(closes))
    trs: list[float] = []
    for index in range(length):
        if index == 0:
            trs.append(highs[index] - lows[index])
            continue
        tr = max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        trs.append(tr)
    return trs


def _atr_wilder(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = ATR_PERIOD,
) -> float | None:
    trs = _true_ranges(highs, lows, closes)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for index in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[index]) / period
    return round(atr, 4)


def _atr_pct(atr: float | None, close: float | None) -> float | None:
    if atr is None or close is None or close <= 0:
        return None
    return round(atr / close * 100, 2)


def _atr_pct_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    period: int = ATR_PERIOD,
) -> list[float]:
    trs = _true_ranges(highs, lows, closes)
    if len(trs) < period:
        return []
    atr = sum(trs[:period]) / period
    atr_pcts: list[float] = []
    close_index = period - 1
    if closes[close_index] > 0:
        atr_pcts.append(round(atr / closes[close_index] * 100, 2))
    for index in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[index]) / period
        close_index = index
        if closes[close_index] > 0:
            atr_pcts.append(round(atr / closes[close_index] * 100, 2))
    return atr_pcts


def _volatility_regime_fixed(
    atr_pct: float,
    *,
    high_pct: float = ATR_VOLATILITY_HIGH_PCT,
    low_pct: float = ATR_VOLATILITY_LOW_PCT,
) -> str:
    if atr_pct >= high_pct:
        return "high"
    if atr_pct <= low_pct:
        return "low"
    return "normal"


def _volatility_regime(
    atr_pct: float | None,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    high_pct: float = ATR_VOLATILITY_HIGH_PCT,
    low_pct: float = ATR_VOLATILITY_LOW_PCT,
) -> str:
    if atr_pct is None:
        return "unknown"
    atr_pcts = _atr_pct_series(highs, lows, closes)
    if len(atr_pcts) < 5:
        return _volatility_regime_fixed(atr_pct, high_pct=high_pct, low_pct=low_pct)
    recent = atr_pcts[-ATR_VOLATILITY_LOOKBACK:]
    avg_pct = sum(recent) / len(recent)
    if avg_pct <= 0:
        return "normal"
    ratio = atr_pct / avg_pct
    if ratio >= ATR_VOLATILITY_HIGH_RATIO:
        return "high"
    if ratio <= ATR_VOLATILITY_LOW_RATIO:
        return "low"
    return "normal"


def _adx_wilder(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = ADX_PERIOD,
) -> float | None:
    length = min(len(highs), len(lows), len(closes))
    if length < period * 2:
        return None

    plus_dm = [0.0]
    minus_dm = [0.0]
    tr = [0.0]
    for index in range(1, length):
        up = highs[index] - highs[index - 1]
        down = lows[index - 1] - lows[index]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )

    atr = sum(tr[1 : period + 1])
    smooth_plus = sum(plus_dm[1 : period + 1])
    smooth_minus = sum(minus_dm[1 : period + 1])

    dx_values: list[float] = []
    adx_values: list[float] = []

    for index in range(period, length):
        if index > period:
            atr = atr - atr / period + tr[index]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[index]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[index]

        if atr == 0:
            dx = 0.0
        else:
            plus_di = 100.0 * smooth_plus / atr
            minus_di = 100.0 * smooth_minus / atr
            denom = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0
        dx_values.append(dx)

        if len(dx_values) >= period:
            if not adx_values:
                adx_values.append(sum(dx_values[:period]) / period)
            else:
                adx_values.append(
                    (adx_values[-1] * (period - 1) + dx) / period
                )

    if not adx_values:
        return None
    return round(adx_values[-1], 2)


def _trend_strength(adx: float | None) -> str:
    if adx is None:
        return "unknown"
    if adx >= ADX_STRONG_THRESHOLD:
        return "strong"
    if adx <= ADX_WEAK_THRESHOLD:
        return "weak"
    return "neutral"


def _margin_short_ratio_pct(
    margin_lots: int | None,
    short_lots: int | None,
) -> float | None:
    if margin_lots is None or short_lots is None or margin_lots <= 0:
        return None
    return round(short_lots / margin_lots * 100, 2)


def _margin_momentum_pct(
    first_margin: int | None,
    last_margin: int | None,
) -> float | None:
    if first_margin is None or last_margin is None or first_margin <= 0:
        return None
    return round((last_margin - first_margin) / first_margin * 100, 2)


def _margin_short_ratio_zone(ratio_pct: float | None) -> str:
    if ratio_pct is None:
        return "unknown"
    if ratio_pct >= MARGIN_SHORT_RATIO_HIGH_PCT:
        return "high"
    if ratio_pct <= MARGIN_SHORT_RATIO_LOW_PCT:
        return "low"
    return "neutral"


def _margin_momentum_label(momentum_pct: float | None) -> str:
    if momentum_pct is None:
        return "unknown"
    if momentum_pct >= MARGIN_MOMENTUM_HEATING_PCT:
        return "heating"
    if momentum_pct <= MARGIN_MOMENTUM_COOLING_PCT:
        return "cooling"
    return "stable"


def _margin_balances_from_row_and_history(
    row: dict,
    history: list[dict],
) -> tuple[int | None, int | None]:
    margins: list[int] = []
    for hist_row in history:
        value = _to_int(hist_row.get("融資今日餘額_張"))
        if value is not None:
            margins.append(value)
    margin_today = _to_int(row.get("融資今日餘額_張"))
    if margin_today is not None:
        if not margins or margins[-1] != margin_today:
            margins.append(margin_today)
    if len(margins) < 2:
        return (margins[0] if margins else None), (margins[-1] if margins else None)
    return margins[0], margins[-1]


def _closes_from_row_and_history(row: dict, history: list[dict]) -> list[float]:
    closes: list[float] = []
    for hist_row in history:
        close = _to_float(hist_row.get("收盤價"))
        if close is not None:
            closes.append(close)
    close_today = _to_float(row.get("收盤價"))
    if close_today is not None:
        if not closes or closes[-1] != close_today:
            closes.append(close_today)
    return closes


def _ohlc_from_row_and_history(
    row: dict,
    history: list[dict],
) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for hist_row in history:
        high = _to_float(hist_row.get("最高價"))
        low = _to_float(hist_row.get("最低價"))
        close = _to_float(hist_row.get("收盤價"))
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
        if close is not None:
            closes.append(close)

    high_today = _to_float(row.get("最高價"))
    low_today = _to_float(row.get("最低價"))
    close_today = _to_float(row.get("收盤價"))
    if close_today is not None and (not closes or closes[-1] != close_today):
        if high_today is not None:
            highs.append(high_today)
        if low_today is not None:
            lows.append(low_today)
        closes.append(close_today)
    return highs, lows, closes


def _compute_range_levels(
    highs: list[float],
    lows: list[float],
    close: float | None,
    *,
    period: int = RANGE_PERIOD,
    near_band_pct: float = RANGE_NEAR_BAND_PCT,
) -> dict[str, object]:
    unknown: dict[str, object] = {
        "high_20d": None,
        "low_20d": None,
        "dist_to_20d_high_pct": None,
        "dist_to_20d_low_pct": None,
        "range_position": "unknown",
        "breakout_20d_high": "unknown",
        "breakdown_20d_low": "unknown",
    }
    if close is None or len(highs) < period or len(lows) < period:
        return unknown

    window_highs = highs[-period:]
    window_lows = lows[-period:]
    high_20d = max(window_highs)
    low_20d = min(window_lows)
    if high_20d <= 0 or low_20d <= 0:
        return unknown

    dist_high = (high_20d - close) / high_20d * 100
    dist_low = (close - low_20d) / low_20d * 100
    if dist_high <= near_band_pct:
        range_position = "near_high"
    elif dist_low <= near_band_pct:
        range_position = "near_low"
    else:
        range_position = "mid"

    prior_high = max(highs[-period:-1])
    prior_low = min(lows[-period:-1])
    return {
        "high_20d": round(high_20d, 2),
        "low_20d": round(low_20d, 2),
        "dist_to_20d_high_pct": round(dist_high, 2),
        "dist_to_20d_low_pct": round(dist_low, 2),
        "range_position": range_position,
        "breakout_20d_high": "yes" if close > prior_high else "no",
        "breakdown_20d_low": "yes" if close < prior_low else "no",
    }


def _range_levels_from_row(row: dict, history: list[dict]) -> dict[str, object]:
    close = _to_float(row.get("收盤價"))
    high_20d = _to_float(row.get("區間20日高"))
    low_20d = _to_float(row.get("區間20日低"))
    if high_20d is not None and low_20d is not None and close is not None:
        dist_high = _to_float(row.get("距20日高_%"))
        dist_low = _to_float(row.get("距20日低_%"))
        if dist_high is None and high_20d > 0:
            dist_high = round((high_20d - close) / high_20d * 100, 2)
        if dist_low is None and low_20d > 0:
            dist_low = round((close - low_20d) / low_20d * 100, 2)

        breakout_raw = str(row.get("突破20日高", "")).strip()
        breakdown_raw = str(row.get("跌破20日低", "")).strip()
        if dist_high is not None and dist_high <= RANGE_NEAR_BAND_PCT:
            range_position = "near_high"
        elif dist_low is not None and dist_low <= RANGE_NEAR_BAND_PCT:
            range_position = "near_low"
        else:
            range_position = "mid"

        breakout = {
            "是": "yes",
            "否": "no",
        }.get(breakout_raw, "unknown")
        breakdown = {
            "是": "yes",
            "否": "no",
        }.get(breakdown_raw, "unknown")
        return {
            "high_20d": high_20d,
            "low_20d": low_20d,
            "dist_to_20d_high_pct": dist_high,
            "dist_to_20d_low_pct": dist_low,
            "range_position": range_position,
            "breakout_20d_high": breakout,
            "breakdown_20d_low": breakdown,
        }

    highs, lows, closes = _ohlc_from_row_and_history(row, history)
    return _compute_range_levels(highs, lows, close)


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
    ma_stack: str = "unknown"  # bullish_stack/bearish_stack/mixed/unknown
    ma5_cross_ma10: str = "unknown"  # golden/death/none/unknown
    ma5_cross_recency: str = "unknown"  # today/within_3d/none/unknown
    ma10_cross_ma20: str = "unknown"
    ma10_cross_recency: str = "unknown"
    ma20_slope: str = "unknown"  # rising/falling/flat/unknown
    ma20_slope_pct: float | None = None
    rsi_14: float | None = None
    rsi_zone: str = "unknown"  # overbought/oversold/neutral/unknown
    atr_14: float | None = None
    atr_pct: float | None = None
    volatility_regime: str = "unknown"  # high/normal/low/unknown
    adx_14: float | None = None
    trend_strength: str = "unknown"  # strong/weak/neutral/unknown
    margin_short_ratio_pct: float | None = None
    margin_short_ratio_zone: str = "unknown"  # high/low/neutral/unknown
    margin_momentum_pct: float | None = None
    margin_momentum: str = "unknown"  # heating/cooling/stable/unknown
    high_20d: float | None = None
    low_20d: float | None = None
    dist_to_20d_high_pct: float | None = None
    dist_to_20d_low_pct: float | None = None
    range_position: str = "unknown"  # near_high/near_low/mid/unknown
    breakout_20d_high: str = "unknown"  # yes/no/unknown
    breakdown_20d_low: str = "unknown"  # yes/no/unknown
    divergences: list[str] = field(default_factory=list)

    # 規則化判定（v2）
    institutional_consensus: str = "unknown"
    major_foreign_divergence: bool = False
    margin_short_regime: str = "unknown"
    day_trade_intensity: str = "unknown"
    borrow_pressure: str = "unknown"
    volume_anomaly: str = "unknown"
    volume_trend: str = "unknown"  # heating/cooling/stable/unknown
    vol_ma5_lots: int | None = None
    vol_ma20_lots: int | None = None
    volume_ma_ratio: float | None = None
    volume_price_divergence: str = "unknown"
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
    *,
    spike_ratio: float = 1.5,
    shrink_ratio: float = 0.65,
) -> str:
    if today_volume is None or avg_volume is None or avg_volume <= 0:
        return "unknown"
    ratio = today_volume / avg_volume
    if ratio >= spike_ratio:
        return "spike"
    if ratio <= shrink_ratio:
        return "shrink"
    return "normal"


VOLUME_ANOMALY_LABEL = {
    "spike": "成交量明顯放大",
    "shrink": "成交量明顯萎縮",
    "normal": "成交量接近區間均值",
    "unknown": "成交量資料不足",
}

VOLUME_TREND_HEATING_RATIO = 1.2
VOLUME_TREND_COOLING_RATIO = 0.8

VOLUME_TREND_LABEL = {
    "heating": "量能升溫（5日均量高於20日均量）",
    "cooling": "量能降溫（5日均量低於20日均量）",
    "stable": "量能平穩（5日/20日均量接近）",
    "unknown": "量能趨勢資料不足",
}

VOLUME_PRICE_DIVERGENCE_LABEL = {
    "confirming_up": "價量配合偏多（區間上漲且量能偏強）",
    "confirming_down": "價量配合偏空（區間下跌且量能偏弱）",
    "bearish_divergence": "價漲量縮（量價背離，追高需留意）",
    "bullish_divergence": "價跌量增（可能洗盤或承接）",
    "none": "價量未見明顯背離",
    "unknown": "價量背離資料不足",
}


def _volume_moving_average(volumes: list[int], period: int) -> float | None:
    if len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period


def _volumes_from_row_and_history(row: dict, history: list[dict]) -> list[int]:
    volumes: list[int] = []
    for hist_row in history:
        volume = _to_int(hist_row.get("成交量_張"))
        if volume is not None:
            volumes.append(volume)
    volume_today = _to_int(row.get("成交量_張"))
    if volume_today is not None:
        if not volumes or volumes[-1] != volume_today:
            volumes.append(volume_today)
    return volumes


def _volume_trend_metrics(
    vol_ma5: float | None,
    vol_ma20: float | None,
) -> tuple[str, float | None]:
    if vol_ma5 is None or vol_ma20 is None or vol_ma20 <= 0:
        return "unknown", None
    ratio = vol_ma5 / vol_ma20
    if ratio >= VOLUME_TREND_HEATING_RATIO:
        return "heating", round(ratio, 2)
    if ratio <= VOLUME_TREND_COOLING_RATIO:
        return "cooling", round(ratio, 2)
    return "stable", round(ratio, 2)


def _volume_metrics_from_row(row: dict, history: list[dict]) -> dict[str, object]:
    vol_ma5 = _to_float(row.get("量均線5_張"))
    vol_ma20 = _to_float(row.get("量均線20_張"))
    ratio = _to_float(row.get("量均線比"))

    if vol_ma5 is None or vol_ma20 is None:
        volumes = _volumes_from_row_and_history(row, history)
        vol_ma5 = _volume_moving_average(volumes, MA5_PERIOD)
        vol_ma20 = _volume_moving_average(volumes, MA20_PERIOD)

    volume_trend, computed_ratio = _volume_trend_metrics(vol_ma5, vol_ma20)
    if ratio is None:
        ratio = computed_ratio

    return {
        "vol_ma5_lots": int(round(vol_ma5)) if vol_ma5 is not None else None,
        "vol_ma20_lots": int(round(vol_ma20)) if vol_ma20 is not None else None,
        "volume_ma_ratio": ratio,
        "volume_trend": volume_trend,
    }


def _volume_price_divergence(
    price_trend: str,
    volume_trend: str,
    volume_anomaly: str,
) -> str:
    if price_trend == "unknown":
        return "unknown"

    if volume_trend == "heating":
        if price_trend == "up":
            return "confirming_up"
        if price_trend == "down":
            return "bullish_divergence"
    elif volume_trend == "cooling":
        if price_trend == "up":
            return "bearish_divergence"
        if price_trend == "down":
            return "confirming_down"

    if volume_trend in {"stable", "unknown"}:
        if price_trend == "up" and volume_anomaly == "shrink":
            return "bearish_divergence"
        if price_trend == "down" and volume_anomaly == "spike":
            return "bullish_divergence"
        if price_trend == "up" and volume_anomaly == "spike":
            return "confirming_up"
        if price_trend == "down" and volume_anomaly == "shrink":
            return "confirming_down"

    return "none"


def _detect_chip_regime(
    *,
    foreign_direction: int,
    foreign_cum: int | None,
    institutional_consensus: str,
    price_trend: str,
    divergences: list[str],
    major_foreign_divergence: bool,
    margin_short_regime: str,
    volume_price_divergence: str = "unknown",
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
    if volume_price_divergence == "bearish_divergence":
        score -= 1
    elif volume_price_divergence == "confirming_up":
        score += 1

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

    thresholds = _load_stock_thresholds(stock_id)

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

    ma5_val = _to_float(row.get("MA5"))
    ma10_val = _to_float(row.get("MA10"))
    ma20_val = _to_float(row.get("MA20"))
    ma_stack = _ma_stack(ma5_val, ma10_val, ma20_val)

    ma20_slope_pct = _to_float(row.get("MA20斜率_%"))
    if ma20_slope_pct is None:
        ma20_slope_pct = _ma20_slope_pct_from_closes(
            _closes_from_row_and_history(row, history)
        )
    ma20_slope = _ma20_slope_label(ma20_slope_pct)

    rsi_14 = _to_float(row.get("RSI14"))
    if rsi_14 is None:
        rsi_14 = _rsi_wilder(_closes_from_row_and_history(row, history))
    rsi_zone = _rsi_zone(
        rsi_14,
        overbought=_threshold_value(thresholds, "rsi_overbought", RSI_OVERBOUGHT),
        oversold=_threshold_value(thresholds, "rsi_oversold", RSI_OVERSOLD),
    )

    atr_14 = _to_float(row.get("ATR14"))
    atr_pct = _to_float(row.get("ATR14_%"))
    highs, lows, closes = _ohlc_from_row_and_history(row, history)
    if atr_14 is None and highs and lows and closes:
        atr_14 = _atr_wilder(highs, lows, closes)
    close_price = _to_float(row.get("收盤價")) or (closes[-1] if closes else None)
    if atr_pct is None:
        atr_pct = _atr_pct(atr_14, close_price)
    volatility_regime = _volatility_regime(
        atr_pct,
        highs,
        lows,
        closes,
        high_pct=_threshold_value(thresholds, "atr_pct_high", ATR_VOLATILITY_HIGH_PCT),
        low_pct=_threshold_value(thresholds, "atr_pct_low", ATR_VOLATILITY_LOW_PCT),
    )

    adx_14 = _to_float(row.get("ADX14"))
    if adx_14 is None and highs and lows and closes:
        adx_14 = _adx_wilder(highs, lows, closes)
    trend_strength = _trend_strength(adx_14)

    margin_short_ratio_pct = _to_float(row.get("券資比_%"))
    margin_momentum_pct = _to_float(row.get("融資動能_%"))
    margin_today = _to_int(row.get("融資今日餘額_張"))
    short_today = _to_int(row.get("融券今日餘額_張"))
    if margin_short_ratio_pct is None:
        margin_short_ratio_pct = _margin_short_ratio_pct(margin_today, short_today)
    if margin_momentum_pct is None:
        first_margin, last_margin = _margin_balances_from_row_and_history(row, history)
        if first_margin is None and margin_delta is not None and margin_today is not None:
            first_margin = margin_today - margin_delta
            last_margin = margin_today
        elif first_margin is None and margin_today is not None:
            first_margin = margin_today
            last_margin = margin_today
        margin_momentum_pct = _margin_momentum_pct(first_margin, last_margin)
    margin_short_ratio_zone = _margin_short_ratio_zone(margin_short_ratio_pct)
    margin_momentum = _margin_momentum_label(margin_momentum_pct)

    range_levels = _range_levels_from_row(row, history)
    ma_crosses = _ma_crosses_from_row(row, history)

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
    volume_flag = _volume_anomaly(
        volume_today,
        avg_volume,
        spike_ratio=_threshold_value(thresholds, "volume_spike_ratio", 1.5),
        shrink_ratio=_threshold_value(thresholds, "volume_shrink_ratio", 0.65),
    )
    volume_metrics = _volume_metrics_from_row(row, history)
    volume_trend = volume_metrics["volume_trend"]  # type: ignore[assignment]
    volume_price_div = _volume_price_divergence(
        price_trend,
        str(volume_trend),
        volume_flag,
    )
    chip_regime = _detect_chip_regime(
        foreign_direction=foreign_direction,
        foreign_cum=foreign_cum,
        institutional_consensus=institutional,
        price_trend=price_trend,
        divergences=divergences,
        major_foreign_divergence=major_foreign_div,
        margin_short_regime=margin_short,
        volume_price_divergence=volume_price_div,
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
        ma_stack=ma_stack,
        ma5_cross_ma10=ma_crosses["ma5_cross_ma10"],  # type: ignore[arg-type]
        ma5_cross_recency=ma_crosses["ma5_cross_recency"],  # type: ignore[arg-type]
        ma10_cross_ma20=ma_crosses["ma10_cross_ma20"],  # type: ignore[arg-type]
        ma10_cross_recency=ma_crosses["ma10_cross_recency"],  # type: ignore[arg-type]
        ma20_slope=ma20_slope,
        ma20_slope_pct=ma20_slope_pct,
        rsi_14=rsi_14,
        rsi_zone=rsi_zone,  # type: ignore[arg-type]
        atr_14=atr_14,
        atr_pct=atr_pct,
        volatility_regime=volatility_regime,  # type: ignore[arg-type]
        adx_14=adx_14,
        trend_strength=trend_strength,  # type: ignore[arg-type]
        margin_short_ratio_pct=margin_short_ratio_pct,
        margin_short_ratio_zone=margin_short_ratio_zone,  # type: ignore[arg-type]
        margin_momentum_pct=margin_momentum_pct,
        margin_momentum=margin_momentum,  # type: ignore[arg-type]
        high_20d=range_levels["high_20d"],  # type: ignore[arg-type]
        low_20d=range_levels["low_20d"],  # type: ignore[arg-type]
        dist_to_20d_high_pct=range_levels["dist_to_20d_high_pct"],  # type: ignore[arg-type]
        dist_to_20d_low_pct=range_levels["dist_to_20d_low_pct"],  # type: ignore[arg-type]
        range_position=range_levels["range_position"],  # type: ignore[arg-type]
        breakout_20d_high=range_levels["breakout_20d_high"],  # type: ignore[arg-type]
        breakdown_20d_low=range_levels["breakdown_20d_low"],  # type: ignore[arg-type]
        divergences=divergences,
        institutional_consensus=institutional,
        major_foreign_divergence=major_foreign_div,
        margin_short_regime=margin_short,
        day_trade_intensity=day_trade,
        borrow_pressure=borrow,
        volume_anomaly=volume_flag,
        volume_trend=str(volume_trend),
        vol_ma5_lots=volume_metrics["vol_ma5_lots"],  # type: ignore[arg-type]
        vol_ma20_lots=volume_metrics["vol_ma20_lots"],  # type: ignore[arg-type]
        volume_ma_ratio=volume_metrics["volume_ma_ratio"],  # type: ignore[arg-type]
        volume_price_divergence=volume_price_div,
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

    if facts.ma_stack in {"bullish_stack", "bearish_stack"}:
        anchors.append(MA_STACK_LABEL[facts.ma_stack])
    elif facts.ma_stack == "mixed":
        anchors.append(MA_STACK_LABEL["mixed"])

    if (
        facts.ma5_cross_ma10 in {"golden", "death"}
        and facts.ma5_cross_recency in {"today", "within_3d"}
    ):
        label = MA_CROSS_PAIR_LABEL[(facts.ma5_cross_ma10, "ma5_ma10")]
        recency = MA_CROSS_RECENCY_LABEL[facts.ma5_cross_recency]
        anchors.append(f"{label}（{recency}）")
    if (
        facts.ma10_cross_ma20 in {"golden", "death"}
        and facts.ma10_cross_recency in {"today", "within_3d"}
    ):
        label = MA_CROSS_PAIR_LABEL[(facts.ma10_cross_ma20, "ma10_ma20")]
        recency = MA_CROSS_RECENCY_LABEL[facts.ma10_cross_recency]
        anchors.append(f"{label}（{recency}）")

    if facts.ma20_slope in {"rising", "falling"}:
        label = MA20_SLOPE_LABEL[facts.ma20_slope]
        if facts.ma20_slope_pct is not None:
            anchors.append(f"{label}（近 5 日 {facts.ma20_slope_pct:+.1f}%）")
        else:
            anchors.append(label)

    if facts.rsi_zone != "unknown" and facts.rsi_14 is not None:
        anchors.append(
            f"{RSI_ZONE_LABEL[facts.rsi_zone]}（RSI14 {facts.rsi_14:.1f}）"
        )

    if facts.volatility_regime != "unknown" and facts.atr_pct is not None:
        anchors.append(
            f"{VOLATILITY_REGIME_LABEL[facts.volatility_regime]}"
            f"（ATR14 約 {facts.atr_pct:.1f}%）"
        )

    if facts.trend_strength != "unknown" and facts.adx_14 is not None:
        anchors.append(
            f"{TREND_STRENGTH_LABEL[facts.trend_strength]}"
            f"（ADX14 {facts.adx_14:.1f}）"
        )

    if facts.margin_short_ratio_zone != "unknown" and facts.margin_short_ratio_pct is not None:
        anchors.append(
            f"{MARGIN_SHORT_RATIO_ZONE_LABEL[facts.margin_short_ratio_zone]}"
            f"（券資比 {facts.margin_short_ratio_pct:.1f}%）"
        )

    if facts.margin_momentum != "unknown" and facts.margin_momentum_pct is not None:
        anchors.append(
            f"{MARGIN_MOMENTUM_LABEL[facts.margin_momentum]}"
            f"（融資動能 {facts.margin_momentum_pct:+.1f}%）"
        )

    if facts.high_20d is not None and facts.low_20d is not None:
        anchors.append(
            f"近20日區間 {facts.low_20d:.2f}～{facts.high_20d:.2f}"
        )
    if facts.range_position in {"near_high", "near_low"}:
        anchors.append(RANGE_POSITION_LABEL[facts.range_position])
    if facts.breakout_20d_high == "yes":
        anchors.append(BREAKOUT_LABEL["yes"])
    elif facts.breakdown_20d_low == "yes":
        anchors.append(BREAKDOWN_LABEL["yes"])

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
    if facts.volume_trend in {"heating", "cooling"}:
        label = VOLUME_TREND_LABEL[facts.volume_trend]
        if facts.volume_ma_ratio is not None:
            anchors.append(f"{label}（5日/20日均量比 {facts.volume_ma_ratio:.2f}）")
        else:
            anchors.append(label)
    if facts.volume_price_divergence in {
        "bearish_divergence",
        "bullish_divergence",
        "confirming_up",
        "confirming_down",
    }:
        anchors.append(VOLUME_PRICE_DIVERGENCE_LABEL[facts.volume_price_divergence])
    if facts.borrow_pressure == "high":
        anchors.append(BORROW_LABEL["high"])

    for flag in facts.divergences:
        label = DIVERGENCE_LABEL.get(flag)
        if label:
            anchors.append(label)

    return anchors


def facts_to_json(facts: ChipFacts) -> dict:
    return asdict(facts)


_BASE_RATE_LABEL_NAMES = {
    "chip_regime": "籌碼型態",
    "ma_stack": "均線排列",
    "trend_strength": "趨勢強度",
    "price_trend": "區間趨勢",
    "rs_period": "相對大盤",
    "institutional_consensus": "法人共識",
    "volatility_regime": "波動狀態",
    "rsi_zone": "RSI動能",
}

_BASE_RATE_MIN_SAMPLE_DEFAULT = 20


def _base_rate_confidence(n: int, min_sample: int) -> str:
    """信心等級標註。

    小樣本桶已被 shrinkage 拉回全市場先驗，數字本身不具統計效力；此標註讓報告
    誠實揭露可信度，避免把 n 很小的桶當成可靠勝率使用。
    """
    if n >= min_sample:
        return "樣本充足"
    if n >= max(1, min_sample // 2):
        return "樣本偏少"
    return "樣本不足"


def format_base_rates_for_prompt(
    facts: ChipFacts,
    base_rates: dict | None,
) -> list[str]:
    """Lines describing how this stock's *current* regimes fared historically.

    Uses shrunk statistics so tiny buckets don't over-claim; empty when the
    calibration file is missing or no matching bucket has data.
    """
    if not base_rates:
        return []
    buckets = base_rates.get("buckets", {})
    if not isinstance(buckets, dict):
        return []
    min_sample = int(
        base_rates.get("min_bucket_sample", _BASE_RATE_MIN_SAMPLE_DEFAULT)
        or _BASE_RATE_MIN_SAMPLE_DEFAULT
    )
    lines: list[str] = []
    for key, name in _BASE_RATE_LABEL_NAMES.items():
        value = getattr(facts, key, "unknown")
        if value in (None, "", "unknown"):
            continue
        stats = buckets.get(key, {}).get(str(value))
        if not isinstance(stats, dict):
            continue
        parts: list[str] = []
        for slot in ("3d", "5d"):
            horizon = stats.get(slot)
            if not isinstance(horizon, dict):
                continue
            n = int(horizon.get("n", 0) or 0)
            if n <= 0:
                continue
            p_up = horizon.get("p_up_shrunk", horizon.get("p_up"))
            mean = horizon.get("mean_excess_shrunk", horizon.get("mean_excess"))
            if p_up is None or mean is None:
                continue
            confidence = _base_rate_confidence(n, min_sample)
            parts.append(
                f"{slot} 上漲機率 {p_up * 100:.0f}%、平均超額 {mean:+.1f}%"
                f"（n={n}，{confidence}）"
            )
        if parts:
            lines.append(f"- {name}={value}：" + "；".join(parts))
    return lines


def facts_summary_for_prompt(
    facts: ChipFacts,
    *,
    base_rates: dict | None = None,
) -> str:
    """Human-readable, LLM-facing summary of the deterministic facts."""
    if base_rates is None:
        base_rates = load_base_rates()
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
    if facts.ma_stack != "unknown":
        lines.append(f"- 均線排列：{MA_STACK_LABEL[facts.ma_stack]}")
    if (
        facts.ma5_cross_ma10 in {"golden", "death"}
        and facts.ma5_cross_recency in {"today", "within_3d"}
    ):
        lines.append(
            f"- MA5/MA10 交叉：{MA_CROSS_PAIR_LABEL[(facts.ma5_cross_ma10, 'ma5_ma10')]}"
            f"（{MA_CROSS_RECENCY_LABEL[facts.ma5_cross_recency]}）"
        )
    if (
        facts.ma10_cross_ma20 in {"golden", "death"}
        and facts.ma10_cross_recency in {"today", "within_3d"}
    ):
        lines.append(
            f"- MA10/MA20 交叉：{MA_CROSS_PAIR_LABEL[(facts.ma10_cross_ma20, 'ma10_ma20')]}"
            f"（{MA_CROSS_RECENCY_LABEL[facts.ma10_cross_recency]}）"
        )
    if facts.ma20_slope != "unknown":
        slope_line = f"- 月線斜率：{MA20_SLOPE_LABEL[facts.ma20_slope]}"
        if facts.ma20_slope_pct is not None:
            slope_line += f"（近 5 日 MA20 變化 {facts.ma20_slope_pct:+.2f}%）"
        lines.append(slope_line)
    if facts.rsi_zone != "unknown" and facts.rsi_14 is not None:
        lines.append(
            f"- RSI（14日）：{facts.rsi_14:.1f}，{RSI_ZONE_LABEL[facts.rsi_zone]}"
        )
    if facts.volatility_regime != "unknown" and facts.atr_pct is not None:
        atr_line = (
            f"- ATR（14日）：{facts.atr_pct:.1f}%"
            f"（{VOLATILITY_REGIME_LABEL[facts.volatility_regime]}）"
        )
        if facts.atr_14 is not None:
            atr_line += f"，絕對值約 {facts.atr_14:.2f}"
        lines.append(atr_line)
    if facts.trend_strength != "unknown" and facts.adx_14 is not None:
        lines.append(
            f"- ADX（14日）：{facts.adx_14:.1f}，"
            f"{TREND_STRENGTH_LABEL[facts.trend_strength]}"
        )
    if facts.margin_short_ratio_zone != "unknown" and facts.margin_short_ratio_pct is not None:
        lines.append(
            f"- 券資比：{facts.margin_short_ratio_pct:.1f}%，"
            f"{MARGIN_SHORT_RATIO_ZONE_LABEL[facts.margin_short_ratio_zone]}"
        )
    if facts.margin_momentum != "unknown" and facts.margin_momentum_pct is not None:
        lines.append(
            f"- 融資動能：{facts.margin_momentum_pct:+.1f}%，"
            f"{MARGIN_MOMENTUM_LABEL[facts.margin_momentum]}"
        )
    if facts.high_20d is not None and facts.low_20d is not None:
        lines.append(
            f"- 近20日區間：低 {facts.low_20d:.2f} / 高 {facts.high_20d:.2f}"
        )
        if facts.dist_to_20d_high_pct is not None:
            lines.append(f"- 距20日高：{facts.dist_to_20d_high_pct:.2f}%")
        if facts.dist_to_20d_low_pct is not None:
            lines.append(f"- 距20日低：{facts.dist_to_20d_low_pct:.2f}%")
    if facts.range_position != "unknown":
        lines.append(f"- 區間位置：{RANGE_POSITION_LABEL[facts.range_position]}")
    if facts.breakout_20d_high == "yes":
        lines.append(f"- 突破：{BREAKOUT_LABEL['yes']}")
    elif facts.breakdown_20d_low == "yes":
        lines.append(f"- 跌破：{BREAKDOWN_LABEL['yes']}")
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
    if facts.volume_trend != "unknown":
        trend_line = f"- 量能趨勢：{VOLUME_TREND_LABEL[facts.volume_trend]}"
        if facts.volume_ma_ratio is not None:
            trend_line += f"（5日/20日均量比 {facts.volume_ma_ratio:.2f}）"
        lines.append(trend_line)
    if facts.vol_ma5_lots is not None and facts.vol_ma20_lots is not None:
        lines.append(
            f"- 量均線：5日 {facts.vol_ma5_lots:,} 張 / 20日 {facts.vol_ma20_lots:,} 張"
        )
    if facts.volume_price_divergence not in {"unknown", "none"}:
        lines.append(
            f"- 價量關係：{VOLUME_PRICE_DIVERGENCE_LABEL[facts.volume_price_divergence]}"
        )

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

    base_rate_lines = format_base_rates_for_prompt(facts, base_rates)
    if base_rate_lines:
        lines.extend(
            [
                "",
                "【歷史命中率（同型態個股過去 forward 表現，僅供情境權重參考，"
                "非保證；已對小樣本做收縮）。每項標註信心等級：樣本充足＞樣本偏少"
                "＞樣本不足；『樣本不足／偏少』者數字已大幅收縮回全市場平均，"
                "不可當成可靠勝率，敘述須弱化其權重】",
            ]
        )
        lines.extend(base_rate_lines)

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
