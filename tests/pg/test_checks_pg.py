"""Each check against real PostgreSQL: what it finds, what it leaves alone,
and that its suggested fix makes the finding go away."""

import pytest
from sqlalchemy.exc import DBAPIError

from pytest_pghealth import CHECKS, run_checks


def test_foreign_key_without_index(schema):
    schema.sql(
        "create table parent (id bigint primary key)",
        "create table child (id bigint primary key, parent_id bigint references parent)",
        # covered: the index's leading columns are the key's, in any order
        "create table p2 (a int, b int, primary key (a, b))",
        "create table c2 (id int primary key, a int, b int, c int, foreign key (a, b) references p2)",
        "create index on c2 (b, a, c)",
        # not covered: partial index, key column second, key column only in INCLUDE
        "create table c3 (id int primary key, parent_id bigint references parent)",
        "create index on c3 (parent_id) where parent_id > 0",
        "create table c4 (id int primary key, x int, parent_id bigint references parent)",
        "create index on c4 (x, parent_id)",
        "create table c5 (id int primary key, x int, parent_id bigint references parent)",
        "create index on c5 (x) include (parent_id)",
    )
    assert schema.targets("foreign_key_without_index") == [
        "c3.c3_parent_id_fkey",
        "c4.c4_parent_id_fkey",
        "c5.c5_parent_id_fkey",
        "child.child_parent_id_fkey",
    ]
    fix = next(
        f.fix for f in schema.check("foreign_key_without_index") if f.table == "child"
    )
    assert fix == (f"CREATE INDEX CONCURRENTLY ON {schema.name}.child (parent_id);")
    schema.apply_all("foreign_key_without_index")


def test_foreign_key_on_partitioned_table(schema):
    schema.sql(
        "create table sensor (id bigint primary key)",
        "create table reading (sensor_id bigint references sensor, ts date not null)"
        " partition by range (ts)",
        "create table reading_2026 partition of reading"
        " for values from ('2026-01-01') to ('2027-01-01')",
    )
    # reported once, on the partitioned table, not on each partition
    assert schema.targets("foreign_key_without_index") == [
        "reading.reading_sensor_id_fkey"
    ]
    schema.apply_all("foreign_key_without_index")


def test_quoted_identifiers(schema):
    schema.sql(
        'create table "Parent" (id int primary key)',
        'create table "Order Items" (id int primary key, "Parent Id" int references "Parent")',
        'create table "user" (name text)',
    )
    assert schema.targets("foreign_key_without_index") == [
        "Order Items.Order Items_Parent Id_fkey"
    ]
    (finding,) = schema.check("foreign_key_without_index")
    assert f'ON {schema.name}."Order Items" ("Parent Id");' in finding.fix
    schema.apply_all("foreign_key_without_index")
    schema.apply_all("table_without_primary_key")


def test_duplicate_index(schema):
    schema.sql(
        "create table t (id int primary key, a int, b int, c text)",
        "create index t_a_1 on t (a)",
        "create index t_a_2 on t (a)",
        # a plain index next to a unique constraint's: the plain one goes
        "alter table t add constraint t_b_key unique (b)",
        "create index t_b_idx on t (b)",
        # not duplicates: other predicate, other operator class, other order
        "create index t_c_1 on t (c)",
        "create index t_c_2 on t (c) where c is not null",
        "create index t_c_3 on t (c text_pattern_ops)",
        "create index t_c_4 on t (c desc)",
    )
    assert schema.targets("duplicate_index") == ["t.t_a_2", "t.t_b_idx"]
    messages = {f.object: f.message for f in schema.check("duplicate_index")}
    assert "identical to t_a_1" in messages["t_a_2"]
    assert "identical to t_b_key" in messages["t_b_idx"]
    schema.apply_all("duplicate_index")


def test_duplicate_unique_constraints(schema):
    schema.sql(
        "create table u (id int primary key, a int)",
        "alter table u add constraint u_a_key1 unique (a)",
        "alter table u add constraint u_a_key2 unique (a)",
    )
    (finding,) = schema.check("duplicate_index")
    assert finding.object == "u_a_key2"
    assert finding.fix == f"ALTER TABLE {schema.name}.u DROP CONSTRAINT u_a_key2;"
    schema.apply_all("duplicate_index")


def test_overlapping_index(schema):
    schema.sql(
        "create table o (id int primary key, a int, b int, c text)",
        "create index o_a on o (a)",
        "create index o_a_b on o (a, b)",
        # a unique index is never the redundant one
        "create unique index o_b_u on o (b)",
        "create index o_b_c on o (b, c)",
        # different sort order or predicate
        "create index o_c_desc on o (c desc)",
        "create index o_c_a on o (c, a)",
        "create index o_b_partial on o (b) where a > 0",
        # the primary key's index next to (id, a)
        "create index o_id_a on o (id, a)",
    )
    (finding,) = schema.check("overlapping_index")
    assert finding.object == "o_a"
    assert "prefix of o_a_b" in finding.message
    assert "foreign key" not in finding.message
    schema.apply_all("overlapping_index")


def test_invalid_index(schema):
    schema.sql(
        "create table inv (id int primary key, v int)",
        "insert into inv values (1, 1), (2, 1)",
    )
    with pytest.raises(DBAPIError):
        schema.sql("create unique index concurrently inv_v_key on inv (v)")
    (finding,) = schema.check("invalid_index")
    assert finding.object == "inv_v_key"
    assert finding.fix == f"REINDEX INDEX CONCURRENTLY {schema.name}.inv_v_key;"
    schema.sql("delete from inv where id = 2")
    schema.apply_all("invalid_index")


def test_table_without_primary_key(schema):
    schema.sql(
        "create table nopk (a int)",
        "create table nopk_u (code text not null)",
        "create unique index nopk_u_code on nopk_u (code)",
        "create table withpk (id int primary key)",
        "create table part (id int, ts date not null) partition by range (ts)",
        "create table part_1 partition of part for values from ('2026-01-01') to ('2027-01-01')",
    )
    assert schema.targets("table_without_primary_key") == ["nopk", "nopk_u", "part"]
    fixes = {f.table: f.fix for f in schema.check("table_without_primary_key")}
    assert fixes["nopk_u"] == (
        f"ALTER TABLE {schema.name}.nopk_u ADD PRIMARY KEY USING INDEX nopk_u_code;"
    )
    assert "RANGE (ts)" in fixes["part"]
    for finding in schema.check("table_without_primary_key"):
        if finding.table != "part":
            schema.apply(finding)
    assert schema.targets("table_without_primary_key") == ["part"]


def test_int4_primary_key(schema):
    schema.sql(
        "create table s1 (id serial primary key)",
        "create table s2 (id int generated always as identity primary key)",
        "create table s3 (id bigserial primary key)",
        "create table s4 (id int primary key)",
        "create table s5 (id smallserial primary key)",
    )
    assert schema.targets("int4_primary_key") == ["s1.id", "s2.id", "s5.id"]
    schema.apply_all("int4_primary_key")
    # the fixes widen the sequences too, identity ones included
    types = schema.sql(
        "select format_type(seqtypid, null) from pg_sequence"
        " where seqrelid in ('s1_id_seq'::regclass, 's2_id_seq'::regclass, 's5_id_seq'::regclass)"
    )
    assert types == [("bigint",)] * 3


def test_sequence_near_overflow(schema):
    schema.sql(
        "create table so (id serial primary key)",
        "select setval('so_id_seq', 2000000000)",
        "create sequence standalone as smallint",
        "select setval('standalone', 30000)",
        "create sequence fine_seq",
        "create sequence used_seq",
        "select setval('used_seq', 1000)",
    )
    assert schema.targets("sequence_near_overflow") == ["so.so_id_seq", "standalone"]
    messages = {f.target: f.message for f in schema.check("sequence_near_overflow")}
    assert "6.87% of its range left" in messages[f"{schema.name}.so.so_id_seq"]
    assert schema.targets("sequence_near_overflow", sequence_remaining_percent=5) == []
    schema.apply_all("sequence_near_overflow")


def test_foreign_key_type_mismatch(schema):
    schema.sql(
        "create table fp (id bigint primary key, code varchar(10) unique)",
        "create table fc (id int primary key, fp_id int references fp (id),"
        " code varchar(20) references fp (code))",
        "create table ok (id int primary key, fp_id bigint references fp (id))",
    )
    assert schema.targets("foreign_key_type_mismatch") == [
        "fc.fc_code_fkey.code",
        "fc.fc_fp_id_fkey.fp_id",
    ]
    fixes = sorted(f.fix for f in schema.check("foreign_key_type_mismatch"))
    assert fixes[0].endswith("ALTER COLUMN code TYPE character varying(10);")
    assert fixes[1].endswith("ALTER COLUMN fp_id TYPE bigint;")
    schema.apply_all("foreign_key_type_mismatch")


def test_nullable_unique_column(schema):
    schema.sql(
        "create table nu (id int primary key, tenant_id int, email text not null,"
        " code text, unique (tenant_id, email))",
        # single-column: an optional unique value, not reported
        "create unique index nu_code on nu (code)",
        # the predicate excludes the NULLs
        "create unique index nu_partial on nu (tenant_id, code)"
        " where tenant_id is not null and code is not null",
    )
    if schema.server_version_num() >= 150000:
        schema.sql("create table nn (a int, b int, unique nulls not distinct (a, b))")
    (finding,) = schema.check("nullable_unique_column")
    assert finding.object == "nu_tenant_id_email_key"
    assert "tenant_id" in finding.message
    schema.apply_all("nullable_unique_column")


def test_not_valid_constraint(schema):
    schema.sql(
        "create table nv (id int primary key, n int)",
        "alter table nv add constraint nv_n_positive check (n > 0) not valid",
        "create table nvp (id int primary key)",
        "create table nvc (id int primary key, p int)",
        "alter table nvc add constraint nvc_p_fkey foreign key (p) references nvp not valid",
        "alter table nvp add constraint nvp_id_positive check (id > 0)",
    )
    assert schema.targets("not_valid_constraint") == [
        "nv.nv_n_positive",
        "nvc.nvc_p_fkey",
    ]
    schema.apply_all("not_valid_constraint")


def test_duplicate_foreign_key(schema):
    schema.sql(
        "create table dp (id int primary key, code int unique)",
        "create table dc (id int primary key, p int,"
        " constraint dc_a foreign key (p) references dp,"
        " constraint dc_b foreign key (p) references dp,"
        " constraint dc_c foreign key (p) references dp,"
        " constraint dc_d foreign key (p) references dp (code))",
    )
    assert schema.targets("duplicate_foreign_key") == ["dc.dc_b", "dc.dc_c"]
    assert all(
        "repeats dc_a" in f.message for f in schema.check("duplicate_foreign_key")
    )
    schema.apply_all("duplicate_foreign_key")


def test_btree_index_on_array(schema):
    schema.sql(
        "create table ar (id int primary key, tags text[], n int)",
        "create index ar_tags on ar (tags)",
        "create index ar_tags_gin on ar using gin (tags)",
        "create index ar_n on ar (n)",
    )
    assert schema.targets("btree_index_on_array") == ["ar.ar_tags"]
    schema.apply_all("btree_index_on_array")


def test_a_clean_schema_has_no_findings(schema):
    schema.sql(
        "create table customer (id bigint generated always as identity primary key,"
        " email text not null unique)",
        "create table orders (id bigint generated always as identity primary key,"
        " customer_id bigint not null references customer, total numeric not null)",
        "create index on orders (customer_id)",
    )
    assert schema.check(*CHECKS) == []


def test_schemas_are_isolated(schema, make_schema):
    other = make_schema()
    other.sql("create table nopk (a int)")
    assert schema.check("table_without_primary_key") == []
    assert other.targets("table_without_primary_key") == ["nopk"]


def test_default_schemas_are_all_user_schemas(schema, engine):
    schema.sql("create table nopk (a int)")
    report = run_checks(engine, disable=["all"], enable=["table_without_primary_key"])
    assert report.schemas is None
    assert f"{schema.name}.nopk" in [f.target for f in report.findings]
    assert not any(f.schema.startswith("pg_") for f in report.findings)


def test_duplicate_index_keeps_the_primary_key(schema):
    schema.sql(
        "create table pk (id int not null unique)",
        "alter table pk add primary key (id)",
    )
    (finding,) = schema.check("duplicate_index")
    assert finding.object == "pk_id_key"
    assert finding.fix == f"ALTER TABLE {schema.name}.pk DROP CONSTRAINT pk_id_key;"
    schema.apply_all("duplicate_index")
    assert schema.sql(
        f"select count(*) from pg_constraint where conrelid = '{schema.name}.pk'::regclass"
        " and contype = 'p'"
    ) == [(1,)]


def test_indexes_on_a_partitioned_table(schema):
    schema.sql(
        "create table pm (id int, a int, b int, ts date not null) partition by range (ts)",
        "create table pm_2026 partition of pm for values from ('2026-01-01') to ('2027-01-01')",
        "create index pm_a_1 on pm (a)",
        "create index pm_a_2 on pm (a)",
        "create index pm_b on pm (b)",
        "create index pm_b_a on pm (b, a)",
    )
    # reported on the partitioned table only, with fixes PostgreSQL accepts there
    assert schema.targets("duplicate_index") == ["pm.pm_a_2"]
    assert schema.targets("overlapping_index") == ["pm.pm_b"]
    schema.apply_all("duplicate_index")
    schema.apply_all("overlapping_index")


def test_foreign_keys_with_other_actions_are_not_duplicates(schema):
    schema.sql(
        "create table dp (id int primary key)",
        "create table dc (id int primary key, p int,"
        " constraint dc_a foreign key (p) references dp on delete restrict,"
        " constraint dc_b foreign key (p) references dp on delete cascade,"
        " constraint dc_c foreign key (p) references dp deferrable)",
    )
    assert schema.check("duplicate_foreign_key") == []


def test_nullable_unique_column_matches_whole_names(schema):
    schema.sql(
        "create table nw (id int primary key, a int, x int, ba int)",
        # "a" is not named by the predicate, even though "ba" contains it
        "create unique index nw_a_x on nw (a, x) where ba is not null",
    )
    (finding,) = schema.check("nullable_unique_column")
    assert "a, x" in finding.message


def test_btree_index_on_array_keeps_multicolumn_indexes(schema):
    schema.sql(
        "create table am (id int primary key, n int, tags text[])",
        "create index am_n_tags on am (n, tags)",
    )
    (finding,) = schema.check("btree_index_on_array")
    assert "DROP INDEX" not in finding.fix
    schema.apply(finding)
    assert schema.sql(
        "select count(*) from pg_indexes where tablename = 'am' and position('gin' in indexdef) > 0"
    ) == [(1,)]


def test_a_failing_check_leaves_the_callers_transaction_usable(schema, monkeypatch):
    from sqlalchemy import create_engine

    from pytest_pghealth.checks import Check

    original = Check.sql

    def broken(self):
        return (
            "-- pghealth: x\nselect 1 / 0"
            if self.name == "invalid_index"
            else original(self)
        )

    monkeypatch.setattr(Check, "sql", broken)
    schema.sql(
        "create table parent (id bigint primary key)",
        "create table child (id bigint primary key, parent_id bigint references parent)",
    )
    engine = create_engine(schema.engine.url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("select 1")  # the caller's transaction is open
            report = run_checks(connection, schemas=[schema.name])
            assert [e.check for e in report.errors] == ["invalid_index"]
            assert [f.check for f in report.findings] == ["foreign_key_without_index"]
            assert connection.exec_driver_sql("select 2").scalar() == 2
    finally:
        engine.dispose()


def test_overlapping_index_of_a_foreign_key(schema):
    # what Django creates for a ForeignKey next to a unique_together
    schema.sql(
        "create table grp (id int primary key)",
        "create table member (id int primary key, grp_id int references grp, name text,"
        " constraint member_grp_name_uniq unique (grp_id, name))",
        "create index member_grp_id_idx on member (grp_id)",
    )
    (finding,) = schema.check("overlapping_index")
    assert finding.object == "member_grp_id_idx"
    assert (
        "those of foreign key member_grp_id_fkey, which member_grp_name_uniq covers"
        in finding.message
    )
    schema.apply_all("overlapping_index")
    # dropping it leaves the foreign key indexed
    assert schema.check("foreign_key_without_index") == []


def test_overlapping_partial_indexes_say_nothing_about_foreign_keys(schema):
    schema.sql(
        "create table grp (id int primary key)",
        "create table member (id int primary key, grp_id int references grp, name text,"
        " deleted_at timestamptz)",
        "create index member_grp on member (grp_id) where deleted_at is null",
        "create index member_grp_name on member (grp_id, name) where deleted_at is null",
    )
    (finding,) = schema.check("overlapping_index")
    assert finding.object == "member_grp"
    assert "foreign key" not in finding.message
    # neither partial index serves the foreign key
    assert schema.targets("foreign_key_without_index") == ["member.member_grp_id_fkey"]
