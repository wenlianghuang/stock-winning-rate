"""Filter raw concept news with a fixed payload. No network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "concept-news"))

from filter_news import (  # noqa: E402
    DROP_DOMAIN,
    DROP_DUP,
    DROP_NO_TIME,
    DROP_OUTSIDE,
    filter_raw,
    write_kept_news,
)
from theme_pack import ThemeMember, ThemePack  # noqa: E402

PACK = ThemePack(
    theme_id="panel",
    label="面板",
    queries=("面板", "面板報價"),
    members=(ThemeMember("2409", "友達"), ThemeMember("3481", "群創")),
)
COLLECTED = "2026-10-28T21:55:00+08:00"


def _item(
    title: str,
    url: str,
    published: str | None,
    *,
    summary: str = "",
    origin: str = "yahoo",
    query: str = "3481",
    publisher: str = "",
    matched_ids: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "url": url,
        "publisher": publisher,
        "published": published,
        "summary": summary,
        "origin": origin,
        "query": query,
        "matched_ids": matched_ids or [],
    }


def _raw(*items: dict) -> dict:
    return {
        "theme_id": "panel",
        "label": "面板",
        "as_of": "2026-10-28",
        "collected_at": COLLECTED,
        "lookback_hours": 48,
        "items": list(items),
        "failures": [],
    }


class FilterNewsTests(unittest.TestCase):
    def test_time_domain_and_duplicate_reasons(self) -> None:
        news = filter_raw(
            _raw(
                _item("舊聞", "https://tw.stock.yahoo.com/old", "2026-10-26T20:00:00+08:00"),
                _item("沒時間", "https://tw.stock.yahoo.com/none", None),
                _item("東森稿", "https://news.ebc.net.tw/a", "2026-10-28T10:00:00+08:00", publisher="EBC"),
                _item(
                    "友達與群創同步",
                    "https://www.cna.com.tw/news/afe/1",
                    "2026-10-27T09:00:00+08:00",
                    origin="searxng",
                    query="面板",
                    publisher="中央社",
                ),
                _item(
                    "友達與群創同步！",
                    "https://tw.stock.yahoo.com/copy",
                    "2026-10-28T18:00:00+08:00",
                    summary="轉載",
                ),
            ),
            PACK,
        )
        self.assertEqual([item.title for item in news.kept], ["友達與群創同步"])
        kept = news.kept[0]
        self.assertEqual(kept.mentioned_ids, ("2409", "3481"))
        self.assertFalse(kept.theme_only)
        self.assertEqual(kept.keep_reason, "時間內、允許網域、對上股名、保留較早來源")
        self.assertEqual(kept.url, "https://www.cna.com.tw/news/afe/1")
        self.assertEqual(
            [item.drop_reason for item in news.dropped],
            [DROP_OUTSIDE, DROP_NO_TIME, DROP_DOMAIN, DROP_DUP],
        )

    def test_ignores_preliminary_ids_when_unnamed(self) -> None:
        news = filter_raw(
            _raw(
                _item(
                    "面板報價上修",
                    "https://tw.stock.yahoo.com/theme",
                    "2026-10-28T12:00:00+08:00",
                    summary="未點名個股，只有 3481",
                    matched_ids=["3481"],
                )
            ),
            PACK,
        )
        self.assertEqual(len(news.kept), 1)
        self.assertEqual(news.kept[0].mentioned_ids, ())
        self.assertTrue(news.kept[0].theme_only)
        self.assertEqual(news.kept[0].keep_reason, "時間內、允許網域、題材稿未點名")
        self.assertEqual(news.dropped, ())

    def test_equal_time_prefers_wire_over_yahoo(self) -> None:
        stamp = "2026-10-28T11:00:00+08:00"
        news = filter_raw(
            _raw(
                _item("群創法說", "https://tw.stock.yahoo.com/a", stamp),
                _item(
                    "群創法說",
                    "https://news.cnyes.com/news/id/1",
                    stamp,
                    origin="searxng",
                    query="面板",
                    publisher="鉅亨",
                ),
            ),
            PACK,
        )
        self.assertEqual(news.kept[0].publisher, "鉅亨")
        self.assertEqual(news.dropped[0].url, "https://tw.stock.yahoo.com/a")
        self.assertEqual(news.dropped[0].drop_reason, DROP_DUP)

    def test_same_url_merges_and_query_strings_do_not(self) -> None:
        news = filter_raw(
            _raw(
                _item(
                    "友達公告",
                    "https://mops.twse.com.tw/mops/web/t05st01?id=2409",
                    "2026-10-28T08:00:00+08:00",
                ),
                _item(
                    "另一則公告",
                    "https://mops.twse.com.tw/mops/web/t05st01?id=3481",
                    "2026-10-28T09:00:00+08:00",
                ),
                _item(
                    "友達公告轉載",
                    "https://WWW.MOPS.twse.com.tw/mops/web/t05st01?id=2409",
                    "2026-10-28T10:00:00+08:00",
                ),
            ),
            PACK,
        )
        self.assertEqual([item.title for item in news.kept], ["友達公告", "另一則公告"])
        self.assertEqual(news.dropped[0].title, "友達公告轉載")

    def test_allowed_domains(self) -> None:
        urls = (
            "https://mops.twse.com.tw/a",
            "https://mopsov.twse.com.tw/a",
            "https://www.cna.com.tw/a",
            "https://money.udn.com/a",
            "https://ctee.com.tw/a",
            "https://news.cnyes.com/a",
            "https://www.moneydj.com/a",
            "https://tw.stock.yahoo.com/a",
            "https://tw.news.yahoo.com/a",
            "https://finance.yahoo.com/a",
        )
        items = [
            _item(f"群創稿{index}", url, "2026-10-28T10:00:00+08:00")
            for index, url in enumerate(urls)
        ]
        news = filter_raw(_raw(*items), PACK)
        self.assertEqual(len(news.kept), len(urls))
        self.assertEqual(news.dropped, ())
        blocked = filter_raw(
            _raw(_item("中時稿", "https://www.chinatimes.com/a", "2026-10-28T10:00:00+08:00")),
            PACK,
        )
        self.assertEqual(blocked.dropped[0].drop_reason, DROP_DOMAIN)

    def test_stale_copy_does_not_mark_the_fresh_item_as_duplicate(self) -> None:
        news = filter_raw(
            _raw(
                _item("群創快訊", "https://ctee.com.tw/old", "2026-10-26T08:00:00+08:00"),
                _item("群創快訊", "https://ctee.com.tw/new", "2026-10-28T08:00:00+08:00"),
            ),
            PACK,
        )
        self.assertEqual(len(news.kept), 1)
        self.assertNotIn("保留較早來源", news.kept[0].keep_reason)
        self.assertEqual(news.dropped[0].drop_reason, DROP_OUTSIDE)

    def test_write_roundtrip(self) -> None:
        news = filter_raw(
            _raw(_item("群創", "https://tw.stock.yahoo.com/a", "2026-10-28T10:00:00+08:00")),
            PACK,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kept_news(news, Path(tmp) / "panel" / "2026-10-28_kept.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["kept"][0]["mentioned_ids"], ["3481"])
        self.assertFalse(payload["kept"][0]["theme_only"])
        self.assertEqual(payload["dropped"], [])


if __name__ == "__main__":
    unittest.main()
