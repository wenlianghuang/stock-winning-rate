"""Validate concept-news reports against a fixed event table. No network and no model."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "concept-news"))

from process_report import (  # noqa: E402
    build_report_prompt,
    execute_report,
    extract_body,
    main,
    render_summary,
)
from validate_concept_report import (  # noqa: E402
    BUY_SELL_PATTERNS,
    EMPTY_NOTICE,
    FORBIDDEN_PHRASES,
    NO_AFTER_CLOSE,
    validate_concept_report,
)

sys.path.insert(0, str(ROOT / ".agents" / "skills" / "us-tech-news"))
from validate_tech_report import BUY_SELL_PATTERNS as TECH_PATTERNS  # noqa: E402
from validate_tech_report import FORBIDDEN_PHRASES as TECH_FORBIDDEN  # noqa: E402

TITLE = "友達與群創同步調漲面板報價"
LONG = "面板報價上修"
SHORT = "面板報價"


PUBLISHED = "2026-10-28T18:00:00+08:00"
OPINION = (
    "這批收盤後的稿是在講資金進出，不是這個題材的報價、產能或出貨。"
    "同一件事被寫成多則時，標題數量會讓人高估分量，不該照單全收。"
    "公司公告或合作若沒有接到本業，就不能上升成營運轉折。"
    "早盤漲跌和收盤後的統計衝突時，結論站在收盤後，早盤只是過程。"
)


def _source(title: str, url: str = "https://www.cna.com.tw/a") -> dict:
    return {
        "title": title,
        "url": url,
        "publisher": "中央社",
        "published": PUBLISHED,
        "origin": "searxng",
        "summary": "同步調漲",
    }


def _event(title: str, ids: list[str], *, source_title: str | None = None) -> dict:
    return {
        "event_id": title,
        "title": title,
        "published": PUBLISHED,
        "sources": [_source(source_title or title)],
        "mentioned_ids": ids,
        "theme_only": not ids,
    }


def _table(*events: dict) -> dict:
    return {
        "theme_id": "panel",
        "label": "面板",
        "as_of": "2026-10-28",
        "collected_at": "2026-10-28T22:41:31+08:00",
        "lookback_hours": 48,
        "events": list(events),
    }


def _page(title: str, named: str = "2409、3481") -> str:
    return (
        f"## 看法\n"
        f"{OPINION}\n\n"
        f"## 收盤後\n- {title}：{named}\n\n"
        f"## 來源列表\n- {title}｜中央社｜https://www.cna.com.tw/a\n"
    )


def _empty_page() -> str:
    line = f"{EMPTY_NOTICE}。"
    return (
        f"## 看法\n{line}\n\n"
        f"## 收盤後\n{line}\n\n"
        f"## 來源列表\n{line}\n"
    )


class ConceptReportValidationTests(unittest.TestCase):
    def test_forbidden_phrases_match_the_tech_daily(self) -> None:
        self.assertEqual(FORBIDDEN_PHRASES, TECH_FORBIDDEN)
        self.assertEqual(
            [item.pattern for item in BUY_SELL_PATTERNS],
            [item.pattern for item in TECH_PATTERNS],
        )

    def test_quoted_event_passes(self) -> None:
        result = validate_concept_report(
            _page(TITLE),
            _table(_event(TITLE, ["2409", "3481"])),
        )
        self.assertTrue(result.passed, result.summary_lines())

    def test_one_event_satisfies_a_higher_citation_floor(self) -> None:
        result = validate_concept_report(
            _page(TITLE),
            _table(_event(TITLE, ["2409", "3481"])),
            min_citations=3,
        )
        self.assertTrue(result.passed, result.summary_lines())

    def test_invented_title_fails(self) -> None:
        body = _page(TITLE).replace(
            f"## 收盤後\n- {TITLE}",
            f"## 收盤後\n- {TITLE}\n- 自創的面板標題",
            1,
        )
        result = validate_concept_report(body, _table(_event(TITLE, ["2409", "3481"])))
        self.assertIn("unknown_title", [issue.code for issue in result.issues])

    def test_stock_outside_the_event_fails(self) -> None:
        result = validate_concept_report(
            _page(TITLE, "2454"),
            _table(_event(TITLE, ["2409", "3481"])),
        )
        self.assertIn("stock_not_mentioned", [issue.code for issue in result.issues])

    def test_theme_only_rejects_a_stock_id(self) -> None:
        bare = validate_concept_report(
            _page(TITLE, "未點名個股"),
            _table(_event(TITLE, [])),
        )
        named = validate_concept_report(
            _page(TITLE, "2409"),
            _table(_event(TITLE, [])),
        )
        self.assertTrue(bare.passed, bare.summary_lines())
        self.assertIn("stock_not_mentioned", [issue.code for issue in named.issues])

    def test_mention_phrase_uses_the_event_ids(self) -> None:
        allowed = _page(TITLE).replace(
            f"- {TITLE}：2409、3481",
            f"- {TITLE}：2409 被點名。",
        )
        blocked = _page(TITLE).replace(
            f"- {TITLE}：2409、3481",
            f"- {TITLE}：2454 被點名。",
        )
        table = _table(_event(TITLE, ["2409", "3481"]))
        self.assertTrue(validate_concept_report(allowed, table).passed)
        self.assertIn(
            "stock_not_mentioned",
            [issue.code for issue in validate_concept_report(blocked, table).issues],
        )

    def test_nested_title_counts_only_the_longer_event(self) -> None:
        table = _table(_event(SHORT, ["3481"]), _event(LONG, ["2409"]))
        result = validate_concept_report(_page(LONG, "2409"), table, min_citations=2)
        codes = [issue.code for issue in result.issues]
        self.assertEqual(codes, ["too_few_citations"])
        self.assertIn("1 則", result.issues[0].message)

    def test_source_title_alone_does_not_count_as_a_citation(self) -> None:
        source_title = "面板廠同步調漲報價"
        body = _page(source_title)
        result = validate_concept_report(
            body,
            _table(_event(TITLE, ["2409", "3481"], source_title=source_title)),
        )
        self.assertIn("too_few_citations", [issue.code for issue in result.issues])
        self.assertNotIn("unknown_title", [issue.code for issue in result.issues])

    def test_zero_events_need_the_empty_notice(self) -> None:
        present = validate_concept_report(_empty_page(), _table())
        missing = validate_concept_report(
            _empty_page().replace(EMPTY_NOTICE, "今天沒有新聞"),
            _table(),
        )
        self.assertTrue(present.passed, present.summary_lines())
        self.assertIn("missing_empty_notice", [issue.code for issue in missing.issues])

    def test_forbidden_phrase_and_advice(self) -> None:
        table = _table(_event(TITLE, ["2409", "3481"]))
        phrase = validate_concept_report(_page(TITLE) + "\n建議買進\n", table)
        advice = validate_concept_report(_page(TITLE) + "\n可以買進\n", table)
        self.assertIn("forbidden_phrase", [issue.code for issue in phrase.issues])
        self.assertIn("forbidden_advice", [issue.code for issue in advice.issues])

    def test_missing_section(self) -> None:
        body = _page(TITLE).replace("## 來源列表\n", "")
        result = validate_concept_report(body, _table(_event(TITLE, ["2409", "3481"])))
        self.assertIn("missing_sources", [issue.code for issue in result.issues])

    def test_prompt_asks_for_a_fused_after_close_reading(self) -> None:
        prompt = build_report_prompt(
            _table(_event(TITLE, ["2409", "3481"])),
            Path("events.json"),
            min_citations=1,
        )
        self.assertIn("## 看法", prompt)
        self.assertIn("after_close", prompt)
        self.assertIn("13:30", prompt)
        self.assertIn("不要寫「稿面寫了」", prompt)
        self.assertIn("阿拉伯數字", prompt)
        self.assertIn("判斷和結論", prompt)
        self.assertIn("不要解釋", prompt)
        self.assertNotIn("並陳差了多少", prompt)
        self.assertNotIn("## 稿面寫了什麼與沒寫什麼", prompt)
        self.assertNotIn("## 見解", prompt)

    def test_listing_every_headline_in_the_reading_fails(self) -> None:
        second = "群創公告取得使用權資產"
        table = _table(_event(TITLE, ["2409"]), _event(second, ["3481"]))
        stitched = (
            f"## 看法\n- {TITLE}\n- {second}\n\n"
            f"## 收盤後\n- {TITLE}：2409\n- {second}：3481\n\n"
            f"## 來源列表\n"
            f"- {TITLE}｜中央社｜https://www.cna.com.tw/a\n"
            f"- {second}｜中央社｜https://www.cna.com.tw/a\n"
        )
        fused = (
            f"## 看法\n{OPINION}\n\n"
            f"## 收盤後\n- {TITLE}：2409\n- {second}：3481\n\n"
            f"## 來源列表\n"
            f"- {TITLE}｜中央社｜https://www.cna.com.tw/a\n"
            f"- {second}｜中央社｜https://www.cna.com.tw/a\n"
        )
        self.assertIn(
            "stitched_reading",
            [issue.code for issue in validate_concept_report(stitched, table).issues],
        )
        fused_result = validate_concept_report(fused, table)
        self.assertTrue(fused_result.passed, fused_result.summary_lines())

    def test_intraday_copy_is_not_a_settled_source(self) -> None:
        morning = _event(TITLE, ["2409"])
        morning["published"] = "2026-10-28T09:56:00+08:00"
        morning["sources"][0]["published"] = morning["published"]
        notice = (
            f"## 看法\n{NO_AFTER_CLOSE}。盤中漲跌不拿來下結論。\n\n"
            f"## 收盤後\n{NO_AFTER_CLOSE}。\n\n"
            f"## 來源列表\n{NO_AFTER_CLOSE}。\n"
        )
        table = _table(morning)
        present = validate_concept_report(notice, table)
        self.assertTrue(present.passed, present.summary_lines())
        cited = notice.replace("## 收盤後\n", f"## 收盤後\n- {TITLE}：2409\n", 1)
        blocked = validate_concept_report(cited, table)
        self.assertIn("unknown_title", [issue.code for issue in blocked.issues])

    def test_prior_day_close_is_context_not_evidence(self) -> None:
        prior_title = "外資賣超面板調節"
        today = _event(TITLE, ["2409"])
        today["sources"][0]["summary"] = "外資買超"
        prior = _event(prior_title, ["2409"])
        prior["published"] = "2026-10-27T17:41:00+08:00"
        prior["sources"][0]["published"] = prior["published"]
        prior["sources"][0]["summary"] = "外資賣超"
        table = _table(today, prior)
        cited = _page(TITLE, "2409").replace(
            f"- {TITLE}：2409",
            f"- {TITLE}：2409\n- {prior_title}：2409",
        )
        blocked = validate_concept_report(cited, table)
        codes = [issue.code for issue in blocked.issues]
        self.assertIn("prior_day_title", codes)
        self.assertIn("missing_prior_relation", codes)

        kept = _page(TITLE, "2409").replace(
            "早盤只是過程。",
            "早盤只是過程。與前一日收盤違逆，結論仍以今日為準。",
        )
        passed = validate_concept_report(kept, table)
        self.assertTrue(passed.passed, passed.summary_lines())
        summary = render_summary(table, kept)
        self.assertIn("（當日收盤 1）", summary)
        self.assertNotIn(prior_title, extract_body(summary))

        reversed_word = kept.replace("違逆", "順從")
        wrong = validate_concept_report(reversed_word, table)
        self.assertIn("wrong_prior_relation", [issue.code for issue in wrong.issues])

    def test_prompt_keeps_the_previous_close_out_of_the_table(self) -> None:
        prior_title = "外資賣超面板調節"
        today = _event(TITLE, ["2409"])
        today["sources"][0]["summary"] = "外資買超"
        prior = _event(prior_title, ["2409"])
        prior["published"] = "2026-10-27T17:41:00+08:00"
        prior["sources"][0]["published"] = prior["published"]
        prior["sources"][0]["summary"] = "外資賣超"
        prompt = build_report_prompt(
            _table(today, prior),
            Path("events.json"),
            min_citations=1,
        )
        self.assertIn("與前一日收盤：違逆", prompt)
        self.assertIn(TITLE, prompt)
        self.assertNotIn(prior_title, prompt)
        self.assertNotIn("外資賣超", prompt)

        plain = build_report_prompt(
            _table(_event(TITLE, ["2409"])),
            Path("events.json"),
            min_citations=1,
        )
        self.assertIn("不要寫「違逆」", plain)
        self.assertNotIn("與前一日收盤：", plain)

    def test_after_close_section_rejects_an_intraday_title(self) -> None:
        morning = _event("面板雙虎早盤不同調", ["2409"])
        morning["published"] = "2026-10-27T09:56:00+08:00"
        morning["sources"][0]["published"] = morning["published"]
        body = _page(TITLE, "2409").replace(
            f"- {TITLE}：2409",
            f"- {TITLE}：2409\n- 面板雙虎早盤不同調：2409",
        )
        result = validate_concept_report(body, _table(morning, _event(TITLE, ["2409"])))
        self.assertIn("unsettled_title", [issue.code for issue in result.issues])

    def test_per_article_copy_phrase_fails(self) -> None:
        body = _page(TITLE).replace(
            "這批收盤後的稿是在講資金進出，不是這個題材的報價、產能或出貨。",
            "稿面寫了同步調漲。",
        )
        result = validate_concept_report(body, _table(_event(TITLE, ["2409", "3481"])))
        self.assertIn("stitched_phrase", [issue.code for issue in result.issues])

    def test_after_close_section_rejects_an_explanation(self) -> None:
        body = _page(TITLE).replace(
            f"- {TITLE}：2409、3481",
            f"- {TITLE}：2409 買超，與另一稿相差 0.1 萬張，係將標題金額誤作回補",
        )
        result = validate_concept_report(body, _table(_event(TITLE, ["2409", "3481"])))
        self.assertIn("close_explains", [issue.code for issue in result.issues])

    def test_opinion_cannot_recopy_figures_or_the_headline(self) -> None:
        table = _table(_event(TITLE, ["2409", "3481"]))
        figures = _page(TITLE).replace(
            "早盤只是過程。",
            "早盤只是過程。友達買超 6.8 萬張。",
        )
        headline = _page(TITLE).replace(
            "早盤只是過程。",
            f"早盤只是過程。{TITLE}",
        )
        self.assertIn(
            "opinion_recites_figures",
            [issue.code for issue in validate_concept_report(figures, table).issues],
        )
        self.assertIn(
            "opinion_copies_title",
            [issue.code for issue in validate_concept_report(headline, table).issues],
        )


class ConceptReportGateTests(unittest.TestCase):
    def test_second_round_passes_and_writes_summary(self) -> None:
        table = _table(_event(TITLE, ["2409", "3481"]))
        good = _page(TITLE)
        prompts: list[str] = []

        def complete(prompt: str) -> tuple[str, int]:
            prompts.append(prompt)
            if len(prompts) == 1:
                return good + "\n建議買進\n", 0
            return good, 0

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder / "2026-10-28_events.json"
            events.write_text("{}\n", encoding="utf-8")
            code = execute_report(
                table,
                events,
                summary_path=folder / "2026-10-28_summary.md",
                gate_log_path=folder / "2026-10-28.gate.log",
                gate_rounds_dir=folder / "2026-10-28.gate.rounds",
                max_rounds=2,
                min_citations=1,
                complete=complete,
            )
            summary = (folder / "2026-10-28_summary.md").read_text(encoding="utf-8")
            rows = [
                json.loads(line)
                for line in (folder / "2026-10-28.gate.log").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(code, 0)
        self.assertEqual(len(prompts), 2)
        self.assertIn("報告不應包含「建議買進」", prompts[1])
        self.assertIn(TITLE, summary)
        self.assertEqual([row["passed"] for row in rows], [False, True])
        self.assertTrue(validate_concept_report(extract_body(summary), table).passed)

    def test_failed_rounds_do_not_write_summary(self) -> None:
        table = _table(_event(TITLE, ["2409"]))

        def complete(_prompt: str) -> tuple[str, int]:
            return "沒有四段", 0

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            code = execute_report(
                table,
                folder / "2026-10-28_events.json",
                summary_path=folder / "2026-10-28_summary.md",
                gate_log_path=folder / "2026-10-28.gate.log",
                gate_rounds_dir=folder / "2026-10-28.gate.rounds",
                max_rounds=2,
                min_citations=1,
                complete=complete,
            )
            self.assertEqual(code, 1)
            self.assertFalse((folder / "2026-10-28_summary.md").exists())
            lines = (folder / "2026-10-28.gate.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len([line for line in lines if line.strip()]), 2)

    def test_missing_events_file_is_a_clear_error(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "2026-10-28_events.json"
            with contextlib.redirect_stderr(stderr):
                code = main(["--events", str(missing), "--date", "2026-10-28"])
        self.assertEqual(code, 1)
        self.assertIn("找不到事件表", stderr.getvalue())

    def test_existing_summary_skips_the_model(self) -> None:
        table = _table(_event(TITLE, ["2409", "3481"]))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder / "2026-10-28_events.json"
            events.write_text(json.dumps(table), encoding="utf-8")
            summary = folder / "2026-10-28_summary.md"
            summary.write_text("已有報告\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["--events", str(events)])
            self.assertEqual(code, 0)
            self.assertEqual(summary.read_text(encoding="utf-8"), "已有報告\n")
            self.assertFalse((folder / "2026-10-28.gate.log").exists())

    def test_validate_only_reads_the_saved_summary(self) -> None:
        table = _table(_event(TITLE, ["2409", "3481"]))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder / "2026-10-28_events.json"
            events.write_text(json.dumps(table), encoding="utf-8")
            summary = folder / "2026-10-28_summary.md"
            summary.write_text(render_summary(table, _page(TITLE)), encoding="utf-8")
            self.assertEqual(main(["--events", str(events), "--validate-only"]), 0)
            summary.write_text(render_summary(table, "只有一段"), encoding="utf-8")
            self.assertEqual(main(["--events", str(events), "--validate-only"]), 1)


if __name__ == "__main__":
    unittest.main()
