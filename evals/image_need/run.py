"""The image-need eval of the one assistant: ``python -m evals.image_need.run`` (WS-VOX VOX-5).

Spec: ``project-docs/specs/projects_ai_chat.md`` §31.5 VOX-5. The question: with
an image attached, does the one assistant call ``analyze_image`` only when the
answer needs what the image shows? The README of this folder says how to run it.

Two modes.

``--scripted`` calls no model and no network, and CI runs it. A scripted model
gives the right answer for each case, through the real ``orchestrator`` run and
the same scorer as the live run. It also checks the request: the tool and its rule are in the
tool list, and the upload note is in the member's
message. A scripted run proves the path, never the choice.

``--live`` asks the real model through the Router (``--tier``, default the
chat tier). It is manual. ``--budget-usd`` caps the spend, and the run stops
before a call would pass it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.image_need import dataset as ds
from evals.image_need import harness as hs
from evals.image_need.dataset import CALL, Case
from evals.routing import shell
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RunRecord,
    text_turn,
    tool_turn,
)

HERE = Path(__file__).resolve().parent
EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

#: The default Router tier of the chat.
DEFAULT_TIER = "tier-balanced"
#: The default cap of a live run, in USD.
DEFAULT_BUDGET_USD = 0.50
#: The cost guess for the first live case, before any case gave a real cost.
FIRST_CASE_GUESS_USD = 0.05
#: A fixed thread id, so the upload path is the same in each run.
THREAD = "11111111-1111-4111-8111-111111111111"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ── 1. The scripted run ─────────────────────────────────────────────────────


def script_for(case: Case) -> dict[str, ReplyModel]:
    """The known-good model of *case*: it calls the tool, or it does not."""
    if case.expected == CALL:
        return {ORCHESTRATOR: ReplyModel([
            tool_turn(hs.TOOL, {"file_name": case.file_name, "question": case.words}),
            text_turn("Here is what the image shows."),
        ])}
    return {ORCHESTRATOR: ReplyModel([text_turn("Done. The file moved by its name.")])}


@dataclass
class Result:
    case: Case
    record: RunRecord | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def request_problems(case: Case, model: ReplyModel, *, image_on: bool = True) -> list[str]:
    """What the first request must hold: the tool, the rule, the note, the words."""
    if not model.bodies:
        return [f"{case.id}: the model got no request"]
    first = model.bodies[0]
    problems: list[str] = []
    tools = hs.tool_names(first)
    if image_on and hs.TOOL not in tools:
        problems.append(f"{case.id}: the first request does not offer {hs.TOOL}")
    if not image_on and hs.TOOL in tools:
        problems.append(f"{case.id}: the first request offers {hs.TOOL} with the flag off")
    from acb_skills.addendum import IMAGE_CALL_RULE

    # The one assistant reads no addendum for this tool. The rule reaches the
    # model in the description of the tool, which is the docstring (§31.5).
    described = " ".join(
        str((t.get("function") or t).get("description") or "")
        for t in first.get("tools") or []
        if (t.get("function") or t).get("name") == hs.TOOL)
    if image_on and _flat(IMAGE_CALL_RULE) not in _flat(described):
        problems.append(f"{case.id}: the tool description does not hold the image call rule")
    text = _flat(" ".join(
        str(m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
        for m in first.get("messages") or []))
    if case.words not in text:
        problems.append(f"{case.id}: the model did not get the member's words")
    if f"Uploaded 1 file(s): {case.file_name}" not in text:
        problems.append(f"{case.id}: the model did not get the upload note")
    return problems


def check_scripted(case: Case, record: RunRecord, model: ReplyModel,
                   harness: hs.ImageHarness, sent_before: int) -> list[str]:
    problems = hs.score(case, hs.calls_of(record))
    # The eval always needs the tool on. A harness with the flag off must fail
    # the run, and not pass a case that names a tool the model never had.
    problems += request_problems(case, model)
    if len(harness.sent) != sent_before:
        problems.append(f"{case.id}: the scripted run sent {harness.sent[sent_before:]}")
    if record.error:
        problems.append(f"{case.id}: the run failed: {record.error}")
    return problems


async def _drive(harness: hs.ImageHarness, case: Case, tier: str | None = None,
                 timeout: float = 120.0) -> RunRecord:
    return await harness.drive(
        hs.message_for(case, THREAD), shell.system_context(None, guide=harness.guide),
        member=ds.MEMBER, org=ds.ORG, tier=tier, thread_id=THREAD, timeout=timeout,
        typed=case.words,
    )


async def _run_cases(harness: hs.ImageHarness, cases: list[Case],
                     scripts: Callable[[Case], dict[str, ReplyModel]]) -> list[Result]:
    results: list[Result] = []
    for case in cases:
        models = scripts(case)
        harness.models = models
        sent = len(harness.sent)
        record = await _drive(harness, case)
        results.append(Result(case, record,
                              check_scripted(case, record, models[ORCHESTRATOR], harness, sent)))
    return results


def dataset_problems(cases: Iterable[Case]) -> list[str]:
    """The set must hold both groups, with unique ids, and the port must hold."""
    cases = list(cases)
    problems = hs.drift()
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        problems.append("the case ids are not unique")
    for group in (ds.CALL, ds.NO_CALL):
        if sum(c.expected == group for c in cases) < 6:
            problems.append(f"the set holds fewer than 6 {group} cases")
    problems += [f"{c.id}: expected is {c.expected!r}" for c in cases
                 if c.expected not in (ds.CALL, ds.NO_CALL)]
    return problems


def run_scripted(
    cases: Iterable[Case] = ds.CASES,
    *,
    scripts: Callable[[Case], dict[str, ReplyModel]] = script_for,
    image_on: bool = True,
) -> tuple[list[str], list[Result]]:
    """The set problems of *cases*, and one scripted run of each case.

    *scripts* and *image_on* are parameters, so a test can hand in a wrong
    script or a harness with the flag off and see the run fail (R7)."""
    cases = list(cases)
    with hs.ImageHarness(image_on=image_on) as harness:
        results = asyncio.run(_run_cases(harness, cases, scripts))
    return dataset_problems(cases), results


def _print_scripted(problems: list[str], results: list[Result]) -> None:
    print(f"set: {len(results)} cases, {len(problems)} problems")
    for p in problems:
        print(f"  FAIL {p}")
    for r in results:
        print(f"  {'pass' if r.passed else 'FAIL'}  {r.case.id:<3} {r.case.expected:<8} {r.case.words}")
        for p in r.problems:
            print(f"        {p}")


# ── 2. The live run ─────────────────────────────────────────────────────────


def _case_cost(harness: hs.ImageHarness, mark: int) -> tuple[float, int, int]:
    """The USD, prompt tokens and completion tokens of the calls since *mark*."""
    from evals.one_assistant_measure.run import call_row

    rows = [call_row(c) for c in harness.model_calls[mark:]]
    return (sum(r["cost_usd"] for r in rows), sum(r["prompt"] for r in rows),
            sum(r["completion"] for r in rows))


async def _live_cases(harness: hs.ImageHarness, cases: list[Case], tier: str,
                      budget: float) -> tuple[list[dict[str, Any]], bool]:
    """Run *cases* live. The bool is True when the budget stopped the run."""
    rows: list[dict[str, Any]] = []
    spent, worst = 0.0, FIRST_CASE_GUESS_USD
    for case in cases:
        if spent + worst > budget:
            return rows, True
        mark = len(harness.model_calls)
        record = await _drive(harness, case, tier=tier, timeout=300.0)
        usd, prompt, completion = _case_cost(harness, mark)
        spent += usd
        worst = max(worst, usd)
        calls = hs.calls_of(record)
        problems = hs.score(case, calls)
        rows.append({
            "id": case.id, "file_name": case.file_name, "words": case.words,
            "expected": case.expected, "calls": len(calls),
            "file_named": [c.get("file_name") for c in calls],
            "passed": not problems and not record.error, "problems": problems,
            "cost_usd": round(usd, 6), "prompt_tokens": prompt,
            "completion_tokens": completion, "answer": record.answer[:600],
            "error": record.error,
        })
    return rows, False


def summarise(rows: list[dict[str, Any]], tier: str, budget: float, stopped: bool) -> dict[str, Any]:
    by: dict[str, Counter[str]] = {}
    for r in rows:
        c = by.setdefault(r["expected"], Counter())
        c["runs"] += 1
        c["passed"] += int(r["passed"])
    passed = sum(int(r["passed"]) for r in rows)
    return {
        "date": datetime.now(UTC).date().isoformat(), "sha": _sha(), "tier": tier,
        "accuracy": round(passed / len(rows), 3) if rows else None,
        "passed": passed, "runs": len(rows),
        "by_expected": {k: {"passed": v["passed"], "runs": v["runs"]} for k, v in sorted(by.items())},
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "budget_usd": budget, "stopped_by_budget": stopped, "cases": rows,
    }


def _sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=shell.REPO_ROOT, check=False).stdout.strip()
    except OSError:
        return ""


def _print_live(summary: dict[str, Any]) -> None:
    print(f"{'case':<5}{'expected':<10}{'calls':<7}{'USD':<10}result")
    for r in summary["cases"]:
        print(f"{r['id']:<5}{r['expected']:<10}{r['calls']:<7}{r['cost_usd']:<10.4f}"
              f"{'pass' if r['passed'] else 'FAIL'}")
        for p in r["problems"]:
            print(f"      {p}")
    for key, v in summary["by_expected"].items():
        print(f"  {key:<10}{v['passed']}/{v['runs']}")
    print(f"accuracy {summary['passed']}/{summary['runs']} = {summary['accuracy']} on "
          f"{summary['tier']}, {summary['date']}, {summary['sha']}")
    print(f"cost USD {summary['cost_usd']:.4f} of the budget USD {summary['budget_usd']:.2f}")
    if summary["stopped_by_budget"]:
        print("STOPPED: the next case could pass the budget, so the run did not start it.")


def _router_gate(tier: str) -> tuple[bool, str]:
    from acb_common import get_settings

    from evals.coding_engine import preflight

    settings = get_settings()
    check = preflight.router(settings.litellm_base_url, settings.llm_api_key, tier, ds.MEMBER)
    return check.ok, check.reason


# ── 3. The command line ─────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true", help="no model, no network (CI)")
    mode.add_argument("--live", action="store_true", help="the real model, on a local stack")
    parser.add_argument("--cases", default="", help="a comma list of case ids (default: all)")
    parser.add_argument("--tier", default=DEFAULT_TIER, help="live: the Router tier")
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
                        help="live: the spend cap in USD")
    parser.add_argument("--out", default=str(HERE / "baseline.json"),
                        help="live: the baseline JSON to write")
    args = parser.parse_args(argv)

    cases = list(ds.CASES)
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
        cases = [c for c in cases if c.id in wanted]

    if args.scripted:
        problems, results = run_scripted(cases)
        _print_scripted(problems, results)
        failed = bool(problems) or any(not r.passed for r in results)
        print("FAIL" if failed else "PASS")
        return EXIT_FAIL if failed else EXIT_PASS

    ok, reason = _router_gate(args.tier)
    print(reason)
    if not ok:
        return EXIT_NO_GO
    with hs.ImageHarness(live=True) as harness:
        rows, stopped = asyncio.run(_live_cases(harness, cases, args.tier, args.budget_usd))
    summary = summarise(rows, args.tier, args.budget_usd, stopped)
    Path(args.out).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    _print_live(summary)
    print(f"baseline: {args.out}")
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
