-- pghealth: invalid_index
-- Ported from pg-index-health-sql, sql/invalid_indexes.sql
-- (https://github.com/mfvanek/pg-index-health-sql, commit 8d8b71f).
-- Copyright (c) 2019-2026. Ivan Vakhrushev and others.
-- Licensed under the Apache License 2.0 (LICENSES/Apache-2.0.txt).
-- Modified for pytest-pghealth: several schemas, quoted
-- identifiers, and the index kind (a partitioned index is invalid until every
-- partition has an attached index).

-- Invalid indexes, usually left behind by a failed CREATE INDEX CONCURRENTLY.
-- The planner never uses them, but every write still updates them.
select
    nsp.nspname::text as schema_name,
    tbl.relname::text as table_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(tbl.relname) as table_ident,
    ic.relname::text as index_name,
    quote_ident(nsp.nspname) || '.' || quote_ident(ic.relname) as index_ident,
    quote_ident(nsp.nspname) as schema_ident,
    ic.relkind::text as index_kind
from
    pg_catalog.pg_index pi
    inner join pg_catalog.pg_class ic on ic.oid = pi.indexrelid
    inner join pg_catalog.pg_class tbl on tbl.oid = pi.indrelid
    inner join pg_catalog.pg_namespace nsp on nsp.oid = ic.relnamespace
where
    not pi.indisvalid and
    {schema_filter}
order by schema_name, table_name, index_name;
