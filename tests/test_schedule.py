"""Phase 4 pre-market schedule: skip early / idempotent / US required."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKILL = ROOT / ".agents" / "skills" / "market-daily"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(ROOT / "ui"))

from agent.schedule import (  # noqa: E402
    CanonicalBrief,
    decide_premarket_action,
    load_canonical_brief,
    run_premarket_once,
)
from agent.tools._types import EXIT_OK  # noqa: E402
from agent.tools.market import MarketDailyResult  # noqa: E402
from market_day_signals import (  # noqa: E402
    fetch_us_day_block_with_retry,
    us_block_usable,
)


def _usable_us() -> dict:
    return {
        "available": True,
        "indices": {
            "IXIC": {"day_return_pct": 0.5},
            "SOX": {"day_return_pct": -0.2},
        },
    }


class UsRetryTests(unittest.TestCase):
    def test_usable_requires_index_return(self) -> None:
        self.assertFalse(us_block_usable({"available": True, "indices": {}}))
        self.assertTrue(us_block_usable(_usable_us()))

    def test_retry_then_raise_does_not_return_empty(self) -> None:
        calls = {"n": 0}

        def boom(_as_of: str) -> dict:
            calls["n"] += 1
            raise RuntimeError("yahoo down")

        with self.assertRaises(RuntimeError) as ctx:
            fetch_us_day_block_with_retry(
                "2026-08-04",
                attempts=3,
                backoff_sec=0,
                sleep=lambda _s: None,
                fetch=boom,
            )
        self.assertEqual(calls["n"], 3)
        self.assertIn("不略過", str(ctx.exception))

    def test_retry_succeeds_on_third(self) -> None:
        calls = {"n": 0}

        def flaky(_as_of: str) -> dict:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("temp")
            return _usable_us()

        block = fetch_us_day_block_with_retry(
            "2026-08-04",
            attempts=3,
            backoff_sec=0,
            sleep=lambda _s: None,
            fetch=flaky,
        )
        self.assertEqual(calls["n"], 3)
        self.assertTrue(us_block_usable(block))


class CanonicalBriefTests(unittest.TestCase):
    def test_ready_requires_us(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "2026-08-01"
            day.mkdir()
            (day / "tw_market_daily.md").write_text("# brief\n", encoding="utf-8")
            (day / "tw_market_daily.summary.json").write_text(
                '{"for_session":"2026-08-04"}',
                encoding="utf-8",
            )
            (day / "tw_market_daily.facts.json").write_text(
                json.dumps({"for_session": "2026-08-04", "us": {"available": False}}),
                encoding="utf-8",
            )
            brief = load_canonical_brief("2026-08-01", root=root)
            self.assertFalse(brief.ready)
            self.assertFalse(brief.us_available)

    def test_ready_when_us_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "2026-08-01"
            day.mkdir()
            (day / "tw_market_daily.md").write_text("# brief\n", encoding="utf-8")
            (day / "tw_market_daily.summary.json").write_text("{}", encoding="utf-8")
            (day / "tw_market_daily.facts.json").write_text(
                json.dumps({"us": _usable_us(), "for_session": "2026-08-04"}),
                encoding="utf-8",
            )
            brief = load_canonical_brief("2026-08-01", root=root)
            self.assertTrue(brief.ready)


class DecideActionTests(unittest.TestCase):
    def test_skip_before_cutover(self) -> None:
        action, reason = decide_premarket_action(
            us_cutover_passed=False,
            brief=None,
            force=False,
        )
        self.assertEqual(action, "skip_early")
        self.assertIn("05:30", reason)

    def test_skip_when_ready(self) -> None:
        brief = CanonicalBrief(
            trade_date="2026-08-01",
            for_session="2026-08-04",
            ready=True,
            us_available=True,
            facts={},
            summary={},
            markdown="# x",
            facts_path=None,
            md_path=None,
        )
        action, _ = decide_premarket_action(
            us_cutover_passed=True,
            brief=brief,
            force=False,
        )
        self.assertEqual(action, "skip_exists")

    def test_rerun_if_missing_us(self) -> None:
        brief = CanonicalBrief(
            trade_date="2026-08-01",
            for_session="2026-08-04",
            ready=False,
            us_available=False,
            facts={},
            summary={},
            markdown="# x",
            facts_path=None,
            md_path=None,
        )
        action, reason = decide_premarket_action(
            us_cutover_passed=True,
            brief=brief,
            force=False,
        )
        self.assertEqual(action, "run")
        self.assertIn("美股", reason)

    def test_force_runs_even_if_ready(self) -> None:
        brief = CanonicalBrief(
            trade_date="2026-08-01",
            for_session="2026-08-04",
            ready=True,
            us_available=True,
            facts={},
            summary={},
            markdown="# x",
            facts_path=None,
            md_path=None,
        )
        action, _ = decide_premarket_action(
            us_cutover_passed=True,
            brief=brief,
            force=True,
        )
        self.assertEqual(action, "run")


class RunOnceTests(unittest.TestCase):
    def test_dry_run_does_not_call_daily(self) -> None:
        called = {"n": 0}

        def fake_daily(*_a, **_k):
            called["n"] += 1
            return MarketDailyResult(ok=True, exit_code=EXIT_OK)

        class Window:
            trade_date = "2026-08-01"
            for_session = "2026-08-04"
            us_cutover_passed = True

        with patch(
            "market_day_signals.resolve_window_or_fail",
            return_value=Window(),
        ), patch(
            "agent.schedule.load_canonical_brief",
            return_value=CanonicalBrief(
                trade_date="2026-08-01",
                for_session="2026-08-04",
                ready=False,
                us_available=False,
                facts=None,
                summary=None,
                markdown=None,
                facts_path=None,
                md_path=None,
            ),
        ):
            result = run_premarket_once(
                as_of="2026-08-04T05:30:00+08:00",
                dry_run=True,
                run_daily=fake_daily,
            )
        self.assertEqual(result.action, "run")
        self.assertIn("dry-run", result.reason)
        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
