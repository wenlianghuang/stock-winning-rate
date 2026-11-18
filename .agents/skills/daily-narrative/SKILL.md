---
name: daily-narrative
description: >-
  Writes the Taiwan pre-open brief from facts the caller already computed.
  Use when asked to draft or revise that five-section open narrative.
  Do not fetch market data, run market-daily, or judge whether validation passed.
  Overnight futures do not change bias_hint.
---

# 開盤前正文

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
若 tx_night.available：再加 1 點，引用近月台指期盤後相對前一日一般交易時段結算價的漲跌點數與漲跌%。那指可用時，用 facts.tx_night.alignment_vs_ixic 的 **一致** 或 **背離**。
時段只許使用 facts.tx_night.session_window（營業日 15:00 至次日曆日 05:00；週一那列是週五 15:00–週六 05:00，不是週日夜盤）。
夜盤漲跌是開盤缺口，不得改寫 bias_hint，也不得寫成開盤保證。
若 tx_night.available 為 false：寫「台指夜盤資料不足」。

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
若 tx_night.available：其中 1 項為夜盤相對前一日結算價的漲跌 → 若開盤跳空延續或回補 → 較支持／否決哪一情境。
禁止保證獲利或下單指令。

## 五、免責聲明
一句話免責：僅供參考，不構成投資建議。
