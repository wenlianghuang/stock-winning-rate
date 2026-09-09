#!/usr/bin/env python3
"""HTTP API for on-demand Taiwan stock report pipeline (stock-report → report-gate [→ position-gate])."""

from __future__ import annotations

import csv
import json
import os
import re
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
    from fastapi.responses import FileResponse, StreamingResponse
    from pydantic import BaseModel, Field
    import uvicorn
except ImportError as exc:
    print(f"CRITICAL_ERROR: 缺少 server 套件：{exc}", file=sys.stderr)
    print("請執行：uv sync --extra server --extra ui --extra stock", file=sys.stderr)
    raise SystemExit(10) from exc

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ModuleNotFoundError:
    pass
STOCK_SKILL_DIR = ROOT / ".agents" / "skills" / "tw-stock-report"
if str(STOCK_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(STOCK_SKILL_DIR))
STOCK_ROOT = ROOT / "reports" / "stock"
PORTFOLIO_ROOT = ROOT / "reports" / "portfolio"
PORTFOLIO_SKILL_DIR = ROOT / ".agents" / "skills" / "portfolio-gate"
PORTFOLIO_GATE_SCRIPT = PORTFOLIO_SKILL_DIR / "portfolio_gate.py"
PORTFOLIO_PROFILES = ("conservative", "balanced", "aggressive")
PORTFOLIO_MIN_AMOUNT = 50_000
MARKET_WEEKLY_SKILL_DIR = ROOT / ".agents" / "skills" / "market-weekly"
MARKET_WEEKLY_SCRIPT = MARKET_WEEKLY_SKILL_DIR / "market_weekly_gate.py"
MARKET_DAILY_SKILL_DIR = ROOT / ".agents" / "skills" / "market-daily"
MARKET_ROOT = ROOT / "reports" / "market"
WEB_ROOT = ROOT / "web"
CHART_LOOKBACK_DAYS = 60


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
    facts_json: dict[str, Any] | None = None
    history_json: list[dict[str, Any]] | None = None
    summary_json: dict[str, Any] | None = None
    skip_pdf: bool = True
    is_holding: bool = False
    share_count: int | None = None
    avg_cost: float | None = None
    uses_margin: bool = False
    cash_share_count: int | None = None
    cash_avg_cost: float | None = None
    margin_share_count: int | None = None
    margin_avg_cost: float | None = None

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

_portfolio_jobs: dict[str, "PortfolioJob"] = {}
_portfolio_jobs_lock = threading.Lock()

_market_weekly_jobs: dict[str, "MarketWeeklyJob"] = {}
_market_weekly_jobs_lock = threading.Lock()

_market_daily_jobs: dict[str, "MarketDailyJob"] = {}
_market_daily_jobs_lock = threading.Lock()


@dataclass
class MarketWeeklyJob:
    id: str
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    as_of: str | None = None
    week_end: str | None = None
    week_start: str | None = None
    error: str | None = None
    skip_fetch: bool = False
    skip_news: bool = False
    force: bool = False
    facts: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    markdown: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _update_market_weekly_job(job: MarketWeeklyJob, **changes: Any) -> None:
    for key, value in changes.items():
        setattr(job, key, value)
    job.updated_at = _now_iso()


@dataclass
class MarketDailyJob:
    id: str
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    as_of: str | None = None
    trade_date: str | None = None
    for_session: str | None = None
    error: str | None = None
    skip_fetch: bool = False
    skip_us: bool = False
    force: bool = False
    facts: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    markdown: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _update_market_daily_job(job: MarketDailyJob, **changes: Any) -> None:
    for key, value in changes.items():
        setattr(job, key, value)
    job.updated_at = _now_iso()


@dataclass
class PortfolioJob:
    id: str
    profile: str
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    amount: int | None = None
    requested_trade_date: str | None = None
    trade_date: str | None = None
    error: str | None = None
    portfolio: dict[str, Any] | None = None
    skip_pdf: bool = True
    mode: str = "beginner"
    themes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _update_portfolio_job(job: PortfolioJob, **changes: Any) -> None:
    for key, value in changes.items():
        setattr(job, key, value)
    job.updated_at = _now_iso()


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
    uses_margin: bool = False
    cash_share_count: int | None = Field(default=None, ge=0)
    cash_avg_cost: float | None = Field(default=None, gt=0)
    margin_share_count: int | None = Field(default=None, ge=0)
    margin_avg_cost: float | None = Field(default=None, gt=0)


class DigestItem(BaseModel):
    stock_id: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")
    stock_name: str | None = None
    trade_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    markdown: str = Field(..., min_length=20)
    position_markdown: str | None = None


class CreateDigestRequest(BaseModel):
    digest_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    items: list[DigestItem] = Field(..., min_length=1)


class CreatePortfolioJobRequest(BaseModel):
    mode: str = Field(default="beginner", pattern=r"^(beginner|theme)$")
    profile: str | None = Field(
        default=None,
        pattern=r"^(conservative|balanced|aggressive)$",
        description="新手模式必填；主題模式可省略",
    )
    themes: list[str] | None = Field(
        default=None,
        description="主題模式必填，例如 ['financials'] 或 ['financials','thermal']",
    )
    amount: int = Field(..., ge=PORTFOLIO_MIN_AMOUNT)
    trade_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    skip_pdf: bool = True
    force: bool = False


class CreateMarketWeeklyJobRequest(BaseModel):
    as_of: str | None = Field(
        default=None,
        description="覆寫當下時間 ISO 或 YYYY-MM-DD（cutover 測試）",
    )
    week_end: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="強制週最後交易日（略過 cutover）",
    )
    skip_fetch: bool = False
    skip_news: bool = False
    force: bool = False
    max_rounds: int = Field(default=6, ge=1, le=12)


class CreateMarketDailyJobRequest(BaseModel):
    as_of: str | None = Field(
        default=None,
        description="覆寫當下時間 ISO 或 YYYY-MM-DD（cutover 測試）",
    )
    trade_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="強制籌碼 trade_date（略過 cutover）",
    )
    skip_fetch: bool = False
    skip_us: bool = False
    force: bool = False
    max_rounds: int = Field(default=6, ge=1, le=12)


class MarketDailyChatHistoryItem(BaseModel):
    role: str = Field(description="user 或 assistant")
    content: str


class MarketDailyChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    trade_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="若未帶 artifacts，則從此日期載入報告",
    )
    facts: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    markdown: str | None = None
    has_holdings: bool = False
    holdings: list[dict[str, Any]] = Field(default_factory=list)
    history: list[MarketDailyChatHistoryItem] = Field(default_factory=list)
    use_llm: bool = True
    skip_tavily: bool = False


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


def _ensure_import_paths() -> None:
    ui_path = ROOT / "ui"
    ui_text = str(ui_path)
    if ui_text not in sys.path:
        sys.path.insert(0, ui_text)


def _tool_error(result: Any, fallback: str) -> None:
    if getattr(result, "ok", False):
        return
    detail = getattr(result, "error", None) or fallback
    raise RuntimeError(detail)


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


def _find_facts_path(stock_id: str, trade_date: str | None) -> Path | None:
    if trade_date:
        candidate = STOCK_ROOT / trade_date / f"tw_stock_{stock_id}.facts.json"
        return candidate if candidate.exists() else None

    matches = sorted(STOCK_ROOT.glob(f"*/tw_stock_{stock_id}.facts.json"))
    return matches[-1] if matches else None


def _find_history_path(stock_id: str, trade_date: str | None) -> Path | None:
    if trade_date:
        candidate = STOCK_ROOT / trade_date / f"tw_stock_{stock_id}_history.csv"
        return candidate if candidate.exists() else None

    matches = sorted(STOCK_ROOT.glob(f"*/tw_stock_{stock_id}_history.csv"))
    return matches[-1] if matches else None


def _find_chart_history_path(stock_id: str, trade_date: str | None) -> Path | None:
    if trade_date:
        candidate = STOCK_ROOT / trade_date / f"tw_stock_{stock_id}_chart_history.csv"
        return candidate if candidate.exists() else None

    matches = sorted(STOCK_ROOT.glob(f"*/tw_stock_{stock_id}_chart_history.csv"))
    return matches[-1] if matches else None


def _resolve_history_path(stock_id: str, trade_date: str | None) -> Path | None:
    chart_path = _find_chart_history_path(stock_id, trade_date)
    if chart_path is not None:
        return chart_path
    return _find_history_path(stock_id, trade_date)


def _fetch_stock_chart_data(stock_id: str, trade_date: str | None) -> None:
    """Fetch chip/price CSVs for one stock (no report-gate / no markdown)."""
    from agent.tools.chips import FetchChipsInput, fetch_chips

    result = fetch_chips(
        FetchChipsInput(
            stocks=[stock_id],
            trade_date=trade_date,
            chart_lookback_days=CHART_LOOKBACK_DAYS,
        )
    )
    _tool_error(result, "fetch_chips failed")


def _load_stock_chart_payload(
    stock_id: str,
    date: str | None,
) -> dict[str, Any] | None:
    history_path = _resolve_history_path(stock_id, date)
    history_json = _load_history_for_api(history_path)
    if not history_json:
        return None

    trade_date = date or (history_path.parent.name if history_path else None)
    csv_path = _find_csv_path(stock_id, trade_date)
    stock_name = _infer_stock_name_from_csv(csv_path) if csv_path else None
    facts_json = _load_facts_json(_find_facts_path(stock_id, trade_date))

    return {
        "stock_id": stock_id,
        "stock_name": stock_name,
        "trade_date": trade_date,
        "history_json": history_json,
        "facts_json": facts_json,
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "nan", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _find_summary_path(stock_id: str, trade_date: str | None) -> Path | None:
    if trade_date:
        candidate = STOCK_ROOT / trade_date / f"tw_stock_{stock_id}.summary.json"
        return candidate if candidate.exists() else None

    matches = sorted(STOCK_ROOT.glob(f"*/tw_stock_{stock_id}.summary.json"))
    return matches[-1] if matches else None


def _load_summary_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_facts_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_history_for_api(path: Path | None) -> list[dict[str, Any]] | None:
    if path is None or not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        rows = list(csv.DictReader(text.splitlines()))
    except csv.Error:
        return None

    history: list[dict[str, Any]] = []
    for row in rows:
        date = str(row.get("日期", "")).strip()
        close = _to_float(row.get("收盤價"))
        if not date or close is None:
            continue
        entry: dict[str, Any] = {"date": date, "close": close}
        open_price = _to_float(row.get("開盤價"))
        if open_price is not None:
            entry["open"] = open_price
        high = _to_float(row.get("最高價"))
        if high is not None:
            entry["high"] = high
        low = _to_float(row.get("最低價"))
        if low is not None:
            entry["low"] = low
        volume = _to_int(row.get("成交量_張"))
        if volume is not None:
            entry["volume"] = volume
        change_pct = _to_float(row.get("漲跌幅"))
        if change_pct is not None:
            entry["change_pct"] = change_pct
        history.append(entry)
    return history or None


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
    from agent.tools.chips import FetchChipsInput, fetch_chips
    from agent.tools.position import PositionGateInput, run_position_gate
    from agent.tools.report import ReportGateInput, run_report_gate

    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return

    stock_id = job.stock_id
    try:
        with _jobs_lock:
            _update_job(job, status=JobStatus.FETCHING, error=None)

        fetch_result = fetch_chips(
            FetchChipsInput(
                stocks=[stock_id],
                trade_date=job.requested_trade_date,
                chart_lookback_days=CHART_LOOKBACK_DAYS,
            )
        )
        _tool_error(fetch_result, "fetch_chips failed")

        trade_date = fetch_result.trade_date or job.requested_trade_date
        csv_path = _find_csv_path(stock_id, trade_date)
        stock_name = _infer_stock_name_from_csv(csv_path) if csv_path else None

        with _jobs_lock:
            _update_job(
                job,
                status=JobStatus.GATING,
                trade_date=trade_date,
                stock_name=stock_name,
            )

        gate_result = run_report_gate(
            ReportGateInput(
                stock_id=stock_id,
                trade_date=trade_date,
                skip_pdf=job.skip_pdf,
            )
        )
        _tool_error(gate_result, "run_report_gate failed")

        md_path = _find_md_path(stock_id, trade_date)
        if md_path is None:
            raise RuntimeError(f"找不到 tw_stock_{stock_id}.md")

        markdown = md_path.read_text(encoding="utf-8")
        csv_path = _find_csv_path(stock_id, trade_date)
        facts_path = _find_facts_path(stock_id, trade_date)
        history_path = _resolve_history_path(stock_id, trade_date)
        facts_json = _load_facts_json(facts_path)
        history_json = _load_history_for_api(history_path)
        summary_path = _find_summary_path(stock_id, trade_date)
        summary_json = _load_summary_json(summary_path)

        position_markdown: str | None = None
        if job.is_holding:
            cash_n = int(job.cash_share_count or 0)
            margin_n = int(job.margin_share_count or 0)
            has_legs = cash_n > 0 or margin_n > 0
            if has_legs:
                if cash_n > 0 and job.cash_avg_cost is None:
                    raise ValueError("現股分析需要 cash_avg_cost")
                if margin_n > 0 and job.margin_avg_cost is None:
                    raise ValueError("融資分析需要 margin_avg_cost")
            elif job.share_count is None or job.avg_cost is None:
                raise ValueError("持股分析需要 share_count 與 avg_cost，或現股／融資分腿")

            with _jobs_lock:
                _update_job(job, status=JobStatus.POSITIONING)

            position_result = run_position_gate(
                PositionGateInput(
                    stock_id=stock_id,
                    trade_date=trade_date,
                    avg_cost=job.avg_cost,
                    share_count=job.share_count,
                    uses_margin=job.uses_margin,
                    cash_share_count=job.cash_share_count,
                    cash_avg_cost=job.cash_avg_cost,
                    margin_share_count=job.margin_share_count,
                    margin_avg_cost=job.margin_avg_cost,
                    skip_pdf=job.skip_pdf,
                )
            )
            _tool_error(position_result, "run_position_gate failed")

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
                facts_json=facts_json,
                history_json=history_json,
                summary_json=summary_json,
                trade_date=trade_date or md_path.parent.name,
                stock_name=stock_name,
                error=None,
            )
    except Exception as exc:
        with _jobs_lock:
            _update_job(job, status=JobStatus.FAILED, error=str(exc))


def _ensure_portfolio_paths() -> None:
    _ensure_import_paths()
    text = str(PORTFOLIO_SKILL_DIR)
    if text not in sys.path:
        sys.path.insert(0, text)


def _load_portfolio_narrative(profile: str, trade_date: str) -> tuple[str | None, str]:
    """Prefer the standalone agy narrative; fall back to splitting the gate .md."""
    out_dir = PORTFOLIO_ROOT / trade_date
    narrative_path = out_dir / f"portfolio_{profile}.narrative.md"
    if narrative_path.exists():
        text = narrative_path.read_text(encoding="utf-8").strip()
        if text:
            return text, "agy"

    md_path = out_dir / f"portfolio_{profile}.md"
    if md_path.exists():
        raw = md_path.read_text(encoding="utf-8")
        if "## 報告資訊" in raw:  # gate-authored report: header --- tables --- narrative
            parts = [part.strip() for part in raw.split("\n---\n")]
            if len(parts) >= 3 and parts[-1]:
                return parts[-1], "agy"
    return None, "rules"


def _parse_theme_query(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip()]


def _build_portfolio_payload(
    profile: str | None,
    amount: int | None,
    date: str | None,
    *,
    mode: str = "beginner",
    themes: list[str] | None = None,
) -> dict[str, Any]:
    _ensure_portfolio_paths()
    from chip_signals import load_base_rates
    from portfolio_data import (
        DEFAULT_THEME_UNIVERSE,
        DEFAULT_UNIVERSE,
        build_candidates,
        resolve_trade_date,
    )
    from portfolio_signals import (
        build_portfolio_facts,
        build_theme_portfolio_facts,
        load_theme_catalog,
        sector_label,
        theme_slug,
    )

    trade_date = resolve_trade_date(date)
    if not trade_date:
        raise RuntimeError("找不到可用交易日，請先在『台股籌碼報告』產生當日個股資料。")

    if mode == "theme":
        theme_list = [t.strip().lower() for t in (themes or []) if str(t).strip()]
        if not theme_list:
            raise RuntimeError("主題模式須指定 themes（例如 financials 或 financials,thermal）")
        universe_path = DEFAULT_THEME_UNIVERSE
        candidates = build_candidates(trade_date, universe_path)
        if not any(getattr(c, "data_available", False) for c in candidates):
            raise RuntimeError(
                f"{trade_date} 尚無主題候選股資料，請先於『台股籌碼報告』抓取主題池個股。"
            )
        catalog = load_theme_catalog(universe_path)
        facts = build_theme_portfolio_facts(
            candidates,
            themes=theme_list,
            base_rates=load_base_rates(),
            amount_twd=amount,
            trade_date=trade_date,
            theme_catalog=catalog,
        )
        artifact_key = theme_slug(theme_list)
    else:
        if profile not in PORTFOLIO_PROFILES:
            raise RuntimeError("profile 須為 conservative / balanced / aggressive")
        candidates = build_candidates(trade_date, DEFAULT_UNIVERSE)
        if not any(getattr(c, "data_available", False) for c in candidates):
            raise RuntimeError(
                f"{trade_date} 尚無候選股資料，請先於『台股籌碼報告』抓取個股，或改用有資料的日期。"
            )
        facts = build_portfolio_facts(
            candidates,
            profile=profile,
            base_rates=load_base_rates(),
            amount_twd=amount,
            trade_date=trade_date,
        )
        artifact_key = profile

    data = asdict(facts)
    for holding in data.get("holdings", []):
        holding["sector_label"] = sector_label(str(holding.get("sector", "")))
    data["top_sector_label"] = sector_label(str(data.get("top_sector", "")))

    narrative, via = _load_portfolio_narrative(artifact_key, trade_date)
    return {
        "facts": data,
        "narrative": narrative,
        "has_narrative": narrative is not None,
        "generated_via": via,
        "artifact_key": artifact_key,
    }


def _get_job_or_404(job_id: str) -> Job:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到 job")
    return job


def _get_portfolio_job_or_404(job_id: str) -> PortfolioJob:
    with _portfolio_jobs_lock:
        job = _portfolio_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到 portfolio job")
    return job


def _run_portfolio_pipeline(job_id: str) -> None:
    with _portfolio_jobs_lock:
        job = _portfolio_jobs.get(job_id)
        if job is None:
            return

    try:
        with _portfolio_jobs_lock:
            _update_portfolio_job(job, status=JobStatus.GATING, error=None)

        gate_args: list[str] = ["--mode", job.mode]
        if job.mode == "theme":
            gate_args.extend(["--themes", ",".join(job.themes)])
        else:
            gate_args.insert(0, job.profile)
        if job.skip_pdf:
            gate_args.append("--skip-pdf")
        if job.amount is not None:
            gate_args.extend(["--amount", str(job.amount)])
        trade_date = job.trade_date or job.requested_trade_date
        if trade_date:
            gate_args.extend(["--date", trade_date])

        _run_script(PORTFOLIO_GATE_SCRIPT, gate_args)

        payload = _build_portfolio_payload(
            job.profile if job.mode == "beginner" else None,
            job.amount,
            trade_date,
            mode=job.mode,
            themes=job.themes if job.mode == "theme" else None,
        )
        if not payload.get("has_narrative"):
            raise RuntimeError("portfolio-gate 已結束，但仍找不到白話說明檔案")

        with _portfolio_jobs_lock:
            _update_portfolio_job(
                job,
                status=JobStatus.DONE,
                portfolio=payload,
                trade_date=str(payload.get("facts", {}).get("trade_date") or trade_date),
                error=None,
            )
    except Exception as exc:
        with _portfolio_jobs_lock:
            _update_portfolio_job(job, status=JobStatus.FAILED, error=str(exc))


def _load_market_weekly_artifacts(week_end: str) -> dict[str, Any]:
    out_dir = MARKET_ROOT / week_end
    facts_file = out_dir / "tw_market_weekly.facts.json"
    summary_file = out_dir / "tw_market_weekly.summary.json"
    md_file = out_dir / "tw_market_weekly.md"
    facts = (
        json.loads(facts_file.read_text(encoding="utf-8"))
        if facts_file.exists()
        else None
    )
    summary = (
        json.loads(summary_file.read_text(encoding="utf-8"))
        if summary_file.exists()
        else None
    )
    markdown = md_file.read_text(encoding="utf-8") if md_file.exists() else None
    return {"facts": facts, "summary": summary, "markdown": markdown}


def _run_market_weekly_pipeline(job_id: str, max_rounds: int) -> None:
    with _market_weekly_jobs_lock:
        job = _market_weekly_jobs.get(job_id)
    if job is None:
        return
    try:
        with _market_weekly_jobs_lock:
            _update_market_weekly_job(job, status=JobStatus.GATING, error=None)
        args: list[str] = ["--max-rounds", str(max_rounds)]
        if job.week_end:
            args.extend(["--week-end", job.week_end])
        elif job.as_of:
            args.extend(["--as-of", job.as_of])
        if job.skip_fetch:
            args.append("--skip-fetch")
        if job.skip_news:
            args.append("--skip-news")
        _run_script(MARKET_WEEKLY_SCRIPT, args)
        week_end = job.week_end
        if not week_end:
            raise RuntimeError("market-weekly job 缺少 week_end")
        artifacts = _load_market_weekly_artifacts(week_end)
        if not artifacts.get("markdown"):
            raise RuntimeError("market-weekly 已結束，但仍找不到報告 Markdown")
        with _market_weekly_jobs_lock:
            _update_market_weekly_job(
                job,
                status=JobStatus.DONE,
                facts=artifacts.get("facts"),
                summary=artifacts.get("summary"),
                markdown=artifacts.get("markdown"),
                error=None,
            )
    except Exception as exc:
        with _market_weekly_jobs_lock:
            _update_market_weekly_job(job, status=JobStatus.FAILED, error=str(exc))


def _load_market_daily_artifacts(trade_date: str) -> dict[str, Any]:
    out_dir = MARKET_ROOT / trade_date
    facts_file = out_dir / "tw_market_daily.facts.json"
    summary_file = out_dir / "tw_market_daily.summary.json"
    md_file = out_dir / "tw_market_daily.md"
    facts = (
        json.loads(facts_file.read_text(encoding="utf-8"))
        if facts_file.exists()
        else None
    )
    summary = (
        json.loads(summary_file.read_text(encoding="utf-8"))
        if summary_file.exists()
        else None
    )
    markdown = md_file.read_text(encoding="utf-8") if md_file.exists() else None
    return {"facts": facts, "summary": summary, "markdown": markdown}


def _run_market_daily_pipeline(job_id: str, max_rounds: int) -> None:
    from agent.tools.market import MarketDailyInput, run_market_daily

    with _market_daily_jobs_lock:
        job = _market_daily_jobs.get(job_id)
    if job is None:
        return
    try:
        with _market_daily_jobs_lock:
            _update_market_daily_job(job, status=JobStatus.GATING, error=None)
        daily_result = run_market_daily(
            MarketDailyInput(
                as_of=job.as_of,
                trade_date=job.trade_date,
                max_rounds=max_rounds,
                skip_fetch=job.skip_fetch,
                skip_us=job.skip_us,
            )
        )
        _tool_error(daily_result, "run_market_daily failed")
        trade_date = daily_result.trade_date or job.trade_date
        if not trade_date:
            raise RuntimeError("market-daily job 缺少 trade_date")
        artifacts = _load_market_daily_artifacts(trade_date)
        if not artifacts.get("markdown"):
            raise RuntimeError("market-daily 已結束，但仍找不到報告 Markdown")
        with _market_daily_jobs_lock:
            _update_market_daily_job(
                job,
                status=JobStatus.DONE,
                facts=artifacts.get("facts"),
                summary=artifacts.get("summary"),
                markdown=artifacts.get("markdown"),
                error=None,
            )
    except Exception as exc:
        with _market_daily_jobs_lock:
            _update_market_daily_job(job, status=JobStatus.FAILED, error=str(exc))


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

    @app.get("/last-trading-date")
    def last_trading_date(date: str | None = None) -> dict[str, Any]:
        from agent.tools.dates import get_last_trading_date

        result = get_last_trading_date(date=date)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error or "無法解析交易日")
        return {
            "reference_date": result.reference_date,
            "trade_date": result.trade_date,
            "note": result.note,
        }

    @app.get("/stocks/{stock_id}/chart")
    def get_stock_chart(
        stock_id: str,
        date: str | None = None,
        fetch: bool = True,
    ) -> dict[str, Any]:
        """Return price history + chip facts for website K-line charts.

        Reads existing CSVs first. If missing and ``fetch=true`` (default),
        runs stock-report for this stock only (no report-gate / no markdown).
        """
        stock_id = stock_id.strip()
        if not re.match(r"^\d{4,6}$", stock_id):
            raise HTTPException(status_code=400, detail="stock_id 須為 4～6 位數台股代號")
        if date is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            raise HTTPException(status_code=400, detail="date 格式須為 YYYY-MM-DD")

        payload = _load_stock_chart_payload(stock_id, date)
        if payload is None and fetch:
            try:
                _fetch_stock_chart_data(stock_id, date)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"圖表資料抓取失敗：{exc}",
                ) from exc
            payload = _load_stock_chart_payload(stock_id, date)

        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"找不到 {stock_id} 的圖表資料。",
            )
        return payload

    @app.get("/us-indices")
    def us_indices() -> dict[str, Any]:
        _ensure_import_paths()
        try:
            from us_indices import fetch_us_market_indices_payload

            return fetch_us_market_indices_payload()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"美股指數抓取失敗：{exc}") from exc

    @app.get("/")
    def web_home() -> FileResponse:
        index_path = WEB_ROOT / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="找不到 web/index.html")
        return FileResponse(index_path, media_type="text/html; charset=utf-8")

    @app.post("/digest")
    def create_digest(body: CreateDigestRequest) -> dict[str, Any]:
        from agent.tools.digest import DigestItem as ToolDigestItem
        from agent.tools.digest import draft_digest

        try:
            items = [
                ToolDigestItem(
                    stock_id=item.stock_id,
                    stock_name=item.stock_name,
                    trade_date=item.trade_date,
                    markdown=item.markdown,
                    position_markdown=item.position_markdown,
                )
                for item in body.items
            ]
            result = draft_digest(digest_date=body.digest_date, items=items)
            if not result.ok:
                raise RuntimeError(result.error or "draft_digest failed")
            return {
                "digest": {
                    "subject": result.subject,
                    "main_detail_markdown": result.main_detail_markdown,
                }
            }
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail=f"Digest JSON 解析失敗：{exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/portfolio/themes")
    def list_portfolio_themes() -> dict[str, Any]:
        _ensure_portfolio_paths()
        from portfolio_data import DEFAULT_THEME_UNIVERSE
        from portfolio_signals import load_theme_catalog

        catalog = load_theme_catalog(DEFAULT_THEME_UNIVERSE)
        themes = [
            {"id": tid, **meta}
            for tid, meta in sorted(catalog.items())
        ]
        return {"themes": themes}

    @app.get("/portfolio")
    def get_portfolio(
        profile: str | None = None,
        amount: int | None = None,
        date: str | None = None,
        mode: str = "beginner",
        themes: str | None = None,
    ) -> dict[str, Any]:
        if mode not in ("beginner", "theme"):
            raise HTTPException(status_code=400, detail="mode 須為 beginner 或 theme")
        if amount is not None and amount < PORTFOLIO_MIN_AMOUNT:
            raise HTTPException(
                status_code=400,
                detail=f"amount 須 ≥ {PORTFOLIO_MIN_AMOUNT}",
            )
        if date is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            raise HTTPException(status_code=400, detail="date 格式須為 YYYY-MM-DD")
        theme_list = _parse_theme_query(themes)
        if mode == "beginner":
            if not profile or profile not in PORTFOLIO_PROFILES:
                raise HTTPException(
                    status_code=400,
                    detail="新手模式 profile 須為 conservative / balanced / aggressive",
                )
        elif not theme_list:
            raise HTTPException(
                status_code=400,
                detail="主題模式須指定 themes（例如 financials 或 financials,thermal）",
            )
        try:
            payload = _build_portfolio_payload(
                profile,
                amount,
                date,
                mode=mode,
                themes=theme_list or None,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"組合產生失敗：{exc}") from exc
        return {"portfolio": payload}

    @app.post("/portfolio/jobs")
    def create_portfolio_job(body: CreatePortfolioJobRequest) -> dict[str, Any]:
        mode = body.mode or "beginner"
        theme_list = [t.strip().lower() for t in (body.themes or []) if str(t).strip()]
        if mode == "beginner":
            if not body.profile or body.profile not in PORTFOLIO_PROFILES:
                raise HTTPException(
                    status_code=400,
                    detail="新手模式 profile 須為 conservative / balanced / aggressive",
                )
        elif not theme_list:
            raise HTTPException(
                status_code=400,
                detail="主題模式須指定 themes（例如 ['financials']）",
            )

        try:
            payload = _build_portfolio_payload(
                body.profile,
                body.amount,
                body.trade_date,
                mode=mode,
                themes=theme_list or None,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"組合產生失敗：{exc}") from exc

        trade_date = str(payload.get("facts", {}).get("trade_date") or body.trade_date or "")
        artifact_key = str(payload.get("artifact_key") or body.profile or "theme")
        job_id = uuid.uuid4().hex
        has_narrative = bool(payload.get("has_narrative")) and not body.force
        job = PortfolioJob(
            id=job_id,
            profile=artifact_key,
            amount=body.amount,
            requested_trade_date=body.trade_date,
            trade_date=trade_date or None,
            portfolio=payload,
            skip_pdf=body.skip_pdf,
            status=JobStatus.DONE if has_narrative else JobStatus.GATING,
            mode=mode,
            themes=theme_list,
        )
        with _portfolio_jobs_lock:
            _portfolio_jobs[job_id] = job

        if not has_narrative:
            thread = threading.Thread(
                target=_run_portfolio_pipeline,
                args=(job_id,),
                daemon=True,
            )
            thread.start()

        return {"job": job.to_dict()}

    @app.get("/portfolio/jobs/{job_id}")
    def get_portfolio_job(job_id: str) -> dict[str, Any]:
        job = _get_portfolio_job_or_404(job_id)
        return {"job": job.to_dict()}

    @app.get("/market-weekly/resolve")
    def resolve_market_weekly(
        as_of: str | None = None,
        week_end: str | None = None,
    ) -> dict[str, Any]:
        if week_end is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", week_end):
            raise HTTPException(status_code=400, detail="week_end 格式須為 YYYY-MM-DD")
        skill = str(MARKET_WEEKLY_SKILL_DIR)
        if skill not in sys.path:
            sys.path.insert(0, skill)
        try:
            from market_week_signals import resolve_window_or_fail

            window = resolve_window_or_fail(as_of=as_of, week_end=week_end)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"window": window.as_dict()}

    @app.get("/market-weekly")
    def list_market_weekly() -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if MARKET_ROOT.exists():
            for day_dir in sorted(MARKET_ROOT.iterdir(), reverse=True):
                if not day_dir.is_dir():
                    continue
                summary_file = day_dir / "tw_market_weekly.summary.json"
                facts_file = day_dir / "tw_market_weekly.facts.json"
                md_file = day_dir / "tw_market_weekly.md"
                if not summary_file.exists() and not facts_file.exists():
                    continue
                summary = None
                facts = None
                markdown = None
                if summary_file.exists():
                    summary = json.loads(summary_file.read_text(encoding="utf-8"))
                if facts_file.exists():
                    facts = json.loads(facts_file.read_text(encoding="utf-8"))
                if md_file.exists():
                    markdown = md_file.read_text(encoding="utf-8")
                items.append(
                    {
                        "week_end": day_dir.name,
                        "week_start": (facts or summary or {}).get("week_start"),
                        "summary": summary,
                        "facts": facts,
                        "markdown": markdown,
                        "has_report": md_file.exists(),
                    }
                )
        return {"items": items[:30]}

    @app.get("/market-weekly/jobs/{job_id}")
    def get_market_weekly_job(job_id: str) -> dict[str, Any]:
        with _market_weekly_jobs_lock:
            job = _market_weekly_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="找不到 market-weekly job")
        return {"job": job.to_dict()}

    @app.post("/market-weekly/jobs")
    def create_market_weekly_job(body: CreateMarketWeeklyJobRequest) -> dict[str, Any]:
        skill = str(MARKET_WEEKLY_SKILL_DIR)
        if skill not in sys.path:
            sys.path.insert(0, skill)
        try:
            from market_week_signals import resolve_window_or_fail

            window = resolve_window_or_fail(as_of=body.as_of, week_end=body.week_end)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        out_dir = MARKET_ROOT / window.week_end
        md_path = out_dir / "tw_market_weekly.md"
        facts_path = out_dir / "tw_market_weekly.facts.json"
        summary_path = out_dir / "tw_market_weekly.summary.json"

        job_id = uuid.uuid4().hex
        reuse = (
            not body.force
            and md_path.exists()
            and facts_path.exists()
            and summary_path.exists()
        )
        job = MarketWeeklyJob(
            id=job_id,
            as_of=body.as_of,
            week_end=window.week_end,
            week_start=window.week_start,
            skip_fetch=body.skip_fetch,
            skip_news=body.skip_news,
            force=body.force,
            status=JobStatus.DONE if reuse else JobStatus.GATING,
        )
        if reuse:
            job.facts = json.loads(facts_path.read_text(encoding="utf-8"))
            job.summary = json.loads(summary_path.read_text(encoding="utf-8"))
            job.markdown = md_path.read_text(encoding="utf-8")

        with _market_weekly_jobs_lock:
            _market_weekly_jobs[job_id] = job

        if not reuse:
            thread = threading.Thread(
                target=_run_market_weekly_pipeline,
                args=(job_id, body.max_rounds),
                daemon=True,
            )
            thread.start()

        return {"job": job.to_dict()}

    @app.get("/market-daily/resolve")
    def resolve_market_daily(
        as_of: str | None = None,
        trade_date: str | None = None,
    ) -> dict[str, Any]:
        if trade_date is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", trade_date):
            raise HTTPException(status_code=400, detail="trade_date 格式須為 YYYY-MM-DD")
        skill = str(MARKET_DAILY_SKILL_DIR)
        if skill not in sys.path:
            sys.path.insert(0, skill)
        try:
            from market_day_signals import resolve_window_or_fail

            window = resolve_window_or_fail(as_of=as_of, trade_date=trade_date)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"window": window.as_dict()}

    @app.get("/market-daily")
    def list_market_daily() -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if MARKET_ROOT.exists():
            for day_dir in sorted(MARKET_ROOT.iterdir(), reverse=True):
                if not day_dir.is_dir():
                    continue
                summary_file = day_dir / "tw_market_daily.summary.json"
                facts_file = day_dir / "tw_market_daily.facts.json"
                md_file = day_dir / "tw_market_daily.md"
                if not summary_file.exists() and not facts_file.exists():
                    continue
                summary = None
                facts = None
                markdown = None
                if summary_file.exists():
                    summary = json.loads(summary_file.read_text(encoding="utf-8"))
                if facts_file.exists():
                    facts = json.loads(facts_file.read_text(encoding="utf-8"))
                if md_file.exists():
                    markdown = md_file.read_text(encoding="utf-8")
                items.append(
                    {
                        "trade_date": day_dir.name,
                        "for_session": (facts or summary or {}).get("for_session"),
                        "summary": summary,
                        "facts": facts,
                        "markdown": markdown,
                        "has_report": md_file.exists(),
                    }
                )
        return {"items": items[:30]}

    @app.get("/market-daily/jobs/{job_id}")
    def get_market_daily_job(job_id: str) -> dict[str, Any]:
        with _market_daily_jobs_lock:
            job = _market_daily_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="找不到 market-daily job")
        return {"job": job.to_dict()}

    @app.post("/market-daily/jobs")
    def create_market_daily_job(body: CreateMarketDailyJobRequest) -> dict[str, Any]:
        skill = str(MARKET_DAILY_SKILL_DIR)
        if skill not in sys.path:
            sys.path.insert(0, skill)
        try:
            from market_day_signals import resolve_window_or_fail

            window = resolve_window_or_fail(as_of=body.as_of, trade_date=body.trade_date)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        out_dir = MARKET_ROOT / window.trade_date
        md_path = out_dir / "tw_market_daily.md"
        facts_path = out_dir / "tw_market_daily.facts.json"
        summary_path = out_dir / "tw_market_daily.summary.json"

        job_id = uuid.uuid4().hex
        reuse = (
            not body.force
            and md_path.exists()
            and facts_path.exists()
            and summary_path.exists()
        )
        job = MarketDailyJob(
            id=job_id,
            as_of=body.as_of,
            trade_date=window.trade_date,
            for_session=window.for_session,
            skip_fetch=body.skip_fetch,
            skip_us=body.skip_us,
            force=body.force,
            status=JobStatus.DONE if reuse else JobStatus.GATING,
        )
        if reuse:
            job.facts = json.loads(facts_path.read_text(encoding="utf-8"))
            job.summary = json.loads(summary_path.read_text(encoding="utf-8"))
            job.markdown = md_path.read_text(encoding="utf-8")

        with _market_daily_jobs_lock:
            _market_daily_jobs[job_id] = job

        if not reuse:
            thread = threading.Thread(
                target=_run_market_daily_pipeline,
                args=(job_id, body.max_rounds),
                daemon=True,
            )
            thread.start()

        return {"job": job.to_dict()}

    @app.post("/market-daily/chat/stream")
    async def market_daily_chat_stream(
        body: MarketDailyChatRequest,
    ) -> StreamingResponse:
        skill = str(MARKET_DAILY_SKILL_DIR)
        if skill not in sys.path:
            sys.path.insert(0, skill)
        try:
            from market_day_chat import format_sse, iter_market_day_chat_events
        except ImportError as exc:
            raise HTTPException(
                status_code=500, detail=f"無法載入 market_day_chat：{exc}"
            ) from exc

        has_artifacts = body.facts is not None or body.summary is not None or bool(
            body.markdown
        )
        if not has_artifacts and not body.trade_date:
            raise HTTPException(
                status_code=400,
                detail="需要 facts/summary/markdown 或 trade_date",
            )

        history = [
            {"role": item.role, "content": item.content}
            for item in body.history
            if item.role in ("user", "assistant") and item.content.strip()
        ]

        import asyncio

        async def event_gen():
            # Run blocking Ollama iteration in a worker thread; push SSE chunks
            # through a queue so the event loop can flush each token promptly.
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def worker() -> None:
                try:
                    for event, payload in iter_market_day_chat_events(
                        message=body.message,
                        facts=body.facts,
                        summary=body.summary,
                        markdown=body.markdown,
                        trade_date=body.trade_date,
                        has_holdings=body.has_holdings,
                        holdings=body.holdings,
                        history=history,
                        use_llm=body.use_llm,
                        skip_tavily=body.skip_tavily,
                    ):
                        chunk = format_sse(event, payload).encode("utf-8")
                        fut = asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
                        fut.result()
                except Exception as exc:
                    chunk = format_sse("error", {"error": str(exc)}).encode("utf-8")
                    fut = asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
                    fut.result()
                finally:
                    fut = asyncio.run_coroutine_threadsafe(queue.put(None), loop)
                    fut.result()

            loop.run_in_executor(None, worker)

            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
                # Let uvicorn flush before the next token arrives.
                await asyncio.sleep(0)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/jobs")
    def create_job(body: CreateJobRequest) -> dict[str, Any]:
        if body.is_holding:
            cash_n = int(body.cash_share_count or 0)
            margin_n = int(body.margin_share_count or 0)
            has_legs = cash_n > 0 or margin_n > 0
            if has_legs:
                if cash_n > 0 and body.cash_avg_cost is None:
                    raise HTTPException(
                        status_code=400,
                        detail="現股分析需要 cash_avg_cost",
                    )
                if margin_n > 0 and body.margin_avg_cost is None:
                    raise HTTPException(
                        status_code=400,
                        detail="融資分析需要 margin_avg_cost",
                    )
            elif body.share_count is None or body.avg_cost is None:
                raise HTTPException(
                    status_code=400,
                    detail="持股分析需要 share_count 與 avg_cost，或現股／融資分腿",
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
            uses_margin=bool(
                body.is_holding
                and (
                    body.uses_margin
                    or int(body.margin_share_count or 0) > 0
                )
            ),
            cash_share_count=body.cash_share_count if body.is_holding else None,
            cash_avg_cost=body.cash_avg_cost if body.is_holding else None,
            margin_share_count=body.margin_share_count if body.is_holding else None,
            margin_avg_cost=body.margin_avg_cost if body.is_holding else None,
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
            payload.pop("facts_json", None)
            payload.pop("history_json", None)
            payload.pop("summary_json", None)
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
