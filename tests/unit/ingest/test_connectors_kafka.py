"""Unit tests for Kafka connector — Daft Phase 2, Sprint 6."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.exceptions import IngestError
from arrow_lake.ingest.connectors_kafka import KafkaConnector


class TestKafkaConnectorInit:
    def test_defaults(self) -> None:
        c = KafkaConnector("localhost:9092")
        assert c._bootstrap_servers == "localhost:9092"
        assert c._group_id == "arrow-lake-kafka-reader"

    def test_custom_group(self) -> None:
        c = KafkaConnector("kafka:9092", group_id="my-group")
        assert c._group_id == "my-group"


class TestKafkaConnectorRead:
    def test_read_returns_dataframe(self) -> None:
        mock_df = MagicMock()
        with patch("daft.read_kafka", return_value=mock_df) as mock_dk:
            c = KafkaConnector("localhost:9092")
            result = c.read(topics=["test-topic"], start="earliest", end="latest")
            mock_dk.assert_called_once()
            assert result is mock_df

    def test_read_failure_raises(self) -> None:
        c = KafkaConnector("nonexistent:9092")
        with patch("daft.read_kafka", side_effect=RuntimeError("connection refused")):
            with pytest.raises(IngestError, match="Kafka read failed"):
                c.read(topics=["test"])

    def test_read_single_topic_string(self) -> None:
        mock_df = MagicMock()
        with patch("daft.read_kafka", return_value=mock_df) as mock_dk:
            c = KafkaConnector("localhost:9092")
            c.read(topics="my-topic")
            mock_dk.assert_called_once()
            call_kwargs = mock_dk.call_args
            assert call_kwargs.kwargs["topics"] == "my-topic"

    def test_read_multi_topic_list(self) -> None:
        mock_df = MagicMock()
        with patch("daft.read_kafka", return_value=mock_df) as mock_dk:
            c = KafkaConnector("localhost:9092")
            c.read(topics=["topic-a", "topic-b"])
            call_kwargs = mock_dk.call_args
            assert call_kwargs.kwargs["topics"] == ["topic-a", "topic-b"]
