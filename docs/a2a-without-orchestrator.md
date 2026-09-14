# 為什麼 A2A 沒有 orchestrator 是核心問題

用語見 [`a2a.md`](./a2a.md)。協定落地見 [`Phase5.md`](./Phase5.md)。意圖 → plan → policy 見 [`Phase2.md`](./Phase2.md)。本文件只講一件事：

> Agent2Agent 協定解決「另一個 Agent 怎麼發現我們、丟一個 task」。Orchestrator 解決「收到一句話之後要跑哪些 tool、哪些步驟不准跑」。缺後者時，前者只是開著的空房間。

這不是風格偏好。MCP 可以沒有 orchestrator（Phase 1 就是如此）。A2A 不行。差別來自**對端丟進來的契約粒度**，不是 HTTP 跟 stdio 差在哪。

---

## 1. 一句話

MCP 把本系統當**工具箱**：Cursor 自己選扳手。A2A 把本系統當**對等 Agent**：對方丟任務、等完成，不會逐步呼叫 `fetch_chips`。

沒有 orchestrator，A2A server 仍然可以：

- 掛 `GET /.well-known/agent-card.json`
- 接受 `POST /` JSON-RPC `SendMessage`
- 把 task 標成 `submitted` → `working` → `completed`

協定看起來成功。台股作業卻沒有穩定路徑：不知道 2330 是股號、不知道要先抓 CSV、不知道無持倉不准跑部位、不知道 `send_digest` 不能寄。

「發現得到、委派了、task completed」≠「做出正確的籌碼報告」。

---

## 2. 兩種契約：誰負責下一步

```
MCP（Phase 1）                         A2A（Phase 5）
Cursor / Claude                        Copilot Studio / a2a-inspector
  │  tools/list → 10 個 tool             │  GET Agent Card → 3 個 skill
  │  tools/call fetch_chips              │  SendMessage("2330 要不要動")
  │  tools/call run_report_gate          │  等 task completed
  ▼                                      ▼
agent/mcp_server.py                    agent/a2a_server.py
  │  無決策，只轉接                        │  必須把「一句話」變成作業
  ▼                                      ▼
agent.tools.*                          run_agent() ← orchestrator
                                       policy.py
                                       agent.tools.*
```

| | MCP | A2A |
|--|-----|-----|
| 對端是什麼 | 會自己選 tool 的 Agent | 另一個還有自己狀態的 Agent |
| 對端看見 | 10 個具名函式與 JSON schema | 一張 Card、三個 skill、一個 task |
| 對端丟什麼 | `fetch_chips(stocks=["2330"])` | `"2330 要不要動"` |
| 誰排順序 | **client LLM**（Cursor） | **必須是本系統** |
| 沒有 orchestrator | Phase 1 仍能演示 2330 產報 | Card 能讀，作業沒人負責 |
| 本 repo 入口 | `python main.py mcp` | `python main.py a2a` |

Skill 印在 Card 上，等於餐廳菜單。菜單不會煎牛排。廚房是 `run_agent`（`classify_intent` → `build_plan` → `apply_policy` → `agent.tools`）。三個 skill 共用這一間廚房，不是三個獨立 process。見 §5。

---

## 3. 對端實際丟進來的東西

A2A client **不會**傳 tool 名稱。最小形狀接近 `tests/test_a2a_server.py` 組出來的 JSON-RPC：

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "…",
      "role": "ROLE_USER",
      "parts": [{"text": "2330 要不要動"}],
      "metadata": {"skill": "stock_research", "trade_date": "2026-08-18"}
    }
  }
}
```

`agent/a2a_server.py` 的 `StockAgentExecutor` 從這裡抽出：

1. 文字：`2330 要不要動`（可能只有 `2330`，或完全空白只帶 skill id）
2. 可選 skill：`stock_research` / `process_holdings` / `market_outlook`
3. 可選 `trade_date`、`dry_run`

然後 `resolve_intent()` 把空訊息或裸代號補成意圖字串，交給 `handle_intent()` → `run_agent()`。

沒有 orchestrator 時，executor 停在「我拿到一句中文」。Card 上的 `stock_research` 不會自己去寫 `tw_stock_2330.md`。

對照 MCP：Cursor 若要同一份報告，會自己發兩次（或三次）tool call——那是另一種契約，大腦在對面。見 [`mcp-cursor.md`](./mcp-cursor.md)。

---

## 4. 有 orchestrator 時，同一句話怎麼走（本 repo 現況）

範例 A：對方丟 **「2330 要不要動」**（或 Inspector 選 skill `stock_research` 只打 `2330`）。

```
SendMessage
  → resolve_intent          「2330」+ skill stock_research → 「2330 要不要動」
  → classify_intent         stock_decision（四碼代號 / 「要不要動」）
  → probes                  get_last_trading_date、get_holdings
  → build_plan
        缺 CSV → fetch_chips
        一定有 → run_report_gate          ← 籌碼深度報告
        有均價／張數才 → run_position_gate ← 持股部位報告
        絕不放 → send_digest
  → apply_policy            無持倉則從 plan 拿掉 position；寄信預設 blocked
  → 依序執行 agent.tools
  → artifact                文字摘要 + plan JSON
  → task                    TASK_STATE_COMPLETED
```

對應程式：

- Card 三個 skill：`agent/a2a_server.py` 的 `build_agent_card()`
- 句子 → 意圖：`resolve_intent()`
- 意圖 → plan：`agent/orchestrator.py` 的 `_plan_stock_decision()` / `_plan_process_holdings()` / `_plan_market_outlook()`
- 砍違法步驟：`agent/policy.py` 的 `apply_policy()` / `allow_position()` / `allow_send_digest()`

測試證據（`tests/test_a2a_server.py`，`dry_run=True`，不跑 agy）：

- `2330 要不要動` → plan 含 `run_report_gate`，**不含** `run_position_gate`、`send_digest`，notes 有「無持倉」
- `幫我處理今天持股` → 含 report / position / `draft_digest`，**不含** `send_digest`，digest 狀態 `pending_approval`
- `明天開盤怎麼看` → 含 `answer_market_chat`，不含 position
- 空白訊息 → `TASK_STATE_REJECTED`（還在協定層，還沒進 kitchen）

所以：問股號**可以**出報告。Skill 是廣告，不是「假能力」。沒有獨立 process，只代表三道菜同一間廚房。

範例 B：對方丟 **「幫我處理今天持股」**。

Plan 不是「再跑一次 2330」，而是讀 `holdings.json` 裡**每一檔**有均價／張數的標的：缺 CSV 就 fetch → 每檔 report-gate → 允許才 position-gate → `draft_digest`（不寄）。空持股則 steps 為空，notes 說明沒有可處理標的。

範例 C：對方丟 **「明天開盤怎麼看」**。

不跑個股部位。沒有既有日報 facts 就 `run_market_daily`，再 `answer_market_chat`。

這三條對應 Card 上三個 skill，也對應 Phase 2 三個 golden 意圖。分流依據是**句子內容**（外加空訊息時的 skill 預設句），不是三個 HTTP server。

---

## 5. 「三個 skill 是 Card 上的廣告」是什麼意思

不是說點了不會產報。是說：

| 是 | 不是 |
|----|------|
| 給對方看的能力目錄（發現用） | 三個獨立 Python process |
| 空訊息時的預設意圖字串 | 三套籌碼／gate 實作 |
| 對應 orchestrator 的三種 `intent_kind` | 對方可以直接 `tools/call run_report_gate` |

`build_agent_card()` 宣告的 id：

| skill id | 菜單上的名字 | 空訊息時補成 | 進 orchestrator 的種類 |
|----------|--------------|--------------|------------------------|
| `process_holdings` | 處理持股 | 幫我處理今天持股 | `process_holdings` |
| `stock_research` | 個股深度報告 | （裸代號補成「{代號} 要不要動」） | `stock_decision` |
| `market_outlook` | 開盤前日報 | 明天開盤怎麼看 | `market_outlook` |

對方選錯 skill、但句子寫「2330 要不要動」時，`resolve_intent` 仍回傳原句，分類走 `stock_decision`。真正決定跑哪些 tool 的是 orchestrator，不是 Card 上的按鈕標籤。

若把 skill 做成三個獨立 process，每個 process 複製一份「先 fetch 再 gate」、複製一份「無持倉判斷」，政策一改要改三處。本 repo 刻意不做那條路。見 [`agent-roadmap.md`](./agent-roadmap.md) §5。

---

## 6. 沒有 orchestrator 時會落到哪裡

協定層（Card、JSON-RPC、task 狀態）都可以照做。缺的是「一句話 → 可執行、可拒絕的 tool 序列」。實作者若仍要讓 A2A「看起來有做事」，通常掉進下列四種結果。沒有第五種「反正 client 會幫你排」——那是 MCP 的前提，A2A 對端不成立。

### 6.1 空殼：發現得到，作業交白卷

Card 漂亮，Inspector 看得到三個 skill。`SendMessage` 只能：

- 立刻 `rejected`（沒有可執行的意圖），或
- `completed` 但 artifact 把原句 echo 回去，磁碟沒有 `reports/stock/{日期}/tw_stock_2330.md`

面試／Studio 對接時：對方「連上了」，委派沒有產物。這是最直接的失敗。

**同一句「2330 要不要動」**

| 有 orchestrator | 空殼 |
|-----------------|------|
| plan 含 fetch + report-gate；無持倉 notes 寫明不跑部位 | task completed，沒有 CSV、沒有 md、或只有「已收到 2330 要不要動」 |

### 6.2 每個 skill 寫死一條 script（最常見的假 A2A）

沒有 `classify_intent` / `build_plan`，變成：

- 點 `stock_research` → 永遠 `fetch_chips` + `run_report_gate`（代號可能寫死 2330，或自己再 parse 一次）
- 點 `process_holdings` → 永遠整批持股 CLI
- 點 `market_outlook` → 永遠日報

後果用例子講：

1. 句子是「3711 要不要動」，對方卻點了處理持股 → 可能去跑 **holdings 裡全部標的**，或完全不理 3711。
2. 句子是「2330 要不要動」，script 寫死只跑 research → 即使 `holdings.json` 裡 2330 有均價／張數，也**不出**部位報告；或反過來寫死一定跑 position，無持股時撞 `exit_code=21`。
3. 政策（寄信要核准、缺 CSV 先 fetch）每個 skill 複製一份。漏改一條，A2A 外表仍是 `completed`。

這時 A2A 只是 **HTTP 包 CLI**。對方碰到的不是「另一個會判斷的 Agent」，是三個按鈕後面的三支 shell。職缺若追問 Agent Card 與 task，你仍能答協定；若追問「無持倉為什麼沒跑部位」，答案會散落在三份 if/else，而不是 `policy.py` 一條可測規則。

### 6.3 把 10 個 MCP tool 再暴露給 A2A client（用錯協定）

等於逼 Copilot Studio／inspector 當 Cursor：自己知道要先 fetch、缺 CSV 要 retry、無持股不要 position。

後果：

- A2A 契約是 **task**，不是 MCP 的 `tools/call`。對端大多不會對你的內部函式做 tool loop。
- 政策變成「希望對方 LLM 讀 description」。沒有執行前的 plan 可砍步驟。
- 維護兩套入口契約（MCP 10 tools + A2A 又 10 tools），「Agent 委派」沒做成。

**同一句「2330 要不要動」**

| 有 orchestrator | 誤用成 tool 目錄 |
|-----------------|------------------|
| 一次 SendMessage，server 內部排完 | 對端必須自己再發 fetch、再發 gate；Studio 通常不會 |

### 6.4 把原句直接丟給模型，沒有 plan／policy

agy／Ollama 自己決定要不要 `send_digest`、要不要對沒持股的代號跑部位、要不要先抓 CSV。

相對本 repo 現在的五條規則（[`Phase2.md`](./Phase2.md) §3.1）：

| 規則 | 有 orchestrator | 只靠模型、沒有 plan |
|------|-----------------|---------------------|
| 缺資料先 Data | plan 插入 `fetch_chips`；執行期 exit 20 再補一次 | 可能直接 `run_report_gate` → `exit_code=20` 停住 |
| 無持倉不 Position | `allow_position` 事前從 plan 移除 | 可能仍呼叫，事後才碰到 21 |
| gate 失敗 | 仍在既有 gate 的 `max_rounds` 內 | 模型可能在 gate 外再互叫、重跑 |
| send 預設 blocked | plan 不含 send；JSON 裡 `approved=true` 也不算 | 模型可能自行核准或跳過草稿 |
| tool budget | 32 次／1800 秒，用盡 SKIP | 可能無限轉圈 |

A2A 外表仍可 `completed`。作業路徑每次不一樣，golden fixture 測不了。這正是 Phase 2 要把規則寫成函式、不交給模型的原因。A2A 若跳過那一層，等於把 Phase 2 的約束在「給另一個 Agent 的入口」上打開一個洞。

---

## 7. 三個對照表：有／沒有 orchestrator

下列假設對端都已讀到同一張 Card、同一個 JSON-RPC endpoint。變的只是 `SendMessage` 之後有沒有 `run_agent`。

### 7.1 單檔研究（skill `stock_research`）

意圖：`2330 要不要動`。測試裡 2330 無持倉。

| 步驟 | 有 | 沒有（空殼／寫死／丟模型） |
|------|----|----------------------------|
| 認出 2330 | `STOCK_RE` + `stock_decision` | 可能當閒聊、當盤前、或寫死另一檔 |
| 缺 CSV | 插入 `fetch_chips` | 可能直接 gate 失敗 20 |
| 深度報告 | 一定 `run_report_gate` | 不一定 |
| 部位報告 | 無持倉：plan 不含 `run_position_gate`，notes 說明 | 可能跑、可能不跑、可能炸 21 |
| 寄信 | 不在 plan 裡 | 模型路徑可能嘗試 send |

本機 `holdings.json` 若**有** 2330 的均價／張數（範例檔目前有），真執行（非 `--dry-run`）會連部位一起跑。那是 policy 讀持股的結果，不是 skill 名稱變了。Fixture `tests/fixtures/agent/stock_no_holding.json` 把 2330 設成沒持股，用來鎖住「無持倉只走 research」。

### 7.2 整批持股（skill `process_holdings`）

意圖：`幫我處理今天持股`。

| 步驟 | 有 | 沒有 |
|------|----|------|
| 標的從哪來 | `get_holdings`，只收有均價／張數的檔 | 可能忽略 holdings、或跑 watchlist、或只跑 2330 |
| 每檔報告 | report-gate；允許才 position-gate | 三條 script 各自決定 |
| digest | 只 `draft_digest`，狀態 `pending_approval` | 可能真的組出 send，或完全沒有草稿 |
| 空持股 | steps 空、notes 說明 | 可能對不存在的代號硬跑 gate |

### 7.3 選錯菜單、句子是對的

對方點了 `process_holdings`，文字卻是 `2330 要不要動`。

| 有 orchestrator | 寫死 skill → script |
|-----------------|---------------------|
| `resolve_intent` 保留原句 → `stock_decision` → **只處理 2330** | 忽略句子，去跑「今天全部持股」 |

這就是「廣告 vs 廚房」的操作後果：菜單點錯不應把整桌菜端錯；廚房要讀點單上的字。

---

## 8. 為什麼這是核心問題，而不是「再包一層比較漂亮」

少掉的不是程式風格。少掉的是 **A2A 作為 Agent 入口所預設的那顆大腦**：

1. **契約不對稱。** MCP 的大腦可以在 client。A2A 的大腦不能在 client，因為 client 不呼叫你的 tool。
2. **政策必須在委派邊界之內。** 無持倉不部位、寄信要人核准、不下單、budget，若只寫在 MCP description 或 tool 回傳碼，A2A 對端看不見那些 tool，也就不會遵守。必須在 `SendMessage` → `agent.tools` 中間擋。那中間就是 orchestrator + policy。
3. **可測性。** `tests/test_orchestrator.py` 的 golden 意圖與 `tests/test_a2a_server.py` 的 dry-run SendMessage 測的是同一顆大腦。拿掉 orchestrator，A2A 測試只能測 Card JSON 與 HTTP 200，測不到「2330 無持倉不含 position」。
4. **領域邏輯只准一份。** 沒有 orchestrator 時，為了讓三個 skill「還能動」，人會把 fetch／gate／持股判斷複製進 A2A server。那違反 Phase 0「能力收成 `agent.tools`、入口只做適配」。

因此 Phase 5 的設計句是：**包 orchestrator，不包 script、也不包 MCP tool 清單。** `a2a_server.py` 是協定適配；`orchestrator.py` 才是被委派的那個 Agent。

MCP 仍然沒有、也不該強制經過 orchestrator。Cursor 已經會排 tool。兩扇門共用 `agent.tools`，不共用「誰決定下一步」。

---

## 9. 本機怎麼對這份文件

不需要為了理解而真跑 agy。

```bash
# Card 上的三個 skill（廣告）
uv run --extra a2a --extra stock --extra ui python main.py a2a --list-skills
uv run --extra a2a --extra stock --extra ui python main.py a2a --print-card

# 同一顆大腦、不經 HTTP
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "2330 要不要動"

# A2A 測試：Card + SendMessage 的 plan 含／不含哪些 tool
uv run --extra a2a --extra stock --extra ui python -m unittest tests.test_a2a_server
```

Inspector 畫面見 [`a2a-inspector.md`](./a2a-inspector.md)。連到的 server 若加了 `--dry-run`，artifact 只有 plan，磁碟不會立刻出現報告——那是演示開關，不是「沒有獨立 process 所以不能產報」。

---

## 10. 面試怎麼講（30 秒）

1. MCP 是 Agent → 工具。Cursor 列 10 個 tool，自己先 fetch 再 gate。那一層不需要我們的 planner。
2. A2A 是 Agent → Agent。對方只丟「2330 要不要動」。若沒有 orchestrator，我們只有 Card 與 task 狀態，沒有人把句子編成 tool 序列。
3. 所以 Phase 2 的 `run_agent` 不是拿來冒充 A2A 協定；它是 A2A server **被叫到之後**唯一能負責作業的那一層。Phase 5 一個 Card、三個 skill 當目錄，進同一套 plan／policy。無持倉不 position、send 需核准，測試裡對 SendMessage 鎖死。

不要說「我們做了六個 A2A agent 互叫」。不要說「skill 只是廣告所以問 2330 不會出報告」。前者把 in-process 角色講成協定；後者把「同一間廚房」講成「不開伙」。
