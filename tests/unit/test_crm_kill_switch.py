"""The CRM kill switch (WS-53 CRM-0).

Spec: ``project-docs/specs/crm_platform.md``, the CRM-0 row. The switch is
two settings, ``CRM_ENABLED`` and ``CRM_ORGS``, and one reader,
``gateway.routes.crm.flags``. The router in ``routes/crm/core.py`` runs
``require_crm_enabled`` BEFORE the feature gate, so an organization outside
the list gets 404 and never 403.

Hermetic: the ``/crm`` router is mounted on a bare FastAPI app, the DB seam is
``tests/unit/_crm_fakes.py``'s mirror, and ``get_current_user`` is overridden
with a ``UserContext`` that the test builds. That is the server-side identity,
so a header, a query value or a body field cannot reach it.

The fence (item 7) walks the REAL gateway app, so a second router under the
``/crm`` prefix that skips the switch fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

from acb_auth import get_current_user
from acb_common.env_guard import is_platform_env
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import crm as crm_package
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import deal_contacts as crm_deal_contacts
from gateway.routes.crm import flags as crm_flags
from gateway.routes.crm import pipeline as crm_pipeline
from gateway.routes.crm import records as crm_records

from tests.unit._crm_fakes import (
    CRM_TEST_ORG,
    FakeCrmDB,
    bind_db,
    crm_switch_on,
    crm_user,
)
from tests.unit._routes import served_routes

ORG_A = CRM_TEST_ORG
ORG_B = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0002"

#: The six calls of acceptance items 1 to 4: the four entity lists, the
#: board and one create.
_CALLS: list[tuple[str, str]] = [
    ("GET", "/crm/leads"),
    ("GET", "/crm/deals"),
    ("GET", "/crm/contacts"),
    ("GET", "/crm/organizations"),
    ("GET", "/crm/pipeline"),
    ("POST", "/crm/leads"),
]

_LEAD = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@acme.test"}


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(
        monkeypatch, fake,
        (crm_core, crm_records, crm_pipeline, crm_activities, crm_admin,
         crm_deal_contacts),
    )
    fake.seed(
        "crm_lead_statuses", name="New", position=10, type="open",
        is_default=True,
    )
    fake.seed(
        "crm_deal_statuses", name="Qualification", position=10, type="open",
        is_default=True, probability=10,
    )
    return fake


@pytest.fixture
def switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped state on a box, made explicit."""
    crm_switch_on(monkeypatch, ORG_A)
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)


def _client(user: Any) -> TestClient:
    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _send(
    client: TestClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    body_extra: dict[str, str] | None = None,
) -> int:
    if method == "POST":
        body = {**_LEAD, **(body_extra or {})}
        return client.post(path, json=body, headers=headers, params=params).status_code
    return client.get(path, headers=headers, params=params).status_code


def _codes(client: TestClient, **kw: Any) -> dict[tuple[str, str], int]:
    return {(m, p): _send(client, m, p, **kw) for m, p in _CALLS}


# ── 1. Switch OFF, org listed → 404 on each ──────────────────────────────────

def test_switch_off_answers_404_on_each_call(
    db: FakeCrmDB, switch_off: None,
) -> None:
    codes = _codes(_client(crm_user(organization_id=ORG_A)))
    assert codes == {call: 404 for call in _CALLS}


# ── 2. Switch ON, org not listed → 404, also with feature:crm ────────────────

def test_an_org_outside_the_list_gets_404_not_403(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_switch_on(monkeypatch, ORG_A)
    member = crm_user(organization_id=ORG_B)
    assert member.has_permission("feature:crm")
    codes = _codes(_client(member))
    assert codes == {call: 404 for call in _CALLS}


def test_an_org_outside_the_list_without_the_feature_also_gets_404(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The switch runs first, so the feature gate never says 403 here."""
    crm_switch_on(monkeypatch, ORG_A)
    member = crm_user(organization_id=ORG_B, features="feature:email")
    assert not member.has_permission("feature:crm")
    codes = _codes(_client(member))
    assert codes == {call: 404 for call in _CALLS}


# ── 3. Switch ON, org listed, feature:crm → served ──────────────────────────

def test_a_listed_org_with_the_feature_is_served(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_switch_on(monkeypatch, ORG_A)
    codes = _codes(_client(crm_user(organization_id=ORG_A)))
    assert all(code != 404 for code in codes.values()), codes
    assert all(200 <= code < 300 for code in codes.values()), codes


# ── 4. Switch ON, org listed, no feature:crm → 403 ──────────────────────────

def test_a_listed_org_without_the_feature_gets_403(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_switch_on(monkeypatch, ORG_A)
    member = crm_user(organization_id=ORG_A, features="feature:email")
    codes = _codes(_client(member))
    assert codes == {call: 403 for call in _CALLS}


# ── 5. `*` and the missing organization ─────────────────────────────────────

def test_star_allows_each_org_with_a_bound_tenant(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_switch_on(monkeypatch, "*")
    for org in (ORG_A, ORG_B):
        codes = _codes(_client(crm_user(organization_id=org)))
        assert all(200 <= code < 300 for code in codes.values()), (org, codes)


@pytest.mark.parametrize("orgs", ["*", ORG_A, f"{ORG_A},*"])
def test_no_organization_gets_404_also_with_star(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch, orgs: str,
) -> None:
    crm_switch_on(monkeypatch, orgs)
    codes = _codes(_client(crm_user(organization_id=None)))
    assert codes == {call: 404 for call in _CALLS}


@pytest.mark.parametrize("org", [None, "", "   "])
def test_the_reader_refuses_a_missing_org_with_star(
    monkeypatch: pytest.MonkeyPatch, org: str | None,
) -> None:
    crm_switch_on(monkeypatch, "*")
    assert crm_flags.crm_org_allowed(org) is False


def test_the_reader_reads_ids_trimmed_and_in_any_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_switch_on(monkeypatch, f" {ORG_A.upper()} , x")
    assert crm_flags.crm_org_allowed(ORG_A) is True
    assert crm_flags.crm_org_allowed(ORG_B) is False


def test_an_empty_list_allows_no_org(monkeypatch: pytest.MonkeyPatch) -> None:
    crm_switch_on(monkeypatch, "")
    assert crm_flags.crm_org_allowed(ORG_A) is False


def test_the_settings_default_is_off_and_empty() -> None:
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["crm_enabled"].default is False
    assert fields["crm_orgs"].default == ""


# ── 6. An org id in the request changes nothing ──────────────────────────────

_SPOOF_HEADERS = {
    "X-Organization-Id": ORG_A,
    "X-Org-Id": ORG_A,
    "X-Tenant-Id": ORG_A,
}
_SPOOF_PARAMS = {"organization_id": ORG_A, "org": ORG_A, "tenant_id": ORG_A}
_SPOOF_BODY = {"organization_id": ORG_A, "org": ORG_A, "tenant_id": ORG_A}


def test_a_listed_org_in_the_request_does_not_open_the_switch(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_switch_on(monkeypatch, ORG_A)
    client = _client(crm_user(organization_id=ORG_B))
    codes = _codes(
        client, headers=_SPOOF_HEADERS, params=_SPOOF_PARAMS,
        body_extra=_SPOOF_BODY,
    )
    assert codes == {call: 404 for call in _CALLS}


def test_another_org_in_the_request_does_not_close_the_switch(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_switch_on(monkeypatch, ORG_A)
    client = _client(crm_user(organization_id=ORG_A))
    spoof = {k: ORG_B for k in _SPOOF_HEADERS}
    codes = _codes(
        client, headers=spoof, params={k: ORG_B for k in _SPOOF_PARAMS},
        body_extra={k: ORG_B for k in _SPOOF_BODY},
    )
    assert all(200 <= code < 300 for code in codes.values()), codes


def test_the_dependency_takes_only_the_user() -> None:
    """Its one parameter is the resolved ``UserContext``. A ``Header``,
    ``Query`` or ``Body`` parameter here would be request input."""
    import inspect

    params = inspect.signature(crm_flags.require_crm_enabled).parameters
    assert list(params) == ["user"]


# ── 7. Fence: every /crm route carries the switch ────────────────────────────

def _calls_in(dependant: Any) -> set[Any]:
    found: set[Any] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        if getattr(node, "call", None) is not None:
            found.add(node.call)
        stack.extend(getattr(node, "dependencies", []) or [])
    return found


def _crm_routes(routes: Any) -> list[Any]:
    return [
        r for r in served_routes(routes)
        if str(getattr(r, "path", "")).startswith("/crm")
    ]


def test_every_crm_route_of_the_gateway_carries_the_switch() -> None:
    import gateway.main as main
    from fastapi.dependencies.utils import get_dependant

    routes = _crm_routes(main.app.routes)
    assert len(routes) >= 20, (
        f"only {len(routes)} /crm routes found — the CRM router did not mount, "
        "and main.py swallows that import error"
    )
    missing = []
    for route in routes:
        calls: set[Any] = set()
        for dep in getattr(route, "dependencies", []) or []:
            fn = getattr(dep, "dependency", None)
            calls.add(fn)
            if fn is not None:
                calls |= _calls_in(get_dependant(path=route.path, call=fn))
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            calls |= _calls_in(get_dependant(path=route.path, call=endpoint))
        if crm_flags.require_crm_enabled not in calls:
            missing.append((sorted(route.methods or [])[:1], route.path))
    assert not missing, f"/crm routes without require_crm_enabled: {missing}"


def test_the_switch_runs_before_the_feature_gate() -> None:
    deps = [getattr(d, "dependency", None) for d in crm_core.router.dependencies]
    assert deps[0] is crm_flags.require_crm_enabled


# ── 8. The agent: a 404 is a refusal with no rows ────────────────────────────

async def test_search_crm_relays_a_404_as_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit._crm_agent_fakes import (
        FakeClient,
        FakeResponse,
        fake_gateway,
        load_agent_module,
    )

    agent = load_agent_module()

    class _NotFound(FakeClient):
        async def request(self, method: str, url: str, **kwargs: Any):
            self._calls.append({"method": method, "url": url})
            return FakeResponse({"detail": "Not Found"}, 404)

    calls = fake_gateway(
        agent, monkeypatch, None, user="sales@fracktal.in", client_class=_NotFound,
    )
    with pytest.raises(RuntimeError) as exc:
        await agent.search_crm("acme")
    message = str(exc.value)
    assert "404" in message
    assert "Not Found" in message
    assert "Matches for" not in message
    assert len(calls) == 1


# ── 9. A tenant cannot set either name ───────────────────────────────────────

@pytest.mark.parametrize("name", ["CRM_ENABLED", "CRM_ORGS", "crm_orgs"])
def test_both_names_are_platform_env(name: str) -> None:
    assert is_platform_env(name) is True


# ── 10. One reader (R7) ──────────────────────────────────────────────────────

_REPO = Path(__file__).resolve().parents[2]
_READER = _REPO / "apps/services/gateway/gateway/routes/crm/flags.py"


def _setting_readers(roots: list[Path]) -> list[str]:
    """Each file that reads `.crm_enabled` or `.crm_orgs` as an attribute."""
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in {"crm_enabled", "crm_orgs"}:
                    hits.append(path.resolve().as_posix())
                    break
    return hits


def test_flags_is_the_one_reader_of_the_switch() -> None:
    readers = _setting_readers([_REPO / "apps", _REPO / "packages"])
    assert readers == [_READER.as_posix()], (
        "Only routes/crm/flags.py may read settings.crm_enabled or "
        f"settings.crm_orgs. Readers found: {readers}"
    )


def test_the_one_reader_fence_sees_a_second_reader(tmp_path: Path) -> None:
    plant = tmp_path / "second.py"
    plant.write_text("def f(s):\n    return s.crm_orgs\n", encoding="utf-8")
    assert _setting_readers([tmp_path]) == [plant.resolve().as_posix()]
