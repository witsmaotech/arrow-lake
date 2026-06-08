"""Comprehensive tests for RAG provider.py — targeting uncovered paths.

Covers:
- _safe_json_body edge cases
- _QuestionComplexity heuristic branches
- _RetryMixin._request_no_retry
- OpenAICompatibleProvider: circuit breaker, close, DeepSeek build_body,
  Ollama chat_template_kwargs, streaming edge cases (bad JSON, empty lines, non-DONE data)
- AnthropicProvider: build_body with/without system, temperature, streaming edge cases,
  usage mapping, close, circuit breaker for stream
- create_llm_provider: unsupported provider type
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arrow_lake.config import LLMConfig, LLMProviderType
from arrow_lake.exceptions import ErrorCode, RAGError
from arrow_lake.rag.provider import (
    AnthropicProvider,
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    OpenAICompatibleProvider,
    _QuestionComplexity,
    _safe_json_body,
    create_llm_provider,
)


# ---------------------------------------------------------------------------
# _safe_json_body
# ---------------------------------------------------------------------------


class TestSafeJsonBody:
    """Test _safe_json_body helper for error response parsing."""

    def test_empty_content_returns_empty_dict(self) -> None:
        resp = MagicMock()
        resp.content = b""
        assert _safe_json_body(resp) == {}

    def test_none_content_returns_empty_dict(self) -> None:
        resp = MagicMock()
        resp.content = None
        assert _safe_json_body(resp) == {}

    def test_valid_json_parsed(self) -> None:
        resp = MagicMock()
        resp.content = b'{"error": "rate_limited"}'
        resp.json.return_value = {"error": "rate_limited"}
        result = _safe_json_body(resp)
        assert result == {"error": "rate_limited"}

    def test_invalid_json_falls_back_to_raw_text(self) -> None:
        resp = MagicMock()
        resp.content = b"<html>Bad Gateway</html>"
        resp.json.side_effect = json.JSONDecodeError("expecting value", "", 0)
        resp.text = "<html>Bad Gateway</html>"
        result = _safe_json_body(resp)
        assert "raw" in result
        assert "Bad Gateway" in result["raw"]

    def test_value_error_falls_back_to_raw_text(self) -> None:
        resp = MagicMock()
        resp.content = b"some text"
        resp.json.side_effect = ValueError("bad value")
        resp.text = "some text"
        result = _safe_json_body(resp)
        assert "raw" in result

    def test_raw_text_truncated_at_200_chars(self) -> None:
        resp = MagicMock()
        long_text = "x" * 500
        resp.content = long_text.encode()
        resp.json.side_effect = json.JSONDecodeError("err", "", 0)
        resp.text = long_text
        result = _safe_json_body(resp)
        assert len(result["raw"]) == 200


# ---------------------------------------------------------------------------
# _QuestionComplexity
# ---------------------------------------------------------------------------


class TestQuestionComplexity:
    """Test the DeepSeek thinking mode heuristic classifier."""

    def test_empty_messages_returns_false(self) -> None:
        assert _QuestionComplexity.should_think([]) is False

    def test_no_user_messages_returns_false(self) -> None:
        messages = [LLMMessage(role="system", content="You are helpful.")]
        assert _QuestionComplexity.should_think(messages) is False

    def test_extraction_system_prompt_returns_false(self) -> None:
        messages = [
            LLMMessage(role="system", content="Extract entities in json format"),
            LLMMessage(role="user", content="This is a long enough question about something"),
        ]
        assert _QuestionComplexity.should_think(messages) is False

    def test_knowledge_graph_pattern_returns_false(self) -> None:
        messages = [
            LLMMessage(role="system", content="知识图谱 entity extraction"),
            LLMMessage(role="user", content="A" * 20),
        ]
        assert _QuestionComplexity.should_think(messages) is False

    def test_ner_pattern_returns_false(self) -> None:
        messages = [
            LLMMessage(role="system", content="NER named entity recognition"),
            LLMMessage(role="user", content="A" * 20),
        ]
        assert _QuestionComplexity.should_think(messages) is False

    def test_short_question_returns_false(self) -> None:
        messages = [LLMMessage(role="user", content="Hi")]
        assert _QuestionComplexity.should_think(messages) is False

    def test_exactly_15_chars_still_thinks(self) -> None:
        # Exactly 15 chars (boundary), not less than 15
        messages = [LLMMessage(role="user", content="12345678901234分析")]
        # 15 chars, so len(question) < 15 is False
        result = _QuestionComplexity.should_think(messages)
        # Contains 分析 which is in _COMPLEX_QUESTION_PATTERNS
        assert result is True

    def test_simple_factual_prefix_returns_false(self) -> None:
        messages = [LLMMessage(role="user", content="什么是向量数据库，它的工作原理是什么")]
        assert _QuestionComplexity.should_think(messages) is False

    def test_who_is_prefix_returns_false(self) -> None:
        messages = [LLMMessage(role="user", content="是谁创造了这个伟大的发明")]
        assert _QuestionComplexity.should_think(messages) is False

    def test_list_prefix_returns_false(self) -> None:
        messages = [LLMMessage(role="user", content="列举一些常见的机器学习算法")]
        assert _QuestionComplexity.should_think(messages) is False

    def test_introduce_prefix_returns_false(self) -> None:
        messages = [LLMMessage(role="user", content="介绍一下深度学习的基本概念")]
        assert _QuestionComplexity.should_think(messages) is False

    def test_complex_why_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="请详细解释为什么 Transformer 架构比 RNN 更适合处理长序列")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_compare_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="请对比 DuckDB 和 ClickHouse 在 OLAP 场景下的优缺点")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_analyze_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="请分析一下这个系统架构的性能瓶颈在哪里")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_how_to_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="如何实现一个高可用的分布式向量搜索引擎")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_inference_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="根据这些数据推断出市场的主要趋势")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_evaluate_pattern_returns_true(self) -> None:
        # "判断" is in the complex patterns; test with a complex question
        messages = [LLMMessage(role="user", content="请判断这两种方案的优缺点并给出权衡建议")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_step_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="描述一下数据处理的完整步骤和流程")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_relationship_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="分析用户行为与购买转化之间的关系")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_reason_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="导致这个性能问题的主要原因是什么")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_tradeoff_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="在这两种方案之间做一个权衡分析")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_complex_why_bare_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="为什么选择这个技术栈的理由和依据")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_if_hypothesis_pattern_returns_true(self) -> None:
        messages = [LLMMessage(role="user", content="如果我们采用微服务架构，会有什么影响")]
        assert _QuestionComplexity.should_think(messages) is True

    def test_long_question_without_complex_patterns_returns_false(self) -> None:
        messages = [LLMMessage(role="user", content="The weather is very nice today and I want to go outside")]
        assert _QuestionComplexity.should_think(messages) is False


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider — additional coverage
# ---------------------------------------------------------------------------


class TestOpenAICompatibleProviderExtra:
    """Extra tests targeting uncovered provider paths."""

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
    async def test_circuit_breaker_open_raises(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = False
        provider._cb = mock_cb

        with pytest.raises(RAGError) as exc_info:
            await provider.generate([LLMMessage(role="user", content="Hi")])

        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR
        assert "circuit breaker OPEN" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        with patch.object(provider, "_client") as mock_client:
            mock_client.aclose = AsyncMock()
            await provider.close()
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_body_ollama_disables_thinking(self) -> None:
        config = LLMConfig(provider=LLMProviderType.OLLAMA, model="qwen3:0.6b")
        provider = OpenAICompatibleProvider(config)
        body = provider._build_body([LLMMessage(role="user", content="Hi")])
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    @pytest.mark.asyncio
    async def test_build_body_openai_no_special_fields(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        body = provider._build_body([LLMMessage(role="user", content="Hi")])
        assert "chat_template_kwargs" not in body
        assert "thinking" not in body
        assert "reasoning_effort" not in body

    @pytest.mark.asyncio
    async def test_generate_stream_http_error_raises(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(RAGError) as exc_info:
                async for _ in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                    pass

        assert "HTTP error in stream" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_generate_stream_non_200_raises(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = b'{"error":"internal"}'
        mock_resp.json.return_value = {"error": "internal"}

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            with pytest.raises(RAGError) as exc_info:
                async for _ in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                    pass

        assert "stream returned 500" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_generate_stream_skips_malformed_json(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        lines = [
            "data: {bad json",
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]

        async def _aiter():
            for line in lines:
                yield line

        mock_resp.aiter_lines = _aiter

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            collected = []
            async for chunk in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                collected.append(chunk)

        # v1.6.0: bad JSON yields [ERROR] and breaks stream
        assert len(collected) == 1
        assert "[ERROR]" in collected[0]

    @pytest.mark.asyncio
    async def test_generate_stream_skips_empty_lines(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        lines = [
            "",
            "   ",
            'data: {"choices":[{"delta":{"content":"text"}}]}',
            "data: [DONE]",
        ]

        async def _aiter():
            for line in lines:
                yield line

        mock_resp.aiter_lines = _aiter

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            collected = []
            async for chunk in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                collected.append(chunk)

        assert collected == ["text"]

    @pytest.mark.asyncio
    async def test_generate_stream_skips_missing_content_delta(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        lines = [
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{}}]}',
            'data: {"choices":[{"delta":{"content":"real"}}]}',
            "data: [DONE]",
        ]

        async def _aiter():
            for line in lines:
                yield line

        mock_resp.aiter_lines = _aiter

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            collected = []
            async for chunk in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                collected.append(chunk)

        assert collected == ["real"]

    @pytest.mark.asyncio
    async def test_generate_with_empty_content_field(self, openai_config: LLMConfig) -> None:
        """DeepSeek V4 may return empty content; should raise RAGError."""
        provider = OpenAICompatibleProvider(openai_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant"}, "finish_reason": "stop"}],
            "model": "gpt-4o-mini",
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            with pytest.raises(RAGError, match="empty content"):
                await provider.generate([LLMMessage(role="user", content="Hi")])

    @pytest.mark.asyncio
    async def test_build_body_includes_all_fields(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        messages = [
            LLMMessage(role="system", content="Be helpful"),
            LLMMessage(role="user", content="Hello"),
        ]
        body = provider._build_body(messages, stream=True)

        assert body["model"] == "gpt-4o-mini"
        assert body["temperature"] == 0.5
        assert body["max_tokens"] == 100
        assert body["stream"] is True
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_request_no_retry(self, openai_config: LLMConfig) -> None:
        provider = OpenAICompatibleProvider(openai_config)
        mock_resp = MagicMock()

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            result = await provider._request_no_retry({"test": True})
            assert result is mock_resp
            mock_client.post.assert_called_once()


# ---------------------------------------------------------------------------
# AnthropicProvider — additional coverage
# ---------------------------------------------------------------------------


class TestAnthropicProviderExtra:
    """Extra tests targeting uncovered Anthropic paths."""

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
    async def test_build_body_no_system_message(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        messages = [LLMMessage(role="user", content="Hi")]
        body = provider._build_body(messages)
        assert "system" not in body
        assert len(body["messages"]) == 1

    @pytest.mark.asyncio
    async def test_build_body_with_system_message(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        messages = [
            LLMMessage(role="system", content="Be helpful"),
            LLMMessage(role="user", content="Hi"),
        ]
        body = provider._build_body(messages)
        assert body["system"] == "Be helpful"
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_build_body_default_temperature_not_included(self) -> None:
        config = LLMConfig(
            provider=LLMProviderType.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-test",
            temperature=1.0,
            max_tokens=2048,
        )
        provider = AnthropicProvider(config)
        body = provider._build_body([LLMMessage(role="user", content="Hi")])
        assert "temperature" not in body

    @pytest.mark.asyncio
    async def test_build_body_non_default_temperature_included(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        body = provider._build_body([LLMMessage(role="user", content="Hi")])
        assert body["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_build_body_stream_flag(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        body = provider._build_body([LLMMessage(role="user", content="Hi")], stream=True)
        assert body["stream"] is True

    @pytest.mark.asyncio
    async def test_generate_usage_mapping(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "msg_1",
            "content": [{"type": "text", "text": "Answer"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "stop_reason": "end_turn",
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            resp = await provider.generate([LLMMessage(role="user", content="Hi")])

        assert resp.usage is not None
        assert resp.usage["prompt_tokens"] == 100
        assert resp.usage["completion_tokens"] == 50
        assert resp.usage["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_generate_no_usage(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "msg_1",
            "content": [{"type": "text", "text": "Answer"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            resp = await provider.generate([LLMMessage(role="user", content="Hi")])

        assert resp.usage is None

    @pytest.mark.asyncio
    async def test_generate_multiple_text_blocks(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "msg_1",
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "thinking", "text": "internal"},
                {"type": "text", "text": "Part 2"},
            ],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            resp = await provider.generate([LLMMessage(role="user", content="Hi")])

        assert resp.content == "Part 1\nPart 2"

    @pytest.mark.asyncio
    async def test_generate_http_error(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(RAGError) as exc_info:
                await provider.generate([LLMMessage(role="user", content="Hi")])

        assert "HTTP error calling Anthropic" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_generate(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = False
        provider._cb = mock_cb

        with pytest.raises(RAGError) as exc_info:
            await provider.generate([LLMMessage(role="user", content="Hi")])

        assert "circuit breaker OPEN" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_stream(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = False
        provider._cb = mock_cb

        with pytest.raises(RAGError) as exc_info:
            async for _ in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                pass

        assert "circuit breaker OPEN" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_stream_http_error(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(RAGError) as exc_info:
                async for _ in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                    pass

        assert "HTTP error in Anthropic stream" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_stream_non_200_raises(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = b'{"error":"internal"}'
        mock_resp.json.return_value = {"error": "internal"}

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            with pytest.raises(RAGError) as exc_info:
                async for _ in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                    pass

        assert "stream returned 500" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_stream_skips_non_text_delta(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        lines = [
            'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"..."}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}',
            'data: {"type":"message_stop"}',
        ]

        async def _aiter():
            for line in lines:
                yield line

        mock_resp.aiter_lines = _aiter

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            collected = []
            async for chunk in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                collected.append(chunk)

        assert collected == ["hello"]

    @pytest.mark.asyncio
    async def test_stream_skips_empty_text(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        lines = [
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":""}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"actual"}}',
            'data: {"type":"message_stop"}',
        ]

        async def _aiter():
            for line in lines:
                yield line

        mock_resp.aiter_lines = _aiter

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            collected = []
            async for chunk in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                collected.append(chunk)

        assert collected == ["actual"]

    @pytest.mark.asyncio
    async def test_stream_skips_malformed_json(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        lines = [
            "data: {invalid json",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}',
            'data: {"type":"message_stop"}',
        ]

        async def _aiter():
            for line in lines:
                yield line

        mock_resp.aiter_lines = _aiter

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            collected = []
            async for chunk in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                collected.append(chunk)

        # v1.6.0: bad JSON yields [ERROR] and breaks stream
        assert len(collected) == 1
        assert "[ERROR]" in collected[0]

    @pytest.mark.asyncio
    async def test_stream_skips_empty_lines(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        lines = [
            "",
            "   ",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}',
            'data: {"type":"message_stop"}',
        ]

        async def _aiter():
            for line in lines:
                yield line

        mock_resp.aiter_lines = _aiter

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            collected = []
            async for chunk in provider.generate_stream([LLMMessage(role="user", content="Hi")]):
                collected.append(chunk)

        assert collected == ["ok"]

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self, anthropic_config: LLMConfig) -> None:
        provider = AnthropicProvider(anthropic_config)
        with patch.object(provider, "_client") as mock_client:
            mock_client.aclose = AsyncMock()
            await provider.close()
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_base_url(self) -> None:
        config = LLMConfig(
            provider=LLMProviderType.ANTHROPIC,
            api_key="sk-ant-test",
            api_base="https://custom-anthropic.example.com",
        )
        provider = AnthropicProvider(config)
        assert provider._base_url == "https://custom-anthropic.example.com"

    @pytest.mark.asyncio
    async def test_custom_anthropic_version(self) -> None:
        config = LLMConfig(
            provider=LLMProviderType.ANTHROPIC,
            api_key="sk-ant-test",
        )
        config.anthropic_version = "2024-01-01"
        provider = AnthropicProvider(config)
        assert provider._client.headers.get("anthropic-version") == "2024-01-01"


# ---------------------------------------------------------------------------
# create_llm_provider — unsupported type
# ---------------------------------------------------------------------------


class TestCreateLLMProviderUnsupported:
    """Test factory with unsupported provider type."""

    def test_unsupported_provider_raises(self) -> None:
        config = MagicMock()
        # Use a MagicMock that won't match any case in the match statement
        config.provider = MagicMock()
        config.provider.value = "unknown_provider_xyz"
        with pytest.raises(RAGError) as exc_info:
            create_llm_provider(config)
        assert exc_info.value.error_code == ErrorCode.RAG_PROVIDER_ERROR


# ---------------------------------------------------------------------------
# BaseLLMProvider — close default
# ---------------------------------------------------------------------------


class TestBaseLLMProviderClose:
    """Test that default close() is a no-op."""

    @pytest.mark.asyncio
    async def test_default_close_is_noop(self) -> None:
        config = LLMConfig(provider=LLMProviderType.OPENAI)
        # Create a minimal concrete subclass
        class StubProvider(BaseLLMProvider):
            async def generate(self, messages):
                return LLMResponse(content="", model="m", provider="p")

            async def generate_stream(self, messages):
                return
                yield  # make it an async generator

        provider = StubProvider(config)
        # Should not raise
        await provider.close()


# ---------------------------------------------------------------------------
# Circuit Breaker lazy init
# ---------------------------------------------------------------------------


class TestCircuitBreakerLazyInit:
    """Test that circuit breaker is lazily initialized."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_lazy_creation(self) -> None:
        config = LLMConfig(provider=LLMProviderType.OPENAI, api_key="sk-test")
        provider = OpenAICompatibleProvider(config)
        assert provider._cb is None
        cb = provider._circuit_breaker()
        assert cb is not None
        assert provider._cb is cb  # cached after first call
