"""Peer comparison letter.

The stance, reasons, and same-day figures stay on the rule output.
A model may only explain that finished conclusion, and only after the
reading passes the number and direction checks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

STANCE_ZH = {
    "weaker": "較弱",
    "stronger": "較強",
    "mixed": "分歧",
    "unknown": "無法判斷",
}

_READING_ATTEMPTS = 2
_REPORT_LIMIT = 8000
_POSITION_LIMIT = 4000
_READING_LIMIT = 2500

_NUMBER_RE = re.compile(r"(?<![\d.])[-+]?\d[\d,]*(?:\.\d+)?%?")
_ALLOWED_WORDING_RE = re.compile(
    r"(?:MA|RSI|ATR|ADX)\s*\d+|5日|10日|20日|14日|3日",
    re.IGNORECASE,
)
_FOREIGN_LOT_RE = re.compile(r"([+-][\d,]+)\s*張")
_OWN_POSITION_HEADING_RE = re.compile(r"^## 部位\s*$", re.MULTILINE)

_FOREIGN_BULL = ("外資買超", "外資大買", "外資轉買", "外資偏多", "外資站買方")
_FOREIGN_BEAR = ("外資賣超", "外資大賣", "外資轉賣", "外資偏空", "外資站賣方")


def compose_peer_email(
    state: dict[str, Any],
    *,
    reading: str | None = None,
) -> tuple[str, str]:
    """Locked comparison letter. A checked reading is inserted unchanged."""
    comparison = state.get("comparison") or {}
    label = STANCE_ZH.get(str(comparison.get("label") or ""), "無法判斷")
    base_name, peer_name = _letter_names(state)
    trade_date = str(state.get("trade_date") or comparison.get("trade_date") or "")
    subject = f"{trade_date} 概念股比較｜{base_name} 相對 {peer_name}：{label}".strip()
    lines = [
        f"{base_name} 相對同業 {peer_name}：{label}。",
        "",
        "## 比較結論",
    ]
    reasons = [str(item) for item in (comparison.get("reasons") or []) if str(item).strip()]
    if reasons:
        lines.extend(f"- {item}" for item in reasons)
    else:
        lines.append("- 可比欄位不足，沒有額外結論。")
    highlights = [str(item) for item in (comparison.get("highlights") or []) if str(item).strip()]
    if highlights:
        lines.extend(["", "## 同日事實"])
        lines.extend(f"- {item}" for item in highlights)
    themes = [str(item).strip() for item in (comparison.get("themes") or []) if str(item).strip()]
    if themes:
        lines.extend(["", f"共同題材：{'、'.join(themes)}。"])
    if reading and reading.strip():
        lines.extend(["", reading.strip()])
    else:
        lines.extend(["", "解讀未生成，這封只有規則比較。"])
    lines.extend(["", "## 部位"])
    if state.get("position_entered"):
        path = state.get("position_md_path") or "（尚未取得路徑）"
        lines.append(f"已進入部位報告：{path}")
    else:
        lines.append(str(state.get("position_skipped_reason") or "未跑部位。"))
    lines.extend(
        [
            "",
            "數字來自同日 facts 的規則比較。解讀只解釋上述結論，不另行比較。",
            "這是待核准草稿，尚未寄出。",
        ]
    )
    return subject, "\n".join(lines) + "\n"


def attach_peer_reading(
    state: dict[str, Any],
    *,
    complete: Callable[[str], str],
    read_text: Callable[[str | None], str],
) -> tuple[str, str, str]:
    """Ask for a reading, check it, and fall back to the rule letter."""
    report = _clip(_safe_read(read_text, state.get("md_path")), _REPORT_LIMIT)
    position = ""
    if state.get("position_entered"):
        position = _clip(_safe_read(read_text, state.get("position_md_path")), _POSITION_LIMIT)
    violations: list[str] = []
    reading: str | None = None
    for _ in range(_READING_ATTEMPTS):
        prompt = build_peer_reading_prompt(
            state,
            report=report,
            position=position,
            violations=violations,
        )
        try:
            candidate = parse_peer_reading(complete(prompt))
        except Exception as exc:
            violations = [f"無法讀取解讀：{exc}"]
            reading = None
            continue
        violations = validate_peer_reading(candidate, state, position_text=position)
        if not violations:
            reading = candidate
            break
        reading = None
    subject, body = compose_peer_email(state, reading=reading)
    if reading:
        note = "已寫概念股比較信草稿，含規則結論與解讀，核准後才寄出。"
    else:
        note = "已寫概念股比較信草稿。解讀未生成，這封只有規則比較。"
    return subject, body, note


def build_peer_reading_prompt(
    state: dict[str, Any],
    *,
    report: str,
    position: str,
    violations: list[str] | None = None,
) -> str:
    comparison = state.get("comparison") or {}
    label = STANCE_ZH.get(str(comparison.get("label") or ""), "無法判斷")
    base_name, peer_name = _letter_names(state)
    frozen = _frozen_text(comparison)
    report_body = report.strip() or "（沒有已通過的研究報告。這一段只能複述比較結論，不要補本檔故事。）"
    if position.strip():
        position_block = f"\n=== 部位報告（已通過，只可轉述）===\n{position.strip()}\n"
        position_rule = (
            "## 部位含義\n"
            "2～3 句，只轉述部位報告對這次結論的含義。不要寫新的成本、張數或目標價。\n"
        )
    else:
        position_block = ""
        position_rule = "不要寫「部位含義」，也不要推測持股。\n"
    prior = ""
    if violations:
        shown = "\n".join(f"- {item}" for item in violations[:8])
        prior = f"上次輸出未通過檢查，請整段重寫，並修正下列問題：\n{shown}\n\n"
    return (
        "你是台股比較信的解說。比較已經完成，你不要重新判斷誰強誰弱。\n"
        f"{_name_instruction(state, base_name, peer_name)}\n"
        f"總評標籤已定為：相對同業：{label}\n\n"
        "請輸出嚴格 JSON，不要加多餘說明：\n"
        '{"reading_markdown": "..."}\n\n'
        "reading_markdown 只能使用這些標題，順序固定：\n"
        "## 怎麼讀這次比較\n"
        f"緊接的第一句必須包含「相對同業：{label}」，接著說明凍結理由裡哪些同向、哪些反向。"
        "反向句只能複述凍結理由裡真正存在的那條，不能寫成新的總評。\n"
        "## 和本檔研究的關係\n"
        "用已通過的研究報告解釋上述理由。沒有報告就明說只能複述比較結論。"
        "新聞標題只能原文引用報告裡有的標題。\n"
        f"{position_rule}"
        "## 接下來看什麼\n"
        "2～3 點，每點對應一條凍結理由或研究報告已寫的觀察重點。不寫目標價，不寫保證漲跌。\n\n"
        "限制：\n"
        "- 不要輸出 subject、label，不要重寫「比較結論」或「同日事實」\n"
        "- 不要寫日期。不要寫凍結文字以外的百分比、張數或價位\n"
        "- 若要提到數字，必須與凍結文字裡的寫法一致；否則省略數字，沿用凍結理由的說法\n"
        "- MA5、MA10、MA20、RSI、5日、10日、20日、14日、3日 這些用語可以用\n"
        "- 外資買賣詞必須與同日事實裡本檔（該句第一個張數）的正負一致\n"
        "- 約 400～700 字\n\n"
        f"{prior}"
        "=== 凍結文字（強弱與數字只准來自這裡）===\n"
        f"{frozen}\n"
        "=== 凍結文字結束 ===\n\n"
        "=== 本檔研究報告（已通過；不可從這裡抄出凍結文字沒有的數字）===\n"
        f"{report_body}\n"
        "=== 研究報告結束 ===\n"
        f"{position_block}"
    )


def parse_peer_reading(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("解讀是空的")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        reading = _reading_from_json(text)
    except (json.JSONDecodeError, ValueError):
        reading = ""
    if not reading.strip() and "## 怎麼讀這次比較" in text:
        reading = text
    reading = reading.strip()
    if not reading:
        raise ValueError("解讀 JSON 缺少 reading_markdown")
    return reading


def validate_peer_reading(
    reading: str,
    state: dict[str, Any],
    *,
    position_text: str = "",
) -> list[str]:
    """Reject a reading that changes the stance or introduces new figures."""
    comparison = state.get("comparison") or {}
    label_key = str(comparison.get("label") or "")
    label = STANCE_ZH.get(label_key, "無法判斷")
    required = f"相對同業：{label}"
    problems: list[str] = []
    if len(reading) > _READING_LIMIT:
        problems.append(f"解讀超過 {_READING_LIMIT} 字")
    if "## 怎麼讀這次比較" not in reading:
        problems.append("解讀缺少「## 怎麼讀這次比較」")
    else:
        first = _first_line(_section_body(reading, "## 怎麼讀這次比較"))
        if required not in first:
            problems.append(f"「怎麼讀這次比較」第一句必須包含「{required}」")
    if "## 接下來看什麼" not in reading:
        problems.append("解讀缺少「## 接下來看什麼」")
    if _OWN_POSITION_HEADING_RE.search(reading):
        problems.append("解讀不得重寫「## 部位」")
    for heading in ("## 比較結論", "## 同日事實"):
        if heading in reading:
            problems.append(f"解讀不得重寫「{heading}」")
    if position_text.strip() and "## 部位含義" not in reading:
        problems.append("已有部位報告，解讀必須包含「## 部位含義」")
    frozen = _frozen_text(comparison)
    named_codes = _named_codes(state)
    scrubbed = _ALLOWED_WORDING_RE.sub(" ", reading)
    for token in _number_tokens(scrubbed):
        normalized = _norm_number(token)
        if normalized in named_codes:
            problems.append(f"解讀不要寫股號 {normalized}，請用{named_codes[normalized]}")
            continue
        if not _number_allowed(token, frozen):
            problems.append(f"解讀出現凍結文字沒有的數字：{token}")
    for phrase in _forbidden_totals(label_key):
        if phrase in reading:
            problems.append(f"解讀把總評寫成相反方向：{phrase}")
    sign = _base_foreign_sign(list(comparison.get("highlights") or []))
    if sign is not None:
        if sign > 0:
            banned = _FOREIGN_BEAR
        elif sign < 0:
            banned = _FOREIGN_BULL
        else:
            banned = _FOREIGN_BULL + _FOREIGN_BEAR
        for phrase in banned:
            if phrase in reading:
                problems.append(f"解讀的外資方向與同日事實相反：{phrase}")
    return _dedupe(problems)


def _reading_from_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("解讀 JSON 缺少 reading_markdown")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("解讀 JSON 必須是 object")
    return str(payload.get("reading_markdown") or "")


def _frozen_text(comparison: dict[str, Any]) -> str:
    lines: list[str] = []
    for key in ("reasons", "highlights"):
        for item in comparison.get(key) or []:
            text = str(item).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _section_body(reading: str, heading: str) -> str:
    start = reading.find(heading)
    if start < 0:
        return ""
    rest = reading[start + len(heading) :]
    next_heading = re.search(r"\n## ", rest)
    if next_heading:
        rest = rest[: next_heading.start()]
    return rest.strip()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _forbidden_totals(label: str) -> tuple[str, ...]:
    if label == "weaker":
        return ("整體較強", "本檔強於同業", "相對同業：較強")
    if label == "stronger":
        return ("整體較弱", "本檔弱於同業", "相對同業：較弱")
    return (
        "整體較強",
        "整體較弱",
        "本檔強於同業",
        "本檔弱於同業",
        "相對同業：較強",
        "相對同業：較弱",
    )


def _base_foreign_sign(highlights: list[str]) -> int | None:
    """Sign of the base lot. Highlights write the base figure before the peer."""
    for line in highlights:
        if "外資" not in str(line):
            continue
        match = _FOREIGN_LOT_RE.search(str(line))
        if match is None:
            continue
        value = float(match.group(1).replace(",", ""))
        if value > 0:
            return 1
        if value < 0:
            return -1
        return 0
    return None


def _number_tokens(text: str) -> list[str]:
    return [match.group(0) for match in _NUMBER_RE.finditer(text)]


def _norm_number(token: str) -> str:
    token = token.replace(",", "")
    if token.startswith("+"):
        token = token[1:]
    return token


def _letter_names(state: dict[str, Any]) -> tuple[str, str]:
    comparison = state.get("comparison") or {}
    stock_id = str(state.get("stock_id") or "").strip()
    peer_id = str(state.get("peer_id") or "").strip()
    stock_name = str(comparison.get("stock_name") or "").strip()
    peer_name = str(comparison.get("peer_name") or "").strip()
    return stock_name or stock_id, peer_name or peer_id


def _named_codes(state: dict[str, Any]) -> dict[str, str]:
    """Stock codes that should be written as names in the letter."""
    comparison = state.get("comparison") or {}
    named: dict[str, str] = {}
    for code_key, name_key in (("stock_id", "stock_name"), ("peer_id", "peer_name")):
        code = str(state.get(code_key) or "").strip()
        name = str(comparison.get(name_key) or "").strip()
        if code and name and name != code:
            named[code] = name
    return named


def _name_instruction(state: dict[str, Any], base_name: str, peer_name: str) -> str:
    if _named_codes(state):
        return (
            f"本檔是{base_name}，對照檔是{peer_name}。"
            "解讀只用這兩個名稱，不要寫股號，也不要另列題材。"
        )
    return f"本檔是{base_name}，對照檔是{peer_name}。不要另列題材。"


def _number_allowed(token: str, frozen: str) -> bool:
    normalized = _norm_number(token)
    frozen_norms = {_norm_number(item) for item in _number_tokens(frozen)}
    return normalized in frozen_norms


def _clip(text: str, limit: int) -> str:
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "\n（以下省略）"


def _safe_read(read_text: Callable[[str | None], str], path: Any) -> str:
    if not path:
        return ""
    try:
        return read_text(str(path)) or ""
    except Exception:
        return ""


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
