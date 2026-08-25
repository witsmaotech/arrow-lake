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


class TestCaseInsensitiveDatasetNames:
    """CRITICAL review finding (2026-08-24): DuckDB resolves identifiers
    case-insensitively but enforcement looked ACLs up exact-case — any
    non-lowercase dataset name escaped BOTH the row-filter pushdown and
    the column AST check. Worse: even an exact-case reference missed after
    sqlglot qualify() normalized identifiers to lowercase, so mixed-case
    datasets (e.g. AIGC_2023REPORT) were entirely unprotected."""

    ROW_MC = DatasetACL(dataset="MyData", role="viewer", row_filter="region == 'east'")
    COL_MC = DatasetACL(
        dataset="MyData", role="viewer", visible_columns=frozenset({"id", "region", "salary"})
    )

    @staticmethod
    def _ci_getter(acl):
        # mirrors the fixed PermissionChecker.get_acl: case-insensitive hit
        return lambda t: acl if t.lower() == "mydata" else None

    @pytest.fixture()
    def duck_mc(self):
        conn = duckdb.connect()
        conn.execute(
            'CREATE TABLE "MyData" (id INTEGER, region VARCHAR, salary DOUBLE, phone VARCHAR);'
        )
        conn.execute(
            'INSERT INTO "MyData" VALUES '
            "(1, 'east', 100.0, '111'), (2, 'west', 900.0, '222'), (3, 'east', 300.0, '333')"
        )
        yield conn
        conn.close()

    # ── row filter: every case variant must get the predicate ──

    @pytest.mark.parametrize("ref", ["mydata", "MYDATA", "MyData", '"MyData"'])
    def test_row_filter_applies_to_every_case_variant(self, duck_mc, ref):
        out = enforce_sql_acl(
            f"SELECT id FROM {ref}", get_acl=self._ci_getter(self.ROW_MC)
        )
        assert {r[0] for r in duck_mc.execute(out).fetchall()} == {1, 3}

    @pytest.mark.parametrize("ref", ["mydata", "MYDATA", '"MyData"'])
    def test_aggregation_leak_blocked_for_every_case_variant(self, duck_mc, ref):
        out = enforce_sql_acl(
            f"SELECT max(salary) FROM {ref}", get_acl=self._ci_getter(self.ROW_MC)
        )
        assert duck_mc.execute(out).fetchone()[0] == 300.0  # east only

    # ── column ACL: hidden column rejected under any case spelling ──

    @pytest.mark.parametrize("ref", ["mydata", "MYDATA", "MyData", '"MyData"'])
    def test_hidden_column_rejected_for_every_case_variant(self, ref):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                f"SELECT phone FROM {ref}", get_acl=self._ci_getter(self.COL_MC)
            )

    def test_hidden_column_uppercase_spelling_also_rejected(self):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "SELECT PHONE FROM MyData", get_acl=self._ci_getter(self.COL_MC)
            )

    def test_visible_column_uppercase_spelling_allowed(self, duck_mc):
        """Column names are case-insensitive in DuckDB too — a visible
        column referenced uppercase must NOT be a false rejection."""
        out = enforce_sql_acl(
            "SELECT ID, SALARY FROM MyData WHERE SALARY > 150",
            get_acl=self._ci_getter(self.COL_MC),
        )
        assert {r[0] for r in duck_mc.execute(out).fetchall()} == {2, 3}

    def test_qualified_hidden_column_rejected(self):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "SELECT MyData.phone FROM MyData", get_acl=self._ci_getter(self.COL_MC)
            )

    def test_mixed_case_join_both_sides_enforced(self, duck_mc):
        """Mixed-case + lowercase datasets in one query: both ACLs apply."""
        duck_mc.execute("CREATE TABLE ds (id INTEGER, region VARCHAR)")
        duck_mc.execute("INSERT INTO ds VALUES (1, 'east'), (2, 'west')")

        def get(t):
            if t.lower() == "mydata":
                return self.ROW_MC
            if t == "ds":
                return DatasetACL(dataset="ds", role="viewer", row_filter="region == 'west'")
            return None

        out = enforce_sql_acl(
            'SELECT "MyData".id FROM "MyData" JOIN ds ON "MyData".id = ds.id',
            get_acl=get,
        )
        # MyData: east only (1,3); ds: west only (2) → no join rows
        assert duck_mc.execute(out).fetchall() == []


class TestColumnsWildcardBypass:
    """Review H-1 (2026-08-24): DuckDB's COLUMNS('regex') / COLUMNS([...])
    reference columns by string literal — invisible to the exp.Column walk.
    It smuggled hidden columns out renamed as visible ones, defeating even
    the post-hoc name whitelist."""

    def test_columns_regex_rejected_under_column_acl(self):
        with pytest.raises(AclSqlViolation, match="COLUMNS"):
            enforce_sql_acl(
                "SELECT v AS id FROM (SELECT COLUMNS('ph.*') FROM ds) AS t(v)",
                get_acl=_getter(COL_ACL),
            )

    def test_columns_list_rejected_under_column_acl(self):
        with pytest.raises(AclSqlViolation, match="COLUMNS"):
            enforce_sql_acl(
                "SELECT COLUMNS(['id', 'phone']) FROM ds", get_acl=_getter(COL_ACL)
            )

    def test_columns_star_rejected_under_column_acl(self):
        with pytest.raises(AclSqlViolation, match="COLUMNS"):
            enforce_sql_acl(
                "SELECT COLUMNS(*) FROM ds", get_acl=_getter(COL_ACL)
            )

    def test_columns_allowed_without_column_acl(self, duck):
        """Row-filter-only datasets keep COLUMNS(): the predicate rewrite
        still applies to the expanded projection's source."""
        out = enforce_sql_acl(
            "SELECT COLUMNS(['id']) FROM ds", get_acl=_getter(ROW_ACL)
        )
        assert {r[0] for r in duck.execute(out).fetchall()} == {1, 3}


class TestCrossScopeAliasCollision:
    """Review H-3: _alias_map was one global dict — an outer alias with the
    same name shadowed an inner scope's binding, hiding the inner table's
    restricted columns from the check."""

    def test_inner_scope_alias_resolved_correctly(self):
        with pytest.raises(AclSqlViolation, match="phone"):
            enforce_sql_acl(
                "SELECT (SELECT a.phone FROM ds AS a LIMIT 1) AS leak FROM public_tbl AS a",
                get_acl=_getter(COL_ACL),
            )

    def test_outer_scope_still_resolves(self):
        """The per-scope fix must not break ordinary outer alias checks."""
        with pytest.raises(AclSqlViolation, match="phone"):
            enforce_sql_acl(
                "SELECT a.phone FROM ds AS a", get_acl=_getter(COL_ACL)
            )


class TestDoubleHyphenDatasetName:
    """Review H-5: _NAME_PATTERN allows 'x--y'; the row-filter subquery was
    f-string built with the UNQUOTED name, so the predicate parsed as a
    comment and the filter silently vanished."""

    def test_predicate_survives_double_hyphen_name(self, duck):
        duck.execute('CREATE TABLE "x--y" (id INTEGER, region VARCHAR)')
        duck.execute("INSERT INTO \"x--y\" VALUES (1, 'east'), (2, 'west')")
        acl = DatasetACL(dataset="x--y", role="viewer", row_filter="region == 'east'")
        get = lambda t: acl if t == "x--y" else None  # noqa: E731

        out = enforce_sql_acl("SELECT id FROM \"x--y\"", get_acl=get)
        rows = duck.execute(out).fetchall()
        assert rows == [(1,)]  # west row filtered — predicate survived



class TestCheckerCaseInsensitiveGetAcl:
    """PermissionChecker.get_acl must resolve case-insensitively (DuckDB
    semantics): the enforcement layer passes SQL-written table names."""

    def test_in_memory_lookup_case_insensitive(self):
        from arrow_lake.api.rbac import PermissionChecker

        checker = PermissionChecker()
        checker.set_acl(DatasetACL(dataset="MyData", role="viewer", visible_columns=frozenset({"id"})))
        for probe in ("mydata", "MYDATA", "MyData"):
            acl = checker.get_acl(probe, "viewer")
            assert acl is not None, probe
            assert acl.dataset == "MyData"

    def test_exact_match_preferred(self):
        from arrow_lake.api.rbac import PermissionChecker

        checker = PermissionChecker()
        checker.set_acl(DatasetACL(dataset="MyData", role="viewer", visible_columns=frozenset({"id"})))
        checker.set_acl(DatasetACL(dataset="mydata", role="viewer", visible_columns=frozenset({"id", "name"})))
        # both exist (path-namespaced separately); exact key wins for its own case
        assert checker.get_acl("mydata", "viewer").visible_columns == frozenset({"id", "name"})
        assert checker.get_acl("MyData", "viewer").visible_columns == frozenset({"id"})



# --------------------------------------------------------------------------- #
# W3.2 (DR14): two-part refs — container tables authorize against the
# container dataset. sqlglot keeps the schema in table.db; table.name alone
# ("segments") missed the dataset ACL entirely → fail-open bypass.
# --------------------------------------------------------------------------- #

@pytest.fixture()
def container_duck():
    conn = duckdb.connect()
    conn.execute("CREATE SCHEMA gas_net")
    conn.execute(
        "CREATE TABLE gas_net.segments (id INTEGER, region VARCHAR, salary DOUBLE, phone VARCHAR)"
    )
    conn.execute(
        "INSERT INTO gas_net.segments VALUES "
        "(1, 'east', 100.0, '111'), (2, 'west', 900.0, '222'), (3, 'east', 300.0, '333')"
    )
    yield conn
    conn.close()


ROW_ACL_CN = DatasetACL(dataset="gas_net", role="viewer", row_filter="region == 'east'")
COL_ACL_CN = DatasetACL(
    dataset="gas_net", role="viewer", visible_columns=frozenset({"id", "region", "salary"})
)


class TestContainerTwoPartRefs:
    def test_two_part_row_filter_pushed(self, container_duck):
        out = enforce_sql_acl(
            "SELECT count(*) AS n FROM gas_net.segments",
            get_acl=lambda t: ROW_ACL_CN if t == "gas_net.segments" else None,
        )
        n = container_duck.execute(out).fetchone()[0]
        assert n == 2  # west row filtered

    def test_two_part_rewrite_preserves_qualifier(self, container_duck):
        out = enforce_sql_acl(
            "SELECT id FROM gas_net.segments",
            get_acl=lambda t: ROW_ACL_CN if t == "gas_net.segments" else None,
        )
        # the subquery keeps the two-part reference so DuckDB resolves the
        # schema-qualified table
        assert '"gas_net"."segments"' in out
        ids = [r[0] for r in container_duck.execute(out).fetchall()]
        assert sorted(ids) == [1, 3]

    def test_two_part_hidden_column_rejected(self, container_duck):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "SELECT phone FROM gas_net.segments",
                get_acl=lambda t: COL_ACL_CN if t == "gas_net.segments" else None,
            )

    def test_two_part_alias_hidden_column_rejected(self, container_duck):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "SELECT s.phone FROM gas_net.segments AS s",
                get_acl=lambda t: COL_ACL_CN if t == "gas_net.segments" else None,
            )

    def test_two_part_star_rejected_when_column_restricted(self, container_duck):
        with pytest.raises(AclSqlViolation):
            enforce_sql_acl(
                "SELECT * FROM gas_net.segments",
                get_acl=lambda t: COL_ACL_CN if t == "gas_net.segments" else None,
            )

    def test_two_part_alias_row_filter_still_applies(self, container_duck):
        out = enforce_sql_acl(
            "SELECT count(*) AS n FROM gas_net.segments AS s WHERE s.salary > 50",
            get_acl=lambda t: ROW_ACL_CN if t == "gas_net.segments" else None,
        )
        n = container_duck.execute(out).fetchone()[0]
        assert n == 2

    def test_join_container_and_plain(self, container_duck):
        container_duck.execute("CREATE TABLE plain (id INTEGER)")
        container_duck.execute("INSERT INTO plain VALUES (1), (3)")
        out = enforce_sql_acl(
            "SELECT count(*) AS n FROM gas_net.segments g JOIN plain p ON g.id = p.id",
            get_acl=lambda t: ROW_ACL_CN if t == "gas_net.segments" else None,
        )
        n = container_duck.execute(out).fetchone()[0]
        assert n == 2  # ids 1,3 (east); west row filtered before the join


# --------------------------------------------------------------------------- #
# W3.4 (DR14 D4): table-level ACL override — checker layers 'ds.table' keys
# (store key 'ds::table' first, container default fallback).
# --------------------------------------------------------------------------- #

class TestTableLevelAclLayering:
    def _checker(self):
        from arrow_lake.api.rbac import PermissionChecker

        return PermissionChecker()  # in-memory path

    def test_table_override_wins(self, container_duck):
        chk = self._checker()
        chk.set_acl(DatasetACL(dataset="gas_net", role="viewer",
                               row_filter="region == 'east'"))
        chk.set_acl(DatasetACL(dataset="gas_net.segments", role="viewer",
                               row_filter="region == 'west'"))
        out = enforce_sql_acl(
            "SELECT count(*) AS n FROM gas_net.segments",
            get_acl=lambda t: chk.get_acl(t, "viewer"),
        )
        n = container_duck.execute(out).fetchone()[0]
        assert n == 1  # west override beat the east container default

    def test_fallback_to_container_default(self, container_duck):
        chk = self._checker()
        chk.set_acl(DatasetACL(dataset="gas_net", role="viewer",
                               row_filter="region == 'east'"))
        out = enforce_sql_acl(
            "SELECT count(*) AS n FROM gas_net.segments",
            get_acl=lambda t: chk.get_acl(t, "viewer"),
        )
        n = container_duck.execute(out).fetchone()[0]
        assert n == 2  # no table entry → container default applies

    def test_layered_key_is_dotted_not_table_name(self, container_duck):
        chk = self._checker()
        # an ACL keyed on the bare table name must NOT apply to a two-part ref
        chk.set_acl(DatasetACL(dataset="segments", role="viewer",
                               row_filter="region == 'west'"))
        out = enforce_sql_acl(
            "SELECT count(*) AS n FROM gas_net.segments",
            get_acl=lambda t: chk.get_acl(t, "viewer"),
        )
        n = container_duck.execute(out).fetchone()[0]
        assert n == 3  # untouched — no gas_net ACL, 'segments' key not matched
