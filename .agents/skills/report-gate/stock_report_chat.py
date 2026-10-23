#!/usr/bin/env python3
"""Grounded Q&A over single stock report artifacts (Phase 2+ grounded chat)."""

from __future__ import annotations

import json
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
UI_PATH = ROOT / "ui"
if str(UI_PATH) not in sys.path:
    sys.path.insert(0, str(UI_PATH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from stock_paths import find_stock_artifact, stock_artifact_dir
except ImportError:
    find_stock_artifact = None  # type: ignore[assignment]
    stock_artifact_dir = None  # type: ignore[assignment]

Intent = Literal[
    "entry_advice",
    "position_advice",
    "inference",
    "glossary",
    "factual_qa",
    "news_qa",
    "fundamental_qa",
    "out_of_scope",
    "reading",
    "open_qa",
]

InferenceTopic = Literal[
    "foreign_continue",
    "institutional_continue",
    "major_continue",
    "generic_continue",
]

FactualSlot = Literal[
    "institutional",
    "major",
    "margin_short",
    "day_trade",
    "borrow_sell",
    "technical",
    "rsi",
    "atr",
    "price_range",
    "regime_divergence",
]

ChatHistoryItem = dict[str, str]

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_TIMEOUT_SEC = 120
MAX_MARKDOWN_CHARS = 3500
MAX_HISTORY_TURNS = 6
MAX_REPLY_CHARS = 2500

ENTRY_PATTERNS = (
    r"進場",
    r"該不該買",
    r"要不要買",
    r"能不能買",
    r"可以買嗎",
    r"值得買嗎",
    r"能不能進",
    r"可以進嗎",
    r"要不要進",
    r"該進嗎",
    r"明天買",
    r"現在買",
    r"追高嗎",
    r"要追嗎",
    r"開倉",
    r"建倉",
    r"空手要買",
    r"做多嗎",
    r"推薦買",
    r"建議買",
)

POSITION_PATTERNS = (
    r"我的部位",
    r"持股怎麼",
    r"部位怎麼",
    r"停損",
    r"停利",
    r"套牢",
    r"被套",
    r"買在",
    r"持有怎麼辦",
    r"要不要賣",
    r"該不該賣",
    r"要賣嗎",
    r"該賣嗎",
    r"要持有",
    r"該持有",
    r"繼續持有",
    r"小損出場",
    r"小虧出場",
    r"認賠",
    r"考慮出場",
    r"要出場",
    r"該出場",
    r"出清",
    r"砍倉",
    r"持有還是",
    r"持有或",
    r"還是.{0,8}出場",
    r"還是.{0,8}賣",
    r"加碼攤平",
    r"成本多少",
    r"均價",
    r"幾張",
    r"減碼",
    r"怎麼處理",
    r"該怎麼",
    r"接下來怎",
    r"如何應對",
    r"怎麼辦",
    r"該怎麼辦",
    r"怎麼應對",
    r"怎麼守",
    r"如何處理",
    r"接下來該",
    r"面對這",
    r"持股該",
    r"部位該",
    r"獲利多少",
    r"損益多少",
    r"損益狀況",
    r"自己獲利",
    r"我的獲利",
    r"我的損益",
    r"未實現損益",
    r"未實現",
    r"賺多少",
    r"賠多少",
    r"報酬率",
    r"浮盈",
    r"浮虧",
    r"目前賺",
    r"現在賺",
    r"目前.*獲利",
    r"現在.*獲利",
    r"看一下.*獲利",
    r"幫忙.*獲利",
)

# If these appear, prefer position/entry advice over factual slots even when
# the message also mentions 外資／主力／融資 etc. (follow-up "所以該怎麼辦？").
ACTION_OVERRIDE_PATTERNS = (
    r"你覺得",
    r"你建議",
    r"建議我",
    r"怎麼辦",
    r"怎麼處理",
    r"該怎麼",
    r"如何處理",
    r"如何應對",
    r"接下來該",
    r"接下來怎",
    r"該不該賣",
    r"要不要賣",
    r"要不要減",
    r"該不該減",
    r"要不要加碼",
    r"該不該加碼",
    r"要持有",
    r"該持有",
    r"小損出場",
    r"小虧出場",
    r"認賠",
    r"怎麼守",
    r"怎麼看這檔",
    r"怎麼看我的",
    r"部位怎麼",
    r"持股怎麼",
    r"獲利多少",
    r"損益",
    r"賺多少",
    r"賠多少",
)

# Predictive / interpretive follow-ups: "會不會繼續賣？" must not dump facts again.
# Checked BEFORE action_override so「你覺得明天會繼續賣」≠ 部位紀律模板.
INFERENCE_PATTERNS = (
    r"你認為",
    r"你覺得會",
    r"你覺得.{0,12}會",
    r"會不會",
    r"會不會繼續",
    r"會繼續",
    r"還會不會",
    r"還會",
    r"是不是會",
    r"接下來會",
    r"明天會",
    r"明天還會",
    r"後續會",
    r"他們會",
    r"會再賣",
    r"會再買",
    r"能延續",
    r"會延續",
    r"賣壓會",
    r"買盤會",
)

# Interpretive questions: "why / what does it mean / where is the risk".
# Checked after news and valuation refusal, but before single-slot fact dumps,
# so「為什麼外資在賣」is not answered with only today's lot count.
READING_PATTERNS = (
    r"為什麼",
    r"為何",
    r"為甚麼",
    r"怎麼會",
    r"代表了?什麼",
    r"代表了?甚麼",
    r"意味",
    r"怎麼解讀",
    r"如何解讀",
    r"怎麼理解",
    r"如何理解",
    r"怎麼讀",
    r"放在一起",
    r"風險在",
    r"什麼風險",
    r"有何風險",
    r"風險是什麼",
    r"主要風險",
    r"總體風險",
    r"矛盾",
    r"怎麼看",
)

READING_POLICY_EXTRA = (
    "本則補充：使用者在問多個已核對事實放在一起怎麼讀。"
    "可以同時引用 2～4 個 facts 欄位，說明它們同向或互相矛盾。"
    "若問題在問回檔、跌深或原因，先講價格路徑："
    "period_return_pct 是 lookback_days 個交易日的收盤漲跌，"
    "dist_to_20d_high_pct 是收盤離近 20 日高點的距離。"
    "close_vs_ma20_pct 只是現在離月線的位置，禁止把它說成這段跌幅。"
    "同一段籌碼用 foreign_cum_lots、major_cum_lots；當天淨額只是這段的最後一天。"
    "禁止編造 facts 沒有的數字，禁止改寫損益金額，禁止下單、目標價、保證漲跌。"
    "買賣超方向必須跟 facts 的正負號一致；使用者把買說成賣（或相反）時，第一句先糾正。"
    "使用者口述張數和報告差一個數量級時，第一句先對數字。"
    "不要只回單一欄位。"
)

GLOSSARY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "borrow_vs_short": (r"借券", r"融券", r"借券賣出", r"借券跟融券", r"借券與融券"),
    "day_trade": (r"當沖比", r"當沖佔比", r"當沖率", r"當沖熱度"),
    "major": (r"主力進出", r"主力買賣超", r"主力大戶", r"什麼是主力"),
    "institutional": (r"三大法人", r"法人共識", r"法人對作", r"外資投信對作"),
    "margin_regime": (r"券資比", r"融資動能", r"資券互抵", r"資增價跌", r"資減價漲"),
    "ma_alignment": (r"均線排列", r"多頭排列", r"空頭排列", r"均線糾結", r"月線斜率"),
    "rsi": (r"rsi", r"相對強弱指標", r"超買區", r"超賣區"),
    "atr": (r"atr", r"真實區間", r"波動度", r"震幅指標"),
    "divergence": (r"背離", r"量價背離", r"籌碼背離", r"指標背離"),
    "chip_regime": (r"籌碼型態", r"籌碼偏多", r"籌碼偏空", r"吸籌", r"出貨"),
}

FACTUAL_SLOT_PATTERNS: tuple[tuple[FactualSlot, tuple[str, ...]], ...] = (
    (
        "institutional",
        (r"外資", r"投信", r"自營商", r"三大法人", r"法人買賣超", r"法人賣超", r"法人買超"),
    ),
    (
        "major",
        (r"主力進出", r"主力買賣超", r"主力買超", r"主力賣超", r"主力動向", r"主力有沒有進場", r"主力累計", r"主力"),
    ),
    (
        "margin_short",
        (r"融資增減", r"融資餘額", r"融券餘額", r"融券增減", r"券資比", r"融資動能", r"融資今日"),
    ),
    (
        "day_trade",
        (r"當沖佔比", r"當沖比", r"當沖熱度", r"當沖量", r"今天當沖"),
    ),
    (
        "borrow_sell",
        (r"借券賣出", r"借券壓力", r"借券張數", r"借券今日"),
    ),
    (
        "technical",
        (r"均線", r"ma5", r"ma10", r"ma20", r"月線", r"斜率", r"偏離", r"黃金交叉", r"死亡交叉"),
    ),
    (
        "rsi",
        (r"rsi數值", r"rsi多少", r"現在超買", r"現在超賣", r"\brsi\b"),
    ),
    (
        "atr",
        (r"atr數值", r"atr多少", r"波動度", r"震幅", r"\batr\b"),
    ),
    (
        "price_range",
        (r"20日高", r"20日低", r"近20日", r"最高價", r"最低價", r"突破高點", r"跌破低點", r"支撐壓力"),
    ),
    (
        "regime_divergence",
        (r"籌碼型態", r"背離", r"籌碼偏多", r"籌碼偏空", r"多空評估", r"量價背離", r"籌碼面總評"),
    ),
)

# Topics without structured facts — answer from report news first, else say not found.
NEWS_GROUNDED_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "月營收／營收公布",
        (r"營收", r"月營收", r"年增率", r"yoy營收", r"營收年增"),
    ),
    (
        "財報／盈餘數字",
        (r"財報", r"季報", r"年報", r"\beps\b", r"每股盈餘", r"稅後純益", r"淨利", r"毛利率"),
    ),
    (
        "法說會／展望／訂單",
        (r"法說", r"展望", r"指引", r"guidance", r"訂單", r"能見度", r"客戶拉貨"),
    ),
    (
        "近期新聞／公告",
        (r"新聞", r"消息", r"公告", r"利多", r"利空", r"有什麼消息", r"最近發生"),
    ),
)

# Valuation and revenue YoY are fetched on demand (FinMind), not refused.
# Checked before news so「月營收年增多少」is a number, while「營收出來了嗎」stays news.
FUNDAMENTAL_FIELD_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("per", (r"本益比", r"\bper\b")),
    ("pbr", (r"\bpbr\b", r"股價淨值比", r"淨值比")),
    ("dividend_yield", (r"殖利率", r"現金股利", r"配息")),
    ("revenue_yoy", (r"營收年增", r"月營收年增", r"yoy營收", r"revenue\s*yoy")),
)

# Reserved for topics that are neither in the report nor fetched live.
OUT_OF_SCOPE_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = ()

DISCLAIMER = "僅供參考，不構成投資建議。"

SYSTEM_POLICY = """你是懂台股籌碼的朋友，在幫忙看一份已完成的個股報告。
說人話，像微信／Line 裡回覆，不要像系統報表。

硬規則：
1. 只能用使用者訊息裡的事實數字；沒有就說報告沒寫，禁止編造。
2. 先用 1～2 句直接回答問題，再最多補 1～2 個關鍵數字當依據。
3. 禁止用【標題】、禁止「情境一／二／三」、禁止把所有欄位條列倒出來。
4. 禁止給買賣下單指令或保證獲利（不要說一定漲、明天買、目標價）。
5. 營收／財報／消息類問題：先看報告新聞摘要；有就引用標題，沒有就明說這批新聞沒提到，不要改聊外資主力總覽。
6. 繁體中文，約 80～160 字；結尾加：僅供參考，不構成投資建議。
7. 禁止把內部代號照念給使用者（bearish、bullish、mixed、bullish_stack、overbought）。改說一起偏賣、一起偏買、方向不一致、多頭排列、偏熱。
""".strip()


def load_stock_artifacts(
    stock_id: str,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Find and load stock artifacts from disk."""
    sid = str(stock_id).strip()
    if not sid:
        raise ValueError("stock_id 不可為空")

    facts_path = (
        find_stock_artifact(sid, f"tw_stock_{sid}.facts.json", trade_date=trade_date)
        if find_stock_artifact
        else None
    )
    summary_path = (
        find_stock_artifact(sid, f"tw_stock_{sid}.summary.json", trade_date=trade_date)
        if find_stock_artifact
        else None
    )
    md_path = (
        find_stock_artifact(sid, f"tw_stock_{sid}.md", trade_date=trade_date)
        if find_stock_artifact
        else None
    )
    pos_facts_path = (
        find_stock_artifact(sid, f"tw_stock_{sid}.position.facts.json", trade_date=trade_date)
        if find_stock_artifact
        else None
    )
    pos_md_path = (
        find_stock_artifact(sid, f"tw_stock_{sid}_position.md", trade_date=trade_date)
        if find_stock_artifact
        else None
    )

    facts = (
        json.loads(facts_path.read_text(encoding="utf-8"))
        if facts_path and facts_path.exists()
        else None
    )
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path and summary_path.exists()
        else None
    )
    markdown = md_path.read_text(encoding="utf-8") if md_path and md_path.exists() else None
    pos_facts = (
        json.loads(pos_facts_path.read_text(encoding="utf-8"))
        if pos_facts_path and pos_facts_path.exists()
        else None
    )
    pos_md = (
        pos_md_path.read_text(encoding="utf-8") if pos_md_path and pos_md_path.exists() else None
    )

    return {
        "facts": facts,
        "summary": summary,
        "markdown": markdown,
        "position_facts": pos_facts,
        "position_markdown": pos_md,
    }


def _has_action_override(msg: str) -> bool:
    for pat in ACTION_OVERRIDE_PATTERNS:
        if re.search(pat, msg):
            return True
    return False


def _has_inference_pattern(msg: str) -> bool:
    for pat in INFERENCE_PATTERNS:
        if re.search(pat, msg):
            return True
    return False


def _has_reading_pattern(msg: str) -> bool:
    for pat in READING_PATTERNS:
        if re.search(pat, msg):
            return True
    return False


def match_inference_topic(message: str) -> InferenceTopic:
    msg = (message or "").strip().lower()
    if re.search(r"外資|foreign", msg):
        return "foreign_continue"
    if re.search(r"主力", msg):
        return "major_continue"
    if re.search(r"投信|自營|三大法人|法人", msg):
        return "institutional_continue"
    return "generic_continue"


# Concrete subjects a follow-up can inherit. Order is not priority.
_FOLLOWUP_TOPICS: tuple[str, ...] = (
    "三大法人",
    "外資",
    "投信",
    "自營",
    "主力",
    "融資",
    "融券",
    "券資",
    "當沖",
    "借券",
    "月線",
    "均線",
    "背離",
    "本益比",
    "殖利率",
    "淨值比",
    "股價淨值",
    "營收",
    "rsi",
    "atr",
)

_FOLLOWUP_CUES: tuple[tuple[str, str], ...] = (
    ("為什麼", r"為什麼|為何|為甚麼"),
    ("代表什麼", r"代表了?什麼|代表了?甚麼|意味"),
    ("矛盾", r"矛盾"),
    ("風險在", r"風險"),
    ("會不會", r"會不會|會繼續|還會"),
    ("是什麼", r"什麼是|是什麼|什麼意思"),
    ("多少", r"多少|幾張|數值"),
)


def _followup_topics(message: str) -> list[str]:
    msg = (message or "").lower()
    found: list[str] = []
    for term in _FOLLOWUP_TOPICS:
        if term.lower() in msg and term not in found:
            found.append(term)
    return found


def _followup_cue(message: str) -> str:
    msg = (message or "").lower()
    for label, pattern in _FOLLOWUP_CUES:
        if re.search(pattern, msg):
            return label
    return ""


_CONTINUATION_MARK = re.compile(
    r"^(那|所以|然後|這樣|這|可是|但是|不過|也就是|換句|意思)|"
    r"這個|那個|這樣|那樣|剛才|上面|前述|上一句"
)
# A short reaction with no subject of its own. Wording inside the group can vary;
# anything that is not a new question still continues the previous turn.
_REACTION_MARK = re.compile(
    r"意思|聽不|看不|不懂|不太懂|沒懂|再說|解釋|說明一下|清楚|怎樣|如何|嚴重|要緊|"
    r"正常|合理|奇怪|異常|好壞|算好|算壞|利多|利空|什麼情況|怎麼回事|然後呢|所以呢"
)


def _prior_user_texts(history: list[ChatHistoryItem] | None) -> list[str]:
    return [
        (item.get("content") or "").strip()
        for item in (history or [])
        if item.get("role") == "user" and (item.get("content") or "").strip()
    ]


def _prior_subject(history: list[ChatHistoryItem] | None) -> tuple[list[str], str]:
    topics: list[str] = []
    cue = ""
    for text in reversed(_prior_user_texts(history)[-3:]):
        if not topics:
            topics = _followup_topics(text)
        if not cue:
            cue = _followup_cue(text)
        if topics and cue:
            break
    return topics, cue


def _stands_alone(message: str) -> bool:
    """A question that already names its subject and what it wants."""
    msg = (message or "").strip()
    if not msg:
        return True
    topics = _followup_topics(msg)
    cue = _followup_cue(msg)
    refers_back = re.search(r"這個|那個|這樣|那樣", msg) is not None
    if _has_inference_pattern(msg) and (topics or len(msg) > 12) and not refers_back:
        return True
    asks_action = _has_action_override(msg) or any(
        re.search(pat, msg) for pat in (*ENTRY_PATTERNS, *POSITION_PATTERNS)
    )
    if asks_action and (topics or len(msg) > 18) and not refers_back:
        return True
    if topics and cue and not refers_back:
        return True
    if len(msg) > 36 and (topics or cue) and not refers_back:
        return True
    return False


def _is_continuation(message: str) -> bool:
    """Points back at the previous turn, without listing acceptable phrasings."""
    msg = (message or "").strip()
    if not msg or _stands_alone(msg):
        return False
    if _CONTINUATION_MARK.search(msg) and len(msg) <= 48:
        return True
    if len(msg) <= 20 and not _followup_topics(msg) and _REACTION_MARK.search(msg):
        return True
    if (
        len(msg) <= 8
        and not _followup_topics(msg)
        and re.search(r"^(那|所以|然後)|[？?呢嗎]$", msg)
    ):
        return True
    return False


def _explicit_other_act(message: str) -> str:
    """Number, forecast, or what-to-do. These keep their own intent."""
    msg = message or ""
    if _followup_cue(msg) == "多少":
        return "fact"
    if _has_inference_pattern(msg) or _followup_cue(msg) == "會不會":
        return "inference"
    if _has_action_override(msg) or any(re.search(pat, msg) for pat in (*ENTRY_PATTERNS, *POSITION_PATTERNS)):
        return "action"
    return ""


def resolve_followup(message: str, history: list[ChatHistoryItem] | None) -> dict[str, Any]:
    """Continue the previous subject. Deepen unless the user asked something new.

    A short follow-up inherits the last subject. If it does not ask for a number,
    a forecast, a holding decision, or a different topic, the next reply explains
    that subject instead of repeating the fact paragraph. Exact wording is not required.
    """
    msg = (message or "").strip()
    if not msg or not _is_continuation(msg) or not _prior_user_texts(history):
        return {"message": msg, "deepen": False}
    topics, cue = _prior_subject(history)
    own_topics = _followup_topics(msg)
    new_topics = [term for term in own_topics if term not in topics]
    inherited = [term for term in topics[:3] if term not in own_topics]

    if new_topics:
        extra: list[str] = []
        if not _followup_cue(msg) and cue and cue not in {"代表什麼", "矛盾"}:
            extra.append(cue)
        expanded = f"{msg} {' '.join(extra)}".strip() if extra else msg
        return {"message": expanded, "deepen": False}

    other = _explicit_other_act(msg)
    if other:
        expanded = f"{msg} {' '.join(inherited)}".strip() if inherited else msg
        return {"message": expanded, "deepen": False}

    expanded = f"{msg} {' '.join(inherited)}".strip() if inherited else msg
    return {"message": expanded, "deepen": True}


def expand_followup(message: str, history: list[ChatHistoryItem] | None) -> str:
    """Attach the previous turn's subject or question type before intent classification."""
    return str(resolve_followup(message, history)["message"])


def classify_intent(message: str) -> Intent:
    msg = (message or "").strip().lower()
    for pat in ENTRY_PATTERNS:
        if re.search(pat, msg):
            return "entry_advice"
    for pat in POSITION_PATTERNS:
        if re.search(pat, msg):
            return "position_advice"

    # Prediction / "will they continue?" beats bare「你覺得」action override.
    # e.g.「你覺得他們明天會繼續賣嗎」→ inference，不是持股紀律模板。
    if _has_inference_pattern(msg):
        return "inference"

    # Follow-ups like「外資賣了…接下來該怎麼處理」must not fall into facts slot.
    if _has_action_override(msg):
        return "position_advice"

    # Glossary check: only if asking definition or comparison
    glossary_trigger = (
        "是什麼" in msg
        or "什麼是" in msg
        or "差在哪" in msg
        or "差別" in msg
        or "定義" in msg
        or "意思" in msg
        or "何謂" in msg
        or "說明" in msg
        or "解釋" in msg
    )
    if glossary_trigger:
        for term_key, patterns in GLOSSARY_KEYWORDS.items():
            for pat in patterns:
                if re.search(pat, msg):
                    return "glossary"

    # Valuation / revenue YoY numbers are fetched live. News headlines stay news.
    # Reading then beats a single fact slot, so「為什麼外資在賣」is not only lots.
    if match_fundamental_fields(msg):
        return "fundamental_qa"

    if match_news_grounded_topic(msg):
        return "news_qa"

    if match_out_of_scope_topic(msg):
        return "out_of_scope"

    if _has_reading_pattern(msg):
        return "reading"

    for _, patterns in FACTUAL_SLOT_PATTERNS:
        for pat in patterns:
            if re.search(pat, msg):
                return "factual_qa"

    return "open_qa"


def match_fundamental_fields(message: str) -> tuple[str, ...]:
    """Fields to fetch live: PER / PBR / yield, or month-revenue YoY."""
    msg = (message or "").strip().lower()
    if not msg:
        return ()
    found: list[str] = []
    for field, patterns in FUNDAMENTAL_FIELD_PATTERNS:
        if any(re.search(pat, msg) for pat in patterns):
            found.append(field)
    if re.search(r"估值", msg) and not found:
        return ("per", "pbr", "dividend_yield")
    return tuple(found)


def lookup_stock_fundamentals(stock_id: str, trade_date: str) -> Any:
    """One stock, report trade date. Cache hit skips FinMind; miss fetches live."""
    from fundamental_signals import load_or_fetch_fundamentals

    found = load_or_fetch_fundamentals(
        [str(stock_id).strip()],
        trade_date,
        sleep_sec=0,
    )
    return found.get(str(stock_id).strip())


def match_news_grounded_topic(message: str) -> str | None:
    """Topics answered from report news (Yahoo headlines captured at gate time)."""
    msg = (message or "").strip().lower()
    if not msg:
        return None
    for topic, patterns in NEWS_GROUNDED_TOPICS:
        for pat in patterns:
            if re.search(pat, msg):
                return topic
    return None


def match_out_of_scope_topic(message: str) -> str | None:
    """Return a human topic label if the question is outside chip/news report scope."""
    msg = (message or "").strip().lower()
    if not msg:
        return None
    for topic, patterns in OUT_OF_SCOPE_TOPICS:
        for pat in patterns:
            if re.search(pat, msg):
                return topic
    return None


def extract_report_news(
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
) -> list[dict[str, str]]:
    """Collect news items from summary.market.news and markdown news table."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(title: str, *, date: str = "", category: str = "", summary_text: str = "") -> None:
        t = (title or "").strip()
        if not t or t in seen:
            return
        seen.add(t)
        items.append(
            {
                "date": (date or "").strip(),
                "title": t,
                "category": (category or "").strip(),
                "summary": (summary_text or "").strip(),
            }
        )

    if isinstance(summary, dict):
        market = summary.get("market") if isinstance(summary.get("market"), dict) else {}
        raw_list = None
        if isinstance(market, dict) and isinstance(market.get("news"), list):
            raw_list = market.get("news")
        elif isinstance(summary.get("news"), list):
            raw_list = summary.get("news")
        for raw in raw_list or []:
            if not isinstance(raw, dict):
                continue
            _add(
                str(raw.get("title") or raw.get("headline") or ""),
                date=str(raw.get("date") or ""),
                category=str(raw.get("category") or ""),
                summary_text=str(raw.get("summary") or raw.get("snippet") or ""),
            )

    if markdown:
        section = re.search(
            r"##\s*三[、.．]?\s*近期新聞與事件([\s\S]*?)(?:\n##\s|\Z)",
            markdown,
        )
        block = section.group(1) if section else markdown
        for line in block.splitlines():
            # Markdown table row: | date | title | category | summary |
            if not line.strip().startswith("|"):
                continue
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) < 2:
                continue
            if cols[0] in {"日期", "----", "---"} or set(cols[0]) <= {"-", ":"}:
                continue
            if re.fullmatch(r":?-{3,}:?", cols[0] or ""):
                continue
            date = cols[0] if re.match(r"\d{4}-\d{2}-\d{2}", cols[0] or "") else ""
            title = cols[1] if date else cols[0]
            category = cols[2] if date and len(cols) > 2 else (cols[1] if not date and len(cols) > 1 else "")
            summary_text = cols[3] if date and len(cols) > 3 else ""
            if title in {"標題", "Title"}:
                continue
            _add(title, date=date, category=category if category not in {"分類", "標題"} else "", summary_text=summary_text)

    return items


def _news_query_keywords(message: str) -> list[str]:
    """Keywords used to match news titles/summaries against the user question."""
    msg = (message or "").strip()
    keys: list[str] = []
    topic = match_news_grounded_topic(msg)
    if topic == "月營收／營收公布" or re.search(r"營收", msg):
        keys.extend(["營收", "月營收", "營收年增", "YoY", "年增"])
    if re.search(r"財報|季報|年報|eps|每股盈餘|淨利|毛利", msg, re.I):
        keys.extend(["財報", "季報", "年報", "EPS", "盈餘", "純益", "淨利", "毛利"])
    if re.search(r"法說|展望|指引|guidance|訂單|能見度|拉貨", msg, re.I):
        keys.extend(["法說", "展望", "指引", "訂單", "能見度", "拉貨"])
    if re.search(r"利多", msg):
        keys.append("利多")
    if re.search(r"利空", msg):
        keys.append("利空")
    # Month mentions: 九月 / 9月 / 09月
    for m in re.finditer(r"([0-9一二三四五六七八九十]+)\s*月", msg):
        keys.append(m.group(0))
        raw = m.group(1)
        cn_map = {
            "一": "1",
            "二": "2",
            "三": "3",
            "四": "4",
            "五": "5",
            "六": "6",
            "七": "7",
            "八": "8",
            "九": "9",
            "十": "10",
            "十一": "11",
            "十二": "12",
        }
        if raw in cn_map:
            keys.append(f"{cn_map[raw]}月")
            keys.append(f"{int(cn_map[raw]):02d}月")
        elif raw.isdigit():
            keys.append(f"{int(raw)}月")
            keys.append(f"{int(raw):02d}月")
    # Dedupe preserve order
    out: list[str] = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


def filter_news_for_query(
    news: list[dict[str, str]],
    message: str,
) -> list[dict[str, str]]:
    """Return news items relevant to the question; empty if none match."""
    if not news:
        return []
    msg = message or ""
    keys = _news_query_keywords(msg)

    # Broad "what's the news" → return top items (optionally filtered by 利多/利空).
    if re.search(r"新聞|消息|最近發生|有什麼利多|有什麼利空", msg):
        if "利多" in keys and not any(k in {"營收", "財報", "法說", "訂單"} for k in keys):
            hit = [
                n
                for n in news
                if "利多" in (n.get("category") or "") or "利多" in (n.get("title") or "")
            ]
            return hit[:4] or news[:4]
        if "利空" in keys and not any(k in {"營收", "財報", "法說", "訂單"} for k in keys):
            hit = [
                n
                for n in news
                if "利空" in (n.get("category") or "") or "利空" in (n.get("title") or "")
            ]
            return hit[:4] or news[:4]
        if not keys or set(keys) <= {"利多", "利空"}:
            return news[:4]

    if not keys:
        return []

    scored: list[tuple[int, dict[str, str]]] = []
    for item in news:
        blob = f"{item.get('title', '')} {item.get('summary', '')} {item.get('category', '')}"
        score = sum(1 for k in keys if k.lower() in blob.lower())
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:4]]


def build_news_grounded_reply(
    *,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
    message: str | None = None,
    topic: str | None = None,
) -> tuple[str, str]:
    """Answer from report news. Returns (reply, source_tag)."""
    label = _stock_label(facts)
    msg = (message or "").strip()
    topic_label = topic or match_news_grounded_topic(msg) or "相關消息"
    news = extract_report_news(summary, markdown)
    hits = filter_news_for_query(news, msg)

    if hits:
        lines = []
        for item in hits[:3]:
            title = item.get("title") or ""
            cat = item.get("category") or ""
            date = item.get("date") or ""
            prefix = "、".join(p for p in (date, cat) if p)
            lines.append(f"{'（' + prefix + '）' if prefix else ''}{title}")
        joined = "；".join(lines)
        return (
            _end(
                f"就 {label} 這份報告擷取到的新聞來看：{joined}。"
                f"以上僅反映產報當下抓到的標題，不是券商即時快訊。"
            ),
            "news",
        )

    if news:
        samples = []
        for item in news[:2]:
            t = item.get("title") or ""
            if t:
                samples.append(t)
        sample_bit = f"目前新聞比較像在講：{'；'.join(samples)}。" if samples else ""
        month_bit = ""
        m = re.search(r"([0-9一二三四五六七八九十]+)\s*月", msg)
        if m and re.search(r"營收", msg):
            month_bit = f"你問的「{m.group(0)}營收有沒有出來」，"
        return (
            _end(
                f"{month_bit}在 {label} 這份報告的新聞摘要裡，我沒看到直接提到{topic_label}的標題。"
                f"{sample_bit}"
                f"所以沒辦法用這批新聞斷定有沒有公布；若要對籌碼或持股損益，我可以用報告數字跟你聊。"
            ),
            "news",
        )

    return (
        _end(
            f"這份 {label} 報告這次沒附上可用的新聞摘要，也沒有結構化的{topic_label}欄位，"
            f"所以我沒辦法從報告判斷。"
            f"若要問外資／主力／均線／持股損益，我可以用籌碼數字跟你對。"
        ),
        "out_of_scope",
    )


_FUNDAMENTAL_FIELD_LABELS = {
    "per": "本益比",
    "pbr": "股價淨值比",
    "dividend_yield": "殖利率",
    "revenue_yoy": "月營收年增",
}


def _fundamental_attr(row: Any, field: str) -> Any:
    key = "revenue_yoy_pct" if field == "revenue_yoy" else field
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _fmt_metric(val: float) -> str:
    text = f"{float(val):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def build_fundamental_reply(
    *,
    facts: dict[str, Any] | None,
    message: str,
    trade_date: str | None,
    row: Any = None,
) -> str:
    """Deterministic valuation / revenue-YoY answer. Numbers are not paraphrased."""
    label = _stock_label(facts)
    fields = match_fundamental_fields(message)
    asked = "、".join(_FUNDAMENTAL_FIELD_LABELS[f] for f in fields) or "本益比／殖利率"
    if not trade_date:
        return _end(
            f"這份 {label} 報告沒有基準日，所以我不查{asked}。"
        )

    def _missing() -> str:
        return _end(
            f"這次沒查到 {label} 在報告基準日 {trade_date} 的{asked}（FinMind）。"
            f"不是報告漏了欄位，是這次查詢沒有數字。"
        )

    if row is None:
        return _missing()

    msg = (message or "").lower()
    parts: list[str] = []
    missing: list[str] = []
    for field in fields:
        raw = _fundamental_attr(row, field)
        name = _FUNDAMENTAL_FIELD_LABELS[field]
        try:
            value = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            value = None
        if value is None:
            missing.append(name)
            continue
        shown = _fmt_metric(value)
        if field == "per":
            extra = "（負值，獲利為負）" if value <= 0 else ""
            parts.append(
                f"本益比約 {shown}{extra}（FinMind TaiwanStockPER）"
            )
        elif field == "pbr":
            parts.append(f"股價淨值比約 {shown}（FinMind TaiwanStockPER）")
        elif field == "dividend_yield":
            note = ""
            if re.search(r"現金股利|配息", msg):
                note = "，這是現金殖利率，不是每股配息金額"
            parts.append(f"殖利率約 {shown}%{note}（FinMind TaiwanStockPER）")
        else:
            sign = "+" if value > 0 else ""
            parts.append(
                f"月營收年增約 {sign}{shown}%（FinMind 月營收，不是新聞標題）"
            )

    if not parts:
        return _missing()

    body = f"{label} 在報告基準日 {trade_date} 的" + "；".join(parts) + "。"
    if missing:
        body += f"另外這次沒查到{'、'.join(missing)}。"
    return _end(body)


def build_out_of_scope_reply(
    topic: str,
    facts: dict[str, Any] | None,
    message: str | None = None,
) -> str:
    label = _stock_label(facts)
    return _end(
        f"這份 {label} 深度報告沒有收錄{topic}的結構化數字，"
        f"新聞摘要通常也不會帶這類估值欄位，所以我沒辦法從報告直接回答。"
        f"若要問外資／主力／均線／持股損益，或報告裡有的新聞標題，我可以再幫你對。"
    )


def match_glossary_term(message: str) -> str | None:
    msg = (message or "").strip().lower()
    for term_key, patterns in GLOSSARY_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, msg):
                return term_key
    return None


def match_factual_slot(message: str) -> FactualSlot | None:
    msg = (message or "").strip().lower()
    for slot, patterns in FACTUAL_SLOT_PATTERNS:
        for pat in patterns:
            if re.search(pat, msg):
                return slot
    return None


def _fmt_lots(val: Any) -> str:
    if val is None:
        return "—"
    try:
        iv = int(val)
        return f"{iv:+,d} 張" if iv != 0 else "0 張"
    except (TypeError, ValueError):
        return f"{val} 張"


def _fmt_pct(val: Any) -> str:
    if val is None:
        return "—"
    try:
        fv = float(val)
        return f"{fv:+.2f}%"
    except (TypeError, ValueError):
        return f"{val}%"


# Internal fact codes stay English in JSON. Chat replies use these spoken forms.
_CONSENSUS_SPEAK = {
    "bullish": "一起偏買",
    "bearish": "一起偏賣",
    "mixed": "方向不一致",
    "neutral": "買賣差不多",
    "unknown": "方向還看不出來",
    "strong_buy": "明顯偏買",
    "strong_sell": "明顯偏賣",
}
_CHIP_SPEAK = {
    "accumulation": "偏多，比較像有人在買",
    "distribution": "偏空，賣壓比較明顯",
    "mixed": "多空還沒站同一邊",
    "unknown": "還看不出來",
}
_ALIGN_SPEAK = {
    "bullish": "短線和月線都偏多",
    "bearish": "短線和月線都偏空",
    "short_rebound": "短線有反彈，月線還偏弱",
    "short_pullback": "短線在回檔，月線還偏強",
    "neutral": "方向還不明",
    "unknown": "還看不出來",
}
_STACK_SPEAK = {
    "bullish_stack": "多頭排列",
    "bearish_stack": "空頭排列",
    "mixed": "糾在一起，多空還沒排開",
    "unknown": "還看不出來",
}
_SLOPE_SPEAK = {
    "rising": "往上",
    "falling": "往下",
    "flat": "幾乎走平",
    "unknown": "還看不出來",
}
_RSI_SPEAK = {
    "overbought": "偏熱，短線要小心回檔",
    "oversold": "偏冷，比較像跌深，但不代表隔天一定彈",
    "neutral": "還在中間，沒有明顯過熱或過冷",
    "unknown": "還看不出來",
}
_DIVERGENCE_SPEAK = {
    "price_up_foreign_sell": "股價漲、外資卻在賣",
    "price_down_foreign_buy": "股價跌、外資卻在買",
    "price_down_margin_up": "股價跌、融資卻在增加",
    "price_up_margin_up": "股價漲、融資也在增加",
}
_TREND_SPEAK = {
    "up": "上漲",
    "down": "下跌",
    "flat": "幾乎走平",
    "unknown": "還看不出來",
}
_READER_FIELDS = {
    "institutional_consensus": _CONSENSUS_SPEAK,
    "chip_regime": _CHIP_SPEAK,
    "ma_alignment": _ALIGN_SPEAK,
    "ma_stack": _STACK_SPEAK,
    "ma20_slope": _SLOPE_SPEAK,
    "rsi_zone": _RSI_SPEAK,
    "price_trend": _TREND_SPEAK,
}


def _speak(value: Any, mapping: dict[str, str], *, empty: str = "還看不出來") -> str:
    """Turn an internal code into spoken Chinese. Never echo an unknown English token."""
    if value is None:
        return empty
    text = str(value).strip()
    if not text or text in {"—", "-"}:
        return empty
    if text in mapping:
        return mapping[text]
    if re.search(r"[\u4e00-\u9fff]", text):
        return text
    return empty


def _consensus_phrase(value: Any) -> str:
    return f"三大法人{_speak(value, _CONSENSUS_SPEAK, empty='方向還看不出來')}"


def _speak_divergences(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return ""
    return "、".join(_speak(item, _DIVERGENCE_SPEAK, empty="有一項背離") for item in values[:3])


def _facts_for_reader(facts: dict[str, Any]) -> dict[str, Any]:
    """Copy compact facts with spoken labels so a model cannot recite internal codes."""
    spoken = dict(facts)
    for key, mapping in _READER_FIELDS.items():
        if key in spoken:
            spoken[key] = _speak(spoken.get(key), mapping)
    if isinstance(spoken.get("divergences"), list):
        spoken["divergences"] = [
            _speak(item, _DIVERGENCE_SPEAK, empty="有一項背離") for item in spoken["divergences"]
        ]
    return spoken


def _asks_price_direction(message: str) -> bool:
    return (
        re.search(
            r"下跌|反彈|上漲|續跌|續漲|會跌|會漲|跌還是|還是彈|會不會跌|會不會漲|會不會彈",
            message or "",
        )
        is not None
    )


def _flow_phrase(val: Any, *, unit: str = "張") -> str:
    """Natural buy/sell phrase, e.g. 賣超約 21.2 萬張."""
    if val is None:
        return "資料暫缺"
    try:
        iv = int(val)
    except (TypeError, ValueError):
        return str(val)
    if iv == 0:
        return "幾乎沒動"
    direction = "買超" if iv > 0 else "賣超"
    abs_v = abs(iv)
    if abs_v >= 10_000:
        return f"{direction}約 {abs_v / 10_000:.1f} 萬{unit}"
    return f"{direction} {abs_v:,} {unit}"


def _stock_label(facts: dict[str, Any] | None) -> str:
    f = facts or {}
    sid = f.get("stock_id") or "這檔"
    name = f.get("stock_name") or ""
    return f"{name}" if name else str(sid)


def _end(text: str) -> str:
    body = text.strip()
    if DISCLAIMER in body:
        return body
    return f"{body}\n\n{DISCLAIMER}"


def build_factual_slot_reply(
    slot: FactualSlot,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
) -> str:
    """Short conversational answer grounded in stock facts."""
    f = facts or {}
    label = _stock_label(f)
    date = f.get("trade_date") or "最新交易日"

    if slot == "institutional":
        foreign_lots = f.get("foreign_net_lots")
        trust_lots = f.get("trust_net_lots")
        dealer_lots = f.get("dealer_net_lots")
        streak_dir = f.get("foreign_streak_dir")
        streak_days = int(f.get("foreign_streak_days") or 0)
        cum_lots = f.get("foreign_cum_lots")
        consensus = f.get("institutional_consensus") or "方向還不明"

        streak = ""
        if streak_dir and streak_days > 1:
            streak = f"，而且已經連續 {streak_days} 日{'買' if streak_dir == 1 else '賣'}"

        return _end(
            f"{label}（{date}）外資今天{_flow_phrase(foreign_lots)}{streak}。"
            f"投信{_flow_phrase(trust_lots)}，自營{_flow_phrase(dealer_lots)}；"
            f"{_consensus_phrase(consensus)}。"
            f"{'區間累計外資也還在賣壓。' if isinstance(cum_lots, (int, float)) and cum_lots < 0 else ''}"
            f"{'區間累計外資偏買。' if isinstance(cum_lots, (int, float)) and cum_lots > 0 else ''}"
        )

    if slot == "major":
        major_net = f.get("major_net_lots")
        major_cum = f.get("major_cum_lots")
        major_avail = f.get("major_available", True)
        major_foreign_div = f.get("major_foreign_divergence", False)
        if not major_avail or major_net is None:
            return _end(f"{label}這檔的主力進出資料目前看不到，沒辦法跟你對主力張數。")
        rel = "不過它跟外資在對作，方向不太一致" if major_foreign_div else "跟外資方向大致同邊"
        return _end(
            f"{label}今天主力{_flow_phrase(major_net)}，{rel}。"
            f"{'區間累計主力是 ' + _fmt_lots(major_cum) + '。' if major_cum is not None else ''}"
        )

    if slot == "margin_short":
        m_today = f.get("margin_today_delta_lots")
        ratio = f.get("margin_short_ratio_pct")
        return _end(
            f"{label}今天融資{_flow_phrase(m_today)}。"
            f"{f'券資比大概 {ratio}%。' if ratio is not None else ''}"
            f"看融資券時，重點是有沒有「資增價跌」這類散戶越攤越套的味道，而不是單看一個數字。"
        )

    if slot == "day_trade":
        d_today = f.get("day_trade_ratio_pct")
        return _end(
            f"{label}今天當沖比大約 {d_today}%。"
            f"當沖偏高時，盤中比較容易洗上洗下，隔日也較常有短線賣壓；數字本身不是多空訊號。"
        )

    if slot == "borrow_sell":
        borrow_today = f.get("borrow_sell_today_lots")
        return _end(
            f"{label}今天借券賣出 {_fmt_lots(borrow_today).lstrip('+')}。"
            f"借券比較常是法人避險／放空管道，跟散戶融券不是同一件事；單日數字要搭配外資動向一起看。"
        )

    if slot == "technical":
        stack = _speak(f.get("ma_stack"), _STACK_SPEAK)
        alignment = _speak(f.get("ma_alignment"), _ALIGN_SPEAK)
        slope_label = _speak(f.get("ma20_slope"), _SLOPE_SPEAK)
        return _end(
            f"{label}的均線現在是{stack}，短線相對月線是{alignment}，月線斜率{slope_label}。"
            f"收盤相對 5 日線大概 {_fmt_pct(f.get('close_vs_ma5_pct'))}，相對月線大概 {_fmt_pct(f.get('close_vs_ma20_pct'))}。"
        )

    if slot == "rsi":
        rsi = f.get("rsi_14")
        zone = f.get("rsi_zone") or "neutral"
        rsi_str = f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "—"
        return _end(f"{label}的強弱指標大概 {rsi_str}，{_speak(zone, _RSI_SPEAK)}。")

    if slot == "atr":
        atr = f.get("atr_14")
        atr_pct = f.get("atr_pct")
        atr_str = f"{atr:.2f}" if isinstance(atr, (int, float)) else "—"
        pct_str = f"{atr_pct:.2f}%" if isinstance(atr_pct, (int, float)) else "—"
        return _end(
            f"{label}近期一天大概會晃 {atr_str} 元上下（約 {pct_str}）。"
            f"設停損時最好留一點空間，不然很容易被盤中雜訊掃出去。"
        )

    if slot == "price_range":
        h20 = f.get("high_20d")
        l20 = f.get("low_20d")
        return _end(
            f"{label}近 20 日大致落在 {l20}～{h20} 這帶。"
            f"上面可以先盯前高附近壓力，下面則看有沒有守住前低；"
            f"有沒有真正突破／破底，要比單看一天漲跌有意義。"
        )

    if slot == "regime_divergence":
        chip_regime = f.get("chip_regime") or "mixed"
        divergences = f.get("divergences") or []
        regime_desc = _speak(chip_regime, _CHIP_SPEAK)
        if divergences:
            return _end(
                f"{label}籌碼整體感覺{regime_desc}，另外還看到：{_speak_divergences(divergences)}。"
                f"這代表價跟籌碼沒完全同向，追價要更小心。"
            )
        return _end(f"{label}籌碼整體感覺{regime_desc}，目前沒看到很刺眼的背離。")

    return _end(f"{label}這項我在報告裡找不到對應數字。")


def build_glossary_reply(term_key: str, facts: dict[str, Any] | None) -> str:
    """Conversational glossary with this stock's numbers woven in."""
    f = facts or {}
    label = _stock_label(f)

    if term_key == "borrow_vs_short":
        borrow_lots = f.get("borrow_sell_today_lots")
        return _end(
            f"簡單講：融券多半是散戶向券商借股放空，常有強制回補日；"
            f"借券比較像外資／法人走集保借券在賣，量通常更大、也比較沒有股東會那種死線。"
            f"{label}今天借券賣出是 {_fmt_lots(borrow_lots).lstrip('+')}，可以把它當成法人避險味道的參考，不要跟融券混成同一件事。"
        )

    if term_key == "day_trade":
        d_today = f.get("day_trade_ratio_pct")
        return _end(
            f"當沖比就是「當天進出、隔夜不留倉」的量佔總成交多少。"
            f"偏高時盤中常比較躁、假突破也多；{label}今天大概 {d_today}%。"
            f"它比較像交易氣氛，不是直接告訴你該買該賣。"
        )

    if term_key == "major":
        major_net = f.get("major_net_lots")
        return _end(
            f"主力大概就是分點裡進出比較大、能影響短線定價的那群人。"
            f"跟外資同向時力道通常比較乾淨；對作時就容易震。"
            f"{label}今天主力{_flow_phrase(major_net)}。"
        )

    if term_key == "institutional":
        consensus = f.get("institutional_consensus") or "—"
        foreign = f.get("foreign_net_lots")
        return _end(
            f"三大法人就是外資、投信、自營這三股最大的法人錢。"
            f"一起買通常比較有趨勢感；外資賣、投信買這種土洋對作，常變成拉鋸。"
            f"{label}今天外資{_flow_phrase(foreign)}，{_consensus_phrase(consensus)}。"
        )

    if term_key == "margin_regime":
        m_today = f.get("margin_today_delta_lots")
        ratio = f.get("margin_short_ratio_pct")
        return _end(
            f"融資是借錢做多，融券是借股放空。"
            f"最忌諱的是股價跌、融資還在增——散戶越攤越套。"
            f"{label}今天融資{_flow_phrase(m_today)}"
            f"{f'，券資比約 {ratio}%' if ratio is not None else ''}。"
        )

    if term_key == "ma_alignment":
        stack = _speak(f.get("ma_stack"), _STACK_SPEAK)
        return _end(
            f"多頭排列大概就是短均線在上、長均線在下，像成本墊高的強勢模樣；"
            f"反過來就是空頭排列。均線黏在一起時，往往在等量能決定方向。"
            f"{label}現在比較像{stack}。"
        )

    if term_key == "rsi":
        rsi = f.get("rsi_14")
        rsi_str = f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "—"
        return _end(
            f"RSI 看的是一段時間漲跌力道誰比較強，常見看法是太高偏熱、太低偏冷。"
            f"{label}現在大約 {rsi_str}。它適合當溫度計，不適合當買賣按鈕。"
        )

    if term_key == "atr":
        atr = f.get("atr_14")
        atr_str = f"{atr:.2f}" if isinstance(atr, (int, float)) else "—"
        return _end(
            f"ATR 就是「一天大概會晃多大」，跟漲跌方向無關。"
            f"晃大時停損要留寬一點、部位也可以小一點。"
            f"{label}近期大概一天 {atr_str} 元的波幅。"
        )

    if term_key == "divergence":
        divergences = f.get("divergences") or []
        if divergences:
            return _end(
                f"背離就是價往一邊走、籌碼或量往另一邊走，常常是力道在變弱的警訊。"
                f"{label}目前看到：{_speak_divergences(divergences)}。"
            )
        return _end(f"背離就是價跟籌碼／量不同向。{label}這次沒看到很明顯的背離旗標。")

    return _end("你可以問外資、主力、融資券、當沖、均線、強弱指標、一天波動這些，我用這份報告的數字跟你講。")


def _to_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_int(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _is_pnl_question(message: str) -> bool:
    msg = (message or "").strip()
    if not msg:
        return False
    patterns = (
        r"獲利多少",
        r"損益多少",
        r"損益狀況",
        r"自己獲利",
        r"我的獲利",
        r"我的損益",
        r"未實現",
        r"賺多少",
        r"賠多少",
        r"報酬率",
        r"浮盈",
        r"浮虧",
        r"目前賺",
        r"現在賺",
        r"目前.{0,6}獲利",
        r"現在.{0,6}獲利",
        r"看一下.{0,8}獲利",
        r"幫忙.{0,8}獲利",
    )
    return any(re.search(pat, msg) for pat in patterns)


def extract_position_snapshot(
    *,
    stock_id: str,
    position_facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize cost / shares / close / unrealized PnL from available sources."""
    sid = str(stock_id).strip()
    pos = position_facts or {}
    summary_pos = {}
    if isinstance(summary, dict):
        raw = summary.get("position")
        if isinstance(raw, dict):
            summary_pos = raw

    cost = _to_float(pos.get("avg_cost"))
    shares = _to_int(pos.get("shares") if pos.get("shares") is not None else pos.get("share_count"))
    close = _to_float(pos.get("close_price") if pos.get("close_price") is not None else pos.get("close"))
    pnl_pct = _to_float(pos.get("unrealized_pnl_pct"))
    pnl_bucket = pos.get("pnl_bucket_label") or summary_pos.get("pnl_bucket_label")
    if not pnl_bucket:
        pnl_bucket = pos.get("pnl_bucket") or summary_pos.get("pnl_bucket")

    if cost is None:
        cost = _to_float(summary_pos.get("avg_cost"))
    if shares is None:
        shares = _to_int(
            summary_pos.get("shares")
            if summary_pos.get("shares") is not None
            else summary_pos.get("share_count")
        )
    if close is None:
        close = _to_float(
            summary_pos.get("close_price")
            if summary_pos.get("close_price") is not None
            else summary_pos.get("close")
        )
    if pnl_pct is None:
        pnl_pct = _to_float(summary_pos.get("unrealized_pnl_pct"))

    if (cost is None or shares is None) and holdings:
        for h in holdings:
            if str(h.get("stock_id", "")).strip() != sid:
                continue
            if cost is None:
                cost = _to_float(h.get("avg_cost"))
            if shares is None:
                shares = _to_int(
                    h.get("share_count") if h.get("share_count") is not None else h.get("shares")
                )
            break

    if close is None and cost is not None and pnl_pct is not None:
        close = cost * (1.0 + pnl_pct / 100.0)
    if pnl_pct is None and cost is not None and close is not None and cost != 0:
        pnl_pct = (close - cost) / cost * 100.0

    pnl_amount = None
    if cost is not None and close is not None and shares is not None:
        pnl_amount = (close - cost) * shares

    return {
        "avg_cost": cost,
        "shares": shares,
        "close_price": close,
        "unrealized_pnl_pct": pnl_pct,
        "unrealized_pnl_amount": pnl_amount,
        "pnl_bucket": pnl_bucket,
    }


def build_pnl_reply(
    *,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    position_facts: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
) -> str:
    """Answer 'how much am I up/down?' from holding + position snapshot."""
    f = facts or {}
    sid = str(f.get("stock_id") or "").strip()
    label = _stock_label(f)
    snap = extract_position_snapshot(
        stock_id=sid,
        position_facts=position_facts,
        summary=summary,
        holdings=holdings,
    )
    cost = snap.get("avg_cost")
    shares = snap.get("shares")
    close = snap.get("close_price")
    pnl_pct = snap.get("unrealized_pnl_pct")
    pnl_amount = snap.get("unrealized_pnl_amount")
    bucket = snap.get("pnl_bucket")

    if cost is None or shares is None:
        return _end(
            f"這份報告裡看不到你在 {label} 的持股成本／股數，所以無法試算目前損益。"
            f"若這檔有標記持股，重新產出部位報告後再問一次通常就能帶出來。"
        )

    holding_bit = f"你這檔成本約 {cost:g} 元、持有 {shares:,} 股"
    close_bit = f"，對到收盤約 {close:g} 元" if isinstance(close, (int, float)) else ""
    if isinstance(pnl_pct, (int, float)) and isinstance(pnl_amount, (int, float)):
        sign = "+" if pnl_amount >= 0 else ""
        bucket_bit = f"（{bucket}）" if bucket else ""
        return _end(
            f"{holding_bit}{close_bit}。"
            f"未實現損益大概 {sign}{pnl_amount:,.0f} 元，約 {pnl_pct:+.2f}%{bucket_bit}。"
            f"這是依報告基準日收盤試算，不是券商即時帳戶數字。"
        )
    if isinstance(pnl_pct, (int, float)):
        return _end(
            f"{holding_bit}{close_bit}。"
            f"未實現報酬率大約 {pnl_pct:+.2f}%。"
            f"這是依報告基準日收盤試算，不是券商即時帳戶數字。"
        )
    return _end(
        f"{holding_bit}{close_bit}，但報告沒寫收盤或損益％，我暫時算不出精確未實現損益。"
    )


def build_entry_template_reply(
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    has_holdings: bool = False,
) -> str:
    """Conversational entry discipline — no checklist dump."""
    f = facts or {}
    label = _stock_label(f)
    chip_regime = f.get("chip_regime") or "mixed"
    consensus = f.get("institutional_consensus") or "方向還不明"
    l20 = f.get("low_20d")
    h20 = f.get("high_20d")
    foreign = f.get("foreign_net_lots")

    return _end(
        f"若問能不能進，我不會直接說買或不買——那超出這份報告能保證的範圍。"
        f"就 {label} 現況看，籌碼{_speak(chip_regime, _CHIP_SPEAK)}，{_consensus_phrase(consensus)}，"
        f"外資今天{_flow_phrase(foreign)}。"
        f"若真要評估，我會先想清楚跌破 {l20 if l20 is not None else '前低／月線'} 要不要認錯，"
        f"以及上面 {h20 if h20 is not None else '前高'} 附近壓力怎麼辦；"
        f"籌碼沒轉好前，比較不適合用「跌深」當唯一理由重倉摸底。"
    )


def build_position_template_reply(
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    position_facts: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
    message: str | None = None,
) -> str:
    """Conversational position reply with a few key numbers."""
    f = facts or {}
    sid = str(f.get("stock_id") or "").strip()
    label = _stock_label(f)
    l20 = f.get("low_20d")
    atr = f.get("atr_14")
    foreign = f.get("foreign_net_lots")
    consensus = f.get("institutional_consensus") or "—"
    msg = (message or "").strip()

    if _is_pnl_question(msg):
        return build_pnl_reply(
            facts=facts,
            summary=summary,
            position_facts=position_facts,
            holdings=holdings,
        )

    snap = extract_position_snapshot(
        stock_id=sid,
        position_facts=position_facts,
        summary=summary,
        holdings=holdings,
    )
    cost = snap.get("avg_cost")
    shares = snap.get("shares")
    pnl_pct = snap.get("unrealized_pnl_pct")

    hold_exit_ask = bool(
        re.search(r"持有|出場|小損|小虧|認賠|要不要賣|該不該賣", msg)
    )
    lead = ""
    if hold_exit_ask:
        lead = (
            f"持有或小損出場，我不會替你下最後一刀——那取決於你的防守規則有沒有被打穿。"
            f"就 {label} 現況："
        )

    holding_bit = ""
    if cost is not None and shares is not None:
        holding_bit = f"你這檔大概成本 {cost:g}、持有 {shares:,} 股。"
        if isinstance(pnl_pct, (int, float)):
            holding_bit += f"未實現大約 {pnl_pct:+.2f}%。"

    foreign_bit = ""
    if isinstance(foreign, (int, float)) and foreign < 0:
        foreign_bit = (
            f"外資今天{_flow_phrase(foreign)}，壓力還在，"
            f"這種時候我比較不會建議用回檔當藉口一路攤平。"
        )
    elif isinstance(foreign, (int, float)) and foreign > 0:
        foreign_bit = f"外資今天{_flow_phrase(foreign)}，有承接，但還是要對一下你的防守位。"

    return _end(
        f"{lead}{holding_bit}"
        f"{_consensus_phrase(consensus)}。{foreign_bit}"
        f"實務上我會把近20日低點（{l20 if l20 is not None else '前波低點'}）或月線當警戒線："
        f"收盤真的跌破，就比較像買進理由失效，該想減碼／停損，而不是加碼賭反轉；"
        f"若還守得住、你也認得出自己的防守位，續抱通常比情緒化小損出場更有章法。"
        f"{f'一天大概會晃 {atr:.2f} 元，停利也可以用回檔超過這個幅度的一點五倍當提醒。' if isinstance(atr, (int, float)) else ''}"
        f"若外資賣超開始收斂、甚至轉買，再重新評估要不要加碼也不遲。"
    )


def parse_user_lot_claim(message: str) -> int | None:
    """Extract an absolute lot count the user cited (萬張 / 張)."""
    msg = (message or "").strip()
    if not msg:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*萬\s*張", msg)
    if m:
        try:
            return max(0, int(round(float(m.group(1)) * 10_000)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*張", msg)
    if m:
        try:
            return abs(int(m.group(1).replace(",", "")))
        except ValueError:
            return None
    return None


def _fmt_claim_lots(lots: int) -> str:
    if lots >= 10_000:
        wan = lots / 10_000
        text = f"{wan:.1f}".rstrip("0").rstrip(".")
        return f"{text} 萬張"
    return f"{lots:,} 張"


def _user_lot_grounding_prefix(message: str, fact_lots: Any) -> str:
    """Correct the user when their stated lots disagree with report facts."""
    claimed = parse_user_lot_claim(message)
    if claimed is None or not isinstance(fact_lots, (int, float)):
        return ""
    actual = abs(int(fact_lots))
    if actual <= 0:
        return ""
    ratio = claimed / actual
    # Close enough — treat as agreeing with the report.
    if 0.8 <= ratio <= 1.2:
        return ""
    claim_txt = _fmt_claim_lots(claimed)
    fact_txt = _flow_phrase(fact_lots)
    if 0.07 <= ratio <= 0.15 or 7 <= ratio <= 14:
        return f"先對數字：你剛說約 {claim_txt}，但報告是{fact_txt}（差一個數量級）。"
    return f"先對數字：你剛說約 {claim_txt}，報告裡是{fact_txt}。"


def _flow_sign_correction(message: str, fact_lots: Any) -> str:
    """Correct the user when they call a buy flow a sell, or the reverse."""
    if not isinstance(fact_lots, (int, float)) or fact_lots == 0:
        return ""
    msg = message or ""
    says_sell = re.search(r"賣", msg) is not None
    says_buy = re.search(r"買", msg) is not None
    actual = _flow_phrase(fact_lots)
    if says_sell and not says_buy and fact_lots > 0:
        return f"先對方向：報告裡是{actual}，不是賣超。"
    if says_buy and not says_sell and fact_lots < 0:
        return f"先對方向：報告裡是{actual}，不是買超。"
    return ""


def _daily_vs_cum_note(daily: Any, cum: Any) -> str:
    """Explain when |daily| exceeds |cum| of the same sign (earlier reverse flow)."""
    if not isinstance(daily, (int, float)) or not isinstance(cum, (int, float)):
        return ""
    if daily == 0 or cum == 0:
        return ""
    if (daily > 0) != (cum > 0):
        return ""
    if abs(int(daily)) <= abs(int(cum)):
        return ""
    return "（今天這筆比這段時間的累計還大，是前幾天有相反方向，把累計抵掉一些）"


def _repeat_ask_prefix(history: list[ChatHistoryItem] | None, message: str) -> str:
    """If user re-asks a similar prediction, acknowledge instead of copy-pasting."""
    if not history:
        return ""
    msg = (message or "").strip()
    if not msg:
        return ""
    prior_users = [
        (item.get("content") or "").strip()
        for item in history
        if item.get("role") == "user" and (item.get("content") or "").strip()
    ]
    if not prior_users:
        return ""
    claim = parse_user_lot_claim(msg)
    # Same or largely overlapping previous question.
    for prev in prior_users[-3:]:
        prev_claim = parse_user_lot_claim(prev)
        # Different lot claims → not the same question (e.g. 2.1萬 vs 21萬).
        if (
            claim is not None
            and prev_claim is not None
            and abs(claim - prev_claim) / max(claim, prev_claim, 1) > 0.25
        ):
            continue
        if prev == msg or (len(msg) >= 8 and msg[:12] in prev) or (len(prev) >= 8 and prev[:12] in msg):
            return random.choice(
                (
                    "這題你剛也問過類似的——結論沒變，我換個說法：",
                    "還是同一件事的話，重點其實沒變：",
                    "再講一次也一樣，只能用今天籌碼推，不能保證明天：",
                )
            )
    return ""


def build_inference_reply(
    topic: InferenceTopic,
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    *,
    history: list[ChatHistoryItem] | None = None,
    message: str | None = None,
) -> str:
    """Answer 'will they continue?' in plain language — evidence, not a forecast."""
    f = facts or {}
    label = _stock_label(f)
    foreign = f.get("foreign_net_lots")
    foreign_cum = f.get("foreign_cum_lots")
    streak_dir = f.get("foreign_streak_dir")
    streak_days = int(f.get("foreign_streak_days") or 0)
    consensus = f.get("institutional_consensus") or "—"
    major = f.get("major_net_lots")
    msg = message or ""
    grounding = _user_lot_grounding_prefix(msg, foreign if topic != "major_continue" else major)
    prefix = f"{grounding}{_repeat_ask_prefix(history, msg)}"

    if topic == "major_continue":
        variant = random.choice(
            (
                (
                    f"主力明天會不會延續，沒人能拍胸脯。"
                    f"{label}今天主力{_flow_phrase(major)}，外資{_flow_phrase(foreign)}；"
                    f"我會等賣超收斂或轉買、價格站回關鍵均線，才改看法。"
                ),
                (
                    f"單日主力數字推不出「明天一定怎樣」。"
                    f"目前 {label} 主力{_flow_phrase(major)}、外資{_flow_phrase(foreign)}，"
                    f"比較像短線力道參考，不是預測開關。"
                ),
            )
        )
        return _end(f"{prefix}{variant}")

    streak = ""
    if streak_dir == -1 and streak_days > 1:
        streak = f"連賣已有 {streak_days} 天，"
    elif streak_dir == 1 and streak_days > 1:
        streak = f"連買已有 {streak_days} 天，"

    cum_bit = ""
    if foreign_cum is not None:
        note = _daily_vs_cum_note(foreign, foreign_cum)
        cum_bit = f"區間累計{_flow_phrase(foreign_cum)}{note}，"

    if isinstance(foreign, (int, float)) and foreign < 0:
        if _asks_price_direction(msg):
            core = (
                f"明天會繼續跌還是反彈，這份報告推不出來。"
                f"看得到的是 {label} 今天{_flow_phrase(foreign)}，{cum_bit}{streak}"
                f"{_consensus_phrase(consensus)}，賣壓比較像還沒卸掉。"
                f"先看賣超有沒有縮小、有沒有人轉買，不要先猜明天一定跌或一定彈。"
            )
            return _end(f"{prefix}{core}")
        core = random.choice(
            (
                (
                    f"短答：無法斷言外資明天一定繼續賣。"
                    f"但 {label} 今天{_flow_phrase(foreign)}，{cum_bit}{streak}"
                    f"{_consensus_phrase(consensus)}，壓力比較像還沒卸掉。"
                ),
                (
                    f"我不會賭「明天還會賣」。"
                    f"就籌碼看得到的：{label}今日{_flow_phrase(foreign)}，{cum_bit}{streak}"
                    f"{_consensus_phrase(consensus)}。賣壓還在，但不是已經確定的事。"
                ),
                (
                    f"外資下一步不是報告能預測的。"
                    f"只能說現況偏空：{label}{_flow_phrase(foreign)}，{cum_bit}{streak}"
                    f"{_consensus_phrase(consensus)}。要轉強，至少得先看到賣超明顯縮小。"
                ),
            )
        )
        tail = random.choice(
            (
                "若之後賣超縮小或轉買，再談壓力緩解也不遲。",
                "在賣超還沒收斂前，部位上寧可先防守、少做樂觀假設。",
                "觀察點很單純：賣超有沒有縮、有沒有人轉買。",
            )
        )
    elif isinstance(foreign, (int, float)) and foreign > 0:
        core = random.choice(
            (
                (
                    f"短答：無法斷言外資明天一定繼續買。"
                    f"{label}今天{_flow_phrase(foreign)}，{streak}{_consensus_phrase(consensus)}，"
                    f"比較像有承接，但仍可能只是一天行情。"
                ),
                (
                    f"買超不代表隔天延續。"
                    f"目前 {label}{_flow_phrase(foreign)}，{streak}{_consensus_phrase(consensus)}；"
                    f"我會再看有沒有連續買、而不是只看今天。"
                ),
            )
        )
        tail = random.choice(
            (
                "若連買出現、共識也站穩，再說偏多延續比較有底氣。",
                "單日買超先當參考，別直接外推成趨勢。",
            )
        )
    else:
        core = (
            f"外資下一步我沒辦法預測。"
            f"{label}今天方向不夠清楚，{_consensus_phrase(consensus)}，先別急著押延續。"
        )
        tail = "等買賣方向比較乾淨再說。"

    return _end(f"{prefix}{core}{tail}")


def _asks_pullback_depth(message: str) -> bool:
    """True when the question is about how far price already fell, not a single chip slot."""
    return (
        re.search(r"回檔|回落|跌深|跌了|跌幅|這麼深|殺下來|跌下來|跌超過", message or "")
        is not None
    )


def _has_drawdown_path(facts: dict[str, Any]) -> bool:
    """A multi-day drop the chart already shows: window return, or distance under the 20-day high."""
    dist = facts.get("dist_to_20d_high_pct")
    ret = facts.get("period_return_pct")
    if isinstance(dist, (int, float)) and not isinstance(dist, bool) and dist >= 1:
        return True
    if isinstance(ret, (int, float)) and not isinstance(ret, bool) and ret <= -2:
        return True
    return False


def _price_path_sentence(facts: dict[str, Any]) -> str:
    """State the completed price path. MA20 deviation is a level, not this move."""
    parts: list[str] = []
    ret = facts.get("period_return_pct")
    days = facts.get("lookback_days")
    if isinstance(ret, (int, float)) and not isinstance(ret, bool):
        if isinstance(days, (int, float)) and not isinstance(days, bool) and int(days) > 0:
            window = f"近 {int(days)} 個交易日"
        else:
            window = "這段區間"
        parts.append(f"{window}收盤 {_fmt_pct(ret)}")
    dist = facts.get("dist_to_20d_high_pct")
    if isinstance(dist, (int, float)) and not isinstance(dist, bool) and dist >= 1:
        parts.append(f"收盤離近二十日高點 {float(dist):.2f}%")
    if not parts:
        return ""
    return "。".join(parts) + "。"


def _window_chip_sentence(facts: dict[str, Any]) -> str:
    """Chips over the same window as the price path. Today's print is a separate sentence."""
    bits: list[str] = []
    foreign_cum = facts.get("foreign_cum_lots")
    major_cum = facts.get("major_cum_lots")
    if isinstance(foreign_cum, (int, float)) and not isinstance(foreign_cum, bool):
        bits.append(f"同一段外資累計{_flow_phrase(foreign_cum)}")
    if isinstance(major_cum, (int, float)) and not isinstance(major_cum, bool):
        bits.append(f"主力累計{_flow_phrase(major_cum)}")
    if not bits:
        return ""
    return "，".join(bits) + "。"


def _path_block(facts: dict[str, Any]) -> str:
    path = _price_path_sentence(facts)
    if not path:
        return ""
    note = ""
    ma20 = facts.get("close_vs_ma20_pct")
    if isinstance(ma20, (int, float)) and not isinstance(ma20, bool):
        note = f"{_ma_side(ma20)}。這是現在離月線的位置，不是這段跌幅。"
    return f"{path}{_window_chip_sentence(facts)}{note}"


def _reading_focuses(message: str) -> list[str]:
    """Which fact groups the question actually names. Empty means a general risk read."""
    msg = message or ""
    found: list[str] = []
    if re.search(r"主力", msg):
        found.append("major")
    if re.search(r"融資|融券|券資", msg):
        found.append("margin")
    if re.search(r"背離", msg):
        found.append("divergence")
    if re.search(r"均線|月線|ma20|ma5", msg, re.I):
        found.append("ma")
    if re.search(r"\brsi\b|相對強弱", msg, re.I):
        found.append("rsi")
    if re.search(r"外資|法人", msg):
        found.append("foreign")
    return found or ["risk"]


def _ma_side(ma20: Any) -> str:
    if not isinstance(ma20, (int, float)):
        return ""
    if ma20 > 0:
        return f"收盤在月線之上（相對月線 {_fmt_pct(ma20)}）"
    if ma20 < 0:
        return f"收盤在月線之下（相對月線 {_fmt_pct(ma20)}）"
    return "收盤貼著月線"


def _foreign_ma_relation(foreign: Any, ma20: Any) -> str:
    above = isinstance(ma20, (int, float)) and ma20 > 0
    below = isinstance(ma20, (int, float)) and ma20 < 0
    if isinstance(foreign, (int, float)) and foreign < 0 and above:
        return "外資在賣，但價格還沒落到月線下，籌碼和價格不同步，不是月線已經失守。"
    if isinstance(foreign, (int, float)) and foreign < 0 and below:
        return "外資在賣，價格也在月線下，籌碼和價格同向偏空。"
    if isinstance(foreign, (int, float)) and foreign > 0 and below:
        return "外資在買，但價格還在月線下：有承接，均線還沒站回。"
    if isinstance(foreign, (int, float)) and foreign > 0 and above:
        return "外資在買，價格也在月線之上，籌碼和價格同向偏多。"
    return "外資方向或月線位置不夠清楚，先不要把單日籌碼外推成趨勢。"


def _reading_section(focus: str, facts: dict[str, Any], focuses: list[str]) -> str:
    foreign = facts.get("foreign_net_lots")
    major = facts.get("major_net_lots")
    ma20 = facts.get("close_vs_ma20_pct")
    if focus == "foreign":
        cum = facts.get("foreign_cum_lots")
        cum_bit = ""
        if isinstance(cum, (int, float)):
            cum_bit = f"區間累計{_flow_phrase(cum)}{_daily_vs_cum_note(foreign, cum)}。"
        relation = _foreign_ma_relation(foreign, ma20) if "ma" in focuses else "單日外資不能外推成明天的方向。"
        return f"外資今天{_flow_phrase(foreign)}。{cum_bit}{relation}"
    if focus == "ma":
        side = _ma_side(ma20) or "月線位置資料暫缺"
        align = _speak(facts.get("ma_alignment"), _ALIGN_SPEAK, empty="還看不出來")
        extra = "" if "foreign" in focuses else _foreign_ma_relation(foreign, ma20)
        return f"{side}，均線狀態是{align}。{extra}"
    if focus == "major":
        if major is None:
            return "這份報告沒有主力張數，沒辦法談主力。"
        same = (
            isinstance(foreign, (int, float))
            and isinstance(major, (int, float))
            and foreign != 0
            and major != 0
            and (foreign > 0) == (major > 0)
        )
        versus = "跟外資同向，不是對作。" if same else "跟外資方向相反，這就是對作。"
        if not isinstance(foreign, (int, float)):
            versus = "報告沒有外資淨額，只能先看主力本身。"
        return f"主力今天{_flow_phrase(major)}，外資{_flow_phrase(foreign)}，{versus}"
    if focus == "margin":
        today = facts.get("margin_today_delta_lots")
        ratio = facts.get("margin_short_ratio_pct")
        ratio_bit = f"券資比大概 {ratio}%。" if isinstance(ratio, (int, float)) else ""
        if isinstance(today, (int, float)) and today > 0:
            risk = "融資在增加，散戶槓桿變高，這是部位風險。"
        elif isinstance(today, (int, float)) and today < 0:
            risk = "融資在減少，不是散戶正在加槓桿。"
        else:
            risk = "融資今天幾乎沒動。"
        return f"融資今天{_flow_phrase(today)}。{ratio_bit}{risk}"
    if focus == "divergence":
        divs = facts.get("divergences") or []
        vp = facts.get("volume_price_divergence")
        if isinstance(foreign, (int, float)) and isinstance(major, (int, float)) and foreign != 0 and major != 0:
            pair = "主力與外資方向相反。" if (foreign > 0) != (major > 0) else "主力與外資方向相同。"
        else:
            pair = ""
        if divs:
            shown = "、".join(str(item) for item in divs[:3])
            return f"{pair}報告標了背離：{shown}。"
        if vp:
            return f"{pair}量價欄位是「{vp}」，divergences 清單是空的。"
        return f"{pair}報告的 divergences 是空的，沒有另標背離。"
    if focus == "rsi":
        rsi = facts.get("rsi_14")
        zone = facts.get("rsi_zone") or "neutral"
        if not isinstance(rsi, (int, float)):
            return "這份報告沒有強弱指標。"
        return f"強弱指標大概 {rsi:.1f}，{_speak(zone, _RSI_SPEAK)}。"
    # Generic risk: the completed price path first, then today's print.
    # close vs MA20 is where price sits now, not how far it fell.
    stresses: list[str] = []
    if isinstance(foreign, (int, float)) and foreign < 0:
        stresses.append(f"外資今天{_flow_phrase(foreign)}")
    zone = facts.get("rsi_zone")
    rsi = facts.get("rsi_14")
    if zone in ("overbought", "oversold") and isinstance(rsi, (int, float)):
        stresses.append(f"強弱指標大概 {rsi:.1f}，{_speak(zone, _RSI_SPEAK)}")
    margin = facts.get("margin_today_delta_lots")
    if isinstance(margin, (int, float)) and margin > 0:
        stresses.append(f"融資今天{_flow_phrase(margin)}")
    if _has_drawdown_path(facts):
        today = ("今天這邊是：" + "；".join(stresses) + "。") if stresses else ""
        return f"{_path_block(facts)}{today}"
    if isinstance(ma20, (int, float)) and ma20 < 0:
        stresses.append(_ma_side(ma20))
    if not stresses:
        side = _ma_side(ma20)
        return (
            f"外資今天{_flow_phrase(foreign)}"
            f"{'，' + side if side else ''}。"
            f"報告裡沒有明顯賣壓或月線失守，風險是這些條件之後轉弱，不是現在已經破位。"
        )
    return "目前比較像風險的是：" + "；".join(stresses) + "。"


def _implication_angle(message: str) -> str:
    """How the follow-up wants the same facts read. Unknown wording stays 'meaning'."""
    msg = message or ""
    if re.search(r"嚴重|要緊|危險|糟糕|可怕", msg):
        return "severity"
    if re.search(r"正常|合理|常見|奇怪|異常|罕見", msg):
        return "normal"
    if re.search(r"好壞|算好|算壞|利多|利空", msg):
        return "valence"
    return "meaning"


def _reading_implication(
    facts: dict[str, Any],
    focuses: list[str],
    *,
    angle: str = "meaning",
) -> str:
    """Explain what the combination means, without restating the fact paragraph."""
    foreign = facts.get("foreign_net_lots")
    major = facts.get("major_net_lots")
    ma20 = facts.get("close_vs_ma20_pct")
    above = isinstance(ma20, (int, float)) and ma20 > 0
    below = isinstance(ma20, (int, float)) and ma20 < 0
    parts: list[str] = []

    if "foreign" in focuses and "ma" in focuses:
        if isinstance(foreign, (int, float)) and foreign < 0 and above:
            basis = f"依據是外資今天{_flow_phrase(foreign)}，價格卻還在月線上。"
            if angle == "severity":
                parts.append(
                    f"還沒到嚴重：月線沒破，賣壓還沒被價格確認。{basis}"
                    "要等收盤落到月線下，才算這筆賣超被價格認了。"
                )
            elif angle == "normal":
                parts.append(
                    f"這種不同步不稀奇，也不是兩套數字矛盾。{basis}"
                    "外資可以先賣，價格還留在月線上，中間就是時間差。"
                )
            elif angle == "valence":
                parts.append(
                    f"不能算單邊利空或利多。籌碼這側偏空，價格這側還沒轉空。{basis}"
                )
            else:
                parts.append(
                    "這代表背離，不是月線已經破掉。"
                    f"{basis}"
                    "賣壓還沒被價格確認。"
                    "不能把月線之上讀成外資沒在賣，也不能把賣超讀成月線已經失守。"
                )
        elif isinstance(foreign, (int, float)) and foreign < 0 and below:
            parts.append(
                "這代表籌碼和價格同向偏空，不是互相打架。"
                f"依據是外資今天{_flow_phrase(foreign)}，收盤也在月線下。"
            )
        elif isinstance(foreign, (int, float)) and foreign > 0 and below:
            parts.append(
                "這代表有承接、但均線還沒確認。"
                f"依據是外資今天{_flow_phrase(foreign)}，價格還在月線下。"
            )
        elif isinstance(foreign, (int, float)) and foreign > 0 and above:
            parts.append(
                "這代表籌碼和價格同向偏多，不是背離。"
                f"依據是外資今天{_flow_phrase(foreign)}，價格也在月線之上。"
            )
        else:
            parts.append("外資和月線對不上一個清楚的組合，先不要替它下結論。")
        parts.append("明天會不會續買或續賣，這組數字推不出來。")
    elif "major" in focuses:
        if isinstance(major, (int, float)) and isinstance(foreign, (int, float)) and major != 0 and foreign != 0:
            if (major > 0) == (foreign > 0):
                parts.append("主力跟外資同向，代表力道一致，不是對作。")
            else:
                parts.append("主力跟外資方向相反，代表短線力道不一致，這就是對作。")
        else:
            parts.append("主力這題只能就主力本身讀，報告沒有足夠的外資數字可對。")
        parts.append("這仍不是明天誰還會買的預測。")
    elif "margin" in focuses:
        parts.append("融資這裡代表的是散戶槓桿有沒有升高，不是法人方向。")
    elif "divergence" in focuses:
        parts.append("背離代表兩個已核對的欄位不同向，不是其中一個數字算錯。")
    else:
        parts.append(_foreign_ma_relation(foreign, ma20))

    if "foreign" in focuses:
        cum = facts.get("foreign_cum_lots")
        if isinstance(foreign, (int, float)) and isinstance(cum, (int, float)) and abs(foreign) > abs(cum) and foreign != 0 and (foreign > 0) == (cum > 0):
            parts.append("今天這筆比這段時間的累計還大，也不代表兩套數字打架，是前幾天有相反方向，把累計抵掉一些。")
    return "".join(parts)


def build_reading_reply(
    facts: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    *,
    message: str | None = None,
    implication: bool | None = None,
    raw_message: str | None = None,
) -> str:
    """Read the fact groups the question names. Fallback when the model is unavailable."""
    del summary  # reserved so callers match other reply builders
    f = facts or {}
    label = _stock_label(f)
    msg = message or ""
    asked = raw_message if raw_message is not None else msg
    focuses = _reading_focuses(msg)
    foreign = f.get("foreign_net_lots")
    fact_lots = f.get("major_net_lots") if "major" in focuses and "foreign" not in focuses else foreign
    prefix = f"{_flow_sign_correction(asked, fact_lots)}{_user_lot_grounding_prefix(asked, fact_lots)}"
    cue = _followup_cue(asked)
    if implication or cue in {"代表什麼", "矛盾"}:
        body = _reading_implication(f, focuses, angle=_implication_angle(asked))
        return _end(f"{prefix}就 {label} 來說，{body}")
    body = "".join(_reading_section(focus, f, focuses) for focus in focuses)
    include_path = _has_drawdown_path(f) and (
        focuses == ["risk"] or _asks_pullback_depth(asked)
    )
    if include_path and focuses != ["risk"]:
        body = _path_block(f) + body
    closer = (
        "這不是預測明天。"
        if include_path
        else "這是把問到的欄位放在一起讀，不是預測明天。"
    )
    return _end(f"{prefix}就 {label} 報告數字一起看：{body}{closer}")


def _material_numbers(text: str) -> list[float]:
    """Numbers a reply is asserting: 萬張, percents, decimals, and integers >= 10."""
    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text or "")
    found: list[float] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*萬", cleaned):
        found.append(float(match.group(1)) * 10000)
    cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*萬", " ", cleaned)
    for match in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)", cleaned):
        token = match.group(1)
        number = float(token.replace(",", ""))
        if "." not in token and "," not in token and abs(number) < 10:
            continue
        found.append(number)
    return found


def _allowed_reading_numbers(facts: dict[str, Any] | None, message: str | None) -> list[float]:
    allowed: list[float] = []

    def add(value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        number = float(value)
        allowed.append(number)
        allowed.append(abs(number))

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for item in obj.values():
                walk(item)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        else:
            add(obj)

    walk(build_compact_stock_facts(facts))
    stock_id = str((facts or {}).get("stock_id") or "")
    if stock_id.isdigit():
        add(int(stock_id))
    trade_date = str((facts or {}).get("trade_date") or "")
    dated = re.match(r"(\d{4})-(\d{2})-(\d{2})", trade_date)
    if dated:
        add(int(dated.group(1)))
        add(int(dated.group(2)))
        add(int(dated.group(3)))
    for number in _material_numbers(message or ""):
        add(number)
    for match in re.finditer(r"\d+(?:\.\d+)?", message or ""):
        add(float(match.group(0)))
    return allowed


def _number_allowed(number: float, allowed: list[float]) -> bool:
    for candidate in allowed:
        if abs(number - candidate) <= 0.05:
            return True
        if abs(candidate) >= 1 and abs(number - candidate) / abs(candidate) <= 0.02:
            return True
        if abs(candidate) >= 1000 and abs(number - candidate / 10000) <= 0.15:
            return True
    return False


def _clause_flips_flow(text: str, lots: Any) -> bool:
    if not isinstance(lots, (int, float)) or lots == 0:
        return False
    cleaned = (
        text.replace("不是賣超", "")
        .replace("不是買超", "")
        .replace("不是在賣", "")
        .replace("不是在買", "")
    )
    says_sell = re.search(r"賣超|在賣", cleaned) is not None
    says_buy = re.search(r"買超|在買", cleaned) is not None
    if says_sell and says_buy:
        return False
    if lots > 0 and says_sell:
        return True
    if lots < 0 and says_buy:
        return True
    return False


def repeats_prior_assistant(reply: str, prior_reply: str | None) -> bool:
    """True when the model pasted the previous fact paragraph again."""
    current = re.sub(r"\s+", "", reply or "")
    prior = re.sub(r"\s+", "", prior_reply or "")
    if len(current) < 40 or len(prior) < 40:
        return False
    if current in prior or prior in current:
        return True
    return "報告數字一起看" in (reply or "") and "報告數字一起看" in (prior_reply or "")


def reading_reply_grounded(
    reply: str,
    facts: dict[str, Any] | None,
    message: str | None = None,
) -> bool:
    """False when the model invents a number or flips a buy/sell sign."""
    f = facts or {}
    msg = message or ""
    check_major = re.search(r"主力", msg) is not None
    check_foreign = re.search(r"外資|法人", msg) is not None or not check_major
    for sentence in re.split(r"[。！？\n]", reply or ""):
        if check_major and "主力" in sentence and _clause_flips_flow(sentence, f.get("major_net_lots")):
            return False
        if check_foreign and re.search(r"外資|法人", sentence) and _clause_flips_flow(
            sentence, f.get("foreign_net_lots")
        ):
            return False
    allowed = _allowed_reading_numbers(f, msg)
    return all(_number_allowed(number, allowed) for number in _material_numbers(reply or ""))


def build_compact_stock_facts(facts: dict[str, Any] | None) -> dict[str, Any]:
    if not facts:
        return {}
    return {
        "stock_id": facts.get("stock_id"),
        "stock_name": facts.get("stock_name"),
        "trade_date": facts.get("trade_date"),
        "foreign_net_lots": facts.get("foreign_net_lots"),
        "trust_net_lots": facts.get("trust_net_lots"),
        "dealer_net_lots": facts.get("dealer_net_lots"),
        "major_net_lots": facts.get("major_net_lots"),
        "foreign_cum_lots": facts.get("foreign_cum_lots"),
        "major_cum_lots": facts.get("major_cum_lots"),
        "foreign_streak_days": facts.get("foreign_streak_days"),
        "lookback_days": facts.get("lookback_days"),
        "period_return_pct": facts.get("period_return_pct"),
        "price_trend": facts.get("price_trend"),
        "dist_to_20d_high_pct": facts.get("dist_to_20d_high_pct"),
        "today_change_pct": facts.get("today_change_pct"),
        "close_vs_ma5_pct": facts.get("close_vs_ma5_pct"),
        "close_vs_ma20_pct": facts.get("close_vs_ma20_pct"),
        "ma_stack": facts.get("ma_stack"),
        "ma_alignment": facts.get("ma_alignment"),
        "ma20_slope": facts.get("ma20_slope"),
        "rsi_14": facts.get("rsi_14"),
        "rsi_zone": facts.get("rsi_zone"),
        "atr_14": facts.get("atr_14"),
        "atr_pct": facts.get("atr_pct"),
        "margin_today_delta_lots": facts.get("margin_today_delta_lots"),
        "margin_short_ratio_pct": facts.get("margin_short_ratio_pct"),
        "day_trade_ratio_pct": facts.get("day_trade_ratio_pct"),
        "borrow_sell_today_lots": facts.get("borrow_sell_today_lots"),
        "chip_regime": facts.get("chip_regime"),
        "institutional_consensus": facts.get("institutional_consensus"),
        "divergences": facts.get("divergences"),
        "high_20d": facts.get("high_20d"),
        "low_20d": facts.get("low_20d"),
    }


def prepare_stock_report_chat(
    *,
    stock_id: str,
    message: str,
    facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
    position_facts: dict[str, Any] | None = None,
    position_markdown: str | None = None,
    trade_date: str | None = None,
    holdings: list[dict[str, Any]] | None = None,
    history: list[ChatHistoryItem] | None = None,
    use_llm: bool = True,
    fundamental_lookup: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Prepare a grounded chat execution plan for single stock reports."""
    sid = str(stock_id).strip()
    msg = (message or "").strip()
    if not sid:
        raise ValueError("stock_id 不可為空")
    if not msg:
        raise ValueError("message 不可為空")

    if facts is None and summary is None and markdown is None:
        loaded = load_stock_artifacts(sid, trade_date)
        facts = loaded.get("facts")
        summary = loaded.get("summary")
        markdown = loaded.get("markdown")
        position_facts = position_facts or loaded.get("position_facts")
        position_markdown = position_markdown or loaded.get("position_markdown")
    elif position_facts is None or position_markdown is None:
        # Frontend often sends chip facts/summary but omits position artifacts.
        try:
            loaded = load_stock_artifacts(sid, trade_date or (facts or {}).get("trade_date"))
            position_facts = position_facts or loaded.get("position_facts")
            position_markdown = position_markdown or loaded.get("position_markdown")
        except Exception:
            pass

    if not facts and not summary and not markdown:
        raise ValueError(f"找不到標的 {sid} 之深度報告或籌碼數據 artifacts")

    # Short follow-ups keep the previous subject. If the user did not ask for a
    # number, a forecast, a holding decision, or a new topic, explain that
    # subject instead of repeating the fact paragraph. Wording can vary.
    raw_msg = msg
    followup = resolve_followup(msg, history)
    msg = str(followup["message"])
    deepen = bool(followup["deepen"])
    implication = deepen or _followup_cue(raw_msg) in {"代表什麼", "矛盾"}
    intent: Intent = "reading" if deepen else classify_intent(msg)
    sources_used = ["facts"] if facts else ["report"]
    resolved_trade = (facts or {}).get("trade_date") or trade_date
    position_snap = extract_position_snapshot(
        stock_id=sid,
        position_facts=position_facts,
        summary=summary,
        holdings=holdings,
    )

    def _complete(reply: str, source: str) -> dict[str, Any]:
        return {
            "mode": "complete",
            "result": {
                "reply": reply,
                "intent": intent,
                "source": source,
                "sources_used": sources_used,
                "stock_id": sid,
                "stock_name": (facts or {}).get("stock_name"),
                "trade_date": resolved_trade,
            },
        }

    def _conversational_llm(
        *,
        fallback_reply: str,
        fallback_source: str,
        task_hint: str,
        source_tag: str,
        policy_extra: str = "",
        guard_numbers: bool = False,
    ) -> dict[str, Any]:
        compact_facts = build_compact_stock_facts(facts)
        holding_rows = list(holdings or [])[:8]
        holding_note = ""
        if holding_rows or position_facts or any(
            position_snap.get(k) is not None
            for k in ("avg_cost", "shares", "unrealized_pnl_pct", "unrealized_pnl_amount")
        ):
            holding_note = (
                f"\n持股／損益快照（可引用，不可發明）："
                f"{json.dumps({'holdings': holding_rows, 'position_snapshot': position_snap, 'position_facts': position_facts}, ensure_ascii=False)}"
            )
        clipped_md = (markdown or "")[:1800]
        user_prompt = (
            f"使用者問題：{raw_msg}\n"
            f"任務提示：{task_hint}\n\n"
            f"可用事實（只能用這些數字）：\n"
            f"{json.dumps(_facts_for_reader(compact_facts), ensure_ascii=False)}\n"
            f"{holding_note}\n\n"
            f"報告摘錄（輔助語境，數字仍以 facts 為準）：\n{clipped_md}\n\n"
            f"請直接用人話回答上面的問題。"
        )
        system = SYSTEM_POLICY if not policy_extra else f"{SYSTEM_POLICY}\n\n{policy_extra}"
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if history:
            for item in history[-MAX_HISTORY_TURNS:]:
                r = item.get("role")
                c = item.get("content", "").strip()
                if r in ("user", "assistant") and c:
                    messages.append({"role": r, "content": c[:1200]})
        messages.append({"role": "user", "content": user_prompt})
        plan: dict[str, Any] = {
            "mode": "ollama",
            "messages": messages,
            "fallback_reply": fallback_reply,
            "fallback_source": fallback_source,
            "meta": {
                "intent": intent,
                "source": source_tag,
                "sources_used": list(sources_used) + ["llm"],
                "stock_id": sid,
                "stock_name": (facts or {}).get("stock_name"),
                "trade_date": resolved_trade,
            },
        }
        if guard_numbers:
            prior_reply = ""
            for item in reversed(history or []):
                content = (item.get("content") or "").strip()
                if item.get("role") == "assistant" and content:
                    prior_reply = content
                    break
            plan["number_guard"] = {
                "facts": facts or {},
                "message": msg,
                "prior_reply": prior_reply,
            }
        return plan

    # 1. Entry — prefer natural LLM, conversational template fallback
    if intent == "entry_advice":
        reply = build_entry_template_reply(
            facts=facts,
            summary=summary,
            has_holdings=bool(holdings or position_facts),
        )
        if not use_llm:
            sources_used.append("template")
            return _complete(reply, "template")
        sources_used.append("template")
        return _conversational_llm(
            fallback_reply=reply,
            fallback_source="template",
            task_hint="使用者在問能不能進場。不要給買賣指令；先講你的看法邊界，再點 1～2 個關鍵籌碼／防守位。",
            source_tag="llm",
        )

    # 2. Position / PnL
    if intent == "position_advice":
        reply = build_position_template_reply(
            facts=facts,
            summary=summary,
            position_facts=position_facts,
            holdings=holdings,
            message=msg,
        )
        # PnL answers must stay deterministic (no LLM paraphrase of money numbers).
        if _is_pnl_question(msg) or not use_llm:
            sources_used.append("position_template")
            return _complete(reply, "template")
        sources_used.append("position_template")
        return _conversational_llm(
            fallback_reply=reply,
            fallback_source="template",
            task_hint=(
                "使用者在問持股怎麼辦／要不要持有或小損出場。"
                "直接回答持有 vs 出場的判斷框架（防守位有沒有破、外資賣壓），"
                "不要反問「你想問法人還是持股」那種踢皮球；"
                "禁止下單指令與保證獲利；禁止條列情境一二三。"
            ),
            source_tag="llm",
        )

    # 3. Inference (will they continue?)
    if intent == "inference":
        topic = match_inference_topic(msg)
        reply = build_inference_reply(
            topic,
            facts,
            summary,
            history=history,
            message=msg,
        )
        if not use_llm:
            sources_used.append("inference_template")
            return _complete(reply, "inference")
        sources_used.append("inference_template")
        return _conversational_llm(
            fallback_reply=reply,
            fallback_source="inference",
            task_hint=(
                "使用者在問會不會延續（例如外資會不會繼續賣）。"
                "若使用者講的張數跟 facts 差很多（例如差一個數量級），第一句先糾正報告數字，再回答。"
                "第一句先正面回答：無法斷言一定會／不會；再用今日淨額、連買連賣、累計當依據；"
                "賣超／買超方向必須跟 facts 符號一致，禁止講反。"
                "若單日絕對值大於區間累計，補一句：前幾天有相反方向，把累計抵掉一些。"
                "不要重貼三大法人報表、不要講成持股停損檢核。"
                "若對話裡剛問過類似問題，換個說法，不要一字不差複讀。"
            ),
            source_tag="llm",
        )

    # 3b. Reading — synthesize checked facts. Model may reason; numbers stay locked.
    if intent == "reading":
        reply = build_reading_reply(
            facts,
            summary,
            message=msg,
            implication=implication,
            raw_message=raw_msg,
        )
        if not use_llm:
            sources_used.append("reading")
            return _complete(reply, "reading")
        sources_used.append("reading")
        return _conversational_llm(
            fallback_reply=reply,
            fallback_source="reading",
            task_hint=(
                f"使用者原話是「{raw_msg}」。這是在追問上一句那個組合。"
                "依這句在問的角度回答：意思、嚴不嚴重、正不正常，或好壞。"
                "對話裡已經講過張數和月線位置，禁止把上一段再講一次。"
                "用 2～4 句解釋：它否定哪一種讀法、還不能推出明天漲跌。"
                "最多再提 1 個 facts 裡的數字。禁止下單。"
                if implication
                else
                "使用者在問這些數字放在一起怎麼讀（為什麼、代表什麼、風險在哪）。"
                "只串問題點名的欄位：主力就談主力與外資是否對作，融資就談融資券，"
                "背離就談背離，均線／月線就談均線，外資就談外資。"
                "若問題在問回檔、跌深或原因，第一句先講價格路徑："
                "近 lookback_days 個交易日的 period_return_pct，以及 dist_to_20d_high_pct。"
                "close_vs_ma20_pct 是現在離月線的位置，禁止把它說成這段跌幅。"
                "同一段籌碼用累計張數；今天的淨額只是這段的最後一天。"
                "不要每題都補外資和月線。不要只報今天買超或賣超幾張。"
                "不要預測明天，不要下單。禁止寫出 facts 沒有的數字。"
            ),
            source_tag="llm",
            policy_extra=READING_POLICY_EXTRA,
            guard_numbers=True,
        )

    # 4. Glossary — keep short template (already conversational)
    if intent == "glossary":
        term_key = match_glossary_term(msg)
        if term_key:
            sources_used.append("glossary")
            reply = build_glossary_reply(term_key, facts)
            return _complete(reply, "glossary")

    # 5. Factual slots — short conversational template (deterministic)
    if intent == "factual_qa":
        slot = match_factual_slot(msg)
        if slot:
            sources_used.append("facts_slot")
            reply = build_factual_slot_reply(slot, facts, summary)
            return _complete(reply, "facts")

    # 5a. Valuation / revenue YoY — live FinMind, numbers stay in the template.
    if intent == "fundamental_qa":
        row = None
        if resolved_trade:
            lookup = fundamental_lookup or lookup_stock_fundamentals
            try:
                row = lookup(sid, str(resolved_trade))
            except Exception:
                row = None
        reply = build_fundamental_reply(
            facts=facts,
            message=msg,
            trade_date=str(resolved_trade) if resolved_trade else None,
            row=row,
        )
        sources_used.append("fundamentals")
        return _complete(reply, "fundamentals")

    # 5b. News-grounded (營收／財報／消息) — search report news before refusing
    if intent == "news_qa":
        topic = match_news_grounded_topic(msg) or "相關消息"
        reply, source = build_news_grounded_reply(
            facts=facts,
            summary=summary,
            markdown=markdown,
            message=msg,
            topic=topic,
        )
        sources_used.append(source)
        return _complete(reply, source)

    # 5c. True out of scope
    if intent == "out_of_scope":
        topic = match_out_of_scope_topic(msg) or "這類估值資訊"
        sources_used.append("out_of_scope")
        reply = build_out_of_scope_reply(topic, facts, msg)
        return _complete(reply, "out_of_scope")

    # 6. Open QA — do not pretend chip overview answers an unrelated question
    open_fallback = _end(
        f"這題我在 {_stock_label(facts)} 這份報告裡對不到直接對應的欄位，不敢亂答。"
        f"你可以改問外資／主力／均線／支撐壓力，或持股損益；那些我有數字可以對。"
    )
    if not use_llm:
        return _complete(open_fallback, "facts_fallback")

    return _conversational_llm(
        fallback_reply=open_fallback,
        fallback_source="facts_fallback",
        task_hint=(
            "自由追問。先直接回答；報告沒寫的資訊就明說沒寫，"
            "禁止改聊外資主力總覽來敷衍；少堆數字。"
        ),
        source_tag="llm",
    )


def list_ollama_models(
    base_url: str | None = None,
    *,
    timeout_sec: float = 1.5,
) -> list[str]:
    """Return installed Ollama model names (best-effort)."""
    base = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    req = urllib.request.Request(f"{base}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            names.append(name)
    return names


def resolve_ollama_model(
    base_url: str | None = None,
    preferred: str | None = None,
) -> str:
    """Pick an installed model; fall back to preferred name if tags unavailable."""
    preferred_name = (
        preferred or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    ).strip()
    installed = list_ollama_models(base_url)
    if not installed:
        return preferred_name
    if preferred_name in installed:
        return preferred_name
    pref_base = preferred_name.split(":")[0]
    for name in installed:
        if name.startswith(f"{preferred_name}:") or name.split(":")[0] == pref_base:
            return name
    # Local chat: prefer any llama3.* if the configured tag is missing.
    for name in installed:
        if name.startswith("llama3"):
            return name
    return installed[0]


def iter_ollama_stream(
    messages: list[dict[str, str]],
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
):
    base = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    model_name = resolve_ollama_model(base_url=base, preferred=model)
    url = f"{base}/api/chat"
    payload = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.55},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_sec)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"Ollama HTTP {exc.code}（model={model_name}）：{detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"無法連線 Ollama（{base}）：{exc.reason}") from exc

    total = 0
    try:
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("done") and not (chunk.get("message") or {}).get("content"):
                break
            message = chunk.get("message") or {}
            piece = message.get("content") or ""
            if not piece:
                continue
            if total >= MAX_REPLY_CHARS:
                continue
            remain = MAX_REPLY_CHARS - total
            if len(piece) > remain:
                piece = piece[:remain] + "…(截斷)"
            total += len(piece)
            yield piece
            if chunk.get("done"):
                break
    finally:
        resp.close()


def _chunk_text_for_sse(text: str, *, size: int = 14) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def _iter_paced_tokens(text: str, *, size: int = 14, delay_sec: float = 0.02):
    """Yield reply pieces with a short pause so clients can paint incrementally."""
    for piece in _chunk_text_for_sse(text, size=size):
        yield piece
        if delay_sec > 0:
            time.sleep(delay_sec)


def _ollama_reachable(base_url: str | None = None, *, timeout_sec: float = 0.35) -> bool:
    """Cheap TCP probe so we can fail over to paced fallback without waiting."""
    raw = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).strip()
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def iter_stock_report_chat_events(
    *,
    stock_id: str,
    message: str,
    facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
    position_facts: dict[str, Any] | None = None,
    position_markdown: str | None = None,
    trade_date: str | None = None,
    holdings: list[dict[str, Any]] | None = None,
    history: list[ChatHistoryItem] | None = None,
    use_llm: bool = True,
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
):
    """Yield SSE event tuples: (event_name, payload_dict). Tokens release as they are ready."""
    try:
        plan = prepare_stock_report_chat(
            stock_id=stock_id,
            message=message,
            facts=facts,
            summary=summary,
            markdown=markdown,
            position_facts=position_facts,
            position_markdown=position_markdown,
            trade_date=trade_date,
            holdings=holdings,
            history=history,
            use_llm=use_llm,
        )
    except Exception as exc:
        yield ("error", {"error": str(exc)})
        return

    if plan["mode"] == "complete":
        result = plan["result"]
        yield ("meta", {k: v for k, v in result.items() if k != "reply"})
        for piece in _iter_paced_tokens(result["reply"]):
            yield ("token", {"text": piece})
        yield ("done", result)
        return

    meta = plan["meta"]
    yield ("meta", meta)
    fallback = plan.get("fallback_reply")

    def _stream_fallback(source: str | None = None) -> Any:
        text = fallback or ""
        if not text:
            yield ("error", {"error": "串流生成失敗，且沒有可用備援回覆"})
            return
        meta_fb = {
            **meta,
            "source": source or plan.get("fallback_source") or "facts_fallback",
        }
        yield ("meta", meta_fb)
        for piece in _iter_paced_tokens(text):
            yield ("token", {"text": piece})
        yield ("done", {**meta_fb, "reply": text})

    # If Ollama is not up, release paced fallback immediately (no long wait).
    if not _ollama_reachable(ollama_base_url):
        yield from _stream_fallback()
        return

    guard = plan.get("number_guard")

    def _grounded(text: str) -> bool:
        if not guard:
            return True
        if repeats_prior_assistant(text, guard.get("prior_reply")):
            return False
        return reading_reply_grounded(text, guard.get("facts"), guard.get("message") or "")

    stream_timeout = float(os.environ.get("STOCK_CHAT_OLLAMA_TIMEOUT_SEC", "4") or "4")
    collected: list[str] = []
    try:
        for piece in iter_ollama_stream(
            plan["messages"],
            base_url=ollama_base_url,
            model=ollama_model,
            timeout_sec=max(2, int(stream_timeout)),
        ):
            collected.append(piece)
            if not guard:
                yield ("token", {"text": piece})
    except Exception:
        if not collected:
            yield from _stream_fallback()
            return
        partial = "".join(collected).strip()
        if not _grounded(partial):
            yield from _stream_fallback()
            return
        if DISCLAIMER not in partial:
            tail = f"\n\n{DISCLAIMER}"
            if guard:
                partial += tail
            else:
                yield ("token", {"text": tail})
                partial += tail
        if guard:
            for piece in _iter_paced_tokens(partial):
                yield ("token", {"text": piece})
        yield ("done", {**meta, "reply": partial})
        return

    final_text = "".join(collected).strip()
    if not final_text or not _grounded(final_text):
        yield from _stream_fallback()
        return
    if DISCLAIMER not in final_text:
        tail = f"\n\n{DISCLAIMER}"
        final_text += tail
        if not guard:
            yield ("token", {"text": tail})
    if guard:
        for piece in _iter_paced_tokens(final_text):
            yield ("token", {"text": piece})
    yield ("done", {**meta, "reply": final_text})


def answer_stock_report_chat(
    *,
    stock_id: str,
    message: str,
    facts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    markdown: str | None = None,
    position_facts: dict[str, Any] | None = None,
    position_markdown: str | None = None,
    trade_date: str | None = None,
    holdings: list[dict[str, Any]] | None = None,
    history: list[ChatHistoryItem] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Synchronous answer for tool and MCP calls."""
    plan = prepare_stock_report_chat(
        stock_id=stock_id,
        message=message,
        facts=facts,
        summary=summary,
        markdown=markdown,
        position_facts=position_facts,
        position_markdown=position_markdown,
        trade_date=trade_date,
        holdings=holdings,
        history=history,
        use_llm=use_llm,
    )
    if plan["mode"] == "complete":
        return plan["result"]

    # If in LLM mode, run through llm.complete or Ollama
    try:
        from agent.llm import complete as llm_complete

        prompt = f"{SYSTEM_POLICY}\n\n{plan['messages'][-1]['content']}"
        reply = llm_complete(prompt)
        guard = plan.get("number_guard")
        grounded = True
        if guard and reply:
            grounded = reading_reply_grounded(
                reply,
                guard.get("facts"),
                guard.get("message") or "",
            ) and not repeats_prior_assistant(reply, guard.get("prior_reply"))
        if reply and len(reply.strip()) > 30 and grounded:
            if DISCLAIMER not in reply:
                reply += f"\n\n{DISCLAIMER}"
            return {
                **plan["meta"],
                "reply": reply,
                "source": "llm",
            }
    except Exception:
        pass

    fallback = plan.get("fallback_reply") or "暫無法生成回答。"
    return {
        **plan["meta"],
        "reply": fallback,
        "source": plan.get("fallback_source") or "facts_fallback",
    }
