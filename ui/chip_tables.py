"""Build Markdown chip-data tables from a single-stock CSV row."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path


def load_csv_row(csv_path: Path) -> dict[str, str] | None:
    text = csv_path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(StringIO(text)))
    if len(rows) != 1:
        return None
    return rows[0]


def history_csv_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(f"{snapshot_path.stem}_history.csv")


def load_history_rows(csv_path: Path) -> list[dict[str, str]]:
    hist_path = history_csv_path(csv_path)
    if not hist_path.exists():
        return []
    text = hist_path.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(StringIO(text)))


def _cell(raw: str) -> str:
    return str(raw).strip() if raw is not None else ""


def _fmt_num(raw: str, *, signed: bool = False) -> str:
    text = _cell(raw)
    if not text:
        return "—"
    try:
        value = float(text)
        if value == int(value):
            formatted = f"{int(value):,}"
        else:
            formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
        if signed and value > 0:
            return f"+{formatted}"
        return formatted
    except ValueError:
        return text


def _fmt_pct(raw: str) -> str:
    text = _cell(raw)
    if not text:
        return "—"
    try:
        return f"{float(text):.2f}%"
    except ValueError:
        return text


def _fmt_rsi(raw: str) -> str:
    text = _cell(raw)
    if not text:
        return "—"
    try:
        from chip_signals import RSI_OVERBOUGHT, RSI_OVERSOLD

        value = float(text)
        if value >= RSI_OVERBOUGHT:
            zone = "偏高"
        elif value <= RSI_OVERSOLD:
            zone = "偏低"
        else:
            zone = "中性區"
        return f"{value:.1f}（{zone}）"
    except ValueError:
        return text


def _fmt_atr(raw_atr: str, raw_pct: str) -> str:
    atr_text = _cell(raw_atr)
    pct_text = _cell(raw_pct)
    if not atr_text and not pct_text:
        return "—"
    try:
        if atr_text and pct_text:
            return f"{float(atr_text):.2f} / {float(pct_text):.1f}%"
        if pct_text:
            return f"{float(pct_text):.1f}%"
        return f"{float(atr_text):.2f}"
    except ValueError:
        return pct_text or atr_text or "—"


def _fmt_margin_short_ratio(raw: str) -> str:
    text = _cell(raw)
    if not text:
        return "—"
    try:
        from chip_signals import (
            MARGIN_SHORT_RATIO_HIGH_PCT,
            MARGIN_SHORT_RATIO_LOW_PCT,
        )

        value = float(text)
        if value >= MARGIN_SHORT_RATIO_HIGH_PCT:
            zone = "偏高"
        elif value <= MARGIN_SHORT_RATIO_LOW_PCT:
            zone = "偏低"
        else:
            zone = "中性"
        return f"{value:.1f}%（{zone}）"
    except ValueError:
        return text


def _fmt_margin_momentum(raw: str) -> str:
    text = _cell(raw)
    if not text:
        return "—"
    try:
        from chip_signals import (
            MARGIN_MOMENTUM_COOLING_PCT,
            MARGIN_MOMENTUM_HEATING_PCT,
        )

        value = float(text)
        if value >= MARGIN_MOMENTUM_HEATING_PCT:
            zone = "偏強"
        elif value <= MARGIN_MOMENTUM_COOLING_PCT:
            zone = "偏弱"
        else:
            zone = "平穩"
        return f"{value:+.1f}%（{zone}）"
    except ValueError:
        return text


def _fmt_adx(raw: str) -> str:
    text = _cell(raw)
    if not text:
        return "—"
    try:
        from chip_signals import ADX_STRONG_THRESHOLD, ADX_WEAK_THRESHOLD

        value = float(text)
        if value >= ADX_STRONG_THRESHOLD:
            zone = "趨勢明確"
        elif value <= ADX_WEAK_THRESHOLD:
            zone = "趨勢偏弱"
        else:
            zone = "中性"
        return f"{value:.1f}（{zone}）"
    except ValueError:
        return text


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _build_chip_face_rows(row: dict[str, str]) -> list[list[str]]:
    major_ok = _cell(row.get("主力_擷取狀態", "")).lower() == "ok"
    chip_rows: list[list[str]] = [
        ["外資買賣超（張）", _fmt_num(row.get("外資買賣超_張", ""), signed=True)],
        ["投信買賣超（張）", _fmt_num(row.get("投信買賣超_張", ""), signed=True)],
        ["自營商買賣超（張）", _fmt_num(row.get("自營商買賣超_張", ""), signed=True)],
        ["融資餘額（張）", _fmt_num(row.get("融資今日餘額_張", ""))],
        ["融資增減（張）", _fmt_num(row.get("融資增減_張", ""), signed=True)],
        ["融券餘額（張）", _fmt_num(row.get("融券今日餘額_張", ""))],
        ["融券增減（張）", _fmt_num(row.get("融券增減_張", ""), signed=True)],
        ["借券賣出（張）", _fmt_num(row.get("借券賣出_張", ""))],
        ["券賣還券（張）", _fmt_num(row.get("券賣還券_張", ""))],
        ["當沖成交量（張）", _fmt_num(row.get("當沖成交量_張", ""))],
        ["當沖佔成交量", _fmt_pct(row.get("當沖佔成交量_%", ""))],
    ]
    if major_ok:
        chip_rows.extend(
            [
                [
                    "主力買賣超（張）",
                    _fmt_num(row.get("主力買賣超_張", ""), signed=True),
                ],
                ["主力佔成交量", _fmt_pct(row.get("主力佔成交量_%", ""))],
            ]
        )
    else:
        chip_rows.append(["主力", "未取得（主力_擷取狀態 ≠ ok）"])
    return chip_rows


def build_trend_summary_markdown(row: dict[str, str]) -> str | None:
    lookback = _cell(row.get("回看天數", ""))
    if not lookback:
        return None

    start_date = _cell(row.get("區間起始日", ""))
    trade_date = _cell(row.get("日期", ""))
    period_label = f"{lookback} 日"
    if start_date and trade_date:
        period_label = f"{lookback} 日（{start_date}～{trade_date}）"

    major_days = _cell(row.get("區間主力資料天數", ""))
    major_total = _fmt_num(row.get("區間主力累計_張", ""), signed=True)
    if major_days:
        major_total = f"{major_total}（{major_days}/{lookback} 日有資料）"

    summary_rows = [
        ["外資累計買賣超（張）", _fmt_num(row.get("區間外資累計_張", ""), signed=True)],
        ["投信累計買賣超（張）", _fmt_num(row.get("區間投信累計_張", ""), signed=True)],
        ["自營商累計買賣超（張）", _fmt_num(row.get("區間自營商累計_張", ""), signed=True)],
        ["主力累計買賣超（張）", major_total],
        [
            "融資餘額淨變化（張）",
            _fmt_num(row.get("區間融資餘額淨變化_張", ""), signed=True),
        ],
        [
            "融券餘額淨變化（張）",
            _fmt_num(row.get("區間融券餘額淨變化_張", ""), signed=True),
        ],
        ["成交量均值（張）", _fmt_num(row.get("區間成交量均值_張", ""))],
        ["當沖佔比均值", _fmt_pct(row.get("區間當沖佔比均值_%", ""))],
        ["區間漲跌幅", _fmt_pct(row.get("區間漲跌幅_%", ""))],
        ["MA5", _fmt_num(row.get("MA5", ""))],
        ["收盤偏離 MA5", _fmt_pct(row.get("收盤偏離MA5_%", ""))],
        ["MA10（10 日線）", _fmt_num(row.get("MA10", ""))],
        ["收盤偏離 MA10", _fmt_pct(row.get("收盤偏離MA10_%", ""))],
        ["MA20（月線）", _fmt_num(row.get("MA20", ""))],
        ["收盤偏離 MA20", _fmt_pct(row.get("收盤偏離MA20_%", ""))],
        ["RSI14（14日）", _fmt_rsi(row.get("RSI14", ""))],
        [
            "ATR14（14日）",
            _fmt_atr(row.get("ATR14", ""), row.get("ATR14_%", "")),
        ],
        ["ADX14（14日）", _fmt_adx(row.get("ADX14", ""))],
        ["券資比", _fmt_margin_short_ratio(row.get("券資比_%", ""))],
        ["融資動能", _fmt_margin_momentum(row.get("融資動能_%", ""))],
    ]

    return "\n".join(
        [
            f"### 區間趨勢摘要（近 {period_label}）",
            _md_table(["項目", "數值"], summary_rows),
        ]
    )


def build_market_context_markdown(row: dict[str, str]) -> str | None:
    close = _cell(row.get("大盤收盤", ""))
    if not close:
        return None

    market_rows = [
        ["加權指數收盤", _fmt_num(row.get("大盤收盤", ""))],
        ["大盤當日漲跌幅", _fmt_pct(row.get("大盤漲跌幅_%", ""))],
        ["大盤 MA5", _fmt_num(row.get("大盤MA5", ""))],
        ["大盤收盤偏離 MA5", _fmt_pct(row.get("大盤收盤偏離MA5_%", ""))],
        ["大盤 MA20（月線）", _fmt_num(row.get("大盤MA20", ""))],
        ["大盤收盤偏離 MA20", _fmt_pct(row.get("大盤收盤偏離MA20_%", ""))],
        ["大盤區間漲跌幅", _fmt_pct(row.get("大盤區間漲跌幅_%", ""))],
    ]

    return "\n".join(
        [
            "### 大盤脈絡（加權指數 TAIEX）",
            _md_table(["項目", "數值"], market_rows),
        ]
    )


def build_history_table_markdown(history_rows: list[dict[str, str]]) -> str | None:
    if not history_rows:
        return None

    table_rows: list[list[str]] = []
    for row in history_rows:
        major_ok = _cell(row.get("主力_擷取狀態", "")).lower() == "ok"
        major_cell = (
            _fmt_num(row.get("主力買賣超_張", ""), signed=True)
            if major_ok
            else "—"
        )
        table_rows.append(
            [
                _cell(row.get("日期", "")),
                _fmt_num(row.get("收盤價", "")),
                _fmt_num(row.get("漲跌幅", ""), signed=True),
                _fmt_num(row.get("成交量_張", "")),
                _fmt_num(row.get("外資買賣超_張", ""), signed=True),
                _fmt_num(row.get("投信買賣超_張", ""), signed=True),
                _fmt_num(row.get("自營商買賣超_張", ""), signed=True),
                _fmt_num(row.get("融資增減_張", ""), signed=True),
                _fmt_num(row.get("融券增減_張", ""), signed=True),
                _fmt_pct(row.get("當沖佔成交量_%", "")),
                major_cell,
            ]
        )

    return "\n".join(
        [
            "### 近 N 日歷史明細",
            _md_table(
                [
                    "日期",
                    "收盤",
                    "漲跌%",
                    "成交量",
                    "外資",
                    "投信",
                    "自營",
                    "融資Δ",
                    "融券Δ",
                    "當沖%",
                    "主力",
                ],
                table_rows,
            ),
        ]
    )


def build_chip_tables_markdown(
    row: dict[str, str],
    *,
    history_rows: list[dict[str, str]] | None = None,
) -> str:
    stock_id = _cell(row.get("代碼", ""))
    stock_name = _cell(row.get("名稱", ""))
    trade_date = _cell(row.get("日期", ""))

    sections: list[str] = [
        f"## 籌碼數據（{stock_name} {stock_id}）",
        f"**資料日期：** {trade_date}  ",
        f"**資料來源：** `{stock_id}` CSV（系統自動產生，數字以 CSV 為準）",
        "",
        "### 當日價量",
        _md_table(
            ["項目", "數值"],
            [
                ["開盤價", _fmt_num(row.get("開盤價", ""))],
                ["最高價", _fmt_num(row.get("最高價", ""))],
                ["最低價", _fmt_num(row.get("最低價", ""))],
                ["收盤價", _fmt_num(row.get("收盤價", ""))],
                ["漲跌幅", _fmt_num(row.get("漲跌幅", ""), signed=True)],
                ["成交量（張）", _fmt_num(row.get("成交量_張", ""))],
            ],
        ),
        "",
        "### 當日籌碼面",
        _md_table(["項目", "數值"], _build_chip_face_rows(row)),
    ]

    market_md = build_market_context_markdown(row)
    if market_md:
        sections.extend(["", market_md])

    trend_md = build_trend_summary_markdown(row)
    if trend_md:
        sections.extend(["", trend_md])

    history_md = build_history_table_markdown(history_rows or [])
    if history_md:
        sections.extend(["", history_md])

    return "\n".join(sections).strip() + "\n"


def merge_report_body(csv_path: Path, agy_body: str) -> str:
    row = load_csv_row(csv_path)
    if row is None:
        return agy_body.strip()
    history_rows = load_history_rows(csv_path)
    chip_md = build_chip_tables_markdown(row, history_rows=history_rows)
    analysis = agy_body.strip()
    if not analysis:
        return chip_md
    return f"{chip_md}\n\n---\n\n{analysis}\n"
