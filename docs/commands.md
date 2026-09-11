# 執行命令速查

專案根目錄執行。統一入口是 `main.py`；細節見各 skill 的 `SKILL.md`。

```bash
uv sync --extra stock --extra ui --extra server --extra tech --extra mcp --extra a2a
python main.py --list
```

需要 agy 的命令：安裝 Antigravity CLI，或設定 `AGY_BIN`。FinMind 可選 `FINMIND_TOKEN`。

---

## 個股籌碼 → 深度報告 → 部位

先抓 CSV，再產報告。產物都在 `reports/stock/{日期}/`。

```bash
# 抓籌碼（預設讀 .agents/skills/tw-stock-report/watchlist.txt）
uv run --extra stock python main.py stock-report
uv run --extra stock python main.py stock-report -- --stocks 2330,2409 --date 2026-08-18
uv run --extra stock python main.py stock-report -- --skip-major

# 深度報告（CSV 必須已存在）
uv run --extra ui --extra stock python main.py report-gate -- 2409
uv run --extra ui --extra stock python main.py report-gate -- 2409 --date 2026-08-18
uv run --extra ui --extra stock python main.py report-gate -- 2409 --validate-only
uv run --extra ui --extra stock python main.py report-gate -- 2409 --max-rounds 2 --skip-pdf

# 持股部位（代碼 均價 張數）
uv run --extra ui --extra stock python main.py position-gate -- 2409 32.5 500
uv run --extra ui --extra stock python main.py position-gate -- 2409 32.5 500 --date 2026-08-18
uv run --extra ui --extra stock python main.py position-gate -- --all-holdings
```

主要產物：

- `tw_stock_{代碼}.csv` / `_history.csv` — 籌碼快照
- `tw_stock_{代碼}.md` / `.facts.json` / `.gate.log` / `.gate.rounds/` — report-gate
- `tw_stock_{代碼}_position.md` / `.position.facts.json` / `.position.gate.*` — position-gate

---

## 開盤前日報 / 週報

產物在 `reports/market/{日期}/`。

```bash
# 日報（交易日 15:00 起切到當日）
uv run --extra ui --extra stock python main.py market-daily
uv run --extra ui --extra stock python main.py market-daily -- --date 2026-08-13
uv run --extra ui --extra stock python main.py market-daily -- --resolve-only
uv run --extra ui --extra stock python main.py market-daily -- --skip-agy --skip-fetch

# Phase 4：05:30 後產一份全站共用 brief（美股失敗會重試，不略過）
uv run --extra stock --extra ui python main.py schedule --once --dry-run
uv run --extra stock --extra ui python main.py schedule --once
```

# 週報（週五 17:30 cutover）
uv run --extra ui --extra stock python main.py market-weekly
uv run --extra ui --extra stock python main.py market-weekly -- --week-end 2026-07-31
uv run --extra ui --extra stock python main.py market-weekly -- --resolve-only
# crontab：30 17 * * 5  … scripts/run_market_weekly.sh

# 日報 grounded chat（讀既有 facts，不重跑 gate）
uv run python main.py market-daily-chat -- --date 2026-08-13 -m "今天外資怎麼做？" --dry-run --json
```

---

## 投資組合

產物在 `reports/portfolio/{日期}/`。候選池：`.agents/skills/portfolio-gate/portfolio_universe.json`、`portfolio_theme_universe.json`。

```bash
# 規則組倉（不呼叫 agy）
uv run --extra ui --extra stock python main.py portfolio-build
uv run --extra ui --extra stock python main.py portfolio-build -- balanced --date 2026-07-31 --amount 300000
uv run --extra ui --extra stock python main.py portfolio-build -- --mode theme --themes financials,thermal

# agy 敘事 + 驗證閉環
uv run --extra ui --extra stock python main.py portfolio-gate -- balanced
uv run --extra ui --extra stock python main.py portfolio-gate -- --mode theme --themes financials --skip-pdf
```

基本面快取：`reports/fundamentals/{日期}.json`。配額緊張可加 `--skip-fundamentals`。

---

## 美股科技新聞

產物在 `reports/us-tech/`。

```bash
uv run --extra tech python main.py tech-news
uv run --extra tech python main.py tech-news -- --force
uv run --extra tech python main.py tech-news -- --skip-summary
uv run --extra tech python main.py tech-news -- --validate-only
uv run --extra tech python main.py tech-news -- --hours 72
```

---

## MCP（給 Agent，不是日常產報）

細節見 [`Phase1.md`](./Phase1.md) §5。Cursor 掛 MCP、演示 prompt、2330／3711 實測見 [`mcp-cursor.md`](./mcp-cursor.md)。兩種連法擇一：stdio 由 Inspector／Cursor 拉起 process；streamable-http 才需要你先開 HTTP，且不要用瀏覽器打開 `/mcp`。網站仍走 `python main.py api`。這是 MCP（Agent → 工具）；Agent → Agent 見下一節與 [`a2a.md`](./a2a.md)。

```bash
uv run --extra mcp --extra stock --extra ui python main.py mcp --list-tools
```

---

## Agent2Agent（給另一個 Agent，Phase 5）

細節見 [`Phase5.md`](./Phase5.md)。用語對照見 [`a2a.md`](./a2a.md)。這是獨立 HTTP process：Card + JSON-RPC task，被叫到仍走 orchestrator／`agent.tools`。不是把 Phase 2／3 的角色拆成多個 server。預設埠 `9999`。演示用 `--dry-run`，避免一接上就跑 agy。

```bash
uv run --extra a2a --extra stock --extra ui python main.py a2a --print-card
uv run --extra a2a --extra stock --extra ui python main.py a2a --list-skills
uv run --extra a2a --extra stock --extra ui python main.py a2a --dry-run --date 2026-08-18
```

Card：`http://127.0.0.1:9999/.well-known/agent-card.json`。JSON-RPC：`POST http://127.0.0.1:9999/`（method `SendMessage`，建議 header `A2A-Version: 1.0`）。

要像 MCP Inspector 那樣有畫面：另開 `python main.py a2a-inspector`，Connect 填 `http://127.0.0.1:9999`。步驟見 [`a2a-inspector.md`](./a2a-inspector.md)。

```bash
uv run --extra a2a python main.py a2a-inspector --howto
```

---

## Orchestrator（一句話意圖，Phase 2–3）

細節見 [`Phase2.md`](./Phase2.md)、[`Phase3.md`](./Phase3.md)。不必再指定 `/gate` 或 `/position`。`send_digest` 權限預設關閉，只產草稿並標待核准。實際執行會寫 `reports/agent/{日期}/run_{id}.jsonl`。

這是單 process 的意圖編排與角色 allowlist，**不是** Agent2Agent 協定。A2A 入口是 `python main.py a2a`，見 [`Phase5.md`](./Phase5.md)、[`a2a.md`](./a2a.md)。

現場 10–15 分鐘不當主線；主線是 MCP 2330 → gate 產物 → 盤前 brief，見 [`agent-roadmap.md`](./agent-roadmap.md) §8。

```bash
# 處理 holdings.json 裡有均價／張數的標的（fetch → report-gate → position-gate → digest 草稿）
uv run --extra stock --extra ui python main.py agent -- "幫我處理今天持股"

# 只列 plan，不跑 fetch／agy
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "幫我處理今天持股"

# 無持倉：只 research，並說明為什麼沒跑部位
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "2330 要不要動"

# 開盤路況：日報（若尚無 facts）→ grounded chat（角色 chat，不得 send_digest）
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "明天開盤怎麼看"

# 演示 allowlist：chat 角色拆掉 report / position / draft
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 --role chat -- "幫我處理今天持股"

# 重放 audit（不執行 tools）
uv run --extra stock --extra ui python main.py agent --replay reports/agent/2026-08-18/run_<id>.jsonl
```

---

## 開盤前排程（Phase 4）

細節見 [`Phase4.md`](./Phase4.md)。這是全站共用 brief，不是每人持股。

```bash
uv run --extra stock --extra ui python main.py schedule --once --dry-run
uv run --extra stock --extra ui python main.py schedule --once
# crontab：30 5 * * 2-6  … schedule --once
# crontab：30 17 * * 5    … market-weekly
```

敘事引擎：`LLM_BACKEND=agy`（預設）或 `ollama`。

---

## API / Chat UI

```bash
uv run --extra server --extra ui --extra stock python main.py api
```

預設 `127.0.0.1:8765`（`STOCK_API_HOST` / `STOCK_API_PORT`）。Chat 指令：

```
/stock 2409
/gate 2409
/position 2409 32.5 500
/tech
```

---

## 回補與校準

`backfill_history.py` 不在 `main.py` 裡，需直接跑。

```bash
# 回補歷史快照 → reports/stock/{日期}/
python tools/backfill_history.py --stocks 2330,2409 --start 2026-04-07 --end 2026-08-18
python tools/backfill_history.py
# 事後標籤 → reports/outcomes/outcomes.jsonl
uv run python main.py outcome-label

# 分位門檻 + regime base rate → reports/calibration/
uv run python main.py calibrate
uv run python main.py calibrate -- --stock 2409

# gate 品質統計（掃 reports/stock/**/*.gate.log）
uv run python main.py gate-stats
uv run python main.py gate-stats -- --date 2026-08-18 --stock 2330 --json
```

---

## 建議當日流程

```bash
uv run --extra stock python main.py stock-report -- --stocks 2330,2409
uv run --extra ui --extra stock python main.py report-gate -- 2330 --skip-pdf
uv run --extra ui --extra stock python main.py position-gate -- 2330 <均價> <張數> --skip-pdf
uv run --extra ui --extra stock python main.py market-daily
```
