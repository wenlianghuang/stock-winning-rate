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
