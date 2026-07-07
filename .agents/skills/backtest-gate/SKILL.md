---
name: backtest-gate
description: 台股策略回測閉環：布林通道+ADX+停損，train grid search → holdout 鎖一次終極大考 → 門檻判定 PASS/FAIL，防過擬合。
---

# Skill: Backtest Gate（策略回測閉環）

## Description

Loop Engineering 的量化交易版。四個核心組件各司其職：

| 組件 | 檔案 | 職責 |
|------|------|------|
| 眼睛 (Data Engine) | `data_engine.py` | FinMind 抓多年**還原股價**日K，快取 CSV |
| 指標 | `indicators.py` | Bollinger / ATR / ADX（Wilder 平滑） |
| 手腳 (Sandbox) | `strategy.py` | 事件驅動回測：布林+ADX 趨勢過濾+硬性停損，含台股成本、防 look-ahead |
| 教練 (Evaluator) | `metrics.py` | 年化報酬、最大回撤、Sharpe、Sortino、勝率、交易數 |
| 循環 (Harness) | `backtest_gate.py` | train grid search → holdout 只評一次 → 門檻判定 |

**策略邏輯（long-only）：**
- ADX > 門檻（趨勢盤）：突破上軌追買，跌破中軌出場。
- ADX <= 門檻（盤整盤）：跌破下軌承接，回中軌出場。
- 硬性停損：跌破進場價 × (1 − stop_loss_pct) 出場。

**防過擬合設計：** train（預設 2018–2023）可迭代優化；holdout（預設 2024–2026）**鎖住只評一次**。另有最低交易次數約束，避免少數幸運單筆撐起績效。

## Setup

```bash
uv sync --extra stock
```

可選：`export FINMIND_TOKEN=""` 提高 API 上限。

## Command

```bash
# 依預設 train/holdout 與門檻回測 2330
python3 .agents/skills/backtest-gate/backtest_gate.py 2330

# 自訂區間與門檻
python3 .agents/skills/backtest-gate/backtest_gate.py 2330 \
  --train 2016-01-01:2022-12-31 \
  --holdout 2023-01-01:2026-12-31 \
  --min-cagr 0.15 --max-drawdown 0.10 --min-sharpe 0.8 --min-trades 10

# 只抓/檢視資料
python3 .agents/skills/backtest-gate/data_engine.py 2330 --start 2018-01-01 --end 2026-12-31
```

專案入口：

```bash
uv run --extra stock python main.py backtest-gate -- 2330
```

## Exit Codes

| Code | 意義 |
|------|------|
| 0 | holdout 通過所有門檻 |
| 1 | holdout 未達標（樣本外失敗） |
| 2 | train 找不到符合約束的參數 |
| 20 | 資料不足 / 抓取失敗 |

## Output

- `reports/backtest/{代碼}/backtest_gate.json` — train 前 N 名候選、holdout 指標、買進持有基準、判定與失敗原因、逐筆交易
- `reports/backtest/cache/{代碼}_adj_{起}_{迄}.csv` — 價格快取

## 門檻（預設，可用參數覆寫）

- 年化報酬 (CAGR) > 15%
- 最大回撤 < 10%
- Sharpe >= 0.8
- 交易次數 >= 10

## 已知限制（實務注意）

- 單標的、long-only、滿倉；未含滑價與流動性/漲跌停限制（僅計手續費+證交稅）。
- grid search 仍可能對 train 過擬合；holdout 通過只是必要非充分條件，上線前應再做 walk-forward 與 paper trading。
- 「大腦 (LLM)」目前由 grid search 代理；若要接 agy 依 holdout 失敗原因改寫策略，可比照 `report-gate` 的迴圈另行擴充。
