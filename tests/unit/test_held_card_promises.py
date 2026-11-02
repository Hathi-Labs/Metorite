"""RX-1 (D168): the card must keep its promise.

D168 rests on one sentence: the member's click on a server-built card is the
guard. The threat: an attacker controls one inbound email, and the member clicks
Approve on most cards. So a card that hides a change is no guard. Each case here
is a finding of the adversarial review of RX-1, and each names the mutation that
turns it red (R7).

1. A write to ``lead`` is a grant write, so a held chat refuses it.
2. A cut card: a short field always shows, and a held chat refuses a cut.
3. A destination: the card names the project and who can see it.
4. Access: the card says who an assignment or a move opens the task to.
5. A mention: the card names who gets told.
6. No text sends the member to an app.

Run: ``uv run pytest tests/unit/test_held_card_promises.py -q``.
"""
from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import acb_skills.egress as eg
from acb_common import card_body as cb
from acb_common.tool_refusals import GatewayRefusal
from skill_projects import forms as F
from skill_projects import guarded as G
from skill_projects import inbox as I
from skill_projects import writes as W

from tests.unit._crm_agent_fakes import approve as crm_approve
from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_crm_agent_write import _M as CRM
from tests.unit.test_crm_agent_write import _gateway as crm_gateway

PID = "0f8fad5b-d9cb-469f-a165-70867728950e"
DEST = "1f8fad5b-d9cb-469f-a165-70867728950e"
TID = "2f8fad5b-d9cb-469f-a165-70867728950e"
PARENT = "3f8fad5b-d9cb-469f-a165-70867728950e"
NEW = "4f8fad5b-d9cb-469f-a165-70867728950e"
LANE = "5f8fad5b-d9cb-469f-a165-70867728950e"
ROOT = "6f8fad5b-d9cb-469f-a165-70867728950e"

AUDIENCE = {"count": 3, "everyone": False, "sample": ["a@x.example", "b@x.example", "c@x.example"],
            "without_access": [], "gains": [], "gains_count": 0}


class Gateway:
    """The gateway, as far as these tools can tell. *audience* is its answer."""

    def __init__(self, audience: dict[str, Any] | None = None) -> None:
        self.audience = {**AUDIENCE, **(audience or {})}

    def __call__(self, call: dict[str, Any]) -> Any:
        path, method = call["path"], call["method"]
        if path.endswith("/audience"):
            return self.audience
        if path.endswith("/watchers"):
            return {"watchers": [], "watching": False, "audience": ["w@x.example"]}
        if path.endswith("/statuses"):
            return {"rows": [{"id": LANE, "name": "Todo", "category": "todo", "position": 1}]}
        if path == "/projects/tasks/move/preview":
            return {"task_count": 1, "drops": [], "subtasks": {"count": 0}}
        if method == "GET" and path.startswith("/projects/nodes/"):
            pid = path.rsplit("/", 1)[-1]
            return {"id": pid, "name": "Plant" if pid == PID else "Sales", "kind": "project",
                    "parent_project_id": ROOT, "status": "active", "lead": "old@x.example"}
        if method == "GET" and path.endswith(f"/tasks/{TID}"):
            return {"id": TID, "task_number": 7, "title": "Fix it", "project_id": PID,
                    "assignees": ["old@x.example"], "description": "was @one@x.example"}
        if method == "GET" and path.endswith(f"/tasks/{PARENT}"):
            return {"id": PARENT, "task_number": 8, "title": "The parent", "project_id": PID}
        if path.endswith("/people/names"):
            return {"names": {}}
        if method == "GET":
            return {"rows": [], "total": 0, "people": [], "agents": []}
        return {"id": NEW, "task_number": 9, "title": "made", "project_id": PID, "moved": 1}


def _held(monkeypatch: Any, answer: bool = True) -> None:
    """The chat read outside mail. The reader has tests of its own below."""

    async def chat_is_held(_ctx: Any = None) -> bool:
        return answer

    monkeypatch.setattr(eg, "chat_is_held", chat_is_held)


def _setup(monkeypatch: Any, *, held: bool, audience: dict[str, Any] | None = None) -> tuple:
    calls = fake_gateway(monkeypatch, Gateway(audience))
    cards = approve(monkeypatch)
    _held(monkeypatch, held)
    return calls, cards


def _no_app(text: str) -> None:
    low = text.lower()
    assert "new chat" not in low and "app" not in low.replace("apply", ""), text


# ── 1. A write to lead is a grant write ─────────────────────────────────────


@pytest.mark.parametrize(
    ("tool", "call"),
    [("create_project", lambda: W.create_project("New", lead="x@y.example")),
     ("update_project", lambda: W.update_project(PID, lead="x@y.example"))],
)
async def test_a_held_chat_refuses_lead(tool: str, call: Any, monkeypatch) -> None:
    """D-OP-11: a write to ``lead`` is a grant write, and ``grant_access`` is off,
    so ``lead`` was a second door. Mutation: drop the refusal -> a card -> red."""
    calls, cards = _setup(monkeypatch, held=True)
    answer = await call()
    assert answer == eg.held_param_refusal(tool, "lead")
    assert cards == [] and writes(calls) == []
    _no_app(answer)


async def test_an_open_chat_keeps_lead(monkeypatch) -> None:
    """Mutation: refuse in each chat -> red."""
    calls, cards = _setup(monkeypatch, held=False)
    await W.update_project(PID, lead="x@y.example")
    assert len(cards) == 1
    assert writes(calls)[-1]["json"]["lead"] == "x@y.example"


async def test_a_held_chat_refuses_the_lead_of_a_form_answer(monkeypatch) -> None:
    """``edit_project`` hands its answer to ``update_project``. Mutation: keep the
    field on the form -> red. Mutation: drop the refusal -> the write -> red."""
    calls, _cards = _setup(monkeypatch, held=True)
    forms: list[dict[str, Any]] = []

    async def member_types_a_lead(template: Any, **_k: Any) -> dict[str, Any]:
        forms.append(template)
        return {"name": "Plant", "status": "active", "lead": "x@y.example"}

    monkeypatch.setattr(F, "_ask", member_types_a_lead)
    answer = await F.edit_project(PID)
    assert "lead" not in json.dumps(forms[0]).lower().replace("leads", "")
    assert answer == eg.held_param_refusal("update_project", "lead")
    assert writes(calls) == []


@pytest.mark.parametrize(
    ("tool", "call"),
    [("create_field", lambda: W.create_field(PID, "F", org_wide=True)),
     ("create_tag", lambda: W.create_tag(PID, "T", org_wide=True)),
     ("create_type", lambda: W.create_type(PID, "Y", org_wide=True))],
)
async def test_a_held_chat_refuses_an_org_wide_entry(tool: str, call: Any, monkeypatch) -> None:
    """``org_wide`` puts the row on each project, so it writes an audience.
    Mutation: drop the refusal -> a card -> red."""
    calls, cards = _setup(monkeypatch, held=True)
    assert await call() == eg.held_param_refusal(tool, "org_wide")
    assert cards == [] and writes(calls) == []


def test_each_refused_parameter_is_a_real_parameter_of_a_kept_tool() -> None:
    """Mutation: a typo in the table -> the refusal guards nothing -> red."""
    for tool, params in eg.HELD_REFUSED_PARAMS.items():
        if tool in eg.HELD_CARD_WRITE_TOOLS:
            assert params <= eg.HELD_CARD_WRITE_TOOLS[tool], tool
            assert all(p in eg.HELD_PARAM_REASONS for p in params), tool


#: Words of a parameter that can write a grant, an owner or an audience.
_REACH_WORDS = ("lead", "owner", "grant", "audience", "visib", "share", "public", "recipient",
                "org_wide", "member")
#: Each parameter of a kept tool that holds such a word, and why it stays.
_REACH_KEPT = {
    ("create_lead", "lead_name"): "the name of a CRM lead",
    ("convert_lead", "lead_id"): "the id of a CRM lead",
}


def test_no_kept_parameter_writes_a_grant_an_owner_or_an_audience() -> None:
    """A new parameter like ``lead`` fails here until a person decides. Mutation:
    take ``lead`` out of ``HELD_REFUSED_PARAMS`` -> red."""
    found = []
    for tool, params in eg.HELD_CARD_WRITE_TOOLS.items():
        for param in params:
            if (any(word in param for word in _REACH_WORDS)
                    and param not in eg.HELD_REFUSED_PARAMS.get(tool, ())
                    and (tool, param) not in _REACH_KEPT):
                found.append((tool, param))
    assert found == []


# ── 2. A cut card ───────────────────────────────────────────────────────────


async def test_a_long_description_hides_no_status_and_no_lead(monkeypatch) -> None:
    """THE finding: ``update_project`` with 4100 characters of description drew no
    status and no lead, and the write sent both. Mutation: cut from the tail of
    the joined body -> red."""
    calls, cards = _setup(monkeypatch, held=False)
    text = "d" * 4100
    await W.update_project(PID, description=text, status="stopped", lead="x@y.example")
    context = cards[0]["context"]
    assert len(context) <= W.CARD_CONTEXT_LIMIT
    assert "status: «active» → «stopped»" in context
    assert "lead: «old@x.example» → «x@y.example»" in context
    assert cb.CUT_MARK in context
    assert writes(calls)[-1]["json"]["description"] == text


async def test_a_held_chat_refuses_a_cut_card_and_says_what_to_shorten(monkeypatch) -> None:
    """A 4225 character comment drew a 4000 character card, and the tail reached
    the wire. Mutation: drop the check in ``_confirm`` -> a card -> red."""
    calls, cards = _setup(monkeypatch, held=True)
    with pytest.raises(GatewayRefusal) as caught:
        await W.comment(TID, "c" * 4225)
    assert "Shorten body" in str(caught.value)
    assert cards == [] and writes(calls) == []
    _no_app(str(caught.value))


async def test_an_open_chat_may_show_a_cut_of_long_text_only(monkeypatch) -> None:
    """Mutation: refuse in each chat -> red."""
    calls, cards = _setup(monkeypatch, held=False)
    await W.comment(TID, "c" * 4225)
    assert cb.CUT_MARK in cards[0]["context"]
    assert writes(calls)[-1]["json"]["body"] == "c" * 4225


async def test_a_create_task_with_long_text_keeps_its_assignee(monkeypatch) -> None:
    """A long title and a long description hid the assignee in ``detail`` and in
    ``context``. Mutation: put the whole title in ``detail`` -> red."""
    _calls, cards = _setup(monkeypatch, held=False)
    await W.create_task(PID, "t" * 900, description="d" * 4000, assignees="who@x.example")
    card = cards[0]
    assert "who@x.example" in card["detail"]
    assert len(card["detail"]) <= 500
    assert "assignees: «who@x.example»" in card["context"]
    assert f"title: «{'t' * 900}»" in card["context"]


async def test_a_short_field_that_does_not_fit_refuses_in_each_chat(monkeypatch) -> None:
    """Mutation: cut the title -> a card -> red."""
    calls, cards = _setup(monkeypatch, held=False)
    with pytest.raises(cb.CardWontFit):
        await W.create_task(PID, "t" * 4200)
    assert cards == [] and writes(calls) == []


async def test_a_body_past_the_clip_never_reaches_the_card(monkeypatch) -> None:
    """``ask_tools`` clips with no mark. A line added after ``_fields_block`` could
    pass the clip. Mutation: drop the length check -> a card -> red."""
    _calls, cards = _setup(monkeypatch, held=False)
    with pytest.raises(cb.CardWontFit):
        await W._confirm("t", "d", "x" * (W.CARD_CONTEXT_LIMIT + 1))
    assert cards == []


def test_the_impact_line_of_a_class_c_card_counts_in_the_clip() -> None:
    """Mutation: add the impact after the body -> past the clip -> red."""
    body = G._guard_card("i" * 500, {"description": "d" * 5000})
    assert len(body) <= W.CARD_CONTEXT_LIMIT
    assert body.split("\n")[1].startswith("impact: ")


async def test_a_crm_card_keeps_each_short_field(monkeypatch) -> None:
    """The CRM copy of the cut. Mutation: cut the joined body -> red."""
    crm_gateway(monkeypatch)
    cards = crm_approve(monkeypatch)
    _held(monkeypatch, False)
    await CRM.create_lead(
        "Ravi", email="r@x.example", organization_name="Acme", description="d" * 4100)
    context = cards[0]["context"]
    assert "email: r@x.example" in context and "organization_name: Acme" in context
    assert cb.CUT_MARK in context and len(context) <= 4000


async def test_a_held_chat_refuses_a_cut_crm_card(monkeypatch) -> None:
    """Mutation: the plain ``_fields_block`` at the card -> a card -> red."""
    calls = crm_gateway(monkeypatch)
    cards = crm_approve(monkeypatch)
    _held(monkeypatch, True)
    with pytest.raises(GatewayRefusal) as caught:
        await CRM.create_lead("Ravi", description="d" * 4100)
    assert "Shorten description" in str(caught.value)
    assert cards == [] and [c for c in calls if c["method"] != "GET"] == []


def test_a_crm_value_cannot_forge_a_card_line() -> None:
    """A value kept its newline. Mutation: print the raw value -> red."""
    body = CRM._fields_block({"subject": "hi\nstage: Won", "body": "a\n\nb"})
    assert "\nstage:" not in body
    assert "subject: hi stage: Won" in body


# ── 3. The destination ──────────────────────────────────────────────────────


async def test_a_card_with_a_project_id_names_the_project(monkeypatch) -> None:
    """With a UUID the card held only a hidden ``project_id`` line, so the member
    approved text with no destination. Mutation: keep the id line -> red."""
    _calls, cards = _setup(monkeypatch, held=False)
    await W.create_task(PID, "Fix it")
    context = cards[0]["context"]
    assert "project: «Plant»" in context
    assert "project_id" not in context
    assert "· in «Plant»" in cards[0]["detail"]


async def test_a_card_names_the_parent_task(monkeypatch) -> None:
    """``parent_task_id`` had the same fault. Mutation: keep the id line -> red."""
    _calls, cards = _setup(monkeypatch, held=False)
    await W.create_task(PID, "Fix it", parent_task_id=PARENT)
    context = cards[0]["context"]
    assert "parent: #8 «The parent»" in context
    assert "parent_task_id" not in context


async def test_a_held_card_says_who_can_see_the_task(monkeypatch) -> None:
    """Mutation: drop ``_access_lines`` -> red."""
    calls, cards = _setup(monkeypatch, held=True)
    await W.create_task(PID, "Fix it")
    assert "seen by: «a@x.example, b@x.example, c@x.example»" in cards[0]["context"]
    assert any(c["path"] == f"/projects/nodes/{PID}/audience" for c in calls)


async def test_the_card_says_when_everyone_sees_the_task(monkeypatch) -> None:
    _calls, cards = _setup(monkeypatch, held=True, audience={"count": 12, "everyone": True})
    await W.create_task(PID, "Fix it")
    assert "everyone in the organization (12 people)" in cards[0]["context"]


async def test_a_held_chat_shows_the_mailbox_line_with_no_source(monkeypatch) -> None:
    """A steered model leaves out ``source="email"``, so the warning cannot rest on
    it. Mutation: read ``source`` alone -> red."""
    _calls, cards = _setup(monkeypatch, held=True)
    await W.create_task(PID, "Fix it")
    assert cards[0]["context"].split("\n")[1].startswith("mailbox: This text leaves your mailbox")


async def test_an_open_chat_shows_no_mailbox_line_with_no_source(monkeypatch) -> None:
    """Mutation: show it in each chat -> red."""
    calls, cards = _setup(monkeypatch, held=False)
    await W.create_task(PID, "Fix it")
    assert "mailbox" not in cards[0]["context"] and "seen by" not in cards[0]["context"]
    assert not any(c["path"].endswith("/audience") for c in calls), "an open chat reads nothing"


async def test_a_held_batch_says_the_same(monkeypatch) -> None:
    """``create_tasks`` is the same act. Mutation: skip the batch -> red."""
    _calls, cards = _setup(monkeypatch, held=True, audience={"without_access": ["n@x.example"]})

    async def ticks_all(*_a: Any, **kw: Any) -> frozenset[str]:
        cards.append(kw)
        return frozenset(r["id"] for r in kw["rows"])

    monkeypatch.setattr(F, "_confirm", ticks_all)
    await F.create_tasks(PID, json.dumps([
        {"title": "One", "assignees": "n@x.example"}, {"title": "Two"}]))
    context = cards[-1]["context"]
    assert "mailbox: This text leaves your mailbox" in context
    assert "seen by:" in context and "gives access to: «n@x.example»" in context


async def test_a_held_capture_shows_the_mailbox_line_with_no_source(monkeypatch) -> None:
    """Mutation: read ``source`` alone -> red."""
    _calls, cards = _setup(monkeypatch, held=True)
    await I.capture_intake("Fix it", project_id=PID)
    assert "mailbox: This text leaves your mailbox" in cards[0]["context"]
    assert "seen by:" in cards[0]["context"]


async def test_a_failed_read_of_the_audience_shows_no_card(monkeypatch) -> None:
    """A card that cannot say who sees the row is no guard. Mutation: swallow the
    refusal -> a card -> red."""
    gateway = Gateway()

    def broken(call: dict[str, Any]) -> Any:
        if call["path"].endswith("/audience"):
            from tests.unit._projects_agent_fakes import FakeResponse

            return FakeResponse({"detail": "no"}, 500)
        return gateway(call)

    calls = fake_gateway(monkeypatch, broken)
    cards = approve(monkeypatch)
    _held(monkeypatch, True)
    with pytest.raises(GatewayRefusal):
        await W.create_task(PID, "Fix it")
    assert cards == [] and writes(calls) == []


# ── 4. Access ───────────────────────────────────────────────────────────────


async def test_an_assignment_says_who_it_opens_the_task_to(monkeypatch) -> None:
    """An assignment opens the task to its assignee (``notifications.py``). Mutation:
    drop the line -> red."""
    calls, cards = _setup(monkeypatch, held=True, audience={"without_access": ["new@x.example"]})
    await W.assign(TID, "old@x.example, new@x.example")
    assert "gives access to: «new@x.example»" in cards[0]["context"]
    read = next(c for c in calls if c["path"].endswith("/audience"))
    assert read["params"]["people"] == "new@x.example", "only the people that the call adds"


async def test_an_assignment_inside_the_audience_says_nothing_more(monkeypatch) -> None:
    """Mutation: print the line with no person -> red."""
    _calls, cards = _setup(monkeypatch, held=True)
    await W.assign(TID, "old@x.example, a@x.example")
    assert "gives access to" not in cards[0]["context"]


async def test_a_move_says_who_it_opens_the_task_to(monkeypatch) -> None:
    """D-OP-14: a move changes who sees a row. Mutation: drop the read -> red."""
    calls, cards = _setup(monkeypatch, held=True,
                          audience={"gains": ["z@x.example"], "gains_count": 3})
    await W.move_task(TID, destination_project_id=DEST)
    context = cards[0]["context"]
    assert "gives access to: «z@x.example and 2 more»" in context
    assert "seen by:" in context
    read = next(c for c in calls if c["path"].endswith("/audience"))
    assert read["path"] == f"/projects/nodes/{DEST}/audience"
    assert read["params"]["beyond"] == PID


async def test_a_batch_move_says_it_on_the_row(monkeypatch) -> None:
    """``file_tasks`` draws each row from the move card. Mutation: drop the key from
    the row hint -> red."""
    _calls, _cards = _setup(monkeypatch, held=True,
                            audience={"gains": ["z@x.example"], "gains_count": 1})
    rows: list[Any] = []

    async def ticks_all(**kw: Any) -> frozenset[str]:
        rows.extend(kw["rows"])
        return frozenset(r["id"] for r in kw["rows"])

    monkeypatch.setattr(W, "_confirm", ticks_all)
    await W.file_tasks(json.dumps([{"task_id": TID, "destination_project_id": DEST}]))
    assert "gives access to: z@x.example" in rows[0]["hint"]


# ── 5. A mention ────────────────────────────────────────────────────────────


async def test_a_comment_card_names_who_it_tells(monkeypatch) -> None:
    """``add_comment`` notifies each address that the body names. Mutation: drop the
    line -> red."""
    _calls, cards = _setup(monkeypatch, held=False)
    await W.comment(TID, "See this @priya@x.example and @ravi@x.example")
    assert "sent to: «priya@x.example, ravi@x.example»" in cards[0]["context"]


async def test_a_held_comment_card_names_the_people_the_task_tells(monkeypatch) -> None:
    """The comment reaches each watcher and assignee too. Mutation: read the mentions
    alone -> red."""
    _calls, cards = _setup(monkeypatch, held=True)
    await W.comment(TID, "See this @priya@x.example")
    assert "sent to: «priya@x.example, w@x.example»" in cards[0]["context"]


async def test_an_edit_names_only_the_people_it_adds(monkeypatch) -> None:
    """A description edit tells only the addresses it adds. Mutation: use
    ``mention_targets`` -> ``one`` shows -> red."""
    _calls, cards = _setup(monkeypatch, held=False)
    await W.update_task(TID, description="was @one@x.example and @two@x.example")
    assert "sent to: «two@x.example»" in cards[0]["context"]


def test_the_card_and_the_gateway_read_one_parser() -> None:
    """A second parser lets a card and its write disagree. Mutation: copy the regex
    back into the gateway -> red."""
    from acb_common import mentions
    from gateway.routes.projects import notifications

    assert notifications.mention_targets is mentions.mention_targets
    assert notifications.new_mentions is mentions.new_mentions
    assert W.mention_targets is mentions.mention_targets


# ── 6. No text sends the member away ────────────────────────────────────────


def test_an_agent_assignee_refusal_claims_no_cause_and_names_no_app() -> None:
    """D168 item 7. The text said "sandboxed turn" in a mail hold, and sent the member
    to the Projects app. Mutation: put either back -> red."""
    assert "sandboxed" not in W.AGENT_ASSIGNEE_REFUSED
    _no_app(W.AGENT_ASSIGNEE_REFUSED)


async def test_a_marked_top_level_chat_refuses_an_agent_assignee(monkeypatch) -> None:
    """A top-level run binds ``no_egress=False``, so the old read missed it. Mutation:
    read ``no_egress`` alone -> a card -> red."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    calls, cards = _setup(monkeypatch, held=True)
    with artifact_context_scope():
        bind_artifact_context(no_egress=False, member="m@x.example", session_id="s-1")
        answer = await W.assign(TID, "agent:projects-assistant")
    assert answer == W.AGENT_ASSIGNEE_REFUSED
    assert cards == [] and writes(calls) == []


def test_each_kept_project_write_reaches_the_one_door() -> None:
    """``_check_card`` runs in ``_confirm``. Mutation: a new door past it -> red."""
    source = inspect.getsource(W._confirm)
    assert "await _check_card(context)" in source
    assert source.index("_check_card") < source.index("request_confirmation(")
