#!/usr/bin/env python3
"""手腳 (Sandbox Strategy): 布林通道 + ADX 趨勢過濾 + 硬性停損 的事件驅動回測。

策略邏輯（long-only，單一部位，滿倉進出）：
- ADX > adx_threshold（趨勢盤）：順勢突破 —— 收盤突破上軌買進；跌破中軌出場。
- ADX <= adx_threshold（盤整盤）：均值回歸 —— 收盤跌破下軌買進；回到中軌出場。
- 任何時候：跌破 進場價 * (1 - stop_loss_pct) 觸發硬性停損。

防 look-ahead：所有訊號用「前一日」指標 (shift 1) 判斷，隔日以開盤價成交。
含台股交易成本：買賣手續費 + 賣出證交稅。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from indicators import adx, bollinger

FEE_RATE = 0.001425  # 手續費（單邊）
TAX_RATE = 0.003     # 證交稅（賣出）


@dataclass(frozen=True)
class StrategyParams:
    bb_period: int = 20
    bb_std: float = 2.0
    adx_period: int = 14
    adx_threshold: float = 25.0
    stop_loss_pct: float = 0.05

    def as_dict(self) -> dict:
        return {
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "adx_period": self.adx_period,
            "adx_threshold": self.adx_threshold,
            "stop_loss_pct": self.stop_loss_pct,
        }


@dataclass
class BacktestResult:
    equity: pd.Series          # 每日淨值（起始 = 1.0）
    daily_returns: pd.Series   # 每日報酬率
    trades: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)


def _prepare(df: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    data = df.copy().reset_index(drop=True)
    bands = bollinger(data["close"], params.bb_period, params.bb_std)
    data = pd.concat([data, bands], axis=1)
    data["adx"] = adx(
        data["high"], data["low"], data["close"], params.adx_period
    )
    # 用前一日指標做決策，避免 look-ahead
    for col in ["bb_mid", "bb_upper", "bb_lower", "adx", "close"]:
        data[f"{col}_prev"] = data[col].shift(1)
    return data


def run_backtest(df: pd.DataFrame, params: StrategyParams) -> BacktestResult:
    """回傳每日淨值曲線與逐筆交易。df 需含 date/open/high/low/close。"""
    data = _prepare(df, params)

    n = len(data)
    equity = np.ones(n)
    position = 0          # 0 空手 / 1 持有
    entry_price = 0.0
    entry_date = None
    cash = 1.0            # 淨值（以權益計）
    shares = 0.0          # 相對部位（滿倉）
    trades: list[dict] = []

    def portfolio_value(price: float) -> float:
        return cash + shares * price

    for i in range(n):
        row = data.iloc[i]
        open_px = row["open"]
        low_px = row["low"]
        close_px = row["close"]

        prev_close = row["close_prev"]
        prev_mid = row["bb_mid_prev"]
        prev_upper = row["bb_upper_prev"]
        prev_lower = row["bb_lower_prev"]
        prev_adx = row["adx_prev"]

        indicators_ready = not (
            pd.isna(prev_mid)
            or pd.isna(prev_upper)
            or pd.isna(prev_lower)
            or pd.isna(prev_adx)
            or pd.isna(prev_close)
        )

        # --- 1. 持有中：先檢查停損，再檢查訊號出場（皆於今日執行）---
        if position == 1:
            stop_price = entry_price * (1.0 - params.stop_loss_pct)
            exited = False

            if low_px <= stop_price:
                fill = min(open_px, stop_price)  # 若開盤已跳空破停損，以開盤成交
                cash = shares * fill * (1.0 - FEE_RATE - TAX_RATE)
                shares = 0.0
                position = 0
                trades.append(
                    _close_trade(entry_date, entry_price, row["date"], fill, "stop_loss")
                )
                exited = True

            elif indicators_ready:
                trend = prev_adx > params.adx_threshold
                if trend:
                    exit_signal = prev_close < prev_mid
                else:
                    exit_signal = prev_close >= prev_mid
                if exit_signal:
                    fill = open_px
                    cash = shares * fill * (1.0 - FEE_RATE - TAX_RATE)
                    shares = 0.0
                    position = 0
                    trades.append(
                        _close_trade(entry_date, entry_price, row["date"], fill, "signal")
                    )
                    exited = True

            if exited:
                equity[i] = cash
                # 出場當日不再進場（避免同日反覆），續下一日
                continue

        # --- 2. 空手：檢查進場訊號 ---
        if position == 0 and indicators_ready:
            trend = prev_adx > params.adx_threshold
            if trend:
                entry_signal = prev_close > prev_upper       # 突破上軌追買
            else:
                entry_signal = prev_close < prev_lower       # 觸及下軌承接
            if entry_signal:
                fill = open_px * (1.0 + FEE_RATE)
                shares = cash / fill
                cash = 0.0
                position = 1
                entry_price = open_px
                entry_date = row["date"]

        equity[i] = portfolio_value(close_px)

    equity_series = pd.Series(equity, index=data["date"], name="equity")
    daily_returns = equity_series.pct_change().fillna(0.0)

    return BacktestResult(
        equity=equity_series,
        daily_returns=daily_returns,
        trades=trades,
        params=params.as_dict(),
    )


def _close_trade(
    entry_date, entry_price: float, exit_date, exit_price: float, reason: str
) -> dict:
    gross = (exit_price - entry_price) / entry_price if entry_price else 0.0
    net = (
        (exit_price * (1.0 - FEE_RATE - TAX_RATE))
        - (entry_price * (1.0 + FEE_RATE))
    ) / (entry_price * (1.0 + FEE_RATE)) if entry_price else 0.0
    return {
        "entry_date": str(pd.Timestamp(entry_date).date()) if entry_date is not None else None,
        "entry_price": round(float(entry_price), 4),
        "exit_date": str(pd.Timestamp(exit_date).date()),
        "exit_price": round(float(exit_price), 4),
        "gross_return": round(float(gross), 5),
        "net_return": round(float(net), 5),
        "reason": reason,
    }
