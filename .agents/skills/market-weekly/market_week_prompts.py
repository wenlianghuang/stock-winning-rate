"""agy prompt for the Taiwan weekly market report.

Writing rules live in the weekly-narrative skill. This module only attaches
the facts and news for the current run.
"""

from __future__ import annotations

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parent.parent / "weekly-narrative" / "SKILL.md"


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
    if "## 五、下週情境推演" not in body:
        raise ValueError(f"{_SKILL_PATH} 缺少下週情境章節規格")
    return body


def build_market_week_prompt(
    *,
    facts_summary: str,
    news_section: str,
) -> str:
    news_block = (
        news_section.strip()
        if news_section.strip()
        else "（本週未提供可用新聞；交叉段請寫「本週無可對帳新聞」並改做結構交叉）"
    )
    return (
        "這是「台股市場週報」任務：依系統確定性 facts 撰寫本週總評與下週展望。\n"
        "類股來自證交所官方類股指數，不是個人持股觀察池。\n"
        "讀者最關心的是假日無法交易、卻要為週一做準備——因此「下週情境推演」必須寫得完整、可執行。\n"
        "交叉解讀為情境鋪陳；數字儀表板已另有 UI，勿把一～三寫成 facts 朗讀。\n"
        "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
        f"=== 系統市場週事實（facts）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"=== 新聞（未驗證軟脈絡；標題須原文引用）===\n{news_block}\n"
        f"=== 新聞結束 ===\n\n"
        f"{narrative_rules()}\n"
    )


def build_fix_prompt(*, base_prompt: str, issues: list[str]) -> str:
    bullet = "\n".join(f"- {item}" for item in issues)
    return (
        f"{base_prompt}\n\n"
        "上一版未通過驗證，請依下列問題修正後重寫完整正文：\n"
        f"{bullet}\n"
        "特別注意：若問題與情境／週一／否決／排序有關，請優先加厚第五章，勿只改關鍵字。\n"
    )
