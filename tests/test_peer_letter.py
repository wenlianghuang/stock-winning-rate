"""Peer letter keeps rule figures and only attaches a checked reading."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.peer_letter import (  # noqa: E402
    attach_peer_reading,
    build_peer_reading_prompt,
    compose_peer_email,
    parse_peer_reading,
    validate_peer_reading,
)


def _reading(label: str, extra: str = "") -> str:
    return (
        f"## 怎麼讀這次比較\n相對同業：{label}。規則理由已足以說明這次比較。\n\n"
        "## 和本檔研究的關係\n這裡只複述既有結論，並對照 MA20。\n\n"
        f"{extra}"
        "## 接下來看什麼\n- 沿用比較結論裡的區間報酬差距。\n"
    )


def _state() -> dict:
    return {
        "stock_id": "2409",
        "peer_id": "3481",
        "trade_date": "2026-08-18",
        "position_entered": False,
        "position_skipped_reason": "本檔並未弱於同業，不跑部位",
        "md_path": "report.md",
        "position_md_path": None,
        "comparison": {
            "label": "weaker",
            "stock_name": "友達",
            "peer_name": "群創",
            "reasons": ["區間報酬落後（友達 -2.10%）"],
            "highlights": ["外資方向分歧：友達 -1,200 張、群創 +800 張。"],
            "themes": ["面板"],
        },
    }


class PeerLetterTests(unittest.TestCase):
    def test_rule_letter_keeps_conclusion_and_themes(self) -> None:
        subject, body = compose_peer_email(_state())
        self.assertIn("友達 相對 群創：較弱", subject)
        self.assertNotIn("2409", subject)
        self.assertNotIn("3481", subject)
        self.assertIn("友達 相對同業 群創：較弱。", body)
        self.assertNotIn("2409", body)
        self.assertIn("## 比較結論", body)
        self.assertIn("區間報酬落後（友達 -2.10%）", body)
        self.assertIn("外資方向分歧：友達 -1,200 張、群創 +800 張。", body)
        self.assertIn("共同題材：面板。", body)
        self.assertIn("解讀未生成，這封只有規則比較。", body)
        self.assertIn("解讀只解釋上述結論，不另行比較。", body)
        self.assertNotIn("不是模型改寫", body)

    def test_checked_reading_is_inserted(self) -> None:
        _subject, body = compose_peer_email(_state(), reading=_reading("較弱"))
        self.assertIn("## 怎麼讀這次比較", body)
        self.assertNotIn("解讀未生成", body)
        self.assertLess(body.index("## 怎麼讀這次比較"), body.index("## 部位"))
        self.assertLess(body.index("## 同日事實"), body.index("## 怎麼讀這次比較"))

    def test_validate_allows_frozen_numbers_and_indicator_words(self) -> None:
        reading = _reading("較弱").replace(
            "規則理由已足以說明這次比較。",
            "規則理由已足以說明這次比較，區間為 -2.10%，外資 -1,200 張、對照 +800 張。",
        )
        self.assertEqual(validate_peer_reading(reading, _state()), [])

    def test_validate_rejects_new_number_and_flipped_stance(self) -> None:
        reading = _reading("較弱").replace(
            "規則理由已足以說明這次比較。",
            "整體較強，另有 -3.50% 沒寫在結論裡。",
        )
        problems = validate_peer_reading(reading, _state())
        self.assertTrue(any("-3.50%" in item for item in problems))
        self.assertTrue(any("整體較強" in item for item in problems))

    def test_validate_rejects_stock_codes_when_names_exist(self) -> None:
        reading = _reading("較弱").replace(
            "規則理由已足以說明這次比較。",
            "規則理由已足以說明這次比較，2409 相對 3481。",
        )
        problems = validate_peer_reading(reading, _state())
        self.assertTrue(any("2409" in item and "友達" in item for item in problems))
        self.assertTrue(any("3481" in item and "群創" in item for item in problems))

    def test_prompt_asks_for_names(self) -> None:
        prompt = build_peer_reading_prompt(_state(), report="", position="")
        self.assertIn("本檔是友達，對照檔是群創", prompt)
        self.assertIn("不要寫股號", prompt)
        self.assertNotIn("本檔代號", prompt)

    def test_validate_rejects_foreign_direction_against_base_lot(self) -> None:
        reading = _reading("較弱").replace(
            "規則理由已足以說明這次比較。",
            "規則理由已足以說明這次比較，但外資買超。",
        )
        problems = validate_peer_reading(reading, _state())
        self.assertTrue(any("外資買超" in item for item in problems))

    def test_validate_requires_position_section_when_report_exists(self) -> None:
        problems = validate_peer_reading(
            _reading("較弱"),
            _state(),
            position_text="部位已偏高。",
        )
        self.assertTrue(any("部位含義" in item for item in problems))
        ok = _reading("較弱", extra="## 部位含義\n部位報告只說明這次較弱，沒有新數字。\n\n")
        self.assertEqual(validate_peer_reading(ok, _state(), position_text="部位已偏高。"), [])

    def test_parse_fenced_json(self) -> None:
        raw = "```json\n" + json.dumps({"reading_markdown": _reading("較弱")}, ensure_ascii=False) + "\n```"
        self.assertIn("相對同業：較弱", parse_peer_reading(raw))

    def test_retry_then_keep_valid_reading(self) -> None:
        calls: list[str] = []

        def complete(prompt: str) -> str:
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps(
                    {"reading_markdown": "## 怎麼讀這次比較\n整體較強，跌了 -9.99%。\n## 接下來看什麼\n- 無"},
                    ensure_ascii=False,
                )
            return json.dumps({"reading_markdown": _reading("較弱")}, ensure_ascii=False)

        _subject, body, note = attach_peer_reading(
            _state(),
            complete=complete,
            read_text=lambda path: "",
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("上次輸出未通過檢查", calls[1])
        self.assertIn("## 怎麼讀這次比較", body)
        self.assertNotIn("-9.99%", body)
        self.assertIn("含規則結論與解讀", note)
        self.assertIn("共同題材：面板。", body)

    def test_both_attempts_fall_back_to_rule_letter(self) -> None:
        def complete(prompt: str) -> str:
            del prompt
            raise RuntimeError("agy down")

        _subject, body, note = attach_peer_reading(
            _state(),
            complete=complete,
            read_text=lambda path: "",
        )
        self.assertIn("解讀未生成，這封只有規則比較。", body)
        self.assertIn("區間報酬落後（友達 -2.10%）", body)
        self.assertNotIn("agy down", body)
        self.assertIn("只有規則比較", note)

    def test_position_report_is_required_in_the_second_attempt(self) -> None:
        state = _state()
        state["position_entered"] = True
        state["position_md_path"] = "position.md"
        calls: list[str] = []

        def complete(prompt: str) -> str:
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps({"reading_markdown": _reading("較弱")}, ensure_ascii=False)
            extra = "## 部位含義\n部位報告只說明這次較弱，沒有新數字。\n\n"
            return json.dumps({"reading_markdown": _reading("較弱", extra=extra)}, ensure_ascii=False)

        def read_text(path: str | None) -> str:
            if path == "position.md":
                return "部位已偏高，這次較弱宜縮小。"
            return "研究：區間仍弱。"

        _subject, body, note = attach_peer_reading(state, complete=complete, read_text=read_text)
        self.assertEqual(len(calls), 2)
        self.assertIn("部位報告", calls[0])
        self.assertIn("## 部位含義", body)
        self.assertIn("已進入部位報告：position.md", body)
        self.assertIn("含規則結論與解讀", note)


if __name__ == "__main__":
    unittest.main()
