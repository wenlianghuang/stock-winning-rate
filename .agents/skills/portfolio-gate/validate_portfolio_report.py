"""Validate an agy portfolio narrative against the portfolio facts.

Three layers, classified by issue-code prefix so the gate can report where a
round failed:
- format    : structure / required sections / forbidden phrases
- facts     : contradicts the deterministic portfolio — prefix ``portfolio_fact_``
- reasoning : quality gaps — prefix ``portfolio_reasoning_``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_BODY_CHARS = 200

FORBIDDEN_PHRASES = ("保證", "穩賺", "一定漲", "一定賺", "目標價", "工作摘要")

# 與風險屬性衝突的字眼：保守型不該被描述為積極/高風險（正面推薦語境）。
CONSERVATIVE_CONFLICT = ("積極", "高風險", "追求高報酬", "高波動")
AGGRESSIVE_CONFLICT = ("保守", "低風險", "波動極小")

# 主題模式不可把集中曝險說成保本/全市場分散
THEME_FALSE_SAFETY = ("保本", "零風險", "全市場分散", "最分散的組合")

DISCIPLINE_DROP_KEYWORDS = ("下跌", "回檔", "虧損", "套牢", "崩", "退潮")
DISCIPLINE_BATCH_KEYWORDS = ("分批", "定期定額", "不要一次", "逐步")
DISCIPLINE_REVIEW_KEYWORDS = ("檢視", "再平衡", "調整", "定期")
DIVERSIFICATION_KEYWORDS = ("分散", "ETF", "一籃子", "不同產業", "不集中")
THEME_CONCENTRATION_KEYWORDS = ("主題", "集中", "袖口", "曝險", "族群")

INCLUSION_VERBS = ("納入", "建議買", "配置", "買進", "持有", "推薦", "加入", "選入")
NEGATION_HINTS = ("不", "避免", "排除", "未", "沒有")


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


def _section_present(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _is_theme(facts) -> bool:
    return getattr(facts, "mode", "beginner") == "theme"


def _validate_format(text: str, facts) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if len(text) < MIN_BODY_CHARS:
        issues.append(
            ValidationIssue(
                "too_short",
                f"報告正文過短（{len(text)} 字，至少需要 {MIN_BODY_CHARS} 字）",
            )
        )

    if facts.profile_label not in text:
        label_kind = "組合名稱" if _is_theme(facts) else "風險屬性"
        issues.append(
            ValidationIssue(
                "missing_profile_label",
                f"報告未提及{label_kind}「{facts.profile_label}」",
            )
        )

    if _is_theme(facts):
        for label in getattr(facts, "theme_labels", []) or []:
            if label and label not in text:
                issues.append(
                    ValidationIssue(
                        "missing_theme_label",
                        f"報告未提及主題「{label}」",
                    )
                )

    for h in facts.holdings:
        if h.name not in text and h.stock_id not in text:
            issues.append(
                ValidationIssue(
                    f"missing_holding_{h.stock_id}",
                    f"報告未提及持股 {h.name}（{h.stock_id}）",
                )
            )

    if _is_theme(facts):
        required = (
            ("missing_fit", "「這個組合適合誰」段落", ("適合",)),
            ("missing_risk", "「風險/波動」段落", ("風險", "波動")),
            (
                "missing_discipline",
                "「部位紀律」段落",
                ("紀律", "分批", "檢視", "下跌", "退潮", "再平衡"),
            ),
            ("missing_disclaimer", "免責聲明", ("免責", "投資有風險", "非投資建議")),
        )
    else:
        required = (
            ("missing_fit", "「這個組合適合誰」段落", ("適合",)),
            ("missing_risk", "「風險/波動」段落", ("風險", "波動")),
            (
                "missing_discipline",
                "「新手紀律」段落",
                ("紀律", "分批", "再平衡", "檢視", "下跌"),
            ),
            ("missing_disclaimer", "免責聲明", ("免責", "投資有風險", "非投資建議")),
        )
    for code, label, keywords in required:
        if not _section_present(text, keywords):
            issues.append(ValidationIssue(code, f"缺少必要內容：{label}"))

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            issues.append(
                ValidationIssue("forbidden_phrase", f"報告不應包含「{phrase}」")
            )

    return issues


def _validate_facts(text: str, facts) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if _is_theme(facts):
        for word in THEME_FALSE_SAFETY:
            if word in text:
                issues.append(
                    ValidationIssue(
                        "portfolio_fact_theme_safety_mismatch",
                        f"主題組合不可描述為「{word}」，請強調主題集中／袖口風險",
                    )
                )
                break
    elif facts.profile == "conservative":
        for word in CONSERVATIVE_CONFLICT:
            if word in text:
                issues.append(
                    ValidationIssue(
                        "portfolio_fact_risk_mismatch",
                        f"保守型組合不應描述為「{word}」，請對齊低風險定位",
                    )
                )
                break
    elif facts.profile == "aggressive":
        for word in AGGRESSIVE_CONFLICT:
            if word in text:
                issues.append(
                    ValidationIssue(
                        "portfolio_fact_risk_mismatch",
                        f"積極型組合不應描述為「{word}」，請對齊中高風險定位",
                    )
                )
                break

    holding_ids = {h.stock_id for h in facts.holdings}
    for excluded in facts.excluded:
        name = str(excluded.get("name", "")).strip()
        stock_id = str(excluded.get("stock_id", "")).strip()
        if not name or stock_id in holding_ids:
            continue
        for line in text.splitlines():
            if name not in line:
                continue
            if _contains_any(line, INCLUSION_VERBS) and not _contains_any(
                line, NEGATION_HINTS
            ):
                issues.append(
                    ValidationIssue(
                        "portfolio_fact_hallucinated_holding",
                        f"{name}（{stock_id}）不在系統選出的持股內，不可寫成建議納入/買進",
                    )
                )
                break

    return issues


def _slice_discipline(text: str) -> str:
    """Best-effort slice of the discipline section for keyword coverage."""
    match = re.search(r"(紀律[\s\S]*)", text)
    return match.group(1) if match else text


def _validate_reasoning(text: str, facts) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    discipline = _slice_discipline(text)
    covered = sum(
        1
        for group in (
            DISCIPLINE_DROP_KEYWORDS,
            DISCIPLINE_BATCH_KEYWORDS,
            DISCIPLINE_REVIEW_KEYWORDS,
        )
        if _contains_any(discipline, group)
    )
    if covered < 2:
        issues.append(
            ValidationIssue(
                "portfolio_reasoning_discipline_incomplete",
                (
                    "部位紀律須至少涵蓋兩項：下跌/退潮應對、分批進場、定期檢視"
                    if _is_theme(facts)
                    else "新手紀律須至少涵蓋兩項：下跌時如何應對、分批進場、定期檢視/再平衡"
                ),
            )
        )

    if _is_theme(facts):
        if not _contains_any(text, THEME_CONCENTRATION_KEYWORDS):
            issues.append(
                ValidationIssue(
                    "portfolio_reasoning_theme_concentration_unmentioned",
                    "主題組合須說明主題集中／袖口曝險（不可省略集中風險）",
                )
            )
    elif facts.etf_weight_pct > 0 and not _contains_any(text, DIVERSIFICATION_KEYWORDS):
        issues.append(
            ValidationIssue(
                "portfolio_reasoning_diversification_unmentioned",
                "組合含 ETF 核心，須向新手說明分散/一籃子的概念",
            )
        )

    if "適合" in text:
        fit_region = text[text.find("適合") : text.find("適合") + 200]
        fit_keys = (
            ("主題", "袖口", "集中", "波動", "風險", "部位")
            if _is_theme(facts)
            else ("波動", "分散", "穩", "長期", "風險", "領息", "成長")
        )
        if not _contains_any(fit_region, fit_keys):
            issues.append(
                ValidationIssue(
                    "portfolio_reasoning_fit_unjustified",
                    (
                        "「適合誰」須說明主題袖口用途或集中風險特性"
                        if _is_theme(facts)
                        else "「適合誰」須用波動/分散/穩健/長期等特性說明為何符合此風險屬性"
                    ),
                )
            )

    return issues


def validate_portfolio_report(body: str, facts) -> ValidationResult:
    text = body.strip()
    issues: list[ValidationIssue] = []
    issues.extend(_validate_format(text, facts))
    issues.extend(_validate_facts(text, facts))
    issues.extend(_validate_reasoning(text, facts))
    return ValidationResult(passed=not issues, issues=issues)
