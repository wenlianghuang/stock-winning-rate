"""fetch_chips / build_chip_facts — Data agent tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.tools._paths import ROOT, ensure_paths
from agent.tools._types import EXIT_CSV_MISSING, EXIT_FAILED, EXIT_OK, ToolResult

ensure_paths("tw-stock-report")


DEFAULT_LOOKBACK_DAYS = 5
DEFAULT_CHART_LOOKBACK_DAYS = 60


@dataclass
class FetchChipsInput:
    stocks: list[str] | str | None = None
    trade_date: str | None = None
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    chart_lookback_days: int = DEFAULT_CHART_LOOKBACK_DAYS
    watchlist: Path | None = None
    skip_major: bool = False


@dataclass
class FetchChipsResult(ToolResult):
    trade_date: str | None = None
    date_note: str | None = None
    output_dir: str | None = None
    csv_paths: list[str] = field(default_factory=list)
    history_paths: list[str] = field(default_factory=list)
    chart_history_paths: list[str] = field(default_factory=list)


@dataclass
class BuildChipFactsInput:
    stock_id: str | None = None
    trade_date: str | None = None
    csv_path: Path | str | None = None


@dataclass
class BuildChipFactsResult(ToolResult):
    stock_id: str | None = None
    trade_date: str | None = None
    csv_path: str | None = None
    facts_path: str | None = None
    facts: dict[str, Any] | None = None


def _resolve_snapshot_csv(
    stock_id: str | None,
    trade_date: str | None,
    csv_path: Path | str | None,
) -> Path | None:
    if csv_path is not None:
        path = Path(csv_path)
        return path if path.exists() else None

    if not stock_id:
        return None

    stock_root = ROOT / "reports" / "stock"
    if trade_date:
        candidate = stock_root / trade_date / f"tw_stock_{stock_id}.csv"
        return candidate if candidate.exists() else None
    matches = sorted(stock_root.glob(f"*/tw_stock_{stock_id}.csv"))
    return matches[-1] if matches else None


def fetch_chips(
    inp: FetchChipsInput | None = None,
    **overrides: Any,
) -> FetchChipsResult:
    params = inp or FetchChipsInput()
    if overrides:
        data = {
            "stocks": params.stocks,
            "trade_date": params.trade_date,
            "lookback_days": params.lookback_days,
            "chart_lookback_days": params.chart_lookback_days,
            "watchlist": params.watchlist,
            "skip_major": params.skip_major,
        }
        data.update(overrides)
        params = FetchChipsInput(**data)
    try:
        from fetch_chip_report import run_fetch  # type: ignore[import-not-found]

        run = run_fetch(
            stocks=params.stocks,
            date=params.trade_date,
            lookback_days=params.lookback_days,
            chart_lookback_days=params.chart_lookback_days,
            watchlist=params.watchlist,
            skip_major=params.skip_major,
        )
    except Exception as exc:  # noqa: BLE001
        return FetchChipsResult(ok=False, exit_code=EXIT_FAILED, error=str(exc))
    return FetchChipsResult(
        ok=True,
        exit_code=EXIT_OK,
        trade_date=run.trade_date,
        date_note=run.date_note,
        output_dir=str(run.output_dir),
        csv_paths=[str(p) for p in run.snapshot_paths],
        history_paths=[str(p) for p in run.history_paths],
        chart_history_paths=[str(p) for p in run.chart_history_paths],
    )


def build_chip_facts(inp: BuildChipFactsInput | None = None, **overrides: Any) -> BuildChipFactsResult:
    params = inp or BuildChipFactsInput()
    if overrides:
        data = {
            "stock_id": params.stock_id,
            "trade_date": params.trade_date,
            "csv_path": params.csv_path,
        }
        data.update(overrides)
        params = BuildChipFactsInput(**data)

    csv_path = _resolve_snapshot_csv(params.stock_id, params.trade_date, params.csv_path)
    if csv_path is None:
        hint = params.stock_id or str(params.csv_path or "")
        return BuildChipFactsResult(
            ok=False,
            exit_code=EXIT_CSV_MISSING,
            error=f"找不到 CSV：{hint}。請先執行 fetch_chips。",
            stock_id=params.stock_id,
            trade_date=params.trade_date,
        )

    ensure_paths("tw-stock-report")
    from chip_signals import build_chip_facts as compute_facts  # type: ignore[import-not-found]
    from chip_signals import facts_to_json, write_facts_json  # type: ignore[import-not-found]
    from chip_tables import load_csv_row, load_history_rows  # type: ignore[import-not-found]

    row = load_csv_row(csv_path)
    if row is None:
        return BuildChipFactsResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=f"CSV 不是單列快照：{csv_path}",
            csv_path=str(csv_path),
        )

    history = load_history_rows(csv_path)
    try:
        facts_obj = compute_facts(row, history)
        facts_path = csv_path.with_suffix(".facts.json")
        write_facts_json(facts_path, facts_obj)
        payload = facts_to_json(facts_obj)
    except Exception as exc:  # noqa: BLE001
        return BuildChipFactsResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=str(exc),
            csv_path=str(csv_path),
        )

    stock_id = params.stock_id or str(row.get("代碼") or csv_path.stem.replace("tw_stock_", ""))
    trade_date = params.trade_date or csv_path.parent.name
    return BuildChipFactsResult(
        ok=True,
        exit_code=EXIT_OK,
        stock_id=stock_id,
        trade_date=trade_date,
        csv_path=str(csv_path),
        facts_path=str(facts_path),
        facts=payload,
    )
