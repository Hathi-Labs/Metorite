"""The routing eval of the one assistant: ``python -m evals.routing.run`` (NS-6 slice 6d).

Spec: ``project-docs/specs/navigation_shell.md`` §7.1 and slice 6d of NS-6.
Decision D99: the member always talks to ``orchestrator``, and it delegates
to the specialist of each app. The README of this folder says how to run it.

Two modes.

``--scripted`` calls no model and no network, and CI runs it. It checks three
things against REAL code:

1. **The catalog** (:func:`catalog_problems`), for every case. Each expected
   agent is in the real registry, has an in-repo factory, and is a tool of
   the real orchestrator agent. Each app is a live pane of ``nav.ts``. Each
   specialist the orchestrator holds, and each live pane whose agent it
   holds, has a case. The port of ``surfaceContext`` matches ``assistant.ts``.
2. **The delegation** (:func:`run_scripted`), for one case of each expected
   answer. A scripted model makes the orchestrator call the expected agent,
   through the real ``run_agent_stream``. The run must reach that agent's
   real factory and model with the member's words, and no other agent. A case
   with no specialist must delegate to nobody, and an ``asks`` case must show
   a question card. The model must see the page context.
3. **The tools offered**. The first request of each run must offer
   ``call_agent``, ``ask_questions`` and the expected specialist's tool.

A scripted run proves the plumbing, never the choice: the script makes the
choice. The ``--live`` run measures the choice.

``--live`` is a MANUAL run on a local stack. The orchestrator asks the real
model through the Router, each case once (``--repeat``), and no specialist
runs: a stand-in records the delegate and answers with a fixed line. It
prints an accuracy table and writes the baseline JSON (``--out``).

Exit codes: 0 pass (or a live run that finished), 1 a check failed,
2 NO-GO (the live run cannot start).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.routing import dataset as ds
from evals.routing import shell
from evals.routing.dataset import Case
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    RunRecord,
    orchestrator_tool_names,
    registry,
    specialist_tool,
    text_turn,
    tool_turn,
)

HERE = Path(__file__).resolve().parent
EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

#: The least number of cases the set holds (slice 6d, the narrowed slice).
MIN_CASES = 24

#: The tools every run of the one assistant must offer the model.
REQUIRED_TOOLS = frozenset({"call_agent", "ask_questions"})


# ── 1. The catalog ──────────────────────────────────────────────────────────


def catalog_problems(cases: Iterable[Case]) -> list[str]:
    """Each way the set disagrees with the real catalog. Empty is a pass.

    It reads the registry and builds the real orchestrator agent, so call it
    inside a :class:`RoutingHarness` (the dynamic registry answers empty).
    """
    cases = list(cases)
    reg = registry()
    tools = orchestrator_tool_names()
    out: list[str] = []
    if len(cases) < MIN_CASES:
        out.append(f"the set holds {len(cases)} cases, and it needs {MIN_CASES} at least")
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            out.append(f"{c.id}: the id is used twice")
        seen.add(c.id)
        out.extend(_case_problems(c, reg, tools))
    out.extend(_coverage_problems(cases, reg, tools))
    out.extend(shell.drift())
    return out


def _case_problems(c: Case, reg: dict[str, dict[str, Any]], tools: frozenset[str]) -> list[str]:
    """The problems of one case: its words, its app and its agent."""
    out: list[str] = []
    if not c.words.strip():
        out.append(f"{c.id}: no words")
    if c.asks and c.expected is not None:
        out.append(f"{c.id}: a case that asks first names no specialist")
    if c.opens is not None:
        # WS-62 CF-3: the path must pass the server's own check, and the
        # orchestrator opens it itself.
        from orchestrator.in_app_path import is_in_app_path, pane_of

        if not is_in_app_path(c.opens) or pane_of(c.opens) is None:
            out.append(f"{c.id}: {c.opens!r} is not a page of this app")
        if c.expected is not None or c.asks:
            out.append(f"{c.id}: a case that opens a page names no specialist and asks nothing")
        if "show_in_app" not in tools:
            out.append(f"{c.id}: the orchestrator holds no tool 'show_in_app'")
    if c.app is not None and c.app not in shell.live_panes():
        where = "a preview pane" if c.app in {p.href for p in shell.panes()} else "no pane of nav.ts"
        out.append(f"{c.id}: the app {c.app!r} is {where}, not a live pane")
    if c.expected is None:
        return out
    entry = reg.get(c.expected)
    if entry is None:
        return [*out, f"{c.id}: {c.expected!r} is not an agent of the registry"]
    if c.expected == ORCHESTRATOR:
        return [*out, f"{c.id}: the orchestrator cannot be its own specialist"]
    path = shell.REPO_ROOT / str(entry.get("local_path") or "-")
    if not (path / "agents.py").is_file() or not (path / "config.json").is_file():
        out.append(f"{c.id}: {c.expected!r} has no in-repo factory at {path}")
    if specialist_tool(c.expected) not in tools:
        out.append(f"{c.id}: the orchestrator holds no tool {specialist_tool(c.expected)!r}")
    return out


def _coverage_problems(cases: list[Case], reg: dict[str, dict[str, Any]],
                       tools: frozenset[str]) -> list[str]:
    """Each specialist, live app and ``ALSO_SERVES`` pair with no case."""
    out: list[str] = []
    asked_for = {c.expected for c in cases if c.expected}
    for name in sorted(reg):
        if name != ORCHESTRATOR and specialist_tool(name) in tools and name not in asked_for:
            out.append(f"the orchestrator holds {name!r}, and no case expects it")
    pairs = {(c.app, c.expected) for c in cases}
    # A pane agent that the orchestrator does not hold as a tool is no
    # specialist: a Copilot agent runs only as its own app's agent (D84, D92,
    # ``loader.refuse_copilot_specialist``). No case can expect it.
    for pane in shell.live_panes().values():
        if (pane.agent and specialist_tool(pane.agent) in tools
                and (pane.href, pane.agent) not in pairs):
            out.append(f"the live app {pane.href} names {pane.agent!r}, and no case asks it there")
    for agent, hrefs in shell.also_serves().items():
        if specialist_tool(agent) not in tools:
            out.append(f"ALSO_SERVES names {agent!r}, and the orchestrator holds no tool for it")
            continue
        out.extend(f"ALSO_SERVES sends {href} to {agent!r}, and no case asks it there"
                   for href in hrefs if (href, agent) not in pairs)
    if not any(c.expected is None and not c.asks and c.opens is None for c in cases):
        out.append("no case expects the orchestrator to answer itself")
    if not any(c.opens for c in cases):
        out.append("no case expects the orchestrator to open a page (show_in_app)")
    if not any(c.asks for c in cases):
        out.append("no case expects the orchestrator to ask first")
    return out


def pane_of(agent: str | None) -> str | None:
    """The live pane whose ``agent`` is *agent*: where the page moves."""
    if agent is None:
        return None
    for pane in shell.live_panes().values():
        if pane.agent == agent:
            return pane.href
    return None


# ── 2. The scripted delegation ──────────────────────────────────────────────


def runtime_of(agent: str) -> str:
    return str(registry().get(agent, {}).get("agent_runtime") or "maf")


def drivable(case: Case) -> bool:
    """A script can drive the case: no specialist, or a MAF one.

    A Copilot SDK specialist (``app-builder``, the one that
    ``loader.COPILOT_SPECIALIST_ALLOWED`` keeps) runs its sub-run in the
    Copilot CLI, a process a hermetic run cannot start. Its case gets the
    catalog checks only. Every other Copilot agent is no specialist.
    """
    return case.expected is None or runtime_of(case.expected) == "maf"


def representative(cases: Iterable[Case]) -> list[Case]:
    """The first drivable case of each expected answer, in set order."""
    out: list[Case] = []
    keys: set[tuple[str | None, bool, bool]] = set()
    for c in cases:
        key = (c.expected, c.asks, c.opens is not None)
        if key not in keys and drivable(c):
            keys.add(key)
            out.append(c)
    return out


def script_for(case: Case, form: str) -> dict[str, ReplyModel]:
    """The known-good models of *case*: the orchestrator's, and the specialist's.

    *form* is ``call_agent`` or ``tool`` (the specialist tool of
    ``orchestrator/agents.py``). Both reach ``_run_sub_agent_streaming``.
    """
    if case.asks:
        question = {"questions": [{"header": "Which one?",
                                   "question": f"Which one do you mean by: {case.words}"}]}
        return {ORCHESTRATOR: ReplyModel([tool_turn("ask_questions",
                                                    {"questions": json.dumps(question)}),
                                          text_turn("Tell me which one, and I will do it.")])}
    if case.opens is not None:
        # WS-62 CF-3: the orchestrator opens the page, then says what the
        # member will see, in one sentence.
        return {ORCHESTRATOR: ReplyModel([
            tool_turn("show_in_app", {"path": case.opens, "why": f"Here it is: {case.words}"}),
            text_turn("You see it now, on the right."),
        ])}
    if case.expected is None:
        return {ORCHESTRATOR: ReplyModel([text_turn("I can help with your apps.")])}
    agent = case.expected
    call = (tool_turn("call_agent", {"agent_name": agent, "message": case.words})
            if form == "call_agent" else
            tool_turn(specialist_tool(agent), {"task": case.words}))
    return {
        ORCHESTRATOR: ReplyModel([call, text_turn(f"{agent} has it.")]),
        agent: ReplyModel([text_turn(f"{agent} handled: {case.words}")]),
    }


@dataclass
class Result:
    case: Case
    form: str
    record: RunRecord | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def check_scripted(case: Case, record: RunRecord, models: dict[str, ReplyModel],
                   harness: RoutingHarness, loaded_before: int, sent_before: int) -> list[str]:
    """Each way the run differs from what *case* expects."""
    out: list[str] = []
    if record.error:
        out.append(f"the run failed: {record.error}")
    orch = models[ORCHESTRATOR]
    if not orch.bodies:
        return [*out, "the orchestrator's model got no request"]
    out.extend(_offer_problems(case, orch))
    out.extend(_delegate_problems(case, record, models, harness.loaded[loaded_before:]))
    out.extend(_open_problems(case, record))
    if case.asks and not any(a.get("answered") for a in record.asked):
        out.append("expected a question card for the member, and none was answered")
    if not case.asks and record.asked:
        out.append(f"expected no question, and it asked {len(record.asked)}")
    sent = harness.sent[sent_before:]
    if sent:
        out.append(f"the run reached the network: {sent}")
    return out


def _offer_problems(case: Case, orch: ReplyModel) -> list[str]:
    """The orchestrator's first request: the tools, the persona, the context, the words."""
    out: list[str] = []
    offered = {t["function"]["name"] for t in orch.bodies[0].get("tools") or []}
    want = set(REQUIRED_TOOLS)
    if case.expected:
        want.add(specialist_tool(case.expected))
    if case.opens is not None:
        want.add("show_in_app")
    missing = sorted(want - offered)
    if missing:
        out.append(f"the orchestrator was not offered {', '.join(missing)}")
    context = shell.surface_context(case.app, case.facts)
    for what, text in (("the persona", shell.persona()[:80]),
                       ("the page context", context.splitlines()[1]),
                       ("the member's words", case.words)):
        if not orch.saw(text):
            out.append(f"the orchestrator's model did not see {what}")
    return out


def _open_problems(case: Case, record: RunRecord) -> list[str]:
    """The ``navigate`` events of the run, against ``case.opens`` (WS-62 CF-3).

    A case that opens a page must send exactly ONE ``CUSTOM`` event
    ``navigate`` with that path, in the parent's own stream. Every other case
    must send none: a page that moves under a member who asked a question
    is the failure §4.4 names.
    """
    paths = [str((e.get("value") or {}).get("path"))
             for e in record.events
             if e.get("type") == "CUSTOM" and e.get("name") == "navigate"]
    if case.opens is None:
        return [f"expected no page to open, and it opened {paths}"] if paths else []
    if paths != [case.opens]:
        return [f"expected one navigate event for {case.opens!r}, and it sent {paths}"]
    return []


def _delegate_problems(case: Case, record: RunRecord, models: dict[str, ReplyModel],
                       loaded: list[str]) -> list[str]:
    """Where the run delegated, against what *case* expects."""
    if case.expected is None:
        if record.routed or record.reached:
            return [f"expected no delegate, and it routed {record.routed} "
                    f"and reached {record.reached}"]
        return []
    agent = case.expected
    out: list[str] = []
    if record.routed != [agent]:
        out.append(f"expected a call of {agent!r}, and it routed {record.routed}")
    if record.reached != [agent]:
        out.append(f"expected a sub-run of {agent!r}, and it reached {record.reached}")
    if agent not in loaded:
        out.append(f"the loader never built {agent!r} (it built {loaded})")
    sub = models.get(agent)
    if sub is None or not sub.saw(case.words):
        out.append(f"{agent!r}'s model never got the member's words")
    return out


async def _run_cases(harness: RoutingHarness, chosen: list[Case],
                     scripts: Callable[[Case, str], dict[str, ReplyModel]]) -> list[Result]:
    results: list[Result] = []
    for i, case in enumerate(chosen):
        form = "call_agent" if i % 2 == 0 else "tool"
        models = scripts(case, form)
        harness.models = models
        loaded, sent = len(harness.loaded), len(harness.sent)
        record = await harness.drive(
            case.words, shell.system_context(case.app, case.facts),
            member=ds.MEMBER, org=ds.ORG,
        )
        results.append(Result(case, form, record,
                              check_scripted(case, record, models, harness, loaded, sent)))
    return results


def run_scripted(
    cases: Iterable[Case] = ds.CASES,
    *,
    only: Iterable[str] = (),
    every: bool = False,
    scripts: Callable[[Case, str], dict[str, ReplyModel]] = script_for,
) -> tuple[list[str], list[Result]]:
    """The catalog problems of *cases*, and the scripted runs.

    It drives one case of each expected answer (:func:`representative`),
    every drivable case with *every*, or only the case ids of *only*.
    *scripts* is a parameter, so a test can hand in a wrong script and see
    the run fail (R7)."""
    cases = list(cases)
    wanted = set(only)
    with RoutingHarness() as harness:
        problems = catalog_problems(cases)
        if wanted:
            chosen = [c for c in cases if c.id in wanted and drivable(c)]
        elif every:
            chosen = [c for c in cases if drivable(c)]
        else:
            chosen = representative(cases)
        results = asyncio.run(_run_cases(harness, chosen, scripts))
    return problems, results


def _print_scripted(problems: list[str], results: list[Result], cases: list[Case]) -> None:
    print(f"catalog: {len(cases)} cases, {len(problems)} problems")
    for p in problems:
        print(f"  FAIL {p}")
    driven = {r.case.id for r in results}
    print("delegation, through the real orchestrator run:")
    for r in results:
        status = "pass" if r.passed else "FAIL"
        got = ", ".join(r.record.routed) if r.record and r.record.routed else "-"
        want = r.case.expected or ("ask" if r.case.asks else
                                   f"opens {r.case.opens}" if r.case.opens else "itself")
        print(f"  {status}  {r.case.id:<3} {r.form:<10} expected {want:<20} routed {got}")
        for p in r.problems:
            print(f"        {p}")
    rest = [c for c in cases if c.id not in driven]
    static = [c.id for c in rest if not drivable(c)]
    if static:
        print(f"catalog only (a Copilot SDK specialist): {', '.join(static)}")
    no_pane = sorted({c.expected for c in cases if c.expected and pane_of(c.expected) is None})
    if no_pane:
        print(f"note: no live pane names {', '.join(no_pane)}, so the page does not move for them")


# ── 3. The live run ─────────────────────────────────────────────────────────


def first_routed(record: RunRecord, stand_ins: list[tuple[str, str]]) -> str | None:
    """The first agent the run delegated to, or None."""
    if record.routed:
        return record.routed[0]
    return stand_ins[0][0] if stand_ins else None


def score_live(case: Case, got: str | None, asked: bool) -> bool:
    if case.asks:
        return got is None and asked
    return got == case.expected


async def _live_cases(harness: RoutingHarness, cases: list[Case], tier: str,
                      repeat: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        for n in range(repeat):
            mark = len(harness.delegated)
            record = await harness.drive(
                case.words, shell.system_context(case.app, case.facts),
                member=ds.MEMBER, org=ds.ORG, tier=tier, timeout=300.0,
            )
            stand_ins = harness.delegated[mark:]
            got = first_routed(record, stand_ins)
            asked = bool(record.asked) or any(n == "ask_questions" for n, _ in record.tool_calls)
            rows.append({
                "id": case.id, "run": n + 1, "app": case.app, "words": case.words,
                "expected": case.expected, "asks": case.asks, "got": got,
                "routed": record.routed, "asked": asked,
                "passed": score_live(case, got, asked),
                "tool_calls": [name for name, _ in record.tool_calls],
                "answer": record.answer[:1500], "error": record.error,
            })
    return rows


def summarise(rows: list[dict[str, Any]], tier: str) -> dict[str, Any]:
    by: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        key = r["expected"] or ("(ask)" if r["asks"] else "(itself)")
        by[key]["runs"] += 1
        by[key]["passed"] += int(r["passed"])
    total = len(rows)
    passed = sum(int(r["passed"]) for r in rows)
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "sha": _sha(),
        "tier": tier,
        "accuracy": round(passed / total, 3) if total else None,
        "passed": passed,
        "runs": total,
        "by_expected": {k: {"passed": v["passed"], "runs": v["runs"]} for k, v in sorted(by.items())},
        "cases": rows,
    }


def _sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=shell.REPO_ROOT, check=False).stdout.strip()
    except OSError:
        return ""


def _print_live(summary: dict[str, Any]) -> None:
    print(f"{'case':<5}{'app':<14}{'expected':<22}{'got':<22}result")
    for r in summary["cases"]:
        want = r["expected"] or ("(ask)" if r["asks"] else "(itself)")
        got = r["got"] or ("(ask)" if r["asked"] else "(itself)")
        print(f"{r['id']:<5}{(r['app'] or 'My Day'):<14}{want:<22}{got:<22}"
              f"{'pass' if r['passed'] else 'FAIL'}")
    print()
    for key, v in summary["by_expected"].items():
        print(f"  {key:<22}{v['passed']}/{v['runs']}")
    print(f"accuracy {summary['passed']}/{summary['runs']} = {summary['accuracy']} "
          f"on {summary['tier']}, {summary['date']}, {summary['sha']}")


def _router_gate(tier: str) -> tuple[bool, str]:
    from acb_common import get_settings

    from evals.coding_engine import preflight

    settings = get_settings()
    check = preflight.router(settings.litellm_base_url, settings.llm_api_key, tier, ds.MEMBER)
    return check.ok, check.reason


# ── the command line ────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true", help="no model, no network (CI)")
    mode.add_argument("--live", action="store_true", help="the real model, on a local stack")
    parser.add_argument("--all", action="store_true",
                        help="scripted: drive every drivable case, not one of each answer")
    parser.add_argument("--cases", default="", help="a comma list of case ids (default: all)")
    parser.add_argument("--tier", default="tier-balanced", help="live: the Router tier")
    parser.add_argument("--repeat", type=int, default=1, help="live: runs of each case")
    parser.add_argument("--out", default=str(HERE / "baseline.json"),
                        help="live: the baseline JSON to write")
    args = parser.parse_args(argv)

    cases = list(ds.CASES)
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
        cases = [c for c in cases if c.id in wanted]

    if args.scripted:
        problems, results = run_scripted(ds.CASES, only=[c.id for c in cases] if args.cases else (),
                                         every=args.all)
        _print_scripted(problems, results, list(ds.CASES))
        failed = bool(problems) or any(not r.passed for r in results)
        print("FAIL" if failed else "PASS")
        return EXIT_FAIL if failed else EXIT_PASS

    ok, reason = _router_gate(args.tier)
    print(reason)
    if not ok:
        return EXIT_NO_GO
    with RoutingHarness(live=True) as harness:
        rows = asyncio.run(_live_cases(harness, cases, args.tier, max(1, args.repeat)))
    summary = summarise(rows, args.tier)
    Path(args.out).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    _print_live(summary)
    print(f"baseline: {args.out}")
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
