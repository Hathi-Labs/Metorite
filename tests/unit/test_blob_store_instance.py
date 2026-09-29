"""The agent file store partitions by instance (migration 136).

Spec: project-docs/specs/memory_architecture.md §5.3, §6.1

Before 117, ``agent_blob`` was keyed by ``(agent_name, path)`` — one
``agent-data/NOTES.md`` per agent, shared by every user of it, returned whole by
``recall_notes``. That is worse than the Mem0 agent bucket in a specific way:
the vector tier surfaces only what is semantically close to the query, while the
file tier returns everything it holds on every run that loads it. It is also not
gated behind ``MEM0_ENABLED``.

Two properties matter and both are asserted against a live database:

1. **Isolation** — two instances hold the same path with different content, and
   neither can see the other's.
2. **No behaviour change at the default** — a caller that passes no instance
   resolves to ``''`` and sees exactly what it saw before the migration, which
   is what makes adopting this safe for every agent still declared ``shared``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from acb_memory import blob_store


@pytest.fixture(autouse=True)
def _bound_tenant():
    """WS-27bm S15 (§21). The store binds the caller's tenant, and with none it
    refuses. So each test runs inside a bound tenant, as a request or a run
    does. This suite runs on the ladder-only shape, so any id binds."""
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant("5150aaaa-0000-4000-8000-000000000015")
    try:
        yield
    finally:
        release_tenant(token)


def _db_reachable() -> bool:
    """True if a session opens AND agent_blob carries the instance column."""
    try:
        from acb_graph import get_session
        from sqlalchemy import text
    except Exception:
        return False
    try:
        with get_session() as s:
            s.execute(text("SELECT instance FROM agent_blob LIMIT 1"))
        return True
    except Exception:
        return False


_needs_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="no reachable Postgres with migration 136 (agent_blob.instance) — "
    "live instance-partition tests skipped (run on the VPS / any box with the DB)",
)

_AGENT = "pytest-blobstore-instance"
_ALICE = "u:alice@fracktal.in"
_BOB = "u:bob@fracktal.in"


def _purge() -> None:
    try:
        from acb_graph import get_session
        from sqlalchemy import text
        with get_session() as s:
            s.execute(
                text("DELETE FROM agent_blob WHERE agent_name = :a"), {"a": _AGENT},
            )
            s.execute(
                text("DELETE FROM agent_file_history WHERE agent_name = :a"),
                {"a": _AGENT},
            )
            s.commit()
    except Exception:
        pass


@pytest.fixture
def clean_store():
    _purge()
    yield
    _purge()


# ---------------------------------------------------------------------------
# Isolation — the point of the migration
# ---------------------------------------------------------------------------

@_needs_db
@pytest.mark.asyncio
async def test_two_people_hold_the_same_path_without_colliding(clean_store) -> None:
    """The old PK (agent_name, path) made this impossible — the second write
    overwrote the first."""
    await blob_store.put_file(
        _AGENT, "agent-data/NOTES.md", b"alice private notes", instance=_ALICE,
    )
    await blob_store.put_file(
        _AGENT, "agent-data/NOTES.md", b"bob private notes", instance=_BOB,
    )

    assert await blob_store.get_file(
        _AGENT, "agent-data/NOTES.md", instance=_ALICE,
    ) == b"alice private notes"
    assert await blob_store.get_file(
        _AGENT, "agent-data/NOTES.md", instance=_BOB,
    ) == b"bob private notes"


@_needs_db
@pytest.mark.asyncio
async def test_listing_never_crosses_instances(clean_store) -> None:
    await blob_store.put_file(_AGENT, "agent-data/a.md", b"A", instance=_ALICE)
    await blob_store.put_file(_AGENT, "agent-data/b.md", b"B", instance=_BOB)

    alice = {m.path for m in await blob_store.list_files(_AGENT, instance=_ALICE)}
    bob = {m.path for m in await blob_store.list_files(_AGENT, instance=_BOB)}

    assert alice == {"agent-data/a.md"}
    assert bob == {"agent-data/b.md"}


@_needs_db
@pytest.mark.asyncio
async def test_a_missing_file_in_one_instance_is_not_served_from_another(
    clean_store,
) -> None:
    """The leak this prevents: Bob asking for a path only Alice has must get
    nothing, not Alice's content."""
    await blob_store.put_file(_AGENT, "agent-data/secret.md", b"alice", instance=_ALICE)
    assert await blob_store.get_file(
        _AGENT, "agent-data/secret.md", instance=_BOB,
    ) is None


@_needs_db
@pytest.mark.asyncio
async def test_delete_only_affects_its_own_instance(clean_store) -> None:
    await blob_store.put_file(_AGENT, "agent-data/x.md", b"alice", instance=_ALICE)
    await blob_store.put_file(_AGENT, "agent-data/x.md", b"bob", instance=_BOB)

    await blob_store.delete_file(_AGENT, "agent-data/x.md", instance=_ALICE)

    assert await blob_store.get_file(_AGENT, "agent-data/x.md", instance=_ALICE) is None
    assert await blob_store.get_file(
        _AGENT, "agent-data/x.md", instance=_BOB,
    ) == b"bob"


@_needs_db
@pytest.mark.asyncio
async def test_identical_content_keeps_separate_history_per_instance(
    clean_store,
) -> None:
    """71's dedup index was (agent_name, path, sha256, action). Left in place it
    would swallow the second person's history row whenever two people wrote
    byte-identical content — the provenance log quietly lying."""
    same = b"identical content"
    await blob_store.put_file(_AGENT, "agent-data/n.md", same, instance=_ALICE)
    await blob_store.put_file(_AGENT, "agent-data/n.md", same, instance=_BOB)

    alice = await blob_store.file_history(_AGENT, instance=_ALICE)
    bob = await blob_store.file_history(_AGENT, instance=_BOB)
    assert len(alice) >= 1
    assert len(bob) >= 1


# ---------------------------------------------------------------------------
# No behaviour change at the default
# ---------------------------------------------------------------------------

@_needs_db
@pytest.mark.asyncio
async def test_omitting_the_instance_is_the_shared_partition(clean_store) -> None:
    """Every pre-migration row became an instance='' row. A caller that knows
    nothing about instances must still see exactly those."""
    await blob_store.put_file(_AGENT, "agent-data/shared.md", b"shared")
    assert await blob_store.get_file(_AGENT, "agent-data/shared.md") == b"shared"
    assert await blob_store.get_file(
        _AGENT, "agent-data/shared.md", instance="",
    ) == b"shared"
    # ...and a personal instance must NOT see it.
    assert await blob_store.get_file(
        _AGENT, "agent-data/shared.md", instance=_ALICE,
    ) is None


@_needs_db
@pytest.mark.asyncio
async def test_rehydrate_restores_only_the_requested_instance(
    clean_store, tmp_path: Path,
) -> None:
    """The sharpest seam: the on-disk workspace is ONE directory, so restoring
    the wrong instance would put one person's notes in front of another."""
    await blob_store.put_file(
        _AGENT, "agent-data/NOTES.md", b"alice notes", instance=_ALICE,
    )
    await blob_store.put_file(
        _AGENT, "agent-data/NOTES.md", b"bob notes", instance=_BOB,
    )

    alice_root = tmp_path / "alice"
    alice_root.mkdir()
    restored = await blob_store.rehydrate_workspace(
        _AGENT, str(alice_root), instance=_ALICE,
    )

    assert restored == 1
    assert (alice_root / "agent-data/NOTES.md").read_bytes() == b"alice notes"


@_needs_db
@pytest.mark.asyncio
async def test_rehydrate_without_an_instance_restores_the_shared_partition(
    clean_store, tmp_path: Path,
) -> None:
    await blob_store.put_file(_AGENT, "agent-data/NOTES.md", b"shared notes")
    await blob_store.put_file(
        _AGENT, "agent-data/NOTES.md", b"alice notes", instance=_ALICE,
    )

    root = tmp_path / "shared"
    root.mkdir()
    await blob_store.rehydrate_workspace(_AGENT, str(root))

    assert (root / "agent-data/NOTES.md").read_bytes() == b"shared notes"


# ---------------------------------------------------------------------------
# The manifest supplies the key
# ---------------------------------------------------------------------------

def test_manifest_blob_instance_matches_the_store_convention() -> None:
    """No DB needed: the manifest is what will supply `instance` at run time, so
    its output has to be in the store's vocabulary."""
    from acb_skills.manifest import AgentManifest

    personal = AgentManifest.from_config(
        {"name": "coach", "sharing": {"instancing": "personal"}}, name="coach",
    )
    assert personal.blob_instance("alice@fracktal.in") == "u:alice@fracktal.in"

    team = AgentManifest.from_config(
        {"name": "sales", "sharing": {"instancing": "team", "team": "sales"}},
        name="sales",
    )
    assert team.blob_instance("anyone@x.com") == "t:sales"

    shared = AgentManifest.from_config(
        {"name": "reconciler", "sharing": {"instancing": "shared"}},
        name="reconciler",
    )
    assert shared.blob_instance("anyone@x.com") == ""
