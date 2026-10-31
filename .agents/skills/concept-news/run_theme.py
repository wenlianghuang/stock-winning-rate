#!/usr/bin/env python3
"""Run one concept-news theme from the query pack through the summary.

An existing event table is reused unless --force is set, so a status check
does not search. Collection runs only when the event file is missing or the
run is forced. The summary is still written only after validation passes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from answer_events import reports_root
from cluster_events import cluster_kept, write_events
from collect_news import (
    TAIPEI,
    SearxngNewsClient,
    _validate_day,
    collect_theme_news,
    live_yahoo_fetch,
    resolve_searxng_url,
    write_theme_news_raw,
)
from filter_news import filter_raw, write_kept_news
from process_report import (
    artifact_paths,
    execute_report,
    load_event_table,
    resolve_agy_bin,
    run_agy,
)
from theme_pack import build_theme_pack, write_theme_pack

THEME_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
# 題材新聞 18:00 起才能把當天當報告日。籌碼仍是 twse_calendar 的 21:30。
NEWS_READY_HOUR = 18
NEWS_READY_MINUTE = 0


def news_report_date(now: datetime | None = None) -> str:
    """Latest concept-news day that may be generated at ``now`` (Taipei)."""
    moment = now or datetime.now(TAIPEI)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TAIPEI)
    else:
        moment = moment.astimezone(TAIPEI)
    day = moment.date()
    minutes = moment.hour * 60 + moment.minute
    if minutes < NEWS_READY_HOUR * 60 + NEWS_READY_MINUTE:
        day -= timedelta(days=1)
    return day.isoformat()


def ensure_news_date_ready(day: str, now: datetime | None = None) -> str:
    """Reject a report date that is still before the 18:00 news cutover."""
    chosen = day.strip()
    _validate_day(chosen)
    latest = news_report_date(now)
    if chosen > latest:
        raise ValueError(
            f"{chosen} 還沒到可以產生題材新聞的時間。台北時間 18:00 以後才能產生當天的新聞報告。"
        )
    return chosen


def run_theme(
    theme_id: str,
    *,
    as_of: str | None = None,
    force: bool = False,
    skip_summary: bool = False,
    skip_searxng: bool = False,
    max_rounds: int = 2,
    min_citations: int = 1,
    output_root: Path | None = None,
    yahoo_fetch=None,
    searx_fetch=None,
    complete=None,
    now: datetime | None = None,
) -> dict:
    """Collect only when needed, then optionally write the validated summary."""
    key = theme_id.strip()
    if not THEME_ID.fullmatch(key):
        raise ValueError("題材 id 格式錯誤")
    if max_rounds < 1:
        raise ValueError("max_rounds 至少為 1")
    if min_citations < 1:
        raise ValueError("min_citations 至少為 1")

    pack = build_theme_pack(key)
    if as_of:
        day = ensure_news_date_ready(as_of, now)
    else:
        day = news_report_date(now)
    root = output_root if output_root is not None else reports_root()
    folder = root / pack.theme_id
    events_path = folder / f"{day}_events.json"
    summary_path = folder / f"{day}_summary.md"

    if not force and events_path.is_file():
        table = load_event_table(events_path)
        summary = summary_path.read_text(encoding="utf-8") if summary_path.is_file() else None
        if skip_summary or summary is not None:
            return _result(
                pack.theme_id,
                pack.label,
                day,
                events_path,
                summary_path if summary is not None else None,
                table,
                summary,
                searched=False,
                reused=True,
            )
        written = _write_summary(
            table,
            events_path,
            max_rounds=max_rounds,
            min_citations=min_citations,
            complete=complete,
        )
        return _result(
            pack.theme_id,
            pack.label,
            day,
            events_path,
            summary_path,
            table,
            written,
            searched=False,
            reused=False,
        )

    write_theme_pack(pack, folder / "theme_pack.json")
    fetch_yahoo = yahoo_fetch if yahoo_fetch is not None else live_yahoo_fetch
    fetch_searx, skip_reason = _searx_fetch(searx_fetch, skip_searxng=skip_searxng)
    raw = collect_theme_news(
        pack,
        yahoo_fetch=fetch_yahoo,
        searx_fetch=fetch_searx,
        now=now,
        as_of=day,
        searx_skip_reason=skip_reason,
    )
    write_theme_news_raw(raw, folder / f"{day}_raw.json")
    kept = filter_raw(raw.to_dict(), pack)
    write_kept_news(kept, folder / f"{day}_kept.json")
    table_obj = cluster_kept(kept.to_dict())
    write_events(table_obj, events_path)
    table = table_obj.to_dict()
    if summary_path.is_file():
        summary_path.unlink()

    if skip_summary:
        return _result(
            pack.theme_id,
            pack.label,
            day,
            events_path,
            None,
            table,
            None,
            searched=True,
            reused=False,
        )

    written = _write_summary(
        table,
        events_path,
        max_rounds=max_rounds,
        min_citations=min_citations,
        complete=complete,
    )
    return _result(
        pack.theme_id,
        pack.label,
        day,
        events_path,
        summary_path,
        table,
        written,
        searched=True,
        reused=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑一個概念題材：沿用事件表，或蒐集後寫報告")
    parser.add_argument("--theme", required=True)
    parser.add_argument("--date", default="")
    parser.add_argument("--output", type=Path, default=None, help="報告根目錄，預設 reports/concept-news")
    parser.add_argument("--force", action="store_true", help="已有事件表也重新蒐集並重寫報告")
    parser.add_argument("--skip-summary", action="store_true", help="寫到事件表就停，不呼叫 agy")
    parser.add_argument("--skip-searxng", action="store_true", help="只交 Yahoo")
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--min-citations", type=int, default=1)
    parser.add_argument("--validate-only", action="store_true", help="只驗證既有 summary，不蒐集")
    args = parser.parse_args(argv)

    if args.validate_only:
        from process_report import main as report_main

        forwarded = ["--validate-only", "--theme", args.theme]
        if args.date.strip():
            forwarded.extend(["--date", args.date.strip()])
        if args.output is not None:
            forwarded.extend(["--output", str(args.output)])
        forwarded.extend(
            [
                "--max-rounds",
                str(args.max_rounds),
                "--min-citations",
                str(args.min_citations),
            ]
        )
        return report_main(forwarded)

    try:
        result = run_theme(
            args.theme,
            as_of=args.date.strip() or None,
            force=args.force,
            skip_summary=args.skip_summary,
            skip_searxng=args.skip_searxng,
            max_rounds=args.max_rounds,
            min_citations=args.min_citations,
            output_root=args.output,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if result.get("reused"):
        print("沿用已寫好的檔案，沒有重新蒐集。", file=sys.stderr)
    print(result.get("summary_path") or result.get("events_path"))
    return 0


def _searx_fetch(explicit, *, skip_searxng: bool):
    if explicit is not None:
        return explicit, None
    if skip_searxng:
        return None, "已略過 SearXNG"
    url = resolve_searxng_url(None, skip=False)
    if not url:
        return None, None
    return SearxngNewsClient(url).fetch_articles, None


def _write_summary(
    table: dict,
    events_path: Path,
    *,
    max_rounds: int,
    min_citations: int,
    complete,
) -> str:
    paths = artifact_paths(events_path, str(table.get("as_of") or events_path.name[:10]))
    completer = complete
    if completer is None:
        resolve_agy_bin()
        completer = run_agy
    code = execute_report(
        table,
        events_path,
        summary_path=paths["summary"],
        gate_log_path=paths["gate_log"],
        gate_rounds_dir=paths["rounds"],
        max_rounds=max_rounds,
        min_citations=min_citations,
        complete=completer,
    )
    if code != 0 or not paths["summary"].is_file():
        raise RuntimeError(f"事件表已寫入 {events_path}，報告未通過驗證。")
    return paths["summary"].read_text(encoding="utf-8")


def _result(
    theme_id: str,
    label: str,
    day: str,
    events_path: Path,
    summary_path: Path | None,
    table: dict,
    summary: str | None,
    *,
    searched: bool,
    reused: bool,
) -> dict:
    return {
        "theme_id": theme_id,
        "label": label,
        "as_of": day,
        "events_path": str(events_path),
        "summary_path": str(summary_path) if summary_path is not None else None,
        "events": table,
        "summary": summary,
        "searched": searched,
        "reused": reused,
        "exit_code": 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
