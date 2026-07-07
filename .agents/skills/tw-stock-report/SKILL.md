---
name: tw-stock-report
description: 抓取台股個股籌碼面資料（收盤價、三大法人、融資融券、借券、當沖、主力進出）並輸出 CSV 表格。
---

# Skill: Taiwan Stock Chip Report

## Description
從 [FinMind](https://finmindtrade.com/) 拉取台股個股籌碼資料，並從 Yahoo 股市 SSR 補上主力進出，整理成**當日快照 CSV**與**近 N 日歷史 CSV**（預設 5 個交易日）。

涵蓋欄位：
- 開盤價、最高價、最低價、收盤價、成交量、漲跌幅
- 外資 / 投信 / 自營商買賣超（單位：張）
- 融資、融券餘額與增減（單位：張）
- 借券賣出、券賣還券（單位：張）
- 當沖成交量與佔比
- **主力買賣超_張**、**主力佔成交量_%**（Yahoo `broker-trading` API，歷史逐日）
- **主力_擷取狀態**（`ok` / `date_mismatch` / `fetch_failed` 等）
- **區間摘要**：累計法人/主力買賣超、融資券淨變化、成交量/當沖均值、區間漲跌幅、MA5、收盤偏離 MA5

成交量與買賣超等原為「股」的欄位，會以 **1 張 = 1000 股** 換算並四捨五入為整數。

## Setup

```bash
uv sync --extra stock
```

或：

```bash
pip install -r .agents/skills/tw-stock-report/requirements.txt
```

可選：設定 `FINMIND_TOKEN` 可提高 API 請求上限（非必要）。

```bash
export FINMIND_TOKEN=""   # 選填
```

## Command

預設讀取 `watchlist.txt`（台積電 2330、聯發科 2454）：

```bash
python3 .agents/skills/tw-stock-report/fetch_chip_report.py
```

指定股票與日期：

```bash
python3 .agents/skills/tw-stock-report/fetch_chip_report.py \
  --stocks 2409 \
  --date 2026-06-26
```

自訂回看天數（預設 5 個交易日）：

```bash
python3 .agents/skills/tw-stock-report/fetch_chip_report.py \
  --stocks 2409 \
  --lookback-days 10
```

僅 FinMind、不要 Yahoo 主力：

```bash
python3 .agents/skills/tw-stock-report/fetch_chip_report.py --skip-major
```

chat UI `/stock` 會執行完整流程（FinMind + Yahoo 主力 → agy 產出 .md / .pdf）。

## Output

目錄結構（每檔股票一組）：

```
reports/stock/{交易日}/
  tw_stock_2409.csv           # 當日快照 + 區間摘要欄位（單列）
  tw_stock_2409_history.csv   # 近 N 日逐日明細（N 列）
  tw_stock_2409.md            # agy 分析（chat UI / report-gate 產生）
  tw_stock_2409.pdf           # agy 分析 PDF
```

- 快照 CSV：當日完整籌碼 + 區間趨勢摘要（回看天數、累計買賣超、MA5 等）
- 歷史 CSV：每個交易日一列，欄位與當日相同（含主力）
- 新聞在 agy 分析時即時擷取，不另存檔案
- agy 失敗時的 debug log 寫入 `reports/.agy_logs/`（不在 stock 日期目錄內）

## Watchlist
編輯 `.agents/skills/tw-stock-report/watchlist.txt`，一行一個股票代碼。

## Notes
| 項目 | 說明 |
|------|------|
| 資料源 | FinMind REST API；主力進出來自 Yahoo 股市 API |
| 回看天數 | 預設 5 個**交易日**（非日曆日）；`--lookback-days` 可調整 |
| 單位 | 股數欄位換算為張（÷1000，四捨五入）；融資融券餘額本身即為張 |
| 更新時間 | 股價 ~17:30；法人/融資券 ~21:00；當沖 ~21:30；**主力進出 ~21:30** |
| 主力日期 | 歷史 CSV 逐日呼叫 Yahoo API；API 失敗時該日主力欄位留空 |
| 非交易日 | 週末或國定假日會自動改用最近一個台股交易日 |

## Validation
- 腳本 exit 0 且印出 `reports/stock/{date}/` 下的快照與歷史 CSV 路徑
- 每檔股票各一個 `tw_stock_{代碼}.csv` 與 `tw_stock_{代碼}_history.csv`
