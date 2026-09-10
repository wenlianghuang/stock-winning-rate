"""Phase 3 JSONL audit: one record per tool / validator round, replayable.

A process-holdings run writes ``reports/agent/{trade_date}/run_{run_id}.jsonl``.
Records keep args summaries (no report body), actor, result, elapsed_ms, and
optional ``issue_codes`` so a failed report-gate round is visible without
re-running agy.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.policy import ROLE_ORCHESTRATOR, ROLE_VALIDATOR, actor_for_tool
from agent.tools._paths import ROOT

AUDIT_ROOT = ROOT / "reports" / "agent"

REDACT_KEYS = frozenset(
    {
        "markdown",
        "items",
        "prompt",
        "main_detail_markdown",
        "body",
        "position_markdown",
        "news_text",
    }
)

MAX_SUMMARY_CHARS = 240


@dataclass
class AuditRecord:
    run_id: str
    ts: str
    actor: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    result: str = "ok"
    elapsed_ms: int = 0
    exit_code: int | None = None
    issue_codes: list[str] = field(default_factory=list)
    handoff_from: str | None = None
    handoff_to: str | None = None
    summary: str = ""
    skipped: bool = False
    skip_reason: str | None = None
    gate_round: int | None = None
    stock_id: str | None = None


@dataclass
class Handoff:
    frm: str
    to: str
    reason: str
    stock_id: str | None = None
    issue_codes: list[str] = field(default_factory=list)


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_audit_path(run_id: str, trade_date: str | None = None) -> Path:
    day = trade_date or "undated"
    return AUDIT_ROOT / day / f"run_{run_id}.jsonl"


def summarize_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Drop long narrative fields so the log can be committed to disk safely."""
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key in REDACT_KEYS:
            if key == "items" and isinstance(value, list):
                out["items_count"] = len(value)
                out["item_stocks"] = [
                    str(item.get("stock_id") or "")
                    for item in value
                    if isinstance(item, dict)
                ]
            else:
                out[key] = f"<{key} omitted>"
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) or item is None for item in value
        ):
            out[key] = list(value)
        else:
            out[key] = f"<{type(value).__name__}>"
    return out


def stock_id_from_args(args: dict[str, Any] | None) -> str | None:
    raw = (args or {}).get("stock_id")
    if raw:
        return str(raw)
    stocks = (args or {}).get("stocks")
    if isinstance(stocks, list) and len(stocks) == 1:
        return str(stocks[0])
    return None


def result_label(*, ok: bool, skipped: bool, skip_reason: str | None) -> str:
    if skipped:
        reason = skip_reason or ""
        if "blocked" in reason or "不允許" in reason or "權限關閉" in reason:
            return "blocked"
        return "skip"
    return "ok" if ok else "fail"


def parse_gate_log(path: Path | str) -> list[dict[str, Any]]:
    """Read report-gate / position-gate JSONL. Missing file → empty list."""
    file_path = Path(path)
    if not file_path.is_file():
        return []
    rounds: list[dict[str, Any]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        codes = entry.get("issue_codes") or []
        if not isinstance(codes, list):
            codes = []
        rounds.append(
            {
                "round": entry.get("round"),
                "passed": bool(entry.get("passed")),
                "issue_codes": [str(code) for code in codes],
                "issues": list(entry.get("issues") or []),
                "duration_sec": entry.get("duration_sec"),
            }
        )
    return rounds


def gate_rounds_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer in-memory ``gate_rounds`` (tests); else parse ``gate_log_path``."""
    raw = payload.get("gate_rounds")
    if isinstance(raw, list) and raw:
        rounds: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            codes = item.get("issue_codes") or []
            rounds.append(
                {
                    "round": item.get("round"),
                    "passed": bool(item.get("passed")),
                    "issue_codes": [str(code) for code in codes],
                    "issues": list(item.get("issues") or []),
                    "duration_sec": item.get("duration_sec"),
                }
            )
        return rounds
    log_path = payload.get("gate_log_path") or payload.get("log_path")
    csv_path = payload.get("csv_path")
    if log_path:
        return parse_gate_log(str(log_path))
    if csv_path:
        return parse_gate_log(Path(str(csv_path)).with_suffix(".gate.log"))
    md_path = payload.get("md_path")
    if md_path:
        return parse_gate_log(Path(str(md_path)).with_suffix(".gate.log"))
    return []


def handoffs_from_gate_rounds(
    rounds: list[dict[str, Any]],
    *,
    research_actor: str,
    stock_id: str | None,
) -> list[Handoff]:
    """Materialize the existing gate loop as Research ↔ Validator handoffs."""
    events: list[Handoff] = []
    if not rounds:
        return events
    for index, round_info in enumerate(rounds):
        codes = [str(code) for code in (round_info.get("issue_codes") or [])]
        passed = bool(round_info.get("passed"))
        events.append(
            Handoff(
                frm=research_actor,
                to=ROLE_VALIDATOR,
                reason=f"第 {round_info.get('round') or index + 1} 輪交 Validator",
                stock_id=stock_id,
                issue_codes=codes,
            )
        )
        if passed:
            continue
        is_last = index == len(rounds) - 1
        if not is_last:
            events.append(
                Handoff(
                    frm=ROLE_VALIDATOR,
                    to=research_actor,
                    reason="issue_codes 交回 Research 再跑",
                    stock_id=stock_id,
                    issue_codes=codes,
                )
            )
    return events


class AuditLog:
    def __init__(self, run_id: str, path: Path | str | None = None) -> None:
        self.run_id = run_id
        self.path = Path(path) if path else None
        self.records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> AuditRecord:
        if not record.run_id:
            record.run_id = self.run_id
        if not record.ts:
            record.ts = utc_now()
        self.records.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n"
                )
        return record

    def record_tool(
        self,
        *,
        tool: str,
        args: dict[str, Any] | None,
        ok: bool,
        elapsed_ms: int = 0,
        exit_code: int | None = None,
        skipped: bool = False,
        skip_reason: str | None = None,
        summary: str = "",
        issue_codes: list[str] | None = None,
        actor: str | None = None,
        handoff_from: str | None = None,
        handoff_to: str | None = None,
        gate_round: int | None = None,
        stock_id: str | None = None,
    ) -> AuditRecord:
        clipped = summary[:MAX_SUMMARY_CHARS]
        return self.append(
            AuditRecord(
                run_id=self.run_id,
                ts=utc_now(),
                actor=actor or actor_for_tool(tool),
                tool=tool,
                args=summarize_args(args),
                result=result_label(ok=ok, skipped=skipped, skip_reason=skip_reason),
                elapsed_ms=elapsed_ms,
                exit_code=exit_code,
                issue_codes=list(issue_codes or []),
                handoff_from=handoff_from,
                handoff_to=handoff_to,
                summary=clipped,
                skipped=skipped,
                skip_reason=skip_reason,
                gate_round=gate_round,
                stock_id=stock_id or stock_id_from_args(args),
            )
        )

    def record_validator_round(
        self,
        *,
        stock_id: str | None,
        gate_round: int | None,
        issue_codes: list[str],
        passed: bool,
        research_actor: str,
        elapsed_ms: int = 0,
    ) -> AuditRecord:
        codes = list(issue_codes)
        handoff_to = None if passed else research_actor
        summary = (
            "validator 通過"
            if passed
            else ("issue_codes=" + ",".join(codes) if codes else "validator 未通過")
        )
        return self.append(
            AuditRecord(
                run_id=self.run_id,
                ts=utc_now(),
                actor=ROLE_VALIDATOR,
                tool="validate_research",
                args={"stock_id": stock_id, "gate_round": gate_round},
                result="ok" if passed else "fail",
                elapsed_ms=elapsed_ms,
                issue_codes=codes,
                handoff_from=research_actor,
                handoff_to=handoff_to,
                summary=summary,
                gate_round=gate_round,
                stock_id=stock_id,
            )
        )


def load_audit(path: Path | str) -> list[AuditRecord]:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"找不到 audit log：{file_path}")
    records: list[AuditRecord] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        data = json.loads(stripped)
        records.append(
            AuditRecord(
                run_id=str(data.get("run_id") or ""),
                ts=str(data.get("ts") or ""),
                actor=str(data.get("actor") or ROLE_ORCHESTRATOR),
                tool=str(data.get("tool") or ""),
                args=dict(data.get("args") or {}),
                result=str(data.get("result") or ""),
                elapsed_ms=int(data.get("elapsed_ms") or 0),
                exit_code=data.get("exit_code"),
                issue_codes=[str(code) for code in (data.get("issue_codes") or [])],
                handoff_from=data.get("handoff_from"),
                handoff_to=data.get("handoff_to"),
                summary=str(data.get("summary") or ""),
                skipped=bool(data.get("skipped")),
                skip_reason=data.get("skip_reason"),
                gate_round=data.get("gate_round"),
                stock_id=data.get("stock_id"),
            )
        )
    return records


def replay_audit(records: list[AuditRecord]) -> dict[str, Any]:
    """Rebuild the sequence a '處理持股' run actually took."""
    run_id = records[0].run_id if records else ""
    tools = [item.tool for item in records]
    actors = [item.actor for item in records]
    handoffs: list[dict[str, Any]] = []
    issue_rounds: list[dict[str, Any]] = []
    send_attempted = False
    digest_blocked = False
    for item in records:
        if item.handoff_from and item.handoff_to:
            handoffs.append(
                {
                    "from": item.handoff_from,
                    "to": item.handoff_to,
                    "tool": item.tool,
                    "stock_id": item.stock_id,
                    "issue_codes": item.issue_codes,
                }
            )
        if item.issue_codes or item.actor == ROLE_VALIDATOR:
            issue_rounds.append(
                {
                    "actor": item.actor,
                    "tool": item.tool,
                    "gate_round": item.gate_round,
                    "issue_codes": item.issue_codes,
                    "result": item.result,
                    "stock_id": item.stock_id,
                }
            )
        if item.tool == "send_digest":
            send_attempted = True
            if item.result in {"blocked", "skip", "fail"}:
                digest_blocked = True
        if item.result == "blocked" and "send_digest" in (item.skip_reason or item.tool):
            digest_blocked = True
    send_in_log = "send_digest" in tools
    return {
        "run_id": run_id,
        "tools": tools,
        "actors": actors,
        "handoffs": handoffs,
        "issue_rounds": issue_rounds,
        "send_digest_attempted": send_attempted,
        "send_digest_in_log": send_in_log,
        "digest_blocked": digest_blocked or not send_in_log,
        "steps": [asdict(item) for item in records],
    }


def format_replay(payload: dict[str, Any]) -> str:
    lines = [
        f"run_id: {payload.get('run_id') or '（空）'}",
        "replay:",
    ]
    steps = payload.get("steps") or []
    if not steps:
        lines.append("  （沒有紀錄）")
    for index, step in enumerate(steps, start=1):
        actor = step.get("actor") or "?"
        tool = step.get("tool") or "?"
        result = step.get("result") or ""
        extra = f"  {step.get('summary')}" if step.get("summary") else ""
        codes = step.get("issue_codes") or []
        if codes:
            extra += f"  issue_codes={codes}"
        handoff = ""
        if step.get("handoff_from") and step.get("handoff_to"):
            handoff = f"  {step['handoff_from']}→{step['handoff_to']}"
        lines.append(f"  {index}. [{actor}] {tool} {result}{handoff}{extra}")
    lines.append(
        "send_digest: "
        + (
            "blocked / 未執行"
            if payload.get("digest_blocked")
            else ("in log" if payload.get("send_digest_in_log") else "not in log")
        )
    )
    return "\n".join(lines)
