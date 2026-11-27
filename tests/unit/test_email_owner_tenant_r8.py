"""WS-29 H-334, R8: the mailbox owner check names the bound tenant by
default, on a REAL Postgres.

``_assert_account_owner`` (``routes/email/core.py``) takes
``this_tenant=True`` by default. Its SQL then names ``app.tenant_id`` on
the mailbox row. Production's role ``acb_app`` does not bypass RLS, so RLS
already keeps a mailbox of another organization out. The predicate is
defence in depth (R5). This file runs as the SUPERUSER, which bypasses RLS,
so only the predicate refuses the other organization's mailbox.

The seed: ONE member owns a mailbox in org A and one in org B.

* (a) Bound to org B, the check with no keyword refuses org A's mailbox
  with 404.
* (b) The same call passes on org B's mailbox.
* (c) Non-vacuity: with ``this_tenant=False``, bound to org B, org A's
  mailbox is NOT refused. So the role bypasses RLS, and (a) proves the
  predicate.

R7: put back ``this_tenant: bool = False`` and (a) fails.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.email.core import _assert_account_owner

# The catalog and the binding, shared rather than copied. Fixtures are used
# by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _account,
    promoted,
)
from tests.unit.test_feed_task_kinds_r8 import _as_owner

pytestmark = _DB_GATE

OWNER = f"h334-owner-{uuid.uuid4().hex[:8]}@h334.test"


@pytest.fixture(scope="module")
def boxes(promoted):  # noqa: F811
    """OWNER owns one mailbox in org A and one in org B."""
    p = promoted
    return {
        "box_a": _account(p.admin_engine, org=p.org_a, owner=OWNER),
        "box_b": _account(p.admin_engine, org=p.org_b, owner=OWNER),
    }


class TestTheOwnerCheckNamesTheTenant:
    async def test_the_default_refuses_the_other_orgs_mailbox(
        self, promoted, boxes,  # noqa: F811
    ):
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            with pytest.raises(HTTPException) as err:
                await _assert_account_owner(db, boxes["box_a"], OWNER)
        assert err.value.status_code == 404

    async def test_the_default_passes_this_orgs_mailbox(
        self, promoted, boxes,  # noqa: F811
    ):
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            await _assert_account_owner(db, boxes["box_b"], OWNER)

    async def test_the_role_bypasses_rls(self, promoted, boxes):  # noqa: F811
        """Non-vacuity: with no tenant predicate, org A's mailbox passes
        while bound to org B. Only the predicate refuses it above."""
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            await _assert_account_owner(db, boxes["box_a"], OWNER, this_tenant=False)
