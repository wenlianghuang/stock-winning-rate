"""Validate single-stock agy report body against CSV and required sections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


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


MIN_BODY_CHARS = 120
MIN_BULLETS = 2

SECTION_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("today_chip", "當日籌碼解讀", ("當日籌碼", "籌碼解讀")),
    ("trend", "近 N 日籌碼趨勢", ("趨勢", "近")),
    ("news", "近期新聞與事件", ("新聞", "事件")),
    ("cross", "籌碼與新聞交叉對照", ("交叉", "對照", "背離", "一致")),
    ("scenarios", "短中線情境推演", ("情境推演", "情境", "交易日")),
    ("watch", "觀察重點", ("觀察",)),
    ("disclaimer", "免責聲明", ("免責",)),
)

TEXT_SECTIONS = ("cross", "scenarios", "watch")

FORBIDDEN_PHRASES = (
    "工作摘要",
    "本次工作摘要",
    "跨股票對照",
)


def validate_facts(body: str, facts) -> list["ValidationIssue"]:
    """Fact-consistency layer (delegates to shared ``fact_checks``)."""
    from fact_checks import run_fact_checks

    return [
        ValidationIssue(code, message)
        for code, message in run_fact_checks(body, facts)
    ]


def _has_markdown_table(text: str) -> bool:
    return bool(re.search(r"^\|.+\|\s*$", text, re.MULTILINE))


def _bullet_count(text: str) -> int:
    bullets = re.findall(r"^\s*[-*]\s+", text, re.MULTILINE)
    numbered = re.findall(r"^\s*\d+[.)]\s+", text, re.MULTILINE)
    return len(bullets) + len(numbered)


def _section_present(body: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in body for keyword in keywords)


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


def validate_single_stock_report(
    body: str,
    row: dict[str, str],
    *,
    facts=None,
    has_news: bool,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    text = body.strip()

    if len(text) < MIN_BODY_CHARS:
        issues.append(
            ValidationIssue(
                "too_short",
                f"報告正文過短（{len(text)} 字，至少需要 {MIN_BODY_CHARS} 字）",
            )
        )

    stock_id = str(row.get("代碼", "")).strip()
    stock_name = str(row.get("名稱", "")).strip()
    if stock_id and stock_id not in text:
        issues.append(
            ValidationIssue("missing_stock_id", f"報告未提及股票代碼 {stock_id}")
        )
    if stock_name and stock_name not in text:
        issues.append(
            ValidationIssue("missing_stock_name", f"報告未提及股票名稱 {stock_name}")
        )

    for code, label, keywords in SECTION_CHECKS:
        if code == "news" and not has_news:
            if not (
                _section_present(text, keywords)
                or "未取得" in text
                or "缺少新聞" in text
            ):
                issues.append(
                    ValidationIssue(
                        "missing_news_note",
                        "未取得新聞時，報告應註明缺少新聞來源或僅依 CSV 分析",
                    )
                )
            continue
        if code == "trend":
            if not (
                _section_present(text, keywords)
                and (
                    "延續" in text
                    or "轉折" in text
                    or "背離" in text
                    or "累計" in text
                    or "區間" in text
                )
            ):
                issues.append(
                    ValidationIssue(
                        "missing_trend_analysis",
                        f"缺少必要內容：{label}（須描述延續/轉折/背離或區間趨勢）",
                    )
                )
            continue
        if not _section_present(text, keywords):
            issues.append(
                ValidationIssue(
                    f"missing_{code}",
                    f"缺少必要內容：{label}",
                )
            )

    if has_news and not _has_markdown_table(text):
        issues.append(
            ValidationIssue(
                "missing_news_table",
                "有提供新聞時，「近期新聞與事件」章節應使用 Markdown 表格",
            )
        )

    for code, label, keywords in SECTION_CHECKS:
        if code not in TEXT_SECTIONS:
            continue
        section_text = _slice_after_keywords(text, keywords)
        if not section_text.strip():
            continue
        if _has_markdown_table(section_text):
            issues.append(
                ValidationIssue(
                    f"{code}_should_be_text",
                    f"「{label}」應以文字條列撰寫，請勿使用表格",
                )
            )
        elif _bullet_count(section_text) < MIN_BULLETS and code in ("cross", "watch"):
            issues.append(
                ValidationIssue(
                    f"sparse_{code}",
                    f"「{label}」應有至少 {MIN_BULLETS} 點條列或編號項目",
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

    issues.extend(validate_facts(text, facts))

    return ValidationResult(passed=not issues, issues=issues)
