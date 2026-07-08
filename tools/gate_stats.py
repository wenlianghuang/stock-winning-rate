#!/usr/bin/env python3
"""Aggregate gate.log files into loop-quality metrics.

Scans ``reports/stock/**/*.gate.log`` (report-gate) and ``*.position.gate.log``
(position-gate), segments each file into runs (a run starts at round 1), and
reports round-1 pass rate, eventual pass rate, average rounds, most common
issue codes, and per-layer (format / facts) failure counts.

Pure stdlib; no extra dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def stock_root() -> Path:
    return project_root() / "reports" / "stock"


def iter_log_files(date: str | None) -> tuple[list[Path], list[Path]]:
    """Return (report_logs, position_logs)."""
    root = stock_root()
    if not root.exists():
        return [], []
    pattern = f"{date}/*.gate.log" if date else "*/*.gate.log"
    all_logs = sorted(root.glob(pattern))
    position_logs = [p for p in all_logs if p.name.endswith(".position.gate.log")]
    report_logs = [p for p in all_logs if not p.name.endswith(".position.gate.log")]
    return report_logs, position_logs


def read_entries(log_path: Path) -> list[dict]:
    entries: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def segment_runs(entries: list[dict]) -> list[list[dict]]:
    """Split appended log entries into runs; a run begins at round == 1."""
    runs: list[list[dict]] = []
    current: list[dict] = []
    for entry in entries:
        if entry.get("round") == 1 and current:
            runs.append(current)
            current = []
        current.append(entry)
    if current:
        runs.append(current)
    return runs


@dataclass
class Stats:
    runs: int = 0
    round1_pass: int = 0
    final_pass: int = 0
    total_rounds: int = 0
    total_duration: float = 0.0
    duration_samples: int = 0
    issue_codes: Counter = field(default_factory=Counter)
    layered_rounds: int = 0
    format_fail: int = 0
    facts_fail: int = 0
    reasoning_fail: int = 0
    position_fail: int = 0
    legacy_rounds: int = 0  # entries without issue_codes/layers

    def add_run(self, run: list[dict]) -> None:
        if not run:
            return
        self.runs += 1
        self.total_rounds += len(run)
        if bool(run[0].get("passed")):
            self.round1_pass += 1
        if bool(run[-1].get("passed")):
            self.final_pass += 1
        for entry in run:
            duration = entry.get("duration_sec")
            if isinstance(duration, (int, float)):
                self.total_duration += float(duration)
                self.duration_samples += 1
            codes = entry.get("issue_codes")
            layers = entry.get("layers")
            if codes is None and layers is None:
                self.legacy_rounds += 1
            if isinstance(codes, list):
                self.issue_codes.update(codes)
            if isinstance(layers, dict):
                self.layered_rounds += 1
                if layers.get("format") == "fail":
                    self.format_fail += 1
                if layers.get("facts") == "fail":
                    self.facts_fail += 1
                if layers.get("reasoning") == "fail":
                    self.reasoning_fail += 1
                if layers.get("position") == "fail":
                    self.position_fail += 1

    @property
    def round1_pass_rate(self) -> float:
        return self.round1_pass / self.runs if self.runs else 0.0

    @property
    def final_pass_rate(self) -> float:
        return self.final_pass / self.runs if self.runs else 0.0

    @property
    def avg_rounds(self) -> float:
        return self.total_rounds / self.runs if self.runs else 0.0

    @property
    def avg_duration(self) -> float:
        return (
            self.total_duration / self.duration_samples
            if self.duration_samples
            else 0.0
        )

    def to_dict(self, *, top_n: int) -> dict:
        return {
            "runs": self.runs,
            "round1_pass_rate": round(self.round1_pass_rate, 3),
            "final_pass_rate": round(self.final_pass_rate, 3),
            "avg_rounds": round(self.avg_rounds, 2),
            "avg_round_duration_sec": round(self.avg_duration, 1),
            "layer_failures": {
                "format": self.format_fail,
                "facts": self.facts_fail,
                "reasoning": self.reasoning_fail,
                "position": self.position_fail,
                "layered_rounds": self.layered_rounds,
            },
            "top_issue_codes": self.issue_codes.most_common(top_n),
            "legacy_rounds_without_codes": self.legacy_rounds,
        }


def collect_stats(log_files: list[Path], stock_filter: str | None) -> Stats:
    stats = Stats()
    for log_path in log_files:
        if stock_filter and f"tw_stock_{stock_filter}" not in log_path.name:
            continue
        for run in segment_runs(read_entries(log_path)):
            stats.add_run(run)
    return stats


def _format_section(title: str, stats: Stats, *, top_n: int) -> str:
    if stats.runs == 0:
        return f"## {title}\n（無資料）\n"
    lines = [
        f"## {title}",
        f"- 執行次數（runs）：{stats.runs}",
        f"- 第一輪通過率：{stats.round1_pass_rate:.0%}"
        f"（{stats.round1_pass}/{stats.runs}）",
        f"- 最終通過率：{stats.final_pass_rate:.0%}"
        f"（{stats.final_pass}/{stats.runs}）",
        f"- 平均輪數：{stats.avg_rounds:.2f}",
        f"- 平均每輪耗時：{stats.avg_duration:.1f}s",
    ]
    if stats.layered_rounds:
        parts = [
            f"format {stats.format_fail}",
            f"facts {stats.facts_fail}",
        ]
        if stats.reasoning_fail:
            parts.append(f"reasoning {stats.reasoning_fail}")
        if stats.position_fail:
            parts.append(f"position {stats.position_fail}")
        lines.append(
            f"- 分層失敗（近期含 layers 的 {stats.layered_rounds} 輪）："
            + "、".join(parts)
        )
    if stats.issue_codes:
        lines.append("- 最常見 issue code：")
        for code, count in stats.issue_codes.most_common(top_n):
            lines.append(f"    - {code}: {count}")
    if stats.legacy_rounds:
        lines.append(
            f"- （備註：{stats.legacy_rounds} 輪為舊格式，無 issue_codes/layers）"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="彙整 gate.log 的 loop 品質指標（round-1 通過率、issue code 分布等）",
    )
    parser.add_argument("--date", help="只統計某交易日目錄，例如 2026-07-06")
    parser.add_argument("--stock", help="只統計某股票代碼，例如 2409")
    parser.add_argument(
        "--top", type=int, default=10, help="顯示前 N 個 issue code（預設 10）"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    report_logs, position_logs = iter_log_files(args.date)
    report_stats = collect_stats(report_logs, args.stock)
    position_stats = collect_stats(position_logs, args.stock)

    if args.json:
        payload = {
            "scope": {"date": args.date, "stock": args.stock},
            "report_gate": report_stats.to_dict(top_n=args.top),
            "position_gate": position_stats.to_dict(top_n=args.top),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    scope = []
    if args.date:
        scope.append(f"date={args.date}")
    if args.stock:
        scope.append(f"stock={args.stock}")
    scope_note = f"（範圍：{', '.join(scope)}）" if scope else "（全部）"

    print(f"# Gate Stats {scope_note}\n")
    print(_format_section("report-gate", report_stats, top_n=args.top))
    print(_format_section("position-gate", position_stats, top_n=args.top))

    if report_stats.runs == 0 and position_stats.runs == 0:
        print("找不到任何 gate.log，請先執行 report-gate / position-gate。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
