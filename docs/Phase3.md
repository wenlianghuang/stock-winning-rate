# Phase 3 — 角色 allowlist、交接標籤與權限

規劃見 [`agent-roadmap.md`](./agent-roadmap.md) §4。人若要逐步產報仍見 [`commands.md`](./commands.md)；一句話意圖見 [`Phase2.md`](./Phase2.md)。本文件記錄：為什麼要把 Phase 2 的內部步驟標上 `actor`、角色 allowlist 怎麼擋、JSONL audit 怎麼重放、以及關掉 `send_digest` 時流程如何停在草稿。

本階段**不是** Agent2Agent（A2A）協定。職缺的 A2A 與這裡的關係（協定層沒有、問題層只有薄概念親戚）見 [`a2a.md`](./a2a.md)。

狀態：**已落地**（角色 allowlist + Research↔Validator 交接 + `reports/agent/` JSONL + `--replay`）。

---

## 1. 概念：步驟變成可審計的角色標籤，不是 A2A，也不是再包一層 Agent 框架

Phase 2 的 Orchestrator 已經會選 tool、擋無持倉 position、擋寄信。對面試與事後追查來說，缺的是兩件事：

1. **誰做了這一步。** `run_report_gate` 內部本來就有「產文 → 驗證 → 打回」的 N 輪 loop；人從 CLI 只看到一個 tool 名稱，講不出 Research / Validator / Position。
2. **這次 run 可不可以重看。** 沒有 log 的話，gate 第一輪的 `issue_codes` 與「信為什麼沒寄」只能靠記憶或翻 `.gate.log`。

Phase 3 要解的是：

> 同一組 `agent.tools`，把執行標成 Data / Research / Validator / Position / Notify / chat。權限依角色白名單；每次 tool call 寫 JSONL。`send_digest` 權限預設關閉，流程停在草稿。禁止下單。

不重寫 gate。`report-gate` 的 `max_rounds` loop 仍在 skill 裡跑。Orchestrator 在 `run_report_gate` 結束後讀 `.gate.log`（測試可塞 `gate_rounds`），把每一輪物質化成 Validator 紀錄與 `Research → Validator → Research` 交接。這是「現有 loop 的多角色版」，不是第二層 retry。

LLM 仍然不能自己寄信、不能改持股、不能對無持倉跑部位。Phase 3 只是把這些規則標上 **actor** 與 **audit**。不要把 `Handoff` 或 Data／Research／Notify 角色說成已做 Agent2Agent。

---

## 2. 目標架構（本階段實際長成這樣）

```
使用者：python main.py agent -- "幫我處理今天持股"
        │
        ▼
┌───────────────────┐
│ Orchestrator      │  意圖 → plan → policy（含角色 allowlist）
└─────────┬─────────┘
          │ actor 切換（同一 process，不是 A2A）
     ┌────┴─────────────────────────────┐
     ▼          ▼            ▼          ▼
   Data      Research     Position    Notify
     │          │            │          │
 fetch_chips  report-gate  position   draft_digest
              ▲     │                   （send 權限關閉）
              │     ▼
              └── Validator  ← .gate.log 的 issue_codes
```

同一條「處理持股」run 的角色順序（仍是一個 Orchestrator，不是六個 A2A server）：

1. Orchestrator → Data（缺 CSV 才 fetch）
2. Data → Research（`run_report_gate`）
3. Research → Validator（每一輪 issue list）
4. 未通過則 Validator → Research（交回同一角色；實際執行仍在 gate 內）
5. 通過且有均價／張數 → Validator／Research → Position（必須已有 Research facts，除非 `--skip-research`）
6. 僅「已通過 gate」的成品 → Notify（`draft_digest`）
7. Notify **不停** 去 `send_digest`：權限關閉 + human gate

`chat` 是獨立角色（路況意圖預設）：只准 `answer_market_chat` / `run_market_daily` / 讀日期與持股，**不得** `send_digest`。

Audit 寫在 `reports/agent/{交易日}/run_{run_id}.jsonl`（`reports/` 已 gitignore）。`--replay` 讀同一份檔，不重跑 agy。

Phase 4 已落地：`llm.py` adapter、05:30 共用盤前排程（見 [`Phase4.md`](./Phase4.md)）。

---

## 3. 修改過程（做了什麼、為什麼）

### 3.1 `agent/policy.py`：角色 allowlist 與兩道寄信閘門

Roadmap 的「chat 不得 send_digest」不能只靠 Phase 2 的「plan 裡不要放 send」。模型仍可能用 `--plan-json` 塞進來；路況意圖也不該碰 Notify。

| 角色 | 可呼叫 | 不可 |
|------|--------|------|
| `orchestrator` | Phase 0 全部 10 個 tools | 券商下單（根本不在清單） |
| `data` | 日期、fetch、facts、holdings | report / position / digest |
| `research` | Data tools + report-gate / daily / chat | position、send |
| `validator` | 無 tool（只讀 gate.log） | 一切副作用 |
| `position` | position-gate、holdings、日期 | send、改持股 |
| `notify` | `draft_digest`、`send_digest` | 仍受 human gate |
| `chat` | chat、daily、日期、holdings | **send_digest**、position、fetch、draft |

預設角色：`market_outlook` → `chat`；`process_holdings` / `stock_decision` → `orchestrator`。CLI `--role` 可覆蓋（例如把「處理持股」硬切成 chat，plan 會被拆空）。

`Permissions` 是 run 級授權，跟角色白名單分開：

| 欄位 | 預設 | 意義 |
|------|------|------|
| `allow_send_digest` | `False` | 關掉時流程停在草稿；就算 plan 寫 `approved=true` 也不算 |
| `allow_mutate_holdings` | 永遠 `False` | 禁止自動改持股／下單 |
| `skip_research` | `False` | 明確允許 Position 不依賴本輪 Research facts |

寄信要過兩道：角色允許 **且** `allow_send_digest` **且** 人核准。CLI 仍然沒有 `--approve-send`；本 repo 核准後也不寄信（寄信在 `stock-report-site`）。

Position 交接加嚴：有持倉之後還要 `report_ok` 與 facts。`--skip-research` 才放行「本輪 research 沒過仍跑部位」。Notify 只收 `report_passed=True` 的成品；報告沒過就不會進 `draft_digest`。

`FORBIDDEN_BROKER_TOOLS` 與 `broker_tools_exposed()` 用來保證 allowlist 永遠不含 `place_order` 一類名字。

### 3.2 `agent/audit.py`：可重放的 JSONL

一筆紀錄對應一次 tool 或一輪 Validator：

```json
{
  "run_id": "20260909T144343_b8006c60",
  "ts": "2026-09-09T14:43:43Z",
  "actor": "validator",
  "tool": "validate_research",
  "args": {"stock_id": "2409", "gate_round": 1},
  "result": "fail",
  "elapsed_ms": 35229,
  "issue_codes": ["missing_stock_id"],
  "handoff_from": "research",
  "handoff_to": "research",
  "summary": "issue_codes=missing_stock_id",
  "gate_round": 1,
  "stock_id": "2409"
}
```

`summarize_args` 會丢掉 `markdown` / `prompt` / `items` 本文，只留 `stock_id`、日期、旗標與 `items_count`。重放不需要報告全文。

`parse_gate_log` 讀既有 `*.gate.log`（JSONL，每輪含 `issue_codes`）。測試不必碰真實檔：dispatch 回傳 `gate_rounds` 即可。`handoffs_from_gate_rounds` 把「第 1 輪 fail → 第 2 輪 pass」編成 Research↔Validator 交接，對應 roadmap §8.1 第 3 條：audit 看得到 `issue_codes` 與下一輪通過（現場不必重放）。

`replay_audit` / `format_replay` 重建 tools、actors、handoffs、issue rounds，以及「send 有沒有出現在 log」。處理持股的完成條件是：**log 裡沒有成功的 send_digest，digest 停在 blocked／未執行。**

### 3.3 `agent/orchestrator.py`：執行期接上交接與 audit

Plan 步驟帶 `actor`（依 `TOOL_ACTOR`）。執行時：

- 角色不允許的 tool：SKIP，寫進 notes 與 audit（`result=blocked`）。
- `run_position_gate` 走 `allow_position_handoff`（facts / skip_research）。
- `run_report_gate` 成功才把 markdown 交給 Notify；失敗不塞佔位文案（避免沒過的報告混進草稿）。
- 每個實際步驟與 probe 寫 audit；Validator 輪次插在 research tool 之後。
- actor 切換時記 `Handoff`（例如 data → research、position → notify）。

CLI 新增：

| 旗標 | 用途 |
|------|------|
| `--role` | 覆蓋 allowlist（`chat` / `orchestrator` / `data` / …） |
| `--skip-research` | Position 不要求本輪 facts |
| `--audit-dir` | 指定 JSONL 目錄；dry-run 預設不寫，有這個旗標才寫 |
| `--no-audit` | 實際執行也不寫 |
| `--run-id` | 固定 run_id，方便對帳 |
| `--replay PATH` | 只讀 JSONL，不跑 tools |

實際執行（非 dry-run）預設寫 `reports/agent/{交易日}/run_{run_id}.jsonl`。單元測試預設 `audit_enabled=False`，避免污染；Phase 3 測試用 tempfile。

沒有把 MCP tool 清單加大。Validator 不是給 Cursor 點的 MCP tool，是 Orchestrator 對 gate.log 的讀取角色。

### 3.4 測試：allowlist、草稿停住、audit 可重放

`tests/test_phase3.py`（既有 `tests/test_orchestrator.py` 三個 golden 意圖維持不變）：

- chat 不得 `send_digest` / `run_position_gate`；路況意圖預設 chat；`--plan-json` 塞 send 會被拆掉
- `allow_send_digest=False` 即使 `approved=True` 仍 blocked
- 無券商 tool；`allow_mutate_holdings` 永遠拒絕
- Position 缺 facts 不行；`skip_research` 才放行
- 處理持股：send 權限關閉 → fetch / report / position / draft，**不 dispatch send**；handoff 含 Research↔Validator
- tempfile JSONL → `load_audit` → `replay_audit` 看得到 `missing_stock_id` 與第二輪通過，且 `send_digest` 不在 log
- 報告沒過 → position SKIP、Notify 不收成品
- `--replay` CLI 印 issue_codes

跑：

```bash
uv run --extra stock --extra ui python -m unittest tests.test_orchestrator tests.test_phase3 tests.test_agent_tools
```

---

## 4. 對操作者

逐步產報與一句話意圖都還在。Phase 3 多出來的是角色、audit、重放。

```bash
uv run --extra stock --extra ui python main.py agent -- "幫我處理今天持股"
```

結束時會多 `run_id`、`角色`、`權限: send_digest=off`、Handoffs（實際執行才有 Validator 輪次）、以及 `Audit: reports/agent/{日期}/run_….jsonl`。信不會寄出，`digest=pending_approval`。

只看 plan：

```bash
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 -- "幫我處理今天持股"
```

本機 dry-run（holdings 範例只有 2409；該日已有 CSV 就不會列入 fetch）：

```
意圖: 幫我處理今天持股
種類: process_holdings
交易日: 2026-08-18
角色: orchestrator
權限: send_digest=off  skip_research=off

Plan:
  1. [research] run_report_gate  …
  2. [position] run_position_gate  … from_holdings_file=True
  3. [notify] draft_digest  …
Notes:
  - send_digest 預設 blocked，草稿待核准
  - send_digest: blocked（待核准）
```

「2330 要不要動」仍只有 `[research] run_report_gate`，notes 寫無持倉所以不跑部位。

把整次 run 當成 chat（演示 allowlist）：

```bash
uv run --extra stock --extra ui python main.py agent --dry-run --date 2026-08-18 --role chat -- "幫我處理今天持股"
```

plan 會被拆空，notes 出現 `角色 chat 不允許 run_report_gate` / `run_position_gate` / `draft_digest`。

重放：

```bash
uv run --extra stock --extra ui python main.py agent --replay reports/agent/2026-08-18/run_<id>.jsonl
```

會列出 `[research]` / `[validator]` 各步、`issue_codes`、以及 `send_digest: blocked / 未執行`。

---

## 5. 動到的檔案

新增：

- `agent/audit.py`
- `tests/test_phase3.py`
- `docs/Phase3.md`

修改：

- `agent/policy.py` — 角色、allowlist、`Permissions`、position facts、Notify 只收通過品
- `agent/orchestrator.py` — actor、handoff、audit、`--role` / `--replay` / `--skip-research`
- `agent/__init__.py` — 套件說明含 Phase 3
- `docs/commands.md` — audit / replay 速查
- `docs/agent-roadmap.md` — Phase 3 指向本文件
- `docs/Phase0.md`、`docs/Phase1.md`、`docs/Phase2.md` — 下一階段連結

沒有改 gate 驗證規則、報告模板、MCP tool 清單、FastAPI JSON、網站 repo。沒有接券商 API。本階段沒有實作 Agent2Agent（協定在 [`Phase5.md`](./Phase5.md)）。

---

## 6. 完成定義對照

| 條件 | 結果 |
|------|------|
| 把內部步驟顯式化成交接 | `Handoff` + 執行列印 `[research]` / `[validator]` / `[position]` / `[notify]` |
| Research 失敗 → Validator issue list → Research 再跑 | 讀 `.gate.log` / `gate_rounds`；audit 有 `validate_research` 與 `issue_codes` |
| Position 前要有 Research facts，或明確 skip | `allow_position_handoff`；`--skip-research` |
| Notify 只收已通過 gate 的成品 | 失敗報告不進 `draft_digest` |
| Tool allowlist（chat 不得 send） | `ROLE_ALLOWLIST`；測試 + `--role chat` |
| Human gate | `Permissions.allow_send_digest=False`；無 `--approve-send` |
| Audit JSONL 可重放 | `reports/agent/…/run_*.jsonl`；`--replay`；測試用 tempfile |
| 關掉 send 權限時停在草稿 | 處理持股 dispatch 不含 `send_digest`；`digest=pending_approval` |
| 禁止自動下單 | allowlist 不含下單 tool；`allow_mutate_holdings` 永遠 False |

---

## 7. 現場 demo

現場**不要**把本階段當獨立戲份。這裡不是六個 process，不是 Agent2Agent，也不是第二層 retry：Validator 標籤是事後讀 `.gate.log`，既有 gate loop 的多角色版。`--role chat` 拆空 plan、`--replay` JSONL 對懂 A2A **協定**的人不能當 A2A 演示——Card 與跨 Agent HTTP 在 [`Phase5.md`](./Phase5.md)。

閉環用 Phase 1 產出來的 `.gate.log` 就能講。職缺提到權限／審計時口頭對應：角色 allowlist、`send_digest` 預設關、JSONL 可重放、不下單。職缺提到 **A2A** 時不要用本階段充數。程式與 `tests/test_phase3.py` 保留。

現場主線：MCP 2330 → facts／gate 產物 → 05:30 共用 brief。見 [`agent-roadmap.md`](./agent-roadmap.md) §8。

---

## 8. 下一階段

Phase 4 落地見 [`Phase4.md`](./Phase4.md)：台北 05:30 後產一份全站共用開盤前 brief；美股失敗重試、不略過；持股仍不進排程。A2A 協定見 [`Phase5.md`](./Phase5.md)。

面試可以講：一句話處理持股；audit 看得到第一輪 `issue_codes` 與 Validator 打回；chat 角色寄不出信；關掉 send 權限時只留草稿。數字仍由 harness 算、gate 擋住。這是 in-process 權限與審計，不是 A2A。現場不必重跑 holdings 或 replay。
