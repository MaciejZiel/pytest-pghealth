# Checking a long-lived database

A test database is built from scratch on every run, so some problems never
show up in it: an invalid index left by a `CREATE INDEX CONCURRENTLY` that
failed in production, a sequence that has used most of its range, a
constraint added `NOT VALID` by hand, a table someone switched to
`UNLOGGED` during a migration and never back. Those live in the databases
that have been running for years.

The `pghealth` command runs the same checks against any database, outside
pytest: staging, a restored production snapshot, a read replica.

```bash
pghealth "postgresql://pghealth@staging-db/app"
# or
PGHEALTH_URL="postgresql://pghealth@staging-db/app" pghealth --format github
```

It is installed with pytest-pghealth and uses the PostgreSQL driver you
already have (psycopg, psycopg2, or pg8000 with SQLAlchemy and a
`postgresql+pg8000://` URL). With psycopg or psycopg2, a libpq connection
string (`"host=staging-db dbname=app user=pghealth"`) works too, and so do
the usual `PG*` environment variables and `~/.pgpass`.

## Is it safe to run against production?

It is designed to be:

- **Read-only.** Everything runs in one `BEGIN TRANSACTION READ ONLY`
  transaction. Before the first check, `pghealth` asks PostgreSQL whether
  the transaction is read-only; if PostgreSQL does not confirm it, nothing
  runs and the exit status is 2. Any write, by a bug or otherwise, fails
  with "cannot execute ... in a read-only transaction". The transaction is
  rolled back when the connection closes.
- **Nothing outlives the run.** Its settings are `SET LOCAL`, so they end
  with the transaction; behind a transaction-mode pooler such as
  pgbouncer, no setting is left on a server connection that your
  application uses next.
- **Catalog queries only.** Every check is one `SELECT` over the system
  catalog (`pg_class`, `pg_index`, `pg_constraint`, `pg_attribute`,
  `pg_sequence`, `pg_depend`). No check reads table rows or locks a table.
  `sequence_near_overflow` reads each sequence's current value with
  `pg_sequence_last_value`, which briefly locks that sequence and does not
  advance it. The SQL
  of every check is in
  [`src/pytest_pghealth/sql/`](../src/pytest_pghealth/sql/).
- **Bounded.** `statement_timeout` is 60 seconds per check
  (`--statement-timeout`), and `lock_timeout` is 5 seconds, so a check
  never queues behind a migration's exclusive lock.
- **Identifiable.** `application_name` is `pghealth` for the transaction.
- **Fixes are printed, never run.** The fix SQL in the report is text for
  you to review and put in a migration.

The least privilege it needs is `CONNECT` on the database: the catalog is
readable by every role. For `sequence_near_overflow` to see a sequence's
value the role needs `SELECT` or `USAGE` on it (or the `pg_read_all_data`
role, PostgreSQL 14+); without it, the sequence counts as unused and is not
reported. A dedicated role is the cleanest:

```sql
CREATE ROLE pghealth LOGIN PASSWORD '...';
GRANT pg_read_all_data TO pghealth;   -- optional: for sequence_near_overflow
```

## Options

| Option | Meaning |
|---|---|
| `DSN` (argument) | database URL or libpq connection string; default `$PGHEALTH_URL` |
| `--config PATH` | the `pyproject.toml` whose `[tool.pghealth]` to use; default `./pyproject.toml` if there is one |
| `--no-config` | ignore any `pyproject.toml` |
| `--enable CHECKS`, `--disable CHECKS` | comma-separated, added to the configuration's lists (`all` works) |
| `--schema NAME` | check only this schema; repeatable |
| `--sequence-remaining-percent N` | the `sequence_near_overflow` threshold (default 20) |
| `--include-framework-tables` | also report findings on [framework-owned tables](configuration.md#framework-owned-tables) |
| `--format text\|json\|github` | the output on stdout (default `text`) |
| `--json PATH` | also write the JSON report to `PATH` |
| `--warn` | exit 0 even when there are findings (a check that failed still exits 1) |
| `--statement-timeout DURATION` | per check (default `60s`) |
| `--list-checks` | list the checks and the active ignores, and exit |

From `[tool.pghealth]` it uses `enable`, `disable`, `schemas`, `ignore`,
`ignore_framework_tables` and `sequence_remaining_percent`; the keys of the
pytest session check (`session`, `url`, `fixture`, ...) are accepted and not
used. The `url` there names your test database, so `pghealth` never
connects to it: pass the DSN.

Exit status: 0 healthy (or findings with `--warn`), 1 a finding or a check that failed,
2 a usage, configuration or connection error.

## In GitHub Actions

`--format github` prints one
[annotation](https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#setting-an-error-message)
per finding (`::error`, or `::warning` with `--warn`), with the message and
the fix, followed by the summary line:

```yaml
  schema-health:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10
      - run: uv sync
      - run: uv run pghealth --format github --json pghealth.json
        env:
          PGHEALTH_URL: ${{ secrets.STAGING_PGHEALTH_URL }}
```

GitHub shows at most ten annotations of each level per step; the full list
is in the log and in the JSON report.

A scheduled run against staging (`on: schedule`) catches what migrations
did not: a failed concurrent index build, a sequence approaching its limit.
