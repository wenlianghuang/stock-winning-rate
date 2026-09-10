"""Phase 4 LLM adapter: backend selection, no agy import at complete() dispatch."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.llm import complete, resolved_backend  # noqa: E402


class LlmAdapterTests(unittest.TestCase):
    def test_default_backend_is_agy(self) -> None:
        env = os.environ.copy()
        env.pop("LLM_BACKEND", None)
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(resolved_backend(), "agy")

    def test_ollama_backend_from_env(self) -> None:
        with patch.dict("os.environ", {"LLM_BACKEND": "ollama"}):
            self.assertEqual(resolved_backend(), "ollama")

    def test_unknown_backend_rejected(self) -> None:
        with patch.dict("os.environ", {"LLM_BACKEND": "copilot"}):
            with self.assertRaises(ValueError):
                resolved_backend()

    def test_complete_dispatches_agy(self) -> None:
        with patch.dict("os.environ", {"LLM_BACKEND": "agy"}):
            with patch("agent.llm.complete_agy", return_value="hello") as mock_agy:
                self.assertEqual(complete("p"), "hello")
                mock_agy.assert_called_once()

    def test_complete_dispatches_ollama(self) -> None:
        with patch.dict("os.environ", {"LLM_BACKEND": "ollama"}):
            with patch("agent.llm.complete_ollama", return_value="local") as mock_ol:
                self.assertEqual(complete("p"), "local")
                mock_ol.assert_called_once()


if __name__ == "__main__":
    unittest.main()
