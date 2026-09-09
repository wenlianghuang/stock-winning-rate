# Agent 精進路線

把本專案從「人觸發的產報 pipeline」升級成「會自己呼叫工具、交接、受權限約束的台股作業 Agent」。

網站（`stock-report-site`）當導入層，STT（`AI_Speech/stt`）當語音 tool。本文件只規劃 `stock-winning-rate` 本體；相鄰 repo 的接點寫在最後一節。

命令速查見 [`commands.md`](./commands.md)。

---

## 1. 為什麼改這裡、而不是重開專案

現有系統已經具備企業 Agent 最難補的兩塊：

1. **真實證券作業**：籌碼抓取、個股報告、持股部位、投組、開盤前日報／週報、Email digest。
2. **harness + 閉環**：Python 算 `facts.json`，agy 只寫敘事，gate 用 `issue_codes` 打回修正（最多 N 輪）。

缺的不是「更好看的報告」，而是 **Agent 形態**：

| 現況 | 目標 |
|------|------|
| `main.py` / FastAPI 依序呼叫 script | Orchestrator 依意圖決定要跑哪些 tool |
| skills 是 CLI 與 HTTP 工作流 | 同一組能力以 MCP tool 暴露 |
| report / position / daily 各跑各的 | specialist agents 交接（A2A） |
| 人按按鈕才跑 | 收盤後可排程；寄信／發布要核准 |
| agy 呼叫帶 `--dangerously-skip-permissions` | 工具白名單、審計 log、人機臨界點 |

原則：

- **不重寫 gate。** `report-gate`、`position-gate`、`portfolio-gate`、`market-daily`、`market-weekly` 的驗證邏輯是核心資產，改成被呼叫的 tool。
- **不另做無關 Agent 框架。** 領域與失敗案例都在這條產品線上。
- **下一階段不加「新的報告章節」。** 除非它是某個 tool 的必要輸出。
- **LLM 可替換。** 業務邏輯在 tool 與驗證層；agy / Ollama / 其他模型只是 backend。

---

## 2. 現況盤點

### 已有（保留）

| 能力 | 入口 | 產物 |
|------|------|------|
| 籌碼抓取 | `tw-stock-report/fetch_chip_report.py` | `reports/stock/{日期}/tw_stock_{代碼}.csv` |
| 個股深度報告閉環 | `report-gate/report_gate.py` | `.md` / `.facts.json` / `.gate.log` |
| 持股部位閉環 | `position-gate/position_gate.py` | `_position.md` / `.position.facts.json` |
| 投組規則 + 敘事 | `portfolio-gate/` | `reports/portfolio/` |
| 開盤前日報／週報 | `market-daily/`、`market-weekly/` | `reports/market/` |
| Grounded chat | `market_day_chat.py`、`POST /market-daily/chat/stream` | facts 優先、長尾才 LLM |
| HTTP 工作流 | `api/stock_api.py` | `/jobs`、`/digest`、portfolio／market jobs |
| 品質回饋 | `tools/gate_stats.py`、`outcome-label`、`calibrate` | 校準與事後標籤 |

`pyproject.toml` 已有 `mcp` extra（`mcp>=1.28,<2`），尚未實作 server。

### 缺（本路線要補）

- MCP tool 介面（依賴已宣告、程式沒有）
- Orchestrator：自然語言／結構化意圖 → 選 tool、填參數、處理失敗
- 多 Agent 交接，而不是一支 script 串完
- 權限：誰能跑什麼、寄信／發布是否要人確認
- 審計：每次 tool call 的輸入、輸出摘要、耗時、結果
- 排程型「類 RPA」：收盤後自動跑 daily → 持股報告 → digest（核准後才寄）

---

## 3. 目標架構

```
使用者（網站／CLI／語音）
        │
        ▼
┌───────────────────┐
│ Orchestrator      │  意圖解析、tool 選擇、失敗重試、人機臨界點
│ （本 repo 新層）   │
└─────────┬─────────┘
          │ MCP
    ┌─────┴──────────────────────────────┐
    ▼          ▼           ▼             ▼
 Data        Research    Position     Notify
 Agent       Agent       Agent        Agent
    │          │           │             │
    ▼          ▼           ▼             ▼
 fetch_chips  report-gate position-gate send_digest
 build_facts  daily/weekly portfolio    （核准後）
              validate-*
```

同一組 tool 三種入口共用：Orchestrator、現有 FastAPI、未來 Copilot Studio custom connector。  
**不要**為 Microsoft 棧重做整套台股邏輯。

角色對應現有程式（先當邏輯邊界，不一定要六個 process）：

| Agent | 現有模組 | 職責 |
|-------|----------|------|
| Data | `tw-stock-report`、`chip_signals`、`market_day_signals` | 抓資料、算 facts；不寫敘事 |
| Research | `report-gate`、`market-daily`、`market-weekly`、`us-tech-news` | 產報 + 格式／事實／推理 gate |
| Position | `position-gate`、`portfolio-gate` | 有持倉／有資金才跑；部位層驗證 |
| Validator | `validate_*.py`、`fact_checks`、`reasoning_checks` | 可被 Research／Position 呼叫，也可獨立退回 |
| Notify | `/digest`、report-site Email | 預設需核准 |
| Orchestrator | **新** | 只編排，不算籌碼、不寫長文 |

---

## 4. 分階段

每階段都要能獨立演示。做完再進下一階段，不要平行開很多空殼。

### Phase 0 — 收斂介面（先做、範圍小）

落地說明（概念、改了什麼、為何 `commands.md` 看起來沒變）見 [`Phase0.md`](./Phase0.md)。

把「可被 Agent 呼叫」的能力收成穩定 Python function，CLI 與 API 都走這層。現在 `stock_api.py` 大量 `subprocess` 呼叫 script，MCP 若再包一層 script 會更脆。

建議模組：`agent/tools/`（名稱可再定，重點是單一實作）。

第一批 tool（對應現有命令，不發明新業務）：

| Tool | 對應現況 | 副作用 |
|------|----------|--------|
| `get_last_trading_date` | `GET /last-trading-date` | 無 |
| `fetch_chips` | `stock-report` | 寫 CSV |
| `build_chip_facts` | `chip_signals.build_chip_facts` | 寫 `.facts.json` |
| `run_report_gate` | `report-gate` | 寫 md／gate log |
| `run_position_gate` | `position-gate` | 寫 position 產物 |
| `run_market_daily` | `market-daily` | 寫日報 |
| `get_holdings` | holdings.json／API 持倉欄 | 無 |
| `answer_market_chat` | `market_day_chat` | 無（或只讀） |
| `draft_digest` | `POST /digest` 的 agy 融合 | 無（只產草稿） |
| `send_digest` | report-site 寄信 | **需核准** |

完成定義：

- CLI（`main.py`）與 FastAPI 至少有一條路徑（建議 `/jobs`）改走 function，不再為同一件事維護兩套參數組裝。
- 每個 tool 有 typed 輸入／輸出（Pydantic 或 dataclass），錯誤碼沿用現有 exit code 語意（agy 缺失、CSV 不存在等）。
- 現有 tests 仍過；新 tool 層補單元測試（至少 facts／date／缺 CSV）。

### Phase 1 — MCP server

落地說明（概念、expose 哪些 tools、如何用 inspector／最小 client 跑 2330）見 [`Phase1.md`](./Phase1.md)。

`pyproject.toml` 的 `mcp` extra 在這裡落地。

- `python main.py mcp`（或同等入口）啟動 MCP server，expose Phase 0 的 tools。
- 每個 tool 的 description 寫清楚：何時用、需要哪些前置（例如 `run_report_gate` 需要 CSV）。
- 用 MCP inspector 或一個最小 client 跑通：`fetch_chips(2330)` → `run_report_gate(2330)`。
- FastAPI **不必**刪除；網站仍走 HTTP。MCP 是給 Agent 的介面。

完成定義：Cursor／Claude／其他 MCP client 能列出 tools 並成功跑完「一檔沒有持股的深度報告」。

### Phase 2 — Orchestrator（自主決策）

落地說明（意圖分類、policy、CLI、golden fixtures）見 [`Phase2.md`](./Phase2.md)。

使用者不再指定 `/gate` 或 `/position`，只給意圖。

例子：

- 「幫我處理今天持股」→ 讀 holdings → 缺 CSV 就 fetch → 每檔 report-gate → 有均價／張數才 position-gate → digest 草稿（不寄）。
- 「2330 要不要動」→ 有持倉走 position，沒有只走 research，並說明為什麼沒跑部位。
- 「明天開盤怎麼看」→ `run_market_daily` 或讀既有日報 → `answer_market_chat`。

Orchestrator 規則（寫成可測的 policy，不要全交給模型）：

1. 缺資料先 Data，再 Research。
2. 無持倉不跑 Position。
3. gate 失敗則把 `issue_codes` 交回同一 specialist，直到通過或達 `max_rounds`。
4. `send_digest` 預設 blocked，只產生草稿與「待核准」狀態。
5. 單次 run 有 tool budget（次數／時間），避免無限互叫。

LLM 在這一層只做：意圖 → 結構化 plan（tool 序列 + 參數）。  
Plan 通過 schema 驗證後才執行。執行失敗用規則決定 retry 或停。

完成定義：

- CLI：`python main.py agent -- "幫我處理今天持股"`。
- 印出 plan、每步 tool、gate 通過與否。
- 至少 3 個 golden 意圖有 fixture 測試（有持倉／無持倉／只問路況）。

### Phase 3 — 多 Agent 交接與權限

把 Phase 2 的內部步驟顯式化成交接，方便講 A2A，也方便審計。

- Research 失敗 → Validator 產出 issue list → Research 再跑（現有 loop 的多角色版）。
- Position 開始前必須有 Research 的 facts（或明確 `skip_research`）。
- Notify 只收「已通過 gate」的成品。

權限與審計（對職缺「安全防護與權限控管」）：

| 機制 | 行為 |
|------|------|
| Tool allowlist | 依角色：例如 chat 不得 `send_digest` |
| Human gate | `send_digest`、未來若有「改 holdings」皆需核准 |
| Audit log | JSONL：`run_id`、actor、tool、args 摘要、result、elapsed_ms |
| 禁止自動下單 | 本系統輸出研究／部位情境，**永不**接券商下單 API |

完成定義：一次「處理持股」run 可重放 audit log；關掉 `send_digest` 權限時，流程在草稿停住且測試覆蓋此路徑。

### Phase 4 — 類 RPA 排程與可替換 LLM

- 排程：交易日 21:30 後（籌碼較完整）跑 Data → Daily → 持股 Research／Position → digest 草稿。
- 核准：CLI flag 或之後由網站按「寄出」（網站改動不在本 repo 必做範圍，但 API 要能回傳草稿與 pending 狀態）。
- LLM backend 介面：`agy` 與現有 Ollama chat 都走同一 adapter；Orchestrator 不綁死 agy。
- **可選、薄適配：** 同一 MCP tools 給 Copilot Studio custom connector。不要用 Power Platform 重做 gate。

完成定義：不用人盯著 `main.py` 逐步下指令，也能產出當日草稿；寄信仍要一次核准。

---

## 5. 明確不做

- 不再為面試加新的報告模板／章節（除非 tool 契約需要）。
- 不把 STT、Next.js、Supabase 搬進本 repo。
- 不接券商下單、不自動改持股。
- 不用 Copilot Studio 重寫籌碼／gate。
- 不把 MCP、Orchestrator、排程在同一個 PR 一次做完。

---

## 6. 建議目錄

現有 `.agents/skills/` 與 `api/stock_api.py` 先保留。新程式往獨立套件長，避免再把 1.6k 行 API 撐更大。

```
agent/
  tools/          # Phase 0：typed tool 實作（包現有 skills）
  mcp_server.py   # Phase 1
  orchestrator.py # Phase 2
  policy.py       # 有持倉才 position、send 需核准、tool budget
  audit.py        # Phase 3 JSONL
  llm.py          # Phase 4 adapter
```

`main.py` 加子命令：`mcp`、`agent`。  
`api/stock_api.py` 逐步改呼叫 `agent.tools`，行為對網站保持相容。

---

## 7. 與相鄰專案

| Repo | 角色 | 本路線對它的要求 |
|------|------|------------------|
| `stock-report-site` | 導入：登入、儀表板、語音填表、寄信 | 之後可改為「對 Orchestrator 下意圖」；寄信改吃 pending digest。非 Phase 0–1 阻擋項。 |
| `AI_Speech/stt` | `transcribe_voice` tool | 本 repo 最多加一個可選 MCP tool 轉打 STT HTTP；不在本 repo 擴 whisper。 |

面試故事收成一句：這是券商研究／投顧作業的 Agent；網站是導入層，語音是入口；數字由 harness 算、gate 擋住。

---

## 8. 演示完成定義（整條路線結束時）

能現場跑（或錄影）這一條，且能指著程式講 MCP／權限／閉環：

1. 一句話：「幫我處理今天持股。」
2. Orchestrator 列出 plan（fetch / report / position / digest draft）。
3. 某檔 report-gate 若第一輪失敗，audit 看得到 `issue_codes` 與下一輪通過。
4. 無持倉的股票沒有 position 產物，且 plan 有說明。
5. 信沒寄出，只有草稿 + 待核准。
6. 同一組 tools 可用 MCP inspector 單獨點名呼叫。

---

## 9. 建議開工順序

1. Phase 0：抽出 `fetch_chips` + `run_report_gate` + `get_last_trading_date`（最小可演示的 tool 層）。
2. Phase 1：MCP 只先 expose 這三個，跑通一檔 2330。
3. 再把 position、daily、digest draft 收進 tool 層，進入 Phase 2 的「處理持股」意圖。
4. Phase 3 權限／審計與 Phase 2 可重疊，但 allowlist 測試要先有。
5. Phase 4 排程最後做；沒有它仍可面試，有它才像內部流程機器人。
