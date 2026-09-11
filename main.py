#!/usr/bin/env python3
"""Stock analysis CLI: TW chip reports, report gate, position gate, US tech news."""

from __future__ import annotations

import importlib
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
    "portfolio-build": (
        ROOT / ".agents" / "skills" / "portfolio-gate" / "build_portfolio.py",
        "ui,stock",
    ),
    "portfolio-gate": (
        ROOT / ".agents" / "skills" / "portfolio-gate" / "portfolio_gate.py",
        "ui,stock",
    ),
    "tech-news": (
        ROOT / ".agents" / "skills" / "us-tech-news" / "process_report.py",
        "tech",
    ),
    "market-weekly": (
        ROOT / ".agents" / "skills" / "market-weekly" / "market_weekly_gate.py",
        "ui,stock",
    ),
    "market-daily": (
        ROOT / ".agents" / "skills" / "market-daily" / "market_daily_gate.py",
        "ui,stock",
    ),
    "market-daily-chat": (
        ROOT / ".agents" / "skills" / "market-daily" / "market_day_chat.py",
        None,
    ),
    "api": (ROOT / "api" / "stock_api.py", "server,ui,stock"),
    "gate-stats": (ROOT / "tools" / "gate_stats.py", None),
    "outcome-label": (ROOT / "tools" / "outcome_label.py", None),
    "calibrate": (ROOT / "tools" / "calibrate_thresholds.py", None),
}

# Phase 0: these commands share agent.tools / skill functions in-process.
INPROC_COMMANDS: dict[str, tuple[str, str]] = {
    "stock-report": ("tw-stock-report", "fetch_chip_report"),
    "report-gate": ("report-gate", "report_gate"),
    "position-gate": ("position-gate", "position_gate"),
    "market-daily": ("market-daily", "market_daily_gate"),
    "market-daily-chat": ("market-daily", "market_day_chat"),
}


def _run_inprocess(skill_name: str, module_name: str, extra_args: list[str]) -> int:
    skill_dir = ROOT / ".agents" / "skills" / skill_name
    ui_dir = ROOT / "ui"
    stock_dir = ROOT / ".agents" / "skills" / "tw-stock-report"
    for path in (ui_dir, stock_dir, skill_dir):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    mod = importlib.import_module(module_name)
    script = skill_dir / f"{module_name}.py"
    previous_argv = sys.argv
    sys.argv = [str(script), *extra_args]
    try:
        return int(mod.main(extra_args))
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    finally:
        sys.argv = previous_argv


def _run_command(command: str, extra_args: list[str]) -> int:
    if command in INPROC_COMMANDS:
        skill_name, module_name = INPROC_COMMANDS[command]
        return _run_inprocess(skill_name, module_name, extra_args)

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
        print(f"  {'mcp':<16} agent/mcp_server.py (extra: mcp,stock,ui)")
        print(f"  {'a2a':<16} agent/a2a_server.py (extra: a2a,stock,ui)  Agent2Agent")
        print(f"  {'a2a-inspector':<16} agent/a2a_inspector.py  官方 Inspector UI → :9999")
        print(f"  {'agent':<16} agent/orchestrator.py (extra: stock,ui)")
        print(f"  {'schedule':<16} agent/schedule.py (extra: stock,ui)  05:30 共用盤前 brief")
        print("\nExample: uv run --extra stock python main.py stock-report --stocks 2330")
        print("         uv run --extra server --extra ui --extra stock python main.py api")
        print("         uv run --extra mcp --extra stock --extra ui python main.py mcp")
        print("         uv run --extra a2a --extra stock --extra ui python main.py a2a --print-card")
        print("         uv run --extra a2a python main.py a2a-inspector --howto")
        print('         uv run --extra stock --extra ui python main.py agent -- "幫我處理今天持股"')
        print("         uv run --extra stock --extra ui python main.py schedule --once")
        return 0

    command = argv[0]
    if command == "mcp":
        from agent.mcp_server import main as run_mcp

        return run_mcp(argv[1:])
    if command == "a2a":
        try:
            from agent.a2a_server import main as run_a2a
        except ImportError:
            print(
                "ERROR: A2A extra missing. Install: uv sync --extra a2a --extra stock --extra ui",
                file=sys.stderr,
            )
            return 1
        return run_a2a(argv[1:])
    if command == "a2a-inspector":
        from agent.a2a_inspector import main as run_inspector

        return run_inspector(argv[1:])
    if command == "agent":
        from agent.orchestrator import main as run_agent

        return run_agent(argv[1:])
    if command == "schedule":
        from agent.schedule import main as run_schedule

        return run_schedule(argv[1:])

    if command not in COMMANDS:
        print(f"ERROR: unknown command: {command}", file=sys.stderr)
        print("Run `python main.py` to list commands.", file=sys.stderr)
        return 1

    return _run_command(command, argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
