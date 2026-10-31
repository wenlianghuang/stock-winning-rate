"""Concept-news collection: one item shape, failures stay inside the raw file."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "concept-news"))

from collect_news import (  # noqa: E402
    Article,
    collect_theme_news,
    read_searxng_results,
    write_theme_news_raw,
)
from theme_pack import ThemeMember, ThemePack  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 28, 21, 0, tzinfo=TAIPEI)
RECENT = datetime(2026, 10, 28, 9, 0, tzinfo=TAIPEI)
OLDER = datetime(2026, 10, 27, 9, 0, tzinfo=TAIPEI)
STALE = datetime(2026, 10, 26, 20, 0, tzinfo=TAIPEI)

PACK = ThemePack(
    theme_id="optical_comms",
    label="光通訊",
    queries=("光通訊", "矽光子"),
    members=(
        ThemeMember("3081", "聯亞"),
        ThemeMember("4979", "華星光"),
    ),
)


def _article(title: str, *, published: datetime | None = RECENT, url: str = "https://tw.stock.yahoo.com/a", publisher: str = "經濟日報", summary: str = "") -> Article:
    return Article(title=title, url=url, publisher=publisher, published=published, summary=summary)


class CollectNewsTests(unittest.TestCase):
    def test_yahoo_and_searx_share_item_fields(self) -> None:
        def yahoo(stock_id: str) -> list[Article]:
            if stock_id == "4979":
                return [_article("華星光與聯亞同獲訂單", summary="光模組出貨")]
            return [_article("聯亞法說", url="https://tw.stock.yahoo.com/b")]

        def searx(query: str) -> list[Article]:
            if query == "矽光子":
                return [
                    _article(
                        "矽光子供應鏈升溫",
                        url="https://www.cna.com.tw/news/1",
                        publisher="",
                        summary="未點名個股",
                    )
                ]
            return []

        raw = collect_theme_news(PACK, yahoo_fetch=yahoo, searx_fetch=searx, now=NOW, as_of="2026-10-28")
        self.assertEqual(raw.failures, ())
        origins = [item.origin for item in raw.items]
        self.assertEqual(origins, ["yahoo", "yahoo", "searxng"])
        yahoo_item = next(item for item in raw.items if item.query == "4979")
        self.assertEqual(yahoo_item.matched_ids, ("3081", "4979"))
        self.assertEqual(yahoo_item.publisher, "經濟日報")
        theme_item = raw.items[2]
        self.assertEqual(theme_item.query, "矽光子")
        self.assertEqual(theme_item.matched_ids, ())
        self.assertEqual(theme_item.publisher, "cna.com.tw")
        keys = set(raw.to_dict()["items"][0])
        self.assertEqual(
            keys,
            {"title", "url", "publisher", "published", "summary", "origin", "query", "matched_ids"},
        )

    def test_yahoo_always_matches_the_queried_id(self) -> None:
        def yahoo(stock_id: str) -> list[Article]:
            if stock_id != "4979":
                return []
            return [_article("光模組訂單", summary="正文沒有股名，只有 4979")]

        raw = collect_theme_news(PACK, yahoo_fetch=yahoo, searx_fetch=lambda _query: [], now=NOW)
        self.assertEqual(len(raw.items), 1)
        self.assertEqual(raw.items[0].matched_ids, ("4979",))
        self.assertNotIn("3081", raw.items[0].matched_ids)

    def test_lookback_drops_stale_and_caps_newest(self) -> None:
        def yahoo(stock_id: str) -> list[Article]:
            if stock_id != "3081":
                return []
            return [
                _article("舊聞", published=STALE),
                _article("較早", published=OLDER, url="https://example.com/old"),
                _article("最新", published=RECENT, url="https://example.com/new"),
                _article("沒時間", published=None, url="https://example.com/none"),
            ]

        raw = collect_theme_news(
            PACK,
            yahoo_fetch=yahoo,
            searx_fetch=lambda _query: [],
            now=NOW,
            max_items=2,
        )
        titles = [item.title for item in raw.items]
        self.assertEqual(titles, ["最新", "較早"])

    def test_one_failure_keeps_the_rest(self) -> None:
        def yahoo(stock_id: str) -> list[Article]:
            if stock_id == "3081":
                raise RuntimeError("yahoo down")
            return [_article("華星光快訊")]

        def searx(query: str) -> list[Article]:
            if query == "光通訊":
                raise RuntimeError("connection refused")
            return [_article("矽光子題材", url="https://news.cnyes.com/x")]

        raw = collect_theme_news(PACK, yahoo_fetch=yahoo, searx_fetch=searx, now=NOW)
        self.assertEqual([item.query for item in raw.items], ["4979", "矽光子"])
        self.assertEqual(
            [(failure.origin, failure.query) for failure in raw.failures],
            [("yahoo", "3081"), ("searxng", "光通訊")],
        )
        self.assertIn("yahoo down", raw.failures[0].error)

    def test_skip_searxng_records_each_query(self) -> None:
        raw = collect_theme_news(
            PACK,
            yahoo_fetch=lambda _stock_id: [],
            searx_fetch=None,
            now=NOW,
            searx_skip_reason="已略過 SearXNG",
        )
        self.assertEqual(raw.items, ())
        self.assertEqual(len(raw.failures), 2)
        self.assertTrue(all(failure.origin == "searxng" for failure in raw.failures))
        self.assertEqual(raw.failures[0].error, "已略過 SearXNG")

    def test_searx_payload_and_dead_engines(self) -> None:
        articles = read_searxng_results(
            {
                "results": [
                    {
                        "title": "800G 出貨",
                        "url": "https://www.ctee.com.tw/news/1",
                        "content": "華星光出貨",
                        "publishedDate": "2026-10-28T01:00:00Z",
                    }
                ]
            }
        )
        self.assertEqual(articles[0].publisher, "ctee.com.tw")
        self.assertEqual(articles[0].published, datetime(2026, 10, 28, 9, 0, tzinfo=TAIPEI))
        self.assertEqual(articles[0].summary, "華星光出貨")
        with self.assertRaises(ValueError):
            read_searxng_results({"results": [], "unresponsive_engines": [["google", "timeout"]]})

    def test_write_raw_roundtrip(self) -> None:
        raw = collect_theme_news(
            PACK,
            yahoo_fetch=lambda stock_id: [_article("華星光")] if stock_id == "4979" else [],
            searx_fetch=None,
            now=NOW,
            as_of="2026-10-28",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_theme_news_raw(raw, Path(tmp) / "optical_comms" / "2026-10-28_raw.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["theme_id"], "optical_comms")
        self.assertEqual(payload["as_of"], "2026-10-28")
        self.assertEqual(payload["lookback_hours"], 48)
        self.assertEqual(payload["items"][0]["origin"], "yahoo")
        self.assertEqual(payload["items"][0]["matched_ids"], ["4979"])
        self.assertEqual(payload["failures"][0]["origin"], "searxng")


if __name__ == "__main__":
    unittest.main()
