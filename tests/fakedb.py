"""A DB-API connection that answers pghealth's queries with canned rows.

The unit tests use it so that they run without PostgreSQL. Each check's SQL
starts with ``-- pghealth: <check name>``; the fake answers by that name.
"""

from __future__ import annotations

import re
from typing import Any

_CHECK = re.compile(r"-- pghealth: (\w+)")


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str) -> None:
        self.connection.queries.append(sql)
        match = _CHECK.search(sql)
        if match is None:
            rows = [
                {
                    "version": self.connection.version,
                    "version_num": self.connection.version_num,
                }
            ]
        else:
            name = match.group(1)
            if name in self.connection.failing:
                raise RuntimeError(f"permission denied for {name}\nDETAIL: more")
            rows = self.connection.rows.get(name, [])
        keys = list(rows[0]) if rows else ["nothing"]
        self.description = [(key,) for key in keys]
        self._rows = [tuple(row[key] for key in keys) for row in rows]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        self.connection.closed_cursors += 1


class FakeConnection:
    def __init__(
        self,
        rows: dict[str, list[dict[str, Any]]] | None = None,
        *,
        version: str = "18.1 (Debian 18.1-1.pgdg13+2)",
        version_num: int = 180001,
        failing: tuple[str, ...] = (),
    ) -> None:
        self.rows = rows or {}
        self.version = version
        self.version_num = version_num
        self.failing = failing
        self.queries: list[str] = []
        self.closed_cursors = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


FK_ROW = {
    "schema_name": "public",
    "table_name": "orders",
    "table_ident": "public.orders",
    "table_kind": "r",
    "constraint_name": "orders_customer_id_fkey",
    "referenced_table": "customers",
    "columns": ["customer_id"],
    "column_idents": ["customer_id"],
}

NO_PK_ROW = {
    "schema_name": "public",
    "table_name": "events",
    "table_ident": "public.events",
    "table_kind": "r",
    "partition_key": None,
    "candidate_index": None,
}
