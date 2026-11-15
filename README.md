# pytest-pghealth

PostgreSQL schema health checks in your test suite. Your tests already build
the real schema from your migrations; pytest-pghealth reads the catalog of
that test database and fails the run when it finds a structural problem:

- a foreign key with no index behind it,
- duplicate, overlapping and invalid indexes,
- tables without a primary key,
- `integer` primary keys and sequences that will run out,
- foreign keys whose column type differs from the column they reference,
- multi-column unique constraints that NULLs slip through,
- constraints added `NOT VALID` and never validated.

Every finding names the table and the object, says why it matters, and gives
the SQL that fixes it. No extension to install, no production statistics, no
running database beyond the one your tests use.

```text
=================================== pghealth ===================================
PostgreSQL 18.1, all user schemas: 11 checks, 3 problems (fixture engine)

foreign_key_without_index: public.order_items.order_items_order_id_fkey
  Foreign key order_items_order_id_fkey (order_id) has no index: every delete or key update in orders scans public.order_items, and joins on these columns cannot use an index.
  fix: CREATE INDEX CONCURRENTLY ON public.order_items (order_id);

overlapping_index: public.orders.ix_orders_customer_id
  Index ix_orders_customer_id is a leading prefix of ix_orders_customer_id_created_at, which serves the same lookups; the shorter index only adds write cost.
  fix: DROP INDEX CONCURRENTLY public.ix_orders_customer_id;

int4_primary_key: public.events.id
  Primary key public.events.id is integer fed by a sequence: inserts start failing after 2,147,483,647 rows, and widening it later rewrites the table.
  fix: ALTER TABLE public.events ALTER COLUMN id TYPE bigint;
       -- and every foreign key column that references it

To accept a finding, add an ignore with a reason to pyproject.toml:
  [[tool.pghealth.ignore]]
  check = "foreign_key_without_index"
  object = "public.order_items.order_items_order_id_fkey"
  reason = "why this is fine here"
========================= 42 passed in 3.17s =========================
```

## Why in the tests

These problems are invisible in code review. An unindexed foreign key is one
missing line in a migration; it shows up months later as a `DELETE` on the
parent table that scans a child table of ten million rows while holding
locks. An `integer` key fails on row 2,147,483,648, when widening it means
rewriting the table under load. A failed `CREATE INDEX CONCURRENTLY` leaves
an invalid index that every write still maintains.

All of them are visible in the catalog as soon as the migrations have run,
and the test database is the cheapest place where that happens on every
pull request.

## Install

```bash
uv add --dev pytest-pghealth   # or: pip install pytest-pghealth
```

pytest-pghealth needs pytest 8+ and Python 3.11+. It uses whatever
PostgreSQL driver you already have (psycopg, psycopg2 or pg8000, directly or
through SQLAlchemy); it does not install one. PostgreSQL 14 to 18 are tested.

## Use it

### A test that checks the schema

The `pghealth` fixture takes whatever your suite already has: a SQLAlchemy
engine, connection or session (async engines too), a Django connection, a
DB-API connection or a URL.

```python
def test_schema_is_healthy(pghealth, engine):
    pghealth.assert_healthy(engine)
```

`assert_healthy` fails the test with the report above; `check` returns the
`Report` without failing. Both take overrides:
`pghealth.assert_healthy(engine, schemas=["billing"], disable=["int4_primary_key"])`.

### A check at the end of the session

Turn on the session check and tell it where the database is:

```toml
# pyproject.toml
[tool.pghealth]
session = true
fixture = "engine"      # a session-scoped fixture that returns an engine, a connection or a URL
```

or pass `--pghealth` on the command line (and `--no-pghealth` to turn it off).
The check runs after the last test and before the session fixtures are torn
down, so a schema created by a fixture and dropped at teardown is still
there. A finding fails the run (exit code 1) unless `--pghealth-warn` or
`warn_only = true`.

The session check finds the database, in this order:

1. `--pghealth-url`, `url` in `[tool.pghealth]`, or `$PGHEALTH_URL`;
2. targets registered from a fixture:

   ```python
   @pytest.fixture(scope="session")
   def engine(pghealth_session):
       engine = create_engine(TEST_DATABASE_URL)
       run_migrations(engine)
       pghealth_session.register(engine)
       yield engine
       engine.dispose()
   ```

3. the session-scoped fixture named by `fixture` (if a test used it);
4. with pytest-django, the Django test database (`django_alias`, default
   `"default"`), if a test used it.

Under pytest-xdist a URL is checked once, by the controller; otherwise each
worker checks its own database and the controller reports the findings once.

A run that checks nothing fails when `--pghealth` was typed on the command
line, so a CI job that asks for the check cannot pass without it; enabled in
`pyproject.toml` (or `addopts`), a run of a few tests that never touch the
database just says so.

`pytest --pghealth-list-checks` lists the checks and which of them run with
your configuration.

### From any code

```python
from pytest_pghealth import run_checks

report = run_checks("postgresql://localhost/app_test")
print(report.format())
```

## The checks

| Check | Finds | Fix |
|---|---|---|
| `foreign_key_without_index` | foreign keys whose columns are not the leading columns of a valid, non-partial index | `CREATE INDEX CONCURRENTLY` |
| `duplicate_index` | indexes identical to another one (columns, operator classes, collations, order, expressions, predicate) | drop the copy (or its constraint) |
| `overlapping_index` | B-tree indexes that are a leading prefix of another index with the same predicate; unique and constraint indexes are never reported | `DROP INDEX CONCURRENTLY` |
| `invalid_index` | invalid indexes, usually left by a failed `CREATE INDEX CONCURRENTLY` | `REINDEX INDEX CONCURRENTLY` |
| `table_without_primary_key` | tables (and partitioned tables) without a primary key | `ADD PRIMARY KEY USING INDEX` when a unique NOT NULL index exists, else an identity column |
| `int4_primary_key` | `integer`/`smallint` primary keys fed by a sequence or identity | `ALTER COLUMN ... TYPE bigint` |
| `sequence_near_overflow` | sequences with 20% or less of their range left (useful on a restored production snapshot) | widen the column and sequence |
| `foreign_key_type_mismatch` | foreign key columns whose type or length differs from the referenced column | `ALTER COLUMN ... TYPE` |
| `nullable_unique_column` | multi-column unique indexes and constraints with a nullable column (not `NULLS NOT DISTINCT`) | `SET NOT NULL` or `NULLS NOT DISTINCT` |
| `not_valid_constraint` | `CHECK` and foreign key constraints added `NOT VALID` and never validated | `VALIDATE CONSTRAINT` |
| `duplicate_foreign_key` | foreign keys identical to another one | `DROP CONSTRAINT` |
| `btree_index_on_array` (off by default) | B-tree indexes on array columns, which cannot serve `@>`, `<@` or `&&` | a GIN index |

[docs/checks.md](docs/checks.md) explains each check, what it skips and why.
Objects that belong to extensions and PostgreSQL's own schemas are never
reported.

Django note: Django indexes foreign keys by itself, so
`foreign_key_without_index` mostly catches `db_index=False` and raw SQL
migrations there; the index, primary key, `int4` and constraint checks apply
as they are.

## Configuration

```toml
[tool.pghealth]
session = true                      # the end-of-session check (or --pghealth)
url = "postgresql://localhost/test" # or fixture = "engine", or $PGHEALTH_URL
schemas = ["public", "billing"]     # default: every non-system schema
disable = ["int4_primary_key"]      # or --pghealth-disable
enable = ["btree_index_on_array"]   # or --pghealth-enable; "all" for every check
sequence_remaining_percent = 10     # default 20
warn_only = false                   # report without failing (or --pghealth-warn)
json = "pghealth.json"              # JSON report (or --pghealth-json)

[[tool.pghealth.ignore]]
check = "foreign_key_without_index" # a pattern; omit for every check
object = "public.audit_log"         # schema.table or schema.table.object, a pattern
reason = "append-only; rows in the parent table are never deleted"
```

An ignore needs a reason, so the exception stays documented next to the
code. Unknown keys and check names are an error, not silently ignored. All
options: [docs/configuration.md](docs/configuration.md).

The JSON report (`--pghealth-json`) lists the findings with their fix SQL,
the ignored findings with their reasons and any check that failed to run;
its `format` key is versioned.

## Compared with

| | pytest-pghealth | [pg-index-health](https://github.com/mfvanek/pg-index-health) | [pglinter](https://github.com/pmpetit/pglinter) | [squawk](https://github.com/sbdchd/squawk) |
|---|---|---|---|---|
| Language / runs in | Python, pytest | Java, JUnit / Spring Boot | PostgreSQL extension (Rust) | CLI (Rust) |
| Looks at | the migrated test database | a live database | a live database | migration SQL files |
| Needs installed in the database | nothing | nothing | the extension | no database |
| Works with the stock `postgres` image in CI | yes | yes | no | n/a |
| Output | findings with fix SQL, text and JSON | Java objects for assertions; a generator of foreign key index migrations | SARIF | rule violations with advice |
| Production statistics (bloat, unused indexes) | no, by design | yes | n/a | no |

pg-index-health is the mature tool for this on the JVM, and nine of the
queries here are ported from its SQL (see [Credits](#credits)). squawk lints
the migration *statements* (locks, unsafe `ALTER`s) and complements this:
squawk asks "is this migration safe to run?", pytest-pghealth asks "is the
schema it produces sound?". Production-only checks (bloat, unused indexes)
are left out on purpose: on a test database their numbers mean nothing.

It pairs with [pytest-queryguard](https://github.com/MaciejZiel/pytest-queryguard),
which watches the queries your tests run (N+1, query budgets); pytest-pghealth
watches the schema they run against.

## Credits

The SQL of `foreign_key_without_index`, `duplicate_index`, `invalid_index`,
`table_without_primary_key`, `sequence_near_overflow`,
`foreign_key_type_mismatch`, `not_valid_constraint`, `duplicate_foreign_key`
and `btree_index_on_array` is ported from
[pg-index-health-sql](https://github.com/mfvanek/pg-index-health-sql) by
Ivan Vakhrushev and others, under the Apache License 2.0. Each ported file
keeps its copyright line and says what was changed; see [NOTICE](NOTICE) and
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt). The rest of
pytest-pghealth is under the [MIT License](LICENSE).
