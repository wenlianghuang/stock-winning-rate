"""Canonical layout for per-stock artifacts under ``reports/stock``.

Layout (write path)::

    reports/stock/{trade_date}/{stock_id}/tw_stock_{stock_id}.csv
    reports/stock/{trade_date}/{stock_id}/tw_stock_{stock_id}.facts.json
    …

Readers prefer the nested path, then fall back to the legacy flat layout::

    reports/stock/{trade_date}/tw_stock_{stock_id}.csv
"""

from __future__ import annotations

import re
from pathlib import Path

_SNAPSHOT_NAME_RE = re.compile(r"^tw_stock_(\d+)\.csv$")
_ARTIFACT_PREFIX_RE = re.compile(r"^tw_stock_(\d+)(.*)$")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def stock_root(*, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else project_root()
    return base / "reports" / "stock"


def stock_day_dir(trade_date: str, *, root: Path | None = None, mkdir: bool = False) -> Path:
    path = stock_root(root=root) / trade_date
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def stock_artifact_dir(
    trade_date: str,
    stock_id: str,
    *,
    root: Path | None = None,
    mkdir: bool = True,
) -> Path:
    """Canonical per-stock folder: ``reports/stock/{date}/{stock_id}/``."""
    path = stock_day_dir(trade_date, root=root, mkdir=mkdir) / str(stock_id).strip()
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def stock_csv_path(
    trade_date: str,
    stock_id: str,
    *,
    root: Path | None = None,
    mkdir: bool = True,
) -> Path:
    sid = str(stock_id).strip()
    return stock_artifact_dir(trade_date, sid, root=root, mkdir=mkdir) / f"tw_stock_{sid}.csv"


def stock_history_csv_path(
    trade_date: str,
    stock_id: str,
    *,
    root: Path | None = None,
    mkdir: bool = True,
) -> Path:
    sid = str(stock_id).strip()
    return (
        stock_artifact_dir(trade_date, sid, root=root, mkdir=mkdir)
        / f"tw_stock_{sid}_history.csv"
    )


def stock_chart_history_csv_path(
    trade_date: str,
    stock_id: str,
    *,
    root: Path | None = None,
    mkdir: bool = True,
) -> Path:
    sid = str(stock_id).strip()
    return (
        stock_artifact_dir(trade_date, sid, root=root, mkdir=mkdir)
        / f"tw_stock_{sid}_chart_history.csv"
    )


def stock_facts_json_path(
    trade_date: str,
    stock_id: str,
    *,
    root: Path | None = None,
    mkdir: bool = True,
) -> Path:
    sid = str(stock_id).strip()
    return (
        stock_artifact_dir(trade_date, sid, root=root, mkdir=mkdir)
        / f"tw_stock_{sid}.facts.json"
    )


def find_stock_artifact(
    stock_id: str,
    name: str,
    *,
    trade_date: str | None = None,
    root: Path | None = None,
) -> Path | None:
    """Locate ``name`` for a stock; nested first, then legacy flat."""
    sid = str(stock_id).strip()
    base = stock_root(root=root)
    if not base.exists():
        return None

    if trade_date:
        for candidate in (
            base / trade_date / sid / name,
            base / trade_date / name,
        ):
            if candidate.is_file():
                return candidate
        return None

    matches = list(base.glob(f"*/{sid}/{name}"))
    matches.extend(base.glob(f"*/{name}"))
    if not matches:
        return None
    matches.sort(key=lambda p: p.relative_to(base).parts[0])
    return matches[-1]


def find_stock_csv(
    stock_id: str,
    trade_date: str | None = None,
    *,
    root: Path | None = None,
) -> Path | None:
    sid = str(stock_id).strip()
    return find_stock_artifact(
        sid, f"tw_stock_{sid}.csv", trade_date=trade_date, root=root
    )


def iter_date_snapshot_csvs(date_dir: Path) -> list[Path]:
    """Snapshot CSVs for one trade date (nested preferred; skip history/chart)."""
    if not date_dir.is_dir():
        return []
    found: dict[str, Path] = {}
    for sub in sorted(date_dir.iterdir()):
        if not sub.is_dir():
            continue
        # Skip gate.rounds dirs left over from flat layout
        if sub.name.startswith("tw_stock_"):
            continue
        csv_path = sub / f"tw_stock_{sub.name}.csv"
        if csv_path.is_file() and _SNAPSHOT_NAME_RE.match(csv_path.name):
            found[sub.name] = csv_path
    for csv_path in sorted(date_dir.glob("tw_stock_*.csv")):
        match = _SNAPSHOT_NAME_RE.match(csv_path.name)
        if not match:
            continue
        stock_id = match.group(1)
        found.setdefault(stock_id, csv_path)
    return [found[k] for k in sorted(found)]


def iter_date_named_files(date_dir: Path, pattern: str) -> list[Path]:
    """Glob ``pattern`` under nested stock dirs and the flat date dir."""
    if not date_dir.is_dir():
        return []
    paths = list(date_dir.glob(f"*/{pattern}"))
    paths.extend(date_dir.glob(pattern))
    # Dedupe by resolved path, prefer nested (longer path) when both exist
    by_name: dict[str, Path] = {}
    for path in sorted(paths, key=lambda p: len(p.parts)):
        by_name[path.name] = path
    return [by_name[k] for k in sorted(by_name)]


def iter_stock_artifacts(
    *,
    name_glob: str,
    trade_date: str | None = None,
    root: Path | None = None,
) -> list[Path]:
    """Collect files matching ``name_glob`` across all (or one) trade dates."""
    base = stock_root(root=root)
    if not base.exists():
        return []
    if trade_date:
        return iter_date_named_files(base / trade_date, name_glob)
    paths: list[Path] = []
    for date_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        paths.extend(iter_date_named_files(date_dir, name_glob))
    return paths


def migrate_flat_date_dir(
    date_dir: Path,
    *,
    dry_run: bool = False,
) -> list[tuple[Path, Path]]:
    """Move flat ``tw_stock_{id}*`` entries into ``{date}/{id}/``. Returns moves."""
    if not date_dir.is_dir():
        return []
    moves: list[tuple[Path, Path]] = []
    for entry in list(date_dir.iterdir()):
        match = _ARTIFACT_PREFIX_RE.match(entry.name)
        if not match:
            continue
        stock_id = match.group(1)
        dest_dir = date_dir / stock_id
        dest = dest_dir / entry.name
        if entry.resolve() == dest.resolve():
            continue
        if dest.exists():
            continue
        moves.append((entry, dest))
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            entry.rename(dest)
    return moves
