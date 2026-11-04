"""Park and ask: the approval gate of a run with no person present (WS-AUTO AUTO-7b).

Spec: ``project-docs/specs/proactive_agent.md`` §4.2. Decisions: D161, D131
rule 4.

A run of ``service.UNATTENDED_TRIGGERS`` carries the mark of
``acb_common.no_person`` (AUTO-7a). So the run cannot approve its own gate.
AUTO-7a filed a broker proposal that any approver could settle. With
``AUTO_PARK_ORGS`` on, the gate asks ONE person instead: the owner of the
workflow, ``workflows.owner_email``, read in the org of the run.

**One transaction.** ``service._hold_at_gate`` opens one ``_run_session(org)``
and calls :func:`park_with_decision`. That call makes the subject task, asks
the decision and writes the pause row. A failure rolls all three back, and
the gate takes the broker door of AUTO-7a. The gate never files both.

**The subject.** A private task in the owner's personal project, one for each
parked gate. ``create_personal_task(..., announce=False)`` makes it, so no
``pm.task.created`` event starts a workflow. Without that, a workflow with an
event trigger on that event could park and start itself again with no end.

**The question.** :func:`park_question` builds it from the stored workflow
name and the node label only. A trigger payload or a node output never
reaches it.

**Who answers.** Only the decider, through the answer route of
``routes/projects/decisions.py``. That route refuses a run with no person
present before any read. This module never writes ``answered_by``,
``answer_key`` or ``answered_at``, and never calls ``answer_decision``.

**The settle.** ``scheduler.scan_decided_pauses`` reads the decision ROW in the
org of the run. :func:`outcome_of` names the outcome from that row only:
``yes`` from the decider resumes, any other answer ends the run, and the DB
clock ends an unanswered one. The snapshot, the trigger payload and a node
output never decide. :func:`end_parked` ends a run with a compare-and-set on
the pause row. Only the winner tells the member, once.

**No score for any person (ALN-Q5).** Nothing here counts, groups or ranks a
person.

The flag ``AUTO_PARK_ORGS`` (default empty) has one reader,
:func:`park_allowed`. ``DECISIONS_ORGS`` must list the org too.

Fence: ``tests/unit/test_auto_park.py`` (R8).
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from acb_common import get_settings
from sqlalchemy import text

#: The two options of a parked gate. The keys are the only answer vocabulary.
YES, NO = "yes", "no"
OPTIONS: tuple[dict[str, str], ...] = (
    {"key": YES, "label": "Yes, let the run go on"},
    {"key": NO, "label": "No, stop the run"},
)
#: How long a parked gate waits for its answer. It is inside the bounds of
#: ``decisions.MIN_LIFE`` and ``decisions.MAX_LIFE``.
PARK_LIFE = timedelta(days=7)
#: The longest question. ``decisions.QUESTION_MAX`` and the CHECK hold it too.
QUESTION_MAX = 140
_NAME_MAX = 60
_LABEL_MAX = 48

#: What :func:`outcome_of` can name.
WAIT, RESUME, DECLINE, EXPIRE = "wait", "resume", "decline", "expire"
#: The error of a run that a parked gate ended.
DECLINED = "decision declined"
EXPIRED = "decision expired"

#: The description of the subject task.
SUBJECT_TEXT = (
    "A workflow run stopped at an approval step, and it waits for your answer. "
    "Answer Yes to let it go on, or No to stop it. If nobody answers in 7 days, "
    "the run stops."
)
#: The comment that tells the member that the question expired.
EXPIRED_TEXT = "Nobody answered this question in time, so the workflow run stopped."


class ParkRefused(Exception):
    """A fact that the park needs is missing. The gate takes the broker door."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ParkLost(Exception):
    """The run moved under the end of a parked gate. The caller rolls back."""


# ── The flag ────────────────────────────────────────────────────────────────


def park_allowed(organization_id: str | None) -> bool:
    """True when ``AUTO_PARK_ORGS`` lists the org, or holds ``*``, AND
    ``DECISIONS_ORGS`` lists it too. Default OFF.

    The org is the stored org of the run, never request input (R5). It reads
    ``get_settings()`` on each call, so a flag reload applies a change.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "auto_park_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    if "*" not in listed and org not in listed:
        return False
    from gateway.routes.projects.decisions import decisions_allowed

    return decisions_allowed(org)


# ── The ask ─────────────────────────────────────────────────────────────────


def _clip(value: str, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def park_question(workflow_name: str, node_label: str) -> str:
    """The question of a parked gate, from two STORED fields only."""
    name = _clip(workflow_name, _NAME_MAX) or "a workflow"
    label = _clip(node_label, _LABEL_MAX) or "its approval step"
    return f'Let the workflow "{name}" go past "{label}"?'[:QUESTION_MAX]


def asker(workflow_id: str) -> str:
    """The actor of the ask and of the tell, ``system:workflow:<id>``."""
    from gateway.routes.projects.automation import workflow_actor

    return workflow_actor(workflow_id)


_INSERT_PAUSE = (
    "INSERT INTO workflow_run_pauses (id, run_id, node_id, snapshot, reason, status) "
    "VALUES (CAST(:id AS uuid), :run_id, :node_id, :snapshot ::jsonb, 'approval', "
    "'pending')"
)


async def _insert_pause(
    db: Any, *, pause_id: str, run_id: str, node_id: str, snapshot: dict[str, Any],
) -> None:
    await db.execute(
        text(_INSERT_PAUSE),
        {
            "id": pause_id,
            "run_id": run_id,
            "node_id": node_id,
            "snapshot": json.dumps(snapshot, default=str),
        },
    )


async def _owner_in_org(db: Any, who: str, org: str) -> bool:
    """True when *who* is an ACTIVE member of *org*.

    ``core.resolve_organization_id`` is the one directory seam, and it answers
    None for a member who is not active. A member of another org resolves to
    that org, so the compare refuses them.
    """
    from gateway.routes.projects.core import resolve_organization_id

    member_org = await resolve_organization_id(db, who)
    return bool(member_org) and str(member_org).strip().lower() == org


async def park_with_decision(
    db: Any,
    *,
    organization_id: str,
    owner: str,
    workflow_id: str,
    workflow_name: str,
    run_id: str,
    node_id: str,
    node_label: str,
    snapshot: dict[str, Any],
) -> dict[str, str]:
    """Make the subject task, ask the owner and write the pause, on *db*.

    *db* is the caller's ``_run_session(org)``, and the caller binds the org
    for :func:`decisions.ask_decision`. Nothing here commits. Raises
    :class:`ParkRefused` or :class:`decisions.DecisionRefused` when a fact is
    missing, and the caller's rollback then removes each row.
    """
    from gateway.routes.projects.core import load_default_status
    from gateway.routes.projects.decisions import ask_decision
    from gateway.routes.projects.personal import (
        create_personal_task,
        ensure_personal_project,
    )

    org = str(organization_id or "").strip().lower()
    who = str(owner or "").strip().lower()
    if not org or "@" not in who:
        raise ParkRefused("no_owner")
    # The owner must be an ACTIVE member of THIS org, before any write. The
    # ask holds a second wall: a decider who cannot open the task is refused.
    if not await _owner_in_org(db, who, org):
        raise ParkRefused("owner_not_member")

    question = park_question(workflow_name, node_label)
    project = await ensure_personal_project(db, who)
    project_id = str(project.id)
    status = await load_default_status(db, project_id)
    task = await create_personal_task(
        db, who, project_id, project_id, str(status.id),
        {"title": question, "description": SUBJECT_TEXT, "source": "automation"},
        announce=False,
    )
    task_id = str(task.id)
    pause_id = str(uuid.uuid4())
    decision = await ask_decision(
        db,
        task_id=task_id,
        decider=who,
        question=question,
        options=[dict(o) for o in OPTIONS],
        asked_by=asker(workflow_id),
        source="auto",
        payload={
            "run_id": run_id,
            "pause_id": pause_id,
            "node_id": node_id,
            "workflow_id": workflow_id,
        },
        expires_in=PARK_LIFE,
    )
    await _insert_pause(
        db,
        pause_id=pause_id,
        run_id=run_id,
        node_id=node_id,
        snapshot={
            **snapshot,
            "decision_id": decision["id"],
            "subject_task_id": task_id,
            "decider": who,
        },
    )
    return {"decision_id": decision["id"], "task_id": task_id, "pause_id": pause_id}


# ── The settle ──────────────────────────────────────────────────────────────

#: The parked gates of one org that wait on a decision. The org is bound by
#: the session, and the outer predicate names it again.
PARKED_SQL = (
    "SELECT p.id, p.run_id, p.snapshot, r.organization_id, r.workflow_id, "
    "       r.workflow_name "
    "  FROM workflow_run_pauses p "
    "  JOIN workflow_runs r ON r.id = p.run_id "
    " WHERE p.reason = 'approval' AND p.status = 'pending' "
    "   AND r.status = 'paused' "
    "   AND p.snapshot ->> 'decision_id' IS NOT NULL "
    "   AND r.organization_id = CAST(:org AS uuid)"
)

#: One decision of a parked gate. It must be an ``auto`` decision asked for
#: THIS run. The expiry is read with the DB clock.
_DECISION_SQL = (
    "SELECT id, decider, answered_by, answer_key, answered_at, subject_id, "
    "       (expires_at <= now()) AS is_expired "
    "  FROM pm_decisions "
    " WHERE id = CAST(:id AS uuid) AND source = 'auto' "
    "   AND payload ->> 'run_id' = :run_id"
)


def decision_id_of(snapshot: Any) -> str | None:
    """The decision id that a parked gate's snapshot holds, or None."""
    if not isinstance(snapshot, dict):
        return None
    raw = snapshot.get("decision_id")
    if not raw:
        return None
    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError):
        return None


async def read_decision(db: Any, decision_id: str, run_id: str) -> Any | None:
    """The decision row, in the org that *db* binds, or None."""
    return (
        await db.execute(text(_DECISION_SQL), {"id": decision_id, "run_id": str(run_id)})
    ).fetchone()


def outcome_of(decision: Any | None) -> str:
    """What the stored decision row says. Nothing else decides.

    ``resume`` only for the key ``yes`` from the decider. Any other answer is
    ``decline``. An unanswered row is ``expire`` once the DB clock passed its
    ``expires_at``, and ``wait`` before that. A missing row is ``wait``: the
    run stays parked, and nothing resumes it.
    """
    if decision is None:
        return WAIT
    if decision.answered_at is not None:
        who = str(decision.answered_by or "").strip().lower()
        decider = str(decision.decider or "").strip().lower()
        if decision.answer_key == YES and who and who == decider:
            return RESUME
        return DECLINE
    return EXPIRE if decision.is_expired else WAIT


async def end_parked(
    db: Any,
    *,
    pause_id: str,
    run_id: str,
    outcome: str,
    workflow_id: str,
    subject_task_id: str | None,
    decision_id: str,
) -> bool:
    """End one parked run: the pause ``rejected``, the run ``cancelled``.

    The pause UPDATE is the compare-and-set. A loser returns False and writes
    nothing. The run UPDATE holds the matching guard, and a loss there raises
    :class:`ParkLost`, so the caller rolls the pause back too. Only the
    winner of an expiry writes the tell, one comment on the subject task.
    """
    if outcome not in (DECLINE, EXPIRE):
        raise ValueError(f"end_parked takes {DECLINE} or {EXPIRE}, not {outcome}")
    won = (
        await db.execute(
            text(
                "UPDATE workflow_run_pauses SET status = 'rejected', resolved_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'pending' RETURNING id"
            ),
            {"id": pause_id},
        )
    ).fetchone()
    if won is None:
        return False
    ran = (
        await db.execute(
            text(
                "UPDATE workflow_runs SET status = 'cancelled', error = :error, "
                "finished_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'paused' RETURNING id"
            ),
            {"id": run_id, "error": DECLINED if outcome == DECLINE else EXPIRED},
        )
    ).fetchone()
    if ran is None:
        raise ParkLost(run_id)
    if outcome == EXPIRE and subject_task_id:
        from gateway.routes.projects.core import record_activity

        await record_activity(
            db,
            activity_type="comment",
            created_by=asker(workflow_id),
            task_id=subject_task_id,
            body=EXPIRED_TEXT,
            meta={"decision_id": decision_id},
            automation=True,
        )
    return True
