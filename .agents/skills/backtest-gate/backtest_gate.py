#!/usr/bin/env python3
"""循環 (Loop / Harness): 布林+ADX+停損 策略回測閉環。

流程：
1. 眼睛：抓多年日K（預設還原股價）。
2. 切分 train（可迭代優化）與 holdout（鎖住，只評一次）以防過擬合。
3. 教練：在 train 上做 grid search，套用約束（最低交易次數）後挑最佳參數。
4. 用最佳參數在 holdout 上做「終極大考」，只跑一次。
5. 依門檻（年化 > 15%、最大回撤 < 10%、Sharpe >= 下限）判定 PASS / FAIL。
6. 輸出 JSON + 主控台摘要。

Exit codes:
  0  holdout 通過所有門檻
  1  holdout 未通過門檻（策略在樣本外不達標）
  2  train 找不到符合約束的參數（連 in-sample 都無有效策略）
  20 資料不足 / 抓取失敗
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from data_engine import load_prices, project_root
from metrics import compute_metrics
from strategy import StrategyParams, run_backtest

EXIT_OK = 0
EXIT_HOLDOUT_FAILED = 1
EXIT_NO_TRAIN_CANDIDATE = 2
EXIT_DATA = 20

# ---- 預設門檻（教練標準）----
DEFAULT_MIN_CAGR = 0.15
DEFAULT_MAX_DRAWDOWN = 0.10   # 允許的最大回撤幅度（正數表示 10%）
DEFAULT_MIN_SHARPE = 0.8
DEFAULT_MIN_TRADES = 10       # 防少數幸運單筆撐起績效

# ---- 預設搜尋網格（可調，刻意保持精簡以降低過擬合風險）----
GRID = {
    "bb_period": [15, 20, 25],
    "bb_std": [1.5, 2.0, 2.5],
    "adx_period": [14],
    "adx_threshold": [20.0, 25.0, 30.0],
    "stop_loss_pct": [0.04, 0.05, 0.06],
}


@dataclass
class Thresholds:
    min_cagr: float = DEFAULT_MIN_CAGR
    max_drawdown: float = DEFAULT_MAX_DRAWDOWN
    min_sharpe: float = DEFAULT_MIN_SHARPE
    min_trades: int = DEFAULT_MIN_TRADES


def slice_by_date(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    mask = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
    return df.loc[mask].reset_index(drop=True)


def evaluate(df: pd.DataFrame, params: StrategyParams) -> dict:
    result = run_backtest(df, params)
    metrics = compute_metrics(result.equity, result.daily_returns, result.trades)
    return {"metrics": metrics, "trades": result.trades, "params": params.as_dict()}


def buy_and_hold(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0}
    equity = df.set_index("date")["close"] / df["close"].iloc[0]
    daily = equity.pct_change().fillna(0.0)
    m = compute_metrics(equity, daily, [])
    return {
        "total_return": m["total_return"],
        "cagr": m["cagr"],
        "max_drawdown": m["max_drawdown"],
        "sharpe": m["sharpe"],
    }


def train_objective(metrics: dict, th: Thresholds) -> float | None:
    """回傳排序分數；不符約束回傳 None。以 Sharpe 為主、回撤為輔。"""
    if metrics["num_trades"] < th.min_trades:
        return None
    if metrics["cagr"] <= 0:
        return None
    # 綜合分：Sharpe 為核心，對過大回撤扣分
    return metrics["sharpe"] - 2.0 * abs(metrics["max_drawdown"])


def grid_search(train_df: pd.DataFrame, th: Thresholds, grid: dict) -> list[dict]:
    keys = list(grid.keys())
    candidates: list[dict] = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = StrategyParams(**dict(zip(keys, combo)))
        result = run_backtest(train_df, params)
        metrics = compute_metrics(result.equity, result.daily_returns, result.trades)
        score = train_objective(metrics, th)
        if score is None:
            continue
        candidates.append(
            {"params": params.as_dict(), "score": round(score, 4), "metrics": metrics}
        )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def check_thresholds(metrics: dict, th: Thresholds) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if metrics["cagr"] < th.min_cagr:
        reasons.append(
            f"年化報酬 {metrics['cagr']:.1%} < 門檻 {th.min_cagr:.0%}"
        )
    if abs(metrics["max_drawdown"]) > th.max_drawdown:
        reasons.append(
            f"最大回撤 {metrics['max_drawdown']:.1%} 超過 -{th.max_drawdown:.0%}"
        )
    if metrics["sharpe"] < th.min_sharpe:
        reasons.append(
            f"Sharpe {metrics['sharpe']:.2f} < 門檻 {th.min_sharpe:.2f}"
        )
    if metrics["num_trades"] < th.min_trades:
        reasons.append(
            f"交易次數 {metrics['num_trades']} < 門檻 {th.min_trades}"
        )
    return (len(reasons) == 0, reasons)


def output_dir(stock_id: str) -> Path:
    path = project_root() / "reports" / "backtest" / stock_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fmt(m: dict) -> str:
    return (
        f"年化 {m['cagr']:.1%} | 總報酬 {m['total_return']:.1%} | "
        f"最大回撤 {m['max_drawdown']:.1%} | Sharpe {m['sharpe']:.2f} | "
        f"勝率 {m['win_rate']:.1%} | 交易 {m['num_trades']} 筆"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="布林+ADX+停損 策略回測閉環")
    p.add_argument("stock_id", help="台股代碼，如 2330")
    p.add_argument("--train", default="2018-01-01:2023-12-31",
                   help="訓練區間 start:end（可迭代優化）")
    p.add_argument("--holdout", default="2024-01-01:2026-12-31",
                   help="樣本外區間 start:end（只評一次）")
    p.add_argument("--raw", action="store_true", help="用原始股價（預設還原股價）")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--top", type=int, default=3, help="輸出前 N 名 train 候選")
    p.add_argument("--min-cagr", type=float, default=DEFAULT_MIN_CAGR)
    p.add_argument("--max-drawdown", type=float, default=DEFAULT_MAX_DRAWDOWN)
    p.add_argument("--min-sharpe", type=float, default=DEFAULT_MIN_SHARPE)
    p.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    th = Thresholds(
        min_cagr=args.min_cagr,
        max_drawdown=args.max_drawdown,
        min_sharpe=args.min_sharpe,
        min_trades=args.min_trades,
    )

    train_start, train_end = args.train.split(":")
    hold_start, hold_end = args.holdout.split(":")
    full_start = min(train_start, hold_start)
    full_end = max(train_end, hold_end)

    try:
        prices = load_prices(
            args.stock_id, full_start, full_end,
            adjusted=not args.raw, use_cache=not args.no_cache,
        )
    except Exception as exc:
        print(f"CRITICAL_ERROR: 資料抓取失敗：{exc}", file=sys.stderr)
        return EXIT_DATA

    train_df = slice_by_date(prices, train_start, train_end)
    holdout_df = slice_by_date(prices, hold_start, hold_end)

    if len(train_df) < 60 or len(holdout_df) < 30:
        print(
            f"CRITICAL_ERROR: 資料不足（train={len(train_df)} / holdout={len(holdout_df)}）",
            file=sys.stderr,
        )
        return EXIT_DATA

    print(f"=== backtest-gate {args.stock_id} ===")
    print(f"資料：{prices['date'].min().date()} ~ {prices['date'].max().date()}"
          f"（train {len(train_df)} 日 / holdout {len(holdout_df)} 日）")
    print(f"門檻：年化>{th.min_cagr:.0%}、最大回撤<-{th.max_drawdown:.0%}、"
          f"Sharpe>={th.min_sharpe}、交易>={th.min_trades}")
    print()

    # --- Step 1: train grid search（教練只讓它看 train）---
    candidates = grid_search(train_df, th, GRID)
    total_combos = 1
    for v in GRID.values():
        total_combos *= len(v)

    print(f"[TRAIN] grid search {total_combos} 組 → {len(candidates)} 組符合約束")
    if not candidates:
        print("FAIL: train 找不到符合約束的參數（連 in-sample 都無有效策略）")
        return EXIT_NO_TRAIN_CANDIDATE

    for i, c in enumerate(candidates[: args.top], 1):
        print(f"  #{i} {c['params']}  → {_fmt(c['metrics'])}")

    best = candidates[0]
    best_params = StrategyParams(**best["params"])

    # --- Step 2: holdout 終極大考（只跑一次）---
    holdout_eval = evaluate(holdout_df, best_params)
    holdout_metrics = holdout_eval["metrics"]
    passed, reasons = check_thresholds(holdout_metrics, th)

    bh_train = buy_and_hold(train_df)
    bh_holdout = buy_and_hold(holdout_df)

    print()
    print(f"[HOLDOUT] 最佳參數 {best['params']}")
    print(f"  策略：{_fmt(holdout_metrics)}")
    print(f"  買進持有基準：年化 {bh_holdout['cagr']:.1%} | "
          f"最大回撤 {bh_holdout['max_drawdown']:.1%}")
    print()

    verdict = "PASS" if passed else "FAIL"
    print(f"===> 判定：{verdict}")
    if not passed:
        for r in reasons:
            print(f"   - {r}")

    report = {
        "stock_id": args.stock_id,
        "verdict": verdict,
        "thresholds": asdict(th),
        "data_range": {
            "start": str(prices["date"].min().date()),
            "end": str(prices["date"].max().date()),
        },
        "train": {
            "range": [train_start, train_end],
            "candidates_evaluated": total_combos,
            "candidates_passed_constraints": len(candidates),
            "top": candidates[: args.top],
            "buy_and_hold": bh_train,
        },
        "holdout": {
            "range": [hold_start, hold_end],
            "params": best["params"],
            "metrics": holdout_metrics,
            "buy_and_hold": bh_holdout,
            "fail_reasons": reasons,
            "trades": holdout_eval["trades"],
        },
    }

    out = output_dir(args.stock_id)
    json_path = out / "backtest_gate.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n輸出：{json_path}")

    return EXIT_OK if passed else EXIT_HOLDOUT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
