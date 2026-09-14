"""Phase 5 ADK peer client: howto, Card URL, loopback-only Web, no committed key."""

from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.a2a_adk import (  # noqa: E402
    CLIENTS_DIR,
    DEFAULT_CARD_URL,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    apply_gemini_key_alias,
    has_gemini_key,
    howto_text,
    main as adk_main,
)
from agent.tools._types import EXIT_BAD_ARGS, EXIT_OK  # noqa: E402
from main import main as cli_main  # noqa: E402


class A2AAdkLauncherTests(unittest.TestCase):
    def test_default_model_is_gemini_36_flash(self) -> None:
        self.assertEqual(DEFAULT_MODEL, "gemini-3.6-flash")

    def test_howto_text_points_at_local_card(self) -> None:
        text = howto_text()
        self.assertIn("127.0.0.1:8002", text)
        self.assertIn(DEFAULT_CARD_URL, text)
        self.assertIn("2330 要不要動", text)
        self.assertIn("GOOGLE_API_KEY", text)
        self.assertIn("不代填", text)
        self.assertIn(str(CLIENTS_DIR), text)

    def test_a2a_adk_howto(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = adk_main(["--howto"])
        self.assertEqual(code, EXIT_OK)
        output = buffer.getvalue()
        self.assertIn("stock_a2a", output)
        self.assertIn("http://127.0.0.1:9999", output)
        self.assertIn("2330 要不要動", output)

    def test_main_py_routes_a2a_adk_howto(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = cli_main(["a2a-adk", "--howto"])
        self.assertEqual(code, 0)
        self.assertIn("a2a-adk", buffer.getvalue())
        self.assertIn(str(DEFAULT_PORT), buffer.getvalue())

    def test_missing_key_does_not_start_web(self) -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("GOOGLE_API_KEY", "GEMINI_API_KEY")
        }
        stderr = io.StringIO()
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
            patch("agent.a2a_adk._load_root_env"),
            patch("agent.a2a_adk._run") as run,
        ):
            code = adk_main([])
        self.assertEqual(code, EXIT_BAD_ARGS)
        self.assertIn("GOOGLE_API_KEY", stderr.getvalue())
        run.assert_not_called()

    def test_non_loopback_host_rejected(self) -> None:
        stderr = io.StringIO()
        stdout = io.StringIO()
        with (
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
            patch("agent.a2a_adk.has_gemini_key", return_value=True),
            patch("agent.a2a_adk._run") as run,
        ):
            code = adk_main(["--host", "0.0.0.0"])
        self.assertEqual(code, EXIT_BAD_ARGS)
        self.assertIn("loopback", stderr.getvalue())
        run.assert_not_called()

    def test_gemini_key_alias_fills_google_api_key(self) -> None:
        env = {"GEMINI_API_KEY": "from-gemini"}
        with patch.dict(os.environ, env, clear=True):
            apply_gemini_key_alias()
            self.assertEqual(os.environ.get("GOOGLE_API_KEY"), "from-gemini")

    def test_has_gemini_key_true_when_google_set(self) -> None:
        with (
            patch("agent.a2a_adk._load_root_env"),
            patch.dict(os.environ, {"GOOGLE_API_KEY": "abc"}, clear=True),
        ):
            self.assertTrue(has_gemini_key())

    def test_adk_web_argv_uses_isolated_project(self) -> None:
        from agent.a2a_adk import _adk_web_argv

        argv = _adk_web_argv(host="127.0.0.1", port=8002, skip_install=True)
        self.assertIsNotNone(argv)
        assert argv is not None
        self.assertIn("--project", argv)
        self.assertIn(str(CLIENTS_DIR), argv)
        self.assertIn("stock_a2a", argv)
        self.assertIn("--frozen", argv)
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")

    def test_clients_dir_has_pyproject(self) -> None:
        self.assertTrue((CLIENTS_DIR / "pyproject.toml").is_file())
        self.assertTrue((CLIENTS_DIR / "stock_a2a" / "agent.py").is_file())
        self.assertTrue((CLIENTS_DIR / "uv.lock").is_file())

    def test_agent_module_source_mentions_card_and_remote_name(self) -> None:
        source = (CLIENTS_DIR / "stock_a2a" / "agent.py").read_text(encoding="utf-8")
        self.assertIn("RemoteA2aAgent", source)
        self.assertIn("AgentTool", source)
        self.assertIn("tools=[AgentTool(stock_agent)]", source)
        self.assertNotIn("sub_agents=[stock_agent]", source)
        self.assertIn("同一輪", source)
        self.assertIn("stock_winning_rate", source)
        self.assertIn("md_path", source)
        self.assertIn("skip_pdf", source)
        self.assertIn("無產生實體報告檔案", source)
        self.assertIn("禁止", source)
        self.assertIn("2454_analysis.md", source)
        self.assertIn("逐字抄", source)
        self.assertIn("127.0.0.1:9999", source)
        self.assertIn("gemini-3.6-flash", source)
        self.assertNotIn("gemini-2.5-flash", source)
        self.assertNotIn("GOOGLE_API_KEY=", source)


if __name__ == "__main__":
    unittest.main()
