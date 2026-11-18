---
name: us-tech-narrative
description: >-
  Writes the US tech slide deck from the raw file and index lines the caller
  already prepared. Use when asked to draft or revise those seven slides.
  Do not fetch RSS, run the news pipeline, or judge whether validation passed.
  Read only the raw file named in the prompt.
---

# 美股科技簡報正文

輸出格式（每個主題一頁 slide，以單獨一行的 --- 分隔）：
## 市場概況
- 重點 bullet
---
## 那斯達克與費城半導體指數解讀
- 重點 bullet
---
## 半導體與 AI 基建
- 重點 bullet
---
## 大型科技動態
- 重點 bullet
---
## 跨主題連動分析
- 重點 bullet
---
## 值得後續關注
- 重點 bullet
---
## 新聞來源
- 標題（來源 feed）

要求：
- 必須先讀取原始資料檔再分析，不可臆測未收錄的新聞。只讀 prompt 給的那一個原始資料路徑
- 每頁 slide 以 ## 標題開頭，正文用 - bullet 列點（每點 1～2 句）
- 僅根據原始資料中的新聞與指數，客觀中立，不提供買賣建議
- 若某主題在原始資料中資訊不足，請明確說明
- 完成初稿後自我檢查：指數數字、新聞事實是否與原始資料一致，修正後再輸出最終版
- 完整報告必須印在 stdout，不要只寫入檔案
- 不要加「工作摘要」或工具操作說明
- 「新聞來源」slide 列出實際引用的新聞標題與來源 feed
