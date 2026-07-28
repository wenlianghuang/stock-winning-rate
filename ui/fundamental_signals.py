"""Fundamental metrics for portfolio scoring (FinMind + on-disk cache).

Metrics (v1):
- PER / PBR / dividend yield from ``TaiwanStockPER``
- Latest month revenue YoY % from ``TaiwanStockMonthRevenue``

Results are cached under ``reports/fundamentals/{trade_date}.json`` so
portfolio-build / portfolio-gate do not re-hit FinMind on every run.
Missing or failed fetches are soft: chip-only scoring still works.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FUNDAMENTALS_ROOT = PROJECT_ROOT / "reports" / "fundamentals"


@dataclass
class FundamentalFacts:
    stock_id: str
    trade_date: str
    per: float | None = None
    pbr: float | None = None
    dividend_yield: float | None = None
    revenue_yoy_pct: float | None = None
    available: bool = False
    source: str = "none"  # cache | live | none
    tags: list[str] = field(default_factory=list)

    def valuation_bucket(self) -> str:
        """Coarse valuation bucket for selection diversity."""
        if not self.available or self.per is None:
            return "unknown"
        if self.per <= 0:
            return "distressed"
        if self.per < 15:
            return "value"
        if self.per < 30:
            return "blend"
        return "growth_val"


def cache_path(trade_date: str) -> Path:
    return FUNDAMENTALS_ROOT / f"{trade_date}.json"


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _finmind_client():
    from fetch_chip_report import FinMindClient

    token = os.environ.get("FINMIND_TOKEN", "").strip()
    return FinMindClient(token=token)


def _latest_per_row(
    rows: list[dict[str, Any]], trade_date: str
) -> dict[str, Any] | None:
    eligible = [row for row in rows if str(row.get("date", "")) <= trade_date]
    if not eligible:
        return None
    eligible.sort(key=lambda row: str(row.get("date", "")))
    return eligible[-1]


def _revenue_yoy_from_rows(rows: list[dict[str, Any]]) -> float | None:
    """Compute YoY from FinMind month-revenue rows (revenue + year/month)."""
    keyed: dict[tuple[int, int], float] = {}
    for row in rows:
        revenue = _safe_float(row.get("revenue"))
        if revenue is None:
            continue
        year = row.get("revenue_year") or row.get("year")
        month = row.get("revenue_month") or row.get("month")
        try:
            y = int(year)
            m = int(month)
        except (TypeError, ValueError):
            # Fallback: parse date
            raw = str(row.get("date", ""))
            if len(raw) >= 7:
                try:
                    y = int(raw[:4])
                    m = int(raw[5:7])
                except ValueError:
                    continue
            else:
                continue
        keyed[(y, m)] = revenue

    if not keyed:
        return None
    latest_y, latest_m = max(keyed)
    current = keyed[(latest_y, latest_m)]
    prior = keyed.get((latest_y - 1, latest_m))
    if prior is None or prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100.0, 2)


def build_fundamental_tags(facts: FundamentalFacts) -> list[str]:
    tags: list[str] = []
    if facts.dividend_yield is not None and facts.dividend_yield >= 4.0:
        tags.append(f"殖利率偏高（約 {facts.dividend_yield:.1f}%）")
    elif facts.dividend_yield is not None and facts.dividend_yield >= 2.5:
        tags.append(f"具息收（約 {facts.dividend_yield:.1f}%）")

    if facts.per is not None and 0 < facts.per < 15:
        tags.append(f"本益比相對合理（約 {facts.per:.1f}）")
    elif facts.per is not None and facts.per >= 40:
        tags.append(f"本益比偏高（約 {facts.per:.1f}）")
    elif facts.per is not None and facts.per <= 0:
        tags.append("本益比負值／虧損股")

    if facts.pbr is not None and 0 < facts.pbr < 1.2:
        tags.append(f"股價淨值比偏低（約 {facts.pbr:.2f}）")
    elif facts.pbr is not None and facts.pbr >= 5:
        tags.append(f"股價淨值比偏高（約 {facts.pbr:.2f}）")

    if facts.revenue_yoy_pct is not None and facts.revenue_yoy_pct >= 15:
        tags.append(f"月營收年增偏強（約 {facts.revenue_yoy_pct:.1f}%）")
    elif facts.revenue_yoy_pct is not None and facts.revenue_yoy_pct >= 5:
        tags.append(f"月營收年增轉正（約 {facts.revenue_yoy_pct:.1f}%）")
    elif facts.revenue_yoy_pct is not None and facts.revenue_yoy_pct <= -10:
        tags.append(f"月營收年增偏弱（約 {facts.revenue_yoy_pct:.1f}%）")
    return tags


def fetch_fundamental_live(
    client,
    stock_id: str,
    trade_date: str,
) -> FundamentalFacts:
    """Fetch PER/PBR/yield + month-revenue YoY for one stock (2 API calls)."""
    facts = FundamentalFacts(stock_id=stock_id, trade_date=trade_date, source="live")
    try:
        trade = date.fromisoformat(trade_date)
    except ValueError:
        return facts

    per_start = (trade - timedelta(days=21)).isoformat()
    try:
        per_rows = client.fetch_dataset(
            "TaiwanStockPER",
            stock_id=stock_id,
            start_date=per_start,
            end_date=trade_date,
        )
        latest = _latest_per_row(per_rows, trade_date)
        if latest:
            facts.per = _safe_float(latest.get("PER", latest.get("per")))
            facts.pbr = _safe_float(latest.get("PBR", latest.get("pbr")))
            facts.dividend_yield = _safe_float(
                latest.get("dividend_yield")
                or latest.get("DividendYield")
                or latest.get("dividendYield")
            )
    except Exception:
        pass

    rev_start = (trade - timedelta(days=450)).isoformat()
    try:
        rev_rows = client.fetch_dataset(
            "TaiwanStockMonthRevenue",
            stock_id=stock_id,
            start_date=rev_start,
            end_date=trade_date,
        )
        facts.revenue_yoy_pct = _revenue_yoy_from_rows(rev_rows)
    except Exception:
        pass

    facts.available = any(
        value is not None
        for value in (facts.per, facts.pbr, facts.dividend_yield, facts.revenue_yoy_pct)
    )
    facts.tags = build_fundamental_tags(facts) if facts.available else []
    return facts


def load_fundamentals_cache(trade_date: str) -> dict[str, FundamentalFacts]:
    path = cache_path(trade_date)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stocks = raw.get("stocks") if isinstance(raw, dict) else None
    if not isinstance(stocks, dict):
        return {}
    out: dict[str, FundamentalFacts] = {}
    for sid, payload in stocks.items():
        if not isinstance(payload, dict):
            continue
        facts = FundamentalFacts(
            stock_id=str(sid),
            trade_date=trade_date,
            per=_safe_float(payload.get("per")),
            pbr=_safe_float(payload.get("pbr")),
            dividend_yield=_safe_float(payload.get("dividend_yield")),
            revenue_yoy_pct=_safe_float(payload.get("revenue_yoy_pct")),
            available=bool(payload.get("available", True)),
            source="cache",
        )
        facts.available = facts.available and any(
            value is not None
            for value in (
                facts.per,
                facts.pbr,
                facts.dividend_yield,
                facts.revenue_yoy_pct,
            )
        )
        facts.tags = build_fundamental_tags(facts) if facts.available else []
        out[str(sid)] = facts
    return out


def save_fundamentals_cache(
    trade_date: str, facts_by_id: dict[str, FundamentalFacts]
) -> Path:
    FUNDAMENTALS_ROOT.mkdir(parents=True, exist_ok=True)
    existing = load_fundamentals_cache(trade_date)
    existing.update(facts_by_id)
    payload = {
        "trade_date": trade_date,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "stocks": {
            sid: {
                "per": facts.per,
                "pbr": facts.pbr,
                "dividend_yield": facts.dividend_yield,
                "revenue_yoy_pct": facts.revenue_yoy_pct,
                "available": facts.available,
            }
            for sid, facts in sorted(existing.items())
        },
    }
    path = cache_path(trade_date)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_or_fetch_fundamentals(
    stock_ids: list[str],
    trade_date: str,
    *,
    force_refresh: bool = False,
    sleep_sec: float = 0.35,
    skip_ids: set[str] | None = None,
) -> dict[str, FundamentalFacts]:
    """Return fundamentals for stock_ids, using cache first to save API quota."""
    skip = skip_ids or set()
    cached = {} if force_refresh else load_fundamentals_cache(trade_date)
    result: dict[str, FundamentalFacts] = {}
    missing: list[str] = []

    for sid in stock_ids:
        if sid in skip:
            continue
        if sid in cached and cached[sid].available and not force_refresh:
            result[sid] = cached[sid]
        else:
            missing.append(sid)

    if not missing:
        return result

    client = _finmind_client()
    fetched: dict[str, FundamentalFacts] = {}
    for sid in missing:
        facts = fetch_fundamental_live(client, sid, trade_date)
        fetched[sid] = facts
        result[sid] = facts
        time.sleep(max(0.0, sleep_sec))

    if fetched:
        save_fundamentals_cache(trade_date, {**cached, **fetched})
    return result


# Theme style hints for scoring weights.
_DEFENSIVE_THEMES = frozenset({"financials", "dividend", "telecom", "consumer"})
_GROWTH_THEMES = frozenset(
    {
        "ai",
        "semiconductor",
        "servers",
        "green_energy",
        "biotech",
        "memory",
    }
)
_CYCLICAL_THEMES = frozenset(
    {"pcb", "thermal", "shipping", "panel", "passive_components"}
)


def fundamental_score(
    facts: FundamentalFacts | None,
    *,
    themes: list[str] | None = None,
    defensive_bias: bool = False,
) -> int:
    """Map fundamentals to about [-12, +16] points for portfolio ranking."""
    if facts is None or not facts.available:
        return 0

    theme_set = set(themes or [])
    defensive = defensive_bias or bool(theme_set & _DEFENSIVE_THEMES)
    growth = bool(theme_set & _GROWTH_THEMES) and not defensive
    cyclical = bool(theme_set & _CYCLICAL_THEMES)

    score = 0
    dy = facts.dividend_yield
    per = facts.per
    pbr = facts.pbr
    yoy = facts.revenue_yoy_pct

    if defensive:
        if dy is not None:
            if dy >= 5:
                score += 8
            elif dy >= 3.5:
                score += 5
            elif dy >= 2:
                score += 2
            elif dy < 1:
                score -= 2
        if per is not None:
            if 8 <= per <= 18:
                score += 4
            elif 0 < per < 8:
                score += 2
            elif per > 35:
                score -= 4
            elif per <= 0:
                score -= 5
        if pbr is not None:
            if 0 < pbr <= 1.5:
                score += 2
            elif pbr >= 4:
                score -= 2
        if yoy is not None and yoy <= -20:
            score -= 3
    elif growth:
        if yoy is not None:
            if yoy >= 25:
                score += 8
            elif yoy >= 10:
                score += 5
            elif yoy >= 0:
                score += 1
            elif yoy <= -10:
                score -= 5
        if per is not None:
            if 0 < per <= 25:
                score += 2
            elif 25 < per <= 45 and yoy is not None and yoy >= 15:
                score += 1  # growth premium tolerated
            elif per > 60:
                score -= 3
            elif per <= 0:
                score -= 2
        if dy is not None and dy >= 3:
            score += 1  # still a mild plus
    else:
        # cyclical / general
        if yoy is not None:
            if yoy >= 15:
                score += 6
            elif yoy >= 0:
                score += 2
            elif yoy <= -15:
                score -= 5
        if per is not None:
            if 0 < per <= 20:
                score += 3
            elif per > 40:
                score -= 3
            elif per <= 0:
                score -= 4
        if dy is not None and dy >= 4:
            score += 2
        if cyclical and yoy is not None and yoy >= 20:
            score += 2

    return max(-12, min(16, score))


def attach_fundamentals_to_candidates(
    candidates: list,
    trade_date: str,
    *,
    force_refresh: bool = False,
    sleep_sec: float = 0.35,
) -> dict[str, FundamentalFacts]:
    """Populate ``candidate.fundamentals`` for non-ETF names; return map."""
    stock_ids = [
        c.stock_id
        for c in candidates
        if getattr(c, "asset_class", "stock") != "etf"
    ]
    etf_ids = {
        c.stock_id
        for c in candidates
        if getattr(c, "asset_class", "stock") == "etf"
    }
    facts_map = load_or_fetch_fundamentals(
        stock_ids,
        trade_date,
        force_refresh=force_refresh,
        sleep_sec=sleep_sec,
        skip_ids=etf_ids,
    )
    for cand in candidates:
        fund = facts_map.get(cand.stock_id)
        cand.fundamentals = fund
    return facts_map
