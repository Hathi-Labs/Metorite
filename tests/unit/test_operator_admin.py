"""Adding and removing an operator — WS-31 **CP-12d**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §6.1 · §8.1
done-whens 16-19 · **D64.3**.

The four guards are the subject, and each one exists because of a specific way
a registry breaks: locked out (last admin), self-promotion, a revocation that
leaves a live session, and an audit trail orphaned by a delete.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_operator_admin.py
"""
from __future__ import annotations

import os
import pathlib
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


@pytest.fixture(autouse=True)
def _empty_registry(eng):
    """⚠️ The last-admin guard COUNTS, so leftover admins change the answer.

    Every test here starts from an empty registry. Without this, a suite that
    happened to run after another would find two admins and the guard under
    test would correctly not fire — a green run proving nothing.
    """
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM operator_session"))
        conn.execute(text("DELETE FROM operator_elevation"))
        conn.execute(text("DELETE FROM operator"))
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", SHARED)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    from customer_console.main import app
    return TestClient(app)


def _make(eng, role: str, *, status: str = "active") -> tuple[str, str, str]:
    """An operator with a live session. Returns ``(id, email, token)``."""
    from customer_console import operator_sessions, store

    email = f"{role}-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        row = conn.execute(
            text("INSERT INTO operator (email, role, status) "
                 "VALUES (:e, :r, :s) RETURNING id"),
            {"e": email, "r": role, "s": status},
        ).first()
        store.operator_session_insert(
            conn, operator_id=str(row[0]), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12),
        )
    return str(row[0]), email, issued.token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _status(eng, operator_id: str) -> tuple[str, str]:
    with eng.begin() as conn:
        r = conn.execute(
            text("SELECT role, status FROM operator WHERE id = CAST(:i AS UUID)"),
            {"i": operator_id},
        ).first()
    return str(r[0]), str(r[1])


# ── Adding ─────────────────────────────────────────────────────────────────


def test_an_admin_adds_an_operator(client, eng):
    _, _, admin = _make(eng, "admin")
    r = client.post("/operators", headers=_auth(admin),
                    json={"email": "New.Person@Fracktal.in", "role": "editor"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "new.person@fracktal.in", "email not normalised"


def test_adding_the_same_person_twice_is_not_a_duplicate(client, eng):
    _, _, admin = _make(eng, "admin")
    first = client.post("/operators", headers=_auth(admin),
                        json={"email": "dup@fracktal.in", "role": "viewer"})
    second = client.post("/operators", headers=_auth(admin),
                         json={"email": "DUP@fracktal.in", "role": "admin"})
    assert first.json()["id"] == second.json()["id"]
    assert _status(eng, first.json()["id"])[0] == "viewer", (
        "a repeat add silently re-roled somebody"
    )


def test_an_unknown_role_is_a_400_not_a_database_error(client, eng):
    _, _, admin = _make(eng, "admin")
    r = client.post("/operators", headers=_auth(admin),
                    json={"email": "x@fracktal.in", "role": "superuser"})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_only_an_admin_may_add_an_operator(client, eng, role):
    """The matrix's job, asserted here because this is the route that matters."""
    _, _, token = _make(eng, role)
    _make(eng, "admin")  # so the registry is not empty for the wrong reason
    r = client.post("/operators", headers=_auth(token),
                    json={"email": "x@fracktal.in", "role": "admin"})
    assert r.status_code == 403, r.text


def test_every_role_may_read_the_operator_list(client, eng):
    """Transparency inside the team is the point (spec §5)."""
    _make(eng, "admin")
    for role in ("viewer", "editor", "admin"):
        _, _, token = _make(eng, role)
        r = client.get("/operators", headers=_auth(token))
        assert r.status_code == 200, f"{role}: {r.text}"
        assert r.json()["operators"]


# ── Guard 1: the last active admin ─────────────────────────────────────────


def test_the_last_active_admin_cannot_be_demoted(client, eng):
    """Done-when 16. One careless change would lock the team out."""
    admin_id, _, admin = _make(eng, "admin")
    other_id, _, _ = _make(eng, "editor")

    # The admin cannot demote themselves (guard 2 fires first, also 409)…
    assert client.patch(f"/operators/{admin_id}", headers=_auth(admin),
                        json={"role": "viewer"}).status_code == 409

    # …and a SECOND admin cannot demote the one that would leave zero.
    client.patch(f"/operators/{other_id}", headers=_auth(admin),
                 json={"role": "admin"})
    with eng.begin() as conn:
        conn.execute(
            text("UPDATE operator SET status='deactivated' "
                 "WHERE id = CAST(:i AS UUID)"),
            {"i": other_id},
        )
    # Now `admin` is the only ACTIVE admin again.
    r = client.patch(f"/operators/{other_id}", headers=_auth(admin),
                     json={"status": "active"})
    assert r.status_code == 200, r.text


def test_the_last_active_admin_cannot_be_suspended_by_another_admin(
    client, eng
):
    """Two admins, one demoted first, then the survivor cannot be switched off."""
    a_id, _, a_token = _make(eng, "admin")
    b_id, _, b_token = _make(eng, "admin")

    # B suspends A. Two active admins → allowed, one left.
    assert client.patch(f"/operators/{a_id}", headers=_auth(b_token),
                        json={"status": "suspended"}).status_code == 200
    # A's token is dead now, so B is the only admin. Nobody can switch B off.
    assert client.patch(f"/operators/{b_id}", headers=_auth(b_token),
                        json={"status": "suspended"}).status_code == 409
    assert _status(eng, b_id) == ("admin", "active")


def test_the_last_active_admin_cannot_be_demoted_even_by_break_glass(
    client, eng
):
    """⚠️ Guard 1 IN ISOLATION — and the only way to reach it.

    Guard 2 (no self-write) fires on any admin editing themselves, and TWO
    active admins cannot exist while one demotes the other to zero. So an
    operator caller can never isolate guard 1: whichever way it is set up,
    guard 2 answers first and the test passes for the wrong reason.

    The SHARED (break-glass) token holds no operator id, so `guard_not_self`
    does not apply to it and guard 1 is the only thing left standing.
    Measured: without this test, removing the last-admin guard entirely left
    the suite green.
    """
    only_id, _, _ = _make(eng, "admin")

    demote = client.patch(f"/operators/{only_id}", headers=_auth(SHARED),
                          json={"role": "viewer"})
    assert demote.status_code == 409, demote.text
    assert _status(eng, only_id) == ("admin", "active")

    suspend = client.patch(f"/operators/{only_id}", headers=_auth(SHARED),
                           json={"status": "suspended"})
    assert suspend.status_code == 409, suspend.text
    assert _status(eng, only_id) == ("admin", "active")


def test_the_guard_permits_the_demotion_when_a_second_admin_exists(
    client, eng
):
    """The mirror, and it is what makes the guard a guard rather than a wall.

    It also kills the off-by-one: a guard counting `<= 0` would refuse
    nothing here, and one counting `<= 2` would refuse this legitimate change.
    """
    first_id, _, _ = _make(eng, "admin")
    _make(eng, "admin")

    r = client.patch(f"/operators/{first_id}", headers=_auth(SHARED),
                     json={"role": "viewer"})
    assert r.status_code == 200, r.text
    assert _status(eng, first_id) == ("viewer", "active")


def test_a_SUSPENDED_admin_is_not_protection_and_can_be_demoted(client, eng):
    """⚠️ "Active admin" is BOTH halves, and dropping either one breaks it.

    A suspended admin cannot sign in, so they protect nobody from a lockout.
    Counting them would refuse a legitimate tidy-up — demoting a colleague who
    already left — and the refusal would look like a bug in the guard.

    Measured: without this test, a mutation that read `target_role == ADMIN`
    and ignored the status survived the whole suite.
    """
    live_id, _, _ = _make(eng, "admin")
    gone_id, _, _ = _make(eng, "admin", status="suspended")

    r = client.patch(f"/operators/{gone_id}", headers=_auth(SHARED),
                     json={"role": "viewer"})
    assert r.status_code == 200, r.text
    assert _status(eng, gone_id) == ("viewer", "suspended")
    # …and the live admin is still protected.
    assert client.patch(f"/operators/{live_id}", headers=_auth(SHARED),
                        json={"role": "viewer"}).status_code == 409


def test_a_non_admin_can_always_be_deactivated(client, eng):
    """The guard must not block ordinary work — it counts ADMINS."""
    _, _, admin = _make(eng, "admin")
    viewer_id, _, _ = _make(eng, "viewer")
    assert client.delete(f"/operators/{viewer_id}",
                         headers=_auth(admin)).status_code == 200
    assert _status(eng, viewer_id)[1] == "deactivated"


# ── Guard 2: no self-write ─────────────────────────────────────────────────


def test_an_operator_cannot_change_their_own_role(client, eng):
    """Done-when 17. An admin who can promote themselves holds no role."""
    _make(eng, "admin")  # a second admin, so guard 1 is not what fires
    me_id, _, me = _make(eng, "admin")
    r = client.patch(f"/operators/{me_id}", headers=_auth(me),
                     json={"role": "viewer"})
    assert r.status_code == 409, r.text
    assert _status(eng, me_id)[0] == "admin"


def test_an_operator_cannot_deactivate_themselves(client, eng):
    _make(eng, "admin")
    me_id, _, me = _make(eng, "admin")
    assert client.delete(f"/operators/{me_id}",
                         headers=_auth(me)).status_code == 409


# ── Guards 3 and 4: deactivation seals, and kills every session ────────────


def test_deactivation_revokes_every_session_in_one_transaction(client, eng):
    """Done-when 18, and it is spec §2's F5 answered.

    Two live sessions for one person, and BOTH die on the deactivation —
    not the one that happened to be used.
    """
    from customer_console import operator_sessions, store

    _, _, admin = _make(eng, "admin")
    victim_id, _, first = _make(eng, "editor")

    now = datetime.now(UTC)
    second = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        store.operator_session_insert(
            conn, operator_id=victim_id, prefix=second.prefix,
            key_hash=second.key_hash, expires_at=now + timedelta(hours=12),
        )

    assert client.get("/orgs", headers=_auth(first)).status_code == 200
    assert client.get("/orgs", headers=_auth(second.token)).status_code == 200

    r = client.delete(f"/operators/{victim_id}", headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert r.json()["sessions_revoked"] == 2

    assert client.get("/orgs", headers=_auth(first)).status_code == 401
    assert client.get("/orgs", headers=_auth(second.token)).status_code == 401


def test_deactivation_seals_the_row_rather_than_deleting_it(client, eng):
    """Done-when 19 — D63. The audit trail must survive the person leaving."""
    _, _, admin = _make(eng, "admin")
    victim_id, victim_email, _ = _make(eng, "editor")

    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO control_audit (organization_id, actor, action, "
                 "detail) VALUES (NULL, :a, 'credits.grant', '{}')"),
            {"a": victim_email},
        )

    client.delete(f"/operators/{victim_id}", headers=_auth(admin))

    with eng.begin() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM operator WHERE id = CAST(:i AS UUID)"),
            {"i": victim_id},
        ).scalar_one() == 1, "the row was DELETED — the audit trail is orphaned"
        assert conn.execute(
            text("SELECT count(*) FROM control_audit WHERE actor = :a"),
            {"a": victim_email},
        ).scalar_one() >= 1, "the person's history is no longer readable"


def test_suspension_also_kills_sessions(client, eng):
    """`suspended` is not a softer `active` — it must stop working at once."""
    _, _, admin = _make(eng, "admin")
    victim_id, _, victim = _make(eng, "editor")

    assert client.get("/orgs", headers=_auth(victim)).status_code == 200
    client.patch(f"/operators/{victim_id}", headers=_auth(admin),
                 json={"status": "suspended"})
    assert client.get("/orgs", headers=_auth(victim)).status_code == 401


def test_a_role_change_alone_does_not_revoke_sessions(client, eng):
    """The mirror. Re-roling somebody must not sign them out of their work."""
    _, _, admin = _make(eng, "admin")
    victim_id, _, victim = _make(eng, "editor")

    r = client.patch(f"/operators/{victim_id}", headers=_auth(admin),
                     json={"role": "viewer"})
    assert r.status_code == 200
    assert r.json()["sessions_revoked"] == 0
    assert client.get("/orgs", headers=_auth(victim)).status_code == 200


def test_a_re_role_takes_effect_on_the_very_next_request(client, eng):
    """The role is read per request, so a demotion is not deferred."""
    _, _, admin = _make(eng, "admin")
    victim_id, _, victim = _make(eng, "admin")

    # `POST /operators` is admin-only and NOT `elevated`, so this measures the
    # ROLE alone. `/keys` would also need an elevation window (CP-12e) and the
    # test would then be about two things at once.
    assert client.post("/operators", headers=_auth(victim),
                       json={"email": "probe-a@fracktal.in", "role": "viewer"}
                       ).status_code == 200

    client.patch(f"/operators/{victim_id}", headers=_auth(admin),
                 json={"role": "viewer"})

    assert client.post("/operators", headers=_auth(victim),
                       json={"email": "probe-b@fracktal.in", "role": "viewer"}
                       ).status_code == 403


# ── Shapes ─────────────────────────────────────────────────────────────────


def test_a_patch_that_changes_nothing_is_a_400(client, eng):
    _, _, admin = _make(eng, "admin")
    other_id, _, _ = _make(eng, "viewer")
    assert client.patch(f"/operators/{other_id}", headers=_auth(admin),
                        json={}).status_code == 400


def test_an_unknown_operator_is_a_404(client, eng):
    _, _, admin = _make(eng, "admin")
    r = client.patch(f"/operators/{uuid.uuid4()}", headers=_auth(admin),
                     json={"role": "viewer"})
    assert r.status_code == 404


def test_the_admin_write_is_audited_with_the_person_who_made_it(client, eng):
    _, admin_email, admin = _make(eng, "admin")
    client.post("/operators", headers=_auth(admin),
                json={"email": "audited@fracktal.in", "role": "viewer"})
    with eng.begin() as conn:
        actors = [
            r[0] for r in conn.execute(
                text("SELECT actor FROM control_audit "
                     "WHERE action = 'operator.add' ORDER BY created_at DESC")
            )
        ]
    assert actors and actors[0] == admin_email


# ── The R8 gate cannot silently disarm ─────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_operator_admin.py" in workflow


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    spec = (
        _ROOT / "project-docs" / "specs" / "operator_identity_and_access.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_operator_admin.py" in spec
