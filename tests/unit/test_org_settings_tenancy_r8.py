"""R8 — org settings are per organization, under phase-4 RLS.

The bug this fences, measured on production on 2026-10-08: two logo uploads
answered 500 with ``invalid input syntax for type uuid: ""``.
``acb_common/org_settings.py`` wrote through a raw psycopg connection that bound
no tenant, to a table that the generated phases had put under FORCED row-level
security, and whose primary key was still ``key`` alone. Every read came back
empty too, so no organization's logo or default look ever reached its members.

This runs both functions as the NON-privileged role of the phase-4 rehearsal
(``acb_app_h3rls``: no superuser, no BYPASSRLS), against a dedicated database
that has the full ladder (with migration 234) and all four generated phases,
and two organizations. A hermetic fake would agree with any SQL it is handed.
"""
from __future__ import annotations

import contextlib

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import org_settings
from acb_common.db import TenantUnbound, bind_tenant, release_tenant
from sqlalchemy import text

# The two-org phase-4 fixture and its DB gate. Used by name for injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    app_engine,
    promoted,  # noqa: F811
)


@pytest.fixture
def db(promoted, monkeypatch: pytest.MonkeyPatch):
    """Point the module's connection at the non-privileged role."""
    dsn = promoted.app_url.set(drivername="postgresql").render_as_string(hide_password=False)
    monkeypatch.setattr(org_settings, "_conninfo", lambda: dsn)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_settings"))
    return promoted


@contextlib.contextmanager
def tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def test_a_write_saves_and_reads_back_for_its_own_organization(db) -> None:
    with tenant(db.org_a):
        org_settings.save_org_setting("branding", {"logo": "A"}, updated_by="a@x.test")
        assert org_settings.load_org_setting("branding", default={}) == {"logo": "A"}


def test_another_organization_never_reads_it(db) -> None:
    with tenant(db.org_a):
        org_settings.save_org_setting("branding", {"logo": "A"})
    with tenant(db.org_b):
        assert org_settings.load_org_setting("branding", default="none") == "none"


def test_two_organizations_hold_the_same_key_apart(db) -> None:
    # The old primary key, `key` alone, made this a collision.
    with tenant(db.org_a):
        org_settings.save_org_setting("branding", {"logo": "A"})
    with tenant(db.org_b):
        org_settings.save_org_setting("branding", {"logo": "B"})
        assert org_settings.load_org_setting("branding") == {"logo": "B"}
    with tenant(db.org_a):
        assert org_settings.load_org_setting("branding") == {"logo": "A"}
    with db.admin_engine.connect() as c:
        rows = c.execute(text("SELECT count(*) FROM org_settings WHERE key = 'branding'")).scalar()
    assert rows == 2


def test_a_second_write_replaces_the_first(db) -> None:
    with tenant(db.org_a):
        org_settings.save_org_setting("appearance", {"mode": "dark"})
        org_settings.save_org_setting("appearance", {"mode": "light"}, updated_by="b@x.test")
        assert org_settings.load_org_setting("appearance") == {"mode": "light"}
    with db.admin_engine.connect() as c:
        n, by = c.execute(text(
            "SELECT count(*), max(updated_by) FROM org_settings WHERE key = 'appearance'"
        )).one()
    assert (n, by) == (1, "b@x.test")


def test_with_no_tenant_a_read_is_the_default_and_a_write_refuses(db) -> None:
    assert org_settings.load_org_setting("branding", default="dflt") == "dflt"
    with pytest.raises(TenantUnbound):
        org_settings.save_org_setting("branding", {"logo": "?"})


def _save_policy(engine, org: str, value: str) -> None:
    from gateway.routes.people.schedule import UPSERT_POLICY_SQL

    with engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        c.execute(text(UPSERT_POLICY_SQL), {"key": "work_schedule", "value": value, "by": "a@x.test"})


def test_the_work_schedule_upsert_is_per_organization(db, app_engine) -> None:
    # Measured 2026-10-09: after 234 the old `ON CONFLICT (key)` matched no
    # constraint, and every PUT /people/schedule answered 500.
    _save_policy(app_engine, db.org_a, '{"week": "A1"}')
    _save_policy(app_engine, db.org_a, '{"week": "A2"}')
    _save_policy(app_engine, db.org_b, '{"week": "B"}')
    with db.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, value->>'week' FROM org_settings "
            "WHERE key = 'work_schedule' ORDER BY 2"
        )).all()
    assert rows == [(db.org_a, "A2"), (db.org_b, "B")]


def test_the_primary_key_is_per_organization(db) -> None:
    with db.admin_engine.connect() as c:
        defs = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'org_settings'::regclass AND contype = 'p'"
        )).scalars().all()
    assert defs == ["PRIMARY KEY (organization_id, key)"]
