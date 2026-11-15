# Real projects

Before 0.2.0, every check ran against the migrated schemas of four
open-source projects: two built on SQLAlchemy and Alembic, two on Django.
Each project's own migrations built its schema in PostgreSQL 18, the same
schema its PostgreSQL test suite runs against. Then `run_checks` ran with
every check turned on (`enable = ["all"]`). The
[Real projects workflow](../.github/workflows/realworld.yml) does this in
GitHub Actions with each project pinned to the version below, and
`scripts/realworld.py` writes each report as text and JSON.

Every finding was then read and put into one of three classes:

- **true**: the schema has the problem the check describes, and the fix
  makes sense for the project;
- **debatable**: the catalog fact is right, but the project may well want it
  that way (a NULL that means "not applicable", a lookup table that will
  never reach two billion rows);
- **false positive**: the check misread the schema.

## Results

| Project | Version | Stack | Findings | True | Debatable | False positive |
|---|---|---|---|---|---|---|
| [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | `aabb901` | SQLModel, Alembic | 1 | 1 | 0 | 0 |
| [Prefect](https://github.com/PrefectHQ/prefect) | 3.9.3 | SQLAlchemy, Alembic | 26 | 22 | 4 | 0 |
| [NetBox](https://github.com/netbox-community/netbox) | `2ac39b5` | Django | 217 | 168 | 49 | 0 |
| [Wagtail](https://github.com/wagtail/wagtail) (a new site from `wagtail start`) | 8.1 | Django | 69 | 27 | 42 | 0 |

No finding was a false positive. Nothing reported by `invalid_index`,
`table_without_primary_key`, `sequence_near_overflow`,
`foreign_key_type_mismatch`, `not_valid_constraint`,
`duplicate_foreign_key` or `btree_index_on_array`: these schemas are built
by migration tools from scratch, which is where those problems are rare.
They show up in long-lived databases, after a failed `CREATE INDEX
CONCURRENTLY` or a hand-written migration.

## By project

### full-stack-fastapi-template

One finding, true: `item.owner_id` references `user` with
`ON DELETE CASCADE` and has no index, so deleting a user scans the whole
`item` table. This is the template many FastAPI projects start from, so
the missing index is copied into each of them.

```text
foreign_key_without_index: public.item.item_owner_id_fkey
  fix: CREATE INDEX CONCURRENTLY ON public.item (owner_id);
```

### Prefect

- `foreign_key_without_index`, 9, true. Examples:
  `deployment.storage_document_id` and
  `deployment.infrastructure_document_id` (deleting a block document scans
  `deployment`), `block_document_reference.parent_block_document_id`, and
  `work_pool.default_queue_id`.
- `duplicate_index`, 1, true: `ix_configuration__key` is identical to
  the unique index `uq_configuration__key`.
- `overlapping_index`, 12, true. Examples: `ix_log__flow_run_id` next to
  `ix_log__flow_run_id_timestamp`; `ix_task_run__state_type` next to
  `ix_task_run__state_type_start_time`. Four of the longer indexes are
  unique indexes, which serve the same lookups.
- `nullable_unique_column`, 4, debatable. `flow_run(flow_id,
  idempotency_key)` with a nullable `idempotency_key` is an optional key by
  design. `task_run(flow_run_id, task_key, dynamic_key)` with a nullable
  `flow_run_id` means the uniqueness is not enforced for tasks that run
  outside a flow, which may or may not be intended.

### NetBox

- `overlapping_index`, 168, true. 161 of them are the index Django creates
  for each `ForeignKey`, next to a unique constraint or a composite index
  that starts with the same column. The longer index serves every lookup by that column, including
  the foreign key's checks. In Django the fix is `db_index=False` on the
  `ForeignKey`; the report now names the foreign key (see below).
- `nullable_unique_column`, 44, debatable. NetBox uses NULL as "not
  applicable": a component template belongs to a device type *or* a module
  type, with one unique constraint for each; a device may have no rack
  position. Those constraints do let duplicates in where the column is
  NULL, which NetBox handles in its own validation.
- `int4_primary_key`, 5, debatable: `auth_group`, `auth_permission` and
  `django_content_type` from `django.contrib`, and two `django-taggit`
  tables. True of the catalog, but these tables come from other packages and
  will not reach two billion rows.

### Wagtail

- `int4_primary_key`, 41, debatable. Wagtail's models use `AutoField`
  (`integer`), so every Wagtail table has an `integer` key. That matters
  for revision and log tables on a big site, much less for locales and
  sites.
- `overlapping_index`, 27, true; 23 are Django's per-`ForeignKey` indexes,
  as in NetBox, including those of `django.contrib.auth`.
- `nullable_unique_column`, 1, debatable: in
  `wagtailredirects_redirect(old_path, site_id)` a NULL `site_id` means "all
  sites".

## What changed in pytest-pghealth

- `overlapping_index` now says when the redundant index has exactly the
  columns of a foreign key, and that the longer index still covers that
  foreign key. On
  the Django projects this is most of the findings, and without it the
  natural worry is "if I drop this, is my foreign key unindexed?" (it is
  not: `foreign_key_without_index` accepts any index whose leading columns
  are the key's).
- [Django advice](configuration.md#ignores) for tables that belong to
  other packages: a narrow ignore per check and table pattern, for example
  `check = "int4_primary_key"`, `object = "public.auth_*"`.

## Reproduce

Push to a `realworld/*` branch or run the workflow by hand
(`gh workflow run "Real projects"`). Each job uploads `<project>.json` and
`<project>.txt`. It takes about four minutes per project, most of it
installing the project and running its migrations.
