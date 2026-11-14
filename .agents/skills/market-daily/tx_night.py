"""TAIFEX TX after-hours row for the next cash open.

The night session opens on a TWSE trading day at 15:00 and closes at 05:00
the next calendar day. Sundays and public holidays do not open a session.
The CSV 交易日期 is the next regular session (for_session). Across a weekend
that row is Friday 15:00–Saturday 05:00, filed under Monday.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import sys
import time
from collections.abc import Callable
from typing import Any

TAIFEX_DOWNLOAD_URL = "https://www.taifex.com.tw/cht/3/futDataDown"
TAIFEX_REFERER = "https://www.taifex.com.tw/cht/3/dlFutDailyMarketView"
TX_FETCH_ATTEMPTS = 3
TX_FETCH_BACKOFF_SEC = 5.0

CsvFetch = Callable[[str, str], str]


def empty_tx_night(*, reason: str) -> dict[str, Any]:
    return {
        "source": "taifex",
        "available": False,
        "skipped": reason.startswith("skip"),
        "skip_reason": reason,
        "contract": "TX",
        "contract_month": None,
        "session": "盤後",
        "attribution_date": None,
        "session_window": None,
        "close": None,
        "change": None,
        "change_pct": None,
        "volume": None,
        "prior_regular": None,
        "alignment_vs_ixic": "unavailable",
    }


def session_window_label(trade_date: str) -> str:
    """Clock window: trading-day 15:00 through the next calendar morning 05:00."""
    start = dt.date.fromisoformat(trade_date)
    end = start + dt.timedelta(days=1)
    return f"{start.isoformat()} 15:00–{end.isoformat()} 05:00"


def decode_taifex_body(raw: bytes) -> str:
    if not raw or raw.lstrip().startswith(b"<") or raw.lstrip().lower().startswith(b"<!doctype"):
        raise ValueError("期交所回應不是行情 CSV")
    for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp950", errors="replace")


def _slash_to_iso(text: str) -> str | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw, "%Y/%m/%d").date().isoformat()
    except ValueError:
        return None


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "—", "NULL", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _points(value: float | None) -> int | float | None:
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return round(float(value), 2)


def _volume(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value))


def parse_tx_csv(text: str) -> list[dict[str, Any]]:
    """Parse a futDataDown CSV into outright TX rows (spread months dropped)."""
    if "<html" in text[:300].lower() or "<head" in text[:300].lower():
        raise ValueError("期交所回應不是行情 CSV")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        item: dict[str, str] = {}
        for key, value in raw.items():
            if not key or isinstance(value, list):
                continue
            item[str(key).strip()] = str(value or "").strip()
        contract = item.get("契約") or ""
        if contract and contract != "TX":
            continue
        month = (item.get("到期月份(週別)") or "").strip()
        if not month or "/" in month:
            continue
        session = (item.get("交易時段") or "").strip()
        if session not in {"一般", "盤後"}:
            continue
        trade_date = _slash_to_iso(item.get("交易日期") or "")
        if trade_date is None:
            continue
        close = _to_float(item.get("收盤價"))
        change = _to_float(item.get("漲跌價"))
        change_pct = _to_float(item.get("漲跌%"))
        if change_pct is not None:
            change_pct = round(change_pct, 2)
        rows.append(
            {
                "date": trade_date,
                "contract": "TX",
                "month": month,
                "session": session,
                "close": _points(close),
                "change": _points(change),
                "change_pct": change_pct,
                "volume": _volume(_to_float(item.get("成交量"))),
                "settlement": _points(_to_float(item.get("結算價"))),
            }
        )
    return rows


def select_front_month(rows: list[dict[str, Any]], *, date: str, session: str) -> dict[str, Any] | None:
    """Highest volume in that session. Ties go to the nearer (smaller) contract month."""
    chosen: dict[str, Any] | None = None
    chosen_rank: tuple[int, int] | None = None
    for row in rows:
        if row.get("date") != date or row.get("session") != session:
            continue
        volume = int(row.get("volume") or 0)
        month = str(row.get("month") or "")
        month_key = int(month) if month.isdigit() else 10**12
        rank = (volume, -month_key)
        if chosen is None or chosen_rank is None or rank > chosen_rank:
            chosen = row
            chosen_rank = rank
    return chosen


def _same_month_regular(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
    month: str,
) -> dict[str, Any] | None:
    for row in rows:
        if (
            row.get("date") == trade_date
            and row.get("session") == "一般"
            and str(row.get("month")) == month
        ):
            return row
    return None


def build_tx_night_block(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
    for_session: str,
) -> dict[str, Any]:
    night = select_front_month(rows, date=for_session, session="盤後")
    if night is None or night.get("close") is None or night.get("change") is None:
        return empty_tx_night(reason=f"missing_session:{for_session}")
    month = str(night["month"])
    regular = _same_month_regular(rows, trade_date=trade_date, month=month)
    prior: dict[str, Any] | None = None
    if regular is not None:
        prior = {
            "trade_date": trade_date,
            "contract_month": month,
            "close": regular.get("close"),
            "settlement": regular.get("settlement"),
        }
    return {
        "source": "taifex",
        "available": True,
        "skipped": False,
        "skip_reason": None,
        "contract": "TX",
        "contract_month": month,
        "session": "盤後",
        "attribution_date": for_session,
        "session_window": session_window_label(trade_date),
        "close": night.get("close"),
        "change": night.get("change"),
        "change_pct": night.get("change_pct"),
        "volume": night.get("volume"),
        "prior_regular": prior,
        "alignment_vs_ixic": "unavailable",
    }


def download_tx_csv(start_date: str, end_date: str, *, timeout: float = 30.0) -> str:
    import requests

    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    if start > end:
        start, end = end, start
    response = requests.post(
        TAIFEX_DOWNLOAD_URL,
        data={
            "down_type": "1",
            "commodity_id": "TX",
            "commodity_id2": "",
            "queryStartDate": start.strftime("%Y/%m/%d"),
            "queryEndDate": end.strftime("%Y/%m/%d"),
        },
        headers={
            "Referer": TAIFEX_REFERER,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "stock-winning-rate/market-daily",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return decode_taifex_body(response.content)


def fetch_tx_night_block(
    *,
    trade_date: str,
    for_session: str,
    attempts: int = TX_FETCH_ATTEMPTS,
    backoff_sec: float = TX_FETCH_BACKOFF_SEC,
    sleep: Callable[[float], None] | None = None,
    fetch_csv: CsvFetch | None = None,
) -> dict[str, Any]:
    """Fetch the for_session 盤後 row. Missing data stays unavailable; it does not raise."""
    sleeper = sleep or time.sleep
    tries = max(1, int(attempts))
    loader = fetch_csv or download_tx_csv
    last_reason = "missing_session"
    for i in range(tries):
        try:
            text = loader(trade_date, for_session)
            block = build_tx_night_block(
                parse_tx_csv(text),
                trade_date=trade_date,
                for_session=for_session,
            )
            if block.get("available"):
                return block
            last_reason = str(block.get("skip_reason") or "missing_session")
        except Exception as exc:  # noqa: BLE001
            last_reason = f"fetch_error:{exc}"
        if i < tries - 1:
            print(
                f"WARN: TX night fetch retry {i + 1}/{tries}：{last_reason}",
                file=sys.stderr,
            )
            sleeper(max(0.0, float(backoff_sec)))
    return empty_tx_night(reason=last_reason)
