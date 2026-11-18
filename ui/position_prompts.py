"""Shared agy prompt instructions for position-aware stock analysis.

Writing rules live in the position-narrative skill. This module only attaches
the facts and the holding for the current run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_SKILL_PATH = (
    Path(__file__).resolve().parent.parent / ".agents" / "skills" / "position-narrative" / "SKILL.md"
)


def narrative_rules() -> str:
    """Return the skill body. Frontmatter and the title stay out of the prompt."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{_SKILL_PATH} 缺少 frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{_SKILL_PATH} 的 frontmatter 沒有結束")
    body = text[end + 4 :].strip()
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        body = "\n".join(lines[1:]).strip()
    if "## 一、部位現況" not in body:
        raise ValueError(f"{_SKILL_PATH} 缺少部位現況章節規格")
    return body


@dataclass(frozen=True)
class HoldingInfo:
    stock_id: str
    avg_cost: float
    shares: int
    note: str = ""
    uses_margin: bool = False
    cash_shares: int = 0
    cash_avg_cost: float | None = None
    margin_shares: int = 0
    margin_avg_cost: float | None = None


def build_position_analysis_prompt_suffix(
    *,
    stock_name: str,
    stock_id: str,
    facts_summary: str,
    holding: HoldingInfo,
    unrealized_pnl_pct: float | None,
    close_price: float | None,
    news_section: str,
    market_report_path: str | None,
    position_facts_summary: str | None = None,
) -> str:
    pnl_note = (
        f"綜合未實現損益（系統試算）：{unrealized_pnl_pct:+.2f}%"
        if unrealized_pnl_pct is not None
        else "綜合未實現損益：無法試算（收盤價缺失）"
    )
    price_note = (
        f"收盤價（CSV）：{close_price}"
        if close_price is not None
        else "收盤價：未取得"
    )
    market_note = (
        f"市場觀察報告（可引用摘要，勿複製全文）：{market_report_path}\n"
        if market_report_path
        else "市場觀察報告：尚無（請直接依 facts 與新聞撰寫市場面摘要）。\n"
    )

    holding_lines = [
        f"總持股：{holding.shares:,} 股",
        f"加權均價：{holding.avg_cost} 元/股",
        price_note,
        pnl_note,
    ]
    if holding.cash_shares > 0 and holding.cash_avg_cost is not None:
        holding_lines.append(
            f"現股腿：{holding.cash_shares:,} 股 × 均價 {holding.cash_avg_cost} 元"
        )
    else:
        holding_lines.append("現股腿：無")
    if holding.margin_shares > 0 and holding.margin_avg_cost is not None:
        holding_lines.append(
            f"融資腿：{holding.margin_shares:,} 股 × 均價 {holding.margin_avg_cost} 元"
        )
    else:
        holding_lines.append("融資腿：無")
    if holding.note.strip():
        holding_lines.append(f"備註：{holding.note.strip()}")
    holding_note = "\n".join(holding_lines) + "\n"

    position_state_block = (
        f"{position_facts_summary}\n\n" if position_facts_summary else ""
    )

    has_cash = holding.cash_shares > 0
    has_margin = holding.margin_shares > 0
    leg_rules: list[str] = []
    if has_cash and has_margin:
        leg_rules.append(
            "- 現股與融資**兩腿都有**：部位現況與操作情境須分述「現股」「融資」，"
            "並寫「綜合結論」對齊系統優先序；融資段須引用系統維持率／距追繳（若有）\n"
        )
    elif has_margin:
        leg_rules.append(
            "- 僅融資腿：操作情境與風險須談追繳／斷頭／維持率或融資減碼；"
            "若系統有維持率／距追繳試算須引用數字；不宜無前提攤平\n"
        )
    else:
        leg_rules.append("- 僅現股腿：勿虛構融資追繳／斷頭情境\n")

    return (
        f"這是「單檔持股部位決策」任務：{stock_name}（{stock_id}）。\n"
        f"系統已完成籌碼資料的**確定性計算**，下方 facts 為市場面唯一事實來源；"
        f"請結合**現股／融資分腿部位資料**與新聞撰寫**部位決策正文**（Markdown），"
        f"市場面方向不可與 facts 矛盾，操作情境須對齊系統試算的各腿狀態與綜合優先序。\n"
        "寫作規格已附在下方，不要再讀 skill 檔，不要加工作摘要或工具操作說明。\n\n"
        f"=== 系統籌碼事實（facts）===\n{facts_summary}\n"
        f"=== facts 結束 ===\n\n"
        f"{market_note}\n"
        f"## 系統提供的部位資料\n\n"
        f"{holding_note}\n"
        f"{position_state_block}"
        f"{news_section}\n\n"
        f"{narrative_rules()}\n\n"
        f"{''.join(leg_rules)}"
    )
