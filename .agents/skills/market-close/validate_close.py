"""Validate the close-report body against assembled facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MIN_BODY_CHARS = 280
MAX_BODY_CHARS = 3500

FORBIDDEN_PHRASES = (
    "明日開盤偏誤",
    "目標價",
    "穩賺",
    "一定漲",
    "保證獲利",
)

SCOPE_WORDS = ("融資", "融券", "當沖", "主力")
EXCLUDED_IDS = ("0050", "006208")


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


def _number_token(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    raw = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return raw or None


def validate_close_report(body: str, facts: dict[str, Any]) -> ValidationResult:
    issues: list[ValidationIssue] = []
    text = body or ""
    stripped = text.strip()
    titles = [str(title) for title in (facts.get("news_titles") or []) if str(title).strip()]

    if len(stripped) < MIN_BODY_CHARS:
        issues.append(ValidationIssue("too_short", f"正文過短（<{MIN_BODY_CHARS} 字）"))
    if len(stripped) > MAX_BODY_CHARS:
        issues.append(ValidationIssue("too_long", f"正文過長（>{MAX_BODY_CHARS} 字）"))

    for label in ("今日收盤", "權值", "新聞", "免責"):
        if label not in text:
            issues.append(ValidationIssue(f"missing_{label}", f"缺少「{label}」"))

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            issues.append(ValidationIssue("forbidden_phrase", f"禁止出現「{phrase}」"))

    for word in SCOPE_WORDS:
        if word in text and not any(word in title for title in titles):
            issues.append(ValidationIssue("out_of_scope", f"這份收盤日報不寫「{word}」"))

    for token in EXCLUDED_IDS:
        if token in text and not any(token in title for title in titles):
            issues.append(ValidationIssue("excluded_id", f"不要寫入 ETF {token}"))

    market = facts.get("market") or {}
    if market.get("available"):
        token = _number_token(market.get("day_return_pct"))
        if token and token not in text:
            issues.append(ValidationIssue("missing_return", f"須寫出大盤日報酬 {token}"))
    elif "收盤價尚未公布" not in text and "尚未公布" not in text:
        issues.append(ValidationIssue("missing_market", "大盤收盤價尚未公布時，正文要寫尚未公布"))

    inst = facts.get("institutional") or {}
    if inst.get("available"):
        label = str(inst.get("consensus_label") or "")
        if label and label not in text:
            issues.append(ValidationIssue("missing_consensus", f"須寫出全市場法人結論「{label}」"))
    elif "尚未公布" not in text:
        issues.append(ValidationIssue("missing_inst", "全市場法人尚未公布時，正文要寫尚未公布"))

    unpublished = False
    for item in facts.get("leaders") or []:
        name = str(item.get("name") or "")
        if name and name not in text:
            issues.append(ValidationIssue("missing_leader", f"須點名 {name}"))
        if item.get("institutional_status") != "published":
            unpublished = True
            continue
        for key in ("foreign_net_lots", "trust_net_lots", "dealer_net_lots"):
            lots = item.get(key)
            if isinstance(lots, int) and str(lots) not in text:
                issues.append(
                    ValidationIssue(
                        "missing_lots",
                        f"{name} 的 {key} {lots} 須出現在正文",
                    )
                )
    if unpublished and "尚未公布" not in text:
        issues.append(ValidationIssue("missing_unpublished", "有權值法人尚未公布時，正文要寫尚未公布"))

    if titles:
        if not any(title in text for title in titles):
            issues.append(ValidationIssue("missing_news", "須原文引用至少一則新聞標題"))
    elif "沒有對得上的收盤新聞" not in text:
        issues.append(ValidationIssue("missing_empty_news", "沒有新聞時須寫「沒有對得上的收盤新聞」"))

    return ValidationResult(passed=not issues, issues=issues)
