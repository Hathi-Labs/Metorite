"""Task 4117 (PAR-4e-1): the four write tools of the ``notes`` pack, hermetic.

Spec: ``project-docs/specs/chat_parity.md`` section 6.4. Module:
``acb_skills.notes_write_tools``.

A fake gateway answers each route over ``httpx.MockTransport`` and records each
request. What is locked, each with its mutation (R7):

1. One card shows BEFORE the one write, and it holds the old and the new value.
2. A decline, a steer and a chat with no card write nothing.
3. The settings write sends each field that the member did not name as it was read.
4. An all-default settings read gives no card and no write.
5. A value that moved while the card waited stops the write.
6. A bad value, a bad id, a shared room and no member send no write.
7. A held chat holds off each write. The 4 rows are COVERED.

Run: ``uv run pytest tests/unit/test_notes_write_tools.py -q``.
"""
from __future__ import annotations

import copy
import inspect
import json
from typing import Any

import httpx
import pytest
from acb_common import get_settings
from acb_skills import ask_tools, egress
from acb_skills import chat_parity as cp
from acb_skills import notes_read_tools as nt
from acb_skills import notes_write_tools as nw

MEMBER = "mel@example.test"
TID = "3f2c9a5e-1b7d-4c2e-9a4f-6d8e0b1c2a3f"
_REAL_CLIENT = httpx.AsyncClient

#: A saved row: each field differs from its default.
SAVED: dict[str, Any] = {
    "copilot_instructions": "Be brief.\nAsk about budget.",
    "copilot_default_on": True,
    "copilot_sensitivity": "high",
    "live_transcription": "never",
    "template_instructions": {"standup": "Name each blocker.", "retro": "Stay kind."},
    "default_template": "standup",
    "bot_name": "Mel's notetaker",
    "auto_dispatch_tasks": False,
    "auto_dispatch_emails": False,
    "auto_dispatch_docs": False,
}
TEMPLATES = [{"key": k, "label": k.title()} for k in ("standup", "retro", "interview")]


class _Gateway:
    def __init__(self) -> None:
        self.settings = copy.deepcopy(SAVED)
        self.instructions = "Old text."
        self.glossary = [{"id": TID, "term": "Metorite"}]
        self.calls: list[tuple[str, str, Any]] = []
        self.forced: dict[tuple[str, str], tuple[int, Any]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((method, path, body))
        assert request.headers["x-user-email"] == MEMBER
        if (method, path) in self.forced:
            status, data = self.forced[(method, path)]
            return httpx.Response(status, json=data)
        if (method, path) == ("GET", "/notes/settings"):
            return httpx.Response(200, json={"settings": copy.deepcopy(self.settings),
                                             "templates": TEMPLATES})
        if (method, path) == ("PUT", "/notes/settings"):
            return httpx.Response(200, json={"settings": body})
        if (method, path) == ("GET", "/notes/copilot/instructions"):
            return httpx.Response(200, json={"instructions": self.instructions})
        if (method, path) == ("PUT", "/notes/copilot/instructions"):
            return httpx.Response(200, json=body)
        if (method, path) == ("GET", "/notes/glossary"):
            return httpx.Response(200, json=copy.deepcopy(self.glossary))
        if (method, path) == ("POST", "/notes/glossary"):
            return httpx.Response(201, json={"id": TID, "term": body["term"]})
        if method == "DELETE" and path.startswith("/notes/glossary/"):
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Not Found"})

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def gw(monkeypatch) -> _Gateway:
    g = _Gateway()
    transport = httpx.MockTransport(g.handle)
    monkeypatch.setattr(nw.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=transport, **kw))
    monkeypatch.setattr(nt, "_current_user_email", lambda: MEMBER)
    return g


class _Card:
    """The card of the run. It records each card and the writes sent before it."""

    def __init__(self, g: _Gateway) -> None:
        self.g = g
        self.answer: Any = True
        self.open = True
        self.during: Any = None
        self.shown: list[dict[str, Any]] = []

    async def ask(self, title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        self.shown.append({"title": title, "detail": detail, "context": context, "kw": kw,
                           "writes_before": len(self.g.writes())})
        if self.during is not None:
            self.during()
        return self.answer


@pytest.fixture
def card(gw, monkeypatch) -> _Card:
    c = _Card(gw)
    monkeypatch.setattr(ask_tools, "request_confirmation", c.ask)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: c.open)
    return c


CALLS = {
    "notes_update_settings": lambda: nw.notes_update_settings(copilot_sensitivity="low"),
    "notes_set_copilot_instructions": lambda: nw.notes_set_copilot_instructions("New text."),
    "notes_add_glossary_term": lambda: nw.notes_add_glossary_term("Hathi"),
    "notes_delete_glossary_term": lambda: nw.notes_delete_glossary_term(TID),
}
WRITES = {
    "notes_update_settings": ("PUT", "/notes/settings", {**SAVED, "copilot_sensitivity": "low"}),
    "notes_set_copilot_instructions": (
        "PUT", "/notes/copilot/instructions", {"instructions": "New text."}),
    "notes_add_glossary_term": ("POST", "/notes/glossary", {"term": "Hathi"}),
    "notes_delete_glossary_term": ("DELETE", f"/notes/glossary/{TID}", None),
}


# ── 1. One card, then one write ─────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_one_card_shows_before_the_one_write(gw, card, name) -> None:
    """Mutation: write before the card, or write twice -> red."""
    out = await CALLS[name]()
    assert out["ok"] is True and out["decision"] == "approved", out
    assert len(card.shown) == 1 and card.shown[0]["writes_before"] == 0
    assert gw.writes() == [WRITES[name]]
    kw = card.shown[0]["kw"]
    assert kw["non_interactive_default"] == "deny" and kw["responder"] == MEMBER
    assert kw["fenced"] is True and kw["close_on_steer"] is True


# ── 2. No approve, no write ─────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(CALLS))
@pytest.mark.parametrize(("answer", "channel", "decision"), [
    (False, True, "stopped"), (None, True, "stopped"),
    (ask_tools.REVISED, True, "revised"), (False, False, "no_card")])
async def test_no_approve_writes_nothing(gw, card, name, answer, channel, decision) -> None:
    """Mutation: send on any answer that is not ``True`` -> red."""
    card.answer, card.open = answer, channel
    out = await CALLS[name]()
    assert out["ok"] is False and out["decision"] == decision, out
    assert gw.writes() == []


# ── 3. The settings write keeps every other field ──────────────────────────


@pytest.mark.parametrize(("kw", "changed"), [
    ({"copilot_default_on": False}, {"copilot_default_on": False}),
    ({"bot_name": ""}, {"bot_name": None}),
    ({"default_template": "retro"}, {"default_template": "retro"}),
    ({"auto_dispatch_docs": True}, {"auto_dispatch_docs": True}),
    ({"template_instructions": {"interview": "Probe depth."}},
     {"template_instructions": {**SAVED["template_instructions"],
                                "interview": "Probe depth."}}),
    ({"template_instructions": {"standup": ""}},
     {"template_instructions": {"retro": "Stay kind."}}),
])
async def test_a_field_that_the_member_did_not_name_is_sent_as_read(gw, card, kw, changed) -> None:
    """Mutation: send only the named fields, or a default for the others, or
    replace the template map -> the route would wipe them -> red."""
    out = await nw.notes_update_settings(**kw)
    assert out["ok"] is True and out["saved"] == sorted(changed), out
    assert gw.writes() == [("PUT", "/notes/settings", {**SAVED, **changed})]


async def test_the_body_takes_the_second_read_for_the_other_fields(gw, card) -> None:
    """A field that the member did not name changed while the card waited. The
    write keeps the newer value. Mutation: build the body from the first read -> red."""
    card.during = lambda: gw.settings.update(bot_name="Newer")
    out = await nw.notes_update_settings(copilot_sensitivity="low")
    assert out["ok"] is True
    assert gw.writes()[0][2] == {**SAVED, "bot_name": "Newer", "copilot_sensitivity": "low"}


def test_the_defaults_table_is_the_model_of_the_gateway() -> None:
    """Mutation: add a setting to the route and not to the tool -> red."""
    pytest.importorskip("gateway.routes.notes.settings", reason="gateway not installed")
    from gateway.routes.notes.settings import NotesSettings

    assert NotesSettings().model_dump() == nw.SETTINGS_DEFAULTS
    params = set(inspect.signature(nw.notes_update_settings).parameters)
    assert params == set(nw.SETTINGS_DEFAULTS) - {"copilot_instructions"}
    assert nw.MAX_INSTRUCTIONS == 2000


# ── 4. An all-default read is not proof ────────────────────────────────────


async def test_an_all_default_read_gives_no_card_and_no_write(gw, card) -> None:
    """The route answers with the defaults on a failed or a blind read. Mutation:
    drop the check -> the PUT erases the saved row -> red."""
    gw.settings = copy.deepcopy(nw.SETTINGS_DEFAULTS)
    out = await nw.notes_update_settings(copilot_sensitivity="low")
    assert out == {"ok": False, "error": nw._UNPROVEN}
    assert card.shown == [] and gw.writes() == []


async def test_a_default_second_read_stops_the_write(gw, card) -> None:
    card.during = lambda: setattr(gw, "settings", copy.deepcopy(nw.SETTINGS_DEFAULTS))
    out = await nw.notes_update_settings(copilot_sensitivity="low")
    assert out["error"] == nw._UNPROVEN and gw.writes() == []


async def test_a_read_that_misses_a_field_writes_nothing(gw, card) -> None:
    """Mutation: drop the shape check -> the PUT resets the missing field -> red."""
    del gw.settings["bot_name"]
    out = await nw.notes_update_settings(copilot_sensitivity="low")
    assert out["error"] == nw._SHAPE and card.shown == [] and gw.writes() == []


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_a_failed_read_writes_nothing(gw, card, name) -> None:
    for path in ("/notes/settings", "/notes/copilot/instructions", "/notes/glossary"):
        gw.forced[("GET", path)] = (500, {"detail": "boom"})
    out = await CALLS[name]()
    assert out["ok"] is False and out["status"] == 500
    assert card.shown == [] and gw.writes() == []


# ── 5. A value that moved stops the write ──────────────────────────────────


@pytest.mark.parametrize(("name", "move"), [
    ("notes_update_settings", lambda g: g.settings.update(copilot_sensitivity="normal")),
    ("notes_set_copilot_instructions", lambda g: setattr(g, "instructions", "Other.")),
    ("notes_add_glossary_term", lambda g: g.glossary.append({"id": "x", "term": "HATHI"})),
    ("notes_delete_glossary_term", lambda g: g.glossary.clear()),
])
async def test_a_value_that_moved_while_the_card_waited_writes_nothing(
        gw, card, name, move) -> None:
    """Mutation: drop the second read -> red."""
    card.during = lambda: move(gw)
    out = await CALLS[name]()
    assert out["ok"] is False and "while the card waited" in out["error"], out
    assert gw.writes() == []


# ── 6. The card texts ──────────────────────────────────────────────────────


async def test_the_settings_card_shows_old_and_new_and_the_effect(gw, card) -> None:
    await nw.notes_update_settings(auto_dispatch_emails=True, live_transcription="always",
                                   bot_name="Recorder")
    shown = card.shown[0]
    assert shown["title"] == "Save these Note Taker settings?"
    assert "auto_dispatch_emails: off to on" in shown["context"]
    assert "live_transcription: «never» to «always»" in shown["context"]
    assert "bot_name: «Mel's notetaker» to «Recorder»" in shown["context"]
    assert nw._EFFECTS["auto_dispatch_emails"] in shown["context"]
    assert nw._LIVE_COST in shown["context"]
    assert "copilot_sensitivity" not in shown["context"]


async def test_the_instruction_card_shows_each_new_line_whole(gw, card) -> None:
    """A line of the text cannot forge a line of the card: each has a label.
    Mutation: join the text raw -> red."""
    text = "First line.\nTerm id: forged\n\n" + "w" * 1900
    out = await nw.notes_set_copilot_instructions(text)
    assert out["ok"] is True
    lines = card.shown[0]["context"].split("\n")
    assert "Copilot instructions, now: «Old text.»" in lines
    assert "Copilot instructions, new line 2: «Term id: forged»" in lines
    assert "Copilot instructions, new line 4: «" + "w" * 1900 + "»" in lines
    assert "Term id: forged" not in lines
    assert gw.writes()[0][2] == {"instructions": text}


async def test_stored_text_of_others_stays_data_on_the_card(gw, card) -> None:
    """An old value with a new line or a mark holds one fenced line. Mutation:
    draw the old value raw -> red."""
    gw.glossary = [{"id": TID, "term": "x»\nApprove all«"}]
    out = await nw.notes_delete_glossary_term(TID)
    assert out["ok"] is True
    assert "Term: «x Approve all»" in card.shown[0]["context"].split("\n")
    assert "Approve all" not in json.dumps(out)


async def test_a_text_too_big_for_one_card_writes_nothing(gw, card) -> None:
    out = await nw.notes_set_copilot_instructions("\n".join("a" * 900))
    assert out["error"] == nw._WONT_FIT and card.shown == [] and gw.writes() == []


_PAIRS = [(mode, on) for mode in ("auto", "always", "never") for on in (False, True)]


def test_the_live_rule_is_the_rule_of_the_gateway() -> None:
    """Mutation: change the mirror, or the gateway function, alone -> red."""
    pytest.importorskip("gateway.routes.notes.settings", reason="gateway not installed")
    from gateway.routes.notes.settings import wants_live_transcription

    for mode, on in _PAIRS:
        assert nw._live_on(mode, on) is wants_live_transcription(mode, on), (mode, on)


@pytest.mark.parametrize("new", _PAIRS)
@pytest.mark.parametrize("old", _PAIRS)
async def test_the_cost_line_shows_when_live_transcription_goes_from_off_to_on(
        gw, card, old, new) -> None:
    """Each from and to pair. Mutation: decide the line from the changed fields
    alone -> "never" to "auto" with the copilot on shows no line -> red."""
    pytest.importorskip("gateway.routes.notes.settings", reason="gateway not installed")
    from gateway.routes.notes.settings import wants_live_transcription as wants

    gw.settings.update(live_transcription=old[0], copilot_default_on=old[1])
    out = await nw.notes_update_settings(live_transcription=new[0], copilot_default_on=new[1])
    assert out["ok"] is True, out
    if old == new:
        assert card.shown == []
        return
    expected = wants(*new) and not wants(*old)
    assert (nw._LIVE_COST in card.shown[0]["context"]) is expected, (old, new)


async def test_a_move_that_changes_the_cost_line_writes_nothing(gw, card) -> None:
    """The member turns the copilot on while live transcription is "never". It
    moves to "auto" while the card waits, so the write would start a cost that
    the card did not name. Mutation: drop that check of the second read -> red."""
    gw.settings.update(live_transcription="never", copilot_default_on=False)
    card.during = lambda: gw.settings.update(live_transcription="auto")
    out = await nw.notes_update_settings(copilot_default_on=True)
    assert "while the card waited" in out["error"] and gw.writes() == []


async def test_the_card_names_the_text_of_a_template_that_no_longer_ships(gw, card) -> None:
    """The route drops that text on any save. Mutation: no row for it -> red."""
    gw.settings["template_instructions"] = {"standup": "Keep.", "old_kind": "Lost text."}
    out = await nw.notes_update_settings(copilot_sensitivity="low")
    assert out["ok"] is True
    context = card.shown[0]["context"]
    assert ("This save removes your text for the template «old_kind», which no longer "
            "ships: «Lost text.»") in context.split("\n")
    assert "«Keep.»" not in context


async def test_a_new_dropped_text_while_the_card_waited_writes_nothing(gw, card) -> None:
    card.during = lambda: gw.settings["template_instructions"].update(old_kind="Late.")
    out = await nw.notes_update_settings(copilot_sensitivity="low")
    assert "while the card waited" in out["error"] and gw.writes() == []


def test_the_settings_write_says_that_it_can_erase() -> None:
    from acb_skills.tool_annotations import get_annotations

    assert get_annotations("notes_update_settings")["destructive"] is True


# ── 7. Refusals before a write ─────────────────────────────────────────────


@pytest.mark.parametrize("call", [
    lambda: nw.notes_update_settings(),
    lambda: nw.notes_update_settings(copilot_sensitivity="loud"),
    lambda: nw.notes_update_settings(copilot_default_on="yes"),
    lambda: nw.notes_update_settings(default_template="nope"),
    lambda: nw.notes_update_settings(template_instructions={"nope": "x"}),
    lambda: nw.notes_update_settings(template_instructions={"retro": "a" * 2001}),
    lambda: nw.notes_update_settings(bot_name="a\nb"),
    lambda: nw.notes_set_copilot_instructions("a" * 2001),
    lambda: nw.notes_set_copilot_instructions("a" + chr(0x200B) + "b"),
    lambda: nw.notes_set_copilot_instructions("a" + chr(0x2028) + "b"),
    lambda: nw.notes_set_copilot_instructions("a » b"),
    lambda: nw.notes_add_glossary_term("  "),
    lambda: nw.notes_add_glossary_term("a" * 121),
    lambda: nw.notes_add_glossary_term("a\nb"),
    lambda: nw.notes_delete_glossary_term("../settings"),
    lambda: nw.notes_delete_glossary_term("00000000-0000-4000-8000-000000000000"),
])
async def test_a_bad_value_shows_no_card_and_writes_nothing(gw, card, call) -> None:
    out = await call()
    assert out["ok"] is False, out
    assert card.shown == [] and gw.writes() == []


@pytest.mark.parametrize(("who", "words"), [("room:7f1c", nw._SHARED_ROOM), ("", nw._NO_MEMBER)])
@pytest.mark.parametrize("name", sorted(CALLS))
async def test_a_room_or_no_member_sends_no_request(
        gw, card, monkeypatch, name, who, words) -> None:
    monkeypatch.setattr(nt, "_current_user_email", lambda: who)
    assert await CALLS[name]() == {"ok": False, "error": words}
    assert gw.calls == [] and card.shown == []


@pytest.mark.parametrize(("call", "paths"), [
    (lambda: nw.notes_update_settings(copilot_sensitivity="high"), 1),
    (lambda: nw.notes_set_copilot_instructions("Old text."), 1),
    (lambda: nw.notes_add_glossary_term("Metorite"), 1),
])
async def test_no_change_shows_no_card(gw, card, call, paths) -> None:
    out = await call()
    assert out["ok"] is True and out["saved"] == []
    assert card.shown == [] and gw.writes() == [] and len(gw.calls) == paths


async def test_a_new_case_of_a_term_says_that_it_replaces(gw, card) -> None:
    await nw.notes_add_glossary_term("METORITE")
    assert card.shown[0]["title"] == "Change the spelling of this glossary term?"
    assert "It replaces the spelling «Metorite»" in card.shown[0]["context"]


async def test_a_route_refusal_says_that_nothing_was_saved(gw, card) -> None:
    gw.forced[("POST", "/notes/glossary")] = (400, {"detail": "term too long"})
    out = await nw.notes_add_glossary_term("Hathi")
    assert out == {"ok": False, "error": "Nothing was saved. term too long", "status": 400}


def test_no_tool_takes_an_organization_a_caller_or_an_address() -> None:
    """R5. Mutation: add ``email`` to a tool -> red."""
    identity = {"organization_id", "org_id", "tenant_id", "email", "user", "user_email",
                "owner_email", "member"}
    for fn in nw.NOTES_WRITE_TOOLS:
        assert not set(inspect.signature(fn).parameters) & identity, fn.__name__


def test_a_held_chat_holds_off_each_write(monkeypatch) -> None:
    """``org-admin`` is in ``egress.HELD_WRITE_OWNERS``, and no list changed.
    Mutation: mark a write ``read_only=True`` -> it stays -> red."""
    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
    for fn in nw.NOTES_WRITE_TOOLS:
        for item in (fn, fn.__name__):
            assert egress.is_held_off_write(item) is True, fn.__name__
        assert fn.__name__ not in egress.HELD_CARD_WRITE_TOOLS


def test_the_4_write_rows_are_covered() -> None:
    rows = {
        "PUT /api/notes/settings": "notes_update_settings",
        "PUT /api/notes/copilot/instructions": "notes_set_copilot_instructions",
        "POST /api/notes/glossary": "notes_add_glossary_term",
        "DELETE /api/notes/glossary/{}": "notes_delete_glossary_term",
    }
    for key, tool in rows.items():
        assert cp.COVERED[key] == tool and key not in cp.DEBT, key
    assert {fn.__name__ for fn in nw.NOTES_WRITE_TOOLS} == set(rows.values())
