"""R8 — Change CRM setup: ``PATCH /crm/settings {"mode": null}`` (WS-53 CRM-U5b).

Spec: ``crm_platform.md`` §3.4. Real Postgres, the real ``/crm`` router, and
the non-privileged role of the phase-4 rehearsal. The reset needs 0 rows in
the five record tables and no Zoho connection that is ``connected``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_access_r8 import (  # noqa: F401  (ns is a fixture)
    _SEEDED,
    _address,
    _admin,
    _assert_non_priv,
    _call,
    _counts,
    _member,
    _set_access,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Needs the real database. Without the gate the deploy run errors.
pytestmark = _DB_GATE

_HAS_RECORDS = {
    "code": "crm_has_records",
    "message": "This CRM has records, so its setup cannot change. "
               "Remove the records first.",
}
_CONNECTED = {
    "code": "crm_connected",
    "message": "Disconnect Zoho first, then change the setup.",
}


async def _native(ns: Any) -> None:  # noqa: F811
    r = await _call(ns, _admin(ns.org_a), "PATCH", "/crm/settings", {"mode": "native"})
    assert r.status_code == 200, r.text


async def _reset(ns: Any, user: Any = None) -> Any:  # noqa: F811
    return await _call(ns, user or _admin(ns.org_a), "PATCH", "/crm/settings",
                       {"mode": None})


def _mode(ns: Any) -> tuple[Any, ...] | None:  # noqa: F811
    with ns.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT mode, access, access_groups, connection_id FROM crm_settings "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a}).first()
    return tuple(row) if row else None


def _add_lead(ns: Any) -> None:  # noqa: F811
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id) "
            "SELECT CAST(:o AS uuid), 'Ann', id FROM crm_lead_statuses "
            "WHERE organization_id = CAST(:o AS uuid) LIMIT 1"), {"o": ns.org_a})


def _add_connection(ns: Any, status: str) -> str:  # noqa: F811
    with ns.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections "
            "(organization_id, provider, owning_app, status) "
            "VALUES (CAST(:o AS uuid), 'zoho', 'crm', :s) RETURNING id"),
            {"o": ns.org_a, "s": status}).scalar())


@pytest.fixture(autouse=True)
def _clean(ns):  # noqa: F811
    yield
    with ns.admin_engine.begin() as c:
        for table in ("crm_leads", "integration_connections"):
            c.execute(text(
                f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                {"o": ns.org_a})


async def test_reset_from_native_removes_the_17_rows(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set_access(ns, ns.org_a, "groups", ["group:sales"])
    await _native(ns)
    assert _counts(ns, ns.org_a) == (6, 5, 6)
    r = await _reset(ns)
    assert r.status_code == 200, r.text
    assert r.json()["mode"] is None
    assert _counts(ns, ns.org_a) == (0, 0, 0)
    assert _mode(ns) == (None, "groups", ["group:sales"], None)


async def test_native_again_reseeds_17_rows(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _native(ns)
    assert (await _reset(ns)).status_code == 200
    await _native(ns)
    assert _counts(ns, ns.org_a) == (6, 5, 6)


async def test_reset_with_a_lead_is_409_and_changes_nothing(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _native(ns)
    _add_lead(ns)
    r = await _reset(ns)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == _HAS_RECORDS
    assert _counts(ns, ns.org_a) == (6, 5, 6)
    assert _mode(ns)[0] == "native"


async def test_reset_with_a_connected_zoho_is_409(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _native(ns)
    _add_connection(ns, "connected")
    r = await _reset(ns)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == _CONNECTED
    assert _counts(ns, ns.org_a) == (6, 5, 6)
    assert _mode(ns)[0] == "native"


async def test_reset_from_mirror_with_a_disconnected_zoho_works(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    conn = _add_connection(ns, "disconnected")
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, connection_id) "
            "VALUES (CAST(:o AS uuid), 'mirror', CAST(:c AS uuid))"),
            {"o": ns.org_a, "c": conn})
    r = await _reset(ns)
    assert r.status_code == 200, r.text
    assert _mode(ns)[0] is None
    assert _mode(ns)[3] is None


async def test_a_member_who_is_not_admin_gets_403(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _native(ns)
    r = await _reset(ns, _member(_address("m"), ns.org_a))
    assert r.status_code == 403
    assert _mode(ns)[0] == "native"


async def test_a_stale_expected_mode_is_409(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """The settings read says 'native' while the stored mode is already NULL."""
    _assert_non_priv(app_engine)
    await _native(ns)
    from gateway.routes.crm import settings as mod

    real = mod.read_settings

    async def stale(db: Any, org: str) -> Any:
        row = await real(db, org)
        with ns.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE crm_settings SET mode = NULL "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
        return row

    monkeypatch.setattr(mod, "read_settings", stale)
    r = await _reset(ns)
    assert r.status_code == 409, r.text
    assert "changed while this request ran" in r.json()["detail"]
    assert _counts(ns, ns.org_a) == (6, 5, 6)
