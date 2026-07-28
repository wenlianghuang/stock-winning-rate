#!/usr/bin/env python3
"""Portfolio Gate: agy portfolio narrative with validation loop.

Produces portfolios for:
- beginner: one per risk profile
- theme (v1): one sleeve for user-selected themes (chips + theme universe)

Python builds the deterministic allocation (portfolio_signals), agy writes the
narrative, and validate_portfolio_report rejects contradictions — feeding issues
back to agy for up to MAX_ROUNDS.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from portfolio_data import (
    DEFAULT_THEME_UNIVERSE,
    DEFAULT_UNIVERSE,
    build_candidates,
    ensure_import_paths,
    output_dir,
    project_root,
    resolve_trade_date,
)
from validate_portfolio_report import ValidationResult, validate_portfolio_report

MAX_ROUNDS_DEFAULT = 8
AGY_TIMEOUT_SEC = 900
PROFILE_CHOICES = ("conservative", "balanced", "aggressive")

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_AGY_MISSING = 10
EXIT_NO_DATA = 20
EXIT_INSUFFICIENT = 22
EXIT_BAD_ARGS = 2


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
    ensure_import_paths()
    from agy_output import agy_output_usable, clean_agy_output

    return clean_agy_output, agy_output_usable


FIX_HINT_BY_CODE: dict[str, str] = {
    "too_short": "正文過短，請把六段內容寫完整（尤其每檔理由與紀律）",
    "missing_profile_label": "正文須點出組合名稱／風險屬性",
    "missing_theme_label": "正文須點出主題名稱（如金融／散熱／AI）",
    "missing_fit": "請補「這個組合適合誰」段落",
    "missing_risk": "請補「風險/波動」段落，說明波動代表的意義",
    "missing_discipline": "請補紀律段落（下跌怎麼辦、分批、定期檢視）",
    "missing_disclaimer": "請補一句話免責聲明",
    "forbidden_phrase": "請移除保證/穩賺/一定漲/目標價等字眼",
    "portfolio_fact_risk_mismatch": "風險定位描述與系統風險屬性衝突，請對齊",
    "portfolio_fact_theme_safety_mismatch": "主題組合不可寫成保本/全市場分散，請強調袖口集中風險",
    "portfolio_fact_hallucinated_holding": "不可把未選入的股票寫成建議持股，只能討論配置表中的持股",
    "portfolio_reasoning_discipline_incomplete": "紀律須至少涵蓋兩項：下跌應對、分批進場、定期檢視",
    "portfolio_reasoning_diversification_unmentioned": "須向新手解釋分散/一籃子（ETF）的概念",
    "portfolio_reasoning_theme_concentration_unmentioned": "須說明主題集中／袖口曝險",
    "portfolio_reasoning_fit_unjustified": "「適合誰」須用波動/風險/主題用途等特性說明",
}


def _parse_themes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip()]


def _targeted_fix_lines(validation: ValidationResult) -> str:
    lines: list[str] = []
    for issue in validation.issues:
        hint = FIX_HINT_BY_CODE.get(issue.code)
        lines.append(f"- [{issue.code}] {hint or issue.message}")
    return "\n".join(lines)


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> tuple[str, int]:
    agy_bin = resolve_agy_bin()
    clean_agy_output, agy_output_usable = _load_agy_helpers()
    print("agy 產生 / 修正組合報告中…", file=sys.stderr)
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


FACT_ISSUE_PREFIX = "portfolio_fact_"
REASONING_ISSUE_PREFIX = "portfolio_reasoning_"


def _layer_status(validation: ValidationResult) -> dict[str, str]:
    fact_codes = [i.code for i in validation.issues if i.code.startswith(FACT_ISSUE_PREFIX)]
    reasoning_codes = [
        i.code for i in validation.issues if i.code.startswith(REASONING_ISSUE_PREFIX)
    ]
    format_codes = [
        i.code
        for i in validation.issues
        if not i.code.startswith(FACT_ISSUE_PREFIX)
        and not i.code.startswith(REASONING_ISSUE_PREFIX)
    ]
    return {
        "format": "fail" if format_codes else "pass",
        "facts": "fail" if fact_codes else "pass",
        "reasoning": "fail" if reasoning_codes else "pass",
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


def _round_dir(out_dir: Path, profile: str) -> Path:
    return out_dir / f"portfolio_{profile}.gate.rounds"


def _round_paths(out_dir: Path, profile: str, round_no: int) -> dict[str, Path]:
    base = _round_dir(out_dir, profile) / f"r{round_no:02d}"
    return {
        "prompt": base.with_suffix(".prompt.txt"),
        "body": base.with_suffix(".body.md"),
        "validation": base.with_suffix(".validation.json"),
    }


def _write_round_artifacts(
    out_dir: Path,
    profile: str,
    round_no: int,
    *,
    prompt: str,
    body: str,
    validation: ValidationResult,
    agy_exit_code: int,
    duration_sec: float,
) -> dict[str, Path]:
    paths = _round_paths(out_dir, profile, round_no)
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
                    {"code": i.code, "message": i.message} for i in validation.issues
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return paths


def _append_gate_log(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _print_validation(validation: ValidationResult, *, round_no: int) -> None:
    if validation.passed:
        print(f"Round {round_no}: PASS", file=sys.stderr)
        return
    print(f"Round {round_no}: FAIL", file=sys.stderr)
    for line in validation.summary_lines():
        print(f"  - {line}", file=sys.stderr)


def _write_final_artifacts(
    out_dir: Path,
    profile: str,
    facts,
    body: str,
    *,
    duration_sec: float,
    agy_exit: int,
    round_no: int,
    skip_pdf: bool,
) -> tuple[Path, Path | None]:
    ensure_import_paths()
    from portfolio_signals import merge_portfolio_report_body, write_portfolio_facts_json
    from report_pdf import ReportMeta, format_report_metadata, write_portfolio_pdf

    full_body = merge_portfolio_report_body(facts, body)
    meta = ReportMeta(
        source="portfolio-gate / agy",
        version=f"portfolio-{profile}",
        duration_sec=duration_sec,
        job_id=f"portfolio-gate-{profile}-{facts.trade_date}-r{round_no}",
        exit_code=agy_exit,
    )
    title = (
        f"主題投資組合建議（{facts.profile_label}）"
        if getattr(facts, "mode", "beginner") == "theme"
        else f"新手投資組合建議（{facts.profile_label}）"
    )
    md_text = (
        f"# {title}\n\n"
        f"## 報告資訊\n\n{format_report_metadata(meta)}\n\n"
        f"---\n\n{full_body.strip()}\n"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"portfolio_{profile}.md"
    md_path.write_text(md_text, encoding="utf-8")

    # 純敘事（不含確定性表格），供網站以自算的 facts 表格搭配顯示、避免數字重複。
    narrative_path = out_dir / f"portfolio_{profile}.narrative.md"
    narrative_path.write_text(body.strip() + "\n", encoding="utf-8")

    json_path = out_dir / f"portfolio_{profile}.facts.json"
    try:
        write_portfolio_facts_json(json_path, facts)
    except OSError:
        pass

    pdf_path: Path | None = None
    if not skip_pdf:
        pdf_candidate = out_dir / f"portfolio_{profile}.pdf"
        if write_portfolio_pdf(
            pdf_candidate, title=title, subtitle=f"資料日期：{facts.trade_date}",
            body=full_body, meta=meta
        ):
            pdf_path = pdf_candidate
    return md_path, pdf_path


def run_gate(
    profile: str,
    *,
    trade_date: str,
    amount: int | None,
    universe_path: Path,
    max_rounds: int,
    skip_pdf: bool,
    mode: str = "beginner",
    themes: list[str] | None = None,
    theme_catalog: dict | None = None,
    with_fundamentals: bool = True,
    force_fundamentals_refresh: bool = False,
) -> int:
    ensure_import_paths()
    from chip_signals import load_base_rates
    from portfolio_prompts import (
        build_portfolio_analysis_prompt_suffix,
        build_portfolio_fix_prompt,
    )
    from portfolio_signals import (
        build_portfolio_facts,
        build_theme_portfolio_facts,
        portfolio_facts_summary_for_prompt,
        theme_slug,
    )

    candidates = build_candidates(
        trade_date,
        universe_path,
        with_fundamentals=with_fundamentals,
        force_fundamentals_refresh=force_fundamentals_refresh,
    )
    if not any(c.data_available for c in candidates):
        print(
            f"ERROR: {trade_date} 找不到候選標的 CSV，請先執行 stock-report。",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    base_rates = load_base_rates()
    if mode == "theme":
        facts = build_theme_portfolio_facts(
            candidates,
            themes=themes or [],
            base_rates=base_rates,
            amount_twd=amount,
            trade_date=trade_date,
            theme_catalog=theme_catalog,
        )
        artifact_key = theme_slug(themes or [])
    else:
        facts = build_portfolio_facts(
            candidates,
            profile=profile,
            base_rates=base_rates,
            amount_twd=amount,
            trade_date=trade_date,
        )
        artifact_key = profile

    if facts.num_holdings < 2:
        print(
            f"ERROR: {artifact_key} 可用標的不足（僅 {facts.num_holdings} 檔），無法組成組合。",
            file=sys.stderr,
        )
        return EXIT_INSUFFICIENT

    facts_summary = portfolio_facts_summary_for_prompt(facts)
    out_dir = output_dir(trade_date)
    log_path = out_dir / f"portfolio_{artifact_key}.gate.log"
    gate_started = time.time()

    body = ""
    agy_exit = 0
    validation = ValidationResult(passed=False)
    for round_no in range(1, max_rounds + 1):
        round_started = time.time()
        print(
            f"\n=== Portfolio Gate [{artifact_key}] Round {round_no}/{max_rounds} ===",
            file=sys.stderr,
        )
        if round_no == 1:
            suffix = build_portfolio_analysis_prompt_suffix(
                profile_label=facts.profile_label,
                risk_label=facts.risk_label,
                facts_summary=facts_summary,
                mode=mode,
            )
            if mode == "theme":
                prompt = (
                    f"使用者請求：為選定主題產生{facts.profile_label}投資組合說明\n\n{suffix}\n"
                )
            else:
                prompt = f"使用者請求：為新手產生{facts.profile_label}投資組合說明\n\n{suffix}\n"
        else:
            prompt = build_portfolio_fix_prompt(
                profile_label=facts.profile_label,
                risk_label=facts.risk_label,
                facts_summary=facts_summary,
                previous_body=body,
                issues_block=_targeted_fix_lines(validation),
                mode=mode,
            )

        body, agy_exit = run_agy(prompt)
        validation = validate_portfolio_report(body, facts)
        duration = time.time() - round_started

        paths = _write_round_artifacts(
            out_dir,
            artifact_key,
            round_no,
            prompt=prompt,
            body=body,
            validation=validation,
            agy_exit_code=agy_exit,
            duration_sec=duration,
        )
        round_log = RoundLog(
            round=round_no,
            passed=validation.passed,
            agy_exit_code=agy_exit,
            duration_sec=duration,
            issues=validation.summary_lines(),
            issue_codes=[i.code for i in validation.issues],
            layers=_layer_status(validation),
            prompt_path=str(paths["prompt"]),
            body_path=str(paths["body"]),
            validation_path=str(paths["validation"]),
        )
        _append_gate_log(
            log_path,
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "profile": artifact_key,
                "mode": mode,
                "themes": list(getattr(facts, "themes", []) or []),
                "trade_date": trade_date,
                **asdict(round_log),
            },
        )
        _print_validation(validation, round_no=round_no)
        print(f"  正文: {paths['body']}", file=sys.stderr)

        if validation.passed:
            total_duration = time.time() - gate_started
            md_path, pdf_path = _write_final_artifacts(
                out_dir,
                artifact_key,
                facts,
                body,
                duration_sec=total_duration,
                agy_exit=agy_exit,
                round_no=round_no,
                skip_pdf=skip_pdf,
            )
            print(f"驗證通過（第 {round_no} 輪）", file=sys.stderr)
            print(f"Markdown: {md_path.resolve()}")
            if pdf_path:
                print(f"PDF: {pdf_path.resolve()}")
            print(f"Gate log: {log_path.resolve()}")
            return EXIT_OK

    print(
        f"ERROR: [{artifact_key}] 已達最大輪數 {max_rounds}，仍未通過驗證。",
        file=sys.stderr,
    )
    print(f"Gate log: {log_path.resolve()}", file=sys.stderr)
    return EXIT_VALIDATION_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Portfolio Gate：agy 產投資組合報告 + 驗證閉環（新手／主題）",
    )
    parser.add_argument(
        "profile",
        nargs="?",
        choices=[*PROFILE_CHOICES, "all"],
        default="all",
        help="新手模式風險屬性：conservative / balanced / aggressive / all（預設 all）",
    )
    parser.add_argument(
        "--mode",
        choices=["beginner", "theme"],
        default="beginner",
        help="beginner＝新手風險組合；theme＝主題袖口組合（v1）",
    )
    parser.add_argument(
        "--themes",
        default=None,
        help="主題模式必填，逗號分隔，例如 financials 或 financials,thermal",
    )
    parser.add_argument("--date", help="交易日 YYYY-MM-DD（預設取最新有資料的日期）")
    parser.add_argument("--amount", type=int, default=None, help="試算投入金額（新台幣）")
    parser.add_argument(
        "--universe",
        type=Path,
        default=None,
        help="候選池 JSON（新手預設 portfolio_universe.json；主題預設 portfolio_theme_universe.json）",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=MAX_ROUNDS_DEFAULT,
        help=f"最多 agy 輪數（預設 {MAX_ROUNDS_DEFAULT}）",
    )
    parser.add_argument("--skip-pdf", action="store_true", help="通過驗證後不產生 PDF")
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="略過基本面（僅用籌碼評分）",
    )
    parser.add_argument(
        "--refresh-fundamentals",
        action="store_true",
        help="強制重抓基本面並覆寫快取",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_import_paths()
    args = build_parser().parse_args(argv)

    trade_date = resolve_trade_date(args.date)
    if not trade_date:
        print("ERROR: 找不到可用交易日。請先執行 stock-report 抓取資料。", file=sys.stderr)
        return EXIT_NO_DATA

    if args.amount is not None and args.amount <= 0:
        print("ERROR: --amount 須為正整數", file=sys.stderr)
        return EXIT_BAD_ARGS

    if args.max_rounds < 1:
        print("ERROR: --max-rounds 至少為 1", file=sys.stderr)
        return EXIT_VALIDATION_FAILED

    if args.mode == "theme":
        themes = _parse_themes(args.themes)
        if not themes:
            print(
                "ERROR: 主題模式須指定 --themes（例如 --themes financials）",
                file=sys.stderr,
            )
            return EXIT_BAD_ARGS
        universe_path = (args.universe or DEFAULT_THEME_UNIVERSE).expanduser().resolve()
        if not universe_path.exists():
            print(f"ERROR: 找不到候選池 JSON：{universe_path}", file=sys.stderr)
            return EXIT_NO_DATA

        ensure_import_paths()
        from portfolio_signals import load_theme_catalog

        catalog = load_theme_catalog(universe_path)
        print(
            f"交易日：{trade_date}｜模式：theme｜主題：{', '.join(themes)}",
            file=sys.stderr,
        )
        return run_gate(
            "theme",
            trade_date=trade_date,
            amount=args.amount,
            universe_path=universe_path,
            max_rounds=args.max_rounds,
            skip_pdf=args.skip_pdf,
            mode="theme",
            themes=themes,
            theme_catalog=catalog,
            with_fundamentals=not args.skip_fundamentals,
            force_fundamentals_refresh=args.refresh_fundamentals,
        )

    universe_path = (args.universe or DEFAULT_UNIVERSE).expanduser().resolve()
    if not universe_path.exists():
        print(f"ERROR: 找不到候選池 JSON：{universe_path}", file=sys.stderr)
        return EXIT_NO_DATA

    profiles = list(PROFILE_CHOICES) if args.profile == "all" else [args.profile]
    print(f"交易日：{trade_date}｜模式：beginner", file=sys.stderr)

    last_code = EXIT_OK
    for profile in profiles:
        print(f"\n>>> Portfolio Gate: {profile}", file=sys.stderr)
        code = run_gate(
            profile,
            trade_date=trade_date,
            amount=args.amount,
            universe_path=universe_path,
            max_rounds=args.max_rounds,
            skip_pdf=args.skip_pdf,
            mode="beginner",
            with_fundamentals=not args.skip_fundamentals,
            force_fundamentals_refresh=args.refresh_fundamentals,
        )
        if code != EXIT_OK:
            last_code = code
    return last_code


if __name__ == "__main__":
    raise SystemExit(main())
