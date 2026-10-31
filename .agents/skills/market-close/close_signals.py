"""Close-report facts: TAIEX, market-wide 三大法人, weighted-stock 法人, same-day headlines."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from close_window import CloseWindow

TAIPEI = ZoneInfo("Asia/Taipei")

# Price weights live in the weekly report. ETFs are omitted: their 法人 series
# is not the same as a listed company's.
CLOSE_LEADERS: tuple[tuple[str, str], ...] = (
    ("2330", "台積電"),
    ("2317", "鴻海"),
    ("2454", "聯發科"),
    ("2308", "台達電"),
    ("2382", "廣達"),
    ("2881", "富邦金"),
    ("2882", "國泰金"),
)

FACTS_NAME = "tw_market_close.facts.json"
MAX_NEWS_PER_NAME = 2
MAX_NEWS_ITEMS = 12


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_paths() -> None:
    root = project_root()
    for sub in (
        ".agents/skills/market-daily",
        ".agents/skills/tw-stock-report",
        ".agents/skills/market-close",
    ):
        text = str(root / sub)
        if text not in sys.path:
            sys.path.insert(0, text)


def market_output_dir(trade_date: str) -> Path:
    return project_root() / "reports" / "market" / trade_date


def facts_path(trade_date: str) -> Path:
    return market_output_dir(trade_date) / FACTS_NAME


def leader_fact(
    stock_id: str,
    name: str,
    *,
    close: float | None = None,
    day_return_pct: float | None = None,
    foreign_net_lots: int | None = None,
    trust_net_lots: int | None = None,
    dealer_net_lots: int | None = None,
) -> dict[str, Any]:
    lots = (foreign_net_lots, trust_net_lots, dealer_net_lots)
    published = any(value is not None for value in lots)
    return {
        "stock_id": stock_id,
        "name": name,
        "close": close,
        "day_return_pct": day_return_pct,
        "price_status": "published" if close is not None else "unpublished",
        "foreign_net_lots": foreign_net_lots,
        "trust_net_lots": trust_net_lots,
        "dealer_net_lots": dealer_net_lots,
        "institutional_status": "published" if published else "unpublished",
        "unit": "lots",
    }


def assemble_close_facts(
    window: CloseWindow,
    *,
    taiex_rows: dict[str, dict[str, Any]],
    institutional_rows: list[dict[str, Any]],
    leaders: list[dict[str, Any]],
    news_items: list[dict[str, Any]],
) -> dict[str, Any]:
    ensure_paths()
    from market_day_signals import build_institutional_block, build_market_block, build_volume_block

    market = build_market_block(window.trade_date, window.prior_trade_date, taiex_rows)
    market["available"] = market.get("close") is not None
    volume = build_volume_block(window.trade_date, window.lookback_days, taiex_rows)
    volume["available"] = volume.get("volume") is not None
    institutional = build_institutional_block(window.trade_date, institutional_rows)
    titles = [str(item["title"]) for item in news_items if str(item.get("title") or "").strip()]
    return {
        "kind": "tw_market_close",
        "trade_date": window.trade_date,
        "prior_trade_date": window.prior_trade_date,
        "lookback_days": list(window.lookback_days),
        "resolved_as_of": window.resolved_as_of,
        "cutover_applied": window.cutover_applied,
        "cutover": "17:30",
        "market": market,
        "volume": volume,
        "institutional": institutional,
        "leaders": leaders,
        "news_items": news_items,
        "news_titles": titles,
    }


def _leader_from_yahoo(http: Any, stock_id: str, name: str, trade_date: str) -> dict[str, Any]:
    from yahoo_board import (
        _pct,
        _row_day,
        _rows_by_date,
        _slice_to_date,
        _trade_lots,
        fetch_trade_history,
        fetch_trades_row,
        resolve_listing_symbol,
    )

    listing = resolve_listing_symbol(http, stock_id)
    if listing is None:
        return leader_fact(stock_id, name)
    symbol, bars, _quote_name = listing
    series = _slice_to_date(bars, trade_date)
    close = None
    day_return = None
    if series and series[-1][0] == trade_date:
        close = round(float(series[-1][1]), 2)
        if len(series) >= 2:
            day_return = _pct(close, series[-2][1])
    history = fetch_trade_history(http, symbol) or []
    row = _rows_by_date(history).get(trade_date)
    if row is None:
        row = fetch_trades_row(http, symbol, trade_date)
        if row is not None:
            day = _row_day(row)
            if day and day != trade_date:
                row = None
    return leader_fact(
        stock_id,
        name,
        close=close,
        day_return_pct=day_return,
        foreign_net_lots=_trade_lots(row, "foreignDiffVolK"),
        trust_net_lots=_trade_lots(row, "investmentTrustDiffVolK"),
        dealer_net_lots=_trade_lots(row, "dealerDiffVolK"),
    )


def fetch_leaders(trade_date: str, *, http: Any = None) -> list[dict[str, Any]]:
    ensure_paths()
    from yahoo_board import _session

    client = http or _session()
    leaders: list[dict[str, Any]] = []
    for stock_id, name in CLOSE_LEADERS:
        try:
            leaders.append(_leader_from_yahoo(client, stock_id, name, trade_date))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: leader {stock_id} failed: {exc}", file=sys.stderr)
            leaders.append(leader_fact(stock_id, name))
    return leaders


def _same_day(published: datetime | None, trade_date: str) -> bool:
    if published is None:
        return False
    try:
        local = published.replace(tzinfo=TAIPEI) if published.tzinfo is None else published.astimezone(TAIPEI)
        return local.date().isoformat() == trade_date
    except Exception:  # noqa: BLE001
        return False


def collect_close_news(
    trade_date: str,
    *,
    client: Any = None,
) -> list[dict[str, Any]]:
    """Same-calendar-day Yahoo headlines for the fixed leader list. Undated rows are dropped."""
    ensure_paths()
    if client is None:
        from stock_news import YahooStockNewsClient

        client = YahooStockNewsClient()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stock_id, name in CLOSE_LEADERS:
        if len(items) >= MAX_NEWS_ITEMS:
            break
        try:
            articles = client.fetch_articles(stock_id)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: news {stock_id} failed: {exc}", file=sys.stderr)
            continue
        kept = 0
        for article in articles:
            title = (getattr(article, "title", "") or "").strip()
            published = getattr(article, "published", None)
            if not title or title in seen or not _same_day(published, trade_date):
                continue
            seen.add(title)
            date_str = ""
            if published is not None:
                date_str = published.date().isoformat()
            items.append(
                {
                    "title": title,
                    "date": date_str or None,
                    "stock_id": stock_id,
                    "name": name,
                    "publisher": (getattr(article, "publisher", "") or "").strip() or None,
                }
            )
            kept += 1
            if kept >= MAX_NEWS_PER_NAME or len(items) >= MAX_NEWS_ITEMS:
                break
    return items


def build_close_facts(
    window: CloseWindow,
    *,
    skip_news: bool = False,
    finmind_token: str | None = None,
    http: Any = None,
    news_client: Any = None,
) -> dict[str, Any]:
    ensure_paths()
    from market_day_signals import fetch_institutional_rows, fetch_taiex_rows

    start = window.lookback_days[0] if window.lookback_days else window.trade_date
    end = window.trade_date
    try:
        taiex_rows = fetch_taiex_rows(start, end, token=finmind_token)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: FinMind TAIEX fetch failed: {exc}", file=sys.stderr)
        taiex_rows = {}
    try:
        inst_rows = fetch_institutional_rows(end, end, token=finmind_token)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: FinMind institutional fetch failed: {exc}", file=sys.stderr)
        inst_rows = []
    leaders = fetch_leaders(window.trade_date, http=http)
    news_items = [] if skip_news else collect_close_news(window.trade_date, client=news_client)
    return assemble_close_facts(
        window,
        taiex_rows=taiex_rows,
        institutional_rows=inst_rows,
        leaders=leaders,
        news_items=news_items,
    )


def write_facts_json(facts: dict[str, Any]) -> Path:
    path = facts_path(str(facts["trade_date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _yi(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "尚未公布"
    yi = float(value) / 1e8
    sign = "+" if yi > 0 else ""
    return f"{sign}{yi:.2f} 億元"


def _lots(value: Any) -> str:
    if value is None:
        return "尚未公布"
    if not isinstance(value, (int, float)):
        return "尚未公布"
    lots = int(value)
    sign = "+" if lots > 0 else ""
    return f"{sign}{lots} 張"


def facts_summary_for_prompt(facts: dict[str, Any]) -> str:
    market = facts.get("market") or {}
    volume = facts.get("volume") or {}
    inst = facts.get("institutional") or {}
    lines = [
        f"交易日 {facts.get('trade_date')}。這是收盤日報，不是明日開盤偏誤。",
        "全市場三大法人單位是元，下面已換算成億元。權值法人單位是張。",
        "尚未公布的欄位不可補數字，不可寫融資、融券、當沖、主力。",
        "",
        "=== 大盤 ===",
        (
            f"收盤 {market.get('close')} 日報酬 {market.get('day_return_pct')}% "
            f"開 {market.get('open')} 高 {market.get('high')} 低 {market.get('low')} "
            f"收在當日區間 {market.get('close_in_day_range_pct')}%"
            if market.get("available")
            else "收盤價尚未公布"
        ),
        (
            f"成交量 {volume.get('volume')} 相對五日均量 {volume.get('vs_avg5_ratio')} "
            f"regime {volume.get('regime')}"
            if volume.get("available")
            else "成交量尚未公布"
        ),
        "",
        "=== 全市場三大法人 ===",
        (
            f"{inst.get('consensus_label')} 外資 {_yi(inst.get('foreign_net'))} "
            f"投信 {_yi(inst.get('trust_net'))} 自營 {_yi(inst.get('dealer_net'))} "
            f"合計 {_yi(inst.get('total_net'))}"
            if inst.get("available")
            else "全市場法人尚未公布"
        ),
        "",
        "=== 權值法人（張）===",
    ]
    for item in facts.get("leaders") or []:
        if item.get("institutional_status") != "published":
            lines.append(
                f"{item.get('name')}（{item.get('stock_id')}）收盤 {item.get('close')} "
                f"日報酬 {item.get('day_return_pct')}% 法人尚未公布"
            )
            continue
        lines.append(
            f"{item.get('name')}（{item.get('stock_id')}）收盤 {item.get('close')} "
            f"日報酬 {item.get('day_return_pct')}% "
            f"外資 {_lots(item.get('foreign_net_lots'))} "
            f"投信 {_lots(item.get('trust_net_lots'))} "
            f"自營 {_lots(item.get('dealer_net_lots'))}"
        )
    lines.extend(["", "=== 當天新聞標題（引用必須與下列文字完全相同）==="])
    titles = list(facts.get("news_titles") or [])
    if not titles:
        lines.append("沒有對得上的收盤新聞")
    else:
        for title in titles:
            lines.append(f"- {title}")
    return "\n".join(lines)
