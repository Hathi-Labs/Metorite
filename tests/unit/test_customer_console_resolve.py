"""CP-2b — sign-in resolve under the FOURTH scheme, the deployment key.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2b (done-when clauses
1-5, 8, 9, 10, 12) · §5.2 · ``user_management_contract.md`` R11 · D32.4.

**Scope of this file: the Customer Console half.** CP-2b has two halves and only
one is built here — the Console answers a deployment key; the *deployment-side*
caller (``packages/acb_auth``, the resolve cache, the TTL/staleness pair, the
tenant projection columns) is the other half and is deliberately absent, so
clauses 6, 7, 11 and the deployment-side halves of 8 and 9 have no fences here.

The property the whole ticket rests on, stated once:

    **A deployment key resolves an email if and only if that email holds a
    membership in an organization whose CURRENT ``org_placement.deployment_id``
    equals the key's deployment.** Placement is the boundary, not the
    organization — which is why re-placing an org onto this deployment makes the
    *same* key start resolving it. That is not a leak; it is the definition of
    pooled.

⚠️ **R8, and it is not decoration.** Every clause below reads or writes: a
CITEXT unique index, a four-table join whose predicate IS the acceptance
criterion, a partial unique index on seats, an ``ON CONFLICT`` upsert. Hermetic
fakes agree with whatever SQL they are handed — five live bugs shipped green
that way — so this suite skips loudly rather than passing without a server, and
``pr-check.yml``'s hand-list names it so CI cannot go back to skipping it while
reporting green (CP-3's finding).

⚠️ **Fixtures INSERT ``deployment`` and ``org_placement`` rows directly, and
that is correct.** Nothing in the tree writes them today — ``POST
/orgs/provision`` does not, and teaching it to is a NON-goal of this ticket. A
fixture that went through a route that does not exist would be testing a route
that does not exist.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

from customer_console import auth, store
from customer_console.keys import ENV_DEPLOYMENT, mint_key, split_key
from customer_console.main import app
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

TOKEN = "test-operator-token"
INTERNAL = "test-internal-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
INT = {"Authorization": f"Bearer {INTERNAL}"}


# ── The exempt set, named ────────────────────────────────────────────────────

#: The ONE route on this service that authenticates nothing, excluded **by
#: name** rather than by a rule that could widen silently.
#:
#: ``/health`` is liveness: *"Deliberately unauthenticated and deliberately says
#: nothing"* (``main.py``). Adding a second entry here is an edit somebody has
#: to justify in review — which is the whole reason this is a constant and not
#: a predicate.
#:
#: ⚠️ FastAPI's four documentation routes (``/openapi.json``, ``/docs``,
#: ``/docs/oauth2-redirect``, ``/redoc``) are NOT listed and must not be: they
#: are plain ``starlette.routing.Route``, not ``APIRoute``, so filtering on
#: ``APIRoute`` excludes them without a list to maintain. *(That they serve the
#: full OpenAPI schema of a cross-tenant service to anyone who can reach the
#: port is a real finding — pre-existing, unrelated to CP-2b, and a matter for
#: the board rather than a line of this suite.)*
_UNAUTHENTICATED_ROUTES = frozenset({"/health"})


def _api_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _guarded_calls() -> list[tuple[str, str]]:
    """``(method, path)`` for every APIRoute a credential must open."""
    calls: list[tuple[str, str]] = []
    for route in _api_routes():
        if route.path in _UNAUTHENTICATED_ROUTES:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            calls.append((method, route.path))
    return sorted(calls)


_GUARDED_CALLS = _guarded_calls()


def _authenticates(dependant) -> bool:
    """Does this route (or any of its dependencies) authenticate somebody?

    Reads ``auth.AUTHENTICATING_DEPENDENCIES`` rather than a hand-list here: a
    second list would be a mirror, and a mirror goes stale and then lies.
    """
    if dependant.call in auth.AUTHENTICATING_DEPENDENCIES:
        return True
    return any(_authenticates(sub) for sub in dependant.dependencies)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    """Build the schema — and replay the ladder, proving 006 is idempotent.

    Applied TWICE on purpose. Every migration in the ladder is written
    ``IF NOT EXISTS``; a claim of replay-safety that holds only by reading the
    DDL is unenforceable, and the deploy replays the whole ladder every time.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    return TestClient(app)


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


def _new_org(client, prefix: str = "org", *, core_seats: int = 3) -> str:
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"owner@{slug}.example",
        "core_seats": core_seats,
    })
    assert r.status_code == 200, r.text
    return slug


def _org_id(client, slug: str) -> str:
    r = client.get(f"/billing/summary?org_slug={slug}", headers=OP)
    assert r.status_code == 200, r.text
    return r.json()["organization_id"]


def _new_deployment(db, label: str = "dep") -> str:
    with db.begin() as c:
        return str(c.execute(
            text("INSERT INTO deployment (label, base_url) "
                 "VALUES (:l, :u) RETURNING id"),
            {"l": f"{label}-{uuid.uuid4().hex[:8]}",
             "u": "https://box.example"},
        ).scalar_one())


def _place(db, *, org_id: str, deployment_id: str, target: str | None = None):
    """Place an org on a deployment. Re-placing MOVES it — that is the point.

    ``org_placement`` is keyed on ``organization_id``, so an org is placed in
    exactly one place at a time and "current placement" is a fact rather than a
    query with an ordering.
    """
    with db.begin() as c:
        c.execute(
            text(
                """
                INSERT INTO org_placement
                    (organization_id, deployment_id, database_target)
                VALUES (:o, :d, :t)
                ON CONFLICT (organization_id) DO UPDATE
                    SET deployment_id = EXCLUDED.deployment_id,
                        database_target = EXCLUDED.database_target,
                        moved_at = now()
                """
            ),
            {"o": org_id, "d": deployment_id, "t": target},
        )


def _depl_key(db, *, deployment_id: str,
              capabilities: list[str] | None = None) -> str:
    """Mint a deployment key straight into the table.

    ``mint_key(env="depl")`` + a store helper, because CP-2b specifies **no**
    HTTP route that issues one: no done-when asks for it, and issuing a real
    ``cc_depl_`` key into a live deployment is OWNER-GATE (§8 gate 7).
    """
    minted = mint_key(env=ENV_DEPLOYMENT)
    with db.begin() as c:
        store.issue_deployment_key(
            c, deployment_id=deployment_id, prefix=minted.prefix,
            key_hash=minted.key_hash, label="fixture", created_by="fixture",
            capabilities=capabilities,
        )
    return minted.token


def _member(db, *, org_id: str, email: str, role: str = "member") -> str:
    with db.begin() as c:
        identity = store.ensure_identity(c, email=email)
        c.execute(
            text(
                """
                INSERT INTO org_membership
                    (organization_id, user_identity_id, role, status, joined_at)
                VALUES (:o, :i, :r, 'active', now())
                ON CONFLICT (organization_id, user_identity_id) DO NOTHING
                """
            ),
            {"o": org_id, "i": identity, "r": role},
        )
    return identity


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _resolve(client, token: str, **body):
    return client.post("/registry/resolve", headers=_headers(token), json=body)


@pytest.fixture
def box(client, db):
    """One deployment, one key, one org placed on it, one member of that org."""
    deployment = _new_deployment(db, "box")
    slug = _new_org(client, "acme")
    org_id = _org_id(client, slug)
    _place(db, org_id=org_id, deployment_id=deployment, target="acb")
    email = f"staff-{uuid.uuid4().hex[:8]}@acme.example"
    _member(db, org_id=org_id, email=email)
    return {
        "deployment_id": deployment,
        "key": _depl_key(db, deployment_id=deployment),
        "slug": slug,
        "org_id": org_id,
        "email": email,
    }


# ══ Clause 1 — the key reaches resolve and nothing else ══════════════════════

class TestTheKeyReachesOneEndpoint:
    @pytest.mark.parametrize(
        "method,path", _GUARDED_CALLS,
        ids=[f"{m}:{p}" for m, p in _GUARDED_CALLS],
    )
    def test_a_deployment_key_reaches_resolve_and_nothing_else(
        self, client, box, method, path
    ):
        """Parametrised over the LIVE route table, so tomorrow's route is covered.

        Not a hand-list of endpoints: a list would have to be remembered, and
        the failure mode of forgetting is a new cross-tenant surface silently
        opened to every deployment we have ever issued a key to.
        """
        r = client.request(method, path, headers=_headers(box["key"]), json={})

        if path == "/registry/resolve":
            # Accepted at the door. (The empty body is then a 422 from the
            # model — which is the point: authentication happened.)
            assert r.status_code != 401, r.text
        else:
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}"

    def test_the_unauthenticated_route_set_is_exactly_health(self):
        """Derived from the app, not asserted against a copy of itself.

        A route added without a credential shows up here as an extra member of
        the set, which fails — rather than being covered by a parametrisation
        that skipped it because somebody added it to an exempt list.
        """
        open_routes = {
            r.path for r in _api_routes() if not _authenticates(r.dependant)
        }
        assert open_routes == set(_UNAUTHENTICATED_ROUTES)

    def test_a_deployment_key_without_the_resolve_capability_is_refused(
        self, client, db, box
    ):
        """The capability set is enforced, and it is enforced as a dependency."""
        crippled = _depl_key(
            db, deployment_id=box["deployment_id"], capabilities=["heartbeat"],
        )
        r = _resolve(client, crippled, email=box["email"])
        # 403, not 401: the credential is valid, it simply may not do this.
        assert r.status_code == 403, r.text
        assert "resolve" in r.json()["detail"]

    def test_a_revoked_deployment_key_401s(self, client, db, box):
        """``revoked_at IS NULL`` is resolved in SQL, not left to a caller."""
        # Via `split_key`, never `rsplit`: the secret is `token_urlsafe` output
        # and its alphabet INCLUDES `_`, so an rsplit lands inside it — the
        # trap that function's docstring records, and that made a sibling test
        # in `test_customer_console_key_auth.py` pass for the wrong reason.
        parsed = split_key(box["key"])
        assert parsed is not None
        assert _resolve(client, box["key"],
                        email=box["email"]).status_code == 200

        with db.begin() as c:
            revoked = c.execute(
                text("UPDATE deployment_key SET revoked_at = now() "
                     "WHERE prefix = :p RETURNING id"),
                {"p": parsed[0]},
            ).first()
        assert revoked is not None, "the prefix did not match a row"

        assert _resolve(client, box["key"],
                        email=box["email"]).status_code == 401


# ══ Clause 2 — the org is the answer, never the assertion ════════════════════

class TestTheOrgIsTheAnswer:
    def test_a_deployment_key_may_not_name_an_org(self, client, db, box):
        """400, never ignored — an ignored field is a caller who believes it worked."""
        r = _resolve(client, box["key"],
                     email=box["email"], org_slug=box["slug"])

        assert r.status_code == 400, r.text
        # And the refusal is total: no seat was allocated on the way to it.
        with db.begin() as c:
            seats = c.execute(
                text("SELECT count(*) FROM seat_assignment sa "
                     "JOIN user_identity ui ON ui.id = sa.user_identity_id "
                     "WHERE ui.email = :e AND sa.released_at IS NULL"),
                {"e": box["email"]},
            ).scalar_one()
        assert seats == 0

    def test_naming_someone_elses_org_is_the_same_400(self, client, db, box):
        """Refused on SHAPE, before the slug is looked at.

        Otherwise the status code would answer "does this org exist" for every
        slug an attacker cares to try — a cross-org existence oracle wearing a
        validation error's clothes.
        """
        stranger = _new_org(client, "stranger")
        r = _resolve(client, box["key"], email=box["email"], org_slug=stranger)
        assert r.status_code == 400
        missing = _resolve(client, box["key"],
                           email=box["email"], org_slug="no-such-org-at-all")
        assert (missing.status_code, missing.json()) == (400, r.json())

    def test_the_org_comes_from_membership_not_from_the_body(
        self, client, db, box
    ):
        """Two orgs on one box; the answer is the one the person belongs to."""
        other = _new_org(client, "other")
        _place(db, org_id=_org_id(client, other),
               deployment_id=box["deployment_id"])

        answer = _resolve(client, box["key"], email=box["email"]).json()

        assert [o["slug"] for o in answer["organizations"]] == [box["slug"]]


# ══ Clause 3 — the two schemes do not substitute for each other ══════════════

class TestSchemeSeparation:
    def test_the_two_schemes_do_not_substitute_for_each_other(
        self, client, db, box
    ):
        """The NEW pairs only — the other three are already pinned.

        ``test_customer_console_key_auth.py::
        test_the_three_schemes_do_not_open_each_other`` owns operator, internal
        and organization-key. Restating that matrix here would be a
        parallel copy, and a copy is what goes stale and then lies (root
        ``CLAUDE.md`` §5). This is its sibling, covering only what CP-2b added:
        the deployment key against the other three, and each of them against
        resolve's deployment arm.
        """
        depl = box["key"]

        # A deployment key is not an operator token — at resolve or anywhere.
        assert client.post("/registry/resolve", headers=_headers(depl), json={
            "org_slug": box["slug"], "email": box["email"]}).status_code == 400
        assert client.get(f"/billing/summary?org_slug={box['slug']}",
                          headers=_headers(depl)).status_code == 401
        # ...nor an internal token, nor an organization key.
        assert client.post("/usage/record", headers=_headers(depl), json={
            "organization_id": box["org_id"],
            "request_id": f"r-{uuid.uuid4().hex}"}).status_code == 401
        assert client.get("/me", headers=_headers(depl)).status_code == 401

        # And nothing else opens the deployment arm. An operator token reaching
        # resolve gets the OPERATOR arm (400 for a missing org_slug), never a
        # deployment answer; the internal token and an org key get nothing.
        assert client.post("/registry/resolve", headers=OP,
                           json={"email": box["email"]}).status_code == 400
        assert client.post("/registry/resolve", headers=INT,
                           json={"email": box["email"]}).status_code == 401
        org_key = client.post("/keys", headers=OP,
                              json={"org_slug": box["slug"]}).json()["token"]
        assert client.post("/registry/resolve", headers=_headers(org_key),
                           json={"email": box["email"]}).status_code == 401

    def test_a_key_shaped_operator_token_is_still_not_a_deployment_key(
        self, client, db, box, monkeypatch
    ):
        """Guards the DISPATCH, not the comparison.

        A staff token that happens to be shaped like a deployment key must not
        be parsed as one — the sibling of
        ``test_a_key_shaped_operator_token_is_still_not_a_key`` one scheme over.
        The scheme is decided by what the credential IS, never by trying each
        comparison until one passes.
        """
        shaped = "cc_depl_deadbeefcafe_supersecret"
        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", shaped)
        c = TestClient(app)

        r = c.post("/registry/resolve", headers=_headers(shaped),
                   json={"email": box["email"]})
        assert r.status_code == 401

    def test_the_operator_calls_still_pass_unchanged(self, client, box):
        """Clause 3's regression, in one line here and in full in the suites it names.

        ``test_customer_console_api.py:121,134,144`` and
        ``test_customer_console_lifecycle.py:173,189`` are the real regression
        set and they run unchanged; this asserts the seam itself — an operator
        token still reaches resolve with a body naming its subject.
        """
        r = client.post("/registry/resolve", headers=OP, json={
            "org_slug": box["slug"], "email": box["email"]})
        assert r.status_code == 200, r.text
        assert r.json()["organization_id"] == box["org_id"]


# ══ Clause 4 — placement is the boundary, not the organization ═══════════════

class TestPlacementIsTheBoundary:
    def test_a_deployment_key_sees_only_the_orgs_placed_on_it(
        self, client, db
    ):
        a, b = _new_deployment(db, "a"), _new_deployment(db, "b")
        x, y = _new_org(client, "x"), _new_org(client, "y")
        _place(db, org_id=_org_id(client, x), deployment_id=a)
        _place(db, org_id=_org_id(client, y), deployment_id=b)
        email = f"person-{uuid.uuid4().hex[:8]}@y.example"
        _member(db, org_id=_org_id(client, y), email=email)

        answer = _resolve(client, _depl_key(db, deployment_id=a), email=email)

        assert answer.status_code == 200
        assert answer.json() == {"organizations": []}

    def test_a_pooled_deployment_resolves_every_org_placed_on_it(
        self, client, db
    ):
        """The same key, the same email, a different placement — and it resolves.

        Not a leak: the definition of pooled. The claim under test is an *iff*
        on placement, so the case that would falsify a naive reading of clause 4
        is the case that must pass.
        """
        a, b = _new_deployment(db, "a"), _new_deployment(db, "b")
        y = _new_org(client, "y")
        y_id = _org_id(client, y)
        _place(db, org_id=y_id, deployment_id=b)
        email = f"person-{uuid.uuid4().hex[:8]}@y.example"
        _member(db, org_id=y_id, email=email)
        key_a = _depl_key(db, deployment_id=a)

        assert _resolve(client, key_a, email=email).json() == {
            "organizations": []}

        _place(db, org_id=y_id, deployment_id=a)  # the operator moves them

        answer = _resolve(client, key_a, email=email).json()
        assert [o["slug"] for o in answer["organizations"]] == [y]

    def test_an_org_with_no_placement_row_is_placed_nowhere(self, client, db):
        """Nowhere is not here. The join is inner on purpose."""
        deployment = _new_deployment(db, "lonely")
        orphan = _new_org(client, "orphan")
        email = f"person-{uuid.uuid4().hex[:8]}@orphan.example"
        _member(db, org_id=_org_id(client, orphan), email=email)

        r = _resolve(client, _depl_key(db, deployment_id=deployment),
                     email=email)
        assert r.json() == {"organizations": []}


# ══ Clause 5 — no cross-org existence oracle ═════════════════════════════════

class TestNoExistenceOracle:
    def test_the_invisible_cases_are_indistinguishable(self, client, db):
        """Three causes, ONE answer — status, body and bytes.

        A distinguishable negative *is* a cross-tenant read: it tells a
        deployment whether an address it does not serve exists somewhere else
        in our customer base. CP-3 learned this from ``recorded:false``; this
        applies it before a verifier has to find it again.
        """
        here = _new_deployment(db, "here")
        elsewhere = _new_deployment(db, "elsewhere")
        key = _depl_key(db, deployment_id=here)

        # (a) known to the Console, member of nothing.
        known_no_membership = f"nomember-{uuid.uuid4().hex[:8]}@x.example"
        with db.begin() as c:
            store.ensure_identity(c, email=known_no_membership)

        # (b) a member — of an organization placed on ANOTHER deployment.
        other_org = _new_org(client, "other")
        _place(db, org_id=_org_id(client, other_org),
               deployment_id=elsewhere)
        placed_elsewhere = f"elsewhere-{uuid.uuid4().hex[:8]}@x.example"
        _member(db, org_id=_org_id(client, other_org),
                email=placed_elsewhere)

        # (c) an address the Console has never seen at all.
        unknown = f"ghost-{uuid.uuid4().hex[:8]}@x.example"

        answers = [
            _resolve(client, key, email=e)
            for e in (known_no_membership, placed_elsewhere, unknown)
        ]

        assert {a.status_code for a in answers} == {200}
        assert {a.content for a in answers} == {b'{"organizations":[]}'}

    def test_a_dead_org_placed_here_is_named_not_hidden(self, client, db, box):
        """The one deliberate exception to the invisible set.

        This deployment already serves that customer, so the state reveals
        nothing it does not have — and it needs the state to refuse correctly.
        """
        client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": box["slug"], "target": "cancelled"})
        client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": box["slug"], "target": "deleted"})

        r = _resolve(client, box["key"], email=box["email"])

        assert r.status_code == 403
        assert r.json() == {"detail": "organization is deleted"}

    def test_a_suspended_org_placed_here_still_signs_in_and_says_so(
        self, client, db, box
    ):
        """``capabilities_of`` decides — this ticket does not mint a second copy.

        ``deleted`` is the only state with ``can_sign_in=False``; ``suspended``
        keeps the door open deliberately (a customer who cannot log in cannot
        pay you) and the answer carries the state so the box can lock features.
        """
        client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": box["slug"], "target": "suspended"})

        answer = _resolve(client, box["key"], email=box["email"])

        assert answer.status_code == 200
        assert answer.json()["organizations"][0]["status"] == "suspended"

    def test_a_dead_org_does_not_block_a_live_one_on_the_same_box(
        self, client, db, box
    ):
        """The partition rule, stated: dead orgs are removed, not fatal.

        403 only when NOTHING admissible remains. Refusing the whole sign-in
        because some unrelated organization the person also belongs to was
        deleted would deny a paying customer's staff for another customer's
        lifecycle.
        """
        live = _new_org(client, "live")
        live_id = _org_id(client, live)
        _place(db, org_id=live_id, deployment_id=box["deployment_id"])
        _member(db, org_id=live_id, email=box["email"])
        client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": box["slug"], "target": "cancelled"})
        client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": box["slug"], "target": "deleted"})

        answer = _resolve(client, box["key"], email=box["email"])

        assert answer.status_code == 200
        assert [o["slug"] for o in answer.json()["organizations"]] == [live]


# ══ Clause 8 (Console half) — idempotent, one row ════════════════════════════

class TestIdempotence:
    def test_resolving_five_times_writes_one_console_identity_row(
        self, client, db, box
    ):
        """Resolve runs on EVERY sign-in. Five of them are still one person.

        The Console half of clause 8 only: the ``resolved_at`` clock and the
        ``lower(email)`` match belong to migration 159's projection, which is
        the deployment half and is held.
        """
        for _ in range(5):
            assert _resolve(client, box["key"],
                            email=box["email"]).status_code == 200
        # And the case-varied form is the SAME human — CITEXT, 001:110.
        assert _resolve(client, box["key"],
                        email=box["email"].upper()).status_code == 200

        with db.begin() as c:
            identities = c.execute(
                text("SELECT count(*) FROM user_identity WHERE email = :e"),
                {"e": box["email"]},
            ).scalar_one()
            memberships = c.execute(
                text("SELECT count(*) FROM org_membership m "
                     "JOIN user_identity ui ON ui.id = m.user_identity_id "
                     "WHERE ui.email = :e"),
                {"e": box["email"]},
            ).scalar_one()
        assert (identities, memberships) == (1, 1)


# ══ Clause 9 / 12 — seats and response shape ═════════════════════════════════

class TestSeatSemantics:
    def test_a_deployment_key_signing_in_five_times_burns_one_seat(
        self, client, db, box
    ):
        """The drift bug, under the new scheme.

        The operator equivalent is pinned at ``test_customer_console_api.py:131``;
        this is the deployment-key equivalent, added rather than assumed,
        because "the scheme is transparent to seats" is exactly the assumption
        that would be worth checking after it had cost a customer their cap.
        """
        outcomes = [
            _resolve(client, box["key"], email=box["email"]).json(
            )["organizations"][0]["seat"]
            for _ in range(5)
        ]

        assert outcomes[0] == "allocated"
        assert set(outcomes[1:]) == {"already_held"}

        summary = client.get(f"/billing/summary?org_slug={box['slug']}",
                             headers=OP).json()
        core = next(s for s in summary["seats"] if s["plan_slug"] == "core")
        assert core["assigned"] == 2  # the owner, and this member once

    def test_the_deployment_at_cap_returns_the_shipped_buy_more_payload(
        self, client, db
    ):
        """409, byte-compatible with the seats path. Never an auto-upgrade.

        A caller that learned this payload from the operator scheme must not
        have to relearn it, and auto-upgrading is how a customer discovers a
        charge they did not authorise (D19.3).
        """
        deployment = _new_deployment(db, "full")
        slug = _new_org(client, "full", core_seats=1)  # the owner fills it
        org_id = _org_id(client, slug)
        _place(db, org_id=org_id, deployment_id=deployment)
        email = f"late-{uuid.uuid4().hex[:8]}@full.example"
        _member(db, org_id=org_id, email=email)

        r = _resolve(client, _depl_key(db, deployment_id=deployment),
                     email=email)

        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["reason"] == "seat_cap_exceeded"
        assert set(detail["buy_more"]) == {
            "plan_slug", "purchased", "assigned",
            "additional_seats_required", "price_inr",
        }
        assert detail["buy_more"]["additional_seats_required"] == 1
        assert detail["buy_more"]["price_inr"] == "600.00"

        # Refused means refused: nothing was written on the way out.
        with db.begin() as c:
            assigned = c.execute(
                text("SELECT count(*) FROM seat_assignment sa "
                     "JOIN user_identity ui ON ui.id = sa.user_identity_id "
                     "WHERE ui.email = :e AND sa.released_at IS NULL"),
                {"e": email},
            ).scalar_one()
        assert assigned == 0

    def test_a_multi_org_resolve_allocates_no_seat(self, client, db, box):
        """Several visible orgs → the full list, and NOTHING allocated.

        Allocating a seat in every organization a person can see would bill an
        admin for a login they did not make. Choosing among them is the
        chooser, a named non-goal — the Console reports, the box decides.
        """
        second = _new_org(client, "second")
        second_id = _org_id(client, second)
        _place(db, org_id=second_id, deployment_id=box["deployment_id"])
        _member(db, org_id=second_id, email=box["email"])

        answer = _resolve(client, box["key"], email=box["email"])

        assert answer.status_code == 200
        body = answer.json()
        assert len(body["organizations"]) == 2
        assert {o["seat"] for o in body["organizations"]} == {"not_allocated"}

        with db.begin() as c:
            assigned = c.execute(
                text("SELECT count(*) FROM seat_assignment sa "
                     "JOIN user_identity ui ON ui.id = sa.user_identity_id "
                     "WHERE ui.email = :e AND sa.released_at IS NULL"),
                {"e": box["email"]},
            ).scalar_one()
        assert assigned == 0


class TestResponseShape:
    def test_the_deployment_answer_never_carries_a_role(self, client, db, box):
        """``org_membership.role`` is registry/billing vocabulary and stays there.

        The tenant's permission vocabulary is ``org_role`` plus the ladder
        ``acb_auth/access.py`` resolves; copying one into the other creates a
        second grant vocabulary, forbidden by name (D12, ``CLAUDE.md`` §5). The
        member here is deliberately an **owner**, so a leak would have
        something conspicuous to leak.
        """
        with db.begin() as c:
            c.execute(
                text("UPDATE org_membership SET role = 'admin' "
                     "WHERE organization_id = :o AND user_identity_id = ("
                     "  SELECT id FROM user_identity WHERE email = :e)"),
                {"o": box["org_id"], "e": box["email"]},
            )

        body = _resolve(client, box["key"], email=box["email"]).json()

        assert set(body) == {"identity_id", "organizations"}
        assert set(body["organizations"][0]) == {
            "organization_id", "slug", "placement", "status", "seat"}
        assert "admin" not in str(body)

    def test_the_empty_answer_carries_nothing_but_an_empty_list(
        self, client, db
    ):
        """No ``identity_id`` — that key alone would be the existence oracle."""
        deployment = _new_deployment(db, "empty")
        r = _resolve(client, _depl_key(db, deployment_id=deployment),
                     email=f"nobody-{uuid.uuid4().hex[:8]}@x.example")

        assert r.status_code == 200
        assert r.json() == {"organizations": []}

    def test_the_placement_target_is_reported_and_tolerates_null(
        self, client, db
    ):
        """``org_placement.database_target`` is nullable with no default (001:104).

        NULL is a legitimate answer — day one every row resolves to the same
        target and the INDIRECTION is the point — so it reaches the wire as
        ``null`` rather than as an invented string.
        """
        deployment = _new_deployment(db, "target")
        key = _depl_key(db, deployment_id=deployment)
        named, blank = _new_org(client, "named"), _new_org(client, "blank")
        _place(db, org_id=_org_id(client, named), deployment_id=deployment,
               target="acb_shard_2")
        _place(db, org_id=_org_id(client, blank), deployment_id=deployment,
               target=None)
        one = f"one-{uuid.uuid4().hex[:8]}@x.example"
        two = f"two-{uuid.uuid4().hex[:8]}@x.example"
        _member(db, org_id=_org_id(client, named), email=one)
        _member(db, org_id=_org_id(client, blank), email=two)

        assert _resolve(client, key, email=one).json()[
            "organizations"][0]["placement"] == "acb_shard_2"
        assert _resolve(client, key, email=two).json()[
            "organizations"][0]["placement"] is None

    def test_the_operator_response_shape_is_unchanged(self, client, box):
        """One endpoint, two schemes, two shapes — and the shipped one moves not at all."""
        body = client.post("/registry/resolve", headers=OP, json={
            "org_slug": box["slug"], "email": box["email"]}).json()

        assert set(body) == {
            "identity_id", "organization_id", "role", "status", "seats"}
        assert body["role"] == "member"
        assert body["seats"] == ["core"]


# ══ R7 — the two fences this implementation itself introduces ════════════════

class TestTheRelaxationsAreFenced:
    def test_an_operator_without_an_org_slug_is_refused(self, client, box):
        """``org_slug`` became optional so the deployment arm could refuse it.

        The cost of that relaxation is that the operator's own subject is now
        optional at the model, so the handler has to refuse its absence — and
        **400, not pydantic's 422**, because 422 would mean the model was still
        doing the work and the relaxation had not actually happened.
        """
        r = client.post("/registry/resolve", headers=OP,
                        json={"email": box["email"]})

        assert r.status_code == 400, r.text
        assert "org_slug" in str(r.json()["detail"])

    def test_resolve_still_fails_closed_when_the_operator_token_is_unconfigured(
        self, client, db, box, monkeypatch
    ):
        """503, not 401 and certainly not 200.

        Existing coverage (``test_customer_console_api.py:81``) pins this for
        ``/billing/summary``; resolve now routes its operator arm through a
        dispatcher, and a dispatcher that swallowed the unconfigured case would
        make this the one door that opens on a box where the rest of the
        service fails closed — D33.1, exactly.
        """
        monkeypatch.delenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", raising=False)
        c = TestClient(app)

        assert c.post("/registry/resolve", headers=OP, json={
            "org_slug": box["slug"], "email": box["email"]}).status_code == 503

        # ...and the failure is arm-scoped: a deployment key does not depend on
        # a token it never presents.
        assert c.post("/registry/resolve", headers=_headers(box["key"]),
                      json={"email": box["email"]}).status_code == 200
