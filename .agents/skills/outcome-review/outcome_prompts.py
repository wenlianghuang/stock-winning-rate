"""agy prompt for the outcome postmortem.

Writing rules live in the outcome-narrative skill. This module only attaches
the scored table for the current run.
"""

from __future__ import annotations

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parent.parent / "outcome-narrative" / "SKILL.md"


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
    if "## 有方向的判斷" not in body:
        raise ValueError(f"{_SKILL_PATH} 缺少事後對帳章節規格")
    return body


def build_outcome_prompt(*, table: str) -> str:
    return (
        "這是「個股判斷事後對帳」任務：用一般人看得懂的句子說明，"
        "判斷日當時的方向，後來這段是比大盤強還是比大盤弱。"
        "結算日不是新的方向。\n"
        "輸出必須是完整 Markdown 正文（含三個章節標題），不要前言寒暄。\n"
        "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
        f"{narrative_rules()}\n\n"
        "======= 對帳表（唯讀）=======\n"
        f"{table.strip()}\n"
        "======= END =======\n"
    )


def build_fix_prompt(*, base_prompt: str, issues: list[str]) -> str:
    issue_block = "\n".join(f"- {line}" for line in issues) or "- （未提供細節）"
    return (
        f"{base_prompt.rstrip()}\n\n"
        "======= 驗證未通過，請修正 =======\n"
        "請保留三個章節，修正下列問題後重寫完整短文：\n"
        f"{issue_block}\n"
        "======= END =======\n"
    )
