-- pghealth: int4_primary_key
-- Written for pytest-pghealth; the sequence lookup follows
-- primary_keys_with_serial_types.sql of pg-index-health-sql
-- (https://github.com/mfvanek/pg-index-health-sql).

-- Primary key columns of type integer or smallint whose values come from a
-- sequence (serial or identity). An integer key fails with "nextval: reached
-- maximum value" after 2,147,483,647 rows (smallint: 32,767), and changing
-- the type later rewrites the table and every referencing foreign key.
select
    nsp.nspname::text as schema_name,
    pc.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(pc.relname) as table_ident,
    col.attname::text as column_name,
    quote_ident(col.attname) as column_ident,
    format_type(col.atttypid, col.atttypmod) as data_type,
    col.attidentity <> '' as is_identity,
    quote_ident(seq_nsp.nspname) || '.' || quote_ident(seq.relname) as sequence_ident
from
    pg_catalog.pg_class pc
    inner join pg_catalog.pg_namespace nsp on nsp.oid = pc.relnamespace
    inner join pg_catalog.pg_constraint c on c.conrelid = pc.oid and c.contype = 'p'
    inner join pg_catalog.pg_attribute col on col.attrelid = pc.oid and col.attnum = any(c.conkey)
    inner join pg_catalog.pg_depend dep
        on dep.refclassid = 'pg_catalog.pg_class'::regclass and dep.refobjid = pc.oid and
        dep.refobjsubid = col.attnum and dep.classid = 'pg_catalog.pg_class'::regclass and dep.deptype in ('a', 'i')
    inner join pg_catalog.pg_class seq on seq.oid = dep.objid and seq.relkind = 'S'
    inner join pg_catalog.pg_namespace seq_nsp on seq_nsp.oid = seq.relnamespace
where
    pc.relkind in ('r', 'p') and
    not pc.relispartition and
    not col.attisdropped and
    col.atttypid in ('int4'::regtype, 'int2'::regtype) and
    c.conparentid = 0 and
    {schema_filter} and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = pc.oid and d.deptype = 'e'
    )
order by schema_name, table_name, column_name;
