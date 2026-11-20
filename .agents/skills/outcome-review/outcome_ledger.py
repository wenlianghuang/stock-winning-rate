"""Freeze an eval-calls payload into the table the narrative may quote."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import eval_calls  # noqa: E402


def format_excess(value: float | None) -> str:
    if value is None:
        return "無"
    return f"{value:+.2f}%"


def _pct_text(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return format_excess(float(value))


def _price_text(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return f"{float(value):.2f}"


def plain_row(row: dict[str, Any], *, as_of: str | None) -> str:
    """One sentence a reader can follow without knowing the scoring jargon."""
    if row.get("verdict") not in ("hit", "miss"):
        return ""
    excess = row.get("excess_return_pct")
    if not isinstance(excess, (int, float)):
        return ""
    call = str(row.get("call") or "")
    relative = "比大盤強" if float(excess) > 0 else "比大盤弱"
    settle = str(row.get("fwd_date") or as_of or "5 個交易日後")
    move_bits: list[str] = []
    entry = row.get("entry_close_text")
    fwd = row.get("fwd_close_text")
    if entry and fwd:
        move_bits.append(f"股價從 {entry} 到 {fwd}")
    stock_ret = row.get("stock_return_text")
    market_ret = row.get("market_return_text")
    if stock_ret and market_ret:
        move_bits.append(f"個股 {stock_ret}、加權 {market_ret}")
    move = "，".join(move_bits)
    if move:
        path = f"到 {settle}，{move}，這段{relative}（超額 {row['excess_text']}）。"
    else:
        path = f"到 {settle}，這段{relative}（超額 {row['excess_text']}）。"
    if row["verdict"] == "hit":
        wanted = "比大盤強" if call == "偏多" else "比大盤弱"
        ending = f"{call}要的是{wanted}，結果{relative}，所以這筆方向一致，算命中。"
    else:
        wanted = "比大盤弱" if call == "偏空" else "比大盤強"
        ending = f"{call}要的是{wanted}，結果{relative}，所以這筆方向相反。"
    return (
        f"{row['trade_date']} 的 {row['stock_id']} 當時被記成{call}。"
        f"{path}{ending}"
        f"{settle} 只是用來對帳的收盤，不是新的看多或看空。"
    )


def accuracy_sentence(payload: dict[str, Any]) -> str:
    line = eval_calls._accuracy_line(payload["accuracy"])
    if line.startswith("- "):
        return line[2:]
    return line


def build_ledger(
    payload: dict[str, Any],
    *,
    scope: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Copy scored rows. The narrative is not allowed to recompute these."""
    counts = {
        "hit": int(payload["accuracy"]["hits"]),
        "miss": int(payload["accuracy"]["misses"]),
        "abstain": int(payload["accuracy"]["abstain"]),
        "flat": int(payload["accuracy"]["flat"]),
        "pending": int(payload["accuracy"]["pending"]),
    }
    failures = payload.get("failure_counts") or {}
    rows: list[dict[str, Any]] = []
    issue_codes: list[str] = []
    seen_codes: set[str] = set()
    for raw in payload.get("rows") or []:
        excess = raw.get("excess_return_pct")
        codes = [str(code) for code in (raw.get("issue_codes") or [])]
        row_failures = [str(item) for item in (raw.get("failures") or [])]
        if "writing" in row_failures:
            for code in codes:
                if code not in seen_codes:
                    seen_codes.add(code)
                    issue_codes.append(code)
        row = {
            "trade_date": str(raw.get("trade_date") or ""),
            "stock_id": str(raw.get("stock_id") or ""),
            "call": str(raw.get("call") or "中性"),
            "excess_return_pct": excess,
            "excess_text": format_excess(excess if isinstance(excess, (int, float)) else None),
            "stock_return_pct": raw.get("stock_return_pct"),
            "stock_return_text": _pct_text(raw.get("stock_return_pct")),
            "market_return_pct": raw.get("market_return_pct"),
            "market_return_text": _pct_text(raw.get("market_return_pct")),
            "entry_close": raw.get("entry_close"),
            "entry_close_text": _price_text(raw.get("entry_close")),
            "fwd_close": raw.get("fwd_close"),
            "fwd_close_text": _price_text(raw.get("fwd_close")),
            "fwd_date": raw.get("fwd_date") or None,
            "verdict": str(raw.get("verdict") or ""),
            "failures": row_failures,
            "issue_codes": codes,
        }
        row["plain"] = plain_row(row, as_of=as_of)
        rows.append(row)
    accuracy = payload["accuracy"].get("accuracy")
    return {
        "scope": scope,
        "horizon": str((payload.get("scope") or {}).get("horizon") or eval_calls.DEFAULT_HORIZON),
        "epsilon_pct": float(payload.get("epsilon_pct") or eval_calls.EPSILON_PCT),
        "accuracy_sentence": accuracy_sentence(payload),
        "accuracy_pct": None if accuracy is None else round(float(accuracy) * 100, 2),
        "scored": int(payload["accuracy"]["scored"]),
        "counts": counts,
        "failure_counts": {
            "judgment": int(failures.get("judgment") or 0),
            "writing": int(failures.get("writing") or 0),
            "operational": int(failures.get("operational") or 0),
        },
        "issue_codes": issue_codes,
        "rows": rows,
    }


def ledger_summary(ledger: dict[str, Any]) -> str:
    counts = ledger["counts"]
    failures = ledger["failure_counts"]
    lines = [
        f"範圍：{ledger['scope']}",
        "怎麼讀：被檢查的是「當時被記成」那天的方向。後面的日期只是 5 個交易日後的收盤，不是新的方向。",
        "超額 = 個股漲跌 − 大盤漲跌。正數是比大盤強，負數是比大盤弱。",
        "偏空後來比大盤強，就是相反。偏空後來比大盤弱，就是命中。偏多後來比大盤弱，就是相反。",
        "有方向的每一筆，「人話」必須原句照抄，不要改數字，也不要把結算日寫成另一個方向。",
        f"方向門檻：超額絕對值須大於 {ledger['epsilon_pct']}%。",
        f"準確率句：{ledger['accuracy_sentence']}",
        (
            "件數：命中 {hit}、相反 {miss}、中性 {abstain}、"
            "未過門檻 {flat}、尚未成熟 {pending}、可計分 {scored}"
        ).format(scored=ledger["scored"], **counts),
        (
            "失敗件數：判斷 {judgment}、寫作 {writing}、作業 {operational}"
        ).format(**failures),
        f"寫作 issue code：{', '.join(ledger['issue_codes']) or '無'}",
        "",
        "列（excess 為無代表這個期限還沒有超額）：",
    ]
    for row in ledger["rows"]:
        fails = ",".join(row["failures"]) or "無"
        codes = ",".join(row["issue_codes"]) or "無"
        lines.append(
            f"- {row['trade_date']} {row['stock_id']} {row['call']} "
            f"excess={row['excess_text']} verdict={row['verdict']} "
            f"failures={fails} issue_codes={codes}"
        )
        if row.get("plain"):
            lines.append(f"  人話：{row['plain']}")
    if not ledger["rows"]:
        lines.append("- （沒有列）")
    return "\n".join(lines) + "\n"


LOOKBACK_SESSIONS = 5


def entry_date_before_latest(
    trading_dates: list[str],
    *,
    sessions: int = LOOKBACK_SESSIONS,
) -> tuple[str, str] | None:
    """Return (entry, latest) where latest is the 5th trading session after entry.

    ``sessions`` counts forward closes after the judgment day, not the judgment
    day itself. A latest close of 2026-11-17 is the 11/10 judgment.
    """
    dates = sorted({day for day in trading_dates if day})
    if sessions < 1 or len(dates) <= sessions:
        return None
    return dates[-1 - sessions], dates[-1]


def scope_label(
    trade_date: str | None,
    stock: str | None,
    *,
    as_of: str | None = None,
) -> str:
    if as_of and trade_date:
        base = f"以 {as_of} 收盤回看，對帳 {trade_date} 的判斷"
        if stock:
            return f"{base}（{stock}）"
        return base
    if trade_date and stock:
        return f"{trade_date} {stock}"
    if trade_date:
        return trade_date
    if stock:
        return stock
    return "全部已產出的個股報告"
