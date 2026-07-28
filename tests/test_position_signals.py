"""Tests for deterministic position facts + bucket-aware checks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from chip_signals import build_chip_facts  # noqa: E402
from position_signals import (  # noqa: E402
    build_position_facts,
    run_position_checks,
)


def _row(close: str, ma20: str = "50", **overrides) -> dict[str, str]:
    base = {
        "代碼": "2409",
        "名稱": "友達",
        "日期": "2026-07-06",
        "收盤價": close,
        "MA20": ma20,
        "區間20日高": "55",
        "區間20日低": "45",
        "距20日高_%": "9.09",
        "距20日低_%": "11.11",
        "突破20日高": "否",
        "跌破20日低": "否",
        "外資買賣超_張": "-500",
        "投信買賣超_張": "-100",
        "自營商買賣超_張": "-50",
        "主力買賣超_張": "-200",
        "主力_擷取狀態": "ok",
        "漲跌幅": "-1",
        "收盤偏離MA5_%": "-2",
        "收盤偏離MA10_%": "-1.5",
        "收盤偏離MA20_%": "-2",
        "成交量_張": "10000",
        "區間成交量均值_張": "12000",
        "回看天數": "5",
        "區間漲跌幅_%": "-6",
        "區間外資累計_張": "-2000",
    }
    base.update(overrides)
    return base



def _facts(
    avg_cost: float,
    close: str,
    ma20: str = "50",
    *,
    uses_margin: bool = False,
    **row_overrides,
):
    row = _row(close, ma20, **row_overrides)
    chip = build_chip_facts(row)
    return build_position_facts(
        row,
        stock_id="2409",
        stock_name="友達",
        avg_cost=avg_cost,
        shares=500_000,
        chip_facts=chip,
        uses_margin=uses_margin,
    )


def _scenario_body(facts) -> str:
    plan = facts.scenario_plan
    assert plan is not None
    lines = ["## 操作情境", "部位目前虧損，須依成本與均價設防。"]
    if facts.stop_loss_hint:
        lines.append(f"- {facts.stop_loss_hint}")
    if facts.take_profit_hint:
        lines.append(f"- {facts.take_profit_hint}")
    rank_labels = ("主線", "次線", "尾線")
    for index, item in enumerate(plan.scenarios):
        rank = rank_labels[index]
        lines.append(
            f"- **{item.label}（{item.weight_pct}%，{rank}）**："
            f"{item.trigger_hint} → **{item.action}**"
        )
    return "\n".join(lines)


class PositionBucketTests(unittest.TestCase):
    def test_profit_large_bucket(self) -> None:
        f = _facts(40.0, "50")  # +25%
        self.assertEqual(f.pnl_bucket, "profit_large")
        self.assertEqual(f.position_bias, "protect_gains")
        self.assertEqual(f.technical_stop_price, 45.0)
        self.assertEqual(f.technical_target_price, 55.0)

    def test_loss_large_bucket(self) -> None:
        f = _facts(60.0, "50")  # -16.7%
        self.assertEqual(f.pnl_bucket, "loss_large")
        self.assertEqual(f.position_bias, "defensive")
        self.assertIn("停損參考", f.stop_loss_hint)
        self.assertIn("解套參考", f.take_profit_hint)

    def test_high_volatility_enriches_stop_hint(self) -> None:
        f = _facts(
            60.0,
            "50",
            **{"ATR14": "2.5", "ATR14_%": "5.0"},
        )
        self.assertIn("波動偏高", f.stop_loss_hint)
        self.assertIn("2×ATR", f.stop_loss_hint)

    def test_breakeven_bucket(self) -> None:
        f = _facts(50.0, "50")  # 0%
        self.assertEqual(f.pnl_bucket, "breakeven")

    def test_cost_vs_ma20(self) -> None:
        self.assertEqual(_facts(60.0, "50", "55").cost_vs_ma20, "above")
        self.assertEqual(_facts(40.0, "50", "55").cost_vs_ma20, "below")

    def test_cost_vs_20d_high(self) -> None:
        self.assertEqual(_facts(60.0, "50").cost_vs_20d_high, "above")
        self.assertEqual(_facts(40.0, "50").cost_vs_20d_high, "below")


class ScenarioPlanTests(unittest.TestCase):
    def test_weights_sum_to_100(self) -> None:
        pf = _facts(60.0, "50")
        plan = pf.scenario_plan
        self.assertIsNotNone(plan)
        total = sum(item.weight_pct for item in plan.scenarios)  # type: ignore[union-attr]
        self.assertEqual(total, 100)

    def test_defensive_favors_continuation(self) -> None:
        row = _row("50", **{"區間漲跌幅_%": "-8", "外資買賣超_張": "-800"})
        pf = build_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            avg_cost=60.0,
            shares=500_000,
            chip_facts=build_chip_facts(row),
        )
        plan = pf.scenario_plan
        assert plan is not None
        primary = next(s for s in plan.scenarios if s.is_primary)
        self.assertEqual(primary.id, "continuation")
        self.assertNotIn("加碼", primary.action.split("/")[0])

    def test_summary_includes_plan(self) -> None:
        from position_signals import position_facts_summary_for_prompt

        pf = _facts(60.0, "50")
        summary = position_facts_summary_for_prompt(pf)
        self.assertIn("操作情境權重", summary)
        self.assertIn("主線", summary)


class PositionCheckTests(unittest.TestCase):
    def test_profit_large_requires_protection(self) -> None:
        f = _facts(40.0, "50")
        body = "## 操作情境\n- 續抱觀望，等待更高點\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_profit_no_protection", codes)

    def test_profit_large_generic_fails(self) -> None:
        f = _facts(40.0, "50")
        body = "## 操作情境\n- 觀望，看看市場方向\n- 等待訊號\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_profit_no_protection", codes)
        self.assertIn("position_scenario_unanchored", codes)

    def test_loss_requires_risk_control(self) -> None:
        f = _facts(60.0, "50")
        body = "## 操作情境\n- 目前虧損，續抱等待反彈\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_loss_no_risk_control", codes)

    def test_loss_with_stop_passes_bucket_checks(self) -> None:
        f = _facts(60.0, "50")
        body = (
            "## 操作情境\n"
            f"- 虧損擴大，若跌破近20日低 {f.technical_stop_price} 則停損減碼\n"
            f"- {f.take_profit_hint}\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_loss_no_risk_control", codes)
        self.assertNotIn("position_stop_level_missing", codes)

    def test_loss_missing_stop_level_fails(self) -> None:
        f = _facts(60.0, "50")
        body = "## 操作情境\n- 虧損擴大，續抱等待反彈並減碼\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_stop_level_missing", codes)

    def test_profit_large_missing_target_fails(self) -> None:
        f = _facts(40.0, "50")
        body = "## 操作情境\n- 大幅獲利，採移動停損續抱\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_target_level_missing", codes)

    def test_loss_with_scenario_plan_passes(self) -> None:
        f = _facts(60.0, "50")
        body = _scenario_body(f)
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_scenario_weight_missing", codes)
        self.assertNotIn("position_scenario_primary_unmarked", codes)
        self.assertNotIn("position_loss_no_risk_control", codes)

    def test_scenario_plan_missing_weights_fails(self) -> None:
        f = _facts(60.0, "50")
        body = (
            "## 操作情境\n"
            "- 虧損部位，若延續調節則減碼停損\n"
            "- 若橫盤則觀望\n"
            "- 若反彈則不加碼\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_scenario_weight_missing", codes)

    def test_breakeven_needs_trigger(self) -> None:
        f = _facts(50.0, "50")
        body = "## 操作情境\n- 成本附近觀望\n"
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_breakeven_no_trigger", codes)

    def test_no_close_price_skips(self) -> None:
        f = build_position_facts(
            {"代碼": "2409", "名稱": "友達", "MA20": "50"},
            stock_id="2409",
            stock_name="友達",
            avg_cost=50.0,
            shares=1000,
        )
        self.assertEqual(f.pnl_bucket, "unknown")
        self.assertEqual(run_position_checks("任意內容", f), [])

    def test_margin_flag_on_facts(self) -> None:
        f = _facts(60.0, "50", uses_margin=True)
        self.assertTrue(f.uses_margin)
        self.assertIn("融資", f.required_action_hint)
        self.assertTrue(any("融資" in a for a in f.anchors))

    def test_margin_requires_risk_narrative(self) -> None:
        f = _facts(60.0, "50", uses_margin=True)
        body = _scenario_body(f)
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_margin_no_risk", codes)

    def test_margin_with_call_risk_passes(self) -> None:
        f = _facts(60.0, "50", uses_margin=True)
        body = (
            _scenario_body(f)
            + "\n## 風險與紀律提醒\n"
            + "1. 融資部位須嚴控維持率，接近追繳線優先減碼\n"
            + "2. 勿因沉沒成本無前提攤平\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_margin_no_risk", codes)

    def test_cash_position_skips_margin_gate(self) -> None:
        f = _facts(60.0, "50", uses_margin=False)
        body = _scenario_body(f)
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_margin_no_risk", codes)


if __name__ == "__main__":
    unittest.main()
