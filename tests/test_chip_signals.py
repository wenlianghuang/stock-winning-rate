"""Tests for deterministic chip facts (v2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from chip_signals import build_chip_facts  # noqa: E402
from fact_checks import run_fact_checks  # noqa: E402


def _row(**overrides) -> dict[str, str]:
    base = {
        "代碼": "2409",
        "名稱": "友達",
        "日期": "2026-07-06",
        "外資買賣超_張": "500",
        "投信買賣超_張": "200",
        "自營商買賣超_張": "100",
        "主力買賣超_張": "-300",
        "主力_擷取狀態": "ok",
        "漲跌幅": "1.5",
        "收盤偏離MA5_%": "2.0",
        "收盤偏離MA20_%": "1.0",
        "融資增減_張": "50",
        "成交量_張": "20000",
        "當沖佔成交量_%": "45",
        "借券賣出_張": "100",
        "回看天數": "5",
        "區間漲跌幅_%": "5.0",
        "區間外資累計_張": "2000",
        "區間主力累計_張": "-500",
        "區間融資餘額淨變化_張": "800",
        "區間融券餘額淨變化_張": "20",
        "區間成交量均值_張": "12000",
        "區間當沖佔比均值_%": "30",
    }
    base.update(overrides)
    return base


class ChipSignalsTests(unittest.TestCase):
    def test_institutional_consensus_bullish(self) -> None:
        facts = build_chip_facts(_row())
        self.assertEqual(facts.institutional_consensus, "bullish")

    def test_major_foreign_divergence(self) -> None:
        facts = build_chip_facts(_row())
        self.assertTrue(facts.major_foreign_divergence)

    def test_chip_regime_and_anchors(self) -> None:
        facts = build_chip_facts(_row())
        self.assertIn(facts.chip_regime, {"accumulation", "mixed", "distribution"})
        self.assertGreaterEqual(len(facts.anchors), 2)
        self.assertTrue(any("法人" in anchor for anchor in facts.anchors))

    def test_volume_spike(self) -> None:
        facts = build_chip_facts(_row(成交量_張="20000", 區間成交量均值_張="10000"))
        self.assertEqual(facts.volume_anomaly, "spike")

    def test_ma20_position_and_bullish_alignment(self) -> None:
        # 收盤同時高於 MA5(+2%) 與 MA20(+1%) → 短中線同步偏多
        facts = build_chip_facts(_row())
        self.assertEqual(facts.ma5_position, "above")
        self.assertEqual(facts.ma20_position, "above")
        self.assertEqual(facts.ma_alignment, "bullish")
        self.assertTrue(any("MA20" in a for a in facts.anchors))

    def test_short_rebound_alignment(self) -> None:
        # 站上 MA5(+2%) 但仍在 MA20 下方(-1.5%) → 短線反彈、中期仍弱
        row = _row()
        row["收盤偏離MA20_%"] = "-1.5"
        facts = build_chip_facts(row)
        self.assertEqual(facts.ma20_position, "below")
        self.assertEqual(facts.ma_alignment, "short_rebound")

    def test_ma20_unknown_when_missing(self) -> None:
        row = _row()
        del row["收盤偏離MA20_%"]
        facts = build_chip_facts(row)
        self.assertEqual(facts.ma20_position, "unknown")
        self.assertEqual(facts.ma_alignment, "unknown")

    def test_ma20_position_fact_check(self) -> None:
        # facts 判定跌破月線，正文卻寫站上月線 → fact_ma20_position
        row = _row()
        row["收盤偏離MA5_%"] = "-2.0"
        row["收盤偏離MA20_%"] = "-2.0"
        facts = build_chip_facts(row)
        body = "## 當日籌碼解讀\n收盤已站上月線，中期轉強。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma20_position", codes)

    def test_ma_alignment_mismatch_fact_check(self) -> None:
        # facts 短中線同步偏空，正文卻寫多頭排列 → fact_ma_alignment_mismatch
        row = _row()
        row["收盤偏離MA5_%"] = "-2.0"
        row["收盤偏離MA20_%"] = "-2.0"
        facts = build_chip_facts(row)
        body = "## 當日籌碼解讀\n均線呈多頭排列，續強可期。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma_alignment_mismatch", codes)

    def test_short_rebound_alignment_mismatch(self) -> None:
        row = _row()
        row["收盤偏離MA5_%"] = "2.0"
        row["收盤偏離MA20_%"] = "-1.5"
        facts = build_chip_facts(row)
        self.assertEqual(facts.ma_alignment, "short_rebound")
        body = "## 當日籌碼解讀\n短中線同步偏多，多頭排列確立。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma_alignment_mismatch", codes)

    def test_short_pullback_alignment_mismatch(self) -> None:
        row = _row()
        row["收盤偏離MA5_%"] = "-2.0"
        row["收盤偏離MA20_%"] = "1.5"
        facts = build_chip_facts(row)
        self.assertEqual(facts.ma_alignment, "short_pullback")
        body = "## 當日籌碼解讀\n短中線同步偏空，空頭排列延續。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma_alignment_mismatch", codes)

    def test_volume_spike_mismatch(self) -> None:
        facts = build_chip_facts(_row(成交量_張="20000", 區間成交量均值_張="10000"))
        self.assertEqual(facts.volume_anomaly, "spike")
        body = "## 當日籌碼解讀\n成交量萎縮，交投清淡。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_volume_mismatch", codes)

    def test_volume_shrink_mismatch(self) -> None:
        facts = build_chip_facts(_row(成交量_張="5000", 區間成交量均值_張="10000"))
        self.assertEqual(facts.volume_anomaly, "shrink")
        body = "## 當日籌碼解讀\n成交量放大，量能激增。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_volume_mismatch", codes)

    def test_price_trend_up_mismatch(self) -> None:
        row = _row()
        row["區間漲跌幅_%"] = "5.0"
        facts = build_chip_facts(row)
        self.assertEqual(facts.price_trend, "up")
        body = "## 近 N 日籌碼趨勢\n區間走弱，區間偏空。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_price_trend_mismatch", codes)

    def test_price_trend_down_mismatch(self) -> None:
        row = _row()
        row["區間漲跌幅_%"] = "-5.0"
        facts = build_chip_facts(row)
        self.assertEqual(facts.price_trend, "down")
        body = "## 近 N 日籌碼趨勢\n區間走強，區間偏多。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_price_trend_mismatch", codes)

    def test_distribution_regime_on_sell_signals(self) -> None:
        row = _row(
            外資買賣超_張="-800",
            投信買賣超_張="-200",
            自營商買賣超_張="-100",
            主力買賣超_張="-400",
            漲跌幅="2.0",
        )
        row["區間漲跌幅_%"] = "-6.0"
        row["區間外資累計_張"] = "-3000"
        row["區間融資餘額淨變化_張"] = "1500"
        facts = build_chip_facts(row)
        self.assertEqual(facts.institutional_consensus, "bearish")
        self.assertIn("price_up_foreign_sell", facts.divergences)


class MarketContextTests(unittest.TestCase):
    def _market_row(self, **overrides) -> dict[str, str]:
        row = _row(**overrides)
        row.update(
            {
                "大盤收盤": "45000",
                "大盤漲跌幅_%": "0.5",
                "大盤MA5": "45500",
                "大盤MA20": "46000",
                "大盤收盤偏離MA5_%": "-1.1",
                "大盤收盤偏離MA20_%": "-2.2",
                "大盤區間漲跌幅_%": "1.0",
            }
        )
        return row

    def test_market_trend_and_outperform(self) -> None:
        # 個股區間 +5% vs 大盤 +1% → 強於大盤；大盤跌破 MA5/MA20
        facts = build_chip_facts(self._market_row())
        self.assertEqual(facts.rs_period, "outperform")
        self.assertEqual(facts.market_ma5_position, "below")
        self.assertEqual(facts.market_ma20_position, "below")
        self.assertTrue(any("強於大盤" in a for a in facts.anchors))

    def test_underperform_when_stock_lags(self) -> None:
        row = self._market_row()
        row["區間漲跌幅_%"] = "-4.0"  # 個股 -4% vs 大盤 +1%
        facts = build_chip_facts(row)
        self.assertEqual(facts.rs_period, "underperform")

    def test_market_rs_mismatch_flagged(self) -> None:
        row = self._market_row()
        row["區間漲跌幅_%"] = "-4.0"  # underperform
        facts = build_chip_facts(row)
        body = (
            "## 當日籌碼解讀\n個股走勢相對抗跌，明顯強於大盤，後續可留意。\n"
        )
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_market_rs_mismatch", codes)

    def test_market_rs_consistent_passes(self) -> None:
        row = self._market_row()
        row["區間漲跌幅_%"] = "-4.0"  # underperform
        facts = build_chip_facts(row)
        body = "## 當日籌碼解讀\n個股走勢弱於大盤，相對弱勢。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertNotIn("fact_market_rs_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
