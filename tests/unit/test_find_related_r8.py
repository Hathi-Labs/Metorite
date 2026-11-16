"""R8 — FIND-3 (task 3240): rung 4 reads ids, and opens only what the member can open.

``find_engine.md`` §6: "A related record goes through ``open``. A record that
the member cannot open leaves no trace." ``knowledge_graph.md`` §6: "A hidden
neighbour leaves no trace: no count, no relation and no hidden marker."

Against a REAL Postgres, as the non-privileged role of the phase-4 rehearsal
(no superuser, no BYPASSRLS), through the real ``related`` and ``open`` of the
CRM, People and Email, and the real ``GET /search/company``:

* **The Fracktal case.** "Neuropeak order" finds the deal, and the thread from
  the address of its lead, in one call. No mail holds «Neuropeak» or "order".
  One mail stands for the thread. Flag off: no ``related`` key.
* **Another member's mailbox.** The lead's mail in member B's mailbox, and in
  a mailbox that member A keeps separate, never shows for A.
* **The CRM gates.** A member whom the CRM access rule refuses, and an org off
  the kill switch, get no CRM record and no id of one.
* **Org B.** Org B's records and mail never show in org A, and A's never in B.
* **Free mail.** A company whose website is a free-mail host gives no domain
  mail. A company with its own domain does.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from sqlalchemy import text

from tests.unit.test_crm_find_and_page_r8 import _make, _native, ns  # noqa: F401
from tests.unit.test_email_keep_separate import _account, _as_member, _assert_non_priv, _mail
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
A = f"related-a-{TAG}@related-r8.test"
B = f"related-b-{TAG}@related-r8.test"
C = f"related-c-{TAG}@related-r8.test"
R = f"related-r-{TAG}@related-r8.test"
DOMAIN = f"neuropeek.n{TAG}.test"
SHARAT = f"sharat@{DOMAIN}"
PRIYA = f"priya@bitnib.n{TAG}.test"
RAVI = f"ravi.{TAG}@gmail.com"
MEERA = f"meera@fracktal.n{TAG}.test"
KIRAN = f"kiran@kiranco.n{TAG}.test"

#: Every app of the ladder, and the CRM admin's right, so the access rule passes.
ALL = ("feature:email", "feature:crm", "feature:people", "admin:access:manage")


def _member(email: str, org: str, *perms: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(list(perms)))


async def _crm(p: Any, org: str) -> dict[str, str]:
    """The CRM of one org: the deal, its company, its converted lead and a
    contact, and a company whose website is a free-mail host."""
    company = await _make(p, org, "/crm/organizations", {
        "name": "Bitnib Technologies", "website": f"https://www.{DOMAIN}/about"})
    deal = await _make(p, org, "/crm/deals", {
        "name": "Neuropeak - Snowflake 3D printer", "company_id": company})
    lead = await _make(p, org, "/crm/leads", {
        "first_name": "Sharat", "last_name": "Satyanarayana", "email": SHARAT})
    contact = await _make(p, org, "/crm/contacts", {
        "first_name": "Priya", "last_name": "Bitnib", "email": PRIYA, "company_id": company})
    gmail_co = await _make(p, org, "/crm/organizations", {
        "name": "Gmailshop Traders", "website": "https://gmail.com"})
    ravi = await _make(p, org, "/crm/contacts", {
        "first_name": "Ravi", "last_name": "Gmailuser", "email": RAVI, "company_id": gmail_co})
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_leads SET converted_deal_id = CAST(:d AS uuid), converted_at = now() "
            "WHERE id = CAST(:l AS uuid)"), {"d": deal, "l": lead})
        c.execute(text(
            "INSERT INTO crm_deal_contacts (organization_id, deal_id, contact_id, is_primary) "
            "VALUES (CAST(:o AS uuid), CAST(:d AS uuid), CAST(:c AS uuid), true)"),
            {"o": org, "d": deal, "c": contact})
    return {"company": company, "deal": deal, "lead": lead, "contact": contact,
            "gmail_co": gmail_co, "ravi": ravi}


def _copied(admin: Any, *, org: str, account_id: str, sender: str, subject: str,
            folder: str = "inbox", to: tuple[str, ...] = (), cc: tuple[str, ...] = (),
            bcc: tuple[str, ...] = (), minutes_ago: int = 10) -> str:
    """One mail with To, Cc and Bcc lists, in its own thread."""
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id, folder, "
            "from_address, to_addresses, cc_addresses, bcc_addresses, subject, body_text, "
            "received_at, is_read, organization_id) VALUES (CAST(:a AS uuid), :p, :t, :f, "
            "CAST(:frm AS jsonb), CAST(:to AS jsonb), CAST(:cc AS jsonb), CAST(:bcc AS jsonb), "
            ":s, 'A note.', :r, false, CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:10]}", "t": f"t-{uuid.uuid4().hex[:8]}",
             "f": folder, "frm": json.dumps({"name": "N", "email": sender}),
             "to": json.dumps([{"email": e} for e in to]),
             "cc": json.dumps([{"email": e} for e in cc]),
             "bcc": json.dumps([{"email": e} for e in bcc]), "s": subject,
             "r": datetime.now(UTC) - timedelta(minutes=minutes_ago), "o": org}).scalar_one())


@pytest.fixture
async def world(ns, app_engine):  # noqa: F811
    """Org A: the CRM, member A's mailbox and a mailbox that A keeps
    separate, member B's mailbox, and a person. Org B: the same CRM and the
    lead's mail in member C's mailbox."""
    _assert_non_priv(app_engine)
    p = ns
    await _native(p, p.org_a)
    await _native(p, p.org_b)
    crm_a = await _crm(p, p.org_a)
    crm_b = await _crm(p, p.org_b)
    box_a = _account(p.admin_engine, org=p.org_a, owner=A)
    box_a2 = _account(p.admin_engine, org=p.org_a, owner=A, pooled=False)
    box_b = _account(p.admin_engine, org=p.org_a, owner=B)
    box_c = _account(p.admin_engine, org=p.org_b, owner=C)
    thread = f"t-sharat-{TAG}"
    # The thread of the order. No mail holds «Neuropeak» or "order".
    sharat = [
        _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=SHARAT, thread=thread,
              subject=f"Snowflake unit: {what}", body=f"The {what} of the 3D unit.",
              minutes_ago=minutes)
        for what, minutes in (("payment details", 30), ("shipping", 20), ("invoice", 10))
    ]
    to_priya = _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=A, folder="sent",
                     to=[PRIYA], subject="Quote for the bench", body="The quote.")
    portal = _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=f"info@{DOMAIN}",
                   subject="Portal access for your team", body="Your login.")
    chatter = _mail(p.admin_engine, org=p.org_a, account_id=box_a,
                    sender=f"other.{TAG}@gmail.com", subject="Weekend chatter", body="Hi.")
    from_ravi = _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=RAVI,
                      subject="Ravi's note on the stock", body="The stock list.")
    from_meera = _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=MEERA,
                       subject="Meera's plan for the week", body="The plan.")
    separate = _mail(p.admin_engine, org=p.org_a, account_id=box_a2, sender=SHARAT,
                     subject="Kept apart by member A", body="Separate box.")
    for_b = _mail(p.admin_engine, org=p.org_a, account_id=box_b, sender=SHARAT,
                  subject="For member B only", body="B's copy.")
    org_b = _mail(p.admin_engine, org=p.org_b, account_id=box_c, sender=SHARAT,
                  subject="The mail of org B", body="Org B.")
    # Fix round, P2-3: a mail where Priya is only in Cc, and A's own sent mail
    # where Priya is only in Bcc. Each is newer than the quote of `to_priya`.
    cc_priya = _copied(p.admin_engine, org=p.org_a, account_id=box_a,
                       sender=f"vendor@vendor.n{TAG}.test", subject="Shared note on the bench",
                       to=(A,), cc=(PRIYA,), minutes_ago=1)
    bcc_priya = _copied(p.admin_engine, org=p.org_a, account_id=box_a, sender=A, folder="sent",
                        subject="Blind copy note", to=(f"else@else.n{TAG}.test",),
                        bcc=(PRIYA,), minutes_ago=2)
    # Fix round, P2-1: Kiran wrote in thread tk, A answered in tk, and Kiran
    # wrote again in tk2, longer ago.
    kiran = await _make(p, p.org_a, "/crm/contacts", {
        "first_name": "Kiran", "last_name": "Threadtest", "email": KIRAN})
    tk, tk2 = f"t-kiran-{TAG}", f"t-kiran2-{TAG}"
    kiran_mail = {
        "first": _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=KIRAN, thread=tk,
                       subject="Bench calibration", body="The dates.", minutes_ago=5),
        "reply": _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=A, thread=tk,
                       folder="sent", to=[KIRAN], subject="Re: Bench calibration",
                       body="Agreed.", minutes_ago=3),
        "other": _mail(p.admin_engine, org=p.org_a, account_id=box_a, sender=KIRAN,
                       thread=tk2, subject="Spare parts list", body="The list.",
                       minutes_ago=50),
    }
    with p.admin_engine.begin() as c:
        meera = str(c.execute(text(
            "INSERT INTO people (name, email, department, source, organization_id) "
            "VALUES ('Meera Colleague', :e, 'Ops', :s, CAST(:o AS uuid)) RETURNING id"),
            {"e": MEERA, "s": f"related-{TAG}", "o": p.org_a}).scalar_one())
        # Fix round, P2-2: two people that `open` must not open.
        gone = {status: str(c.execute(text(
            "INSERT INTO people (name, email, department, status, source, organization_id) "
            "VALUES (:n, :e, 'Ops', :st, :s, CAST(:o AS uuid)) RETURNING id"),
            {"n": f"{status.title()} Person", "e": f"{status}@fracktal.n{TAG}.test",
             "st": status, "s": f"related-{TAG}", "o": p.org_a}).scalar_one())
            for status in ("alumni", "invited")}
    yield {"p": p, "a": crm_a, "b": crm_b, "sharat": sharat, "to_priya": to_priya,
           "portal": portal, "chatter": chatter, "from_ravi": from_ravi,
           "from_meera": from_meera, "separate": separate, "for_b": for_b, "org_b": org_b,
           "meera": meera, "gone": gone, "cc_priya": cc_priya, "bcc_priya": bcc_priya,
           "kiran": kiran, "kiran_mail": kiran_mail, "threads": (tk, tk2)}
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id IN (:a, :b, :c)"),
                  {"a": A, "b": B, "c": C})
        c.execute(text("DELETE FROM people WHERE source = :s"), {"s": f"related-{TAG}"})


@pytest.fixture
def flags(monkeypatch):
    """``FIND_ORGS`` and ``CONTEXT_RECENCY`` on the cached settings."""
    settings = get_settings()

    def _set(find: str, recency: bool = True) -> None:
        monkeypatch.setattr(settings, "find_orgs", find, raising=False)
        monkeypatch.setattr(settings, "context_recency", recency, raising=False)

    _set("")
    return _set


@pytest.fixture
def roomy(monkeypatch):
    """A cold scratch database is slower than the route's budget, and a read
    cut off by its deadline gives nothing. Room here."""
    from gateway.retrieval import company

    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 60.0)
    monkeypatch.setattr(company, "NEAR_LOOKUP_S", 30.0)
    monkeypatch.setattr(company, "BROADEN_BUDGET_S", 120.0)
    monkeypatch.setattr(company, "RELATED_BUDGET_S", 60.0)
    monkeypatch.setattr(company, "ANSWER_CAP_S", 300.0)


async def _ask(p: Any, user: UserContext, q: str, apps: str = "crm,email") -> dict[str, Any]:
    from tests.unit.test_company_search_r8 import _ask as ask

    return await ask(p, user, {"q": q, "apps": apps})


def _related(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["id"]: h for h in answer.get("related", [])}


async def _abilities(p: Any, user: UserContext, key: str, refs: list[str], *,
                     opening: bool, raw: bool = False) -> list[Any]:
    """``related`` or ``open`` of one source, as *user*. *raw* calls the
    ability itself, past ``Source.open_refs``, so its own gate is the test."""
    from gateway.retrieval import sources

    source = sources.REGISTRY[key]
    async with _as_member(p, user.organization_id):
        if raw:
            ability = source.open if opening else source.related
            assert ability is not None
            return list(await ability(user, refs))
        if opening:
            return await source.open_refs(user, refs)
        return await source.related_refs(user, refs)


# ── The Fracktal case ───────────────────────────────────────────────────────

async def test_neuropeak_order_finds_the_deal_and_the_thread_of_its_lead(
        world, flags, roomy) -> None:
    p, crm = world["p"], world["a"]
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "Neuropeak order")
    hits = {h["id"] for h in answer["hits"]}
    assert crm["deal"] in hits
    related = _related(answer)
    thread = [i for i in world["sharat"] if i in related]
    # One mail stands for the thread: the newest.
    assert thread == [world["sharat"][2]]
    mail = related[thread[0]]
    assert (mail["matched_by"], mail["from"], mail["edge"], mail["via"]) == (
        "related", f"deal:{crm['deal']}", "mail_from", "lead_of")
    assert "neuropeak" not in mail["title"].lower() and "order" not in mail["title"].lower()
    assert not set(world["sharat"]) & hits
    # The other places of the deal: its lead and its contact.
    assert related[crm["lead"]]["edge"] == "lead_of"
    assert related[crm["contact"]]["edge"] == "contact_of"
    assert len([h for h in answer["related"] if h["from"] == f"deal:{crm['deal']}"]) == 3
    # The address and the domain are transit values: no `from`, `edge` or
    # `via` holds one. (The lead's own hint shows its address, as a CRM hit.)
    for hit in answer["related"]:
        for key in ("from", "edge", "via"):
            assert "@" not in str(hit.get(key)) and DOMAIN not in str(hit.get(key))
    # The flag off: no rung 4, and no mail of the thread anywhere.
    flags("")
    off = await _ask(p, _member(A, p.org_a, *ALL), "Neuropeak order")
    assert "related" not in off
    assert not any(i in json.dumps(off) for i in world["sharat"])


async def test_the_related_reads_give_ids_and_keys_only(world) -> None:
    p, crm = world["p"], world["a"]
    me = _member(A, p.org_a, *ALL)
    triples = await _abilities(p, me, "crm", [f"deal:{crm['deal']}"], opening=False)
    assert (f"deal:{crm['deal']}", "lead_of", f"lead:{crm['lead']}") in triples
    assert (f"deal:{crm['deal']}", "contact_of", f"contact:{crm['contact']}") in triples
    assert (f"deal:{crm['deal']}", "company_of", f"company:{crm['company']}") in triples
    assert (f"lead:{crm['lead']}", "address", f"address:{SHARAT}") in triples
    assert (f"company:{crm['company']}", "domain", f"domain:{DOMAIN}") in triples
    for _left, edge, right in triples:
        assert edge in ("contact_of", "lead_of", "company_of", "address", "domain")
        assert right.split(":", 1)[0] in ("contact", "lead", "company", "address", "domain")
        assert "Satyanarayana" not in right and "Bitnib" not in right
    mail = await _abilities(p, me, "email", [f"address:{SHARAT}"], opening=False)
    assert mail == [(f"address:{SHARAT}", "mail_from", f"email:{world['sharat'][2]}")]


# ── Another member's mailbox ────────────────────────────────────────────────

async def test_the_mail_of_another_member_never_shows(world, flags, roomy) -> None:
    p = world["p"]
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "Neuropeak order")
    text_of = json.dumps(answer)
    for hidden in (world["for_b"], world["separate"], world["org_b"]):
        assert hidden not in text_of
    assert "For member B only" not in text_of and "Kept apart" not in text_of
    # Non-vacuity: member B gets the lead's mail of B's own mailbox, and none of A's.
    seen_b = await _ask(p, _member(B, p.org_a, *ALL), "Neuropeak order")
    assert world["for_b"] in _related(seen_b)
    assert not any(i in json.dumps(seen_b) for i in world["sharat"])
    # The abilities, read straight: A's open of B's mail and of the kept-apart
    # mail by the search's scope gives nothing.
    me = _member(A, p.org_a, *ALL)
    assert await _abilities(p, me, "email", [f"email:{world['for_b']}"], opening=True) == []
    assert await _abilities(p, me, "email", [f"email:{world['separate']}"], opening=True) == []
    mail = await _abilities(p, me, "email", [f"address:{SHARAT}"], opening=False)
    assert {t[2] for t in mail} == {f"email:{world['sharat'][2]}"}
    # Non-vacuity: A opens A's own mail of the thread.
    own = f"email:{world['sharat'][0]}"
    opened = await _abilities(p, me, "email", [own], opening=True)
    assert [h.id for h in opened] == [world["sharat"][0]]
    # No feature:email: nothing, also past `open_refs`.
    no_mail = _member(A, p.org_a, "feature:crm")
    assert await _abilities(p, no_mail, "email", [own], opening=True, raw=True) == []
    assert await _abilities(p, no_mail, "email", [f"address:{SHARAT}"], opening=False,
                            raw=True) == []


# ── The CRM gates ───────────────────────────────────────────────────────────

async def test_a_member_the_crm_refuses_gets_no_trace_of_a_record(world, flags, roomy) -> None:
    p, crm = world["p"], world["a"]
    flags("*")
    refused = _member(R, p.org_a, "feature:email", "feature:crm", "feature:people")
    answer = await _ask(p, refused, "Neuropeak order")
    assert "crm" in answer["skipped"]
    text_of = json.dumps(answer)
    for ident in crm.values():
        assert ident not in text_of
    ref = f"deal:{crm['deal']}"
    assert await _abilities(p, refused, "crm", [ref], opening=True) == []
    assert await _abilities(p, refused, "crm", [ref], opening=False) == []
    # The ability itself checks the gate: a caller past `open_refs` (KG-6)
    # still gets nothing, and no error that names the CRM.
    assert await _abilities(p, refused, "crm", [ref], opening=True, raw=True) == []
    assert await _abilities(p, refused, "crm", [ref], opening=False, raw=True) == []


async def test_an_org_off_the_kill_switch_gets_no_crm_record(world, monkeypatch) -> None:
    from tests.unit._crm_fakes import crm_switch_on

    p, crm = world["p"], world["a"]
    me = _member(A, p.org_a, *ALL)
    ref = f"deal:{crm['deal']}"
    assert await _abilities(p, me, "crm", [ref], opening=True)  # non-vacuity
    crm_switch_on(monkeypatch, p.org_b)
    assert await _abilities(p, me, "crm", [ref], opening=True) == []
    assert await _abilities(p, me, "crm", [ref], opening=False) == []
    assert await _abilities(p, me, "crm", [ref], opening=True, raw=True) == []


# ── Org B ───────────────────────────────────────────────────────────────────

async def test_org_b_never_shows(world, flags, roomy) -> None:
    p = world["p"]
    flags("*")
    seen_c = await _ask(p, _member(C, p.org_b, *ALL), "Neuropeak order")
    assert world["org_b"] in _related(seen_c)
    assert world["b"]["deal"] in {h["id"] for h in seen_c["hits"]}
    text_c = json.dumps(seen_c)
    for ident in [*world["a"].values(), *world["sharat"], world["portal"], world["for_b"]]:
        assert ident not in text_c
    seen_a = await _ask(p, _member(A, p.org_a, *ALL), "Neuropeak order")
    text_a = json.dumps(seen_a)
    for ident in [*world["b"].values(), world["org_b"]]:
        assert ident not in text_a
    # The abilities, read straight, across the orgs.
    c = _member(C, p.org_b, *ALL)
    assert await _abilities(p, c, "crm", [f"deal:{world['a']['deal']}"], opening=True) == []
    assert await _abilities(p, c, "crm", [f"deal:{world['a']['deal']}"], opening=False) == []
    assert await _abilities(p, c, "email", [f"email:{world['sharat'][0]}"], opening=True) == []


# ── Free mail ───────────────────────────────────────────────────────────────

async def test_a_free_mail_company_gives_no_domain_mail(world, flags, roomy) -> None:
    p, crm = world["p"], world["a"]
    flags("*")
    me = _member(A, p.org_a, *ALL)
    shop = await _ask(p, me, "Gmailshop Traders")
    assert crm["gmail_co"] in {h["id"] for h in shop["hits"]}
    assert not [h for h in shop.get("related", []) if h["edge"] == "mail_domain"]
    assert world["chatter"] not in json.dumps(shop)
    triples = await _abilities(p, me, "crm", [f"company:{crm['gmail_co']}"], opening=False)
    assert not [t for t in triples if t[1] == "domain"]
    # A contact at a free-mail host still has the mail from its own address,
    # and never the mail of the host.
    ravi = await _ask(p, me, "Ravi Gmailuser")
    related = _related(ravi)
    assert related[world["from_ravi"]]["edge"] == "mail_from"
    assert world["chatter"] not in json.dumps(ravi)
    # Non-vacuity: a company with its own domain gives its domain mail.
    bitnib = await _ask(p, me, "Bitnib Technologies")
    portal = _related(bitnib)[world["portal"]]
    assert (portal["edge"], portal["from"], "via" in portal) == (
        "mail_domain", f"company:{crm['company']}", False)


# ── People ──────────────────────────────────────────────────────────────────

async def test_a_person_gives_the_mail_of_the_work_address(world, flags, roomy) -> None:
    p = world["p"]
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "Meera Colleague", apps="people,email")
    assert world["meera"] in {h["id"] for h in answer["hits"]}
    mail = _related(answer)[world["from_meera"]]
    assert (mail["edge"], mail["from"]) == ("mail_from", f"person:{world['meera']}")
    # No feature:people: the person is no hit, and nothing relates to it.
    none = await _ask(p, _member(A, p.org_a, "feature:email"), "Meera Colleague",
                      apps="people,email")
    assert world["meera"] not in json.dumps(none)


async def test_the_people_abilities_read_the_tenant_and_the_feature(world) -> None:
    """No FIND-3 edge ends at a person, so the step never opens one. KG-6
    will, so the gate of People's ``open`` is fenced here."""
    p = world["p"]
    ref = f"person:{world['meera']}"
    me = _member(A, p.org_a, *ALL)
    opened = await _abilities(p, me, "people", [ref], opening=True)
    assert [(h.id, h.title) for h in opened] == [(world["meera"], "Meera Colleague")]
    assert await _abilities(p, me, "people", [ref], opening=False) == [
        (ref, "address", f"address:{MEERA}")]
    # Org B: nothing of org A, as a hit or an address.
    c = _member(C, p.org_b, *ALL)
    assert await _abilities(p, c, "people", [ref], opening=True) == []
    assert await _abilities(p, c, "people", [ref], opening=False) == []
    # Fix round, P2-2 (``knowledge_graph.md`` §6): active people only. An
    # alumni and an invited person do not open, and Meera still does.
    gone = [f"person:{ident}" for ident in world["gone"].values()]
    assert await _abilities(p, me, "people", gone, opening=True) == []
    both = await _abilities(p, me, "people", [ref, *gone], opening=True)
    assert [h.id for h in both] == [world["meera"]]
    # No feature:people: nothing, also past `open_refs`.
    no_people = _member(A, p.org_a, "feature:email")
    for raw in (False, True):
        assert await _abilities(p, no_people, "people", [ref], opening=True, raw=raw) == []
        assert await _abilities(p, no_people, "people", [ref], opening=False, raw=raw) == []


# ── One conversation, one place (fix round, P2-1) ───────────────────────────

async def test_one_thread_takes_one_place_of_a_hit(world, flags, roomy) -> None:
    """Kiran wrote in thread tk, and A answered in tk. mail_from gives the
    first and mail_to gives the answer, so one round would show tk twice. The
    contact gets tk once, and tk2 takes the next place."""
    p, mails = world["p"], world["kiran_mail"]
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "Kiran Threadtest")
    assert world["kiran"] in {h["id"] for h in answer["hits"]}
    related = _related(answer)
    kiran = {i: h for i, h in related.items() if h["from"] == f"contact:{world['kiran']}"}
    assert list(kiran) == [mails["first"], mails["other"]]
    assert mails["reply"] not in json.dumps(answer)
    # The thread is internal: no answer key and no text holds it.
    for thread in world["threads"]:
        assert thread not in json.dumps(answer)
    # Non-vacuity: the related read gives the answer, a mail_to of tk.
    me = _member(A, p.org_a, *ALL)
    triples = await _abilities(p, me, "email", [f"address:{KIRAN}"], opening=False)
    assert (f"address:{KIRAN}", "mail_to", f"email:{mails['reply']}") in triples


# ── Cc and Bcc (fix round, P2-3) ────────────────────────────────────────────

async def test_a_mail_with_the_contact_in_cc_or_bcc_is_mail_to_it(world, flags, roomy) -> None:
    """A mail where Priya is only in Cc, and A's own sent mail where she is
    only in Bcc, are mail to her address."""
    p = world["p"]
    flags("*")
    me = _member(A, p.org_a, *ALL)
    triples = set(await _abilities(p, me, "email", [f"address:{PRIYA}"], opening=False))
    for key in ("cc_priya", "bcc_priya", "to_priya"):
        assert (f"address:{PRIYA}", "mail_to", f"email:{world[key]}") in triples, key
    answer = await _ask(p, me, "Priya Bitnib")
    related = _related(answer)
    for key in ("cc_priya", "bcc_priya"):
        assert related[world[key]]["edge"] == "mail_to", key
    # Member B's mailbox holds none of them, and B gets none.
    seen_b = await _ask(p, _member(B, p.org_a, *ALL), "Priya Bitnib")
    assert world["cc_priya"] not in json.dumps(seen_b)
    assert world["bcc_priya"] not in json.dumps(seen_b)
