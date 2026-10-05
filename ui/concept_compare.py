"""Concept-peer lookup + side-by-side chip comparison (no LLM)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_CONCEPT_UNIVERSE = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "portfolio-gate"
    / "concept_peers_universe.json"
)

MAX_PEERS_PER_THEME = 24

COMPARE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("today_change_pct", "當日漲跌幅", "pct"),
    ("period_return_pct", "區間報酬", "pct"),
    ("foreign_net_lots", "外資買賣超（張）", "lots"),
    ("trust_net_lots", "投信買賣超（張）", "lots"),
    ("dealer_net_lots", "自營買賣超（張）", "lots"),
    ("major_net_lots", "主力買賣超（張）", "lots"),
    ("volume_today_lots", "成交量（張）", "lots"),
    ("volume_ma_ratio", "量／均量比", "ratio"),
    ("close_vs_ma10_pct", "相對 MA10", "pct"),
    ("close_vs_ma20_pct", "相對 MA20", "pct"),
    ("ma20_position", "MA20 位置", "label"),
    ("ma_stack", "均線排列", "label"),
    ("institutional_consensus", "法人共識", "label"),
    ("chip_regime", "籌碼狀態", "label"),
    ("rs_today", "當日相對大盤", "label"),
    ("rs_period", "區間相對大盤", "label"),
    ("price_trend", "價格趨勢", "label"),
    ("rsi_14", "RSI(14)", "number"),
)

LABEL_ZH: dict[str, dict[str, str]] = {
    "ma20_position": {
        "above": "站上",
        "below": "跌破",
        "at": "貼近",
        "unknown": "未知",
    },
    "ma_stack": {
        "bullish_stack": "多頭排列",
        "bearish_stack": "空頭排列",
        "mixed": "糾結",
        "unknown": "未知",
    },
    "institutional_consensus": {
        "bullish": "偏多",
        "bearish": "偏空",
        "mixed": "分歧",
        "neutral": "中性",
        "unknown": "未知",
    },
    "chip_regime": {
        "accumulation": "籌碼偏多",
        "distribution": "籌碼偏空",
        "neutral": "中性",
        "unknown": "未知",
    },
    "rs_today": {
        "outperform": "強於大盤",
        "underperform": "弱於大盤",
        "inline": "同步",
        "unknown": "未知",
    },
    "rs_period": {
        "outperform": "強於大盤",
        "underperform": "弱於大盤",
        "inline": "同步",
        "unknown": "未知",
    },
    "price_trend": {
        "up": "偏多",
        "down": "偏空",
        "flat": "橫盤",
        "unknown": "未知",
    },
}


def _themes_of(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("themes") or []
    if isinstance(raw, str):
        return [t.strip().lower() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip().lower() for t in raw if str(t).strip()]
    return []


def find_concept_peers(
    stock_id: str,
    universe_path: Path | None = None,
    *,
    max_per_theme: int = MAX_PEERS_PER_THEME,
) -> dict[str, Any]:
    """Return theme groups of peers sharing themes with ``stock_id``."""
    from portfolio_signals import load_theme_catalog, load_universe

    path = Path(universe_path) if universe_path else DEFAULT_CONCEPT_UNIVERSE
    stock_id = stock_id.strip()
    catalog = load_theme_catalog(path)
    candidates = load_universe(path)

    self_entry = next(
        (c for c in candidates if str(c.get("id", "")).strip() == stock_id),
        None,
    )
    if self_entry is None:
        return {
            "stock_id": stock_id,
            "stock_name": None,
            "in_universe": False,
            "themes": [],
            "groups": [],
        }

    theme_ids = _themes_of(self_entry)
    groups: list[dict[str, Any]] = []
    for tid in theme_ids:
        meta = catalog.get(tid) or {"label": tid, "style": "growth", "risk_hint": ""}
        peers: list[dict[str, Any]] = []
        for cand in candidates:
            cid = str(cand.get("id", "")).strip()
            if not cid or cid == stock_id:
                continue
            if tid not in _themes_of(cand):
                continue
            peers.append(
                {
                    "stock_id": cid,
                    "stock_name": str(cand.get("name") or cid),
                    "asset_class": str(cand.get("asset_class") or "stock"),
                    "sector": str(cand.get("sector") or ""),
                }
            )
            if len(peers) >= max_per_theme:
                break
        groups.append(
            {
                "theme_id": tid,
                "label": str(meta.get("label", tid)),
                "style": str(meta.get("style", "growth")),
                "risk_hint": str(meta.get("risk_hint", "")),
                "peers": peers,
            }
        )

    return {
        "stock_id": stock_id,
        "stock_name": str(self_entry.get("name") or stock_id),
        "in_universe": True,
        "themes": [
            {
                "id": g["theme_id"],
                "label": g["label"],
                "style": g["style"],
                "risk_hint": g["risk_hint"],
            }
            for g in groups
        ],
        "groups": groups,
    }


def _side_snapshot(facts: dict[str, Any] | None, *, close: float | None = None) -> dict[str, Any]:
    if not facts:
        return {
            "stock_id": None,
            "stock_name": None,
            "trade_date": None,
            "close": close,
            "available": False,
        }
    return {
        "stock_id": facts.get("stock_id"),
        "stock_name": facts.get("stock_name"),
        "trade_date": facts.get("trade_date"),
        "close": close if close is not None else facts.get("close"),
        "available": True,
        "today_change_pct": facts.get("today_change_pct"),
        "period_return_pct": facts.get("period_return_pct"),
        "foreign_net_lots": facts.get("foreign_net_lots"),
        "trust_net_lots": facts.get("trust_net_lots"),
        "dealer_net_lots": facts.get("dealer_net_lots"),
        "major_net_lots": facts.get("major_net_lots"),
        "volume_today_lots": facts.get("volume_today_lots"),
        "volume_ma_ratio": facts.get("volume_ma_ratio"),
        "close_vs_ma10_pct": facts.get("close_vs_ma10_pct"),
        "close_vs_ma20_pct": facts.get("close_vs_ma20_pct"),
        "ma20_position": facts.get("ma20_position"),
        "ma_stack": facts.get("ma_stack"),
        "institutional_consensus": facts.get("institutional_consensus"),
        "chip_regime": facts.get("chip_regime"),
        "rs_today": facts.get("rs_today"),
        "rs_period": facts.get("rs_period"),
        "price_trend": facts.get("price_trend"),
        "rsi_14": facts.get("rsi_14"),
    }


def _fmt_label(field: str, value: Any) -> str:
    if value is None:
        return "—"
    mapping = LABEL_ZH.get(field)
    if mapping:
        return mapping.get(str(value), str(value))
    return str(value)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_highlights(
    base: dict[str, Any],
    peer: dict[str, Any],
) -> list[str]:
    """Rule-based Chinese bullets from two side snapshots."""
    if not base.get("available") or not peer.get("available"):
        return ["其中一檔尚無同日籌碼 facts，無法完整比較。"]

    base_name = str(base.get("stock_name") or base.get("stock_id") or "本檔")
    peer_name = str(peer.get("stock_name") or peer.get("stock_id") or "對照檔")
    out: list[str] = []

    b_chg = _num(base.get("today_change_pct"))
    p_chg = _num(peer.get("today_change_pct"))
    if b_chg is not None and p_chg is not None:
        if b_chg > p_chg + 0.05:
            out.append(f"當日漲跌：{base_name}（{b_chg:+.2f}%）強於 {peer_name}（{p_chg:+.2f}%）。")
        elif p_chg > b_chg + 0.05:
            out.append(f"當日漲跌：{peer_name}（{p_chg:+.2f}%）強於 {base_name}（{b_chg:+.2f}%）。")
        else:
            out.append(f"當日漲跌接近：{base_name} {b_chg:+.2f}%／{peer_name} {p_chg:+.2f}%。")

    b_f = _num(base.get("foreign_net_lots"))
    p_f = _num(peer.get("foreign_net_lots"))
    if b_f is not None and p_f is not None:
        if (b_f > 0 and p_f > 0) or (b_f < 0 and p_f < 0):
            out.append(
                f"外資同向：{base_name} {b_f:+,.0f} 張、{peer_name} {p_f:+,.0f} 張。"
            )
        elif b_f == 0 and p_f == 0:
            out.append("外資當日皆接近平盤。")
        else:
            out.append(
                f"外資方向分歧：{base_name} {b_f:+,.0f} 張、{peer_name} {p_f:+,.0f} 張。"
            )

    b_ma = str(base.get("ma20_position") or "unknown")
    p_ma = str(peer.get("ma20_position") or "unknown")
    if b_ma != "unknown" or p_ma != "unknown":
        out.append(
            f"相對 MA20：{base_name} {_fmt_label('ma20_position', b_ma)}、"
            f"{peer_name} {_fmt_label('ma20_position', p_ma)}。"
        )

    b_rs = str(base.get("rs_today") or "unknown")
    p_rs = str(peer.get("rs_today") or "unknown")
    if b_rs != "unknown" or p_rs != "unknown":
        out.append(
            f"當日相對大盤：{base_name} {_fmt_label('rs_today', b_rs)}、"
            f"{peer_name} {_fmt_label('rs_today', p_rs)}。"
        )

    b_chip = str(base.get("chip_regime") or "unknown")
    p_chip = str(peer.get("chip_regime") or "unknown")
    if b_chip != "unknown" or p_chip != "unknown":
        out.append(
            f"籌碼狀態：{base_name} {_fmt_label('chip_regime', b_chip)}、"
            f"{peer_name} {_fmt_label('chip_regime', p_chip)}。"
        )

    b_ret = _num(base.get("period_return_pct"))
    p_ret = _num(peer.get("period_return_pct"))
    if b_ret is not None and p_ret is not None:
        stronger = base_name if b_ret >= p_ret else peer_name
        out.append(
            f"區間報酬：{base_name} {b_ret:+.2f}%／{peer_name} {p_ret:+.2f}%（{stronger}較強）。"
        )

    if not out:
        out.append("已對齊同日 facts，但可比欄位不足，僅能參照下方對照表。")
    return out


def relative_stance(base: dict[str, Any], peer: dict[str, Any]) -> dict[str, Any]:
    """Deterministic base-vs-peer stance. ``base_weaker`` is true only for label weaker.

    Weaker needs at least two of: period return lags by >1pt, today lags by >0.3pt,
    chip regime is distribution while the peer is not, or today RS underperforms
    while the peer outperforms. The mirror counts as stronger. Otherwise mixed.
    """
    if not base.get("available") or not peer.get("available"):
        return {
            "label": "unknown",
            "base_weaker": False,
            "reasons": ["其中一檔沒有同日 facts，無法判斷強弱。"],
        }

    weaker: list[str] = []
    stronger: list[str] = []
    base_name = str(base.get("stock_name") or base.get("stock_id") or "本檔")

    b_ret = _num(base.get("period_return_pct"))
    p_ret = _num(peer.get("period_return_pct"))
    if b_ret is not None and p_ret is not None:
        if b_ret < p_ret - 1.0:
            weaker.append(f"區間報酬落後（{base_name} {b_ret:+.2f}%）")
        elif p_ret < b_ret - 1.0:
            stronger.append(f"區間報酬領先（{base_name} {b_ret:+.2f}%）")

    b_chg = _num(base.get("today_change_pct"))
    p_chg = _num(peer.get("today_change_pct"))
    if b_chg is not None and p_chg is not None:
        if b_chg < p_chg - 0.3:
            weaker.append(f"當日漲跌落後（{base_name} {b_chg:+.2f}%）")
        elif p_chg < b_chg - 0.3:
            stronger.append(f"當日漲跌領先（{base_name} {b_chg:+.2f}%）")

    b_chip = str(base.get("chip_regime") or "unknown")
    p_chip = str(peer.get("chip_regime") or "unknown")
    if b_chip == "distribution" and p_chip != "distribution":
        weaker.append("籌碼狀態較空")
    elif p_chip == "distribution" and b_chip != "distribution":
        stronger.append("對照檔籌碼較空")

    b_rs = str(base.get("rs_today") or "unknown")
    p_rs = str(peer.get("rs_today") or "unknown")
    if b_rs == "underperform" and p_rs == "outperform":
        weaker.append("當日相對大盤弱於對照檔")
    elif p_rs == "underperform" and b_rs == "outperform":
        stronger.append("當日相對大盤強於對照檔")

    if len(weaker) >= 2 and len(weaker) > len(stronger):
        label = "weaker"
    elif len(stronger) >= 2 and len(stronger) > len(weaker):
        label = "stronger"
    else:
        label = "mixed"
    reasons = weaker + stronger
    if not reasons:
        reasons = ["可比欄位不足或差距在門檻內，視為分歧。"]
    return {"label": label, "base_weaker": label == "weaker", "reasons": reasons}


def build_compare_rows(
    base: dict[str, Any],
    peer: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label, kind in COMPARE_FIELDS:
        b_raw = base.get(key) if base.get("available") else None
        p_raw = peer.get(key) if peer.get("available") else None
        if kind == "label":
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "kind": kind,
                    "base": _fmt_label(key, b_raw),
                    "peer": _fmt_label(key, p_raw),
                    "base_raw": b_raw,
                    "peer_raw": p_raw,
                }
            )
        else:
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "kind": kind,
                    "base": b_raw,
                    "peer": p_raw,
                    "base_raw": b_raw,
                    "peer_raw": p_raw,
                }
            )
    return rows


def build_comparison(
    *,
    base_facts: dict[str, Any] | None,
    peer_facts: dict[str, Any] | None,
    base_close: float | None = None,
    peer_close: float | None = None,
    shared_themes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    base = _side_snapshot(base_facts, close=base_close)
    peer = _side_snapshot(peer_facts, close=peer_close)
    return {
        "base": base,
        "peer": peer,
        "rows": build_compare_rows(base, peer),
        "highlights": build_highlights(base, peer),
        "stance": relative_stance(base, peer),
        "shared_themes": shared_themes or [],
    }


def shared_themes_for(
    base_id: str,
    peer_id: str,
    universe_path: Path | None = None,
) -> list[dict[str, str]]:
    from portfolio_signals import load_theme_catalog, load_universe

    path = Path(universe_path) if universe_path else DEFAULT_CONCEPT_UNIVERSE
    catalog = load_theme_catalog(path)
    candidates = load_universe(path)
    base_themes = set()
    peer_themes = set()
    for cand in candidates:
        cid = str(cand.get("id", "")).strip()
        themes = set(_themes_of(cand))
        if cid == base_id:
            base_themes = themes
        elif cid == peer_id:
            peer_themes = themes
    shared = sorted(base_themes & peer_themes)
    return [
        {
            "id": tid,
            "label": str((catalog.get(tid) or {}).get("label", tid)),
        }
        for tid in shared
    ]
