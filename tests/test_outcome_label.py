"""Tests for the outcome labeling job."""

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

import outcome_label  # noqa: E402


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class HorizonRecordTests(unittest.TestCase):
    def test_pending_when_forward_missing(self) -> None:
        record = outcome_label._horizon_record(100.0, None, 20000.0, None)
        self.assertEqual(record["status"], "pending")

    def test_excess_vs_market(self) -> None:
        record = outcome_label._horizon_record(100.0, 110.0, 20000.0, 20200.0)
        self.assertEqual(record["status"], "resolved")
        self.assertEqual(record["return_pct"], 10.0)
        self.assertEqual(record["market_return_pct"], 1.0)
        self.assertEqual(record["excess_return_pct"], 9.0)

    def test_excess_none_when_market_missing(self) -> None:
        record = outcome_label._horizon_record(100.0, 110.0, None, None)
        self.assertEqual(record["return_pct"], 10.0)
        self.assertIsNone(record["excess_return_pct"])


class ForwardDateTests(unittest.TestCase):
    def test_forward_offsets(self) -> None:
        dates = [f"2026-06-{day:02d}" for day in range(1, 16)]
        fwd = outcome_label._forward_dates(dates, "2026-06-01")
        self.assertEqual(fwd[1], "2026-06-02")
        self.assertEqual(fwd[10], "2026-06-11")

    def test_forward_unavailable_near_edge(self) -> None:
        dates = ["2026-06-01", "2026-06-02", "2026-06-03"]
        fwd = outcome_label._forward_dates(dates, "2026-06-02")
        self.assertEqual(fwd[1], "2026-06-03")
        self.assertIsNone(fwd[5])


class LabelAllIntegrationTests(unittest.TestCase):
    def _build_tree(self, root: Path) -> None:
        dates = [f"2026-06-{day:02d}" for day in range(1, 16)]
        chart_headers = [
            "日期",
            "開盤價",
            "最高價",
            "最低價",
            "收盤價",
            "成交量_張",
            "漲跌幅",
        ]
        chart_rows = [
            {
                "日期": date,
                "開盤價": 100 + idx,
                "最高價": 101 + idx,
                "最低價": 99 + idx,
                "收盤價": 100 + idx,
                "成交量_張": 1000,
                "漲跌幅": 1.0,
            }
            for idx, date in enumerate(dates)
        ]
        # snapshot on entry date D0 (close 100, TAIEX 20000)
        stock_dir_d0 = root / "2026-06-01"
        _write_csv(
            stock_dir_d0 / "tw_stock_9999.csv",
            ["代碼", "名稱", "日期", "收盤價", "大盤收盤", "漲跌幅"],
            [{"代碼": "9999", "名稱": "測試", "日期": "2026-06-01", "收盤價": 100,
              "大盤收盤": 20000, "漲跌幅": 0.0}],
        )
        _write_csv(stock_dir_d0 / "tw_stock_9999_history.csv", ["日期", "收盤價"],
                   [{"日期": "2026-06-01", "收盤價": 100}])
        _write_csv(stock_dir_d0 / "tw_stock_9999_chart_history.csv", chart_headers,
                   chart_rows)
        # snapshot on forward date D10 (2026-06-11, close 110, TAIEX 20200 -> +1%)
        stock_dir_d10 = root / "2026-06-11"
        _write_csv(
            stock_dir_d10 / "tw_stock_9999.csv",
            ["代碼", "名稱", "日期", "收盤價", "大盤收盤", "漲跌幅"],
            [{"代碼": "9999", "名稱": "測試", "日期": "2026-06-11", "收盤價": 110,
              "大盤收盤": 20200, "漲跌幅": 0.0}],
        )
        _write_csv(stock_dir_d10 / "tw_stock_9999_history.csv", ["日期", "收盤價"],
                   [{"日期": "2026-06-11", "收盤價": 110}])
        _write_csv(stock_dir_d10 / "tw_stock_9999_chart_history.csv", chart_headers,
                   chart_rows)

    def test_label_all_resolves_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stock = root / "stock"
            self._build_tree(stock)
            out = root / "outcomes" / "outcomes.jsonl"
            with mock.patch.object(outcome_label, "stock_root", lambda: stock), \
                 mock.patch.object(outcome_label, "outcomes_path", lambda: out):
                store = outcome_label.label_all(quiet=True)

            key = "9999|2026-06-01"
            self.assertIn(key, store)
            horizons = store[key]["horizons"]
            self.assertEqual(horizons["1d"]["status"], "resolved")
            # 10d forward: close 100 -> 110 (+10%), market +1% => excess +9%
            self.assertEqual(horizons["10d"]["return_pct"], 10.0)
            self.assertEqual(horizons["10d"]["excess_return_pct"], 9.0)
            self.assertTrue(out.exists())
            lines = [line for line in out.read_text().splitlines() if line.strip()]
            self.assertTrue(any(json.loads(line)["stock_id"] == "9999" for line in lines))


if __name__ == "__main__":
    unittest.main()
