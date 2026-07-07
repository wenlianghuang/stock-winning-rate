#!/usr/bin/env python3
"""Fetch Taiwan stock chip / flow data from FinMind + Yahoo 主力進出, export CSV."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
YAHOO_BROKER_URL = "https://tw.stock.yahoo.com/quote/{symbol}.TW/broker-trading"
YAHOO_BROKER_API = (
    "https://tw.stock.yahoo.com/_td-stock/api/resource/"
    "StockServices.brokerTrades;crumb=;symbol={symbol}.TW;date={iso_date}"
)
YAHOO_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_WATCHLIST = Path(__file__).with_name("watchlist.txt")
SHARES_PER_LOT = 1000
DEFAULT_LOOKBACK_DAYS = 5

DAILY_COLUMNS = [
    "代碼",
    "名稱",
    "日期",
    "開盤價",
    "最高價",
    "最低價",
    "收盤價",
    "成交量_張",
    "漲跌幅",
    "外資買賣超_張",
    "投信買賣超_張",
    "自營商買賣超_張",
    "融資今日餘額_張",
    "融資增減_張",
    "融券今日餘額_張",
    "融券增減_張",
    "借券賣出_張",
    "券賣還券_張",
    "當沖成交量_張",
    "當沖買金額",
    "當沖賣金額",
    "當沖佔成交量_%",
    "主力買賣超_張",
    "主力佔成交量_%",
    "主力_擷取狀態",
]

SUMMARY_COLUMNS = [
    "回看天數",
    "區間起始日",
    "區間外資累計_張",
    "區間投信累計_張",
    "區間自營商累計_張",
    "區間主力累計_張",
    "區間主力資料天數",
    "區間融資餘額淨變化_張",
    "區間融券餘額淨變化_張",
    "區間成交量均值_張",
    "區間當沖佔比均值_%",
    "區間漲跌幅_%",
    "MA5",
    "收盤偏離MA5_%",
]


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
        stock_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"dataset": dataset}
        if stock_id:
            params["data_id"] = stock_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        response = self.session.get(FINMIND_DATA_URL, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 200:
            raise RuntimeError(
                f"{dataset} 查詢失敗: {payload.get('msg', payload)}"
            )
        return payload.get("data") or []


def shares_to_lots(value: int | float | None) -> int | str:
    """Convert share count to lots (1 lot = 1000 shares), rounded to integer."""
    if value is None:
        return ""
    return int(round(float(value) / SHARES_PER_LOT))


def load_stock_ids(
    stocks_arg: str | None, watchlist_path: Path | None
) -> list[str]:
    if stocks_arg:
        return [s.strip() for s in stocks_arg.split(",") if s.strip()]

    path = watchlist_path or DEFAULT_WATCHLIST
    if not path.exists():
        raise FileNotFoundError(f"找不到 watchlist: {path}")

    stock_ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        stock_ids.append(line.split()[0])
    if not stock_ids:
        raise ValueError(f"watchlist 為空: {path}")
    return stock_ids


def fetch_trading_dates(
    client: FinMindClient,
    end_date: datetime.date,
    lookback_days: int = 90,
) -> list[str]:
    start_date = end_date - timedelta(days=lookback_days)
    rows = client.fetch_dataset(
        "TaiwanStockTradingDate",
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    return sorted(row["date"] for row in rows)


def resolve_trade_date(
    client: FinMindClient, requested_date: str | None
) -> tuple[str, str | None]:
    """Resolve to the latest Taiwan trading day on or before the reference date."""
    today = datetime.now().date()
    reference = (
        datetime.strptime(requested_date, "%Y-%m-%d").date()
        if requested_date
        else today
    )

    trading_dates = fetch_trading_dates(client, reference)
    if not trading_dates:
        trading_dates = fetch_trading_dates(client, today, lookback_days=180)

    if not trading_dates:
        fallback = (reference - timedelta(days=1)).isoformat()
        return fallback, f"無法取得台股交易日曆，暫用 {fallback}"

    eligible = [d for d in trading_dates if d <= reference.isoformat()]
    resolved = eligible[-1] if eligible else trading_dates[-1]

    if resolved == reference.isoformat():
        return resolved, None

    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    ref_label = (
        f"指定日期 {requested_date}"
        if requested_date
        else (
            f"今日（{reference.isoformat()} 星期"
            f"{weekday_names[reference.weekday()]}）"
        )
    )
    note = f"{ref_label} 非台股交易日，已改用最近交易日 {resolved}"
    return resolved, note


def resolve_lookback_dates(
    client: FinMindClient,
    trade_date: str,
    lookback_days: int,
) -> list[str]:
    """Return the last N trading days up to and including trade_date."""
    end = datetime.strptime(trade_date, "%Y-%m-%d").date()
    calendar_span = max(lookback_days * 4, 30)
    trading_dates = fetch_trading_dates(client, end, lookback_days=calendar_span)
    eligible = [d for d in trading_dates if d <= trade_date]
    if not eligible:
        return [trade_date]
    return eligible[-lookback_days:]


def load_stock_names(client: FinMindClient) -> dict[str, str]:
    rows = client.fetch_dataset("TaiwanStockInfo")
    return {row["stock_id"]: row["stock_name"] for row in rows}


def index_rows_by_date(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["date"]): row for row in rows if row.get("date")}


def calc_net(buy: int | float, sell: int | float) -> int:
    return int(buy or 0) - int(sell or 0)


def calc_dealer_net(row: dict[str, Any]) -> int:
    buy = (
        int(row.get("Dealer_buy") or 0)
        + int(row.get("Dealer_self_buy") or 0)
        + int(row.get("Dealer_Hedging_buy") or 0)
    )
    sell = (
        int(row.get("Dealer_sell") or 0)
        + int(row.get("Dealer_self_sell") or 0)
        + int(row.get("Dealer_Hedging_sell") or 0)
    )
    return buy - sell


def calc_foreign_net(row: dict[str, Any]) -> int:
    buy = int(row.get("Foreign_Investor_buy") or 0) + int(
        row.get("Foreign_Dealer_Self_buy") or 0
    )
    sell = int(row.get("Foreign_Investor_sell") or 0) + int(
        row.get("Foreign_Dealer_Self_sell") or 0
    )
    return buy - sell


def _to_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _sum_int_field(rows: list[dict[str, Any]], key: str) -> int | str:
    values = [_to_int(row.get(key)) for row in rows]
    nums = [value for value in values if value is not None]
    return sum(nums) if nums else ""


def _mean_numeric_field(rows: list[dict[str, Any]], key: str) -> float | str:
    values = [_to_float(row.get(key)) for row in rows]
    nums = [value for value in values if value is not None]
    if not nums:
        return ""
    return round(sum(nums) / len(nums), 2)


class YahooMajorFlowClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = YAHOO_USER_AGENT

    def fetch_broker_html(self, stock_id: str) -> str:
        response = self.session.get(
            YAHOO_BROKER_URL.format(symbol=stock_id),
            timeout=60,
        )
        response.raise_for_status()
        return response.text

    def fetch_major_flow_for_date(
        self, stock_id: str, trade_date: str
    ) -> tuple[str, int, float]:
        iso_date = quote(f"{trade_date}T00:00:00+08:00", safe="")
        response = self.session.get(
            YAHOO_BROKER_API.format(symbol=stock_id, iso_date=iso_date),
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("list") or []
        if not rows:
            raise ValueError(f"Yahoo API 無 {trade_date} 主力資料")

        item = rows[0]
        resolved_date = str(item["date"]).split("T", 1)[0]
        if resolved_date != trade_date:
            raise ValueError(
                f"Yahoo API 日期不符（要求 {trade_date} / 回傳 {resolved_date}）"
            )

        net_lots = int(item["totalDifferenceVolK"])
        volume_rate_pct = round(float(item["tradeVolumeRate"]) * 100, 2)
        return resolved_date, net_lots, volume_rate_pct


def _extract_json_object(html: str, marker: str) -> dict[str, Any]:
    idx = html.find(marker)
    if idx < 0:
        raise ValueError(f"找不到 {marker!r}")

    start = idx + len(marker)
    if start >= len(html) or html[start] != "{":
        raise ValueError(f"{marker!r} 後方不是 JSON 物件")

    depth = 0
    in_string = False
    escape = False
    end = start
    for pos in range(start, len(html)):
        ch = html[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break

    if depth != 0:
        raise ValueError(f"無法解析 {marker!r} 的 JSON 物件")

    return json.loads(html[start:end])


def parse_yahoo_major_flow(html: str) -> tuple[str, int, float]:
    payload = _extract_json_object(html, '"brokerTrades":{"data":')
    trade_date = str(payload["date"]).split("T", 1)[0]
    net_lots = int(payload["totalDifferenceVolK"])
    volume_rate_pct = round(float(payload["tradeVolumeRate"]) * 100, 2)
    return trade_date, net_lots, volume_rate_pct


def attach_major_flow(
    row: dict[str, Any],
    yahoo: YahooMajorFlowClient,
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    stock_id = str(row["代碼"])
    csv_date = str(row.get("日期", "")).strip()
    row["主力買賣超_張"] = ""
    row["主力佔成交量_%"] = ""
    row["主力_擷取狀態"] = ""

    if not csv_date:
        row["主力_擷取狀態"] = "missing_trade_date"
        return row

    try:
        yahoo_date, net_lots, volume_rate_pct = yahoo.fetch_major_flow_for_date(
            stock_id, csv_date
        )
    except Exception as api_exc:
        if not quiet:
            try:
                yahoo_date, net_lots, volume_rate_pct = parse_yahoo_major_flow(
                    yahoo.fetch_broker_html(stock_id)
                )
                if yahoo_date != csv_date:
                    row["主力_擷取狀態"] = (
                        f"date_mismatch(csv={csv_date},yahoo={yahoo_date},api={api_exc})"
                    )
                    print(
                        f"{stock_id}: 主力日期不符（CSV {csv_date} / Yahoo {yahoo_date}），欄位留空",
                        file=sys.stderr,
                    )
                    return row
            except Exception as html_exc:
                row["主力_擷取狀態"] = f"fetch_failed:api={api_exc};html={html_exc}"
                print(
                    f"ERROR: {stock_id} 主力進出擷取失敗：{api_exc}",
                    file=sys.stderr,
                )
                return row
        else:
            row["主力_擷取狀態"] = f"fetch_failed:{api_exc}"
            return row

    row["主力買賣超_張"] = net_lots
    row["主力佔成交量_%"] = volume_rate_pct
    row["主力_擷取狀態"] = "ok"
    if not quiet:
        print(
            f"{stock_id} {csv_date}: 主力買賣超 {net_lots} 張，佔成交量 {volume_rate_pct}%",
            file=sys.stderr,
        )
    return row


def fetch_stock_datasets(
    client: FinMindClient,
    stock_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "price": index_rows_by_date(
            client.fetch_dataset(
                "TaiwanStockPrice",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
        ),
        "inst": index_rows_by_date(
            client.fetch_dataset(
                "TaiwanStockInstitutionalInvestorsBuySellWide",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
        ),
        "margin": index_rows_by_date(
            client.fetch_dataset(
                "TaiwanStockMarginPurchaseShortSale",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
        ),
        "sbl": index_rows_by_date(
            client.fetch_dataset(
                "TaiwanDailyShortSaleBalances",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
        ),
        "daytrade": index_rows_by_date(
            client.fetch_dataset(
                "TaiwanStockDayTrading",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
        ),
    }


def build_daily_row(
    stock_id: str,
    stock_name: str,
    trade_date: str,
    datasets: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    price_row = datasets["price"].get(trade_date, {})
    inst_row = datasets["inst"].get(trade_date, {})
    margin_row = datasets["margin"].get(trade_date, {})
    sbl_row = datasets["sbl"].get(trade_date, {})
    daytrade_row = datasets["daytrade"].get(trade_date, {})

    trading_volume = int(price_row.get("Trading_Volume") or 0)
    daytrade_volume = int(daytrade_row.get("Volume") or 0)
    margin_today = int(margin_row.get("MarginPurchaseTodayBalance") or 0)
    margin_yesterday = int(margin_row.get("MarginPurchaseYesterdayBalance") or 0)
    short_today = int(margin_row.get("ShortSaleTodayBalance") or 0)
    short_yesterday = int(margin_row.get("ShortSaleYesterdayBalance") or 0)

    actual_date = (
        price_row.get("date")
        or inst_row.get("date")
        or margin_row.get("date")
        or trade_date
    )

    daytrade_ratio = (
        round(daytrade_volume / trading_volume * 100, 2)
        if trading_volume > 0 and daytrade_volume > 0
        else ""
    )

    return {
        "代碼": stock_id,
        "名稱": stock_name or stock_id,
        "日期": actual_date,
        "開盤價": price_row.get("open", ""),
        "最高價": price_row.get("max", ""),
        "最低價": price_row.get("min", ""),
        "收盤價": price_row.get("close", ""),
        "成交量_張": shares_to_lots(trading_volume) if price_row else "",
        "漲跌幅": price_row.get("spread", ""),
        "外資買賣超_張": (
            shares_to_lots(calc_foreign_net(inst_row)) if inst_row else ""
        ),
        "投信買賣超_張": (
            shares_to_lots(
                calc_net(
                    inst_row.get("Investment_Trust_buy", 0),
                    inst_row.get("Investment_Trust_sell", 0),
                )
            )
            if inst_row
            else ""
        ),
        "自營商買賣超_張": (
            shares_to_lots(calc_dealer_net(inst_row)) if inst_row else ""
        ),
        "融資今日餘額_張": margin_today if margin_row else "",
        "融資增減_張": (margin_today - margin_yesterday) if margin_row else "",
        "融券今日餘額_張": short_today if margin_row else "",
        "融券增減_張": (short_today - short_yesterday) if margin_row else "",
        "借券賣出_張": (
            shares_to_lots(int(sbl_row.get("SBLShortSalesShortSales") or 0))
            if sbl_row
            else ""
        ),
        "券賣還券_張": (
            shares_to_lots(int(sbl_row.get("SBLShortSalesReturns") or 0))
            if sbl_row
            else ""
        ),
        "當沖成交量_張": shares_to_lots(daytrade_volume) if daytrade_row else "",
        "當沖買金額": int(daytrade_row.get("BuyAmount") or 0) if daytrade_row else "",
        "當沖賣金額": int(daytrade_row.get("SellAmount") or 0) if daytrade_row else "",
        "當沖佔成交量_%": daytrade_ratio,
        "主力買賣超_張": "",
        "主力佔成交量_%": "",
        "主力_擷取狀態": "",
    }


def compute_summary_fields(
    daily_rows: list[dict[str, Any]],
    *,
    lookback_days: int,
) -> dict[str, Any]:
    if not daily_rows:
        return {key: "" for key in SUMMARY_COLUMNS}

    first_row = daily_rows[0]
    last_row = daily_rows[-1]
    actual_days = len(daily_rows)

    first_margin = _to_int(first_row.get("融資今日餘額_張"))
    last_margin = _to_int(last_row.get("融資今日餘額_張"))
    first_short = _to_int(first_row.get("融券今日餘額_張"))
    last_short = _to_int(last_row.get("融券今日餘額_張"))

    major_ok_rows = [
        row
        for row in daily_rows
        if str(row.get("主力_擷取狀態", "")).lower() == "ok"
    ]

    closes = [_to_float(row.get("收盤價")) for row in daily_rows]
    valid_closes = [value for value in closes if value is not None]
    ma_window = valid_closes[-5:] if len(valid_closes) >= 5 else valid_closes
    ma5 = round(sum(ma_window) / len(ma_window), 2) if ma_window else ""

    last_close = valid_closes[-1] if valid_closes else None
    first_close = valid_closes[0] if valid_closes else None
    period_return = ""
    if first_close and last_close and first_close != 0:
        period_return = round((last_close - first_close) / first_close * 100, 2)

    ma_deviation = ""
    if ma5 != "" and last_close is not None and ma5 != 0:
        ma_deviation = round((last_close - float(ma5)) / float(ma5) * 100, 2)

    return {
        "回看天數": actual_days,
        "區間起始日": first_row.get("日期", ""),
        "區間外資累計_張": _sum_int_field(daily_rows, "外資買賣超_張"),
        "區間投信累計_張": _sum_int_field(daily_rows, "投信買賣超_張"),
        "區間自營商累計_張": _sum_int_field(daily_rows, "自營商買賣超_張"),
        "區間主力累計_張": _sum_int_field(major_ok_rows, "主力買賣超_張"),
        "區間主力資料天數": len(major_ok_rows),
        "區間融資餘額淨變化_張": (
            last_margin - first_margin
            if first_margin is not None and last_margin is not None
            else ""
        ),
        "區間融券餘額淨變化_張": (
            last_short - first_short
            if first_short is not None and last_short is not None
            else ""
        ),
        "區間成交量均值_張": _mean_numeric_field(daily_rows, "成交量_張"),
        "區間當沖佔比均值_%": _mean_numeric_field(daily_rows, "當沖佔成交量_%"),
        "區間漲跌幅_%": period_return,
        "MA5": ma5,
        "收盤偏離MA5_%": ma_deviation,
    }


def build_stock_report(
    client: FinMindClient,
    stock_id: str,
    stock_name: str,
    trade_date: str,
    lookback_dates: list[str],
    yahoo: YahooMajorFlowClient | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not lookback_dates:
        lookback_dates = [trade_date]

    range_start = lookback_dates[0]
    datasets = fetch_stock_datasets(client, stock_id, range_start, trade_date)

    daily_rows: list[dict[str, Any]] = []
    for day in lookback_dates:
        row = build_daily_row(stock_id, stock_name, day, datasets)
        if yahoo is not None:
            row = attach_major_flow(row, yahoo, quiet=True)
        daily_rows.append(row)

    summary = compute_summary_fields(
        daily_rows,
        lookback_days=len(lookback_dates),
    )
    snapshot = {**daily_rows[-1], **summary}
    return snapshot, daily_rows


def stock_report_dir(trade_date: str) -> Path:
    path = Path.cwd() / "reports" / "stock" / trade_date
    path.mkdir(parents=True, exist_ok=True)
    return path


def stock_csv_path(trade_date: str, stock_id: str) -> Path:
    return stock_report_dir(trade_date) / f"tw_stock_{stock_id}.csv"


def stock_history_csv_path(trade_date: str, stock_id: str) -> Path:
    return stock_report_dir(trade_date) / f"tw_stock_{stock_id}_history.csv"


def stock_facts_json_path(trade_date: str, stock_id: str) -> Path:
    return stock_report_dir(trade_date) / f"tw_stock_{stock_id}.facts.json"


def _load_chip_signals():
    root = Path(__file__).resolve().parents[3]
    ui_path = str(root / "ui")
    if ui_path not in sys.path:
        sys.path.insert(0, ui_path)
    from chip_signals import build_chip_facts, write_facts_json

    return build_chip_facts, write_facts_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抓取台股個股籌碼資料並輸出 CSV 表格"
    )
    parser.add_argument(
        "--stocks",
        help="股票代碼，逗號分隔（例如 2330,2454）。未指定則讀取 watchlist.txt",
    )
    parser.add_argument(
        "--date",
        help="交易日期 YYYY-MM-DD；未指定則使用最近交易日",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"回看交易日天數（預設 {DEFAULT_LOOKBACK_DAYS}）",
    )
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=DEFAULT_WATCHLIST,
        help="自訂 watchlist 路徑",
    )
    parser.add_argument(
        "--skip-major",
        action="store_true",
        help="略過 Yahoo 主力進出（僅 FinMind 欄位）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    lookback_days = max(1, args.lookback_days)

    try:
        stock_ids = load_stock_ids(args.stocks, args.watchlist)
        client = FinMindClient(token=token)
        trade_date, date_note = resolve_trade_date(client, args.date)
        lookback_dates = resolve_lookback_dates(client, trade_date, lookback_days)
        stock_names = load_stock_names(client)
        yahoo = None if args.skip_major else YahooMajorFlowClient()

        output_dir = stock_report_dir(trade_date)
        snapshot_paths: list[Path] = []
        history_paths: list[Path] = []

        for stock_id in stock_ids:
            snapshot, history = build_stock_report(
                client,
                stock_id,
                stock_names.get(stock_id, ""),
                trade_date,
                lookback_dates,
                yahoo,
            )

            snapshot_path = stock_csv_path(trade_date, stock_id)
            history_path = stock_history_csv_path(trade_date, stock_id)

            snapshot_columns = DAILY_COLUMNS + SUMMARY_COLUMNS
            pd.DataFrame([snapshot], columns=snapshot_columns).to_csv(
                snapshot_path,
                index=False,
                encoding="utf-8-sig",
            )
            pd.DataFrame(history, columns=DAILY_COLUMNS).to_csv(
                history_path,
                index=False,
                encoding="utf-8-sig",
            )

            snapshot_paths.append(snapshot_path)
            history_paths.append(history_path)

            try:
                build_chip_facts, write_facts_json = _load_chip_signals()
                facts = build_chip_facts(snapshot, history)
                write_facts_json(
                    stock_facts_json_path(trade_date, stock_id), facts
                )
            except Exception as facts_exc:  # facts 為附加產物，失敗不應中斷抓取
                print(
                    f"WARNING: {stock_id} facts.json 產生失敗：{facts_exc}",
                    file=sys.stderr,
                )

            if yahoo is not None:
                major_days = snapshot.get("區間主力資料天數", 0)
                print(
                    f"{stock_id}: 回看 {len(history)} 日（{lookback_dates[0]}～{trade_date}），"
                    f"主力資料 {major_days}/{len(history)} 日",
                    file=sys.stderr,
                )

        print(f"交易日期: {trade_date}")
        print(f"回看天數: {len(lookback_dates)}（{lookback_dates[0]}～{trade_date}）")
        if date_note:
            print(date_note)
        print(f"輸出目錄: {output_dir.resolve()}")
        for csv_path in snapshot_paths:
            print(f"輸出檔案: {csv_path.resolve()}")
        for csv_path in history_paths:
            print(f"歷史檔案: {csv_path.resolve()}")
        print(f"股票檔數: {len(snapshot_paths)}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
