"""WS43-F14 — the safe opener, and each host reader and writer that uses it.

Spec ``project-docs/specs/maf_coding_engine.md`` §7.5 rule B and §10 WS43-F14.

What breaks this fence:

* the safe opener follows a symlink at any depth;
* a racing thread swaps a parent dir for a symlink and wins;
* a host reader or writer of §7.5 rule B skips the opener: the store of the
  file tools, the sweep, the rehydrate, the fault-in and the workspace routes;
* a skill under ``agent-data/skills/`` does not survive a lost disk copy and
  a rehydrate.

The opener has two code paths on Linux: one ``openat2`` call (5.6+) and a
walk that opens each part with ``O_NOFOLLOW``. Each refusal test runs on both.
Windows has no ``dir_fd``, so there the module checks each part and then
opens. The race tests need the descriptor walk, so they run on POSIX only.
CI runs Linux, and every test runs there.

Mutations this suite catches (R7), each run red once by hand on 2026-10-03:

* ``_walk_open`` opens a part without ``O_NOFOLLOW`` (Linux, the walk path):
  three middle-link tests go red;
* ``write_bytes`` drops ``O_NOFOLLOW`` on the leaf (Linux): two tests go red;
* the sweep walks with ``rglob`` AND reads with ``Path.read_bytes``: the sweep
  test goes red. Either layer alone still refuses the link, so a mutation of
  one of them stays green, by design;
* ``acb_memory.rehydrate_workspace`` writes with ``Path.write_bytes``: the
  rehydrate test goes red;
* ``O_NONBLOCK`` taken out of the opener (Linux): the FIFO tests go red, and
  they release the blocked thread, so a red run never hangs;
* ``openat2`` without ``RESOLVE_NO_SYMLINKS`` (``RESOLVE_BENEATH`` kept, the
  verifier's M7b): the relative in-root link test goes red on Linux.
"""
from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from acb_skills import safe_open as so

POSIX_FD = so._FD_WALK


@pytest.fixture(params=["openat2", "walk"] if POSIX_FD else ["portable"])
def opener_path(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run a test on each code path of the opener."""
    if request.param == "walk":
        monkeypatch.setattr(so, "_OPENAT2", False)
    elif request.param == "openat2":
        monkeypatch.setattr(so, "_OPENAT2", None)
    return request.param


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "agent-data" / "skills").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_text("HOST SECRET", encoding="utf-8")
    (root / "agent-data" / "notes.md").write_text("ok", encoding="utf-8")
    return root, outside


# ── the path grammar ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../x", "a/../b", "/etc/passwd", "a/./b", "a\x00b", "C:/x",
                                 "..\\x"])
def test_split_rel_refuses_a_path_that_could_leave_the_root(bad: str) -> None:
    with pytest.raises(so.UnsafePath):
        so.split_rel(bad)


def test_split_rel_drops_empty_parts() -> None:
    assert so.split_rel("a//b/") == ["a", "b"]
    assert so.split_rel("") == []


# ── reads and writes with no link ────────────────────────────────────────────


def test_a_plain_write_read_unlink_round_trip(tmp_path: Path, opener_path: str) -> None:
    root, _ = _tree(tmp_path)
    so.write_bytes(root, "outputs/t1/chart.txt", b"chart")
    assert (root / "outputs" / "t1" / "chart.txt").read_bytes() == b"chart"
    assert so.read_bytes(root, "outputs/t1/chart.txt") == b"chart"
    assert so.is_file(root, "outputs/t1/chart.txt")
    assert so.read_bytes(root, "outputs/missing.txt") is None
    assert so.unlink(root, "outputs/t1/chart.txt") is True
    assert so.unlink(root, "outputs/t1/chart.txt") is False
    assert not (root / "outputs" / "t1" / "chart.txt").exists()


def test_an_exclusive_write_refuses_an_existing_file(tmp_path: Path, opener_path: str) -> None:
    root, _ = _tree(tmp_path)
    so.write_bytes(root, "agent-data/x.md", b"one", exclusive=True)
    with pytest.raises(FileExistsError):
        so.write_bytes(root, "agent-data/x.md", b"two", exclusive=True)
    assert so.read_bytes(root, "agent-data/x.md") == b"one"


def test_a_read_past_its_limit_is_refused(tmp_path: Path, opener_path: str) -> None:
    root, _ = _tree(tmp_path)
    so.write_bytes(root, "agent-data/big.bin", b"x" * 100)
    with pytest.raises(so.UnsafePath):
        so.read_bytes(root, "agent-data/big.bin", limit=10)


def test_list_and_walk_report_real_entries(tmp_path: Path, opener_path: str) -> None:
    root, _ = _tree(tmp_path)
    so.write_bytes(root, "agent-data/skills/s/SKILL.md", b"---\n")
    assert so.list_dir(root, "agent-data") == [("skills", "dir"), ("notes.md", "file")]
    assert so.list_dir(root, "no-such-dir") is None
    walked = {p for p, _size, _mtime in so.walk_files(root, "agent-data")}
    assert walked == {"agent-data/notes.md", "agent-data/skills/s/SKILL.md"}


# ── a link at any depth ──────────────────────────────────────────────────────


def test_a_link_at_the_leaf_is_refused_for_a_read(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    os.symlink(outside / "secret.txt", root / "agent-data" / "leak.txt")
    with pytest.raises(so.UnsafePath):
        so.read_bytes(root, "agent-data/leak.txt")
    assert not so.is_file(root, "agent-data/leak.txt")


def test_a_link_at_the_leaf_is_never_written_through(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    os.symlink(outside / "secret.txt", root / "agent-data" / "leak.txt")
    with pytest.raises(so.UnsafePath):
        so.write_bytes(root, "agent-data/leak.txt", b"OVERWRITTEN")
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "HOST SECRET"


def test_a_link_in_the_middle_is_refused(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    os.symlink(outside, root / "outputs", target_is_directory=True)
    with pytest.raises(so.UnsafePath):
        so.read_bytes(root, "outputs/secret.txt")
    with pytest.raises(so.UnsafePath):
        so.write_bytes(root, "outputs/new.txt", b"x")
    with pytest.raises(so.UnsafePath):
        so.ensure_dir(root, "outputs/sub")
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]


def test_a_link_two_levels_down_is_refused(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    os.symlink(outside, root / "agent-data" / "skills" / "evil", target_is_directory=True)
    with pytest.raises(so.UnsafePath):
        so.read_bytes(root, "agent-data/skills/evil/secret.txt")
    with pytest.raises(so.UnsafePath):
        so.write_bytes(root, "agent-data/skills/evil/SKILL.md", b"x")
    assert not (outside / "SKILL.md").exists()


def test_a_relative_link_that_stays_in_the_root_is_refused(tmp_path: Path, opener_path: str) -> None:
    """The verifier's M7b: a link from one thread's folder to another's,
    ``outputs/t2/x -> ../t1/f``, never leaves the root, so only
    ``RESOLVE_NO_SYMLINKS`` (or the ``O_NOFOLLOW`` walk) refuses it."""
    root, _ = _tree(tmp_path)
    (root / "outputs" / "t1").mkdir(parents=True)
    (root / "outputs" / "t2").mkdir()
    (root / "outputs" / "t1" / "f").write_text("T1 PRIVATE", encoding="utf-8")
    os.symlink("../t1/f", root / "outputs" / "t2" / "x")
    os.symlink("../t1", root / "outputs" / "t2" / "d", target_is_directory=True)
    for rel in ("outputs/t2/x", "outputs/t2/d/f"):
        with pytest.raises(so.UnsafePath):
            so.read_bytes(root, rel)
        with pytest.raises(so.UnsafePath):
            so.write_bytes(root, rel, b"OVERWRITTEN")
    assert (root / "outputs" / "t1" / "f").read_text(encoding="utf-8") == "T1 PRIVATE"


def test_a_link_as_the_root_is_refused(tmp_path: Path, opener_path: str) -> None:
    _root, outside = _tree(tmp_path)
    link = tmp_path / "root-link"
    os.symlink(outside, link, target_is_directory=True)
    with pytest.raises(so.UnsafePath):
        so.read_bytes(link, "secret.txt")


def test_listing_and_walking_never_show_or_follow_a_link(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    os.symlink(outside, root / "agent-data" / "linked-dir", target_is_directory=True)
    os.symlink(outside / "secret.txt", root / "agent-data" / "linked-file")
    names = [n for n, _kind in so.list_dir(root, "agent-data") or []]
    assert "linked-dir" not in names and "linked-file" not in names
    walked = [p for p, _s, _m in so.walk_files(root, "agent-data")]
    assert all("linked" not in p and "secret" not in p for p in walked)


def test_unlink_removes_a_link_and_never_its_target(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    os.symlink(outside / "secret.txt", root / "agent-data" / "leak.txt")
    assert so.unlink(root, "agent-data/leak.txt") is True
    assert (outside / "secret.txt").exists()


def test_remove_tree_follows_no_link_inside_the_tree(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    run = root / "run-data"
    run.mkdir()
    (run / "data.csv").write_text("member rows", encoding="utf-8")
    os.symlink(outside, run / "escape", target_is_directory=True)
    assert so.remove_tree(root, "run-data") is True
    assert not run.exists()
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "HOST SECRET"
    assert so.remove_tree(root, "run-data") is False


# ── a FIFO never blocks a host thread (review P1, fix round 1) ───────────────


def _bounded(call: Any, release: Path, timeout: float = 5.0) -> tuple[bool, Any]:
    """Run *call* in a thread. ``(False, None)`` when it blocked past *timeout*.

    A blocked call is released by opening the FIFO from the other side, so a
    failing run of this test never hangs the suite.
    """
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            out["value"] = call()
        except BaseException as exc:  # recorded and checked by the caller
            out["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if not t.is_alive():
        return True, out
    for mode in (os.O_WRONLY | os.O_NONBLOCK, os.O_RDONLY | os.O_NONBLOCK):
        with contextlib.suppress(OSError):
            os.close(os.open(release, mode))
    t.join(timeout)
    return False, None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFO on Windows; CI runs it")
def test_a_fifo_is_refused_and_never_blocks(tmp_path: Path, opener_path: str) -> None:
    root, _ = _tree(tmp_path)
    (root / "outputs").mkdir()
    pipe = root / "outputs" / "pipe"
    os.mkfifo(pipe)
    calls = {
        "read": lambda: so.read_bytes(root, "outputs/pipe"),
        "stat": lambda: so.stat_file(root, "outputs/pipe"),
        "write": lambda: so.write_bytes(root, "outputs/pipe", b"x"),
        "open": lambda: so.open_read(root, "outputs/pipe"),
    }
    for name, call in calls.items():
        finished, out = _bounded(call, pipe)
        assert finished, f"{name} blocked on a FIFO"
        assert isinstance(out.get("error"), so.UnsafePath), (name, out)
    finished, out = _bounded(lambda: so.is_file(root, "outputs/pipe"), pipe)
    assert finished and out.get("value") is False


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFO on Windows; CI runs it")
def test_a_link_to_a_fifo_is_refused_and_never_blocks(tmp_path: Path, opener_path: str) -> None:
    root, outside = _tree(tmp_path)
    pipe = outside / "pipe"
    os.mkfifo(pipe)
    os.symlink(pipe, root / "agent-data" / "pipe-link")
    for call in (
        lambda: so.read_bytes(root, "agent-data/pipe-link"),
        lambda: so.write_bytes(root, "agent-data/pipe-link", b"x"),
    ):
        finished, out = _bounded(call, pipe)
        assert finished, "a link to a FIFO blocked"
        assert isinstance(out.get("error"), so.UnsafePath), out


# ── a racing swap (POSIX only: the race needs the descriptor walk) ───────────


@pytest.mark.skipif(not POSIX_FD, reason="the race needs dir_fd, which Windows lacks; CI runs it")
def test_a_racing_swap_of_a_parent_dir_never_wins(tmp_path: Path, opener_path: str) -> None:
    """A thread flips ``outputs`` between a real dir and a link to the host.

    Every read either sees the real file, refuses, or finds nothing. No read
    ever returns the host secret, and no write ever lands on the host.
    """
    root, outside = _tree(tmp_path)
    real = root / "outputs"
    real.mkdir()
    (real / "secret.txt").write_text("in the root", encoding="utf-8")
    parked = root / "parked"
    stop = threading.Event()

    def flip() -> None:
        while not stop.is_set():
            try:
                real.rename(parked)
                os.symlink(outside, real, target_is_directory=True)
                os.unlink(real)
                parked.rename(real)
            except OSError:
                continue

    t = threading.Thread(target=flip, daemon=True)
    t.start()
    seen: set[bytes | None] = set()
    try:
        for i in range(3000):
            try:
                seen.add(so.read_bytes(root, "outputs/secret.txt"))
            except (so.UnsafePath, OSError):
                seen.add(b"refused")
            with contextlib.suppress(so.UnsafePath, OSError):
                so.write_bytes(root, f"outputs/w{i % 5}.txt", b"w", make_parents=False)
    finally:
        stop.set()
        t.join(timeout=10)
    assert b"HOST SECRET" not in seen
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]


# ── each host reader and writer of §7.5 rule B uses the opener ───────────────


def test_the_sweep_never_reads_through_a_link(tmp_path: Path, opener_path: str) -> None:
    from acb_skills.code_tools import _collect_changed

    root, outside = _tree(tmp_path)
    os.symlink(outside, root / "agent-data" / "escape", target_is_directory=True)
    os.symlink(outside / "secret.txt", root / "agent-data" / "leak.txt")
    collected = dict(_collect_changed(root, since=0.0, subdirs=("agent-data",)))
    assert collected == {"agent-data/notes.md": b"ok"}
    assert all(b"HOST SECRET" not in data for data in collected.values())


def test_the_rehydrate_never_writes_through_a_link(
    tmp_path: Path, opener_path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from acb_memory import blob_store

    root, outside = _tree(tmp_path)
    os.symlink(outside / "secret.txt", root / "agent-data" / "leak.txt")
    os.symlink(outside, root / "outputs", target_is_directory=True)
    rows = {
        "agent-data/leak.txt": b"FROM THE STORE",
        "outputs/planted.txt": b"FROM THE STORE",
        "agent-data/fine.md": b"restored",
    }

    async def list_files(agent: str, prefix: Any = None, **kw: Any) -> list[Any]:
        return [
            blob_store.BlobMeta(agent, p, p.split("/")[0], blob_store._sha256(d), len(d), "text/plain")
            for p, d in rows.items()
        ]

    async def get_file(agent: str, path: str, **kw: Any) -> bytes | None:
        return rows.get(path)

    monkeypatch.setattr(blob_store, "list_files", list_files)
    monkeypatch.setattr(blob_store, "get_file", get_file)
    restored = asyncio.run(blob_store.rehydrate_workspace("agent-x", str(root), instance="o:x"))
    assert restored == 1
    assert (root / "agent-data" / "fine.md").read_bytes() == b"restored"
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "HOST SECRET"
    assert not (outside / "planted.txt").exists()


def test_every_rule_b_route_opens_through_the_opener() -> None:
    """A source fence: the workspace routes read and write with the opener."""
    import inspect

    from gateway.routes import workspace

    for fn in (
        workspace.get_workspace_file, workspace.write_workspace_file,
        workspace.delete_workspace_file, workspace.upload_files,
        workspace.promote_input_to_agent_data, workspace.get_artifact_file,
        workspace.write_artifact_file, workspace.upload_artifact,
        workspace._faultin_from_store,
    ):
        src = inspect.getsource(fn)
        for banned in (".write_bytes(", ".read_bytes(", ".write_text(", "open(file_path", ".unlink("):
            assert banned not in src.replace("safe_open.", "SAFE."), (fn.__name__, banned)


# ── a skill survives a lost disk copy (done-when 7, R8) ──────────────────────

from tests.unit._sandbox_tools_fakes import PA, short_tmp  # noqa: E402,F401
from tests.unit.test_chat_write_under_rls import graph_as_app, members  # noqa: E402,F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402,F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_maf_code_session import _run, clone  # noqa: E402,F401


@_DB_GATE
def test_a_skill_survives_a_lost_disk_copy_and_a_rehydrate(graph_as_app, clone) -> None:  # noqa: F811
    import asyncio
    import shutil

    from acb_memory import rehydrate_workspace

    a = graph_as_app.org_a
    skill = f"agent-data/skills/chart-{os.getpid()}/SKILL.md"
    body = "---\nname: chart\ndescription: Draw a chart.\n---\nRun the script.\n"
    seen: dict[str, Path] = {}

    async def write(store: Any, ws: Path, run_data: Path, slug: str) -> None:
        await store.write(skill, body)
        seen["ws"] = ws

    _run(a, clone, write)
    ws = seen["ws"]
    shutil.rmtree(ws / "agent-data")
    assert not (ws / skill).exists()
    restored = asyncio.run(rehydrate_workspace(PA, str(ws), instance=f"o:{a}", organization_id=a))
    assert restored >= 1
    assert (ws / skill).read_text(encoding="utf-8") == body
