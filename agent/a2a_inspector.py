"""Launch the official A2A Inspector as a debug client (not vendored).

CLI: ``python main.py a2a-inspector``. Same role as MCP Inspector: a UI that
talks the protocol. Cursor is not an A2A client; this is the closest analog.

The inspector lives in a gitignored clone of
https://github.com/a2aproject/a2a-inspector — we do not copy its UI into
this repo. Your A2A server must already be running (``python main.py a2a``).
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
CACHE_DIR = ROOT / ".cache" / "a2a-inspector"
INSPECTOR_REPO = "https://github.com/a2aproject/a2a-inspector.git"
INSPECTOR_HOST = "127.0.0.1"
INSPECTOR_PORT = 5001
DEFAULT_AGENT_URL = "http://127.0.0.1:9999"


def howto_text(*, agent_url: str = DEFAULT_AGENT_URL) -> str:
    inspector_url = f"http://{INSPECTOR_HOST}:{INSPECTOR_PORT}"
    return f"""A2A Inspector 是外部 debug client（對 MCP Inspector），不是本系統的一部分。
Cursor 不會掛 A2A；請用 Inspector 連本機 server。

兩個 terminal：

  # 1. A2A server（保持開著；真跑不要加 --dry-run）
  uv run --extra a2a --extra stock --extra ui python main.py a2a

  # 2. Inspector UI
  uv run --extra a2a python main.py a2a-inspector

瀏覽器開 {inspector_url}
Connect Agent URL 填：{agent_url}
第一句話用：2330 要不要動
不要丟「幫我處理今天持股」（agy 多輪、慢）。

只印這段說明、不啟動 UI：python main.py a2a-inspector --howto
官方 repo：{INSPECTOR_REPO}
本機 clone（gitignore）：{CACHE_DIR}
"""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    print("+ " + " ".join(argv), file=sys.stderr)
    completed = subprocess.run(argv, cwd=cwd, env=env)
    return int(completed.returncode)


def _ensure_clone() -> int:
    git = _which("git")
    if git is None:
        print("ERROR: 需要 git 才能 clone A2A Inspector。", file=sys.stderr)
        return EXIT_BAD_ARGS
    CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
    if (CACHE_DIR / ".git").is_dir():
        return EXIT_OK
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    code = _run(
        [git, "clone", "--depth", "1", INSPECTOR_REPO, str(CACHE_DIR)],
        cwd=ROOT,
        env=env,
    )
    if code != 0:
        print("ERROR: clone a2a-inspector 失敗。", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def _ensure_deps() -> int:
    uv = _which("uv")
    npm = _which("npm")
    if uv is None:
        print("ERROR: 需要 uv（跟本 repo 一樣）。", file=sys.stderr)
        return EXIT_BAD_ARGS
    if npm is None:
        print("ERROR: 需要 Node.js / npm 才能建 Inspector frontend。", file=sys.stderr)
        return EXIT_BAD_ARGS
    code = _run([uv, "sync"], cwd=CACHE_DIR)
    if code != 0:
        return EXIT_FAILED
    frontend = CACHE_DIR / "frontend"
    code = _run([npm, "ci"], cwd=frontend)
    if code != 0:
        return EXIT_FAILED
    return _run([npm, "run", "build"], cwd=frontend)


def _run_backend() -> int:
    uv = _which("uv")
    if uv is None:
        print("ERROR: 需要 uv。", file=sys.stderr)
        return EXIT_BAD_ARGS
    backend = CACHE_DIR / "backend"
    if not (backend / "app.py").is_file():
        print(f"ERROR: 找不到 {backend / 'app.py'}，請刪 .cache/a2a-inspector 再試。", file=sys.stderr)
        return EXIT_FAILED
    print(f"A2A Inspector UI {INSPECTOR_HOST}:{INSPECTOR_PORT}", file=sys.stderr)
    print(f"Connect Agent URL {DEFAULT_AGENT_URL}", file=sys.stderr)
    print("第一句：2330 要不要動", file=sys.stderr)
    return _run(
        [
            uv,
            "run",
            "--",
            "uvicorn",
            "app:app",
            "--host",
            INSPECTOR_HOST,
            "--port",
            str(INSPECTOR_PORT),
        ],
        cwd=backend,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python main.py a2a-inspector",
        description="Run the official A2A Inspector against python main.py a2a.",
    )
    parser.add_argument(
        "--howto",
        action="store_true",
        help="只印兩個 terminal 的接法，不 clone、不開 UI。",
    )
    parser.add_argument(
        "--agent-url",
        default=DEFAULT_AGENT_URL,
        help="印在說明裡的 A2A server URL（預設 http://127.0.0.1:9999）。",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="已 clone 且 frontend 建過時，跳過 uv sync / npm。",
    )
    args = parser.parse_args(argv)

    print(howto_text(agent_url=args.agent_url.rstrip("/")))
    if args.howto:
        return EXIT_OK

    code = _ensure_clone()
    if code != EXIT_OK:
        return code
    if not args.skip_install:
        code = _ensure_deps()
        if code != EXIT_OK:
            return code
    return _run_backend()


if __name__ == "__main__":
    raise SystemExit(main())
