# MCP 與 Agent2Agent（A2A）

職缺常把「LLM、MCP、A2A」並列。這三個不是同義詞，本專案也不是三個都做了。本文件是用語來源；其他 `docs/` 不再把 Phase 2／3 叫做 A2A。

---

## 1. 職缺指的是哪一套

| 關鍵字 | 是什麼 | 解決什麼 | 本 repo |
|--------|--------|----------|---------|
| LLM | 大型語言模型 | 語言與敘事 | 有：agy／Ollama，經 `agent/llm.py`；數字仍由 harness 算 |
| MCP | Anthropic 的 [Model Context Protocol](https://modelcontextprotocol.io/) | **一個 Agent 怎麼呼叫工具／資料**（垂直） | **有：** Phase 1 `python main.py mcp`，見 [`Phase1.md`](./Phase1.md) |
| A2A | Google 2025 開源的 [Agent2Agent](https://github.com/a2aproject/A2A) 協定（開放標準；Copilot Studio 文件稱 *Agent2Agent (A2A) protocol*） | **兩個獨立 Agent 怎麼發現彼此、委派任務**（水平） | **沒有。** 未實作協定，也沒有排進 Phase 0–4 |

標準講法：MCP 連工具；A2A 連另一個還有自己狀態、自己工具的 Agent。Copilot Studio 可當 A2A **client**（Add agent → A2A agent，讀對方的 Agent Card／endpoint），那是協定互操作，不是「程式裡兩個 function 互相呼叫」。

Agent2Agent 協定實際會有、本 repo **都沒有** 的東西：

- Agent Card（能力廣告），常見路徑 `/.well-known/agent-card.json` 或 `agent.json`
- 獨立的 A2A HTTP／message endpoint
- 跨 process 的 task 生命週期（submitted / working / completed）
- 兩個獨立 Agent runtime 用協定互叫（含 OAuth 等認證）
- Copilot Studio 能直接加進去的 A2A server

---

## 2. Phase 2／3 跟 A2A 有沒有關係

**協定層：沒有關聯。** 不能當成 Agent2Agent 專案經驗。

| 階段 | 實際做了什麼 | 是不是 A2A |
|------|----------------|------------|
| Phase 2 | 同一 process：意圖 → plan → policy → `agent.tools` | 否。這是單一套 Orchestrator + 工具編排，比較靠近「MCP client 自己排 tool」加上可測規則 |
| Phase 3 | 同一 process：步驟貼 `actor`、角色 allowlist、JSONL audit | 否。交接是 log／CLI 上的標籤，不是兩個 Agent 用協定握手 |

舊版 roadmap 曾寫「specialist agents 交接（A2A）」「方便講 A2A」。那是用詞錯誤：把 in-process 角色當成了職缺的 A2A。已改掉。

**問題層：只有很薄的概念親戚。** 兩邊都在處理「不要讓一個模型包辦、誰能做什麼、做完交給下一手」。

- Agent2Agent 把這件事做成 **跨廠商、跨程序的通訊協定**。
- Phase 2／3 做成 **同一個 Python process 裡的 plan、policy、`actor` 欄位**。Data／Research／Position／Notify 是邏輯邊界，**不是** 六個 A2A server。

懂協定的人若聽到「我們做了 A2A」，會問 Agent Card、task、認證。本專案對不上。面試講 Phase 2／3 時用 orchestrator、allowlist、human gate、audit；**不要**說 A2A。

---

## 3. 各層怎麼接（現況 vs 未做）

```
現況（已落地）

  網站 HTTP / CLI / Cursor MCP client
              │
              ▼
        agent.tools     ← 同一組台股能力
              ▲
              │
     python main.py mcp   ← MCP：Agent → 工具


未做（職缺的 A2A）

  Copilot Studio 或其他 A2A client
              │  Agent2Agent 協定（Card + task + 認證）
              ▼
        （本系統若要當 A2A server：尚未實作）
              │
              ▼
        仍應進 agent.tools / 現有 MCP
        不要為 Studio 重寫籌碼／gate
```

三種入口共用 tools：Orchestrator、FastAPI、MCP。Copilot Studio **custom connector／MCP** 接的是工具層，和 **Add A2A agent** 不是同一條路。後者要本系統先成為 A2A server；那一步不在本路線裡。

---

## 4. 面試怎麼講

1. **MCP 有專案經驗。** 同一組 `agent.tools`，Cursor 用 MCP 跑通 2330 fetch → report-gate。見 [`mcp-cursor.md`](./mcp-cursor.md)。
2. **A2A 只有理解，沒有實作。** 職缺寫的 A2A = Agent2Agent 協定；本 repo 沒有 Card、沒有 A2A endpoint。
3. **Phase 2／3 不要冒充 A2A。** 那是編排與權限。若問「以後怎麼進凱基的 Studio」：Studio 當 A2A client 叫我們時，應先包 A2A server，被叫到的那一端仍走現有 tools；領域邏輯不搬進畫布。

現場主線仍然是 MCP 2330 → facts／gate → 05:30 brief，見 [`agent-roadmap.md`](./agent-roadmap.md) §8。不要為了關鍵字現場演示 A2A——沒有東西可演示。
