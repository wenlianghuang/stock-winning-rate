"""Shared fact-consistency checks for agy report bodies (report-gate & position-gate).

The narrative produced by agy must not contradict the deterministic chip facts
(see ``chip_signals.ChipFacts``). This module centralises those checks so both
gates share one implementation — fixing a false-positive here fixes it for both.

``run_fact_checks`` returns a list of ``(code, message)`` tuples; each caller
wraps them into its own ``ValidationIssue`` type.
"""

from __future__ import annotations

import re

MIN_FACT_CITATIONS = 2

# 當日方向檢查優先鎖定的段落關鍵字（找不到時退回現況段落）
TODAY_SECTION_KEYWORDS = ("當日籌碼", "籌碼解讀")

# 情境推演/操作情境章節會合理提及「若外資轉買」等反向假設，方向檢查須排除
_SCENARIO_MARKERS = ("情境推演", "操作情境", "情境", "推演", "短中線")

FOREIGN_BULLISH_PHRASES = (
    "外資大買",
    "外資買超",
    "外資轉買",
    "外資買盤",
    "外資偏多",
    "外資站買方",
    "外資持續買",
    "外資買進",
    "外資敲進",
)
FOREIGN_BEARISH_PHRASES = (
    "外資大賣",
    "外資賣超",
    "外資轉賣",
    "外資偏空",
    "外資站賣方",
    "外資持續賣",
    "外資調節",
    "外資賣出",
    "外資出脫",
)
MA5_ABOVE_PHRASES = (
    "站上MA5",
    "站回MA5",
    "站上均線",
    "站穩均線",
    "突破均線",
    "站上5日線",
    "收復均線",
    "站回5日",
)
MA5_BELOW_PHRASES = (
    "跌破MA5",
    "跌破均線",
    "失守均線",
    "跌破5日線",
    "跌破5日均線",
    "跌破月線",
)
CHIP_HEALTHY_PHRASES = (
    "籌碼健康",
    "量價配合良好",
    "量價齊揚",
    "籌碼穩定",
    "籌碼結構良好",
    "籌碼面樂觀",
)

FactIssue = tuple[str, str]


def _contains_any(text: str, phrases: tuple[str, ...]) -> str | None:
    for phrase in phrases:
        if phrase in text:
            return phrase
    return None


def _strip_tables(text: str) -> str:
    """Drop Markdown table rows so news headlines don't trip direction checks.

    新聞表格常含「外資賣超/調節」等字，與個股當日方向無關，須先排除。
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("|")
    )


def _current_state_region(text: str) -> str:
    """Return the portion describing the *current* state (before scenarios)."""
    cut = len(text)
    for marker in _SCENARIO_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut]


def _slice_after_keywords(body: str, keywords: tuple[str, ...]) -> str:
    lines = body.splitlines()
    start = None
    for index, line in enumerate(lines):
        if any(keyword in line for keyword in keywords):
            start = index + 1
            break
    if start is None:
        return ""

    chunk: list[str] = []
    for line in lines[start:]:
        if chunk and re.match(r"^##\s+", line.strip()):
            break
        chunk.append(line)
    return "\n".join(chunk)


def _direction_region(text: str, today_keywords: tuple[str, ...]) -> str:
    """Region used for present-tense direction checks (tables stripped)."""
    region = _strip_tables(_slice_after_keywords(text, today_keywords))
    if not region.strip():
        region = _strip_tables(_current_state_region(text))
    return region


def _count_fact_citations(text: str, facts) -> tuple[int, int]:
    """Count how many *asserted* fact concepts the narrative references.

    Robust to number formatting: matches on stable concept keywords rather than
    exact anchor strings (the LLM is told not to repeat raw numbers).
    """
    concepts: list[bool] = []

    if getattr(facts, "foreign_direction", 0) != 0:
        concepts.append("外資" in text)
    if getattr(facts, "foreign_streak_days", 0) >= 2:
        concepts.append("連續" in text and "外資" in text)
    if getattr(facts, "major_available", False) and getattr(
        facts, "major_net_lots", None
    ) is not None:
        concepts.append("主力" in text)
    if getattr(facts, "ma5_position", "unknown") in {"above", "below"}:
        concepts.append(
            "MA5" in text or "均線" in text or "5日線" in text or "5 日線" in text
        )
    if getattr(facts, "price_trend", "unknown") in {"up", "down"}:
        concepts.append("區間" in text or "趨勢" in text)
    if getattr(facts, "divergences", None):
        concepts.append("背離" in text)

    total = len(concepts)
    cited = sum(1 for hit in concepts if hit)
    return cited, total


def run_fact_checks(
    body: str,
    facts,
    *,
    today_keywords: tuple[str, ...] = TODAY_SECTION_KEYWORDS,
) -> list[FactIssue]:
    """Return fact-consistency issues as ``(code, message)`` tuples.

    Guard against the "today vs period" ambiguity: if the region contains BOTH a
    bullish and a bearish phrase (e.g. 今日買超但區間賣超), the narrative is a
    legitimate mixed description and is NOT flagged.
    """
    if facts is None:
        return []

    text = body.strip()
    region = _direction_region(text, today_keywords)
    issues: list[FactIssue] = []

    foreign_dir = getattr(facts, "foreign_direction", 0)
    if foreign_dir != 0:
        bull = _contains_any(region, FOREIGN_BULLISH_PHRASES)
        bear = _contains_any(region, FOREIGN_BEARISH_PHRASES)
        if foreign_dir > 0 and bear and not bull:
            issues.append(
                (
                    "fact_foreign_direction",
                    f"外資今日為買超，但現況段出現偏空敘述「{bear}」，與系統 facts 矛盾",
                )
            )
        elif foreign_dir < 0 and bull and not bear:
            issues.append(
                (
                    "fact_foreign_direction",
                    f"外資今日為賣超，但現況段出現偏多敘述「{bull}」，與系統 facts 矛盾",
                )
            )

    ma5_position = getattr(facts, "ma5_position", "unknown")
    if ma5_position in {"above", "below"}:
        above = _contains_any(region, MA5_ABOVE_PHRASES)
        below = _contains_any(region, MA5_BELOW_PHRASES)
        if ma5_position == "below" and above and not below:
            issues.append(
                (
                    "fact_ma5_position",
                    f"收盤低於 MA5，但現況段出現「{above}」，與系統 facts 矛盾",
                )
            )
        elif ma5_position == "above" and below and not above:
            issues.append(
                (
                    "fact_ma5_position",
                    f"收盤高於 MA5，但現況段出現「{below}」，與系統 facts 矛盾",
                )
            )

    if getattr(facts, "divergences", None):
        hit = _contains_any(_strip_tables(text), CHIP_HEALTHY_PHRASES)
        if hit:
            issues.append(
                (
                    "fact_divergence_ignored",
                    f"facts 已標記量價背離/風險旗標，正文卻描述「{hit}」",
                )
            )

    cited, total = _count_fact_citations(text, facts)
    if total >= 2:
        required = min(MIN_FACT_CITATIONS, total)
        if cited < required:
            issues.append(
                (
                    "anchors_underused",
                    f"正文引用的系統籌碼事實不足（{cited}/{total} 概念），"
                    f"至少須明確引用 {required} 項 facts 概念",
                )
            )

    return issues
