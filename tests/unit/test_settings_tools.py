"""PAR-5 (task 3108): the member's settings from the chat.

Spec: ``project-docs/specs/chat_parity.md`` §6.3, rung 3. The module is
``packages/acb_skills/acb_skills/settings_tools.py``, with
``profile_tools.profile_read``. The pack is ``settings``, and its owner is the
pack-only owner ``settings-keeper``.

The fence holds:

* each tool sends its route as the run's member, and no tool takes a member,
  an address or an org (R5);
* a change to the member's own setting acts at once with no card, and its
  answer names the old value (D-CF-5). A run that must show a card shows one;
* an organization default, the logo style and the logo removal show one card,
  and a card that did nothing sends no write;
* the logo read carries no image bytes;
* the pack is dark: no surface, not in FOLLOWS, and off where the list names
  only other packs.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import httpx
import pytest
from acb_skills import profile_tools as pt
from acb_skills import settings_tools as st
from acb_skills import tool_packs as tp
from acb_skills.skill_families import SKILL_FAMILIES

MEMBER = "ravi@acme.com"


class Gateway:
    """A fake gateway: canned answers by ``(verb, path)``, and a log of calls."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, Any, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        key = (request.method, request.url.path)
        self.calls.append((*key, body, request.headers.get("X-User-Email", "")))
        status, data = self.answers.get(key, (404, {"detail": "Not found"}))
        return httpx.Response(status, json=data)

    def writes(self) -> list[tuple[str, str, Any]]:
        return [(v, p, b) for v, p, b, _m in self.calls if v != "GET"]


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch):
    """The member is verified, no card is due, and the gateway is fake."""
    holder: dict[str, Gateway] = {}

    def make(answers: dict[tuple[str, str], tuple[int, Any]]) -> Gateway:
        gate = Gateway(answers)
        holder["gw"] = gate
        monkeypatch.setattr(st, "_transport", lambda: httpx.MockTransport(gate.handler))
        return gate

    monkeypatch.setattr(st, "_member", lambda: MEMBER)

    async def no_card() -> bool:
        return False

    monkeypatch.setattr(st, "_must_ask", no_card)
    return make


@pytest.fixture()
def cards(monkeypatch: pytest.MonkeyPatch):
    """Record each card. The answer is ``cards.answer`` (True approves)."""

    class Cards(list):
        answer = True

    shown = Cards()

    async def approved(title: str, detail: str, member: str) -> bool:
        shown.append((title, detail, member))
        return shown.answer

    monkeypatch.setattr(st, "_approved", approved)
    monkeypatch.setattr(st, "_not_done", lambda act: f"Not done. {act}")
    return shown


# ── R5: no tool takes a member, an address or an org ──────────────────────


@pytest.mark.parametrize("fn", [*st.SETTINGS_TOOLS, *pt.READ_TOOLS])
def test_no_tool_takes_a_member_or_an_org(fn) -> None:
    params = set(inspect.signature(fn).parameters)
    for bad in ("member", "email", "user", "org", "organization_id", "tenant"):
        assert bad not in params, (fn.__name__, bad)


def test_each_tool_carries_its_risk() -> None:
    reads = {"settings_read", "profile_read"}
    for fn in (*st.SETTINGS_TOOLS, *pt.READ_TOOLS):
        risk = fn.__tool_risk__
        assert risk["open_world"] is False, fn.__name__
        assert risk["read_only"] is (fn.__name__ in reads), fn.__name__
    assert st.settings_remove_logo.__tool_risk__["destructive"] is True
    # Review round 1, P1-b: a style change can delete the dark-mode image.
    assert st.settings_set_logo_style.__tool_risk__["destructive"] is True


async def test_no_member_sends_no_request(gw, monkeypatch) -> None:
    gate = gw({})
    monkeypatch.setattr(st, "_member", lambda: "")
    assert await st.settings_set_time_zone("Asia/Kolkata") == st.NO_MEMBER
    assert await st.settings_remove_logo() == st.NO_MEMBER
    assert gate.calls == []


# ── The read ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("part", "path"), [
    ("appearance", "/auth/me/appearance"), ("org_appearance", "/settings/appearance"),
    ("number_system", "/settings/number-system"), ("logo", "/settings/branding"),
    ("time_zone", "/auth/me/zone"), ("sidebar", "/auth/me/shell"),
    ("agent_access", "/auth/me/agent-access")])
async def test_the_read_sends_the_route_of_its_part_as_the_member(gw, part, path) -> None:
    gate = gw({("GET", path): (200, {"timezone": "Asia/Kolkata"})})
    out = await st.settings_read(part)
    assert out.startswith(st.READ_HEADER)
    assert [(v, p, m) for v, p, _b, m in gate.calls] == [("GET", path, MEMBER)]


async def test_the_logo_read_carries_no_image_bytes(gw) -> None:
    logo = {"dataUri": "data:image/png;base64,AAAA", "mime": "image/png", "width": 200,
            "height": 50, "byteSize": 3}
    gw({("GET", "/settings/branding"): (200, {"logo": logo, "darkStyle": "same"})})
    out = await st.settings_read("logo")
    assert "base64" not in out and "image/png" in out


async def test_a_read_of_a_closed_setting_says_so(gw) -> None:
    gw({})
    assert await st.settings_read("agent_access") == st.NOT_ON


# ── My own settings: at once, with the old value ───────────────────────────


async def test_my_look_changes_at_once_and_names_the_old_value(gw, cards) -> None:
    gate = gw({("GET", "/auth/me/appearance"): (200, {"mode": "dark", "density": None,
                                                      "accent": "#e11d48"}),
               ("PUT", "/auth/me/appearance"): (200, {})})
    out = await st.settings_set_appearance(mode="light")
    assert cards == []
    assert gate.writes() == [("PUT", "/auth/me/appearance",
                              {"mode": "light", "density": None, "accent": "#e11d48"})]
    assert "Mode: dark -> light" in out and "To undo, set mode dark" in out


async def test_a_run_that_must_show_a_card_shows_one(gw, cards, monkeypatch) -> None:
    gate = gw({("GET", "/auth/me/zone"): (200, {"timezone": "UTC"}),
               ("PUT", "/auth/me/zone"): (200, {})})

    async def must() -> bool:
        return True

    monkeypatch.setattr(st, "_must_ask", must)
    cards.answer = False
    out = await st.settings_set_time_zone("Asia/Kolkata")
    assert len(cards) == 1 and cards[0][2] == MEMBER
    assert gate.writes() == [] and out.startswith("Not done.")


async def test_my_time_zone_changes_at_once(gw, cards) -> None:
    gate = gw({("GET", "/auth/me/zone"): (200, {"timezone": "UTC"}),
               ("PUT", "/auth/me/zone"): (200, {"timezone": "Asia/Kolkata"})})
    out = await st.settings_set_time_zone("Asia/Kolkata")
    assert gate.writes() == [("PUT", "/auth/me/zone", {"timezone": "Asia/Kolkata"})]
    assert "set it to UTC again" in out and cards == []


async def test_the_same_zone_writes_nothing(gw) -> None:
    gate = gw({("GET", "/auth/me/zone"): (200, {"timezone": "UTC"})})
    assert "already" in await st.settings_set_time_zone("UTC")
    assert gate.writes() == []


async def test_a_star_keeps_the_layout_and_adds_the_app(gw, cards) -> None:
    shell = {"preset": "sales", "answered": "answered", "pins": ["/email"],
             "known": ["/email", "/crm"], "cardOrder": None, "newOrder": None,
             "tour_seen_at": "2026-11-01T00:00:00Z", "guide": {"x": 1}}
    gate = gw({("GET", "/auth/me/shell"): (200, shell), ("PUT", "/auth/me/shell"): (200, {})})
    out = await st.settings_star_app("/crm")
    (_v, _p, body), = gate.writes()
    assert body == {"preset": "sales", "answered": "answered", "pins": ["/email", "/crm"],
                    "known": ["/email", "/crm"], "cardOrder": None, "newOrder": None}
    assert "Starred /crm" in out and "unstar /crm" in out


async def test_a_star_with_no_saved_stars_writes_nothing(gw) -> None:
    gate = gw({("GET", "/auth/me/shell"): (200, {"pins": None, "known": None})})
    assert "every app" in await st.settings_star_app("/crm", starred=False)
    assert gate.writes() == []


async def test_my_number_system_follows_the_org_with_none(gw, cards) -> None:
    gate = gw({("GET", "/settings/number-system"): (200, {"org": "indian",
                                                         "member": "international"}),
               ("PUT", "/settings/number-system/me"): (200, {})})
    out = await st.settings_set_number_system("organization")
    assert gate.writes() == [("PUT", "/settings/number-system/me", {"value": None})]
    assert cards == [] and "international again" in out


async def test_no_stored_mode_and_no_named_mode_writes_nothing(gw, cards) -> None:
    """Review round 1, P1-a: a member with no server copy asks for compact. The
    tool never guesses a mode, so it writes nothing and asks for dark or light."""
    gate = gw({("GET", "/auth/me/appearance"): (200, {"mode": None, "density": None,
                                                      "accent": None}),
               ("GET", "/settings/appearance"): (200, {"org": {"mode": "light"}})})
    out = await st.settings_set_appearance(density="compact")
    assert gate.writes() == [] and cards == []
    assert "dark or light" in out


async def test_the_undo_names_the_organization_density_and_the_accent_reset(gw) -> None:
    gate = gw({("GET", "/auth/me/appearance"): (200, {"mode": "dark", "density": None,
                                                      "accent": None}),
               ("PUT", "/auth/me/appearance"): (200, {})})
    out = await st.settings_set_appearance(mode="light", density="compact",
                                           accent="#e11d48")
    assert len(gate.writes()) == 1
    assert "set mode dark, set density organization and reset the accent again" in out
    assert "not set" not in out.split("To undo")[1]


# ── Organization defaults and the logo: one card ───────────────────────────


async def test_the_org_number_system_asks_one_card(gw, cards) -> None:
    gate = gw({("GET", "/settings/number-system"): (200, {"org": None, "default": "indian"}),
               ("PUT", "/settings/number-system"): (200, {})})
    cards.answer = False
    assert (await st.settings_set_number_system("international", "organization")).startswith(
        "Not done.")
    assert gate.writes() == [] and len(cards) == 1
    cards.answer = True
    await st.settings_set_number_system("international", "organization")
    assert gate.writes() == [("PUT", "/settings/number-system", {"value": "international"})]


async def test_the_org_look_asks_one_card_and_a_403_names_the_admin(gw, cards) -> None:
    org = {"themeId": "rapidtool", "mode": "dark", "density": "default",
           "allowUserOverride": True}
    gate = gw({("GET", "/settings/appearance"): (200, {"org": org}),
               ("PUT", "/settings/appearance"): (403, {"detail": "Forbidden"})})
    out = await st.settings_set_org_appearance(mode="light")
    assert len(cards) == 1 and "Mode: dark -> light" in cards[0][1]
    assert gate.writes() == [("PUT", "/settings/appearance", {**org, "mode": "light"})]
    assert out == st.NOT_ADMIN


async def test_the_logo_style_sends_the_stored_images_back(gw, cards) -> None:
    logo = {"dataUri": "data:image/png;base64,LOGO"}
    mark = {"dataUri": "data:image/png;base64,MARK"}
    gate = gw({("GET", "/settings/branding"): (200, {"logo": logo, "logoMark": mark,
                                                    "darkStyle": "same"}),
               ("PUT", "/settings/branding"): (200, {})})
    await st.settings_set_logo_style("plate")
    assert len(cards) == 1
    assert gate.writes() == [("PUT", "/settings/branding", {
        "logoBase64": logo["dataUri"], "darkStyle": "plate", "logoDarkBase64": None,
        "logoMarkBase64": mark["dataUri"]})]


async def test_own_to_plate_names_the_deletion_and_a_decline_writes_nothing(gw, cards) -> None:
    """Review round 1, P1-b: the route keeps no dark-mode image for plate, so the
    card must say that the image is deleted."""
    branding = {"logo": {"dataUri": "data:image/png;base64,LOGO"},
                "logoDark": {"dataUri": "data:image/png;base64,DARK"},
                "darkStyle": "own", "updatedAt": "t1"}
    gate = gw({("GET", "/settings/branding"): (200, branding),
               ("PUT", "/settings/branding"): (200, {})})
    cards.answer = False
    assert (await st.settings_set_logo_style("plate")).startswith("Not done.")
    assert gate.writes() == []
    assert st.DARK_IMAGE_DELETED in cards[0][1]
    cards.answer = True
    out = await st.settings_set_logo_style("plate")
    assert st.DARK_IMAGE_DELETED in out
    (_v, _p, body), = gate.writes()
    assert body["logoDarkBase64"] is None and body["darkStyle"] == "plate"


async def test_same_to_plate_names_no_deletion(gw, cards) -> None:
    gw({("GET", "/settings/branding"): (200, {"logo": {"dataUri": "x"},
                                             "darkStyle": "same"}),
        ("PUT", "/settings/branding"): (200, {})})
    await st.settings_set_logo_style("plate")
    assert st.DARK_IMAGE_DELETED not in cards[0][1]


async def test_a_declined_org_look_card_writes_nothing(gw, cards) -> None:
    org = {"themeId": "rapidtool", "mode": "dark", "density": "default",
           "allowUserOverride": True}
    gate = gw({("GET", "/settings/appearance"): (200, {"org": org})})
    cards.answer = False
    assert (await st.settings_set_org_appearance(mode="light")).startswith("Not done.")
    assert gate.writes() == [] and len(cards) == 1


async def test_an_org_look_that_changed_while_the_card_waited_writes_nothing(
        gw, cards, monkeypatch) -> None:
    """P2: after the approve the tool reads again. A new updatedAt stops it."""
    org = {"themeId": "rapidtool", "mode": "dark", "density": "default",
           "allowUserOverride": True}
    reads = iter([{"org": org, "updatedAt": "t1"}, {"org": org, "updatedAt": "t2"}])
    gate = gw({("PUT", "/settings/appearance"): (200, {})})
    base = gate.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            gate.calls.append(("GET", request.url.path, None, ""))
            return httpx.Response(200, json=next(reads))
        return base(request)

    monkeypatch.setattr(st, "_transport", lambda: httpx.MockTransport(handler))
    assert await st.settings_set_org_appearance(mode="light") == st.CHANGED_MEANWHILE
    assert gate.writes() == []


async def test_a_logo_that_changed_while_the_card_waited_writes_nothing(
        gw, cards, monkeypatch) -> None:
    first = {"logo": {"dataUri": "x"}, "darkStyle": "same", "updatedAt": "t1"}
    reads = iter([first, {**first, "updatedAt": "t2"}])
    gate = gw({("PUT", "/settings/branding"): (200, {})})
    base = gate.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=next(reads))
        return base(request)

    monkeypatch.setattr(st, "_transport", lambda: httpx.MockTransport(handler))
    assert await st.settings_set_logo_style("plate") == st.CHANGED_MEANWHILE
    assert gate.writes() == []


async def test_a_style_that_needs_a_dark_image_with_none_writes_nothing(gw, cards) -> None:
    gate = gw({("GET", "/settings/branding"): (200, {"logo": {"dataUri": "x"},
                                                    "darkStyle": "same"})})
    assert "dark-mode image" in await st.settings_set_logo_style("white")
    assert gate.writes() == [] and cards == []


async def test_the_logo_removal_asks_one_card(gw, cards) -> None:
    gate = gw({("DELETE", "/settings/branding"): (200, {"logo": None})})
    cards.answer = False
    await st.settings_remove_logo()
    assert gate.writes() == []
    cards.answer = True
    assert (await st.settings_remove_logo()).startswith("Removed the logo")
    assert gate.writes() == [("DELETE", "/settings/branding", None)]
    assert len(cards) == 2


# ── The profile read ───────────────────────────────────────────────────────


async def test_the_profile_read_refuses_a_shared_room(monkeypatch) -> None:
    monkeypatch.setattr(pt, "_current_user_email", lambda: "room:thread-1")
    assert await pt.profile_read("as_text") == pt.SHARED_ROOM_READ


async def test_the_profile_read_sends_the_route_of_its_part(monkeypatch) -> None:
    monkeypatch.setattr(pt, "_current_user_email", lambda: MEMBER)
    sent: list[tuple[str, str]] = []
    facts = [{"id": f"f{i}", "kind": "fact", "text": f"fact {i}"} for i in range(20)]

    async def send(headers: dict[str, str], method: str, path: str = "",
                   body: Any = None) -> tuple[int, Any]:
        assert headers["X-User-Email"] == MEMBER and "X-Profile-Via" not in headers
        sent.append((method, path))
        return 200, {"text": "Ravi likes short answers.", "name": "Ravi", "facts": facts,
                     "aliases": [{"alias": "Raj", "email": "raj@acme.com"}],
                     "summaries": [], "more": False, "member": MEMBER}

    monkeypatch.setattr(pt, "_send", send)
    assert await pt.profile_read("as_text") == (
        f"{pt.PROFILE_READ_HEADER}\nRavi likes short answers.")
    assert MEMBER not in await pt.profile_read("export")
    await pt.profile_read("name")
    await pt.profile_read("chat_summaries")
    assert sent == [("GET", "/block"), ("GET", "/export"), ("GET", "/address"),
                    ("GET", "/summaries")]
    assert "Raj -> raj@acme.com" in await pt.profile_read("names")


async def test_the_profile_read_pages_the_facts(monkeypatch) -> None:
    """A list is never cut in silence: the page line names what is left."""
    monkeypatch.setattr(pt, "_current_user_email", lambda: MEMBER)
    facts = [{"id": f"f{i}", "kind": "fact", "text": f"fact {i}"} for i in range(20)]

    async def send(headers: dict[str, str], method: str, path: str = "",
                   body: Any = None) -> tuple[int, Any]:
        return 200, {"facts": facts}

    monkeypatch.setattr(pt, "_send", send)
    first = await pt.profile_read("facts")
    assert "f0 | fact | fact 0" in first and "f15 |" not in first
    assert "20" in first.splitlines()[-1]
    second = await pt.profile_read("facts", offset=15)
    assert "f19 | fact | fact 19" in second and "f0 |" not in second


# ── The pack is dark ───────────────────────────────────────────────────────


def test_the_pack_holds_the_owner_tools_and_is_dark() -> None:
    from tests.unit._native_maf_harness import load_agent_module

    module = load_agent_module("apps/agents/agent-settings-keeper")
    names = tuple(fn.__name__ for fn in tp.owner_tools("settings", module))
    assert names == SKILL_FAMILIES["settings"]["tools"]
    assert len(names) == 9 <= tp.MAX_PACK_TOOLS
    found = tp.PACKS["settings"]
    assert found.owner == "settings-keeper" and found.surface_slugs == ()
    assert "settings" not in tp.FOLLOWS and "settings" not in tp.FOLLOWS.values()
    assert "settings-keeper" in tp.PACK_ONLY_OWNERS
    assert tp.pack_only_refusal("settings-keeper") == tp.PACK_ONLY_REFUSED


def test_the_pack_is_off_where_the_list_names_other_packs(monkeypatch) -> None:
    from acb_common.settings import get_settings

    monkeypatch.setenv("TOOL_PACKS", "projects,studio,email")
    monkeypatch.setenv("TOOL_PACKS_ORGS", "*")
    get_settings.cache_clear()
    try:
        assert "settings" not in tp.packs_on("org-1")
        monkeypatch.setenv("TOOL_PACKS", "settings")
        get_settings.cache_clear()
        assert tp.packs_on("org-1") == {"settings"}
    finally:
        get_settings.cache_clear()
