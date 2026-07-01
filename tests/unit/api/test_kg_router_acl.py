"""Unit tests for per-dataset ACL gates on kg_stats / kg_neighbors.

The ``dataset`` query param scopes a request to a ``kg_{dataset}`` graph; when set,
the endpoint must enforce the per-dataset read ACL (IDOR prevention). When
``dataset`` is None (legacy default-graph path) the gate is skipped.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from arrow_lake.api.routers.knowledge_graph import kg_neighbors, kg_stats


class _User:
    role = "viewer"


class _Checker:
    def __init__(self, *, allow: bool) -> None:
        self._allow = allow

    def check_dataset_access(self, *, role, dataset, action) -> bool:  # noqa: ANN001
        return self._allow


class _Lake:
    async def kg_stats(self, **kw):  # noqa: ANN003, ANN202
        return {"total_vertices": 0, "total_edges": 0}

    async def kg_get_neighbors(self, *args, **kw):  # noqa: ANN002, ANN003, ANN202
        return []


@pytest.mark.asyncio()
async def test_kg_stats_denied_dataset_returns_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await kg_stats(
            dataset="secret",
            lake=_Lake(),
            _user=_User(),
            checker=_Checker(allow=False),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio()
async def test_kg_stats_allowed_dataset_returns_response() -> None:
    resp = await kg_stats(
        dataset="ds",
        lake=_Lake(),
        _user=_User(),
        checker=_Checker(allow=True),
    )
    assert resp.total_vertices == 0
    assert resp.graph_enabled is True


@pytest.mark.asyncio()
async def test_kg_stats_no_dataset_skips_acl() -> None:
    # dataset=None preserves legacy default-graph behavior (no ACL gate).
    resp = await kg_stats(
        dataset=None,
        lake=_Lake(),
        _user=_User(),
        checker=_Checker(allow=False),
    )
    assert resp.total_vertices == 0


@pytest.mark.asyncio()
async def test_kg_neighbors_denied_dataset_returns_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await kg_neighbors(
            entity_id="1:a",
            dataset="secret",
            lake=_Lake(),
            _user=_User(),
            checker=_Checker(allow=False),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio()
async def test_kg_neighbors_no_dataset_skips_acl() -> None:
    resp = await kg_neighbors(
        entity_id="1:a",
        dataset=None,
        lake=_Lake(),
        _user=_User(),
        checker=_Checker(allow=False),
    )
    assert resp.neighbors == []


@pytest.mark.asyncio()
async def test_traverser_denied_dataset_returns_403() -> None:
    """All 8 traverser endpoints share _enforce_read_acl; verify via rays."""
    from arrow_lake.api.routers.knowledge_graph import _SimpleTraverseReq, traverse_rays

    with pytest.raises(HTTPException) as exc:
        await traverse_rays(
            _SimpleTraverseReq(source="1:a", dataset="secret"),
            lake=_Lake(),
            _user=_User(),
            checker=_Checker(allow=False),
        )
    assert exc.value.status_code == 403


def test_customized_step_rejects_bad_direction() -> None:
    """Customized traverser steps validate the direction enum (→ HTTP 422)."""
    from pydantic import ValidationError

    from arrow_lake.api.routers.knowledge_graph import _CustomizedReq

    with pytest.raises(ValidationError):
        _CustomizedReq(source="1:a", steps=[{"direction": "BOGUS", "labels": ["knows"]}])
    # valid direction is accepted
    _CustomizedReq(source="1:a", steps=[{"direction": "OUT", "labels": ["knows"]}])


def test_multi_node_rejects_oversized_lists() -> None:
    """Multi-node traverser caps sources/targets at 100 (DoS bound, → HTTP 422)."""
    from pydantic import ValidationError

    from arrow_lake.api.routers.knowledge_graph import _MultiNodeReq

    with pytest.raises(ValidationError):
        _MultiNodeReq(sources=["x"] * 101, targets=["y"])
    with pytest.raises(ValidationError):
        _MultiNodeReq(sources=["x"], targets=["y"] * 101)
    # boundary: exactly 100 is accepted
    _MultiNodeReq(sources=["x"] * 100, targets=["y"] * 100)
