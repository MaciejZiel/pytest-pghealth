-- pghealth: duplicate_foreign_key
-- Ported from pg-index-health-sql, sql/duplicated_foreign_keys.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted
-- identifiers, and the referenced columns, actions, match type and
-- deferrability compared as well.

-- Foreign keys identical to another one on the same table: same columns,
-- same referenced table and columns, same ON DELETE / ON UPDATE actions,
-- match type and deferrability. Each one is checked on every write.
with
    fk as (
        select
            nsp.nspname::text as schema_name,
            tbl.relname::text as table_name,
            quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
            c.conname::text as constraint_name,
            c.conrelid,
            c.confrelid,
            c.conkey::text || ' ' || c.confkey::text || ' ' || c.confupdtype::text || ' ' ||
            c.confdeltype::text || ' ' || c.confmatchtype::text || ' ' ||
            c.condeferrable::text || ' ' || c.condeferred::text as key_columns
        from
            pg_catalog.pg_constraint c
            inner join pg_catalog.pg_class tbl on tbl.oid = c.conrelid
            inner join pg_catalog.pg_namespace nsp on nsp.oid = tbl.relnamespace
        where
            c.contype = 'f' and
            c.conparentid = 0 and c.coninhcount = 0 and /* not a constraint inherited by a partition */
            {schema_filter}
    )

select
    b.schema_name,
    b.table_name,
    b.table_ident,
    b.constraint_name,
    quote_ident(b.constraint_name) as constraint_ident,
    a.constraint_name as kept_constraint_name
from
    fk a
    inner join fk b
        on b.conrelid = a.conrelid and
        b.confrelid = a.confrelid and
        b.key_columns = a.key_columns and
        b.constraint_name > a.constraint_name
where not exists (
    /* report each redundant constraint once, against the first of its group */
    select 1 from fk z
    where
        z.conrelid = a.conrelid and z.confrelid = a.confrelid and
        z.key_columns = a.key_columns and z.constraint_name < a.constraint_name
)
order by b.schema_name, b.table_name, b.constraint_name;
