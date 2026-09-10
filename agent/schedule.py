"""Phase 4 pre-market schedule: one shared brief after Taipei 05:30 US settle.

Does not fan-out per-user holdings. Idempotent: skip if the canonical brief
already exists and US indices are present (unless --force).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from agent.tools._paths import ROOT

MARKET_ROOT = ROOT / "reports" / "market"
FACTS_NAME = "tw_market_daily.facts.json"
SUMMARY_NAME = "tw_market_daily.summary.json"
MD_NAME = "tw_market_daily.md"


@dataclass
class CanonicalBrief:
    trade_date: str
    for_session: str | None
    ready: bool
    us_available: bool
    facts: dict[str, Any] | None
    summary: dict[str, Any] | None
    markdown: str | None
    facts_path: str | None
    md_path: str | None


@dataclass
class ScheduleResult:
    action: str
    reason: str
    trade_date: str | None = None
    for_session: str | None = None
    us_cutover_passed: bool | None = None
    exit_code: int = 0
    md_path: str | None = None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def us_available_in_facts(facts: dict[str, Any] | None) -> bool:
    if not facts:
        return False
    us = facts.get("us")
    if not isinstance(us, dict):
        return False
    if not us.get("available"):
        return False
    indices = us.get("indices") if isinstance(us.get("indices"), dict) else {}
    for key in ("IXIC", "SOX"):
        item = indices.get(key)
        if isinstance(item, dict) and item.get("day_return_pct") is not None:
            return True
    return False


def load_canonical_brief(trade_date: str, *, root: Path | None = None) -> CanonicalBrief:
    day_dir = (root or MARKET_ROOT) / trade_date
    facts_file = day_dir / FACTS_NAME
    summary_file = day_dir / SUMMARY_NAME
    md_file = day_dir / MD_NAME
    facts = _read_json(facts_file)
    summary = _read_json(summary_file)
    markdown = md_file.read_text(encoding="utf-8") if md_file.exists() else None
    us_ok = us_available_in_facts(facts)
    ready = bool(markdown) and facts is not None and summary is not None and us_ok
    for_session = None
    if facts and facts.get("for_session"):
        for_session = str(facts["for_session"])
    elif summary and summary.get("for_session"):
        for_session = str(summary["for_session"])
    return CanonicalBrief(
        trade_date=trade_date,
        for_session=for_session,
        ready=ready,
        us_available=us_ok,
        facts=facts,
        summary=summary,
        markdown=markdown,
        facts_path=str(facts_file) if facts_file.exists() else None,
        md_path=str(md_file) if md_file.exists() else None,
    )


def decide_premarket_action(
    *,
    us_cutover_passed: bool,
    brief: CanonicalBrief | None,
    force: bool = False,
) -> tuple[str, str]:
    if not force and not us_cutover_passed:
        return "skip_early", "台北 05:30 前，美股 overnight 尚未 settle"
    if not force and brief is not None and brief.ready:
        return "skip_exists", f"{brief.trade_date} 已有通過且含美股的共用 brief"
    if not force and brief is not None and brief.markdown and not brief.us_available:
        return "run", "既有 brief 缺少美股，重跑（不略過那指／費半）"
    return "run", "產出全站共用開盤前 brief"


def run_premarket_once(
    *,
    as_of: str | None = None,
    trade_date: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    skip_agy: bool = False,
    skip_fetch: bool = False,
    us_attempts: int = 3,
    us_backoff_sec: float = 15.0,
    run_daily: Callable[..., Any] | None = None,
) -> ScheduleResult:
    from agent.tools._paths import ensure_paths
    from agent.tools.market import MarketDailyInput, run_market_daily

    ensure_paths("market-daily", "tw-stock-report")
    from market_day_signals import resolve_window_or_fail

    try:
        window = resolve_window_or_fail(as_of=as_of, trade_date=trade_date)
    except Exception as exc:  # noqa: BLE001
        return ScheduleResult(
            action="error",
            reason=str(exc),
            exit_code=2,
        )

    brief = load_canonical_brief(window.trade_date)
    action, reason = decide_premarket_action(
        us_cutover_passed=window.us_cutover_passed,
        brief=brief,
        force=force,
    )
    base = ScheduleResult(
        action=action,
        reason=reason,
        trade_date=window.trade_date,
        for_session=window.for_session,
        us_cutover_passed=window.us_cutover_passed,
        md_path=brief.md_path,
    )
    if action != "run":
        return base
    if dry_run:
        base.reason = f"dry-run：{reason}"
        return base

    runner = run_daily or run_market_daily
    result = runner(
        MarketDailyInput(
            as_of=as_of,
            trade_date=window.trade_date if trade_date else None,
            skip_agy=skip_agy,
            skip_fetch=skip_fetch,
            skip_us=False,
            require_us=True,
            us_attempts=us_attempts,
            us_backoff_sec=us_backoff_sec,
        )
    )
    refreshed = load_canonical_brief(window.trade_date)
    base.md_path = refreshed.md_path or result.md_path
    if not result.ok:
        base.action = "error"
        base.reason = result.error or "run_market_daily failed"
        base.exit_code = int(result.exit_code or 1)
        return base
    if not refreshed.us_available:
        base.action = "error"
        base.reason = "產報結束但美股仍不可用（拒絕略過）"
        base.exit_code = 20
        return base
    base.action = "ran"
    base.reason = "已產出全站共用 brief"
    return base


def format_schedule(result: ScheduleResult) -> str:
    lines = [
        f"action: {result.action}",
        f"reason: {result.reason}",
    ]
    if result.trade_date:
        lines.append(f"trade_date: {result.trade_date} → {result.for_session or '—'}")
    if result.us_cutover_passed is not None:
        lines.append(f"us_cutover_passed: {result.us_cutover_passed}")
    if result.md_path:
        lines.append(f"md: {result.md_path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="台北 05:30 後產一份全站共用開盤前 brief（類 RPA）",
    )
    parser.add_argument("--once", action="store_true", help="跑一輪後結束（cron 用；預設）")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="持續檢查；已過 05:30 且尚無 brief 就產",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="--loop 間隔秒數（預設 300）",
    )
    parser.add_argument("--as-of", default=None, help="覆寫當下時間（測試 cutover）")
    parser.add_argument("--date", default=None, help="強制 trade_date")
    parser.add_argument("--force", action="store_true", help="略過幂等，重跑")
    parser.add_argument("--dry-run", action="store_true", help="只決定 action，不產報")
    parser.add_argument("--skip-agy", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--us-attempts", type=int, default=3)
    parser.add_argument("--us-backoff", type=float, default=15.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    def _once() -> ScheduleResult:
        return run_premarket_once(
            as_of=args.as_of,
            trade_date=args.date,
            force=args.force,
            dry_run=args.dry_run,
            skip_agy=args.skip_agy,
            skip_fetch=args.skip_fetch,
            us_attempts=max(1, args.us_attempts),
            us_backoff_sec=max(0.0, args.us_backoff),
        )

    if args.loop:
        while True:
            result = _once()
            if args.json:
                print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
            else:
                print(format_schedule(result), flush=True)
                print("---", flush=True)
            if result.action == "error" and result.exit_code not in (0,):
                # keep looping after a failed fetch; operator can --once in cron instead
                pass
            time.sleep(max(30, args.interval))
    result = _once()
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False))
    else:
        print(format_schedule(result))
    return int(result.exit_code)
