"""Concept-news entry: read written files, answer from one event table, run one theme."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "concept-news"))

from answer_events import (  # noqa: E402
    EMPTY,
    MISS,
    NO_ADVICE,
    answer_from_events,
    list_themes,
    list_written_reports,
    load_written_report,
)
from collect_news import Article  # noqa: E402
from run_theme import main as run_main  # noqa: E402
from run_theme import news_report_date, run_theme  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 28, 22, 0, tzinfo=TAIPEI)
TITLE = "友達與群創同步調漲面板報價"


def _table(*events: dict, empty: bool = False) -> dict:
    return {
        "theme_id": "panel",
        "label": "面板",
        "as_of": "2026-10-28",
        "collected_at": "2026-10-28T22:00:00+08:00",
        "lookback_hours": 48,
        "events": [] if empty else list(events),
    }


def _event() -> dict:
    return {
        "event_id": "auo",
        "title": TITLE,
        "published": "2026-10-28T18:00:00+08:00",
        "sources": [
            {
                "title": TITLE,
                "url": "https://www.cna.com.tw/a",
                "publisher": "中央社",
                "published": "2026-10-28T18:00:00+08:00",
                "origin": "yahoo",
                "summary": "友達（2409）與群創同步調漲。",
            }
        ],
        "mentioned_ids": ["2409", "3481"],
        "theme_only": False,
    }


def _page() -> str:
    return (
        "## 看法\n"
        "這批收盤後的稿是在講資金進出，不是這個題材的報價、產能或出貨。"
        "同一件事被寫成多則時，標題數量會讓人高估分量，不該照單全收。"
        "公司公告或合作若沒有接到本業，就不能上升成營運轉折。"
        "早盤漲跌和收盤後的統計衝突時，結論站在收盤後，早盤只是過程。\n\n"
        f"## 收盤後\n- {TITLE}：2409、3481\n\n"
        f"## 來源列表\n- {TITLE}｜中央社｜https://www.cna.com.tw/a\n"
    )


class AnswerEventsTests(unittest.TestCase):
    def test_empty_table_says_nothing_passed(self) -> None:
        result = answer_from_events("友達怎麼樣", _table(empty=True))
        self.assertFalse(result["found"])
        self.assertIn(EMPTY, result["answer"])
        self.assertEqual(result["event_ids"], [])

    def test_named_stock_is_quoted_from_the_file(self) -> None:
        result = answer_from_events("友達被點名了嗎", _table(_event()))
        self.assertTrue(result["found"])
        self.assertIn(TITLE, result["answer"])
        self.assertIn("2409", result["answer"])
        self.assertIn("同步調漲", result["answer"])
        self.assertIn("https://www.cna.com.tw/a", result["answer"])
        self.assertNotIn("目標價", result["answer"])

    def test_unknown_topic_is_a_miss(self) -> None:
        result = answer_from_events("矽光子出貨", _table(_event()))
        self.assertFalse(result["found"])
        self.assertIn(MISS, result["answer"])
        self.assertNotIn(TITLE, result["answer"])

    def test_missing_stock_id_is_named(self) -> None:
        result = answer_from_events("9999 有沒有被寫到", _table(_event()))
        self.assertFalse(result["found"])
        self.assertIn("9999", result["answer"])
        self.assertIn(MISS, result["answer"])

    def test_partial_phrase_says_what_is_missing(self) -> None:
        result = answer_from_events("友達產能", _table(_event()))
        self.assertTrue(result["found"])
        self.assertIn("事件表沒有寫到「友達產能」", result["answer"])
        self.assertIn("同步調漲", result["answer"])

    def test_advice_question_does_not_add_a_call(self) -> None:
        result = answer_from_events("友達要不要買進", _table(_event()))
        self.assertTrue(result["found"])
        self.assertIn(NO_ADVICE, result["answer"])
        self.assertNotIn("建議買進", result["answer"])

    def test_who_was_named_lists_ids_only(self) -> None:
        result = answer_from_events("誰被點名", _table(_event()))
        self.assertTrue(result["found"])
        self.assertIn("2409、3481", result["answer"])
        self.assertNotIn("摘要（", result["answer"])

    def test_year_is_not_a_stock_id(self) -> None:
        result = answer_from_events("2026 矽光子", _table(_event()))
        self.assertFalse(result["found"])
        self.assertNotIn("2026 沒有出現", result["answer"])


class WrittenReportTests(unittest.TestCase):
    def test_catalog_reads_only_written_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "panel"
            folder.mkdir()
            (folder / "2026-10-28_events.json").write_text(
                json.dumps(_table(_event()), ensure_ascii=False),
                encoding="utf-8",
            )
            (folder / "2026-10-28_summary.md").write_text("# 面板\n", encoding="utf-8")
            (folder / "2026-10-28_raw.json").write_text("{}", encoding="utf-8")
            items = list_written_reports("panel", root)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["event_count"], 1)
            self.assertTrue(items[0]["has_summary"])
            loaded = load_written_report("panel", "2026-10-28", root)
            assert loaded is not None
            self.assertEqual(loaded["summary"], "# 面板\n")
            self.assertIsNone(load_written_report("panel", "2026-10-01", root))

    def test_theme_id_cannot_escape_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                list_written_reports("../secret", Path(tmp))

    def test_theme_list_comes_from_the_universe(self) -> None:
        themes = list_themes()
        panel = next(item for item in themes if item["theme_id"] == "panel")
        self.assertEqual(panel["label"], "面板")
        self.assertGreater(panel["member_count"], 0)


class RunThemeTests(unittest.TestCase):
    def test_news_date_opens_at_18_and_chips_stay_at_2130(self) -> None:
        calendar = (ROOT / ".agents" / "skills" / "tw-stock-report" / "twse_calendar.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("CHIP_READY_HOUR = 21", calendar)
        self.assertIn("CHIP_READY_MINUTE = 30", calendar)
        early = datetime(2026, 10, 29, 17, 59, tzinfo=TAIPEI)
        opened = datetime(2026, 10, 29, 18, 0, tzinfo=TAIPEI)
        self.assertEqual(news_report_date(early), "2026-10-28")
        self.assertEqual(news_report_date(opened), "2026-10-29")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as caught:
                run_theme(
                    "panel",
                    as_of="2026-10-29",
                    skip_summary=True,
                    skip_searxng=True,
                    output_root=Path(tmp),
                    yahoo_fetch=lambda _stock_id: [],
                    now=early,
                )
        self.assertIn("18:00", str(caught.exception))

    def test_collects_when_no_event_file_exists(self) -> None:
        calls: list[str] = []

        def yahoo(stock_id: str) -> list[Article]:
            calls.append(stock_id)
            if stock_id != "2409":
                return []
            return [
                Article(
                    title="友達法說維持展望",
                    url="https://tw.stock.yahoo.com/news/auo",
                    publisher="經濟日報",
                    published=datetime(2026, 10, 28, 10, 0, tzinfo=TAIPEI),
                    summary="友達維持既有看法。",
                )
            ]

        with tempfile.TemporaryDirectory() as tmp:
            result = run_theme(
                "panel",
                as_of="2026-10-28",
                skip_summary=True,
                skip_searxng=True,
                output_root=Path(tmp),
                yahoo_fetch=yahoo,
                now=NOW,
            )
            self.assertTrue(result["searched"])
            self.assertIn("2409", calls)
            self.assertIsNone(result["summary"])
            events = result["events"]["events"]
            self.assertEqual(len(events), 1)
            self.assertIn("2409", events[0]["mentioned_ids"])
            self.assertEqual(events[0]["session"], "intraday")
            self.assertTrue(Path(result["events_path"]).is_file())

    def test_existing_events_skip_the_search(self) -> None:
        def boom(_stock_id: str) -> list[Article]:
            raise AssertionError("不該蒐集")

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "panel"
            folder.mkdir()
            table = _table(_event())
            (folder / "2026-10-28_events.json").write_text(
                json.dumps(table, ensure_ascii=False),
                encoding="utf-8",
            )
            result = run_theme(
                "panel",
                as_of="2026-10-28",
                skip_summary=False,
                output_root=Path(tmp),
                yahoo_fetch=boom,
                complete=lambda _prompt: (_page(), 0),
                now=NOW,
            )
            self.assertFalse(result["searched"])
            self.assertIn(TITLE, result["summary"])
            self.assertTrue((folder / "2026-10-28_summary.md").is_file())

            again = run_theme(
                "panel",
                as_of="2026-10-28",
                output_root=Path(tmp),
                yahoo_fetch=boom,
                now=NOW,
            )
        self.assertTrue(again["reused"])
        self.assertFalse(again["searched"])

    def test_cli_rejects_an_unknown_theme(self) -> None:
        code = run_main(["--theme", "not_a_theme", "--skip-summary", "--skip-searxng"])
        self.assertEqual(code, 1)


class ConceptNewsApiTests(unittest.TestCase):
    def test_reads_written_report_and_answers_without_search(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "panel"
            folder.mkdir()
            (folder / "2026-10-28_events.json").write_text(
                json.dumps(_table(_event()), ensure_ascii=False),
                encoding="utf-8",
            )
            (folder / "2026-10-28_summary.md").write_text("# 已寫好\n", encoding="utf-8")
            previous = os.environ.get("CONCEPT_NEWS_ROOT")
            os.environ["CONCEPT_NEWS_ROOT"] = tmp
            try:
                client = TestClient(create_app())
                listed = client.get("/concept-news/reports", params={"theme": "panel"})
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()["items"][0]["date"], "2026-10-28")
                loaded = client.get("/concept-news/reports/panel/2026-10-28")
                self.assertEqual(loaded.status_code, 200)
                self.assertIn("已寫好", loaded.json()["report"]["summary"])
                asked = client.post(
                    "/concept-news/ask",
                    json={"theme": "panel", "date": "2026-10-28", "message": "矽光子"},
                )
                self.assertEqual(asked.status_code, 200)
                self.assertFalse(asked.json()["found"])
                self.assertIn(MISS, asked.json()["answer"])
                missing = client.post(
                    "/concept-news/ask",
                    json={"theme": "panel", "date": "2026-10-01", "message": "友達"},
                )
                self.assertEqual(missing.status_code, 404)
            finally:
                if previous is None:
                    os.environ.pop("CONCEPT_NEWS_ROOT", None)
                else:
                    os.environ["CONCEPT_NEWS_ROOT"] = previous

    def test_unknown_theme_is_rejected(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")

        client = TestClient(create_app())
        response = client.post("/concept-news/jobs", json={"theme": "not_a_theme"})
        self.assertEqual(response.status_code, 422)

    def test_job_status_is_readable(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")

        def fake_run(theme_id: str, **_kwargs):
            return {
                "theme_id": theme_id,
                "label": "面板",
                "as_of": "2026-10-28",
                "summary": "# 面板\n",
                "events": _table(_event()),
                "searched": False,
                "reused": True,
                "exit_code": 0,
            }

        with patch("run_theme.run_theme", side_effect=fake_run):
            client = TestClient(create_app())
            created = client.post(
                "/concept-news/jobs",
                json={"theme": "panel", "date": "2026-10-28"},
            )
            self.assertEqual(created.status_code, 200)
            job_id = created.json()["job"]["id"]
            payload = {}
            for _ in range(40):
                payload = client.get(f"/concept-news/jobs/{job_id}").json()["job"]
                if payload["status"] in {"done", "failed"}:
                    break
                time.sleep(0.05)
        self.assertEqual(payload["status"], "done")
        self.assertFalse(payload["searched"])
        self.assertIn("面板", payload["summary"])


if __name__ == "__main__":
    unittest.main()
