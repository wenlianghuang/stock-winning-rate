"""Website peer-decision graph.

Runs inside a FastAPI job. Nodes call existing tools. Report and position
narrative stay inside their gates. The comparison letter may ask the model
to explain the finished stance, then the graph stops before send. MCP and A2A
do not import this module.
"""

from __future__ import annotations

import contextvars
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent.peer_letter import STANCE_ZH, attach_peer_reading
from agent.policy import Permissions, allow_position, allow_send_digest
from agent.tools._types import EXIT_FAILED, EXIT_OK

StatusListener = Callable[[str], None]
_listener: contextvars.ContextVar[StatusListener | None] = contextvars.ContextVar(
    "peer_graph_listener",
    default=None,
)


def _emit(status: str) -> None:
    listener = _listener.get()
    if listener is not None:
        listener(status)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with(state: dict[str, Any], **updates: Any) -> dict[str, Any]:
    """Return the full state. LangGraph replaces state with the node output."""
    merged = dict(state)
    merged.update(updates)
    return merged


def _notes(state: dict[str, Any], *extra: str) -> list[str]:
    items = list(state.get("notes") or [])
    items.extend(note for note in extra if note)
    return items


def _steps(state: dict[str, Any], node: str, *, ok: bool, detail: str) -> list[dict[str, Any]]:
    items = list(state.get("steps") or [])
    items.append({"node": node, "ok": ok, "detail": detail})
    return items


class PeerGraphIO:
    """Side effects. Tests pass a fake; the website job uses the defaults."""

    def prepare_facts(self, stock_id: str, trade_date: str | None, *, fetch: bool) -> dict[str, Any]:
        from agent.tools._paths import ROOT, ensure_paths

        ensure_paths()
        from stock_paths import find_stock_artifact

        existing = find_stock_artifact(
            stock_id,
            f"tw_stock_{stock_id}.facts.json",
            trade_date=trade_date,
            root=ROOT,
        )
        if existing is not None:
            resolved = (
                existing.parent.parent.name
                if existing.parent.name == stock_id
                else existing.parent.name
            )
            return {
                "ok": True,
                "exit_code": EXIT_OK,
                "error": None,
                "skipped": True,
                "trade_date": resolved,
                "facts_path": str(existing),
            }
        if not fetch:
            return {
                "ok": False,
                "exit_code": 20,
                "error": f"{stock_id} 沒有 facts。請先點同業比較，或允許抓取。",
                "skipped": False,
                "trade_date": trade_date,
                "facts_path": None,
            }
        from agent.tools.chips import (
            BuildChipFactsInput,
            FetchChipsInput,
            build_chip_facts,
            fetch_chips,
        )

        fetched = fetch_chips(FetchChipsInput(stocks=[stock_id], trade_date=trade_date))
        if not fetched.ok:
            return {
                "ok": False,
                "exit_code": fetched.exit_code,
                "error": fetched.error or f"{stock_id} 抓取失敗",
                "skipped": False,
                "trade_date": trade_date,
                "facts_path": None,
            }
        resolved = fetched.trade_date or trade_date
        built = build_chip_facts(BuildChipFactsInput(stock_id=stock_id, trade_date=resolved))
        return {
            "ok": built.ok,
            "exit_code": built.exit_code,
            "error": built.error,
            "skipped": False,
            "trade_date": built.trade_date or resolved,
            "facts_path": built.facts_path,
        }

    def research(self, stock_id: str, trade_date: str | None, *, skip_pdf: bool) -> dict[str, Any]:
        from agent.tools._paths import ROOT, ensure_paths

        ensure_paths()
        from stock_paths import find_stock_artifact

        md_path = find_stock_artifact(
            stock_id,
            f"tw_stock_{stock_id}.md",
            trade_date=trade_date,
            root=ROOT,
        )
        facts_path = find_stock_artifact(
            stock_id,
            f"tw_stock_{stock_id}.facts.json",
            trade_date=trade_date,
            root=ROOT,
        )
        if md_path is not None and facts_path is not None:
            return {
                "ok": True,
                "exit_code": EXIT_OK,
                "error": None,
                "skipped": True,
                "md_path": str(md_path),
            }
        from agent.tools.report import ReportGateInput, run_report_gate

        result = run_report_gate(
            ReportGateInput(stock_id=stock_id, trade_date=trade_date, skip_pdf=skip_pdf)
        )
        return {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "error": result.error,
            "skipped": False,
            "md_path": result.md_path,
        }

    def compare(self, stock_id: str, peer_id: str, trade_date: str | None) -> dict[str, Any]:
        from agent.tools.compare import ComparePeerInput, compare_concept_peer

        result = compare_concept_peer(
            ComparePeerInput(stock_id=stock_id, peer_id=peer_id, trade_date=trade_date)
        )
        return {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "error": result.error,
            "label": result.label,
            "base_weaker": result.base_weaker,
            "reasons": list(result.reasons or []),
            "highlights": list(result.highlights or []),
            "themes": list(result.themes or []),
            "stock_name": result.stock_name,
            "peer_name": result.peer_name,
            "trade_date": result.trade_date,
        }

    def read_text(self, path: str | None) -> str:
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    def complete_reading(self, prompt: str) -> str:
        from agent.llm import complete

        return complete(prompt)

    def position(
        self,
        stock_id: str,
        trade_date: str | None,
        *,
        avg_cost: float | None,
        share_count: int | None,
        skip_pdf: bool,
    ) -> dict[str, Any]:
        from agent.tools._paths import ROOT, ensure_paths

        ensure_paths()
        from stock_paths import find_stock_artifact

        existing = find_stock_artifact(
            stock_id,
            f"tw_stock_{stock_id}_position.md",
            trade_date=trade_date,
            root=ROOT,
        )
        if existing is not None:
            return {
                "ok": True,
                "exit_code": EXIT_OK,
                "error": None,
                "skipped": True,
                "md_path": str(existing),
            }
        from agent.tools.position import PositionGateInput, run_position_gate

        result = run_position_gate(
            PositionGateInput(
                stock_id=stock_id,
                trade_date=trade_date,
                avg_cost=avg_cost,
                share_count=share_count,
                skip_pdf=skip_pdf,
            )
        )
        return {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "error": result.error,
            "skipped": False,
            "md_path": result.md_path,
        }

    def write_peer_draft(
        self,
        *,
        digest_date: str,
        stock_id: str,
        peer_id: str,
        subject: str,
        body: str,
    ) -> str:
        from datetime import date

        from agent.tools._paths import ROOT
        from agent.tools.digest import write_digest_markdown

        day = (digest_date or "").strip() or date.today().isoformat()
        path = ROOT / "reports" / "digest" / day / f"peer_{stock_id}_{peer_id}.md"
        write_digest_markdown(path, digest_date=day, subject=subject, body=body)
        return str(path.resolve())


def _holding_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not state.get("is_holding"):
        return {}
    stock_id = str(state.get("stock_id") or "")
    shares = int(state.get("share_count") or 0)
    return {
        stock_id: {
            "avg_cost": state.get("avg_cost") or 0,
            "shares": shares,
            "share_count": shares,
        }
    }


def _position_decision(state: dict[str, Any]) -> tuple[bool, str | None]:
    if not state.get("base_weaker"):
        return False, "本檔並未弱於同業，不跑部位"
    allowed, reason = allow_position(str(state.get("stock_id") or ""), _holding_map(state))
    if not allowed:
        return False, reason
    return True, None


def data_node(state: dict[str, Any], io: PeerGraphIO) -> dict[str, Any]:
    _emit("fetching")
    fetch = bool(state.get("fetch"))
    trade_date = state.get("trade_date")
    base = io.prepare_facts(str(state["stock_id"]), trade_date, fetch=fetch)
    if not base.get("ok"):
        detail = str(base.get("error") or "本檔資料失敗")
        return _with(
            state,
            halt=True,
            exit_code=int(base.get("exit_code") or EXIT_FAILED),
            error=detail,
            notes=_notes(state, detail),
            steps=_steps(state, "data", ok=False, detail=detail),
        )
    resolved = base.get("trade_date") or trade_date
    peer = io.prepare_facts(str(state["peer_id"]), resolved, fetch=fetch)
    if not peer.get("ok"):
        detail = str(peer.get("error") or "對照檔資料失敗")
        return _with(
            state,
            halt=True,
            trade_date=resolved,
            exit_code=int(peer.get("exit_code") or EXIT_FAILED),
            error=detail,
            notes=_notes(state, detail),
            steps=_steps(state, "data", ok=False, detail=detail),
        )
    base_note = "本檔已有 facts" if base.get("skipped") else "本檔已抓取並計算 facts"
    peer_note = "對照檔已有 facts" if peer.get("skipped") else "對照檔已抓取並計算 facts"
    detail = f"{base_note}；{peer_note}"
    return _with(
        state,
        halt=False,
        trade_date=resolved,
        exit_code=EXIT_OK,
        error=None,
        notes=_notes(state, detail),
        steps=_steps(state, "data", ok=True, detail=detail),
    )


def research_node(state: dict[str, Any], io: PeerGraphIO) -> dict[str, Any]:
    _emit("gating")
    result = io.research(
        str(state["stock_id"]),
        state.get("trade_date"),
        skip_pdf=bool(state.get("skip_pdf", True)),
    )
    if not result.get("ok"):
        detail = str(result.get("error") or "研究報告未通過")
        return _with(
            state,
            halt=True,
            exit_code=int(result.get("exit_code") or EXIT_FAILED),
            error=detail,
            notes=_notes(state, detail),
            steps=_steps(state, "research", ok=False, detail=detail),
        )
    detail = "已有研究報告，不重跑敘事" if result.get("skipped") else "研究報告已寫完"
    return _with(
        state,
        halt=False,
        md_path=result.get("md_path"),
        exit_code=EXIT_OK,
        error=None,
        notes=_notes(state, detail),
        steps=_steps(state, "research", ok=True, detail=detail),
    )


def compare_node(state: dict[str, Any], io: PeerGraphIO) -> dict[str, Any]:
    _emit("comparing")
    result = io.compare(str(state["stock_id"]), str(state["peer_id"]), state.get("trade_date"))
    if not result.get("ok"):
        detail = str(result.get("error") or "同業比較失敗")
        return _with(
            state,
            halt=True,
            exit_code=int(result.get("exit_code") or EXIT_FAILED),
            error=detail,
            notes=_notes(state, detail),
            steps=_steps(state, "compare", ok=False, detail=detail),
        )
    merged = dict(state)
    merged["base_weaker"] = bool(result.get("base_weaker"))
    run_position, skip_reason = _position_decision(merged)
    reasons = "；".join(result.get("reasons") or [])
    label = str(result.get("label") or "unknown")
    label_zh = STANCE_ZH.get(label, label)
    detail = f"相對同業：{label_zh}" + (f"（{reasons}）" if reasons else "")
    notes = _notes(state, detail)
    if skip_reason:
        notes.append(skip_reason)
    return _with(
        state,
        halt=False,
        base_weaker=bool(result.get("base_weaker")),
        run_position=run_position,
        position_skipped_reason=skip_reason,
        comparison={
            "label": label,
            "base_weaker": bool(result.get("base_weaker")),
            "reasons": list(result.get("reasons") or []),
            "highlights": list(result.get("highlights") or []),
            "themes": list(result.get("themes") or []),
            "stock_name": result.get("stock_name"),
            "peer_name": result.get("peer_name"),
            "trade_date": result.get("trade_date") or state.get("trade_date"),
        },
        exit_code=EXIT_OK,
        error=None,
        notes=notes,
        steps=_steps(state, "compare", ok=True, detail=detail),
    )


def position_node(state: dict[str, Any], io: PeerGraphIO) -> dict[str, Any]:
    _emit("positioning")
    result = io.position(
        str(state["stock_id"]),
        state.get("trade_date"),
        avg_cost=state.get("avg_cost"),
        share_count=state.get("share_count"),
        skip_pdf=bool(state.get("skip_pdf", True)),
    )
    if not result.get("ok"):
        detail = str(result.get("error") or "部位報告未通過")
        return _with(
            state,
            position_entered=True,
            position_md_path=None,
            exit_code=int(result.get("exit_code") or EXIT_FAILED),
            error=detail,
            notes=_notes(state, detail),
            steps=_steps(state, "position", ok=False, detail=detail),
        )
    detail = "已有部位報告，不重跑敘事" if result.get("skipped") else "部位報告已寫完"
    return _with(
        state,
        position_entered=True,
        position_md_path=result.get("md_path"),
        exit_code=EXIT_OK,
        error=None,
        notes=_notes(state, detail),
        steps=_steps(state, "position", ok=True, detail=detail),
    )


def draft_node(state: dict[str, Any], io: PeerGraphIO) -> dict[str, Any]:
    _emit("drafting")
    subject, body, reading_detail = attach_peer_reading(
        state,
        complete=io.complete_reading,
        read_text=io.read_text,
    )
    trade_date = str(state.get("trade_date") or "")
    try:
        path = io.write_peer_draft(
            digest_date=trade_date,
            stock_id=str(state.get("stock_id") or ""),
            peer_id=str(state.get("peer_id") or ""),
            subject=subject,
            body=body,
        )
        detail = reading_detail
        error = None
    except Exception as exc:
        path = None
        detail = f"草稿已組成，但寫檔失敗：{exc}"
        error = str(exc)
    return _with(
        state,
        digest_status="pending_approval",
        digest_subject=subject,
        digest_body=body,
        digest_path=path,
        error=error,
        notes=_notes(state, detail),
        steps=_steps(state, "draft", ok=path is not None, detail=detail),
    )


def send_node(state: dict[str, Any]) -> dict[str, Any]:
    """Reached only after an explicit resume. Default permissions keep send blocked."""
    _emit("sending")
    allowed, reason = allow_send_digest(
        approved=False,
        permissions=Permissions(role="notify", allow_send_digest=False),
    )
    del allowed
    return _with(
        state,
        digest_status="blocked",
        notes=_notes(state, reason),
        steps=_steps(state, "send", ok=False, detail=reason),
    )


def _after_data(state: dict[str, Any]) -> str:
    return "end" if state.get("halt") else "research"


def _after_research(state: dict[str, Any]) -> str:
    return "end" if state.get("halt") else "compare"


def _after_compare(state: dict[str, Any]) -> str:
    if state.get("halt"):
        return "end"
    if state.get("run_position"):
        return "position"
    return "draft"


def compile_peer_graph(io: PeerGraphIO | None = None, checkpointer: Any | None = None):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    tools = io or PeerGraphIO()
    graph = StateGraph(dict)
    graph.add_node("data", lambda state: data_node(state, tools))
    graph.add_node("research", lambda state: research_node(state, tools))
    graph.add_node("compare", lambda state: compare_node(state, tools))
    graph.add_node("position", lambda state: position_node(state, tools))
    graph.add_node("draft", lambda state: draft_node(state, tools))
    graph.add_node("send", send_node)
    graph.add_edge(START, "data")
    graph.add_conditional_edges("data", _after_data, {"research": "research", "end": END})
    graph.add_conditional_edges(
        "research",
        _after_research,
        {"compare": "compare", "end": END},
    )
    graph.add_conditional_edges(
        "compare",
        _after_compare,
        {"position": "position", "draft": "draft", "end": END},
    )
    graph.add_edge("position", "draft")
    graph.add_edge("draft", "send")
    graph.add_edge("send", END)
    saver = checkpointer if checkpointer is not None else MemorySaver()
    return graph.compile(checkpointer=saver, interrupt_before=["send"])


def initial_peer_state(
    *,
    stock_id: str,
    peer_id: str,
    trade_date: str | None = None,
    fetch: bool = True,
    is_holding: bool = False,
    avg_cost: float | None = None,
    share_count: int | None = None,
    skip_pdf: bool = True,
) -> dict[str, Any]:
    return {
        "stock_id": stock_id,
        "peer_id": peer_id,
        "trade_date": trade_date,
        "fetch": fetch,
        "is_holding": is_holding,
        "avg_cost": avg_cost,
        "share_count": share_count,
        "skip_pdf": skip_pdf,
        "halt": False,
        "notes": [],
        "steps": [],
        "run_position": False,
        "base_weaker": False,
        "position_entered": False,
        "digest_status": "not_applicable",
        "digest_subject": None,
        "digest_body": None,
        "digest_path": None,
        "exit_code": EXIT_OK,
        "error": None,
        "comparison": None,
        "md_path": None,
        "position_md_path": None,
        "position_skipped_reason": None,
    }


def run_peer_graph(
    state: dict[str, Any],
    *,
    io: PeerGraphIO | None = None,
    thread_id: str | None = None,
    checkpointer: Any | None = None,
) -> tuple[dict[str, Any], tuple[str, ...], Any, dict[str, Any]]:
    """Run until the send interrupt (or an earlier halt). Returns state, next nodes, graph, config."""
    graph = compile_peer_graph(io, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id or uuid.uuid4().hex}}
    result = graph.invoke(state, config)
    nxt = tuple(graph.get_state(config).next)
    return result, nxt, graph, config


def resume_peer_graph(graph: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Continue past the send interrupt. send_digest stays blocked."""
    return graph.invoke(None, config)


@dataclass
class PeerGraphJob:
    id: str
    stock_id: str
    peer_id: str
    status: str = "queued"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    trade_date: str | None = None
    fetch: bool = True
    is_holding: bool = False
    skip_pdf: bool = True
    avg_cost: float | None = None
    share_count: int | None = None
    error: str | None = None
    exit_code: int = 0
    notes: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    comparison: dict[str, Any] | None = None
    base_weaker: bool = False
    position_entered: bool = False
    position_skipped_reason: str | None = None
    md_path: str | None = None
    position_md_path: str | None = None
    digest_status: str = "not_applicable"
    digest_subject: str | None = None
    digest_body: str | None = None
    digest_path: str | None = None
    interrupted_before: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_jobs: dict[str, PeerGraphJob] = {}
_jobs_lock = threading.Lock()


def get_peer_graph_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return job.to_dict() if job is not None else None


def _update_job(job_id: str, **changes: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = _now_iso()


def execute_peer_graph_job(job_id: str, io: PeerGraphIO | None = None) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        state = initial_peer_state(
            stock_id=job.stock_id,
            peer_id=job.peer_id,
            trade_date=job.trade_date,
            fetch=job.fetch,
            is_holding=job.is_holding,
            avg_cost=job.avg_cost,
            share_count=job.share_count,
            skip_pdf=job.skip_pdf,
        )

    token = _listener.set(lambda status: _update_job(job_id, status=status))
    try:
        result, nxt, _graph, _config = run_peer_graph(state, io=io, thread_id=job_id)
        interrupted = "send" in nxt
        halted = bool(result.get("halt"))
        status = "failed" if halted else ("pending_approval" if interrupted else "done")
        _update_job(
            job_id,
            status=status,
            trade_date=result.get("trade_date"),
            error=result.get("error") if halted else None,
            exit_code=int(result.get("exit_code") or EXIT_FAILED) if halted else EXIT_OK,
            notes=list(result.get("notes") or []),
            steps=list(result.get("steps") or []),
            comparison=result.get("comparison"),
            base_weaker=bool(result.get("base_weaker")),
            position_entered=bool(result.get("position_entered")),
            position_skipped_reason=result.get("position_skipped_reason"),
            md_path=result.get("md_path"),
            position_md_path=result.get("position_md_path"),
            digest_status=str(result.get("digest_status") or "not_applicable"),
            digest_subject=result.get("digest_subject"),
            digest_body=result.get("digest_body"),
            digest_path=result.get("digest_path"),
            interrupted_before="send" if interrupted else None,
        )
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc), exit_code=EXIT_FAILED)
    finally:
        _listener.reset(token)


def claim_peer_graph_send(job_id: str) -> dict[str, Any]:
    """Move a reviewed draft to sending. Does not send mail."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status != "pending_approval" or not (job.digest_body and job.digest_subject):
            raise ValueError("沒有待核准的比較信草稿")
        job.status = "sending"
        job.digest_status = "sending"
        job.updated_at = _now_iso()
        return job.to_dict()


def finish_peer_graph_send(job_id: str, *, ok: bool, error: str | None = None) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status != "sending":
            raise ValueError("這封比較信不在寄送中")
        if ok:
            job.status = "sent"
            job.digest_status = "sent"
            job.error = None
        else:
            job.status = "pending_approval"
            job.digest_status = "pending_approval"
            job.error = error or "寄送失敗"
        job.updated_at = _now_iso()
        return job.to_dict()


def enqueue_peer_graph(
    *,
    stock_id: str,
    peer_id: str,
    trade_date: str | None = None,
    fetch: bool = True,
    is_holding: bool = False,
    avg_cost: float | None = None,
    share_count: int | None = None,
    skip_pdf: bool = True,
    runner: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    try:
        import langgraph  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "缺少 langgraph。請執行 uv sync --extra server --extra ui --extra stock"
        ) from exc

    job = PeerGraphJob(
        id=uuid.uuid4().hex,
        stock_id=stock_id.strip(),
        peer_id=peer_id.strip(),
        trade_date=trade_date,
        fetch=fetch,
        is_holding=is_holding,
        skip_pdf=skip_pdf,
        avg_cost=avg_cost,
        share_count=share_count,
    )
    with _jobs_lock:
        _jobs[job.id] = job
    target = runner or (lambda job_id: execute_peer_graph_job(job_id))
    threading.Thread(target=target, args=(job.id,), daemon=True).start()
    return job.to_dict()
