#!/usr/bin/env python3
"""One-shot: move flat reports/stock/{date}/tw_stock_* into {date}/{id}/.

Safe to re-run; skips when the destination already exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from stock_paths import migrate_flat_date_dir, stock_root  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="Only migrate one trade date (YYYY-MM-DD). Default: all dates.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print moves without renaming.",
    )
    args = parser.parse_args(argv)

    root = stock_root(root=ROOT)
    if not root.exists():
        print(f"No stock root at {root}", file=sys.stderr)
        return 1

    date_dirs = (
        [root / args.date]
        if args.date
        else sorted(p for p in root.iterdir() if p.is_dir() and p.name[:4].isdigit())
    )

    total = 0
    for date_dir in date_dirs:
        if not date_dir.is_dir():
            print(f"skip missing {date_dir}", file=sys.stderr)
            continue
        moves = migrate_flat_date_dir(date_dir, dry_run=args.dry_run)
        prefix = "DRY" if args.dry_run else "OK "
        for src, dest in moves:
            print(f"{prefix} {src.relative_to(root)} -> {dest.relative_to(root)}")
        total += len(moves)

    print(f"{'Would move' if args.dry_run else 'Moved'} {total} entr{'y' if total == 1 else 'ies'}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
