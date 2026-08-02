"""Fetch US market index snapshots from Yahoo Finance (free, ~15m delayed)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

USER_AGENT = "Mozilla/5.0 (compatible; stock-winning-rate/us-indices)"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

US_MARKET_INDICES: tuple[dict[str, str], ...] = (
    {"symbol": "^DJI", "name": "道瓊工業指數", "short_name": "道瓊"},
    {"symbol": "^IXIC", "name": "那斯達克綜合指數", "short_name": "那斯達克"},
    {"symbol": "^SOX", "name": "費城半導體指數", "short_name": "費半"},
)

# Taiwan market-weekly cross-check set (no Dow for v1).
US_WEEKLY_INDICES: tuple[dict[str, str], ...] = (
    {"symbol": "^IXIC", "name": "那斯達克綜合指數", "short_name": "那斯達克", "key": "IXIC"},
    {"symbol": "^SOX", "name": "費城半導體指數", "short_name": "費半", "key": "SOX"},
)


@dataclass(frozen=True)
class IndexSnapshot:
    symbol: str
    name: str
    short_name: str
    price: float | None
    previous_close: float | None
    change_pct: float | None
    currency: str
    market_time: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_index_snapshot(
    symbol: str,
    *,
    name: str = "",
    short_name: str = "",
    session: requests.Session | None = None,
) -> IndexSnapshot:
    """Return latest quote for one Yahoo Finance index symbol."""
    http = session or requests.Session()
    encoded = requests.utils.quote(symbol, safe="")
    response = http.get(
        YAHOO_CHART_URL.format(symbol=encoded),
        params={"interval": "1d", "range": "5d"},
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result") or []
    if not result:
        return IndexSnapshot(
            symbol=symbol,
            name=name or symbol,
            short_name=short_name or symbol,
            price=None,
            previous_close=None,
            change_pct=None,
            currency="USD",
            market_time="",
        )

    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct: float | None = None
    if isinstance(price, (int, float)) and isinstance(previous, (int, float)) and previous:
        change_pct = (float(price) - float(previous)) / float(previous) * 100

    market_time = ""
    ts = meta.get("regularMarketTime")
    if isinstance(ts, (int, float)):
        market_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

    return IndexSnapshot(
        symbol=symbol,
        name=name or str(meta.get("longName") or meta.get("shortName") or symbol),
        short_name=short_name or symbol,
        price=float(price) if isinstance(price, (int, float)) else None,
        previous_close=float(previous) if isinstance(previous, (int, float)) else None,
        change_pct=change_pct,
        currency=str(meta.get("currency", "USD")),
        market_time=market_time,
    )


def fetch_us_market_indices(
    definitions: tuple[dict[str, str], ...] | None = None,
) -> list[IndexSnapshot]:
    """Fetch all configured US headline indices in display order."""
    items = definitions or US_MARKET_INDICES
    session = requests.Session()
    snapshots: list[IndexSnapshot] = []
    for item in items:
        snapshots.append(
            fetch_index_snapshot(
                item["symbol"],
                name=item.get("name", item["symbol"]),
                short_name=item.get("short_name", item["symbol"]),
                session=session,
            )
        )
    return snapshots


def fetch_us_market_indices_payload(
    definitions: tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    """JSON-serializable bundle for API responses."""
    snapshots = fetch_us_market_indices(definitions)
    return {
        "indices": [snap.to_dict() for snap in snapshots],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "yahoo_finance",
        "delay_note": "Yahoo Finance 公開報價，盤中通常延遲約 15 分鐘",
    }


def _to_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def week_return_pct(first_close: float, last_close: float) -> float | None:
    if first_close == 0:
        return None
    return round((last_close - first_close) / first_close * 100, 2)


def parse_chart_daily_closes(payload: dict[str, Any]) -> dict[str, float]:
    """Parse Yahoo chart JSON into {YYYY-MM-DD: close}."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return {}
    timestamps = result[0].get("timestamp") or []
    quotes = (result[0].get("indicators") or {}).get("quote") or []
    if not quotes:
        return {}
    closes = quotes[0].get("close") or []
    out: dict[str, float] = {}
    for ts, close in zip(timestamps, closes, strict=False):
        if not isinstance(ts, (int, float)) or not isinstance(close, (int, float)):
            continue
        day = datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
        out[day] = float(close)
    return out


def closes_in_calendar_window(
    closes: dict[str, float],
    start: date | str,
    end: date | str,
) -> list[tuple[str, float]]:
    """Return sorted (date, close) sessions within [start, end] inclusive."""
    start_d = _to_date(start)
    end_d = _to_date(end)
    rows = [
        (day, close)
        for day, close in closes.items()
        if start_d <= date.fromisoformat(day) <= end_d
    ]
    rows.sort(key=lambda item: item[0])
    return rows


def index_week_from_closes(
    closes: dict[str, float],
    *,
    symbol: str,
    name: str,
    short_name: str,
    start: date | str,
    end: date | str,
) -> dict[str, Any] | None:
    """Build one index week block from daily closes; None if <2 sessions."""
    sessions = closes_in_calendar_window(closes, start, end)
    if len(sessions) < 2:
        return None
    first_day, first_close = sessions[0]
    last_day, last_close = sessions[-1]
    ret = week_return_pct(first_close, last_close)
    if ret is None:
        return None
    return {
        "symbol": symbol,
        "name": name,
        "short_name": short_name,
        "first_session": first_day,
        "last_session": last_day,
        "first_close": first_close,
        "last_close": last_close,
        "week_return_pct": ret,
        "session_count": len(sessions),
    }


def fetch_index_week_bars(
    symbol: str,
    start_date: date | str,
    end_date: date | str,
    *,
    session: requests.Session | None = None,
) -> dict[str, float]:
    """Fetch Yahoo daily closes and return bars within the calendar window."""
    start_d = _to_date(start_date)
    end_d = _to_date(end_date)
    # period2 is exclusive on Yahoo; pad one day past end.
    period1 = int(datetime(start_d.year, start_d.month, start_d.day, tzinfo=timezone.utc).timestamp())
    end_exclusive = end_d + timedelta(days=1)
    period2 = int(
        datetime(
            end_exclusive.year,
            end_exclusive.month,
            end_exclusive.day,
            tzinfo=timezone.utc,
        ).timestamp()
    )
    http = session or requests.Session()
    encoded = requests.utils.quote(symbol, safe="")
    response = http.get(
        YAHOO_CHART_URL.format(symbol=encoded),
        params={"interval": "1d", "period1": period1, "period2": period2},
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return parse_chart_daily_closes(response.json())


def build_us_week_block(
    week_monday: date | str,
    week_friday: date | str,
    *,
    session: requests.Session | None = None,
    definitions: tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    """Deterministic IXIC/SOX week returns aligned to TW calendar Mon–Fri."""
    start_d = _to_date(week_monday)
    end_d = _to_date(week_friday)
    items = definitions or US_WEEKLY_INDICES
    http = session or requests.Session()
    indices: dict[str, Any] = {}
    for item in items:
        key = item.get("key") or item["symbol"].lstrip("^")
        try:
            closes = fetch_index_week_bars(
                item["symbol"],
                start_d,
                end_d,
                session=http,
            )
            block = index_week_from_closes(
                closes,
                symbol=item["symbol"],
                name=item.get("name", item["symbol"]),
                short_name=item.get("short_name", item["symbol"]),
                start=start_d,
                end=end_d,
            )
        except (requests.RequestException, ValueError, KeyError, TypeError):
            block = None
        indices[key] = block

    usable = [v for v in indices.values() if isinstance(v, dict) and v.get("week_return_pct") is not None]
    return {
        "source": "yahoo_finance",
        "calendar_start": start_d.isoformat(),
        "calendar_end": end_d.isoformat(),
        "available": len(usable) > 0,
        "indices": indices,
    }
