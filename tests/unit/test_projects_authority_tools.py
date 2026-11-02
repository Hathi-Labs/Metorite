"""WS-ALN ALN-4 repair: the chat reaches the pin of a Knowledge page (D-OP-9).

Spec: ``project-docs/specs/project_alignment.md`` §4.1 ("Chat tools"). Code:
``skill_projects/inbox.py`` ``project_authorities`` (class A) and
``skill_projects/guarded.py`` ``pin_authority`` and ``unpin_authority``
(class C, one card each). The gateway is a fake. The R8 fence of the routes is
``test_project_authorities_r8.py``.

* The manifest maps the three routes to the three tools.
* A write shows its card first, and a declined card writes nothing.
* A card names the page title only when the gateway says the member can read
  the page (``page_hidden`` false). A hidden page shows no title anywhere.
* A refusal (403, 404, 409) comes back as ONE plain sentence.
* Text that other people wrote goes through ``data(...)``.

**The mutations (R7).** Commit before each, restore with ``git checkout``.
``S`` is ``apps/skills/skill-projects/skill_projects``.

* M1. ``S/manifest.py``: delete the ``pin_authority`` row.
  ``test_the_manifest_maps_the_three_routes`` and the coverage fence go red.
* M2. ``S/guarded.py`` ``pin_authority``: skip the ``_confirm`` card.
  ``test_a_declined_card_writes_nothing`` goes red.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    writes,
)

PID = str(uuid.uuid4())
PARENT = str(uuid.uuid4())
PAGE = str(uuid.uuid4())
OTHER_PAGE = str(uuid.uuid4())
PIN = str(uuid.uuid4())
LAUNCH = {"id": PID, "name": "Launch", "parent_project_id": PARENT, "can_write_grants": True}
READABLE = {"id": PIN, "project_id": PID, "page_id": PAGE, "page_version": 2,
            "pinned_by": "lead@acme.test", "state": "active", "stale_reason": None,
            "page_hidden": False, "title": "Launch rules"}
HIDDEN = {"id": PIN, "project_id": PID, "page_id": PAGE, "page_version": None,
          "pinned_by": "lead@acme.test", "state": "stale", "stale_reason": None,
          "page_hidden": True}
SECRET = "Secret board plan"


def _gateway(monkeypatch, node=LAUNCH, pins=None, post=None, delete=None):
    rows = list(pins or [])

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        base = f"/projects/nodes/{PID}"
        if path == base and method == "GET":
            return node
        if path == f"/projects/nodes/{PARENT}" and method == "GET":
            return {"id": PARENT, "name": "Product", "parent_project_id": None}
        if path == f"{base}/authorities" and method == "GET":
            return {"rows": rows, "total": len(rows)}
        if path == f"{base}/authorities" and method == "POST":
            return post if post is not None else {**READABLE, "id": str(uuid.uuid4())}
        if path == f"{base}/authorities/{PIN}" and method == "DELETE":
            return delete if delete is not None else {**READABLE, "state": "removed"}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, answer)


def test_the_manifest_maps_the_three_routes() -> None:
    base = "/projects/nodes/{project_id}/authorities"
    got = {(r.method, r.path): (r.tool, r.cls) for r in manifest.MANIFEST if "authorities" in r.path}
    assert got == {
        ("GET", base): ("project_authorities", "A"),
        ("POST", base): ("pin_authority", "C"),
        ("DELETE", base + "/{pin_id}"): ("unpin_authority", "C"),
    }
    for tool in ("project_authorities", "pin_authority", "unpin_authority"):
        assert tool in skill_projects.__all__


async def test_a_pin_shows_one_card_first_and_then_writes(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.pin_authority(PID, PAGE)
    assert len(asked) == 1
    assert asked[0]["detail"] == f"«Product» / «Launch» · page {PAGE}"
    sent = [(c["method"], c["path"], c["json"]) for c in writes(calls)]
    assert sent == [("POST", f"/projects/nodes/{PID}/authorities", {"page_id": PAGE})]
    assert "is now the authority page of «Product» / «Launch»" in out
    assert "«Launch rules»" in out, "a title that people wrote is fenced by data()"


async def test_with_the_flag_off_no_tool_makes_a_card_or_a_write(monkeypatch) -> None:
    """The route answers 404 while AUTHORITY_PINS_ORGS lacks the org. The tool
    reads first, so a flag that is off shows no card and sends no POST."""
    asked = approve(monkeypatch)

    def off(call: dict) -> Any:
        if call["path"].endswith("/authorities") or "/authorities/" in call["path"]:
            return FakeResponse({"detail": "Not Found"}, 404)
        return LAUNCH if call["path"] == f"/projects/nodes/{PID}" else {"rows": []}

    calls = fake_gateway(monkeypatch, off)
    outs = [
        await skill_projects.pin_authority(PID, PAGE),
        await skill_projects.unpin_authority(PID, PIN),
        await skill_projects.project_authorities(PID),
    ]
    assert all("not on for this organization yet" in o for o in outs), outs
    assert asked == [], "no confirmation was requested"
    assert [c for c in calls if c["method"] != "GET"] == [], "no POST or DELETE was sent"


async def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch, pins=[])
    assert await skill_projects.pin_authority(PID, PAGE) == W.CANCELLED
    calls += _gateway(monkeypatch, pins=[READABLE])
    assert await skill_projects.unpin_authority(PID, PIN) == W.CANCELLED
    assert writes(calls) == []


async def test_a_member_who_may_not_write_gets_no_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    node = {**LAUNCH, "can_write_grants": False, "grant_refusal": "Only a lead may do this."}
    calls = _gateway(monkeypatch, node=node, pins=[READABLE])
    for out in (await skill_projects.pin_authority(PID, PAGE),
                await skill_projects.unpin_authority(PID, PIN)):
        assert out.startswith("Refused:"), out
    assert asked == [] and writes(calls) == []


async def test_a_page_already_pinned_shows_no_card_and_hides_a_title_the_member_cannot_read(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, pins=[READABLE])
    out = await skill_projects.pin_authority(PID, PAGE)
    assert "«Launch rules» is already the authority page" in out
    calls += _gateway(monkeypatch, pins=[{**HIDDEN, "title": SECRET}])
    out = await skill_projects.pin_authority(PID, PAGE)
    assert "a page you cannot read is already" in out and SECRET not in out
    assert asked == [] and writes(calls) == []


@pytest.mark.parametrize(("status", "detail", "want"), [
    (403, "Not allowed", "may not change the authority page"),
    (404, "Page not found", "was not found, or you cannot see it"),
    (409, "This page is visible to fewer people than this project.",
     "«This page is visible to fewer people than this project.»"),
    (409, "Only a published page can be the authority of a project.",
     "Only a published page"),
])
async def test_a_refusal_of_the_route_is_one_plain_sentence(
    monkeypatch, status, detail, want,
) -> None:
    approve(monkeypatch)
    _gateway(monkeypatch, pins=[], post=FakeResponse({"detail": detail}, status))
    out = await skill_projects.pin_authority(PID, OTHER_PAGE)
    assert out.startswith("Refused:") and want in out and "\n" not in out, out
    assert out.endswith("Nothing was changed.")


async def test_an_unpin_names_the_title_only_for_a_page_the_member_can_read(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, pins=[READABLE])
    out = await skill_projects.unpin_authority(PID, PIN.upper())
    assert asked[-1]["detail"] == "«Product» / «Launch» · remove «Launch rules»"
    assert [(c["method"], c["path"]) for c in writes(calls)] == [
        ("DELETE", f"/projects/nodes/{PID}/authorities/{PIN}")
    ]
    assert out.startswith("Removed «Launch rules» as the authority page")
    calls = _gateway(monkeypatch, pins=[{**HIDDEN, "title": SECRET}])
    out = await skill_projects.unpin_authority(PID, PIN)
    assert asked[-1]["detail"] == "«Product» / «Launch» · remove a page you cannot read"
    assert SECRET not in asked[-1]["context"] and SECRET not in out


async def test_an_unknown_pin_shows_no_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, pins=[READABLE])
    out = await skill_projects.unpin_authority(PID, str(uuid.uuid4()))
    assert "has no pin like that" in out
    assert asked == [] and writes(calls) == []


async def test_a_list_of_ids_is_refused_before_any_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    assert "ONE" in await skill_projects.pin_authority(PID, f"{PAGE},{OTHER_PAGE}")
    assert "ONE" in await skill_projects.unpin_authority(PID, f"{PIN},{PIN}")
    assert asked == [] and calls == []


async def test_the_read_fences_titles_and_hides_a_page_the_member_cannot_read(monkeypatch) -> None:
    pins = [{**READABLE, "title": "Rules «x»"}, {**HIDDEN, "id": str(uuid.uuid4()),
                                                 "title": SECRET, "page_id": OTHER_PAGE}]
    calls = _gateway(monkeypatch, pins=pins)
    out = await skill_projects.project_authorities(PID)
    assert "(2 pins)" in out and "pin_id " + PIN in out
    assert "a page you cannot read · stale" in out and SECRET not in out
    assert "stale (" not in out, "a hidden page gives no reason"
    assert all(c["method"] == "GET" for c in calls)
