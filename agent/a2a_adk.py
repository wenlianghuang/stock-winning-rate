"""Launch a Google ADK peer agent against python main.py a2a.

CLI: ``python main.py a2a-adk``. This is the A2A *client* (Gemini + Card
discovery), not a second chip/gate stack. Inspector remains the debug UI.

ADK lives in ``agent/adk_clients`` (its own uv project) so google-adk does
not collide with this repo's FastAPI / a2a-sdk pins. Put ``GOOGLE_API_KEY``
in the repo-root ``.env`` yourself; this launcher never writes a key.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent.tools._types import EXIT_BAD_ARGS, EXIT_FAILED, EXIT_OK

ROOT = Path(__file__).resolve().parent.parent
CLIENTS_DIR = ROOT / "agent" / "adk_clients"
DEFAULT_AGENT_URL = "http://127.0.0.1:9999"
DEFAULT_CARD_URL = f"{DEFAULT_AGENT_URL}/.well-known/agent-card.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8002
DEFAULT_MODEL = "gemini-3.6-flash"


def howto_text(
    *,
    agent_url: str = DEFAULT_AGENT_URL,
    card_url: str = DEFAULT_CARD_URL,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> str:
    ui = f"http://{host}:{port}"
    return f"""Google ADK 是本機 A2A peer client（有大腦、讀 Card、委派 task）。
Inspector（:5001）仍是除錯 UI；Cursor 不講 A2A。Gemini key 請自己設，本命令不代填。

兩個 terminal：

  # 1. A2A server（保持開著；練習加 --dry-run）
  uv run --extra a2a --extra stock --extra ui python main.py a2a --dry-run --date 2026-08-18

  # 2. 專案根目錄 .env（已 gitignore）自己放：
  #    GOOGLE_API_KEY=...
  #    GOOGLE_GENAI_USE_VERTEXAI=false
  #    亦可用 GEMINI_API_KEY，launcher 會轉成 GOOGLE_API_KEY。

  # 3. ADK Web
  uv run --extra a2a python main.py a2a-adk

瀏覽器開 {ui}，選 agent stock_a2a。
第一句：2330 要不要動
不要丟「幫我處理今天持股」（agy 多輪、慢）。

ADK 會 GET {card_url}，再 POST {agent_url.rstrip('/')}/ SendMessage。
廚房仍是 run_agent；ADK 不呼叫 fetch_chips。

只印這段說明、不啟動 UI：python main.py a2a-adk --howto
client 專案（獨立 uv，不進本 repo extra）：{CLIENTS_DIR}
"""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _load_root_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


def apply_gemini_key_alias() -> None:
    """ADK reads GOOGLE_API_KEY. Accept GEMINI_API_KEY without copying into files."""
    if (os.environ.get("GOOGLE_API_KEY") or "").strip():
        return
    gemini = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if gemini:
        os.environ["GOOGLE_API_KEY"] = gemini


def has_gemini_key() -> bool:
    _load_root_env()
    apply_gemini_key_alias()
    return bool((os.environ.get("GOOGLE_API_KEY") or "").strip())


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    print("+ " + " ".join(argv), file=sys.stderr)
    completed = subprocess.run(argv, cwd=cwd, env=env)
    return int(completed.returncode)


def _adk_web_argv(*, host: str, port: int, skip_install: bool) -> list[str] | None:
    uv = _which("uv")
    if uv is None:
        print("ERROR: 需要 uv（跟本 repo 一樣）。", file=sys.stderr)
        return None
    argv = [uv, "run", "--project", str(CLIENTS_DIR)]
    if skip_install:
        argv.append("--frozen")
    argv.extend(
        [
            "adk",
            "web",
            "stock_a2a",
            "--host",
            host,
            "--port",
            str(port),
        ]
    )
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python main.py a2a-adk",
        description="Run a Google ADK peer agent against python main.py a2a.",
    )
    parser.add_argument(
        "--howto",
        action="store_true",
        help="只印兩個 terminal 的接法，不啟動 ADK Web。",
    )
    parser.add_argument(
        "--agent-url",
        default=DEFAULT_AGENT_URL,
        help="A2A server base URL（預設 http://127.0.0.1:9999）。",
    )
    parser.add_argument(
        "--card-url",
        default="",
        help="Agent Card URL。預設 {agent-url}/.well-known/agent-card.json。",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="ADK Web bind host（預設 127.0.0.1，不要改成 0.0.0.0）。",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="ADK Web port（預設 8002，避開 MCP inspector :8000）。",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Gemini model id（預設 gemini-3.6-flash；亦可用 ADK_MODEL）。",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="已 uv lock 過時，凍結依賴、不上網。",
    )
    args = parser.parse_args(argv)

    agent_url = args.agent_url.rstrip("/")
    card_url = (args.card_url or f"{agent_url}/.well-known/agent-card.json").strip()
    print(
        howto_text(
            agent_url=agent_url,
            card_url=card_url,
            host=args.host,
            port=args.port,
        )
    )
    if args.howto:
        return EXIT_OK

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "ERROR: ADK Web 只綁 loopback。CVE-2026-4810 修過仍不要對 LAN 開放。",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS

    if not (CLIENTS_DIR / "pyproject.toml").is_file():
        print(f"ERROR: 找不到 {CLIENTS_DIR / 'pyproject.toml'}。", file=sys.stderr)
        return EXIT_FAILED

    if not has_gemini_key():
        print(
            "ERROR: 未設定 GOOGLE_API_KEY（或 GEMINI_API_KEY）。"
            "請自己寫進專案根目錄 .env，本命令不代填。",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS

    if not (os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") or "").strip():
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"

    adk_argv = _adk_web_argv(host=args.host, port=args.port, skip_install=args.skip_install)
    if adk_argv is None:
        return EXIT_BAD_ARGS

    env = os.environ.copy()
    env["STOCK_A2A_CARD_URL"] = card_url
    env["ADK_MODEL"] = (args.model or DEFAULT_MODEL).strip()
    print(f"A2A ADK Web {args.host}:{args.port}", file=sys.stderr)
    print(f"Agent Card {card_url}", file=sys.stderr)
    print("第一句：2330 要不要動", file=sys.stderr)
    return _run(adk_argv, cwd=CLIENTS_DIR, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
