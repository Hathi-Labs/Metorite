"""Task 2462 follow-up — the folder of an app goes, also on a cancel (hermetic).

Code: ``gateway/routes/apps/lifecycle.py`` ``_remove_folder``,
``_NewFolder``, ``create_app``, ``fork_app`` and ``_sweep_tombstones``.

**Acceptance.**

a. A cancel during the write of a create or a fork leaves no folder. Before
   the fix, ``except Exception`` did not catch ``CancelledError``, and a
   restart left a folder with no row. A fork left the full source.
b. The removal never runs under a write that still runs. The writer
   removes the folder when it stops, and a write that has not started
   never starts.
c. A read-only file in a read-only folder goes. A failure that stays is
   logged with the folder, and never with the contents of a file.
d. The delete sweep removes an old tombstone and keeps a new one.

**The mutations (R7).**

* M1. ``create_app``: ``except Exception``. Test a (create) goes red.
* M2. ``fork_app``: ``except Exception``. Test a (fork) goes red.
* M3. ``_NewFolder.discard``: remove when busy. Test a goes red, because
  the late write makes the folder again.
* M4. ``_remove_folder``: no ``onexc``. Test c goes red.
* M5. ``_sweep_tombstones``: no age check. Test d goes red.
"""

from __future__ import annotations

import asyncio
import os
import stat
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from acb_auth import UserContext, UserRole
from gateway.routes.apps import lifecycle

_SECRET = "only-this-app-may-read-this"


def _user() -> UserContext:
    return UserContext(email="ana@cleanup.test", role=UserRole.EMPLOYEE)


@asynccontextmanager
async def _no_session():
    yield SimpleNamespace()


def _fake_reserve(root: Path):
    async def reserve(db: Any, name: str, **_: Any):
        workspace = root / "new-app-id"
        assert lifecycle._claim_folder(workspace)
        return SimpleNamespace(slug="new-app"), workspace, {"slug": "new-app"}
    return reserve


def _slow_write(started: threading.Event, release: threading.Event):
    """A write that waits in the executor thread, then writes again."""
    def write(workspace: Path, *_: Any) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "index.html").write_text(_SECRET, encoding="utf-8")
        started.set()
        assert release.wait(10)
        # The real scaffold does ``mkdir(exist_ok=True)`` and git init.
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "app.json").write_text("{}", encoding="utf-8")
    return write


def _cancel_mid_write(call: Any, root: Path, started: threading.Event,
                      release: threading.Event) -> None:
    async def go() -> None:
        task = asyncio.ensure_future(call())
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The write still runs, so the folder stays until it stops.
        assert (root / "new-app-id").exists()
        release.set()
    # ``asyncio.run`` waits for the executor thread before it returns.
    asyncio.run(go())


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    apps = tmp_path / "custom_apps"
    apps.mkdir()
    monkeypatch.setattr(lifecycle, "_tenant_session", _no_session)
    monkeypatch.setattr(lifecycle, "_reserve_app", _fake_reserve(apps))
    return apps


# ── a. A cancel during a create or a fork leaves no folder ────────────────


def test_a_cancel_during_the_create_leaves_no_folder(
        root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(lifecycle, "_scaffold_workspace", _slow_write(started, release))

    _cancel_mid_write(
        lambda: lifecycle.create_app(lifecycle.AppCreate(name="New app"), user=_user()),
        root, started, release)

    assert not (root / "new-app-id").exists(), "a cancelled create left its folder"
    assert list(root.iterdir()) == []


def test_a_cancel_during_the_fork_leaves_no_copy_of_the_source(
        root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = root / "source-id"
    source.mkdir()
    started, release = threading.Event(), threading.Event()
    row = SimpleNamespace(name="Src", icon="", description="", manifest="{}")

    async def get_app(*_: Any, **__: Any) -> Any:
        return row, []

    async def ensure(*_: Any) -> Path:
        return source

    monkeypatch.setattr(lifecycle, "get_app_or_404", get_app)
    monkeypatch.setattr(lifecycle, "ensure_workspace", ensure)
    monkeypatch.setattr(lifecycle, "_read_workspace_files", lambda _ws: {})
    monkeypatch.setattr(lifecycle, "_fork_workspace", _slow_write(started, release))

    _cancel_mid_write(lambda: lifecycle.fork_app("src", user=_user()), root, started, release)

    assert not (root / "new-app-id").exists(), "a cancelled fork left the source on disk"
    assert [p.name for p in root.iterdir()] == ["source-id"]


def test_a_failed_write_still_removes_the_folder(
        root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(workspace: Path, *_: Any) -> None:
        (workspace / "index.html").write_text(_SECRET, encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(lifecycle, "_scaffold_workspace", fail)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(lifecycle.create_app(lifecycle.AppCreate(name="New app"), user=_user()))
    assert list(root.iterdir()) == []


# ── b. The removal never runs under a write ───────────────────────────────


def test_a_discard_before_the_write_starts_stops_the_write(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    folder.mkdir()
    made = lifecycle._NewFolder(folder)
    calls: list[Path] = []

    made.discard()
    made.write(lambda p: calls.append(p) or p.mkdir(exist_ok=True), folder)

    assert calls == [], "a discarded folder was written"
    assert not folder.exists()


# ── c. A read-only tree goes, and a failure is logged ─────────────────────


def _read_only_tree(folder: Path) -> Path:
    sub = folder / "src"
    sub.mkdir(parents=True)
    leaf = sub / "secret.txt"
    leaf.write_text(_SECRET, encoding="utf-8")
    leaf.chmod(stat.S_IRUSR)
    sub.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return sub


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores the read-only bit")
def test_a_read_only_file_in_a_read_only_folder_is_removed(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    _read_only_tree(folder)

    with structlog.testing.capture_logs() as caps:
        assert lifecycle._remove_folder(folder) is True

    assert not folder.exists()
    assert [c for c in caps if c["event"] == "apps.folder_remove_failed"] == []


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores the read-only bit")
def test_a_failure_that_stays_is_logged_without_the_contents(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "app"
    sub = _read_only_tree(folder)
    monkeypatch.setattr(lifecycle, "_make_writable", lambda _path: None)
    try:
        with structlog.testing.capture_logs() as caps:
            assert lifecycle._remove_folder(folder) is False
    finally:
        sub.chmod(stat.S_IRWXU)

    [line] = [c for c in caps if c["event"] == "apps.folder_remove_failed"]
    assert line["folder"] == str(folder)
    assert line["errors"] >= 1
    assert "PermissionError" in line["error"]
    assert _SECRET not in repr(line), "the log holds the contents of a file"


def test_a_link_at_the_folder_is_unlinked_and_its_target_stays(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    link = tmp_path / "app"
    link.symlink_to(target, target_is_directory=True)

    assert lifecycle._remove_folder(link) is True

    assert not os.path.lexists(link)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


# ── d. The sweep of tombstones ────────────────────────────────────────────


def test_the_sweep_removes_an_old_tombstone_and_keeps_a_new_one(tmp_path: Path) -> None:
    old = tmp_path / f".deleted-aaa-{int(time.time()) - lifecycle._TOMBSTONE_MAX_AGE_S - 5}"
    new = tmp_path / f".deleted-bbb-{int(time.time())}"
    app = tmp_path / "an-app"
    for folder in (old, new, app):
        folder.mkdir()
        (folder / "index.html").write_text(_SECRET, encoding="utf-8")

    with structlog.testing.capture_logs() as caps:
        lifecycle._sweep_tombstones(tmp_path)

    assert not old.exists(), "an old tombstone stayed"
    assert new.exists(), "the sweep removed the tombstone of an open delete"
    assert app.exists(), "the sweep removed an app folder"
    assert [c["name"] for c in caps if c["event"] == "apps.tombstone_left"] == [old.name]


def test_a_tombstone_name_is_never_a_slug_or_a_clean_app_path(tmp_path: Path) -> None:
    from fastapi import HTTPException
    from gateway.routes.apps._common import SLUG_RE, clean_app_rel

    tomb = lifecycle._tombstone_for(tmp_path, "0b9e6c1e-0000-4000-8000-000000000000")
    assert tomb.parent == tmp_path
    assert tomb.name.startswith(lifecycle._TOMBSTONE_PREFIX)
    assert not SLUG_RE.match(tomb.name)
    with pytest.raises(HTTPException):
        clean_app_rel(tomb.name)
