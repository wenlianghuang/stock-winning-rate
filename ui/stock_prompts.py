"""Shared agy prompt instructions for single-stock analysis (narrative only; no chip tables).

Writing rules live in the stock-narrative skill. This module only attaches
the facts and news for the current run.
"""

from __future__ import annotations

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parent.parent / ".agents" / "skills" / "stock-narrative" / "SKILL.md"


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
    if "## 一、當日籌碼解讀" not in body:
        raise ValueError(f"{_SKILL_PATH} 缺少當日籌碼章節規格")
    return body


def build_single_stock_analysis_prompt_suffix(
    *,
    stock_name: str,
    stock_id: str,
    facts_summary: str,
    news_section: str,
) -> str:
    return (
        f"這是「單檔個股」分析任務：{stock_name}（{stock_id}）。\n"
        f"系統已完成籌碼資料的**確定性計算**，下方 facts 即是唯一事實來源；"
        f"請**依 facts 撰寫分析正文**（Markdown），不要自行臆測與 facts 相反的方向。\n"
        "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
        f"=== 系統籌碼事實（facts）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"{news_section}\n\n"
        f"{narrative_rules()}\n"
    )
