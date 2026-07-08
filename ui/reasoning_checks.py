"""Reasoning-quality checks for agy report bodies (report-gate & position-gate).

Validates that narratives complete the inference chain — triggers, evidence
linkage, and actionable watch items — without re-checking factual direction.
"""

from __future__ import annotations

import re

from fact_checks import _slice_after_keywords, _strip_tables

ReasoningIssue = tuple[str, str]

_CHIP_KEYWORDS = ("外資", "主力", "投信", "自營", "融資", "融券", "籌碼", "當沖", "借券")
_NEWS_KEYWORDS = ("新聞", "事件", "利多", "利空", "公告", "法說", "訂單", "財報")
_TRIGGER_KEYWORDS = ("若", "觸發", "條件", "則", "一旦", "當")
_CONTINUATION_KEYWORDS = ("延續", "轉折", "背離", "連續")
_OBSERVABLE_KEYWORDS = (
    "外資",
    "主力",
    "投信",
    "融資",
    "融券",
    "MA5",
    "均線",
    "5日線",
    "成交量",
    "突破",
    "跌破",
    "連續",
    "當沖",
    "借券",
    "背離",
)


def parse_news_titles(news_text: str | None) -> list[str]:
    if not news_text:
        return []
    return [
        title.strip()
        for title in re.findall(r"^標題：(.+)$", news_text, re.MULTILINE)
        if title.strip()
    ]


def _title_overlap(title: str, body: str) -> bool:
    normalized = re.sub(r"\s+", "", body.lower())
    words = [word for word in re.split(r"\W+", title.lower()) if len(word) >= 4]
    if len(words) >= 2:
        hits = sum(1 for word in words if word in normalized)
        if hits >= max(2, len(words) // 2):
            return True
    compact = re.sub(r"\s+", "", title.lower())
    if len(compact) >= 8 and compact[:12] in normalized:
        return True
    return title in body


def _contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _count_cited_news(titles: list[str], body: str) -> int:
    return sum(1 for title in titles if _title_overlap(title, body))


def run_reasoning_checks(
    body: str,
    facts,
    *,
    has_news: bool = False,
    news_titles: list[str] | None = None,
    cross_keywords: tuple[str, ...] = ("交叉", "對照"),
    scenario_keywords: tuple[str, ...] = ("情境推演", "情境", "短中線"),
    trend_keywords: tuple[str, ...] = ("趨勢", "近"),
    watch_keywords: tuple[str, ...] = ("觀察",),
    min_cited_news: int = 1,
) -> list[ReasoningIssue]:
    text = body.strip()
    if not text:
        return []

    issues: list[ReasoningIssue] = []
    cross_region = _strip_tables(_slice_after_keywords(text, cross_keywords))
    scenario_region = _slice_after_keywords(text, scenario_keywords)
    trend_region = _slice_after_keywords(text, trend_keywords)
    watch_region = _slice_after_keywords(text, watch_keywords)

    if cross_region.strip():
        has_chip = _contains_any_keyword(cross_region, _CHIP_KEYWORDS)
        has_news_ref = _contains_any_keyword(cross_region, _NEWS_KEYWORDS)
        if has_news and not (has_chip and has_news_ref):
            issues.append(
                (
                    "reasoning_cross_no_evidence",
                    "「交叉對照」須同時結合籌碼與新聞/事件（各至少一項依據）",
                )
            )
        elif not has_news and not has_chip:
            issues.append(
                (
                    "reasoning_cross_no_evidence",
                    "「交叉對照」須明確引用籌碼面依據（外資/主力/融資券等）",
                )
            )

    if scenario_region.strip() and not _contains_any_keyword(
        scenario_region, _TRIGGER_KEYWORDS
    ):
        issues.append(
            (
                "reasoning_scenario_no_trigger",
                "「情境推演」須含觸發條件（若/觸發/條件/則/一旦等）",
            )
        )

    streak_days = int(getattr(facts, "foreign_streak_days", 0) or 0)
    if (
        facts is not None
        and streak_days >= 2
        and trend_region.strip()
        and not _contains_any_keyword(trend_region, _CONTINUATION_KEYWORDS)
    ):
        issues.append(
            (
                "reasoning_trend_no_continuation",
                f"外資近 {streak_days} 日連續買賣，趨勢段須描述延續/轉折/背離",
            )
        )

    if getattr(facts, "major_foreign_divergence", False):
        combined = trend_region + cross_region
        if combined.strip() and not _contains_any_keyword(
            combined, ("背離", "分歧", "不同步", "不一致")
        ):
            issues.append(
                (
                    "reasoning_major_foreign_unmentioned",
                    "facts 標記主力與外資背離，趨勢或交叉對照須說明此分歧",
                )
            )

    if watch_region.strip():
        watch_lines = [
            line
            for line in watch_region.splitlines()
            if re.match(r"^\s*[-*\d]", line.strip())
        ]
        if watch_lines:
            actionable = sum(
                1
                for line in watch_lines
                if _contains_any_keyword(line, _OBSERVABLE_KEYWORDS)
            )
            if actionable < min(2, len(watch_lines)):
                issues.append(
                    (
                        "reasoning_watch_not_actionable",
                        "「觀察重點」須含可觀察指標（外資/主力/均線/成交量/突破跌破等）",
                    )
                )

    if has_news and news_titles:
        cited = _count_cited_news(news_titles, _strip_tables(text))
        if cited < min_cited_news:
            issues.append(
                (
                    "reasoning_news_uncited",
                    f"交叉對照或趨勢段應引用至少 {min_cited_news} 則新聞標題關鍵字"
                    f"（目前約 {cited} 則）",
                )
            )

    return issues
