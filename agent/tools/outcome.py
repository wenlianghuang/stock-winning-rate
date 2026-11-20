"""run_outcome_review — Research tool. Reads the scored 5-day table, then the narrative gate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.tools._paths import ROOT, ensure_paths
from agent.tools._types import (
    EXIT_AGY_MISSING,
    EXIT_BAD_ARGS,
    EXIT_FAILED,
    EXIT_OK,
    ToolResult,
    message_for_exit,
)

ensure_paths("outcome-review")

from outcome_review_gate import prepare_review_window, run_gate  # type: ignore[import-not-found]  # noqa: E402


@dataclass
class OutcomeReviewInput:
    trade_date: str | None = None
    stock: str | None = None
    max_rounds: int = 2
    skip_agy: bool = False
    refresh: bool = False
    root: str | None = None


@dataclass
class OutcomeReviewResult(ToolResult):
    md_path: str | None = None
    facts_path: str | None = None
    narrative_generated: bool = False
    scored: int = 0
    pending: int = 0


def run_outcome_review(
    inp: OutcomeReviewInput | None = None,
    **overrides: Any,
) -> OutcomeReviewResult:
    params = inp or OutcomeReviewInput()
    if overrides:
        data = {
            "trade_date": params.trade_date,
            "stock": params.stock,
            "max_rounds": params.max_rounds,
            "skip_agy": params.skip_agy,
            "refresh": params.refresh,
            "root": params.root,
        }
        data.update(overrides)
        params = OutcomeReviewInput(**data)

    root = Path(params.root) if params.root else ROOT
    window = prepare_review_window(trade_date=params.trade_date, refresh=params.refresh)
    if isinstance(window, str):
        return OutcomeReviewResult(ok=False, exit_code=EXIT_BAD_ARGS, error=window)

    try:
        outcome = run_gate(
            root=root,
            trade_date=window.trade_date,
            stock=params.stock or None,
            max_rounds=params.max_rounds,
            skip_agy=params.skip_agy,
            as_of=window.as_of,
            include_date_in_path=window.include_date_in_path,
        )
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_AGY_MISSING
        return OutcomeReviewResult(ok=False, exit_code=code, error=message_for_exit(code))
    except Exception as exc:  # noqa: BLE001
        return OutcomeReviewResult(ok=False, exit_code=EXIT_FAILED, error=str(exc))

    ok = outcome.exit_code == EXIT_OK
    return OutcomeReviewResult(
        ok=ok,
        exit_code=outcome.exit_code,
        error=None if ok else "事後對帳短文未通過，已改留規則統計",
        md_path=str(outcome.md_path),
        facts_path=str(outcome.facts_path),
        narrative_generated=outcome.narrative_generated,
        scored=outcome.scored,
        pending=outcome.pending,
    )
