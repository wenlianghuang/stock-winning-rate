---
name: market-weekly
description: 台股市場週報：大盤 + 權值 + 證交所類股強弱 + 那指／費半對帳 → agy 總評與下週情境（週五 17:30 cutover）。
---

# Skill: Market Weekly（市場週報）

## Description

台北時間**週五 17:30 前**產出／對應的是**上一曆週**；**17:30 起**才是**本曆週**。  
Python 組 deterministic facts（FinMind 加權、權值池 CSV、TWSE MI_INDEX 類股、Yahoo 那指／費半週報酬），agy 寫敘事（含台美對帳），規則驗證閉環。

**篇幅重心在「五、下週情境推演」**：假日無交易、讀者要為週一做準備。須含最可能／次可能／尾部排序、結構連貫、觸發、週一開盤含義、可追蹤訊號、否決條件、週一決策儀表板。一～三保持精簡。

美股對齊規則：

- 使用該 TW 曆週的 **Monday～Friday**（`WeekWindow.week_monday`～`week_friday`），不是 TWSE `trading_days` 列表。
- 週報酬 = 區間內第一個／最後一個有收盤的美股 session（假日可少於 5 日）。
- 指數：`^IXIC`（那斯達克）、`^SOX`（費半）；道瓊不納入 v1。
- 抓取失敗時 `us.available=false`，整份週報仍可產出。

## Command

```bash
# 依 cutover 解析週窗並產報
uv run --extra ui --extra stock python main.py market-weekly

# 只看會產生哪一週
uv run --extra ui --extra stock python main.py market-weekly --resolve-only

# 強制指定週（略過 cutover）
uv run --extra ui --extra stock python main.py market-weekly --week-end 2026-07-31

# 只產 facts（不跑 agy）
uv run --extra ui --extra stock python main.py market-weekly --skip-agy --skip-fetch

# 略過那指／費半抓取（離線）
uv run --extra ui --extra stock python main.py market-weekly --skip-us --skip-agy

# cutover 測試：週五 17:29 → 上週
uv run --extra ui --extra stock python main.py market-weekly \
  --as-of 2026-07-31T17:29:00+08:00 --resolve-only
```

## Output

`reports/market/{week_end}/tw_market_weekly.{facts.json,md,summary.json,gate.log}`

`facts.us` 含指數週報酬、`alignment`（那指vs大盤／費半vs半導體類）、`gaps`。
