"""Write stock summary artifacts (Markdown + PDF) from CSV report paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chip_tables import merge_report_body

LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]+\)")
INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET_PATTERN = re.compile(r"^(\s*)[*\-]\s+(.*)$")
TABLE_ROW_PATTERN = re.compile(r"^\s*\|")
TABLE_SEP_PATTERN = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")
_PDF_UNSUPPORTED_SYMBOLS_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    r"]\s*",
    flags=re.UNICODE,
)


@dataclass(frozen=True)
class ReportMeta:
    source: str
    version: str
    duration_sec: float | None = None
    job_id: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True)
class MarkdownTable:
    headers: list[str]
    rows: list[list[str]]


@dataclass(frozen=True)
class MarkdownBlock:
    kind: str
    text: str = ""
    level: int = 0
    items: tuple[str, ...] = ()
    table: MarkdownTable | None = None


def summary_paths_for_csv(csv_path: Path) -> tuple[Path, Path]:
    return csv_path.with_suffix(".md"), csv_path.with_suffix(".pdf")


def position_paths_for_csv(csv_path: Path) -> tuple[Path, Path]:
    stem = csv_path.stem + "_position"
    return (
        csv_path.with_name(f"{stem}.md"),
        csv_path.with_name(f"{stem}.pdf"),
    )


def format_report_metadata(meta: ReportMeta) -> str:
    lines = [
        f"- **版本：** {meta.version}",
        f"- **來源：** {meta.source}",
    ]
    if meta.job_id:
        lines.append(f"- **任務 ID：** `{meta.job_id}`")
    if meta.duration_sec is not None:
        lines.append(f"- **耗時：** {meta.duration_sec:.1f} 秒")
    if meta.exit_code is not None:
        lines.append(f"- **exit code：** {meta.exit_code}")
    return "\n".join(lines)


def sanitize_pdf_text(text: str) -> str:
    return _PDF_UNSUPPORTED_SYMBOLS_RE.sub("", text)


def plain_text_line(line: str) -> str:
    text = line.rstrip()
    if not text.strip():
        return ""

    text = sanitize_pdf_text(text)
    text = LINK_PATTERN.sub(r"\1", text)
    text = INLINE_CODE_PATTERN.sub(r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = text.replace("---", "—")

    heading = HEADING_PATTERN.match(text)
    if heading:
        level = len(heading.group(1))
        prefix = " " * (level - 1) * 2
        return f"{prefix}{heading.group(2).strip()}"

    bullet = BULLET_PATTERN.match(text)
    if bullet:
        indent = "  " * (len(bullet.group(1)) // 2 + 1)
        return f"{indent}• {bullet.group(2).strip()}"

    return sanitize_pdf_text(text.strip())


def _split_table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _parse_table_lines(lines: list[str]) -> MarkdownTable:
    headers = _split_table_cells(lines[0])
    rows: list[list[str]] = []
    for line in lines[1:]:
        if TABLE_SEP_PATTERN.match(line):
            continue
        rows.append(_split_table_cells(line))
    return MarkdownTable(headers=headers, rows=rows)


def _is_block_start(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if HEADING_PATTERN.match(stripped):
        return True
    if TABLE_ROW_PATTERN.match(stripped):
        return True
    if BULLET_PATTERN.match(stripped):
        return True
    if stripped == "---":
        return True
    return False


def parse_markdown_blocks(text: str) -> list[MarkdownBlock]:
    blocks: list[MarkdownBlock] = []
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped == "---":
            blocks.append(MarkdownBlock(kind="hr"))
            index += 1
            continue

        heading = HEADING_PATTERN.match(stripped)
        if heading:
            blocks.append(
                MarkdownBlock(
                    kind="heading",
                    level=len(heading.group(1)),
                    text=heading.group(2).strip(),
                )
            )
            index += 1
            continue

        if TABLE_ROW_PATTERN.match(stripped):
            table_lines: list[str] = []
            while index < len(lines) and TABLE_ROW_PATTERN.match(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            blocks.append(
                MarkdownBlock(kind="table", table=_parse_table_lines(table_lines))
            )
            continue

        if BULLET_PATTERN.match(stripped):
            items: list[str] = []
            while index < len(lines) and BULLET_PATTERN.match(lines[index].strip()):
                match = BULLET_PATTERN.match(lines[index].strip())
                if match:
                    items.append(match.group(2).strip())
                index += 1
            blocks.append(MarkdownBlock(kind="bullets", items=tuple(items)))
            continue

        paragraph_lines: list[str] = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index]
            if not next_line.strip() or _is_block_start(next_line):
                break
            paragraph_lines.append(next_line.rstrip())
            index += 1
        blocks.append(MarkdownBlock(kind="text", text="\n".join(paragraph_lines)))

    return blocks


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


def _markdown_for_report(
    csv_path: Path,
    body: str,
    meta: ReportMeta,
    *,
    title: str = "台股籌碼分析報告",
) -> str:
    return (
        f"# {title}\n\n"
        f"**資料檔：** `{csv_path.name}`\n\n"
        f"## 報告資訊\n\n"
        f"{format_report_metadata(meta)}\n\n"
        f"---\n\n"
        f"{body.strip()}\n"
    )


def write_summary_markdown(
    md_path: Path, csv_path: Path, body: str, meta: ReportMeta
) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        _markdown_for_report(csv_path, body, meta),
        encoding="utf-8",
    )


def write_position_markdown(
    md_path: Path, csv_path: Path, body: str, meta: ReportMeta
) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        _markdown_for_report(
            csv_path,
            body,
            meta,
            title="台股持股部位決策報告",
        ),
        encoding="utf-8",
    )


def _pdf_write_line(pdf, text: str, *, line_height: float, size: int | None = None) -> None:
    from fpdf.enums import XPos, YPos

    if size is not None:
        pdf.set_font("CJK", size=size)
    pdf.multi_cell(
        pdf.epw,
        line_height,
        text if text else " ",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )


def _table_col_widths(pdf, col_count: int, headers: list[str]) -> list[float]:
    if col_count == 2 and headers == ["項目", "數值"]:
        return [pdf.epw * 0.38, pdf.epw * 0.62]
    if col_count == 2 and headers == ["法人", "買賣超"]:
        return [pdf.epw * 0.35, pdf.epw * 0.65]
    if col_count == 3 and headers == ["項目", "今日餘額", "增減"]:
        return [pdf.epw * 0.28, pdf.epw * 0.36, pdf.epw * 0.36]
    if col_count == 4:
        return [pdf.epw * 0.18, pdf.epw * 0.34, pdf.epw * 0.16, pdf.epw * 0.32]
    return [pdf.epw / col_count] * col_count


def _draw_pdf_table(pdf, table: MarkdownTable) -> None:
    col_count = len(table.headers)
    if col_count == 0:
        return

    widths = _table_col_widths(pdf, col_count, table.headers)
    try:
        from fpdf.fonts import FontFace
    except ImportError:
        FontFace = None  # type: ignore[misc, assignment]

    headings_style = (
        FontFace(fill_color=(239, 246, 255)) if FontFace is not None else None
    )
    table_kwargs: dict[str, Any] = {"col_widths": widths, "line_height": 6}
    if headings_style is not None:
        table_kwargs["headings_style"] = headings_style

    with pdf.table(**table_kwargs) as pdf_table:
        header_row = pdf_table.row()
        for header in table.headers:
            header_row.cell(sanitize_pdf_text(header))
        for row in table.rows:
            data_row = pdf_table.row()
            for cell in row:
                data_row.cell(sanitize_pdf_text(cell))


def _render_markdown_body_to_pdf(pdf, body: str) -> None:
    blocks = parse_markdown_blocks(body)
    for block in blocks:
        if block.kind == "hr":
            pdf.ln(2)
            continue
        if block.kind == "heading":
            size = max(10, 14 - block.level)
            _pdf_write_line(
                pdf,
                plain_text_line(f"{'#' * block.level} {block.text}"),
                line_height=7,
                size=size,
            )
            pdf.ln(1)
            pdf.set_font("CJK", size=11)
            continue
        if block.kind == "table" and block.table is not None:
            _draw_pdf_table(pdf, block.table)
            pdf.ln(2)
            continue
        if block.kind == "bullets":
            for item in block.items:
                _pdf_write_line(
                    pdf,
                    plain_text_line(f"- {item}"),
                    line_height=6,
                )
            pdf.ln(1)
            continue
        if block.kind == "text":
            for line in block.text.splitlines():
                rendered = plain_text_line(line)
                if rendered:
                    _pdf_write_line(pdf, rendered, line_height=6)
            pdf.ln(1)


def write_summary_pdf(
    pdf_path: Path, csv_path: Path, body: str, meta: ReportMeta
) -> bool:
    return _write_report_pdf(
        pdf_path,
        csv_path,
        body,
        meta,
        title="台股籌碼分析報告（深度分析）",
    )


def write_position_pdf(
    pdf_path: Path, csv_path: Path, body: str, meta: ReportMeta
) -> bool:
    return _write_report_pdf(
        pdf_path,
        csv_path,
        body,
        meta,
        title="台股持股部位決策報告",
    )


def _write_report_pdf(
    pdf_path: Path,
    csv_path: Path,
    body: str,
    meta: ReportMeta,
    *,
    title: str,
) -> bool:
    font_info = _find_cjk_font()
    if not font_info:
        return False

    try:
        from fpdf import FPDF
    except ImportError:
        return False

    font_path, collection_index = font_info
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    pdf.add_font(
        "CJK",
        "",
        str(font_path),
        collection_font_number=collection_index,
    )
    pdf.set_font("CJK", size=12)

    version_label = title
    _pdf_write_line(pdf, version_label, line_height=8)
    pdf.ln(2)
    pdf.set_font("CJK", size=10)
    _pdf_write_line(pdf, f"資料檔：{csv_path.name}", line_height=6)
    for line in format_report_metadata(meta).splitlines():
        _pdf_write_line(
            pdf,
            plain_text_line(line) or line.lstrip("- ").replace("**", ""),
            line_height=6,
        )
    pdf.ln(2)
    pdf.set_font("CJK", size=11)

    _render_markdown_body_to_pdf(pdf, body.strip())

    pdf.output(str(pdf_path))
    return True


def write_summary_artifacts(
    csv_path: Path,
    body: str,
    meta: ReportMeta,
) -> tuple[Path, Path | None]:
    """Write Markdown and optional PDF next to the CSV. Returns (md_path, pdf_path)."""
    full_body = merge_report_body(csv_path, body)
    md_path, pdf_path = summary_paths_for_csv(csv_path)
    write_summary_markdown(md_path, csv_path, full_body, meta)
    pdf_ok = write_summary_pdf(pdf_path, csv_path, full_body, meta)
    return md_path, pdf_path if pdf_ok else None


def write_position_artifacts(
    csv_path: Path,
    body: str,
    meta: ReportMeta,
    holding,
) -> tuple[Path, Path | None]:
    """Write position Markdown/PDF next to the CSV. Returns (md_path, pdf_path)."""
    from position_tables import merge_position_report_body

    full_body = merge_position_report_body(csv_path, holding, body)
    md_path, pdf_path = position_paths_for_csv(csv_path)
    write_position_markdown(md_path, csv_path, full_body, meta)
    pdf_ok = write_position_pdf(pdf_path, csv_path, full_body, meta)
    return md_path, pdf_path if pdf_ok else None
