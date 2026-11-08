"""The tenant guard of the memory layer (KG-9, task 786).

Spec: ``project-docs/specs/knowledge_graph.md`` §9, the KG-9 row.

**The rule.** Mem0 and Graphiti refuse to start while two organizations or
more exist. Their stores have no org scope:

* Mem0 keeps every fact in the one ``mem0_memories`` collection. A person's key
  is a bare email, and an agent's key is ``agent:<name>``. Neither key names an
  organization, and the table has no row level security.
* Graphiti searches the whole Neo4j graph, and Neo4j has no row level security.

KG-9 step 2 makes the org compartment ``org:<organization id>`` and the Graphiti
``group_id`` ``org_<organization id>``. The person and agent keys still cross
organizations, so this guard stays until KG-11 removes both stores.

**What it gates.** ``MemoryClient.search``, ``add`` and ``get_all``, and the
start of the Graphiti client. A delete is not gated. A refused memory layer
must still forget what a member asks it to forget.

**Flags off, nothing happens.** With ``MEM0_ENABLED`` and ``GRAPHITI_ENABLED``
both off, :func:`memory_tenancy_ok` reads two settings and returns. It opens no
session.

**Caching.** A refusal stays for the life of the process, because the count of
organizations does not go down. A pass keeps the id of the one organization
that the probe saw. The pass is good for :data:`PASS_TTL_S` seconds, and only
for a call whose bound tenant is unset or is that same id. A call bound to any
other tenant probes again at once. So a new second organization never reads
the memory of the first one from a kept pass. A database fault refuses this
call and is not kept, so the next call asks again.

The read goes through the shared engine (``acb_common.db.get_db``), unbound,
because ``organization`` is RLS-exempt. It is the same idiom as
``gateway/routes/email/memory_purge.py::_organization_ids``. Fence:
``tests/unit/test_memory_tenant_guard.py``.
"""
from __future__ import annotations

import time
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("acb_memory.tenant_guard")

#: How long a pass stays good, in seconds.
PASS_TTL_S = 60.0

#: One read, at most two rows. It answers "which organizations, up to two?"
#: and never reads the whole table.
_ORG_PROBE_SQL = "SELECT id::text AS id FROM organization LIMIT 2"


class MemoryTenancyRefused(RuntimeError):
    """The memory layer refused to start: two organizations or more exist."""


#: ``None`` until the first check. Then ``(ok, at, sole_org)``. ``at`` is from
#: ``time.monotonic``. ``sole_org`` is the one org id that a pass saw, or "".
_verdict: tuple[bool, float, str] | None = None


def memory_flags_on() -> tuple[str, ...]:
    """The memory layers whose flag is on, by name."""
    settings = get_settings()
    layers: list[str] = []
    if getattr(settings, "mem0_enabled", False):
        layers.append("mem0")
    if getattr(settings, "graphiti_enabled", False):
        layers.append("graphiti")
    return tuple(layers)


def _norm(org: str | None) -> str:
    return str(org or "").strip().lower()


async def _probe_organizations() -> tuple[str, ...]:
    """The ids of the organizations, two at most. A fault raises."""
    from sqlalchemy import text

    from acb_common.db import get_db

    session: Any = await get_db()
    try:
        rows = (await session.execute(text(_ORG_PROBE_SQL))).mappings().all()
    finally:
        await session.close()
    return tuple(_norm(r["id"]) for r in rows)


def _bound_tenant() -> str:
    from acb_common.db import current_tenant

    return _norm(current_tenant())


async def memory_tenancy_ok() -> bool:
    """True when the memory layer may start. False refuses it.

    With both flags off this returns True and opens no session. The callers
    already return an empty result when their flag is off.
    """
    global _verdict
    layers = memory_flags_on()
    if not layers:
        return True
    now = time.monotonic()
    tenant = _bound_tenant()
    if _verdict is not None:
        ok, at, sole = _verdict
        if not ok:
            return False
        # A kept pass serves only the org that it saw. Another bound tenant
        # means that a new org exists, so probe again now, not after the TTL.
        if now - at < PASS_TTL_S and (not tenant or tenant == sole):
            return True
    try:
        orgs = await _probe_organizations()
    except Exception as exc:
        # Fail closed for this call. Do not keep it, so the next call asks again.
        _log.warning("memory.tenant_guard_probe_failed", error=type(exc).__name__)
        return False
    if len(orgs) > 1:
        _verdict = (False, now, "")
        _log.error(
            "memory.tenant_guard_refused",
            layers=",".join(layers),
            reason="two or more organizations exist and the memory store has no org scope",
        )
        return False
    sole = orgs[0] if orgs else ""
    if tenant and tenant != sole:
        # The bound tenant is not the one org that exists. Refuse this call
        # and keep no pass. This is not a state that a real run reaches.
        _log.warning("memory.tenant_guard_unknown_tenant")
        return False
    _verdict = (True, now, sole)
    return True


async def check_memory_tenancy() -> None:
    """The startup check. Raise :class:`MemoryTenancyRefused` on a refusal.

    The gateway calls this once in its lifespan and logs the refusal. The
    gateway itself still starts. Only the memory layer stays down.
    """
    if not await memory_tenancy_ok():
        raise MemoryTenancyRefused(
            f"the memory layer ({', '.join(memory_flags_on())}) refuses to start. "
            "Two or more organizations exist, or the check could not read them, "
            "and the memory store has no org scope (KG-9)"
        )


def reset_memory_tenancy() -> None:
    """Forget the kept verdict. For tests only."""
    global _verdict
    _verdict = None
