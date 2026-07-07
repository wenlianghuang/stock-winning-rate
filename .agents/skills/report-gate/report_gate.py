#!/usr/bin/env python3
"""Report Gate: agy stock report with validation loop until pass or max rounds."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path

from validate_report import ValidationResult, validate_single_stock_report

MAX_ROUNDS_DEFAULT = 3
AGY_TIMEOUT_SEC = 900
EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_AGY_MISSING = 10
EXIT_CSV_MISSING = 20


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ensure_import_paths() -> None:
    root = project_root()
    ui_path = root / "ui"
    stock_skill = root / ".agents" / "skills" / "tw-stock-report"
    for path in (ui_path, stock_skill):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def resolve_agy_bin() -> str:
    custom = os.environ.get("AGY_BIN", "").strip()
    if custom:
        return custom
    found = shutil.which("agy")
    if not found:
        print(
            "CRITICAL_ERROR: 找不到 agy 指令。請安裝 Antigravity CLI 或設定 AGY_BIN。",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_AGY_MISSING)
    return found


def _load_agy_helpers():
    _ensure_import_paths()
    from agy_output import agy_output_usable, clean_agy_output

    return clean_agy_output, agy_output_usable


def _load_report_writer():
    _ensure_import_paths()
    from report_pdf import ReportMeta, write_summary_artifacts

    return ReportMeta, write_summary_artifacts


def _load_chip_facts(csv_path: Path):
    """Compute deterministic chip facts from the snapshot row + history CSV."""
    _ensure_import_paths()
    from chip_signals import build_chip_facts, facts_summary_for_prompt, write_facts_json
    from chip_tables import load_history_rows

    row = parse_csv_row(csv_path)
    history_rows = load_history_rows(csv_path)
    facts = build_chip_facts(row, history_rows)
    facts_path = csv_path.with_suffix(".facts.json")
    try:
        write_facts_json(facts_path, facts)
    except OSError:
        pass
    return facts, facts_summary_for_prompt(facts)


def parse_csv_row(csv_path: Path) -> dict[str, str]:
    text = csv_path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(StringIO(text)))
    if len(rows) != 1:
        raise ValueError(f"Report Gate 僅支援單檔 CSV，但 {csv_path.name} 有 {len(rows)} 列")
    return rows[0]


def fetch_news_text(row: dict[str, str]) -> str | None:
    stock_id = str(row.get("代碼", "")).strip()
    if not stock_id:
        return None
    _ensure_import_paths()
    from stock_news import fetch_news_text as _fetch

    return _fetch(
        stock_id,
        str(row.get("名稱", stock_id)).strip(),
        str(row.get("日期", "")).strip(),
    )


def find_csv_path(stock_id: str, trade_date: str | None) -> Path | None:
    stock_root = project_root() / "reports" / "stock"
    if not stock_root.exists():
        return None

    if trade_date:
        candidate = stock_root / trade_date / f"tw_stock_{stock_id}.csv"
        return candidate if candidate.exists() else None

    matches = sorted(stock_root.glob(f"*/tw_stock_{stock_id}.csv"))
    return matches[-1] if matches else None


def history_csv_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(f"{snapshot_path.stem}_history.csv")


def build_initial_prompt(
    csv_path: Path,
    row: dict[str, str],
    facts_summary: str,
    news_text: str | None,
    user_prompt: str,
) -> str:
    _ensure_import_paths()
    from stock_prompts import build_single_stock_analysis_prompt_suffix

    stock_id = str(row.get("代碼", "")).strip()
    stock_name = str(row.get("名稱", stock_id)).strip()
    if news_text:
        news_section = (
            "以下為系統已擷取的近期新聞（請在分析中引用，不可臆造未列出的新聞）：\n\n"
            f"{news_text}\n"
        )
    else:
        news_section = (
            "近期新聞：未取得（請僅依 facts 籌碼分析，並註明缺少新聞來源）。\n"
        )
    suffix = build_single_stock_analysis_prompt_suffix(
        stock_name=stock_name,
        stock_id=stock_id,
        facts_summary=facts_summary,
        news_section=news_section,
    )
    return f"使用者請求：{user_prompt}\n\n{suffix}\n"


FIX_HINT_BY_CODE: dict[str, str] = {
    "fact_foreign_direction": "外資方向與系統 facts 相反，請改為與 facts 一致的買/賣方向描述",
    "fact_ma5_position": "收盤相對 MA5 的位置與 facts 相反，請依 facts 修正站上/跌破描述",
    "fact_divergence_ignored": "facts 已標記量價背離/風險旗標，正文不可描述為籌碼健康或量價配合良好",
    "anchors_underused": "正文引用的系統 anchors 不足，請在趨勢/交叉對照章節明確引用至少 2 條 anchors",
    "missing_trend_analysis": "請補「近 N 日籌碼趨勢」章節，明確描述延續/轉折/背離",
}


def _targeted_fix_lines(
    validation: ValidationResult, facts_summary: str
) -> str:
    lines: list[str] = []
    for issue in validation.issues:
        hint = FIX_HINT_BY_CODE.get(issue.code)
        lines.append(f"- [{issue.code}] {hint or issue.message}")
    return "\n".join(lines)


def build_fix_prompt(
    csv_path: Path,
    row: dict[str, str],
    facts_summary: str,
    news_text: str | None,
    previous_body: str,
    validation: ValidationResult,
) -> str:
    stock_id = str(row.get("代碼", "")).strip()
    stock_name = str(row.get("名稱", stock_id)).strip()
    issues = _targeted_fix_lines(validation, facts_summary)
    news_note = "系統已提供新聞" if news_text else "系統未取得新聞，請註明"
    return (
        f"上一版「{stock_name}（{stock_id}）」單檔股報未通過自動驗證。\n"
        f"請依下列問題修正後，輸出完整新版報告正文到 stdout（Markdown）。\n\n"
        f"=== 系統籌碼事實（facts，方向以此為準）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"驗證問題（含修正指引）：\n{issues}\n\n"
        f"新聞狀態：{news_note}\n\n"
        "修正要求：\n"
        "- 方向（買/賣、偏多/偏空、站上/跌破 MA5）必須與 facts 一致\n"
        "- 正文須明確引用 facts 的 anchors（至少 2 條）\n"
        "- 籌碼數字由系統表格自動產生，正文勿重複列數字\n"
        "- 僅「近期新聞與事件」使用 Markdown 表格\n"
        "- 交叉對照、情境推演、觀察重點請用**文字條列**，不要用表格\n"
        "- 須含「近 N 日籌碼趨勢」章節，描述延續/轉折/背離\n"
        "- 補齊缺少的章節與免責聲明\n"
        "- 不可臆造新聞；僅能引用系統提供的新聞或註明缺少新聞\n"
        "- 不要加工作摘要或工具操作說明\n\n"
        "上一版全文：\n"
        f"{previous_body.strip()}\n"
    )


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> tuple[str, int]:
    agy_bin = resolve_agy_bin()
    clean_agy_output, agy_output_usable = _load_agy_helpers()
    print("agy 產生 / 修正報告中…", file=sys.stderr)
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
        raise RuntimeError(
            f"agy 輸出不可用（exit {result.returncode}）：{detail}"
        )
    return body, result.returncode


FACT_ISSUE_PREFIXES = ("fact_", "anchors_")


def _layer_status(validation: ValidationResult) -> dict[str, str]:
    """Classify issue codes into gate layers for observability."""
    fact_codes = [
        issue.code
        for issue in validation.issues
        if issue.code.startswith(FACT_ISSUE_PREFIXES)
    ]
    format_codes = [
        issue.code
        for issue in validation.issues
        if not issue.code.startswith(FACT_ISSUE_PREFIXES)
    ]
    return {
        "format": "fail" if format_codes else "pass",
        "facts": "fail" if fact_codes else "pass",
    }


@dataclass
class RoundLog:
    round: int
    passed: bool
    agy_exit_code: int | None
    duration_sec: float
    issues: list[str]
    issue_codes: list[str] = field(default_factory=list)
    layers: dict[str, str] = field(default_factory=dict)
    prompt_path: str = ""
    body_path: str = ""
    validation_path: str = ""


def round_artifacts_dir(csv_path: Path) -> Path:
    return csv_path.parent / f"{csv_path.stem}.gate.rounds"


def round_artifact_paths(csv_path: Path, round_no: int) -> dict[str, Path]:
    base = round_artifacts_dir(csv_path) / f"r{round_no:02d}"
    return {
        "prompt": base.with_suffix(".prompt.txt"),
        "body": base.with_suffix(".body.md"),
        "validation": base.with_suffix(".validation.json"),
    }


def write_round_artifacts(
    csv_path: Path,
    round_no: int,
    *,
    prompt: str,
    body: str,
    validation: ValidationResult,
    agy_exit_code: int,
    duration_sec: float,
) -> dict[str, Path]:
    paths = round_artifact_paths(csv_path, round_no)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    paths["prompt"].write_text(prompt.strip() + "\n", encoding="utf-8")
    paths["body"].write_text(body.strip() + "\n", encoding="utf-8")
    paths["validation"].write_text(
        json.dumps(
            {
                "round": round_no,
                "passed": validation.passed,
                "agy_exit_code": agy_exit_code,
                "duration_sec": round(duration_sec, 2),
                "issues": [
                    {"code": issue.code, "message": issue.message}
                    for issue in validation.issues
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return paths


def write_rounds_index(
    csv_path: Path,
    round_summaries: list[dict],
    *,
    final_passed: bool,
) -> Path:
    rounds_dir = round_artifacts_dir(csv_path)
    rounds_dir.mkdir(parents=True, exist_ok=True)
    index_path = rounds_dir / "index.md"

    lines = [
        f"# Report Gate 各輪比較 — `{csv_path.name}`",
        "",
        f"- **產生時間：** {datetime.now().isoformat(timespec='seconds')}",
        f"- **最終結果：** {'PASS' if final_passed else 'FAIL'}",
        "",
        "## 輪次摘要",
        "",
        "| 輪次 | 結果 | 耗時 (s) | 問題數 | 檔案 |",
        "|------|------|----------|--------|------|",
    ]

    for summary in round_summaries:
        round_no = summary["round"]
        rel = f"r{round_no:02d}"
        status = "PASS" if summary["passed"] else "FAIL"
        lines.append(
            f"| {round_no} | {status} | {summary['duration_sec']:.1f} | "
            f"{len(summary['issues'])} | "
            f"[正文]({rel}.body.md) · [prompt]({rel}.prompt.txt) · "
            f"[驗證]({rel}.validation.json) |"
        )

    lines.extend(["", "## 各輪驗證問題", ""])
    for summary in round_summaries:
        round_no = summary["round"]
        lines.append(f"### Round {round_no} — {'PASS' if summary['passed'] else 'FAIL'}")
        if summary["issues"]:
            for issue in summary["issues"]:
                lines.append(f"- {issue}")
        else:
            lines.append("- （無）")
        lines.append("")

    lines.extend(
        [
            "## 如何比較修正",
            "",
            "1. 先看 `r01.validation.json` 的 `issues`，了解第一輪哪裡不合格。",
            "2. 打開 `r02.prompt.txt`，確認第二輪是否把這些問題餵回 agy。",
            "3. 對照 `r01.body.md` 與 `r02.body.md`，看 LLM 是否針對缺漏修正。",
            "",
        ]
    )

    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def append_gate_log(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def extract_body_from_saved_md(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    marker = "\n---\n\n"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def run_gate(
    csv_path: Path,
    *,
    max_rounds: int,
    user_prompt: str,
    validate_only: bool,
    skip_pdf: bool,
) -> int:
    row = parse_csv_row(csv_path)
    facts, facts_summary = _load_chip_facts(csv_path)
    news_text = fetch_news_text(row)
    has_news = bool(news_text and news_text.strip())
    log_path = csv_path.with_suffix(".gate.log")
    gate_started = time.time()
    round_summaries: list[dict] = []

    if validate_only:
        md_path = csv_path.with_suffix(".md")
        if not md_path.exists():
            print(f"ERROR: 找不到既有報告 {md_path}", file=sys.stderr)
            return EXIT_CSV_MISSING
        body = extract_body_from_saved_md(md_path)
        validation = validate_single_stock_report(
            body, row, facts=facts, has_news=has_news
        )
        _print_validation(validation, round_no=0)
        return EXIT_OK if validation.passed else EXIT_VALIDATION_FAILED

    body = ""
    agy_exit = 0
    validation = ValidationResult(passed=False)
    for round_no in range(1, max_rounds + 1):
        round_started = time.time()
        print(f"\n=== Report Gate Round {round_no}/{max_rounds} ===", file=sys.stderr)

        if round_no == 1:
            prompt = build_initial_prompt(
                csv_path, row, facts_summary, news_text, user_prompt
            )
        else:
            prompt = build_fix_prompt(
                csv_path, row, facts_summary, news_text, body, validation
            )

        body, agy_exit = run_agy(prompt)
        validation = validate_single_stock_report(
            body, row, facts=facts, has_news=has_news
        )
        duration = time.time() - round_started

        artifact_paths = write_round_artifacts(
            csv_path,
            round_no,
            prompt=prompt,
            body=body,
            validation=validation,
            agy_exit_code=agy_exit,
            duration_sec=duration,
        )
        issue_lines = validation.summary_lines()
        round_summaries.append(
            {
                "round": round_no,
                "passed": validation.passed,
                "duration_sec": duration,
                "issues": issue_lines,
            }
        )

        round_log = RoundLog(
            round=round_no,
            passed=validation.passed,
            agy_exit_code=agy_exit,
            duration_sec=duration,
            issues=issue_lines,
            issue_codes=[issue.code for issue in validation.issues],
            layers=_layer_status(validation),
            prompt_path=str(artifact_paths["prompt"]),
            body_path=str(artifact_paths["body"]),
            validation_path=str(artifact_paths["validation"]),
        )
        append_gate_log(
            log_path,
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "csv": str(csv_path),
                **asdict(round_log),
            },
        )
        _print_validation(validation, round_no=round_no)
        print(f"  正文: {artifact_paths['body']}", file=sys.stderr)
        print(f"  prompt: {artifact_paths['prompt']}", file=sys.stderr)

        if validation.passed:
            index_path = write_rounds_index(
                csv_path, round_summaries, final_passed=True
            )
            ReportMeta, write_summary_artifacts = _load_report_writer()
            total_duration = time.time() - gate_started
            meta = ReportMeta(
                source="report-gate / agy",
                version="deep",
                duration_sec=total_duration,
                job_id=f"gate-{csv_path.stem}-r{round_no}",
                exit_code=agy_exit,
            )
            md_path, pdf_path = write_summary_artifacts(csv_path, body, meta)
            print(f"驗證通過（第 {round_no} 輪）", file=sys.stderr)
            print(f"Markdown: {md_path.resolve()}")
            if pdf_path and not skip_pdf:
                print(f"PDF: {pdf_path.resolve()}")
            elif skip_pdf:
                print("PDF: 已略過（--skip-pdf）", file=sys.stderr)
            else:
                print("PDF: 未產生（請確認 fpdf2 與中文字型）", file=sys.stderr)
            print(f"Gate log: {log_path.resolve()}")
            print(f"各輪比較: {index_path.resolve()}")
            print(f"各輪目錄: {round_artifacts_dir(csv_path).resolve()}")
            return EXIT_OK

    index_path = write_rounds_index(csv_path, round_summaries, final_passed=False)
    print(
        f"ERROR: 已達最大輪數 {max_rounds}，報告仍未通過驗證。",
        file=sys.stderr,
    )
    print(f"Gate log: {log_path.resolve()}", file=sys.stderr)
    print(f"各輪比較: {index_path.resolve()}", file=sys.stderr)
    print(f"各輪目錄: {round_artifacts_dir(csv_path).resolve()}", file=sys.stderr)
    return EXIT_VALIDATION_FAILED


def _print_validation(validation: ValidationResult, *, round_no: int) -> None:
    if validation.passed:
        print(f"Round {round_no}: PASS", file=sys.stderr)
        return
    print(f"Round {round_no}: FAIL", file=sys.stderr)
    for line in validation.summary_lines():
        print(f"  - {line}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report Gate：agy 產股報 + 驗證閉環（loop engineering）",
    )
    parser.add_argument(
        "stock_id",
        nargs="?",
        help="台股代碼，例如 2409",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="直接指定 CSV 路徑（優先於 stock_id）",
    )
    parser.add_argument(
        "--date",
        help="交易日 YYYY-MM-DD（搭配 stock_id）",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=MAX_ROUNDS_DEFAULT,
        help=f"最多 agy 輪數（預設 {MAX_ROUNDS_DEFAULT}）",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只驗證既有 .md，不呼叫 agy",
    )
    parser.add_argument(
        "--skip-pdf",
        action="store_true",
        help="通過驗證後不產生 PDF",
    )
    parser.add_argument(
        "--prompt",
        default="產出單檔台股籌碼深度分析報告",
        help="傳給 agy 的使用者請求描述",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_import_paths()
    args = build_parser().parse_args(argv)

    if args.csv:
        csv_path = args.csv.expanduser().resolve()
        if not csv_path.exists():
            print(f"ERROR: CSV 不存在：{csv_path}", file=sys.stderr)
            return EXIT_CSV_MISSING
    elif args.stock_id:
        csv_path = find_csv_path(args.stock_id.strip(), args.date)
        if csv_path is None:
            hint = f"（日期 {args.date}）" if args.date else ""
            print(
                f"ERROR: 找不到 tw_stock_{args.stock_id}.csv {hint}。"
                f"請先執行 stock-report 抓取資料。",
                file=sys.stderr,
            )
            return EXIT_CSV_MISSING
    else:
        build_parser().print_help()
        return EXIT_VALIDATION_FAILED

    if args.max_rounds < 1:
        print("ERROR: --max-rounds 至少為 1", file=sys.stderr)
        return EXIT_VALIDATION_FAILED

    print(f"CSV: {csv_path.resolve()}", file=sys.stderr)
    return run_gate(
        csv_path,
        max_rounds=args.max_rounds,
        user_prompt=args.prompt,
        validate_only=args.validate_only,
        skip_pdf=args.skip_pdf,
    )


if __name__ == "__main__":
    raise SystemExit(main())
