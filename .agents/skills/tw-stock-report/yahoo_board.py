"""Concept-board rows from Yahoo 股市. Does not call FinMind.

Price comes from the chart API. 三大法人 come from
``StockServices.tradesWithQuoteStats`` (the 法人買賣 page). 主力 comes from
the existing broker-trades resource. 融資融券餘額 come from
``StockServices.creditsWithQuoteStats`` (the 資券變化 page). Chip regime
uses the same five-session window as the board's period return. 當沖 stays
on the full FinMind report.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

TAIPEI = ZoneInfo("Asia/Taipei")
PERIOD_SESSIONS = 5
# Five sessions, the prior close, and a few extra rows for holidays.
CHIP_HISTORY_LIMIT = PERIOD_SESSIONS + 6
YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
YAHOO_RESOURCE = "https://tw.stock.yahoo.com/_td-stock/api/resource/{path}"
USER_AGENT = "Mozilla/5.0 (compatible; stock-winning-rate/us-indices)"
CONTEXT = {
    "intl": "tw",
    "lang": "zh-Hant-TW",
    "region": "TW",
    "site": "finance",
    "tz": "Asia/Taipei",
    "ver": "1.4.931",
    "device": "desktop",
}


def _session() -> requests.Session:
    http = requests.Session()
    http.headers["User-Agent"] = USER_AGENT
    return http


def _pct(latest: float, earlier: float) -> float | None:
    if earlier == 0:
        return None
    return round((latest - earlier) / earlier * 100.0, 2)


def _price_trend(period_return: float | None) -> str:
    if period_return is None:
        return "unknown"
    if period_return > 2:
        return "up"
    if period_return < -2:
        return "down"
    return "flat"


def chart_bars(payload: dict[str, Any]) -> list[tuple[str, float]]:
    """Daily (Taipei date, close) from a Yahoo chart payload, oldest first."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    block = result[0]
    timestamps = block.get("timestamp") or []
    quotes = (block.get("indicators") or {}).get("quote") or []
    if not quotes:
        return []
    closes = quotes[0].get("close") or []
    bars: list[tuple[str, float]] = []
    for ts, close in zip(timestamps, closes, strict=False):
        if not isinstance(ts, (int, float)) or not isinstance(close, (int, float)):
            continue
        day = datetime.fromtimestamp(float(ts), tz=TAIPEI).date().isoformat()
        bars.append((day, float(close)))
    bars.sort(key=lambda item: item[0])
    return bars


def _slice_to_date(
    bars: list[tuple[str, float]],
    trade_date: str | None,
) -> list[tuple[str, float]]:
    if not trade_date:
        return bars
    return [bar for bar in bars if bar[0] <= trade_date]


def _row_day(row: dict[str, Any]) -> str:
    raw = str(row.get("date") or "").strip()
    if not raw:
        return ""
    return raw.split("T", 1)[0].replace("/", "-")


def _int_field(row: dict[str, Any] | None, key: str) -> int | None:
    if not row or row.get(key) is None:
        return None
    text = str(row.get(key)).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _rows_by_date(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = _row_day(row)
        if day:
            found[day] = row
    return found


def credit_chip_inputs(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
    anchor_date: str | None,
) -> tuple[int | None, int | None, int | None]:
    """Today's margin balance change, plus margin and short change since ``anchor_date``.

    Yahoo's credit row has balances, not a diff column. Today's change is the
    last balance minus the previous session. The period change uses the same
    prior close the board uses for period return.
    """
    by_date = _rows_by_date(rows)
    today = by_date.get(trade_date)
    today_delta = None
    if today is not None:
        earlier = [day for day in by_date if day < trade_date]
        if earlier:
            previous = by_date[max(earlier)]
            last_margin = _int_field(today, "financingTotalVolK")
            previous_margin = _int_field(previous, "financingTotalVolK")
            if last_margin is not None and previous_margin is not None:
                today_delta = last_margin - previous_margin
    period_margin = None
    period_short = None
    anchor = by_date.get(anchor_date or "")
    if today is not None and anchor is not None:
        last_margin = _int_field(today, "financingTotalVolK")
        anchor_margin = _int_field(anchor, "financingTotalVolK")
        last_short = _int_field(today, "shortTotalVolK")
        anchor_short = _int_field(anchor, "shortTotalVolK")
        if last_margin is not None and anchor_margin is not None:
            period_margin = last_margin - anchor_margin
        if last_short is not None and anchor_short is not None:
            period_short = last_short - anchor_short
    return today_delta, period_margin, period_short


def foreign_cumulative(
    rows: list[dict[str, Any]],
    session_dates: list[str],
) -> int | None:
    """Sum 外資買賣超 over the five board sessions. A missing day stays unknown."""
    if len(session_dates) < PERIOD_SESSIONS:
        return None
    by_date = _rows_by_date(rows)
    total = 0
    for day in session_dates:
        lots = _int_field(by_date.get(day), "foreignDiffVolK")
        if lots is None:
            return None
        total += lots
    return total


def yahoo_chip_inputs_ready(payload: dict[str, Any]) -> bool:
    """Sidecars written before 資券／區間外資 were stored should be fetched again."""
    return "foreign_cum_lots" in payload and "margin_period_delta_lots" in payload


def assemble_yahoo_board_facts(
    *,
    stock_id: str,
    stock_name: str | None,
    bars: list[tuple[str, float]],
    market_bars: list[tuple[str, float]],
    trades: dict[str, Any] | None,
    major_net: int | None,
    trade_date: str | None = None,
    margin_today_delta: int | None = None,
    margin_period_delta: int | None = None,
    short_period_delta: int | None = None,
    foreign_cum: int | None = None,
) -> dict[str, Any] | None:
    """Build the fields the concept board reads. ``None`` when price is missing."""
    from chip_signals import (
        _detect_chip_regime,
        _detect_divergences,
        _institutional_consensus,
        _margin_short_regime,
        _relative_strength,
        _sign,
    )

    series = _slice_to_date(bars, trade_date)
    if not series:
        return None
    day, close = series[-1]
    close = round(float(close), 2)
    previous = series[-2][1] if len(series) >= 2 else None
    today_change = _pct(close, previous) if previous is not None else None
    anchor = series[-(PERIOD_SESSIONS + 1)][1] if len(series) > PERIOD_SESSIONS else None
    period_return = _pct(close, anchor) if anchor is not None else None

    market = _slice_to_date(market_bars, day)
    market_change = None
    market_period = None
    if len(market) >= 2:
        market_change = _pct(market[-1][1], market[-2][1])
    if len(market) > PERIOD_SESSIONS:
        market_period = _pct(market[-1][1], market[-(PERIOD_SESSIONS + 1)][1])

    foreign = _trade_lots(trades, "foreignDiffVolK")
    trust = _trade_lots(trades, "investmentTrustDiffVolK")
    dealer = _trade_lots(trades, "dealerDiffVolK")
    consensus = _institutional_consensus(foreign, trust, dealer)
    divergences = _detect_divergences(
        today_change=today_change,
        foreign_net=foreign,
        margin_today_delta=margin_today_delta,
        period_return=period_return,
        margin_delta=margin_period_delta,
    )
    major_available = major_net is not None
    major_divergence = bool(
        major_available
        and foreign is not None
        and _sign(major_net) != 0
        and _sign(foreign) != 0
        and _sign(major_net) != _sign(foreign)
    )
    trend = _price_trend(period_return)
    regime = _detect_chip_regime(
        foreign_direction=_sign(foreign),
        foreign_cum=foreign_cum,
        institutional_consensus=consensus,
        price_trend=trend,
        divergences=divergences,
        major_foreign_divergence=major_divergence,
        margin_short_regime=_margin_short_regime(margin_period_delta, short_period_delta),
        volume_price_divergence="unknown",
    )
    return {
        "stock_id": stock_id,
        "stock_name": stock_name or stock_id,
        "trade_date": day,
        "close": close,
        "today_change_pct": today_change,
        "period_return_pct": period_return,
        "foreign_net_lots": foreign,
        "trust_net_lots": trust,
        "dealer_net_lots": dealer,
        "major_net_lots": major_net,
        "margin_today_delta_lots": margin_today_delta,
        "margin_period_delta_lots": margin_period_delta,
        "short_period_delta_lots": short_period_delta,
        "foreign_cum_lots": foreign_cum,
        "chip_regime": regime,
        "rs_today": _relative_strength(today_change, market_change, band=0.5),
        "rs_period": _relative_strength(period_return, market_period, band=1.0),
        "source": "yahoo",
    }


def _trade_lots(trades: dict[str, Any] | None, key: str) -> int | None:
    if not trades or trades.get(key) is None:
        return None
    try:
        return int(trades[key])
    except (TypeError, ValueError):
        return None


def _get_json(
    http: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    try:
        response = http.get(url, params=params, headers=headers, timeout=20)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_chart(
    http: requests.Session,
    symbol: str,
) -> tuple[list[tuple[str, float]], str | None] | None:
    encoded = quote(symbol, safe="")
    for template in YAHOO_CHART_URLS:
        payload = _get_json(
            http,
            template.format(symbol=encoded),
            params={"interval": "1d", "range": "3mo"},
        )
        if payload is None:
            continue
        bars = chart_bars(payload)
        if not bars:
            continue
        result = (payload.get("chart") or {}).get("result") or []
        meta = result[0].get("meta") if result else {}
        raw_name = ""
        if isinstance(meta, dict):
            raw_name = str(meta.get("shortName") or meta.get("longName") or "").strip()
        if raw_name.upper().endswith((".TW", ".TWO")):
            raw_name = ""
        return bars, raw_name or None
    return None


def fetch_chart_bars(http: requests.Session, symbol: str) -> list[tuple[str, float]]:
    loaded = _load_chart(http, symbol)
    if loaded is None:
        return []
    return loaded[0]


def resolve_listing_symbol(
    http: requests.Session,
    stock_id: str,
) -> tuple[str, list[tuple[str, float]], str | None] | None:
    """Listed names use ``.TW``; OTC names such as 雙鴻 use ``.TWO``."""
    for suffix in (".TW", ".TWO"):
        symbol = f"{stock_id}{suffix}"
        loaded = _load_chart(http, symbol)
        if loaded is not None:
            bars, quote_name = loaded
            return symbol, bars, quote_name
    return None


def fetch_market_bars(http: requests.Session | None = None) -> list[tuple[str, float]]:
    client = http or _session()
    return fetch_chart_bars(client, "^TWII")


def _iso_date(trade_date: str) -> str:
    return quote(f"{trade_date}T00:00:00+08:00", safe="")


def _history_rows(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]] | None:
    if not payload:
        return None
    if key == "list":
        rows = payload.get("list")
    else:
        result = (payload.get("data") or {}).get("result") or {}
        rows = result.get(key) if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def fetch_trade_history(
    http: requests.Session,
    symbol: str,
    *,
    limit: int = CHIP_HISTORY_LIMIT,
) -> list[dict[str, Any]] | None:
    """Dated 三大法人 rows, newest first. One call covers the five-session sum."""
    path = f"StockServices.tradesWithQuoteStats;symbol={symbol};period=day;limit={limit}"
    payload = _get_json(
        http,
        YAHOO_RESOURCE.format(path=path),
        params=CONTEXT,
        headers={"Referer": f"https://tw.stock.yahoo.com/quote/{symbol}/institutional-trading"},
    )
    return _history_rows(payload, "list")


def fetch_credit_rows(
    http: requests.Session,
    symbol: str,
    *,
    limit: int = CHIP_HISTORY_LIMIT,
) -> list[dict[str, Any]] | None:
    """Dated 融資融券 balances from the 資券變化 page."""
    path = f"StockServices.creditsWithQuoteStats;symbol={symbol};period=day;limit={limit}"
    payload = _get_json(
        http,
        YAHOO_RESOURCE.format(path=path),
        params=CONTEXT,
        headers={"Referer": f"https://tw.stock.yahoo.com/quote/{symbol}/margin"},
    )
    return _history_rows(payload, "credits")


def fetch_trades_row(http: requests.Session, symbol: str, trade_date: str) -> dict[str, Any] | None:
    path = f"StockServices.trades;symbol={symbol};date={_iso_date(trade_date)}"
    payload = _get_json(
        http,
        YAHOO_RESOURCE.format(path=path),
        params=CONTEXT,
        headers={"Referer": f"https://tw.stock.yahoo.com/quote/{symbol}/institutional-trading"},
    )
    rows = (payload or {}).get("list") or []
    if not rows or not isinstance(rows[0], dict):
        return None
    return rows[0]


def fetch_major_net(http: requests.Session, symbol: str, trade_date: str) -> int | None:
    path = f"StockServices.brokerTrades;crumb=;symbol={symbol};date={_iso_date(trade_date)}"
    payload = _get_json(
        http,
        YAHOO_RESOURCE.format(path=path),
        params=CONTEXT,
        headers={"Referer": f"https://tw.stock.yahoo.com/quote/{symbol}/broker-trading"},
    )
    rows = (payload or {}).get("list") or []
    if not rows or not isinstance(rows[0], dict):
        return None
    resolved = str(rows[0].get("date") or "").split("T", 1)[0]
    if resolved and resolved != trade_date:
        return None
    try:
        return int(rows[0]["totalDifferenceVolK"])
    except (KeyError, TypeError, ValueError):
        return None


def fetch_yahoo_board_facts(
    stock_id: str,
    trade_date: str | None,
    *,
    market_bars: list[tuple[str, float]] | None = None,
    stock_name: str | None = None,
    http: requests.Session | None = None,
) -> dict[str, Any] | None:
    """One stock. Network errors return ``None`` so the rest of the board still loads."""
    client = http or _session()
    listing = resolve_listing_symbol(client, stock_id)
    if listing is None:
        return None
    symbol, bars, quote_name = listing
    series = _slice_to_date(bars, trade_date)
    if not series:
        return None
    day = series[-1][0]
    session_dates = [bar_day for bar_day, _price in series[-PERIOD_SESSIONS:]]
    anchor_date = series[-(PERIOD_SESSIONS + 1)][0] if len(series) > PERIOD_SESSIONS else None
    history = fetch_trade_history(client, symbol) or []
    trades = _rows_by_date(history).get(day)
    if trades is None:
        trades = fetch_trades_row(client, symbol, day)
    credits = fetch_credit_rows(client, symbol) or []
    margin_today, margin_period, short_period = credit_chip_inputs(
        credits,
        trade_date=day,
        anchor_date=anchor_date,
    )
    major = fetch_major_net(client, symbol, day)
    market = market_bars if market_bars is not None else fetch_market_bars(client)
    return assemble_yahoo_board_facts(
        stock_id=stock_id,
        stock_name=stock_name or quote_name,
        bars=bars,
        market_bars=market,
        trades=trades,
        major_net=major,
        trade_date=day,
        margin_today_delta=margin_today,
        margin_period_delta=margin_period,
        short_period_delta=short_period,
        foreign_cum=foreign_cumulative(history, session_dates),
    )


def week_window(
    bars: list[tuple[str, float]],
    market_bars: list[tuple[str, float]],
    foreign_by_date: dict[str, int | None],
    trade_date: str | None = None,
    flows_by_date: dict[str, dict[str, int | None]] | None = None,
) -> dict[str, Any] | None:
    """Five sessions ending on ``trade_date``, plus the prior close as the return anchor.

    ``flows_by_date`` carries 外資、投信、自營 for each session. ``foreign_by_date``
    remains for callers that only have the foreign column.
    """
    from chip_signals import _relative_strength

    series = _slice_to_date(bars, trade_date)
    if len(series) <= PERIOD_SESSIONS:
        return None
    window = series[-PERIOD_SESSIONS:]
    anchor = series[-(PERIOD_SESSIONS + 1)][1]
    close = window[-1][1]
    week_return = _pct(close, anchor)
    sessions: list[dict[str, Any]] = []
    totals = {"foreign": 0, "trust": 0, "dealer": 0}
    complete = True
    for day, price in window:
        flow = dict((flows_by_date or {}).get(day) or {})
        if "foreign" not in flow:
            flow["foreign"] = foreign_by_date.get(day)
        session: dict[str, Any] = {
            "date": day,
            "close": round(float(price), 2),
        }
        for key in ("foreign", "trust", "dealer"):
            lots = flow.get(key)
            session_key = {
                "foreign": "foreign_net_lots",
                "trust": "trust_net_lots",
                "dealer": "dealer_net_lots",
            }[key]
            if lots is None:
                complete = False
                session[session_key] = None
            else:
                totals[key] += int(lots)
                session[session_key] = int(lots)
        sessions.append(session)
    market = _slice_to_date(market_bars, window[-1][0])
    market_period = None
    if len(market) > PERIOD_SESSIONS:
        market_period = _pct(market[-1][1], market[-(PERIOD_SESSIONS + 1)][1])
    return {
        "trade_date": window[-1][0],
        "sessions": sessions,
        "week_return_pct": week_return,
        "week_foreign_net_lots": totals["foreign"] if complete else None,
        "week_trust_net_lots": totals["trust"] if complete else None,
        "week_dealer_net_lots": totals["dealer"] if complete else None,
        "rs_period": _relative_strength(week_return, market_period, band=1.0),
    }


def fetch_week_window(
    stock_id: str,
    trade_date: str | None,
    *,
    market_bars: list[tuple[str, float]] | None = None,
    http: requests.Session | None = None,
) -> dict[str, Any] | None:
    """Chart plus one 法人買賣 call per session. Network errors return ``None``."""
    client = http or _session()
    listing = resolve_listing_symbol(client, stock_id)
    if listing is None:
        return None
    symbol, bars, _quote_name = listing
    series = _slice_to_date(bars, trade_date)
    if len(series) <= PERIOD_SESSIONS:
        return None
    window = series[-PERIOD_SESSIONS:]
    flows_by_date: dict[str, dict[str, int | None]] = {}
    for day, _price in window:
        trades = fetch_trades_row(client, symbol, day)
        flows_by_date[day] = {
            "foreign": _trade_lots(trades, "foreignDiffVolK"),
            "trust": _trade_lots(trades, "investmentTrustDiffVolK"),
            "dealer": _trade_lots(trades, "dealerDiffVolK"),
        }
    market = market_bars if market_bars is not None else fetch_market_bars(client)
    return week_window(
        bars,
        market,
        {},
        trade_date=window[-1][0],
        flows_by_date=flows_by_date,
    )
