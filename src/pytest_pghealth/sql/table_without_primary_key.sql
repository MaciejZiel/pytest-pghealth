-- pghealth: table_without_primary_key
-- Ported from pg-index-health-sql, sql/tables_without_primary_key.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted
-- identifiers, partitions and extension tables skipped, and a unique index on
-- NOT NULL columns that could become the primary key.

-- Tables without a primary key. Logical replication cannot replicate their
-- updates and deletes, and many tools (ORMs, CDC, pg_repack) need one.
select
    nsp.nspname::text as schema_name,
    pc.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(pc.relname) as table_ident,
    pc.relkind::text as table_kind,
    case when pc.relkind = 'p' then pg_get_partkeydef(pc.oid) end as partition_key,
    (
        select quote_ident(ic.relname)
        from
            pg_catalog.pg_index pi
            inner join pg_catalog.pg_class ic on ic.oid = pi.indexrelid
            inner join pg_catalog.pg_am am on am.oid = ic.relam and am.amname = 'btree'
        where
            pi.indrelid = pc.oid and
            pi.indisunique and
            pi.indisvalid and
            pi.indpred is null and
            pi.indexprs is null and
            pi.indnatts = pi.indnkeyatts and
            /* ADD PRIMARY KEY USING INDEX cannot take a constraint's index */
            not exists (select 1 from pg_catalog.pg_constraint con where con.conindid = pi.indexrelid) and
            not exists (
                select 1
                from pg_catalog.pg_attribute a
                where a.attrelid = pc.oid and a.attnum = any(pi.indkey) and not a.attnotnull
            )
        order by pi.indnkeyatts, ic.relname
        limit 1
    ) as candidate_index
from
    pg_catalog.pg_class pc
    inner join pg_catalog.pg_namespace nsp on nsp.oid = pc.relnamespace
where
    pc.relkind in ('r', 'p') and
    not pc.relispartition and /* a partition gets its parent's primary key */
    not exists (
        select 1 from pg_catalog.pg_constraint c
        where c.conrelid = pc.oid and c.contype = 'p'
    ) and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = pc.oid and d.deptype = 'e'
    ) and
    {schema_filter}
order by schema_name, table_name;
