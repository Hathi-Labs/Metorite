"""Task 487 (H-162): a malformed assignee address is refused before any card.

``_resolve_assignee_name`` let any string with an ``@`` through. A broken one
("anitha@", "a b@c.io", "a@b@c") then reached the route as a bare string. Now
it is refused with a plain sentence. A good address still passes, and a name
still resolves through the picker.

Hermetic: the gateway is stubbed, no database.
"""
from __future__ import annotations

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from acb_common.tool_refusals import GatewayRefusal
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_projects_agent_writes import UUID, responder

BAD = ["anitha@", "@bosch.in", "a b@c.io", "a@b@c.io", "a@b.io,c@d.io", "«x»@y.io",
       "x@y.io»", "<a@b.io>", "a@b.io;c@d.io"]
GOOD = ["Anitha@Bosch.IN", "k@y.test", "first.last+tag@sub.example.com", "x@y"]


@pytest.mark.parametrize("raw", BAD)
async def test_a_malformed_address_is_refused(raw: str, monkeypatch) -> None:
    fake_gateway(monkeypatch, responder)
    with pytest.raises(GatewayRefusal) as exc:
        await W._resolve_assignee_name(raw)
    assert "not a valid email address" in str(exc.value)


@pytest.mark.parametrize("raw", GOOD)
async def test_a_good_address_passes_in_lower_case(raw: str, monkeypatch) -> None:
    fake_gateway(monkeypatch, responder)
    assert await W._resolve_assignee_name(raw) == raw.lower()


async def test_an_agent_name_still_passes(monkeypatch) -> None:
    fake_gateway(monkeypatch, responder)
    assert await W._resolve_assignee_name("Agent:Mira") == "agent:mira"


async def test_a_chat_write_with_a_malformed_assignee_makes_no_card_and_no_write(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    try:
        out = await skill_projects.create_task(UUID, "Survey the site", assignees="anitha@")
    except GatewayRefusal as exc:
        out = str(exc)
    assert "not a valid email address" in out
    assert asked == []
    assert writes(calls) == []
