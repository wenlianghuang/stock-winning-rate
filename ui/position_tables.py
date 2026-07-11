"""Build Markdown position-context tables for position reports."""

from __future__ import annotations

from pathlib import Path

from chip_tables import (
    _fmt_adx,
    _fmt_atr,
    _fmt_margin_momentum,
    _fmt_margin_short_ratio,
    _fmt_rsi,
    load_csv_row,
    merge_report_body,
)
from position_prompts import HoldingInfo


def _fmt_num(value: float, *, signed: bool = False) -> str:
    if value == int(value):
        formatted = f"{int(value):,}"
    else:
        formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    if signed and value > 0:
        return f"+{formatted}"
    return formatted


def compute_unrealized_pnl_pct(avg_cost: float, close_price: float) -> float:
    if avg_cost <= 0:
        return 0.0
    return (close_price - avg_cost) / avg_cost * 100.0


def parse_close_price(row: dict[str, str]) -> float | None:
    raw = str(row.get("收盤價", "")).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_position_summary_markdown(
    holding: HoldingInfo,
    row: dict[str, str],
) -> str:
    stock_name = str(row.get("名稱", holding.stock_id)).strip()
    stock_id = str(row.get("代碼", holding.stock_id)).strip()
    trade_date = str(row.get("日期", "")).strip()
    close_price = parse_close_price(row)
    pnl_pct = (
        compute_unrealized_pnl_pct(holding.avg_cost, close_price)
        if close_price is not None
        else None
    )
    ma20_raw = str(row.get("MA20", "")).strip()
    ma20_dev = str(row.get("收盤偏離MA20_%", "")).strip()

    rows: list[list[str]] = [
        ["持股均價（元）", _fmt_num(holding.avg_cost)],
        ["持股股數", _fmt_num(float(holding.shares))],
        ["收盤價（元）", _fmt_num(close_price) if close_price is not None else "—"],
    ]
    if pnl_pct is not None:
        rows.append(["未實現損益", f"{pnl_pct:+.2f}%"])
        cost_diff = (close_price or 0) - holding.avg_cost
        rows.append(["現價 vs 均價（元）", _fmt_num(cost_diff, signed=True)])
    if ma20_raw:
        rows.append(["MA20（月線）", ma20_raw])
    if ma20_dev:
        rows.append(["收盤偏離 MA20", f"{ma20_dev}%"])
    rsi_cell = _fmt_rsi(str(row.get("RSI14", "")))
    if rsi_cell != "—":
        rows.append(["RSI14（14日）", rsi_cell])
    atr_cell = _fmt_atr(str(row.get("ATR14", "")), str(row.get("ATR14_%", "")))
    if atr_cell != "—":
        rows.append(["ATR14（14日）", atr_cell])
    adx_cell = _fmt_adx(str(row.get("ADX14", "")))
    if adx_cell != "—":
        rows.append(["ADX14（14日）", adx_cell])
    ratio_cell = _fmt_margin_short_ratio(str(row.get("券資比_%", "")))
    if ratio_cell != "—":
        rows.append(["券資比", ratio_cell])
    momentum_cell = _fmt_margin_momentum(str(row.get("融資動能_%", "")))
    if momentum_cell != "—":
        rows.append(["融資動能", momentum_cell])
    high_20d = str(row.get("區間20日高", "")).strip()
    low_20d = str(row.get("區間20日低", "")).strip()
    if low_20d:
        rows.append(["近20日低（停損參考）", low_20d])
    if high_20d:
        rows.append(["近20日高（停利/壓力參考）", high_20d])
    if holding.note.strip():
        rows.append(["備註", holding.note.strip()])

    table_lines = [
        "| 項目 | 數值 |",
        "| --- | --- |",
    ]
    for item_row in rows:
        table_lines.append(f"| {item_row[0]} | {item_row[1]} |")

    return "\n".join(
        [
            f"## 部位摘要（{stock_name} {stock_id}）",
            f"**資料日期：** {trade_date}  ",
            f"**資料來源：** holdings + CSV（系統自動產生）",
            "",
            "\n".join(table_lines),
        ]
    )


def merge_position_report_body(
    csv_path: Path,
    holding: HoldingInfo,
    agy_body: str,
) -> str:
    row = load_csv_row(csv_path)
    if row is None:
        return agy_body.strip()

    chip_and_analysis = merge_report_body(csv_path, "")
    position_md = build_position_summary_markdown(holding, row)
    analysis = agy_body.strip()

    parts = [chip_and_analysis.strip(), position_md]
    if analysis:
        parts.append(f"---\n\n{analysis}")
    return "\n\n".join(part for part in parts if part.strip()) + "\n"
