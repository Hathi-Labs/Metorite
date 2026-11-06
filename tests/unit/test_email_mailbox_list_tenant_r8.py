"""WS-29 H-334b, R8: the three mailbox list reads name the bound tenant, on a
REAL Postgres.

H-334 (task 563) made ``_assert_account_owner`` name the bound tenant. Three
list reads are not calls of that check, and they named no tenant on the
private arm:

* ``transport/accounts.list_accounts``
* ``transport/accounts.feed_mailboxes``
* ``automation/identity.resolve_self_addresses``, through
  ``_MEMBER_MAILBOXES_SQL`` (``resolve_self`` reads the same SQL)

Each now names ``organization_id = app.tenant_id``. Production's role
``acb_app`` does not bypass RLS, so RLS already keeps the other organization
out. The predicate is defence in depth (R5). This file runs as the SUPERUSER,
which bypasses RLS, so only the predicate keeps the other organization out.

The seed: ONE member owns a mailbox in org A and one in org B. Each test
binds org B.

* (a) ``list_accounts`` lists the org B mailbox only.
* (b) ``feed_mailboxes`` gives the org B mailbox only.
* (c) ``resolve_self_addresses`` on the org A mailbox is empty, and on the
  org B mailbox it is the org B address only.
* (d) Non-vacuity: the member predicate alone, bound to org B, reads BOTH
  mailboxes. So the role bypasses RLS, and (a) to (c) prove the predicate.

(a) and (b) run with ``EMAIL_SHARED_MAILBOX`` off and on.

R7: drop the predicate from one read, and its test fails.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.email import mailbox_access
from gateway.routes.email.automation.identity import (
    resolve_self,
    resolve_self_addresses,
)
from gateway.routes.email.transport import accounts
from sqlalchemy import text

# The catalog and the binding, shared rather than copied. Fixtures are used
# by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _account,
    _address,
    promoted,
)
from tests.unit.test_feed_task_kinds_r8 import _as_owner

pytestmark = _DB_GATE

OWNER = f"h334b-owner-{uuid.uuid4().hex[:8]}@h334b.test"


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset({"feature:email"})),
    )


@pytest.fixture(scope="module")
def boxes(promoted):  # noqa: F811
    """OWNER owns one mailbox in org A and one in org B."""
    p = promoted
    box_a = _account(p.admin_engine, org=p.org_a, owner=OWNER)
    box_b = _account(p.admin_engine, org=p.org_b, owner=OWNER)
    return {
        "box_a": box_a, "box_b": box_b,
        "addr_a": _address(p.admin_engine, box_a).lower(),
        "addr_b": _address(p.admin_engine, box_b).lower(),
    }


@pytest.fixture(params=[False, True], ids=["private-only", "shared-on"])
def shared(request, monkeypatch):
    """Run with the shared mailbox flag off, then on."""
    monkeypatch.setattr(mailbox_access, "shared_enabled", lambda: request.param)
    return request.param


class TestTheListReadsNameTheTenant:
    async def test_list_accounts_lists_this_orgs_mailbox_only(
        self, promoted, boxes, shared,  # noqa: F811
    ):
        async with _as_owner(promoted, promoted.org_b):
            listed = await accounts.list_accounts(user=_member(OWNER))
        assert [a.id for a in listed] == [boxes["box_b"]]

    async def test_feed_mailboxes_gives_this_orgs_mailbox_only(
        self, promoted, boxes, shared,  # noqa: F811
    ):
        async with _as_owner(promoted, promoted.org_b):
            found = await accounts.feed_mailboxes(_member(OWNER))
        assert [b["id"] for b in found] == [boxes["box_b"]]

    async def test_the_self_addresses_name_the_tenant(
        self, promoted, boxes,  # noqa: F811
    ):
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            assert await resolve_self_addresses(db, boxes["box_a"]) == frozenset()
            assert (await resolve_self(db, boxes["box_a"])).address == ""
            assert await resolve_self_addresses(db, boxes["box_b"]) == frozenset(
                {boxes["addr_b"]})

    async def test_the_role_bypasses_rls(self, promoted, boxes):  # noqa: F811
        """Non-vacuity: the member predicate alone, bound to org B, reads
        both mailboxes. Only the tenant predicate keeps org A out above."""
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            rows = (await db.execute(text(
                "SELECT id::text AS id FROM email_accounts WHERE "
                + mailbox_access.mailbox_predicate(uid="uid", level="read")),
                {"uid": OWNER})).fetchall()
        assert {r.id for r in rows} == {boxes["box_a"], boxes["box_b"]}
