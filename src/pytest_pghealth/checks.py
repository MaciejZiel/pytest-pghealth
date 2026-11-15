"""The checks: one SQL file each, plus how a result row becomes a finding."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

from pytest_pghealth.findings import Finding

Row = Mapping[str, Any]


@dataclass(frozen=True)
class Check:
    """A schema check.

    ``default`` says whether the check runs when the configuration does not
    name it; ``ported`` says whether its SQL comes from pg-index-health-sql.
    """

    name: str
    summary: str
    build: Callable[[Row], Finding]
    default: bool = True
    ported: bool = True

    def sql(self) -> str:
        return (
            resources.files("pytest_pghealth")
            .joinpath("sql", f"{self.name}.sql")
            .read_text(encoding="utf-8")
        )


def _concurrently(index_kind: str) -> str:
    """CONCURRENTLY, except for a partitioned index, which does not support it."""
    return "" if index_kind == "I" else " CONCURRENTLY"


def _columns(names: list[str]) -> str:
    return ", ".join(names)


def _foreign_key_without_index(row: Row) -> Finding:
    columns = list(row["columns"])
    concurrently = "" if row["table_kind"] == "p" else " CONCURRENTLY"
    fix = (
        # no index name: PostgreSQL picks one that is free (<table>_<column>_idx)
        f"CREATE INDEX{concurrently} ON {row['table_ident']} ({_columns(list(row['column_idents']))});"
    )
    return Finding(
        check="foreign_key_without_index",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["constraint_name"],
        message=(
            f"Foreign key {row['constraint_name']} ({_columns(columns)}) "
            f"has no index: every delete or key update in {row['referenced_table']} "
            f"scans {row['table_ident']}, and joins on these columns cannot use an index."
        ),
        fix=fix,
    )


def _duplicate_index(row: Row) -> Finding:
    if row["constraint_name"]:
        fix = f"ALTER TABLE {row['table_ident']} DROP CONSTRAINT {row['constraint_ident']};"
    else:
        fix = f"DROP INDEX{_concurrently(row['index_kind'])} {row['index_ident']};"
    return Finding(
        check="duplicate_index",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["index_name"],
        message=(
            f"Index {row['index_name']} is identical to {row['kept_index_name']}: "
            "it takes space and slows every write without serving any query "
            "the other one cannot."
        ),
        fix=fix,
    )


def _overlapping_index(row: Row) -> Finding:
    return Finding(
        check="overlapping_index",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["index_name"],
        message=(
            f"Index {row['index_name']} is a leading prefix of "
            f"{row['covering_index_name']}, which serves the same lookups; "
            "the shorter index only adds write cost."
            + (
                f" It looks like the index of foreign key {row['foreign_key_name']}, "
                f"which {row['covering_index_name']} covers as well."
                if row.get("foreign_key_name")
                else ""
            )
        ),
        fix=f"DROP INDEX{_concurrently(row['index_kind'])} {row['index_ident']};",
    )


def _invalid_index(row: Row) -> Finding:
    if row["index_kind"] == "I":
        message = (
            f"Partitioned index {row['index_name']} is invalid: some partition "
            "has no matching index attached."
        )
        fix = (
            "-- create the index on each partition that lacks it, then:\n"
            f"ALTER INDEX {row['index_ident']} ATTACH PARTITION "
            f"{row['schema_ident']}.<partition_index>;"
        )
    else:
        message = (
            f"Index {row['index_name']} is invalid (often a failed CREATE INDEX "
            "CONCURRENTLY): queries never use it, but every write updates it."
        )
        fix = f"REINDEX INDEX CONCURRENTLY {row['index_ident']};"
    return Finding(
        check="invalid_index",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["index_name"],
        message=message,
        fix=fix,
    )


def _table_without_primary_key(row: Row) -> Finding:
    if row["table_kind"] == "p":
        fix = (
            "-- a partitioned table's primary key must include its partition key "
            f"({row['partition_key']}):\n"
            f"ALTER TABLE {row['table_ident']} ADD PRIMARY KEY (<columns>);"
        )
    elif row["candidate_index"]:
        fix = (
            f"ALTER TABLE {row['table_ident']} ADD PRIMARY KEY "
            f"USING INDEX {row['candidate_index']};"
        )
    else:
        fix = (
            f"ALTER TABLE {row['table_ident']} ADD COLUMN id bigint "
            "GENERATED ALWAYS AS IDENTITY PRIMARY KEY;\n"
            "-- or ADD PRIMARY KEY (...) on columns that already identify a row"
        )
    return Finding(
        check="table_without_primary_key",
        schema=row["schema_name"],
        table=row["table_name"],
        object=None,
        message=(
            f"Table {row['table_ident']} has no primary key: rows cannot be "
            "addressed reliably, and logical replication cannot replicate "
            "its updates and deletes."
        ),
        fix=fix,
    )


def _sequence_near_overflow(row: Row) -> Finding:
    remaining = float(row["remaining_percent"])
    owner = (
        f" (it feeds {row['table_ident']}.{row['column_ident']})"
        if row["table_ident"]
        else ""
    )
    if row["data_type"] != "bigint" and row["table_ident"]:
        fix = f"ALTER TABLE {row['table_ident']} ALTER COLUMN {row['column_ident']} TYPE bigint;"
        if not row["is_identity"]:
            fix += f"\nALTER SEQUENCE {row['sequence_ident']} AS bigint;"
    elif row["data_type"] != "bigint":
        fix = f"ALTER SEQUENCE {row['sequence_ident']} AS bigint;"
    else:
        fix = (
            f"-- {row['sequence_ident']} is a bigint sequence: raise its MAXVALUE "
            "or check why it advances so fast"
        )
    return Finding(
        check="sequence_near_overflow",
        schema=row["schema_name"],
        table=row["table_name"] or row["sequence_name"],
        object=row["sequence_name"] if row["table_name"] else None,
        message=(
            f"Sequence {row['sequence_ident']} ({row['data_type']}){owner} has "
            f"{remaining:.2f}% of its range left (last value {row['last_value']}, "
            f"max {row['max_value']})."
        ),
        fix=fix,
    )


def _int4_primary_key(row: Row) -> Finding:
    fix = f"ALTER TABLE {row['table_ident']} ALTER COLUMN {row['column_ident']} TYPE bigint;"
    if not row["is_identity"]:
        fix += f"\nALTER SEQUENCE {row['sequence_ident']} AS bigint;"
    fix += "\n-- and every foreign key column that references it"
    limit = "32,767" if row["data_type"] == "smallint" else "2,147,483,647"
    return Finding(
        check="int4_primary_key",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["column_name"],
        message=(
            f"Primary key {row['table_ident']}.{row['column_ident']} is "
            f"{row['data_type']} fed by a sequence: inserts start failing after "
            f"{limit} rows, and widening it later rewrites the table."
        ),
        fix=fix,
    )


def _foreign_key_type_mismatch(row: Row) -> Finding:
    return Finding(
        check="foreign_key_type_mismatch",
        schema=row["schema_name"],
        table=row["table_name"],
        object=f"{row['constraint_name']}.{row['column_name']}",
        message=(
            f"Foreign key {row['constraint_name']}: column {row['column_name']} is "
            f"{row['column_type']} but references {row['referenced_table']}."
            f"{row['referenced_column']}, which is {row['referenced_type']}."
        ),
        fix=(
            f"ALTER TABLE {row['table_ident']} ALTER COLUMN {row['column_ident']} "
            f"TYPE {row['referenced_type']};"
        ),
    )


def _nullable_unique_column(row: Row) -> Finding:
    columns = list(row["nullable_columns"])
    kind = (
        f"Unique constraint {row['constraint_name']}"
        if row["constraint_name"]
        else f"Unique index {row['index_name']}"
    )
    set_not_null = "\n".join(
        f"ALTER TABLE {row['table_ident']} ALTER COLUMN {ident} SET NOT NULL;"
        for ident in row["nullable_column_idents"]
    )
    return Finding(
        check="nullable_unique_column",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["constraint_name"] or row["index_name"],
        message=(
            f"{kind} includes nullable column(s) {_columns(columns)}: rows with "
            "NULL there never conflict, so duplicates get in."
        ),
        fix=(
            set_not_null
            + "\n-- or, on PostgreSQL 15+, recreate it with UNIQUE NULLS NOT DISTINCT"
        ),
    )


def _not_valid_constraint(row: Row) -> Finding:
    kind = "Foreign key" if row["constraint_type"] == "f" else "Check constraint"
    return Finding(
        check="not_valid_constraint",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["constraint_name"],
        message=(
            f"{kind} {row['constraint_name']} was added NOT VALID and never "
            "validated: existing rows may violate it."
        ),
        fix=(
            f"ALTER TABLE {row['table_ident']} VALIDATE CONSTRAINT "
            f"{row['constraint_ident']};"
        ),
    )


def _duplicate_foreign_key(row: Row) -> Finding:
    return Finding(
        check="duplicate_foreign_key",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["constraint_name"],
        message=(
            f"Foreign key {row['constraint_name']} repeats "
            f"{row['kept_constraint_name']}: both are checked on every write."
        ),
        fix=(
            f"ALTER TABLE {row['table_ident']} DROP CONSTRAINT "
            f"{row['constraint_ident']};"
        ),
    )


def _btree_index_on_array(row: Row) -> Finding:
    columns = list(row["columns"])
    concurrently = _concurrently(row["index_kind"])
    gin = "\n".join(
        f"CREATE INDEX{concurrently} ON {row['table_ident']} USING gin ({ident});"
        for ident in row["column_idents"]
    )
    if row["key_column_count"] == 1 and not row["backs_constraint"]:
        fix = f"{gin}\nDROP INDEX{concurrently} {row['index_ident']};"
    else:
        # the index has other columns or enforces a constraint: keep it
        fix = f"{gin}\n-- and keep {row['index_name']} for its other lookups or its constraint"
    return Finding(
        check="btree_index_on_array",
        schema=row["schema_name"],
        table=row["table_name"],
        object=row["index_name"],
        message=(
            f"B-tree index {row['index_name']} covers array column(s) "
            f"{_columns(columns)}: it cannot serve @>, <@ or && queries."
        ),
        fix=fix,
    )


CHECKS: dict[str, Check] = {
    check.name: check
    for check in (
        Check(
            "foreign_key_without_index",
            "foreign keys with no index on the referencing columns",
            _foreign_key_without_index,
        ),
        Check(
            "duplicate_index",
            "indexes identical to another index",
            _duplicate_index,
        ),
        Check(
            "overlapping_index",
            "B-tree indexes that are a leading prefix of another index",
            _overlapping_index,
            ported=False,
        ),
        Check(
            "invalid_index",
            "invalid indexes (failed CREATE INDEX CONCURRENTLY)",
            _invalid_index,
        ),
        Check(
            "table_without_primary_key",
            "tables without a primary key",
            _table_without_primary_key,
        ),
        Check(
            "int4_primary_key",
            "integer or smallint primary keys fed by a sequence",
            _int4_primary_key,
            ported=False,
        ),
        Check(
            "sequence_near_overflow",
            "sequences with little of their range left",
            _sequence_near_overflow,
        ),
        Check(
            "foreign_key_type_mismatch",
            "foreign key columns whose type differs from the referenced column",
            _foreign_key_type_mismatch,
        ),
        Check(
            "nullable_unique_column",
            "multi-column unique indexes with a nullable column",
            _nullable_unique_column,
            ported=False,
        ),
        Check(
            "not_valid_constraint",
            "constraints added NOT VALID and never validated",
            _not_valid_constraint,
        ),
        Check(
            "duplicate_foreign_key",
            "foreign keys identical to another one",
            _duplicate_foreign_key,
        ),
        Check(
            "btree_index_on_array",
            "B-tree indexes on array columns (off by default)",
            _btree_index_on_array,
            default=False,
        ),
    )
}
