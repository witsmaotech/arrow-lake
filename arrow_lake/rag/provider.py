"""LLM provider abstraction for RAG pipeline."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from arrow_lake.config import LLMConfig, LLMProviderType
from arrow_lake.exceptions import ErrorCode, RAGError

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 3


def _safe_json_body(resp: httpx.Response) -> dict[str, Any]:
    """Parse JSON error body, falling back to raw text on decode failure."""
    if not resp.content:
        return {}
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return {"raw": resp.text[:200]}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMMessage:
    """A single message in an LLM conversation."""

    role: str
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """Response from an LLM provider."""

    content: str
    model: str
    provider: str
    usage: dict[str, int] | None = None
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    @abstractmethod
    async def generate(self, messages: list[LLMMessage]) -> LLMResponse:
        """Generate a response for the given messages."""

    @abstractmethod
    async def generate_stream(
        self, messages: list[LLMMessage]
    ) -> AsyncIterator[str]:
        """Stream a response, yielding content deltas."""

    async def close(self) -> None:  # noqa: B027
        """Close the underlying HTTP client."""


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (OpenAI / vLLM / Ollama)
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URLS: dict[LLMProviderType, str] = {
    LLMProviderType.OPENAI: "https://api.openai.com/v1",
    LLMProviderType.VLLM: "http://localhost:8000/v1",
    LLMProviderType.OLLAMA: "http://localhost:11434/v1",
}


class OpenAICompatibleProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible APIs.

    Supports OpenAI, vLLM, and Ollama (all use ``/v1/chat/completions``).
    """

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._base_url = (
            config.api_base
            if config.api_base
            else _DEFAULT_BASE_URLS.get(config.provider, "https://api.openai.com/v1")
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(config.timeout_seconds),
        )

    def _build_body(
        self, messages: list[LLMMessage], stream: bool = False
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "stream": stream,
        }
        # qwen3.x on Ollama: thinking tokens count against max_tokens budget,
        # producing empty content.  Disable extended thinking for RAG use.
        if self._config.provider == LLMProviderType.OLLAMA:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return body

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _request(self, body: dict[str, Any]) -> httpx.Response:
        """Send a POST request with tenacity retry on transient failures."""
        return await self._client.post("/chat/completions", json=body)

    async def _request_no_retry(self, body: dict[str, Any]) -> httpx.Response:
        """Send a POST request without retry (for streaming, where retry causes data duplication)."""
        return await self._client.post("/chat/completions", json=body)

    async def generate(self, messages: list[LLMMessage]) -> LLMResponse:
        try:
            resp = await self._request(self._build_body(messages))
        except httpx.HTTPError as exc:
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"HTTP error calling {self._config.provider.value}: {exc}",
                context={"provider": self._config.provider.value},
            ) from exc

        if resp.status_code != 200:
            error_body = _safe_json_body(resp)
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"Provider returned {resp.status_code}: {error_body}",
                context={
                    "provider": self._config.provider.value,
                    "status_code": resp.status_code,
                },
            )

        data = resp.json()
        choice = data["choices"][0]
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", self._config.model),
            usage=data.get("usage"),
            finish_reason=choice.get("finish_reason"),
            provider=self._config.provider.value,
        )

    async def generate_stream(
        self, messages: list[LLMMessage]
    ) -> AsyncIterator[str]:
        try:
            resp = await self._request_no_retry(self._build_body(messages, stream=True))
        except httpx.HTTPError as exc:
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"HTTP error in stream: {exc}",
                context={"provider": self._config.provider.value},
            ) from exc

        if resp.status_code != 200:
            error_body = _safe_json_body(resp)
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"Provider stream returned {resp.status_code}: {error_body}",
                context={"provider": self._config.provider.value, "status_code": resp.status_code},
            )

        async for raw_line in resp.aiter_lines():
            line = raw_line.strip()
            if not line:
                continue
            if line == "data: [DONE]":
                break
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider(BaseLLMProvider):
    """Provider for Anthropic Claude API (``/v1/messages``)."""

    _ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        if not config.api_key:
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message="Anthropic provider requires an API key",
                context={"provider": "anthropic"},
            )
        self._base_url = (
            config.api_base if config.api_base else "https://api.anthropic.com"
        )
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": config.api_key,
            "anthropic-version": getattr(config, "anthropic_version", self._ANTHROPIC_VERSION),
        }
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(config.timeout_seconds),
        )

    def _build_body(
        self, messages: list[LLMMessage], stream: bool = False
    ) -> dict[str, Any]:
        # Anthropic puts system in a top-level field, not in messages
        system_text: str | None = None
        api_messages: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_text = m.content
            else:
                api_messages.append({"role": m.role, "content": m.content})

        body: dict[str, Any] = {
            "model": self._config.model,
            "messages": api_messages,
            "max_tokens": self._config.max_tokens,
            "stream": stream,
        }
        if system_text:
            body["system"] = system_text
        if self._config.temperature != 1.0:
            body["temperature"] = self._config.temperature
        return body

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _request(self, body: dict[str, Any]) -> httpx.Response:
        """Send a POST request with tenacity retry on transient failures."""
        return await self._client.post("/v1/messages", json=body)

    async def _request_no_retry(self, body: dict[str, Any]) -> httpx.Response:
        """Send a POST request without retry (for streaming)."""
        return await self._client.post("/v1/messages", json=body)

    async def generate(self, messages: list[LLMMessage]) -> LLMResponse:
        try:
            resp = await self._request(self._build_body(messages))
        except httpx.HTTPError as exc:
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"HTTP error calling Anthropic: {exc}",
                context={"provider": "anthropic"},
            ) from exc

        if resp.status_code != 200:
            error_body = _safe_json_body(resp)
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"Anthropic returned {resp.status_code}: {error_body}",
                context={"provider": "anthropic", "status_code": resp.status_code},
            )

        data = resp.json()
        # Anthropic returns content as list of blocks
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        content = "\n".join(text_blocks)

        usage = data.get("usage")
        usage_map: dict[str, int] | None = None
        if usage:
            usage_map = {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            }

        return LLMResponse(
            content=content,
            model=data.get("model", self._config.model),
            usage=usage_map,
            finish_reason=data.get("stop_reason"),
            provider="anthropic",
        )

    async def generate_stream(
        self, messages: list[LLMMessage]
    ) -> AsyncIterator[str]:
        try:
            resp = await self._request_no_retry(self._build_body(messages, stream=True))
        except httpx.HTTPError as exc:
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"HTTP error in Anthropic stream: {exc}",
                context={"provider": "anthropic"},
            ) from exc

        if resp.status_code != 200:
            error_body = _safe_json_body(resp)
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"Anthropic stream returned {resp.status_code}: {error_body}",
                context={"provider": "anthropic", "status_code": resp.status_code},
            )

        async for raw_line in resp.aiter_lines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    event_type = chunk.get("type", "")
                    if event_type == "content_block_delta":
                        delta = chunk.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_llm_provider(config: LLMConfig) -> BaseLLMProvider:
    """Create an LLM provider based on configuration.

    Args:
        config: LLM configuration with provider type and credentials.

    Returns:
        An appropriate ``BaseLLMProvider`` instance.

    Raises:
        RAGError: If the provider type is unsupported.
    """
    match config.provider:
        case LLMProviderType.OPENAI | LLMProviderType.VLLM | LLMProviderType.OLLAMA:
            return OpenAICompatibleProvider(config)
        case LLMProviderType.ANTHROPIC:
            return AnthropicProvider(config)
        case _:
            raise RAGError(
                error_code=ErrorCode.RAG_PROVIDER_ERROR,
                message=f"Unsupported LLM provider: {config.provider.value}",
                context={"provider": config.provider.value},
            )
