# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The `pghealth` command runs the checks against any database outside
  pytest: staging, a restored production snapshot, a replica. It makes its
  session read-only (and refuses to run if PostgreSQL does not confirm it),
  sets a statement and lock timeout, reads the ignores of `./pyproject.toml`,
  and prints text, the JSON report or GitHub Actions annotations
  (`--format text|json|github`). See [docs/cli.md](docs/cli.md).
- Findings on tables that frameworks and packages create (`django.contrib`,
  django-taggit, django-celery-beat and -results, DRF's `authtoken_token`,
  Wagtail, Alembic's `alembic_version`, Celery's result tables, Flyway,
  Liquibase) are ignored by default, except `invalid_index` and
  `sequence_near_overflow`. `ignore_framework_tables = false` reports them;
  a list of package names limits the skip to those packages.
- `--pghealth-list-checks` also lists the active ignores, each with the
  file and position it comes from, and the framework-owned tables.
- Three checks ported from pg-index-health-sql: `json_column` (`json`
  instead of `jsonb`), `unlogged_table`, and `timestamp_without_time_zone`
  (`timestamp` without time zone and `timetz`; off by default).
- An ignored finding records where its ignore comes from:
  `IgnoredFinding.source`, and `source` in the JSON report's `ignored`
  entries.

### Changed

- Thirteen checks run by default (eleven before): `json_column` and
  `unlogged_table` are on.

## [0.2.0] - 2026-11-15

### Added

- `--pghealth-list-checks` lists the checks, `on` or `off` for the current
  configuration, and exits.
- `overlapping_index` names the foreign key whose columns a redundant,
  non-partial index has (Django creates one such index per `ForeignKey`)
  and says the longer index still covers it.
- [Real projects](docs/real-projects.md): every check against the migrated
  schemas of the full-stack FastAPI template, Prefect, NetBox and Wagtail,
  each finding classified (313 findings, no false positives), and the
  workflow that reproduces it.
- The README has a "Pairs with pytest-queryguard" section.
- The session check works under pytest-xdist without a URL: each worker
  checks its own database and the controller reports each finding once.
  It needs pytest-xdist's `workeroutput`, present in every 3.x release.

### Changed

- A session check that checks nothing fails the run when `--pghealth` was
  typed on the command line; enabled in `pyproject.toml` or `addopts`, it
  stays a warning.
- Several registered targets: a finding found in more than one is reported
  once.

## [0.1.0] - 2026-11-15

The first release.

### Added

- Eleven structural checks of a PostgreSQL schema, each with a fix:
  `foreign_key_without_index`, `duplicate_index`, `overlapping_index`,
  `invalid_index`, `table_without_primary_key`, `int4_primary_key`,
  `sequence_near_overflow`, `foreign_key_type_mismatch`,
  `nullable_unique_column`, `not_valid_constraint` and
  `duplicate_foreign_key`; `btree_index_on_array` is available but off by
  default. Nine of the queries are ported from
  [pg-index-health-sql](https://github.com/mfvanek/pg-index-health-sql)
  (Apache-2.0, see `NOTICE`).
- `run_checks(target)` for a database URL, a SQLAlchemy engine, connection,
  session or async engine, a Django connection or a DB-API connection.
- The `pghealth` fixture (`check`, `assert_healthy`) and the
  `pghealth_session` fixture (`register`).
- The session check (`--pghealth` or `session = true`): runs before the
  session fixtures are torn down, against a URL, a registered target, a named
  session fixture or the pytest-django test database, and fails the run on a
  finding unless `--pghealth-warn`.
- Configuration in `[tool.pghealth]` of `pyproject.toml`: checks on and off,
  schemas, ignores with a required reason, the sequence threshold, and a JSON
  report (`--pghealth-json`).

[Unreleased]: https://github.com/MaciejZiel/pytest-pghealth/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MaciejZiel/pytest-pghealth/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MaciejZiel/pytest-pghealth/releases/tag/v0.1.0
