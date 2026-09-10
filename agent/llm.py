"""Phase 4 LLM adapter: narrative backends are swappable (agy / Ollama).

Business logic stays in tools and gates. Orchestrator and skills call `complete()`
and do not import agy or Ollama directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Literal

from agent.tools._paths import ROOT, ensure_paths

BackendName = Literal["agy", "ollama"]

AGY_TIMEOUT_SEC = 900
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"


def resolved_backend() -> BackendName:
    raw = os.environ.get("LLM_BACKEND", "agy").strip().lower()
    if raw in ("ollama", "agy"):
        return raw  # type: ignore[return-value]
    raise ValueError(f"不支援的 LLM_BACKEND={raw!r}（允許 agy / ollama）")


def complete(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> str:
    """Return model text for a single prompt. Side effects stay in tools."""
    backend = resolved_backend()
    if backend == "ollama":
        return complete_ollama(prompt, timeout_sec=timeout_sec)
    return complete_agy(prompt, timeout_sec=timeout_sec)


def complete_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> str:
    ensure_paths()
    from agy_output import agy_output_usable, clean_agy_output

    custom = os.environ.get("AGY_BIN", "").strip()
    agy_bin = custom or shutil.which("agy")
    if not agy_bin:
        raise FileNotFoundError("找不到 agy 指令。請安裝 Antigravity CLI 或設定 AGY_BIN。")

    try:
        result = subprocess.run(
            [
                agy_bin,
                "-p",
                prompt,
                "--dangerously-skip-permissions",
                "--print-timeout",
                "15m",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"agy 逾時（>{timeout_sec}s）") from exc

    raw = result.stdout or result.stderr or ""
    body = clean_agy_output(raw)
    if not agy_output_usable(body, min_chars=20):
        detail = body[:200] if body else "(空)"
        raise RuntimeError(f"agy 輸出不可用（exit {result.returncode}）：{detail}")
    return body


def complete_ollama(
    prompt: str,
    *,
    timeout_sec: int = AGY_TIMEOUT_SEC,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    base = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip(
        "/"
    )
    model_name = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    url = f"{base}/api/chat"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Ollama HTTP {exc.code}（model={model_name}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"無法連線 Ollama（{base}）。請確認已啟動 ollama serve，"
            f"並設定 OLLAMA_BASE_URL / OLLAMA_MODEL。原因：{exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Ollama 逾時（>{timeout_sec}s）") from exc

    message = data.get("message") if isinstance(data, dict) else None
    content = ""
    if isinstance(message, dict):
        content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("Ollama 回傳空白內容")
    return content
