#!/usr/bin/env python3
"""Build portfolio recommendations (pure rules, no LLM).

Phase 1 of portfolio-gate:
- beginner mode: ETF/blue-chip universe × risk profile
- theme mode (v1): theme universe × user themes, ranked by chip health

Outputs Markdown + facts JSON (consumed later by the agy narrative loop).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_data import (
    DEFAULT_THEME_UNIVERSE,
    DEFAULT_UNIVERSE,
    build_candidates,
    ensure_import_paths,
    output_dir,
    resolve_trade_date,
)

EXIT_OK = 0
EXIT_NO_DATA = 20
EXIT_BAD_ARGS = 2

PROFILE_CHOICES = ("conservative", "balanced", "aggressive")


def _parse_themes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip()]


def run_beginner(
    *,
    profiles: list[str],
    trade_date: str,
    amount: int | None,
    universe_path: Path,
    write_files: bool,
    with_fundamentals: bool = True,
    force_fundamentals_refresh: bool = False,
) -> int:
    ensure_import_paths()
    from chip_signals import load_base_rates
    from portfolio_signals import (
        build_portfolio_facts,
        build_portfolio_markdown,
        write_portfolio_facts_json,
    )

    candidates = build_candidates(
        trade_date,
        universe_path,
        with_fundamentals=with_fundamentals,
        force_fundamentals_refresh=force_fundamentals_refresh,
    )
    available = sum(1 for c in candidates if c.data_available)
    if available == 0:
        print(
            f"ERROR: {trade_date} 找不到任何候選標的的 CSV。"
            f"請先執行 stock-report 抓取資料（reports/stock/{trade_date}/）。",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    base_rates = load_base_rates()
    print(
        f"[beginner] 候選池 {len(candidates)} 檔，{trade_date} 有資料 {available} 檔"
        f"{'（含 base_rates 勝率）' if base_rates else '（無 base_rates，略過勝率加權）'}",
        file=sys.stderr,
    )

    out_dir = output_dir(trade_date)
    for profile in profiles:
        facts = build_portfolio_facts(
            candidates,
            profile=profile,
            base_rates=base_rates,
            amount_twd=amount,
            trade_date=trade_date,
        )
        markdown = build_portfolio_markdown(facts)
        print("\n" + "=" * 72)
        print(markdown)

        if write_files:
            out_dir.mkdir(parents=True, exist_ok=True)
            md_path = out_dir / f"portfolio_{profile}.md"
            json_path = out_dir / f"portfolio_{profile}.facts.json"
            md_path.write_text(markdown, encoding="utf-8")
            write_portfolio_facts_json(json_path, facts)
            print(f"Markdown: {md_path.resolve()}", file=sys.stderr)
            print(f"Facts JSON: {json_path.resolve()}", file=sys.stderr)

    return EXIT_OK


def run_theme(
    *,
    themes: list[str],
    trade_date: str,
    amount: int | None,
    universe_path: Path,
    write_files: bool,
    with_fundamentals: bool = True,
    force_fundamentals_refresh: bool = False,
) -> int:
    ensure_import_paths()
    from chip_signals import load_base_rates
    from portfolio_signals import (
        build_portfolio_markdown,
        build_theme_portfolio_facts,
        load_theme_catalog,
        theme_slug,
        write_portfolio_facts_json,
    )

    catalog = load_theme_catalog(universe_path)
    unknown = [t for t in themes if t not in catalog]
    if unknown:
        print(
            f"WARNING: 未知主題 {unknown}；已知：{', '.join(sorted(catalog))}",
            file=sys.stderr,
        )

    candidates = build_candidates(
        trade_date,
        universe_path,
        with_fundamentals=with_fundamentals,
        force_fundamentals_refresh=force_fundamentals_refresh,
    )
    available = sum(1 for c in candidates if c.data_available)
    if available == 0:
        print(
            f"ERROR: {trade_date} 找不到任何候選標的的 CSV。"
            f"請先執行 stock-report 抓取主題池個股（reports/stock/{trade_date}/）。",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    base_rates = load_base_rates()
    facts = build_theme_portfolio_facts(
        candidates,
        themes=themes,
        base_rates=base_rates,
        amount_twd=amount,
        trade_date=trade_date,
        theme_catalog=catalog,
    )
    print(
        f"[theme] {facts.profile_label}｜候選 {len(candidates)} 檔、"
        f"有資料 {available} 檔、選出 {facts.num_holdings} 檔",
        file=sys.stderr,
    )
    if facts.num_holdings < 2:
        print(
            f"ERROR: 主題可用標的不足（僅 {facts.num_holdings} 檔），無法組成組合。",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    markdown = build_portfolio_markdown(facts)
    print("\n" + "=" * 72)
    print(markdown)

    if write_files:
        out_dir = output_dir(trade_date)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = theme_slug(themes)
        md_path = out_dir / f"portfolio_{slug}.md"
        json_path = out_dir / f"portfolio_{slug}.facts.json"
        md_path.write_text(markdown, encoding="utf-8")
        write_portfolio_facts_json(json_path, facts)
        print(f"Markdown: {md_path.resolve()}", file=sys.stderr)
        print(f"Facts JSON: {json_path.resolve()}", file=sys.stderr)

    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Portfolio Build：新手風險組合或主題袖口組合（純規則，Phase 1）",
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
    parser.add_argument(
        "--amount",
        type=int,
        default=None,
        help="試算投入金額（新台幣），用來換算各檔投入金額與估計股數",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=None,
        help="候選池 JSON（新手預設 portfolio_universe.json；主題預設 portfolio_theme_universe.json）",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="只印出報告，不寫入 reports/portfolio/",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="略過基本面（僅用籌碼評分；適合 FinMind 配額緊張時）",
    )
    parser.add_argument(
        "--refresh-fundamentals",
        action="store_true",
        help="強制重抓基本面並覆寫 reports/fundamentals/{date}.json 快取",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_import_paths()
    args = build_parser().parse_args(argv)

    trade_date = resolve_trade_date(args.date)
    if not trade_date:
        print(
            "ERROR: 找不到可用交易日。請先執行 stock-report 抓取資料。",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    if args.amount is not None and args.amount <= 0:
        print("ERROR: --amount 須為正整數", file=sys.stderr)
        return EXIT_BAD_ARGS

    if args.mode == "theme":
        themes = _parse_themes(args.themes)
        if not themes:
            print(
                "ERROR: 主題模式須指定 --themes（例如 --themes financials "
                "或 --themes financials,thermal）",
                file=sys.stderr,
            )
            return EXIT_BAD_ARGS
        universe_path = (args.universe or DEFAULT_THEME_UNIVERSE).expanduser().resolve()
        if not universe_path.exists():
            print(f"ERROR: 找不到候選池 JSON：{universe_path}", file=sys.stderr)
            return EXIT_BAD_ARGS
        print(f"交易日：{trade_date}｜模式：theme｜主題：{', '.join(themes)}", file=sys.stderr)
        return run_theme(
            themes=themes,
            trade_date=trade_date,
            amount=args.amount,
            universe_path=universe_path,
            write_files=not args.no_write,
            with_fundamentals=not args.skip_fundamentals,
            force_fundamentals_refresh=args.refresh_fundamentals,
        )

    universe_path = (args.universe or DEFAULT_UNIVERSE).expanduser().resolve()
    if not universe_path.exists():
        print(f"ERROR: 找不到候選池 JSON：{universe_path}", file=sys.stderr)
        return EXIT_BAD_ARGS

    profiles = list(PROFILE_CHOICES) if args.profile == "all" else [args.profile]
    print(f"交易日：{trade_date}｜模式：beginner", file=sys.stderr)
    return run_beginner(
        profiles=profiles,
        trade_date=trade_date,
        amount=args.amount,
        universe_path=universe_path,
        write_files=not args.no_write,
        with_fundamentals=not args.skip_fundamentals,
        force_fundamentals_refresh=args.refresh_fundamentals,
    )


if __name__ == "__main__":
    raise SystemExit(main())
