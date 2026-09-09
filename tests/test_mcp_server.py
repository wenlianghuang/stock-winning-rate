"""Phase 1 MCP: list tools and fetch_chips → run_report_gate over in-memory transport."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.mcp_server import TOOL_NAMES, build_mcp, main as mcp_main  # noqa: E402
from agent.tools._types import EXIT_CSV_MISSING, EXIT_FAILED, EXIT_HOLDING_MISSING, EXIT_OK  # noqa: E402
from agent.tools.chips import FetchChipsResult  # noqa: E402
from agent.tools.report import ReportGateResult  # noqa: E402


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


def _session():
    from mcp.shared.memory import create_connected_server_and_client_session

    return create_connected_server_and_client_session(build_mcp(), raise_exceptions=True)


class McpCliTests(unittest.TestCase):
    def test_list_tools_prints_phase0_names(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = mcp_main(["--list-tools"])
        self.assertEqual(code, 0)
        names = [line.split("\t", 1)[0] for line in buffer.getvalue().splitlines() if line]
        self.assertEqual(names, list(TOOL_NAMES))

    def test_main_py_routes_mcp_list_tools(self) -> None:
        from main import main as cli_main

        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = cli_main(["mcp", "--list-tools"])
        self.assertEqual(code, 0)
        self.assertIn("fetch_chips", buffer.getvalue())
        self.assertIn("run_report_gate", buffer.getvalue())


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_tools_and_report_gate_prereq(self) -> None:
        async with _session() as session:
            listed = await session.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            self.assertEqual(set(by_name), set(TOOL_NAMES))
            report_desc = by_name["run_report_gate"].description or ""
            self.assertIn("fetch_chips", report_desc)
            self.assertIn("CSV", report_desc)
            send_desc = by_name["send_digest"].description or ""
            self.assertIn("approved", send_desc)

    async def test_fetch_chips_then_report_gate_for_2330(self) -> None:
        fake_fetch = FetchChipsResult(
            ok=True,
            exit_code=EXIT_OK,
            trade_date="2026-08-18",
            output_dir="/tmp/reports/stock/2026-08-18",
            csv_paths=["/tmp/tw_stock_2330.csv"],
        )
        fake_gate = ReportGateResult(
            ok=True,
            exit_code=EXIT_OK,
            stock_id="2330",
            trade_date="2026-08-18",
            csv_path="/tmp/tw_stock_2330.csv",
            md_path="/tmp/tw_stock_2330.md",
        )
        with (
            patch("agent.tools.chips.fetch_chips", return_value=fake_fetch) as fetch_mock,
            patch("agent.tools.report.run_report_gate", return_value=fake_gate) as gate_mock,
        ):
            async with _session() as session:
                fetch = _payload(await session.call_tool("fetch_chips", {"stocks": ["2330"]}))
                gate = _payload(
                    await session.call_tool(
                        "run_report_gate",
                        {"stock_id": "2330", "trade_date": fetch["trade_date"], "skip_pdf": True},
                    )
                )
        self.assertTrue(fetch["ok"])
        self.assertEqual(fetch["csv_paths"], ["/tmp/tw_stock_2330.csv"])
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["stock_id"], "2330")
        self.assertEqual(gate["md_path"], "/tmp/tw_stock_2330.md")
        fetch_mock.assert_called_once()
        self.assertEqual(fetch_mock.call_args.kwargs.get("stocks"), ["2330"])
        gate_mock.assert_called_once()
        self.assertEqual(gate_mock.call_args.kwargs.get("stock_id"), "2330")

    async def test_run_report_gate_missing_csv(self) -> None:
        async with _session() as session:
            payload = _payload(
                await session.call_tool(
                    "run_report_gate",
                    {"stock_id": "9999", "trade_date": "2099-01-01"},
                )
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit_code"], EXIT_CSV_MISSING)
        self.assertIn("tw_stock_9999.csv", payload["error"] or "")

    async def test_2330_is_not_a_holding(self) -> None:
        async with _session() as session:
            payload = _payload(await session.call_tool("get_holdings", {"stock_id": "2330"}))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit_code"], EXIT_HOLDING_MISSING)

    async def test_send_digest_blocked_without_approval(self) -> None:
        async with _session() as session:
            payload = _payload(
                await session.call_tool(
                    "send_digest",
                    {
                        "digest_date": "2026-08-18",
                        "subject": "subject",
                        "main_detail_markdown": "body " * 10,
                    },
                )
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit_code"], EXIT_FAILED)
        self.assertEqual(payload["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
