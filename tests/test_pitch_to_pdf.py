"""Tests for docs/pitch_to_pdf.py (HTML transform; no Chrome required)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "docs" / "pitch_to_pdf.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pitch_to_pdf", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pitch_to_pdf"] = mod
    spec.loader.exec_module(mod)
    return mod


pitch_to_pdf = _load_module()

MINI_HTML = """<!DOCTYPE html>
<html>
<head><title>demo</title></head>
<body>
  <header>
    <div>
      <h1>券商投研 Agent 作業系統
        <span>Phase 0–5 已落地</span>
      </h1>
    </div>
    <div class="flex flex-wrap">
      <button type="button">全域</button>
      <button type="button" id="btn-notes">講者小抄</button>
    </div>
  </header>
  <footer id="drawer">面試一句話</footer>
</body>
</html>
"""


class PrepareProjectionHtmlTests(unittest.TestCase):
    def test_injects_style_and_script(self) -> None:
        out = pitch_to_pdf.prepare_projection_html(MINI_HTML)
        self.assertIn('id="pitch-projection"', out)
        self.assertIn('id="pitch-projection-js"', out)
        self.assertIn("#btn-notes", out)
        self.assertIn("#drawer", out)
        self.assertIn("h1 > span", out)

    def test_does_not_strip_source_markers(self) -> None:
        """Transform is a copy: original tokens still exist in the temp HTML,
        hidden by CSS / removed at DOMContentLoaded, not deleted from disk."""
        out = pitch_to_pdf.prepare_projection_html(MINI_HTML)
        self.assertIn("講者小抄", out)
        self.assertIn("Phase 0–5 已落地", out)
        self.assertIn("面試一句話", out)

    def test_missing_head_still_prefixes_style(self) -> None:
        out = pitch_to_pdf.prepare_projection_html("<p>no head</p>")
        self.assertTrue(out.startswith(pitch_to_pdf.PROJECTION_STYLE))

    def test_computer_keeps_source_type_density(self) -> None:
        out = pitch_to_pdf.prepare_projection_html(MINI_HTML, pitch_to_pdf.PROFILES["computer"].extra_css)
        self.assertNotIn("font-size: 14px", out)

    def test_projector_bumps_type(self) -> None:
        out = pitch_to_pdf.prepare_projection_html(MINI_HTML, pitch_to_pdf.PROFILES["projector"].extra_css)
        self.assertIn(".text-xs", out)
        self.assertIn("font-size: 14px", out)

    def test_resolve_outputs_both_uses_profile_filenames(self) -> None:
        html = Path("/tmp/docs/pitch.html")
        jobs = pitch_to_pdf.resolve_outputs("both", html, None)
        names = [path.name for _, path in jobs]
        self.assertEqual(names, ["pitch-computer.pdf", "pitch-projector.pdf"])
        self.assertEqual([p.key for p, _ in jobs], ["computer", "projector"])
        self.assertEqual(pitch_to_pdf.PROFILES["computer"].scale, 2)
        self.assertEqual(pitch_to_pdf.PROFILES["projector"].scale, 2)

    def test_resolve_outputs_both_rejects_pdf_path(self) -> None:
        with self.assertRaises(ValueError):
            pitch_to_pdf.resolve_outputs("both", Path("/tmp/pitch.html"), Path("/tmp/out.pdf"))


if __name__ == "__main__":
    unittest.main()
