import json

import pytest

from fakedb import FK_ROW, NO_PK_ROW, FakeConnection
from pytest_pghealth import ConfigError, Ignore, Settings, run_checks
from pytest_pghealth.runner import connect


def test_clean_database():
    connection = FakeConnection()
    report = run_checks(connection)
    assert report.ok
    assert report.server_version == "18.1"
    assert (
        report.summary() == "PostgreSQL 18.1, all user schemas: 13 checks, 0 problems"
    )
    # the version, the framework marker tables, the savepoint probe, then one query per check, each on
    # its own cursor
    assert len(connection.queries) == 16
    assert connection.closed_cursors == 16


def test_findings_and_ignores():
    connection = FakeConnection(
        {
            "foreign_key_without_index": [FK_ROW],
            "table_without_primary_key": [NO_PK_ROW],
        }
    )
    report = run_checks(
        connection,
        ignore=[
            Ignore("table_without_primary_key", "public.events", "append-only log")
        ],
    )
    assert not report.ok
    assert [f.check for f in report.findings] == ["foreign_key_without_index"]
    assert report.ignored[0].reason == "append-only log"
    assert report.summary().endswith("1 problem, 1 ignored")


def test_placeholders_are_filled():
    connection = FakeConnection()
    run_checks(
        connection, schemas=["public", "o'brien"], sequence_remaining_percent=7.5
    )
    joined = "\n".join(connection.queries)
    assert "{" not in joined.replace("'{", "")  # no placeholder left
    assert "nsp.nspname in ('public', 'o''brien')" in joined
    assert "<= 7.5000" in joined
    assert "pi.indnullsnotdistinct" in joined


def test_nulls_not_distinct_needs_postgresql_15():
    connection = FakeConnection(version="14.12", version_num=140012)
    run_checks(connection)
    joined = "\n".join(connection.queries)
    assert "indnullsnotdistinct" not in joined
    assert "not false and" in joined


def test_default_schema_filter_skips_system_schemas():
    connection = FakeConnection()
    run_checks(connection)
    assert "left(nsp.nspname, 3) <> 'pg_'" in connection.queries[3]


def test_bad_schema_names():
    with pytest.raises(ConfigError, match="backslash"):
        run_checks(FakeConnection(), schemas=["a\\b"])
    with pytest.raises(ConfigError, match="at least one"):
        run_checks(FakeConnection(), schemas=[])


def test_a_failing_check_does_not_stop_the_others():
    connection = FakeConnection(
        {"foreign_key_without_index": [FK_ROW]}, failing=("sequence_near_overflow",)
    )
    report = run_checks(connection)
    assert len(report.findings) == 1
    assert report.errors[0].check == "sequence_near_overflow"
    assert (
        report.errors[0].error
        == "RuntimeError: permission denied for sequence_near_overflow"
    )
    assert not report.ok
    assert "sequence_near_overflow: the check failed" in report.format()


def test_a_failing_check_is_rolled_back_to_a_savepoint():
    connection = FakeConnection(failing=("invalid_index",), in_transaction=True)
    settings = Settings(disable=("all",), enable=("invalid_index", "duplicate_index"))
    report = run_checks(connection, settings)
    assert [e.check for e in report.errors] == ["invalid_index"]
    assert connection.savepoint_log == [
        "savepoint pghealth_probe",
        "release savepoint pghealth_probe",
        "savepoint pghealth_check",
        "release savepoint pghealth_check",
        "savepoint pghealth_check",
        "rollback to savepoint pghealth_check",
        "release savepoint pghealth_check",
    ]


def test_no_savepoints_in_autocommit():
    connection = FakeConnection()
    run_checks(connection)
    assert connection.savepoint_log == []


def test_async_url_gets_a_sync_driver(monkeypatch):
    from contextlib import contextmanager

    from pytest_pghealth import runner

    seen = []

    @contextmanager
    def fake_connect(url):
        seen.append(url)
        yield runner._dbapi_fetch(FakeConnection())

    monkeypatch.setattr(runner, "_connect_sqlalchemy", fake_connect)
    monkeypatch.setattr(runner, "_sync_driver", lambda: "pg8000")
    run_checks("postgresql+asyncpg://u:p@db/app")
    assert seen == ["postgresql+pg8000://u:p@db/app"]
    monkeypatch.setattr(runner, "_sync_driver", lambda: None)
    with pytest.raises(ConfigError, match="async driver"):
        run_checks("postgresql+asyncpg://u:p@db/app")


def test_overrides_are_validated():
    with pytest.raises(ConfigError, match="between 0 and 100"):
        run_checks(FakeConnection(), sequence_remaining_percent=150)
    report = run_checks(FakeConnection(), schemas=(name for name in ["public"]))
    assert report.schemas == ("public",)


def test_ignore_snippet_is_escaped():
    row = dict(FK_ROW, constraint_name='we"ird*fk')
    text = run_checks(FakeConnection({"foreign_key_without_index": [row]})).format()
    assert 'object = "public.orders.we\\"ird[*]fk"' in text


def test_only_enabled_checks_run():
    connection = FakeConnection()
    report = run_checks(
        connection, Settings(disable=("all",), enable=("invalid_index",))
    )
    assert report.checks == ("invalid_index",)
    assert len(connection.queries) == 4
    report = run_checks(connection, enable=["btree_index_on_array"])
    assert "btree_index_on_array" in report.checks


def test_report_format_and_json():
    report = run_checks(FakeConnection({"foreign_key_without_index": [FK_ROW]}))
    text = report.format()
    assert text.splitlines()[0].startswith("pghealth: PostgreSQL 18.1")
    assert "foreign_key_without_index: public.orders.orders_customer_id_fkey" in text
    assert "  fix: CREATE INDEX CONCURRENTLY ON public.orders (customer_id);" in text
    assert 'object = "public.orders.orders_customer_id_fkey"' in text
    data = json.loads(report.to_json())
    assert data["format"] == 1
    assert data["tool"] == "pytest-pghealth"
    assert data["ok"] is False
    assert data["findings"][0]["target"] == "public.orders.orders_customer_id_fkey"
    assert data["schemas"] is None


def test_multiline_fix_is_indented():
    report = run_checks(FakeConnection({"table_without_primary_key": [NO_PK_ROW]}))
    lines = report.format().splitlines()
    position = next(i for i, line in enumerate(lines) if line.startswith("  fix: "))
    assert lines[position + 1].startswith("       -- or ADD PRIMARY KEY")


def test_unknown_target():
    with (
        pytest.raises(TypeError, match="cannot run pghealth checks on int"),
        connect(42),
    ):
        pass


def test_sqlite_engine_is_rejected():
    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine("sqlite://")
    with pytest.raises(TypeError, match="need PostgreSQL, not sqlite"):
        run_checks(engine)


def test_url_without_driver(monkeypatch):
    from pytest_pghealth import runner

    monkeypatch.setattr(runner, "_has_module", lambda name: False)
    with pytest.raises(ConfigError, match="no PostgreSQL driver"):
        run_checks("postgresql://localhost/x")


def test_framework_tables_are_ignored_except_state_checks():
    django = {
        **FK_ROW,
        "table_name": "auth_permission",
        "table_ident": "public.auth_permission",
    }
    invalid = {
        "schema_name": "public",
        "table_name": "django_session",
        "table_ident": "public.django_session",
        "index_name": "django_session_expire_date_idx",
        "index_ident": "public.django_session_expire_date_idx",
        "schema_ident": "public",
        "index_kind": "i",
    }
    markers = [{"schema_name": "public", "table_name": "django_migrations"}]
    connection = FakeConnection(
        {
            "foreign_key_without_index": [django, FK_ROW],
            "invalid_index": [invalid],
            "framework_markers": markers,
        }
    )
    report = run_checks(connection)
    assert [f.table for f in report.findings] == ["orders", "django_session"]
    [ignored] = report.ignored
    assert ignored.finding.table == "auth_permission"
    assert ignored.reason.startswith("framework-owned table (django.contrib.auth)")
    assert ignored.source == "built-in (ignore_framework_tables)"
    assert json.loads(report.to_json())["ignored"][0]["source"] == ignored.source

    report = run_checks(connection, ignore_framework_tables=False)
    assert len(report.findings) == 3 and not report.ignored
    report = run_checks(connection, ignore_framework_tables=["alembic"])
    assert len(report.findings) == 3


def test_django_tables_need_django_migrations_in_the_same_schema():
    # a Flask application's own auth_user is checked
    flask = {**FK_ROW, "table_name": "auth_user", "table_ident": "public.auth_user"}
    report = run_checks(FakeConnection({"foreign_key_without_index": [flask]}))
    assert len(report.findings) == 1
    other_schema = [{"schema_name": "legacy", "table_name": "django_migrations"}]
    report = run_checks(
        FakeConnection(
            {"foreign_key_without_index": [flask], "framework_markers": other_schema}
        )
    )
    assert len(report.findings) == 1


def test_a_configured_ignore_comes_first():
    django = {
        **FK_ROW,
        "table_name": "auth_permission",
        "table_ident": "public.auth_permission",
    }
    report = run_checks(
        FakeConnection({"foreign_key_without_index": [django]}),
        ignore=[Ignore("*", "public.auth_*", "ours", source="here")],
    )
    assert [(i.reason, i.source) for i in report.ignored] == [("ours", "here")]
