"""A tenant-scoped route reached with no tenant answers JSON, not five words.

⚠️ **1,120 of these in three days, and every one reached the browser as the
plain text `Internal Server Error`.** Starlette's default 500 body is not
JSON, so a client calling `res.json()` gets

    Unexpected token 'I', "Internal S"... is not valid JSON

— a parser error standing in front of the real one. Owner report 2026-09-17,
on the Projects app. The same crash was hitting `/apps/pins` and the chat
message routes, which is what made it obvious the cause was not in Projects.

**The cause, which this file does NOT fix.** `acb_auth.deps` branch 1b answers
a Bearer-matched call carrying no `X-User-Email` with a service context, and
never calls `bind_tenant`. Any tenant-scoped route then raises `TenantUnbound`.
That is why it is intermittent, spread across unrelated apps, and fires for
users whose org membership is healthy. Filed as **H-115**.

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


@pytest.fixture
def client() -> TestClient:
    # `raise_server_exceptions=False` so the handler runs and we read the
    # RESPONSE, rather than the exception being re-raised into the test.
    return TestClient(_app_with_handler(), raise_server_exceptions=False)


class TestTheBodyIsReadable:
    def test_it_is_valid_JSON(self, client):
        """⚠️ The whole point. `res.json()` raised on the old plain-text body,
        and the parse error is what the owner actually saw."""
        r = client.get("/scoped")
        r.json()  # would raise on `Internal Server Error`

    def test_the_body_is_not_the_starlette_default(self, client):
        assert client.get("/scoped").text.strip() != "Internal Server Error"

    def test_it_carries_a_machine_readable_code(self, client):
        # So a client can special-case this without matching on prose.
        assert client.get("/scoped").json()["code"] == "tenant_unbound"

    def test_the_message_does_not_blame_the_reader(self, client):
        """It is our defect, and a person reading it should not go hunting
        through their own account settings."""
        detail = client.get("/scoped").json()["detail"].lower()
        assert "our side" in detail or "fault on our" in detail
        assert "reload" in detail


class TestTheStatusIsUNCHANGED:
    def test_it_is_still_500(self, client):
        """⚠️ Deliberate, and the reason is worth keeping.

        A 401 would read as *"sign in again"*, and the proxy would act on it —
        logging people out over a header that was merely late is a worse
        failure than the one being fixed. Reaching a tenant-scoped route with
        no tenant is OUR defect, and 5xx is the honest class for it.
        """
        assert client.get("/scoped").status_code == 500

    def test_it_is_not_a_4xx(self, client):
        code = client.get("/scoped").status_code
        assert not 400 <= code < 500, (
            "a 4xx here tells the client the CALLER is at fault, and a 401"
            " specifically would sign people out over a late header"
        )


def test_the_gateway_itself_registers_it():
    """The handler exists on the real app, not only in this test's fixture."""
    from gateway.main import app

    assert TenantUnbound in app.exception_handlers
