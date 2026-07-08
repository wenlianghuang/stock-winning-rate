"""Tests for reasoning-quality validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from chip_signals import build_chip_facts  # noqa: E402
from fact_checks import run_fact_checks  # noqa: E402
from reasoning_checks import parse_news_titles, run_reasoning_checks  # noqa: E402


def _sample_row() -> dict[str, str]:
    return {
        "代碼": "2409",
        "名稱": "友達",
        "日期": "2026-07-06",
        "外資買賣超_張": "500",
        "投信買賣超_張": "200",
        "自營商買賣超_張": "100",
        "主力買賣超_張": "-300",
        "主力_擷取狀態": "ok",
        "漲跌幅": "1.5",
        "收盤偏離MA5_%": "2.0",
        "回看天數": "5",
        "區間漲跌幅_%": "5.0",
        "區間外資累計_張": "2000",
        "區間主力累計_張": "-500",
        "區間融資餘額淨變化_張": "800",
        "區間融券餘額淨變化_張": "20",
        "成交量_張": "15000",
        "區間成交量均值_張": "12000",
    }


def _good_body() -> str:
    return """
## 當日籌碼解讀
外資今日買超，三大法人一致買超，但主力賣超形成背離。

## 近 N 日籌碼趨勢
外資近 3 日連續買超，區間延續偏多；主力與外資分歧需留意。

## 近期新聞與事件
| 日期 | 標題 | 分類 | 摘要 |
| --- | --- | --- | --- |
| 2026-07-06 | 面板景氣回溫 | 利多 | 需求增 |

## 籌碼與新聞交叉對照
- 外資買超與面板景氣回溫新聞方向一致
- 主力賣超與外資背離，短線仍有分歧

## 短中線情境推演
- 若外資延續買超且站穩均線，則短線偏多；觀察外資連續買超
- 若主力轉賣且跌破 MA5，則轉弱；觀察成交量

## 觀察重點
1. 外資是否連續買超
2. 收盤能否站穩 MA5
3. 成交量是否放大

## 免責聲明
僅供參考，不構成投資建議。
""".strip()


class ReasoningChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = build_chip_facts(
            _sample_row(),
            [
                {"外資買賣超_張": "100"},
                {"外資買賣超_張": "200"},
                {"外資買賣超_張": "500"},
            ],
        )

    def test_parse_news_titles(self) -> None:
        text = "標題：面板景氣回溫\n標題：法人買超\n"
        self.assertEqual(parse_news_titles(text), ["面板景氣回溫", "法人買超"])

    def test_good_body_passes_reasoning(self) -> None:
        issues = run_reasoning_checks(
            _good_body(),
            self.facts,
            has_news=True,
            news_titles=["面板景氣回溫"],
        )
        self.assertEqual(issues, [])

    def test_scenario_no_trigger(self) -> None:
        body = _good_body().replace(
            "## 短中線情境推演\n"
            "- 若外資延續買超且站穩均線，則短線偏多；觀察外資連續買超\n"
            "- 若主力轉賣且跌破 MA5，則轉弱；觀察成交量",
            "## 短中線情境推演\n"
            "- 短線偏多，觀察籌碼\n"
            "- 短線轉弱，留意賣壓",
        )
        codes = [code for code, _ in run_reasoning_checks(body, self.facts)]
        self.assertIn("reasoning_scenario_no_trigger", codes)

    def test_fact_chip_regime_mismatch(self) -> None:
        self.facts.chip_regime = "distribution"
        issues = run_fact_checks(
            _good_body().replace("背離", "健康").replace("分歧", "穩定")
            + "\n籌碼集中，法人布局積極。",
            self.facts,
        )
        codes = [code for code, _ in issues]
        self.assertIn("fact_chip_regime_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
