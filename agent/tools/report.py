"""run_report_gate — wrap report-gate without subprocess."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.tools._paths import ensure_paths
from agent.tools._types import (
    EXIT_CSV_MISSING,
    EXIT_FAILED,
    EXIT_OK,
    ToolResult,
    message_for_exit,
)

ensure_paths("report-gate", "tw-stock-report")

from report_gate import (  # type: ignore[import-not-found]  # noqa: E402
    EXIT_AGY_MISSING as GATE_AGY_MISSING,
)
from report_gate import find_csv_path, run_gate  # type: ignore[import-not-found]


@dataclass
class ReportGateInput:
    stock_id: str | None = None
    trade_date: str | None = None
    csv_path: Path | str | None = None
    max_rounds: int = 8
    validate_only: bool = False
    skip_pdf: bool = True
    prompt: str = "產出單檔台股籌碼深度分析報告"


@dataclass
class ReportGateResult(ToolResult):
    stock_id: str | None = None
    trade_date: str | None = None
    csv_path: str | None = None
    md_path: str | None = None


def _catch_agy_exit(exc: BaseException) -> int | None:
    if isinstance(exc, SystemExit):
        code = exc.code
        if code is None:
            return EXIT_FAILED
        if isinstance(code, int):
            return code
        return EXIT_FAILED
    return None


def run_report_gate(
    inp: ReportGateInput | None = None,
    **overrides: Any,
) -> ReportGateResult:
    params = inp or ReportGateInput()
    if overrides:
        data = {
            "stock_id": params.stock_id,
            "trade_date": params.trade_date,
            "csv_path": params.csv_path,
            "max_rounds": params.max_rounds,
            "validate_only": params.validate_only,
            "skip_pdf": params.skip_pdf,
            "prompt": params.prompt,
        }
        data.update(overrides)
        params = ReportGateInput(**data)

    if params.csv_path is not None:
        csv_path = Path(params.csv_path).expanduser().resolve()
        if not csv_path.exists():
            return ReportGateResult(
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
            return ReportGateResult(
                ok=False,
                exit_code=EXIT_CSV_MISSING,
                error=f"找不到 tw_stock_{stock_id}.csv {hint}。請先執行 fetch_chips。",
                stock_id=stock_id,
                trade_date=params.trade_date,
            )
    else:
        return ReportGateResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error="run_report_gate 需要 stock_id 或 csv_path",
        )

    try:
        code = run_gate(
            csv_path,
            max_rounds=max(1, params.max_rounds),
            user_prompt=params.prompt,
            validate_only=params.validate_only,
            skip_pdf=params.skip_pdf,
        )
    except BaseException as exc:
        agy_code = _catch_agy_exit(exc)
        if agy_code is not None:
            return ReportGateResult(
                ok=False,
                exit_code=agy_code if agy_code else GATE_AGY_MISSING,
                error=message_for_exit(agy_code or GATE_AGY_MISSING),
                stock_id=stock_id,
                csv_path=str(csv_path),
            )
        return ReportGateResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=str(exc),
            stock_id=stock_id,
            csv_path=str(csv_path),
        )

    md_path = csv_path.with_suffix(".md")
    trade_date = params.trade_date or csv_path.parent.name
    return ReportGateResult(
        ok=code == EXIT_OK,
        exit_code=code,
        error=None if code == EXIT_OK else message_for_exit(code),
        stock_id=stock_id,
        trade_date=trade_date,
        csv_path=str(csv_path),
        md_path=str(md_path) if md_path.exists() else None,
    )
