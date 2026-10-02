"""EM-T3d — the connected-member count (D-EM-4: counts, never mail).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.3, subsection
"EM-T3d — pre-approval and the connected-member count".

``GET /email/admin/connections`` is the one email route that reads every
mailbox row of an organization. Three things limit it, and this file fences
each one:

* **The gate.** ``require_permission("admin:members:read")`` on the route, on
  top of the router's ``feature:email``. A member without the admin
  permission gets 403 and the handler body does not run.
* **The tenant.** The handler takes ``user`` and nothing else (R5). With no
  organization it answers 403 before it opens a session. Its SQL filters on
  ``organization_id`` on top of FORCE RLS.
* **The answer.** ``OrgConnectionCounts`` holds seven integers. A field of any
  other type fails the model fence, so no address, member or account id can
  ride out on it.

R7 fences named here:

* ``email-admin-count-gate``: ``TestTheGate`` (hermetic).
* ``email-admin-count-shape``: ``TestTheShape`` (hermetic).
* ``email-admin-count-rls``: ``TestTheCountsUnderForceRls`` (R8, the
  phase-4-promoted two-org catalog, as ``acb_app_h3rls``).

Run (real Postgres for the R8 class)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_org_connection_counts.py -v -rs
"""
from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from acb_auth import get_current_user
from acb_auth.deps import require_authenticated
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import email as email_pkg
from gateway.routes.email.transport import accounts
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

PATH = "/email/admin/connections"

#: The seven fields of the answer, and nothing else (scope item 5).
SEVEN = (
    "members", "mailboxes", "microsoft", "gmail", "imap",
    "sync_errors", "first_sync_pending",
)

ORG = str(uuid.uuid4())

ADMIN = UserContext(
    email="admin@customer.example",
    role=UserRole.EMPLOYEE,
    organization_id=ORG,
    access=EffectiveAccess(
        role_granted=frozenset({"feature:email", "admin:members:read"})),
)
MEMBER = UserContext(
    email="member@customer.example",
    role=UserRole.EMPLOYEE,
    organization_id=ORG,
    access=EffectiveAccess(role_granted=frozenset({"feature:email"})),
)
ADMIN_NO_EMAIL_APP = UserContext(
    email="admin2@customer.example",
    role=UserRole.EMPLOYEE,
    organization_id=ORG,
    access=EffectiveAccess(role_granted=frozenset({"admin:members:read"})),
)
ADMIN_NO_ORG = UserContext(
    email="admin3@customer.example",
    role=UserRole.EMPLOYEE,
    organization_id=None,
    access=EffectiveAccess(
        role_granted=frozenset({"feature:email", "admin:members:read"})),
)


def _client(user: UserContext) -> TestClient:
    """The real email router, mounted as ``gateway/main.py`` mounts it."""
    app = FastAPI(dependencies=[require_authenticated(public=())])
    app.include_router(email_pkg.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class SessionSpy:
    """Stands in for ``_tenant_session``. Each call is one opened session."""

    def __init__(self, row: object | None = None) -> None:
        self.row = row
        self.opened: list[tuple] = []
        self.statements: list[tuple[str, dict]] = []

    def __call__(self, *args, **kwargs):
        self.opened.append((args, kwargs))
        spy = self

        class _Result:
            def one(self):
                return spy.row

        class _Session:
            async def execute(self, stmt, params=None):
                spy.statements.append((str(stmt), dict(params or {})))
                return _Result()

        @asynccontextmanager
        async def _cm():
            yield _Session()

        return _cm()


@pytest.fixture
def spy(monkeypatch) -> SessionSpy:
    s = SessionSpy(row=SimpleNamespace(
        members=2, mailboxes=3, microsoft=1, gmail=1, imap=1,
        sync_errors=1, first_sync_pending=1))
    monkeypatch.setattr(accounts, "_tenant_session", s)
    return s


# ── The gate (hermetic) ─────────────────────────────────────────────────────


class TestTheGate:

    def test_an_admin_with_an_organization_reads_the_counts(self, spy):
        """The positive control: the spy records a session here, so the
        refusals below are not empty because the spy saw nothing."""
        r = _client(ADMIN).get(PATH)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "members": 2, "mailboxes": 3, "microsoft": 1, "gmail": 1,
            "imap": 1, "sync_errors": 1, "first_sync_pending": 1,
        }
        assert len(spy.opened) == 1
        (sql, params), = spy.statements
        assert params == {"org": ORG}, (
            "the organization must come from the session's user, and from "
            "nowhere else (R11)"
        )
        assert "organization_id = CAST(:org AS uuid)" in sql

    def test_a_member_without_the_admin_permission_gets_403(self, spy):
        r = _client(MEMBER).get(PATH)
        assert r.status_code == 403, r.text
        assert "admin:members:read" in r.json()["detail"]
        assert spy.opened == [], "the handler body ran for a non-admin"

    def test_an_admin_without_the_email_feature_gets_403(self, spy):
        """The route sits under the router's ``feature:email`` gate too."""
        r = _client(ADMIN_NO_EMAIL_APP).get(PATH)
        assert r.status_code == 403, r.text
        assert "feature:email" in r.json()["detail"]
        assert spy.opened == []

    def test_an_admin_with_no_organization_gets_403_and_no_session(self, spy):
        r = _client(ADMIN_NO_ORG).get(PATH)
        assert r.status_code == 403, r.text
        assert spy.opened == [], (
            "the handler opened a session with no organization to bind"
        )

    def test_a_caller_with_no_identity_gets_401(self, spy):
        anon = UserContext(email=None, role=UserRole.EMPLOYEE)
        r = _client(anon).get(PATH)
        assert r.status_code == 401, r.text
        assert spy.opened == []


# ── The shape (hermetic) ────────────────────────────────────────────────────


def non_int_fields(model: type) -> list[str]:
    """Each field of ``model`` whose type is not exactly ``int``.

    ``is not int`` and never ``issubclass``: a ``bool`` is an ``int`` to
    Python, and a flag is not a count."""
    return sorted(name for name, field in model.model_fields.items()
                  if field.annotation is not int)


class TestTheShape:

    def test_the_model_holds_the_seven_counts(self):
        assert tuple(accounts.OrgConnectionCounts.model_fields) == SEVEN

    def test_every_field_is_an_int(self):
        assert non_int_fields(accounts.OrgConnectionCounts) == []

    def test_the_model_fence_fails_on_a_field_of_another_type(self):
        class Leaky(accounts.OrgConnectionCounts):
            email_address: str = ""

        class Flagged(accounts.OrgConnectionCounts):
            approved: bool = False

        assert non_int_fields(Leaky) == ["email_address"]
        assert non_int_fields(Flagged) == ["approved"]

    def test_the_handler_takes_user_and_nothing_else(self):
        """R5: no tenant and no identity can come from request input, because
        the handler has no parameter that could carry one."""
        params = inspect.signature(accounts.org_connection_counts).parameters
        assert list(params) == ["user"]

    def test_the_route_is_registered_once_as_a_get(self):
        routes = [r for r in email_pkg.router.routes
                  if getattr(r, "path", None) == PATH]
        assert len(routes) == 1
        assert routes[0].methods == {"GET"}
        assert routes[0].endpoint is accounts.org_connection_counts


# ── R8: the counts under FORCE RLS ──────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _seed(admin_engine, org: str, rows: list[dict]) -> None:
    """Seed as the superuser, which escapes the policies under test."""
    with admin_engine.begin() as c:
        for row in rows:
            c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, sync_status, sync_error, "
                "initial_sync_done, organization_id) "
                "VALUES (:u, :p, :m, 'x', :s, :e, :d, CAST(:o AS uuid))"),
                {"u": row["user"], "p": row["provider"], "m": row["address"],
                 "s": row["status"], "e": row.get("error"),
                 "d": row["done"], "o": org})


def _admin_of(org: str) -> UserContext:
    return UserContext(
        email=f"admin-{_tag()}@em-t3d.test",
        role=UserRole.EMPLOYEE,
        organization_id=org,
        access=EffectiveAccess(
            role_granted=frozenset({"feature:email", "admin:members:read"})),
    )


@pytest.fixture
def two_orgs(promoted):  # noqa: F811
    """Org A: two members, three mailboxes. Org B: three members, four.

    * A member with two mailboxes counts once in ``members`` (alice in A).
    * Case does not split a member (carol in B is seeded twice, in two cases).
    * A ``sync_error`` holds the address, so a leak of the text would show.
    """
    t = _tag()
    a = {
        "alice": f"alice-{t}@a.em-t3d.test",
        "bob": f"bob-{t}@a.em-t3d.test",
    }
    b = {
        "carol": f"carol-{t}@b.em-t3d.test",
        "dave": f"dave-{t}@b.em-t3d.test",
        "erin": f"erin-{t}@b.em-t3d.test",
    }
    rows_a = [
        {"user": a["alice"], "provider": "microsoft", "address": a["alice"],
         "status": "error", "error": f"token refused for {a['alice']}",
         "done": True},
        {"user": a["alice"], "provider": "gmail",
         "address": f"alice.home-{t}@gmail.em-t3d.test",
         "status": "idle", "done": False},
        {"user": a["bob"], "provider": "imap", "address": a["bob"],
         "status": "syncing", "done": True},
    ]
    rows_b = [
        {"user": b["carol"], "provider": "microsoft", "address": b["carol"],
         "status": "error", "error": f"mailbox {b['carol']} not found",
         "done": True},
        {"user": b["carol"].upper(), "provider": "microsoft",
         "address": f"carol.shared-{t}@b.em-t3d.test",
         "status": "idle", "done": False},
        {"user": b["dave"], "provider": "gmail", "address": b["dave"],
         "status": "idle", "done": True},
        {"user": b["erin"], "provider": "imap", "address": b["erin"],
         "status": "error", "error": "IMAP login failed", "done": True},
    ]
    _seed(promoted.admin_engine, promoted.org_a, rows_a)
    _seed(promoted.admin_engine, promoted.org_b, rows_b)
    seeded = [r["address"] for r in rows_a + rows_b] + [
        r["user"] for r in rows_a + rows_b]
    try:
        yield SimpleNamespace(
            expected_a={"members": 2, "mailboxes": 3, "microsoft": 1,
                        "gmail": 1, "imap": 1, "sync_errors": 1,
                        "first_sync_pending": 1},
            expected_b={"members": 3, "mailboxes": 4, "microsoft": 2,
                        "gmail": 1, "imap": 1, "sync_errors": 2,
                        "first_sync_pending": 1},
            seeded=seeded,
        )
    finally:
        with promoted.admin_engine.begin() as c:
            c.execute(text(
                "DELETE FROM email_accounts WHERE organization_id IN "
                "(CAST(:a AS uuid), CAST(:b AS uuid))"),
                {"a": promoted.org_a, "b": promoted.org_b})


async def _counts_as_admin_of(promoted, *, bound: str, org: str):  # noqa: F811
    """Run the REAL handler as an admin of ``org``, with ``bound`` as the
    tenant of the request."""
    app_dsn = promoted.app_url.render_as_string(hide_password=False)
    token = bind_tenant(bound)
    try:
        async with tenant_engine_scope(app_dsn):
            return await accounts.org_connection_counts(user=_admin_of(org))
    finally:
        release_tenant(token)


@_DB_GATE
class TestTheCountsUnderForceRls:

    @pytest.mark.parametrize("which", ["a", "b"])
    async def test_an_admin_reads_the_counts_of_their_own_org_only(
        self, promoted, app_engine, two_orgs, which,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        org = promoted.org_a if which == "a" else promoted.org_b
        expected = two_orgs.expected_a if which == "a" else two_orgs.expected_b
        got = await _counts_as_admin_of(promoted, bound=org, org=org)
        assert got.model_dump() == expected, (
            f"the counts of org {which.upper()} were wrong, or held rows of "
            "the other organization"
        )

    @pytest.mark.parametrize("which", ["a", "b"])
    async def test_the_answer_holds_no_address(
        self, promoted, app_engine, two_orgs, which,  # noqa: F811
    ):
        org = promoted.org_a if which == "a" else promoted.org_b
        got = await _counts_as_admin_of(promoted, bound=org, org=org)
        wire = got.model_dump_json()
        assert "@" not in wire
        for value in two_orgs.seeded:
            assert value.lower() not in wire.lower()
        assert set(got.model_dump()) == set(SEVEN)

    async def test_the_tenant_predicate_holds_when_the_binding_disagrees(
        self, promoted, app_engine, two_orgs,  # noqa: F811
    ):
        """RLS binds org A, and the user names org B. The SQL predicate and
        the policy then agree on no row, so every count is zero. Without the
        predicate, this read returns the counts of org A."""
        got = await _counts_as_admin_of(
            promoted, bound=promoted.org_a, org=promoted.org_b)
        assert got.model_dump() == dict.fromkeys(SEVEN, 0)
