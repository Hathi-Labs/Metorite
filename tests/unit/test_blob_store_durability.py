"""Blob-store durability tests — the Part-2 agent file/memory persistence.

The agent's three folders (agent-data/, inputs/, outputs/) are backed by
Postgres (source of truth) with the on-disk workspace as a rehydratable cache.
This module proves the durability contract end-to-end:

  put_file  → wipe the disk workspace → rehydrate_workspace → file is BACK,
              byte-for-byte, and every unique version is recorded in history.

Two kinds of test live here:

  * Pure-logic tests (no DB) — path classification and the graceful-degradation
    guarantee. These always run.
  * Live round-trip tests — require a reachable Postgres with migration 71
    applied (agent_blob + agent_file_history). They SKIP automatically when no
    DB is reachable, so CI without a database stays green; on the VPS (or any
    box with the live DB) they exercise the real store.

Everything is scoped to a throwaway agent_name and cleaned up in a finally, so
running against the live DB leaves no residue.
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

# ═══════════════════════════════════════════════════════════════════════════
# Pure-logic — path classification (no DB, always runs)
# ═══════════════════════════════════════════════════════════════════════════

def test_folder_of_classifies_the_three_folders() -> None:
    assert blob_store.folder_of("agent-data/memory.md") == "agent-data"
    assert blob_store.folder_of("inputs/photo.png") == "inputs"
    assert blob_store.folder_of("outputs/reports/q3.html") == "outputs"


def test_folder_of_rejects_non_stored_paths() -> None:
    assert blob_store.folder_of("config.json") is None
    assert blob_store.folder_of("src/main.py") is None
    assert blob_store.folder_of("") is None


def test_folder_of_normalises_windows_seps_and_leading_slash() -> None:
    assert blob_store.folder_of("outputs\\a\\b.txt") == "outputs"
    assert blob_store.folder_of("/outputs/a.txt") == "outputs"


def test_is_stored_path_matches_folder_of() -> None:
    assert blob_store.is_stored_path("agent-data/x")
    assert not blob_store.is_stored_path("nope/x")


# ═══════════════════════════════════════════════════════════════════════════
# Graceful degradation — public API never raises for bad inputs (no DB)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_put_file_noop_for_unstored_path() -> None:
    """A path outside the three folders is a no-op (returns None), never an
    error — the write_artifact write-through relies on this."""
    assert await blob_store.put_file("agentX", "src/main.py", b"x") is None


@pytest.mark.asyncio
async def test_put_file_noop_for_empty_agent_name() -> None:
    assert await blob_store.put_file("", "outputs/x.txt", b"x") is None


@pytest.mark.asyncio
async def test_get_file_noop_for_unstored_path() -> None:
    assert await blob_store.get_file("agentX", "src/main.py") is None


@pytest.mark.asyncio
async def test_list_files_empty_for_empty_agent_name() -> None:
    assert await blob_store.list_files("") == []


@pytest.mark.asyncio
async def test_rehydrate_noop_for_empty_args(tmp_path: Path) -> None:
    assert await blob_store.rehydrate_workspace("", str(tmp_path)) == 0
    assert await blob_store.rehydrate_workspace("agentX", "") == 0


# ═══════════════════════════════════════════════════════════════════════════
# Live round-trip — requires a reachable Postgres with migration 71
# ═══════════════════════════════════════════════════════════════════════════

def _db_reachable() -> bool:
    """True if we can open a session AND agent_blob exists (migration 71)."""
    try:
        from acb_graph import get_session
        from sqlalchemy import text
    except Exception:
        return False
    try:
        with get_session() as s:
            s.execute(text("SELECT 1 FROM agent_blob LIMIT 1"))
        return True
    except Exception:
        return False


# One probe, reused by every live test as a skip gate.
_LIVE = _db_reachable()
_needs_db = pytest.mark.skipif(
    not _LIVE,
    reason="no reachable Postgres with migration 71 (agent_blob) — live "
    "durability round-trip skipped (runs on the VPS / any box with the DB)",
)

# A throwaway agent name so nothing collides with real agents; cleaned up after.
_TEST_AGENT = "pytest-blobstore-durability"


def _purge_test_agent() -> None:
    """Remove all store rows for the throwaway agent (idempotent)."""
    try:
        from acb_graph import get_session
        from sqlalchemy import text
        with get_session() as s:
            s.execute(
                text("DELETE FROM agent_blob WHERE agent_name = :a"),
                {"a": _TEST_AGENT},
            )
            s.execute(
                text("DELETE FROM agent_file_history WHERE agent_name = :a"),
                {"a": _TEST_AGENT},
            )
            s.commit()
    except Exception:
        pass


@pytest.fixture()
def clean_agent():
    """Ensure the throwaway agent has no residue before AND after the test."""
    _purge_test_agent()
    try:
        yield _TEST_AGENT
    finally:
        _purge_test_agent()


@pytest.mark.asyncio
@_needs_db
async def test_put_then_get_roundtrip(clean_agent: str) -> None:
    """put_file → get_file returns the exact bytes."""
    data = b"# Agent memory\nThe founder prefers CNTS breakdowns.\n"
    meta = await blob_store.put_file(
        clean_agent, "agent-data/memory.md", data,
        mime_type="text/markdown", action="create",
    )
    assert meta is not None
    assert meta.folder == "agent-data"
    assert meta.size == len(data)

    got = await blob_store.get_file(clean_agent, "agent-data/memory.md")
    assert got == data


@pytest.mark.asyncio
@_needs_db
async def test_wipe_disk_then_rehydrate_restores_files(
    clean_agent: str, tmp_path: Path
) -> None:
    """THE durability contract: write through the store, blow away the disk
    workspace, rehydrate — every file comes back byte-for-byte."""
    files = {
        "agent-data/memory.md": b"durable knowledge",
        "inputs/brief.txt": b"user uploaded brief",
        "outputs/reports/q3.html": b"<div class='cc-report'>Q3</div>",
    }
    for path, data in files.items():
        assert await blob_store.put_file(
            clean_agent, path, data, action="create"
        ) is not None

    # Simulate a fresh / wiped volume: an empty workspace root.
    workspace = tmp_path / "repos" / clean_agent
    workspace.mkdir(parents=True)
    assert not any(workspace.rglob("*"))  # genuinely empty

    restored = await blob_store.rehydrate_workspace(clean_agent, str(workspace))
    assert restored == len(files)

    # Every file is back on disk with identical content (nested dirs recreated).
    for path, data in files.items():
        dest = workspace / path
        assert dest.exists(), f"{path} was not restored"
        assert dest.read_bytes() == data


@pytest.mark.asyncio
@_needs_db
async def test_rehydrate_skips_files_already_current(
    clean_agent: str, tmp_path: Path
) -> None:
    """A second rehydrate over an up-to-date disk restores nothing (0), so
    load is cheap and idempotent."""
    data = b"already here"
    await blob_store.put_file(clean_agent, "outputs/x.txt", data, action="create")
    workspace = tmp_path / clean_agent
    workspace.mkdir(parents=True)

    first = await blob_store.rehydrate_workspace(clean_agent, str(workspace))
    assert first == 1
    second = await blob_store.rehydrate_workspace(clean_agent, str(workspace))
    assert second == 0  # nothing to do — disk already current


@pytest.mark.asyncio
@_needs_db
async def test_history_records_each_unique_version_and_dedupes(
    clean_agent: str,
) -> None:
    """Every genuine content change is one history row; a same-content rewrite
    is deduped (this is the 'track every unique file version' guarantee)."""
    path = "agent-data/notes.md"
    await blob_store.put_file(clean_agent, path, b"v1", action="create")
    await blob_store.put_file(clean_agent, path, b"v2", action="modify")
    await blob_store.put_file(clean_agent, path, b"v2", action="modify")  # dup sha

    hist = await blob_store.file_history(clean_agent, path)
    # v1 (create) + v2 (modify) = 2 unique versions; the duplicate v2 is deduped.
    assert len(hist) == 2
    actions = {h["action"] for h in hist}
    assert actions == {"create", "modify"}
    # Newest first.
    assert hist[0]["created_at"] >= hist[-1]["created_at"]


@pytest.mark.asyncio
@_needs_db
async def test_delete_drops_current_but_keeps_history(clean_agent: str) -> None:
    """A delete removes the live blob (get → None) but the version history of
    what existed is preserved (append-only audit)."""
    path = "outputs/temp.txt"
    await blob_store.put_file(clean_agent, path, b"bye", action="create")
    assert await blob_store.get_file(clean_agent, path) == b"bye"

    await blob_store.delete_file(clean_agent, path, actor="user")
    assert await blob_store.get_file(clean_agent, path) is None

    hist = await blob_store.file_history(clean_agent, path)
    actions = [h["action"] for h in hist]
    assert "create" in actions
    assert "delete" in actions


@pytest.mark.asyncio
@_needs_db
async def test_list_files_filters_by_prefix(clean_agent: str) -> None:
    await blob_store.put_file(clean_agent, "outputs/a.txt", b"a", action="create")
    await blob_store.put_file(clean_agent, "outputs/sub/b.txt", b"b", action="create")
    await blob_store.put_file(clean_agent, "inputs/c.txt", b"c", action="create")

    outputs = await blob_store.list_files(clean_agent, prefix="outputs")
    paths = {m.path for m in outputs}
    assert paths == {"outputs/a.txt", "outputs/sub/b.txt"}

    everything = await blob_store.list_files(clean_agent)
    assert len(everything) == 3
