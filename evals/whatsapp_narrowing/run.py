"""The runner of the WhatsApp narrowing eval: ``python -m evals.whatsapp_narrowing.run`` (WS-48 N4).

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2 and §9 N4. The
README of this folder says what each number is, and which numbers a stub
gives. It follows ``evals/email_narrowing/run.py``, and it uses that runner's
helpers for the environment, the ratios and the ``--compare`` gate.

Each question runs two ways, on the same synthetic chats:

* **before**: today's whatsapp-assistant, with no ``narrow_and_read``. It
  searches with ``search_whatsapp`` and reads chats with ``read_whatsapp_chat``.
* **after**: the same agent with ``NARROWING_AGENTS=whatsapp-assistant``. It
  calls ``narrow_and_read`` once.

Both paths call the REAL tools of ``apps/agents/agent-whatsapp-assistant``
against the gateway stub, and the after path runs the REAL pipeline of
``acb_skills.narrowing`` with the REAL decide facade and Console client.

**The pass rule:**

1. RECALL. For each gated question (Q1 to Q4), the after path reads in full
   every answering message. So no answer is dropped, and no answer is unread.
2. COST. The after path's credits on the gated questions are at most
   :data:`COST_BAR` of the before path's credits.
3. The rules of :func:`_rules` hold: tenancy, the real route parameters, one
   message for each PICK item, and a READ that changes no state.

Q5 is the expected miss (spec Q3). Its recall is reported and not gated.

Modes: ``--scripted`` (CI, no model, no Router) and ``--compare`` (the real
decide door on THIS machine), as in the email eval. Exit codes: 0 pass,
1 fail, 2 NO-GO.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from evals.email_narrowing.run import (
    _compare_gate,
    _env,
    _no_system_one,
    _patched,
    _ratio,
    _recall,
    _TapTransport,
    git_sha,
)
from evals.whatsapp_narrowing import dataset as ds_mod
from evals.whatsapp_narrowing import scripted, stub_api
from evals.whatsapp_narrowing.dataset import Dataset, Question, message_text
from evals.whatsapp_narrowing.stub_api import StubDoor, StubRouter

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "apps" / "agents" / "agent-whatsapp-assistant"
AGENT = "whatsapp-assistant"
RUN_ID = "run-whatsapp-narrowing-eval"
THREAD = "thread-whatsapp-narrowing-eval"

#: The cost bar of the pass rule: the after path costs no more than today's
#: path. ⚠️ It is NOT the 40 percent bar of the email eval (§7.2). A WhatsApp
#: message is about 15 tokens, and PICK spends about 160 tokens on each
#: candidate (one question with its guidance), so PICK costs more than it
#: saves here. The first scripted run gave a gated ratio of 0.65. The summary
#: prints the email bar beside this one, so nobody reads a pass as a saving.
COST_BAR = Decimal("1.00")
#: The bar of the email eval, printed for comparison. It is not a gate here.
EMAIL_BAR = Decimal("0.40")

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

#: Which numbers come from a stub in each mode. The summary carries it.
STUBBED = {
    "scripted": [
        "every token count (4 characters to a token, no tokenizer)",
        "every credit (the eval card of evals/email_narrowing/fixtures/rate_card.json)",
        "every PICK verdict (the stub door answers from the fixture)",
        "the full-text match (stub_api.tokens approximates to_tsvector('simple'))",
        "the before path's chat reads per request (an assumption, READS_PER_TURN)",
    ],
    "compare": [
        "the tokens of the model turns (estimated; the decide tokens are measured)",
        "every credit (the eval card; join the request ids to usage_event for real credits)",
        "the full-text match (stub_api.tokens approximates to_tsvector('simple'))",
        "the before path's chat reads per request (an assumption, READS_PER_TURN)",
    ],
}

_THREAD_PREFIX = "/whatsapp/chats/"


def _load_agent() -> Any:
    name = f"whatsapp_narrowing_eval_agent_{os.getpid()}"
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
                     source="chat", app="whatsapp", member_verified=True)
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


# ── One question ─────────────────────────────────────────────────────────────


@dataclass
class PathResult:
    router: StubRouter
    found: list[str]
    read: list[str]
    calls: list[dict[str, Any]]
    requests: list[stub_api.ChatRequest]
    output: str = ""

    def table(self) -> dict[str, dict[str, int]]:
        return self.router.table()


def _schemas(agent: Any) -> str:
    return json.dumps([t.to_json_schema_spec() for t in agent.default_options["tools"]])


def _is_read(r: stub_api.ChatRequest) -> bool:
    return r.path.startswith(_THREAD_PREFIX) and r.path.endswith("/messages")


async def _run_path(
    which: str, q: Question, ds: Dataset, running: stub_api.RunningStub, *,
    reads_per_turn: int, door: StubDoor | None, compare_seen: list[str] | None,
) -> PathResult:
    from acb_auth import console_resolve
    from acb_skills import system_one

    router = StubRouter()
    flag = AGENT if which == "after" else ""
    env = {"NARROWING_AGENTS": flag, "GATEWAY_URL": running.url,
           "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY") or "sk-whatsapp-narrowing-eval"}
    if door is not None:
        env.update({
            "DECIDE_ENABLED": "true",
            "CUSTOMER_CONSOLE_URL": "https://console.whatsapp-narrowing-eval.test",
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
            shown = await scripted.play_before(session, ds, q, reads_per_turn=reads_per_turn)
        else:
            read, output = await scripted.play_after(session, ds, q)
    requests = running.stub.requests[start:]
    if which == "before":
        # Today's model saw the lines of each search and each chat it read.
        thread_ids = [i for r in requests if _is_read(r) for i in r.ids]
        read = list(dict.fromkeys([*shown, *thread_ids]))
        found = list(dict.fromkeys(i for r in requests if r.path == "/whatsapp/search"
                                   for i in r.ids))
        found = list(dict.fromkeys([*found, *thread_ids]))
    else:
        found = next((r.ids for r in requests if r.path == "/whatsapp/search"), [])
    return PathResult(router, found, read, session.calls, requests, output)


def _one_message_each(ds: Dataset, door: StubDoor | None) -> list[str]:
    """Each PICK item that holds more than the text of its one message.

    A summary is one message: its snippet must be a part of that message's
    text, and a long message must not reach PICK past its clip."""
    if door is None:
        return []
    bad: list[str] = []
    for body in door.bodies:
        for item in body["state"]["items"].values():
            message_id = door.message_of(item)
            m = ds.by_id(message_id)
            if m is None:
                bad.append(f"no message for {item.get('who')} at {item.get('when')}")
                continue
            snippet = str(item.get("snippet") or "").removeprefix("… ")
            snippet = snippet.removeprefix("(voice note) ")
            text = " ".join(message_text(m).split())
            if snippet.startswith("[") and snippet.endswith("]") and not text:
                continue  # a media message with no text: its kind
            if snippet not in text:
                bad.append(m["slug"])
    flat = " ".join(" ".join(str(v) for v in i.values())
                    for b in door.bodies for i in b["state"]["items"].values())
    for m in ds.messages + ds.stranger_messages:
        text = " ".join(message_text(m).split())
        if len(text) > 420 and text[320:400] in flat:
            bad.append(f"{m['slug']} past its clip")
    return bad


def _rules(ds: Dataset, results: dict[str, dict[str, PathResult]],
           door: StubDoor | None) -> dict[str, dict[str, Any]]:
    """The rules that bind every question: tenancy, the routes, the wire, no state."""
    every = [r for paths in results.values() for p in paths.values() for r in p.requests]
    stranger_ids = {m["id"] for m in ds.stranger_messages}
    members = sorted({r.member for r in every})
    leaked = sorted({i for r in every for i in r.ids if i in stranger_ids})
    unknown = sorted({f"{r.path}?{u}" for r in every for u in r.unknown})
    writes = sorted({f"{r.method} {r.path}" for r in every if r.method != "GET"})
    after_requests = [r for paths in results.values() for r in paths["after"].requests]
    searches = [r for r in after_requests if r.path == "/whatsapp/search"]
    not_fixed = [r.params for r in searches
                 if r.params.get("hybrid") != ["true"] or r.params.get("websearch") != ["true"]
                 or r.params.get("limit") != ["200"]]
    over: list[str] = []
    off_route: list[str] = []
    from acb_skills import narrowing

    window = scripted_window()
    for qid, paths in results.items():
        after = paths["after"]
        reads = [r for r in after.requests if r.path != "/whatsapp/search"]
        off_route += [f"{qid}:{r.path}" for r in reads
                      if not _is_read(r) or "around" not in r.params
                      or r.params.get("window") != [str(window)]]
        anchors = [(r.params.get("around") or [""])[-1] for r in reads]
        if len(anchors) > narrowing.READ_CAP or not set(anchors) <= set(after.found):
            over.append(qid)
    bodies = _one_message_each(ds, door)
    return {
        "acts_as_the_member": {"pass": members == [ds_mod.MEMBER], "detail": members},
        "no_other_members_chats": {"pass": not leaked, "detail": leaked},
        "real_route_parameters": {"pass": not unknown, "detail": unknown},
        "narrow_is_hybrid_websearch_200": {"pass": bool(searches) and not not_fixed,
                                           "detail": not_fixed},
        "read_only_kept_within_cap": {"pass": not over, "detail": over},
        # READ changes no state: a GET of the thread route with `around`, and
        # no other method or route on either path.
        "read_changes_no_state": {"pass": not writes and not off_route,
                                  "detail": writes + off_route},
        "one_message_for_each_pick_item": (
            {"pass": not bodies, "detail": bodies} if door is not None
            else {"pass": None, "detail": "not checked in --compare mode"}
        ),
    }


def scripted_window() -> int:
    """The adapter's READ window, read from the adapter file."""
    spec = importlib.util.spec_from_file_location(
        "whatsapp_narrowing_eval_source", AGENT_DIR / "narrow_source.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.READ_WINDOW)


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
        if q.spec.gated:
            gated_before += b_cr
            gated_after += a_cr
            gated_best += best_cr
            status = "pass" if e2e == 1.0 else "fail"
            recall_ok = recall_ok and e2e == 1.0
        else:
            status = "xfail" if e2e < 1.0 else "xpass"
        questions.append({
            "id": q.id, "prompt": q.spec.prompt, "gated": q.spec.gated, "status": status,
            "note": q.spec.note, "answering": len(answering),
            "before": {"found": len(before.found), "read": len(before.read),
                       "recall": _recall(answering, before.read),
                       "tiers": before.table(), "credits": str(b_cr)},
            "before_best_case": {"tiers": best[q.id].table(), "credits": str(best_cr)},
            "after": {"found": len(after.found), "read": len(after.read),
                      "narrow_recall": _recall(answering, after.found),
                      "pick_recall": _recall(found_answering, after.read), "recall": e2e,
                      "count_line": after.output.splitlines()[0] if after.output else "",
                      "tiers": after.table(), "credits": str(a_cr)},
            "ratio": _ratio(a_cr, b_cr),
            "ratio_best_case": _ratio(a_cr, best_cr),
        })
    ratio = _ratio(gated_after, gated_before)
    cost_ok = ratio is not None and Decimal(str(ratio)) <= COST_BAR
    rules_ok = all(r["pass"] is not False for r in rules.values())
    gated_tables = [results[q.id]["after"].table() for q in ds.questions if q.spec.gated]
    texts = [len(message_text(m)) for m in ds.messages]
    totals = {
        "date": datetime.now(UTC).date().isoformat(),
        "sha": git_sha(),
        "reads_per_turn": raw.reads_per_turn,
        "cost_bar": str(COST_BAR),
        "email_bar": str(EMAIL_BAR),
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
        "chats": len([c for c in ds.chats if ds.owner_of_account(c["account_id"]) == ds_mod.MEMBER]),
        "mean_text_chars": round(sum(texts) / len(texts)),
        "stubbed": STUBBED["scripted" if raw.mode == "scripted" else "compare"],
        "decide_request_ids": seen,
    }
    return Summary(mode=raw.mode, passed=cost_ok and recall_ok and rules_ok,
                   questions=questions, rules=rules, totals=totals)


def summary_lines(s: Summary) -> list[str]:
    t = s.totals
    over = (t["ratio_best_case_for_today"] or 0) > float(t["cost_bar"])
    lines = [
        f"whatsapp narrowing eval ({s.mode}), {t['date']}, {t['sha']}: "
        + ("PASS" if s.passed else "FAIL"),
        # The gated ratio is NOT the whole story. These lines say where it
        # breaks, so nobody reads the gated ratio alone.
        f"  GATED ratio {t['ratio']} (bar {t['cost_bar']}, today's path at "
        f"{t['reads_per_turn']} chat reads to a request, an assumption)",
        f"  WEAK CASE: today's path with every chat read in ONE request gives ratio "
        f"{t['ratio_best_case_for_today']}" + (" -- OVER the bar" if over else ""),
        f"  WEAK CASE: tier-powerful at x{t['break_even_powerful_x']} of the eval "
        f"card or more takes the saving to the bar",
        f"  EMAIL BAR: the email eval gates at {t['email_bar']}. This ratio is "
        + ("OVER it: no saving of that size here" if t["ratio"] is None
           or t["ratio"] > float(t["email_bar"]) else "under it"),
        *(f"  COSTS MORE: {q['id']} costs x{q['ratio']} of today's path"
          for q in s.questions if q["gated"] and (q["ratio"] or 0) > 1),
        f"  chats: {t['messages']} messages in {t['chats']} chats, "
        f"mean text {t['mean_text_chars']} characters",
        "  q   gated  answers  before(found/read)  recall  after(found/read)  recall  ratio  best-case",
    ]
    for q in s.questions:
        b, a = q["before"], q["after"]
        lines.append(
            f"  {q['id']}  {'yes' if q['gated'] else 'no ':5}  {q['answering']:7}  "
            f"{b['found']:>8}/{b['read']:<9}  {b['recall']:<6}  "
            f"{a['found']:>8}/{a['read']:<8}  {a['recall']:<6}  "
            f"{q['ratio']}  {q['ratio_best_case']}  {q['status']}"
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
        if rule["pass"] is False:
            lines.append(f"  RULE FAILED: {name}: {rule['detail']}")
        elif rule["pass"] is None:
            lines.append(f"  RULE NOT CHECKED: {name}: {rule['detail']}")
    lines.append("  stubbed: " + "; ".join(t["stubbed"]))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The WhatsApp narrowing eval (WS-48 N4).")
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
