"""Shared data loading for portfolio-build (Phase 1) and portfolio-gate (Phase 3).

Turns the universe JSON + snapshot CSVs into ``PortfolioCandidate`` objects with
deterministic ChipFacts, and resolves the trade date. Kept separate so the pure
rules CLI and the agy gate share one source of truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_UNIVERSE = Path(__file__).with_name("portfolio_universe.json")
DEFAULT_THEME_UNIVERSE = Path(__file__).with_name("portfolio_theme_universe.json")


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_import_paths() -> None:
    root = project_root()
    for sub in ("ui", ".agents/skills/tw-stock-report", ".agents/skills/portfolio-gate"):
        text = str(root / sub)
        if text not in sys.path:
            sys.path.insert(0, text)


def stock_root() -> Path:
    return project_root() / "reports" / "stock"


def resolve_trade_date(explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip()
    root = stock_root()
    if not root.exists():
        return None
    dates = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name[:4].isdigit())
    return dates[-1] if dates else None


def csv_path(trade_date: str, stock_id: str) -> Path:
    return stock_root() / trade_date / f"tw_stock_{stock_id}.csv"


def output_dir(trade_date: str) -> Path:
    return project_root() / "reports" / "portfolio" / trade_date


def build_candidate(entry: dict, trade_date: str):
    """Pair a universe entry with its ChipFacts; facts=None when CSV is absent."""
    ensure_import_paths()
    from chip_signals import build_chip_facts
    from chip_tables import load_csv_row, load_history_rows
    from portfolio_signals import PortfolioCandidate

    stock_id = str(entry["id"]).strip()
    path = csv_path(trade_date, stock_id)
    facts = None
    close_price = None
    if path.exists():
        row = load_csv_row(path)
        if row is not None:
            history = load_history_rows(path)
            facts = build_chip_facts(row, history)
            close_raw = str(row.get("收盤價", "")).strip().replace(",", "")
            try:
                close_price = float(close_raw) if close_raw else None
            except ValueError:
                close_price = None

    raw_themes = entry.get("themes") or []
    if isinstance(raw_themes, str):
        themes = [t.strip() for t in raw_themes.split(",") if t.strip()]
    elif isinstance(raw_themes, list):
        themes = [str(t).strip() for t in raw_themes if str(t).strip()]
    else:
        themes = []

    return PortfolioCandidate(
        stock_id=stock_id,
        name=str(entry.get("name", stock_id)).strip(),
        asset_class=str(entry.get("asset_class", "stock")).strip(),
        category=str(entry.get("category", "blue_chip")).strip(),
        sector=str(entry.get("sector", "unknown")).strip(),
        beginner_core=bool(entry.get("beginner_core", False)),
        defensive=bool(entry.get("defensive", False)),
        themes=themes,
        facts=facts,
        close_price=close_price,
    )


def build_candidates(
    trade_date: str,
    universe_path: Path = DEFAULT_UNIVERSE,
    *,
    with_fundamentals: bool = True,
    force_fundamentals_refresh: bool = False,
) -> list:
    ensure_import_paths()
    import sys

    from portfolio_signals import load_universe

    universe = load_universe(universe_path)
    candidates = [build_candidate(entry, trade_date) for entry in universe]
    if with_fundamentals:
        try:
            from fundamental_signals import attach_fundamentals_to_candidates

            facts_map = attach_fundamentals_to_candidates(
                candidates,
                trade_date,
                force_refresh=force_fundamentals_refresh,
            )
            loaded = sum(1 for f in facts_map.values() if f.available)
            print(
                f"基本面：可用 {loaded}/{len(facts_map)} 檔"
                f"（快取 reports/fundamentals/{trade_date}.json）",
                file=sys.stderr,
            )
        except Exception as exc:
            # Soft-fail: still allow chip-only portfolios when FinMind is down/quota'd.
            print(f"WARNING: 基本面載入失敗，改以籌碼評分：{exc}", file=sys.stderr)
    return candidates
