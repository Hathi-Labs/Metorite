"""The role matrix, cell by cell — WS-31 **CP-12c**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §5 · §8.1
done-whens 13-15 · **D64.3**.

Until this slice, anybody who signed in could do anything — including
destroying a customer's tenant plane (spec §2, F4). This suite is the proof
that the matrix binds, and it is written cell by cell **from the matrix
itself** so a row added later cannot go untested.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_operator_roles.py
"""
from __future__ import annotations

import os
import pathlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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


def _token(eng, role: str) -> str:
    """An operator holding *role*, with a live session. Returns the token."""
    from customer_console import operator_sessions, store

    email = f"{role}-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        row = conn.execute(
            text("INSERT INTO operator (email, role) VALUES (:e, :r) "
                 "RETURNING id"),
            {"e": email, "r": role},
        ).first()
        store.operator_session_insert(
            conn,
            operator_id=str(row[0]),
            prefix=issued.prefix,
            key_hash=issued.key_hash,
            expires_at=now + timedelta(hours=12),
        )
    return issued.token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _org(eng) -> str:
    slug = f"acme-{uuid.uuid4().hex[:8]}"
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO organization (slug, name) VALUES (:s, 'Acme')"),
            {"s": slug},
        )
    return slug


# ── Done-when 14: the matrix covers every operator-gated route ──────────────


def test_every_operator_gated_route_has_a_matrix_row():
    """Done-when 14, as a SOURCE fence.

    ⚠️ This is the test that stops the matrix rotting. A new operator route
    added without a row is refused at RUNTIME (the matrix fails closed), which
    is safe but arrives as a mystery 403 in production. This makes it arrive in
    CI instead, which is what R7 asks a rule to do.
    """
    from customer_console import operator_roles

    src = (
        _ROOT / "apps" / "services" / "customer_console" / "customer_console"
        / "main.py"
    ).read_text(encoding="utf-8")

    gates = (
        "Operator", "ProvisionCaller", "SeatAdminCaller", "MemberAdminCaller",
        "CatalogCaller",
    )
    pattern = re.compile(
        r'@app\.(get|post|patch|delete)\("([^"]+)"\)\s*\ndef \w+\(([^)]*)\)',
        re.S,
    )

    missing = []
    for m in pattern.finditer(src):
        method, path, args = m.group(1).upper(), m.group(2), m.group(3)
        if not any(re.search(rf":\s*{g}\b", args) for g in gates):
            continue
        if operator_roles.rule_for(method, path) is None:
            missing.append(f"{method} {path}")

    assert not missing, (
        "these operator-reachable routes have no row in the §5 matrix, so a "
        f"signed-in operator is refused with an unexplained 403: {missing}"
    )


def test_the_matrix_names_no_route_that_does_not_exist():
    """The mirror of the above — a row for a deleted route is a lie."""
    from customer_console import operator_roles

    src = (
        _ROOT / "apps" / "services" / "customer_console" / "customer_console"
        / "main.py"
    ).read_text(encoding="utf-8")
    declared = {
        (m.group(1).upper(), m.group(2))
        for m in re.finditer(r'@app\.(get|post|patch|delete)\("([^"]+)"\)', src)
    }
    # CP-12d and CP-12f add `/operators*`; a row may legitimately land first.
    stale = [
        f"{k[0]} {k[1]}"
        for k in operator_roles.MATRIX
        if k not in declared and not k[1].startswith("/operators")
    ]
    assert not stale, f"the matrix names routes that do not exist: {stale}"


def test_every_matrix_row_names_a_known_role():
    from customer_console import operator_roles
    from customer_console.operators import ROLES

    for key, rule in operator_roles.MATRIX.items():
        assert rule.min_role in ROLES, f"{key} demands an unknown role"


# ── Done-when 13: the cells, driven off the matrix itself ───────────────────

#: One safe, side-effect-free probe per method. A `no` cell must refuse BEFORE
#: the body runs, so the body never executes and the payload only has to parse.
_PROBES: dict[tuple[str, str], dict] = {
    ("GET", "/orgs"): {},
    ("GET", "/billing/summary"): {"params": {"org_slug": "nope"}},
    ("GET", "/credits/balance"): {"params": {"org_slug": "nope"}},
    ("GET", "/keys"): {"params": {"org_slug": "nope"}},
    ("POST", "/orgs/lifecycle"): {
        "json": {"org_slug": "nope", "target": "suspended", "reason": "t"}
    },
    ("POST", "/orgs/purge"): {"json": {"org_slug": "nope", "confirm": "nope"}},
    ("POST", "/billing/seats"): {"json": {"org_slug": "nope",
                                          "email": "a@b.c",
                                          "plan_slug": "core"}},
    ("POST", "/billing/seats/release"): {"json": {"org_slug": "nope",
                                                  "email": "a@b.c",
                                                  "plan_slug": "core"}},
    ("POST", "/credits/grant"): {"json": {"org_slug": "nope", "credits": "1",
                                          "reason": "manual"}},
    ("POST", "/keys"): {"json": {"org_slug": "nope", "label": "t"}},
    ("POST", "/keys/revoke"): {"json": {"org_slug": "nope", "prefix": "x"}},
}

_CELLS = [
    (method, path, role)
    for (method, path) in _PROBES
    for role in ("viewer", "editor", "admin")
]


@pytest.mark.parametrize("method,path,role", _CELLS)
def test_each_matrix_cell(client, eng, method, path, role):
    """Done-when 13. A `no` cell answers **403**. A `yes` cell does not.

    A `yes` cell is asserted as *"not 403"* rather than as 200, because these
    probes deliberately name an organization that does not exist — the point
    here is the GATE, and the route's own 404 or 400 proves the gate opened.
    """
    from customer_console import operator_roles

    rule = operator_roles.rule_for(method, path)
    assert rule is not None, f"{method} {path} left the matrix"

    allowed = operator_roles.rank(role) >= operator_roles.rank(rule.min_role)
    probe = _PROBES[(method, path)]
    r = client.request(method, path, headers=_auth(_token(eng, role)), **probe)

    if allowed:
        assert r.status_code != 403, (
            f"{role} should reach {method} {path} and was refused: {r.text}"
        )
    else:
        assert r.status_code == 403, (
            f"{role} must NOT reach {method} {path}, got {r.status_code}"
        )


def test_a_viewer_cannot_purge_an_organization(client, eng):
    """Named on its own because it is the worst cell in the matrix.

    Spec §2's F4 in one line: before CP-12c, anybody who signed in could
    destroy a customer's tenant plane.
    """
    r = client.post(
        "/orgs/purge",
        headers=_auth(_token(eng, "viewer")),
        json={"org_slug": _org(eng), "confirm": "x"},
    )
    assert r.status_code == 403


# ── Done-when 15: the refusal is not an oracle ──────────────────────────────


def test_the_403_is_identical_for_a_real_and_an_imaginary_company(client, eng):
    """Done-when 15. The role is checked BEFORE the organization is read.

    Otherwise a `viewer` could enumerate our customers by watching a 404 turn
    into a 403.
    """
    viewer = _auth(_token(eng, "viewer"))
    real, imaginary = _org(eng), f"ghost-{uuid.uuid4().hex[:8]}"

    responses = [
        client.post("/orgs/lifecycle", headers=viewer,
                    json={"org_slug": slug, "target": "suspended",
                          "reason": "t"})
        for slug in (real, imaginary)
    ]
    assert {r.status_code for r in responses} == {403}
    assert len({r.text for r in responses}) == 1, (
        "the refusal differs between a real and an imaginary company, so it "
        "is an oracle for which customers exist"
    )


def test_every_role_refusal_reads_identically(client, eng):
    """One body for a rank refusal and for an unmapped route alike."""
    viewer = _auth(_token(eng, "viewer"))
    bodies = {
        client.post("/orgs/purge", headers=viewer,
                    json={"org_slug": "x", "confirm": "x"}).text,
        client.post("/keys", headers=viewer,
                    json={"org_slug": "x", "label": "t"}).text,
        client.post("/discounts", headers=viewer,
                    json={"label": "t", "kind": "percent", "value": 100}).text,
    }
    assert len(bodies) == 1, f"role refusals differ: {bodies}"


# ── The credit threshold — the half a dependency cannot see ─────────────────


def test_an_editor_may_grant_below_the_threshold(client, eng):
    from customer_console import operator_roles

    slug = _org(eng)
    under = operator_roles.credit_elevation() - 1
    r = client.post(
        "/credits/grant",
        headers=_auth(_token(eng, "editor")),
        json={"org_slug": slug, "credits": str(under), "reason": "manual"},
    )
    assert r.status_code == 200, r.text


def test_an_editor_may_not_grant_above_the_threshold(client, eng):
    """⚠️ The rule the matrix cannot apply at the door, because it is a BODY."""
    from customer_console import operator_roles

    slug = _org(eng)
    over = operator_roles.credit_elevation() + 1
    r = client.post(
        "/credits/grant",
        headers=_auth(_token(eng, "editor")),
        json={"org_slug": slug, "credits": str(over), "reason": "manual"},
    )
    assert r.status_code == 403, r.text


def test_an_admin_may_grant_above_the_threshold(client, eng):
    from customer_console import operator_roles

    slug = _org(eng)
    over = operator_roles.credit_elevation() + 1
    r = client.post(
        "/credits/grant",
        headers=_auth(_token(eng, "admin")),
        json={"org_slug": slug, "credits": str(over), "reason": "manual"},
    )
    assert r.status_code == 200, r.text


def test_a_large_NEGATIVE_grant_is_a_correction_not_an_escalation(client, eng):
    """An editor may reverse their own mistake without finding an admin.

    Holding corrections to the admin bar would push people to fix an error by
    granting MORE, which is the opposite of what the threshold is for.
    """
    from customer_console import operator_roles

    slug = _org(eng)
    big = -(operator_roles.credit_elevation() + 1)
    r = client.post(
        "/credits/grant",
        headers=_auth(_token(eng, "editor")),
        json={"org_slug": slug, "credits": str(big), "reason": "manual"},
    )
    assert r.status_code == 200, r.text


def test_a_broken_threshold_falls_back_rather_than_disabling_the_rule(
    monkeypatch,
):
    """A typo must not read as "no ceiling"."""
    from customer_console import operator_roles

    default = Decimal(operator_roles.DEFAULT_CREDIT_ELEVATION)
    for bad in ("", "0", "-5", "banana"):
        assert operator_roles.credit_elevation(
            {"OPERATOR_CREDIT_ELEVATION": bad}
        ) == default, bad


# ── The shared token, and unknown roles ─────────────────────────────────────


def test_the_shared_token_still_bypasses_the_matrix(client):
    """⚠️ Documented, not accidental — it is the break-glass path.

    The shared token carries no role, so the matrix cannot judge it and
    deliberately does not try. It keeps reaching everything, which is exactly
    why using it is an owner act (`work_plan.md` §6.1, CP-12 block (f)) and
    why CP-12e alerts on it.
    """
    assert client.get("/orgs", headers=_auth(SHARED)).status_code == 200
    r = client.post("/orgs/purge", headers=_auth(SHARED),
                    json={"org_slug": "nope", "confirm": "nope"})
    assert r.status_code != 403, "the shared token lost its break-glass reach"


def test_an_unknown_role_ranks_below_viewer(client, eng):
    """A role this module does not know is refused everywhere, never admitted."""
    from customer_console import operator_roles

    assert operator_roles.rank("superuser") < operator_roles.rank("viewer")
    assert operator_roles.rank(None) < operator_roles.rank("viewer")
    assert operator_roles.rank("") < operator_roles.rank("viewer")


def test_an_unmapped_route_fails_closed():
    """The matrix refuses what it does not name."""
    from customer_console import operator_roles

    with pytest.raises(operator_roles.RoleForbidden):
        operator_roles.check_route("admin", "POST", "/a/route/nobody/mapped")


# ── The R8 gate cannot silently disarm ──────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_operator_roles.py" in workflow


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    spec = (
        _ROOT / "project-docs" / "specs" / "operator_identity_and_access.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_operator_roles.py" in spec
