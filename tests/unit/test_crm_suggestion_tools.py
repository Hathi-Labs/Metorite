"""P-3 (task 3954): the chat reads, previews, uses, dismisses and undoes a
suggestion of the CRM field map, and reads and starts the AI analysis.

Spec: ``project-docs/specs/chat_parity.md`` (wave P-3) and
``project-docs/specs/crm_conversational.md``. The tools are
``crm_mapping_suggestions``, ``crm_preview_suggestion``,
``crm_decide_suggestion``, ``crm_analysis_status`` and ``crm_rerun_analysis``
of ``apps/agents/agent-crm/agents.py``. The routes are
``gateway/routes/crm/suggestions.py``, ``mapping_plans.py`` (the preview) and
``analysis.py``.

The properties that each write of the agent holds (a card first, no write on
a decline, no write with no channel, the card names the row) are held for the
two writes by ``test_crm_agent_write.py``. This file holds what each tool
does: the route, the body, the name checks, and the refusals that send no
request.

Mutations this file catches (R7):

* a version from the model, not from the read -> ``test_an_approve_sends_the_version_of_the_read``;
* a first match of two -> ``test_a_module_that_matches_two_lists_is_a_question``;
* an unknown name that reaches the wire -> ``test_an_unknown_module_sends_no_write``;
* a list word from the caller in the path -> ``test_an_unknown_list_sends_no_request``;
* an undo that reads the suggestions, not the lists in use -> ``test_an_undo_posts_the_slot_of_the_list_in_use``.

Hermetic: no gateway, no database, no network.
"""

from __future__ import annotations

from typing import Any

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

_M = load_agent_module("crm_assistant_suggestion_tools")

_USER = "admin@fracktal.in"

_COMMERCIALS = {
    "slot": "deal", "version": 7, "module": "Deals_X", "label": "Commercials",
    "records": 40,
    "targets": [{"target": "name", "fields": ["Deal_Name"], "labels": ["Deal Name"],
                 "unmapped": 0},
                {"target": "amount", "fields": ["Value"], "labels": ["Value"],
                 "unmapped": 2}],
    "stage": {"field": "Phase", "label": "Phase",
              "values": {"Signed": "won", "Gone": "lost", "Talking": "open"},
              "unsorted": 1},
}
_PEOPLE = {
    "slot": "contact", "version": 7, "module": "People_X", "label": "People",
    "records": 12, "targets": [], "stage": None,
}
_IN_USE = {"slot": "lead", "module": "Prospects", "label": "Prospects"}

_LIST = {"suggestions": [_COMMERCIALS, _PEOPLE], "overlays": [_IN_USE]}

_PREVIEW = {"version": 7, "entities": [], "overlay": {
    "slot": "deal", "module": "Deals_X", "columns": ["name", "amount", "stage"],
    "rows": [{"id": "z1", "values": {"name": "Acme AMC", "amount": 5000, "stage": "won"}},
             {"id": "z2", "values": {"name": "Beta", "amount": None, "stage": None}}],
    "total": 40, "unmapped": {"amount": 2}, "unsorted": 1,
}}


@pytest.fixture(autouse=True)
def _internal_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")


def _responder(listing: dict[str, Any] = _LIST) -> Any:
    def _answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if method == "GET" and path == "/crm/mapping/suggestions":
            return listing
        if method == "POST" and path == "/crm/mapping/preview":
            return _PREVIEW
        if method == "POST" and path.startswith("/crm/mapping/suggestions/"):
            return {"slot": path.split("/")[4], "module": "Deals_X", "version": 8}
        if method == "GET" and path == "/crm/analysis/status":
            return {"run": {"id": "a1", "state": "finished",
                            "started_at": "2026-11-25T10:00:00Z",
                            "finished_at": "2026-11-25T10:02:00Z",
                            "steps": {"mapping": "done", "labels": "skipped",
                                      "questions": "done"},
                            "outcome": "limit"}, "allowed": True}
        if method == "POST" and path == "/crm/analysis/rerun":
            return {"id": "a2", "state": "running", "started_at": "2026-11-25T11:00:00Z",
                    "steps": {}, "outcome": None, "started": False}
        raise AssertionError(f"a tool reached an unexpected route: {method} {path}")
    return _answer


def _gateway(monkeypatch: pytest.MonkeyPatch, listing: dict[str, Any] = _LIST) -> list[dict]:
    return fake_gateway(_M, monkeypatch, _responder(listing), user=_USER)


def _card_text(card: dict) -> str:
    return " · ".join(str(card.get(k) or "") for k in ("title", "detail", "context"))


# ── The reads ───────────────────────────────────────────────────────────────


async def test_the_list_names_each_suggestion_and_each_list_in_use(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    out = await _M.crm_mapping_suggestions()
    assert [(c["method"], c["path"]) for c in calls] == [("GET", "/crm/mapping/suggestions")]
    assert "Commercials for Deals: 40 records" in out
    assert "columns from its fields: Name, Amount" in out
    assert "stage from Phase: 1 won, 1 lost and 1 open" in out
    assert "People for Contacts" in out and "Prospects for Leads" in out


async def test_an_empty_list_says_so(monkeypatch) -> None:
    _gateway(monkeypatch, {"suggestions": [], "overlays": []})
    assert await _M.crm_mapping_suggestions() == "No suggestion waits."


async def test_a_preview_posts_the_version_and_slot_of_the_read(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    out = await _M.crm_preview_suggestion(module="commercials")
    assert [(c["method"], c["path"], c["json"]) for c in calls] == [
        ("GET", "/crm/mapping/suggestions", None),
        ("POST", "/crm/mapping/preview", {"version": 7, "slot": "deal"}),
    ]
    assert "40 records, the first 2 shown" in out
    assert "Name=Acme AMC, Amount=5000, Stage=won" in out
    assert "2 values did not fit the Amount column" in out
    assert "1 records have a stage that is not sorted" in out


async def test_a_preview_with_no_name_sends_no_request(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    out = await _M.crm_preview_suggestion()
    assert calls == [] and "Which suggestion" in out


async def test_the_status_says_the_steps_and_the_outcome(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    out = await _M.crm_analysis_status()
    assert [(c["method"], c["path"]) for c in calls] == [("GET", "/crm/analysis/status")]
    assert "AI limit" in out
    assert "Reading your notes: skipped" in out


# ── crm_decide_suggestion ───────────────────────────────────────────────────


async def test_an_approve_sends_the_version_of_the_read(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_decide_suggestion("use", list_name="deal")
    sent = writes(calls)
    assert [(c["method"], c["path"], c["json"]) for c in sent] == [
        ("POST", "/crm/mapping/suggestions/deal/approve", {"version": 7})]
    assert cards[0]["title"] == "Use Commercials as Deals?"
    assert "Deals now opens the list of Commercials" in out


async def test_a_dismiss_by_module_finds_its_list(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await _M.crm_decide_suggestion("dismiss", module="PEOPLE")
    assert [(c["method"], c["path"], c["json"]) for c in writes(calls)] == [
        ("POST", "/crm/mapping/suggestions/contact/dismiss", {"version": 7})]
    assert "People" in _card_text(cards[0]) and "Contacts" in _card_text(cards[0])


async def test_an_undo_posts_the_slot_of_the_list_in_use(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_decide_suggestion("undo", list_name="leads")
    assert [(c["method"], c["path"], c["json"]) for c in writes(calls)] == [
        ("POST", "/crm/mapping/suggestions/lead/undo", {})]
    assert "Prospects" in cards[0]["title"]
    assert "Leads shows its own list again" in out


async def test_an_undo_with_no_list_in_use_says_so_plainly(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch, {"suggestions": [_COMMERCIALS], "overlays": []})
    out = await _M.crm_decide_suggestion("undo", list_name="leads")
    assert writes(calls) == [] and cards == []
    assert out == "No list shows a Zoho module now. Nothing was changed."


async def test_an_undo_of_a_list_no_module_shows_is_refused(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_decide_suggestion("undo", list_name="deals")
    assert writes(calls) == [] and cards == []
    assert "Prospects for Leads" in out and "Nothing was changed" in out


async def test_an_unknown_module_sends_no_write(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_decide_suggestion("approve", module="Invoices")
    assert writes(calls) == [] and cards == []
    assert "Commercials for Deals; People for Contacts" in out


async def test_a_module_that_matches_two_lists_is_a_question(monkeypatch) -> None:
    twin = dict(_PEOPLE, slot="company", module="People_Y")
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch, {"suggestions": [_COMMERCIALS, _PEOPLE, twin],
                                   "overlays": []})
    out = await _M.crm_decide_suggestion("approve", module="people")
    assert writes(calls) == [] and cards == []
    assert "more than one suggestion" in out and "Ask which list" in out


async def test_an_unknown_list_sends_no_request(monkeypatch) -> None:
    """The slot goes into the path, so only the map gives it."""
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    with pytest.raises(RuntimeError, match="Unknown CRM list"):
        await _M.crm_decide_suggestion("approve", list_name="../admin")
    assert calls == [] and cards == []


@pytest.mark.parametrize(("kwargs", "words"), [
    ({"action": "merge", "list_name": "deals"}, "not an action"),
    ({"action": "approve"}, "Which suggestion"),
])
async def test_a_bad_argument_sends_no_request(kwargs: dict, words: str, monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_decide_suggestion(**kwargs)
    assert calls == [] and cards == []
    assert words in out


async def test_a_declined_decision_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_decide_suggestion("approve", list_name="deals")
    assert writes(calls) == [] and out.startswith("Declined:")


# ── crm_rerun_analysis ──────────────────────────────────────────────────────


async def test_a_rerun_reads_the_status_then_asks_then_posts(monkeypatch) -> None:
    cards = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_rerun_analysis()
    assert [(c["method"], c["path"]) for c in calls] == [
        ("GET", "/crm/analysis/status"), ("POST", "/crm/analysis/rerun")]
    assert "AI credits" in cards[0]["context"]
    # The route answers `started: false`: a run began between the read and the POST.
    assert "runs already" in out and "no second one" in out


async def test_a_declined_rerun_sends_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await _M.crm_rerun_analysis()
    assert writes(calls) == [] and out.startswith("Declined:")


class _StatusClient(FakeClient):
    """The status read answers ``STATUS`` with ``BODY``. A test sets both."""

    STATUS = 200
    BODY: Any = None

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        resp = await super().request(method, url, **kwargs)
        if method.upper() == "GET" and url.endswith("/crm/analysis/status"):
            return FakeResponse(self.BODY, status_code=self.STATUS)
        return resp


@pytest.mark.parametrize(("status", "body", "words"), [
    (403, {"detail": "Forbidden"}, "Only a CRM admin"),
    (404, {"detail": "Not Found"}, "not on for this CRM"),
    (200, {"run": None, "allowed": False}, "not on for this organization"),
    (200, {"run": {"id": "a1", "state": "running", "started_at": "10:00",
                   "steps": {}, "outcome": None}, "allowed": True}, "runs already"),
])
async def test_a_rerun_that_cannot_start_shows_no_card(
    status: int, body: Any, words: str, monkeypatch,
) -> None:
    """Each refusal comes before the card, and sends no POST."""
    client = type("_Client", (_StatusClient,), {"STATUS": status, "BODY": body})
    cards = approve(monkeypatch)
    calls = fake_gateway(_M, monkeypatch, _responder(), user=_USER, client_class=client)
    out = await _M.crm_rerun_analysis()
    assert cards == [] and writes(calls) == []
    assert [c["path"] for c in calls] == ["/crm/analysis/status"]
    assert words in out
