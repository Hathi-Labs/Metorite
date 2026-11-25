"""Task 3975 — the org never sees an unreviewed write tool (R8).

Code: ``gateway/routes/apps/publish.py`` (``require_org_review``,
``rollback_app``, ``publish_app``) and ``gateway/routes/apps/lifecycle.py``
(``patch_app``).

**The fault.** The admin review of §4.8 ran at publish time only. A version
published while the app was private needs no review, so it is ``auto``, even
when it asks for a write tool. Two paths then put it in front of the org with
no review:

1. ``POST /{slug}/rollback`` on an org app, to that ``auto`` version.
2. ``PATCH /{slug}`` with ``visibility = "org"``, while that version is live.

**The race (review of 8b3f1bd33).** ``load_app`` reads with no lock. So a
check could pass on a state that a parallel request was about to change.
Each route now takes :func:`publish.lock_app_row` first, and reads the
visibility and the live version from under it (tests i).

**The fix.** One check, :func:`publish.require_org_review`, with the one
query of an approved scope set that ``publish_app`` also uses. An org app
whose live version asks for a write tool needs an ``approved`` version with
the same ``scope_set_hash``. Otherwise the call is 409 and writes nothing.

**The visibility paths (found 2026-11-25).** ``publish_app`` (its own gate,
now on the shared query), ``patch_app`` (fixed here), and
``tools._apply_publish_review`` (the admin approval itself). A rollback
changes what the org sees, so it is fixed here too. ``create_app`` and
``fork_app`` make a private app. ``packages_store`` makes a private package
and never changes the visibility. ``load_app`` hides a package from the
slug routes.

**The mutations (R7).** On 2026-11-25 each guard was removed in turn and this
suite ran. Without the call in ``rollback_app``, a went red. Without the call
in ``patch_app``, d and the second e went red. With ``require_org_review``
made to ignore the approved scope set, b, the first e and the one-query
test went red. With the row lock taken out of each route, the i test of
that route went red.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_app_visibility_review_r8.py -v -rs

Without the database every R8 test here SKIPS, and the run reads green. A
skip is not a pass.
"""

from __future__ import annotations

import asyncio
import json
import threading
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
from gateway.routes.apps import lifecycle, publish, tools
from gateway.routes.apps._common import scope_set_hash
from sqlalchemy import text

from tests.unit import _r8_lists as r8_lists
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@visibility3975.test"
_LIKE = f"%.{TAG}@visibility3975.test"

WRITE = ["tool:demo.write"]
READ = ["tool:demo.read"]


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _user(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


def _run(world: Any, call: Any) -> Any:
    """Run ``call()`` on the app role, bound to org A. Raises what it raised."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            tok = bind_tenant(world.org)
            try:
                return await call()
            finally:
                release_tenant(tok)
    return asyncio.run(go())


def _caught(world: Any, call: Any) -> Any:
    """Like :func:`_run`, but answers an HTTPException instead of raising it."""
    async def wrapped() -> Any:
        try:
            return await call()
        except HTTPException as exc:
            return exc
    return _run(world, wrapped)


async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
    return {}


@pytest.fixture
def world(promoted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "custom_apps_root", str(tmp_path / "apps"))
    # The activity feed is Redis. This suite proves the pointer and the column.
    for mod in (lifecycle, publish):
        monkeypatch.setattr(mod, "publish_app_activity", lambda *a, **k: None)
    # The registry is empty since D52. One write tool and one read tool.
    monkeypatch.setattr(tools, "_TOOL_REGISTRY", {
        "demo.read": tools.ToolSpec("demo", True, False, False, _handler),
        "demo.write": tools.ToolSpec("demo", False, True, False, _handler),
    })
    proposals: list[Any] = []

    async def _submit(proposal: Any) -> dict[str, Any]:
        proposals.append(proposal)
        return {"ok": True}

    monkeypatch.setattr(publish, "submit", _submit)
    w = SimpleNamespace(p=promoted, org=promoted.org_a, proposals=proposals)
    detail = _run(w, lambda: lifecycle.create_app(
        lifecycle.AppCreate(name=f"Visibility {TAG}"), user=_user(ANA)))
    assert isinstance(detail, lifecycle.AppDetail), detail
    w.slug = detail.slug
    with promoted.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT id, workspace_path FROM apps WHERE slug = :s "
            "AND organization_id = CAST(:o AS uuid)"),
            {"s": w.slug, "o": w.org}).one()
    w.id, w.workspace = str(got[0]), Path(got[1])
    # Two private versions with no review needed. Version 2 is live.
    for notes in ("one", "two"):
        out = _run(w, lambda n=notes: publish.publish_app(
            w.slug, publish.PublishRequest(notes=n), user=_user(ANA)))
        assert out["review"] == "auto", out
    assert _app(w) == (2, "private")
    yield w
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE app_id = CAST(:id AS uuid)"),
                  {"id": w.id})
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _app(world: Any) -> tuple[int | None, str]:
    """The live version and the visibility, as the superuser reads them."""
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT live_version, visibility FROM apps WHERE id = CAST(:id AS uuid)"),
            {"id": world.id}).one()
    return row[0], row[1]


def _set_visibility(world: Any, visibility: str) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE apps SET visibility = :v WHERE id = CAST(:id AS uuid)"),
                  {"v": visibility, "id": world.id})


def _seed(world: Any, version: int, scopes: list[str], status: str) -> None:
    """Give one version these scopes and this review state, as the superuser."""
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE app_versions SET manifest = CAST(:m AS jsonb), "
            "scope_set_hash = :h, review_status = :s "
            "WHERE app_id = CAST(:id AS uuid) AND version = :v"),
            {"m": json.dumps({"scopes": scopes}), "h": scope_set_hash(scopes),
             "s": status, "id": world.id, "v": version})


def _audits(world: Any) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT COUNT(*) FROM app_audit WHERE app_id = CAST(:id AS uuid)"),
            {"id": world.id}).scalar_one())


def _rollback(world: Any, version: int) -> Any:
    return _caught(world, lambda: publish.rollback_app(
        world.slug, publish.RollbackRequest(version=version), user=_user(ANA)))


def _patch(world: Any, visibility: str) -> Any:
    return _caught(world, lambda: lifecycle.patch_app(
        world.slug, lifecycle.AppPatch(visibility=visibility), user=_user(ANA)))


def _assert_review_409(out: Any) -> None:
    assert isinstance(out, HTTPException), out
    assert out.status_code == 409
    assert "admin" in out.detail and "review" in out.detail, out.detail
    assert "write tools" in out.detail, out.detail


# ── The rollback ──────────────────────────────────────────────────────────


@_DB_GATE
def test_r8_a_rollback_refuses_an_unreviewed_write_version_on_an_org_app(world) -> None:
    _seed(world, 1, WRITE, "auto")
    _set_visibility(world, "org")
    before = _audits(world)

    out = _rollback(world, 1)

    _assert_review_409(out)
    assert _app(world) == (2, "org"), "the unreviewed write version went live"
    assert _audits(world) == before, "a refused rollback wrote an audit row"


@_DB_GATE
def test_r8_b_rollback_allows_a_write_version_whose_scope_set_was_approved(world) -> None:
    _seed(world, 1, WRITE, "auto")
    _seed(world, 2, WRITE, "approved")
    _set_visibility(world, "org")

    out = _rollback(world, 1)

    assert out == {"version": 1}, out
    assert _app(world) == (1, "org")


@_DB_GATE
def test_r8_c_rollback_on_a_private_app_is_unaffected(world) -> None:
    _seed(world, 1, WRITE, "auto")

    out = _rollback(world, 1)

    assert out == {"version": 1}, out
    assert _app(world) == (1, "private")


@_DB_GATE
def test_r8_c_rollback_to_a_read_only_version_on_an_org_app_is_unaffected(world) -> None:
    _seed(world, 1, READ, "auto")
    _set_visibility(world, "org")

    out = _rollback(world, 1)

    assert out == {"version": 1}, out


# ── PATCH /{slug} with a visibility ───────────────────────────────────────


@_DB_GATE
def test_r8_d_patch_to_org_refuses_an_unreviewed_write_live_version(world) -> None:
    _seed(world, 2, WRITE, "auto")
    before = _audits(world)

    out = _patch(world, "org")

    _assert_review_409(out)
    assert _app(world) == (2, "private"), "the visibility changed"
    assert _audits(world) == before, "a refused patch wrote an audit row"


@_DB_GATE
def test_r8_e_patch_to_org_allows_an_approved_scope_set(world) -> None:
    _seed(world, 1, WRITE, "approved")
    _seed(world, 2, WRITE, "auto")

    out = _patch(world, "org")

    assert isinstance(out, lifecycle.AppDetail), out
    assert _app(world) == (2, "org")


@_DB_GATE
def test_r8_e_an_approval_of_another_scope_set_does_not_count(world) -> None:
    _seed(world, 1, WRITE + READ, "approved")
    _seed(world, 2, WRITE, "auto")

    _assert_review_409(_patch(world, "org"))
    assert _app(world) == (2, "private")


@_DB_GATE
def test_r8_f_patch_to_org_with_no_write_scope_is_allowed(world) -> None:
    _seed(world, 2, READ, "auto")

    out = _patch(world, "org")

    assert isinstance(out, lifecycle.AppDetail), out
    assert _app(world) == (2, "org")


@_DB_GATE
@pytest.mark.parametrize("visibility", ["private", "people"])
def test_r8_g_patch_away_from_org_is_always_allowed(world, visibility: str) -> None:
    _seed(world, 2, WRITE, "auto")
    _set_visibility(world, "org")

    out = _patch(world, visibility)

    assert isinstance(out, lifecycle.AppDetail), out
    assert _app(world) == (2, visibility)


# ── The publish path keeps its own gate, on the shared query ───────────────


def _write_workspace_scopes(world: Any, scopes: list[str]) -> None:
    path = world.workspace / "app.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["scopes"] = scopes
    path.write_text(json.dumps(manifest), encoding="utf-8")


@_DB_GATE
def test_r8_h_publish_to_org_with_write_scopes_waits_for_review(world) -> None:
    _write_workspace_scopes(world, WRITE)

    out = _run(world, lambda: publish.publish_app(
        world.slug, publish.PublishRequest(visibility="org"), user=_user(ANA)))

    assert out["review"] == "pending", out
    assert len(world.proposals) == 1
    assert _app(world) == (2, "private")


@_DB_GATE
def test_r8_h_publish_to_org_reuses_an_approved_scope_set(world) -> None:
    _seed(world, 1, WRITE, "approved")
    _write_workspace_scopes(world, WRITE)

    out = _run(world, lambda: publish.publish_app(
        world.slug, publish.PublishRequest(visibility="org"), user=_user(ANA)))

    assert out["review"] == "auto", out
    assert world.proposals == []
    assert _app(world) == (3, "org")


# ── The row lock: a check and a parallel write cannot interleave ──────────
#
# Connection A (the superuser) locks the app row and changes it, as a
# parallel request would. The call under test starts in a thread. It must
# wait for A, then read A's state and act on it. Without the lock, the call
# checks the old state from ``load_app``, waits only at its UPDATE, and then
# writes over A's commit: the org gets an unreviewed write version.
#
# ⚠️ The ``is_alive`` assertion in :func:`_race` does NOT prove the lock. The
# call also waits with no lock, at its UPDATE. Only the assertions on the
# result and on the final state prove the serialization. The wait assertion
# only proves that A committed after the call started.

_HOLD_SECONDS = 1.5


def _race(world: Any, change: str, params: dict[str, Any], call: Any) -> Any:
    """Hold the row lock in A, run ``change`` in A, start ``call`` in a
    thread, check that it has not finished, then commit A. Answer what
    ``call`` gave. The caller's assertions on that answer and on the final
    state are the proof, not the wait."""
    out: dict[str, Any] = {}

    def worker() -> None:
        try:
            out["value"] = _caught(world, call)
        except BaseException as exc:  # the test asserts on it
            out["value"] = exc

    with world.p.admin_engine.connect() as a:
        tx = a.begin()
        a.execute(text("SELECT 1 FROM apps WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                  {"id": world.id})
        a.execute(text(change), {**params, "id": world.id})
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(_HOLD_SECONDS)
        assert thread.is_alive(), f"the call finished before A committed: {out}"
        tx.commit()
    thread.join(60)
    assert not thread.is_alive(), "the call never finished"
    return out["value"]


@_DB_GATE
def test_r8_i_patch_to_org_waits_for_a_rollback_and_sees_its_version(world) -> None:
    """A private rollback to an auto write version races PATCH org."""
    _seed(world, 1, WRITE, "auto")

    out = _race(
        world, "UPDATE apps SET live_version = 1 WHERE id = CAST(:id AS uuid)", {},
        lambda: lifecycle.patch_app(
            world.slug, lifecycle.AppPatch(visibility="org"), user=_user(ANA)),
    )

    _assert_review_409(out)
    assert _app(world) == (1, "private")


@_DB_GATE
def test_r8_i_rollback_waits_for_patch_org_and_sees_the_org(world) -> None:
    """PATCH org (its check passed on a read-only version) races a rollback
    to an auto write version on a private app."""
    _seed(world, 1, WRITE, "auto")

    out = _race(
        world, "UPDATE apps SET visibility = 'org' WHERE id = CAST(:id AS uuid)", {},
        lambda: publish.rollback_app(
            world.slug, publish.RollbackRequest(version=1), user=_user(ANA)),
    )

    _assert_review_409(out)
    assert _app(world) == (2, "org")


@_DB_GATE
def test_r8_i_publish_waits_for_patch_org_and_asks_for_review(world) -> None:
    """PATCH org races a publish (no visibility in the body) of a write draft."""
    _write_workspace_scopes(world, WRITE)

    out = _race(
        world, "UPDATE apps SET visibility = 'org' WHERE id = CAST(:id AS uuid)", {},
        lambda: publish.publish_app(
            world.slug, publish.PublishRequest(), user=_user(ANA)),
    )

    assert isinstance(out, dict) and out["review"] == "pending", out
    assert _app(world) == (2, "org")


# ── Pure ──────────────────────────────────────────────────────────────────


class _NoDB:
    async def execute(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("the check read the database")


@pytest.mark.parametrize(("version", "visibility"), [
    (1, "private"), (1, "people"), (1, None), (None, "org"),
])
def test_the_check_reads_nothing_off_the_org_or_with_no_live_version(
    version: int | None, visibility: str | None,
) -> None:
    asyncio.run(publish.require_org_review(_NoDB(), "app", version, visibility))


def test_publish_and_the_check_share_one_query() -> None:
    """One copy of the approved-scope-set query: a second copy drifts."""
    src = Path(publish.__file__).read_text(encoding="utf-8")
    assert src.count("review_status = 'approved'") == 1, src.count(
        "review_status = 'approved'")
    assert src.count("_scope_set_approved(") == 3  # the def and two callers


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed."""
    assert "tests/unit/test_app_visibility_review_r8.py" in r8_lists.must_run()
