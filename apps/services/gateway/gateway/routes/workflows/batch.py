"""The Studio batch node of a workflow (WS-STU E8b, task 3096).

Spec: ``project-docs/specs/ai_studio.md`` §14 E8b. The store is
``gateway/studio/batch_store.py`` (E8a), and the door is
``routes/studio/invoke.run_workflow_batch``.

**The node.** A ``studio_batch`` node holds a literal list (``items``) of
writes of one action of a Studio package. It follows an approval node
directly, and that approval node leads to the batch alone
(``engine/graph.py``). The run is a schedule run with no person present.

**The park.** ``park.park_with_decision`` calls :func:`park_batch` when the
gate clears a batch. It plans the list as the owner, opens the batch through
E8a with ``run_id`` and ``node_id``, and returns the payload keys that bind the
batch id and its list digest to the ONE decision. The task, the decision, the
pause and the batch commit in one transaction, or none of them does. The park
refuses an org that is not ready (:func:`org_ready`), so no batch opens for it.

**The source.** The list is the literal ``items`` of the run's pinned graph:
a frozen input. No run changes it, so the batch can never change its own
source. A list built from records of the object store is NOT a source of E8b.

**The resume.** A stop in a chunk leaves the run ``failed`` (the startup sweep
``reconcile_orphaned_runs``, a timeout of the node, or a busy store) and the
batch ``open``. ``scheduler.scan_open_batches`` finds such a batch, reads the
bound decision again (:func:`approved_batch`) and needs a yes. Then
``service.start_batch_run`` writes a new run row and moves ``run_id`` with the
compare-and-set of ``batch_store.rebind_run``, in one transaction. The new run
replays each node before the batch and starts AT the batch, which goes on from
the first pending item. A run that failed with no yes is never resumed.

**The bound.** At most :data:`MAX_RESUMES` new runs for one batch. After that,
the scan ends the item that blocks with ``query_timeout``
(``batch_store.end_stuck_item``), and the batch ends ``failed``.

The flag ``STUDIO_BATCH_ORGS`` (default empty, no ``*``) has ONE reader,
:func:`batch_allowed`. Fence: ``tests/unit/test_studio_workflow_batch.py``
(no database) and ``tests/unit/test_studio_workflow_batch_r8.py`` (R8).
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import Any

from acb_common import get_settings
from gateway.routes.workflows import park, unattended
from sqlalchemy import text

#: The most new runs that the batch scan starts for one batch.
MAX_RESUMES = 3
#: The keys of the decision payload that bind the batch (the park writes them).
BATCH_ID = "batch_id"
LIST_DIGEST = "list_digest"
#: The ``started_by`` of a run that the batch scan starts.
STARTED_BY = "scheduler:batch"

#: Why a park refused a batch of an org that is not ready. The gate then fails
#: the run and files no broker card, as for an unresolved write.
BATCH_OFF = "batch_off"
#: The error of a run whose gate clears a batch that the org may not run.
BATCH_OFF_ERROR = (
    "The approval step clears a Studio batch, and batches are not turned on for "
    "this organization, so the run stopped. Nothing was written."
)
_NAME_MAX = 32
_OBJECT_MAX = 24
_PACKAGE_MAX = 32


# ── The flag ────────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=16)
def _parse(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def batch_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_BATCH_ORGS`` lists the org. Default OFF, and ``*``
    is not a wildcard. The ONE reader of the setting. The org is the stored
    org of the run or of the scan, never request input (R5). It reads the
    settings on each call, so a SIGHUP reload applies a change."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "studio_batch_orgs", "") or "")
    return org in {o for o in _parse(raw) if o != "*"}


def org_ready(organization_id: str | None) -> bool:
    """True when the org may park, run and resume a batch: the batch flag, the
    unattended list (only its scans resume a run), the park flags
    (``AUTO_PARK_ORGS`` and ``DECISIONS_ORGS``) and ``STUDIO_ORGS``."""
    from gateway.routes.studio import flags as studio_flags

    return (batch_allowed(organization_id) and unattended.org_allowed(organization_id)
            and park.park_allowed(organization_id)
            and studio_flags.studio_allowed(organization_id))


# ── The graph ───────────────────────────────────────────────────────────────


def batch_below(serialized: Any, node_id: str) -> dict[str, Any] | None:
    """The ``studio_batch`` block that the gate *node_id* clears, or None."""
    from gateway.routes.workflows.engine.graph import runtime_gate_of

    if not isinstance(serialized, dict):
        return None
    for block in serialized.get("blocks") or []:
        if str(block.get("type") or "") == "studio_batch" and runtime_gate_of(
                serialized, str(block.get("id"))) == node_id:
            return block
    return None


def pinned_config(serialized: Any, node_id: str) -> dict[str, Any]:
    """The config of *node_id* in the run's PINNED graph, never the draft."""
    if not isinstance(serialized, dict):
        return {}
    for block in serialized.get("blocks") or []:
        if str(block.get("id")) == node_id and isinstance(block.get("config"), dict):
            return dict(block["config"])
    return {}


# ── The park ────────────────────────────────────────────────────────────────


def _clip(value: Any, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def batch_question(workflow_name: str, plan: Any) -> str:
    """The question of a gate that clears a batch. It names the verb, the
    count, the object and the package, from stored fields and the package."""
    from gateway.routes.studio.invoke import WORKFLOW_VERBS

    name = _clip(workflow_name, _NAME_MAX) or "a workflow"
    verb = WORKFLOW_VERBS.get(plan.op, plan.op)
    obj = _clip(plan.object, _OBJECT_MAX)
    package = _clip(plan.package_name, _PACKAGE_MAX) or "a Studio app"
    return f'Let "{name}" {verb} {len(plan.items)} records of {obj} in "{package}"?'[
        :park.QUESTION_MAX]


def batch_detail(plan: Any, batch_id: str) -> str:
    """The description of the subject task: the action, the count and the
    digest of the list. It holds no value of an item (E8c shows the list)."""
    return "\n".join([
        park.SUBJECT_TEXT, "",
        f'The batch: {plan.uses} in the app "{plan.package_name}", '
        f"{len(plan.items)} items, run in chunks of at most 50 writes.",
        f"Batch: {batch_id}",
        f"List digest: {plan.list_digest}",
    ])


async def park_batch(db: Any, *, organization_id: str, owner: str, workflow_name: str,
                     run_id: str, block: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    """Plan the list as the owner and open its batch, on *db* (E8b).

    *db* is the session of the park, and nothing here commits. Returns the
    question, the description and the payload keys of the decision. Raises
    :class:`park.ParkRefused`: :data:`BATCH_OFF` for an org that is not ready,
    and ``park.WRITE_UNRESOLVED`` for a list that does not resolve as the
    owner or that the store refuses. The caller's rollback then removes every
    row of the park.
    """
    from gateway.routes.studio.invoke import plan_workflow_batch
    from gateway.studio import batch_store

    if not org_ready(organization_id):
        raise park.ParkRefused(BATCH_OFF)
    config = block.get("config") if isinstance(block.get("config"), dict) else {}
    plan = await plan_workflow_batch(
        db, member=owner, package_id=config.get("package_id"), action=config.get("action"),
        items=config.get("items"))
    if plan is None:
        raise park.ParkRefused(park.WRITE_UNRESOLVED)
    try:
        async with db.begin_nested():
            opened = await batch_store.open_batch(
                db, owner, package_id=plan.package_id, action=str(config.get("action")),
                items=list(plan.items), run_id=run_id, node_id=str(block.get("id")),
                expect_digest=plan.list_digest)
    except batch_store.BatchStoreError:
        raise park.ParkRefused(park.WRITE_UNRESOLVED) from None
    return (batch_question(workflow_name, plan), batch_detail(plan, opened.id),
            {BATCH_ID: opened.id, LIST_DIGEST: opened.list_digest})


# ── The bound decision ──────────────────────────────────────────────────────


#: The ONE decision that binds a batch. It must be an ``auto`` decision of a
#: park, and the payload names the batch. The session binds the org.
_BATCH_DECISION_SQL = (
    "SELECT id, decider, answered_by, answer_key, answered_at, subject_id, payload, "
    "       (expires_at <= now()) AS is_expired "
    "  FROM pm_decisions "
    " WHERE source = 'auto' AND payload ->> 'batch_id' = :bid"
)


@dataclass(frozen=True)
class BoundDecision:
    """The yes that binds a batch, as the decision row holds it."""

    decision_id: str
    gate: str
    origin_run_id: str
    pause_id: str
    list_digest: str


def _payload(raw: Any) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


async def approved_batch(db: Any, *, organization_id: str, owner: str,
                         batch: Any) -> BoundDecision | None:
    """The yes of the owner that binds *batch*, or None (E8b). *db* binds the
    run's org.

    It reads the decision ROW again: exactly one ``auto`` decision names the
    batch. :func:`park.outcome_of` must give ``resume``, the decider must be
    *owner* (``workflows.owner_email``, read in the same org), the owner must
    be an ACTIVE member of the org now, and the payload must bind the list
    digest of the batch. The snapshot, the trigger payload and a node output
    never decide. The batch scan and the node both call it."""
    org = str(organization_id or "").strip().lower()
    who = str(owner or "").strip().lower()
    if not org or "@" not in who or batch is None:
        return None
    rows = (await db.execute(text(_BATCH_DECISION_SQL), {"bid": str(batch.id)})).fetchall()
    if len(rows) != 1:
        return None
    decision = rows[0]
    if park.outcome_of(decision) != park.RESUME:
        return None
    if str(decision.decider or "").strip().lower() != who:
        return None
    if not await park._owner_in_org(db, who, org):
        return None
    payload = _payload(decision.payload)
    if (str(payload.get(BATCH_ID) or "") != str(batch.id)
            or str(payload.get(LIST_DIGEST) or "") != str(batch.list_digest)):
        return None
    return BoundDecision(
        decision_id=str(decision.id), gate=str(payload.get("node_id") or ""),
        origin_run_id=str(payload.get("run_id") or ""),
        pause_id=str(payload.get("pause_id") or ""),
        list_digest=str(batch.list_digest))


# ── The scan ────────────────────────────────────────────────────────────────


#: The open batches of one org whose run failed. The session binds the org,
#: and both predicates name it again.
#: ⚠️ Only a SCHEDULE run. An event or a webhook run has no person present
#: too, so its gate can park and open a batch, and its node then refuses
#: (``not_scheduled``). A new run is a schedule run, so a resume of such a
#: batch would skip that refusal. Its batch stays open and idle.
OPEN_SQL = (
    "SELECT b.id, b.run_id, b.node_id, b.resumes, r.workflow_id, r.workflow_name, "
    "       r.version, r.node_results, r.organization_id "
    "  FROM studio_batches b JOIN workflow_runs r ON r.id = b.run_id "
    " WHERE b.status = 'open' AND r.status = 'failed' AND r.trigger_kind = 'schedule' "
    "   AND b.organization_id = CAST(:org AS uuid) "
    "   AND r.organization_id = CAST(:org AS uuid)"
)

#: The pause row of the park, which holds the snapshot of the first run.
_PAUSE_SQL = (
    "SELECT snapshot FROM workflow_run_pauses "
    " WHERE id = CAST(:pid AS uuid) AND run_id = CAST(:run AS uuid) AND reason = 'approval'"
)


async def park_snapshot(db: Any, bound: BoundDecision) -> dict[str, Any] | None:
    """The snapshot of the park that the decision names: the pinned graph, the
    trigger payload and the variables of the first run, or None."""
    from gateway.routes.workflows.core import parse_jsonb

    row = (await db.execute(text(_PAUSE_SQL), {
        "pid": bound.pause_id, "run": bound.origin_run_id})).fetchone()
    if row is None:
        return None
    snapshot = parse_jsonb(row.snapshot, {}) or {}
    return snapshot if isinstance(snapshot.get("serialized"), dict) else None


def replayed(node_results: Any, node_id: str) -> dict[str, Any]:
    """The stored outputs of the nodes that ran before the stop, minus the
    batch node. The new run passes each through with no second side effect."""
    from gateway.routes.workflows.core import parse_jsonb

    results = parse_jsonb(node_results, {}) or {}
    return {
        nid: res.get("output")
        for nid, res in results.items()
        if nid != node_id and isinstance(res, dict) and res.get("status") == "ok"
    }
