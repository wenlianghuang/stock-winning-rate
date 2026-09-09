"""Phase 2 orchestrator policy: testable rules, not left to the model.

Rules (agent-roadmap Phase 2):
1. Missing data first (Data), then Research.
2. No holdings → do not run Position.
3. Gate failure returns to the same specialist until pass or max_rounds
   (implemented by existing gate tools; orchestrator passes max_rounds).
4. send_digest is blocked by default; only a draft + pending-approval.
5. Each run has a tool budget (call count / elapsed time).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent.tools._types import EXIT_CSV_MISSING

ALLOWED_TOOLS = frozenset(
    {
        "get_last_trading_date",
        "get_holdings",
        "fetch_chips",
        "build_chip_facts",
        "run_report_gate",
        "run_position_gate",
        "run_market_daily",
        "answer_market_chat",
        "draft_digest",
        "send_digest",
    }
)

DATA_TOOLS = frozenset({"get_last_trading_date", "fetch_chips", "build_chip_facts"})
RESEARCH_TOOLS = frozenset({"run_report_gate", "run_market_daily", "answer_market_chat"})
POSITION_TOOLS = frozenset({"run_position_gate"})
GATE_TOOLS = frozenset({"run_report_gate", "run_position_gate", "run_market_daily"})
NOTIFY_TOOLS = frozenset({"draft_digest", "send_digest"})
NEEDS_CSV_TOOLS = frozenset({"run_report_gate", "run_position_gate", "build_chip_facts"})

INTENT_PROCESS_HOLDINGS = "process_holdings"
INTENT_STOCK_DECISION = "stock_decision"
INTENT_MARKET_OUTLOOK = "market_outlook"

DIGEST_PENDING = "pending_approval"
DIGEST_SKIPPED = "skipped"
DIGEST_NA = "not_applicable"
DIGEST_BLOCKED = "blocked"

DEFAULT_MAX_TOOL_CALLS = 32
DEFAULT_MAX_ELAPSED_SEC = 1800.0
DEFAULT_MAX_GATE_ROUNDS = 8


@dataclass
class Budget:
    max_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_elapsed_sec: float = DEFAULT_MAX_ELAPSED_SEC
    calls: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)

    def elapsed_sec(self) -> float:
        return time.monotonic() - self.started_monotonic

    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls)

    def check(self) -> str | None:
        if self.calls >= self.max_calls:
            return f"tool budget 次數用盡（{self.calls}/{self.max_calls}）"
        elapsed = self.elapsed_sec()
        if elapsed >= self.max_elapsed_sec:
            return f"tool budget 時間用盡（{elapsed:.1f}s/{self.max_elapsed_sec}s）"
        return None

    def consume(self) -> str | None:
        blocked = self.check()
        if blocked:
            return blocked
        self.calls += 1
        return None


def is_actionable_holding(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    try:
        avg_cost = float(record.get("avg_cost") or 0)
        shares = int(record.get("shares") or record.get("share_count") or 0)
    except (TypeError, ValueError):
        return False
    return avg_cost > 0 and shares > 0


def holding_for(stock_id: str, holdings: dict[str, Any]) -> dict[str, Any] | None:
    if not stock_id:
        return None
    record = holdings.get(stock_id) or holdings.get(stock_id.strip())
    return record if isinstance(record, dict) else None


def allow_position(stock_id: str, holdings: dict[str, Any]) -> tuple[bool, str]:
    """Rule 2: no position unless the stock has avg_cost and share count."""
    sid = (stock_id or "").strip()
    if not sid:
        return False, "無持倉不跑 Position：缺少 stock_id"
    record = holding_for(sid, holdings)
    if record is None:
        return False, f"{sid} 無持倉，不跑 position-gate（只走 research）"
    if not is_actionable_holding(record):
        return False, f"{sid} 持倉缺少均價／張數，不跑 position-gate"
    return True, f"{sid} 有均價／張數，跑 position-gate"


def allow_send_digest(*, approved: bool) -> tuple[bool, str]:
    """Rule 4: send is blocked unless a human approved. Orchestrator never self-approves."""
    if not approved:
        return False, "send_digest 預設 blocked，只產生草稿與待核准狀態"
    return True, "send_digest 已核准（本 repo 仍不寄信）"


def needs_fetch_before(tool: str, stock_id: str, csv_missing: set[str]) -> bool:
    """Rule 1: Data before Research/Position when CSV is missing."""
    if tool not in NEEDS_CSV_TOOLS:
        return False
    sid = (stock_id or "").strip()
    return bool(sid) and sid in csv_missing


def should_retry_after_csv_missing(tool: str, exit_code: int) -> bool:
    return tool in NEEDS_CSV_TOOLS and exit_code == EXIT_CSV_MISSING


def skip_position_after_failed_research(*, report_ok: bool) -> bool:
    """Do not run position if the same stock's report-gate did not pass."""
    return not report_ok


def validate_plan_schema(data: dict[str, Any]) -> list[str]:
    """Schema check for LLM- or rule-produced plans. Does not execute."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["plan 必須是 JSON object"]
    kind = data.get("intent_kind")
    if kind not in {
        INTENT_PROCESS_HOLDINGS,
        INTENT_STOCK_DECISION,
        INTENT_MARKET_OUTLOOK,
    }:
        errors.append(
            "intent_kind 必須是 process_holdings / stock_decision / market_outlook"
        )
    steps = data.get("steps")
    if not isinstance(steps, list):
        errors.append("steps 必須是陣列")
        return errors
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            errors.append(f"steps[{index}] 必須是 object")
            continue
        tool = step.get("tool")
        if tool not in ALLOWED_TOOLS:
            errors.append(f"steps[{index}] 未知或不允許的 tool：{tool!r}")
        args = step.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            errors.append(f"steps[{index}].args 必須是 object")
    return errors
