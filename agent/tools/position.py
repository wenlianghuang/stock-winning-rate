"""run_position_gate / get_holdings — Position agent tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.tools._paths import ensure_paths
from agent.tools._types import (
    EXIT_CSV_MISSING,
    EXIT_FAILED,
    EXIT_HOLDING_MISSING,
    EXIT_OK,
    ToolResult,
    message_for_exit,
)

ensure_paths("position-gate", "report-gate", "tw-stock-report")

from holdings import DEFAULT_HOLDINGS, get_holding, load_holdings, resolve_holding  # type: ignore[import-not-found]  # noqa: E402
from position_gate import find_csv_path, run_gate  # type: ignore[import-not-found]  # noqa: E402


@dataclass
class HoldingsInput:
    stock_id: str | None = None
    path: Path | str | None = None


@dataclass
class HoldingsResult(ToolResult):
    holdings: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class PositionGateInput:
    stock_id: str | None = None
    trade_date: str | None = None
    csv_path: Path | str | None = None
    avg_cost: float | None = None
    share_count: int | None = None
    uses_margin: bool = False
    cash_share_count: int | None = None
    cash_avg_cost: float | None = None
    margin_share_count: int | None = None
    margin_avg_cost: float | None = None
    note: str = ""
    holdings_path: Path | str | None = None
    from_holdings_file: bool = False
    max_rounds: int = 8
    validate_only: bool = False
    skip_pdf: bool = True
    prompt: str = "產出單檔持股部位決策報告"


@dataclass
class PositionGateResult(ToolResult):
    stock_id: str | None = None
    trade_date: str | None = None
    csv_path: str | None = None
    md_path: str | None = None


def get_holdings(inp: HoldingsInput | None = None, **overrides: Any) -> HoldingsResult:
    params = inp or HoldingsInput()
    if overrides:
        data = {"stock_id": params.stock_id, "path": params.path}
        data.update(overrides)
        params = HoldingsInput(**data)
    path = Path(params.path).expanduser().resolve() if params.path else None
    try:
        if params.stock_id:
            record = get_holding(params.stock_id, path)
            payload = {record.stock_id: asdict(record)}
        else:
            payload = {sid: asdict(rec) for sid, rec in load_holdings(path).items()}
    except FileNotFoundError as exc:
        return HoldingsResult(ok=False, exit_code=EXIT_FAILED, error=str(exc))
    except (KeyError, ValueError) as exc:
        return HoldingsResult(ok=False, exit_code=EXIT_HOLDING_MISSING, error=str(exc))
    return HoldingsResult(ok=True, exit_code=EXIT_OK, holdings=payload)


def _catch_agy_exit(exc: BaseException) -> int | None:
    if isinstance(exc, SystemExit):
        code = exc.code
        if isinstance(code, int):
            return code
        return EXIT_FAILED
    return None


def run_position_gate(
    inp: PositionGateInput | None = None,
    **overrides: Any,
) -> PositionGateResult:
    params = inp or PositionGateInput()
    if overrides:
        data = asdict(params)
        data.update(overrides)
        params = PositionGateInput(**data)

    if params.csv_path is not None:
        csv_path = Path(params.csv_path).expanduser().resolve()
        if not csv_path.exists():
            return PositionGateResult(
                ok=False,
                exit_code=EXIT_CSV_MISSING,
                error=f"CSV 不存在：{csv_path}",
                stock_id=params.stock_id,
                trade_date=params.trade_date,
                csv_path=str(csv_path),
            )
        stock_id = params.stock_id or csv_path.stem.replace("tw_stock_", "")
    elif params.stock_id:
        stock_id = params.stock_id.strip()
        csv_path = find_csv_path(stock_id, params.trade_date)
        if csv_path is None:
            hint = f"（日期 {params.trade_date}）" if params.trade_date else ""
            return PositionGateResult(
                ok=False,
                exit_code=EXIT_CSV_MISSING,
                error=f"找不到 tw_stock_{stock_id}.csv {hint}。請先執行 fetch_chips。",
                stock_id=stock_id,
                trade_date=params.trade_date,
            )
    else:
        return PositionGateResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error="run_position_gate 需要 stock_id 或 csv_path",
        )

    holdings_path = (
        Path(params.holdings_path).expanduser().resolve()
        if params.holdings_path
        else DEFAULT_HOLDINGS
    )
    try:
        holding = resolve_holding(
            stock_id,
            holdings_path,
            avg_cost=params.avg_cost,
            shares=params.share_count,
            note=params.note,
            uses_margin=params.uses_margin,
            cash_shares=params.cash_share_count,
            cash_avg_cost=params.cash_avg_cost,
            margin_shares=params.margin_share_count,
            margin_avg_cost=params.margin_avg_cost,
            from_holdings_file=params.from_holdings_file,
        )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        return PositionGateResult(
            ok=False,
            exit_code=EXIT_HOLDING_MISSING,
            error=str(exc),
            stock_id=stock_id,
            csv_path=str(csv_path),
        )

    try:
        code = run_gate(
            csv_path,
            holding,
            max_rounds=max(1, params.max_rounds),
            user_prompt=params.prompt,
            validate_only=params.validate_only,
            skip_pdf=params.skip_pdf,
        )
    except BaseException as exc:
        agy_code = _catch_agy_exit(exc)
        if agy_code is not None:
            return PositionGateResult(
                ok=False,
                exit_code=agy_code,
                error=message_for_exit(agy_code),
                stock_id=stock_id,
                csv_path=str(csv_path),
            )
        return PositionGateResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=str(exc),
            stock_id=stock_id,
            csv_path=str(csv_path),
        )

    md_path = csv_path.with_name(f"{csv_path.stem}_position.md")
    return PositionGateResult(
        ok=code == EXIT_OK,
        exit_code=code,
        error=None if code == EXIT_OK else message_for_exit(code),
        stock_id=stock_id,
        trade_date=params.trade_date or csv_path.parent.name,
        csv_path=str(csv_path),
        md_path=str(md_path) if md_path.exists() else None,
    )
