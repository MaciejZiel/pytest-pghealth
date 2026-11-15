"""Settings: the ``[tool.pghealth]`` table of pyproject.toml and the overrides."""

from __future__ import annotations

import fnmatch
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pytest_pghealth.checks import CHECKS
from pytest_pghealth.findings import Finding
from pytest_pghealth.frameworks import (
    FRAMEWORK_PACKAGES,
    STATE_CHECKS,
    framework_ignores,
)

DEFAULT_REMAINING_PERCENT = 20.0


class ConfigError(ValueError):
    """The configuration is invalid; the message says where and why."""


@dataclass(frozen=True)
class Ignore:
    """Accept the findings of ``check`` whose target matches ``object``.

    ``check`` and ``object`` are shell-style patterns (``*``, ``?``, ``[ab]``).
    ``object`` matches a finding's target (``schema.table.object``) or its
    table (``schema.table``), so ``public.audit_log`` accepts every finding
    of that table. ``source`` says where the ignore was defined; it does not
    take part in matching or comparison.
    """

    check: str
    object: str
    reason: str
    source: str = field(default="", compare=False)

    def matches(self, finding: Finding) -> bool:
        if not fnmatch.fnmatchcase(finding.check, self.check):
            return False
        table = f"{finding.schema}.{finding.table}"
        return fnmatch.fnmatchcase(finding.target, self.object) or (
            fnmatch.fnmatchcase(table, self.object)
        )

    def describe(self) -> str:
        return f"check {self.check}, object {self.object}: {self.reason}"


@dataclass(frozen=True)
class Settings:
    """What to check and how to report it.

    ``schemas`` of ``None`` means every schema except PostgreSQL's own
    (``pg_*`` and ``information_schema``). ``ignore_framework_tables`` is
    ``True`` (every package in :data:`~pytest_pghealth.frameworks.FRAMEWORK_TABLES`),
    ``False``, or the names of the packages whose tables are skipped.
    """

    enable: tuple[str, ...] = ()
    disable: tuple[str, ...] = ()
    schemas: tuple[str, ...] | None = None
    ignore: tuple[Ignore, ...] = ()
    ignore_framework_tables: bool | tuple[str, ...] = True
    sequence_remaining_percent: float = DEFAULT_REMAINING_PERCENT
    # The session check (plugin only).
    session: bool = False
    url: str | None = None
    fixture: str | None = None
    django_alias: str = "default"
    warn_only: bool = False
    json: str | None = None

    @property
    def checks(self) -> tuple[str, ...]:
        """The names of the checks that run, in registry order."""
        enabled = {name for name, check in CHECKS.items() if check.default}
        if "all" in self.enable:
            enabled = set(CHECKS)
        if "all" in self.disable:
            enabled = set()
        # named checks after "all": disable = ["all"], enable = ["x"] runs x
        enabled |= set(self.enable) - {"all"}
        enabled -= set(self.disable) - {"all"}
        return tuple(name for name in CHECKS if name in enabled)

    def with_command_line(self, enable: str | None, disable: str | None) -> Settings:
        """Add comma-separated command-line check names to ``enable``/``disable``."""
        return self.with_overrides(
            enable=(*self.enable, *split_names(enable)) if enable else None,
            disable=(*self.disable, *split_names(disable)) if disable else None,
        )

    def with_overrides(self, **overrides: Any) -> Settings:
        """A copy with the given fields replaced; ``None`` values are skipped."""
        given = {key: value for key, value in overrides.items() if value is not None}
        for key in ("enable", "disable"):
            if key in given:
                given[key] = _check_names(given[key], key)
        if "schemas" in given:
            given["schemas"] = tuple(_strings(given["schemas"], "schemas"))
        if "sequence_remaining_percent" in given:
            given["sequence_remaining_percent"] = _percent(
                given["sequence_remaining_percent"]
            )
        if "ignore" in given:
            given["ignore"] = tuple(_ignores(given["ignore"]))
        if "ignore_framework_tables" in given:
            given["ignore_framework_tables"] = _framework_tables(
                given["ignore_framework_tables"]
            )
        return replace(self, **given)


_KEYS = {
    "enable",
    "disable",
    "schemas",
    "ignore",
    "ignore_framework_tables",
    "sequence_remaining_percent",
    "session",
    "url",
    "fixture",
    "django_alias",
    "warn_only",
    "json",
}


def load_pyproject(path: Path) -> Settings:
    """Settings from the ``[tool.pghealth]`` table of ``path``, if any."""
    if not path.is_file():
        return Settings()
    with path.open("rb") as stream:
        try:
            data = tomllib.load(stream)
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"{path}: {error}") from error
    table = data.get("tool", {}).get("pghealth")
    if table is None:
        return Settings()
    try:
        return parse_table(table, source=str(path))
    except ConfigError as error:
        raise ConfigError(f"{path}: [tool.pghealth]: {error}") from error


def parse_table(table: Mapping[str, Any], source: str = "") -> Settings:
    """Settings from a ``[tool.pghealth]``-shaped mapping.

    ``source`` (the file it came from) is recorded on each ignore.
    """
    if not isinstance(table, Mapping):
        raise ConfigError("must be a table")
    unknown = sorted(set(table) - _KEYS)
    if unknown:
        raise ConfigError(
            f"unknown key(s) {', '.join(unknown)}; known: {', '.join(sorted(_KEYS))}"
        )
    values: dict[str, Any] = {}
    for key in ("enable", "disable"):
        if key in table:
            values[key] = _check_names(table[key], key)
    if "schemas" in table:
        values["schemas"] = tuple(_strings(table["schemas"], "schemas"))
    if "ignore" in table:
        values["ignore"] = tuple(_ignores(table["ignore"], source))
    if "ignore_framework_tables" in table:
        values["ignore_framework_tables"] = _framework_tables(
            table["ignore_framework_tables"]
        )
    if "sequence_remaining_percent" in table:
        values["sequence_remaining_percent"] = _percent(
            table["sequence_remaining_percent"]
        )
    for key in ("session", "warn_only"):
        if key in table:
            if not isinstance(table[key], bool):
                raise ConfigError(f"{key} must be true or false")
            values[key] = table[key]
    for key in ("url", "fixture", "django_alias", "json"):
        if key in table:
            if not isinstance(table[key], str) or not table[key]:
                raise ConfigError(f"{key} must be a non-empty string")
            values[key] = table[key]
    return Settings(**values)


def describe(settings: Settings) -> str:
    """The checks (on or off) and the active ignores with where each comes from."""
    enabled = set(settings.checks)
    width = max(len(name) for name in CHECKS)
    lines = ["pghealth checks (on: runs with this configuration)"]
    for name, check in CHECKS.items():
        state = "on " if name in enabled else "off"
        lines.append(f"  {state}  {name:<{width}}  {check.summary}")
    lines.append("")
    if settings.ignore:
        lines.append(f"ignores ({len(settings.ignore)}):")
        for ignore in settings.ignore:
            lines.append(f"  {ignore.describe()}")
            lines.append(f"    from {ignore.source or 'code (an override)'}")
    else:
        lines.append("ignores: none")
    framework = framework_ignores(settings.ignore_framework_tables)
    lines.append("")
    if framework:
        lines.append(
            "framework-owned tables, skipped by every check except "
            f"{' and '.join(sorted(STATE_CHECKS))}"
        )
        lines.append(
            "  from the built-in list (ignore_framework_tables = false to report them):"
        )
        lines.extend(f"  {ignore.describe()}" for ignore in framework)
    else:
        lines.append(
            "framework-owned tables: reported (ignore_framework_tables = false)"
        )
    return "\n".join(lines)


def split_names(value: str) -> list[str]:
    """``"a, b,c"`` -> ``["a", "b", "c"]`` (for command-line options)."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _strings(value: Any, key: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable):
        raise ConfigError(f"{key} must be a list of non-empty strings")
    value = list(value)  # a generator is consumed once
    if not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{key} must be a list of non-empty strings")
    return list(value)


def _check_names(value: Any, key: str) -> tuple[str, ...]:
    names = _strings(value, key)
    unknown = [name for name in names if name != "all" and name not in CHECKS]
    if unknown:
        raise ConfigError(
            f"{key}: unknown check(s) {', '.join(unknown)}; "
            f"known: all, {', '.join(CHECKS)}"
        )
    return tuple(names)


def _percent(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("sequence_remaining_percent must be a number")
    if not 0 <= value <= 100:
        raise ConfigError("sequence_remaining_percent must be between 0 and 100")
    return float(value)


def _framework_tables(value: Any) -> bool | tuple[str, ...]:
    if isinstance(value, bool):
        return value
    names = _strings(value, "ignore_framework_tables")
    if not names:
        raise ConfigError(
            "ignore_framework_tables: an empty list skips nothing; write false"
        )
    unknown = [name for name in names if name not in FRAMEWORK_PACKAGES]
    if unknown:
        raise ConfigError(
            f"ignore_framework_tables: unknown package(s) {', '.join(unknown)}; "
            f"known: {', '.join(FRAMEWORK_PACKAGES)}"
        )
    return tuple(names)


def _ignores(value: Any, source: str = "") -> list[Ignore]:
    if isinstance(value, (Ignore, Mapping)):
        value = [value]
    if not isinstance(value, Iterable) or isinstance(value, str):
        raise ConfigError("ignore must be a list of tables (check, object, reason)")
    ignores = []
    for position, entry in enumerate(value, 1):
        if isinstance(entry, Ignore):
            ignores.append(entry)
            continue
        if not isinstance(entry, Mapping):
            raise ConfigError(f"ignore #{position} must be a table")
        unknown = sorted(set(entry) - {"check", "object", "reason"})
        if unknown:
            raise ConfigError(
                f"ignore #{position}: unknown key(s) {', '.join(unknown)}"
            )
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(
                f"ignore #{position}: a reason is required (why the finding is fine)"
            )
        check = entry.get("check", "*")
        target = entry.get("object", "*")
        if not isinstance(check, str) or not isinstance(target, str):
            raise ConfigError(f"ignore #{position}: check and object must be strings")
        if not any(ch in check for ch in "*?[") and check not in CHECKS:
            raise ConfigError(
                f"ignore #{position}: unknown check {check!r}; known: {', '.join(CHECKS)}"
            )
        # without a file, the ignore came from code (an override)
        where = f"{source}: ignore #{position}" if source else ""
        ignores.append(
            Ignore(check=check, object=target, reason=reason.strip(), source=where)
        )
    return ignores


__all__ = [
    "DEFAULT_REMAINING_PERCENT",
    "ConfigError",
    "Ignore",
    "Settings",
    "describe",
    "load_pyproject",
    "parse_table",
    "split_names",
]
