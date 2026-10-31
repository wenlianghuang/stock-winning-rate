#!/usr/bin/env python3
"""Collect concept-news items into one raw file.

Reads a theme pack only. Yahoo covers each member for the lookback window.
SearXNG covers theme queries. Both origins share one item shape. A failed
query is recorded and does not abort the file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from theme_pack import ThemeMember, ThemePack, build_theme_pack, load_theme_pack, project_root

TAIPEI = ZoneInfo("Asia/Taipei")
LOOKBACK_HOURS = 48
MAX_ITEMS_PER_QUERY = 15
SEARXNG_ENV = "SEARXNG_URL"
SKIP_SEARXNG = "未設定 SEARXNG_URL"

YahooFetch = Callable[[str], list]
SearxFetch = Callable[[str], list]


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    publisher: str
    published: datetime | None
    summary: str


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    publisher: str
    published: datetime | None
    summary: str
    origin: str
    query: str
    matched_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "publisher": self.publisher,
            "published": _published_text(self.published),
            "summary": self.summary,
            "origin": self.origin,
            "query": self.query,
            "matched_ids": list(self.matched_ids),
        }


@dataclass(frozen=True)
class FetchFailure:
    origin: str
    query: str
    error: str

    def to_dict(self) -> dict:
        return {"origin": self.origin, "query": self.query, "error": self.error}


@dataclass(frozen=True)
class ThemeNewsRaw:
    theme_id: str
    label: str
    as_of: str
    collected_at: str
    lookback_hours: int
    items: tuple[NewsItem, ...]
    failures: tuple[FetchFailure, ...]

    def to_dict(self) -> dict:
        return {
            "theme_id": self.theme_id,
            "label": self.label,
            "as_of": self.as_of,
            "collected_at": self.collected_at,
            "lookback_hours": self.lookback_hours,
            "items": [item.to_dict() for item in self.items],
            "failures": [failure.to_dict() for failure in self.failures],
        }


def default_raw_path(theme_id: str, as_of: str, root: Path | None = None) -> Path:
    base = root if root is not None else project_root() / "reports" / "concept-news"
    return base / theme_id / f"{as_of}_raw.json"


def match_member_ids(text: str, members: tuple[ThemeMember, ...]) -> tuple[str, ...]:
    """Preliminary name hits. Stock ids in the text are not treated as names."""
    found: list[str] = []
    for member in members:
        if member.name and member.name in text and member.id not in found:
            found.append(member.id)
    return tuple(found)


def publisher_from_url(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host


def parse_published(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = _try_parse_datetime(text)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    return parsed.astimezone(TAIPEI)


def read_searxng_results(payload: object) -> list[Article]:
    if not isinstance(payload, dict):
        raise ValueError("SearXNG 回應不是物件")
    error = payload.get("error")
    if error:
        raise ValueError(str(error))
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError("SearXNG results 不是陣列")

    articles: list[Article] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title:
            continue
        publisher = str(row.get("publisher") or "").strip() or publisher_from_url(url)
        summary = str(row.get("content") or row.get("summary") or "").strip()
        articles.append(
            Article(
                title=title,
                url=url,
                publisher=publisher,
                published=parse_published(row.get("publishedDate")),
                summary=summary,
            )
        )
    if articles:
        return articles
    dead = payload.get("unresponsive_engines")
    if isinstance(dead, list) and dead:
        raise ValueError("SearXNG 引擎無回應")
    return []


class SearxngNewsClient:
    """News-category search. language=zh-TW, time_range=day."""

    def __init__(self, base_url: str, session: object | None = None) -> None:
        import requests

        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")

    def fetch_articles(self, query: str) -> list[Article]:
        response = self.session.get(
            f"{self.base_url}/search",
            params={
                "q": query,
                "format": "json",
                "categories": "news",
                "language": "zh-TW",
                "time_range": "day",
            },
            timeout=30,
        )
        response.raise_for_status()
        return read_searxng_results(response.json())


def collect_theme_news(
    pack: ThemePack,
    *,
    yahoo_fetch: YahooFetch | None,
    searx_fetch: SearxFetch | None,
    now: datetime | None = None,
    as_of: str | None = None,
    lookback_hours: int = LOOKBACK_HOURS,
    max_items: int = MAX_ITEMS_PER_QUERY,
    searx_skip_reason: str | None = None,
) -> ThemeNewsRaw:
    moment = _aware_now(now)
    day = as_of or moment.date().isoformat()
    _validate_day(day)
    if pack.members and yahoo_fetch is None:
        raise ValueError("缺少 Yahoo 擷取函式")

    items: list[NewsItem] = []
    failures: list[FetchFailure] = []

    for member in pack.members:
        assert yahoo_fetch is not None
        try:
            fetched = yahoo_fetch(member.id)
        except Exception as exc:  # noqa: BLE001 — one ticker must not abort the file
            failures.append(FetchFailure("yahoo", member.id, _error_text(exc)))
            continue
        selected = _select_articles(
            fetched,
            now=moment,
            lookback_hours=lookback_hours,
            max_items=max_items,
            newest_first=True,
        )
        for article in selected:
            item = _to_item(
                article,
                origin="yahoo",
                query=member.id,
                members=pack.members,
                force_id=member.id,
            )
            if item is not None:
                items.append(item)

    if searx_fetch is None:
        reason = searx_skip_reason or SKIP_SEARXNG
        for query in pack.queries:
            failures.append(FetchFailure("searxng", query, reason))
    else:
        for query in pack.queries:
            try:
                fetched = searx_fetch(query)
            except Exception as exc:  # noqa: BLE001
                failures.append(FetchFailure("searxng", query, _error_text(exc)))
                continue
            selected = _select_articles(
                fetched,
                now=moment,
                lookback_hours=lookback_hours,
                max_items=max_items,
                newest_first=False,
            )
            for article in selected:
                item = _to_item(
                    article,
                    origin="searxng",
                    query=query,
                    members=pack.members,
                    force_id=None,
                )
                if item is not None:
                    items.append(item)

    return ThemeNewsRaw(
        theme_id=pack.theme_id,
        label=pack.label,
        as_of=day,
        collected_at=moment.isoformat(timespec="seconds"),
        lookback_hours=lookback_hours,
        items=tuple(items),
        failures=tuple(failures),
    )


def write_theme_news_raw(raw: ThemeNewsRaw, path: Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(raw.to_dict(), ensure_ascii=False, indent=2) + "\n"
    destination.write_text(payload, encoding="utf-8")
    return destination


def live_yahoo_fetch(stock_id: str) -> list:
    skill = project_root() / ".agents" / "skills" / "tw-stock-report"
    text = str(skill)
    if text not in sys.path:
        sys.path.insert(0, text)
    from stock_news import YahooStockNewsClient

    client = getattr(live_yahoo_fetch, "_client", None)
    if client is None:
        client = YahooStockNewsClient()
        setattr(live_yahoo_fetch, "_client", client)
    return client.fetch_articles(stock_id)


def resolve_searxng_url(explicit: str | None, *, skip: bool) -> str | None:
    if skip:
        return None
    if explicit is not None and explicit.strip():
        return explicit.strip()
    env = os.environ.get(SEARXNG_ENV, "").strip()
    return env or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="蒐集概念題材新聞 raw")
    parser.add_argument("--theme", default="", help="theme id；未給 --pack 時用來產生查詢包")
    parser.add_argument("--pack", type=Path, default=None, help="已寫好的 theme_pack.json")
    parser.add_argument("--date", default="", help="檔名日期 YYYY-MM-DD，預設台北今天")
    parser.add_argument("--hours", type=int, default=LOOKBACK_HOURS)
    parser.add_argument("--searxng-url", default="", help="覆寫 SEARXNG_URL")
    parser.add_argument("--skip-searxng", action="store_true", help="只交 Yahoo，題材查詢記為略過")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true", help="只印 JSON，不寫檔")
    args = parser.parse_args(argv)

    pack = _pack_from_args(args.theme, args.pack)
    as_of = args.date.strip() or None
    if as_of:
        _validate_day(as_of)
    searx_url = resolve_searxng_url(args.searxng_url, skip=args.skip_searxng)
    searx_fetch = SearxngNewsClient(searx_url).fetch_articles if searx_url else None
    skip_reason = "已略過 SearXNG" if args.skip_searxng else None
    raw = collect_theme_news(
        pack,
        yahoo_fetch=live_yahoo_fetch,
        searx_fetch=searx_fetch,
        as_of=as_of,
        lookback_hours=args.hours,
        searx_skip_reason=skip_reason,
    )
    if args.stdout:
        sys.stdout.write(json.dumps(raw.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        destination = args.out or default_raw_path(raw.theme_id, raw.as_of)
        written = write_theme_news_raw(raw, destination)
        print(written)
    print(
        f"items={len(raw.items)} failures={len(raw.failures)}",
        file=sys.stderr,
    )
    return 0


def _pack_from_args(theme: str, pack_path: Path | None) -> ThemePack:
    if pack_path is not None:
        return load_theme_pack(pack_path)
    key = theme.strip()
    if not key:
        raise SystemExit("請給 --theme 或 --pack")
    return build_theme_pack(key)


def _select_articles(
    fetched: list,
    *,
    now: datetime,
    lookback_hours: int,
    max_items: int,
    newest_first: bool,
) -> list[Article]:
    articles = [_read_article(item) for item in fetched]
    kept = [
        article
        for article in articles
        if article.title and _within_lookback(article.published, now=now, lookback_hours=lookback_hours)
    ]
    if newest_first:
        kept.sort(key=lambda article: article.published or datetime.min.replace(tzinfo=TAIPEI), reverse=True)
    return kept[:max_items]


def _to_item(
    article: Article,
    *,
    origin: str,
    query: str,
    members: tuple[ThemeMember, ...],
    force_id: str | None,
) -> NewsItem | None:
    if not article.title:
        return None
    matched = list(match_member_ids(f"{article.title}\n{article.summary}", members))
    if force_id and force_id not in matched:
        matched.append(force_id)
        matched.sort()
    return NewsItem(
        title=article.title,
        url=article.url,
        publisher=article.publisher or publisher_from_url(article.url),
        published=article.published,
        summary=article.summary,
        origin=origin,
        query=query,
        matched_ids=tuple(matched),
    )


def _read_article(raw: object) -> Article:
    if isinstance(raw, Article):
        return raw
    published = getattr(raw, "published", None)
    if published is not None and not isinstance(published, datetime):
        published = None
    return Article(
        title=str(getattr(raw, "title", "") or "").strip(),
        url=str(getattr(raw, "url", "") or "").strip(),
        publisher=str(getattr(raw, "publisher", "") or "").strip(),
        published=published,
        summary=str(getattr(raw, "summary", "") or "").strip(),
    )


def _within_lookback(published: datetime | None, *, now: datetime, lookback_hours: int) -> bool:
    if published is None:
        return True
    moment = published if published.tzinfo is not None else published.replace(tzinfo=TAIPEI)
    cutoff = now - timedelta(hours=lookback_hours)
    return moment >= cutoff


def _published_text(published: datetime | None) -> str | None:
    if published is None:
        return None
    moment = published if published.tzinfo is not None else published.replace(tzinfo=TAIPEI)
    return moment.astimezone(TAIPEI).isoformat(timespec="seconds")


def _aware_now(now: datetime | None) -> datetime:
    moment = now or datetime.now(TAIPEI)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TAIPEI)
    return moment.astimezone(TAIPEI)


def _validate_day(text: str) -> None:
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"日期格式須為 YYYY-MM-DD：{text}") from exc


def _try_parse_datetime(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text.replace("\n", " ")[:300]


if __name__ == "__main__":
    raise SystemExit(main())
