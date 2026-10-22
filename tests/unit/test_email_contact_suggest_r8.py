"""The chat finds a colleague by name, through the tenant seam (R8).

Owner, 2026-10-22: the member says "add Kiran and Geo from Fracktal to CC" in
the chat. ``find_contact`` reads ``GET /email/contacts/suggest``, and each hit
now names its ``source``: ``org`` for a member or a People row of the caller's
own organization, ``sent`` for a person the member wrote to, and ``seen`` for
a contact or a sender only. ``domain`` keeps the addresses at one domain.

R7 fences named here, each on a real database:

* ``email-suggest-org-members``: an active member and a People row of the
  bound organization come back as ``org``. A member of ANOTHER organization
  with the same name never comes back.
* ``email-suggest-source``: a person the member wrote to is ``sent``, and a
  sender only is ``seen``.
* ``email-suggest-domain``: ``domain`` keeps only the addresses at it.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_contact_suggest_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.transport import contacts as C
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


class _World:
    def __init__(self, p) -> None:
        tag = uuid.uuid4().hex[:8]
        self.p = p
        self.tag = tag
        self.owner = f"owner-{tag}@fracktal-{tag}.test"
        self.kiran = f"kiran-{tag}@fracktal-{tag}.test"
        self.geo = f"geo-{tag}@fracktal-{tag}.test"
        self.kiran_other_org = f"kiran-{tag}@rival-{tag}.test"
        self.kiran_client = f"kiran.k-{tag}@client-{tag}.test"
        self.kiran_stranger = f"kiran.s-{tag}@stranger-{tag}.test"
        self.emails = [self.owner, self.kiran, self.kiran_other_org]
        self.people = [self.geo]
        self.account = ""

    def seed(self) -> None:
        p = self.p
        with p.admin_engine.begin() as c:
            for email, org, name in (
                (self.owner, p.org_b, "Owner"),
                (self.kiran, p.org_b, f"Kiran {self.tag}"),
                # Same name, other customer: must never come back.
                (self.kiran_other_org, p.org_a, f"Kiran {self.tag}"),
            ):
                c.execute(text(
                    "INSERT INTO app_user (email, display_name, role, status, "
                    "organization_id) VALUES (:e, :n, 'employee', 'active', "
                    "CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
                    {"e": email, "n": name, "o": org})
            # A People row with no login.
            c.execute(text(
                "INSERT INTO people (organization_id, name, email) VALUES "
                "(CAST(:o AS uuid), :n, :e)"),
                {"o": p.org_b, "n": f"Geo {self.tag}", "e": self.geo})
            self.account = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id) "
                "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid)) RETURNING id"),
                {"u": self.owner, "m": self.owner, "o": p.org_b}).scalar_one())
            # The member wrote to the client once.
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "folder, from_address, to_addresses, subject, received_at, "
                "organization_id) VALUES (CAST(:a AS uuid), :pm, 'sent', "
                "CAST(:f AS jsonb), CAST(:t AS jsonb), 's', :r, CAST(:o AS uuid))"),
                {"a": self.account, "pm": f"pm-{self.tag}",
                 "f": json.dumps({"email": self.owner}),
                 "t": json.dumps([{"email": self.kiran_client,
                                   "name": f"Kiran K {self.tag}"}]),
                 "r": datetime.now(UTC), "o": p.org_b})
            # A stranger only mailed the member.
            c.execute(text(
                "INSERT INTO email_senders (account_id, email, name, "
                "organization_id) VALUES (CAST(:a AS uuid), :e, :n, "
                "CAST(:o AS uuid))"),
                {"a": self.account, "e": self.kiran_stranger,
                 "n": f"Kiran S {self.tag}", "o": p.org_b})

    def purge(self) -> None:
        with self.p.admin_engine.begin() as c:
            if self.account:
                for table in ("email_messages", "email_senders"):
                    c.execute(text(
                        f"DELETE FROM {table} WHERE account_id = CAST(:a AS uuid)"),
                        {"a": self.account})
                c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                          {"a": self.account})
            c.execute(text("DELETE FROM people WHERE lower(email) = ANY(:e)"),
                      {"e": [*self.people, *self.emails]})
            c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"),
                      {"e": self.emails})


async def _suggest(w: _World, q: str, domain: str | None = None) -> dict[str, str]:
    p = w.p
    me = UserContext(email=w.owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
    app_dsn = p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(p.org_b)
    try:
        async with tenant_engine_scope(app_dsn):
            out = await C.suggest_contacts(
                q=q, account_id=None, limit=20, domain=domain, user=me)
    finally:
        release_tenant(token)
    return {s.email: s.source for s in out}


async def test_each_hit_names_its_source_and_never_another_org(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    try:
        w.seed()
        got = await _suggest(w, f"kiran-{w.tag}")
        assert got.get(w.kiran) == "org"
        assert w.kiran_other_org not in got
        got = await _suggest(w, w.tag)
        assert got.get(w.kiran) == "org"
        assert got.get(w.geo) == "org"
        assert got.get(w.kiran_client) == "sent"
        assert got.get(w.kiran_stranger) == "seen"
        assert w.kiran_other_org not in got
        # The member is never offered to themself.
        assert w.owner not in got
    finally:
        w.purge()


async def test_the_domain_keeps_only_its_addresses(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    try:
        w.seed()
        got = await _suggest(w, w.tag, domain=f"fracktal-{w.tag}.test")
        assert got == {w.kiran: "org", w.geo: "org"}
        # The name part alone matches too ("from Fracktal").
        got = await _suggest(w, w.tag, domain=f"client-{w.tag}")
        assert got == {w.kiran_client: "sent"}
    finally:
        w.purge()
