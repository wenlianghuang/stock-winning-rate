#!/usr/bin/env python3
"""Position Gate: agy position report with validation loop until pass or max rounds."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path

from holdings import (
    DEFAULT_HOLDINGS,
    HOLDING_USAGE_HINT,
    HoldingRecord,
    load_holdings,
    resolve_holding,
)
from validate_position_report import ValidationResult, validate_position_report

MAX_ROUNDS_DEFAULT = 3
AGY_TIMEOUT_SEC = 900
EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_AGY_MISSING = 10
EXIT_CSV_MISSING = 20
EXIT_HOLDING_MISSING = 21


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ensure_import_paths() -> None:
    root = project_root()
    ui_path = root / "ui"
    stock_skill = root / ".agents" / "skills" / "tw-stock-report"
    position_skill = root / ".agents" / "skills" / "position-gate"
    for path in (ui_path, stock_skill, position_skill):
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
    from report_pdf import ReportMeta, write_position_artifacts

    return ReportMeta, write_position_artifacts


def _to_holding_info(record: HoldingRecord):
    _ensure_import_paths()
    from position_prompts import HoldingInfo

    return HoldingInfo(
        stock_id=record.stock_id,
        avg_cost=record.avg_cost,
        shares=record.shares,
        note=record.note,
    )


def parse_csv_row(csv_path: Path) -> dict[str, str]:
    text = csv_path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(StringIO(text)))
    if len(rows) != 1:
        raise ValueError(
            f"Position Gate 僅支援單檔 CSV，但 {csv_path.name} 有 {len(rows)} 列"
        )
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


def market_report_path(snapshot_path: Path) -> Path | None:
    candidate = snapshot_path.with_suffix(".md")
    return candidate if candidate.exists() else None


def build_initial_prompt(
    csv_path: Path,
    row: dict[str, str],
    holding: HoldingRecord,
    news_text: str | None,
    user_prompt: str,
) -> str:
    _ensure_import_paths()
    from position_prompts import build_position_analysis_prompt_suffix
    from position_tables import compute_unrealized_pnl_pct, parse_close_price

    stock_id = str(row.get("代碼", "")).strip()
    stock_name = str(row.get("名稱", stock_id)).strip()
    hist_path = history_csv_path(csv_path)
    holding_info = _to_holding_info(holding)
    close_price = parse_close_price(row)
    pnl_pct = (
        compute_unrealized_pnl_pct(holding.avg_cost, close_price)
        if close_price is not None
        else None
    )
    market_md = market_report_path(csv_path)

    if news_text:
        news_section = (
            "以下為系統已擷取的近期新聞（請在分析中引用，不可臆造未列出的新聞）：\n\n"
            f"{news_text}\n"
        )
    else:
        news_section = (
            "近期新聞：未取得（請僅依 CSV 籌碼分析市場面，並註明缺少新聞來源）。\n"
        )

    suffix = build_position_analysis_prompt_suffix(
        stock_name=stock_name,
        stock_id=stock_id,
        csv_path=str(csv_path),
        history_csv_path=str(hist_path) if hist_path.exists() else None,
        holding=holding_info,
        unrealized_pnl_pct=pnl_pct,
        close_price=close_price,
        news_section=news_section,
        market_report_path=str(market_md) if market_md else None,
    )
    return f"使用者請求：{user_prompt}\n\n{suffix}\n"


def build_fix_prompt(
    csv_path: Path,
    row: dict[str, str],
    holding: HoldingRecord,
    news_text: str | None,
    previous_body: str,
    validation: ValidationResult,
) -> str:
    stock_id = str(row.get("代碼", "")).strip()
    stock_name = str(row.get("名稱", stock_id)).strip()
    issues = "\n".join(f"- {line}" for line in validation.summary_lines())
    news_note = "系統已提供新聞" if news_text else "系統未取得新聞，請註明"
    return (
        f"上一版「{stock_name}（{stock_id}）」持股部位報告未通過自動驗證。\n"
        f"請依下列問題修正後，輸出完整新版報告正文到 stdout（Markdown）。\n\n"
        f"驗證問題：\n{issues}\n\n"
        f"CSV 路徑：{csv_path}\n"
        f"持股均價：{holding.avg_cost} 元，{holding.shares:,} 股\n"
        f"新聞狀態：{news_note}\n\n"
        "修正要求：\n"
        "- 籌碼與部位數字由系統表格自動產生，正文勿重複列數字\n"
        "- 須含部位現況、市場面摘要、交叉對照、操作情境、風險提醒、免責聲明\n"
        "- 操作情境須含觸發條件，並提及觀望/減碼/加碼/停損/獲利了結等方向\n"
        "- 市場面須客觀，勿因成本扭曲籌碼解讀\n"
        "- 交叉對照、操作情境、風險提醒請用**文字條列**，不要用表格\n"
        "- 不可臆造新聞；僅能引用系統提供的新聞或註明缺少新聞\n"
        "- 不要加工作摘要或工具操作說明\n\n"
        "上一版全文：\n"
        f"{previous_body.strip()}\n"
    )


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> tuple[str, int]:
    agy_bin = resolve_agy_bin()
    clean_agy_output, agy_output_usable = _load_agy_helpers()
    print("agy 產生 / 修正部位報告中…", file=sys.stderr)
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


@dataclass
class RoundLog:
    round: int
    passed: bool
    agy_exit_code: int | None
    duration_sec: float
    issues: list[str]
    prompt_path: str = ""
    body_path: str = ""
    validation_path: str = ""


def round_artifacts_dir(csv_path: Path) -> Path:
    return csv_path.parent / f"{csv_path.stem}.position.gate.rounds"


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
        f"# Position Gate 各輪比較 — `{csv_path.name}`",
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

    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def append_gate_log(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def extract_body_from_saved_md(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    marker = "\n---\n\n"
    parts = text.split(marker)
    if len(parts) >= 3:
        return parts[-1].strip()
    if len(parts) == 2:
        return parts[1].strip()
    return text.strip()


def run_gate(
    csv_path: Path,
    holding: HoldingRecord,
    *,
    max_rounds: int,
    user_prompt: str,
    validate_only: bool,
    skip_pdf: bool,
) -> int:
    row = parse_csv_row(csv_path)
    news_text = fetch_news_text(row)
    has_news = bool(news_text and news_text.strip())
    log_path = csv_path.with_name(f"{csv_path.stem}.position.gate.log")
    gate_started = time.time()
    round_summaries: list[dict] = []
    holding_info = _to_holding_info(holding)

    if validate_only:
        md_path = csv_path.with_name(f"{csv_path.stem}_position.md")
        if not md_path.exists():
            print(f"ERROR: 找不到既有部位報告 {md_path}", file=sys.stderr)
            return EXIT_CSV_MISSING
        body = extract_body_from_saved_md(md_path)
        validation = validate_position_report(
            body, row, holding, has_news=has_news
        )
        _print_validation(validation, round_no=0)
        return EXIT_OK if validation.passed else EXIT_VALIDATION_FAILED

    body = ""
    agy_exit = 0
    validation = ValidationResult(passed=False)
    for round_no in range(1, max_rounds + 1):
        round_started = time.time()
        print(
            f"\n=== Position Gate Round {round_no}/{max_rounds} ===",
            file=sys.stderr,
        )

        if round_no == 1:
            prompt = build_initial_prompt(
                csv_path, row, holding, news_text, user_prompt
            )
        else:
            prompt = build_fix_prompt(
                csv_path, row, holding, news_text, body, validation
            )

        body, agy_exit = run_agy(prompt)
        validation = validate_position_report(
            body, row, holding, has_news=has_news
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
            prompt_path=str(artifact_paths["prompt"]),
            body_path=str(artifact_paths["body"]),
            validation_path=str(artifact_paths["validation"]),
        )
        append_gate_log(
            log_path,
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "csv": str(csv_path),
                "holding": {
                    "avg_cost": holding.avg_cost,
                    "shares": holding.shares,
                },
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
            ReportMeta, write_position_artifacts = _load_report_writer()
            total_duration = time.time() - gate_started
            meta = ReportMeta(
                source="position-gate / agy",
                version="position",
                duration_sec=total_duration,
                job_id=f"position-gate-{csv_path.stem}-r{round_no}",
                exit_code=agy_exit,
            )
            md_path, pdf_path = write_position_artifacts(
                csv_path, body, meta, holding_info
            )
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
        f"ERROR: 已達最大輪數 {max_rounds}，部位報告仍未通過驗證。",
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
        description="Position Gate：agy 產持股部位報告 + 驗證閉環",
    )
    parser.add_argument(
        "stock_id",
        nargs="?",
        help="台股代碼，例如 2409",
    )
    parser.add_argument(
        "avg_cost_pos",
        nargs="?",
        type=float,
        help="簡寫：持股均價（元），例 position_gate.py 2409 32.5 500",
    )
    parser.add_argument(
        "shares_pos",
        nargs="?",
        type=int,
        help="簡寫：持股股數",
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
        "--holdings",
        type=Path,
        default=DEFAULT_HOLDINGS,
        help=f"持股設定檔（僅 --all-holdings 或省略 CLI 均價/股數時使用，預設 {DEFAULT_HOLDINGS.name}）",
    )
    parser.add_argument(
        "--avg-cost",
        "--cost",
        dest="avg_cost_opt",
        type=float,
        metavar="PRICE",
        help="持股均價（元）",
    )
    parser.add_argument(
        "--shares",
        dest="shares_opt",
        type=int,
        metavar="N",
        help="持股股數",
    )
    parser.add_argument(
        "--lots",
        dest="lots_opt",
        type=int,
        metavar="N",
        help="（相容）持股張數，會換算為股數（×1000）",
    )
    parser.add_argument(
        "--note",
        default="",
        help="持股備註（選填）",
    )
    parser.add_argument(
        "--from-holdings",
        action="store_true",
        help="強制從 holdings.json 讀取該股（忽略未傳的 CLI 均價/股數）",
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
        help="只驗證既有 _position.md，不呼叫 agy",
    )
    parser.add_argument(
        "--skip-pdf",
        action="store_true",
        help="通過驗證後不產生 PDF",
    )
    parser.add_argument(
        "--prompt",
        default="產出單檔持股部位決策報告",
        help="傳給 agy 的使用者請求描述",
    )
    parser.add_argument(
        "--all-holdings",
        action="store_true",
        help="處理 holdings.json 中全部持股（忽略 stock_id）",
    )
    return parser


def _effective_holding_inputs(args: argparse.Namespace) -> tuple[float | None, int | None, str]:
    avg_cost = (
        args.avg_cost_opt
        if args.avg_cost_opt is not None
        else args.avg_cost_pos
    )
    shares = args.shares_opt if args.shares_opt is not None else args.shares_pos
    if shares is None and args.lots_opt is not None:
        shares = int(args.lots_opt) * 1000
    return avg_cost, shares, str(args.note or "").strip()


def main(argv: list[str] | None = None) -> int:
    _ensure_import_paths()
    args = build_parser().parse_args(argv)
    holdings_path = args.holdings.expanduser().resolve()
    avg_cost, shares, note = _effective_holding_inputs(args)

    if args.all_holdings:
        try:
            holdings = load_holdings(holdings_path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_HOLDING_MISSING

        last_code = EXIT_OK
        for stock_id in sorted(holdings):
            holding = holdings[stock_id]
            csv_path = find_csv_path(stock_id, args.date)
            if csv_path is None:
                print(
                    f"ERROR: 找不到 tw_stock_{stock_id}.csv，請先執行 stock-report。",
                    file=sys.stderr,
                )
                return EXIT_CSV_MISSING
            print(f"\n>>> Position Gate: {stock_id}", file=sys.stderr)
            code = run_gate(
                csv_path,
                holding,
                max_rounds=args.max_rounds,
                user_prompt=args.prompt,
                validate_only=args.validate_only,
                skip_pdf=args.skip_pdf,
            )
            if code != EXIT_OK:
                return code
            last_code = code
        return last_code

    if args.csv:
        csv_path = args.csv.expanduser().resolve()
        if not csv_path.exists():
            print(f"ERROR: CSV 不存在：{csv_path}", file=sys.stderr)
            return EXIT_CSV_MISSING
        stock_id = csv_path.stem.replace("tw_stock_", "")
    elif args.stock_id:
        stock_id = args.stock_id.strip()
        csv_path = find_csv_path(stock_id, args.date)
        if csv_path is None:
            hint = f"（日期 {args.date}）" if args.date else ""
            print(
                f"ERROR: 找不到 tw_stock_{stock_id}.csv {hint}。"
                f"請先執行 stock-report 抓取資料。",
                file=sys.stderr,
            )
            return EXIT_CSV_MISSING
    else:
        print(
            "ERROR: 請提供股票代碼，例如：position-gate 2409 32.5 500",
            file=sys.stderr,
        )
        print(HOLDING_USAGE_HINT, file=sys.stderr)
        build_parser().print_help()
        return EXIT_VALIDATION_FAILED

    try:
        holding = resolve_holding(
            stock_id,
            holdings_path,
            avg_cost=avg_cost,
            shares=shares,
            note=note,
            from_holdings_file=args.from_holdings,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_HOLDING_MISSING

    if args.max_rounds < 1:
        print("ERROR: --max-rounds 至少為 1", file=sys.stderr)
        return EXIT_VALIDATION_FAILED

    print(f"CSV: {csv_path.resolve()}", file=sys.stderr)
    print(
        f"持股：均價 {holding.avg_cost} 元 × {holding.shares:,} 股",
        file=sys.stderr,
    )
    return run_gate(
        csv_path,
        holding,
        max_rounds=args.max_rounds,
        user_prompt=args.prompt,
        validate_only=args.validate_only,
        skip_pdf=args.skip_pdf,
    )


if __name__ == "__main__":
    raise SystemExit(main())
