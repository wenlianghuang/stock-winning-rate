"""Tests for reports/stock/{date}/{id}/ layout helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from stock_paths import (  # noqa: E402
    find_stock_csv,
    iter_date_snapshot_csvs,
    migrate_flat_date_dir,
    stock_csv_path,
)


class StockPathsTests(unittest.TestCase):
    def test_write_path_is_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = stock_csv_path("2026-09-21", "2330", root=root)
            self.assertEqual(
                path,
                root / "reports" / "stock" / "2026-09-21" / "2330" / "tw_stock_2330.csv",
            )
            self.assertTrue(path.parent.is_dir())

    def test_find_prefers_nested_over_flat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "reports" / "stock" / "2026-09-21"
            flat = day / "tw_stock_2330.csv"
            nested = day / "2330" / "tw_stock_2330.csv"
            day.mkdir(parents=True)
            flat.write_text("flat\n", encoding="utf-8")
            nested.parent.mkdir()
            nested.write_text("nested\n", encoding="utf-8")
            found = find_stock_csv("2330", "2026-09-21", root=root)
            self.assertEqual(found, nested)

    def test_find_falls_back_to_flat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "reports" / "stock" / "2026-09-21"
            flat = day / "tw_stock_2409.csv"
            day.mkdir(parents=True)
            flat.write_text("x\n", encoding="utf-8")
            found = find_stock_csv("2409", "2026-09-21", root=root)
            self.assertEqual(found, flat)

    def test_iter_snapshots_nested_and_flat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026-09-21"
            (day / "2330").mkdir(parents=True)
            (day / "2330" / "tw_stock_2330.csv").write_text("a\n", encoding="utf-8")
            (day / "tw_stock_2409.csv").write_text("b\n", encoding="utf-8")
            (day / "tw_stock_2330_history.csv").write_text("h\n", encoding="utf-8")
            paths = iter_date_snapshot_csvs(day)
            ids = {p.parent.name if p.parent.name != "2026-09-21" else "2409" for p in paths}
            # 2330 nested parent is 2330; 2409 flat parent is date
            names = {p.name for p in paths}
            self.assertEqual(names, {"tw_stock_2330.csv", "tw_stock_2409.csv"})

    def test_migrate_flat_into_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026-09-21"
            day.mkdir()
            csv_path = day / "tw_stock_2330.csv"
            rounds = day / "tw_stock_2330.gate.rounds"
            csv_path.write_text("x\n", encoding="utf-8")
            rounds.mkdir()
            (rounds / "r01.body.md").write_text("body\n", encoding="utf-8")
            moves = migrate_flat_date_dir(day)
            self.assertEqual(len(moves), 2)
            self.assertTrue((day / "2330" / "tw_stock_2330.csv").is_file())
            self.assertTrue((day / "2330" / "tw_stock_2330.gate.rounds" / "r01.body.md").is_file())
            self.assertFalse(csv_path.exists())


if __name__ == "__main__":
    unittest.main()
