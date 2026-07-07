#!/usr/bin/env python3
"""Fetch Taiwan stock news from Yahoo Finance TW (SSR stream_items)."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

YAHOO_NEWS_URL = "https://tw.stock.yahoo.com/quote/{symbol}.TW/news"
YAHOO_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass
class StockNewsItem:
    title: str
    summary: str
    url: str
    publisher: str
    published: datetime | None


def _extract_json_array(html: str, marker: str) -> list[dict]:
    idx = html.find(marker)
    if idx < 0:
        raise ValueError(f"找不到 {marker!r}")

    start = idx + len(marker)
    if start >= len(html) or html[start] != "[":
        raise ValueError(f"{marker!r} 後方不是 JSON 陣列")

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
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break

    if depth != 0:
        raise ValueError(f"無法解析 {marker!r} 的 JSON 陣列")

    raw = re.sub(r"\bundefined\b", "null", html[start:end])
    return json.loads(raw)


def parse_yahoo_stock_news(html: str) -> list[StockNewsItem]:
    items = _extract_json_array(html, '"stream_items":')
    articles: list[StockNewsItem] = []
    for item in items:
        if item.get("type") != "article":
            continue
        pubtime = item.get("pubtime")
        published = None
        if isinstance(pubtime, (int, float)) and pubtime > 0:
            published = datetime.fromtimestamp(pubtime / 1000, tz=TAIPEI)
        articles.append(
            StockNewsItem(
                title=str(item.get("title") or "").strip(),
                summary=str(item.get("summary") or "").strip(),
                url=str(item.get("url") or item.get("link") or "").strip(),
                publisher=str(item.get("publisher") or "").strip(),
                published=published,
            )
        )
    return articles


class YahooStockNewsClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = YAHOO_USER_AGENT

    def fetch_articles(self, stock_id: str) -> list[StockNewsItem]:
        response = self.session.get(
            YAHOO_NEWS_URL.format(symbol=stock_id),
            timeout=60,
        )
        response.raise_for_status()
        return parse_yahoo_stock_news(response.text)


def filter_recent_articles(
    articles: list[StockNewsItem],
    *,
    lookback_hours: int = 72,
    max_items: int = 12,
) -> list[StockNewsItem]:
    cutoff = datetime.now(TAIPEI) - timedelta(hours=lookback_hours)
    recent = [
        article
        for article in articles
        if article.published is None or article.published >= cutoff
    ]
    if not recent:
        recent = articles
    recent.sort(
        key=lambda a: a.published or datetime.min.replace(tzinfo=TAIPEI),
        reverse=True,
    )
    return recent[:max_items]


def format_news_file(
    stock_id: str,
    stock_name: str,
    trade_date: str,
    articles: list[StockNewsItem],
) -> str:
    lines = [
        f"股票：{stock_name}（{stock_id}）",
        f"籌碼資料日期：{trade_date}",
        f"新聞擷取時間：{datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')}",
        f"收錄則數：{len(articles)}",
        "",
        "---",
        "",
    ]
    for index, article in enumerate(articles, start=1):
        when = (
            article.published.strftime("%Y-%m-%d %H:%M")
            if article.published
            else "未知時間"
        )
        lines.append(f"[{index}] {when} | {article.publisher or '未知來源'}")
        lines.append(f"標題：{article.title}")
        if article.summary:
            lines.append(f"摘要：{article.summary}")
        if article.url:
            lines.append(f"連結：{article.url}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def fetch_news_text(
    stock_id: str,
    stock_name: str,
    trade_date: str,
    *,
    lookback_hours: int = 72,
) -> str | None:
    try:
        client = YahooStockNewsClient()
        articles = filter_recent_articles(
            client.fetch_articles(stock_id),
            lookback_hours=lookback_hours,
        )
    except Exception as exc:
        print(f"WARN: {stock_id} 新聞擷取失敗：{exc}", file=sys.stderr)
        return None

    if not articles:
        print(f"WARN: {stock_id} 無近期新聞", file=sys.stderr)
        return None

    return format_news_file(stock_id, stock_name, trade_date, articles)
