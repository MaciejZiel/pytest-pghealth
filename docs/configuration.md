# Configuration

pytest-pghealth reads the `[tool.pghealth]` table of the `pyproject.toml`
pytest uses (its config file when that is a `pyproject.toml`, otherwise the
one in the root directory). Command-line options override it. An unknown
key, an unknown check name or an ignore without a reason stops pytest with a
usage error.

## Keys

| Key | Type | Default | Command line |
|---|---|---|---|
| `session` | bool | `false` | `--pghealth`, `--no-pghealth` |
| `url` | string | `$PGHEALTH_URL` | `--pghealth-url URL` |
| `fixture` | string | none | |
| `django_alias` | string | `"default"` | |
| `schemas` | list of strings | every non-system schema | |
| `enable` | list of check names, or `"all"` | | `--pghealth-enable a,b` |
| `disable` | list of check names, or `"all"` | | `--pghealth-disable a,b` |
| `sequence_remaining_percent` | number, 0-100 | `20` | |
| `warn_only` | bool | `false` | `--pghealth-warn` |
| | | | `--pghealth-list-checks` |
| `json` | path | none | `--pghealth-json PATH` |
| `ignore` | array of tables | none | |

`--pghealth-enable` and `--pghealth-disable` add to the lists in the file.

## Which checks run

Every check runs except `btree_index_on_array`. `enable` adds checks,
`disable` removes them, and a check named in both does not run. `"all"`
applies first: `disable = ["all"]` with `enable = ["invalid_index"]` runs
only `invalid_index`.

## Where the session check looks

With `session = true` (or `--pghealth`) the check runs once after the last
test, before the session-scoped fixtures are torn down, against the first of:

1. `--pghealth-url`, `url`, or the `PGHEALTH_URL` environment variable;
2. the targets registered with `pghealth_session.register(target)`;
3. the value of the session-scoped fixture named by `fixture`, if a test
   used it (a function-scoped fixture is gone by then);
4. with pytest-django, the test database of `django_alias`, if a test used
   the database.

When nothing is configured (no URL, no registered target, no `fixture`,
no pytest-django), the run fails: the check was asked for and cannot run.
When a source is configured but this run never set it up (the fixture or
the Django database was not used by any of the selected tests), the run
fails if `--pghealth` was on the command line, and only shows a warning if
the check was enabled by `session = true` alone, so `pytest tests/unit`
keeps passing. `--collect-only` never runs the check.

When the check cannot run (the database refuses the connection), the run
fails and the summary shows the error.

Under pytest-xdist, a URL is checked once, by the controller. Without one,
each worker runs the check against its own source (with pytest-django, its
own copy of the test database) and sends the report to the controller,
which reports each finding once, writes the JSON report and sets the exit
code.

`pytest --pghealth-list-checks` prints every check, `on` or `off` for the
current configuration, and exits.

A URL without a driver (`postgresql://...`) connects with psycopg, then
psycopg2, then pg8000 through SQLAlchemy, whichever is installed. A URL with
one (`postgresql+pg8000://...`) goes through SQLAlchemy.

## Ignores

```toml
[[tool.pghealth.ignore]]
check = "foreign_key_without_index"
object = "public.audit_log"
reason = "append-only; parents are never deleted"
```

- `check` is a shell-style pattern over check names (`*`, `?`, `[ab]`);
  omitted, it matches every check.
- `object` is a pattern over the finding's target (`schema.table.object`) or
  its table (`schema.table`), so `public.audit_log` accepts every finding on
  that table and `public.*` every finding in the schema. Matching is
  case-sensitive, on the unquoted names.
- `reason` is required.

Ignored findings do not fail the run; the summary counts them and the JSON
report lists them with their reasons.

## JSON report

`--pghealth-json PATH` (or `json`) writes the session check's report, relative
to the directory pytest was started in; missing directories are created:

```json
{
  "format": 1,
  "tool": "pytest-pghealth",
  "tool_version": "0.1.0",
  "server_version": "18.1",
  "schemas": null,
  "checks": ["foreign_key_without_index", "..."],
  "ok": false,
  "findings": [
    {
      "check": "foreign_key_without_index",
      "schema": "public",
      "table": "order_items",
      "object": "order_items_order_id_fkey",
      "target": "public.order_items.order_items_order_id_fkey",
      "message": "Foreign key order_items_order_id_fkey (order_id) has no index: ...",
      "fix": "CREATE INDEX CONCURRENTLY ON public.order_items (order_id);"
    }
  ],
  "ignored": [],
  "errors": []
}
```

`format` changes when a key changes meaning or disappears. From code,
`Report.to_json()` gives the same document.

## Python API

```python
from pytest_pghealth import run_checks, Settings

report = run_checks(engine, schemas=["public"], disable=["int4_primary_key"])
report.ok  # no findings and no failed checks
report.findings  # list of Finding(check, schema, table, object, message, fix)
report.ignored  # list of IgnoredFinding(finding, reason)
report.errors  # list of CheckError(check, error): a query that failed
print(report.format())
```

`run_checks` accepts a URL, a SQLAlchemy `Engine`, `Connection`, `Session` or
`AsyncEngine` (checked through a synchronous driver: psycopg, psycopg2 or
pg8000), a Django connection, or any DB-API connection. It only reads the
catalog; on a connection you pass in, it runs in whatever transaction that
connection has open, and wraps each check in a savepoint, so a check that
fails (for example on a privilege) leaves that transaction usable. A URL
with an async driver (`postgresql+asyncpg://`) is checked through a
synchronous one.
