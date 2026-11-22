"""Which memory compartments a run may read, and where its facts are written.

Spec: ``docs/multiplayer/memory-clearance.md`` §3.1 (compartments), §3.3 (the
clearance rule), §3.4 (the write rule).

A **compartment** is the unit of memory scoping — one Mem0 partition, one
audience. This module answers the only two questions a run needs:

* **What may this run read?** The clearance rule: *a run reads at the clearance
  of its least-cleared viewer.* Intersection, never union.
* **Where do its facts go?** The write rule: *no wider than the session they
  were learned in.*

Deliberately dependency-free. It knows nothing about rooms, participants, or
permissions — the caller resolves ``shared`` (the gateway already has
``resolve_room_access``) and this decides what follows from it. Keeping the
direction that way is what lets ``acb_memory`` stay below ``acb_auth`` and the
gateway in the import graph.

**What is here and what is not.** Room and preference compartments need no
registry: a room's audience is its participant list, which
``chat_session_participant`` already holds, and a person's preferences are
theirs. ``subject:`` compartments — "everything about the Falcon deal" — do
need the registry and the membership tables in §3.2, and are not built. The cut
is along the design's own grain rather than halfway through a feature.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: Reserved namespaces. A person's private scope is a bare email, so every
#: other kind carries a prefix and the three can never collide in the shared
#: Mem0 collection.
AGENT_SCOPE_PREFIX = "agent:"
ROOM_SCOPE_PREFIX = "room:"
PREFS_SCOPE_PREFIX = "prefs:"
#: KG-9 step 2: the org compartment is ``org:<organization id>``. The org id
#: comes from the bound tenant of the caller, never from request input.
ORG_SCOPE_PREFIX = "org:"
#: The OLD org key, one value for every organization. Nothing writes it after
#: KG-9. It stays for the memory API and for the clearance fingerprint, which
#: must not change (see ``Clearance.fingerprint``). Rows under it are not
#: moved. The memory tenant guard keeps Mem0 off while two orgs exist.
ORG_SCOPE_KEY = "org:global"


@dataclass(frozen=True, slots=True)
class Clearance:
    """What one run may read, and the one compartment it may write.

    ``read`` is ordered, and the order is the order the prompt block assembles
    in — the block must stay byte-stable across turns or it defeats prompt
    caching on the memory portion.
    """

    read: tuple[str, ...]
    write: str
    shared: bool
    #: The acting member's preference compartment, when it is in play.
    prefs: str | None = None
    #: The room compartment, when this is a room.
    room: str | None = None

    @property
    def fingerprint(self) -> str:
        """A stable id for this clearance, for cache keys.

        The assembled memory block is cached per thread. Without the clearance
        in the key, a thread cached while solo keeps serving the block it built
        THEN — including the owner's private facts — for the rest of the TTL
        after somebody is invited in. The cache would quietly undo the rule the
        read path enforces, which is the failure §3.5 warned about.
        """
        # KG-9: the org compartment enters the hash as the old ``org:global``.
        # A thread belongs to one tenant, so the org id adds nothing to a key
        # that is per thread. And the hash stays byte-identical to the one
        # from before KG-9, so no saved session and no cached block goes stale.
        # A clearance with no org id hashes the same way: the org slot is a
        # rule of the shape, not a fact of the tenant.
        read = [s for s in self.read if scope_kind(s) != "org"]
        material = "|".join((*sorted((*read, ORG_SCOPE_KEY)), ">", self.write))
        return hashlib.sha256(material.encode()).hexdigest()[:12]


def scope_key(
    *,
    user: str | None = None,
    agent: str | None = None,
    org: bool = False,
    room: str | None = None,
    prefs: bool = False,
    organization_id: str | None = None,
) -> str:
    """Build the Mem0 partition value for one compartment.

    Exactly one kind is selected, checked in the order below::

        scope_key(user="a@b.com")             → "a@b.com"        their private facts
        scope_key(user="a@b.com", prefs=True) → "prefs:a@b.com"   how they like to work
        scope_key(room="thread-abc")          → "room:thread-abc" facts this room established
        scope_key(agent="sales")              → "agent:sales"     the agent's tradecraft
        scope_key(org=True, organization_id=X) → "org:X"          company facts

    ``prefs`` and ``user`` are the same person split in two, and that split is
    what makes a shared room usable at all: in a room nobody's ``user:``
    compartment loads, but the person being answered keeps their ``prefs:``.
    The agent still writes the way you like without exposing one thing you told
    it in private. Preferences are self-descriptive and you are in the room;
    episodic facts are neither.

    KG-9: ``org=True`` with no ``organization_id`` gives ``""``, the empty
    scope, which every reader and writer skips. It never falls back to one
    shared org key. The caller passes the bound tenant, never request input.
    """
    if org:
        return org_scope_key(organization_id)
    if room:
        return f"{ROOM_SCOPE_PREFIX}{room}"
    if agent:
        return f"{AGENT_SCOPE_PREFIX}{agent}"
    if prefs:
        return f"{PREFS_SCOPE_PREFIX}{user}" if user else ""
    return user or ""


def org_scope_key(organization_id: str | None) -> str:
    """The org compartment of one organization, or ``""`` with no org id."""
    org = str(organization_id or "").strip().lower()
    return f"{ORG_SCOPE_PREFIX}{org}" if org else ""


def scope_kind(scope: str) -> str:
    """Classify a scope key: prefs | room | agent | org | user | unknown.

    Used by anything that must branch on the shape rather than the value —
    the API's authorization, the memory inspector, audit lines.
    """
    scope = (scope or "").strip()
    if not scope:
        return "unknown"
    if scope.startswith(ORG_SCOPE_PREFIX):
        rest = scope[len(ORG_SCOPE_PREFIX):]
        return "org" if rest and "@" not in rest else "unknown"
    if scope.startswith(PREFS_SCOPE_PREFIX):
        return "prefs" if len(scope) > len(PREFS_SCOPE_PREFIX) else "unknown"
    if scope.startswith(ROOM_SCOPE_PREFIX):
        return "room" if len(scope) > len(ROOM_SCOPE_PREFIX) else "unknown"
    if scope.startswith(AGENT_SCOPE_PREFIX):
        return "agent" if len(scope) > len(AGENT_SCOPE_PREFIX) else "unknown"
    if "@" in scope:
        return "user"
    return "unknown"


def resolve_clearance(
    *,
    actor: str,
    agent_name: str,
    thread_id: str | None,
    shared: bool,
    organization_id: str | None = None,
) -> Clearance:
    """The compartments this run reads, and the one it writes.

    **Solo is byte-identical to before compartments existed** — the same three
    scopes in the same order, writing to the same place. That is not a
    coincidence to preserve carefully; it is the whole design: a room of one
    has an intersection of one, so the rule collapses to today's behaviour.

    **Shared** swaps the acting member's private compartment for the room's,
    and keeps their preferences:

    * ``room:<thread_id>`` — what this room has established. Its audience is
      the room's participants, so it needs no registry entry.
    * ``prefs:<actor>`` — how the person being answered likes to work. Safe to
      carry in because they are in the room and the facts describe them.
    * agent and org — never one person's, so they cross unchanged.

    KG-9: the org compartment is ``org:<organization_id>``. With no
    ``organization_id`` the run reads no org compartment. It never reads one
    key that every organization shares.

    What is absent is the point: no ``<actor>`` user compartment, in a room, for
    anyone. Not the owner's, not the typer's. The agent does not *decline* to
    use those facts — ``search()`` is never called with the scope key, so there
    is no retrieval to leak. That is the difference between a boundary and a
    request in a system prompt.
    """
    actor = (actor or "").strip()
    agent_scope = scope_key(agent=agent_name) if agent_name else ""
    org_scope = scope_key(org=True, organization_id=organization_id)

    if not shared:
        read = [s for s in (actor, agent_scope, org_scope) if s]
        return Clearance(
            read=tuple(read),
            write=actor,
            shared=False,
        )

    room_scope = scope_key(room=thread_id) if thread_id else ""
    prefs_scope = scope_key(user=actor, prefs=True) if actor else ""
    read = [s for s in (room_scope, prefs_scope, agent_scope, org_scope) if s]
    return Clearance(
        read=tuple(read),
        # A room with no thread id cannot have a room compartment; writing to
        # the actor instead would be exactly the leak this exists to prevent,
        # so such a run writes nowhere.
        write=room_scope,
        shared=True,
        prefs=prefs_scope or None,
        room=room_scope or None,
    )
