"""agy prompt instructions for Taiwan market daily open brief."""

from __future__ import annotations

FORMAT_RULES = """
輸出格式（Markdown）：
- 只能使用下方 facts；不可發明未列出的數字、點位或標的
- 數字方向（漲跌、買超／賣超、量能放縮、美股日報酬）必須與 facts 一致
- 不可寫保證漲跌、目標價、穩賺、一定漲
- 不可用美股敘事推翻台股 facts 的方向數字
- **全文宜精簡（約 800～1200 字）**；重心在「三、明日開盤偏誤」與「四、開盤儀表板」
- bias_hint 若為 neutral，不得強行看多或看空

## 一、今日結構
**純文字** 2～4 句：加權日報酬、收在當日區間位置、量能（相對五日均量／regime）、三大法人共識與外資方向；可一句帶台積電。
禁止把完整表格重念一遍。

## 二、外盤改寫
若 us.available：用 1～3 點 `-` 條列，引用那指／費半日報酬 + 台股／台積電對應數字，使用 facts.alignment 的 **一致** 或 **背離**。
美股 session 以 facts.us.as_of／indices.*.session_date 為準（台北 05:30 cutover：過了用最新收盤，沒到用前一日）。
若 us.available 為 false：寫「美股指數資料不足」。
Phase 1 無台指夜盤：可一句註明「本版未納入台指夜盤」。

## 三、明日開盤偏誤（核心）
讀者情境：依 trade_date 收盤籌碼，為 **for_session** 開盤做準備。
必須標明偏誤方向（偏多／偏空／中性），且不得與 facts.bias_hint 明顯矛盾：
- bias_hint=bullish → 偏多（可寫中性偏多）
- bias_hint=bearish → 偏空（可寫中性偏空）
- bias_hint=neutral → 必須中性／觀望語氣

必含：
1. **基準情境**（最可能）：結構連貫、觸發條件、開盤含義、可追蹤訊號、**否決**條件
2. **尾部風險**（次要）：結構連貫、觸發、開盤含義、訊號、**否決**條件

## 四、開盤儀表板
條列至少 4 項：「觀察什麼 → 若出現 → 較支持／否決哪一情境」。
禁止保證獲利或下單指令。

## 五、免責聲明
一句話免責：僅供參考，不構成投資建議。
""".strip()


def build_market_day_prompt(
    *,
    facts_summary: str,
) -> str:
    return (
        "這是「台股開盤前戰術 brief」任務：依系統確定性 facts 撰寫短評與明日開盤偏誤。\n"
        "這不是市場週報縮小版；不要寫類股排行長文或新聞長篇。\n"
        "輸出必須是完整 Markdown 報告本文（含一～五章標題），不要前言寒暄。\n\n"
        f"{FORMAT_RULES}\n\n"
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
