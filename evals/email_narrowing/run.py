"""The runner of the email narrowing eval: ``python -m evals.email_narrowing.run`` (WS-48 N2).

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2 and §9 N2. The
README of this folder says what each number is, and which numbers a stub
gives.

Each question runs two ways, on the same synthetic mailbox:

* **before**: today's email-assistant, with no ``narrow_and_read``. It lists
  the mail with ``query_inbox`` and reads it with ``read_email``.
* **after**: the same agent with ``NARROWING_AGENTS=email-assistant``. It
  calls ``narrow_and_read`` once.

Both paths call the REAL tools of ``apps/agents/agent-email-assistant``
against the gateway stub, and the after path runs the REAL pipeline of
``acb_skills.narrowing`` with the REAL decide facade and Console client.

**The pass rule** (§7.2, the brief of N2):

1. RECALL. For each gated question (Q1 to Q4), the after path reads in full
   every answering message. So no answering message is dropped, and no
   answering message stays unread.
2. COST. The after path's credits on the gated questions are at most
   :data:`COST_BAR` (40 percent) of the before path's credits.

Q5 is the expected miss (spec Q3). The search is lexical, and two of its
answers share no word with the search. Its recall is reported and not gated.

Modes:

* ``--scripted`` (CI): the stub door answers PICK, and nothing reaches a
  model or a Router. Every token and credit number is an estimate.
* ``--compare`` (a dev box): PICK goes to the REAL decide door of the
  Console that ``CUSTOMER_CONSOLE_URL`` names. That address must be on this
  machine. The verdicts, and so the recall, are real. The model turns are
  still played from the sequences, so their tokens are still estimates. The
  run lists each decide ``request_id``, to join to ``usage_event``.

Exit codes: 0 pass, 1 fail, 2 NO-GO (``--compare`` with no local door).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from evals.email_narrowing import dataset as ds_mod
from evals.email_narrowing import scripted, stub_api
from evals.email_narrowing.dataset import Dataset, Question
from evals.email_narrowing.stub_api import StubDoor, StubRouter

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "apps" / "agents" / "agent-email-assistant"
AGENT = "email-assistant"
RUN_ID = "run-email-narrowing-eval"
THREAD = "thread-email-narrowing-eval"

#: The cost bar of the pass rule: after <= 40 percent of before (§7.2).
COST_BAR = Decimal("0.40")

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

#: Which numbers come from a stub in each mode. The summary carries it.
STUBBED = {
    "scripted": [
        "every token count (4 characters to a token, no tokenizer)",
        "every credit (the eval card of fixtures/rate_card.json, not the production card)",
        "every PICK verdict (the stub door answers from the fixture)",
        "the full-text match (stub_api.tokens approximates to_tsvector)",
        "the before path's reads per request (an assumption, READS_PER_TURN)",
    ],
    "compare": [
        "the tokens of the model turns (estimated; the decide tokens are measured)",
        "every credit (the eval card; join the request ids to usage_event for real credits)",
        "the full-text match (stub_api.tokens approximates to_tsvector)",
        "the before path's reads per request (an assumption, READS_PER_TURN)",
    ],
}


# ── The environment of one run ───────────────────────────────────────────────


@contextlib.contextmanager
def _env(values: dict[str, str | None]) -> Iterator[None]:
    from acb_common.settings import get_settings

    saved = {k: os.environ.get(k) for k in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def _load_agent() -> Any:
    name = f"email_narrowing_eval_agent_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / "agents.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _bound_run(org: str, member: str) -> Iterator[None]:
    """The run binding of one chat turn: the member, the org and the thread."""
    from acb_common import bind_run_context, clear_run_context
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills import narrowing
    from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    narrowing._DROPPED.clear()
    clear_run_context()
    bind_run_context(run_id=RUN_ID, thread_id=THREAD, agent=AGENT, user=member,
                     source="chat", app="email", member_verified=True)
    tenant = bind_tenant(org)
    user = _bind_memory_user_id(member)
    try:
        with artifact_context_scope():
            bind_artifact_context(session_id=THREAD, agent_name=AGENT, run_id=RUN_ID,
                                  member=member, no_egress=False)
            yield
    finally:
        _unbind_memory_user_id(user)
        release_tenant(tenant)
        clear_run_context()
        narrowing._DROPPED.clear()


@contextlib.contextmanager
def _patched(target: Any, name: str, value: Any) -> Iterator[None]:
    old = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, old)


async def _no_system_one(*_a: Any, **_k: Any) -> Any:
    """In a scripted run, a fallback must not reach a model. It keeps the
    batch as not checked, and the count line shows it."""
    raise RuntimeError("the scripted eval calls no model")


class _TapTransport(httpx.AsyncBaseTransport):
    """``--compare``: the real transport to the Console, with a record of
    each decide request's size and request id."""

    def __init__(self, router: StubRouter, seen: list[str]) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.router = router
        self.seen = seen

    async def handle_async_request(self, request: Any) -> Any:
        response = await self.inner.handle_async_request(request)
        await response.aread()
        try:
            body = json.loads(response.content)
        except ValueError:
            body = {}
        answers = body.get("answers") if isinstance(body, dict) else None
        if isinstance(body, dict) and isinstance(body.get("request_id"), str):
            self.seen.append(body["request_id"])
        self.router.add(stub_api.DECIDE_TIER, stub_api.estimate_tokens(len(request.content)),
                        stub_api.estimate_tokens(len(json.dumps(answers or {}))))
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


# ── One question ─────────────────────────────────────────────────────────────


@dataclass
class PathResult:
    router: StubRouter
    found: list[str]
    read: list[str]
    calls: list[dict[str, Any]]
    requests: list[stub_api.MailRequest]
    output: str = ""

    def table(self) -> dict[str, dict[str, int]]:
        return self.router.table()


def _schemas(agent: Any) -> str:
    tools = agent.default_options["tools"]
    return json.dumps([t.to_json_schema_spec() for t in tools])


async def _run_path(
    which: str, q: Question, ds: Dataset, running: stub_api.RunningStub, *,
    reads_per_turn: int, door: StubDoor | None, compare_seen: list[str] | None,
) -> PathResult:
    from acb_auth import console_resolve
    from acb_skills import system_one

    router = StubRouter()
    flag = AGENT if which == "after" else ""
    env = {"NARROWING_AGENTS": flag, "GATEWAY_URL": running.url,
           "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY") or "sk-email-narrowing-eval"}
    if door is not None:
        env.update({
            "DECIDE_ENABLED": "true",
            "CUSTOMER_CONSOLE_URL": "https://console.email-narrowing-eval.test",
            "CUSTOMER_CONSOLE_ORG_KEY": "cc_live_eval_notarealsecret",
            "CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY": "false",
        })
    start = len(running.stub.requests)
    with contextlib.ExitStack() as stack:
        stack.enter_context(_env(env))
        if door is not None:
            door.router = router
            stack.enter_context(_patched(console_resolve, "_new_http_client", door.client))
            stack.enter_context(_patched(system_one, "ask", _no_system_one))
        elif compare_seen is not None:
            def tapped(timeout: float = 10.0) -> Any:
                return httpx.AsyncClient(
                    timeout=timeout, transport=_TapTransport(router, compare_seen))

            stack.enter_context(_patched(console_resolve, "_new_http_client", tapped))
        agents = _load_agent()
        agent = agents.build_agents()[0]
        tools = {t.name: t.func for t in agent.default_options["tools"]}
        if which == "after" and "narrow_and_read" not in tools:
            raise RuntimeError("the after path built no narrow_and_read")
        if which == "before" and "narrow_and_read" in tools:
            raise RuntimeError("the before path built narrow_and_read")
        stack.enter_context(_bound_run(ds_mod.ORG, ds_mod.MEMBER))
        session = scripted.Session(
            system=agent.default_options["instructions"], schemas=_schemas(agent),
            prompt=q.spec.prompt, tools=tools, router=router,
        )
        output = ""
        if which == "before":
            read = await scripted.play_before(session, ds, q, reads_per_turn=reads_per_turn)
        else:
            read, output = await scripted.play_after(session, ds, q)
    requests = running.stub.requests[start:]
    if which == "before":
        found = next((r.ids for r in requests if r.path == "/email/messages"), [])
    else:
        found = next((r.ids for r in requests if r.path == "/email/search"), [])
    return PathResult(router, found, read, session.calls, requests, output)


def _no_body_on_the_wire(ds: Dataset, door: StubDoor | None) -> list[str]:
    """Each message whose body (past its snippet) reached a PICK request."""
    if door is None:
        return []
    text = " ".join(
        " ".join(str(v) for v in item.values())
        for body in door.bodies for item in body["state"]["items"].values()
    )
    flat = " ".join(text.split())
    bad = []
    for m in ds.messages + ds.stranger_messages:
        body = " ".join(m["body_text"].split())
        if len(body) > 420 and body[160:360] in flat:
            bad.append(m["key"])
    return bad


def _rules(ds: Dataset, results: dict[str, dict[str, PathResult]],
           door: StubDoor | None) -> dict[str, dict[str, Any]]:
    """The rules that bind every question: tenancy, the route, the wire."""
    every = [r for paths in results.values() for p in paths.values() for r in p.requests]
    stranger_ids = {m["id"] for m in ds.stranger_messages}
    members = sorted({r.member for r in every})
    leaked = sorted({i for r in every for i in r.ids if i in stranger_ids})
    unknown = sorted({f"{r.path}?{u}" for r in every for u in r.unknown})
    searches = [r for r in every if r.path == "/email/search"]
    not_light = [r.params for r in searches
                 if r.params.get("light") != ["true"] or r.params.get("hybrid") != ["true"]]
    over = []
    for qid, paths in results.items():
        after = paths["after"]
        reads = [r.path.rsplit("/", 1)[-1] for r in after.requests
                 if r.path.startswith("/email/messages/")]
        if len(reads) > 25 or not set(reads) <= set(after.found):
            over.append(qid)
    bodies = _no_body_on_the_wire(ds, door)
    return {
        "acts_as_the_member": {"pass": members == [ds_mod.MEMBER], "detail": members},
        "no_other_members_mail": {"pass": not leaked, "detail": leaked},
        "real_route_parameters": {"pass": not unknown, "detail": unknown},
        "narrow_is_light_and_hybrid": {"pass": bool(searches) and not not_light,
                                       "detail": not_light},
        "read_only_kept_within_cap": {"pass": not over, "detail": over},
        "no_full_body_on_pick_wire": {"pass": not bodies, "detail": bodies},
    }


def _recall(answering: list[str], got: list[str]) -> float:
    if not answering:
        return 1.0
    return round(len(set(answering) & set(got)) / len(answering), 4)


def _ratio(after: Decimal, before: Decimal) -> float | None:
    return round(float(after / before), 4) if before else None


def _break_even(
    before: Decimal, after_tables: list[dict[str, dict[str, int]]],
    card: dict[str, dict[str, str]], tier: str,
) -> float | None:
    """The factor on *tier*'s price at which the after path hits the bar."""
    def on(scale: Decimal) -> Decimal:
        scaled = {
            t: ({k: str(Decimal(v) * scale) for k, v in r.items()} if t == tier else r)
            for t, r in card.items()
        }
        return sum((stub_api.credits_of(t, scaled) for t in after_tables), Decimal(0))

    base, unit = on(Decimal(0)), on(Decimal(1)) - on(Decimal(0))
    if unit <= 0:
        return None
    return round(float((COST_BAR * before - base) / unit), 2)


# ── The sweep ────────────────────────────────────────────────────────────────


@dataclass
class Summary:
    mode: str
    passed: bool
    questions: list[dict[str, Any]] = field(default_factory=list)
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    totals: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "passed": self.passed, "totals": self.totals,
                "rules": self.rules, "questions": self.questions}


@dataclass
class Raw:
    """What one sweep saw, before any pricing (:func:`judge` prices it)."""

    mode: str
    reads_per_turn: int
    ds: Dataset
    results: dict[str, dict[str, PathResult]]
    best: dict[str, PathResult]
    rules: dict[str, dict[str, Any]]
    seen: list[str]


async def collect(
    *, mode: str = "scripted", reads_per_turn: int = scripted.READS_PER_TURN,
    ds: Dataset | None = None, only: tuple[str, ...] | None = None,
    door_override: Callable[[str, str], tuple[str, float] | None] | None = None,
) -> Raw:
    """Every question (or *only* those), both paths. ``door_override`` lets a
    test break PICK, and *only* lets a test run one question."""
    ds = ds or ds_mod.load()
    if only:
        ds.questions = [q for q in ds.questions if q.id in only]
    running = stub_api.serve(ds)
    seen: list[str] = []
    try:
        door = StubDoor(ds, StubRouter()) if mode == "scripted" else None
        if door is not None:
            door.override = door_override
        results: dict[str, dict[str, PathResult]] = {}
        best: dict[str, PathResult] = {}
        for q in ds.questions:
            results[q.id] = {}
            for which in ("before", "after"):
                results[q.id][which] = await _run_path(
                    which, q, ds, running, reads_per_turn=reads_per_turn,
                    door=door, compare_seen=None if door is not None else seen,
                )
            best[q.id] = await _run_path(
                "before", q, ds, running, reads_per_turn=0, door=door, compare_seen=None,
            )
        rules = _rules(ds, results, door)
    finally:
        running.close()
    return Raw(mode, reads_per_turn, ds, results, best, rules, seen)


async def sweep(
    *, mode: str = "scripted", reads_per_turn: int = scripted.READS_PER_TURN,
    card: dict[str, dict[str, str]] | None = None, ds: Dataset | None = None,
    only: tuple[str, ...] | None = None,
    door_override: Callable[[str, str], tuple[str, float] | None] | None = None,
) -> Summary:
    raw = await collect(mode=mode, reads_per_turn=reads_per_turn, ds=ds, only=only,
                        door_override=door_override)
    return judge(raw, card)


def judge(raw: Raw, card: dict[str, dict[str, str]] | None = None) -> Summary:
    """The pass rule over what a sweep saw, priced with *card*. Pure."""
    card = card or stub_api.load_card()
    ds, results, best, rules, seen = raw.ds, raw.results, raw.best, raw.rules, raw.seen
    mode, reads_per_turn = raw.mode, raw.reads_per_turn
    questions: list[dict[str, Any]] = []
    gated_before = gated_after = gated_best = Decimal(0)
    recall_ok = True
    for q in ds.questions:
        before, after = results[q.id]["before"], results[q.id]["after"]
        b_cr, a_cr, best_cr = (before.router.credits(card), after.router.credits(card),
                               best[q.id].router.credits(card))
        answering = list(q.answering)
        found_answering = [i for i in answering if i in after.found]
        e2e = _recall(answering, after.read)
        pick = _recall(found_answering, after.read)
        status = "pass"
        if q.spec.gated:
            gated_before += b_cr
            gated_after += a_cr
            gated_best += best_cr
            if e2e < 1.0:
                recall_ok = False
                status = "fail"
        else:
            status = "xfail" if e2e < 1.0 else "xpass"
        questions.append({
            "id": q.id, "prompt": q.spec.prompt, "gated": q.spec.gated, "status": status,
            "note": q.spec.note,
            "answering": len(answering),
            "before": {"listed": len(before.found), "read": len(before.read),
                       "recall": _recall(answering, before.read),
                       "tiers": before.table(), "credits": str(b_cr)},
            "before_best_case": {"tiers": best[q.id].table(), "credits": str(best_cr)},
            "after": {"found": len(after.found), "read": len(after.read),
                      "narrow_recall": _recall(answering, after.found),
                      "pick_recall": pick, "recall": e2e,
                      "count_line": after.output.splitlines()[0] if after.output else "",
                      "tiers": after.table(), "credits": str(a_cr)},
            "ratio": _ratio(a_cr, b_cr),
            "ratio_best_case": _ratio(a_cr, best_cr),
        })
    ratio = _ratio(gated_after, gated_before)
    cost_ok = ratio is not None and Decimal(str(ratio)) <= COST_BAR
    rules_ok = all(r["pass"] for r in rules.values())
    gated_tables = [results[q.id]["after"].table() for q in ds.questions if q.spec.gated]
    bodies = [len(m["body_text"]) for m in ds.messages]
    totals = {
        "date": datetime.now(UTC).date().isoformat(),
        "sha": git_sha(),
        "reads_per_turn": reads_per_turn,
        "cost_bar": str(COST_BAR),
        "before_credits": str(gated_before),
        "after_credits": str(gated_after),
        "ratio": ratio,
        "ratio_best_case_for_today": _ratio(gated_after, gated_best),
        "cost_pass": cost_ok,
        "recall_pass": recall_ok,
        "rules_pass": rules_ok,
        "break_even_powerful_x": _break_even(gated_before, gated_tables, card, "tier-powerful"),
        "break_even_decide_x": _break_even(gated_before, gated_tables, card, "tier-decide"),
        "messages": len(ds.messages),
        "senders": len({m["from_address"]["email"] for m in ds.messages}),
        "mean_body_chars": round(sum(bodies) / len(bodies)),
        "stubbed": STUBBED["scripted" if mode == "scripted" else "compare"],
        "decide_request_ids": seen,
    }
    return Summary(mode=mode, passed=cost_ok and recall_ok and rules_ok,
                   questions=questions, rules=rules, totals=totals)


def git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                             capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def summary_lines(s: Summary) -> list[str]:
    t = s.totals
    lines = [
        f"email narrowing eval ({s.mode}), {t['date']}, {t['sha']}: "
        + ("PASS" if s.passed else "FAIL"),
        f"  mailbox: {t['messages']} messages, {t['senders']} senders, "
        f"mean body {t['mean_body_chars']} characters",
        "  q   gated  answers  before(listed/read)  after(found/read)  recall  ratio  best-case",
    ]
    for q in s.questions:
        b, a = q["before"], q["after"]
        lines.append(
            f"  {q['id']}  {'yes' if q['gated'] else 'no ':5}  {q['answering']:7}  "
            f"{b['listed']:>8}/{b['read']:<10}  {a['found']:>8}/{a['read']:<8}  "
            f"{a['recall']:<6}  {q['ratio']}  {q['ratio_best_case']}  {q['status']}"
        )
    lines.append(
        f"  gated credits: before {t['before_credits']}, after {t['after_credits']}, "
        f"ratio {t['ratio']} (bar {t['cost_bar']}), best case for today "
        f"{t['ratio_best_case_for_today']}"
    )
    lines.append(
        f"  break-even: tier-powerful x{t['break_even_powerful_x']}, "
        f"tier-decide x{t['break_even_decide_x']}"
    )
    for name, rule in s.rules.items():
        if not rule["pass"]:
            lines.append(f"  RULE FAILED: {name}: {rule['detail']}")
    lines.append("  stubbed: " + "; ".join(t["stubbed"]))
    return lines


def _compare_gate() -> str | None:
    """``--compare`` runs only against a decide door on THIS machine."""
    url = os.environ.get("CUSTOMER_CONSOLE_URL", "")
    host = (urlsplit(url).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return (f"CUSTOMER_CONSOLE_URL must name a Console on this machine, not {host or 'nothing'}."
                " A run on the production Router spends credits, so it is an owner gate.")
    if os.environ.get("DECIDE_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return "DECIDE_ENABLED must be on for a --compare run."
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The email narrowing eval (WS-48 N2).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scripted", action="store_true", help="the stub door, no model (CI)")
    mode.add_argument("--compare", action="store_true", help="the real decide door on this box")
    parser.add_argument("--reads-per-turn", type=int, default=scripted.READS_PER_TURN)
    parser.add_argument("--out", type=Path, default=None, help="write summary.json here")
    args = parser.parse_args(argv)
    chosen = "compare" if args.compare else "scripted"
    if chosen == "compare":
        refusal = _compare_gate()
        if refusal:
            print(f"NO-GO: {refusal}", file=sys.stderr)
            return EXIT_NO_GO
    summary = asyncio.run(sweep(mode=chosen, reads_per_turn=args.reads_per_turn))
    for line in summary_lines(summary):
        print(line)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(
            json.dumps(summary.as_dict(), indent=2), encoding="utf-8")
    return EXIT_PASS if summary.passed else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
