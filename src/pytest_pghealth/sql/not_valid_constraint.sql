-- pghealth: not_valid_constraint
-- Ported from pg-index-health-sql, sql/not_valid_constraints.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas and quoted
-- identifiers.

-- CHECK and FOREIGN KEY constraints added with NOT VALID and never
-- validated. New rows are checked, but existing rows may break the rule, and
-- the planner cannot rely on the constraint.
select
    nsp.nspname::text as schema_name,
    tbl.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
    c.conname::text as constraint_name,
    quote_ident(c.conname) as constraint_ident,
    c.contype::text as constraint_type
from
    pg_catalog.pg_constraint c
    inner join pg_catalog.pg_class tbl on tbl.oid = c.conrelid
    inner join pg_catalog.pg_namespace nsp on nsp.oid = tbl.relnamespace
where
    not c.convalidated and
    c.contype in ('c', 'f') and
    c.conparentid = 0 and c.coninhcount = 0 and /* not a constraint inherited by a partition */
    {schema_filter}
order by schema_name, table_name, constraint_name;
