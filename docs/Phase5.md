# Phase 5 — Agent2Agent（A2A）server

規劃見 [`agent-roadmap.md`](./agent-roadmap.md) §4。用語對照見 [`a2a.md`](./a2a.md)。日常產報仍見 [`commands.md`](./commands.md)。本文件記錄：為什麼要 A2A、Card／JSON-RPC 接在哪、以及怎麼驗證「另一個 Agent 能發現我們並委派任務」。

狀態：**已落地**（`python main.py a2a` + Agent Card／JSON-RPC 測試）。

---

## 1. 概念：第四扇門，不是第二套台股邏輯

Phase 0 收成 `agent.tools`。Phase 1 用 MCP 給「會自己選 tool 的 client」。Phase 2／3 是同一 process 的編排與權限。

職缺的 A2A 要解的是另一件事：

> 獨立的 Agent runtime（Copilot Studio、a2a-inspector、別的公司的 Agent）怎麼發現本系統、丟一個 task、等完成。被叫到的那一端仍走 `run_agent` → policy → `agent.tools`。

所以本階段只做協定適配：

1. 廣告能力：Agent Card（`/.well-known/agent-card.json`）
2. 收 task：JSON-RPC `SendMessage`
3. 生命週期：`submitted` → `working` → `completed`（空意圖 `rejected`）
4. **一個** server，三個 skill 只是 Card 上的能力廣告，不是六個 process

不重寫 gate。不把 Data／Research／Position 網路化。`send_digest` 繼續 blocked。

---

## 2. 目標架構

```
Copilot Studio / a2a-inspector / curl
        │  GET /.well-known/agent-card.json
        │  POST /   JSON-RPC SendMessage
        ▼
python main.py a2a
        └─ agent/a2a_server.py  (a2a-sdk 1.x + Starlette)
                  │
                  ▼
            run_agent()          ← 與 `python main.py agent` 同一條
                  │
                  ▼
            agent.tools.*        ← CLI / FastAPI / MCP 也走這裡
```

網站仍走 FastAPI。MCP 仍給 Cursor。A2A 是給「另一個 Agent」的入口。

---

## 3. 修改過程

### 3.1 `agent/a2a_server.py`：包 orchestrator，不包 script

`build_agent_card()` 宣告三個 skill，對應 Phase 2 的 golden 意圖：

| Skill id | 例子 | 進 orchestrator 的意圖 |
|----------|------|------------------------|
| `process_holdings` | 幫我處理今天持股 | 原句 |
| `stock_research` | 2330 要不要動 | 四碼代號會補成「要不要動」 |
| `market_outlook` | 明天開盤怎麼看 | 原句或 skill 預設句 |

`StockAgentExecutor` 實作 A2A 1.0 的 **task lifecycle**（先 enqueue `Task`，再 working／artifact／completed）。訊息文字交給 `handle_intent` → `run_agent`。預設 `approve_send=False`，policy 不會寄信。

`--dry-run` 只編成 plan，給 inspector 演示，避免一接上就跑 agy。

### 3.2 協定細節

| 項目 | 行為 |
|------|------|
| Card | `GET /.well-known/agent-card.json` |
| JSON-RPC | `POST /`，method `SendMessage`（v0.3 的 `message/send` 也開了 compat） |
| 版本 | 客戶端應帶 `A2A-Version: 1.0`。缺 header 時本 server 預設補 1.0（官方 SDK 缺省會當成 0.3） |
| 認證 | 可選 Bearer（`--token` / `A2A_TOKEN`）。Card 一律公開。未設 token 則本機開放 |
| Streaming | Card 宣告 `streaming: false`；一次 `SendMessage` 等到終態 |

沒有做 OAuth／JWKS。Studio 生產接線要另補認證，不要在本階段假裝已接上 Azure AD。

### 3.3 `python main.py a2a`

與 `mcp` 一樣走 in-process，不進 `COMMANDS` script 表。缺 `a2a` extra 時印安裝提示。

| 旗標 | 用途 |
|------|------|
| （預設）開 HTTP | `127.0.0.1:9999`（避開 API `:8765`、MCP inspector `:8000`） |
| `--print-card` | 印 Card JSON，不開 server |
| `--list-skills` | 只列 skill id |
| `--dry-run` | orchestrator 只列 plan |
| `--date` | 指定交易日 |
| `--token` | Bearer；亦可用 `A2A_TOKEN` |

### 3.4 測試：TestClient 當最小 A2A client

`tests/test_a2a_server.py` 不打外網、不跑 agy（`dry_run=True`）：

- Card 含 JSONRPC interface 與三個 skill
- `GET /.well-known/agent-card.json`
- `SendMessage`「2330 要不要動」→ 有 `run_report_gate`、無 `run_position_gate`
- 「幫我處理今天持股」→ 有 position／draft、無 `send_digest`、digest pending
- 空訊息 → `TASK_STATE_REJECTED`
- Bearer 擋 RPC、不擋 Card
- `python main.py a2a --list-skills` 路由正確

跑：

```bash
uv run --extra a2a --extra stock --extra ui python -m unittest tests.test_a2a_server
```

---

## 4. 對操作者

人產報仍然走 [`commands.md`](./commands.md)。只想確認 Card：

```bash
uv run --extra a2a --extra stock --extra ui python main.py a2a --print-card
uv run --extra a2a --extra stock --extra ui python main.py a2a --list-skills
```

開 server（演示建議 `--dry-run`）：

```bash
uv run --extra a2a --extra stock --extra ui python main.py a2a --dry-run --date 2026-08-18
```

stderr 會印：

```
A2A Agent Card http://127.0.0.1:9999/.well-known/agent-card.json
A2A JSON-RPC POST http://127.0.0.1:9999/
```

curl 讀 Card：

```bash
curl -s http://127.0.0.1:9999/.well-known/agent-card.json
```

JSON-RPC（另一個 terminal）：

```bash
curl -s http://127.0.0.1:9999/ \
  -H 'Content-Type: application/json' \
  -H 'A2A-Version: 1.0' \
  -d '{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"messageId":"m1","role":"ROLE_USER","parts":[{"text":"2330 要不要動"}]}}}'
```

畫面（另開 terminal；A2A server 保持開著）：

```bash
uv run --extra a2a python main.py a2a-inspector
```

瀏覽器 `http://127.0.0.1:5001`，Connect 填 `http://127.0.0.1:9999`。步驟見 [`a2a-inspector.md`](./a2a-inspector.md)。Studio：Add A2A agent，填 Card URL。

---

## 5. 動到的檔案

新增：

- `agent/a2a_server.py`
- `agent/a2a_inspector.py`
- `tests/test_a2a_server.py`
- `docs/Phase5.md`、`docs/a2a-inspector.md`

修改：

- `pyproject.toml` — `a2a` extra（`a2a-sdk[http-server]` + uvicorn）
- `main.py` — `a2a` 子命令
- `docs/a2a.md`、`docs/commands.md`、`docs/agent-roadmap.md`

沒有改 gate 驗證規則、報告模板、MCP tool 清單、FastAPI JSON、網站 repo。沒有接券商 API。沒有把內部角色拆成多個 A2A server。

---

## 6. 完成定義對照

| 條件 | 結果 |
|------|------|
| Agent Card 可發現 | `GET /.well-known/agent-card.json`；`--print-card` |
| JSON-RPC 委派任務 | `SendMessage` → task completed + orchestrator artifact |
| 同一組 tools／policy | 無持倉不 position；send_digest blocked |
| 一個 server | 三個 skill 只是 Card 廣告 |
| FastAPI／MCP 不刪 | 未改 `stock_api.py`、`mcp_server.py` 行為 |

---

## 7. 現場 demo

**不要**用本階段取代 MCP 2330 主線。A2A 現場：`--print-card`，或 Inspector 丟「2330 要不要動」（見 [`a2a-inspector.md`](./a2a-inspector.md)）。不要真跑持股當第二條 live 產報。

接著仍指 facts／gate 產物與 05:30 brief。見 [`agent-roadmap.md`](./agent-roadmap.md) §8、[`a2a.md`](./a2a.md)。
