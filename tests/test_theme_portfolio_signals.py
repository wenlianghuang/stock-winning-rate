"""Tests for theme-mode portfolio construction (v1)."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "portfolio-gate"))

from portfolio_signals import (  # noqa: E402
    THEME_TEMPLATE,
    PortfolioCandidate,
    build_theme_portfolio_facts,
    normalize_themes,
    theme_slug,
)
from validate_portfolio_report import validate_portfolio_report  # noqa: E402


def _facts(**overrides):
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


def _cand(
    stock_id,
    name,
    *,
    asset_class="stock",
    category="theme_stock",
    sector="financials",
    themes=None,
    close=100.0,
    **fact_over,
):
    return PortfolioCandidate(
        stock_id=stock_id,
        name=name,
        asset_class=asset_class,
        category=category,
        sector=sector,
        themes=list(themes or []),
        defensive=("financials" in (themes or [])),
        facts=_facts(**fact_over),
        close_price=close,
    )


def _financial_universe():
    return [
        _cand("0055", "元大MSCI金融", asset_class="etf", category="theme_etf",
              sector="etf_theme", themes=["financials"]),
        _cand("2881", "富邦金", themes=["financials"], sector="financials"),
        _cand("2882", "國泰金", themes=["financials"], sector="financials"),
        _cand("2886", "兆豐金", themes=["financials"], sector="financials"),
        _cand("2891", "中信金", themes=["financials"], sector="financials"),
        _cand("2884", "玉山金", themes=["financials"], sector="financials",
              chip_regime="distribution", ma_stack="bearish_stack", price_trend="down",
              rs_period="underperform"),
    ]


def _fusion_universe():
    return _financial_universe() + [
        _cand("3017", "奇鋐", themes=["thermal"], sector="thermal"),
        _cand("3324", "雙鴻", themes=["thermal"], sector="thermal"),
        _cand("3653", "健策", themes=["thermal"], sector="thermal"),
        _cand("2421", "建準", themes=["thermal"], sector="thermal"),
    ]


class ThemeHelpersTests(unittest.TestCase):
    def test_normalize_and_slug(self) -> None:
        self.assertEqual(normalize_themes(["Financials", "thermal", "financials"]),
                         ["financials", "thermal"])
        self.assertEqual(theme_slug(["thermal", "financials"]), "theme_financials_thermal")

    def test_universe_catalog_has_independent_themes(self) -> None:
        from portfolio_signals import load_theme_catalog, load_universe

        path = (
            ROOT
            / ".agents"
            / "skills"
            / "portfolio-gate"
            / "portfolio_theme_universe.json"
        )
        catalog = load_theme_catalog(path)
        self.assertGreaterEqual(len(catalog), 10)
        for required in (
            "financials",
            "thermal",
            "ai",
            "pcb",
            "shipping",
            "semiconductor",
            "dividend",
        ):
            self.assertIn(required, catalog)
        candidates = load_universe(path)
        for tid in catalog:
            self.assertTrue(
                any(tid in (c.get("themes") or []) for c in candidates),
                f"theme {tid} has no candidates",
            )


class ThemeSelectionTests(unittest.TestCase):
    def test_financial_portfolio_weights_and_same_sector(self) -> None:
        facts = build_theme_portfolio_facts(
            _financial_universe(), themes=["financials"], trade_date="2026-07-13"
        )
        self.assertEqual(facts.mode, "theme")
        self.assertEqual(facts.themes, ["financials"])
        self.assertEqual(facts.profile_label, "金融主題")
        self.assertEqual(sum(h.weight_pct for h in facts.holdings), 100)
        self.assertLessEqual(facts.num_holdings, THEME_TEMPLATE.max_holdings)
        self.assertGreaterEqual(facts.num_holdings, 2)
        stock_ids = [h.stock_id for h in facts.holdings if h.asset_class == "stock"]
        # 主題模式允許同一 sector 多檔金融股
        self.assertGreaterEqual(len(stock_ids), 2)
        for h in facts.holdings:
            self.assertLessEqual(h.weight_pct, facts.max_single_weight)
            self.assertEqual(h.role, "theme")
        self.assertEqual(sum(h.weight_pct for h in facts.holdings), 100)

    def test_weaker_chip_stock_ranks_lower(self) -> None:
        facts = build_theme_portfolio_facts(
            _financial_universe(), themes=["financials"]
        )
        scores = {h.stock_id: h.score for h in facts.holdings}
        if "2884" in scores and "2881" in scores:
            self.assertLess(scores["2884"], scores["2881"])

    def test_fusion_picks_both_themes(self) -> None:
        facts = build_theme_portfolio_facts(
            _fusion_universe(), themes=["financials", "thermal"]
        )
        self.assertEqual(facts.profile_label, "金融＋散熱主題")
        theme_hits = set()
        for h in facts.holdings:
            theme_hits.update(h.themes)
        self.assertIn("financials", theme_hits)
        self.assertIn("thermal", theme_hits)
        self.assertTrue(any("袖口" in w or "融合" in w for w in facts.warnings))

    def test_unrelated_theme_candidates_ignored(self) -> None:
        universe = _financial_universe() + [
            _cand("2330", "台積電", themes=["ai"], sector="semiconductor"),
        ]
        facts = build_theme_portfolio_facts(universe, themes=["financials"])
        self.assertNotIn("2330", {h.stock_id for h in facts.holdings})

    def test_missing_data_skipped(self) -> None:
        universe = _financial_universe()
        universe.append(
            PortfolioCandidate(
                stock_id="2899",
                name="無資料金",
                asset_class="stock",
                category="theme_stock",
                sector="financials",
                themes=["financials"],
                facts=None,
            )
        )
        facts = build_theme_portfolio_facts(universe, themes=["financials"])
        self.assertNotIn("2899", {h.stock_id for h in facts.holdings})


class ThemeValidatorTests(unittest.TestCase):
    def _good_body(self, facts) -> str:
        holds = "\n".join(
            f"- {h.name}（{h.stock_id}）：籌碼偏多，作為主題持股。"
            for h in facts.holdings
        )
        themes = "、".join(facts.theme_labels)
        return (
            f"這是一個{facts.profile_label}的組合，定位是{themes}主題袖口，"
            f"適合已有其他部位、想加一筆主題曝險的人。\n\n"
            f"## 適合誰\n"
            f"適合能接受主題集中風險、把這筆錢當袖口而非全倉的人。\n\n"
            f"## 每檔為什麼選它\n{holds}\n\n"
            f"## 風險\n"
            f"預期波動中高，主題集中時回檔幅度可能大於大盤。\n\n"
            f"## 部位紀律\n"
            f"- 下跌或主題退潮時，先確認籌碼理由是否還在。\n"
            f"- 分批進場，不要一次買滿。\n"
            f"- 每季檢視主題是否仍成立。\n\n"
            f"## 免責\n僅供教育參考，非投資建議，投資有風險。\n"
        )

    def test_theme_narrative_passes(self) -> None:
        facts = build_theme_portfolio_facts(
            _financial_universe(), themes=["financials"]
        )
        result = validate_portfolio_report(self._good_body(facts), facts)
        self.assertTrue(result.passed, result.summary_lines())

    def test_theme_false_safety_rejected(self) -> None:
        facts = build_theme_portfolio_facts(
            _financial_universe(), themes=["financials"]
        )
        body = self._good_body(facts).replace(
            "主題集中時回檔幅度可能大於大盤",
            "這是保本又全市場分散的配置",
        )
        codes = {i.code for i in validate_portfolio_report(body, facts).issues}
        self.assertIn("portfolio_fact_theme_safety_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
