#!/usr/bin/env python3
"""ai knowledge entry quality checker with 5-dimension scoring."""

import json
import pathlib
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STANDARD_TAGS: frozenset[str] = frozenset({
    "llm", "agent", "multimodal", "computer-vision", "nlp",
    "rust", "python", "go", "typescript", "web", "mobile",
    "devops", "cloud", "data-science", "cybersecurity",
    "openai", "anthropic", "deepmind", "meta", "google",
    "github", "docker", "kubernetes", "git",
    "large-language-model", "reinforcement-learning",
    "transformer", "diffusion-model", "reinforcement-learning",
    "few-shot", "fine-tuning", "rag", "vector-database",
    "code-generation", "text-to-image", "speech-to-text",
    "open-source", "api", "cli-tool",
})

CHINESE_BLANK_WORDS: frozenset[str] = frozenset({
    "赋能", "抓手", "闭环", "打通", "全链路", "底层逻辑",
    "颗粒度", "对齐", "拉通", "沉淀", "强大的", "革命性的",
})

ENGLISH_BLANK_WORDS = frozenset({
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "robust", "seamless", "pioneering",
    "paradigm-shifting", "state-of-the-art",
})

TECH_KEYWORDS = frozenset({
    "model", "training", "inference", "api", "code", "ml",
    "algorithm", "neural", "data", "dataset", "agent",
    "llm", "nlp", "cv", "transformer", "fine-tuning",
    "api", "sdk", "library", "framework", "pipeline",
})

MAX_SCORES: dict[str, int] = {
    "summary_quality": 25,
    "technical_depth": 25,
    "format_compliance": 20,
    "tag_accuracy": 15,
    "no_blank_words": 15,
}


class Grade(Enum):
    A = "A"
    B = "B"
    C = "C"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: float
    max_score: int
    grade: Grade
    details: str = ""


@dataclass(frozen=True)
class QualityReport:
    file_path: str
    total_score: float
    max_score: int  # 100
    grade: Grade
    dimensions: list[DimensionScore] = field(default_factory=list)
    is_valid: bool = True  # entry not missing critical fields


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _check_summary_quality(entry: dict[str, Any]) -> DimensionScore:
    summary = entry.get("summary", "") or ""
    text = summary.strip()

    if not text:
        return DimensionScore(
            name="summary_quality",
            score=0.0,
            max_score=MAX_SCORES["summary_quality"],
            grade=Grade.C,
            details="summary 缺失或为空",
        )

    char_count = len(text)
    technical_matches = sum(
        1 for kw in TECH_KEYWORDS if kw.lower() in text.lower()
    )

    if char_count >= 50 and technical_matches > 0:
        score = float(MAX_SCORES["summary_quality"])
        grade = Grade.A
    elif char_count >= 50:
        score = 22.0
        grade = Grade.B
    elif char_count >= 20:
        score = 14.0
        grade = Grade.C
    else:
        score = 5.0
        grade = Grade.C

    technical_bonus = 0
    if technical_matches == 0:
        technical_bonus = 0
    else:
        technical_bonus = min(technical_matches, 3)

    score = min(score + technical_bonus * 1.0, float(MAX_SCORES["summary_quality"]))
    if score >= 80 / 100 * MAX_SCORES["summary_quality"]:
        grade = Grade.A
    elif score >= 60 / 100 * MAX_SCORES["summary_quality"]:
        grade = Grade.B
    else:
        grade = Grade.C

    details = (
        f"字数: {char_count} | 技术关键词: {technical_matches}个 "
        f"(加分: {technical_bonus * 1.0:.0f})"
    )
    return DimensionScore(
        name="summary_quality",
        score=score,
        max_score=MAX_SCORES["summary_quality"],
        grade=grade,
        details=details,
    )


def _check_technical_depth(entry: dict[str, Any]) -> DimensionScore:
    raw_score = entry.get("score")
    max_s = MAX_SCORES["technical_depth"]

    if raw_score is None:
        return DimensionScore(
            name="technical_depth",
            score=0.0,
            max_score=max_s,
            grade=Grade.C,
            details="score 字段缺失",
        )

    try:
        numeric = float(raw_score)
    except (TypeError, ValueError):
        return DimensionScore(
            name="technical_depth",
            score=0.0,
            max_score=max_s,
            grade=Grade.C,
            details=f"score 值无效: {raw_score}",
        )

    clamped = max(1.0, min(10.0, numeric))
    score = (clamped / 10.0) * max_s
    score = round(score, 1)

    ratio = score / max_s
    if ratio >= 0.8:
        grade = Grade.A
    elif ratio >= 0.6:
        grade = Grade.B
    else:
        grade = Grade.C

    return DimensionScore(
        name="technical_depth",
        score=score,
        max_score=max_s,
        grade=grade,
        details=f"score: {raw_score}/10 → {score:.1f}/{max_s}",
    )


def _check_format_compliance(entry: dict[str, Any]) -> DimensionScore:
    max_s = MAX_SCORES["format_compliance"]
    scored_fields: list[str] = []

    checks: dict[str, Any] = {
        "id": bool(entry.get("id")) and len(str(entry["id"]).strip()) > 0,
        "title": bool(entry.get("title")) and len(str(entry["title"]).strip()) > 0,
        "source_url": bool(entry.get("source_url")) and _is_valid_url(str(entry.get("source_url", ""))),
        "status": bool(entry.get("status")) and entry["status"] in (
            "draft", "review", "published", "archived",
        ),
        "timestamps": _has_timestamps(entry),
    }

    points_each = max_s / len(checks)  # 4
    total = 0.0

    for field_name, passed in checks.items():
        if passed:
            scored_fields.append(field_name)
        else:
            total += 0.0
        total += points_each if passed else 0.0

    total = round(total, 1)
    total = min(total, float(max_s))

    ratio = total / max_s
    if ratio >= 0.8:
        grade = Grade.A
    elif ratio >= 0.6:
        grade = Grade.B
    else:
        grade = Grade.C

    missing = [k for k, v in checks.items() if not v]
    details = (
        f"通过: {', '.join(scored_fields) if scored_fields else '无'} "
        f"| 缺失: {', '.join(missing) if missing else '无'}"
    )
    return DimensionScore(
        name="format_compliance",
        score=total,
        max_score=max_s,
        grade=grade,
        details=details,
    )


def _check_tag_accuracy(entry: dict[str, Any]) -> DimensionScore:
    max_s = MAX_SCORES["tag_accuracy"]
    raw_tags = entry.get("tags")

    if not raw_tags or not isinstance(raw_tags, list):
        return DimensionScore(
            name="tag_accuracy",
            score=0.0,
            max_score=max_s,
            grade=Grade.C,
            details="tags 缺失或不是列表",
        )

    tag_count = len(raw_tags)
    valid_tags = [t for t in raw_tags if isinstance(t, str)]
    match_count = sum(1 for t in valid_tags if t.lower() in STANDARD_TAGS)

    issue = ""

    if tag_count == 0 or len(valid_tags) == 0:
        score = 0.0
        grade = Grade.C
        issue = "无有效标签"
    elif tag_count > 5:
        score = 5.0
        grade = Grade.C
        issue = f"标签过多({tag_count}), 超过5个扣分"
    elif match_count == tag_count and 1 <= tag_count <= 3:
        score = float(max_s)
        grade = Grade.A
        issue = f"所有{tag_count}个标签均为标准标签"
    elif 1 <= tag_count <= 3 and match_count > 0:
        score = 10.0
        grade = Grade.B
        issue = f"{match_count}/{tag_count}为标准标签"
    elif match_count > 2 and tag_count <= 5:
        score = 12.0
        grade = Grade.B
        issue = f"{match_count}/{tag_count}为标准标签"
    else:
        score = max(2.0, match_count * 2.0)
        grade = Grade.C
        issue = f"{match_count}/{tag_count}为标准标签(标签数={tag_count})"

    score = round(score, 1)
    score = min(score, float(max_s))

    details = (
        f"标签数: {tag_count} | 标准匹配: {match_count} | {issue}"
    )
    return DimensionScore(
        name="tag_accuracy",
        score=score,
        max_score=max_s,
        grade=grade,
        details=details,
    )


def _check_no_blank_words(entry: dict[str, Any]) -> DimensionScore:
    max_s = MAX_SCORES["no_blank_words"]
    text_fields = " ".join(str(v) for v in entry.values() if isinstance(v, str))

    if not text_fields.strip():
        return DimensionScore(
            name="no_blank_words",
            score=float(max_s),
            max_score=max_s,
            grade=Grade.A,
            details="无可检测文本",
        )

    text_lower = text_fields.lower()

    zh_found = set(CHINESE_BLANK_WORDS) & set(text_fields)
    # more precise search
    zh_found = set()
    for word in CHINESE_BLANK_WORDS:
        if word in text_fields:
            zh_found.add(word)

    en_found = set()
    for word in ENGLISH_BLANK_WORDS:
        pattern = re.compile(rf"\b{re.escape(word.lower())}\b", re.IGNORECASE)
        if pattern.search(text_fields):
            en_found.add(word)

    total_found = zh_found | {w.lower() for w in en_found}

    if not total_found:
        return DimensionScore(
            name="no_blank_words",
            score=float(max_s),
            max_score=max_s,
            grade=Grade.A,
            details="未发现空洞词",
        )

    penalty = min(len(total_found) * 3.0, float(max_s))
    score = round(max_s - penalty, 1)

    details = (
        f"中文: {', '.join(sorted(zh_found)) if zh_found else '无'} | "
        f"英文: {', '.join(sorted({w.lower() for w in en_found})) if en_found else '无'}"
    )

    if score >= 0.8 * max_s:
        grade = Grade.A
    elif score >= 0.6 * max_s:
        grade = Grade.B
    else:
        grade = Grade.C

    return DimensionScore(
        name="no_blank_words",
        score=score,
        max_score=max_s,
        grade=grade,
        details=details,
    )


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _is_valid_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _has_timestamps(entry: dict[str, Any]) -> bool:
    return "created_at" in entry and "updated_at" in entry


def _determine_grade(total_score: float, max_score: int) -> Grade:
    ratio = total_score / max_score
    if ratio >= 0.8:
        return Grade.A
    elif ratio >= 0.6:
        return Grade.B
    else:
        return Grade.C


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------


def _progress_bar(current: int, total: int, width: int = 20) -> str:
    ratio = current / max(total, 1)
    filled = int(width * ratio)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {current}/{total}"


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def process_entry(entry: dict[str, Any], file_path: str) -> QualityReport:
    dimensions: list[DimensionScore] = [
        _check_summary_quality(entry),
        _check_technical_depth(entry),
        _check_format_compliance(entry),
        _check_tag_accuracy(entry),
        _check_no_blank_words(entry),
    ]

    total = round(sum(d.score for d in dimensions), 1)
    max_total = sum(MAX_SCORES.values())

    grade = _determine_grade(total, max_total)

    format_dim = dimensions[2]
    if format_dim.details and "缺失" in format_dim.details:
        is_valid = False
    if not entry.get("id") and not entry.get("title"):
        is_valid = False

    return QualityReport(
        file_path=file_path,
        total_score=total,
        max_score=max_total,
        grade=grade,
        dimensions=dimensions,
        is_valid=is_valid,
    )


def display_report(report: QualityReport) -> None:
    print("\n" + "=" * 60)
    print(f"  文件: {report.file_path}")
    print("=" * 60)

    for dim in report.dimensions:
        bar_len = 15
        filled = int(bar_len * (dim.score / dim.max_score)) if dim.max_score > 0 else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        print(
            f"  {dim.name:20s} [{bar}] {dim.score:5.1f}/{dim.max_score:3d}  {dim.grade.value}  | {dim.details}"
        )

    ratio = report.total_score / report.max_score
    ratio_pct = ratio * 100
    print("-" * 60)
    print(
        f"  总分: {report.total_score:5.1f}/{report.max_score} "
        f"({ratio_pct:.0f}%)  评级: {report.grade.value} "
        f"{'[pass]' if report.grade != Grade.C else '[fail]'}"
    )
    print("-" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def resolve_files(patterns: list[str]) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for p in patterns:
        path = pathlib.Path(p)
        if path.is_file():
            files.append(path)
        elif "*" in str(path):
            matched = sorted(path.glob(path.name))
            files.extend(matched)
    return files


def generate_reports(files: list[pathlib.Path]) -> list[QualityReport]:
    reports: list[QualityReport] = []
    total = len(files)

    for idx, file_path in enumerate(files, 1):
        bar = _progress_bar(idx, total)
        print(f"{bar} 处理 {file_path.name}...", end="\r", flush=True)

        try:
            text = file_path.read_text(encoding="utf-8")
            entry = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"\n  [跳过] {file_path.name}: JSON 解析错误 - {e}")
            continue
        except OSError as e:
            print(f"\n  [跳过] {file_path.name}: 读取失败 - {e}")
            continue

        report = process_entry(entry, str(file_path))
        reports.append(report)

    print()  # newline after progress bar
    return reports


def exit_code_from_reports(reports: list[QualityReport]) -> int:
    for report in reports:
        if report.grade == Grade.C:
            return 1
    return 0


def main() -> None:
    patterns = sys.argv[1:] if len(sys.argv) > 1 else ["knowledge/**/*.json"]

    if len(patterns) == 0:
        print("用法: python check_quality.py [file.json ... | knowledge/**/*.json]")
        sys.exit(0)

    files = resolve_files(patterns)

    if not files:
        print("错误: 未找到匹配的文件")
        sys.exit(1)

    print(f"\n  找到 {len(files)} 个文件开始质量检查...\n")
    reports = generate_reports(files)

    if not reports:
        print("\n结果: 无有效报告")
        sys.exit(0)

    for report in reports:
        display_report(report)

    c_count = sum(1 for r in reports if r.grade == Grade.C)
    b_count = sum(1 for r in reports if r.grade == Grade.B)
    a_count = sum(1 for r in reports if r.grade == Grade.A)

    print("\n" + "-" * 50)
    print(f"  总结: {a_count} A级 | {b_count} B级 | {c_count} C级 | "
          f"共 {len(reports)} 文件")
    print("-" * 50)

    sys.exit(exit_code_from_reports(reports))


if __name__ == "__main__":
    main()
