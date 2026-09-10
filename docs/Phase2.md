# Phase 2 — Orchestrator（自主決策）

規劃見 [`agent-roadmap.md`](./agent-roadmap.md) §4。人若要逐步產報仍見 [`commands.md`](./commands.md)；本文件記錄：為什麼要 Orchestrator、意圖怎麼變成 plan、policy 擋什麼、以及怎麼跑三個 golden 意圖。

狀態：**已落地**（`python main.py agent` + fixture 測試）。

---

## 1. 概念：人給意圖，規則選 tool

Phase 0 把能力收成 `agent.tools`。Phase 1 用 MCP 暴露同一組 tools，但 **client 還要自己排順序**（先 fetch 再 report-gate；無持股不要 position）。

Phase 2 要解的是：使用者不再下 `/gate 2409` 或 `/position 2409 32.5 500`，只說一句話。

> Orchestrator 把意圖編成結構化 plan（tool 序列 + 參數）。Plan 通過 schema 與 policy 才執行。模型不能自己寄信、不能對無持倉跑部位、也不能無限互叫。

LLM 在這一層 **只允許** 填 plan JSON（可選 `--plan-json`）。預設 planner 是規則，不呼叫 agy／Ollama——golden 測試與 `--dry-run` 不需要模型。Phase 4 的 `llm.py` adapter 再接可替換 backend；現在不要把編排綁死 agy。

不重寫 gate。`max_rounds` 仍交給既有 `report-gate`／`position-gate`；Orchestrator 只在 CSV 缺失（exit 20）時插入一次 `fetch_chips` 再重試。

---

## 2. 目標架構（本階段實際長成這樣）

```
使用者：python main.py agent -- "幫我處理今天持股"
        │
        ▼
┌───────────────────┐
│ classify_intent   │  規則：process_holdings / stock_decision / market_outlook
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ build_plan        │  tool 序列 + 參數（可改由 --plan-json 提供）
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ policy            │  schema、無持倉不 position、send 需核准、缺 CSV 先 fetch、budget
└─────────┬─────────┘
          ▼
     agent.tools.*   ◀── 與 Phase 0 / MCP / FastAPI 同一實作
```

MCP server **沒有刪**。Cursor／Inspector 仍可單點呼叫 tools；Orchestrator 是「一句話」這條路。

尚未建立（留給 Phase 4）：`llm.py` adapter、排程。Phase 3 已落地：`audit.py` JSONL、角色 allowlist（見 [`Phase3.md`](./Phase3.md)）。

---

## 3. 修改過程（做了什麼、為什麼）

### 3.1 `agent/policy.py`：規則寫成可測函數

Roadmap 五條不要交給模型：

| # | 規則 | 實作 |
|---|------|------|
| 1 | 缺資料先 Data，再 Research | plan 階段插入 `fetch_chips`；執行時若 gate 回 exit 20，再 fetch 一次後重試 |
| 2 | 無持倉不跑 Position | `allow_position`：要有均價且股數 > 0 |
| 3 | gate 失敗交回同一 specialist | 不重開第二層 loop；把 `max_rounds` 傳進既有 gate。CSV 缺失才由 Orchestrator 補 Data |
| 4 | `send_digest` 預設 blocked | plan **不含** send；草稿狀態 `pending_approval`。LLM plan 裡的 `approved=true` 也不算數 |
| 5 | tool budget | 預設 32 次／1800 秒；用盡則後面步驟 SKIP |

Position 還多一條執行期防護：同一檔 `run_report_gate` 沒通過，就不跑 `run_position_gate`（Phase 3「先有 Research facts」的薄版）。

### 3.2 `agent/orchestrator.py`：意圖 → plan → 執行

三個意圖種類（對應 roadmap 例子與 golden fixtures）：

| 種類 | 例子 | Plan |
|------|------|------|
| `process_holdings` | 幫我處理今天持股 | 讀 holdings → 缺 CSV 就 fetch → 每檔 report-gate → 有均價／張數才 position-gate → `draft_digest`（不寄） |
| `stock_decision` | 2330 要不要動 | 有持倉走 position；沒有只走 research，notes 說明為什麼 |
| `market_outlook` | 明天開盤怎麼看 | 沒有日報 facts 就 `run_market_daily`，然後 `answer_market_chat` |

分類是關鍵字 + 四碼股票代號，不是 LLM。Ambiguous 句子（沒有持股／開盤／代碼）落到 `market_outlook`，走 grounded chat。

執行前會做便宜的 probes：`get_last_trading_date`（若沒 `--date`）、`get_holdings`。Plan 本身只列「還要做的事」，避免把已讀過的 holdings 再跑一遍。

`--plan-json` 讓外部模型填同一份 schema。未知 tool 直接拒絕（exit 2），不會執行。通過 schema 後仍跑 `apply_policy`，所以模型不能靠 JSON 偷跑 `send_digest` 或對 2330 塞 `run_position_gate`。

### 3.3 `python main.py agent`

與 `mcp` 一樣走 in-process，不進 `COMMANDS` 那張 script 表。

| 旗標 | 用途 |
|------|------|
| `--dry-run` | 只印 plan；仍讀 holdings／看 CSV 在不在，不跑 fetch／gate／digest |
| `--date` | 指定交易日，略過交易日 probe |
| `--holdings` | holdings.json 路徑 |
| `--json` | 機器可讀的整次 run |
| `--plan-json` | 外部 plan（`-` = stdin） |
| `--max-calls` / `--max-elapsed` | tool budget |
| `--tavily` | 才允許 chat 走付費搜尋；預設 `skip_tavily` |

沒有 `--approve-send`。寄信仍屬 `stock-report-site`，與 Phase 0 `send_digest` 行為一致。

### 3.4 測試：三個 golden 意圖用 fixture

`tests/fixtures/agent/`：

- `process_holdings.json` — 2409 有均價／張數、CSV 缺失 → fetch + report + position + draft；禁止 send
- `stock_no_holding.json` — 「2330 要不要動」、holdings 只有 2409 → research、禁止 position
- `market_outlook.json` — 「明天開盤怎麼看」→ daily + chat
- `bad_plan.json` — 未知 tool，CLI 拒絕

執行測試用 fake dispatch，不打網路、不跑 agy。另外覆蓋：policy 拔掉自核准 send、執行時缺 CSV 重試一次、budget 用盡、report 失敗則跳過 position。

跑：

```bash
uv run --extra stock --extra ui python -m unittest tests.test_orchestrator tests.test_agent_tools
```

---

## 4. Plan schema（LLM 若要填，只能填這個）

```json
{
  "intent": "2330 要不要動",
  "intent_kind": "stock_decision",
  "trade_date": "2026-08-18",
  "steps": [
    {"tool": "fetch_chips", "args": {"stocks": ["2330"]}, "reason": "缺 CSV"},
    {"tool": "run_report_gate", "args": {"stock_id": "2330", "skip_pdf": true}, "reason": "research"}
  ],
  "notes": ["2330 無持倉，不跑 position-gate"],
  "digest_status": "not_applicable"
}
```

`intent_kind` 只允許 `process_holdings` / `stock_decision` / `market_outlook`。`tool` 必須是 Phase 0／1 那 10 個名字。`args` 是 object。驗證在 `validate_plan_schema`；失敗不執行。

---

## 5. 對操作者

逐步產報（`stock-report` → `report-gate`）仍然有效。新的是一句話：

```bash
uv run --extra stock --extra ui python main.py agent -- "幫我處理今天持股"
```

先看會跑什麼、不碰網路／agy：

```bash
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "幫我處理今天持股"
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "2330 要不要動"
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "明天開盤怎麼看"
```

實際執行會寫既有產物路徑：`reports/stock/{日期}/`、`reports/market/{日期}/`。CLI 會印 plan、每步 tool、gate 通過與否（PASS／FAIL）、以及 `digest=pending_approval`。信不會寄出。

本機 `--dry-run` 對「幫我處理今天持股」的樣子（holdings 範例只有 2409；該日若已有 CSV 就不會列入 fetch）：

```
意圖: 幫我處理今天持股
種類: process_holdings
交易日: 2026-08-18
模式: dry-run（只列 plan，不跑 fetch／gate／digest）

Plan:
  1. run_report_gate  …
  2. run_position_gate  … from_holdings_file=True
  3. draft_digest  …
Notes:
  - send_digest 預設 blocked，草稿待核准
```

「2330 要不要動」的 plan 只有 `run_report_gate`，notes 寫無持倉所以不跑部位。

---

## 6. 動到的檔案

新增：

- `agent/policy.py`
- `agent/orchestrator.py`
- `tests/test_orchestrator.py`
- `tests/fixtures/agent/*.json`
- `docs/Phase2.md`

修改：

- `main.py` — `agent` 子命令
- `agent/__init__.py` — 套件說明
- `docs/commands.md` — Orchestrator 速查
- `docs/agent-roadmap.md` — Phase 2 指向本文件
- `docs/Phase0.md`、`docs/Phase1.md` — 下一階段連結

沒有改 gate 驗證規則、報告模板、MCP tool 清單、FastAPI JSON、網站 repo。

---

## 7. 完成定義對照

| 條件 | 結果 |
|------|------|
| CLI：`python main.py agent -- "幫我處理今天持股"` | `main.py` → `agent.orchestrator.main` |
| 印出 plan、每步 tool、gate 通過與否 | `format_run`：Plan / 執行 PASS·FAIL / digest 狀態 |
| 至少 3 個 golden 意圖有 fixture | `tests/fixtures/agent/` + `tests/test_orchestrator.py` |
| 缺資料先 Data | plan 插入 fetch；執行期 exit 20 再補一次 |
| 無持倉不跑 Position | 2330 fixture；policy 會拆掉 LLM 塞進來的 position |
| `send_digest` 預設 blocked | 規則 plan 不含 send；`approved=true` 也被拆掉 |
| tool budget | `--max-calls`；測試覆蓋用盡即停 |

---

## 8. 下一階段

Phase 3 落地見 [`Phase3.md`](./Phase3.md)：角色 allowlist、Research → Validator → Position 交接、JSONL audit 可重放；關掉 `send_digest` 權限時流程停在草稿。Phase 2 已經保證 send 預設 blocked；Phase 3 補上可重放的 log 與依角色的 allowlist。

面試可以講：人只說「處理今天持股」，程式列出 plan、有持倉才跑部位、信停在待核准；模型不能改這幾條規則。
