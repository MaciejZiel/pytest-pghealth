-- pghealth: btree_index_on_array
-- Ported from pg-index-health-sql, sql/btree_indexes_on_array_columns.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas,
-- quoted identifiers, and one row per index with its array columns, its key
-- column count and whether a constraint needs it.

-- B-tree indexes on array columns. A B-tree only helps comparisons of the
-- whole array; containment and overlap queries (@>, <@, &&) need GIN.
select
    nsp.nspname::text as schema_name,
    tbl.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
    pc.relname::text as index_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(pc.relname) as index_ident,
    pc.relkind::text as index_kind,
    pi.indnkeyatts as key_column_count,
    exists (
        select 1 from pg_catalog.pg_constraint con where con.conindid = pi.indexrelid
    ) as backs_constraint,
    array_agg(col.attname::text order by col.attnum) as columns,
    array_agg(quote_ident(col.attname) order by col.attnum) as column_idents
from
    pg_catalog.pg_index pi
    inner join pg_catalog.pg_class pc on pc.oid = pi.indexrelid
    inner join pg_catalog.pg_class tbl on tbl.oid = pi.indrelid
    inner join pg_catalog.pg_namespace nsp on nsp.oid = pc.relnamespace
    inner join pg_catalog.pg_am am on am.oid = pc.relam and am.amname = 'btree'
    inner join pg_catalog.pg_attribute col
        on col.attrelid = pi.indrelid and
        col.attnum = any((string_to_array(pi.indkey::text, ' ')::int2[])[1:pi.indnkeyatts])
    inner join pg_catalog.pg_type typ on typ.oid = col.atttypid
where
    {schema_filter} and
    not pc.relispartition and
    typ.typcategory = 'A' /* array types */
group by nsp.nspname, tbl.relname, pc.relname, pc.relkind, pi.indnkeyatts, pi.indexrelid
order by schema_name, table_name, index_name;
