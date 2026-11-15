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
        report.summary() == "PostgreSQL 18.1, all user schemas: 11 checks, 0 problems"
    )
    # one version query, then one query per check, each on its own cursor
    assert len(connection.queries) == 12
    assert connection.closed_cursors == 12


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
    assert "left(nsp.nspname, 3) <> 'pg_'" in connection.queries[1]


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


def test_only_enabled_checks_run():
    connection = FakeConnection()
    report = run_checks(
        connection, Settings(disable=("all",), enable=("invalid_index",))
    )
    assert report.checks == ("invalid_index",)
    assert len(connection.queries) == 2
    report = run_checks(connection, enable=["btree_index_on_array"])
    assert "btree_index_on_array" in report.checks


def test_report_format_and_json():
    report = run_checks(FakeConnection({"foreign_key_without_index": [FK_ROW]}))
    text = report.format()
    assert text.splitlines()[0].startswith("pghealth: PostgreSQL 18.1")
    assert "foreign_key_without_index: public.orders.orders_customer_id_fkey" in text
    assert (
        "  fix: CREATE INDEX CONCURRENTLY orders_customer_id_idx ON public.orders (customer_id);"
        in text
    )
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
