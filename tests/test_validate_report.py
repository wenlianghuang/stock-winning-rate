"""Tests for report-gate format validation (section slicing)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "report-gate"))

from validate_report import validate_single_stock_report  # noqa: E402


def _row() -> dict[str, str]:
    return {"代碼": "2409", "名稱": "友達"}


def _report_with_scenario_observe_language() -> str:
    return """
## 一、友達（2409）當日籌碼解讀
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
- **情境一**：若外資持續賣超，則短線偏弱；此時可觀察的指標為外資連續賣超是否擴大
- **情境二**：若外資轉買，則有機會站回 MA5；此時可觀察的指標為成交量是否放大

## 六、觀察重點
1. 外資是否終止連續賣超並轉為買超
2. 收盤能否站回 MA5 均線
3. 成交量是否溫和放大

## 七、免責聲明
本報告僅供參考，不構成投資建議。
""".strip()


class ValidateReportTests(unittest.TestCase):
    def test_watch_section_not_stolen_by_scenario_observe_phrase(self) -> None:
        result = validate_single_stock_report(
            _report_with_scenario_observe_language(),
            _row(),
            facts=None,
            has_news=True,
            news_titles=["友達爆違約交割7325萬元　今年上市公司第7起"],
        )
        codes = [issue.code for issue in result.issues]
        self.assertNotIn("sparse_watch", codes)
        self.assertNotIn("reasoning_watch_not_actionable", codes)


if __name__ == "__main__":
    unittest.main()
