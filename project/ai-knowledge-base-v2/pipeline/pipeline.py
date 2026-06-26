#!/usr/bin/env python3
"""Four-step automated knowledge-base pipeline.

Steps: Collect (GitHub/Search + RSS) -> Analyze (LLM) -> Organize (dedup
+standardize+validate) -> Save (knowledge/articles/).

Usage::

    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5
    python pipeline/pipeline.py --sources rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose

"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR: Path = Path(__file__).resolve().parent.parent
RAW_DIR: Path = BASE_DIR / "knowledge" / "raw"
ARTICLES_DIR: Path = BASE_DIR / "knowledge" / "articles"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("pipeline")


def _setup_logging(verbose: bool = False) -> None:
    """Configure root logger for the pipeline process."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ),
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)


# ---------------------------------------------------------------------------
# Step 1: Collect
# ---------------------------------------------------------------------------

GITHUB_SEARCH_URL: str = (
    "https://api.github.com/search/repositories"
)

DEFAULT_GITHUB_TOPICS: list[str] = [
    "artificial-intelligence",
    "machine-learning",
    "large-language-model",
    "llm",
    "generative-ai",
    "agent",
    "computer-vision",
    "nlp",
    "reinforcement-learning",
    "transformer",
    "rag",
    "vector-database",
    "code-generation",
]

RSS_FEEDS: list[str] = [
    "https://hnrss.org/front?points=50",
    "https://hnrss.org/best?points=50",
]


def collect_github(
    *,
    limit: int = 20,
    topics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Collect trending AI-related repositories from GitHub Search API.

    Uses the GitHub Search API with AI-related topics to find
    recently starred repositories.  Fetches up to *limit* results,
    sorted by stars descending.

    Args:
        limit: Maximum number of repositories to fetch (default 20).
        topics: Optional list of GitHub topics to filter by.
            Defaults to a curated list of AI/ML topics.

    Returns:
        A list of dicts with keys: ``name``, ``url``, ``summary``,
        ``stars``, ``language``, ``topics``.
    """
    if not topics:
        topics = DEFAULT_GITHUB_TOPICS

    # 用 '+' 连接（GitHub 搜索中 + 表示 OR）
    topic_query = "+".join(f"topic:{t}" for t in topics)

    all_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for single_topic in topics:
        if len(all_items) >= limit:
            break
        remaining = limit - len(all_items)

        params: dict[str, Any] = {
            "q": f"stars:>10 topic:{single_topic}",
            "sort": "stars",
            "per_page": min(30, remaining),
            "page": 1,
        }

        headers: dict[str, str] = {}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    GITHUB_SEARCH_URL,
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "GitHub采集topic=%s请求失败(status=%s): %s",
                single_topic,
                exc.response.status_code if hasattr(exc, "response") else "N/A",
                exc,
            )
            continue
        except httpx.RequestError as exc:
            logger.error("GitHub网络请求失败: %s", exc)
            continue

        data = response.json()
        items = data.get("items", [])
        if not items:
            continue

        for item in items:
            if len(all_items) >= limit:
                break
            url = item.get("html_url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            all_items.append(
                {
                    "name": (
                        f"{item.get('owner', {}).get('login', '')}/{item.get('name', '')}"
                    ),
                    "url": item.get("html_url", ""),
                    "summary": item.get("description", "无描述"),
                    "stars": item.get("stargazers_count", 0),
                    "language": (item.get("language") or "").strip(),
                    "topics": item.get("topics", []),
                },
            )

    logger.info("GitHub采集完成: %s条记录", len(all_items))
    return all_items


def _parse_rss_item(text: str) -> dict[str, str] | None:
    """Parse a single <item> from Hacker News RSS XML text.

    Minimal regex-based parser: extracts title, link, and description
    from <item> blocks.

    Args:
        text: Raw XML text of one <item> block.

    Returns:
        A dict with ``name``, ``url``, ``summary``, or None on
        parse failure.
    """
    title_m = re.search(r"<title>([^<]+)</title>", text)
    link_m = re.search(r"<link>([^<]+)</link>", text)
    desc_m = re.search(r"<description>([^<]+)</description>", text)

    title = (title_m.group(1).strip()) if title_m else ""
    link = (link_m.group(1).strip()) if link_m else ""
    desc = (desc_m.group(1).strip() if desc_m else "").strip()
    # strip HTML tags from description
    desc = re.sub(r"<[^>]+>", "", desc) if desc else ""

    if not title or not link:
        return None

    return {
        "name": title,
        "url": link,
        "summary": desc,
        "stars": 0,
        "language": "",
        "topics": [],
    }


def collect_rss(_limit: int = 20) -> list[dict[str, Any]]:
    """Collect AI-related posts from Hacker News RSS feeds.

    Hits each configured RSS feed, parses XML using lightweight
    regex extraction, and returns all items whose title or
    description contains AI/ML keywords.

    Args:
        _limit: Maximum items to return (currently unenforced on
            feed side; the feed itself limits via ?points=50).

    Returns:
        A list of dicts with keys: ``name``, ``url``, ``summary``,
        ``stars``, ``language``, ``topics``.
    """
    all_items: list[dict[str, Any]] = []
    ai_keywords = (
        "ai", "machine-learning", "llm", "agent", "gpt",
        "deep-learning", "neural", "large-language-model",
        "transform", "nlp", "cv", "computer-vision",
    )

    for feed_url in RSS_FEEDS:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(feed_url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "RSS采集失败(%s, status=%s): %s",
                feed_url,
                exc.response.status_code if hasattr(exc, "response") else "N/A",
                exc,
            )
            continue
        except httpx.RequestError as exc:
            logger.error("RSS网络请求失败(%s): %s", feed_url, exc)
            continue

        xml_text: str = response.text
        # Split by <item> tags
        blocks = re.split(r"<item\b", xml_text)

        for block in blocks:
            item_text = (
                f"<item{block}" if block.startswith("<") else block
            )
            parsed = _parse_rss_item(item_text)
            if not parsed:
                continue

            title = parsed["name"].lower()
            summary = (parsed["summary"].lower())

            if not any(kw in title or kw in summary for kw in ai_keywords):
                continue

            all_items.append(parsed)
            if len(all_items) >= _limit:
                break

        if len(all_items) >= _limit:
            break

    logger.info("RSS采集完成: %s条记录", len(all_items))
    return all_items


# ---------------------------------------------------------------------------
# Step 2: Analyze (LLM-powered)
# ---------------------------------------------------------------------------


def _build_analysis_prompt(item: dict[str, Any]) -> str:
    """Build a prompt for LLM-based analysis of a single item.

    Args:
        item: Raw collected item dict.

    Returns:
        A structured prompt string for the LLM.
    """
    return (
        f"你是AI技术知识库的智能摘要助手。请分析以下技术内容，"
        f"并严格按照JSON格式返回结果（不要使用markdown代码块包装）：\n\n"
        f"名称: {item.get('name', '')}\n"
        f"链接: {item.get('url', '')}\n"
        f"描述: {item.get('summary', '')}\n"
        f"星标: {item.get('stars', 0)}\n"
        f"语言: {item.get('language', '')}\n"
        f"标签: {item.get('topics', [])}\n\n"
        f"【评分标准（1-10）】\n"
        f"- 9-10: 突破性创新\n"
        f"- 7-8: 优秀技术分享\n"
        f"- 5-6: 普通有用信息\n"
        f"- 3-4: 内容较浅\n"
        f"- 1-2: 低质量\n\n"
        f"【可用标签】\n"
        f"agent, rag, mcp, llm, fine-tuning, prompt-engineering, multi-agent,\n"
        f"tool-use, evaluation, deployment, security, reasoning, code-generation, vision, audio\n\n"
        f"【audience】\n"
        f"可选值: beginner, intermediate, advanced\n\n"
        f"请返回以下JSON格式：\n"
        f'{{\n'
        f'"title": "中文标题（10-30字）",\n'
        f'"summary": "简短描述技术亮点（50-200字）",\n'
        f'"tags": ["从可用标签中选择"],\n'
        f'"score": 1-10的整数评分,\n'
        f'"audience": "从可选值选择一个"\n'
        f'}}'
    )


def analyze_items(
    items: list[dict[str, Any]],
    *,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Analyze collected items using LLM (via model_client).

    For each item, constructs a prompt describing the raw data,
    calls ``model_client.chat_with_retry()`` to get structured JSON
    output (title, summary, tags, score), and attaches the result.

    If ``model_client`` is not available, falls back to basic
    local enrichment.

    Args:
        items: List of raw collected items from Step 1.
        verbose: Whether to print per-item analysis logs.

    Returns:
        Enriched list of items with ``title``, ``summary``,
        ``tags``, and ``score`` fields.
    """
    _mc_parent = BASE_DIR / "pipeline"
    if str(_mc_parent) not in sys.path:
        sys.path.insert(0, str(_mc_parent))
    try:
        from model_client import create_provider, chat_with_retry  # noqa: F401
    except (ImportError, ModuleNotFoundError) as exc:
        logger.warning(
            "model_client 模块加载失败或 API Key 未配置，使用基础分析跳过。\n"
            "如需 AI 分析，请确保 model_client.py 已正确配置并设置 LLM_API_KEY。",
        )
        enriched: list[dict[str, Any]] = [
            {
                **item,
                "title": item.get("name", "未命名"),
                "summary": item.get("summary", "无摘要"),
                "tags": item.get("topics", [])[:5],
                "score": min(10, max(1, item.get("stars", 0) // 1000)),
                "audience": "intermediate",
            }
            for item in items
        ]
        return enriched

    enriched: list[dict[str, Any]] = []

    for idx, item in enumerate(items, 1):
        prompt = _build_analysis_prompt(item)

        try:
            logger.debug(
                "LLM调用 [%d/%d]: %s\n  提示: %s",
                idx,
                len(items),
                item.get("name", ""),
                prompt[:200],
            )
            result = chat_with_retry(
                prompt, verbose_logging=verbose,
            )

            # Try to parse as JSON first
            if isinstance(result, str):
                parsed = _parse_llm_json(result)
            else:
                # Extract .content from LLMResponse or similar objects
                if hasattr(result, "content"):
                    parsed = _parse_llm_json(result.content)
                else:
                    parsed = result

            logger.debug(
                "LLM返回: 类型=%s, isinstance(dict): %s, 内容=%s",
                type(result).__name__,
                isinstance(parsed, dict),
                str(result)[:300],
            )

            if isinstance(parsed, dict):
                enriched.append(
                    {
                        **item,
                        "title": parsed.get("title", item.get("name", "")),
                        "summary": parsed.get(
                            "summary", item.get("summary", ""),
                        ),
                        "tags": parsed.get(
                            "tags", item.get("topics", [])[:5],
                        ),
                        "score": parsed.get(
                            "score",
                            min(10, max(1, item.get("stars", 0) // 1000)),
                        ),
                        "audience": parsed.get("audience", "intermediate"),
                    },
                )
            else:
                enriched.append(
                    {
                        **item,
                        "title": item.get("name", "未命名"),
                        "summary": item.get("summary", "无摘要"),
                        "tags": item.get("topics", [])[:5],
                        "score": 1,
                    },
                )

            logger.debug(
                "分析完成(%d/%d): %s -> score=%s",
                idx,
                len(items),
                item.get("name", ""),
                enriched[-1].get("score"),
            )

        except Exception as exc:
            logger.error("分析失败(%s): %s", item.get("name", ""), exc)
            enriched.append(
                {
                    **item,
                    "title": item.get("name", "未命名"),
                    "summary": item.get("summary", "无摘要"),
                    "tags": item.get("topics", [])[:5],
                    "score": min(10, max(1, item.get("stars", 0) // 1000)),
                    "audience": "intermediate",
                },
            )

    logger.info("分析完成: %d/%d条", len(enriched), len(items))
    return enriched


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from LLM response text.

    Handles common LLM output patterns:
    - Plain JSON object
    - JSON wrapped in `` ```json ... ``` `` markdown block
    - JSON preceded by explanation text

    Args:
        text: Raw LLM response string.

    Returns:
        Parsed dict, or None if no valid JSON can be extracted.
    """
    text = text.strip()

    # Try extracting from markdown code block
    md_match = re.search(
        r"```(?:json|JSON)?\s*\n?(.*?)\n?```", text, re.DOTALL,
    )
    if md_match:
        json_str = md_match.group(1).strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Try parsing the full text as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try finding a JSON object within the text
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Step 3: Organize (dedup + standardize + validate)
# ---------------------------------------------------------------------------


def compute_numeric_id(date_str: str, source_type: str, index: int) -> str:
    """Generate an ID with numeric suffix matching the validator pattern.

    Args:
        date_str: Date string in YYYYMMDD format.
        source_type: Source type string (e.g. ``github_trending``).
        index: 1-based sequence number for this item.

    Returns:
        A formatted ID string like ``github-20260623-001``.
    """
    numeric_suffix = str(index).zfill(3)
    return f"{source_type[:4].lower()}-{date_str}-{numeric_suffix}"


def _generate_unique_id(
    items: list[dict[str, Any]], existing_ids: set[str],
) -> dict[str, str]:
    """Generate unique IDs for all items, avoiding conflicts.

    Uses a sequence counter to produce IDs matching the format
    ``{source}-YYYYMMDD-NNN`` (e.g. ``github-20260623-001``).

    Args:
        items: Items needing IDs.
        existing_ids: Set of already-used IDs.

    Returns:
        Mapping of item URL -> generated unique ID.
    """
    id_map: dict[str, str] = {}
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    seq: int = 1

    for item in items:
        url = item.get("url", "")
        source_type = (
            "github_trending"
            if "github" in url
            else "hacker_news"
        )

        candidate_id = compute_numeric_id(date_str, source_type, seq)
        while candidate_id in existing_ids:
            seq += 1
            candidate_id = compute_numeric_id(date_str, source_type, seq)
        id_map[url] = candidate_id
        existing_ids.add(candidate_id)
        seq += 1

    return id_map


def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate items based on URL.

    Preserves the first occurrence of each unique URL.

    Args:
        items: Collected and analyzed items.

    Returns:
        Deduplicated list of items.
    """
    seen_urls: set[str] = set()
    unique_items: list[dict[str, Any]] = []

    for item in items:
        url = item.get("url", "").strip()
        if url and url in seen_urls:
            logger.debug("跳过重复项: %s", url)
            continue
        if url:
            seen_urls.add(url)
        unique_items.append(item)

    removed = len(items) - len(unique_items)
    logger.info("去重完成: 移除%d条重复，保留%d条", removed, len(unique_items))
    return unique_items


def standardize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Standardize all items to a uniform knowledge-entry schema.

    Ensures every item has: id, title, source_type, source_url,
    summary, tags, score, status, created_at, updated_at, metadata.

    Args:
        items: Deduplicated items from previous step.

    Returns:
        Standardized items ready for saving.
    """
    # Collect existing IDs from articles directory
    existing_ids: set[str] = set()
    if ARTICLES_DIR.exists():
        for f in ARTICLES_DIR.glob("*.json"):
            try:
                entry = json.loads(f.read_text(encoding="utf-8"))
                if entry.get("id"):
                    existing_ids.add(entry["id"])
            except (json.JSONDecodeError, OSError):
                pass

    _id_map = _generate_unique_id(items, existing_ids)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source_type = (
        "github_trending"
        if items and "github" in items[0].get("url", "")
        else "hacker_news"
    )

    standardized: list[dict[str, Any]] = []

    for item in items:
        url = item.get("url", "")
        entry = {
            "id": _id_map.get(url, compute_numeric_id(
                datetime.now(timezone.utc).strftime("%Y%m%d"),
                source_type, 1,
            )),
            "title": item.get("title", item.get("name", "未命名")),
            "source_type": source_type,
            "source_url": url,
             "summary": item.get("summary", ""),
             "tags": item.get("tags", []),
             "score": item.get("score", 0),
             "status": "draft",
             "audience": item.get("audience", "intermediate"),
             "created_at": now_str,
            "updated_at": now_str,
            "publish_channels": [],
            "metadata": {
                "stars": item.get("stars", 0),
                "original_name": item.get("name", ""),
                "language": item.get("language", ""),
                "highlights": [],
            },
        }
        standardized.append(entry)

    logger.info("标准化完成: %d条", len(standardized))
    return standardized


# ---------------------------------------------------------------------------
# Step 4: Save
# ---------------------------------------------------------------------------


def save_articles(
    items: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    save_to_raw: bool = True,
) -> list[dict[str, Any]]:
    """Save knowledge articles to disk.

    Writes each item as an individual JSON file in
    ``knowledge/articles/``.  If *save_to_raw* is true, raw
    collected data is also saved in ``knowledge/raw/``.

    Args:
        items: Standardized article dicts to save.
        dry_run: If true, only print what would be saved
            without writing.
        save_to_raw: Whether to also save raw data to
            knowledge/raw/.

    Returns:
        The same list of items that were saved.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Save raw data as a batch file (always saved, even on dry-run)
    if save_to_raw and items:
        raw_data = {
            "source": "pipeline",
            "collected_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ",
            ),
            "items": [
                {
                    "name": item.get("metadata", {}).get(
                        "original_name", "",
                    ),
                    "url": item.get("source_url", ""),
                    "summary": item.get("summary", ""),
                    "stars": item.get("metadata", {}).get(
                        "stars", 0,
                    ),
                    "language": item.get("metadata", {}).get(
                        "language", "",
                    ),
                    "topics": item.get("tags", []),
                }
                for item in items
            ],
        }

        raw_file = RAW_DIR / f"pipeline-{today_str}.json"
        _save_raw_data(raw_data, raw_file, dry_run)

        raw_file = RAW_DIR / f"pipeline-{today_str}.json"
        _save_raw_data(raw_data, raw_file, dry_run)

    # Save individual articles (always save if not dry-run)
    saved_count = 0

    for item in items:
        source_prefix = (
            "github-trending"
            if item.get("source_type") == "github_trending"
            else "hacker-news"
        )
        repo_name = item.get("metadata", {}).get(
            "original_name", "",
        )
        base_filename = (
            f"{today_str}-{source_prefix}-"
            f"{repo_name.split('/')[-1].lower().replace('/', '-')}.json"
        )
        # Sanitize filename to safe chars only
        safe_filename = re.sub(r"[^\w\-\d\.]", "-", base_filename)

        if dry_run:
            logger.info(
                "[dry-run]将保存文章: knowledge/articles/%s",
                safe_filename,
            )
        else:
            article_file = ARTICLES_DIR / safe_filename

            # Avoid overwriting existing files
            if article_file.exists():
                base_num = 1
                while (
                    ARTICLES_DIR
                    / f"{safe_filename.rsplit('.json', 1)[0]}-{base_num}.json"
                ).exists():
                    base_num += 1
                safe_filename = (
                    f"{safe_filename.rsplit('.json', 1)[0]}-"
                    f"{base_num}.json"
                )
                article_file = ARTICLES_DIR / safe_filename

            score_val = item.get("score")
            if isinstance(score_val, int) and (score_val < 1 or score_val > 10):
                logger.warning(
                    "文章 %s 的 score=%s 超出 [1,10] 范围，已跳过修正。"
                    "请检查评分逻辑。",
                    safe_filename,
                    score_val,
                )
            _save_article(article_file, item)
            logger.info("文章已保存: knowledge/articles/%s", safe_filename)
            saved_count += 1

    logger.info("保存完成: %d篇文章", saved_count)
    return items


def _save_raw_data(
    raw_data: dict[str, Any],
    raw_file: Path,
    dry_run: bool,
) -> None:
    """Write raw batch data to knowledge/raw/."""
    if dry_run:
        logger.info(
            "[dry-run]将存入 %s (%d条)", raw_file, len(raw_data["items"]),
        )
    else:
        raw_file.write_text(
            json.dumps(raw_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("原始数据已保存: %s (%d条)", raw_file, len(raw_data["items"]))


def _save_article(article_file: Path, item: dict[str, Any]) -> None:
    """Write a single article to knowledge/articles/."""
    article_file.write_text(
        json.dumps(item, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


def run_pipeline(
    sources: list[str],
    limit: int,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Execute the full four-step knowledge-base pipeline.

    Args:
        sources: Source type strings (``github``, ``rss``, or both).
        limit: Maximum items to collect per source.
        dry_run: If true, skip file writes but log what would happen.
        verbose: Enable verbose/debug logging.

    Returns:
        List of finalized article dicts.
    """
    sep = "=" * 60
    print(sep)
    logger.info("%s", "知识库流水线启动")
    logger.info(
        "  模式: %s | 上限: %d | 干跑: %s",
        ", ".join(sources), limit, dry_run,
    )
    print(sep)

    # --- Step 1: Collect ---
    all_raw_items: list[dict[str, Any]] = []

    for source in sources:
        source_lower = source.lower().strip()
        if source_lower == "github":
            all_raw_items.extend(collect_github(limit=limit))
        elif source_lower == "rss":
            all_raw_items.extend(collect_rss(_limit=limit))
        else:
            logger.warning("未知数据源'%s'，已跳过", source)

    if not all_raw_items:
        logger.warning("%s", "未发现任何内容可以处理")
        return []

    logger.info("Step 1 采集完成: %d条", len(all_raw_items))

    # --- Step 2: Analyze ---
    analyzed = analyze_items(all_raw_items, verbose=verbose)
    logger.info("Step 2 分析完成: %d条", len(analyzed))

    # --- Step 3: Organize (dedup + standardize) ---
    unique = deduplicate(analyzed)
    standardized = standardize(unique)
    logger.info("Step 3 整理完成: %d条", len(standardized))

    # --- Step 4: Save ---
    save_articles(standardized, dry_run=dry_run)

    logger.info("=" * 60)
    logger.info("流水线完成: 总计%d篇文章", len(standardized))
    logger.info("=" * 60)

    return standardized


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description=(
            "AI知识库自动化流水线: "
            "采集(GitHub+RSS) -> 分析(LLM) -> 整理 -> 保存"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  %(prog)s --sources github,rss --limit 20\n"
            "  %(prog)s --sources github --limit 5\n"
            "  %(prog)s --sources rss --limit 10 --dry-run\n"
            "  %(prog)s --verbose\n"
        ),
    )

    parser.add_argument(
        "--sources",
        type=str,
        default="github,rss",
        help=(
            "Comma-separated source types. "
            "Available: github, rss. "
            "Default: github,rss"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum items to collect per source. Default: 20",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate the pipeline without writing files",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,  
        help="Enable verbose (debug) logging",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> list[dict[str, Any]]:
    """CLI entry point for the pipeline.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        The list of finalized article dicts for programmatic use.
    """
    args = parse_args(argv)

    _setup_logging(verbose=args.verbose)

    sources = [
        s.strip().lower() for s in args.sources.split(",") if s.strip()
    ]
    if not sources:
        logger.error("%s", "未指定有效数据源(github/rss)")
        sys.exit(1)

    if args.limit < 1:
        logger.error("%s", "--limit必须为正整数")
        sys.exit(1)

    try:
        result = run_pipeline(
            sources=sources,
            limit=args.limit,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

        # Print summary
        logger.info("%s", "=== 流水线总览 ===")
        logger.info("  来源:   %s", ", ".join(sources))
        logger.info("  成品:   %s篇文章", len(result))
        logger.info("  干跑模式: %s", args.dry_run)

        return result

    except KeyboardInterrupt:
        logger.warning("%s", "流水线被用户中断")
        sys.exit(130)
    except Exception as exc:
        logger.critical("流水线执行失败: %s", exc, exc_info=True)
        sys.exit(1)


# Don't run main() when imported
if __name__ == "__main__":
    main()
