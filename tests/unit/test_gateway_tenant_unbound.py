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
