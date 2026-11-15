# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/MaciejZiel/pytest-pghealth/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MaciejZiel/pytest-pghealth/releases/tag/v0.1.0
