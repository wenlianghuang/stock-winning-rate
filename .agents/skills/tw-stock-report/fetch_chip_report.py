#!/usr/bin/env python3
"""Fetch Taiwan stock chip / flow data from FinMind + Yahoo 主力進出, export CSV."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from twse_calendar import resolve_lookback_dates, resolve_trade_date

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
except ModuleNotFoundError:
    pass

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
DEFAULT_CHART_LOOKBACK_DAYS = 60
MA5_PERIOD = 5
MA10_PERIOD = 10
MA20_PERIOD = 20
RSI14_PERIOD = 14
ATR14_PERIOD = 14
ADX14_PERIOD = 14
MARKET_INDEX_ID = "TAIEX"  # 加權指數（FinMind TaiwanStockPrice data_id）

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

CHART_HISTORY_COLUMNS = [
    "日期",
    "開盤價",
    "最高價",
    "最低價",
    "收盤價",
    "成交量_張",
    "漲跌幅",
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
    "MA10",
    "收盤偏離MA10_%",
    "MA20",
    "收盤偏離MA20_%",
    "MA20斜率_%",
    "RSI14",
    "ATR14",
    "ATR14_%",
    "ADX14",
    "區間20日高",
    "區間20日低",
    "距20日高_%",
    "距20日低_%",
    "突破20日高",
    "跌破20日低",
    "量均線5_張",
    "量均線20_張",
    "量均線比",
    "MA5交叉MA10",
    "MA10交叉MA20",
    "券資比_%",
    "融資動能_%",
]

# 大盤（加權指數）脈絡欄位：屬當日全市場資料，每檔快照共用同一組數字。
MARKET_COLUMNS = [
    "大盤收盤",
    "大盤漲跌幅_%",
    "大盤MA5",
    "大盤MA20",
    "大盤收盤偏離MA5_%",
    "大盤收盤偏離MA20_%",
    "大盤區間漲跌幅_%",
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
    stocks_arg: str | list[str] | None, watchlist_path: Path | None
) -> list[str]:
    if isinstance(stocks_arg, list):
        ids = [str(s).strip() for s in stocks_arg if str(s).strip()]
        if ids:
            return ids
    elif stocks_arg:
        return [s.strip() for s in str(stocks_arg).split(",") if s.strip()]

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


def _moving_average(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 2)


def _ma_deviation_pct(close: float | None, ma: float | None) -> float | str:
    if close is None or ma is None or ma == 0:
        return ""
    return round((close - ma) / ma * 100, 2)


def _rsi_wilder(closes: list[float], period: int = RSI14_PERIOD) -> float | str:
    """Wilder RSI; needs at least period + 1 closes."""
    if len(closes) < period + 1:
        return ""
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
    period: int = ATR14_PERIOD,
) -> float | str:
    trs = _true_ranges(highs, lows, closes)
    if len(trs) < period:
        return ""
    atr = sum(trs[:period]) / period
    for index in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[index]) / period
    return round(atr, 4)


def _atr_pct(atr: float | str, close: float | None) -> float | str:
    if close is None or close <= 0:
        return ""
    if atr in ("", None):
        return ""
    try:
        return round(float(atr) / close * 100, 2)
    except (TypeError, ValueError):
        return ""


def _margin_short_ratio_pct(
    margin_lots: int | None,
    short_lots: int | None,
) -> float | str:
    if margin_lots is None or short_lots is None or margin_lots <= 0:
        return ""
    return round(short_lots / margin_lots * 100, 2)


def _margin_momentum_pct(
    first_margin: int | None,
    last_margin: int | None,
) -> float | str:
    if first_margin is None or last_margin is None or first_margin <= 0:
        return ""
    return round((last_margin - first_margin) / first_margin * 100, 2)


def _adx_wilder(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = ADX14_PERIOD,
) -> float | str:
    """Wilder ADX; needs roughly 2 * period OHLC bars."""
    length = min(len(highs), len(lows), len(closes))
    if length < period * 2:
        return ""

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
        return ""
    return round(adx_values[-1], 2)


def _ma20_slope_pct(
    closes: list[float],
    *,
    period: int = MA20_PERIOD,
    lag: int = 5,
) -> float | str:
    """MA20 近 lag 日變化率（%），需至少 period + lag 根收盤價。"""
    if len(closes) < period + lag:
        return ""
    ma_now = sum(closes[-period:]) / period
    ma_past = sum(closes[-(period + lag) : -lag]) / period
    if ma_past == 0:
        return ""
    return round((ma_now - ma_past) / ma_past * 100, 2)


def _trailing_closes_from_dataset(
    price_by_date: dict[str, dict[str, Any]],
    end_date: str,
) -> list[float]:
    closes: list[float] = []
    for day in sorted(price_by_date):
        if day > end_date:
            continue
        close = _to_float(price_by_date[day].get("close"))
        if close is not None:
            closes.append(close)
    return closes


def _trailing_highs_lows_from_dataset(
    price_by_date: dict[str, dict[str, Any]],
    end_date: str,
) -> tuple[list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    for day in sorted(price_by_date):
        if day > end_date:
            continue
        high = _to_float(price_by_date[day].get("max"))
        low = _to_float(price_by_date[day].get("min"))
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
    return highs, lows


def _trailing_volumes_from_dataset(
    price_by_date: dict[str, dict[str, Any]],
    end_date: str,
) -> list[int]:
    volumes: list[int] = []
    for day in sorted(price_by_date):
        if day > end_date:
            continue
        trading_volume = price_by_date[day].get("Trading_Volume")
        if trading_volume is None:
            continue
        lots = shares_to_lots(int(trading_volume))
        if lots != "":
            volumes.append(int(lots))
    return volumes


def _volume_moving_average(volumes: list[int], period: int) -> float | None:
    if len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period


def _volume_summary_fields(volumes: list[int]) -> dict[str, Any]:
    empty = {
        "量均線5_張": "",
        "量均線20_張": "",
        "量均線比": "",
    }
    vol_ma5 = _volume_moving_average(volumes, MA5_PERIOD)
    vol_ma20 = _volume_moving_average(volumes, MA20_PERIOD)
    if vol_ma5 is None or vol_ma20 is None or vol_ma20 <= 0:
        return empty
    return {
        "量均線5_張": int(round(vol_ma5)),
        "量均線20_張": int(round(vol_ma20)),
        "量均線比": round(vol_ma5 / vol_ma20, 2),
    }


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
    lookback_days: int = 3,
) -> str:
    min_len = max(short_period, long_period) + 1
    if len(closes) < min_len:
        return ""
    short_series = _ma_series(closes, short_period)
    long_series = _ma_series(closes, long_period)
    last_index = len(closes) - 1
    start_index = max(1, last_index - lookback_days)
    for index in range(last_index, start_index - 1, -1):
        event = _cross_event_at_index(short_series, long_series, index)
        if event == "golden":
            return "黃金交叉"
        if event == "death":
            return "死亡交叉"
    return "無"


def _ma_cross_summary_fields(closes: list[float]) -> dict[str, str]:
    if not closes:
        return {"MA5交叉MA10": "", "MA10交叉MA20": ""}
    return {
        "MA5交叉MA10": _recent_ma_cross(closes, MA5_PERIOD, MA10_PERIOD),
        "MA10交叉MA20": _recent_ma_cross(closes, MA10_PERIOD, MA20_PERIOD),
    }


def _range_summary_fields(
    highs: list[float],
    lows: list[float],
    close: float | None,
    *,
    period: int = MA20_PERIOD,
    near_band_pct: float = 2.0,
) -> dict[str, Any]:
    empty = {
        "區間20日高": "",
        "區間20日低": "",
        "距20日高_%": "",
        "距20日低_%": "",
        "突破20日高": "",
        "跌破20日低": "",
    }
    if close is None or len(highs) < period or len(lows) < period:
        return empty

    window_highs = highs[-period:]
    window_lows = lows[-period:]
    high_20d = max(window_highs)
    low_20d = min(window_lows)
    if high_20d <= 0 or low_20d <= 0:
        return empty

    dist_high = (high_20d - close) / high_20d * 100
    dist_low = (close - low_20d) / low_20d * 100
    prior_high = max(highs[-period:-1])
    prior_low = min(lows[-period:-1])

    return {
        "區間20日高": round(high_20d, 2),
        "區間20日低": round(low_20d, 2),
        "距20日高_%": round(dist_high, 2),
        "距20日低_%": round(dist_low, 2),
        "突破20日高": "是" if close > prior_high else "否",
        "跌破20日低": "是" if close < prior_low else "否",
    }


def compute_summary_fields(
    daily_rows: list[dict[str, Any]],
    *,
    lookback_days: int,
    price_closes: list[float] | None = None,
    price_highs: list[float] | None = None,
    price_lows: list[float] | None = None,
    price_volumes: list[int] | None = None,
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
    trailing_closes = price_closes if price_closes else valid_closes
    trailing_highs = price_highs if price_highs else [
        _to_float(row.get("最高價"))
        for row in daily_rows
        if _to_float(row.get("最高價")) is not None
    ]
    trailing_lows = price_lows if price_lows else [
        _to_float(row.get("最低價"))
        for row in daily_rows
        if _to_float(row.get("最低價")) is not None
    ]

    ma5_val = _moving_average(trailing_closes, MA5_PERIOD)
    ma5 = ma5_val if ma5_val is not None else ""
    ma10_val = _moving_average(trailing_closes, MA10_PERIOD)
    ma10 = ma10_val if ma10_val is not None else ""
    ma20_val = _moving_average(trailing_closes, MA20_PERIOD)
    ma20 = ma20_val if ma20_val is not None else ""

    last_close = trailing_closes[-1] if trailing_closes else None
    first_close = valid_closes[0] if valid_closes else None
    period_return = ""
    if first_close and last_close and first_close != 0:
        period_return = round((last_close - first_close) / first_close * 100, 2)

    ma_deviation = _ma_deviation_pct(last_close, ma5_val)
    ma10_deviation = _ma_deviation_pct(last_close, ma10_val)
    ma20_deviation = _ma_deviation_pct(last_close, ma20_val)

    range_fields = _range_summary_fields(
        price_highs or [],
        price_lows or [],
        last_close,
    )
    volume_fields = _volume_summary_fields(price_volumes or [])
    cross_fields = _ma_cross_summary_fields(trailing_closes)
    atr_val = _atr_wilder(trailing_highs, trailing_lows, trailing_closes)

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
        "MA10": ma10,
        "收盤偏離MA10_%": ma10_deviation,
        "MA20": ma20,
        "收盤偏離MA20_%": ma20_deviation,
        "MA20斜率_%": _ma20_slope_pct(trailing_closes),
        "RSI14": _rsi_wilder(trailing_closes),
        "ATR14": atr_val,
        "ATR14_%": _atr_pct(atr_val, last_close),
        "ADX14": _adx_wilder(trailing_highs, trailing_lows, trailing_closes),
        "券資比_%": _margin_short_ratio_pct(last_margin, last_short),
        "融資動能_%": _margin_momentum_pct(first_margin, last_margin),
        **range_fields,
        **volume_fields,
        **cross_fields,
    }


def market_context_from_rows(
    rows: dict[str, dict[str, Any]],
    trade_date: str,
    lookback_dates: list[str],
) -> dict[str, Any]:
    """Compute 加權指數脈絡 from an already-fetched TAIEX price index.

    Uses only rows on or before ``trade_date`` (no look-ahead). Safe to pass a
    longer pre-fetched range (e.g. backfill once for the full span) — MA5/MA20
    only consume the trailing period closes.
    """
    empty = {col: "" for col in MARKET_COLUMNS}
    if not lookback_dates:
        lookback_dates = [trade_date]

    closes_by_date = {
        day: _to_float(rows[day].get("close"))
        for day in sorted(rows)
        if day <= trade_date and _to_float(rows[day].get("close")) is not None
    }
    if not closes_by_date:
        return empty

    trailing_closes = [closes_by_date[day] for day in sorted(closes_by_date)]
    last_close = trailing_closes[-1]
    ma5_val = _moving_average(trailing_closes, MA5_PERIOD)
    ma20_val = _moving_average(trailing_closes, MA20_PERIOD)

    today_row = rows.get(trade_date, {})
    spread = _to_float(today_row.get("spread"))
    today_close = _to_float(today_row.get("close"))
    change_pct: float | str = ""
    if spread is not None and today_close is not None:
        prev_close = today_close - spread
        if prev_close:
            change_pct = round(spread / prev_close * 100, 2)

    first_close = None
    for day in sorted(closes_by_date):
        if day >= lookback_dates[0]:
            first_close = closes_by_date[day]
            break
    period_return: float | str = ""
    if first_close and last_close and first_close != 0:
        period_return = round((last_close - first_close) / first_close * 100, 2)

    return {
        "大盤收盤": last_close,
        "大盤漲跌幅_%": change_pct,
        "大盤MA5": ma5_val if ma5_val is not None else "",
        "大盤MA20": ma20_val if ma20_val is not None else "",
        "大盤收盤偏離MA5_%": _ma_deviation_pct(last_close, ma5_val),
        "大盤收盤偏離MA20_%": _ma_deviation_pct(last_close, ma20_val),
        "大盤區間漲跌幅_%": period_return,
    }


def fetch_market_context(
    client: FinMindClient,
    trade_date: str,
    lookback_dates: list[str],
) -> dict[str, Any]:
    """Fetch 加權指數（TAIEX）context: 收盤、當日漲跌幅、MA5/MA20、區間漲跌幅。

    大盤脈絡屬全市場資料（與個股無關），單日 job 抓一次即可共用；歷史回補請改用
    ``market_context_from_rows`` 對一次抓取的區間做本地切日。
    """
    empty = {col: "" for col in MARKET_COLUMNS}
    if not lookback_dates:
        lookback_dates = [trade_date]

    ma_dates = resolve_lookback_dates(trade_date, MA20_PERIOD)
    range_start = min(lookback_dates[0], ma_dates[0])
    try:
        rows = index_rows_by_date(
            client.fetch_dataset(
                "TaiwanStockPrice",
                stock_id=MARKET_INDEX_ID,
                start_date=range_start,
                end_date=trade_date,
            )
        )
    except Exception as exc:  # 大盤為附加脈絡，失敗不應中斷個股抓取
        print(f"WARNING: 加權指數脈絡取得失敗：{exc}", file=sys.stderr)
        return empty

    return market_context_from_rows(rows, trade_date, lookback_dates)


def build_chart_history_rows(
    datasets: dict[str, dict[str, dict[str, Any]]],
    chart_dates: list[str],
) -> list[dict[str, Any]]:
    """Slim OHLCV rows for website charts (separate from chip lookback history)."""
    price_by_date = datasets["price"]
    rows: list[dict[str, Any]] = []
    for day in chart_dates:
        price_row = price_by_date.get(day)
        if not price_row:
            continue
        close = price_row.get("close", "")
        if close == "":
            continue
        trading_volume = int(price_row.get("Trading_Volume") or 0)
        rows.append(
            {
                "日期": day,
                "開盤價": price_row.get("open", ""),
                "最高價": price_row.get("max", ""),
                "最低價": price_row.get("min", ""),
                "收盤價": close,
                "成交量_張": shares_to_lots(trading_volume) if trading_volume else "",
                "漲跌幅": price_row.get("spread", ""),
            }
        )
    return rows


def build_stock_report(
    client: FinMindClient,
    stock_id: str,
    stock_name: str,
    trade_date: str,
    lookback_dates: list[str],
    yahoo: YahooMajorFlowClient | None,
    market_context: dict[str, Any] | None = None,
    chart_lookback_days: int = DEFAULT_CHART_LOOKBACK_DAYS,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not lookback_dates:
        lookback_dates = [trade_date]

    chart_days = max(chart_lookback_days, MA20_PERIOD)
    ma_price_dates = resolve_lookback_dates(trade_date, MA20_PERIOD)
    chart_dates = resolve_lookback_dates(trade_date, chart_days)
    range_start = min(lookback_dates[0], ma_price_dates[0], chart_dates[0])
    datasets = fetch_stock_datasets(client, stock_id, range_start, trade_date)
    price_closes = _trailing_closes_from_dataset(datasets["price"], trade_date)
    price_highs, price_lows = _trailing_highs_lows_from_dataset(
        datasets["price"], trade_date
    )
    price_volumes = _trailing_volumes_from_dataset(datasets["price"], trade_date)

    daily_rows: list[dict[str, Any]] = []
    for day in lookback_dates:
        row = build_daily_row(stock_id, stock_name, day, datasets)
        if yahoo is not None:
            row = attach_major_flow(row, yahoo, quiet=True)
        daily_rows.append(row)

    chart_history = build_chart_history_rows(datasets, chart_dates)

    summary = compute_summary_fields(
        daily_rows,
        lookback_days=len(lookback_dates),
        price_closes=price_closes,
        price_highs=price_highs,
        price_lows=price_lows,
        price_volumes=price_volumes,
    )
    market = market_context or {col: "" for col in MARKET_COLUMNS}
    snapshot = {**daily_rows[-1], **summary, **market}
    return snapshot, daily_rows, chart_history


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def stock_report_dir(trade_date: str, *, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else project_root()
    path = base / "reports" / "stock" / trade_date
    path.mkdir(parents=True, exist_ok=True)
    return path


def stock_csv_path(trade_date: str, stock_id: str) -> Path:
    return stock_report_dir(trade_date) / f"tw_stock_{stock_id}.csv"


def stock_history_csv_path(trade_date: str, stock_id: str) -> Path:
    return stock_report_dir(trade_date) / f"tw_stock_{stock_id}_history.csv"


def stock_chart_history_csv_path(trade_date: str, stock_id: str) -> Path:
    return stock_report_dir(trade_date) / f"tw_stock_{stock_id}_chart_history.csv"


def stock_facts_json_path(trade_date: str, stock_id: str) -> Path:
    return stock_report_dir(trade_date) / f"tw_stock_{stock_id}.facts.json"


def _load_chip_signals():
    root = Path(__file__).resolve().parents[3]
    ui_path = str(root / "ui")
    if ui_path not in sys.path:
        sys.path.insert(0, ui_path)
    from chip_signals import build_chip_facts, write_facts_json

    return build_chip_facts, write_facts_json


@dataclass
class FetchRunResult:
    trade_date: str
    date_note: str | None
    output_dir: Path
    snapshot_paths: list[Path] = field(default_factory=list)
    history_paths: list[Path] = field(default_factory=list)
    chart_history_paths: list[Path] = field(default_factory=list)
    lookback_dates: list[str] = field(default_factory=list)
    chart_lookback_days: int = DEFAULT_CHART_LOOKBACK_DAYS


def run_fetch(
    *,
    stocks: str | list[str] | None = None,
    date: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    chart_lookback_days: int = DEFAULT_CHART_LOOKBACK_DAYS,
    watchlist: Path | None = None,
    skip_major: bool = False,
    token: str | None = None,
) -> FetchRunResult:
    """Fetch chip CSVs for one or more stocks. Raises on failure."""
    auth = (token if token is not None else os.environ.get("FINMIND_TOKEN", "")).strip()
    lookback_days = max(1, lookback_days)
    chart_lookback_days = max(MA20_PERIOD, chart_lookback_days)

    stock_ids = load_stock_ids(stocks, watchlist)
    client = FinMindClient(token=auth)
    trade_date, date_note = resolve_trade_date(date, finmind_token=auth)
    lookback_dates = resolve_lookback_dates(trade_date, lookback_days)
    stock_names = load_stock_names(client)
    yahoo = None if skip_major else YahooMajorFlowClient()
    market_context = fetch_market_context(client, trade_date, lookback_dates)

    output_dir = stock_report_dir(trade_date)
    snapshot_paths: list[Path] = []
    history_paths: list[Path] = []
    chart_history_paths: list[Path] = []

    for stock_id in stock_ids:
        snapshot, history, chart_history = build_stock_report(
            client,
            stock_id,
            stock_names.get(stock_id, ""),
            trade_date,
            lookback_dates,
            yahoo,
            market_context=market_context,
            chart_lookback_days=chart_lookback_days,
        )

        snapshot_path = stock_csv_path(trade_date, stock_id)
        history_path = stock_history_csv_path(trade_date, stock_id)
        chart_history_path = stock_chart_history_csv_path(trade_date, stock_id)

        snapshot_columns = DAILY_COLUMNS + SUMMARY_COLUMNS + MARKET_COLUMNS
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
        pd.DataFrame(chart_history, columns=CHART_HISTORY_COLUMNS).to_csv(
            chart_history_path,
            index=False,
            encoding="utf-8-sig",
        )

        snapshot_paths.append(snapshot_path)
        history_paths.append(history_path)
        chart_history_paths.append(chart_history_path)

        try:
            build_chip_facts, write_facts_json = _load_chip_signals()
            facts = build_chip_facts(snapshot, history)
            write_facts_json(stock_facts_json_path(trade_date, stock_id), facts)
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

    return FetchRunResult(
        trade_date=trade_date,
        date_note=date_note,
        output_dir=output_dir,
        snapshot_paths=snapshot_paths,
        history_paths=history_paths,
        chart_history_paths=chart_history_paths,
        lookback_dates=lookback_dates,
        chart_lookback_days=chart_lookback_days,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
        "--chart-lookback-days",
        type=int,
        default=DEFAULT_CHART_LOOKBACK_DAYS,
        help=(
            f"網站圖表回看交易日天數（預設 {DEFAULT_CHART_LOOKBACK_DAYS}；"
            "與籌碼回看分開）"
        ),
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        result = run_fetch(
            stocks=args.stocks,
            date=args.date,
            lookback_days=args.lookback_days,
            chart_lookback_days=args.chart_lookback_days,
            watchlist=args.watchlist,
            skip_major=args.skip_major,
        )
        lookback_dates = result.lookback_dates
        print(f"交易日期: {result.trade_date}")
        if lookback_dates:
            print(
                f"回看天數: {len(lookback_dates)}"
                f"（{lookback_dates[0]}～{result.trade_date}）"
            )
        if result.chart_history_paths:
            print(
                f"圖表回看: {result.chart_lookback_days} 交易日"
                f"（檔案: *_chart_history.csv）"
            )
        if result.date_note:
            print(result.date_note)
        print(f"輸出目錄: {result.output_dir.resolve()}")
        for csv_path in result.snapshot_paths:
            print(f"輸出檔案: {csv_path.resolve()}")
        for csv_path in result.history_paths:
            print(f"歷史檔案: {csv_path.resolve()}")
        for csv_path in result.chart_history_paths:
            print(f"圖表歷史: {csv_path.resolve()}")
        print(f"股票檔數: {len(result.snapshot_paths)}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
