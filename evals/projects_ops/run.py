"""The runner of the Projects operations eval: ``python -m evals.projects_ops.run`` (WS-46 P3).

Spec: ``project-docs/specs/projects_agent_parity.md`` §11. The README of this
folder says how to run the model sweep on a local stack.

It reuses the coding-engine harness (:mod:`evals.coding_engine.run`) and does
not copy it. The coding :class:`~evals.coding_engine.run.Harness` drives the
REAL ``orchestrator.executor.run_agent_stream`` with the REAL
projects-assistant factory, and replaces the loader, the gateway address of
``skill_projects.client`` and the state root. This runner adds three things:

* :mod:`evals.projects_ops.stub_api`, a stub with write routes that records
  each request, in place of the coding stub (``Harness.serve_stub``).
* A card responder. It answers each confirmation card as the task says
  (APPROVE or REJECT), and records the card's title, detail and context, and
  how many requests the stub had seen when the card appeared.
* The cover. Each task runs uncovered by default, and covered with
  ``--covered``: ``maf_coding_scope`` is ``projects:<test org>`` in THIS
  process only, as the coding eval sets it. The executor's own record of the
  run (``executor.run_was_no_egress``) is the evidence that the run was
  covered, and each checker reads it.

``--scripted`` replays the known-good sequences (:mod:`evals.projects_ops.scripted`)
with ``ScriptedModel`` and calls no model. Without it, the runner first checks
that the Router is on THIS machine (``evals.coding_engine.preflight.router``).
A sweep on the production Router spends credits, so it is an owner gate, as
WS43-G6 is (§11.3). The runner refuses an address that is not local.

Exit codes: 0 every task passed (an ``xfail`` task counts as passed), 1 a
task failed, 2 NO-GO.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.coding_engine import run as coding
from evals.coding_engine.checkers import Session
from evals.projects_ops import checkers, scripted, stub_api, tasks
from evals.projects_ops import dataset as ds_mod
from evals.projects_ops.checkers import Card, Evidence
from evals.projects_ops.dataset import Dataset
from evals.projects_ops.tasks import DECLINE, MEMBER, TaskSpec

AGENT = coding.AGENT
SESSION_TIMEOUT_S = 300.0
ANSWER_CAP = 6000
BODY_CAP = 2000

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = coding.EXIT_PASS, coding.EXIT_FAIL, coding.EXIT_NO_GO


@dataclass
class OpsHarness(coding.Harness):
    """The coding harness, with the operations stub and the cover of this sweep."""

    covered: bool = False
    serve_stub: Any = stub_api.serve

    def __enter__(self) -> OpsHarness:
        from acb_common import get_settings

        super().__enter__()
        settings = get_settings()
        if hasattr(settings, "maf_coding_scope"):
            # The coding harness sets the scope for its own sweep. This sweep
            # sets it only when it asks for a covered run.
            scope = f"projects:{self.org}" if self.covered else ""
            self._patches.set(settings, "maf_coding_scope", scope)
        return self

    @property
    def ops(self) -> stub_api.OpsStub:
        assert self.stub is not None
        return self.stub.stub  # type: ignore[return-value]


# ── one session ─────────────────────────────────────────────────────────────


@dataclass
class _Run:
    stream: Any
    cards: list[Card] = field(default_factory=list)
    #: WS-46 P13: each selection card, with the member's submit.
    forms: list[dict[str, Any]] = field(default_factory=list)
    no_egress: bool = False


def _answer_card(harness: OpsHarness, run: _Run, spec: TaskSpec, value: dict[str, Any]) -> bool:
    """Answer one card as *spec* says, and record it. False when no card was waiting."""
    index = len(run.cards)
    answer = spec.cards[index] if index < len(spec.cards) else DECLINE
    seen = len(harness.ops.requests)
    if not coding._resolve_card(value, {"answer": answer}):
        return False
    run.cards.append(Card(
        title=str(value.get("title") or ""), detail=str(value.get("detail") or ""),
        context=str(value.get("context") or ""), answer=answer, at=seen,
    ))
    return True


def _answer_form(run: _Run, spec: TaskSpec, value: dict[str, Any]) -> bool:
    """Submit a SELECTION card as drawn, as a member who reads it and agrees.
    A field in ``spec.untick`` is submitted unticked. False when no selection
    card was waiting.

    Only a ``formCard`` with checkbox fields is answered (review round 1).
    Any other blocking card, an edit form or a plan, stays unanswered, so a
    run that opens one it should not open still fails "run_completed"."""
    props = value.get("props") if isinstance(value.get("props"), dict) else {}
    data = props.get("data") if isinstance(props.get("data"), dict) else {}
    fields = [f for f in data.get("fields") or [] if isinstance(f, dict)]
    if props.get("name") != "formCard" or not any(f.get("type") == "checkbox" for f in fields):
        return False
    values = {str(f.get("name")): f.get("value") for f in fields}
    for name in spec.untick:
        if name in values:
            values[name] = False
    label = str(data.get("submitLabel") or data.get("title") or "Form")
    if not coding._resolve_card(value, {"answer": f"{label} — {json.dumps(values)}"}):
        return False
    run.forms.append({"title": str(data.get("title") or ""), "fields": fields, "values": values})
    return True


async def _drive(harness: OpsHarness, session: Session, spec: TaskSpec, run: _Run) -> None:
    from orchestrator import executor

    from tests.unit._native_maf_harness import parse_frames

    run_id = str(uuid.uuid4())
    try:
        gen = executor.run_agent_stream(
            AGENT, {"message": session.prompt, "user_email": session.member, "source": "eval"},
            run_id=run_id, thread_id=session.thread_id, model=harness.tier,
            organization_id=harness.org, session_user=session.member,
        )
        async for line in gen:
            for event in parse_frames([line]):
                value = event.get("value") or {}
                if (event.get("type") == "CUSTOM" and event.get("name") == "confirmation_requested"
                        and isinstance(value, dict) and _answer_card(harness, run, spec, value)):
                    continue
                if (event.get("type") == "CUSTOM" and event.get("name") == "generative_ui"
                        and isinstance(value, dict) and value.get("request_id")
                        and _answer_form(run, spec, value)):
                    continue
                coding._take(run.stream, event)
    finally:
        run.no_egress = executor.run_was_no_egress(run_id)
        coding._finish(run.stream)


async def run_session(
    harness: OpsHarness, spec: TaskSpec, steps: list[tuple[Any, ...]] | None,
) -> tuple[Session, _Run, coding.ModelTap, float]:
    """One session: a new thread, the task's prompt, the real executor."""
    session = Session(member=MEMBER, thread_id=str(uuid.uuid4()), prompt=spec.prompt)
    run = _Run(stream=coding._Stream(session))
    tap = coding.ModelTap()
    coding._set_wire(harness, tap, steps)
    started = time.monotonic()
    try:
        await asyncio.wait_for(_drive(harness, session, spec, run), SESSION_TIMEOUT_S)
    except TimeoutError:
        session.error = f"the session ran past {SESSION_TIMEOUT_S:.0f} s"
    finally:
        coding._reset_run_state()
    return session, run, tap, time.monotonic() - started


# ── one run of one task ─────────────────────────────────────────────────────


def _head(harness: OpsHarness, spec: TaskSpec, run: int) -> dict[str, Any]:
    return {
        "task": spec.id, "title": spec.title, "short_prompt": spec.short, "run": run,
        "engine": "maf", "agent": AGENT, "tier": harness.tier,
        "mode": "scripted" if harness.scripted else "router",
        "covered": harness.covered, "sha": coding.git_sha(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "organization_id": harness.org,
    }


def _reset_stub(harness: OpsHarness) -> None:
    """A fresh dataset state for each run, so no run sees another's writes."""
    fresh = stub_api.OpsStub(harness.dataset)
    ops = harness.ops
    ops.requests.clear()
    ops.tasks, ops.rules, ops.nodes = fresh.tasks, fresh.rules, fresh.nodes


async def run_task(
    harness: OpsHarness, spec: TaskSpec, run: int,
    *, steps: list[tuple[Any, ...]] | None = None,
) -> dict[str, Any]:
    """One run of *spec*. Returns the record that becomes its JSON file.

    *steps* replaces the known-good sequence. It is no CLI flag: a test passes
    a mutated sequence to see a checker fail a wrong run (R7).
    """
    head = _head(harness, spec, run)
    if spec.xfail:
        return {**head, "status": "xfail", "failure": spec.xfail, "rules": []}
    if harness.scripted and steps is None:
        steps = scripted.steps_for(spec.id, harness.dataset)
    _reset_stub(harness)
    session, ran, tap, wall = await run_session(harness, spec, steps)
    evidence = Evidence(
        task_id=spec.id, dataset=harness.dataset, sessions=[session],
        requests=list(harness.ops.requests), cards=ran.cards, forms=ran.forms,
        covered=harness.covered, no_egress=[ran.no_egress],
    )
    return {**head, **_verdict(evidence), **_record(evidence, tap, wall)}


def _verdict(evidence: Evidence) -> dict[str, Any]:
    try:
        rules = checkers.check(evidence)
    except Exception as exc:
        return {"status": "error", "failure": f"checker raised {type(exc).__name__}: {exc}",
                "rules": []}
    ok = checkers.passed(rules)
    return {"status": "pass" if ok else "fail",
            "failure": None if ok else checkers.first_failure(rules),
            "rules": [r.as_dict() for r in rules]}


def _clip(value: Any) -> Any:
    text = json.dumps(value, default=str)
    return value if len(text) <= BODY_CAP else text[:BODY_CAP] + "…"


def _record(evidence: Evidence, tap: coding.ModelTap, wall: float) -> dict[str, Any]:
    calls = evidence.calls
    return {
        "wall_s": round(wall, 2),
        "tool_calls": len(calls),
        "model_calls": len(tap.bodies),
        "tokens": tap.tokens(),
        "no_egress": evidence.no_egress,
        "cards": [{"title": c.title, "detail": c.detail, "context": c.context,
                   "answer": c.answer, "at": c.at} for c in evidence.cards],
        "forms": evidence.forms,
        "requests": [{"method": r.method, "path": r.path, "query": r.query, "member": r.member,
                      "status": r.status, "body": _clip(r.body)} for r in evidence.requests],
        "sessions": [{
            "member": s.member, "thread_id": s.thread_id, "prompt": s.prompt, "error": s.error,
            "tool_calls": [{"name": c.name, "ok": c.ok, "args": c.args[:600],
                            "result": c.result[:1200]} for c in s.tool_calls],
            "answer": s.answer[:ANSWER_CAP],
        } for s in evidence.sessions],
    }


# ── the sweep ───────────────────────────────────────────────────────────────


async def sweep(
    harness: OpsHarness, chosen: list[TaskSpec], repeat: int, out: Path,
) -> list[dict[str, Any]]:
    """Every run of every chosen task, in ONE event loop, as the gateway runs them."""
    out.mkdir(parents=True, exist_ok=True)
    suffix = "covered" if harness.covered else "uncovered"
    records = []
    for spec in chosen:
        for run in range(1, repeat + 1):
            record = await run_task(harness, spec, run)
            record["repeat"] = repeat
            (out / f"{spec.id}-{suffix}-run{run}.json").write_text(
                json.dumps(record, indent=2, default=str), encoding="utf-8",
            )
            records.append(record)
            print(f"{spec.id} ({suffix}) run {run}/{repeat}: {record['status']}"
                  + (f": {record['failure']}" if record.get("failure") else ""), flush=True)
    return records


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.projects_ops.run", description=__doc__)
    p.add_argument("--tier", default="tier-balanced", help="the Router tier of the sweep")
    p.add_argument("--tasks", default="all", help="all, or a comma list such as PO-1,PO-4")
    p.add_argument("--repeat", "--runs", dest="repeat", type=int, default=0,
                   help="runs of each task (default: 1 scripted, 3 on the Router)")
    p.add_argument("--org", default="", help="the test organization (default: the dataset's)")
    p.add_argument("--out", default="", help="the results dir (default: a new one under results/)")
    p.add_argument("--scripted", action="store_true",
                   help="replay the known-good sequences with ScriptedModel, and call no model")
    p.add_argument("--covered", action="store_true",
                   help="run covered: MAF_CODING_SCOPE=projects:<org> in this process only")
    return p


def make_harness(
    data: Dataset, org: str, state_root: Path, *, tier: str, scripted_mode: bool, covered: bool,
) -> OpsHarness:
    return OpsHarness(
        dataset=data, org=org, state_root=state_root, tier=tier, scripted=scripted_mode,
        clean_blob_store=False, covered=covered,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        chosen = tasks.select(args.tasks)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    data = ds_mod.load()
    org = args.org or data.organization_id
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else Path(__file__).parent / "results" / stamp
    out.mkdir(parents=True, exist_ok=True)
    if not args.scripted:
        gate = coding._router_gate(args.tier, MEMBER)
        if not gate.ok:
            print(gate.reason, file=sys.stderr)
            (out / "NO-GO.json").write_text(json.dumps({"no_go": gate.reason}), encoding="utf-8")
            return EXIT_NO_GO
    repeat = args.repeat or (1 if args.scripted else 3)
    state = Path(tempfile.mkdtemp(prefix="state-", dir=out))
    with make_harness(data, org, state, tier=args.tier, scripted_mode=args.scripted,
                      covered=args.covered) as harness:
        records = asyncio.run(sweep(harness, chosen, max(1, repeat), out))
    lines = coding.summary_lines(records)
    (out / "summary.json").write_text(
        json.dumps({"lines": lines, "sha": coding.git_sha(), "covered": args.covered,
                    "mode": "scripted" if args.scripted else "router"}, indent=2),
        encoding="utf-8",
    )
    print("\n".join(lines))
    print(f"results: {out}")
    return coding.exit_code(records)


if __name__ == "__main__":
    raise SystemExit(main())
