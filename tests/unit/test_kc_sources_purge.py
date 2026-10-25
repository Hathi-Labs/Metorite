"""WS-61 KC-1a review P1 — a member purge takes the leaver's Drive with it.

Spec: ``project-docs/specs/knowledge_curation.md`` §19: "When a member
leaves, their member sources disconnect". ``knowledge_base.md`` §7.4.

The case the review found. Member M holds Gmail and Drive on ONE Google
account. Before this fix an admin purged M, the email teardown asked
``email_gmail_grant_shared``, the Drive half of migration 300 saw M's live
Drive row and answered true, and nothing revoked. M's ``drive.readonly``
token stayed connected, and M's tree stayed readable.

After the fix:

* **In the purge transaction**, ``sources.purge_member_sources`` deletes every
  member-scope Knowledge connection of M through ``store.delete``. The blob
  goes, and the foreign key cascades the sources and the nodes.
* **After the commit**, the email teardown runs first. Its check no longer
  counts M's Drive row, so it revokes the grant when nothing else holds it.
* **Then** ``sources.finish_purge_revokes`` runs once for each Drive account.
  An address that a Gmail plan of the purge covers is left to the Gmail
  teardown, so one Google grant gets one revoke. Any other address gets the
  shared-grant check and, when free, one revoke.

These tests are hermetic. ``tests/unit/test_kc_sources_r8.py`` holds the same
purge on a real database.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from gateway.connections import store
from gateway.crm_sources.base import SourceCredential
from gateway.routes.knowledge import sources

ORG = "11111111-1111-4111-8111-111111111111"
M = "m@kc.test"
_NOW = datetime(2026, 10, 25, tzinfo=UTC)


def _conn(cid: str, *, app: str = "knowledge", member: str | None = M,
          provider: str = "google_drive", email: str = "m@gmail.test") -> store.Connection:
    return store.Connection(
        id=cid, organization_id=ORG, provider=provider, owning_app=app,
        scope_kind="member" if member else "org", member_email=member,
        status="connected", provider_meta={"account_email": email}, scopes=(),
        connected_by=member, last_sync_at=None, last_error=None,
        created_at=_NOW, updated_at=_NOW)


def _cred(token: str) -> SourceCredential:
    return SourceCredential(access_token="a", refresh_token=token,
                            expires_at=_NOW + timedelta(hours=1))


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    rows = [
        _conn("c-mine"),
        _conn("c-other-member", member="other@kc.test", email="o@gmail.test"),
        _conn("c-crm", app="crm", member=None, provider="zoho"),
        _conn("c-mcp", app="integrations", member=M, provider="mcp:x"),
    ]
    seen: dict[str, Any] = {"deleted": [], "loaded": [], "sql": []}

    async def _list_all(db: Any, org: str) -> list[store.Connection]:
        return list(rows)

    async def _load(db: Any, org: str, provider: str, owning_app: str, *,
                    scope_kind: str, member_email: str | None) -> SourceCredential:
        seen["loaded"].append((provider, owning_app, scope_kind, member_email))
        return _cred(f"rt-{member_email}")

    async def _delete(db: Any, org: str, provider: str, owning_app: str, *,
                      scope_kind: str, member_email: str | None) -> bool:
        seen["deleted"].append((provider, owning_app, scope_kind, member_email))
        return True

    monkeypatch.setattr(store, "list_all", _list_all)
    monkeypatch.setattr(store, "load_credential", _load)
    monkeypatch.setattr(store, "delete", _delete)

    class _Db:
        async def execute(self, statement: Any, params: Any = None) -> Any:
            seen["sql"].append(str(statement))
            return SimpleNamespace(first=lambda: None)

    seen["db"] = _Db()
    return seen


async def test_the_purge_deletes_only_the_leavers_knowledge_connections(fake_store) -> None:
    revokes = await sources.purge_member_sources(fake_store["db"], ORG, M.upper())
    assert fake_store["deleted"] == [("google_drive", "knowledge", "member", M)]
    assert [r.connection_id for r in revokes] == ["c-mine"]
    [r] = revokes
    assert r.account_email == "m@gmail.test" and r.credential.refresh_token == f"rt-{M}"
    assert "rt-" not in repr(r), "a pending revoke must not print its token"
    assert fake_store["sql"] == [], "the purge names no table itself: store.delete does"


async def test_a_leaver_with_no_source_deletes_nothing(fake_store) -> None:
    assert await sources.purge_member_sources(fake_store["db"], ORG, "nobody@kc.test") == []
    assert fake_store["deleted"] == []


async def test_the_gmail_teardown_owns_an_address_it_covers(monkeypatch) -> None:
    """One Google grant, one revoke: the Gmail teardown decides for its address."""
    calls: list[tuple[str | None, str]] = []

    async def _revoke(org, source, cred, email, connection_id):
        calls.append((email, connection_id))
        return "revoked"

    monkeypatch.setattr(sources, "_revoke_if_free", _revoke)
    monkeypatch.setattr(sources, "google_client", lambda: sources.OAuthClientConfig(
        client_id="c", client_secret="s", redirect_uri="https://app.test/cb"))
    pending = [
        sources.SourceRevoke("c-1", "Same@Gmail.test", _cred("rt-1")),
        sources.SourceRevoke("c-2", "drive-only@gmail.test", _cred("rt-2")),
    ]
    out = await sources.finish_purge_revokes(ORG, pending, gmail_addresses={"same@gmail.test"})
    assert calls == [("drive-only@gmail.test", "c-2")]
    assert out == {"c-1": "gmail_teardown", "c-2": "revoked"}


async def test_no_google_client_revokes_nothing_and_never_raises(monkeypatch) -> None:
    from fastapi import HTTPException

    def _none():
        raise HTTPException(status_code=503, detail="google_not_configured")

    monkeypatch.setattr(sources, "google_client", _none)
    out = await sources.finish_purge_revokes(
        ORG, [sources.SourceRevoke("c-1", "x@gmail.test", _cred("rt"))], gmail_addresses=set())
    assert out == {"c-1": "not_configured"}


# ── The order, in both purge doors ──────────────────────────────────────────


def _body(fn: Any) -> str:
    return inspect.getsource(fn)


def test_the_purge_body_disconnects_the_sources_in_its_transaction() -> None:
    from gateway.routes.admin import members

    body = _body(members.purge_member_rows)
    assert "knowledge_revokes" in inspect.signature(members.purge_member_rows).parameters
    assert "purge_knowledge_member(" in body
    knowledge = _body(members.purge_knowledge_member)
    assert "purge_member_sources(" in knowledge


@pytest.mark.parametrize("door", ["admin", "operator"])
def test_each_door_revokes_drive_after_the_email_teardown(door: str) -> None:
    """The email teardown must run its check after the Drive rows went (they
    went with the commit), and the Drive revokes run after it."""
    if door == "admin":
        from gateway.routes.admin.members import purge_member as fn
    else:
        from gateway.routes.operator import remove_organization_member as fn
    body = _body(fn)
    rows_at = body.index("purge_member_rows(")
    assert "knowledge_revokes=" in body[rows_at:rows_at + 300], "the door drops the revokes"
    teardown_at = body.rindex("finish_teardowns(")
    drive_at = body.index("finish_purge_revokes(")
    assert rows_at < teardown_at < drive_at, (
        "the Drive revoke must run after the email teardown, so one grant gets one revoke")
