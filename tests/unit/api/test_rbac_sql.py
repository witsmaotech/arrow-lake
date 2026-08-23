"""v1.10.7 WP1b/WP1c: source-level SQL ACL enforcement (review C2).

Attack corpus: every bypass that worked against post-hoc result filtering
must now fail (or be neutralized at the source).
"""

from __future__ import annotations

import duckdb
import pytest

from arrow_lake.api.rbac import DatasetACL
from arrow_lake.api.rbac_sql import AclSqlViolation, enforce_sql_acl

ROW_ACL = DatasetACL(dataset="ds", role="viewer", row_filter="region == 'east'")
COL_ACL = DatasetACL(
    dataset="ds", role="viewer", visible_columns=frozenset({"id", "region", "salary"})
)
HIDDEN = {"phone"}  # everything not in visible_columns


def _getter(acl):
    return lambda t: acl if t == "ds" else None


@pytest.fixture()
def duck():
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE ds (id INTEGER, region VARCHAR, salary DOUBLE, phone VARCHAR);"
    )
    conn.execute(
        "INSERT INTO ds VALUES "
        "(1, 'east', 100.0, '111'), (2, 'west', 900.0, '222'), (3, 'east', 300.0, '333')"
    )
    yield conn
    conn.close()


class TestRowFilterPushdown:
    def test_plain_query_gets_predicate_pushed(self, duck):
        out = enforce_sql_acl(
            "SELECT id, salary FROM ds WHERE salary > 50",
            get_acl=_getter(ROW_ACL),
        )
        rows = duck.execute(out).fetchall()
        # Only east rows survive regardless of the user's own WHERE.
        assert {r[0] for r in rows} == {1, 3}

    def test_constant_alias_cannot_bypass_row_filter(self, duck):
        """Review C2 payload: SELECT 'east' AS region, max(salary) FROM ds."""
        out = enforce_sql_acl(
            "SELECT 'east' AS region, max(salary) FROM ds",
            get_acl=_getter(ROW_ACL),
        )
        row = duck.execute(out).fetchone()
        # Predicate applies to raw rows BEFORE aggregation → max over east only.
        assert row[1] == 300.0

    def test_aggregation_cannot_touch_filtered_rows(self, duck):
        out = enforce_sql_acl(
            "SELECT avg(salary) FROM ds", get_acl=_getter(ROW_ACL)
        )
        (avg,) = duck.execute(out).fetchone()
        assert avg == pytest.approx(200.0)  # (100+300)/2, not (100+900+300)/3

    def test_join_with_alias_still_filtered(self, duck):
        out = enforce_sql_acl(
            "SELECT d.id FROM ds AS d WHERE d.salary > 50",
            get_acl=_getter(ROW_ACL),
        )
        assert {r[0] for r in duck.execute(out).fetchall()} == {1, 3}

    def test_cte_over_dataset_is_filtered(self, duck):
        out = enforce_sql_acl(
            "WITH x AS (SELECT * FROM ds) SELECT max(salary) FROM x",
            get_acl=_getter(ROW_ACL),
        )
        (m,) = duck.execute(out).fetchone()
        assert m == 300.0

    def test_second_acl_dataset_gets_own_predicate(self, duck):
        duck.execute("CREATE TABLE other (id INTEGER, region VARCHAR)")
        duck.execute("INSERT INTO other VALUES (10, 'east'), (11, 'west')")

        def get(t):
            if t == "ds":
                return ROW_ACL
            if t == "other":
                return DatasetACL(dataset="other", role="viewer", row_filter="region == 'west'")
            return None

        out = enforce_sql_acl(
            "SELECT ds.id, other.id FROM ds JOIN other ON ds.id = other.id - 9",
            get_acl=get,
        )
        rows = duck.execute(out).fetchall()
        # ds: east only (1,3); other: west only (11) → join yields (nothing: 1↔10 east excluded, 3↔12 absent)
        assert rows == []

    def test_no_acl_passthrough_identical(self):
        sql = "SELECT id FROM ds"
        assert enforce_sql_acl(sql, get_acl=lambda t: None) == sql

    def test_unparseable_sql_fail_closed(self):
        with pytest.raises(AclSqlViolation, match="analyzed"):
            enforce_sql_acl("SELECT ]]] FROM ds", get_acl=_getter(ROW_ACL))


class TestColumnAclAstEnforcement:
    def test_alias_smuggle_rejected(self):
        """Review C2 payload: SELECT phone AS tel FROM ds."""
        with pytest.raises(AclSqlViolation, match="not in the visible column set"):
            enforce_sql_acl(
                "SELECT phone AS tel FROM ds", get_acl=_getter(COL_ACL)
            )

    def test_expression_reference_rejected(self):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "SELECT phone * 0 + id AS x FROM ds", get_acl=_getter(COL_ACL)
            )

    def test_select_star_rejected_when_column_acl(self):
        with pytest.raises(AclSqlViolation, match="SELECT \\*"):
            enforce_sql_acl("SELECT * FROM ds", get_acl=_getter(COL_ACL))

    def test_qualified_star_rejected(self):
        with pytest.raises(AclSqlViolation, match="ds\\.\\*"):
            enforce_sql_acl("SELECT ds.* FROM ds", get_acl=_getter(COL_ACL))

    def test_count_star_allowed(self, duck):
        out = enforce_sql_acl(
            "SELECT count(*) FROM ds", get_acl=_getter(COL_ACL)
        )
        assert duck.execute(out).fetchone()[0] == 3

    def test_visible_column_allowed(self, duck):
        out = enforce_sql_acl(
            "SELECT id, salary FROM ds WHERE salary > 150", get_acl=_getter(COL_ACL)
        )
        # No row_filter on COL_ACL — salary>150 matches west row 2 as well.
        assert {r[0] for r in duck.execute(out).fetchall()} == {2, 3}

    def test_cte_hidden_column_rejected(self):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "WITH x AS (SELECT phone FROM ds) SELECT id FROM x",
                get_acl=_getter(COL_ACL),
            )

    def test_subquery_hidden_column_rejected(self):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "SELECT id FROM ds WHERE id IN (SELECT id FROM ds WHERE phone = '111')",
                get_acl=_getter(COL_ACL),
            )

    def test_row_and_column_acl_combined(self, duck):
        both = DatasetACL(
            dataset="ds",
            role="viewer",
            visible_columns=frozenset({"id", "salary"}),
            row_filter="region == 'east'",
        )
        out = enforce_sql_acl(
            "SELECT id, salary FROM ds", get_acl=_getter(both)
        )
        assert {(r[0], r[1]) for r in duck.execute(out).fetchall()} == {(1, 100.0), (3, 300.0)}
