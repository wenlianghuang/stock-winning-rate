"""draft_digest / send_digest — Notify tools (send requires approval)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.tools._types import EXIT_AGY_MISSING, EXIT_FAILED, EXIT_OK, ToolResult

AGY_TIMEOUT_SEC = 900


@dataclass
class DigestItem:
    stock_id: str
    markdown: str
    stock_name: str | None = None
    trade_date: str | None = None
    position_markdown: str | None = None


@dataclass
class DraftDigestInput:
    digest_date: str
    items: list[DigestItem] = field(default_factory=list)


@dataclass
class DraftDigestResult(ToolResult):
    subject: str | None = None
    main_detail_markdown: str | None = None


@dataclass
class SendDigestInput:
    digest_date: str
    subject: str
    main_detail_markdown: str
    approved: bool = False
    to: str | None = None


@dataclass
class SendDigestResult(ToolResult):
    status: str = "blocked"
    message: str = ""


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


def _run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> str:
    from agent.llm import complete

    return complete(prompt, timeout_sec=timeout_sec)


def draft_digest(inp: DraftDigestInput | None = None, **overrides: Any) -> DraftDigestResult:
    params = inp
    if params is None:
        digest_date = str(overrides.get("digest_date") or "")
        items = list(overrides.get("items") or [])
        params = DraftDigestInput(digest_date=digest_date, items=items)
    elif overrides:
        items = overrides.get("items", params.items)
        params = DraftDigestInput(
            digest_date=str(overrides.get("digest_date", params.digest_date)),
            items=list(items),
        )
    if not params.digest_date or not params.items:
        return DraftDigestResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error="draft_digest 需要 digest_date 與至少一筆 items",
        )
    try:
        prompt = build_digest_prompt(params.digest_date, params.items)
        raw = _run_agy(prompt)
        payload = json.loads(raw)
        subject = str(payload.get("subject", "")).strip()
        main_detail = str(payload.get("main_detail_markdown", "")).strip()
        if not subject or not main_detail:
            raise ValueError("digest JSON 缺少 subject 或 main_detail_markdown")
    except FileNotFoundError as exc:
        return DraftDigestResult(ok=False, exit_code=EXIT_AGY_MISSING, error=str(exc))
    except json.JSONDecodeError as exc:
        return DraftDigestResult(
            ok=False,
            exit_code=EXIT_FAILED,
            error=f"Digest JSON 解析失敗：{exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return DraftDigestResult(ok=False, exit_code=EXIT_FAILED, error=str(exc))
    return DraftDigestResult(
        ok=True,
        exit_code=EXIT_OK,
        subject=subject,
        main_detail_markdown=main_detail,
    )


def send_digest(inp: SendDigestInput | None = None, **overrides: Any) -> SendDigestResult:
    """Email is owned by stock-report-site. This tool never sends without approval."""
    params = inp
    if params is None:
        params = SendDigestInput(
            digest_date=str(overrides.get("digest_date") or ""),
            subject=str(overrides.get("subject") or ""),
            main_detail_markdown=str(overrides.get("main_detail_markdown") or ""),
            approved=bool(overrides.get("approved", False)),
            to=overrides.get("to"),
        )
    elif overrides:
        params = SendDigestInput(
            digest_date=str(overrides.get("digest_date", params.digest_date)),
            subject=str(overrides.get("subject", params.subject)),
            main_detail_markdown=str(
                overrides.get("main_detail_markdown", params.main_detail_markdown)
            ),
            approved=bool(overrides.get("approved", params.approved)),
            to=overrides.get("to", params.to),
        )
    if not params.approved:
        return SendDigestResult(
            ok=False,
            exit_code=EXIT_FAILED,
            status="blocked",
            message="send_digest 需核准；本階段只產出草稿。",
            error="needs_approval",
        )
    return SendDigestResult(
        ok=False,
        exit_code=EXIT_FAILED,
        status="unsupported",
        message="寄信由 stock-report-site 執行；本 repo 核准後仍只回傳草稿狀態。",
        error="email_owned_by_report_site",
    )
