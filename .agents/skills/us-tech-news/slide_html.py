"""Build self-contained HTML slide deck for US tech news reports."""

from __future__ import annotations

import html
import re
from pathlib import Path

from slide_pdf import Slide, build_slide_deck

_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _inline_format(text: str) -> str:
    escaped = _escape(text)
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def _relative_asset(html_path: Path, asset_path: Path) -> str:
    return asset_path.resolve().relative_to(html_path.parent.resolve()).as_posix()


def _change_class(value: str) -> str:
    value = value.strip()
    if value.startswith("+"):
        return "change-up"
    if value.startswith("-"):
        return "change-down"
    return ""


def _render_table(headers: list[str], rows: list[list[str]], *, html_path: Path) -> str:
    head_html = "".join(f"<th>{_escape(h)}</th>" for h in headers)
    body_rows: list[str] = []
    for row in rows:
        cells: list[str] = []
        for idx, cell in enumerate(row):
            cls = ""
            if idx < len(headers) and headers[idx] == "漲跌幅":
                cls = _change_class(cell)
            class_attr = f' class="{cls}"' if cls else ""
            cells.append(f"<td{class_attr}>{_inline_format(cell)}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        f'<div class="table-wrap"><table>'
        f"<thead><tr>{head_html}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table></div>"
    )


def _render_bullets(bullets: list[str]) -> str:
    items = "".join(f"<li>{_inline_format(b)}</li>" for b in bullets)
    return f'<ul class="slide-list">{items}</ul>'


def _render_slide_content(
    slide: Slide,
    *,
    html_path: Path,
    page_idx: int,
    page_count: int,
    date_str: str,
) -> str:
    parts: list[str] = []

    if slide.center_title and not slide.table_rows and not slide.bullets:
        parts.append('<div class="cover-body">')
        parts.append(f'<h1 class="cover-title">{_escape(slide.title)}</h1>')
        if slide.subtitle:
            for line in slide.subtitle.splitlines():
                if line.strip():
                    parts.append(f'<p class="cover-sub">{_escape(line.strip())}</p>')
        parts.append("</div>")
        title_html = ""
    else:
        if slide.image_path and slide.image_path.exists():
            src = _relative_asset(html_path, slide.image_path)
            parts.append(
                f'<figure class="chart-figure">'
                f'<img src="{_escape(src)}" alt="{_escape(slide.title)}" loading="lazy">'
                f"</figure>"
            )
        if slide.table_headers and slide.table_rows:
            parts.append(
                _render_table(slide.table_headers, slide.table_rows, html_path=html_path)
            )
        if slide.bullets:
            parts.append(_render_bullets(slide.bullets))

        title_html = (
            f'<header class="slide-header">'
            f'<h2 class="slide-title">{_escape(slide.title)}</h2>'
            f"</header>"
        )

    footer = (
        f'<footer class="slide-footer">'
        f'<span class="footer-left">美股科技市場日報 · {_escape(date_str)}</span>'
        f'<span class="footer-right">{page_idx} / {page_count}</span>'
        f"</footer>"
    )

    inner = title_html + "".join(parts) + footer
    active = " active" if page_idx == 1 else ""
    return (
        f'<section class="slide{active}" id="slide-{page_idx}" '
        f'data-index="{page_idx}" aria-label="{_escape(slide.title)}">'
        f'<div class="slide-inner">{inner}</div>'
        f"</section>"
    )


def _deck_styles() -> str:
    return """
:root {
  --bg: #0f172a;
  --bg-slide: #ffffff;
  --text: #0f172a;
  --muted: #64748b;
  --accent: #2563eb;
  --accent-soft: #eff6ff;
  --border: #e2e8f0;
  --up: #059669;
  --down: #dc2626;
  --shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
  --font: "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", "Helvetica Neue", sans-serif;
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  height: 100%;
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
}
body.deck-mode { overflow: hidden; }

.toolbar {
  position: fixed;
  top: 0; left: 0; right: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 18px;
  background: rgba(15, 23, 42, 0.92);
  color: #e2e8f0;
  backdrop-filter: blur(8px);
  border-bottom: 1px solid rgba(148, 163, 184, 0.2);
}
.toolbar-title { font-size: 14px; font-weight: 600; letter-spacing: 0.02em; }
.toolbar-actions { display: flex; gap: 8px; align-items: center; }
.toolbar button, .toolbar .mode-link {
  appearance: none;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(255,255,255,0.06);
  color: #f8fafc;
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
}
.toolbar button:hover, .toolbar .mode-link:hover { background: rgba(255,255,255,0.12); }
.toolbar .counter { font-size: 13px; color: #94a3b8; min-width: 4.5em; text-align: center; }

.deck {
  position: fixed;
  inset: 52px 0 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.slide {
  display: none;
  width: min(1180px, 96vw);
  height: min(680px, calc(100vh - 100px));
  background: var(--bg-slide);
  border-radius: 18px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
.slide.active { display: block; }
.slide-inner {
  height: 100%;
  padding: 28px 36px 22px;
  display: flex;
  flex-direction: column;
}
.slide-header { margin-bottom: 18px; flex-shrink: 0; }
.slide-title {
  margin: 0;
  font-size: clamp(24px, 3vw, 32px);
  font-weight: 700;
  color: var(--text);
  line-height: 1.25;
  padding-bottom: 12px;
  border-bottom: 3px solid var(--accent);
}
.slide-inner > .cover-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 12px;
}
.cover-title {
  margin: 0;
  font-size: clamp(36px, 5vw, 52px);
  font-weight: 800;
  letter-spacing: 0.02em;
}
.cover-sub {
  margin: 0;
  font-size: clamp(16px, 2vw, 20px);
  color: var(--muted);
  line-height: 1.6;
}
.chart-figure {
  margin: 0 0 12px;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
}
.chart-figure img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 10px;
}
.table-wrap {
  flex: 1;
  overflow: auto;
  margin-top: 4px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  line-height: 1.5;
}
thead th {
  position: sticky;
  top: 0;
  background: var(--accent-soft);
  color: #1e3a8a;
  font-weight: 700;
  text-align: left;
  padding: 10px 12px;
  border-bottom: 2px solid #bfdbfe;
  white-space: nowrap;
}
tbody td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  word-break: break-word;
}
tbody tr:nth-child(even) td { background: #f8fafc; }
tbody tr:hover td { background: #f1f5f9; }
td.change-up { color: var(--up); font-weight: 700; }
td.change-down { color: var(--down); font-weight: 700; }
.slide-list {
  flex: 1;
  overflow: auto;
  margin: 0;
  padding: 0 0 0 1.25em;
  font-size: clamp(15px, 1.55vw, 17px);
  line-height: 1.75;
}
.slide-list li { margin-bottom: 0.65em; padding-left: 0.25em; }
.slide-list li::marker { color: var(--accent); }
.slide-footer {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
  padding-top: 14px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--muted);
}

.scroll-mode .toolbar { position: sticky; }
.scroll-mode .deck {
  position: static;
  inset: auto;
  flex-direction: column;
  gap: 28px;
  padding: 24px 24px 48px;
  display: flex;
}
.scroll-mode .slide {
  display: block;
  height: auto;
  min-height: min(680px, 80vh);
}
.scroll-mode .slide-inner { min-height: min(640px, 78vh); }

@media print {
  .toolbar { display: none; }
  body { background: white; }
  .deck { position: static; padding: 0; display: block; }
  .slide {
    display: block !important;
    width: 100%;
    height: auto;
    min-height: 100vh;
    page-break-after: always;
    box-shadow: none;
    border-radius: 0;
  }
}
@media (max-width: 720px) {
  .slide-inner { padding: 20px 18px 16px; }
  thead th, tbody td { padding: 8px 10px; font-size: 13px; }
}
"""


def _deck_script(page_count: int) -> str:
    return f"""
(function() {{
  const slides = Array.from(document.querySelectorAll('.slide'));
  const total = slides.length;
  let index = 0;
  const counter = document.getElementById('slide-counter');
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnScroll = document.getElementById('btn-scroll');
  const body = document.body;

  function show(i) {{
    index = Math.max(0, Math.min(total - 1, i));
    slides.forEach((slide, n) => slide.classList.toggle('active', n === index));
    if (counter) counter.textContent = (index + 1) + ' / ' + total;
    location.hash = 'slide-' + (index + 1);
  }}

  function next() {{ show(index + 1); }}
  function prev() {{ show(index - 1); }}

  btnNext && btnNext.addEventListener('click', next);
  btnPrev && btnPrev.addEventListener('click', prev);
  btnScroll && btnScroll.addEventListener('click', () => {{
    body.classList.toggle('scroll-mode');
    body.classList.toggle('deck-mode', !body.classList.contains('scroll-mode'));
    btnScroll.textContent = body.classList.contains('scroll-mode') ? '簡報模式' : '捲動模式';
  }});

  document.addEventListener('keydown', (e) => {{
    if (body.classList.contains('scroll-mode')) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ') {{ e.preventDefault(); next(); }}
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {{ e.preventDefault(); prev(); }}
    if (e.key === 'Home') {{ e.preventDefault(); show(0); }}
    if (e.key === 'End') {{ e.preventDefault(); show(total - 1); }}
  }});

  const hash = location.hash.match(/slide-(\\d+)/);
  if (hash) show(parseInt(hash[1], 10) - 1);
  else show(0);
}})();
"""


def write_slides_html(
    path: Path,
    *,
    date_str: str,
    slides: list[Slide],
) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    page_count = len(slides)
    slide_html = [
        _render_slide_content(
            slide,
            html_path=path,
            page_idx=idx,
            page_count=page_count,
            date_str=date_str,
        )
        for idx, slide in enumerate(slides, start=1)
    ]

    doc = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>美股科技市場日報 · {_escape(date_str)}</title>
  <style>{_deck_styles()}</style>
</head>
<body class="deck-mode">
  <div class="toolbar">
    <div class="toolbar-title">美股科技市場日報 · {_escape(date_str)}</div>
    <div class="toolbar-actions">
      <button type="button" id="btn-prev" aria-label="上一頁">← 上一頁</button>
      <span class="counter" id="slide-counter">1 / {page_count}</span>
      <button type="button" id="btn-next" aria-label="下一頁">下一頁 →</button>
      <button type="button" id="btn-scroll">捲動模式</button>
    </div>
  </div>
  <main class="deck">
    {"".join(slide_html)}
  </main>
  <script>{_deck_script(page_count)}</script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return True


def write_summary_slides_html(
    path: Path,
    *,
    date_str: str,
    indices,
    items,
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
    return write_slides_html(path, date_str=date_str, slides=slides)
