#!/usr/bin/env python3
"""眼睛 (Historical Data Engine): 從 FinMind 抓取台股多年日K並快取為 CSV。

預設抓「還原股價」(TaiwanStockPriceAdj) 以避免除權息造成的指標失真；
若該資料集查無資料則退回原始股價 (TaiwanStockPrice)。
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"

PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def cache_dir() -> Path:
    path = project_root() / "reports" / "backtest" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FinMindClient:
    def __init__(self, token: str = "") -> None:
        self.token = token.strip()
        self.session = requests.Session()
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def fetch_dataset(
        self,
        dataset: str,
        *,
        stock_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        response = self.session.get(FINMIND_DATA_URL, params=params, timeout=90)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 200:
            raise RuntimeError(f"{dataset} 查詢失敗: {payload.get('msg', payload)}")
        return payload.get("data") or []


def _normalize(rows: list[dict]) -> pd.DataFrame:
    """FinMind 價格欄位 (open/max/min/close/Trading_Volume) → 標準 OHLCV。"""
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["date"]),
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["max"], errors="coerce"),
            "low": pd.to_numeric(df["min"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "volume": pd.to_numeric(df["Trading_Volume"], errors="coerce"),
        }
    )
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.sort_values("date").reset_index(drop=True)
    return out


def load_prices(
    stock_id: str,
    start_date: str,
    end_date: str,
    *,
    adjusted: bool = True,
    use_cache: bool = True,
    token: str | None = None,
) -> pd.DataFrame:
    """回傳 [date, open, high, low, close, volume]，已依日期排序。"""
    kind = "adj" if adjusted else "raw"
    cache_file = cache_dir() / f"{stock_id}_{kind}_{start_date}_{end_date}.csv"

    if use_cache and cache_file.exists():
        cached = pd.read_csv(cache_file, parse_dates=["date"])
        if not cached.empty:
            return cached

    tok = token if token is not None else os.environ.get("FINMIND_TOKEN", "")
    client = FinMindClient(token=tok)

    rows: list[dict] = []
    if adjusted:
        # 還原股價 (TaiwanStockPriceAdj) 常需付費 token；失敗或空則退回原始股價。
        try:
            rows = client.fetch_dataset(
                "TaiwanStockPriceAdj",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            rows = []
    if not rows:
        rows = client.fetch_dataset(
            "TaiwanStockPrice",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    df = _normalize(rows)
    if df.empty:
        raise RuntimeError(
            f"FinMind 查無 {stock_id} {start_date}~{end_date} 價格資料"
        )

    if use_cache:
        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
    return df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="抓取台股多年日K並快取")
    parser.add_argument("stock_id")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2026-12-31")
    parser.add_argument("--raw", action="store_true", help="用原始股價（預設還原股價）")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    frame = load_prices(
        args.stock_id,
        args.start,
        args.end,
        adjusted=not args.raw,
        use_cache=not args.no_cache,
    )
    print(f"{args.stock_id}: {len(frame)} 筆 "
          f"({frame['date'].min().date()} ~ {frame['date'].max().date()})")
    print(frame.tail())
