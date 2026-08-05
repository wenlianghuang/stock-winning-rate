#!/usr/bin/env python3
"""Market daily gate: facts → agy narrative → validate loop."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from day_window import DayWindow
from market_day_prompts import build_fix_prompt, build_market_day_prompt
from market_day_signals import (
    build_market_day_facts,
    facts_path,
    facts_summary_for_prompt,
    market_output_dir,
    resolve_window_or_fail,
    write_facts_json,
)
from market_day_summary import summary_path, write_summary_json
from validate_market_day import validate_market_day_report

MAX_ROUNDS_DEFAULT = 6
AGY_TIMEOUT_SEC = 900

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_AGY_MISSING = 10
EXIT_NO_DATA = 20
EXIT_BAD_ARGS = 2


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_paths() -> None:
    root = project_root()
    for sub in ("ui", ".agents/skills/market-daily", ".agents/skills/tw-stock-report"):
        text = str(root / sub)
        if text not in sys.path:
            sys.path.insert(0, text)


def resolve_agy_bin() -> str:
    custom = os.environ.get("AGY_BIN", "").strip()
    if custom:
        return custom
    found = shutil.which("agy")
    if not found:
        print(
            "CRITICAL_ERROR: 找不到 agy。請安裝 Antigravity CLI 或設定 AGY_BIN。",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_AGY_MISSING)
    return found


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> tuple[str, int]:
    ensure_paths()
    from agy_output import agy_output_usable, clean_agy_output

    agy_bin = resolve_agy_bin()
    try:
        result = subprocess.run(
            [
                agy_bin,
                "-p",
                prompt,
                "--dangerously-skip-permissions",
                "--print-timeout",
                "15m",
            ],
            cwd=project_root(),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"agy 逾時（>{timeout_sec}s）") from exc

    raw = result.stdout or result.stderr or ""
    body = clean_agy_output(raw)
    if not agy_output_usable(body):
        detail = body[:200] if body else "(空)"
        raise RuntimeError(f"agy 輸出不可用（exit {result.returncode}）：{detail}")
    return body, result.returncode


@dataclass
class RoundLog:
    round: int
    passed: bool
    agy_exit_code: int | None
    duration_sec: float
    issues: list[str]
    issue_codes: list[str] = field(default_factory=list)


def ensure_tsmc_csv(
    window: DayWindow,
    *,
    skip_fetch: bool = False,
) -> None:
    """Best-effort: refresh 2330 chip CSV for trade_date (optional context)."""
    if skip_fetch:
        return
    script = (
        project_root()
        / ".agents"
        / "skills"
        / "tw-stock-report"
        / "fetch_chip_report.py"
    )
    if not script.exists():
        return
    cmd = [
        sys.executable,
        str(script),
        "--stocks",
        "2330",
        "--date",
        window.trade_date,
        "--lookback-days",
        "5",
    ]
    print(f"PROGRESS: stock-report 2330 @ {window.trade_date}", file=sys.stderr)
    subprocess.run(cmd, cwd=project_root(), check=False)


def write_markdown(path: Path, body: str, facts: dict[str, Any]) -> None:
    header = (
        f"# 台股開盤前戰術 brief（{facts.get('trade_date')} → "
        f"{facts.get('for_session')} 開盤）\n\n"
        f"> Phase 1：量價／法人／技術／2330／那指費半；未納入台指夜盤\n\n"
    )
    path.write_text(header + body.strip() + "\n", encoding="utf-8")


def run_gate(
    *,
    window: DayWindow,
    max_rounds: int,
    skip_agy: bool,
    skip_fetch: bool,
    skip_us: bool,
    validate_only: bool,
) -> int:
    out_dir = market_output_dir(window.trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "tw_market_daily.md"
    log_path = out_dir / "tw_market_daily.gate.log"
    rounds_dir = out_dir / "tw_market_daily.gate.rounds"

    if validate_only:
        if not md_path.exists() or not facts_path(window.trade_date).exists():
            print("ERROR: validate-only 需要既有 md 與 facts.json", file=sys.stderr)
            return EXIT_NO_DATA
        facts = json.loads(facts_path(window.trade_date).read_text(encoding="utf-8"))
        body = md_path.read_text(encoding="utf-8")
        result = validate_market_day_report(body, facts)
        print(json.dumps({"passed": result.passed, "issues": result.summary_lines()}, ensure_ascii=False))
        return EXIT_OK if result.passed else EXIT_VALIDATION_FAILED

    ensure_tsmc_csv(window, skip_fetch=skip_fetch)
    facts_obj = build_market_day_facts(window, skip_us=skip_us)
    facts_file = write_facts_json(facts_obj)
    facts = facts_obj.to_dict()
    print(f"PROGRESS: wrote {facts_file}", file=sys.stderr)

    if skip_agy:
        print("PROGRESS: skip-agy；僅產出 facts.json", file=sys.stderr)
        return EXIT_OK

    base_prompt = build_market_day_prompt(
        facts_summary=facts_summary_for_prompt(facts_obj),
    )
    rounds_dir.mkdir(parents=True, exist_ok=True)
    logs: list[RoundLog] = []
    body = ""
    prompt = base_prompt

    for round_no in range(1, max_rounds + 1):
        print(f"PROGRESS: agy round {round_no}/{max_rounds}", file=sys.stderr)
        t0 = time.time()
        try:
            body, exit_code = run_agy(prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            logs.append(
                RoundLog(
                    round=round_no,
                    passed=False,
                    agy_exit_code=None,
                    duration_sec=time.time() - t0,
                    issues=[str(exc)],
                    issue_codes=["agy_error"],
                )
            )
            break
        duration = time.time() - t0
        validation = validate_market_day_report(body, facts)
        r_prompt = rounds_dir / f"r{round_no:02d}.prompt.txt"
        r_body = rounds_dir / f"r{round_no:02d}.body.md"
        r_val = rounds_dir / f"r{round_no:02d}.validation.json"
        r_prompt.write_text(prompt.strip() + "\n", encoding="utf-8")
        r_body.write_text(body.strip() + "\n", encoding="utf-8")
        r_val.write_text(
            json.dumps(
                {
                    "round": round_no,
                    "passed": validation.passed,
                    "issues": [
                        {"code": i.code, "message": i.message} for i in validation.issues
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        logs.append(
            RoundLog(
                round=round_no,
                passed=validation.passed,
                agy_exit_code=exit_code,
                duration_sec=duration,
                issues=validation.summary_lines(),
                issue_codes=[i.code for i in validation.issues],
            )
        )
        if validation.passed:
            write_markdown(md_path, body, facts)
            write_summary_json(facts, body, summary_path(window.trade_date))
            log_path.write_text(
                json.dumps([asdict(x) for x in logs], ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            print(f"OK: {md_path}", file=sys.stderr)
            return EXIT_OK
        prompt = build_fix_prompt(
            base_prompt=base_prompt,
            issues=validation.summary_lines(),
        )

    if body.strip():
        write_markdown(md_path, body, facts)
        write_summary_json(facts, body, summary_path(window.trade_date))
    log_path.write_text(
        json.dumps([asdict(x) for x in logs], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("ERROR: 達最大輪數仍未通過驗證", file=sys.stderr)
    return EXIT_VALIDATION_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="台股開盤前戰術 brief（Phase 1）")
    parser.add_argument(
        "--as-of",
        default=None,
        help="覆寫當下時間（ISO 或 YYYY-MM-DD），用於 cutover 測試",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="強制指定 trade_date YYYY-MM-DD（略過 cutover）",
    )
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS_DEFAULT)
    parser.add_argument("--skip-agy", action="store_true", help="只產 facts")
    parser.add_argument("--skip-fetch", action="store_true", help="不自動跑 stock-report")
    parser.add_argument(
        "--skip-us",
        action="store_true",
        help="略過那斯達克／費半日報酬抓取",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="只印出 day window JSON",
    )
    args = parser.parse_args(argv)

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
        skip_fetch=args.skip_fetch,
        skip_us=args.skip_us,
        validate_only=args.validate_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
