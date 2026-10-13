"""You may touch your own memory scope, and no one else's.

Spec: ``docs/multiplayer/memory-clearance.md`` §2.1,
``docs/multiplayer/README.md`` §8 (Phase 0).

``/memory/{scope}`` was guarded only by ``require_internal_auth``, which proves
the caller IS THE PLATFORM. The Next.js BFF is the platform, on behalf of every
signed-in user — it forwards the internal token together with the caller's
identity headers. So the gate excluded outsiders and nobody else: any member
could read, search, or delete a colleague's private memories by putting their
email in a URL.

The path parameter is a scope key (``acb_memory.scope_key``), so the properties
are per scope shape:

1. **Your own email is yours**, and somebody else's is a 403 — including for
   admins, because administering members is not reading what they told an agent
   in private.
2. **Agent memory** is shared across the agent's users by design, so it is
   gated on the same ``agents:run:<name>`` the run path enforces.
3. **Org memory** reads on ``memory:read_org`` and writes on
   ``memory:write_org`` — the permissions migration 131 seeded.
4. **An unrecognised scope is refused**, so a vocabulary that grows fails
   closed instead of quietly becoming world-readable.
5. **Deleting checks the memory is in the scope**, not just that the scope is
   yours — otherwise naming your own scope and a colleague's memory id deletes
   their memory, which is the same IDOR one level down.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


def _user(email: str, *, permissions: tuple[str, ...] = (), agents: tuple[str, ...] = ()):
    """A resolved principal holding exactly the given permissions."""
    from acb_auth.permissions import build_access
    from acb_auth.roles import UserContext, UserRole

    return UserContext(
        email=email,
        role=UserRole.EMPLOYEE,
        access=build_access(
            set(permissions) | {f"agents:run:{a}" for a in agents},
        ),
    )


def _service():
    """The platform acting as itself — cron, service-to-service."""
    from acb_auth.access import SERVICE_ACCESS
    from acb_auth.roles import UserContext, UserRole

    return UserContext(email="", role=UserRole.AGENT, access=SERVICE_ACCESS)


# ---------------------------------------------------------------------------
# 1. A person's own memory
# ---------------------------------------------------------------------------

def test_your_own_scope_is_allowed() -> None:
    from gateway.routes.memory import _authorize_scope

    me = _user("alice@fracktal.in")
    _authorize_scope("alice@fracktal.in", me, write=False)
    _authorize_scope("alice@fracktal.in", me, write=True)
    # Case and surrounding whitespace are an identity detail, not a boundary.
    _authorize_scope("Alice@Fracktal.IN", me, write=False)


def test_a_colleagues_scope_is_refused() -> None:
    from gateway.routes.memory import _authorize_scope

    me = _user("alice@fracktal.in")
    with pytest.raises(HTTPException) as exc:
        _authorize_scope("bob@fracktal.in", me, write=False)
    assert exc.value.status_code == 403
    assert "somebody else" in exc.value.detail


def test_an_admin_still_cannot_read_a_colleagues_private_memory() -> None:
    """Deliberate: administering members is not reading their private context.

    If that ever becomes a product requirement it should arrive as a named
    feature with an audit trail, not as a side effect of holding
    ``admin:members:manage``.
    """
    from gateway.routes.memory import _authorize_scope

    admin = _user(
        "admin@fracktal.in",
        permissions=("admin:members:manage", "admin:members:read",
                     "memory:read_org", "memory:write_org"),
    )
    with pytest.raises(HTTPException) as exc:
        _authorize_scope("bob@fracktal.in", admin, write=False)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 2. Agent memory
# ---------------------------------------------------------------------------

def test_agent_memory_needs_the_right_to_run_that_agent() -> None:
    from gateway.routes.memory import _authorize_scope

    me = _user("alice@fracktal.in", agents=("crm-assistant",))
    _authorize_scope("agent:crm-assistant", me, write=False)

    with pytest.raises(HTTPException) as exc:
        _authorize_scope("agent:agent-finance", me, write=False)
    assert exc.value.status_code == 403
    assert "agent-finance" in exc.value.detail


def test_an_empty_agent_name_is_refused() -> None:
    from gateway.routes.memory import _authorize_scope

    with pytest.raises(HTTPException):
        _authorize_scope("agent:", _user("alice@fracktal.in"), write=False)


# ---------------------------------------------------------------------------
# 3. Org memory
# ---------------------------------------------------------------------------

def test_org_memory_reads_and_writes_are_separately_gated() -> None:
    from gateway.routes.memory import _authorize_scope

    reader = _user("alice@fracktal.in", permissions=("memory:read_org",))
    _authorize_scope("org:global", reader, write=False)

    # Reading org memory does not license appending to it — an agent that can
    # silently write is how org memory fills with noise (migration 131).
    with pytest.raises(HTTPException) as exc:
        _authorize_scope("org:global", reader, write=True)
    assert exc.value.status_code == 403
    assert "memory:write_org" in exc.value.detail

    writer = _user(
        "bob@fracktal.in", permissions=("memory:read_org", "memory:write_org"),
    )
    _authorize_scope("org:global", writer, write=True)


def test_org_memory_is_refused_without_the_permission() -> None:
    from gateway.routes.memory import _authorize_scope

    with pytest.raises(HTTPException) as exc:
        _authorize_scope("org:global", _user("nobody@fracktal.in"), write=False)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 4. Unknown shapes fail closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scope", ["", "   ", "default", "org:other", "team:sales", "*"])
def test_an_unrecognised_scope_is_refused(scope) -> None:
    from gateway.routes.memory import _authorize_scope

    with pytest.raises(HTTPException) as exc:
        _authorize_scope(scope, _user("alice@fracktal.in"), write=False)
    assert exc.value.status_code in (403, 404)


def test_the_platform_acting_as_itself_reaches_shared_scopes() -> None:
    """Cron and service-to-service calls have no human to compare against."""
    from gateway.routes.memory import _authorize_scope

    svc = _service()
    _authorize_scope("org:global", svc, write=True)
    _authorize_scope("agent:crm-assistant", svc, write=True)


def test_the_platform_must_name_the_person_to_reach_a_personal_scope() -> None:
    """The bypass that made this bug reachable, closed.

    `deps.py` §1b grants a bearer-without-headers call everything, on the
    reasoning that the token holder could assert any identity anyway. True —
    and the distinction that matters is between ASSERTING an identity and
    OMITTING one. `lib/memory.ts` omitted it and silently received god mode.
    Requiring the assertion makes that omission fail closed.
    """
    from gateway.routes.memory import _authorize_scope

    with pytest.raises(HTTPException) as exc:
        _authorize_scope("anyone@fracktal.in", _service(), write=True)
    assert exc.value.status_code == 403
    assert "X-User-Email" in exc.value.detail


# ---------------------------------------------------------------------------
# 5. Delete verifies the memory is in the scope
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deleting_a_memory_outside_your_scope_is_a_404(monkeypatch) -> None:
    """The IDOR one level down.

    ``MemoryClient.delete`` takes only a memory id, so authorizing the scope
    alone would still let Alice name her OWN scope and Bob's memory id.
    """
    import gateway.routes.memory as mem

    deleted: list[str] = []

    class _Client:
        async def get_all(self, scope):
            return [{"id": "alice-1"}, {"id": "alice-2"}]

        async def delete(self, memory_id):
            deleted.append(memory_id)

    monkeypatch.setattr(mem, "_get_mem0", lambda: _Client())
    alice = _user("alice@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await mem.delete_memory("alice@fracktal.in", "bobs-memory-id", alice)
    assert exc.value.status_code == 404
    assert deleted == []

    # Her own memory still deletes.
    await mem.delete_memory("alice@fracktal.in", "alice-1", alice)
    assert deleted == ["alice-1"]


@pytest.mark.asyncio
async def test_every_endpoint_refuses_a_colleagues_scope(monkeypatch) -> None:
    """The guard is on all five handlers, not just the one that was noticed."""
    import gateway.routes.memory as mem

    class _Client:
        async def get_all(self, scope):
            return [{"id": "x"}]

        async def search(self, scope, query, limit=8):
            return [{"id": "x"}]

        async def add(self, scope, messages, agent_id=""):
            return None

    monkeypatch.setattr(mem, "_get_mem0", lambda: _Client())
    alice = _user("alice@fracktal.in")
    theirs = "bob@fracktal.in"

    for call in (
        lambda: mem.list_memories(theirs, alice),
        lambda: mem.search_memories(theirs, mem.SearchRequest(query="q"), alice),
        lambda: mem.delete_memory(theirs, "x", alice),
        lambda: mem.add_memories(theirs, mem.AddRequest(messages=[]), alice),
        lambda: mem.memory_status(theirs, alice),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 403
