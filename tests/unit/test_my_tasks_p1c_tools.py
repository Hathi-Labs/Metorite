"""P-1c (task 3923): Sync now, the task switch and Disconnect from the chat.

Spec: ``project-docs/specs/chat_parity.md`` §6.4, the block P-1c.

* ``my_tasks_calendar_connection(provider, action)`` sends Sync now
  (``POST /tasks/calendar/connections/{id}/sync``) or the switch
  (``PATCH /tasks/calendar/connections/{id}`` with ``push_tasks``).
* ``my_tasks_disconnect_calendar(provider)`` sends
  ``DELETE /tasks/calendar/connections/{id}``.

Each tool reads the list of P-1a first, takes the own row of the provider that
is not disconnected, and shows ONE card. A deny, a closed card or a run with no
person sends no write. An approve sends exactly one write. No tool takes an id,
an org or a member: the gateway takes them from the session (R5).

Fakes only: no real calendar, no real connection, no network, no real token.
``core._request`` is a recorder, and the card is a stub of
``acb_skills.ask_tools.request_confirmation``. Each case names the mutation that
turns it red (R7).

Run: ``uv run pytest tests/unit/test_my_tasks_p1c_tools.py -q``.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_my_tasks
from acb_skills import ask_tools
from acb_skills import chat_parity as cp
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from skill_my_tasks import core

from tests.unit.test_calendar_day_pack import SLUG
from tests.unit.test_pack_requests import _chosen

ACT, OFF = "my_tasks_calendar_connection", "my_tasks_disconnect_calendar"
TOOLS = (ACT, OFF)
ME = "member@p1c.test"
CID = "c0ffee00-0000-4000-8000-0000000000c1"
OLD = "c0ffee00-0000-4000-8000-0000000000d0"
LIST = "/tasks/calendar/connections"
ONE = f"{LIST}/{CID}"

#: Secret values that a careless route could send. None may show.
SECRETS = ("ya29.ACCESS-SECRET", "1//REFRESH-SECRET", "CLIENT-SECRET-XYZ",
           "id-token-SECRET", "META-SECRET")


def _row(**over: Any) -> dict[str, Any]:
    row = {
        "id": CID, "provider": "google", "account_email": ME, "status": "connected",
        "last_sync_at": "2026-11-22T08:00:00+00:00", "last_error": None,
        "metorite_calendar": True, "push_tasks": False, "entry_count": 12,
        # Fields the route never sends today. No output may show them.
        "access_token": SECRETS[0], "refresh_token": SECRETS[1],
        "client_secret": SECRETS[2], "id_token": SECRETS[3],
        "provider_meta": {"token": SECRETS[4]},
    }
    row.update(over)
    return row


class Gateway:
    """A fake ``core._request``. It records each call, and answers the list
    with *rows* and each write with *write* (or raises *write_error*)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *,
                 list_error: int | None = None, write: Any = None,
                 write_error: int | None = None) -> None:
        self.rows = [_row()] if rows is None else rows
        self.list_error = list_error
        self.write = write
        self.write_error = write_error
        self.calls: list[tuple[str, str, Any]] = []

    async def __call__(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path, kw.get("json")))
        if method == "GET":
            if self.list_error is not None:
                raise core._GatewayError(f"Tasks GET {path} failed ({self.list_error})",
                                         self.list_error)
            return {"enabled": True, "connections": self.rows, "mailbox_offers": []}
        if self.write_error is not None:
            raise core._GatewayError(f"Tasks {method} {path} failed ({self.write_error})",
                                     self.write_error)
        return self.write

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]


class Card:
    """A stub of ``request_confirmation``. It records each card and answers
    *answer*."""

    def __init__(self, answer: Any = True) -> None:
        self.answer = answer
        self.cards: list[dict[str, Any]] = []

    async def __call__(self, **kw: Any) -> Any:
        self.cards.append(kw)
        return self.answer


@pytest.fixture(autouse=True)
def _member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_current_user_email", lambda: ME)


def _install(monkeypatch: pytest.MonkeyPatch, gw: Gateway, card: Card | None = None
             ) -> tuple[Gateway, Card]:
    card = card or Card()
    monkeypatch.setattr(core, "_request", gw)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    return gw, card


def _text_of(card: dict[str, Any]) -> str:
    return " ".join(str(card.get(k) or "") for k in ("title", "detail", "context"))


# ── 1. An approve sends exactly one write, with the right verb and path ──────


async def test_an_approved_sync_sends_one_post(monkeypatch) -> None:
    """Mutation: send another verb or path, a body, or a second write -> red."""
    gw, card = _install(monkeypatch, Gateway(
        write={"status": "ok", "added": 3, "updated": 1, "removed": 0}))
    text = await core.my_tasks_calendar_connection("google", "sync")
    assert gw.calls == [("GET", LIST, None), ("POST", f"{ONE}/sync", None)]
    assert len(card.cards) == 1
    assert text == f"Synced Google Calendar «{ME}»: 3 added, 1 updated, 0 removed events."


@pytest.mark.parametrize(("action", "was", "want"), [
    ("show_tasks", False, True), ("hide_tasks", True, False)])
async def test_an_approved_switch_sends_one_patch(monkeypatch, action, was, want) -> None:
    """Mutation: send the other value, another verb, or no body -> red."""
    gw, card = _install(monkeypatch, Gateway(
        [_row(push_tasks=was)], write={**_row(push_tasks=want)}))
    text = await core.my_tasks_calendar_connection("google", action)
    assert gw.calls == [("GET", LIST, None), ("PATCH", ONE, {"push_tasks": want})]
    assert len(card.cards) == 1
    assert text.startswith(f"The switch is {'on' if want else 'off'}.")


async def test_an_approved_disconnect_sends_one_delete(monkeypatch) -> None:
    """Mutation: send another verb or path, or a second write -> red."""
    gw, card = _install(monkeypatch, Gateway(
        write={"disconnected": True, "removed_entries": 12}))
    await core.my_tasks_disconnect_calendar("google")
    assert gw.calls == [("GET", LIST, None), ("DELETE", ONE, None)]
    assert len(card.cards) == 1


# ── 2. A deny, a closed card or a run with no person sends no write ──────────


@pytest.mark.parametrize("answer", [False, None, ask_tools.REVISED])
@pytest.mark.parametrize(("tool", "args"), [
    (ACT, ("google", "sync")), (ACT, ("google", "show_tasks")), (OFF, ("google",))])
async def test_no_approve_sends_no_write(monkeypatch, answer, tool, args) -> None:
    """A deny (False), a timeout (None) and a card that a chat message closed
    (REVISED). Mutation: write before the card, or on a falsy answer -> red."""
    gw, card = _install(monkeypatch, Gateway(write={"status": "ok"}), Card(answer))
    text = await getattr(core, tool)(*args)
    assert gw.writes == []
    assert len(card.cards) == 1
    assert "Synced" not in text and "Disconnected" not in text
    assert "The switch is" not in text


async def test_a_closed_card_says_so(monkeypatch) -> None:
    """Mutation: answer a closed card as a decline -> red."""
    _install(monkeypatch, Gateway(), Card(ask_tools.REVISED))
    text = await core.my_tasks_disconnect_calendar("google")
    assert text == ask_tools.card_outcome_text(ask_tools.CLOSED_BY_YOUR_MESSAGE)


@pytest.mark.parametrize(("tool", "args"), [
    (ACT, ("google", "sync")), (ACT, ("google", "hide_tasks")), (OFF, ("google",))])
async def test_a_run_with_no_person_sends_no_write(monkeypatch, tool, args) -> None:
    """The REAL ``request_confirmation`` under the AUTO-7a mark. Mutation: pass
    ``non_interactive_default="approve"`` and skip the mark -> red."""
    from acb_common.no_person import no_person_scope

    gw = Gateway([_row(push_tasks=True)], write={"status": "ok"})
    monkeypatch.setattr(core, "_request", gw)
    with no_person_scope():
        await getattr(core, tool)(*args)
    assert gw.writes == []


async def test_inside_refuse_cards_no_write(monkeypatch) -> None:
    """A run with no card channel (WS-47 WAC-3). Mutation: approve with no card -> red."""
    gw = Gateway(write={"disconnected": True, "removed_entries": 1})
    monkeypatch.setattr(core, "_request", gw)
    with ask_tools.refuse_cards():
        await core.my_tasks_disconnect_calendar("google")
    assert gw.writes == []


async def test_the_card_binds_the_run_member(monkeypatch) -> None:
    """PZ-32: only the run's own member answers. Mutation: drop ``responder``,
    ``fenced`` or ``close_on_steer`` -> red."""
    _, card = _install(monkeypatch, Gateway(write={"status": "ok"}))
    await core.my_tasks_calendar_connection("google", "sync")
    kw = card.cards[0]
    assert kw["responder"] == ME
    assert kw["fenced"] is True and kw["close_on_steer"] is True
    assert "non_interactive_default" not in kw  # the deny default stays


# ── 3. The counts of Disconnect ──────────────────────────────────────────────


async def test_the_disconnect_card_has_the_count_and_the_reply_the_route_count(
        monkeypatch) -> None:
    """The card shows the ``entry_count`` of the list read. The reply shows the
    ``removed_entries`` of the route, which a sync between the two can change.
    Mutation: put the list count in the reply -> red."""
    _, card = _install(monkeypatch, Gateway(
        [_row(entry_count=12)], write={"disconnected": True, "removed_entries": 15}))
    text = await core.my_tasks_disconnect_calendar("google")
    shown = _text_of(card.cards[0])
    assert card.cards[0]["title"] == "Disconnect Google Calendar?"
    assert "«12» synced events leave Metorite" in shown and f"«{ME}»" in shown
    assert "Your tasks stay" in shown and "stays until you delete it" in shown
    assert "15 synced events left Metorite" in text and "12" not in text


async def test_a_disconnect_that_did_not_happen_is_not_a_success(monkeypatch) -> None:
    """Mutation: report success on any 200 -> red."""
    _install(monkeypatch, Gateway(write={"disconnected": False}))
    text = await core.my_tasks_disconnect_calendar("google")
    assert "did not disconnect" in text and "Disconnected" not in text


# ── 4. The flag off, no connection, and a bad argument ───────────────────────


@pytest.mark.parametrize(("tool", "args"), [(ACT, ("google", "sync")), (OFF, ("outlook",))])
async def test_a_404_on_the_list_gives_the_off_line_and_no_card(monkeypatch, tool, args) -> None:
    """``CALENDAR_SYNC_READ`` is off. Mutation: let the 404 raise, or show a
    card -> red."""
    gw, card = _install(monkeypatch, Gateway(list_error=404))
    assert await getattr(core, tool)(*args) == core.CONNECTIONS_OFF
    assert gw.writes == [] and card.cards == []


@pytest.mark.parametrize(("tool", "args"), [(ACT, ("outlook", "sync")), (OFF, ("outlook",))])
async def test_no_connection_of_the_provider_gives_one_line(monkeypatch, tool, args) -> None:
    """Only a Google row exists. Mutation: act on the Google row -> red."""
    gw, card = _install(monkeypatch, Gateway())
    text = await getattr(core, tool)(*args)
    assert text.startswith("No Outlook calendar is connected.") and "\n" not in text
    assert gw.writes == [] and card.cards == []


async def test_a_disconnected_row_is_never_chosen(monkeypatch) -> None:
    """The old disconnected row comes first. Mutation: take the first row of
    the provider -> the write goes to the old id -> red."""
    rows = [_row(id=OLD, status="disconnected"), _row()]
    gw, _ = _install(monkeypatch, Gateway(rows, write={"disconnected": True,
                                                       "removed_entries": 0}))
    await core.my_tasks_disconnect_calendar("google")
    assert gw.writes == [("DELETE", ONE, None)]
    gw2, card = _install(monkeypatch, Gateway([_row(id=OLD, status="disconnected")]))
    await core.my_tasks_disconnect_calendar("google")
    assert gw2.writes == [] and card.cards == []


async def test_an_id_that_is_no_uuid_is_never_sent(monkeypatch) -> None:
    """The id goes in the path. Mutation: send it unchecked -> red."""
    gw, card = _install(monkeypatch, Gateway([_row(id="../../projects/x")]))
    await core.my_tasks_calendar_connection("google", "sync")
    assert gw.writes == [] and card.cards == []


@pytest.mark.parametrize(("tool", "args", "line"), [
    (ACT, ("yahoo", "sync"), core._BAD_PROVIDER),
    (ACT, ("google", "connect"), core._BAD_ACTION),
    (ACT, ("google", "disconnect"), core._BAD_ACTION),
    (OFF, ("icloud",), core._BAD_PROVIDER),
])
async def test_an_unknown_provider_or_act_sends_no_request(monkeypatch, tool, args, line) -> None:
    """Mutation: read the list first, or pass the word on -> red."""
    gw, card = _install(monkeypatch, Gateway())
    assert await getattr(core, tool)(*args) == line
    assert gw.calls == [] and card.cards == []


async def test_a_switch_already_set_shows_no_card(monkeypatch) -> None:
    """Mutation: show a card or write when nothing changes -> red."""
    gw, card = _install(monkeypatch, Gateway([_row(push_tasks=True)]))
    text = await core.my_tasks_calendar_connection("google", "show_tasks")
    assert "already on" in text and "Nothing changed" in text
    assert gw.writes == [] and card.cards == []


# ── 5. The answers of the routes, told plainly ───────────────────────────────


@pytest.mark.parametrize(("status", "words"), [
    ("busy", "runs now"), ("needs_reconnect", "connect it again"),
    ("error", "did not finish"), ("odd", "did not finish")])
async def test_a_sync_that_did_not_run_says_so(monkeypatch, status, words) -> None:
    """Mutation: answer "Synced" for each status -> red."""
    _install(monkeypatch, Gateway(write={"status": status, "added": 0}))
    text = await core.my_tasks_calendar_connection("google", "sync")
    assert words in text and "Synced" not in text


async def test_a_failed_sync_makes_no_false_promise(monkeypatch) -> None:
    """No text says that a retry runs by itself. Mutation: say "in a minute" -> red."""
    _install(monkeypatch, Gateway(write={"status": "error"}))
    text = await core.my_tasks_calendar_connection("google", "sync")
    assert "minute" not in text and "again" in text


async def test_a_sync_of_a_row_that_needs_reconnect_shows_no_card(monkeypatch) -> None:
    """The engine answers at once, so the card would ask for an act that cannot
    run. Mutation: show the card or send the POST -> red."""
    gw, card = _install(monkeypatch, Gateway([_row(status="needs_reconnect")]))
    text = await core.my_tasks_calendar_connection("google", "sync")
    assert card.cards == [] and gw.writes == []
    assert text == core._SYNC_LINES["needs_reconnect"] and "you" in text.lower()


async def test_a_sync_of_a_connected_row_still_shows_the_card(monkeypatch) -> None:
    gw, card = _install(monkeypatch, Gateway([_row(status="connected")], write={"status": "ok"}))
    await core.my_tasks_calendar_connection("google", "sync")
    assert len(card.cards) == 1 and len(gw.writes) == 1


async def test_a_write_404_is_one_line(monkeypatch) -> None:
    """The row went, or the flag went off, between the read and the write.
    Mutation: report success -> red."""
    _install(monkeypatch, Gateway(write_error=404))
    text = await core.my_tasks_disconnect_calendar("google")
    assert text == core._WRITE_GONE and "Nothing changed" in text


async def test_a_sync_503_says_nothing_changed(monkeypatch) -> None:
    """The provider is not set up on the server. Mutation: raise or report
    success -> red."""
    _install(monkeypatch, Gateway(write_error=503))
    text = await core.my_tasks_calendar_connection("google", "sync")
    assert "nothing changed" in text and "Synced" not in text


@pytest.mark.parametrize(("tool", "args"), [(ACT, ("google", "sync")), (OFF, ("google",))])
async def test_another_error_of_the_route_still_raises(monkeypatch, tool, args) -> None:
    """A 500 is a fault, never a success. Mutation: catch every error -> red."""
    _install(monkeypatch, Gateway(write_error=500))
    with pytest.raises(core._GatewayError):
        await getattr(core, tool)(*args)


async def test_a_switch_that_did_not_change_says_so(monkeypatch) -> None:
    """Mutation: trust the request in place of the answer -> red."""
    _install(monkeypatch, Gateway([_row(push_tasks=False)], write=_row(push_tasks=False)))
    text = await core.my_tasks_calendar_connection("google", "show_tasks")
    assert text.startswith("The switch did not change.")


async def test_the_switch_on_promises_no_delivery(monkeypatch) -> None:
    """The tool cannot read ``CALENDAR_SYNC_WRITE``. Mutation: drop "when
    calendar write-out is on" -> red."""
    _, card = _install(monkeypatch, Gateway(write=_row(push_tasks=True)))
    text = await core.my_tasks_calendar_connection("google", "show_tasks")
    assert "when calendar write-out is on" in _text_of(card.cards[0])
    assert "when calendar write-out is on" in text


# ── 6. No token and no field outside the allow-list ──────────────────────────


@pytest.mark.parametrize(("tool", "args", "write"), [
    (ACT, ("google", "sync"), {"status": "ok", "added": 1, "access_token": SECRETS[0]}),
    (ACT, ("google", "show_tasks"), _row(push_tasks=True)),
    (ACT, ("google", "hide_tasks"), _row(push_tasks=False)),
    (OFF, ("google",), {"disconnected": True, "removed_entries": 2,
                        "refresh_token": SECRETS[1]}),
])
async def test_no_output_shows_a_token(monkeypatch, tool, args, write) -> None:
    """The fake body carries tokens. Mutation: print a row or a body -> red."""
    was = args[-1] == "hide_tasks"
    _, card = _install(monkeypatch, Gateway([_row(push_tasks=was)], write=write))
    text = await getattr(core, tool)(*args)
    shown = text + " " + " ".join(_text_of(c) for c in card.cards)
    for secret in SECRETS:
        assert secret not in shown, secret
    for word in ("token", "secret", "provider_meta", CID):
        assert word not in shown.lower(), word


# ── 7. Arguments, risk, the held chat, the pack, and parity ──────────────────


def test_no_tool_takes_an_id_an_org_or_a_member() -> None:
    """R5. Mutation: add ``connection_id``, ``organization_id``,
    ``member_email`` or ``user_email`` -> red."""
    assert set(inspect.signature(core.my_tasks_calendar_connection).parameters) == {
        "provider", "action"}
    assert set(inspect.signature(core.my_tasks_disconnect_calendar).parameters) == {
        "provider"}


def test_each_tool_is_an_open_world_write() -> None:
    """Mutation: mark one ``read_only`` or ``open_world=False``, or make
    Disconnect not destructive -> red."""
    for name in TOOLS:
        risk = getattr(skill_my_tasks, name).__tool_risk__
        assert risk["open_world"] is True and not risk.get("read_only"), name
    assert skill_my_tasks.my_tasks_disconnect_calendar.__tool_risk__["destructive"] is True


@pytest.mark.parametrize("orgs", ["", "*"])
def test_a_held_chat_drops_each_tool(monkeypatch, orgs: str) -> None:
    """With ``HELD_CARD_WRITES_ORGS`` empty and with it on. Mutation: mark a
    tool ``open_world=False`` or take it out of ``HELD_OFF_PACK_WRITES`` -> red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", orgs)
    for name in TOOLS:
        fn = getattr(skill_my_tasks, name)
        assert eg.is_egress_tool(fn), name
        assert name in eg.HELD_OFF_PACK_WRITES[SLUG], name


def test_the_pack_holds_the_tools_and_the_tasks_assistant_does_not() -> None:
    """Mutation: drop a tool from the family or from ``PACK_ONLY_TOOLS`` -> red."""
    assert set(TOOLS) <= set(sf.SKILL_FAMILIES[SLUG]["tools"])
    assert set(TOOLS) <= set(skill_my_tasks.PACK_ONLY_TOOLS)
    assert set(TOOLS) <= set(skill_my_tasks.__all__)


def test_the_parity_rows_name_the_tools() -> None:
    """The three rows moved from ``DEBT`` to ``COVERED``, and the calendar
    ceiling is 1. Mutation: put a row back in ``DEBT`` -> red."""
    rows = {"POST /api/calendar/connections/{}/sync": ACT,
            "PATCH /api/calendar/connections/{}": ACT,
            "DELETE /api/calendar/connections/{}": OFF}
    for key, tool in rows.items():
        assert cp.COVERED[key] == tool, key
        assert key not in cp.DEBT, key
    assert cp.DEBT_CEILING["calendar"] == 1


# ── 8. Reach: the member's words load the pack, and near words do not ────────


@pytest.mark.parametrize("words", [
    "sync my calendar", "disconnect my calendar", "disconnect my google calendar",
    "disconnect my outlook calendar", "sync google calendar",
    "sync my google calendar", "sync my outlook calendar",
    "show my tasks in google calendar", "show my tasks in outlook",
    "stop sending my tasks to outlook", "send my tasks to google calendar",
])
def test_the_words_load_the_pack(words: str) -> None:
    """Before P-1c, only the first two loaded ``calendar-day``. Mutation: drop
    a word from ``_CALENDAR_DAY_WORDS`` -> red."""
    assert SLUG in _chosen(words), words


@pytest.mark.parametrize("words", [
    "connect my outlook mail", "summarise my outlook inbox",
    "create a task to fix the google calendar bug",
    "assign the outlook calendar bug to Ravi", "move the google calendar task to done",
    "disconnect the slack integration", "disconnect the google drive integration",
    "sync the crm", "reconnect my email account", "sync my google drive",
    "send an email from outlook", "disconnect", "sync", "outlook",
])
def test_a_near_word_does_not_load_the_pack(words: str) -> None:
    """A bare "outlook", "sync" or "disconnect" is no word. Mutation: add one -> red."""
    assert SLUG not in _chosen(words), words
