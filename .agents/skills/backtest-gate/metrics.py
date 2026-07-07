#!/usr/bin/env python3
"""教練 (Evaluator): 由淨值曲線與交易紀錄計算績效指標。"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def compute_metrics(
    equity: pd.Series,
    daily_returns: pd.Series,
    trades: list[dict],
) -> dict:
    equity = equity.dropna()
    if equity.empty:
        return _empty_metrics()

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)

    days = (equity.index[-1] - equity.index[0]).days
    years = days / 365.25 if days > 0 else 0.0
    if years > 0 and equity.iloc[0] > 0:
        cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)
    else:
        cagr = 0.0

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_drawdown = float(drawdown.min())  # 負值，如 -0.28

    rets = daily_returns.replace([np.inf, -np.inf], np.nan).dropna()
    std = float(rets.std(ddof=0))
    if std > 0:
        sharpe = float(rets.mean() / std * np.sqrt(TRADING_DAYS))
    else:
        sharpe = 0.0

    downside = rets[rets < 0]
    dstd = float(downside.std(ddof=0)) if len(downside) > 1 else 0.0
    sortino = float(rets.mean() / dstd * np.sqrt(TRADING_DAYS)) if dstd > 0 else 0.0

    num_trades = len(trades)
    wins = [t for t in trades if t.get("net_return", 0.0) > 0]
    win_rate = (len(wins) / num_trades) if num_trades else 0.0
    avg_trade = (
        float(np.mean([t["net_return"] for t in trades])) if num_trades else 0.0
    )
    exposure = float((rets != 0).mean()) if len(rets) else 0.0

    return {
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "win_rate": round(win_rate, 4),
        "num_trades": num_trades,
        "avg_trade_return": round(avg_trade, 5),
        "exposure": round(exposure, 4),
        "years": round(years, 2),
    }


def _empty_metrics() -> dict:
    return {
        "total_return": 0.0,
        "cagr": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "win_rate": 0.0,
        "num_trades": 0,
        "avg_trade_return": 0.0,
        "exposure": 0.0,
        "years": 0.0,
    }
