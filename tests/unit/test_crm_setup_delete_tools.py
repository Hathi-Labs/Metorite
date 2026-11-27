"""Task 3994 (CRM-M2e): the chat deletes a stage, a lead status and a lost
reason, and sets the stage order, each on one card.

Owner answer 47891971 on task 3994 (2026-11-26): "Allow DELETE and PUT on
listed paths only". The tools are ``crm_delete_status``,
``crm_delete_lost_reason`` and ``crm_order_statuses`` of
``apps/agents/agent-crm/agents.py``. The transport guard of the list is held
by ``test_crm_agent.py``. The card properties of each write (a card first, no
write on a decline, no write with no channel, the card names the row) are held
by ``test_crm_agent_write.py``. This file holds what each tool does: the
route, the body, the words on the card, and each refusal that comes before the
card.

Mutations this file catches (R7):

* a delete of a stage that records sit in reaches the card ->
  ``test_a_stage_that_holds_records_is_refused_before_the_card``;
* a delete in Mirror mode reaches the card ->
  ``test_a_delete_in_mirror_mode_is_refused_before_the_card``;
* an order with a stage too few or two times ->
  ``test_an_order_that_is_not_every_stage_once_is_refused``;
* a kind from the caller's text in the path ->
  ``test_an_unknown_kind_sends_no_request``.

Hermetic: no gateway, no database, no network.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tests.unit._crm_agent_fakes import (
    FakeClient,
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    load_agent_module,
    writes,
)

_M = load_agent_module("crm_assistant_setup_delete_tools")

_USER = "admin@fracktal.in"

_OPEN = str(uuid4())
_PROPOSAL = str(uuid4())
_WON = str(uuid4())
_NEW_LEAD = str(uuid4())
_WORKING = str(uuid4())
_PRICE = str(uuid4())
_TIMING = str(uuid4())

_DEAL_STAGES = [
    {"id": _OPEN, "name": "Qualification", "type": "open", "hidden": False},
    {"id": _PROPOSAL, "name": "Proposal", "type": "ongoing", "hidden": False},
    {"id": _WON, "name": "Closed Won", "type": "won", "hidden": True},
]
_LEAD_STATUSES = [
    {"id": _NEW_LEAD, "name": "New", "type": "open", "hidden": False},
    {"id": _WORKING, "name": "Working", "type": "open", "hidden": False},
]
_REASONS = [
    {"id": _PRICE, "label": "Price", "position": 10, "hidden": False},
    {"id": _TIMING, "label": "Timing", "position": 20, "hidden": False},
]


@pytest.fixture(autouse=True)
def _internal_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")


#: What ``GET /crm/mode`` answers, and how many records sit in each stage.
_STATE: dict[str, Any] = {"mode": "native", "total": 0}


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    _STATE.update(mode="native", total=0)


def _responder(call: dict) -> Any:
    path, method = call["path"], call["method"]
    if method == "GET" and path == "/crm/mode":
        return {"mode": _STATE["mode"]}
    if method == "GET" and path == "/crm/statuses/deal":
        return _DEAL_STAGES
    if method == "GET" and path == "/crm/statuses/lead":
        return _LEAD_STATUSES
    if method == "GET" and path == "/crm/lost-reasons":
        return _REASONS
    if method == "GET" and path in ("/crm/deals", "/crm/leads"):
        return {"rows": [], "total": _STATE["total"]}
    if method == "DELETE":
        return {"deleted": path.rsplit("/", 1)[-1]}
    if method == "PUT" and path.endswith("/order"):
        return []
    raise AssertionError(f"a tool reached an unexpected route: {method} {path}")


def _gateway(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[dict]:
    return fake_gateway(_M, monkeypatch, _responder, user=_USER, **kwargs)


def _card_text(card: dict) -> str:
    return " · ".join(str(card.get(k) or "") for k in ("title", "detail", "context"))


# ── crm_delete_status ───────────────────────────────────────────────────────


async def test_a_stage_delete_names_the_row_and_sends_one_delete(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_status("deal", "proposal")
    assert [(c["method"], c["path"]) for c in writes(calls)] == [
        ("DELETE", f"/crm/statuses/deal/{_PROPOSAL}")]
    text = _card_text(cards[0])
    assert cards[0]["title"] == "Delete the deal stage Proposal?"
    assert "no deal sits in it now" in text
    assert "it leaves each pipeline that lists it" in text
    assert "no Undo" in text and "a pipeline holds it" in text
    assert cards[0]["close_on_steer"] is True
    assert "non_interactive_default" not in cards[0]
    assert out.startswith("Deleted the deal stage Proposal.") and "no Undo" in out


async def test_the_count_reads_the_records_of_the_stage(monkeypatch) -> None:
    """The count is the list of the kind, filtered by the status. A lead
    counts a converted lead too, as the delete route does."""
    approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_delete_status("deal", "Proposal")
    await _M.crm_delete_status("lead", "Working")
    counts = [c for c in calls if c["path"] in ("/crm/deals", "/crm/leads")]
    assert [(c["path"], c["params"]) for c in counts] == [
        ("/crm/deals", {"status_id": _PROPOSAL, "page_size": 1}),
        ("/crm/leads", {"status_id": _WORKING, "page_size": 1,
                        "include_converted": "true"}),
    ]
    assert ("DELETE", f"/crm/statuses/lead/{_WORKING}") in [
        (c["method"], c["path"]) for c in writes(calls)]


@pytest.mark.parametrize(("total", "words"), [(1, "1 deal,"), (4, "4 deals,")])
async def test_a_stage_that_holds_records_is_refused_before_the_card(
    total: int, words: str, monkeypatch,
) -> None:
    """The route refuses (409) while a record sits in the status, so the chat
    says so first. Mutation: drop the count -> a card -> red."""
    _STATE["total"] = total
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_status("deal", "Proposal")
    assert writes(calls) == [] and cards == []
    assert words in out and "Nothing was changed" in out and "hidden=true" in out


@pytest.mark.parametrize("call", [
    lambda: _M.crm_delete_status("deal", "Proposal"),
    lambda: _M.crm_delete_lost_reason("Price"),
])
async def test_a_delete_in_mirror_mode_is_refused_before_the_card(call: Any, monkeypatch) -> None:
    """Mirror mode answers each delete of the set-up with 409, so the chat says
    so first. Mutation: drop the mode read -> a card -> red."""
    _STATE["mode"] = "mirror"
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await call()
    assert writes(calls) == [] and cards == []
    assert [c["path"] for c in calls] == ["/crm/mode"]
    assert "Mirror mode" in out and "Nothing was changed" in out and "hidden=true" in out


async def test_an_unknown_stage_lists_the_real_ones(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_status("deal", "Negotiation")
    assert writes(calls) == [] and cards == []
    assert "There is no deal stage called 'Negotiation'" in out and "Proposal" in out


async def test_an_id_is_no_name(monkeypatch) -> None:
    """The model gives a name. A UUID matches no name, so nothing is sent."""
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_status("deal", _PROPOSAL)
    assert writes(calls) == [] and cards == []
    assert "There is no deal stage called" in out


async def test_a_name_that_matches_two_rows_is_a_question(monkeypatch) -> None:
    twins = [{"id": str(uuid4()), "name": "Demo"}, {"id": str(uuid4()), "name": "demo"}]
    cards = approve(monkeypatch)

    def responder(call: dict) -> Any:
        if call["path"] == "/crm/statuses/deal":
            return twins
        return _responder(call)

    calls = fake_gateway(_M, monkeypatch, responder, user=_USER)
    out = await _M.crm_delete_status("deal", "DEMO")
    assert writes(calls) == [] and cards == []
    assert "matches more than one deal stage" in out


async def test_a_declined_stage_delete_writes_nothing(monkeypatch) -> None:
    cards = deny(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_status("lead", "New")
    assert writes(calls) == [] and len(cards) == 1
    assert "The lead status New was not deleted." in out


async def test_an_unknown_kind_sends_no_request(monkeypatch) -> None:
    """The kind goes into the path, so only the map gives it."""
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    for call in (lambda: _M.crm_delete_status("../admin", "x"),
                 lambda: _M.crm_order_statuses("contact", ["x"])):
        with pytest.raises(RuntimeError, match="Unknown status kind"):
            await call()
    assert calls == [] and cards == []


async def test_an_empty_name_sends_no_request(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    assert "Give its name" in await _M.crm_delete_status("deal", "  ")
    assert "Give its label" in await _M.crm_delete_lost_reason("")
    assert calls == [] and cards == []


# ── crm_delete_lost_reason ──────────────────────────────────────────────────


async def test_a_reason_delete_names_the_row_and_says_what_records_keep(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_lost_reason("timing")
    assert [(c["method"], c["path"]) for c in writes(calls)] == [
        ("DELETE", f"/crm/lost-reasons/{_TIMING}")]
    text = _card_text(cards[0])
    assert cards[0]["title"] == "Delete the lost reason Timing?"
    assert "keeps its lost note" in text and "shows no lost reason" in text
    assert "no Undo" in text
    assert out.startswith("Deleted the lost reason Timing.") and "lost note" in out


async def test_an_unknown_reason_lists_the_real_ones(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_lost_reason("Budget")
    assert writes(calls) == [] and cards == []
    assert "There is no lost reason called 'Budget'" in out and "Price" in out


async def test_a_declined_reason_delete_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_delete_lost_reason("Price")
    assert writes(calls) == []
    assert "The lost reason Price was not deleted." in out


# ── crm_order_statuses ──────────────────────────────────────────────────────


async def test_an_order_puts_every_id_in_the_new_order(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_order_statuses("deal", ["Proposal", "qualification", "Closed  Won"])
    sent = writes(calls)
    assert [(c["method"], c["path"]) for c in sent] == [("PUT", "/crm/statuses/deal/order")]
    assert sent[0]["json"] == {"ids": [_PROPOSAL, _OPEN, _WON]}
    text = _card_text(cards[0])
    assert "Order now: Qualification → Proposal → Closed Won" in text
    assert "New order: Proposal → Qualification → Closed Won" in text
    assert "No record changes" in text and "no Undo" in text
    assert "Zoho" not in text
    assert out.startswith("The deal stages are in the new order: Proposal")


async def test_a_lead_order_goes_to_the_lead_route(monkeypatch) -> None:
    approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_order_statuses("lead statuses", ["Working", "New"])
    sent = writes(calls)
    assert [(c["method"], c["path"], c["json"]) for c in sent] == [
        ("PUT", "/crm/statuses/lead/order", {"ids": [_WORKING, _NEW_LEAD]})]


async def test_an_order_in_mirror_mode_says_zoho_keeps_its_order(monkeypatch) -> None:
    """Mirror mode writes the Metorite order only (``ui_position``)."""
    _STATE["mode"] = "mirror"
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_order_statuses("lead", ["Working", "New"])
    assert "Zoho keeps its own order" in _card_text(cards[0])
    assert [c["method"] for c in writes(calls)] == ["PUT"]


@pytest.mark.parametrize(("names", "words"), [
    (["Proposal", "Qualification"], "leaves out 'Closed Won'"),
    (["Proposal", "proposal", "Qualification", "Closed Won"], "two times"),
    (["Proposal", "Negotiation", "Qualification", "Closed Won"], "no deal stage called"),
    (["Qualification", "Proposal", "Closed Won"], "in that order already"),
    (["Proposal", "", "Qualification", "Closed Won"], "is empty"),
    ("Proposal, Qualification, Closed Won", "as a list"),
    (None, "as a list"),
])
async def test_an_order_that_is_not_every_stage_once_is_refused(
    names: Any, words: str, monkeypatch,
) -> None:
    """The route takes every id of the kind once. Mutation: drop the missing
    check or the repeat check -> red."""
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_order_statuses("deal", names)
    assert writes(calls) == [] and cards == []
    assert words in out


async def test_a_declined_order_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_order_statuses("lead", ["Working", "New"])
    assert writes(calls) == []
    assert "The order of the lead statuses did not change." in out


# ── A refusal of the route after the card ───────────────────────────────────


class _InUseClient(FakeClient):
    """A pipeline holds the stage: the route answers 409 ``in_use``."""

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        resp = await super().request(method, url, **kwargs)
        if method.upper() != "DELETE":
            return resp
        return FakeResponse(
            {"detail": {"error": "in_use", "message": "The deal pipeline uses it."}},
            status_code=409,
        )


async def test_a_refusal_after_the_card_is_the_words_of_the_route(monkeypatch) -> None:
    """The registered tool answers a refusal of the route as text."""
    approve(monkeypatch)
    _gateway(monkeypatch, client_class=_InUseClient)
    tool = next(t for t in _M._agent_tools()
                if getattr(t, "__name__", "") == "crm_delete_status")
    out = await tool("deal", "Proposal")
    assert isinstance(out, str) and "409" in out


# ── The edit tools point at the new tools ───────────────────────────────────


@pytest.mark.parametrize(("call", "tool"), [
    (lambda: _M.crm_edit_status("deal", "delete", "Proposal"), "crm_delete_status"),
    (lambda: _M.crm_edit_lost_reason("delete", "Price"), "crm_delete_lost_reason"),
])
async def test_an_edit_tool_names_the_delete_tool(call: Any, tool: str, monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await call()
    assert calls == [] and cards == []
    assert tool in out and "cannot delete" not in out


async def test_a_native_reset_names_the_order_tool(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "reset_order")
    assert writes(calls) == [] and cards == []
    assert "crm_order_statuses" in out and "CRM settings" not in out
