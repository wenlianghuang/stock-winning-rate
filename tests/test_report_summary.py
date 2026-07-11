"""Tests for structured report summary JSON."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from chip_signals import build_chip_facts  # noqa: E402
from report_summary import (  # noqa: E402
    build_market_summary,
    extract_list_items,
    extract_scenarios,
    merge_position_summary,
    parse_markdown_table,
    write_market_summary,
)


def _sample_body() -> str:
    return """
## 一、當日籌碼解讀
外資賣超，三大法人方向分歧，當日價漲但外資賣超形成量價背離。

## 二、近 N 日籌碼趨勢
外資近 2 日連續賣超，區間延續偏空；融資餘額減少，短線回檔但仍在 MA20 上方。

## 三、近期新聞與事件
| 日期 | 標題 | 分類 | 摘要 |
| --- | --- | --- | --- |
| 2026-07-02 | 友達爆違約交割7325萬元　今年上市公司第7起 | 利空 | 高檔震盪 |

## 四、籌碼與新聞交叉對照
- 新聞「友達爆違約交割7325萬元　今年上市公司第7起」與外資連續賣超一致
- 主力與外資累計調節，籌碼偏空延續

## 五、短中線情境推演（1～3 個交易日）
- **情境一**：若外資持續賣超，則短線偏弱；可追蹤外資連續賣超是否擴大
- **情境二**：若外資轉買，則有機會站回 MA5；可追蹤成交量是否放大

## 六、觀察重點
1. 外資是否終止連續賣超並轉為買超
2. 收盤能否站回 MA5 均線

## 七、免責聲明
本報告僅供參考，不構成投資建議。
""".strip()


def _sample_row() -> dict[str, str]:
    return {
        "代碼": "2409",
        "名稱": "友達",
        "日期": "2026-07-10",
        "收盤價": "12.35",
        "漲跌幅": "1.23",
        "外資買賣超_張": "-1200",
        "投信買賣超_張": "50",
        "自營商買賣超_張": "-30",
        "主力買賣超_張": "-800",
        "主力_擷取狀態": "ok",
        "成交量_張": "45000",
        "MA5": "12.10",
        "MA10": "11.95",
        "MA20": "11.50",
        "區間漲跌幅_%": "3.5",
        "區間外資累計_張": "-2500",
        "區間主力累計_張": "-1800",
        "區間天數": "5",
        "融資增減_張": "-100",
        "融券增減_張": "20",
        "當沖佔成交量_%": "18.5",
        "大盤收盤": "22000",
        "大盤漲跌幅_%": "0.5",
        "大盤MA5": "21900",
        "大盤MA20": "21500",
        "大盤區間漲跌幅_%": "2.1",
    }


class ReportSummaryTests(unittest.TestCase):
    def test_parse_markdown_table(self) -> None:
        rows = parse_markdown_table(
            "| 日期 | 標題 | 分類 | 摘要 |\n"
            "| --- | --- | --- | --- |\n"
            "| 2026-07-02 | 測試標題 | 利空 | 摘要文字 |"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["標題"], "測試標題")

    def test_extract_list_items(self) -> None:
        items = extract_list_items("- 第一點\n- 第二點\n1. 第三點")
        self.assertEqual(len(items), 3)

    def test_extract_scenarios_with_title(self) -> None:
        scenarios = extract_scenarios(
            "- **情境一**：若外資持續賣超，則短線偏弱"
        )
        self.assertEqual(scenarios[0]["title"], "情境一")
        self.assertIn("外資", scenarios[0]["content"])

    def test_build_market_summary(self) -> None:
        row = _sample_row()
        facts = build_chip_facts(row, [])
        summary = build_market_summary(facts, _sample_body(), row=row)
        self.assertEqual(summary["stock_id"], "2409")
        self.assertGreater(len(summary["signal_matrix"]), 0)
        self.assertEqual(summary["key_metrics"]["close"], 12.35)
        self.assertEqual(len(summary["news"]), 1)
        self.assertEqual(len(summary["narrative"]["cross_points"]), 2)
        self.assertEqual(len(summary["narrative"]["scenarios"]), 2)
        self.assertEqual(len(summary["narrative"]["watch_items"]), 2)

    def test_write_and_merge_summary(self) -> None:
        row = _sample_row()
        facts = build_chip_facts(row, [])
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "tw_stock_2409.csv"
            csv_path.write_text("代碼\n2409\n", encoding="utf-8")
            write_market_summary(csv_path, facts, _sample_body(), row=row)

            class DummyPositionFacts:
                unrealized_pnl_pct = 5.2
                pnl_bucket = "small_gain"
                position_bias = "hold_watch"
                avg_cost = 11.8
                shares = 1000
                scenario_plan = None
                anchors: list[str] = []

            merge_position_summary(
                csv_path,
                DummyPositionFacts(),
                "## 一、部位現況\n持股小幅獲利。",
            )
            payload = json.loads(csv_path.with_suffix(".summary.json").read_text())
            self.assertIn("market", payload)
            self.assertIn("position", payload)


if __name__ == "__main__":
    unittest.main()
