"""Mailboxes known everywhere, and a mailbox named in words (§7.1.1).

``navigation_shell.md`` §7.1.1 rules 3 and 4. The one assistant names the
member's mailboxes on every surface, and email-assistant resolves the
member's own words for a mailbox.

* ``email-chat-mailbox-by-name``: ``_one_mailbox`` takes "Personal", "my
  personal box", "my gmail" or "vj@…" in ``account_id``. An id wins, then an
  exact label, address or provider, then a unique prefix. Two matches ask,
  and an unknown label asks. An unknown value with the shape of an id, or a
  list that cannot be read, goes on as before.
* ``one-assistant-mailboxes-block``: the orchestrator's per-turn context
  lists the member's mailboxes (label, address, id, default, All inboxes,
  reconnect) on any surface. No other agent gets the block, and a run that
  does not act as its verified member (a shared room) gets none.
* ``email-ws-owned-mailbox``: the working set keeps only a mailbox of the
  member's list, and a label becomes its id.

Run::

    uv run pytest tests/unit/test_email_mailbox_by_name.py -v
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from acb_skills import email_chat_context as ecc

_AGENT = (Path(__file__).resolve().parents[2]
          / "apps" / "agents" / "agent-email-assistant" / "agents.py")


def _load_agents():
    spec = importlib.util.spec_from_file_location("ea_mailbox_by_name", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_agents()

WORK = "0a0a0a0a-0000-4000-8000-00000000000a"
HOME = "0b0b0b0b-0000-4000-8000-00000000000b"
SIDE = "0c0c0c0c-0000-4000-8000-00000000000c"
BOXES = [
    {"id": WORK, "email_address": "vj@hathilabs.test", "label": "Outlook",
     "display_label": "Work", "provider": "microsoft", "is_default": True,
     "in_all_inboxes": True},
    {"id": HOME, "email_address": "vjvarada@gmail.test", "label": "Gmail",
     "display_label": "Personal", "provider": "gmail", "in_all_inboxes": True,
     "needs_reconnect": True},
    {"id": SIDE, "email_address": "vj@sideproject.test", "label": "Outlook",
     "display_label": "Workshop", "provider": "microsoft", "in_all_inboxes": False},
]


def _with(monkeypatch: pytest.MonkeyPatch, boxes: Any) -> None:
    async def accounts() -> list[dict[str, Any]]:
        if isinstance(boxes, Exception):
            raise boxes
        return [dict(b) for b in boxes]

    monkeypatch.setattr(agents, "_accounts", accounts)


@pytest.mark.parametrize(("words", "want"), [
    (WORK, WORK),
    ("Personal", HOME),
    ("personal", HOME),
    ("my personal box", HOME),
    ("the Personal mailbox", HOME),
    ("my gmail", HOME),
    ("vjvarada@gmail.test", HOME),
    ("vjvarada@…", HOME),
    ("Work", WORK),            # exact beats the prefix of "Workshop"
    ("workshop", SIDE),
    ("vj@side", SIDE),
])
def test_a_name_resolves_to_one_mailbox(monkeypatch, words: str, want: str) -> None:
    _with(monkeypatch, BOXES)
    assert asyncio.run(agents._one_mailbox(words, "create_rule")) == (want, "")


@pytest.mark.parametrize("words", ["outlook", "vj@"])
def test_two_matches_ask_and_list_only_them(monkeypatch, words: str) -> None:
    _with(monkeypatch, BOXES)
    aid, ask = asyncio.run(agents._one_mailbox(words, "create_rule"))
    assert aid == ""
    assert ask.startswith("Which mailbox?") and "matches more than one" in ask, ask
    assert "call create_rule again" in ask
    assert "id=" not in ask  # the rule card reads id= as a rule's id


def test_an_unknown_label_asks_and_lists_every_mailbox(monkeypatch) -> None:
    _with(monkeypatch, BOXES)
    aid, ask = asyncio.run(agents._one_mailbox("Billing", "update_assistant_settings"))
    assert aid == "" and "No mailbox is called" in ask
    for b in BOXES:
        assert f"(account_id {b['id']})" in ask


def test_an_id_shaped_value_or_an_unread_list_goes_on_as_before(monkeypatch) -> None:
    _with(monkeypatch, BOXES)
    assert asyncio.run(agents._one_mailbox("acc-unknown-1", "create_rule")) == (
        "acc-unknown-1", "")
    _with(monkeypatch, RuntimeError("down"))
    assert asyncio.run(agents._one_mailbox("Personal", "create_rule")) == ("Personal", "")
    _with(monkeypatch, [])
    assert asyncio.run(agents._one_mailbox("Personal", "create_rule")) == ("Personal", "")


def test_create_rule_on_my_personal_box_acts_in_personal(monkeypatch) -> None:
    """The tool itself, with only the gateway faked."""
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(no_egress=False)
    _with(monkeypatch, BOXES)
    posted: list[tuple[str, dict]] = []

    async def post(path: str, body: dict) -> dict:
        posted.append((path, body))
        return {"id": "rule-1", "name": body.get("name"), "enabled": True}

    async def get(path: str, params: dict | None = None) -> Any:
        return []

    async def approve(**_kw: Any) -> bool:
        return True

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", approve)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    out = asyncio.run(agents.create_rule(
        account_id="my personal box", name="Receipts",
        subject_pattern="receipt", label="Receipts",
    ))
    assert posted, out
    path, body = posted[0]
    assert path == "/email/rules" and body.get("account_id") == HOME, (path, body)
    assert "in Personal · vjvarada@gmail.test" in out, out


# ── the orchestrator's block ────────────────────────────────────────────────


def test_the_block_names_each_mailbox_and_its_state() -> None:
    text = ecc.mailboxes_block(BOXES)
    assert text.startswith("## The member's mailboxes")
    assert f"«Work» «vj@hathilabs.test» (account_id: {WORK}), the default, in All inboxes." in text
    assert f"«Personal» «vjvarada@gmail.test» (account_id: {HOME}), in All inboxes, NEEDS RECONNECTING" in text
    assert "kept separate from All inboxes" in text
    assert "asks which mailbox before a write" in text
    assert ecc.mailboxes_block([]) == ""
    assert "asks which mailbox" not in ecc.mailboxes_block(BOXES[:1])


def _orch_run(monkeypatch, member: str = "vj@hathilabs.test") -> Any:
    from orchestrator import executor

    monkeypatch.setattr(executor, "_email_ctx_member", lambda: member)
    monkeypatch.setattr(executor, "_current_run_org", lambda: "org-a")

    async def fetch(_m: str) -> list[dict]:
        return BOXES

    monkeypatch.setattr(ecc, "fetch_accounts", fetch)
    return executor


def test_the_orchestrator_gets_the_block_on_any_surface(monkeypatch) -> None:
    executor = _orch_run(monkeypatch)
    asyncio.run(executor._prime_email_context("orchestrator"))
    parts = executor._context_preamble_parts(
        {"system_context": "## Where the member is now\nThe member is in the Projects app."},
        {}, "orchestrator")
    block = next(p for p in parts if p.startswith("## The member's mailboxes"))
    assert f"(account_id: {HOME})" in block and "NEEDS RECONNECTING" in block


def test_no_other_agent_and_no_shared_room_gets_the_block(monkeypatch) -> None:
    executor = _orch_run(monkeypatch)
    asyncio.run(executor._prime_email_context("orchestrator"))
    for agent in ("email-assistant", "projects-assistant"):
        parts = executor._context_preamble_parts({}, {}, agent)
        assert not any("mailboxes" in p for p in parts), agent
    monkeypatch.setattr(executor, "_email_ctx_member", lambda: "")
    parts = executor._context_preamble_parts({}, {}, "orchestrator")
    assert not any("mailboxes" in p for p in parts)


def test_a_room_member_is_not_the_acting_member(monkeypatch) -> None:
    """``_email_ctx_member`` gives "" when the tools act as ``room:<thread>``."""
    import acb_common
    from acb_skills import memory_tools
    from orchestrator import executor

    monkeypatch.setattr(acb_common, "get_run_context",
                        lambda: {"user": "vj@hathilabs.test", "member_verified": "1"})
    monkeypatch.setattr(memory_tools, "_get_memory_user_id", lambda: "room:t-1")
    assert executor._email_ctx_member() == ""
    monkeypatch.setattr(memory_tools, "_get_memory_user_id", lambda: "VJ@hathilabs.test")
    assert executor._email_ctx_member() == "vj@hathilabs.test"


def test_a_failed_read_costs_the_block_and_never_the_turn(monkeypatch) -> None:
    executor = _orch_run(monkeypatch)

    async def down(_m: str) -> list[dict]:
        raise ConnectionError("gateway down")

    monkeypatch.setattr(ecc, "fetch_accounts", down)
    asyncio.run(executor._prime_email_context("orchestrator"))
    assert executor._email_context_block("orchestrator") == ""


# ── the working set keeps owned mailboxes only ──────────────────────────────


def test_the_working_set_keeps_only_an_owned_mailbox_and_maps_a_label() -> None:
    call = {"name": "create_rule", "args": {"account_id": "Personal"},
            "result": "Rule created in Personal.", "success": True}
    assert ecc.update_working_set({}, [call], BOXES) == {"account_id": HOME}
    stranger = {**call, "args": {"account_id": "acc-not-mine"}}
    assert ecc.update_working_set({}, [stranger], BOXES) == {}
    asked = {**call, "result": "Which mailbox? No mailbox is called x"}
    assert ecc.update_working_set({}, [asked], BOXES) == {}
