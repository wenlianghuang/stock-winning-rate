"""Tests for the calibration job (percentile thresholds + base rates)."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "ui"))

import calibrate_thresholds as calib  # noqa: E402


class PercentileTests(unittest.TestCase):
    def test_percentile_interpolates(self) -> None:
        values = [10.0, 20.0, 30.0, 40.0]
        self.assertEqual(calib._percentile(values, 0), 10.0)
        self.assertEqual(calib._percentile(values, 100), 40.0)
        self.assertEqual(calib._percentile(values, 50), 25.0)

    def test_percentile_empty(self) -> None:
        self.assertIsNone(calib._percentile([], 50))


class BaseRatesTests(unittest.TestCase):
    def _record(self, regime: str, excess_5d: float) -> dict:
        return {
            "stock_id": "9999",
            "trade_date": "2026-06-01",
            "labels": {"chip_regime": regime},
            "horizons": {
                "1d": {"status": "resolved", "return_pct": 1.0,
                       "excess_return_pct": 0.5},
                "3d": {"status": "pending"},
                "5d": {"status": "resolved", "return_pct": excess_5d,
                       "excess_return_pct": excess_5d},
                "10d": {"status": "pending"},
            },
        }

    def test_build_base_rates_structure(self) -> None:
        records = [
            self._record("accumulation", 2.0),
            self._record("accumulation", -1.0),
            self._record("distribution", -3.0),
        ]
        payload = calib.build_base_rates(records)
        self.assertIn("global", payload)
        self.assertIn("chip_regime", payload["buckets"])
        acc = payload["buckets"]["chip_regime"]["accumulation"]["5d"]
        self.assertEqual(acc["n"], 2)
        self.assertAlmostEqual(acc["mean_excess"], 0.5)
        # p_up counts excess > EPSILON_PCT (0.5); only +2.0 qualifies -> 0.5
        self.assertAlmostEqual(acc["p_up"], 0.5)
        self.assertIn("mean_excess_shrunk", acc)
        self.assertTrue(acc["below_min_sample"])

    def test_global_ignores_pending(self) -> None:
        payload = calib.build_base_rates([self._record("accumulation", 2.0)])
        self.assertEqual(payload["global"]["3d"]["n"], 0)
        self.assertEqual(payload["global"]["5d"]["n"], 1)


class ThresholdWriteTests(unittest.TestCase):
    def test_thresholds_written_with_enough_samples(self) -> None:
        headers = ["日期", "開盤價", "最高價", "最低價", "收盤價", "成交量_張", "漲跌幅"]
        rows = []
        for idx in range(60):
            close = 100 + (idx % 7) - 3  # oscillating series
            rows.append({
                "日期": f"2026-04-{idx + 1:02d}",
                "開盤價": close,
                "最高價": close + 2,
                "最低價": close - 2,
                "收盤價": close,
                "成交量_張": 1000 + (idx % 5) * 200,
                "漲跌幅": 0.0,
            })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chart = root / "stock" / "2026-06-11" / "tw_stock_9999_chart_history.csv"
            chart.parent.mkdir(parents=True, exist_ok=True)
            with chart.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)
            calib_dir = root / "calibration"
            with mock.patch.object(calib, "stock_root", lambda: root / "stock"), \
                 mock.patch.object(calib, "calibration_dir", lambda: calib_dir):
                written = calib.calibrate_stock_thresholds(["9999"], quiet=True)

            self.assertEqual(written, 1)
            payload = json.loads(
                (calib_dir / "9999.thresholds.json").read_text(encoding="utf-8")
            )
            self.assertGreaterEqual(payload["n_rsi"], calib.MIN_THRESHOLD_SAMPLE)
            self.assertGreaterEqual(payload["rsi_overbought"], payload["rsi_oversold"])


if __name__ == "__main__":
    unittest.main()
