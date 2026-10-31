"""Validate a concept-news report against an event table. No network and no model.

The body is one page: a judgment, the after-close basis, and sources.
Only events published at or after 13:30 Taipei time count as settled. The
judgment may not recopy titles or figures. Titles in the basis must come from
those events. A stock id written as named must belong to that event. Forbidden
phrases match the US tech daily report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cluster_events import prior_relation, split_report_events

MIN_CITED_EVENTS = 1
EMPTY_NOTICE = "沒有通過篩選的新聞"
NO_AFTER_CLOSE = "還沒有收盤後的新聞"

SECTIONS: tuple[tuple[str, str], ...] = (
    ("opinion", "看法"),
    ("settled", "收盤後"),
    ("sources", "來源列表"),
)

# 看法要長到放得下判斷。短過這個長度，幾乎一定只是把新聞改寫一句。
MIN_OPINION_CHARS = 80
_DIGIT = re.compile(r"\d")

# 舊格式把每則稿抄一句再補「沒寫什麼」。新報告不准回到這個句型。
STITCH_MARKERS = (
    "稿面寫了",
    "稿面敘述",
    "稿面記載",
)

# 收盤後只留稿子寫的事實。差額、換算、意義都是解釋，要寫在看法。
CLOSE_EXPLAINS = (
    "相差",
    "係將",
    "誤作",
    "不宜",
    "這代表",
    "意味",
    "換言之",
    "也就是說",
    "折合",
)

# 與 us-tech-news validate_tech_report 的禁語相同。
FORBIDDEN_PHRASES = (
    "工作摘要",
    "本次工作摘要",
    "建議買進",
    "建議賣出",
    "強力買進",
    "強力賣出",
    "目標價",
)

BUY_SELL_PATTERNS = (
    re.compile(r"應該(買|賣|加碼|減碼)"),
    re.compile(r"可以(買|賣|加碼|減碼)"),
)

_HEADING = re.compile(r"(?m)^##\s+(\S.*?)\s*$")
_BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(\S.*?)\s*$")
_STOCK_ID = re.compile(r"(?<!\d)(\d{4,6})(?!\d)")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MENTION_ID = re.compile(
    r"(?<!\d)(\d{4,6})(?!\d)\s*被(?:點名|提到)|被(?:點名|提到)\s*(?<!\d)(\d{4,6})(?!\d)"
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass
class ValidationResult:
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        return [issue.message for issue in self.issues]


def required_citation_count(event_count: int, minimum: int = MIN_CITED_EVENTS) -> int:
    """How many representative titles the body must quote."""
    if event_count <= 0:
        return 0
    floor = minimum if minimum > 0 else MIN_CITED_EVENTS
    return min(floor, event_count)


def validate_concept_report(
    body: str,
    table: dict,
    *,
    min_citations: int = MIN_CITED_EVENTS,
) -> ValidationResult:
    if not isinstance(table, dict) or not isinstance(table.get("events"), list):
        raise ValueError("events 格式錯誤")
    events = [event for event in table["events"] if isinstance(event, dict)]
    settled, prior_close, other = split_report_events(table)
    unsettled = prior_close + other
    relation = prior_relation(settled, prior_close)
    issues: list[ValidationIssue] = []
    text = body.strip()
    sections = _sections(text)

    for code, label in SECTIONS:
        if label not in sections:
            issues.append(ValidationIssue(f"missing_{code}", f"缺少必要段落：{label}"))

    basis = _basis_sections(sections)
    if not events:
        if EMPTY_NOTICE not in text:
            issues.append(
                ValidationIssue(
                    "missing_empty_notice",
                    f"零事件時正文必須出現「{EMPTY_NOTICE}」",
                )
            )
        for section in basis.values():
            for bullet in _bullets(section):
                if EMPTY_NOTICE in bullet:
                    continue
                issues.append(
                    ValidationIssue(
                        "unknown_title",
                        f"正文標題對不上事件表：{_clip(bullet)}",
                    )
                )
            for stock_id in _stock_ids(section):
                issues.append(
                    ValidationIssue(
                        "stock_not_mentioned",
                        f"沒有事件時不應把 {stock_id} 寫成被點名",
                    )
                )
    elif not settled:
        if NO_AFTER_CLOSE not in text:
            issues.append(
                ValidationIssue(
                    "missing_after_close_notice",
                    f"沒有收盤後的事件時正文必須出現「{NO_AFTER_CLOSE}」",
                )
            )
        for section in basis.values():
            for bullet in _bullets(section):
                if NO_AFTER_CLOSE in bullet:
                    continue
                issues.append(
                    ValidationIssue(
                        "unknown_title",
                        f"正文標題對不上收盤後的事件：{_clip(bullet)}",
                    )
                )
            for stock_id in _stock_ids(section):
                issues.append(
                    ValidationIssue(
                        "stock_not_mentioned",
                        f"沒有收盤後的事件時不應把 {stock_id} 寫成被點名",
                    )
                )
    else:
        cited = _cited_indexes(text, settled)
        need = required_citation_count(len(settled), min_citations)
        if len(cited) < need:
            issues.append(
                ValidationIssue(
                    "too_few_citations",
                    f"引用收盤後事件不足（{len(cited)} 則，至少需要 {need} 則）",
                )
            )
        for section in basis.values():
            for bullet in _bullets(section):
                if not _matching_events(bullet, settled):
                    issues.append(
                        ValidationIssue(
                            "unknown_title",
                            f"正文標題對不上收盤後的事件：{_clip(bullet)}",
                        )
                    )
        for bullet in _bullets(sections.get("收盤後", "")):
            matched = _matching_events(bullet, settled)
            for stock_id in _stock_ids(bullet, _matched_titles(bullet, settled)):
                if not matched or any(stock_id not in _mentioned(event) for event in matched):
                    issues.append(
                        ValidationIssue(
                            "stock_not_mentioned",
                            f"事件「{_display_title(matched)}」的正文把 {stock_id} 寫成被點名，但該事件的 mentioned_ids 沒有這個股號",
                        )
                    )
        for label, section in basis.items():
            for line in section.splitlines():
                for stock_id in _positive_mention_ids(line):
                    matched = _matching_events(line, settled)
                    if not matched or any(stock_id not in _mentioned(event) for event in matched):
                        issues.append(
                            ValidationIssue(
                                "stock_not_mentioned",
                                f"「{label}」把 {stock_id} 寫成被點名，但同一行的事件 mentioned_ids 沒有這個股號",
                            )
                        )
        for section in basis.values():
            for bullet in _bullets(section):
                for title in _titles_in(bullet, prior_close):
                    issues.append(
                        ValidationIssue(
                            "prior_day_title",
                            f"前一日的收盤不要列進依據，只在看法裡用違逆或順從帶過：{_clip(title)}",
                        )
                    )
                for title in _titles_in(bullet, other):
                    issues.append(
                        ValidationIssue(
                            "unsettled_title",
                            f"收盤後的段落不應列入盤中或盤前的標題：{_clip(title)}",
                        )
                    )
        _check_close_explains(sections.get("收盤後", ""), issues)
        if "看法" in sections:
            _check_opinion(
                sections["看法"],
                settled,
                unsettled,
                issues,
                relation=relation,
            )

    for marker in STITCH_MARKERS:
        if marker in text:
            issues.append(
                ValidationIssue(
                    "stitched_phrase",
                    f"報告不應逐則複述稿面（出現「{marker}」）",
                )
            )

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            issues.append(ValidationIssue("forbidden_phrase", f"報告不應包含「{phrase}」"))

    for pattern in BUY_SELL_PATTERNS:
        if pattern.search(text):
            issues.append(ValidationIssue("forbidden_advice", "報告不應包含明確買賣建議用語"))
            break

    return ValidationResult(passed=not issues, issues=issues)


def _basis_sections(sections: dict[str, str]) -> dict[str, str]:
    """Sections whose bullets must cite after-close events. The opinion is prose."""
    labels = {label for _code, label in SECTIONS if label != "看法"}
    return {label: text for label, text in sections.items() if label in labels}


def _check_close_explains(section: str, issues: list[ValidationIssue]) -> None:
    """The after-close list states the article. It does not interpret it."""
    for marker in CLOSE_EXPLAINS:
        if marker in section:
            issues.append(
                ValidationIssue(
                    "close_explains",
                    f"收盤後只留新聞事實，不要解釋（出現「{marker}」）。判斷和結論寫在看法",
                )
            )
            return


def _check_opinion(
    opinion: str,
    settled: list[dict],
    unsettled: list[dict],
    issues: list[ValidationIssue],
    *,
    relation: str | None = None,
) -> None:
    """The lead section has to be a judgment, not a second copy of the articles."""
    compact = re.sub(r"\s+", "", opinion)
    if len(compact) < MIN_OPINION_CHARS:
        issues.append(
            ValidationIssue(
                "opinion_too_short",
                "看法太短，只夠把新聞改寫一遍。請回答這是不是題材本身、重複標題該不該加權、公告有沒有接到本業、早盤要不要讓位給收盤",
            )
        )
    if _DIGIT.search(opinion):
        issues.append(
            ValidationIssue(
                "opinion_recites_figures",
                "看法不要重抄數字或股號，張數和金額留在收盤後",
            )
        )
    copied = False
    for event in settled + unsettled:
        for title in _titles_of(event):
            if title and title in opinion:
                issues.append(
                    ValidationIssue(
                        "opinion_copies_title",
                        f"看法不要照抄標題：{_clip(title)}",
                    )
                )
                copied = True
                break
        if copied:
            break
    if relation is None:
        if "違逆" in opinion or "順從" in opinion:
            issues.append(
                ValidationIssue(
                    "invented_prior_relation",
                    "前一日收盤對不上方向時，看法不要寫違逆或順從",
                )
            )
    else:
        other_word = "順從" if relation == "違逆" else "違逆"
        if relation not in opinion:
            issues.append(
                ValidationIssue(
                    "missing_prior_relation",
                    f"看法要用一句話寫明與前一日收盤「{relation}」，結論仍以今日為準",
                )
            )
        if other_word in opinion:
            issues.append(
                ValidationIssue(
                    "wrong_prior_relation",
                    f"與前一日收盤是「{relation}」，不要寫成「{other_word}」",
                )
            )
    if _lists_every_title(opinion, settled):
        issues.append(
            ValidationIssue(
                "stitched_reading",
                "看法不要把每則收盤後新聞各列一行",
            )
        )
    allowed: set[str] = set()
    for event in settled:
        allowed |= _mentioned(event)
    for stock_id in _positive_mention_ids(opinion):
        if stock_id not in allowed:
            issues.append(
                ValidationIssue(
                    "stock_not_mentioned",
                    f"看法把 {stock_id} 寫成被點名，但收盤後的事件沒有這個股號",
                )
            )


def _lists_every_title(section: str, events: list[dict]) -> bool:
    if len(events) < 2:
        return False
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    hits = 0
    for event in events:
        title = str(event.get("title") or "").strip()
        if not title:
            continue
        if any(line.lstrip("-*0123456789.) ").startswith(title) for line in lines):
            hits += 1
    return hits >= len(events)


def _titles_in(text: str, events: list[dict]) -> list[str]:
    found: list[str] = []
    for event in events:
        for title in _titles_of(event):
            if title in text and title not in found:
                found.append(title)
    return found


def _sections(body: str) -> dict[str, str]:
    matches = list(_HEADING.finditer(body))
    found: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        found[match.group(1).strip()] = body[start:end]
    return found


def _bullets(section: str) -> list[str]:
    bullets: list[str] = []
    for line in section.splitlines():
        match = _BULLET.match(line)
        if match:
            bullets.append(match.group(1).strip().strip("`").strip("*").strip())
    return bullets


def _titles_of(event: dict) -> tuple[str, ...]:
    titles = [str(event.get("title") or "").strip()]
    sources = event.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict):
                title = str(source.get("title") or "").strip()
                if title:
                    titles.append(title)
    return tuple(title for title in titles if title)


def _mentioned(event: dict) -> set[str]:
    raw = event.get("mentioned_ids")
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _matching_events(text: str, events: list[dict]) -> list[dict]:
    hits: list[tuple[dict, str]] = []
    for event in events:
        for title in _titles_of(event):
            if title in text:
                hits.append((event, title))
    titles = [title for _event, title in hits]
    matched: list[dict] = []
    for event, title in hits:
        if any(title != other and title in other for other in titles):
            continue
        if event not in matched:
            matched.append(event)
    return matched


def _matched_titles(text: str, events: list[dict]) -> list[str]:
    titles: list[str] = []
    for event in _matching_events(text, events):
        for title in _titles_of(event):
            if title in text and title not in titles:
                titles.append(title)
    return titles


def _representative_hit(bullet: str, event: dict, events: list[dict]) -> bool:
    title = str(event.get("title") or "").strip()
    if not title or title not in bullet:
        return False
    for other in events:
        other_title = str(other.get("title") or "").strip()
        if other_title and other_title != title and title in other_title and other_title in bullet:
            return False
    return True


def _cited_indexes(body: str, events: list[dict]) -> list[int]:
    cited: list[int] = []
    for section in _basis_sections(_sections(body)).values():
        for bullet in _bullets(section):
            for index, event in enumerate(events):
                if index in cited:
                    continue
                if _representative_hit(bullet, event, events):
                    cited.append(index)
    return cited


def _stock_ids(text: str, ignore_titles: list[str] | None = None) -> list[str]:
    cleaned = text
    for title in sorted(ignore_titles or [], key=len, reverse=True):
        cleaned = cleaned.replace(title, " ")
    cleaned = _ISO_DATE.sub(" ", cleaned)
    found: list[str] = []
    for stock_id in _STOCK_ID.findall(cleaned):
        if stock_id not in found:
            found.append(stock_id)
    return found


def _positive_mention_ids(line: str) -> list[str]:
    found: list[str] = []
    for match in _MENTION_ID.finditer(line):
        stock_id = match.group(1) or match.group(2)
        mention_at = line.find("被", match.start())
        prefix = line[max(0, mention_at - 2) : mention_at]
        if prefix.endswith(("未", "沒", "沒有")):
            continue
        if stock_id and stock_id not in found:
            found.append(stock_id)
    return found


def _display_title(events: list[dict]) -> str:
    if not events:
        return "（對不上事件）"
    return str(events[0].get("title") or "（無標題）")


def _clip(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= 40:
        return compact
    return compact[:40] + "…"
