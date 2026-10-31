#!/usr/bin/env python3
"""Read concept-news files and answer from one event table.

No network and no model. A question that does not land in that file is said
to be missing. Stock names are matched only when they already appear in the
event text.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from theme_pack import load_universe, project_root

THEME_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STOCK_ID = re.compile(r"(?<!\d)(\d{4,6})(?!\d)")
FILE_KIND = re.compile(r"^(\d{4}-\d{2}-\d{2})_(events\.json|summary\.md)$")

MISS = "這份事件表裡沒有對得上的內容。"
EMPTY = "沒有通過篩選的新聞。"
PREFACE = "以下只取自這份事件表。"
NO_ADVICE = "這裡不另外下買賣結論，只複述事件表裡的句子。"

_STOP_PHRASES = (
    "誰被點名",
    "有沒有",
    "是不是",
    "要不要",
    "什麼",
    "哪些",
    "請問",
    "一下",
    "這份",
    "事件表",
    "被點名",
    "新聞",
    "寫了",
    "沒寫",
    "來源",
    "報告",
    "個股",
)
_STOP_CHARS = set("的了嗎呢和與在是有沒寫到說看請")
_NAMED_FILLER = {"點名", "個股", "股票", "池內", "哪些"}
_ADVICE_WORDS = ("買進", "賣出", "進場", "出場", "加碼", "減碼", "目標價", "要買", "要賣", "該買", "該賣")


def reports_root() -> Path:
    override = os.environ.get("CONCEPT_NEWS_ROOT", "").strip()
    if override:
        return Path(override)
    return project_root() / "reports" / "concept-news"


def list_themes() -> list[dict]:
    """Theme ids and labels from the universe file. Does not read news."""
    themes, candidates = load_universe()
    rows: list[dict] = []
    for theme_id in sorted(themes):
        meta = themes[theme_id] if isinstance(themes[theme_id], dict) else {}
        label = str(meta.get("label") or theme_id).strip() or str(theme_id)
        count = sum(1 for entry in candidates if str(theme_id) in _theme_ids_of(entry))
        rows.append(
            {
                "theme_id": str(theme_id),
                "label": label,
                "member_count": count,
            }
        )
    return rows


def list_written_reports(theme_id: str, root: Path | None = None) -> list[dict]:
    """Dates that already have an event table or a summary. Does not search."""
    folder = _theme_dir(theme_id, root)
    if not folder.is_dir():
        return []
    found: dict[str, dict] = {}
    for path in folder.iterdir():
        match = FILE_KIND.match(path.name)
        if match is None or not path.is_file():
            continue
        day, kind = match.group(1), match.group(2)
        slot = found.setdefault(
            day,
            {
                "date": day,
                "label": "",
                "has_events": False,
                "has_summary": False,
                "event_count": 0,
            },
        )
        if kind == "summary.md":
            slot["has_summary"] = True
            continue
        slot["has_events"] = True
        payload = _read_json(path)
        raw_events = payload.get("events")
        slot["event_count"] = len(raw_events) if isinstance(raw_events, list) else 0
        slot["label"] = str(payload.get("label") or "")
    return [found[day] for day in sorted(found, reverse=True)]


def load_written_report(theme_id: str, day: str, root: Path | None = None) -> dict | None:
    """Load one written summary and event table. Missing both returns None."""
    folder = _theme_dir(theme_id, root)
    if not DATE.fullmatch(day):
        raise ValueError("日期格式須為 YYYY-MM-DD")
    events_path = folder / f"{day}_events.json"
    summary_path = folder / f"{day}_summary.md"
    has_events = events_path.is_file()
    has_summary = summary_path.is_file()
    if not has_events and not has_summary:
        return None
    events = _read_json(events_path) if has_events else None
    raw_events = events.get("events") if isinstance(events, dict) else None
    label = str(events.get("label") or "") if isinstance(events, dict) else ""
    summary = summary_path.read_text(encoding="utf-8") if has_summary else None
    return {
        "theme_id": theme_id,
        "label": label,
        "date": day,
        "has_events": has_events,
        "has_summary": has_summary,
        "event_count": len(raw_events) if isinstance(raw_events, list) else 0,
        "summary": summary,
        "events": events,
    }


def answer_from_events(question: str, table: dict) -> dict:
    """Quote one event table. Unmatched questions stay unmatched."""
    if not isinstance(table, dict):
        raise ValueError("事件表格式錯誤")
    raw_events = table.get("events")
    events = [event for event in raw_events if isinstance(event, dict)] if isinstance(raw_events, list) else []
    asked = question.strip()
    if not events:
        return {"found": False, "answer": EMPTY, "event_ids": []}

    if _asks_named_all(asked, events):
        return _pack(events, _render_named(events), found=True)

    if _asks_overview(asked, events):
        return _pack(events, _render_events(events), found=True)

    mentioned = _mentioned_ids(events)
    asked_ids = _asked_stock_ids(asked, mentioned)
    terms = _content_terms(asked)
    hits = [
        event
        for event in events
        if _event_matches(event, asked_ids, terms)
    ]
    missing_ids = [stock_id for stock_id in asked_ids if not _id_appears(stock_id, events)]
    if not hits:
        note = ""
        if missing_ids:
            note = "股號 " + "、".join(missing_ids) + " 沒有出現在這份事件表。"
        text = MISS if not note else f"{MISS}{note}"
        return {"found": False, "answer": text, "event_ids": []}

    notes: list[str] = []
    if missing_ids:
        notes.append("股號 " + "、".join(missing_ids) + " 沒有出現在這份事件表。")
    missing_terms = [
        term
        for term in terms
        if len(term) >= 2 and all(term not in _blob(event) for event in hits)
    ]
    if missing_terms:
        shown = "、".join(f"「{term}」" for term in missing_terms[:6])
        notes.append(f"事件表沒有寫到{shown}。")
    if any(word in asked for word in _ADVICE_WORDS):
        notes.append(NO_ADVICE)
    return _pack(hits, _render_events(hits), found=True, notes=notes)


def _pack(events: list[dict], body: str, *, found: bool, notes: list[str] | None = None) -> dict:
    lines = [PREFACE, "", body]
    for note in notes or []:
        lines.extend(["", note])
    return {
        "found": found,
        "answer": "\n".join(lines).strip(),
        "event_ids": [str(event.get("event_id") or "") for event in events],
    }


def _asks_overview(question: str, events: list[dict]) -> bool:
    compact = re.sub(r"\s+", "", question)
    if _asked_stock_ids(question, _mentioned_ids(events)):
        return False
    extra = [term for term in _content_terms(question) if term not in {"事件", "摘要", "列出", "全部", "這份"}]
    if extra:
        return False
    keys = ("有哪些事件", "列出事件", "全部事件", "這份事件", "事件有哪些", "摘要")
    return any(key in compact for key in keys)


def _asks_named_all(question: str, events: list[dict]) -> bool:
    compact = re.sub(r"\s+", "", question)
    if "誰被點名" not in compact and "被點名" not in compact and "點名了誰" not in compact:
        return False
    if _asked_stock_ids(question, _mentioned_ids(events)):
        return False
    extra = [term for term in _content_terms(question) if term not in _NAMED_FILLER]
    return not extra


def _event_matches(event: dict, asked_ids: list[str], terms: list[str]) -> bool:
    blob = _blob(event)
    mentioned = {str(stock_id) for stock_id in event.get("mentioned_ids") or []}
    if any(stock_id in mentioned or stock_id in blob for stock_id in asked_ids):
        return True
    return any(piece in blob for piece in _match_pieces(terms))


def _asked_stock_ids(question: str, mentioned: set[str]) -> list[str]:
    found: list[str] = []
    for stock_id in STOCK_ID.findall(question):
        if stock_id.startswith(("19", "20")) and stock_id not in mentioned:
            continue
        if stock_id not in found:
            found.append(stock_id)
    return found


def _id_appears(stock_id: str, events: list[dict]) -> bool:
    for event in events:
        if stock_id in {str(item) for item in event.get("mentioned_ids") or []}:
            return True
        if stock_id in _blob(event):
            return True
    return False


def _match_pieces(terms: list[str]) -> list[str]:
    pieces: list[str] = []
    for term in terms:
        if term not in pieces:
            pieces.append(term)
        if len(term) <= 2:
            continue
        for index in range(len(term) - 1):
            piece = term[index : index + 2]
            if piece not in pieces:
                pieces.append(piece)
    return pieces


def _content_terms(question: str) -> list[str]:
    text = question
    for phrase in _STOP_PHRASES:
        text = text.replace(phrase, " ")
    text = STOCK_ID.sub(" ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    terms: list[str] = []
    for chunk in text.split():
        cleaned = chunk.strip()
        while cleaned and cleaned[0] in _STOP_CHARS:
            cleaned = cleaned[1:]
        while cleaned and cleaned[-1] in _STOP_CHARS:
            cleaned = cleaned[:-1]
        if len(cleaned) < 2 or cleaned in terms:
            continue
        terms.append(cleaned)
    return terms


def _render_events(events: list[dict]) -> str:
    blocks: list[str] = []
    for event in events:
        title = str(event.get("title") or "").strip()
        ids = [str(stock_id) for stock_id in event.get("mentioned_ids") or [] if str(stock_id).strip()]
        named = "、".join(ids) if ids else "未點名個股"
        blocks.append(f"- {title}：{named}")
        sources = event.get("sources") if isinstance(event.get("sources"), list) else []
        for source in sources:
            if not isinstance(source, dict):
                continue
            summary = str(source.get("summary") or "").strip()
            publisher = str(source.get("publisher") or "").strip()
            url = str(source.get("url") or "").strip()
            if summary:
                blocks.append(f"  摘要（{publisher}）：{summary}")
            if url:
                blocks.append(f"  來源：{publisher} {url}".rstrip())
    return "\n".join(blocks)


def _render_named(events: list[dict]) -> str:
    lines: list[str] = []
    for event in events:
        title = str(event.get("title") or "").strip()
        ids = [str(stock_id) for stock_id in event.get("mentioned_ids") or [] if str(stock_id).strip()]
        named = "、".join(ids) if ids else "未點名個股"
        lines.append(f"- {title}：{named}")
    return "\n".join(lines)


def _blob(event: dict) -> str:
    parts = [str(event.get("title") or "")]
    sources = event.get("sources") if isinstance(event.get("sources"), list) else []
    for source in sources:
        if not isinstance(source, dict):
            continue
        parts.append(str(source.get("title") or ""))
        parts.append(str(source.get("summary") or ""))
        parts.append(str(source.get("publisher") or ""))
    return "\n".join(parts)


def _mentioned_ids(events: list[dict]) -> set[str]:
    found: set[str] = set()
    for event in events:
        for stock_id in event.get("mentioned_ids") or []:
            text = str(stock_id).strip()
            if text:
                found.add(text)
    return found


def _theme_dir(theme_id: str, root: Path | None) -> Path:
    if not THEME_ID.fullmatch(theme_id):
        raise ValueError("題材 id 格式錯誤")
    base = (root if root is not None else reports_root()).resolve()
    folder = (base / theme_id).resolve()
    if folder != base and base not in folder.parents:
        raise ValueError("題材路徑超出報告目錄")
    return folder


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"事件表讀不出來：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("事件表格式錯誤")
    return payload


def _theme_ids_of(entry: dict) -> list[str]:
    raw = entry.get("themes") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []
