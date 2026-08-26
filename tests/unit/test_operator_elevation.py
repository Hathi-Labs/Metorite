"""Time-boxed elevation and the break-glass path — WS-31 **CP-12e**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §6.3 · §6.4 ·
§8.1 done-whens 20-24 · **D64.4**.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_operator_elevation.py
"""
from __future__ import annotations

import logging
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
REASON = "customer asked us to suspend billing"


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


def _make(eng, role: str) -> tuple[str, str, str]:
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
            conn, operator_id=str(row[0]), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12),
        )
    return str(row[0]), email, issued.token


def _auth(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _org(eng) -> str:
    slug = f"acme-{uuid.uuid4().hex[:8]}"
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO organization (slug, name) VALUES (:s, 'Acme')"),
            {"s": slug},
        )
    return slug


def _suspend(client, token, slug):
    """An ``elevated`` action from the §5 matrix."""
    return client.post("/orgs/lifecycle", headers=_auth(token),
                       json={"org_slug": slug, "target": "suspended",
                             "reason": "t"})


# ── The route-ordering hazard this slice actually hit ──────────────────────


def test_the_elevate_routes_are_not_swallowed_by_the_path_parameter():
    """⚠️ A real bug, caught at build and pinned here.

    FastAPI matches in DECLARATION order. ``/operators/{operator_id}`` was
    declared first, so ``DELETE /operators/elevate`` matched it with
    ``operator_id="elevate"`` and answered 404 instead of closing a window.
    Nothing about the code looked wrong — the routes simply have to be in the
    right order, and only a test says so.
    """
    from customer_console.main import app

    paths = [
        r.path for r in app.routes
        if getattr(r, "path", "").startswith("/operators")
    ]
    assert paths.index("/operators/elevate") < paths.index(
        "/operators/{operator_id}"
    ), "the path parameter shadows /operators/elevate again"


# ── Done-when 20: an elevated action needs a window ────────────────────────


def test_an_admin_without_a_window_cannot_suspend(client, eng):
    """Done-when 20. The role is right. The window is missing."""
    _, _, admin = _make(eng, "admin")
    assert _suspend(client, admin, _org(eng)).status_code == 403


def test_an_admin_with_a_window_can_suspend(client, eng):
    """The positive case — otherwise every refusal above proves nothing."""
    _, _, admin = _make(eng, "admin")
    opened = client.post("/operators/elevate", headers=_auth(admin),
                         json={"reason": REASON})
    assert opened.status_code == 200, opened.text
    r = _suspend(client, admin, _org(eng))
    assert r.status_code == 200, r.text


def test_a_non_elevated_action_never_needs_a_window(client, eng):
    """The matrix's ``elevated`` flag must not leak onto ordinary work."""
    _, _, editor = _make(eng, "editor")
    r = client.post("/billing/seats", headers=_auth(editor),
                    json={"org_slug": "nope", "email": "a@b.c",
                          "plan_slug": "core"})
    assert r.status_code != 403, r.text


# ── Done-when 22: the window expires ───────────────────────────────────────


def test_an_expired_window_refuses_the_next_elevated_action(client, eng):
    """Done-when 22. Temporary access nobody expires is standing privilege."""
    operator_id, _, admin = _make(eng, "admin")
    client.post("/operators/elevate", headers=_auth(admin),
                json={"reason": REASON})
    assert _suspend(client, admin, _org(eng)).status_code == 200

    with eng.begin() as conn:
        conn.execute(
            text("UPDATE operator_elevation SET expires_at = now() - "
                 "interval '1 second' WHERE operator_id = CAST(:o AS UUID)"),
            {"o": operator_id},
        )
    assert _suspend(client, admin, _org(eng)).status_code == 403


def test_closing_a_window_early_ends_the_privilege(client, eng):
    """Finishing the job should end it, not waiting out the clock."""
    _, _, admin = _make(eng, "admin")
    client.post("/operators/elevate", headers=_auth(admin),
                json={"reason": REASON})
    assert _suspend(client, admin, _org(eng)).status_code == 200

    closed = client.delete("/operators/elevate", headers=_auth(admin))
    assert closed.status_code == 200 and closed.json()["closed"] == 1
    assert _suspend(client, admin, _org(eng)).status_code == 403


def test_the_window_is_readable_so_a_countdown_can_be_shown(client, eng):
    _, _, admin = _make(eng, "admin")
    assert client.get("/operators/elevate",
                      headers=_auth(admin)).json()["elevated"] is False
    client.post("/operators/elevate", headers=_auth(admin),
                json={"reason": REASON, "reference": "ticket:123"})
    body = client.get("/operators/elevate", headers=_auth(admin)).json()
    assert body["elevated"] is True
    assert body["reason"] == REASON
    assert body["reference"] == "ticket:123"


# ── Done-whens 21 and 23: who may elevate, and with what reason ────────────


def test_a_reason_shorter_than_the_floor_is_refused(client, eng):
    """Done-when 21. A reason is what makes the row answer *why*."""
    _, _, admin = _make(eng, "admin")
    for bad in ("", "   ", "too short"):
        r = client.post("/operators/elevate", headers=_auth(admin),
                        json={"reason": bad})
        assert r.status_code in (400, 422), f"{bad!r} -> {r.status_code}"


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_only_an_admin_may_elevate(client, eng, role):
    """Done-when 23. Elevation time-boxes a role. It does not grant one."""
    _, _, token = _make(eng, role)
    r = client.post("/operators/elevate", headers=_auth(token),
                    json={"reason": REASON})
    assert r.status_code == 403, r.text


def test_elevation_is_always_for_the_caller():
    """There is no ``operator_id`` parameter, and that is the design.

    Elevating somebody else would hand out a destructive privilege they did
    not ask for, and the audit row would name the wrong person.
    """
    from customer_console.main import ElevateRequest

    assert "operator_id" not in ElevateRequest.model_fields


def test_a_refusal_for_no_window_looks_like_a_refusal_for_rank(client, eng):
    """⚠️ Deliberately indistinguishable.

    Saying *"your role is fine, you just need to elevate"* is a hint an
    attacker holding a stolen admin session would act on immediately.
    """
    _, _, viewer = _make(eng, "viewer")
    _, _, admin = _make(eng, "admin")
    slug = _org(eng)

    by_rank = _suspend(client, viewer, slug)
    by_window = _suspend(client, admin, slug)
    assert by_rank.status_code == by_window.status_code == 403
    assert by_rank.text == by_window.text


# ── Done-when 24: break-glass is loud ──────────────────────────────────────


def test_the_shared_token_records_breakglass_not_operator(client, eng):
    """Done-when 24, first half. The word is the point.

    Calling it ``operator`` made a bypass of every control look routine.
    """
    from customer_console.auth import SHARED_TOKEN_ACTOR

    assert SHARED_TOKEN_ACTOR == "breakglass"

    slug = _org(eng)
    r = client.post("/credits/grant", headers=_auth(SHARED),
                    json={"org_slug": slug, "credits": "1",
                          "reason": "manual"})
    assert r.status_code == 200, r.text
    with eng.begin() as conn:
        actors = [
            x[0] for x in conn.execute(
                text("SELECT actor FROM control_audit WHERE action = "
                     "'credits.grant' AND organization_id = "
                     "(SELECT id FROM organization WHERE slug = :s)"),
                {"s": slug},
            )
        ]
    assert actors == ["breakglass"]


def test_every_break_glass_use_is_announced(client, caplog):
    """Done-when 24, second half.

    ⚠️ **The Console logs. It does not send the mail.** Routing this to
    ``OPERATOR_ALERT_EMAIL`` is deliberately left to log alerting: the Resend
    seam lives in the GATEWAY (``routes/email_otp.py``), and reaching across a
    service boundary for one message would be a second email seam inside the
    Console. The WARNING is the durable record and the thing an alert rule
    fires on. Named as a deferral in the spec's §9.
    """
    with caplog.at_level(logging.WARNING, logger="platform.auth"):
        client.get("/orgs", headers=_auth(SHARED))
    assert any(r.message == "operator.breakglass" for r in caplog.records), (
        "a break-glass call was not announced"
    )


def test_a_normal_session_does_not_trip_the_alarm(client, eng, caplog):
    """An alarm that fires on ordinary work is an alarm nobody reads."""
    _, _, admin = _make(eng, "admin")
    with caplog.at_level(logging.WARNING, logger="platform.auth"):
        client.get("/orgs", headers=_auth(admin))
    assert not any(r.message == "operator.breakglass" for r in caplog.records)


def test_break_glass_cannot_open_an_elevation_window(client):
    """It is already past every gate. A window would name no person."""
    r = client.post("/operators/elevate", headers=_auth(SHARED),
                    json={"reason": REASON})
    assert r.status_code == 403


# ── The TTL falls back rather than becoming standing privilege ─────────────


def test_a_broken_ttl_falls_back_to_the_default():
    from customer_console import operator_elevation

    default = timedelta(minutes=operator_elevation.DEFAULT_TTL_MINUTES)
    for bad in ("", "0", "-30", "banana"):
        assert operator_elevation.ttl(
            {"OPERATOR_ELEVATION_TTL_MINUTES": bad}
        ) == default, bad


# ── The R8 gate cannot silently disarm ─────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_operator_elevation.py" in workflow


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    spec = (
        _ROOT / "project-docs" / "specs" / "operator_identity_and_access.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_operator_elevation.py" in spec
