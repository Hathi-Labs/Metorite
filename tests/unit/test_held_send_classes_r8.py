"""The classifier of a held card, on a real database (R8, FORCE RLS).

``email_app_master_plan.md`` §21.4 item 2: the classifier reads the same data
as ``GET /email/contacts/suggest``. This file seeds the world of
``test_email_contact_suggest_r8.py`` and runs both, as a non-privileged role,
on a phase-4-promoted catalog.

R7, each with the mutation that turns it red:

* ``test_the_classes_agree_with_the_suggest_route`` — change an arm of
  ``CLASSIFY_SQL`` so that it reads other data than the suggest route.
* ``test_a_person_of_another_tenant_is_never_known`` — drop the tenant
  predicate of an org arm (the role bypasses no RLS, so the predicate and
  the policy both hold it).
* ``test_a_removed_member_is_not_org`` — drop the ``removed`` guard.
* ``test_the_known_domains_hold_the_org_and_the_sent_domains`` — the
  lookalike flag reads the domains of the member, never of another tenant.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_held_send_classes_r8.py -v -rs
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.automation import held_card as hc

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_contact_suggest_r8 import _assert_non_priv, _suggest, _World

# ``promoted`` and ``app_engine`` are used by name for fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


async def _classify(w: _World, addresses: list[str]) -> dict[str, str]:
    p = w.p
    app_dsn = p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(p.org_b)
    try:
        async with tenant_engine_scope(app_dsn), hc._tenant_session() as db:
            return await hc.classify_addresses(db, w.owner, addresses)
    finally:
        release_tenant(token)


async def _domains(w: _World) -> frozenset[str]:
    p = w.p
    app_dsn = p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(p.org_b)
    try:
        async with tenant_engine_scope(app_dsn), hc._tenant_session() as db:
            return await hc.known_domains(db, w.owner)
    finally:
        release_tenant(token)


async def test_the_classes_agree_with_the_suggest_route(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    try:
        w.seed()
        suggested = await _suggest(w, w.tag)
        got = await _classify(w, list(suggested))
        assert suggested, "the world seeded nobody"
        for addr, source in suggested.items():
            want = source if source in ("org", "sent") else "new"
            assert got[addr.lower()] == want, (addr, source, got[addr.lower()])
        # The three shapes, by name.
        assert got[w.kiran] == "org" and got[w.geo] == "org"
        assert got[w.kiran_client] == "sent"
        assert got[w.kiran_stranger] == "new"
        # The member's own mailbox is known.
        assert (await _classify(w, [w.owner]))[w.owner] == "org"
    finally:
        w.purge()


async def test_a_person_of_another_tenant_is_never_known(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    try:
        w.seed()
        got = await _classify(w, [w.kiran_other_org, "nobody@nowhere.test"])
        assert got == {w.kiran_other_org: "new", "nobody@nowhere.test": "new"}
    finally:
        w.purge()


async def test_a_removed_member_is_not_org(promoted, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    w = _World(promoted)
    try:
        w.seed()
        assert (await _classify(w, [w.removed]))[w.removed] == "new"
    finally:
        w.purge()


async def test_the_known_domains_hold_the_org_and_the_sent_domains(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    try:
        w.seed()
        got = await _domains(w)
        assert f"fracktal-{w.tag}.test" in got   # the organization
        assert f"client-{w.tag}.test" in got     # a person the member wrote to
        assert f"rival-{w.tag}.test" not in got  # another tenant
        assert f"stranger-{w.tag}.test" not in got  # a sender only
    finally:
        w.purge()
