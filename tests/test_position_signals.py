"""Tests for deterministic position facts + bucket-aware checks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from chip_signals import build_chip_facts  # noqa: E402
from position_signals import (  # noqa: E402
    DEFAULT_FINANCING_RATIO,
    DEFAULT_MARGIN_CALL_THRESHOLD_PCT,
    build_position_facts,
    compute_margin_maintenance,
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


def _weight(plan, scenario_id: str) -> int:
    return next(s.weight_pct for s in plan.scenarios if s.id == scenario_id)


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
        # 須高於 2409 校準 atr_pct_high（約 7.8%），否則會被判成 low/normal
        f = _facts(
            60.0,
            "50",
            **{"ATR14": "4.5", "ATR14_%": "9.0"},
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


class MarginMaintenanceTests(unittest.TestCase):
    def test_compute_safe_zone(self) -> None:
        # close=50, cost=40 → rate = 50/(40*0.6)*100 ≈ 208.3, dist ≈ 78.3 → safe
        m = compute_margin_maintenance(avg_cost=40.0, close_price=50.0)
        self.assertAlmostEqual(m["maintenance_rate_pct"], 208.33, places=1)
        self.assertEqual(m["margin_pressure_zone"], "safe")
        self.assertGreater(m["distance_to_call_pp"], 40)

    def test_compute_critical_zone(self) -> None:
        # close=50, cost=70 → rate ≈ 119.0, dist ≈ -11 → critical
        m = compute_margin_maintenance(avg_cost=70.0, close_price=50.0)
        self.assertLess(m["maintenance_rate_pct"], DEFAULT_MARGIN_CALL_THRESHOLD_PCT)
        self.assertEqual(m["margin_pressure_zone"], "critical")
        self.assertAlmostEqual(
            m["margin_call_price"],
            70.0 * DEFAULT_FINANCING_RATIO * DEFAULT_MARGIN_CALL_THRESHOLD_PCT / 100.0,
            places=2,
        )

    def test_compute_tight_zone(self) -> None:
        # close=50, cost=60 → rate ≈ 138.9, dist ≈ 8.9 → tight
        m = compute_margin_maintenance(avg_cost=60.0, close_price=50.0)
        self.assertEqual(m["margin_pressure_zone"], "tight")

    def test_cash_leg_skips_maintenance(self) -> None:
        f = _facts(60.0, "50", uses_margin=False)
        self.assertEqual(f.margin_pressure_zone, "unknown")
        self.assertIsNone(f.maintenance_rate_pct)

    def test_margin_leg_fills_maintenance_and_bias(self) -> None:
        f = _facts(70.0, "50", uses_margin=True)
        self.assertEqual(f.margin_pressure_zone, "critical")
        self.assertIsNotNone(f.maintenance_rate_pct)
        self.assertEqual(f.position_bias, "defensive")
        self.assertTrue(any("維持率" in a for a in f.anchors))

    def test_tight_upgrades_bias_to_cautious(self) -> None:
        # profit_large would be protect_gains; raise call threshold so zone=tight
        row = _row("50")
        f = build_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            avg_cost=40.0,
            shares=1000,
            chip_facts=build_chip_facts(row),
            uses_margin=True,
            call_threshold_pct=200.0,  # rate≈208 → dist≈8 → tight
        )
        self.assertEqual(f.margin_pressure_zone, "tight")
        self.assertEqual(f.position_bias, "cautious")

    def test_summary_includes_maintenance(self) -> None:
        from position_signals import position_facts_summary_for_prompt

        f = _facts(70.0, "50", uses_margin=True)
        summary = position_facts_summary_for_prompt(f)
        self.assertIn("融資維持率", summary)
        self.assertIn("距追繳", summary)
        self.assertIn("單檔估算", summary)

    def test_dual_copies_margin_maintenance_not_blended(self) -> None:
        from position_signals import build_dual_position_facts

        row = _row("50")
        chip = build_chip_facts(row)
        bundle = build_dual_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            cash_shares=100_000,
            cash_avg_cost=30.0,  # pulls blended cost down
            margin_shares=100_000,
            margin_avg_cost=70.0,  # critical on margin
            chip_facts=chip,
        )
        assert bundle.margin is not None
        self.assertEqual(bundle.margin.margin_pressure_zone, "critical")
        self.assertEqual(
            bundle.combined.maintenance_rate_pct,
            bundle.margin.maintenance_rate_pct,
        )
        self.assertEqual(bundle.combined.margin_pressure_zone, "critical")
        # blended cost=50 → would be ~166% if misused; margin cost=70 → ~119%
        self.assertLess(bundle.combined.maintenance_rate_pct, 130)


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

    def test_margin_critical_tightens_continuation_vs_cash(self) -> None:
        row = _row("50", **{"區間漲跌幅_%": "-4", "外資買賣超_張": "-200"})
        chip = build_chip_facts(row)
        cash = build_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            avg_cost=70.0,
            shares=1000,
            chip_facts=chip,
            uses_margin=False,
        )
        margin = build_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            avg_cost=70.0,
            shares=1000,
            chip_facts=chip,
            uses_margin=True,
        )
        assert cash.scenario_plan is not None
        assert margin.scenario_plan is not None
        self.assertEqual(margin.margin_pressure_zone, "critical")
        self.assertGreater(
            _weight(margin.scenario_plan, "continuation"),
            _weight(cash.scenario_plan, "continuation"),
        )
        self.assertLess(
            _weight(margin.scenario_plan, "rebound"),
            _weight(cash.scenario_plan, "rebound"),
        )

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
        self.assertNotIn("position_call_distance_ignored", codes)

    def test_margin_maint_rate_mismatch(self) -> None:
        f = _facts(70.0, "50", uses_margin=True)
        self.assertEqual(f.margin_pressure_zone, "critical")
        body = (
            _scenario_body(f)
            + "\n## 風險與紀律提醒\n"
            + "1. 融資維持率約 200%，接近追繳線優先減碼\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_maint_rate_mismatch", codes)

    def test_margin_maint_rate_correct_passes(self) -> None:
        f = _facts(70.0, "50", uses_margin=True)
        rate = int(round(f.maintenance_rate_pct or 0))
        body = (
            _scenario_body(f)
            + "\n## 風險與紀律提醒\n"
            + f"1. 融資維持率約 {rate}%，距追繳約 {f.distance_to_call_pp:.0f}pp，優先減碼\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_maint_rate_mismatch", codes)
        self.assertNotIn("position_call_distance_ignored", codes)

    def test_call_price_gap_pct_not_confused_with_pp(self) -> None:
        """3711-style: +33.4pp 與現價相對追繳價 +20.4% 並存時不可誤判。"""
        f = _facts(565.0, "554", uses_margin=True)
        self.assertAlmostEqual(f.distance_to_call_pp or 0, 33.42, places=0)
        self.assertAlmostEqual(f.distance_to_call_price_pct or 0, 20.45, places=0)
        body = (
            _scenario_body(f)
            + "\n## 風險與紀律提醒\n"
            + f"1. 融資維持率約 {f.maintenance_rate_pct:.1f}%（成數 60%、追繳線 130%），"
            + f"距追繳：維持率空間 {f.distance_to_call_pp:+.1f}pp（融資壓力需盯），"
            + f"估算追繳價：約 {f.margin_call_price}，"
            + f"現價距追繳約 {f.distance_to_call_price_pct:+.1f}%；接近追繳線優先減碼\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_call_distance_mismatch", codes)
        self.assertNotIn("position_maint_rate_mismatch", codes)

    def test_wrong_call_distance_pp_still_fails(self) -> None:
        f = _facts(70.0, "50", uses_margin=True)
        body = (
            _scenario_body(f)
            + "\n## 風險與紀律提醒\n"
            + "1. 距追繳約 +40pp，接近追繳線優先減碼\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_call_distance_mismatch", codes)

    def test_margin_call_distance_ignored_when_only_defense(self) -> None:
        f = _facts(70.0, "50", uses_margin=True)
        self.assertEqual(f.margin_pressure_zone, "critical")
        body = (
            _scenario_body(f)
            + "\n## 風險與紀律提醒\n"
            + "1. 融資部位須減碼停損，勿攤平\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_margin_no_risk", codes)
        self.assertIn("position_call_distance_ignored", codes)

    def test_margin_pressure_unanchored_rebound_main(self) -> None:
        f = _facts(70.0, "50", uses_margin=True)
        assert f.scenario_plan is not None
        self.assertNotEqual(f.scenario_plan.primary_id, "rebound")
        # Keep scenario weights valid but mark 技術反彈 as 主線 in prose
        lines = ["## 操作情境", "融資虧損套牢，須停損減碼。"]
        if f.stop_loss_hint:
            lines.append(f"- {f.stop_loss_hint}")
        if f.take_profit_hint:
            lines.append(f"- {f.take_profit_hint}")
        for item in f.scenario_plan.scenarios:
            rank = "主線" if item.id == "rebound" else "次線"
            lines.append(
                f"- **{item.label}（{item.weight_pct}%，{rank}）**："
                f"{item.trigger_hint} → **{item.action}**"
            )
        body = (
            "\n".join(lines)
            + "\n## 風險與紀律提醒\n"
            + f"1. 融資維持率約 {f.maintenance_rate_pct:.0f}%，接近追繳線優先減碼\n"
        )
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertIn("position_margin_pressure_unanchored", codes)

    def test_cash_position_skips_margin_gate(self) -> None:
        f = _facts(60.0, "50", uses_margin=False)
        body = _scenario_body(f)
        codes = [c for c, _ in run_position_checks(body, f)]
        self.assertNotIn("position_margin_no_risk", codes)

    def test_dual_legs_priority_margin_first(self) -> None:
        from position_signals import build_dual_position_facts, run_dual_position_checks

        row = _row("50")
        chip = build_chip_facts(row)
        bundle = build_dual_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            cash_shares=100_000,
            cash_avg_cost=40.0,  # profit
            margin_shares=100_000,
            margin_avg_cost=60.0,  # loss
            chip_facts=chip,
        )
        self.assertEqual(bundle.priority, "margin_first")
        self.assertIsNotNone(bundle.cash)
        self.assertIsNotNone(bundle.margin)
        self.assertTrue(bundle.uses_margin)

        plan_lines = []
        rank_labels = ("主線", "次線", "尾線")
        for index, item in enumerate(bundle.combined.scenario_plan.scenarios):
            plan_lines.append(
                f"- **{item.label}（{item.weight_pct}%，{rank_labels[index]}）**："
                f"{item.trigger_hint} → **{item.action}**"
            )
        body = (
            "## 操作情境\n"
            + "\n".join(plan_lines)
            + "\n## 現股\n"
            "- 現股已獲利，可分批停利或續抱\n"
            "## 融資\n"
            f"- 融資虧損，成本套牢，若跌破近20日低 {bundle.margin.technical_stop_price} 則停損減碼\n"
            f"- {bundle.margin.take_profit_hint}\n"
            "## 綜合結論\n"
            "- 優先處理融資腿風險，現股可另設停利\n"
            "## 風險與紀律提醒\n"
            "1. 融資須控維持率，接近追繳線優先減碼\n"
            "2. 勿沉沒成本攤平\n"
        )
        codes = [c for c, _ in run_dual_position_checks(body, bundle)]
        self.assertNotIn("position_synthesis_missing", codes)
        self.assertNotIn("position_margin_no_risk", codes)
        self.assertNotIn("position_cash_section_missing", codes)

    def test_dual_legs_stop_level_in_action_not_status(self) -> None:
        """Early 現股/融資 in 部位現況 must not starve 操作情境 stop checks."""
        from position_signals import build_dual_position_facts, run_dual_position_checks

        row = _row("50")
        chip = build_chip_facts(row)
        bundle = build_dual_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            cash_shares=3_000,
            cash_avg_cost=60.0,  # loss
            margin_shares=3_000,
            margin_avg_cost=55.0,  # loss
            chip_facts=chip,
        )
        assert bundle.cash is not None and bundle.margin is not None
        stop = bundle.cash.technical_stop_price
        plan_lines = []
        rank_labels = ("主線", "次線", "尾線")
        for index, item in enumerate(bundle.combined.scenario_plan.scenarios):
            plan_lines.append(
                f"- **{item.label}（{item.weight_pct}%，{rank_labels[index]}）**："
                f"{item.trigger_hint} → **{item.action}**"
            )
        body = (
            "## 一、部位現況\n"
            "整體持股結構分為現股與融資兩腿部位；嚴守停損紀律，優先處理融資腿（風險／虧損較急）。\n"
            "## 四、操作情境\n"
            + "\n".join(plan_lines)
            + "\n"
            f"- **現股部位防禦手段**：現股虧損擴大，若跌破近20日低 {stop} 則停損減碼\n"
            f"- **融資部位防禦手段**：融資虧損，若跌破近20日低 {stop} 則停損減碼；"
            f"維持率約 {bundle.margin.maintenance_rate_pct:.0f}% 須防追繳\n"
            "- **綜合結論**：優先處理融資腿風險\n"
            "## 五、風險與紀律提醒\n"
            "1. 融資維持率接近追繳線須減碼\n"
            "2. 勿沉沒成本攤平\n"
            "## 六、免責聲明\n"
            "僅供參考\n"
        )
        codes = [c for c, _ in run_dual_position_checks(body, bundle)]
        self.assertNotIn("position_stop_level_missing", codes)
        self.assertNotIn("position_loss_no_risk_control", codes)

    def test_dual_legs_require_synthesis(self) -> None:
        from position_signals import build_dual_position_facts, run_dual_position_checks

        row = _row("50")
        bundle = build_dual_position_facts(
            row,
            stock_id="2409",
            stock_name="友達",
            cash_shares=100_000,
            cash_avg_cost=40.0,
            margin_shares=50_000,
            margin_avg_cost=60.0,
            chip_facts=build_chip_facts(row),
        )
        body = "## 操作情境\n- 觀望\n## 現股\n- 現股獲利續抱\n## 融資\n- 融資虧損停損減碼\n"
        codes = [c for c, _ in run_dual_position_checks(body, bundle)]
        self.assertIn("position_synthesis_missing", codes)


if __name__ == "__main__":
    unittest.main()
