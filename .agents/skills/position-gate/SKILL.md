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
- `*.position.gate.log` 每輪含 `layers`（format / facts）與 `issue_codes`

## Validation Criteria（分層 gate）

與 `report-gate` 共用 `ui/fact_checks.py` 事實層：

1. **格式層**：部位現況、市場面摘要、交叉對照、操作情境（含觸發條件與方向）、風險提醒、免責聲明。
2. **事實層（共用）**：市場面方向不可與 `facts.json` 矛盾（`fact_foreign_direction` / `fact_ma5_position` / `fact_divergence_ignored` / `anchors_underused`）；已排除新聞表格、且「當日買、區間賣」等混合敘述不誤判。
3. **部位決策層**：`position_loss_no_risk_control` — 未實現虧損逾 8% 時，操作情境須提出停損/減碼/出場等具體防禦手段。

- **Harness：** agy 不再直接讀 CSV，改依 `facts.json` 撰寫市場面。
- **Loop：** `build_fix_prompt` 依 `issue_codes` 給對應修正指引。
