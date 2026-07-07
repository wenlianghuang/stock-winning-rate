#!/usr/bin/env python3
"""US tech news pipeline: free RSS → index snapshot → agy zh-TW report → Markdown / slide PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from slide_html import write_summary_slides_html
from slide_pdf import write_summary_slides_pdf
from validate_tech_report import ValidationResult, normalize_slide_body, validate_tech_report

MAX_ROUNDS_DEFAULT = 2
AGY_TIMEOUT_SEC = 900

USER_AGENT = "Mozilla/5.0 (compatible; antigravity-agent/us-tech-news)"
CONFIG_PATH = Path(__file__).with_name("feeds.json")
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass(frozen=True)
class FeedConfig:
    feed_id: str
    name: str
    rss_url: str
    category: str


@dataclass(frozen=True)
class IndexConfig:
    symbol: str
    name: str


@dataclass(frozen=True)
class NewsItem:
    feed_id: str
    feed_name: str
    category: str
    title: str
    link: str
    published: datetime
    summary: str

    @property
    def published_str(self) -> str:
        return self.published.strftime("%Y-%m-%d %H:%M")

    @property
    def dedupe_key(self) -> str:
        normalized = re.sub(r"\W+", " ", self.title.lower()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class IndexSnapshot:
    symbol: str
    name: str
    price: float | None
    previous_close: float | None
    change_pct: float | None
    currency: str
    market_time: str

    def format_line(self) -> str:
        if self.price is None:
            return f"{self.name} ({self.symbol})：無法取得報價"
        price_text = f"{self.price:,.2f}"
        if self.change_pct is not None:
            sign = "+" if self.change_pct >= 0 else ""
            return (
                f"{self.name} ({self.symbol})：{price_text} "
                f"({sign}{self.change_pct:.2f}%)"
            )
        return f"{self.name} ({self.symbol})：{price_text}"


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _progress(message: str) -> None:
    print(f"PROGRESS: {message}", file=sys.stderr)


def load_config() -> dict[str, Any]:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    feeds = [
        FeedConfig(
            feed_id=str(item["feed_id"]),
            name=str(item["name"]),
            rss_url=str(item["rss_url"]),
            category=str(item.get("category", "tech")),
        )
        for item in data["feeds"]
    ]
    indices = [
        IndexConfig(symbol=str(item["symbol"]), name=str(item["name"]))
        for item in data["indices"]
    ]
    output_dir = project_root() / str(data.get("output_dir", "reports/us-tech"))
    return {
        "feeds": feeds,
        "indices": indices,
        "output_dir": output_dir,
        "lookback_hours": int(data.get("lookback_hours", 48)),
        "max_items_per_feed": int(data.get("max_items_per_feed", 15)),
    }


def _parse_rss_datetime(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _item_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _item_link(item: ET.Element) -> str:
    link = (item.findtext("link") or "").strip()
    if link:
        return link
    for child in item:
        if child.tag.endswith("link") and child.get("href"):
            return child.get("href", "").strip()
    guid = (item.findtext("guid") or "").strip()
    if guid.startswith("http"):
        return guid
    return ""


def _parse_feed_items(
    feed: FeedConfig,
    xml_bytes: bytes,
    *,
    cutoff: datetime,
    max_items: int,
) -> list[NewsItem]:
    root = ET.fromstring(xml_bytes)
    item_nodes = root.findall(".//item")
    if not item_nodes:
        item_nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    items: list[NewsItem] = []
    for item in item_nodes[: max_items * 2]:
        title = _item_text(item.find("title"))
        link = _item_link(item)
        if not title:
            continue
        pub_raw = (
            item.findtext("pubDate")
            or item.findtext("{http://www.w3.org/2005/Atom}published")
            or item.findtext("{http://www.w3.org/2005/Atom}updated")
            or ""
        )
        published = _parse_rss_datetime(pub_raw) or datetime.now()
        if published < cutoff:
            continue
        summary = _strip_html(
            item.findtext("description")
            or item.findtext("{http://www.w3.org/2005/Atom}summary")
            or ""
        )
        items.append(
            NewsItem(
                feed_id=feed.feed_id,
                feed_name=feed.name,
                category=feed.category,
                title=title,
                link=link,
                published=published,
                summary=summary[:500],
            )
        )
        if len(items) >= max_items:
            break
    return items


def fetch_feed_items(
    feed: FeedConfig,
    *,
    cutoff: datetime,
    max_items: int,
) -> list[NewsItem]:
    response = requests.get(
        feed.rss_url,
        timeout=60,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return _parse_feed_items(feed, response.content, cutoff=cutoff, max_items=max_items)


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in sorted(items, key=lambda x: x.published, reverse=True):
        keys = {item.dedupe_key}
        if item.link:
            keys.add(urlparse(item.link).path.lower())
        if keys & seen:
            continue
        seen.update(keys)
        unique.append(item)
    return unique


def fetch_index_snapshot(index: IndexConfig) -> IndexSnapshot:
    symbol = requests.utils.quote(index.symbol, safe="")
    response = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={"interval": "1d", "range": "5d"},
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result") or []
    if not result:
        return IndexSnapshot(
            symbol=index.symbol,
            name=index.name,
            price=None,
            previous_close=None,
            change_pct=None,
            currency="USD",
            market_time="",
        )

    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct = None
    if isinstance(price, (int, float)) and isinstance(previous, (int, float)) and previous:
        change_pct = (price - previous) / previous * 100
    ts = meta.get("regularMarketTime")
    market_time = ""
    if isinstance(ts, (int, float)):
        market_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

    return IndexSnapshot(
        symbol=index.symbol,
        name=index.name,
        price=float(price) if isinstance(price, (int, float)) else None,
        previous_close=float(previous) if isinstance(previous, (int, float)) else None,
        change_pct=change_pct,
        currency=str(meta.get("currency", "USD")),
        market_time=market_time,
    )


def report_date_str(when: datetime | None = None) -> str:
    when = when or datetime.now()
    return when.strftime("%Y-%m-%d")


def artifact_paths(output_dir: Path, date_str: str) -> dict[str, Path]:
    prefix = date_str
    return {
        "raw_json": output_dir / f"{prefix}_raw.json",
        "raw_txt": output_dir / f"{prefix}_raw.txt",
        "summary_md": output_dir / f"{prefix}_summary.md",
        "summary_pdf": output_dir / f"{prefix}_summary.pdf",
        "summary_html": output_dir / f"{prefix}_summary.html",
        "assets_dir": output_dir / f"{prefix}_assets",
        "gate_log": output_dir / f"{prefix}.gate.log",
        "gate_rounds_dir": output_dir / f"{prefix}.gate.rounds",
    }


def write_raw_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_raw_text(
    path: Path,
    *,
    date_str: str,
    indices: list[IndexSnapshot],
    items: list[NewsItem],
) -> None:
    lines = [
        f"Report date: {date_str}",
        "",
        "=== Index snapshots ===",
    ]
    for snap in indices:
        detail = snap.format_line()
        if snap.market_time:
            detail += f"  （更新：{snap.market_time}）"
        lines.append(detail)
    lines.extend(["", "=== News items ===", ""])
    for idx, item in enumerate(items, start=1):
        lines.extend(
            [
                f"[{idx}] {item.title}",
                f"Feed: {item.feed_name} ({item.feed_id})",
                f"Category: {item.category}",
                f"Published: {item.published_str}",
                f"Link: {item.link or '(none)'}",
            ]
        )
        if item.summary:
            lines.append(f"Summary: {item.summary}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def resolve_agy_bin() -> str:
    custom = os.environ.get("AGY_BIN", "").strip()
    if custom:
        return custom
    found = shutil.which("agy")
    if not found:
        raise RuntimeError(
            "找不到 agy 指令。請安裝 Antigravity CLI 或設定 AGY_BIN。"
        )
    return found


def _load_agy_helpers():
    ui_path = project_root() / "ui"
    if str(ui_path) not in sys.path:
        sys.path.insert(0, str(ui_path))
    from agy_output import agy_output_usable, clean_agy_output

    return clean_agy_output, agy_output_usable


def build_agy_prompt(date_str: str, raw_path: Path, indices: list[IndexSnapshot]) -> str:
    index_lines = "\n".join(f"- {snap.format_line()}" for snap in indices)
    return (
        f"請讀取以下美股科技新聞彙整檔案，"
        f"撰寫繁體中文（台灣用語）簡報用分析內容，"
        f"並將完整正文直接輸出到 stdout（Markdown）。\n\n"
        f"報告日期：{date_str}\n"
        f"原始資料路徑：{raw_path}\n\n"
        f"指數快照（請在報告中引用，勿臆測數字）：\n{index_lines}\n\n"
        "輸出格式（每個主題一頁 slide，以單獨一行的 --- 分隔）：\n"
        "## 市場概況\n"
        "- 重點 bullet\n"
        "---\n"
        "## 那斯達克與費城半導體指數解讀\n"
        "- 重點 bullet\n"
        "---\n"
        "## 半導體與 AI 基建\n"
        "- 重點 bullet\n"
        "---\n"
        "## 大型科技動態\n"
        "- 重點 bullet\n"
        "---\n"
        "## 跨主題連動分析\n"
        "- 重點 bullet\n"
        "---\n"
        "## 值得後續關注\n"
        "- 重點 bullet\n"
        "---\n"
        "## 新聞來源\n"
        "- 標題（來源 feed）\n\n"
        "要求：\n"
        "- 必須先讀取原始資料檔再分析，不可臆測未收錄的新聞\n"
        "- 每頁 slide 以 ## 標題開頭，正文用 - bullet 列點（每點 1～2 句）\n"
        "- 僅根據原始資料中的新聞與指數，客觀中立，不提供買賣建議\n"
        "- 若某主題在原始資料中資訊不足，請明確說明\n"
        "- 完成初稿後自我檢查：指數數字、新聞事實是否與原始資料一致，"
        "修正後再輸出最終版\n"
        "- 完整報告必須印在 stdout，不要只寫入檔案\n"
        "- 不要加「工作摘要」或工具操作說明\n"
        "- 「新聞來源」slide 列出實際引用的新聞標題與來源 feed\n"
    )


def build_fix_prompt(
    date_str: str,
    raw_path: Path,
    indices: list[IndexSnapshot],
    previous_body: str,
    validation: ValidationResult,
) -> str:
    index_lines = "\n".join(f"- {snap.format_line()}" for snap in indices)
    issues = "\n".join(f"- {line}" for line in validation.summary_lines())
    return (
        f"上一版美股科技市場日報（{date_str}）未通過自動驗證。\n"
        f"請依下列問題修正後，輸出完整新版 slide 正文到 stdout（Markdown）。\n\n"
        f"驗證問題：\n{issues}\n\n"
        f"報告日期：{date_str}\n"
        f"原始資料路徑：{raw_path}\n\n"
        f"指數快照（數字必須完全一致，勿臆測）：\n{index_lines}\n\n"
        "修正要求：\n"
        "- 保留 7 個 slide 主題，以單獨一行的 --- 分隔\n"
        "- 每頁以 ## 標題開頭，正文用 - bullet 列點\n"
        "- 指數收盤價與漲跌幅必須與上方快照一致\n"
        "- 「新聞來源」須引用原始資料中的真實新聞標題與 feed\n"
        "- 不可提供買賣建議；不要加工作摘要\n\n"
        "上一版全文：\n"
        f"{previous_body.strip()}\n"
    )


def run_agy(prompt: str, *, timeout_sec: int = AGY_TIMEOUT_SEC) -> tuple[str, int]:
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
            cwd=project_root(),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"agy 逾時（>{timeout_sec}s）") from exc

    body = clean_agy_output(result.stdout or result.stderr or "")
    if not agy_output_usable(body):
        detail = body[:200] if body else "(空)"
        raise RuntimeError(f"agy 輸出不可用（exit {result.returncode}）：{detail}")
    return body, result.returncode


def round_artifact_paths(gate_rounds_dir: Path, round_no: int) -> dict[str, Path]:
    base = gate_rounds_dir / f"r{round_no:02d}"
    return {
        "prompt": base.with_suffix(".prompt.txt"),
        "body": base.with_suffix(".body.md"),
        "validation": base.with_suffix(".validation.json"),
    }


def write_round_artifacts(
    gate_rounds_dir: Path,
    round_no: int,
    *,
    prompt: str,
    body: str,
    validation: ValidationResult,
    agy_exit_code: int,
    duration_sec: float,
) -> dict[str, Path]:
    paths = round_artifact_paths(gate_rounds_dir, round_no)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    paths["prompt"].write_text(prompt.strip() + "\n", encoding="utf-8")
    paths["body"].write_text(body.strip() + "\n", encoding="utf-8")
    paths["validation"].write_text(
        json.dumps(
            {
                "round": round_no,
                "passed": validation.passed,
                "agy_exit_code": agy_exit_code,
                "duration_sec": round(duration_sec, 2),
                "issues": [
                    {"code": issue.code, "message": issue.message}
                    for issue in validation.issues
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return paths


def write_rounds_index(
    gate_rounds_dir: Path,
    date_str: str,
    round_summaries: list[dict[str, Any]],
    *,
    final_passed: bool,
) -> Path:
    gate_rounds_dir.mkdir(parents=True, exist_ok=True)
    index_path = gate_rounds_dir / "index.md"
    lines = [
        f"# US Tech Gate 各輪比較 — {date_str}",
        "",
        f"- **產生時間：** {datetime.now().isoformat(timespec='seconds')}",
        f"- **最終結果：** {'PASS' if final_passed else 'FAIL'}",
        "",
        "## 輪次摘要",
        "",
        "| 輪次 | 結果 | 耗時 (s) | 問題數 | 檔案 |",
        "|------|------|----------|--------|------|",
    ]
    for summary in round_summaries:
        round_no = summary["round"]
        rel = f"r{round_no:02d}"
        status = "PASS" if summary["passed"] else "FAIL"
        lines.append(
            f"| {round_no} | {status} | {summary['duration_sec']:.1f} | "
            f"{len(summary['issues'])} | "
            f"[正文]({rel}.body.md) · [prompt]({rel}.prompt.txt) · "
            f"[驗證]({rel}.validation.json) |"
        )
    lines.extend(["", "## 各輪驗證問題", ""])
    for summary in round_summaries:
        round_no = summary["round"]
        lines.append(f"### Round {round_no} — {'PASS' if summary['passed'] else 'FAIL'}")
        if summary["issues"]:
            for issue in summary["issues"]:
                lines.append(f"- {issue}")
        else:
            lines.append("- （無）")
        lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def append_gate_log(log_path: Path, entry: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _print_validation(validation: ValidationResult, *, round_no: int) -> None:
    if validation.passed:
        print(f"Round {round_no}: PASS", file=sys.stderr)
        return
    print(f"Round {round_no}: FAIL", file=sys.stderr)
    for line in validation.summary_lines():
        print(f"  - {line}", file=sys.stderr)


def extract_body_from_saved_md(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    marker = "\n---\n\n"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def load_indices_items_from_raw(raw_json_path: Path) -> tuple[list[IndexSnapshot], list[NewsItem]]:
    payload = json.loads(raw_json_path.read_text(encoding="utf-8"))
    indices = [
        IndexSnapshot(
            symbol=str(item["symbol"]),
            name=str(item["name"]),
            price=item.get("price"),
            previous_close=item.get("previous_close"),
            change_pct=item.get("change_pct"),
            currency="USD",
            market_time=str(item.get("market_time", "")),
        )
        for item in payload.get("indices", [])
    ]
    items = [
        NewsItem(
            feed_id=str(item["feed_id"]),
            feed_name=str(item["feed_name"]),
            category=str(item["category"]),
            title=str(item["title"]),
            link=str(item.get("link", "")),
            published=datetime.now(),
            summary=str(item.get("summary", "")),
        )
        for item in payload.get("items", [])
    ]
    return indices, items


def render_slide_artifacts(
    paths: dict[str, Path],
    *,
    date_str: str,
    indices: list[IndexSnapshot],
    items: list[NewsItem],
    feed_count: int,
    summary_body: str,
    skip_pdf: bool,
    skip_html: bool,
) -> int:
    body = normalize_slide_body(summary_body)
    slide_kwargs = dict(
        date_str=date_str,
        indices=indices,
        items=items,
        feed_count=feed_count,
        agy_body=body,
        assets_dir=paths["assets_dir"],
    )
    exit_code = 0

    if not skip_html:
        _progress("產生 slide HTML…")
        if write_summary_slides_html(paths["summary_html"], **slide_kwargs):
            print(f"Slide HTML：{paths['summary_html']}")
        else:
            print("警告：slide HTML 未產生", file=sys.stderr)
            exit_code = 1

    if not skip_pdf:
        _progress("產生 slide PDF…")
        if write_summary_slides_pdf(paths["summary_pdf"], **slide_kwargs):
            print(f"Slide PDF：{paths['summary_pdf']}")
        else:
            print(
                "警告：slide PDF 未產生（請確認 fpdf2、matplotlib 與中文字型）",
                file=sys.stderr,
            )
            exit_code = 1

    return exit_code


def run_summary_gate(
    date_str: str,
    raw_path: Path,
    indices: list[IndexSnapshot],
    items: list[NewsItem],
    *,
    max_rounds: int,
    gate_log_path: Path,
    gate_rounds_dir: Path,
) -> tuple[str, bool]:
    body = ""
    validation = ValidationResult(passed=False)
    round_summaries: list[dict[str, Any]] = []

    for round_no in range(1, max_rounds + 1):
        round_started = time.time()
        print(f"\n=== US Tech Gate Round {round_no}/{max_rounds} ===", file=sys.stderr)
        _progress(f"agy 市場日報第 {round_no}/{max_rounds} 輪…")

        if round_no == 1:
            prompt = build_agy_prompt(date_str, raw_path, indices)
        else:
            prompt = build_fix_prompt(
                date_str, raw_path, indices, body, validation
            )

        body, agy_exit = run_agy(prompt)
        body = normalize_slide_body(body)
        validation = validate_tech_report(body, indices=indices, items=items)
        duration = time.time() - round_started

        artifact_paths_written = write_round_artifacts(
            gate_rounds_dir,
            round_no,
            prompt=prompt,
            body=body,
            validation=validation,
            agy_exit_code=agy_exit,
            duration_sec=duration,
        )
        issue_lines = validation.summary_lines()
        round_summaries.append(
            {
                "round": round_no,
                "passed": validation.passed,
                "duration_sec": duration,
                "issues": issue_lines,
            }
        )
        append_gate_log(
            gate_log_path,
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "report_date": date_str,
                "round": round_no,
                "passed": validation.passed,
                "agy_exit_code": agy_exit,
                "duration_sec": round(duration, 2),
                "issues": issue_lines,
                "prompt_path": str(artifact_paths_written["prompt"]),
                "body_path": str(artifact_paths_written["body"]),
                "validation_path": str(artifact_paths_written["validation"]),
            },
        )
        _print_validation(validation, round_no=round_no)
        print(f"  正文: {artifact_paths_written['body']}", file=sys.stderr)

        if validation.passed:
            write_rounds_index(
                gate_rounds_dir,
                date_str,
                round_summaries,
                final_passed=True,
            )
            print(f"驗證通過（第 {round_no} 輪）", file=sys.stderr)
            return body, True

    write_rounds_index(
        gate_rounds_dir,
        date_str,
        round_summaries,
        final_passed=False,
    )
    return body, False


def summarize_agy(
    date_str: str,
    raw_path: Path,
    indices: list[IndexSnapshot],
    items: list[NewsItem],
    *,
    max_rounds: int,
    gate_log_path: Path,
    gate_rounds_dir: Path,
) -> str:
    print("agy 產生繁體中文市場日報（含驗證閉環）…", file=sys.stderr)
    body, passed = run_summary_gate(
        date_str,
        raw_path,
        indices,
        items,
        max_rounds=max_rounds,
        gate_log_path=gate_log_path,
        gate_rounds_dir=gate_rounds_dir,
    )
    if not passed:
        raise RuntimeError(
            f"已達最大輪數 {max_rounds}，報告仍未通過驗證。"
            f"詳見 {gate_rounds_dir} 與 {gate_log_path}"
        )
    return body


def write_summary_markdown(
    path: Path,
    *,
    date_str: str,
    body: str,
    item_count: int,
    feed_count: int,
    model: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"# 美股科技市場日報\n\n"
        f"**報告日期：** {date_str}  \n"
        f"**新聞來源數：** {feed_count} 個 RSS feed  \n"
        f"**收錄新聞：** {item_count} 則（去重後）  \n"
        f"**摘要模型：** {model}\n\n"
        f"---\n\n"
        f"{body.strip()}\n"
    )
    path.write_text(content, encoding="utf-8")


def load_state(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {str(d) for d in data.get("processed_dates", [])}


def save_state(state_path: Path, dates: set[str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed_dates": sorted(dates),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="美股科技 RSS → agy 繁中市場日報（Markdown / slide PDF）",
    )
    parser.add_argument("--date", help="報告日期 YYYY-MM-DD（預設今天）")
    parser.add_argument("--force", action="store_true", help="強制重新產生")
    parser.add_argument("--skip-summary", action="store_true", help="只抓 RSS，不跑 agy")
    parser.add_argument("--skip-pdf", action="store_true", help="不產生 slide PDF")
    parser.add_argument("--skip-html", action="store_true", help="不產生 slide HTML")
    parser.add_argument(
        "--render-slides",
        action="store_true",
        help="從既有 summary.md + raw.json 重新產生 PDF / HTML（不抓 RSS、不跑 agy）",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="新聞回溯小時數（預設讀 feeds.json）",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="每個 feed 最多收錄則數（預設讀 feeds.json）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="輸出目錄（預設 reports/us-tech）",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=MAX_ROUNDS_DEFAULT,
        help=f"agy 驗證閉環最大輪數（預設 {MAX_ROUNDS_DEFAULT}）",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只驗證既有 summary.md，不呼叫 agy",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    output_dir = args.output or config["output_dir"]
    output_dir = output_dir if output_dir.is_absolute() else project_root() / output_dir

    date_str = args.date or report_date_str()
    lookback_hours = args.hours if args.hours is not None else config["lookback_hours"]
    max_items = (
        args.max_items if args.max_items is not None else config["max_items_per_feed"]
    )
    cutoff = datetime.now() - timedelta(hours=lookback_hours)

    paths = artifact_paths(output_dir, date_str)
    state_path = output_dir / "state.json"
    processed = load_state(state_path)

    if args.validate_only:
        if not paths["summary_md"].exists():
            print(f"ERROR: 找不到既有報告 {paths['summary_md']}", file=sys.stderr)
            return 1
        if not paths["raw_json"].exists():
            print(f"ERROR: 找不到原始資料 {paths['raw_json']}", file=sys.stderr)
            return 1
        indices, items = load_indices_items_from_raw(paths["raw_json"])
        body = normalize_slide_body(extract_body_from_saved_md(paths["summary_md"]))
        validation = validate_tech_report(body, indices=indices, items=items)
        _print_validation(validation, round_no=0)
        return 0 if validation.passed else 1

    if args.render_slides:
        if not paths["summary_md"].exists():
            print(f"ERROR: 找不到既有報告 {paths['summary_md']}", file=sys.stderr)
            return 1
        if not paths["raw_json"].exists():
            print(f"ERROR: 找不到原始資料 {paths['raw_json']}", file=sys.stderr)
            return 1
        indices, items = load_indices_items_from_raw(paths["raw_json"])
        summary_body = extract_body_from_saved_md(paths["summary_md"])
        feed_count = len({item.feed_id for item in items})
        print(f"重新渲染 slide（{date_str}）…", file=sys.stderr)
        return render_slide_artifacts(
            paths,
            date_str=date_str,
            indices=indices,
            items=items,
            feed_count=feed_count,
            summary_body=summary_body,
            skip_pdf=args.skip_pdf,
            skip_html=args.skip_html,
        )

    if (
        not args.force
        and date_str in processed
        and paths["summary_md"].exists()
        and not args.skip_summary
    ):
        print(f"今日報告已存在：{paths['summary_md']}", file=sys.stderr)
        print(f"原始資料：{paths['raw_txt']}")
        print(f"摘要 Markdown：{paths['summary_md']}")
        if paths["summary_pdf"].exists():
            print(f"Slide PDF：{paths['summary_pdf']}")
        if paths["summary_html"].exists():
            print(f"Slide HTML：{paths['summary_html']}")
        return 0

    if not args.skip_summary:
        try:
            resolve_agy_bin()
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(f"報告日期：{date_str}", file=sys.stderr)
    _progress("抓取指數快照…")
    indices = [fetch_index_snapshot(index) for index in config["indices"]]
    for snap in indices:
        print(snap.format_line(), file=sys.stderr)

    all_items: list[NewsItem] = []
    feeds_ok = 0
    for feed in config["feeds"]:
        _progress(f"抓取 RSS：{feed.name}…")
        try:
            items = fetch_feed_items(feed, cutoff=cutoff, max_items=max_items)
            all_items.extend(items)
            feeds_ok += 1
            print(f"  {feed.feed_id}: {len(items)} 則", file=sys.stderr)
        except requests.RequestException as exc:
            print(f"  警告：{feed.feed_id} 抓取失敗：{exc}", file=sys.stderr)

    if not all_items:
        print("ERROR: 所有 RSS feed 皆無新聞或抓取失敗", file=sys.stderr)
        return 1

    items = dedupe_items(all_items)
    print(f"去重後共 {len(items)} 則新聞（{feeds_ok} 個 feed 成功）", file=sys.stderr)

    payload = {
        "report_date": date_str,
        "lookback_hours": lookback_hours,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "indices": [
            {
                "symbol": snap.symbol,
                "name": snap.name,
                "price": snap.price,
                "previous_close": snap.previous_close,
                "change_pct": snap.change_pct,
                "market_time": snap.market_time,
            }
            for snap in indices
        ],
        "items": [
            {
                "feed_id": item.feed_id,
                "feed_name": item.feed_name,
                "category": item.category,
                "title": item.title,
                "link": item.link,
                "published": item.published_str,
                "summary": item.summary,
            }
            for item in items
        ],
    }
    write_raw_json(paths["raw_json"], payload)
    write_raw_text(paths["raw_txt"], date_str=date_str, indices=indices, items=items)
    print(f"原始資料：{paths['raw_txt']}")

    if args.skip_summary:
        return 0

    try:
        summary = summarize_agy(
            date_str,
            paths["raw_txt"],
            indices,
            items,
            max_rounds=args.max_rounds,
            gate_log_path=paths["gate_log"],
            gate_rounds_dir=paths["gate_rounds_dir"],
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Gate log: {paths['gate_log']}", file=sys.stderr)
        print(f"各輪比較: {paths['gate_rounds_dir'] / 'index.md'}", file=sys.stderr)
        return 1

    write_summary_markdown(
        paths["summary_md"],
        date_str=date_str,
        body=summary,
        item_count=len(items),
        feed_count=feeds_ok,
        model="agy",
    )
    print(f"摘要 Markdown：{paths['summary_md']}")
    print(f"Gate log：{paths['gate_log']}")
    print(f"各輪比較：{paths['gate_rounds_dir'] / 'index.md'}")

    render_code = render_slide_artifacts(
        paths,
        date_str=date_str,
        indices=indices,
        items=items,
        feed_count=feeds_ok,
        summary_body=summary,
        skip_pdf=args.skip_pdf,
        skip_html=args.skip_html,
    )
    if render_code != 0:
        return render_code

    processed.add(date_str)
    save_state(state_path, processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
