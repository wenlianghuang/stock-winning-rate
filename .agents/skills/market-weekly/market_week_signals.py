"""Deterministic market-week facts: TAIEX + leaders + TWSE sectors + news titles."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sector_indices import (
    TAIEX_INDEX_NAME,
    fetch_sector_closes_for_days,
    rank_sector_week_returns,
    week_return_pct,
)
from week_window import WeekWindow, resolve_week_window

LEADER_WEIGHT_HINT: dict[str, float] = {
    "2330": 0.30,
    "2317": 0.05,
    "2454": 0.04,
    "2308": 0.03,
    "2382": 0.03,
    "2881": 0.02,
    "2882": 0.02,
    "0050": 0.08,
    "006208": 0.05,
}

# TWSE 類指數名 → Yahoo 新聞代表股（非 theme／持倉宇宙）
SECTOR_NEWS_PROXIES: dict[str, tuple[str, str]] = {
    "半導體類指數": ("2330", "台積電"),
    "電子工業類指數": ("2317", "鴻海"),
    "電腦及週邊設備類指數": ("2382", "廣達"),
    "電子零組件類指數": ("2308", "台達電"),
    "光電類指數": ("2409", "友達"),
    "通信網路類指數": ("2412", "中華電"),
    "金融保險類指數": ("2881", "富邦金"),
    "航運類指數": ("2603", "長榮"),
    "鋼鐵類指數": ("2002", "中鋼"),
    "食品類指數": ("1216", "統一"),
    "塑膠類指數": ("1301", "台塑"),
    "汽車類指數": ("2207", "和泰車"),
    "建材營造類指數": ("2501", "國建"),
    "生技醫療類指數": ("6547", "創惟"),
    "化學類指數": ("1303", "南亞"),
    "化學生技醫療類指數": ("1303", "南亞"),
    "其他電子類指數": ("2454", "聯發科"),
    "電子通路類指數": ("3036", "文曄"),
    "資訊服務類指數": ("2471", "資通"),
    "綠能環保類指數": ("1519", "華城"),
    "數位雲端類指數": ("2382", "廣達"),
    "觀光餐旅類指數": ("2707", "晶華"),
    "油電燃氣類指數": ("6505", "台塑化"),
    "貿易百貨類指數": ("2912", "統一超"),
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_import_paths() -> None:
    root = project_root()
    for sub in (
        "ui",
        ".agents/skills/tw-stock-report",
        ".agents/skills/portfolio-gate",
        ".agents/skills/market-weekly",
    ):
        text = str(root / sub)
        if text not in sys.path:
            sys.path.insert(0, text)


def market_output_dir(week_end: str) -> Path:
    return project_root() / "reports" / "market" / week_end


def facts_path(week_end: str) -> Path:
    return market_output_dir(week_end) / "tw_market_weekly.facts.json"


def default_universe_path() -> Path:
    return (
        project_root()
        / ".agents"
        / "skills"
        / "portfolio-gate"
        / "portfolio_universe.json"
    )


def load_leader_universe(universe_path: Path | None = None) -> list[dict[str, str]]:
    path = universe_path or default_universe_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, str]] = []
    for entry in data.get("candidates") or []:
        category = str(entry.get("category") or "")
        if category in {"blue_chip", "broad_etf"}:
            out.append(
                {
                    "id": str(entry["id"]).strip(),
                    "name": str(entry.get("name") or entry["id"]).strip(),
                    "category": category,
                }
            )
    return out


SEMICONDUCTOR_SECTOR_NAME = "半導體類指數"


@dataclass
class MarketWeekFacts:
    week_start: str
    week_end: str
    trading_days: list[str]
    resolved_as_of: str
    cutover_applied: bool
    market: dict[str, Any] = field(default_factory=dict)
    leaders: dict[str, Any] = field(default_factory=dict)
    sectors: dict[str, Any] = field(default_factory=dict)
    us: dict[str, Any] = field(default_factory=dict)
    news_titles: list[str] = field(default_factory=list)
    news_items: list[dict[str, Any]] = field(default_factory=list)
    news_section: str = ""
    anchors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def alignment_label(left: float | None, right: float | None) -> str:
    """Same-sign → 一致; opposite-sign → 背離; missing → unavailable."""
    if left is None or right is None:
        return "unavailable"
    if left == 0 or right == 0:
        # Zero treated as neutral: same non-negative / non-positive side → 一致
        if left == 0 and right == 0:
            return "一致"
        if (left >= 0 and right >= 0) or (left <= 0 and right <= 0):
            return "一致"
        return "背離"
    if (left > 0 and right > 0) or (left < 0 and right < 0):
        return "一致"
    return "背離"


def sector_week_return(
    sectors: dict[str, Any],
    name: str,
) -> float | None:
    for bucket in ("all", "strong", "weak"):
        for item in sectors.get(bucket) or []:
            if str(item.get("name") or "") == name:
                value = item.get("week_return_pct")
                if isinstance(value, (int, float)):
                    return float(value)
    stored = sectors.get("semiconductor_week_return_pct")
    if name == SEMICONDUCTOR_SECTOR_NAME and isinstance(stored, (int, float)):
        return float(stored)
    return None


def attach_us_alignment(
    us_block: dict[str, Any],
    *,
    taiex_week_return: float | None,
    tw_semi_week_return: float | None,
) -> dict[str, Any]:
    """Mutate/return us block with alignment + gaps vs TW."""
    indices = us_block.get("indices") or {}
    ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else None
    sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else None
    ixic_ret = ixic.get("week_return_pct") if ixic else None
    sox_ret = sox.get("week_return_pct") if sox else None
    if not isinstance(ixic_ret, (int, float)):
        ixic_ret = None
    else:
        ixic_ret = float(ixic_ret)
    if not isinstance(sox_ret, (int, float)):
        sox_ret = None
    else:
        sox_ret = float(sox_ret)

    gap_ixic = (
        round(ixic_ret - taiex_week_return, 2)
        if ixic_ret is not None and taiex_week_return is not None
        else None
    )
    gap_sox = (
        round(sox_ret - tw_semi_week_return, 2)
        if sox_ret is not None and tw_semi_week_return is not None
        else None
    )
    us_block["tw_semi_week_return_pct"] = tw_semi_week_return
    us_block["alignment"] = {
        "ixic_vs_taiex": alignment_label(ixic_ret, taiex_week_return),
        "sox_vs_tw_semi": alignment_label(sox_ret, tw_semi_week_return),
    }
    us_block["gaps"] = {
        "ixic_minus_taiex_pct": gap_ixic,
        "sox_minus_tw_semi_pct": gap_sox,
    }
    usable = ixic_ret is not None or sox_ret is not None
    us_block["available"] = bool(usable and us_block.get("available", True))
    return us_block


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


def fetch_taiex_closes(
    start_date: str,
    end_date: str,
    *,
    token: str | None = None,
) -> dict[str, float]:
    ensure_import_paths()
    import os

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
    closes: dict[str, float] = {}
    for day, row in rows.items():
        close = _to_float(row.get("close"))
        if close is not None:
            closes[day] = close
    return closes


def build_market_block(
    trading_days: list[str],
    taiex_closes: dict[str, float],
    *,
    sector_day_closes: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    if not trading_days:
        return {}
    first_day, last_day = trading_days[0], trading_days[-1]
    # Prefer FinMind TAIEX; fall back to TWSE MI_INDEX name
    first = taiex_closes.get(first_day)
    last = taiex_closes.get(last_day)
    if (first is None or last is None) and sector_day_closes:
        first = (sector_day_closes.get(first_day) or {}).get(TAIEX_INDEX_NAME, first)
        last = (sector_day_closes.get(last_day) or {}).get(TAIEX_INDEX_NAME, last)

    week_ret = week_return_pct(first, last) if first and last else None
    series = [taiex_closes[d] for d in trading_days if d in taiex_closes]
    if not series and sector_day_closes:
        series = [
            (sector_day_closes.get(d) or {}).get(TAIEX_INDEX_NAME)
            for d in trading_days
        ]
        series = [v for v in series if v is not None]

    high = max(series) if series else None
    low = min(series) if series else None
    position = None
    if high is not None and low is not None and last is not None and high != low:
        position = round((last - low) / (high - low) * 100, 1)

    return {
        "first_day": first_day,
        "last_day": last_day,
        "first_close": first,
        "last_close": last,
        "week_return_pct": week_ret,
        "week_high": high,
        "week_low": low,
        "close_in_week_range_pct": position,
    }


def _leader_week_stats(
    stock_id: str,
    name: str,
    category: str,
    trading_days: list[str],
    taiex_week_return: float | None,
) -> dict[str, Any] | None:
    ensure_import_paths()
    from chip_tables import load_csv_row, load_history_rows

    week_end = trading_days[-1]
    csv_path = project_root() / "reports" / "stock" / week_end / f"tw_stock_{stock_id}.csv"
    if not csv_path.exists():
        return None
    row = load_csv_row(csv_path)
    if row is None:
        return None
    history = load_history_rows(csv_path)
    closes_by_day: dict[str, float] = {}
    for hist in history:
        day = str(hist.get("日期") or "").strip()
        close = _to_float(hist.get("收盤價"))
        if day and close is not None:
            closes_by_day[day] = close
    # Snapshot may be latest day
    snap_day = str(row.get("日期") or week_end).strip()
    snap_close = _to_float(row.get("收盤價"))
    if snap_day and snap_close is not None:
        closes_by_day[snap_day] = snap_close

    first_day, last_day = trading_days[0], trading_days[-1]
    first = closes_by_day.get(first_day)
    last = closes_by_day.get(last_day)
    if first is None or last is None:
        return None
    ret = week_return_pct(first, last)
    if ret is None:
        return None
    excess = None if taiex_week_return is None else round(ret - taiex_week_return, 2)
    weight = LEADER_WEIGHT_HINT.get(stock_id, 0.02)
    contribution = None if excess is None else round(excess * weight, 3)
    return {
        "stock_id": stock_id,
        "name": name,
        "category": category,
        "week_return_pct": ret,
        "excess_vs_taiex_pct": excess,
        "weight_hint": weight,
        "contribution_score": contribution,
        "first_close": first,
        "last_close": last,
    }


def build_leaders_block(
    trading_days: list[str],
    taiex_week_return: float | None,
    *,
    universe_path: Path | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    leaders = load_leader_universe(universe_path)
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for entry in leaders:
        stats = _leader_week_stats(
            entry["id"],
            entry["name"],
            entry["category"],
            trading_days,
            taiex_week_return,
        )
        if stats is None:
            missing.append(entry["id"])
            continue
        rows.append(stats)

    by_contrib = sorted(
        rows,
        key=lambda r: (
            r["contribution_score"]
            if r["contribution_score"] is not None
            else r["week_return_pct"]
        ),
        reverse=True,
    )
    return {
        "top": by_contrib[:top_n],
        "bottom": list(reversed(by_contrib[-top_n:])) if by_contrib else [],
        "available": len(rows),
        "missing_ids": missing,
    }


def _news_sample_sources(
    leaders: dict[str, Any],
    sectors: dict[str, Any],
) -> list[dict[str, str]]:
    """Build deduped Yahoo news fetch targets: leaders + sector proxies."""
    samples: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    def _add(
        stock_id: str,
        name: str,
        *,
        related_sector: str = "",
    ) -> None:
        sid = stock_id.strip()
        if not sid or sid in seen_ids:
            return
        seen_ids.add(sid)
        samples.append(
            {
                "stock_id": sid,
                "name": name.strip() or sid,
                "related_sector": related_sector.strip(),
            }
        )

    for item in (leaders.get("top") or [])[:3]:
        _add(str(item.get("stock_id") or ""), str(item.get("name") or ""))
    for item in (leaders.get("bottom") or [])[:2]:
        _add(str(item.get("stock_id") or ""), str(item.get("name") or ""))

    for bucket in ("strong", "weak"):
        for item in (sectors.get(bucket) or [])[:2]:
            sector_name = str(item.get("name") or "").strip()
            proxy = SECTOR_NEWS_PROXIES.get(sector_name)
            if proxy:
                _add(proxy[0], proxy[1], related_sector=sector_name)

    if not samples:
        _add("2330", "台積電")
        _add("0050", "元大台灣50")
    return samples


def collect_news(
    leaders: dict[str, Any],
    sectors: dict[str, Any],
    week_end: str,
    trading_days: list[str],
) -> tuple[list[dict[str, Any]], list[str], str]:
    """Return (news_items, news_titles, news_section) aligned to leaders/sectors."""
    ensure_import_paths()
    from stock_news import YahooStockNewsClient, filter_recent_articles

    hours = max(24 * max(len(trading_days), 5), 120)
    client = YahooStockNewsClient()
    samples = _news_sample_sources(leaders, sectors)
    items: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    section_blocks: list[str] = []

    for sample in samples:
        sid = sample["stock_id"]
        name = sample["name"]
        related = sample.get("related_sector") or ""
        try:
            articles = filter_recent_articles(
                client.fetch_articles(sid),
                lookback_hours=hours,
                max_items=8,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: news {sid} failed: {exc}", file=sys.stderr)
            continue
        if not articles:
            continue

        header = f"### {name}（{sid}）"
        if related:
            header += f"｜關聯類股：{related}"
        lines = [header, ""]
        kept = 0
        for art in articles:
            title = (art.title or "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            date_str = (
                art.published.strftime("%Y-%m-%d")
                if art.published is not None
                else ""
            )
            publisher = (art.publisher or "").strip()
            items.append(
                {
                    "title": title,
                    "date": date_str or None,
                    "stock_id": sid,
                    "name": name,
                    "related_sector": related or None,
                    "publisher": publisher or None,
                }
            )
            when = date_str or "未知日期"
            lines.append(f"- [{when}] {title}")
            if related:
                lines.append(f"  （對帳類股：{related}）")
            kept += 1
            if kept >= 4 or len(items) >= 20:
                break
        if kept:
            section_blocks.append("\n".join(lines))
        if len(items) >= 20:
            break

    items = items[:20]
    titles = [str(it["title"]) for it in items]
    section = "\n\n".join(section_blocks).strip()
    return items, titles, section


def build_anchors(facts: MarketWeekFacts) -> list[str]:
    anchors: list[str] = []
    m = facts.market
    if m.get("week_return_pct") is not None:
        anchors.append(f"大盤週報酬 {m['week_return_pct']}%")
    for item in (facts.leaders.get("top") or [])[:2]:
        anchors.append(
            f"權值 {item['name']}({item['stock_id']}) 週報酬 {item['week_return_pct']}%"
        )
    for item in (facts.sectors.get("strong") or [])[:2]:
        excess = item.get("excess_vs_taiex_pct")
        anchors.append(
            f"強勢類股 {item['name']} 超額 {excess if excess is not None else item['week_return_pct']}%"
        )
    for item in (facts.sectors.get("weak") or [])[:2]:
        excess = item.get("excess_vs_taiex_pct")
        anchors.append(
            f"弱勢類股 {item['name']} 超額 {excess if excess is not None else item['week_return_pct']}%"
        )
    us = facts.us or {}
    indices = us.get("indices") or {}
    ixic = indices.get("IXIC") if isinstance(indices.get("IXIC"), dict) else None
    sox = indices.get("SOX") if isinstance(indices.get("SOX"), dict) else None
    if ixic and ixic.get("week_return_pct") is not None:
        anchors.append(f"那指週報酬 {ixic['week_return_pct']}%")
    if sox and sox.get("week_return_pct") is not None:
        anchors.append(f"費半週報酬 {sox['week_return_pct']}%")
    alignment = us.get("alignment") or {}
    sox_align = alignment.get("sox_vs_tw_semi")
    if sox_align and sox_align != "unavailable":
        anchors.append(f"費半vs半導體 {sox_align}")
    ixic_align = alignment.get("ixic_vs_taiex")
    if ixic_align and ixic_align != "unavailable":
        anchors.append(f"那指vs大盤 {ixic_align}")
    return anchors


def empty_us_block(*, reason: str = "skipped") -> dict[str, Any]:
    return {
        "source": "yahoo_finance",
        "calendar_start": None,
        "calendar_end": None,
        "available": False,
        "skipped": True,
        "skip_reason": reason,
        "indices": {"IXIC": None, "SOX": None},
        "alignment": {
            "ixic_vs_taiex": "unavailable",
            "sox_vs_tw_semi": "unavailable",
        },
        "gaps": {
            "ixic_minus_taiex_pct": None,
            "sox_minus_tw_semi_pct": None,
        },
        "tw_semi_week_return_pct": None,
    }


def build_market_week_facts(
    window: WeekWindow,
    *,
    skip_news: bool = False,
    skip_sectors: bool = False,
    skip_us: bool = False,
    finmind_token: str | None = None,
    universe_path: Path | None = None,
) -> MarketWeekFacts:
    ensure_import_paths()
    trading_days = list(window.trading_days)
    cache_dir = market_output_dir(window.week_end) / "cache"

    sector_day_closes: dict[str, dict[str, float]] = {}
    if not skip_sectors:
        try:
            sector_day_closes = fetch_sector_closes_for_days(
                trading_days,
                cache_dir=cache_dir,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: TWSE sector fetch failed: {exc}", file=sys.stderr)

    taiex_closes: dict[str, float] = {}
    try:
        # pad one extra lookback day before week for safety
        taiex_closes = fetch_taiex_closes(
            trading_days[0],
            trading_days[-1],
            token=finmind_token,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: FinMind TAIEX fetch failed: {exc}", file=sys.stderr)

    market = build_market_block(
        trading_days,
        taiex_closes,
        sector_day_closes=sector_day_closes,
    )
    taiex_week_return = market.get("week_return_pct")
    if isinstance(taiex_week_return, (int, float)):
        taiex_ret: float | None = float(taiex_week_return)
    else:
        taiex_ret = None

    leaders = build_leaders_block(
        trading_days,
        taiex_ret,
        universe_path=universe_path,
    )
    sectors = (
        rank_sector_week_returns(
            sector_day_closes,
            trading_days,
            taiex_week_return=taiex_ret,
        )
        if sector_day_closes
        else {
            "universe": "twse_industry_indices",
            "strong": [],
            "weak": [],
            "all": [],
        }
    )

    tw_semi = sector_week_return(sectors, SEMICONDUCTOR_SECTOR_NAME)
    if tw_semi is not None:
        sectors = dict(sectors)
        sectors["semiconductor_week_return_pct"] = tw_semi

    news_titles: list[str] = []
    news_items: list[dict[str, Any]] = []
    news_section = ""
    if not skip_news:
        news_items, news_titles, news_section = collect_news(
            leaders,
            sectors,
            window.week_end,
            trading_days,
        )

    us_block: dict[str, Any]
    if skip_us:
        us_block = empty_us_block(reason="skip_us")
        us_block["tw_semi_week_return_pct"] = tw_semi
    else:
        try:
            ensure_import_paths()
            from us_indices import build_us_week_block

            us_block = build_us_week_block(window.week_monday, window.week_friday)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: US index week fetch failed: {exc}", file=sys.stderr)
            us_block = empty_us_block(reason=f"fetch_error:{exc}")
        us_block = attach_us_alignment(
            us_block,
            taiex_week_return=taiex_ret,
            tw_semi_week_return=tw_semi,
        )

    facts = MarketWeekFacts(
        week_start=window.week_start,
        week_end=window.week_end,
        trading_days=trading_days,
        resolved_as_of=window.resolved_as_of,
        cutover_applied=window.cutover_applied,
        market=market,
        leaders=leaders,
        sectors=sectors,
        us=us_block,
        news_titles=news_titles,
        news_items=news_items,
        news_section=news_section,
    )
    facts.anchors = build_anchors(facts)
    return facts


def write_facts_json(facts: MarketWeekFacts, path: Path | None = None) -> Path:
    out = path or facts_path(facts.week_end)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(facts.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def facts_summary_for_prompt(facts: MarketWeekFacts) -> str:
    lines: list[str] = [
        f"報告週：{facts.week_start}～{facts.week_end}",
        f"交易日：{', '.join(facts.trading_days)}",
        f"cutover_applied={facts.cutover_applied}",
        "",
        "=== 大盤 ===",
        json.dumps(facts.market, ensure_ascii=False, indent=2),
        "",
        "=== 權值 top ===",
        json.dumps(facts.leaders.get("top") or [], ensure_ascii=False, indent=2),
        "",
        "=== 權值 bottom ===",
        json.dumps(facts.leaders.get("bottom") or [], ensure_ascii=False, indent=2),
        "",
        "=== 強勢類股 ===",
        json.dumps(facts.sectors.get("strong") or [], ensure_ascii=False, indent=2),
        "",
        "=== 弱勢類股 ===",
        json.dumps(facts.sectors.get("weak") or [], ensure_ascii=False, indent=2),
        "",
        "=== 美股週事實（那指／費半；數字不可改寫）===",
        json.dumps(facts.us or {}, ensure_ascii=False, indent=2),
        "",
        "=== 對帳新聞 items（標題須原文引用）===",
        json.dumps(facts.news_items or [], ensure_ascii=False, indent=2),
        "",
        "=== anchors ===",
        "\n".join(f"- {a}" for a in facts.anchors),
    ]
    return "\n".join(lines)


def resolve_window_or_fail(
    *,
    as_of: str | None = None,
    week_end: str | None = None,
) -> WeekWindow:
    import datetime as dt

    from week_window import TAIPEI_TZ

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
    return resolve_week_window(now, week_end=week_end)
