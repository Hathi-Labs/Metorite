"""What a run may read, and where its facts go.

Spec: ``docs/multiplayer/memory-clearance.md`` §3.1, §3.3, §3.4.

The acceptance test the spec asks for is at the QUERY layer, not the answer
layer: assert that ``search()`` is never *called* with a compartment the room
is not cleared for — not that the answer avoids mentioning it. "Don't mention
Falcon" in a system prompt is a request; not passing the scope key is a
boundary, and only the second is testable.

1. **Solo is byte-identical.** Same scopes, same order, same write target as
   before compartments existed — a room of one has an intersection of one.
2. **A room reads the room, never a person.** No participant's private
   compartment is in the set, including the owner's and the typer's.
3. **Preferences travel, episodic facts do not.** The split is what makes a
   shared room usable rather than amnesiac.
4. **Facts learned in a room are written to the room.**
5. **The clearance fingerprint changes when the clearance does** — the cache
   key depends on it, and a stale key would serve a solo block to a room.
"""
from __future__ import annotations

import pytest
from acb_memory import resolve_clearance, scope_key, scope_kind

ALICE = "alice@fracktal.in"
BOB = "bob@fracktal.in"
TID = "thread-abc123"
#: KG-9: the org compartment holds the org id.
ORG = "0b9f1c2e-1111-4222-8333-444455556666"
ORG_SCOPE = f"org:{ORG}"


# ---------------------------------------------------------------------------
# 1. Solo is unchanged
# ---------------------------------------------------------------------------

def test_a_solo_run_reads_and_writes_exactly_what_it_always_did() -> None:
    c = resolve_clearance(
        actor=ALICE, agent_name="sales", thread_id=TID, shared=False, organization_id=ORG,
    )
    # Order is part of the contract: the assembled block must stay byte-stable
    # across turns or it defeats prompt caching on the memory portion.
    assert c.read == (ALICE, "agent:sales", ORG_SCOPE)
    assert c.write == ALICE
    assert not c.shared
    assert c.room is None
    assert c.prefs is None


def test_an_anonymous_run_still_reads_the_shared_scopes() -> None:
    """Cron and webhooks have no person; agent and org memory are not one."""
    c = resolve_clearance(
        actor="", agent_name="sales", thread_id=None, shared=False, organization_id=ORG,
    )
    assert c.read == ("agent:sales", ORG_SCOPE)
    assert c.write == ""


# ---------------------------------------------------------------------------
# 2-3. A room reads the room, and nobody's private compartment
# ---------------------------------------------------------------------------

def test_a_shared_run_never_reads_any_persons_private_compartment() -> None:
    """The load-bearing assertion: the scope key is absent, not filtered."""
    c = resolve_clearance(
        actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG,
    )
    assert ALICE not in c.read
    assert BOB not in c.read
    # ...and no bare-email compartment of any kind.
    assert not [s for s in c.read if scope_kind(s) == "user"]


def test_a_shared_run_reads_the_room_and_the_typers_preferences() -> None:
    c = resolve_clearance(
        actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG,
    )
    assert c.read == (
        f"room:{TID}", f"prefs:{ALICE}", "agent:sales", ORG_SCOPE,
    )
    assert [scope_kind(s) for s in c.read] == ["room", "prefs", "agent", "org"]


def test_agent_and_org_memory_cross_into_a_room_unchanged() -> None:
    """They were never one person's, so sharing takes nothing away."""
    solo = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=False, organization_id=ORG)
    room = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG)
    shared_scopes = {"agent:sales", ORG_SCOPE}
    assert shared_scopes <= set(solo.read)
    assert shared_scopes <= set(room.read)


# ---------------------------------------------------------------------------
# 4. The write rule
# ---------------------------------------------------------------------------

def test_facts_learned_in_a_room_are_written_to_the_room() -> None:
    c = resolve_clearance(
        actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG,
    )
    assert c.write == f"room:{TID}"
    assert c.write != ALICE


def test_a_room_with_no_thread_id_writes_nowhere_rather_than_to_the_actor() -> None:
    """Falling back to the actor would be precisely the leak this prevents."""
    c = resolve_clearance(
        actor=ALICE, agent_name="sales", thread_id=None, shared=True, organization_id=ORG,
    )
    assert c.write == ""
    assert ALICE not in c.read


# ---------------------------------------------------------------------------
# 5. The fingerprint guards the cache
# ---------------------------------------------------------------------------

def test_the_fingerprint_changes_when_a_thread_becomes_shared() -> None:
    """The cache key depends on this.

    Without it, a thread cached while solo keeps serving the block built from
    the owner's private compartment for the rest of the TTL after somebody is
    invited in — the cache quietly undoing the read rule.
    """
    solo = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=False, organization_id=ORG)
    room = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG)
    assert solo.fingerprint != room.fingerprint


def test_the_fingerprint_is_stable_for_the_same_clearance() -> None:
    a = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG)
    b = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG)
    assert a.fingerprint == b.fingerprint


def test_two_people_in_the_same_room_do_not_share_a_cache_entry() -> None:
    """Their preference compartments differ, so their blocks must too."""
    a = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG)
    b = resolve_clearance(actor=BOB, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG)
    assert a.fingerprint != b.fingerprint


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("kwargs", "expected", "kind"),
    [
        ({"user": ALICE}, ALICE, "user"),
        ({"user": ALICE, "prefs": True}, f"prefs:{ALICE}", "prefs"),
        ({"room": TID}, f"room:{TID}", "room"),
        ({"agent": "sales"}, "agent:sales", "agent"),
        ({"org": True, "organization_id": ORG}, ORG_SCOPE, "org"),
    ],
)
def test_scope_key_and_kind_round_trip(kwargs, expected, kind) -> None:
    key = scope_key(**kwargs)
    assert key == expected
    assert scope_kind(key) == kind


@pytest.mark.parametrize(
    "scope", ["", "   ", "room:", "agent:", "prefs:", "org:", "nonsense"],
)
def test_an_unrecognised_scope_has_no_kind(scope) -> None:
    """Anything the vocabulary does not name must be classifiable as unknown,
    so the API's guard can refuse it rather than guess."""
    assert scope_kind(scope) == "unknown"


def test_the_vocabulary_has_exactly_one_definition() -> None:
    """mem0_client re-exports rather than redefining — two definitions that can
    drift is how a partition boundary stops matching itself."""
    from acb_memory import compartments, mem0_client

    assert mem0_client.scope_key is compartments.scope_key
    assert mem0_client.ORG_SCOPE_KEY is compartments.ORG_SCOPE_KEY


# ---------------------------------------------------------------------------
# The acceptance test, at the query layer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_room_never_issues_a_search_for_a_private_compartment(monkeypatch) -> None:
    """The spec's load-bearing acceptance check (§7, acceptance for 3a).

    Asserted against the SEARCH CALLS, not the answer. Every scope the run
    touches is recorded, and the private compartments must simply not appear —
    there is no retrieval to leak, rather than a retrieval we hope the model
    ignores.
    """
    import acb_memory.mem0_client as mc

    searched: list[str] = []

    class _Client:
        async def search(self, scope, query, limit=8):
            searched.append(scope)
            return [{"memory": f"a fact from {scope}"}]

        async def get_all(self, scope):
            return []

    monkeypatch.setattr(mc, "get_memory_client", lambda: _Client())

    from acb_memory.mem0_client import get_memory_context, get_scoped_context

    clearance = resolve_clearance(
        actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG,
    )

    # Exactly what the run path does with a room clearance: the room and prefs
    # compartments, then the two shared ones. No personal read.
    for scope in clearance.read:
        await get_scoped_context(scope, "what did we decide", header="x")

    # Exact, not a subset: an extra scope here would be an extra disclosure.
    assert searched == [
        f"room:{TID}", f"prefs:{ALICE}", "agent:sales", ORG_SCOPE,
    ]
    # No bare-email compartment was searched. `prefs:alice@…` contains the
    # address as a substring, so this compares by scope KIND rather than by
    # `ALICE not in searched`, which would pass for the wrong reason.
    assert not [s for s in searched if scope_kind(s) == "user"]

    # The solo path's personal helper searches the actor's own compartment —
    # shown here so the contrast is explicit rather than assumed.
    searched.clear()
    await get_memory_context(ALICE, "what did we decide")
    assert searched == [ALICE]


@pytest.mark.asyncio
async def test_sharing_a_thread_does_not_serve_it_the_cached_solo_block() -> None:
    """The cache must not resurrect what the read rule excludes.

    The assembled block is cached per thread for 10 minutes. A thread cached
    while solo built its block from the owner's private compartment; if the key
    ignored clearance, that block would keep being served after somebody was
    invited in — handing the room precisely the facts the read path refuses to
    fetch. Verified against a fake Redis so the key, not the intention, is what
    is asserted.
    """
    from acb_memory.session_cache import get_session_memory

    store: dict[str, str] = {}

    class _Redis:
        async def get(self, k):
            return store.get(k)

        async def setex(self, k, ttl, v):
            store[k] = v

    solo = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=False, organization_id=ORG)
    room = resolve_clearance(actor=ALICE, agent_name="sales", thread_id=TID, shared=True, organization_id=ORG)

    async def _solo_block():
        return "ALICE'S PRIVATE FACTS"

    async def _room_block():
        return "room facts only"

    first = await get_session_memory(
        redis=_Redis(), thread_id=TID, build=_solo_block,
        clearance=solo.fingerprint,
    )
    assert first == "ALICE'S PRIVATE FACTS"

    # Alice shares the thread. The very next run must rebuild, not hit.
    after = await get_session_memory(
        redis=_Redis(), thread_id=TID, build=_room_block,
        clearance=room.fingerprint,
    )
    assert after == "room facts only"
    assert "PRIVATE" not in after

    # Both entries coexist under distinct keys — the solo one is not served to
    # the room, and is not destroyed either (Alice alone may still read it).
    assert len(store) == 2
