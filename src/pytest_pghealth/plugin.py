"""The pytest plugin: options, the ``pghealth`` fixtures and the session check."""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pytest_pghealth.checks import CHECKS
from pytest_pghealth.config import ConfigError, Settings, load_pyproject, split_names
from pytest_pghealth.findings import Report
from pytest_pghealth.runner import run_checks

SETTINGS_KEY = pytest.StashKey[Settings]()
STATE_KEY = pytest.StashKey["_SessionState"]()

URL_ENVIRONMENT_VARIABLE = "PGHEALTH_URL"


@dataclass
class _SessionState:
    targets: list[Any] = field(default_factory=list)
    fixtures: dict[str, Any] = field(default_factory=dict)
    ran: bool = False
    report: Report | None = None
    source: str | None = None
    error: str | None = None
    # why nothing was checked, and whether that fails the run
    skipped: str | None = None
    skip_fails: bool = False
    # under pytest-xdist, the controller collects each worker's result here
    worker_results: list[dict[str, Any]] = field(default_factory=list)

    def to_worker_output(self) -> dict[str, Any]:
        return {
            "report": self.report.to_dict() if self.report is not None else None,
            "source": self.source,
            "error": self.error,
            "skipped": self.skipped,
            "skip_fails": self.skip_fails,
        }


class PgHealth:
    """What the ``pghealth`` fixture returns.

    Both methods take the target to check (a URL, a SQLAlchemy engine,
    connection or session, a Django connection, or a DB-API connection) and
    keyword overrides of the project settings: ``enable``, ``disable``,
    ``schemas``, ``ignore`` and ``sequence_remaining_percent``.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def check(self, target: Any, **overrides: Any) -> Report:
        """Run the checks and return the report without failing."""
        return run_checks(target, self.settings, **overrides)

    def assert_healthy(self, target: Any, **overrides: Any) -> Report:
        """Run the checks and fail the test if any of them finds a problem."""
        report = self.check(target, **overrides)
        if not report.ok:
            pytest.fail(report.format(), pytrace=False)
        return report


class PgHealthSession:
    """What the ``pghealth_session`` fixture returns."""

    def __init__(self, state: _SessionState) -> None:
        self._state = state

    def register(self, target: Any) -> None:
        """Check ``target`` at the end of the session, before fixtures tear down.

        The session check runs only when it is enabled (``--pghealth`` or
        ``session = true`` in ``[tool.pghealth]``).
        """
        if not any(known is target for known in self._state.targets):
            self._state.targets.append(target)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup(
        "pghealth", "PostgreSQL schema health checks (pytest-pghealth)"
    )
    group.addoption(
        "--pghealth",
        action="store_true",
        default=None,
        help="check the test database's schema at the end of the session",
    )
    group.addoption(
        "--no-pghealth",
        action="store_false",
        dest="pghealth",
        help="do not run the session check, even if pyproject.toml enables it",
    )
    group.addoption(
        "--pghealth-url",
        metavar="URL",
        help=f"database to check (default: url in [tool.pghealth], then ${URL_ENVIRONMENT_VARIABLE})",
    )
    group.addoption(
        "--pghealth-enable",
        metavar="CHECKS",
        help="comma-separated checks to run in addition to the defaults (or 'all')",
    )
    group.addoption(
        "--pghealth-disable",
        metavar="CHECKS",
        help="comma-separated checks not to run",
    )
    group.addoption(
        "--pghealth-json",
        metavar="PATH",
        help="write the session check's report as JSON to PATH",
    )
    group.addoption(
        "--pghealth-list-checks",
        action="store_true",
        default=False,
        help="list the checks, whether each runs with this configuration, and exit",
    )
    group.addoption(
        "--pghealth-warn",
        action="store_true",
        default=None,
        help="report the session check's findings without failing the run",
    )


def _pyproject_path(config: pytest.Config) -> Path:
    inipath = config.inipath
    if inipath is not None and inipath.name == "pyproject.toml":
        return inipath
    return config.rootpath / "pyproject.toml"


def _settings_from(config: pytest.Config) -> Settings:
    settings = load_pyproject(_pyproject_path(config))
    option = config.option
    enable = option.pghealth_enable
    disable = option.pghealth_disable
    return settings.with_overrides(
        session=option.pghealth,
        url=option.pghealth_url,
        enable=(*settings.enable, *split_names(enable)) if enable else None,
        disable=(*settings.disable, *split_names(disable)) if disable else None,
        json=option.pghealth_json,
        warn_only=option.pghealth_warn,
    )


def pytest_configure(config: pytest.Config) -> None:
    try:
        config.stash[SETTINGS_KEY] = _settings_from(config)
    except ConfigError as error:
        raise pytest.UsageError(f"pytest-pghealth: {error}") from error
    config.stash[STATE_KEY] = _SessionState()


@pytest.fixture
def pghealth(pytestconfig: pytest.Config) -> PgHealth:
    """Run the schema checks from a test: ``pghealth.assert_healthy(engine)``."""
    return PgHealth(pytestconfig.stash[SETTINGS_KEY])


@pytest.fixture(scope="session")
def pghealth_session(pytestconfig: pytest.Config) -> PgHealthSession:
    """Register a database for the end-of-session check."""
    return PgHealthSession(pytestconfig.stash[STATE_KEY])


def pytest_report_header(config: pytest.Config) -> str | None:
    settings = config.stash.get(SETTINGS_KEY, None)
    if settings is None or not settings.session:
        return None
    return f"pghealth: session check on, {len(settings.checks)} checks"


_DJANGO_FIXTURES = ("django_db_setup", "django_db_blocker")


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[Any], request: Any
) -> Generator[None, Any, Any]:
    # Remember the values the session check may need: the fixture named in
    # [tool.pghealth] and pytest-django's database fixtures.
    value = yield
    config = request.config
    settings = config.stash.get(SETTINGS_KEY, None)
    if settings is not None and settings.session:
        name = fixturedef.argname
        if name == settings.fixture or name in _DJANGO_FIXTURES:
            config.stash[STATE_KEY].fixtures[name] = value
    return value


def pytest_fixture_post_finalizer(
    fixturedef: pytest.FixtureDef[Any], request: Any
) -> None:
    state = request.config.stash.get(STATE_KEY, None)
    if state is not None:
        state.fixtures.pop(fixturedef.argname, None)


def _uses_xdist(config: pytest.Config) -> bool:
    return getattr(config.option, "dist", "no") != "no"


def _skip(state: _SessionState, reason: str, *, fails: bool) -> None:
    state.skipped = reason
    state.skip_fails = fails


def _run_session_check(session: pytest.Session) -> None:
    config = session.config
    settings = config.stash[SETTINGS_KEY]
    state = config.stash[STATE_KEY]
    url = settings.url or os.environ.get(URL_ENVIRONMENT_VARIABLE) or None
    # --pghealth on the command line asks for a check in this very run: a
    # run that checks nothing fails. Enabled only in pyproject.toml, a run
    # of a subset of tests that never touches the database just says so.
    explicit = config.option.pghealth is True

    if url:
        state.source = "url"
        state.report = run_checks(url, settings)
        return
    if _uses_xdist(config):
        _merge_worker_results(state, explicit)
        return
    if state.targets:
        state.source = "registered target"
        report = Report()
        for target in state.targets:
            part = run_checks(target, settings)
            report.server_version = part.server_version
            report.schemas = part.schemas
            report.checks = part.checks
            report.findings.extend(part.findings)
            report.ignored.extend(part.ignored)
            report.errors.extend(part.errors)
        state.report = report
        return
    if settings.fixture:
        if settings.fixture not in state.fixtures:
            _skip(
                state,
                f"fixture {settings.fixture!r} was not alive at the end of the session "
                "(no test used it, or it is not session-scoped), so the schema was not checked",
                fails=explicit,
            )
            return
        state.source = f"fixture {settings.fixture}"
        state.report = run_checks(state.fixtures[settings.fixture], settings)
        return
    if config.pluginmanager.hasplugin("django"):
        blocker = state.fixtures.get("django_db_blocker")
        if "django_db_setup" not in state.fixtures or blocker is None:
            _skip(
                state,
                "no test used the Django test database in this run, "
                "so the schema was not checked",
                fails=explicit,
            )
            return
        from django.db import connections

        state.source = f"Django database {settings.django_alias!r}"
        with blocker.unblock():
            state.report = run_checks(connections[settings.django_alias], settings)
        return
    _skip(
        state,
        "nothing to check: set url or fixture in [tool.pghealth], pass --pghealth-url, "
        "or register a target with the pghealth_session fixture",
        fails=True,
    )


def _merge_worker_results(state: _SessionState, explicit: bool) -> None:
    """Combine the pytest-xdist workers' results into the controller's state.

    Every worker checks its own database (with pytest-django, a copy per
    worker), so the same finding usually arrives once per worker: it is kept
    once.
    """
    reports = [r for r in state.worker_results if r["report"] is not None]
    errors = [r["error"] for r in state.worker_results if r["error"]]
    if errors:
        state.error = errors[0]
    if not reports:
        if state.error is None:
            skipped = [r for r in state.worker_results if r["skipped"]]
            if skipped:
                _skip(
                    state,
                    skipped[0]["skipped"],
                    fails=explicit or any(r["skip_fails"] for r in skipped),
                )
            else:
                _skip(state, "no pytest-xdist worker ran the check", fails=explicit)
        return
    merged = Report.from_dict(reports[0]["report"])
    seen = {(f.check, f.target) for f in merged.findings}
    seen_ignored = {(i.finding.check, i.finding.target) for i in merged.ignored}
    seen_errors = {(e.check, e.error) for e in merged.errors}
    for result in reports[1:]:
        part = Report.from_dict(result["report"])
        for finding in part.findings:
            if (finding.check, finding.target) not in seen:
                seen.add((finding.check, finding.target))
                merged.findings.append(finding)
        for ignored in part.ignored:
            key = (ignored.finding.check, ignored.finding.target)
            if key not in seen_ignored:
                seen_ignored.add(key)
                merged.ignored.append(ignored)
        for error in part.errors:
            if (error.check, error.error) not in seen_errors:
                seen_errors.add((error.check, error.error))
                merged.errors.append(error)
    state.report = merged
    sources = sorted({r["source"] for r in reports if r["source"]})
    state.source = f"{', '.join(sources)}, {len(reports)} xdist workers"


def _session_check_once(session: pytest.Session) -> None:
    config = session.config
    settings = config.stash.get(SETTINGS_KEY, None)
    if settings is None or not settings.session or config.option.collectonly:
        return
    worker = hasattr(config, "workerinput")
    url = settings.url or os.environ.get(URL_ENVIRONMENT_VARIABLE)
    if worker and url:
        return  # the controller checks the URL once
    state = config.stash[STATE_KEY]
    if state.ran:
        return
    state.ran = True
    try:
        _run_session_check(session)
    except Exception as error:  # noqa: BLE001 - reported in the summary, the run fails
        state.error = f"{type(error).__name__}: {error}"
    if worker:
        # the controller reports, writes the JSON and sets the exit code
        config.workeroutput["pghealth"] = state.to_worker_output()  # type: ignore[attr-defined]
        return
    if state.report is not None and settings.json:
        # relative to the directory pytest was started in, as --junitxml is
        path = config.invocation_params.dir / settings.json
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(state.report.to_json(), encoding="utf-8")
        except OSError as error:
            state.error = f"cannot write the JSON report: {error}"


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: Any) -> None:
    """pytest-xdist: a worker finished; keep its session check result."""
    output = getattr(node, "workeroutput", None) or {}
    result = output.get("pghealth")
    state = node.config.stash.get(STATE_KEY, None)
    if result is not None and state is not None:
        state.worker_results.append(result)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None]:
    # The last test's teardown also tears down the session-scoped fixtures,
    # so the check runs before it, while the database fixtures are alive.
    if nextitem is None:
        _session_check_once(item.session)
    return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> Generator[None]:
    # A run stopped early (-x, --maxfail) never reaches the last test's
    # teardown: its session fixtures are torn down by pytest's own
    # pytest_sessionfinish, inside this wrapper, so they are still alive here.
    if exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        _session_check_once(session)
    config = session.config
    settings = config.stash.get(SETTINGS_KEY, None)
    state = config.stash.get(STATE_KEY, None)
    if (
        settings is not None
        and state is not None
        and state.ran
        and not hasattr(config, "workerinput")
    ):
        failed = (
            state.error is not None
            or (state.skipped is not None and state.skip_fails)
            or (
                state.report is not None
                and not state.report.ok
                and not settings.warn_only
            )
        )
        if failed and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    return (yield)


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> int | None:
    if not config.option.pghealth_list_checks:
        return None
    try:
        settings = _settings_from(config)
    except ConfigError as error:
        raise pytest.UsageError(f"pytest-pghealth: {error}") from error
    enabled = set(settings.checks)
    width = max(len(name) for name in CHECKS)
    print("pghealth checks (on: runs with this configuration)")
    for name, check in CHECKS.items():
        state = "on " if name in enabled else "off"
        print(f"  {state}  {name:<{width}}  {check.summary}")
    return 0


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    state = config.stash.get(STATE_KEY, None)
    settings = config.stash.get(SETTINGS_KEY, None)
    if state is None or settings is None:
        return
    write = terminalreporter
    if state.error is not None:
        write.section("pghealth", sep="=", red=True, bold=True)
        write.line(f"the session check failed: {state.error}", red=True)
        return
    if state.skipped is not None:
        if state.skip_fails:
            write.section("pghealth", sep="=", red=True, bold=True)
            write.line(state.skipped, red=True)
        else:
            write.section("pghealth", sep="=", yellow=True)
            write.line(state.skipped, yellow=True)
        return
    report = state.report
    if report is None:
        return
    if report.ok:
        write.section("pghealth", sep="=", green=True)
        write.line(f"{report.summary()} ({state.source})", green=True)
        return
    colour = {"yellow": True} if settings.warn_only else {"red": True}
    write.section("pghealth", sep="=", bold=True, **colour)
    write.line(f"{report.summary()} ({state.source})", bold=True, **colour)
    for line in report.format().splitlines()[1:]:
        write.line(line)
    if settings.json:
        write.line(f"JSON report: {settings.json}")
