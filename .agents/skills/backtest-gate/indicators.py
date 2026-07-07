#!/usr/bin/env python3
"""技術指標: Bollinger Bands / ATR / ADX（Wilder 平滑）。

所有指標僅使用當日與過去資料計算，回測時再統一 shift(1) 以避免 look-ahead。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower})


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑 = EMA with alpha = 1/period。"""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return _wilder(tr, period)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """標準 Wilder ADX：趨勢強度（無方向），值越大代表趨勢越明確。"""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    tr_component = atr(high, low, close, period)
    # 避免除以 0
    atr_safe = tr_component.replace(0.0, np.nan)

    plus_di = 100.0 * _wilder(plus_dm, period) / atr_safe
    minus_di = 100.0 * _wilder(minus_dm, period) / atr_safe

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return _wilder(dx.fillna(0.0), period)
