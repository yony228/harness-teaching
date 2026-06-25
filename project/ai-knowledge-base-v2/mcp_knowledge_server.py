#!/usr/bin/env python3
"""AI Knowledge Base MCP Server.

Provides MCP (Model Context Protocol) tools for searching and querying
the local knowledge base stored in knowledge/articles/ as JSON files.

Supports JSON-RPC 2.0 over stdio with the standard MCP methods:
- initialize
- tools/list
- tools/call (search_articles, get_article, knowledge_stats)

Usage:
    python mcp_knowledge_server.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_knowledge_server")

ARTICLES_DIR = Path(__file__).resolve().parent / "knowledge" / "articles"


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 helpers
# ---------------------------------------------------------------------------

def _make_response(id: int | str | None, result: Any) -> dict[str, Any]:
    """Build a successful JSON-RPC response object."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _make_error(
    id: int | str | None,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """Build an error JSON-RPC response object."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


# ---------------------------------------------------------------------------
# Knowledge base helpers
# ---------------------------------------------------------------------------

def _load_all_articles() -> list[dict[str, Any]]:
    """Load every JSON file from the articles directory."""
    if not ARTICLES_DIR.is_dir():
        logger.warning("Articles directory not found: %s", ARTICLES_DIR)
        return []

    articles: list[dict[str, Any]] = []
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                article = json.load(fh)
                article["_file"] = str(path)  # keep file ref internally
                articles.append(article)
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Skipping %s: %s", path, exc)
    return articles


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_search_articles(
    params: dict[str, Any],
) -> dict[str, Any]:
    """Search articles by keyword in title and summary.

    Args:
        keyword: Search term (case-insensitive).
        limit: Max results to return (default 5).

    Returns:
        A list of matching articles sorted by file name.
    """
    articles = _load_all_articles()
    keyword: str = params.get("keyword", "").lower()
    limit: int = params.get("limit", 5)

    results: list[dict[str, Any]] = []
    for article in articles:
        title = (article.get("title") or "").lower()
        summary = (article.get("summary") or "").lower()
        if keyword in title or keyword in summary:
            results.append(_strip_internal(article))

    return _make_response(None, results[:limit])


def _tool_get_article(
    params: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve a single article by its ID.

    Args:
        article_id: The article identifier (e.g. 'gith-20260624-fe862bc9').

    Returns:
        The article object, or an error if not found.
    """
    article_id: str = params.get("article_id", "")
    if not article_id:
        return _make_error(None, -32602, "Missing article_id")

    articles = _load_all_articles()
    for article in articles:
        if article.get("id") == article_id:
            return _make_response(None, _strip_internal(article))

    return _make_error(
        None,
        -32601,
        f"Article not found: {article_id}",
    )


def _tool_knowledge_stats(
    _params: dict[str, Any],
) -> dict[str, Any]:
    """Return library statistics.

    Returns:
        total_count, source_distribution, top_tags.
    """
    articles = _load_all_articles()

    source_dist: dict[str, int] = {}
    tag_counter: dict[str, int] = {}

    for article in articles:
        source = article.get("source_type") or "unknown"
        source_dist[source] = source_dist.get(source, 0) + 1

        for tag in article.get("tags", []):
            tag_counter[tag] = tag_counter.get(tag, 0) + 1

    top_tags = sorted(
        ({"tag": k, "count": v} for k, v in tag_counter.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    stats = {
        "total_count": len(articles),
        "source_distribution": source_dist,
        "top_tags": top_tags,
    }
    return _make_response(None, stats)


# ---------------------------------------------------------------------------
# MCP tool registry
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "name": "search_articles",
        "description": (
            "Search knowledge base articles by keyword in title and summary."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["keyword"],
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (case-insensitive)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5)",
                    "default": 5,
                },
            },
        },
    },
    {
        "name": "get_article",
        "description": "Retrieve a single article by its ID.",
        "inputSchema": {
            "type": "object",
            "required": ["article_id"],
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "Article identifier",
                },
            },
        },
    },
    {
        "name": "knowledge_stats",
        "description": (
            "Return knowledge base statistics: article count, "
            "source distribution, and top tags."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

_TOOL_HANDLERS = {
    "search_articles": _tool_search_articles,
    "get_article": _tool_get_article,
    "knowledge_stats": _tool_knowledge_stats,
}

# ---------------------------------------------------------------------------
# MCP method dispatch (JSON-RPC over stdio)
# ---------------------------------------------------------------------------

def _handle_initialize(request: dict[str, Any]) -> dict[str, Any]:
    """Handle the MCP initialize request."""
    return _make_response(
        request.get("id"),
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": "mcp_knowledge_server",
                "version": "1.0.0",
            },
        },
    )


def _handle_tools_list(request: dict[str, Any]) -> dict[str, Any]:
    """Handle tools/list — return the list of available tools."""
    return _make_response(request.get("id"), {"tools": _TOOLS})


def _handle_tools_call(request: dict[str, Any]) -> dict[str, Any]:
    """Handle tools/call — dispatch to the appropriate handler."""
    params = request.get("params", {})
    tool_name = params.get("name", "")
    tool_params = params.get("arguments", {})

    if tool_name not in _TOOL_HANDLERS:
        return _make_error(
            request.get("id"),
            -32601,
            f"Tool not found: {tool_name}",
        )

    try:
        return _TOOL_HANDLERS[tool_name](tool_params)
    except Exception as exc:
        logger.exception("Tool '%s' failed", tool_name)
        return _make_error(
            request.get("id"),
            -32603,
            f"Internal error: {exc}",
        )


_HANDLERS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


def _strip_internal(article: dict[str, Any]) -> dict[str, Any]:
    """Return a clean copy without internal helpers keys."""
    return {k: v for k, v in article.items() if not k.startswith("_")}


def _process_message(message: str) -> bytes:
    """Parse and process a single JSON-RPC message, return response as JSON."""
    try:
        request = json.loads(message)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON: %s", exc)
        return json.dumps(
            _make_error(None, -32700, "Parse error"),
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"

    method = request.get("method", "")
    handler = _HANDLERS.get(method)

    if handler is None:
        return json.dumps(
            _make_error(request.get("id"), -32601, f"Method not found: {method}"),
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"

    try:
        response = handler(request)
    except Exception as exc:
        logger.exception("Unhandled error in '%s'", method)
        response = _make_error(
            request.get("id"),
            -32603,
            f"Internal error: {exc}",
        )

    return json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"


def main() -> None:
    """Run the MCP server loop, reading JSON-RPC requests from stdin."""
    logger.info("mcp_knowledge_server starting — articles dir: %s", ARTICLES_DIR)

    # Ensure articles directory exists
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue

        response = _process_message(raw)
        logger.debug("Request: %s  -> Response: %s", raw[:120], response.decode()[:200])
        sys.stdout.buffer.write(response)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
