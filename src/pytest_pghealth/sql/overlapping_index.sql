-- pghealth: overlapping_index
-- Written for pytest-pghealth; the idea follows intersected_indexes.sql of
-- pg-index-health-sql (https://github.com/mfvanek/pg-index-health-sql).

-- B-tree indexes whose key columns are a leading prefix of another B-tree
-- index on the same table, with the same operator classes, collations, sort
-- options and predicate: an index on (a) next to one on (a, b). The longer
-- index serves the same lookups, so the shorter one only costs writes.
-- Unique indexes, constraint indexes, expression indexes and indexes with
-- INCLUDE columns are never reported as the redundant one.
with
    idx as (
        select
            pi.indexrelid,
            pi.indrelid,
            nsp.nspname::text as schema_name,
            tbl.relname::text as table_name,
            quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
            ic.relname::text as index_name,
            quote_ident(nsp.nspname) || '.' || quote_ident(ic.relname) as index_ident,
            ic.relkind::text as index_kind,
            pi.indisunique,
            pi.indnatts = pi.indnkeyatts as no_include,
            pi.indexprs is null as no_expressions,
            exists (
                select 1 from pg_catalog.pg_constraint con
                where con.conindid = pi.indexrelid and con.contype in ('p', 'u', 'x')
            ) as backs_constraint,
            coalesce(pg_get_expr(pi.indpred, pi.indrelid), '') as predicate,
            array(
                select
                    (string_to_array(pi.indkey::text, ' '))[i] || '/' ||
                    (string_to_array(pi.indclass::text, ' '))[i] || '/' ||
                    (string_to_array(pi.indcollation::text, ' '))[i] || '/' ||
                    (string_to_array(pi.indoption::text, ' '))[i]
                from generate_series(1, pi.indnkeyatts) as i
                order by i
            ) as key_parts
        from
            pg_catalog.pg_index pi
            inner join pg_catalog.pg_class ic on ic.oid = pi.indexrelid
            inner join pg_catalog.pg_am am on am.oid = ic.relam and am.amname = 'btree'
            inner join pg_catalog.pg_class tbl on tbl.oid = pi.indrelid
            inner join pg_catalog.pg_namespace nsp on nsp.oid = ic.relnamespace
        where
            {schema_filter} and
            pi.indisvalid and
            not ic.relispartition and
            not exists (
                select 1 from pg_catalog.pg_depend d
                where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = tbl.oid and d.deptype = 'e'
            )
    )

select distinct on (a.indexrelid)
    a.schema_name,
    a.table_name,
    a.table_ident,
    a.index_name,
    a.index_ident,
    a.index_kind,
    b.index_name as covering_index_name
from
    idx a
    inner join idx b
        on b.indrelid = a.indrelid and
        b.indexrelid <> a.indexrelid and
        b.no_expressions and
        b.predicate = a.predicate and
        cardinality(b.key_parts) > cardinality(a.key_parts) and
        b.key_parts[1:cardinality(a.key_parts)] = a.key_parts
where
    not a.indisunique and
    not a.backs_constraint and
    a.no_include and
    a.no_expressions
order by a.indexrelid, cardinality(b.key_parts), b.index_name;
