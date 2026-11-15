"""Running the checks against a database.

A target is whatever the test suite already has: a database URL, a
SQLAlchemy engine, connection or session (sync or async engine), a Django
connection, or a DB-API connection (psycopg, psycopg2, pg8000).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from pytest_pghealth.checks import CHECKS
from pytest_pghealth.config import ConfigError, Settings
from pytest_pghealth.findings import CheckError, IgnoredFinding, Report

Fetch = Callable[[str], list[dict[str, Any]]]


def run_checks(
    target: Any, settings: Settings | None = None, **overrides: Any
) -> Report:
    """Run the enabled checks against ``target`` and return the report.

    ``overrides`` replace fields of ``settings`` (``enable``, ``disable``,
    ``schemas``, ``ignore``, ``sequence_remaining_percent``).
    """
    settings = (settings or Settings()).with_overrides(**overrides)
    schema_filter = _schema_filter(settings.schemas)
    with connect(target) as fetch:
        version = fetch(
            "select current_setting('server_version') as version, "
            "current_setting('server_version_num')::int as version_num"
        )[0]
        replacements = {
            "{schema_filter}": schema_filter,
            "{remaining_percent}": f"{settings.sequence_remaining_percent:.4f}",
            "{nulls_not_distinct}": (
                "pi.indnullsnotdistinct"
                if int(version["version_num"]) >= 150000
                else "false"
            ),
        }
        report = Report(
            server_version=str(version["version"]).split(" ", 1)[0],
            schemas=settings.schemas,
            checks=settings.checks,
        )
        for name in settings.checks:
            check = CHECKS[name]
            sql = check.sql()
            for placeholder, value in replacements.items():
                sql = sql.replace(placeholder, value)
            try:
                rows = fetch(sql)
            except Exception as error:  # noqa: BLE001 - reported per check, the others still run
                report.errors.append(CheckError(name, _one_line(error)))
                continue
            for row in rows:
                finding = check.build(row)
                ignore = next((i for i in settings.ignore if i.matches(finding)), None)
                if ignore is None:
                    report.findings.append(finding)
                else:
                    report.ignored.append(IgnoredFinding(finding, ignore.reason))
    return report


def _one_line(error: BaseException) -> str:
    text = str(error).strip().splitlines()
    return f"{type(error).__name__}: {text[0] if text else ''}".rstrip(": ")


def _literal(value: str) -> str:
    if "\\" in value or "\x00" in value:
        raise ConfigError(f"schema name {value!r} contains a backslash or NUL")
    return "'" + value.replace("'", "''") + "'"


def _schema_filter(schemas: tuple[str, ...] | None) -> str:
    if schemas is None:
        return "(nsp.nspname <> 'information_schema' and left(nsp.nspname, 3) <> 'pg_')"
    if not schemas:
        raise ConfigError("schemas must name at least one schema")
    return f"nsp.nspname in ({', '.join(_literal(s) for s in schemas)})"


def _dbapi_fetch(connection: Any) -> Fetch:
    def fetch(sql: str) -> list[dict[str, Any]]:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    return fetch


def _sqlalchemy_fetch(connection: Any) -> Fetch:
    def fetch(sql: str) -> list[dict[str, Any]]:
        result = connection.exec_driver_sql(sql)
        return [dict(row) for row in result.mappings().all()]

    return fetch


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _sqlalchemy_types() -> dict[str, Any] | None:
    if not _has_module("sqlalchemy"):
        return None
    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.orm import Session

    types: dict[str, Any] = {
        "engine": Engine,
        "connection": Connection,
        "session": Session,
    }
    try:
        from sqlalchemy.ext.asyncio import AsyncEngine
    except ImportError:  # pragma: no cover - present in every SQLAlchemy 2
        pass
    else:
        types["async_engine"] = AsyncEngine
    return types


def _sync_driver() -> str | None:
    for module, driver in (
        ("psycopg", "psycopg"),
        ("psycopg2", "psycopg2"),
        ("pg8000", "pg8000"),
    ):
        if _has_module(module):
            return driver
    return None


@contextmanager
def connect(target: Any) -> Iterator[Fetch]:
    """Yield a function that runs a query on ``target`` and returns its rows."""
    if isinstance(target, str):
        with _connect_url(target) as fetch:
            yield fetch
        return

    sqlalchemy = _sqlalchemy_types()
    if sqlalchemy is not None:
        if "async_engine" in sqlalchemy and isinstance(
            target, sqlalchemy["async_engine"]
        ):
            url = target.url
            driver = _sync_driver()
            if driver is None:
                raise TypeError(
                    "an async engine needs a synchronous driver for the checks: "
                    "install psycopg, psycopg2 or pg8000"
                )
            with _connect_url(
                url.set(drivername=f"postgresql+{driver}").render_as_string(
                    hide_password=False
                )
            ) as fetch:
                yield fetch
            return
        if isinstance(target, sqlalchemy["engine"]):
            _require_postgresql(target.dialect.name)
            with target.connect() as connection:
                yield _sqlalchemy_fetch(connection)
            return
        if isinstance(target, sqlalchemy["connection"]):
            _require_postgresql(target.dialect.name)
            yield _sqlalchemy_fetch(target)
            return
        if isinstance(target, sqlalchemy["session"]):
            connection = target.connection()
            _require_postgresql(connection.dialect.name)
            yield _sqlalchemy_fetch(connection)
            return

    vendor = getattr(target, "vendor", None)
    if isinstance(vendor, str) and hasattr(target, "settings_dict"):
        # a Django database connection
        _require_postgresql(vendor)
        yield _dbapi_fetch(target)
        return

    if callable(getattr(target, "cursor", None)):
        yield _dbapi_fetch(target)
        return

    raise TypeError(
        f"cannot run pghealth checks on {type(target).__name__}: pass a database URL, "
        "a SQLAlchemy engine, connection or session, a Django connection, "
        "or a DB-API connection"
    )


def _require_postgresql(name: str) -> None:
    if name not in ("postgresql", "postgres"):
        raise TypeError(f"pghealth checks need PostgreSQL, not {name}")


@contextmanager
def _connect_url(url: str) -> Iterator[Fetch]:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    scheme = url.split("://", 1)[0] if "://" in url else ""
    if "+" in scheme:
        # an explicit SQLAlchemy driver: postgresql+pg8000://...
        with _connect_sqlalchemy(url) as fetch:
            yield fetch
        return
    if _has_module("psycopg"):
        import psycopg

        with psycopg.connect(url, autocommit=True) as connection:
            yield _dbapi_fetch(connection)
        return
    if _has_module("psycopg2"):
        import psycopg2

        connection = psycopg2.connect(url)
        connection.autocommit = True
        try:
            yield _dbapi_fetch(connection)
        finally:
            connection.close()
        return
    if scheme and _has_module("pg8000") and _has_module("sqlalchemy"):
        with _connect_sqlalchemy(
            "postgresql+pg8000://" + url.split("://", 1)[1]
        ) as fetch:
            yield fetch
        return
    raise ConfigError(
        "no PostgreSQL driver to connect with: install psycopg (or psycopg2), "
        "or use a SQLAlchemy URL with a driver such as postgresql+pg8000://"
    )


@contextmanager
def _connect_sqlalchemy(url: str) -> Iterator[Fetch]:
    if not _has_module("sqlalchemy"):
        raise ConfigError(
            f"the URL {url.split('://', 1)[0]}:// needs SQLAlchemy installed"
        )
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    engine = create_engine(url, poolclass=NullPool)
    try:
        _require_postgresql(engine.dialect.name)
        with engine.connect() as connection:
            yield _sqlalchemy_fetch(connection)
    finally:
        engine.dispose()
