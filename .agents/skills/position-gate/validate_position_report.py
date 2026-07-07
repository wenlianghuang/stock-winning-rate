"""Validate position-aware agy report body against holdings and required sections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from holdings import HoldingRecord

MIN_BODY_CHARS = 120
MIN_BULLETS = 2

SECTION_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("position", "部位現況", ("部位", "均價", "持股")),
    ("market", "市場面摘要", ("市場", "籌碼", "趨勢")),
    ("cross", "部位與市場交叉對照", ("交叉對照", "部位與市場")),
    ("actions", "操作情境推演", ("情境", "操作", "觀望", "減碼", "加碼", "停損")),
    ("risk", "風險與紀律提醒", ("風險", "紀律")),
    ("disclaimer", "免責聲明", ("免責",)),
)

TEXT_SECTIONS = ("cross", "actions", "risk")
ACTION_KEYWORDS = ("觀望", "減碼", "加碼", "停損", "獲利", "了結", "持有")

FORBIDDEN_PHRASES = (
    "工作摘要",
    "本次工作摘要",
    "跨股票對照",
    "一定會漲",
    "一定會跌",
    "保證獲利",
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


# 市場面段落關鍵字（供事實方向檢查鎖定；勿含「部位現況」以免誤鎖前段）
_MARKET_SECTION_KEYWORDS = ("市場面摘要", "市場面")
# 操作情境段落關鍵字
_ACTION_SECTION_KEYWORDS = ("操作情境", "操作")
# 虧損超過此百分比時，操作情境須提及具體防禦手段
LOSS_RISK_THRESHOLD_PCT = -8.0
# 只認具體防禦動作，不含泛稱「風險」（第五章標題本就含「風險」二字）
_RISK_CONTROL_KEYWORDS = ("停損", "減碼", "出場", "調節", "停利", "獲利了結")


def _fact_issues(body: str, facts) -> list[ValidationIssue]:
    if facts is None:
        return []
    from fact_checks import run_fact_checks

    return [
        ValidationIssue(code, message)
        for code, message in run_fact_checks(
            body, facts, today_keywords=_MARKET_SECTION_KEYWORDS
        )
    ]


def _position_decision_issues(
    body: str, row: dict[str, str], holding: HoldingRecord
) -> list[ValidationIssue]:
    """部位專屬決策一致性：明顯虧損時操作情境須含具體防禦手段。"""
    from fact_checks import _slice_after_keywords
    from position_tables import compute_unrealized_pnl_pct, parse_close_price

    close_price = parse_close_price(row)
    if close_price is None or holding.avg_cost <= 0:
        return []
    pnl_pct = compute_unrealized_pnl_pct(holding.avg_cost, close_price)
    if pnl_pct > LOSS_RISK_THRESHOLD_PCT:
        return []

    action_region = _slice_after_keywords(body, _ACTION_SECTION_KEYWORDS)
    region = action_region if action_region.strip() else body
    if any(keyword in region for keyword in _RISK_CONTROL_KEYWORDS):
        return []
    return [
        ValidationIssue(
            "position_loss_no_risk_control",
            f"未實現損益約 {pnl_pct:.1f}%（虧損逾 {abs(LOSS_RISK_THRESHOLD_PCT):.0f}%），"
            "操作情境須提出停損/減碼/出場等具體防禦手段",
        )
    ]


def validate_position_report(
    body: str,
    row: dict[str, str],
    holding: HoldingRecord,
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

    cost_token = str(holding.avg_cost).rstrip("0").rstrip(".")
    if cost_token not in text and "均價" not in text:
        issues.append(
            ValidationIssue(
                "missing_avg_cost",
                f"報告應提及持股均價（{holding.avg_cost}）或「均價」",
            )
        )

    for code, label, keywords in SECTION_CHECKS:
        if code == "market" and not has_news:
            if not (
                _section_present(text, keywords)
                or "未取得" in text
                or "缺少新聞" in text
            ):
                issues.append(
                    ValidationIssue(
                        "missing_market_without_news",
                        "未取得新聞時，市場面摘要應註明缺少新聞或僅依 CSV 分析",
                    )
                )
            continue
        if code == "actions":
            if not _section_present(text, keywords):
                issues.append(
                    ValidationIssue(
                        f"missing_{code}",
                        f"缺少必要內容：{label}",
                    )
                )
            elif not any(keyword in text for keyword in ACTION_KEYWORDS):
                issues.append(
                    ValidationIssue(
                        "missing_action_direction",
                        "操作情境須提及觀望/減碼/加碼/停損/獲利了結/持有等方向",
                    )
                )
            elif "觸發" not in text and "條件" not in text:
                issues.append(
                    ValidationIssue(
                        "missing_trigger_conditions",
                        "操作情境須含觸發條件或可觀察訊號",
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
        elif _bullet_count(section_text) < MIN_BULLETS and code in ("cross", "risk"):
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

    issues.extend(_fact_issues(text, facts))
    issues.extend(_position_decision_issues(text, row, holding))

    return ValidationResult(passed=not issues, issues=issues)
