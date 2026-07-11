#!/usr/bin/env python3
"""Stock analysis CLI: TW chip reports, report gate, position gate, US tech news."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

COMMANDS: dict[str, tuple[Path, str | None]] = {
    "stock-report": (
        ROOT / ".agents" / "skills" / "tw-stock-report" / "fetch_chip_report.py",
        "stock",
    ),
    "report-gate": (
        ROOT / ".agents" / "skills" / "report-gate" / "report_gate.py",
        "ui,stock",
    ),
    "position-gate": (
        ROOT / ".agents" / "skills" / "position-gate" / "position_gate.py",
        "ui,stock",
    ),
    "tech-news": (
        ROOT / ".agents" / "skills" / "us-tech-news" / "process_report.py",
        "tech",
    ),
    "api": (ROOT / "api" / "stock_api.py", "server,ui,stock"),
    "gate-stats": (ROOT / "tools" / "gate_stats.py", None),
    "outcome-label": (ROOT / "tools" / "outcome_label.py", None),
    "calibrate": (ROOT / "tools" / "calibrate_thresholds.py", None),
}


def _run_command(command: str, extra_args: list[str]) -> int:
    script, _extra = COMMANDS[command]
    if not script.exists():
        print(f"ERROR: missing script: {script}", file=sys.stderr)
        return 1

    result = subprocess.run([sys.executable, str(script), *extra_args], cwd=ROOT)
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "--list"):
        print("stock-winning-rate")
        print("Available commands:")
        for name, (script, extra) in COMMANDS.items():
            extra_hint = f" (extra: {extra})" if extra else ""
            print(f"  {name:<16} {script.relative_to(ROOT)}{extra_hint}")
        print("\nExample: uv run --extra stock python main.py stock-report --stocks 2330")
        print("         uv run --extra server --extra ui --extra stock python main.py api")
        return 0

    command = argv[0]
    if command not in COMMANDS:
        print(f"ERROR: unknown command: {command}", file=sys.stderr)
        print("Run `python main.py` to list commands.", file=sys.stderr)
        return 1

    return _run_command(command, argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
