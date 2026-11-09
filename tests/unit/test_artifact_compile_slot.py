"""The rate limit of the React artifact compile (task 2432, F1 of STU-L0).

``POST /artifact-compile/slot`` counts one unit for each compile of the
session's member in the tenant Redis wrapper. ``POST /artifact-compile/charge``
adds ``TIMEOUT_CHARGE`` units for a compile that the workbench had to kill.
These cases run the real routes against a stubbed tenant Redis, as
``test_chat_transcribe.py`` does.

What must hold:

* A member gets 204 up to :data:`COMPILES_PER_WINDOW` units, then 429 with
  ``Retry-After`` and one sentence that says when to try again.
* A killed compile costs ``1 + TIMEOUT_CHARGE`` units, so a member who keeps
  the workbench's build slot busy with slow sources reaches the cap after
  about 10 kills.
* The counter is the member's, under the session's org, through
  ``tenant_redis.key``. Another member, or another org, has its own count.
* A counter with no expiry gets one, so it never pins a member for good.
* An outside agent, a service caller and a session with no org get 403.
* A store that does not answer gives 503 here. The workbench then lets the
  compile go on (fail open, the BFF half is in ``route.test.ts``).
* The REAL gateway app serves both routes. The import has no ``try``.
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
KEY = f"cc:{ORG}:artifact_compile:member:ana@acme.test"


class FakeRedis:
    """``incr``, ``expire`` and ``ttl`` of the tenant wrapper, by key string.

    ``ttl`` answers -1 for a key with no expiry, as Redis does.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.down = False

    def _k(self, k: Any) -> str:
        assert isinstance(k, tenant_redis.TenantKey), "a raw key string"
        return k.value

    async def incr(self, k: Any, amount: int = 1) -> int:
        if self.down:
            raise ConnectionError("redis is down")
        key = self._k(k)
        self.counts[key] = self.counts.get(key, 0) + amount
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


def _charge(client: TestClient, kind: str = "timeout") -> httpx.Response:
    return client.post("/artifact-compile/charge", json={"kind": kind})


def test_a_member_gets_a_slot_and_the_window_starts_at_the_first(redis) -> None:
    res = _post(_client(_user()))
    assert res.status_code == 204
    assert redis.counts == {KEY: 1}
    assert redis.ttls == {KEY: ac.WINDOW_S}


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
        "You built many artifacts in the last 10 minutes, which reached the limit. "
        "Try again in 10 minutes."
    )


def test_a_killed_compile_is_charged_heavily(redis) -> None:
    """A member who makes the workbench kill builds reaches the cap fast.

    Mutation: charge 1 unit -> the eleventh kill still gets a slot -> red.
    """
    assert ac.TIMEOUT_CHARGE >= 20
    client = _client(_user())
    kills = 0
    while _post(client).status_code == 204:
        assert _charge(client).status_code == 204
        kills += 1
        assert kills < 50, "the charge does not reach the cap"
    assert kills == ac.COMPILES_PER_WINDOW // (1 + ac.TIMEOUT_CHARGE) + 1
    assert kills <= 10


def test_a_charge_takes_only_the_timeout_kind(redis) -> None:
    client = _client(_user())
    assert _charge(client, "other").status_code == 422
    assert client.post("/artifact-compile/charge", json={}).status_code == 422
    assert redis.counts == {}


def test_a_counter_with_no_expiry_gets_one(redis) -> None:
    """A process that died between the INCR and the EXPIRE left no TTL."""
    redis.counts[KEY] = 7
    assert _post(_client(_user())).status_code == 204
    assert redis.ttls[KEY] == ac.WINDOW_S


def test_a_counter_past_the_cap_with_no_expiry_gets_one_too(redis) -> None:
    redis.counts[KEY] = ac.COMPILES_PER_WINDOW + 5
    res = _post(_client(_user()))
    assert res.status_code == 429
    assert redis.ttls[KEY] == ac.WINDOW_S


def test_each_member_and_each_org_has_its_own_count(redis) -> None:
    redis.counts[KEY] = ac.COMPILES_PER_WINDOW
    assert _post(_client(_user())).status_code == 429
    assert _post(_client(_user(email="bo@acme.test"))).status_code == 204
    assert _post(_client(_user(org=OTHER_ORG))).status_code == 204


@pytest.mark.parametrize("user", [
    _user(email=None),
    _user(email="system:internal"),
    _user(org=None),
])
def test_no_person_or_no_org_gets_403_and_no_count(redis, user) -> None:
    client = _client(user)
    for res in (_post(client), _charge(client)):
        assert res.status_code == 403
        assert res.json()["detail"]["message"] == ac.NO_MEMBER
    assert redis.counts == {}


def test_an_outside_agent_gets_403(redis) -> None:
    from acb_auth.roles import AgentScope

    res = _post(_client(_user(agent=AgentScope(connection_id="conn-1"))))
    assert res.status_code == 403
    assert redis.counts == {}


def test_a_store_outage_answers_503_and_the_workbench_decides(redis) -> None:
    """The gateway says it could not count. The workbench then lets the
    compile go on (fail open). ``route.test.ts`` holds that half."""
    redis.down = True
    client = _client(_user())
    for res in (_post(client), _charge(client)):
        assert res.status_code == 503
        assert res.headers["retry-after"] == "2"
        assert res.json()["detail"]["message"] == ac.UNAVAILABLE


def test_the_real_gateway_app_serves_both_routes() -> None:
    """A router that failed to import would answer 404. Mutation: wrap the
    import in ``try`` and drop it -> red."""
    from gateway.main import app

    from tests.unit._routes import served_routes

    served = {(m, r.path) for r in served_routes(app.routes) for m in (r.methods or ())}
    assert ("POST", "/artifact-compile/slot") in served
    assert ("POST", "/artifact-compile/charge") in served
    src = (_ROOT / "apps/services/gateway/gateway/main.py").read_text(encoding="utf-8")
    line = "from gateway.routes.artifact_compile import router as _artifact_compile_router"
    before = src[: src.index(line)].rstrip().splitlines()[-1]
    assert not before.strip().startswith("try"), "the import must not sit in a try"


def test_the_workbench_takes_a_slot_before_esbuild_and_charges_a_kill() -> None:
    """The BFF calls these paths. A rename on one side alone breaks the limit."""
    guards = (_ROOT / "workbench/control_plane/src/lib/compileGuards.ts").read_text(encoding="utf-8")
    assert 'SLOT_PATH = "/artifact-compile/slot"' in guards
    assert 'CHARGE_PATH = "/artifact-compile/charge"' in guards
    route = (
        _ROOT / "workbench/control_plane/src/app/api/artifacts/compile/route.ts"
    ).read_text(encoding="utf-8")
    assert route.index("await takeSlot(") < route.index("await compileArtifact(")
    assert "chargeTimeout(" in route
