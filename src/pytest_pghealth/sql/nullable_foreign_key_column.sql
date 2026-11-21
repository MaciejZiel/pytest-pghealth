-- pghealth: nullable_foreign_key_column
-- Ported from pg-index-health-sql, sql/foreign_keys_with_null_values.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted identifiers,
-- extension tables skipped, the schema and table as separate columns, and
-- the nullable columns listed apart from all the foreign key's columns.

-- Multi-column foreign keys with a nullable column and the default MATCH
-- SIMPLE. A row with NULL in any one of the columns is not checked at all,
-- so (tenant_id, customer_id) = (1, NULL) or (NULL, 7) passes whatever the
-- other column holds. MATCH FULL allows NULL only in all of them.
select
    nsp.nspname::text as schema_name,
    tbl.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
    c.conname::text as constraint_name,
    c.confrelid::regclass::text as referenced_table,
    array_agg(col.attname::text order by u.attposition) as columns,
    array_agg(col.attname::text order by u.attposition) filter (where not col.attnotnull) as nullable_columns,
    array_agg(quote_ident(col.attname) order by u.attposition) filter (where not col.attnotnull) as nullable_column_idents
from
    pg_catalog.pg_constraint c
    inner join pg_catalog.pg_class tbl on tbl.oid = c.conrelid
    inner join pg_catalog.pg_namespace nsp on nsp.oid = tbl.relnamespace
    inner join lateral unnest(c.conkey) with ordinality u(attnum, attposition) on true
    inner join pg_catalog.pg_attribute col on col.attrelid = c.conrelid and col.attnum = u.attnum
where
    c.contype = 'f' and
    cardinality(c.conkey) > 1 and
    c.confmatchtype <> 'f' and /* not MATCH FULL */
    c.conparentid = 0 and c.coninhcount = 0 and /* not a constraint inherited by a partition */
    {schema_filter} and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = tbl.oid and d.deptype = 'e'
    )
group by nsp.nspname, tbl.relname, c.conname, c.confrelid, c.oid
having bool_or(not col.attnotnull)
order by schema_name, table_name, constraint_name;
