#!/usr/bin/env python3
"""Multi-provider LLM client for AI-powered knowledge analysis.

Supports OpenAI, DeepSeek, and Alibaba DashScope (Qwen) providers
via a unified interface with automatic retry on failure.

Usage (library):
    from model_client import create_provider, chat_with_retry

    provider = create_provider()
    result = chat_with_retry("Your prompt here", verbose_logging=False)
    # result is a str containing the LLM response

Usage (CLI):
    python pipeline/model_client.py "Analyze this content..."

Environment Variables:
    LLM_PROVIDER:     Provider name (openai, deepseek, qwen). Defaults to 'qwen'.
    OPENAI_API_KEY:   OpenAI API key.
    DEEPSEEK_API_KEY: DeepSeek API key.
    QWEN_API_KEY:     DashScope API key (for Qwen provider).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, FrozenSet

logger = logging.getLogger("model_client")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS: FrozenSet[str] = frozenset({"openai", "deepseek", "qwen"})

DEFAULT_PROVIDER: str = "qwen"

MAX_RETRIES: int = 3

BASE_BACKOFF: float = 1.0

# Provider-agnostic model defaults
PROVIDER_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "deepseek": "deepseek-chat",
    "qwen": "qwen-plus",
}

# API endpoint configuration per provider
API_CONFIG: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "chat_endpoint": "/chat/completions",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "chat_endpoint": "/chat/completions",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "chat_endpoint": "/api/v1/services/aigc/text-generation/generation",
    },
}


# ---------------------------------------------------------------------------
# Provider data class
# ---------------------------------------------------------------------------

@dataclass
class Provider:
    """Encapsulates LLM provider configuration and API client."""

    name: str
    api_key: str
    model: str
    base_url: str


# ---------------------------------------------------------------------------
# Helper: parse LLM response
# ---------------------------------------------------------------------------


def _parse_response(response_text: str) -> str:
    """Extract the final response text from various LLM response formats.

    Handles:
    - Plain JSON content field
    - JSON wrapped in ```json ... ``` markdown block
    - JSON preceded by explanatory text

    Args:
        response_text: Raw string response from the LLM API.

    Returns:
        Clean response text string.
    """
    response_text = response_text.strip()

    # If it looks like JSON, return as-is (pipeline will parse it)
    if response_text.startswith(("{", "[")):
        return response_text

    # Try extracting from markdown code block
    md_match = re.search(
        r"```(?:json|JSON)?\s*\n?(.*?)\n?```",
        response_text,
        re.DOTALL,
    )
    if md_match:
        return md_match.group(1).strip()

    # Return the original text (pipeline will try JSON parsing)
    return response_text


def _is_html_response(text: str) -> bool:
    """Check if the response appears to be HTML (e.g. 5xx error page).

    Args:
        text: Response text to inspect.

    Returns:
        True if the text looks like HTML content, False otherwise. """
    stripped = text.strip()
    return stripped.startswith(("<html", "<!DOCTYPE", "<!doctype"))


# ---------------------------------------------------------------------------
# Core: send request with retry
# ---------------------------------------------------------------------------


def _send_request(
    provider: Provider,
    prompt: str,
    *,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Send a chat request to the LLM API with automatic retry.

    Constructs the appropriate API request body based on the provider,
    sends it, parses the JSON response, and extracts the chat content.
    Implements exponential backoff on failure.

    Args:
        provider: Authenticated Provider instance.
        prompt: The user prompt text.
        max_retries: Maximum number of retry attempts (default 3).

    Returns:
        The final parsed response string.

    Raises:
        RuntimeError: If all retry attempts fail.
    """
    config = API_CONFIG.get(provider.name)
    if not config:
        raise ValueError(f"Unsupported provider: {provider.name}")

    url = config["base_url"] + config["chat_endpoint"]
    headers: dict[str, str] = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }

    # DashScope uses slightly different body structure
    if provider.name == "qwen":
        body: dict[str, Any] = {
            "model": provider.model,
            "input": {
                "messages": [
                    {"role": "user", "content": prompt},
                ],
            },
            "parameters": {},
        }
    else:
        body = {
            "model": provider.model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }

    try:
        import httpx
    except ImportError:
        logger.error(
            "httpx 未安装，无法调用 LLM API。\n"
            "请执行: pip install httpx",
        )
        raise

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, json=body, headers=headers)
                response.raise_for_status()

            data = response.json()

            # Unify response parsing across providers
            if provider.name == "qwen":
                # DashScope response format
                output = data.get("output", {})
                text = output.get("text", "")
                if text:
                    return _parse_response(text)
            elif "choices" in data:
                # OpenAI / DeepSeek response format
                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    text = message.get("content", "")
                    if text:
                        return _parse_response(text)

            # If we got here, no usable content found
            raise ValueError(
                f"Unexpected response format: {json.dumps(data, ensure_ascii=False)[:200]}"
            )

        except httpx.HTTPStatusError as exc:
            if _is_html_response(exc.response.text):
                logger.warning(
                    "收到 HTML 响应 (status=%s)，可能为服务端错误",
                    exc.response.status_code,
                )
            else:
                logger.warning(
                    "API 请求返回状态 %s",
                    exc.response.status_code,
                )

            if attempt < max_retries:
                backoff = BASE_BACKOFF * (2 ** (attempt - 1))
                logger.debug("等待 %.1f 秒后重试 (%d/%d)", backoff, attempt, max_retries)
                time.sleep(backoff)
            else:
                logger.error("API 调用失败 (status=%s)，已放弃重试", exc.response.status_code)
                raise

        except httpx.RequestError as exc:
            if attempt < max_retries:
                backoff = BASE_BACKOFF * (2 ** (attempt - 1))
                logger.debug("网络连接失败，等待 %.1f 秒后重试 (%d/%d)", backoff, attempt, max_retries)
                time.sleep(backoff)
            else:
                logger.error("网络连接失败，已放弃重试: %s", exc)
                raise

    raise RuntimeError("所有重试attempt均失败")


# ---------------------------------------------------------------------------
# API entry points
# -----------------------------------------------------------------------

def create_provider() -> Provider:
    """Create an authenticated Provider instance based on environment configuration.

    Reads LLM_PROVIDER, provider-specific API keys, and resolves the model
    from the default mapping. Name validation is performed.

    Returns:
        Authenticated Provider instance configured for the selected provider.

    Raises:
        ValueError: If LLM_PROVIDER is unsupported or API key is missing.
    """
    provider_name: str = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).lower().strip()
    if provider_name not in SUPPORTED_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{provider_name}'. "
            f"Allowed values: {allowed}"
        )

    config = API_CONFIG[provider_name]
    model = PROVIDER_MODELS.get(provider_name, provider_name)

    api_key_envs: dict[str, str] = {
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "QWEN_API_KEY",
    }
    api_key: str = os.environ.get(
        api_key_envs[provider_name], "",
    ).strip()
    if not api_key:
        raise ValueError(
            f"API key not found. Set the environment variable "
            f"'{api_key_envs[provider_name]}' for provider '{provider_name}'.",
        )

    return Provider(
        name=provider_name,
        api_key=api_key,
        model=model,
        base_url=config["base_url"],
    )


def chat_with_retry(
    prompt: str,
    *,
    max_retries: int = MAX_RETRIES,
    verbose_logging: bool = False,
) -> str:
    """Send a prompt to the configured LLM with automatic retry and logging.

    This is the primary entry point for LLM-powered analysis. It creates
    a Provider instance from environment variables, constructs the API
    request body, sends the request to the API endpoint, and parses the
    response. Automatic retry is implemented with exponential backoff.

    If LLM_PROVIDER is not set or the model_client cannot be loaded, falls
    back to a simple local enrichment strategy that uses repository stats
    to compute a rule-based score.

    Args:
        prompt: The text prompt to send to the LLM.
        max_retries: Maximum number of retry attempts on failure (default 3).
        verbose_logging: Whether to log per-attempt debug information.

    Returns:
        The parsed response as a string (plain text or JSON object).

    Raises:
        ValueError: If provider is unsupported or API key is missing.
        RuntimeError: If all retry attempts fail.
    """
    try:
        provider = create_provider()
    except (ValueError, ImportError) as exc:
        logger.warning(
            "未能创建 LLM Provider (%s)。\n"
            "请检查 LLM_PROVIDER 环境变量及对应 API Key 是否配置。\n"
            "已回退到本地分析模式。",
            exc,
        )
        return _fallback_enrich(prompt)

    try:
        result: str = _send_request(
            provider, prompt, max_retries=max_retries,
        )
        if verbose_logging:
            logger.debug("LLM 响应 (前 200 字符): %s", result[:200])
        return result

    except (ValueError, RuntimeError) as exc:
        logger.error("LLM 调用失败: %s。已回退到本地分析模式。", exc)
        return _fallback_enrich(prompt)


def _fallback_enrich(prompt: str) -> dict[str, float]:
    """Provide a rule-based fallback when LLM API is unavailable.

    Extracts star count and repository name from common prompt patterns,
    computing a score based on star threshold heuristics.

    Args:
        prompt: The original prompt text containing project metadata.

    Returns:
        A dict with keys: title, summary, tags, score.
    """
    name_m = re.search(r"名称:\s*(.+)", prompt)
    url_m = re.search(r"链接:\s*(.+)", prompt)
    stars_m = re.search(r"星标:\s*(\d+)", prompt)

    name = (name_m.group(1).strip()) if name_m else "未命名项目"
    url = (url_m.group(1).strip()) if url_m else ""
    stars = int(stars_m.group(1)) if stars_m else 0

    score = min(10.0, max(1.0, stars / 1000.0)) if (url and stars > 10) else 0.0

    # Extract topics if present
    topics_m = re.search(r"标签:\s*\[([^\]]*)\]", prompt)
    tags: list[str] = []
    if topics_m:
        tags = [t.strip() for t in topics_m.group(1).split(",") if t.strip()]

    return {
        "title": name,
        "summary": f"星标数: {stars}",
        "tags": tags if tags else ["unknown"],
        "score": float(score),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for CLI usage.

    Args:
        argv: Arguments list (defaults to sys.argv[1:]).

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        prog="model_client",
        description="Call configured LLM with a user prompt (CLI mode).",
    )
    parser.add_argument(
        "prompt",
        type=str,
        help="The prompt text to send to the LLM.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (debug) output.",
    )
    return parser.parse_args(argv)


def main() -> None:
    """CLI entry point for model_client.

    Reads the prompt from the command line, calls chat_with_retry,
    and prints the parsed result to stdout.
    """
    args = parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    result = chat_with_retry(args.prompt, verbose_logging=args.verbose)

    try:
        parsed_result: dict[str, Any] = (
            json.loads(result) if isinstance(result, str) else result
        )
        print(json.dumps(parsed_result, ensure_ascii=False, indent=2))
    except (json.JSONDecodeError, TypeError):
        print(result)


if __name__ == "__main__":
    main()
