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
    board_row_from_facts,
    build_comparison,
    build_group_brief,
    build_highlights,
    find_concept_peers,
    list_concept_groups,
    relative_stance,
    resolve_board_members,
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


class ConceptGroupBoardTests(unittest.TestCase):
    def test_pcb_group_includes_holder_and_peers(self) -> None:
        payload = list_concept_groups()
        theme_ids = {theme["theme_id"] for theme in payload["themes"]}
        self.assertIn("pcb", theme_ids)
        self.assertNotIn("packaging & testing", theme_ids)
        pcb = next(theme for theme in payload["themes"] if theme["theme_id"] == "pcb")
        ids = {member["stock_id"] for member in pcb["members"]}
        self.assertTrue({"8046", "3037", "3189", "4958"} <= ids)
        nan_ya = next(member for member in pcb["members"] if member["stock_id"] == "8046")
        self.assertEqual(nan_ya["stock_name"], "南電")
        self.assertIn("pcb", nan_ya["theme_ids"])

    def test_resolve_intersection_skips_other_theme(self) -> None:
        resolved = resolve_board_members(
            theme_id="pcb",
            stock_ids=["8046", "2330", "3037"],
        )
        self.assertTrue(resolved["ok"])
        self.assertEqual(
            [member["stock_id"] for member in resolved["members"]],
            ["8046", "3037"],
        )
        self.assertEqual(resolved["skipped"], ["2330"])

    def test_unknown_theme(self) -> None:
        resolved = resolve_board_members(theme_id="not-a-theme")
        self.assertFalse(resolved["ok"])
        self.assertEqual(resolved["members"], [])

    def test_board_row_labels_and_missing(self) -> None:
        member = {
            "stock_id": "8046",
            "stock_name": "南電",
            "sector": "pcb",
            "theme_ids": ["pcb"],
        }
        row = board_row_from_facts(
            member,
            {
                "stock_id": "8046",
                "stock_name": "南電",
                "trade_date": "2026-10-22",
                "close": 100.0,
                "today_change_pct": 1.2,
                "period_return_pct": 3.0,
                "foreign_net_lots": 10,
                "chip_regime": "accumulation",
                "rs_today": "outperform",
            },
            board_date="2026-10-22",
        )
        self.assertTrue(row["available"])
        self.assertTrue(row["aligned"])
        self.assertEqual(row["chip_regime_label"], "籌碼偏多")
        self.assertEqual(row["rs_today_label"], "強於大盤")
        missing = board_row_from_facts(member, None, board_date="2026-10-22")
        self.assertFalse(missing["available"])
        self.assertEqual(missing["stock_name"], "南電")
        self.assertEqual(missing["chip_regime_label"], "—")


class GroupBriefTests(unittest.TestCase):
    def test_entry_when_three_institutions_buy_with_the_price(self) -> None:
        brief = build_group_brief(
            [
                {
                    "stock_id": "3483",
                    "stock_name": "力致",
                    "available": True,
                    "week_return_pct": 7.07,
                    "week_foreign_net_lots": 200,
                    "week_trust_net_lots": 80,
                    "week_dealer_net_lots": 40,
                },
                {
                    "stock_id": "3017",
                    "stock_name": "奇鋐",
                    "available": True,
                    "week_return_pct": 1.94,
                    "week_foreign_net_lots": -100,
                    "week_trust_net_lots": -20,
                    "week_dealer_net_lots": 10,
                },
            ],
            trade_date="2026-10-22",
        )
        text = "\n".join(brief["paragraphs"])
        self.assertIn("力致不只有價格領先，外資、投信、自營也一起買。", text)
        self.assertIn("較值得觀察入場", text)
        self.assertNotIn("換過去", text)

    def test_no_entry_when_institutions_disagree(self) -> None:
        brief = build_group_brief(
            [
                {
                    "stock_id": "3483",
                    "stock_name": "力致",
                    "available": True,
                    "week_return_pct": 7.07,
                    "week_foreign_net_lots": -116,
                    "week_trust_net_lots": 300,
                    "week_dealer_net_lots": -40,
                },
                {
                    "stock_id": "3324",
                    "stock_name": "雙鴻",
                    "available": True,
                    "week_return_pct": -3.55,
                    "week_foreign_net_lots": 10,
                    "week_trust_net_lots": -20,
                    "week_dealer_net_lots": 5,
                },
            ]
        )
        text = "\n".join(brief["paragraphs"])
        self.assertIn("外資賣超、投信買超、自營賣超", text)
        self.assertIn("不構成入場", text)
        self.assertNotIn("週外資沒有一起偏多", text)

    def test_points_at_the_name_whose_institutions_agree(self) -> None:
        brief = build_group_brief(
            [
                {
                    "stock_id": "3483",
                    "stock_name": "力致",
                    "available": True,
                    "week_return_pct": 7.07,
                    "week_foreign_net_lots": -50,
                    "week_trust_net_lots": -30,
                    "week_dealer_net_lots": -20,
                },
                {
                    "stock_id": "3324",
                    "stock_name": "雙鴻",
                    "available": True,
                    "week_return_pct": -3.55,
                    "week_foreign_net_lots": 100,
                    "week_trust_net_lots": 40,
                    "week_dealer_net_lots": 15,
                },
            ]
        )
        text = "\n".join(brief["paragraphs"])
        self.assertIn("價格和籌碼相反", text)
        self.assertIn("反而雙鴻是外資、投信、自營一起買", text)

    def test_no_entry_when_week_is_tight(self) -> None:
        brief = build_group_brief(
            [
                {
                    "stock_id": "3324",
                    "stock_name": "雙鴻",
                    "available": True,
                    "week_return_pct": -1.0,
                    "week_foreign_net_lots": 10,
                    "week_trust_net_lots": 10,
                    "week_dealer_net_lots": 10,
                },
                {
                    "stock_id": "3017",
                    "stock_name": "奇鋐",
                    "available": True,
                    "week_return_pct": -1.2,
                    "week_foreign_net_lots": -12,
                    "week_trust_net_lots": -4,
                    "week_dealer_net_lots": -2,
                },
            ]
        )
        self.assertIn("報酬沒有拉開，不構成入場。", "\n".join(brief["paragraphs"]))

    def test_switch_when_peer_institutions_agree_and_return_is_better(self) -> None:
        brief = build_group_brief(
            [
                {
                    "stock_id": "3017",
                    "stock_name": "奇鋐",
                    "available": True,
                    "week_return_pct": -8.0,
                    "week_foreign_net_lots": 900,
                    "week_trust_net_lots": -50,
                    "week_dealer_net_lots": 20,
                },
                {
                    "stock_id": "3653",
                    "stock_name": "健策",
                    "available": True,
                    "week_return_pct": -1.0,
                    "week_foreign_net_lots": 40,
                    "week_trust_net_lots": 30,
                    "week_dealer_net_lots": 10,
                },
            ],
            held_ids=["3017"],
        )
        text = "\n".join(brief["paragraphs"])
        self.assertIn("健策報酬更好，而且外資、投信、自營一起買，可考慮換過去。", text)
        self.assertNotIn("續抱", text)

    def test_hold_when_only_foreign_is_larger(self) -> None:
        brief = build_group_brief(
            [
                {
                    "stock_id": "3017",
                    "stock_name": "奇鋐",
                    "available": True,
                    "week_return_pct": -8.0,
                    "week_foreign_net_lots": 20,
                    "week_trust_net_lots": 10,
                    "week_dealer_net_lots": -5,
                },
                {
                    "stock_id": "3653",
                    "stock_name": "健策",
                    "available": True,
                    "week_return_pct": -1.0,
                    "week_foreign_net_lots": 400,
                    "week_trust_net_lots": -30,
                    "week_dealer_net_lots": 10,
                },
            ],
            held_ids=["3017"],
        )
        text = "\n".join(brief["paragraphs"])
        self.assertIn("續抱", text)
        self.assertNotIn("換過去", text)

    def test_checked_names_stay_equal_when_returns_are_all_down(self) -> None:
        brief = build_group_brief(
            [
                {
                    "stock_id": "3008",
                    "stock_name": "大立光",
                    "available": True,
                    "week_return_pct": -6.51,
                    "week_foreign_net_lots": -158,
                    "week_trust_net_lots": 300,
                    "week_dealer_net_lots": -648,
                },
                {
                    "stock_id": "3163",
                    "stock_name": "波若威",
                    "available": True,
                    "week_return_pct": -8.61,
                    "week_foreign_net_lots": -21,
                    "week_trust_net_lots": -1,
                    "week_dealer_net_lots": -6,
                },
                {
                    "stock_id": "3450",
                    "stock_name": "聯鈞",
                    "available": True,
                    "week_return_pct": -9.30,
                    "week_foreign_net_lots": 2438,
                    "week_trust_net_lots": -3526,
                    "week_dealer_net_lots": -307,
                },
                {
                    "stock_id": "6411",
                    "stock_name": "訊芯-KY",
                    "available": True,
                    "week_return_pct": -2.64,
                    "week_foreign_net_lots": 332,
                    "week_trust_net_lots": 0,
                    "week_dealer_net_lots": -13,
                },
            ]
        )
        sections = {section["id"]: section for section in brief["sections"]}
        self.assertEqual(
            [section["title"] for section in brief["sections"]],
            ["規劃", "觀察", "推理", "回看", "結論"],
        )
        observed = "\n".join(sections["observe"]["paragraphs"])
        for name in ("大立光", "波若威", "聯鈞", "訊芯-KY"):
            self.assertIn(name, observed)
        reasoned = "\n".join(sections["reason"]["paragraphs"])
        self.assertIn("聯鈞是外資和投信對作", reasoned)
        self.assertIn("大立光是外資和投信對作", reasoned)
        reviewed = "\n".join(sections["review"]["paragraphs"])
        self.assertIn("初稿撤回", reviewed)
        concluded = "\n".join(sections["conclusion"]["paragraphs"])
        self.assertIn("不構成入場", concluded)
        self.assertNotIn("相對抗跌", concluded)
        self.assertIn("聯鈞", concluded)
        self.assertIn("訊芯-KY", concluded)

    def test_needs_two_available_names(self) -> None:
        brief = build_group_brief(
            [
                {"stock_id": "3324", "stock_name": "雙鴻", "available": True, "week_return_pct": -1.0},
                {"stock_id": "3017", "stock_name": "奇鋐", "available": False},
            ]
        )
        self.assertFalse(brief["ok"])
        self.assertEqual(brief["missing"], ["奇鋐"])


if __name__ == "__main__":
    unittest.main()
