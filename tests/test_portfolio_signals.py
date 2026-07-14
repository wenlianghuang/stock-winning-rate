"""Tests for deterministic beginner portfolio construction."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from portfolio_signals import (  # noqa: E402
    PROFILE_TEMPLATES,
    PortfolioCandidate,
    build_portfolio_facts,
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
        "anchors": ["外資買超", "均線多頭排列"],
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _cand(stock_id, name, asset_class, category, sector, *, close=100.0, **fact_over):
    return PortfolioCandidate(
        stock_id=stock_id,
        name=name,
        asset_class=asset_class,
        category=category,
        sector=sector,
        beginner_core=(asset_class == "etf"),
        defensive=(sector in {"financials", "telecom", "food", "etf_dividend"}),
        facts=_facts(**fact_over),
        close_price=close,
    )


def _universe():
    return [
        _cand("0050", "元大台灣50", "etf", "broad_etf", "etf_broad"),
        _cand("00878", "國泰高股息", "etf", "dividend_etf", "etf_dividend"),
        _cand("2330", "台積電", "stock", "blue_chip", "semiconductor"),
        _cand("2379", "瑞昱", "stock", "blue_chip", "semiconductor"),  # 同 sector
        _cand("1216", "統一", "stock", "blue_chip", "food"),
        _cand("2886", "兆豐金", "stock", "blue_chip", "financials"),
        _cand("2382", "廣達", "stock", "blue_chip", "computer_hardware"),
        _cand("2603", "長榮", "stock", "blue_chip", "shipping"),
        _cand("9999", "高波動股", "stock", "blue_chip", "panel", volatility_regime="high"),
        _cand("8888", "弱勢股", "stock", "blue_chip", "steel", chip_regime="distribution",
              ma_stack="bearish_stack", price_trend="down", rs_period="underperform"),
    ]


class WeightInvariantTests(unittest.TestCase):
    def test_all_profiles_sum_to_100(self) -> None:
        for profile in PROFILE_TEMPLATES:
            facts = build_portfolio_facts(_universe(), profile=profile)
            total = sum(h.weight_pct for h in facts.holdings)
            self.assertEqual(total, 100, f"{profile} weights must sum to 100")

    def test_single_weight_cap_respected(self) -> None:
        for profile, template in PROFILE_TEMPLATES.items():
            facts = build_portfolio_facts(_universe(), profile=profile)
            for h in facts.holdings:
                self.assertLessEqual(
                    h.weight_pct,
                    template.max_single_weight,
                    f"{profile}/{h.stock_id} exceeds single cap",
                )

    def test_holding_count_in_range(self) -> None:
        for profile, template in PROFILE_TEMPLATES.items():
            facts = build_portfolio_facts(_universe(), profile=profile)
            self.assertGreaterEqual(facts.num_holdings, template.min_holdings)
            self.assertLessEqual(facts.num_holdings, template.max_holdings)


class SelectionTests(unittest.TestCase):
    def test_etf_core_minimum_enforced(self) -> None:
        for profile, template in PROFILE_TEMPLATES.items():
            facts = build_portfolio_facts(_universe(), profile=profile)
            self.assertGreaterEqual(
                facts.etf_weight_pct,
                template.etf_core_min_weight,
                f"{profile} ETF core below minimum",
            )

    def test_sector_diversification_one_stock_per_sector(self) -> None:
        facts = build_portfolio_facts(_universe(), profile="aggressive")
        stock_sectors = [h.sector for h in facts.holdings if h.asset_class != "etf"]
        self.assertEqual(
            len(stock_sectors),
            len(set(stock_sectors)),
            "no two individual stocks may share a sector",
        )

    def test_high_volatility_excluded_from_conservative(self) -> None:
        facts = build_portfolio_facts(_universe(), profile="conservative")
        ids = {h.stock_id for h in facts.holdings}
        self.assertNotIn("9999", ids)

    def test_shipping_only_in_aggressive(self) -> None:
        conservative = build_portfolio_facts(_universe(), profile="conservative")
        aggressive = build_portfolio_facts(_universe(), profile="aggressive")
        self.assertNotIn("2603", {h.stock_id for h in conservative.holdings})
        # 積極型允許航運，且長榮是唯一 shipping 候選 → 應可能入選（至少不被規則排除）
        excluded_ids = {e["stock_id"] for e in aggressive.excluded}
        self.assertNotIn("2603", excluded_ids)

    def test_missing_data_candidate_skipped(self) -> None:
        universe = _universe()
        universe.append(
            PortfolioCandidate(
                stock_id="0000",
                name="無資料股",
                asset_class="stock",
                category="blue_chip",
                sector="misc",
                facts=None,
            )
        )
        facts = build_portfolio_facts(universe, profile="balanced")
        self.assertNotIn("0000", {h.stock_id for h in facts.holdings})

    def test_distribution_stock_excluded_from_conservative(self) -> None:
        facts = build_portfolio_facts(_universe(), profile="conservative")
        self.assertNotIn("8888", {h.stock_id for h in facts.holdings})


class AmountTests(unittest.TestCase):
    def test_amount_allocation_matches_weights(self) -> None:
        amount = 500_000
        facts = build_portfolio_facts(_universe(), profile="balanced", amount_twd=amount)
        for h in facts.holdings:
            self.assertEqual(h.allocation_twd, round(amount * h.weight_pct / 100.0))
            if h.close_price:
                self.assertEqual(h.est_shares, int(h.allocation_twd // h.close_price))


if __name__ == "__main__":
    unittest.main()
