# Phase 0 — 收斂介面

規劃見 [`agent-roadmap.md`](./agent-roadmap.md) §4。日常下指令仍見 [`commands.md`](./commands.md)；本文件記錄概念、實際改了什麼、以及為什麼操作看起來沒變。

狀態：**已落地**（typed tool 層 + `/jobs` 與部分 CLI 走同一實作）。

---

## 1. 概念：先收口，不換作業

Phase 0 要解的問題不是「更好看的報告」，而是 **同一件事有兩套參數組裝**：

- CLI：`main.py` → `subprocess` → skill script → argparse → `run_gate()` / `main()`
- FastAPI：`stock_api.py` 自己拼 `--stocks`、`--date`、`--skip-pdf`，再 `subprocess` 同一支 script

若 Phase 1 的 MCP 再包一層 script，失敗模式會更脆（argv 編碼、cwd、stderr 擷取各搞一套）。

所以 Phase 0 只做一件事：

> 把「可被 Agent 呼叫」的能力收成穩定 Python function。CLI 與 API 都走這層。不發明新業務、不重寫 gate。

完成後人還是照 `commands.md` 跑 `stock-report` → `report-gate` → `position-gate`。差別在內部：網站 job 不再為同一件事維護第二套 CLI 參數。

意圖解析、MCP、多 Agent 交接、排程都是後面的 Phase，**不要**在這一層做。

---

## 2. 目標架構（本階段實際長成這樣）

```
使用者（commands.md / 網站）
        │
        ├─ CLI: python main.py stock-report|report-gate|…
        │         └─ in-process skill.main()  →  skill 核心 function
        │                                         ▲
        └─ HTTP: POST /jobs, GET /last-trading-date, POST /digest, …
                  └─ agent.tools.*  ──────────────┘
                                                  │
                         fetch_chip_report.run_fetch
                         report_gate.run_gate
                         position_gate.run_gate
                         market_daily_gate.run_gate
                         chip_signals.build_chip_facts
```

skills 不 import `agent`（避免循環）。`agent.tools` 包現有模組；skill CLI 的 `main()` 也改呼叫抽出來的核心（例如 `run_fetch`），這樣「單一實作」成立。

Phase 2 見 [`Phase2.md`](./Phase2.md)；Phase 3 見 [`Phase3.md`](./Phase3.md)；Phase 4 見 [`Phase4.md`](./Phase4.md)（`llm.py` + 05:30 共用盤前排程）。

---

## 3. 修改過程（做了什麼、為什麼）

### 3.1 抽出可呼叫的核心，而不是再包 script

`fetch_chip_report.py` 的抓取邏輯原本只活在 `main()`。抽成 `run_fetch(...)`，回傳 `FetchRunResult`（交易日、CSV 路徑）。CLI `main()` 只負責 argparse 與印 log。

順手修正輸出目錄：`stock_report_dir` 改以專案根目錄為準，不再依賴 `Path.cwd()`。從 repo 根執行時與以前相同。

`market_daily_gate.ensure_tsmc_csv` 改呼叫 `run_fetch`，不再為 2330 再開一支 subprocess。

### 3.2 新增 `agent/tools/` typed 包裝

每個 tool：dataclass 輸入／輸出、`ok` + `exit_code` + `error`。錯誤碼沿用 skill 語意：

| Code | 意義 |
|------|------|
| 0 | 成功 |
| 1 | 未通過或執行失敗（含 `send_digest` 未核准） |
| 2 | 參數錯誤 |
| 10 | 找不到 agy |
| 20 | CSV／資料不存在 |
| 21 | 找不到持股 |

模組對應：

| 檔案 | Tools |
|------|--------|
| `agent/tools/dates.py` | `get_last_trading_date` |
| `agent/tools/chips.py` | `fetch_chips`、`build_chip_facts` |
| `agent/tools/report.py` | `run_report_gate` |
| `agent/tools/position.py` | `run_position_gate`、`get_holdings` |
| `agent/tools/market.py` | `run_market_daily`、`answer_market_chat` |
| `agent/tools/digest.py` | `draft_digest`、`send_digest` |

`send_digest` **不寄信**。未帶 `approved=True` 回 `status=blocked`；即使核准，本 repo 也只標 `unsupported`（寄信仍屬 `stock-report-site`）。這是 roadmap 的人機臨界點，不是漏做。

`agent/tools/__init__.py` 用 `__getattr__` 延遲載入，避免 `import agent.tools` 一次拉進 pandas／agy／日報模組。

### 3.3 FastAPI：至少 `/jobs` 改走 function

這是 Phase 0 完成定義裡「建議的那一條路徑」。`_run_pipeline` 不再組 argv：

1. `fetch_chips`（含圖表 lookback）
2. `run_report_gate`
3. 若 `is_holding` → `run_position_gate`

一併改掉的 HTTP，避免同一能力又留 subprocess：

- `GET /last-trading-date` → `get_last_trading_date`
- `GET /stocks/{id}/chart` 缺 CSV 時 → `fetch_chips`
- `POST /digest` → `draft_digest`
- `POST /market-daily/jobs` 背景管線 → `run_market_daily`

**沒改**（仍 subprocess）：`portfolio-gate`、`market-weekly`。不在 Phase 0 第一批 tool 表裡。

對外 JSON 形狀維持相容，網站不必改。

### 3.4 CLI：同一批命令改 in-process

`main.py` 對 `stock-report`、`report-gate`、`position-gate`、`market-daily`、`market-daily-chat` 改 `importlib` 呼叫 skill `main(extra_args)`，不再開子行程。argparse、旗標、產物不變，所以 `commands.md` 的指令列仍然有效。

其餘命令（portfolio、週報、tech-news、api、校準工具）仍 subprocess。

### 3.5 測試

`tests/test_agent_tools.py` 覆蓋 roadmap 要求的最小集合：交易日解析、facts 寫檔、缺 CSV → exit 20、holdings 讀取、`send_digest` 未核准 blocked。`fetch_chips` 用假 `run_fetch` 測包裝層，不打網路。

跑：

```bash
uv run --extra stock --extra ui python -m unittest tests.test_agent_tools
```

---

## 4. 第一批 tools（對應現況，不發明新業務）

| Tool | 對應現況 | 副作用 |
|------|----------|--------|
| `get_last_trading_date` | `GET /last-trading-date` | 無 |
| `fetch_chips` | `stock-report` | 寫 CSV |
| `build_chip_facts` | `chip_signals.build_chip_facts` | 寫 `.facts.json` |
| `run_report_gate` | `report-gate` | 寫 md／gate log |
| `run_position_gate` | `position-gate` | 寫 position 產物 |
| `run_market_daily` | `market-daily` | 寫日報 |
| `get_holdings` | `holdings.json`／API 持倉欄 | 無 |
| `answer_market_chat` | `market_day_chat` | 無（或只讀） |
| `draft_digest` | `POST /digest` 的 agy 融合 | 無（只產草稿） |
| `send_digest` | report-site 寄信 | **需核准**；本 repo 不發信 |

呼叫例（給之後 MCP／測試用，不是新的日常 CLI）：

```python
from agent.tools.chips import FetchChipsInput, fetch_chips
from agent.tools.report import ReportGateInput, run_report_gate

fetch = fetch_chips(FetchChipsInput(stocks=["2330"]))
gate = run_report_gate(ReportGateInput(stock_id="2330", trade_date=fetch.trade_date, skip_pdf=True))
```

---

## 5. 對操作者：沒有新流程

Phase 0 **沒有** `python main.py agent`，也 **沒有** MCP。人還是：

```bash
uv run --extra stock python main.py stock-report -- --stocks 2330
uv run --extra ui --extra stock python main.py report-gate -- 2409 --skip-pdf
```

產物仍在 `reports/stock/{日期}/`、`reports/market/{日期}/`。

會「感覺不一樣」的是 Phase 2：一句話意圖 → plan → 自動選 tool。見 [`Phase2.md`](./Phase2.md)。

---

## 6. 動到的檔案

新增：

- `agent/__init__.py`
- `agent/tools/`（`_paths.py`、`_types.py`、`dates.py`、`chips.py`、`report.py`、`position.py`、`market.py`、`digest.py`、`__init__.py`）
- `tests/test_agent_tools.py`

修改：

- `.agents/skills/tw-stock-report/fetch_chip_report.py` — `run_fetch`、`main(argv)`、輸出根目錄
- `.agents/skills/market-daily/market_daily_gate.py` — `ensure_tsmc_csv` 改 call `run_fetch`
- `api/stock_api.py` — `/jobs`、圖表 fetch、交易日、digest、日報 job
- `main.py` — 上列五個命令 in-process

沒有改 gate 驗證規則、報告模板、網站 repo。

---

## 7. 完成定義對照

| 條件 | 結果 |
|------|------|
| CLI 與 FastAPI 至少一條路徑走 function | `main.py` 五個命令 in-process；`POST /jobs` 走 `agent.tools` |
| typed 輸入／輸出、exit code 沿用 | dataclass + `_types.py` |
| 新 tool 層單元測試（facts／date／缺 CSV） | `tests/test_agent_tools.py` |
| 不重寫 gate、不加新章節 | 只包 `run_gate` / `run_fetch` |

---

## 8. 下一階段

Phase 1 落地見 [`Phase1.md`](./Phase1.md)：`python main.py mcp` expose 本層 tools。建議用 inspector 或測試裡的最小 client 跑通 `fetch_chips(2330)` → `run_report_gate(2330)`。FastAPI 保留給網站。

本階段本身不是現場戲份；現場用 Phase 1 跑出來的 `.facts.json`／`.gate.log` 講 harness。整條現場主線見 [`agent-roadmap.md`](./agent-roadmap.md) §8。

「我先做了券商研究作業裡最難的一塊：數字由程式算、模型只寫敘事、不合格就打回。後來發現這還只是人觸發的 pipeline，所以把同一組能力收成 tool，再加上編排與核准——因為職缺要的是會自己跑流程、但不能自己寄信／下單的 Agent。」