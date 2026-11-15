"""PostgreSQL schema health checks for pytest.

Run the checks from a test with the ``pghealth`` fixture, at the end of the
session with ``--pghealth``, or from any code with :func:`run_checks`.
"""

from pytest_pghealth.checks import CHECKS, Check
from pytest_pghealth.config import ConfigError, Ignore, Settings
from pytest_pghealth.findings import CheckError, Finding, IgnoredFinding, Report
from pytest_pghealth.runner import run_checks

__version__ = "0.1.0.dev0"

__all__ = [
    "CHECKS",
    "Check",
    "CheckError",
    "ConfigError",
    "Finding",
    "Ignore",
    "IgnoredFinding",
    "Report",
    "Settings",
    "__version__",
    "run_checks",
]
