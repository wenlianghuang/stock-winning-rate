#!/usr/bin/env python3
"""Filter and dedupe a concept-news raw file. No network and no model.

Rules, in order: lookback window, domain allowlist, same URL or normalized
title collapse to the earlier origin, then pool names only. A story that
names nobody is kept as theme_only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from collect_news import default_raw_path, match_member_ids, parse_published
from theme_pack import ThemePack, build_theme_pack, load_theme_pack

# 公開資訊觀測站、中央社、經濟日報、工商時報、鉅亨、MoneyDJ、Yahoo。
# 順序同時是時間相同時的原發優先序：越前面越優先。
ALLOWED_DOMAINS: tuple[str, ...] = (
    "mops.twse.com.tw",
    "mopsov.twse.com.tw",
    "cna.com.tw",
    "money.udn.com",
    "ctee.com.tw",
    "cnyes.com",
    "moneydj.com",
    "tw.stock.yahoo.com",
    "tw.news.yahoo.com",
    "finance.yahoo.com",
)

DROP_NO_TITLE = "沒有標題"
DROP_NO_TIME = "沒有時間"
DROP_OUTSIDE = "時間超出回溯窗"
DROP_NO_URL = "沒有網址"
DROP_DOMAIN = "網域不在允許名單"
DROP_DUP = "與較早來源重複"
KEEP_NAMED = "時間內、允許網域、對上股名"
KEEP_THEME = "時間內、允許網域、題材稿未點名"
KEEP_EARLIER_SUFFIX = "、保留較早來源"


@dataclass(frozen=True)
class _Row:
    index: int
    title: str
    url: str
    publisher: str
    published: datetime | None
    published_text: str | None
    summary: str
    origin: str
    query: str


@dataclass(frozen=True)
class KeptItem:
    title: str
    url: str
    publisher: str
    published: str | None
    summary: str
    origin: str
    query: str
    mentioned_ids: tuple[str, ...]
    theme_only: bool
    keep_reason: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "publisher": self.publisher,
            "published": self.published,
            "summary": self.summary,
            "origin": self.origin,
            "query": self.query,
            "mentioned_ids": list(self.mentioned_ids),
            "theme_only": self.theme_only,
            "keep_reason": self.keep_reason,
        }


@dataclass(frozen=True)
class DroppedItem:
    title: str
    url: str
    publisher: str
    published: str | None
    origin: str
    query: str
    drop_reason: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "publisher": self.publisher,
            "published": self.published,
            "origin": self.origin,
            "query": self.query,
            "drop_reason": self.drop_reason,
        }


@dataclass(frozen=True)
class KeptNews:
    theme_id: str
    label: str
    as_of: str
    collected_at: str
    lookback_hours: int
    kept: tuple[KeptItem, ...]
    dropped: tuple[DroppedItem, ...]

    def to_dict(self) -> dict:
        return {
            "theme_id": self.theme_id,
            "label": self.label,
            "as_of": self.as_of,
            "collected_at": self.collected_at,
            "lookback_hours": self.lookback_hours,
            "kept": [item.to_dict() for item in self.kept],
            "dropped": [item.to_dict() for item in self.dropped],
        }


def normalize_title(title: str) -> str:
    text = re.sub(r"\s+", "", title.strip().lower())
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("", host, path, parts.query, ""))


def allowed_domain(url: str) -> str | None:
    host = (urlsplit(url.strip()).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    for domain in ALLOWED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain
    return None


def filter_raw(payload: dict, pack: ThemePack) -> KeptNews:
    """Apply the keep/drop rules. Member names come from the theme pack."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("raw 格式錯誤")
    theme_id = str(payload.get("theme_id") or "").strip()
    if theme_id != pack.theme_id:
        raise ValueError(f"查詢包與 raw 的題材不一致：{pack.theme_id} / {theme_id}")
    collected_at = parse_published(payload.get("collected_at"))
    if collected_at is None:
        raise ValueError("raw 缺少 collected_at")
    lookback = payload.get("lookback_hours")
    hours = lookback if isinstance(lookback, int) and lookback > 0 else 48
    cutoff = collected_at - timedelta(hours=hours)

    dropped: list[DroppedItem] = []
    survivors: list[_Row] = []
    for index, item in enumerate(payload["items"]):
        if not isinstance(item, dict):
            dropped.append(_dropped("", "", "", None, "", "", DROP_NO_TITLE))
            continue
        row = _row_from_item(index, item)
        reason = _reject_reason(row, collected_at=collected_at, cutoff=cutoff)
        if reason is not None:
            dropped.append(_dropped_row(row, reason))
            continue
        survivors.append(row)

    groups = _clusters(survivors)
    winners: list[tuple[_Row, int]] = []
    for group in groups:
        group.sort(key=lambda row: (row.published or collected_at, _source_rank(row.url), row.index))
        winner = group[0]
        winners.append((winner, len(group)))
        for loser in group[1:]:
            dropped.append(_dropped_row(loser, DROP_DUP))

    kept = tuple(
        _kept_item(row, pack, absorbed=size > 1)
        for row, size in sorted(winners, key=lambda pair: (pair[0].published or collected_at, pair[0].index))
    )
    return KeptNews(
        theme_id=theme_id,
        label=str(payload.get("label") or pack.label),
        as_of=str(payload.get("as_of") or collected_at.date().isoformat()),
        collected_at=str(payload.get("collected_at")),
        lookback_hours=hours,
        kept=kept,
        dropped=tuple(dropped),
    )


def write_kept_news(news: KeptNews, path: Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(news.to_dict(), ensure_ascii=False, indent=2) + "\n"
    destination.write_text(payload, encoding="utf-8")
    return destination


def kept_path_for_raw(raw_path: Path) -> Path:
    name = raw_path.name
    if name.endswith("_raw.json"):
        return raw_path.with_name(name[: -len("_raw.json")] + "_kept.json")
    return raw_path.with_name(raw_path.stem + "_kept.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="篩選並去重概念題材新聞 raw")
    parser.add_argument("--raw", type=Path, default=None)
    parser.add_argument("--pack", type=Path, default=None)
    parser.add_argument("--theme", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    raw_path = _resolve_raw_path(args.raw, args.theme, args.date)
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    pack = _resolve_pack(args.pack, raw_path, str(payload.get("theme_id") or args.theme))
    news = filter_raw(payload, pack)
    if args.stdout:
        sys.stdout.write(json.dumps(news.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        destination = args.out or kept_path_for_raw(raw_path)
        written = write_kept_news(news, destination)
        print(written)
    print(f"kept={len(news.kept)} dropped={len(news.dropped)}", file=sys.stderr)
    return 0


def _resolve_raw_path(raw: Path | None, theme: str, day: str) -> Path:
    if raw is not None:
        return raw
    key = theme.strip()
    date = day.strip()
    if not key or not date:
        raise SystemExit("請給 --raw，或同時給 --theme 與 --date")
    return default_raw_path(key, date)


def _resolve_pack(pack_path: Path | None, raw_path: Path, theme_id: str) -> ThemePack:
    if pack_path is not None:
        return load_theme_pack(pack_path)
    sibling = raw_path.parent / "theme_pack.json"
    if sibling.is_file():
        return load_theme_pack(sibling)
    key = theme_id.strip()
    if not key:
        raise SystemExit("raw 沒有 theme_id，請另給 --pack")
    return build_theme_pack(key)


def _row_from_item(index: int, item: dict) -> _Row:
    published = parse_published(item.get("published"))
    return _Row(
        index=index,
        title=str(item.get("title") or "").strip(),
        url=str(item.get("url") or "").strip(),
        publisher=str(item.get("publisher") or "").strip(),
        published=published,
        published_text=published.isoformat(timespec="seconds") if published is not None else None,
        summary=str(item.get("summary") or "").strip(),
        origin=str(item.get("origin") or "").strip(),
        query=str(item.get("query") or "").strip(),
    )


def _reject_reason(row: _Row, *, collected_at: datetime, cutoff: datetime) -> str | None:
    if not row.title or not normalize_title(row.title):
        return DROP_NO_TITLE
    if row.published is None:
        return DROP_NO_TIME
    if row.published < cutoff or row.published > collected_at:
        return DROP_OUTSIDE
    if not row.url:
        return DROP_NO_URL
    if allowed_domain(row.url) is None:
        return DROP_DOMAIN
    return None


def _clusters(rows: list[_Row]) -> list[list[_Row]]:
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    by_url: dict[str, int] = {}
    by_title: dict[str, int] = {}
    for index, row in enumerate(rows):
        url_key = normalize_url(row.url)
        title_key = normalize_title(row.title)
        if url_key:
            previous = by_url.get(url_key)
            if previous is None:
                by_url[url_key] = index
            else:
                union(index, previous)
        if title_key:
            previous = by_title.get(title_key)
            if previous is None:
                by_title[title_key] = index
            else:
                union(index, previous)

    groups: dict[int, list[_Row]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(find(index), []).append(row)
    return list(groups.values())


def _source_rank(url: str) -> int:
    domain = allowed_domain(url)
    if domain is None:
        return len(ALLOWED_DOMAINS)
    return ALLOWED_DOMAINS.index(domain)


def _kept_item(row: _Row, pack: ThemePack, *, absorbed: bool) -> KeptItem:
    mentioned = tuple(sorted(match_member_ids(f"{row.title}\n{row.summary}", pack.members)))
    theme_only = not mentioned
    reason = KEEP_THEME if theme_only else KEEP_NAMED
    if absorbed:
        reason += KEEP_EARLIER_SUFFIX
    return KeptItem(
        title=row.title,
        url=row.url,
        publisher=row.publisher,
        published=row.published_text,
        summary=row.summary,
        origin=row.origin,
        query=row.query,
        mentioned_ids=mentioned,
        theme_only=theme_only,
        keep_reason=reason,
    )


def _dropped_row(row: _Row, reason: str) -> DroppedItem:
    return _dropped(row.title, row.url, row.publisher, row.published_text, row.origin, row.query, reason)


def _dropped(
    title: str,
    url: str,
    publisher: str,
    published: str | None,
    origin: str,
    query: str,
    reason: str,
) -> DroppedItem:
    return DroppedItem(
        title=title,
        url=url,
        publisher=publisher,
        published=published,
        origin=origin,
        query=query,
        drop_reason=reason,
    )


if __name__ == "__main__":
    raise SystemExit(main())
