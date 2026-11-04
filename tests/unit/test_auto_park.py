"""WS-AUTO AUTO-7b: a run with no person present parks at its gate and asks.

Spec: ``project-docs/specs/proactive_agent.md`` §4.2 and §11. Decisions: D161,
D131 rule 4. Code: ``routes/workflows/park.py``, ``service._hold_at_gate``,
``service.resume_run`` and ``scheduler.scan_decided_pauses``.

A workflow run of ``UNATTENDED_TRIGGERS`` carries the AUTO-7a mark. With
``AUTO_PARK_ORGS`` and ``DECISIONS_ORGS`` on for its org, its approval gate
asks the workflow's owner through ONE decision (ALN-2a). A yes of the owner
resumes the run once, a no ends it, and the DB clock ends an unanswered one.
The run itself can never answer.

**Hermetic** (no database): the flag, the broker door byte for byte, the
owner as the decider, the outcome rule, the source scans and the loop guard.

**R8** (the class carries ``_DB_GATE``): the real router seams on a dedicated
database with the full ladder and the four ``generated/`` phases, as a role
that is NOT a superuser, NOT the owner and has NO BYPASSRLS.

**The nine fences of the audit, by name.**

* F1. One decision and no proposal: ``test_f1_*``.
* F2. Off, a refusal, no owner, no org: the AUTO-7a broker door, byte for
  byte: ``test_f2_*``.
* F3. A run with no person cannot answer, and only the decision row decides:
  ``test_f3_*``.
* F4. The decider is ``workflows.owner_email``: ``test_f4_*``.
* F5. The new module writes no answer, calls no ``answer_decision`` and
  counts no person: ``test_f5_*``.
* F6. A yes resumes once, under two scans or a scan and the broker door:
  ``test_f6_*``.
* F7. A no ends the run, and an expiry ends it and tells once: ``test_f7_*``.
* F8. A resumed run stays marked, and a second gate parks again:
  ``test_f8_*``.
* F9. ``test_no_person_present.py`` needs no new allow-list entry:
  ``test_f9_*``.

**Mutations this suite catches** (each was run, see the PR):

* M1. ``_park_with_decision``: drop the ``park_allowed`` check (F2 red).
* M2. ``_hold_at_gate``: file the proposal after a park as well (F1 red).
* M3. ``decisions.answer_decision``: drop ``_refuse_no_person()`` (F3 red).
* M4. ``park.outcome_of``: resume on ``yes`` in the snapshot (F3 red).
* M5. ``_park_with_decision``: take the owner from
  ``_workflow_billing_member`` (F4 red).
* M6. ``park.py``: add ``UPDATE pm_decisions SET answered_by`` (F5 red).
* M7. ``resume_run``: drop ``AND status = 'pending'`` and
  ``AND status = 'paused'`` (F6 red).
* M8. ``park.end_parked``: drop both status guards (F7 red).
* M9. ``resume_run``: resume with ``trigger_kind="manual"`` (F8 red).
* M10. ``park.park_with_decision``: ``announce=True`` (the loop guard red).

Run (a PRIVATE database, because the fixture drops ``<db>_autopark``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/auto7b_r8
    uv run pytest tests/unit/test_auto_park.py -v -rs

Without the database each R8 test SKIPS. A skip is not a pass.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import itertools
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

import action_broker.broker as broker
from acb_common import get_settings
from acb_common.no_person import (
    NO_PERSON_PRESENT,
    no_person_present,
    no_person_refusal,
    no_person_scope,
)
from fastapi import HTTPException
from gateway.routes.projects import decisions, personal
from gateway.routes.workflows import park, scheduler, service, unattended
from gateway.routes.workflows.engine.handlers import NodeExecutionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "workflows"
_PARK = _WORKFLOWS / "park.py"
_SCHEDULER = _WORKFLOWS / "scheduler.py"
_SERVICE = _WORKFLOWS / "service.py"
_GENERATED = _ROOT / "infra" / "postgres" / "generated"

#: Every bound on a wait. Broken code goes red and never hangs.
_WAIT = 60

ORG = "0d4b8f5e-0000-4000-8000-0000000000a1"


def _flags(monkeypatch, *, park_orgs: str, decision_orgs: str) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_park_orgs", park_orgs)
    monkeypatch.setattr(settings, "decisions_orgs", decision_orgs)


# ══ Hermetic ════════════════════════════════════════════════════════════════

# ── The flag ────────────────────────────────────────────────────────────────


def test_the_flag_ships_empty_and_needs_both_lists(monkeypatch) -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["auto_park_orgs"].default == ""
    other = str(uuid.uuid4())
    for park_orgs, decision_orgs, want in (
        ("", "", False),
        ("", "*", False),
        (ORG, "", False),
        (ORG, other, False),
        (f" {ORG.upper()} ,x", ORG, True),
        ("*", "*", True),
        (other, "*", False),
    ):
        _flags(monkeypatch, park_orgs=park_orgs, decision_orgs=decision_orgs)
        assert park.park_allowed(ORG) is want, (park_orgs, decision_orgs)
    _flags(monkeypatch, park_orgs="*", decision_orgs="*")
    for nobody in (None, "", "  "):
        assert park.park_allowed(nobody) is False


def test_the_flag_is_named_in_each_place_of_a_flag() -> None:
    from acb_common import env_guard
    from gateway.flag_reload import RELOADABLE

    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^AUTO_PARK_ORGS=$", env, re.MULTILINE)
    assert "AUTO_PARK_ORGS" in env_guard.PLATFORM_ENV_NAMES
    assert env_guard.is_platform_env("AUTO_PARK_ORGS") is True
    assert "AUTO_PARK_ORGS" in RELOADABLE


def test_park_allowed_is_the_one_reader_of_the_flag() -> None:
    allowed = {
        "apps/services/gateway/gateway/routes/workflows/park.py",
        "packages/acb_common/acb_common/env_guard.py",
    }
    found: set[str] = set()
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            if "auto_park_orgs" not in body.lower():
                continue
            for node in ast.walk(ast.parse(body)):
                hit = (isinstance(node, ast.Attribute) and node.attr == "auto_park_orgs") or (
                    isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.strip().lower() == "auto_park_orgs")
                if hit:
                    found.add(path.relative_to(_ROOT).as_posix())
    assert found == allowed, found ^ allowed


# ── The gate: one decision, or the broker door of AUTO-7a ───────────────────


_SERIALIZED = {"blocks": [{"id": "gate", "label": "Approve"}]}
_PAYLOAD = {"scheduled_at": "2099-01-01T00:00:00+00:00"}

#: The broker door as AUTO-7a left it. Pinned, so a change of a byte is red.
_BROKER_SQL = (
    "INSERT INTO workflow_run_pauses (run_id, node_id, snapshot, reason, status) "
    "VALUES (:run_id, :node_id, :snapshot ::jsonb, :reason, 'pending')"
)
_BROKER_PARAMS = {
    "run_id": "r-1",
    "node_id": "gate",
    "reason": "approval",
    "snapshot": (
        '{"serialized": {"blocks": [{"id": "gate", "label": "Approve"}]}, '
        '"trigger_payload": {"scheduled_at": "2099-01-01T00:00:00+00:00"}, '
        '"variables": {}, "workflow_id": "w-1", "workflow_name": "Nudge", '
        '"started_by": "scheduler", "action_id": "act-1", "resume_at": null}'
    ),
}
_BROKER_PROPOSAL = {
    "actor": "workflow:Nudge",
    "action": "workflow.resume_run",
    "target": "workflow_run:r-1",
    "payload": {
        "run_id": "r-1", "workflow_id": "w-1", "workflow_name": "Nudge",
        "node_id": "gate", "node_label": "Approve", "requested_by": "scheduler",
    },
    "destructive": False,
}


class _CaptureDb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        self.calls.append((" ".join(str(clause).split()), dict(params or {})))
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


def _gate(
    monkeypatch,
    *,
    marked: bool = True,
    org: str | None = ORG,
    park_orgs: str = ORG,
    decision_orgs: str = ORG,
    owner: str | None = "owner@a.test",
    refuse: Exception | None = None,
    payload: dict | None = None,
) -> SimpleNamespace:
    """Run the real ``_hold_at_gate`` of an approval gate on fakes."""
    db = _CaptureDb()

    @contextlib.asynccontextmanager
    async def _session(_org: str | None):
        yield db

    seen = SimpleNamespace(db=db, proposals=[], parked=[], owner_reads=[])

    async def _submit(proposal: Any) -> dict:
        seen.proposals.append(proposal)
        return {"action_id": "act-1"}

    async def _park(_db: Any, **kw: Any) -> dict:
        seen.parked.append(kw)
        if refuse is not None:
            raise refuse
        return {"decision_id": "d-1", "task_id": "t-1", "pause_id": "p-1"}

    async def _owner(workflow_id: str, organization_id: str | None) -> tuple[str, str]:
        seen.owner_reads.append((workflow_id, organization_id))
        if owner is None:
            raise NodeExecutionError("workflow w-1 has no owner to tell")
        return str(organization_id), owner

    monkeypatch.setattr(service, "_run_session", _session)
    monkeypatch.setattr(broker, "submit", _submit)
    monkeypatch.setattr(park, "park_with_decision", _park)
    monkeypatch.setattr(service, "_workflow_owner", _owner)
    _flags(monkeypatch, park_orgs=park_orgs, decision_orgs=decision_orgs)

    async def main() -> None:
        scope = no_person_scope() if marked else contextlib.nullcontext()
        with scope:
            await asyncio.wait_for(service._hold_at_gate(
                run_id="r-1", workflow_id="w-1", workflow_name="Nudge", node_id="gate",
                serialized=_SERIALIZED, trigger_payload=payload or dict(_PAYLOAD),
                variables={}, started_by="scheduler", organization_id=org,
            ), _WAIT)

    asyncio.run(main())
    return seen


def _proposal_shape(p: Any) -> dict:
    return {"actor": p.actor, "action": p.action, "target": p.target,
            "payload": dict(p.payload), "destructive": p.destructive}


def _assert_broker_door(seen: SimpleNamespace) -> None:
    assert [_proposal_shape(p) for p in seen.proposals] == [_BROKER_PROPOSAL]
    assert seen.db.calls == [(_BROKER_SQL, _BROKER_PARAMS)]


def test_f1_a_marked_gate_parks_once_and_files_no_proposal(monkeypatch) -> None:
    seen = _gate(monkeypatch)
    assert seen.proposals == [], "a parked gate filed a broker proposal too"
    assert seen.db.calls == [], "a parked gate wrote the broker pause row too"
    assert len(seen.parked) == 1
    call = seen.parked[0]
    assert (call["organization_id"], call["owner"], call["run_id"], call["node_id"],
            call["node_label"], call["workflow_name"]) == (
        ORG, "owner@a.test", "r-1", "gate", "Approve", "Nudge")
    # The snapshot is the one a resume needs, with no proposal id.
    assert call["snapshot"] == {
        "serialized": _SERIALIZED, "trigger_payload": _PAYLOAD, "variables": {},
        "workflow_id": "w-1", "workflow_name": "Nudge", "started_by": "scheduler",
        "action_id": None, "resume_at": None,
    }


@pytest.mark.parametrize("case", [
    "flag_off", "decisions_off", "unmarked", "no_org", "no_owner",
    "decision_refused", "park_refused", "crash",
])
def test_f2_each_fallback_is_the_broker_door_byte_for_byte(monkeypatch, case) -> None:
    kw: dict[str, Any] = {
        "flag_off": {"park_orgs": ""},
        "decisions_off": {"decision_orgs": ""},
        "unmarked": {"marked": False},
        "no_org": {"org": None},
        "no_owner": {"owner": None},
        "decision_refused": {"refuse": decisions.DecisionRefused("decider_cannot_open", "x")},
        "park_refused": {"refuse": park.ParkRefused("owner_not_member")},
        "crash": {"refuse": RuntimeError("the pause insert failed")},
    }[case]
    seen = _gate(monkeypatch, **kw)
    _assert_broker_door(seen)
    if case in ("flag_off", "decisions_off", "unmarked", "no_org"):
        # These cases read nothing: no owner, no park, no session.
        assert seen.parked == [] and seen.owner_reads == []


def test_f2_a_wait_never_parks_on_a_decision(monkeypatch) -> None:
    db = _CaptureDb()

    @contextlib.asynccontextmanager
    async def _session(_org: str | None):
        yield db

    parked: list[Any] = []

    async def _park(*_a: Any, **_k: Any) -> dict:
        parked.append(1)
        return {}

    monkeypatch.setattr(service, "_run_session", _session)
    monkeypatch.setattr(park, "park_with_decision", _park)
    _flags(monkeypatch, park_orgs="*", decision_orgs="*")

    async def main() -> None:
        with no_person_scope():
            await service._hold_at_gate(
                run_id="r-1", workflow_id="w-1", workflow_name="Nudge", node_id="hold",
                serialized={"blocks": []}, trigger_payload={}, variables={},
                started_by="scheduler", resume_at=1.0, organization_id=ORG)

    asyncio.run(main())
    assert parked == []
    assert [p["reason"] for _, p in db.calls] == ["wait"]


def test_f4_the_decider_is_the_owner_never_the_payload_or_the_member(monkeypatch) -> None:
    """The ambient member is a verified other person, and the payload names a
    third. The owner comes from ``_workflow_owner`` alone."""
    from acb_common import job_member_scope

    async def _billing(_wid: str) -> str:
        return "member@a.test"

    monkeypatch.setattr(service, "_workflow_billing_member", _billing)
    with job_member_scope("member@a.test", app="workflows"):
        seen = _gate(monkeypatch, payload={"owner_email": "payload@a.test",
                                           "decider": "payload@a.test"})
    assert seen.owner_reads == [("w-1", ORG)]
    assert [c["owner"] for c in seen.parked] == ["owner@a.test"]


def test_f4_the_ask_names_the_owner_as_the_decider_and_the_workflow_as_the_asker() -> None:
    """Read ``park_with_decision``: ``ask_decision`` gets ``decider=who`` (the
    owner), ``asked_by=asker(workflow_id)``, ``source="auto"``."""
    fn = _function(_PARK, "park_with_decision")
    asks = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and _name(c.func) == "ask_decision"]
    assert len(asks) == 1
    kws = {k.arg: k.value for k in asks[0].keywords}
    assert isinstance(kws["decider"], ast.Name) and kws["decider"].id == "who"
    assert ast.unparse(kws["asked_by"]) == "asker(workflow_id)"
    assert ast.unparse(kws["source"]) == "'auto'"
    assert park.asker("w-1") == "system:workflow:w-1"


# ── The outcome rule: only the decision row decides ─────────────────────────


def _decision(**kw: Any) -> SimpleNamespace:
    base = {"decider": "owner@a.test", "answered_by": None, "answer_key": None,
            "answered_at": None, "is_expired": False, "subject_id": "t-1"}
    return SimpleNamespace(**{**base, **kw})


def test_f3_the_outcome_comes_from_the_decision_row_only() -> None:
    at = "2099-01-01T00:00:00+00:00"
    assert park.outcome_of(None) == park.WAIT
    assert park.outcome_of(_decision()) == park.WAIT
    assert park.outcome_of(_decision(is_expired=True)) == park.EXPIRE
    assert park.outcome_of(_decision(
        answered_by="owner@a.test", answer_key="yes", answered_at=at)) == park.RESUME
    assert park.outcome_of(_decision(
        answered_by="OWNER@a.test", answer_key="yes", answered_at=at)) == park.RESUME
    assert park.outcome_of(_decision(
        answered_by="owner@a.test", answer_key="no", answered_at=at)) == park.DECLINE
    # A yes of anybody else, or of nobody, never resumes.
    assert park.outcome_of(_decision(
        answered_by="other@a.test", answer_key="yes", answered_at=at)) == park.DECLINE
    assert park.outcome_of(_decision(
        answered_by=None, answer_key="yes", answered_at=at)) == park.DECLINE
    # An answer that came in time wins over a clock that passed later.
    assert park.outcome_of(_decision(
        answered_by="owner@a.test", answer_key="yes", answered_at=at,
        is_expired=True)) == park.RESUME


def _decided_scan(monkeypatch, decision: Any, snapshot_extra: dict) -> SimpleNamespace:
    """Run the real ``scan_decided_pauses`` on a fake session: one parked
    gate, and *decision* as its row."""
    did = str(uuid.uuid4())
    pause = SimpleNamespace(
        id="p-1", run_id="r-1", organization_id=ORG, workflow_id="w-1",
        workflow_name="Nudge",
        snapshot={"decision_id": did, "trigger_payload": {"approved": True},
                  **snapshot_extra},
    )

    class _Db:
        async def execute(self, clause: Any, params: dict | None = None) -> Any:
            sql = str(clause)
            if "FROM pm_decisions" in sql:
                return SimpleNamespace(fetchone=lambda: decision)
            if sql.startswith("UPDATE"):
                raise AssertionError("the scan ended a run that it must leave")
            return SimpleNamespace(fetchall=lambda: [pause])

    @contextlib.asynccontextmanager
    async def _session(_org: str | None = None):
        yield _Db()

    seen = SimpleNamespace(resumes=[])

    async def _resume(run_id: str, **kw: Any) -> dict:
        seen.resumes.append((run_id, kw))
        return {"ok": True}

    monkeypatch.setattr(scheduler, "_tenant_session", _session)
    monkeypatch.setattr(scheduler, "resume_run", _resume)
    seen.settled = asyncio.run(scheduler.scan_decided_pauses(ORG))
    return seen


def test_f3_an_approved_word_in_the_snapshot_or_the_payload_resumes_nothing(monkeypatch) -> None:
    seen = _decided_scan(monkeypatch, _decision(), {
        "answer_key": "yes", "answered_by": "owner@a.test", "answered_at": "now",
        "variables": {"approved": True, "decision": "yes"},
    })
    assert seen.resumes == [] and seen.settled == 0


def test_f3_a_yes_of_the_decider_resumes_as_the_decider_in_the_rows_org(monkeypatch) -> None:
    seen = _decided_scan(monkeypatch, _decision(
        answered_by="owner@a.test", answer_key="yes", answered_at="now"), {})
    assert seen.resumes == [("r-1", {"resumed_by": "owner@a.test", "organization_id": ORG})]


def test_f3_a_marked_turn_resumes_nothing(monkeypatch) -> None:
    """Q5 item 5. ``resume_run`` refuses a marked turn before any read."""

    @contextlib.asynccontextmanager
    async def _no_session(_org: str | None):
        raise AssertionError("a marked turn opened a session")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "_run_session", _no_session)

    async def main() -> dict:
        with no_person_scope():
            return await service.resume_run("r-1", resumed_by="owner@a.test",
                                            organization_id=ORG)

    assert asyncio.run(main()) == no_person_refusal()


# ── The compare-and-set of resume_run ───────────────────────────────────────


class _ResumeDb:
    """One paused run and its pause. *won* says which UPDATE wins."""

    def __init__(self, *, pause_wins: bool, run_wins: bool) -> None:
        self.pause_wins, self.run_wins = pause_wins, run_wins
        self.updates: list[str] = []
        self.rolled_back = False

    async def execute(self, clause: Any, _params: Any = None) -> Any:
        sql = " ".join(str(clause).split())
        if sql.startswith("UPDATE workflow_run_pauses"):
            self.updates.append(sql)
            row = SimpleNamespace(id="p-1") if self.pause_wins else None
            return SimpleNamespace(fetchone=lambda: row)
        if sql.startswith("UPDATE workflow_runs"):
            self.updates.append(sql)
            row = SimpleNamespace(id="r-1") if self.run_wins else None
            return SimpleNamespace(fetchone=lambda: row)
        if "FROM workflow_runs" in sql:
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(
                status="paused", node_results="{}", workflow_id="w-1",
                workflow_name="Nudge", trigger_kind="schedule", organization_id=ORG))
        return SimpleNamespace(fetchone=lambda: SimpleNamespace(
            id="p-1", node_id="gate", reason="approval", snapshot={}))

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize(("pause_wins", "run_wins"), [(False, True), (True, False)])
def test_f6_a_lost_claim_starts_nothing(monkeypatch, pause_wins, run_wins) -> None:
    db = _ResumeDb(pause_wins=pause_wins, run_wins=run_wins)

    @contextlib.asynccontextmanager
    async def _session(_org: str | None):
        yield db

    started: list[Any] = []

    async def _execute(**kw: Any) -> None:
        started.append(kw)

    monkeypatch.setattr(service, "_run_session", _session)
    monkeypatch.setattr(service, "_execute_run", _execute)
    out = asyncio.run(service.resume_run("r-1", resumed_by="owner@a.test", organization_id=ORG))
    assert out["ok"] is False and started == []
    assert db.rolled_back is (pause_wins and not run_wins)


def test_f6_each_update_of_a_resume_names_the_status_it_read() -> None:
    fn = ast.unparse(_function(_SERVICE, "resume_run"))
    assert "WHERE id = :id AND status = 'pending' RETURNING id" in fn
    assert fn.count("WHERE id = :id AND status = 'paused' RETURNING id") == 2
    end = ast.unparse(_function(_PARK, "end_parked"))
    assert "AND status = 'pending' RETURNING id" in end
    assert "AND status = 'paused' RETURNING id" in end


# ── The source scans ────────────────────────────────────────────────────────


def _function(path: Path, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} has no function {name}")


def _name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _code_strings(tree: ast.AST) -> list[str]:
    """The string constants of *tree*, each docstring left out."""
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _settle_sources() -> list[tuple[str, ast.AST]]:
    return [("park.py", ast.parse(_PARK.read_text(encoding="utf-8"))),
            ("scan_decided_pauses", _function(_SCHEDULER, "scan_decided_pauses"))]


_ANSWER_COLUMNS = ("answered_by", "answer_key", "answered_at", "answer_reason")


def test_f5_the_settle_code_writes_no_answer_and_calls_no_answer() -> None:
    for where, tree in _settle_sources():
        calls = {_name(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "answer_decision" not in calls and "answer" not in calls, where
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "answer_decision" not in names, where
        for node in ast.walk(tree):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, ast.AugAssign | ast.AnnAssign)
                       else [])
            for t in targets:
                assert not (isinstance(t, ast.Attribute) and t.attr in _ANSWER_COLUMNS), where
        for value in _code_strings(tree):
            sql = " ".join(value.split()).lower()
            assert "update pm_decisions" not in sql, (where, value)
            assert "insert into pm_decisions" not in sql, (where, value)
            assert not re.search(r"\bset\b[^;]*\banswer", sql), (where, value)


def _without_docs(tree: ast.AST) -> str:
    """The code of *tree*, each docstring left out. A comment never reaches
    the tree."""
    import copy

    clone = copy.deepcopy(tree)
    for node in ast.walk(clone):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(clone)


def test_f5_the_settle_code_counts_no_person() -> None:
    """ALN-Q5: no count, group, rank or score of a person."""
    for where, tree in _settle_sources():
        code = _without_docs(tree)
        for forbidden in (r"group\s+by", r"count\s*\(", r"\brank\b", r"\bscore\b",
                          r"\bsum\s*\(", r"\bavg\s*\("):
            assert not re.search(forbidden, code, re.IGNORECASE), (where, forbidden)


def test_f5_the_question_comes_from_two_stored_fields_only() -> None:
    tree = ast.parse(_PARK.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for word in ("trigger_payload", "variables", "node_results", "outputs", "output"):
        assert word not in names and word not in attrs, word
    fn = _function(_PARK, "park_with_decision")
    asks = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and _name(c.func) == "park_question"]
    assert [ast.unparse(a) for a in asks] == ["park_question(workflow_name, node_label)"]
    question = park.park_question("Weekly  invoice\nrun", "Approve the send")
    assert question == 'Let the workflow "Weekly invoice run" go past "Approve the send"?'
    long = park.park_question("n" * 500, "l" * 500)
    assert len(long) <= decisions.QUESTION_MAX == park.QUESTION_MAX


def test_f5_the_decided_scan_lives_in_the_one_org_loop() -> None:
    """No second loop over the orgs and no second scheduler. The decided scan
    runs inside the loop of ``scan_due_waits``, after its list check."""
    tree = ast.parse(_SCHEDULER.read_text(encoding="utf-8"))
    org_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and _name(n.func) == "_organization_ids"]
    assert len(org_calls) == 2, "a third loop over the orgs"
    scan = _function(_SCHEDULER, "scan_due_waits")
    loop = next(n for n in ast.walk(scan) if isinstance(n, ast.For))
    assert isinstance(loop.body[0], ast.If)  # the list check comes first
    inner = [n for stmt in loop.body[1:] for n in ast.walk(stmt)
             if isinstance(n, ast.Call) and _name(n.func) == "scan_decided_pauses"]
    assert len(inner) == 1
    decided = _function(_SCHEDULER, "scan_decided_pauses")
    assert not [n for n in ast.walk(decided) if isinstance(n, ast.Call)
                and _name(n.func) == "_organization_ids"]
    callers = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and _name(n.func) == "scan_decided_pauses"]
    assert len(callers) == 1


def test_f9_the_new_module_needs_no_allow_list_entry() -> None:
    """The new module names no route that approves or answers, and it never
    reads or sets the mark. So ``test_no_person_present.py`` keeps its lists."""
    tree = ast.parse(_PARK.read_text(encoding="utf-8"))
    routes = ("/actions/pending", "/respond-input", "/archive-approval",
              "/agent-runs/", "/decisions/", "/answer")
    for value in _code_strings(tree):
        assert not any(r in value for r in routes), value
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not names & {"no_person_present", "no_person_scope", "_NO_PERSON_PRESENT"}
    fence = (_ROOT / "tests/unit/test_no_person_present.py").read_text(encoding="utf-8")
    assert "workflows/park.py" not in fence


# ── The loop guard: a parked gate's task announces nothing ──────────────────


def test_the_subject_task_sends_no_created_event(monkeypatch) -> None:
    """A ``pm.task.created`` event can start a workflow with an event trigger.
    The parked gate's task must not send one, or a workflow could park and
    start itself again with no end. Every other caller keeps the event."""
    fn = _function(_PARK, "park_with_decision")
    makes = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
             and _name(c.func) == "create_personal_task"]
    assert len(makes) == 1
    kws = {k.arg: k.value for k in makes[0].keywords}
    assert isinstance(kws.get("announce"), ast.Constant) and kws["announce"].value is False

    emitted: list[str] = []

    async def _emit(event: str, _payload: dict) -> None:
        emitted.append(event)

    async def _insert(_db: Any, _table: str, values: dict) -> Any:
        return SimpleNamespace(id=str(uuid.uuid4()), **values)

    async def _nothing(*_a: Any, **_k: Any) -> Any:
        return 1

    class _Db:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(name="x", archived_at=None))

    monkeypatch.setattr(personal, "emit", _emit)
    monkeypatch.setattr(personal, "insert_row", _insert)
    monkeypatch.setattr(personal, "next_task_number", _nothing)
    monkeypatch.setattr(personal, "record_activity", _nothing)
    monkeypatch.setattr(personal, "_upsert_personal", _nothing)

    async def main(**kw: Any) -> None:
        await personal.create_personal_task(
            _Db(), "owner@a.test", "p-1", "p-1", "s-1", {"title": "q", "source": "automation"},
            **kw)

    asyncio.run(main(announce=False))
    assert emitted == []
    asyncio.run(main())
    assert emitted == ["pm.task.created"]


# ══ R8 ══════════════════════════════════════════════════════════════════════

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT if _SNAPSHOT is not None else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it. Do NOT export "
        "it as DATABASE_URL."
    ),
)

#: Suite-private role name, canonical privileges.
_APP_ROLE = "acb_app_autopark"
_APP_PW = "autopark_scratch_pw"

OWNER_A = "owner@a.autopark.test"
OTHER_A = "other@a.autopark.test"
GONE_A = "gone@a.autopark.test"
OWNER_B = "owner@b.autopark.test"
GATE_LABEL = "Approve the send"


def _node(nid: str, ntype: str, label: str | None = None, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": label or nid, "config": config or {}}}


def _gated(gates: int = 1) -> dict:
    """trigger -> gate [-> gate2] -> output."""
    from gateway.routes.workflows.engine.graph import compile_graph

    nodes = [_node("start", "trigger", config={"kind": "manual"})]
    ids = ["gate"] + [f"gate{i}" for i in range(2, gates + 1)]
    for nid in ids:
        nodes.append(_node(nid, "approval", GATE_LABEL if nid == "gate" else "Approve again"))
    nodes.append(_node("done", "output", config={"value": "end"}))
    chain = ["start", *ids, "done"]
    edges = [{"id": f"{a}->{b}", "source": a, "target": b} for a, b in itertools.pairwise(chain)]
    return compile_graph({"nodes": nodes, "edges": edges})


def _exec_sql_file(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


def _grant_app_role(ded_admin_url, dedicated: str) -> None:
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))


def _seed_member(conn, org: str, email: str, status: str = "active") -> None:
    conn.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid))"),
        {"e": email, "s": status, "o": org})


def _seed_workflow(conn, org: str, owner: str, name: str, serialized: dict) -> str:
    wid = str(uuid.uuid4())
    conn.execute(text(
        "INSERT INTO workflows (id, name, description, owner_email, status, "
        "latest_version, organization_id) VALUES (:id, :n, '', :o, 'published', 1, "
        "CAST(:org AS uuid))"), {"id": wid, "n": name, "o": owner, "org": org})
    conn.execute(text(
        "INSERT INTO workflow_versions (workflow_id, version, serialized, published_by, "
        "organization_id) VALUES (:w, 1, CAST(:s AS jsonb), 'seed', CAST(:org AS uuid))"),
        {"w": wid, "s": json.dumps(serialized), "org": org})
    return wid


def _seed_personal_root(conn, org: str, email: str) -> None:
    """A personal root with its four lanes, as ``ensure_personal_project``
    makes it. The inactive owner keeps a tree from the time they were active."""
    project = str(conn.execute(text(
        "INSERT INTO pm_projects (name, personal_owner, created_by, source, "
        "organization_id, owns_statuses) VALUES ('My work', :e, :e, 'manual', "
        "CAST(:o AS uuid), true) RETURNING id"), {"e": email, "o": org}).scalar_one())
    conn.execute(text(
        "INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by) "
        "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :e, :e)"),
        {"o": org, "p": project, "e": email})
    for pos, (name, cat) in enumerate((("Inbox", "backlog"), ("Next", "todo"),
                                       ("Doing", "in_progress"), ("Done", "done"))):
        conn.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, category, position, "
            "organization_id) VALUES (CAST(:p AS uuid), :n, :c, :pos, CAST(:o AS uuid))"),
            {"p": project, "n": name, "c": cat, "pos": (pos + 1) * 10, "o": org})


@pytest.fixture(scope="module")
def world():
    """A dedicated phase-4 catalog, a NOBYPASSRLS role and two orgs."""
    from tests.unit._tenant_ladder import apply_ladder

    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_autopark"
    _drop_database(admin_url, dedicated)
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    maint.dispose()
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn:
            _exec_sql_file(conn, (_GENERATED / phase).read_text(encoding="utf-8"))
    _grant_app_role(ded_admin_url, dedicated)

    ns = SimpleNamespace(admin_engine=eng)
    ns.org_a, ns.org_b = str(uuid.uuid4()), str(uuid.uuid4())
    ns.wf = {}
    with eng.begin() as conn:
        for oid, slug in ((ns.org_a, "autopark-a"), (ns.org_b, "autopark-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :s)"),
                {"id": oid, "s": slug})
        for email in (OWNER_A, OTHER_A):
            _seed_member(conn, ns.org_a, email)
        _seed_member(conn, ns.org_a, GONE_A, status="suspended")
        _seed_member(conn, ns.org_b, OWNER_B)
        _seed_personal_root(conn, ns.org_a, GONE_A)
        for name, org, owner, serialized in (
            ("Gate in A", ns.org_a, OWNER_A, _gated(1)),
            ("Two gates in A", ns.org_a, OWNER_A, _gated(2)),
            ("Gate in B", ns.org_b, OWNER_B, _gated(1)),
            ("Gone owner in A", ns.org_a, GONE_A, _gated(1)),
        ):
            ns.wf[name] = (_seed_workflow(conn, org, owner, name, serialized), org, serialized)
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield ns
    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def w(world, monkeypatch):
    """Both flags and the unattended list on for A and B. No run, decision,
    bell or subject task is left from the last test. The broker store is a
    spy, and so is the event of a new task."""
    orgs = f"{world.org_a},{world.org_b}"
    _flags(monkeypatch, park_orgs=orgs, decision_orgs=orgs)
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    world.listed = orgs
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=world.listed))
    world.proposals = []

    async def _submit(proposal: Any) -> dict:
        world.proposals.append(proposal)
        return {"action_id": str(uuid.uuid4())}

    monkeypatch.setattr(broker, "submit", _submit)
    world.created_events = []
    real_emit = personal.emit

    async def _emit(event: str, payload: dict) -> None:
        if event == "pm.task.created":
            world.created_events.append(payload)
        await real_emit(event, payload)

    monkeypatch.setattr(personal, "emit", _emit)
    world.executed = []
    real_execute = service._execute_run

    async def _execute(**kw: Any) -> None:
        world.executed.append({"run_id": kw["run_id"], "kind": kw["trigger_kind"]})
        await real_execute(**kw)

    monkeypatch.setattr(service, "_execute_run", _execute)
    world.marks = []
    real_engine = service.execute_workflow

    async def _engine(*a: Any, **kw: Any) -> Any:
        world.marks.append(no_person_present())
        return await real_engine(*a, **kw)

    monkeypatch.setattr(service, "execute_workflow", _engine)
    with world.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflow_runs"))
        c.execute(text("DELETE FROM pm_decisions"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_tasks WHERE source = 'automation'"))
    return world


async def _drain_runs() -> None:
    tasks = [t for t in asyncio.all_tasks()
             if t.get_name().startswith(("workflow-run:", "workflow-resume:"))]
    if tasks:
        await asyncio.wait(tasks, timeout=30)


def _on_app_role(w: Any, body: Any) -> Any:
    """Run *body* on the app-role engine, with no tenant in the context."""
    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _go() -> Any:
        async with tenant_engine_scope(w.app_dsn):
            try:
                return await asyncio.wait_for(body(), _WAIT)
            finally:
                await _drain_runs()

    return asyncio.run(_go())


def _start(w: Any, name: str, *, payload: dict | None = None) -> str:
    """Start one scheduled run of *name* in its own org, and wait for it."""
    wid, org, serialized = w.wf[name]

    async def body() -> str:
        return await service.start_run(
            workflow_id=wid, workflow_name=name, version=1, serialized=serialized,
            trigger_kind="schedule", trigger_payload=payload or {"scheduled_at": "x"},
            started_by="scheduler", organization_id=org)

    return _on_app_role(w, body)


def _one(w: Any, sql: str, **params: Any) -> Any:
    with w.admin_engine.connect() as c:
        return c.execute(text(sql), params).fetchone()


def _all(w: Any, sql: str, **params: Any) -> list[Any]:
    with w.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _run(w: Any, run_id: str) -> Any:
    return _one(w, "SELECT status, error FROM workflow_runs WHERE id = CAST(:r AS uuid)",
                r=run_id)


def _pauses(w: Any, run_id: str) -> list[Any]:
    return _all(w, "SELECT id, status, reason, snapshot FROM workflow_run_pauses "
                   "WHERE run_id = CAST(:r AS uuid) ORDER BY created_at", r=run_id)


def _decisions_of(w: Any, run_id: str) -> list[Any]:
    return _all(w, "SELECT * FROM pm_decisions WHERE payload ->> 'run_id' = :r "
                   "ORDER BY created_at", r=run_id)


def _snap(row: Any) -> dict:
    return row.snapshot if isinstance(row.snapshot, dict) else json.loads(row.snapshot)


def _park(w: Any, name: str = "Gate in A", **kw: Any) -> tuple[str, Any]:
    """A scheduled run that parked at its first gate on one decision."""
    run_id = _start(w, name, **kw)
    assert _run(w, run_id).status == "paused"
    made = _decisions_of(w, run_id)
    assert len(made) == 1, made
    return run_id, made[0]


def _principal(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]), organization_id=org)


async def _answer_in(org: str, email: str, decision_id: str, key: str) -> Any:
    from gateway.db import tenant_session

    async with tenant_session(org) as db:
        return await decisions.answer_decision(db, _principal(email, org), decision_id, key, None)


def _answer(w: Any, org: str, email: str, decision_id: str, key: str) -> Any:
    return _on_app_role(w, lambda: _answer_in(org, email, decision_id, key))


def _scan(w: Any, *, now: float | None = None) -> int:
    return _on_app_role(w, lambda: scheduler.scan_due_waits(now))


def _comments(w: Any, task_id: str) -> list[Any]:
    return _all(w, "SELECT created_by, body FROM pm_activities WHERE "
                   "task_id = CAST(:t AS uuid) AND type = 'comment' ORDER BY created_at",
                t=task_id)


def _expire(w: Any, decision_id: str) -> None:
    """Move the decision's clock into the past. The guard trigger refuses that
    change for each role, so the superuser skips triggers for one statement."""
    with w.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text(
            "UPDATE pm_decisions SET created_at = now() - interval '2 hours', "
            "expires_at = now() - interval '1 minute' WHERE id = CAST(:d AS uuid)"),
            {"d": decision_id})


def _race(monkeypatch, parties: int = 2) -> None:
    """Hold each racer at its first UPDATE of a pause until all of them came.

    Each racer has read the same pending pause by then. So the outcome is the
    compare-and-set alone, never the order the event loop chose."""
    state: dict[str, Any] = {"barrier": None}

    class _Held:
        def __init__(self, db: Any) -> None:
            self._db, self._held = db, False

        async def execute(self, clause: Any, *a: Any, **k: Any) -> Any:
            sql = " ".join(str(clause).split()).upper()
            if not self._held and sql.startswith("UPDATE WORKFLOW_RUN_PAUSES"):
                self._held = True
                if state["barrier"] is None:
                    state["barrier"] = asyncio.Barrier(parties)
                await asyncio.wait_for(state["barrier"].wait(), 30)
            return await self._db.execute(clause, *a, **k)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._db, name)

    real_run, real_tenant = service._run_session, scheduler._tenant_session

    @contextlib.asynccontextmanager
    async def run_session(org: str | None):
        async with real_run(org) as db:
            yield _Held(db)

    @contextlib.asynccontextmanager
    async def tenant(org: str | None = None):
        async with real_tenant(org) as db:
            yield _Held(db)

    monkeypatch.setattr(service, "_run_session", run_session)
    monkeypatch.setattr(scheduler, "_tenant_session", tenant)


def _resumes(w: Any, run_id: str) -> int:
    return sum(1 for e in w.executed if e["run_id"] == run_id) - 1


@_DB_GATE
class TestParkAndAskOnARealDatabase:
    def test_the_engine_cannot_bypass_rls(self, w):
        eng = create_engine(w.app_dsn, future=True)
        try:
            with eng.connect() as c:
                role = c.execute(text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user")).first()
                forced = c.execute(text(
                    "SELECT count(*) FROM pg_class WHERE relname IN "
                    "('pm_decisions', 'workflow_run_pauses', 'workflow_runs', 'pm_tasks') "
                    "AND relrowsecurity AND relforcerowsecurity")).scalar()
        finally:
            eng.dispose()
        assert role is not None and not role[0] and not role[1]
        assert forced == 4

    # ── F1 and the acceptance: one decision, then yes, no, expiry ───────

    def test_f1_a_scheduled_run_parks_with_one_decision_and_no_proposal(self, w):
        run_id, made = _park(w)
        wid = w.wf["Gate in A"][0]
        assert w.proposals == [], "a parked gate filed a broker proposal"
        assert (made.decider, made.asked_by, made.source) == (
            OWNER_A, f"system:workflow:{wid}", "auto")
        assert made.question == f'Let the workflow "Gate in A" go past "{GATE_LABEL}"?'
        pauses = _pauses(w, run_id)
        assert [(p.status, p.reason) for p in pauses] == [("pending", "approval")]
        snap = _snap(pauses[0])
        assert snap["decision_id"] == str(made.id) and snap["action_id"] is None
        assert snap["decider"] == OWNER_A
        assert made.payload == {"run_id": run_id, "pause_id": str(pauses[0].id),
                                "node_id": "gate", "workflow_id": wid}
        task = _one(w, "SELECT t.*, p.personal_owner FROM pm_tasks t JOIN pm_projects p "
                       "ON p.id = t.project_id WHERE t.id = :t", t=made.subject_id)
        assert task.personal_owner == OWNER_A and task.source == "automation"
        assert task.title == made.question and str(task.organization_id) == w.org_a
        assert [a.assignee for a in _all(
            w, "SELECT assignee FROM pm_task_assignees WHERE task_id = :t",
            t=made.subject_id)] == [OWNER_A]
        bells = _all(w, "SELECT recipient, kind FROM pm_notifications")
        assert [(b.recipient, b.kind) for b in bells] == [(OWNER_A, "decision")]
        # The loop guard: the subject task sent no event.
        assert w.created_events == []

    def test_a_yes_resumes_the_run_once(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        assert _scan(w) == 1
        assert _run(w, run_id).status == "succeeded"
        assert [p.status for p in _pauses(w, run_id)] == ["resolved"]
        assert _scan(w) == 0
        assert _resumes(w, run_id) == 1

    def test_f7_a_no_ends_the_run(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "no")
        assert _scan(w) == 1
        assert tuple(_run(w, run_id)) == ("cancelled", park.DECLINED)
        assert [p.status for p in _pauses(w, run_id)] == ["rejected"]
        assert _resumes(w, run_id) == 0
        # The answer wrote its own comment. The end adds no tell.
        assert [c.created_by for c in _comments(w, str(made.subject_id))] == [OWNER_A]

    def test_f7_an_expiry_ends_the_run_by_the_db_clock_and_tells_once(self, w):
        run_id, made = _park(w)
        # The DB clock says open. A Python clock far ahead changes nothing.
        assert _scan(w, now=1e12) == 0
        assert _run(w, run_id).status == "paused"
        _expire(w, str(made.id))
        # The DB clock says expired. A Python clock at 1970 changes nothing.
        assert _scan(w, now=0.0) == 1
        assert tuple(_run(w, run_id)) == ("cancelled", park.EXPIRED)
        assert [p.status for p in _pauses(w, run_id)] == ["rejected"]
        wid = w.wf["Gate in A"][0]
        told = _comments(w, str(made.subject_id))
        assert [(c.created_by, c.body) for c in told] == [
            (f"system:workflow:{wid}", park.EXPIRED_TEXT)]
        assert _scan(w) == 0
        assert len(_comments(w, str(made.subject_id))) == 1
        assert _resumes(w, run_id) == 0

    def test_f7_two_concurrent_expiry_scans_tell_once(self, w, monkeypatch):
        run_id, made = _park(w)
        _expire(w, str(made.id))
        _race(monkeypatch)

        async def body() -> list[int]:
            return list(await asyncio.gather(scheduler.scan_decided_pauses(w.org_a),
                                             scheduler.scan_decided_pauses(w.org_a)))

        assert sorted(_on_app_role(w, body)) == [0, 1]
        assert tuple(_run(w, run_id)) == ("cancelled", park.EXPIRED)
        assert len(_comments(w, str(made.subject_id))) == 1

    # ── F6: exactly one resume ──────────────────────────────────────────

    def test_f6_two_concurrent_yes_answers_give_one_answer_and_one_resume(
        self, w, monkeypatch,
    ):
        run_id, made = _park(w)

        async def answers() -> list[Any]:
            return list(await asyncio.gather(
                _answer_in(w.org_a, OWNER_A, str(made.id), "yes"),
                _answer_in(w.org_a, OWNER_A, str(made.id), "yes"),
                return_exceptions=True))

        got = _on_app_role(w, answers)
        refused = [g for g in got if isinstance(g, HTTPException)]
        assert len(refused) == 1 and refused[0].status_code == 409, got
        assert refused[0].detail == "decision_answered"
        _race(monkeypatch)

        async def scans() -> list[int]:
            return list(await asyncio.gather(scheduler.scan_decided_pauses(w.org_a),
                                             scheduler.scan_decided_pauses(w.org_a)))

        assert sorted(_on_app_role(w, scans)) == [0, 1]
        assert _resumes(w, run_id) == 1
        assert _run(w, run_id).status == "succeeded"

    def test_f6_a_scan_and_the_broker_door_resume_once(self, w, monkeypatch):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        _race(monkeypatch)

        async def body() -> list[Any]:
            return list(await asyncio.gather(
                scheduler.scan_decided_pauses(w.org_a),
                service.resume_run(run_id, resumed_by="approvals-inbox",
                                   organization_id=w.org_a)))

        scanned, door = _on_app_role(w, body)
        assert (scanned, bool(door.get("ok"))) in ((1, False), (0, True)), (scanned, door)
        assert _resumes(w, run_id) == 1

    # ── F8: the resumed run stays marked ────────────────────────────────

    def test_f8_a_resumed_run_stays_marked_and_its_second_gate_parks_again(self, w):
        run_id, first = _park(w, "Two gates in A")
        _answer(w, w.org_a, OWNER_A, str(first.id), "yes")
        assert _scan(w) == 1
        assert _run(w, run_id).status == "paused"
        made = _decisions_of(w, run_id)
        assert len(made) == 2 and made[1].answered_at is None
        assert made[1].payload["node_id"] == "gate2"
        assert [p.status for p in _pauses(w, run_id)] == ["resolved", "pending"]
        assert w.proposals == []
        assert [e["kind"] for e in w.executed] == ["schedule", "schedule"]
        assert w.marks == [True, True]

    # ── F3: the run with no person cannot answer ────────────────────────

    def test_f3_a_marked_turn_cannot_answer_and_a_person_can(self, w):
        run_id, made = _park(w)

        async def marked() -> Any:
            with no_person_scope():
                return await _answer_in(w.org_a, OWNER_A, str(made.id), "yes")

        with pytest.raises(HTTPException) as refused:
            _on_app_role(w, marked)
        assert refused.value.status_code == 403
        assert refused.value.detail["error"] == NO_PERSON_PRESENT
        row = _one(w, "SELECT answered_at FROM pm_decisions WHERE id = :d", d=made.id)
        assert row.answered_at is None
        assert _scan(w) == 0
        assert [p.status for p in _pauses(w, run_id)] == ["pending"]
        assert _run(w, run_id).status == "paused"
        # The same act by the person works, so the refusal is not a break.
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        assert _scan(w) == 1 and _run(w, run_id).status == "succeeded"

    def test_f3_an_approved_word_in_the_payload_or_the_snapshot_resumes_nothing(self, w):
        run_id, _made = _park(w, payload={"approved": True, "answer_key": "yes",
                                          "decision": "yes", "answered_by": OWNER_A})
        with w.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE workflow_run_pauses SET snapshot = snapshot || CAST(:x AS jsonb) "
                "WHERE run_id = CAST(:r AS uuid)"),
                {"r": run_id, "x": json.dumps({
                    "answer_key": "yes", "answered_by": OWNER_A, "answered_at": "now",
                    "approved": True})})
            c.execute(text(
                "UPDATE workflow_runs SET node_results = CAST(:x AS jsonb) "
                "WHERE id = CAST(:r AS uuid)"),
                {"r": run_id, "x": json.dumps({"start": {"status": "ok", "output": {
                    "approved": True, "answer_key": "yes"}}})})
        assert _scan(w) == 0
        assert _run(w, run_id).status == "paused"
        assert _resumes(w, run_id) == 0

    # ── F4: the decider is the owner ────────────────────────────────────

    def test_f4_the_decider_is_the_workflow_owner(self, w):
        from acb_common import job_member_scope

        wid, org, serialized = w.wf["Gate in A"]

        async def body() -> str:
            with job_member_scope(OTHER_A, app="workflows"):
                return await service.start_run(
                    workflow_id=wid, workflow_name="Gate in A", version=1,
                    serialized=serialized, trigger_kind="schedule",
                    trigger_payload={"decider": OTHER_A, "owner_email": OTHER_A},
                    started_by=OTHER_A, organization_id=org)

        run_id = _on_app_role(w, body)
        made = _decisions_of(w, run_id)
        assert [m.decider for m in made] == [OWNER_A]
        # The member of the payload cannot answer it.
        with pytest.raises(HTTPException) as refused:
            _answer(w, w.org_a, OTHER_A, str(made[0].id), "yes")
        assert refused.value.status_code == 404

    # ── The org wall ────────────────────────────────────────────────────

    def test_org_b_cannot_see_resume_or_end_org_a_parked_run(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        w.listed = w.org_b
        assert _scan(w) == 0
        assert _run(w, run_id).status == "paused"
        assert _on_app_role(w, lambda: scheduler.scan_decided_pauses(w.org_b)) == 0
        door = _on_app_role(w, lambda: service.resume_run(
            run_id, resumed_by="approvals-inbox", organization_id=w.org_b))
        assert door == {"ok": False, "error": f"no run {run_id}"}

        async def peek() -> int:
            from gateway.db import tenant_session

            async with tenant_session(w.org_b) as db:
                return int((await db.execute(text(
                    "SELECT count(*) FROM pm_decisions WHERE id = CAST(:d AS uuid)"),
                    {"d": str(made.id)})).scalar())

        assert _on_app_role(w, peek) == 0
        with pytest.raises(HTTPException) as refused:
            _answer(w, w.org_b, OWNER_B, str(made.id), "no")
        assert refused.value.status_code == 404
        assert _run(w, run_id).status == "paused"
        w.listed = f"{w.org_a},{w.org_b}"
        assert _scan(w) == 1 and _run(w, run_id).status == "succeeded"

    # ── One transaction, and the broker door ────────────────────────────

    def _assert_broker_door(self, w: Any, run_id: str) -> None:
        assert _run(w, run_id).status == "paused"
        assert _decisions_of(w, run_id) == []
        assert _all(w, "SELECT id FROM pm_tasks WHERE source = 'automation'") == []
        assert _all(w, "SELECT id FROM pm_notifications") == []
        assert [p.action for p in w.proposals] == ["workflow.resume_run"]
        pauses = _pauses(w, run_id)
        assert len(pauses) == 1
        snap = _snap(pauses[0])
        assert snap["action_id"] and "decision_id" not in snap

    def test_a_crash_before_commit_leaves_no_decision_and_no_task(self, w, monkeypatch):
        """``ask_decision`` does not commit by itself: a crash after it leaves
        no decision, no task and no bell, and the gate takes the broker door."""

        async def _crash(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("the pause insert failed")

        monkeypatch.setattr(park, "_insert_pause", _crash)
        self._assert_broker_door(w, _start(w, "Gate in A"))

    def test_an_inactive_owner_gets_no_decision(self, w):
        self._assert_broker_door(w, _start(w, "Gone owner in A"))

    def test_a_decider_who_cannot_open_the_task_is_refused_by_the_ask(self, w, monkeypatch):
        """The second wall. Past the owner check, the ask itself refuses a
        decider who cannot open the subject task, and the rollback takes the
        task that the park had made."""

        async def _pass(*_a: Any) -> bool:
            return True

        monkeypatch.setattr(park, "_owner_in_org", _pass)
        self._assert_broker_door(w, _start(w, "Gone owner in A"))

    @pytest.mark.parametrize("off", ["auto_park_orgs", "decisions_orgs"])
    def test_f2_with_a_flag_off_the_gate_files_the_proposal(self, w, monkeypatch, off):
        monkeypatch.setattr(get_settings(), off, "")
        self._assert_broker_door(w, _start(w, "Gate in A"))

    def test_a_park_in_org_b_stays_in_org_b(self, w):
        run_id, made = _park(w, "Gate in B")
        assert made.decider == OWNER_B and str(made.organization_id) == w.org_b
        _answer(w, w.org_b, OWNER_B, str(made.id), "yes")
        assert _on_app_role(w, lambda: scheduler.scan_decided_pauses(w.org_a)) == 0
        assert _scan(w) == 1 and _run(w, run_id).status == "succeeded"


# ── The guard of this suite ─────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_auto_park\.py \\?$", workflow, re.M)
