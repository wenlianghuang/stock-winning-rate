"""Deterministic market-day facts: TAIEX volume/price, institutions, 2330, US day."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from day_window import DayWindow, resolve_day_window

FOREIGN_NAMES = {"Foreign_Investor", "Foreign_Dealer_Self"}
TRUST_NAMES = {"Investment_Trust"}
DEALER_NAMES = {"Dealer_self", "Dealer_Hedging", "Dealer"}
TSM_ID = "2330"
TSM_NAME = "台積電"
VOLUME_RATIO_EXPAND = 1.2
VOLUME_RATIO_SHRINK = 0.8
MA_NEAR_BAND_PCT = 0.3


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_import_paths() -> None:
    root = project_root()
    for sub in (
        "ui",
        ".agents/skills/tw-stock-report",
        ".agents/skills/market-daily",
    ):
        text = str(root / sub)
        if text not in sys.path:
            sys.path.insert(0, text)


def market_output_dir(trade_date: str) -> Path:
    return project_root() / "reports" / "market" / trade_date


def facts_path(trade_date: str) -> Path:
    return market_output_dir(trade_date) / "tw_market_daily.facts.json"


@dataclass
class MarketDayFacts:
    trade_date: str
    for_session: str
    prior_trade_date: str | None
    lookback_days: list[str]
    resolved_as_of: str
    cutover_applied: bool
    us_as_of: str | None = None
    us_cutover_passed: bool | None = None
    market: dict[str, Any] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)
    institutional: dict[str, Any] = field(default_factory=dict)
    technical: dict[str, Any] = field(default_factory=dict)
    tsmc: dict[str, Any] = field(default_factory=dict)
    us: dict[str, Any] = field(default_factory=dict)
    bias_hint: str = "neutral"
    anchors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_float(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw: Any) -> int | None:
    value = _to_float(raw)
    if value is None:
        return None
    return int(round(value))


def _sign(value: int | float | None, *, deadband: int = 0) -> int:
    if value is None:
        return 0
    if abs(value) <= deadband:
        return 0
    return 1 if value > 0 else -1


def day_return_pct(prior: float | None, last: float | None) -> float | None:
    if prior is None or last is None or prior == 0:
        return None
    return round((last - prior) / prior * 100, 2)


def alignment_label(left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return "unavailable"
    if left == 0 or right == 0:
        if left == 0 and right == 0:
            return "一致"
        if (left >= 0 and right >= 0) or (left <= 0 and right <= 0):
            return "一致"
        return "背離"
    if (left > 0 and right > 0) or (left < 0 and right < 0):
        return "一致"
    return "背離"


def institutional_consensus(
    foreign: int | None,
    trust: int | None,
    dealer: int | None,
) -> str:
    signs = [_sign(value) for value in (foreign, trust, dealer) if value is not None]
    if not signs:
        return "unknown"
    active = [sign for sign in signs if sign != 0]
    if not active:
        return "neutral"
    if all(sign > 0 for sign in active):
        return "bullish"
    if all(sign < 0 for sign in active):
        return "bearish"
    return "mixed"


INSTITUTIONAL_LABEL = {
    "bullish": "三大法人一致買超",
    "bearish": "三大法人一致賣超",
    "mixed": "三大法人方向分歧",
    "neutral": "三大法人買賣中性",
    "unknown": "三大法人資料不足",
}


def fetch_taiex_rows(
    start_date: str,
    end_date: str,
    *,
    token: str | None = None,
) -> dict[str, dict[str, Any]]:
    ensure_import_paths()
    from fetch_chip_report import FinMindClient, index_rows_by_date

    client = FinMindClient(token=(token or os.environ.get("FINMIND_TOKEN", "")).strip())
    rows = index_rows_by_date(
        client.fetch_dataset(
            "TaiwanStockPrice",
            stock_id="TAIEX",
            start_date=start_date,
            end_date=end_date,
        )
    )
    return rows


def fetch_institutional_rows(
    start_date: str,
    end_date: str,
    *,
    token: str | None = None,
) -> list[dict[str, Any]]:
    ensure_import_paths()
    from fetch_chip_report import FinMindClient

    client = FinMindClient(token=(token or os.environ.get("FINMIND_TOKEN", "")).strip())
    return client.fetch_dataset(
        "TaiwanStockTotalInstitutionalInvestors",
        start_date=start_date,
        end_date=end_date,
    )


def fetch_stock_day(
    stock_id: str,
    start_date: str,
    end_date: str,
    *,
    token: str | None = None,
) -> dict[str, dict[str, Any]]:
    ensure_import_paths()
    from fetch_chip_report import FinMindClient, index_rows_by_date

    client = FinMindClient(token=(token or os.environ.get("FINMIND_TOKEN", "")).strip())
    return index_rows_by_date(
        client.fetch_dataset(
            "TaiwanStockPrice",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
    )


def build_market_block(
    trade_date: str,
    prior_trade_date: str | None,
    taiex_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = taiex_rows.get(trade_date) or {}
    prior_row = taiex_rows.get(prior_trade_date or "") or {}
    close = _to_float(row.get("close"))
    prior_close = _to_float(prior_row.get("close"))
    high = _to_float(row.get("max"))
    low = _to_float(row.get("min"))
    open_ = _to_float(row.get("open"))
    ret = day_return_pct(prior_close, close)
    position = None
    if high is not None and low is not None and close is not None and high != low:
        position = round((close - low) / (high - low) * 100, 1)
    return {
        "trade_date": trade_date,
        "prior_trade_date": prior_trade_date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "prior_close": prior_close,
        "day_return_pct": ret,
        "close_in_day_range_pct": position,
    }


def build_volume_block(
    trade_date: str,
    lookback_days: list[str],
    taiex_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    volumes: list[tuple[str, int]] = []
    for day in lookback_days:
        row = taiex_rows.get(day) or {}
        vol = _to_int(row.get("Trading_Volume"))
        if vol is not None:
            volumes.append((day, vol))
    today_vol = next((v for d, v in volumes if d == trade_date), None)
    hist = [v for d, v in volumes if d != trade_date]
    avg5 = round(sum(hist[-5:]) / 5, 0) if len(hist) >= 5 else None
    avg20 = round(sum(hist[-20:]) / 20, 0) if len(hist) >= 20 else None
    ratio5 = (
        round(today_vol / avg5, 2)
        if today_vol is not None and avg5 not in (None, 0)
        else None
    )
    if ratio5 is None:
        regime = "unknown"
    elif ratio5 >= VOLUME_RATIO_EXPAND:
        regime = "expand"
    elif ratio5 <= VOLUME_RATIO_SHRINK:
        regime = "shrink"
    else:
        regime = "normal"
    return {
        "trade_date": trade_date,
        "volume": today_vol,
        "avg5": avg5,
        "avg20": avg20,
        "vs_avg5_ratio": ratio5,
        "regime": regime,
    }


def build_institutional_block(
    trade_date: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    day_rows = [r for r in rows if str(r.get("date") or "") == trade_date]
    if not day_rows:
        return {
            "available": False,
            "trade_date": trade_date,
            "foreign_net": None,
            "trust_net": None,
            "dealer_net": None,
            "total_net": None,
            "consensus": "unknown",
            "consensus_label": INSTITUTIONAL_LABEL["unknown"],
            "unit": "shares",
        }

    foreign = 0
    trust = 0
    dealer = 0
    saw_foreign = False
    saw_trust = False
    saw_dealer = False
    for row in day_rows:
        name = str(row.get("name") or "").strip()
        buy = _to_float(row.get("buy")) or 0.0
        sell = _to_float(row.get("sell")) or 0.0
        net = int(round(buy - sell))
        if name in FOREIGN_NAMES:
            foreign += net
            saw_foreign = True
        elif name in TRUST_NAMES:
            trust += net
            saw_trust = True
        elif name in DEALER_NAMES:
            dealer += net
            saw_dealer = True

    foreign_v = foreign if saw_foreign else None
    trust_v = trust if saw_trust else None
    dealer_v = dealer if saw_dealer else None
    parts = [v for v in (foreign_v, trust_v, dealer_v) if v is not None]
    total = sum(parts) if parts else None
    consensus = institutional_consensus(foreign_v, trust_v, dealer_v)
    return {
        "available": bool(parts),
        "trade_date": trade_date,
        "foreign_net": foreign_v,
        "trust_net": trust_v,
        "dealer_net": dealer_v,
        "total_net": total,
        "consensus": consensus,
        "consensus_label": INSTITUTIONAL_LABEL.get(consensus, consensus),
        "unit": "shares",
    }


def build_technical_block(
    trade_date: str,
    lookback_days: list[str],
    taiex_rows: dict[str, dict[str, Any]],
    market: dict[str, Any],
) -> dict[str, Any]:
    closes: list[float] = []
    for day in lookback_days:
        close = _to_float((taiex_rows.get(day) or {}).get("close"))
        if close is not None:
            closes.append(close)
    last = closes[-1] if closes else market.get("close")
    ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else None
    ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None

    def _vs_ma(ma: float | None) -> str:
        if last is None or ma is None or ma == 0:
            return "unknown"
        diff_pct = (float(last) - ma) / ma * 100
        if abs(diff_pct) <= MA_NEAR_BAND_PCT:
            return "near"
        return "above" if diff_pct > 0 else "below"

    return {
        "trade_date": trade_date,
        "close": last,
        "ma5": ma5,
        "ma20": ma20,
        "vs_ma5": _vs_ma(ma5),
        "vs_ma20": _vs_ma(ma20),
        "close_in_day_range_pct": market.get("close_in_day_range_pct"),
    }


def build_tsmc_block(
    trade_date: str,
    prior_trade_date: str | None,
    stock_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = stock_rows.get(trade_date) or {}
    prior = stock_rows.get(prior_trade_date or "") or {}
    close = _to_float(row.get("close"))
    prior_close = _to_float(prior.get("close"))
    if close is None:
        return {"available": False, "stock_id": TSM_ID, "name": TSM_NAME}
    return {
        "available": True,
        "stock_id": TSM_ID,
        "name": TSM_NAME,
        "close": close,
        "prior_close": prior_close,
        "day_return_pct": day_return_pct(prior_close, close),
    }


def empty_us_block(
    *,
    reason: str = "skipped",
    as_of: str | None = None,
    cutover_passed: bool | None = None,
) -> dict[str, Any]:
    return {
        "source": "yahoo_finance",
        "as_of": as_of,
        "cutover": "05:30+08:00",
        "cutover_passed": cutover_passed,
        "available": False,
        "skipped": True,
        "skip_reason": reason,
        "indices": {"IXIC": None, "SOX": None},
        "alignment": {
            "ixic_vs_taiex": "unavailable",
            "sox_vs_tsmc": "unavailable",
        },
        "gaps": {
            "ixic_minus_taiex_pct": None,
            "sox_minus_tsmc_pct": None,
        },
    }


def attach_us_alignment(
    us_block: dict[str, Any],
    *,
    taiex_day_return: float | None,
    tsmc_day_return: float | None,
) -> dict[str, Any]:
    indices = us_block.get("indices") or {}
    ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else None
    sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else None
    ixic_ret = ixic.get("day_return_pct") if ixic else None
    sox_ret = sox.get("day_return_pct") if sox else None
    if not isinstance(ixic_ret, (int, float)):
        ixic_ret = None
    else:
        ixic_ret = float(ixic_ret)
    if not isinstance(sox_ret, (int, float)):
        sox_ret = None
    else:
        sox_ret = float(sox_ret)

    gap_ixic = (
        round(ixic_ret - taiex_day_return, 2)
        if ixic_ret is not None and taiex_day_return is not None
        else None
    )
    gap_sox = (
        round(sox_ret - tsmc_day_return, 2)
        if sox_ret is not None and tsmc_day_return is not None
        else None
    )
    us_block["alignment"] = {
        "ixic_vs_taiex": alignment_label(ixic_ret, taiex_day_return),
        "sox_vs_tsmc": alignment_label(sox_ret, tsmc_day_return),
    }
    us_block["gaps"] = {
        "ixic_minus_taiex_pct": gap_ixic,
        "sox_minus_tsmc_pct": gap_sox,
    }
    usable = ixic_ret is not None or sox_ret is not None
    us_block["available"] = bool(usable and us_block.get("available", True))
    return us_block


def compute_bias_hint(
    *,
    market: dict[str, Any],
    volume: dict[str, Any],
    institutional: dict[str, Any],
    technical: dict[str, Any],
    us: dict[str, Any],
) -> str:
    """Conservative rule blend; neutral unless multiple signals agree."""
    score = 0
    ret = market.get("day_return_pct")
    if isinstance(ret, (int, float)):
        if ret > 0.3:
            score += 1
        elif ret < -0.3:
            score -= 1

    consensus = institutional.get("consensus")
    if consensus == "bullish":
        score += 1
    elif consensus == "bearish":
        score -= 1

    if technical.get("vs_ma5") == "above":
        score += 1
    elif technical.get("vs_ma5") == "below":
        score -= 1

    regime = volume.get("regime")
    if regime == "expand" and isinstance(ret, (int, float)):
        score += 1 if ret > 0 else -1
    elif regime == "shrink":
        # shrinking volume weakens conviction; nudge toward neutral
        if score > 0:
            score -= 1
        elif score < 0:
            score += 1

    ixic_align = (us.get("alignment") or {}).get("ixic_vs_taiex")
    if ixic_align == "一致" and isinstance(ret, (int, float)):
        score += 1 if ret > 0 else -1
    elif ixic_align == "背離":
        if score > 0:
            score -= 1
        elif score < 0:
            score += 1

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


BIAS_LABEL = {
    "bullish": "偏多",
    "bearish": "偏空",
    "neutral": "中性",
}


def build_anchors(facts: MarketDayFacts) -> list[str]:
    anchors: list[str] = []
    m = facts.market
    if m.get("day_return_pct") is not None:
        anchors.append(f"大盤日報酬 {m['day_return_pct']}%")
    if m.get("close") is not None:
        anchors.append(f"加權收盤 {m['close']}")
    vol = facts.volume
    if vol.get("vs_avg5_ratio") is not None:
        anchors.append(f"量能相對五日均量 {vol['vs_avg5_ratio']}x（{vol.get('regime')}）")
    inst = facts.institutional
    if inst.get("available"):
        anchors.append(inst.get("consensus_label") or INSTITUTIONAL_LABEL["unknown"])
        if inst.get("foreign_net") is not None:
            anchors.append(f"外資淨額 {inst['foreign_net']}")
    tech = facts.technical
    if tech.get("ma5") is not None:
        anchors.append(f"MA5 {tech['ma5']}（{tech.get('vs_ma5')}）")
    if tech.get("ma20") is not None:
        anchors.append(f"MA20 {tech['ma20']}（{tech.get('vs_ma20')}）")
    tsmc = facts.tsmc
    if tsmc.get("available") and tsmc.get("day_return_pct") is not None:
        anchors.append(f"台積電日報酬 {tsmc['day_return_pct']}%")
    us = facts.us or {}
    indices = us.get("indices") or {}
    ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else None
    sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else None
    if ixic and ixic.get("day_return_pct") is not None:
        anchors.append(f"那指日報酬 {ixic['day_return_pct']}%")
    if sox and sox.get("day_return_pct") is not None:
        anchors.append(f"費半日報酬 {sox['day_return_pct']}%")
    anchors.append(f"開盤偏誤提示 {BIAS_LABEL.get(facts.bias_hint, facts.bias_hint)}")
    return anchors


def build_market_day_facts(
    window: DayWindow,
    *,
    skip_us: bool = False,
    finmind_token: str | None = None,
) -> MarketDayFacts:
    ensure_import_paths()
    lookback = list(window.lookback_days)
    start = lookback[0]
    end = window.trade_date

    taiex_rows: dict[str, dict[str, Any]] = {}
    try:
        taiex_rows = fetch_taiex_rows(start, end, token=finmind_token)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: FinMind TAIEX fetch failed: {exc}", file=sys.stderr)

    inst_rows: list[dict[str, Any]] = []
    try:
        inst_rows = fetch_institutional_rows(end, end, token=finmind_token)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: FinMind institutional fetch failed: {exc}", file=sys.stderr)

    tsmc_rows: dict[str, dict[str, Any]] = {}
    try:
        tsmc_rows = fetch_stock_day(TSM_ID, start, end, token=finmind_token)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: FinMind 2330 fetch failed: {exc}", file=sys.stderr)

    market = build_market_block(window.trade_date, window.prior_trade_date, taiex_rows)
    volume = build_volume_block(window.trade_date, lookback, taiex_rows)
    institutional = build_institutional_block(window.trade_date, inst_rows)
    technical = build_technical_block(window.trade_date, lookback, taiex_rows, market)
    tsmc = build_tsmc_block(window.trade_date, window.prior_trade_date, tsmc_rows)

    taiex_ret = market.get("day_return_pct")
    taiex_ret_f = float(taiex_ret) if isinstance(taiex_ret, (int, float)) else None
    tsmc_ret = tsmc.get("day_return_pct")
    tsmc_ret_f = float(tsmc_ret) if isinstance(tsmc_ret, (int, float)) else None

    if skip_us:
        us_block = empty_us_block(
            reason="skip_us",
            as_of=window.us_as_of,
            cutover_passed=window.us_cutover_passed,
        )
    else:
        try:
            from us_indices import build_us_day_block

            us_block = build_us_day_block(window.us_as_of)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: US index day fetch failed: {exc}", file=sys.stderr)
            us_block = empty_us_block(
                reason=f"fetch_error:{exc}",
                as_of=window.us_as_of,
                cutover_passed=window.us_cutover_passed,
            )
        us_block["as_of"] = window.us_as_of
        us_block["cutover"] = "05:30+08:00"
        us_block["cutover_passed"] = window.us_cutover_passed
        us_block = attach_us_alignment(
            us_block,
            taiex_day_return=taiex_ret_f,
            tsmc_day_return=tsmc_ret_f,
        )

    facts = MarketDayFacts(
        trade_date=window.trade_date,
        for_session=window.for_session,
        prior_trade_date=window.prior_trade_date,
        lookback_days=lookback,
        resolved_as_of=window.resolved_as_of,
        cutover_applied=window.cutover_applied,
        us_as_of=window.us_as_of,
        us_cutover_passed=window.us_cutover_passed,
        market=market,
        volume=volume,
        institutional=institutional,
        technical=technical,
        tsmc=tsmc,
        us=us_block,
    )
    facts.bias_hint = compute_bias_hint(
        market=market,
        volume=volume,
        institutional=institutional,
        technical=technical,
        us=us_block,
    )
    facts.anchors = build_anchors(facts)
    return facts


def write_facts_json(facts: MarketDayFacts, path: Path | None = None) -> Path:
    out = path or facts_path(facts.trade_date)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(facts.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def facts_summary_for_prompt(facts: MarketDayFacts) -> str:
    us_note = (
        "台北 05:30 後用最新美股；05:30 前仍用前一日美股（as_of 見下）"
    )
    lines = [
        f"trade_date（籌碼日）：{facts.trade_date}",
        f"for_session（服務開盤日）：{facts.for_session}",
        f"prior_trade_date：{facts.prior_trade_date}",
        f"cutover_applied={facts.cutover_applied}",
        f"us_as_of={facts.us_as_of}（cutover_passed={facts.us_cutover_passed}；{us_note}）",
        f"bias_hint={facts.bias_hint}（{BIAS_LABEL.get(facts.bias_hint, facts.bias_hint)}）",
        "",
        "=== 大盤 ===",
        json.dumps(facts.market, ensure_ascii=False, indent=2),
        "",
        "=== 量能 ===",
        json.dumps(facts.volume, ensure_ascii=False, indent=2),
        "",
        "=== 三大法人（全市場；單位 shares）===",
        json.dumps(facts.institutional, ensure_ascii=False, indent=2),
        "",
        "=== 技術錨點 ===",
        json.dumps(facts.technical, ensure_ascii=False, indent=2),
        "",
        "=== 台積電 2330 ===",
        json.dumps(facts.tsmc, ensure_ascii=False, indent=2),
        "",
        "=== 美股日事實（那指／費半；依 us_as_of；數字不可改寫）===",
        json.dumps(facts.us or {}, ensure_ascii=False, indent=2),
        "",
        "=== anchors ===",
        "\n".join(f"- {a}" for a in facts.anchors),
    ]
    return "\n".join(lines)


def resolve_window_or_fail(
    *,
    as_of: str | None = None,
    trade_date: str | None = None,
) -> DayWindow:
    import datetime as dt

    from day_window import TAIPEI_TZ

    now = None
    if as_of:
        raw = as_of.strip()
        if "T" in raw or " " in raw:
            now = dt.datetime.fromisoformat(raw.replace(" ", "T"))
            if now.tzinfo is None:
                now = now.replace(tzinfo=TAIPEI_TZ)
        else:
            day = dt.date.fromisoformat(raw)
            now = dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=TAIPEI_TZ)
    return resolve_day_window(now, trade_date=trade_date)
