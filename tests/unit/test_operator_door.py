"""The gateway operator door (CP-2g) — the two-token ladder, on the REAL app.

Review round 1's P0: the first draft of this suite built a bare
``FastAPI(); include_router(router)`` and was green while the real gateway —
whose APP-LEVEL ``require_authenticated`` dependency consumes the
``Authorization`` header — refused every call before the door's own gate ran.
A fence that tests a different app is not a fence. Every test here drives
``gateway.main.app``, so the app-level gate, the router mounting (past
``main.py``'s ``except Exception: pass`` include idiom) and the door's own
ladder are all in the measured path.

The design under test: BOTH tokens, always —
``Authorization: Bearer <GATEWAY_INTERNAL_TOKEN>`` clears the app-level gate,
``X-Operator-Token: <GATEWAY_OPERATOR_TOKEN>`` clears the door. The purge's
own semantics are R8-fenced in ``test_org_purge_tenant.py``; here the purge is
monkeypatched and the ladder is the subject: missing app auth → 401 (app
gate), operator token unset → 503 (ship-dark), wrong operator token → 401,
mismatched confirm → 400, purge failure → 502 carrying the cause — and every
refusal happens BEFORE the purge runs.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import acb_auth.offboard as offboard
from fastapi.testclient import TestClient

INTERNAL = "test-internal-token"
OPERATOR = "test-gateway-operator-token"


@pytest.fixture
def calls(monkeypatch):
    """Record purge invocations; answer a canned receipt."""
    made: list[str] = []

    async def fake_purge(*, slug: str):
        made.append(slug)
        return {
            "slug": slug,
            "already_absent": False,
            "deleted": {"organization": 1},
        }

    monkeypatch.setattr(offboard, "purge_tenant_organization", fake_purge)
    return made


@pytest.fixture
def client(monkeypatch, calls):
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", OPERATOR)
    from gateway.main import app

    return TestClient(app)


def _delete(client, slug="acme", *, confirm=None, bearer=INTERNAL,
            operator=OPERATOR):
    headers = {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if operator is not None:
        headers["X-Operator-Token"] = operator
    return client.delete(
        f"/internal/operator/organizations/{slug}",
        params={"confirm": slug if confirm is None else confirm},
        headers=headers,
    )


class TestTheTwoTokenLadder:
    def test_the_door_is_mounted_and_reachable_on_the_real_app(self, client):
        """The P0 fence: the route resolves on gateway.main.app (not a 404,
        which is what a failed import under the include's bare except would
        produce) and the door's OWN voice answers once both tokens present."""
        r = _delete(client, "hathilabs")
        assert r.status_code == 200, r.text

    def test_without_the_app_level_bearer_the_gateway_refuses_first(
        self, client, calls
    ):
        """The internal token is REQUIRED: the operator token alone must not
        bypass the gateway's ordinary machine auth."""
        r = _delete(client, bearer=None)
        assert r.status_code == 401, r.text
        # The refusal is the APP gate's, not the door's — the door never ran.
        assert "operator token" not in r.json()["detail"]
        assert calls == []

    def test_the_internal_token_alone_is_not_an_org_destroy_credential(
        self, client, calls
    ):
        """The other half of the two-token argument: a caller holding only
        the gateway's ordinary bearer (whose unprovisioned-box fallback is
        LITELLM_MASTER_KEY — a credential agents hold) is refused by the
        door's own gate."""
        assert _delete(client, operator=None).status_code == 401
        assert _delete(client, operator="not-it").status_code == 401
        assert calls == []

    def test_ship_dark_an_unset_operator_token_answers_503(
        self, client, monkeypatch, calls
    ):
        monkeypatch.delenv("GATEWAY_OPERATOR_TOKEN", raising=False)
        r = _delete(client)
        assert r.status_code == 503, r.text
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
