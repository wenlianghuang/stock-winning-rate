"""Phase 1 MCP server: expose Phase 0 tools over stdio / HTTP.

CLI: ``python main.py mcp``. FastAPI stays for the website; this process is
the Agent-facing interface. Skill logs are redirected to stderr so stdio
JSON-RPC stays clean.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Iterator
from dataclasses import asdict, is_dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

SERVER_NAME = "stock-winning-rate"

SERVER_INSTRUCTIONS = """\
台股籌碼作業 MCP。業務邏輯在 agent.tools（與 CLI / FastAPI 同一實作），不要重跑 script。

無持股個股深度報告（Phase 1 演示，例如 2330）：
1. 可選 get_last_trading_date
2. fetch_chips(stocks=["2330"])
3. run_report_gate(stock_id="2330", skip_pdf=true)
不要對無持股標的呼叫 run_position_gate。send_digest 預設 blocked，且本 repo 不寄信。

缺 CSV 先 fetch；gate 失敗看 exit_code（20=缺 CSV，10=缺 agy，1=未通過）。
"""

TOOL_NAMES = (
    "get_last_trading_date",
    "fetch_chips",
    "build_chip_facts",
    "run_report_gate",
    "get_holdings",
    "run_position_gate",
    "run_market_daily",
    "answer_market_chat",
    "draft_digest",
    "send_digest",
)


def _opt_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _payload(result: Any) -> dict[str, Any]:
    if is_dataclass(result) and not isinstance(result, type):
        data: Any = asdict(result)
    elif isinstance(result, dict):
        data = result
    else:
        data = {"result": result}
    return json.loads(json.dumps(data, default=str))


@contextlib.contextmanager
def _stderr_stdout() -> Iterator[None]:
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _normalize_stocks(stocks: list[str] | str | None) -> list[str] | str | None:
    if stocks is None:
        return None
    if isinstance(stocks, str):
        parts = [part.strip() for part in stocks.replace("，", ",").split(",") if part.strip()]
        return parts or None
    return [str(item).strip() for item in stocks if str(item).strip()]


def build_mcp(*, host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    mcp = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        log_level="WARNING",
    )
    _register_tools(mcp)
    return mcp


def _register_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True, title="Last TWSE trading date"),
        description=(
            "查台股籌碼參考交易日（證交所日曆）。產報前可先叫，拿 trade_date 再傳給 fetch_chips / "
            "run_report_gate。無副作用。date 省略則用系統參考日；validate_market 預設 true。"
        ),
    )
    def get_last_trading_date(
        date: str | None = None,
        validate_market: bool = True,
    ) -> dict[str, Any]:
        from agent.tools.dates import get_last_trading_date as impl

        with _stderr_stdout():
            return _payload(impl(date=_opt_str(date), validate_market=validate_market))

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, title="Fetch chip CSV"),
        description=(
            "抓台股籌碼快照，寫 reports/stock/{日期}/tw_stock_{代碼}.csv。"
            "深度報告的第一步；run_report_gate 需要這份 CSV。"
            "stocks 可為 [\"2330\"] 或 \"2330\"；省略則讀 watchlist。"
            "可選 trade_date、lookback_days、chart_lookback_days、skip_major。"
        ),
    )
    def fetch_chips(
        stocks: list[str] | str | None = None,
        trade_date: str | None = None,
        lookback_days: int = 5,
        chart_lookback_days: int = 60,
        skip_major: bool = False,
    ) -> dict[str, Any]:
        from agent.tools.chips import fetch_chips as impl

        with _stderr_stdout():
            return _payload(
                impl(
                    stocks=_normalize_stocks(stocks),
                    trade_date=_opt_str(trade_date),
                    lookback_days=lookback_days,
                    chart_lookback_days=chart_lookback_days,
                    skip_major=skip_major,
                )
            )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, title="Build chip facts.json"),
        description=(
            "由既有 CSV 計算籌碼 facts 並寫 .facts.json，不跑 agy 敘事。"
            "前置：fetch_chips 已產出 CSV。提供 stock_id+trade_date 或 csv_path。"
            "缺 CSV 時 exit_code=20。"
        ),
    )
    def build_chip_facts(
        stock_id: str | None = None,
        trade_date: str | None = None,
        csv_path: str | None = None,
    ) -> dict[str, Any]:
        from agent.tools.chips import build_chip_facts as impl

        with _stderr_stdout():
            return _payload(
                impl(
                    stock_id=_opt_str(stock_id),
                    trade_date=_opt_str(trade_date),
                    csv_path=_opt_str(csv_path),
                )
            )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, title="Deep chip report gate"),
        description=(
            "單檔台股籌碼深度報告閉環（agy 敘事 + 規則驗證）。寫 md / facts / gate log。"
            "前置：該檔 CSV 必須已存在，否則 exit_code=20，請先 fetch_chips。"
            "需要 stock_id 或 csv_path。無持股也應跑此 tool；不要因此去跑 run_position_gate。"
            "預設 skip_pdf=true。validate_only 只驗證既有 md。"
        ),
    )
    def run_report_gate(
        stock_id: str | None = None,
        trade_date: str | None = None,
        csv_path: str | None = None,
        max_rounds: int = 8,
        validate_only: bool = False,
        skip_pdf: bool = True,
        prompt: str = "產出單檔台股籌碼深度分析報告",
    ) -> dict[str, Any]:
        from agent.tools.report import run_report_gate as impl

        with _stderr_stdout():
            return _payload(
                impl(
                    stock_id=_opt_str(stock_id),
                    trade_date=_opt_str(trade_date),
                    csv_path=_opt_str(csv_path),
                    max_rounds=max_rounds,
                    validate_only=validate_only,
                    skip_pdf=skip_pdf,
                    prompt=prompt,
                )
            )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True, title="Read holdings"),
        description=(
            "讀 holdings.json。決定要不要跑 run_position_gate：有均價／張數才跑。"
            "可傳 stock_id 查單檔；查無持股 exit_code=21。無副作用。"
        ),
    )
    def get_holdings(
        stock_id: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        from agent.tools.position import get_holdings as impl

        with _stderr_stdout():
            return _payload(impl(stock_id=_opt_str(stock_id), path=_opt_str(path)))

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, title="Position decision gate"),
        description=(
            "持股部位決策閉環。前置：CSV 已存在，且有均價／張數（參數或 holdings 檔）。"
            "無持股不要呼叫（例如演示用的 2330）。缺 CSV=20，缺持股=21。"
            "預設 skip_pdf=true。"
        ),
    )
    def run_position_gate(
        stock_id: str | None = None,
        trade_date: str | None = None,
        csv_path: str | None = None,
        avg_cost: float | None = None,
        share_count: int | None = None,
        from_holdings_file: bool = False,
        max_rounds: int = 8,
        validate_only: bool = False,
        skip_pdf: bool = True,
    ) -> dict[str, Any]:
        from agent.tools.position import run_position_gate as impl

        with _stderr_stdout():
            return _payload(
                impl(
                    stock_id=_opt_str(stock_id),
                    trade_date=_opt_str(trade_date),
                    csv_path=_opt_str(csv_path),
                    avg_cost=avg_cost,
                    share_count=share_count,
                    from_holdings_file=from_holdings_file,
                    max_rounds=max_rounds,
                    validate_only=validate_only,
                    skip_pdf=skip_pdf,
                )
            )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, title="Pre-open market daily"),
        description=(
            "開盤前戰術 brief（大盤＋2330＋那指費半）。全站共用，不是個股深度報告。"
            "可傳 trade_date 或 as_of。05:30 後預設必須有美股；skip_us 僅測試用，排程不會設。"
            "產物在 reports/market/{日期}/。"
        ),
    )
    def run_market_daily(
        as_of: str | None = None,
        trade_date: str | None = None,
        max_rounds: int = 6,
        skip_agy: bool = False,
        skip_fetch: bool = False,
        skip_us: bool = False,
        validate_only: bool = False,
    ) -> dict[str, Any]:
        from agent.tools.market import run_market_daily as impl

        with _stderr_stdout():
            return _payload(
                impl(
                    as_of=_opt_str(as_of),
                    trade_date=_opt_str(trade_date),
                    max_rounds=max_rounds,
                    skip_agy=skip_agy,
                    skip_fetch=skip_fetch,
                    skip_us=skip_us,
                    validate_only=validate_only,
                )
            )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True, title="Grounded market chat"),
        description=(
            "針對既有日報 facts 回答路況／外資等問題。優先讀 reports/market，不重跑 gate。"
            "需要 message；可傳 trade_date。外訊可 skip_tavily=true。"
        ),
    )
    def answer_market_chat(
        message: str,
        trade_date: str | None = None,
        has_holdings: bool = False,
        use_llm: bool = True,
        skip_tavily: bool = False,
    ) -> dict[str, Any]:
        from agent.tools.market import answer_market_chat as impl

        with _stderr_stdout():
            return _payload(
                impl(
                    message=message,
                    trade_date=_opt_str(trade_date),
                    has_holdings=has_holdings,
                    use_llm=use_llm,
                    skip_tavily=skip_tavily,
                )
            )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, title="Draft email digest"),
        description=(
            "把同日多檔報告融合成 email 草稿（不寄信）。需要 digest_date 與 items"
            "（每筆 stock_id、markdown，可選 position_markdown）。需要 agy。這不是 send。"
        ),
    )
    def draft_digest(
        digest_date: str,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from agent.tools.digest import DigestItem, draft_digest as impl

        parsed: list[DigestItem] = []
        for item in items or []:
            parsed.append(
                DigestItem(
                    stock_id=str(item.get("stock_id") or ""),
                    markdown=str(item.get("markdown") or ""),
                    stock_name=item.get("stock_name"),
                    trade_date=item.get("trade_date"),
                    position_markdown=item.get("position_markdown"),
                )
            )
        with _stderr_stdout():
            return _payload(impl(digest_date=digest_date, items=parsed))

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            title="Send digest (approval required)",
        ),
        description=(
            "寄出 digest。必須 approved=true，否則 status=blocked、exit_code=1。"
            "即使核准，本 repo 仍不發信（status=unsupported；寄信在 stock-report-site）。"
            "需要 digest_date、subject、main_detail_markdown。Agent 不可自行把 approved 設為 true。"
        ),
    )
    def send_digest(
        digest_date: str,
        subject: str,
        main_detail_markdown: str,
        approved: bool = False,
        to: str | None = None,
    ) -> dict[str, Any]:
        from agent.tools.digest import send_digest as impl

        with _stderr_stdout():
            return _payload(
                impl(
                    digest_date=digest_date,
                    subject=subject,
                    main_detail_markdown=main_detail_markdown,
                    approved=approved,
                    to=_opt_str(to),
                )
            )


def registered_tool_names(server: FastMCP | None = None) -> list[str]:
    target = server or build_mcp()
    return [tool.name for tool in target._tool_manager.list_tools()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python main.py mcp",
        description="Start the stock-winning-rate MCP server (Phase 1).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
        help="stdio for Cursor/Claude; streamable-http for MCP inspector.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print tool names and first-line descriptions, then exit.",
    )
    args = parser.parse_args(argv)

    server = build_mcp(host=args.host, port=args.port)
    if args.list_tools:
        for tool in server._tool_manager.list_tools():
            first = (tool.description or "").splitlines()[0]
            print(f"{tool.name}\t{first}")
        return 0

    if args.transport == "sse":
        print(f"MCP SSE http://{args.host}:{args.port}/sse", file=sys.stderr)
    elif args.transport == "streamable-http":
        print(f"MCP streamable-http http://{args.host}:{args.port}/mcp", file=sys.stderr)

    server.run(transport=args.transport)
    return 0


# Discovered by `mcp run agent/mcp_server.py` and MCP inspector.
mcp = build_mcp()


if __name__ == "__main__":
    raise SystemExit(main())
