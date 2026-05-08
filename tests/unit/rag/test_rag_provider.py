"""Tests for LLM Provider abstraction — M2 Day 2."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arrow_lake.config import LLMConfig, LLMProviderType
from arrow_lake.exceptions import ErrorCode, RAGError
from arrow_lake.rag.provider import (
    AnthropicProvider,
    LLMMessage,
    LLMResponse,
    OpenAICompatibleProvider,
    create_llm_provider,
)

# ---------------------------------------------------------------------------
# LLMMessage
# ---------------------------------------------------------------------------


class TestLLMMessage:
    def test_construction(self) -> None:
        msg = LLMMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_frozen(self) -> None:
        msg = LLMMessage(role="user", content="Hello")
        with pytest.raises(AttributeError):
            msg.role = "system"  # type: ignore[misc]

    def test_system_role(self) -> None:
        msg = LLMMessage(role="system", content="You are helpful.")
        assert msg.role == "system"

    def test_assistant_role(self) -> None:
        msg = LLMMessage(role="assistant", content="Hi there!")
        assert msg.role == "assistant"


# ---------------------------------------------------------------------------
# LLMResponse
# ---------------------------------------------------------------------------


class TestLLMResponse:
    def test_construction_with_usage(self) -> None:
        resp = LLMResponse(
            content="Hello!",
            model="gpt-4o-mini",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            finish_reason="stop",
            provider="openai",
        )
        assert resp.content == "Hello!"
        assert resp.model == "gpt-4o-mini"
        assert resp.usage["total_tokens"] == 15
        assert resp.finish_reason == "stop"
        assert resp.provider == "openai"

    def test_construction_minimal(self) -> None:
        resp = LLMResponse(content="OK", model="test", provider="test")
        assert resp.content == "OK"
        assert resp.finish_reason is None
        assert resp.usage is None

    def test_frozen(self) -> None:
        resp = LLMResponse(content="X", model="m", provider="p")
        with pytest.raises(AttributeError):
            resp.content = "Y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# create_llm_provider factory
# ---------------------------------------------------------------------------


class TestCreateLLMProvider:
    def test_openai_provider(self) -> None:
        config = LLMConfig(provider=LLMProviderType.OPENAI, api_key="sk-test")
        provider = create_llm_provider(config)
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_vllm_provider(self) -> None:
        config = LLMConfig(provider=LLMProviderType.VLLM)
        provider = create_llm_provider(config)
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_ollama_provider(self) -> None:
        config = LLMConfig(provider=LLMProviderType.OLLAMA)
        provider = create_llm_provider(config)
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_anthropic_provider(self) -> None:
        config = LLMConfig(provider=LLMProviderType.ANTHROPIC, api_key="sk-ant-test")
        provider = create_llm_provider(config)
        assert isinstance(provider, AnthropicProvider)

    def test_anthropic_requires_api_key(self) -> None:
        config = LLMConfig(provider=LLMProviderType.ANTHROPIC)
        with pytest.raises(RAGError) as exc_info:
            create_llm_provider(config)
        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR
        assert "API key" in exc_info.value.message

    def test_deepseek_provider(self) -> None:
        config = LLMConfig(provider=LLMProviderType.DEEPSEEK, api_key="sk-ds-test")
        provider = create_llm_provider(config)
        assert isinstance(provider, OpenAICompatibleProvider)


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider — unit tests with mocked httpx
# ---------------------------------------------------------------------------


class TestOpenAICompatibleProvider:
    @pytest.fixture()
    def openai_config(self) -> LLMConfig:
        return LLMConfig(
            provider=LLMProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="sk-test-key",
            temperature=0.5,
            max_tokens=100,
        )

    @pytest.mark.asyncio
    async def test_generate_success(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "choices": [
                {"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}
            ],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [LLMMessage(role="user", content="Hi")]
            resp = await provider.generate(messages)

        assert resp.content == "Hello!"
        assert resp.model == "gpt-4o-mini"
        assert resp.finish_reason == "stop"
        assert resp.usage is not None
        assert resp.usage["total_tokens"] == 12

    @pytest.mark.asyncio
    async def test_generate_api_error_raises(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {
            "error": {"message": "Rate limited", "type": "rate_limit_error"}
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [LLMMessage(role="user", content="Hi")]

            with pytest.raises(RAGError) as exc_info:
                await provider.generate(messages)

        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_generate_http_error_raises(self, openai_config: LLMConfig) -> None:
        import httpx

        provider = OpenAICompatibleProvider(openai_config)

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            messages = [LLMMessage(role="user", content="Hi")]

            with pytest.raises(RAGError) as exc_info:
                await provider.generate(messages)

        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_generate_non_json_error_body(self, openai_config: LLMConfig) -> None:
        """Provider should not crash when error body is not valid JSON."""
        provider = OpenAICompatibleProvider(openai_config)
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.content = b"<html>Bad Gateway</html>"
        mock_response.json.side_effect = json.JSONDecodeError("expecting value", "", 0)
        mock_response.text = "<html>Bad Gateway</html>"

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [LLMMessage(role="user", content="Hi")]

            with pytest.raises(RAGError) as exc_info:
                await provider.generate(messages)

        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_generate_stream_yields_content(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)

        mock_response = MagicMock()
        mock_response.status_code = 200

        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]

        async def mock_aiter_lines():
            for line in lines:
                yield line

        mock_response.aiter_lines = mock_aiter_lines

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [LLMMessage(role="user", content="Hi")]
            collected = []
            async for chunk in provider.generate_stream(messages):
                collected.append(chunk)

        assert collected == ["Hello", " world"]

    @pytest.mark.asyncio
    async def test_vllm_default_base_url(self) -> None:
        config = LLMConfig(provider=LLMProviderType.VLLM)
        provider = OpenAICompatibleProvider(config)
        assert provider._base_url == "http://localhost:8000/v1"

    @pytest.mark.asyncio
    async def test_ollama_default_base_url(self) -> None:
        config = LLMConfig(provider=LLMProviderType.OLLAMA)
        provider = OpenAICompatibleProvider(config)
        assert provider._base_url == "http://localhost:11434/v1"

    @pytest.mark.asyncio
    async def test_deepseek_default_base_url(self) -> None:
        config = LLMConfig(provider=LLMProviderType.DEEPSEEK, api_key="sk-ds")
        provider = OpenAICompatibleProvider(config)
        assert provider._base_url == "https://api.deepseek.com"

    @pytest.mark.asyncio
    async def test_deepseek_thinking_disabled_for_simple_query(self) -> None:
        """Short factual queries should disable thinking mode."""
        config = LLMConfig(provider=LLMProviderType.DEEPSEEK, api_key="sk-ds")
        provider = OpenAICompatibleProvider(config)
        body = provider._build_body([LLMMessage(role="user", content="Hi")])
        assert body["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in body

    @pytest.mark.asyncio
    async def test_deepseek_thinking_disabled_for_extraction(self) -> None:
        """Entity extraction needs strict JSON output — thinking breaks format."""
        config = LLMConfig(provider=LLMProviderType.DEEPSEEK, api_key="sk-ds")
        provider = OpenAICompatibleProvider(config)
        body = provider._build_body([
            LLMMessage(role="system", content="你是一个专业的中文知识图谱实体关系抽取器"),
            LLMMessage(role="user", content="OpenAI 发布了 GPT-4，该模型基于 Transformer 架构"),
        ])
        assert body["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_deepseek_thinking_enabled_for_complex_question(self) -> None:
        """Complex reasoning questions should enable thinking mode."""
        config = LLMConfig(provider=LLMProviderType.DEEPSEEK, api_key="sk-ds")
        provider = OpenAICompatibleProvider(config)
        body = provider._build_body([
            LLMMessage(role="user", content="请分析 Transformer 和 RNN 在长文本处理上的优缺点，并解释为什么 Transformer 更适合并行计算"),
        ])
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_deepseek_thinking_disabled_for_factual_prefix(self) -> None:
        """Questions starting with factual prefixes should not enable thinking."""
        config = LLMConfig(provider=LLMProviderType.DEEPSEEK, api_key="sk-ds")
        provider = OpenAICompatibleProvider(config)
        body = provider._build_body([
            LLMMessage(role="user", content="什么是向量数据库"),
        ])
        assert body["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_deepseek_handles_reasoning_content(self) -> None:
        """DeepSeek V4 may return reasoning_content; content field should be used."""
        config = LLMConfig(provider=LLMProviderType.DEEPSEEK, api_key="sk-ds")
        provider = OpenAICompatibleProvider(config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Final answer",
                    "reasoning_content": "Chain of thought...",
                },
                "finish_reason": "stop",
            }],
            "model": "deepseek-v4-flash",
        }
        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            resp = await provider.generate([LLMMessage(role="user", content="Hi")])

        assert resp.content == "Final answer"
        assert resp.provider == "deepseek"

    @pytest.mark.asyncio
    async def test_custom_base_url(self) -> None:
        config = LLMConfig(
            provider=LLMProviderType.VLLM,
            api_base="http://custom:8080/v1",
        )
        provider = OpenAICompatibleProvider(config)
        assert provider._base_url == "http://custom:8080/v1"

    @pytest.mark.asyncio
    async def test_api_key_in_header(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        assert provider._client.headers.get("Authorization") == "Bearer sk-test-key"

    @pytest.mark.asyncio
    async def test_no_api_key_for_ollama(self) -> None:
        config = LLMConfig(provider=LLMProviderType.OLLAMA)
        provider = OpenAICompatibleProvider(config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "model": "qwen3:0.6b",
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            await provider.generate([LLMMessage(role="user", content="test")])

            call_kwargs = mock_client.post.call_args
            headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
            assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# AnthropicProvider — unit tests with mocked httpx
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    @pytest.fixture()
    def anthropic_config(self) -> LLMConfig:
        return LLMConfig(
            provider=LLMProviderType.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-test",
            temperature=0.7,
            max_tokens=2048,
        )

    @pytest.mark.asyncio
    async def test_generate_success(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "msg_123",
            "content": [{"type": "text", "text": "Hello from Claude!"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [LLMMessage(role="user", content="Hi")]
            resp = await provider.generate(messages)

        assert resp.content == "Hello from Claude!"
        assert resp.model == "claude-sonnet-4-20250514"
        assert resp.finish_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_generate_api_error_raises(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": {"type": "invalid_request_error", "message": "Bad request"}
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            with pytest.raises(RAGError) as exc_info:
                await provider.generate([LLMMessage(role="user", content="Hi")])

        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_generate_non_json_error_body(self, anthropic_config: LLMConfig) -> None:
        """Anthropic should not crash when error body is not valid JSON."""
        provider = AnthropicProvider(anthropic_config)
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.content = b"Gateway Timeout"
        mock_response.json.side_effect = json.JSONDecodeError("expecting value", "", 0)
        mock_response.text = "Gateway Timeout"

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            with pytest.raises(RAGError) as exc_info:
                await provider.generate([LLMMessage(role="user", content="Hi")])

        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_system_message_extracted(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "msg_1",
            "content": [{"type": "text", "text": "Response"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 1},
            "stop_reason": "end_turn",
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [
                LLMMessage(role="system", content="Be helpful."),
                LLMMessage(role="user", content="Hello"),
            ]
            await provider.generate(messages)

            call_kwargs = mock_client.post.call_args
            body = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
            assert body["system"] == "Be helpful."
            # System message should NOT be in the messages array
            assert all(m["role"] != "system" for m in body["messages"])

    @pytest.mark.asyncio
    async def test_default_base_url(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        assert provider._base_url == "https://api.anthropic.com"

    @pytest.mark.asyncio
    async def test_api_key_header(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        assert provider._client.headers.get("x-api-key") == "sk-ant-test"
        assert "anthropic-version" in provider._client.headers

    @pytest.mark.asyncio
    async def test_generate_stream_yields_content(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)

        mock_response = MagicMock()
        mock_response.status_code = 200

        lines = [
            'data: {"type":"message_start","message":{"id":"msg_1"}}',
            'data: {"type":"content_block_start","index":0}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" Claude"}}',
            'data: {"type":"content_block_stop","index":0}',
            'data: {"type":"message_delta","stop_reason":"end_turn"}',
            'data: {"type":"message_stop"}',
        ]

        async def mock_aiter_lines():
            for line in lines:
                yield line

        mock_response.aiter_lines = mock_aiter_lines

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [LLMMessage(role="user", content="Hi")]
            collected = []
            async for chunk in provider.generate_stream(messages):
                collected.append(chunk)

        assert collected == ["Hello", " Claude"]

    @pytest.mark.asyncio
    async def test_generate_stream_error(self, anthropic_config: LLMConfig) -> None:
        """Anthropic streaming should raise RAGError on non-200 status."""
        provider = AnthropicProvider(anthropic_config)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.content = b'{"error":"internal"}'
        mock_response.json.return_value = {"error": "internal"}

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            messages = [LLMMessage(role="user", content="Hi")]

            with pytest.raises(RAGError) as exc_info:
                async for _ in provider.generate_stream(messages):
                    pass

        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR
