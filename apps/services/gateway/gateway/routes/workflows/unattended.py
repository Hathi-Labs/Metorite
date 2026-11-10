"""The org allowlist of unattended workflow runs (H-328 part (b)).

This module is the ONE reader of ``WORKFLOW_UNATTENDED_ORGS``. It copies the
shape of ``gateway/reach/flags.py``.

* ``WORKFLOW_UNATTENDED_ORGS`` is a comma list of organization ids. Empty, the
  default, allows no org. ``*`` is not a wildcard, so a ``*`` entry allows
  nothing.

**What it gates.** A schedule tick, a matured wait and the startup sweep act
for an org only when the org is listed. Each scan checks :func:`org_allowed`
for each org BEFORE it reads a trigger or starts a run. Once a run binds its
tenant, it can send mail or WhatsApp and spend credits, so the list is the
owner's switch. ``WORKFLOW_SCHEDULER_ENABLED`` stays the kill switch of the
whole loop and is read where it was read before.

The org always comes from the loop's own enumeration of the ``organization``
table, never from a request, a payload or the ambient tenant (R5, R11).

Fence: ``tests/unit/test_workflows_unattended_flag.py`` and
``tests/unit/test_workflows_lifecycle_rls_r8.py``.
"""

from __future__ import annotations

import functools

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def unattended_orgs() -> frozenset[str]:
    """The organization ids that may run unattended. ``*`` is not a wildcard."""
    raw = str(getattr(get_settings(), "workflow_unattended_orgs", "") or "")
    return frozenset(o for o in _parse(raw) if o != "*")


def org_allowed(organization_id: str | None) -> bool:
    """True when the org is on the list. Default OFF, and no org answers False."""
    if not organization_id:
        return False
    return str(organization_id).strip().lower() in unattended_orgs()
