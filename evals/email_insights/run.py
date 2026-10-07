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

So a mail that the screen closes never reaches the checks. ``screen_open``
in ``scripted_answers.json`` holds an honest answer for the newsletter and
for the injection mail. :func:`run_screen_open` checks them with the screen
forced open, and the report shows the result outside the bars (review
round 1).

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
from evals.email_insights.dataset import Mail, load_answers, load_mails, load_screen_open

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


def run_screen_open(
    mails: list[Mail], answers: dict[str, dict[str, Any]],
) -> checkers.Produced:
    """Each fact that the checks keep for each mail of *answers*, with the
    screen forced open. No bar reads this. It shows what the quote rule
    alone lets through."""
    produced: checkers.Produced = {}
    for mail in mails:
        if mail.id not in answers:
            continue
        for key, source in mail.sources().items():
            result = extract.check_answer(answers[mail.id].get(key, NO_ANSWER), source)
            produced.setdefault(mail.id, []).extend((key, fact) for fact in result.facts)
    return produced


def _shown(produced: checkers.Produced) -> dict[str, list[dict[str, Any]]]:
    return {
        mail_id: [{"source": key, "type": f.fact_type, "ref": f.ref,
                   "amount": None if f.amount is None else str(f.amount),
                   "currency": f.currency,
                   "due_on": None if f.due_on is None else f.due_on.isoformat(),
                   "confidence": f.confidence} for key, f in rows]
        for mail_id, rows in produced.items()
    }


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
    score = checkers.score(mails, produced, scripted=True)
    report = {"mode": "scripted", "extractor_version": extract.VERSION,
              "mails": len(mails), **score.summary(), "drops": dict(sorted(drops.items())),
              "screen_open": _shown(run_screen_open(mails, load_screen_open()))}
    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    return EXIT_PASS if score.passed() else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
