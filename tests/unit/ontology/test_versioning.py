"""W2.2 — ontology version snapshots:模板→shapes→V010(同 hash 跳过)+ diff。

契约(实施计划 §4 W2.2):
* build 成功后快照落库;同 ``source_hash`` 重复 build 不产生新版本;
* 内容变 → 新版本 + ``diff_json``(类/枚举/必填/type-pairs 增删);
* diff 从两版 Turtle 的结构化特征计算(快照形态即 Turtle,读回即比)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.ontology import OntologyVersionStore

# --- helpers ----------------------------------------------------------------


def _template_yaml(entity_enum: str = "[person, organization]",
                   relation_enum: str = "[works_at]",
                   extra: str = "") -> str:
    return f"""
name: ver_test_template
ontology:
  entity_type_enum: {entity_enum}
  relation_type_enum: {relation_enum}
  type_pairs:
    - [person, works_at, organization]
  required_entity_fields: [name, type]
output:
  entities:
    fields:
      - {{name: name, type: str, required: true}}
      - {{name: type, type: str, required: true}}
  relations:
    fields:
      - {{name: source, type: str, required: true}}
      - {{name: target, type: str, required: true}}
      - {{name: type, type: str, required: true}}
{extra}
"""


def _artifact(yaml_text: str):
    """yaml 文本 → TemplateArtifact(template_name/shapes_turtle/source_hash)。"""
    import hashlib

    import yaml as _yaml
    from arrow_lake.ontology.shape_builder import build_shapes, to_turtle
    from arrow_lake.ontology.template_adapter import adapt_template

    template = _yaml.safe_load(yaml_text)
    spec = adapt_template(template)
    return _Artifact(
        template_name=spec.template_name,
        shapes_turtle=to_turtle(build_shapes(spec)),
        source_hash=hashlib.sha1(yaml_text.encode("utf-8")).hexdigest(),
    )


class _Artifact:
    def __init__(self, template_name: str, shapes_turtle: str, source_hash: str) -> None:
        self.template_name = template_name
        self.shapes_turtle = shapes_turtle
        self.source_hash = source_hash


@pytest.fixture
def store() -> OntologyVersionStore:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield OntologyVersionStore(conn)
    conn.close()


def _snap(store: OntologyVersionStore, yaml_text: str, scope: str = "ds_a") -> dict:
    art = _artifact(yaml_text)
    return store.snapshot(
        scope=scope, template_name=art.template_name,
        shapes_turtle=art.shapes_turtle, source_hash=art.source_hash,
    )


# --- extract / diff ---------------------------------------------------------


def test_extract_features_from_turtle() -> None:
    from arrow_lake.ontology.versioning import extract_features

    art = _artifact(_template_yaml())
    feats = extract_features(art.shapes_turtle)
    assert set(feats["entity_type_enum"]) == {"person", "organization"}
    assert set(feats["relation_type_enum"]) == {"works_at"}
    assert set(feats["required_entity_fields"]) == {"name", "type"}
    assert feats["type_pairs"] == [("person", "works_at", "organization")]


def test_diff_features_reports_added_removed() -> None:
    from arrow_lake.ontology.versioning import diff_features, extract_features

    old = extract_features(_artifact(_template_yaml()).shapes_turtle)
    new = extract_features(
        _artifact(_template_yaml(entity_enum="[person, organization, device]")).shapes_turtle
    )
    diff = diff_features(old, new)
    assert diff["entity_type_enum"]["added"] == ["device"]
    assert diff["entity_type_enum"]["removed"] == []
    # 未变化的段全空
    assert diff["relation_type_enum"]["added"] == []
    assert diff["type_pairs"]["added"] == []


def test_diff_features_removed_enum() -> None:
    from arrow_lake.ontology.versioning import diff_features, extract_features

    old = extract_features(
        _artifact(_template_yaml(entity_enum="[person, organization, device]")).shapes_turtle
    )
    new = extract_features(_artifact(_template_yaml()).shapes_turtle)
    diff = diff_features(old, new)
    assert diff["entity_type_enum"]["removed"] == ["device"]


# --- store: version chain ---------------------------------------------------


def test_first_snapshot_is_version_1_without_diff(store: OntologyVersionStore) -> None:
    row = _snap(store, _template_yaml())
    assert row["created"] is True
    assert row["version"] == 1
    assert row["diff"] is None


def test_same_source_hash_skips_new_version(store: OntologyVersionStore) -> None:
    _snap(store, _template_yaml())
    row = _snap(store, _template_yaml())  # identical content → same hash
    assert row["created"] is False
    assert row["version"] == 1
    assert len(store.list_versions(scope="ds_a")) == 1


def test_changed_content_creates_new_version_with_diff(store: OntologyVersionStore) -> None:
    _snap(store, _template_yaml())
    row = _snap(store, _template_yaml(entity_enum="[person, organization, device]"))
    assert row["created"] is True
    assert row["version"] == 2
    assert row["diff"] is not None
    assert row["diff"]["entity_type_enum"]["added"] == ["device"]
    chain = store.list_versions(scope="ds_a")
    assert [v["version"] for v in chain] == [2, 1]  # newest first


def test_list_versions_filters_by_scope(store: OntologyVersionStore) -> None:
    _snap(store, _template_yaml(), scope="ds_a")
    _snap(store, _template_yaml(), scope="ds_b")
    assert len(store.list_versions(scope="ds_a")) == 1
    assert len(store.list_versions()) == 2


def test_list_versions_omits_turtle(store: OntologyVersionStore) -> None:
    _snap(store, _template_yaml())
    row = store.list_versions(scope="ds_a")[0]
    assert "shapes_turtle" not in row
    assert row["source_hash"]


def test_get_version_full_row(store: OntologyVersionStore) -> None:
    created = _snap(store, _template_yaml())
    full = store.get_version(created["id"])
    assert full is not None
    assert isinstance(full["shapes_turtle"], str) and full["shapes_turtle"]
    assert "EntityShape" in full["shapes_turtle"]


def test_get_version_missing_returns_none(store: OntologyVersionStore) -> None:
    assert store.get_version(9999) is None


# --- libSQL commit discipline ----------------------------------------------


def test_snapshot_commits(store: OntologyVersionStore) -> None:
    """libSQL 不 autocommit — 写方法必须显式 commit(CLAUDE.md 速查坑,钉住)。"""
    db = store._db
    committed = [False]
    orig_commit = db.commit

    def spy_commit() -> None:
        committed[0] = True
        orig_commit()

    db.commit = spy_commit  # type: ignore[method-assign]
    _snap(store, _template_yaml())
    assert committed[0], "snapshot 必须 commit"


def test_snapshots_survive_new_connection(tmp_path: Path) -> None:
    """同 hash 跳过依赖跨连接持久(V010 落库,非进程内存)。"""
    db_path = tmp_path / "onto.db"
    conn = SystemDB(f"file:{db_path}")
    Migrator(conn).run()
    store = OntologyVersionStore(conn)
    _snap(store, _template_yaml())
    conn.close()

    conn2 = SystemDB(f"file:{db_path}")
    store2 = OntologyVersionStore(conn2)
    art = _artifact(_template_yaml())
    row = store2.snapshot(
        scope="ds_a", template_name=art.template_name,
        shapes_turtle=art.shapes_turtle, source_hash=art.source_hash,
    )
    assert row["created"] is False, "重启后同 hash 仍应跳过(已持久)"
    conn2.close()
