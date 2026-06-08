"""Cover missing lines in arrow_lake.knowledge_graph.client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import KGError
from arrow_lake.knowledge_graph.client import HugeGraphClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg() -> HugeGraphConfig:
    return HugeGraphConfig(
        host="localhost",
        port=8080,
        graph_name="test_graph",
        username="admin",
        password="pass",
    )


def _client() -> HugeGraphClient:
    with patch.object(HugeGraphClient, "__init__", lambda self, cfg: None):
        c = HugeGraphClient.__new__(HugeGraphClient)
        c._config = _cfg()
        c._base_url = "http://localhost:8080"
        c._graph_base = "/graphspaces/DEFAULT/graphs/test_graph"
        c._client = AsyncMock()
        return c


# ---------------------------------------------------------------------------
# _delete / _handle_http_error
# ---------------------------------------------------------------------------


class TestHttpMethods:
    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        c = _client()
        c._client.delete.return_value = MagicMock(status_code=200)
        resp = await c._delete("/test")
        assert resp.status_code == 200

    def test_handle_http_error(self) -> None:
        c = _client()
        with pytest.raises(KGError, match="HTTP error"):
            c._handle_http_error(httpx.ConnectError("conn"))


# ---------------------------------------------------------------------------
# gremlin
# ---------------------------------------------------------------------------


class TestGremlin:
    @pytest.mark.asyncio
    async def test_http_error(self) -> None:
        c = _client()
        c._client.post.side_effect = httpx.ConnectError("err")
        with pytest.raises(KGError, match="HTTP error"):
            await c.gremlin("g.V()")


# ---------------------------------------------------------------------------
# add_vertices / get_vertex / add_edges
# ---------------------------------------------------------------------------


class TestVertexOps:
    @pytest.mark.asyncio
    async def test_add_vertices_http_error(self) -> None:
        c = _client()
        c._client.post.side_effect = httpx.ConnectError("err")
        with pytest.raises(KGError):
            await c.add_vertices([{"label": "person"}])

    @pytest.mark.asyncio
    async def test_add_vertices_bad_status(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"
        c._client.post.return_value = mock_resp
        with pytest.raises(KGError, match="Batch vertex insert"):
            await c.add_vertices([{"label": "person"}])

    @pytest.mark.asyncio
    async def test_get_vertex_unsafe_id(self) -> None:
        c = _client()
        result = await c.get_vertex("../../etc")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_vertex_http_error(self) -> None:
        c = _client()
        c._client.get.side_effect = httpx.ConnectError("err")
        result = await c.get_vertex("v1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_vertex_not_found(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        c._client.get.return_value = mock_resp
        result = await c.get_vertex("v1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_vertex_other_status(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        c._client.get.return_value = mock_resp
        result = await c.get_vertex("v1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_vertex_success(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "v1"}
        c._client.get.return_value = mock_resp
        result = await c.get_vertex("v1")
        assert result == {"id": "v1"}


class TestEdgeOps:
    @pytest.mark.asyncio
    async def test_add_edges_http_error(self) -> None:
        c = _client()
        c._client.post.side_effect = httpx.ConnectError("err")
        with pytest.raises(KGError):
            await c.add_edges([{"label": "knows"}])

    @pytest.mark.asyncio
    async def test_add_edges_bad_status(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"
        c._client.post.return_value = mock_resp
        with pytest.raises(KGError, match="Batch edge"):
            await c.add_edges([{"label": "knows"}])

    @pytest.mark.asyncio
    async def test_add_edges_success(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = [{"id": "e1"}, {"id": "e2"}]
        c._client.post.return_value = mock_resp
        count = await c.add_edges([{"label": "knows"}])
        assert count == 2

    @pytest.mark.asyncio
    async def test_add_edges_non_list(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = "not a list"
        c._client.post.return_value = mock_resp
        count = await c.add_edges([{"label": "knows"}])
        assert count == 0


# ---------------------------------------------------------------------------
# ensure_graph
# ---------------------------------------------------------------------------


class TestEnsureGraph:
    @pytest.mark.asyncio
    async def test_already_exists(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", return_value=["test_graph"]):
            result = await c.ensure_graph()
        assert result is True

    @pytest.mark.asyncio
    async def test_list_graphs_error(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", side_effect=ConnectionError("err")):
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            c._client.post.return_value = mock_resp
            result = await c.ensure_graph()
        assert result is True

    @pytest.mark.asyncio
    async def test_create_success(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", return_value=[]):
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            c._client.post.return_value = mock_resp
            result = await c.ensure_graph()
        assert result is True

    @pytest.mark.asyncio
    async def test_create_bad_status(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", return_value=[]):
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "err"
            c._client.post.return_value = mock_resp
            result = await c.ensure_graph()
        assert result is False

    @pytest.mark.asyncio
    async def test_create_exception(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", return_value=[]):
            c._client.post.side_effect = ConnectionError("err")
            result = await c.ensure_graph()
        assert result is False


# ---------------------------------------------------------------------------
# list_graphs / graph_exists
# ---------------------------------------------------------------------------


class TestListGraphs:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"graphs": ["g1", "g2"]}
        c._client.get.return_value = mock_resp
        result = await c.list_graphs()
        assert result == ["g1", "g2"]


class TestGraphExists:
    @pytest.mark.asyncio
    async def test_exists(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", return_value=["test_graph"]):
            assert await c.graph_exists() is True

    @pytest.mark.asyncio
    async def test_not_exists(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", return_value=["other"]):
            assert await c.graph_exists() is False

    @pytest.mark.asyncio
    async def test_error(self) -> None:
        c = _client()
        with patch.object(c, "list_graphs", side_effect=ConnectionError):
            assert await c.graph_exists() is False


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    @pytest.mark.asyncio
    async def test_graph_not_exists(self) -> None:
        c = _client()
        with patch.object(c, "graph_exists", return_value=False):
            await c.clear()  # no-op

    @pytest.mark.asyncio
    async def test_clear_post_success(self) -> None:
        c = _client()
        with patch.object(c, "graph_exists", return_value=True):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            c._client.post.return_value = mock_resp
            await c.clear()

    @pytest.mark.asyncio
    async def test_clear_post_fails_then_delete(self) -> None:
        c = _client()
        with patch.object(c, "graph_exists", return_value=True):
            c._client.post.side_effect = httpx.HTTPError("err")
            mock_del = MagicMock()
            mock_del.status_code = 200
            c._client.delete.return_value = mock_del
            await c.clear()

    @pytest.mark.asyncio
    async def test_clear_delete_fails(self) -> None:
        c = _client()
        with patch.object(c, "graph_exists", return_value=True):
            c._client.post.side_effect = httpx.HTTPError("err")
            c._client.delete.side_effect = httpx.HTTPError("err2")
            with pytest.raises(KGError, match="Failed to clear"):
                await c.clear()


# ---------------------------------------------------------------------------
# ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    @pytest.mark.asyncio
    async def test_property_key_error(self) -> None:
        c = _client()
        with patch.object(c, "ensure_graph", return_value=True):
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_resp.text = "forbidden"
            c._client.post.return_value = mock_resp
            with pytest.raises(KGError, match="PropertyKey"):
                await c.ensure_schema({
                    "property_keys": [{"name": "pk1", "data_type": "TEXT", "cardinality": "SINGLE"}]
                })

    @pytest.mark.asyncio
    async def test_vertex_label_error(self) -> None:
        c = _client()
        with patch.object(c, "ensure_graph", return_value=True):
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_resp.text = "forbidden"
            c._client.post.return_value = mock_resp
            with pytest.raises(KGError, match="VertexLabel"):
                await c.ensure_schema({
                    "vertex_labels": [{"name": "vl1"}]
                })

    @pytest.mark.asyncio
    async def test_edge_label_error(self) -> None:
        c = _client()
        with patch.object(c, "ensure_graph", return_value=True):
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_resp.text = "forbidden"
            c._client.post.return_value = mock_resp
            with pytest.raises(KGError, match="EdgeLabel"):
                await c.ensure_schema({
                    "edge_labels": [{"name": "el1", "source_label": "a", "target_label": "b"}]
                })

    @pytest.mark.asyncio
    async def test_index_label_error(self) -> None:
        c = _client()
        with patch.object(c, "ensure_graph", return_value=True):
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_resp.text = "forbidden"
            c._client.post.return_value = mock_resp
            with pytest.raises(KGError, match="IndexLabel"):
                await c.ensure_schema({
                    "index_labels": [{"name": "il1"}]
                })

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        c = _client()
        with patch.object(c, "ensure_graph", return_value=True):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            c._client.post.return_value = mock_resp
            await c.ensure_schema({
                "property_keys": [{"name": "pk1"}],
                "vertex_labels": [{"name": "vl1"}],
                "edge_labels": [{"name": "el1"}],
                "index_labels": [{"name": "il1"}],
            })


# ---------------------------------------------------------------------------
# get_schema
# ---------------------------------------------------------------------------


class TestGetSchema:
    @pytest.mark.asyncio
    async def test_http_error(self) -> None:
        c = _client()
        c._client.get.side_effect = httpx.ConnectError("err")
        with pytest.raises(KGError):
            await c.get_schema()

    @pytest.mark.asyncio
    async def test_bad_status(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"
        c._client.get.return_value = mock_resp
        with pytest.raises(KGError, match="Failed to get schema"):
            await c.get_schema()

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        c = _client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"vertex_labels": [], "edge_labels": []}
        c._client.get.return_value = mock_resp
        result = await c.get_schema()
        assert "vertex_labels" in result


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        c = _client()
        v_resp = MagicMock()
        v_resp.status_code = 200
        v_resp.json.return_value = {"vertices": [{"id": 1}]}
        e_resp = MagicMock()
        e_resp.status_code = 200
        e_resp.json.return_value = {"edges": [{"id": 1}, {"id": 2}]}
        c._client.get.side_effect = [v_resp, e_resp]
        result = await c.get_stats()
        assert result["total_vertices"] == 1
        assert result["total_edges"] == 2

    @pytest.mark.asyncio
    async def test_error(self) -> None:
        c = _client()
        # _get is decorated with @retry, so patch the underlying httpx client
        # to raise ConnectionError (builtin) which get_stats catches
        c._client.get.side_effect = ConnectionError("err")
        result = await c.get_stats()
        assert result["total_vertices"] == 0
        assert result["total_edges"] == 0


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self) -> None:
        c = _client()
        await c.close()
        c._client.aclose.assert_called_once()
