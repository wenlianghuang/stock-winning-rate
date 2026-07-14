"""Tests for shared fact-consistency checks (fact_checks.run_fact_checks)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from fact_checks import run_fact_checks  # noqa: E402


def _facts(**overrides) -> SimpleNamespace:
    return SimpleNamespace(**overrides)


class MaAlignmentFalsePositiveTests(unittest.TestCase):
    """The neutral anchor「均線糾結（未形成明確多/空頭排列）」contains the
    substring「空頭排列」, but semantically means *no* bull/bear alignment. It
    must not trip the ma_alignment / ma_stack mismatch checks."""

    def test_short_pullback_not_flagged_by_neutral_stack_anchor(self) -> None:
        body = (
            "個股短線回檔、中期仍強（跌破 MA5 但仍在 MA20 上方）。\n"
            "整體均線排列：均線糾結（未形成明確多/空頭排列），尚未確立波段方向。"
        )
        codes = [
            code
            for code, _ in run_fact_checks(body, _facts(ma_alignment="short_pullback"))
        ]
        self.assertNotIn("fact_ma_alignment_mismatch", codes)

    def test_new_neutral_stack_label_not_flagged(self) -> None:
        body = (
            "個股短線回檔、中期仍強（跌破 MA5 但仍在 MA20 上方）。\n"
            "整體均線排列：均線糾結（多空排列未明）。"
        )
        codes = [
            code
            for code, _ in run_fact_checks(body, _facts(ma_alignment="short_pullback"))
        ]
        self.assertNotIn("fact_ma_alignment_mismatch", codes)

    def test_short_rebound_not_flagged_by_neutral_stack_anchor(self) -> None:
        body = (
            "個股短線反彈、中期仍弱。\n"
            "整體均線排列：均線糾結（未形成明確多/空頭排列）。"
        )
        codes = [
            code
            for code, _ in run_fact_checks(body, _facts(ma_alignment="short_rebound"))
        ]
        self.assertNotIn("fact_ma_alignment_mismatch", codes)

    def test_real_bearish_alignment_still_flagged_for_bullish_facts(self) -> None:
        """Masking must not suppress a genuine contradiction."""
        body = "個股目前呈現空頭排列，均線向下。"
        codes = [
            code
            for code, _ in run_fact_checks(body, _facts(ma_alignment="bullish"))
        ]
        self.assertIn("fact_ma_alignment_mismatch", codes)

    def test_stack_mismatch_not_tripped_by_neutral_anchor(self) -> None:
        body = "均線糾結（未形成明確多/空頭排列），方向不明。"
        codes = [
            code
            for code, _ in run_fact_checks(body, _facts(ma_stack="bullish_stack"))
        ]
        self.assertNotIn("fact_ma_stack_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
