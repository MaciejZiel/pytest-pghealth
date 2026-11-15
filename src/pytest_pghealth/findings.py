"""Findings and the report of one run of the checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Bumped when a key of the JSON report changes or disappears.
JSON_FORMAT_VERSION = 1


@dataclass(frozen=True)
class Finding:
    """One problem found by one check.

    ``schema`` and ``table`` are the raw (unquoted) names; ``object`` is the
    index, constraint, column or sequence the finding is about, or ``None``
    when the finding is about the whole table.
    """

    check: str
    schema: str
    table: str
    object: str | None
    message: str
    fix: str

    @property
    def target(self) -> str:
        """``schema.table`` or ``schema.table.object``: what ignores match."""
        base = f"{self.schema}.{self.table}"
        return f"{base}.{self.object}" if self.object else base

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "schema": self.schema,
            "table": self.table,
            "object": self.object,
            "target": self.target,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class IgnoredFinding:
    finding: Finding
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.finding.to_dict(), "reason": self.reason}


@dataclass(frozen=True)
class CheckError:
    """A check whose query failed (for example, missing privileges)."""

    check: str
    error: str


@dataclass
class Report:
    """The result of running the checks against one database."""

    server_version: str = ""
    schemas: tuple[str, ...] | None = None
    checks: tuple[str, ...] = ()
    findings: list[Finding] = field(default_factory=list)
    ignored: list[IgnoredFinding] = field(default_factory=list)
    errors: list[CheckError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no check found a problem and no check failed."""
        return not self.findings and not self.errors

    def to_dict(self) -> dict[str, Any]:
        from pytest_pghealth import __version__

        return {
            "format": JSON_FORMAT_VERSION,
            "tool": "pytest-pghealth",
            "tool_version": __version__,
            "server_version": self.server_version,
            "schemas": list(self.schemas) if self.schemas is not None else None,
            "checks": list(self.checks),
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
            "ignored": [i.to_dict() for i in self.ignored],
            "errors": [{"check": e.check, "error": e.error} for e in self.errors],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    def summary(self) -> str:
        """One line: what ran and what it found."""
        schemas = ", ".join(self.schemas) if self.schemas else "all user schemas"
        parts = [
            f"{len(self.checks)} checks",
            _plural(len(self.findings), "problem"),
        ]
        if self.ignored:
            parts.append(f"{len(self.ignored)} ignored")
        if self.errors:
            parts.append(_plural(len(self.errors), "failed check"))
        version = f"PostgreSQL {self.server_version}, " if self.server_version else ""
        return f"{version}{schemas}: {', '.join(parts)}"

    def format(self) -> str:
        """The findings as plain text, each with its fix."""
        lines = [f"pghealth: {self.summary()}"]
        for finding in self.findings:
            lines.append("")
            lines.append(f"{finding.check}: {finding.target}")
            lines.append(f"  {finding.message}")
            lines.extend(_indent_fix(finding.fix))
        for error in self.errors:
            lines.append("")
            lines.append(f"{error.check}: the check failed: {error.error}")
        if self.findings:
            first = self.findings[0]
            lines.append("")
            lines.append(
                "To accept a finding, add an ignore with a reason to pyproject.toml:"
            )
            lines.append("  [[tool.pghealth.ignore]]")
            lines.append(f'  check = "{first.check}"')
            lines.append(f'  object = "{first.target}"')
            lines.append('  reason = "why this is fine here"')
        return "\n".join(lines)


def _indent_fix(fix: str) -> list[str]:
    fix_lines = fix.splitlines() or [""]
    out = [f"  fix: {fix_lines[0]}"]
    out.extend(f"       {line}" for line in fix_lines[1:])
    return out


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
