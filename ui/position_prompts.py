"""Shared agy prompt instructions for position-aware stock analysis."""

from __future__ import annotations

from dataclasses import dataclass

POSITION_ANALYSIS_FORMAT_RULES = """
輸出格式（Markdown）：
- **不要**重複列出 CSV 籌碼數字（系統會自動在報告前段插入籌碼表格與部位摘要表）
- **市場面**須依 CSV 與（若有）市場觀察報告理解趨勢，但正文以文字摘要，勿複製籌碼表格
- 分析須同時參考：**當日快照 CSV**、**歷史 CSV**、**區間摘要欄位**、**系統提供的部位資料**

## 一、部位現況
**純文字**：先概述總持股與加權均價；若有**現股**與**融資**兩腿，須分開說明各腿股數、均價、未實現損益意義（數字由系統表格提供，正文勿重複列表）；
有融資時須點出槓桿／追繳風險意識。

## 二、市場面摘要
**純文字** 3～5 句：綜合當日與近 N 日籌碼趨勢（延續/轉折/背離）與新聞方向；
須依 facts 說明**收盤相對 MA5 與 MA20（月線）**位置與**短中線技術面對齊**（短線與月線是否一致）；
若 facts 提供 **RSI（14日）**，須說明動能是否過熱/偏弱；
若 facts 提供 **ATR 波動**，須說明波動是否偏高/偏低，並對停損/停利紀律的影響；
若 facts 提供 **ADX 趨勢強度**，須說明趨勢是否明確或易震盪，並對操作情境權重的意義；
若 facts 提供 **券資比** 或 **融資動能**，須說明信用戶多空結構與散戶槓桿變化；
若 facts 提供**大盤脈絡（加權指數）**，須點出大盤趨勢與個股相對大盤是強於/弱於/同步；
若系統提供市場觀察報告路徑，可引用其結論，但勿整段複製。

## 三、部位與市場交叉對照
**純文字** 2～4 點條列（`-` 開頭）：說明市場訊號對**現股／融資各腿**的意義（獲利緩衝、套牢風險、加減碼空間等）；
須區分「個股自身問題」與「大盤系統性風險」；**不要用表格**。

## 四、操作情境推演
**純文字** 結構如下：
1. **綜合市場情境權重**（加總 100%）：依系統**綜合部位**給定的三種情境（延續調節／橫盤整理／技術反彈），百分比勿改。
   三種情境標題必須各自獨立成行，且以 Markdown 三級標題寫出，格式固定為：
   `### 主線：延續調節（48%）`
   `### 次線：橫盤整理（30%）`
   `### 尾線：技術反彈（22%）`
   （標籤與百分比以系統給定為準；標題勿包在列表裡。）
   每個標題下方再用 `-` 條列：觸發條件、建議操作方向、現股部位防禦手段（若有）、融資部位防禦手段（若有）、綜合操作處置。
2. **現股**（若有現股）：專段標題含「現股」，對齊現股損益分桶：
   - 大幅獲利 → 停利/移動停損/獲利了結或續抱理由 + 停利參考價
   - 小幅獲利 → 加碼條件/停利/回吐
   - 損益兩平 → 出場或加碼觸發價
   - 虧損 → 停損/減碼 + 停損參考價與解套參考
3. **融資**（若有融資）：專段標題含「融資」，對齊融資損益分桶，並談追繳／斷頭／維持率或融資減碼；不宜無前提攤平。
   若系統提供**融資維持率／距追繳／追繳價／壓力分桶**，須在融資段或風險段**引用系統數字**（可四捨五入到整數），不可自行改寫或發明不同維持率；
   壓力為 tight/critical 時必須點出追繳線或距追繳，且不可把「技術反彈」標成主線。
4. **綜合結論**：依系統**綜合優先序**說明先處理現股或融資、兩者如何取捨（現股與融資同時存在時必寫）。
不可寫保證漲跌、目標價或「一定買/賣」；**不要用表格**。正文稱「融資」即可，勿寫「融資腿」。

## 五、風險與紀律提醒
**純文字** 編號清單（2～4 項）：部位過大、沉沒成本、停損紀律、與大盤連動等；
若有**融資**，須至少一項談追繳／斷頭／維持率或融資減碼，並在系統有試算時引用維持率或距追繳；**不要用表格**。

## 六、免責聲明
一句話免責（非投資建議）。
""".strip()


@dataclass(frozen=True)
class HoldingInfo:
    stock_id: str
    avg_cost: float
    shares: int
    note: str = ""
    uses_margin: bool = False
    cash_shares: int = 0
    cash_avg_cost: float | None = None
    margin_shares: int = 0
    margin_avg_cost: float | None = None


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
    position_facts_summary: str | None = None,
) -> str:
    pnl_note = (
        f"綜合未實現損益（系統試算）：{unrealized_pnl_pct:+.2f}%"
        if unrealized_pnl_pct is not None
        else "綜合未實現損益：無法試算（收盤價缺失）"
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

    holding_lines = [
        f"總持股：{holding.shares:,} 股",
        f"加權均價：{holding.avg_cost} 元/股",
        price_note,
        pnl_note,
    ]
    if holding.cash_shares > 0 and holding.cash_avg_cost is not None:
        holding_lines.append(
            f"現股腿：{holding.cash_shares:,} 股 × 均價 {holding.cash_avg_cost} 元"
        )
    else:
        holding_lines.append("現股腿：無")
    if holding.margin_shares > 0 and holding.margin_avg_cost is not None:
        holding_lines.append(
            f"融資腿：{holding.margin_shares:,} 股 × 均價 {holding.margin_avg_cost} 元"
        )
    else:
        holding_lines.append("融資腿：無")
    if holding.note.strip():
        holding_lines.append(f"備註：{holding.note.strip()}")
    holding_note = "\n".join(holding_lines) + "\n"

    position_state_block = (
        f"{position_facts_summary}\n\n" if position_facts_summary else ""
    )

    has_cash = holding.cash_shares > 0
    has_margin = holding.margin_shares > 0
    leg_rules: list[str] = []
    if has_cash and has_margin:
        leg_rules.append(
            "- 現股與融資**兩腿都有**：部位現況與操作情境須分述「現股」「融資」，"
            "並寫「綜合結論」對齊系統優先序；融資段須引用系統維持率／距追繳（若有）\n"
        )
    elif has_margin:
        leg_rules.append(
            "- 僅融資腿：操作情境與風險須談追繳／斷頭／維持率或融資減碼；"
            "若系統有維持率／距追繳試算須引用數字；不宜無前提攤平\n"
        )
    else:
        leg_rules.append("- 僅現股腿：勿虛構融資追繳／斷頭情境\n")

    return (
        f"這是「單檔持股部位決策」任務：{stock_name}（{stock_id}）。\n"
        f"系統已完成籌碼資料的**確定性計算**，下方 facts 為市場面唯一事實來源；"
        f"請結合**現股／融資分腿部位資料**與新聞撰寫**部位決策正文**（Markdown），"
        f"市場面方向不可與 facts 矛盾，操作情境須對齊系統試算的各腿狀態與綜合優先序。\n\n"
        f"=== 系統籌碼事實（facts）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"{market_note}\n"
        f"## 系統提供的部位資料\n\n"
        f"{holding_note}\n"
        f"{position_state_block}"
        f"{news_section}\n\n"
        f"{POSITION_ANALYSIS_FORMAT_RULES}\n\n"
        "其他要求：\n"
        "- 市場面方向（外資買/賣、站上/跌破 MA5 與 MA20 月線）須與籌碼 facts 一致\n"
        "- 市場面須說明收盤相對 MA20（月線）位置與短中線對齊，勿只寫 MA5\n"
        "- 部位現況須依系統試算的 MA20（月線）與各腿均價關係撰寫\n"
        "- 市場面分析須客觀，勿因套牢或獲利而扭曲籌碼解讀\n"
        "- 綜合市場情境權重須與系統**綜合部位**百分比一致（勿改）\n"
        "- 若系統提供停損/停利參考價（近20日低/高），相關腿的操作情境須明確引用\n"
        f"{''.join(leg_rules)}"
        "- 新聞只能引用上方內容，不可臆造\n"
        "- 完整正文印在 stdout，不要只寫入檔案\n"
        "- 不要加「工作摘要」或工具操作說明\n"
        "- 不要寫「跨股票對照」章節\n"
    )
