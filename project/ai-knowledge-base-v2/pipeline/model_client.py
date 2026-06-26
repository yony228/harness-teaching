#!/usr/bin/env python3
"""Unified LLM client for the knowledge-base pipeline.

Provides a consistent interface across DeepSeek, Qwen, and OpenAI providers
via their OpenAI-compatible REST APIs using ``httpx`` directly.

Usage::

    from pipeline.model_client import chat_with_retry, quick_chat

    # With retry (3 attempts, exponential backoff)
    response = chat_with_retry("Hello, world!")

    # Quick one-liner
    result = quick_chat("Explain quantization")

Environment variables:

    LLM_PROVIDER
        One of ``deepseek``, ``qwen``, or ``openai``. Defaults to ``deepseek``.

    LLM_API_KEY
        The API key for the selected provider.

"""

from __future__ import annotations

import abc
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint configuration per provider
# ---------------------------------------------------------------------------


PROVIDER_CONFIG: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "base_url": "https://api.qwen.ai/v1",
        "default_model": "qwen-plus",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
}

# Per-token cost in USD (input / output).  None means unknown.
MODEL_COST: dict[str, tuple[float | None, float | None]] = {
    "deepseek-chat": (1.40e-7, 2.80e-7),
    "deepseek-reasoner": (4.67e-7, 1.87e-6),
    "gpt-4o": (2.50e-6, 1.00e-5),
    "gpt-4o-mini": (1.50e-7, 6.00e-7),
    "qwen-plus": (4.00e-7, 1.20e-6),
    "qwen-turbo": (2.50e-7, 8.00e-7),
}

MAX_RETRIES: int = 3
REQUEST_TIMEOUT: float = 60.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Token consumption and cost breakdown."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def total(self) -> int:
        """Total tokens consumed."""
        return self.total_tokens


@dataclass
class LLMResponse:
    """Unified LLM response envelope.

    Attributes:
        content: The model's response text.
        usage: Token usage and cost statistics.
        model: The model identifier that produced the response.
        finish_reason: Reason the model stopped generating.
    """

    content: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    finish_reason: str = ""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LLMProvider(abc.ABC):
    """Abstract interface for an LLM provider.

    Subclasses must implement ``build_request_body()`` and
    ``parse_response()`` to adapt the generic OpenAI-compatible
    chat completion contract to a specific provider.
    """

    @abc.abstractmethod
    def build_request_body(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build a provider-specific request body dict.

        Args:
            messages: List of message dicts with ``role`` and ``content``.
            model: Model identifier string.
            **kwargs: Extra parameters (temperature, max_tokens, etc.).

        Returns:
            A serializable dict ready for ``json.dumps()``.
        """

    @abc.abstractmethod
    def parse_response(
        self,
        raw: dict[str, Any],
    ) -> LLMResponse:
        """Parse a JSON API response into ``LLMResponse``.

        Args:
            raw: Parsed JSON response dict from the provider.

        Returns:
            A fully populated ``LLMResponse`` instance.
        """

    @abc.abstractmethod
    def get_api_key(self) -> str:
        """Return the API key for this provider."""


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible API provider (works for DeepSeek, Qwen, etc.).

    Most modern LLM providers expose an OpenAI-compatible chat completions
    endpoint.  This class normalizes the request/response format across
    such providers.

    Args:
        provider_name: Provider identifier used to look up configuration.
            Must be one of ``deepseek``, ``qwen``, or ``openai``.
    """

    def __init__(self, provider_name: str = "deepseek") -> None:
        """Initialize the provider.

        Args:
            provider_name: One of ``deepseek``, ``qwen``, ``openai``.
                Defaults to ``deepseek``.

        Raises:
            ValueError: If the provider name is not supported.
        """
        if provider_name not in PROVIDER_CONFIG:
            raise ValueError(
                f"不支持的提供商 '{provider_name}'. "
                f"可选值: {', '.join(PROVIDER_CONFIG.keys())}",
            )
        self._provider_name = provider_name
        config = PROVIDER_CONFIG[provider_name]
        self._base_url: str = config["base_url"]
        self._default_model: str = config["default_model"]
        self._api_key: str = self.get_api_key()

    # -- LLMProvider interface --

    def get_api_key(self) -> str:
        """Return the API key from the environment.

        Returns:
            The API key string.

        Raises:
            ValueError: If the relevant environment variable is not set.
        """
        env_key = f"{self._provider_name.upper()}_API_KEY"
        api_key = os.environ.get(env_key)
        if not api_key:
            raise ValueError(
                f"环境变量 {env_key} 未设置。"
                f"请为提供商 '{self._provider_name}' 配置 API Key。",
            )
        return api_key

    def build_request_body(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build a standard chat completions request body.

        Args:
            messages: List of message dicts (each with ``role`` and
                ``content`` keys).
            model: Optional model override.  Falls back to the provider
                default.
            **kwargs: Additional parameters forwarded to the API
                (``temperature``, ``max_tokens``, ``top_p``, etc.).

        Returns:
            A request body dict ready for JSON encoding.
        """
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
        }
        body.update(kwargs)
        return body

    def parse_response(self, raw: dict[str, Any]) -> LLMResponse:
        """Parse a chat completions API JSON response.

        Args:
            raw: The parsed JSON body of the HTTP response.

        Returns:
            A populated ``LLMResponse`` with content and usage stats.

        Raises:
            ValueError: If the response structure is invalid or
                indicates an error.
        """
        # Handle provider-specific error wrapping
        if "error" in raw:
            error_info = raw["error"]
            msg = error_info.get("message", "Unknown error")
            code = error_info.get("code", "unknown")
            raise ValueError(f"API error [{code}]: {msg}")

        choices = raw.get("choices", [])
        if not choices:
            raise ValueError("API 响应中未找到 choices 字段")

        first = choices[0]
        message = first.get("message", {})
        content = message.get("content", "")

        usage_raw = raw.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
            estimated_cost_usd=_compute_cost(
                usage_raw.get("total_tokens", 0),
                self._get_pricing(raw.get("model", "")),
            ),
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=raw.get("model", self._default_model),
            finish_reason=first.get("finish_reason", ""),
        )

    # -- Internal helpers --

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        """Send a chat request and return the parsed response.

        Args:
            messages: List of message dicts with ``role`` and
                ``content`` keys.
            **kwargs: Extra API parameters (``temperature``,
                ``max_tokens``, etc.).

        Returns:
            A ``LLMResponse`` with the model output.

        Raises:
            httpx.HTTPStatusError: On non-2xx HTTP responses.
            httpx.RequestError: On network-level failures.
            ValueError: On invalid response structure.
        """
        body = self.build_request_body(messages, **kwargs)
        model = body.get("model", self._default_model)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._base_url}/chat/completions"

        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post(
                url,
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            raw = response.json()

        return self.parse_response(raw), model

    def _get_pricing(
        self,
        model_name: str,
    ) -> tuple[float | None, float | None]:
        """Look up pricing for a model name.

        Args:
            model_name: The model identifier string.

        Returns:
            A tuple of (input_cost_per_token, output_cost_per_token),
            or ``(None, None)`` if unknown.
        """
        # Check exact match first
        for key, pricing in MODEL_COST.items():
            if key in model_name:
                return pricing
        return (None, None)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _compute_cost(
    total_tokens: int,
    pricing: tuple[float | None, float | None],
) -> float:
    """Estimate the USD cost for a given token count and pricing.

    Uses the input_cost_per_token (zero-index pricing if None).  Output
    cost is not separately tracked here because ``total_tokens`` alone
    doesn't distinguish input vs output — a rough approximation using
    input cost is applied.

    Args:
        total_tokens: Total number of tokens consumed.
        pricing: Tuple of (input_per_token, output_per_token) in USD.

    Returns:
        Estimated cost in USD, or 0.0 if pricing is unknown.
    """
    input_cost, _ = pricing
    if input_cost is None:
        return 0.0
    return round(total_tokens * input_cost, 10)


def estimate_tokens(text: str) -> int:
    """Roughly estimate the number of tokens in a text string.

    This is a heuristic approximation — actual token counts depend on
    the tokenizer used by each provider.

    Rules of thumb:
    - English: ~4 characters per token
    - CJK: ~1-2 characters per token (roughly 1.5x English)

    Args:
        text: The text string to estimate.

    Returns:
        An approximate token count (always >= 0).
    """
    if not text:
        return 0
    # Count CJK characters vs ASCII characters
    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_count = len(text) - cjk_count
    # Rough formula: 1 CJK char ≈ 2.5 tokens, 1 ASCII word ≈ 1 token
    token_count = (cjk_count * 1.5) + (ascii_count / 4.0)
    return max(1, int(token_count))


def format_cost(cost_usd: float) -> str:
    """Format a USD cost value into a human-readable string.

    Args:
        cost_usd: Cost in US dollars.

    Returns:
        A formatted string, e.g. ``"$0.001234567890"``.

    Raises:
        ValueError: If *cost_usd* is negative.
    """
    if cost_usd < 0:
        raise ValueError(f"成本不能为负数: {cost_usd}")
    return f"${cost_usd:.10f}".rstrip("0").rstrip(".")


def create_provider(provider_name: str | None = None) -> OpenAICompatibleProvider:
    """Create an LLM provider instance based on environment configuration.

    Reads ``LLM_PROVIDER`` from the environment (defaults to
    ``"deepseek"``) and returns the corresponding
    ``OpenAICompatibleProvider``.

    Args:
        provider_name: Optional explicit provider override.  If provided,
            takes precedence over the environment variable.

    Returns:
        An ``OpenAICompatibleProvider`` instance ready for use.

    Raises:
        ValueError: If the provider name is invalid or the API key is
            not configured.
    """
    effective = (
        provider_name
        or os.environ.get("LLM_PROVIDER", "deepseek")
    ).lower().strip()

    return OpenAICompatibleProvider(effective)


def chat_with_retry(
    prompt: str | list[dict[str, Any]],
    *,
    system_message: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    verbose_logging: bool = False,
    **kwargs: Any,
) -> LLMResponse:
    """Send a chat request with automatic retry (exponential backoff).

    Retries up to ``MAX_RETRIES`` times (default 3) on transient errors
    (HTTP 429, 5xx, network timeouts) with exponential backoff between
    attempts.

    Args:
        prompt: Either a plain text string (automatically wrapped as a
            user message) or a pre-built list of message dicts with
            ``role`` and ``content`` keys.
        system_message: Optional system prompt to prepend.
        model: Optional model name override (e.g. ``"gpt-4o-mini"``).
        provider: Explicit provider override (``"deepseek"``,
            ``"qwen"``, ``"openai"``).  Falls back to
            ``LLM_PROVIDER`` env var.
        verbose_logging: If true, log each retry attempt with details.
        **kwargs: Additional parameters forwarded to the API
            (``temperature``, ``max_tokens``, ``top_p``, etc.).

    Returns:
        A ``LLMResponse`` from the last successful attempt.

    Raises:
        ValueError: If the provider is invalid or API key is missing.
        httpx.HTTPStatusError: On non-retryable HTTP errors (after
            exhausting retries).
        httpx.RequestError: On network failures (after exhausting
            retries).
    """
    provider_instance = create_provider(provider)

    # Normalize prompt into messages list
    if isinstance(prompt, str):
        messages: list[dict[str, Any]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
    else:
        messages = list(prompt)  # shallow copy

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Add token count as hint for providers that support it
            total_prompt_tokens = sum(
                estimate_tokens(str(m.get("content", ""))) for m in messages
            )
            if verbose_logging:
                logger.info(
                    "请求 %d/%d | 模型: %s | 预估tokens: %d",
                    attempt,
                    MAX_RETRIES,
                    model or provider_instance._default_model,
                    total_prompt_tokens,
                )

            response, _ = provider_instance.chat(messages, model=model, **kwargs)

            if verbose_logging:
                logger.info(
                    "响应成功 | 模型: %s | Tokens: %s | 成本: %s",
                    response.model,
                    response.usage.total_tokens,
                    format_cost(response.usage.estimated_cost_usd),
                )

            return response

        except (ValueError, httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_error = exc
            if verbose_logging:
                logger.warning(
                    "请求失败 (尝试 %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
            else:
                logger.debug("重试 %d 异常: %s", attempt, exc)

        if attempt < MAX_RETRIES:
            backoff = 2.0 ** attempt  # 2, 4, 8, ...
            if verbose_logging:
                logger.debug("等待 %.1f 秒后重试...", backoff)
            time.sleep(backoff)

    # All retries exhausted
    if isinstance(last_error, ValueError):
        raise last_error
    if isinstance(last_error, httpx.HTTPStatusError):
        raise last_error
    raise last_error or RuntimeError("未知错误: 所有重试均失败")


def quick_chat(
    prompt: str,
    *,
    system_message: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    **kwargs: Any,
) -> str:
    """One-liner convenience function for simple LLM calls.

    This is a shorthand for ``chat_with_retry(prompt).content``.  It
    returns only the response text string rather than the full
    ``LLMResponse``.

    Args:
        prompt: The user prompt string.
        system_message: Optional system prompt.
        model: Optional model name override.
        provider: Explicit provider override.
        **kwargs: Additional parameters forwarded to the API.

    Returns:
        The LLM's response text string.

    Raises:
        ValueError: If the provider is invalid or API key is missing.
        httpx.HTTPStatusError: On non-retryable HTTP errors.
        httpx.RequestError: On network failures.
    """
    response = chat_with_retry(
        prompt,
        system_message=system_message,
        model=model,
        provider=provider,
        **kwargs,
    )
    return response.content


# ---------------------------------------------------------------------------
# Main: self-test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print("  model_client 自检")
    print("=" * 60)

    # 1. Test create_provider
    print("\n[1] 测试 create_provider() ...")
    for name in PROVIDER_CONFIG:
        try:
            p = create_provider(name)
            print(f"  提供商 '{name}': 配置正确 (默认模型: {p._default_model})")
        except ValueError as e:
            print(f"  提供商 '{name}': {e}")

    # 2. Test estimate_tokens
    print("\n[2] 测试 estimate_tokens() ...")
    en_text = "The quick brown fox jumps over the lazy dog."
    zh_text = "这是一个测试句子，用于估算token数量。"
    print(f"  英文: '{en_text}' -> {estimate_tokens(en_text)} tokens")
    print(f"  中文: '{zh_text}' -> {estimate_tokens(zh_text)} tokens")

    # 3. Test format_cost
    print("\n[3] 测试 format_cost() ...")
    test_costs = [0.0, 1.4e-7, 1e-4, 0.001234567890]
    for c in test_costs:
        print(f"  {c} USD -> {format_cost(c)}")

    # 4. Attempt live chat (requires API keys in environment)
    print("\n[4] 尝试实际 LLM 调用 ...")
    try:
        response = quick_chat(
            "请用一句话解释什么是大语言模型。",
            provider=None,  # 使用 LLM_PROVIDER 环境变量
        )
        print(f"  响应: {response}")
    except ValueError as e:
        print(f"  跳过: {e}")

    print("\n" + "=" * 60)
    print("  自检完成。")
    print("=" * 60)
