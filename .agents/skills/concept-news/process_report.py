#!/usr/bin/env python3
"""Write a one-page concept-news report from the event table and validate it.

The model sees only the event file. A failed draft is sent back with the
rule violations, at most two rounds. The summary file is written only after
the draft passes. Every round is appended to the gate log.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from collect_news import TAIPEI
from compare_events import (
    append_diff_section,
    compare_event_tables,
    load_previous_events,
    previous_events_path,
    strip_diff_section,
)
from theme_pack import project_root
from cluster_events import annotate_sessions, prior_relation, split_report_events
from validate_concept_report import (
    EMPTY_NOTICE,
    MIN_CITED_EVENTS,
    NO_AFTER_CLOSE,
    ValidationResult,
    required_citation_count,
    validate_concept_report,
)

MAX_ROUNDS_DEFAULT = 2
Completer = Callable[[str], tuple[str, int]]
_SKILL_PATH = Path(__file__).resolve().parent.parent / "concept-narrative" / "SKILL.md"


def narrative_rules() -> str:
    """Return the skill body. Frontmatter and the title stay out of the prompt."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{_SKILL_PATH} 缺少 frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{_SKILL_PATH} 的 frontmatter 沒有結束")
    body = text[end + 4 :].strip()
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        body = "\n".join(lines[1:]).strip()
    if "## 看法" not in body:
        raise ValueError(f"{_SKILL_PATH} 缺少看法章節規格")
    return body


def default_events_path(theme_id: str, as_of: str, root: Path | None = None) -> Path:
    base = root if root is not None else project_root() / "reports" / "concept-news"
    return base / theme_id / f"{as_of}_events.json"


def today_text() -> str:
    return datetime.now(TAIPEI).date().isoformat()


def load_event_table(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("events 格式錯誤")
    return payload


def report_date(path: Path, table: dict, explicit: str = "") -> str:
    chosen = explicit.strip()
    if chosen:
        return chosen
    match = re.search(r"(\d{4}-\d{2}-\d{2})_events\.json$", path.name)
    if match:
        return match.group(1)
    as_of = str(table.get("as_of") or "").strip()
    if as_of:
        return as_of
    return today_text()


def build_report_prompt(table: dict, events_path: Path, *, min_citations: int) -> str:
    today_close, prior_close, _other = split_report_events(table)
    relation = prior_relation(today_close, prior_close)
    focused = dict(table)
    focused["events"] = today_close
    annotated = annotate_sessions(focused)
    events = table.get("events") if isinstance(table.get("events"), list) else []
    settled = annotated.get("events") if isinstance(annotated.get("events"), list) else []
    need = required_citation_count(len(settled), min_citations)
    payload = json.dumps(annotated, ensure_ascii=False, indent=2)
    if not events:
        quota = f"事件數是 0。三段都要出現「{EMPTY_NOTICE}」，不要列股號。"
    elif not settled:
        quota = (
            "沒有報告日當天 13:30 以後的事件。"
            f"三段都要出現「{NO_AFTER_CLOSE}」，不要把前一日或盤中標題列成條目。"
        )
    else:
        quota = (
            f"報告日當天的收盤事件 {len(settled)} 件。"
            f"「收盤後」至少引用 {need} 則代表標題，標題用該事件的 title。"
            "看法要寫判斷，不要重抄標題或數字。"
        )
    relation_line = (
        f"與前一日收盤：{relation}\n"
        f"看法用一句話寫「{relation}」，並寫明結論仍以今日收盤為準。"
        "不要寫前一日的標題、張數或點數。\n"
        if relation
        else "沒有可對照的前一日收盤方向。看法不要寫「違逆」或「順從」。\n"
    )
    return (
        "這是「題材新聞」任務：只根據這一輪的事件表寫一頁。\n"
        "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
        f"{narrative_rules()}\n\n"
        f"{relation_line}\n"
        f"事件表路徑：{events_path}\n"
        f"題材：{table.get('label') or table.get('theme_id') or ''}\n"
        f"日期：{table.get('as_of') or ''}\n"
        f"{quota}\n\n"
        "事件表：\n"
        f"{payload}\n"
    )


def build_fix_prompt(
    table: dict,
    events_path: Path,
    previous_body: str,
    validation: ValidationResult,
    *,
    min_citations: int,
) -> str:
    issues = "\n".join(f"- {line}" for line in validation.summary_lines())
    opening = build_report_prompt(table, events_path, min_citations=min_citations)
    return (
        "上一版題材新聞未通過自動驗證。請依問題重寫完整的一頁三段，印在 stdout。\n"
        "仍然只能使用事件表裡的事實。\n\n"
        f"驗證問題：\n{issues}\n\n"
        f"{opening}\n"
        "上一版全文：\n"
        f"{previous_body.strip()}\n"
    )


def render_summary(table: dict, body: str) -> str:
    label = str(table.get("label") or table.get("theme_id") or "題材")
    theme_id = str(table.get("theme_id") or "")
    events = table.get("events") if isinstance(table.get("events"), list) else []
    today_close, _prior_close, _other = split_report_events(table)
    return (
        f"# {label}題材新聞\n\n"
        f"**題材：** {theme_id} {label}  \n"
        f"**報告日期：** {table.get('as_of') or ''}  \n"
        f"**事件數：** {len(events)}（當日收盤 {len(today_close)}）  \n"
        f"**摘要模型：** agy\n\n"
        f"---\n\n"
        f"{body.strip()}\n"
    )


def extract_body(summary_text: str) -> str:
    marker = "\n---\n\n"
    if marker in summary_text:
        return summary_text.split(marker, 1)[1].strip()
    return summary_text.strip()


def _body_with_previous_diff(body: str, table: dict, events_path: Path) -> str:
    date_str = report_date(events_path, table)
    previous = load_previous_events(events_path, date_str, str(table.get("theme_id") or ""))
    if previous is None:
        return strip_diff_section(body)
    path = previous_events_path(events_path, date_str)
    if path is not None:
        print(f"前一份事件表：{path}", file=sys.stderr)
    return append_diff_section(body, compare_event_tables(table, previous))


def artifact_paths(events_path: Path, date_str: str) -> dict[str, Path]:
    folder = events_path.parent
    return {
        "summary": folder / f"{date_str}_summary.md",
        "gate_log": folder / f"{date_str}.gate.log",
        "rounds": folder / f"{date_str}.gate.rounds",
    }


def execute_report(
    table: dict,
    events_path: Path,
    *,
    summary_path: Path,
    gate_log_path: Path,
    gate_rounds_dir: Path,
    max_rounds: int,
    min_citations: int,
    complete: Completer,
) -> int:
    """Run the validate loop. Write the summary only when a round passes."""
    body = ""
    validation = ValidationResult(passed=False, issues=[])
    summaries: list[dict[str, Any]] = []

    for round_no in range(1, max_rounds + 1):
        started = time.time()
        print(f"\n=== Concept News Gate Round {round_no}/{max_rounds} ===", file=sys.stderr)
        if round_no == 1:
            prompt = build_report_prompt(table, events_path, min_citations=min_citations)
        else:
            prompt = build_fix_prompt(
                table,
                events_path,
                body,
                validation,
                min_citations=min_citations,
            )
        try:
            body, agy_exit = complete(prompt)
        except RuntimeError as exc:
            _record_round(
                round_no,
                prompt=prompt,
                body="",
                validation=ValidationResult(
                    passed=False,
                    issues=[],
                ),
                agy_exit_code=1,
                duration=time.time() - started,
                gate_log_path=gate_log_path,
                gate_rounds_dir=gate_rounds_dir,
                date_str=str(table.get("as_of") or ""),
                extra_issues=[str(exc)],
                summaries=summaries,
            )
            _write_rounds_index(gate_rounds_dir, str(table.get("as_of") or ""), summaries, passed=False)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        validation = validate_concept_report(body, table, min_citations=min_citations)
        duration = time.time() - started
        _record_round(
            round_no,
            prompt=prompt,
            body=body,
            validation=validation,
            agy_exit_code=agy_exit,
            duration=duration,
            gate_log_path=gate_log_path,
            gate_rounds_dir=gate_rounds_dir,
            date_str=str(table.get("as_of") or ""),
            extra_issues=[],
            summaries=summaries,
        )
        _print_validation(validation, round_no=round_no)
        if validation.passed:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                render_summary(table, _body_with_previous_diff(body, table, events_path)),
                encoding="utf-8",
            )
            _write_rounds_index(gate_rounds_dir, str(table.get("as_of") or ""), summaries, passed=True)
            print(summary_path)
            print(f"Gate log：{gate_log_path}")
            return 0

    _write_rounds_index(gate_rounds_dir, str(table.get("as_of") or ""), summaries, passed=False)
    print(
        f"ERROR: 已達最大輪數 {max_rounds}，報告仍未通過驗證。詳見 {gate_log_path}",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None, *, complete: Completer | None = None) -> int:
    parser = argparse.ArgumentParser(description="依概念題材事件表寫一頁報告並驗證")
    parser.add_argument("--theme", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None, help="事件表根目錄，預設 reports/concept-news")
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS_DEFAULT)
    parser.add_argument("--min-citations", type=int, default=MIN_CITED_EVENTS)
    parser.add_argument("--force", action="store_true", help="已有 summary 時仍重跑")
    parser.add_argument("--validate-only", action="store_true", help="只驗證既有 summary，不呼叫 agy")
    args = parser.parse_args(argv)

    if args.max_rounds < 1:
        print("ERROR: --max-rounds 至少為 1", file=sys.stderr)
        return 1
    if args.min_citations < 1:
        print("ERROR: --min-citations 至少為 1", file=sys.stderr)
        return 1

    events_path = _resolve_events_path(args.events, args.theme, args.date, args.output)
    if events_path is None:
        print("ERROR: 請給 --theme，或給 --events", file=sys.stderr)
        return 1
    if not events_path.is_file():
        print(f"ERROR: 找不到事件表 {events_path}", file=sys.stderr)
        print("請先寫出該日的 events.json。cluster_events.py 不要加 --stdout。", file=sys.stderr)
        return 1

    try:
        table = load_event_table(events_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    date_str = report_date(events_path, table, args.date)
    paths = artifact_paths(events_path, date_str)

    if args.validate_only:
        if not paths["summary"].is_file():
            print(f"ERROR: 找不到既有報告 {paths['summary']}", file=sys.stderr)
            return 1
        body = extract_body(paths["summary"].read_text(encoding="utf-8"))
        validation = validate_concept_report(body, table, min_citations=args.min_citations)
        _print_validation(validation, round_no=0)
        return 0 if validation.passed else 1

    if paths["summary"].is_file() and not args.force:
        print(f"報告已存在：{paths['summary']}", file=sys.stderr)
        return 0

    completer = complete if complete is not None else run_agy
    if complete is None:
        try:
            resolve_agy_bin()
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    return execute_report(
        table,
        events_path,
        summary_path=paths["summary"],
        gate_log_path=paths["gate_log"],
        gate_rounds_dir=paths["rounds"],
        max_rounds=args.max_rounds,
        min_citations=args.min_citations,
        complete=completer,
    )


def resolve_agy_bin() -> str:
    try:
        return _import_llm().agy_executable()
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc


def run_agy(prompt: str) -> tuple[str, int]:
    llm = _import_llm()
    try:
        done = llm.invoke_agy(
            prompt,
            timeout_sec=llm.AGY_TIMEOUT_SEC,
            min_chars=llm.GATE_MIN_CHARS,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc
    return done.text, done.exit_code


def _import_llm():
    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from agent import llm

    return llm


def _resolve_events_path(
    events: Path | None,
    theme: str,
    day: str,
    output: Path | None,
) -> Path | None:
    if events is not None:
        return events
    key = theme.strip()
    if not key:
        return None
    date = day.strip() or today_text()
    root = output if output is not None else None
    return default_events_path(key, date, root)


def _record_round(
    round_no: int,
    *,
    prompt: str,
    body: str,
    validation: ValidationResult,
    agy_exit_code: int,
    duration: float,
    gate_log_path: Path,
    gate_rounds_dir: Path,
    date_str: str,
    extra_issues: list[str],
    summaries: list[dict[str, Any]],
) -> None:
    issues = extra_issues + validation.summary_lines()
    passed = validation.passed and not extra_issues
    paths = _round_paths(gate_rounds_dir, round_no)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["prompt"].write_text(prompt.strip() + "\n", encoding="utf-8")
    paths["body"].write_text(body.strip() + "\n", encoding="utf-8")
    paths["validation"].write_text(
        json.dumps(
            {
                "round": round_no,
                "passed": passed,
                "agy_exit_code": agy_exit_code,
                "duration_sec": round(duration, 2),
                "issues": [{"message": issue} for issue in issues],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    summaries.append(
        {
            "round": round_no,
            "passed": passed,
            "duration_sec": duration,
            "issues": issues,
        }
    )
    _append_gate_log(
        gate_log_path,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "report_date": date_str,
            "round": round_no,
            "passed": passed,
            "agy_exit_code": agy_exit_code,
            "duration_sec": round(duration, 2),
            "issues": issues,
            "prompt_path": str(paths["prompt"]),
            "body_path": str(paths["body"]),
            "validation_path": str(paths["validation"]),
        },
    )


def _round_paths(gate_rounds_dir: Path, round_no: int) -> dict[str, Path]:
    base = gate_rounds_dir / f"r{round_no:02d}"
    return {
        "prompt": base.with_suffix(".prompt.txt"),
        "body": base.with_suffix(".body.md"),
        "validation": base.with_suffix(".validation.json"),
    }


def _append_gate_log(log_path: Path, entry: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_rounds_index(
    gate_rounds_dir: Path,
    date_str: str,
    summaries: list[dict[str, Any]],
    *,
    passed: bool,
) -> None:
    gate_rounds_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Concept News Gate — {date_str}",
        "",
        f"- 最終結果：{'PASS' if passed else 'FAIL'}",
        "",
    ]
    for summary in summaries:
        status = "PASS" if summary["passed"] else "FAIL"
        lines.append(f"## Round {summary['round']} — {status}")
        if summary["issues"]:
            lines.extend(f"- {issue}" for issue in summary["issues"])
        else:
            lines.append("- （無）")
        lines.append("")
    (gate_rounds_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _print_validation(validation: ValidationResult, *, round_no: int) -> None:
    if validation.passed:
        print(f"Round {round_no}: PASS", file=sys.stderr)
        return
    print(f"Round {round_no}: FAIL", file=sys.stderr)
    for line in validation.summary_lines():
        print(f"  - {line}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
