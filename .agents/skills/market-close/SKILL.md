---
name: market-close
description: 台股收盤日報：17:30 後以大盤量價、全市場三大法人、七檔權值法人與當天新聞標題寫收盤總結。不含融資券、當沖、主力，不含美股。
---

# Skill: Market Close（收盤日報）

## Description

台北時間**交易日 17:30 起**，`trade_date` 切到當日；此前仍是上一交易日。  
服務的是**當天收盤**，不是下一交易日的開盤偏誤。開盤偏誤仍走 `market-daily`（15:00 切日、05:30 排程）。

Python 組 facts：

- 加權開高低收、日報酬、量相對五日均量（FinMind）
- 全市場三大法人買賣金額（FinMind，顯示用億元）
- 權值法人：2330、2317、2454、2308、2382、2881、2882（Yahoo 法人買賣，單位張）。不含 0050、006208
- 上述七檔當天 Yahoo 標題。沒有時間或不在報告日的稿不收

某一檔法人或大盤數字還沒到，facts 標成尚未公布，報告仍可產出。不讀融資券、當沖、主力。

## Command

```bash
uv run --extra ui --extra stock python main.py market-close
uv run --extra ui --extra stock python main.py market-close -- --resolve-only
uv run --extra ui --extra stock python main.py market-close -- --date 2026-10-29 --skip-agy
uv run --extra ui --extra stock python main.py market-close -- --as-of 2026-10-29T17:29:00+08:00 --resolve-only
```

排程腳本：`scripts/run_market_close.sh`（建議 `30 17 * * 1-5`）。

## Output

`reports/market/{trade_date}/tw_market_close.{facts.json,md,summary.json,gate.log}`
