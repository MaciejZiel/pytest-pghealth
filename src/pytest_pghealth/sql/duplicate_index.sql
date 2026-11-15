-- pghealth: duplicate_index
-- Ported from pg-index-health-sql, sql/duplicated_indexes.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, invalid indexes
-- skipped, one row per redundant index naming the index to keep (a constraint's
-- index, then a unique index, then the oldest), and the constraint to drop when
-- the redundant index backs one; a primary key is always kept.

-- Indexes identical to another index on the same table: same columns,
-- operator classes, collations, sort options, expressions and predicate.
with
    idx as (
        select
            nsp.nspname::text as schema_name,
            tbl.relname::text as table_name,
            quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
            ic.relname::text as index_name,
            quote_ident(nsp.nspname) || '.' || quote_ident(ic.relname) as index_ident,
            con.conname::text as constraint_name,
            quote_ident(con.conname) as constraint_ident,
            ic.relkind::text as index_kind,
            pi.indexrelid,
            pi.indisprimary,
            pi.indisunique,
            (
                pi.indrelid::text || ' ' || ic.relam::text || ' ' || pi.indclass::text || ' ' ||
                pi.indkey::text || ' ' || pi.indnkeyatts::text || ' ' || pi.indcollation::text || ' ' ||
                pi.indoption::text || ' ' ||
                coalesce(pg_get_expr(pi.indexprs, pi.indrelid), '') || ' ' ||
                coalesce(pg_get_expr(pi.indpred, pi.indrelid), '')
            ) as grouping_key
        from
            pg_catalog.pg_index pi
            inner join pg_catalog.pg_class ic on ic.oid = pi.indexrelid
            inner join pg_catalog.pg_class tbl on tbl.oid = pi.indrelid
            inner join pg_catalog.pg_namespace nsp on nsp.oid = ic.relnamespace
            left join pg_catalog.pg_constraint con on con.conindid = pi.indexrelid and con.contype in ('p', 'u', 'x')
        where
            {schema_filter} and
            pi.indisvalid and
            not ic.relispartition and
            not exists (
                select 1 from pg_catalog.pg_depend d
                where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = tbl.oid and d.deptype = 'e'
            )
    ),

    ranked as (
        select
            idx.*,
            row_number() over w as position,
            first_value(index_name) over w as kept_index_name
        from idx
        window w as (
            partition by grouping_key
            order by indisprimary desc, (constraint_name is not null) desc, indisunique desc, indexrelid
        )
    )

select
    schema_name,
    table_name,
    table_ident,
    index_name,
    index_ident,
    constraint_name,
    constraint_ident,
    index_kind,
    kept_index_name
from ranked
where position > 1
order by schema_name, table_name, index_name;
