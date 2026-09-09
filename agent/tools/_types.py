"""Shared tool result types and exit-code semantics from existing skills."""

from __future__ import annotations

from dataclasses import dataclass

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_AGY_MISSING = 10
EXIT_CSV_MISSING = 20
EXIT_HOLDING_MISSING = 21
EXIT_NO_DATA = 20
EXIT_BAD_ARGS = 2


def message_for_exit(code: int) -> str:
    return {
        EXIT_OK: "ok",
        EXIT_FAILED: "未通過或執行失敗",
        EXIT_BAD_ARGS: "參數錯誤",
        EXIT_AGY_MISSING: "找不到 agy",
        EXIT_CSV_MISSING: "CSV 不存在",
        EXIT_HOLDING_MISSING: "找不到持股",
    }.get(code, f"exit {code}")


@dataclass
class ToolResult:
    ok: bool
    exit_code: int
    error: str | None = None
