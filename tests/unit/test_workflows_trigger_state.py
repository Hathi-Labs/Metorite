"""STB-6c: arm or disarm one trigger, and the gate of a Save (hermetic half).

Spec: ``project-docs/specs/ai_studio.md`` row E13c (task 3119). Owner decision
of 2026-11-13 (run answer 09bcb706, D-number pending): arming a trigger on a
published workflow needs ``workflows:publish``.

Code: ``set_trigger_state``, ``refuse_live_arming`` and
``arms_a_live_trigger`` in ``gateway/routes/workflows/publish.py``, and the
call in ``crud.update_workflow``. The R8 half, with real roles under FORCE RLS
and two orgs, is ``test_workflows_trigger_state_r8.py``. The route table and
the class of the route are in ``test_workflows_author_gate.py``.

A fake session here keeps the triggers in a list. It reads no SQL beyond the
first words of each statement, so the R8 half proves each statement.

**The mutations (R7).** Each ran on 2026-11-13, and each test named here went
red.

* M1: drop ``dependencies=[require_permission(PUBLISH_PERMISSION)]`` from the
  route. Red: ``test_a_member_without_the_right_gets_403_and_no_session``,
  two route table tests of ``test_workflows_author_gate.py``, and
  ``test_live_routes_all_require_publish_authority``.
* M2: drop the ``refuse_live_arming`` call of ``update_workflow``. Red: the
  four cases of ``test_a_save_that_arms_a_published_trigger_needs_the_right``.
* M3 (drop ``id = :tid AND`` from the UPDATE statements) goes red only in the
  R8 half, because this fake reads no WHERE clause.
* M4: ``_pick_trigger`` ignores ``trigger_id`` and takes the first trigger.
  Red: ``test_an_id_arms_that_trigger_and_no_other`` and
  ``test_an_id_of_another_kind_gets_404_and_writes_nothing``.
* M5: the route skips ``_refuse_foreign_studio_trigger``. Red:
  ``test_another_member_cannot_turn_on_a_studio_trigger``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway.routes.workflows import crud, publish
from gateway.routes.workflows import router as workflows_router

AUTHOR = "author@example.test"
OTHER = "other@example.test"
WID = str(uuid.uuid4())

MEMBER = ["feature:workflows"]
PUBLISHER = ["feature:workflows", "workflows:publish"]

_STUDIO = {"source": "studio", "event_type": "studio.record.created",
           "filter": {"object": "claim"}}


def _trigger(kind: str, enabled: bool, config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), kind=kind, config=dict(config or {}),
                           enabled=enabled, last_fired_at=None)


class _FakeDb:
    """One workflow and its triggers, in memory."""

    def __init__(self, status: str, triggers: list[SimpleNamespace],
                 *, latest_version: int | None = 1, lose_update: bool = False) -> None:
        self.workflow = SimpleNamespace(
            id=WID, name="Nightly", description="", owner_email=AUTHOR, status=status,
            latest_version=latest_version, graph={}, variables={}, hook_token="tok-secret",
            created_at=None, updated_at=None, disabled_reason=None, disabled_at=None,
        )
        self.triggers = triggers
        self.lose_update = lose_update
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        sql = " ".join(str(clause).split())
        params = params or {}
        self.statements.append(sql)
        self.params.append(params)
        value: Any = None
        if sql.startswith("SELECT * FROM workflows"):
            value = self.workflow
        elif sql.startswith("SELECT") and "FROM workflow_triggers" in sql:
            value = list(self.triggers)
        elif sql.startswith("UPDATE workflow_triggers"):
            hit = [t for t in self.triggers if t.id == params["tid"]
                   and t.kind == params["kind"]]
            if hit and not self.lose_update:
                hit[0].enabled = params["on"]
                if "last_fired_at = NULL" in sql:
                    hit[0].last_fired_at = None
                value = SimpleNamespace(id=hit[0].id)
        elif sql.startswith("DELETE FROM workflow_triggers"):
            self.triggers = []
        elif sql.startswith("INSERT INTO workflow_triggers"):
            self.triggers.append(SimpleNamespace(
                id=uuid.uuid4(), kind=params["kind"], config=params["config"],
                enabled=params["enabled"], last_fired_at=params.get("last_fired")))

        class _Result:
            def fetchone(self) -> Any:
                return value

            def fetchall(self) -> list:
                return value or []

        return _Result()

    def writes(self) -> list[str]:
        return [s for s in self.statements if not s.startswith("SELECT")]


class _Sessions(list):
    """The fakes of one test. A session opens on ``[0]``, and ``opened``
    counts each open."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[str] = []


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> _Sessions:
    dbs = _Sessions()

    @asynccontextmanager
    async def _session() -> Any:
        dbs.opened.append("open")
        yield dbs[0]

    monkeypatch.setattr(publish, "_tenant_session", _session)
    monkeypatch.setattr(crud, "_tenant_session", _session)
    return dbs


def _client(grants: list[str], email: str = AUTHOR) -> TestClient:
    app = FastAPI()
    app.include_router(workflows_router)

    def _user() -> UserContext:
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           organization_id=str(uuid.uuid4()), access=build_access(grants))

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app, raise_server_exceptions=False)


def _state(client: TestClient, kind: str, body: Any) -> Any:
    return client.post(f"/workflows/{WID}/triggers/{kind}/state", json=body)


# ── The pure rule of a Save ──────────────────────────────────────────────────


_CRON = {"cron": "0 9 * * *"}


@pytest.mark.parametrize("old,new,arms", [
    ([], [("schedule", _CRON, True)], True),                      # add
    ([("webhook", {}, False)], [("webhook", {}, True)], True),     # turn on
    ([("schedule", _CRON, True)], [("schedule", {"cron": "* * * * *"}, True)], True),
    ([("event", {"source": "zoho"}, True)],
     [("event", {"source": "zoho"}, True)] * 2, True),             # a second copy
    ([("schedule", _CRON, True)], [("schedule", _CRON, True)], False),  # unchanged
    ([("schedule", _CRON, True)], [("schedule", _CRON, False)], False),  # turn off
    ([("schedule", _CRON, True)], [], False),                      # remove
    ([], [("manual", {}, True), ("api", {}, True)], False),       # no state
    ([], [("schedule", _CRON, False)], False),                     # add, off
    ([("event", {"a": 1, "b": 2}, True)], [("event", {"b": 2, "a": 1}, True)], False),
])
def test_arms_a_live_trigger(old: list, new: list, arms: bool) -> None:
    assert publish.arms_a_live_trigger(old, new) is arms


class _NoRightRead:
    """A caller whose right must not be read."""

    def has_permission(self, permission: str) -> bool:  # pragma: no cover - a trap
        raise AssertionError("the right was read for a Save that arms nothing")


def test_the_check_reads_the_right_only_when_a_published_save_arms() -> None:
    arm = [("schedule", _CRON, True)]
    publish.refuse_live_arming(_NoRightRead(), "published", arm, arm)
    publish.refuse_live_arming(_NoRightRead(), "draft", [], arm)
    publish.refuse_live_arming(_NoRightRead(), "disabled", [], arm)
    member = UserContext(email=OTHER, role=UserRole.EMPLOYEE, access=build_access(MEMBER))
    with pytest.raises(HTTPException) as err:
        publish.refuse_live_arming(member, "published", [], arm)
    assert err.value.status_code == 403
    assert err.value.detail == publish.ARMING_REFUSAL
    holder = UserContext(email=OTHER, role=UserRole.EMPLOYEE, access=build_access(PUBLISHER))
    publish.refuse_live_arming(holder, "published", [], arm)


def test_the_armable_kinds_are_the_unattended_kinds() -> None:
    from gateway.routes.workflows.service import UNATTENDED_TRIGGERS

    assert publish.ARMABLE_KINDS == UNATTENDED_TRIGGERS == {"schedule", "webhook", "event"}


# ── The route: the gate and the body ─────────────────────────────────────────


def test_a_member_without_the_right_gets_403_and_no_session(sessions: _Sessions) -> None:
    sessions.append(_FakeDb("published", [_trigger("schedule", False, _CRON)]))
    r = _state(_client(MEMBER), "schedule", {"enabled": True})
    assert r.status_code == 403 and "workflows:publish" in r.text, r.text
    assert sessions.opened == []


@pytest.mark.parametrize("kind", ["manual", "api"])
def test_a_trigger_with_no_state_gets_a_plain_422(sessions: _Sessions, kind: str) -> None:
    sessions.append(_FakeDb("published", [_trigger(kind, True)]))
    r = _state(_client(PUBLISHER), kind, {"enabled": False})
    assert r.status_code == 422, r.text
    assert "always available" in r.json()["detail"]
    assert sessions.opened == []


def test_an_unknown_kind_gets_422(sessions: _Sessions) -> None:
    sessions.append(_FakeDb("published", []))
    r = _state(_client(PUBLISHER), "cron", {"enabled": True})
    assert r.status_code == 422 and "Unknown trigger kind" in r.text
    assert sessions.opened == []


@pytest.mark.parametrize("body", [
    {"enabled": "yes"},                                  # not a JSON boolean
    {"enabled": 1},
    {},
    {"enabled": True, "trigger_id": "not-a-uuid"},
    {"enabled": True, "organization_id": str(uuid.uuid4())},  # no tenant from input
])
def test_a_bad_body_gets_422(sessions: _Sessions, body: dict) -> None:
    sessions.append(_FakeDb("published", [_trigger("schedule", False, _CRON)]))
    assert _state(_client(PUBLISHER), "schedule", body).status_code == 422
    assert sessions.opened == []


# ── The route: one trigger ───────────────────────────────────────────────────


def test_arming_the_one_schedule_resets_its_baseline_and_hides_the_config(
        sessions: _Sessions) -> None:
    tick = _trigger("schedule", False, {"cron": "0 9 * * *", "secret": "s3cret"})
    tick.last_fired_at = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    db = _FakeDb("published", [tick])
    sessions.append(db)
    r = _state(_client(PUBLISHER), "schedule", {"enabled": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "workflow_id": WID, "trigger_id": str(tick.id), "kind": "schedule",
        "enabled": True, "changed": True, "workflow_status": "published", "live": True,
        "note": "The trigger is on, and the workflow is published, so the trigger is live.",
    }
    assert "s3cret" not in r.text and "tok-secret" not in r.text
    assert tick.enabled is True and tick.last_fired_at is None
    update = next(s for s in db.statements if s.startswith("UPDATE workflow_triggers"))
    assert "last_fired_at = NULL" in update
    assert "FOR UPDATE" in next(s for s in db.statements if "FROM workflow_triggers" in s)
    assert db.writes()[-1].startswith("UPDATE workflows SET updated_at")


def test_an_id_arms_that_trigger_and_no_other(sessions: _Sessions) -> None:
    first, second = _trigger("event", False, {"source": "zoho"}), _trigger(
        "event", False, {"source": "gmail"})
    hook = _trigger("webhook", False)
    sessions.append(_FakeDb("published", [first, second, hook]))
    r = _state(_client(PUBLISHER), "event", {"enabled": True, "trigger_id": str(second.id)})
    assert r.status_code == 200, r.text
    assert r.json()["trigger_id"] == str(second.id)
    assert (first.enabled, second.enabled, hook.enabled) == (False, True, False)


def test_two_of_a_kind_and_no_id_gets_409_and_writes_nothing(sessions: _Sessions) -> None:
    db = _FakeDb("published", [_trigger("schedule", False, _CRON),
                               _trigger("schedule", False, {"cron": "0 10 * * *"})])
    sessions.append(db)
    r = _state(_client(PUBLISHER), "schedule", {"enabled": True})
    assert r.status_code == 409 and "trigger_id" in r.json()["detail"], r.text
    assert db.writes() == []


def test_an_id_of_another_kind_gets_404_and_writes_nothing(sessions: _Sessions) -> None:
    hook = _trigger("webhook", False)
    db = _FakeDb("published", [hook, _trigger("schedule", False, _CRON)])
    sessions.append(db)
    r = _state(_client(PUBLISHER), "schedule", {"enabled": True, "trigger_id": str(hook.id)})
    assert r.status_code == 404, r.text
    assert db.writes() == [] and hook.enabled is False


def test_no_trigger_of_the_kind_gets_404(sessions: _Sessions) -> None:
    sessions.append(_FakeDb("published", [_trigger("webhook", True)]))
    r = _state(_client(PUBLISHER), "schedule", {"enabled": True})
    assert r.status_code == 404 and "no schedule trigger" in r.text


def test_the_same_state_again_changes_nothing(sessions: _Sessions) -> None:
    db = _FakeDb("published", [_trigger("webhook", True)])
    sessions.append(db)
    r = _state(_client(PUBLISHER), "webhook", {"enabled": True})
    assert r.status_code == 200 and r.json()["changed"] is False
    assert db.writes() == []


def test_a_disarm_keeps_the_baseline_and_says_off(sessions: _Sessions) -> None:
    tick = _trigger("schedule", True, _CRON)
    tick.last_fired_at = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    db = _FakeDb("published", [tick])
    sessions.append(db)
    r = _state(_client(PUBLISHER), "schedule", {"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["live"] is False and r.json()["note"] == "The trigger is off. It starts nothing."
    assert tick.enabled is False and tick.last_fired_at is not None


@pytest.mark.parametrize("status,words", [("draft", "not published"), ("disabled", "disabled")])
def test_arming_a_workflow_that_is_not_live_writes_the_flag_and_says_so(
        sessions: _Sessions, status: str, words: str) -> None:
    hook = _trigger("webhook", False)
    sessions.append(_FakeDb(status, [hook], latest_version=None if status == "draft" else 1))
    r = _state(_client(PUBLISHER), "webhook", {"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["live"] is False and r.json()["workflow_status"] == status
    assert words in r.json()["note"]
    assert hook.enabled is True


def test_a_lost_row_gets_409(sessions: _Sessions) -> None:
    sessions.append(_FakeDb("published", [_trigger("webhook", False)], lose_update=True))
    r = _state(_client(PUBLISHER), "webhook", {"enabled": True})
    assert r.status_code == 409, r.text


# ── The route: the E3 author rule ────────────────────────────────────────────


def test_another_member_cannot_turn_on_a_studio_trigger(sessions: _Sessions) -> None:
    studio = _trigger("event", False, _STUDIO)
    db = _FakeDb("published", [studio])
    sessions.append(db)
    r = _state(_client(PUBLISHER, email=OTHER), "event", {"enabled": True})
    assert r.status_code == 403, r.text
    assert "Only the author of this workflow" in r.json()["detail"]
    assert studio.enabled is False and db.writes() == []


def test_the_author_turns_on_a_studio_trigger_and_another_member_turns_it_off(
        sessions: _Sessions) -> None:
    studio = _trigger("event", False, _STUDIO)
    sessions.append(_FakeDb("published", [studio]))
    r = _state(_client(PUBLISHER, email=AUTHOR.upper()), "event", {"enabled": True})
    assert r.status_code == 200 and studio.enabled is True, r.text
    r = _state(_client(PUBLISHER, email=OTHER), "event", {"enabled": False})
    assert r.status_code == 200 and studio.enabled is False, r.text


def test_another_member_turns_on_an_event_of_another_source(sessions: _Sessions) -> None:
    zoho = _trigger("event", False, {"source": "zoho"})
    sessions.append(_FakeDb("published", [zoho]))
    r = _state(_client(PUBLISHER, email=OTHER), "event", {"enabled": True})
    assert r.status_code == 200 and zoho.enabled is True, r.text


# ── The Save (PUT) gate ──────────────────────────────────────────────────────


def _save(sessions: _Sessions, status: str, old: list, new: list, grants: list[str],
          **extra: Any) -> tuple[Any, _FakeDb]:
    db = _FakeDb(status, old)
    sessions.append(db)
    body = {"triggers": [{"kind": k, "config": c, "enabled": e} for k, c, e in new], **extra}
    return _client(grants).put(f"/workflows/{WID}", json=body), db


@pytest.mark.parametrize("old,new", [
    ([], [("schedule", _CRON, True)]),
    ([("webhook", {}, False)], [("webhook", {}, True)]),
    ([("event", {"source": "zoho"}, False)], [("event", {"source": "zoho"}, True)]),
    ([("schedule", _CRON, True)], [("schedule", {"cron": "* * * * *"}, True)]),
])
def test_a_save_that_arms_a_published_trigger_needs_the_right(
        sessions: _Sessions, old: list, new: list) -> None:
    olds = [_trigger(k, e, c) for k, c, e in old]
    r, db = _save(sessions, "published", olds, new, MEMBER)
    assert r.status_code == 403 and r.json()["detail"] == publish.ARMING_REFUSAL, r.text
    assert db.writes() == []
    assert any("FOR UPDATE" in s for s in db.statements)


@pytest.mark.parametrize("old,new", [
    ([], [("schedule", _CRON, True)]),
    ([("webhook", {}, False)], [("webhook", {}, True)]),
])
def test_a_save_that_arms_works_for_a_holder_of_the_right(
        sessions: _Sessions, old: list, new: list) -> None:
    r, db = _save(sessions, "published", [_trigger(k, e, c) for k, c, e in old], new, PUBLISHER)
    assert r.status_code == 200, r.text
    assert [t.enabled for t in db.triggers] == [True]


@pytest.mark.parametrize("old,new", [
    ([("schedule", _CRON, True)], [("schedule", _CRON, True)]),     # unchanged
    ([("schedule", _CRON, True)], [("schedule", _CRON, False)]),    # turn off
    ([("webhook", {}, True)], []),                                  # remove
    ([], [("manual", {}, True)]),                                   # no state
    ([], [("webhook", {}, False)]),                                 # add, off
])
def test_a_save_that_arms_nothing_needs_no_right(sessions: _Sessions, old: list, new: list) -> None:
    r, _db = _save(sessions, "published", [_trigger(k, e, c) for k, c, e in old], new, MEMBER)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("status", ["draft", "disabled"])
def test_a_save_of_a_workflow_that_is_not_live_needs_no_right(
        sessions: _Sessions, status: str) -> None:
    r, db = _save(sessions, status, [], [("schedule", _CRON, True)], MEMBER)
    assert r.status_code == 200, r.text
    assert [t.enabled for t in db.triggers] == [True]


def test_a_save_with_no_triggers_reads_no_trigger(sessions: _Sessions) -> None:
    db = _FakeDb("published", [_trigger("schedule", True, _CRON)])
    sessions.append(db)
    r = _client(MEMBER).put(f"/workflows/{WID}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert not any("FOR UPDATE" in s for s in db.statements)


# ── The R8 half runs in CI ───────────────────────────────────────────────────


def test_the_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    """A fence that never runs is no fence. The ``--must-run`` list names the
    pytest ARGUMENT, so a mention in a comment does not count."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_workflows_trigger_state_r8\.py(?: \\)?$",
                     block.group(1), re.M)
