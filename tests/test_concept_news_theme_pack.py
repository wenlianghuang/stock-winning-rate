"""Theme-pack contract: universe members plus a separate query map."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "concept-news"))

from theme_pack import (  # noqa: E402
    DEFAULT_QUERIES,
    DEFAULT_UNIVERSE,
    build_theme_pack,
    iter_theme_ids,
    load_queries,
    theme_pack_from_dict,
    write_theme_pack,
)

UNIVERSE = DEFAULT_UNIVERSE


def _universe_members(theme_id: str) -> list[tuple[str, str]]:
    raw = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    rows = [
        (str(item["id"]).strip(), str(item["name"]).strip())
        for item in raw["candidates"]
        if theme_id in (item.get("themes") or [])
    ]
    return sorted(rows)


class ThemePackTests(unittest.TestCase):
    def test_optical_comms_matches_universe_and_query_map(self) -> None:
        pack = build_theme_pack("optical_comms")
        self.assertEqual(pack.theme_id, "optical_comms")
        self.assertEqual(pack.label, "光通訊")
        for term in ("光通訊", "矽光子", "800G", "CPO"):
            self.assertIn(term, pack.queries)
        self.assertGreaterEqual(len(pack.queries), 2)
        self.assertTrue(any(term != pack.label for term in pack.queries))
        self.assertEqual(
            [(member.id, member.name) for member in pack.members],
            _universe_members("optical_comms"),
        )
        names = {member.name for member in pack.members}
        self.assertIn("華星光", names)
        self.assertIn("聯亞", names)
        self.assertIn("全新", names)
        for term in pack.queries:
            self.assertNotIn(term, names)

    def test_every_theme_has_queries_and_exact_members(self) -> None:
        query_ids = set(load_queries())
        theme_ids = iter_theme_ids()
        self.assertEqual(query_ids, set(theme_ids))
        self.assertGreater(len(theme_ids), 0)
        for theme_id in theme_ids:
            pack = build_theme_pack(theme_id)
            self.assertEqual(pack.theme_id, theme_id)
            self.assertTrue(pack.label)
            self.assertGreaterEqual(len(pack.queries), 2)
            self.assertTrue(any(term != pack.label for term in pack.queries))
            self.assertEqual(
                [(member.id, member.name) for member in pack.members],
                _universe_members(theme_id),
            )
            reserved = {member.id for member in pack.members} | {
                member.name for member in pack.members
            }
            for term in pack.queries:
                self.assertNotIn(term, reserved)

    def test_names_are_copied_verbatim(self) -> None:
        pack = build_theme_pack("passive_components")
        member = next(item for item in pack.members if item.id == "2327")
        self.assertEqual(member.name, "國巨*")

    def test_empty_theme_still_has_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe.json"
            queries = root / "queries.json"
            universe.write_text(
                json.dumps(
                    {"themes": {"scratch": {"label": "空題材"}}, "candidates": []},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            queries.write_text(
                json.dumps(
                    {"queries": {"scratch": ["空題材新聞", "另一個詞"]}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            pack = build_theme_pack(
                "scratch", universe_path=universe, queries_path=queries
            )
        self.assertEqual(pack.members, ())
        self.assertEqual(pack.queries, ("空題材新聞", "另一個詞"))

    def test_unknown_theme_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_theme_pack("not_a_theme")

    def test_write_roundtrip(self) -> None:
        pack = build_theme_pack("panel")
        with tempfile.TemporaryDirectory() as tmp:
            written = write_theme_pack(pack, Path(tmp) / "panel" / "theme_pack.json")
            payload = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(payload["theme_id"], "panel")
        self.assertEqual(payload["label"], "面板")
        self.assertEqual(
            [(item["id"], item["name"]) for item in payload["members"]],
            [("2409", "友達"), ("3481", "群創")],
        )
        self.assertTrue(DEFAULT_QUERIES.exists())

    def test_pack_file_roundtrip_keeps_members(self) -> None:
        pack = build_theme_pack("optical_comms")
        loaded = theme_pack_from_dict(pack.to_dict())
        self.assertEqual(loaded, pack)


if __name__ == "__main__":
    unittest.main()
