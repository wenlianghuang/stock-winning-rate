"""Tests for TWSE sector week ranking and market-week validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".agents" / "skills" / "market-weekly"
sys.path.insert(0, str(SKILL))

from market_week_signals import (  # noqa: E402
    _news_sample_sources,
    alignment_label,
    attach_us_alignment,
    build_anchors,
    MarketWeekFacts,
)
from sector_indices import (  # noqa: E402
    parse_mi_index_payload,
    rank_sector_week_returns,
    week_return_pct,
)
from validate_market_week import validate_market_week_report  # noqa: E402


class TestSectorRanking(unittest.TestCase):
    def test_week_return(self) -> None:
        self.assertEqual(week_return_pct(100.0, 110.0), 10.0)
        self.assertEqual(week_return_pct(100.0, 90.0), -10.0)

    def test_parse_tables(self) -> None:
        payload = {
            "stat": "OK",
            "tables": [
                {
                    "title": "115年07月31日 價格指數(臺灣證券交易所)",
                    "fields": ["指數", "收盤指數"],
                    "data": [
                        ["發行量加權股價指數", "23,000.00"],
                        ["半導體類指數", "1,500.00"],
                        ["金融保險類指數", "2,000.00"],
                    ],
                }
            ],
        }
        closes = parse_mi_index_payload(payload)
        self.assertEqual(closes["半導體類指數"], 1500.0)
        self.assertEqual(closes["發行量加權股價指數"], 23000.0)

    def test_rank_strong_weak(self) -> None:
        days = ["2026-07-27", "2026-07-31"]
        closes = {
            "2026-07-27": {
                "半導體類指數": 100.0,
                "金融保險類指數": 100.0,
                "航運類指數": 100.0,
                "食品類指數": 100.0,
                "鋼鐵類指數": 100.0,
                "光電類指數": 100.0,
            },
            "2026-07-31": {
                "半導體類指數": 110.0,
                "金融保險類指數": 101.0,
                "航運類指數": 95.0,
                "食品類指數": 102.0,
                "鋼鐵類指數": 90.0,
                "光電類指數": 100.0,
            },
        }
        ranked = rank_sector_week_returns(
            closes,
            days,
            taiex_week_return=2.0,
            top_n=2,
        )
        self.assertEqual(ranked["universe"], "twse_industry_indices")
        self.assertEqual(ranked["strong"][0]["name"], "半導體類指數")
        self.assertEqual(ranked["strong"][0]["excess_vs_taiex_pct"], 8.0)
        self.assertEqual(ranked["weak"][0]["name"], "鋼鐵類指數")


class TestNewsSamples(unittest.TestCase):
    def test_includes_sector_proxies(self) -> None:
        samples = _news_sample_sources(
            {
                "top": [{"stock_id": "2330", "name": "台積電"}],
                "bottom": [{"stock_id": "2002", "name": "中鋼"}],
            },
            {
                "strong": [{"name": "半導體類指數"}],
                "weak": [{"name": "鋼鐵類指數"}],
            },
        )
        ids = {s["stock_id"] for s in samples}
        self.assertIn("2330", ids)
        self.assertIn("2002", ids)
        # proxy for 半導體 is also 2330 (deduped); 鋼鐵 proxy is 2002 (already)
        sectors = {s.get("related_sector") for s in samples if s.get("related_sector")}
        self.assertTrue(sectors.issubset({"半導體類指數", "鋼鐵類指數", ""}))


class TestAlignment(unittest.TestCase):
    def test_same_sign(self) -> None:
        self.assertEqual(alignment_label(1.0, 2.0), "一致")
        self.assertEqual(alignment_label(-1.0, -0.5), "一致")

    def test_opposite_sign(self) -> None:
        self.assertEqual(alignment_label(1.0, -2.0), "背離")
        self.assertEqual(alignment_label(-1.0, 2.0), "背離")

    def test_missing(self) -> None:
        self.assertEqual(alignment_label(None, 1.0), "unavailable")
        self.assertEqual(alignment_label(1.0, None), "unavailable")

    def test_attach_us_alignment_gaps(self) -> None:
        us = {
            "available": True,
            "indices": {
                "IXIC": {"week_return_pct": 2.0},
                "SOX": {"week_return_pct": -1.0},
            },
        }
        out = attach_us_alignment(
            us,
            taiex_week_return=-1.18,
            tw_semi_week_return=0.6,
        )
        self.assertEqual(out["alignment"]["ixic_vs_taiex"], "背離")
        self.assertEqual(out["alignment"]["sox_vs_tw_semi"], "背離")
        self.assertEqual(out["gaps"]["ixic_minus_taiex_pct"], 3.18)
        self.assertEqual(out["gaps"]["sox_minus_tw_semi_pct"], -1.6)

    def test_build_anchors_include_us(self) -> None:
        facts = MarketWeekFacts(
            week_start="2026-07-27",
            week_end="2026-07-31",
            trading_days=["2026-07-27", "2026-07-31"],
            resolved_as_of="2026-08-02T00:00:00+08:00",
            cutover_applied=True,
            market={"week_return_pct": -1.18},
            leaders={"top": [], "bottom": []},
            sectors={"strong": [], "weak": []},
            us={
                "available": True,
                "indices": {
                    "IXIC": {"week_return_pct": 1.5},
                    "SOX": {"week_return_pct": 2.0},
                },
                "alignment": {
                    "ixic_vs_taiex": "背離",
                    "sox_vs_tw_semi": "一致",
                },
            },
        )
        anchors = build_anchors(facts)
        self.assertTrue(any("那指週報酬" in a for a in anchors))
        self.assertTrue(any("費半週報酬" in a for a in anchors))
        self.assertTrue(any("費半vs半導體 一致" in a for a in anchors))


class TestValidateMarketWeek(unittest.TestCase):
    def _facts(self, *, with_news: bool = False, with_us: bool = False) -> dict:
        facts = {
            "week_start": "2026-07-27",
            "week_end": "2026-07-31",
            "market": {"week_return_pct": 1.5},
            "leaders": {
                "top": [
                    {
                        "stock_id": "2330",
                        "name": "台積電",
                        "week_return_pct": 2.0,
                        "excess_vs_taiex_pct": 0.5,
                    }
                ],
                "bottom": [],
            },
            "sectors": {
                "strong": [
                    {
                        "name": "半導體類指數",
                        "week_return_pct": 3.0,
                        "excess_vs_taiex_pct": 1.5,
                    }
                ],
                "weak": [
                    {
                        "name": "鋼鐵類指數",
                        "week_return_pct": -2.0,
                        "excess_vs_taiex_pct": -3.5,
                    }
                ],
                "all": [
                    {"name": "半導體類指數"},
                    {"name": "鋼鐵類指數"},
                ],
            },
            "anchors": [
                "大盤週報酬 1.5%",
                "權值 台積電(2330) 週報酬 2.0%",
                "強勢類股 半導體類指數 超額 1.5%",
            ],
            "news_titles": [],
        }
        if with_news:
            facts["news_titles"] = [
                "台積電先進製程需求升溫",
                "鋼鐵報價承壓產業觀望",
            ]
        if with_us:
            facts["us"] = {
                "available": True,
                "indices": {
                    "IXIC": {"week_return_pct": 1.2, "name": "那斯達克綜合指數"},
                    "SOX": {"week_return_pct": 2.5, "name": "費城半導體指數"},
                },
                "alignment": {
                    "ixic_vs_taiex": "一致",
                    "sox_vs_tw_semi": "一致",
                },
                "gaps": {
                    "ixic_minus_taiex_pct": -0.3,
                    "sox_minus_tw_semi_pct": -0.5,
                },
                "tw_semi_week_return_pct": 3.0,
            }
            facts["anchors"].extend(
                [
                    "那指週報酬 1.2%",
                    "費半週報酬 2.5%",
                    "費半vs半導體 一致",
                ]
            )
        return facts

    def _scenarios_section(self, *, with_us: bool = False) -> str:
        us_bits = (
            "外生上那斯達克週報酬 1.2%、費半 2.5% 與台股半導體同向，"
            if with_us
            else ""
        )
        us_trigger = (
            "觸發條件：費半延續相對強勢且那指不破週末區間低點，外資續買半導體權值。\n"
            if with_us
            else "觸發條件：外資續買半導體權值，且量能未急縮。\n"
        )
        us_signal = (
            "可追蹤訊號：費半盤中不破本週結構、台積電（2330）守住相對強勢、半導體類指數超額維持正值。\n"
            if with_us
            else "可追蹤訊號：台積電（2330）相對強勢、半導體類指數超額維持正值、加權量能未急縮。\n"
        )
        return (
            "## 五、下週情境推演\n"
            f"進入下週的起手盤是：大盤週報酬 1.5% 偏多、台積電（2330）週報酬 2.0% 帶動權值，"
            f"半導體類指數超額 1.5% 優於鋼鐵類指數。{us_bits}"
            "對週一開盤最關鍵的變數是台積電能否延續超額，以及半導體相對傳產的資金偏好是否延續。\n\n"
            "### 基準／最可能：權值帶動偏多延續\n"
            "結構連貫：與本週權值帶動、半導體超額為正的結構銜接。\n"
            f"{us_trigger}"
            "週一開盤含義：若開盤溫和偏多且權值不弱，偏向延續本週結構而非全面擴散。\n"
            f"{us_signal}"
            "否決條件：台積電相對大盤轉弱，或半導體類指數超額翻負。\n\n"
            "### 次可能：高檔震盪、傳產拖累加大\n"
            "結構連貫：本週鋼鐵類指數已偏弱，若拖累擴散可能壓制大盤節奏。\n"
            "觸發條件：一旦開盤後量能萎縮且鋼鐵／傳產賣壓擴大。\n"
            "週一開盤含義：開盤後若快速翻黑且權值護盤力道不足，宜假設震盪而非單邊。\n"
            "可追蹤訊號：鋼鐵類指數超額是否更負、加權能否守住週線結構、台積電是否獨強難撐盤。\n"
            "否決條件：傳產止跌且廣基同步轉強，顯示擴散式上漲。\n\n"
            "### 尾部風險：結構翻轉、風險偏好轉弱\n"
            "結構連貫：若本週偏多結構被否決，則進入尾部路徑。\n"
            "觸發條件：若週一開盤大幅低開且權值與半導體同步轉弱。\n"
            "週一開盤含義：開盤弱且無法快速收回，代表上週偏多假設暫失效。\n"
            "可追蹤訊號：大盤跌破關鍵區、2330 失守相對強勢、半導體與鋼鐵同弱。\n"
            "否決條件：午盤前權值急遽回穩且超額修復。\n\n"
            "### 週一決策儀表板\n"
            "- 開盤後檢核台積電相對大盤：偏強 → 支持最可能；轉弱 → 傾向次可能。\n"
            "- 開盤後檢核半導體類指數超額：維持正 → 支持基準；翻負 → 提高尾部權重。\n"
            "- 開盤後檢核鋼鐵類指數賣壓：擴大 → 次可能；止跌 → 降低拖累假設。\n"
            "- 開盤後 30～60 分鐘量能：急縮且翻黑 → 震盪假設；溫和放量偏多 → 延續假設。\n"
        )

    def _body_no_news(self) -> str:
        return (
            "## 一、本週大盤總評\n"
            "加權指數本週上漲，大盤週報酬約 1.5%，收盤位在本週區間偏上，整體風險偏好偏多，"
            "量能與均線位置顯示短線仍有承接。\n\n"
            "## 二、權值帶動結構\n"
            "台積電（2330）週報酬 2.0%，對權值與大盤帶動明顯；廣基 ETF 亦大致同步。"
            "拖累端相對有限，結構偏集中在大型電子權值。\n\n"
            "## 三、類股強弱\n"
            "強勢可見半導體類指數相對大盤超額為正；弱勢為鋼鐵類指數，超額偏負，"
            "顯示資金仍偏好科技成長而非傳產循環。\n\n"
            "## 四、交叉解讀\n"
            "本週無可對帳新聞。\n"
            "- 大盤週報酬 1.5% 與權值台積電 2.0% 同向，結構偏權值帶動。\n"
            "- 半導體類指數超額 1.5% 對鋼鐵類指數 -3.5%，資金偏好成長電子。\n\n"
            f"{self._scenarios_section()}\n"
            "## 六、觀察重點\n"
            "1. 週一台積電相對大盤強弱\n"
            "2. 半導體類指數超額能否維持\n"
            "3. 鋼鐵類指數賣壓是否擴散\n"
            "4. 開盤後量能與翻黑／翻紅節奏\n\n"
            "## 七、免責聲明\n"
            "本報告僅供參考，不構成投資建議。\n"
        )

    def _body_with_us(self) -> str:
        return (
            "## 一、本週大盤總評\n"
            "加權指數本週上漲，大盤週報酬約 1.5%，收盤位在本週區間偏上，整體風險偏好偏多。"
            "外生方面可對照那指與費半偏多，但大盤數字仍以台股 facts 為準，量能與均線位置顯示短線仍有承接。\n\n"
            "## 二、權值帶動結構\n"
            "台積電（2330）週報酬 2.0%，對權值與大盤帶動明顯；廣基 ETF 亦大致同步。"
            "拖累端相對有限，結構偏集中在大型電子權值。\n\n"
            "## 三、類股強弱\n"
            "強勢可見半導體類指數相對大盤超額為正；弱勢為鋼鐵類指數，超額偏負，"
            "顯示資金仍偏好科技成長而非傳產循環。\n\n"
            "## 四、交叉解讀\n"
            "本週無可對帳新聞。\n"
            "- 那斯達克週報酬 1.2% 與大盤 1.5% 同向，判定一致。\n"
            "- 費半週報酬 2.5% 與半導體類指數 3.0% 同向，判定一致。\n\n"
            f"{self._scenarios_section(with_us=True)}\n"
            "## 六、觀察重點\n"
            "1. 週一台積電相對大盤強弱\n"
            "2. 費半／那指是否續強\n"
            "3. 半導體類指數超額\n"
            "4. 開盤後量能與結構切換\n\n"
            "## 七、免責聲明\n"
            "本報告僅供參考，不構成投資建議。\n"
        )

    def _body_with_news(self) -> str:
        return (
            "## 一、本週大盤總評\n"
            "加權指數本週上漲，大盤週報酬約 1.5%，收盤位在本週區間偏上，整體風險偏好偏多，"
            "量能與均線位置顯示短線仍有承接。\n\n"
            "## 二、權值帶動結構\n"
            "台積電（2330）週報酬 2.0%，對權值與大盤帶動明顯；廣基 ETF 亦大致同步。"
            "拖累端相對有限，結構偏集中在大型電子權值。\n\n"
            "## 三、類股強弱\n"
            "強勢可見半導體類指數相對大盤超額為正；弱勢為鋼鐵類指數，超額偏負，"
            "顯示資金仍偏好科技成長而非傳產循環。\n\n"
            "## 四、交叉解讀\n"
            "- 「台積電先進製程需求升溫」對上台積電週報酬 2.0% 與半導體類指數超額 1.5%，方向一致。\n"
            "- 「鋼鐵報價承壓產業觀望」對上鋼鐵類指數週報酬 -2.0%，消息與價格一致偏弱；"
            "相對大盤超額 -3.5% 顯示資金離開傳產。\n\n"
            f"{self._scenarios_section()}\n"
            "## 六、觀察重點\n"
            "1. 週一台積電相對大盤強弱\n"
            "2. 半導體類指數超額能否維持\n"
            "3. 鋼鐵類指數賣壓是否擴散\n"
            "4. 開盤後量能與翻黑／翻紅節奏\n\n"
            "## 七、免責聲明\n"
            "本報告僅供參考，不構成投資建議。\n"
        )

    def test_passes_without_news(self) -> None:
        result = validate_market_week_report(
            self._body_no_news(),
            self._facts(with_news=False),
        )
        self.assertTrue(result.passed, result.summary_lines())

    def test_passes_with_news_citations(self) -> None:
        result = validate_market_week_report(
            self._body_with_news(),
            self._facts(with_news=True),
        )
        self.assertTrue(result.passed, result.summary_lines())

    def test_passes_with_us_cross(self) -> None:
        result = validate_market_week_report(
            self._body_with_us(),
            self._facts(with_us=True),
        )
        self.assertTrue(result.passed, result.summary_lines())

    def test_rejects_thin_scenarios(self) -> None:
        thin = self._body_no_news().replace(
            self._scenarios_section(),
            "## 五、下週情境推演\n若外資續買則上漲，一旦量能萎縮則震盪。可追蹤訊號為量能。\n\n",
        )
        result = validate_market_week_report(thin, self._facts())
        self.assertFalse(result.passed)
        codes = {i.code for i in result.issues}
        self.assertTrue(
            {
                "scenarios_too_thin",
                "scenarios_missing_rank",
                "scenarios_missing_falsifier",
                "scenarios_missing_monday",
                "scenarios_missing_monday_dashboard",
            }
            & codes
        )

    def test_rejects_us_without_mentions(self) -> None:
        result = validate_market_week_report(
            self._body_no_news(),
            self._facts(with_us=True),
        )
        self.assertFalse(result.passed)
        codes = {i.code for i in result.issues}
        self.assertTrue(
            {"missing_nasdaq_mention", "missing_sox_mention", "scenario_missing_us_trigger"}
            & codes
        )

    def test_rejects_us_unavailable_unacknowledged(self) -> None:
        facts = self._facts()
        facts["us"] = {"available": False, "indices": {"IXIC": None, "SOX": None}}
        result = validate_market_week_report(self._body_no_news(), facts)
        self.assertFalse(result.passed)
        self.assertTrue(
            any(i.code == "us_unavailable_unacknowledged" for i in result.issues)
        )

    def test_rejects_missing_news_titles(self) -> None:
        body = self._body_with_news().replace("台積電先進製程需求升溫", "某則未提供標題")
        result = validate_market_week_report(body, self._facts(with_news=True))
        self.assertFalse(result.passed)
        self.assertTrue(
            any(i.code == "cross_missing_news_titles" for i in result.issues)
        )

    def test_rejects_hallucinated_sector(self) -> None:
        body = self._body_no_news() + "\n另外太空梭類指數暴漲。\n"
        result = validate_market_week_report(body, self._facts())
        self.assertFalse(result.passed)
        self.assertTrue(any(i.code == "hallucinated_sector" for i in result.issues))

    def test_rejects_hallucinated_leader(self) -> None:
        body = self._body_no_news().replace("台積電（2330）", "奇葩股（9999）")
        result = validate_market_week_report(body, self._facts())
        self.assertFalse(result.passed)
        self.assertTrue(any(i.code == "hallucinated_leader" for i in result.issues))


if __name__ == "__main__":
    unittest.main()
