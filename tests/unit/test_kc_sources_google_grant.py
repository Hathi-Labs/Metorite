"""WS-61 KC-1a — Gmail and Drive share ONE Google grant, hermetic.

Spec: ``project-docs/specs/knowledge_curation.md`` §7.4 and §22 ("Disconnecting
Drive leaves Gmail connected, and a test proves it"), and
``project-docs/specs/knowledge_base.md`` §7.4.

Metorite owns one Google client. A Gmail consent and a Drive consent of one
Google account join one grant, and Google's revoke ends the whole grant. So:

* **Drive disconnect.** ``DELETE /knowledge/sources/{id}`` calls the revoke
  ONLY when both shared-grant checks answer false. ``true`` and ``NULL`` skip
  it. Either way the Drive blob goes, and the Gmail row and its blob stay.
* **Gmail disconnect, the reverse hole.** ``teardown.revoke_grant`` asks
  ``email_gmail_grant_shared``. Migration 300 replaces that function so a
  live Drive connection of the address counts. While one lives, the Gmail
  disconnect does not revoke.

The fake database is an in-memory model of ``email_accounts``,
``integration_connections``, ``kb_sources`` and ``kb_source_nodes``. It
dispatches on the exact SQL constants of the modules, and the real
``connections/store.py`` code runs against it. It answers the two functions
with a model of their SQL, or with a forced answer. The real SQL of both
functions, in both directions, is ``tests/unit/test_kc_sources_r8.py``.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.connections import store
from gateway.kb_sources.google_drive import GoogleDriveSource
from gateway.kb_sources.google_drive import auth as google_auth
from gateway.kb_sources.http import HttpAnswer
from gateway.routes import knowledge as kb_package
from gateway.routes.email import teardown
from gateway.routes.knowledge import core, sources

ORG = "11111111-1111-4111-8111-111111111111"
ME = "ana@kc.test"
ADDRESS = "ana@gmail.test"
_NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


# ── The model ───────────────────────────────────────────────────────────────


class _Keys:
    """A key store with no key: the blob is the JSON itself."""

    def encrypt(self, plain: str) -> str:
        return "sealed:" + plain

    def decrypt(self, blob: str) -> str:
        assert blob.startswith("sealed:")
        return blob[len("sealed:"):]


class Model:
    def __init__(self) -> None:
        self.gmail: list[dict[str, Any]] = []
        self.conns: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        #: A forced answer of a function, by its SQL, or absent for the model.
        self.forced: dict[str, Any] = {}

    def gmail_shared(self, email: str, exclude: str | None) -> bool:
        """The model of migration 300's ``email_gmail_grant_shared``."""
        e = email.strip().lower()
        return any(r["email"].lower() == e and r["id"] != exclude for r in self.gmail) or any(
            c["provider"] == "google_drive" and c["status"] != "disconnected"
            and c["meta"].get("account_email", "").lower() == e and c["id"] != exclude
            for c in self.conns)

    def drive_shared(self, email: str, exclude: str | None) -> bool:
        """The model of ``kb_google_drive_grant_shared``."""
        e = email.strip().lower()
        return any(
            c["provider"] == "google_drive" and c["status"] != "disconnected"
            and c["meta"].get("account_email", "").lower() == e and c["id"] != exclude
            for c in self.conns)


def _conn_row(c: dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=c["id"], organization_id=ORG, provider=c["provider"], owning_app=c["app"],
        scope_kind="member", member_email=c["member"], status=c["status"],
        provider_meta=dict(c["meta"]), scopes=[], connected_by=c["member"],
        last_sync_at=None, last_error=None, created_at=_NOW, updated_at=_NOW,
        credentials_encrypted=c["blob"],
    )


def _source_row(s: dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=s["id"], provider="google_drive", kind="member", owner_email=s["owner"],
        state=s["state"], account_email=s["account_email"], node_count=len(s.get("n", [])),
        discovered_at=None, last_error=None, created_at=_NOW, updated_at=_NOW,
        connection_id=s["connection_id"],
    )


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._scalar


class FakeDb:
    def __init__(self, model: Model) -> None:
        self.m = model

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql, p, m = str(statement), dict(params or {}), self.m
        if sql in (sources._LOCK_ONE_SQL, sources._ONE_SQL):
            rows = [s for s in m.sources if s["id"] == p["id"]
                    and (p["manager"] or s["owner"] == p["me"])]
            return _Result([_source_row(s) for s in rows])
        if sql == sources._DELETE_NODES_SQL:
            m.nodes = [n for n in m.nodes if n["source"] != p["source"]]
            return _Result()
        if sql == sources._DISCONNECTED_SQL:
            for s in m.sources:
                if s["id"] == p["id"]:
                    s["state"] = "disconnected"
            return _Result()
        if sql == teardown.GRANT_SHARED_SQL:
            if sql in m.forced:
                return _Result(scalar=m.forced[sql])
            return _Result(scalar=m.gmail_shared(p["email"], p["exclude"]))
        if sql == sources.DRIVE_GRANT_SHARED_SQL:
            if sql in m.forced:
                return _Result(scalar=m.forced[sql])
            return _Result(scalar=m.drive_shared(p["email"], p["exclude"]))
        if sql == store._GET_BY_ID_SQL:
            return _Result([_conn_row(c) for c in m.conns if c["id"] == p["id"]])
        if sql == store._BLOB_SQL:
            return _Result([_conn_row(c) for c in m.conns
                            if c["provider"] == p["provider"] and c["app"] == p["owning_app"]
                            and c["member"] == p["member"] and c["status"] != "disconnected"])
        if sql in (store._DISCONNECT_SQL, store._DISCONNECT_OTHER_SQL):
            crm = sql == store._DISCONNECT_SQL
            for c in m.conns:
                if c["id"] == p["id"] and (c["app"] == "crm") == crm:
                    c.update(status="disconnected", blob=None)
                    return _Result([_conn_row(c)])
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql[:120]}")


class FakeGoogle:
    """The faked Google client: records each POST, refuses any other host."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, params: Any = None, headers: Any = None) -> HttpAnswer:
        raise AssertionError(f"a disconnect reads nothing: {url}")

    async def post(self, url: str, *, form: Any, headers: Any = None) -> HttpAnswer:
        self.posts.append((url, dict(form)))
        return HttpAnswer(status_code=200, body=b"{}")


def _blob(refresh: str) -> str:
    return "sealed:" + json.dumps({
        "access_token": "drive-access", "refresh_token": refresh,
        "expires_at": (_NOW + timedelta(hours=1)).isoformat(),
        "scopes": [google_auth.DRIVE_READONLY], "provider_meta": {"account_email": ADDRESS},
    })


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Ana has a Gmail mailbox AND a Drive source of the same Google account."""
    settings = get_settings()
    for name, value in (("knowledge_enabled", True), ("knowledge_orgs", "*"),
                        ("knowledge_sources", "*"), ("knowledge_source_google", "*")):
        monkeypatch.setattr(settings, name, value, raising=False)
    model = Model()
    gmail_id, drive_id, source_id = (str(uuid.uuid4()) for _ in range(3))
    model.gmail.append({"id": gmail_id, "email": ADDRESS, "blob": "gmail-blob"})
    model.conns.append({"id": drive_id, "provider": "google_drive", "app": "knowledge",
                        "member": ME, "status": "connected",
                        "meta": {"account_email": ADDRESS, "account_id": "perm-1"},
                        "blob": _blob("drive-refresh")})
    model.sources.append({"id": source_id, "owner": ME, "state": "ready",
                          "account_email": ADDRESS, "connection_id": drive_id})
    model.nodes.append({"source": source_id, "ext_id": "f1"})

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        yield FakeDb(model)

    google = FakeGoogle()
    monkeypatch.setattr(core, "_tenant_session", _session)
    monkeypatch.setattr(store, "_keys", lambda: _Keys())
    monkeypatch.setattr(sources, "google_client", lambda: google_auth.OAuthClientConfig(
        client_id="cid", client_secret="secret", redirect_uri="https://app.test/cb"))
    monkeypatch.setattr(sources, "make_source",
                        lambda client: GoogleDriveSource(client, http=google))
    return {"model": model, "google": google, "source": source_id,
            "drive": drive_id, "gmail": gmail_id, "session": _session}


def _delete(world: dict[str, Any]) -> Any:
    user = UserContext(email=ME, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:knowledge"]), organization_id=ORG)
    app = FastAPI()
    app.include_router(kb_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app).delete(f"/knowledge/sources/{world['source']}")


def _revokes(world: dict[str, Any]) -> list[dict[str, str]]:
    return [form for url, form in world["google"].posts if url == google_auth.REVOKE_URL]


def _assert_drive_gone_gmail_kept(world: dict[str, Any]) -> None:
    m = world["model"]
    [drive] = [c for c in m.conns if c["id"] == world["drive"]]
    assert drive["status"] == "disconnected" and drive["blob"] is None, "the Drive blob stays"
    assert drive["meta"]["account_email"] == ADDRESS, "the lift keeps provider_meta"
    assert m.gmail == [{"id": world["gmail"], "email": ADDRESS, "blob": "gmail-blob"}], (
        "the Gmail row or its blob changed")
    assert m.nodes == [], "the nodes of the source stay"
    assert m.sources[0]["state"] == "disconnected"


# ── Drive disconnect ────────────────────────────────────────────────────────


def test_a_shared_gmail_grant_is_not_revoked(world: dict[str, Any]) -> None:
    """The Gmail mailbox of the address holds the grant: no revoke."""
    answer = _delete(world)
    assert answer.status_code == 200, answer.text
    assert answer.json()["state"] == "disconnected"
    assert _revokes(world) == [], "Drive revoked the grant of a live Gmail mailbox"
    _assert_drive_gone_gmail_kept(world)


@pytest.mark.parametrize("forced", [True, None])
@pytest.mark.parametrize("which", ["gmail", "drive"])
def test_true_and_null_both_skip_the_revoke(world: dict[str, Any], which: str,
                                            forced: bool | None) -> None:
    """Each check alone, at true or NULL, skips the revoke. The other one is free."""
    m = world["model"]
    m.gmail.clear()  # the model alone would now answer false
    sql = teardown.GRANT_SHARED_SQL if which == "gmail" else sources.DRIVE_GRANT_SHARED_SQL
    m.forced[sql] = forced
    assert _delete(world).status_code == 200
    assert _revokes(world) == [], f"{which} answered {forced!r} and the revoke still ran"
    [drive] = [c for c in m.conns if c["id"] == world["drive"]]
    assert drive["blob"] is None


def test_false_from_both_revokes_once(world: dict[str, Any]) -> None:
    """No Gmail row and no other Drive row: ONE revoke, with the refresh token."""
    world["model"].gmail.clear()
    assert _delete(world).status_code == 200
    assert _revokes(world) == [{"token": "drive-refresh"}]
    assert len(world["google"].posts) == 1, "one call to Google, the revoke"


def test_another_live_drive_connection_skips_the_revoke(world: dict[str, Any]) -> None:
    """A Drive source of the same account in another org holds the grant."""
    m = world["model"]
    m.gmail.clear()
    m.conns.append({"id": str(uuid.uuid4()), "provider": "google_drive", "app": "knowledge",
                    "member": "ana@other.test", "status": "connected",
                    "meta": {"account_email": ADDRESS.upper()}, "blob": _blob("other")})
    assert _delete(world).status_code == 200
    assert _revokes(world) == []


def test_a_second_delete_changes_nothing(world: dict[str, Any]) -> None:
    world["model"].gmail.clear()
    assert _delete(world).status_code == 200
    assert _delete(world).status_code == 200
    assert len(_revokes(world)) == 1, "a second disconnect revoked again"


# ── Gmail disconnect: the reverse hole ──────────────────────────────────────


def _gmail_plan(world: dict[str, Any]) -> teardown.TeardownPlan:
    return teardown.TeardownPlan(
        account_id=world["gmail"], org=ORG, owner=ME, provider="gmail",
        email_address=ADDRESS, credentials_encrypted="gmail-blob", subscription_id=None)


@pytest.fixture
def gmail_revokes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def _post(token: str) -> int:
        seen.append(token)
        return 200

    monkeypatch.setattr(teardown, "_post_revoke", _post)
    monkeypatch.setattr(teardown, "_decrypt_credentials",
                        lambda blob: ({"refresh_token": "gmail-refresh"}, None))
    return seen


async def test_a_gmail_disconnect_does_not_revoke_while_a_drive_row_lives(
    world: dict[str, Any], gmail_revokes: list[str],
) -> None:
    world["model"].gmail.clear()  # the mailbox row is the one being deleted
    assert await teardown.revoke_grant(_gmail_plan(world), world["session"]) is True
    assert gmail_revokes == [], "the Gmail disconnect killed the live Drive token"


async def test_a_gmail_disconnect_revokes_once_the_drive_row_is_gone(
    world: dict[str, Any], gmail_revokes: list[str],
) -> None:
    m = world["model"]
    m.gmail.clear()
    for c in m.conns:
        c["status"] = "disconnected"
    assert await teardown.revoke_grant(_gmail_plan(world), world["session"]) is True
    assert gmail_revokes == ["gmail-refresh"]


def test_the_replaced_function_counts_drive_rows() -> None:
    """The model above stands for this SQL. The R8 suite runs the SQL itself."""
    found = [p for p in sorted(_MIGRATIONS.glob("*.sql"))
             if "kb_google_drive_grant_shared" in p.read_text(encoding="utf-8")
             and "CREATE TABLE IF NOT EXISTS kb_sources" in p.read_text(encoding="utf-8")]
    assert len(found) == 1, [p.name for p in found]
    body = found[0].read_text(encoding="utf-8")
    replaced = body.split("CREATE OR REPLACE FUNCTION public.email_gmail_grant_shared")[1]
    replaced = replaced.split("$email_gmail_grant_shared$;")[0]
    assert "(p_email text, p_exclude uuid)" in replaced, "the signature must not change (R6)"
    assert "public.integration_connections" in replaced
    assert "ic.provider = 'google_drive'" in replaced
    assert "ic.status <> 'disconnected'" in replaced
