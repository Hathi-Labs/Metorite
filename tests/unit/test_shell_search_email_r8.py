"""NS-4a, done-when 3 and R8, the email provider: a record the member cannot
read never returns through the command bar.

Runs the WHOLE shell route on a real Postgres, connected as the NON-privileged
``acb_app`` role with RLS forced (the `promoted` catalog of
`test_h3_rls_promotion_rehearsal.py`, as `test_email_keep_separate.py` uses
it). Two members of ONE organization each hold a mailbox:

* the member finds their own message by its words;
* the member never finds the other member's message, though it matches;
* the member never finds their own SEPARATE mailbox's message, which stays
  out of every search across mailboxes (EM-T8g-1, D-EM-30).

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import logging
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.shell import search as shell

# The catalog, the app role, the seed helpers and the binding, shared rather
# than copied. Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _account,
    _as_member,
    _mail,
    _purge,
    app_engine,
    promoted,
)


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset({"feature:email"})),
    )


@_DB_GATE
class TestTheEmailProviderOnARealDatabase:
    async def test_only_the_members_own_pooled_mail_comes_back(self, promoted, caplog, monkeypatch):  # noqa: F811
        # A cold scratch database is slower than the bar's budget, and a
        # provider cut off by the deadline also returns nothing. Room here,
        # and the log check below proves the provider finished.
        monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
        monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 60.0)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        me = f"shell-me-{tag}@t8g1.test"
        other = f"shell-other-{tag}@t8g1.test"
        word = f"falcon{tag}"
        mine = _account(p.admin_engine, org=p.org_b, owner=me, default=True)
        hidden = _account(p.admin_engine, org=p.org_b, owner=me, pooled=False)
        theirs = _account(p.admin_engine, org=p.org_b, owner=other, default=True)
        _mail(p.admin_engine, org=p.org_b, account_id=mine, sender="a@x.test",
              subject=f"Quarterly {word} report")
        _mail(p.admin_engine, org=p.org_b, account_id=hidden, sender="a@x.test",
              subject=f"Board {word} papers")
        _mail(p.admin_engine, org=p.org_b, account_id=theirs, sender="a@x.test",
              subject=f"Their {word} secret")
        try:
            with caplog.at_level(logging.INFO, logger=shell.__name__):
                async with _as_member(p, p.org_b):
                    answer = await shell.shell_search(q=word, scope="/email", user=_member(me))
            assert not [r for r in caplog.records if "provider" in r.getMessage()], (
                "the email provider failed or timed out, so an empty answer proves nothing"
            )
            titles = [i["title"] for g in answer["groups"] for i in g["items"]]
            assert titles == [f"Quarterly {word} report"]
        finally:
            _purge(p.admin_engine, f"shell-%-{tag}@t8g1.test")
