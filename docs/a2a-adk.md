# Google ADK：本機 A2A peer client

協定與 Card 見 [`Phase5.md`](./Phase5.md)。Inspector 除錯 UI 見 [`a2a-inspector.md`](./a2a-inspector.md)。本文件記錄：沒有 Copilot Studio 時，用 Google ADK 當「另一個有大腦的 Agent」連 `python main.py a2a`。

狀態：**操作說明已落地**（`python main.py a2a-adk`）。ADK 本體在 `agent/adk_clients/` 的獨立 uv 專案，不進本 repo 的 `a2a` extra。**Gemini key 請自己設**，本 repo 不代填、不進 git。

---

## 1. 概念：這是 peer Agent，不是 Inspector

| | Inspector | ADK |
|--|-----------|-----|
| 埠 | `:5001` | `:8002`（避開 MCP inspector `:8000`） |
| 誰丟句子 | 你在網頁打字 | Gemini 讀 Card，決定委派 |
| 對端打哪 | `POST http://127.0.0.1:9999/` `SendMessage` | 同一個 |
| 誰排 plan | 我們的 orchestrator | 仍是我們的 orchestrator |

ADK **不會**呼叫 `fetch_chips`。那是 MCP 契約。A2A 對端只丟「2330 要不要動」。

遠端 agent 用 `AgentTool` 包起來，不用 `sub_agents`。後者會 `transfer_to_agent`，廚房做完這一輪不會再讓 Gemini 開口，人話被拖到下一句。Tool 結束後控制權回到 `root_agent`，同一輪只回步驟／是否跑部位／**逐字抄**的 `md_path`。不要讓 Gemini 重寫收盤價或籌碼（會編錯，例如把 2454 收盤 4,560 講成 1,415）。A2A artifact 會先給短的「產物:」路徑清單；`skip_pdf` 只代表沒寫 PDF，不代表沒有 Markdown。

官方對照：[ADK consuming a remote agent](https://adk.dev/a2a/quickstart-consuming/)、[`RemoteA2aAgent`](https://github.com/google/adk-python/blob/main/src/google/adk/agents/remote_a2a_agent.py)。ADK 的 A2A client 目前仍標 experimental；協定本身不是。

---

## 2. 三個 terminal（key 自己處理）

專案根目錄 `.env`（已 gitignore）自行加上：

```
GOOGLE_API_KEY=你的金鑰
GOOGLE_GENAI_USE_VERTEXAI=false
```

亦可用 `GEMINI_API_KEY`；launcher 會轉成 ADK 要的 `GOOGLE_API_KEY`，不會寫回檔案。新申請的 Gemini key 預設模型是 `gemini-3.6-flash`（`ADK_MODEL` / `--model` 可改）。

```bash
# terminal 1：A2A server（保持開著）
uv run --extra a2a --extra stock --extra ui python main.py a2a --dry-run --date 2026-08-18

# terminal 2：ADK Web（第一次會在 agent/adk_clients 裝 google-adk）
uv run --extra a2a python main.py a2a-adk
```

只想看步驟：

```bash
uv run --extra a2a python main.py a2a-adk --howto
```

瀏覽器開 `http://127.0.0.1:8002`，選 agent `stock_a2a`。

第一句：

> 2330 要不要動

不要第一句就「幫我處理今天持股」。`--dry-run` 的 server 只回 plan。真跑不要加 `--dry-run`。

已裝過依賴可加 `--skip-install`（`uv run --frozen`）。

正式句：
> 2059今天表現如何?
---

## 3. 常見踩坑

| 現象 | 原因 |
|------|------|
| `ERROR: 未設定 GOOGLE_API_KEY` | `.env` 還沒自己放 key；本命令不代填 |
| ADK 連不上 Card | terminal 1 沒開 `python main.py a2a` |
| 開 `:9999` 當網頁 | JSON-RPC 不是 HTML；ADK UI 是 `:8002` |
| 一委派就跑很久 | 沒加 `--dry-run` 會真跑 agy |
| ADK Web 綁 `0.0.0.0` | launcher 拒絕。只准 loopback |
| `404` / `gemini-2.5-flash is no longer available` | 新 Gemini key 不給 2.5。預設已改 `gemini-3.6-flash`；停掉舊 process 再開 `a2a-adk` |
| 廚房做完了，人話等到下一句才出現 | 舊版用 `transfer_to_agent`。現在已改 `AgentTool`。請停掉 `a2a-adk` 再開一次，並用 **New Session** |
| UI 說沒報告、磁碟卻有 `.md` | 舊版把 `skip_pdf` 當成沒檔。A2A 現在先回「產物:」路徑清單。請停掉 `a2a-adk` 再開一次，並用 **New Session** |
| UI 數字／路徑跟 `.md` 不符 | Gemini 當分析師亂摘。簡答應只抄 `md_path`、不重寫籌碼。重開 `a2a-adk` + **New Session** |

現場 10–15 分鐘仍用 Inspector 指 Card／JSON-RPC。ADK 是本機確認「另一個 Agent runtime 真的委派進來」。
