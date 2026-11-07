"""Unit tests for acb_auth RBAC scaffold (WBS 1.7).

All tests are pure-Python -- no DB, no live FastAPI server required.
FastAPI route-level enforcement is covered by the async dependency tests
using httpx.AsyncClient with dependency_overrides.
"""
from __future__ import annotations

import os as _os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from acb_auth import UserContext, UserRole, get_current_user, require_role
from acb_auth.roles import _coerce_role


# ---- _coerce_role ----------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("executive", UserRole.EXECUTIVE),
        ("EXECUTIVE", UserRole.EXECUTIVE),
        ("Executive", UserRole.EXECUTIVE),
        ("employee", UserRole.EMPLOYEE),
        ("agent", UserRole.AGENT),
        ("unknown", UserRole.EMPLOYEE),
        ("admin", UserRole.EMPLOYEE),
        ("", UserRole.EMPLOYEE),
        (None, UserRole.EMPLOYEE),
    ],
)
def test_coerce_role(raw, expected):
    assert _coerce_role(raw) == expected


# ---- UserContext -----------------------------------------------------------

def test_user_context_executive_flag():
    u = UserContext(email="ceo@fracktal.in", role=UserRole.EXECUTIVE)
    assert u.is_executive
    assert not u.is_employee
    assert not u.is_agent


def test_user_context_employee_flag():
    u = UserContext(email="dev@fracktal.in", role=UserRole.EMPLOYEE)
    assert u.is_employee
    assert not u.is_executive


def test_user_context_no_email():
    u = UserContext(email=None, role=UserRole.EMPLOYEE)
    assert u.email is None


# ---- get_current_user (FastAPI dependency via TestClient) ------------------

def _make_app():
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user=Depends(get_current_user)):
        return {"email": user.email, "role": user.role.value}

    return app


def test_get_current_user_from_headers(monkeypatch):
    # SSO headers ALONE (no internal Bearer) no longer authenticate when a gateway
    # token is configured — a bare X-User-Email is spoofable by anyone who reaches
    # the gateway directly. Real identity now requires the Next.js proxy's Bearer
    # (see TestBearerIdentityChain); here the header resolves to anonymous.
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "tok")
    client = TestClient(_make_app())
    r = client.get("/whoami", headers={"X-User-Email": "ceo@fracktal.in", "X-User-Role": "executive"})
    assert r.status_code == 200
    assert r.json()["email"] is None


def test_get_current_user_no_headers_defaults_to_employee():
    client = TestClient(_make_app())
    r = client.get("/whoami")
    assert r.status_code == 200
    assert r.json()["role"] == "employee"


def test_get_current_user_unknown_role_falls_back():
    client = TestClient(_make_app())
    r = client.get("/whoami", headers={"X-User-Role": "superadmin"})
    assert r.status_code == 200
    assert r.json()["role"] == "employee"


# ---- require_role ----------------------------------------------------------

def _make_gated_app():
    app = FastAPI()

    @app.get("/open")
    async def open_route(user=Depends(get_current_user)):
        return {"role": user.role.value}

    @app.get("/exec-only", dependencies=[require_role(UserRole.EXECUTIVE)])
    async def exec_only():
        return {"ok": True}

    @app.get("/staff", dependencies=[require_role(UserRole.EXECUTIVE, UserRole.EMPLOYEE)])
    async def staff_only():
        return {"ok": True}

    return app


def test_require_role_executive_granted():
    client = TestClient(_make_gated_app())
    r = client.get("/exec-only", headers={"X-User-Role": "executive"})
    assert r.status_code == 200


def test_require_role_employee_denied_on_exec_route():
    client = TestClient(_make_gated_app())
    r = client.get("/exec-only", headers={"X-User-Role": "employee"})
    assert r.status_code == 403


def test_require_role_no_header_denied_on_exec_route():
    client = TestClient(_make_gated_app())
    r = client.get("/exec-only")
    assert r.status_code == 403


def test_require_role_multi_roles_employee_allowed():
    client = TestClient(_make_gated_app())
    r = client.get("/staff", headers={"X-User-Role": "employee"})
    assert r.status_code == 200


def test_require_role_multi_roles_executive_allowed():
    client = TestClient(_make_gated_app())
    r = client.get("/staff", headers={"X-User-Role": "executive"})
    assert r.status_code == 200


def test_require_role_agent_denied_on_staff_route():
    client = TestClient(_make_gated_app())
    r = client.get("/staff", headers={"X-User-Role": "agent"})
    assert r.status_code == 403


def test_require_role_403_body_contains_required_roles():
    client = TestClient(_make_gated_app())
    r = client.get("/exec-only", headers={"X-User-Role": "employee"})
    assert "executive" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# Bearer token + user identity chain (M2.7 — WBS 1.7)
# ═══════════════════════════════════════════════════════════════════════════
#
# The Next.js proxy sends both an internal Bearer token AND user identity
# headers (X-User-Email, X-User-Role) on every proxied request.  The gateway
# MUST:
#   1. Verify the Bearer token matches GATEWAY_INTERNAL_TOKEN.
#   2. When Bearer matches AND user headers are present → resolve to the
#      real user identity (not "system:internal").
#   3. When Bearer matches but NO user headers → retain legacy "agent" role
#      for cron/CI/internal services.
#   4. When Bearer is wrong/missing → fall through to SSO-only or anonymous.
#
# These tests use TestClient with headers only (no live token validation
# unless GATEWAY_INTERNAL_TOKEN is set in the test environment).  See the
# integration test script for live-token scenarios.


def _bearer_app(gateway_token: str | None = None):
    """Build a mini FastAPI app whose /whoami depends on get_current_user.

    If *gateway_token* is provided, monkey-patch os.environ so
    _get_internal_token() returns it.  Pass None to leave the env alone
    (token will be empty → Bearer auth disabled).
    """
    if gateway_token is not None:
        _os.environ["GATEWAY_INTERNAL_TOKEN"] = gateway_token
        _os.environ.pop("LITELLM_MASTER_KEY", None)

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user=Depends(get_current_user)):
        return {"email": user.email, "role": user.role.value}

    return app


class TestBearerIdentityChain:
    """Bearer-token + user-header identity resolution."""

    TOKEN = "test-internal-token-abc123"

    def teardown_method(self) -> None:
        """Clean up env vars between tests."""
        _os.environ.pop("GATEWAY_INTERNAL_TOKEN", None)

    # ── Bearer matches + user headers → real identity ──────────────────

    def test_bearer_with_user_email_returns_employee(self):
        """Bearer matches + X-User-Email + X-User-Role → employee identity."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "Authorization": f"Bearer {self.TOKEN}",
            "X-User-Email": "dev@fracktal.in",
            "X-User-Role": "employee",
        })
        assert r.status_code == 200
        assert r.json() == {"email": "dev@fracktal.in", "role": "employee"}

    def test_bearer_with_user_email_returns_executive(self):
        """Bearer matches + X-User-Email + X-User-Role=executive → exec."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "Authorization": f"Bearer {self.TOKEN}",
            "X-User-Email": "ceo@fracktal.in",
            "X-User-Role": "executive",
        })
        assert r.status_code == 200
        assert r.json() == {"email": "ceo@fracktal.in", "role": "executive"}

    def test_bearer_with_user_email_no_role_header_defaults_to_employee(self):
        """Bearer + email, no X-User-Role → defaults to employee."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "Authorization": f"Bearer {self.TOKEN}",
            "X-User-Email": "dev@fracktal.in",
        })
        assert r.status_code == 200
        assert r.json() == {"email": "dev@fracktal.in", "role": "employee"}

    # ── Bearer matches, no user headers → agent role (legacy) ──────────

    def test_bearer_only_returns_agent_role(self):
        """Bearer matches, no X-User-Email → system:internal + agent."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "Authorization": f"Bearer {self.TOKEN}",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "system:internal"
        assert data["role"] == "agent"

    # ── Bearer mismatch → falls through ────────────────────────────────

    def test_bearer_wrong_token_with_user_email_is_anonymous(self):
        """Wrong Bearer + user headers → NOT trusted. A bare X-User-Email is
        spoofable, so with a token configured it resolves to anonymous rather
        than the old (insecure) SSO-trust path."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "Authorization": "Bearer wrong-token",
            "X-User-Email": "dev@fracktal.in",
            "X-User-Role": "employee",
        })
        assert r.status_code == 200
        assert r.json()["email"] is None

    def test_bearer_wrong_token_no_headers_anonymous(self):
        """Wrong Bearer, no user headers → anonymous (email=None)."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "Authorization": "Bearer wrong-token",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] is None
        assert data["role"] == "employee"

    # ── Token disabled (empty GATEWAY_INTERNAL_TOKEN) ───────────────────

    def test_bearer_disabled_when_token_empty_fails_closed(self, monkeypatch):
        """No internal token configured → Bearer auth disabled → fail CLOSED
        (WS-24b, 2026-11-06). Before WS-24b the LLM-key fallback kept the
        token non-empty on every real box, so a bare header was refused
        there. With the fallback gone the empty token must refuse it too."""
        monkeypatch.setattr("acb_auth.deps._get_internal_token", lambda: "")
        monkeypatch.setattr("acb_auth.deps._acb_env", lambda: "prod")
        client = TestClient(_bearer_app(None))
        r = client.get("/whoami", headers={
            "Authorization": "Bearer anything",
            "X-User-Email": "dev@fracktal.in",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] is None
        assert data["role"] == "employee"

    def test_bearer_disabled_when_token_empty_stays_open_on_dev(self, monkeypatch):
        """ACB_ENV=dev keeps the old SSO-header trust, so a laptop with no
        token is not bricked. The rule is the one of require_llm_api_auth."""
        monkeypatch.setattr("acb_auth.deps._get_internal_token", lambda: "")
        monkeypatch.setattr("acb_auth.deps._acb_env", lambda: "dev")
        client = TestClient(_bearer_app(None))
        r = client.get("/whoami", headers={
            "Authorization": "Bearer anything",
            "X-User-Email": "dev@fracktal.in",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "dev@fracktal.in"
        assert data["role"] == "employee"

    # ── Domain enforcement under Bearer ────────────────────────────────

    def test_bearer_with_non_fracktal_email_trusts_nextjs(self):
        """Bearer + non-fracktal email → trusts Next.js, keeps email."""
        # The gateway's domain check only downgrades to anonymous in the
        # SSO-only path.  Under Bearer, we trust Next.js to have already
        # validated the domain via Google SSO + NextAuth signIn callback.
        # We still flag the mismatch by keeping the email (not None) so
        # the audit trail is preserved.
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "Authorization": f"Bearer {self.TOKEN}",
            "X-User-Email": "hacker@gmail.com",
            "X-User-Role": "employee",
        })
        assert r.status_code == 200
        data = r.json()
        # Under Bearer we preserve the email even for non-fracktal domains
        # because Next.js already validated it.
        assert data["email"] == "hacker@gmail.com"

    # ── SSO headers without Bearer (direct access) ─────────────────────

    def test_sso_without_bearer_is_anonymous_when_token_set(self):
        """SSO headers WITHOUT the internal Bearer → not trusted (spoofable),
        even for a fracktal.in address, when a gateway token is configured."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "X-User-Email": "dev@fracktal.in",
            "X-User-Role": "employee",
        })
        assert r.status_code == 200
        assert r.json()["email"] is None

    def test_sso_without_bearer_trusts_with_escape_hatch(self, monkeypatch):
        """The rollback escape hatch restores the old SSO-header trust — for a
        token-mismatch emergency only."""
        monkeypatch.setenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", "1")
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "X-User-Email": "dev@fracktal.in",
            "X-User-Role": "employee",
        })
        assert r.status_code == 200
        assert r.json()["email"] == "dev@fracktal.in"

    def test_sso_without_bearer_rejects_non_fracktal(self):
        """SSO headers without Bearer → anonymous (email None) regardless of
        domain when a token is configured; the spoofed role is dropped too."""
        client = TestClient(_bearer_app(self.TOKEN))
        r = client.get("/whoami", headers={
            "X-User-Email": "hacker@gmail.com",
            "X-User-Role": "executive",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] is None           # untrusted → anonymous
        assert data["role"] == "employee"      # spoofed role dropped

