"""MCP OAuth: the signed state and the pending flow (WS-54 IN-8a, row O3).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.4, callback steps 1
and 2. Each case answers 400 ``invalid_state`` and sends NO token request:

* a replayed state (its pending flow was taken once, by GETDEL);
* a state of org A, used by a member of org B;
* a state of one admin, used by another admin of the same org;
* an expired state (ten minutes);
* a state signed for ``crm.zoho.connect``, with the same body;
* a forged state, and a state with no pending flow.

Mutations M3, M4 and M12 of ``test_mcp_oauth_flow.py`` fail here.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from tests.unit import _mcp_oauth_fakes as f

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
ADMIN = "admin@a.test"


@pytest.fixture
def redis(monkeypatch) -> f.FakeRawRedis:
    return f.configure(monkeypatch)


@pytest.fixture
def world(monkeypatch, redis) -> f.World:
    return f.install(monkeypatch, f.World())


@pytest.fixture
def reg(monkeypatch, world) -> f.Registry:
    return f.fake_registry(monkeypatch, ORG_A)


def _admin(email: str = ADMIN, org: str = ORG_A) -> Any:
    return f.user(email, org)


def _tokens(world: f.World) -> int:
    return len([r for r in world.requests if r.url.path == "/token"])


async def _refused(who: Any, code: str, state: str, world: f.World) -> None:
    before = _tokens(world)
    res = await f.finish(who, code, state, iss=f.ISSUER)
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == "invalid_state"
    assert _tokens(world) == before, "a refused state sent a token request"


async def test_a_replayed_state_is_400_and_sends_no_token_request(world, reg) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302 and _tokens(world) == 1
    await _refused(_admin(), code, state, world)
    assert len(reg.attached) == 1


async def test_a_state_of_another_org_is_400(world, reg, redis) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    # The tenant prefix of Redis is a second wall: org B holds no pending flow
    # under this nonce. Plant one, so only the org of the state stops B.
    (key, raw), = redis.data.items()
    assert ORG_A in key
    redis.data[key.replace(ORG_A, ORG_B)] = raw
    # The same email in another org, and another admin of another org.
    for who in (_admin(ADMIN, ORG_B), _admin("admin@b.test", ORG_B)):
        await _refused(who, code, state, world)
    # The pending flow was not taken, so the admin of A still finishes.
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302 and reg.attached[0]["org"] == ORG_A


async def test_a_state_of_another_member_is_400(world, reg) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    await _refused(_admin("other-admin@a.test", ORG_A), code, state, world)
    assert reg.attached == []
    # The refusal came before the pending flow was taken, so the admin who
    # started the consent still finishes it.
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302 and reg.attached[0]["connected_by"] == ADMIN


async def test_an_expired_state_is_400(monkeypatch, world, reg) -> None:
    from gateway.routes import integrations_mcp_oauth as routes
    from gateway.routes.email.transport import signing

    _, code, state = await f.start_and_consent(_admin(), world)
    later = time.time() + signing.OAUTH_STATE_TTL_SECONDS + 5

    def _late(token: Any, *, purpose: str) -> Any:
        return signing.verify_state(token, purpose=purpose, now=later)

    monkeypatch.setattr(routes, "verify_state", _late)
    await _refused(_admin(), code, state, world)


def _resign(state: str, purpose: str) -> str:
    """The same body, with the MAC of another purpose."""
    from gateway.routes.email.transport import signing

    body = state.split(".", 1)[0]
    return f"{body}.{signing._mac(signing._secret(), purpose, body)}"


async def test_a_state_of_another_purpose_is_400(world, reg) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    zoho = _resign(state, "crm.zoho.connect")
    assert zoho != state
    await _refused(_admin(), code, zoho, world)
    # The control: the body re-signed with the right purpose still finishes.
    same = _resign(state, "integrations.mcp.connect")
    assert same == state
    res = await f.finish(_admin(), code, same, iss=f.ISSUER)
    assert res.status_code == 302


async def test_a_forged_state_and_a_missing_state_are_400(world, reg) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    body, mac = state.split(".", 1)
    await _refused(_admin(), code, f"{body}x.{mac}", world)
    await _refused(_admin(), code, "not-a-state", world)
    res = await f.finish(_admin(), code, None, iss=f.ISSUER)
    assert res.status_code == 400 and res.json()["detail"] == "invalid_state"


async def test_a_good_state_with_no_pending_flow_is_400(world, reg, redis) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    redis.data.clear()  # the ten minutes of Redis are over
    await _refused(_admin(), code, state, world)


async def test_the_pending_flow_is_under_the_org_and_the_nonce(world, reg, redis) -> None:
    from gateway.routes.email.transport import signing

    _, _, state = await f.start_and_consent(_admin(), world)
    payload = signing.verify_state(state, purpose="integrations.mcp.connect")
    assert payload is not None and payload["org"] == ORG_A and payload["member"] == ADMIN
    (key, raw), = redis.data.items()
    assert ORG_A in key and payload["nonce"] in key
    record = json.loads(raw)
    assert record["member"] == ADMIN and record["name"] == "notion"
    # The verifier and the secret are in the pending flow, never in the state.
    decoded = signing._unb64(state.split(".", 1)[0]).decode()
    assert record["verifier"] not in decoded and "verifier" not in decoded
    assert f.SENTINEL_SECRET not in raw
