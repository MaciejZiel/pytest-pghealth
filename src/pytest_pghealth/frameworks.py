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
from dataclasses import dataclass, replace

from pytest_pghealth.findings import Finding

#: Checks about this database's state rather than the schema's design.
STATE_CHECKS = frozenset({"invalid_index", "sequence_near_overflow"})


@dataclass(frozen=True)
class FrameworkTables:
    """The tables one package creates, as patterns over the bare table name.

    ``requires`` names a table that must exist in the same schema for the
    patterns to apply: the Django packages' tables are skipped only next to
    ``django_migrations``, so a Flask application's own ``auth_user`` is
    still checked.
    """

    package: str
    tables: tuple[str, ...]
    requires: str | None = None

    def owns(self, table: str) -> bool:
        return any(fnmatch.fnmatchcase(table, pattern) for pattern in self.tables)


DJANGO = "django_migrations"

FRAMEWORK_TABLES: tuple[FrameworkTables, ...] = (
    FrameworkTables("django", ("django_migrations",)),
    FrameworkTables("django.contrib.admin", ("django_admin_log",), DJANGO),
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
        DJANGO,
    ),
    FrameworkTables("django.contrib.contenttypes", ("django_content_type",), DJANGO),
    FrameworkTables("django.contrib.sessions", ("django_session",), DJANGO),
    FrameworkTables("django.contrib.sites", ("django_site",), DJANGO),
    FrameworkTables(
        "django.contrib.flatpages", ("django_flatpage", "django_flatpage_sites"), DJANGO
    ),
    FrameworkTables("django.contrib.redirects", ("django_redirect",), DJANGO),
    FrameworkTables("django-taggit", ("taggit_tag", "taggit_taggeditem"), DJANGO),
    FrameworkTables("django-celery-beat", ("django_celery_beat_*",), DJANGO),
    FrameworkTables("django-celery-results", ("django_celery_results_*",), DJANGO),
    FrameworkTables("djangorestframework (authtoken)", ("authtoken_token",), DJANGO),
    FrameworkTables(
        "wagtail",
        tuple(
            f"wagtail{app}_*"
            for app in (
                "admin",
                "core",
                "docs",
                "embeds",
                "forms",
                "images",
                "redirects",
                "search",
                "searchpromotions",
                "users",
            )
        ),
        DJANGO,
    ),
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
    """Ignore the design findings on one package's tables.

    ``schemas`` are the schemas where the package's ``requires`` table
    exists (see :func:`bind`); until bound, a package with a ``requires``
    table matches nothing.
    """

    tables: FrameworkTables
    schemas: frozenset[str] = frozenset()

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
        if finding.check in STATE_CHECKS or not self.tables.owns(finding.table):
            return False
        return self.tables.requires is None or finding.schema in self.schemas

    def describe(self) -> str:
        where = (
            f" (where {self.tables.requires} exists)" if self.tables.requires else ""
        )
        return f"{self.tables.package}: {', '.join(self.tables.tables)}{where}"


def required_tables(ignores: tuple[FrameworkIgnore, ...]) -> list[str]:
    """The ``requires`` tables whose presence the ignores depend on."""
    return sorted({i.tables.requires for i in ignores if i.tables.requires})


def bind(
    ignores: tuple[FrameworkIgnore, ...], present: set[tuple[str, str]]
) -> tuple[FrameworkIgnore, ...]:
    """The ignores, each knowing the schemas where its ``requires`` table is.

    ``present`` holds the ``(schema, table)`` pairs found in the database.
    """
    return tuple(
        replace(
            ignore,
            schemas=frozenset(
                schema for schema, table in present if table == ignore.tables.requires
            ),
        )
        for ignore in ignores
    )


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
    "bind",
    "framework_ignores",
    "required_tables",
]
