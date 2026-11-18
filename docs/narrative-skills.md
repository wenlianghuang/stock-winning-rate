# 敘事規格改由 Antigravity skill 承載

這次改的是 **agy 怎麼拿到寫作規格**。每一輪才算得出來的事實、配額、驗證，仍由 Python 組好再交出去。

日常產報的命令沒有改。要改某一份報告怎麼寫，改對應的正文 skill，不用再改 Python 字串。

---

## 1. 為什麼要拆

呼叫 agy 的形狀一直是同一種三明治：固定文案、這一輪的資料、再 `agy -p`。固定文案以前寫在 Python 常數裡（`FORMAT_RULES`、`ANALYSIS_FORMAT_RULES` 這一類），每一輪都原樣貼進 prompt。

`.agents/skills/*/SKILL.md` 裡原本那一批是 **怎麼跑 gate**：命令、cutover、exit code、產物路徑。它們給人和其他程式代理看，agy 寫正文時並不讀它們。刪掉那些檔，產報流程不受影響，因為規格在 Python 字串裡。

Antigravity skill 適合管的是「每輪都一樣的寫法」：章節、禁語、篇幅、語氣。`name` 和 `description` 讓 agy 在互動時判斷要不要讀這份 skill。單次列印（`agy -p`）不會自己去打開 skill，所以產報時仍由 Python 把正文讀進來貼上。prompt 裡加一句「規格已附在下方，不要再讀 skill 檔」，避免模型再去開檔，把工作摘要混進報告。

---

## 2. 一輪怎麼組

```text
Python 算出這一輪的 facts／新聞／配額
        │
        ▼
讀敘事 skill，去掉 frontmatter 和第一行標題
        │
        ▼
任務說明 + 寫作規格 + 這一輪的資料
        │
        ▼
agy -p  →  Markdown 正文
        │
        ▼
Python 驗證。沒過就把問題清單接在同一份 prompt 後面再寫一輪
```

讀檔時會檢查 frontmatter 有沒有收好，以及該份報告的代表章節還在不在。缺了就直接失敗，不會送出一份沒有章節規格的 prompt。

第二輪把問題清單接在同一份規格後面。個股、部位、投組、美股日報的修正 prompt 是另外組的，所以會再貼一次同一份 skill，不再另抄一份章節清單。

驗證仍以 Python 為準。skill 裡寫「至少引用 2 條 anchors」是在告訴寫稿的人；判過沒過的還是 gate。兩邊各改各的，就會出現 skill 放行、gate 打回。

---

## 3. 兩種 skill

| 種類 | 檔名 | 誰在讀 | 內容 |
|---|---|---|---|
| 正文 | `*-narrative/SKILL.md` | 組 prompt 的 Python，以及互動中的 agy | 章節、禁語、篇幅 |
| 操作 | 原本的 `market-daily`、`report-gate` 等 | 人、排程、其他代理 | 怎麼跑、產物在哪 |

操作 skill 各加了一句指向正文 skill。跑 gate 的說明留在原處。

| 報告 | 正文 skill | 組 prompt | 操作 skill |
|---|---|---|---|
| 收盤日報 | `close-narrative` | `market-close/close_prompts.py` | `market-close` |
| 開盤前 brief | `daily-narrative` | `market-daily/market_day_prompts.py` | `market-daily` |
| 市場週報 | `weekly-narrative` | `market-weekly/market_week_prompts.py` | `market-weekly` |
| 個股深度報告 | `stock-narrative` | `ui/stock_prompts.py` | `report-gate` |
| 持股部位 | `position-narrative` | `ui/position_prompts.py` | `position-gate` |
| 新手組合 | `portfolio-narrative` | `ui/portfolio_prompts.py` | （投組沒有操作 skill） |
| 主題袖口 | `theme-narrative` | `ui/portfolio_prompts.py` | 同上，依 `mode=theme` 選這份 |
| 題材新聞 | `concept-narrative` | `concept-news/process_report.py` | `concept-news` |
| 美股科技簡報 | `us-tech-narrative` | `us-tech-news/process_report.py` | `us-tech-news` |

新手組合和主題袖口是兩份 skill。同一輪只貼其中一份，避免袖口報告吃到新手紀律，或反過來。

美股簡報仍要讀 prompt 裡給的那一個 `raw.json`。這句寫在 `us-tech-narrative` 裡。其他報告的 facts 是貼進 prompt 的，模型不必開檔。

---

## 4. 仍留在 Python 的部分

這些每輪都在變，或是程式在查表，不進正文 skill：

- facts、新聞原文、事件表、指數行、持股均價與張數
- 題材新聞的引用則數、空事件通知、與前一日是「違逆」還是「順從」
- 部位情境的百分比。skill 只寫「百分比照系統給的數字，不可改」；48%、30%、22% 這種權重是系統算的
- `validate_*.py` 發出的問題代碼，以及 `FIX_HINT_BY_CODE` 那組對人話。句子可以跟寫作規格放在一起想，查表仍在 gate
- 開盤前 `market_day_chat.py` 和個股 `stock_report_chat.py` 的問答人設。多數事實題走模板，長尾走 Ollama。問答流程沒有改成由 agy skill 現查現答

---

## 5. 改寫法時看哪

改章節、禁語、篇幅：改上表的正文 skill。下一輪產報就會帶進 prompt。

改數字怎麼算、什麼情況算沒過、命令與排程：改對應的 Python 和操作 skill。不要把驗證門檻只寫進正文 skill。
