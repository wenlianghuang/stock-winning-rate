"""ADK root agent: Gemini decides; stock-winning-rate does the chip work via A2A.

This process is the A2A *client*. The server is ``python main.py a2a``
(``http://127.0.0.1:9999``). Gemini key stays in the environment — this file
never reads a committed secret.

RemoteA2aAgent is wrapped as ``AgentTool``, not ``sub_agents``. Sub-agents
use transfer_to_agent, which hands the turn to the remote process and never
lets root_agent speak until the next user message.
"""

from __future__ import annotations

import os

from google.adk.agents.llm_agent import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.tools.agent_tool import AgentTool

DEFAULT_CARD_URL = "http://127.0.0.1:9999/.well-known/agent-card.json"
DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_TIMEOUT_SEC = 1800.0

REMOTE_AGENT_NAME = "stock_winning_rate"

ROOT_INSTRUCTION = """你是台股作業的入口 Agent。你自己不算籌碼、不下單、不編造數字。

台股、持股、開盤問題：呼叫工具 stock_winning_rate，request 填使用者原句（不要改成 tool 名稱）。
非台股（天氣、閒聊等）：不要呼叫工具，立刻用一句話拒絕或改口。

工具一回來，必須在**同一輪**用中文簡答，不要把 JSON 原樣貼上，也不要等使用者再問一次。
plan 沒有 run_position_gate 就說沒有持倉所以沒跑部位。

簡答只准這三件事，各一兩句：
1. 跑了哪些步驟（fetch / report-gate / position-gate；沒跑的說沒跑）
2. 有沒有持倉／有沒有跑 position-gate
3. 產物路徑：從回傳最前面的「產物:」或 JSON files[]／steps[].result.md_path **逐字抄完整路徑**

禁止：摘錄或改寫收盤價、漲跌、張數、法人、建議、新聞；禁止發明路徑（例如 reports/2454_analysis.md）。
數字與解讀只存在廚房寫出的 .md；請使用者打開該檔。你不是分析師。
有 md_path 就必須列出完整路徑。禁止說「無產生實體報告檔案」或「沒有報告」。
skip_pdf=true 只代表沒寫 PDF，Markdown 仍是磁碟上的 .md。沒有 files／md_path 才說本輪沒有報告檔。

示範第一句：「2330 要不要動」。
不要建議「幫我處理今天持股」當第一句（會對整批持股跑 gate，很慢）。
"""


def card_url() -> str:
    return (os.environ.get("STOCK_A2A_CARD_URL") or DEFAULT_CARD_URL).strip()


def model_name() -> str:
    return (os.environ.get("ADK_MODEL") or DEFAULT_MODEL).strip()


def timeout_sec() -> float:
    raw = (os.environ.get("STOCK_A2A_TIMEOUT") or str(DEFAULT_TIMEOUT_SEC)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SEC
    return value if value > 0 else DEFAULT_TIMEOUT_SEC


def build_root_agent(
    *,
    agent_card: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> Agent:
    stock_agent = RemoteA2aAgent(
        name=REMOTE_AGENT_NAME,
        description=(
            "台股籌碼作業 Agent。"
            "一句話意圖：個股要不要動、處理今天持股、明天開盤怎麼看。"
            "無持倉不跑部位；不會自動寄信。"
            "request 填使用者原句。"
        ),
        agent_card=agent_card or card_url(),
        timeout=timeout if timeout is not None else timeout_sec(),
    )
    return Agent(
        name="root_agent",
        model=model or model_name(),
        instruction=ROOT_INSTRUCTION,
        description="本機 A2A client：把台股意圖委派給 stock-winning-rate。",
        tools=[AgentTool(stock_agent)],
    )


root_agent = build_root_agent()
