"""Validate market-daily agy body against MarketDayFacts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MIN_BODY_CHARS = 350
MAX_BODY_CHARS = 4500

REQUIRED_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("structure", "今日結構", ("今日", "結構", "大盤")),
    ("external", "外盤改寫", ("外盤", "美股", "那指", "費半")),
    ("bias", "明日開盤偏誤", ("偏誤", "開盤", "情境")),
    ("dashboard", "開盤儀表板", ("儀表", "觀察", "檢核")),
    ("disclaimer", "免責", ("免責",)),
)

FORBIDDEN_PHRASES = (
    "工作摘要",
    "穩賺",
    "一定漲",
    "保證獲利",
    "目標價",
)

FALSIFY_KEYWORDS = ("否決",)
BIAS_KEYWORDS = ("偏多", "偏空", "中性")
RANK_KEYWORDS = ("基準", "尾部", "最可能")


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


def _section_present(body: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in body for kw in keywords)


def _fact_number_tokens(facts: dict[str, Any]) -> list[str]:
    tokens: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, (int, float)):
            raw = f"{float(value):.2f}".rstrip("0").rstrip(".")
            tokens.append(raw)
            tokens.append(f"{raw}%")

    market = facts.get("market") or {}
    _add(market.get("day_return_pct"))
    _add(market.get("close"))
    volume = facts.get("volume") or {}
    _add(volume.get("vs_avg5_ratio"))
    tech = facts.get("technical") or {}
    _add(tech.get("ma5"))
    _add(tech.get("ma20"))
    tsmc = facts.get("tsmc") or {}
    _add(tsmc.get("day_return_pct"))
    _add(tsmc.get("close"))
    us = facts.get("us") or {}
    indices = us.get("indices") or {}
    for key in ("IXIC", "SOX"):
        block = indices.get(key)
        if isinstance(block, dict):
            _add(block.get("day_return_pct"))
    inst = facts.get("institutional") or {}
    for key in ("foreign_net", "trust_net", "dealer_net", "total_net"):
        value = inst.get(key)
        if isinstance(value, (int, float)) and abs(value) >= 1000:
            # cite in 億股近似過嚴；改要求出現縮寫數字之一即可於下方另檢
            raw = str(int(value))
            if len(raw) > 4:
                tokens.append(raw[:-4])  # rough 萬 shares prefix
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def validate_market_day_report(body: str, facts: dict[str, Any]) -> ValidationResult:
    issues: list[ValidationIssue] = []
    text = body or ""

    if len(text.strip()) < MIN_BODY_CHARS:
        issues.append(
            ValidationIssue("too_short", f"正文過短（<{MIN_BODY_CHARS} 字），請補足開盤偏誤與儀表板")
        )
    if len(text.strip()) > MAX_BODY_CHARS:
        issues.append(
            ValidationIssue("too_long", f"正文過長（>{MAX_BODY_CHARS} 字），請精簡成開盤前 brief")
        )

    for code, label, keywords in REQUIRED_SECTIONS:
        if not _section_present(text, keywords):
            issues.append(ValidationIssue(f"missing_{code}", f"缺少「{label}」相關內容"))

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            issues.append(ValidationIssue("forbidden_phrase", f"禁止出現「{phrase}」"))

    if not any(kw in text for kw in BIAS_KEYWORDS):
        issues.append(ValidationIssue("missing_bias", "須標明偏多／偏空／中性偏誤方向"))

    if not any(kw in text for kw in RANK_KEYWORDS):
        issues.append(ValidationIssue("missing_scenarios", "須含基準／尾部（或最可能）情境"))

    if not any(kw in text for kw in FALSIFY_KEYWORDS):
        issues.append(ValidationIssue("missing_falsify", "情境須含「否決」條件"))

    bias_hint = str(facts.get("bias_hint") or "neutral")
    if bias_hint == "neutral" and ("強烈看多" in text or "強烈看空" in text):
        issues.append(
            ValidationIssue("bias_contradiction", "bias_hint 為 neutral，不可寫強烈看多／看空")
        )
    if bias_hint == "bullish" and "偏空" in text and "偏多" not in text:
        issues.append(
            ValidationIssue("bias_contradiction", "bias_hint 為 bullish，正文應出現偏多方向")
        )
    if bias_hint == "bearish" and "偏多" in text and "偏空" not in text:
        issues.append(
            ValidationIssue("bias_contradiction", "bias_hint 為 bearish，正文應出現偏空方向")
        )

    market = facts.get("market") or {}
    day_ret = market.get("day_return_pct")
    if isinstance(day_ret, (int, float)):
        raw = f"{float(day_ret):.2f}".rstrip("0").rstrip(".")
        if raw not in text and f"{raw}%" not in text:
            issues.append(
                ValidationIssue("missing_market_return", f"須引用大盤日報酬 {raw}%")
            )

    us = facts.get("us") or {}
    if us.get("available"):
        if not any(kw in text for kw in ("那斯達克", "那指", "IXIC", "費半", "SOX")):
            issues.append(ValidationIssue("missing_us", "us.available 時須提到那指或費半"))
        align = us.get("alignment") or {}
        if any(v in {"一致", "背離"} for v in align.values()):
            if "一致" not in text and "背離" not in text:
                issues.append(
                    ValidationIssue("missing_alignment", "須使用「一致」或「背離」做台美對帳")
                )

    # Soft numeric citation: at least 2 fact tokens appear
    tokens = _fact_number_tokens(facts)
    cited = sum(1 for tok in tokens[:12] if tok in text)
    if tokens and cited < 2:
        issues.append(
            ValidationIssue("sparse_numbers", "請至少引用 2 個 facts 中的關鍵數字")
        )

    # Dashboard-ish bullets
    bullet_count = len(re.findall(r"^\s*[-*]\s+", text, flags=re.M))
    if bullet_count < 4:
        issues.append(
            ValidationIssue("sparse_dashboard", "開盤儀表板／條列觀察至少應有 4 項")
        )

    return ValidationResult(passed=not issues, issues=issues)
