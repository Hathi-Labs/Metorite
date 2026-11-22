"""R8: the topics of the chat list (WS-CO CO-2a, task 3501).

Spec: ``project-docs/specs/chat_organization.md`` §6 and §10 (D163.1,
D163.2). Module: ``gateway/chat_topic.py``. Routes: ``/chat/topics``
(``gateway/routes/chat_topics.py``). Migration:
``infra/postgres/<n>_chat_topic.sql``, found by its content.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

1. the table: its columns, its keys, its CHECKs, FORCE RLS and its policy. A
   second run of the migration changes nothing;
(a) a private topic of member A never reaches member B in the same org: not
    in the list, not by a rename, a hide or a merge, not by a duplicate name,
    and not by an id guess. An admin reads none of it through these routes;
(b) org B never reads or changes a topic of org A, also with the same names,
    and the policy refuses a row stamped with another org;
(c) a member cannot rename, hide or merge a company topic. An admin can, and
    a hidden topic leaves the member's list;
(d) a duplicate name in one list is refused, in any case. The same name in
    another list is no clash;
(e) the member purge deletes the member's private topics in the bound org,
    and keeps the company topics, a colleague's topics and the same address
    in another org;
(f) FORCE RLS holds: a session with no tenant bound reads no row and writes
    no row;
(g) with ``CHAT_TOPICS_ORGS`` off, each route answers 404 and changes nothing;
(h) ``find_or_create_member_topic``, the topic of the automatic pass (CO-1):
    it finds a company topic that shows by its name in any case, else the
    member's own topic, else it makes ONE private topic of the member. It
    never returns a colleague's topic or a topic of another org;
(i) a hidden company topic answers a member who is not an admin as an id
    that does not exist, and a flag, a new emoji and a joined emoji pass the
    CHECK of the table;
(j) two merges that cross, as two real transactions and through the
    routes, never both win and always leave one company row. A merge into
    a target that an admin hides at that moment is refused.

A hermetic fake agrees with any SQL that it gets, so none of this could be
proved without the database.

Mutations, each red here (R7):

* drop ``_VISIBLE`` from ``_LIST_SQL`` -> (a) the list of B holds A's topic;
* drop the admin check of ``rename_topic`` -> (c) a member's rename of a
  company topic no longer answers 403;
* key the unique index without the owner -> (a) B gets 409 on A's name;
* key it on ``owner_email`` with no ``COALESCE`` -> (d) the company list
  takes a second "Sales", because NULL is distinct in a unique index;
* key it on ``name`` and not ``lower(name)`` -> (d) "q4 PLAN" goes in;
* drop the ``chat_topics`` row of the purge -> (e);
* drop ``require_topics`` from the router -> (g);
* drop ``_VISIBLE`` from ``_FIND_SQL`` -> (h) Bob gets Alice's topic;
* drop the hidden filter from ``_FIND_SQL`` -> (h) the pass files a chat
  under a hidden company topic;
* drop ``FOR UPDATE`` from ``_LOCK_SQL`` -> (j) both merges win, and no
  company row stays, and a merge into a target hidden at that moment wins;
* drop ``_SHOWN`` from ``_ONE_SQL`` and ``_LOCK_SQL`` -> (i) a member gets
  403 for a hidden company topic, where an unknown id gets 404.

Not a mutation here: ``FORCE`` binds the table owner, and this suite runs
as the app role, which RLS binds with or without it. (f) proves the policy.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_ALICE = "alice@co2.test"
_BOB = "bob@co2.test"
_ADMIN = "admin@co2.test"  # org A, an admin of company topics
_CAROL = "carol@co2.test"  # org B, an admin there
_DANA = "Dana.Mixed@CO2.test"  # org A, an address in mixed case
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS chat_topic ("
_NOT_FOUND = {"detail": "Topic not found"}


def _migration() -> Path:
    """The migration of the table, found by its content and not its number."""
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Alice, Bob, the admin and Dana in org A, Carol in org B, seeded as the
    superuser."""
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, promoted.org_a), (_BOB, promoted.org_a),
                           (_ADMIN, promoted.org_a), (_DANA, promoted.org_a),
                           (_CAROL, promoted.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
    return promoted


@pytest.fixture
def db(world, monkeypatch):
    """The world with the switch on, ``acb_graph`` sessions as the app role
    (one fresh backend each), and no topic before or after a test."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_topics_orgs", "*", raising=False)
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    with world.admin_engine.begin() as c:
        c.execute(text("DELETE FROM chat_topic"))
    try:
        yield world
    finally:
        eng.dispose()
        with world.admin_engine.begin() as c:
            c.execute(text("DELETE FROM chat_topic"))


def _user(email: str, org: str | None, *, admin: bool = False):
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:chat", *(["admin:settings:manage"] if admin else [])]
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
                       organization_id=org)


def _client(user):
    """The real router behind FastAPI, with its flag and feature gates."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import chat_topics

    app = FastAPI()
    app.include_router(chat_topics.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _company(ns, org: str, name: str, *, hidden: bool = False) -> str:
    """One company topic, seeded as the superuser. CO-2a runs no seed."""
    with ns.admin_engine.begin() as c:
        return c.execute(text(
            "INSERT INTO chat_topic (organization_id, name, scope, hidden_at) "
            "VALUES (CAST(:o AS uuid), :n, 'company', CASE WHEN :h THEN now() END) "
            "RETURNING id::text"), {"o": org, "n": name, "h": hidden}).scalar()


def _rows(ns, *, org: str | None = None) -> set[tuple[str, str, str, str | None, bool]]:
    """Each topic as the superuser sees it: the org, the name, the scope, the
    owner, and whether it is hidden. A set, so no collation decides an order."""
    sql = ("SELECT organization_id::text AS o, name, scope, owner_email, "
           "hidden_at IS NOT NULL AS hidden FROM chat_topic ")
    params: dict[str, str] = {}
    if org is not None:
        sql += "WHERE organization_id = CAST(:o AS uuid) "
        params["o"] = org
    with ns.admin_engine.connect() as c:
        return {(r.o, r.name, r.scope, r.owner_email, r.hidden)
                for r in c.execute(text(sql), params)}


def _names(client, **query) -> list[tuple[str, str]]:
    res = client.get("/chat/topics", params=query)
    assert res.status_code == 200, res.text
    return [(t["scope"], t["name"]) for t in res.json()["topics"]]


def _create(client, name: str) -> str:
    res = client.post("/chat/topics", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["topic"]["id"]


# ── 1. The table ────────────────────────────────────────────────────────────


def test_1_the_table_has_its_keys_its_checks_and_force_rls(db) -> None:
    with db.admin_engine.connect() as c:
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in c.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'chat_topic'"))}
        cons = {r.conname: r.d for r in c.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conrelid = 'chat_topic'::regclass"))}
        index = c.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'chat_topic_name_uq'")).scalar()
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'chat_topic'::regclass")).first()
        policies = c.execute(text(
            "SELECT policyname, qual, with_check FROM pg_policies "
            "WHERE tablename = 'chat_topic'")).fetchall()
    assert cols == {
        "organization_id": ("uuid", "NO"),
        "id": ("uuid", "NO"),
        "name": ("text", "NO"),
        "scope": ("text", "NO"),
        "owner_email": ("text", "YES"),
        "hidden_at": ("timestamp with time zone", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
    }
    assert cons["chat_topic_pkey"] == "PRIMARY KEY (id)"
    assert cons["chat_topic_org_id_key"] == "UNIQUE (organization_id, id)"
    assert "company" in cons["chat_topic_scope"] and "member" in cons["chat_topic_scope"]
    assert {"chat_topic_owner", "chat_topic_name"} <= set(cons)
    assert "UNIQUE INDEX" in index and "lower(name)" in index and "COALESCE(owner_email" in index
    assert tuple(rls) == (True, True)
    assert [p.policyname for p in policies] == ["chat_topic_tenant_isolation"]
    assert "app.tenant_id" in policies[0].qual and "app.tenant_id" in policies[0].with_check


def test_1_the_migration_runs_twice(db) -> None:
    sql = _migration().read_text(encoding="utf-8")
    # The file holds its own BEGIN and COMMIT, so it runs on an autocommit
    # connection, through the cursor, as the ladder runs it.
    auto = db.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    test_1_the_table_has_its_keys_its_checks_and_force_rls(db)


@pytest.mark.parametrize(("name", "scope", "owner"), [
    ("Sales", "company", "alice@co2.test"),  # a company topic names no owner
    ("Mine", "member", None),  # a member topic names its owner
    ("Mine", "member", "Alice@co2.test"),  # in lower case
    ("Mine", "member", ""),
    ("Mine", "team", None),  # two scopes only
    (" Mine", "member", "alice@co2.test"),  # trimmed
    ("x" * 41, "member", "alice@co2.test"),  # 40 at most
    ("", "member", "alice@co2.test"),
    ("Mi\nne", "member", "alice@co2.test"),  # no control character
])
def test_1_the_checks_refuse_a_bad_row(db, name: str, scope: str, owner: str | None) -> None:
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_topic (organization_id, name, scope, owner_email) "
            "VALUES (CAST(:o AS uuid), :n, :s, :e)"),
            {"o": db.org_a, "n": name, "s": scope, "e": owner})


# ── (a) A private topic never reaches a colleague ───────────────────────────


def test_a_a_private_topic_never_reaches_a_colleague(db) -> None:
    alice = _client(_user(_ALICE, db.org_a))
    bob = _client(_user(_BOB, db.org_a))
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    sales = _company(db, db.org_a, "Sales")
    secret = _create(alice, "Acquisition of Globex")

    assert _names(alice) == [("company", "Sales"), ("member", "Acquisition of Globex")]
    # The colleague and the admin read the company list only.
    assert _names(bob) == [("company", "Sales")]
    assert _names(admin) == [("company", "Sales")]
    assert _names(admin, include_hidden="true") == [("company", "Sales")]

    # A rename, a hide and a merge of her id answer as an id that does not
    # exist, for a member and for an admin.
    guess = str(uuid.uuid4())
    for client in (bob, admin):
        for tid in (secret, guess):
            for res in (client.patch(f"/chat/topics/{tid}", json={"name": "Mine now"}),
                        client.patch(f"/chat/topics/{tid}", json={"hidden": True}),
                        client.post(f"/chat/topics/{tid}/merge", json={"into": sales}),
                        client.post(f"/chat/topics/{sales}/merge", json={"into": tid})):
                assert (res.status_code, res.json()) == (404, _NOT_FOUND), res.text

    # Her name is no clash for him, so a refusal cannot show that she holds it.
    # His own second copy, in another case, is a clash in his own list.
    assert _create(bob, "ACQUISITION OF GLOBEX ") != secret
    res = bob.post("/chat/topics", json={"name": "Acquisition of Globex"})
    assert res.status_code == 409, res.text

    assert _rows(db, org=db.org_a) == {
        (db.org_a, "Sales", "company", None, False),
        (db.org_a, "ACQUISITION OF GLOBEX", "member", _BOB, False),
        (db.org_a, "Acquisition of Globex", "member", _ALICE, False),
    }
    assert _names(alice) == [("company", "Sales"), ("member", "Acquisition of Globex")]


def test_a_an_address_in_mixed_case_keys_on_lower_case(db) -> None:
    dana = _client(_user(_DANA, db.org_a))
    tid = _create(dana, "Hiring plan")
    assert _names(dana) == [("member", "Hiring plan")]
    res = dana.patch(f"/chat/topics/{tid}", json={"name": "Hiring plan 2027"})
    assert res.status_code == 200, res.text
    assert _rows(db, org=db.org_a) == {
        (db.org_a, "Hiring plan 2027", "member", _DANA.lower(), False)}


# ── (b) Org B never reads or changes org A ──────────────────────────────────


def test_b_org_b_never_reads_or_changes_org_a(db) -> None:
    from acb_graph import tenant_session

    a_sales = _company(db, db.org_a, "Sales")
    b_sales = _company(db, db.org_b, "Sales")
    alice = _client(_user(_ALICE, db.org_a))
    a_mine = _create(alice, "Board prep")
    carol = _client(_user(_CAROL, db.org_b, admin=True))
    # The same name in each org: Carol sees her own org's only.
    b_mine = _create(carol, "Board prep")
    res = carol.get("/chat/topics", params={"include_hidden": "true"})
    assert [(t["id"], t["name"]) for t in res.json()["topics"]] == [
        (b_sales, "Sales"), (b_mine, "Board prep")]
    # An admin of org B gets 404 on each id of org A.
    for tid in (a_sales, a_mine):
        for res in (carol.patch(f"/chat/topics/{tid}", json={"name": "Taken"}),
                    carol.patch(f"/chat/topics/{tid}", json={"hidden": True}),
                    carol.post(f"/chat/topics/{tid}/merge", json={"into": b_sales}),
                    carol.post(f"/chat/topics/{b_sales}/merge", json={"into": tid})):
            assert (res.status_code, res.json()) == (404, _NOT_FOUND), res.text
    assert _rows(db, org=db.org_a) == {
        (db.org_a, "Sales", "company", None, False),
        (db.org_a, "Board prep", "member", _ALICE, False)}
    with tenant_session(db.org_b) as s:
        assert s.execute(text("SELECT count(*) FROM chat_topic WHERE id IN "
                              "(CAST(:x AS uuid), CAST(:y AS uuid))"),
                         {"x": a_sales, "y": a_mine}).scalar() == 0
    # The policy refuses a row stamped with another org, from the app role.
    with pytest.raises(DBAPIError), tenant_session(db.org_b) as s:
        s.execute(text(
            "INSERT INTO chat_topic (organization_id, name, scope) "
            "VALUES (CAST(:o AS uuid), 'Planted', 'company')"), {"o": db.org_a})


# ── (c) A company topic: a member may not change it, an admin may ───────────


def test_c_a_member_cannot_change_a_company_topic(db) -> None:
    sales = _company(db, db.org_a, "Sales")
    ops = _company(db, db.org_a, "Operations")
    bob = _client(_user(_BOB, db.org_a))
    for res in (bob.patch(f"/chat/topics/{sales}", json={"name": "Revenue"}),
                bob.patch(f"/chat/topics/{sales}", json={"hidden": True}),
                bob.post(f"/chat/topics/{ops}/merge", json={"into": sales})):
        assert res.status_code == 403, res.text
        assert res.json() == {"detail": "Only an admin can change a company topic."}
    assert _rows(db, org=db.org_a) == {
        (db.org_a, "Operations", "company", None, False),
        (db.org_a, "Sales", "company", None, False)}


def test_c_an_admin_renames_hides_shows_and_merges_a_company_topic(db) -> None:
    sales = _company(db, db.org_a, "Sales")
    ops = _company(db, db.org_a, "Ops")
    operations = _company(db, db.org_a, "Operations")
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    bob = _client(_user(_BOB, db.org_a))

    res = admin.patch(f"/chat/topics/{sales}", json={"name": "Revenue"})
    assert res.status_code == 200 and res.json()["topic"]["name"] == "Revenue", res.text
    assert ("company", "Revenue") in _names(bob)

    res = admin.patch(f"/chat/topics/{sales}", json={"hidden": True})
    assert res.status_code == 200 and res.json()["topic"]["hidden"] is True, res.text
    assert ("company", "Revenue") not in _names(bob)
    assert ("company", "Revenue") not in _names(admin)
    assert ("company", "Revenue") in _names(admin, include_hidden="true")
    # A member who asks for the hidden topics gets none.
    assert ("company", "Revenue") not in _names(bob, include_hidden="true")
    # A merge into a hidden topic is refused, and so is a hide of a member topic.
    res = admin.post(f"/chat/topics/{ops}/merge", json={"into": sales})
    assert (res.status_code, res.json()) == (404, _NOT_FOUND), res.text
    own = _create(admin, "My notes")
    res = admin.patch(f"/chat/topics/{own}", json={"hidden": True})
    assert res.status_code == 422, res.text

    res = admin.patch(f"/chat/topics/{sales}", json={"hidden": False})
    assert res.status_code == 200 and res.json()["topic"]["hidden"] is False, res.text
    assert ("company", "Revenue") in _names(bob)

    res = admin.post(f"/chat/topics/{ops}/merge", json={"into": operations})
    assert res.status_code == 200, res.text
    assert res.json() == {"merged": ops, "topic": {
        "id": operations, "name": "Operations", "scope": "company", "mine": False,
        "hidden": False}}
    assert _names(bob) == [("company", "Operations"), ("company", "Revenue")]
    res = admin.post(f"/chat/topics/{operations}/merge", json={"into": operations})
    assert res.status_code == 422, res.text


def test_c_a_refused_step_undoes_the_whole_patch(db) -> None:
    """A rename and a hide in one PATCH: a member's own topic cannot be hidden,
    so the rename does not stay either."""
    alice = _client(_user(_ALICE, db.org_a, admin=True))
    tid = _create(alice, "Draft")
    res = alice.patch(f"/chat/topics/{tid}", json={"name": "Final", "hidden": True})
    assert res.status_code == 422, res.text
    assert _rows(db, org=db.org_a) == {(db.org_a, "Draft", "member", _ALICE, False)}


# ── (d) One name for each list ──────────────────────────────────────────────


def test_d_a_duplicate_name_in_one_list_is_refused(db) -> None:
    _company(db, db.org_a, "Sales")
    finance = _company(db, db.org_a, "Finance")
    alice = _client(_user(_ALICE, db.org_a))
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    plan = _create(alice, "Q4 plan")
    for name in ("Q4 plan", "q4 PLAN", "  Q4 Plan "):
        res = alice.post("/chat/topics", json={"name": name})
        assert res.status_code == 409, res.text
        assert res.json() == {"detail": "You already have a topic with this name."}
    other = _create(alice, "Q1 plan")
    res = alice.patch(f"/chat/topics/{other}", json={"name": "Q4 PLAN"})
    assert res.status_code == 409, res.text
    # A change of case of the topic itself is no clash.
    res = alice.patch(f"/chat/topics/{plan}", json={"name": "q4 plan"})
    assert res.status_code == 200, res.text
    # The company list holds one "Sales", in any case.
    res = admin.patch(f"/chat/topics/{finance}", json={"name": "SALES"})
    assert res.status_code == 409, res.text
    assert res.json() == {"detail": "The company already has a topic with this name."}
    # A member topic with the name of a company topic is another list.
    assert _create(alice, "Sales")
    assert sorted(n for _o, n, _s, _e, _h in _rows(db, org=db.org_a)) == [
        "Finance", "Q1 plan", "Sales", "Sales", "q4 plan"]


# ── (e) The member purge ────────────────────────────────────────────────────


@pytest.fixture
def purge_world(db, monkeypatch):
    """An owner in org A who survives the purge, and the off-Postgres hops
    stubbed, as ``test_chat_summary_r8.py`` does."""
    from gateway.routes.admin import members
    from gateway.routes.crm import questions

    async def _no_keys(org: str, email: str) -> int:
        return 0

    monkeypatch.setattr(members, "record_admin_change", lambda *a, **k: None)
    monkeypatch.setattr(questions, "forget_member_keys", _no_keys)
    with db.admin_engine.begin() as c:
        role = c.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
            "VALUES (CAST(:o AS uuid), 'owner', 'Owner', true, 0) "
            "ON CONFLICT DO NOTHING RETURNING id"), {"o": db.org_a}).scalar()
        if role is None:
            role = c.execute(text(
                "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) "
                "AND slug = 'owner'"), {"o": db.org_a}).scalar()
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "SELECT id, :r, CAST(:o AS uuid) FROM app_user WHERE email = :e "
            "ON CONFLICT DO NOTHING"),
            {"r": role, "e": db.owner_email, "o": db.org_a})
    leaver = f"leaver.{uuid.uuid4().hex[:8]}@co2.test"
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": leaver, "o": db.org_a})
    db.leaver = leaver
    yield db
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"), {"e": leaver})


async def test_e_the_purge_deletes_the_private_topics_of_the_member(purge_world) -> None:
    from acb_common.db import tenant_session
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import purge_member_rows

    ns = purge_world
    _company(ns, ns.org_a, "Sales")
    leaver_a = _client(_user(ns.leaver, ns.org_a))
    _create(leaver_a, "Exit notes")
    _create(leaver_a, "Handover")
    _create(_client(_user(_BOB, ns.org_a)), "Exit notes")
    _create(_client(_user(ns.leaver, ns.org_b)), "Exit notes")
    dsn = ns.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(dsn), tenant_session(ns.org_a) as session:
        member = await get_member(session, ns.org_a, ns.leaver)
        deleted, _kept, _plans = await purge_member_rows(
            session, ns.org_a, member, actor_email=ns.owner_email)
    assert deleted["chat_topics"] == 2
    assert _rows(ns) == {
        (ns.org_a, "Sales", "company", None, False),
        (ns.org_a, "Exit notes", "member", _BOB, False),
        (ns.org_b, "Exit notes", "member", ns.leaver, False),
    }


# ── (f) FORCE RLS ───────────────────────────────────────────────────────────


def test_f_a_session_with_no_tenant_reads_and_writes_no_row(db) -> None:
    _company(db, db.org_a, "Sales")
    _create(_client(_user(_ALICE, db.org_a)), "Mine")
    # A fresh backend of the app role that never bound a tenant.
    eng = create_engine(db.app_url, poolclass=NullPool, future=True)
    try:
        with eng.connect() as c:
            assert c.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar() \
                in (None, "")
            assert c.execute(text("SELECT count(*) FROM chat_topic")).scalar() == 0
        with pytest.raises(DBAPIError), eng.begin() as c:
            c.execute(text("INSERT INTO chat_topic (name, scope) VALUES ('Planted', 'company')"))
    finally:
        eng.dispose()
    assert len(_rows(db)) == 2


# ── (g) The switch ──────────────────────────────────────────────────────────


def test_g_with_the_switch_off_each_route_is_404_and_changes_nothing(db, monkeypatch) -> None:
    from acb_common import get_settings

    sales = _company(db, db.org_a, "Sales")
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    mine = _create(admin, "Mine")
    before = _rows(db)
    for org in ("", db.org_b):
        monkeypatch.setattr(get_settings(), "chat_topics_orgs", org, raising=False)
        for res in (admin.get("/chat/topics"),
                    admin.post("/chat/topics", json={"name": "New"}),
                    admin.patch(f"/chat/topics/{sales}", json={"name": "Revenue"}),
                    admin.patch(f"/chat/topics/{mine}", json={"name": "Renamed"}),
                    admin.post(f"/chat/topics/{sales}/merge", json={"into": sales})):
            assert (res.status_code, res.json()) == (404, {"detail": "Not Found"}), res.text
    assert _rows(db) == before
    # The switch on again: the same list answers.
    monkeypatch.setattr(get_settings(), "chat_topics_orgs", db.org_a, raising=False)
    assert _names(admin) == [("company", "Sales"), ("member", "Mine")]


# ── (i) A hidden company topic, and the names of the table ──────────────────


def test_i_a_hidden_company_topic_reads_as_no_topic_for_a_member(db) -> None:
    """Reviewer note: a member who is not an admin gets the 404 of an unknown
    id for a hidden company topic, so the member cannot tell the two apart.
    The admin still reads it and shows it again."""
    legal = _company(db, db.org_a, "Legal", hidden=True)
    sales = _company(db, db.org_a, "Sales")
    bob = _client(_user(_BOB, db.org_a))
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    for tid in (legal, str(uuid.uuid4())):
        for res in (bob.patch(f"/chat/topics/{tid}", json={"name": "Contracts"}),
                    bob.patch(f"/chat/topics/{tid}", json={"hidden": False}),
                    bob.post(f"/chat/topics/{tid}/merge", json={"into": sales}),
                    bob.post(f"/chat/topics/{sales}/merge", json={"into": tid})):
            assert (res.status_code, res.json()) == (404, _NOT_FOUND), res.text
    # A topic that shows still answers 403 to him: the list shows it anyway.
    assert bob.patch(f"/chat/topics/{sales}", json={"name": "Revenue"}).status_code == 403
    res = admin.patch(f"/chat/topics/{legal}", json={"hidden": False})
    assert res.status_code == 200 and res.json()["topic"]["hidden"] is False, res.text


#: The flag of England: a black flag and six tag characters (Cf).
_ENGLAND = "".join(chr(p) for p in (0x1F3F4, 0xE0067, 0xE0062, 0xE0065, 0xE006E,
                                    0xE0067, 0xE007F))
#: U+1FAE9, an emoji of Unicode 16, and a family joined by two ZWJ.
_NEW_EMOJI = chr(0x1FAE9)
_FAMILY = "".join(chr(p) for p in (0x1F468, 0x200D, 0x1F469, 0x200D, 0x1F467))


def test_i_an_emoji_name_passes_the_check_of_the_table(db) -> None:
    """Reviewer P2: the store takes a flag, a new emoji and a joined emoji,
    and so must the CHECK of the table, or a create answers 500."""
    alice = _client(_user(_ALICE, db.org_a))
    names = ("Team " + _ENGLAND, "Tired " + _NEW_EMOJI, "Family " + _FAMILY)
    for name in names:
        _create(alice, name)
    assert {n for _o, n, _s, _e, _h in _rows(db, org=db.org_a)} == set(names)


# ── (j) Two merges at once ──────────────────────────────────────────────────


def _until(check, timeout: float = 5.0) -> bool:
    """Poll *check* every 50 ms for at most *timeout* seconds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def _a_session_waits_on_a_lock(ns) -> bool:
    """True when a backend of the test database waits on a lock. AUTOCOMMIT,
    because ``pg_stat_activity`` keeps one snapshot for each transaction."""
    auto = ns.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c:
        return bool(c.execute(text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'")).scalar())


def _in_a_thread(fn) -> tuple[threading.Thread, dict]:
    out: dict = {}

    def run() -> None:
        try:
            out["value"] = fn()
        except BaseException as exc:  # the test reads it
            out["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def test_j_two_merges_that_cross_cannot_both_win(db) -> None:
    """Verifier P1: X into Y and Y into X at once left no company row. The
    first merge holds both rows (``FOR UPDATE``, id order) until its commit.
    The second waits, then finds its target gone, and gets ``missing``.
    Mutation: drop ``FOR UPDATE`` from ``_LOCK_SQL`` -> the second merge does
    not wait, both answer ``ok``, and no company row stays."""
    from acb_graph import tenant_session
    from gateway.chat_topic import merge_company_topics

    x = _company(db, db.org_a, "Ops")
    y = _company(db, db.org_a, "Operations")

    def second():
        with tenant_session(db.org_a) as s2:
            return merge_company_topics(s2, y, into=x, member=_ADMIN, admin=True).outcome

    with tenant_session(db.org_a) as s1:
        first = merge_company_topics(s1, x, into=y, member=_ADMIN, admin=True)
        assert first.outcome == "ok"
        t, out = _in_a_thread(second)
        _until(lambda: _a_session_waits_on_a_lock(db) or not t.is_alive())
    t.join(30)
    assert not t.is_alive() and "error" not in out, out
    assert out["value"] == "missing"
    assert _rows(db, org=db.org_a) == {(db.org_a, "Operations", "company", None, False)}


def test_j_crossing_merge_routes_leave_one_topic(db) -> None:
    """The same race through the routes, three times: one merge answers 200,
    the other 404, and one of the two company rows stays."""
    admin_1 = _client(_user(_ADMIN, db.org_a, admin=True))
    admin_2 = _client(_user(_ADMIN, db.org_a, admin=True))
    for n in range(3):
        x = _company(db, db.org_a, f"Ops {n}")
        y = _company(db, db.org_a, f"Operations {n}")
        gate = threading.Barrier(2)

        def merge(client, src, dst, gate=gate):
            gate.wait(10)
            return client.post(f"/chat/topics/{src}/merge", json={"into": dst}).status_code

        t1, o1 = _in_a_thread(lambda c=admin_1, s=x, d=y: merge(c, s, d))
        t2, o2 = _in_a_thread(lambda c=admin_2, s=y, d=x: merge(c, s, d))
        t1.join(30)
        t2.join(30)
        assert "error" not in o1 and "error" not in o2, (o1, o2)
        assert sorted((o1["value"], o2["value"])) == [200, 404], (o1, o2)
        left = {n_ for _o, n_, _s, _e, _h in _rows(db, org=db.org_a)} & {f"Ops {n}",
                                                                        f"Operations {n}"}
        assert len(left) == 1, left


def test_j_a_merge_into_a_target_that_an_admin_hides_at_that_moment_is_refused(db) -> None:
    """The hide commits first, so the merge reads the hidden target under its
    lock and refuses. Mutation: drop ``FOR UPDATE`` from ``_LOCK_SQL`` -> the
    merge reads the target as it was before the hide, and deletes X."""
    from acb_graph import tenant_session
    from gateway.chat_topic import merge_company_topics, set_hidden

    x = _company(db, db.org_a, "Ops")
    y = _company(db, db.org_a, "Operations")

    def merge():
        with tenant_session(db.org_a) as s2:
            return merge_company_topics(s2, x, into=y, member=_ADMIN, admin=True).outcome

    with tenant_session(db.org_a) as s1:
        hid = set_hidden(s1, y, member=_ADMIN, hidden=True, admin=True)
        assert hid.outcome == "ok"
        t, out = _in_a_thread(merge)
        _until(lambda: _a_session_waits_on_a_lock(db) or not t.is_alive())
    t.join(30)
    assert not t.is_alive() and "error" not in out, out
    assert out["value"] == "missing"
    assert _rows(db, org=db.org_a) == {
        (db.org_a, "Ops", "company", None, False),
        (db.org_a, "Operations", "company", None, True)}


# ── (h) The topic of the automatic pass (CO-1) ──────────────────────────────


def _find(org: str, member: str, name: str) -> tuple[str, str, str]:
    """``find_or_create_member_topic`` in a tenant-bound session of the app role."""
    from acb_graph import tenant_session
    from gateway.chat_topic import find_or_create_member_topic

    with tenant_session(org) as s:
        t = find_or_create_member_topic(s, member, name)
    return t.id, t.name, t.scope


def test_h_the_pass_finds_a_company_topic_in_any_case(db) -> None:
    sales = _company(db, db.org_a, "Sales")
    assert _find(db.org_a, _ALICE, "  sALES ") == (sales, "Sales", "company")
    assert _find(db.org_a, _BOB, "Sales") == (sales, "Sales", "company")
    # No member topic was made.
    assert _rows(db, org=db.org_a) == {(db.org_a, "Sales", "company", None, False)}


def test_h_the_pass_makes_a_private_topic_once(db) -> None:
    first = _find(db.org_a, _DANA, "Hiring plan")
    assert first[1:] == ("Hiring plan", "member")
    assert _find(db.org_a, _DANA, "HIRING PLAN") == first
    assert _find(db.org_a, _DANA.lower(), "hiring plan") == first
    assert _rows(db, org=db.org_a) == {
        (db.org_a, "Hiring plan", "member", _DANA.lower(), False)}
    # Only the member reads it.
    assert _names(_client(_user(_BOB, db.org_a))) == []


def test_h_the_pass_never_returns_a_colleagues_topic(db) -> None:
    secret = _create(_client(_user(_ALICE, db.org_a)), "Acquisition of Globex")
    bobs = _find(db.org_a, _BOB, "acquisition of globex")
    assert bobs[0] != secret
    assert bobs[1:] == ("acquisition of globex", "member")
    # Org B: the same name in org A is no match.
    a_sales = _company(db, db.org_a, "Sales")
    carols = _find(db.org_b, _CAROL, "Sales")
    assert carols[0] != a_sales and carols[2] == "member"
    assert _rows(db) == {
        (db.org_a, "Sales", "company", None, False),
        (db.org_a, "Acquisition of Globex", "member", _ALICE, False),
        (db.org_a, "acquisition of globex", "member", _BOB, False),
        (db.org_b, "Sales", "member", _CAROL, False),
    }


def test_h_the_pass_skips_a_hidden_company_topic(db) -> None:
    hidden = _company(db, db.org_a, "Legal", hidden=True)
    tid, name, scope = _find(db.org_a, _ALICE, "Legal")
    assert tid != hidden and (name, scope) == ("Legal", "member")


def test_the_suite_finds_the_migration_by_its_content() -> None:
    assert re.fullmatch(r"\d+_chat_topic\.sql", _migration().name)
