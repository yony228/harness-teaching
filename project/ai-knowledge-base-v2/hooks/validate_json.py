#!/usr/bin/env python3
"""Validate knowledge entry JSON files against the required schema.

Supports both single-file and multiple-file (glob wildcard *.json)
input modes.  Checks structural integrity, required fields, ID format,
status enumeration, URL format, content constraints, and optional field
sanity.

Usage:
    python hooks/validate_json.py <json_file> [json_file2 ...]

Exit codes:
    0  -- all files pass validation
    1  -- one or more files fail (errors and summary printed to stderr)
"""

from __future__ import annotations

import json
import re
import sys
import glob as glob_module
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES: frozenset[str] = frozenset(
    ["draft", "review", "published", "archived"],
)

ID_PATTERN: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9_-]+-\d{8}-\d{3,}$",
)

URL_PATTERN: re.Pattern[str] = re.compile(
    r"^https?://\S+$",
)

VALID_AUDIENCES: frozenset[str] = frozenset(
    ["beginner", "intermediate", "advanced"],
)

SUMMARY_MIN_LENGTH: int = 20

SCORE_MIN: int = 1
SCORE_MAX: int = 10

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def errors_for_file(path: Path, data: Any) -> list[str]:
    """Return a list of human-readable errors for *data* (parsed JSON)."""

    file_errors: list[str] = []

    if data is None:
        file_errors.append(f"[{path.name}] JSON 解析为 null")
        return file_errors

    if not isinstance(data, dict):
        file_errors.append(f"[{path.name}] JSON 根节点必须是对象 (object)")
        return file_errors

    # -- required fields ----------------------------------------
    for field_name, expected_type in REQUIRED_FIELDS.items():
        if field_name not in data:
            file_errors.append(
                f"[{path.name}] 缺少必填字段: {field_name}",
            )
        elif not isinstance(data[field_name], expected_type):
            # Special case: int is not a subclass of float in user's mind,
            # but more importantly strings aren't lists, etc.
            file_errors.append(
                f"[{path.name}] 字段 {field_name} 类型错误: "
                f"期望 {expected_type.__name__}, "
                f"实际 {type(data[field_name]).__name__}",
            )

    # -- per-field extra checks (only when the field exists + is correct type)
    if "id" in data and isinstance(data["id"], str):
        _check_id(path.name, data["id"], file_errors)

    if "status" in data and isinstance(data["status"], str):
        if data["status"] not in VALID_STATUSES:
            file_errors.append(
                f"[{path.name}] status 值无效: {data['status']!r}, "
                f"必须是 {sorted(VALID_STATUSES)} 之一",
            )

    if "source_url" in data and isinstance(data["source_url"], str):
        if not URL_PATTERN.match(data["source_url"]):
            file_errors.append(
                f"[{path.name}] source_url 格式无效: "
                f"{data['source_url']!r} (必须是 https?://...)",
            )

    if "summary" in data and isinstance(data["summary"], str):
        if len(data["summary"]) < SUMMARY_MIN_LENGTH:
            file_errors.append(
                f"[{path.name}] summary 至少 {SUMMARY_MIN_LENGTH} 字, "
                f"当前 {len(data['summary'])} 字",
            )

    if "tags" in data and isinstance(data["tags"], list):
        if len(data["tags"]) < 1:
            file_errors.append(
                f"[{path.name}] tags 至少需要 1 个标签",
            )

    # -- optional fields ----------------------------------------
    if isinstance(data.get("score"), int):
        if not (SCORE_MIN <= data["score"] <= SCORE_MAX):
            file_errors.append(
                f"[{path.name}] score 必须在 {SCORE_MIN}-{SCORE_MAX} 范围, "
                f"当前: {data['score']}",
            )

    if "audience" in data:
        if data["audience"] not in VALID_AUDIENCES:
            file_errors.append(
                f"[{path.name}] audience 值无效: {data['audience']!r}, "
                f"必须是 {sorted(VALID_AUDIENCES)} 之一",
            )

    return file_errors


def _check_id(file_label: str, value: str, container: list[str]) -> None:
    """Validate the knowledge-entry ID format."""
    if not ID_PATTERN.match(value):
        container.append(
            f"[{file_label}] ID 格式无效: {value!r}, "
            f"应为 {{source}}-{{YYYYMMDD}}-{{NNN}} "
            f"(例: github-20260317-001)",
        )


# ---------------------------------------------------------------------------
# Public API (file resolution + CLI)
# ---------------------------------------------------------------------------


def resolve_files(pattern: str) -> list[Path]:
    """Given a shell-style glob, return sorted absolute-Path objects."""
    abs_pattern = str(Path(pattern).resolve())
    matches = sorted(glob_module.glob(abs_pattern))
    result: list[Path] = []
    for m in matches:
        p = Path(m)
        if p.is_file():
            result.append(p)
    return result


def main() -> None:
    args = list(sys.argv[1:])

    if not args:
        print(
            "Usage: python hooks/validate_json.py <json_file> [json_file ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    all_files: list[Path] = []
    for arg in args:
        all_files.extend(resolve_files(arg))

    if not all_files:
        print("未找到匹配的 JSON 文件", file=sys.stderr)
        sys.exit(1)

    total_files: int = len(all_files)
    good_files: int = 0
    all_errors: list[str] = []

    for file_path in all_files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            all_errors.append(f"[{file_path.name}] 读取失败: {exc}")
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            all_errors.append(f"[{file_path.name}] JSON 解析错误: {exc}")
            continue

        errs = errors_for_file(file_path, data)
        if errs:
            all_errors.extend(errs)
        else:
            good_files += 1

    # -- summary ---------------------------------------------------
    failed = total_files - good_files
    print(f"=== 校验汇总 ===")
    print(f"  总文件数 : {total_files}")
    print(f"  通过     : {good_files}")
    print(f"  失败     : {failed}")

    if all_errors:
        print(f"\n --- 错误列表 ---")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)

    print("\n 所有文件校验通过 ✓")
    sys.exit(0)


if __name__ == "__main__":
    main()
