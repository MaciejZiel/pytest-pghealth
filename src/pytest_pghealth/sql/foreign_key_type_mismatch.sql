-- pghealth: foreign_key_type_mismatch
-- Ported from pg-index-health-sql, sql/foreign_keys_with_unmatched_column_type.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas,
-- quoted identifiers, extension tables skipped, and one row per mismatched
-- column with both types.

-- Foreign key columns whose type differs from the referenced column: an
-- integer column referencing a bigint key, varchar(50) referencing
-- varchar(100). Every check and join then converts types, and the narrower
-- column overflows first.
select
    nsp.nspname::text as schema_name,
    tbl.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
    c.conname::text as constraint_name,
    col.attname::text as column_name,
    quote_ident(col.attname) as column_ident,
    format_type(col.atttypid, col.atttypmod) as column_type,
    c.confrelid::regclass::text as referenced_table,
    foreign_col.attname::text as referenced_column,
    format_type(foreign_col.atttypid, foreign_col.atttypmod) as referenced_type
from
    pg_catalog.pg_constraint c
    inner join pg_catalog.pg_class tbl on tbl.oid = c.conrelid
    inner join pg_catalog.pg_namespace nsp on nsp.oid = tbl.relnamespace
    inner join lateral unnest(c.conkey) with ordinality u(attnum, attposition) on true
    inner join lateral unnest(c.confkey) with ordinality foreign_u(attnum, attposition) on foreign_u.attposition = u.attposition
    inner join pg_catalog.pg_attribute col on col.attrelid = c.conrelid and col.attnum = u.attnum
    inner join pg_catalog.pg_attribute foreign_col on foreign_col.attrelid = c.confrelid and foreign_col.attnum = foreign_u.attnum
where
    c.contype = 'f' and
    c.conparentid = 0 and c.coninhcount = 0 and /* not a constraint inherited by a partition */
    (col.atttypid <> foreign_col.atttypid or col.atttypmod <> foreign_col.atttypmod) and
    {schema_filter} and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = tbl.oid and d.deptype = 'e'
    )
order by schema_name, table_name, constraint_name, u.attposition;
