"""Who may land agent code: the ONE rule of the reviewer of agent commits.

A push of agent code uses the operator's ``GITHUB_TOKEN``, and an agent's
clone is shared by every organization. So a push of agent code is a
deployment-wide act, done with the operator's credentials. Since H-363 the
clone holds no token: ``acb_common.git_credentials`` gives it to one gateway
git process, and only on a push that one of these paths allows. Both paths
call this one rule:

* the self-mutation review queue (``/agent/mutations/*``), whose approve
  pushes a pending commit (``gateway.routes.agent._require_mutation_reviewer``);
* a chat run whose member said "commit and push"
  (``orchestrator.executor._agent_push_allowed``, read by the permission
  guard of ``acb_skills.permission_policy``, by ``run_script`` and by
  ``_detect_agent_commits``, which then pushes as the gateway through
  ``_push_reviewed_run``).

The rule: the member holds ``agents:manage`` (owner and admin only, migration
130) AND the member's org is not empty AND it is either the first-party org
(``organization.first_party``) or in ``AGENT_REGISTRY_ORGS``. A missing org, a
missing row and a failed read each answer no. The org always comes from a
server-side binding, never from the request (R5).

Why ``first_party`` too: self-mutation writes ``pending_commit`` rows only for
a first-party org (MT-0b), and on production the first-party org is not in
``AGENT_REGISTRY_ORGS``. The registry list alone locked out the owner.

This module also holds the ONE read of ``organization.first_party``
(:func:`read_first_party`). ``orchestrator.mutation._read_first_party`` calls
it, so "is this org ours" has one query. ``acb_auth`` must not import
``orchestrator``, which is why the read lives here.

Fence: ``tests/unit/test_chat_autopush_reviewer_gate.py``.
"""
from __future__ import annotations

from collections.abc import Callable

from acb_common import get_logger

_log = get_logger("acb_auth.agent_operator")

__all__ = [
    "AGENT_COMMIT_REVIEW_PERMISSION",
    "is_registry_operator_org",
    "may_review_agent_commits",
    "org_may_review_agent_commits",
    "read_first_party",
    "registry_operator_orgs",
]

#: The permission of a reviewer of agent code. Only ``owner`` (``*``) and
#: ``admin`` hold it (migration 130).
AGENT_COMMIT_REVIEW_PERMISSION = "agents:manage"

_SQL_FIRST_PARTY_BY_ID = """
SELECT first_party FROM organization WHERE id = :org_id
"""
#: Untenanted resolution. Deliberately requires the org to be the ONLY one: on a
#: single-tenant box this is the operator's org and behaviour is unchanged, and
#: the moment a second organization exists it returns no row and the gate fails
#: closed. A "pick the default org" fallback would keep answering true forever.
_SQL_SOLE_ORG_FIRST_PARTY = """
SELECT first_party FROM organization
 WHERE (SELECT count(*) FROM organization) = 1
"""


async def read_first_party(organization_id: str | None) -> bool | None:
    """``organization.first_party`` for *organization_id*. The ONE read of it.

    With no *organization_id* it answers only on a box with exactly one
    organization (MT-0b's untenanted case). Returns ``None`` when no row
    resolves. It raises on a database failure, and each caller decides that
    means no. :func:`org_may_review_agent_commits` never calls it with an
    empty org.
    """
    from acb_common.db import get_db
    from sqlalchemy import text

    session = await get_db()
    try:
        if organization_id:
            row = (await session.execute(
                text(_SQL_FIRST_PARTY_BY_ID), {"org_id": organization_id},
            )).first()
        else:
            row = (await session.execute(text(_SQL_SOLE_ORG_FIRST_PARTY))).first()
    finally:
        await session.close()
    return None if row is None else bool(row[0])


def registry_operator_orgs(raw: str | None = None) -> frozenset[str]:
    """The org ids of ``AGENT_REGISTRY_ORGS``, lower case. Empty means none.

    *raw* is the setting's value. With ``None`` it reads the setting.
    """
    if raw is None:
        from acb_common import get_settings

        raw = getattr(get_settings(), "agent_registry_orgs", "")
    return frozenset(
        part.strip().lower() for part in str(raw or "").split(",") if part.strip()
    )


def is_registry_operator_org(
    organization_id: str | None, raw: str | None = None,
) -> bool:
    """True when *organization_id* is in ``AGENT_REGISTRY_ORGS``.

    An empty org is never one, also when the setting is ``*``.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    allowed = registry_operator_orgs(raw)
    return "*" in allowed or org in allowed


async def org_may_review_agent_commits(organization_id: str | None) -> bool:
    """True when the org may review agent commits: first-party or operator.

    The registry list is checked first, so an operator org needs no database
    read. An empty org answers no and never reaches :func:`read_first_party`,
    which would answer for a sole org. A missing row or a failed read is no.
    """
    org = str(organization_id or "").strip()
    if not org:
        return False
    if is_registry_operator_org(org):
        return True
    try:
        return await read_first_party(org) is True
    except Exception as exc:  # fail closed
        _log.warning("agent_operator.first_party_read_failed", error=str(exc)[:200])
        return False


async def may_review_agent_commits(
    organization_id: str | None, has_permission: Callable[[str], bool],
) -> bool:
    """True when this member may land agent code (approve it, or push it).

    *has_permission* answers for the member, for example
    ``EffectiveAccess.has`` or ``UserContext.has_permission``. The permission
    is checked first, so a member without it causes no org read. Any error in
    it reads as no.
    """
    try:
        if not has_permission(AGENT_COMMIT_REVIEW_PERMISSION):
            return False
    except Exception:
        return False
    return await org_may_review_agent_commits(organization_id)
