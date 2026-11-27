"""Task 4120: ``GET /notes/meetings/{id}/live/wanted`` stays gated.

The meeting-bot worker used to poll this route to decide whether to pay for
streaming ASR. The route is in neither ``main.PUBLIC_ROUTES`` nor the
``exempt`` list of ``core.router``. So the feature gate answered 401 to the
worker, which has no member, before the bot check ran. The worker read each
401 as "wanted". Task 4120 removed the poll from the worker, and kept the
route gated on purpose.

Why the route is not opened: with no member there is no bound tenant. The
route then reads the meeting in an unbound session, and FORCE RLS hides the
row. The answer is the defaults or ``unknown``, never the setting of the
meeting. ``test_notes_meeting_owner_r8.py`` proves the unbound read against a
real database.

These cases pin the decision over HTTP, through the real gate:

* a machine caller with the right bot token gets 401 from the gate, and
  ``live_wanted`` never runs,
* a member who holds ``feature:notes`` still meets the bot check (task 4113),
* the two lists that open a route do not name it.

To open the route, change these cases in the same change that adds a tenant
resolver for the meeting. Do not delete them alone.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TOKEN = "s3cret-bot-token-4120"
PATH = "/notes/meetings/{meeting_id}/live/wanted"
URL = "/notes/meetings/7b0c6c56-0000-4000-8000-000000004120/live/wanted"


def _user(email: str, features: list[str]) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(features))


@pytest.fixture
def wanted_calls(monkeypatch) -> list[str]:
    from gateway.routes.notes import live

    seen: list[str] = []

    async def wanted(meeting_id: str, db: Any = None) -> tuple[bool, str]:
        seen.append(meeting_id)
        return True, "wanted"

    monkeypatch.setattr(live, "live_wanted", wanted)
    monkeypatch.setenv("MEETING_BOT_TOKEN", TOKEN)
    return seen


def _client(user: Any) -> TestClient:
    """The real ``/notes`` router and its feature gate. ``user`` is what
    ``get_current_user`` resolves for the request."""
    from acb_auth import get_current_user
    from gateway.routes.notes import router as notes_router

    app = FastAPI()
    app.include_router(notes_router)

    async def who() -> Any:
        return user

    app.dependency_overrides[get_current_user] = who
    return TestClient(app)


def test_the_worker_gets_401_from_the_gate(wanted_calls) -> None:
    """No member, the right bot token: the gate refuses first."""
    c = _client(_user("", []))
    r = c.get(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 401, r.text
    assert wanted_calls == [], "the route read the meeting with no member"


@pytest.mark.parametrize(
    ("auth", "status"),
    [(None, 401), ("Bearer wrong", 401), (f"Bearer {TOKEN}", 200)],
    ids=["missing", "wrong", "right"],
)
def test_a_member_still_meets_the_bot_check(wanted_calls, auth, status) -> None:
    """Past the gate, the route keeps the one bot check of task 4113."""
    c = _client(_user("alice@notes-4120.test", ["feature:notes"]))
    headers = {"Authorization": auth} if auth else {}
    r = c.get(URL, headers=headers)
    assert r.status_code == status, r.text
    assert wanted_calls == ([URL.split("/")[3]] if status == 200 else [])


def test_an_unset_token_still_answers_503_past_the_gate(monkeypatch) -> None:
    monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    c = _client(_user("alice@notes-4120.test", ["feature:notes"]))
    r = c.get(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 503
    assert r.json()["detail"] == "MEETING_BOT_TOKEN is not configured"


def test_no_list_opens_the_route() -> None:
    """The route is not public and not exempt from the feature gate."""
    from gateway import main
    from gateway.routes.notes import core

    assert PATH not in main.PUBLIC_ROUTES
    dep = core.router.dependencies[0].dependency
    exempt: set[str] = set()
    for cell in dep.__closure__ or ():
        if isinstance(cell.cell_contents, frozenset):
            exempt |= set(cell.cell_contents)
    assert "/notes/meetings/{meeting_id}/live/segment" in exempt
    assert PATH not in exempt
