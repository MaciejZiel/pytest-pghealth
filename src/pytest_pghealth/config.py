"""Settings: the ``[tool.pghealth]`` table of pyproject.toml and the overrides."""

from __future__ import annotations

import fnmatch
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pytest_pghealth.checks import CHECKS
from pytest_pghealth.findings import Finding

DEFAULT_REMAINING_PERCENT = 20.0


class ConfigError(ValueError):
    """The configuration is invalid; the message says where and why."""


@dataclass(frozen=True)
class Ignore:
    """Accept the findings of ``check`` whose target matches ``object``.

    ``check`` and ``object`` are shell-style patterns (``*``, ``?``, ``[ab]``).
    ``object`` matches a finding's target (``schema.table.object``) or its
    table (``schema.table``), so ``public.audit_log`` accepts every finding
    of that table.
    """

    check: str
    object: str
    reason: str

    def matches(self, finding: Finding) -> bool:
        if not fnmatch.fnmatchcase(finding.check, self.check):
            return False
        table = f"{finding.schema}.{finding.table}"
        return fnmatch.fnmatchcase(finding.target, self.object) or (
            fnmatch.fnmatchcase(table, self.object)
        )


@dataclass(frozen=True)
class Settings:
    """What to check and how to report it.

    ``schemas`` of ``None`` means every schema except PostgreSQL's own
    (``pg_*`` and ``information_schema``).
    """

    enable: tuple[str, ...] = ()
    disable: tuple[str, ...] = ()
    schemas: tuple[str, ...] | None = None
    ignore: tuple[Ignore, ...] = ()
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
        return replace(self, **given)


_KEYS = {
    "enable",
    "disable",
    "schemas",
    "ignore",
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
        return parse_table(table)
    except ConfigError as error:
        raise ConfigError(f"{path}: [tool.pghealth]: {error}") from error


def parse_table(table: Mapping[str, Any]) -> Settings:
    """Settings from a ``[tool.pghealth]``-shaped mapping."""
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
        values["ignore"] = tuple(_ignores(table["ignore"]))
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


def _ignores(value: Any) -> list[Ignore]:
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
        ignores.append(Ignore(check=check, object=target, reason=reason.strip()))
    return ignores


__all__ = [
    "DEFAULT_REMAINING_PERCENT",
    "ConfigError",
    "Ignore",
    "Settings",
    "load_pyproject",
    "parse_table",
    "split_names",
]
