#!/usr/bin/env python3
"""Outcome review gate: scored table → agy note → validate, at most two rounds.

A failed note is not published. The file then keeps the eval-calls stats only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from outcome_ledger import (
    build_ledger,
    entry_date_before_latest,
    ledger_summary,
    scope_label,
)
from outcome_prompts import build_fix_prompt, build_outcome_prompt
from validate_outcome import validate_outcome_report

MAX_ROUNDS = 2
AGY_TIMEOUT_SEC = 900
AGY_MIN_CHARS = 60

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_AGY_MISSING = 10
EXIT_BAD_ARGS = 2

AgyFn = Callable[..., tuple[str, int]]


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _eval_calls():
    tools = str(project_root() / "tools")
    if tools not in sys.path:
        sys.path.insert(0, str(tools))
    import eval_calls

    return eval_calls


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> tuple[str, int]:
    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from agent.llm import complete, invoke_agy, resolved_backend

    if resolved_backend() == "ollama":
        return complete(prompt, timeout_sec=timeout_sec), 0
    try:
        done = invoke_agy(prompt, timeout_sec=timeout_sec, min_chars=AGY_MIN_CHARS)
    except FileNotFoundError as exc:
        print(f"CRITICAL_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_AGY_MISSING) from exc
    return done.text, done.exit_code


@dataclass
class RoundLog:
    round: int
    passed: bool
    agy_exit_code: int | None
    duration_sec: float
    issues: list[str]
    issue_codes: list[str] = field(default_factory=list)


@dataclass
class GateOutcome:
    exit_code: int
    md_path: Path
    facts_path: Path
    log_path: Path
    narrative_generated: bool
    scored: int
    pending: int


def review_paths(root: Path, trade_date: str | None, stock: str | None) -> tuple[Path, Path, Path]:
    stem_bits = ["outcome_review"]
    if trade_date:
        stem_bits.append(trade_date)
    if stock:
        stem_bits.append(stock)
    stem = "_".join(stem_bits)
    out_dir = root / "reports" / "eval"
    return (
        out_dir / f"{stem}.md",
        out_dir / f"{stem}.facts.json",
        out_dir / f"{stem}.gate.log",
    )


def stats_markdown(payload: dict[str, Any], *, banner: str) -> str:
    report = _eval_calls().format_report(payload)
    return f"> {banner}\n\n{report}"


def narrative_markdown(body: str, ledger: dict[str, Any]) -> str:
    return (
        f"# 個股判斷事後對帳（{ledger['scope']}）\n\n"
        "> 這頁在檢查判斷日當天已經寫下的方向。後面那個日期只是 5 個交易日後的收盤，不是另一個看多或看空。\n"
        "> 超額是這段個股漲跌減掉大盤漲跌：正數代表比大盤強，負數代表比大盤弱。\n\n"
        f"{body.strip()}\n"
    )


@dataclass(frozen=True)
class ReviewWindow:
    trade_date: str | None
    as_of: str | None
    include_date_in_path: bool


def refresh_outcomes() -> None:
    tools = str(project_root() / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import outcome_label

    outcome_label.label_all()


def market_trading_dates() -> list[str]:
    """Trading sessions from on-disk TAIEX closes, else the union of stock closes."""
    tools = str(project_root() / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import outcome_label

    stock_closes, taiex_closes = outcome_label.build_price_oracle()
    if taiex_closes:
        return sorted(taiex_closes)
    dates: set[str] = set()
    for series in stock_closes.values():
        dates.update(series)
    return sorted(dates)


def prepare_review_window(*, trade_date: str | None, refresh: bool) -> ReviewWindow | str:
    """No date: latest close, then the judgment 5 sessions earlier.

    An explicit date stays that judgment day and is not shifted.
    The automatic window refreshes on-disk 5-day excess first; otherwise the
    chosen day is still pending in an old outcomes file.
    """
    explicit = (trade_date or "").strip() or None
    if explicit is None or refresh:
        refresh_outcomes()
    if explicit:
        return ReviewWindow(trade_date=explicit, as_of=None, include_date_in_path=True)
    found = entry_date_before_latest(market_trading_dates())
    if found is None:
        return "收盤日不足 5 個交易日，無法從最新收盤回看"
    entry, as_of = found
    print(f"PROGRESS: 以 {as_of} 收盤回看，對帳 {entry} 的判斷", file=sys.stderr)
    return ReviewWindow(trade_date=entry, as_of=as_of, include_date_in_path=False)


def run_gate(
    *,
    root: Path,
    trade_date: str | None = None,
    stock: str | None = None,
    max_rounds: int = MAX_ROUNDS,
    skip_agy: bool = False,
    payload: dict[str, Any] | None = None,
    agy: AgyFn | None = None,
    as_of: str | None = None,
    include_date_in_path: bool = True,
) -> GateOutcome:
    rounds = min(MAX_ROUNDS, max(1, max_rounds))
    if payload is None:
        payload = _eval_calls().evaluate(
            root,
            trade_date=trade_date,
            stock=stock,
            horizon="5d",
        )
    scope = scope_label(trade_date, stock, as_of=as_of)
    ledger = build_ledger(payload, scope=scope, as_of=as_of)
    file_date = trade_date if include_date_in_path else None
    md_path, facts_path, log_path = review_paths(root, file_date, stock)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    facts_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    scored = int(ledger["scored"])
    pending = int(ledger["counts"]["pending"])
    call = agy or run_agy

    def finish(code: int, body: str, logs: list[RoundLog], *, generated: bool) -> GateOutcome:
        md_path.write_text(body, encoding="utf-8")
        if logs:
            log_path.write_text(
                json.dumps([asdict(item) for item in logs], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return GateOutcome(
            exit_code=code,
            md_path=md_path,
            facts_path=facts_path,
            log_path=log_path,
            narrative_generated=generated,
            scored=scored,
            pending=pending,
        )

    if int(payload.get("cases") or 0) == 0:
        print("PROGRESS: 沒有可對帳的報告，不呼叫模型", file=sys.stderr)
        return finish(
            EXIT_OK,
            stats_markdown(payload, banner="沒有可對帳的報告，不呼叫模型。"),
            [],
            generated=False,
        )
    if skip_agy:
        print("PROGRESS: skip-agy；只留規則對帳", file=sys.stderr)
        return finish(
            EXIT_OK,
            stats_markdown(payload, banner="未呼叫模型，這份只有規則對帳。"),
            [],
            generated=False,
        )

    base_prompt = build_outcome_prompt(table=ledger_summary(ledger))
    prompt = base_prompt
    logs: list[RoundLog] = []
    for round_index in range(1, rounds + 1):
        started = time.time()
        body, code = call(prompt, timeout_sec=AGY_TIMEOUT_SEC)
        validation = validate_outcome_report(body, ledger)
        logs.append(
            RoundLog(
                round=round_index,
                passed=validation.passed,
                agy_exit_code=code,
                duration_sec=round(time.time() - started, 2),
                issues=validation.summary_lines(),
                issue_codes=[issue.code for issue in validation.issues],
            )
        )
        if validation.passed:
            print(f"OK: {md_path}", file=sys.stderr)
            return finish(EXIT_OK, narrative_markdown(body, ledger), logs, generated=True)
        prompt = build_fix_prompt(base_prompt=base_prompt, issues=validation.summary_lines())

    print("ERROR: 達最大輪數仍未通過驗證，只留規則對帳", file=sys.stderr)
    return finish(
        EXIT_VALIDATION_FAILED,
        stats_markdown(payload, banner="解讀未生成，這份只有規則對帳。"),
        logs,
        generated=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="個股判斷事後對帳（5 日超額，模型只解釋已算好的表）")
    parser.add_argument(
        "--date",
        default=None,
        help="指定判斷日 YYYY-MM-DD。不填則用最新收盤往前第 5 個交易日",
    )
    parser.add_argument("--stock", default=None, help="只看某股票代碼")
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    parser.add_argument("--skip-agy", action="store_true", help="只留規則對帳，不呼叫模型")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="指定 --date 時也先補 5 日超額。未指定日期時一定會補",
    )
    parser.add_argument("--root", type=Path, default=None, help="專案根目錄，預設為本 repo")
    args = parser.parse_args(argv[1:] if argv and argv[0] == "--" else argv)

    root = args.root or project_root()
    window = prepare_review_window(trade_date=args.date, refresh=args.refresh)
    if isinstance(window, str):
        print(f"ERROR: {window}", file=sys.stderr)
        return EXIT_BAD_ARGS

    try:
        outcome = run_gate(
            root=root,
            trade_date=window.trade_date,
            stock=args.stock,
            max_rounds=args.max_rounds,
            skip_agy=args.skip_agy,
            as_of=window.as_of,
            include_date_in_path=window.include_date_in_path,
        )
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_AGY_MISSING
    print(outcome.md_path)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
