"""CRM-M2e (task 3951): the chat adds and changes a stage, a lead status and a
lost reason, and resets the Zoho order of the stages, each on one card.

Spec: ``project-docs/specs/crm_conversational.md`` §8 (the slice CRM-M2e) and
``project-docs/specs/chat_parity.md`` (wave P-3). The tools are
``crm_edit_status`` and ``crm_edit_lost_reason`` of
``apps/agents/agent-crm/agents.py``.

The properties that each write of the agent holds (a card first, no write on
a decline, no write with no channel, the card names the row) are held for
these two tools too, by ``test_crm_agent_write.py``. This file holds what each
tool does: the route, the body, the name checks, and the refusals that send
no request.

Mutations this file catches (R7):

* a won stage with no win chance -> ``test_a_won_stage_sends_the_pinned_chance``;
* an add of a case variant -> ``test_an_add_refuses_a_name_the_crm_has``;
* a rename onto another row -> ``test_a_rename_onto_another_row_is_refused``;
* a delete that reaches the wire -> ``test_a_delete_sends_no_request``;
* a kind from the caller's text in the path -> ``test_an_unknown_kind_sends_no_request``.

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

_M = load_agent_module("crm_assistant_setup_tools")

_USER = "admin@fracktal.in"

_OPEN = str(uuid4())
_PROPOSAL = str(uuid4())
_WON = str(uuid4())
_NEW_LEAD = str(uuid4())
_PRICE = str(uuid4())
_TIMING = str(uuid4())

_DEAL_STAGES = [
    {"id": _OPEN, "name": "Qualification", "type": "open", "probability": 10,
     "hidden": False},
    {"id": _PROPOSAL, "name": "Proposal", "type": "ongoing", "probability": 40,
     "hidden": False},
    {"id": _WON, "name": "Closed Won", "type": "won", "probability": 100,
     "hidden": False},
]
_LEAD_STATUSES = [
    {"id": _NEW_LEAD, "name": "New", "type": "open", "probability": 0, "hidden": False},
]
_REASONS = [
    {"id": _PRICE, "label": "Price", "position": 10, "hidden": False},
    {"id": _TIMING, "label": "Timing", "position": 20, "hidden": True},
]


@pytest.fixture(autouse=True)
def _internal_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")


def _responder(call: dict) -> Any:
    path, method, body = call["path"], call["method"], call["json"] or {}
    if method == "GET" and path == "/crm/statuses/deal":
        return _DEAL_STAGES
    if method == "GET" and path == "/crm/statuses/lead":
        return _LEAD_STATUSES
    if method == "GET" and path == "/crm/lost-reasons":
        return _REASONS
    if method == "POST" and path.endswith("/order/reset"):
        return _DEAL_STAGES
    if method == "POST":
        return {"id": str(uuid4()), **body}
    if method == "PATCH":
        return {"id": path.rsplit("/", 1)[-1], **body}
    raise AssertionError(f"a tool reached an unexpected route: {method} {path}")


def _gateway(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[dict]:
    return fake_gateway(_M, monkeypatch, _responder, user=_USER, **kwargs)


def _card_text(card: dict) -> str:
    return " · ".join(str(card.get(k) or "") for k in ("title", "detail", "context"))


# ── crm_edit_status: add ────────────────────────────────────────────────────


async def test_an_add_posts_the_new_stage_as_an_open_stage(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "add", "Site visit")
    sent = writes(calls)
    assert [(c["method"], c["path"]) for c in sent] == [("POST", "/crm/statuses/deal")]
    assert sent[0]["json"] == {"name": "Site visit", "type": "open"}
    assert "Site visit" in _card_text(cards[0]) and "deal stage" in cards[0]["title"]
    assert out.startswith("Added the deal stage Site visit")


async def test_a_won_stage_sends_the_pinned_chance(monkeypatch) -> None:
    """D-CRM-10: the route refuses a won stage at 0%. Mutation: drop the pin -> red."""
    approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_edit_status("stages", "add", "Signed", status_type="WON")
    assert writes(calls)[0]["json"] == {"name": "Signed", "type": "won", "probability": 100}


async def test_a_lead_status_goes_to_the_lead_route(monkeypatch) -> None:
    approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_edit_status("lead status", "add", "Contacted", probability=20, hidden=True)
    sent = writes(calls)
    assert [(c["method"], c["path"]) for c in sent] == [("POST", "/crm/statuses/lead")]
    assert sent[0]["json"] == {"name": "Contacted", "type": "open", "probability": 20,
                               "hidden": True}


async def test_an_add_refuses_a_name_the_crm_has(monkeypatch) -> None:
    """A case variant is a second row with the same spoken name."""
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "add", "  proposal ")
    assert writes(calls) == [] and cards == []
    assert "already" in out and "action='change'" in out


# ── crm_edit_status: change ─────────────────────────────────────────────────


async def test_a_rename_patches_the_row_by_its_id(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "change", "proposal", new_name="Quote sent")
    sent = writes(calls)
    assert [(c["method"], c["path"]) for c in sent] == [
        ("PATCH", f"/crm/statuses/deal/{_PROPOSAL}")]
    assert sent[0]["json"] == {"name": "Quote sent"}
    assert "Proposal → Quote sent" in cards[0]["context"]
    assert "Change history keeps it" in out


async def test_a_new_lost_type_sends_the_pinned_chance(monkeypatch) -> None:
    approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_edit_status("deal", "change", "Proposal", status_type="lost")
    assert writes(calls)[0]["json"] == {"type": "lost", "probability": 0}


async def test_a_change_to_the_same_values_shows_no_card(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "change", "Proposal", new_name="Proposal",
                                   probability=40, hidden=False)
    assert writes(calls) == [] and cards == []
    assert "nothing was changed" in out


async def test_an_unknown_name_lists_the_real_ones(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "change", "Negotiation", new_name="x")
    assert writes(calls) == [] and cards == []
    assert "Qualification, Proposal, Closed Won" in out


async def test_a_name_that_matches_two_rows_is_a_question(monkeypatch) -> None:
    twin = {"id": str(uuid4()), "name": "proposal", "type": "open", "probability": 30}
    cards = approve(monkeypatch)

    def _twins(call: dict) -> Any:
        if call["method"] == "GET" and call["path"] == "/crm/statuses/deal":
            return [*_DEAL_STAGES, twin]
        return _responder(call)

    calls = fake_gateway(_M, monkeypatch, _twins, user=_USER)
    out = await _M.crm_edit_status("deal", "change", "Proposal", hidden=True)
    assert writes(calls) == [] and cards == []
    assert "more than one deal stage" in out


async def test_a_rename_onto_another_row_is_refused(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "change", "Proposal", new_name="closed won")
    assert writes(calls) == [] and cards == []
    assert "Another deal stage is called 'Closed Won'" in out


# ── crm_edit_status: reset_order ────────────────────────────────────────────


async def test_a_reset_posts_to_the_reset_route(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", "reset_order")
    assert [(c["method"], c["path"], c["json"]) for c in calls] == [
        ("POST", "/crm/statuses/deal/order/reset", {})]
    assert "Zoho" in cards[0]["title"]
    assert "order of Zoho again" in out


async def test_a_declined_reset_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("lead", "reset order")
    assert calls == [] and out.startswith("Declined:")


# ── The refusals that send no request ───────────────────────────────────────


@pytest.mark.parametrize("call", [
    lambda: _M.crm_edit_status("deal", "delete", "Proposal"),
    lambda: _M.crm_edit_lost_reason("remove", "Price"),
])
async def test_a_delete_sends_no_request(call: Any, monkeypatch) -> None:
    """The agent sends no DELETE (apps/AGENTS.md). Mutation: route a delete -> red."""
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await call()
    assert calls == [] and cards == []
    assert "cannot delete" in out and "CRM settings" in out


async def test_an_unknown_kind_sends_no_request(monkeypatch) -> None:
    """The kind goes into the path, so only the map gives it."""
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    with pytest.raises(RuntimeError, match="Unknown status kind"):
        await _M.crm_edit_status("../admin", "add", "x")
    assert calls == [] and cards == []


@pytest.mark.parametrize(("kwargs", "words"), [
    ({"action": "merge", "name": "Proposal"}, "not an action"),
    ({"action": "add", "name": "x", "status_type": "closed"}, "not a type"),
    ({"action": "add", "name": "x", "probability": 140}, "0 to 100"),
    ({"action": "change", "name": "  "}, "Give its name"),
])
async def test_a_bad_argument_sends_no_request(kwargs: dict, words: str, monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_status("deal", **kwargs)
    assert calls == [] and cards == []
    assert words in out


# ── crm_edit_lost_reason ────────────────────────────────────────────────────


async def test_a_lost_reason_add_posts_its_label(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_lost_reason("add", "Budget cut")
    sent = writes(calls)
    assert [(c["method"], c["path"], c["json"]) for c in sent] == [
        ("POST", "/crm/lost-reasons", {"label": "Budget cut"})]
    assert "Budget cut" in _card_text(cards[0])
    assert out.startswith("Added the lost reason Budget cut")


async def test_a_lost_reason_add_refuses_a_label_the_crm_has(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_lost_reason("add", "PRICE")
    assert writes(calls) == [] and cards == []
    assert "already" in out


async def test_a_lost_reason_change_patches_by_id(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_edit_lost_reason("change", "timing", new_label="Bad timing", hidden=False)
    sent = writes(calls)
    assert [(c["method"], c["path"]) for c in sent] == [
        ("PATCH", f"/crm/lost-reasons/{_TIMING}")]
    assert sent[0]["json"] == {"label": "Bad timing", "hidden": False}
    assert "yes → no" in cards[0]["context"]


async def test_a_lost_reason_rename_onto_another_is_refused(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_edit_lost_reason("change", "Timing", new_label="price")
    assert writes(calls) == [] and cards == []
    assert "Another lost reason is called 'Price'" in out


# ── Mirror mode ─────────────────────────────────────────────────────────────


class _MirrorClient(FakeClient):
    """The gateway in Mirror mode: a set-up write answers 409."""

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        resp = await super().request(method, url, **kwargs)
        if method.upper() == "GET":
            return resp
        return FakeResponse(
            {"detail": {"code": "mirror_mode_settings", "message": "Zoho owns this."}},
            status_code=409,
        )


@pytest.mark.parametrize("call", [
    lambda: _M.crm_edit_status("deal", "change", "Proposal", new_name="Quote"),
    lambda: _M.crm_edit_lost_reason("add", "Budget cut"),
])
async def test_a_mirror_refusal_comes_back_as_words(call: Any, monkeypatch) -> None:
    approve(monkeypatch)
    _gateway(monkeypatch, client_class=_MirrorClient)
    out = await call()
    assert out.startswith("Nothing was changed.") and "Mirror mode" in out
