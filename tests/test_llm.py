"""Phase 4 LLM adapter: backend selection, no agy import at complete() dispatch."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.llm import (  # noqa: E402
    AgyResult,
    complete,
    invoke_agy,
    resolve_ollama_model,
    resolved_backend,
)


def _load_us_tech_process_report():
    """Load us-tech process_report under its own name.

    concept-news also publishes process_report. A bare import reuses whichever
    copy unittest already loaded.
    """
    name = "us_tech_process_report"
    path = ROOT / ".agents" / "skills" / "us-tech-news" / "process_report.py"
    loaded = sys.modules.get(name)
    if loaded is not None and Path(getattr(loaded, "__file__", "")).resolve() == path.resolve():
        return loaded
    directory = str(path.parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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

    def test_invoke_agy_returns_cleaned_text_and_exit_code(self) -> None:
        body = "台股籌碼敘事。" * 20
        completed = type(
            "Completed",
            (),
            {"returncode": 3, "stdout": body, "stderr": ""},
        )()
        with patch.dict("os.environ", {"AGY_BIN": "/tmp/agy"}):
            with patch("agent.llm.subprocess.run", return_value=completed) as run:
                result = invoke_agy("prompt", min_chars=20)
        self.assertEqual(result, AgyResult(text=body, exit_code=3))
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "/tmp/agy")
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertEqual(run.call_args.kwargs["timeout"], 900)

    def test_complete_agy_uses_invoke_agy(self) -> None:
        with patch.dict("os.environ", {"LLM_BACKEND": "agy"}):
            with patch(
                "agent.llm.invoke_agy",
                return_value=AgyResult(text="ok", exit_code=0),
            ) as invoke:
                self.assertEqual(complete("p"), "ok")
        invoke.assert_called_once()

    def test_gates_call_invoke_agy(self) -> None:
        modules = {
            "report_gate": ROOT / ".agents" / "skills" / "report-gate",
            "position_gate": ROOT / ".agents" / "skills" / "position-gate",
            "portfolio_gate": ROOT / ".agents" / "skills" / "portfolio-gate",
            "market_daily_gate": ROOT / ".agents" / "skills" / "market-daily",
            "market_weekly_gate": ROOT / ".agents" / "skills" / "market-weekly",
        }
        for directory in modules.values():
            text = str(directory)
            if text not in sys.path:
                sys.path.insert(0, text)
        with patch.dict("os.environ", {"LLM_BACKEND": "agy"}):
            with patch(
                "agent.llm.invoke_agy",
                return_value=AgyResult(text="body", exit_code=2),
            ) as invoke:
                import market_daily_gate
                import market_weekly_gate
                import portfolio_gate
                import position_gate
                import report_gate

                process_report = _load_us_tech_process_report()
                for module in (
                    report_gate,
                    position_gate,
                    portfolio_gate,
                    market_daily_gate,
                    market_weekly_gate,
                    process_report,
                ):
                    self.assertEqual(module.run_agy("prompt"), ("body", 2))
        self.assertEqual(invoke.call_count, 6)
        for call in invoke.call_args_list:
            self.assertEqual(call.kwargs["min_chars"], 120)

    def test_missing_agy_raises_file_not_found(self) -> None:
        env = os.environ.copy()
        env.pop("AGY_BIN", None)
        with patch.dict("os.environ", env, clear=True):
            with patch("agent.llm.shutil.which", return_value=None):
                with self.assertRaises(FileNotFoundError):
                    invoke_agy("p")

    def test_resolve_ollama_model_falls_back_to_installed_llama3(self) -> None:
        with patch(
            "agent.llm.list_ollama_models",
            return_value=["llama3.2:3b"],
        ):
            self.assertEqual(resolve_ollama_model(preferred="llama3.1"), "llama3.2:3b")
        with patch(
            "agent.llm.list_ollama_models",
            return_value=["llama3.1:latest"],
        ):
            self.assertEqual(resolve_ollama_model(preferred="llama3.1"), "llama3.1:latest")


if __name__ == "__main__":
    unittest.main()
