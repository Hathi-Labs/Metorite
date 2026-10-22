"""The files rule of a held send, on a real database (R8, FORCE RLS).

``email_app_master_plan.md`` §21.1 item 3: a file row resolves only from a
file of a mail of the member's own sending mailbox. This runs the real SQL of
``resolve_file_refs`` and ``mail_files`` as the non-privileged role on a
phase-4-promoted catalog.

R7, each with the mutation that turns it red:

* ``test_the_members_own_file_resolves`` — break the owner join.
* ``test_a_file_of_another_member_is_refused`` — drop ``acc.user_id = :uid``.
* ``test_a_file_of_another_tenant_is_refused`` — the role bypasses no RLS,
  so only the policy and the owner join stand between the two tenants.
* ``test_a_file_of_another_mailbox_of_the_member_is_refused`` — drop
  ``em.account_id = :aid``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_held_send_files_r8.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email.automation import held_card as hc
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_contact_suggest_r8 import _assert_non_priv

# ``promoted`` and ``app_engine`` are used by name for fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


class _World:
    """Owner A and member B of org B, and member C of org A. Each has one
    mailbox, one mail and one file. A has a second mailbox with a file."""

    def __init__(self, p) -> None:
        tag = uuid.uuid4().hex[:8]
        self.p = p
        self.a = f"a-{tag}@held-files.test"
        self.b = f"b-{tag}@held-files.test"
        self.c = f"c-{tag}@held-files.test"
        self.accounts: list[str] = []
        self.box: dict[str, str] = {}
        self.mail: dict[str, str] = {}
        self.file: dict[str, str] = {}

    def seed(self) -> None:
        p = self.p
        with p.admin_engine.begin() as c:
            for key, who, org in (("a", self.a, p.org_b), ("a2", self.a, p.org_b),
                                  ("b", self.b, p.org_b), ("c", self.c, p.org_a)):
                box = str(c.execute(text(
                    "INSERT INTO email_accounts (user_id, provider, email_address, "
                    "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', :m, "
                    "'x', CAST(:o AS uuid)) RETURNING id"),
                    {"u": who, "m": f"{key}-{uuid.uuid4().hex[:6]}@held-files.test",
                     "o": org}).scalar_one())
                self.accounts.append(box)
                self.box[key] = box
                mail = str(c.execute(text(
                    "INSERT INTO email_messages (account_id, provider_message_id, folder, "
                    "from_address, to_addresses, subject, received_at, organization_id) "
                    "VALUES (CAST(:a AS uuid), :pm, 'inbox', "
                    "'{\"email\": \"ravi@contoso.test\"}'::jsonb, '[]'::jsonb, :s, now(), "
                    "CAST(:o AS uuid)) RETURNING id"),
                    {"a": box, "pm": f"pm-{key}-{uuid.uuid4().hex[:6]}", "s": f"Mail {key}",
                     "o": org}).scalar_one())
                self.mail[key] = mail
                self.file[key] = str(c.execute(text(
                    "INSERT INTO email_attachments (message_id, filename, mime_type, "
                    "size_bytes, provider_attachment_id, organization_id) VALUES "
                    "(CAST(:m AS uuid), :n, 'application/pdf', 2048, :pa, CAST(:o AS uuid)) "
                    "RETURNING id"),
                    {"m": mail, "n": f"{key}.pdf", "pa": f"prov-{key}", "o": org}).scalar_one())

    def purge(self) -> None:
        with self.p.admin_engine.begin() as c:
            for box in self.accounts:
                c.execute(text(
                    "DELETE FROM email_attachments WHERE message_id IN ("
                    "SELECT id FROM email_messages WHERE account_id = CAST(:a AS uuid))"),
                    {"a": box})
                c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                          {"a": box})
                c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                          {"a": box})


@pytest.fixture()
def world(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    w = _World(promoted)
    w.seed()
    try:
        yield w
    finally:
        w.purge()


async def _resolve(w: _World, refs: list[str], *, box: str = "a", who: str | None = None):
    p = w.p
    token = bind_tenant(p.org_b)
    try:
        async with (tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
                    hc._tenant_session() as db):
            return await hc.resolve_file_refs(db, who or w.a, w.box[box], refs)
    finally:
        release_tenant(token)


async def _mail_files(w: _World, mail: str) -> list:
    p = w.p
    token = bind_tenant(p.org_b)
    try:
        async with (tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
                    hc._tenant_session() as db):
            return await hc.mail_files(db, w.a, w.box["a"], mail)
    finally:
        release_tenant(token)


async def test_the_members_own_file_resolves(world) -> None:
    got = await _resolve(world, [f"attachment:{world.file['a']}"])
    assert [(r.id, r.filename, r.subject) for r in got] == [(world.file["a"], "a.pdf", "Mail a")]
    files = await _mail_files(world, world.mail["a"])
    assert [r.id for r in files] == [world.file["a"]]


async def test_a_file_of_another_member_is_refused(world) -> None:
    with pytest.raises(HTTPException) as e:
        await _resolve(world, [f"attachment:{world.file['b']}"])
    assert e.value.status_code == 404
    assert await _mail_files(world, world.mail["b"]) == []


async def test_a_file_of_another_tenant_is_refused(world) -> None:
    with pytest.raises(HTTPException) as e:
        await _resolve(world, [f"attachment:{world.file['c']}"])
    assert e.value.status_code == 404
    # Not even its owner reaches it from this tenant.
    with pytest.raises(HTTPException):
        await _resolve(world, [f"attachment:{world.file['c']}"], box="a", who=world.c)


async def test_a_file_of_another_mailbox_of_the_member_is_refused(world) -> None:
    with pytest.raises(HTTPException) as e:
        await _resolve(world, [f"attachment:{world.file['a2']}"])
    assert e.value.status_code == 404
