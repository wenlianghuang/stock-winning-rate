"""Shared agy prompt instructions for position-aware stock analysis."""

from __future__ import annotations

from dataclasses import dataclass

POSITION_ANALYSIS_FORMAT_RULES = """
輸出格式（Markdown）：
- **不要**重複列出 CSV 籌碼數字（系統會自動在報告前段插入籌碼表格與部位摘要表）
- **市場面**須依 CSV 與（若有）市場觀察報告理解趨勢，但正文以文字摘要，勿複製籌碼表格
- 分析須同時參考：**當日快照 CSV**、**歷史 CSV**、**區間摘要欄位**、**系統提供的部位資料**

## 一、部位現況
**純文字** 2～4 句：說明均價、股數、未實現損益%、現價相對均價與 MA5 的意義（數字由系統表格提供，正文勿重複列表）。

## 二、市場面摘要
**純文字** 3～5 句：綜合當日與近 N 日籌碼趨勢（延續/轉折/背離）與新聞方向；
若系統提供市場觀察報告路徑，可引用其結論，但勿整段複製。

## 三、部位與市場交叉對照
**純文字** 2～4 點條列（`-` 開頭）：說明市場訊號對**這筆部位**的意義（獲利緩衝、套牢風險、加減碼空間等）；**不要用表格**。

## 四、操作情境推演
**純文字** 2～4 種情境，每種須明確標示方向（例如：觀望 / 減碼 / 加碼 / 停損 / 獲利了結），
且各含「觸發條件」與「可觀察訊號」；須納入部位成本背景；**不要用表格**；
不可寫保證漲跌、目標價或「一定買/賣」。

## 五、風險與紀律提醒
**純文字** 編號清單（2～4 項）：部位過大、沉沒成本、停損紀律、與大盤連動等；**不要用表格**。

## 六、免責聲明
一句話免責（非投資建議）。
""".strip()


@dataclass(frozen=True)
class HoldingInfo:
    stock_id: str
    avg_cost: float
    shares: int
    note: str = ""


def build_position_analysis_prompt_suffix(
    *,
    stock_name: str,
    stock_id: str,
    facts_summary: str,
    holding: HoldingInfo,
    unrealized_pnl_pct: float | None,
    close_price: float | None,
    news_section: str,
    market_report_path: str | None,
) -> str:
    pnl_note = (
        f"未實現損益（系統試算）：{unrealized_pnl_pct:+.2f}%"
        if unrealized_pnl_pct is not None
        else "未實現損益：無法試算（收盤價缺失）"
    )
    price_note = (
        f"收盤價（CSV）：{close_price}"
        if close_price is not None
        else "收盤價：未取得"
    )
    market_note = (
        f"市場觀察報告（可引用摘要，勿複製全文）：{market_report_path}\n"
        if market_report_path
        else "市場觀察報告：尚無（請直接依 facts 與新聞撰寫市場面摘要）。\n"
    )
    holding_note = (
        f"持股均價：{holding.avg_cost} 元/股\n"
        f"持股股數：{holding.shares:,} 股\n"
        f"{price_note}\n"
        f"{pnl_note}\n"
    )
    if holding.note.strip():
        holding_note += f"備註：{holding.note.strip()}\n"

    return (
        f"這是「單檔持股部位決策」任務：{stock_name}（{stock_id}）。\n"
        f"系統已完成籌碼資料的**確定性計算**，下方 facts 為市場面唯一事實來源；"
        f"請結合**部位資料**與新聞撰寫**部位決策正文**（Markdown），"
        f"市場面方向不可與 facts 矛盾。\n\n"
        f"=== 系統籌碼事實（facts）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"{market_note}\n"
        f"## 系統提供的部位資料\n\n"
        f"{holding_note}\n"
        f"{news_section}\n\n"
        f"{POSITION_ANALYSIS_FORMAT_RULES}\n\n"
        "其他要求：\n"
        "- 市場面方向（外資買/賣、站上/跌破 MA5）須與 facts 一致\n"
        "- 市場面分析須客觀，勿因套牢或獲利而扭曲籌碼解讀\n"
        "- 操作情境須具體說明對**這筆部位**的意義，而非泛泛而談\n"
        "- 新聞只能引用上方內容，不可臆造\n"
        "- 完整正文印在 stdout，不要只寫入檔案\n"
        "- 不要加「工作摘要」或工具操作說明\n"
        "- 不要寫「跨股票對照」章節\n"
    )
