"""CIV-29a (D218): tell a browser-suite failure that main already has from a new one.

Reads Playwright JSON reports. The PR report comes from this run. The main
reports come from the newest e2e-nightly run on main that this repo started
(a schedule or a dispatch, never a fork PR). Pass `--main` once per shard. The
failed tests of all main reports are joined, because a spec file can move to
another shard when the test count changes.

Exit 0: the suite passed, or every failed test is also in main's report.
Exit 1: a failure is new, the PR report is unreadable, the suite exited
non-zero with no failed test in the report, or main's report is missing.
A missing main report keeps the old rule: any failure fails the job.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def failed_tests(report: dict) -> set[str]:
    """Return 'file :: title path' for each spec that did not end well."""
    out: set[str] = set()

    def walk(suite: dict, path: list[str]) -> None:
        here = path + ([suite["title"]] if suite.get("title") else [])
        for spec in suite.get("specs", []):
            if not spec.get("ok", True):
                out.add(f"{spec.get('file', '?')} :: {' > '.join([*here, spec.get('title', '?')])}")
        for child in suite.get("suites", []):
            walk(child, here)

    for top in report.get("suites", []):
        walk(top, [])
    return out


def load(path: str | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", required=True)
    ap.add_argument("--main", action="append", default=[],
                    help="a main nightly report; pass once per shard")
    ap.add_argument("--exit-code", type=int, default=0, help="exit code of the suite run")
    ap.add_argument("--summary", default=None, help="file to append the Markdown summary to")
    args = ap.parse_args(argv)

    lines: list[str] = []

    def done(code: int) -> int:
        text = "\n".join(lines)
        print(text)
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        return code

    pr = load(args.pr)
    if pr is None:
        if args.exit_code == 0:
            lines.append("No browser report found, but the suite exited 0. The job passes.")
            return done(0)
        lines.append("The suite failed and left no readable report. The job fails.")
        return done(1)
    failed = failed_tests(pr)
    if not failed:
        if args.exit_code != 0:
            lines.append(f"The suite exited {args.exit_code} with no failed test in the report. The job fails.")
            return done(1)
        lines.append("No browser test failed.")
        return done(0)
    bases = [b for b in (load(p) for p in args.main) if b is not None]
    if not bases:
        lines.append("Main's nightly result could not be read, so every failure counts as new. The job fails.")
        lines += [f"- new: {t}" for t in sorted(failed)]
        return done(1)
    main_failed: set[str] = set().union(*(failed_tests(b) for b in bases))
    inherited = failed & main_failed
    new = failed - inherited
    if inherited:
        lines.append(f"Inherited from main ({len(inherited)}): the newest nightly on main fails these too.")
        lines += [f"- inherited: {t}" for t in sorted(inherited)]
    if new:
        lines.append(f"New failures ({len(new)}): main does not fail these. The job fails.")
        lines += [f"- new: {t}" for t in sorted(new)]
        return done(1)
    # A run with a global error (a crash, a setup failure, the watchdog) is
    # not only its failed tests. Inherited tests must not hide it (verifier,
    # 2026-11-12).
    if args.exit_code != 0 and pr.get("errors"):
        lines.append(f"The suite exited {args.exit_code} with {len(pr['errors'])} run error(s) "
                     "beside the inherited failures. The job fails.")
        return done(1)
    lines.append("Every failure is inherited from main. The job passes.")
    return done(0)


if __name__ == "__main__":
    sys.exit(main())
