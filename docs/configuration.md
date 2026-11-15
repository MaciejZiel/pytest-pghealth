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
| `json` | path | none | `--pghealth-json PATH` |
| `ignore` | array of tables | none | |
| `ignore_framework_tables` | bool, or list of package names | `true` | |

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

When the session check finds nothing to check (no source configured, or
the fixture, registered target or Django database was not set up by any of
the selected tests), the run fails if `--pghealth` was typed on the command
line: that run asked for a check. Enabled by `session = true` or by
`--pghealth` in `addopts`, it only shows a warning, so `pytest tests/unit`
keeps passing. `--collect-only` never runs the check.

When the check cannot run (the database refuses the connection), the run
fails and the summary shows the error.

Under pytest-xdist, a URL is checked once, by the controller. Without one,
each worker runs the check against its own source (with pytest-django, its
own copy of the test database) and sends the report to the controller,
which reports each finding once, writes the JSON report and sets the exit
code. Each worker runs the checks, a few catalog queries, against its own
copy; that costs well under a second per worker. With `-x` or `--maxfail`
the workers' results are still reported.

`pytest --pghealth-list-checks` prints every check, `on` or `off` for the
current configuration, then the active ignores, each with the file and
position it comes from, and the built-in framework tables, and exits:

```text
pghealth checks (on: runs with this configuration)
  on   foreign_key_without_index    foreign keys with no index on the referencing columns
  ...
  off  btree_index_on_array         B-tree indexes on array columns (off by default)

ignores (1):
  check foreign_key_without_index, object public.audit_log: append-only; parents are never deleted
    from /home/me/app/pyproject.toml: ignore #1

framework-owned tables, skipped by every check except invalid_index and sequence_near_overflow
  from the built-in list (ignore_framework_tables = false to report them):
  django: django_migrations
  django.contrib.admin: django_admin_log
  ...
```

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

Keep ignores narrow: name the check, and the tables as exactly as you
can. `*` matches any characters, dots included, so `public.auth_*` also
matches a table of your own called `auth_tokens`.

An ignore in `pyproject.toml` is checked before the built-in framework
list, so its reason is the one reported.

## Framework-owned tables

Frameworks and third-party packages bring tables your migrations do not
define: `django.contrib.auth` has `integer` keys and per-`ForeignKey`
indexes you cannot change from your code. Findings on those tables are
ignored by default; the report counts them as ignored, with the package as
the reason, and the JSON report lists them with
`"source": "built-in (ignore_framework_tables)"`.

Two checks still report them: `invalid_index` and `sequence_near_overflow`
describe the state of your database (a failed index build, a sequence
running out), which you fix whoever wrote the migration.

| Package | Tables (in any schema) |
|---|---|
| `django` | `django_migrations` |
| `django.contrib.admin` | `django_admin_log` |
| `django.contrib.auth` | `auth_group`, `auth_group_permissions`, `auth_permission`, `auth_user`, `auth_user_groups`, `auth_user_user_permissions` |
| `django.contrib.contenttypes` | `django_content_type` |
| `django.contrib.sessions` | `django_session` |
| `django.contrib.sites` | `django_site` |
| `django.contrib.flatpages` | `django_flatpage`, `django_flatpage_sites` |
| `django.contrib.redirects` | `django_redirect` |
| `django-taggit` | `taggit_tag`, `taggit_taggeditem` |
| `django-celery-beat` | `django_celery_beat_*` |
| `django-celery-results` | `django_celery_results_*` |
| `djangorestframework (authtoken)` | `authtoken_token` |
| `wagtail` | `wagtailadmin_*`, `wagtailcore_*`, `wagtaildocs_*`, `wagtailembeds_*`, `wagtailforms_*`, `wagtailimages_*`, `wagtailredirects_*`, `wagtailsearch_*`, `wagtailsearchpromotions_*`, `wagtailusers_*` |
| `alembic` | `alembic_version` |
| `celery (database result backend)` | `celery_taskmeta`, `celery_tasksetmeta` |
| `flyway` | `flyway_schema_history` |
| `liquibase` | `databasechangelog`, `databasechangeloglock` |

The patterns match the table name, so a finding on your own table that
references `auth_user` is still reported. The tables of every package from
`django.contrib.admin` to `wagtail` in this list are skipped only in a
schema that also has `django_migrations`: a Flask or FastAPI application
with its own `auth_user` table is checked as usual. A project that uses the default
`auth.User` model for millions of users may want `auth_user` checked:

```toml
[tool.pghealth]
ignore_framework_tables = false                 # report every table (an empty list is an error)
# or only some packages:
ignore_framework_tables = ["django.contrib.admin", "django.contrib.contenttypes", "alembic"]
```

`pytest --pghealth-list-checks` (and `pghealth --list-checks`) prints the
list that is in effect.

## JSON report

`--pghealth-json PATH` (or `json`) writes the session check's report, relative
to the directory pytest was started in; missing directories are created:

```json
{
  "format": 1,
  "tool": "pytest-pghealth",
  "tool_version": "0.3.0",
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

Each entry of `ignored` is a finding plus its `reason` and `source` (the
file and position of the ignore, or the built-in framework list).
`format` changes when a key changes meaning or disappears. From code,
`Report.to_json()` gives the same document.

## Python API

```python
from pytest_pghealth import run_checks, Settings

report = run_checks(engine, schemas=["public"], disable=["int4_primary_key"])
report.ok  # no findings and no failed checks
report.findings  # list of Finding(check, schema, table, object, message, fix)
report.ignored  # list of IgnoredFinding(finding, reason, source)
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
