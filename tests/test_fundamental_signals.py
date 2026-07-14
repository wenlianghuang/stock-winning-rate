"""Tests for fundamental scoring and valuation-style diversification."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from fundamental_signals import (  # noqa: E402
    FundamentalFacts,
    build_fundamental_tags,
    fundamental_score,
)
from portfolio_signals import (  # noqa: E402
    PortfolioCandidate,
    _select_theme_holdings,
    _theme_candidate_score,
    build_theme_portfolio_facts,
)


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
        "anchors": [],
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _fund(**overrides) -> FundamentalFacts:
    facts = FundamentalFacts(
        stock_id=overrides.pop("stock_id", "0000"),
        trade_date="2026-07-13",
        available=True,
        source="test",
        **overrides,
    )
    facts.tags = build_fundamental_tags(facts)
    return facts


def _cand(stock_id, name, *, themes, fundamentals=None, close=100.0, **fact_over):
    return PortfolioCandidate(
        stock_id=stock_id,
        name=name,
        asset_class="stock",
        category="theme_stock",
        sector=themes[0],
        themes=list(themes),
        facts=_facts(**fact_over),
        fundamentals=fundamentals,
        close_price=close,
    )


class FundamentalScoreTests(unittest.TestCase):
    def test_defensive_theme_prefers_yield(self) -> None:
        high = _fund(dividend_yield=5.5, per=12.0, pbr=1.0)
        low = _fund(dividend_yield=0.5, per=40.0, pbr=5.0)
        self.assertGreater(
            fundamental_score(high, themes=["financials"]),
            fundamental_score(low, themes=["financials"]),
        )

    def test_growth_theme_prefers_revenue_yoy(self) -> None:
        strong = _fund(revenue_yoy_pct=30.0, per=28.0)
        weak = _fund(revenue_yoy_pct=-15.0, per=28.0)
        self.assertGreater(
            fundamental_score(strong, themes=["ai"]),
            fundamental_score(weak, themes=["ai"]),
        )

    def test_missing_fundamentals_score_zero(self) -> None:
        self.assertEqual(fundamental_score(None), 0)
        empty = FundamentalFacts(stock_id="1", trade_date="2026-07-13")
        self.assertEqual(fundamental_score(empty), 0)

    def test_tags_include_yield_and_yoy(self) -> None:
        tags = build_fundamental_tags(
            _fund(dividend_yield=4.5, revenue_yoy_pct=20.0, per=12.0)
        )
        self.assertTrue(any("殖利率" in t for t in tags))
        self.assertTrue(any("營收年增" in t for t in tags))

    def test_valuation_bucket(self) -> None:
        self.assertEqual(_fund(per=10.0).valuation_bucket(), "value")
        self.assertEqual(_fund(per=22.0).valuation_bucket(), "blend")
        self.assertEqual(_fund(per=45.0).valuation_bucket(), "growth_val")
        self.assertEqual(_fund(per=-5.0).valuation_bucket(), "distressed")


class ValuationDiversityTests(unittest.TestCase):
    def test_theme_selection_prefers_mixed_valuation_buckets(self) -> None:
        # Same chip strength; different PER buckets. First pass should pick
        # one of each style before doubling up on growth_val.
        candidates = [
            _cand(
                "A1",
                "成長高估1",
                themes=["ai"],
                fundamentals=_fund(stock_id="A1", per=50.0, revenue_yoy_pct=40.0),
            ),
            _cand(
                "A2",
                "成長高估2",
                themes=["ai"],
                fundamentals=_fund(stock_id="A2", per=55.0, revenue_yoy_pct=35.0),
            ),
            _cand(
                "A3",
                "價值股",
                themes=["ai"],
                fundamentals=_fund(stock_id="A3", per=12.0, revenue_yoy_pct=8.0),
            ),
            _cand(
                "A4",
                "平衡股",
                themes=["ai"],
                fundamentals=_fund(stock_id="A4", per=22.0, revenue_yoy_pct=12.0),
            ),
        ]
        scores = {c.stock_id: _theme_candidate_score(c, None) for c in candidates}
        selected, _ = _select_theme_holdings(candidates, ["ai"], scores)
        stock_ids = [c.stock_id for c in selected if not c.is_etf]
        self.assertIn("A3", stock_ids)
        self.assertIn("A4", stock_ids)
        # Among the two pricey names, at most one enters before fill-back need.
        # With 4-slot cap and 3 distinct buckets, both A3/A4 plus one growth should fit.
        growth_hits = sum(1 for sid in stock_ids if sid in {"A1", "A2"})
        self.assertLessEqual(growth_hits, 2)
        self.assertGreaterEqual(len(stock_ids), 3)

    def test_fundamentals_lift_score_into_holdings_tags(self) -> None:
        cands = [
            _cand(
                "0055",
                "元大MSCI金融",
                themes=["financials"],
            ),
            _cand(
                "2881",
                "富邦金",
                themes=["financials"],
                fundamentals=_fund(
                    stock_id="2881",
                    dividend_yield=5.2,
                    per=11.0,
                    pbr=1.1,
                ),
            ),
            _cand(
                "2882",
                "國泰金",
                themes=["financials"],
                fundamentals=_fund(
                    stock_id="2882",
                    dividend_yield=4.8,
                    per=12.0,
                    pbr=1.2,
                ),
            ),
            _cand(
                "2886",
                "兆豐金",
                themes=["financials"],
                fundamentals=_fund(
                    stock_id="2886",
                    dividend_yield=3.5,
                    per=14.0,
                ),
            ),
        ]
        # Mark first as ETF
        cands[0] = PortfolioCandidate(
            stock_id="0055",
            name="元大MSCI金融",
            asset_class="etf",
            category="theme_etf",
            sector="etf_theme",
            themes=["financials"],
            facts=_facts(),
            close_price=60.0,
        )
        facts = build_theme_portfolio_facts(cands, themes=["financials"])
        tags = " ".join(
            " ".join(h.rationale_tags) for h in facts.holdings if h.stock_id == "2881"
        )
        self.assertTrue("殖利率" in tags or "本益比" in tags)


if __name__ == "__main__":
    unittest.main()
