"""CT-1 (task 3602, R8): the mail tier reads only the caller's own mail.

Runs on a real Postgres as the non-privileged role. Member A never sees the
recipients of member B, and org B never sees the recipients of org A.

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_contact_mail_people_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.transport import contacts as C
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_contact_suggest_r8 import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


class _Mail:
    def __init__(self, p) -> None:
        t = uuid.uuid4().hex[:8]
        self.p, self.t = p, t
        self.a = f"a-{t}@ct1-{t}.test"          # member A, org B
        self.b = f"b-{t}@ct1-{t}.test"          # member B, org B
        self.x = f"x-{t}@other-{t}.test"        # member X, org A
        self.accounts: dict[str, str] = {}
        self.vidya = f"bidyadhar-{t}@client.test"
        self.sanjeev = f"sg-{t}@client.test"
        self.rajnish = f"rn-{t}@client.test"

    def seed(self) -> None:
        p = self.p
        with p.admin_engine.begin() as c:
            for who, org, to, name in (
                (self.a, p.org_b, self.vidya, "Bidyadhar Nayak"),
                (self.b, p.org_b, self.sanjeev, "Sanjeev Goel"),
                (self.x, p.org_a, self.rajnish, "Rajnish Nimbark"),
            ):
                c.execute(text(
                    "INSERT INTO app_user (email, display_name, role, status, "
                    "organization_id) VALUES (:e, 'M', 'employee', 'active', "
                    "CAST(:o AS uuid)) ON CONFLICT DO NOTHING"), {"e": who, "o": org})
                acct = str(c.execute(text(
                    "INSERT INTO email_accounts (user_id, provider, email_address, "
                    "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', "
                    ":m, 'x', CAST(:o AS uuid)) RETURNING id"),
                    {"u": who, "m": who, "o": org}).scalar_one())
                self.accounts[who] = acct
                c.execute(text(
                    "INSERT INTO email_messages (account_id, provider_message_id, "
                    "folder, from_address, to_addresses, subject, received_at, "
                    "organization_id) VALUES (CAST(:a AS uuid), :pm, 'sent', "
                    "CAST(:f AS jsonb), CAST(:t AS jsonb), 's', :r, CAST(:o AS uuid))"),
                    {"a": acct, "pm": f"pm-{self.t}-{who}", "f": json.dumps({"email": who}),
                     "t": json.dumps([{"email": to, "name": name}]),
                     "r": datetime.now(UTC), "o": org})

    def purge(self) -> None:
        with self.p.admin_engine.begin() as c:
            for acct in self.accounts.values():
                c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                          {"a": acct})
                c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                          {"a": acct})
            c.execute(text("DELETE FROM people WHERE lower(email) = ANY(:e)"),
                      {"e": [self.a, self.b, self.x]})
            c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"),
                      {"e": [self.a, self.b, self.x]})


async def _suggest(w: _Mail, who: str, org: str, q: str):
    me = UserContext(email=who, role=UserRole.EMPLOYEE, organization_id=org)
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            return await C.suggest_contacts(q=q, account_id=None, limit=20, domain=None, user=me)
    finally:
        release_tenant(token)


async def _resolve(w: _Mail, who: str, org: str, name: str) -> dict:
    from gateway.routes.admin import assistant_profile_people as ap

    me = UserContext(email=who, role=UserRole.EMPLOYEE,
                     access=build_access(["feature:email", "feature:people"], roles=["member"]))
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            return await ap.resolve_person_for_assistant(name=name, user=me)
    finally:
        release_tenant(token)


async def test_a_near_name_finds_the_person_the_member_wrote_to(promoted, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    w = _Mail(promoted)
    try:
        w.seed()
        got = {s.email: s for s in await _suggest(w, w.a, promoted.org_b, "Vidyadhar Nayak")}
        assert got[w.vidya].match == "closest" and got[w.vidya].source == "sent"
        body = await _resolve(w, w.a, promoted.org_b, "Vidyadhar Nayak")
        assert body["mail"] is True and body["matches"][0]["email"] == w.vidya
        assert body["mail_searched"] is True
    finally:
        w.purge()


async def test_member_a_never_sees_member_b_and_org_b_never_sees_org_a(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _Mail(promoted)
    try:
        w.seed()
        assert w.sanjeev not in {s.email for s in await _suggest(
            w, w.a, promoted.org_b, "Sanjeev Goyal")}
        assert (await _resolve(w, w.a, promoted.org_b, "Sanjeev Goyal"))["matches"] == []
        assert w.rajnish not in {s.email for s in await _suggest(
            w, w.a, promoted.org_b, "Rajnish Nimbarkk")}
        assert (await _resolve(w, w.a, promoted.org_b, "Rajnish Nimbarkk"))["matches"] == []
        # The owner of that mail does find them.
        assert w.rajnish in {s.email for s in await _suggest(
            w, w.x, promoted.org_a, "Rajnish Nimbarkk")}
    finally:
        w.purge()
