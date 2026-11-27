"""STU-E12a (R8): ``people.person.updated`` under FORCE RLS.

Spec: ``project-docs/specs/ai_studio.md`` §14 E12a.

The event goes through the real bus and the real dispatcher, on the app role,
in a promoted clone of the ladder. Two orgs each hold a workflow that listens
for the event. The cases prove that:

* the event of org A starts only the workflow of org A, and org B starts nothing;
* no bound tenant starts nothing, though the payload names an org;
* with ``CORE_EVENTS_ORGS`` empty, no event goes out and no run starts.

The dispatcher reads the bound tenant, never the payload. A mutation that
reads ``organization_id`` of the payload, or loops over every org, turns the
cross-org case red (R7).

⚠️ **R8.** A skip is not a pass. The gate is ``TENANT_LADDER_DATABASE_URL``.

Run::

    uv run pytest tests/unit/test_people_events_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from gateway import core_events
from gateway.routes.workflows import triggers, unattended
from ingestion import event_hooks
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database
from tests.unit.test_workflows_triggers_r8 import (
    _drain_runs,
    _drop_database,
    _trivial,
)
from tests.unit.test_workflows_triggers_r8 import (
    _grant_app_role as _grant,
)

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT if _SNAPSHOT is not None else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason="TENANT_LADDER_DATABASE_URL unset: R8 requires a REAL Postgres. "
    "A skip here is not a pass.",
)

_PERSON = str(uuid.uuid4())
_ACTOR = "owner@a.pplev.test"


@pytest.fixture(scope="module")
def promoted():
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_pplev"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    _grant(ded_admin_url, dedicated)

    orgs = {"A": str(uuid.uuid4()), "B": str(uuid.uuid4())}
    wf: dict[str, str] = {}
    graph = json.dumps(_trivial())
    with eng.begin() as conn:
        for tag, oid in orgs.items():
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :n)"),
                {"id": oid, "s": f"pplev-{tag.lower()}", "n": f"pplev {tag}"})
            wid = str(uuid.uuid4())
            conn.execute(text(
                "INSERT INTO workflows (id, name, description, owner_email, status, "
                "latest_version, hook_token, organization_id) VALUES (:id, :n, '', :o, "
                "'published', 1, :t, :org)"),
                {"id": wid, "n": f"Person in {tag}", "o": f"owner@{tag.lower()}.pplev.test",
                 "t": f"pplev-{uuid.uuid4().hex}", "org": oid})
            conn.execute(text(
                "INSERT INTO workflow_versions (workflow_id, version, serialized, "
                "published_by, organization_id) VALUES (:w, 1, CAST(:s AS jsonb), 'seed', :org)"),
                {"w": wid, "s": graph, "org": oid})
            conn.execute(text(
                "INSERT INTO workflow_triggers (workflow_id, kind, config, enabled, "
                "organization_id) VALUES (CAST(:w AS uuid), 'event', CAST(:c AS jsonb), "
                "true, CAST(:o AS uuid))"),
                {"w": wid, "c": json.dumps({"source": "people",
                                            "event_type": "people.person.updated"}),
                 "o": oid})
            wf[tag] = wid
    ns = SimpleNamespace(
        admin_engine=eng,
        app_dsn=admin_url.set(database=dedicated, username="acb_app_wftrig",
                              password="wftrig_scratch_pw").render_as_string(
                                  hide_password=False),
        org_a=orgs["A"], org_b=orgs["B"], wf=wf)
    yield ns
    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture(autouse=True)
def _clean(promoted, monkeypatch):
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflow_runs WHERE workflow_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(promoted.wf.values())})
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(triggers.dispatch_event)
    yield
    event_hooks.clear_event_sinks()


def _listen(monkeypatch, *, unattended_orgs: tuple[str, ...], core: str) -> None:
    monkeypatch.setattr(
        unattended, "get_settings",
        lambda: SimpleNamespace(workflow_unattended_orgs=",".join(unattended_orgs)))
    monkeypatch.setattr(core_events, "get_settings",
                        lambda: SimpleNamespace(core_events_orgs=core))


def _emit(promoted, ambient: str | None):
    """The real emit site, on the app role, with *ambient* bound."""

    async def _go():
        async with tenant_engine_scope(promoted.app_dsn):
            token = bind_tenant(ambient) if ambient else None
            try:
                return await core_events.emit_person_updated(_PERSON, _ACTOR, ["title", "phone"])
            finally:
                await _drain_runs()
                if token is not None:
                    release_tenant(token)

    return asyncio.run(_go())


def _runs(promoted, tag: str) -> list:
    with promoted.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT status, trigger_kind, started_by, CAST(organization_id AS text) AS org "
            "FROM workflow_runs WHERE workflow_id = CAST(:w AS uuid)"),
            {"w": promoted.wf[tag]}).fetchall())


def test_the_event_of_org_a_starts_only_the_workflow_of_org_a(promoted, monkeypatch):
    _listen(monkeypatch, unattended_orgs=(promoted.org_a, promoted.org_b), core="*")
    assert _emit(promoted, promoted.org_a) is True
    (row,) = _runs(promoted, "A")
    assert (row.status, row.trigger_kind, row.started_by, row.org) == (
        "succeeded", "event", "event:people", promoted.org_a)
    assert _runs(promoted, "B") == []


def test_the_event_of_org_b_starts_only_the_workflow_of_org_b(promoted, monkeypatch):
    _listen(monkeypatch, unattended_orgs=(promoted.org_a, promoted.org_b), core="*")
    assert _emit(promoted, promoted.org_b) is True
    assert _runs(promoted, "A") == []
    (row,) = _runs(promoted, "B")
    assert row.org == promoted.org_b


def test_no_bound_tenant_emits_and_starts_nothing(promoted, monkeypatch):
    _listen(monkeypatch, unattended_orgs=(promoted.org_a, promoted.org_b), core="*")
    assert _emit(promoted, None) is False
    assert _runs(promoted, "A") == [] and _runs(promoted, "B") == []


def test_the_payload_org_never_decides(promoted, monkeypatch):
    """With B bound, an event whose payload names A starts B's run only."""
    _listen(monkeypatch, unattended_orgs=(promoted.org_a, promoted.org_b), core="*")

    async def _go():
        async with tenant_engine_scope(promoted.app_dsn):
            token = bind_tenant(promoted.org_b)
            try:
                await event_hooks.emit_event(
                    "people", "people.person.updated",
                    core_events.person_payload(_PERSON, _ACTOR, ["title"], promoted.org_a))
            finally:
                await _drain_runs()
                release_tenant(token)

    asyncio.run(_go())
    assert _runs(promoted, "A") == []
    (row,) = _runs(promoted, "B")
    assert row.org == promoted.org_b


def test_with_core_events_orgs_empty_no_run_starts(promoted, monkeypatch):
    _listen(monkeypatch, unattended_orgs=(promoted.org_a, promoted.org_b), core="")
    assert _emit(promoted, promoted.org_a) is False
    assert _runs(promoted, "A") == [] and _runs(promoted, "B") == []


def test_an_org_off_the_unattended_list_starts_no_run(promoted, monkeypatch):
    _listen(monkeypatch, unattended_orgs=(promoted.org_b,), core="*")
    assert _emit(promoted, promoted.org_a) is True
    assert _runs(promoted, "A") == [] and _runs(promoted, "B") == []


def test_an_edit_with_no_changed_directory_key_starts_no_run(promoted, monkeypatch):
    """`update_person` hands over only the keys whose value changed. An empty
    list, or an HR key, emits nothing and starts no run."""
    _listen(monkeypatch, unattended_orgs=(promoted.org_a, promoted.org_b), core="*")

    async def _go(names):
        async with tenant_engine_scope(promoted.app_dsn):
            token = bind_tenant(promoted.org_a)
            try:
                return await core_events.emit_person_updated(_PERSON, _ACTOR, names)
            finally:
                await _drain_runs()
                release_tenant(token)

    assert asyncio.run(_go([])) is False
    assert asyncio.run(_go(["skills"])) is False
    assert _runs(promoted, "A") == [] and _runs(promoted, "B") == []
