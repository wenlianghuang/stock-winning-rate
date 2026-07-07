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
        avg_cost = float(entry["avg_cost"])
        if avg_cost <= 0:
            raise ValueError(f"holdings[{stock_id}].avg_cost 須 > 0")
        shares = _parse_share_count(entry)
        holdings[str(stock_id).strip()] = HoldingRecord(
            stock_id=str(stock_id).strip(),
            avg_cost=avg_cost,
            shares=shares,
            note=str(entry.get("note", "")).strip(),
        )
    return holdings


def get_holding(stock_id: str, path: Path | None = None) -> HoldingRecord:
    holdings = load_holdings(path)
    key = stock_id.strip()
    if key not in holdings:
        known = ", ".join(sorted(holdings))
        raise KeyError(f"holdings 中無 {key}（已知：{known}）")
    return holdings[key]


def make_holding_record(
    stock_id: str,
    *,
    avg_cost: float,
    shares: int,
    note: str = "",
) -> HoldingRecord:
    if avg_cost <= 0:
        raise ValueError("均價須 > 0")
    if shares <= 0:
        raise ValueError("持股股數須 > 0")
    return HoldingRecord(
        stock_id=stock_id.strip(),
        avg_cost=float(avg_cost),
        shares=int(shares),
        note=note.strip(),
    )


HOLDING_USAGE_HINT = (
    "請提供持股均價與股數，例如：\n"
    "  position-gate 2409 32.5 500000\n"
    "  position-gate 2409 --avg-cost 32.5 --shares 500000\n"
    "  Chat：/position 2409 32.5 500000\n"
    "或加 --all-holdings 讀取 holdings.json 整批處理。"
)


def resolve_holding(
    stock_id: str,
    holdings_path: Path | None,
    *,
    avg_cost: float | None = None,
    shares: int | None = None,
    note: str = "",
    from_holdings_file: bool = False,
) -> HoldingRecord:
    """Resolve holding from CLI args or holdings.json (--from-holdings / fallback)."""
    if avg_cost is not None or shares is not None:
        if avg_cost is None or shares is None:
            raise ValueError("請同時提供均價與股數（--avg-cost 與 --shares）")
        return make_holding_record(
            stock_id,
            avg_cost=avg_cost,
            shares=shares,
            note=note,
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
