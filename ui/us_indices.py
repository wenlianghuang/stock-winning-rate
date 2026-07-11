"""Fetch US market index snapshots from Yahoo Finance (free, ~15m delayed)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import requests

USER_AGENT = "Mozilla/5.0 (compatible; stock-winning-rate/us-indices)"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

US_MARKET_INDICES: tuple[dict[str, str], ...] = (
    {"symbol": "^DJI", "name": "道瓊工業指數", "short_name": "道瓊"},
    {"symbol": "^IXIC", "name": "那斯達克綜合指數", "short_name": "那斯達克"},
    {"symbol": "^SOX", "name": "費城半導體指數", "short_name": "費半"},
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
