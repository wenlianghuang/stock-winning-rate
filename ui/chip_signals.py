"""Deterministic chip signals (facts) computed from a stock CSV row + history.

This is the *harness* layer: Python owns the factual verdicts (directions,
streaks, divergences) so the LLM only has to explain them. The same facts feed
both the agy prompt and the fact-consistency validation, so any narrative that
contradicts these signals can be caught mechanically.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


def _to_float(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("+", "")
    if not text or text in {"—", "-", "NA", "N/A", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw: object) -> int | None:
    value = _to_float(raw)
    return int(round(value)) if value is not None else None


def _sign(value: float | None, *, deadband: float = 0.0) -> int:
    if value is None:
        return 0
    if value > deadband:
        return 1
    if value < -deadband:
        return -1
    return 0


def _fmt_lots(value: int | None) -> str:
    if value is None:
        return "資料未取得"
    return f"{value:,} 張"


def _trailing_streak(history: list[dict], key: str) -> tuple[int, int]:
    """Return (direction, count) of the trailing same-sign run in ``key``.

    ``history`` is expected oldest-first; the streak is measured from the most
    recent row backwards. direction is -1/0/1, count is the run length.
    """
    if not history:
        return 0, 0
    signs = [_sign(_to_float(row.get(key))) for row in history]
    last = signs[-1]
    if last == 0:
        return 0, 0
    count = 0
    for sign in reversed(signs):
        if sign == last:
            count += 1
        else:
            break
    return last, count


DIRECTION_LABEL = {1: "買超", -1: "賣超", 0: "中性"}


@dataclass
class ChipFacts:
    stock_id: str
    stock_name: str
    trade_date: str

    # 當日方向（張）
    foreign_net_lots: int | None
    trust_net_lots: int | None
    dealer_net_lots: int | None
    major_net_lots: int | None
    major_available: bool

    today_change_pct: float | None
    close_vs_ma5_pct: float | None
    margin_today_delta_lots: int | None

    # 區間趨勢
    lookback_days: int | None
    period_return_pct: float | None
    foreign_cum_lots: int | None
    major_cum_lots: int | None
    margin_delta_lots: int | None
    short_delta_lots: int | None

    # 規則化判定
    foreign_direction: int  # -1/0/1 當日
    foreign_streak_dir: int
    foreign_streak_days: int
    price_trend: str  # "up" | "down" | "flat" | "unknown"
    ma5_position: str  # "above" | "below" | "at" | "unknown"
    divergences: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)


def build_chip_facts(row: dict, history: list[dict] | None = None) -> ChipFacts:
    history = history or []

    stock_id = str(row.get("代碼", "")).strip()
    stock_name = str(row.get("名稱", "")).strip()
    trade_date = str(row.get("日期", "")).strip()

    foreign_net = _to_int(row.get("外資買賣超_張"))
    trust_net = _to_int(row.get("投信買賣超_張"))
    dealer_net = _to_int(row.get("自營商買賣超_張"))
    major_available = str(row.get("主力_擷取狀態", "")).strip().lower() == "ok"
    major_net = _to_int(row.get("主力買賣超_張")) if major_available else None

    today_change = _to_float(row.get("漲跌幅"))
    close_vs_ma5 = _to_float(row.get("收盤偏離MA5_%"))
    margin_today_delta = _to_int(row.get("融資增減_張"))

    lookback = _to_int(row.get("回看天數"))
    period_return = _to_float(row.get("區間漲跌幅_%"))
    foreign_cum = _to_int(row.get("區間外資累計_張"))
    major_cum = _to_int(row.get("區間主力累計_張"))
    margin_delta = _to_int(row.get("區間融資餘額淨變化_張"))
    short_delta = _to_int(row.get("區間融券餘額淨變化_張"))

    foreign_direction = _sign(foreign_net)
    streak_dir, streak_days = _trailing_streak(history, "外資買賣超_張")

    if period_return is None:
        price_trend = "unknown"
    elif period_return > 2:
        price_trend = "up"
    elif period_return < -2:
        price_trend = "down"
    else:
        price_trend = "flat"

    if close_vs_ma5 is None:
        ma5_position = "unknown"
    elif close_vs_ma5 > 0.3:
        ma5_position = "above"
    elif close_vs_ma5 < -0.3:
        ma5_position = "below"
    else:
        ma5_position = "at"

    divergences = _detect_divergences(
        today_change=today_change,
        foreign_net=foreign_net,
        margin_today_delta=margin_today_delta,
        period_return=period_return,
        margin_delta=margin_delta,
    )

    facts = ChipFacts(
        stock_id=stock_id,
        stock_name=stock_name,
        trade_date=trade_date,
        foreign_net_lots=foreign_net,
        trust_net_lots=trust_net,
        dealer_net_lots=dealer_net,
        major_net_lots=major_net,
        major_available=major_available,
        today_change_pct=today_change,
        close_vs_ma5_pct=close_vs_ma5,
        margin_today_delta_lots=margin_today_delta,
        lookback_days=lookback,
        period_return_pct=period_return,
        foreign_cum_lots=foreign_cum,
        major_cum_lots=major_cum,
        margin_delta_lots=margin_delta,
        short_delta_lots=short_delta,
        foreign_direction=foreign_direction,
        foreign_streak_dir=streak_dir,
        foreign_streak_days=streak_days,
        price_trend=price_trend,
        ma5_position=ma5_position,
        divergences=divergences,
    )
    facts.anchors = _build_anchors(facts)
    return facts


def _detect_divergences(
    *,
    today_change: float | None,
    foreign_net: int | None,
    margin_today_delta: int | None,
    period_return: float | None,
    margin_delta: int | None,
) -> list[str]:
    flags: list[str] = []
    if today_change is not None and foreign_net is not None:
        if today_change > 0 and foreign_net < 0:
            flags.append("price_up_foreign_sell")
        elif today_change < 0 and foreign_net > 0:
            flags.append("price_down_foreign_buy")
    if period_return is not None and margin_delta is not None:
        if period_return < 0 and margin_delta > 0:
            flags.append("price_down_margin_up")
        elif period_return > 0 and margin_delta > 0 and today_change is not None and today_change > 0:
            flags.append("price_up_margin_up")
    return flags


DIVERGENCE_LABEL = {
    "price_up_foreign_sell": "當日價漲但外資賣超（量價背離，追高需留意）",
    "price_down_foreign_buy": "當日價跌但外資買超（可能逢低承接）",
    "price_down_margin_up": "區間價跌但融資餘額增加（散戶不認賠，籌碼偏壓）",
    "price_up_margin_up": "區間價漲且融資同步增加（追價籌碼偏浮動）",
}


def _build_anchors(facts: ChipFacts) -> list[str]:
    anchors: list[str] = []

    if facts.foreign_net_lots is not None:
        anchors.append(
            f"外資今日{DIRECTION_LABEL[facts.foreign_direction]}"
            + (
                f" {abs(facts.foreign_net_lots):,} 張"
                if facts.foreign_direction != 0
                else ""
            )
        )
    if facts.foreign_streak_days >= 2 and facts.foreign_streak_dir != 0:
        anchors.append(
            f"外資近 {facts.foreign_streak_days} 日連續"
            f"{DIRECTION_LABEL[facts.foreign_streak_dir]}"
        )
    if facts.major_available and facts.major_net_lots is not None:
        direction = _sign(facts.major_net_lots)
        anchors.append(
            f"主力今日{DIRECTION_LABEL[direction]}"
            + (f" {abs(facts.major_net_lots):,} 張" if direction != 0 else "")
        )
    elif not facts.major_available:
        anchors.append("主力進出資料未取得")

    if facts.ma5_position == "above" and facts.close_vs_ma5_pct is not None:
        anchors.append(f"收盤高於 MA5 約 {facts.close_vs_ma5_pct:.1f}%")
    elif facts.ma5_position == "below" and facts.close_vs_ma5_pct is not None:
        anchors.append(f"收盤低於 MA5 約 {abs(facts.close_vs_ma5_pct):.1f}%")

    if facts.price_trend in {"up", "down"} and facts.period_return_pct is not None:
        move = "上漲" if facts.price_trend == "up" else "下跌"
        days = f"{facts.lookback_days} 日" if facts.lookback_days else "區間"
        anchors.append(f"近 {days}{move} {abs(facts.period_return_pct):.1f}%")

    for flag in facts.divergences:
        label = DIVERGENCE_LABEL.get(flag)
        if label:
            anchors.append(label)

    return anchors


def facts_to_json(facts: ChipFacts) -> dict:
    return asdict(facts)


def facts_summary_for_prompt(facts: ChipFacts) -> str:
    """Human-readable, LLM-facing summary of the deterministic facts."""
    lines = [
        f"股票：{facts.stock_name}（{facts.stock_id}），資料日期 {facts.trade_date}",
        "",
        "【當日方向（系統判定，勿與之矛盾）】",
        f"- 外資：{DIRECTION_LABEL[facts.foreign_direction]}"
        f"（{_fmt_lots(facts.foreign_net_lots)}）",
        f"- 投信：{_fmt_lots(facts.trust_net_lots)}",
        f"- 自營商：{_fmt_lots(facts.dealer_net_lots)}",
        f"- 主力：{_fmt_lots(facts.major_net_lots) if facts.major_available else '資料未取得'}",
    ]
    if facts.today_change_pct is not None:
        lines.append(f"- 當日漲跌幅：{facts.today_change_pct:+.2f}%")
    if facts.ma5_position != "unknown" and facts.close_vs_ma5_pct is not None:
        pos = {"above": "高於", "below": "低於", "at": "貼近"}[facts.ma5_position]
        lines.append(f"- 收盤{pos} MA5（偏離 {facts.close_vs_ma5_pct:+.2f}%）")

    lines.extend(["", "【區間趨勢】"])
    if facts.foreign_streak_days >= 2 and facts.foreign_streak_dir != 0:
        lines.append(
            f"- 外資近 {facts.foreign_streak_days} 日連續"
            f"{DIRECTION_LABEL[facts.foreign_streak_dir]}"
        )
    trend_label = {
        "up": "偏多（區間上漲）",
        "down": "偏空（區間下跌）",
        "flat": "區間震盪",
        "unknown": "資料不足",
    }[facts.price_trend]
    if facts.period_return_pct is not None:
        lines.append(f"- 價格趨勢：{trend_label}（{facts.period_return_pct:+.2f}%）")
    else:
        lines.append(f"- 價格趨勢：{trend_label}")
    lines.append(f"- 外資區間累計：{_fmt_lots(facts.foreign_cum_lots)}")
    lines.append(f"- 主力區間累計：{_fmt_lots(facts.major_cum_lots)}")
    lines.append(f"- 融資餘額淨變化：{_fmt_lots(facts.margin_delta_lots)}")

    if facts.divergences:
        lines.extend(["", "【背離/風險旗標】"])
        for flag in facts.divergences:
            lines.append(f"- {DIVERGENCE_LABEL.get(flag, flag)}")

    if facts.anchors:
        lines.extend(["", "【必須在正文中引用的關鍵事實 anchors（至少 2 條）】"])
        for anchor in facts.anchors:
            lines.append(f"- {anchor}")

    return "\n".join(lines)


def write_facts_json(path, facts: ChipFacts) -> None:
    from pathlib import Path

    Path(path).write_text(
        json.dumps(facts_to_json(facts), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
