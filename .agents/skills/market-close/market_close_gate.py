#!/usr/bin/env python3
"""Close report gate: facts → agy narrative → validate loop."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from close_prompts import build_close_prompt, build_fix_prompt
from close_signals import (
    build_close_facts,
    facts_path,
    facts_summary_for_prompt,
    market_output_dir,
    write_facts_json,
)
from close_summary import summary_path, write_summary_json
from close_window import CloseWindow, parse_as_of, resolve_close_window
from validate_close import validate_close_report

MAX_ROUNDS_DEFAULT = 4
AGY_TIMEOUT_SEC = 900

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_AGY_MISSING = 10
EXIT_BAD_ARGS = 2


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> tuple[str, int]:
    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from agent.llm import GATE_MIN_CHARS, complete, invoke_agy, resolved_backend

    if resolved_backend() == "ollama":
        return complete(prompt, timeout_sec=timeout_sec), 0
    try:
        done = invoke_agy(prompt, timeout_sec=timeout_sec, min_chars=GATE_MIN_CHARS)
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


def write_markdown(path: Path, body: str, facts: dict[str, Any]) -> None:
    header = (
        f"# 台股收盤日報（{facts.get('trade_date')}）\n\n"
        "> 大盤、權值股法人、當天新聞。不含融資券、當沖、主力，不含美股。\n\n"
    )
    path.write_text(header + body.strip() + "\n", encoding="utf-8")


def resolve_window_or_fail(*, as_of: str | None, trade_date: str | None) -> CloseWindow:
    now = parse_as_of(as_of)
    return resolve_close_window(now, trade_date=trade_date)


def run_gate(
    *,
    window: CloseWindow,
    max_rounds: int,
    skip_agy: bool,
    skip_news: bool,
    validate_only: bool,
) -> int:
    out_dir = market_output_dir(window.trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "tw_market_close.md"
    log_path = out_dir / "tw_market_close.gate.log"
    facts_file = facts_path(window.trade_date)

    if validate_only:
        if not md_path.exists() or not facts_file.exists():
            print("ERROR: validate-only 需要既有 md 與 facts.json", file=sys.stderr)
            return EXIT_BAD_ARGS
        facts = json.loads(facts_file.read_text(encoding="utf-8"))
        body = md_path.read_text(encoding="utf-8")
        result = validate_close_report(body, facts)
        print(json.dumps({"passed": result.passed, "issues": result.summary_lines()}, ensure_ascii=False))
        return EXIT_OK if result.passed else EXIT_VALIDATION_FAILED

    facts = build_close_facts(window, skip_news=skip_news)
    write_facts_json(facts)
    print(f"PROGRESS: wrote {facts_file}", file=sys.stderr)
    if skip_agy:
        print("PROGRESS: skip-agy；僅產出 facts.json", file=sys.stderr)
        return EXIT_OK

    base_prompt = build_close_prompt(facts_summary=facts_summary_for_prompt(facts))
    prompt = base_prompt
    logs: list[RoundLog] = []
    body = ""
    for round_index in range(1, max_rounds + 1):
        started = time.time()
        body, code = run_agy(prompt)
        validation = validate_close_report(body, facts)
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
            write_markdown(md_path, body, facts)
            write_summary_json(facts, body, summary_path(window.trade_date))
            log_path.write_text(
                json.dumps([asdict(item) for item in logs], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"OK: {md_path}", file=sys.stderr)
            return EXIT_OK
        prompt = build_fix_prompt(base_prompt=base_prompt, issues=validation.summary_lines())

    if body.strip():
        write_markdown(md_path, body, facts)
        write_summary_json(facts, body, summary_path(window.trade_date))
    log_path.write_text(
        json.dumps([asdict(item) for item in logs], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("ERROR: 達最大輪數仍未通過驗證", file=sys.stderr)
    return EXIT_VALIDATION_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="台股收盤日報（大盤、權值法人、當天新聞）")
    parser.add_argument("--as-of", default=None, help="覆寫當下時間（ISO 或 YYYY-MM-DD）")
    parser.add_argument("--date", default=None, help="強制指定 trade_date YYYY-MM-DD（略過 cutover）")
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS_DEFAULT)
    parser.add_argument("--skip-agy", action="store_true", help="只產 facts")
    parser.add_argument("--skip-news", action="store_true", help="不抓 Yahoo 標題")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--resolve-only", action="store_true", help="只印出 close window JSON")
    args = parser.parse_args(argv[1:] if argv and argv[0] == "--" else argv)

    try:
        window = resolve_window_or_fail(as_of=args.as_of, trade_date=args.date)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGS

    if args.resolve_only:
        print(json.dumps(window.as_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK

    return run_gate(
        window=window,
        max_rounds=max(1, args.max_rounds),
        skip_agy=args.skip_agy,
        skip_news=args.skip_news,
        validate_only=args.validate_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
