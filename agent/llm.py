"""Narrative calls.

`invoke_agy` is the only agy subprocess: timeout, output cleanup, and CLI
flags live here. Report gates call it directly and keep the process exit
code. `complete()` still selects agy or Ollama for callers that opt in
via `LLM_BACKEND` (digest, peer reading).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

from agent.tools._paths import ROOT, ensure_paths

BackendName = Literal["agy", "ollama"]

AGY_TIMEOUT_SEC = 900
# Gates already reject bodies shorter than agy_output_usable's default.
GATE_MIN_CHARS = 120
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"


def list_ollama_models(
    base_url: str | None = None,
    *,
    timeout_sec: float = 1.5,
) -> list[str]:
    """Return installed Ollama model names (best-effort)."""
    base = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    req = urllib.request.Request(f"{base}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            names.append(name)
    return names


def resolve_ollama_model(
    base_url: str | None = None,
    preferred: str | None = None,
) -> str:
    """Pick an installed model; fall back to preferred name if tags unavailable."""
    preferred_name = (
        preferred or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    ).strip()
    installed = list_ollama_models(base_url)
    if not installed:
        return preferred_name
    if preferred_name in installed:
        return preferred_name
    pref_base = preferred_name.split(":")[0]
    for name in installed:
        if name.startswith(f"{preferred_name}:") or name.split(":")[0] == pref_base:
            return name
    for name in installed:
        if name.startswith("llama3"):
            return name
    return installed[0]


@dataclass(frozen=True)
class AgyResult:
    text: str
    exit_code: int


def agy_executable() -> str:
    custom = os.environ.get("AGY_BIN", "").strip()
    agy_bin = custom or shutil.which("agy")
    if not agy_bin:
        raise FileNotFoundError("找不到 agy 指令。請安裝 Antigravity CLI 或設定 AGY_BIN。")
    return agy_bin


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


def invoke_agy(
    prompt: str,
    *,
    timeout_sec: int = AGY_TIMEOUT_SEC,
    min_chars: int = 20,
) -> AgyResult:
    """Run one agy prompt. Callers that need a longer body pass min_chars."""
    ensure_paths()
    from agy_output import agy_output_usable, clean_agy_output

    agy_bin = agy_executable()
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
    if not agy_output_usable(body, min_chars=min_chars):
        detail = body[:200] if body else "(空)"
        raise RuntimeError(f"agy 輸出不可用（exit {result.returncode}）：{detail}")
    return AgyResult(text=body, exit_code=result.returncode)


def complete_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> str:
    return invoke_agy(prompt, timeout_sec=timeout_sec).text


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
    model_name = resolve_ollama_model(base_url=base, preferred=model)
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
