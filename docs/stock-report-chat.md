# 個股報告提問（Grounded Chat）

報告頁「報告提問」讓使用者用自然話追問**已完成**的個股深度報告。回答必須 grounded：只能用報告 artifacts（籌碼 facts、summary、新聞、持股／部位），禁止編造數字、禁止直接下單指令。

實作主檔：`.agents/skills/report-gate/stock_report_chat.py`  
前端：`stock-report-site` 的 `StockReportChat` + `POST /api/reports/[id]/chat`  
後端串流：`POST /stock/{stock_id}/chat/stream`（`api/stock_api.py`）  
工具／MCP：`answer_report_chat`

與開盤前日報 chat（`market_day_chat.py`）同思路，但資料源是**單檔報告**，不是 market-daily brief。

---

## 1. 設計原則

1. **意圖先分流，模板優先。** 籌碼事實、名詞解釋、損益、新聞、推論、進場／部位紀律，能用規則 + 模板答的不要先丟給 LLM。
2. **LLM 是潤飾與長尾，不是事實來源。** Ollama 不可達、模型名不對、或超時時，必須能落回 deterministic 模板。
3. **沒有的資料要明說。** 不要用「外資／主力總覽」敷衍無關問題。
4. **數字以 facts／summary 為準。** Markdown 敘事只當語境；使用者口述的張數若和報告差一個數量級，要先對帳。

---

## 2. 資料從哪來

| 來源 | 典型欄位 | 用途 |
|------|----------|------|
| `tw_stock_{id}.facts.json` | `foreign_net_lots`、`major_net_lots`、均線、RSI… | 籌碼／技術事實槽 |
| `tw_stock_{id}.summary.json` → `market.news` | 標題、分類、摘要 | 營收／消息類新聞 grounding |
| 同上 → `position` | `avg_cost`、`shares`、`unrealized_pnl_pct` | 持股損益 |
| `tw_stock_{id}.position.facts.json` | 成本、收盤、未實現％ | 同上（前端常沒傳，後端可補載） |
| holdings（網站持股／報告快照） | `avg_cost`、`share_count` | 損益與部位模板 |
| Markdown | 新聞表、敘事摘錄 | 新聞補齊；LLM 語境（數字仍以 facts 為準） |

前端 chat route 會帶 `facts`、`summary`、`markdown`、`holdings`；`position_facts` 可能缺。`prepare_stock_report_chat` 在缺部位 artifacts 時會依 `stock_id` + `trade_date` 嘗試從 disk 補載。

---

## 3. 請求路徑

```
報告頁 StockReportChat
   │  POST /api/reports/{id}/chat  { message, history }
   ▼
stock-report-site（組 facts / summary / holdings）
   │  POST /stock/{stockId}/chat/stream
   ▼
stock_api → iter_stock_report_chat_events
   │
   ├─ prepare_stock_report_chat → 意圖 + 模板／Ollama plan
   ├─ 確定答案：SSE meta → token（paced）→ done
   └─ Ollama：先 probe；失敗或空白 → 同一套 fallback 模板
```

SSE 事件：`meta`（intent／source）、`token`（增量文字）、`done`（完整 reply）、`error`。

---

## 4. 意圖分類（`classify_intent`）

優先順序大致如下（先匹配先生效）：

| Intent | 典型問法 | 回答策略 |
|--------|----------|----------|
| `entry_advice` | 能不能買、要不要進場 | 進場紀律模板；可選 LLM 潤飾 |
| `position_advice` | 持股怎麼辦、小損出場、獲利多少 | 部位／損益模板；**損益強制模板、不走 LLM** |
| `inference` | 外資會不會繼續賣、你覺得明天會… | 推論模板（證據 ≠ 預測）；可選 LLM |
| `glossary` | 什麼是借券／當沖比 | 名詞庫 + 本檔數字 |
| `fundamental_qa` | 本益比、殖利率、月營收年增多少 | 問的當下用報告基準日查 FinMind 單檔；數字不走 LLM |
| `news_qa` | 九月營收出來了嗎、最近新聞 | 掃報告新聞；有就引用，沒有就明說 |
| `out_of_scope` | （目前無固定題） | 報告沒有、也沒有即時查詢時才拒絕 |
| `reading` | 為什麼、代表什麼、風險在哪 | 整包 facts 交給 LLM 解讀；掛了則多欄位模板。單欄「多少」不走這條 |
| `factual_qa` | 今天外資／主力／RSI／支撐 | 事實槽模板（deterministic） |
| `open_qa` | 其餘 | LLM；fallback **不再**倒籌碼總覽敷衍 |

重要規則：

- 「你覺得他們**會**繼續賣」→ `inference`，不要被「你覺得」做成部位模板。
- 「外資賣了…接下來**該怎麼處理**」→ `position_advice`。
- 「持有還是小損出場」「你建議我…」→ `position_advice`（勿掉 `open_qa`）。
- 「獲利多少／未實現損益」→ `position_advice` + `build_pnl_reply`。
- 「為什麼外資在賣」→ `reading`，不要被「外資」做成單日張數。買賣方向或張數和報告不符時，先糾正再解讀。
- 「為什麼會繼續賣」仍是 `inference`（預測鎖模板）。「本益比多少／為什麼偏高」是 `fundamental_qa`，先給基準日數字。「九月營收出來了嗎」仍是 `news_qa`。
- 接上一句時，短問句沿用上一句的主題。沒有改問數字、預測、進出場，也沒有換主題，就解釋那個組合；「所以呢」「嚴重嗎」「這是什麼意思」不必各自對到固定句型。換主題（「那主力呢」）或完整新問題則維持原本的問法。

---

## 5. 各路徑怎麼答

### 5.1 事實槽（`factual_qa`）

對應 `FACTUAL_SLOT_PATTERNS`（外資、主力、融資券、當沖、借券、均線、RSI、ATR、20 日高低、籌碼型態）。  
回覆短、口語、帶 1～2 個關鍵數字，結尾免責。

### 5.1a 基本面（`fundamental_qa`）

本益比、股價淨值比、殖利率、月營收年增用報告 `trade_date` 呼叫 `load_or_fetch_fundamentals`（單檔）。當天快取有這檔就用，沒有才打 FinMind。沒有基準日不查。查失敗明說沒查到，不改聊籌碼。回覆只含問到的欄位；問配息時註明這是殖利率，不是每股金額。「營收出來了嗎」不走這條。

### 5.2a 解讀（`reading`）

「為什麼」先把點名的欄位放在一起讀。同一主題的接續問句改解釋這個組合：意思、嚴不嚴重、正不正常、好壞，用問句裡看得到的角度，不要求固定說法。換主題或改問張數、預測、進出場時不走這段解釋。Ollama 可用時由模型寫；寫出 facts 沒有的數字，或把買賣方向說反，整段改回模板。使用者把買說成賣，或張數差一個數量級，第一句先對帳。

### 5.2 推論（`inference`）

回答「會不會延續」：先講無法斷言，再用今日淨額、區間累計、連買連賣當依據。

- 使用者口述張數與 `foreign_net_lots` 差很多（例如 2.1 萬 vs 21.2 萬）→ 先對數字。
- 單日絕對值 > 區間累計（同向）→ 補一句：前幾天有相反方向，把累計抵掉一些。
- 重複追問類似預測 → 換說法前綴，避免一字不差複讀。

### 5.3 部位與損益（`position_advice`）

- **損益問句**：從 `summary.position`／position facts／holdings 組 snapshot（成本、股數、收盤、未實現％與金額）。金額 = `(close - cost) * shares`。註明是報告基準日試算，不是券商即時帳戶。
- **持有 vs 出場**：給防守位框架（近 20 日低／月線、外資賣壓），不替使用者下最後一刀。

### 5.4 新聞（`news_qa`）

1. `extract_report_news(summary, markdown)` 收集標題。  
2. 依問題關鍵字（營收、九月、財報、法說…）過濾。  
3. **有命中** → 引用標題（來源 badge：`news`）。  
4. **有新聞但沒命中** →「這批新聞沒直接提到…」，並帶 1～2 則實際主題（例如擴產／設備）。  
5. **完全沒新聞** → 才說報告這次沒附新聞摘要。

不要把「沒有結構化營收欄位」說成「報告裡完全看不到新聞」。

### 5.5 Open QA fallback

對不到欄位時：明說對不到，引導改問外資／主力／均線／損益。  
**禁止**再用「籌碼偏 mixed，外資賣超…你比較想問什麼？」當萬用答案。

---

## 6. Ollama 與模型

- 預設名 `llama3.1`；本機若只有 `llama3.2:3b` 等，靠 `resolve_ollama_model()` 從 `/api/tags` 挑已安裝模型。
- TCP 通不代表模型可用：錯誤名會 404，舊邏輯會一直掉模板 → 看起來「怎麼問都同一句」。
- Chat 串流有短 timeout；失敗則 paced 輸出 fallback，前端仍可逐字顯示。

環境變數：`OLLAMA_BASE_URL`、`OLLAMA_MODEL`、`STOCK_CHAT_OLLAMA_TIMEOUT_SEC`。

---

## 7. 前端注意

- `StockReportChat` 送 `message` + 最近 history；依 `reportId` 清空對話。
- 畫面上的來源標籤用白話：報告數字、名詞說明、持股判斷、依現況說明、怎麼解讀、基本面、報告新聞、報告沒有這項、口語整理。內部 source 代號不變。模型成功時標籤是「口語整理」；Ollama 失敗才落「怎麼解讀」。基本面數字的標籤是「基本面」。回覆不把 bearish、bullish 這類代號念出來。
- 網站必須把 **summary（含 news、position）** 傳給 agent；只傳 facts 會讓新聞／損益變弱。

---

## 8. 常見坑（本輪踩過的）

| 現象 | 原因 | 修法方向 |
|------|------|----------|
| 2.1 萬／21 萬問同一句 | Ollama 模型名錯 → 永遠 inference 模板；模板不對使用者數字 | `resolve_ollama_model` + lot claim grounding |
| 持有還是出場 → 踢皮球 | 意圖漏網 → `open_qa` 舊 fallback | 補 position patterns；改 open fallback |
| 獲利多少答籌碼 | 意圖漏網；沒用 `summary.position`／`shares` | PnL 意圖 + `extract_position_snapshot` |
| 九月營收 → 說報告沒營收／或倒籌碼 | 沒掃 `market.news`；或硬 out_of_scope | `news_qa` 先搜新聞再拒絕 |
| 單日賣超 21 萬、累計只 8 萬「矛盾」 | 累計含前幾日買超沖淡，數學可成立 | 回覆加一句解釋，勿當資料錯 |

---

## 9. 怎麼測、怎麼擴

```bash
# 單元測試
uv run python -m unittest tests.test_stock_report_chat -v

# 本機 API（網站 chat 依賴）
uv run --extra server --extra ui --extra stock python main.py api
```

擴充建議：

1. **新事實槽**：在 `FACTUAL_SLOT_PATTERNS` + `build_factual_slot_reply` 加一組；先寫測試再接 LLM。  
2. **新新聞主題**：加進 `NEWS_GROUNDED_TOPICS` 與 `_news_query_keywords`。  
3. **新部位用語**：補 `POSITION_PATTERNS`／`ACTION_OVERRIDE_PATTERNS`，並確認不會蓋過 `inference`。  
4. **月營收「出來了嗎」**仍走新聞標題。年增數字已走 `fundamental_qa`。

相關文件：[`agent-roadmap.md`](./agent-roadmap.md)（Grounded chat 定位）、[`commands.md`](./commands.md)（API／技能入口）、開盤前 chat 實作對照 `.agents/skills/market-daily/market_day_chat.py`。
