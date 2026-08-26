"""The operator session, and the real actor in the audit log — **CP-12b**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §4.3 · §4.4 ·
§8.1 done-whens 7-12 · **D64**.

Two subjects, both of which `engineering_practice.md` §4 marks for mutation
testing: **auth** and **the audit trail**. R8 binds — the session read is a
JOIN whose freshness is the whole security property, and a hermetic fake would
agree with any JOIN it was handed.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_operator_session.py
"""
from __future__ import annotations

import os
import pathlib
import re
import uuid
from datetime import UTC, datetime, timedelta

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


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def eng():
    e = create_engine(_URL, future=True)
    yield e
    e.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", SHARED)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    from customer_console.main import app
    return TestClient(app)


def _operator(eng, *, role: str = "admin", status: str = "active") -> tuple[str, str]:
    """Insert one operator. Returns ``(id, email)``."""
    email = f"op-{uuid.uuid4().hex[:8]}@fracktal.in"
    with eng.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO operator (email, role, status) "
                "VALUES (:e, :r, :s) RETURNING id"
            ),
            {"e": email, "r": role, "s": status},
        ).first()
    return str(row[0]), email


def _session(
    eng,
    operator_id: str,
    *,
    expires_in: timedelta = timedelta(hours=12),
    last_seen_ago: timedelta = timedelta(0),
    revoked: bool = False,
) -> str:
    """Mint a session straight into the table. Returns the TOKEN."""
    from customer_console import operator_sessions, store

    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        session_id = store.operator_session_insert(
            conn,
            operator_id=operator_id,
            prefix=issued.prefix,
            key_hash=issued.key_hash,
            expires_at=now + expires_in,
        )
        conn.execute(
            text(
                "UPDATE operator_session SET last_seen_at = :seen "
                "WHERE id = CAST(:id AS UUID)"
            ),
            {"seen": now - last_seen_ago, "id": session_id},
        )
        if revoked:
            store.operator_session_revoke(conn, session_id)
    return issued.token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Done-when 7: the stored row cannot reconstruct the token ────────────────


def test_the_database_holds_no_usable_session_token(eng):
    """Done-when 7. A disclosure of `operator_session` hands over nothing.

    This is the whole reason the scheme exists: today the cookie holds the
    shared passphrase ITSELF, so a disclosed cookie is a disclosed passphrase
    for the entire team (spec §2, F2).
    """
    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id)

    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT prefix, key_hash FROM operator_session "
                 "WHERE operator_id = CAST(:o AS UUID)"),
            {"o": operator_id},
        ).mappings().first()

    _, _, secret = token.rpartition("_")
    assert secret, "the token has no secret segment"
    assert secret not in row["key_hash"]
    assert secret not in row["prefix"]
    # And the hash is not reversible by the obvious mistake of storing it raw.
    assert row["key_hash"] != secret
    assert re.fullmatch(r"[0-9a-f]{64}", row["key_hash"]), "not a sha256 digest"


def test_a_session_token_carries_its_own_scheme(eng):
    """`cc_sess_` — a fourth VALUE in `keys.py`, not a fourth implementation."""
    from customer_console.keys import is_deployment_key, is_operator_session

    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id)

    assert token.startswith("cc_sess_")
    prefix = token.rsplit("_", 1)[0]
    assert is_operator_session(prefix)
    assert not is_deployment_key(prefix), "a session must not read as a box"


# ── Done-whens 8-10: the session stops working ──────────────────────────────


def test_a_live_session_is_admitted(client, eng):
    """The positive case, so the refusals below are not passing vacuously."""
    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id)

    assert client.get("/orgs", headers=_auth(token)).status_code == 200


def test_a_session_past_its_absolute_expiry_is_refused(client, eng):
    """Done-when 8."""
    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id, expires_in=timedelta(seconds=-1))

    assert client.get("/orgs", headers=_auth(token)).status_code == 401


def test_a_session_idle_past_the_timeout_is_refused(client, eng):
    """Done-when 9. Absolute time left, but nothing has happened on it."""
    operator_id, _ = _operator(eng)
    token = _session(
        eng, operator_id,
        expires_in=timedelta(hours=12),
        last_seen_ago=timedelta(hours=2),
    )

    assert client.get("/orgs", headers=_auth(token)).status_code == 401


def test_a_revoked_session_is_refused_on_the_very_next_request(client, eng):
    """Done-when 10 — no restart, no cache wait.

    A revocation that took a deploy to apply would not be a revocation.
    """
    from customer_console import store

    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id)
    assert client.get("/orgs", headers=_auth(token)).status_code == 200

    with eng.begin() as conn:
        revoked = store.operator_sessions_revoke_all(conn, operator_id)
    assert revoked == 1

    assert client.get("/orgs", headers=_auth(token)).status_code == 401


def test_deactivating_an_operator_kills_a_live_session_at_once(client, eng):
    """The status is re-read per request, not trusted from sign-in time.

    ⚠️ This is spec §2's **F5** — *"removing one person means changing the
    secret for everybody"*. It is the reason the whole scheme exists.
    """
    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id)
    assert client.get("/orgs", headers=_auth(token)).status_code == 200

    with eng.begin() as conn:
        conn.execute(
            text("UPDATE operator SET status = 'deactivated' "
                 "WHERE id = CAST(:o AS UUID)"),
            {"o": operator_id},
        )

    assert client.get("/orgs", headers=_auth(token)).status_code == 401


def test_an_unknown_or_forged_token_is_refused(client, eng):
    """Four bad credentials, and none of them may be told apart."""
    operator_id, _ = _operator(eng)
    good = _session(eng, operator_id)
    prefix = good.rsplit("_", 1)[0]

    bodies = set()
    for bad in (
        "cc_sess_deadbeefcafe_notarealsecret",   # unknown prefix
        f"{prefix}_wrongsecretwrongsecret",      # right prefix, wrong secret
        "cc_sess_malformed",                     # unsplittable
        "cc_sess__",                             # empty segments
    ):
        r = client.get("/orgs", headers=_auth(bad))
        assert r.status_code == 401, bad
        bodies.add(r.text)

    assert len(bodies) == 1, f"the 401 leaks which guess was closer: {bodies}"


def test_the_idle_clock_moves_only_for_an_admitted_request(client, eng):
    """A holder of a revoked token must not keep the row warm."""
    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id, last_seen_ago=timedelta(minutes=30))

    def seen() -> datetime:
        with eng.begin() as conn:
            return conn.execute(
                text("SELECT last_seen_at FROM operator_session "
                     "WHERE operator_id = CAST(:o AS UUID)"),
                {"o": operator_id},
            ).scalar_one()

    before = seen()
    assert client.get("/orgs", headers=_auth(token)).status_code == 200
    admitted = seen()
    assert admitted > before, "an admitted request did not move the idle clock"

    with eng.begin() as conn:
        conn.execute(
            text("UPDATE operator SET status = 'suspended' "
                 "WHERE id = CAST(:o AS UUID)"),
            {"o": operator_id},
        )
    assert client.get("/orgs", headers=_auth(token)).status_code == 401
    assert seen() == admitted, "a REFUSED request moved the idle clock"


# ── The shared token keeps working, and keeps naming nobody ─────────────────


def test_the_shared_operator_token_still_opens_the_door(client):
    """CP-12b adds a scheme. It removes none. CP-12g is the cutover."""
    assert client.get("/orgs", headers=_auth(SHARED)).status_code == 200


def test_an_unconfigured_shared_token_still_503s(client, monkeypatch):
    """The fail-closed posture is untouched for the shared credential."""
    monkeypatch.delenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", raising=False)
    assert client.get("/orgs", headers=_auth("anything")).status_code == 503


def test_a_session_is_admitted_even_with_no_shared_token_configured(
    client, eng, monkeypatch
):
    """⚠️ A session is a COMPLETE credential, not a fallback.

    A box that has retired the shared passphrase (CP-12g) must still admit
    real operators. If the session path consulted the shared token, the
    cutover would lock the whole team out.
    """
    operator_id, _ = _operator(eng)
    token = _session(eng, operator_id)

    monkeypatch.delenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", raising=False)
    assert client.get("/orgs", headers=_auth(token)).status_code == 200


# ── Done-whens 11-12: the audit log names the PERSON ────────────────────────


def _org(eng) -> tuple[str, str]:
    """One organization to act on. Returns ``(id, slug)``."""
    slug = f"acme-{uuid.uuid4().hex[:8]}"
    with eng.begin() as conn:
        row = conn.execute(
            text("INSERT INTO organization (slug, name) "
                 "VALUES (:s, :n) RETURNING id"),
            {"s": slug, "n": "Acme"},
        ).first()
    return str(row[0]), slug


def _actors(eng, org_id: str, action: str) -> list[str]:
    with eng.begin() as conn:
        return [
            r[0]
            for r in conn.execute(
                text("SELECT actor FROM control_audit "
                     "WHERE organization_id = CAST(:o AS UUID) "
                     "AND action = :a ORDER BY created_at"),
                {"o": org_id, "a": action},
            )
        ]


def test_a_credit_grant_records_the_person_who_made_it(client, eng):
    """Done-when 11. The log answers **who**, which today it cannot."""
    operator_id, email = _operator(eng)
    token = _session(eng, operator_id)
    org_id, slug = _org(eng)

    r = client.post(
        "/credits/grant",
        headers=_auth(token),
        json={"org_slug": slug, "credits": "10", "reason": "manual"},
    )
    assert r.status_code == 200, r.text
    assert _actors(eng, org_id, "credits.grant") == [email]


def test_the_same_grant_under_the_shared_token_names_nobody(client, eng):
    """The contrast that makes the point, and the reason a reader looks twice.

    A row carrying the literal `operator` is now a row that came from the
    SHARED credential — scripts today, break-glass after CP-12e.
    """
    from customer_console.auth import SHARED_TOKEN_ACTOR

    org_id, slug = _org(eng)
    r = client.post(
        "/credits/grant",
        headers=_auth(SHARED),
        json={"org_slug": slug, "credits": "10", "reason": "manual"},
    )
    assert r.status_code == 200, r.text
    assert _actors(eng, org_id, "credits.grant") == [SHARED_TOKEN_ACTOR]


def test_a_key_issue_records_the_person(client, eng):
    """A second action, so done-when 11 is not one route's accident."""
    operator_id, email = _operator(eng)
    token = _session(eng, operator_id)
    org_id, slug = _org(eng)

    # `/keys` is an `elevated` row in the §5 matrix (CP-12e), so the admin
    # opens a window first. The SUBJECT here is still the audit actor.
    assert client.post(
        "/operators/elevate", headers=_auth(token),
        json={"reason": "issuing a key for a test"},
    ).status_code == 200

    r = client.post(
        "/keys",
        headers=_auth(token),
        json={"org_slug": slug, "label": "test"},
    )
    assert r.status_code == 200, r.text
    assert _actors(eng, org_id, "key.issue") == [email]


def test_no_operator_gated_audit_call_hardcodes_the_shared_actor(client):
    """Done-when 12, as a SOURCE fence rather than one test per route.

    A behavioural test only covers the routes somebody remembered to write
    one for. This reads every `_audit` call inside a route whose gate is
    `staff: Operator` and fails when one does not name the actor — so a new
    operator route that forgets is caught at the moment it is added, which
    is what R7 asks a rule to do.
    """
    src = (
        _ROOT / "apps" / "services" / "customer_console" / "customer_console"
        / "main.py"
    ).read_text(encoding="utf-8")

    offenders = []
    for match in re.finditer(r"^def (\w+)\(", src, re.MULTILINE):
        start = match.start()
        end = src.find("\n@app.", start)
        body = src[start: end if end != -1 else len(src)]
        if "staff: Operator" not in body:
            continue
        for call in re.finditer(r"_audit\(", body):
            tail = body[call.start(): call.start() + 600]
            if "actor=staff.actor" not in tail.split(")\n")[0] + ")":
                offenders.append(match.group(1))

    assert not offenders, (
        "these operator-gated routes audit without naming the actor, so their "
        f"rows would say 'operator' for a signed-in person: {sorted(set(offenders))}"
    )


def test_the_provision_arm_now_names_the_person(client):
    """CP-12b's known gap, CLOSED by CP-12c.

    `/orgs/provision` is a dual-arm door. Its operator arm used to record the
    shared actor because no identity was in scope. CP-12c's `auth._stash` puts
    the identity on the request, so the arm names the person and done-when 12
    is fully met.
    """
    src = (
        _ROOT / "apps" / "services" / "customer_console" / "customer_console"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert 'actor="operator" if caller is None else "deployment")' not in src, (
        "the provision arm went back to the shared actor"
    )
    assert "CP-12c CLOSES CP-12b's known gap" in src


# ── The R8 gate cannot silently disarm ──────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_operator_session.py" in workflow, (
        "this suite is not in pr-check.yml's R8 skip-guard list — without the "
        "entry it can skip in CI while the job reports green"
    )


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    spec = (
        _ROOT / "project-docs" / "specs" / "operator_identity_and_access.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_operator_session.py" in spec, (
        "operator_identity_and_access.md §11 does not name this suite"
    )
