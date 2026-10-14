"""EM-G11c1 (email_app_master_plan.md §12.3.13.6): a card for each chat write.

``MAILBOX_WRITE_TOOLS`` in the email agent names each tool that writes to a
mailbox, saves an automation, turns on a background write, or changes what
the assistant knows. Each one asks a card before its first write. A refusal
writes nothing. A run with no card channel (the email chat pane, which runs
with ``shows_cards=False``, or a scheduled run) writes nothing and answers
``_USE_CHAT_APP``, which sends the member to the Chat app (Q-GM-14,
answered 2026-10-14: cards come to the pane in PR-G3b).

The gateway is faked at ``_request``, the one seam under ``_get``, ``_post``,
``_patch``, ``_delete`` and ``_patch_settings``. So a tool that calls
``_request`` directly is seen too. The card is faked at
``acb_skills.ask_tools.request_confirmation`` (the idiom of
``test_email_rule_confirm.py``), except in the headless test, which runs the
real one.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path
from typing import Any

import pytest

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("ea_write_cards", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

BOX = "acc-1"
BOX_TEXT = "Work · jane@acme.test"
MSG = "0b0b0b0b-0000-4000-8000-00000000000b"
WRITE_METHODS = ("POST", "PATCH", "PUT", "DELETE")

#: The pinned answer of a write tool with no card channel (auditor edit).
CHAT_APP_TEXT = (
    "Nothing changed. This needs your approval on a card, and this chat "
    "cannot show one. Ask for it in the Chat app."
)

#: Each POST that only reads, by path, with the reason. The behavioural
#: fences do not count these as writes.
READ_POSTS = {
    "/email/messages/summaries": "sender and subject of each id, no write",
    "/email/rules/generate/preview": "turns a prompt into rule specs, saves nothing",
    "/email/rules/test": "a dry run of the rules on one email",
}
#: A POST whose body says it is a dry run.
DRY_RUN_POSTS = ("/email/cleanup/auto-categorize", "/email/rules/run")


def _is_write(method: str, path: str, body: Any) -> bool:
    if method not in WRITE_METHODS:
        return False
    if method == "POST" and path in READ_POSTS:
        return False
    if (method == "POST" and path == "/email/draft-reply"
            and isinstance(body, dict) and body.get("create_draft") is False):
        return False  # a draft that only shows in the chat: no provider write
    dry_run = isinstance(body, dict) and body.get("dry_run") is True
    return not (method == "POST" and path in DRY_RUN_POSTS and dry_run)


FORWARD_RULE = {"id": "r2", "name": "Copy", "enabled": True,
                "instructions": "invoices",
                "actions": [{"type": "FORWARD", "to_address": "out@evil.test"}]}


#: Each setting off, as a new mailbox has it.
ALL_OFF: dict[str, Any] = {
    "auto_run": False, "cold_email_blocker": "OFF", "draft_replies": False,
    "follow_up_auto_draft": False, "multi_rule_execution": False,
    "sensitive_data_protection": True, "draft_confidence": "STANDARD",
    "digest_frequency": "OFF", "digest_send_to_email": True,
    "follow_up_awaiting_days": 0, "follow_up_needs_reply_days": 0,
    "about": "", "signature": "", "personal_instructions": "",
    "writing_style": "", "digest_categories": [], "digest_day_of_week": 1,
    "digest_time_of_day": "08:00",
}


class _Resp:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.status_code = 200
        self.content = b"{}"

    def json(self) -> Any:
        return self._data


class Gateway:
    """Records each call and each card. ``answer`` is the reply of the card,
    and ``channel`` says whether a live chat showed it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.answer = False
        self.channel = True
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.provider = "gmail"
        self.settings: dict[str, Any] = dict(ALL_OFF)
        self.specs: list[dict[str, Any]] = [
            {"name": "Bank", "actions": [{"type": "LABEL", "label": "Finance"}]}]
        self.history_actions: list[dict[str, Any]] = [{"type": "ARCHIVE"}]

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if _is_write(*c)]

    def answer_of(self, method: str, path: str, body: Any) -> Any:
        if method == "GET":
            return self._answer_get(path)
        return self._answer_write(path, body)

    def _answer_get(self, path: str) -> Any:
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": "jane@acme.test",
                     "display_label": "Work", "provider": self.provider}]
        if path == "/email/rules":
            return {"rules": [
                {"id": "r1", "name": "Bank", "enabled": True,
                 "actions": [{"type": "LABEL", "label": "Finance"}]},
                {**FORWARD_RULE,
                 "actions": [dict(a) for a in FORWARD_RULE["actions"]]}]}
        if path == "/email/messages":
            return {"emails": [{"id": "m1", "account_id": BOX,
                                "subject": "Quote for June"}]}
        if path == "/email/rules/history":
            return {"history": [{
                "id": "e1", "rule_name": "Bank", "subject": "Invoice 7",
                "from": {"name": "Kim", "email": "kim@contoso.test"},
                "actions": [a["type"] for a in self.history_actions],
                "rule_actions": [dict(a) for a in self.history_actions]}]}
        if path == "/email/learned-patterns":
            return {"patterns": [{"id": "p1", "pattern": "Short replies"}]}
        if path == "/email/rules/patterns":
            return {"patterns": [{"id": "p1", "rule_name": "Bank",
                                  "pattern_type": "from",
                                  "value": "bank.test", "exclude": False}]}
        if path == "/email/assistant/settings":
            return dict(self.settings)
        if path == "/email/knowledge":
            return {"entries": [{"id": "k1", "title": "Prices", "content": "c"}]}
        if path == "/email/unsubscribe/target":
            return {"kind": "one-click", "link": "https://list.test/u"}
        if path == "/email/digest":
            return {"sections": []}
        if path.startswith("/email/messages/"):
            return {"account_id": BOX, "folder": "drafts", "subject": "Hi",
                    "from_address": {"email": "kim@contoso.test"},
                    "to_addresses": [{"email": "kim@contoso.test"}],
                    "cc_addresses": [], "bcc_addresses": [], "attachments": []}
        return {}

    def _answer_write(self, path: str, body: Any) -> Any:
        if path == "/email/messages/summaries":
            return {"summaries": [
                {"id": i, "from": "Kim", "subject": f"Offer {n}"}
                for n, i in enumerate(body["ids"])]}
        if path == "/email/cleanup/auto-categorize":
            return {"categorized": 3, "scanned": 10,
                    "by_category": {"Newsletter": 3}}
        if path == "/email/rules/generate/preview":
            return {"specs": [dict(s) for s in self.specs]}
        if path == "/email/rules/test":
            return {"matched": False, "reason": "No rule matched."}
        if path == "/email/follow-ups/scan":
            return {"configured": True, "scanned": 1, "labeled": 1}
        return {"id": "new-1", "affected": 3, "installed": ["To Reply"],
                "created": [{"id": "new-1", "name": "Bank"}], "count": 1,
                "writing_style": "Short.", "draft": "Hi", "actions": [],
                "reversed": [], "messages_synced": 1}


def _recording_request(g: Gateway) -> Any:
    async def fake_request(method: str, path: str, **kwargs: Any) -> _Resp:
        body = kwargs.get("json")
        if method == "GET":
            g.gets.append((path, dict(kwargs.get("params") or {})))
        g.calls.append((method, path, body))
        return _Resp(g.answer_of(method, path, body))
    return fake_request


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    g = Gateway()

    fake_request = _recording_request(g)

    async def card(**kw: Any) -> bool:
        g.cards.append(kw)
        return g.answer

    monkeypatch.setattr(agents, "_request", fake_request)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open",
                        lambda: g.channel)
    return g


#: For each registry tool, the arguments of a call that writes.
WRITE_CASES: dict[str, dict[str, Any]] = {
    "send_email": {"account_id": BOX, "body": "Hi", "to": ["kim@contoso.test"],
                   "subject": "S"},
    "forward_email": {"email_id": MSG, "to": ["lee@contoso.test"]},
    "send_draft": {"account_id": BOX, "draft_id": "d1"},
    "unsubscribe_sender": {"account_id": BOX, "email": "news@list.test"},
    "digest": {"account_id": BOX, "send": True},
    "sync_account": {"account_id": BOX, "purge": True},
    "install_default_rules": {"account_id": BOX},
    "create_rule": {"account_id": BOX, "name": "News", "action_type": "ARCHIVE"},
    "update_rule": {"account_id": BOX, "rule_id": "r1", "instructions": "bank mail"},
    "create_rules_from_prompt": {"account_id": BOX, "prompt": "label bank mail"},
    "manage_inbox": {"action": "archive", "message_ids": ["m1", "m2", "m3"]},
    "draft_reply": {"email_id": "m1", "account_id": BOX, "save": True},
    "auto_categorize_inbox": {"account_id": BOX, "apply": True},
    "delete_rule": {"account_id": BOX, "rule_id": "r1"},
    "run_rules": {"account_id": BOX, "dry_run": False},
    "learn_rule_pattern": {"account_id": BOX, "rule_id": "r1", "sender": "bank.test"},
    "update_assistant_settings": {"account_id": BOX, "auto_run": True},
    "create_label": {"account_id": BOX, "name": "Finance"},
    "find_follow_ups": {"account_id": BOX},
    "mark_thread_done": {"account_id": BOX, "thread_id": "t1"},
    "reclassify_reply_zero": {"account_id": BOX},
    "set_sender_status": {"account_id": BOX, "email": "x@y.test", "status": "cold"},
    "resolve_execution": {"execution_id": "e1", "decision": "approve"},
    "save_knowledge": {"account_id": BOX, "title": "Prices", "content": "c"},
    "delete_knowledge": {"account_id": BOX, "knowledge_id": "k1"},
    "generate_writing_style": {"account_id": BOX},
    "forget_pattern": {"pattern_id": "p1"},
}
REGISTRY = sorted(agents.MAILBOX_WRITE_TOOLS)


def test_each_registry_tool_has_a_write_case() -> None:
    assert set(WRITE_CASES) == set(agents.MAILBOX_WRITE_TOOLS)


def test_each_registry_tool_is_a_registered_tool() -> None:
    tools = agents._register_agent_tools()
    assert set(agents.MAILBOX_WRITE_TOOLS) <= set(tools)
    assert set(agents.GATEWAY_READ_POSTS) <= set(tools)
    assert not set(agents.MAILBOX_WRITE_TOOLS) & set(agents.GATEWAY_READ_POSTS)


# ── A card before each write ─────────────────────────────────────────────────

@pytest.mark.parametrize("tool", REGISTRY)
async def test_each_registry_tool_asks_a_card_before_it_writes(
    gw: Gateway, tool: str,
) -> None:
    gw.answer = False
    out = await getattr(agents, tool)(**WRITE_CASES[tool])
    assert len(gw.cards) == 1, (tool, out)
    assert gw.writes == [], tool
    assert "ancelled" in out, (tool, out)


@pytest.mark.parametrize("tool", REGISTRY)
async def test_a_yes_lets_each_registry_tool_write(gw: Gateway, tool: str) -> None:
    """The write cases reach a write: so the refusal test above is not empty."""
    gw.answer = True
    out = await getattr(agents, tool)(**WRITE_CASES[tool])
    assert len(gw.cards) == 1, (tool, out)
    assert gw.writes, (tool, out)


# ── No card channel: the email chat pane and a scheduled run ─────────────────

def test_the_chat_app_text_is_pinned() -> None:
    assert agents._USE_CHAT_APP == CHAT_APP_TEXT
    assert agents._NO_CARD_CHANNEL == CHAT_APP_TEXT
    # ``noActionOf`` in EmailToolCards.tsx draws this lead as "did not act".
    assert CHAT_APP_TEXT.startswith("Nothing changed.")
    assert "ancelled" not in CHAT_APP_TEXT


#: The tools that still work with no card channel: they write nothing and
#: answer the result, not the Chat-app text (fix round 1, P1-a).
PANE_FALLBACK = {
    "draft_reply": "the draft shows in the chat, and the user saves or sends "
                   "it from the composer",
}


@pytest.mark.parametrize("pane", [True, False], ids=["email-pane", "scheduled"])
@pytest.mark.parametrize("tool", REGISTRY)
async def test_a_headless_caller_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, gw: Gateway, tool: str, pane: bool,
) -> None:
    """The REAL ``request_confirmation`` and channel probe. ``refuse_cards`` is
    what the email chat pane binds (``shows_cards=False``). A run outside it
    with no run queue and no relay is a scheduled run."""
    from acb_skills import ask_tools

    monkeypatch.undo()
    monkeypatch.setattr(agents, "_request", _recording_request(gw))
    if pane:
        with ask_tools.refuse_cards():
            out = await getattr(agents, tool)(**WRITE_CASES[tool])
    else:
        out = await getattr(agents, tool)(**WRITE_CASES[tool])
    assert gw.writes == [], tool
    if tool in PANE_FALLBACK:
        assert out != CHAT_APP_TEXT, tool
    else:
        assert out == CHAT_APP_TEXT, (tool, out)


async def test_a_draft_in_the_email_pane_shows_and_saves_nothing(
    monkeypatch: pytest.MonkeyPatch, gw: Gateway,
) -> None:
    """P1-a: drafting works in the pane. The draft comes back, no card shows,
    and no provider draft is made. The first line says it is not saved."""
    from acb_skills import ask_tools

    monkeypatch.undo()
    monkeypatch.setattr(agents, "_request", _recording_request(gw))
    with ask_tools.refuse_cards():
        out = await agents.draft_reply("m1", BOX, save=True)
    head, _, body = out.partition("\n\n")
    assert head.startswith(f"Draft from {BOX_TEXT} (mailbox {BOX})")
    assert "not saved to Drafts" in head and "composer" in head
    assert body == "Hi"
    [(method, path, sent)] = [c for c in gw.calls if c[0] != "GET"]
    assert (method, path) == ("POST", "/email/draft-reply")
    assert sent["create_draft"] is False
    assert gw.writes == []


async def test_a_draft_in_a_live_chat_still_asks_before_it_saves(gw: Gateway) -> None:
    out = await agents.draft_reply("m1", BOX, save=True)
    assert len(gw.cards) == 1 and gw.writes == []
    assert out.startswith("Cancelled")
    gw.answer = True
    await agents.draft_reply("m1", BOX, save=True)
    [(_m, _p, sent)] = gw.writes
    assert sent["create_draft"] is True


# ── What needs no card ───────────────────────────────────────────────────────

NO_CARD_CASES: list[tuple[str, dict[str, Any]]] = [
    ("run_rules", {"account_id": BOX, "dry_run": True}),
    ("auto_categorize_inbox", {"account_id": BOX, "apply": False}),
    ("draft_reply", {"email_id": "m1", "account_id": BOX}),
    ("digest", {"account_id": BOX}),
    ("sync_account", {"account_id": BOX}),
    ("test_rule_match", {"account_id": BOX, "subject": "Invoice"}),
    ("present_email_groups", {"groups_json": '[{"title": "A", "email_ids": ["m1"]}]'}),
    ("categorize_senders", {"account_id": BOX}),
    ("update_rule", {"account_id": BOX, "rule_id": "r1", "enabled": False}),
]


@pytest.mark.parametrize(("tool", "kwargs"), NO_CARD_CASES,
                         ids=[f"{t}-{i}" for i, (t, _) in enumerate(NO_CARD_CASES)])
async def test_a_dry_run_needs_no_card(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    await getattr(agents, tool)(**kwargs)
    assert gw.cards == [], tool
    assert gw.calls, tool


async def test_a_dry_run_of_the_rules_writes_nothing(gw: Gateway) -> None:
    await agents.run_rules(BOX, dry_run=True)
    assert gw.writes == []
    await agents.auto_categorize_inbox(BOX, apply=False)
    assert gw.writes == []


# ── Each branch: the card comes before the write (fix round 1, fence gap) ────

#: A branch of a registry tool that writes with no card, and the reason.
#: Each other call of a registry tool must ask a card before its first write.
NO_CARD_BRANCHES: dict[tuple[str, str, Any], str] = {
    ("sync_account", "purge", False): (
        "it pulls a copy from the provider, and changes no mail, rule or "
        "setting"),
}
#: The values of each choice that a registry tool reads as text.
CHOICES: dict[tuple[str, str], list[Any]] = {
    ("manage_inbox", "action"): ["archive", "trash", "read", "unread", "star",
                                 "unstar", "move", "label"],
    ("run_rules", "scope"): ["new", "past"],
    ("set_sender_status", "status"): ["cold", "not_cold", "keep"],
    ("resolve_execution", "decision"): ["approve", "reject", "undo"],
    ("forget_pattern", "kind"): ["draft", "rule"],
    ("digest", "period"): ["day", "week"],
}
_EXTRA = {
    ("manage_inbox", "move"): {"folder": "Archive"},
    ("manage_inbox", "label"): {"add_labels": ["Finance"]},
}


def _branch_cases() -> list[tuple[str, str, Any, dict[str, Any]]]:
    """Each registry tool, called with each bool flipped both ways and each
    choice of ``CHOICES``. ``update_assistant_settings`` has its own sweep."""
    cases: list[tuple[str, str, Any, dict[str, Any]]] = []
    for tool in REGISTRY:
        if tool == "update_assistant_settings":
            continue
        base = WRITE_CASES[tool]
        for name, param in inspect.signature(getattr(agents, tool)).parameters.items():
            if isinstance(param.default, bool):
                for value in (True, False):
                    cases.append((tool, name, value, {**base, name: value}))
            for value in CHOICES.get((tool, name), []):
                extra = _EXTRA.get((tool, value), {})
                cases.append((tool, name, value, {**base, name: value, **extra}))
    return cases


BRANCH_CASES = _branch_cases()


@pytest.mark.parametrize(("tool", "name", "value", "kwargs"), BRANCH_CASES,
                         ids=[f"{t}-{n}={v}" for t, n, v, _ in BRANCH_CASES])
async def test_each_branch_asks_before_its_first_write(
    gw: Gateway, tool: str, name: str, value: Any, kwargs: dict[str, Any],
) -> None:
    """A refused card leaves no write, on every bool and every choice."""
    gw.answer = False
    await getattr(agents, tool)(**kwargs)
    if (tool, name, value) in NO_CARD_BRANCHES:
        assert gw.cards == [] and gw.writes, "a stale NO_CARD_BRANCHES entry"
        return
    assert gw.writes == [], (tool, name, value)


def test_the_branch_sweep_covers_each_choice_param() -> None:
    """A text parameter with a fixed set of values is in ``CHOICES``."""
    for tool, name in CHOICES:
        assert name in inspect.signature(getattr(agents, tool)).parameters


# ── update_assistant_settings (scope 5, fix round 1) ─────────────────────────

#: For each parameter, a value that turns it on or replaces its text.
SETTING_ON: dict[str, Any] = {
    "about": "I run sales.", "signature": "Jane", "auto_run": True,
    "cold_email_blocker": "LABEL", "personal_instructions": "Never commit.",
    "writing_style": "Short.", "draft_replies": True,
    "draft_confidence": "ALL_EMAILS", "follow_up_awaiting_days": 3,
    "follow_up_needs_reply_days": 2, "follow_up_auto_draft": True,
    "digest_frequency": "DAILY", "digest_categories": ["Newsletter"],
    "digest_day_of_week": 3, "digest_time_of_day": "09:30",
    "digest_send_to_email": True, "multi_rule_execution": True,
    "sensitive_data_protection": False,
}
#: The settings that ask no card even when they change, and why.
SETTING_NO_CARD: dict[str, str] = {
    "digest_categories": "it chooses what a digest that is on holds",
    "digest_day_of_week": "it moves when a digest that is on goes out",
    "digest_time_of_day": "it moves when a digest that is on goes out",
    "digest_send_to_email": "the digest frequency is OFF, so no mail goes out",
}


def test_each_setting_has_a_value_or_a_reason() -> None:
    params = set(inspect.signature(agents.update_assistant_settings).parameters)
    assert params - {"account_id"} == set(SETTING_ON)


@pytest.mark.parametrize("key", sorted(SETTING_ON))
async def test_each_setting_that_turns_on_asks(gw: Gateway, key: str) -> None:
    """Fix round 1 (P1-d): from a mailbox with each setting off, each change
    asks a card, unless ``SETTING_NO_CARD`` gives the reason."""
    gw.answer = False
    out = await agents.update_assistant_settings(BOX, **{key: SETTING_ON[key]})
    if key in SETTING_NO_CARD:
        assert gw.cards == [], key
        return
    assert len(gw.cards) == 1, key
    assert gw.writes == [], key
    assert out.startswith("Cancelled"), out


TURN_ON = [
    {"digest_frequency": "DAILY"},
    {"digest_frequency": "WEEKLY", "digest_send_to_email": True},
    {"signature": "Jane"},
    {"about": "I run sales."},
    {"personal_instructions": "Never commit."},
    {"writing_style": "Short."},
    {"sensitive_data_protection": False},
    {"multi_rule_execution": True},
    {"draft_confidence": "HIGH_CONFIDENCE"},
    {"cold_email_blocker": "ARCHIVE"},
]


@pytest.mark.parametrize("change", TURN_ON, ids=lambda c: "-".join(f"{k}" for k in c))
async def test_turning_a_write_on_asks(gw: Gateway, change: dict[str, Any]) -> None:
    gw.answer = False
    out = await agents.update_assistant_settings(BOX, **change)
    assert len(gw.cards) == 1
    assert gw.writes == []
    assert out.startswith("Cancelled")


async def test_turning_on_the_digest_mail_asks_when_the_digest_is_on(gw: Gateway) -> None:
    gw.settings.update(digest_frequency="DAILY", digest_send_to_email=False)
    gw.answer = False
    await agents.update_assistant_settings(BOX, digest_send_to_email=True)
    assert len(gw.cards) == 1 and gw.writes == []


#: Each setting ON, as a mailbox that the member set up has it.
ALL_ON: dict[str, Any] = {
    **SETTING_ON, "cold_email_blocker": "LABEL", "draft_confidence": "STANDARD",
}
NO_TURN_ON = [
    {"auto_run": False},
    {"cold_email_blocker": "OFF"},
    {"draft_replies": False},
    {"follow_up_auto_draft": False},
    {"multi_rule_execution": False},
    {"sensitive_data_protection": True},
    {"digest_frequency": "OFF"},
    {"digest_send_to_email": False},
    {"follow_up_awaiting_days": 0},
    {"signature": ""},
    {"signature": "Jane"},  # the text of now: no change
    {"digest_frequency": "DAILY"},  # the frequency of now: no change
]


@pytest.mark.parametrize("change", NO_TURN_ON,
                         ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()))
async def test_turning_a_write_off_does_not_ask(
    gw: Gateway, change: dict[str, Any],
) -> None:
    gw.settings.update(ALL_ON)
    await agents.update_assistant_settings(BOX, **change)
    assert gw.cards == []
    [(method, path, body)] = gw.writes
    assert (method, path) == ("PUT", "/email/assistant/settings")
    for key, value in change.items():
        assert body[key] == value


async def test_turning_a_write_on_asks_and_turning_it_off_does_not(gw: Gateway) -> None:
    """The fence name of the spec: one call of each kind, in one run."""
    gw.answer = True
    await agents.update_assistant_settings(BOX, auto_run=True)
    assert len(gw.cards) == 1
    gw.settings["auto_run"] = True
    await agents.update_assistant_settings(BOX, auto_run=False)
    assert len(gw.cards) == 1
    assert [w[1] for w in gw.writes] == ["/email/assistant/settings"] * 2


async def test_a_switch_that_is_on_already_does_not_ask(gw: Gateway) -> None:
    gw.settings["auto_run"] = True
    await agents.update_assistant_settings(BOX, auto_run=True)
    assert gw.cards == []


async def test_the_settings_card_shows_the_new_text_plainly(gw: Gateway) -> None:
    await agents.update_assistant_settings(
        BOX, signature="Jane‮​ Doe", personal_instructions="Never commit.")
    [card] = gw.cards
    assert "- The signature becomes: Jane Doe." in card["context"]
    assert "‮" not in card["context"] and "​" not in card["context"]
    assert "Never commit." in card["context"]


# ── What the card says (acceptance) ──────────────────────────────────────────

async def test_an_archive_card_names_the_act_the_count_and_the_mailbox(
    gw: Gateway,
) -> None:
    """"Archive the three newest mails from X": one card, "no" changes
    nothing, "yes" archives the three."""
    out = await agents.manage_inbox("archive", ["m1", "m2", "m3"])
    [card] = gw.cards
    assert card["title"] == f"Archive 3 messages in {BOX_TEXT}?"
    assert card["context"].splitlines() == [
        "Each message:", "- Kim: Offer 0", "- Kim: Offer 1", "- Kim: Offer 2"]
    assert gw.writes == [] and out.startswith("Cancelled")
    gw.answer = True
    await agents.manage_inbox("archive", ["m1", "m2", "m3"])
    assert gw.writes == [("POST", "/email/messages/bulk",
                          {"action": "archive", "message_ids": ["m1", "m2", "m3"]})]


async def test_a_model_rule_that_archives_shows_a_card_before_it_saves(
    gw: Gateway,
) -> None:
    gw.answer = True
    await agents.create_rule(BOX, name="News", action_type="LABEL",
                             label="Newsletter", second_action_type="ARCHIVE")
    [card] = gw.cards
    assert card["title"] == 'Save the rule "News" that labels "Newsletter" and archives?'
    assert BOX_TEXT in card["detail"]
    assert [w[1] for w in gw.writes] == ["/email/rules"]


async def test_one_call_over_many_messages_asks_once(gw: Gateway) -> None:
    ids = [f"m{i}" for i in range(30)]
    await agents.manage_inbox("label", ids, add_labels=["Finance"])
    [card] = gw.cards
    assert card["title"].startswith("Change the labels of 30 messages")
    assert card["context"].splitlines()[-1] == "+10 more"


# ── The static fence: each gateway write tool is in the registry ─────────────

_WRITE_HELPERS = frozenset({"_post", "_patch", "_delete", "_patch_settings"})
_READ_HELPERS = frozenset({"_get"})


def _gateway_write_tools(source: str) -> set[str]:
    """Each tool of ``_TOOLS`` whose body reaches a gateway POST, PATCH, PUT or
    DELETE, through the module's own functions.

    A call of ``_post``, ``_patch``, ``_delete`` or ``_patch_settings`` is a
    write. A call of ``_request`` is a write unless its first argument is the
    literal ``"GET"``. A call of another function of the module is followed.
    """
    tree = ast.parse(source)
    funcs = {
        n.name: n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    tools: list[str] = []
    for n in tree.body:
        if (isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_TOOLS" for t in n.targets)):
            tools = [e.id for e in n.value.elts if isinstance(e, ast.Name)]

    memo: dict[str, bool] = {}

    def writes(name: str, seen: frozenset[str]) -> bool:
        if name in memo:
            return memo[name]
        hit = False
        for node in ast.walk(funcs[name]):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            callee = node.func.id
            if callee in _WRITE_HELPERS:
                hit = True
            elif callee == "_request":
                first = node.args[0] if node.args else None
                if not (isinstance(first, ast.Constant) and first.value == "GET"):
                    hit = True
            elif (callee in funcs and callee not in _READ_HELPERS
                    and callee not in seen):
                hit = writes(callee, seen | {callee})
            if hit:
                break
        memo[name] = hit
        return hit

    return {t for t in tools if t in funcs and writes(t, frozenset({t}))}


def test_each_gateway_write_tool_is_in_the_registry() -> None:
    found = _gateway_write_tools(_AGENT.read_text(encoding="utf-8"))
    allowed = set(agents.MAILBOX_WRITE_TOOLS) | set(agents.GATEWAY_READ_POSTS)
    assert found - allowed == set(), (
        "a tool writes through the gateway with no card. Add it to "
        "MAILBOX_WRITE_TOOLS with a card, or to GATEWAY_READ_POSTS with a reason")
    # No stale entry: each listed tool still reaches the gateway.
    assert allowed - found == set()
    for tool, reason in agents.GATEWAY_READ_POSTS.items():
        assert len(reason) > 40, tool


def test_the_static_fence_sees_a_new_write_tool() -> None:
    """The scan is not blind: a synthetic tool that writes through a helper,
    or through ``_request`` with a verb, is found. A read is not."""
    source = '''
async def _helper(x):
    return await _post("/email/x", {})

async def reads():
    return await _get("/email/y")

async def via_helper():
    return await _helper(1)

async def via_request():
    return await _request("DELETE", "/email/z")

async def via_get_request():
    return await _request("GET", "/email/z")

_TOOLS = [reads, via_helper, via_request, via_get_request]
'''
    assert _gateway_write_tools(source) == {"via_helper", "via_request"}


# ── Fix round 1: each rule card names each rule and each action (P1-b) ───────

async def test_a_forward_with_trash_shows_the_trash(gw: Gateway) -> None:
    gw.answer = False
    await agents.create_rule(BOX, name="Copy", action_type="FORWARD",
                             forward_to="out@evil.test", second_action_type="TRASH")
    [card] = gw.cards
    assert '- rule "Copy": forwards and moves to Trash' in card["context"].splitlines()
    assert gw.writes == []


async def test_a_mixed_batch_shows_each_rule_on_its_one_card(gw: Gateway) -> None:
    gw.specs = [
        {"name": "Copy", "actions": [{"type": "FORWARD", "to_address": "out@evil.test"}]},
        {"name": "Junk", "actions": [{"type": "TRASH"}]},
    ]
    gw.answer = False
    await agents.create_rules_from_prompt(BOX, prompt="forward and trash")
    [card] = gw.cards
    lines = card["context"].splitlines()
    assert '- rule "Copy": forwards' in lines
    assert '- rule "Junk": moves to Trash' in lines
    assert gw.writes == []


async def test_an_update_that_adds_trash_to_a_forward_rule_shows_the_trash(
    gw: Gateway,
) -> None:
    gw.answer = False
    await agents.update_rule(BOX, "r2", add_action_type="TRASH")
    [card] = gw.cards
    assert ('- rule "Copy": forwards and moves to Trash (when: invoices)'
            in card["context"].splitlines())
    assert gw.writes == []


# ── Fix round 1: the default rules, as the route installs them (P1-c) ────────

async def test_the_default_rules_card_names_each_rule_and_action(gw: Gateway) -> None:
    gw.settings["draft_replies"] = True
    gw.answer = False
    await agents.install_default_rules(BOX)
    [card] = gw.cards
    lines = card["context"].splitlines()
    assert card["title"].startswith("Install 10 default rule(s)")
    assert len(lines) == 10
    assert lines[0].startswith('- rule "Needs Reply": labels "Needs Reply" and drafts an email')
    assert any(x.startswith('- rule "Marketing": labels "Marketing" and archives')
               for x in lines)
    assert any(x.startswith('- rule "Cold Email": labels "Cold Email" and archives')
               for x in lines)


async def test_an_outlook_default_rule_moves_to_its_folder(gw: Gateway) -> None:
    gw.provider = "microsoft"
    await agents.install_default_rules(BOX, reset=True)
    [card] = gw.cards
    assert any(
        x.startswith('- rule "Newsletter": labels "Newsletter" and moves to the '
                     'folder "Newsletter"') for x in card["context"].splitlines())


@pytest.mark.parametrize("provider", ["gmail", "microsoft", "imap", ""])
@pytest.mark.parametrize("drafts", [True, False])
def test_the_preset_mirror_matches_the_route(provider: str, drafts: bool) -> None:
    """The agent's ``_PRESETS`` give the same rules as ``_PRESET_RULES``."""
    from gateway.routes.email.automation import rules

    want = [
        {"name": p["name"], "instructions": p["instructions"],
         "actions": rules._actions_for_preset(p, provider, draft_replies=drafts)}
        for p in rules._PRESET_RULES
    ]
    assert agents._preset_rules(provider, draft_replies=drafts) == want


# ── Fix round 1: cards that name their object (P2-b, P2-c) ───────────────────

async def test_a_done_card_names_the_thread_and_its_tasks(gw: Gateway) -> None:
    await agents.mark_thread_done(BOX, "t1")
    [card] = gw.cards
    assert card["title"] == f'Mark the thread "Quote for June" done in {BOX_TEXT}?'
    assert "closes each open task" in card["detail"]


async def test_an_approve_card_names_the_email_the_mailbox_and_a_webhook(
    gw: Gateway,
) -> None:
    gw.history_actions = [{"type": "LABEL", "label": "Finance"},
                          {"type": "CALL_WEBHOOK", "url": "https://hook.test/in"}]
    await agents.resolve_execution("e1", "approve")
    [card] = gw.cards
    assert "It calls a webhook: host hook.test." in card["detail"]
    assert BOX_TEXT in card["detail"]
    assert "- email: Invoice 7" in card["context"]
    assert '- actions: labels "Finance" and calls a webhook' in card["context"]


async def test_an_unknown_run_or_pattern_writes_nothing_and_asks_nothing(
    gw: Gateway,
) -> None:
    out = await agents.resolve_execution("nope", "approve")
    assert out.startswith("Nothing changed.")
    out = await agents.forget_pattern("nope")
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


@pytest.mark.parametrize(("kind", "shown"), [
    ("draft", 'pattern "Short replies"'),
    ("rule", 'from "bank.test": always match the rule "Bank"'),
])
async def test_a_forget_card_names_the_pattern(gw: Gateway, kind: str, shown: str) -> None:
    await agents.forget_pattern("p1", kind=kind)
    [card] = gw.cards
    assert shown in card["detail"]


async def test_the_knowledge_card_shows_no_hidden_text(gw: Gateway) -> None:
    await agents.save_knowledge(BOX, title="Prices", content="Net‮30​ days")
    [card] = gw.cards
    assert card["context"] == "Net30 days"


# ── Fix round 1: a read for a card marks nothing read (P3-b) ─────────────────

@pytest.mark.parametrize(("tool", "kwargs"), [
    ("draft_reply", {"email_id": "m1", "account_id": BOX, "save": True}),
    ("send_email", {"body": "Hi", "reply_to_email_id": "m1"}),
    ("forward_email", {"email_id": MSG, "to": ["lee@contoso.test"]}),
])
async def test_a_read_for_a_card_marks_nothing_read(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    await getattr(agents, tool)(**kwargs)
    reads = [params for path, params in gw.gets
             if path.startswith("/email/messages/")]
    assert reads and all(p.get("mark_read") == "false" for p in reads), reads
