#!/usr/bin/env python3
"""HTTP API for on-demand Taiwan stock report pipeline (stock-report → report-gate [→ position-gate])."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    import uvicorn
except ImportError as exc:
    print(f"CRITICAL_ERROR: 缺少 server 套件：{exc}", file=sys.stderr)
    print("請執行：uv sync --extra server --extra ui --extra stock", file=sys.stderr)
    raise SystemExit(10) from exc

ROOT = Path(__file__).resolve().parent.parent
STOCK_SCRIPT = ROOT / ".agents" / "skills" / "tw-stock-report" / "fetch_chip_report.py"
GATE_SCRIPT = ROOT / ".agents" / "skills" / "report-gate" / "report_gate.py"
POSITION_SCRIPT = ROOT / ".agents" / "skills" / "position-gate" / "position_gate.py"
STOCK_ROOT = ROOT / "reports" / "stock"


class JobStatus(str, Enum):
    QUEUED = "queued"
    FETCHING = "fetching"
    GATING = "gating"
    POSITIONING = "positioning"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    stock_id: str
    stock_name: str | None = None
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    requested_trade_date: str | None = None
    trade_date: str | None = None
    error: str | None = None
    markdown: str | None = None
    position_markdown: str | None = None
    md_path: str | None = None
    csv_path: str | None = None
    skip_pdf: bool = True
    is_holding: bool = False
    share_count: int | None = None
    avg_cost: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_job(job: Job, **changes: Any) -> None:
    for key, value in changes.items():
        setattr(job, key, value)
    job.updated_at = _now_iso()


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


class CreateJobRequest(BaseModel):
    stock_id: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")
    trade_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="交易日期 YYYY-MM-DD；未指定則使用最近交易日",
    )
    skip_pdf: bool = True
    is_holding: bool = False
    share_count: int | None = Field(default=None, gt=0)
    avg_cost: float | None = Field(default=None, gt=0)


def _run_script(script: Path, args: list[str]) -> int:
    result = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"{script.name} exit {result.returncode}")
    return int(result.returncode)


def _find_md_path(stock_id: str, trade_date: str | None) -> Path | None:
    if trade_date:
        candidate = STOCK_ROOT / trade_date / f"tw_stock_{stock_id}.md"
        return candidate if candidate.exists() else None

    matches = sorted(STOCK_ROOT.glob(f"*/tw_stock_{stock_id}.md"))
    return matches[-1] if matches else None


def _find_position_md_path(stock_id: str, trade_date: str | None) -> Path | None:
    if trade_date:
        candidate = STOCK_ROOT / trade_date / f"tw_stock_{stock_id}_position.md"
        return candidate if candidate.exists() else None

    matches = sorted(STOCK_ROOT.glob(f"*/tw_stock_{stock_id}_position.md"))
    return matches[-1] if matches else None


def _find_csv_path(stock_id: str, trade_date: str | None) -> Path | None:
    if trade_date:
        candidate = STOCK_ROOT / trade_date / f"tw_stock_{stock_id}.csv"
        return candidate if candidate.exists() else None

    matches = sorted(STOCK_ROOT.glob(f"*/tw_stock_{stock_id}.csv"))
    return matches[-1] if matches else None


def _infer_trade_date(stock_id: str) -> str | None:
    csv_path = _find_csv_path(stock_id, None)
    if csv_path is None:
        return None
    return csv_path.parent.name


def _infer_stock_name_from_csv(csv_path: Path) -> str | None:
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        rows = list(csv.DictReader(text.splitlines()))
    except csv.Error:
        return None
    if not rows:
        return None
    raw = str(rows[0].get("名稱", "")).strip()
    return raw or None


def _run_pipeline(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return

    stock_id = job.stock_id
    try:
        with _jobs_lock:
            _update_job(job, status=JobStatus.FETCHING, error=None)

        stock_args = ["--stocks", stock_id]
        if job.requested_trade_date:
            stock_args.extend(["--date", job.requested_trade_date])
        _run_script(STOCK_SCRIPT, stock_args)

        csv_path = _find_csv_path(stock_id, job.requested_trade_date)
        trade_date = csv_path.parent.name if csv_path else _infer_trade_date(stock_id)
        stock_name = _infer_stock_name_from_csv(csv_path) if csv_path else None

        with _jobs_lock:
            _update_job(
                job,
                status=JobStatus.GATING,
                trade_date=trade_date,
                stock_name=stock_name,
            )

        gate_args = [stock_id]
        if job.skip_pdf:
            gate_args.append("--skip-pdf")
        if trade_date:
            gate_args.extend(["--date", trade_date])

        _run_script(GATE_SCRIPT, gate_args)

        md_path = _find_md_path(stock_id, trade_date)
        if md_path is None:
            raise RuntimeError(f"找不到 tw_stock_{stock_id}.md")

        markdown = md_path.read_text(encoding="utf-8")
        csv_path = _find_csv_path(stock_id, trade_date)

        position_markdown: str | None = None
        if job.is_holding:
            if job.share_count is None or job.avg_cost is None:
                raise ValueError("持股分析需要 share_count 與 avg_cost")

            with _jobs_lock:
                _update_job(job, status=JobStatus.POSITIONING)

            position_args = [
                stock_id,
                str(job.avg_cost),
                str(job.share_count),
            ]
            if job.skip_pdf:
                position_args.append("--skip-pdf")
            if trade_date:
                position_args.extend(["--date", trade_date])

            _run_script(POSITION_SCRIPT, position_args)

            position_md_path = _find_position_md_path(stock_id, trade_date)
            if position_md_path is None:
                raise RuntimeError(f"找不到 tw_stock_{stock_id}_position.md")
            position_markdown = position_md_path.read_text(encoding="utf-8")

        with _jobs_lock:
            _update_job(
                job,
                status=JobStatus.DONE,
                markdown=markdown,
                position_markdown=position_markdown,
                md_path=str(md_path.resolve()),
                csv_path=str(csv_path.resolve()) if csv_path else None,
                trade_date=trade_date or md_path.parent.name,
                stock_name=stock_name,
                error=None,
            )
    except Exception as exc:
        with _jobs_lock:
            _update_job(job, status=JobStatus.FAILED, error=str(exc))


def _get_job_or_404(job_id: str) -> Job:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到 job")
    return job


def create_app() -> FastAPI:
    app = FastAPI(title="Stock Winning Rate API", version="0.1.0")

    origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/jobs")
    def create_job(body: CreateJobRequest) -> dict[str, Any]:
        if body.is_holding:
            if body.share_count is None or body.avg_cost is None:
                raise HTTPException(
                    status_code=400,
                    detail="持股分析需要 share_count 與 avg_cost",
                )

        job_id = uuid.uuid4().hex
        job = Job(
            id=job_id,
            stock_id=body.stock_id.strip(),
            skip_pdf=body.skip_pdf,
            requested_trade_date=body.trade_date,
            trade_date=body.trade_date,
            is_holding=body.is_holding,
            share_count=body.share_count,
            avg_cost=body.avg_cost,
        )
        with _jobs_lock:
            _jobs[job_id] = job

        thread = threading.Thread(target=_run_pipeline, args=(job_id,), daemon=True)
        thread.start()

        return {"job": job.to_dict()}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        payload = job.to_dict()
        if job.status != JobStatus.DONE:
            payload.pop("markdown", None)
            payload.pop("position_markdown", None)
        return {"job": payload}

    return app


app = create_app()


def main() -> None:
    port = int(os.environ.get("STOCK_API_PORT", "8765"))
    host = os.environ.get("STOCK_API_HOST", "127.0.0.1")
    print(f"Stock API listening on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
