"""CP-2b — ``POST /signin/resolve`` itself: the ROUTE, not the module under it.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2b clause 11 · §6(e) ·
§6(g)'s *"flag ON is a claim, and the box is held to it"* · §6(j) · D33.1.

⚠️ **This file exists because nothing tested the route at all**, and that gap
cost a P0 (independent review, 2026-08-18, finding **F5**). The module's
:func:`acb_auth.console_resolve.resolve_for_signin` fails **open** when the box
is not wired — it returns ``admit=True, source="unwired"``, which is what "ships
dark" means for a deployment nobody has configured. The route passed that answer
through, so:

    Next's ``CUSTOMER_CONSOLE_RESOLVE_ENABLED=true`` (one container, one env
    file) + the gateway's ``CUSTOMER_CONSOLE_URL`` / deployment key **missing**
    (a different container, a different env file — the ordinary topology)
    = every sign-in admitted, no seat allocated, nothing asked, and **no log
    line at all**, because the route only logged refusals.

That is the F1 class one hop down: the half-provisioned box that silently
admits. §6(g) and ``auth.ts`` both promise the opposite in as many words, so the
promise is now kept where it is checkable — **reaching this route at all means
somebody declared the box wired**, and a box that cannot ask refuses.

⚠️ **DB-free on purpose, like `test_console_dependency_boundary.py`.** Folded
into ``test_deployment_resolve_cache.py`` these would skip whenever
``TENANT_LADDER_DATABASE_URL`` is unset, and a structural fence that skips is a
fence that was never there (§6(i)(4)). Nothing here opens a session: the unwired
path returns before any query, and the wired path's module is stubbed.

The app is assembled the way ``gateway/main.py`` assembles it — the router
mounted under an app-wide ``require_authenticated``, never in ``PUBLIC_ROUTES``
— in the idiom ``test_default_deny_auth.py`` established, so the auth posture is
exercised rather than asserted about.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from acb_auth import get_current_user  # noqa: E402
from acb_auth.console_resolve import (  # noqa: E402
    CONSOLE_UNAVAILABLE,
    ResolveDecision,
)
from acb_auth.deps import require_authenticated  # noqa: E402
from acb_auth.roles import UserContext, UserRole  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gateway.routes import signin as route  # noqa: E402

PERSON = UserContext(email="ada@customer.example", role=UserRole.EMPLOYEE)
ANON = UserContext(email=None, role=UserRole.EMPLOYEE)

CONSOLE_URL = "https://console.invalid"
DEPLOYMENT_KEY = "cc_depl_fixture_notarealsecret"


def _client(user: UserContext = PERSON) -> TestClient:
    """The route mounted exactly as ``gateway/main.py`` mounts it."""
    app = FastAPI(dependencies=[require_authenticated(public=())])
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class Recorder:
    """Captures structured log calls, with the LEVEL, so a fence can assert it."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def warning(self, event: str, **kw) -> None:
        self.events.append(("warning", event, kw))

    def error(self, event: str, **kw) -> None:
        self.events.append(("error", event, kw))

    def info(self, event: str, **kw) -> None:
        self.events.append(("info", event, kw))

    def names(self) -> list[str]:
        return [name for _, name, _ in self.events]


class Spy:
    """A stand-in for the resolve module that RECORDS whether it was consulted."""

    def __init__(self, decision: ResolveDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, email: str, display_name: str = "") -> ResolveDecision:
        self.calls.append((email, display_name))
        return self.decision


@pytest.fixture
def unwired(monkeypatch):
    """A gateway whose Console settings are genuinely absent.

    The real :func:`acb_auth.console_resolve.is_wired` is used — env plus a
    cleared settings cache — rather than a patched predicate, because the
    subject is what happens on a box where nobody filled the env in.
    """
    from acb_common.settings import get_settings

    monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
    monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def wired(monkeypatch):
    """A gateway that has been told how to reach a Console."""
    from acb_common.settings import get_settings

    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


# ══ F5 — a half-provisioned gateway REFUSES, and says so ═════════════════════

class TestAHalfProvisionedBoxRefuses:
    def test_an_unwired_gateway_refuses_instead_of_admitting(self, unwired):
        """The whole finding, end to end, with the REAL module underneath.

        No stub: ``resolve_for_signin`` runs and returns its fail-open
        ``unwired`` answer, exactly as it does in production, and the route is
        what must not pass it through. Anybody reaching this route asserted the
        box was wired; it is not, so it cannot answer, so it refuses — with the
        service-unavailable copy, never "access denied" (D33.1): the person has
        done nothing wrong.
        """
        response = _client().post("/signin/resolve", json={"display_name": "Ada L"})

        assert response.status_code == 200, "the refusal is the ANSWER, not an error"
        assert response.json() == {
            "admit": False,
            "code": CONSOLE_UNAVAILABLE,
            "source": "unwired",
            # WS-31 CP-2c: wire-shape parity. An unwired box is not the zero-org
            # case, so the self-serve funnel must stay shut — always False here.
            "signup_eligible": False,
        }

    def test_the_unwired_refusal_never_consults_the_seat_allocating_module(
        self, unwired, monkeypatch
    ):
        """It refuses BEFORE the hop, not after reading its answer.

        The module's unwired branch may keep failing open — it has other
        callers and no callers, and "ships dark" is its contract. What must not
        happen is the route asking a question it already knows the box cannot
        answer and then trusting the reply.
        """
        spy = Spy(ResolveDecision(admit=True, source="unwired"))
        monkeypatch.setattr(route, "resolve_for_signin", spy)

        body = _client().post("/signin/resolve", json={}).json()

        assert body["admit"] is False
        assert spy.calls == [], "the route consulted the module it cannot reach"

    def test_the_unwired_refusal_leaves_a_LOG_LINE_at_error(
        self, unwired, monkeypatch
    ):
        """Zero log signal was half of what made this survivable in production.

        A silent admit and a silent refusal are both invisible; an operator
        watching a sign-in outage needs the box to say *why*, and this is a
        provisioning error rather than a person's problem — hence ``error``,
        not ``warning``.
        """
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)

        _client().post("/signin/resolve", json={})

        assert recorder.names().count("signin.resolve_unwired") == 1
        level, _, fields = recorder.events[0]
        assert level == "error"
        assert fields, "the line carries no detail an operator could act on"


# ══ The wired path is untouched by the guard ═════════════════════════════════

class TestAWiredBoxStillAsks:
    def test_a_wired_gateway_passes_the_modules_answer_through(
        self, wired, monkeypatch
    ):
        """Admit stays admit, and the code and source ride along unchanged."""
        spy = Spy(
            ResolveDecision(admit=True, slug="acme", source="console")
        )
        monkeypatch.setattr(route, "resolve_for_signin", spy)

        body = _client().post(
            "/signin/resolve", json={"display_name": "Ada L"}
        ).json()

        assert body == {
            "admit": True,
            "code": None,
            "source": "console",
            "signup_eligible": False,
        }
        assert spy.calls == [("ada@customer.example", "Ada L")]

    def test_a_wired_refusal_is_a_200_carrying_the_code(self, wired, monkeypatch):
        """A 4xx would make "the Console says no" look like "the route broke".

        Which is the one distinction this call site exists to draw, so the
        refusal travels as a body and the status stays 200.
        """
        spy = Spy(
            ResolveDecision(admit=False, code=CONSOLE_UNAVAILABLE, source="unreachable")
        )
        monkeypatch.setattr(route, "resolve_for_signin", spy)
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)

        response = _client().post("/signin/resolve", json={})

        assert response.status_code == 200
        assert response.json() == {
            "admit": False,
            "code": CONSOLE_UNAVAILABLE,
            "source": "unreachable",
            "signup_eligible": False,
        }
        assert recorder.names() == ["signin.resolve_refused"]

    def test_the_route_passes_signup_eligible_through_unchanged(
        self, wired, monkeypatch
    ):
        """WS-31 CP-2c: the zero-org signal rides the wire, verbatim.

        The route neither derives nor re-computes ``signup_eligible`` — it hands
        the module's decision to the BFF's limbo branch untouched. Here the
        module answers the zero-org refusal (``console-empty``, the ONLY True
        case), and the route's JSON must carry ``signup_eligible: True``. Every
        other refusal leaves it False (the two dicts above), so the funnel opens
        for the org-less person alone.
        """
        spy = Spy(
            ResolveDecision(
                admit=False,
                code="AccessDenied",
                source="console-empty",
                signup_eligible=True,
            )
        )
        monkeypatch.setattr(route, "resolve_for_signin", spy)

        body = _client().post("/signin/resolve", json={}).json()

        assert body["signup_eligible"] is True
        assert body["admit"] is False
        assert body["code"] == "AccessDenied"


# ══ The posture the route inherits rather than declares ══════════════════════

class TestTheRoutesPosture:
    def test_an_anonymous_caller_never_reaches_the_handler(self, wired, monkeypatch):
        """Authenticated by construction, and NOT in ``PUBLIC_ROUTES``.

        Adding it there to "make it reachable" is forbidden by name (root
        ``AGENTS.md`` constraint 10); the BFF reaches it with the internal
        bearer it already holds.
        """
        spy = Spy(ResolveDecision(admit=True, source="console"))
        monkeypatch.setattr(route, "resolve_for_signin", spy)

        response = _client(ANON).post("/signin/resolve", json={})

        assert response.status_code == 401
        assert spy.calls == []

    def test_the_address_comes_from_the_context_never_from_the_body(
        self, wired, monkeypatch
    ):
        """R11. The body may say anything; it is not an identity.

        ``SignInResolveRequest`` carries ``display_name`` and nothing else, so
        an ``email`` in the body is dropped rather than believed — the same
        reason the Customer Console 400s a deployment key that names an
        ``org_slug``.
        """
        spy = Spy(ResolveDecision(admit=True, source="console"))
        monkeypatch.setattr(route, "resolve_for_signin", spy)

        _client().post(
            "/signin/resolve",
            json={"email": "attacker@elsewhere.example", "display_name": "x"},
        )

        assert spy.calls == [("ada@customer.example", "x")]
