-- pghealth: sequence_near_overflow
-- Ported from pg-index-health-sql, sql/sequence_overflow.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted
-- identifiers, the threshold inlined, extension sequences skipped, and the
-- column that owns the sequence.

-- Sequences that have used up most of their range. A test database restored
-- from a production snapshot shows the production state.
with
    all_sequences as (
        select
            nsp.nspname::text as schema_name,
            pc.relname::text as sequence_name,
            quote_ident(nsp.nspname) || '.' || quote_ident(pc.relname) as sequence_ident,
            pc.oid as sequence_oid,
            format_type(s.seqtypid, null) as data_type,
            s.seqstart as start_value,
            s.seqmin as min_value,
            s.seqmax as max_value,
            s.seqincrement as increment_by,
            case
                when has_sequence_privilege(pc.oid, 'select,usage'::text) then pg_sequence_last_value(pc.oid::regclass)
                else null::bigint
            end as last_value
        from
            pg_catalog.pg_sequence s
            inner join pg_catalog.pg_class pc on pc.oid = s.seqrelid
            inner join pg_catalog.pg_namespace nsp on nsp.oid = pc.relnamespace
        where
            pc.relkind = 'S' and
            not s.seqcycle and /* a cycling sequence wraps around instead of failing */
            {schema_filter} and
            not exists (
                select 1 from pg_catalog.pg_depend d
                where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = pc.oid and d.deptype = 'e'
            )
    ),

    sequence_state as (
        select
            t.*,
            (
                case
                    when t.increment_by > 0
                        then 100.0 * (t.max_value - coalesce(t.last_value, t.start_value)) / (t.max_value - t.min_value)
                    else 100.0 * (coalesce(t.last_value, t.start_value) - t.min_value) / (t.max_value - t.min_value)
                end
            )::float8 as remaining_percent
        from all_sequences t
    )

select
    s.schema_name,
    s.sequence_name,
    s.sequence_ident,
    s.data_type,
    s.last_value,
    s.max_value,
    s.remaining_percent,
    owner_tbl.relname::text as table_name,
    quote_ident(owner_nsp.nspname) || '.' || quote_ident(owner_tbl.relname) as table_ident,
    quote_ident(owner_col.attname) as column_ident,
    owner_col.attidentity <> '' as is_identity
from
    sequence_state s
    left join pg_catalog.pg_depend dep
        on dep.classid = 'pg_catalog.pg_class'::regclass and dep.objid = s.sequence_oid and
        dep.refclassid = 'pg_catalog.pg_class'::regclass and dep.refobjsubid > 0 and dep.deptype in ('a', 'i')
    left join pg_catalog.pg_class owner_tbl on owner_tbl.oid = dep.refobjid
    left join pg_catalog.pg_namespace owner_nsp on owner_nsp.oid = owner_tbl.relnamespace
    left join pg_catalog.pg_attribute owner_col on owner_col.attrelid = dep.refobjid and owner_col.attnum = dep.refobjsubid
where
    s.remaining_percent <= {remaining_percent}
order by s.schema_name, s.sequence_name;
