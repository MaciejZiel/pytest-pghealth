"""Tests against a real PostgreSQL server.

They run when PGHEALTH_TEST_URL names a server, as a SQLAlchemy URL such as
postgresql+pg8000://postgres:pg@localhost:5432/postgres (CI starts one per
PostgreSQL version). Without it they are skipped; with PGHEALTH_REQUIRE_DB=1
a missing URL fails them instead, so CI cannot pass by skipping.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator

import pytest

from pytest_pghealth import Finding, run_checks

URL = os.environ.get("PGHEALTH_TEST_URL")


@pytest.fixture(scope="session")
def engine() -> Iterator[object]:
    if not URL:
        if os.environ.get("PGHEALTH_REQUIRE_DB") == "1":
            pytest.fail("PGHEALTH_REQUIRE_DB=1 but PGHEALTH_TEST_URL is not set")
        pytest.skip("PGHEALTH_TEST_URL is not set")
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    # AUTOCOMMIT: CREATE INDEX CONCURRENTLY cannot run inside a transaction
    engine = create_engine(URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    yield engine
    engine.dispose()


class Schema:
    """A fresh schema; ``sql`` runs statements with it first on the search path."""

    def __init__(self, engine: object, name: str) -> None:
        self.engine = engine
        self.name = name

    def sql(self, *statements: str) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        with self.engine.connect() as connection:  # type: ignore[attr-defined]
            connection.exec_driver_sql(f"set search_path to {self.name}, public")
            for statement in statements:
                result = connection.exec_driver_sql(statement)
                if result.returns_rows:
                    rows = [tuple(row) for row in result]
        return rows

    def server_version_num(self) -> int:
        return int(self.sql("show server_version_num")[0][0])

    def check(self, *names: str, **overrides: object) -> list[Finding]:
        report = run_checks(
            self.engine,
            schemas=[self.name],
            disable=["all"],
            enable=list(names),
            **overrides,
        )
        assert not report.errors, report.errors
        return report.findings

    def targets(self, *names: str, **overrides: object) -> list[str]:
        prefix = f"{self.name}."
        return sorted(
            f.target.removeprefix(prefix) for f in self.check(*names, **overrides)
        )

    def apply(self, finding: Finding) -> None:
        """Run the finding's fix, skipping comment lines."""
        code = "\n".join(
            line
            for line in finding.fix.splitlines()
            if not line.lstrip().startswith("--")
        )
        statements = [part.strip() for part in code.split(";") if part.strip()]
        assert statements, f"no SQL in the fix of {finding}"
        with self.engine.connect() as connection:  # type: ignore[attr-defined]
            for statement in statements:
                connection.exec_driver_sql(statement)

    def apply_all(self, *names: str) -> None:
        findings = self.check(*names)
        assert findings
        for finding in findings:
            self.apply(finding)
        assert self.check(*names) == []


@pytest.fixture
def make_schema(engine: object) -> Iterator[Callable[[], Schema]]:
    created: list[Schema] = []

    def make() -> Schema:
        schema = Schema(engine, f"pgh_{uuid.uuid4().hex[:12]}")
        schema.sql(f"create schema {schema.name}")
        created.append(schema)
        return schema

    yield make
    for schema in created:
        schema.sql(f"drop schema {schema.name} cascade")


@pytest.fixture
def schema(make_schema: Callable[[], Schema]) -> Schema:
    return make_schema()
