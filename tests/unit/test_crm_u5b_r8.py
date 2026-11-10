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
        for table in ("crm_leads", "crm_module_records", "crm_field_rules",
                      "crm_mapping_plans", "integration_connections"):
            c.execute(text(
                f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                {"o": ns.org_a})


def _add_module_record(ns: Any) -> None:  # noqa: F811
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name) VALUES (CAST(:o AS uuid), 'Products', 'zoho', 'p1', 'W')"),
            {"o": ns.org_a})


def _module_records(ns: Any) -> int:  # noqa: F811
    with ns.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM crm_module_records "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a}).scalar())


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
    """Regression only: the router dependency already gave 403 before U5b."""
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


def _seed_sync_state(ns: Any) -> None:  # noqa: F811
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_cursors (organization_id, module, "
            "last_pulled_at, initial_done_at) VALUES (CAST(:o AS uuid), "
            "'Leads', now(), now())"), {"o": ns.org_a})
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, source) "
            "VALUES (CAST(:o AS uuid), 'lead', 'zk', 'ZK', 'zoho'), "
            "(CAST(:o AS uuid), 'lead', 'mk', 'MK', 'manual')"), {"o": ns.org_a})
        c.execute(text(
            "INSERT INTO crm_sync_runs (organization_id, kind, status) "
            "VALUES (CAST(:o AS uuid), 'import', 'ok')"), {"o": ns.org_a})


def _sync_counts(ns: Any) -> tuple[int, ...]:  # noqa: F811
    q = "SELECT count(*) FROM {t} WHERE organization_id = CAST(:o AS uuid){w}"
    with ns.admin_engine.connect() as c:
        return tuple(
            int(c.execute(text(q.format(t=t, w=w)), {"o": ns.org_a}).scalar())
            for t, w in (
                ("crm_sync_cursors", ""),
                ("crm_sync_runs", ""),
                ("crm_field_defs", " AND source = 'zoho'"),
                ("crm_field_defs", " AND source = 'manual'"),
            )
        )


@pytest.fixture(autouse=True)
def _clean_sync(ns):  # noqa: F811
    yield
    with ns.admin_engine.begin() as c:
        for t in ("crm_sync_cursors", "crm_sync_runs", "crm_field_defs"):
            c.execute(text(f"DELETE FROM {t} WHERE organization_id = CAST(:o AS uuid)"),
                      {"o": ns.org_a})


def _field_rules(ns: Any, table: str = "crm_field_rules") -> int:  # noqa: F811
    with ns.admin_engine.connect() as c:
        return int(c.execute(text(
            f"SELECT count(*) FROM {table} "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a}).scalar())


async def test_reset_deletes_the_field_rules(ns, app_engine) -> None:  # noqa: F811
    """CRM-Q7a review P2: a rule names a field of the old Zoho account."""
    _assert_non_priv(app_engine)
    await _native(ns)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'lead', 'Sector', "
            "'select', 1, 'industry', 'approved', 'admin')"), {"o": ns.org_a})
        # CRM-Q18-1: a version of the plan names the same rule.
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, "
            "rules, created_by) VALUES (CAST(:o AS uuid), 1, 'approved', "
            "'[]'::jsonb, 'admin')"), {"o": ns.org_a})
    assert _field_rules(ns) == 1
    assert _field_rules(ns, "crm_mapping_plans") == 1
    assert (await _reset(ns)).status_code == 200
    assert _field_rules(ns) == 0
    assert _field_rules(ns, "crm_mapping_plans") == 0


async def test_reset_clears_the_zoho_sync_state(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _native(ns)
    _seed_sync_state(ns)
    assert _sync_counts(ns) == (1, 1, 1, 1)
    assert (await _reset(ns)).status_code == 200
    # No cursor, so the engine takes the import path next time. The Zoho field
    # def goes. A manual one stays.
    assert _sync_counts(ns) == (0, 0, 0, 1)


async def test_a_record_written_after_the_count_is_409_not_500(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    await _native(ns)
    from gateway.routes.crm import settings as mod

    real = mod._check_reset_allowed

    async def racing(db: Any, org: str) -> None:
        await real(db, org)  # passes: 0 records
        _add_lead(ns)  # a record lands before the delete

    monkeypatch.setattr(mod, "_check_reset_allowed", racing)
    r = await _reset(ns)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == _HAS_RECORDS
    assert _counts(ns, ns.org_a) == (6, 5, 6)
    assert _mode(ns)[0] == "native"


async def test_reset_drops_the_credential_of_a_needs_reconnect_row(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from gateway.routes.crm import connection as cmod

    async def no_revoke(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(cmod, "_best_effort_revoke", no_revoke)
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "acb_master_key", "r8-master-key", raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    conn = _add_connection(ns, "needs_reconnect")
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET credentials_encrypted = 'blob' "
            "WHERE id = CAST(:i AS uuid)"), {"i": conn})
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, connection_id) "
            "VALUES (CAST(:o AS uuid), 'mirror', CAST(:c AS uuid))"),
            {"o": ns.org_a, "c": conn})
    assert (await _reset(ns)).status_code == 200
    with ns.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT status, credentials_encrypted FROM integration_connections "
            "WHERE id = CAST(:i AS uuid)"), {"i": conn}).first()
    assert tuple(row) == ("disconnected", None)


# ── CRM-Q6a: the generic record store ───────────────────────────────────────


async def test_reset_with_a_generic_module_record_is_409(ns, app_engine) -> None:  # noqa: F811
    """A record of a generic module (``crm_module_records``) is a CRM record."""
    _assert_non_priv(app_engine)
    await _native(ns)
    _add_module_record(ns)
    r = await _reset(ns)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == _HAS_RECORDS
    assert _module_records(ns) == 1
    assert _mode(ns)[0] == "native"


async def test_reset_deletes_a_module_record_that_lands_after_the_count(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """The reset deletes the cursors of the modules. A module record that it
    left would then belong to no cursor, so the reset deletes it too."""
    _assert_non_priv(app_engine)
    await _native(ns)
    from gateway.routes.crm import settings as mod

    real = mod._check_reset_allowed

    async def racing(db: Any, org: str) -> None:
        await real(db, org)
        _add_module_record(ns)

    monkeypatch.setattr(mod, "_check_reset_allowed", racing)
    r = await _reset(ns)
    assert r.status_code == 200, r.text
    assert _module_records(ns) == 0
