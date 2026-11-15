"""Run every pghealth check against a migrated real-world schema.

Used by .github/workflows/realworld.yml: python scripts/realworld.py NAME URL OUTDIR.
Writes OUTDIR/NAME.json and OUTDIR/NAME.txt, prints the report, and adds a
per-check count to the GitHub step summary. Always exits 0: the findings are
material for review, not a verdict.
"""

from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

from pytest_pghealth import run_checks


def main() -> None:
    name, url, out = sys.argv[1:4]
    report = run_checks(url, enable=["all"])
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(report.to_json(), encoding="utf-8")
    text = report.format()
    (out_dir / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    counts = collections.Counter(f.check for f in report.findings)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(
                f"### {name}\n\n{report.summary()}\n\n| check | findings |\n|---|---|\n"
            )
            stream.writelines(
                f"| {check} | {counts.get(check, 0)} |\n" for check in report.checks
            )
            stream.writelines(
                f"\nfailed: {error.check}: {error.error}\n" for error in report.errors
            )
            stream.write("\n")


if __name__ == "__main__":
    main()
