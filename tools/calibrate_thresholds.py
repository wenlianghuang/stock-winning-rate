#!/usr/bin/env python3
"""Calibration job: per-stock adaptive thresholds + regime base rates.

Two deterministic outputs, both consumed back by ``chip_signals`` on the next
report run (graceful fallback to the hard-coded constants when a file is absent
or the sample is too small):

(a) ``reports/calibration/{stock}.thresholds.json``
    Percentile cutoffs from each stock's *own* price history (rolling RSI / ATR%
    / volume ratio out of its latest 60-day ``_chart_history.csv``). Lets
    "RSI 偏高" mean the top of *this* stock's range instead of a fixed 70.

(b) ``reports/calibration/base_rates.json``
    Per-regime forward-excess statistics aggregated from ``outcomes.jsonl``,
    shrunk toward the all-market prior so tiny buckets don't overfit. Injected
    into the agy prompt so the scenario section is backed by realized hit rates.

Pure stdlib. Safe to re-run.
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

HORIZONS = (1, 3, 5, 10)
EPSILON_PCT = 0.5  # excess beyond +/- this counts as a directional move
SHRINK_K = 20  # pseudo-count pulling small buckets toward the global prior
MIN_THRESHOLD_SAMPLE = 30
MIN_BUCKET_SAMPLE = 20
PCT_LOW = 15.0
PCT_HIGH = 85.0

LABEL_KEYS = (
    "chip_regime",
    "ma_stack",
    "trend_strength",
    "price_trend",
    "rs_period",
    "institutional_consensus",
    "volatility_regime",
    "rsi_zone",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def stock_root() -> Path:
    return project_root() / "reports" / "stock"


def calibration_dir() -> Path:
    return project_root() / "reports" / "calibration"


def outcomes_path() -> Path:
    return project_root() / "reports" / "outcomes" / "outcomes.jsonl"


def _load_chip_helpers():
    ui_path = str(project_root() / "ui")
    if ui_path not in sys.path:
        sys.path.insert(0, ui_path)
    from chip_signals import _atr_wilder, _rsi_wilder  # noqa: WPS433

    return _rsi_wilder, _atr_wilder


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


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (q / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * frac, 2)


# --- (a) per-stock thresholds ------------------------------------------------


def _latest_chart_history(stock_id: str) -> Path | None:
    root = stock_root()
    if not root.exists():
        return None
    best: Path | None = None
    for date_dir in sorted(root.iterdir()):
        candidate = date_dir / f"tw_stock_{stock_id}_chart_history.csv"
        if candidate.exists():
            best = candidate  # sorted ascending -> keep latest
    return best


def _rolling_series(chart_rows: list[dict[str, str]]) -> dict[str, list[float]]:
    _rsi_wilder, _atr_wilder = _load_chip_helpers()
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    for row in chart_rows:
        close = _to_float(row.get("收盤價"))
        if close is None:
            continue
        closes.append(close)
        highs.append(_to_float(row.get("最高價")) or close)
        lows.append(_to_float(row.get("最低價")) or close)
        vol = _to_float(row.get("成交量_張"))
        volumes.append(vol if vol is not None else 0.0)

    rsi_series: list[float] = []
    atr_pct_series: list[float] = []
    vol_ratio_series: list[float] = []

    for end in range(15, len(closes) + 1):
        rsi = _rsi_wilder(closes[:end])
        if rsi is not None:
            rsi_series.append(rsi)
        atr = _atr_wilder(highs[:end], lows[:end], closes[:end])
        last_close = closes[end - 1]
        if atr is not None and last_close > 0:
            atr_pct_series.append(round(atr / last_close * 100, 2))

    for end in range(20, len(volumes) + 1):
        window = volumes[:end]
        vol_ma5 = sum(window[-5:]) / 5
        vol_ma20 = sum(window[-20:]) / 20
        if vol_ma20 > 0:
            vol_ratio_series.append(round(vol_ma5 / vol_ma20, 3))

    return {
        "rsi": rsi_series,
        "atr_pct": atr_pct_series,
        "vol_ratio": vol_ratio_series,
    }


def calibrate_stock_thresholds(stock_ids: list[str], *, quiet: bool) -> int:
    written = 0
    today = dt.date.today().isoformat()
    calibration_dir().mkdir(parents=True, exist_ok=True)
    for stock_id in stock_ids:
        chart = _latest_chart_history(stock_id)
        if chart is None:
            continue
        series = _rolling_series(_read_csv_rows(chart))
        rsi = series["rsi"]
        atr = series["atr_pct"]
        vol = series["vol_ratio"]
        if len(rsi) < MIN_THRESHOLD_SAMPLE:
            continue
        payload: dict[str, Any] = {
            "stock_id": stock_id,
            "computed_at": today,
            "source": f"chart_history:{chart.parent.name}",
            "n_rsi": len(rsi),
            "n_atr": len(atr),
            "n_vol": len(vol),
            "rsi_overbought": _percentile(rsi, PCT_HIGH),
            "rsi_oversold": _percentile(rsi, PCT_LOW),
            "atr_pct_high": _percentile(atr, PCT_HIGH),
            "atr_pct_low": _percentile(atr, PCT_LOW),
            "volume_spike_ratio": _percentile(vol, PCT_HIGH),
            "volume_shrink_ratio": _percentile(vol, PCT_LOW),
        }
        out = calibration_dir() / f"{stock_id}.thresholds.json"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written += 1
    if not quiet:
        print(
            f"thresholds: wrote {written} per-stock file(s) -> {calibration_dir()}",
            file=sys.stderr,
        )
    return written


def _all_stock_ids() -> list[str]:
    root = stock_root()
    ids: set[str] = set()
    if not root.exists():
        return []
    for date_dir in root.iterdir():
        if not date_dir.is_dir():
            continue
        for path in date_dir.glob("tw_stock_*_chart_history.csv"):
            match = re.match(r"^tw_stock_(\d+)_chart_history\.csv$", path.name)
            if match:
                ids.add(match.group(1))
    return sorted(ids)


# --- (b) regime base rates ---------------------------------------------------


def _load_outcomes() -> list[dict[str, Any]]:
    path = outcomes_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _resolved_excess(horizon_record: dict[str, Any]) -> float | None:
    if horizon_record.get("status") != "resolved":
        return None
    excess = horizon_record.get("excess_return_pct")
    if excess is not None:
        return float(excess)
    ret = horizon_record.get("return_pct")
    return float(ret) if ret is not None else None


def _summary(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean_excess": None, "p_up": None}
    mean = sum(values) / n
    p_up = sum(1 for v in values if v > EPSILON_PCT) / n
    return {"n": n, "mean_excess": round(mean, 2), "p_up": round(p_up, 3)}


def _shrink(bucket: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    n = bucket["n"]
    if n == 0 or prior["n"] == 0 or bucket["mean_excess"] is None:
        return bucket
    weight = n / (n + SHRINK_K)
    mean = weight * bucket["mean_excess"] + (1 - weight) * prior["mean_excess"]
    p_up = weight * bucket["p_up"] + (1 - weight) * prior["p_up"]
    return {
        **bucket,
        "mean_excess_shrunk": round(mean, 2),
        "p_up_shrunk": round(p_up, 3),
        "below_min_sample": n < MIN_BUCKET_SAMPLE,
    }


def build_base_rates(records: list[dict[str, Any]]) -> dict[str, Any]:
    global_stats: dict[str, dict[str, Any]] = {}
    for horizon in HORIZONS:
        slot = f"{horizon}d"
        values = [
            excess
            for rec in records
            if (excess := _resolved_excess(rec.get("horizons", {}).get(slot, {})))
            is not None
        ]
        global_stats[slot] = _summary(values)

    buckets: dict[str, dict[str, dict[str, Any]]] = {}
    for label_key in LABEL_KEYS:
        by_value: dict[str, dict[str, Any]] = {}
        values_present: set[str] = {
            str(rec.get("labels", {}).get(label_key))
            for rec in records
            if rec.get("labels", {}).get(label_key) not in (None, "", "unknown")
        }
        for value in sorted(values_present):
            per_horizon: dict[str, Any] = {}
            for horizon in HORIZONS:
                slot = f"{horizon}d"
                excesses = [
                    excess
                    for rec in records
                    if str(rec.get("labels", {}).get(label_key)) == value
                    and (
                        excess := _resolved_excess(
                            rec.get("horizons", {}).get(slot, {})
                        )
                    )
                    is not None
                ]
                per_horizon[slot] = _shrink(_summary(excesses), global_stats[slot])
            by_value[value] = per_horizon
        if by_value:
            buckets[label_key] = by_value

    return {
        "generated_at": dt.date.today().isoformat(),
        "epsilon_pct": EPSILON_PCT,
        "shrink_k": SHRINK_K,
        "min_bucket_sample": MIN_BUCKET_SAMPLE,
        "horizons": [f"{h}d" for h in HORIZONS],
        "global": global_stats,
        "buckets": buckets,
    }


def calibrate_base_rates(*, quiet: bool) -> dict[str, Any]:
    records = _load_outcomes()
    payload = build_base_rates(records)
    calibration_dir().mkdir(parents=True, exist_ok=True)
    out = calibration_dir() / "base_rates.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not quiet:
        resolved = sum(
            1
            for rec in records
            for slot in (f"{h}d" for h in HORIZONS)
            if rec.get("horizons", {}).get(slot, {}).get("status") == "resolved"
        )
        print(
            f"base_rates: {len(records)} records, {resolved} resolved horizons -> {out}",
            file=sys.stderr,
        )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校準：每檔自適應分位門檻 + regime 命中率 base rate（含 shrinkage）",
    )
    parser.add_argument(
        "--stock", help="只校準某股票代碼的門檻，例如 2409（base rate 仍全量計算）"
    )
    parser.add_argument(
        "--skip-thresholds", action="store_true", help="略過每檔分位門檻"
    )
    parser.add_argument(
        "--skip-base-rates", action="store_true", help="略過 regime base rate"
    )
    parser.add_argument("--quiet", action="store_true", help="不印摘要到 stderr")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.skip_thresholds:
        stock_ids = [args.stock] if args.stock else _all_stock_ids()
        calibrate_stock_thresholds(stock_ids, quiet=args.quiet)
    if not args.skip_base_rates:
        calibrate_base_rates(quiet=args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
