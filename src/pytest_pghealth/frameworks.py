"""Tables that belong to a framework or a third-party package, not to the project.

A finding on ``auth_permission`` or ``alembic_version`` is right about the
catalog, but the project cannot change that table's migrations, so by
default those findings are ignored (``ignore_framework_tables``). They
still appear in the report as ignored, with the package that owns the
table as the reason.

Two checks are never skipped: ``invalid_index`` and
``sequence_near_overflow`` describe the state of this database (a failed
index build, a sequence running out), which the project fixes whoever wrote
the migration.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from pytest_pghealth.findings import Finding

#: Checks about this database's state rather than the schema's design.
STATE_CHECKS = frozenset({"invalid_index", "sequence_near_overflow"})


@dataclass(frozen=True)
class FrameworkTables:
    """The tables one package creates, as patterns over the bare table name."""

    package: str
    tables: tuple[str, ...]

    def owns(self, table: str) -> bool:
        return any(fnmatch.fnmatchcase(table, pattern) for pattern in self.tables)


FRAMEWORK_TABLES: tuple[FrameworkTables, ...] = (
    FrameworkTables("django", ("django_migrations",)),
    FrameworkTables("django.contrib.admin", ("django_admin_log",)),
    FrameworkTables(
        "django.contrib.auth",
        (
            "auth_group",
            "auth_group_permissions",
            "auth_permission",
            "auth_user",
            "auth_user_groups",
            "auth_user_user_permissions",
        ),
    ),
    FrameworkTables("django.contrib.contenttypes", ("django_content_type",)),
    FrameworkTables("django.contrib.sessions", ("django_session",)),
    FrameworkTables("django.contrib.sites", ("django_site",)),
    FrameworkTables(
        "django.contrib.flatpages", ("django_flatpage", "django_flatpage_sites")
    ),
    FrameworkTables("django.contrib.redirects", ("django_redirect",)),
    FrameworkTables("django-taggit", ("taggit_tag", "taggit_taggeditem")),
    FrameworkTables("django-celery-beat", ("django_celery_beat_*",)),
    FrameworkTables("django-celery-results", ("django_celery_results_*",)),
    FrameworkTables("djangorestframework (authtoken)", ("authtoken_token",)),
    FrameworkTables("wagtail", ("wagtail*",)),
    FrameworkTables("alembic", ("alembic_version",)),
    FrameworkTables(
        "celery (database result backend)", ("celery_taskmeta", "celery_tasksetmeta")
    ),
    FrameworkTables("flyway", ("flyway_schema_history",)),
    FrameworkTables("liquibase", ("databasechangelog", "databasechangeloglock")),
)

FRAMEWORK_PACKAGES: tuple[str, ...] = tuple(f.package for f in FRAMEWORK_TABLES)

SOURCE = "built-in (ignore_framework_tables)"


@dataclass(frozen=True)
class FrameworkIgnore:
    """Ignore the design findings on one package's tables, in any schema."""

    tables: FrameworkTables

    @property
    def reason(self) -> str:
        return (
            f"framework-owned table ({self.tables.package}); "
            "set ignore_framework_tables = false to report it"
        )

    @property
    def source(self) -> str:
        return SOURCE

    def matches(self, finding: Finding) -> bool:
        return finding.check not in STATE_CHECKS and self.tables.owns(finding.table)

    def describe(self) -> str:
        return f"{self.tables.package}: {', '.join(self.tables.tables)}"


def framework_ignores(packages: bool | tuple[str, ...]) -> tuple[FrameworkIgnore, ...]:
    """The built-in ignores for ``ignore_framework_tables``."""
    if packages is False:
        return ()
    return tuple(
        FrameworkIgnore(tables)
        for tables in FRAMEWORK_TABLES
        if packages is True or tables.package in packages
    )


__all__ = [
    "FRAMEWORK_PACKAGES",
    "FRAMEWORK_TABLES",
    "STATE_CHECKS",
    "FrameworkIgnore",
    "FrameworkTables",
    "framework_ignores",
]
