"""The runner of the Insights eval: ``python -m evals.email_insights.run`` (WS-17 EM-T14b-1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §13.9.2 items 3 to 5.

``--scripted`` replays the answers of ``scripted_answers.json`` through the
REAL checks of :mod:`gateway.routes.email.automation.insights_extract`, and
scores the facts with :mod:`evals.email_insights.checkers`. It calls no model,
no Router and no database.

Stage 1, the screen (EM-T14b-0, #702), asks ``decide``, and a scripted run
calls no model. So this runner takes the expected screen answer of each mail in
its place: a mail whose ``screen.finance`` is false gets no extraction. EM-T14b-2 puts the real screen
here, and adds the model sweep through the Router on a local stack. Without
``--scripted`` the runner says so and exits with code 2.

Exit codes: 0 every bar passed, 1 a bar failed, 2 the run cannot start.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

from gateway.routes.email.automation import insights_extract as extract

from evals.email_insights import checkers
from evals.email_insights.dataset import Mail, load_answers, load_mails

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2
NO_ANSWER: dict[str, Any] = {"facts": []}


def run_scripted(
    mails: list[Mail], answers: dict[str, dict[str, Any]],
) -> tuple[checkers.Produced, Counter[str]]:
    """Each fact that the checks keep, by mail, and the drops of each check.

    *answers* is a parameter so that a test can pass a changed set and see a
    bar fail (R7)."""
    produced: checkers.Produced = {}
    drops: Counter[str] = Counter()
    for mail in mails:
        if not mail.screen.get("finance"):
            continue
        for key, source in mail.sources().items():
            answer = answers.get(mail.id, {}).get(key, NO_ANSWER)
            result = extract.check_answer(answer, source)
            drops.update(result.drops)
            produced.setdefault(mail.id, []).extend((key, fact) for fact in result.facts)
    return produced, drops


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.email_insights.run",
                                description=__doc__.splitlines()[0])
    p.add_argument("--scripted", action="store_true",
                   help="replay scripted_answers.json, and call no model")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.scripted:
        print("The model sweep comes with EM-T14b-2 (email_app_master_plan.md 13.9.2). "
              "Run with --scripted.", file=sys.stderr)
        return EXIT_NO_GO
    mails = load_mails()
    produced, drops = run_scripted(mails, load_answers())
    score = checkers.score(mails, produced)
    report = {"mode": "scripted", "extractor_version": extract.VERSION,
              "mails": len(mails), **score.summary(), "drops": dict(sorted(drops.items()))}
    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    return EXIT_PASS if score.passed() else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
