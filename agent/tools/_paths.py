"""Import-path helpers so tools can load existing skill modules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SKILL_DIRS = {
    "tw-stock-report": ROOT / ".agents" / "skills" / "tw-stock-report",
    "report-gate": ROOT / ".agents" / "skills" / "report-gate",
    "position-gate": ROOT / ".agents" / "skills" / "position-gate",
    "market-daily": ROOT / ".agents" / "skills" / "market-daily",
}

UI_DIR = ROOT / "ui"


def ensure_paths(*skill_names: str) -> None:
    dirs = [UI_DIR]
    names = skill_names or tuple(SKILL_DIRS)
    dirs.extend(SKILL_DIRS[name] for name in names)
    for path in dirs:
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
