"""Website LangGraph: branch on peer stance, stop before send."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.web_graph import (  # noqa: E402
    PeerGraphIO,
    claim_peer_graph_send,
    enqueue_peer_graph,
    execute_peer_graph_job,
    finish_peer_graph_send,
    get_peer_graph_job,
    initial_peer_state,
    resume_peer_graph,
    run_peer_graph,
)


class FakeIO(PeerGraphIO):
    def __init__(
        self,
        *,
        weaker: bool = True,
        research_ok: bool = True,
        reading: str = "ok",
    ) -> None:
        self.weaker = weaker
        self.research_ok = research_ok
        self.reading = reading
        self.position_calls = 0
        self.reading_calls = 0

    def prepare_facts(self, stock_id: str, trade_date: str | None, *, fetch: bool) -> dict:
        del stock_id, fetch
        return {
            "ok": True,
            "exit_code": 0,
            "error": None,
            "skipped": True,
            "trade_date": trade_date or "2026-08-18",
            "facts_path": "facts.json",
        }

    def research(self, stock_id: str, trade_date: str | None, *, skip_pdf: bool) -> dict:
        del stock_id, trade_date, skip_pdf
        if not self.research_ok:
            return {
                "ok": False,
                "exit_code": 1,
                "error": "gate failed",
                "skipped": False,
                "md_path": None,
            }
        return {
            "ok": True,
            "exit_code": 0,
            "error": None,
            "skipped": True,
            "md_path": "report.md",
        }

    def compare(self, stock_id: str, peer_id: str, trade_date: str | None) -> dict:
        del stock_id, peer_id
        label = "weaker" if self.weaker else "stronger"
        return {
            "ok": True,
            "exit_code": 0,
            "error": None,
            "label": label,
            "base_weaker": self.weaker,
            "reasons": ["區間報酬落後"] if self.weaker else ["區間報酬領先"],
            "highlights": ["對照"],
            "stock_name": "友達",
            "peer_name": "群創",
            "trade_date": trade_date,
        }

    def position(
        self,
        stock_id: str,
        trade_date: str | None,
        *,
        avg_cost: float | None,
        share_count: int | None,
        skip_pdf: bool,
    ) -> dict:
        del stock_id, trade_date, avg_cost, share_count, skip_pdf
        self.position_calls += 1
        return {
            "ok": True,
            "exit_code": 0,
            "error": None,
            "skipped": False,
            "md_path": "position.md",
        }

    def write_peer_draft(
        self,
        *,
        digest_date: str,
        stock_id: str,
        peer_id: str,
        subject: str,
        body: str,
    ) -> str:
        del digest_date, stock_id, peer_id
        self.draft_subject = subject
        self.draft_body = body
        return "peer-draft.md"

    def complete_reading(self, prompt: str) -> str:
        del prompt
        self.reading_calls += 1
        if self.reading == "fail":
            raise RuntimeError("agy down")
        label = "較弱" if self.weaker else "較強"
        body = (
            f"## 怎麼讀這次比較\n相對同業：{label}。規則理由已足以說明這次比較。\n\n"
            "## 和本檔研究的關係\n這裡只複述既有結論。\n\n"
            "## 接下來看什麼\n- 沿用比較結論裡的區間報酬差距。\n"
        )
        return json.dumps({"reading_markdown": body}, ensure_ascii=False)


def _state(*, holding: bool) -> dict:
    return initial_peer_state(
        stock_id="2409",
        peer_id="3481",
        trade_date="2026-08-18",
        fetch=False,
        is_holding=holding,
        avg_cost=20.5 if holding else None,
        share_count=1000 if holding else None,
    )


class PeerGraphTests(unittest.TestCase):
    def test_weaker_holding_enters_position_and_stops_before_send(self) -> None:
        io = FakeIO(weaker=True)
        result, nxt, graph, config = run_peer_graph(_state(holding=True), io=io, thread_id="hold")
        self.assertEqual(nxt, ("send",))
        self.assertEqual(io.position_calls, 1)
        self.assertTrue(result["position_entered"])
        self.assertEqual(result["digest_status"], "pending_approval")
        self.assertIn("概念股比較", result["digest_subject"])
        self.assertIn("友達 相對 群創", result["digest_subject"])
        self.assertNotIn("2409", result["digest_subject"])
        self.assertIn("友達 相對同業 群創", result["digest_body"])
        self.assertIn("較弱", result["digest_body"])
        self.assertIn("區間報酬落後", result["digest_body"])
        self.assertIn("怎麼讀這次比較", result["digest_body"])
        self.assertNotIn("解讀未生成", result["digest_body"])
        self.assertEqual(io.reading_calls, 1)
        self.assertEqual(result["digest_path"], "peer-draft.md")
        self.assertNotIn("send", [step["node"] for step in result["steps"]])
        resumed = resume_peer_graph(graph, config)
        self.assertEqual(resumed["digest_status"], "blocked")
        self.assertTrue(any(step["node"] == "send" and not step["ok"] for step in resumed["steps"]))

    def test_stronger_skips_position(self) -> None:
        io = FakeIO(weaker=False)
        result, nxt, _graph, _config = run_peer_graph(_state(holding=True), io=io, thread_id="strong")
        self.assertEqual(nxt, ("send",))
        self.assertEqual(io.position_calls, 0)
        self.assertFalse(result["position_entered"])
        self.assertIn("不跑部位", result["position_skipped_reason"])

    def test_weaker_without_holding_skips_position(self) -> None:
        io = FakeIO(weaker=True)
        result, nxt, _graph, _config = run_peer_graph(_state(holding=False), io=io, thread_id="flat")
        self.assertEqual(nxt, ("send",))
        self.assertEqual(io.position_calls, 0)
        self.assertIn("無持倉", result["position_skipped_reason"])

    def test_reading_failure_keeps_rule_letter(self) -> None:
        io = FakeIO(weaker=True, reading="fail")
        result, nxt, _graph, _config = run_peer_graph(_state(holding=True), io=io, thread_id="noread")
        self.assertEqual(nxt, ("send",))
        self.assertEqual(io.reading_calls, 2)
        self.assertEqual(result["digest_status"], "pending_approval")
        self.assertIn("解讀未生成", result["digest_body"])
        self.assertIn("區間報酬落後", result["digest_body"])
        self.assertIn("較弱", result["digest_subject"])

    def test_research_failure_stops(self) -> None:
        io = FakeIO(research_ok=False)
        result, nxt, _graph, _config = run_peer_graph(_state(holding=True), io=io, thread_id="fail")
        self.assertEqual(nxt, ())
        self.assertTrue(result["halt"])
        self.assertEqual(io.position_calls, 0)
        self.assertNotIn("compare", [step["node"] for step in result["steps"]])

    def test_job_wrapper_records_pending_approval(self) -> None:
        io = FakeIO(weaker=True)
        done = threading.Event()

        def runner(job_id: str) -> None:
            execute_peer_graph_job(job_id, io=io)
            done.set()

        payload = enqueue_peer_graph(
            stock_id="2409",
            peer_id="3481",
            trade_date="2026-08-18",
            fetch=False,
            is_holding=True,
            avg_cost=20.5,
            share_count=1000,
            runner=runner,
        )
        self.assertTrue(done.wait(10), "job thread did not finish")
        job = get_peer_graph_job(payload["id"])
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job["status"], "pending_approval")
        self.assertEqual(job["interrupted_before"], "send")
        self.assertEqual(job["digest_status"], "pending_approval")
        self.assertTrue(job["position_entered"])
        self.assertEqual(io.position_calls, 1)
        self.assertIn("較弱", job["digest_body"])

    def test_claim_then_finish_send(self) -> None:
        io = FakeIO(weaker=False)
        done = threading.Event()

        def runner(job_id: str) -> None:
            execute_peer_graph_job(job_id, io=io)
            done.set()

        payload = enqueue_peer_graph(
            stock_id="2409",
            peer_id="3481",
            trade_date="2026-08-18",
            fetch=False,
            runner=runner,
        )
        self.assertTrue(done.wait(10))
        claimed = claim_peer_graph_send(payload["id"])
        self.assertEqual(claimed["status"], "sending")
        self.assertIn("較強", claimed["digest_body"])
        finished = finish_peer_graph_send(payload["id"], ok=True)
        self.assertEqual(finished["status"], "sent")
        with self.assertRaises(ValueError):
            claim_peer_graph_send(payload["id"])


class ComparePeerToolTests(unittest.TestCase):
    def test_known_pair_does_not_raise_missing_skill(self) -> None:
        from agent.tools.compare import ComparePeerInput, compare_concept_peer

        result = compare_concept_peer(
            ComparePeerInput(stock_id="2881", peer_id="2882", trade_date="1900-01-01")
        )
        self.assertFalse(result.ok)
        self.assertNotIn("portfolio-gate", result.error or "")
        self.assertEqual(result.exit_code, 20)


class PeerGraphApiTests(unittest.TestCase):
    def test_rejects_same_stock(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")
        client = TestClient(create_app())
        response = client.post(
            "/peer-graph/jobs",
            json={"stock_id": "2330", "peer_id": "2330"},
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_job(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")
        client = TestClient(create_app())
        response = client.get("/peer-graph/jobs/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_post_enqueues(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from api.stock_api import create_app
        except ImportError:
            self.skipTest("server extra not installed")
        with patch(
            "agent.web_graph.enqueue_peer_graph",
            return_value={"id": "abc", "status": "queued"},
        ) as mocked:
            client = TestClient(create_app())
            response = client.post(
                "/peer-graph/jobs",
                json={
                    "stock_id": "2409",
                    "peer_id": "3481",
                    "fetch": False,
                    "is_holding": False,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job"]["id"], "abc")
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
