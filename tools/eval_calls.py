#!/usr/bin/env python3
"""Score report-day chip calls against realized 5-day excess, and gate stability.

The directional call is the chip regime frozen in that day's ``facts.json``
(the stance the narrative is required to follow):

- ``accumulation`` → 偏多
- ``distribution`` → 偏空
- anything else → 中性（不表態，不進準確率）

A resolved horizon counts as a directional move only when excess return vs
TAIEX is strictly outside ±``EPSILON_PCT`` (same band as ``calibrate_thresholds``).
偏多 then needs positive excess; 偏空 needs negative excess. The opposite is a
judgment failure. Format / fact / reasoning misses that remain after the last
gate round are writing failures. agy or CSV exits (10 / 20), or a gate that
never logged a round, are operational failures.

Accuracy and stability are separate. Changing the prompt moves stability
(round-1 pass rate, rounds). Changing how ``chip_regime`` is labeled, or which
regimes stay 中性, is what moves accuracy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Keep aligned with tools/calibrate_thresholds.py EPSILON_PCT.
EPSILON_PCT = 0.5
DEFAULT_HORIZON = "5d"
EXIT_AGY_MISSING = 10
EXIT_CSV_MISSING = 20

_FACTS_RE = re.compile(r"^tw_stock_(\d+)\.facts\.json$")
_LOG_RE = re.compile(r"^tw_stock_(\d+)\.gate\.log$")
_MD_RE = re.compile(r"^tw_stock_(\d+)\.md$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_CALL_FROM_REGIME = {
    "accumulation": "偏多",
    "distribution": "偏空",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def direction_call(chip_regime: str | None) -> str:
    """Map the same-day chip regime to the call the report must not reverse."""
    return _CALL_FROM_REGIME.get(str(chip_regime or ""), "中性")


def score_direction(call: str, excess: float | None, *, epsilon: float = EPSILON_PCT) -> str:
    """Return hit, miss, flat, abstain, or pending.

    中性 abstains. Unresolved excess is pending. Excess inside ±epsilon has no
    direction, so a directional call is flat rather than a hit or a miss.
    """
    if call not in ("偏多", "偏空"):
        return "abstain"
    if excess is None:
        return "pending"
    if abs(excess) <= epsilon:
        return "flat"
    if call == "偏多":
        return "hit" if excess > epsilon else "miss"
    return "hit" if excess < -epsilon else "miss"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _trade_date_from_path(path: Path, facts: dict[str, Any] | None) -> str:
    if facts:
        raw = str(facts.get("trade_date") or "").strip()
        if raw:
            return raw
    for parent in path.parents:
        if _DATE_RE.fullmatch(parent.name):
            return parent.name
    return ""


def _latest_run(log_path: Path | None) -> list[dict[str, Any]]:
    if log_path is None or not log_path.is_file():
        return []
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import gate_stats

    runs = gate_stats.segment_runs(gate_stats.read_entries(log_path))
    return runs[-1] if runs else []


def _agy_exit(run: list[dict[str, Any]]) -> int | None:
    for entry in run:
        code = entry.get("agy_exit_code")
        if isinstance(code, int) and code != 0:
            return code
    return None


def _load_outcomes(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    store: dict[tuple[str, str], dict[str, Any]] = {}
    for record in _read_jsonl(path):
        stock_id = str(record.get("stock_id") or "")
        trade_date = str(record.get("trade_date") or "")
        if stock_id and trade_date:
            store[(stock_id, trade_date)] = record
    return store


def _excess(record: dict[str, Any] | None, horizon: str) -> float | None:
    if not record:
        return None
    horizons = record.get("horizons")
    if not isinstance(horizons, dict):
        return None
    slot = horizons.get(horizon)
    if not isinstance(slot, dict) or slot.get("status") != "resolved":
        return None
    value = slot.get("excess_return_pct")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scan_report_dirs(stock_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Index report-gate artifacts. Position-gate files are skipped."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    if not stock_root.is_dir():
        return found
    for path in stock_root.rglob("tw_stock_*"):
        if not path.is_file():
            continue
        facts_match = _FACTS_RE.match(path.name)
        log_match = _LOG_RE.match(path.name)
        md_match = _MD_RE.match(path.name)
        match = facts_match or log_match or md_match
        if match is None:
            continue
        stock_id = match.group(1)
        facts = _read_json(path) if facts_match else None
        trade_date = _trade_date_from_path(path, facts)
        if not trade_date:
            continue
        slot = found.setdefault(
            (stock_id, trade_date),
            {"facts_path": None, "log_path": None, "md_path": None, "facts": None},
        )
        if facts_match:
            slot["facts_path"] = path
            slot["facts"] = facts
        elif log_match:
            slot["log_path"] = path
        else:
            slot["md_path"] = path
    return found


def _operational_from_audit(agent_root: Path) -> dict[tuple[str, str], str]:
    """Map (stock, date) → reason for report-gate exits 10 and 20."""
    reasons: dict[tuple[str, str], str] = {}
    if not agent_root.is_dir():
        return reasons
    labels = {
        EXIT_AGY_MISSING: "exit_10_agy_missing",
        EXIT_CSV_MISSING: "exit_20_csv_missing",
    }
    for path in sorted(agent_root.rglob("run_*.jsonl")):
        trade_date = path.parent.name if _DATE_RE.fullmatch(path.parent.name) else ""
        for record in _read_jsonl(path):
            if record.get("tool") != "run_report_gate" or record.get("skipped"):
                continue
            code = record.get("exit_code")
            if code not in labels:
                continue
            stock_id = str(record.get("stock_id") or "")
            if not stock_id:
                args = record.get("args")
                if isinstance(args, dict) and args.get("stock_id"):
                    stock_id = str(args["stock_id"])
            day = trade_date or str(record.get("trade_date") or "")
            if not stock_id or not day:
                continue
            reasons[(stock_id, day)] = labels[int(code)]
    return reasons


def _case(
    *,
    stock_id: str,
    trade_date: str,
    facts: dict[str, Any] | None,
    run: list[dict[str, Any]],
    md_exists: bool,
    excess: float | None,
    audit_reason: str | None,
    epsilon: float,
) -> dict[str, Any]:
    regime = None
    if facts and facts.get("chip_regime") not in (None, "", "unknown"):
        regime = str(facts["chip_regime"])
    elif facts and facts.get("chip_regime") == "unknown":
        regime = "unknown"
    call = direction_call(regime if regime != "unknown" else None)
    if regime == "unknown":
        call = "中性"
    verdict = score_direction(call, excess, epsilon=epsilon)

    final_passed: bool | None
    round1_passed: bool | None
    if run:
        final_passed = bool(run[-1].get("passed"))
        round1_passed = bool(run[0].get("passed"))
    elif md_exists:
        final_passed = True
        round1_passed = None
    else:
        final_passed = None
        round1_passed = None

    last_codes: list[str] = []
    if run:
        raw_codes = run[-1].get("issue_codes") or []
        if isinstance(raw_codes, list):
            last_codes = [str(code) for code in raw_codes]

    agy_code = _agy_exit(run)
    recovered = final_passed is True
    operational_reason = None
    if not recovered:
        if agy_code is not None:
            operational_reason = f"agy_exit_{agy_code}"
        elif audit_reason:
            operational_reason = audit_reason
        elif not run and facts is not None and not md_exists:
            operational_reason = "gate_log_missing"

    failures: list[str] = []
    if operational_reason:
        failures.append("operational")
    if final_passed is False and (last_codes or agy_code is None):
        failures.append("writing")
    if verdict == "miss":
        failures.append("judgment")

    return {
        "stock_id": stock_id,
        "trade_date": trade_date,
        "chip_regime": regime,
        "call": call,
        "excess_return_pct": excess,
        "verdict": verdict,
        "failures": failures,
        "round1_passed": round1_passed,
        "final_passed": final_passed,
        "rounds": len(run),
        "issue_codes": last_codes,
        "operational_reason": operational_reason,
    }


def evaluate(
    root: Path,
    *,
    trade_date: str | None = None,
    stock: str | None = None,
    horizon: str = DEFAULT_HORIZON,
    epsilon: float = EPSILON_PCT,
) -> dict[str, Any]:
    """Join report artifacts, outcomes, and audit into per-report cases."""
    indexed = _scan_report_dirs(root / "reports" / "stock")
    outcomes = _load_outcomes(root / "reports" / "outcomes" / "outcomes.jsonl")
    audit = _operational_from_audit(root / "reports" / "agent")

    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for (stock_id, day), slot in sorted(indexed.items()):
        # facts.json is also written by fetch and position-gate. A report-gate
        # attempt is a narrative log, a report markdown, or an audit exit.
        if slot.get("log_path") is None and slot.get("md_path") is None:
            continue
        if trade_date and day != trade_date:
            continue
        if stock and stock_id != stock:
            continue
        seen.add((stock_id, day))
        facts = slot.get("facts")
        run = _latest_run(slot.get("log_path"))
        cases.append(
            _case(
                stock_id=stock_id,
                trade_date=day,
                facts=facts if isinstance(facts, dict) else None,
                run=run,
                md_exists=slot.get("md_path") is not None,
                excess=_excess(outcomes.get((stock_id, day)), horizon),
                audit_reason=audit.get((stock_id, day)),
                epsilon=epsilon,
            )
        )

    for (stock_id, day), reason in sorted(audit.items()):
        if (stock_id, day) in seen:
            continue
        if trade_date and day != trade_date:
            continue
        if stock and stock_id != stock:
            continue
        cases.append(
            _case(
                stock_id=stock_id,
                trade_date=day,
                facts=None,
                run=[],
                md_exists=False,
                excess=_excess(outcomes.get((stock_id, day)), horizon),
                audit_reason=reason,
                epsilon=epsilon,
            )
        )

    return summarize(cases, horizon=horizon, epsilon=epsilon, trade_date=trade_date, stock=stock)


def summarize(
    cases: list[dict[str, Any]],
    *,
    horizon: str,
    epsilon: float,
    trade_date: str | None,
    stock: str | None,
) -> dict[str, Any]:
    verdicts: Counter[str] = Counter(case["verdict"] for case in cases)
    hits = verdicts["hit"]
    misses = verdicts["miss"]
    scored = hits + misses
    failure_counts: Counter[str] = Counter()
    for case in cases:
        failure_counts.update(case["failures"])

    stability_cases = [case for case in cases if case["rounds"] > 0]
    stability_n = len(stability_cases)
    round1 = sum(1 for case in stability_cases if case["round1_passed"])
    final = sum(1 for case in stability_cases if case["final_passed"])
    total_rounds = sum(int(case["rounds"]) for case in stability_cases)

    by_regime: dict[str, Counter[str]] = {}
    for case in cases:
        key = str(case.get("chip_regime") or "未記錄")
        by_regime.setdefault(key, Counter())[case["verdict"]] += 1

    return {
        "scope": {"date": trade_date, "stock": stock, "horizon": horizon},
        "epsilon_pct": epsilon,
        "cases": len(cases),
        "accuracy": {
            "hits": hits,
            "misses": misses,
            "scored": scored,
            "accuracy": round(hits / scored, 3) if scored else None,
            "abstain": verdicts["abstain"],
            "flat": verdicts["flat"],
            "pending": verdicts["pending"],
        },
        "stability": {
            "runs": stability_n,
            "round1_pass": round1,
            "final_pass": final,
            "round1_pass_rate": round(round1 / stability_n, 3) if stability_n else None,
            "final_pass_rate": round(final / stability_n, 3) if stability_n else None,
            "avg_rounds": round(total_rounds / stability_n, 2) if stability_n else None,
        },
        "failure_counts": {
            "operational": failure_counts["operational"],
            "writing": failure_counts["writing"],
            "judgment": failure_counts["judgment"],
        },
        "by_regime": {
            key: dict(counts) for key, counts in sorted(by_regime.items())
        },
        "rows": cases,
    }


def format_report(payload: dict[str, Any]) -> str:
    scope = payload["scope"]
    bits = []
    if scope.get("date"):
        bits.append(f"date={scope['date']}")
    if scope.get("stock"):
        bits.append(f"stock={scope['stock']}")
    bits.append(f"horizon={scope.get('horizon')}")
    accuracy = payload["accuracy"]
    stability = payload["stability"]
    failures = payload["failure_counts"]
    lines = [
        f"# 報告傾向對帳（{'，'.join(bits)}）",
        "",
        "傾向取自當天 facts.json 的 chip_regime（報告不得寫反）。"
        "accumulation＝偏多，distribution＝偏空，其餘＝中性、不進準確率。",
        f"方向門檻：相對大盤超額絕對值須大於 {payload['epsilon_pct']}%。",
        "",
        "## 準確性",
        _accuracy_line(accuracy),
        f"- 未納入分母：中性 {accuracy['abstain']}、"
        f"超額未過門檻 {accuracy['flat']}、"
        f"5 日尚未成熟 {accuracy['pending']}",
        "",
        "## 穩定性",
        _stability_line(stability),
        "",
        "## 失敗件數",
        f"- 作業（agy／CSV 沒跑完，或 gate 沒有輪次紀錄）：{failures['operational']}",
        f"- 寫作（最後一輪仍未通過）：{failures['writing']}",
        f"- 判斷（偏多／偏空與超額方向相反）：{failures['judgment']}",
    ]
    regime_lines = _regime_lines(payload.get("by_regime") or {})
    if regime_lines:
        lines.extend(["", "## 依籌碼型態", *regime_lines])
    samples = _sample_lines(payload.get("rows") or [])
    if samples:
        lines.extend(["", "## 失敗案例", *samples])
    lines.extend(
        [
            "",
            "## 怎麼比較前後",
            "- 先另存這次的 summary，改完再用同一批日期重跑，比兩個數字。",
            "- 準確性看 accuracy：只含偏多／偏空且超額已過門檻的報告。"
            "改 prompt 不會動這個數字；要動它，得改 chip_regime 怎麼歸類，或讓不清楚的狀態維持中性。",
            "- 穩定性看 round1_pass_rate 與 avg_rounds。"
            "最終通過率高、第一輪通過率低，代表靠重試才過。",
            "- 5 日還沒成熟的列是 pending，不要把它算進準確率。",
        ]
    )
    return "\n".join(lines) + "\n"


def _accuracy_line(accuracy: dict[str, Any]) -> str:
    if not accuracy["scored"]:
        return "- 準確率：尚無（沒有已成熟、且有方向的判斷）"
    rate = accuracy["accuracy"]
    pct = f"{rate:.0%}" if isinstance(rate, float) else "—"
    return (
        f"- 準確率：{pct}"
        f"（命中 {accuracy['hits']} / 相反 {accuracy['misses']}，"
        f"分母 {accuracy['scored']}）"
    )


def _stability_line(stability: dict[str, Any]) -> str:
    if not stability["runs"]:
        return "- 尚無 gate 輪次（有報告但沒有 .gate.log 時不計入）"
    r1 = stability["round1_pass_rate"]
    final = stability["final_pass_rate"]
    return (
        f"- 有輪次的報告：{stability['runs']}\n"
        f"- 第一輪通過率：{r1:.0%}（{stability['round1_pass']}/{stability['runs']}）\n"
        f"- 最終通過率：{final:.0%}（{stability['final_pass']}/{stability['runs']}）\n"
        f"- 平均輪數：{stability['avg_rounds']:.2f}"
    )


def _regime_lines(by_regime: dict[str, dict[str, int]]) -> list[str]:
    lines: list[str] = []
    for regime, counts in by_regime.items():
        hit = int(counts.get("hit") or 0)
        miss = int(counts.get("miss") or 0)
        if hit + miss == 0:
            continue
        lines.append(f"- {regime}：命中 {hit}、相反 {miss}")
    return lines


def _sample_lines(rows: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    failed = [row for row in rows if row.get("failures")]
    lines: list[str] = []
    for row in failed[:limit]:
        kinds = "、".join(row["failures"])
        extra = ""
        if row.get("excess_return_pct") is not None and "judgment" in row["failures"]:
            extra = f"，5日超額 {row['excess_return_pct']:+.2f}%"
        reason = row.get("operational_reason")
        if reason and "operational" in row["failures"]:
            extra += f"，{reason}"
        codes = row.get("issue_codes") or []
        if codes and "writing" in row["failures"]:
            extra += "，" + ",".join(codes[:4])
        lines.append(
            f"- {row['trade_date']} {row['stock_id']} {row['call']}：{kinds}{extra}"
        )
    remaining = len(failed) - len(lines)
    if remaining > 0:
        lines.append(f"- …另有 {remaining} 筆，見 call_eval.jsonl")
    return lines


def write_outputs(root: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = root / "reports" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "call_eval.jsonl"
    summary_path = out_dir / "call_eval_summary.json"
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in payload["rows"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {key: value for key, value in payload.items() if key != "rows"}
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return rows_path, summary_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把報告當天的籌碼傾向對上已實現超額，並分開統計準確性與穩定性",
    )
    parser.add_argument("--date", help="只看某交易日，例如 2026-08-18")
    parser.add_argument("--stock", help="只看某股票代碼，例如 2330")
    parser.add_argument(
        "--horizon",
        default=DEFAULT_HORIZON,
        help="outcomes 裡的 horizon 欄位，預設 5d",
    )
    parser.add_argument("--json", action="store_true", help="只印 summary JSON")
    parser.add_argument("--no-write", action="store_true", help="不寫 reports/eval/")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="專案根目錄，預設為本 repo",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    args = build_parser().parse_args(argv)
    root = args.root or project_root()
    payload = evaluate(
        root,
        trade_date=args.date,
        stock=args.stock,
        horizon=args.horizon,
    )
    if args.json:
        summary = {key: value for key, value in payload.items() if key != "rows"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(format_report(payload), end="")
    if not args.no_write:
        rows_path, summary_path = write_outputs(root, payload)
        print(f"cases: {rows_path}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
    if payload["cases"] == 0:
        print(
            "找不到 report-gate 產物（tw_stock_*.gate.log 或 tw_stock_*.md）。"
            "只抓籌碼寫出的 facts.json 不算一次報告。5 日超額要先跑 outcome-label。",
            file=sys.stderr,
        )
    elif payload["accuracy"]["pending"] and not payload["accuracy"]["scored"]:
        print(
            "方向判斷都還沒成熟。先跑 outcome-label，等 5 個交易日的價格進了 outcomes 再重跑。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
