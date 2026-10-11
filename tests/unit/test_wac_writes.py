"""WS-47 WAC-4 — a confirmed write from WhatsApp, database-free.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §14 (14.7: W1 to W7
and W9). W8 is the R8 suite, ``test_wac_writes_r8.py``.

The REAL webhook route, the REAL ``inbound`` and ``bot_run`` logic, the REAL
injection seam wrapper (``_gate_injected_tool``), the REAL card gate
(``ask_tools.request_confirmation``) and the REAL ``create_task`` tool run
here. The fakes are those of ``test_wac_bot_run.py``, plus the act table of
``acts.py`` in memory and the Projects gateway behind ``skill_projects.client``.

R7 fences named here:

* ``wac4-park-then-confirm`` (W1, W2): an allowed act writes nothing until
  Confirm, and one Confirm makes exactly one create call.
* ``wac4-bound`` (W3): an expired act, another phone and another org write
  nothing.
* ``wac4-cancel-void`` (W4): Cancel closes the act, any other message voids
  it and runs as normal.
* ``wac4-only-create-task`` (W5): every other card stays refused.
* ``wac4-reserved-titles`` (W6): the model cannot offer Confirm or Cancel.
* ``wac4-dark`` (W7): with the switch off, the run is the read-only run.
* ``wac4-card-must-match`` (W9): a card that changed writes nothing.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import orchestrator._tool_injection as ti
import pytest
import skill_projects
from acb_common import get_settings
from acb_skills import ask_tools
from acb_skills import whatsapp_acts as wacts
from acb_skills import whatsapp_ui as wui
from gateway.db import current_tenant
from gateway.routes.whatsapp_channel import acts, bot_run, flags
from skill_projects import manifest
from skill_projects.refusals import refusals_as_text

from tests.unit._projects_agent_fakes import FakeClient
from tests.unit.test_projects_agent_writes import UUID, responder
from tests.unit.test_wac_bot_run import (  # noqa: F401 - `world` is a fixture
    MEMBER,
    ORG_A,
    ORG_B,
    PHONE,
    _later_sweeps,
    _message,
    _post,
    _post_and_run,
    _World,
    world,
)
from tests.unit.test_wac_native_ui import _UiProvider

PHONE_2 = "919990000002"
ASK = "Add a task: call the vendor tomorrow"
READY = "Ready to add it. Tap Confirm to add it, or Cancel."
CREATE = {"project_id": UUID, "title": "Call the vendor", "due": "2026-10-12"}


# ── The fakes ───────────────────────────────────────────────────────────────


class _ActStore:
    """The act table of ``acts.py``, in memory, with row level security."""

    def __init__(self, msgs: Any) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        #: The bot message rows of ``test_wac_bot_run._Store``: the join of
        #: ``_LIVE_SQL`` reads the tap's context and its order there.
        self.msgs = msgs
        self.now = time.time()
        #: The current link row id of each (org, phone). None: no link.
        self.links: dict[tuple[str, str], str | None] = {}
        self.calls = 0

    def _org(self) -> str:
        org = current_tenant()
        assert org, "an unbound read or write"
        return org

    async def current_link_id(self, wa_id: str, email: str) -> str | None:
        self.calls += 1
        return self.links.get((self._org(), wa_id), f"link-{self._org()[:4]}-{wa_id}")

    async def store(self, req: bot_run.RunRequest, act: Any, link_id: str) -> str:
        self.calls += 1
        org = self._org()
        assert org == req.organization_id
        for row in self.rows.values():
            if (row["org"], row["wa_id"], row["sid"], row["state"]) == (
                    org, req.wa_id, req.chat_session_id, "pending"):
                row.update(state="void", code="replaced")
        aid = str(uuid.uuid4())
        self.rows[aid] = {
            "id": aid, "org": org, "member_email": req.member_email,
            "wa_id": req.wa_id, "sid": req.chat_session_id, "link_id": link_id,
            "tool_name": act.tool, "arguments": dict(act.arguments),
            "card_text": act.card, "state": "pending", "code": None,
            "expires": self.now + acts.ACT_TTL_S, "order": len(self.rows),
            "offered_seq": None, "offered_wamid": None,
        }
        return aid

    async def mark_offered(self, act_id: str, out_wamid: str) -> bool:
        row = self.rows.get(act_id)
        if row is None or row["org"] != self._org() or row["state"] != "pending"                 or row["offered_seq"] is not None:
            return False
        # The order of the bot message rows is the clock of "received after".
        row.update(offered_seq=self.msgs._next(), offered_wamid=out_wamid,
                   expires=self.now + acts.ACT_TTL_S)
        return True

    async def live_act(self, req: bot_run.RunRequest) -> dict[str, Any] | None:
        self.calls += 1
        org = self._org()
        mine = sorted((r for r in self.rows.values()
                       if r["org"] == org and r["wa_id"] == req.wa_id
                       and r["sid"] == req.chat_session_id and r["state"] == "pending"),
                      key=lambda r: -r["order"])
        if not mine:
            return None
        act, msg = mine[0], self.msgs.rows.get(req.message_id) or {}
        offered = act["offered_seq"] is not None
        return {**act, "live": act["expires"] > self.now, "offered": offered,
                "tap_of": msg.get("context"),
                "after_offer": offered and msg.get("order", -1) > act["offered_seq"]}

    async def claim(self, act_id: str) -> bool:
        row = self.rows.get(act_id)
        if row is None or row["org"] != self._org() or row["state"] != "pending" \
                or row["expires"] <= self.now:
            return False
        row["state"] = "running"
        return True

    async def close(self, act_id: str, frm: str, to: str, code: str | None) -> bool:
        row = self.rows.get(act_id)
        if row is None or row["org"] != self._org() or row["state"] != frm:
            return False
        row.update(state=to, code=code)
        return True

    def only(self) -> dict[str, Any]:
        (row,) = self.rows.values()
        return row


class _ActAgent:
    """``run_agent`` that calls the REAL ``create_task`` the way a run does.

    The tool goes through the REAL injection seam wrapper, with the member
    bound as the executor binds it. Each run takes the next script entry:
    the ``create_task`` arguments, or None for a plain answer.
    """

    def __init__(self, *script: dict[str, Any] | None, reply: str = READY) -> None:
        self.script = list(script)
        self.reply = reply
        self.seen: list[dict[str, Any]] = []
        self.outputs: list[str] = []

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any) -> Any:
        from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id

        self.seen.append({"payload": payload, "run": wui.current_run()})
        args = self.script.pop(0) if self.script else None
        if args is None:
            return {"result": "Two tasks are due today."}
        tool = ti._gate_injected_tool(refusals_as_text(skill_projects.create_task))
        binding = _bind_memory_user_id(kw["session_user"])
        try:
            self.outputs.append(str(await tool(**args)))
        finally:
            _unbind_memory_user_id(binding)
        return {"result": self.reply}


class _ButtonProvider(_UiProvider):
    """Records each button's titles, and the message id each one went as."""

    def __init__(self, w: _World) -> None:
        super().__init__(w)
        self.buttons: list[list[str]] = []
        #: Meta's message id of each button message, in send order.
        self.button_wamids: list[str] = []

    async def send_interactive(self, to: str, interactive: dict, *,
                               reply_to_wa_message_id: str | None = None) -> str:
        out = await super().send_interactive(
            to, interactive, reply_to_wa_message_id=reply_to_wa_message_id)
        if interactive["type"] == "button":
            self.buttons.append([b["reply"]["title"]
                                 for b in interactive["action"]["buttons"]])
            self.button_wamids.append(out)
        return out


class _Env(SimpleNamespace):
    world: _World
    acts: _ActStore
    prov: _ButtonProvider
    gateway: list[dict]
    tree: list[dict[str, Any]]


def _creates(env: _Env) -> list[dict]:
    return [c for c in env.gateway
            if c["method"] == "POST" and c["path"] == "/projects/tasks"]


def _texts(env: _Env) -> list[str]:
    return [s for _to, s in env.world.sent if isinstance(s, str)]


def _switches(monkeypatch: pytest.MonkeyPatch, *, native: bool, writes: bool) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_assistant_native_ui", native, raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_writes", writes, raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_writes_orgs", f"{ORG_A},{ORG_B}",
                        raising=False)


@pytest.fixture()
def env(world: _World, monkeypatch: pytest.MonkeyPatch) -> _Env:  # noqa: F811
    _switches(monkeypatch, native=True, writes=True)
    store = _ActStore(world.store)
    monkeypatch.setattr(acts, "_current_link_id", store.current_link_id)
    monkeypatch.setattr(acts, "_store", store.store)
    monkeypatch.setattr(acts, "_live_act", store.live_act)
    monkeypatch.setattr(acts, "_claim", store.claim)
    monkeypatch.setattr(acts, "_close", store.close)
    monkeypatch.setattr(acts, "_mark_offered", store.mark_offered)

    prov = _ButtonProvider(world)
    from whatsapp_ingestion.providers import factory

    monkeypatch.setattr(factory, "build_provider", lambda name, creds: prov)

    tree: list[dict[str, Any]] = [{"id": UUID, "name": "Ops", "kind": "project"}]

    def _gateway(call: dict) -> Any:
        if call["path"] == "/projects/tree":
            return {"rows": [dict(n) for n in tree]}
        return responder(call)

    import skill_projects.client as client

    calls: list[dict] = []
    # The client's REAL acting user: the member the run or the Confirm binds.
    monkeypatch.setattr(client, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: FakeClient(calls, _gateway)))
    return _Env(world=world, acts=store, prov=prov, gateway=calls, tree=tree)


def _tap(title: str, sender: str = PHONE, *, of: str | None = None) -> dict[str, Any]:
    """A tap on a reply button. *of* is the button message's id, which Meta
    gives in ``context.id``."""
    change = _message(sender=sender)
    msg = change["value"]["messages"][0]
    msg.pop("text", None)
    msg.update(type="interactive", interactive={
        "type": "button_reply", "button_reply": {"id": "b1", "title": title}})
    if of is not None:
        msg["context"] = {"from": "919800000000", "id": of}
    return change


async def _ask(env: _Env, args: dict[str, Any] | None = None) -> None:
    env.world.agent = _ActAgent(dict(args or CREATE))
    await _post_and_run(_message(ASK))


# ── W1: the ask parks the act, and writes nothing ──────────────────────────


async def test_an_add_task_gets_the_summary_and_confirm_and_cancel_and_writes_nothing(
    env: _Env,
) -> None:
    await _ask(env)

    assert _creates(env) == [], "the ask wrote a task"
    (output,) = env.world.agent.outputs
    assert output == wacts.PARKED_TEXT, "the model must not read 'Cancelled'"
    (seen,) = env.world.agent.seen
    assert seen["payload"]["system_context"] == bot_run.SCOPE_RULE_WRITES
    assert seen["run"].writes is True

    row = env.acts.only()
    assert row["state"] == "pending" and row["org"] == ORG_A
    assert row["member_email"] == MEMBER and row["wa_id"] == PHONE
    assert row["tool_name"] == "create_task"
    assert row["arguments"]["title"] == "Call the vendor"
    assert row["arguments"]["due"] == "2026-10-12"

    sent = env.world.sent
    assert sent[0] == (PHONE, READY), "the model's line goes first"
    assert sent[-1] == (PHONE, ("interactive", "button"))
    summary = sent[1][1]
    assert summary.startswith("*Create this task?*")
    assert "Call the vendor" in summary and "«" not in summary
    assert env.prov.buttons == [["Confirm", "Cancel"]]
    # The thread keeps the buttons, so the next tap is the AI's own choice.
    reply = env.world.store.threads[row["sid"]][-1]["content"]
    assert "[Buttons: Confirm | Cancel]" in reply


# ── W2: one Confirm, one create ─────────────────────────────────────────────


async def test_one_confirm_makes_exactly_one_create_as_the_member_and_runs_no_model(
    env: _Env,
) -> None:
    await _ask(env)
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))

    (created,) = _creates(env)
    assert created["json"]["title"] == "Call the vendor"
    assert created["headers"]["X-User-Email"] == MEMBER
    assert len(env.world.agent.seen) == 1, "a model ran on Confirm"
    assert env.acts.only()["state"] == "done"
    receipt = _texts(env)[-1]
    assert "Call the vendor" in receipt and "Next:" not in receipt
    # N4: the "yes" answered one card and is gone.
    assert wacts._CONFIRMING.get() is None
    assert ask_tools.cards_refused() is False


async def test_a_confirm_in_a_thread_that_became_a_room_writes_nothing(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verifier D7: the room check ran after the act settled, so the task was
    # written and the member got the failure text. A room settles no act.
    await _ask(env)
    # The run's own check (`_check`) sees a solo thread. The thread becomes a
    # room right after, before the act would settle.
    calls: list[str] = []

    async def _room(_sid: str) -> bool:
        calls.append(_sid)
        return len(calls) > 1

    monkeypatch.setattr(bot_run, "_thread_shared", _room)
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))
    assert _creates(env) == [], "a Confirm in a room wrote the task"
    assert env.acts.only()["state"] == "pending"


async def test_a_second_confirm_or_a_redelivered_confirm_writes_nothing(env: _Env) -> None:
    await _ask(env)
    confirm = _message("confirm ", wamid="wamid.CONFIRM")
    await _post_and_run(confirm)
    assert len(_creates(env)) == 1

    await _post_and_run(confirm)   # Meta delivers it again
    env.world.agent = _ActAgent(None)
    await _post_and_run(_message("Confirm"))   # a second Confirm: no act waits

    assert len(_creates(env)) == 1, "a second create"
    assert len(env.world.agent.seen) == 1, "the second Confirm is a normal message"


# ── W3: the expiry, the phone and the org ───────────────────────────────────


async def test_a_confirm_after_10_minutes_writes_nothing_and_says_it_expired(
    env: _Env,
) -> None:
    await _ask(env)
    env.acts.now += acts.ACT_TTL_S + 1
    await _post_and_run(_message("Confirm"))

    assert _creates(env) == []
    assert env.acts.only()["state"] == "expired"
    assert _texts(env)[-1] == acts.REPLY_EXPIRED


async def test_a_confirm_from_another_phone_writes_nothing(env: _Env) -> None:
    await _ask(env)
    env.world.agent = _ActAgent(None)
    await _post_and_run(_message("Confirm", sender=PHONE_2))

    assert _creates(env) == []
    assert env.acts.only()["state"] == "pending", "another phone touched the act"
    assert len(env.world.agent.seen) == 1, "the other phone's Confirm is a normal message"


async def test_a_confirm_after_an_org_switch_writes_nothing(env: _Env) -> None:
    await _ask(env)
    env.world.links = [
        {"organization_id": ORG_A, "member_email": MEMBER, "is_current": False},
        {"organization_id": ORG_B, "member_email": MEMBER, "is_current": True},
    ]
    env.world.identity = ("uid-1", ORG_B)
    env.world.agent = _ActAgent(None)
    await _post_and_run(_message("Confirm"))

    assert _creates(env) == []
    assert env.acts.only()["state"] == "pending", "org B saw org A's act"
    (seen,) = env.world.agent.seen
    assert seen["payload"]["message"] == "Confirm"


async def test_a_confirm_after_the_link_changed_voids_the_act(env: _Env) -> None:
    """A lost link and a new link give another link row, or none (§14.5)."""
    await _ask(env)
    env.acts.links[(ORG_A, PHONE)] = "link-new"
    await _post_and_run(_message("Confirm"))

    assert _creates(env) == []
    assert env.acts.only()["state"] == "void"
    assert _texts(env)[-1] == acts.REPLY_LINK


async def test_an_ask_with_no_current_link_row_offers_no_buttons(env: _Env) -> None:
    env.acts.links[(ORG_A, PHONE)] = None
    await _ask(env)

    assert env.acts.rows == {} and env.prov.buttons == []
    assert acts.REPLY_NOT_HELD in _texts(env)[0]


# ── W4: Cancel, or anything else ────────────────────────────────────────────


async def test_cancel_closes_the_act_and_writes_nothing(env: _Env) -> None:
    await _ask(env)
    await _post_and_run(_tap("Cancel"))

    assert _creates(env) == []
    assert env.acts.only()["state"] == "cancelled"
    assert _texts(env)[-1] == acts.REPLY_CANCELLED
    assert len(env.world.agent.seen) == 1


async def test_any_other_message_voids_the_act_and_runs_as_normal(env: _Env) -> None:
    await _ask(env)
    env.world.agent = _ActAgent(None, None)
    await _post_and_run(_message("What is due today?"))

    assert env.acts.only()["state"] == "void"
    (seen,) = env.world.agent.seen
    assert seen["payload"]["message"] == "What is due today?"
    assert _texts(env)[-1] == "Two tasks are due today."

    await _post_and_run(_message("Confirm"))
    assert _creates(env) == [], "a Confirm after a void wrote"


async def test_a_new_ask_replaces_the_pending_act(env: _Env) -> None:
    await _ask(env)
    await _ask(env, {**CREATE, "title": "Send the quote"})
    await _post_and_run(_message("Confirm"))

    (created,) = _creates(env)
    assert created["json"]["title"] == "Send the quote"
    states = sorted(r["state"] for r in env.acts.rows.values())
    assert states == ["done", "void"]


# ── W5: every other card stays refused ──────────────────────────────────────


def _named(name: str, module: str = "skill_projects.writes") -> Any:
    def fn() -> None:
        return None

    fn.__name__ = name
    fn.__module__ = module
    return fn


async def _card_in_run(name: str, *, module: str = "skill_projects.writes",
                       rows: list[dict] | None = None) -> tuple[Any, Any]:
    with ask_tools.refuse_cards(), wui.whatsapp_run("orchestrator", writes=True) as run, \
            wacts.tool_call(_named(name, module), (), {"task_id": UUID}):
        answer = await ask_tools.request_confirmation(
            title="Do it?", detail="d", context="c", rows=rows)
    return answer, run.parked


def _refused_tools() -> list[str]:
    names = manifest.tools_by_class("C") | manifest.tools_by_class("B")
    return sorted(n for n in names if n and n not in wacts.ALLOWED_ACTS)


@pytest.mark.parametrize("tool", _refused_tools())
async def test_every_class_c_tool_and_every_other_class_b_tool_stays_refused(
    tool: str,
) -> None:
    answer, parked = await _card_in_run(tool)
    assert answer is False and parked is None


async def test_the_allowed_acts_are_class_b_and_one_card_and_a_class_x_route_has_no_tool() -> None:
    assert set(wacts.ALLOWED_ACTS) == {"create_task"}
    assert all(manifest.tool_class(n) == "B" for n in wacts.ALLOWED_ACTS)
    assert not any(r.tool for r in manifest.MANIFEST if r.cls == "X")


async def test_a_rows_card_a_form_and_a_question_stay_refused() -> None:
    answer, parked = await _card_in_run(
        "create_task", rows=[{"id": "r1", "label": "One"}])
    assert answer == frozenset() and parked is None

    answer, parked = await _card_in_run("create_task", module="skill_crm.writes")
    assert answer is False and parked is None, "a create_task of another module"

    with ask_tools.refuse_cards(), wui.whatsapp_run("orchestrator", writes=True):
        assert await ask_tools.ask_questions('{"questions": []}') == ask_tools.NO_CARD_QUESTIONS
    assert "ask_questions" in wui.WITHHELD_TOOLS
    assert "emit_generative_ui" in wui.WITHHELD_TOOLS


async def test_one_run_parks_one_act_and_a_second_card_is_refused() -> None:
    first, parked = await _card_in_run("create_task")
    assert first is False and parked is not None
    with ask_tools.refuse_cards(), wui.whatsapp_run("orchestrator", writes=True) as run:
        for _ in range(2):
            with wacts.tool_call(_named("create_task"), (), {"title": "x"}):
                await ask_tools.request_confirmation(title="t", detail="d", context="c")
        assert run.parked is not None and run.parked.arguments == {"title": "x"}


async def test_the_yes_answers_one_matching_card_only() -> None:
    card = wacts.card_text("t", "d", "c")
    with wacts.confirming(card) as state:
        assert await ask_tools.request_confirmation(title="t", detail="d", context="c") is True
        assert await ask_tools.request_confirmation(title="t", detail="d", context="c") is False
    assert state.used and state.matched
    assert wacts._CONFIRMING.get() is None
    with wacts.confirming(card) as state:
        assert await ask_tools.request_confirmation(title="t", detail="d2", context="c") is False
    assert state.used and not state.matched


async def test_a_call_that_code_cannot_store_never_parks() -> None:
    """Positional arguments, or a value JSON cannot hold, give no act."""
    for args, kwargs in (((UUID,), {}), ((), {"title": object()})):
        with ask_tools.refuse_cards(), wui.whatsapp_run("orchestrator", writes=True) as run, \
                wacts.tool_call(_named("create_task"), args, kwargs):
            answer = await ask_tools.request_confirmation(title="t", detail="d", context="c")
        assert answer is False and run.parked is None


async def test_outside_a_whatsapp_run_the_seam_names_no_call() -> None:
    with wacts.tool_call(_named("create_task"), (), {"title": "x"}):
        assert wacts._CALL.get() is None
    with wui.whatsapp_run("orchestrator"), wacts.tool_call(_named("create_task"), (), {}):
        assert wacts._CALL.get() is None, "a run with writes off named the call"


# ── W6: the model cannot offer Confirm or Cancel ────────────────────────────


@pytest.mark.parametrize("kind,data", [
    ("buttons", {"body": "Add it?", "buttons": ["Confirm", "Later"]}),
    ("buttons", {"body": "Add it?", "buttons": ["  CANCEL "]}),
    ("list", {"body": "Pick", "rows": [{"title": "Confirm"}, {"title": "Other"}]}),
    ("list", {"body": "Pick", "rows": ["cancel"]}),
])
async def test_the_model_cannot_offer_confirm_or_cancel(kind: str, data: dict) -> None:
    with wui.whatsapp_run("orchestrator", writes=True) as run:
        out = await wui.whatsapp_ui(kind, data)
    assert out["ok"] is False and "Confirm" in out["error"]
    assert run.outbox == []


def test_only_code_builds_the_confirm_buttons() -> None:
    msg = wacts.confirm_buttons()
    titles = [b["reply"]["title"] for b in msg.interactive["action"]["buttons"]]
    assert titles == ["Confirm", "Cancel"]
    assert "10 minutes" in wacts.CONFIRM_BODY and acts.ACT_TTL_S == 600


def test_the_writes_rule_allows_adding_a_task_and_sends_every_other_change_away() -> None:
    rule = bot_run.SCOPE_RULE_WRITES
    assert rule != bot_run.SCOPE_RULE_NATIVE
    assert "You cannot change data from WhatsApp" not in rule
    assert "add a task" in rule and "Confirm" in rule
    assert "web app, and add a link button" in rule


# ── W7: with the switch off, the run is the read-only run ───────────────────


@pytest.mark.parametrize("native,writes", [(True, False), (False, True)])
async def test_with_the_switch_off_the_run_is_todays_read_only_run(
    env: _Env, monkeypatch: pytest.MonkeyPatch, native: bool, writes: bool,
) -> None:
    _switches(monkeypatch, native=native, writes=writes)
    assert flags.writes_enabled(ORG_A) is False
    await _ask(env)

    assert _creates(env) == []
    (output,) = env.world.agent.outputs
    assert output.startswith("Cancelled"), "the card was not refused"
    assert env.acts.calls == 0 and env.acts.rows == {}
    assert env.prov.buttons == []
    (seen,) = env.world.agent.seen
    expected = bot_run.SCOPE_RULE_NATIVE if native else bot_run.SCOPE_RULE
    assert seen["payload"]["system_context"] == expected
    await _post_and_run(_message("Confirm"))
    assert _creates(env) == []


# ── W9: the card must match at Confirm ──────────────────────────────────────


async def test_a_confirm_whose_card_changed_writes_nothing_and_asks_again(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The project is renamed between the ask and the Confirm."""
    from skill_projects import choices

    monkeypatch.setattr(choices, "assist_on", lambda: True)

    async def _resolve(rows: list[dict], wanted: str, what: str) -> dict | None:
        return rows[0] if rows else None   # a sure engine: "Ops" is the renamed row

    monkeypatch.setattr(choices, "resolve_name", _resolve)
    await _ask(env, {**CREATE, "project_id": "Ops"})
    assert "Ops" in env.acts.only()["card_text"]

    env.tree[0]["name"] = "Ops West"
    await _post_and_run(_message("Confirm"))

    assert _creates(env) == [], "a changed card wrote"
    row = env.acts.only()
    assert (row["state"], row["code"]) == ("void", "changed")
    assert _texts(env)[-1] == acts.REPLY_CHANGED
    assert len(env.world.agent.seen) == 1


async def test_an_unchanged_card_by_name_confirms(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from skill_projects import choices

    monkeypatch.setattr(choices, "assist_on", lambda: True)
    await _ask(env, {**CREATE, "project_id": "Ops"})
    await _post_and_run(_message("Confirm"))
    assert len(_creates(env)) == 1
    assert env.acts.only()["state"] == "done"


# ── Review round 1 (2026-10-11): a Confirm names the card it answers ───────
#
# P1-a: a tap on an OLDER card's buttons, queued behind the thread lock
# while the run parks a newer card, confirmed the newer card. P1-b: an act
# whose summary and buttons never reached the phone could be confirmed by a
# typed "confirm". Both probes drive the real webhook and ``run_message``.

B = {"project_id": UUID, "title": "Email Bob", "due": "2026-10-13"}


class _RacingAgent(_ActAgent):
    """On its run, the member taps Confirm before the run ends."""

    def __init__(self, *script: Any, tap: dict[str, Any]) -> None:
        super().__init__(*script)
        self.tap: dict[str, Any] | None = tap

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any) -> Any:
        if self.tap is not None:
            tap, self.tap = self.tap, None
            await _post(tap)   # arrives while this run holds the thread lock
        return await super().__call__(agent, payload, **kw)


@pytest.mark.parametrize("on_old_card", [True, False],
                         ids=["tap-on-the-old-buttons", "typed-during-the-run"])
async def test_a_confirm_sent_before_the_new_card_was_shown_never_confirms_it(
    env: _Env, on_old_card: bool,
) -> None:
    """P1-a. Card A waits. "Also add one" voids A, and its run parks B. A
    Confirm that the member sent during that run answers A, never B."""
    await _ask(env)
    old_buttons = env.prov.button_wamids[-1]
    tap = _tap("Confirm", of=old_buttons) if on_old_card else _message("Confirm")
    env.world.agent = _RacingAgent(dict(B), tap=tap)
    await _post_and_run(_message("Also add one: email Bob"))

    assert _creates(env) == [], "a Confirm sent before card B was shown wrote B"
    states = {r["arguments"]["title"]: r["state"] for r in env.acts.rows.values()}
    assert states == {"Call the vendor": "void", "Email Bob": "pending"}
    assert _texts(env)[-1] == acts.REPLY_STALE

    # The member taps Confirm on card B itself: that one writes B.
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))
    (created,) = _creates(env)
    assert created["json"]["title"] == "Email Bob"


async def test_a_tap_on_another_message_leaves_the_act_and_writes_nothing(env: _Env) -> None:
    await _ask(env)
    await _post_and_run(_tap("Cancel", of="wamid.some.other.message"))
    assert env.acts.only()["state"] == "pending"
    assert _texts(env)[-1] == acts.REPLY_STALE
    await _post_and_run(_tap("Cancel", of=env.prov.button_wamids[-1]))
    assert env.acts.only()["state"] == "cancelled"


def _surely_refused() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://graph.facebook.com")
    return httpx.HTTPStatusError("rate", request=req, response=httpx.Response(400, request=req))


async def test_a_card_whose_first_send_failed_is_never_confirmed_after_the_resend(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-b (a). The first part gets a 4xx, so the row waits again. The
    resend sends the stored text only: no summary and no buttons."""
    real, state = env.prov.send_text, {"n": 0}

    async def flaky(to: str, body: str, *, reply_to_wa_message_id: str | None = None) -> str:
        state["n"] += 1
        if state["n"] == 1:
            raise _surely_refused()
        return await real(to, body, reply_to_wa_message_id=reply_to_wa_message_id)

    monkeypatch.setattr(env.prov, "send_text", flaky)
    await _ask(env)
    (row,) = env.world.store.inbound_rows()
    await _later_sweeps(env.world, row, times=1)
    assert env.prov.buttons == [], "the resend showed buttons"

    env.world.agent = _ActAgent(None)
    await _post_and_run(_message("confirm"))
    assert _creates(env) == [], "a card the member never saw was confirmed"
    act = env.acts.only()
    assert (act["state"], act["code"]) == ("void", "unoffered")
    assert len(env.world.agent.seen) == 1, "the confirm did not run as a normal message"


@pytest.mark.parametrize("failure", ["surely", "unsure"])
async def test_buttons_that_did_not_surely_reach_the_phone_offer_nothing(
    env: _Env, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """P1-b (b) and (c): ``send_partial`` after the model's line, and
    ``send_unconfirmed`` on the buttons themselves."""
    async def broken(to: str, interactive: dict, **_kw: Any) -> str:
        if failure == "surely":
            raise _surely_refused()
        raise RuntimeError("read timeout")   # may have reached the phone

    monkeypatch.setattr(env.prov, "send_interactive", broken)
    await _ask(env)
    (row,) = env.world.store.inbound_rows()
    assert (row["state"], row["code"]) == (
        "replied", "send_partial" if failure == "surely" else "send_unconfirmed")

    env.world.agent = _ActAgent(None)
    await _post_and_run(_message("Confirm"))
    assert _creates(env) == []
    assert env.acts.only()["state"] == "void"


# ── Review round 1: writes are per org, and the card binds the ids ─────────


async def test_writes_are_switched_on_per_org(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_writes_orgs", ORG_B,
                        raising=False)
    assert flags.writes_enabled(ORG_B) is True
    assert flags.writes_enabled(ORG_A) is False
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_writes_orgs", "", raising=False)
    assert flags.writes_enabled(ORG_B) is False, "an empty list allows no org"

    monkeypatch.setattr(get_settings(), "whatsapp_assistant_writes_orgs", ORG_B,
                        raising=False)
    await _ask(env)   # the member's org is ORG_A
    assert _creates(env) == [] and env.acts.rows == {} and env.prov.buttons == []
    (output,) = env.world.agent.outputs
    assert output.startswith("Cancelled")
    (seen,) = env.world.agent.seen
    assert seen["payload"]["system_context"] == bot_run.SCOPE_RULE_NATIVE


async def test_a_confirm_after_another_project_took_the_name_writes_nothing(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W9, by name: the card text is the same, but the project is another."""
    from skill_projects import choices

    from tests.unit.test_projects_agent_writes import OTHER

    monkeypatch.setattr(choices, "assist_on", lambda: True)
    env.tree.append({"id": OTHER, "name": "Ops West", "kind": "project"})
    await _ask(env, {**CREATE, "project_id": "Ops"})
    summary_before = env.world.sent[1][1]

    env.tree[0]["name"] = "Ops Old"
    env.tree[1]["name"] = "Ops"
    await _post_and_run(_message("Confirm"))

    assert _creates(env) == [], "the act wrote into another project"
    assert (env.acts.only()["state"], env.acts.only()["code"]) == ("void", "changed")
    assert _texts(env)[-1] == acts.REPLY_CHANGED
    assert UUID not in summary_before, "the member's summary changed shape"
