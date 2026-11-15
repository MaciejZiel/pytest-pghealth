import pytest

from pytest_pghealth import CHECKS, Finding
from pytest_pghealth.config import (
    ConfigError,
    Ignore,
    Settings,
    load_pyproject,
    parse_table,
)

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
