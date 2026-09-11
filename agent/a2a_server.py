"""Phase 5 A2A server: one Agent Card wrapping the existing orchestrator.

CLI: ``python main.py a2a``. Copilot Studio / a2a-inspector discover
``/.well-known/agent-card.json`` and POST JSON-RPC to this process.
Domain logic stays in ``agent.tools`` / ``run_agent``; this module is the
Agent2Agent adapter, not a second chip/gate implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from a2a.helpers import (
    new_data_artifact_update_event,
    new_task_from_user_message,
    new_text_artifact_update_event,
    new_text_status_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agent.orchestrator import (
    STOCK_RE,
    AgentContext,
    AgentRun,
    format_run,
    run_agent,
    run_to_dict,
)
from agent.tools._types import EXIT_BAD_ARGS, EXIT_OK

SERVER_NAME = "stock-winning-rate"
SERVER_VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999
JSONRPC_PATH = "/"
CARD_PATH = "/.well-known/agent-card.json"

SKILL_PROCESS_HOLDINGS = "process_holdings"
SKILL_STOCK_RESEARCH = "stock_research"
SKILL_MARKET_OUTLOOK = "market_outlook"
SKILL_IDS = (SKILL_PROCESS_HOLDINGS, SKILL_STOCK_RESEARCH, SKILL_MARKET_OUTLOOK)

SERVER_DESCRIPTION = (
    "台股籌碼作業 Agent。一句話意圖交給既有 orchestrator（plan + policy + agent.tools）。"
    "無持倉不跑 position；send_digest 預設 blocked；不下單。"
    "這是 Agent2Agent 入口，不是第二套籌碼／gate。"
)

RunAgent = Callable[..., AgentRun]


class DefaultA2AVersionMiddleware(BaseHTTPMiddleware):
    """SDK treats a missing A2A-Version header as 0.3. This server speaks 1.0."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.method == "POST" and not request.headers.get("a2a-version"):
            headers = [
                (key, value)
                for key, value in request.scope.get("headers", [])
                if key.lower() != b"a2a-version"
            ]
            headers.append((b"a2a-version", b"1.0"))
            request.scope["headers"] = headers
        return await call_next(request)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Optional Authorization: Bearer. Agent Card stays public for discovery."""

    def __init__(self, app: Any, token: str | None = None) -> None:
        super().__init__(app)
        self.token = (token or "").strip() or None

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if not self.token or request.url.path == CARD_PATH:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self.token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def _base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def build_agent_card(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> AgentCard:
    base = _base_url(host, port)
    jsonrpc_url = urljoin(f"{base}/", JSONRPC_PATH.lstrip("/")) if JSONRPC_PATH != "/" else f"{base}/"
    modes_in = ["text/plain"]
    modes_out = ["text/plain", "application/json"]
    skills = [
        AgentSkill(
            id=SKILL_PROCESS_HOLDINGS,
            name="處理持股",
            description=(
                "讀 holdings → 缺 CSV 就 fetch → report-gate → 有均價／張數才 position-gate → "
                "digest 草稿。send_digest 不會自動寄出。"
            ),
            tags=["twse", "holdings", "chips"],
            examples=["幫我處理今天持股"],
            input_modes=modes_in,
            output_modes=modes_out,
        ),
        AgentSkill(
            id=SKILL_STOCK_RESEARCH,
            name="個股深度報告",
            description=(
                "單檔籌碼研究。無持倉只跑 fetch + report-gate，不跑 position。"
                "請在訊息裡給四碼代號，例如 2330。"
            ),
            tags=["twse", "research", "chips"],
            examples=["2330 要不要動", "2330"],
            input_modes=modes_in,
            output_modes=modes_out,
        ),
        AgentSkill(
            id=SKILL_MARKET_OUTLOOK,
            name="開盤前日報",
            description="開盤怎麼看：日報 facts + grounded chat。不跑持股部位、不寄信。",
            tags=["twse", "market"],
            examples=["明天開盤怎麼看"],
            input_modes=modes_in,
            output_modes=modes_out,
        ),
    ]
    return AgentCard(
        name=SERVER_NAME,
        description=SERVER_DESCRIPTION,
        version=SERVER_VERSION,
        default_input_modes=modes_in,
        default_output_modes=modes_out,
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=jsonrpc_url,
                protocol_version="1.0",
            ),
        ],
        skills=skills,
    )


def card_as_dict(card: AgentCard) -> dict[str, Any]:
    return agent_card_to_dict(card)


def _metadata_dict(message: Any) -> dict[str, Any]:
    if message is None:
        return {}
    payload = MessageToDict(message)
    meta = payload.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def resolve_intent(text: str, skill: str | None = None) -> str:
    """Map A2A message text + optional skill id to an orchestrator intent string."""
    stripped = (text or "").strip()
    skill_id = (skill or "").strip() or None
    if skill_id == SKILL_PROCESS_HOLDINGS and not stripped:
        return "幫我處理今天持股"
    if skill_id == SKILL_MARKET_OUTLOOK and not stripped:
        return "明天開盤怎麼看"
    if skill_id == SKILL_STOCK_RESEARCH and stripped:
        codes = STOCK_RE.findall(stripped)
        if codes and stripped == codes[0]:
            return f"{stripped} 要不要動"
    return stripped


def _jsonable(run: AgentRun) -> dict[str, Any]:
    return json.loads(json.dumps(run_to_dict(run), ensure_ascii=False, default=str))


def handle_intent(
    intent: str,
    *,
    dry_run: bool = False,
    trade_date: str | None = None,
    run_agent_fn: RunAgent | None = None,
) -> AgentRun:
    runner = run_agent_fn or run_agent
    ctx = AgentContext(
        intent=intent,
        trade_date=trade_date,
        skip_pdf=True,
        skip_tavily=True,
        dry_run=dry_run,
        approve_send=False,
        audit_enabled=False,
    )
    return runner(intent, ctx)


class StockAgentExecutor(AgentExecutor):
    """Single A2A runtime. Data/Research/Position stay in-process roles."""

    def __init__(
        self,
        *,
        dry_run: bool = False,
        trade_date: str | None = None,
        run_agent_fn: RunAgent | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.trade_date = trade_date
        self._run_agent = run_agent_fn or run_agent
        self._cancelled: set[str] = set()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is not None:
            task = context.current_task
        elif context.message is not None:
            task = new_task_from_user_message(context.message)
        else:
            return
        await event_queue.enqueue_event(task)

        text = context.get_user_input().strip()
        meta = _metadata_dict(context.message)
        skill = str(meta.get("skill") or meta.get("skillId") or "").strip() or None
        trade_date = str(meta.get("trade_date") or meta.get("tradeDate") or "").strip() or self.trade_date
        dry_run = self.dry_run
        if "dry_run" in meta:
            dry_run = bool(meta["dry_run"])
        elif "dryRun" in meta:
            dry_run = bool(meta["dryRun"])

        intent = resolve_intent(text, skill)
        if not intent:
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task.id,
                    task.context_id,
                    TaskState.TASK_STATE_REJECTED,
                    "請提供意圖文字，例如：2330 要不要動",
                )
            )
            return

        await event_queue.enqueue_event(
            new_text_status_update_event(
                task.id,
                task.context_id,
                TaskState.TASK_STATE_WORKING,
                "正在編成 plan 並交給 orchestrator（同一組 agent.tools）",
            )
        )

        if task.id in self._cancelled:
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task.id,
                    task.context_id,
                    TaskState.TASK_STATE_CANCELED,
                    "已取消",
                )
            )
            return

        try:
            run = await asyncio.to_thread(
                handle_intent,
                intent,
                dry_run=dry_run,
                trade_date=trade_date,
                run_agent_fn=self._run_agent,
            )
        except Exception as exc:  # noqa: BLE001 — surface any orchestrator crash as a failed task
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task.id,
                    task.context_id,
                    TaskState.TASK_STATE_FAILED,
                    f"orchestrator 失敗：{exc}",
                )
            )
            return

        if task.id in self._cancelled:
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task.id,
                    task.context_id,
                    TaskState.TASK_STATE_CANCELED,
                    "已取消",
                )
            )
            return

        summary = format_run(run)
        payload = _jsonable(run)
        await event_queue.enqueue_event(
            new_text_artifact_update_event(
                task.id,
                task.context_id,
                name="orchestrator_run",
                text=summary,
            )
        )
        await event_queue.enqueue_event(
            new_data_artifact_update_event(
                task.id,
                task.context_id,
                name="orchestrator_run_json",
                data=payload,
                media_type="application/json",
                last_chunk=True,
            )
        )
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task.id,
                task.context_id,
                TaskState.TASK_STATE_COMPLETED,
                "完成（send_digest 仍受 policy 阻擋，除非人已核准）",
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        task_id = context.task_id or (task.id if task is not None else "")
        if task_id:
            self._cancelled.add(task_id)
        if task is None:
            return
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task.id,
                task.context_id,
                TaskState.TASK_STATE_CANCELED,
                "已取消",
            )
        )


def build_app(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    dry_run: bool = False,
    trade_date: str | None = None,
    token: str | None = None,
    run_agent_fn: RunAgent | None = None,
) -> Starlette:
    card = build_agent_card(host=host, port=port)
    handler = DefaultRequestHandler(
        agent_executor=StockAgentExecutor(
            dry_run=dry_run,
            trade_date=trade_date,
            run_agent_fn=run_agent_fn,
        ),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = []
    routes.extend(create_agent_card_routes(card, card_url=CARD_PATH))
    routes.extend(
        create_jsonrpc_routes(
            handler,
            rpc_url=JSONRPC_PATH,
            enable_v0_3_compat=True,
        )
    )
    middleware = [
        Middleware(DefaultA2AVersionMiddleware),
        Middleware(BearerTokenMiddleware, token=token),
    ]
    return Starlette(routes=routes, middleware=middleware)


def listed_skill_ids(card: AgentCard | None = None) -> list[str]:
    target = card or build_agent_card()
    return [skill.id for skill in target.skills]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python main.py a2a",
        description="Start the stock-winning-rate Agent2Agent server (Phase 5).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Orchestrator 只編成 plan，不跑 fetch／gate／digest（inspector 演示用）。",
    )
    parser.add_argument("--date", dest="trade_date", default=None, help="指定交易日 YYYY-MM-DD")
    parser.add_argument(
        "--token",
        default=os.environ.get("A2A_TOKEN"),
        help="可選 Bearer token（也可用 A2A_TOKEN）。未設則本機開放；Agent Card 一律公開。",
    )
    parser.add_argument(
        "--print-card",
        action="store_true",
        help="印出 Agent Card JSON 後結束，不開 HTTP。",
    )
    parser.add_argument(
        "--list-skills",
        action="store_true",
        help="列出 skill id，不開 HTTP。",
    )
    args = parser.parse_args(argv)

    card = build_agent_card(host=args.host, port=args.port)
    if args.list_skills:
        for skill in card.skills:
            first = (skill.description or "").splitlines()[0]
            print(f"{skill.id}\t{first}")
        return EXIT_OK
    if args.print_card:
        print(json.dumps(card_as_dict(card), ensure_ascii=False, indent=2))
        return EXIT_OK

    token = (args.token or "").strip() or None
    app = build_app(
        host=args.host,
        port=args.port,
        dry_run=args.dry_run,
        trade_date=args.trade_date,
        token=token,
    )
    card_url = f"{_base_url(args.host, args.port)}{CARD_PATH}"
    rpc_url = f"{_base_url(args.host, args.port)}/"
    print(f"A2A Agent Card {card_url}", file=sys.stderr)
    print(f"A2A JSON-RPC POST {rpc_url}", file=sys.stderr)
    if args.dry_run:
        print("A2A dry-run: orchestrator 只列 plan", file=sys.stderr)
    if token:
        print("A2A Bearer token required (card stays public)", file=sys.stderr)

    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn 未安裝。請執行: uv sync --extra a2a --extra stock --extra ui",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
