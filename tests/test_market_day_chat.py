"""Eval / unit tests for market-daily grounded chat (Phase 2)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".agents" / "skills" / "market-daily"
sys.path.insert(0, str(SKILL))

from market_day_chat import (  # noqa: E402
    DISCLAIMER,
    answer_market_day_chat,
    build_chat_context,
    build_entry_template_reply,
    build_factual_slot_reply,
    build_position_template_reply,
    classify_intent,
    format_sse,
    get_tavily_quota_used,
    iter_market_day_chat_events,
    load_us_tech_news_bullets,
    match_factual_slot,
    prepare_market_day_chat,
    resolve_external_news,
)


SAMPLE_FACTS = {
    "trade_date": "2026-08-03",
    "for_session": "2026-08-04",
    "bias_hint": "bullish",
    "market": {"close": 43386.41, "day_return_pct": 0.62},
    "volume": {"vs_avg5_ratio": 0.86, "regime": "normal"},
    "institutional": {
        "available": True,
        "consensus": "mixed",
        "consensus_label": "三大法人方向分歧",
        "foreign_net": -19190915634,
        "trust_net": 23324680573,
        "dealer_net": -20653372342,
        "unit": "twd",
    },
    "technical": {
        "vs_ma5": "above",
        "vs_ma20": "below",
        "ma5": 41616.4,
        "ma20": 43945.05,
    },
    "tsmc": {"available": True, "close": 2370.0, "day_return_pct": -2.27},
    "us": {
        "available": True,
        "as_of": "2026-08-03",
        "indices": {
            "IXIC": {"day_return_pct": 1.46},
            "SOX": {"day_return_pct": -0.57},
        },
        "alignment": {"ixic_vs_taiex": "一致", "sox_vs_tsmc": "一致"},
    },
    "anchors": [
        "大盤日報酬 0.62%",
        "外資淨額 -191.91 億元",
        "開盤偏誤提示 偏多",
    ],
}

SAMPLE_SUMMARY = {
    "trade_date": "2026-08-03",
    "for_session": "2026-08-04",
    "bias_hint": "bullish",
    "bias": "基準情境偏多，開盤需量價確認；尾部風險為外資續賣。",
    "dashboard": "觀察開盤量能 → 若放大 → 較支持基準情境",
    "institutional": SAMPLE_FACTS["institutional"],
}


class TestClassifyIntent(unittest.TestCase):
    def test_entry(self) -> None:
        self.assertEqual(classify_intent("明天是否進場"), "entry_advice")
        self.assertEqual(classify_intent("該不該買"), "entry_advice")

    def test_factual(self) -> None:
        self.assertEqual(classify_intent("今天外資怎麼做？"), "factual_qa")
        self.assertEqual(classify_intent("明天開盤偏誤？"), "factual_qa")

    def test_external_news(self) -> None:
        self.assertEqual(classify_intent("某某新聞剛出？"), "external_news")
        self.assertEqual(classify_intent("幫我搜尋盤後消息"), "external_news")

    def test_position(self) -> None:
        self.assertEqual(classify_intent("我的部位怎麼辦"), "position_advice")


class TestFactualSlots(unittest.TestCase):
    def test_match_slots(self) -> None:
        self.assertEqual(match_factual_slot("今天外資怎麼做？"), "foreign")
        self.assertEqual(match_factual_slot("明天開盤偏誤？"), "bias")
        self.assertEqual(match_factual_slot("量能如何"), "volume")
        self.assertEqual(match_factual_slot("台積電怎麼了"), "tsmc")
        self.assertEqual(match_factual_slot("那指費半"), "us")
        self.assertIsNone(match_factual_slot("隨便聊聊天氣"))

    def test_foreign_template_preserves_direction(self) -> None:
        reply = build_factual_slot_reply(
            "foreign", facts=SAMPLE_FACTS, summary=SAMPLE_SUMMARY
        )
        self.assertIn("賣超", reply)
        self.assertIn("-191.91 億元", reply)
        self.assertIn(DISCLAIMER, reply)
        # Must not claim foreign net is 買超 when negative.
        self.assertRegex(reply, r"外資淨額：.*賣超")

    def test_bias_template(self) -> None:
        reply = build_factual_slot_reply(
            "bias", facts=SAMPLE_FACTS, summary=SAMPLE_SUMMARY
        )
        self.assertIn("偏多", reply)
        self.assertIn("基準情境偏多", reply)

    def test_answer_foreign_without_ollama(self) -> None:
        result = answer_market_day_chat(
            message="今天外資怎麼做？",
            facts=SAMPLE_FACTS,
            summary=SAMPLE_SUMMARY,
            use_llm=False,
        )
        self.assertEqual(result["intent"], "factual_qa")
        self.assertEqual(result["source"], "facts_template")
        self.assertEqual(result["factual_slot"], "foreign")
        self.assertIn("brief", result["sources_used"])
        self.assertIn("賣超", result["reply"])

    def test_answer_bias_without_ollama(self) -> None:
        result = answer_market_day_chat(
            message="明天開盤偏誤？",
            facts=SAMPLE_FACTS,
            summary=SAMPLE_SUMMARY,
            use_llm=False,
        )
        self.assertEqual(result["source"], "facts_template")
        self.assertEqual(result["bias_hint"], "bullish")
        self.assertIn("偏多", result["reply"])


class TestContextAndTemplates(unittest.TestCase):
    def test_context_contains_foreign_and_bias(self) -> None:
        ctx = build_chat_context(SAMPLE_FACTS, SAMPLE_SUMMARY, "# brief\n內容")
        self.assertIn("foreign_net", ctx)
        self.assertIn("bullish", ctx)

    def test_entry_no_holdings_rejects_binary(self) -> None:
        reply = build_entry_template_reply(
            facts=SAMPLE_FACTS,
            summary=SAMPLE_SUMMARY,
            has_holdings=False,
        )
        self.assertIn("無法替你決定", reply)
        for banned in ("建議買進", "明天買", "可以開倉"):
            self.assertNotIn(banned, reply)


class TestHoldingsPosition(unittest.TestCase):
    def test_empty_holdings(self) -> None:
        reply = build_position_template_reply(
            facts=SAMPLE_FACTS, summary=SAMPLE_SUMMARY, holdings=[]
        )
        self.assertIn("沒有登記持股", reply)
        self.assertNotIn("建議買進", reply)

    def test_with_holdings(self) -> None:
        result = answer_market_day_chat(
            message="我的部位怎麼辦",
            facts=SAMPLE_FACTS,
            summary=SAMPLE_SUMMARY,
            holdings=[
                {"stock_id": "2330", "share_count": 1000, "avg_cost": 800.0},
            ],
            use_llm=False,
        )
        self.assertEqual(result["intent"], "position_advice")
        self.assertIn("2330", result["reply"])
        self.assertIn("holdings", result["sources_used"])
        self.assertNotIn("建議買進", result["reply"])
        self.assertNotIn("建議加碼", result["reply"])


class TestExternalNews(unittest.TestCase):
    def test_rss_from_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "report_date": "2026-08-03",
                "items": [
                    {
                        "feed_id": "tech",
                        "feed_name": "Tech",
                        "category": "tech",
                        "title": "Nasdaq rises on semiconductor strength",
                        "link": "https://example.com/a",
                        "published": "2026-08-03 10:00",
                        "summary": "SOX higher",
                    }
                ],
            }
            (root / "2026-08-03_raw.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            bullets, used = load_us_tech_news_bullets(
                "2026-08-03", query="半導體新聞", root=root
            )
            self.assertEqual(used, "2026-08-03")
            self.assertTrue(any("Nasdaq" in b["title"] for b in bullets))

            result = answer_market_day_chat(
                message="某某新聞剛出？",
                facts=SAMPLE_FACTS,
                summary=SAMPLE_SUMMARY,
                use_llm=False,
                skip_tavily=True,
                us_tech_root=root,
            )
            self.assertEqual(result["intent"], "external_news")
            self.assertIn("rss", result["sources_used"])
            self.assertIn("Nasdaq", result["reply"])
            self.assertNotIn("tavily", result["sources_used"])

    def test_no_key_skips_tavily(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {k: v for k, v in os.environ.items() if k != "TAVILY_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                reply, sources, bullets = resolve_external_news(
                    message="最新突發新聞",
                    facts=SAMPLE_FACTS,
                    summary=SAMPLE_SUMMARY,
                    trade_date="2026-08-03",
                    skip_tavily=False,
                    us_tech_root=root,
                )
            self.assertIn("brief", sources)
            self.assertNotIn("tavily", sources)
            self.assertEqual(bullets, [])
            self.assertIn("沒有可用的外訊", reply)

    def test_tavily_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            quota = Path(tmp)
            with patch.dict(
                os.environ,
                {"TAVILY_API_KEY": "test-key", "TAVILY_DAILY_LIMIT": "1"},
                clear=False,
            ):
                calls = {"n": 0}

                def fake_search(query: str):
                    calls["n"] += 1
                    return [
                        {
                            "title": f"Hit {calls['n']} for {query}",
                            "link": "https://example.com",
                            "published": "",
                            "feed": "tavily",
                            "snippet": "x",
                        }
                    ]

                empty_rss = Path(tmp) / "rss"
                empty_rss.mkdir()
                reply1, sources1, _ = resolve_external_news(
                    message="突發新聞 ABC",
                    facts=SAMPLE_FACTS,
                    summary=SAMPLE_SUMMARY,
                    trade_date="2026-08-03",
                    us_tech_root=empty_rss,
                    quota_dir=quota,
                    tavily_search=fake_search,
                )
                self.assertIn("tavily", sources1)
                self.assertIn("Hit 1", reply1)
                self.assertEqual(get_tavily_quota_used(quota_dir=quota), 1)

                reply2, sources2, _ = resolve_external_news(
                    message="突發新聞 DEF",
                    facts=SAMPLE_FACTS,
                    summary=SAMPLE_SUMMARY,
                    trade_date="2026-08-03",
                    us_tech_root=empty_rss,
                    quota_dir=quota,
                    tavily_search=fake_search,
                )
                self.assertNotIn("tavily", sources2)
                self.assertIn("額度已用完", reply2)
                self.assertEqual(calls["n"], 1)


class TestAnswerEntry(unittest.TestCase):
    def test_entry_path(self) -> None:
        result = answer_market_day_chat(
            message="明天是否進場",
            facts=SAMPLE_FACTS,
            summary=SAMPLE_SUMMARY,
            has_holdings=False,
            use_llm=False,
        )
        self.assertEqual(result["intent"], "entry_advice")
        self.assertEqual(result["source"], "template")
        self.assertIn("無法替你決定", result["reply"])


class TestStreaming(unittest.TestCase):
    def test_format_sse(self) -> None:
        raw = format_sse("token", {"text": "你好"})
        self.assertIn("event: token\n", raw)
        self.assertIn('"text": "你好"', raw)
        self.assertTrue(raw.endswith("\n\n"))

    def test_template_stream_events(self) -> None:
        events = list(
            iter_market_day_chat_events(
                message="今天外資怎麼做？",
                facts=SAMPLE_FACTS,
                summary=SAMPLE_SUMMARY,
                use_llm=False,
            )
        )
        names = [name for name, _ in events]
        self.assertEqual(names[0], "meta")
        self.assertIn("token", names)
        self.assertEqual(names[-1], "done")
        meta = events[0][1]
        self.assertEqual(meta["intent"], "factual_qa")
        self.assertEqual(meta["factual_slot"], "foreign")
        done = events[-1][1]
        self.assertIn("賣超", done["reply"])
        joined = "".join(p["text"] for n, p in events if n == "token")
        self.assertEqual(joined, done["reply"])

    def test_prepare_ollama_mode_for_open_question(self) -> None:
        plan = prepare_market_day_chat(
            message="這份 brief 整體你怎麼解讀盤勢結構？",
            facts=SAMPLE_FACTS,
            summary=SAMPLE_SUMMARY,
            use_llm=True,
        )
        self.assertEqual(plan["mode"], "ollama")
        self.assertTrue(plan["messages"])
        self.assertEqual(plan["meta"]["intent"], "factual_qa")

    def test_ollama_stream_tokens(self) -> None:
        def fake_iter(messages, **kwargs):
            yield "盤"
            yield "勢"
            yield "偏多"

        with patch(
            "market_day_chat.iter_ollama_chat",
            side_effect=lambda *a, **k: fake_iter(*a, **k),
        ):
            events = list(
                iter_market_day_chat_events(
                    message="這份 brief 整體你怎麼解讀盤勢結構？",
                    facts=SAMPLE_FACTS,
                    summary=SAMPLE_SUMMARY,
                    use_llm=True,
                )
            )
        names = [n for n, _ in events]
        self.assertEqual(names[0], "meta")
        self.assertEqual(events[0][1]["source"], "ollama")
        tokens = [p["text"] for n, p in events if n == "token"]
        self.assertTrue("".join(tokens).startswith("盤勢偏多"))
        self.assertEqual(names[-1], "done")
        self.assertIn(DISCLAIMER, events[-1][1]["reply"])


if __name__ == "__main__":
    unittest.main()
