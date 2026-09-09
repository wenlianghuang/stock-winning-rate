# 執行命令速查

專案根目錄執行。統一入口是 `main.py`；細節見各 skill 的 `SKILL.md`。

```bash
uv sync --extra stock --extra ui --extra server --extra tech
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

# 週報（週五 17:30 cutover）
uv run --extra ui --extra stock python main.py market-weekly
uv run --extra ui --extra stock python main.py market-weekly -- --week-end 2026-07-31
uv run --extra ui --extra stock python main.py market-weekly -- --resolve-only

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
