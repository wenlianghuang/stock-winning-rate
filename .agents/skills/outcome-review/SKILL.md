---
name: outcome-review
description: >-
  個股判斷事後對帳。讀 eval-calls 已算好的 5 日超額與當天 chip_regime，
  由 agy 只依對帳表寫三節短文，再用規則驗證。不重算籌碼、不抓價格、
  不用 1 日或 3 日補準確率。模型沒過就只留規則統計。
---

# Skill: Outcome Review（事後對帳）

正文的章節與禁語在 `outcome-narrative`。本檔只說明怎麼跑。

對帳表來自 `tools/eval_calls.py`：`accumulation`＝偏多，`distribution`＝偏空，其餘＝中性。準確率只含 5 日超額已過 ±0.5% 的偏多／偏空。中性、未過門檻、尚未成熟不進分母。

未指定日期時，先用磁碟上的收盤補 5 日超額，再以最新收盤往前第 5 個交易日的判斷對帳（11/17 的收盤對的是 11/10，不是 11/17 當天）。`--date` 才改看指定判斷日，而且不會再往前移。5 日未成熟時短文只能寫還沒有可以計分的判斷。

每一筆有方向的列都附一句「人話」：判斷日的方向、個股與大盤各自的漲跌、比大盤強或弱、以及結算日不是新的方向。模型必須原句照抄，不能把正的超額改寫成結算日的新方向。

## Command

```bash
uv run python main.py outcome-review
uv run python main.py outcome-review -- --skip-agy
uv run python main.py outcome-review -- --date 2026-08-18 --stock 2330
uv run python main.py outcome-review -- --refresh
```

一句話走 orchestrator：`python main.py agent -- "上週個股判斷後來怎樣"`。它只呼叫 `run_outcome_review`，不重跑報告。

## Output

`reports/eval/outcome_review.{md,facts.json,gate.log}`。有指定日期或股號時，檔名會帶上那個範圍。模型兩輪仍未通過時，md 只留 `eval-calls` 的規則統計，並註明解讀未生成。
