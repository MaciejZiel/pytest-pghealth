-- pghealth: timestamp_without_time_zone
-- Ported from pg-index-health-sql, sql/columns_with_timestamp_or_timetz_type.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted identifiers,
-- extension tables skipped, the schema and table as separate columns, and
-- the type's precision.

-- Columns of type timestamp (without time zone) or timetz. See
-- https://wiki.postgresql.org/wiki/Don't_Do_This#Don.27t_use_timestamp_.28without_time_zone.29
select
    nsp.nspname::text as schema_name,
    pc.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(pc.relname) as table_ident,
    col.attname::text as column_name,
    quote_ident(col.attname) as column_ident,
    format_type(col.atttypid, col.atttypmod) as column_type,
    col.atttypid = 'timetz'::regtype as is_timetz,
    col.atttypmod as type_precision /* -1 when the column has no precision */
from
    pg_catalog.pg_class pc
    inner join pg_catalog.pg_namespace nsp on nsp.oid = pc.relnamespace
    inner join pg_catalog.pg_attribute col on col.attrelid = pc.oid
where
    pc.relkind in ('r', 'p') and
    not pc.relispartition and /* reported on the partitioned table */
    col.attnum > 0 and /* not a system column */
    not col.attisdropped and
    col.atttypid in ('timestamp without time zone'::regtype, 'timetz'::regtype) and
    {schema_filter} and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = pc.oid and d.deptype = 'e'
    )
order by schema_name, table_name, column_name;
