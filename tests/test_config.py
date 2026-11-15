import pytest

from pytest_pghealth import CHECKS, Finding
from pytest_pghealth.config import (
    ConfigError,
    Ignore,
    Settings,
    describe,
    load_pyproject,
    parse_table,
)
from pytest_pghealth.frameworks import bind, framework_ignores

DEFAULT = tuple(name for name, check in CHECKS.items() if check.default)


def finding(check="foreign_key_without_index", obj="orders_customer_id_fkey"):
    return Finding(check, "public", "orders", obj, "m", "f")


def test_defaults():
    settings = Settings()
    assert settings.checks == DEFAULT
    assert settings.schemas is None
    assert settings.sequence_remaining_percent == 20.0
    assert not settings.session


def test_enable_and_disable():
    assert (
        "btree_index_on_array"
        in parse_table({"enable": ["btree_index_on_array"]}).checks
    )
    assert parse_table({"enable": ["all"]}).checks == tuple(CHECKS)
    assert (
        "int4_primary_key" not in parse_table({"disable": ["int4_primary_key"]}).checks
    )
    assert parse_table({"disable": ["all"], "enable": ["invalid_index"]}).checks == (
        "invalid_index",
    )
    assert parse_table({"enable": ["all"], "disable": ["all"]}).checks == ()
    assert parse_table(
        {"enable": ["invalid_index"], "disable": ["invalid_index"]}
    ).checks == (tuple(n for n in DEFAULT if n != "invalid_index"))
    assert parse_table({"enable": "invalid_index"}).checks == DEFAULT


def test_full_table():
    settings = parse_table(
        {
            "session": True,
            "url": "postgresql://localhost/test",
            "fixture": "engine",
            "schemas": ["public", "billing"],
            "sequence_remaining_percent": 5,
            "warn_only": True,
            "json": "pghealth.json",
            "django_alias": "replica",
            "ignore": [
                {"check": "duplicate_index", "object": "public.t.*", "reason": "legacy"}
            ],
        }
    )
    assert settings.schemas == ("public", "billing")
    assert settings.sequence_remaining_percent == 5.0
    assert settings.ignore == (Ignore("duplicate_index", "public.t.*", "legacy"),)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"enabel": ["x"]}, "unknown key(s) enabel"),
        ({"enable": ["no_such_check"]}, "unknown check(s) no_such_check"),
        ({"schemas": [1]}, "schemas must be a list"),
        ({"session": "yes"}, "session must be true or false"),
        ({"url": ""}, "url must be a non-empty string"),
        ({"sequence_remaining_percent": 120}, "between 0 and 100"),
        ({"sequence_remaining_percent": True}, "must be a number"),
        (
            {"ignore": [{"check": "duplicate_index", "object": "x"}]},
            "a reason is required",
        ),
        (
            {"ignore": [{"check": "dup_index", "object": "x", "reason": "r"}]},
            "unknown check",
        ),
        ({"ignore": [{"object": "x", "reason": "r", "why": 1}]}, "unknown key(s) why"),
        ({"ignore": ["x"]}, "must be a table"),
        ({"ignore": "x"}, "ignore must be a list"),
    ],
)
def test_invalid_tables(table, message):
    with pytest.raises(ConfigError, match=None) as info:
        parse_table(table)
    assert message in str(info.value)


def test_ignore_matching():
    assert Ignore("*", "public.orders", "r").matches(finding())
    assert Ignore("foreign_key_without_index", "public.orders.*", "r").matches(
        finding()
    )
    assert Ignore("foreign_key_*", "public.*", "r").matches(finding())
    assert not Ignore("duplicate_index", "public.orders", "r").matches(finding())
    assert not Ignore("*", "public.order", "r").matches(finding())
    assert not Ignore("*", "PUBLIC.orders", "r").matches(finding())


def test_ignore_without_check_means_every_check():
    (ignore,) = parse_table(
        {"ignore": [{"object": "public.orders", "reason": "r"}]}
    ).ignore
    assert ignore.check == "*"


def test_with_overrides_skips_none_and_validates():
    settings = Settings().with_overrides(enable=None, schemas=["s"], warn_only=None)
    assert settings.schemas == ("s",)
    with pytest.raises(ConfigError):
        Settings().with_overrides(disable=["nope"])
    settings = Settings().with_overrides(
        ignore=[{"check": "invalid_index", "object": "*", "reason": "r"}]
    )
    assert settings.ignore[0].check == "invalid_index"


def test_load_pyproject(tmp_path):
    path = tmp_path / "pyproject.toml"
    assert load_pyproject(path) == Settings()
    path.write_text("[tool.other]\nx = 1\n")
    assert load_pyproject(path) == Settings()
    path.write_text('[tool.pghealth]\ndisable = ["int4_primary_key"]\n')
    assert "int4_primary_key" not in load_pyproject(path).checks
    path.write_text("[tool.pghealth]\nbad = 1\n")
    with pytest.raises(
        ConfigError, match=r"pyproject.toml: \[tool.pghealth\]: unknown key"
    ):
        load_pyproject(path)
    path.write_text("[tool.pghealth\n")
    with pytest.raises(ConfigError):
        load_pyproject(path)


def test_ignore_framework_tables():
    assert Settings().ignore_framework_tables is True
    off = parse_table({"ignore_framework_tables": False})
    assert off.ignore_framework_tables is False
    settings = parse_table({"ignore_framework_tables": ["alembic", "django-taggit"]})
    assert [
        i.tables.package for i in framework_ignores(settings.ignore_framework_tables)
    ] == ["django-taggit", "alembic"]
    assert framework_ignores(False) == ()
    with pytest.raises(ConfigError, match="unknown package"):
        parse_table({"ignore_framework_tables": ["rails"]})
    with pytest.raises(ConfigError, match="non-empty strings"):
        parse_table({"ignore_framework_tables": 1})
    with pytest.raises(ConfigError, match="empty list"):
        parse_table({"ignore_framework_tables": []})


@pytest.mark.parametrize(
    ("check", "table", "skipped"),
    [
        ("int4_primary_key", "auth_permission", True),
        ("overlapping_index", "django_celery_beat_periodictask", True),
        ("json_column", "wagtailcore_page", True),
        ("int4_primary_key", "alembic_version", True),
        ("invalid_index", "auth_permission", False),
        ("sequence_near_overflow", "django_admin_log", False),
        ("int4_primary_key", "auth_tokens", False),
        ("int4_primary_key", "wagtail_extras_thing", False),
        ("int4_primary_key", "orders", False),
    ],
)
def test_framework_ignore_matching(check, table, skipped):
    ignores = bind(framework_ignores(True), {("app", "django_migrations")})
    found = Finding(check, "app", table, None, "m", "f")
    assert any(i.matches(found) for i in ignores) is skipped
    # without django_migrations in that schema, only the non-Django packages apply
    elsewhere = Finding(check, "other", table, None, "m", "f")
    assert any(i.matches(elsewhere) for i in ignores) is (
        skipped and table == "alembic_version"
    )


def test_override_ignores_have_no_file():
    [ignore] = Settings().with_overrides(ignore=[{"object": "a", "reason": "r"}]).ignore
    assert ignore.source == ""
    assert "from code (an override)" in describe(Settings(ignore=(ignore,)))


def test_ignore_source(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "[[tool.pghealth.ignore]]\nobject = 'public.a'\nreason = 'r'\n"
        "[[tool.pghealth.ignore]]\nobject = 'public.b'\nreason = 'r'\n"
    )
    first, second = load_pyproject(path).ignore
    assert first.source == f"{path}: ignore #1"
    assert second.source == f"{path}: ignore #2"
    assert first == Ignore("*", "public.a", "r")  # the source is not compared


def test_describe():
    text = describe(
        Settings(
            ignore=(
                Ignore(
                    "duplicate_index",
                    "public.t",
                    "legacy",
                    source="pyproject.toml: ignore #1",
                ),
            ),
            ignore_framework_tables=("alembic",),
        )
    )
    assert (
        "ignores (1):\n  check duplicate_index, object public.t: legacy\n    from pyproject.toml: ignore #1"
        in text
    )
    assert "except invalid_index and sequence_near_overflow" in text
    assert text.endswith("  alembic: alembic_version")
    assert "django.contrib.auth" not in text
    full = describe(Settings())
    assert "auth_user_user_permissions (where django_migrations exists)" in full
    text = describe(Settings(ignore_framework_tables=False))
    assert "ignores: none" in text
    assert "framework-owned tables: reported" in text
