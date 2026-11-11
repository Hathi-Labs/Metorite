"""WS-PKG PKG-14a — the host door of the extension values, with no database.

Spec: ``project-docs/specs/package_host.md`` §8.3 and the PKG-14a row of §10.
Code: ``gateway/package_host/host_door.py``, ``routes/projects/package_fields.py``
and ``routes/crm/package_fields.py``. The R8 twin is
``tests/unit/test_package_host_door_r8.py``.

What this suite holds:

a. ``fields_of`` keeps only the ``fields`` entries of one record kind. It
   drops a system entry, an install that needs setup, and a key or a
   declaration that the store refuses.
b. ``visible_fields`` takes its set from ``resolve.resolve_for``, with the
   viewer and the entitlement of the session. The door keeps no copy of the
   resolve SQL.
c. ``write_field`` refuses a hidden install (404) and a key that did not win
   (422) before the store runs. ``read_fields`` passes the set of winners to
   the store and shows only the value of a winner.
d. Each route reads the switch first, and opens no session while it is off.
e. The task routes run ``load_visible_task`` before the door. The CRM routes
   are on the CRM router, so its three gates run, and a write runs
   ``require_row`` and ``assert_writable`` before the door.
f. ``CRM_KINDS`` is the one map, and it covers each CRM kind of §8.3.
g. The R8 twin is in the ``--must-run`` lists of pr-check and deploy.

Mutations that turn this suite red (R7): the door keeps a system entry
(``test_a_system_entry_and_a_setup_install_are_dropped``), a non-winner pair
is accepted (``test_a_write_to_a_pair_that_did_not_win_is_refused``), the
flag check is removed (``test_each_route_is_dark_and_opens_no_session``), the
task door skips ``load_visible_task``
(``test_the_task_routes_load_the_visible_task_first``), and the CRM route is
mounted without the router gate (``test_the_crm_routes_are_on_the_crm_router``).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.studio_fields import FieldError
from fastapi import HTTPException
from gateway.package_host import field_values as fv
from gateway.package_host import host_door, resolve
from gateway.package_host.field_values import FieldNotDeclared
from gateway.package_host.store import InstallNotFound
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import package_fields as crm_fields
from gateway.routes.projects import core as projects_core
from gateway.routes.projects import package_fields as task_fields

_ROOT = Path(__file__).resolve().parents[2]
ORG = "11111111-2222-3333-4444-555555555555"
I_ORG, I_GRP, I_OTHER = (str(uuid.uuid4()) for _ in range(3))
RECORD = uuid.uuid4()


def _user(org: str | None = ORG) -> UserContext:
    return UserContext(email="ben@pkg14.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:*"]), organization_id=org)


def _entry(key: str, install: str | None, source: str = "org", *,
           needs_setup: bool = False, **payload: Any) -> dict[str, Any]:
    return {**({"type": "date"} | payload), "key": key, "source": source,
            "install_id": install, "needs_setup": needs_setup}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── a. The entries of one record kind ──────────────────────────────────────


def test_only_the_fields_entries_of_the_record_kind_are_kept() -> None:
    body = {
        "fields": [
            _entry("task:warranty_end", I_ORG, label="Warranty"),
            _entry("crm.deal:warranty_end", I_ORG),
            _entry("task:size", I_GRP, "group", type="number"),
        ],
        "nav": [_entry("task:other", I_ORG)],
        "jobs": [_entry("task:job", I_ORG)],
    }
    seen = host_door.fields_of(body, "task")
    assert [(f.install_id, f.field_key, f.source, f.field.type) for f in seen.fields] == [
        (I_ORG, "warranty_end", "org", "date"), (I_GRP, "size", "group", "number")]
    assert seen.install_ids == {I_ORG, I_GRP}
    assert seen.fields[0].as_dict() == {
        "install_id": I_ORG, "key": "warranty_end", "source": "org", "type": "date",
        "label": "Warranty", "many": False, "options": None}
    deal = host_door.fields_of(body, "crm.deal")
    assert [(f.install_id, f.field_key) for f in deal.fields] == [(I_ORG, "warranty_end")]
    assert host_door.fields_of(body, "crm.lead").fields == ()


def test_a_system_entry_and_a_setup_install_are_dropped() -> None:
    body = {"fields": [
        _entry("task:sys", None, "system"),
        _entry("task:sys2", I_OTHER, "system"),
        _entry("task:setup", I_OTHER, needs_setup=True),
        _entry("task:nosetup", I_OTHER, needs_setup=None),
        _entry("task:kept", I_ORG),
    ]}
    assert [f.field_key for f in host_door.fields_of(body, "task").fields] == ["kept"]
    assert host_door.fields_of(body, "task").install_ids == {I_ORG}


@pytest.mark.parametrize("entry", [
    _entry("task:Bad", I_ORG),
    _entry("task:a:b", I_ORG),
    _entry("task:", I_ORG),
    _entry("taskx:k", I_ORG),
    _entry("task:k", I_ORG, required=True),
    _entry("task:k", I_ORG, type="relation"),
    _entry("task:k", I_ORG, type="nope"),
    _entry("task:k", I_ORG, feature="crm"),
    _entry("task:k", 7),
    "not an entry",
])
def test_an_entry_that_the_store_refuses_is_dropped(entry: Any) -> None:
    assert host_door.fields_of({"fields": [entry]}, "task").fields == ()


@pytest.mark.parametrize("body", [{}, {"fields": None}, {"fields": "x"}, []])
def test_a_malformed_answer_gives_no_field(body: Any) -> None:
    assert host_door.fields_of(body, "task").fields == ()


# ── b. The set comes from the one resolve ──────────────────────────────────


def test_the_set_comes_from_resolve_for_with_the_session_viewer(monkeypatch) -> None:
    seen: dict[str, Any] = {}
    user = _user()

    async def fake(db: Any, *, viewer: str, can_use: Any) -> dict[str, Any]:
        seen.update(db=db, viewer=viewer, can_use=can_use)
        return {"version": "v", "contributions": {"fields": [_entry("task:k", I_ORG)]}}

    monkeypatch.setattr(resolve, "resolve_for", fake)
    got = _run(host_door.visible_fields("DB", user, "task"))
    assert seen["db"] == "DB" and seen["viewer"] == user.email
    assert seen["can_use"] == user.can_use_feature
    assert got.install_ids == {I_ORG}


def test_the_door_keeps_no_copy_of_the_resolve() -> None:
    """The set has one source. A second SQL of the installs that a viewer
    sees would be a second answer (§8.3, PKG-14a)."""
    src = (_ROOT / "apps/services/gateway/gateway/package_host/host_door.py").read_text(
        encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    for word in ("package_install", "SHARE_ROW", "INSTALLS_SQL", "my_groups", "text("):
        assert word not in code, word
    assert "resolve.resolve_for(" in code
    for name in ("routes/projects/package_fields.py", "routes/crm/package_fields.py"):
        route = (_ROOT / "apps/services/gateway/gateway" / name).read_text(encoding="utf-8")
        assert "text(" not in route and "package_field_value" not in route, name


# ── c. The write and the read ──────────────────────────────────────────────


class _Store:
    """Records the calls of the field store."""

    def __init__(self, values: list[fv.FieldValue] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.values = values or []

    async def set_value(self, db: Any, **kw: Any) -> Any:
        self.calls.append(("set", kw))
        return kw["value"]

    async def get_values(self, db: Any, **kw: Any) -> list[fv.FieldValue]:
        self.calls.append(("get", kw))
        return self.values


def _door(monkeypatch, entries: list[dict[str, Any]], store: _Store) -> None:
    async def fake(db: Any, *, viewer: str, can_use: Any) -> dict[str, Any]:
        return {"version": "v", "contributions": {"fields": entries}}

    monkeypatch.setattr(resolve, "resolve_for", fake)
    monkeypatch.setattr(fv, "set_value", store.set_value)
    monkeypatch.setattr(fv, "get_values", store.get_values)


def _write(**kw: Any) -> Any:
    args = {"record_kind": "task", "record_id": str(RECORD), "install_id": I_ORG,
            "field_key": "k", "value": "2027-01-31", **kw}
    return _run(host_door.write_field("DB", _user(), **args))


def test_a_write_to_a_winner_reaches_the_store_with_the_set(monkeypatch) -> None:
    store = _Store()
    _door(monkeypatch, [_entry("task:k", I_ORG), _entry("task:j", I_GRP, "group")], store)
    assert _write(install_id=I_ORG.upper()) == "2027-01-31"
    [(op, kw)] = store.calls
    assert op == "set" and kw["install_id"] == I_ORG and kw["field_key"] == "k"
    assert kw["install_ids"] == {I_ORG, I_GRP}


def test_a_write_to_a_hidden_install_is_not_found(monkeypatch) -> None:
    store = _Store()
    _door(monkeypatch, [_entry("task:k", I_ORG)], store)
    with pytest.raises(InstallNotFound) as exc:
        _write(install_id=I_OTHER)
    assert exc.value.status == 404
    assert store.calls == []


def test_a_write_to_a_pair_that_did_not_win_is_refused(monkeypatch) -> None:
    """I_GRP is in the set for the key ``j``. It did not win ``k``, so a
    write of ``k`` to I_GRP is refused, also when I_GRP declares ``k``."""
    store = _Store()
    _door(monkeypatch, [_entry("task:k", I_ORG), _entry("task:j", I_GRP, "group")], store)
    with pytest.raises(FieldNotDeclared) as exc:
        _write(install_id=I_GRP, field_key="k")
    assert exc.value.status == 422 and "`k`" in str(exc.value)
    # The install holds no field on another record kind, so it is not in
    # the set of that kind.
    with pytest.raises(InstallNotFound):
        _write(record_kind="crm.deal")
    assert store.calls == []


def test_a_bad_key_or_install_id_is_refused_before_the_resolve(monkeypatch) -> None:
    def no_resolve(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the door read the resolve for a bad argument")

    monkeypatch.setattr(resolve, "resolve_for", no_resolve)
    for kw, code in (({"install_id": "x"}, "bad_install_id"),
                     ({"field_key": "Bad Key"}, "bad_field_key")):
        with pytest.raises(fv.InstallError) as exc:
            _write(**kw)
        assert exc.value.code == code


def test_a_read_passes_the_winners_and_shows_only_their_values(monkeypatch) -> None:
    store = _Store(values=[
        fv.FieldValue(install_id=I_ORG, app_id="a", field_key="k", field_type="date",
                      value="2027-01-31"),
        # The store answers a value of I_GRP at ``k``. I_GRP did not win ``k``.
        fv.FieldValue(install_id=I_GRP, app_id="b", field_key="k", field_type="date",
                      value="1999-01-01"),
    ])
    _door(monkeypatch, [_entry("task:k", I_ORG), _entry("task:j", I_GRP, "group")], store)
    got = _run(host_door.read_fields("DB", _user(), record_kind="task",
                                     record_id=str(RECORD)))
    assert [(f["install_id"], f["key"], f["value"]) for f in got] == [
        (I_ORG, "k", "2027-01-31"), (I_GRP, "j", None)]
    [(op, kw)] = store.calls
    assert op == "get" and kw["install_ids"] == {I_ORG, I_GRP}


def test_a_refusal_answers_its_status_and_names_the_key() -> None:
    exc = host_door.refusal(InstallNotFound("install_not_found", "No install has this id."),
                            "ben@pkg14.test")
    assert (exc.status_code, exc.detail["code"]) == (404, "install_not_found")
    exc = host_door.refusal(FieldError("k", "Field `k` is bad."), "ben@pkg14.test")
    assert exc.status_code == 422 and exc.detail["field"] == "k"
    exc = host_door.refusal(FieldNotDeclared("task", "k"), "ben@pkg14.test")
    assert (exc.status_code, exc.detail["code"]) == (422, "field_not_declared")


# ── d. The switch ──────────────────────────────────────────────────────────


def _calls() -> list[tuple[str, Any, Any]]:
    user = _user()
    body = task_fields.PackageFieldIn(value=None)
    return [
        ("task get", task_fields, lambda: task_fields.task_package_fields(RECORD, user)),
        ("task put", task_fields, lambda: task_fields.set_task_package_field(
            RECORD, I_ORG, "k", body, user)),
        ("crm get", crm_fields, lambda: crm_fields.read_record_fields("crm.deal", RECORD, user)),
        ("crm put", crm_fields, lambda: crm_fields.write_record_field(
            "crm.deal", RECORD, I_ORG, "k", None, user)),
    ]


def test_each_route_is_dark_and_opens_no_session(monkeypatch) -> None:
    def no_session(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a dark route opened a session")

    for raw in ("", "other-org"):
        monkeypatch.setattr(get_settings(), "package_host_orgs", raw)
        for name, module, call in _calls():
            monkeypatch.setattr(module, "_tenant_session", no_session)
            with pytest.raises(HTTPException) as exc:
                _run(call())
            assert (exc.value.status_code, exc.value.detail) == (404, "Not found"), name


# ── e. The rule of the host app ────────────────────────────────────────────


class _Session:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    @contextlib.asynccontextmanager
    async def __call__(self):
        self.log.append("session")
        yield "DB"


def test_the_task_routes_load_the_visible_task_first(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    log: list[str] = []

    async def vis(db: Any, user: Any) -> str:
        log.append("visibility")
        return "VIS"

    async def load(db: Any, v: Any, task_id: str) -> None:
        log.append(f"load {v} {task_id}")
        raise HTTPException(status_code=404, detail="Task not found")

    async def door(*_a: Any, **_k: Any) -> Any:
        log.append("door")
        return []

    monkeypatch.setattr(task_fields, "_tenant_session", _Session(log))
    monkeypatch.setattr(task_fields, "resolve_visibility", vis)
    monkeypatch.setattr(task_fields, "load_visible_task", load)
    monkeypatch.setattr(host_door, "read_fields", door)
    monkeypatch.setattr(host_door, "write_field", door)
    for name, _module, call in _calls()[:2]:
        log.clear()
        with pytest.raises(HTTPException) as exc:
            _run(call())
        assert (exc.value.status_code, exc.value.detail) == (404, "Task not found"), name
        assert log == ["session", "visibility", f"load VIS {RECORD}"], name
    # The load is the one of patch_task.
    src = Path(task_fields.__file__).read_text(encoding="utf-8")
    assert src.count("await load_visible_task(db, vis, record)") == 2


def test_the_crm_write_loads_the_row_and_asks_assert_writable_first(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    log: list[str] = []

    async def row(db: Any, table: str, record_id: str, what: str) -> str:
        log.append(f"row {table} {what}")
        return "ROW"

    async def writable(db: Any, org: Any, entity: Any, fields: Any, *, row: Any) -> None:
        log.append(f"writable {entity.slug} {fields} {row}")
        raise HTTPException(status_code=409, detail={"code": "mirrored_record"})

    async def door(*_a: Any, **_k: Any) -> Any:
        log.append("door")
        return []

    monkeypatch.setattr(crm_fields, "_tenant_session", _Session(log))
    monkeypatch.setattr(crm_fields, "require_row", row)
    monkeypatch.setattr(crm_fields, "assert_writable", writable)
    monkeypatch.setattr(crm_fields, "current_tenant", lambda: ORG)
    monkeypatch.setattr(host_door, "read_fields", door)
    monkeypatch.setattr(host_door, "write_field", door)
    _, _, get, put = (c[2] for c in _calls())
    with pytest.raises(HTTPException) as exc:
        _run(put())
    assert exc.value.status_code == 409
    assert log == ["session", "row crm_deals Deal", "writable deals None ROW"]
    log.clear()
    assert _run(get())["fields"] == []
    assert log == ["session", "row crm_deals Deal", "door"]


def test_the_crm_routes_are_on_the_crm_router() -> None:
    """The router carries the kill switch, the feature gate and the access
    rule. A route on a second router would skip all three."""
    from gateway.routes.crm.access import require_crm_access
    from gateway.routes.crm.flags import require_crm_enabled

    deps = [d.dependency for d in crm_core.router.dependencies]
    assert require_crm_enabled in deps and require_crm_access in deps
    assert len(deps) == 3
    mine = sorted((sorted(r.methods)[0], r.path) for r in crm_core.router.routes
                  if "package-fields" in r.path)
    want = sorted(
        (verb, f"/crm/{e.slug}/{{record_id}}/package-fields{tail}")
        for e in crm_fields.CRM_KINDS.values()
        for verb, tail in (("GET", ""), ("PUT", "/{install_id}/{field_key}")))
    assert mine == want
    task = sorted((sorted(r.methods)[0], r.path) for r in projects_core.router.routes
                  if "package-fields" in r.path)
    assert task == [("GET", "/projects/tasks/{task_id}/package-fields"),
                    ("PUT", "/projects/tasks/{task_id}/package-fields/{install_id}/{field_key}")]


def test_every_package_fields_route_of_the_gateway_is_one_of_ours() -> None:
    """A route mounted on another router of the app shows here."""
    import gateway.main as main

    from tests.unit._routes import served_routes

    paths = sorted(
        (sorted(getattr(r, "methods", ()) or ("",))[0], r.path)
        for r in served_routes(main.app.routes) if "package-fields" in str(r.path))
    assert len(paths) == 10, paths


# ── f. The one map ─────────────────────────────────────────────────────────


def test_crm_kinds_is_the_one_map_and_covers_each_crm_kind() -> None:
    assert set(crm_fields.CRM_KINDS) == {
        k for k in fv.HOST_RECORD_KINDS if k.startswith("crm.")}
    for kind, entity in crm_fields.CRM_KINDS.items():
        assert crm_core.ENTITIES[entity.slug] is entity, kind
    assert {e.table for e in crm_fields.CRM_KINDS.values()} == {
        "crm_leads", "crm_contacts", "crm_companies", "crm_deals"}
    assert task_fields.TASK_KIND in fv.HOST_RECORD_KINDS


# ── g. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_package_host_door_r8\.py \\$", text, re.M)
