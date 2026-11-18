"""Prompt builders inline narrative skills and keep per-run facts outside them."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "market-weekly"))

from market_week_prompts import build_market_week_prompt  # noqa: E402
from portfolio_prompts import (  # noqa: E402
    build_portfolio_analysis_prompt_suffix,
    build_portfolio_fix_prompt,
)
from position_prompts import HoldingInfo, build_position_analysis_prompt_suffix  # noqa: E402
from stock_prompts import build_single_stock_analysis_prompt_suffix  # noqa: E402

_US_TECH_DIR = ROOT / ".agents" / "skills" / "us-tech-news"
sys.path.insert(0, str(_US_TECH_DIR))
_US_TECH = _US_TECH_DIR / "process_report.py"
_spec = importlib.util.spec_from_file_location("us_tech_process_report", _US_TECH)
assert _spec is not None and _spec.loader is not None
_us_tech = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _us_tech
_spec.loader.exec_module(_us_tech)
IndexSnapshot = _us_tech.IndexSnapshot
build_agy_prompt = _us_tech.build_agy_prompt


def _assert_inlined(test: unittest.TestCase, prompt: str, marker: str, name: str, title: str) -> None:
    test.assertIn(marker, prompt)
    test.assertIn("不要再讀 skill 檔", prompt)
    test.assertNotIn(f"name: {name}", prompt)
    test.assertNotIn(title, prompt)


class TestNarrativeSkills(unittest.TestCase):
    def test_weekly_inlines_skill_and_facts(self) -> None:
        prompt = build_market_week_prompt(facts_summary="加權週報酬 +1.2", news_section="")
        _assert_inlined(self, prompt, "## 五、下週情境推演", "weekly-narrative", "# 週報正文")
        self.assertIn("加權週報酬 +1.2", prompt)
        self.assertIn("本週無可對帳新聞", prompt)

    def test_stock_inlines_skill_and_facts(self) -> None:
        prompt = build_single_stock_analysis_prompt_suffix(
            stock_name="台積電",
            stock_id="2330",
            facts_summary="外資買超",
            news_section="近期新聞：無",
        )
        _assert_inlined(self, prompt, "## 一、當日籌碼解讀", "stock-narrative", "# 個股正文")
        self.assertIn("台積電（2330）", prompt)
        self.assertIn("外資買超", prompt)

    def test_position_inlines_skill_and_keeps_weights_verbatim(self) -> None:
        holding = HoldingInfo(stock_id="2330", avg_cost=900, shares=1000, cash_shares=1000, cash_avg_cost=900)
        prompt = build_position_analysis_prompt_suffix(
            stock_name="台積電",
            stock_id="2330",
            facts_summary="站上月線",
            holding=holding,
            unrealized_pnl_pct=1.5,
            close_price=913.5,
            news_section="近期新聞：無",
            market_report_path=None,
        )
        _assert_inlined(self, prompt, "## 一、部位現況", "position-narrative", "# 部位正文")
        self.assertIn("百分比勿改", prompt)
        self.assertIn("站上月線", prompt)
        self.assertIn("1,000 股", prompt)

    def test_portfolio_beginner_and_theme_use_different_skills(self) -> None:
        beginner = build_portfolio_analysis_prompt_suffix(
            profile_label="穩健",
            risk_label="低",
            facts_summary="核心 0050",
        )
        theme = build_portfolio_analysis_prompt_suffix(
            profile_label="金融袖口",
            risk_label="主題",
            facts_summary="2882",
            mode="theme",
        )
        _assert_inlined(self, beginner, "## 五、新手紀律", "portfolio-narrative", "# 新手組合正文")
        _assert_inlined(self, theme, "## 五、部位紀律", "theme-narrative", "# 主題袖口正文")
        self.assertNotIn("新手紀律", theme)
        self.assertNotIn("部位紀律", beginner)
        self.assertIn("核心 0050", beginner)
        fix = build_portfolio_fix_prompt(
            profile_label="穩健",
            risk_label="低",
            facts_summary="核心 0050",
            previous_body="舊稿",
            issues_block="- 太短",
        )
        self.assertIn("## 五、新手紀律", fix)
        self.assertNotIn("name: portfolio-narrative", fix)

    def test_us_tech_inlines_slides_and_index_line(self) -> None:
        snap = IndexSnapshot(
            symbol="^IXIC",
            name="那斯達克",
            price=18000,
            previous_close=17900,
            change_pct=0.56,
            currency="USD",
            market_time="2026-08-03",
        )
        prompt = build_agy_prompt("2026-08-03", Path("raw.json"), [snap])
        _assert_inlined(self, prompt, "## 市場概況", "us-tech-narrative", "# 美股科技簡報正文")
        self.assertIn("那斯達克", prompt)
        self.assertIn("raw.json", prompt)
        self.assertIn("只讀 prompt 給的那一個原始資料路徑", prompt)


if __name__ == "__main__":
    unittest.main()
