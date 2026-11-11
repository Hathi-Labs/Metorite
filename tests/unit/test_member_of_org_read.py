"""H-232b (task 1712): the two ``(email, org)`` reads of a run gate.

``executor._first_party_admin_runs`` and ``executor._agent_push_allowed`` know
the run's org. H-232 asked ``resolve_identity`` for "the member's ONE org". With
``IDENTITY_CUTOVER`` on, a person with two active memberships has no such org,
and PLN-Q5 (2026-11-02) allows that person. So the gates now ask two narrower
questions, both in ``acb_auth.access``:

* ``is_active_member(email, org)``: is this person an active member of THIS
  org? Flag ON it reads ``user_identity JOIN org_membership``. Flag OFF it reads
  the ``app_user`` row, as the flag-OFF ``resolve_identity`` does.
* ``resolve_access_in_org(email, org)``: the roles of this person in THIS org.
  The roles live on the ``app_user`` row, and that row is in one org only.

Two halves. The structural half takes no database and must never skip. The R8
half answers to ``TENANT_LADDER_DATABASE_URL`` (never ``DATABASE_URL``, see
``test_h6_identity_shadow.py``) and proves the SQL on a real Postgres.

Mutation record (R7): drop ``m.organization_id = CAST(:org AS uuid)`` from
``_MEMBER_OF_ORG_IDENTITY_SQL``, and
``test_two_active_memberships_pass_for_each_and_not_for_a_third`` goes red.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_member_of_org_read.py -v -rs
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

import acb_auth.access as access_mod
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

# Resolved by name as a fixture (the phase-4 R8 test). The import is load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import promoted  # noqa: F401

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass. Run `bash scripts/dev_db.sh`."
    ),
)

ORG = "aaaaaaaa-0000-4000-8000-000000000001"


# ── The structural half: no database, and it must never skip ────────────────

class _Capture:
    """A session factory that records each statement and answers *rows*."""

    def __init__(self, rows: list[tuple] | None = None, raises: Exception | None = None):
        self.rows = rows or []
        self.raises = raises
        self.sql: list[str] = []
        self.params: list[dict] = []
        self.bound: list[str | None] = []

    def __call__(self, organization_id: str | None = None) -> _Capture:
        """Stands in for ``tenant_session(org)``: records the bound org."""
        self.bound.append(organization_id)
        return self

    async def __aenter__(self) -> _Capture:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: object, params: dict | None = None) -> _Capture:
        if self.raises is not None:
            raise self.raises
        self.sql.append(str(sql))
        self.params.append(dict(params or {}))
        return self

    def first(self):
        return self.rows[0] if self.rows else None


async def test_flag_off_reads_the_app_user_row_of_the_org(monkeypatch) -> None:
    """Flag OFF is the old path: the ``app_user`` row, with the org as a
    predicate. ``app_user`` holds one org per person, so nobody is locked out."""
    monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
    cap = _Capture(rows=[(1,)])
    monkeypatch.setattr(access_mod, "tenant_session", cap)
    assert await access_mod.is_active_member(" Admin@Example.COM ", ORG) is True
    assert "FROM app_user" in cap.sql[0]
    assert "org_membership" not in cap.sql[0]
    assert cap.params[0] == {"email": "admin@example.com", "org": ORG}
    assert cap.bound == [ORG], "the read must bind the org it asks about"


async def test_flag_on_reads_the_membership_of_the_org(monkeypatch) -> None:
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    cap = _Capture(rows=[(1,)])
    monkeypatch.setattr(access_mod, "tenant_session", cap)
    assert await access_mod.is_active_member("admin@example.com", ORG) is True
    assert "JOIN org_membership" in cap.sql[0]
    assert "app_user" not in cap.sql[0]
    assert cap.bound == [ORG]


@pytest.mark.parametrize("flag", ["", "1"])
async def test_no_row_is_not_a_member(monkeypatch, flag: str) -> None:
    monkeypatch.setenv("IDENTITY_CUTOVER", flag)
    monkeypatch.setattr(access_mod, "tenant_session", _Capture(rows=[]))
    assert await access_mod.is_active_member("a@example.com", ORG) is False


@pytest.mark.parametrize("email, org", [("", ORG), (None, ORG), ("a@x", ""), ("a@x", None)])
async def test_a_blank_email_or_org_is_not_a_member_and_reads_nothing(
    monkeypatch, email, org,
) -> None:
    cap = _Capture(rows=[(1,)])
    monkeypatch.setattr(access_mod, "tenant_session", cap)
    assert await access_mod.is_active_member(email, org) is False
    assert await access_mod.resolve_access_in_org(email, org) == access_mod.EffectiveAccess(
        is_active=False)
    assert cap.sql == []


@pytest.mark.parametrize("flag", ["", "1"])
async def test_a_read_error_fails_closed(monkeypatch, flag: str) -> None:
    """Any read error answers no, and never sets the sign-in flag."""
    monkeypatch.setenv("IDENTITY_CUTOVER", flag)
    access_mod._identity_read_failed.set(False)
    monkeypatch.setattr(
        access_mod, "tenant_session",
        _Capture(raises=RuntimeError("relation does not exist")),
    )
    assert await access_mod.is_active_member("a@example.com", ORG) is False
    refused = await access_mod.resolve_access_in_org("a@example.com", ORG)
    assert refused.is_active is False and not refused.has("*")
    assert access_mod.identity_read_failed() is False


async def test_a_pool_timeout_raises_so_the_gate_refuses(monkeypatch) -> None:
    monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
    monkeypatch.setattr(access_mod.db_busy, "is_db_timeout", lambda exc: True)
    monkeypatch.setattr(access_mod.db_busy, "mark", lambda: None)
    monkeypatch.setattr(
        access_mod, "tenant_session", _Capture(raises=TimeoutError("QueuePool limit")),
    )
    with pytest.raises(access_mod.IdentityUnavailable):
        await access_mod.is_active_member("a@example.com", ORG)
    with pytest.raises(access_mod.IdentityUnavailable):
        await access_mod.resolve_access_in_org("a@example.com", ORG)


def test_the_access_read_is_the_role_read_with_the_org_as_a_predicate() -> None:
    assert access_mod._ACCESS_IN_ORG_SQL != access_mod._ACCESS_SQL
    assert "u.organization_id = CAST(:org AS uuid)" in access_mod._ACCESS_IN_ORG_SQL
    assert access_mod._ACCESS_IN_ORG_SQL.replace(
        "\n       AND u.organization_id = CAST(:org AS uuid)", "",
    ) == access_mod._ACCESS_SQL


def test_both_gates_use_the_two_reads_and_not_the_email_reads() -> None:
    """Both run gates go through ``_member_access_in_run_org``."""
    import inspect

    import orchestrator.executor as executor

    helper = inspect.getsource(executor._member_access_in_run_org)
    assert "is_active_member(member, org)" in helper
    assert "resolve_access_in_org(member, org)" in helper
    for gate in (executor._first_party_admin_runs, executor._agent_push_allowed):
        body = inspect.getsource(gate).split('"""')[-1]
        assert "_member_access_in_run_org(member, org)" in body
        assert "await resolve_identity(" not in body
        assert "await resolve_access(" not in body


# ── The R8 half: the two reads against the replayed ladder ──────────────────

@pytest.fixture(scope="module")
def replayed():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


class _World:
    """Committed rows for one test, removed at the end."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.tag = uuid.uuid4().hex[:8]
        self.slugs: list[str] = []
        self.emails: list[str] = []

    def org(self, name: str) -> str:
        slug = f"h232b-{name}-{self.tag}"
        self.slugs.append(slug)
        with self.engine.begin() as c:
            return str(c.execute(
                text("INSERT INTO organization (slug, display_name) "
                     "VALUES (:s, :s) RETURNING id"), {"s": slug}).scalar_one())

    def identity(self, email: str) -> str:
        self.emails.append(email)
        with self.engine.begin() as c:
            return str(c.execute(
                text("INSERT INTO user_identity (email, display_name) "
                     "VALUES (:e, :e) RETURNING id"), {"e": email}).scalar_one())

    def membership(self, org: str, uid: str, status: str = "active") -> None:
        with self.engine.begin() as c:
            c.execute(
                text("INSERT INTO org_membership (organization_id, user_id, status) "
                     "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :s)"),
                {"o": org, "u": uid, "s": status})

    def app_user(self, org: str, email: str, *, status: str = "active",
                 permission: str | None = None) -> None:
        """The ``app_user`` row, with one role that grants *permission*."""
        self.emails.append(email)
        with self.engine.begin() as c:
            uid = str(c.execute(
                text("INSERT INTO app_user (email, display_name, role, status, "
                     "organization_id, joined_at) VALUES (:e, :e, 'employee', "
                     ":st, CAST(:o AS uuid), now()) RETURNING id"),
                {"e": email, "o": org, "st": status}).scalar_one())
            if permission is None:
                return
            role = str(c.execute(
                text("INSERT INTO org_role (organization_id, slug, display_name, "
                     "is_system, rank) VALUES (CAST(:o AS uuid), :r, :r, false, 100) "
                     "RETURNING id"), {"o": org, "r": f"h232b-{self.tag}"}).scalar_one())
            c.execute(
                text("INSERT INTO org_role_permission (role_id, permission) "
                     "VALUES (CAST(:r AS uuid), :p)"), {"r": role, "p": permission})
            c.execute(
                text("INSERT INTO user_role (user_id, role_id, assigned_by) "
                     "VALUES (CAST(:u AS uuid), CAST(:r AS uuid), 'h232b-test')"),
                {"u": uid, "r": role})

    def drop(self) -> None:
        with self.engine.begin() as c:
            for email in self.emails:
                c.execute(text("DELETE FROM app_user WHERE lower(email) = lower(:e)"),
                          {"e": email})
                c.execute(text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                          {"e": email})
            for slug in self.slugs:
                c.execute(text("DELETE FROM organization WHERE slug = :s"), {"s": slug})


@pytest.fixture
def world(replayed):
    w = _World(replayed)
    try:
        yield w
    finally:
        w.drop()


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    token = access_mod._identity_read_failed.set(False)
    yield
    access_mod._identity_read_failed.reset(token)


@_DB_GATE
class TestFlagOn:
    """``IDENTITY_CUTOVER`` on: the read of ``org_membership``."""

    async def test_two_active_memberships_pass_for_each_and_not_for_a_third(
        self, world, monkeypatch,
    ) -> None:
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        a, b, c = world.org("a"), world.org("b"), world.org("c")
        email = f"two-{world.tag}@h232b.example"
        uid = world.identity(email)
        world.membership(a, uid)
        world.membership(b, uid)
        async with tenant_engine_scope(_URL):
            # The lockout this fixes: the one-org read has no answer.
            assert await access_mod.resolve_identity(email) == (None, None)
            assert access_mod.identity_read_failed() is False
            assert await access_mod.is_active_member(email, a) is True
            assert await access_mod.is_active_member(email, b) is True
            assert await access_mod.is_active_member(email, c) is False

    @pytest.mark.parametrize("status", ["suspended", "removed", "invited"])
    async def test_a_membership_that_is_not_active_is_refused(
        self, world, monkeypatch, status: str,
    ) -> None:
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        a, b = world.org("a"), world.org("b")
        email = f"gone-{world.tag}@h232b.example"
        uid = world.identity(email)
        world.membership(a, uid, status)
        world.membership(b, uid)
        async with tenant_engine_scope(_URL):
            assert await access_mod.is_active_member(email, a) is False
            assert await access_mod.is_active_member(email, b) is True

    async def test_the_email_compare_ignores_case(self, world, monkeypatch) -> None:
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        a = world.org("a")
        uid = world.identity(f"Mixed.Case-{world.tag}@H232b.Example")
        world.membership(a, uid)
        async with tenant_engine_scope(_URL):
            assert await access_mod.is_active_member(
                f"mixed.case-{world.tag}@h232b.example", a) is True
            assert await access_mod.is_active_member(
                f"MIXED.CASE-{world.tag}@H232B.EXAMPLE", a) is True

    async def test_an_org_that_is_not_a_uuid_fails_closed(self, world, monkeypatch) -> None:
        """A real cast error, not a fake one: the answer is no."""
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        a = world.org("a")
        email = f"cast-{world.tag}@h232b.example"
        world.membership(a, world.identity(email))
        async with tenant_engine_scope(_URL):
            assert await access_mod.is_active_member(email, "not-a-uuid") is False

    async def test_access_counts_only_in_the_org_of_the_role_row(
        self, world, monkeypatch,
    ) -> None:
        """Member of two orgs, roles in one. The access read in the other org
        is no access, so the roles of one org never act in another."""
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        a, b = world.org("a"), world.org("b")
        email = f"roles-{world.tag}@h232b.example"
        world.app_user(a, email, permission="admin:members:manage")
        uid = world.identity(email)
        world.membership(a, uid)
        world.membership(b, uid)
        async with tenant_engine_scope(_URL):
            in_a = await access_mod.resolve_access_in_org(email.upper(), a)
            in_b = await access_mod.resolve_access_in_org(email, b)
            # The email-keyed read gives the roles of org A, wherever it runs.
            by_email = await access_mod.resolve_access(email, use_cache=False)
        assert in_a.is_active and in_a.has("admin:members:manage")
        assert not in_b.is_active and not in_b.has("admin:members:manage")
        assert by_email.has("admin:members:manage")


@_DB_GATE
class TestFlagOff:
    """``IDENTITY_CUTOVER`` off: the ``app_user`` row, as before H-232b."""

    async def test_the_app_user_org_passes_and_another_org_does_not(
        self, world, monkeypatch,
    ) -> None:
        monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
        a, b = world.org("a"), world.org("b")
        email = f"off-{world.tag}@h232b.example"
        world.app_user(a, email, permission="admin:members:manage")
        # A shadow membership in B. Flag OFF never reads it.
        world.membership(b, world.identity(email))
        async with tenant_engine_scope(_URL):
            _uid, org = await access_mod.resolve_identity(email)
            assert org == a, "flag OFF: the old one-org read still answers A"
            assert await access_mod.is_active_member(email.upper(), a) is True
            assert await access_mod.is_active_member(email, b) is False
            in_a = await access_mod.resolve_access_in_org(email, a)
            in_b = await access_mod.resolve_access_in_org(email, b)
        assert in_a.is_active and in_a.has("admin:members:manage")
        assert not in_b.is_active

    async def test_a_suspended_app_user_is_refused(self, world, monkeypatch) -> None:
        monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
        a = world.org("a")
        email = f"susp-{world.tag}@h232b.example"
        world.app_user(a, email, status="suspended", permission="admin:members:manage")
        async with tenant_engine_scope(_URL):
            assert await access_mod.is_active_member(email, a) is False
            assert (await access_mod.resolve_access_in_org(email, a)).is_active is False


# ── R8 under phase-4 RLS, as the non-privileged app role ────────────────────

@_DB_GATE
def test_both_reads_work_under_phase_4_rls_as_the_app_role(
    promoted, monkeypatch,  # noqa: F811
) -> None:
    """``app_user`` is FORCE-RLS'd on this catalog. H-232's unbound flag-OFF
    read found no row here. The bound reads find the row of the org they ask
    about, and no row of another org."""
    from acb_common import db as acb_db

    email = promoted.owner_email      # app_user in org A, seeded by the fixture
    with promoted.admin_engine.begin() as c:
        uid = str(c.execute(
            text("INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
                 "ON CONFLICT (lower(email)) DO UPDATE SET email = EXCLUDED.email "
                 "RETURNING id"), {"e": email}).scalar_one())
        for org in (promoted.org_a, promoted.org_b):
            c.execute(
                text("INSERT INTO org_membership (organization_id, user_id, status) "
                     "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active') "
                     "ON CONFLICT DO NOTHING"), {"o": org, "u": uid})

    monkeypatch.setenv("DATABASE_URL", promoted.app_url.render_as_string(hide_password=False))
    monkeypatch.setattr(acb_db, "_ENGINE", None)
    monkeypatch.setattr(acb_db, "_SESSION_FACTORY", None)
    monkeypatch.setattr(access_mod, "_tables_missing", False)

    async def _outcomes(flag: str) -> dict[str, object]:
        monkeypatch.setenv("IDENTITY_CUTOVER", flag)
        try:
            return {
                "member_a": await access_mod.is_active_member(email, promoted.org_a),
                "member_b": await access_mod.is_active_member(email, promoted.org_b),
                "active_a": (await access_mod.resolve_access_in_org(
                    email, promoted.org_a)).is_active,
                "active_b": (await access_mod.resolve_access_in_org(
                    email, promoted.org_b)).is_active,
            }
        finally:
            if acb_db._ENGINE is not None:
                await acb_db._ENGINE.dispose()
                acb_db._ENGINE = None
                acb_db._SESSION_FACTORY = None

    import asyncio

    # Flag OFF: the app_user row, which is in org A only.
    assert asyncio.run(_outcomes("")) == {
        "member_a": True, "member_b": False, "active_a": True, "active_b": False,
    }
    # Flag ON: the memberships in A and B. The roles stay in org A.
    assert asyncio.run(_outcomes("1")) == {
        "member_a": True, "member_b": True, "active_a": True, "active_b": False,
    }
