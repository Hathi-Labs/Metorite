"""The front door — WS-31 **CP-12f2**, which closes **F8**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §2 F8 · §4.1 ·
§8.1 done-whens 1 to 6 · **D64.1**.

⚠️ Done-whens 1 to 6 were written for CP-12a and were **unreachable until this
slice**: nothing called ``operators.admit``. They are exercised here, through
the route, for the first time.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_operator_signin.py
"""
from __future__ import annotations

import os
import pathlib
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")
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

_ROOT = pathlib.Path(__file__).resolve().parents[2]
SHARED = "test-operator-token"
INTERNAL = "test-internal-token"
TENANT = "11111111-2222-3333-4444-555555555555"
DOMAIN = "fracktal.in"


# ⚠️ **This suite gets its OWN database, and that is not belt and braces.**
#
# The bootstrap is a COUNT over the whole `operator` table, so testing it
# through the route means the registry must really be empty when the route
# runs. The idiom the sibling suites use — a connection whose transaction is
# rolled back — cannot do that here, because the route opens its own
# connection and never sees an uncommitted delete.
#
# The first version of this suite deleted the rows for real. It passed alone
# and broke FOUR other suites in the same session, because their operators
# and sessions vanished underneath them. A scratch database is the honest
# fix: still a real Postgres, so R8 holds, and no shared row is touched.


@pytest.fixture(scope="module", autouse=True)
def _scratch_db():
    """Create a database for this module alone, and drop it afterwards."""
    from customer_console.db import get_engine

    name = f"cc_signin_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_URL, future=True,
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))

    scratch_url = _URL.rsplit("/", 1)[0] + "/" + name
    eng = create_engine(scratch_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()

    previous = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL")
    os.environ["CUSTOMER_CONSOLE_DATABASE_URL"] = scratch_url
    get_engine.cache_clear()
    try:
        yield scratch_url
    finally:
        get_engine().dispose()
        if previous is None:
            os.environ.pop("CUSTOMER_CONSOLE_DATABASE_URL", None)
        else:
            os.environ["CUSTOMER_CONSOLE_DATABASE_URL"] = previous
        get_engine.cache_clear()
        with admin.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"),
                {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def eng(_scratch_db):
    e = create_engine(_scratch_db, future=True)
    yield e
    e.dispose()


@pytest.fixture
def client(monkeypatch, _scratch_db):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", SHARED)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("OPERATOR_SUPABASE_URL", "https://p.supabase.co")
    monkeypatch.setenv("OPERATOR_SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("OPERATOR_ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("OPERATOR_STAFF_DOMAINS", DOMAIN)
    monkeypatch.delenv("OPERATOR_BOOTSTRAP_EMAIL", raising=False)
    from customer_console.main import app
    return TestClient(app)

class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _payload(email: str, *, tid: str | None = TENANT, provider: str = "azure",
             verified: bool = True, subject: str | None = None,
             extra_identity: dict | None = None) -> dict:
    """A Supabase ``/auth/v1/user`` body, in the shape the module reads."""
    identity_data: dict = {"email": email, "email_verified": verified}
    if tid is not None:
        identity_data["tid"] = tid
    identities = [{"provider": provider, "identity_data": identity_data}]
    if extra_identity:
        identities.append(extra_identity)
    return {
        "id": subject or str(uuid.uuid4()),
        "email": email,
        "email_confirmed_at": "2026-08-27T00:00:00Z" if verified else None,
        "app_metadata": {"provider": provider,
                         "providers": [i["provider"] for i in identities]},
        "user_metadata": {},
        "identities": identities,
    }


@pytest.fixture
def issuer(monkeypatch):
    """Stand in for Supabase. The REAL `introspect` still runs against it."""
    box: dict = {"response": None, "calls": []}

    def fake_get(url, **kwargs):
        box["calls"].append((url, kwargs))
        r = box["response"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr("httpx.get", fake_get)

    def serve(payload, status: int = 200):
        box["response"] = (payload if isinstance(payload, Exception)
                           else _Response(status, payload))
        return box

    box["serve"] = serve
    return box


def _register(eng, email: str, role: str = "editor",
              status: str = "active") -> str:
    with eng.begin() as conn:
        return str(conn.execute(
            text("INSERT INTO operator (email, role, status) "
                 "VALUES (:e, :r, :s) RETURNING id"),
            {"e": email, "r": role, "s": status},
        ).scalar())


def _email() -> str:
    return f"op-{uuid.uuid4().hex[:10]}@{DOMAIN}"


def _signin(client, token="supabase-access-token"):
    return client.post("/operators/session", json={"access_token": token})


def _auth(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


# ── F8 itself: the door exists, and it is the only one ─────────────────────


def test_the_console_declares_a_sign_in_route():
    """⚠️ **This is F8, as a test.**

    CP-12a to CP-12e verified a session that no route could issue. The whole
    identity stack was unreachable, and every operator route answered to the
    shared break-glass token alone. If this assertion ever fails again, the
    door has been removed and the stack is inert once more.
    """
    from customer_console.main import app

    doors = [
        r for r in app.routes
        if getattr(r, "path", "") == "/operators/session"
        and "POST" in getattr(r, "methods", set())
    ]
    assert len(doors) == 1


def test_the_session_routes_are_not_swallowed_by_the_path_parameter():
    """The CP-12e bug, which this slice would have repeated.

    `DELETE /operators/{operator_id}` was already declared. A `DELETE
    /operators/session` after it matches with `operator_id="session"` and
    deactivates nothing while answering as though it did.
    """
    from customer_console.main import app

    order = [r.path for r in app.routes
             if getattr(r, "path", "").startswith("/operators")]
    assert order.index("/operators/session") < order.index(
        "/operators/{operator_id}"
    )


def test_the_ungated_door_is_declared_in_the_repo_s_own_fence():
    """⚠️ **The fence for this already existed, and I nearly built a second.**

    `test_customer_console_resolve.py` derives the unauthenticated route set
    from FastAPI's dependency graph, which is stronger than any source scan:
    it reads `auth.AUTHENTICATING_DEPENDENCIES` rather than a hand-list.
    Adding `POST /operators/session` there is an edit somebody must justify
    in review, which is the point of that constant.

    This test only asserts the entry is present and named. The property
    itself is enforced over there — one seam, not two.
    """
    src = (_ROOT / "tests" / "unit"
           / "test_customer_console_resolve.py").read_text(encoding="utf-8")
    assert '_UNAUTHENTICATED_ROUTES = frozenset({"/health", "/operators/session"})' in src, (
        "the sign-in route is no longer declared in the repo's "
        "unauthenticated-route fence"
    )

# ── The happy path, end to end ─────────────────────────────────────────────


def test_a_registered_operator_signs_in_and_the_session_works(
    client, eng, issuer
):
    """The whole point: a real name, holding a real session, doing real work."""
    email = _email()
    _register(eng, email, role="admin")
    issuer["serve"](_payload(email))

    r = _signin(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["operator"]["email"] == email
    assert body["operator"]["role"] == "admin"
    assert body["token"].startswith("cc_sess_")

    # The minted token authenticates a subsequent request.
    listed = client.get("/operators", headers=_auth(body["token"]))
    assert listed.status_code == 200, listed.text


def test_the_sign_in_names_the_person_in_the_audit_log(client, eng, issuer):
    """Done-when 11's real proof. F3 said the log could not say who."""
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    assert _signin(client).status_code == 200

    with eng.begin() as conn:
        actors = [x[0] for x in conn.execute(
            text("SELECT actor FROM control_audit WHERE action = "
                 "'operator.signin' AND actor = :e"), {"e": email})]
    assert actors == [email]


def test_signing_in_records_the_directory_subject(client, eng, issuer):
    """`has_signed_in` on the operator list becomes true, and stays accurate."""
    email = _email()
    _register(eng, email)
    subject = str(uuid.uuid4())
    issuer["serve"](_payload(email, subject=subject))
    assert _signin(client).status_code == 200

    with eng.begin() as conn:
        got = conn.execute(
            text("SELECT directory_subject FROM operator WHERE email = :e"),
            {"e": email},
        ).scalar()
    assert got == subject


def test_the_issuer_is_asked_with_the_token_and_the_project_key(
    client, eng, issuer
):
    """The anon key selects the project. The operator's token authenticates."""
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    _signin(client, token="the-access-token")

    url, kwargs = issuer["calls"][-1]
    assert url == "https://p.supabase.co/auth/v1/user"
    assert kwargs["headers"]["Authorization"] == "Bearer the-access-token"
    assert kwargs["headers"]["apikey"] == "anon-key"


# ── Done-whens 1 to 4, now reachable ───────────────────────────────────────


def test_a_foreign_directory_is_refused(client, eng, issuer):
    """Done-when 1. Check 1 — the identity did not come from our directory."""
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email, tid=str(uuid.uuid4())))
    assert _signin(client).status_code == 403


def test_an_email_outside_the_named_domains_is_refused(client, eng, issuer):
    """Done-when 2. Check 2 — right directory, wrong domain."""
    email = f"op-{uuid.uuid4().hex[:8]}@not-ours.example"
    _register(eng, email)
    issuer["serve"](_payload(email))
    assert _signin(client).status_code == 403


def test_an_unregistered_person_is_refused(client, eng, issuer):
    """Done-when 3. Checks 1 and 2 pass. There is no `operator` row."""
    issuer["serve"](_payload(_email()))
    assert _signin(client).status_code == 403


@pytest.mark.parametrize("status", ["suspended", "deactivated"])
def test_a_sealed_operator_cannot_sign_back_in(client, eng, issuer, status):
    """Done-when 4. Removing somebody has to actually remove them."""
    email = _email()
    _register(eng, email, status=status)
    issuer["serve"](_payload(email))
    assert _signin(client).status_code == 403


def test_every_refusal_is_byte_identical(client, eng, issuer):
    """⚠️ Done-when 15's rule, applied at the door.

    A door that says *"good token, wrong directory"* has told an attacker
    which half to work on next.
    """
    stranger = _email()
    foreign = _email()
    _register(eng, foreign)
    sealed = _email()
    _register(eng, sealed, status="suspended")

    issuer["serve"](_payload(stranger))
    a = _signin(client)
    issuer["serve"](_payload(foreign, tid=str(uuid.uuid4())))
    b = _signin(client)
    issuer["serve"](_payload(sealed))
    c = _signin(client)

    assert a.status_code == b.status_code == c.status_code == 403
    assert a.text == b.text == c.text


# ── Done-when 5: unconfigured fails CLOSED ─────────────────────────────────


@pytest.mark.parametrize("missing", [
    "OPERATOR_SUPABASE_URL",
    "OPERATOR_SUPABASE_ANON_KEY",
])
def test_an_unconfigured_issuer_is_503_and_admits_nobody(
    client, eng, issuer, monkeypatch, missing
):
    """Done-when 5, for the issuer half. 503, exactly as `staff.ts` does."""
    monkeypatch.delenv(missing, raising=False)
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    assert _signin(client).status_code == 503


@pytest.mark.parametrize("missing", [
    "OPERATOR_ENTRA_TENANT_ID",
    "OPERATOR_STAFF_DOMAINS",
])
def test_an_unpinned_directory_is_503_not_403(
    client, eng, issuer, monkeypatch, missing
):
    """Done-when 5. A box nobody configured and a person we refuse are
    DIFFERENT incidents, and the status code has to tell them apart."""
    monkeypatch.delenv(missing, raising=False)
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    assert _signin(client).status_code == 503


def test_an_unreachable_issuer_refuses_rather_than_admits(
    client, eng, issuer
):
    """⚠️ The failure mode that would be a full bypass.

    "Fail open when the network is bad" turns a Supabase outage into an
    authentication bypass on a cross-customer console.
    """
    email = _email()
    _register(eng, email)
    issuer["serve"](RuntimeError("connection reset"))
    assert _signin(client).status_code == 401


@pytest.mark.parametrize("status", [400, 401, 403, 500, 502])
def test_an_unhappy_issuer_answer_is_a_refusal(client, eng, issuer, status):
    email = _email()
    _register(eng, email)
    issuer["serve"]({"error": "nope"}, status)
    assert _signin(client).status_code == 401


def test_an_unreadable_body_is_a_refusal(client, eng, issuer):
    email = _email()
    _register(eng, email)
    issuer["serve"](_Response(200, ValueError("not json")))
    issuer["response"] = _Response(200, ValueError("not json"))
    assert _signin(client).status_code == 401


# ── What the payload reader must never do ──────────────────────────────────


def test_a_non_microsoft_sign_in_is_refused(client, eng, issuer):
    """A password or magic-link user is a real Supabase user, and not staff."""
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email, provider="email", tid=None))
    assert _signin(client).status_code == 401


def test_an_unproven_email_is_refused(client, eng, issuer):
    """Defence in depth behind the directory check, and cheap."""
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email, verified=False))
    assert _signin(client).status_code == 401


def test_a_linked_microsoft_identity_does_not_admit_another_provider(
    client, eng, issuer
):
    """⚠️ **A real bypass, found by mutation testing, and now closed.**

    A Supabase account can carry several linked identities. The first
    version of this module asked whether a Microsoft identity was AMONG
    them. It is not the same question as whether the sign-in came from one.

    The attack it allowed: a colleague links a personal account to theirs.
    Somebody takes that personal account. They sign in through it, Supabase
    still lists the linked Microsoft identity carrying our tenant id, and
    the old gate admitted them — reaching a cross-customer console WITHOUT
    passing through Entra, which is the whole thing D64.1 pinned the
    directory to prevent.
    """
    from customer_console import operator_signin

    email = _email()
    linked = _payload(email, provider="github", tid=None, extra_identity={
        "provider": "azure",
        "identity_data": {"email": email, "tid": TENANT,
                          "email_verified": True},
    })
    # `app_metadata.provider` is github, so this sign-in was not Microsoft.
    assert linked["app_metadata"]["provider"] == "github"
    assert "azure" in linked["app_metadata"]["providers"], (
        "the payload must really carry the linked Microsoft identity, or "
        "this test proves nothing"
    )

    with pytest.raises(operator_signin.SigninRejected):
        operator_signin.extract_identity(linked)

    # And the same payload is refused at the door, not just in the unit.
    _register(eng, email)
    issuer["serve"](linked)
    assert _signin(client).status_code == 401


def test_the_same_person_signing_in_through_microsoft_is_admitted(
    client, eng, issuer
):
    """The positive half. Without it, the refusal above proves only that
    something is broken, not that the right thing is refused."""
    email = _email()
    _register(eng, email)
    both = _payload(email, provider="azure", extra_identity={
        "provider": "github",
        "identity_data": {"email": email, "email_verified": True},
    })
    issuer["serve"](both)
    assert _signin(client).status_code == 200

def test_promoted_claims_are_read_only_when_microsoft_stands_alone(client):
    """Some projects copy provider claims up to `app_metadata`.

    Reading them is safe only while Microsoft is the ONLY provider on the
    account, because then there is no other sign-in they could belong to.
    """
    from customer_console import operator_signin

    email = _email()
    payload = _payload(email, tid=None)
    payload["identities"][0]["identity_data"].pop("tid", None)
    payload["app_metadata"] = {"provider": "azure", "providers": ["azure"]}
    payload["app_metadata"]["tid"] = TENANT
    assert operator_signin.extract_identity(payload).tid == TENANT

    payload["app_metadata"]["providers"] = ["azure", "github"]
    assert operator_signin.extract_identity(payload).tid is None


def test_a_payload_with_no_named_sign_in_provider_is_refused(client):
    """⚠️ The linked-provider LIST must never stand in for the sign-in one.

    A payload that lists `providers: ["azure"]` but names no
    `app_metadata.provider` does not say which identity was used. Guessing
    the last entry re-opens the bypass this module closed, so the reader
    refuses instead.
    """
    from customer_console import operator_signin

    email = _email()
    payload = _payload(email)
    payload["app_metadata"] = {"providers": ["azure"]}
    with pytest.raises(operator_signin.SigninRejected):
        operator_signin.extract_identity(payload)

def test_a_missing_tenant_claim_yields_none_and_the_door_refuses(
    client, eng, issuer
):
    """⚠️ **The H-54 unknown, and it fails CLOSED.**

    No live project has confirmed the claim shape yet. A payload this module
    cannot read produces no `tid`, and `admit` refuses on check 1.
    """
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email, tid=None))
    assert _signin(client).status_code == 403


@pytest.mark.parametrize("broken", [
    {},
    {"id": "x"},
    {"email": "a@b.c"},
    {"id": "", "email": "a@b.c"},
    {"id": "x", "email": "no-at-sign"},
    "not-a-dict",
    None,
])
def test_a_payload_the_reader_cannot_understand_is_refused(broken):
    from customer_console import operator_signin

    with pytest.raises(operator_signin.SigninRejected):
        operator_signin.extract_identity(broken)


def test_an_empty_token_never_reaches_the_issuer(issuer):
    from customer_console import operator_signin

    with pytest.raises(operator_signin.SigninRejected):
        operator_signin.introspect("   ")
    assert issuer["calls"] == [], "an empty token was sent to Supabase"


# ── Done-when 6: the one-time bootstrap, through the door ──────────────────


def _empty_registry(eng):
    """Truly empty, in the scratch database only. See `_scratch_db`."""
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM operator_elevation"))
        conn.execute(text("DELETE FROM operator_session"))
        conn.execute(text("UPDATE operator SET added_by = NULL"))
        conn.execute(text("DELETE FROM operator"))
        # The audit trail too, so a later assertion counting sign-in rows
        # measures THIS request rather than every one the module has made.
        conn.execute(text("DELETE FROM control_audit"))


def test_the_first_person_through_the_door_becomes_admin(
    client, eng, issuer, monkeypatch
):
    """Done-when 6. A registry nobody can enter is a console nobody can run."""
    email = _email()
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_EMAIL", email)
    _empty_registry(eng)
    issuer["serve"](_payload(email))

    r = _signin(client)
    assert r.status_code == 200, r.text
    assert r.json()["operator"]["role"] == "admin"


def test_the_bootstrap_does_not_fire_twice(client, eng, issuer, monkeypatch):
    """⚠️ An environment variable that kept working is a back door."""
    first, second = _email(), _email()
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_EMAIL", first)
    _empty_registry(eng)
    issuer["serve"](_payload(first))
    assert _signin(client).status_code == 200

    monkeypatch.setenv("OPERATOR_BOOTSTRAP_EMAIL", second)
    issuer["serve"](_payload(second))
    assert _signin(client).status_code == 403, "the bootstrap fired again"


def test_a_stranger_cannot_consume_the_bootstrap(
    client, eng, issuer, monkeypatch
):
    """⚠️ Only somebody already inside our directory may trigger it.

    A stranger triggering it gains nothing — `admit` still refuses them — but
    it would burn the one-time path before the owner reached it.
    """
    owner = _email()
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_EMAIL", owner)
    _empty_registry(eng)

    issuer["serve"](_payload(_email(), tid=str(uuid.uuid4())))
    assert _signin(client).status_code == 403
    with eng.begin() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM operator")).scalar() == 0, (
            "a foreign directory consumed the one-time bootstrap"
        )

    issuer["serve"](_payload(owner))
    assert _signin(client).status_code == 200


def test_the_bootstrap_is_inert_when_no_email_is_named(
    client, eng, issuer, monkeypatch
):
    monkeypatch.delenv("OPERATOR_BOOTSTRAP_EMAIL", raising=False)
    _empty_registry(eng)
    issuer["serve"](_payload(_email()))
    assert _signin(client).status_code == 403


# ── What mutation testing said these tests were not proving ───────────────


def test_a_valid_looking_body_behind_an_error_status_is_still_refused(
    client, eng, issuer
):
    """⚠️ A mutation survived because every error case also had a junk body.

    The status check and the payload reader were covering for each other, so
    loosening the status check changed nothing. This is the case that
    isolates it: a body that WOULD admit, behind a status that must not.
    """
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email), 403)
    assert _signin(client).status_code == 401


def test_every_401_refusal_is_byte_identical_too(client, eng, issuer):
    """The 403s were compared. The 401s were not, and they leak more.

    These four refusals have four different causes inside the module. A
    caller must not be able to tell which one it hit.
    """
    email = _email()
    _register(eng, email)

    bodies = []
    for payload, status in (
        (_payload(email, provider="email", tid=None), 200),   # provider
        (_payload(email, verified=False), 200),               # unverified
        ({"nonsense": True}, 200),                            # payload
        ({"error": "no"}, 401),                               # issuer
    ):
        issuer["serve"](payload, status)
        r = _signin(client)
        assert r.status_code == 401, r.text
        bodies.append(r.text)
    assert len(set(bodies)) == 1, f"the refusals differ: {set(bodies)}"


def test_a_refused_sign_in_writes_absolutely_nothing(client, eng, issuer,
                                                     monkeypatch):
    """⚠️ **The real protection behind the bootstrap guard, stated once.**

    A mutation that let ANY caller trigger the one-time bootstrap survived,
    and the reason is worth writing down rather than patching over: the
    whole route runs in ONE transaction, and `admit` raising rolls it back.
    The explicit tenant check on the bootstrap is defence in depth, not the
    thing that holds.

    **This is the test that would fail if somebody split the route into two
    transactions**, which is exactly when that guard stops being spare.
    """
    owner = _email()
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_EMAIL", owner)
    _empty_registry(eng)

    # A stranger from a foreign directory. Everything this request touches
    # must be undone: the bootstrap row, and the audit row.
    issuer["serve"](_payload(_email(), tid=str(uuid.uuid4())))
    assert _signin(client).status_code == 403

    with eng.begin() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM operator")).scalar() == 0
        assert conn.execute(
            text("SELECT count(*) FROM operator_session")).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM control_audit WHERE action = "
            "'operator.signin'")).scalar() == 0

# ── Sign-out, which closes F5 ──────────────────────────────────────────────


def test_signing_out_revokes_the_session_immediately(client, eng, issuer):
    """F5 — the interim gate could only ask a browser to forget a cookie."""
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    token = _signin(client).json()["token"]
    assert client.get("/operators", headers=_auth(token)).status_code == 200

    out = client.delete("/operators/session", headers=_auth(token))
    assert out.status_code == 200 and out.json()["revoked"] is True
    assert client.get("/operators", headers=_auth(token)).status_code == 401


def test_signing_out_leaves_other_sessions_alone(client, eng, issuer):
    """Signing out of one browser must not sign the person out of the other."""
    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    first = _signin(client).json()["token"]
    second = _signin(client).json()["token"]

    client.delete("/operators/session", headers=_auth(first))
    assert client.get("/operators", headers=_auth(second)).status_code == 200


def test_break_glass_has_no_session_to_revoke(client):
    """It holds no row. Answering 200 would claim work that never happened."""
    assert client.delete("/operators/session",
                         headers=_auth(SHARED)).status_code == 409


def test_the_session_carries_the_absolute_expiry_it_was_minted_with(
    client, eng, issuer
):
    from customer_console import operator_sessions

    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    body = _signin(client).json()

    expires = datetime.fromisoformat(body["expires_at"])
    expected = datetime.now(UTC) + operator_sessions.absolute_ttl()
    assert abs((expires - expected).total_seconds()) < 120


def test_the_stored_row_cannot_reproduce_the_token(client, eng, issuer):
    """Done-when 7, proved at the door rather than in a unit."""
    from customer_console.keys import split_key

    email = _email()
    _register(eng, email)
    issuer["serve"](_payload(email))
    token = _signin(client).json()["token"]
    parsed = split_key(token)
    assert parsed is not None

    with eng.begin() as conn:
        stored = conn.execute(
            text("SELECT prefix, key_hash FROM operator_session "
                 "WHERE prefix = :p"), {"p": parsed[0]},
        ).mappings().first()
    assert stored is not None
    assert parsed[1] not in stored["key_hash"]
    assert parsed[1] not in stored["prefix"]


# ── The R8 gate cannot silently disarm ─────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_operator_signin.py" in workflow


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    spec = (
        _ROOT / "project-docs" / "specs" / "operator_identity_and_access.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_operator_signin.py" in spec


# ── Who am I — the sidebar's identity row (GET /operators/session) ─────────


def test_whoami_names_the_signed_in_operator_and_their_role(
    client, eng, issuer
):
    """The row exists so an operator knows which name their writes audit
    under, and which matrix rank judges them."""
    email = _email()
    _register(eng, email, role="editor")
    issuer["serve"](_payload(email))
    token = _signin(client).json()["token"]

    r = client.get("/operators/session", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"method": "session", "actor": email, "role": "editor"}


def test_whoami_admits_the_break_glass_token_but_names_NOBODY(client):
    """The shared token carries no person. Claiming one would put a made-up
    name over audit lines the sidebar teaches operators to trust."""
    r = client.get("/operators/session", headers=_auth(SHARED))
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "breakglass"
    assert body["actor"] is None
    assert body["role"] is None


def test_whoami_refuses_the_anonymous(client):
    assert client.get("/operators/session").status_code in (401, 403)


# ── Console-review regressions (2026-08-30): §5's credit rows, the resolve
# matrix hole, and the audit gaps — all need a REAL session, so they live
# beside the sign-in harness. ─────────────────────────────────────────────


def _mkorg(eng, slug: str) -> None:
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO organization (slug, name) VALUES (:s, :n)"),
            {"s": slug, "n": slug})


def test_a_big_grant_is_elevated_admin_AND_window(client, eng, issuer):
    """Spec §5: above the threshold a grant is ELEVATED, not merely admin."""
    email = _email()
    _register(eng, email, role="admin")
    issuer["serve"](_payload(email))
    token = _signin(client).json()["token"]
    slug = f"gr-{uuid.uuid4().hex[:6]}"
    _mkorg(eng, slug)
    big = {"org_slug": slug, "credits": "20000", "reason": "manual",
           "ref": f"NEFT-{slug}"}

    r = client.post("/credits/grant", json=big, headers=_auth(token))
    assert r.status_code == 403, "admin with NO window must be refused"

    assert client.post("/operators/elevate", headers=_auth(token), json={
        "reason": "credit the NEFT transfer (window regression test)",
    }).status_code == 200
    r2 = client.post("/credits/grant", json=big, headers=_auth(token))
    assert r2.status_code == 200, r2.text


def test_activation_credits_obey_the_grant_amount_rules(client, eng, issuer):
    """The OTHER ledger door: an editor refused at /credits/grant must not
    attach the same quantity to a manual activation instead."""
    email = _email()
    _register(eng, email, role="editor")
    issuer["serve"](_payload(email))
    token = _signin(client).json()["token"]
    slug = f"ac-{uuid.uuid4().hex[:6]}"
    _mkorg(eng, slug)
    body = {"org_slug": slug, "plan_slug": "core", "seats": 1,
            "credits": "1000000"}

    r = client.post("/billing/subscriptions/activate", json=body,
                    headers=_auth(token))
    assert r.status_code == 403
    # The refusal unwound the WHOLE activation — no term, no seats.
    with eng.begin() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM org_subscription os "
            "JOIN organization o ON o.id = os.organization_id "
            "WHERE o.slug = :s"), {"s": slug}).scalar_one()
    assert n == 0

    # A negative "credits to ADD" is refused at the model: corrections are
    # /credits/grant's negative deltas, where the amount rules apply.
    r2 = client.post("/billing/subscriptions/activate", headers=_auth(token),
                     json={**body, "credits": "-5"})
    assert r2.status_code == 422


def test_registry_resolve_admits_a_signed_in_operator(client, eng, issuer):
    """The matrix row exists. Unmapped, check_route fails CLOSED, so every
    signed-in operator got 403 here and only break-glass worked — the exact
    state CP-12 exists to end."""
    email = _email()
    _register(eng, email, role="editor")
    issuer["serve"](_payload(email))
    token = _signin(client).json()["token"]
    slug = f"rs-{uuid.uuid4().hex[:6]}"
    _mkorg(eng, slug)

    r = client.post("/registry/resolve", headers=_auth(token),
                    json={"org_slug": slug, "email": f"m@{slug}.com"})
    assert r.status_code != 403, r.text


def test_sign_out_writes_an_audit_row(client, eng, issuer):
    email = _email()
    _register(eng, email, role="viewer")
    issuer["serve"](_payload(email))
    token = _signin(client).json()["token"]
    assert client.delete("/operators/session",
                         headers=_auth(token)).status_code == 200
    with eng.begin() as conn:
        actor = conn.execute(text(
            "SELECT actor FROM control_audit "
            "WHERE action = 'operator.signout' "
            "ORDER BY created_at DESC LIMIT 1")).scalar()
    assert actor == email


def test_re_adding_an_operator_with_another_role_is_409(client, eng, issuer):
    """ON CONFLICT DO NOTHING + a 200 echoing the REQUESTED role audited a
    demotion that never happened. Now the conflict answers 409 with the
    real role; a same-role re-add stays the idempotent 200."""
    existing = _email()
    _register(eng, existing, role="admin")
    acting = _email()
    _register(eng, acting, role="admin")
    issuer["serve"](_payload(acting))
    token = _signin(client).json()["token"]

    r = client.post("/operators", headers=_auth(token),
                    json={"email": existing, "role": "viewer"})
    assert r.status_code == 409
    assert "already exists as 'admin'" in r.json()["detail"]

    r2 = client.post("/operators", headers=_auth(token),
                     json={"email": existing, "role": "admin"})
    assert r2.status_code == 200
    assert r2.json()["role"] == "admin"


def test_the_operator_scheme_names_the_signed_in_person(eng):
    """The three /registry doors audited the literal string "operator" —
    `auth._stash` had put the person on the request and the scheme threw
    it away. Break-glass (actor None) keeps the old label."""
    from types import SimpleNamespace

    from customer_console import main as m

    slug = f"an-{uuid.uuid4().hex[:6]}"
    _mkorg(eng, slug)
    req = m.AdminSchemeRequest(org_slug=slug)
    with eng.begin() as conn:
        named = SimpleNamespace(state=SimpleNamespace(
            staff=SimpleNamespace(actor="alice@fracktal.in")))
        _, actor = m._admin_scheme_for_operator(conn, req, named)
        assert actor == "alice@fracktal.in"

        breakglass = SimpleNamespace(state=SimpleNamespace(
            staff=SimpleNamespace(actor=None)))
        _, actor2 = m._admin_scheme_for_operator(conn, req, breakglass)
        assert actor2 == "operator"
