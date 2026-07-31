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
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field
    import uvicorn
except ImportError as exc:
    print(f"CRITICAL_ERROR: 缺少 server 套件：{exc}", file=sys.stderr)
    print("請執行：uv sync --extra server --extra ui --extra stock", file=sys.stderr)
    raise SystemExit(10) from exc

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ModuleNotFoundError:
    pass
STOCK_SKILL_DIR = ROOT / ".agents" / "skills" / "tw-stock-report"
if str(STOCK_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(STOCK_SKILL_DIR))
STOCK_SCRIPT = STOCK_SKILL_DIR / "fetch_chip_report.py"
GATE_SCRIPT = ROOT / ".agents" / "skills" / "report-gate" / "report_gate.py"
POSITION_SCRIPT = ROOT / ".agents" / "skills" / "position-gate" / "position_gate.py"
STOCK_ROOT = ROOT / "reports" / "stock"
PORTFOLIO_ROOT = ROOT / "reports" / "portfolio"
PORTFOLIO_SKILL_DIR = ROOT / ".agents" / "skills" / "portfolio-gate"
PORTFOLIO_GATE_SCRIPT = PORTFOLIO_SKILL_DIR / "portfolio_gate.py"
PORTFOLIO_PROFILES = ("conservative", "balanced", "aggressive")
PORTFOLIO_MIN_AMOUNT = 50_000
WEB_ROOT = ROOT / "web"
CHART_LOOKBACK_DAYS = 60

AGY_TIMEOUT_SEC = 900


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


def _load_agy_helpers():
    _ensure_import_paths()
    from agy_output import agy_output_usable, clean_agy_output

    return clean_agy_output, agy_output_usable


def resolve_agy_bin() -> str:
    custom = os.environ.get("AGY_BIN", "").strip()
    if custom:
        return custom
    found = shutil.which("agy")
    if not found:
        raise RuntimeError("找不到 agy 指令。請安裝 Antigravity CLI 或設定 AGY_BIN。")
    return found


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> str:
    agy_bin = resolve_agy_bin()
    clean_agy_output, agy_output_usable = _load_agy_helpers()
    try:
        result = subprocess.run(
            [
                agy_bin,
                "-p",
                prompt,
                "--dangerously-skip-permissions",
                "--print-timeout",
                "15m",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"agy 逾時（>{timeout_sec}s）") from exc

    raw = result.stdout or result.stderr or ""
    body = clean_agy_output(raw)
    if not agy_output_usable(body, min_chars=20):
        detail = body[:200] if body else "(空)"
        raise RuntimeError(f"agy 輸出不可用（exit {result.returncode}）：{detail}")
    return body


def build_digest_prompt(digest_date: str, items: list[DigestItem]) -> str:
    blocks: list[str] = []
    for idx, item in enumerate(items, start=1):
        name = item.stock_name or item.stock_id
        header = f"【{idx}】{name}（{item.stock_id}）"
        date_hint = f"交易日：{item.trade_date}" if item.trade_date else ""
        parts = [header, date_hint, "", "=== 市場報告（Markdown）===", item.markdown.strip()]
        if item.position_markdown and item.position_markdown.strip():
            parts.extend(["", "=== 部位報告（Markdown）===", item.position_markdown.strip()])
        blocks.append("\n".join([p for p in parts if p]))

    joined = "\n\n---\n\n".join(blocks)
    return (
        "你是一位台股籌碼日報編輯，任務是把同一日多檔報告融合成一封 email 日報。\n"
        f"日報日期：{digest_date}\n\n"
        "輸出要求：\n"
        "- 請輸出 **嚴格 JSON**（不得有多餘文字、不得用 Markdown code fence）\n"
        '- JSON 只允許以下欄位：{"subject": string, "main_detail_markdown": string}\n'
        "- subject：一句話總結（含日期），格式建議：YYYY-MM-DD 台股籌碼日報｜{一句話}\n"
        "- main_detail_markdown：以 Markdown 撰寫 email 內文，結構固定：\n"
        "  1) 最上方 2 句總結\n"
        "  2) ## 重點摘要（3～7 點條列）\n"
        "  3) ## 個股觀察（每檔 2～4 行，避免表格，避免塞大量數字）\n"
        "  4) ## 風險提醒（最多 3 點）\n"
        "- 嚴禁臆造不存在於輸入的新聞或數據；不確定就用保守措辭\n"
        "- 內文要好讀，避免太長（目標 400～900 字）\n\n"
        "以下是同日多檔報告（輸入即事實來源）：\n\n"
        f"{joined}\n"
    )


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
    stock_args = [
        "--stocks",
        stock_id,
        "--chart-lookback-days",
        str(CHART_LOOKBACK_DAYS),
    ]
    if trade_date:
        stock_args.extend(["--date", trade_date])
    _run_script(STOCK_SCRIPT, stock_args)


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
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return

    stock_id = job.stock_id
    try:
        with _jobs_lock:
            _update_job(job, status=JobStatus.FETCHING, error=None)

        stock_args = ["--stocks", stock_id, "--chart-lookback-days", str(CHART_LOOKBACK_DAYS)]
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

            if has_legs:
                position_args = [stock_id]
                if cash_n > 0:
                    position_args.extend(
                        ["--cash-shares", str(cash_n), "--cash-cost", str(job.cash_avg_cost)]
                    )
                if margin_n > 0:
                    position_args.extend(
                        [
                            "--margin-shares",
                            str(margin_n),
                            "--margin-cost",
                            str(job.margin_avg_cost),
                        ]
                    )
            else:
                position_args = [
                    stock_id,
                    str(job.avg_cost),
                    str(job.share_count),
                ]
                if job.uses_margin:
                    position_args.append("--margin")
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
        from twse_calendar import chip_reference_date, resolve_trade_date

        reference = date or chip_reference_date().isoformat()
        trade_date, note = resolve_trade_date(
            reference,
            finmind_token=os.environ.get("FINMIND_TOKEN", ""),
        )
        return {
            "reference_date": reference,
            "trade_date": trade_date,
            "note": note,
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
        try:
            prompt = build_digest_prompt(body.digest_date, body.items)
            raw = run_agy(prompt)
            payload = json.loads(raw)
            subject = str(payload.get("subject", "")).strip()
            main_detail = str(payload.get("main_detail_markdown", "")).strip()
            if not subject or not main_detail:
                raise ValueError("digest JSON 缺少 subject 或 main_detail_markdown")
            return {"digest": {"subject": subject, "main_detail_markdown": main_detail}}
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
