"""``POST /studio/skills/match`` — would a saved skill run on these words
(TOF rung 1, task 3830). Code: ``gateway/routes/studio/skills.py``.

The store read is faked at ``store.list_loadable``. The matchers are the real
ones of the turn (``skill_packs.matches`` and
``tool_search.match_skill_descriptions``), so the route and the turn cannot
drift apart.

**The mutations (R7).**

* Drop the name pass in ``match_skill``: ``test_a_name_in_the_words_matches``
  goes red.
* Drop the ``chat_tools_allowed`` test: ``test_the_description_pass_needs_the_chat_tools_flag``
  goes red.
* Drop the ``skill_offer_allowed`` test: ``test_the_switches_are_404`` goes red.
* Return ``found[0]`` in place of a boolean: ``test_the_answer_is_a_boolean_only`` goes red.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, skills
from gateway.studio import skills_store as store

ORG = str(uuid.uuid4())
ANA = "ana@example.test"


def _head(name: str, slug: str, description: str = "") -> Any:
    return SimpleNamespace(id=str(uuid.uuid4()), name=name, slug=slug, description=description)


@pytest.fixture
def world(monkeypatch):
    seen: dict[str, Any] = {"member": None, "heads": []}

    @asynccontextmanager
    async def _session():
        yield object()

    async def _loadable(db: Any, member: str) -> list[Any]:
        seen["member"] = member
        return list(seen["heads"])

    monkeypatch.setattr(skills, "_tenant_session", _session)
    monkeypatch.setattr(store, "list_loadable", _loadable)
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", "*")
    monkeypatch.setattr(settings, "studio_skill_offer_orgs", "*")
    monkeypatch.setattr(settings, "studio_chat_tools_orgs", "*")
    return seen


def _client(org: str | None = ORG) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        return UserContext(email=ANA, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:studio"]), organization_id=org)

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _ask(words: str, org: str | None = ORG) -> Any:
    return _client(org).post("/studio/skills/match", json={"words": words})


def test_a_name_in_the_words_matches(world):
    world["heads"] = [_head("Onboarding check", "onboarding-check")]
    assert _ask("Please run the onboarding check for Ravi").json() == {"matched": True}
    assert world["member"] == ANA


def test_no_skill_no_name_is_no_match(world):
    world["heads"] = [_head("Onboarding check", "onboarding-check")]
    assert _ask("Look through the knowledge spaces").json() == {"matched": False}
    world["heads"] = []
    assert _ask("Onboarding check").json() == {"matched": False}


def test_a_description_matches_when_no_name_does(world):
    world["heads"] = [_head("Quarterly pass", "qp-1", "Summarise the quarterly revenue figures for the finance review")]
    assert _ask("Summarise the quarterly revenue figures for the finance review please").json() == {"matched": True}


def test_the_description_pass_needs_the_chat_tools_flag(world, monkeypatch):
    world["heads"] = [_head("Quarterly pass", "qp-1", "Summarise the quarterly revenue figures for the finance review")]
    monkeypatch.setattr(get_settings(), "studio_chat_tools_orgs", "")
    assert _ask("Summarise the quarterly revenue figures for the finance review please").json() == {"matched": False}


def test_the_answer_is_a_boolean_only(world):
    world["heads"] = [_head("Onboarding check", "onboarding-check", "Secret words about payroll")]
    body = _ask("run the onboarding check").json()
    assert set(body) == {"matched"} and body["matched"] is True


def test_the_switches_are_404(world, monkeypatch):
    world["heads"] = [_head("Onboarding check", "onboarding-check")]
    monkeypatch.setattr(get_settings(), "studio_skill_offer_orgs", "")
    assert _ask("onboarding check").status_code == 404
    monkeypatch.setattr(get_settings(), "studio_skill_offer_orgs", ORG)
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    assert _ask("onboarding check").status_code == 404
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    assert _ask("onboarding check", org=None).status_code == 404
    assert _ask("onboarding check").status_code == 200


def test_a_body_with_no_words_is_422(world):
    assert _client().post("/studio/skills/match", json={}).status_code == 422
