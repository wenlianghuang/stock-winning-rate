"""Orchestrator: intent → structured plan → policy-checked execution.

Phase 2: rule-based planner, schema + policy, tool budget, send_digest blocked.
Phase 3: role allowlists, Research→Validator→Position handoffs, JSONL audit.

The default planner is rule-based (golden intents, no LLM). An external model
may only emit the same plan JSON (``--plan-json``); schema + policy run before
any tool. Execution failures use rules to retry once on missing CSV, skip
position without Research facts, block send_digest, and stop at the tool budget.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from agent.audit import (
    AuditLog,
    Handoff,
    default_audit_path,
    format_replay,
    gate_rounds_from_payload,
    handoffs_from_gate_rounds,
    load_audit,
    new_run_id,
    replay_audit,
)
from agent.policy import (
    ALLOWED_TOOLS,
    DEFAULT_MAX_ELAPSED_SEC,
    DEFAULT_MAX_GATE_ROUNDS,
    DEFAULT_MAX_TOOL_CALLS,
    DIGEST_NA,
    DIGEST_PENDING,
    DIGEST_SKIPPED,
    GATE_TOOLS,
    INTENT_MARKET_OUTLOOK,
    INTENT_PROCESS_HOLDINGS,
    INTENT_STOCK_DECISION,
    KNOWN_ROLES,
    NEEDS_CSV_TOOLS,
    ROLE_DATA,
    ROLE_ORCHESTRATOR,
    ROLE_RESEARCH,
    ROLE_VALIDATOR,
    Budget,
    Permissions,
    actor_for_tool,
    allow_position,
    allow_position_handoff,
    allow_send_digest,
    is_actionable_holding,
    notify_may_include,
    role_for_intent,
    should_retry_after_csv_missing,
    tool_allowed_for_role,
    validate_plan_schema,
)
from agent.tools._paths import ROOT
from agent.tools._types import EXIT_BAD_ARGS, EXIT_FAILED, EXIT_HOLDING_MISSING, EXIT_OK

Dispatch = Callable[[str, dict[str, Any]], Any]

STOCK_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")

PROCESS_MARKERS = (
    "處理今天持股",
    "今天持股",
    "處理持股",
    "幫我處理持股",
    "處理持倉",
    "今天持倉",
    "持股部位",
)
MARKET_MARKERS = (
    "明天開盤",
    "開盤怎麼看",
    "開盤前",
    "路況",
    "大盤",
    "盤勢",
    "外資怎麼",
    "那指",
    "費半",
)
DECISION_MARKERS = (
    "要不要動",
    "該不該",
    "加減碼",
    "要不要賣",
    "要不要買",
    "怎麼辦",
)


@dataclass
class ClassifiedIntent:
    kind: str
    text: str
    stocks: list[str] = field(default_factory=list)


@dataclass
class PlanStep:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    actor: str = ""


@dataclass
class Plan:
    intent: str
    intent_kind: str
    trade_date: str | None
    steps: list[PlanStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    digest_status: str = DIGEST_NA


@dataclass
class StepResult:
    tool: str
    args: dict[str, Any]
    ok: bool
    exit_code: int
    reason: str = ""
    skipped: bool = False
    skip_reason: str | None = None
    gate_passed: bool | None = None
    error: str | None = None
    summary: str = ""
    elapsed_ms: int = 0
    superseded: bool = False
    actor: str = ""
    issue_codes: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentContext:
    intent: str
    trade_date: str | None = None
    holdings: dict[str, dict[str, Any]] | None = None
    holdings_path: Path | str | None = None
    csv_missing: set[str] | None = None
    daily_facts_present: bool | None = None
    skip_pdf: bool = True
    skip_tavily: bool = True
    max_rounds: int = DEFAULT_MAX_GATE_ROUNDS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_elapsed_sec: float = DEFAULT_MAX_ELAPSED_SEC
    approve_send: bool = False
    dry_run: bool = False
    role: str | None = None
    permissions: Permissions | None = None
    skip_research: bool = False
    run_id: str | None = None
    audit_enabled: bool = False
    audit_path: Path | str | None = None


@dataclass
class AgentRun:
    intent: str
    intent_kind: str
    trade_date: str | None
    plan: Plan
    probes: list[StepResult] = field(default_factory=list)
    steps: list[StepResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    digest_status: str = DIGEST_NA
    digest_subject: str | None = None
    ok: bool = False
    exit_code: int = EXIT_FAILED
    budget_calls: int = 0
    budget_max: int = DEFAULT_MAX_TOOL_CALLS
    dry_run: bool = False
    run_id: str = ""
    role: str = ROLE_ORCHESTRATOR
    handoffs: list[Handoff] = field(default_factory=list)
    audit_path: str | None = None
    permissions: Permissions = field(default_factory=Permissions)


def classify_intent(text: str) -> ClassifiedIntent:
    stripped = (text or "").strip()
    stocks = list(dict.fromkeys(STOCK_RE.findall(stripped)))
    if any(marker in stripped for marker in PROCESS_MARKERS) or (
        "持股" in stripped and any(token in stripped for token in ("處理", "今天", "幫我"))
    ):
        return ClassifiedIntent(kind=INTENT_PROCESS_HOLDINGS, text=stripped, stocks=stocks)
    if any(marker in stripped for marker in MARKET_MARKERS):
        return ClassifiedIntent(kind=INTENT_MARKET_OUTLOOK, text=stripped, stocks=stocks)
    if stocks or any(marker in stripped for marker in DECISION_MARKERS):
        return ClassifiedIntent(kind=INTENT_STOCK_DECISION, text=stripped, stocks=stocks)
    return ClassifiedIntent(kind=INTENT_MARKET_OUTLOOK, text=stripped, stocks=stocks)


def chip_csv_missing(stock_id: str, trade_date: str | None) -> bool:
    stock_root = ROOT / "reports" / "stock"
    if trade_date:
        return not (stock_root / trade_date / f"tw_stock_{stock_id}.csv").exists()
    if not stock_root.exists():
        return True
    return not any(stock_root.glob(f"*/tw_stock_{stock_id}.csv"))


def daily_facts_exist(trade_date: str | None) -> bool:
    if not trade_date:
        return False
    path = ROOT / "reports" / "market" / trade_date / "tw_market_daily.facts.json"
    return path.is_file()


def research_facts_exist(stock_id: str, trade_date: str | None) -> bool:
    stock_root = ROOT / "reports" / "stock"
    name = f"tw_stock_{stock_id}.facts.json"
    if trade_date:
        return (stock_root / trade_date / name).is_file()
    if not stock_root.exists():
        return False
    return any(stock_root.glob(f"*/{name}"))


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "intent": plan.intent,
        "intent_kind": plan.intent_kind,
        "trade_date": plan.trade_date,
        "steps": [
            {
                "tool": step.tool,
                "args": step.args,
                "reason": step.reason,
                "actor": step.actor or actor_for_tool(step.tool),
            }
            for step in plan.steps
        ],
        "notes": list(plan.notes),
        "digest_status": plan.digest_status,
    }


def plan_from_dict(data: dict[str, Any], *, fallback_intent: str = "") -> Plan:
    errors = validate_plan_schema(data)
    if errors:
        raise ValueError("；".join(errors))
    steps = [
        PlanStep(
            tool=str(step["tool"]),
            args=dict(step.get("args") or {}),
            reason=str(step.get("reason") or ""),
            actor=str(step.get("actor") or actor_for_tool(str(step["tool"]))),
        )
        for step in data.get("steps") or []
    ]
    digest = str(data.get("digest_status") or DIGEST_NA)
    if digest not in {DIGEST_NA, DIGEST_PENDING, DIGEST_SKIPPED}:
        digest = DIGEST_NA
    return Plan(
        intent=str(data.get("intent") or fallback_intent),
        intent_kind=str(data["intent_kind"]),
        trade_date=data.get("trade_date"),
        steps=steps,
        notes=[str(note) for note in (data.get("notes") or [])],
        digest_status=digest,
    )


def apply_policy(
    plan: Plan,
    holdings: dict[str, dict[str, Any]],
    *,
    csv_missing: set[str],
    approve_send: bool = False,
    permissions: Permissions | None = None,
) -> Plan:
    """Strip illegal steps and insert fetch_chips when research would hit missing CSV."""
    perms = permissions or Permissions(role=role_for_intent(plan.intent_kind))
    notes = list(plan.notes)
    filtered: list[PlanStep] = []
    for step in plan.steps:
        if step.tool not in ALLOWED_TOOLS:
            notes.append(f"移除未知 tool：{step.tool}")
            continue
        allowed_role, role_reason = tool_allowed_for_role(step.tool, perms.role)
        if not allowed_role:
            notes.append(role_reason)
            continue
        actor = step.actor or actor_for_tool(step.tool)
        if step.tool == "run_position_gate":
            stock_id = str(step.args.get("stock_id") or "")
            allowed, reason = allow_position(stock_id, holdings)
            if not allowed:
                notes.append(reason)
                continue
        if step.tool == "send_digest":
            # Never trust approved=true from a model-produced plan.
            allowed, reason = allow_send_digest(
                approved=approve_send, permissions=perms
            )
            if not allowed:
                notes.append(reason)
                plan = Plan(
                    intent=plan.intent,
                    intent_kind=plan.intent_kind,
                    trade_date=plan.trade_date,
                    steps=plan.steps,
                    notes=plan.notes,
                    digest_status=DIGEST_PENDING,
                )
                continue
            step.args = {**step.args, "approved": True}
        filtered.append(
            PlanStep(tool=step.tool, args=step.args, reason=step.reason, actor=actor)
        )

    covered: set[str] = set()
    for step in filtered:
        if step.tool != "fetch_chips":
            continue
        stocks = step.args.get("stocks") or []
        if isinstance(stocks, str):
            stocks = [part.strip() for part in stocks.split(",") if part.strip()]
        covered.update(str(item) for item in stocks)

    need_fetch: list[str] = []
    for step in filtered:
        if step.tool not in NEEDS_CSV_TOOLS:
            continue
        stock_id = str(step.args.get("stock_id") or "").strip()
        if stock_id and stock_id in csv_missing and stock_id not in covered:
            need_fetch.append(stock_id)
            covered.add(stock_id)

    if need_fetch:
        fetch_args: dict[str, Any] = {"stocks": need_fetch}
        if plan.trade_date:
            fetch_args["trade_date"] = plan.trade_date
        insert_at = next(
            (index for index, step in enumerate(filtered) if step.tool in NEEDS_CSV_TOOLS),
            0,
        )
        filtered.insert(
            insert_at,
            PlanStep(
                tool="fetch_chips",
                args=fetch_args,
                reason="缺 CSV，先 Data 再 Research",
                actor=ROLE_DATA,
            ),
        )
        notes.append(f"已插入 fetch_chips：{', '.join(need_fetch)}")

    digest_status = plan.digest_status
    if plan.intent_kind == INTENT_PROCESS_HOLDINGS and digest_status == DIGEST_NA:
        digest_status = DIGEST_PENDING
    return Plan(
        intent=plan.intent,
        intent_kind=plan.intent_kind,
        trade_date=plan.trade_date,
        steps=filtered,
        notes=notes,
        digest_status=digest_status,
    )


def build_plan(
    classified: ClassifiedIntent,
    *,
    trade_date: str | None,
    holdings: dict[str, dict[str, Any]],
    csv_missing: set[str],
    daily_facts_present: bool,
    skip_pdf: bool = True,
    skip_tavily: bool = True,
    max_rounds: int = DEFAULT_MAX_GATE_ROUNDS,
    permissions: Permissions | None = None,
) -> Plan:
    if classified.kind == INTENT_PROCESS_HOLDINGS:
        plan = _plan_process_holdings(
            classified,
            trade_date=trade_date,
            holdings=holdings,
            csv_missing=csv_missing,
            skip_pdf=skip_pdf,
            max_rounds=max_rounds,
        )
    elif classified.kind == INTENT_STOCK_DECISION:
        plan = _plan_stock_decision(
            classified,
            trade_date=trade_date,
            holdings=holdings,
            csv_missing=csv_missing,
            skip_pdf=skip_pdf,
            max_rounds=max_rounds,
        )
    else:
        plan = _plan_market_outlook(
            classified,
            trade_date=trade_date,
            holdings=holdings,
            daily_facts_present=daily_facts_present,
            skip_tavily=skip_tavily,
            max_rounds=max_rounds,
        )
    perms = permissions or Permissions(role=role_for_intent(classified.kind))
    return apply_policy(plan, holdings, csv_missing=csv_missing, approve_send=False, permissions=perms)


def _gate_args(
    stock_id: str, trade_date: str | None, skip_pdf: bool, max_rounds: int
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "stock_id": stock_id,
        "skip_pdf": skip_pdf,
        "max_rounds": max_rounds,
    }
    if trade_date:
        args["trade_date"] = trade_date
    return args


def _plan_process_holdings(
    classified: ClassifiedIntent,
    *,
    trade_date: str | None,
    holdings: dict[str, dict[str, Any]],
    csv_missing: set[str],
    skip_pdf: bool,
    max_rounds: int,
) -> Plan:
    notes: list[str] = []
    steps: list[PlanStep] = []
    stock_ids = [sid for sid, record in holdings.items() if is_actionable_holding(record)]
    if not stock_ids:
        return Plan(
            intent=classified.text,
            intent_kind=INTENT_PROCESS_HOLDINGS,
            trade_date=trade_date,
            steps=[],
            notes=["holdings 為空或沒有均價／張數，沒有可處理的標的"],
            digest_status=DIGEST_SKIPPED,
        )

    missing = [sid for sid in stock_ids if sid in csv_missing]
    if missing:
        args: dict[str, Any] = {"stocks": missing}
        if trade_date:
            args["trade_date"] = trade_date
        steps.append(PlanStep(tool="fetch_chips", args=args, reason="持股缺 CSV，先 Data"))

    for stock_id in stock_ids:
        steps.append(
            PlanStep(
                tool="run_report_gate",
                args=_gate_args(stock_id, trade_date, skip_pdf, max_rounds),
                reason=f"{stock_id} 深度報告",
            )
        )
        allowed, reason = allow_position(stock_id, holdings)
        if allowed:
            pos_args = _gate_args(stock_id, trade_date, skip_pdf, max_rounds)
            pos_args["from_holdings_file"] = True
            steps.append(PlanStep(tool="run_position_gate", args=pos_args, reason=reason))
        else:
            notes.append(reason)

    digest_args: dict[str, Any] = {}
    if trade_date:
        digest_args["digest_date"] = trade_date
    steps.append(
        PlanStep(
            tool="draft_digest",
            args=digest_args,
            reason="融合當日報告為 email 草稿（不寄）",
        )
    )
    notes.append("send_digest 預設 blocked，草稿待核准")
    return Plan(
        intent=classified.text,
        intent_kind=INTENT_PROCESS_HOLDINGS,
        trade_date=trade_date,
        steps=steps,
        notes=notes,
        digest_status=DIGEST_PENDING,
    )


def _plan_stock_decision(
    classified: ClassifiedIntent,
    *,
    trade_date: str | None,
    holdings: dict[str, dict[str, Any]],
    csv_missing: set[str],
    skip_pdf: bool,
    max_rounds: int,
) -> Plan:
    notes: list[str] = []
    stock_id = classified.stocks[0] if classified.stocks else ""
    if not stock_id:
        return Plan(
            intent=classified.text,
            intent_kind=INTENT_STOCK_DECISION,
            trade_date=trade_date,
            notes=["無法從意圖解析股票代碼，請在句子裡加上例如 2330"],
            digest_status=DIGEST_NA,
        )

    steps: list[PlanStep] = []
    if stock_id in csv_missing:
        args: dict[str, Any] = {"stocks": [stock_id]}
        if trade_date:
            args["trade_date"] = trade_date
        steps.append(
            PlanStep(tool="fetch_chips", args=args, reason=f"{stock_id} 缺 CSV，先 Data")
        )
    steps.append(
        PlanStep(
            tool="run_report_gate",
            args=_gate_args(stock_id, trade_date, skip_pdf, max_rounds),
            reason=f"{stock_id} 只問要不要動時仍先 research",
        )
    )
    allowed, reason = allow_position(stock_id, holdings)
    if allowed:
        pos_args = _gate_args(stock_id, trade_date, skip_pdf, max_rounds)
        pos_args["from_holdings_file"] = True
        steps.append(PlanStep(tool="run_position_gate", args=pos_args, reason=reason))
    else:
        notes.append(reason)
    return Plan(
        intent=classified.text,
        intent_kind=INTENT_STOCK_DECISION,
        trade_date=trade_date,
        steps=steps,
        notes=notes,
        digest_status=DIGEST_NA,
    )


def _plan_market_outlook(
    classified: ClassifiedIntent,
    *,
    trade_date: str | None,
    holdings: dict[str, dict[str, Any]],
    daily_facts_present: bool,
    skip_tavily: bool,
    max_rounds: int,
) -> Plan:
    steps: list[PlanStep] = []
    notes: list[str] = []
    if daily_facts_present:
        notes.append("已有日報 facts，不重跑 run_market_daily")
    else:
        daily_args: dict[str, Any] = {"max_rounds": max_rounds}
        if trade_date:
            daily_args["trade_date"] = trade_date
        steps.append(
            PlanStep(
                tool="run_market_daily",
                args=daily_args,
                reason="沒有既有日報，先產開盤前 brief",
            )
        )
    chat_args: dict[str, Any] = {
        "message": classified.text,
        "skip_tavily": skip_tavily,
        "has_holdings": any(is_actionable_holding(record) for record in holdings.values()),
    }
    if trade_date:
        chat_args["trade_date"] = trade_date
    steps.append(
        PlanStep(
            tool="answer_market_chat",
            args=chat_args,
            reason="用日報 facts 回答開盤偏誤／路況",
        )
    )
    return Plan(
        intent=classified.text,
        intent_kind=INTENT_MARKET_OUTLOOK,
        trade_date=trade_date,
        steps=steps,
        notes=notes,
        digest_status=DIGEST_NA,
    )


def _result_payload(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    if isinstance(result, dict):
        return dict(result)
    return {"value": result}


def _result_ok(payload: dict[str, Any]) -> tuple[bool, int, str | None]:
    exit_code = int(payload.get("exit_code", EXIT_OK if payload.get("ok", True) else EXIT_FAILED))
    ok = bool(payload.get("ok", exit_code == EXIT_OK))
    error = payload.get("error")
    return ok, exit_code, str(error) if error else None


def _summarize(
    tool: str, payload: dict[str, Any], *, skipped: bool, skip_reason: str | None
) -> str:
    if skipped:
        return skip_reason or "skipped"
    if tool in GATE_TOOLS:
        label = "通過" if payload.get("ok") else "未通過"
        extra = payload.get("md_path") or payload.get("error") or ""
        return f"gate {label}" + (f" {extra}" if extra else "")
    if tool == "fetch_chips":
        paths = payload.get("csv_paths") or []
        return f"csv×{len(paths)}"
    if tool == "draft_digest":
        return str(payload.get("subject") or payload.get("error") or "draft")
    if tool == "send_digest":
        return str(payload.get("status") or payload.get("error") or "")
    if tool == "answer_market_chat":
        reply = str(payload.get("reply") or "")
        return reply[:80] + ("…" if len(reply) > 80 else "")
    if tool == "get_holdings":
        holdings = payload.get("holdings") or {}
        return f"{len(holdings)} 檔"
    if tool == "get_last_trading_date":
        return str(payload.get("trade_date") or payload.get("error") or "")
    return "ok" if payload.get("ok") else str(payload.get("error") or "failed")


def default_dispatch(tool: str, args: dict[str, Any]) -> Any:
    if tool == "get_last_trading_date":
        from agent.tools.dates import get_last_trading_date

        return get_last_trading_date(**args)
    if tool == "get_holdings":
        from agent.tools.position import get_holdings

        return get_holdings(**args)
    if tool == "fetch_chips":
        from agent.tools.chips import fetch_chips

        return fetch_chips(**args)
    if tool == "build_chip_facts":
        from agent.tools.chips import build_chip_facts

        return build_chip_facts(**args)
    if tool == "run_report_gate":
        from agent.tools.report import run_report_gate

        return run_report_gate(**args)
    if tool == "run_position_gate":
        from agent.tools.position import run_position_gate

        return run_position_gate(**args)
    if tool == "run_market_daily":
        from agent.tools.market import run_market_daily

        return run_market_daily(**args)
    if tool == "answer_market_chat":
        from agent.tools.market import answer_market_chat

        return answer_market_chat(**args)
    if tool == "draft_digest":
        from agent.tools.digest import DigestItem, draft_digest

        items = []
        for item in args.get("items") or []:
            if isinstance(item, DigestItem):
                items.append(item)
            else:
                items.append(DigestItem(**item))
        return draft_digest(digest_date=str(args.get("digest_date") or ""), items=items)
    if tool == "send_digest":
        from agent.tools.digest import send_digest

        return send_digest(**args)
    raise KeyError(f"未知 tool：{tool}")


def _call(
    dispatch: Dispatch,
    budget: Budget,
    tool: str,
    args: dict[str, Any],
    *,
    reason: str = "",
    skipped: bool = False,
    skip_reason: str | None = None,
) -> StepResult:
    if skipped:
        actor = actor_for_tool(tool)
        return StepResult(
            tool=tool,
            args=args,
            ok=True,
            exit_code=EXIT_OK,
            reason=reason,
            skipped=True,
            skip_reason=skip_reason,
            summary=skip_reason or "skipped",
            actor=actor,
        )
    blocked = budget.consume()
    if blocked:
        actor = actor_for_tool(tool)
        return StepResult(
            tool=tool,
            args=args,
            ok=False,
            exit_code=EXIT_FAILED,
            reason=reason,
            skipped=True,
            skip_reason=blocked,
            error=blocked,
            summary=blocked,
            actor=actor,
        )
    started = time.monotonic()
    raw = dispatch(tool, args)
    payload = _result_payload(raw)
    ok, exit_code, error = _result_ok(payload)
    gate_passed = ok if tool in GATE_TOOLS else None
    codes = payload.get("issue_codes") or []
    if not isinstance(codes, list):
        codes = []
    return StepResult(
        tool=tool,
        args=args,
        ok=ok,
        exit_code=exit_code,
        reason=reason,
        gate_passed=gate_passed,
        error=error,
        summary=_summarize(tool, payload, skipped=False, skip_reason=None),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        actor=actor_for_tool(tool),
        issue_codes=[str(code) for code in codes],
        result=payload,
    )


def _read_text(path: str | None) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    return file_path.read_text(encoding="utf-8")


def _holdings_from_payload(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = payload.get("holdings") or {}
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}


def _resolve_permissions(ctx: AgentContext, intent_kind: str) -> Permissions:
    if ctx.permissions is not None:
        perms = Permissions(
            role=ctx.role or ctx.permissions.role,
            allow_send_digest=ctx.permissions.allow_send_digest,
            allow_mutate_holdings=False,
            skip_research=ctx.skip_research or ctx.permissions.skip_research,
        )
        return perms
    return Permissions(
        role=ctx.role or role_for_intent(intent_kind),
        allow_send_digest=False,
        allow_mutate_holdings=False,
        skip_research=ctx.skip_research,
    )


def _note_handoff(
    run: AgentRun,
    *,
    frm: str,
    to: str,
    reason: str,
    stock_id: str | None = None,
    issue_codes: list[str] | None = None,
) -> Handoff:
    event = Handoff(
        frm=frm,
        to=to,
        reason=reason,
        stock_id=stock_id,
        issue_codes=list(issue_codes or []),
    )
    run.handoffs.append(event)
    return event


def _audit_step(
    audit: AuditLog | None,
    step: StepResult,
    *,
    handoff_from: str | None = None,
    handoff_to: str | None = None,
    issue_codes: list[str] | None = None,
) -> None:
    if audit is None:
        return
    audit.record_tool(
        tool=step.tool,
        args=step.args,
        ok=step.ok,
        elapsed_ms=step.elapsed_ms,
        exit_code=step.exit_code,
        skipped=step.skipped,
        skip_reason=step.skip_reason,
        summary=step.summary,
        issue_codes=issue_codes if issue_codes is not None else step.issue_codes,
        actor=step.actor or actor_for_tool(step.tool),
        handoff_from=handoff_from,
        handoff_to=handoff_to,
        stock_id=str(step.args.get("stock_id") or "") or None,
    )


def _record_research_validation(
    run: AgentRun,
    audit: AuditLog | None,
    outcome: StepResult,
) -> str:
    """Turn gate.log rounds into Validator audit lines. Returns the actor to hand off from."""
    stock_id = str(outcome.args.get("stock_id") or "") or None
    rounds = gate_rounds_from_payload(outcome.result)
    if not rounds and outcome.issue_codes:
        rounds = [
            {
                "round": 1,
                "passed": outcome.ok,
                "issue_codes": outcome.issue_codes,
            }
        ]
    if not rounds:
        return ROLE_RESEARCH
    events = handoffs_from_gate_rounds(
        rounds, research_actor=ROLE_RESEARCH, stock_id=stock_id
    )
    run.handoffs.extend(events)
    if audit is not None:
        for round_info in rounds:
            elapsed = int(float(round_info.get("duration_sec") or 0) * 1000)
            audit.record_validator_round(
                stock_id=stock_id,
                gate_round=round_info.get("round"),
                issue_codes=list(round_info.get("issue_codes") or []),
                passed=bool(round_info.get("passed")),
                research_actor=ROLE_RESEARCH,
                elapsed_ms=elapsed,
            )
    last = rounds[-1]
    if last.get("passed"):
        return ROLE_VALIDATOR
    return ROLE_VALIDATOR


def _mark_research_artifact(
    artifacts: dict[str, dict[str, Any]],
    report_ok: dict[str, bool],
    facts_ready: dict[str, bool],
    outcome: StepResult,
    trade_date: str | None,
) -> None:
    stock_id = str(outcome.args.get("stock_id") or "")
    if not stock_id:
        return
    report_ok[stock_id] = outcome.ok
    facts_path = outcome.result.get("facts_path")
    csv_path = outcome.result.get("csv_path")
    on_disk = research_facts_exist(stock_id, outcome.result.get("trade_date") or trade_date)
    if facts_path:
        on_disk = on_disk or Path(str(facts_path)).is_file()
    if csv_path:
        on_disk = on_disk or Path(str(csv_path)).with_suffix(".facts.json").is_file()
    facts_ready[stock_id] = bool(outcome.ok or on_disk)
    artifacts.setdefault(stock_id, {})
    artifacts[stock_id]["trade_date"] = outcome.result.get("trade_date") or trade_date
    artifacts[stock_id]["passed"] = bool(outcome.ok)
    if notify_may_include(report_passed=outcome.ok):
        markdown = _read_text(outcome.result.get("md_path"))
        if not markdown:
            markdown = str(outcome.result.get("markdown") or "").strip()
        if not markdown:
            markdown = f"（{stock_id} 報告已通過 gate）"
        artifacts[stock_id]["markdown"] = markdown
    else:
        artifacts[stock_id].pop("markdown", None)


def run_agent(
    intent: str,
    context: AgentContext | None = None,
    *,
    dispatch: Dispatch | None = None,
    plan: Plan | None = None,
) -> AgentRun:
    ctx = context or AgentContext(intent=intent)
    if not ctx.intent:
        ctx.intent = intent
    runner = dispatch or default_dispatch
    budget = Budget(max_calls=ctx.max_tool_calls, max_elapsed_sec=ctx.max_elapsed_sec)
    probes: list[StepResult] = []

    trade_date = ctx.trade_date
    if not trade_date:
        probe = _call(runner, budget, "get_last_trading_date", {}, reason="解析籌碼交易日")
        probes.append(probe)
        if probe.ok:
            trade_date = probe.result.get("trade_date") or trade_date

    holdings = ctx.holdings
    if holdings is None:
        hold_args: dict[str, Any] = {}
        if ctx.holdings_path:
            hold_args["path"] = str(ctx.holdings_path)
        probe = _call(runner, budget, "get_holdings", hold_args, reason="讀持股")
        probes.append(probe)
        holdings = _holdings_from_payload(probe.result) if probe.ok else {}

    classified = classify_intent(ctx.intent)
    perms = _resolve_permissions(ctx, classified.kind)
    stock_universe = list(holdings)
    if classified.stocks:
        stock_universe = list(dict.fromkeys([*classified.stocks, *stock_universe]))
    if ctx.csv_missing is None:
        csv_missing = {sid for sid in stock_universe if chip_csv_missing(sid, trade_date)}
    else:
        csv_missing = set(ctx.csv_missing)

    if ctx.daily_facts_present is None:
        daily_present = daily_facts_exist(trade_date)
    else:
        daily_present = ctx.daily_facts_present

    if plan is None:
        built = build_plan(
            classified,
            trade_date=trade_date,
            holdings=holdings,
            csv_missing=csv_missing,
            daily_facts_present=daily_present,
            skip_pdf=ctx.skip_pdf,
            skip_tavily=ctx.skip_tavily,
            max_rounds=ctx.max_rounds,
            permissions=perms,
        )
    else:
        if not plan.trade_date:
            plan.trade_date = trade_date
        built = apply_policy(
            plan,
            holdings,
            csv_missing=csv_missing,
            approve_send=ctx.approve_send,
            permissions=perms,
        )

    run_id = ctx.run_id or new_run_id()
    audit: AuditLog | None = None
    audit_path: Path | None = None
    if ctx.audit_enabled:
        audit_path = (
            Path(ctx.audit_path) if ctx.audit_path else default_audit_path(run_id, trade_date)
        )
        audit = AuditLog(run_id, audit_path)
        for probe in probes:
            _audit_step(audit, probe)

    run = AgentRun(
        intent=ctx.intent,
        intent_kind=built.intent_kind,
        trade_date=trade_date,
        plan=built,
        probes=probes,
        notes=list(built.notes),
        digest_status=built.digest_status,
        budget_max=ctx.max_tool_calls,
        dry_run=ctx.dry_run,
        run_id=run_id,
        role=perms.role,
        audit_path=str(audit_path) if audit_path else None,
        permissions=perms,
    )

    if ctx.dry_run:
        run.ok = True
        run.exit_code = EXIT_OK
        run.budget_calls = budget.calls
        if built.intent_kind == INTENT_PROCESS_HOLDINGS and not built.steps:
            run.ok = False
            run.exit_code = EXIT_HOLDING_MISSING
        return run

    artifacts: dict[str, dict[str, Any]] = {}
    report_ok: dict[str, bool] = {}
    facts_ready: dict[str, bool] = {}
    fetched: set[str] = set()
    step_results: list[StepResult] = []
    fatal_budget = False
    previous_actor = ROLE_ORCHESTRATOR

    def _append(step_result: StepResult, *, handoff_from: str | None = None) -> StepResult:
        step_results.append(step_result)
        dest = step_result.actor or actor_for_tool(step_result.tool)
        source = handoff_from
        if (
            source is None
            and not step_result.skipped
            and previous_actor
            and dest
            and previous_actor != dest
        ):
            source = previous_actor
            _note_handoff(
                run,
                frm=source,
                to=dest,
                reason=step_result.reason or step_result.tool,
                stock_id=str(step_result.args.get("stock_id") or "") or None,
            )
        _audit_step(
            audit,
            step_result,
            handoff_from=source,
            handoff_to=dest if source else None,
        )
        return step_result

    for step in built.steps:
        allowed_role, role_reason = tool_allowed_for_role(step.tool, perms.role)
        if not allowed_role:
            skipped = _call(
                runner,
                budget,
                step.tool,
                step.args,
                reason=step.reason,
                skipped=True,
                skip_reason=role_reason,
            )
            _append(skipped)
            run.notes.append(role_reason)
            continue

        if fatal_budget:
            _append(
                _call(
                    runner,
                    budget,
                    step.tool,
                    step.args,
                    reason=step.reason,
                    skipped=True,
                    skip_reason="tool budget 已用盡，停止後續步驟",
                )
            )
            continue

        if step.tool == "run_position_gate":
            stock_id = str(step.args.get("stock_id") or "")
            ready = facts_ready.get(stock_id, False) or research_facts_exist(
                stock_id, trade_date
            )
            allowed, reason = allow_position_handoff(
                stock_id,
                holdings,
                report_ok=report_ok.get(stock_id, False),
                facts_ready=ready,
                skip_research=perms.skip_research,
            )
            if not allowed:
                skipped = _call(
                    runner,
                    budget,
                    step.tool,
                    step.args,
                    reason=step.reason,
                    skipped=True,
                    skip_reason=reason,
                )
                _append(skipped)
                run.notes.append(reason)
                continue

        if step.tool == "draft_digest":
            items = _digest_items(artifacts, trade_date)
            if not items:
                skip_reason = "沒有已通過 gate 的成品，Notify 略過 draft_digest"
                skipped = _call(
                    runner,
                    budget,
                    step.tool,
                    step.args,
                    reason=step.reason,
                    skipped=True,
                    skip_reason=skip_reason,
                )
                _append(skipped)
                run.notes.append(skip_reason)
                run.digest_status = DIGEST_SKIPPED
                continue
            step.args = {
                **step.args,
                "items": items,
                "digest_date": trade_date or step.args.get("digest_date"),
            }

        if step.tool == "send_digest":
            allowed, reason = allow_send_digest(
                approved=ctx.approve_send, permissions=perms
            )
            if not allowed:
                skipped = _call(
                    runner,
                    budget,
                    step.tool,
                    {**step.args, "approved": False},
                    reason=step.reason,
                    skipped=True,
                    skip_reason=reason,
                )
                _append(skipped)
                run.digest_status = DIGEST_PENDING
                run.notes.append(reason)
                continue

        outcome = _call(runner, budget, step.tool, step.args, reason=step.reason)
        if outcome.skip_reason and "budget" in (outcome.skip_reason or ""):
            fatal_budget = True
        _append(outcome)
        if not outcome.skipped:
            previous_actor = outcome.actor or actor_for_tool(outcome.tool)

        if outcome.tool == "fetch_chips" and outcome.ok:
            stocks = outcome.args.get("stocks") or []
            fetched.update(str(item) for item in stocks)
            csv_missing.difference_update(fetched)

        if outcome.tool == "run_report_gate":
            _mark_research_artifact(artifacts, report_ok, facts_ready, outcome, trade_date)
            previous_actor = _record_research_validation(run, audit, outcome)

        if outcome.tool == "run_position_gate" and outcome.ok:
            stock_id = str(outcome.args.get("stock_id") or "")
            artifacts.setdefault(stock_id, {})
            if artifacts.get(stock_id, {}).get("passed"):
                artifacts[stock_id]["position_markdown"] = _read_text(
                    outcome.result.get("md_path")
                )

        if should_retry_after_csv_missing(outcome.tool, outcome.exit_code) and not outcome.skipped:
            stock_id = str(outcome.args.get("stock_id") or "")
            if stock_id and stock_id not in fetched:
                fetch_args: dict[str, Any] = {"stocks": [stock_id]}
                if trade_date:
                    fetch_args["trade_date"] = trade_date
                fetch_outcome = _call(
                    runner,
                    budget,
                    "fetch_chips",
                    fetch_args,
                    reason="執行時缺 CSV，插入 Data 後重試",
                )
                _append(fetch_outcome, handoff_from=previous_actor)
                if fetch_outcome.ok:
                    fetched.add(stock_id)
                    csv_missing.discard(stock_id)
                    previous_actor = ROLE_DATA
                    outcome.superseded = True
                    retry = _call(
                        runner,
                        budget,
                        step.tool,
                        step.args,
                        reason=f"{step.reason}（CSV 補齊後重試）",
                    )
                    _append(retry, handoff_from=ROLE_DATA)
                    outcome = retry
                    if not retry.skipped:
                        previous_actor = retry.actor or actor_for_tool(retry.tool)
                    if retry.tool == "run_report_gate":
                        _mark_research_artifact(
                            artifacts, report_ok, facts_ready, retry, trade_date
                        )
                        previous_actor = _record_research_validation(run, audit, retry)

        if outcome.tool == "draft_digest" and outcome.ok:
            run.digest_subject = outcome.result.get("subject")
            run.digest_status = DIGEST_PENDING
            run.notes.append("digest 草稿已產出，send_digest 權限關閉（待核准，本 repo 不寄信）")

    run.steps = step_results
    run.budget_calls = budget.calls
    run.ok, run.exit_code = _run_exit(run)
    return run


def _digest_items(
    artifacts: dict[str, dict[str, Any]], trade_date: str | None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for stock_id, payload in artifacts.items():
        if not notify_may_include(report_passed=bool(payload.get("passed"))):
            continue
        markdown = str(payload.get("markdown") or "").strip()
        if not markdown:
            continue
        items.append(
            {
                "stock_id": stock_id,
                "markdown": markdown,
                "trade_date": payload.get("trade_date") or trade_date,
                "position_markdown": payload.get("position_markdown"),
            }
        )
    return items


def _run_exit(run: AgentRun) -> tuple[bool, int]:
    if run.intent_kind == INTENT_PROCESS_HOLDINGS and not run.plan.steps:
        return False, EXIT_HOLDING_MISSING
    executed = [step for step in run.steps if not step.skipped and not step.superseded]
    if any("budget" in (step.skip_reason or "") for step in run.steps):
        return False, EXIT_FAILED
    if executed and any(not step.ok for step in executed):
        return False, EXIT_FAILED
    if run.intent_kind == INTENT_STOCK_DECISION and not run.plan.steps:
        return False, EXIT_BAD_ARGS
    return True, EXIT_OK


def format_run(run: AgentRun) -> str:
    lines = [
        f"意圖: {run.intent}",
        f"種類: {run.intent_kind}",
        f"交易日: {run.trade_date or '（未定）'}",
    ]
    if run.run_id:
        lines.append(f"run_id: {run.run_id}")
    lines.append(f"角色: {run.role}")
    send_flag = "on" if run.permissions.allow_send_digest else "off"
    skip_flag = "on" if run.permissions.skip_research else "off"
    lines.append(f"權限: send_digest={send_flag}  skip_research={skip_flag}")
    if run.dry_run:
        lines.append("模式: dry-run（只列 plan，不跑 fetch／gate／digest）")
    lines.append("")
    lines.append("Plan:")
    if not run.plan.steps:
        lines.append("  （沒有步驟）")
    for index, step in enumerate(run.plan.steps, start=1):
        args = _short_args(step.args)
        extra = f"  {args}" if args else ""
        actor = f"[{step.actor}] " if step.actor else ""
        lines.append(f"  {index}. {actor}{step.tool}{extra}")
        if step.reason:
            lines.append(f"      {step.reason}")
    if run.plan.notes:
        lines.append("")
        lines.append("Notes:")
        for note in run.plan.notes:
            lines.append(f"  - {note}")
    if run.digest_status == DIGEST_PENDING:
        lines.append("  - send_digest: blocked（待核准）")

    if run.handoffs:
        lines.append("")
        lines.append("Handoffs:")
        for index, event in enumerate(run.handoffs, start=1):
            codes = f"  issue_codes={event.issue_codes}" if event.issue_codes else ""
            stock = f"  {event.stock_id}" if event.stock_id else ""
            lines.append(f"  {index}. {event.frm} → {event.to}{stock}  {event.reason}{codes}")

    if run.probes:
        lines.append("")
        lines.append("Probes:")
        for index, step in enumerate(run.probes, start=1):
            lines.append(_format_step_line(index, step))

    if not run.dry_run:
        lines.append("")
        lines.append("執行:")
        if not run.steps:
            lines.append("  （沒有執行）")
        for index, step in enumerate(run.steps, start=1):
            lines.append(_format_step_line(index, step))

    lines.append("")
    status = "ok" if run.ok else "failed"
    lines.append(
        f"結束: {status}  exit={run.exit_code}  tools={run.budget_calls}/{run.budget_max}"
        f"  digest={run.digest_status}"
    )
    if run.digest_subject:
        lines.append(f"草稿主旨: {run.digest_subject}")
    if run.audit_path:
        lines.append(f"Audit: {run.audit_path}")
    return "\n".join(lines)


def _short_args(args: dict[str, Any]) -> str:
    skip = {"items", "prompt", "markdown", "main_detail_markdown"}
    parts = []
    for key, value in args.items():
        if key in skip:
            continue
        parts.append(f"{key}={value!r}")
    return " ".join(parts)


def _format_step_line(index: int, step: StepResult) -> str:
    if step.skipped:
        flag = "SKIP"
    elif step.gate_passed is True:
        flag = "PASS"
    elif step.gate_passed is False:
        flag = "FAIL"
    elif step.ok:
        flag = "ok"
    else:
        flag = "FAIL"
    elapsed = f" {step.elapsed_ms}ms" if step.elapsed_ms else ""
    actor = f"[{step.actor}] " if step.actor else ""
    return f"  {index}. {actor}{step.tool:<22} {flag}{elapsed}  {step.summary}"


def run_to_dict(run: AgentRun) -> dict[str, Any]:
    return {
        "intent": run.intent,
        "intent_kind": run.intent_kind,
        "trade_date": run.trade_date,
        "plan": plan_to_dict(run.plan),
        "probes": [asdict(step) for step in run.probes],
        "steps": [asdict(step) for step in run.steps],
        "notes": run.notes,
        "digest_status": run.digest_status,
        "digest_subject": run.digest_subject,
        "ok": run.ok,
        "exit_code": run.exit_code,
        "budget_calls": run.budget_calls,
        "budget_max": run.budget_max,
        "dry_run": run.dry_run,
        "run_id": run.run_id,
        "role": run.role,
        "audit_path": run.audit_path,
        "permissions": {
            "role": run.permissions.role,
            "allow_send_digest": run.permissions.allow_send_digest,
            "skip_research": run.permissions.skip_research,
        },
        "handoffs": [
            {
                "from": event.frm,
                "to": event.to,
                "reason": event.reason,
                "stock_id": event.stock_id,
                "issue_codes": event.issue_codes,
            }
            for event in run.handoffs
        ],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python main.py agent",
        description="意圖 → plan → 執行 Phase 0 tools。send_digest 預設不寄；寫 JSONL audit。",
    )
    parser.add_argument("intent", nargs="*", help="自然語言意圖，例如：幫我處理今天持股")
    parser.add_argument("--date", dest="trade_date", default=None, help="指定交易日 YYYY-MM-DD")
    parser.add_argument("--holdings", dest="holdings_path", default=None, help="holdings.json 路徑")
    parser.add_argument("--dry-run", action="store_true", help="只印 plan，不跑 fetch／gate／digest")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 而不是文字")
    parser.add_argument(
        "--plan-json",
        default=None,
        help="讀 LLM／外部產生的 plan JSON（- 代表 stdin），通過 schema 後才執行",
    )
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    parser.add_argument("--max-elapsed", type=float, default=DEFAULT_MAX_ELAPSED_SEC)
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_GATE_ROUNDS)
    parser.add_argument("--skip-pdf", action="store_true", default=True)
    parser.add_argument("--tavily", action="store_true", help="允許 answer_market_chat 使用 Tavily")
    parser.add_argument(
        "--role",
        default=None,
        choices=sorted(KNOWN_ROLES),
        help="覆蓋角色 allowlist（預設依意圖：路況=chat，其餘=orchestrator）",
    )
    parser.add_argument(
        "--skip-research",
        action="store_true",
        help="Position 不要求本輪 Research facts（仍要有持倉）",
    )
    parser.add_argument("--run-id", dest="run_id", default=None, help="指定 audit run_id")
    parser.add_argument(
        "--audit-dir",
        default=None,
        help="audit JSONL 目錄；預設 reports/agent/{交易日}/",
    )
    parser.add_argument("--no-audit", action="store_true", help="不寫 audit JSONL")
    parser.add_argument(
        "--replay",
        default=None,
        help="重放既有 audit JSONL（不執行 tools）",
    )
    return parser.parse_args(argv)


def _load_plan_json(source: str, intent: str) -> Plan:
    if source == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(source).read_text(encoding="utf-8")
    data = json.loads(raw)
    return plan_from_dict(data, fallback_intent=intent)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    args = _parse_args(argv)
    if args.replay:
        try:
            records = load_audit(args.replay)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: 無法重放 audit：{exc}", file=sys.stderr)
            return EXIT_BAD_ARGS
        payload = replay_audit(records)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_replay(payload))
        return EXIT_OK

    intent = " ".join(args.intent).strip()
    if not intent and not args.plan_json:
        print(
            "ERROR: 請提供意圖，例如 python main.py agent -- 「幫我處理今天持股」",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS

    external_plan = None
    if args.plan_json:
        try:
            external_plan = _load_plan_json(args.plan_json, intent)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"ERROR: plan JSON 無效：{exc}", file=sys.stderr)
            return EXIT_BAD_ARGS
        if not intent:
            intent = external_plan.intent

    audit_path = None
    if args.audit_dir:
        run_id = args.run_id or new_run_id()
        day = args.trade_date or "undated"
        audit_path = Path(args.audit_dir) / day / f"run_{run_id}.jsonl"
    else:
        run_id = args.run_id

    ctx = AgentContext(
        intent=intent,
        trade_date=args.trade_date,
        holdings_path=args.holdings_path,
        skip_pdf=args.skip_pdf,
        skip_tavily=not args.tavily,
        max_rounds=max(1, args.max_rounds),
        max_tool_calls=max(1, args.max_calls),
        max_elapsed_sec=max(1.0, args.max_elapsed),
        dry_run=args.dry_run,
        role=args.role,
        skip_research=args.skip_research,
        run_id=run_id,
        audit_enabled=(not args.no_audit) and (bool(args.audit_dir) or not args.dry_run),
        audit_path=audit_path,
    )
    run = run_agent(intent, ctx, plan=external_plan)
    if args.json:
        print(json.dumps(run_to_dict(run), ensure_ascii=False, indent=2, default=str))
    else:
        print(format_run(run))
    return int(run.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
