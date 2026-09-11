# Cursor MCP：無持股深度報告實測

協定與 tool 契約見 [`Phase1.md`](./Phase1.md)。本文件記錄：三條入口差在哪、Cursor 怎麼掛、Inspector 表單怎麼填、以及 2026-09-10 用 Cursor Agent 跑通 2330／3711 的結果。

狀態：**已用 Cursor MCP 實測**（stdio；專案 `.cursor/mcp.json`）。單元測試仍是 `tests/test_mcp_server.py`，不取代本文件的現場路徑。

---

## 1. 概念：MCP 不是網站操作路徑

使用者在 `stock-report-site` 點產報，走的是 Next.js → `lib/agent-client.ts` → FastAPI `:8765` → `agent.tools`。**不經 MCP。**

MCP 是同一組 `agent.tools` 的第三扇門：給 Cursor／Claude／Inspector，以及未來若接 Copilot 的 **custom connector／MCP**（工具層）。那 **不是** Copilot Studio 的「Add A2A agent」（Agent2Agent 協定）。Inspector 那頁是除錯／演示 client，不是產品 UI。對照見 [`a2a.md`](./a2a.md)。

| 入口 | 誰啟動 server | 誰在用 |
|------|----------------|--------|
| 網站 | `python main.py api`（HTTP `/jobs` 等） | 登入使用者 |
| Cursor MCP | Cursor fork `python main.py mcp`（**stdio**） | Agent 對話 |
| Inspector HTTP | 你先開 `--transport streamable-http` | 人在 Inspector 填表 |

Cursor 這條**不要**自己在 terminal 跑 `python main.py mcp`，也不要加 `--transport streamable-http`。stdio 的 stdout 是 JSON-RPC 專線；你先佔住，Cursor 就接不上。

面試不要說「使用者現在用 MCP 產報」。可以說：導入層仍是 HTTP；MCP 證明同一組 tools 換一個 Agent client 也能跑，沒有第二套台股邏輯。網站幾乎感覺不到 Phase 1，是刻意的。

---

## 2. Cursor 怎麼跑（stdio）

### 2.1 設定

專案已有 [`.cursor/mcp.json`](../.cursor/mcp.json)：

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

`args` 最後是 `mcp` 而已。若 Cursor 從 Dock 開找不到 `uv`，把 `command` 改成 `which uv` 的絕對路徑。

### 2.2 確認已連上

1. **Cursor Settings → MCP**（或 Tools & MCP）
2. `stock-winning-rate` 開關打開、狀態綠
3. 展開應有 10 個 tools（`fetch_chips`、`run_report_gate`、…）

紅燈：Reload Window；查 MCP log 是 `uv` PATH 還是 `cwd` 錯。

### 2.3 必須用 Agent 對話

Ask mode 不會真的呼叫 MCP tools。開新的 **Agent** chat，核准有副作用的 tool（`fetch_chips` 寫 CSV，`run_report_gate` 寫 md／facts／gate log）。

Phase 1 演示 prompt（無持股、不跑部位、不寄信）：

> 用 stock-winning-rate 的 MCP tools，對 2330 產出一份沒有持股的深度報告。先 `fetch_chips`（stocks 為 `["2330"]`），再 `run_report_gate`（stock_id `2330`，skip_pdf true）。不要跑 `run_position_gate`，也不要 `send_digest`。每一步把 tool 回傳的 ok / exit_code / csv_paths / trade_date 貼出來。

預期順序：`fetch_chips` → `run_report_gate`。不要呼叫 `run_position_gate`、`draft_digest`、`send_digest`。

---

## 3. Inspector 表單（若用瀏覽器 Inspector，不是 Cursor）

連線步驟見 [`Phase1.md`](./Phase1.md) §5。`fetch_chips` 的 **Stocks** 是陣列：每一格只填一檔代號，例如 `2330`。不要填 `["2330"]`、`null`、`nu11`。

| 欄位 | 最小填法 |
|------|----------|
| Stocks 第 1 列 | `2330` |
| Trade Date | 空白（用最近交易日） |
| Lookback / Chart lookback | 預設 5 / 60 |
| Skip Major | 關 |

或開 **Edit as JSON**：

```json
{"stocks": ["2330"], "lookback_days": 5, "chart_lookback_days": 60, "skip_major": false}
```

接著 `run_report_gate`：`stock_id` `2330`，`skip_pdf` true。缺 CSV 則 `exit_code=20`。

---

## 4. 2026-09-10 Cursor 實測

Client：Cursor Agent + 專案 MCP（stdio）。交易日皆為 **2026-09-09**。無持股路徑：只 fetch + report-gate。

### 4.1 2330

**`fetch_chips`** `stocks: ["2330"]`

| 欄位 | 值 |
|------|-----|
| ok | `true` |
| exit_code | `0` |
| trade_date | `2026-09-09` |
| csv_paths | `reports/stock/2026-09-09/tw_stock_2330.csv` |

**`run_report_gate`** `stock_id: 2330`，`skip_pdf: true`，`trade_date: 2026-09-09`

| 欄位 | 值 |
|------|-----|
| ok | `true` |
| exit_code | `0` |
| trade_date | `2026-09-09` |
| csv_path | `reports/stock/2026-09-09/tw_stock_2330.csv` |

產物：`tw_stock_2330.md`（另有 `.facts.json`）。未跑 position／digest。

### 4.2 3711

**`fetch_chips`** `stocks: ["3711"]`

| 欄位 | 值 |
|------|-----|
| ok | `true` |
| exit_code | `0` |
| trade_date | `2026-09-09` |
| csv_paths | `reports/stock/2026-09-09/tw_stock_3711.csv` |

**`run_report_gate`** `stock_id: 3711`，`skip_pdf: true`，`trade_date: 2026-09-09`

| 欄位 | 值 |
|------|-----|
| ok | `true` |
| exit_code | `0` |
| trade_date | `2026-09-09` |
| csv_path | `reports/stock/2026-09-09/tw_stock_3711.csv` |

產物：`tw_stock_3711.md`。未跑 `run_position_gate`、`draft_digest`、`send_digest`。

這兩次證明：Cursor 掛上後，模型依 tool description 自己排「先 CSV 再 gate」；無持股演示不必碰 Notify。

---

## 5. 面試 30 秒

Settings MCP 綠燈 → 貼 §2.3 prompt → 核准兩個 tool → 指著 chat 裡的 `fetch_chips` / `run_report_gate` 與 `ok`／`exit_code` → 打開剛寫的 `tw_stock_{代碼}.facts.json`（有 `.gate.log` 再指一輪 `issue_codes`）。

這是現場主線第一段，不是暖身。接著指 gate 產物，再接到當天共用盤前 brief（Phase 4）。**不要**接著跑 `python main.py agent` 處理持股、`--replay` 或 `--role chat`——那些是路線完成條件，不當 live 戲份。見 [`agent-roadmap.md`](./agent-roadmap.md) §8。

收句：同一組 tools，網站走 HTTP，Cursor 走 MCP。職缺的 A2A 是另一個入口（`python main.py a2a`），不要在 MCP 演示結尾說「這就是 A2A」。見 [`a2a.md`](./a2a.md)、[`Phase5.md`](./Phase5.md)。
