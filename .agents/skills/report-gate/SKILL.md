---
name: report-gate
description: 台股單檔 agy 深度報告品質閉環：產報 → 規則驗證 → 不合格則 agy 修正，最多 N 輪。
---

# Skill: Report Gate（報告品質閉環）

## Description

Loop engineering 工具：在 **CSV 已存在** 的前提下，用 agy 產出單檔台股深度報告，並以 Python 規則驗證章節與 CSV 數字一致性。未通過時將具體缺漏餵回 agy 修正，直到通過或達輪數上限。

分析須參考 **快照 CSV**（當日 + 區間摘要）與 **歷史 CSV**（近 N 日逐日明細）。

**不包含** 拉取籌碼資料；請先執行 `tw-stock-report`。

## Setup

```bash
uv sync --extra ui --extra stock
```

需要已安裝 **Antigravity CLI（agy）** 或設定 `AGY_BIN`。

## Command

```bash
# 依最新交易日 CSV 產報（最多 8 輪 agy）
python3 .agents/skills/report-gate/report_gate.py 2409

# 指定日期
python3 .agents/skills/report-gate/report_gate.py 2409 --date 2026-07-02

# 直接指定 CSV
python3 .agents/skills/report-gate/report_gate.py --csv reports/stock/2026-07-02/tw_stock_2409.csv

# 只驗證既有 .md，不呼叫 agy
python3 .agents/skills/report-gate/report_gate.py 2409 --validate-only

# 自訂輪數、略過 PDF
python3 .agents/skills/report-gate/report_gate.py 2409 --max-rounds 2 --skip-pdf
```

專案入口：

```bash
uv run --extra ui --extra stock python main.py report-gate -- 2409
```

Chat UI：`/gate 2409` 或 `/report-gate 2409`

## Exit Codes

| Code | 意義 |
|------|------|
| 0 | 驗證通過並寫入 `.md` / `.pdf` |
| 1 | 達最大輪數仍不通過 |
| 10 | 找不到 agy |
| 20 | CSV 不存在 |

## Output

- `reports/stock/{日期}/tw_stock_{代碼}.md`
- `reports/stock/{日期}/tw_stock_{代碼}.pdf`（除非 `--skip-pdf`）
- `reports/stock/{日期}/tw_stock_{代碼}.facts.json`（**harness 產物**：系統確定性計算的籌碼事實，餵給 agy 並作為事實驗證基準）
- `reports/stock/{日期}/tw_stock_{代碼}.gate.log`（每輪驗證 JSON 紀錄，含 `layers` 與 `issue_codes`）
- `reports/stock/{日期}/tw_stock_{代碼}.gate.rounds/`（各輪比較）
  - `index.md` — 輪次摘要與比較指引
  - `r01.body.md` / `r01.prompt.txt` / `r01.validation.json`
  - `r02.*` …（若有第二輪修正）

## Validation Criteria（分層 gate）

驗證分為三層，任一層失敗即進入下一輪 agy 修正：

1. **格式層（format）**：須含當日籌碼解讀、**近 N 日籌碼趨勢**（延續/轉折/背離）、新聞、交叉對照、短中線情境推演、觀察重點、免責聲明；表格/條列規範。
2. **事實層（facts）**：正文方向不可與 `facts.json` 矛盾——
   - `fact_foreign_direction` / `fact_ma5_position` / `fact_divergence_ignored`
   - `fact_ma20_position`：收盤相對 MA20（月線）站上/跌破敘述與 facts 矛盾
   - `fact_ma_alignment_mismatch`：短中線均線排列（MA5 vs MA20）敘述矛盾（含 short_rebound / short_pullback 型態）
   - `fact_volume_mismatch`：成交量放量/縮量敘述與 facts 矛盾
   - `fact_price_trend_mismatch`：區間價格趨勢（上漲/下跌）敘述與 facts 矛盾
   - `fact_chip_regime_mismatch`：籌碼型態（accumulation/distribution）敘述矛盾
   - `fact_institutional_mismatch`：三大法人共識矛盾
   - `fact_major_foreign_ignored`：主力外資背離未說明
   - `fact_market_rs_mismatch`：個股相對大盤（加權指數）強弱敘述與 facts 矛盾（例：弱於大盤卻寫抗跌）
   - `anchors_underused`：引用系統事實不足
3. **推理層（reasoning）**：推理鏈完整性——
   - `reasoning_cross_no_evidence`：交叉對照缺籌碼+新聞依據
   - `reasoning_scenario_no_trigger`：情境缺觸發條件
   - `reasoning_trend_no_continuation`：外資連續買賣但趨勢段未描述延續/轉折
   - `reasoning_watch_not_actionable`：觀察重點不可追蹤
   - `reasoning_news_uncited`：有新聞但未引用標題關鍵字

- **Harness：** agy 依 `facts.json`（含 v2：法人共識、chip_regime、主力外資背離、融資券型態等）撰寫敘事。
- **Loop：** `build_fix_prompt` 依 `issue_codes` 給出對應的修正指引，第二輪針對矛盾修正而非只補章節。
- **自主執行階段：** 驗證失敗時自動進入下一輪 agy 修正，無需人工確認。
- **人工介入（臨界點）：** 達 `--max-rounds` 仍失敗；或 exit 10 / 20。
- 預設 `MAX_ROUNDS = 8`。
