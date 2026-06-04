"""Unit tests for SQL connector — Daft Phase 2, Sprint 5."""

from __future__ import annotations

import ipaddress
import socket
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.exceptions import IngestError
from arrow_lake.ingest.connectors_sql import (
    SqlConnector,
    _is_private_ip,
    _validate_connection_url,
    _validate_sql_readonly,
)


class TestValidateSqlReadonly:
    def test_select_allowed(self) -> None:
        _validate_sql_readonly("SELECT * FROM t")

    def test_with_cte_allowed(self) -> None:
        _validate_sql_readonly("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_insert_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("INSERT INTO t VALUES (1)")

    def test_update_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("UPDATE t SET x = 1")

    def test_delete_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("DELETE FROM t")

    def test_drop_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("DROP TABLE t")

    def test_non_select_prefix_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT"):
            _validate_sql_readonly("DESCRIBE t")

    def test_select_with_insert_subquery_rejected(self) -> None:
        with pytest.raises(IngestError, match="forbidden"):
            _validate_sql_readonly("SELECT * FROM t; INSERT INTO t VALUES (1)")


class TestSqlConnectorRead:
    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    def test_read_from_sqlite(self, _mock_validate) -> None:
        db = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE items(id INTEGER, name TEXT)")
        conn.execute("INSERT INTO items VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        conn.commit()
        conn.close()

        try:
            connector = SqlConnector(f"sqlite:///{db}")
            df = connector.read("SELECT * FROM items")
            table = df.to_arrow()
            assert table.num_rows == 3
            assert "id" in table.column_names
            assert "name" in table.column_names
        finally:
            Path(db).unlink(missing_ok=True)

    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    def test_invalid_connection_raises(self, _mock_validate) -> None:
        connector = SqlConnector("sqlite:///nonexistent/path/db.sqlite")
        with patch("daft.read_sql", side_effect=RuntimeError("unable to open database file")):
            with pytest.raises(IngestError, match="SQL read failed"):
                connector.read("SELECT * FROM t")

    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    def test_safe_url_masks_password(self, _mock_validate) -> None:
        connector = SqlConnector("postgresql://user:secret@dbhost:5432/mydb")
        safe = connector._safe_url()
        assert "secret" not in safe
        assert "***" in safe

    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    def test_safe_url_no_credentials(self, _mock_validate) -> None:
        connector = SqlConnector("sqlite:///test.db")
        assert connector._safe_url() == "sqlite:///test.db"


class TestIsPrivateIp:
    """Cover _is_private_ip() for all network ranges in _PRIVATE_NETWORKS."""

    def test_loopback_ipv4(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("127.0.0.1")) is True

    def test_loopback_ipv4_high(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("127.255.255.255")) is True

    def test_class_a_private(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("10.0.0.1")) is True

    def test_class_a_private_high(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("10.255.255.255")) is True

    def test_class_b_private(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("172.16.0.1")) is True

    def test_class_b_private_upper_bound(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("172.31.255.255")) is True

    def test_class_c_private(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("192.168.1.1")) is True

    def test_link_local(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("169.254.1.1")) is True

    def test_zero_network(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("0.0.0.1")) is True

    def test_carrier_grade_nat(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("100.64.0.1")) is True

    def test_ipv6_loopback(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("::1")) is True

    def test_ipv4_mapped_loopback(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("::ffff:127.0.0.1")) is True

    def test_ipv6_unique_local(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("fc00::1")) is True

    def test_ipv6_link_local(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("fe80::1")) is True

    def test_public_ipv4(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("8.8.8.8")) is False

    def test_public_ipv4_another(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("1.1.1.1")) is False

    def test_public_ipv6(self) -> None:
        assert _is_private_ip(ipaddress.ip_address("2001:4860:4860::8888")) is False

    def test_class_b_outside_range(self) -> None:
        # 172.15.x.x and 172.32.x.x are NOT in 172.16.0.0/12
        assert _is_private_ip(ipaddress.ip_address("172.15.255.255")) is False
        assert _is_private_ip(ipaddress.ip_address("172.32.0.0")) is False


class TestValidateConnectionUrl:
    """Cover _validate_connection_url() SSRF guard branches."""

    def test_missing_hostname(self) -> None:
        with pytest.raises(IngestError, match="must contain a hostname"):
            _validate_connection_url("sqlite:///relative/path.db")

    def test_private_ip_hostname_direct(self) -> None:
        with pytest.raises(IngestError, match="private/internal IP"):
            _validate_connection_url("postgresql://127.0.0.1:5432/mydb")

    def test_private_ip_10_range(self) -> None:
        with pytest.raises(IngestError, match="private/internal IP"):
            _validate_connection_url("mysql://10.0.0.1:3306/testdb")

    def test_private_ip_192_range(self) -> None:
        with pytest.raises(IngestError, match="private/internal IP"):
            _validate_connection_url("postgresql://user:pass@192.168.1.1/db")

    def test_private_ip_172_range(self) -> None:
        with pytest.raises(IngestError, match="private/internal IP"):
            _validate_connection_url("mysql://172.16.0.1/db")

    def test_private_ip_link_local(self) -> None:
        with pytest.raises(IngestError, match="private/internal IP"):
            _validate_connection_url("postgresql://169.254.1.1:5432/db")

    def test_dns_resolution_fails(self) -> None:
        with patch(
            "arrow_lake.ingest.connectors_sql.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            with pytest.raises(IngestError, match="Cannot resolve"):
                _validate_connection_url("postgresql://nonexistent.invalid:5432/db")

    def test_dns_resolves_to_private_ip(self) -> None:
        # Simulate DNS returning a loopback address
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5432)),
        ]
        with patch(
            "arrow_lake.ingest.connectors_sql.socket.getaddrinfo",
            return_value=fake_addrinfo,
        ):
            with pytest.raises(IngestError, match="resolves to private IP"):
                _validate_connection_url("postgresql://internal.company.local:5432/db")

    def test_dns_resolves_to_public_ip_passes(self) -> None:
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 5432)),
        ]
        with patch(
            "arrow_lake.ingest.connectors_sql.socket.getaddrinfo",
            return_value=fake_addrinfo,
        ):
            # Should not raise — public IP is allowed
            _validate_connection_url("postgresql://example.com:5432/mydb")

    def test_dns_resolves_to_ipv6_loopback(self) -> None:
        fake_addrinfo = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 5432, 0, 0)),
        ]
        with patch(
            "arrow_lake.ingest.connectors_sql.socket.getaddrinfo",
            return_value=fake_addrinfo,
        ):
            with pytest.raises(IngestError, match="resolves to private IP"):
                _validate_connection_url("postgresql://localhost:5432/db")

    def test_public_ip_hostname_passes(self) -> None:
        # Direct public IP in URL should pass validation
        _validate_connection_url("postgresql://8.8.8.8:5432/mydb")

    def test_oserror_during_dns(self) -> None:
        with patch(
            "arrow_lake.ingest.connectors_sql.socket.getaddrinfo",
            side_effect=OSError("Network unreachable"),
        ):
            with pytest.raises(IngestError, match="Cannot resolve"):
                _validate_connection_url("mysql://somehost:3306/db")


class TestSqlConnectorPartitions:
    """Cover SqlConnector.read() partition_col / num_partitions kwargs."""

    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    @patch("arrow_lake.ingest.connectors_sql.daft.read_sql")
    @patch("arrow_lake.ingest.connectors_sql._validate_sql_readonly")
    def test_partition_col_passed_to_daft(
        self, mock_validate_sql, mock_read_sql, mock_validate_url
    ) -> None:
        mock_read_sql.return_value = MagicMock()
        connector = SqlConnector(
            "postgresql://host/db", partition_col="created_at"
        )
        connector.read("SELECT * FROM t")
        mock_read_sql.assert_called_once_with(
            "SELECT * FROM t",
            "postgresql://host/db",
            partition_col="created_at",
        )

    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    @patch("arrow_lake.ingest.connectors_sql.daft.read_sql")
    @patch("arrow_lake.ingest.connectors_sql._validate_sql_readonly")
    def test_partition_col_and_num_partitions_passed(
        self, mock_validate_sql, mock_read_sql, mock_validate_url
    ) -> None:
        mock_read_sql.return_value = MagicMock()
        connector = SqlConnector(
            "postgresql://host/db",
            partition_col="id",
            num_partitions=10,
        )
        connector.read("SELECT * FROM t")
        mock_read_sql.assert_called_once_with(
            "SELECT * FROM t",
            "postgresql://host/db",
            partition_col="id",
            num_partitions=10,
        )

    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    @patch("arrow_lake.ingest.connectors_sql.daft.read_sql")
    @patch("arrow_lake.ingest.connectors_sql._validate_sql_readonly")
    def test_no_partition_kwargs_when_not_set(
        self, mock_validate_sql, mock_read_sql, mock_validate_url
    ) -> None:
        mock_read_sql.return_value = MagicMock()
        connector = SqlConnector("postgresql://host/db")
        connector.read("SELECT * FROM t")
        mock_read_sql.assert_called_once_with(
            "SELECT * FROM t",
            "postgresql://host/db",
        )

    @patch("arrow_lake.ingest.connectors_sql._validate_connection_url")
    @patch("arrow_lake.ingest.connectors_sql.daft.read_sql")
    @patch("arrow_lake.ingest.connectors_sql._validate_sql_readonly")
    def test_num_partitions_passed_without_partition_col(
        self, mock_validate_sql, mock_read_sql, mock_validate_url
    ) -> None:
        mock_read_sql.return_value = MagicMock()
        connector = SqlConnector(
            "postgresql://host/db", num_partitions=5
        )
        connector.read("SELECT * FROM t")
        # Source code passes num_partitions independently of partition_col
        mock_read_sql.assert_called_once_with(
            "SELECT * FROM t",
            "postgresql://host/db",
            num_partitions=5,
        )
