"""WS-29 H-334b, R8: the three mailbox list reads name the bound tenant, on a
REAL Postgres.

H-334 (task 563) made ``_assert_account_owner`` name the bound tenant. Three
list reads are not calls of that check, and they named no tenant on the
private arm:

* ``transport/accounts.list_accounts``
* ``transport/accounts.feed_mailboxes``
* ``automation/identity.resolve_self_addresses``, through
  ``_MEMBER_MAILBOXES_SQL`` (``resolve_self`` reads the same SQL)

H-334c (task 1812) adds three more reads that named no tenant on ``a``:

* ``automation/identity.SELF_ADDRESSES_SQL`` (digest, Reply Zero, cleanup)
* ``automation/identity.SELF_MAILBOX_IDS_SQL`` (``proven_own_send``)
* ``transport/accounts._display_labels`` (create, set-default, update)

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
  The member clause of the self reads alone reads the org A mailbox too.
* (e) Each H-334c read, bound to org B, keeps the org A mailbox out.
* (f) Fail closed: with no tenant bound, each self read is empty.

H-334d (task 1990) moves the tenant predicate into the shared FROM clause
``_member_mailboxes_from``. Before, the pair set read that clause with a
column anchor and named no bound tenant. A second seed member owns TWO
paired mailboxes in org A and two in org B, so each mailbox has a pair:

* (g) ``PAIRED_MAILBOX_IDS_SQL``, ``ALSO_IN_SQL``, ``also_in_by_message``
  and ``draft_skip_in_pair``, bound to org B, give no pair of an org A
  mailbox. The org B pair still reads.
* (h) Fail closed: with no tenant bound, the pair reads give no row.
* (i) Non-vacuity: bound to org A, the org A pair reads. Bound to org B, the
  pair clause less its tenant predicate reads the org A pair too.

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
    _mail,
    promoted,
)
from tests.unit.test_feed_task_kinds_r8 import _as_owner

pytestmark = _DB_GATE

OWNER = f"h334b-owner-{uuid.uuid4().hex[:8]}@h334b.test"
#: The H-334d member: two paired mailboxes in each org.
PAIR_OWNER = f"h334d-owner-{uuid.uuid4().hex[:8]}@h334d.test"
#: The tenant predicate of the shared FROM clause (H-334c, H-334d).
_BOUND = f" AND a.organization_id = {mailbox_access._bound_org()}"


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

    async def test_self_addresses_sql_names_the_tenant(
        self, promoted, boxes,  # noqa: F811
    ):
        """H-334c: the subquery of digest, Reply Zero and cleanup."""
        from acb_common.db import tenant_session
        from gateway.routes.email.automation.identity import SELF_ADDRESSES_SQL

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            other = (await db.execute(text(SELF_ADDRESSES_SQL),
                                      {"aid": boxes["box_a"]})).fetchall()
            this = (await db.execute(text(SELF_ADDRESSES_SQL),
                                     {"aid": boxes["box_b"]})).fetchall()
        assert other == []
        assert [r.addr for r in this] == [boxes["addr_b"]]

    async def test_self_mailbox_ids_sql_names_the_tenant(
        self, promoted, boxes,  # noqa: F811
    ):
        """H-334c: the subquery of ``proven_own_send``."""
        from acb_common.db import tenant_session
        from gateway.routes.email.automation.identity import SELF_MAILBOX_IDS_SQL

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            other = (await db.execute(text(SELF_MAILBOX_IDS_SQL),
                                      {"aid": boxes["box_a"]})).fetchall()
            this = (await db.execute(text(SELF_MAILBOX_IDS_SQL),
                                     {"aid": boxes["box_b"]})).fetchall()
        assert other == []
        assert [str(r.id) for r in this] == [boxes["box_b"]]

    async def test_display_labels_names_the_tenant(
        self, promoted, boxes, shared,  # noqa: F811
    ):
        """H-334c: the label read of create, set-default and update."""
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            labels, defaults = await accounts._display_labels(db, OWNER)
        assert set(labels) == {boxes["box_b"]}
        assert set(defaults) == {boxes["box_b"]}

    def test_an_unbound_session_reads_no_self_row(
        self, promoted, boxes,  # noqa: F811
    ):
        """Fail closed: with no ``app.tenant_id``, each self read is empty
        for the role that bypasses RLS. It is never a set of another org."""
        from gateway.routes.email.automation.identity import (
            _MEMBER_MAILBOXES_SQL,
            SELF_ADDRESSES_SQL,
            SELF_MAILBOX_IDS_SQL,
        )

        with promoted.admin_engine.connect() as c:
            for sql in (SELF_ADDRESSES_SQL, SELF_MAILBOX_IDS_SQL,
                        _MEMBER_MAILBOXES_SQL):
                for box in (boxes["box_a"], boxes["box_b"]):
                    assert c.execute(text(sql), {"aid": box}).fetchall() == []

    async def test_the_role_bypasses_rls(self, promoted, boxes):  # noqa: F811
        """Non-vacuity: the member predicate alone, bound to org B, reads
        both mailboxes. Only the tenant predicate keeps org A out above."""
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            rows = (await db.execute(text(
                "SELECT id::text AS id FROM email_accounts WHERE "
                + mailbox_access.mailbox_predicate(uid="uid", level="read")),
                {"uid": OWNER})).fetchall()
            # The member clause of the self reads, with no tenant predicate,
            # reads the org A mailbox too (H-334c).
            from gateway.routes.email.automation.identity import (
                _member_mailboxes_from,
            )
            clause = _member_mailboxes_from(":aid")
            assert _BOUND in clause
            alien = (await db.execute(text(
                "SELECT o.id::text AS id " + clause.replace(_BOUND, "")),
                {"aid": boxes["box_a"]})).fetchall()
        assert {r.id for r in rows} == {boxes["box_a"], boxes["box_b"]}
        assert [r.id for r in alien] == [boxes["box_a"]]


# ── H-334d: the pair set names the bound tenant ────────────────────────────

_SENDER = "peer@h334d-outside.test"


def _pair_side(admin, org: str) -> dict[str, str]:
    """Two paired mailboxes of PAIR_OWNER in ``org``. The mail ``m`` in the
    first has a copy in the second, and the thread of the copy holds a newer
    Sent mail. So ``m`` is "also in" the second, and its draft is answered."""
    first = _account(admin, org=org, owner=PAIR_OWNER)
    second = _account(admin, org=org, owner=PAIR_OWNER)
    imid = f"<h334d-{uuid.uuid4().hex[:10]}@outside.test>"
    m = _mail(admin, org=org, account_id=first, sender=_SENDER,
              message_id=imid, minutes_ago=30)
    _mail(admin, org=org, account_id=second, sender=_SENDER,
          message_id=imid, thread="h334d-thread", minutes_ago=30)
    _mail(admin, org=org, account_id=second, sender=PAIR_OWNER,
          folder="sent", thread="h334d-thread", minutes_ago=5)
    return {"first": first, "second": second, "mail": m}


@pytest.fixture(scope="module")
def pairs(promoted):  # noqa: F811
    p = promoted
    return {"a": _pair_side(p.admin_engine, p.org_a),
            "b": _pair_side(p.admin_engine, p.org_b)}


def _pair_ids(rows) -> list[str]:
    return sorted(str(r.id) for r in rows)


class TestThePairSetNamesTheTenant:
    async def test_paired_mailbox_ids_sql_names_the_tenant(
        self, promoted, pairs,  # noqa: F811
    ):
        from acb_common.db import tenant_session
        from gateway.routes.email.automation.identity import PAIRED_MAILBOX_IDS_SQL

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            other = (await db.execute(text(PAIRED_MAILBOX_IDS_SQL),
                                      {"aid": pairs["a"]["first"]})).fetchall()
            this = (await db.execute(text(PAIRED_MAILBOX_IDS_SQL),
                                     {"aid": pairs["b"]["first"]})).fetchall()
        assert other == []
        assert _pair_ids(this) == [pairs["b"]["second"]]

    async def test_also_in_names_the_tenant(self, promoted, pairs):  # noqa: F811
        """``ALSO_IN_SQL`` reads the pair set with the column anchor
        ``m.account_id``: the read of a page of mail."""
        from acb_common.db import tenant_session
        from gateway.routes.email.automation.identity import also_in_by_message

        mails = [pairs["a"]["mail"], pairs["b"]["mail"]]
        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            got = await also_in_by_message(db, mails, PAIR_OWNER)
        assert got == {pairs["b"]["mail"]: [pairs["b"]["second"]]}

    async def test_the_draft_dedupe_names_the_tenant(
        self, promoted, pairs,  # noqa: F811
    ):
        from acb_common.db import tenant_session
        from gateway.routes.email.automation.identity import draft_skip_in_pair

        a, b = pairs["a"], pairs["b"]
        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            assert await draft_skip_in_pair(db, a["first"], a["mail"]) is None
        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            assert await draft_skip_in_pair(db, b["first"], b["mail"]) == "answered"

    async def test_each_org_reads_its_own_pair(self, promoted, pairs):  # noqa: F811
        """Non-vacuity: bound to org A, the org A pair reads. So the seed
        pairs, and the empty reads above prove the predicate."""
        from acb_common.db import tenant_session
        from gateway.routes.email.automation.identity import (
            PAIRED_MAILBOX_IDS_SQL,
            also_in_by_message,
            draft_skip_in_pair,
        )

        a = pairs["a"]
        async with _as_owner(promoted, promoted.org_a), tenant_session() as db:
            ids = (await db.execute(text(PAIRED_MAILBOX_IDS_SQL),
                                    {"aid": a["first"]})).fetchall()
            got = await also_in_by_message(db, [a["mail"]], PAIR_OWNER)
        assert _pair_ids(ids) == [a["second"]]
        assert got == {a["mail"]: [a["second"]]}
        async with _as_owner(promoted, promoted.org_a), tenant_session() as db:
            assert await draft_skip_in_pair(db, a["first"], a["mail"]) == "answered"

    async def test_the_pair_clause_less_the_tenant_reads_the_other_org(
        self, promoted, pairs,  # noqa: F811
    ):
        """Non-vacuity: bound to org B, the pair clause less its tenant
        predicate reads the org A pair. Only the predicate keeps it out."""
        from acb_common.db import tenant_session
        from gateway.routes.email.automation.identity import PAIRED_MAILBOX_IDS_SQL

        assert _BOUND in PAIRED_MAILBOX_IDS_SQL
        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            rows = (await db.execute(
                text(PAIRED_MAILBOX_IDS_SQL.replace(_BOUND, "")),
                {"aid": pairs["a"]["first"]})).fetchall()
        assert _pair_ids(rows) == [pairs["a"]["second"]]

    def test_an_unbound_session_reads_no_pair_row(
        self, promoted, pairs,  # noqa: F811
    ):
        """Fail closed: with no ``app.tenant_id``, the pair reads are empty
        for the role that bypasses RLS."""
        from gateway.routes.email.automation.identity import (
            _DRAFT_LOCK_SQL,
            ALSO_IN_SQL,
            NOT_A_COPY_FOLDERS,
            PAIRED_MAILBOX_IDS_SQL,
        )

        with promoted.admin_engine.connect() as c:
            for side in ("a", "b"):
                s = pairs[side]
                assert c.execute(text(PAIRED_MAILBOX_IDS_SQL),
                                 {"aid": s["first"]}).fetchall() == []
                assert c.execute(text(ALSO_IN_SQL), {
                    "ids": [s["mail"]], "uid": PAIR_OWNER,
                    "not_a_copy": list(NOT_A_COPY_FOLDERS),
                }).fetchall() == []
                assert c.execute(text(_DRAFT_LOCK_SQL), {
                    "aid": s["first"], "mid": s["mail"]}).fetchall() == []
