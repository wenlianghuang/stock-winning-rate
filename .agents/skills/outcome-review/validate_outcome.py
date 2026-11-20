"""Validate the outcome postmortem against the frozen ledger."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

HEADINGS = ("## 有方向的判斷", "## 還不能計分", "## 該動哪一層")
DENIAL = "不是新的看多或看空"
SECTION_DIRECTION = HEADINGS[0]
SECTION_UNSCORED = HEADINGS[1]
SECTION_LAYER = HEADINGS[2]

FORBIDDEN_PHRASES = ("目標價", "穩賺", "一定漲", "建議買", "建議賣", "應該買", "應該賣")
OTHER_HORIZONS = ("1日超額", "3日超額", "10日超額", "一日超額", "三日超額", "十日超額")
UNSCORED_BANNED = ("命中", "相反", "看對", "看錯", "正確", "看多", "看空", "偏多", "偏空")
YEAR_OR_LATER = ("19", "20")
STOCK_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")

MIN_BODY_CHARS = 40
MAX_BODY_CHARS = 3500

_COUNT_LABELS = (
    ("abstain", "中性"),
    ("flat", "未過門檻"),
    ("pending", "尚未成熟"),
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


def _sections(body: str) -> tuple[dict[str, str], list[ValidationIssue]]:
    matches = list(re.finditer(r"^## .+$", body, re.M))
    titles = [match.group(0).strip() for match in matches]
    if titles != list(HEADINGS):
        return {}, [
            ValidationIssue(
                "headings",
                "三個標題必須依序是「有方向的判斷」「還不能計分」「該動哪一層」",
            )
        ]
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(0).strip()] = body[match.end() : end]
    return sections, []


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, abs_tol=0.011)


def _visible(text: str) -> str:
    """Drop the fixed denial so「看多」inside it is not a new call."""
    return text.replace(DENIAL, "")


def _allowed_percents(ledger: dict[str, Any]) -> list[float]:
    allowed = [float(ledger["epsilon_pct"])]
    accuracy = ledger.get("accuracy_pct")
    if isinstance(accuracy, (int, float)):
        allowed.append(float(accuracy))
    for row in ledger["rows"]:
        for key in ("excess_return_pct", "stock_return_pct", "market_return_pct"):
            value = row.get(key)
            if isinstance(value, (int, float)):
                allowed.append(float(value))
                allowed.append(abs(float(value)))
    return allowed


def _percent_allowed(value: float, allowed: list[float]) -> bool:
    return any(_close(value, item) or _close(abs(value), abs(item)) for item in allowed)


def _row_window(section: str, row: dict[str, Any]) -> str:
    lines = [
        line
        for line in section.splitlines()
        if row["stock_id"] in line and row["trade_date"] in line
    ]
    return "\n".join(lines)


def validate_outcome_report(body: str, ledger: dict[str, Any]) -> ValidationResult:
    issues: list[ValidationIssue] = []
    text = body or ""
    stripped = text.strip()
    if len(stripped) < MIN_BODY_CHARS:
        issues.append(ValidationIssue("too_short", f"正文過短（<{MIN_BODY_CHARS} 字）"))
    if len(stripped) > MAX_BODY_CHARS:
        issues.append(ValidationIssue("too_long", f"正文過長（>{MAX_BODY_CHARS} 字）"))

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            issues.append(ValidationIssue("forbidden_phrase", f"禁止出現「{phrase}」"))
    for token in OTHER_HORIZONS:
        if token in text:
            issues.append(ValidationIssue("other_horizon", f"不可用「{token}」代替 5 日"))

    known_ids = {str(row["stock_id"]) for row in ledger["rows"] if row.get("stock_id")}
    for token in STOCK_RE.findall(text):
        if token.startswith(YEAR_OR_LATER):
            continue
        if token not in known_ids:
            issues.append(ValidationIssue("unknown_stock", f"對帳表沒有股號 {token}"))

    allowed = _allowed_percents(ledger)
    for raw in PERCENT_RE.findall(text):
        value = float(raw)
        if not _percent_allowed(value, allowed):
            issues.append(ValidationIssue("unknown_percent", f"對帳表沒有這個百分比：{raw}%"))

    if "準確率" in text and ledger["accuracy_sentence"] not in text:
        issues.append(
            ValidationIssue(
                "accuracy_sentence",
                f"寫準確率必須照抄：{ledger['accuracy_sentence']}",
            )
        )

    calls = {str(row["call"]) for row in ledger["rows"]}
    hits = int(ledger["counts"]["hit"])
    visible = _visible(text)
    if "看多" in visible and "偏多" not in calls:
        issues.append(ValidationIssue("flipped_call", "這張表沒有偏多，不可寫成看多"))
    if "看空" in visible and "偏空" not in calls:
        issues.append(ValidationIssue("flipped_call", "這張表沒有偏空，不可寫成看空"))
    if "偏多" in text and "偏多" not in calls:
        issues.append(ValidationIssue("flipped_call", "這張表沒有偏多"))
    if "偏空" in text and "偏空" not in calls:
        issues.append(ValidationIssue("flipped_call", "這張表沒有偏空"))
    if hits == 0 and re.search(r"(?<!不)正確", text):
        issues.append(ValidationIssue("flipped_verdict", "沒有命中的列，不可寫成正確"))

    sections, heading_issues = _sections(text)
    issues.extend(heading_issues)
    if heading_issues:
        return ValidationResult(passed=False, issues=issues)

    direction = sections[SECTION_DIRECTION]
    unscored = sections[SECTION_UNSCORED]
    layer = sections[SECTION_LAYER]
    scored = int(ledger["scored"])
    if scored == 0:
        if "還沒有可以計分的判斷" not in direction:
            issues.append(
                ValidationIssue("no_scored", "可計分件數為 0 時，要寫「還沒有可以計分的判斷」")
            )
        for word in ("命中", "相反", "看對", "看錯", "偏多", "偏空", "看多", "看空"):
            if word in direction:
                issues.append(
                    ValidationIssue("no_scored", f"還沒有可計分的判斷，這一節不可寫「{word}」")
                )
    else:
        for row in ledger["rows"]:
            if row["verdict"] not in ("hit", "miss"):
                continue
            window = _row_window(direction, row)
            label = f"{row['trade_date']} {row['stock_id']}"
            if not window:
                issues.append(ValidationIssue("missing_row", f"有方向的判斷要寫出 {label}"))
                continue
            if row["call"] not in window or row["excess_text"] not in window:
                issues.append(
                    ValidationIssue(
                        "missing_row",
                        f"{label} 要寫出{row['call']}與超額 {row['excess_text']}",
                    )
                )
            plain = str(row.get("plain") or "")
            if plain and plain not in direction:
                issues.append(ValidationIssue("missing_plain", f"{label} 的人話要原句照抄"))
            checked = _visible(window)
            if row["verdict"] == "hit" and "命中" not in window:
                issues.append(ValidationIssue("missing_verdict", f"{label} 要寫命中"))
            if row["verdict"] == "miss" and "相反" not in window:
                issues.append(ValidationIssue("missing_verdict", f"{label} 要寫相反"))
            if row["verdict"] == "miss" and any(word in checked for word in ("命中", "正確", "看多")):
                issues.append(ValidationIssue("flipped_verdict", f"{label} 是相反，不可寫成命中或正確"))
            if row["verdict"] == "hit" and any(word in checked for word in ("相反", "看錯", "看反")):
                issues.append(ValidationIssue("flipped_verdict", f"{label} 是命中，不可寫成相反"))
            if row["call"] == "偏空" and any(word in checked for word in ("偏多", "看多")):
                issues.append(ValidationIssue("flipped_call", f"{label} 是偏空，不可寫成偏多"))
            if row["call"] == "偏多" and any(word in checked for word in ("偏空", "看空")):
                issues.append(ValidationIssue("flipped_call", f"{label} 是偏多，不可寫成偏空"))

    for word in UNSCORED_BANNED:
        if word in unscored:
            issues.append(ValidationIssue("unscored_as_score", f"還不能計分的這一節不可寫「{word}」"))
    for key, label in _COUNT_LABELS:
        count = int(ledger["counts"][key])
        if count <= 0:
            continue
        if label not in unscored or str(count) not in unscored:
            issues.append(
                ValidationIssue("missing_unscored", f"還不能計分要寫出{label} {count}")
            )
    verdict_for_label = {"abstain": "中性", "flat": "未過門檻", "pending": "尚未成熟"}
    for row in ledger["rows"]:
        label = verdict_for_label.get(str(row["verdict"]))
        stock_id = str(row.get("stock_id") or "")
        if not label or not stock_id or int(ledger["counts"][row["verdict"]]) <= 0:
            continue
        if stock_id not in unscored:
            issues.append(
                ValidationIssue("missing_unscored", f"還不能計分要寫出 {stock_id}（{label}）")
            )

    failure_counts = ledger["failure_counts"]
    active = any(int(failure_counts[key]) > 0 for key in ("judgment", "writing", "operational"))
    if active and "沒有要改的層" in layer:
        issues.append(ValidationIssue("layer_contradiction", "已有失敗件數，不可寫「沒有要改的層」"))
    if not active and "沒有要改的層" not in layer:
        issues.append(ValidationIssue("missing_layer", "三種失敗都是 0 時，要寫「沒有要改的層」"))
    if int(failure_counts["judgment"]) > 0 and "籌碼歸類" not in layer:
        issues.append(ValidationIssue("missing_layer", "判斷失敗要寫「籌碼歸類」"))
    if int(failure_counts["judgment"]) == 0 and "籌碼" in layer:
        issues.append(ValidationIssue("wrong_layer", "沒有判斷失敗，不要寫成籌碼問題"))
    if int(failure_counts["writing"]) > 0:
        if "寫作" not in layer:
            issues.append(ValidationIssue("missing_layer", "寫作失敗要寫「寫作」"))
        for code in ledger["issue_codes"]:
            if code not in layer:
                issues.append(ValidationIssue("missing_issue_code", f"要寫出 issue code {code}"))
    elif "寫作" in layer:
        issues.append(ValidationIssue("wrong_layer", "沒有寫作失敗，不要寫成寫作問題"))
    if int(failure_counts["operational"]) > 0 and "產報沒跑完" not in layer:
        issues.append(ValidationIssue("missing_layer", "作業失敗要寫「產報沒跑完」"))
    if int(failure_counts["operational"]) == 0 and "產報" in layer:
        issues.append(ValidationIssue("wrong_layer", "沒有作業失敗，不要寫成產報問題"))

    return ValidationResult(passed=not issues, issues=issues)
