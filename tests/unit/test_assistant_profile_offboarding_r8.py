"""R8 — the assistant profile when access ends, and its export (WS-63 PZ-9).

Spec: ``personalization.md`` §7 points 4 to 6, and §13.3 item 7.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), on a
database with the whole ladder and two organizations:

- P1 a purge deletes the member's ``user_settings`` row of THIS org. Their
  row in the other org stays, a colleague's row stays, and the answer
  counts ``member_settings`` = 1;
- P2 a member invited again after a purge starts with the empty profile,
  and the first write works;
- P3 a removal clears the profile and keeps the row, with its other
  columns, and a colleague keeps theirs;
- P4 a removal and then a return to ``active`` leave the profile empty;
- P5 the operator door's purge deletes the row too;
- P6 the purge of a member with no settings row counts 0 and does not fail;
- P7 the purge of an organization removes its rows by cascade, and no row
  of another org;
- E1 to E4 the export: JSON and text of a stored profile, the empty
  profile, only the bound org's row, the switch off, and no address.

The full bodies run (``purge_member_rows``, ``remove_member_rows``). Only
the hops that leave Postgres are stubbed: the audit entry (its own sync
engine), the CRM Redis keys, the identity shadow and the mailbox teardown.

Mutations, each red here (``personalization.md`` §9 PZ-9):
drop the ``user_settings`` entry of ``_PURGE_DELETES`` (P1, P5);
drop its org predicate (P1b); drop the clear of ``remove_member_rows``
(P3, P4); make the clear a DELETE (P3); drop the clear's org predicate
(P1b); drop the PATCH call (P8); clear on a suspension (P9); drop
``mark_changed`` (P8, P10).
"""
from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_PROFILE = {
    "v": 1, "learning": True,
    "address": {"name": "Asha", "formality": "casual"},
    "style": {"length": "short"},
    "facts": [{"id": "f1", "text": "I run on Mondays", "kind": "routine",
               "source": "chat", "at": "2026-10-23T11:00:00Z"}],
    "aliases": [{"id": "a1", "alias": "Mum", "email": "mum@example.com"}],
}
_SHELL = {"preset": "engineer", "pins": ["/projects"]}


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@pz9.test"


@pytest.fixture
def db(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog with an owner in org A, the off-Postgres hops
    stubbed, and this module's rows removed after."""
    from gateway.routes.admin import members
    from gateway.routes.crm import questions

    async def _no_keys(org: str, email: str) -> int:
        return 0

    monkeypatch.setattr(members, "record_admin_change", lambda *a, **k: None)
    monkeypatch.setattr(questions, "forget_member_keys", _no_keys)

    with promoted.admin_engine.begin() as c:
        role = c.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
            "VALUES (CAST(:o AS uuid), 'owner', 'Owner', true, 0) "
            "ON CONFLICT DO NOTHING RETURNING id"), {"o": promoted.org_a}).scalar()
        if role is None:
            role = c.execute(text(
                "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) "
                "AND slug = 'owner'"), {"o": promoted.org_a}).scalar()
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "SELECT id, :r, CAST(:o AS uuid) FROM app_user WHERE email = :e "
            "ON CONFLICT DO NOTHING"),
            {"r": role, "e": promoted.owner_email, "o": promoted.org_a})
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE lower(user_id) = :u"),
                      {"u": email})
            c.execute(text("DELETE FROM app_user WHERE lower(email) = :u"), {"u": email})


def _seed_member(ns, email: str, org: str) -> None:
    ns.made.append(email)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": email, "o": org})


def _seed_settings(ns, email: str, org: str, profile: Any = _PROFILE) -> None:
    if email not in ns.made:
        ns.made.append(email)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile, "
            "shell_prefs, number_system) VALUES (CAST(:o AS uuid), :e, "
            "CAST(:p AS jsonb), CAST(:s AS jsonb), 'international')"),
            {"o": org, "e": email, "p": json.dumps(profile) if profile else None,
             "s": json.dumps(_SHELL)})


def _rows(ns, email: str) -> dict[str, dict[str, Any]]:
    """Each settings row of *email*, by org, as the superuser sees it."""
    with ns.admin_engine.connect() as c:
        return {str(r.organization_id): dict(r._mapping) for r in c.execute(text(
            "SELECT organization_id, assistant_profile, shell_prefs, number_system "
            "FROM user_settings WHERE lower(user_id) = :u"), {"u": email})}


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _owner_dsn(ns) -> str:
    """The superuser, which RLS does not bind. Only the statement's own org
    predicate keeps another organization's row safe from it."""
    return ns.admin_engine.url.render_as_string(hide_password=False)


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _user(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:tasks"], roles=["member"]))


async def _purge(org: str, email: str) -> dict[str, int]:
    from gateway.db import tenant_session
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import purge_member_rows

    async with tenant_session(org) as session:
        member = await get_member(session, org, email)
        deleted, _kept, _plans = await purge_member_rows(
            session, org, member, actor_email="owner@h3rls.test")
    return deleted


async def _remove(org: str, email: str) -> None:
    from gateway.db import tenant_session
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import remove_member_rows

    async with tenant_session(org) as session:
        member = await get_member(session, org, email)
        await remove_member_rows(session, org, member)


# ── The purge ──────────────────────────────────────────────────────────────


async def test_p1_a_purge_deletes_this_orgs_row_only(db) -> None:
    a, b = _address("a"), _address("b")
    _seed_member(db, a, db.org_a)
    _seed_member(db, b, db.org_a)
    _seed_settings(db, a, db.org_a)
    _seed_settings(db, a, db.org_b)
    _seed_settings(db, b, db.org_a)
    async with tenant_engine_scope(_app_dsn(db)):
        deleted = await _purge(db.org_a, a)
    assert deleted["member_settings"] == 1
    assert set(_rows(db, a)) == {db.org_b}, "the purge reached another org's row"
    assert _rows(db, a)[db.org_b]["assistant_profile"] == _PROFILE
    assert _rows(db, b)[db.org_a]["assistant_profile"] == _PROFILE


async def test_p1b_the_org_predicate_holds_where_rls_does_not(db) -> None:
    """The purge and the removal each name the bound org beside RLS. A role
    that bypasses RLS must still touch only this org's row."""
    a, b = _address("a"), _address("b")
    _seed_member(db, a, db.org_a)
    _seed_member(db, b, db.org_a)
    _seed_settings(db, a, db.org_a)
    _seed_settings(db, a, db.org_b)
    _seed_settings(db, b, db.org_a)
    _seed_settings(db, b, db.org_b)
    async with tenant_engine_scope(_owner_dsn(db)):
        await _remove(db.org_a, b)
        await _purge(db.org_a, a)
    assert set(_rows(db, a)) == {db.org_b}
    assert _rows(db, a)[db.org_b]["assistant_profile"] == _PROFILE
    assert _rows(db, b)[db.org_a]["assistant_profile"] is None
    assert _rows(db, b)[db.org_b]["assistant_profile"] == _PROFILE


async def test_p2_a_member_invited_again_starts_empty(db) -> None:
    from acb_common.assistant_profile import empty_profile

    a = _address("a")
    _seed_member(db, a, db.org_a)
    _seed_settings(db, a, db.org_a)
    async with tenant_engine_scope(_app_dsn(db)):
        await _purge(db.org_a, a)
    _seed_member(db, a, db.org_a)  # the new invitation
    from gateway.routes.admin import assistant_profile as ap

    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            first = await ap.get_assistant_profile(user=_user(a))
            fact = await ap.add_fact(body={"text": "New start", "kind": "about"},
                                     user=_user(a))
            after = await ap.get_assistant_profile(user=_user(a))
    assert first == empty_profile()
    assert [f["text"] for f in after["facts"]] == ["New start"]
    assert fact["text"] == "New start"


async def test_p5_the_operator_door_purge_deletes_the_row(db, monkeypatch) -> None:
    import acb_auth.offboard as offboard
    from gateway.routes import operator
    from gateway.routes.email.teardown import TeardownCounts

    a = _address("a")
    _seed_member(db, a, db.org_a)
    _seed_settings(db, a, db.org_a)

    async def _org(slug: str) -> str:
        return db.org_a

    async def _none(**_kw: Any) -> None:
        return None

    async def _counts(*_a: Any, **_kw: Any) -> TeardownCounts:
        return TeardownCounts()

    monkeypatch.setattr(operator, "_require_operator_token", lambda _t: None)
    monkeypatch.setattr(offboard, "organization_id_for_slug", _org)
    monkeypatch.setattr(operator, "purge_identity_shadow", _none)
    monkeypatch.setattr(operator, "invalidate_for", lambda *_e: None)
    monkeypatch.setattr(operator, "finish_teardowns", _counts)
    async with tenant_engine_scope(_app_dsn(db)):
        out = await operator.remove_organization_member(
            slug="h3rls-a", email=a, confirm=a, mode="purge", x_operator_token="t")
    assert out["deleted"]["member_settings"] == 1
    assert _rows(db, a) == {}


async def test_p6_a_member_with_no_settings_row_counts_zero(db) -> None:
    a = _address("a")
    _seed_member(db, a, db.org_a)
    async with tenant_engine_scope(_app_dsn(db)):
        deleted = await _purge(db.org_a, a)
    assert deleted["member_settings"] == 0
    assert deleted["member_record"] == 1


async def test_p7_an_org_purge_cascades_its_rows_and_no_other(db) -> None:
    from acb_auth.offboard import purge_tenant_organization

    slug = f"pz9-{uuid.uuid4().hex[:8]}"
    with db.admin_engine.begin() as c:
        victim = str(c.execute(text(
            "INSERT INTO organization (slug, display_name) VALUES (:s, :s) RETURNING id"),
            {"s": slug}).scalar())
    a = _address("a")
    _seed_settings(db, a, victim)
    _seed_settings(db, a, db.org_b)
    try:
        async with tenant_engine_scope(_app_dsn(db)):
            receipt = await purge_tenant_organization(slug=slug)
        assert receipt["deleted"]["organization"] == 1
        assert set(_rows(db, a)) == {db.org_b}
    finally:
        with db.admin_engine.begin() as c:
            c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                      {"o": victim})


# ── The removal ────────────────────────────────────────────────────────────


async def test_p3_a_removal_clears_the_profile_and_keeps_the_row(db) -> None:
    a, b = _address("a"), _address("b")
    _seed_member(db, a, db.org_a)
    _seed_member(db, b, db.org_a)
    _seed_settings(db, a, db.org_a)
    _seed_settings(db, a, db.org_b)
    _seed_settings(db, b, db.org_a)
    async with tenant_engine_scope(_app_dsn(db)):
        await _remove(db.org_a, a)
    mine = _rows(db, a)
    assert set(mine) == {db.org_a, db.org_b}, "the removal deleted a row"
    assert mine[db.org_a]["assistant_profile"] is None
    assert mine[db.org_a]["shell_prefs"] == _SHELL
    assert mine[db.org_a]["number_system"] == "international"
    assert mine[db.org_b]["assistant_profile"] == _PROFILE
    assert _rows(db, b)[db.org_a]["assistant_profile"] == _PROFILE


async def test_p4_a_member_who_returns_finds_the_profile_empty(db) -> None:
    from acb_common.assistant_profile import empty_profile
    from gateway.routes.admin import assistant_profile as ap

    a = _address("a")
    _seed_member(db, a, db.org_a)
    _seed_settings(db, a, db.org_a)
    async with tenant_engine_scope(_app_dsn(db)):
        await _remove(db.org_a, a)
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE app_user SET status = 'active' WHERE email = :e"), {"e": a})
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            back = await ap.get_assistant_profile(user=_user(a))
    assert back == empty_profile()


async def _patch(db, email: str, status: str) -> None:
    """``PATCH /admin/members/{email}`` as the owner of org A."""
    from gateway.routes.admin import members

    owner = UserContext(email=db.owner_email, role=UserRole.EXECUTIVE,
                        access=build_access(["admin:members:manage"], roles=["owner"]))
    with _tenant(db.org_a):
        await members.update_member(email, patch=members.MemberPatch(status=status),
                                    admin=owner)


@pytest.fixture
def quiet_patch(monkeypatch):
    """The PATCH's hops after the commit, which leave Postgres."""
    from gateway.routes.admin import members

    async def _none(**_kw: Any) -> None:
        return None

    monkeypatch.setattr(members, "mirror_membership_status", _none)
    monkeypatch.setattr(members, "invalidate_for", lambda *_e: None)


async def test_p8_a_patch_to_removed_clears_the_profile(db, quiet_patch) -> None:
    """The lifecycle PATCH is a second door to ``removed`` (verify of PZ-9).
    It forgets the profile as the DELETE door does, and keeps the row, and
    it moves the change count so the cached block goes."""
    from acb_common.assistant_profile import generation

    a = _address("a")
    _seed_member(db, a, db.org_a)
    _seed_settings(db, a, db.org_a)
    _seed_settings(db, a, db.org_b)
    before = generation(a)
    async with tenant_engine_scope(_app_dsn(db)):
        await _patch(db, a, "removed")
    mine = _rows(db, a)
    assert mine[db.org_a]["assistant_profile"] is None
    assert mine[db.org_a]["shell_prefs"] == _SHELL
    assert mine[db.org_b]["assistant_profile"] == _PROFILE
    assert generation(a) == before + 1


async def test_p9_a_suspension_keeps_the_profile(db, quiet_patch) -> None:
    """A suspension can be reversed and does not end the membership, so the
    profile stays (§7 point 6)."""
    a = _address("a")
    _seed_member(db, a, db.org_a)
    _seed_settings(db, a, db.org_a)
    async with tenant_engine_scope(_app_dsn(db)):
        await _patch(db, a, "suspended")
    assert _rows(db, a)[db.org_a]["assistant_profile"] == _PROFILE


async def test_p10_the_removal_and_the_purge_move_the_change_count(db) -> None:
    from acb_common.assistant_profile import generation

    a, b = _address("a"), _address("b")
    _seed_member(db, a, db.org_a)
    _seed_member(db, b, db.org_a)
    ga, gb = generation(a), generation(b)
    async with tenant_engine_scope(_app_dsn(db)):
        await _remove(db.org_a, a)
        await _purge(db.org_a, b)
    assert (generation(a), generation(b)) == (ga + 1, gb + 1)


# ── The export ─────────────────────────────────────────────────────────────


async def _export(org: str, email: str, fmt: str = "json"):
    from gateway.routes.admin import assistant_profile as ap

    with _tenant(org):
        return await ap.export_assistant_profile(user=_user(email), format=fmt)


async def test_e1_the_json_and_the_text_of_a_stored_profile(db) -> None:
    from acb_common.assistant_profile import render_export, stored_profile

    a = _address("a")
    _seed_settings(db, a, db.org_a)
    async with tenant_engine_scope(_app_dsn(db)):
        as_json = await _export(db.org_a, a)
        as_text = await _export(db.org_a, a, "text")
    body = json.loads(as_json.body)
    assert set(body) == {"exported_at", "member", "profile", "text"}
    assert body["member"] == a
    assert body["profile"] == stored_profile(_PROFILE)
    assert body["profile"]["facts"][0]["source"] == "chat"
    assert body["profile"]["facts"][0]["at"] == "2026-10-23T11:00:00Z"
    assert body["text"] == render_export(_PROFILE)
    assert as_json.headers["cache-control"] == "no-store"
    assert 'filename="assistant-profile.json"' in as_json.headers["content-disposition"]
    assert as_text.media_type.startswith("text/plain")
    assert 'filename="assistant-profile.txt"' in as_text.headers["content-disposition"]
    assert as_text.body.decode() == render_export(_PROFILE) + "\n"
    assert "I run on Mondays (routine, learned in chat, 2026-10-23)" in as_text.body.decode()


async def test_e2_a_member_with_nothing_gets_the_empty_profile(db) -> None:
    from acb_common.assistant_profile import EXPORT_EMPTY, empty_profile

    a = _address("a")
    db.made.append(a)
    async with tenant_engine_scope(_app_dsn(db)):
        as_json = await _export(db.org_a, a)
        as_text = await _export(db.org_a, a, "text")
    body = json.loads(as_json.body)
    assert as_json.status_code == 200
    assert body["profile"] == empty_profile()
    assert body["text"] == EXPORT_EMPTY
    assert as_text.body.decode() == EXPORT_EMPTY + "\n"


async def test_e3_the_export_holds_only_the_bound_orgs_row(db) -> None:
    a = _address("a")
    _seed_settings(db, a, db.org_a, {"v": 1, "facts": [
        {"id": "f8", "text": "Org A only", "kind": "about"}]})
    _seed_settings(db, a, db.org_b, {"v": 1, "facts": [
        {"id": "f9", "text": "Org B only", "kind": "about"}]})
    async with tenant_engine_scope(_app_dsn(db)):
        out = (await _export(db.org_a, a)).body.decode()
    assert "Org A only" in out
    assert "Org B only" not in out


async def test_e4_learning_off_still_exports_and_no_address_answers_401(db) -> None:
    from fastapi import HTTPException

    a = _address("a")
    _seed_settings(db, a, db.org_a, {**_PROFILE, "learning": False})
    async with tenant_engine_scope(_app_dsn(db)):
        out = json.loads((await _export(db.org_a, a)).body)
        with pytest.raises(HTTPException) as refused:
            await _export(db.org_a, "")
    assert out["profile"]["learning"] is False
    assert [f["id"] for f in out["profile"]["facts"]] == ["f1"]
    assert "learning" not in out["text"].lower()
    assert refused.value.status_code == 401
