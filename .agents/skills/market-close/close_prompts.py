"""agy prompt for the Taiwan close report."""

from __future__ import annotations

FORMAT_RULES = """
輸出格式（Markdown 正文，不要再寫最上層標題）：
- 只能使用下方 facts。不可發明數字、點位、標的或新聞
- 漲跌與買超／賣超方向必須與 facts 一致
- 權值法人單位是張，全市場法人用 facts 裡已經換算好的億元
- 寫「尚未公布」的欄位不可補張數或金額
- 不可寫融資、融券、當沖、主力，除非該詞就在一則必須原文引用的標題裡
- 不可寫 0050、006208
- 不可寫保證漲跌、目標價、穩賺、一定漲
- 這不是開盤前日報：不可寫「明日開盤偏誤」

## 一、今日收盤
2～4 句：加權日報酬、收在當日區間的位置、量能相對五日均量、全市場三大法人方向。
收盤價或全市場法人若是尚未公布，就直接寫尚未公布。

## 二、權值法人
七檔都要點名。有數字的寫外資、投信、自營買賣超張數。沒有數字的寫法人尚未公布。
用這些張數說明今天是誰在拉、誰在拖，不要改寫成買賣建議。

## 三、新聞怎麼對上
有標題時，至少原文引用一則，解釋它和今天收盤的關係。不可改寫標題。
沒有標題時，只寫「沒有對得上的收盤新聞」。

## 四、免責聲明
一句話：僅供參考，不構成投資建議。
""".strip()


def build_close_prompt(*, facts_summary: str) -> str:
    return (
        "這是「台股收盤日報」任務：依系統確定性 facts 說明當天大盤、權值法人與當天新聞。\n"
        "輸出必須是完整 Markdown 正文（含一～四章標題），不要前言寒暄。\n\n"
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
