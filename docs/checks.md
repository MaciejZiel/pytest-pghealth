# The checks

Each check is one SQL query in `src/pytest_pghealth/sql/` and reads only the
system catalog (`pg_class`, `pg_index`, `pg_constraint`, `pg_attribute`,
`pg_sequence`, `pg_depend`). None of them reads table data or statistics, so
they give the same answer on an empty test database as on production.

Every check skips PostgreSQL's own schemas (`pg_*`, `information_schema`)
and objects that belong to an extension. `schemas` in `[tool.pghealth]`
narrows them to the schemas you name.

A finding's *target* is `schema.table` or `schema.table.object`; ignores
match it (see [configuration](configuration.md#ignores)).

## foreign_key_without_index

**Finds** foreign keys whose columns are not the leading key columns (in any
order) of a valid, non-partial index on the referencing table.

**Why.** PostgreSQL indexes the referenced side (a primary key or unique
constraint) but never the referencing side. Without that index, deleting or
updating the key of a parent row scans the whole child table, once per row,
while holding locks; joins from parent to child cannot use an index either.

**Does not count:** a partial index (it cannot serve every lookup), an
invalid index, an index whose leading column is something else
(`(created_at, customer_id)` does not help a lookup by `customer_id`), and
columns that are only in `INCLUDE`.

**Fix:** `CREATE INDEX CONCURRENTLY ON <table> (<columns>);` The index is
unnamed, so PostgreSQL picks a free name such as `<table>_<column>_idx`;
name it in your migration if you prefer. On a partitioned table the fix has
no `CONCURRENTLY`, which partitioned tables do not support.
A foreign key on a partitioned table is reported once, on the parent.

**Django:** `ForeignKey` creates the index unless `db_index=False`, so this
mostly finds hand-written SQL and `db_index=False`.

## duplicate_index

**Finds** indexes identical to another index on the same table: same access
method, columns, operator classes, collations, sort order, expressions and
predicate.

**Which one is reported.** The index that stays is the primary key's, then
one backing a constraint, then a unique one, then the oldest; every other
copy is reported.
When the copy itself backs a constraint (two `UNIQUE (email)` constraints),
the fix drops the constraint.

**Fix:** `DROP INDEX CONCURRENTLY <index>;` (without `CONCURRENTLY` for an
index on a partitioned table) or `ALTER TABLE <table> DROP CONSTRAINT <constraint>;`

## overlapping_index

**Finds** B-tree indexes whose key columns are a leading prefix of another
B-tree index on the same table, with the same operator classes, collations,
sort order and predicate: `(customer_id)` next to `(customer_id, created_at)`.

The longer index serves every lookup the shorter one does. The shorter index
can still be slightly faster for its own queries, so treat the finding as a
prompt to decide, and ignore it with a reason where the shorter index is
deliberate.

**Never reported as the redundant one:** unique indexes and indexes behind a
constraint (they enforce something), expression indexes, indexes with
`INCLUDE` columns, and invalid indexes.

**Fix:** `DROP INDEX CONCURRENTLY <shorter index>;`

## invalid_index

**Finds** indexes marked invalid. A failed `CREATE INDEX CONCURRENTLY` (a
duplicate value for a unique index, a cancelled migration) leaves one behind.
The planner never uses it, but every write still updates it, and a unique one
still rejects duplicates.

**Fix:** `REINDEX INDEX CONCURRENTLY <index>;` after fixing what made the
build fail, or `DROP INDEX CONCURRENTLY`. A partitioned index is invalid
while some partition has no matching index attached; the fix is to create
it and `ALTER INDEX ... ATTACH PARTITION`.

## table_without_primary_key

**Finds** tables and partitioned tables without a primary key. Partitions
are not reported (they get the parent's key).

**Why.** Logical replication cannot replicate updates and deletes of such a
table (unless `REPLICA IDENTITY FULL`), ORMs and CDC tools need a key to
address rows, and duplicate rows cannot be told apart.

**Fix:** when the table has a unique, non-partial B-tree index on NOT NULL
columns that does not back a constraint,
`ALTER TABLE <table> ADD PRIMARY KEY USING INDEX <index>;`. Otherwise an
identity column: `ALTER TABLE <table> ADD COLUMN id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY;`
For a partitioned table, the key must include the partition key, which the
fix names.

## int4_primary_key

**Finds** primary key columns of type `integer` or `smallint` whose values
come from a sequence (`serial`, `smallserial` or an identity column).

**Why.** `integer` runs out at 2,147,483,647 (`smallint` at 32,767): inserts
then fail with "nextval: reached maximum value of sequence". Changing the
type later rewrites the table and every referencing foreign key column, under
an exclusive lock. A primary key without a sequence (a natural key) is not
reported.

**Fix:** `ALTER TABLE <table> ALTER COLUMN <column> TYPE bigint;` plus, for a
`serial` column, `ALTER SEQUENCE <sequence> AS bigint;` (an identity
column's sequence follows the column). Widen the referencing foreign key
columns as well; `foreign_key_type_mismatch` reports the ones you miss.

Turn it off (`disable = ["int4_primary_key"]`) if the schema has many small
lookup tables with `serial` keys and you accept the limit.

## sequence_near_overflow

**Finds** non-cycling sequences with `sequence_remaining_percent` (default
20) percent or less of their range left, and names the column that owns the
sequence.

**When it matters.** A fresh test database never gets near the limit; this
check is for suites that run against a restored production snapshot, and for
sequences deliberately started high.

**Fix:** widen the owning column to `bigint` and the sequence with
`ALTER SEQUENCE <sequence> AS bigint;` (the maximum follows the type when it
was the type's maximum). For a `bigint` sequence the fix is advice only.

## foreign_key_type_mismatch

**Finds** foreign key columns whose type or type modifier differs from the
referenced column: `integer` referencing `bigint`, `varchar(20)` referencing
`varchar(10)`. One finding per mismatched column; the object is
`<constraint>.<column>`.

**Why.** The narrower side overflows or truncates first (a typical case: the
parent's key was widened to `bigint`, the children were not), and every
check and join converts types.

**Fix:** `ALTER TABLE <table> ALTER COLUMN <column> TYPE <referenced type>;`

## nullable_unique_column

**Finds** unique indexes and constraints over two or more columns where a
key column is nullable.

**Why.** `NULL` never equals `NULL`, so `UNIQUE (tenant_id, email)` accepts
any number of `(NULL, 'a@example.com')` rows. That is rarely what the
constraint was meant to say.

**Skipped:** single-column unique indexes (an optional unique value with
many NULLs is usually intended), indexes created with `NULLS NOT DISTINCT`
(PostgreSQL 15+), and partial indexes whose predicate names the column
(`WHERE tenant_id IS NOT NULL`; the name must appear as a whole word).

**Fix:** `ALTER TABLE <table> ALTER COLUMN <column> SET NOT NULL;`, or on
PostgreSQL 15+ recreate the constraint with `UNIQUE NULLS NOT DISTINCT`.

## not_valid_constraint

**Finds** `CHECK` and foreign key constraints added with `NOT VALID` and
never validated. Adding a constraint `NOT VALID` and validating it in a
second migration is the safe way to add one to a big table; forgetting the
second step leaves existing rows unchecked, and the planner cannot use the
constraint.

**Fix:** `ALTER TABLE <table> VALIDATE CONSTRAINT <constraint>;`

## duplicate_foreign_key

**Finds** foreign keys identical to another one on the same table: same
columns, same referenced table and columns, same `ON DELETE` and
`ON UPDATE` actions, match type and deferrability. Foreign keys that differ
in any of these behave differently and are not reported. Each is checked on every
insert and update. The first by name is kept; the others are reported.

**Fix:** `ALTER TABLE <table> DROP CONSTRAINT <constraint>;`

## btree_index_on_array (off by default)

**Finds** B-tree indexes with an array column among their key columns. A
B-tree serves comparisons of the whole array (`=`, `<`), not containment or
overlap (`@>`, `<@`, `&&`), which need a GIN index. Off by default because
whole-array equality is a legitimate use; turn it on with
`enable = ["btree_index_on_array"]`.

**Fix:** `CREATE INDEX CONCURRENTLY ON <table> USING gin (<column>);`, and
for a single-column index that no constraint needs, drop the B-tree index.
A multi-column or constraint index is kept: the fix only adds the GIN index.

## Origin of the queries

Nine queries are ported from
[pg-index-health-sql](https://github.com/mfvanek/pg-index-health-sql)
(Apache-2.0); each file's header names the original and lists the changes.
`overlapping_index`, `int4_primary_key` and `nullable_unique_column` were
written for pytest-pghealth. See [NOTICE](../NOTICE).
