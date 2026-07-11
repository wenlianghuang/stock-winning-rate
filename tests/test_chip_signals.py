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
        "收盤偏離MA10_%": "1.2",
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
        "融資今日餘額_張": "10000",
        "融券今日餘額_張": "3000",
        "券資比_%": "30.0",
        "融資動能_%": "8.0",
        "區間成交量均值_張": "12000",
        "區間當沖佔比均值_%": "30",
        "MA5": "32.0",
        "MA10": "31.0",
        "MA20": "30.0",
        "MA20斜率_%": "1.2",
        "量均線5_張": "15000",
        "量均線20_張": "10000",
        "量均線比": "1.5",
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

    def test_volume_trend_heating(self) -> None:
        facts = build_chip_facts(_row())
        self.assertEqual(facts.volume_trend, "heating")
        self.assertEqual(facts.volume_ma_ratio, 1.5)

    def test_volume_trend_cooling(self) -> None:
        facts = build_chip_facts(
            _row(**{"量均線5_張": "8000", "量均線20_張": "10000", "量均線比": "0.8"})
        )
        self.assertEqual(facts.volume_trend, "cooling")

    def test_volume_price_confirming_up(self) -> None:
        facts = build_chip_facts(_row(**{"區間漲跌幅_%": "5.0"}))
        self.assertEqual(facts.price_trend, "up")
        self.assertEqual(facts.volume_trend, "heating")
        self.assertEqual(facts.volume_price_divergence, "confirming_up")

    def test_volume_price_bearish_divergence(self) -> None:
        facts = build_chip_facts(
            _row(
                **{
                    "區間漲跌幅_%": "5.0",
                    "量均線5_張": "7000",
                    "量均線20_張": "10000",
                    "量均線比": "0.7",
                }
            )
        )
        self.assertEqual(facts.volume_price_divergence, "bearish_divergence")

    def test_volume_trend_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row())
        body = "## 當日籌碼解讀\n量能降溫，交投轉弱。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_volume_trend_mismatch", codes)

    def test_volume_price_divergence_mismatch(self) -> None:
        facts = build_chip_facts(
            _row(
                **{
                    "區間漲跌幅_%": "5.0",
                    "量均線5_張": "7000",
                    "量均線20_張": "10000",
                    "量均線比": "0.7",
                }
            )
        )
        body = "## 近 N 日籌碼趨勢\n價量配合偏多，量價同步走強。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_volume_price_divergence_mismatch", codes)

    def test_volume_trend_from_history(self) -> None:
        history = []
        for index in range(20):
            volume = 10000 + (index * 200 if index >= 15 else 0)
            history.append(
                {
                    "日期": f"2026-06-{index + 1:02d}",
                    "成交量_張": str(volume),
                    "收盤價": "30",
                }
            )
        row = _row()
        for key in ("量均線5_張", "量均線20_張", "量均線比"):
            row.pop(key, None)
        facts = build_chip_facts(row, history)
        self.assertEqual(facts.volume_trend, "heating")

    def test_ma5_golden_cross_from_csv(self) -> None:
        facts = build_chip_facts(_row(**{"MA5交叉MA10": "黃金交叉"}))
        self.assertEqual(facts.ma5_cross_ma10, "golden")
        self.assertEqual(facts.ma5_cross_recency, "today")

    def test_ma5_death_cross_from_closes(self) -> None:
        history = []
        closes = [40.0 + i * 0.5 for i in range(19)] + [35.0]
        for index, close in enumerate(closes):
            history.append(
                {
                    "日期": f"2026-06-{index + 1:02d}",
                    "收盤價": str(close),
                }
            )
        row = _row()
        row.pop("收盤價", None)
        row["收盤價"] = str(closes[-1])
        facts = build_chip_facts(row, history)
        self.assertEqual(facts.ma5_cross_ma10, "death")
        self.assertEqual(facts.ma5_cross_recency, "today")

    def test_ma10_golden_cross_from_closes(self) -> None:
        history = []
        closes = [50.0 - i * 0.1 for i in range(24)] + [65.0]
        for index, close in enumerate(closes):
            history.append(
                {
                    "日期": f"2026-06-{index + 1:02d}",
                    "收盤價": str(close),
                }
            )
        row = _row()
        row["收盤價"] = str(closes[-1])
        facts = build_chip_facts(row, history)
        self.assertEqual(facts.ma10_cross_ma20, "golden")
        self.assertEqual(facts.ma10_cross_recency, "today")

    def test_ma5_cross_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(**{"MA5交叉MA10": "黃金交叉"}))
        body = "## 當日籌碼解讀\nMA5死亡交叉MA10，短線轉弱。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma5_ma10_cross_mismatch", codes)

    def test_ma20_position_and_bullish_alignment(self) -> None:
        # 收盤同時高於 MA5(+2%) 與 MA20(+1%) → 短中線同步偏多
        facts = build_chip_facts(_row())
        self.assertEqual(facts.ma5_position, "above")
        self.assertEqual(facts.ma20_position, "above")
        self.assertEqual(facts.ma_alignment, "bullish")
        self.assertTrue(any("MA20" in a for a in facts.anchors))
        self.assertNotEqual(getattr(facts, "ma_short_alignment", "unknown"), "unknown")
        self.assertNotEqual(getattr(facts, "ma_mid_alignment", "unknown"), "unknown")

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

    def test_ma_stack_bullish(self) -> None:
        facts = build_chip_facts(_row())
        self.assertEqual(facts.ma_stack, "bullish_stack")
        self.assertTrue(any("多頭排列" in anchor for anchor in facts.anchors))

    def test_ma_stack_bearish(self) -> None:
        facts = build_chip_facts(_row(MA5="28.0", MA10="29.0", MA20="30.0"))
        self.assertEqual(facts.ma_stack, "bearish_stack")

    def test_ma_stack_mixed_when_interleaved(self) -> None:
        facts = build_chip_facts(_row(MA5="31.0", MA10="30.0", MA20="30.5"))
        self.assertEqual(facts.ma_stack, "mixed")

    def test_ma_stack_unknown_without_ma_values(self) -> None:
        row = _row()
        for key in ("MA5", "MA10", "MA20"):
            del row[key]
        facts = build_chip_facts(row)
        self.assertEqual(facts.ma_stack, "unknown")

    def test_ma20_slope_rising(self) -> None:
        facts = build_chip_facts(_row(**{"MA20斜率_%": "1.2"}))
        self.assertEqual(facts.ma20_slope, "rising")
        self.assertEqual(facts.ma20_slope_pct, 1.2)

    def test_ma20_slope_falling(self) -> None:
        facts = build_chip_facts(_row(**{"MA20斜率_%": "-1.0"}))
        self.assertEqual(facts.ma20_slope, "falling")

    def test_ma20_slope_flat(self) -> None:
        facts = build_chip_facts(_row(**{"MA20斜率_%": "0.2"}))
        self.assertEqual(facts.ma20_slope, "flat")

    def test_ma_stack_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(MA5="28.0", MA10="29.0", MA20="30.0"))
        body = "## 當日籌碼解讀\n均線呈多頭排列，結構偏多。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma_stack_mismatch", codes)

    def test_ma20_slope_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(**{"MA20斜率_%": "-1.0"}))
        body = "## 當日籌碼解讀\n月線上揚，中期結構轉強。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma20_slope_mismatch", codes)

    def test_ma20_slope_from_history_closes(self) -> None:
        history = []
        for index, close in enumerate(range(100, 125), start=1):
            history.append(
                {
                    "日期": f"2026-06-{index:02d}",
                    "收盤價": str(close),
                }
            )
        row = _row()
        del row["MA20斜率_%"]
        row["收盤價"] = "124"
        facts = build_chip_facts(row, history)
        self.assertEqual(facts.ma20_slope, "rising")
        self.assertIsNotNone(facts.ma20_slope_pct)
        self.assertGreater(facts.ma20_slope_pct or 0, 0)

    def test_breakdown_20d_low(self) -> None:
        facts = build_chip_facts(
            _row(
                **{
                    "收盤價": "44",
                    "區間20日高": "55",
                    "區間20日低": "45",
                    "距20日高_%": "20",
                    "距20日低_%": "-2.22",
                    "突破20日高": "否",
                    "跌破20日低": "是",
                }
            )
        )
        self.assertEqual(facts.breakdown_20d_low, "yes")
        self.assertEqual(facts.range_position, "near_low")

    def test_range_levels_from_history(self) -> None:
        history = []
        for index in range(20):
            price = 100 + index
            history.append(
                {
                    "日期": f"2026-06-{index + 1:02d}",
                    "最高價": str(price + 1),
                    "最低價": str(price - 1),
                    "收盤價": str(price),
                }
            )
        row = _row()
        for key in (
            "區間20日高",
            "區間20日低",
            "距20日高_%",
            "距20日低_%",
            "突破20日高",
            "跌破20日低",
        ):
            row.pop(key, None)
        row["收盤價"] = "119"
        row["最高價"] = "120"
        row["最低價"] = "118"
        facts = build_chip_facts(row, history)
        self.assertIsNotNone(facts.high_20d)
        self.assertIsNotNone(facts.low_20d)
        self.assertNotEqual(facts.range_position, "unknown")

    def test_ma20_position_fact_check(self) -> None:
        # facts 判定跌破月線，正文卻寫站上月線 → fact_ma20_position
        row = _row()
        row["收盤偏離MA5_%"] = "-2.0"
        row["收盤偏離MA20_%"] = "-2.0"
        facts = build_chip_facts(row)
        body = "## 當日籌碼解讀\n收盤已站上月線，中期轉強。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma20_position", codes)

    def test_ma10_position_fact_check(self) -> None:
        # facts 判定跌破 10 日線，正文卻寫站上 10 日線 → fact_ma10_position
        row = _row()
        row["收盤偏離MA10_%"] = "-1.2"
        facts = build_chip_facts(row)
        body = "## 當日籌碼解讀\n收盤已站上10日線，短線轉強。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma10_position", codes)

    def test_ma5_ma10_alignment_mismatch_fact_check(self) -> None:
        # facts MA5/MA10 同步偏空，正文卻寫短線偏多 → fact_ma5_ma10_alignment_mismatch
        row = _row()
        row["收盤偏離MA5_%"] = "-1.0"
        row["收盤偏離MA10_%"] = "-1.0"
        facts = build_chip_facts(row)
        body = "## 當日籌碼解讀\n短線偏多，MA5與MA10同步偏多。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma5_ma10_alignment_mismatch", codes)

    def test_ma10_ma20_alignment_mismatch_fact_check(self) -> None:
        # facts MA10/MA20 同步偏多，正文卻寫短中線偏空 → fact_ma10_ma20_alignment_mismatch
        row = _row()
        row["收盤偏離MA10_%"] = "1.0"
        row["收盤偏離MA20_%"] = "1.0"
        facts = build_chip_facts(row)
        body = "## 當日籌碼解讀\n短中線偏空，MA10與MA20同步偏空。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_ma10_ma20_alignment_mismatch", codes)

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


class RsiSignalsTests(unittest.TestCase):
    def test_rsi_from_csv_overbought(self) -> None:
        facts = build_chip_facts(_row(**{"RSI14": "75.5"}))
        self.assertEqual(facts.rsi_14, 75.5)
        self.assertEqual(facts.rsi_zone, "overbought")
        self.assertTrue(any("RSI14" in anchor for anchor in facts.anchors))

    def test_rsi_from_csv_oversold(self) -> None:
        facts = build_chip_facts(_row(**{"RSI14": "28.0"}))
        self.assertEqual(facts.rsi_zone, "oversold")

    def test_rsi_computed_from_history(self) -> None:
        history = []
        closes = [100.0 - index * 0.8 for index in range(20)]
        for index, close in enumerate(closes):
            history.append(
                {
                    "日期": f"2026-06-{index + 1:02d}",
                    "收盤價": str(close),
                }
            )
        row = _row()
        row["收盤價"] = str(closes[-1])
        facts = build_chip_facts(row, history)
        self.assertIsNotNone(facts.rsi_14)
        self.assertIn(facts.rsi_zone, {"overbought", "oversold", "neutral"})

    def test_rsi_zone_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(**{"RSI14": "75.0"}))
        body = "## 近 N 日籌碼趨勢\nRSI 超賣區，動能偏弱，短線仍有下探風險。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_rsi_zone_mismatch", codes)

    def test_rsi_zone_consistent_passes(self) -> None:
        facts = build_chip_facts(_row(**{"RSI14": "75.0"}))
        body = "## 近 N 日籌碼趨勢\nRSI 偏高，動能過熱，留意回檔。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertNotIn("fact_rsi_zone_mismatch", codes)


class AtrSignalsTests(unittest.TestCase):
    def test_atr_from_csv_high_volatility(self) -> None:
        facts = build_chip_facts(_row(**{"ATR14": "1.25", "ATR14_%": "4.2"}))
        self.assertEqual(facts.atr_14, 1.25)
        self.assertEqual(facts.atr_pct, 4.2)
        self.assertEqual(facts.volatility_regime, "high")
        self.assertTrue(any("ATR14" in anchor for anchor in facts.anchors))

    def test_atr_computed_from_history(self) -> None:
        history = []
        for index in range(20):
            base = 100.0 + index * 0.2
            history.append(
                {
                    "日期": f"2026-06-{index + 1:02d}",
                    "開盤價": str(base - 0.5),
                    "最高價": str(base + 2.0),
                    "最低價": str(base - 2.0),
                    "收盤價": str(base),
                }
            )
        row = _row()
        row["收盤價"] = "104.0"
        row["開盤價"] = "103.5"
        row["最高價"] = "106.0"
        row["最低價"] = "102.0"
        facts = build_chip_facts(row, history)
        self.assertIsNotNone(facts.atr_14)
        self.assertIsNotNone(facts.atr_pct)
        self.assertIn(facts.volatility_regime, {"high", "normal", "low"})

    def test_volatility_regime_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(**{"ATR14": "1.25", "ATR14_%": "4.2"}))
        body = "## 近 N 日籌碼趨勢\n波動偏低，區間參考較可靠。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_volatility_regime_mismatch", codes)

    def test_volatility_regime_consistent_passes(self) -> None:
        facts = build_chip_facts(_row(**{"ATR14": "1.25", "ATR14_%": "4.2"}))
        body = "## 近 N 日籌碼趨勢\n波動偏高，ATR 擴大，停損宜保守。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertNotIn("fact_volatility_regime_mismatch", codes)


class AdxSignalsTests(unittest.TestCase):
    def test_adx_from_csv_strong_trend(self) -> None:
        facts = build_chip_facts(_row(**{"ADX14": "32.5"}))
        self.assertEqual(facts.adx_14, 32.5)
        self.assertEqual(facts.trend_strength, "strong")
        self.assertTrue(any("ADX14" in anchor for anchor in facts.anchors))

    def test_adx_from_csv_weak_trend(self) -> None:
        facts = build_chip_facts(_row(**{"ADX14": "15.0"}))
        self.assertEqual(facts.trend_strength, "weak")

    def test_adx_computed_from_history(self) -> None:
        history = []
        for index in range(35):
            base = 100.0 + index * 1.5
            history.append(
                {
                    "日期": f"2026-06-{index + 1:02d}",
                    "開盤價": str(base - 0.5),
                    "最高價": str(base + 1.0),
                    "最低價": str(base - 0.5),
                    "收盤價": str(base),
                }
            )
        row = _row()
        row["收盤價"] = str(100.0 + 34 * 1.5)
        row["最高價"] = str(100.0 + 34 * 1.5 + 1.0)
        row["最低價"] = str(100.0 + 34 * 1.5 - 0.5)
        facts = build_chip_facts(row, history)
        self.assertIsNotNone(facts.adx_14)
        self.assertIn(facts.trend_strength, {"strong", "weak", "neutral"})

    def test_trend_strength_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(**{"ADX14": "32.0"}))
        body = "## 近 N 日籌碼趨勢\nADX 偏低，趨勢不明，易震盪盤整。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_trend_strength_mismatch", codes)

    def test_trend_strength_consistent_passes(self) -> None:
        facts = build_chip_facts(_row(**{"ADX14": "32.0"}))
        body = "## 近 N 日籌碼趨勢\nADX 偏高，趨勢明確，均線方向較可信。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertNotIn("fact_trend_strength_mismatch", codes)


class MarginChipSignalsTests(unittest.TestCase):
    def test_margin_short_ratio_from_csv_high(self) -> None:
        facts = build_chip_facts(_row(**{"券資比_%": "30.0"}))
        self.assertEqual(facts.margin_short_ratio_pct, 30.0)
        self.assertEqual(facts.margin_short_ratio_zone, "high")
        self.assertTrue(any("券資比" in anchor for anchor in facts.anchors))

    def test_margin_short_ratio_computed_from_balances(self) -> None:
        row = _row(
            **{
                "融資今日餘額_張": "10000",
                "融券今日餘額_張": "500",
            }
        )
        row.pop("券資比_%", None)
        facts = build_chip_facts(row)
        self.assertEqual(facts.margin_short_ratio_pct, 5.0)
        self.assertEqual(facts.margin_short_ratio_zone, "low")

    def test_margin_momentum_heating(self) -> None:
        facts = build_chip_facts(_row(**{"融資動能_%": "8.0"}))
        self.assertEqual(facts.margin_momentum, "heating")
        self.assertEqual(facts.margin_momentum_pct, 8.0)

    def test_margin_momentum_from_history(self) -> None:
        history = [
            {"日期": "2026-07-01", "融資今日餘額_張": "10000"},
            {"日期": "2026-07-02", "融資今日餘額_張": "10200"},
            {"日期": "2026-07-03", "融資今日餘額_張": "10500"},
        ]
        row = _row(**{"融資今日餘額_張": "10500"})
        row.pop("融資動能_%", None)
        row.pop("券資比_%", None)
        facts = build_chip_facts(row, history)
        self.assertEqual(facts.margin_momentum, "heating")
        self.assertAlmostEqual(facts.margin_momentum_pct or 0, 5.0)

    def test_margin_short_ratio_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(**{"券資比_%": "30.0"}))
        body = "## 近 N 日籌碼趨勢\n券資比偏低，融券壓力小。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_margin_short_ratio_mismatch", codes)

    def test_margin_momentum_mismatch_fact_check(self) -> None:
        facts = build_chip_facts(_row(**{"融資動能_%": "8.0"}))
        body = "## 近 N 日籌碼趨勢\n融資動能偏弱，融資餘額減少。\n"
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertIn("fact_margin_momentum_mismatch", codes)

    def test_margin_signals_consistent_passes(self) -> None:
        facts = build_chip_facts(
            _row(**{"券資比_%": "30.0", "融資動能_%": "8.0"})
        )
        body = (
            "## 近 N 日籌碼趨勢\n"
            "券資比偏高，融券壓力大；融資動能偏強，融資餘額增加。\n"
        )
        codes = [c for c, _ in run_fact_checks(body, facts)]
        self.assertNotIn("fact_margin_short_ratio_mismatch", codes)
        self.assertNotIn("fact_margin_momentum_mismatch", codes)


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
