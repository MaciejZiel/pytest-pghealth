import re
from importlib import resources

import pytest

from pytest_pghealth.checks import CHECKS

SQL_DIR = resources.files("pytest_pghealth").joinpath("sql")
PLACEHOLDERS = {"{schema_filter}", "{remaining_percent}", "{nulls_not_distinct}"}


def test_every_check_has_one_sql_file_and_back():
    files = {path.name for path in SQL_DIR.iterdir() if path.name.endswith(".sql")}
    assert files == {f"{name}.sql" for name in CHECKS}


@pytest.mark.parametrize("name", list(CHECKS))
def test_sql_file_header_names_the_check(name):
    assert CHECKS[name].sql().startswith(f"-- pghealth: {name}\n")


@pytest.mark.parametrize("name", list(CHECKS))
def test_sql_is_safe_for_every_driver(name):
    sql = CHECKS[name].sql()
    # pyformat drivers (psycopg, pg8000 through SQLAlchemy) treat % specially
    assert "%" not in sql
    # SQLAlchemy text() and some drivers treat :name as a bind parameter
    assert not re.search(r"(?<![:\d[]):[a-z_]", sql)
    assert set(re.findall(r"\{[a-z_]+\}", sql)) <= PLACEHOLDERS
    assert "{schema_filter}" in sql


@pytest.mark.parametrize("name", [n for n, c in CHECKS.items() if c.ported])
def test_ported_sql_keeps_the_apache_attribution(name):
    header = CHECKS[name].sql()[:800]
    assert "Ported from pg-index-health-sql" in header
    assert "Copyright (c) 2019-2026. Ivan Vakhrushev and others." in header
    assert "Apache License 2.0" in header
    assert "Modified for pytest-pghealth:" in header


@pytest.mark.parametrize("name", [n for n, c in CHECKS.items() if not c.ported])
def test_original_sql_says_so(name):
    assert "Written for pytest-pghealth" in CHECKS[name].sql()[:300]


def test_only_the_array_check_is_off_by_default():
    assert [n for n, c in CHECKS.items() if not c.default] == ["btree_index_on_array"]


def build(name, **row):
    return CHECKS[name].build(row)


def test_foreign_key_without_index_fix():
    finding = build(
        "foreign_key_without_index",
        schema_name="shop",
        table_name="Order Items",
        table_ident='shop."Order Items"',
        table_kind="r",
        constraint_name="fk_order",
        referenced_table="shop.orders",
        columns=["order_id", "Line"],
        column_idents=["order_id", '"Line"'],
    )
    assert finding.target == "shop.Order Items.fk_order"
    assert finding.fix == (
        'CREATE INDEX CONCURRENTLY ON shop."Order Items" (order_id, "Line");'
    )
    assert "shop.orders" in finding.message


def test_foreign_key_on_partitioned_table_has_no_concurrently():
    finding = build(
        "foreign_key_without_index",
        schema_name="public",
        table_name="measurements",
        table_ident="public.measurements",
        table_kind="p",
        constraint_name="fk",
        referenced_table="sensors",
        columns=["sensor_id"],
        column_idents=["sensor_id"],
    )
    assert finding.fix.startswith("CREATE INDEX ON public.measurements")


def test_duplicate_index_drops_the_constraint_when_it_backs_one():
    common = {
        "schema_name": "public",
        "table_name": "t",
        "table_ident": "public.t",
        "index_name": "t_a_key1",
        "index_ident": "public.t_a_key1",
        "kept_index_name": "t_a_key",
        "constraint_ident": "t_a_key1",
        "index_kind": "i",
    }
    assert build("duplicate_index", constraint_name=None, **common).fix == (
        "DROP INDEX CONCURRENTLY public.t_a_key1;"
    )
    assert build("duplicate_index", constraint_name="t_a_key1", **common).fix == (
        "ALTER TABLE public.t DROP CONSTRAINT t_a_key1;"
    )


def test_invalid_partitioned_index_explains_attach():
    row = {
        "schema_name": "Sales",
        "table_name": "m",
        "table_ident": '"Sales".m',
        "index_name": "m_idx",
        "index_ident": '"Sales".m_idx',
        "schema_ident": '"Sales"',
    }
    assert build("invalid_index", index_kind="i", **row).fix == (
        'REINDEX INDEX CONCURRENTLY "Sales".m_idx;'
    )
    attach = build("invalid_index", index_kind="I", **row).fix
    assert (
        'ALTER INDEX "Sales".m_idx ATTACH PARTITION "Sales".<partition_index>;'
        in attach
    )


def test_table_without_primary_key_prefers_an_existing_unique_index():
    row = {
        "schema_name": "public",
        "table_name": "t",
        "table_ident": "public.t",
        "table_kind": "r",
        "partition_key": None,
    }
    assert build(
        "table_without_primary_key", candidate_index="t_code_key", **row
    ).fix == ("ALTER TABLE public.t ADD PRIMARY KEY USING INDEX t_code_key;")
    finding = build("table_without_primary_key", candidate_index=None, **row)
    assert finding.object is None
    assert finding.target == "public.t"
    assert "GENERATED ALWAYS AS IDENTITY PRIMARY KEY" in finding.fix
    row.update(table_kind="p", partition_key="RANGE (ts)")
    fix = build("table_without_primary_key", candidate_index=None, **row).fix
    assert "partition key (RANGE (ts))" in fix


@pytest.mark.parametrize(
    ("is_identity", "expected"),
    [
        (True, "ALTER TABLE public.t ALTER COLUMN id TYPE bigint;"),
        (
            False,
            (
                "ALTER TABLE public.t ALTER COLUMN id TYPE bigint;\n"
                "ALTER SEQUENCE public.t_id_seq AS bigint;"
            ),
        ),
    ],
)
def test_int4_primary_key_fix(is_identity, expected):
    finding = build(
        "int4_primary_key",
        schema_name="public",
        table_name="t",
        table_ident="public.t",
        column_name="id",
        column_ident="id",
        data_type="integer",
        is_identity=is_identity,
        sequence_ident="public.t_id_seq",
    )
    assert finding.fix.startswith(expected)
    assert "2,147,483,647" in finding.message


def sequence_row(**overrides):
    row = {
        "schema_name": "public",
        "sequence_name": "t_id_seq",
        "sequence_ident": "public.t_id_seq",
        "data_type": "integer",
        "last_value": 2_000_000_000,
        "max_value": 2_147_483_647,
        "remaining_percent": 6.87,
        "table_name": "t",
        "table_ident": "public.t",
        "column_ident": "id",
        "is_identity": False,
    }
    row.update(overrides)
    return row


def test_sequence_near_overflow_with_owner():
    finding = CHECKS["sequence_near_overflow"].build(sequence_row())
    assert finding.target == "public.t.t_id_seq"
    assert "6.87%" in finding.message
    assert finding.fix == (
        "ALTER TABLE public.t ALTER COLUMN id TYPE bigint;\nALTER SEQUENCE public.t_id_seq AS bigint;"
    )


def test_sequence_near_overflow_without_owner():
    finding = CHECKS["sequence_near_overflow"].build(
        sequence_row(table_name=None, table_ident=None, column_ident=None)
    )
    assert finding.target == "public.t_id_seq"
    assert finding.fix == "ALTER SEQUENCE public.t_id_seq AS bigint;"


def test_bigint_sequence_near_overflow_has_advice_only():
    finding = CHECKS["sequence_near_overflow"].build(sequence_row(data_type="bigint"))
    assert finding.fix.startswith("-- ")


def test_nullable_unique_column_fix():
    finding = build(
        "nullable_unique_column",
        schema_name="public",
        table_name="users",
        table_ident="public.users",
        index_name="users_tenant_id_email_key",
        constraint_name="users_tenant_id_email_key",
        nullable_columns=["tenant_id"],
        nullable_column_idents=["tenant_id"],
    )
    assert finding.message.startswith("Unique constraint users_tenant_id_email_key")
    assert finding.fix.splitlines()[0] == (
        "ALTER TABLE public.users ALTER COLUMN tenant_id SET NOT NULL;"
    )


def test_other_fixes():
    assert (
        build(
            "not_valid_constraint",
            schema_name="public",
            table_name="t",
            table_ident="public.t",
            constraint_name="t_check",
            constraint_ident="t_check",
            constraint_type="c",
        ).fix
        == "ALTER TABLE public.t VALIDATE CONSTRAINT t_check;"
    )
    assert (
        build(
            "duplicate_foreign_key",
            schema_name="public",
            table_name="t",
            table_ident="public.t",
            constraint_name="fk2",
            constraint_ident="fk2",
            kept_constraint_name="fk1",
        ).fix
        == "ALTER TABLE public.t DROP CONSTRAINT fk2;"
    )
    assert (
        build(
            "foreign_key_type_mismatch",
            schema_name="public",
            table_name="t",
            table_ident="public.t",
            constraint_name="fk",
            column_name="p_id",
            column_ident="p_id",
            column_type="integer",
            referenced_table="p",
            referenced_column="id",
            referenced_type="bigint",
        ).fix
        == "ALTER TABLE public.t ALTER COLUMN p_id TYPE bigint;"
    )
    array_row = {
        "schema_name": "public",
        "table_name": "t",
        "table_ident": "public.t",
        "index_name": "t_tags_idx",
        "index_ident": "public.t_tags_idx",
        "index_kind": "i",
        "key_column_count": 1,
        "backs_constraint": False,
        "columns": ["tags"],
        "column_idents": ["tags"],
    }
    assert build("btree_index_on_array", **array_row).fix == (
        "CREATE INDEX CONCURRENTLY ON public.t USING gin (tags);\n"
        "DROP INDEX CONCURRENTLY public.t_tags_idx;"
    )
    array_row["key_column_count"] = 2
    fix = build("btree_index_on_array", **array_row).fix
    assert "DROP INDEX" not in fix
    assert "keep t_tags_idx" in fix


def test_partitioned_indexes_are_dropped_without_concurrently():
    row = {
        "schema_name": "public",
        "table_name": "m",
        "table_ident": "public.m",
        "index_name": "m_a_idx1",
        "index_ident": "public.m_a_idx1",
        "index_kind": "I",
        "constraint_name": None,
        "constraint_ident": None,
        "kept_index_name": "m_a_idx",
        "covering_index_name": "m_a_b_idx",
    }
    assert build("duplicate_index", **row).fix == "DROP INDEX public.m_a_idx1;"
    assert build("overlapping_index", **row).fix == "DROP INDEX public.m_a_idx1;"
