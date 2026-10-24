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
        "mixed": "籌碼中性",
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


MAX_BOARD_MEMBERS = 40


def list_concept_groups(universe_path: Path | None = None) -> dict[str, Any]:
    """Catalog themes with every member, including the stock you would open a report for.

    Themes that are not in the catalog are omitted, so a secondary tag does not
    become its own group.
    """
    from portfolio_signals import load_theme_catalog, load_universe

    path = Path(universe_path) if universe_path else DEFAULT_CONCEPT_UNIVERSE
    catalog = load_theme_catalog(path)
    candidates = load_universe(path)
    catalog_ids = set(catalog)
    buckets: dict[str, list[dict[str, Any]]] = {tid: [] for tid in catalog}

    for cand in candidates:
        cid = str(cand.get("id", "")).strip()
        if not cid:
            continue
        themes = [tid for tid in _themes_of(cand) if tid in catalog_ids]
        if not themes:
            continue
        member = {
            "stock_id": cid,
            "stock_name": str(cand.get("name") or cid),
            "sector": str(cand.get("sector") or ""),
            "theme_ids": themes,
        }
        for tid in themes:
            buckets.setdefault(tid, []).append(member)

    themes_out: list[dict[str, Any]] = []
    ordered = list(catalog.keys())
    for tid in buckets:
        if tid not in ordered:
            ordered.append(tid)
    for tid in ordered:
        members = buckets.get(tid) or []
        if tid not in catalog and not members:
            continue
        meta = catalog.get(tid) or {"label": tid, "style": "growth", "risk_hint": ""}
        themes_out.append(
            {
                "theme_id": tid,
                "label": str(meta.get("label", tid)),
                "style": str(meta.get("style", "growth")),
                "risk_hint": str(meta.get("risk_hint", "")),
                "members": members,
            }
        )
    return {"themes": themes_out}


def resolve_board_members(
    *,
    theme_id: str | None = None,
    stock_ids: list[str] | None = None,
    universe_path: Path | None = None,
) -> dict[str, Any]:
    """Pick board rows from one theme, an id list, or the intersection of both."""
    payload = list_concept_groups(universe_path)
    themes = payload["themes"]
    theme_key = (theme_id or "").strip().lower() or None
    wanted = [sid.strip() for sid in (stock_ids or []) if sid and sid.strip()]

    if theme_key:
        theme = next((item for item in themes if item["theme_id"] == theme_key), None)
        if theme is None:
            return {
                "ok": False,
                "error": f"找不到主題 {theme_key}",
                "members": [],
                "skipped": [],
                "warning": None,
                "theme_id": theme_key,
            }
        pool = {member["stock_id"]: member for member in theme["members"]}
    else:
        pool = {}
        for theme in themes:
            for member in theme["members"]:
                pool.setdefault(member["stock_id"], member)

    skipped: list[str] = []
    if wanted:
        members = []
        for sid in wanted:
            member = pool.get(sid)
            if member is None:
                skipped.append(sid)
            else:
                members.append(member)
    elif theme_key:
        members = list(pool.values())
    else:
        return {
            "ok": False,
            "error": "請指定主題或代號",
            "members": [],
            "skipped": [],
            "warning": None,
            "theme_id": None,
        }

    warning = None
    if len(members) > MAX_BOARD_MEMBERS:
        warning = f"僅顯示前 {MAX_BOARD_MEMBERS} 檔"
        members = members[:MAX_BOARD_MEMBERS]

    return {
        "ok": True,
        "error": None,
        "members": members,
        "skipped": skipped,
        "warning": warning,
        "theme_id": theme_key,
    }


def board_row_from_facts(
    member: dict[str, Any],
    facts: dict[str, Any] | None,
    *,
    close: float | None = None,
    board_date: str | None = None,
) -> dict[str, Any]:
    """One status row. Missing facts stay on the board so the UI can offer a refill."""
    snap = _side_snapshot(facts, close=close)
    available = bool(snap.get("available"))
    trade_date = str(snap.get("trade_date") or "") or None
    aligned = True
    if board_date and trade_date and trade_date != board_date:
        aligned = False
    if not available:
        aligned = False if board_date else True

    def _raw(key: str) -> Any:
        if not available:
            return None
        return snap.get(key)

    chip = _raw("chip_regime")
    rs = _raw("rs_today")
    return {
        "stock_id": str(member.get("stock_id") or snap.get("stock_id") or ""),
        "stock_name": str(
            (snap.get("stock_name") if available else None)
            or member.get("stock_name")
            or member.get("stock_id")
            or ""
        ),
        "sector": str(member.get("sector") or ""),
        "theme_ids": list(member.get("theme_ids") or []),
        "available": available,
        "aligned": aligned if available else False,
        "trade_date": trade_date,
        "close": _raw("close"),
        "today_change_pct": _raw("today_change_pct"),
        "period_return_pct": _raw("period_return_pct"),
        "foreign_net_lots": _raw("foreign_net_lots"),
        "chip_regime": chip,
        "chip_regime_label": _fmt_label("chip_regime", chip) if available else "—",
        "rs_today": rs,
        "rs_today_label": _fmt_label("rs_today", rs) if available else "—",
    }


def _brief_name(row: dict[str, Any]) -> str:
    return str(row.get("stock_name") or row.get("stock_id") or "")


def _ranked(rows: list[dict[str, Any]], key: str) -> list[tuple[dict[str, Any], float]]:
    scored: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        value = _num(row.get(key))
        if value is not None:
            scored.append((row, value))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _row_id(row: dict[str, Any]) -> str:
    return str(row.get("stock_id") or _brief_name(row))


def _join_names(rows: list[dict[str, Any]]) -> str:
    return "、".join(_brief_name(row) for row in rows)


def _week_return(row: dict[str, Any]) -> float | None:
    return _num(row.get("week_return_pct"))


_INSTITUTIONS = (
    ("week_foreign_net_lots", "外資"),
    ("week_trust_net_lots", "投信"),
    ("week_dealer_net_lots", "自營"),
)


def _week_lots(row: dict[str, Any], key: str) -> int | None:
    value = _num(row.get(key))
    if value is None:
        return None
    return int(value)


def _institution_parts(row: dict[str, Any]) -> list[tuple[str, int]] | None:
    parts: list[tuple[str, int]] = []
    for key, label in _INSTITUTIONS:
        lots = _week_lots(row, key)
        if lots is None:
            return None
        parts.append((label, lots))
    return parts


def _side_phrase(parts: list[tuple[str, int]]) -> str:
    bits: list[str] = []
    for label, lots in parts:
        if lots > 0:
            bits.append(f"{label}買超")
        elif lots < 0:
            bits.append(f"{label}賣超")
        else:
            bits.append(f"{label}接近平盤")
    return "、".join(bits)


def _alignment(parts: list[tuple[str, int]]) -> str:
    signs = [1 if lots > 0 else -1 if lots < 0 else 0 for _label, lots in parts]
    active = [sign for sign in signs if sign != 0]
    if len(active) == 3 and all(sign > 0 for sign in active):
        return "all_buy"
    if len(active) == 3 and all(sign < 0 for sign in active):
        return "all_sell"
    if active and all(sign > 0 for sign in active):
        return "buyers"
    if active and all(sign < 0 for sign in active):
        return "sellers"
    if not active:
        return "flat"
    return "split"


def _price_words(value: float | None) -> str:
    if value is None:
        return "五日報酬不足"
    if value > 1:
        return f"五日報酬 {value:+.2f}%，價格上漲"
    if value < -1:
        return f"五日報酬 {value:+.2f}%，價格下跌"
    return f"五日報酬 {value:+.2f}%，價格接近平"


def _observe_one(row: dict[str, Any], *, held: bool) -> str:
    name = _brief_name(row)
    role = "（持股，與其他勾選平等）" if held else ""
    parts = _institution_parts(row)
    price = _price_words(_week_return(row))
    if parts is None:
        return f"{name}{role}：{price}。外資、投信、自營的週資料不齊，這檔先不參加判斷。"
    alignment = _alignment(parts)
    detail = _side_phrase(parts)
    change = _week_return(row) or 0
    if alignment == "all_buy" and change > 0:
        relation = "價格上漲，且外資、投信、自營一起買，價格和三大法人同向。"
    elif alignment == "all_buy" and change < 0:
        relation = "價格下跌，但外資、投信、自營一起買，籌碼比價格齊。"
    elif alignment == "all_sell" and change > 0:
        relation = "價格上漲，但外資、投信、自營一起賣，價格和籌碼相反。"
    elif alignment == "all_sell":
        relation = "價格下跌，外資、投信、自營一起賣，價格和三大法人同向偏弱。"
    elif alignment == "flat":
        relation = "三大法人這一週都接近平盤，籌碼沒有方向。"
    else:
        relation = f"三大法人沒有同心：{detail}。不能只用其中一類代表這檔。"
    return f"{name}{role}：{price}。{relation}"


def _reasoning(ready: list[dict[str, Any]]) -> list[str]:
    ranked = _ranked(ready, "week_return_pct")
    lines: list[str] = []
    if len(ranked) >= 2:
        spread = ranked[0][1] - ranked[-1][1]
        if spread <= 1:
            lines.append("這幾檔的五日報酬沒有拉開，價格本身分不出誰該被單獨看待。")
        else:
            lines.append(
                "五日報酬有差距，從"
                f"{_brief_name(ranked[0][0])} {ranked[0][1]:+.2f}% 到"
                f"{_brief_name(ranked[-1][0])} {ranked[-1][1]:+.2f}%。"
                "這只是價格的排列，不表示排前面的就是這組的核心。"
            )
    buyers = _aligned_names(ready, "all_buy")
    sellers = _aligned_names(ready, "all_sell")
    if buyers:
        lines.append(f"外資、投信、自營一起買的是{_join_names(buyers)}。")
    else:
        lines.append("沒有任何一檔是外資、投信、自營一起買。")
    if sellers:
        lines.append(f"外資、投信、自營一起賣的是{_join_names(sellers)}。")
    if ranked and buyers:
        leader_parts = _institution_parts(ranked[0][0])
        leader_alignment = _alignment(leader_parts) if leader_parts else None
        if leader_alignment != "all_buy":
            lines.append(f"反而{_join_names(buyers)}是外資、投信、自營一起買。")
    for row in ready:
        parts = _institution_parts(row)
        if parts is None or _alignment(parts) != "split":
            continue
        foreign = _week_lots(row, "week_foreign_net_lots") or 0
        trust = _week_lots(row, "week_trust_net_lots") or 0
        if foreign * trust < 0:
            lines.append(
                f"{_brief_name(row)}是外資和投信對作：{_side_phrase(parts)}。"
                "只跟外資會把這檔看成單向，投信的方向會消失。"
            )
        else:
            lines.append(f"{_brief_name(row)}法人方向拆開：{_side_phrase(parts)}。")
    lines.append("每一檔都要同時看價格和三類法人。價格排第一、或外資買超最大，都不能代表其他檔。")
    return lines


def _same_direction(row: dict[str, Any]) -> bool:
    """Price and all three institutions point the same way, and price actually moved."""
    change = _week_return(row)
    parts = _institution_parts(row)
    if change is None or parts is None or abs(change) <= 1:
        return False
    alignment = _alignment(parts)
    if change > 0 and alignment == "all_buy":
        return True
    return change < 0 and alignment == "all_sell"


def _spread(ready: list[dict[str, Any]]) -> float:
    ranked = _ranked(ready, "week_return_pct")
    if len(ranked) < 2:
        return 0.0
    return ranked[0][1] - ranked[-1][1]


def _coverage(ready: list[dict[str, Any]]) -> str:
    bits: list[str] = []
    for row in ready:
        parts = _institution_parts(row)
        name = _brief_name(row)
        if parts is None:
            bits.append(f"{name}法人不齊")
        else:
            bits.append(f"{name}是{_side_phrase(parts)}")
    return "改寫後逐檔對過：" + "；".join(bits) + "。"


def _conclusion(ready: list[dict[str, Any]], held_ids: set[str]) -> list[str]:
    held_rows = [row for row in ready if _row_id(row) in held_ids]
    aligned = [row for row in ready if _same_direction(row) and (_week_return(row) or 0) > 0]
    spread = _spread(ready)
    if not held_rows:
        if spread <= 1:
            lines = ["這幾檔報酬沒有拉開，不構成入場。"]
        elif len(aligned) == 1:
            name = _brief_name(aligned[0])
            others = [row for row in ready if _row_id(row) != _row_id(aligned[0])]
            other_text = _join_names(others) if others else "其他檔"
            lines = [
                f"沒有持股。{name}不只有價格領先，外資、投信、自營也一起買。較值得觀察入場。"
                f"{other_text}沒有這個組合，不跟著一起進場。"
            ]
        elif aligned:
            lines = [
                f"沒有持股。{_join_names(aligned)}都是價格上漲且三大法人一起買，可以一起觀察，不挑勾選裡的某一檔當核心。"
            ]
        else:
            lines = [
                "沒有持股。這一組沒有任何一檔同時是價格上漲、且外資、投信、自營一起買。不構成入場。"
            ]
        lines.append(_coverage(ready))
        return lines
    lines: list[str] = []
    switched: set[str] = set()
    for held in held_rows:
        held_parts = _institution_parts(held)
        if held_parts is None:
            continue
        peers = []
        for row in ready:
            if _row_id(row) == _row_id(held):
                continue
            peer_return = _week_return(row)
            held_return = _week_return(held)
            peer_parts = _institution_parts(row)
            if peer_return is None or held_return is None or peer_parts is None:
                continue
            if peer_return <= held_return + 1:
                continue
            if _alignment(peer_parts) == "all_buy" and _alignment(held_parts) != "all_buy":
                peers.append(row)
        if not peers:
            continue
        peers.sort(key=lambda row: _week_return(row) or 0, reverse=True)
        peer = peers[0]
        lines.append(
            f"{_brief_name(held)}是持股，和其他勾選平等比較。"
            f"{_brief_name(peer)}報酬更好，而且外資、投信、自營一起買，可考慮換過去。"
        )
        switched.add(_row_id(held))
    staying = [row for row in held_rows if _row_id(row) not in switched]
    if staying:
        lines.append(
            f"{_join_names(staying)}是持股。其他檔沒有同時報酬更好、且外資、投信、自營一起買，還不到換檔，續抱。"
        )
    lines.append(_coverage(ready))
    return lines


def _review(ready: list[dict[str, Any]], conclusion: list[str]) -> list[str]:
    ranked = _ranked(ready, "week_return_pct")
    leader = _brief_name(ranked[0][0]) if ranked else ""
    others = [_brief_name(row) for row in ready if _brief_name(row) != leader]
    notes = [
        f"初稿只寫{leader}，因為它的五日報酬排在這組前面，勾選順序也容易被看成重要性。",
        f"回看：這樣會漏掉{'、'.join(others) or '其他檔'}各自的外資、投信、自營。初稿撤回，改成逐檔平等比較。",
    ]
    text = "\n".join(conclusion)
    missing = [name for name in (_brief_name(row) for row in ready) if name not in text]
    opposed = []
    for row in ready:
        parts = _institution_parts(row)
        if parts is None or _alignment(parts) != "split":
            continue
        foreign = _week_lots(row, "week_foreign_net_lots") or 0
        trust = _week_lots(row, "week_trust_net_lots") or 0
        if foreign * trust < 0:
            opposed.append(_brief_name(row))
    if missing:
        notes.append("回看結論仍漏了" + "、".join(missing) + "，已補上逐檔對過。")
    elif opposed:
        notes.append(
            "回看"
            + "、".join(opposed)
            + "：外資和投信方向相反。若只跟價格最好的一檔或只跟外資，這個對作會消失，已寫進結論。"
        )
    else:
        notes.append("回看後每一檔都留在結論裡，沒有再指定某一檔當核心。")
    notes.append("最後只保留價格和三大法人是否同向，不看勾選順序，也不只看外資。")
    return notes


def _sections(
    ready: list[dict[str, Any]],
    held_ids: set[str],
    *,
    missing: list[str],
) -> list[dict[str, Any]]:
    names = _join_names(ready)
    held_rows = [row for row in ready if _row_id(row) in held_ids]
    if held_rows:
        question = f"其中{_join_names(held_rows)}是持股，要和其他檔平等判斷留著還是換到另一檔。"
    else:
        question = "勾選裡沒有持股，只判斷有沒有值得觀察入場的一檔，不替沒被勾到的股票作主。"
    plan = [
        f"比較對象是{names}。這幾檔地位一樣，勾選的先後、表上的第一列，都不是核心。",
        f"材料是近五個交易日的價格，加上外資、投信、自營的累計方向。{question}",
        "順序是規劃、逐檔觀察、跨檔推理、回看。回看若發現結論只抓一檔或只跟外資，就改寫。",
    ]
    observations = [
        _observe_one(row, held=_row_id(row) in held_ids) for row in ready
    ]
    if any(_institution_parts(row) is None for row in ready):
        conclusion = ["三大法人的週資料還沒齊，先不判斷入場、換檔或續抱。"]
        review = ["回看：法人資料不齊，不強行寫建議。"]
        reasoning = ["有檔缺少外資、投信或自營，跨檔推理先停。"]
    else:
        reasoning = _reasoning(ready)
        conclusion = _conclusion(ready, held_ids)
        review = _review(ready, conclusion)
    if missing:
        observations.append("還沒有近五個交易日資料：" + "、".join(missing) + "。")
    return [
        {"id": "plan", "title": "規劃", "paragraphs": plan},
        {"id": "observe", "title": "觀察", "paragraphs": observations},
        {"id": "reason", "title": "推理", "paragraphs": reasoning},
        {"id": "review", "title": "回看", "paragraphs": review},
        {"id": "conclusion", "title": "結論", "paragraphs": conclusion},
    ]


def _aligned_names(ready: list[dict[str, Any]], alignment: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for row in ready:
        parts = _institution_parts(row)
        if parts is not None and _alignment(parts) == alignment:
            found.append(row)
    return found


def build_group_brief(
    rows: list[dict[str, Any]],
    *,
    trade_date: str | None = None,
    held_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Five-session entry or switch note. Rules only; no model and no position size."""
    held = {str(stock_id) for stock_id in (held_ids or [])}
    ready = [row for row in rows if row.get("available") and _week_return(row) is not None]
    missing = [
        _brief_name(row)
        for row in rows
        if not row.get("available") or _week_return(row) is None
    ]
    names = "、".join(_brief_name(row) for row in ready) or "這一小群"
    title = f"{names} · 近五個交易日"
    if trade_date:
        title = f"{title} · {trade_date}"
    brief_rows = [
        {
            "stock_id": _row_id(row),
            "stock_name": _brief_name(row),
            "held": _row_id(row) in held,
            "week_return_pct": _week_return(row),
            "week_foreign_net_lots": _week_lots(row, "week_foreign_net_lots"),
            "week_trust_net_lots": _week_lots(row, "week_trust_net_lots"),
            "week_dealer_net_lots": _week_lots(row, "week_dealer_net_lots"),
        }
        for row in rows
        if row.get("available") or _week_return(row) is not None
    ]
    brief_rows.sort(
        key=lambda row: (
            row["week_return_pct"] is None,
            -(row["week_return_pct"] or 0),
        )
    )
    if len(ready) < 2:
        return {
            "ok": False,
            "title": title,
            "trade_date": trade_date,
            "window": "5d",
            "rows": brief_rows,
            "paragraphs": ["至少要有兩檔近五個交易日的報酬，才能判斷入場或換檔。"],
            "sections": [],
            "missing": missing,
        }
    sections = _sections(ready, held, missing=missing)
    paragraphs = [paragraph for section in sections for paragraph in section["paragraphs"]]
    return {
        "ok": True,
        "title": title,
        "trade_date": trade_date,
        "window": "5d",
        "rows": brief_rows,
        "paragraphs": paragraphs,
        "sections": sections,
        "missing": missing,
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
