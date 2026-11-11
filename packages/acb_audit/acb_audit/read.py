"""The reader of the acts of one member's day (WS-BRIEF BR-7b-1).

Spec: ``project-docs/specs/core_brief.md`` §8.1 to §8.3. The close-out of
18:30 (BR-7b-2) lists each act that Mira or an automation ran today. This
module is the one read of those acts from ``audit_event``.

**What counts as an act.** :data:`ACT_ACTIONS` and :data:`ACT_PREFIXES` are
the allow-list of §8.1. A new action is not an act until a person adds it
here. :data:`EXCLUDED` names the rows that §8.1 keeps out by name, and the
allow-list never holds one of them. A row that holds a ``decision`` other
than ``allowed`` is a refusal, so it is not an act (``agent_door_call``
writes a row for a refused call too).

**The member.** The key is ``payload.member``. The WRITER sets it from the
run's user. :func:`acts` takes the member from its caller, which got it from
the run or the session, never from request input (R5, contract R11). A row
with no ``payload.member`` matches no member, so it is never listed.

**Tenancy (R5).** :func:`acts` opens ``acb_graph.tenant_session(org)``, the
sync seam that binds ``app.tenant_id`` for one transaction. FORCE RLS binds
each statement, and the SQL also filters on the bound org. With no org the
seam raises ``TenantUnbound``. ⚠️ The column ``audit_event.organization_id``
exists after the phase-4 promotion only, so on a database that the ladder
built without the generated phases the read fails. It never falls back to
an unbound read.

The read is sync, as the ``acb_graph`` engine is. An async caller runs it in
``asyncio.to_thread``. Fence: ``tests/unit/test_audit_acts_read_r8.py``
(the allow-list with no database, and R8 with two orgs and the app role).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: The actions that are acts (§8.1). ``mutation_commit_auto_pushed`` and
#: each ``execute:<action>`` give no row for a tenant yet, because their
#: writers set no org and no member (Metorite task 2189). They are on the
#: list so that they show once the writers are fixed.
ACT_ACTIONS: frozenset[str] = frozenset({
    "agent_door_call", "agent_push_by_reviewer", "mutation_commit_auto_pushed"})
#: The prefixes of the actions that are acts: ``execute:<action>`` of the
#: Action Broker. ``execute_refused:<action>`` does not match.
ACT_PREFIXES: tuple[str, ...] = ("execute:",)
#: The actions that §8.1 keeps out by name: a usage row, run bookkeeping, a
#: warning, and a label.
EXCLUDED: frozenset[str] = frozenset({
    "llm_completion", "agent_run_complete", "agent_run_error",
    "agent_self_commit_detected", "email_triage"})
#: The one ``decision`` of a row that is an act. A row with another
#: decision is a refusal.
ALLOWED_DECISION = "allowed"
#: The most acts that one read gives.
MAX_ACTS = 50

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_ACTS_SQL = (
    "SELECT id::text, at, actor, action, target, payload FROM audit_event "
    f"WHERE organization_id = {_BOUND_ORG} "
    "AND lower(payload->>'member') = :m "
    "AND at >= :since AND at < :until "
    "AND (action = ANY(CAST(:actions AS text[])) OR action LIKE ANY(CAST(:prefixes AS text[]))) "
    "AND NOT (action = ANY(CAST(:excluded AS text[]))) "
    "AND (payload->>'decision' IS NULL OR payload->>'decision' = :allowed) "
    "ORDER BY at, id LIMIT :n"
)


def is_act(action: Any, payload: Any = None) -> bool:
    """True when a row with *action* and *payload* is an act: on the
    allow-list, not excluded by name, and not a refusal. The SQL of
    :func:`acts` holds the same rule. Pure."""
    if not isinstance(action, str) or action in EXCLUDED:
        return False
    if action not in ACT_ACTIONS and not action.startswith(ACT_PREFIXES):
        return False
    decision = payload.get("decision") if isinstance(payload, dict) else None
    return decision is None or decision == ALLOWED_DECISION


@dataclass(frozen=True)
class Act:
    """One act of a member's day, as ``audit_event`` holds it."""

    id: str
    at: datetime
    actor: str
    action: str
    target: str
    member: str
    #: ``payload.policy``: the policy that allowed the act. None when the
    #: writer recorded none.
    policy: str | None
    payload: dict[str, Any] = field(default_factory=dict)


def _act(row: Any) -> Act:
    payload = row[5] if isinstance(row[5], dict) else {}
    policy = payload.get("policy")
    return Act(id=str(row[0]), at=row[1], actor=str(row[2]), action=str(row[3]),
               target=str(row[4]), member=str(payload.get("member") or ""),
               policy=str(policy) if isinstance(policy, str) and policy.strip() else None,
               payload=payload)


def acts(organization_id: str, member: str, since: datetime, until: datetime,
         *, limit: int = MAX_ACTS) -> list[Act]:
    """The acts of *member* in the org from *since* up to *until*, the
    oldest first, :data:`MAX_ACTS` at most.

    *organization_id* and *member* come from the caller's run or session,
    never from request input. Both bounds must be aware. Raises
    ``TenantUnbound`` with no org, and ``ValueError`` with no member or a
    naive bound. A database fault raises too: the caller decides."""
    from acb_graph import tenant_session
    from sqlalchemy import text

    who = (member or "").strip().lower()
    if not who:
        raise ValueError("acts() needs the member")
    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("acts() needs aware bounds")
    params = {"m": who, "since": since, "until": until, "actions": sorted(ACT_ACTIONS),
              "prefixes": [p + "%" for p in ACT_PREFIXES], "excluded": sorted(EXCLUDED),
              "allowed": ALLOWED_DECISION, "n": max(1, min(int(limit), MAX_ACTS))}
    with tenant_session(organization_id) as db:
        rows = db.execute(text(_ACTS_SQL), params).all()
    return [_act(r) for r in rows]
