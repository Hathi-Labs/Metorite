"""The always-copy rules on a real database (R8, FORCE RLS, §21.16).

Runs as a non-privileged role on a phase-4-promoted catalog, in the world of
``test_email_contact_suggest_r8``:

* migration 299 adds ``email_assistant_settings.always_copy``, and the route
  writes it with ``always_copy.WRITE_SQL`` for a mailbox with no settings row
  (the insert path) and with one (the update path);
* ``put_always_copy`` saves an ``org`` and a ``sent`` colleague, and refuses a
  stranger and a person of another tenant (the classifier, under RLS);
* ``always_copy_people`` reads the rules back and classes each person again;
* another tenant reads no rule of this one.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_always_copy_r8.py -v -rs
"""
from __future__ import annotations

import types

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email.automation import always_copy as ac
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_contact_suggest_r8 import _assert_non_priv, _World

# ``promoted`` and ``app_engine`` are used by name for fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


async def _as(w: _World, org: str, fn):
    app_dsn = w.p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(app_dsn):
            return await fn()
    finally:
        release_tenant(token)


async def test_the_always_copy_rules_on_a_real_database(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    jain = f"jain-{w.tag}.test"
    sagar = f"sagar-{w.tag}@{jain}"
    stranger = f"spy-{w.tag}@evil-{w.tag}.test"
    user = types.SimpleNamespace(email=w.owner, organization_id=w.p.org_b)

    def rule(*cc: str, expected: str | None = None) -> ac.AlwaysCopyPut:
        return ac.AlwaysCopyPut(account_id=w.account,
                                rules=[ac.AlwaysCopyRule(match=jain, cc=list(cc))],
                                expected=expected or ac.version_of([]))

    try:
        w.seed()
        # A stranger, and a colleague of another tenant, are refused.
        for bad in (stranger, w.kiran_other_org):
            with pytest.raises(HTTPException) as err:
                await _as(w, w.p.org_b, lambda bad=bad: ac.put_always_copy(rule(w.kiran, bad), user))
            assert err.value.status_code == 422 and bad in err.value.detail
        # The insert path: the mailbox has no settings row yet.
        out = await _as(w, w.p.org_b, lambda: ac.put_always_copy(
            rule(w.kiran, w.kiran_client), user))
        assert out["rules"] == [{"match": jain, "cc": [w.kiran, w.kiran_client]}]
        # The update path: the row exists now.
        # A stale version answers 409 under the row lock (review P2).
        with pytest.raises(HTTPException) as err:
            await _as(w, w.p.org_b, lambda: ac.put_always_copy(rule(w.kiran), user))
        assert err.value.status_code == 409
        await _as(w, w.p.org_b, lambda: ac.put_always_copy(
            rule(w.kiran, expected=ac.version_of(out["rules"])), user))
        with w.p.admin_engine.begin() as c:
            row = c.execute(text(
                "SELECT always_copy, organization_id::text AS org FROM email_assistant_settings "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": w.account}).one()
        assert row.always_copy == [{"match": jain, "cc": [w.kiran]}]
        assert row.org == w.p.org_b

        # A card reads the rule back and classes each person again.
        async def people():
            async with ac._tenant_session() as db:
                return await ac.always_copy_people(db, w.owner, w.account, [sagar])

        assert await _as(w, w.p.org_b, people) == [(w.kiran, jain)]

        # Another tenant reads no rule of this one (FORCE RLS).
        async def other():
            async with ac._tenant_session() as db:
                return await ac.read_rules(db, w.account)

        assert await _as(w, w.p.org_a, other) == []
    finally:
        if w.account:
            with w.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_assistant_settings "
                               "WHERE account_id = CAST(:a AS uuid)"), {"a": w.account})
        w.purge()


async def test_two_first_writes_at_once_let_one_pass(
    promoted, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """H-411: two first writes of a mailbox with no settings row. Each one
    read the empty list. Only the first may pass, and the second answers 409.
    Mutation: remove the ``LOCK_SQL`` call, and both writes pass."""
    import asyncio

    from gateway.routes.email.automation import held_card

    _assert_non_priv(app_engine)
    w = _World(promoted)
    jain = f"jain-{w.tag}.test"
    user = types.SimpleNamespace(email=w.owner, organization_id=w.p.org_b)
    real = held_card.classify_addresses

    async def slow(*a, **k):
        # The window between the version check and the write. Without the
        # lock, the other writer reads the empty list in this window.
        await asyncio.sleep(0.5)
        return await real(*a, **k)

    monkeypatch.setattr(held_card, "classify_addresses", slow)

    def first(*cc: str) -> ac.AlwaysCopyPut:
        return ac.AlwaysCopyPut(account_id=w.account,
                                rules=[ac.AlwaysCopyRule(match=jain, cc=list(cc))],
                                expected=ac.version_of([]))

    async def both():
        return await asyncio.gather(
            ac.put_always_copy(first(w.kiran), user),
            ac.put_always_copy(first(w.kiran_client), user),
            return_exceptions=True)

    try:
        w.seed()
        with w.p.admin_engine.begin() as c:
            assert c.execute(text(
                "SELECT count(*) FROM email_assistant_settings "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": w.account}).scalar() == 0
        out = await _as(w, w.p.org_b, both)
        passed = [o for o in out if isinstance(o, dict)]
        refused = [o for o in out if isinstance(o, HTTPException)]
        assert len(passed) == 1 and len(refused) == 1, out
        assert refused[0].status_code == 409
        with w.p.admin_engine.begin() as c:
            stored = c.execute(text(
                "SELECT always_copy FROM email_assistant_settings "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": w.account}).scalar_one()
        assert stored == passed[0]["rules"]
    finally:
        if w.account:
            with w.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_assistant_settings "
                               "WHERE account_id = CAST(:a AS uuid)"), {"a": w.account})
        w.purge()
