"""The session check under pytest-xdist: each worker checks, the controller reports."""

import json
from pathlib import Path

import pytest

pytest.importorskip("xdist")

FAKEDB = (Path(__file__).parent / "fakedb.py").read_text()

CONFTEST = """
import pytest
from fakedb import FakeConnection, FK_ROW

@pytest.fixture(scope="session")
def database(pghealth_session):
    connection = FakeConnection({"foreign_key_without_index": [FK_ROW]})
    pghealth_session.register(connection)
    yield connection
"""


@pytest.fixture
def project(pytester):
    pytester.makepyfile(fakedb=FAKEDB)
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile("\n".join(f"def test_{i}(database): pass" for i in range(8)))
    return pytester


def test_workers_check_and_the_controller_reports_once(project):
    result = project.runpytest_subprocess(
        "-n", "2", "--pghealth", "--pghealth-json", "out.json", "-p", "no:randomly"
    )
    assert result.ret == 1
    result.assert_outcomes(passed=8)
    result.stdout.fnmatch_lines(
        ["*: 11 checks, 1 problem (registered target, from 2 xdist workers)"]
    )
    # the same finding from both workers is reported once
    assert result.stdout.str().count("foreign_key_without_index: public.orders") == 1
    data = json.loads((project.path / "out.json").read_text())
    assert len(data["findings"]) == 1


def test_warn_only_under_xdist(project):
    result = project.runpytest_subprocess("-n", "2", "--pghealth", "--pghealth-warn")
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        ["*1 problem (registered target, from 2 xdist workers)"]
    )


def test_nothing_checked_under_xdist_fails_with_pghealth(pytester):
    pytester.makepyfile("def test_a(): pass\ndef test_b(): pass")
    result = pytester.runpytest_subprocess("-n", "2", "--pghealth")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*nothing to check*"])


def test_workers_results_survive_maxfail(project):
    project.makepyfile(test_fail="def test_fail(database): assert 0")
    result = project.runpytest_subprocess("-n", "2", "-x", "--pghealth")
    assert result.ret != 0
    result.stdout.fnmatch_lines(
        ["*1 problem (registered target, from * xdist worker*)"]
    )
