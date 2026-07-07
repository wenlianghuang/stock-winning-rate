"""Build landscape PDF slide deck for US tech news reports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import requests

from validate_tech_report import split_slide_chunks

USER_AGENT = "Mozilla/5.0 (compatible; antigravity-agent/us-tech-news)"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

CATEGORY_LABELS: dict[str, str] = {
    "index": "指數",
    "semiconductor": "半導體 / AI 晶片",
    "tech": "大型科技",
    "market": "市場動態",
}

SLIDE_MARGIN = 14.0
TITLE_Y = 16.0
CONTENT_Y = 32.0
FOOTER_Y = 196.0


class IndexSnapshotLike(Protocol):
    symbol: str
    name: str
    price: float | None
    previous_close: float | None
    change_pct: float | None
    market_time: str


class NewsItemLike(Protocol):
    feed_name: str
    category: str
    title: str
    published_str: str


@dataclass(frozen=True)
class ChartSeries:
    symbol: str
    name: str
    dates: list[str]
    closes: list[float]


@dataclass
class Slide:
    title: str
    bullets: list[str] = field(default_factory=list)
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    image_path: Path | None = None
    subtitle: str = ""
    center_title: bool = False


def _find_cjk_font() -> tuple[Path, int] | None:
    candidates: list[tuple[Path, int]] = [
        (Path("/Library/Fonts/Arial Unicode.ttf"), 0),
        (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), 0),
        (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), 0),
        (Path("/System/Library/Fonts/PingFang.ttc"), 0),
    ]
    for path, index in candidates:
        if path.exists():
            return path, index
    return None


def _configure_matplotlib_cjk() -> bool:
    font_info = _find_cjk_font()
    if not font_info:
        return False
    try:
        import matplotlib
        from matplotlib import font_manager
    except ImportError:
        return False

    font_path, _ = font_info
    try:
        font_manager.fontManager.addfont(str(font_path))
        font_name = font_manager.FontProperties(fname=str(font_path)).get_name()
        matplotlib.rcParams["font.sans-serif"] = [font_name]
        matplotlib.rcParams["axes.unicode_minus"] = False
        return True
    except (OSError, ValueError):
        return False


def fetch_index_chart_series(symbol: str, name: str) -> ChartSeries | None:
    encoded = requests.utils.quote(symbol, safe="")
    try:
        response = requests.get(
            YAHOO_CHART_URL.format(symbol=encoded),
            params={"interval": "1d", "range": "5d"},
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    result = payload.get("chart", {}).get("result") or []
    if not result:
        return None

    timestamps = result[0].get("timestamp") or []
    quotes = (result[0].get("indicators") or {}).get("quote") or []
    if not quotes:
        return None
    closes = quotes[0].get("close") or []

    dates: list[str] = []
    values: list[float] = []
    for ts, close in zip(timestamps, closes, strict=False):
        if not isinstance(ts, (int, float)) or not isinstance(close, (int, float)):
            continue
        dates.append(datetime.fromtimestamp(ts).strftime("%m/%d"))
        values.append(float(close))

    if len(values) < 2:
        return None
    return ChartSeries(symbol=symbol, name=name, dates=dates, closes=values)


def render_index_chart(path: Path, series_list: list[ChartSeries]) -> bool:
    if not series_list:
        return False
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    _configure_matplotlib_cjk()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9.5, 4.8), dpi=120)
    colors = ["#2563eb", "#dc2626", "#059669", "#9333ea"]
    for idx, series in enumerate(series_list):
        color = colors[idx % len(colors)]
        ax.plot(
            series.dates,
            series.closes,
            marker="o",
            linewidth=2.2,
            markersize=5,
            color=color,
            label=series.name,
        )
    ax.set_title("近 5 日指數走勢", fontsize=14, pad=12)
    ax.set_ylabel("收盤點數")
    ax.grid(True, alpha=0.25, linestyle="--")
    ax.legend(loc="best", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path.exists()


def render_category_chart(path: Path, items: list[NewsItemLike]) -> bool:
    counts: dict[str, int] = {}
    for item in items:
        label = CATEGORY_LABELS.get(item.category, item.category)
        counts[label] = counts.get(label, 0) + 1
    if not counts:
        return False
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    _configure_matplotlib_cjk()
    path.parent.mkdir(parents=True, exist_ok=True)
    labels = list(counts.keys())
    values = [counts[label] for label in labels]
    fig, ax = plt.subplots(figsize=(9.5, 4.8), dpi=120)
    bars = ax.bar(labels, values, color="#3b82f6", alpha=0.85)
    ax.set_title("新聞主題分布（去重後）", fontsize=14, pad=12)
    ax.set_ylabel("則數")
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    for bar, value in zip(bars, values, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.15,
            str(value),
            ha="center",
            va="bottom",
            fontsize=10,
        )
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path.exists()


def _plain_text(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    return text.strip()


def parse_agy_slides(body: str) -> list[Slide]:
    chunks = split_slide_chunks(body)
    slides: list[Slide] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        title = ""
        bullets: list[str] = []
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            heading = re.match(r"^#{1,3}\s+(.*)$", stripped)
            if heading and not title:
                title = _plain_text(heading.group(1))
                continue
            bullet = re.match(r"^[-*]\s+(.*)$", stripped)
            if bullet:
                bullets.append(_plain_text(bullet.group(1)))
                continue
            if not title:
                title = _plain_text(stripped)
            elif stripped and not stripped.startswith("#"):
                bullets.append(_plain_text(stripped))
        if title or bullets:
            slides.append(Slide(title=title or "重點摘要", bullets=bullets))
    return slides


def _format_change_pct(change_pct: float | None) -> str:
    if change_pct is None:
        return "—"
    sign = "+" if change_pct >= 0 else ""
    return f"{sign}{change_pct:.2f}%"


def _format_price(price: float | None) -> str:
    if price is None:
        return "—"
    return f"{price:,.2f}"


def build_programmatic_slides(
    *,
    date_str: str,
    indices: list[IndexSnapshotLike],
    items: list[NewsItemLike],
    feed_count: int,
    assets_dir: Path,
) -> tuple[list[Slide], list[Path]]:
    generated_assets: list[Path] = []
    slides: list[Slide] = [
        Slide(
            title="美股科技市場日報",
            subtitle=f"報告日期：{date_str}\n"
            f"收錄 {len(items)} 則新聞 · {feed_count} 個 RSS 來源",
            center_title=True,
        )
    ]

    index_rows = [
        [
            snap.name,
            snap.symbol,
            _format_price(snap.price),
            _format_change_pct(snap.change_pct),
            snap.market_time or "—",
        ]
        for snap in indices
    ]
    slides.append(
        Slide(
            title="指數快照",
            table_headers=["指數", "代碼", "收盤", "漲跌幅", "更新時間"],
            table_rows=index_rows,
        )
    )

    chart_series: list[ChartSeries] = []
    for snap in indices:
        series = fetch_index_chart_series(snap.symbol, snap.name)
        if series:
            chart_series.append(series)
    chart_path = assets_dir / "index_chart.png"
    if render_index_chart(chart_path, chart_series):
        generated_assets.append(chart_path)
        slides.append(
            Slide(
                title="近 5 日指數走勢",
                image_path=chart_path,
            )
        )

    category_chart_path = assets_dir / "category_chart.png"
    if render_category_chart(category_chart_path, items):
        generated_assets.append(category_chart_path)
        slides.append(
            Slide(
                title="新聞主題分布",
                image_path=category_chart_path,
            )
        )

    grouped: dict[str, list[NewsItemLike]] = {}
    for item in items:
        grouped.setdefault(item.category, []).append(item)

    for category in ("index", "semiconductor", "tech", "market"):
        group = grouped.get(category, [])[:6]
        if not group:
            continue
        label = CATEGORY_LABELS.get(category, category)
        rows = [
            [item.title[:72] + ("…" if len(item.title) > 72 else ""), item.feed_name]
            for item in group
        ]
        slides.append(
            Slide(
                title=f"重點新聞 — {label}",
                table_headers=["標題", "來源"],
                table_rows=rows,
            )
        )

    return slides, generated_assets


def build_slide_deck(
    *,
    date_str: str,
    indices: list[IndexSnapshotLike],
    items: list[NewsItemLike],
    feed_count: int,
    agy_body: str,
    assets_dir: Path,
) -> list[Slide]:
    programmatic, _ = build_programmatic_slides(
        date_str=date_str,
        indices=indices,
        items=items,
        feed_count=feed_count,
        assets_dir=assets_dir,
    )
    analysis = parse_agy_slides(agy_body)
    if not analysis:
        analysis = [
            Slide(
                title="市場分析",
                bullets=[line.strip() for line in agy_body.splitlines() if line.strip()],
            )
        ]
    return programmatic + analysis


def write_slides_pdf(
    path: Path,
    *,
    date_str: str,
    slides: list[Slide],
) -> bool:
    font_info = _find_cjk_font()
    if not font_info:
        return False
    try:
        from fpdf import FPDF
        from fpdf.enums import Align, XPos, YPos
    except ImportError:
        return False

    font_path, collection_index = font_info
    path.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(SLIDE_MARGIN, SLIDE_MARGIN, SLIDE_MARGIN)
    pdf.add_font("CJK", "", str(font_path), collection_font_number=collection_index)

    page_width = pdf.w - 2 * SLIDE_MARGIN
    page_count = len(slides)

    for page_idx, slide in enumerate(slides, start=1):
        pdf.add_page()
        _draw_slide_header(pdf, slide)
        content_bottom = FOOTER_Y - 6

        if slide.center_title and not slide.table_rows and not slide.bullets:
            pdf.set_font("CJK", size=24)
            pdf.set_y(70)
            pdf.multi_cell(
                page_width,
                12,
                slide.title,
                align=Align.C,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            if slide.subtitle:
                pdf.ln(6)
                pdf.set_font("CJK", size=13)
                pdf.multi_cell(
                    page_width,
                    8,
                    slide.subtitle,
                    align=Align.C,
                    new_x=XPos.LMARGIN,
                    new_y=YPos.NEXT,
                )
        else:
            pdf.set_y(CONTENT_Y)
            if slide.image_path and slide.image_path.exists():
                image_width = min(page_width, 250.0)
                pdf.image(str(slide.image_path), w=image_width)
                pdf.ln(4)
            if slide.table_headers and slide.table_rows:
                _draw_table(
                    pdf,
                    headers=slide.table_headers,
                    rows=slide.table_rows,
                    max_y=content_bottom,
                )
            if slide.bullets:
                pdf.set_font("CJK", size=12)
                for bullet in slide.bullets:
                    if pdf.get_y() > content_bottom:
                        break
                    pdf.multi_cell(
                        page_width,
                        7,
                        f"• {bullet}",
                        new_x=XPos.LMARGIN,
                        new_y=YPos.NEXT,
                    )
                    pdf.ln(1)

        _draw_slide_footer(pdf, date_str=date_str, page_idx=page_idx, page_count=page_count)

    pdf.output(str(path))
    return True


def _draw_slide_header(pdf, slide: Slide) -> None:
    from fpdf.enums import XPos, YPos

    pdf.set_y(TITLE_Y)
    pdf.set_font("CJK", size=18)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(pdf.w - 2 * SLIDE_MARGIN, 10, slide.title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(37, 99, 235)
    pdf.set_line_width(0.6)
    y = pdf.get_y() + 1.5
    pdf.line(SLIDE_MARGIN, y, pdf.w - SLIDE_MARGIN, y)
    pdf.set_text_color(0, 0, 0)


def _draw_slide_footer(pdf, *, date_str: str, page_idx: int, page_count: int) -> None:
    from fpdf.enums import Align, XPos, YPos

    pdf.set_y(FOOTER_Y)
    pdf.set_font("CJK", size=9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0,
        6,
        f"美股科技市場日報 · {date_str}",
        align=Align.L,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_y(FOOTER_Y)
    pdf.cell(
        0,
        6,
        f"{page_idx} / {page_count}",
        align=Align.R,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_text_color(0, 0, 0)


def _draw_table(
    pdf,
    *,
    headers: list[str],
    rows: list[list[str]],
    max_y: float,
) -> None:
    col_count = len(headers)
    if col_count == 0:
        return

    if col_count == 2 and headers == ["標題", "來源"]:
        widths = [pdf.epw * 0.72, pdf.epw * 0.28]
    else:
        widths = [pdf.epw / col_count] * col_count

    try:
        from fpdf.fonts import FontFace
    except ImportError:
        FontFace = None  # type: ignore[misc, assignment]

    headings_style = (
        FontFace(fill_color=(239, 246, 255)) if FontFace is not None else None
    )
    table_kwargs: dict[str, Any] = {"col_widths": widths}
    if headings_style is not None:
        table_kwargs["headings_style"] = headings_style

    with pdf.table(**table_kwargs) as table:
        header_row = table.row()
        for header in headers:
            header_row.cell(header)
        for row in rows:
            if pdf.get_y() > max_y:
                break
            data_row = table.row()
            for cell in row:
                data_row.cell(cell)


def write_summary_slides_pdf(
    path: Path,
    *,
    date_str: str,
    indices: list[IndexSnapshotLike],
    items: list[NewsItemLike],
    feed_count: int,
    agy_body: str,
    assets_dir: Path,
) -> bool:
    slides = build_slide_deck(
        date_str=date_str,
        indices=indices,
        items=items,
        feed_count=feed_count,
        agy_body=agy_body,
        assets_dir=assets_dir,
    )
    return write_slides_pdf(path, date_str=date_str, slides=slides)
