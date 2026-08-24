"""The gateway operator door (CP-2g) — auth, confirm echo, ship-dark, relay.

Hermetic, deliberately: the door itself is pure wiring (token check → confirm
check → call the purge → relay the receipt or the failure), and the purge's
own semantics are R8-fenced in ``test_org_purge_tenant.py``. What must hold
HERE is the refusal ladder: unset token → 503 (ship-dark), wrong token → 401,
mismatched confirm → 400, purge failure → 502 with the cause in the detail —
and that a refusal happens BEFORE the purge runs.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import acb_auth.offboard as offboard
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.operator import router

TOKEN = "test-gateway-operator-token"


@pytest.fixture
def calls(monkeypatch):
    """Record purge invocations; answer a canned receipt."""
    made: list[str] = []

    async def fake_purge(*, slug: str):
        made.append(slug)
        return {"slug": slug, "already_absent": False, "deleted": {"organization": 1}}

    monkeypatch.setattr(offboard, "purge_tenant_organization", fake_purge)
    return made


@pytest.fixture
def client(monkeypatch, calls):
    monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _delete(client, slug="acme", *, confirm=None, token=TOKEN):
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return client.delete(
        f"/internal/operator/organizations/{slug}",
        params={"confirm": slug if confirm is None else confirm},
        headers=headers,
    )


class TestTheRefusalLadder:
    def test_ship_dark_an_unset_token_answers_503_and_purges_nothing(
        self, monkeypatch, calls
    ):
        monkeypatch.delenv("GATEWAY_OPERATOR_TOKEN", raising=False)
        app = FastAPI()
        app.include_router(router)
        r = _delete(TestClient(app))
        assert r.status_code == 503, r.text
        assert calls == []

    def test_a_wrong_token_is_401_and_purges_nothing(self, client, calls):
        assert _delete(client, token="not-it").status_code == 401
        assert _delete(client, token=None).status_code == 401
        assert calls == []

    def test_a_mismatched_confirm_is_400_and_purges_nothing(self, client, calls):
        r = _delete(client, confirm="other")
        assert r.status_code == 400, r.text
        assert calls == []

    def test_a_failing_purge_is_a_502_that_carries_the_cause(
        self, client, monkeypatch
    ):
        async def boom(*, slug: str):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(offboard, "purge_tenant_organization", boom)
        r = _delete(client)
        assert r.status_code == 502, r.text
        assert "the database went away" in r.json()["detail"]


class TestTheHappyPath:
    def test_the_receipt_is_relayed_and_the_slug_reaches_the_purge(
        self, client, calls
    ):
        r = _delete(client, "hathilabs")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] == {"organization": 1}
        assert calls == ["hathilabs"]
