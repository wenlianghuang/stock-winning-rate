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
