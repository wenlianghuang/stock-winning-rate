"""Fetch TWSE official industry sector index closes (MI_INDEX type=IND)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
USER_AGENT = "Mozilla/5.0 (compatible; antigravity-agent/market-weekly)"

# Official TWSE industry 「類指數」names (price index table). Not theme/personal.
SECTOR_INDEX_NAMES: tuple[str, ...] = (
    "水泥類指數",
    "食品類指數",
    "塑膠類指數",
    "紡織纖維類指數",
    "電機機械類指數",
    "電器電纜類指數",
    "化學生技醫療類指數",
    "化學類指數",
    "生技醫療類指數",
    "玻璃陶瓷類指數",
    "造紙類指數",
    "鋼鐵類指數",
    "橡膠類指數",
    "汽車類指數",
    "電子工業類指數",
    "半導體類指數",
    "電腦及週邊設備類指數",
    "光電類指數",
    "通信網路類指數",
    "電子零組件類指數",
    "電子通路類指數",
    "資訊服務類指數",
    "其他電子類指數",
    "建材營造類指數",
    "航運類指數",
    "觀光餐旅類指數",
    "金融保險類指數",
    "貿易百貨類指數",
    "油電燃氣類指數",
    "綠能環保類指數",
    "數位雲端類指數",
    "運動休閒類指數",
    "居家生活類指數",
    "其他類指數",
)

SECTOR_NAME_SET = frozenset(SECTOR_INDEX_NAMES)
TAIEX_INDEX_NAME = "發行量加權股價指數"

_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SectorClose:
    name: str
    close: float


def _parse_number(raw: str) -> float | None:
    text = _HTML_TAG_RE.sub("", str(raw or "")).strip().replace(",", "")
    if not text or text in {"-", "---"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_yyyymmdd(iso_date: str) -> str:
    return iso_date.replace("-", "")


def parse_mi_index_payload(payload: dict[str, Any]) -> dict[str, float]:
    """Return index_name → close from a MI_INDEX JSON payload."""
    if str(payload.get("stat", "")).upper() not in {"OK", ""}:
        # empty stat sometimes on older shapes; require tables/data
        if not payload.get("tables") and "data1" not in payload:
            return {}

    closes: dict[str, float] = {}

    tables = payload.get("tables")
    if isinstance(tables, list):
        for table in tables:
            title = str(table.get("title") or "")
            # Prefer 證券交易所 price-index table; skip 報酬指數 / 槓桿
            if "報酬" in title or "兩倍" in title or "反向" in title:
                continue
            if "跨市場" in title or "指數公司" in title:
                continue
            rows = table.get("data") or []
            for row in rows:
                if not row or len(row) < 2:
                    continue
                name = str(row[0]).strip()
                close = _parse_number(row[1])
                if close is None:
                    continue
                closes[name] = close
        return closes

    # Legacy shape: data1 + fields1
    rows = payload.get("data1") or []
    for row in rows:
        if not row or len(row) < 2:
            continue
        name = str(row[0]).strip()
        close = _parse_number(row[1])
        if close is not None:
            closes[name] = close
    return closes


def fetch_mi_index_closes(
    trade_date: str,
    *,
    session: requests.Session | None = None,
    sleep_sec: float = 0.35,
) -> dict[str, float]:
    """Fetch one day of index closes. Keys are TWSE index display names."""
    http = session or requests.Session()
    response = http.get(
        TWSE_MI_INDEX_URL,
        params={
            "response": "json",
            "date": _date_yyyymmdd(trade_date),
            "type": "IND",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if sleep_sec > 0:
        time.sleep(sleep_sec)
    if str(payload.get("stat", "")).upper() == "OK" or payload.get("tables"):
        return parse_mi_index_payload(payload)
    return {}


def fetch_sector_closes_for_days(
    trading_days: list[str],
    *,
    session: requests.Session | None = None,
    cache_dir: Path | None = None,
) -> dict[str, dict[str, float]]:
    """Return {date: {index_name: close}} for each trading day."""
    http = session or requests.Session()
    out: dict[str, dict[str, float]] = {}
    for day in trading_days:
        cached: Path | None = None
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached = cache_dir / f"mi_index_{day}.json"
            if cached.exists():
                payload = json.loads(cached.read_text(encoding="utf-8"))
                out[day] = parse_mi_index_payload(payload)
                continue
        response = http.get(
            TWSE_MI_INDEX_URL,
            params={
                "response": "json",
                "date": _date_yyyymmdd(day),
                "type": "IND",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if cache_dir is not None and cached is not None:
            cached.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        out[day] = parse_mi_index_payload(payload)
        time.sleep(0.35)
    return out


def week_return_pct(first_close: float, last_close: float) -> float | None:
    if first_close == 0:
        return None
    return round((last_close - first_close) / first_close * 100, 2)


def rank_sector_week_returns(
    closes_by_day: dict[str, dict[str, float]],
    trading_days: list[str],
    *,
    taiex_week_return: float | None,
    top_n: int = 5,
) -> dict[str, Any]:
    """Compute sector week returns vs TAIEX; return strong/weak lists."""
    if len(trading_days) < 2:
        return {
            "universe": "twse_industry_indices",
            "strong": [],
            "weak": [],
            "all": [],
        }

    first_day = trading_days[0]
    last_day = trading_days[-1]
    first_map = closes_by_day.get(first_day) or {}
    last_map = closes_by_day.get(last_day) or {}

    rows: list[dict[str, Any]] = []
    for name in SECTOR_INDEX_NAMES:
        first = first_map.get(name)
        last = last_map.get(name)
        if first is None or last is None:
            continue
        ret = week_return_pct(first, last)
        if ret is None:
            continue
        excess = None if taiex_week_return is None else round(ret - taiex_week_return, 2)
        rows.append(
            {
                "index_id": name,
                "name": name,
                "week_return_pct": ret,
                "excess_vs_taiex_pct": excess,
                "first_close": first,
                "last_close": last,
            }
        )

    by_excess = sorted(
        rows,
        key=lambda r: (
            r["excess_vs_taiex_pct"]
            if r["excess_vs_taiex_pct"] is not None
            else r["week_return_pct"]
        ),
        reverse=True,
    )
    return {
        "universe": "twse_industry_indices",
        "strong": by_excess[:top_n],
        "weak": list(reversed(by_excess[-top_n:])) if by_excess else [],
        "all": by_excess,
    }
