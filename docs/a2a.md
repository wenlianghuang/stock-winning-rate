# MCP 與 Agent2Agent（A2A）

職缺常把「LLM、MCP、A2A」並列。這三個不是同義詞。本文件是用語來源；Phase 2／3 **仍然不是** A2A。A2A 協定落地見 [`Phase5.md`](./Phase5.md)。

---

## 1. 職缺指的是哪一套

| 關鍵字 | 是什麼 | 解決什麼 | 本 repo |
|--------|--------|----------|---------|
| LLM | 大型語言模型 | 語言與敘事 | 有：agy／Ollama，經 `agent/llm.py`；數字仍由 harness 算 |
| MCP | Anthropic 的 [Model Context Protocol](https://modelcontextprotocol.io/) | **一個 Agent 怎麼呼叫工具／資料**（垂直） | **有：** Phase 1 `python main.py mcp`，見 [`Phase1.md`](./Phase1.md) |
| A2A | Google 開源的 [Agent2Agent](https://github.com/a2aproject/A2A) 協定（Copilot Studio 稱 *Agent2Agent (A2A) protocol*） | **兩個獨立 Agent 怎麼發現彼此、委派任務**（水平） | **有：** Phase 5 `python main.py a2a`，一個 server 包現有 orchestrator |

標準講法：MCP 連工具；A2A 連另一個還有自己狀態、自己工具的 Agent。Copilot Studio 可當 A2A **client**（Add agent → A2A agent，讀對方的 Agent Card／endpoint）。那是協定互操作，不是「程式裡兩個 function 互相呼叫」。

本系統當 A2A **server** 時具備：

- Agent Card：`GET /.well-known/agent-card.json`
- JSON-RPC endpoint：`POST /`（method `SendMessage`）
- task 生命週期：`submitted` → `working` → `completed`／`rejected`／`failed`
- 可選 Bearer（`A2A_TOKEN`／`--token`）；未設則本機開放。OAuth 給 Studio 生產環境仍是下一步，不是本階段

**沒有**做的：把 Data／Research／Position 拆成六個 A2A server 互叫。那不是職缺要的協定經驗，也違反「領域邏輯不搬進第二套框架」。

---

## 2. Phase 2／3 跟 A2A 有沒有關係

**協定層：沒有關聯。** 不能把 Phase 2／3 講成 Agent2Agent 專案經驗。

| 階段 | 實際做了什麼 | 是不是 A2A |
|------|----------------|------------|
| Phase 2 | 同一 process：意圖 → plan → policy → `agent.tools` | 否。單一套 Orchestrator |
| Phase 3 | 同一 process：步驟貼 `actor`、角色 allowlist、JSONL audit | 否。交接是 log／CLI 標籤 |
| Phase 5 | 獨立 HTTP process：Card + JSON-RPC + task，被叫到仍進 `run_agent` | **是。** 協定適配層 |

舊版 roadmap 曾把 in-process 角色叫做 A2A。那是用詞錯誤，已改掉。Phase 5 才是協定。

懂協定的人若問 Agent Card、task、認證：指 `python main.py a2a --print-card` 與 `tests/test_a2a_server.py`。面試講 Phase 2／3 時仍用 orchestrator、allowlist、human gate、audit。

---

## 3. 各層怎麼接

```
網站 HTTP / CLI / Cursor MCP / A2A client
              │
              ▼
        agent.tools     ← 同一組台股能力
              ▲
     ┌────────┴────────┐
     │                 │
python main.py mcp   python main.py a2a
 MCP：Agent → 工具    A2A：Agent → 本 Agent
                      （Card + task）
                              │
                              ▼
                       run_agent / policy
```

四種入口共用 tools：Orchestrator、FastAPI、MCP、A2A。Copilot Studio **custom connector／MCP** 接的是工具層；**Add A2A agent** 接 Phase 5 這個 server。不要為 Studio 重寫籌碼／gate。

---

## 4. 面試怎麼講

1. **MCP 有專案經驗。** 同一組 `agent.tools`，Cursor 用 MCP 跑通 2330 fetch → report-gate。見 [`mcp-cursor.md`](./mcp-cursor.md)。
2. **A2A 有協定實作，一個 server。** Card 在 well-known；client 丟一句話，task 走完後 artifact 是 orchestrator 的 plan／結果。無持倉不跑 position，寄信仍 blocked。
3. **Phase 2／3 不要冒充 A2A。** 那是編排與權限。A2A 是「別的 Agent runtime 怎麼發現並委派給我們」。

現場主線仍然是 MCP 2330 → facts／gate → 05:30 brief，見 [`agent-roadmap.md`](./agent-roadmap.md) §8。A2A 本機畫面用 [`a2a-inspector.md`](./a2a-inspector.md)（Cursor 不是 A2A client）。不要真跑持股當第二條 live 產報。
