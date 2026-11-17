"""Tests for the 17:30 close report window, facts, and validation."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".agents" / "skills" / "market-close"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "market-daily"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "tw-stock-report"))

from close_signals import (  # noqa: E402
    assemble_close_facts,
    collect_close_news,
    leader_fact,
)
from close_window import (  # noqa: E402
    CUTOVER_HOUR,
    CUTOVER_MINUTE,
    TAIPEI_TZ,
    resolve_close_trade_date,
    resolve_close_window,
)
from validate_close import validate_close_report  # noqa: E402


def _weekday_trading(day: dt.date, *, session=None) -> bool:
    return day.weekday() < 5


def _prev_weekday(trade_date: str, *, session=None) -> str | None:
    day = dt.date.fromisoformat(trade_date) - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat()


def _fetch_weekdays(end_date: dt.date, lookback_days: int = 90, *, session=None):
    start = end_date - dt.timedelta(days=lookback_days)
    out = []
    cur = start
    while cur <= end_date:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return out


def _window(trade_date: str = "2026-07-31"):
    with patch("twse_calendar.fetch_trading_dates", side_effect=_fetch_weekdays):
        return resolve_close_window(trade_date=trade_date)


def _facts(**overrides):
    window = _window()
    taiex = {}
    for day in window.lookback_days:
        taiex[day] = {
            "open": 22000,
            "max": 22300,
            "min": 21900,
            "close": 22000,
            "Trading_Volume": 1000,
        }
    taiex["2026-07-31"] = {
        "open": 22100,
        "max": 22500,
        "min": 22000,
        "close": 22400,
        "Trading_Volume": 1500,
    }
    taiex["2026-07-30"]["close"] = 22000
    leaders = [
        leader_fact("2330", "台積電", close=1000, day_return_pct=1.2, foreign_net_lots=1200, trust_net_lots=-30, dealer_net_lots=10),
        leader_fact("2317", "鴻海", close=200, day_return_pct=-0.4),
        leader_fact("2454", "聯發科", close=1500, day_return_pct=0.2, foreign_net_lots=-500, trust_net_lots=20, dealer_net_lots=5),
        leader_fact("2308", "台達電", close=300, day_return_pct=0.1, foreign_net_lots=80, trust_net_lots=1, dealer_net_lots=-2),
        leader_fact("2382", "廣達", close=400, day_return_pct=2.0, foreign_net_lots=300, trust_net_lots=15, dealer_net_lots=4),
        leader_fact("2881", "富邦金", close=90, day_return_pct=-0.2, foreign_net_lots=-40, trust_net_lots=3, dealer_net_lots=1),
        leader_fact("2882", "國泰金", close=70, day_return_pct=0.3, foreign_net_lots=25, trust_net_lots=-4, dealer_net_lots=2),
    ]
    news = [{"title": "台積電收盤上漲", "date": "2026-07-31", "stock_id": "2330", "name": "台積電", "publisher": "中央社"}]
    facts = assemble_close_facts(
        window,
        taiex_rows=taiex,
        institutional_rows=[
            {"date": "2026-07-31", "name": "Foreign_Investor", "buy": 5_000_000_000, "sell": 1_000_000_000},
            {"date": "2026-07-31", "name": "Investment_Trust", "buy": 200_000_000, "sell": 100_000_000},
            {"date": "2026-07-31", "name": "Dealer_self", "buy": 50_000_000, "sell": 20_000_000},
        ],
        leaders=leaders,
        news_items=news,
    )
    facts.update(overrides)
    return facts


class TestCloseWindow(unittest.TestCase):
    def test_before_1730_uses_prior(self) -> None:
        now = dt.datetime(2026, 7, 31, 17, 29, tzinfo=TAIPEI_TZ)
        with (
            patch("twse_calendar.is_trading_day", side_effect=_weekday_trading),
            patch("twse_calendar.previous_trading_day", side_effect=_prev_weekday),
        ):
            day, applied = resolve_close_trade_date(now)
        self.assertEqual(day.isoformat(), "2026-07-30")
        self.assertTrue(applied)
        self.assertEqual(CUTOVER_HOUR, 17)
        self.assertEqual(CUTOVER_MINUTE, 30)

    def test_at_1730_uses_today(self) -> None:
        now = dt.datetime(2026, 7, 31, 17, 30, tzinfo=TAIPEI_TZ)
        with patch("twse_calendar.is_trading_day", side_effect=_weekday_trading):
            day, applied = resolve_close_trade_date(now)
        self.assertEqual(day.isoformat(), "2026-07-31")
        self.assertTrue(applied)

    def test_forced_date_skips_cutover(self) -> None:
        window = _window()
        self.assertEqual(window.trade_date, "2026-07-31")
        self.assertFalse(window.cutover_applied)
        self.assertEqual(window.as_dict()["cutover"], "17:30")
        self.assertNotIn("for_session", window.as_dict())


class TestCloseFacts(unittest.TestCase):
    def test_unpublished_leader_has_no_lots(self) -> None:
        facts = _facts()
        by_id = {item["stock_id"]: item for item in facts["leaders"]}
        self.assertEqual(by_id["2317"]["institutional_status"], "unpublished")
        self.assertIsNone(by_id["2317"]["foreign_net_lots"])
        self.assertEqual(by_id["2330"]["institutional_status"], "published")
        self.assertEqual(by_id["2330"]["foreign_net_lots"], 1200)
        self.assertTrue(facts["market"]["available"])
        self.assertEqual(facts["market"]["day_return_pct"], 1.82)
        self.assertTrue(facts["institutional"]["available"])
        self.assertEqual(facts["institutional"]["unit"], "twd")

    def test_news_keeps_only_the_report_day(self) -> None:
        taipei = dt.timezone(dt.timedelta(hours=8))
        articles = [
            SimpleNamespace(title="今天這則", publisher="中央社", published=dt.datetime(2026, 7, 31, 15, 0, tzinfo=taipei)),
            SimpleNamespace(title="昨天那則", publisher="中央社", published=dt.datetime(2026, 7, 30, 18, 0, tzinfo=taipei)),
            SimpleNamespace(title="沒有時間", publisher="中央社", published=None),
        ]
        client = SimpleNamespace(fetch_articles=lambda stock_id: articles if stock_id == "2330" else [])
        items = collect_close_news("2026-07-31", client=client)
        titles = [item["title"] for item in items]
        self.assertEqual(titles, ["今天這則"])


class TestCloseValidation(unittest.TestCase):
    def test_passes_when_unpublished_is_named(self) -> None:
        facts = _facts()
        body = (
            "## 一、今日收盤\n"
            "加權日報酬 1.82%，收在當日區間偏高。量能相對五日均量放大。"
            "三大法人一致買超，外資買超約 40 億元。\n\n"
            "## 二、權值法人\n"
            "台積電外資 1200 張、投信 -30 張、自營 10 張。"
            "鴻海法人尚未公布。"
            "聯發科外資 -500 張、投信 20 張、自營 5 張。"
            "台達電外資 80 張、投信 1 張、自營 -2 張。"
            "廣達外資 300 張、投信 15 張、自營 4 張。"
            "富邦金外資 -40 張、投信 3 張、自營 1 張。"
            "國泰金外資 25 張、投信 -4 張、自營 2 張。"
            "今天的拉抬主要在台積電與廣達，鴻海沒有法人數字就不能拿來解釋。\n\n"
            "## 三、新聞怎麼對上\n"
            "台積電收盤上漲這則標題和加權上漲的方向一致，只說明有收盤稿，不另外加事件。\n\n"
            "## 四、免責聲明\n"
            "僅供參考，不構成投資建議。\n"
        )
        result = validate_close_report(body, facts)
        self.assertTrue(result.passed, result.summary_lines())

    def test_rejects_open_brief_language_and_margin(self) -> None:
        facts = _facts()
        body = "明日開盤偏誤，而且融資增加。今日收盤。權值。新聞。免責。" + ("補字" * 80)
        result = validate_close_report(body, facts)
        codes = {issue.code for issue in result.issues}
        self.assertIn("forbidden_phrase", codes)
        self.assertIn("out_of_scope", codes)
        self.assertFalse(result.passed)


class TestCloseNarrativeSkill(unittest.TestCase):
    def test_prompt_inlines_skill_body_and_facts(self) -> None:
        from close_prompts import build_close_prompt

        prompt = build_close_prompt(facts_summary="加權 +10")
        self.assertIn("## 一、今日收盤", prompt)
        self.assertIn("不可寫「明日開盤偏誤」", prompt)
        self.assertIn(
            "======= FACTS（唯讀）=======\n加權 +10\n======= END FACTS =======",
            prompt,
        )
        self.assertNotIn("name: close-narrative", prompt)
        self.assertNotIn("# 收盤日報正文", prompt)


if __name__ == "__main__":
    unittest.main()
