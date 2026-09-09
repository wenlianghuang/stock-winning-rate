"""Unit tests for Phase 0 agent.tools (facts / date / missing CSV)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.tools.chips import build_chip_facts, fetch_chips  # noqa: E402
from agent.tools.dates import get_last_trading_date  # noqa: E402
from agent.tools.digest import send_digest  # noqa: E402
from agent.tools.position import get_holdings, run_position_gate  # noqa: E402
from agent.tools.report import run_report_gate  # noqa: E402
from agent.tools._types import (  # noqa: E402
    EXIT_CSV_MISSING,
    EXIT_FAILED,
    EXIT_HOLDING_MISSING,
    EXIT_OK,
)


def _snapshot_row() -> dict[str, str]:
    return {
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


def _write_snapshot_csv(path: Path, row: dict[str, str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


class AgentToolsDateTests(unittest.TestCase):
    def test_get_last_trading_date_returns_typed_result(self) -> None:
        with patch(
            "agent.tools.dates.resolve_trade_date",
            return_value=("2026-08-18", "note"),
        ), patch(
            "agent.tools.dates.chip_reference_date",
            return_value=dt.date(2026, 8, 19),
        ):
            result = get_last_trading_date(date="2026-08-19")
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, EXIT_OK)
        self.assertEqual(result.reference_date, "2026-08-19")
        self.assertEqual(result.trade_date, "2026-08-18")
        self.assertEqual(result.note, "note")

    def test_get_last_trading_date_uses_reference_when_date_omitted(self) -> None:
        with patch(
            "agent.tools.dates.resolve_trade_date",
            return_value=("2026-08-18", None),
        ) as mocked, patch(
            "agent.tools.dates.chip_reference_date",
            return_value=dt.date(2026, 8, 18),
        ):
            result = get_last_trading_date()
        self.assertTrue(result.ok)
        self.assertEqual(result.trade_date, "2026-08-18")
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0], "2026-08-18")


class AgentToolsFactsTests(unittest.TestCase):
    def test_fetch_chips_returns_paths_from_run_fetch(self) -> None:
        from types import SimpleNamespace

        fake = SimpleNamespace(
            trade_date="2026-08-18",
            date_note=None,
            output_dir=Path("/tmp/reports/stock/2026-08-18"),
            snapshot_paths=[Path("/tmp/tw_stock_2330.csv")],
            history_paths=[Path("/tmp/tw_stock_2330_history.csv")],
            chart_history_paths=[],
        )
        fake_mod = SimpleNamespace(run_fetch=lambda **_kwargs: fake)
        with patch.dict(sys.modules, {"fetch_chip_report": fake_mod}):
            result = fetch_chips(stocks=["2330"], trade_date="2026-08-18")
        self.assertTrue(result.ok)
        self.assertEqual(result.trade_date, "2026-08-18")
        self.assertEqual(result.csv_paths, ["/tmp/tw_stock_2330.csv"])
    def test_build_chip_facts_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "tw_stock_2409.csv"
            _write_snapshot_csv(csv_path, _snapshot_row())
            result = build_chip_facts(csv_path=csv_path)
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.exit_code, EXIT_OK)
            self.assertEqual(result.stock_id, "2409")
            self.assertIsNotNone(result.facts)
            facts_path = Path(result.facts_path or "")
            self.assertTrue(facts_path.exists())
            payload = json.loads(facts_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("stock_id"), "2409")

    def test_build_chip_facts_missing_csv(self) -> None:
        result = build_chip_facts(stock_id="9999", trade_date="2099-01-01")
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, EXIT_CSV_MISSING)


class AgentToolsGateTests(unittest.TestCase):
    def test_run_report_gate_missing_csv(self) -> None:
        result = run_report_gate(stock_id="9999", trade_date="2099-01-01")
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, EXIT_CSV_MISSING)
        self.assertIn("tw_stock_9999.csv", result.error or "")

    def test_run_position_gate_missing_csv(self) -> None:
        result = run_position_gate(
            stock_id="9999",
            trade_date="2099-01-01",
            avg_cost=32.5,
            share_count=1000,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, EXIT_CSV_MISSING)


class AgentToolsHoldingsTests(unittest.TestCase):
    def test_get_holdings_reads_default_file(self) -> None:
        result = get_holdings()
        self.assertTrue(result.ok)
        self.assertIn("2409", result.holdings)
        self.assertEqual(result.holdings["2409"]["shares"], 500000)

    def test_get_holdings_unknown_stock(self) -> None:
        result = get_holdings(stock_id="0000")
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, EXIT_HOLDING_MISSING)


class AgentToolsDigestTests(unittest.TestCase):
    def test_send_digest_blocked_without_approval(self) -> None:
        result = send_digest(
            digest_date="2026-08-18",
            subject="subject",
            main_detail_markdown="body " * 10,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, EXIT_FAILED)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error, "needs_approval")


if __name__ == "__main__":
    unittest.main()
