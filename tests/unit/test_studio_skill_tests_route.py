"""The test run route of a skill (WS-STU F4b, ``ai_studio_rows/F4.md``).

Code: ``gateway/routes/studio/skills.py`` ``run_skill_tests``. Hermetic: the
store runs on the fake session of ``test_studio_skills_routes.py``. The real
``run_suite`` runs with a fake runner and a fake meter, so no model call and
no credit read happen. ``agent_runner`` and ``console_meter`` are replaced.

**The mutations (R7).**

* In ``run_skill_tests``, drop the ``skill_tests_allowed`` test.
  ``test_the_flag_off_is_404_on_every_request_shape`` goes red.
* In ``run_skill_tests``, drop the ``skill_tests_cap`` test.
  ``test_no_cap_is_a_plain_refusal_and_runs_nothing`` goes red.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills.skill_dry_run import HoldAndRecord, current_dry_run
from agent_framework import FunctionTool
from gateway.jobs import headless_turn
from gateway.routes.studio import skills
from gateway.studio import skill_tests as st

from tests.unit import test_studio_skills_routes as _routes
from tests.unit.test_studio_skills_routes import (
    ANA,
    BEN,
    NOT_FOUND,
    ORG,
    OTHER,
    _client,
    _create,
    _open,
)

#: The fake store world of the F2a suite, as a fixture of this module.
world = _routes.world

SECRET = "sk-secret-company-value"
BODY = {"description": "Use it for leads.", "instructions": "Add the lead.",
        "actions": ["object:lead.create"],
        "tests": [{"id": "t1", "input": "Add Ravi from Acme",
                   "expect": [{"action": "object:lead.create",
                               "fields": {"data.company": "Acme"}}]}]}
RUN = "/studio/skills/{sid}/tests/run"


def _record_tool() -> FunctionTool:
    async def studio_record_create(package_id: str = "", object: str = "",
                                   data: dict[str, Any] | None = None) -> str:
        raise AssertionError("a held write ran")

    return FunctionTool(func=studio_record_create, name="studio_record_create", description="d")


@pytest.fixture
def run(monkeypatch, world):
    """The switches on, a cap set, and the fake runner and meter."""
    seen: dict[str, Any] = {"runs": [], "reads": 0}

    async def runner(message: str, *, run_id: str, thread_id: str, org: str,
                     member: str) -> None:
        record = current_dry_run()
        assert record is not None
        seen["runs"].append({"org": org, "member": member, "message": message})
        record.armed = record.loaded = True
        ctx: Any = type("C", (), {})()
        ctx.function, ctx.result = _record_tool(), None
        ctx.arguments = {"package_id": "p1", "object": "lead", "data": {"company": SECRET}}

        async def never() -> None:
            raise AssertionError("a held write ran")

        await HoldAndRecord().process(ctx, never)

    async def meter(org: str, member: str, run_id: str) -> Decimal:
        seen["reads"] += 1
        return Decimal("0.25")

    monkeypatch.setattr(st, "agent_runner", runner)
    monkeypatch.setattr(headless_turn, "console_meter", meter)
    monkeypatch.setattr(st, "_tenant_session", skills._tenant_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_skill_tests_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "studio_skill_tests_cap_credits", "5", raising=False)
    return seen


def _skill(world: Any) -> str:
    # The catalog of the fake world has no object row, so a direct store
    # write makes the version (the route of a version checks the catalog).
    sid = _create(_client(world))["skill"]["id"]
    from tests.unit.test_studio_skills_routes import _FakeDB

    async def add() -> None:
        world.org = ORG
        await skills.store.add_version(_FakeDB(world.tables, ORG), ANA, sid, BODY)

    import asyncio

    asyncio.run(add())
    return sid


def _shapes(sid: str) -> list[dict[str, Any] | None]:
    return [None, {}, {"version": 1}, {"body": BODY}, {"inputs": ["x"]}, {"cap": "1"},
            {"organization_id": ORG, "member": ANA}]


def test_the_flag_off_is_404_on_every_request_shape(monkeypatch, world, run) -> None:
    sid = _skill(world)
    world.log.clear()
    world.store_calls.clear()
    monkeypatch.setattr(get_settings(), "studio_skill_tests_orgs", "", raising=False)
    c = _client(world)
    for shape in _shapes(sid):
        answer = c.post(RUN.format(sid=sid), json=shape)
        assert (answer.status_code, answer.json()) == (404, NOT_FOUND), shape
    assert c.post(RUN.format(sid="not-a-uuid"), json={}).status_code == 404
    assert world.log == [] and world.store_calls == [] and run["runs"] == []
    assert run["reads"] == 0


def test_studio_orgs_off_is_404(monkeypatch, world, run) -> None:
    sid = _skill(world)
    _open(monkeypatch, OTHER)
    answer = _client(world).post(RUN.format(sid=sid), json={})
    assert (answer.status_code, answer.json()) == (404, NOT_FOUND) and run["runs"] == []


def test_another_org_and_another_member_get_404(world, run) -> None:
    sid = _skill(world)
    for org, email in ((OTHER, ANA), (ORG, BEN)):
        answer = _client(world, org=org, email=email).post(RUN.format(sid=sid), json={})
        assert (answer.status_code, answer.json()) == (404, NOT_FOUND), (org, email)
    assert run["runs"] == [] and run["reads"] == 0


def test_no_cap_is_a_plain_refusal_and_runs_nothing(monkeypatch, world, run) -> None:
    sid = _skill(world)
    monkeypatch.setattr(get_settings(), "studio_skill_tests_cap_credits", "", raising=False)
    answer = _client(world).post(RUN.format(sid=sid), json={})
    assert answer.status_code == 409
    assert answer.json()["detail"] == {"code": "cap_unset", "message": skills.CAP_UNSET}
    assert run["runs"] == [] and run["reads"] == 0


def test_a_run_answers_codes_and_names_only(world, run) -> None:
    sid = _skill(world)
    answer = _client(world).post(RUN.format(sid=sid), json={})
    assert answer.status_code == 200, answer.text
    out = answer.json()
    assert SECRET not in answer.text
    assert out["stopped"] is None and out["cap"] == "5" and out["version"] == 1
    only = out["results"][0]
    assert (only["test_id"], only["passed"], only["reason"]) == ("t1", False, "field_mismatch")
    assert only["writes"] == ["object:lead.create"] and only["credits"] == "0.25"
    assert set(only) == {"test_id", "passed", "reason", "detail", "writes", "calls",
                         "credits", "model_calls"}
    assert run["runs"] == [{"org": ORG, "member": ANA, "message": "Add Ravi from Acme"}]


def test_the_org_and_the_member_come_from_the_session(world, run) -> None:
    sid = _skill(world)
    answer = _client(world).post(RUN.format(sid=sid), json={
        "organization_id": OTHER, "member": BEN, "owner_email": BEN})
    assert answer.status_code == 200
    assert run["runs"][0]["org"] == ORG and run["runs"][0]["member"] == ANA


def test_a_request_cap_lowers_the_setting(world, run) -> None:
    sid = _skill(world)
    c = _client(world)
    assert c.post(RUN.format(sid=sid), json={"cap": "2.5"}).json()["cap"] == "2.5"
    assert c.post(RUN.format(sid=sid), json={"cap": "50"}).json()["cap"] == "5"


@pytest.mark.parametrize("shape", [
    {"cap": "0"}, {"cap": "-1"}, {"cap": "1.23456"}, {"cap": "NaN"}, {"cap": 5},
    {"inputs": ["a", "b"]}, {"inputs": [" "]}, {"inputs": ["x" * 2001]},
    {"version": 0}, {"version": True}, {"version": 1, "body": BODY},
], ids=["cap_zero", "cap_negative", "cap_places", "cap_nan", "cap_number", "two_inputs",
        "blank_input", "long_input", "version_zero", "version_bool", "version_and_body"])
def test_a_bad_request_is_422_and_runs_nothing(world, run, shape) -> None:
    sid = _skill(world)
    answer = _client(world).post(RUN.format(sid=sid), json=shape)
    assert answer.status_code == 422, (shape, answer.text)
    assert run["runs"] == []


def test_an_unsaved_body_runs_the_body_checks_first(world, run) -> None:
    sid = _skill(world)
    bad = {**BODY, "actions": ["no.such_op"]}
    answer = _client(world).post(RUN.format(sid=sid), json={"body": bad})
    assert answer.status_code == 422 and "$.actions[0]" in answer.text
    assert run["runs"] == []
    bad_test = {**BODY, "tests": [{"id": "t1"}]}
    assert _client(world).post(RUN.format(sid=sid), json={"body": bad_test}).status_code == 422


def test_a_version_that_does_not_exist_is_404(world, run) -> None:
    sid = _skill(world)
    answer = _client(world).post(RUN.format(sid=sid), json={"version": 9})
    assert (answer.status_code, answer.json()) == (404, NOT_FOUND) and run["runs"] == []
