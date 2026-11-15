"""The kinds of targets run_checks accepts, and the plugin, on real PostgreSQL."""

import os

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from pytest_pghealth import run_checks

URL = os.environ.get("PGHEALTH_TEST_URL", "")

FK_SCHEMA = (
    "create table parent (id bigint primary key)",
    "create table child (id bigint primary key, parent_id bigint references parent)",
)


def targets(target, schema):
    report = run_checks(target, schemas=[schema.name], enable=["all"])
    assert not report.errors
    return [f.target for f in report.findings]


def expected(schema):
    return [f"{schema.name}.child.child_parent_id_fkey"]


def test_engine_connection_and_session(schema, engine):
    schema.sql(*FK_SCHEMA)
    assert targets(engine, schema) == expected(schema)
    with engine.connect() as connection:
        assert targets(connection, schema) == expected(schema)
    with Session(engine) as session:
        assert targets(session, schema) == expected(schema)


def test_urls(schema):
    schema.sql(*FK_SCHEMA)
    assert targets(URL, schema) == expected(schema)
    # no driver in the URL: psycopg if installed, else pg8000 through SQLAlchemy
    plain = (
        make_url(URL).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    assert targets(plain, schema) == expected(schema)
    assert targets(
        plain.replace("postgresql://", "postgres://", 1), schema
    ) == expected(schema)


def test_dbapi_connection(schema):
    import pg8000.dbapi

    schema.sql(*FK_SCHEMA)
    url = make_url(URL)
    connection = pg8000.dbapi.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port or 5432,
        database=url.database,
    )
    try:
        assert targets(connection, schema) == expected(schema)
    finally:
        connection.close()


def test_server_version(schema, engine):
    report = run_checks(engine, schemas=[schema.name])
    major = int(schema.sql("show server_version_num")[0][0]) // 10000
    assert report.server_version.split(".")[0] == str(major)


@pytest.fixture
def project(pytester, schema):
    schema.sql(*FK_SCHEMA)
    pytester.makepyprojecttoml(
        f"""
        [tool.pghealth]
        session = true
        schemas = ["{schema.name}"]
        """
    )
    return pytester


def test_plugin_session_check_with_url(project, schema):
    project.makepyfile("def test_ok(): pass")
    result = project.runpytest("--pghealth-url", URL, "--pghealth-json", "report.json")
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        [
            "*= pghealth =*",
            "PostgreSQL *: 11 checks, 1 problem (url)",
            f"foreign_key_without_index: {schema.name}.child.child_parent_id_fkey",
            (f"  fix: CREATE INDEX CONCURRENTLY ON {schema.name}.child (parent_id);"),
        ]
    )
    assert '"ok": false' in (project.path / "report.json").read_text()


def test_plugin_session_check_with_engine_fixture(project, schema):
    project.makeconftest(
        f"""
        import pytest
        from sqlalchemy import create_engine

        @pytest.fixture(scope="session")
        def engine():
            engine = create_engine({URL!r})
            yield engine
            engine.dispose()
        """
    )
    project.makepyfile("def test_uses(engine): pass")
    result = project.runpytest()
    assert result.ret == 0  # no fixture configured: nothing to check
    result.stdout.fnmatch_lines(["*nothing to check*"])
    project.makepyprojecttoml(
        f"""
        [tool.pghealth]
        session = true
        fixture = "engine"
        schemas = ["{schema.name}"]
        """
    )
    result = project.runpytest()
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*1 problem (fixture engine)"])


def test_plugin_fixture_in_a_test(project, schema):
    project.makepyfile(
        f"""
        from sqlalchemy import create_engine

        def test_schema(pghealth):
            engine = create_engine({URL!r})
            try:
                pghealth.assert_healthy(engine)
            finally:
                engine.dispose()
        """
    )
    result = project.runpytest("--no-pghealth")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [f"*foreign_key_without_index: {schema.name}.child.child_parent_id_fkey"]
    )
