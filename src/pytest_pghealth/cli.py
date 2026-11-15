"""``pghealth``: run the checks against any database, outside pytest.

For long-lived databases (staging, a restored production snapshot, a
replica), where invalid indexes and sequences near overflow actually occur.
The session is made read-only before the first check runs, and the run
stops if PostgreSQL does not confirm it.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pytest_pghealth.config import (
    ConfigError,
    Settings,
    describe,
    load_pyproject,
    split_names,
)
from pytest_pghealth.findings import Report
from pytest_pghealth.runner import Fetch, connect, run_with

URL_ENVIRONMENT_VARIABLE = "PGHEALTH_URL"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def _parser() -> argparse.ArgumentParser:
    from pytest_pghealth import __version__

    parser = argparse.ArgumentParser(
        prog="pghealth",
        description=(
            "Check a PostgreSQL database's schema with the pytest-pghealth checks. "
            "Read-only: the session is set to read-only before any check runs."
        ),
        epilog=(
            "Exit status: 0 healthy (or --warn), 1 findings or a failed check, "
            "2 a usage, configuration or connection error."
        ),
    )
    parser.add_argument(
        "dsn",
        nargs="?",
        help=f"database URL or libpq connection string (default: ${URL_ENVIRONMENT_VARIABLE})",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="pyproject.toml whose [tool.pghealth] to use (default: ./pyproject.toml if present)",
    )
    parser.add_argument(
        "--no-config", action="store_true", help="ignore any pyproject.toml"
    )
    parser.add_argument(
        "--enable", metavar="CHECKS", help="comma-separated checks to add (or 'all')"
    )
    parser.add_argument(
        "--disable", metavar="CHECKS", help="comma-separated checks not to run"
    )
    parser.add_argument(
        "--schema",
        action="append",
        metavar="NAME",
        help="check only this schema (repeatable; default: every non-system schema)",
    )
    parser.add_argument(
        "--sequence-remaining-percent",
        type=float,
        metavar="N",
        help="report sequences with N%% or less of their range left (default 20)",
    )
    parser.add_argument(
        "--include-framework-tables",
        action="store_true",
        help="report findings on tables owned by frameworks (django.contrib, alembic, ...)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "github"),
        default="text",
        help="output: text, the JSON report, or GitHub Actions annotations (default: text)",
    )
    parser.add_argument(
        "--json", metavar="PATH", help="also write the JSON report to PATH"
    )
    parser.add_argument(
        "--warn", action="store_true", help="exit 0 even when there are findings"
    )
    parser.add_argument(
        "--statement-timeout",
        default="60s",
        metavar="DURATION",
        help="statement_timeout for each check (default: 60s)",
    )
    parser.add_argument(
        "--list-checks",
        action="store_true",
        help="list the checks and the active ignores, and exit",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    if args.no_config:
        settings = Settings()
    else:
        path = Path(args.config) if args.config else Path("pyproject.toml")
        if args.config and not path.is_file():
            raise ConfigError(f"{path}: no such file")
        settings = load_pyproject(path)
    return settings.with_overrides(
        enable=(*settings.enable, *split_names(args.enable)) if args.enable else None,
        disable=(*settings.disable, *split_names(args.disable))
        if args.disable
        else None,
        schemas=args.schema,
        sequence_remaining_percent=args.sequence_remaining_percent,
        ignore_framework_tables=False if args.include_framework_tables else None,
    )


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def read_only_session(fetch: Fetch, statement_timeout: str) -> None:
    """Make the session read-only and bounded, and confirm it.

    Raises :class:`RuntimeError` if PostgreSQL does not report the current
    transaction as read-only.
    """
    fetch("set application_name = 'pghealth'")
    fetch(f"set statement_timeout = {_literal(statement_timeout)}")
    # catalog reads take only light locks; never wait behind a migration
    fetch("set lock_timeout = '5s'")
    fetch("set default_transaction_read_only = on")
    if _read_only(fetch):
        return
    # a driver that opened a transaction before the setting: make this one read-only too
    fetch("set transaction read only")
    if not _read_only(fetch):
        raise RuntimeError("the session could not be made read-only; nothing was run")


def _read_only(fetch: Fetch) -> bool:
    rows = fetch("select current_setting('transaction_read_only') as read_only")
    return bool(rows) and str(rows[0]["read_only"]) == "on"


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def github_annotations(report: Report, *, warn: bool = False) -> str:
    """The report as GitHub Actions workflow commands, one annotation per finding."""
    level = "warning" if warn else "error"
    lines = []
    for finding in report.findings:
        title = _escape_property(f"pghealth {finding.check}: {finding.target}")
        body = _escape_data(f"{finding.message}\nfix: {finding.fix}")
        lines.append(f"::{level} title={title}::{body}")
    for error in report.errors:
        title = _escape_property(f"pghealth {error.check}: the check failed")
        lines.append(f"::error title={title}::{_escape_data(error.error)}")
    lines.append(f"pghealth: {report.summary()}")
    return "\n".join(lines)


def _output(report: Report, args: argparse.Namespace) -> str:
    if args.format == "json":
        return report.to_json().rstrip("\n")
    if args.format == "github":
        return github_annotations(report, warn=args.warn)
    return report.format()


def _error(message: str) -> int:
    print(f"pghealth: error: {message}", file=sys.stderr)
    return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = _settings(args)
    except ConfigError as error:
        return _error(str(error))
    if args.list_checks:
        print(describe(settings))
        return EXIT_OK
    dsn: Any = args.dsn or os.environ.get(URL_ENVIRONMENT_VARIABLE)
    if not dsn:
        return _error(
            f"no database: pass a URL or set ${URL_ENVIRONMENT_VARIABLE} (see --help)"
        )
    try:
        with connect(dsn) as fetch:
            read_only_session(fetch, args.statement_timeout)
            report = run_with(fetch, settings)
    except Exception as error:  # noqa: BLE001 - a connection or setup failure is exit 2
        return _error(f"{type(error).__name__}: {error}")
    print(_output(report, args))
    if args.json:
        try:
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report.to_json(), encoding="utf-8")
        except OSError as error:
            return _error(f"cannot write the JSON report: {error}")
    if report.ok or args.warn:
        return EXIT_OK
    return EXIT_FINDINGS


def run() -> None:
    """The console script."""
    sys.exit(main())


__all__ = ["github_annotations", "main", "read_only_session", "run"]
