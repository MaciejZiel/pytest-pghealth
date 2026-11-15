"""pytest-django: the session check finds the test database by itself.

Runs in the "django" CI job, which installs Django, pytest-django and psycopg
(Django's PostgreSQL backend needs psycopg, which is LGPL and therefore not a
development dependency). Skipped elsewhere.
"""

import os

import pytest

pytest.importorskip("django")
pytest.importorskip("pytest_django")
pytest.importorskip("psycopg")

URL = os.environ.get("PGHEALTH_TEST_URL")
if not URL:
    pytest.skip("PGHEALTH_TEST_URL is not set", allow_module_level=True)

SETTINGS = """
import os
from urllib.parse import urlsplit

url = urlsplit(os.environ["PGHEALTH_TEST_URL"])
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": url.path.lstrip("/"),
        "USER": url.username,
        "PASSWORD": url.password,
        "HOST": url.hostname,
        "PORT": url.port or 5432,
    }
}
INSTALLED_APPS = ["shop"]
SECRET_KEY = "not secret"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
"""

MODELS = """
from django.db import models


class Customer(models.Model):
    name = models.TextField()


class Order(models.Model):
    # db_index=False: Django indexes foreign keys unless told not to
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, db_index=False)
"""

TESTS = """
import pytest
from django.db import connection

from shop.models import Order


@pytest.mark.django_db
def test_orders():
    assert Order.objects.count() == 0


@pytest.mark.django_db
def test_fixture(pghealth):
    report = pghealth.check(connection)
    assert [f.check for f in report.findings] == ["foreign_key_without_index"]
"""


@pytest.fixture
def project(pytester):
    pytester.makepyfile(settings=SETTINGS, test_shop=TESTS)
    pytester.mkpydir("shop")
    (pytester.path / "shop" / "models.py").write_text(MODELS)
    pytester.makepyprojecttoml(
        """
        [tool.pytest.ini_options]
        DJANGO_SETTINGS_MODULE = "settings"
        django_find_project = false
        pythonpath = ["."]
        """
    )
    return pytester


def test_session_check_uses_the_django_test_database(project):
    result = project.runpytest_subprocess("--pghealth")
    result.assert_outcomes(passed=2)
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        [
            "*= pghealth =*",
            "PostgreSQL *: 11 checks, 1 problem (Django database 'default')",
            "foreign_key_without_index: public.shop_order.shop_order_customer_id_*",
            ("  fix: CREATE INDEX CONCURRENTLY ON public.shop_order (customer_id);"),
        ]
    )


def test_no_database_test_no_check(project):
    project.makepyfile(test_shop="def test_plain(): pass")
    result = project.runpytest_subprocess("--pghealth")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*no test used the Django test database*"])
