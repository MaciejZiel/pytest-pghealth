"""Run every pghealth check against a migrated real-world schema.

Used by .github/workflows/realworld.yml: python scripts/realworld.py NAME URL OUTDIR.
Writes OUTDIR/NAME.json and OUTDIR/NAME.txt, prints the report, and adds a
per-check count of findings and of findings ignored on framework-owned
tables (the default) to the GitHub step summary. Findings do not fail it (they
are material for review, not a verdict); a check run that cannot connect
does, after writing the error to OUTDIR/NAME.txt.
"""

from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

from pytest_pghealth import run_checks


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit("usage: realworld.py NAME URL OUTDIR")
    name, url, out = sys.argv[1:4]
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = run_checks(url, enable=["all"])
    except Exception as error:
        (out_dir / f"{name}.txt").write_text(f"the checks could not run: {error!r}\n")
        raise
    (out_dir / f"{name}.json").write_text(report.to_json(), encoding="utf-8")
    text = report.format()
    (out_dir / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    counts = collections.Counter(f.check for f in report.findings)
    # run_checks reads no pyproject.toml, so every ignored finding is on a
    # framework-owned table; count them by package
    ignored = collections.Counter(i.finding.check for i in report.ignored)
    owners = collections.Counter(
        i.reason.partition("(")[2].partition(")")[0] or i.reason for i in report.ignored
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(
                f"### {name}\n\n{report.summary()}\n\n"
                "| check | findings | ignored (framework-owned) |\n|---|---|---|\n"
            )
            stream.writelines(
                f"| {check} | {counts.get(check, 0)} | {ignored.get(check, 0)} |\n"
                for check in report.checks
            )
            stream.writelines(
                f"\n{count} ignored on tables of {package}\n"
                for package, count in sorted(owners.items())
            )
            stream.writelines(
                f"\nfailed: {error.check}: {error.error}\n" for error in report.errors
            )
            stream.write("\n")


if __name__ == "__main__":
    main()
