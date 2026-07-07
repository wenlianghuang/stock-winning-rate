"""Shared agy prompt instructions for single-stock analysis (tables + no chip repeat)."""

from __future__ import annotations

ANALYSIS_FORMAT_RULES = """
輸出格式（Markdown）：
- **不要**重複列出 CSV 籌碼數字（系統會自動在報告前段插入籌碼表格，含當日、區間摘要與歷史明細）
- **數字與清單用表、解釋與推理用文字**（見各章節說明）
- 分析須同時參考：**當日快照 CSV**、**歷史 CSV（逐日）**、**區間摘要欄位**（累計買賣超、MA5、區間漲跌幅等）

## 一、當日籌碼解讀
**純文字** 2～4 句，解讀當日法人/主力/融資券/當沖方向與意義（勿重複列數字、勿用表格）。

## 二、近 N 日籌碼趨勢解讀
**純文字** 3～5 句，依歷史 CSV 與區間摘要判斷：
- 法人/主力是**延續**、**轉折**還是**背離**？
- 融資券餘額變化趨勢、當沖佔比是否異常？
- 股價相對 MA5 與區間漲跌幅的技術位置
（勿重複列數字、勿用表格）

## 三、近期新聞與事件
**必須使用 Markdown 表格**（此章節唯一必填表格）：
| 日期 | 標題 | 分類 | 摘要 |
（分類：利多 / 利空 / 中性；僅能引用提供的新聞）

## 四、籌碼與新聞交叉對照
**純文字** 2～4 點條列（`-` 開頭），結合**當日與近 N 日趨勢**說明籌碼與新聞是否一致或背離；**不要用表格**。

## 五、短中線情境推演（1～3 個交易日）
**純文字** 2～3 種情境，每種用一小段或條列，須含「觸發條件」與「可觀察訊號」；
須納入近 N 日趨勢背景，**不要用表格**；不可寫保證漲跌或目標價。

## 六、觀察重點
**純文字** 編號清單（3～5 項），每項一句話；須含趨勢延續/轉折的觀察指標；**不要用表格**。

## 七、免責聲明
一句話免責。
""".strip()


def build_single_stock_analysis_prompt_suffix(
    *,
    stock_name: str,
    stock_id: str,
    csv_path: str,
    history_csv_path: str | None = None,
    news_section: str,
) -> str:
    history_note = (
        f"歷史 CSV 路徑：{history_csv_path}\n"
        if history_csv_path
        else "歷史 CSV：未取得（請僅依快照 CSV 的區間摘要欄位分析趨勢，並註明缺少逐日明細）。\n"
    )
    return (
        f"這是「單檔個股」分析任務：{stock_name}（{stock_id}）。\n"
        f"請先讀取**當日快照 CSV**與**歷史 CSV**理解籌碼，結合下方新聞撰寫**分析正文**（Markdown）。\n"
        f"快照 CSV 路徑：{csv_path}\n"
        f"{history_note}\n"
        f"{news_section}\n\n"
        f"{ANALYSIS_FORMAT_RULES}\n\n"
        "其他要求：\n"
        "- 新聞只能引用上方內容，不可臆造\n"
        "- 趨勢判斷須有歷史/區間依據，避免僅依單日數據下結論\n"
        "- 完整正文印在 stdout，不要只寫入檔案\n"
        "- 不要加「工作摘要」或工具操作說明\n"
        "- 不要寫「跨股票對照」章節\n"
    )
