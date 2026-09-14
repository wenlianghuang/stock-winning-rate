# Agent 精進路線

把本專案從「人觸發的產報 pipeline」升級成「會自己呼叫工具、受權限約束的台股作業 Agent」。

網站（`stock-report-site`）當導入層，STT（`AI_Speech/stt`）當語音 tool。本文件只規劃 `stock-winning-rate` 本體；相鄰 repo 的接點寫在最後一節。

命令速查見 [`commands.md`](./commands.md)。職缺並列的 MCP／A2A 差在哪、Phase 2／3 為什麼**不是** Agent2Agent，見 [`a2a.md`](./a2a.md)。A2A 協定落地見 [`Phase5.md`](./Phase5.md)。A2A 為什麼不能沒有 orchestrator，見 [`a2a-without-orchestrator.md`](./a2a-without-orchestrator.md)。

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
| report / position / daily 各跑各的 | 同一 process 裡用角色標籤編排（**不是** Agent2Agent 協定） |
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

`pyproject.toml` 的 `mcp` extra 在 Phase 1 落地；`a2a` extra 在 Phase 5 落地。

### 缺（本路線要補）

Phase 0–4 已落地後，下列是**當時**要補的能力（實作狀態見各 Phase 文件）。A2A 協定在 Phase 5，見 [`Phase5.md`](./Phase5.md) 與 [`a2a.md`](./a2a.md)。

- MCP tool 介面（依賴已宣告、程式沒有）→ Phase 1
- Orchestrator：自然語言／結構化意圖 → 選 tool、填參數、處理失敗 → Phase 2
- 角色 allowlist 與可重放 audit，而不是一支 script 串完就看不出誰做了哪步 → Phase 3（in-process，不是 A2A）
- 權限：誰能跑什麼、寄信／發布是否要人確認
- 審計：每次 tool call 的輸入、輸出摘要、耗時、結果
- 排程型「類 RPA」：台北 05:30 後產一份全站共用開盤前 brief（不夜跑每人持股）→ Phase 4
- Agent2Agent：一個對外 server（Card + JSON-RPC），仍進 `run_agent` → Phase 5

---

## 3. 目標架構

```
使用者（網站／CLI／語音）
        │
        ▼
┌───────────────────┐
│ Orchestrator      │  意圖解析、tool 選擇、失敗重試、人機臨界點
│ （本 repo 新層）   │  單 process；不是 A2A 網路
└─────────┬─────────┘
          │ MCP（Agent → 工具；已落地）
    ┌─────┴──────────────────────────────┐
    ▼          ▼           ▼             ▼
 Data        Research    Position     Notify
 （角色）     （角色）     （角色）     （角色）
    │          │           │             │
    ▼          ▼           ▼             ▼
 fetch_chips  report-gate position-gate send_digest
 build_facts  daily/weekly portfolio    （核准後）
              validate-*
```

圖裡的 Data／Research／… 是 **邏輯角色**，跑在同一個 Orchestrator process。它們沒有各自的 Agent Card，也沒有用 Agent2Agent 協定互叫。職缺的 A2A 是「Studio 或其他 Agent ↔ 本系統」的另一條線：Phase 5 用**一個** A2A server 包 `run_agent`。見 [`a2a.md`](./a2a.md)、[`Phase5.md`](./Phase5.md)。

同一組 tool 四種入口共用：Orchestrator、現有 FastAPI、MCP、A2A。Copilot Studio **custom connector** 若接，接的是 HTTP／MCP 工具層；**Add A2A agent** 接 Phase 5 的 `python main.py a2a`。  
**不要**為 Microsoft 棧重做整套台股邏輯。

角色對應現有程式（先當邏輯邊界，**不是** 六個 A2A server；A2A 只有一個對外 runtime）：

| 角色 | 現有模組 | 職責 |
|-------|----------|------|
| Data | `tw-stock-report`、`chip_signals`、`market_day_signals` | 抓資料、算 facts；不寫敘事 |
| Research | `report-gate`、`market-daily`、`market-weekly`、`us-tech-news` | 產報 + 格式／事實／推理 gate |
| Position | `position-gate`、`portfolio-gate` | 有持倉／有資金才跑；部位層驗證 |
| Validator | `validate_*.py`、`fact_checks`、`reasoning_checks` | 可被 Research／Position 呼叫，也可獨立退回 |
| Notify | `/digest`、report-site Email | 預設需核准 |
| Orchestrator | **新** | 只編排，不算籌碼、不寫長文 |

---

## 4. 分階段

每階段都要能獨立驗證（測試／CLI），做完再進下一階段，不要平行開很多空殼。現場時間不均分：Phase 2／3 不必當獨立 live 戲份，見 §8。

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
3. gate 失敗則把 `issue_codes` 交回同一角色（仍在既有 gate loop 內），直到通過或達 `max_rounds`。
4. `send_digest` 預設 blocked，只產生草稿與「待核准」狀態。
5. 單次 run 有 tool budget（次數／時間），避免無限互叫。

LLM 在這一層只做：意圖 → 結構化 plan（tool 序列 + 參數）。  
Plan 通過 schema 驗證後才執行。執行失敗用規則決定 retry 或停。

完成定義：

- CLI：`python main.py agent -- "幫我處理今天持股"`。
- 印出 plan、每步 tool、gate 通過與否。
- 至少 3 個 golden 意圖有 fixture 測試（有持倉／無持倉／只問路況）。

### Phase 3 — 角色 allowlist、交接標籤與權限

落地說明（角色 allowlist、Research↔Validator 標籤、JSONL audit／replay）見 [`Phase3.md`](./Phase3.md)。與 Agent2Agent 的對照見 [`a2a.md`](./a2a.md)。

把 Phase 2 的內部步驟標上 `actor`，方便審計與權限，**不是**為了實作或冒充 A2A。

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

### Phase 4 — 類 RPA：05:30 共用開盤前 brief 與可替換 LLM

落地說明見 [`Phase4.md`](./Phase4.md)。

- **主路徑（共用）：** 台北 05:30（美股 overnight settle）跑 `run_market_daily` → `reports/market/{trade_date}/`。所有使用者看同一份。
- **不做：** 夜跑每人持股／position／digest。均價與持股方法跟人走，仍由使用者觸發。
- **美股失敗不略過：** Yahoo 重試後仍失敗則整次 run 失敗，不得默默 `--skip-us`。
- **幂等：** 該 `trade_date` 已有通過且含美股的 brief 則 skip；`--force` 才重跑。
- LLM backend：`agent/llm.py`（`agy` / Ollama）；Orchestrator 與 tools 不綁死 agy。
- 網站讀 canonical brief，不再按 `user_id` 複製一份敘事。

完成定義：不用人按「產生日報」，05:30 後每位登入者看到同一份 `for_session` brief；持股報告仍手動。

### Phase 5 — Agent2Agent server

落地說明（一個 Card、JSON-RPC task、仍進 orchestrator）見 [`Phase5.md`](./Phase5.md)。

- `python main.py a2a` 啟動 A2A server；`GET /.well-known/agent-card.json`。
- Client `SendMessage` 一句話 → `run_agent`；policy 不變（無持倉不 position、send 需核准）。
- 三個 skill 是 Card 廣告，不是三個 process。
- 可選 Bearer；本階段不做 OAuth。

完成定義：最小 client（測試裡的 TestClient 或 curl）能讀 Card，並 dry-run 完「2330 要不要動」得到 completed task、plan 不含 position。

---

## 5. 明確不做

- 不再為面試加新的報告模板／章節（除非 tool 契約需要）。
- 不把 STT、Next.js、Supabase 搬進本 repo。
- 不接券商下單、不自動改持股。
- 不用 Copilot Studio 重寫籌碼／gate。
- **不把 Data／Research／Position 拆成多個 A2A server。** Phase 5 只有一個對外 Agent Card，被叫到仍進 `agent.tools`。Phase 2／3 也不得改稱 A2A 經驗。見 [`a2a.md`](./a2a.md)。
- 不把 MCP、Orchestrator、排程、A2A 在同一個 PR 一次做完（A2A 已另開 Phase 5）。

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
  schedule.py     # Phase 4：05:30 共用盤前 brief
  a2a_server.py   # Phase 5：一個 A2A server
  a2a_inspector.py  # 官方 Inspector 啟動器（clone 在 .cache）
```

`main.py` 加子命令：`mcp`、`agent`、`schedule`、`a2a`。  
`api/stock_api.py` 逐步改呼叫 `agent.tools`，行為對網站保持相容。

---

## 7. 與相鄰專案

| Repo | 角色 | 本路線對它的要求 |
|------|------|------------------|
| `stock-report-site` | 導入：登入、儀表板、語音填表、寄信 | 開盤前日報讀全站共用 brief（Phase 4）。寄信仍可之後吃 pending digest。 |
| `AI_Speech/stt` | `transcribe_voice` tool | 本 repo 最多加一個可選 MCP tool 轉打 STT HTTP；不在本 repo 擴 whisper。 |

面試故事收成一句：這是券商研究／投顧作業的 Agent；網站是導入層，語音是入口；數字由 harness 算、gate 擋住。MCP 給 Cursor 選 tool；A2A 給另一個 Agent runtime 發現並委派。Phase 2／3 是編排與權限，不是 A2A。

---

## 8. 演示：路線完成 vs 現場腳本

整條路線結束時，§8.1 都要**能指著程式或測試講**。現場 10–15 分鐘**不要**依序演完每一 Phase。Phase 2／3 對職缺的 **orchestrator／權限／審計** 有用，對 **A2A 關鍵字**要用 Phase 5 的 Card／JSON-RPC，不要拿角色標籤充當（見 [`a2a.md`](./a2a.md)）。Phase 2／3 當口頭對應，不要當第二、第三個 live 流程。

### 8.1 路線完成定義（工程上要有，不必現場全跑）

1. 一句話意圖能編成 plan：`python main.py agent -- "幫我處理今天持股。"`（`--dry-run` 即可證明）
2. Plan 含 fetch / report / position / digest draft；無持倉不跑 position，notes 有說明。
3. report-gate 第一輪失敗時，audit／`.gate.log` 看得到 `issue_codes` 與下一輪通過。
4. `send_digest` 預設 blocked，只有草稿 + 待核准。
5. 同一組 tools 可用 MCP inspector 或 Cursor Agent 單獨點名呼叫（Cursor 實測見 [`mcp-cursor.md`](./mcp-cursor.md)）。
6. 另一個 Agent 可發現本系統：`python main.py a2a --print-card`；dry-run `SendMessage` 完 2330 無 position（見 [`Phase5.md`](./Phase5.md)）。

Golden fixtures 與 Phase 3 測試覆蓋 1–4；5 用 Cursor 現場跑。不要為了演示刪 `orchestrator.py`／`audit.py`——政策（無持倉不部位、寄信要核准、budget）仍是這條線跟「包一層 LLM 呼叫 script」的差別。

### 8.2 現場主線（建議）

資訊密度高、失敗面可控的三段：

1. **Phase 1 MCP（2330）** — Cursor Agent：`fetch_chips` → `run_report_gate`。無持股不跑 position、不寄信。步驟見 [`mcp-cursor.md`](./mcp-cursor.md)。
2. **Harness + gate** — 打開剛寫的 `.facts.json` 與（若有）曾被打回的 `.gate.log`。數字程式算、模型只寫敘事、不合格打回。閉環在 `report-gate` 裡就發生，不必靠 Phase 3 的角色標籤。
3. **Phase 4 盤前** — 今天 05:30 已有的共用 brief（或 `schedule --once --dry-run` 說明幂等／美股失敗不略過）。沒人按按鈕也會跑；持股仍手動。

跳過（現場）：真跑 `python main.py agent` 處理持股（agy 多輪、慢、易卡）、`--replay` 當主線、`--role chat` 把 plan 拆空。必要時 `--dry-run` 十秒帶過 policy。

為什麼 Phase 2／3 不當 live 主線：

- Phase 1 的 Cursor client **已經**在用自然語言選 tool；預設 planner 是規則分類（關鍵字 + 四碼代號），dry-run 看起來像固定工作流表，現場比 MCP 弱。
- Phase 3 不是六個 process，更不是 Agent2Agent。Validator「交接」是事後讀 `.gate.log`，既有 gate loop 的多角色標籤。若講成 A2A，懂協定的人會問 Agent Card——那張 Card 在 Phase 5，不在本階段。

職缺關鍵字怎麼對：

- **MCP**：現場主線第一段（真的有）。
- **orchestrator／權限／審計**：口頭對應 Phase 2／3——意圖進可測 plan；模型不能靠 JSON 偷寄信；每次 tool 有 actor + JSONL；不下單。
- **A2A（Agent2Agent）**：Phase 5 的一個 server。`--print-card` 或 curl well-known；不要用 Phase 2／3 充當，也不要現場真跑 agy。

---

## 9. 建議開工順序

1. Phase 0：抽出 `fetch_chips` + `run_report_gate` + `get_last_trading_date`（最小可演示的 tool 層）。
2. Phase 1：MCP 只先 expose 這三個，跑通一檔 2330。
3. 再把 position、daily、digest draft 收進 tool 層，進入 Phase 2 的「處理持股」意圖。
4. Phase 3 權限／審計與 Phase 2 可重疊，但 allowlist 測試要先有。不要在這一階段做 A2A server。
5. Phase 4：05:30 共用盤前 brief（不要做成每人持股 cron）。
6. Phase 5：一個 A2A server 包 `run_agent`（不要拆成多個 Card）。
