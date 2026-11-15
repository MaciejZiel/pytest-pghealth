import json
from contextlib import contextmanager

import pytest

from fakedb import FK_ROW, FakeConnection
from pytest_pghealth import cli
from pytest_pghealth.runner import connect

INVALID_ROW = {
    "schema_name": "public",
    "table_name": "orders",
    "table_ident": "public.orders",
    "index_name": "ix_orders_status",
    "index_ident": "public.ix_orders_status",
    "schema_ident": "public",
    "index_kind": "i",
}


@pytest.fixture
def database(monkeypatch, tmp_path):
    """The CLI connects to a FakeConnection; no pyproject.toml in the directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PGHEALTH_URL", raising=False)
    state = {"connection": FakeConnection(), "dsn": None}

    @contextmanager
    def fake_connect(dsn):
        state["dsn"] = dsn
        with connect(state["connection"]) as fetch:
            yield fetch

    monkeypatch.setattr(cli, "connect", fake_connect)
    return state


def test_healthy_database(database, capsys):
    assert cli.main(["postgresql://staging/app"]) == 0
    assert database["dsn"] == "postgresql://staging/app"
    out = capsys.readouterr().out
    assert out.startswith(
        "pghealth: PostgreSQL 18.1, all user schemas: 13 checks, 0 problems"
    )


def test_the_session_is_read_only_before_any_check(database):
    connection = database["connection"]
    cli.main(["postgresql://staging/app", "--statement-timeout", "5min"])
    # one transaction, local settings only: nothing outlives it on a pooled connection
    assert connection.settings == [
        "begin transaction read only",
        "set transaction read only",
        "set local application_name = 'pghealth'",
        "set local statement_timeout = '5min'",
        "set local lock_timeout = '5s'",
    ]
    first_check = next(
        i for i, q in enumerate(connection.queries) if "-- pghealth:" in q
    )
    confirmed = connection.queries.index(
        "select current_setting('transaction_read_only') as read_only"
    )
    assert confirmed < first_check
    # inside the transaction, each check runs in a savepoint
    assert connection.savepoint_log


def test_a_transaction_already_open_is_made_read_only(database):
    database["connection"] = FakeConnection(in_transaction=True)
    assert cli.main(["postgresql://staging/app"]) == 0


def test_a_statement_timeout_with_a_backslash_is_refused(database, capsys):
    assert cli.main(["postgresql://x/y", "--statement-timeout", "1\\s"]) == 2
    assert "backslash" in capsys.readouterr().err


def test_refuses_to_run_without_read_only(database, capsys):
    database["connection"] = connection = FakeConnection(refuse_read_only=True)
    assert cli.main(["postgresql://staging/app"]) == 2
    assert "could not be made read-only" in capsys.readouterr().err
    assert not any("-- pghealth:" in q for q in connection.queries)


def test_findings_exit_1_and_warn_exits_0(database, capsys):
    database["connection"] = FakeConnection({"foreign_key_without_index": [FK_ROW]})
    assert cli.main(["postgresql://x/y"]) == 1
    assert "foreign_key_without_index: public.orders.orders_customer_id_fkey" in (
        capsys.readouterr().out
    )
    assert cli.main(["postgresql://x/y", "--warn"]) == 0


def test_a_failed_check_exits_1_even_with_warn(database):
    database["connection"] = FakeConnection(failing=("invalid_index",))
    assert cli.main(["postgresql://x/y", "--warn"]) == 1


def test_json_output_and_file(database, capsys, tmp_path):
    database["connection"] = FakeConnection({"invalid_index": [INVALID_ROW]})
    assert (
        cli.main(["postgresql://x/y", "--format", "json", "--json", "out/r.json"]) == 1
    )
    data = json.loads(capsys.readouterr().out)
    assert data["findings"][0]["check"] == "invalid_index"
    assert json.loads((tmp_path / "out" / "r.json").read_text()) == data


def test_github_annotations(database, capsys):
    database["connection"] = FakeConnection(
        {"foreign_key_without_index": [FK_ROW]}, failing=("invalid_index",)
    )
    assert cli.main(["postgresql://x/y", "--format", "github"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(
        "::error title=pghealth foreign_key_without_index%3A "
        "public.orders.orders_customer_id_fkey::Foreign key"
    )
    assert (
        "%0Afix: CREATE INDEX CONCURRENTLY ON public.orders (customer_id);" in lines[0]
    )
    assert lines[1].startswith(
        "::error title=pghealth invalid_index%3A the check failed::"
    )
    assert lines[2].startswith("pghealth: ")
    cli.main(["postgresql://x/y", "--format", "github", "--warn"])
    assert capsys.readouterr().out.startswith("::warning ")


def test_annotation_escaping():
    assert cli._escape_data("50%\nb\r") == "50%25%0Ab%0D"
    assert cli._escape_property("a:b,c") == "a%3Ab%2Cc"


def test_dsn_from_the_environment(database, monkeypatch):
    monkeypatch.setenv("PGHEALTH_URL", "postgresql://env/db")
    assert cli.main([]) == 0
    assert database["dsn"] == "postgresql://env/db"


def test_no_dsn_is_an_error(database, capsys):
    assert cli.main([]) == 2
    assert "no database" in capsys.readouterr().err


def test_connection_failure_is_exit_2(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)

    @contextmanager
    def refuse(dsn):
        raise OSError("connection refused")
        yield

    monkeypatch.setattr(cli, "connect", refuse)
    assert cli.main(["postgresql://nowhere/db"]) == 2
    assert "OSError: connection refused" in capsys.readouterr().err


def test_pyproject_ignores_and_options(database, capsys, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pghealth]\nsession = true\n"
        "[[tool.pghealth.ignore]]\n"
        'check = "foreign_key_without_index"\nobject = "public.orders"\nreason = "small"\n'
    )
    database["connection"] = connection = FakeConnection(
        {"foreign_key_without_index": [FK_ROW]}
    )
    assert (
        cli.main(
            ["postgresql://x/y", "--schema", "public", "--disable", "invalid_index"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "public: 12 checks, 0 problems, 1 ignored" in out
    assert not any("-- pghealth: invalid_index" in q for q in connection.queries)
    # --no-config: the ignore is gone
    assert cli.main(["postgresql://x/y", "--no-config"]) == 1


def test_bad_config_is_exit_2(database, capsys, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pghealth]\nnope = 1\n")
    assert cli.main(["postgresql://x/y"]) == 2
    assert "unknown key(s) nope" in capsys.readouterr().err
    assert cli.main(["--config", "missing.toml", "postgresql://x/y"]) == 2


def test_framework_tables(database, capsys):
    row = {
        **FK_ROW,
        "table_name": "django_admin_log",
        "table_ident": "public.django_admin_log",
    }
    database["connection"] = FakeConnection(
        {
            "foreign_key_without_index": [row],
            "framework_markers": [
                {"schema_name": "public", "table_name": "django_migrations"}
            ],
        }
    )
    assert cli.main(["postgresql://x/y"]) == 0
    assert cli.main(["postgresql://x/y", "--include-framework-tables"]) == 1


def test_list_checks(database, capsys):
    assert cli.main(["--list-checks"]) == 0
    out = capsys.readouterr().out
    assert "  on   json_column" in out
    assert "  off  timestamp_without_time_zone" in out
    assert "django.contrib.auth: auth_group" in out
    assert database["dsn"] is None
