---
name: concept-news
description: 概念股題材新聞。產出查詢包、蒐集、去重並聚成事件表，再由 agy 只讀事件表寫一頁報告並用規則驗證。不讀籌碼。
---

# Skill: Concept News

概念新聞線。單位是 `concept_peers_universe.json` 裡的一個 theme。不讀籌碼 facts，不進概念股比較。

## 1. 題材合約

輸入 theme id，輸出 `theme_pack.json`：中文標籤、池內股號與股名、搜尋詞。沒有網路，也沒有模型。

- 成員只來自概念股池。
- 搜尋詞只來自 `theme_queries.json`，不能只有標籤。光通訊另含矽光子、800G、CPO。
- 搜尋詞不得等於池內股名或股號。

```bash
python3 .agents/skills/concept-news/theme_pack.py --theme optical_comms --stdout
```

不帶 `--stdout` 時寫到 `reports/concept-news/{theme}/theme_pack.json`。

## 2. 蒐集

只讀查詢包。Yahoo 與 SearXNG 都寫進同一種項目：標題、網址、來源、時間、摘要、`origin`（`yahoo` 或 `searxng`）、觸發它的查詢、初步對上的股號。

- Yahoo：池內每一檔，近 48 小時。這是掛名新聞。查詢欄是股號，對上的股號一定含這一檔；標題或摘要若出現其他成員股名，一併列入。
- SearXNG：只跑題材搜尋詞，`categories=news`、`language=zh-TW`、`time_range=day`。沒點到股名時 `matched_ids` 為空。位址用 `SEARXNG_URL` 或 `--searxng-url`。沒設定就略過，每一個題材詞在 `failures` 留一筆，欄位與成功時相同。

單筆失敗記在 `failures`，整包仍寫出。超過 48 小時的稿不收入。沒有時間的稿先留下；同一來源單次超過 15 則時保留較新的，沒有時間的排在後面。

Yahoo 客戶端用專案環境（需要 `requests`）：

```bash
uv sync --extra stock

# 寫到 reports/concept-news/optical_comms/YYYY-MM-DD_raw.json
uv run --extra stock python3 .agents/skills/concept-news/collect_news.py --theme optical_comms

# 只讀已寫好的查詢包
uv run --extra stock python3 .agents/skills/concept-news/collect_news.py --pack reports/concept-news/optical_comms/theme_pack.json

# SearXNG 還沒架好
uv run --extra stock python3 .agents/skills/concept-news/collect_news.py --theme panel --skip-searxng --stdout
```

## 3. 篩選與去重

只讀 raw 與查詢包。沒有網路，也沒有模型。規則順序：

1. 沒有標題、沒有時間，或時間不在 raw 的 `collected_at` 回溯窗內 → 丟掉。
2. 網域不在允許名單 → 丟掉。名單是公開資訊觀測站（`mops.twse.com.tw`、`mopsov.twse.com.tw`）、中央社（`cna.com.tw`）、經濟日報（`money.udn.com`）、工商時報（`ctee.com.tw`）、鉅亨（`cnyes.com`）、MoneyDJ（`moneydj.com`）、Yahoo（`tw.stock.yahoo.com`、`tw.news.yahoo.com`、`finance.yahoo.com`）。
3. 網址相同，或標題正規化後相同，合成一筆。留下較早的一則；時間相同時，名單越前面的網域越優先。
4. 點名只看池內股名是否出現在留下的那一則標題或摘要。不沿用 raw 的 `matched_ids`，正文裡的股號不算。沒點到名字的標成 `theme_only`。

產出 `YYYY-MM-DD_kept.json`。留下的有 `keep_reason`，丟掉的有 `drop_reason`。

```bash
python3 .agents/skills/concept-news/filter_news.py --theme panel --date 2026-10-28

python3 .agents/skills/concept-news/filter_news.py \
  --raw reports/concept-news/panel/2026-10-28_raw.json --stdout
```

## 4. 事件表

只讀 `YYYY-MM-DD_kept.json`。沒有網路，也沒有模型。同一則稿的改寫收成一件事；不確定就拆成兩件。

一則留下的稿先各自成一件事，再依序併入。要併進去，必須同時成立：

1. 被點名的股號集合相同。沒有點名的只和沒有點名的併。股號沿用 kept 欄位，不重新對股名。
2. 時間落在該事件最早一則的 18 小時以內。
3. 標題和事件裡每一則都相近，而且只對上這一件。相近是標題正規化後的字元二元組 Jaccard 至少 0.72，或較短標題被較長標題包含、而且只多 4 個字以內。短於 8 個字的標題不做法模糊合併。
4. 數字不同、否定詞「不」「沒」的數量不同，或兩邊各寫了相反的詞（上修／下修、調漲／調降、看好／看壞、樂觀／保守、成長／衰退、增持／減持、利多／利空、創高／創低、漲停／跌停），都不併。

代表標題是最早一則的原標題。時間相同時，沿用篩選層的網域優先序。事件鍵 `event_id` 是代表標題正規化後的文字。來源列出這一叢的每一則，帶標題、網址、媒體、時間、摘要。每件依代表時間標 `session`：台北 13:30 起為 `after_close`，09:00 至 13:30 前為 `intraday`，更早為 `premarket`。

沒有留下的稿也寫檔，`events` 為空陣列。

```bash
python3 .agents/skills/concept-news/cluster_events.py --theme panel --date 2026-10-28

python3 .agents/skills/concept-news/cluster_events.py \
  --kept reports/concept-news/panel/2026-10-28_kept.json --stdout
```

## 5. 報告與驗證

只讀 `YYYY-MM-DD_events.json`。agy 寫一頁三段：看法、收盤後、來源列表。不下買賣建議，不補事件表沒有的數字。沒有出現在該事件 `mentioned_ids` 的股號，不寫成被點名或被提到。

結論只寫報告日當天、台北時間 13:30 以後的收盤。看法是依照這些新聞寫出的判斷和結論：對題材代表什麼、不代表什麼，以及理由。看法不寫阿拉伯數字，也不照抄標題。前一日的收盤不當依據；方向對得上時，看法用一句話寫「違逆」或「順從」，結論仍以今日為準。對不上方向就不寫這兩個詞。「收盤後」只留稿子寫的事實，一件一行，不解釋差額、不換算、不寫意義。09:00 到 13:30 以前是盤中，更早是盤前，也不進「收盤後」和「來源列表」。不要逐則寫稿面抄了什麼、沒寫什麼。

驗證最多兩輪。沒過就把問題交回 agy 重寫。通過才寫 `YYYY-MM-DD_summary.md`。每一輪追加 `YYYY-MM-DD.gate.log`，該輪的 prompt、正文、驗證留在 `YYYY-MM-DD.gate.rounds/`。已有 summary 時不重跑，除非加 `--force`。

- 三個段落標題要在。
- 「收盤後」和「來源列表」的標題必須是報告日當天收盤事件的代表標題，或該事件的來源標題。引用則數只算代表標題，而且只算當天收盤。前一日的標題列進來，驗證不通過。
- 代表標題至少出現 `--min-citations` 則，預設 1。盤後事件少於這個數時，改為全部引用。
- 「收盤後」列出的股號必須屬於該事件的 `mentioned_ids`。這一段若出現差額、換算或意義（相差、折合、這代表等），驗證不通過。
- 看法若太短、含有阿拉伯數字，或照抄事件標題，驗證不通過。有前一日對照時，看法必須寫出「違逆」或「順從」其中正確的一個。
- 零事件時正文要出現「沒有通過篩選的新聞」。只有盤中或盤前時要出現「還沒有收盤後的新聞」。
- 禁語與美股日報相同。

```bash
python3 .agents/skills/concept-news/process_report.py --theme optical_comms

python3 .agents/skills/concept-news/process_report.py \
  --theme panel --date 2026-10-28 --validate-only
```

## 6. 和前一份比較

讀同一題材、日期較早的最近一份 `YYYY-MM-DD_events.json`。事件鍵是 `event_id`。鍵相同標成沿用，只在新檔出現標成新增，只在前一份出現標成未再出現。通過驗證的正文後面多一段「和前一份的差異」。沒有前一份、檔案讀不出來，或題材不同，就略過這段，不讓整份報告失敗。

```bash
python3 .agents/skills/concept-news/process_report.py --theme panel --date 2026-10-28
```

## 7. 入口

已有事件表就沿用，不重新蒐集。沒有事件表，或加上 `--force`，才跑查詢包、蒐集、篩選、事件表，再寫報告。台北時間 18:00 起才能把當天當成報告日；18:00 前預設仍是前一日，指定更晚的日期會拒絕。籌碼與其他報告仍是 21:30。第一版不寄信、不排程。

```bash
uv run --extra stock python main.py concept-news -- --theme panel
uv run --extra stock python main.py concept-news -- --theme panel --force
uv run --extra stock python main.py concept-news -- --theme panel --skip-summary
uv run --extra stock python main.py concept-news -- --theme panel --validate-only
```

FastAPI（`python main.py api`）手動跑一個題材，狀態可查。讀取端點只打開已寫好的 summary 與事件表。

- `GET /concept-news/themes`
- `GET /concept-news/reports?theme=panel`
- `GET /concept-news/reports/{theme}/{date}`
- `POST /concept-news/jobs`、`GET /concept-news/jobs/{job_id}`
- `POST /concept-news/ask`：答案只來自該日 `events.json`，對不上就明說

網站台股頁的「題材新聞」走上面這些讀取與提問端點。開啟頁面不會現搜。
