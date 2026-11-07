"""WS-PKG PKG-17a — the org default of the shell layout, with no database.

Spec: ``project-docs/specs/package_host.md`` §9.1. Code:
``gateway/package_host/shell_layout.py``. The R8 twin is
``tests/unit/test_shell_layout_r8.py``.

**R7.** Mutation M1: drop ``Depends(_flag_gate)`` from the PUT route.
``test_flag_off_is_404_before_403`` goes red. Mutation M2: drop
``require_permission("admin:settings:manage")``.
``test_a_non_admin_put_is_403`` goes red.
"""

from __future__ import annotations

from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.package_host import shell_layout as sl
from gateway.routes.admin import me
from gateway.routes.shell import router

ORG = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"
PANES = sorted(me.SHELL_PANES)[:3]
PRESET = sorted(me.SHELL_PRESETS)[0]


def _user(perms: list[str] | None = None, org: str | None = ORG,
          roles: list[str] | None = None) -> UserContext:
    return UserContext(email="m@pkg.test", role=UserRole.EMPLOYEE,
                       access=build_access(perms or [], roles=roles or ["member"]),
                       organization_id=org)


class _Store:
    def __init__(self) -> None:
        self.saved: list[tuple[str, Any, str]] = []
        self.blob: Any = {}
        self.fail = False


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    s = _Store()

    def _load(key: str, default: Any = None) -> Any:
        if s.fail:
            raise RuntimeError("down")
        return s.blob if s.blob else default

    def _save(key: str, value: Any, updated_by: str = "") -> None:
        if s.fail:
            raise RuntimeError("down")
        s.saved.append((key, value, updated_by))
        s.blob = value

    import acb_common

    monkeypatch.setattr(acb_common, "load_org_setting", _load)
    monkeypatch.setattr(acb_common, "save_org_setting", _save)
    monkeypatch.setattr(get_settings(), "package_host_orgs", ORG)
    return s


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


ADMIN = ["admin:settings:manage"]
GOOD = {"roles": {"member": {"preset": PRESET, "pins": PANES}}}


# ── Validation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    {"roles": {"astronaut": {"pins": PANES}}},
    {"roles": {"member": {"pins": ["/nowhere"]}}},
    {"roles": {"member": {"pins": [PANES[0], PANES[0]]}}},
    {"roles": {"member": {"preset": "wizard"}}},
    {"roles": {"member": {"order": PANES}}},
    {"roles": {"member": {"pins": sorted(me.SHELL_PANES) * 2}}},
    {"roles": {}, "extra": 1},
    {"roles": {"agent_service": {"pins": PANES}}},
])
def test_a_bad_document_is_422(store: _Store, body: dict) -> None:
    r = _client(_user(ADMIN)).put("/shell/layout", json=body)
    assert r.status_code == 422
    assert store.saved == []


def test_the_vocabulary_is_me_s() -> None:
    assert sl.SHELL_PANES is me.SHELL_PANES
    assert sl.SHELL_PRESETS is me.SHELL_PRESETS
    assert sl.MAX_PINS == me.MAX_PINS


def test_the_roles_are_the_system_roles() -> None:
    assert sl.ROLE_ORDER == ("owner", "admin", "manager", "member", "guest")


# ── The routes ─────────────────────────────────────────────────────────────


def test_an_admin_put_stores_and_stamps(store: _Store) -> None:
    r = _client(_user(ADMIN)).put("/shell/layout", json=GOOD)
    assert r.status_code == 200
    key, value, by = store.saved[0]
    assert key == "shell_layout" and by == "m@pkg.test"
    assert value["roles"]["member"]["pins"] == PANES
    assert value["updatedBy"] == "m@pkg.test" and value["updatedAt"]
    assert r.json()["updatedAt"] == value["updatedAt"]


def test_a_member_get_reads_it(store: _Store) -> None:
    _client(_user(ADMIN)).put("/shell/layout", json=GOOD)
    r = _client(_user()).get("/shell/layout")
    assert r.status_code == 200
    assert r.json()["roles"]["member"]["preset"] == PRESET


def test_get_with_nothing_stored_is_the_empty_default(store: _Store) -> None:
    r = _client(_user()).get("/shell/layout")
    assert r.status_code == 200
    assert r.json()["roles"] == {}


def test_a_stored_bad_entry_drops_alone(store: _Store) -> None:
    store.blob = {"roles": {"member": {"pins": PANES}, "guest": {"preset": "nope"},
                            "ghost": {}}}
    assert set(_client(_user()).get("/shell/layout").json()["roles"]) == {"member"}


def test_a_non_admin_put_is_403(store: _Store) -> None:
    r = _client(_user()).put("/shell/layout", json=GOOD)
    assert r.status_code == 403
    assert store.saved == []


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize("perms", [[], ADMIN])
def test_flag_off_is_404_before_403(store: _Store, monkeypatch, method: str,
                                    perms: list[str]) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", OTHER)
    c = _client(_user(perms))
    r = c.get("/shell/layout") if method == "get" else c.put("/shell/layout", json=GOOD)
    assert r.status_code == 404
    assert store.saved == []


def test_a_store_fault_is_503(store: _Store) -> None:
    store.fail = True
    assert _client(_user(ADMIN)).put("/shell/layout", json=GOOD).status_code == 503


def test_no_tenant_comes_from_input(store: _Store) -> None:
    body = {"roles": {}, "organization_id": OTHER}
    assert _client(_user(ADMIN)).put("/shell/layout", json=body).status_code == 422
    r = _client(_user()).get("/shell/layout", params={"organization_id": OTHER})
    assert r.status_code == 200
    import inspect

    for fn in (sl.get_layout, sl.put_layout):
        assert not {"organization_id", "org", "tenant"} & set(inspect.signature(fn).parameters)
    assert "organization_id" not in sl.ShellLayout.model_fields


# ── The resolver ───────────────────────────────────────────────────────────

VALID = {"roles": {"member": {"preset": PRESET, "pins": PANES},
                   "admin": {"pins": PANES[:1]}}}


@pytest.mark.parametrize(("prefs", "role", "pins", "preset"), [
    ({"pins": [PANES[2]], "preset": "engineer"}, "member", [PANES[2]], "engineer"),
    ({"pins": [], "preset": None}, "member", [], PRESET),
    ({"pins": None, "preset": None}, "member", PANES, PRESET),
    (None, "member", PANES, PRESET),
    ({}, ["member", "admin"], PANES[:1], None),
    ({}, ["guest"], None, None),
    ({}, "Admin", PANES[:1], None),
    ({"pins": "oops", "preset": 7}, "member", PANES, PRESET),
    ({"pins": None, "preset": "engineer"}, "member", PANES, "engineer"),
])
def test_precedence(prefs, role, pins, preset) -> None:
    got = sl.resolve_layout(prefs, VALID, role)
    assert got == {"pins": pins, "preset": preset}


def test_no_default_is_none() -> None:
    assert sl.resolve_layout({}, None, "member") == {"preset": None, "pins": None}
    assert sl.resolve_layout({}, {"roles": "bad"}, "member")["pins"] is None


# ── The lenient read (P2-1) ────────────────────────────────────────────────


def _coerce(entry: dict) -> sl.RoleLayout:
    return sl.coerce_stored({"roles": {"member": entry}}).roles["member"]


def test_a_retired_pane_drops_alone_and_the_preset_stays() -> None:
    got = _coerce({"preset": PRESET, "pins": [PANES[0], "/retired", PANES[1]]})
    assert got.pins == PANES[:2]
    assert got.preset == PRESET


def test_all_pins_unknown_leaves_the_preset() -> None:
    got = _coerce({"preset": PRESET, "pins": ["/x", 7, None]})
    assert got.pins == []
    assert got.preset == PRESET


def test_duplicate_pins_collapse_in_order() -> None:
    assert _coerce({"pins": [PANES[1], PANES[0], PANES[1]]}).pins == [PANES[1], PANES[0]]


def test_the_stored_pins_are_capped() -> None:
    panes = sorted(me.SHELL_PANES)
    assert _coerce({"pins": panes}).pins == panes[: me.MAX_PINS]


def test_the_pins_cap_is_on_the_field() -> None:
    # 25 unique panes cannot exist, so a behaviour test cannot see a dropped cap.
    meta = sl.RoleLayout.model_fields["pins"].metadata
    assert any(getattr(m, "max_length", None) == me.MAX_PINS for m in meta)


def test_put_stays_strict_on_a_retired_pane(store: _Store) -> None:
    body = {"roles": {"member": {"pins": [PANES[0], "/retired"]}}}
    assert _client(_user(ADMIN)).put("/shell/layout", json=body).status_code == 422
