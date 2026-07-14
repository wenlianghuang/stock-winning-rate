#!/usr/bin/env python3
"""Efficient historical backfill of daily chip snapshots.

``fetch_chip_report.py --date D`` re-fetches each stock's whole trailing range
for *every* date, which makes multi-week backfills hit FinMind rate limits fast.
This tool instead fetches each stock's datasets **once** for the full span and
materializes one snapshot directory per trading day locally, reusing the exact
same builders so the output is byte-compatible with the normal daily job.

Output matches what ``outcome_label`` + ``calibrate_thresholds`` consume:
``reports/stock/{date}/tw_stock_{id}.csv`` (snapshot) and ``..._history.csv``.

No look-ahead: every per-date snapshot only uses data on or before that date
(trailing series are sliced with ``end_date=d``).

Pure reuse of ``fetch_chip_report`` builders; ``--skip-major`` (default) avoids
Yahoo per-date calls. ``chip_regime`` and the other regime labels do not depend
on the Yahoo 主力 column, so skipping it does not affect base-rate buckets.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ModuleNotFoundError:
    pass

SKILL_DIR = PROJECT_ROOT / ".agents" / "skills" / "tw-stock-report"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from fetch_chip_report import (  # noqa: E402
    CHART_HISTORY_COLUMNS,
    DAILY_COLUMNS,
    DEFAULT_CHART_LOOKBACK_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    MA20_PERIOD,
    MARKET_COLUMNS,
    SUMMARY_COLUMNS,
    FinMindClient,
    YahooMajorFlowClient,
    attach_major_flow,
    build_chart_history_rows,
    build_daily_row,
    compute_summary_fields,
    fetch_market_context,
    fetch_stock_datasets,
    load_stock_names,
    stock_chart_history_csv_path,
    stock_csv_path,
    stock_history_csv_path,
    _trailing_closes_from_dataset,
    _trailing_highs_lows_from_dataset,
    _trailing_volumes_from_dataset,
)
from twse_calendar import (  # noqa: E402
    fetch_trading_dates,
    resolve_lookback_dates,
    resolve_trade_date,
)

DEFAULT_START_LOOKBACK_DAYS = 60

PORTFOLIO_GATE_DIR = PROJECT_ROOT / ".agents" / "skills" / "portfolio-gate"
BEGINNER_UNIVERSE = PORTFOLIO_GATE_DIR / "portfolio_universe.json"
THEME_UNIVERSE = PORTFOLIO_GATE_DIR / "portfolio_theme_universe.json"

# 若 universe JSON 讀不到時的後備清單（與既有新手池對齊）。
FALLBACK_STOCKS = [
    "1216", "1301", "2002", "2303", "2308", "2317", "2330", "2357",
    "2368", "2379", "2382", "2409", "2412", "2454", "2603", "2609",
    "2881", "2882", "2886", "2891", "3008", "3034", "3037", "3305",
    "3711", "3714", "0050", "006208", "0056", "00878",
]


def _ids_from_universe(path: Path) -> list[str]:
    """Read candidate stock ids from a portfolio universe JSON."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    candidates = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(candidates, list):
        return []
    ids: list[str] = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id", "")).strip()
        if sid:
            ids.append(sid)
    return ids


def load_default_stock_ids(
    *,
    beginner_path: Path = BEGINNER_UNIVERSE,
    theme_path: Path = THEME_UNIVERSE,
) -> list[str]:
    """Merge beginner + theme universe ids (deduped, beginner order first).

    Keeps ``uv run --extra stock python tools/backfill_history.py`` covering
    both novice ETF/blue-chip sleeves and theme sleeves without ``--stocks``.
    """
    seen: set[str] = set()
    out: list[str] = []
    for sid in _ids_from_universe(beginner_path) + _ids_from_universe(theme_path):
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out or list(FALLBACK_STOCKS)


# 啟動時組好，方便 import / 測試；檔案更新後重跑 CLI 即可。
DEFAULT_STOCKS = load_default_stock_ids()


def target_trading_dates(start: str, end: str) -> list[str]:
    end_d = dt.date.fromisoformat(end)
    span_days = (end_d - dt.date.fromisoformat(start)).days + 7
    all_days = fetch_trading_dates(end_d, lookback_days=span_days)
    return [d for d in all_days if start <= d <= end]


def _write_csv(rows: list[dict], columns: list[str], path: Path) -> None:
    pd.DataFrame(rows, columns=columns).to_csv(
        path, index=False, encoding="utf-8-sig"
    )


def backfill(
    stock_ids: list[str],
    start: str,
    end: str,
    *,
    skip_major: bool,
    lookback_days: int,
    chart_lookback_days: int,
    sleep: float,
    write_chart_history: bool,
) -> None:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    client = FinMindClient(token=token)

    dates = target_trading_dates(start, end)
    if not dates:
        print("找不到區間內的交易日", file=sys.stderr)
        return
    earliest = dates[0]

    # trailing window before the earliest target for MA20 / ADX / chart history
    trail = resolve_lookback_dates(earliest, chart_lookback_days + 40)
    range_start = trail[0]

    print(
        f"回補區間：{dates[0]} ~ {dates[-1]}（{len(dates)} 個交易日）；"
        f"資料抓取起點 {range_start}；股票 {len(stock_ids)} 檔",
        file=sys.stderr,
    )

    stock_names = load_stock_names(client)
    yahoo = None if skip_major else YahooMajorFlowClient()

    # 大盤脈絡：每個交易日一組（全市場共用），先建好快取
    market_by_date: dict[str, dict] = {}
    for d in dates:
        lb = resolve_lookback_dates(d, lookback_days)
        try:
            market_by_date[d] = fetch_market_context(client, d, lb)
        except Exception as exc:  # 大盤脈絡失敗不中斷
            print(f"WARNING: 大盤脈絡 {d} 失敗：{exc}", file=sys.stderr)
            market_by_date[d] = {col: "" for col in MARKET_COLUMNS}
        time.sleep(sleep)

    written = 0
    for sid in stock_ids:
        try:
            datasets = fetch_stock_datasets(client, sid, range_start, end)
        except Exception as exc:
            print(f"ERROR: {sid} 資料抓取失敗，跳過：{exc}", file=sys.stderr)
            time.sleep(sleep)
            continue

        price_by_date = datasets["price"]
        stock_written = 0
        for d in dates:
            if d not in price_by_date:
                continue  # 該日未上市 / 停牌 / 非個股交易日
            lb_dates = [x for x in resolve_lookback_dates(d, lookback_days)
                        if x in price_by_date]
            daily_rows = []
            for day in lb_dates:
                row = build_daily_row(sid, stock_names.get(sid, ""), day, datasets)
                if yahoo is not None:
                    row = attach_major_flow(row, yahoo, quiet=True)
                daily_rows.append(row)
            if not daily_rows:
                continue

            price_closes = _trailing_closes_from_dataset(price_by_date, d)
            price_highs, price_lows = _trailing_highs_lows_from_dataset(
                price_by_date, d
            )
            price_volumes = _trailing_volumes_from_dataset(price_by_date, d)

            summary = compute_summary_fields(
                daily_rows,
                lookback_days=len(lb_dates),
                price_closes=price_closes,
                price_highs=price_highs,
                price_lows=price_lows,
                price_volumes=price_volumes,
            )
            snapshot = {**daily_rows[-1], **summary, **market_by_date[d]}

            _write_csv(
                [snapshot],
                DAILY_COLUMNS + SUMMARY_COLUMNS + MARKET_COLUMNS,
                stock_csv_path(d, sid),
            )
            _write_csv(daily_rows, DAILY_COLUMNS, stock_history_csv_path(d, sid))

            if write_chart_history:
                chart_dates = resolve_lookback_dates(d, chart_lookback_days)
                chart_rows = build_chart_history_rows(datasets, chart_dates)
                _write_csv(
                    chart_rows,
                    CHART_HISTORY_COLUMNS,
                    stock_chart_history_csv_path(d, sid),
                )

            stock_written += 1
            written += 1

        print(f"{sid}: 寫入 {stock_written} 個交易日快照", file=sys.stderr)
        time.sleep(sleep)

    print(f"完成：共寫入 {written} 個 (股票, 交易日) 快照", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="高效回補歷史逐日籌碼快照（每檔一次區間抓取，本地切日）",
    )
    parser.add_argument(
        "--stocks",
        help=(
            "股票代碼，逗號分隔；未指定則合併 "
            "portfolio_universe.json + portfolio_theme_universe.json"
        ),
    )
    parser.add_argument(
        "--start",
        help=(
            "回補起始交易日 YYYY-MM-DD"
            f"（預設：--end 往前第 {DEFAULT_START_LOOKBACK_DAYS} 個交易日）"
        ),
    )
    parser.add_argument(
        "--end",
        help="回補結束交易日 YYYY-MM-DD（預設：台北今天對應的最新交易日）",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"每個快照的籌碼回看交易日（預設 {DEFAULT_LOOKBACK_DAYS}）",
    )
    parser.add_argument(
        "--chart-lookback-days",
        type=int,
        default=DEFAULT_CHART_LOOKBACK_DAYS,
        help=f"圖表回看交易日（預設 {DEFAULT_CHART_LOOKBACK_DAYS}）",
    )
    parser.add_argument(
        "--with-major",
        action="store_true",
        help="逐日抓 Yahoo 主力（很慢、易限流；預設略過，不影響 regime 桶）",
    )
    parser.add_argument(
        "--no-chart-history",
        action="store_true",
        help="不輸出每日 chart_history（快照收盤已足夠 forward 解析）",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.4,
        help="每次 API 之間的延遲秒數，避免限流（預設 0.4）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stocks:
        stock_ids = [s.strip() for s in args.stocks.split(",") if s.strip()]
    else:
        # 每次執行重讀 universe，避免改 JSON 後還要重 import
        stock_ids = load_default_stock_ids()
        print(
            f"未指定 --stocks：自新手+主題候選池載入 {len(stock_ids)} 檔",
            file=sys.stderr,
        )

    end = args.end
    if not end:
        end, note = resolve_trade_date(validate_market=False)
        if note:
            print(f"end 預設：{note}", file=sys.stderr)

    start = args.start
    if not start:
        # 「end 往前第 N 個交易日」當起點：含 end 共 N+1 個交易日
        window = resolve_lookback_dates(end, DEFAULT_START_LOOKBACK_DAYS + 1)
        start = window[0]

    print(f"回補區間：{start} ~ {end}", file=sys.stderr)

    backfill(
        stock_ids,
        start,
        end,
        skip_major=not args.with_major,
        lookback_days=max(1, args.lookback_days),
        chart_lookback_days=max(MA20_PERIOD, args.chart_lookback_days),
        sleep=max(0.0, args.sleep),
        write_chart_history=not args.no_chart_history,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
