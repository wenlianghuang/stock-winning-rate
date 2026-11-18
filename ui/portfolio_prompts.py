"""Shared agy prompt instructions for portfolio narratives.

Writing rules live in portfolio-narrative and theme-narrative. This module
only attaches the facts for the current run. The deterministic holdings table
is inserted by the report harness; agy only writes the narrative.
"""

from __future__ import annotations

from pathlib import Path

_SKILLS = Path(__file__).resolve().parent.parent / ".agents" / "skills"
_PORTFOLIO_SKILL = _SKILLS / "portfolio-narrative" / "SKILL.md"
_THEME_SKILL = _SKILLS / "theme-narrative" / "SKILL.md"


def narrative_rules(path: Path, marker: str) -> str:
    """Return the skill body. Frontmatter and the title stay out of the prompt."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path} 缺少 frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{path} 的 frontmatter 沒有結束")
    body = text[end + 4 :].strip()
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        body = "\n".join(lines[1:]).strip()
    if marker not in body:
        raise ValueError(f"{path} 缺少章節規格：{marker}")
    return body


def _rules_for(mode: str) -> str:
    if mode == "theme":
        return narrative_rules(_THEME_SKILL, "## 五、部位紀律")
    return narrative_rules(_PORTFOLIO_SKILL, "## 五、新手紀律")


def build_portfolio_analysis_prompt_suffix(
    *,
    profile_label: str,
    risk_label: str,
    facts_summary: str,
    mode: str = "beginner",
) -> str:
    rules = _rules_for(mode)
    if mode == "theme":
        return (
            f"這是一份「主題投資組合（袖口）」的撰寫任務。\n"
            f"系統已依規則**確定性地**在主題候選池完成選股與配權重，下方 facts 即唯一事實來源；"
            f"請**依 facts 撰寫說明正文**（Markdown），"
            f"不要自行更動權重、金額或加入未列出的股票。\n"
            "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
            f"組合名稱：{profile_label}（{risk_label}）\n\n"
            f"=== 系統組合事實（facts）===\n{facts_summary}\n"
            f"=== facts 結束 ===\n\n"
            f"{rules}\n"
        )

    return (
        f"這是一份「新手投資組合建議」的撰寫任務。\n"
        f"系統已依規則**確定性地**完成選股與配權重，下方 facts 即唯一事實來源；"
        f"請**依 facts 撰寫給新手看的說明正文**（Markdown），"
        f"不要自行更動權重、金額或加入未列出的股票。\n"
        "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
        f"風險屬性：{profile_label}（{risk_label}）\n\n"
        f"=== 系統組合事實（facts）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"{rules}\n"
    )


def build_portfolio_fix_prompt(
    *,
    profile_label: str,
    risk_label: str,
    facts_summary: str,
    previous_body: str,
    issues_block: str,
    mode: str = "beginner",
) -> str:
    rules = _rules_for(mode)
    spec = (
        "寫作規格如下，不要再讀 skill 檔。依該規格與下列問題重寫完整正文。\n\n"
        f"{rules}\n\n"
    )
    if mode == "theme":
        return (
            f"上一版「{profile_label}主題組合」未通過自動驗證。\n"
            f"請依下列問題修正後，輸出完整新版正文到 stdout（Markdown）。\n\n"
            f"組合名稱：{profile_label}（{risk_label}）\n\n"
            f"=== 系統組合事實（facts，權重/角色/持股以此為準）===\n{facts_summary}\n"
            f"=== facts 結束 ===\n\n"
            f"驗證問題（含修正指引）：\n{issues_block}\n\n"
            f"{spec}"
            "上一版全文：\n"
            f"{previous_body.strip()}\n"
        )

    return (
        f"上一版「{profile_label}投資組合建議」未通過自動驗證。\n"
        f"請依下列問題修正後，輸出完整新版正文到 stdout（Markdown）。\n\n"
        f"風險屬性：{profile_label}（{risk_label}）\n\n"
        f"=== 系統組合事實（facts，權重/角色/持股以此為準）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"驗證問題（含修正指引）：\n{issues_block}\n\n"
        f"{spec}"
        "上一版全文：\n"
        f"{previous_body.strip()}\n"
    )
