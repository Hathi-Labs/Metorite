"""The classifier of a held card (``email_app_master_plan.md`` §21.4 item 2).

The server decides who is known, from data. The model, the draft text and the
mail never set a class (§21.2 rule 1). A sender of outside mail is not known
(rule 3). This is the hermetic half. The R8 half,
``test_held_send_classes_r8.py``, runs the same SQL on a real database under
FORCE ROW LEVEL SECURITY, and a person of another tenant is never known there.

R7, each with the mutation that turns it red:

* ``test_org_and_sent_start_ticked_and_new_starts_unticked`` — tick a new row.
* ``test_the_strongest_class_wins`` — reorder the arms of the classifier.
* ``test_a_seen_sender_is_new`` — read a sender as known.
* ``test_the_model_cannot_pass_a_class`` — add a class field to a request.
* ``test_the_org_arms_name_the_bound_tenant`` — drop the tenant predicate.
* ``test_lookalike_*`` — the flag of open question 4.
"""
from __future__ import annotations

import inspect

import pytest
from acb_skills import held_card as H
from gateway.routes.email.automation import held_card as hc
from pydantic import ValidationError

from tests.unit import _held_card_fakes as F


async def test_the_strongest_class_wins() -> None:
    db = F.FakeDb()
    db.classes = {
        "both@acme.test": (True, True),       # a colleague the member wrote to
        "wrote@contoso.test": (False, True),  # sent only
        "boss@acme.test": (True, False),      # org only
    }
    got = await hc.classify_addresses(
        db, F.MEMBER,
        ["Both@acme.test", "wrote@contoso.test", "boss@acme.test", "kim@thread.test",
         "x@evil.example"],
        thread_senders=["kim@thread.test", "wrote@contoso.test"])
    assert got == {
        "both@acme.test": "org",
        "wrote@contoso.test": "sent",
        "boss@acme.test": "org",
        "kim@thread.test": "thread",
        "x@evil.example": "new",
    }


async def test_a_seen_sender_is_new() -> None:
    """A sender that mailed the member, and that the member never wrote to,
    is the shape of the attacker. The classifier reads no ``email_senders``
    and no ``email_contacts`` row, so a seen sender stays new."""
    assert "email_senders" not in hc.CLASSIFY_SQL
    assert "email_contacts" not in hc.CLASSIFY_SQL
    db = F.FakeDb()
    db.classes = {}
    got = await hc.classify_addresses(db, F.MEMBER, ["sender@outside.test"])
    assert got == {"sender@outside.test": "new"}


def test_org_and_sent_start_ticked_and_new_starts_unticked() -> None:
    rows = hc.recipient_rows(
        {"to": [F.KNOWN], "cc": [F.COLLEAGUE, F.STRANGER], "bcc": []},
        {F.KNOWN: "sent", F.COLLEAGUE: "org", F.STRANGER: "new"},
        ["contoso.test", "acme.test"])
    by = {r["address"]: r for r in rows}
    assert by[F.KNOWN]["checked"] is True and by[F.KNOWN]["hint"] == ""
    assert by[F.COLLEAGUE]["checked"] is True
    assert by[F.STRANGER]["checked"] is False
    assert by[F.STRANGER]["hint"] == H.NEW_MARK
    assert [r["id"] for r in rows] == ["to-0", "cc-0", "cc-1"]
    assert frozenset({"org", "sent", "thread"}) == H.DEFAULT_TICKED


def test_an_address_with_no_class_is_new() -> None:
    rows = hc.recipient_rows({"to": ["who@x.test"]}, {}, [])
    assert rows[0]["class"] == "new" and rows[0]["checked"] is False


def test_the_model_cannot_pass_a_class() -> None:
    """No request of the model's path carries a class, and the card rows
    come from the record (``card_rows``), never from a request."""
    assert "class" not in hc.HeldCardRequest.model_fields
    assert "classes" not in hc.HeldCardRequest.model_fields
    assert "rows" not in hc.HeldCardActRequest.model_fields
    agents = F.load_email_agent("ea_held_classes")
    for name in ("send_draft", "schedule_send", "compose_reply"):
        params = set(inspect.signature(getattr(agents, name)).parameters)
        assert not params & {"class", "classes", "known", "rows", "checked"}, name
    with pytest.raises(ValidationError):
        hc.HeldCardRequest(kind="draft", account_id=F.BOX, draft_id=F.DRAFT, classes={})


def test_the_org_arms_name_the_bound_tenant() -> None:
    """Each org arm names the bound tenant, as the suggest route does, so an
    unbound session matches no row (R5)."""
    tenant = "current_setting('app.tenant_id', true)"
    assert hc.CLASSIFY_SQL.count(tenant) == 2
    assert "ux.status IN ('suspended', 'removed')" in hc.CLASSIFY_SQL
    assert "user_id = :uid" in hc.CLASSIFY_SQL


@pytest.mark.parametrize(("domain", "known", "like"), [
    ("fracktai.in", ["fracktal.in"], "fracktal.in"),         # one letter
    ("fracktall.in", ["fracktal.in"], "fracktal.in"),        # one more
    ("frakctal.in", ["fracktal.in"], "fracktal.in"),         # a swap
    ("fr4cktal.in", ["fracktal.in"], "fracktal.in"),         # a digit for a letter
    ("fr\u0430cktal.in", ["fracktal.in"], "fracktal.in"),    # a Cyrillic a
    ("rnicrosoft.com", ["microsoft.com"], "microsoft.com"),  # rn for m
    ("fracktal.in", ["fracktal.in"], None),                  # known is not a lookalike
    ("gmail.com", ["fracktal.in"], None),                    # unrelated
    ("a.io", ["b.io"], None),                                # too short for one edit
])
def test_lookalike_domains(domain: str, known: list[str], like: str | None) -> None:
    assert H.lookalike_of(domain, known) == like


def test_lookalike_reads_punycode_of_a_lookalike() -> None:
    puny = "fr\u0430cktal.in".encode("idna").decode("ascii")
    assert puny.startswith("xn--")
    assert H.lookalike_of(puny, ["fracktal.in"]) == "fracktal.in"


def test_a_lookalike_warning_goes_on_a_new_row_only() -> None:
    rows = hc.recipient_rows(
        {"to": ["ravi@fracktai.in", "geo@fracktal.in"]},
        {"ravi@fracktai.in": "new", "geo@fracktal.in": "org"},
        ["fracktal.in"])
    assert "fracktal.in" in rows[0]["warning"]
    assert "warning" not in rows[1]


@pytest.mark.parametrize("bad", ["", "no-at-sign", "a b@x.test", "a​@x.test",
                                 "a@x.test‮", "a" * 250 + "@x.test"])
def test_an_address_that_the_card_cannot_show_whole_is_refused(bad: str) -> None:
    assert H.address_problem(bad)


def test_the_label_shows_the_punycode_of_a_non_ascii_domain() -> None:
    assert H.recipient_label("cc", "ravi@fr\u0430cktal.in").endswith(
        "(non-ASCII domain: xn--frcktal-3fg.in)")
    assert H.recipient_label("to", F.KNOWN) == f"To: {F.KNOWN}"


# ── The reply card of a held chat (§21.3, the Reply row; open question 1) ────


def _prep() -> dict:
    return {
        "message_id": "m-1", "account_id": F.BOX, "mailbox": "Work", "subject": "Re: Q",
        "original": {}, "body": "Hi", "notices": [], "draft_id": "",
        "recipients": {"reply": {"to": [F.KNOWN], "cc": []},
                       "reply_all": {"to": [F.KNOWN, F.STRANGER], "cc": [F.COLLEAGUE]}},
        "classes": {F.KNOWN: "thread", F.STRANGER: "new", F.COLLEAGUE: "org"},
    }


def test_a_held_reply_all_stays_and_a_new_address_starts_unticked() -> None:
    """Open question 1 took "the card opens on Reply". The tick boxes
    (owner, 2026-10-24) replace that: the card opens on Reply all (§22.6),
    and a person that the member does not know starts UNTICKED. Mutation:
    leave the new address ticked → red."""
    agents = F.load_email_agent("ea_held_reply")
    form = agents._reply_form(_prep(), True)
    agents._held_reply_form(form, _prep())
    assert form["reply_all"] is True
    assert form["ticked"]["reply_all"] == {"to": [F.KNOWN], "cc": [F.COLLEAGUE]}
    assert form["ticked"]["reply"] == {"to": [F.KNOWN], "cc": []}
    assert agents._REPLY_NEW_UNTICKED_NOTICE in form["notices"]
    assert form["classes"][F.STRANGER] == "new"
    assert form["classes"][F.KNOWN] == "thread"


def test_a_held_reply_all_of_known_people_stays_reply_all() -> None:
    agents = F.load_email_agent("ea_held_reply_known")
    prep = _prep()
    prep["classes"][F.STRANGER] = "sent"
    form = agents._reply_form(prep, True)
    agents._held_reply_form(form, prep)
    assert form["reply_all"] is True and form["notices"] == []


def test_an_address_with_no_class_reads_new_on_the_reply_card() -> None:
    """A gateway that sent no classes fails closed."""
    agents = F.load_email_agent("ea_held_reply_none")
    prep = _prep()
    prep.pop("classes")
    form = agents._reply_form(prep, True)
    agents._held_reply_form(form, prep)
    assert set(form["classes"].values()) == {"new"}
    # Each person starts unticked, so nothing goes until the member ticks.
    assert form["ticked"]["reply_all"] == {"to": [], "cc": []}
    assert form["ticked"]["reply"] == {"to": [], "cc": []}


def test_the_reply_form_keeps_the_classes_and_a_plain_form_is_as_before() -> None:
    from acb_skills.ask_tools import clean_reply_form

    agents = F.load_email_agent("ea_held_reply_clean")
    form = agents._reply_form(_prep(), False)
    plain = clean_reply_form(form)
    assert "classes" not in plain
    form["classes"] = {F.KNOWN.upper(): "thread", F.STRANGER: "vip"}
    cleaned = clean_reply_form(form)
    assert cleaned["classes"] == {F.KNOWN: "thread", F.STRANGER: "new"}
    form["classes"] = {"not an address": "org"}
    with pytest.raises(ValueError):
        clean_reply_form(form)


async def test_the_reply_card_route_classes_only_with_the_switch_on(monkeypatch) -> None:
    """Mutation: send ``classes`` with the switch off → red."""
    from gateway.routes.email.automation import reply_card as rc

    async def load_mail(message_id: str, owner: str):
        import types

        return types.SimpleNamespace(
            recipients=lambda reply_all: (
                ([F.KNOWN, F.STRANGER], []) if reply_all else ([F.KNOWN], [])),
            notices=lambda: [], account_id=F.BOX, mailbox="Work", subject="Q",
            sender=F.KNOWN, sender_name="", received="", excerpt="",
            thread_id=None, provider_message_id="p", address=F.MEMBER)

    async def keep(*_a, **_k):
        return "", False

    async def no_record(_mail):
        return []

    seen: list = []

    async def classes(owner, addresses, *, primary, **_kw):
        seen.append((addresses, primary))
        return {F.KNOWN: "thread", F.STRANGER: "new"}

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "_keep_card_draft", keep)
    monkeypatch.setattr(rc, "_chat_record", no_record)
    monkeypatch.setattr(rc, "_reply_classes", classes)
    req = rc.ReplyCardRequest(message_id="0b0b0b0b-0000-4000-8000-00000000000b", draft="Hi")
    F.flag_off(monkeypatch)
    assert "classes" not in await rc.reply_card(req, F.user())
    F.flag_on(monkeypatch)
    out = await rc.reply_card(req, F.user())
    assert out["classes"] == {F.KNOWN: "thread", F.STRANGER: "new"}
    assert seen[-1][1] == [F.KNOWN]


async def test_a_reply_to_that_is_not_the_sender_is_not_thread(monkeypatch) -> None:
    """Fix round 1 (P2-a). From vendor@x, Reply-To drop@evil: the Reply goes
    to drop@evil, and drop@evil is ``new``, never "from this thread".
    Mutation: pass the Reply-To as the thread sender → red."""
    import types

    from gateway.routes.email.automation import reply_card as rc

    vendor, drop = "vendor@x.test", "drop@evil.example"

    async def load_mail(message_id: str, owner: str):
        return types.SimpleNamespace(
            recipients=lambda reply_all: ([drop], []),
            notices=lambda: [], account_id=F.BOX, mailbox="Work", subject="Q",
            sender=vendor, sender_name="", received="", excerpt="",
            thread_id=None, provider_message_id="p", address=F.MEMBER)

    async def keep(*_a, **_k):
        return "", False

    async def no_record(_mail):
        return []

    db = F.FakeDb()
    db.classes = {}
    import contextlib

    @contextlib.asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "_keep_card_draft", keep)
    monkeypatch.setattr(rc, "_chat_record", no_record)
    monkeypatch.setattr(rc, "_tenant_session", session)
    F.flag_on(monkeypatch)
    out = await rc.reply_card(
        rc.ReplyCardRequest(message_id="0b0b0b0b-0000-4000-8000-00000000000b", draft="Hi"),
        F.user())
    assert out["classes"] == {drop: "new"}
