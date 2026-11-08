"""The rate limit of the React artifact compile (task 2432, F1 of STU-L0).

``POST /artifact-compile/slot`` counts one compile of the session's member in
the tenant Redis wrapper. These cases run the real route against a stubbed
tenant Redis, as ``test_chat_transcribe.py`` does.

What must hold:

* A member gets 204 up to :data:`COMPILES_PER_WINDOW`, then 429 with
  ``Retry-After`` and one sentence that says when to try again.
* The counter is the member's, under the session's org, through
  ``tenant_redis.key``. Another member, or another org, has its own count.
* An outside agent, a service caller and a session with no org get 403.
* A store that does not answer refuses (503): the limit fails closed.
* The route is registered in the gateway app.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import tenant_redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import artifact_compile as ac

_ROOT = Path(__file__).resolve().parents[2]

ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
ME = "Ana@Acme.test"


class FakeRedis:
    """``incr``, ``expire`` and ``ttl`` of the tenant wrapper, by key string."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.down = False

    def _k(self, k: Any) -> str:
        assert isinstance(k, tenant_redis.TenantKey), "a raw key string"
        return k.value

    async def incr(self, k: Any) -> int:
        if self.down:
            raise ConnectionError("redis is down")
        key = self._k(k)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, k: Any, seconds: int) -> bool:
        self.ttls[self._k(k)] = seconds
        return True

    async def ttl(self, k: Any) -> int:
        return self.ttls.get(self._k(k), -1)


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: fake)
    return fake


def _user(email: str | None = ME, org: str | None = ORG, **kw: Any) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:chat"], roles=["member"]), **kw)


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(ac.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _post(client: TestClient) -> httpx.Response:
    return client.post("/artifact-compile/slot")


def test_a_member_gets_a_slot_and_the_window_starts_at_the_first(redis) -> None:
    res = _post(_client(_user()))
    assert res.status_code == 204
    key = f"cc:{ORG}:artifact_compile:member:ana@acme.test"
    assert redis.counts == {key: 1}
    assert redis.ttls == {key: ac.WINDOW_S}


def test_the_cap_refuses_with_429_retry_after_and_one_sentence(redis) -> None:
    """Mutation: drop the cap check -> the extra call answers 204 -> red."""
    client = _client(_user())
    for _ in range(ac.COMPILES_PER_WINDOW):
        assert _post(client).status_code == 204
    res = _post(client)
    assert res.status_code == 429
    assert res.headers["retry-after"] == str(ac.WINDOW_S)
    detail = res.json()["detail"]
    assert detail["code"] == "cap"
    assert detail["message"] == (
        f"You built {ac.COMPILES_PER_WINDOW} artifacts in the last 10 minutes, "
        "which is the limit. Try again in 10 minutes."
    )


def test_a_counter_that_lost_its_expiry_gets_one_again(redis) -> None:
    key = f"cc:{ORG}:artifact_compile:member:ana@acme.test"
    redis.counts[key] = ac.COMPILES_PER_WINDOW + 5
    res = _post(_client(_user()))
    assert res.status_code == 429
    assert redis.ttls[key] == ac.WINDOW_S


def test_each_member_and_each_org_has_its_own_count(redis) -> None:
    key = f"cc:{ORG}:artifact_compile:member:ana@acme.test"
    redis.counts[key] = ac.COMPILES_PER_WINDOW
    assert _post(_client(_user())).status_code == 429
    assert _post(_client(_user(email="bo@acme.test"))).status_code == 204
    assert _post(_client(_user(org=OTHER_ORG))).status_code == 204


@pytest.mark.parametrize("user", [
    _user(email=None),
    _user(email="system:internal"),
    _user(org=None),
])
def test_no_person_or_no_org_gets_403_and_no_count(redis, user) -> None:
    res = _post(_client(user))
    assert res.status_code == 403
    assert res.json()["detail"]["message"] == ac.NO_MEMBER
    assert redis.counts == {}


def test_an_outside_agent_gets_403(redis) -> None:
    from acb_auth.roles import AgentScope

    res = _post(_client(_user(agent=AgentScope(connection_id="conn-1"))))
    assert res.status_code == 403
    assert redis.counts == {}


def test_a_store_that_does_not_answer_fails_closed(redis) -> None:
    """Mutation: answer 204 on an outage -> red."""
    redis.down = True
    res = _post(_client(_user()))
    assert res.status_code == 503
    assert res.headers["retry-after"] == "2"
    assert res.json()["detail"]["message"] == ac.UNAVAILABLE


def test_the_gateway_app_serves_the_route() -> None:
    src = (_ROOT / "apps/services/gateway/gateway/main.py").read_text(encoding="utf-8")
    assert "from gateway.routes.artifact_compile import router" in src
    assert "app.include_router(_artifact_compile_router)" in src


def test_the_workbench_route_takes_the_slot_before_esbuild() -> None:
    """The BFF calls this path. A rename on one side alone breaks the limit."""
    guards = (_ROOT / "workbench/control_plane/src/lib/compileGuards.ts").read_text(encoding="utf-8")
    assert 'SLOT_PATH = "/artifact-compile/slot"' in guards
    route = (
        _ROOT / "workbench/control_plane/src/app/api/artifacts/compile/route.ts"
    ).read_text(encoding="utf-8")
    assert route.index("await takeSlot()") < route.index("await compileArtifact(")
