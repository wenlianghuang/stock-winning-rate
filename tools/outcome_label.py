#!/usr/bin/env python3
"""Outcome labeling job: join historical chip snapshots with realized forward prices.

For every daily snapshot ``reports/stock/{date}/tw_stock_{id}.csv`` this job:

1. Recomputes the deterministic ``ChipFacts`` from the snapshot + history CSV
   (using the *current* ``chip_signals`` so every date shares one label schema --
   old ``.facts.json`` files are ignored on purpose, they predate newer fields).
2. Resolves the close price at +1/+3/+5/+10 **trading days** from on-disk data
   (each stock's most recent ``_chart_history.csv`` is 60 consecutive trading
   days, so recent horizons need no network at all).
3. Computes forward return and **excess return vs TAIEX** (大盤收盤), the primary
   metric -- consistent with the report's relative-strength philosophy.
4. Appends/updates a row keyed by ``(stock_id, trade_date)`` in
   ``reports/outcomes/outcomes.jsonl``. Horizons that have not matured yet are
   marked ``pending`` and filled on a later run (this is a backlog-sweep job).

Pure stdlib. Safe to re-run daily; idempotent per ``(stock, date)``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

# --- Configuration ----------------------------------------------------------

HORIZONS = (1, 3, 5, 10)  # trading days
EXCESS_LABEL_KEYS = (
    "chip_regime",
    "ma_stack",
    "trend_strength",
    "price_trend",
    "rs_period",
    "institutional_consensus",
    "volatility_regime",
    "rsi_zone",
)
NUMERIC_KEYS = (
    "rsi_14",
    "atr_pct",
    "adx_14",
    "volume_ma_ratio",
    "margin_short_ratio_pct",
)

_SNAPSHOT_RE = re.compile(r"^tw_stock_(\d+)\.csv$")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def stock_root() -> Path:
    return project_root() / "reports" / "stock"


def outcomes_path() -> Path:
    return project_root() / "reports" / "outcomes" / "outcomes.jsonl"


def _load_chip_signals():
    ui_path = str(project_root() / "ui")
    if ui_path not in sys.path:
        sys.path.insert(0, ui_path)
    from chip_signals import build_chip_facts  # noqa: WPS433

    return build_chip_facts


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _to_float(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "").replace("+", "")
    if not text or text in {"—", "-", "NA", "N/A", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# --- Price oracles -----------------------------------------------------------


def build_price_oracle() -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Return (per-stock date->close, TAIEX date->close) from everything on disk.

    Stock closes union chart-history (dense, 60 consecutive trading days) with
    snapshot closes. TAIEX closes come from the 大盤收盤 column of every snapshot.
    """
    stock_closes: dict[str, dict[str, float]] = {}
    taiex_closes: dict[str, float] = {}
    root = stock_root()
    if not root.exists():
        return stock_closes, taiex_closes

    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        for csv_path in date_dir.glob("tw_stock_*_chart_history.csv"):
            match = re.match(r"^tw_stock_(\d+)_chart_history\.csv$", csv_path.name)
            if not match:
                continue
            stock_id = match.group(1)
            series = stock_closes.setdefault(stock_id, {})
            for row in _read_csv_rows(csv_path):
                date = str(row.get("日期", "")).strip()
                close = _to_float(row.get("收盤價"))
                if date and close is not None:
                    series[date] = close
        for csv_path in date_dir.glob("tw_stock_*.csv"):
            match = _SNAPSHOT_RE.match(csv_path.name)
            if not match:
                continue
            stock_id = match.group(1)
            rows = _read_csv_rows(csv_path)
            if not rows:
                continue
            row = rows[0]
            date = str(row.get("日期", "")).strip()
            close = _to_float(row.get("收盤價"))
            if date and close is not None:
                stock_closes.setdefault(stock_id, {}).setdefault(date, close)
            taiex = _to_float(row.get("大盤收盤"))
            if date and taiex is not None:
                taiex_closes.setdefault(date, taiex)
    return stock_closes, taiex_closes


def _forward_dates(sorted_dates: list[str], entry_date: str) -> dict[int, str | None]:
    """Map each horizon to the entry_date + N-th trading day, or None if unavailable."""
    try:
        idx = sorted_dates.index(entry_date)
    except ValueError:
        return {h: None for h in HORIZONS}
    result: dict[int, str | None] = {}
    for horizon in HORIZONS:
        target = idx + horizon
        result[horizon] = sorted_dates[target] if target < len(sorted_dates) else None
    return result


# --- Labeling ----------------------------------------------------------------


def iter_snapshots() -> list[tuple[str, str, Path, Path]]:
    """Return (stock_id, trade_date, snapshot_csv, history_csv) for every snapshot."""
    root = stock_root()
    entries: list[tuple[str, str, Path, Path]] = []
    if not root.exists():
        return entries
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        trade_date = date_dir.name
        for csv_path in sorted(date_dir.glob("tw_stock_*.csv")):
            match = _SNAPSHOT_RE.match(csv_path.name)
            if not match:
                continue
            stock_id = match.group(1)
            history = date_dir / f"tw_stock_{stock_id}_history.csv"
            entries.append((stock_id, trade_date, csv_path, history))
    return entries


def _labels_and_numeric(facts) -> tuple[dict[str, str], dict[str, float | None]]:
    labels = {
        key: getattr(facts, key, "unknown")
        for key in EXCESS_LABEL_KEYS
        if getattr(facts, key, "unknown") not in (None, "", "unknown")
    }
    numeric = {key: getattr(facts, key, None) for key in NUMERIC_KEYS}
    return labels, numeric


def _horizon_record(
    entry_close: float,
    fwd_close: float | None,
    market_entry: float | None,
    market_fwd: float | None,
) -> dict[str, Any]:
    if fwd_close is None or entry_close <= 0:
        return {"status": "pending"}
    return_pct = round((fwd_close - entry_close) / entry_close * 100, 2)
    record: dict[str, Any] = {
        "status": "resolved",
        "fwd_close": fwd_close,
        "return_pct": return_pct,
        "market_return_pct": None,
        "excess_return_pct": None,
    }
    if (
        market_entry is not None
        and market_fwd is not None
        and market_entry > 0
    ):
        market_return = round((market_fwd - market_entry) / market_entry * 100, 2)
        record["market_return_pct"] = market_return
        record["excess_return_pct"] = round(return_pct - market_return, 2)
    return record


def label_all(*, quiet: bool = False) -> dict[str, dict[str, Any]]:
    build_chip_facts = _load_chip_signals()
    stock_closes, taiex_closes = build_price_oracle()
    today = dt.date.today().isoformat()

    store = _load_store()
    resolved_new = 0

    for stock_id, trade_date, snap_path, hist_path in iter_snapshots():
        key = f"{stock_id}|{trade_date}"
        rows = _read_csv_rows(snap_path)
        if not rows:
            continue
        snapshot = rows[0]
        entry_close = _to_float(snapshot.get("收盤價"))
        if entry_close is None:
            continue

        series = stock_closes.get(stock_id, {})
        sorted_dates = sorted(series)
        fwd_dates = _forward_dates(sorted_dates, trade_date)
        market_entry = taiex_closes.get(trade_date)

        existing = store.get(key)
        if existing is None:
            history = _read_csv_rows(hist_path)
            facts = build_chip_facts(snapshot, history)
            labels, numeric = _labels_and_numeric(facts)
            existing = {
                "stock_id": stock_id,
                "trade_date": trade_date,
                "entry_close": entry_close,
                "labels": labels,
                "numeric": numeric,
                "horizons": {f"{h}d": {"status": "pending"} for h in HORIZONS},
            }
            store[key] = existing

        horizons = existing["horizons"]
        changed = False
        for horizon in HORIZONS:
            slot = f"{horizon}d"
            if horizons.get(slot, {}).get("status") == "resolved":
                continue
            fwd_date = fwd_dates.get(horizon)
            fwd_close = series.get(fwd_date) if fwd_date else None
            record = _horizon_record(
                entry_close,
                fwd_close,
                market_entry,
                taiex_closes.get(fwd_date) if fwd_date else None,
            )
            if fwd_date:
                record.setdefault("fwd_date", fwd_date)
            if record.get("status") == "resolved" and horizons.get(slot, {}).get(
                "status"
            ) != "resolved":
                changed = True
                resolved_new += 1
            horizons[slot] = record
        if changed:
            existing["computed_at"] = today

    _write_store(store)
    if not quiet:
        matured = sum(
            1
            for rec in store.values()
            if all(h.get("status") == "resolved" for h in rec["horizons"].values())
        )
        print(
            f"outcomes: {len(store)} snapshots labeled, "
            f"{resolved_new} horizons newly resolved, "
            f"{matured} fully matured -> {outcomes_path()}",
            file=sys.stderr,
        )
    return store


def _load_store() -> dict[str, dict[str, Any]]:
    path = outcomes_path()
    store: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return store
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = f"{record.get('stock_id')}|{record.get('trade_date')}"
        store[key] = record
    return store


def _write_store(store: dict[str, dict[str, Any]]) -> None:
    path = outcomes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(store.values(), key=lambda r: (r["trade_date"], r["stock_id"]))
    with path.open("w", encoding="utf-8") as handle:
        for record in ordered:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="回顧型 outcome labeling：把歷史快照接上已實現 forward 報酬與超額報酬",
    )
    parser.add_argument("--quiet", action="store_true", help="不印摘要到 stderr")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = label_all(quiet=args.quiet)
    if not store and not args.quiet:
        print(
            "找不到任何 reports/stock/*/tw_stock_*.csv，請先執行 stock-report。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
