"""Tests for the beginner-portfolio narrative validator (portfolio-gate)."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "portfolio-gate"))

from portfolio_signals import PortfolioCandidate, build_portfolio_facts  # noqa: E402
from validate_portfolio_report import validate_portfolio_report  # noqa: E402


def _facts_ns(**overrides):
    base = {
        "chip_regime": "accumulation",
        "ma_stack": "bullish_stack",
        "ma20_slope": "rising",
        "price_trend": "up",
        "rs_period": "outperform",
        "trend_strength": "strong",
        "rsi_zone": "neutral",
        "institutional_consensus": "bullish",
        "volatility_regime": "normal",
        "day_trade_intensity": "normal",
        "anchors": ["外資買超", "均線多頭排列"],
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _cand(stock_id, name, asset_class, category, sector, **fo):
    return PortfolioCandidate(
        stock_id=stock_id,
        name=name,
        asset_class=asset_class,
        category=category,
        sector=sector,
        beginner_core=(asset_class == "etf"),
        defensive=(sector in {"financials", "telecom", "food", "etf_dividend"}),
        facts=_facts_ns(**fo),
        close_price=100.0,
    )


def _universe():
    return [
        _cand("0050", "元大台灣50", "etf", "broad_etf", "etf_broad"),
        _cand("00878", "國泰高股息", "etf", "dividend_etf", "etf_dividend"),
        _cand("1216", "統一", "stock", "blue_chip", "food"),
        _cand("2886", "兆豐金", "stock", "blue_chip", "financials"),
        _cand("2330", "台積電", "stock", "blue_chip", "semiconductor"),
        _cand("2603", "長榮", "stock", "blue_chip", "shipping"),
    ]


def _good_body(facts) -> str:
    holdings_lines = "\n".join(
        f"- {h.name}（{h.stock_id}）：這是{'核心 ETF' if h.role == 'core' else '衛星個股'}，"
        f"因為法人偏多、體質穩健，適合當作組合的一部分。"
        for h in facts.holdings
    )
    return (
        f"## 一句話總結\n"
        f"這是一個{facts.profile_label}的組合，用 ETF 打底、分散在不同產業，適合剛開始投資的新手。\n\n"
        f"## 二、這個組合適合誰\n"
        f"這個配置波動相對溫和、透過 ETF 分散風險，適合能放長期、不想每天盯盤的人。\n\n"
        f"## 三、每檔為什麼選它\n{holdings_lines}\n\n"
        f"## 四、這個組合的風險\n"
        f"整體屬於{facts.risk_label}，波動代表帳面會上下起伏；最壞情況下短期可能帳面虧損，"
        f"建議至少放一年以上。\n\n"
        f"## 五、新手紀律\n"
        f"- 下跌時先別急著賣，確認當初買進的理由還在不在。\n"
        f"- 分批進場，不要一次把錢全部投入。\n"
        f"- 每季定期檢視一次，必要時再平衡回原本的配置比例。\n\n"
        f"## 六、免責聲明\n"
        f"本內容僅供教育參考、非投資建議，投資有風險。\n"
    )


class GoodReportTests(unittest.TestCase):
    def test_good_report_passes(self) -> None:
        for profile in ("conservative", "balanced", "aggressive"):
            facts = build_portfolio_facts(_universe(), profile=profile)
            result = validate_portfolio_report(_good_body(facts), facts)
            self.assertTrue(
                result.passed,
                f"{profile} good report should pass, got {result.summary_lines()}",
            )


class BadReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = build_portfolio_facts(_universe(), profile="conservative")

    def _codes(self, body: str) -> set[str]:
        return {i.code for i in validate_portfolio_report(body, self.facts).issues}

    def test_missing_disclaimer_and_discipline(self) -> None:
        body = "## 總結\n這是保守型組合，用 ETF 分散。台積電 統一 兆豐金 元大台灣50 國泰高股息 都適合長期。"
        codes = self._codes(body)
        self.assertIn("missing_disclaimer", codes)

    def test_forbidden_phrase_flagged(self) -> None:
        body = _good_body(self.facts) + "\n這檔保證獲利、一定漲。"
        self.assertIn("forbidden_phrase", self._codes(body))

    def test_missing_holding_flagged(self) -> None:
        facts = self.facts
        # 移除正文中第一檔持股的名稱與代碼
        drop = facts.holdings[0]
        body = _good_body(facts).replace(drop.name, "某檔").replace(drop.stock_id, "----")
        self.assertIn(f"missing_holding_{drop.stock_id}", self._codes(body))

    def test_hallucinated_holding_flagged(self) -> None:
        # 長榮在保守型被排除，正文卻寫「建議買進長榮」
        body = _good_body(self.facts) + "\n另外也建議買進長榮，作為核心持股。"
        self.assertIn("portfolio_fact_hallucinated_holding", self._codes(body))

    def test_risk_mismatch_flagged(self) -> None:
        body = _good_body(self.facts).replace(
            "整體屬於低風險", "整體屬於高風險、追求高報酬"
        )
        self.assertIn("portfolio_fact_risk_mismatch", self._codes(body))

    def test_incomplete_discipline_flagged(self) -> None:
        body = _good_body(self.facts).replace(
            "## 五、新手紀律\n"
            "- 下跌時先別急著賣，確認當初買進的理由還在不在。\n"
            "- 分批進場，不要一次把錢全部投入。\n"
            "- 每季定期檢視一次，必要時再平衡回原本的配置比例。\n",
            "## 五、新手紀律\n- 記得長期持有就對了。\n",
        )
        self.assertIn("portfolio_reasoning_discipline_incomplete", self._codes(body))


if __name__ == "__main__":
    unittest.main()
