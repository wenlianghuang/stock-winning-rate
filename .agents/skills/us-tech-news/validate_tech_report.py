"""Validate us-tech-news agy slide body against raw indices and news."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


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


class IndexSnapshotLike(Protocol):
    symbol: str
    name: str
    price: float | None
    change_pct: float | None


class NewsItemLike(Protocol):
    title: str
    feed_name: str


MIN_BODY_CHARS = 200
MIN_SLIDE_SEPARATORS = 5
MIN_TOTAL_BULLETS = 8
MIN_CITED_NEWS = 3

REQUIRED_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("overview", "市場概況", ("市場概況",)),
    ("index", "那斯達克與費城半導體指數解讀", ("那斯達克", "半導體")),
    ("semiconductor", "半導體與 AI 基建", ("半導體", "AI")),
    ("big_tech", "大型科技動態", ("大型科技", "科技動態")),
    ("cross", "跨主題連動分析", ("連動", "交叉")),
    ("watch", "值得後續關注", ("後續關注", "值得關注")),
    ("sources", "新聞來源", ("新聞來源",)),
)

MIN_H2_HEADINGS = len(REQUIRED_SECTIONS)

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


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _section_present(body: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in body for keyword in keywords)


def _value_variants(raw: str) -> list[str]:
    text = str(raw).strip()
    if not text:
        return []

    variants = {text}
    try:
        if "." in text:
            number = float(text.replace(",", ""))
            variants.add(f"{number:,.2f}")
            variants.add(f"{number:.2f}")
            variants.add(str(number))
            if number == int(number):
                variants.add(str(int(number)))
        else:
            number = int(text.replace(",", ""))
            variants.add(str(number))
            variants.add(f"{number:,}")
    except ValueError:
        pass
    return [variant for variant in variants if variant]


def _number_mentioned(raw: str, body: str) -> bool:
    normalized_body = _normalize(body)
    for variant in _value_variants(raw):
        if variant in body or _normalize(variant) in normalized_body:
            return True
    return False


def _change_pct_variants(change_pct: float) -> list[str]:
    sign = "+" if change_pct >= 0 else ""
    unsigned = f"{abs(change_pct):.2f}%"
    signed = f"{sign}{change_pct:.2f}%"
    return list({signed, unsigned, f"{change_pct:.2f}%"})


def _change_pct_mentioned(change_pct: float, body: str) -> bool:
    normalized_body = _normalize(body)
    for variant in _change_pct_variants(change_pct):
        if variant in body or _normalize(variant) in normalized_body:
            return True
    return False


def _count_slide_separators(body: str) -> int:
    return len(re.findall(r"(?:^|\n)---+\s*(?:\n|$)", body))


def _count_h2_headings(body: str) -> int:
    return len(re.findall(r"(?m)^##\s+\S", body))


def normalize_slide_body(body: str) -> str:
    """Insert --- before ## headings when agy omits Marp-style slide separators."""
    text = body.strip()
    if not text:
        return text
    if _count_slide_separators(text) >= MIN_SLIDE_SEPARATORS:
        return text
    if _count_h2_headings(text) < MIN_H2_HEADINGS:
        return text
    return re.sub(r"(?<!\A)\n(?=## )", "\n---\n", text)


def _slide_structure_ok(body: str) -> bool:
    return (
        _count_slide_separators(body) >= MIN_SLIDE_SEPARATORS
        or _count_h2_headings(body) >= MIN_H2_HEADINGS
    )


def split_slide_chunks(body: str) -> list[str]:
    """Split agy body into slide chunks (--- or ## headings)."""
    normalized = normalize_slide_body(body)
    chunks = [chunk.strip() for chunk in re.split(r"\n---+\n", normalized) if chunk.strip()]
    if len(chunks) > 1:
        return chunks
    if _count_h2_headings(normalized) >= 2:
        return [
            chunk.strip()
            for chunk in re.split(r"(?m)^(?=## )", normalized)
            if chunk.strip()
        ]
    return chunks


def _count_bullets(body: str) -> int:
    return len(re.findall(r"(?m)^\s*[-*]\s+\S", body))


def _title_overlap(raw_title: str, body: str) -> bool:
    normalized_body = _normalize(body.lower())
    words = [w for w in re.split(r"\W+", raw_title.lower()) if len(w) >= 4]
    if len(words) >= 2:
        hits = sum(1 for word in words if word in normalized_body)
        if hits >= max(2, len(words) // 2):
            return True
    compact = _normalize(raw_title.lower())
    if len(compact) >= 8 and compact[:12] in normalized_body:
        return True
    return raw_title in body


def _count_cited_news(items: list[NewsItemLike], body: str) -> int:
    cited = 0
    for item in items:
        if _title_overlap(item.title, body) or item.feed_name in body:
            cited += 1
    return cited


def validate_tech_report(
    body: str,
    *,
    indices: list[IndexSnapshotLike],
    items: list[NewsItemLike],
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    text = normalize_slide_body(body.strip())

    if len(text) < MIN_BODY_CHARS:
        issues.append(
            ValidationIssue(
                "too_short",
                f"報告正文過短（{len(text)} 字，至少需要 {MIN_BODY_CHARS} 字）",
            )
        )

    for code, label, keywords in REQUIRED_SECTIONS:
        if not _section_present(text, keywords):
            issues.append(
                ValidationIssue(
                    f"missing_{code}",
                    f"缺少必要 slide：{label}",
                )
            )

    if not _slide_structure_ok(text):
        separators = _count_slide_separators(text)
        headings = _count_h2_headings(text)
        issues.append(
            ValidationIssue(
                "missing_slide_separators",
                "slide 結構不足（"
                f"--- {separators}/{MIN_SLIDE_SEPARATORS}，"
                f"## {headings}/{MIN_H2_HEADINGS}）",
            )
        )

    bullets = _count_bullets(text)
    if bullets < MIN_TOTAL_BULLETS:
        issues.append(
            ValidationIssue(
                "too_few_bullets",
                f"bullet 列點不足（{bullets} 點，至少需要 {MIN_TOTAL_BULLETS} 點）",
            )
        )

    for snap in indices:
        if snap.price is None:
            continue
        price_text = f"{snap.price:.2f}"
        if not _number_mentioned(price_text, text):
            issues.append(
                ValidationIssue(
                    f"missing_index_price_{snap.symbol}",
                    f"報告未引用 {snap.name} 收盤價（{snap.price:,.2f}）",
                )
            )
        if snap.change_pct is not None and not _change_pct_mentioned(
            snap.change_pct, text
        ):
            sign = "+" if snap.change_pct >= 0 else ""
            issues.append(
                ValidationIssue(
                    f"missing_index_change_{snap.symbol}",
                    f"報告未引用 {snap.name} 漲跌幅（{sign}{snap.change_pct:.2f}%）",
                )
            )

    cited = _count_cited_news(items, text)
    if cited < MIN_CITED_NEWS:
        issues.append(
            ValidationIssue(
                "too_few_news_citations",
                f"「新聞來源」引用不足（約 {cited} 則，至少需要 {MIN_CITED_NEWS} 則）",
            )
        )

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            issues.append(
                ValidationIssue(
                    "forbidden_phrase",
                    f"報告不應包含「{phrase}」",
                )
            )

    for pattern in BUY_SELL_PATTERNS:
        if pattern.search(text):
            issues.append(
                ValidationIssue(
                    "forbidden_advice",
                    "報告不應包含明確買賣建議用語",
                )
            )
            break

    return ValidationResult(passed=not issues, issues=issues)
