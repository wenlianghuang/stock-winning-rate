"""Validate market-weekly agy body against MarketWeekFacts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


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


MIN_BODY_CHARS = 400
MIN_NEWS_TITLES_CITED = 2

REQUIRED_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("market", "本週大盤", ("大盤", "加權")),
    ("leaders", "權值結構", ("權值", "帶動", "拖累")),
    ("sectors", "類股強弱", ("類股", "強勢", "弱勢")),
    ("cross", "交叉解讀", ("交叉", "解讀", "資金", "對帳")),
    ("scenarios", "下週情境", ("下週", "情境", "觸發")),
    ("watch", "觀察重點", ("觀察",)),
    ("disclaimer", "免責", ("免責",)),
)

FORBIDDEN_PHRASES = (
    "工作摘要",
    "穩賺",
    "一定漲",
    "保證獲利",
    "目標價",
)

RELATION_KEYWORDS = ("一致", "背離", "落後")


def _section_present(body: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in body for kw in keywords)


def _extract_cross_section(body: str) -> str:
    """Best-effort slice from 交叉 heading until next major section."""
    lines = body.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if any(kw in line for kw in ("交叉", "對帳")) and (
            line.strip().startswith("#")
            or re.match(r"^##?\s*[一二三四五六七]", line)
            or "四" in line[:8]
        ):
            start = i
            break
    if start is None:
        for i, line in enumerate(lines):
            if "交叉" in line or "對帳" in line:
                start = i
                break
    if start is None:
        return body
    chunks: list[str] = []
    for line in lines[start + 1 :]:
        if re.match(r"^#{1,3}\s", line) or re.match(r"^##?\s*[一二三四五六七]", line):
            break
        chunks.append(line)
    return "\n".join(chunks).strip() or body


def _fact_number_tokens(facts: dict[str, Any]) -> list[str]:
    tokens: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, (int, float)):
            raw = f"{float(value):.2f}".rstrip("0").rstrip(".")
            tokens.append(raw)
            tokens.append(f"{raw}%")

    market = facts.get("market") or {}
    _add(market.get("week_return_pct"))
    for bucket in ("top", "bottom"):
        for item in (facts.get("leaders") or {}).get(bucket) or []:
            _add(item.get("week_return_pct"))
            _add(item.get("excess_vs_taiex_pct"))
    for bucket in ("strong", "weak"):
        for item in (facts.get("sectors") or {}).get(bucket) or []:
            _add(item.get("week_return_pct"))
            _add(item.get("excess_vs_taiex_pct"))
    us = facts.get("us") or {}
    indices = us.get("indices") or {}
    for key in ("IXIC", "SOX"):
        block = indices.get(key)
        if isinstance(block, dict):
            _add(block.get("week_return_pct"))
    gaps = us.get("gaps") or {}
    _add(gaps.get("ixic_minus_taiex_pct"))
    _add(gaps.get("sox_minus_tw_semi_pct"))
    _add(us.get("tw_semi_week_return_pct"))
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _us_number_tokens(facts: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    us = facts.get("us") or {}
    indices = us.get("indices") or {}
    for key in ("IXIC", "SOX"):
        block = indices.get(key)
        if not isinstance(block, dict):
            continue
        value = block.get("week_return_pct")
        if isinstance(value, (int, float)):
            raw = f"{float(value):.2f}".rstrip("0").rstrip(".")
            tokens.append(raw)
            tokens.append(f"{raw}%")
    return tokens


def _extract_scenarios_section(body: str) -> str:
    lines = body.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if any(kw in line for kw in ("下週", "情境")) and (
            line.strip().startswith("#")
            or re.match(r"^##?\s*[一二三四五六七]", line)
            or "五" in line[:8]
        ):
            start = i
            break
    if start is None:
        for i, line in enumerate(lines):
            if "下週" in line or "情境" in line:
                start = i
                break
    if start is None:
        return body
    chunks: list[str] = []
    for line in lines[start + 1 :]:
        if re.match(r"^#{1,3}\s", line) or re.match(r"^##?\s*[一二三四五六七]", line):
            break
        chunks.append(line)
    return "\n".join(chunks).strip() or body


def _has_nasdaq_mention(text: str) -> bool:
    return any(kw in text for kw in ("那斯達克", "那指", "IXIC", "^IXIC"))


def _has_sox_mention(text: str) -> bool:
    return any(kw in text for kw in ("費半", "費城半導體", "SOX", "^SOX"))


def _validate_us_cross(
    text: str,
    facts: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    if "us" not in facts:
        return
    us = facts.get("us") or {}
    available = bool(us.get("available"))
    cross = _extract_cross_section(text)
    scenarios = _extract_scenarios_section(text)

    if not available:
        if "美股指數資料不足" not in text and "美股資料不足" not in text:
            issues.append(
                ValidationIssue(
                    "us_unavailable_unacknowledged",
                    "us.available 為 false 時，請寫明「本週美股指數資料不足」",
                )
            )
        return

    if not _has_nasdaq_mention(text):
        issues.append(
            ValidationIssue(
                "missing_nasdaq_mention",
                "us.available 時正文須提及那斯達克／那指（或 IXIC）",
            )
        )
    if not _has_sox_mention(text):
        issues.append(
            ValidationIssue(
                "missing_sox_mention",
                "us.available 時正文須提及費半／費城半導體（或 SOX）",
            )
        )

    us_relation = ("一致", "背離", "落後", "落離")
    if not any(kw in cross or kw in text for kw in us_relation):
        issues.append(
            ValidationIssue(
                "us_cross_missing_relation",
                "台美交叉須使用一致／背離／落後之一（對齊 facts.alignment）",
            )
        )

    us_tokens = _us_number_tokens(facts)
    if us_tokens and not any(tok in cross or tok in text for tok in us_tokens):
        issues.append(
            ValidationIssue(
                "us_cross_missing_number",
                "台美交叉須引用至少一個那指或費半週報酬數字",
            )
        )

    if not (_has_nasdaq_mention(scenarios) or _has_sox_mention(scenarios)):
        issues.append(
            ValidationIssue(
                "scenario_missing_us_trigger",
                "下週情境至少一個觸發條件須綁費半或那指",
            )
        )


def validate_market_week_report(
    body: str,
    facts: dict[str, Any],
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    text = (body or "").strip()
    if len(text) < MIN_BODY_CHARS:
        issues.append(
            ValidationIssue("too_short", f"正文過短（<{MIN_BODY_CHARS} 字），請寫完整七段")
        )

    for code, label, keywords in REQUIRED_SECTIONS:
        if not _section_present(text, keywords):
            issues.append(
                ValidationIssue(
                    f"missing_{code}",
                    f"缺少「{label}」相關內容（關鍵字：{'/'.join(keywords)}）",
                )
            )

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            issues.append(
                ValidationIssue("forbidden_phrase", f"請移除禁用字眼：{phrase}")
            )

    if "觸發" not in text and "一旦" not in text and "若" not in text:
        issues.append(
            ValidationIssue(
                "missing_trigger",
                "下週情境須含觸發條件（若/一旦/觸發）",
            )
        )

    allowed_sectors = {
        str(item.get("name") or "")
        for bucket in ("strong", "weak", "all")
        for item in (facts.get("sectors") or {}).get(bucket) or []
    }
    allowed_sectors.discard("")
    if allowed_sectors:
        for match in re.finditer(r"([\u4e00-\u9fff]{2,20}類指數)", text):
            name = match.group(1)
            if name in allowed_sectors:
                continue
            if any(name.endswith(allowed) for allowed in allowed_sectors):
                continue
            issues.append(
                ValidationIssue(
                    "hallucinated_sector",
                    f"類股「{name}」不在 facts 強弱榜／清單中，請改引用提供名稱",
                )
            )

    allowed_ids = {
        str(item.get("stock_id") or "")
        for bucket in ("top", "bottom")
        for item in (facts.get("leaders") or {}).get(bucket) or []
    }
    allowed_ids.discard("")
    if allowed_ids:
        for match in re.finditer(r"[（(](\d{4})[）)]", text):
            sid = match.group(1)
            if sid not in allowed_ids:
                issues.append(
                    ValidationIssue(
                        "hallucinated_leader",
                        f"權值代號 {sid} 不在 facts 權值榜，請改引用提供標的",
                    )
                )

    market = facts.get("market") or {}
    week_ret = market.get("week_return_pct")
    if isinstance(week_ret, (int, float)):
        if week_ret > 0.5 and re.search(r"大盤[^。\n]{0,12}(大跌|重挫|崩)", text):
            issues.append(
                ValidationIssue(
                    "market_direction_mismatch",
                    f"facts 大盤週報酬為 +{week_ret}%，請勿寫成大盤大跌／重挫",
                )
            )
        if week_ret < -0.5 and re.search(r"大盤[^。\n]{0,12}(大漲|狂飆|暴漲)", text):
            issues.append(
                ValidationIssue(
                    "market_direction_mismatch",
                    f"facts 大盤週報酬為 {week_ret}%，請勿寫成大盤大漲",
                )
            )

    anchors = [str(a) for a in (facts.get("anchors") or []) if str(a).strip()]
    cited = 0
    for anchor in anchors:
        nums = re.findall(r"-?\d+\.?\d*%?", anchor)
        if any(n and n in text for n in nums):
            cited += 1
            continue
        for token in re.findall(r"[\u4e00-\u9fff]{2,}", anchor):
            if token in text:
                cited += 1
                break
    if anchors and cited < 2:
        issues.append(
            ValidationIssue(
                "missing_anchor_citation",
                "請在正文明確引用至少 2 條 facts anchors（數字或類股／權值名稱）",
            )
        )

    news_titles = [
        str(t).strip()
        for t in (facts.get("news_titles") or [])
        if str(t).strip()
    ]
    cross = _extract_cross_section(text)
    if news_titles:
        title_hits = sum(1 for title in news_titles if title in text)
        if title_hits < min(MIN_NEWS_TITLES_CITED, len(news_titles)):
            need = min(MIN_NEWS_TITLES_CITED, len(news_titles))
            issues.append(
                ValidationIssue(
                    "cross_missing_news_titles",
                    f"交叉解讀須原文引用至少 {need} 則提供的新聞標題（目前命中 {title_hits}）",
                )
            )
        if not any(kw in cross or kw in text for kw in RELATION_KEYWORDS):
            issues.append(
                ValidationIssue(
                    "cross_missing_relation",
                    "交叉解讀須明確寫出一致／背離／資訊落後之一",
                )
            )
        number_tokens = _fact_number_tokens(facts)
        if number_tokens and not any(tok in cross or tok in text for tok in number_tokens):
            issues.append(
                ValidationIssue(
                    "cross_missing_fact_number",
                    "交叉解讀須引用至少一個 facts 數字（大盤／權值／類股週報酬或超額）",
                )
            )
        # Prefer bullet list when news present
        if not re.search(r"^\s*[-*]\s+", cross, re.MULTILINE):
            issues.append(
                ValidationIssue(
                    "cross_missing_bullets",
                    "交叉解讀須使用 2～4 點 `-` 條列（有對帳新聞時）",
                )
            )
    else:
        # No news: allow structural cross; optional phrase
        pass

    _validate_us_cross(text, facts, issues)

    return ValidationResult(passed=not issues, issues=issues)
