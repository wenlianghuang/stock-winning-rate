"""Unit tests for concept peer lookup and side-by-side comparison."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "portfolio-gate"))

from concept_compare import (  # noqa: E402
    DEFAULT_CONCEPT_UNIVERSE,
    build_comparison,
    build_highlights,
    find_concept_peers,
    relative_stance,
)


class ConceptPeersTests(unittest.TestCase):
    def test_default_universe_is_concept_peers_file(self) -> None:
        self.assertTrue(DEFAULT_CONCEPT_UNIVERSE.name == "concept_peers_universe.json")
        self.assertTrue(DEFAULT_CONCEPT_UNIVERSE.exists())

    def test_financials_peers_include_banks(self) -> None:
        result = find_concept_peers("2881")
        self.assertTrue(result["in_universe"])
        self.assertEqual(result["stock_name"], "富邦金")
        theme_ids = {g["theme_id"] for g in result["groups"]}
        self.assertIn("financials", theme_ids)
        fin = next(g for g in result["groups"] if g["theme_id"] == "financials")
        peer_ids = {p["stock_id"] for p in fin["peers"]}
        self.assertNotIn("2881", peer_ids)
        self.assertIn("2882", peer_ids)

    def test_unknown_stock_empty(self) -> None:
        result = find_concept_peers("9999")
        self.assertFalse(result["in_universe"])
        self.assertEqual(result["groups"], [])


class ConceptCompareTests(unittest.TestCase):
    def test_highlights_foreign_divergence(self) -> None:
        base = {
            "available": True,
            "stock_id": "2881",
            "stock_name": "富邦金",
            "today_change_pct": 1.2,
            "foreign_net_lots": 500,
            "ma20_position": "above",
            "rs_today": "outperform",
            "chip_regime": "accumulation",
            "period_return_pct": 3.0,
        }
        peer = {
            "available": True,
            "stock_id": "2882",
            "stock_name": "國泰金",
            "today_change_pct": -0.5,
            "foreign_net_lots": -200,
            "ma20_position": "below",
            "rs_today": "underperform",
            "chip_regime": "distribution",
            "period_return_pct": -1.0,
        }
        bullets = build_highlights(base, peer)
        self.assertTrue(any("外資方向分歧" in b for b in bullets))
        self.assertTrue(any("富邦金" in b and "強於" in b for b in bullets))

    def test_comparison_rows(self) -> None:
        payload = build_comparison(
            base_facts={
                "stock_id": "2881",
                "stock_name": "富邦金",
                "trade_date": "2026-07-31",
                "today_change_pct": 1.0,
                "foreign_net_lots": 100,
                "ma20_position": "above",
            },
            peer_facts={
                "stock_id": "2882",
                "stock_name": "國泰金",
                "trade_date": "2026-07-31",
                "today_change_pct": -0.5,
                "foreign_net_lots": -50,
                "ma20_position": "below",
            },
            base_close=80.0,
            peer_close=60.0,
        )
        self.assertTrue(payload["base"]["available"])
        self.assertEqual(payload["base"]["close"], 80.0)
        keys = {row["key"] for row in payload["rows"]}
        self.assertIn("today_change_pct", keys)
        self.assertIn("foreign_net_lots", keys)
        self.assertIn(payload["stance"]["label"], {"stronger", "weaker", "mixed", "unknown"})

    def test_relative_stance_weaker_needs_two_signals(self) -> None:
        base = {
            "available": True,
            "stock_name": "友達",
            "period_return_pct": -4.0,
            "today_change_pct": -1.2,
            "chip_regime": "distribution",
            "rs_today": "underperform",
        }
        peer = {
            "available": True,
            "stock_name": "群創",
            "period_return_pct": 2.0,
            "today_change_pct": 0.4,
            "chip_regime": "accumulation",
            "rs_today": "outperform",
        }
        stance = relative_stance(base, peer)
        self.assertEqual(stance["label"], "weaker")
        self.assertTrue(stance["base_weaker"])
        self.assertGreaterEqual(len(stance["reasons"]), 2)

    def test_relative_stance_unknown_without_facts(self) -> None:
        stance = relative_stance({"available": False}, {"available": True})
        self.assertEqual(stance["label"], "unknown")
        self.assertFalse(stance["base_weaker"])


if __name__ == "__main__":
    unittest.main()
