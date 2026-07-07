"""Clean and normalize raw agy CLI stdout for report artifacts."""

from __future__ import annotations

import re
from pathlib import Path

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ANSI_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
WORK_SUMMARY_RE = re.compile(
    r"\n\s*#{0,6}\s*(?:本次)?工作摘要\b.*",
    re.DOTALL | re.IGNORECASE,
)
SPINNER_LINE_RE = re.compile(r"^[\s⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷\-\\|/]+$")


def normalize_pty_newlines(text: str) -> str:
    """Normalize CRLF and keep the final segment after spinner-style \\r overwrites."""
    text = text.replace("\r\n", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        lines.append(line)
    return "\n".join(lines)


def strip_ansi(text: str) -> str:
    text = ANSI_OSC_RE.sub("", text)
    text = ANSI_ESCAPE_RE.sub("", text)
    return normalize_pty_newlines(text)


def clean_agy_output(raw: str) -> str:
    """Remove terminal noise and agent footers; keep report body."""
    text = strip_ansi(raw)
    text = WORK_SUMMARY_RE.sub("", text)

    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if SPINNER_LINE_RE.match(stripped):
            continue
        if stripped.startswith("Tip:") or stripped.startswith("Loaded "):
            continue
        kept.append(line.rstrip())

    collapsed: list[str] = []
    blank_run = 0
    for line in kept:
        if not line.strip():
            blank_run += 1
            if blank_run <= 2:
                collapsed.append("")
            continue
        blank_run = 0
        collapsed.append(line)

    return "\n".join(collapsed).strip()


def agy_output_usable(raw: str, *, min_chars: int = 120) -> bool:
    cleaned = clean_agy_output(raw)
    if len(cleaned) >= min_chars:
        return True
    # Fallback: if PTY normalization alone yields enough text, accept it.
    normalized = normalize_pty_newlines(strip_ansi(raw)).strip()
    return len(normalized) >= min_chars


def output_lengths(raw: str) -> tuple[int, int]:
    """Return (raw_char_count, cleaned_char_count)."""
    stripped = raw.strip()
    cleaned = clean_agy_output(raw)
    return len(stripped), len(cleaned)


def write_bg_agy_raw_log(log_dir: Path, job_id: str, raw: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"bg_agy_{job_id}.raw.log"
    log_path.write_text(raw, encoding="utf-8")
    return log_path


CHAT_DISPLAY_MAX_CHARS = 500

ARTIFACT_PATH_RE = re.compile(
    r"(?:Markdown|PDF|摘要|報告|輸出(?:檔案)?)[：:]\s*(?P<path>\S+\.(?:md|pdf))",
    re.IGNORECASE,
)
BARE_REPORT_PATH_RE = re.compile(
    r"(?P<path>(?:reports/)?\S+/(?:\S+/)*(?:\S+_(?:summary|report)|tw_stock_\d+)\.(?:md|pdf))",
    re.IGNORECASE,
)


def extract_artifact_paths(text: str) -> list[Path]:
    """Find report file paths mentioned in agent output."""
    seen: set[str] = set()
    paths: list[Path] = []
    for pattern in (
        ARTIFACT_PATH_RE,
        BARE_REPORT_PATH_RE,
        re.compile(r"(?P<path>\S+\.(?:md|pdf))"),
    ):
        for match in pattern.finditer(text):
            raw = match.group("path").strip().strip("`'\"")
            if raw in seen:
                continue
            seen.add(raw)
            path = Path(raw)
            if path.exists():
                paths.append(path)
    return paths


def brief_chat_text(text: str, *, max_chars: int = CHAT_DISPLAY_MAX_CHARS) -> str:
    """Truncate long agent output for chat; keep short replies intact."""
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped

    cut = stripped[:max_chars]
    for boundary in ("\n\n", "\n", "。"):
        idx = cut.rfind(boundary)
        if idx > max_chars // 2:
            cut = cut[: idx + len(boundary)]
            break

    suffix = "…（內容已省略，請查閱 Markdown 或 PDF 檔案。）"
    artifact_paths = extract_artifact_paths(stripped)
    if artifact_paths:
        path_lines = "\n".join(f"- {path}" for path in artifact_paths[:3])
        suffix = f"…（完整內容請查閱檔案）\n\n{path_lines}"
    return cut.rstrip() + "\n\n" + suffix
