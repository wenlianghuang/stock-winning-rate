"""skip_pdf must skip writing PDF, not only skip printing the path."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

from report_pdf import ReportMeta, write_position_artifacts, write_summary_artifacts  # noqa: E402


class SkipPdfWriteTests(unittest.TestCase):
    def test_write_summary_artifacts_skip_pdf_does_not_call_pdf(self) -> None:
        csv_path = Path("/tmp/tw_stock_2308.csv")
        meta = ReportMeta(source="test", version="deep")
        with (
            patch("report_pdf.merge_report_body") as merge,
            patch("report_pdf.write_summary_markdown") as write_md,
            patch("report_pdf.write_summary_pdf") as write_pdf,
        ):
            md_path, pdf_path = write_summary_artifacts(
                csv_path, "body", meta, skip_pdf=True
            )
        merge.assert_not_called()
        write_md.assert_called_once()
        self.assertEqual(write_md.call_args.args[2], "body")
        write_pdf.assert_not_called()
        self.assertEqual(md_path, csv_path.with_suffix(".md"))
        self.assertIsNone(pdf_path)

    def test_write_summary_artifacts_pdf_merges_tables_markdown_stays_narrative(
        self,
    ) -> None:
        csv_path = Path("/tmp/tw_stock_2308.csv")
        meta = ReportMeta(source="test", version="deep")
        with (
            patch("report_pdf.merge_report_body", return_value="TABLES\n\nbody") as merge,
            patch("report_pdf.write_summary_markdown") as write_md,
            patch("report_pdf.write_summary_pdf", return_value=True) as write_pdf,
        ):
            write_summary_artifacts(csv_path, "body", meta, skip_pdf=False)
        merge.assert_called_once()
        self.assertEqual(write_md.call_args.args[2], "body")
        self.assertEqual(write_pdf.call_args.args[2], "TABLES\n\nbody")

    def test_write_position_artifacts_skip_pdf_does_not_call_pdf(self) -> None:
        csv_path = Path("/tmp/tw_stock_2330.csv")
        meta = ReportMeta(source="test", version="position")
        holding = object()
        with (
            patch("report_pdf.write_position_markdown") as write_md,
            patch("report_pdf.write_position_pdf") as write_pdf,
        ):
            md_path, pdf_path = write_position_artifacts(
                csv_path, "body", meta, holding, skip_pdf=True
            )
        write_md.assert_called_once()
        self.assertEqual(write_md.call_args.args[2], "body")
        write_pdf.assert_not_called()
        self.assertEqual(md_path, csv_path.with_name("tw_stock_2330_position.md"))
        self.assertIsNone(pdf_path)

    def test_write_position_artifacts_pdf_merges_tables_markdown_stays_narrative(
        self,
    ) -> None:
        csv_path = Path("/tmp/tw_stock_2330.csv")
        meta = ReportMeta(source="test", version="position")
        holding = object()
        with (
            patch(
                "position_tables.merge_position_report_body",
                return_value="TABLES\n\nbody",
            ) as merge,
            patch("report_pdf.write_position_markdown") as write_md,
            patch("report_pdf.write_position_pdf", return_value=True) as write_pdf,
        ):
            write_position_artifacts(
                csv_path, "body", meta, holding, skip_pdf=False
            )
        merge.assert_called_once()
        self.assertEqual(write_md.call_args.args[2], "body")
        self.assertEqual(write_pdf.call_args.args[2], "TABLES\n\nbody")


if __name__ == "__main__":
    unittest.main()
