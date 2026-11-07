"""The runner of the proposer eval: ``python -m evals.project_rules_proposer.run`` (WS-ALN ALN-5b).

Spec: ``project-docs/specs/project_alignment.md`` §4.2 and the ALN-5b row of
§10.

``--scripted`` builds each prompt with the REAL ``messages_for`` and makes
the REAL ``_ask`` call of ``routes/project_rules_propose.py``. A recorder
stands in for ``acb_llm.context.acompletion_with_fallback``: it keeps each
request and replays the answer of ``cases.json``. The REAL ``judge`` and
``fit`` then read each answer. :mod:`evals.project_rules_proposer.checkers`
scores the run. It calls no model, no Router and no database, and it is free.

``--live`` reads each ``--page`` file of the repo as one page and asks the
model through the Router, one call for each page, as ``--member``. It needs
the Router on (exit 2 without it). ``--budget-usd`` caps the spend, 0.50 by
default. A budget over 0.50 needs ``--owner-approved``, because an eval over
USD 0.50 is OWNER-GATE. The runner estimates the cost of each call before it
sends it, and stops before a call that could pass the budget. It writes each
proposal to ``--out`` with ``"accepted": null``. A lead marks each one
``true`` or ``false``.

``--score FILE`` reads such a file and prints the share of proposals that the
lead accepted, against the bar of one half. The share is a report, not a
test. It counts proposals, never people (ALN-Q5).

Exit codes: 0 every bar passed, 1 a bar failed, 2 the run cannot start.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acb_skills.untrusted import UntrustedFrame
from gateway.routes import project_rules_propose as propose

from evals.project_rules_proposer import checkers

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.json"
EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2
#: The member of a scripted call. No call leaves the process.
SCRIPTED_MEMBER = "eval@rules-proposer.test"
#: The bar of the live measure: a lead accepts half of the proposals or more.
ACCEPT_BAR = 0.5
#: The default cap of a live run, and the line past which the owner must agree.
BUDGET_USD = 0.50
#: A worst case price of ``tier-fast`` in USD for each 1M tokens: the Flash
#: list prices at peak (``evals/one_assistant_measure/rate_card.json``).
USD_PER_1M_IN = 0.30
USD_PER_1M_OUT = 1.20
#: Three characters a token overstates the tokens of a prompt.
CHARS_PER_TOKEN = 3


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    """The cases of the set."""
    return list(json.loads(path.read_text(encoding="utf-8"))["cases"])


class Recorder:
    """A stand-in for ``acompletion_with_fallback`` that keeps each request
    and answers :attr:`answer`."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.answer: str = ""

    async def __call__(self, **kwargs: Any) -> tuple[Any, str]:
        self.requests.append(kwargs)
        message = SimpleNamespace(content=self.answer)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)]), "scripted"


@contextlib.contextmanager
def recording(recorder: Recorder) -> Iterator[Recorder]:
    """Put *recorder* in the place of the completion call, and put the real
    call back after."""
    from acb_llm import context

    real = context.acompletion_with_fallback
    context.acompletion_with_fallback = recorder
    try:
        yield recorder
    finally:
        context.acompletion_with_fallback = real


def _source(case: dict[str, Any]) -> propose.Source:
    kb = propose.rules._kb()
    return propose.Source(f"eval-{case['id']}", 1, str(case["title"]),
                          kb.same_line_ends(str(case["text"])))


async def run_case(case: dict[str, Any], recorder: Recorder) -> checkers.CaseResult:
    """One case: the real prompt, the real call shape, the real checks."""
    source = _source(case)
    frame = UntrustedFrame.new()
    answer = case["answer"]
    recorder.answer = answer if isinstance(answer, str) else json.dumps(answer)
    first = len(recorder.requests)
    raw = await propose._ask(SCRIPTED_MEMBER, propose.messages_for(source, frame=frame),
                             f"eval-{case['id']}")
    existing = dict(case.get("existing") or {})
    result = checkers.CaseResult(id=str(case["id"]), text=source.text, token=frame.token,
                                 waiting=int(existing.get("waiting") or 0),
                                 requests=recorder.requests[first:])
    try:
        judged = propose.judge(raw, source.text)
    except propose.UnreadableAnswer:
        result.unreadable = True
        return result
    current = propose.Current(
        frozenset(propose.fold(s) for s in existing.get("sentences") or ()), result.waiting)
    fitted = propose.fit(judged, current)
    result.kept = [(p.sentence, p.quote) for p in fitted.kept]
    result.dropped = dict(fitted.dropped)
    return result


async def run_scripted(cases: list[dict[str, Any]]) -> list[checkers.CaseResult]:
    """Each case of *cases*. A parameter, so a test can pass a changed set."""
    recorder = Recorder()
    with recording(recorder):
        return [await run_case(case, recorder) for case in cases]


def scripted_report(cases: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    """The report of a scripted run, and whether each bar passed."""
    results = asyncio.run(run_scripted(cases))
    result = checkers.score(results, {str(c["id"]): c.get("expect") or {} for c in cases},
                            page_cap=propose.PAGE_CAP, min_quote=propose.MIN_QUOTE,
                            scripted=True)
    return {"mode": "scripted", "feature": propose.FEATURE, "model": propose.MODEL,
            **result.summary()}, result.passed()


# ── The live run (OWNER-GATE over USD 0.50) ─────────────────────────────────


def estimate_usd(messages: list[dict[str, str]]) -> float:
    """A worst case cost of one call: each prompt token a cache miss, and an
    answer of ``ANSWER_TOKENS`` at the peak price."""
    chars = sum(len(m["content"]) for m in messages)
    tokens_out = int(propose.ANSWER_TOKENS)
    return ((chars / CHARS_PER_TOKEN) * USD_PER_1M_IN + tokens_out * USD_PER_1M_OUT) / 1_000_000


def within_budget(spent: float, estimate: float, budget: float) -> bool:
    """True when a call of *estimate* keeps the run at *budget* or under."""
    return spent + estimate <= budget


async def run_live(pages: list[Path], member: str, budget: float) -> dict[str, Any]:
    """One call for each page, until the budget stops the run."""
    from acb_llm import routed

    spent, rows, stopped = 0.0, [], None
    for n, path in enumerate(pages, start=1):
        source = propose.Source(path.as_posix(), 0, path.stem,
                                propose.rules._kb().same_line_ends(path.read_text(encoding="utf-8")))
        if len(source.text) > propose.PAGE_MAX_CHARS:
            rows.append({"page": source.page_id, "skipped": "too_long"})
            continue
        messages = propose.messages_for(source)
        estimate = estimate_usd(messages)
        if not within_budget(spent, estimate, budget):
            stopped = f"the budget: USD {spent:.4f} spent, the next call may cost {estimate:.4f}"
            break
        if not routed.routing_is_on():
            stopped = "the Router is off"
            break
        raw = await propose._ask(member, messages, f"eval-live-{n}")
        spent += estimate
        try:
            judged = propose.fit(propose.judge(raw, source.text), propose.Current())
        except propose.UnreadableAnswer:
            rows.append({"page": source.page_id, "unreadable": True})
            continue
        rows.append({"page": source.page_id, "dropped": judged.dropped, "proposals": [
            {"sentence": p.sentence, "quote": p.quote, "accepted": None} for p in judged.kept]})
    return {"mode": "live", "feature": propose.FEATURE, "model": propose.MODEL,
            "budget_usd": budget, "spent_usd_estimate": round(spent, 4),
            "stopped": stopped, "pages": rows}


def score_labels(data: dict[str, Any]) -> dict[str, Any]:
    """The share of proposals that the lead accepted. Counts, never people."""
    proposals = [p for page in data.get("pages") or [] for p in page.get("proposals") or []]
    labelled = [p for p in proposals if isinstance(p.get("accepted"), bool)]
    accepted = sum(1 for p in labelled if p["accepted"])
    ratio = accepted / len(proposals) if proposals else 0.0
    return {"proposed": len(proposals), "labelled": len(labelled), "accepted": accepted,
            "ratio": round(ratio, 3), "bar": ACCEPT_BAR,
            "passed": bool(proposals) and len(labelled) == len(proposals) and ratio >= ACCEPT_BAR}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.project_rules_proposer.run",
                                description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true",
                      help="replay cases.json, and call no model")
    mode.add_argument("--live", action="store_true",
                      help="ask the model through the Router for each --page")
    mode.add_argument("--score", type=Path, help="score a file of a live run that a lead marked")
    p.add_argument("--page", type=Path, action="append", default=[],
                   help="a file of the repo to read as one page (live)")
    p.add_argument("--member", default="", help="the lead who pays for the live calls")
    p.add_argument("--budget-usd", type=float, default=BUDGET_USD)
    p.add_argument("--owner-approved", action="store_true",
                   help="the owner agreed to a budget over USD 0.50")
    p.add_argument("--out", type=Path, help="the file of a live run")
    return p


def _live(args: argparse.Namespace) -> int:
    if args.budget_usd > BUDGET_USD and not args.owner_approved:
        print("A budget over USD 0.50 needs the owner's OK (--owner-approved).", file=sys.stderr)
        return EXIT_NO_GO
    if not args.page or "@" not in args.member or args.out is None:
        print("A live run needs --page, --member and --out.", file=sys.stderr)
        return EXIT_NO_GO
    report = asyncio.run(run_live(list(args.page), args.member, args.budget_usd))
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps({k: v for k, v in report.items() if k != "pages"}) + "\n")
    return EXIT_NO_GO if report["stopped"] == "the Router is off" else EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.live:
        return _live(args)
    if args.score is not None:
        result = score_labels(json.loads(args.score.read_text(encoding="utf-8")))
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return EXIT_PASS if result["passed"] else EXIT_FAIL
    report, passed = scripted_report(load_cases())
    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    return EXIT_PASS if passed else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
