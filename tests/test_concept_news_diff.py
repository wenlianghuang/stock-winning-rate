"""Compare an event table with the previous file. No network and no model."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "concept-news"))

from compare_events import (  # noqa: E402
    ADDED,
    CONTINUED,
    DIFF_HEADING,
    DROPPED,
    append_diff_section,
    compare_event_tables,
    load_previous_events,
    previous_events_path,
    render_diff_section,
)
from process_report import execute_report  # noqa: E402
from validate_concept_report import validate_concept_report  # noqa: E402

KEPT = "友達與群創同步調漲面板報價"
GONE = "群創法說會釋出樂觀展望"
NEW = "面板報價連三漲"


def _event(title: str, event_id: str | None = None, ids: list[str] | None = None) -> dict:
    return {
        "event_id": title if event_id is None else event_id,
        "title": title,
        "published": "2026-10-28T18:00:00+08:00",
        "sources": [
            {
                "title": title,
                "url": "https://www.cna.com.tw/a",
                "publisher": "中央社",
                "published": "2026-10-28T18:00:00+08:00",
                "origin": "searxng",
                "summary": "",
            }
        ],
        "mentioned_ids": ["2409"] if ids is None else ids,
        "theme_only": not (["2409"] if ids is None else ids),
    }


def _table(day: str, *events: dict, theme: str = "panel") -> dict:
    return {
        "theme_id": theme,
        "label": "面板",
        "as_of": day,
        "collected_at": f"{day}T21:00:00+08:00",
        "lookback_hours": 48,
        "events": list(events),
    }


def _page(*titles: str) -> str:
    def block(fmt) -> str:
        return "\n".join(fmt(title) for title in titles)

    return (
        "## 看法\n"
        "這批收盤後的稿是在講資金進出，不是這個題材的報價、產能或出貨。"
        "同一件事被寫成多則時，標題數量會讓人高估分量，不該照單全收。"
        "公司公告或合作若沒有接到本業，就不能上升成營運轉折。"
        "早盤漲跌和收盤後的統計衝突時，結論站在收盤後，早盤只是過程。\n\n"
        "## 收盤後\n"
        + block(lambda title: f"- {title}：2409")
        + "\n\n## 來源列表\n"
        + block(lambda title: f"- {title}｜中央社｜https://www.cna.com.tw/a")
        + "\n"
    )


class CompareEventsTests(unittest.TestCase):
    def test_keys_split_into_added_continued_and_dropped(self) -> None:
        current = _table("2026-10-28", _event(KEPT, "same-key", ["2409"]), _event(NEW))
        current["events"][0]["title"] = "友達與群創仍同步調漲"
        previous = _table("2026-10-27", _event(KEPT, "same-key"), _event(GONE, "old-key"))
        diff = compare_event_tables(current, previous)
        self.assertEqual(diff.titles(ADDED), (NEW,))
        self.assertEqual(diff.titles(CONTINUED), ("友達與群創仍同步調漲",))
        self.assertEqual(diff.titles(DROPPED), (GONE,))
        text = render_diff_section(diff)
        self.assertLess(text.index("新增"), text.index("沿用"))
        self.assertLess(text.index("沿用"), text.index("未再出現"))

    def test_missing_event_id_uses_the_normalized_title(self) -> None:
        current = _table("2026-10-28", _event("友達", ""))
        previous = _table("2026-10-27", _event("友達！", ""))
        diff = compare_event_tables(current, previous)
        self.assertEqual(diff.titles(CONTINUED), ("友達",))
        self.assertEqual(diff.titles(ADDED), ())
        self.assertEqual(diff.titles(DROPPED), ())

    def test_different_keys_stay_apart_when_titles_match(self) -> None:
        current = _table("2026-10-28", _event("面板報價", "new-key"))
        previous = _table("2026-10-27", _event("面板報價", "old-key"))
        diff = compare_event_tables(current, previous)
        self.assertEqual(diff.titles(CONTINUED), ())
        self.assertEqual(diff.titles(ADDED), ("面板報價",))
        self.assertEqual(diff.titles(DROPPED), ("面板報價",))

    def test_empty_status_is_explicit(self) -> None:
        diff = compare_event_tables(_table("2026-10-28", _event(NEW)), _table("2026-10-27"))
        self.assertIn("- 新增：" + NEW, render_diff_section(diff))
        self.assertIn("- 沿用：無", render_diff_section(diff))
        self.assertIn("- 未再出現：無", render_diff_section(diff))

    def test_previous_file_is_the_latest_earlier_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            current = folder / "2026-10-28_events.json"
            current.write_text("{}\n", encoding="utf-8")
            (folder / "2026-10-26_events.json").write_text("{}\n", encoding="utf-8")
            (folder / "2026-10-27_events.json").write_text("{}\n", encoding="utf-8")
            (folder / "2026-10-29_events.json").write_text("{}\n", encoding="utf-8")
            (folder / "notes_events.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(
                previous_events_path(current, "2026-10-28"),
                folder / "2026-10-27_events.json",
            )
            self.assertIsNone(previous_events_path(folder / "2026-10-26_events.json", "2026-10-26"))

    def test_missing_or_unusable_previous_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            current = folder / "2026-10-28_events.json"
            current.write_text(json.dumps(_table("2026-10-28")), encoding="utf-8")
            self.assertIsNone(load_previous_events(current, "2026-10-28", "panel"))

            broken = folder / "2026-10-27_events.json"
            broken.write_text("{", encoding="utf-8")
            self.assertIsNone(load_previous_events(current, "2026-10-28", "panel"))

            broken.write_text(
                json.dumps(_table("2026-10-27", _event(GONE), theme="optical_comms")),
                encoding="utf-8",
            )
            self.assertIsNone(load_previous_events(current, "2026-10-28", "panel"))

    def test_appended_section_replaces_a_model_draft(self) -> None:
        diff = compare_event_tables(
            _table("2026-10-28", _event(NEW)),
            _table("2026-10-27", _event(GONE)),
        )
        body = _page(NEW) + f"\n## {DIFF_HEADING}\n- 新增：假標題\n"
        text = append_diff_section(body, diff)
        self.assertEqual(text.count(f"## {DIFF_HEADING}"), 1)
        self.assertNotIn("假標題", text)
        self.assertIn(f"- 新增：{NEW}", text)
        self.assertIn(f"- 未再出現：{GONE}", text)

    def test_diff_section_does_not_fail_title_checks(self) -> None:
        body = _page(NEW) + f"\n\n## {DIFF_HEADING}\n- 未再出現：{GONE}\n"
        result = validate_concept_report(body, _table("2026-10-28", _event(NEW)))
        self.assertTrue(result.passed, result.summary_lines())

    def test_report_gains_the_diff_only_when_a_previous_file_exists(self) -> None:
        current = _table("2026-10-28", _event(KEPT), _event(NEW))
        previous = _table("2026-10-27", _event(KEPT), _event(GONE))

        def complete(_prompt: str) -> tuple[str, int]:
            drafted = _page(KEPT, NEW) + f"\n## {DIFF_HEADING}\n- 新增：假標題\n"
            return drafted, 0

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder / "2026-10-28_events.json"
            events.write_text(json.dumps(current), encoding="utf-8")
            (folder / "2026-10-27_events.json").write_text(json.dumps(previous), encoding="utf-8")
            summary = folder / "2026-10-28_summary.md"
            code = execute_report(
                current,
                events,
                summary_path=summary,
                gate_log_path=folder / "2026-10-28.gate.log",
                gate_rounds_dir=folder / "rounds",
                max_rounds=1,
                min_citations=1,
                complete=complete,
            )
            text = summary.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn(f"- 沿用：{KEPT}", text)
        self.assertIn(f"- 新增：{NEW}", text)
        self.assertIn(f"- 未再出現：{GONE}", text)
        self.assertNotIn("假標題", text)

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder / "2026-10-28_events.json"
            events.write_text(json.dumps(current), encoding="utf-8")
            summary = folder / "2026-10-28_summary.md"
            code = execute_report(
                current,
                events,
                summary_path=summary,
                gate_log_path=folder / "2026-10-28.gate.log",
                gate_rounds_dir=folder / "rounds",
                max_rounds=1,
                min_citations=1,
                complete=lambda _prompt: (_page(KEPT, NEW), 0),
            )
            text = summary.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertNotIn(DIFF_HEADING, text)


if __name__ == "__main__":
    unittest.main()
