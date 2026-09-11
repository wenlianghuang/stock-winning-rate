# A2A Inspector：連本機 Agent2Agent server

協定與 Card 見 [`Phase5.md`](./Phase5.md)。用語見 [`a2a.md`](./a2a.md)。本文件記錄：Inspector 跟 Cursor MCP 差在哪、兩個 terminal 怎麼開、Connect URL 填什麼。

狀態：**操作說明已落地**（`python main.py a2a-inspector`）。Inspector 本體是官方 repo 的 clone，不進本 git 樹。

---

## 1. 概念：這是 A2A 的 MCP Inspector，不是 Cursor

| | MCP | A2A |
|--|-----|-----|
| Server | `python main.py mcp` | `python main.py a2a`（HTTP `:9999`） |
| Debug UI | `npx @modelcontextprotocol/inspector` | `python main.py a2a-inspector`（官方 [a2a-inspector](https://github.com/a2aproject/a2a-inspector)，本機 `:5001`） |
| 日常 Agent client | **Cursor**（`.cursor/mcp.json`） | **沒有。** Cursor 不講 A2A |
| 人在 UI 做什麼 | 點 tool、填 `stocks: ["2330"]` | Chat 丟一句「2330 要不要動」；plan 由 orchestrator 排 |

Inspector 頁是除錯／演示 client，不是 `stock-report-site`。網站仍走 FastAPI。

A2A 這條**要**自己先開 server。跟 Cursor MCP 相反：MCP stdio 由 Cursor fork；A2A HTTP 由你佔 `:9999`。

---

## 2. 兩個 terminal

你現在若已經在跑 `python main.py a2a`，不要再開第二個 A2A server。另開 Inspector 即可。

```bash
# terminal 1：A2A server（保持開著）
# 真跑產報：不要加 --dry-run
uv run --extra a2a --extra stock --extra ui python main.py a2a

# terminal 2：Inspector（第一次會 clone + npm build，之後較快）
uv run --extra a2a python main.py a2a-inspector
```

只想看步驟、不啟動 UI：

```bash
uv run --extra a2a python main.py a2a-inspector --howto
```

stderr 會印 `A2A Inspector UI 127.0.0.1:5001`。瀏覽器開：

`http://127.0.0.1:5001`

**Connect Agent URL** 填：

`http://127.0.0.1:9999`

不要填 Card 的完整路徑當唯一輸入（有的版本要 base URL，有的可吃 well-known）。先填 base；若連不上再試 `http://127.0.0.1:9999/.well-known/agent-card.json`。

第一次需要 **git、uv、Node.js/npm**。clone 在 `.cache/a2a-inspector/`（已 gitignore）。已建過 frontend 可加 `--skip-install`。

---

## 3. Chat 第一句

無持股、不跑部位、不寄信（對 MCP 2330 演示）：

> 2330 要不要動

預期：Inspector 看到 task completed；本機 `reports/stock/{日期}/tw_stock_2330.md`。plan **沒有** `run_position_gate`、`send_digest`。

不要第一句就「幫我處理今天持股」：會對 holdings 裡的 2409 連跑 report + position，agy 多輪、慢。

`--dry-run` 的 A2A server 只回 plan，不寫報告。真跑就不要加。

Debug console 應看到 JSON-RPC `SendMessage`。那才是協定往返；Cursor Agent 對話不是 A2A。

---

## 4. 常見踩坑

| 現象 | 原因 |
|------|------|
| Inspector 連不上 9999 | terminal 1 沒開 `python main.py a2a` |
| 開 `http://127.0.0.1:9999` 當網頁 | JSON-RPC 入口不是 HTML；UI 是 `:5001` |
| Docker 跑 Inspector 填 `127.0.0.1:9999` | Inspector **後端**去打 Agent。容器裡的 127.0.0.1 不是你的 Mac。本 launcher 走本機 clone，避免這題 |
| 一接上就跑很久 | 沒加 `--dry-run` 會真跑 agy；先 2330 |

Studio「Add A2A agent」是另一條（公開 HTTPS + OAuth），見 [`Phase5.md`](./Phase5.md)。本機演示用 Inspector 即可。
