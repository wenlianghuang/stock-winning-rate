"""triage_watchlist — rank stocks by what changed today, before any agy gate.

Pure Python over CSVs already on disk: no fetch, no LLM, no files written.
Scores mean "needs a look", not "likely to rise"; base rates show chip regimes
carry almost no forward edge, so they never enter the ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.tools._paths import ROOT, ensure_paths
from agent.tools._types import EXIT_BAD_ARGS, EXIT_FAILED, EXIT_OK, ToolResult

ensure_paths("tw-stock-report")

DEFAULT_TOP_K = 3
MAX_TOP_K = 8
MAX_STOCKS = 60
MIN_PICK_SCORE = 2

SOURCE_HOLDINGS = "holdings"
SOURCE_WATCHLIST = "watchlist"
DEFAULT_WATCHLIST = ROOT / ".agents" / "skills" / "tw-stock-report" / "watchlist.txt"

REGIME_SHORT = {
    "accumulation": "籌碼偏多",
    "distribution": "籌碼偏空",
    "mixed": "籌碼中性",
    "unknown": "籌碼資料不足",
}

DIVERGENCE_SHORT = {
    "price_up_foreign_sell": "價漲外資賣",
    "price_down_foreign_buy": "價跌外資買",
    "price_down_margin_up": "價跌融資增",
    "price_up_margin_up": "價漲融資同步增",
}

WEIGHT_NO_CSV = 3
WEIGHT_REGIME_FLIP_STRONG = 3
WEIGHT_REGIME_FLIP = 2
WEIGHT_BREAKDOWN = 3
WEIGHT_BREAKOUT = 2
WEIGHT_NEW_DIVERGENCE = 2
WEIGHT_STREAK_BREAK = 2
WEIGHT_HELD_DISTRIBUTION = 2
WEIGHT_MA_CROSS_TODAY = 1
WEIGHT_VOLUME_SPIKE = 1
WEIGHT_BEARISH_VOLUME = 1
WEIGHT_NO_BASELINE = 1
STREAK_BREAK_MIN_DAYS = 3


@dataclass
class TriageInput:
    stocks: list[str] | str | None = None
    trade_date: str | None = None
    held: list[str] | None = None
    top_k: int = DEFAULT_TOP_K
    source: str = SOURCE_HOLDINGS
    holdings_path: Path | str | None = None
    watchlist: Path | str | None = None
    root: Path | None = None


@dataclass
class TriageResult(ToolResult):
    trade_date: str | None = None
    top_k: int = DEFAULT_TOP_K
    rows: list[dict[str, Any]] = field(default_factory=list)
    picks: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _flag(code: str, label: str, weight: int) -> dict[str, Any]:
    return {"code": code, "label": label, "weight": weight}


def score_facts(
    today: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    *,
    held: bool,
) -> list[dict[str, Any]]:
    """Flags for one stock. ``today is None`` means no snapshot CSV for the day."""
    if today is None:
        return [_flag("no_csv", "當日沒有籌碼 CSV，無法判斷", WEIGHT_NO_CSV)]

    flags: list[dict[str, Any]] = []
    regime = str(today.get("chip_regime") or "unknown")

    if previous is None:
        flags.append(_flag("no_baseline", "沒有更早的資料可比較", WEIGHT_NO_BASELINE))
    else:
        prev_regime = str(previous.get("chip_regime") or "unknown")
        if "unknown" not in (regime, prev_regime) and regime != prev_regime:
            strong = {regime, prev_regime} == {"accumulation", "distribution"}
            flags.append(
                _flag(
                    "regime_flip",
                    f"{REGIME_SHORT[prev_regime]}轉{REGIME_SHORT[regime]}",
                    WEIGHT_REGIME_FLIP_STRONG if strong else WEIGHT_REGIME_FLIP,
                )
            )

    if today.get("breakdown_20d_low") == "yes":
        flags.append(_flag("breakdown_20d", "收盤跌破近 20 日低", WEIGHT_BREAKDOWN))
    if today.get("breakout_20d_high") == "yes":
        flags.append(_flag("breakout_20d", "收盤突破近 20 日高", WEIGHT_BREAKOUT))

    today_div = [str(code) for code in today.get("divergences") or []]
    prev_div = set(str(code) for code in (previous or {}).get("divergences") or [])
    for code in today_div:
        if previous is not None and code in prev_div:
            continue
        label = DIVERGENCE_SHORT.get(code, code)
        prefix = "新出現背離" if previous is not None else "背離"
        flags.append(_flag(f"divergence:{code}", f"{prefix}：{label}", WEIGHT_NEW_DIVERGENCE))

    if previous is not None:
        prev_dir = int(previous.get("foreign_streak_dir") or 0)
        prev_days = int(previous.get("foreign_streak_days") or 0)
        today_dir = int(today.get("foreign_direction") or 0)
        if prev_days >= STREAK_BREAK_MIN_DAYS and prev_dir != 0 and today_dir == -prev_dir:
            before = "連買" if prev_dir > 0 else "連賣"
            after = "賣超" if today_dir < 0 else "買超"
            flags.append(
                _flag(
                    "foreign_streak_break",
                    f"外資{before} {prev_days} 日後轉{after}",
                    WEIGHT_STREAK_BREAK,
                )
            )

    if held and regime == "distribution":
        flags.append(_flag("held_distribution", "持有中且籌碼偏空", WEIGHT_HELD_DISTRIBUTION))

    if today.get("ma5_cross_recency") == "today" and today.get("ma5_cross_ma10") in (
        "golden",
        "death",
    ):
        kind = "黃金交叉" if today.get("ma5_cross_ma10") == "golden" else "死亡交叉"
        flags.append(_flag("ma5_cross_today", f"今日 MA5／MA10 {kind}", WEIGHT_MA_CROSS_TODAY))

    if today.get("volume_anomaly") == "spike":
        flags.append(_flag("volume_spike", "成交量明顯放大", WEIGHT_VOLUME_SPIKE))

    if today.get("volume_price_divergence") == "bearish_divergence" and (
        previous is None or previous.get("volume_price_divergence") != "bearish_divergence"
    ):
        flags.append(_flag("bearish_volume", "價漲量縮", WEIGHT_BEARISH_VOLUME))

    return flags


def flags_score(flags: list[dict[str, Any]]) -> int:
    return sum(int(item.get("weight") or 0) for item in flags)


def rank_rows(rows: list[dict[str, Any]], top_k: int) -> list[str]:
    """Pick at most ``top_k`` stocks with score >= MIN_PICK_SCORE; holdings break ties."""
    eligible = [row for row in rows if int(row.get("score") or 0) >= MIN_PICK_SCORE]
    eligible.sort(
        key=lambda row: (-int(row.get("score") or 0), not row.get("held"), str(row["stock_id"]))
    )
    return [str(row["stock_id"]) for row in eligible[: max(0, top_k)]]


def summary_line(row: dict[str, Any]) -> str:
    name = row.get("stock_name")
    head = f"{name}（{row['stock_id']}）" if name else str(row["stock_id"])
    if not row.get("has_csv"):
        return f"{head}：當日沒有籌碼 CSV"
    regime = REGIME_SHORT.get(str(row.get("chip_regime") or "unknown"), "籌碼資料不足")
    labels = [item["label"] for item in row.get("flags") or [] if item["code"] != "no_baseline"]
    if labels:
        return f"{head}：{regime}；{'、'.join(labels)}"
    if row.get("has_baseline"):
        return f"{head}：{regime}，和 {row.get('prev_trade_date')} 相比沒有新條件"
    return f"{head}：{regime}，沒有更早的資料可比較"


def _facts_from_csv(csv_path: Path) -> dict[str, Any] | None:
    from chip_signals import build_chip_facts as compute_facts  # type: ignore[import-not-found]
    from chip_signals import facts_to_json  # type: ignore[import-not-found]
    from chip_tables import load_csv_row, load_history_rows  # type: ignore[import-not-found]

    row = load_csv_row(csv_path)
    if row is None:
        return None
    return facts_to_json(compute_facts(row, load_history_rows(csv_path)))


def previous_csv(stock_id: str, trade_date: str, *, root: Path | None = None) -> Path | None:
    base = (root or ROOT) / "reports" / "stock"
    if not base.is_dir():
        return None
    candidates = [
        path
        for path in base.glob(f"*/{stock_id}/tw_stock_{stock_id}.csv")
        if path.parent.parent.name < trade_date
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.parent.parent.name)


def _normalize_stocks(stocks: list[str] | str | None) -> list[str]:
    if stocks is None:
        return []
    if isinstance(stocks, str):
        parts = stocks.replace("，", ",").split(",")
    else:
        parts = [str(item) for item in stocks]
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


def _read_watchlist(path: Path) -> list[str]:
    if not path.is_file():
        return []
    stocks: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            stocks.append(text)
    return stocks


def _held_from_file(path: Path | str | None) -> list[str]:
    from agent.policy import is_actionable_holding
    from agent.tools.position import get_holdings

    result = get_holdings(path=str(path) if path else None)
    if not result.ok:
        return []
    return [sid for sid, record in (result.holdings or {}).items() if is_actionable_holding(record)]


def _resolve_trade_date(trade_date: str | None) -> tuple[str | None, str | None]:
    if trade_date:
        return trade_date, None
    from agent.tools.dates import get_last_trading_date

    resolved = get_last_trading_date()
    if not resolved.ok or not resolved.trade_date:
        return None, resolved.error or "無法解析交易日"
    return resolved.trade_date, None


def triage_watchlist(inp: TriageInput | None = None, **overrides: Any) -> TriageResult:
    params = inp or TriageInput()
    if overrides:
        data = {
            "stocks": params.stocks,
            "trade_date": params.trade_date,
            "held": params.held,
            "top_k": params.top_k,
            "source": params.source,
            "holdings_path": params.holdings_path,
            "watchlist": params.watchlist,
            "root": params.root,
        }
        data.update(overrides)
        params = TriageInput(**data)

    top_k = max(0, min(int(params.top_k), MAX_TOP_K))
    notes: list[str] = []
    if int(params.top_k) > MAX_TOP_K:
        notes.append(f"top_k 上限 {MAX_TOP_K}，已從 {params.top_k} 調降")

    held = _normalize_stocks(params.held) if params.held is not None else None
    if held is None:
        held = _held_from_file(params.holdings_path)

    stocks = _normalize_stocks(params.stocks)
    if not stocks:
        if params.source == SOURCE_WATCHLIST:
            stocks = _read_watchlist(Path(params.watchlist) if params.watchlist else DEFAULT_WATCHLIST)
        elif params.source == SOURCE_HOLDINGS:
            stocks = list(held)
        else:
            return TriageResult(
                ok=False,
                exit_code=EXIT_BAD_ARGS,
                error=f"source 必須是 {SOURCE_HOLDINGS} 或 {SOURCE_WATCHLIST}",
            )
    if not stocks:
        return TriageResult(
            ok=False,
            exit_code=EXIT_BAD_ARGS,
            error="沒有可分流的標的（stocks、持股與 watchlist 都是空的）",
        )
    if len(stocks) > MAX_STOCKS:
        notes.append(f"一次最多 {MAX_STOCKS} 檔，只掃前 {MAX_STOCKS} 檔")
        stocks = stocks[:MAX_STOCKS]

    trade_date, date_error = _resolve_trade_date(params.trade_date)
    if not trade_date:
        return TriageResult(ok=False, exit_code=EXIT_FAILED, error=date_error)

    from stock_paths import find_stock_csv  # type: ignore[import-not-found]

    root = params.root or ROOT
    held_set = set(held)
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for stock_id in stocks:
        is_held = stock_id in held_set
        today: dict[str, Any] | None = None
        previous: dict[str, Any] | None = None
        prev_path: Path | None = None
        csv_path = find_stock_csv(stock_id, trade_date, root=root)
        try:
            if csv_path is not None:
                today = _facts_from_csv(csv_path)
            if today is not None:
                prev_path = previous_csv(stock_id, trade_date, root=root)
                if prev_path is not None:
                    previous = _facts_from_csv(prev_path)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{stock_id} facts 計算失敗：{exc}")
            today = None
        if today is None:
            missing.append(stock_id)
        flags = score_facts(today, previous, held=is_held)
        rows.append(
            {
                "stock_id": stock_id,
                "stock_name": (today or {}).get("stock_name") or "",
                "held": is_held,
                "has_csv": today is not None,
                "has_baseline": previous is not None,
                "prev_trade_date": prev_path.parent.parent.name
                if previous is not None and prev_path is not None
                else None,
                "chip_regime": (today or {}).get("chip_regime") or "unknown",
                "prev_chip_regime": (previous or {}).get("chip_regime"),
                "flags": flags,
                "score": flags_score(flags),
            }
        )

    picks = rank_rows(rows, top_k)
    picked = set(picks)
    for row in rows:
        row["picked"] = row["stock_id"] in picked
        row["summary"] = summary_line(row)
    rows.sort(key=lambda row: (not row["picked"], -row["score"], row["stock_id"]))
    skipped = [row["stock_id"] for row in rows if not row["picked"]]

    over_cap = [
        row["stock_id"] for row in rows if not row["picked"] and row["score"] >= MIN_PICK_SCORE
    ]
    if over_cap:
        notes.append(f"超過 top_k={top_k}，未深挖：{', '.join(over_cap)}")

    return TriageResult(
        ok=True,
        exit_code=EXIT_OK,
        trade_date=trade_date,
        top_k=top_k,
        rows=rows,
        picks=picks,
        skipped=skipped,
        missing=missing,
        notes=notes,
    )


def format_triage(result: dict[str, Any]) -> list[str]:
    """Plain-text table for CLI output; takes the asdict() payload."""
    rows = result.get("rows") or []
    picks = result.get("picks") or []
    lines = [
        f"分流（{result.get('trade_date') or '未定'}）：掃描 {len(rows)} 檔，"
        f"建議深挖 {len(picks)} 檔（top_k={result.get('top_k')}）"
    ]
    for row in rows:
        mark = "深挖" if row.get("picked") else "略過"
        held = " 持有" if row.get("held") else ""
        lines.append(f"  [{mark}] {row.get('score', 0):>2} 分{held}  {row.get('summary')}")
    for note in result.get("notes") or []:
        lines.append(f"  - {note}")
    return lines
