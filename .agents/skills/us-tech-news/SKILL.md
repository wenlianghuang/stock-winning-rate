---
name: us-tech-news
description: 免費 RSS 彙整美股科技、那斯達克與費城半導體新聞，agy 繁中市場日報、Markdown / slide PDF。
---

# Skill: US Tech News（美股科技市場日報）

## Description

以免費 RSS（無 API Key）彙整近 48 小時科技與市場新聞，並抓取道瓊（`^DJI`）、那斯達克（`^IXIC`）、費城半導體（`^SOX`）指數快照，再交給 agy 產出繁體中文市場日報。

流程：

1. 多來源 RSS 抓取與去重
2. Yahoo Finance 公開端點取得指數快照（無 API Key）
3. agy 分析 → **Python 規則驗證** → 不合格則修正（loop engineering，預設最多 2 輪）
4. 程式化產生指數表、走勢圖、新聞表
5. 輸出 Markdown / 橫式 slide HTML / PDF

## Setup

```bash
uv sync --extra tech
```

## Command

```bash
# 產生今日市場日報
python3 .agents/skills/us-tech-news/process_report.py

# 強制重新產生
python3 .agents/skills/us-tech-news/process_report.py --force

# 只抓 RSS 與指數，不跑 agy
python3 .agents/skills/us-tech-news/process_report.py --skip-summary

# 自訂 agy 驗證輪數
python3 .agents/skills/us-tech-news/process_report.py --max-rounds 3

# 只驗證既有 summary.md
python3 .agents/skills/us-tech-news/process_report.py --validate-only

# 從既有 md 重新渲染 HTML / PDF（不跑 agy）
python3 .agents/skills/us-tech-news/process_report.py --render-slides

# 指定回溯 72 小時
python3 .agents/skills/us-tech-news/process_report.py --hours 72
```

## RSS 來源

編輯 `.agents/skills/us-tech-news/feeds.json` 可增刪 feed 或調整 `lookback_hours`。

預設含：Google News（Nasdaq / SOX / 半導體 / 大型科技）、CNBC Technology、TechCrunch、The Verge、Ars Technica、Yahoo Finance、MarketWatch。

## 輸出

目錄：`reports/us-tech/`

| 檔案 | 說明 |
|------|------|
| `YYYY-MM-DD_raw.json` | 結構化原始資料 |
| `YYYY-MM-DD_raw.txt` | agy 讀取用純文字 |
| `YYYY-MM-DD_summary.md` | 繁中市場日報 |
| `YYYY-MM-DD_summary.pdf` | 橫式 slide PDF（表格 + 圖表 + agy 分析） |
| `YYYY-MM-DD_summary.html` | 互動式 slide HTML（排版完整、可捲動/簡報模式） |
| `YYYY-MM-DD_assets/` | 走勢圖等 PNG 素材 |
| `YYYY-MM-DD.gate.log` | 各輪驗證 JSON 紀錄 |
| `YYYY-MM-DD.gate.rounds/` | 各輪 prompt / 正文 / validation |

## Validation

驗證項目（`validate_tech_report.py`）：

- 7 個必要 slide 主題
- slide 分隔（`---`）與 bullet 數量
- 指數收盤價、漲跌幅與 raw.json 一致
- 新聞來源引用數量
- 禁語（工作摘要、明確買賣建議）

## Chat UI

- `/tech` → 背景執行今日市場日報
- `/tech force` → 強制重跑
- `/tech max-rounds 3` → 自訂驗證輪數
- `/tech validate-only` → 只驗證既有報告

也可用自然語言，例如：「美股科技新聞日報」「那斯達克半導體新聞摘要」。

## Exit codes

- exit 0：驗證通過並寫入 `*_summary.md` / slide PDF
- exit 1：達 `--max-rounds` 仍驗證失敗，或 RSS / agy 錯誤
- agy 未安裝時應明確報錯
