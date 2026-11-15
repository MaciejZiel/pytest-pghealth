-- pghealth: nullable_unique_column
-- Written for pytest-pghealth; the partial-index test follows
-- indexes_with_null_values.sql of pg-index-health-sql
-- (https://github.com/mfvanek/pg-index-health-sql).

-- Multi-column unique indexes and constraints with a nullable key column.
-- NULL never equals NULL, so (tenant_id, email) with a NULL tenant_id accepts
-- any number of identical rows. Single-column unique indexes are skipped: an
-- optional unique value (many NULLs, no repeated non-NULL values) is usually
-- intended. Indexes created with NULLS NOT DISTINCT (PostgreSQL 15+) and
-- partial indexes whose predicate names the column are skipped as well.
select
    nsp.nspname::text as schema_name,
    tbl.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
    ic.relname::text as index_name,
    con.conname::text as constraint_name,
    array_agg(a.attname::text order by a.attnum) as nullable_columns,
    array_agg(quote_ident(a.attname) order by a.attnum) as nullable_column_idents
from
    pg_catalog.pg_index pi
    inner join pg_catalog.pg_class ic on ic.oid = pi.indexrelid
    inner join pg_catalog.pg_class tbl on tbl.oid = pi.indrelid
    inner join pg_catalog.pg_namespace nsp on nsp.oid = ic.relnamespace
    inner join pg_catalog.pg_attribute a
        on a.attrelid = pi.indrelid and
        a.attnum = any((string_to_array(pi.indkey::text, ' ')::int2[])[1:pi.indnkeyatts])
    left join pg_catalog.pg_constraint con on con.conindid = pi.indexrelid and con.contype = 'u'
where
    pi.indisunique and
    not pi.indisprimary and
    pi.indisvalid and
    pi.indnkeyatts > 1 and
    not {nulls_not_distinct} and
    not ic.relispartition and
    not a.attnotnull and
    (
        pi.indpred is null or
        position(lower(a.attname) in lower(pg_get_expr(pi.indpred, pi.indrelid))) = 0
    ) and
    {schema_filter} and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = tbl.oid and d.deptype = 'e'
    )
group by nsp.nspname, tbl.relname, ic.relname, con.conname
order by schema_name, table_name, index_name;
