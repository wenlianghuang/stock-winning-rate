---
name: position-gate
description: 台股持股部位決策閉環：持股均價/張數 + CSV → agy 部位報告 → 規則驗證 → 修正，最多 N 輪。
---

# Skill: Position Gate（持股部位決策閉環）

## Description

在 **CSV 已存在** 的前提下，依使用者提供的**持股均價與張數**產出部位決策報告。
與 `report-gate`（市場觀察）分離：市場面客觀解讀 + 部位成本下的操作情境。

**不包含** 拉取籌碼資料；請先執行 `tw-stock-report`。

## Command（推薦：直接輸入持股）

```bash
# 簡寫：代碼 均價 張數
python3 .agents/skills/position-gate/position_gate.py 2409 32.5 500

# 長選項
python3 .agents/skills/position-gate/position_gate.py 2409 --avg-cost 32.5 --lots 500

# 指定日期
python3 .agents/skills/position-gate/position_gate.py 2409 32.5 500 --date 2026-07-03
```

專案入口：

```bash
uv run --extra ui --extra stock python main.py position-gate -- 2409 32.5 500
```

Chat UI：

```
/position 2409 32.5 500
/position 2409 32.5 500張
/position 2409@32.5@500
/hold 2409 均價 32.5 500
```

## 選用：holdings.json 整批排程

若已維護 `.agents/skills/position-gate/holdings.json`，可整批處理：

```bash
python3 .agents/skills/position-gate/position_gate.py --all-holdings
```

未傳均價/張數時，也會嘗試從 holdings.json 讀取該股（作為 fallback）。

## Exit Codes

| Code | 意義 |
|------|------|
| 0 | 驗證通過並寫入 `*_position.md` / `.pdf` |
| 1 | 達最大輪數仍不通過 |
| 10 | 找不到 agy |
| 20 | CSV 不存在 |
| 21 | 未提供持股或 holdings 無該股 |

## Output

- `reports/stock/{日期}/tw_stock_{代碼}_position.md`
- `reports/stock/{日期}/tw_stock_{代碼}_position.pdf`（除非 `--skip-pdf`）
- `reports/stock/{日期}/tw_stock_{代碼}.facts.json`（**harness 產物**：系統確定性計算的籌碼事實，餵給 agy 撰寫市場面並作為事實驗證基準）
- `reports/stock/{日期}/tw_stock_{代碼}.position.facts.json`（**harness 產物**：部位確定性試算——損益分桶、距損益兩平、均價 vs MA20、融資維持率／距追繳／壓力 zone、系統傾向）
- `reports/stock/{日期}/tw_stock_{代碼}.summary.json` 的 `position` 區塊含維持率欄位，供 report-site 融資卡顯示
- `*.position.gate.log` 每輪含 `layers`（format / facts / position / reasoning）與 `issue_codes`

## Validation Criteria（分層 gate）

與 `report-gate` 共用 `ui/fact_checks.py` 事實層與 `ui/reasoning_checks.py` 推理層；部位層由 `ui/position_signals.py` 提供：

1. **格式層**：部位現況、市場面摘要、交叉對照、操作情境（含觸發條件與方向）、風險提醒、免責聲明。
2. **事實層（共用）**：市場面方向不可與 `facts.json` 矛盾（含 chip_regime、法人共識、主力外資背離、個股相對大盤強弱 `fact_market_rs_mismatch`、收盤相對 MA20 月線 `fact_ma20_position`、短中線均線排列 `fact_ma_alignment_mismatch`、量能 `fact_volume_mismatch`、區間價格趨勢 `fact_price_trend_mismatch`）。
3. **部位決策層（分桶）**：依 `position.facts.json` 的損益分桶要求操作情境對齊，**確保有無持股/不同成本會產出不同結論**——
   - `position_scenario_unanchored`：操作情境未錨定損益（獲利/虧損/成本/均價/套牢）
   - `position_profit_no_protection`：大幅獲利（>15%）未談停利/移動停損/獲利了結或續抱理由
   - `position_profit_no_plan`：小幅獲利（3%~15%）未談加碼條件/停利/獲利回吐
   - `position_breakeven_no_trigger`：損益兩平（±3%）未給明確出場/加碼觸發條件
   - `position_loss_no_risk_control`：虧損（<-3%）未提停損/減碼/出場/攤平前提
   - `position_margin_no_risk`：融資部位未談追繳／斷頭／維持率或融資減碼
   - `position_maint_rate_mismatch`：正文維持率與系統試算相差逾 ±3pp
   - `position_call_distance_ignored`：融資壓力 tight/critical 卻未點出追繳線／距追繳／追繳價或正確維持率
   - `position_margin_pressure_unanchored`：接近追繳卻把技術反彈標成主線
4. **推理層（共用）**：交叉對照須有籌碼依據；操作情境須有觸發條件；外資連續買賣須描述延續/轉折。

- **Harness：** agy 依 `facts.json`（v2 含 chip_regime、法人共識、MA5/MA20 位置與短中線對齊等）與 `position.facts.json`（損益分桶、均價 vs MA20、融資維持率／距追繳／壓力 zone）撰寫報告。
- **Loop：** `build_fix_prompt` 依 `issue_codes` 給對應修正指引，並回饋部位狀態摘要。
- **分桶門檻：** 於 `ui/position_signals.py` 頂部常數（`PROFIT_LARGE_PCT` 等）可調整停利/停損比例。
