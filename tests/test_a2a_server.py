"""Phase 5 A2A: Agent Card, JSON-RPC SendMessage, orchestrator policy."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.a2a_server import (  # noqa: E402
    CARD_PATH,
    SKILL_IDS,
    SKILL_MARKET_OUTLOOK,
    SKILL_PROCESS_HOLDINGS,
    SKILL_STOCK_RESEARCH,
    build_agent_card,
    build_app,
    card_as_dict,
    listed_skill_ids,
    main as a2a_main,
    resolve_intent,
)
from agent.policy import (  # noqa: E402
    DIGEST_PENDING,
    INTENT_MARKET_OUTLOOK,
    INTENT_PROCESS_HOLDINGS,
    INTENT_STOCK_DECISION,
)
from main import main as cli_main  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


def _send_message_body(text: str, *, skill: str | None = None, trade_date: str | None = None) -> dict:
    metadata: dict[str, str] = {}
    if skill:
        metadata["skill"] = skill
    if trade_date:
        metadata["trade_date"] = trade_date
    message: dict = {
        "messageId": uuid4().hex,
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if metadata:
        message["metadata"] = metadata
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": {"message": message},
    }


A2A_HEADERS = {"A2A-Version": "1.0"}


def _task_from_rpc(payload: dict) -> dict:
    result = payload.get("result") or {}
    return result.get("task") or result


def _plan_tools(task: dict) -> list[str]:
    artifacts = task.get("artifacts") or []
    for artifact in artifacts:
        for part in artifact.get("parts") or []:
            data = part.get("data")
            if isinstance(data, dict) and "plan" in data:
                return [step.get("tool") for step in data["plan"].get("steps") or []]
    return []


def _artifact_text(task: dict) -> str:
    chunks: list[str] = []
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("text"):
                chunks.append(str(part["text"]))
    return "\n".join(chunks)


class A2ACardTests(unittest.TestCase):
    def test_card_advertises_jsonrpc_and_three_skills(self) -> None:
        card = card_as_dict(build_agent_card(host="127.0.0.1", port=9999))
        self.assertEqual(card["name"], "stock-winning-rate")
        bindings = {item.get("protocolBinding") for item in card["supportedInterfaces"]}
        self.assertIn("JSONRPC", bindings)
        urls = {item.get("url") for item in card["supportedInterfaces"]}
        self.assertIn("http://127.0.0.1:9999/", urls)
        skill_ids = [skill["id"] for skill in card["skills"]]
        self.assertEqual(skill_ids, list(SKILL_IDS))
        self.assertFalse(card["capabilities"].get("streaming"))

    def test_resolve_intent_fills_skill_defaults(self) -> None:
        self.assertEqual(resolve_intent("", SKILL_PROCESS_HOLDINGS), "幫我處理今天持股")
        self.assertEqual(resolve_intent("", SKILL_MARKET_OUTLOOK), "明天開盤怎麼看")
        self.assertEqual(resolve_intent("2330", SKILL_STOCK_RESEARCH), "2330 要不要動")
        self.assertEqual(resolve_intent("2330 要不要動"), "2330 要不要動")


class A2ACliTests(unittest.TestCase):
    def test_list_skills_prints_ids(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = a2a_main(["--list-skills"])
        self.assertEqual(code, 0)
        names = [line.split("\t", 1)[0] for line in buffer.getvalue().splitlines() if line]
        self.assertEqual(names, list(SKILL_IDS))
        self.assertEqual(listed_skill_ids(), list(SKILL_IDS))

    def test_print_card_is_json(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = a2a_main(["--print-card", "--host", "127.0.0.1", "--port", "9999"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["name"], "stock-winning-rate")
        self.assertIn("/.well-known/agent-card.json", CARD_PATH)

    def test_main_py_routes_a2a_list_skills(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = cli_main(["a2a", "--list-skills"])
        self.assertEqual(code, 0)
        self.assertIn(SKILL_STOCK_RESEARCH, buffer.getvalue())

    def test_main_py_lists_a2a(self) -> None:
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            code = cli_main(["--list"])
        self.assertEqual(code, 0)
        self.assertIn("a2a", buffer.getvalue())
        self.assertIn("a2a_server.py", buffer.getvalue())


class A2AHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = build_app(dry_run=True, trade_date="2026-08-18")
        self.client = TestClient(self.app)

    def test_well_known_agent_card(self) -> None:
        response = self.client.get(CARD_PATH)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "stock-winning-rate")
        self.assertEqual(
            [skill["id"] for skill in payload["skills"]],
            list(SKILL_IDS),
        )

    def test_send_message_2330_skips_position(self) -> None:
        response = self.client.post(
            "/",
            json=_send_message_body("2330 要不要動", trade_date="2026-08-18"),
            headers=A2A_HEADERS,
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("error", payload)
        task = _task_from_rpc(payload)
        state = (task.get("status") or {}).get("state")
        self.assertEqual(state, "TASK_STATE_COMPLETED")
        tools = _plan_tools(task)
        self.assertIn("run_report_gate", tools)
        self.assertNotIn("run_position_gate", tools)
        self.assertNotIn("send_digest", tools)
        text = _artifact_text(task)
        self.assertIn(INTENT_STOCK_DECISION, text)
        self.assertIn("無持倉", text)

    def test_send_message_process_holdings_blocks_digest(self) -> None:
        response = self.client.post(
            "/",
            json=_send_message_body("幫我處理今天持股", skill=SKILL_PROCESS_HOLDINGS),
            headers=A2A_HEADERS,
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("error", payload)
        task = _task_from_rpc(payload)
        self.assertEqual((task.get("status") or {}).get("state"), "TASK_STATE_COMPLETED")
        tools = _plan_tools(task)
        self.assertIn("run_report_gate", tools)
        self.assertIn("run_position_gate", tools)
        self.assertIn("draft_digest", tools)
        self.assertNotIn("send_digest", tools)
        text = _artifact_text(task)
        self.assertIn(INTENT_PROCESS_HOLDINGS, text)
        self.assertIn("blocked", text)
        artifacts = task.get("artifacts") or []
        kinds = []
        for artifact in artifacts:
            for part in artifact.get("parts") or []:
                if "data" in part:
                    kinds.append(part["data"].get("digest_status"))
        self.assertIn(DIGEST_PENDING, kinds)

    def test_send_message_market_outlook(self) -> None:
        response = self.client.post(
            "/",
            json=_send_message_body("明天開盤怎麼看", skill=SKILL_MARKET_OUTLOOK),
            headers=A2A_HEADERS,
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = _task_from_rpc(response.json())
        self.assertEqual((task.get("status") or {}).get("state"), "TASK_STATE_COMPLETED")
        tools = _plan_tools(task)
        self.assertIn("answer_market_chat", tools)
        self.assertNotIn("run_position_gate", tools)
        self.assertIn(INTENT_MARKET_OUTLOOK, _artifact_text(task))

    def test_missing_version_header_defaults_to_1_0(self) -> None:
        response = self.client.post("/", json=_send_message_body("2330 要不要動"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("error", response.json())
        task = _task_from_rpc(response.json())
        self.assertEqual((task.get("status") or {}).get("state"), "TASK_STATE_COMPLETED")

    def test_empty_message_is_rejected(self) -> None:
        response = self.client.post(
            "/",
            json=_send_message_body("   "),
            headers=A2A_HEADERS,
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = _task_from_rpc(response.json())
        self.assertEqual((task.get("status") or {}).get("state"), "TASK_STATE_REJECTED")

    def test_bearer_token_protects_rpc_not_card(self) -> None:
        app = build_app(dry_run=True, trade_date="2026-08-18", token="secret")
        client = TestClient(app)
        self.assertEqual(client.get(CARD_PATH).status_code, 200)
        denied = client.post(
            "/",
            json=_send_message_body("2330 要不要動"),
            headers=A2A_HEADERS,
        )
        self.assertEqual(denied.status_code, 401)
        allowed = client.post(
            "/",
            json=_send_message_body("2330 要不要動"),
            headers={**A2A_HEADERS, "Authorization": "Bearer secret"},
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertNotIn("error", allowed.json())


if __name__ == "__main__":
    unittest.main()
