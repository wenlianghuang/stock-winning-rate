"""Shared fact-consistency checks for agy report bodies (report-gate & position-gate).

The narrative produced by agy must not contradict the deterministic chip facts
(see ``chip_signals.ChipFacts``). This module centralises those checks so both
gates share one implementation — fixing a false-positive here fixes it for both.

``run_fact_checks`` returns a list of ``(code, message)`` tuples; each caller
wraps them into its own ``ValidationIssue`` type.
"""

from __future__ import annotations

import re

MIN_FACT_CITATIONS = 2

# 當日方向檢查優先鎖定的段落關鍵字（找不到時退回現況段落）
TODAY_SECTION_KEYWORDS = ("當日籌碼", "籌碼解讀")

# 情境推演/操作情境章節會合理提及「若外資轉買」等反向假設，方向檢查須排除
_SCENARIO_MARKERS = ("情境推演", "操作情境", "情境", "推演", "短中線")

FOREIGN_BULLISH_PHRASES = (
    "外資大買",
    "外資買超",
    "外資轉買",
    "外資買盤",
    "外資偏多",
    "外資站買方",
    "外資持續買",
    "外資買進",
    "外資敲進",
)
FOREIGN_BEARISH_PHRASES = (
    "外資大賣",
    "外資賣超",
    "外資轉賣",
    "外資偏空",
    "外資站賣方",
    "外資持續賣",
    "外資調節",
    "外資賣出",
    "外資出脫",
)
MA5_ABOVE_PHRASES = (
    "站上MA5",
    "站回MA5",
    "站上均線",
    "站穩均線",
    "突破均線",
    "站上5日線",
    "收復均線",
    "站回5日",
)
MA5_BELOW_PHRASES = (
    "跌破MA5",
    "跌破均線",
    "失守均線",
    "跌破5日線",
    "跌破5日均線",
)

MA10_ABOVE_PHRASES = (
    "站上MA10",
    "站回MA10",
    "站上10日線",
    "站回10日線",
    "站上10日均線",
    "站回10日均線",
    "突破10日線",
    "收復10日線",
)
MA10_BELOW_PHRASES = (
    "跌破MA10",
    "跌破10日線",
    "失守10日線",
    "跌破10日均線",
    "10日線下方",
    "10日線之下",
)
MA20_ABOVE_PHRASES = (
    "站上MA20",
    "站上月線",
    "站回月線",
    "收復月線",
    "站穩月線",
    "突破月線",
    "站上20日線",
)
MA20_BELOW_PHRASES = (
    "跌破MA20",
    "跌破月線",
    "失守月線",
    "跌破20日線",
    "月線下方",
    "月線之下",
)
# 短中線均線排列（MA5 vs MA20）用語
MA_ALIGN_BULLISH_PHRASES = (
    "多頭排列",
    "均線多頭",
    "短中線偏多",
    "短中線同步偏多",
    "均線上揚",
)
MA_ALIGN_BEARISH_PHRASES = (
    "空頭排列",
    "均線空頭",
    "短中線偏空",
    "短中線同步偏空",
    "均線下彎",
)

# MA5 vs MA10（短線）與 MA10 vs MA20（短中線）用語（避免與 legacy 的 MA5 vs MA20 混用）
MA5_MA10_BULLISH_PHRASES = (
    "MA5與MA10同步偏多",
    "MA5/MA10同步偏多",
    "短線同步偏多",
    "短線偏多",
)
MA5_MA10_BEARISH_PHRASES = (
    "MA5與MA10同步偏空",
    "MA5/MA10同步偏空",
    "短線同步偏空",
    "短線偏空",
)
MA10_MA20_BULLISH_PHRASES = (
    "MA10與MA20同步偏多",
    "MA10/MA20同步偏多",
    "短中線同步偏多",
    "短中線偏多",
)
MA10_MA20_BEARISH_PHRASES = (
    "MA10與MA20同步偏空",
    "MA10/MA20同步偏空",
    "短中線同步偏空",
    "短中線偏空",
)
# 均線數值排列（MA5 > MA10 > MA20 或反之），與收盤相對均線位置不同
MA_STACK_BULLISH_PHRASES = (
    "多頭排列",
    "均線多頭",
    "MA5大於MA10",
    "MA5>MA10",
)
MA_STACK_BEARISH_PHRASES = (
    "空頭排列",
    "均線空頭",
    "MA5小於MA10",
    "MA5<MA10",
)
MA20_SLOPE_RISING_PHRASES = (
    "月線上揚",
    "月線向上",
    "MA20向上",
    "MA20走升",
    "月線走升",
    "月線趨勢向上",
)
MA20_SLOPE_FALLING_PHRASES = (
    "月線下彎",
    "月線向下",
    "MA20向下",
    "MA20走跌",
    "月線走跌",
    "月線趨勢向下",
)
RSI_OVERBOUGHT_PHRASES = (
    "RSI超買",
    "RSI偏高",
    "RSI過熱",
    "動能過熱",
    "超買區",
)
RSI_OVERSOLD_PHRASES = (
    "RSI超賣",
    "RSI偏低",
    "動能偏弱",
    "超賣區",
)
VOLATILITY_HIGH_PHRASES = (
    "波動偏高",
    "波動擴大",
    "波動劇烈",
    "波動加大",
    "ATR偏高",
    "ATR擴大",
    "震盪加劇",
)
VOLATILITY_LOW_PHRASES = (
    "波動偏低",
    "波動收斂",
    "波動萎縮",
    "ATR偏低",
    "波動溫和",
)
TREND_STRONG_PHRASES = (
    "趨勢明確",
    "趨勢強",
    "強勢趨勢",
    "ADX偏高",
    "ADX較高",
    "趨勢夠強",
    "趨勢方向明確",
)
TREND_WEAK_PHRASES = (
    "趨勢不明",
    "趨勢弱",
    "缺乏趨勢",
    "ADX偏低",
    "ADX較低",
    "震盪盤整",
    "區間震盪",
    "均線糾結",
)
MARGIN_RATIO_HIGH_PHRASES = (
    "券資比偏高",
    "券資比高",
    "融券壓力大",
    "融券相對偏高",
)
MARGIN_RATIO_LOW_PHRASES = (
    "券資比偏低",
    "券資比低",
    "融券壓力小",
    "融券相對偏低",
)
MARGIN_MOMENTUM_HEATING_PHRASES = (
    "融資動能偏強",
    "融資動能升溫",
    "融資餘額增加",
    "散戶加槓桿",
    "融資升溫",
)
MARGIN_MOMENTUM_COOLING_PHRASES = (
    "融資動能偏弱",
    "融資動能降溫",
    "融資餘額減少",
    "融資降溫",
)
# short_rebound：站上 MA5 但仍在 MA20 下，不可寫成同步偏多或中期已轉強
MA_ALIGN_REBOUND_DENY_PHRASES = MA_ALIGN_BULLISH_PHRASES + (
    "中期轉強",
    "月線站穩",
    "月線已站",
    "中期偏多",
    "中期已轉強",
)
# short_pullback：跌破 MA5 但仍在 MA20 上，不可寫成同步偏空或中期已轉弱
MA_ALIGN_PULLBACK_DENY_PHRASES = MA_ALIGN_BEARISH_PHRASES + (
    "中期轉弱",
    "月線失守",
    "中期偏空",
    "中期已轉弱",
)
VOLUME_SPIKE_PHRASES = (
    "放量",
    "量能放大",
    "成交量放大",
    "爆量",
    "量能激增",
    "成交量大增",
    "量能明顯放大",
)
VOLUME_SHRINK_PHRASES = (
    "量縮",
    "縮量",
    "量能萎縮",
    "交投清淡",
    "量能不足",
    "成交量萎縮",
    "量能明顯萎縮",
)
VOLUME_TREND_HEATING_PHRASES = (
    "量能升溫",
    "量增趨勢",
    "量能偏強",
)
VOLUME_TREND_COOLING_PHRASES = (
    "量能降溫",
    "量縮趨勢",
    "量能偏弱",
)
VOLUME_BEARISH_DIVERGENCE_PHRASES = (
    "價漲量縮",
    "價量背離",
    "量價背離",
    "上漲量縮",
)
VOLUME_BULLISH_DIVERGENCE_PHRASES = (
    "價跌量增",
    "下跌量增",
    "跌時量增",
)
VOLUME_CONFIRMING_UP_PHRASES = (
    "價量配合",
    "價漲量增",
    "量價配合偏多",
)
VOLUME_CONFIRMING_DOWN_PHRASES = (
    "價跌量縮",
    "量價配合偏空",
)
MA5_MA10_GOLDEN_PHRASES = (
    "MA5黃金交叉",
    "MA5上穿MA10",
    "MA5金叉",
    "5日線上穿10日線",
    "MA5/MA10黃金交叉",
)
MA5_MA10_DEATH_PHRASES = (
    "MA5死亡交叉",
    "MA5下穿MA10",
    "MA5死叉",
    "5日線下穿10日線",
    "MA5/MA10死亡交叉",
)
MA10_MA20_GOLDEN_PHRASES = (
    "MA10黃金交叉",
    "MA10上穿MA20",
    "MA10金叉",
    "10日線上穿月線",
    "MA10/MA20黃金交叉",
)
MA10_MA20_DEATH_PHRASES = (
    "MA10死亡交叉",
    "MA10下穿MA20",
    "MA10死叉",
    "10日線下穿月線",
    "MA10/MA20死亡交叉",
)
PRICE_PERIOD_UP_PHRASES = (
    "區間上漲",
    "區間走強",
    "區間偏多",
    "區間漲幅",
    "走勢偏強",
    "區間強勢上漲",
    "區間呈現上漲",
)
PRICE_PERIOD_DOWN_PHRASES = (
    "區間下跌",
    "區間走弱",
    "區間偏空",
    "區間跌幅",
    "走勢偏弱",
    "區間弱勢下跌",
    "區間呈現下跌",
)
CHIP_HEALTHY_PHRASES = (
    "籌碼健康",
    "量價配合良好",
    "量價齊揚",
    "籌碼穩定",
    "籌碼結構良好",
    "籌碼面樂觀",
)
CHIP_ACCUMULATION_PHRASES = (
    "籌碼集中",
    "籌碼沈澱",
    "吸籌",
    "法人布局",
    "籌碼偏多",
    "籌碼面佳",
)
CHIP_DISTRIBUTION_PHRASES = (
    "籌碼鬆動",
    "籌碼發散",
    "賣壓沉重",
    "籌碼偏空",
    "籌碼轉弱",
    "出貨",
)
INSTITUTIONAL_BULLISH_PHRASES = (
    "法人一致買",
    "三大法人買超",
    "法人同步買",
    "法人偏多",
)
INSTITUTIONAL_BEARISH_PHRASES = (
    "法人一致賣",
    "三大法人賣超",
    "法人同步賣",
    "法人偏空",
)
# 個股表現相對大盤（加權指數）的用語
MARKET_OUTPERFORM_PHRASES = (
    "強於大盤",
    "優於大盤",
    "相對強勢",
    "抗跌",
    "領先大盤",
    "表現優於大盤",
)
MARKET_UNDERPERFORM_PHRASES = (
    "弱於大盤",
    "落後大盤",
    "相對弱勢",
    "補跌",
    "表現弱於大盤",
    "走勢弱於大盤",
)

FactIssue = tuple[str, str]


def _contains_any(text: str, phrases: tuple[str, ...]) -> str | None:
    for phrase in phrases:
        if phrase in text:
            return phrase
    return None


def _strip_tables(text: str) -> str:
    """Drop Markdown table rows so news headlines don't trip direction checks.

    新聞表格常含「外資賣超/調節」等字，與個股當日方向無關，須先排除。
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("|")
    )


def _current_state_region(text: str) -> str:
    """Return the portion describing the *current* state (before scenarios)."""
    cut = len(text)
    for marker in _SCENARIO_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut]


def _slice_after_keywords(body: str, keywords: tuple[str, ...]) -> str:
    lines = body.splitlines()
    start = None
    # Prefer ``##`` section headings so inline text (e.g. 可觀察訊號 in 情境推演)
    # does not steal the slice from the intended chapter.
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^##\s+", stripped) and any(
            keyword in stripped for keyword in keywords
        ):
            start = index + 1
            break
    if start is None:
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


def _direction_region(text: str, today_keywords: tuple[str, ...]) -> str:
    """Region used for present-tense direction checks (tables stripped)."""
    region = _strip_tables(_slice_after_keywords(text, today_keywords))
    if not region.strip():
        region = _strip_tables(_current_state_region(text))
    return region


def _count_fact_citations(text: str, facts) -> tuple[int, int]:
    """Count how many *asserted* fact concepts the narrative references.

    Robust to number formatting: matches on stable concept keywords rather than
    exact anchor strings (the LLM is told not to repeat raw numbers).
    """
    concepts: list[bool] = []

    if getattr(facts, "foreign_direction", 0) != 0:
        concepts.append("外資" in text)
    if getattr(facts, "foreign_streak_days", 0) >= 2:
        concepts.append("連續" in text and "外資" in text)
    if getattr(facts, "major_available", False) and getattr(
        facts, "major_net_lots", None
    ) is not None:
        concepts.append("主力" in text)
    if getattr(facts, "ma5_position", "unknown") in {"above", "below"}:
        concepts.append(
            "MA5" in text or "均線" in text or "5日線" in text or "5 日線" in text
        )
    if getattr(facts, "ma10_position", "unknown") in {"above", "below"}:
        concepts.append("MA10" in text or "10日線" in text or "10 日線" in text)
    if getattr(facts, "ma20_position", "unknown") in {"above", "below"}:
        concepts.append("MA20" in text or "月線" in text or "20日線" in text)
    if getattr(facts, "price_trend", "unknown") in {"up", "down"}:
        concepts.append("區間" in text or "趨勢" in text)
    if getattr(facts, "divergences", None):
        concepts.append("背離" in text)
    if getattr(facts, "institutional_consensus", "unknown") in {"bullish", "bearish"}:
        concepts.append("法人" in text or "三大法人" in text)
    if getattr(facts, "major_foreign_divergence", False):
        concepts.append("背離" in text or "分歧" in text)
    if getattr(facts, "chip_regime", "unknown") in {"accumulation", "distribution"}:
        concepts.append("籌碼" in text or "法人" in text)
    if getattr(facts, "volume_anomaly", "unknown") in {"spike", "shrink"}:
        concepts.append("成交量" in text or "量能" in text)
    if getattr(facts, "rsi_zone", "unknown") in {"overbought", "oversold"}:
        concepts.append("RSI" in text or "動能" in text)
    if getattr(facts, "volatility_regime", "unknown") in {"high", "low"}:
        concepts.append("波動" in text or "ATR" in text)
    if getattr(facts, "trend_strength", "unknown") in {"strong", "weak"}:
        concepts.append("ADX" in text or "趨勢" in text)
    if getattr(facts, "margin_short_ratio_zone", "unknown") in {"high", "low"}:
        concepts.append("券資比" in text or "融券" in text)
    if getattr(facts, "margin_momentum", "unknown") in {"heating", "cooling"}:
        concepts.append("融資" in text or "槓桿" in text)

    total = len(concepts)
    cited = sum(1 for hit in concepts if hit)
    return cited, total


def run_fact_checks(
    body: str,
    facts,
    *,
    today_keywords: tuple[str, ...] = TODAY_SECTION_KEYWORDS,
) -> list[FactIssue]:
    """Return fact-consistency issues as ``(code, message)`` tuples.

    Guard against the "today vs period" ambiguity: if the region contains BOTH a
    bullish and a bearish phrase (e.g. 今日買超但區間賣超), the narrative is a
    legitimate mixed description and is NOT flagged.
    """
    if facts is None:
        return []

    text = body.strip()
    region = _direction_region(text, today_keywords)
    issues: list[FactIssue] = []

    foreign_dir = getattr(facts, "foreign_direction", 0)
    if foreign_dir != 0:
        bull = _contains_any(region, FOREIGN_BULLISH_PHRASES)
        bear = _contains_any(region, FOREIGN_BEARISH_PHRASES)
        if foreign_dir > 0 and bear and not bull:
            issues.append(
                (
                    "fact_foreign_direction",
                    f"外資今日為買超，但現況段出現偏空敘述「{bear}」，與系統 facts 矛盾",
                )
            )
        elif foreign_dir < 0 and bull and not bear:
            issues.append(
                (
                    "fact_foreign_direction",
                    f"外資今日為賣超，但現況段出現偏多敘述「{bull}」，與系統 facts 矛盾",
                )
            )

    ma5_position = getattr(facts, "ma5_position", "unknown")
    if ma5_position in {"above", "below"}:
        above = _contains_any(region, MA5_ABOVE_PHRASES)
        below = _contains_any(region, MA5_BELOW_PHRASES)
        if ma5_position == "below" and above and not below:
            issues.append(
                (
                    "fact_ma5_position",
                    f"收盤低於 MA5，但現況段出現「{above}」，與系統 facts 矛盾",
                )
            )
        elif ma5_position == "above" and below and not above:
            issues.append(
                (
                    "fact_ma5_position",
                    f"收盤高於 MA5，但現況段出現「{below}」，與系統 facts 矛盾",
                )
            )

    ma10_position = getattr(facts, "ma10_position", "unknown")
    if ma10_position in {"above", "below"}:
        above = _contains_any(region, MA10_ABOVE_PHRASES)
        below = _contains_any(region, MA10_BELOW_PHRASES)
        if ma10_position == "below" and above and not below:
            issues.append(
                (
                    "fact_ma10_position",
                    f"收盤低於 MA10（10 日線），但現況段出現「{above}」，與系統 facts 矛盾",
                )
            )
        elif ma10_position == "above" and below and not above:
            issues.append(
                (
                    "fact_ma10_position",
                    f"收盤高於 MA10（10 日線），但現況段出現「{below}」，與系統 facts 矛盾",
                )
            )

    ma20_position = getattr(facts, "ma20_position", "unknown")
    if ma20_position in {"above", "below"}:
        above = _contains_any(region, MA20_ABOVE_PHRASES)
        below = _contains_any(region, MA20_BELOW_PHRASES)
        if ma20_position == "below" and above and not below:
            issues.append(
                (
                    "fact_ma20_position",
                    f"收盤低於 MA20（月線），但現況段出現「{above}」，與系統 facts 矛盾",
                )
            )
        elif ma20_position == "above" and below and not above:
            issues.append(
                (
                    "fact_ma20_position",
                    f"收盤高於 MA20（月線），但現況段出現「{below}」，與系統 facts 矛盾",
                )
            )

    ma_alignment = getattr(facts, "ma_alignment", "unknown")
    if ma_alignment in {"bullish", "bearish", "short_rebound", "short_pullback"}:
        stripped = _strip_tables(text)
        if ma_alignment == "bullish":
            bull = _contains_any(stripped, MA_ALIGN_BULLISH_PHRASES)
            bear = _contains_any(stripped, MA_ALIGN_BEARISH_PHRASES)
            if bear and not bull:
                issues.append(
                    (
                        "fact_ma_alignment_mismatch",
                        f"facts 判定短中線同步偏多（站上 MA5/MA20），正文卻描述「{bear}」，與均線位置矛盾",
                    )
                )
        elif ma_alignment == "bearish":
            bull = _contains_any(stripped, MA_ALIGN_BULLISH_PHRASES)
            bear = _contains_any(stripped, MA_ALIGN_BEARISH_PHRASES)
            if bull and not bear:
                issues.append(
                    (
                        "fact_ma_alignment_mismatch",
                        f"facts 判定短中線同步偏空（跌破 MA5/MA20），正文卻描述「{bull}」，與均線位置矛盾",
                    )
                )
        elif ma_alignment == "short_rebound":
            deny = _contains_any(stripped, MA_ALIGN_REBOUND_DENY_PHRASES)
            if deny:
                issues.append(
                    (
                        "fact_ma_alignment_mismatch",
                        f"facts 判定短線反彈、中期仍弱（站上 MA5 但仍在 MA20 下），"
                        f"正文卻描述「{deny}」，與短中線型態矛盾",
                    )
                )
        elif ma_alignment == "short_pullback":
            deny = _contains_any(stripped, MA_ALIGN_PULLBACK_DENY_PHRASES)
            if deny:
                issues.append(
                    (
                        "fact_ma_alignment_mismatch",
                        f"facts 判定短線回檔、中期仍強（跌破 MA5 但仍在 MA20 上），"
                        f"正文卻描述「{deny}」，與短中線型態矛盾",
                    )
                )

    # New pairwise alignments (B): MA5 vs MA10, MA10 vs MA20
    ma_short = getattr(facts, "ma_short_alignment", "unknown")
    if ma_short in {"bullish", "bearish"}:
        stripped = _strip_tables(text)
        bull = _contains_any(stripped, MA5_MA10_BULLISH_PHRASES)
        bear = _contains_any(stripped, MA5_MA10_BEARISH_PHRASES)
        if ma_short == "bullish" and bear and not bull:
            issues.append(
                (
                    "fact_ma5_ma10_alignment_mismatch",
                    f"facts 判定 MA5 vs MA10 同步偏多，正文卻描述「{bear}」，與短線對齊矛盾",
                )
            )
        elif ma_short == "bearish" and bull and not bear:
            issues.append(
                (
                    "fact_ma5_ma10_alignment_mismatch",
                    f"facts 判定 MA5 vs MA10 同步偏空，正文卻描述「{bull}」，與短線對齊矛盾",
                )
            )

    ma_mid = getattr(facts, "ma_mid_alignment", "unknown")
    if ma_mid in {"bullish", "bearish"}:
        stripped = _strip_tables(text)
        bull = _contains_any(stripped, MA10_MA20_BULLISH_PHRASES)
        bear = _contains_any(stripped, MA10_MA20_BEARISH_PHRASES)
        if ma_mid == "bullish" and bear and not bull:
            issues.append(
                (
                    "fact_ma10_ma20_alignment_mismatch",
                    f"facts 判定 MA10 vs MA20 同步偏多，正文卻描述「{bear}」，與短中線對齊矛盾",
                )
            )
        elif ma_mid == "bearish" and bull and not bear:
            issues.append(
                (
                    "fact_ma10_ma20_alignment_mismatch",
                    f"facts 判定 MA10 vs MA20 同步偏空，正文卻描述「{bull}」，與短中線對齊矛盾",
                )
            )

    ma_stack = getattr(facts, "ma_stack", "unknown")
    if ma_stack in {"bullish_stack", "bearish_stack"}:
        stripped = _strip_tables(text)
        bull = _contains_any(stripped, MA_STACK_BULLISH_PHRASES)
        bear = _contains_any(stripped, MA_STACK_BEARISH_PHRASES)
        if ma_stack == "bullish_stack" and bear and not bull:
            issues.append(
                (
                    "fact_ma_stack_mismatch",
                    f"facts 判定均線多頭排列（MA5 > MA10 > MA20），"
                    f"正文卻描述「{bear}」，與均線排列矛盾",
                )
            )
        elif ma_stack == "bearish_stack" and bull and not bear:
            issues.append(
                (
                    "fact_ma_stack_mismatch",
                    f"facts 判定均線空頭排列（MA5 < MA10 < MA20），"
                    f"正文卻描述「{bull}」，與均線排列矛盾",
                )
            )

    ma20_slope = getattr(facts, "ma20_slope", "unknown")
    if ma20_slope in {"rising", "falling"}:
        stripped = _strip_tables(text)
        rising = _contains_any(stripped, MA20_SLOPE_RISING_PHRASES)
        falling = _contains_any(stripped, MA20_SLOPE_FALLING_PHRASES)
        if ma20_slope == "rising" and falling and not rising:
            issues.append(
                (
                    "fact_ma20_slope_mismatch",
                    f"facts 判定月線（MA20）趨勢向上，"
                    f"正文卻描述「{falling}」，與月線斜率矛盾",
                )
            )
        elif ma20_slope == "falling" and rising and not falling:
            issues.append(
                (
                    "fact_ma20_slope_mismatch",
                    f"facts 判定月線（MA20）趨勢向下，"
                    f"正文卻描述「{rising}」，與月線斜率矛盾",
                )
            )

    rsi_zone = getattr(facts, "rsi_zone", "unknown")
    if rsi_zone in {"overbought", "oversold"}:
        stripped = _strip_tables(text)
        overbought = _contains_any(stripped, RSI_OVERBOUGHT_PHRASES)
        oversold = _contains_any(stripped, RSI_OVERSOLD_PHRASES)
        if rsi_zone == "overbought" and oversold and not overbought:
            issues.append(
                (
                    "fact_rsi_zone_mismatch",
                    f"facts 判定 RSI 偏高（動能過熱），"
                    f"正文卻描述「{oversold}」，與 RSI 動能矛盾",
                )
            )
        elif rsi_zone == "oversold" and overbought and not oversold:
            issues.append(
                (
                    "fact_rsi_zone_mismatch",
                    f"facts 判定 RSI 偏低（動能偏弱），"
                    f"正文卻描述「{overbought}」，與 RSI 動能矛盾",
                )
            )

    volatility_regime = getattr(facts, "volatility_regime", "unknown")
    if volatility_regime in {"high", "low"}:
        stripped = _strip_tables(text)
        high_vol = _contains_any(stripped, VOLATILITY_HIGH_PHRASES)
        low_vol = _contains_any(stripped, VOLATILITY_LOW_PHRASES)
        if volatility_regime == "high" and low_vol and not high_vol:
            issues.append(
                (
                    "fact_volatility_regime_mismatch",
                    f"facts 判定波動偏高（ATR 擴大），"
                    f"正文卻描述「{low_vol}」，與波動狀態矛盾",
                )
            )
        elif volatility_regime == "low" and high_vol and not low_vol:
            issues.append(
                (
                    "fact_volatility_regime_mismatch",
                    f"facts 判定波動偏低，"
                    f"正文卻描述「{high_vol}」，與波動狀態矛盾",
                )
            )

    trend_strength = getattr(facts, "trend_strength", "unknown")
    if trend_strength in {"strong", "weak"}:
        stripped = _strip_tables(text)
        strong = _contains_any(stripped, TREND_STRONG_PHRASES)
        weak = _contains_any(stripped, TREND_WEAK_PHRASES)
        if trend_strength == "strong" and weak and not strong:
            issues.append(
                (
                    "fact_trend_strength_mismatch",
                    f"facts 判定趨勢明確（ADX 偏高），"
                    f"正文卻描述「{weak}」，與 ADX 趨勢強度矛盾",
                )
            )
        elif trend_strength == "weak" and strong and not weak:
            issues.append(
                (
                    "fact_trend_strength_mismatch",
                    f"facts 判定趨勢偏弱（ADX 偏低），"
                    f"正文卻描述「{strong}」，與 ADX 趨勢強度矛盾",
                )
            )

    margin_short_ratio_zone = getattr(facts, "margin_short_ratio_zone", "unknown")
    if margin_short_ratio_zone in {"high", "low"}:
        stripped = _strip_tables(text)
        high_ratio = _contains_any(stripped, MARGIN_RATIO_HIGH_PHRASES)
        low_ratio = _contains_any(stripped, MARGIN_RATIO_LOW_PHRASES)
        if margin_short_ratio_zone == "high" and low_ratio and not high_ratio:
            issues.append(
                (
                    "fact_margin_short_ratio_mismatch",
                    f"facts 判定券資比偏高，"
                    f"正文卻描述「{low_ratio}」，與券資比矛盾",
                )
            )
        elif margin_short_ratio_zone == "low" and high_ratio and not low_ratio:
            issues.append(
                (
                    "fact_margin_short_ratio_mismatch",
                    f"facts 判定券資比偏低，"
                    f"正文卻描述「{high_ratio}」，與券資比矛盾",
                )
            )

    margin_momentum = getattr(facts, "margin_momentum", "unknown")
    if margin_momentum in {"heating", "cooling"}:
        stripped = _strip_tables(text)
        heating = _contains_any(stripped, MARGIN_MOMENTUM_HEATING_PHRASES)
        cooling = _contains_any(stripped, MARGIN_MOMENTUM_COOLING_PHRASES)
        if margin_momentum == "heating" and cooling and not heating:
            issues.append(
                (
                    "fact_margin_momentum_mismatch",
                    f"facts 判定融資動能偏強（區間融資餘額增加），"
                    f"正文卻描述「{cooling}」，與融資動能矛盾",
                )
            )
        elif margin_momentum == "cooling" and heating and not cooling:
            issues.append(
                (
                    "fact_margin_momentum_mismatch",
                    f"facts 判定融資動能偏弱（區間融資餘額減少），"
                    f"正文卻描述「{heating}」，與融資動能矛盾",
                )
            )

    ma5_cross = getattr(facts, "ma5_cross_ma10", "unknown")
    ma5_cross_recency = getattr(facts, "ma5_cross_recency", "unknown")
    if ma5_cross in {"golden", "death"} and ma5_cross_recency in {
        "today",
        "within_3d",
    }:
        stripped = _strip_tables(text)
        golden = _contains_any(stripped, MA5_MA10_GOLDEN_PHRASES)
        death = _contains_any(stripped, MA5_MA10_DEATH_PHRASES)
        if ma5_cross == "golden" and death and not golden:
            issues.append(
                (
                    "fact_ma5_ma10_cross_mismatch",
                    f"facts 判定 MA5 黃金交叉 MA10（{ma5_cross_recency}），"
                    f"正文卻描述「{death}」，與均線交叉矛盾",
                )
            )
        elif ma5_cross == "death" and golden and not death:
            issues.append(
                (
                    "fact_ma5_ma10_cross_mismatch",
                    f"facts 判定 MA5 死亡交叉 MA10（{ma5_cross_recency}），"
                    f"正文卻描述「{golden}」，與均線交叉矛盾",
                )
            )

    ma10_cross = getattr(facts, "ma10_cross_ma20", "unknown")
    ma10_cross_recency = getattr(facts, "ma10_cross_recency", "unknown")
    if ma10_cross in {"golden", "death"} and ma10_cross_recency in {
        "today",
        "within_3d",
    }:
        stripped = _strip_tables(text)
        golden = _contains_any(stripped, MA10_MA20_GOLDEN_PHRASES)
        death = _contains_any(stripped, MA10_MA20_DEATH_PHRASES)
        if ma10_cross == "golden" and death and not golden:
            issues.append(
                (
                    "fact_ma10_ma20_cross_mismatch",
                    f"facts 判定 MA10 黃金交叉 MA20（{ma10_cross_recency}），"
                    f"正文卻描述「{death}」，與均線交叉矛盾",
                )
            )
        elif ma10_cross == "death" and golden and not death:
            issues.append(
                (
                    "fact_ma10_ma20_cross_mismatch",
                    f"facts 判定 MA10 死亡交叉 MA20（{ma10_cross_recency}），"
                    f"正文卻描述「{golden}」，與均線交叉矛盾",
                )
            )

    volume_anomaly = getattr(facts, "volume_anomaly", "unknown")
    if volume_anomaly in {"spike", "shrink"}:
        stripped = _strip_tables(text)
        if volume_anomaly == "spike":
            shrink = _contains_any(stripped, VOLUME_SHRINK_PHRASES)
            spike = _contains_any(stripped, VOLUME_SPIKE_PHRASES)
            if shrink and not spike:
                issues.append(
                    (
                        "fact_volume_mismatch",
                        f"facts 判定成交量明顯放大，正文卻描述「{shrink}」，與量能 facts 矛盾",
                    )
                )
        elif volume_anomaly == "shrink":
            spike = _contains_any(stripped, VOLUME_SPIKE_PHRASES)
            shrink = _contains_any(stripped, VOLUME_SHRINK_PHRASES)
            if spike and not shrink:
                issues.append(
                    (
                        "fact_volume_mismatch",
                        f"facts 判定成交量明顯萎縮，正文卻描述「{spike}」，與量能 facts 矛盾",
                    )
                )

    volume_trend = getattr(facts, "volume_trend", "unknown")
    if volume_trend in {"heating", "cooling"}:
        stripped = _strip_tables(text)
        heating = _contains_any(stripped, VOLUME_TREND_HEATING_PHRASES)
        cooling = _contains_any(stripped, VOLUME_TREND_COOLING_PHRASES)
        if volume_trend == "heating" and cooling and not heating:
            issues.append(
                (
                    "fact_volume_trend_mismatch",
                    f"facts 判定量能升溫（5日均量高於20日均量），"
                    f"正文卻描述「{cooling}」，與量能趨勢矛盾",
                )
            )
        elif volume_trend == "cooling" and heating and not cooling:
            issues.append(
                (
                    "fact_volume_trend_mismatch",
                    f"facts 判定量能降溫（5日均量低於20日均量），"
                    f"正文卻描述「{heating}」，與量能趨勢矛盾",
                )
            )

    volume_price_div = getattr(facts, "volume_price_divergence", "unknown")
    if volume_price_div in {
        "bearish_divergence",
        "bullish_divergence",
        "confirming_up",
        "confirming_down",
    }:
        stripped = _strip_tables(text)
        bearish = _contains_any(stripped, VOLUME_BEARISH_DIVERGENCE_PHRASES)
        bullish = _contains_any(stripped, VOLUME_BULLISH_DIVERGENCE_PHRASES)
        confirming_up = _contains_any(stripped, VOLUME_CONFIRMING_UP_PHRASES)
        confirming_down = _contains_any(stripped, VOLUME_CONFIRMING_DOWN_PHRASES)
        if volume_price_div == "bearish_divergence" and (
            confirming_up or bullish
        ) and not bearish:
            issues.append(
                (
                    "fact_volume_price_divergence_mismatch",
                    f"facts 判定價漲量縮（量價背離），"
                    f"正文卻描述「{confirming_up or bullish}」，與價量關係矛盾",
                )
            )
        elif volume_price_div == "bullish_divergence" and (
            confirming_down or bearish
        ) and not bullish:
            issues.append(
                (
                    "fact_volume_price_divergence_mismatch",
                    f"facts 判定價跌量增，"
                    f"正文卻描述「{confirming_down or bearish}」，與價量關係矛盾",
                )
            )
        elif volume_price_div == "confirming_up" and bearish and not confirming_up:
            issues.append(
                (
                    "fact_volume_price_divergence_mismatch",
                    f"facts 判定價量配合偏多，"
                    f"正文卻描述「{bearish}」，與價量關係矛盾",
                )
            )
        elif volume_price_div == "confirming_down" and bullish and not confirming_down:
            issues.append(
                (
                    "fact_volume_price_divergence_mismatch",
                    f"facts 判定價量配合偏空，"
                    f"正文卻描述「{bullish}」，與價量關係矛盾",
                )
            )

    price_trend = getattr(facts, "price_trend", "unknown")
    if price_trend in {"up", "down"}:
        stripped = _strip_tables(text)
        up = _contains_any(stripped, PRICE_PERIOD_UP_PHRASES)
        down = _contains_any(stripped, PRICE_PERIOD_DOWN_PHRASES)
        if price_trend == "up" and down and not up:
            issues.append(
                (
                    "fact_price_trend_mismatch",
                    f"facts 判定區間價格偏多（上漲），正文卻描述「{down}」，與 price_trend 矛盾",
                )
            )
        elif price_trend == "down" and up and not down:
            issues.append(
                (
                    "fact_price_trend_mismatch",
                    f"facts 判定區間價格偏空（下跌），正文卻描述「{up}」，與 price_trend 矛盾",
                )
            )

    if getattr(facts, "divergences", None):
        hit = _contains_any(_strip_tables(text), CHIP_HEALTHY_PHRASES)
        if hit:
            issues.append(
                (
                    "fact_divergence_ignored",
                    f"facts 已標記量價背離/風險旗標，正文卻描述「{hit}」",
                )
            )

    chip_regime = getattr(facts, "chip_regime", "unknown")
    if chip_regime == "distribution":
        hit = _contains_any(_strip_tables(text), CHIP_ACCUMULATION_PHRASES)
        if hit:
            issues.append(
                (
                    "fact_chip_regime_mismatch",
                    f"facts 判定籌碼偏空（distribution），正文卻描述「{hit}」",
                )
            )
    elif chip_regime == "accumulation":
        hit = _contains_any(_strip_tables(text), CHIP_DISTRIBUTION_PHRASES)
        if hit and not _contains_any(_strip_tables(text), ("風險", "留意", "但")):
            issues.append(
                (
                    "fact_chip_regime_mismatch",
                    f"facts 判定籌碼偏多（accumulation），正文卻描述「{hit}」且未提及風險",
                )
            )

    consensus = getattr(facts, "institutional_consensus", "unknown")
    if consensus == "bearish":
        bull = _contains_any(region, INSTITUTIONAL_BULLISH_PHRASES)
        bear = _contains_any(region, INSTITUTIONAL_BEARISH_PHRASES)
        if bull and not bear:
            issues.append(
                (
                    "fact_institutional_mismatch",
                    f"三大法人一致賣超，但現況段出現「{bull}」，與 facts 矛盾",
                )
            )
    elif consensus == "bullish":
        bull = _contains_any(region, INSTITUTIONAL_BULLISH_PHRASES)
        bear = _contains_any(region, INSTITUTIONAL_BEARISH_PHRASES)
        if bear and not bull:
            issues.append(
                (
                    "fact_institutional_mismatch",
                    f"三大法人一致買超，但現況段出現「{bear}」，與 facts 矛盾",
                )
            )

    if getattr(facts, "major_foreign_divergence", False):
        trend_cross = _strip_tables(
            _slice_after_keywords(text, ("趨勢", "交叉", "對照", "市場面"))
        )
        if trend_cross.strip() and not any(
            keyword in trend_cross for keyword in ("背離", "分歧", "不同步")
        ):
            issues.append(
                (
                    "fact_major_foreign_ignored",
                    "facts 標記主力與外資方向背離，正文須在趨勢/交叉段說明此分歧",
                )
            )

    rs = getattr(facts, "rs_period", "unknown")
    if rs == "unknown":
        rs = getattr(facts, "rs_today", "unknown")
    if rs in {"outperform", "underperform"}:
        stripped = _strip_tables(text)
        strong = _contains_any(stripped, MARKET_OUTPERFORM_PHRASES)
        weak = _contains_any(stripped, MARKET_UNDERPERFORM_PHRASES)
        if rs == "underperform" and strong and not weak:
            issues.append(
                (
                    "fact_market_rs_mismatch",
                    f"個股相對大盤為弱勢，但正文描述「{strong}」，與 facts 相對強弱矛盾",
                )
            )
        elif rs == "outperform" and weak and not strong:
            issues.append(
                (
                    "fact_market_rs_mismatch",
                    f"個股相對大盤為強勢，但正文描述「{weak}」，與 facts 相對強弱矛盾",
                )
            )

    cited, total = _count_fact_citations(text, facts)
    if total >= 2:
        required = min(MIN_FACT_CITATIONS, total)
        if cited < required:
            issues.append(
                (
                    "anchors_underused",
                    f"正文引用的系統籌碼事實不足（{cited}/{total} 概念），"
                    f"至少須明確引用 {required} 項 facts 概念",
                )
            )

    return issues
