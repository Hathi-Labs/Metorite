"""Task 2429 — a new App Workshop app reserves its slug before it writes a file (R8).

Code: ``gateway/routes/apps/lifecycle.py``, ``create_app`` and ``fork_app``.

The slug of an app is unique over all orgs (``apps_slug_key``). The folder of
each app is ``apps_root()/<slug>``, and all folders are siblings. FORCE RLS
shows a member only the rows of their own org. Before this fix, a member of
org B who chose the name of an app of org A wrote ``app.json`` and
``index.html`` into the folder of org A. Only then did the INSERT fail on the
unique key.

The suite runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_app_create_slug_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance.**

a. Org B creates an app with the name of an app of org A. The folder of org A
   does not change, also when it is not on disk (a folder that the store
   rebuilds later). Org B gets a new slug and its own folder.
b. The same for a fork: org B forks its own app to a name whose slug org A
   holds.
c. A folder on disk with no row is never written. The new app takes the next
   slug.
d. A failed file write removes the folder that the request made, and writes
   no row. Every other folder stays the same, byte for byte.
e. A folder that another writer made after the check is never written or
   removed. The request gives its row back and takes the next slug, with no
   409.
f. After 10 sequential slugs, the create takes a slug with a random suffix.
   When those are taken too, it answers 409 with a next step.
g. A broken link at the slug path counts as taken, and is never claimed.

**The mutations (R7).** Commit before each, and restore with git after.
Before the fix, 8 of the first 9 tests were red on the old code. The ninth
checks the CI lists. Each result below is from the fix round, on 2026-11-09,
with the 12 tests of this suite.

* M1. ``create_app``: write the folder BEFORE the reserve (the old order).
  9 go red, both cases of ``test_r8_create_never_writes_the_folder_of_another_org``
  among them.
* M2. ``_discard_new_folder``: remove nothing (drop the rollback).
  ``test_r8_a_failed_write_removes_the_new_folder_and_the_row`` goes red, both
  cases.
* M3. ``fork_app``: write the folder BEFORE the reserve. Both cases of
  ``test_r8_fork_never_writes_the_folder_of_another_org`` and the fork case of
  the rollback test go red.
* M4. ``_claim_folder``: ``mkdir(exist_ok=True)``.
  ``test_r8_a_late_folder_is_never_written_or_removed`` goes red.
* M6. ``_RESERVE_SQL``: drop ``ON CONFLICT (slug) DO NOTHING``. The
  ``off_disk`` case of the create test and of the fork test go red.
* M7. ``_folder_free``: answer True.
  ``test_r8_a_broken_link_at_the_slug_path_counts_as_taken`` goes red. A folder
  with no row is also caught by the claim, so the orphan test stays green.
* MV2. ``_discard_new_folder``: remove ``workspace.parent``, so every app of
  every org goes. Both cases of the rollback test go red on the snapshot.
* M8. ``_reserve_app``: keep the row of a folder that another writer took.
  ``test_r8_a_late_folder_is_never_written_or_removed`` goes red.
* M9. ``_folder_free``: drop the broken-link clause.
  ``test_r8_a_broken_link_at_the_slug_path_counts_as_taken`` goes red.
* M10. ``HEX_TRIES = 0``.
  ``test_r8_after_ten_slugs_the_create_takes_a_random_suffix`` goes red.

The first version's M5 (``made`` set before the claim) has no place now: the
claim moved into ``_reserve_app``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.apps import lifecycle
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their rows.
ANA = f"ana.{TAG}@slug2429.test"
BEN = f"ben.{TAG}@slug2429.test"
_LIKE = f"%.{TAG}@slug2429.test"

#: The text that org A keeps in its folder. Each test checks that it stays.
SECRET = "<p>Only org A may read this.</p>\n"


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    root = tmp_path / "custom_apps"
    monkeypatch.setattr(get_settings(), "custom_apps_root", str(root))
    # The activity feed is Redis, and this suite proves the folders and rows.
    monkeypatch.setattr(lifecycle, "publish_app_activity", lambda *a, **k: None)
    yield SimpleNamespace(p=promoted, root=root, tmp=tmp_path)
    # The files of an app go by the cascade of their foreign key.
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _user(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


def _run(world: Any, org: str, call: Any) -> Any:
    """Run ``call()`` in a new event loop, bound to *org*, on the app role.

    Answers the result, or the exception that the call raised."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            tok = bind_tenant(org)
            try:
                return await call()
            except Exception as exc:  # the test reads it
                return exc
            finally:
                release_tenant(tok)
    return asyncio.run(go())


def _create(world: Any, org: str, name: str, member: str) -> Any:
    return _run(world, org, lambda: lifecycle.create_app(
        lifecycle.AppCreate(name=name), user=_user(member)))


def _fork(world: Any, org: str, slug: str, member: str) -> Any:
    return _run(world, org, lambda: lifecycle.fork_app(slug, user=_user(member)))


def _rows(world: Any) -> list[tuple[str, str, str]]:
    """The rows of this suite, as the superuser: slug, org, folder."""
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT slug, organization_id::text, workspace_path FROM apps "
            "WHERE owner_email LIKE :t ORDER BY created_at, slug"), {"t": _LIKE})]


def _keep_org_a_folder(world: Any, slug: str, on_disk: bool) -> Path:
    """Write the secret of org A into its folder. Off disk, move the folder
    away, as after a disk loss. The store then rebuilds it on the next read."""
    folder = world.root / slug
    (folder / "index.html").write_text(SECRET, encoding="utf-8")
    if not on_disk:
        folder.rename(world.tmp / f"moved-{slug}")
    return folder


def _assert_org_a_folder_kept(world: Any, folder: Path, on_disk: bool, slug: str) -> None:
    if on_disk:
        assert (folder / "index.html").read_text(encoding="utf-8") == SECRET, (
            "org B wrote over the index.html of org A")
        assert json.loads((folder / "app.json").read_text(encoding="utf-8"))["slug"] == slug, (
            "org B wrote over the app.json of org A")
    else:
        assert not folder.exists(), (
            "org B wrote into the folder of org A, which the store rebuilds later")


# ── a. Create ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("on_disk", [True, False], ids=["on_disk", "off_disk"])
def test_r8_create_never_writes_the_folder_of_another_org(world, on_disk: bool) -> None:
    a, b = world.p.org_a, world.p.org_b
    name = f"Ledger {TAG}"
    first = _create(world, a, name, ANA)
    assert isinstance(first, lifecycle.AppDetail), first
    assert first.slug == f"ledger-{TAG}"
    folder = _keep_org_a_folder(world, first.slug, on_disk)

    second = _create(world, b, name, BEN)

    _assert_org_a_folder_kept(world, folder, on_disk, first.slug)
    assert isinstance(second, lifecycle.AppDetail), second
    assert second.slug != first.slug
    mine = world.root / second.slug
    assert json.loads((mine / "app.json").read_text(encoding="utf-8"))["slug"] == second.slug
    assert _rows(world) == [
        (first.slug, a, str(world.root / first.slug)),
        (second.slug, b, str(mine)),
    ]


# ── b. Fork ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("on_disk", [True, False], ids=["on_disk", "off_disk"])
def test_r8_fork_never_writes_the_folder_of_another_org(world, on_disk: bool) -> None:
    a, b = world.p.org_a, world.p.org_b
    source = _create(world, b, f"Quotes {TAG}", BEN)
    assert isinstance(source, lifecycle.AppDetail), source
    # Org A holds the slug that the fork of org B asks for first.
    taken = _create(world, a, f"Quotes {TAG} fork", ANA)
    assert isinstance(taken, lifecycle.AppDetail), taken
    assert taken.slug == f"quotes-{TAG}-fork"
    folder = _keep_org_a_folder(world, taken.slug, on_disk)

    fork = _fork(world, b, source.slug, BEN)

    _assert_org_a_folder_kept(world, folder, on_disk, taken.slug)
    assert isinstance(fork, lifecycle.AppDetail), fork
    assert fork.slug not in (taken.slug, source.slug)
    mine = world.root / fork.slug
    assert json.loads((mine / "app.json").read_text(encoding="utf-8"))["slug"] == fork.slug
    assert [(s, o) for s, o, _ in _rows(world)] == [
        (source.slug, b), (taken.slug, a), (fork.slug, b)]


# ── c. A folder with no row ───────────────────────────────────────────────


def test_r8_create_skips_a_folder_with_no_row(world) -> None:
    a = world.p.org_a
    orphan = world.root / f"orphan-{TAG}"
    orphan.mkdir(parents=True)
    (orphan / "keep.txt").write_text(SECRET, encoding="utf-8")

    out = _create(world, a, f"Orphan {TAG}", ANA)

    assert sorted(p.name for p in orphan.iterdir()) == ["keep.txt"], (
        "the create wrote into a folder that it did not make")
    assert (orphan / "keep.txt").read_text(encoding="utf-8") == SECRET
    assert isinstance(out, lifecycle.AppDetail), out
    assert out.slug == f"orphan-{TAG}-2"
    assert [s for s, _, _ in _rows(world)] == [out.slug]


# ── d. The rollback ───────────────────────────────────────────────────────


def _failing_write(*args: Any) -> None:
    """A file write that fails part way: the folder holds one file."""
    workspace = args[0]
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "index.html").write_text("half", encoding="utf-8")
    raise OSError("No space left on device")


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under *root*, by its path under *root*, with its bytes."""
    return {
        f.relative_to(root).as_posix(): f.read_bytes()
        for f in sorted(root.rglob("*")) if f.is_file()
    }


@pytest.mark.parametrize("route", ["create", "fork"])
def test_r8_a_failed_write_removes_the_new_folder_and_the_row(
        world, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    a, b = world.p.org_a, world.p.org_b
    other = _create(world, a, f"Other {TAG}", ANA)
    assert isinstance(other, lifecycle.AppDetail), other
    (world.root / other.slug / "index.html").write_text(SECRET, encoding="utf-8")
    source = _create(world, b, f"Source {TAG}", BEN)
    assert isinstance(source, lifecycle.AppDetail), source
    before = _tree(world.root)
    assert f"{other.slug}/index.html" in before and f"{source.slug}/app.json" in before
    if route == "create":
        monkeypatch.setattr(lifecycle, "_scaffold_workspace", _failing_write)
        out = _create(world, b, f"Broken {TAG}", BEN)
        new = world.root / f"broken-{TAG}"
    else:
        monkeypatch.setattr(lifecycle, "_fork_workspace", _failing_write)
        out = _fork(world, b, source.slug, BEN)
        new = world.root / f"source-{TAG}-fork"

    assert isinstance(out, OSError), out
    assert not new.exists(), "the failed write left its folder"
    assert _tree(world.root) == before, (
        "the failed write changed or removed a folder that it did not make")
    assert [s for s, _, _ in _rows(world)] == [other.slug, source.slug], (
        "the failed write left its row")


# ── e. A folder that appears after the check ──────────────────────────────


def test_r8_a_late_folder_is_never_written_or_removed(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    """The forced order: the check says the slug path is free, the INSERT
    succeeds, and the folder is there all the same. That is the case when
    another request failed after its claim, rolled its row back, and has not
    yet removed its folder. The request never writes or removes that folder.
    It deletes its own row, and takes the next slug with no 409."""
    a = world.p.org_a
    late = world.root / f"late-{TAG}"
    late.mkdir(parents=True)
    (late / "keep.txt").write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(lifecycle, "_folder_free", lambda folder: True)

    out = _create(world, a, f"Late {TAG}", ANA)

    assert late.is_dir(), "the create removed a folder that it did not make"
    assert sorted(p.name for p in late.iterdir()) == ["keep.txt"], (
        "the create wrote into a folder that it did not make")
    assert isinstance(out, lifecycle.AppDetail), out
    assert out.slug == f"late-{TAG}-2"
    assert _rows(world) == [(out.slug, a, str(world.root / out.slug))], (
        "the row of the taken folder stayed")


# ── f. A common name never runs out ───────────────────────────────────────


def _busy_folders(world: Any, extra: tuple[str, ...] = ()) -> list[Path]:
    """Empty folders at ``busy-<tag>``, ``busy-<tag>-2`` … ``busy-<tag>-10``,
    and at ``busy-<tag>-<x>`` for each *x* in *extra*."""
    names = [f"busy-{TAG}"] + [f"busy-{TAG}-{i}" for i in range(2, 11)]
    names += [f"busy-{TAG}-{x}" for x in extra]
    folders = [world.root / n for n in names]
    for f in folders:
        f.mkdir(parents=True)
    return folders


def test_r8_after_ten_slugs_the_create_takes_a_random_suffix(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    a = world.p.org_a
    busy = _busy_folders(world, ("aaaaaaaa",))
    hexes = iter(["aaaaaaaa", "bbbbbbbb", "cccccccc"])
    monkeypatch.setattr(lifecycle, "_random_hex", lambda: next(hexes))

    out = _create(world, a, f"Busy {TAG}", ANA)

    assert isinstance(out, lifecycle.AppDetail), out
    assert out.slug == f"busy-{TAG}-bbbbbbbb"
    assert all(not any(f.iterdir()) for f in busy), "the create wrote a busy folder"
    assert [s for s, _, _ in _rows(world)] == [out.slug]


def test_r8_an_unfree_name_answers_409_with_a_next_step(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    a = world.p.org_a
    _busy_folders(world, ("aaaaaaaa", "bbbbbbbb", "cccccccc"))
    hexes = iter(["aaaaaaaa", "bbbbbbbb", "cccccccc"])
    monkeypatch.setattr(lifecycle, "_random_hex", lambda: next(hexes))

    out = _create(world, a, f"Busy {TAG}", ANA)

    assert isinstance(out, HTTPException) and out.status_code == 409, out
    assert out.detail == (
        "Could not pick a free short name for this app. "
        "Try a slightly different name.")
    assert _rows(world) == []


# ── g. A broken link ──────────────────────────────────────────────────────


def test_r8_a_broken_link_at_the_slug_path_counts_as_taken(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    a = world.p.org_a
    world.root.mkdir(parents=True, exist_ok=True)
    link = world.root / f"link-{TAG}"
    target = world.tmp / f"nowhere-{TAG}"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this host cannot make a symlink: {exc}")
    claimed: list[Path] = []
    real_claim = lifecycle._claim_folder

    def spy(workspace: Path) -> bool:
        claimed.append(workspace)
        return real_claim(workspace)

    monkeypatch.setattr(lifecycle, "_claim_folder", spy)

    out = _create(world, a, f"Link {TAG}", ANA)

    assert link not in claimed, "the create tried to claim a broken link"
    # Windows answers the target with a "\\?\" prefix.
    points_to = Path(os.readlink(link)).as_posix().removeprefix("//?/")
    assert link.is_symlink() and points_to == target.as_posix()
    assert not target.exists(), "the create made the target of the link"
    assert isinstance(out, lifecycle.AppDetail), out
    assert out.slug == f"link-{TAG}-2"
    assert [s for s, _, _ in _rows(world)] == [out.slug]


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_app_create_slug_r8\.py \\?$", workflow, re.M), name
