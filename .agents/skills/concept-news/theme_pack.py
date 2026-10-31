#!/usr/bin/env python3
"""Build a concept-news theme pack from the peer universe and query map.

This layer does not fetch news and does not call a model. Member names come
only from concept_peers_universe.json. Search terms come only from
theme_queries.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_UNIVERSE = (
    Path(__file__).resolve().parents[1]
    / "portfolio-gate"
    / "concept_peers_universe.json"
)
DEFAULT_QUERIES = Path(__file__).with_name("theme_queries.json")
MIN_QUERIES = 2


@dataclass(frozen=True)
class ThemeMember:
    id: str
    name: str


@dataclass(frozen=True)
class ThemePack:
    theme_id: str
    label: str
    queries: tuple[str, ...]
    members: tuple[ThemeMember, ...]

    def to_dict(self) -> dict:
        return {
            "theme_id": self.theme_id,
            "label": self.label,
            "queries": list(self.queries),
            "members": [{"id": member.id, "name": member.name} for member in self.members],
        }


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_theme_pack_path(theme_id: str, root: Path | None = None) -> Path:
    base = root if root is not None else project_root() / "reports" / "concept-news"
    return base / theme_id / "theme_pack.json"


def load_universe(path: Path | None = None) -> tuple[dict[str, dict], list[dict]]:
    universe_path = Path(path) if path else DEFAULT_UNIVERSE
    raw = json.loads(universe_path.read_text(encoding="utf-8"))
    themes = raw.get("themes") if isinstance(raw, dict) else None
    candidates = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(themes, dict) or not isinstance(candidates, list):
        raise ValueError(f"宇宙檔格式錯誤：{universe_path}")
    cleaned = [item for item in candidates if isinstance(item, dict)]
    return themes, cleaned


def load_queries(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    queries_path = Path(path) if path else DEFAULT_QUERIES
    raw = json.loads(queries_path.read_text(encoding="utf-8"))
    block = raw.get("queries") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        raise ValueError(f"搜尋詞對照格式錯誤：{queries_path}")

    out: dict[str, tuple[str, ...]] = {}
    for theme_id, terms in block.items():
        key = str(theme_id).strip()
        if not key:
            raise ValueError("搜尋詞對照含空白題材 id")
        if not isinstance(terms, list):
            raise ValueError(f"{key} 的搜尋詞不是陣列")
        cleaned: list[str] = []
        for term in terms:
            text = str(term).strip()
            if not text:
                raise ValueError(f"{key} 含空白搜尋詞")
            if text not in cleaned:
                cleaned.append(text)
        if len(cleaned) < MIN_QUERIES:
            raise ValueError(f"{key} 至少要 {MIN_QUERIES} 個搜尋詞")
        out[key] = tuple(cleaned)
    return out


def iter_theme_ids(universe_path: Path | None = None) -> list[str]:
    themes, _candidates = load_universe(universe_path)
    return sorted(str(theme_id) for theme_id in themes)


def _theme_ids_of(entry: dict) -> list[str]:
    raw = entry.get("themes") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


def _members_for(theme_id: str, candidates: list[dict]) -> tuple[ThemeMember, ...]:
    names: dict[str, str] = {}
    for entry in candidates:
        if theme_id not in _theme_ids_of(entry):
            continue
        stock_id = str(entry.get("id", "")).strip()
        stock_name = str(entry.get("name", "")).strip()
        if not stock_id:
            continue
        if not stock_name:
            raise ValueError(f"{theme_id} 的 {stock_id} 沒有股名")
        previous = names.get(stock_id)
        if previous is not None and previous != stock_name:
            raise ValueError(f"{stock_id} 股名不一致：{previous} / {stock_name}")
        names[stock_id] = stock_name
    return tuple(
        ThemeMember(id=stock_id, name=names[stock_id]) for stock_id in sorted(names)
    )


def build_theme_pack(
    theme_id: str,
    *,
    universe_path: Path | None = None,
    queries_path: Path | None = None,
) -> ThemePack:
    """Return the query pack for one theme. Names come from the universe only."""
    key = theme_id.strip()
    if not key:
        raise ValueError("題材 id 是空的")

    themes, candidates = load_universe(universe_path)
    meta = themes.get(key)
    if not isinstance(meta, dict):
        raise ValueError(f"未知題材：{key}")

    label = str(meta.get("label", "")).strip()
    if not label:
        raise ValueError(f"{key} 沒有中文標籤")

    queries = load_queries(queries_path)
    terms = queries.get(key)
    if terms is None:
        raise ValueError(f"{key} 沒有搜尋詞對照")
    if all(term == label for term in terms):
        raise ValueError(f"{key} 搜尋詞不能只有標籤")

    members = _members_for(key, candidates)
    reserved = {member.id for member in members} | {member.name for member in members}
    collided = next((term for term in terms if term in reserved), None)
    if collided is not None:
        raise ValueError(f"{key} 搜尋詞含池內股名或股號：{collided}")

    return ThemePack(theme_id=key, label=label, queries=terms, members=members)


def theme_pack_from_dict(payload: dict) -> ThemePack:
    """Read a theme pack that was already written. Does not reopen the universe."""
    if not isinstance(payload, dict):
        raise ValueError("theme_pack 格式錯誤")
    theme_id = str(payload.get("theme_id") or "").strip()
    label = str(payload.get("label") or "").strip()
    raw_queries = payload.get("queries")
    raw_members = payload.get("members")
    if not theme_id or not label or not isinstance(raw_queries, list) or not isinstance(raw_members, list):
        raise ValueError("theme_pack 缺少 theme_id、label、queries 或 members")

    queries: list[str] = []
    for term in raw_queries:
        text = str(term).strip()
        if text and text not in queries:
            queries.append(text)
    if len(queries) < MIN_QUERIES:
        raise ValueError(f"{theme_id} 至少要 {MIN_QUERIES} 個搜尋詞")

    names: dict[str, str] = {}
    for row in raw_members:
        if not isinstance(row, dict):
            raise ValueError(f"{theme_id} 成員不是物件")
        stock_id = str(row.get("id") or "").strip()
        stock_name = str(row.get("name") or "").strip()
        if not stock_id or not stock_name:
            raise ValueError(f"{theme_id} 成員缺股號或股名")
        previous = names.get(stock_id)
        if previous is not None and previous != stock_name:
            raise ValueError(f"{stock_id} 股名不一致：{previous} / {stock_name}")
        names[stock_id] = stock_name
    members = tuple(ThemeMember(id=stock_id, name=names[stock_id]) for stock_id in sorted(names))
    return ThemePack(
        theme_id=theme_id,
        label=label,
        queries=tuple(queries),
        members=members,
    )


def load_theme_pack(path: Path) -> ThemePack:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return theme_pack_from_dict(payload)


def write_theme_pack(pack: ThemePack, path: Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(pack.to_dict(), ensure_ascii=False, indent=2) + "\n"
    destination.write_text(payload, encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產出概念題材查詢包")
    parser.add_argument("--theme", required=True, help="concept_peers_universe.json 的 theme id")
    parser.add_argument("--out", type=Path, default=None, help="寫出路徑；預設 reports/concept-news/{theme}/theme_pack.json")
    parser.add_argument("--stdout", action="store_true", help="只印 JSON，不寫檔")
    args = parser.parse_args(argv)

    pack = build_theme_pack(args.theme)
    if args.stdout:
        sys.stdout.write(json.dumps(pack.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return 0

    destination = args.out if args.out is not None else default_theme_pack_path(pack.theme_id)
    written = write_theme_pack(pack, destination)
    print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
