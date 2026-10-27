"""R8 — ``resolve_person``, a name to one colleague in ONE org (WS-63 PZ-5).

The route ``GET /auth/me/assistant-profile/resolve-person``
(``routes/admin/assistant_profile_people.py``) runs here against a REAL
Postgres, as the NON-privileged role of the phase-4 rehearsal
(``acb_app_h3rls``: no superuser, no BYPASSRLS), with two organizations.
Spec: ``personalization.md`` §5.4.

What this proves, and a fake could not:

- one match acts, and two people of one name give a list (§9 PZ-5);
- org B's person and org B's member of the same name never come back to a
  member of org A, and an org-B address is refused as an alias in org A;
- the alias tier wins over a directory match, and an alias of a person who
  left falls through;
- ``preferred_name`` beats ``name``, then the local part of the email, then
  ``app_user.display_name``, with the case and the accents folded;
- a member without ``feature:people`` gets the aliases tier only.
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _address(who: str = "m") -> str:
    return f"{who}.{_tag()}@pz5.test"


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
    """The promoted catalog, with this module's rows removed after."""
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})
            c.execute(text("DELETE FROM people WHERE email = :u"), {"u": email})
            c.execute(text("DELETE FROM app_user WHERE email = :u"), {"u": email})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _member(email: str, *, people: bool = True) -> UserContext:
    features = ["feature:tasks", "feature:people"] if people else ["feature:tasks"]
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(features, roles=["member"]))


def _person(ns, org: str, name: str, *, preferred: str | None = None,
            status: str = "active", who: str = "p") -> str:
    email = _address(who)
    ns.made.append(email)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (id, name, preferred_name, email, status, skills, "
            "source, source_key, organization_id, updated_by, updated_at) "
            "VALUES (gen_random_uuid(), :n, :pn, :e, :s, ARRAY[]::text[], 'manual', "
            ":k, CAST(:o AS uuid), 'test', now())"),
            {"n": name, "pn": preferred, "e": email, "s": status,
             "k": f"manual:{email}", "o": org})
    return email


def _login(ns, org: str, display: str, *, who: str = "u",
           directory_name: str | None = None) -> str:
    """A member. Migration 206 gives each member a people row of the same
    name. *directory_name* renames that row, so only the display name holds
    *display*."""
    email = _address(who)
    ns.made.append(email)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :d, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": email, "d": display, "o": org})
        if directory_name is not None:
            c.execute(text("UPDATE people SET name = :n WHERE lower(email) = lower(:e)"),
                      {"n": directory_name, "e": email})
    return email


def _routes():
    from gateway.routes.admin import assistant_profile, assistant_profile_people

    return assistant_profile, assistant_profile_people


async def _resolve(ns, org: str, user: UserContext, name: str) -> dict:
    _, people = _routes()
    async with tenant_engine_scope(_app_dsn(ns)):
        with _tenant(org):
            return await people.resolve_person_for_assistant(name=name, user=user)


async def _alias(ns, org: str, user: UserContext, alias: str, email: str) -> dict:
    ap, _ = _routes()
    async with tenant_engine_scope(_app_dsn(ns)):
        with _tenant(org):
            return await ap.add_alias(body={"alias": alias, "email": email}, user=user)


def _me(ns) -> UserContext:
    email = _address("me")
    ns.made.append(email)
    return _member(email)


# ── One match acts, two ask ────────────────────────────────────────────────


async def test_one_match_acts_and_two_of_one_name_give_a_list(db) -> None:
    tag = _tag()
    one = _person(db, db.org_a, f"Arjun {tag}")
    p1 = _person(db, db.org_a, f"Priya {tag}")
    p2 = _person(db, db.org_a, f"Priya {tag}")
    me = _me(db)
    single = await _resolve(db, db.org_a, me, f"arjun {tag}")
    two = await _resolve(db, db.org_a, me, f"Priya {tag}")
    assert single == {"source": "people", "total": 1, "directory": True,
                      "truncated": False,
                      "matches": [{"name": f"Arjun {tag}", "email": one}]}
    assert two["source"] == "people" and two["total"] == 2
    assert sorted(m["email"] for m in two["matches"]) == sorted([p1, p2])


# ── The tenant wall ────────────────────────────────────────────────────────


async def test_org_b_s_person_and_member_never_come_back(db) -> None:
    tag = _tag()
    name = f"Zed {tag}"
    b_person = _person(db, db.org_b, name)
    b_login = _login(db, db.org_b, name)
    me = _me(db)
    # Org A has nobody of that name: every tier is empty, nothing of B leaks.
    none = await _resolve(db, db.org_a, me, name)
    by_mail = await _resolve(db, db.org_a, me, b_person)
    by_login = await _resolve(db, db.org_a, me, b_login)
    assert none == {"source": None, "matches": [], "total": 0, "directory": True,
                    "truncated": False}
    assert by_mail["matches"] == [] and by_login["matches"] == []
    # And org A's own person of that name is the one match in org A.
    a_person = _person(db, db.org_a, name)
    mine = await _resolve(db, db.org_a, me, name)
    assert [m["email"] for m in mine["matches"]] == [a_person]
    # Org B, bound, sees its own two: the person, and the member's own
    # people row (migration 206).
    theirs = await _resolve(db, db.org_b, me, name)
    assert sorted(m["email"] for m in theirs["matches"]) == sorted([b_person, b_login])


async def test_an_org_b_address_is_refused_as_an_alias(db) -> None:
    ap, _ = _routes()
    b_person = _person(db, db.org_b, f"Bea {_tag()}")
    b_login = _login(db, db.org_b, f"Bea {_tag()}")
    me = _me(db)
    for email in (b_person, b_login, b_person.upper()):
        with pytest.raises(HTTPException) as exc:
            await _alias(db, db.org_a, me, "Bea", email)
        assert (exc.value.status_code, exc.value.detail) == (422, ap.OUTSIDE_ORG)
    with db.admin_engine.connect() as c:
        stored = c.execute(text(
            "SELECT count(*) FROM user_settings WHERE user_id = :u"),
            {"u": me.email}).scalar()
    assert stored == 0
    # The same address IS an alias in org B, where the person is.
    saved = await _alias(db, db.org_b, me, "Bea", b_person)
    assert saved["email"] == b_person


# ── The tiers ──────────────────────────────────────────────────────────────


async def test_the_alias_wins_over_a_people_match(db) -> None:
    tag = _tag()
    _person(db, db.org_a, f"Raj {tag}")
    raj = _person(db, db.org_a, f"Rajesh Kumar {tag}")
    me = _me(db)
    await _alias(db, db.org_a, me, f"Raj {tag}", raj)
    out = await _resolve(db, db.org_a, me, f"RAJ {tag}")
    assert out["source"] == "alias"
    assert out["matches"] == [{"name": f"Raj {tag}", "email": raj}]


async def test_an_alias_of_a_person_who_left_falls_through(db) -> None:
    tag = _tag()
    gone = _person(db, db.org_a, f"Old {tag}")
    other = _person(db, db.org_a, f"Mo {tag}")
    me = _me(db)
    await _alias(db, db.org_a, me, f"Mo {tag}", gone)
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE people SET status = 'alumni' WHERE email = :e"), {"e": gone})
    out = await _resolve(db, db.org_a, me, f"Mo {tag}")
    assert out["source"] == "people"
    assert [m["email"] for m in out["matches"]] == [other]


async def test_each_name_counts_and_two_candidates_ask(db) -> None:
    tag = _tag()
    pref = _person(db, db.org_a, f"Venkata Jagan {tag}", preferred=f"VJ {tag}")
    _person(db, db.org_a, f"VJ {tag}")
    jose = _person(db, db.org_a, f"Jose {tag}")
    local = _person(db, db.org_a, f"Someone {tag}", who=f"kiran{tag}")
    member = _login(db, db.org_a, f"Tara {tag}", directory_name=f"T. Rao {tag}")
    me = _me(db)
    by_pref = await _resolve(db, db.org_a, me, f"vj {tag}")
    accent = await _resolve(db, db.org_a, me, f"José {tag}")
    by_local = await _resolve(db, db.org_a, me, local.split("@")[0].upper())
    by_member = await _resolve(db, db.org_a, me, f"TARA {tag}")
    # Repair round 1: a preferred name "VJ" and a name "VJ" are two people,
    # so both come back and the assistant asks. No rule hides a person.
    assert pref in [m["email"] for m in by_pref["matches"]]
    assert by_pref["total"] == 2
    assert [m["email"] for m in accent["matches"]] == [jose]
    assert [m["email"] for m in by_local["matches"]] == [local]
    assert (by_member["source"], [m["email"] for m in by_member["matches"]]) == (
        "member", [member])


# ── The People gate ────────────────────────────────────────────────────────


async def test_a_member_without_feature_people_gets_the_aliases_only(db) -> None:
    tag = _tag()
    person = _person(db, db.org_a, f"Nina {tag}")
    _login(db, db.org_a, f"Nina {tag}")
    email = _address("me")
    db.made.append(email)
    plain = _member(email, people=False)
    none = await _resolve(db, db.org_a, plain, f"Nina {tag}")
    assert none == {"source": None, "matches": [], "total": 0, "directory": False,
                    "truncated": False}
    # The alias check needs no feature, and the alias tier answers.
    await _alias(db, db.org_a, plain, f"Nina {tag}", person)
    mine = await _resolve(db, db.org_a, plain, f"nina {tag}")
    assert mine["source"] == "alias"
    assert [m["email"] for m in mine["matches"]] == [person]
