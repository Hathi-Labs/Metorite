"""Task 3967 — a rollback cannot make an unreviewed version live (R8).

Code: ``gateway/routes/apps/publish.py`` (``rollback_app``).

**The fault.** An org publish that asks for a write scope waits for an admin
review (§4.8). The version row holds ``review_status = 'pending'`` until an
admin approves it. ``POST /api/apps/{slug}/rollback`` read only that the
version exists. So an author could make the pending version live, and skip
the review. A ``rejected`` version could go live the same way.

**Acceptance.** Each case starts with two versions, and version 2 is live.

a. Version 1 is ``pending``: the rollback is 409, the detail names the
   review and the status, and version 2 stays live.
b. Version 1 is ``rejected``: the same, with ``rejected``.
c. Version 1 is ``approved``: the rollback makes it live.
d. Version 1 is ``auto`` (no review needed): the rollback makes it live.
e. The guard is an allow-list. A state that it does not know is refused.

**The mutations (R7).** On 2026-11-25 the guard in ``rollback_app`` was
removed and this suite ran. a and b went red: the rollback made version 1
live. Then the guard was made a deny-list of pending and rejected again.
e went red.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_app_rollback_review_r8.py -v -rs

Without the database every R8 test here SKIPS, and the run reads green. A
skip is not a pass.
"""

from __future__ import annotations

import asyncio
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
from gateway.routes.apps import lifecycle, publish
from sqlalchemy import text

from tests.unit import _r8_lists as r8_lists
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@rollback3967.test"
_LIKE = f"%.{TAG}@rollback3967.test"


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


@pytest.fixture
def world(promoted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "custom_apps_root", str(tmp_path / "apps"))
    # The activity feed is Redis. This suite proves the live pointer.
    for mod in (lifecycle, publish):
        monkeypatch.setattr(mod, "publish_app_activity", lambda *a, **k: None)
    w = SimpleNamespace(p=promoted, org=promoted.org_a)
    detail = _run(w, lambda: lifecycle.create_app(
        lifecycle.AppCreate(name=f"Rollback {TAG}"), user=_user(ANA)))
    assert isinstance(detail, lifecycle.AppDetail), detail
    w.slug = detail.slug
    with promoted.admin_engine.connect() as c:
        w.id = str(c.execute(text(
            "SELECT id FROM apps WHERE slug = :s "
            "AND organization_id = CAST(:o AS uuid)"),
            {"s": w.slug, "o": w.org}).scalar_one())
    # Two versions with no review needed. Version 2 is live.
    for notes in ("one", "two"):
        out = _run(w, lambda n=notes: publish.publish_app(
            w.slug, publish.PublishRequest(notes=n), user=_user(ANA)))
        assert out["review"] == "auto", out
    assert _live(w) == 2
    yield w
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE app_id = CAST(:id AS uuid)"),
                  {"id": w.id})
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _live(world: Any) -> int | None:
    with world.p.admin_engine.connect() as c:
        return c.execute(text("SELECT live_version FROM apps WHERE id = CAST(:id AS uuid)"),
                         {"id": world.id}).scalar_one()


def _set_review(world: Any, version: int, status: str) -> None:
    """Set the review state of one version, as the superuser."""
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE app_versions SET review_status = :s "
            "WHERE app_id = CAST(:id AS uuid) AND version = :v"),
            {"s": status, "id": world.id, "v": version})


def _rollback(world: Any, version: int) -> Any:
    """Roll back as the author. Answers the result, or the HTTPException."""
    async def call() -> Any:
        try:
            return await publish.rollback_app(
                world.slug, publish.RollbackRequest(version=version),
                user=_user(ANA))
        except HTTPException as exc:
            return exc
    return _run(world, call)


def _rollback_audits(world: Any) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT COUNT(*) FROM app_audit "
            "WHERE app_id = CAST(:id AS uuid) AND kind = 'rollback'"),
            {"id": world.id}).scalar_one())


# ── a and b. An unreviewed version is refused ─────────────────────────────


@_DB_GATE
@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_r8_rollback_refuses_an_unreviewed_version(world, status: str) -> None:
    _set_review(world, 1, status)

    out = _rollback(world, 1)

    assert isinstance(out, HTTPException), out
    assert out.status_code == 409
    assert "review" in out.detail and status in out.detail, out.detail
    assert _live(world) == 2, "the unreviewed version went live"
    assert _rollback_audits(world) == 0, "a refused rollback wrote an audit row"


# ── e. A review state the route does not know is refused (fail closed) ────


def test_only_auto_and_approved_may_go_live() -> None:
    """The guard is an allow-list. A state that a later migration adds, or a
    null, is refused until someone adds it to the list on purpose."""
    assert publish._may_go_live("auto")
    assert publish._may_go_live("approved")
    for status in ("pending", "rejected", "withdrawn", "", None, "AUTO"):
        assert not publish._may_go_live(status), status


@_DB_GATE
def test_r8_rollback_refuses_an_unknown_review_status(world) -> None:
    """The CHECK of ``app_versions`` allows four states only. This test drops
    it in the dedicated scratch database of this module, seeds an unknown
    state, and puts the CHECK back."""
    with world.p.admin_engine.begin() as c:
        names = [r[0] for r in c.execute(text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'app_versions'::regclass AND contype = 'c' "
            "AND pg_get_constraintdef(oid) LIKE '%review_status%'"))]
        assert names, "the CHECK on review_status moved: update this test"
        for name in names:
            c.execute(text(f'ALTER TABLE app_versions DROP CONSTRAINT "{name}"'))
    try:
        _set_review(world, 1, "withdrawn")

        out = _rollback(world, 1)

        assert isinstance(out, HTTPException), out
        assert out.status_code == 409
        assert "review" in out.detail and "withdrawn" in out.detail, out.detail
        assert _live(world) == 2, "an unknown review state went live"
        assert _rollback_audits(world) == 0
    finally:
        _set_review(world, 1, "auto")
        with world.p.admin_engine.begin() as c:
            c.execute(text(
                "ALTER TABLE app_versions ADD CONSTRAINT app_versions_review_status_check "
                "CHECK (review_status IN ('auto', 'pending', 'approved', 'rejected'))"))


# ── c and d. A reviewed version, or one with no review, goes live ─────────


@_DB_GATE
@pytest.mark.parametrize("status", ["approved", "auto"])
def test_r8_rollback_allows_a_reviewed_or_unreviewable_version(
    world, status: str,
) -> None:
    _set_review(world, 1, status)

    out = _rollback(world, 1)

    assert out == {"version": 1}, out
    assert _live(world) == 1
    assert _rollback_audits(world) == 1


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed."""
    assert "tests/unit/test_app_rollback_review_r8.py" in r8_lists.must_run()
