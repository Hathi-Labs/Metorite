"""The runner of the injection eval: ``python -m evals.email_injection.run --scripted``.

WS-17 EM-G11d (UMCC). Spec: ``project-docs/specs/email_app_master_plan.md``
section 12.3.13.8, scope 5, as the audit of 2026-10-14 narrowed it.

``--scripted`` runs each attack mail of :mod:`evals.email_injection.mails`
through each path of :mod:`evals.email_injection.paths`, and scores the
result with :mod:`evals.email_injection.checkers`. It calls no model, no
Router, no gateway and no database. The bar is structural: zero failures.

A behavioural run against a live model is deferred until a spec edit records
it. Without ``--scripted`` the runner says so and exits with code 2.

Exit codes: 0 every check passed, 1 a check failed, 2 the run cannot start.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable

from evals.email_injection import checkers
from evals.email_injection.mails import MAILS, Mail
from evals.email_injection.paths import PATHS, Capture

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2


def run_structural(
    mails: Iterable[Mail] = MAILS,
    paths: dict[str, Callable[[Mail], Capture]] | None = None,
) -> list[checkers.Failure]:
    """Each failure of each check, for each mail and each path.

    *paths* is a parameter so that a test can pass a changed path and see a
    check fail (R7). Each path runs twice for each mail, so the token check
    can compare two runs."""
    failures: list[checkers.Failure] = []
    for name, build in (paths or PATHS).items():
        for mail in mails:
            first, second = build(mail), build(mail)
            assert first.path == name, (first.path, name)
            failures.extend(checkers.check(mail, first, again=second))
    return failures


def report(failures: list[checkers.Failure], mails: int, paths: int) -> dict[str, object]:
    """The summary that the runner prints."""
    by_check = Counter(f.check for f in failures)
    return {
        "mails": mails,
        "paths": paths,
        "runs": mails * paths,
        "failures": len(failures),
        "by_check": {c: by_check.get(c, 0) for c in checkers.CHECKS},
        "first": [f.__dict__ for f in failures[:20]],
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scripted", action="store_true",
                        help="the structural run, with no model")
    args = parser.parse_args(argv)
    if not args.scripted:
        print("Only --scripted runs. A run against a live model is deferred "
              "until a spec edit records it (EM-G11d).", file=sys.stderr)
        return EXIT_NO_GO
    failures = run_structural()
    print(json.dumps(report(failures, len(MAILS), len(PATHS)), indent=2))
    return EXIT_PASS if not failures else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
