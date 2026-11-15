-- pghealth: foreign_key_without_index
-- Ported from pg-index-health-sql, sql/foreign_keys_without_index.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted identifiers
-- in the output, extension objects skipped, and an index counts only when the
-- foreign key's columns are its leading key columns (in any order).

-- Foreign keys with no index on the referencing columns. Deleting or updating
-- a row of the referenced table then scans the whole referencing table.
-- Partial and invalid indexes do not count.
select
    nsp.nspname::text as schema_name,
    tbl.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
    tbl.relkind::text as table_kind,
    c.conname::text as constraint_name,
    c.confrelid::regclass::text as referenced_table,
    array_agg(col.attname::text order by u.attposition) as columns,
    array_agg(quote_ident(col.attname) order by u.attposition) as column_idents
from
    pg_catalog.pg_constraint c
    inner join pg_catalog.pg_class tbl on tbl.oid = c.conrelid
    inner join pg_catalog.pg_namespace nsp on nsp.oid = tbl.relnamespace
    inner join lateral unnest(c.conkey) with ordinality u(attnum, attposition) on true
    inner join pg_catalog.pg_attribute col on col.attrelid = c.conrelid and col.attnum = u.attnum
where
    c.contype = 'f' and
    c.conparentid = 0 and c.coninhcount = 0 and /* not a constraint inherited by a partition */
    {schema_filter} and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = tbl.oid and d.deptype = 'e'
    ) and
    not exists (
        select 1
        from pg_catalog.pg_index pi
        where
            pi.indrelid = c.conrelid and
            pi.indpred is null and /* a partial index cannot serve every lookup */
            pi.indisvalid and
            pi.indnkeyatts >= cardinality(c.conkey) and
            /* the foreign key's columns are the index's leading key columns */
            (string_to_array(pi.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)] @> c.conkey and
            (string_to_array(pi.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)] <@ c.conkey
    )
group by nsp.nspname, tbl.relname, tbl.relkind, c.conname, c.confrelid, c.oid
order by schema_name, table_name, constraint_name;
