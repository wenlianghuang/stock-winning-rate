#!/usr/bin/env python3
"""Cluster kept concept-news items into events. No network and no model.

Reads a kept file only. Exact copies are already one row; this layer groups
rewrites of the same story. A later row joins one event only when the named
stocks match, it is within 18 hours of that event's earliest copy, and the
title is close to every copy already in the event. Anything uncertain stays
its own event. The event key is the normalized title of the earliest copy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from collect_news import TAIPEI, default_raw_path, parse_published
from filter_news import ALLOWED_DOMAINS, allowed_domain, kept_path_for_raw, normalize_title

CLUSTER_GAP_HOURS = 18
# 台股一般交易 09:00–13:30。13:30 起算收盤後，盤中漲跌不當收盤結果。
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(13, 30)
SESSION_AFTER_CLOSE = "after_close"
SESSION_INTRADAY = "intraday"
SESSION_PREMARKET = "premarket"
JACCARD_MIN = 0.72
MIN_FUZZY_CHARS = 8
CONTAIN_EXTRA_CHARS = 4

# 兩邊各出現相反的一詞才算衝突。只有一邊寫到那個詞，不算。
_POLARITY: tuple[tuple[str, str], ...] = (
    ("上修", "下修"),
    ("調漲", "調降"),
    ("看好", "看壞"),
    ("樂觀", "保守"),
    ("成長", "衰退"),
    ("增持", "減持"),
    ("利多", "利空"),
    ("創高", "創低"),
    ("漲停", "跌停"),
)

_NUMBER = re.compile(r"\d+|[零一二三四五六七八九十百千萬億兩]")


@dataclass(frozen=True)
class EventSource:
    title: str
    url: str
    publisher: str
    published: str
    origin: str
    summary: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "publisher": self.publisher,
            "published": self.published,
            "origin": self.origin,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class NewsEvent:
    event_id: str
    title: str
    published: str
    session: str
    sources: tuple[EventSource, ...]
    mentioned_ids: tuple[str, ...]
    theme_only: bool

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "published": self.published,
            "session": self.session,
            "sources": [source.to_dict() for source in self.sources],
            "mentioned_ids": list(self.mentioned_ids),
            "theme_only": self.theme_only,
        }


@dataclass(frozen=True)
class EventTable:
    theme_id: str
    label: str
    as_of: str
    collected_at: str
    lookback_hours: int
    events: tuple[NewsEvent, ...]

    def to_dict(self) -> dict:
        return {
            "theme_id": self.theme_id,
            "label": self.label,
            "as_of": self.as_of,
            "collected_at": self.collected_at,
            "lookback_hours": self.lookback_hours,
            "events": [event.to_dict() for event in self.events],
        }


def session_of(published: datetime | str | None) -> str:
    """Classify a timestamp on the Taipei clock. Missing times stay unknown."""
    moment: datetime | None
    if isinstance(published, datetime):
        moment = published
    elif isinstance(published, str):
        moment = parse_published(published)
    else:
        moment = None
    if moment is None:
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TAIPEI)
    clock = moment.astimezone(TAIPEI).time()
    if clock >= SESSION_CLOSE:
        return SESSION_AFTER_CLOSE
    if clock >= SESSION_OPEN:
        return SESSION_INTRADAY
    return SESSION_PREMARKET


def published_day(published: datetime | str | None) -> str:
    """Taipei calendar date of a timestamp, or empty when it cannot be read."""
    moment: datetime | None
    if isinstance(published, datetime):
        moment = published
    elif isinstance(published, str):
        moment = parse_published(published)
    else:
        moment = None
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TAIPEI)
    return moment.astimezone(TAIPEI).date().isoformat()


def _shift_day(day: str, delta: int) -> str:
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return ""
    return (parsed + timedelta(days=delta)).date().isoformat()


def split_report_events(table: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Split events into this report day's close, the previous day's close, and the rest.

    A report dated as_of only treats that calendar day's 13:30-and-after items as
    the close. The previous day's close is context for 違逆 or 順從, not evidence.
    """
    if not isinstance(table, dict) or not isinstance(table.get("events"), list):
        raise ValueError("events 格式錯誤")
    as_of = str(table.get("as_of") or "").strip()
    prior_day = _shift_day(as_of, -1) if as_of else ""
    today_close: list[dict] = []
    prior_close: list[dict] = []
    other: list[dict] = []
    for event in table["events"]:
        if not isinstance(event, dict):
            continue
        day = published_day(event.get("published"))
        session = session_of(event.get("published"))
        if as_of and day == as_of and session == SESSION_AFTER_CLOSE:
            today_close.append(event)
        elif prior_day and day == prior_day and session == SESSION_AFTER_CLOSE:
            prior_close.append(event)
        else:
            other.append(event)
    return today_close, prior_close, other


_BUY_WORDS = ("買超", "回補", "轉買")
_SELL_WORDS = ("賣超", "調節")
_UP_WORDS = ("大漲", "收高", "上漲")
_DOWN_WORDS = ("大跌", "收低", "下跌")


def _event_text(events: list[dict]) -> str:
    parts: list[str] = []
    for event in events:
        parts.append(str(event.get("title") or ""))
        sources = event.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    parts.append(str(source.get("summary") or ""))
                    parts.append(str(source.get("title") or ""))
    return "\n".join(parts)


def _side(text: str, left: tuple[str, ...], right: tuple[str, ...]) -> str | None:
    left_count = sum(text.count(word) for word in left)
    right_count = sum(text.count(word) for word in right)
    if left_count > right_count and left_count > 0:
        return "left"
    if right_count > left_count and right_count > 0:
        return "right"
    return None


def prior_relation(today_close: list[dict], prior_close: list[dict]) -> str | None:
    """Whether today's close follows or reverses the previous day's close.

    Uses buy/sell wording first, then up/down. A tie or a missing side stays unset
    so the report does not invent a comparison.
    """
    if not today_close or not prior_close:
        return None
    today_text = _event_text(today_close)
    prior_text = _event_text(prior_close)
    for left, right in ((_BUY_WORDS, _SELL_WORDS), (_UP_WORDS, _DOWN_WORDS)):
        today_side = _side(today_text, left, right)
        prior_side = _side(prior_text, left, right)
        if today_side and prior_side:
            return "順從" if today_side == prior_side else "違逆"
    return None


def annotate_sessions(table: dict) -> dict:
    """Copy an event table and stamp each event from its published time."""
    if not isinstance(table, dict) or not isinstance(table.get("events"), list):
        raise ValueError("events 格式錯誤")
    cloned = json.loads(json.dumps(table, ensure_ascii=False))
    for event in cloned["events"]:
        if isinstance(event, dict):
            event["session"] = session_of(event.get("published"))
    return cloned


def cluster_kept(payload: dict) -> EventTable:
    """Group kept rows into events. Stock ids are taken from the file as-is."""
    if not isinstance(payload, dict) or not isinstance(payload.get("kept"), list):
        raise ValueError("kept 格式錯誤")
    items = [_item_from_row(index, row) for index, row in enumerate(payload["kept"])]
    clusters: list[list[_Item]] = []
    for item in sorted(items, key=_sort_key):
        matches = [group for group in clusters if _joins(item, group)]
        # 同時像兩件時留在外面，避免用一則稿把兩件事接起來。
        if len(matches) == 1:
            matches[0].append(item)
        else:
            clusters.append([item])

    events = tuple(_event(group) for group in sorted(clusters, key=_group_key))
    lookback = payload.get("lookback_hours")
    hours = lookback if isinstance(lookback, int) and lookback > 0 else 48
    return EventTable(
        theme_id=str(payload.get("theme_id") or ""),
        label=str(payload.get("label") or ""),
        as_of=str(payload.get("as_of") or ""),
        collected_at=str(payload.get("collected_at") or ""),
        lookback_hours=hours,
        events=events,
    )


def write_events(table: EventTable, path: Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(table.to_dict(), ensure_ascii=False, indent=2) + "\n"
    destination.write_text(payload, encoding="utf-8")
    return destination


def events_path_for_kept(kept_path: Path) -> Path:
    name = kept_path.name
    if name.endswith("_kept.json"):
        return kept_path.with_name(name[: -len("_kept.json")] + "_events.json")
    return kept_path.with_name(kept_path.stem + "_events.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把篩選後的概念題材新聞聚成事件表")
    parser.add_argument("--kept", type=Path, default=None)
    parser.add_argument("--theme", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    kept_path = _resolve_kept_path(args.kept, args.theme, args.date)
    payload = json.loads(kept_path.read_text(encoding="utf-8"))
    table = cluster_kept(payload)
    if args.stdout:
        sys.stdout.write(json.dumps(table.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        destination = args.out or events_path_for_kept(kept_path)
        written = write_events(table, destination)
        print(written)
    print(f"events={len(table.events)}", file=sys.stderr)
    return 0


@dataclass(frozen=True)
class _Item:
    index: int
    title: str
    url: str
    publisher: str
    published: datetime
    published_text: str
    summary: str
    origin: str
    mentioned_ids: tuple[str, ...]


def _resolve_kept_path(kept: Path | None, theme: str, day: str) -> Path:
    if kept is not None:
        return kept
    key = theme.strip()
    date = day.strip()
    if not key or not date:
        raise SystemExit("請給 --kept，或同時給 --theme 與 --date")
    return kept_path_for_raw(default_raw_path(key, date))


def _item_from_row(index: int, row: object) -> _Item:
    if not isinstance(row, dict):
        raise ValueError("kept 項目格式錯誤")
    title = str(row.get("title") or "").strip()
    if not title:
        raise ValueError("kept 項目缺少標題")
    published_text = row.get("published")
    published = parse_published(published_text)
    if not isinstance(published_text, str) or published is None:
        raise ValueError("kept 項目缺少時間")
    return _Item(
        index=index,
        title=title,
        url=str(row.get("url") or "").strip(),
        publisher=str(row.get("publisher") or "").strip(),
        published=published,
        published_text=published_text.strip(),
        summary=str(row.get("summary") or "").strip(),
        origin=str(row.get("origin") or "").strip(),
        mentioned_ids=_mentioned_ids(row.get("mentioned_ids")),
    )


def _mentioned_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    found: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in found:
            found.append(text)
    return tuple(sorted(found))


def _joins(item: _Item, group: list[_Item]) -> bool:
    if any(item.mentioned_ids != other.mentioned_ids for other in group):
        return False
    earliest = min(group, key=_sort_key)
    if abs(item.published - earliest.published) > timedelta(hours=CLUSTER_GAP_HOURS):
        return False
    return all(_same_story(item.title, other.title) for other in group)


def _same_story(left: str, right: str) -> bool:
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if _number_signature(a) != _number_signature(b):
        return False
    if a.count("不") != b.count("不") or a.count("沒") != b.count("沒"):
        return False
    if _polarity_conflict(a, b):
        return False
    if len(a) < MIN_FUZZY_CHARS or len(b) < MIN_FUZZY_CHARS:
        return False
    if _jaccard(a, b) >= JACCARD_MIN:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return shorter in longer and len(longer) - len(shorter) <= CONTAIN_EXTRA_CHARS


def _number_signature(text: str) -> tuple[str, ...]:
    return tuple(sorted(_NUMBER.findall(text)))


def _polarity_conflict(left: str, right: str) -> bool:
    for low, high in _POLARITY:
        if (low in left and high in right) or (high in left and low in right):
            return True
    return False


def _jaccard(left: str, right: str) -> float:
    a = _bigrams(left)
    b = _bigrams(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _event(group: list[_Item]) -> NewsEvent:
    ordered = sorted(group, key=_sort_key)
    head = ordered[0]
    return NewsEvent(
        event_id=normalize_title(head.title),
        title=head.title,
        published=head.published_text,
        session=session_of(head.published),
        sources=tuple(_source(item) for item in ordered),
        mentioned_ids=head.mentioned_ids,
        theme_only=not head.mentioned_ids,
    )


def _source(item: _Item) -> EventSource:
    return EventSource(
        title=item.title,
        url=item.url,
        publisher=item.publisher,
        published=item.published_text,
        origin=item.origin,
        summary=item.summary,
    )


def _group_key(group: list[_Item]) -> tuple[float, str]:
    head = min(group, key=_sort_key)
    return (head.published.timestamp(), normalize_title(head.title))


def _sort_key(item: _Item) -> tuple[float, int, int]:
    return (item.published.timestamp(), _source_rank(item.url), item.index)


def _source_rank(url: str) -> int:
    domain = allowed_domain(url)
    if domain is None:
        return len(ALLOWED_DOMAINS)
    return ALLOWED_DOMAINS.index(domain)


if __name__ == "__main__":
    raise SystemExit(main())
