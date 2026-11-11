"""Task 2462 — a deleted app frees its slug, and no old chat follows it (R8).

Code: ``gateway/routes/apps/lifecycle.py`` ``delete_app``,
``orchestrator/executor.py`` ``_app_workspace_of_org`` and
``_session_workspace_override``, and ``orchestrator/sandbox_broker.py``
``_mount_source_for``.

The slug of an app is unique over all orgs. Before WS-STU A3b-1, each folder
was ``apps_root()/<slug>``. Since A3b-1, the folder of a new app is
``apps_root()/<app id>``, so step 3 below gives org B a new folder. The
checks stay, because an app made before A3b-1 keeps its slug folder. A chat of the App Workshop stores its folder in
``chat_session.workspace_path``. Before this fix, the hard delete left that
path in place, and the executor and the broker checked only that the path lay
under the root. So:

1. Org A makes app ``foo`` and binds a Workshop chat to its folder.
2. Org A deletes ``foo``. The slug is free.
3. Org B makes ``foo``, and gets the same folder.
4. The old chat of org A runs app-builder in the folder of org B.

The suite runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, three members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_app_delete_workspace_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance.**

a. The hard delete clears the folder of each chat of the org that points into
   the app folder, for every member. A chat of a sibling app whose slug starts
   with the same text keeps its folder. The archive step clears nothing.
b. A chat of org A whose path names the folder that org B took never runs
   there. The executor falls back, and the broker refuses the mount. A chat
   of org B on its own folder still runs there.
c. The owner check answers for the run's org only, with the
   ``ACB_GRAPH_TENANT_BIND`` flag OFF, also for a role that bypasses RLS.

**The mutations (R7).**

* M1. ``delete_app``: drop the ``_UNBIND_SESSIONS_SQL`` statement. Test a
  goes red.
* M2. ``_session_workspace_override``: drop the ``_app_workspace_of_org``
  clause. Test b goes red.
* M3. ``_mount_source_for``: drop the ``_app_dir_of_org`` check. Test b goes
  red.
* M4. ``_ORG_APP_FOLDERS_SQL``: drop the ``organization_id`` filter. The
  superuser case of test c goes red.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from acb_auth import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.apps import lifecycle
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@ws2462.test"
BOB = f"bob.{TAG}@ws2462.test"
CAT = f"cat.{TAG}@ws2462.test"
_LIKE = f"%.{TAG}@ws2462.test"

#: What org B keeps in its folder.
SECRET = "<p>Only org B may read this.</p>\n"

_BUILDER = {"allow_session_workspace": True}


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    root = tmp_path / "custom_apps"
    monkeypatch.setattr(get_settings(), "custom_apps_root", str(root))
    monkeypatch.setattr(lifecycle, "publish_app_activity", lambda *a, **k: None)
    # ``acb_graph`` sessions open as the app role, and bind the tenant.
    eng = create_engine(promoted.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    try:
        yield SimpleNamespace(p=promoted, root=root, tmp=tmp_path)
    finally:
        eng.dispose()
        with promoted.admin_engine.begin() as c:
            c.execute(text("DELETE FROM chat_session WHERE user_id LIKE :t"), {"t": _LIKE})
            c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _user(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


def _run(world: Any, org: str, call: Any) -> Any:
    """Run ``call()`` in a new event loop, bound to *org*, on the app role."""
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


def _create(world: Any, org: str, name: str, member: str) -> lifecycle.AppDetail:
    out = _run(world, org, lambda: lifecycle.create_app(
        lifecycle.AppCreate(name=name), user=_user(member)))
    assert isinstance(out, lifecycle.AppDetail), out
    return out


def _delete(world: Any, org: str, slug: str, member: str) -> Any:
    return _run(world, org, lambda: lifecycle.delete_app(slug, user=_user(member)))


def _chat(world: Any, org: str, member: str, path: Path | None) -> str:
    """A Workshop chat of *member*, seeded as the superuser."""
    sid = f"ws2462-{uuid.uuid4().hex[:12]}"
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, workspace_path, "
            "organization_id) VALUES (:id, :u, 'app-builder', :p, :o)"),
            {"id": sid, "u": member, "p": None if path is None else str(path), "o": org})
    return sid


def _path_of(world: Any, sid: str) -> str | None:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT workspace_path FROM chat_session WHERE id = :id"), {"id": sid}).scalar()


def _set_path(world: Any, sid: str, path: Path) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET workspace_path = :p WHERE id = :id"),
                  {"p": str(path), "id": sid})


def _override(sid: str, org: str) -> str | None:
    """The executor's folder for a run of *org* on chat *sid*."""
    from orchestrator import executor

    executor._RUN_ORG[sid] = org
    try:
        return executor._session_workspace_override(sid, _BUILDER)
    finally:
        executor._RUN_ORG.pop(sid, None)


# ── a. The delete unbinds the chats ───────────────────────────────────────


def test_r8_the_hard_delete_unbinds_every_chat_of_the_org(world) -> None:
    a = world.p.org_a
    foo = _create(world, a, f"Foo {TAG}", ANA)
    # A sibling whose slug starts with the slug of foo.
    sib = _create(world, a, f"Foo {TAG} x", ANA)
    assert sib.slug.startswith(foo.slug)
    folder = Path(foo.workspace_path).resolve()
    (folder / "src").mkdir()
    chat_ana = _chat(world, a, ANA, folder)
    chat_bob = _chat(world, a, BOB, folder)
    chat_sub = _chat(world, a, BOB, folder / "src")
    chat_raw = _chat(world, a, ANA, Path(foo.workspace_path))
    sib_folder = Path(sib.workspace_path).resolve()
    chat_sib = _chat(world, a, ANA, sib_folder)
    chat_none = _chat(world, a, ANA, None)

    archived = _delete(world, a, foo.slug, ANA)
    assert archived == {"slug": foo.slug, "status": "archived"}, archived
    assert _path_of(world, chat_ana) == str(folder), "the archive unbound a chat"

    gone = _delete(world, a, foo.slug, ANA)

    assert gone == {"slug": foo.slug, "deleted": True}, gone
    assert not folder.exists()
    for sid in (chat_ana, chat_bob, chat_sub, chat_raw):
        assert _path_of(world, sid) is None, f"the delete left chat {sid} bound to the folder"
    assert _path_of(world, chat_sib) == str(sib_folder), (
        "the delete unbound the chat of a sibling app")
    assert _path_of(world, chat_none) is None


# ── b. A stale path never reaches the folder of another org ───────────────


def test_r8_a_stale_chat_never_runs_in_the_folder_that_another_org_took(world) -> None:
    from orchestrator import sandbox_broker as sb

    a, b = world.p.org_a, world.p.org_b
    foo = _create(world, a, f"Bar {TAG}", ANA)
    old = Path(foo.workspace_path).resolve()
    chat_a = _chat(world, a, ANA, old)
    assert _override(chat_a, a) == str(old), "org A cannot run in its own app"
    assert _delete(world, a, foo.slug, ANA)["status"] == "archived"
    assert _delete(world, a, foo.slug, ANA)["deleted"] is True

    took = _create(world, b, f"Bar {TAG}", CAT)
    assert took.slug == foo.slug, "org B did not get the freed slug"
    # A3b-1: the folder is the app id, so org B never gets the old folder.
    folder = Path(took.workspace_path).resolve()
    assert folder != old, "org B got the folder of the deleted app of org A"
    (folder / "index.html").write_text(SECRET, encoding="utf-8")
    chat_b = _chat(world, b, CAT, folder)
    # A stale path of org A that names a folder of org B. Before A3b-1 the
    # folder of a freed slug was the same folder, and an app made before
    # A3b-1 keeps it.
    _set_path(world, chat_a, folder)

    assert _override(chat_a, a) is None, "the run of org A works in the folder of org B"
    with pytest.raises(sb.SandboxRefused, match="not an app of its organization"):
        sb._mount_source_for(sb.APP_BUILDER_AGENT, "", a, str(folder))
    assert _override(chat_b, b) == str(folder)
    assert sb._mount_source_for(sb.APP_BUILDER_AGENT, "", b, str(folder)) == folder

    # The file routes of the gateway refuse it too: org A reads no app there.
    from gateway.routes.workspace import _allowed_workspace

    for write in (False, True):
        assert _allowed_workspace(
            str(folder), session_id=chat_a, user_email=ANA, organization_id=a,
            write=write) is None, f"org A may use the folder of org B (write={write})"
    assert _allowed_workspace(
        str(folder), session_id=chat_b, user_email=CAT, organization_id=b,
        write=True) == folder


# ── c. The org filter holds for a role that RLS does not bind ────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_the_owner_check_answers_for_the_run_org_only(
        world, monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    """The check holds with ``ACB_GRAPH_TENANT_BIND`` OFF. A role that
    bypasses RLS sees every org, and the explicit ``organization_id`` filter
    still answers for the run's org only."""
    from orchestrator import executor

    a, b = world.p.org_a, world.p.org_b
    mine = _create(world, b, f"Baz {TAG}", CAT)
    folder = Path(mine.workspace_path).resolve()
    if role == "superuser":
        admin = sessionmaker(bind=world.p.admin_engine, expire_on_commit=False, future=True)
        monkeypatch.setattr(graph_db, "_session_factory", lambda: admin)
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND")

    assert executor._app_workspace_of_org(folder, b) is True
    assert executor._app_workspace_of_org(folder / "src", b) is True
    assert executor._app_workspace_of_org(folder, a) is False
    assert executor._app_workspace_of_org(folder, None) is False
    assert executor._app_workspace_of_org(world.root.resolve(), b) is False


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(
            r"^\s+tests/unit/test_app_delete_workspace_r8\.py \\?$", workflow, re.M), name
