import json
from pathlib import Path

import pytest

FAKEDB = (Path(__file__).parent / "fakedb.py").read_text()

pytest_plugins = ["pytester"]


@pytest.fixture
def project(pytester):
    pytester.makepyfile(fakedb=FAKEDB)
    return pytester


CONFTEST_REGISTER = """
import pytest
from fakedb import FakeConnection, FK_ROW

@pytest.fixture(scope="session", autouse=True)
def database(pghealth_session):
    connection = FakeConnection({"foreign_key_without_index": [FK_ROW]})
    pghealth_session.register(connection)
    yield connection
"""


def test_fixture_assert_healthy_fails_with_the_report(project):
    project.makepyfile(
        """
        from fakedb import FakeConnection, FK_ROW

        def test_schema(pghealth):
            pghealth.assert_healthy(FakeConnection({"foreign_key_without_index": [FK_ROW]}))

        def test_clean(pghealth):
            report = pghealth.assert_healthy(FakeConnection())
            assert report.ok

        def test_check_does_not_fail(pghealth):
            report = pghealth.check(FakeConnection({"foreign_key_without_index": [FK_ROW]}))
            assert len(report.findings) == 1

        def test_overrides(pghealth):
            pghealth.assert_healthy(
                FakeConnection({"foreign_key_without_index": [FK_ROW]}),
                disable=["foreign_key_without_index"],
            )
        """
    )
    result = project.runpytest()
    result.assert_outcomes(passed=3, failed=1)
    result.stdout.fnmatch_lines(
        [
            "*pghealth: PostgreSQL 18.1, all user schemas: 13 checks, 1 problem",
            "*foreign_key_without_index: public.orders.orders_customer_id_fkey",
            "*fix: CREATE INDEX CONCURRENTLY ON public.orders (customer_id);",
        ]
    )
    # pytrace=False: the report, not a traceback into the plugin
    assert "plugin.py" not in result.stdout.str()


def test_fixture_uses_pyproject_settings(project):
    project.makepyprojecttoml(
        """
        [tool.pghealth]
        [[tool.pghealth.ignore]]
        check = "foreign_key_without_index"
        object = "public.orders"
        reason = "parents are never deleted"
        """
    )
    project.makepyfile(
        """
        from fakedb import FakeConnection, FK_ROW

        def test_schema(pghealth):
            report = pghealth.assert_healthy(
                FakeConnection({"foreign_key_without_index": [FK_ROW]})
            )
            assert report.ignored[0].reason == "parents are never deleted"
        """
    )
    project.runpytest().assert_outcomes(passed=1)


def test_session_check_off_by_default(project):
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest()
    assert result.ret == 0
    assert "= pghealth =" not in result.stdout.str()


def test_session_check_fails_the_run(project):
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth", "--pghealth-json", "out.json")
    assert result.ret == 1
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        [
            "pghealth: session check on, 13 checks",
            "*= pghealth =*",
            "PostgreSQL 18.1, all user schemas: 13 checks, 1 problem (registered target)",
            "foreign_key_without_index: public.orders.orders_customer_id_fkey",
            "*fix: CREATE INDEX CONCURRENTLY*",
            "JSON report: out.json",
        ]
    )
    data = json.loads((project.path / "out.json").read_text())
    assert data["findings"][0]["check"] == "foreign_key_without_index"


def test_session_check_warn_only(project):
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth", "--pghealth-warn")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*1 problem (registered target)"])


def test_session_check_from_pyproject_and_disable_option(project):
    project.makepyprojecttoml("[tool.pghealth]\nsession = true\n")
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("def test_ok(): pass")
    assert project.runpytest().ret == 1
    result = project.runpytest("--pghealth-disable", "foreign_key_without_index")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*12 checks, 0 problems (registered target)"])
    assert project.runpytest("--no-pghealth").ret == 0


def test_session_check_keeps_a_failing_exit_code(project):
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("def test_bad(): assert False")
    result = project.runpytest("--pghealth")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*1 problem (registered target)"])


def test_session_check_skipped_when_interrupted(project):
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("import nope")
    result = project.runpytest("--pghealth")
    assert result.ret == pytest.ExitCode.INTERRUPTED
    assert "= pghealth =" not in result.stdout.str()


def test_session_check_from_fixture_name(project):
    project.makepyprojecttoml('[tool.pghealth]\nsession = true\nfixture = "engine"\n')
    project.makeconftest(
        """
        import pytest
        from fakedb import FakeConnection, FK_ROW

        @pytest.fixture(scope="session")
        def engine():
            yield FakeConnection({"foreign_key_without_index": [FK_ROW]})
        """
    )
    project.makepyfile("def test_uses(engine): pass")
    result = project.runpytest()
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*1 problem (fixture engine)"])

    # enabled in pyproject.toml only: a run that never set the fixture up
    # (a subset of the tests) just says so ...
    project.makepyfile("def test_uses(): pass")
    result = project.runpytest()
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        ["*fixture 'engine' was not alive at the end of the session*"]
    )
    # ... while --pghealth asks for a check in this run, so it fails
    result = project.runpytest("--pghealth")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*fixture 'engine' was not alive*"])


def test_session_check_skipped_on_collect_only(project):
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth", "--collect-only")
    assert result.ret == 0
    assert "= pghealth =" not in result.stdout.str()


def test_json_report_in_a_new_directory_and_unwritable(project):
    project.makeconftest(CONFTEST_REGISTER)
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth", "--pghealth-json", "reports/pghealth.json")
    assert result.ret == 1
    assert (project.path / "reports" / "pghealth.json").is_file()
    (project.path / "blocker").write_text("")
    result = project.runpytest("--pghealth", "--pghealth-json", "blocker/pghealth.json")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        ["*the session check failed: cannot write the JSON report*"]
    )


def test_register_twice_checks_once(project):
    project.makeconftest(
        CONFTEST_REGISTER + "    pghealth_session.register(connection)\n"
    )
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth")
    result.stdout.fnmatch_lines(["*: 13 checks, 1 problem (registered target)"])


def test_session_check_with_nothing_to_check(project):
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*nothing to check*"])


def test_session_check_error_fails_the_run(project, monkeypatch):
    monkeypatch.delenv("PGHEALTH_URL", raising=False)
    project.makeconftest(
        """
        import pytest

        @pytest.fixture(scope="session", autouse=True)
        def database(pghealth_session):
            pghealth_session.register(object())
        """
    )
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth")
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        ["*the session check failed: TypeError: cannot run pghealth*"]
    )


def test_url_from_environment(project, monkeypatch):
    monkeypatch.setenv("PGHEALTH_URL", "postgresql+nodriver://localhost/x")
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*the session check failed:*"])


def test_bad_configuration_is_a_usage_error(project):
    project.makepyprojecttoml("[tool.pghealth]\ndisable = ['nope']\n")
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*pytest-pghealth:*unknown check(s) nope*"])


def test_bad_option_is_a_usage_error(project):
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth-enable", "nope")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_session_check_runs_before_session_fixtures_tear_down(project):
    project.makeconftest(
        """
        import pytest
        from fakedb import FakeConnection, FK_ROW

        class Closing(FakeConnection):
            closed = False
            def cursor(self):
                assert not self.closed, "checked after teardown"
                return super().cursor()

        @pytest.fixture(scope="session", autouse=True)
        def database(pghealth_session):
            connection = Closing({"foreign_key_without_index": [FK_ROW]})
            pghealth_session.register(connection)
            yield connection
            connection.closed = True
        """
    )
    project.makepyfile("def test_a(): pass\ndef test_b(): pass")
    result = project.runpytest("--pghealth")
    result.stdout.fnmatch_lines(["*1 problem (registered target)"])
    # stopped early: the check runs from pytest_sessionfinish instead
    project.makepyfile("def test_a(): assert 0\ndef test_b(): pass")
    result = project.runpytest("--pghealth", "-x")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*1 problem (registered target)"])


def test_list_checks(project):
    project.makepyprojecttoml(
        '[tool.pghealth]\nenable = ["btree_index_on_array"]\ndisable = ["int4_primary_key"]\n'
        "[[tool.pghealth.ignore]]\n"
        'check = "duplicate_index"\nobject = "public.legacy"\nreason = "frozen schema"\n'
    )
    result = project.runpytest("--pghealth-list-checks")
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        [
            "pghealth checks (on: runs with this configuration)",
            "  on   foreign_key_without_index  *foreign keys with no index*",
            "  off  int4_primary_key  *",
            "  on   btree_index_on_array  *",
            "ignores (1):",
            "  check duplicate_index, object public.legacy: frozen schema",
            "    from */pyproject.toml: ignore #1",
            "framework-owned tables, skipped by every check except *",
            "*",
            "  django.contrib.auth: auth_group, *",
        ]
    )
    assert "collected" not in result.stdout.str()


def test_list_checks_reports_bad_configuration(project):
    project.makepyprojecttoml("[tool.pghealth]\ndisable = ['nope']\n")
    result = project.runpytest("--pghealth-list-checks")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_nothing_to_check_is_a_warning_from_addopts(project):
    project.makepyprojecttoml('[tool.pytest.ini_options]\naddopts = "--pghealth"\n')
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest()
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*nothing to check*"])


def test_registered_targets_report_a_finding_once(project):
    project.makeconftest(
        CONFTEST_REGISTER
        + "    pghealth_session.register(FakeConnection({'foreign_key_without_index': [FK_ROW]}))\n"
    )
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth")
    result.stdout.fnmatch_lines(["*: 13 checks, 1 problem (registered target)"])
