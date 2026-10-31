"""Cluster kept concept news into events. No network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "concept-news"))

from cluster_events import (  # noqa: E402
    cluster_kept,
    events_path_for_kept,
    session_of,
    write_events,
)

COLLECTED = "2026-10-28T21:55:00+08:00"
EARLY = "2026-10-28T09:00:00+08:00"
LATER = "2026-10-28T11:00:00+08:00"


def _kept(
    title: str,
    url: str,
    published: str,
    *,
    summary: str = "",
    origin: str = "yahoo",
    publisher: str = "",
    mentioned: list[str] | None = None,
) -> dict:
    ids = list(mentioned or [])
    return {
        "title": title,
        "url": url,
        "publisher": publisher,
        "published": published,
        "summary": summary,
        "origin": origin,
        "query": "面板",
        "mentioned_ids": ids,
        "theme_only": not ids,
        "keep_reason": "時間內、允許網域、對上股名",
    }


def _file(*items: dict, dropped: list | None = None) -> dict:
    return {
        "theme_id": "panel",
        "label": "面板",
        "as_of": "2026-10-28",
        "collected_at": COLLECTED,
        "lookback_hours": 48,
        "kept": list(items),
        "dropped": dropped or [],
    }


class EventTableTests(unittest.TestCase):
    def test_rewrites_become_one_event_with_the_earliest_title(self) -> None:
        table = cluster_kept(
            _file(
                _kept(
                    "友達群創同步調漲面板報價",
                    "https://tw.stock.yahoo.com/copy",
                    LATER,
                    summary="轉載",
                    publisher="Yahoo股市",
                    mentioned=["3481", "2409"],
                ),
                _kept(
                    "友達與群創同步調漲面板報價",
                    "https://www.cna.com.tw/news/afe/1",
                    EARLY,
                    summary="中央社原文",
                    origin="searxng",
                    publisher="中央社",
                    mentioned=["2409", "3481"],
                ),
            )
        )
        self.assertEqual(len(table.events), 1)
        event = table.events[0]
        self.assertEqual(event.title, "友達與群創同步調漲面板報價")
        self.assertEqual(event.event_id, "友達與群創同步調漲面板報價")
        self.assertEqual(event.published, EARLY)
        self.assertEqual(event.session, "intraday")
        self.assertEqual(event.mentioned_ids, ("2409", "3481"))
        self.assertFalse(event.theme_only)
        self.assertEqual(
            [(source.title, source.summary, source.origin) for source in event.sources],
            [
                ("友達與群創同步調漲面板報價", "中央社原文", "searxng"),
                ("友達群創同步調漲面板報價", "轉載", "yahoo"),
            ],
        )

    def test_partial_title_match_stays_its_own_event(self) -> None:
        original = "友達與群創同步調漲面板報價"
        rewrite = "友達群創同步調漲面板報價"
        prefixed = "快訊友達與群創同步調漲面板報價"
        table = cluster_kept(
            _file(
                _kept(original, "https://www.cna.com.tw/a", "2026-10-28T10:00:00+08:00", mentioned=["2409", "3481"]),
                _kept(rewrite, "https://tw.stock.yahoo.com/b", "2026-10-28T11:00:00+08:00", mentioned=["2409", "3481"]),
                _kept(prefixed, "https://money.udn.com/c", "2026-10-28T12:00:00+08:00", mentioned=["2409", "3481"]),
            )
        )
        self.assertEqual([event.title for event in table.events], [original, prefixed])
        self.assertEqual([source.title for source in table.events[0].sources], [original, rewrite])

    def test_different_stocks_stay_split_and_events_follow_time(self) -> None:
        table = cluster_kept(
            _file(
                _kept(
                    "友達董事會通過重大資本支出案",
                    "https://tw.stock.yahoo.com/a",
                    LATER,
                    mentioned=["2409"],
                ),
                _kept(
                    "群創董事會通過重大資本支出案",
                    "https://www.cna.com.tw/b",
                    EARLY,
                    mentioned=["3481"],
                ),
            )
        )
        self.assertEqual(
            [event.title for event in table.events],
            ["群創董事會通過重大資本支出案", "友達董事會通過重大資本支出案"],
        )
        self.assertEqual(table.events[0].mentioned_ids, ("3481",))
        self.assertEqual(table.events[1].mentioned_ids, ("2409",))

    def test_conflicting_facts_stay_split(self) -> None:
        pairs = (
            ("友達法說會釋出樂觀展望並維持全年資本支出", "友達法說會釋出保守展望並維持全年資本支出"),
            ("友達法說會預告第三季獲利優於預期並上修資本支出", "友達法說會預告第四季獲利優於預期並上修資本支出"),
            ("友達上修全年資本支出計畫", "友達不上修全年資本支出計畫"),
        )
        for index, (left, right) in enumerate(pairs):
            with self.subTest(left=left):
                table = cluster_kept(
                    _file(
                        _kept(left, f"https://www.cna.com.tw/{index}", EARLY, mentioned=["2409"]),
                        _kept(right, f"https://tw.stock.yahoo.com/{index}", LATER, mentioned=["2409"]),
                    )
                )
                self.assertEqual([event.title for event in table.events], [left, right])

    def test_short_titles_do_not_fuzzy_merge(self) -> None:
        table = cluster_kept(
            _file(
                _kept("群創法說", "https://www.cna.com.tw/a", EARLY, mentioned=["3481"]),
                _kept("群創法說會", "https://tw.stock.yahoo.com/b", LATER, mentioned=["3481"]),
            )
        )
        self.assertEqual([event.title for event in table.events], ["群創法說", "群創法說會"])

    def test_eighteen_hours_is_the_gap(self) -> None:
        original = "友達與群創同步調漲面板報價"
        rewrite = "友達群創同步調漲面板報價"
        start = "2026-10-27T09:00:00+08:00"
        inside = cluster_kept(
            _file(
                _kept(original, "https://www.cna.com.tw/a", start, mentioned=["2409", "3481"]),
                _kept(rewrite, "https://tw.stock.yahoo.com/b", "2026-10-28T03:00:00+08:00", mentioned=["2409", "3481"]),
            )
        )
        outside = cluster_kept(
            _file(
                _kept(original, "https://www.cna.com.tw/a", start, mentioned=["2409", "3481"]),
                _kept(rewrite, "https://tw.stock.yahoo.com/b", "2026-10-28T03:01:00+08:00", mentioned=["2409", "3481"]),
            )
        )
        self.assertEqual(len(inside.events), 1)
        self.assertEqual([event.title for event in outside.events], [original, rewrite])

    def test_equal_time_prefers_the_wire_title(self) -> None:
        stamp = "2026-10-28T11:00:00+08:00"
        table = cluster_kept(
            _file(
                _kept(
                    "友達群創同步調漲面板報價",
                    "https://tw.stock.yahoo.com/a",
                    stamp,
                    publisher="Yahoo股市",
                    mentioned=["2409", "3481"],
                ),
                _kept(
                    "友達與群創同步調漲面板報價",
                    "https://www.cna.com.tw/news/afe/1",
                    stamp,
                    origin="searxng",
                    publisher="中央社",
                    mentioned=["2409", "3481"],
                ),
            )
        )
        event = table.events[0]
        self.assertEqual(event.title, "友達與群創同步調漲面板報價")
        self.assertEqual(event.sources[0].publisher, "中央社")
        self.assertEqual(event.event_id, "友達與群創同步調漲面板報價")

    def test_publisher_prefix_within_four_characters_merges(self) -> None:
        table = cluster_kept(
            _file(
                _kept("友達與群創同步調漲", "https://www.cna.com.tw/a", EARLY, mentioned=["2409", "3481"]),
                _kept(
                    "經濟日報：友達與群創同步調漲",
                    "https://money.udn.com/b",
                    LATER,
                    origin="searxng",
                    publisher="經濟日報",
                    mentioned=["2409", "3481"],
                ),
            )
        )
        self.assertEqual(len(table.events), 1)
        self.assertEqual(table.events[0].title, "友達與群創同步調漲")
        self.assertEqual(len(table.events[0].sources), 2)

    def test_theme_only_does_not_absorb_a_named_story(self) -> None:
        plain = "面板報價連三漲後續"
        prefixed = "快訊面板報價連三漲後續"
        merged = cluster_kept(
            _file(
                _kept(plain, "https://www.cna.com.tw/a", EARLY),
                _kept(prefixed, "https://money.udn.com/b", LATER, origin="searxng"),
            )
        )
        self.assertEqual(len(merged.events), 1)
        self.assertTrue(merged.events[0].theme_only)
        self.assertEqual(merged.events[0].mentioned_ids, ())

        split = cluster_kept(
            _file(
                _kept(plain, "https://www.cna.com.tw/a", EARLY),
                _kept(prefixed, "https://money.udn.com/b", LATER, mentioned=["3481"]),
            )
        )
        self.assertEqual(len(split.events), 2)
        self.assertTrue(split.events[0].theme_only)
        self.assertEqual(split.events[1].mentioned_ids, ("3481",))

    def test_stock_ids_come_from_the_kept_row(self) -> None:
        table = cluster_kept(
            _file(
                _kept(
                    "友達與群創同步調漲面板報價",
                    "https://www.cna.com.tw/a",
                    EARLY,
                    summary="正文只寫 3481，股名另有群創",
                    mentioned=["2409"],
                )
            )
        )
        event = table.events[0]
        self.assertEqual(event.mentioned_ids, ("2409",))
        self.assertFalse(event.theme_only)
        self.assertEqual(event.sources[0].summary, "正文只寫 3481，股名另有群創")

    def test_empty_kept_still_writes_events(self) -> None:
        table = cluster_kept(
            _file(dropped=[{"title": "已丟掉", "drop_reason": "網域不在允許名單"}])
        )
        self.assertEqual(table.events, ())
        with tempfile.TemporaryDirectory() as tmp:
            path = write_events(table, Path(tmp) / "panel" / "2026-10-28_events.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["theme_id"], "panel")
        self.assertEqual(payload["lookback_hours"], 48)
        self.assertNotIn("dropped", payload)

    def test_session_follows_the_taipei_clock(self) -> None:
        self.assertEqual(session_of("2026-10-28T08:59:00+08:00"), "premarket")
        self.assertEqual(session_of("2026-10-28T09:00:00+08:00"), "intraday")
        self.assertEqual(session_of("2026-10-28T13:29:59+08:00"), "intraday")
        self.assertEqual(session_of("2026-10-28T13:30:00+08:00"), "after_close")
        self.assertEqual(session_of("2026-10-28T05:30:00Z"), "after_close")
        self.assertEqual(session_of(None), "unknown")

    def test_events_path_replaces_kept_suffix(self) -> None:
        path = events_path_for_kept(Path("reports/concept-news/panel/2026-10-28_kept.json"))
        self.assertEqual(path.as_posix(), "reports/concept-news/panel/2026-10-28_events.json")

    def test_bad_kept_raises(self) -> None:
        with self.assertRaises(ValueError):
            cluster_kept({"items": []})
        with self.assertRaises(ValueError):
            cluster_kept({"kept": [None]})
        with self.assertRaises(ValueError):
            cluster_kept(_file(_kept("", "https://www.cna.com.tw/a", EARLY)))
        with self.assertRaises(ValueError):
            cluster_kept(_file(_kept("群創法說", "https://www.cna.com.tw/a", "昨天", mentioned=["3481"])))


if __name__ == "__main__":
    unittest.main()
