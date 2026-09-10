# Phase 1 — MCP server

規劃見 [`agent-roadmap.md`](./agent-roadmap.md) §4。日常 CLI 仍見 [`commands.md`](./commands.md)；網站仍走 FastAPI。本文件記錄：為什麼要 MCP、實際 expose 了哪些 tools、以及怎麼驗證「一檔沒有持股的深度報告」。Cursor 掛 server、Inspector 表單、現場 2330／3711 實測見 [`mcp-cursor.md`](./mcp-cursor.md)。MCP 與職缺的 A2A（Agent2Agent 協定）不是同一層，見 [`a2a.md`](./a2a.md)。

狀態：**已落地**（`python main.py mcp` + in-memory client 測試）。

---

## 1. 概念：同一組 tools，多一個 Agent 入口

Phase 0 把可呼叫能力收成 `agent.tools`。Phase 1 不改業務、不重寫 gate，只加一層協定：

> MCP server 包 Phase 0 的 typed tools。Cursor／Claude／inspector 用 JSON-RPC 呼叫；人與網站的路徑不變。這是 **Agent → 工具**，不是 **Agent → Agent**（A2A）。

若 MCP 再 `subprocess` 去跑 `main.py stock-report`，會把 Phase 0 收掉的 argv／cwd／stderr 問題加回來。所以 server 只做：

1. 把 tool 的 description／schema 暴露給 client
2. 把參數轉進既有 `fetch_chips` / `run_report_gate` / …
3. 把 dataclass 結果編成 JSON 回傳

意圖解析、自動選 tool、排程都是 Phase 2+。Agent 在這一層仍要**自己**決定呼叫順序。

---

## 2. 目標架構（本階段實際長成這樣）

```
Cursor / Claude / MCP inspector
        │  stdio（預設）或 streamable-http
        ▼
python main.py mcp
        │
        └─ agent/mcp_server.py  (FastMCP)
                  │
                  ▼
            agent.tools.*   ◀── CLI / FastAPI 也走這裡（Phase 0）
                  │
                  ├─ fetch_chips / build_chip_facts / get_last_trading_date
                  ├─ run_report_gate
                  ├─ get_holdings / run_position_gate
                  ├─ run_market_daily / answer_market_chat
                  └─ draft_digest / send_digest（核准點）
```

`api/stock_api.py` **沒有刪**。網站 job 繼續 HTTP；MCP 是給 Agent 的工具介面。本階段沒有 A2A server。

Phase 2 已落地：`orchestrator.py`、`policy.py`、`python main.py agent`（見 [`Phase2.md`](./Phase2.md)）。Phase 3 已落地：`audit.py`、角色 allowlist（見 [`Phase3.md`](./Phase3.md)）。Phase 4 已落地：`llm.py`、`python main.py schedule`（見 [`Phase4.md`](./Phase4.md)）。

---

## 3. 修改過程（做了什麼、為什麼）

### 3.1 `agent/mcp_server.py`：包 tools，不包 script

`build_mcp()` 用官方 SDK v1 的 `FastMCP`（`mcp>=1.28,<2`）註冊 Phase 0 全部 10 個 tools。每個 wrapper：

- 參數是 JSON 友善型別（`list[str]`、`str | None`、bool／int）
- 呼叫對應 `agent.tools` function
- 回傳 `asdict` 後的 JSON（含 `ok`、`exit_code`、`error`）

stdio 傳輸占用 stdout。skill 裡既有的 `print` 若漏到 stdout 會弄髒 JSON-RPC，所以 tool 執行期間 `redirect_stdout(sys.stderr)`。

### 3.2 description 寫清「何時用／前置條件」

這是 roadmap 對 Phase 1 的硬需求，否則模型會在沒有 CSV 時直接 `run_report_gate`，或對無持股標的跑 `run_position_gate`。

例子：`run_report_gate` 註明必須先有 CSV、缺則 `exit_code=20`、無持股也應跑此 tool、不要因此去跑部位。`send_digest` 註明必須 `approved=true`，且 Agent 不可自行核准；即使核准本 repo 仍不寄信。

Server `instructions` 寫死 Phase 1 演示路徑：2330 → fetch → report-gate，不跑 position、不寄信。

### 3.3 `python main.py mcp`

`main.py` 對 `mcp` 走 in-process `agent.mcp_server.main`，不進 `COMMANDS` 那張 script 表。

| 旗標 | 用途 |
|------|------|
| （預設）`--transport stdio` | Cursor／Claude 拉起 subprocess |
| `--transport streamable-http` | Inspector 連 `http://127.0.0.1:8000/mcp` |
| `--list-tools` | 只列名，不開 server |

### 3.4 測試：最小 MCP client，不打網路、不跑 agy

`tests/test_mcp_server.py` 用 SDK 的 in-memory transport（`create_connected_server_and_client_session`），等同「一個最小 client」：

- 列出 10 個 tools；`run_report_gate` description 含 `fetch_chips`／CSV
- mock `fetch_chips` → `run_report_gate` 跑通 `2330`
- 真缺 CSV → exit 20
- `get_holdings(2330)` → exit 21（holdings 裡只有範例 2409，符合「沒有持股的深度報告」）
- `send_digest` 未核准 → `blocked`
- `python main.py mcp --list-tools` 路由正確

跑：

```bash
uv run --extra mcp --extra stock --extra ui python -m unittest tests.test_mcp_server tests.test_agent_tools
```

---

## 4. 暴露的 tools

| Tool | 何時用 | 前置 | 副作用 |
|------|--------|------|--------|
| `get_last_trading_date` | 產報前要交易日 | 無 | 無 |
| `fetch_chips` | 深度報告第一步 | 無 | 寫 CSV |
| `build_chip_facts` | 只要 facts、不要敘事 | CSV | 寫 `.facts.json` |
| `run_report_gate` | 單檔深度報告 | CSV | 寫 md／gate log |
| `get_holdings` | 決定要不要跑部位 | 無 | 無 |
| `run_position_gate` | 有均價／張數 | CSV + 持股 | 寫 position 產物 |
| `run_market_daily` | 開盤前日報 | 無（可自抓） | 寫日報 |
| `answer_market_chat` | 問既有日報 | 最好已有 facts | 無（或只讀） |
| `draft_digest` | 多檔融合成信稿 | 報告 md | 無（只產草稿） |
| `send_digest` | 人已核准要寄 | 草稿 + `approved` | **需核准**；本 repo 不發信 |

演示用呼叫（MCP arguments，不是新的日常 CLI）：

```json
{"stocks": ["2330"]}
{"stock_id": "2330", "trade_date": "2026-08-18", "skip_pdf": true}
```

對應 Python tool 層（與 Phase 0 相同）：

```python
from agent.tools.chips import FetchChipsInput, fetch_chips
from agent.tools.report import ReportGateInput, run_report_gate

fetch = fetch_chips(FetchChipsInput(stocks=["2330"]))
gate = run_report_gate(ReportGateInput(stock_id="2330", trade_date=fetch.trade_date, skip_pdf=True))
```

---

## 5. 對操作者：人還是照 commands.md

人產報仍然：

```bash
uv run --extra stock python main.py stock-report -- --stocks 2330
uv run --extra ui --extra stock python main.py report-gate -- 2330 --skip-pdf
```

只想確認 MCP 有掛上 10 個 tools（這條會印完就結束，可以自己跑）：

```bash
uv run --extra mcp --extra stock --extra ui python main.py mcp --list-tools
```

產物路徑不變：`reports/stock/{日期}/`。一句話意圖 → plan 見 [`Phase2.md`](./Phase2.md)。

下面兩種才是「讓 Agent／Inspector 真的呼叫 tools」。**擇一即可，不要混用。**

---

### 5.1 方式 A — stdio（Cursor／Claude／Inspector 拉起 process）

指令（預設 transport，**不要**加 `--transport`）：

```bash
uv run --extra mcp --extra stock --extra ui python main.py mcp
```

**為什麼不能自己在 terminal 跑這條然後盯畫面？**

stdio 的意思是：MCP client（Cursor、Claude、Inspector）當家長，fork 一個 subprocess，雙方用 **stdin／stdout 傳 JSON-RPC**。stdout 是協定專線，不是給人看的 log；skill 的 `print` 已被轉到 stderr，避免弄髒這條線。

你自己在 terminal 執行它，process 會靜靜讀 stdin、等 JSON-RPC。畫面幾乎空白（像當掉），其實沒有 client 接上。你也把這條 stdout 佔住了，Cursor 沒辦法再用同一條。所以這條指令是「寫給 client 去執行的」，不是日常 CLI。

**在 MCP Inspector 怎麼填（Add server）**

先開 Inspector（只需這一個 terminal）：

```bash
npx -y @modelcontextprotocol/inspector
```

用它印出來的 `http://127.0.0.1:6274?MCP_INSPECTOR_API_TOKEN=...` 進 UI。Add server 維持 **Transport = `stdio (local process)`**，欄位如下。`Command` 只填執行檔；其餘一行一個參數。

| 欄位 | 填什麼 |
|------|--------|
| Server ID | `stock-winning-rate` |
| Transport | `stdio (local process)` |
| Command | `uv` |
| Arguments | 見下一塊（一行一個） |
| Environment | 空白 |
| Working directory | `/Volumes/T7_SSD/stock-winning-rate`（你的 repo 根目錄） |

Arguments：

```
run
--extra
mcp
--extra
stock
--extra
ui
python
main.py
mcp
```

不要在 Arguments 加 `--transport streamable-http`。Add → Connect → List Tools。演示：`fetch_chips`（`stocks: ["2330"]`）→ `run_report_gate`（`stock_id: "2330"`, `skip_pdf: true`）。2330 無持股，不要呼叫 `run_position_gate`。

**給 Cursor 長期用**（同一條指令，由 Cursor 拉起，不必開 Inspector）。專案已放 [`.cursor/mcp.json`](../.cursor/mcp.json)；Agent 對話怎麼跑、實測紀錄見 [`mcp-cursor.md`](./mcp-cursor.md)。

```json
{
  "mcpServers": {
    "stock-winning-rate": {
      "command": "uv",
      "args": [
        "run", "--extra", "mcp", "--extra", "stock", "--extra", "ui",
        "python", "main.py", "mcp"
      ],
      "cwd": "/Volumes/T7_SSD/stock-winning-rate"
    }
  }
}
```

---

### 5.2 方式 B — streamable-http（你先開 HTTP，Inspector 連 URL）

兩個 terminal：

```bash
# terminal 1：MCP HTTP（保持開著）
uv run --extra mcp --extra stock --extra ui python main.py mcp --transport streamable-http

# terminal 2：Inspector UI
npx -y @modelcontextprotocol/inspector
```

stderr 會印 `MCP streamable-http http://127.0.0.1:8000/mcp`。

**為什麼瀏覽器打開這個 URL 會看到 `Not Acceptable: Client must accept text/event-stream`？**

`/mcp` **不是網頁**。Streamable HTTP 規定：

- GET 是開 SSE 長連線，`Accept` 必須含 `text/event-stream`
- 真正 `tools/list`、`tools/call` 走 POST，body 是 JSON-RPC，且 `Accept` 要同時有 `application/json` 與 `text/event-stream`

瀏覽器網址列送的是 `Accept: text/html,...`，SDK 回 JSON-RPC `-32600`／HTTP 406。這代表 server **有在跑**，只是拒絕「當網頁開」。健康檢查看 terminal 1 的 uvicorn，不要用瀏覽器。

**在 MCP Inspector 怎麼連**

Add server 把 **Transport 改成 Streamable HTTP**（名稱可能是 `Streamable HTTP` / `streamable-http`）。表單會變成填 URL，填：

`http://127.0.0.1:8000/mcp`

Connect → List Tools，之後呼叫順序與 §5.1 相同。這句「連 URL」是給 Inspector 填的，不是給 Chrome 開的。

---

### 5.3 兩種擇一

| | 方式 A stdio | 方式 B streamable-http |
|--|----------------|------------------------|
| 誰啟動 `python main.py mcp` | Inspector／Cursor fork | 你自己在 terminal 開 |
| 你要不要先跑 MCP | 不要 | 要（`--transport streamable-http`） |
| Inspector Transport | `stdio` | `Streamable HTTP` |
| 常見踩坑 | 自己跑裸 `mcp` 佔住 stdout | 用瀏覽器開 `/mcp` 得到 -32600 |

同一時間只需一種。stdio 給 Cursor 日常掛 tools；HTTP 方便 Inspector 連已經開著的 process。

---

## 6. 動到的檔案

新增：

- `agent/mcp_server.py`
- `tests/test_mcp_server.py`

修改：

- `main.py` — `mcp` 子命令
- `agent/__init__.py`、`agent/tools/__init__.py` — 註解改為 Phase 1 已接上
- `docs/commands.md` — MCP 速查
- `docs/agent-roadmap.md` — Phase 1 指向本文件

沒有改 gate 驗證規則、報告模板、FastAPI JSON、網站 repo。

---

## 7. 完成定義對照

| 條件 | 結果 |
|------|------|
| `python main.py mcp` 啟動 MCP、expose Phase 0 tools | `agent/mcp_server.py`；10 個 tools |
| 每個 tool description 含何時用／前置 | 見 §4；測試檢查 `run_report_gate` 提到 CSV／`fetch_chips` |
| inspector 或最小 client 跑通 fetch → report-gate | in-memory client：`tests/test_mcp_server.py`；Inspector 逐步操作見 §5.1／§5.2 |
| FastAPI 不刪 | 未改 `stock_api.py` |
| 一檔沒有持股的深度報告 | 2330 不在 holdings；路徑只有 fetch + report-gate |

---

## 8. 現場 demo

這是現場主線的第一段：Cursor Agent 對 2330 `fetch_chips` → `run_report_gate`。步驟與 prompt 見 [`mcp-cursor.md`](./mcp-cursor.md)。接著指 facts／gate 產物，再接到 Phase 4 共用盤前 brief。Phase 2／3 不當 live 主線，也不是 A2A 演示，見 [`agent-roadmap.md`](./agent-roadmap.md) §8、[`a2a.md`](./a2a.md)。

---

## 9. 下一階段

Cursor／Inspector 現場操作與 2330／3711 實測見 [`mcp-cursor.md`](./mcp-cursor.md)。Phase 2–4 見 [`Phase2.md`](./Phase2.md)、[`Phase3.md`](./Phase3.md)、[`Phase4.md`](./Phase4.md)。

