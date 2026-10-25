"""The member's "always copy" rules (owner, 2026-10-24, ``email_app_master_plan.md`` §21.16).

"Always Cc Kiran and Veena on mail to jainuniversity.ac.in." A rule ADDS its
colleagues to the member's own reply card and new-mail card, as rows that
start ticked and that the member can untick. It never sends on its own.

The rules this file holds (R7), each with the mutation that turns it red:

* Only an ``org`` or a ``sent`` colleague can be on the list — drop the class
  check of ``put_always_copy`` and a stranger is saved.
* A card checks the class again — drop the filter of ``always_copy_people``
  and a colleague who left still goes on the card.
* A held chat never changes the list — drop the ``always_copy`` row of
  ``HELD_SETTING_DECISIONS`` and the held tool writes it.

The R8 half is ``test_email_always_copy_r8.py``. No test sends a mail.
"""
from __future__ import annotations

import types
from typing import Any

import pytest
from acb_skills import egress as eg
from acb_skills import held_card as H
from fastapi import HTTPException
from gateway.routes.email.automation import always_copy as ac
from gateway.routes.email.automation import held_card as hc

from tests.unit import _held_card_fakes as F
from tests.unit.test_recipient_intent import (
    KIRAN,
    MEMBER,
    SAGAR,
    USER,
    VEENA,
    _Db,
    _World,
)

JAIN = "jainuniversity.ac.in"
STRANGER = "spy@evil.test"
BOX = "0a0a0a0a-0000-4000-8000-00000000000a"


# ── 1. The rules ─────────────────────────────────────────────────────────────


def test_a_rule_takes_a_domain_or_an_address_and_known_shapes_only() -> None:
    rules = ac.clean_rules([{"match": "@JainUniversity.ac.in", "cc": [KIRAN, KIRAN.upper()]},
                            {"match": SAGAR, "cc": [VEENA]}])
    assert rules == [{"match": JAIN, "cc": [KIRAN]}, {"match": SAGAR, "cc": [VEENA]}]
    for bad in ([{"match": "*.ac.in", "cc": [KIRAN]}], [{"match": JAIN, "cc": []}],
                [{"match": JAIN, "cc": ["not an address"]}],
                [{"match": JAIN, "cc": [f"p{i}@acme.test" for i in range(6)]}]):
        with pytest.raises(ValueError):
            ac.clean_rules(bad)


@pytest.mark.parametrize("person, hit", [
    (SAGAR, True), ("dean@cs.jainuniversity.ac.in", True),
    ("x@evil-jainuniversity.ac.in", False), ("x@jainuniversity.ac.in.evil.test", False),
])
def test_a_domain_matches_itself_and_its_subdomains_only(person: str, hit: bool) -> None:
    assert ac.matches(JAIN, person) is hit


def test_the_people_of_a_rule_come_once_and_skip_who_is_on_the_mail() -> None:
    rules = [{"match": JAIN, "cc": [KIRAN, VEENA]}, {"match": SAGAR, "cc": [KIRAN]}]
    assert ac.match_people(rules, [SAGAR], skip=[VEENA]) == [(KIRAN, JAIN)]
    assert ac.match_people(rules, ["kim@contoso.test"]) == []


# ── 2. The routes ────────────────────────────────────────────────────────────


class _SettingsDb(_Db):
    """The recipient-intent fake, with the always-copy column."""

    def __init__(self) -> None:
        super().__init__()
        self.rules: list[dict[str, Any]] = [{"match": JAIN, "cc": [KIRAN, VEENA]}]
        self.written: list[dict[str, Any]] = []
        self.order: list[str] = []

    async def execute(self, sql: Any, params: dict[str, Any] | None = None) -> Any:
        q = str(sql)
        if q == ac.LOCK_SQL:
            self.order.append("lock")
            return types.SimpleNamespace(fetchone=lambda: None)
        if q == ac.READ_FOR_UPDATE_SQL:
            self.order.append("read")
        if q in (ac.READ_SQL, ac.READ_FOR_UPDATE_SQL):
            row = types.SimpleNamespace(always_copy=list(self.rules))
            return types.SimpleNamespace(fetchone=lambda: row)
        if q == ac.WRITE_SQL:
            self.written.append(dict(params or {}))
            return types.SimpleNamespace(fetchone=lambda: None)
        return await super().execute(sql, params)


@pytest.fixture()
def routes(monkeypatch) -> _SettingsDb:
    import contextlib

    db = _SettingsDb()

    @contextlib.asynccontextmanager
    async def session():
        yield db

    async def owner_ok(*_a: Any, **_k: Any) -> None:
        return None

    # The first settings row keeps what auto-run and the reply drafts read now.
    from gateway.routes.email.automation import rules as rules_mod

    async def _on(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(rules_mod, "auto_run_on", _on)
    monkeypatch.setattr(rules_mod, "reply_rule_drafts", _on)

    async def me(_db: Any, _aid: str) -> Any:
        return types.SimpleNamespace(self_addresses=frozenset({MEMBER}), address=MEMBER,
                                     label="Work")

    monkeypatch.setattr(ac, "_tenant_session", session)
    monkeypatch.setattr(ac, "_assert_account_owner", owner_ok)
    monkeypatch.setattr("gateway.routes.email.automation.identity.resolve_self", me)
    return db


async def test_the_put_saves_a_colleague(routes: _SettingsDb) -> None:
    out = await ac.put_always_copy(ac.AlwaysCopyPut(
        account_id=BOX, rules=[ac.AlwaysCopyRule(match=JAIN, cc=[KIRAN])],
        expected=ac.version_of(routes.rules)), USER)
    assert out["rules"] == [{"match": JAIN, "cc": [KIRAN]}]
    assert out["version"] == ac.version_of(out["rules"])
    assert len(routes.written) == 1
    # H-411: the PUT locks the mailbox before it reads the version, so a
    # mailbox with no settings row is locked too.
    assert routes.order == ["lock", "read"]


def test_the_lock_names_the_mailbox_and_ends_with_the_transaction() -> None:
    """H-411: an advisory lock that a commit or a rollback lets go."""
    assert "pg_advisory_xact_lock" in ac.LOCK_SQL and ":aid" in ac.LOCK_SQL


async def test_the_put_refuses_a_list_that_changed_after_the_read(routes: _SettingsDb) -> None:
    """Review P2: a client that read an old list, or none, cannot write over
    the stored one. Mutation: drop the version check and the write goes."""
    stale = ac.version_of([])
    with pytest.raises(HTTPException) as err:
        await ac.put_always_copy(ac.AlwaysCopyPut(
            account_id=BOX, rules=[ac.AlwaysCopyRule(match=JAIN, cc=[KIRAN])],
            expected=stale), USER)
    assert err.value.status_code == 409 and routes.written == []


def test_the_put_needs_the_version() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ac.AlwaysCopyPut(account_id=BOX, rules=[])


async def test_the_get_names_the_version(routes: _SettingsDb) -> None:
    out = await ac.get_always_copy(account_id=BOX, user=USER)
    assert out["version"] == ac.version_of(routes.rules)


async def test_the_put_refuses_a_stranger(routes: _SettingsDb) -> None:
    with pytest.raises(HTTPException) as err:
        await ac.put_always_copy(ac.AlwaysCopyPut(
            account_id=BOX, rules=[ac.AlwaysCopyRule(match=JAIN, cc=[KIRAN, STRANGER])],
            expected=ac.version_of(routes.rules)), USER)
    assert err.value.status_code == 422 and STRANGER in err.value.detail
    assert routes.written == []


async def test_the_put_refuses_the_members_own_address(routes: _SettingsDb) -> None:
    with pytest.raises(HTTPException) as err:
        await ac.put_always_copy(ac.AlwaysCopyPut(
            account_id=BOX, rules=[ac.AlwaysCopyRule(match=JAIN, cc=[MEMBER])],
            expected=ac.version_of(routes.rules)), USER)
    assert err.value.status_code == 422 and routes.written == []


async def test_a_card_checks_the_class_again(routes: _SettingsDb) -> None:
    """Veena left the organization after the rule was saved."""
    routes.org.discard(VEENA)
    found = await ac.always_copy_people(routes, MEMBER, BOX, [SAGAR])
    assert found == [(KIRAN, JAIN)]


async def test_the_match_route_names_each_colleague(routes: _SettingsDb) -> None:
    out = await ac.match_always_copy(account_id=BOX, people=[SAGAR], user=USER)
    assert out == {"people": [{"address": KIRAN, "match": JAIN},
                              {"address": VEENA, "match": JAIN}]}


# ── 3. The reply card ────────────────────────────────────────────────────────


def _rule_world(monkeypatch) -> _World:
    w = _World(monkeypatch)
    db = _SettingsDb()
    db.__dict__.update({k: v for k, v in w.db.__dict__.items() if k not in ("rules", "written")})
    w.db = db
    return w


@pytest.mark.parametrize("held", [False, True])
async def test_the_rule_pre_adds_its_colleagues_ticked_with_its_words(
    monkeypatch, held: bool,
) -> None:
    w = _rule_world(monkeypatch)
    out = await w.card(**({"held": True, "untick_new": True} if held else {}))
    cc = out["ticked"]["reply_all"]["cc"]
    assert KIRAN in cc and VEENA in cc
    assert out["marks"][KIRAN] == f"your rule: always copy on {JAIN}"
    # The act takes them, because the card recorded them.
    await w.act(out, to=[SAGAR], cc=[a for a in out["recipients"]["reply_all"]["cc"]])
    assert KIRAN in w.sent[0].cc


async def test_the_member_can_untick_a_colleague_of_the_rule(monkeypatch) -> None:
    w = _rule_world(monkeypatch)
    out = await w.card()
    others = [a for a in out["recipients"]["reply_all"]["cc"] if a != KIRAN]
    await w.act(out, to=[SAGAR], cc=others)
    assert KIRAN not in (w.sent[0].cc or [])


async def test_a_mail_that_no_rule_matches_adds_nobody(monkeypatch) -> None:
    w = _rule_world(monkeypatch)
    w.db.rules = [{"match": "other.example", "cc": [KIRAN]}]
    out = await w.card()
    assert KIRAN not in out["recipients"]["reply_all"]["cc"]


# ── 4. The held new-mail card ────────────────────────────────────────────────


async def test_the_held_new_mail_card_adds_a_row_for_each_colleague() -> None:
    db = _SettingsDb()
    lists = {"to": [SAGAR], "cc": [], "bcc": []}
    ruled = await hc._always_copy_rows(db, MEMBER, BOX, lists)
    assert lists["cc"] == [KIRAN, VEENA] and ruled == {KIRAN: JAIN, VEENA: JAIN}
    rows = hc.recipient_rows(lists, {SAGAR: "thread", KIRAN: "org", VEENA: "org"}, [])
    hc._mark_ruled(rows, ruled)
    kiran = next(r for r in rows if r["address"] == KIRAN)
    assert kiran["checked"] is True and kiran["hint"] == H.rule_copy_mark(JAIN)


# ── 5. The tools ─────────────────────────────────────────────────────────────


def test_the_pin_and_the_held_decision() -> None:
    assert "always_copy" in eg.HELD_CARD_SEND_TOOLS["update_assistant_settings"]
    assert H.HELD_SETTING_DECISIONS["always_copy"] == H.SETTING_REFUSED


async def test_a_held_chat_never_changes_the_rules(monkeypatch) -> None:
    agents = F.load_email_agent("ea_always_copy_held")
    calls: list[Any] = []

    async def any_call(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return {}

    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    monkeypatch.setattr(agents, "_put", any_call)
    monkeypatch.setattr(agents, "_get", any_call)
    out = await agents.update_assistant_settings(
        BOX, always_copy=[{"match": JAIN, "cc": [KIRAN]}])
    assert out.startswith("Nothing changed.") and "always_copy" in out
    assert calls == []


async def test_the_tool_asks_a_card_then_puts_the_whole_list(monkeypatch) -> None:
    agents = F.load_email_agent("ea_always_copy_put")
    puts: list[tuple[str, Any]] = []
    cards: list[dict[str, Any]] = []

    async def get(path: str, params: Any = None) -> Any:
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": MEMBER, "display_label": "Work"}]
        if path == "/email/assistant/always-copy":
            return {"rules": [], "version": "v-empty"}
        return {}

    async def put(path: str, body: Any) -> Any:
        puts.append((path, body))
        return {}

    async def card(**kw: Any) -> bool:
        cards.append(kw)
        return True

    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_put", put)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    out = await agents.update_assistant_settings(
        BOX, always_copy=[{"match": "@JAINUNIVERSITY.ac.in", "cc": [KIRAN]}],
        always_copy_version="v-empty")
    assert len(cards) == 1 and "Always copy" in cards[0]["context"]
    assert puts == [("/email/assistant/always-copy",
                     {"account_id": BOX, "rules": [{"match": JAIN, "cc": [KIRAN]}],
                      "expected": "v-empty"})]
    assert out.startswith("Always-copy rules updated")


def _copy_agent(monkeypatch, name: str, versions: list[str], put_error: Any = None):
    """The email agent with a fake gateway. Each read of the list answers
    the next version of *versions*. A PUT records its body, and raises
    *put_error* when one is given."""
    agents = F.load_email_agent(name)
    puts: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    left = list(versions)

    async def get(path: str, params: Any = None) -> Any:
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": MEMBER, "display_label": "Work"}]
        if path == "/email/assistant/always-copy":
            return {"rules": [], "version": left.pop(0) if len(left) > 1 else left[0]}
        if path == "/email/rules":
            return {"rules": []}
        return {}

    async def put(path: str, body: Any) -> Any:
        puts.append(body)
        if put_error is not None:
            raise put_error(agents)
        return {}

    async def card(**kw: Any) -> bool:
        cards.append(kw)
        return True

    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_put", put)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    return agents, puts, cards


async def test_the_list_read_names_its_version(monkeypatch) -> None:
    """H-411: the model reads the version with the list, so it can pass it back."""
    agents, _puts, _cards = _copy_agent(monkeypatch, "ea_always_copy_read", ["v-read"])
    out = await agents.get_rules_and_settings(BOX)
    assert "Always-copy version: v-read." in out


async def test_the_tool_sends_the_version_that_the_model_read(monkeypatch) -> None:
    """H-411: the member changes the list in the settings screen while the
    card waits. The PUT carries the version that the model read, so the
    gateway answers 409 and the tool says so in a plain sentence. Mutation:
    read the version again after the card and send it, and the PUT carries
    ``v-ui``."""
    detail = "Nothing changed. The always-copy rules changed after you read them."

    def conflict(agents: Any) -> Exception:
        return agents.GatewayError("PUT failed (409)", 409, detail=detail)

    agents, puts, cards = _copy_agent(monkeypatch, "ea_always_copy_stale",
                                      ["v-model", "v-ui"], put_error=conflict)
    out = await agents.update_assistant_settings(
        BOX, always_copy=[{"match": JAIN, "cc": [KIRAN]}], always_copy_version="v-model")
    assert [p["expected"] for p in puts] == ["v-model"]
    assert out == detail and len(cards) == 1


async def test_the_tool_refuses_a_version_that_is_old_already(monkeypatch) -> None:
    """H-411: the list changed after the model read it and before the tool
    ran. Nothing changes, no card shows, and nothing is put. Mutation: send
    the version of the tool's own read, as before H-411, and the PUT goes."""
    agents, puts, cards = _copy_agent(monkeypatch, "ea_always_copy_old", ["v-ui"])
    out = await agents.update_assistant_settings(
        BOX, always_copy=[{"match": JAIN, "cc": [KIRAN]}], always_copy_version="v-model")
    assert out == agents._COPY_VERSION_CHANGED and out.startswith("Nothing changed.")
    assert puts == [] and cards == []


async def test_the_tool_needs_the_version(monkeypatch) -> None:
    """H-411: a write with no version is refused, so the tool never falls
    back to a read of its own."""
    agents, puts, cards = _copy_agent(monkeypatch, "ea_always_copy_none", ["v-ui"])
    out = await agents.update_assistant_settings(
        BOX, always_copy=[{"match": JAIN, "cc": [KIRAN]}])
    assert out == agents._COPY_VERSION_MISSING
    assert puts == [] and cards == []


async def test_a_new_mail_gets_the_colleagues_of_the_rule_on_its_card(monkeypatch) -> None:
    agents = F.load_email_agent("ea_always_copy_send")

    async def get(path: str, params: Any = None) -> Any:
        if path == "/email/assistant/always-copy/match":
            assert params["people"] == [SAGAR]
            return {"people": [{"address": KIRAN, "match": JAIN}]}
        return {}

    monkeypatch.setattr(agents, "_get", get)
    cc, lines = await agents._always_copy_cc(BOX, [SAGAR], [], None)
    assert cc == [KIRAN] and lines == [f"Cc {KIRAN} — your rule: always copy on {JAIN}."]
