---
name: market-daily
description: 台股開盤前戰術 brief：當日量價／三大法人／技術錨點／2330 + 那指費半 → agy 明日開盤偏誤（Phase 1，無夜盤）。
---

# Skill: Market Daily（開盤前戰術 brief）

## Description

台北時間**交易日 15:00 起**，`trade_date` 可切到當日（法人資料約此時可用）；此前仍對應**上一交易日**。  
**美股**依台北 **05:30** cutover：過了用最新已完成那指／費半 session；05:30 前仍用前一日美股。  
報告服務的是 `for_session`（下一台股交易日）開盤偏誤，不是單點漲跌預測。

Python 組 deterministic facts（加權量價、三大法人、MA／區間位置、2330、Yahoo 那指／費半日報酬），agy 寫短敘事，規則驗證閉環。

**Phase 1 不做**：台指夜盤、類股長排行、新聞長篇、縮小版週報。

## Command

```bash
# 依 cutover 解析日窗並產報
uv run --extra ui --extra stock python main.py market-daily

# 只看會產生哪一日
uv run --extra ui --extra stock python main.py market-daily --resolve-only

# 強制指定 trade_date（略過 cutover）
uv run --extra ui --extra stock python main.py market-daily --date 2026-07-31

# 只產 facts（不跑 agy）
uv run --extra ui --extra stock python main.py market-daily --skip-agy --skip-fetch

# 略過美股抓取
uv run --extra ui --extra stock python main.py market-daily --skip-us --skip-agy

# cutover 測試：交易日 14:59 → 上一交易日
uv run --extra ui --extra stock python main.py market-daily \
  --as-of 2026-07-31T14:59:00+08:00 --resolve-only

# 美股 cutover：05:29 → us_as_of 前一日；05:30 → 當日
uv run --extra ui --extra stock python main.py market-daily \
  --as-of 2026-08-04T05:29:00+08:00 --resolve-only
```

## Output

`reports/market/{trade_date}/tw_market_daily.{facts.json,md,summary.json,gate.log}`

## Grounded chat（Phase 2）

```bash
# 常見事實題（外資／偏誤等）走 facts_template，不需 Ollama
uv run python main.py market-daily-chat --date 2026-08-03 -m "今天外資怎麼做？" --dry-run --json

# 進場政策模板
uv run python main.py market-daily-chat --date 2026-08-03 -m "明天是否進場" --dry-run --json

# 外訊：優先 reports/us-tech/*_raw.json；--skip-tavily 略過付費搜尋
uv run python main.py market-daily-chat --date 2026-08-03 -m "有什麼科技新聞？" --dry-run --skip-tavily --json

# API（僅 SSE）
# POST /market-daily/chat/stream
# { message, facts?, summary?, markdown?, trade_date?, has_holdings, holdings?, skip_tavily? }
# events: meta / token / done / error
```

環境變數：
- `OLLAMA_BASE_URL`（預設 `http://127.0.0.1:11434`）、`OLLAMA_MODEL`（預設 `llama3.1`）— 長尾 factual／外訊潤飾
- `TAVILY_API_KEY`、`TAVILY_DAILY_LIMIT`（預設 5）— RSS 無命中時才用；計數在 `reports/market/_chat_quota/`

報告站：`POST /api/market-daily/[id]/chat`（SSE，別名 `/chat/stream`）；UI 逐字顯示 LLM 回覆。
