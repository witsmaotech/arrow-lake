"""Unit tests for TurboOCR HTTP client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from arrow_lake.exceptions import DocumentError, ErrorCode
from arrow_lake.ingest.ocr import TurboOcrClient, TurboOcrResult, _CircuitBreaker

# ---------------------------------------------------------------------------
# Circuit breaker tests (no HTTP — pure logic)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_initially_allows(self):
        cb = _CircuitBreaker()
        assert cb.allow() is True

    def test_opens_after_threshold(self):
        cb = _CircuitBreaker(failure_threshold=3, reset_timeout=60.0)
        for _ in range(3):
            cb.record_failure()
        assert cb.allow() is False

    def test_resets_after_timeout(self):
        cb = _CircuitBreaker(failure_threshold=2, reset_timeout=0.001)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow() is False
        import time
        time.sleep(0.002)
        assert cb.allow() is True

    def test_success_resets_counter(self):
        cb = _CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0
        assert cb.allow() is True


# ---------------------------------------------------------------------------
# TurboOcrClient tests
# ---------------------------------------------------------------------------


class TestTurboOcrClient:
    def test_default_endpoint(self):
        client = TurboOcrClient()
        assert client.endpoint == "http://localhost:8002"

    def test_loopback_ip_blocked(self):
        with pytest.raises(ValueError, match="private IP"):
            TurboOcrClient(endpoint="http://127.0.0.1:9000")

    def test_ssrf_blocks_private_ip(self):
        with pytest.raises(ValueError, match="private IP"):
            TurboOcrClient(endpoint="http://192.168.1.100:8002")

    def test_ssrf_blocks_10_range(self):
        with pytest.raises(ValueError, match="private IP"):
            TurboOcrClient(endpoint="http://10.0.0.5:8002")

    def test_ssrf_blocks_172_range(self):
        with pytest.raises(ValueError, match="private IP"):
            TurboOcrClient(endpoint="http://172.16.0.5:8002")

    def test_ssrf_blocks_non_http_scheme(self):
        with pytest.raises(ValueError, match="http/https scheme"):
            TurboOcrClient(endpoint="ftp://localhost:8002")

    def test_ssrf_allows_localhost(self):
        client = TurboOcrClient(endpoint="http://localhost:9999")
        assert client.endpoint == "http://localhost:9999"

    def test_ssrf_blocks_link_local_169254(self):
        with pytest.raises(ValueError, match="private IP"):
            TurboOcrClient(endpoint="http://169.254.169.254:8002")

    def test_ssrf_blocks_zero_network(self):
        with pytest.raises(ValueError, match="private IP"):
            TurboOcrClient(endpoint="http://0.0.0.0:8002")

    def test_is_available_unhealthy(self):
        with patch.object(httpx, "get", side_effect=ConnectionError("refused")):
            client = TurboOcrClient()
            assert client.is_available() is False

    def test_is_available_circuit_open(self):
        client = TurboOcrClient()
        for _ in range(5):
            client._circuit.record_failure()
        import time
        client._circuit._last_failure = time.monotonic() + 9999
        assert client.is_available() is False

    def test_ocr_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "text": "Extracted text content",
            "page_count": 3,
            "confidence": 0.95,
        }
        with patch.object(httpx, "post", return_value=mock_response):
            client = TurboOcrClient()
            result = client.ocr(b"fake pdf bytes", filename="test.pdf")
        assert isinstance(result, TurboOcrResult)
        assert result.text == "Extracted text content"
        assert result.page_count == 3
        assert result.confidence == 0.95

    def test_ocr_success_minimal_json(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"text": "hello"}
        with patch.object(httpx, "post", return_value=mock_response):
            client = TurboOcrClient()
            result = client.ocr(b"pdf", filename="f.pdf")
        assert result.text == "hello"
        assert result.page_count == 1
        assert result.confidence == 0.0

    def test_ocr_connect_error_retries(self):
        with patch.object(httpx, "post", side_effect=httpx.ConnectError("refused")):
            client = TurboOcrClient(max_retries=2, retry_base_delay=0.01)
            with pytest.raises(DocumentError) as exc_info:
                client.ocr(b"fake pdf")
            assert exc_info.value.error_code == ErrorCode.DOCUMENT_OCR_FAILED
            assert "after 2 attempts" in exc_info.value.message

    def test_ocr_circuit_open_blocks(self):
        client = TurboOcrClient()
        for _ in range(5):
            client._circuit.record_failure()
        import time
        client._circuit._last_failure = time.monotonic() + 9999
        with pytest.raises(DocumentError) as exc_info:
            client.ocr(b"fake pdf")
        assert "circuit breaker is open" in exc_info.value.message

    def test_ocr_http_error_no_retry(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response,
        )
        with patch.object(httpx, "post", return_value=mock_response):
            client = TurboOcrClient(max_retries=3)
            with pytest.raises(DocumentError) as exc_info:
                client.ocr(b"fake pdf")
            assert exc_info.value.error_code == ErrorCode.DOCUMENT_OCR_FAILED
