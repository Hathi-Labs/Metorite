"""A tenant-scoped route reached with no tenant answers JSON, not five words.

⚠️ **1,120 of these in three days, and every one reached the browser as the
plain text `Internal Server Error`.** Starlette's default 500 body is not
JSON, so a client calling `res.json()` gets

    Unexpected token 'I', "Internal S"... is not valid JSON

— a parser error standing in front of the real one. Owner report 2026-09-17,
on the Projects app. The same crash was hitting `/apps/pins` and the chat
message routes, which is what made it obvious the cause was not in Projects.

⚠️ **THE CAUSE THIS FILE FIRST NAMED WAS WRONG.** It said branch 1b answered a
Bearer-matched call carrying no `X-User-Email` with a service context. Measured
on the box 2026-09-18, every failing request CARRIED that header — branch 1a
ran — they arrived at :23 seconds past every minute for nine hours, which is
one browser tab polling and not scattered traffic, and the address belonged to
a person whose organization did not exist yet. The membership was created at
17:55:57 UTC; the last `TenantUnbound` is 18:06, and there have been none since.

**So the real defect is the ordinary onboarding state**: a signed-in person who
resolves to NO organization got a 500 on every tenant-scoped route, telling them
it was our fault and to reload. `gateway.main._tenant_unbound` now answers that
case with **403 `no_organization`**, and keeps the 500 for a call with no user
header, which really is a job or a service reaching somewhere it should not.

What this file pins is the SYMPTOM being survivable: a client gets a readable
error instead of a parse failure, and the log names the path so the cause can
be found.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from acb_common.db import TenantUnbound
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app_with_handler() -> FastAPI:
    """The gateway's handler, mounted on a bare app.

    ⚠️ Imported from `gateway.main` rather than copied. A transcribed handler
    agrees with itself forever while the real one drifts — the same rule the
    analytics SQL builders follow.
    """
    from gateway.main import _tenant_unbound

    app = FastAPI()
    app.add_exception_handler(TenantUnbound, _tenant_unbound)

    @app.get("/scoped")
    def scoped() -> dict:
        raise TenantUnbound("no tenant bound — test")

    return app


#: The header the handler splits on. A request carrying it is a PERSON.
WHO = {"X-User-Email": "someone@example.com"}


@pytest.fixture
def client() -> TestClient:
    # `raise_server_exceptions=False` so the handler runs and we read the
    # RESPONSE, rather than the exception being re-raised into the test.
    return TestClient(_app_with_handler(), raise_server_exceptions=False)


class TestTheBodyIsReadable:
    """True of BOTH answers — a parse error in front of the real one was the
    thing the owner actually saw, and neither case may bring it back."""

    @pytest.mark.parametrize("headers", [{}, WHO], ids=["service", "person"])
    def test_it_is_valid_JSON(self, client, headers):
        client.get("/scoped", headers=headers).json()  # raised on plain text

    @pytest.mark.parametrize("headers", [{}, WHO], ids=["service", "person"])
    def test_the_body_is_not_the_starlette_default(self, client, headers):
        assert client.get("/scoped", headers=headers).text.strip() != "Internal Server Error"

    @pytest.mark.parametrize(
        "headers,code",
        [({}, "tenant_unbound"), (WHO, "no_organization")],
        ids=["service", "person"],
    )
    def test_it_carries_a_machine_readable_code(self, client, headers, code):
        # So a client can special-case this without matching on prose — and so
        # the two cases are distinguishable without reading the status.
        assert client.get("/scoped", headers=headers).json()["code"] == code


class TestAPersonIsToldTheTruth:
    """⚠️ The correction. A signed-in person with no organization is NOT
    looking at a server fault, and the old body told them they were."""

    def test_it_is_403_and_not_500(self, client):
        assert client.get("/scoped", headers=WHO).status_code == 403

    def test_it_is_NOT_401(self, client):
        """401 reads as *"sign in again"*, and signing out changes nothing
        about a missing membership. It would also be the one status a proxy
        is most likely to act on."""
        assert client.get("/scoped", headers=WHO).status_code != 401

    def test_it_does_not_claim_to_be_our_fault(self, client):
        """The exact sentence that was wrong. `test_the_message_does_not_blame
        _the_reader` used to REQUIRE this wording, and it was requiring a lie
        for the only case that actually fires."""
        detail = client.get("/scoped", headers=WHO).json()["detail"].lower()
        assert "our side" not in detail
        assert "fault on our" not in detail

    def test_it_says_what_to_do_about_it(self, client):
        detail = client.get("/scoped", headers=WHO).json()["detail"].lower()
        assert "organization" in detail
        assert "administrator" in detail or "admin" in detail


class TestAServiceCallKeepsIts500:
    """A job or consumer reaching a tenant-scoped route IS our defect, and
    there is no person to tell anything to."""

    def test_it_is_still_500(self, client):
        assert client.get("/scoped").status_code == 500

    def test_it_is_not_a_4xx(self, client):
        code = client.get("/scoped").status_code
        assert not 400 <= code < 500, (
            "a 4xx here would tell a JOB that its caller is at fault, when"
            " nothing reached a tenant-scoped route on a person's behalf"
        )

    def test_the_message_does_not_blame_the_reader(self, client):
        detail = client.get("/scoped").json()["detail"].lower()
        assert "our side" in detail or "fault on our" in detail


def test_the_gateway_itself_registers_it():
    """The handler exists on the real app, not only in this test's fixture."""
    from gateway.main import app

    assert TenantUnbound in app.exception_handlers


# ── The third case: the directory could not be READ ────────────────────────


def _app_with_handler_unreachable(monkeypatch) -> FastAPI:
    """The same handler, with this request's identity read marked FAILED.

    ⚠️ The flag is set through `acb_auth.access`'s own ContextVar rather than
    by patching `identity_read_failed`, so the test exercises the real seam
    the gateway reads.
    """
    import acb_auth.access as access_mod

    access_mod._identity_read_failed.set(True)
    return _app_with_handler()


@pytest.fixture
def unavailable(monkeypatch) -> TestClient:
    return TestClient(
        _app_with_handler_unreachable(monkeypatch), raise_server_exceptions=False,
    )


@pytest.fixture(autouse=True)
def _reset_identity_flag():
    """⚠️ Per-test reset. The ContextVar outlives one test inside a worker,
    and a leaked `True` turns every later 403 assertion into a 503."""
    import acb_auth.access as access_mod

    token = access_mod._identity_read_failed.set(False)
    yield
    access_mod._identity_read_failed.reset(token)


class TestAFailedReadIsNotAnAccusation:
    """🔴 The bug this class exists for reached the owner's screen.

    `resolve_identity` swallowed a failed read and returned `(None, None)` —
    the same value as "nobody matched". So the 403 above fired, and a
    signed-in owner looking at their own board was told:

        "This account is not a member of any organization"

    Measured on production 2026-09-20, six hours: eleven
    `auth.identity_resolve_failed` and ten `tenant.unbound`, every one of the
    latter carrying `tenant_had_user_header: True`. Underneath all of them
    `(EMAXCONNSESSION) max clients reached in session mode — max clients are
    limited to pool_size: 15`.

    A connection-pool ceiling was being reported as a membership problem, and
    the instruction it gave — go to an administrator — could not have helped.
    """

    def test_it_is_503_and_not_403(self, unavailable):
        # 403 is the accusation. The whole fix is that these two differ.
        assert unavailable.get("/scoped", headers=WHO).status_code == 503

    def test_it_is_not_a_4xx_at_all(self, unavailable):
        # Any 4xx says "your request was wrong". It was not.
        assert unavailable.get("/scoped", headers=WHO).status_code // 100 == 5

    def test_it_never_says_the_member_has_no_organization(self, unavailable):
        body = unavailable.get("/scoped", headers=WHO).json()["detail"].lower()
        assert "not a member" not in body
        assert "no organization" not in body
        assert "administrator" not in body

    def test_it_says_the_account_is_fine(self, unavailable):
        # The first thought on any auth-shaped error is "is my account
        # broken". Answer it before they ask.
        body = unavailable.get("/scoped", headers=WHO).json()["detail"].lower()
        assert "your account is fine" in body

    def test_it_tells_them_to_retry(self, unavailable):
        body = unavailable.get("/scoped", headers=WHO).json()["detail"].lower()
        assert "try again" in body

    def test_it_carries_Retry_After(self, unavailable):
        # A 503 without it is a dead end for anything automated.
        assert unavailable.get("/scoped", headers=WHO).headers.get("Retry-After")

    def test_the_body_is_readable_JSON(self, unavailable):
        got = unavailable.get("/scoped", headers=WHO).json()
        assert got["code"] == "identity_unavailable"

    def test_it_does_not_leak_the_driver_message(self, unavailable):
        # ⚠️ The asyncpg text names the pooler host and its limits.
        body = unavailable.get("/scoped", headers=WHO).json()["detail"]
        for leak in ("pool_size", "EMAXCONNSESSION", "asyncpg", "supabase"):
            assert leak.lower() not in body.lower()

    def test_a_SERVICE_call_with_a_failed_read_is_also_503(self, unavailable):
        # No user header. It is still our fault and still retryable, so the
        # unreachable branch wins over the 500 — the 500 means "a job reached
        # a tenant route", which is a different defect.
        assert unavailable.get("/scoped").status_code == 503


class TestTheOrdinaryCasesAreUNCHANGED:
    """⚠️ The flag must change nothing when the read WORKED.

    Somebody genuinely between "signs in" and "an admin adds them" is the
    ordinary onboarding state, and the 403 is correct for them. If the flag
    leaked, every one of those would become "try again in a moment" — advice
    that can never come true.
    """

    def test_a_real_non_member_still_gets_403(self, client):
        assert client.get("/scoped", headers=WHO).status_code == 403

    def test_a_service_call_still_gets_500(self, client):
        assert client.get("/scoped").status_code == 500
