"""agy prompt for the Taiwan close report.

Writing rules live in the close-narrative skill. This module only attaches
the facts for the current run.
"""

from __future__ import annotations

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parent.parent / "close-narrative" / "SKILL.md"


def narrative_rules() -> str:
    """Return the skill body. Frontmatter and the title stay out of the prompt."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{_SKILL_PATH} 缺少 frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{_SKILL_PATH} 的 frontmatter 沒有結束")
    body = text[end + 4 :].strip()
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        body = "\n".join(lines[1:]).strip()
    if "## 一、今日收盤" not in body:
        raise ValueError(f"{_SKILL_PATH} 缺少收盤章節規格")
    return body


def build_close_prompt(*, facts_summary: str) -> str:
    return (
        "這是「台股收盤日報」任務：依系統確定性 facts 說明當天大盤、權值法人與當天新聞。\n"
        "輸出必須是完整 Markdown 正文（含一～四章標題），不要前言寒暄。\n"
        "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
        f"{narrative_rules()}\n\n"
        "======= FACTS（唯讀）=======\n"
        f"{facts_summary.strip()}\n"
        "======= END FACTS =======\n"
    )


def build_fix_prompt(*, base_prompt: str, issues: list[str]) -> str:
    issue_block = "\n".join(f"- {line}" for line in issues) or "- （未提供細節）"
    return (
        f"{base_prompt.rstrip()}\n\n"
        "======= 驗證未通過，請修正 =======\n"
        "請保留章節結構，修正下列問題後重寫完整報告：\n"
        f"{issue_block}\n"
        "======= END =======\n"
    )
