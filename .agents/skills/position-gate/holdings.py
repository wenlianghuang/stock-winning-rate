"""Load holdings configuration for position-gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOLDINGS = Path(__file__).with_name("holdings.json")


@dataclass(frozen=True)
class HoldingRecord:
    stock_id: str
    avg_cost: float
    shares: int
    note: str = ""
    uses_margin: bool = False
    cash_shares: int = 0
    cash_avg_cost: float | None = None
    margin_shares: int = 0
    margin_avg_cost: float | None = None


def _parse_share_count(entry: dict) -> int:
    if "shares" in entry:
        shares = int(entry["shares"])
    elif "lots" in entry:
        shares = int(entry["lots"]) * 1000
    else:
        raise ValueError("須提供 shares 或 lots")
    if shares <= 0:
        raise ValueError("持股股數須 > 0")
    return shares


def blend_avg_cost(
    cash_shares: int,
    cash_avg_cost: float | None,
    margin_shares: int,
    margin_avg_cost: float | None,
) -> tuple[int, float]:
    """Return (total_shares, blended_avg_cost)."""
    total = int(cash_shares) + int(margin_shares)
    if total <= 0:
        raise ValueError("現股與融資股數合計須 > 0")
    cash_value = float(cash_shares) * float(cash_avg_cost or 0)
    margin_value = float(margin_shares) * float(margin_avg_cost or 0)
    if cash_shares > 0 and (cash_avg_cost is None or cash_avg_cost <= 0):
        raise ValueError("現股均價須 > 0")
    if margin_shares > 0 and (margin_avg_cost is None or margin_avg_cost <= 0):
        raise ValueError("融資均價須 > 0")
    return total, (cash_value + margin_value) / total


def make_holding_record(
    stock_id: str,
    *,
    avg_cost: float | None = None,
    shares: int | None = None,
    note: str = "",
    uses_margin: bool = False,
    cash_shares: int = 0,
    cash_avg_cost: float | None = None,
    margin_shares: int = 0,
    margin_avg_cost: float | None = None,
) -> HoldingRecord:
    cash_shares = max(0, int(cash_shares or 0))
    margin_shares = max(0, int(margin_shares or 0))

    has_legs = cash_shares > 0 or margin_shares > 0
    if has_legs:
        total, blended = blend_avg_cost(
            cash_shares, cash_avg_cost, margin_shares, margin_avg_cost
        )
        return HoldingRecord(
            stock_id=stock_id.strip(),
            avg_cost=float(blended),
            shares=int(total),
            note=note.strip(),
            uses_margin=margin_shares > 0,
            cash_shares=cash_shares,
            cash_avg_cost=float(cash_avg_cost) if cash_shares > 0 else None,
            margin_shares=margin_shares,
            margin_avg_cost=float(margin_avg_cost) if margin_shares > 0 else None,
        )

    if avg_cost is None or shares is None:
        raise ValueError("請提供現股／融資腿，或同時提供均價與股數")
    if avg_cost <= 0:
        raise ValueError("均價須 > 0")
    if shares <= 0:
        raise ValueError("持股股數須 > 0")

    # Legacy single-leg: --margin 表示整筆融資，否則現股
    if uses_margin:
        return HoldingRecord(
            stock_id=stock_id.strip(),
            avg_cost=float(avg_cost),
            shares=int(shares),
            note=note.strip(),
            uses_margin=True,
            cash_shares=0,
            cash_avg_cost=None,
            margin_shares=int(shares),
            margin_avg_cost=float(avg_cost),
        )
    return HoldingRecord(
        stock_id=stock_id.strip(),
        avg_cost=float(avg_cost),
        shares=int(shares),
        note=note.strip(),
        uses_margin=False,
        cash_shares=int(shares),
        cash_avg_cost=float(avg_cost),
        margin_shares=0,
        margin_avg_cost=None,
    )


def load_holdings(path: Path | None = None) -> dict[str, HoldingRecord]:
    holdings_path = path or DEFAULT_HOLDINGS
    if not holdings_path.exists():
        raise FileNotFoundError(f"找不到 holdings: {holdings_path}")

    raw = json.loads(holdings_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"holdings 為空或格式錯誤: {holdings_path}")

    holdings: dict[str, HoldingRecord] = {}
    for stock_id, entry in raw.items():
        if str(stock_id).startswith("_"):
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"holdings[{stock_id}] 須為物件")

        cash_shares = int(entry.get("cash_shares") or 0)
        margin_shares = int(entry.get("margin_shares") or 0)
        cash_avg = entry.get("cash_avg_cost")
        margin_avg = entry.get("margin_avg_cost")

        if cash_shares > 0 or margin_shares > 0:
            holdings[str(stock_id).strip()] = make_holding_record(
                str(stock_id).strip(),
                note=str(entry.get("note", "")).strip(),
                cash_shares=cash_shares,
                cash_avg_cost=float(cash_avg) if cash_avg is not None else None,
                margin_shares=margin_shares,
                margin_avg_cost=float(margin_avg) if margin_avg is not None else None,
            )
            continue

        avg_cost = float(entry["avg_cost"])
        shares = _parse_share_count(entry)
        holdings[str(stock_id).strip()] = make_holding_record(
            str(stock_id).strip(),
            avg_cost=avg_cost,
            shares=shares,
            note=str(entry.get("note", "")).strip(),
            uses_margin=bool(entry.get("uses_margin", False)),
        )
    return holdings


def get_holding(stock_id: str, path: Path | None = None) -> HoldingRecord:
    holdings = load_holdings(path)
    key = stock_id.strip()
    if key not in holdings:
        known = ", ".join(sorted(holdings))
        raise KeyError(f"holdings 中無 {key}（已知：{known}）")
    return holdings[key]


HOLDING_USAGE_HINT = (
    "請提供持股，例如：\n"
    "  position-gate 2409 --cash-shares 100000 --cash-cost 32.5\n"
    "  position-gate 2409 --margin-shares 50000 --margin-cost 30\n"
    "  position-gate 2409 --cash-shares 100000 --cash-cost 32.5 "
    "--margin-shares 50000 --margin-cost 30\n"
    "  position-gate 2409 32.5 500000 --margin\n"
    "或加 --all-holdings 讀取 holdings.json 整批處理。"
)


def resolve_holding(
    stock_id: str,
    holdings_path: Path | None,
    *,
    avg_cost: float | None = None,
    shares: int | None = None,
    note: str = "",
    uses_margin: bool = False,
    cash_shares: int | None = None,
    cash_avg_cost: float | None = None,
    margin_shares: int | None = None,
    margin_avg_cost: float | None = None,
    from_holdings_file: bool = False,
) -> HoldingRecord:
    """Resolve holding from CLI args or holdings.json (--from-holdings / fallback)."""
    has_legs = (cash_shares or 0) > 0 or (margin_shares or 0) > 0
    if has_legs or avg_cost is not None or shares is not None:
        return make_holding_record(
            stock_id,
            avg_cost=avg_cost,
            shares=shares,
            note=note,
            uses_margin=uses_margin,
            cash_shares=cash_shares or 0,
            cash_avg_cost=cash_avg_cost,
            margin_shares=margin_shares or 0,
            margin_avg_cost=margin_avg_cost,
        )

    path = holdings_path or DEFAULT_HOLDINGS
    if from_holdings_file or path.exists():
        try:
            return get_holding(stock_id, path)
        except FileNotFoundError:
            if from_holdings_file:
                raise
        except KeyError:
            if from_holdings_file:
                raise

    raise ValueError(HOLDING_USAGE_HINT)
