"""Unit and evaluation tests for single stock report grounded chat."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".agents" / "skills" / "report-gate"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_report_chat import (
    DISCLAIMER,
    answer_stock_report_chat,
    build_compact_stock_facts,
    build_entry_template_reply,
    build_factual_slot_reply,
    build_fundamental_reply,
    build_glossary_reply,
    build_inference_reply,
    build_news_grounded_reply,
    build_out_of_scope_reply,
    build_pnl_reply,
    build_reading_reply,
    build_position_template_reply,
    classify_intent,
    expand_followup,
    extract_report_news,
    format_sse,
    iter_stock_report_chat_events,
    match_factual_slot,
    match_glossary_term,
    match_inference_topic,
    match_news_grounded_topic,
    match_out_of_scope_topic,
    parse_user_lot_claim,
    prepare_stock_report_chat,
    reading_reply_grounded,
)
from agent.tools.report import answer_report_chat

SAMPLE_STOCK_FACTS = {
    "stock_id": "2330",
    "stock_name": "台積電",
    "trade_date": "2026-09-09",
    "foreign_net_lots": 8500,
    "trust_net_lots": 1200,
    "dealer_net_lots": -500,
    "major_net_lots": 6200,
    "major_available": True,
    "major_cum_lots": 18500,
    "foreign_cum_lots": 24000,
    "foreign_streak_dir": 1,
    "foreign_streak_days": 3,
    "today_change_pct": 1.5,
    "close_vs_ma5_pct": 1.2,
    "close_vs_ma10_pct": 2.1,
    "close_vs_ma20_pct": 3.4,
    "ma_stack": "bullish_stack",
    "ma_alignment": "bullish",
    "ma20_slope": "rising",
    "ma20_slope_pct": 1.8,
    "ma5_cross_ma10": "golden_cross",
    "rsi_14": 65.4,
    "rsi_zone": "neutral",
    "atr_14": 18.5,
    "atr_pct": 1.85,
    "volatility_regime": "normal",
    "margin_today_delta_lots": -350,
    "margin_delta_lots": -1200,
    "short_delta_lots": 50,
    "margin_short_ratio_pct": 8.5,
    "margin_short_ratio_zone": "normal",
    "margin_momentum_pct": -4.2,
    "margin_momentum": "decelerating",
    "margin_short_regime": "margin_down_short_flat",
    "day_trade_ratio_pct": 22.5,
    "avg_day_trade_ratio_pct": 25.0,
    "day_trade_intensity": "normal",
    "borrow_sell_today_lots": 1200,
    "borrow_pressure": "low",
    "high_20d": 1025.0,
    "low_20d": 940.0,
    "dist_to_20d_high_pct": -1.5,
    "dist_to_20d_low_pct": 6.8,
    "breakout_20d_high": False,
    "breakdown_20d_low": False,
    "range_position": "upper_third",
    "chip_regime": "accumulation",
    "institutional_consensus": "strong_buy",
    "divergences": [],
    "volume_price_divergence": "confirming_up",
}


class TestStockReportChatIntent(unittest.TestCase):
    def test_classify_entry_advice(self) -> None:
        self.assertEqual(classify_intent("台積電明天可以買嗎？"), "entry_advice")
        self.assertEqual(classify_intent("現在該不該進場？"), "entry_advice")
        self.assertEqual(classify_intent("可以追高建倉嗎？"), "entry_advice")

    def test_classify_position_advice(self) -> None:
        self.assertEqual(classify_intent("我的部位怎麼辦？"), "position_advice")
        self.assertEqual(classify_intent("我買在980被套牢要停損嗎"), "position_advice")
        self.assertEqual(classify_intent("這檔持股要不要賣出？"), "position_advice")
        # Follow-up after facts: action language must beat the 外資 keyword.
        self.assertEqual(
            classify_intent("在外資賣了21萬張，你覺得接下來該怎麼處理？"),
            "position_advice",
        )
        self.assertEqual(
            classify_intent("外資大賣之後持股該怎麼辦"),
            "position_advice",
        )
        # Hold vs small-loss exit must not fall into open_qa.
        self.assertEqual(
            classify_intent("那你建議我應該是要持有，還是可以考慮小損出場？"),
            "position_advice",
        )
        self.assertEqual(
            classify_intent("要繼續持有還是認賠出場？"),
            "position_advice",
        )
        self.assertEqual(
            classify_intent("再幫我看一下目前自己獲利多少？"),
            "position_advice",
        )
        self.assertEqual(
            classify_intent("在我幫忙看一下目前自己獲利多少?"),
            "position_advice",
        )
        self.assertEqual(
            classify_intent("我的未實現損益多少"),
            "position_advice",
        )

    def test_classify_glossary(self) -> None:
        self.assertEqual(classify_intent("什麼是借券賣出？跟融券差在哪"), "glossary")
        self.assertEqual(classify_intent("當沖比是什麼意思？"), "glossary")
        self.assertEqual(classify_intent("什麼是均線多頭排列？"), "glossary")
        self.assertEqual(match_glossary_term("什麼是借券賣出"), "borrow_vs_short")
        self.assertEqual(match_glossary_term("當沖比是什麼意思"), "day_trade")

    def test_classify_factual_slot(self) -> None:
        self.assertEqual(classify_intent("今天外資買超多少？"), "factual_qa")
        self.assertEqual(match_factual_slot("今天外資買超多少？"), "institutional")
        self.assertEqual(classify_intent("今天主力進出如何？"), "factual_qa")
        self.assertEqual(match_factual_slot("今天主力進出如何？"), "major")
        self.assertEqual(classify_intent("融資餘額與融券增減多少"), "factual_qa")
        self.assertEqual(match_factual_slot("融資餘額與融券增減多少"), "margin_short")
        self.assertEqual(classify_intent("目前 RSI 多少？"), "factual_qa")
        self.assertEqual(match_factual_slot("目前 RSI 多少？"), "rsi")
        self.assertEqual(classify_intent("ATR 波動度數值是多少"), "factual_qa")
        self.assertEqual(match_factual_slot("ATR 波動度數值是多少"), "atr")
        self.assertEqual(classify_intent("近20日高低點與壓力在哪"), "factual_qa")
        self.assertEqual(match_factual_slot("近20日高低點與壓力在哪"), "price_range")

    def test_classify_inference(self) -> None:
        self.assertEqual(
            classify_intent("所以你認為外資會繼續賣？"),
            "inference",
        )
        self.assertEqual(
            classify_intent("外資會不會繼續賣超？"),
            "inference",
        )
        self.assertEqual(
            classify_intent("主力還會不會買？"),
            "inference",
        )
        # 「你覺得」+ 預測語 → inference，不可被 action_override 搶成部位模板
        self.assertEqual(
            classify_intent("今天外資賣了2.1萬張，你覺得他們明天會繼續賣嗎？"),
            "inference",
        )
        self.assertEqual(
            classify_intent("在外資賣了21萬張，你覺得接下來該怎麼處理？"),
            "position_advice",
        )
        self.assertEqual(match_inference_topic("所以你認為外資會繼續賣？"), "foreign_continue")
        # Pure facts questions must stay factual.
        self.assertEqual(classify_intent("今天外資買超多少？"), "factual_qa")

    def test_classify_reading(self) -> None:
        self.assertEqual(
            classify_intent("為什麼外資在賣、價格還在月線上？"),
            "reading",
        )
        self.assertEqual(classify_intent("外資賣超又在月線上代表什麼"), "reading")
        self.assertEqual(classify_intent("這檔現在風險在哪"), "reading")
        self.assertEqual(
            classify_intent("這份報告評估的主要總體風險是什麼？"),
            "reading",
        )
        # Forecasts, definitions, lot counts, valuation, and news stay put.
        self.assertEqual(classify_intent("今天外資買超多少？"), "factual_qa")
        self.assertEqual(classify_intent("所以你認為外資會繼續賣？"), "inference")
        self.assertEqual(classify_intent("為什麼外資會繼續賣？"), "inference")
        self.assertEqual(classify_intent("什麼是借券賣出？"), "glossary")
        self.assertEqual(classify_intent("本益比為什麼偏高"), "fundamental_qa")
        self.assertEqual(classify_intent("九月的營收已經出來了嗎？"), "news_qa")
        self.assertEqual(
            classify_intent("再幫我看一下目前自己獲利多少？"),
            "position_advice",
        )

    def test_followup_ellipsis_inherits_prior_topic(self) -> None:
        prior_lots = [{"role": "user", "content": "今天外資買超多少？"}]
        self.assertEqual(
            expand_followup("那主力呢？", prior_lots),
            "那主力呢？ 多少",
        )
        plan = prepare_stock_report_chat(
            stock_id="2330",
            message="那主力呢？",
            facts=SAMPLE_STOCK_FACTS,
            history=prior_lots,
            use_llm=False,
        )
        self.assertEqual(plan["result"]["intent"], "factual_qa")
        self.assertIn("主力", plan["result"]["reply"])
        self.assertIn("6,200", plan["result"]["reply"])

        prior_read = [{"role": "user", "content": "為什麼外資在賣、價格還在月線上？"}]
        self.assertIn("外資", expand_followup("那這個代表什麼", prior_read))
        self.assertIn("月線", expand_followup("那這個代表什麼", prior_read))
        read_plan = prepare_stock_report_chat(
            stock_id="2330",
            message="那這個代表什麼",
            facts=SAMPLE_STOCK_FACTS,
            history=prior_read,
            use_llm=False,
        )
        self.assertEqual(read_plan["result"]["intent"], "reading")
        self.assertIn("外資", read_plan["result"]["reply"])
        self.assertIn("月線", read_plan["result"]["reply"])
        self.assertNotIn("一起看", read_plan["result"]["reply"])
        self.assertIn("推不出來", read_plan["result"]["reply"])
        why_reply = build_reading_reply(
            SAMPLE_STOCK_FACTS,
            message="為什麼外資在賣、價格還在月線上？",
        )
        self.assertNotEqual(read_plan["result"]["reply"], why_reply)

        selling = {
            **SAMPLE_STOCK_FACTS,
            "foreign_net_lots": -212136,
            "foreign_cum_lots": -83568,
            "close_vs_ma20_pct": 10.49,
        }
        follow = prepare_stock_report_chat(
            stock_id="2409",
            message="那這代表什麼",
            facts=selling,
            history=[{"role": "user", "content": "為什麼外資在賣，價格還在月線上"}],
            use_llm=False,
        )
        self.assertEqual(follow["result"]["intent"], "reading")
        self.assertIn("背離", follow["result"]["reply"])
        self.assertNotIn("一起看", follow["result"]["reply"])
        self.assertNotIn("均線狀態", follow["result"]["reply"])

        # 「代表了什麼」多一個「了」，不能再掉回事實複讀。
        with_le = prepare_stock_report_chat(
            stock_id="2409",
            message="那這代表了什麼?",
            facts=selling,
            history=[{"role": "user", "content": "為什麼外資在賣，價格還在月線上"}],
            use_llm=False,
        )
        self.assertEqual(with_le["result"]["intent"], "reading")
        self.assertIn("背離", with_le["result"]["reply"])
        self.assertNotIn("一起看", with_le["result"]["reply"])

        # Same subject, different wording: explain, do not miss or repeat the fact dump.
        meaning_reply = with_le["result"]["reply"]
        for follow_up, needle in (
            ("所以呢", "背離"),
            ("那又如何", "背離"),
            ("這是什麼意思", "背離"),
            ("聽不太懂", "背離"),
            ("所以價格其實還沒確認賣壓？", "背離"),
            ("嚴重嗎", "還沒到嚴重"),
            ("這正常嗎", "不稀奇"),
            ("那這算好還是壞", "利空"),
        ):
            varied = prepare_stock_report_chat(
                stock_id="2409",
                message=follow_up,
                facts=selling,
                history=[{"role": "user", "content": "為什麼外資在賣，價格還在月線上"}],
                use_llm=False,
            )
            self.assertEqual(varied["result"]["intent"], "reading", follow_up)
            self.assertIn(needle, varied["result"]["reply"], follow_up)
            self.assertNotIn("一起看", varied["result"]["reply"], follow_up)
            self.assertNotIn("對不到", varied["result"]["reply"], follow_up)
        severe = prepare_stock_report_chat(
            stock_id="2409",
            message="嚴重嗎",
            facts=selling,
            history=[{"role": "user", "content": "為什麼外資在賣，價格還在月線上"}],
            use_llm=False,
        )
        self.assertNotEqual(severe["result"]["reply"], meaning_reply)

        switch = prepare_stock_report_chat(
            stock_id="2330",
            message="那主力呢？",
            facts=SAMPLE_STOCK_FACTS,
            history=prior_read,
            use_llm=False,
        )
        self.assertEqual(switch["result"]["intent"], "reading")
        self.assertIn("主力", switch["result"]["reply"])
        self.assertNotIn("月線之上", switch["result"]["reply"])

        # A full question is not rewritten just because history exists.
        self.assertEqual(
            expand_followup("今天外資買超多少？", prior_read),
            "今天外資買超多少？",
        )
        margin = prepare_stock_report_chat(
            stock_id="2330",
            message="那融資呢？",
            facts=SAMPLE_STOCK_FACTS,
            history=prior_read,
            use_llm=False,
        )
        self.assertEqual(margin["result"]["intent"], "reading")
        self.assertIn("融資", margin["result"]["reply"])
        exit_plan = prepare_stock_report_chat(
            stock_id="2409",
            message="那我該出場嗎",
            facts=selling,
            history=[{"role": "user", "content": "為什麼外資在賣，價格還在月線上"}],
            use_llm=False,
        )
        self.assertEqual(exit_plan["result"]["intent"], "position_advice")
        self.assertEqual(
            classify_intent("那你建議我應該是要持有，還是可以考慮小損出場？"),
            "position_advice",
        )

    def test_classify_open_qa(self) -> None:
        self.assertEqual(classify_intent("報告裡的段落標題是怎麼排的？"), "open_qa")

    def test_classify_out_of_scope_revenue(self) -> None:
        self.assertEqual(classify_intent("九月的營收已經出來了嗎？"), "news_qa")
        self.assertEqual(classify_intent("月營收年增多少"), "fundamental_qa")
        self.assertEqual(classify_intent("最新財報 EPS 多少"), "news_qa")
        self.assertEqual(classify_intent("最近有什麼新聞？"), "news_qa")
        self.assertEqual(match_news_grounded_topic("九月的營收已經出來了嗎？"), "月營收／營收公布")
        self.assertEqual(classify_intent("本益比多少"), "fundamental_qa")
        self.assertEqual(classify_intent("殖利率多少"), "fundamental_qa")
        self.assertEqual(classify_intent("現金股利多少"), "fundamental_qa")
        # Chip questions must not be swallowed.
        self.assertEqual(classify_intent("今天外資買超多少？"), "factual_qa")


class TestStockReportFactualSlotReplies(unittest.TestCase):
    def test_institutional_reply(self) -> None:
        reply = build_factual_slot_reply("institutional", SAMPLE_STOCK_FACTS)
        self.assertIn("買超 8,500 張", reply)
        self.assertIn("連續 3 日買", reply)
        self.assertNotIn("【", reply)
        self.assertIn(DISCLAIMER, reply)

    def test_major_reply(self) -> None:
        reply = build_factual_slot_reply("major", SAMPLE_STOCK_FACTS)
        self.assertIn("主力", reply)
        self.assertIn("買超 6,200 張", reply)
        self.assertIn(DISCLAIMER, reply)

    def test_margin_short_reply(self) -> None:
        reply = build_factual_slot_reply("margin_short", SAMPLE_STOCK_FACTS)
        self.assertIn("融資", reply)
        self.assertIn("8.5%", reply)
        self.assertIn(DISCLAIMER, reply)

    def test_day_trade_reply(self) -> None:
        reply = build_factual_slot_reply("day_trade", SAMPLE_STOCK_FACTS)
        self.assertIn("當沖比大約 22.5%", reply)
        self.assertIn(DISCLAIMER, reply)

    def test_technical_and_rsi_atr(self) -> None:
        tech_reply = build_factual_slot_reply("technical", SAMPLE_STOCK_FACTS)
        self.assertIn("多頭排列", tech_reply)
        self.assertNotIn("bullish_stack", tech_reply)
        rsi_reply = build_factual_slot_reply("rsi", SAMPLE_STOCK_FACTS)
        self.assertIn("65.4", rsi_reply)
        atr_reply = build_factual_slot_reply("atr", SAMPLE_STOCK_FACTS)
        self.assertIn("18.50", atr_reply)


class TestStockReportGlossaryAndTemplates(unittest.TestCase):
    def test_glossary_binds_stock_facts(self) -> None:
        reply = build_glossary_reply("borrow_vs_short", SAMPLE_STOCK_FACTS)
        self.assertIn("融券", reply)
        self.assertIn("借券", reply)
        self.assertIn("台積電", reply)
        self.assertIn("1,200", reply)
        self.assertNotIn("【專業名詞解釋", reply)
        self.assertIn(DISCLAIMER, reply)

    def test_entry_template_discipline(self) -> None:
        reply = build_entry_template_reply(SAMPLE_STOCK_FACTS)
        self.assertIn("不會直接說買或不買", reply)
        self.assertIn("1025.0", reply)
        self.assertIn("940.0", reply)
        self.assertNotIn("情境一", reply)
        self.assertIn(DISCLAIMER, reply)

    def test_position_template_scenarios(self) -> None:
        pos_facts = {
            "avg_cost": 950.0,
            "share_count": 2000,
            "unrealized_pnl_pct": 5.2,
        }
        reply = build_position_template_reply(
            SAMPLE_STOCK_FACTS,
            position_facts=pos_facts,
        )
        self.assertIn("成本 950", reply)
        self.assertIn("持有 2,000 股", reply)
        self.assertIn("買超 8,500 張", reply)
        self.assertNotIn("情境一", reply)
        self.assertIn(DISCLAIMER, reply)

    def test_followup_after_foreign_sell_uses_position_template(self) -> None:
        facts = {
            **SAMPLE_STOCK_FACTS,
            "stock_id": "2409",
            "stock_name": "友達",
            "foreign_net_lots": -212136,
            "institutional_consensus": "bearish",
            "chip_regime": "distribution",
        }
        plan = prepare_stock_report_chat(
            stock_id="2409",
            message="在外資賣了21萬張，你覺得接下來該怎麼處理？",
            facts=facts,
            holdings=[{"stock_id": "2409", "share_count": 1000, "avg_cost": 12.5}],
            use_llm=False,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["intent"], "position_advice")
        self.assertIn("21.2 萬張", plan["result"]["reply"])
        self.assertIn("成本 12.5", plan["result"]["reply"])
        self.assertIn("攤平", plan["result"]["reply"])

    def test_hold_vs_exit_uses_position_not_open_fallback(self) -> None:
        facts = {
            **SAMPLE_STOCK_FACTS,
            "stock_id": "2409",
            "stock_name": "友達",
            "foreign_net_lots": -212136,
            "major_net_lots": -245974,
            "institutional_consensus": "bearish",
            "chip_regime": "mixed",
            "low_20d": 26.35,
        }
        q = "那你建議我應該是要持有，還是可以考慮小損出場？"
        plan = prepare_stock_report_chat(
            stock_id="2409",
            message=q,
            facts=facts,
            holdings=[{"stock_id": "2409", "share_count": 132000, "avg_cost": 40.0}],
            use_llm=False,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["intent"], "position_advice")
        self.assertEqual(plan["result"]["source"], "template")
        reply = plan["result"]["reply"]
        self.assertIn("持有或小損出場", reply)
        self.assertIn("21.2 萬張", reply)
        self.assertIn("26.35", reply)
        self.assertNotIn("你比較想問法人", reply)
        self.assertNotIn("我可以再往下講", reply)

    def test_pnl_question_uses_summary_position(self) -> None:
        facts = {
            **SAMPLE_STOCK_FACTS,
            "stock_id": "2409",
            "stock_name": "友達",
        }
        summary = {
            "position": {
                "avg_cost": 19.0,
                "shares": 132000,
                "close_price": 39.05,
                "unrealized_pnl_pct": 105.53,
                "pnl_bucket_label": "大幅獲利（逾 15%）",
            }
        }
        q = "再幫我看一下目前自己獲利多少？"
        plan = prepare_stock_report_chat(
            stock_id="2409",
            message=q,
            facts=facts,
            summary=summary,
            use_llm=True,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["intent"], "position_advice")
        self.assertEqual(plan["result"]["source"], "template")
        reply = plan["result"]["reply"]
        self.assertIn("19", reply)
        self.assertIn("132,000", reply)
        self.assertIn("105.53%", reply)
        self.assertIn("2,646,600", reply)
        self.assertIn("大幅獲利", reply)
        self.assertNotIn("你比較想問法人", reply)

        direct = build_pnl_reply(facts=facts, summary=summary)
        self.assertIn("未實現損益", direct)
        self.assertIn(DISCLAIMER, direct)

    def test_revenue_question_checks_report_news(self) -> None:
        facts = {
            **SAMPLE_STOCK_FACTS,
            "stock_id": "3711",
            "stock_name": "日月光投控",
            "chip_regime": "accumulation",
            "foreign_net_lots": 3398,
            "major_net_lots": 2475,
        }
        summary = {
            "market": {
                "news": [
                    {
                        "date": "2026-10-05",
                        "title": "日月光投控：矽品精密工業向盟立取得營業用機器設備7批",
                        "category": "利多",
                        "summary": "積極擴充先進封裝產能。",
                    },
                    {
                        "date": "2026-10-05",
                        "title": "【公告】代子公司矽品精密工業(股)公司公告取得廠務工程",
                        "category": "利多",
                        "summary": "契約總金額約 40 億元。",
                    },
                ]
            }
        }
        q = "九月的營收已經出來了嗎？"
        plan = prepare_stock_report_chat(
            stock_id="3711",
            message=q,
            facts=facts,
            summary=summary,
            use_llm=True,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["intent"], "news_qa")
        self.assertEqual(plan["result"]["source"], "news")
        reply = plan["result"]["reply"]
        self.assertIn("九月營收", reply)
        self.assertIn("沒看到直接提到", reply)
        self.assertIn("盟立", reply)
        self.assertNotIn("accumulation", reply)
        self.assertNotIn("你比較想問法人", reply)
        self.assertNotIn("3,398", reply)

        # When news actually mentions 營收, quote it.
        summary_hit = {
            "market": {
                "news": [
                    {
                        "date": "2026-10-10",
                        "title": "日月光投控公布九月營收年增 12%",
                        "category": "中性",
                        "summary": "9月合併營收出爐。",
                    }
                ]
            }
        }
        plan2 = prepare_stock_report_chat(
            stock_id="3711",
            message=q,
            facts=facts,
            summary=summary_hit,
            use_llm=False,
        )
        self.assertEqual(plan2["result"]["source"], "news")
        self.assertIn("九月營收", plan2["result"]["reply"])
        self.assertIn("擷取到的新聞", plan2["result"]["reply"])
        self.assertNotIn("沒看到直接提到", plan2["result"]["reply"])

        items = extract_report_news(summary)
        self.assertEqual(len(items), 2)
        reply_direct, source = build_news_grounded_reply(
            facts=facts, summary=summary, message=q
        )
        self.assertEqual(source, "news")
        self.assertIn(DISCLAIMER, reply_direct)

    def test_will_foreign_continue_uses_inference_not_facts_dump(self) -> None:
        facts = {
            **SAMPLE_STOCK_FACTS,
            "stock_id": "2409",
            "stock_name": "友達",
            "foreign_net_lots": -212136,
            "foreign_cum_lots": -83568,
            "foreign_streak_dir": -1,
            "foreign_streak_days": 2,
            "institutional_consensus": "bearish",
            "chip_regime": "distribution",
            "trust_net_lots": 0,
            "dealer_net_lots": -22053,
        }
        plan = prepare_stock_report_chat(
            stock_id="2409",
            message="所以你認為外資會繼續賣？",
            facts=facts,
            use_llm=False,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["intent"], "inference")
        self.assertEqual(plan["result"]["source"], "inference")
        reply = plan["result"]["reply"]
        self.assertTrue(
            ("無法斷言" in reply)
            or ("不會賭" in reply)
            or ("沒辦法預測" in reply)
            or ("不能說" in reply)
            or ("不是報告能預測" in reply)
        )
        self.assertIn("21.2 萬張", reply)
        self.assertIn("一起偏賣", reply)
        self.assertNotIn("bearish", reply)
        self.assertIn("連賣", reply)
        self.assertIn("2 天", reply)
        self.assertNotIn("【友達", reply)
        self.assertNotEqual(
            classify_intent("所以你認為外資會繼續賣？"),
            "factual_qa",
        )

        direct = build_inference_reply("foreign_continue", facts)
        self.assertTrue(
            ("無法斷言" in direct)
            or ("不會賭" in direct)
            or ("沒辦法預測" in direct)
            or ("不能說" in direct)
            or ("不是報告能預測" in direct)
        )
        self.assertIn(DISCLAIMER, direct)

        # With LLM enabled, still grounded via conversational plan + fallback.
        llm_plan = prepare_stock_report_chat(
            stock_id="2409",
            message="所以你認為外資會繼續賣？",
            facts=facts,
            use_llm=True,
        )
        self.assertEqual(llm_plan["mode"], "ollama")
        self.assertTrue(
            ("無法斷言" in llm_plan["fallback_reply"])
            or ("不會賭" in llm_plan["fallback_reply"])
            or ("沒辦法預測" in llm_plan["fallback_reply"])
            or ("不能說" in llm_plan["fallback_reply"])
            or ("不是報告能預測" in llm_plan["fallback_reply"])
        )

        # Same prediction question must not fall into Discipline / position template.
        same_q = "今天外資賣了2.1萬張，你覺得他們明天會繼續賣嗎？"
        plan2 = prepare_stock_report_chat(
            stock_id="2409",
            message=same_q,
            facts=facts,
            use_llm=False,
        )
        self.assertEqual(plan2["result"]["intent"], "inference")
        self.assertNotIn("停損", plan2["result"]["reply"])
        self.assertNotIn("成本", plan2["result"]["reply"])
        # Wrong order-of-magnitude claim must be corrected against report facts.
        self.assertIn("差一個數量級", plan2["result"]["reply"])

        price_q = "那你覺得明天會繼續下跌?還是反彈?"
        price_plan = prepare_stock_report_chat(
            stock_id="2409",
            message=price_q,
            facts=facts,
            use_llm=False,
        )
        price_reply = price_plan["result"]["reply"]
        self.assertEqual(price_plan["result"]["intent"], "inference")
        self.assertIn("繼續跌還是反彈", price_reply)
        self.assertIn("一起偏賣", price_reply)
        self.assertNotIn("bearish", price_reply)
        self.assertNotIn("Inference", price_reply)
        self.assertIn("2.1 萬張", plan2["result"]["reply"])
        self.assertIn("21.2 萬張", plan2["result"]["reply"])
        self.assertIn("前幾天有相反方向", plan2["result"]["reply"])

        # Near-correct claim (21萬 ≈ 21.2萬) should not trigger a correction prefix.
        close_q = "今天外資賣了21萬張，你覺得他們明天會繼續賣嗎？"
        plan3 = prepare_stock_report_chat(
            stock_id="2409",
            message=close_q,
            facts=facts,
            use_llm=False,
        )
        self.assertEqual(plan3["result"]["intent"], "inference")
        self.assertNotIn("差一個數量級", plan3["result"]["reply"])
        self.assertIn("21.2 萬張", plan3["result"]["reply"])

    def test_parse_user_lot_claim(self) -> None:
        self.assertEqual(parse_user_lot_claim("今天外資賣了2.1萬張"), 21000)
        self.assertEqual(parse_user_lot_claim("今天外資賣了21萬張"), 210000)
        self.assertEqual(parse_user_lot_claim("外資賣超 212,136 張"), 212136)
        self.assertIsNone(parse_user_lot_claim("外資會不會繼續賣"))


class TestStockReportChatExecution(unittest.TestCase):
    def test_reading_uses_several_facts_and_locks_sign(self) -> None:
        q = "為什麼外資在賣、價格還在月線上？"
        plan = prepare_stock_report_chat(
            stock_id="2330",
            message=q,
            facts=SAMPLE_STOCK_FACTS,
            use_llm=False,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["intent"], "reading")
        self.assertEqual(plan["result"]["source"], "reading")
        reply = plan["result"]["reply"]
        self.assertIn("先對方向", reply)
        self.assertIn("買超", reply)
        self.assertIn("不是賣超", reply)
        self.assertIn("月線之上", reply)
        self.assertIn("同向偏多", reply)
        self.assertIn(DISCLAIMER, reply)
        self.assertNotIn("停損", reply)

        selling = {
            **SAMPLE_STOCK_FACTS,
            "foreign_net_lots": -212136,
            "foreign_cum_lots": -83568,
            "close_vs_ma20_pct": 1.2,
        }
        direct = build_reading_reply(
            selling,
            message="為什麼外資在賣、價格還在月線上？",
        )
        self.assertIn("賣超", direct)
        self.assertIn("不同步", direct)
        self.assertNotIn("先對方向", direct)

        off = prepare_stock_report_chat(
            stock_id="2330",
            message="為什麼外資賣了2.1萬張，價格還在月線上？",
            facts=selling,
            use_llm=False,
        )
        self.assertEqual(off["result"]["intent"], "reading")
        self.assertIn("差一個數量級", off["result"]["reply"])
        self.assertIn("21.2 萬張", off["result"]["reply"])

        llm_plan = prepare_stock_report_chat(
            stock_id="2330",
            message=q,
            facts=SAMPLE_STOCK_FACTS,
            use_llm=True,
        )
        self.assertEqual(llm_plan["mode"], "ollama")
        self.assertEqual(llm_plan["fallback_source"], "reading")
        blob = "\n".join(str(item.get("content") or "") for item in llm_plan["messages"])
        self.assertIn("foreign_net_lots", blob)
        self.assertIn("close_vs_ma20_pct", blob)
        self.assertIn("同向或互相矛盾", blob)
        self.assertIn("先對方向", llm_plan["fallback_reply"])
        self.assertIn("number_guard", llm_plan)

    def test_reading_template_follows_subject(self) -> None:
        major = build_reading_reply(
            SAMPLE_STOCK_FACTS,
            message="為什麼主力跟外資對作？",
        )
        self.assertIn("主力", major)
        self.assertIn("6,200", major)
        self.assertIn("不是對作", major)
        self.assertNotIn("月線", major)
        self.assertNotIn("RSI", major)

        margin = build_reading_reply(
            SAMPLE_STOCK_FACTS,
            message="融資風險在哪？",
        )
        self.assertIn("融資", margin)
        self.assertIn("8.5%", margin)
        self.assertNotIn("月線", margin)
        self.assertNotIn("外資今天", margin)

    def test_pullback_reply_states_price_path_before_ma20(self) -> None:
        facts = {
            **SAMPLE_STOCK_FACTS,
            "foreign_net_lots": -5438,
            "foreign_cum_lots": -22000,
            "major_cum_lots": -8000,
            "close_vs_ma20_pct": -7.37,
            "lookback_days": 5,
            "period_return_pct": -4.2,
            "price_trend": "down",
            "dist_to_20d_high_pct": 12.6,
        }
        question = "為什麼回檔到這麼深，原因可能出在哪？"
        reply = build_reading_reply(facts, message=question)
        self.assertLess(reply.index("近 5 個交易日"), reply.index("月線"))
        self.assertIn("-4.20%", reply)
        self.assertIn("12.60%", reply)
        self.assertIn("不是這段跌幅", reply)
        self.assertIn("同一段外資累計", reply)
        self.assertIn("5,438", reply)
        self.assertIn("這不是預測明天", reply)
        self.assertNotIn("放在一起讀", reply)
        self.assertTrue(reading_reply_grounded(reply, facts, question))
        compact = build_compact_stock_facts(facts)
        self.assertEqual(compact["period_return_pct"], -4.2)
        self.assertEqual(compact["lookback_days"], 5)
        self.assertEqual(compact["dist_to_20d_high_pct"], 12.6)
        self.assertEqual(compact["major_cum_lots"], -8000)

    def test_reading_reply_rejects_invented_numbers_and_flipped_sign(self) -> None:
        grounded = build_reading_reply(
            SAMPLE_STOCK_FACTS,
            message="為什麼外資在賣、價格還在月線上？",
        )
        self.assertTrue(
            reading_reply_grounded(
                grounded,
                SAMPLE_STOCK_FACTS,
                "為什麼外資在賣、價格還在月線上？",
            )
        )
        invented = "外資在買，但本益比約 42.7，所以不便宜。\n\n" + DISCLAIMER
        self.assertFalse(
            reading_reply_grounded(invented, SAMPLE_STOCK_FACTS, "為什麼外資在買？")
        )
        flipped = "外資在賣，價格還在月線上，看起來不同步。\n\n" + DISCLAIMER
        self.assertFalse(
            reading_reply_grounded(
                flipped,
                SAMPLE_STOCK_FACTS,
                "為什麼外資在賣、價格還在月線上？",
            )
        )

    def test_fundamental_lookup_is_deterministic(self) -> None:
        calls: list[tuple[str, str]] = []

        def lookup(stock_id: str, trade_date: str) -> dict[str, float]:
            calls.append((stock_id, trade_date))
            return {"per": 18.26, "pbr": 3.15, "dividend_yield": 3.97, "revenue_yoy_pct": 12.5}

        plan = prepare_stock_report_chat(
            stock_id="2330",
            message="本益比多少？",
            facts=SAMPLE_STOCK_FACTS,
            use_llm=True,
            fundamental_lookup=lookup,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["intent"], "fundamental_qa")
        self.assertEqual(plan["result"]["source"], "fundamentals")
        self.assertEqual(calls, [("2330", "2026-09-09")])
        reply = plan["result"]["reply"]
        self.assertIn("18.26", reply)
        self.assertIn("2026-09-09", reply)
        self.assertIn("TaiwanStockPER", reply)
        self.assertNotIn("殖利率", reply)
        self.assertNotIn("外資", reply)
        self.assertIn(DISCLAIMER, reply)

        yield_plan = prepare_stock_report_chat(
            stock_id="2330",
            message="現金股利多少",
            facts=SAMPLE_STOCK_FACTS,
            use_llm=False,
            fundamental_lookup=lookup,
        )
        self.assertIn("3.97%", yield_plan["result"]["reply"])
        self.assertIn("不是每股配息金額", yield_plan["result"]["reply"])
        self.assertNotIn("18.26", yield_plan["result"]["reply"])

        yoy = prepare_stock_report_chat(
            stock_id="2330",
            message="月營收年增多少",
            facts=SAMPLE_STOCK_FACTS,
            use_llm=False,
            fundamental_lookup=lookup,
        )
        self.assertIn("+12.5%", yoy["result"]["reply"])
        self.assertIn("不是新聞標題", yoy["result"]["reply"])
        self.assertNotIn("本益比", yoy["result"]["reply"])

        def boom(stock_id: str, trade_date: str) -> None:
            raise RuntimeError("finmind down")

        missed = prepare_stock_report_chat(
            stock_id="2330",
            message="殖利率多少",
            facts=SAMPLE_STOCK_FACTS,
            use_llm=False,
            fundamental_lookup=boom,
        )
        self.assertIn("沒查到", missed["result"]["reply"])
        self.assertNotIn("外資", missed["result"]["reply"])
        self.assertIn("2026-09-09", missed["result"]["reply"])

        undated = {k: v for k, v in SAMPLE_STOCK_FACTS.items() if k != "trade_date"}
        seen: list[str] = []

        def should_not_call(stock_id: str, trade_date: str) -> None:
            seen.append(stock_id)
            return None

        no_date = prepare_stock_report_chat(
            stock_id="2330",
            message="本益比多少",
            facts=undated,
            trade_date=None,
            use_llm=False,
            fundamental_lookup=should_not_call,
        )
        self.assertEqual(seen, [])
        self.assertIn("沒有基準日", no_date["result"]["reply"])

        direct = build_fundamental_reply(
            facts=SAMPLE_STOCK_FACTS,
            message="本益比多少",
            trade_date="2026-09-09",
            row={"per": -2.5},
        )
        self.assertIn("-2.5", direct)
        self.assertIn("負值", direct)

    def test_prepare_factual_returns_complete_mode(self) -> None:
        plan = prepare_stock_report_chat(
            stock_id="2330",
            message="今天外資買多少",
            facts=SAMPLE_STOCK_FACTS,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["source"], "facts")
        self.assertEqual(plan["result"]["intent"], "factual_qa")
        self.assertIn("買超 8,500 張", plan["result"]["reply"])
        self.assertNotIn("【", plan["result"]["reply"])

    def test_prepare_glossary_returns_complete_mode(self) -> None:
        plan = prepare_stock_report_chat(
            stock_id="2330",
            message="什麼是借券賣出？",
            facts=SAMPLE_STOCK_FACTS,
        )
        self.assertEqual(plan["mode"], "complete")
        self.assertEqual(plan["result"]["source"], "glossary")
        self.assertEqual(plan["result"]["intent"], "glossary")

    def test_iter_events_streaming(self) -> None:
        events = list(
            iter_stock_report_chat_events(
                stock_id="2330",
                message="外資買超多少",
                facts=SAMPLE_STOCK_FACTS,
            )
        )
        event_names = [e[0] for e in events]
        self.assertIn("meta", event_names)
        self.assertIn("token", event_names)
        self.assertIn("done", event_names)

        # Ensure format_sse serializes properly
        sse_chunk = format_sse(events[0][0], events[0][1])
        self.assertTrue(sse_chunk.startswith("event: meta\ndata:"))

    def test_answer_report_chat_tool(self) -> None:
        result = answer_report_chat(
            stock_id="2330",
            message="外資今天買超多少",
            facts=SAMPLE_STOCK_FACTS,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("買超 8,500 張", result.reply or "")


if __name__ == "__main__":
    unittest.main()
