"""Who may land agent code: the ONE rule of the platform operator's reviewer.

An agent's local clone holds the operator's ``GITHUB_TOKEN`` in its remote URL
(``acb_skills.loader._refresh_remote_auth``), and the clone is shared by every
organization. So a push of agent code is a deployment-wide act, done with the
operator's credentials. Two paths can do it, and both take this one rule:

* the self-mutation review queue (``/agent/mutations/*``), whose approve
  pushes a pending commit (``gateway.routes.agent``, PR #1167);
* a chat run whose agent pushes its own commit, when the member said
  "commit and push" (``orchestrator.executor``, the permission guard of
  ``acb_skills.permission_policy`` and ``_detect_agent_commits``).

The rule: the member holds ``agents:manage`` (owner and admin only, migration
130) AND the member's organization is in ``AGENT_REGISTRY_ORGS`` (``*`` for
every org, empty for none, so it fails closed). The org always comes from a
server-side binding, never from the request (R5).

Fence: ``tests/unit/test_chat_autopush_reviewer_gate.py``.
"""
from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "AGENT_COMMIT_REVIEW_PERMISSION",
    "is_registry_operator_org",
    "may_review_agent_commits",
    "registry_operator_orgs",
]

#: The permission of a reviewer of agent code. Only ``owner`` (``*``) and
#: ``admin`` hold it (migration 130).
AGENT_COMMIT_REVIEW_PERMISSION = "agents:manage"


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
    """True when *organization_id* is a platform operator org.

    An empty org is never one, also when the setting is ``*``.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    allowed = registry_operator_orgs(raw)
    return "*" in allowed or org in allowed


def may_review_agent_commits(
    organization_id: str | None, has_permission: Callable[[str], bool],
) -> bool:
    """True when this member may land agent code (approve it, or push it).

    *has_permission* answers for the member, for example
    ``EffectiveAccess.has`` or ``UserContext.has_permission``. Any error in it
    reads as no.
    """
    if not is_registry_operator_org(organization_id):
        return False
    try:
        return bool(has_permission(AGENT_COMMIT_REVIEW_PERMISSION))
    except Exception:
        return False
