-- pghealth: char_column
-- Ported from pg-index-health-sql, sql/columns_with_char_type.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted identifiers,
-- extension tables skipped, the schema and table as separate columns, and
-- the declared type with its length.

-- Columns of type char(n) (character(n), bpchar(n)). Values are padded with
-- spaces to n characters; comparisons ignore the padding and a cast to text
-- drops it, so trailing spaces a client wrote vanish in some expressions and
-- not in others. The padding saves no space or time; text or varchar does
-- the same job without the surprises.
select
    nsp.nspname::text as schema_name,
    pc.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(pc.relname) as table_ident,
    col.attname::text as column_name,
    quote_ident(col.attname) as column_ident,
    format_type(col.atttypid, col.atttypmod) as column_type,
    col.atttypmod - 4 as type_length /* atttypmod of char(n) is n + 4 */
from
    pg_catalog.pg_class pc
    inner join pg_catalog.pg_namespace nsp on nsp.oid = pc.relnamespace
    inner join pg_catalog.pg_attribute col on col.attrelid = pc.oid
where
    pc.relkind in ('r', 'p') and
    not pc.relispartition and /* reported on the partitioned table */
    col.attnum > 0 and /* not a system column */
    not col.attisdropped and
    col.atttypid = 'bpchar'::regtype and
    col.atttypmod > 0 and /* bpchar without a length pads nothing */
    {schema_filter} and
    not exists (
        select 1 from pg_catalog.pg_depend d
        where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = pc.oid and d.deptype = 'e'
    )
order by schema_name, table_name, column_name;
