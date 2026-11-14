"""STU-E12a: a core app emits its events on the one bus (``people.person.updated``).

Spec: ``project-docs/specs/ai_studio.md`` §14 E12a.

Hermetic: the fake DB of ``test_people_write.py`` and a recording sink on the
real bus. The SQL side is none, so the R8 file is ``test_people_events_r8.py``.

What this file holds:

* the catalog and the emit sites match both ways, and the served shape of the
  existing ``pm.*`` events is unchanged;
* one PATCH through each of the three doors emits ONE event with the org;
* the payload holds the changed directory keys and no value;
* an HR or private change, the flag off and no tenant emit nothing;
* a sink that raises never fails the write.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common.db import bind_tenant, release_tenant
from gateway import core_events
from gateway.routes.people import profile as people_profile
from gateway.routes.people import selfservice as people_selfservice
from gateway.routes.tasks import people as tasks_people
from gateway.routes.workflows import catalog
from ingestion import event_hooks

from tests.unit.test_people_write import ADMIN, PERSON, FakeDB, bind, write

ORG = "11111111-aaaa-4aaa-8aaa-111111111111"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def seen():
    got: list[tuple[str, str, dict[str, Any]]] = []

    async def _sink(source: str, event_type: str, payload: dict[str, Any]) -> None:
        got.append((source, event_type, payload))

    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(_sink)
    yield got
    event_hooks.clear_event_sinks()


def _flag(monkeypatch, value: str) -> None:
    monkeypatch.setattr(core_events, "get_settings",
                        lambda: SimpleNamespace(core_events_orgs=value))


@pytest.fixture
def on(monkeypatch):
    """The flag lists the org, and the org is bound for the call."""
    _flag(monkeypatch, ORG)
    token = bind_tenant(ORG)
    yield
    release_tenant(token)


@pytest.fixture
def db(monkeypatch) -> FakeDB:
    database = FakeDB()
    bind(monkeypatch, database)
    for module in (people_profile, people_selfservice):
        monkeypatch.setattr(module, "_tenant_session", tasks_people._tenant_session)

    async def _payload(db, row, user):
        return {}

    monkeypatch.setattr(people_profile, "person_payload", _payload)
    monkeypatch.setattr(people_selfservice, "person_payload", _payload)
    return database


def _patch(door: str, **fields):
    body = write(**fields)
    if door == "tasks":
        return run(tasks_people.update_person(PERSON.id, body, ADMIN))
    if door == "profile":
        return run(people_profile.update_profile(PERSON.id, body, ADMIN))
    return run(people_selfservice.update_me(body, ADMIN))


# ── The catalog and the emit sites ──────────────────────────────────────────

def test_each_catalog_event_has_an_emit_site_and_each_site_a_catalog_entry():
    non_pm = {t["event_type"]: t["source"] for t in catalog.EVENT_TOPICS
              if t["source"] != "projects"}
    assert set(non_pm) == set(core_events.EMIT_SITES)
    assert non_pm == {"people.person.updated": "people"}
    for event_type, site in core_events.EMIT_SITES.items():
        assert callable(site), event_type
    for topic in catalog.EVENT_TOPICS:
        if topic["source"] == "projects":
            assert topic["event_type"].startswith("pm.")
    assert core_events.PEOPLE_SOURCE == "people"
    assert [t for t in catalog.EVENT_TOPICS if t["event_type"] == "people.person.updated"] == [
        {"source": "people", "event_type": "people.person.updated", "label": "Person updated"}]


def test_the_one_list_keeps_the_served_shape_of_the_pm_events():
    pm = [t for t in catalog.EVENT_TOPICS if t["source"] == "projects"]
    assert catalog.PM_EVENT_TOPICS is catalog.EVENT_TOPICS
    assert [t["event_type"] for t in pm] == [
        "pm.task.created", "pm.task.updated", "pm.task.status_changed", "pm.task.assigned",
        "pm.task.moved", "pm.task.deleted", "pm.task.comment_added", "pm.project.created",
        "pm.project.updated", "pm.project.moved", "pm.project.deleted"]
    assert all(set(t) == {"source", "event_type", "label"} for t in catalog.EVENT_TOPICS)
    # The bytes of the first pm entry, as they were before E12a.
    assert json.dumps(pm[0]) == (
        '{"source": "projects", "event_type": "pm.task.created", "label": "Task created"}')
    assert catalog.EVENT_TOPICS[:len(pm)] == pm


def test_every_person_write_field_is_in_exactly_one_tier():
    from gateway.routes.tasks.people import (
        DIRECTORY_FIELDS,
        HR_FIELDS,
        NON_EVENT_FIELDS,
        PRIVATE_FIELDS,
    )

    tiers = [set(DIRECTORY_FIELDS), set(HR_FIELDS), set(PRIVATE_FIELDS), set(NON_EVENT_FIELDS)]
    for field in tasks_people.PersonWrite.model_fields:
        assert sum(field in t for t in tiers) == 1, field
    # A directory key is never an HR or a private key.
    assert not set(DIRECTORY_FIELDS) & (set(HR_FIELDS) | set(PRIVATE_FIELDS))


# ── One PATCH, one event, through each door ─────────────────────────────────

@pytest.mark.parametrize("door", ["tasks", "profile", "me"])
def test_one_patch_emits_one_event_with_the_org(door, db, seen, on):
    _patch(door, title="Lead")
    assert len(seen) == 1
    source, event_type, payload = seen[0]
    assert (source, event_type) == ("people", "people.person.updated")
    assert payload == {"person_id": PERSON.id, "actor": ADMIN.email,
                       "changed": ["title"], "organization_id": ORG}


def test_two_keys_changed_give_one_event_with_sorted_keys(db, seen, on):
    _patch("tasks", title="Lead", name="Priya N", phone="+91 99999 99999",
           capacity_hours_per_week=12)
    assert len(seen) == 1
    assert seen[0][2]["changed"] == ["name", "title"]


def test_the_payload_holds_no_value(db, seen, on):
    _patch("tasks", title="Chief Wizard", team="SecretTeamName", skills=["hidden-skill"])
    (_, _, payload), = seen
    assert set(payload) == {"person_id", "actor", "changed", "organization_id"}
    assert payload["changed"] == ["team", "title"]
    text = repr(payload)
    for value in ("Chief Wizard", "SecretTeamName", "hidden-skill"):
        assert value not in text


@pytest.mark.parametrize("fields", [
    {"skills": ["x"]},
    {"capacity_hours_per_week": 10},
    {"employee_id": "E1", "cost_center": "C1"},
    {"phone": "+91 1", "birthday": "01-02"},
    {"personal_email": "p@example.com", "emergency_contact": {"name": "n"}},
    {"clickup_user_id": "c1"},
])
def test_an_hr_or_private_change_only_emits_nothing(fields, db, seen, on):
    _patch("tasks", **fields)
    assert seen == []


def test_the_flag_off_emits_nothing(monkeypatch, db, seen):
    token = bind_tenant(ORG)
    try:
        for value in ("", "someone-else"):
            _flag(monkeypatch, value)
            _patch("tasks", title="Lead")
    finally:
        release_tenant(token)
    assert seen == []


def test_no_bound_tenant_emits_nothing(monkeypatch, db, seen):
    _flag(monkeypatch, "*")
    _patch("tasks", title="Lead")
    assert seen == []


def test_a_sink_that_raises_never_fails_the_write(monkeypatch, db, seen, on):
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("bus down")

    monkeypatch.setattr(event_hooks, "emit_event", _boom)
    result = _patch("tasks", title="Lead")
    assert result is not None
    assert db.issued("UPDATE people")
    assert run(core_events.emit_person_updated(PERSON.id, "a@b.c", ["title"])) is False


def test_a_registered_sink_that_raises_never_fails_the_write(db, on):
    async def _bad(source: str, event_type: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("sink down")

    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(_bad)
    try:
        assert _patch("tasks", title="Lead") is not None
    finally:
        event_hooks.clear_event_sinks()


# ── `changed` holds the keys whose value changed (round 2) ──────────────────

#: The eight keys that the People editor always sends (`buildWriteBody`), with
#: the values of the stored row.
def _form(**over):
    body = {"name": PERSON.name, "email": PERSON.email, "role": PERSON.role,
            "title": PERSON.title, "department": PERSON.department, "team": PERSON.team,
            "manager_id": PERSON.manager_id, "status": PERSON.status}
    body.update(over)
    return body


def test_a_skills_only_save_of_the_full_form_emits_nothing(db, seen, on):
    _patch("tasks", **_form(skills=["new-skill"]))
    assert seen == []


def test_a_title_change_of_the_full_form_lists_only_title(db, seen, on):
    _patch("tasks", **_form(title="Lead"))
    (_, _, payload), = seen
    assert payload["changed"] == ["title"]


def test_a_no_op_save_emits_nothing(db, seen, on):
    _patch("tasks", **_form())
    assert seen == []


def test_a_key_set_to_the_same_value_is_not_listed(db, seen, on):
    _patch("tasks", **_form(role=PERSON.role, team="Hardware"))
    (_, _, payload), = seen
    assert payload["changed"] == ["team"]


def test_none_and_blank_are_the_same_value(db, seen, on):
    _patch("tasks", **_form(title="", manager_id=""))
    assert seen == []


def test_none_to_a_value_is_listed(db, seen, on):
    _patch("tasks", **_form(reports_to="Ravi"))
    (_, _, payload), = seen
    assert payload["changed"] == ["reports_to"]


def test_a_case_or_whitespace_change_is_a_change_as_the_store_saves_it(db, seen, on):
    _patch("tasks", **_form(team="firmware ", role="engineer"))
    (_, _, payload), = seen
    assert payload["changed"] == ["role", "team"]


@pytest.mark.parametrize("door", ["tasks", "profile", "me"])
def test_each_door_calls_the_emit_once_on_a_real_change(door, db, on, monkeypatch):
    calls: list[Any] = []

    async def _spy(person_id, actor, names):
        calls.append((person_id, actor, list(names)))
        return True

    monkeypatch.setattr(core_events, "emit_person_updated", _spy)
    _patch(door, title="Lead")
    assert calls == [(PERSON.id, ADMIN.email, ["title"])]
