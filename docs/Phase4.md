# Phase 4 — 類 RPA：05:30 共用開盤前 brief + 可替換 LLM

規劃見 [`agent-roadmap.md`](./agent-roadmap.md) §4。本文件記錄為什麼排程只跑大盤、美股失敗為什麼不略過、網站如何讀同一份產物。本階段是 cron 型類 RPA，**不是** Agent2Agent 協定，見 [`a2a.md`](./a2a.md)。

狀態：**已落地**（`python main.py schedule --once` + `GET /market-daily/current` + `agent/llm.py`）。

---

## 1. 概念：共用盤前，不夜跑每人持股

Phase 0–3 仍要人觸發。持股均價／張數／現股融資是私人的，一條全域 cron 會弄丟或串台。

開盤前 brief 不是：上一交易日台股量價／法人／2330 + 台北 **05:30** 後的那指／費半，服務 `for_session` 開盤偏誤。產物路徑本來就是共用的：

`reports/market/{trade_date}/tw_market_daily.{facts.json,md,summary.json}`

Phase 4 只補兩件事：

1. **類 RPA：** 05:30 後自己跑 `run_market_daily` 一次；已有含美股的 brief 就 skip。
2. **LLM adapter：** `agy` / Ollama 走 `agent/llm.py`；敘事可換，gate 與 facts 不換。

不做：每人持股 fan-out、自動寄信、自動下單、用 `--skip-us` 當失敗後備。排程也不是 A2A：沒有第二個 Agent runtime 用協定來觸發 brief。

---

## 2. 目標架構

```
台北 05:30（美股 overnight settle）
        │
        ▼
 python main.py schedule --once
        │  幂等：已有含美股的 brief → skip
        ▼
 run_market_daily（require_us；Yahoo 失敗重試 3 次）
        │
        ▼
 reports/market/{trade_date}/     ← 全站同一份
        │
        ▼
 stock-report-site 每位登入者讀 GET /market-daily
 （chat 仍可帶自己的持股問句）
```

---

## 3. 修改過程

### 3.1 `agent/schedule.py`

`decide_premarket_action`：

| 情況 | action |
|------|--------|
| 尚未過 05:30 | `skip_early` |
| 已有 md＋facts＋summary 且 `us.available` | `skip_exists` |
| 有舊 brief 但美股空 | `run`（重跑，不略過） |
| `--force` | `run` |

美股抓失敗：重試後仍失敗則 **exit 20**，不會寫成「成功但沒有那指／費半」。

### 3.2 美股重試（`market_day_signals.fetch_us_day_block_with_retry`）

預設 3 次、間隔 15 秒。`skip_us` 只留給手動測試。05:30 後 `run_gate` 預設 `require_us=True`。

### 3.3 `agent/llm.py`

`LLM_BACKEND=agy`（預設）或 `ollama`。`draft_digest` 與 market-daily `run_agy` 走 `complete()`。

### 3.4 網站

`market_dailies` 不再當來源。列表／詳情／chat 都讀 Agent 磁碟上的共用 brief。刪除回 403。手動「產一次」是後備，不是每人一份副本。

---

## 4. 對操作者

```bash
# 看這一輪會不會跑（不產報、不抓美股）
uv run --extra stock --extra ui python main.py schedule --once --dry-run

# cron / launchd：交易日（含週六，給下週一）05:30 後
uv run --extra stock --extra ui python main.py schedule --once

# 強制重跑
uv run --extra stock --extra ui python main.py schedule --once --force

# 本機常駐（每 5 分鐘檢查一次）
uv run --extra stock --extra ui python main.py schedule --loop --interval 300
```

crontab 例（台北已是系統時區時）：

```
30 5 * * * cd /path/to/stock-winning-rate && uv run --extra stock --extra ui python main.py schedule --once
```

換敘事引擎：`LLM_BACKEND=ollama`（另設 `OLLAMA_BASE_URL` / `OLLAMA_MODEL`）。

API：`GET /market-daily/current` 回傳日窗 + 是否 ready；`GET /market-daily` 的 items 帶 `shared` / `us_available`。

---

## 5. 動到的檔案

新增：`agent/llm.py`、`agent/schedule.py`、`docs/Phase4.md`、`tests/test_llm.py`、`tests/test_schedule.py`。

修改：`market_day_signals.py`、`market_daily_gate.py`、`agent/tools/market.py`、`agent/tools/digest.py`、`api/stock_api.py`、`main.py`、網站 `app/api/market-daily/**`、`TwMarketDailyDashboard.tsx`。

沒有改 gate 驗證規則、個股報告模板、持股 tool 契約。沒有接券商 API。

---

## 6. 完成定義對照

| 條件 | 結果 |
|------|------|
| 不用人逐步下 `market-daily` | `python main.py schedule --once` |
| 全站同一份 | 網站讀 Agent `reports/market/`，不再按 user 建列 |
| 美股失敗不略過 | 重試後失敗 → 不標 ready、排程非 0 |
| 幂等 | 已有含美股 brief → `skip_exists` |
| LLM 可換 | `LLM_BACKEND` + `complete()` |
| 持股仍手動 | 排程不跑 position / digest |

面試可以講：05:30 機器人產一份共用開盤 brief；美股掛了就重試，不會假裝沒有外盤；使用者進站看到同一份，部位分析仍跟人走。

現場這是主線第三段（MCP 2330 → gate 產物 → 本階段 brief）。不要先花時間跑 Phase 2／3 的 agent CLI，也不要把盤前排程講成 A2A。見 [`agent-roadmap.md`](./agent-roadmap.md) §8、[`a2a.md`](./a2a.md)。
