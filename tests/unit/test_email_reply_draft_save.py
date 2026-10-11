"""WS-17 — the first save of a reply draft (live defect, 2026-10-11).

Spec: ``project-docs/specs/email_app_master_plan.md`` §17.11.

**The defect.** ``PUT /email/drafts`` found the mail that a reply answers
with ``(id = :id OR provider_message_id = :id)``. Postgres gives one bind
parameter one type. ``id = $1`` made it ``uuid``, and then
``provider_message_id = $1`` asked for ``text = uuid``, which does not exist.
So EVERY first save of a reply answered 500, whatever value the composer
sent. Box logs held 62 answers of 500 against 16 of 200 in three days. The
error was ``operator does not exist: text = uuid``.

**The fix.** The reply target matches by two typed parameters, as
``transport/send.py`` ``_reply_target`` already does. A value that parses as
a UUID matches ``id = CAST(:lid AS uuid)`` or ``provider_message_id = :rid``.
Any other value matches ``provider_message_id = :rid`` only.

**R8.** ``TestReplyDraftFirstSave`` runs the real route and the real SQL
against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, under FORCE RLS, as the role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS). Only the provider is fake. The
harness is the one of ``test_gmail_send_and_drafts.py``. CI without a ladder
database skips the class, and the source fence below still runs.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_reply_draft_save.py -v -rs
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email.automation import drafting
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _purge

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_EMAIL_ROUTES = (
    _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "email")
RAVI = "ravi@contoso-reply-save.test"


# ── the source fence (runs everywhere, no database) ─────────────────────────

#: One named parameter that meets ``id`` and ``provider_message_id``.
_ONE_PARAM_TWO_TYPES = re.compile(
    r"\bid\s*=\s*:(\w+)\b[^;]{0,400}?provider_message_id\s*=\s*:\1\b"
    r"|provider_message_id\s*=\s*:(\w+)\b[^;]{0,400}?\bid\s*=\s*:\2\b",
    re.DOTALL)


def test_no_email_sql_compares_one_param_to_a_uuid_and_a_text_column():
    """``id`` is a uuid and ``provider_message_id`` is text. One parameter
    cannot be both, so Postgres raises ``text = uuid`` at run time. A fake
    database never sees it, so this scan is the hermetic half of the fence."""
    hits = []
    for path in sorted(_EMAIL_ROUTES.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _ONE_PARAM_TWO_TYPES.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            hits.append(f"{path.relative_to(_ROOT)}:{line}")
    assert hits == [], (
        "one SQL parameter meets both id (uuid) and provider_message_id "
        f"(text): {hits}")


# ── R8: the real route on the real SQL ──────────────────────────────────────


def _dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


def _account(admin: Any, *, org: str, owner: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, sync_interval_secs, "
            "sync_status, organization_id) "
            "VALUES (:u, 'outlook', :m, 'x', true, 300, 'idle', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": owner, "o": org}).scalar_one())


def _inbox_mail(admin: Any, *, org: str, account_id: str, pmid: str) -> str:
    """One received mail of the mailbox, the target of a reply."""
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, subject, from_address, to_addresses, "
            "organization_id) VALUES (CAST(:a AS uuid), :p, 'conv-1', "
            "'inbox', 'Quote', CAST(:f AS jsonb), '[]'::jsonb, "
            "CAST(:o AS uuid)) RETURNING id::text"),
            {"a": account_id, "p": pmid, "o": org,
             "f": json.dumps({"name": "Ravi", "email": RAVI})}).scalar_one())


def _drafts(admin: Any, account_id: str) -> dict[str, str]:
    """Each draft row of the mailbox: its row id, then its provider id."""
    with admin.connect() as c:
        got = c.execute(text(
            "SELECT id::text AS id, provider_message_id FROM email_messages "
            "WHERE account_id = CAST(:a AS uuid) "
            "AND LOWER(folder) IN ('drafts', 'draft')"),
            {"a": account_id}).fetchall()
    return {r.id: r.provider_message_id for r in got}


class _Provider:
    """A provider that keeps its draft id at each update, as Outlook does."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[str] = []

    async def create_draft(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return f"AAMk-draft-{len(self.created)}"

    async def update_draft(self, provider_id: str, **_kw: Any) -> str:
        self.updated.append(provider_id)
        return provider_id


@pytest.fixture()
def mailbox(promoted, app_engine, monkeypatch):  # noqa: F811
    """One mailbox of one member in org B, and one inbox mail to answer."""
    _assert_non_priv(app_engine)
    p = promoted
    owner = f"rds-{uuid.uuid4().hex[:8]}@reply-save.test"
    aid = _account(p.admin_engine, org=p.org_b, owner=owner)
    provider = _Provider()
    m = SimpleNamespace(
        p=p, admin=p.admin_engine, org=p.org_b, aid=aid, owner=owner,
        provider=provider,
        me=UserContext(email=owner, role=UserRole.EMPLOYEE,
                       organization_id=p.org_b))

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=provider, owner_email=owner,
                              account_id=aid, authed=True)

    monkeypatch.setattr(drafting, "provider_session", _provider_session)
    try:
        yield m
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": aid})
        _purge(p.admin_engine, [aid])


async def _save(m: SimpleNamespace, **fields: Any) -> dict[str, Any]:
    """One PUT /email/drafts of the member: the real route, the real SQL."""
    req = drafting.DraftUpsertRequest(account_id=m.aid, **fields)
    token = bind_tenant(m.org)
    try:
        async with tenant_engine_scope(_dsn(m.p)):
            return await drafting.upsert_draft(req, user=m.me)
    finally:
        release_tenant(token)


@_DB_GATE
class TestReplyDraftFirstSave:
    """The first save of a reply INSERTs one row, and a later save updates it."""

    async def test_a_first_reply_save_by_the_local_id_inserts_one_draft(
            self, mailbox):
        """The inline reply sends the local id of the mail (``target.id``)."""
        m = mailbox
        mail = _inbox_mail(m.admin, org=m.org, account_id=m.aid, pmid="AAMk-in-1")

        saved = await _save(m, reply_to_message_id=mail, to=[RAVI], body="v1")

        assert m.provider.created[0]["reply_to_message_id"] == "AAMk-in-1"
        assert saved["thread_id"] == "conv-1"
        assert saved["subject"] == "Re: Quote"
        assert _drafts(m.admin, m.aid) == {saved["id"]: "AAMk-draft-1"}

    async def test_a_first_reply_save_by_the_provider_id_inserts_one_draft(
            self, mailbox):
        """The pop-out composer sends the provider id (``providerMessageId``),
        and the member typed no To, so the reply goes to the sender."""
        m = mailbox
        _inbox_mail(m.admin, org=m.org, account_id=m.aid, pmid="AAMk-in-2")

        saved = await _save(m, reply_to_message_id="AAMk-in-2", body="v1")

        assert m.provider.created[0]["reply_to_message_id"] == "AAMk-in-2"
        assert m.provider.created[0]["to"] == [RAVI]
        assert _drafts(m.admin, m.aid) == {saved["id"]: "AAMk-draft-1"}

    async def test_a_provider_id_shaped_as_a_uuid_still_matches(self, mailbox):
        """An IMAP or other provider id can parse as a UUID. The match then
        reads both arms, and the provider id arm finds the mail."""
        m = mailbox
        pmid = str(uuid.uuid4())
        _inbox_mail(m.admin, org=m.org, account_id=m.aid, pmid=pmid)

        await _save(m, reply_to_message_id=pmid, body="v1")

        assert m.provider.created[0]["reply_to_message_id"] == pmid

    async def test_a_save_by_the_draft_id_updates_and_does_not_duplicate(
            self, mailbox):
        """The second save sends the row id that the first save returned.
        The provider updates the same draft, and the mailbox keeps one row."""
        m = mailbox
        mail = _inbox_mail(m.admin, org=m.org, account_id=m.aid, pmid="AAMk-in-3")
        first = await _save(m, reply_to_message_id=mail, to=[RAVI], body="v1")

        second = await _save(m, draft_id=first["id"], to=[RAVI], body="v2")
        third = await _save(m, draft_id=first["id"], to=[RAVI], body="v3")

        assert second["id"] == third["id"] == first["id"]
        assert third["body_text"].startswith("v3")
        assert len(m.provider.created) == 1
        assert m.provider.updated == ["AAMk-draft-1", "AAMk-draft-1"]
        assert _drafts(m.admin, m.aid) == {first["id"]: "AAMk-draft-1"}

    async def test_a_new_draft_with_no_id_inserts_one_row(self, mailbox):
        """No draft id and no reply target: a standalone draft."""
        m = mailbox

        saved = await _save(m, to=[RAVI], subject="Hello", body="v1")

        assert _drafts(m.admin, m.aid) == {saved["id"]: "AAMk-draft-1"}

    async def test_an_unknown_reply_target_answers_404(self, mailbox):
        """A target that is in no row answers 404, for both shapes of id.
        The route makes no provider call and writes no row."""
        m = mailbox
        for target in (str(uuid.uuid4()), "AAMk-nowhere"):
            with pytest.raises(HTTPException) as exc:
                await _save(m, reply_to_message_id=target, body="v1")
            assert exc.value.status_code == 404
        assert m.provider.created == []
        assert _drafts(m.admin, m.aid) == {}
