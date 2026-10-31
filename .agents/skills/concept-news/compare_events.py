#!/usr/bin/env python3
"""Compare a concept-news event table with the previous one. No model.

The event key is event_id. The same key is continued, a key only in the new
file is added, and a key only in the previous file has dropped off. If that
previous file is missing, the caller skips this step.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from filter_news import normalize_title

DIFF_HEADING = "和前一份的差異"
ADDED = "新增"
CONTINUED = "沿用"
DROPPED = "未再出現"
_STATUSES = (ADDED, CONTINUED, DROPPED)
_DATED_EVENTS = re.compile(r"^(\d{4}-\d{2}-\d{2})_events\.json$")
_DIFF_HEADING = re.compile(rf"(?m)^##\s+{re.escape(DIFF_HEADING)}\s*$")
_NEXT_HEADING = re.compile(r"(?m)^##\s+")


@dataclass(frozen=True)
class EventChange:
    key: str
    title: str
    status: str


@dataclass(frozen=True)
class EventDiff:
    changes: tuple[EventChange, ...]

    def titles(self, status: str) -> tuple[str, ...]:
        return tuple(change.title for change in self.changes if change.status == status)


def event_key(event: dict) -> str:
    key = str(event.get("event_id") or "").strip()
    if key:
        return key
    return normalize_title(str(event.get("title") or ""))


def compare_event_tables(current: dict, previous: dict) -> EventDiff:
    current_keys = _indexed(current)
    previous_keys = _indexed(previous)
    changes: list[EventChange] = []
    for key, title in current_keys.items():
        if key not in previous_keys:
            changes.append(EventChange(key, title, ADDED))
    for key, title in current_keys.items():
        if key in previous_keys:
            changes.append(EventChange(key, title, CONTINUED))
    for key, title in previous_keys.items():
        if key not in current_keys:
            changes.append(EventChange(key, title, DROPPED))
    return EventDiff(tuple(changes))


def render_diff_section(diff: EventDiff) -> str:
    lines = [f"## {DIFF_HEADING}", ""]
    for status in _STATUSES:
        titles = diff.titles(status)
        if not titles:
            lines.append(f"- {status}：無")
        else:
            lines.extend(f"- {status}：{title}" for title in titles)
    return "\n".join(lines)


def strip_diff_section(body: str) -> str:
    match = _DIFF_HEADING.search(body)
    if match is None:
        return body.strip()
    nxt = _NEXT_HEADING.search(body, match.end())
    end = nxt.start() if nxt is not None else len(body)
    return (body[: match.start()] + body[end:]).strip()


def append_diff_section(body: str, diff: EventDiff) -> str:
    text = strip_diff_section(body)
    section = render_diff_section(diff)
    if not text:
        return section + "\n"
    return f"{text}\n\n{section}\n"


def previous_events_path(events_path: Path, date_str: str) -> Path | None:
    folder = events_path.parent
    if not folder.is_dir():
        return None
    current = events_path.resolve()
    earlier: list[str] = []
    for path in folder.glob("*_events.json"):
        match = _DATED_EVENTS.match(path.name)
        if match is None:
            continue
        day = match.group(1)
        if day < date_str and path.resolve() != current:
            earlier.append(day)
    if not earlier:
        return None
    return folder / f"{max(earlier)}_events.json"


def load_previous_events(events_path: Path, date_str: str, theme_id: str = "") -> dict | None:
    """Return the previous table, or None when this step should be skipped."""
    path = previous_events_path(events_path, date_str)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"略過無法讀取的前一份事件表 {path}：{exc}", file=sys.stderr)
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        print(f"略過格式錯誤的前一份事件表 {path}", file=sys.stderr)
        return None
    previous_theme = str(payload.get("theme_id") or "").strip()
    current_theme = theme_id.strip()
    if current_theme and previous_theme and previous_theme != current_theme:
        print(f"略過題材不同的前一份事件表 {path}", file=sys.stderr)
        return None
    return payload


def _indexed(table: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    events = table.get("events") if isinstance(table, dict) else None
    if not isinstance(events, list):
        return found
    for event in events:
        if not isinstance(event, dict):
            continue
        key = event_key(event)
        if not key or key in found:
            continue
        title = str(event.get("title") or "").strip() or key
        found[key] = title
    return found
