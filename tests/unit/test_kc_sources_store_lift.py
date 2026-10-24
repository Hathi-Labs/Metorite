"""WS-61 KC-1a: the CRM side effects leave the shared connection store.

Spec: ``project-docs/specs/knowledge_curation.md`` §22 (KC-1), item 1 of the
KC-1a verdict. ``connections/store.py`` is the one store of every external
connection. Two CRM-only side effects used to run for EVERY owning app:

* ``save_credential`` dropped or staled ``crm_source_profiles`` rows;
* ``disconnect`` stripped ``domain_name``, ``edition`` and ``tabs`` from
  ``provider_meta``.

After the lift, both run for ``owning_app == "crm"`` only. A Knowledge save
touches no CRM row, and a Knowledge disconnect keeps its ``provider_meta``.
The CRM behaviour does not change: the same SQL runs, in the same order.

The fake database records each statement and answers the two writes with a
row. No SQL here needs a real database: the R8 half of the CRM is
``tests/unit/test_crm_connection_r8.py``, which must pass unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.connections.store", reason="gateway not installed")

from gateway.connections import store
from gateway.crm_sources.base import SourceCredential

ORG = "11111111-1111-4111-8111-111111111111"
CONN = "22222222-2222-4222-8222-222222222222"
_NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


class _Keys:
    """A key store that seals nothing secret, so no master key is needed."""

    def encrypt(self, plain: str) -> str:
        return "sealed"

    def decrypt(self, blob: str) -> str:  # pragma: no cover - never opened here
        raise AssertionError("no blob is opened in this test")


class _RecordingDb:
    """Records each statement. The upsert and the disconnect answer a row.

    *owning_app* is the app of the row that the database holds, so the
    disconnect answers only the statement whose WHERE fits that row.
    """

    def __init__(self, owning_app: str) -> None:
        self.owning_app = owning_app
        self.sql: list[str] = []

    def _row(self, meta: dict[str, Any]) -> Any:
        return SimpleNamespace(
            id=CONN, organization_id=ORG, provider="p", owning_app=self.owning_app,
            scope_kind="member", member_email="m@kc.test", status="connected",
            provider_meta=meta, scopes=[], connected_by="m@kc.test",
            last_sync_at=None, last_error=None, created_at=_NOW, updated_at=_NOW,
        )

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.sql.append(sql)
        row = None
        if sql.startswith("INSERT INTO integration_connections"):
            row = self._row({})
        elif sql.startswith("UPDATE integration_connections SET status = 'disconnected'"):
            fits_crm = "owning_app = 'crm'" in sql
            fits_other = "owning_app <> 'crm'" in sql
            if (fits_crm and self.owning_app == "crm") or (
                    fits_other and self.owning_app != "crm"):
                row = self._row({"account_id": "acc-1"})
        return SimpleNamespace(first=lambda: row)


@pytest.fixture(autouse=True)
def _no_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_keys", lambda: _Keys())


def _credential() -> SourceCredential:
    return SourceCredential(
        access_token="a", refresh_token="r", expires_at=_NOW + timedelta(hours=1),
        provider_meta={"account_email": "m@kc.test"},
    )


def _profile_sql(db: _RecordingDb) -> list[str]:
    return [s for s in db.sql if "crm_source_profiles" in s]


def test_the_crm_constant_is_the_one_the_crm_uses() -> None:
    """The store names the CRM app once. The CRM routes and the sync use the
    same value, so a rename in one place cannot silently lift the CRM out."""
    from gateway.crm_sync import engine
    from gateway.routes.crm import connection

    assert store.CRM_OWNING_APP == connection.OWNING_APP == engine.OWNING_APP == "crm"


async def test_a_knowledge_save_touches_no_crm_row() -> None:
    db = _RecordingDb("knowledge")
    for keep in (False, True):
        await store.save_credential(
            db, ORG, "google_drive", "knowledge", _credential(),
            connected_by="m@kc.test", scope_kind="member", member_email="m@kc.test",
            keep_profile=keep,
        )
    assert _profile_sql(db) == [], db.sql
    assert len(db.sql) == 2, "one upsert for each save, and nothing else"


async def test_a_crm_save_still_drops_or_stales_the_profile() -> None:
    db = _RecordingDb("crm")
    await store.save_credential(db, ORG, "zoho", "crm", _credential(), connected_by="a@x")
    await store.save_credential(db, ORG, "zoho", "crm", _credential(), connected_by="a@x",
                                keep_profile=True)
    assert _profile_sql(db) == [store._DROP_PROFILES_SQL, store._STALE_PROFILES_SQL]


@pytest.mark.parametrize("app", ["integrations", "knowledge", "email"])
async def test_no_other_app_save_touches_a_crm_row(app: str) -> None:
    db = _RecordingDb(app)
    await store.save_credential(db, ORG, "p", app, _credential(), connected_by="a@x")
    assert _profile_sql(db) == []


async def test_a_knowledge_disconnect_strips_no_key() -> None:
    db = _RecordingDb("knowledge")
    row = await store.disconnect(db, ORG, CONN)
    assert row is not None and row.owning_app == "knowledge"
    keep = [s for s in db.sql if "owning_app <> 'crm'" in s]
    assert len(keep) == 1, db.sql
    assert "domain_name" not in keep[0] and "provider_meta =" not in keep[0], keep[0]
    assert "credentials_encrypted = NULL" in keep[0], "the blob still goes"
    assert _profile_sql(db) == []


async def test_a_crm_disconnect_still_strips_the_discovered_keys_in_one_statement() -> None:
    db = _RecordingDb("crm")
    row = await store.disconnect(db, ORG, CONN)
    assert row is not None
    assert db.sql == [store._DISCONNECT_SQL], "the CRM runs one statement, as before"
    for key in ("domain_name", "edition", "tabs"):
        assert f"- '{key}'" in store._DISCONNECT_SQL


async def test_a_missing_row_is_none() -> None:
    class _Empty(_RecordingDb):
        async def execute(self, statement: Any, params: Any = None) -> Any:
            self.sql.append(str(statement))
            return SimpleNamespace(first=lambda: None)

    empty = _Empty("knowledge")
    assert await store.disconnect(empty, ORG, str(uuid.uuid4())) is None
    assert len(empty.sql) == 2, "the CRM statement, then the other one"
