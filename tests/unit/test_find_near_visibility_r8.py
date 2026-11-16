"""R8 — HRD-18b (task 3226): a near name comes from rows the member can open.

``find_engine.md`` §6: "CRM names come after the CRM gates. People names need
``feature:people``. Mail senders come from the mailboxes that the member can
read, by the email app's account filter. A name from the mailbox of another
member never shows, as a hit, a count or a "Do you mean"."

Against a REAL Postgres, as the non-privileged role of the phase-4 rehearsal
(no superuser, no BYPASSRLS), through the real near readers, the real
``/email/messages`` route function and the real ``GET /search/company``:

* **Email.** Member A's near senders hold A's senders only. A sender of
  member B's mailbox, in the mail or in the sender rollup, never reaches A.
  A sender of org B never reaches anyone in org A.
* **CRM.** The F1 pairs of task 3226 (Laxmi and «Lakshmi», Puja and
  «Pooja», Sarath and «Sharat», Deepa and «Dipa», Geeta and «Gita», Phani and
  «Fani») come back near: no raw SQL floor stands before the matcher. Org
  B's names never reach org A. A member whom the CRM access rule refuses
  gets the 403, and no name.
* **People.** Org B's directory never reaches org A.
* **query_inbox.** ``from_email="Sharath"`` finds no mail, then the near
  spelling «Sharat», and says so. ``nuropeak`` finds the mail from
  ``neuropeek``. Flag off: nothing changes.
* **The ladder.** "Sharath 3D printer" finds the deal of «Sharat
  Satyanarayana» and his mail in one call, and "nuropeak" finds both.
* **resolve_name** (FIND-5, ``GET /search/names``). "Sharat" gives the lead,
  the contact and the mail sender, each with its link, exact. "Sharath" gives
  them near, with the spelling. A sender of B's mailbox, the member's own sent
  mail and org B never show. A member whom the CRM refuses gets no CRM row.
  People names need ``feature:people``. ``one`` means one exact record.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings, name_match
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit.test_crm_find_and_page_r8 import _make, _native, ns  # noqa: F401
from tests.unit.test_email_keep_separate import _account, _as_member, _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_read_tool_paging_r8 import _direct

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
A = f"near-a-{TAG}@near-r8.test"
B = f"near-b-{TAG}@near-r8.test"
C = f"near-c-{TAG}@near-r8.test"
NEUROPEEK = f"sharat@neuropeek.n{TAG}.test"

#: The CRM names of org A whose near spellings F1 asked about.
F1_PAIRS = (("laxmi", "Lakshmi"), ("puja", "Pooja"), ("sarath", "Sharat"),
            ("deepa", "Dipa"), ("geeta", "Gita"), ("phani", "Fani"))


def _member(email: str, org: str, *perms: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(list(perms)))


#: Every app of the ladder, and the CRM admin's right, so the access rule passes.
ALL = ("feature:email", "feature:crm", "feature:people", "admin:access:manage")


def _mail(admin: Any, *, org: str, account: str, name: str, address: str,
          subject: str, body: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
            "folder, from_address, to_addresses, subject, body_text, received_at, "
            "is_read, organization_id) VALUES (CAST(:a AS uuid), :p, :t, 'inbox', "
            "CAST(:f AS jsonb), CAST('[]' AS jsonb), :s, :b, :r, false, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account, "p": f"pm-{uuid.uuid4().hex[:10]}", "t": f"t-{uuid.uuid4().hex[:8]}",
             "f": json.dumps({"name": name, "email": address}), "s": subject, "b": body,
             "r": datetime.now(UTC) - timedelta(minutes=5), "o": org}).scalar_one())


@pytest.fixture
async def world(ns, app_engine, monkeypatch):  # noqa: F811
    """Two members of org A, each with a private mailbox, a member of org B,
    the CRM of each org, and a People row of each."""
    _assert_non_priv(app_engine)
    p = ns
    await _native(p, p.org_a)
    await _native(p, p.org_b)
    company = await _make(p, p.org_a, "/crm/organizations", {"name": "Bitnib Technologies"})
    sharat = await _make(p, p.org_a, "/crm/contacts", {
        "first_name": "Sharat", "last_name": "Satyanarayana", "company_id": company})
    deal = await _make(p, p.org_a, "/crm/deals", {
        "name": "Neuropeak - Snowflake 3D printer", "company_id": company})
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deal_contacts (organization_id, deal_id, contact_id, is_primary) "
            "VALUES (CAST(:o AS uuid), CAST(:d AS uuid), CAST(:c AS uuid), true)"),
            {"o": p.org_a, "d": deal, "c": sharat})
    for _asked, name in F1_PAIRS:
        if name != "Sharat":
            await _make(p, p.org_a, "/crm/contacts", {"first_name": name, "last_name": "Fracktal"})
    # Org B's own names, which org A must never see.
    await _make(p, p.org_b, "/crm/contacts", {"first_name": "Shobhana", "last_name": "Rival"})

    box_a = _account(p.admin_engine, org=p.org_a, owner=A)
    box_b = _account(p.admin_engine, org=p.org_a, owner=B)
    box_c = _account(p.admin_engine, org=p.org_b, owner=C)
    mail_a = _mail(p.admin_engine, org=p.org_a, account=box_a, name="Sharat Satyanarayana",
                   address=NEUROPEEK, subject="Snowflake 3D printer order",
                   body="Please ship the 3D printer this week.")
    mail_b = _mail(p.admin_engine, org=p.org_a, account=box_b, name="Secretname Hidden",
                   address=f"secret@hidden-{TAG}.test", subject="Private", body="for B only")
    _mail(p.admin_engine, org=p.org_b, account=box_c, name="Rivalname Orgb",
          address=f"rival@orgb-{TAG}.test", subject="Other org", body="org B")
    with p.admin_engine.begin() as c:
        # A sender of B's rollup only, with no mail.
        c.execute(text(
            "INSERT INTO email_senders (account_id, email, name, organization_id) "
            "VALUES (CAST(:a AS uuid), :e, 'Rollupname Hidden', CAST(:o AS uuid))"),
            {"a": box_b, "e": f"rollup@hidden-{TAG}.test", "o": p.org_a})
        for org, name in ((p.org_a, "Gitanjali Peoplea"), (p.org_b, "Bhaskar Rival")):
            c.execute(text(
                "INSERT INTO people (name, department, source, organization_id) "
                "VALUES (:n, 'Ops', :s, CAST(:o AS uuid))"),
                {"n": name, "s": f"near-{TAG}", "o": org})
    yield {"p": p, "deal": deal, "mail_a": mail_a, "mail_b": mail_b, "box_a": box_a,
           "contact": sharat}
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id IN (:a, :b, :c)"),
                  {"a": A, "b": B, "c": C})
        c.execute(text("DELETE FROM people WHERE source = :s"), {"s": f"near-{TAG}"})


@pytest.fixture
def flags(monkeypatch):
    """``FIND_ORGS`` and ``CONTEXT_RECENCY`` on the cached settings."""
    settings = get_settings()

    def _set(find: str, recency: bool = True) -> None:
        monkeypatch.setattr(settings, "find_orgs", find, raising=False)
        monkeypatch.setattr(settings, "context_recency", recency, raising=False)

    _set("")
    return _set


async def _near(p: Any, user: UserContext, key: str, words: list[str]) -> dict[str, Any]:
    from gateway.retrieval import sources

    async with _as_member(p, user.organization_id):
        found = await sources.REGISTRY[key].near_names(user, words, 5)
    return {w: name_match.best_spelling(w, names) for w, names in found.items()}


def _spelt(found: dict[str, Any], word: str) -> str | None:
    best = found.get(word)
    return best.spelling if best is not None else None


# ── Email ───────────────────────────────────────────────────────────────────

async def test_near_senders_come_from_the_members_own_mailboxes(world) -> None:
    p = world["p"]
    words = ["sharath", "secretnam", "rollupnam", "rivalnam", "nuropeak"]
    got_a = await _near(p, _member(A, p.org_a, *ALL), "email", words)
    assert _spelt(got_a, "sharath") == "Sharat"
    assert _spelt(got_a, "nuropeak") == "neuropeek"
    # B's mail, B's rollup and org B's mail give A no name.
    for word in ("secretnam", "rollupnam", "rivalnam"):
        assert _spelt(got_a, word) is None, word
    got_b = await _near(p, _member(B, p.org_a, *ALL), "email", words)
    # Non-vacuity: B sees B's own senders, and never A's.
    assert _spelt(got_b, "secretnam") == "Secretname"
    assert _spelt(got_b, "rollupnam") == "Rollupname"
    assert _spelt(got_b, "sharath") is None and _spelt(got_b, "nuropeak") is None
    got_c = await _near(p, _member(C, p.org_b, *ALL), "email", words)
    assert _spelt(got_c, "rivalnam") == "Rivalname"
    assert _spelt(got_c, "sharath") is None and _spelt(got_c, "secretnam") is None


async def test_a_member_without_email_gets_no_sender(world) -> None:
    p = world["p"]
    got = await _near(p, _member(A, p.org_a, "feature:crm"), "email", ["sharath"])
    assert got == {}


# ── CRM ─────────────────────────────────────────────────────────────────────

async def test_the_f1_pairs_reach_the_judge(world) -> None:
    p = world["p"]
    got = await _near(p, _member(A, p.org_a, *ALL), "crm", [w for w, _n in F1_PAIRS])
    for asked, name in F1_PAIRS:
        assert _spelt(got, asked) == name, asked


async def test_crm_names_stay_in_their_org(world) -> None:
    p = world["p"]
    got_a = await _near(p, _member(A, p.org_a, *ALL), "crm", ["sobhana", "nuropeak"])
    assert _spelt(got_a, "sobhana") is None
    assert _spelt(got_a, "nuropeak") == "Neuropeak"
    got_c = await _near(p, _member(C, p.org_b, *ALL), "crm", ["sobhana", "nuropeak"])
    assert _spelt(got_c, "sobhana") == "Shobhana"
    assert _spelt(got_c, "nuropeak") is None


async def test_a_member_the_crm_refuses_gets_no_name(world) -> None:
    p = world["p"]
    with pytest.raises(HTTPException) as refused:
        await _near(p, _member(B, p.org_a, "feature:crm"), "crm", ["laxmi"])
    assert refused.value.status_code == 403


# ── People ──────────────────────────────────────────────────────────────────

async def test_people_names_stay_in_their_org(world) -> None:
    p = world["p"]
    got_a = await _near(p, _member(A, p.org_a, *ALL), "people", ["gitanjaly", "baskar"])
    assert _spelt(got_a, "gitanjaly") == "Gitanjali"
    assert _spelt(got_a, "baskar") is None
    got_c = await _near(p, _member(C, p.org_b, *ALL), "people", ["gitanjaly", "baskar"])
    assert _spelt(got_c, "baskar") == "Bhaskar" and _spelt(got_c, "gitanjaly") is None
    got_none = await _near(p, _member(A, p.org_a, "feature:email"), "people", ["gitanjaly"])
    assert got_none == {}


# ── query_inbox: the sender filter of /email/messages ───────────────────────

async def _inbox(p: Any, user: UserContext, sender: str, *, opt_in: bool = True,
                 **given: Any) -> dict[str, Any]:
    """``list_messages`` as ``query_inbox`` calls it: with ``near_sender``."""
    from gateway.routes.email.transport import messages

    if opt_in:
        given["near_sender"] = True
    async with _as_member(p, user.organization_id):
        return await _direct(messages.list_messages, folder="all", from_email=sender,
                             user=user, **given)


async def _inbox_http(p: Any, user: UserContext, params: dict[str, str]) -> dict[str, Any]:
    """``GET /email/messages`` through the real email router, as a request
    of the Mail UI runs it. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes.email.core import router

    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _caller() -> UserContext:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://mail.test") as client,
        ):
            res = await client.get("/email/messages", params=params)
    finally:
        release_tenant(scope)
    assert res.status_code == 200, res.text
    return res.json()


async def test_the_mail_ui_never_gets_a_near_sender(world, flags) -> None:
    """The opt-in (review of 2026-11-16): ``list_messages`` is the shared route
    of the Mail UI. With no ``near_sender``, a sender filter that finds no
    mail stays at zero, with no ``near`` key, also with ``FIND_ORGS=*``."""
    p = world["p"]
    me = _member(A, p.org_a, "feature:email")
    flags("*")
    direct = await _inbox(p, me, "Sharath", opt_in=False)
    assert direct["total"] == 0 and direct["emails"] == [] and "near" not in direct
    plain = await _inbox_http(p, me, {"folder": "all", "from_email": "Sharath"})
    assert plain["total"] == 0 and "near" not in plain
    # Non-vacuity: the same request with the opt-in gets the near spelling.
    asked = await _inbox_http(p, me, {"folder": "all", "from_email": "Sharath",
                                      "near_sender": "true"})
    assert asked["near"] == {"word": "Sharath", "as": "Sharat"} and asked["total"] == 1
    # With the flag off, the opt-in alone changes nothing.
    flags("")
    off = await _inbox_http(p, me, {"folder": "all", "from_email": "Sharath",
                                    "near_sender": "true"})
    assert off["total"] == 0 and "near" not in off


async def test_a_sender_filter_tries_the_near_spelling(world, flags) -> None:
    p = world["p"]
    me = _member(A, p.org_a, "feature:email")
    off = await _inbox(p, me, "Sharath")
    assert off["total"] == 0 and "near" not in off
    flags("*")
    on = await _inbox(p, me, "Sharath")
    assert on["near"] == {"word": "Sharath", "as": "Sharat"}
    assert [str(e["id"]) for e in on["emails"]] == [world["mail_a"]] and on["total"] == 1
    domain = await _inbox(p, me, "nuropeak")
    assert domain["near"] == {"word": "nuropeak", "as": "neuropeek"}
    assert [str(e["id"]) for e in domain["emails"]] == [world["mail_a"]]
    # An exact filter keeps its answer and gets no near key.
    exact = await _inbox(p, me, "neuropeek")
    assert exact["total"] == 1 and "near" not in exact
    # Page 2 counts the same near senders, so the pages agree.
    second = await _inbox(p, me, "Sharath", page=2, page_size=1)
    assert second["total"] == 1 and second["emails"] == []
    assert second["near"] == {"word": "Sharath", "as": "Sharat"}


async def test_a_sender_with_mail_left_out_by_other_filters_tries_no_near_spelling(
        world, flags) -> None:
    """Review of 2026-11-16 (P1-a): "unread mail from Vijay", and all of
    Vijay's mail is read. The total is 0 because of the unread filter, not
    the sender. So no near spelling is tried, and nothing says «Vijaya»."""
    p = world["p"]
    me = _member(A, p.org_a, "feature:email")
    read = _mail(p.admin_engine, org=p.org_a, account=world["box_a"], name="Vijay Raghav",
                 address=f"vijay@vr-{TAG}.test", subject="Read already", body="done")
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE email_messages SET is_read = true WHERE id = CAST(:i AS uuid)"),
                  {"i": read})
    _mail(p.admin_engine, org=p.org_a, account=world["box_a"], name="Vijaya Kumar",
          address=f"vk@vk-{TAG}.test", subject="Not read", body="new")
    flags("*")
    unread = await _inbox(p, me, "Vijay", is_read=False)
    assert unread["total"] == 0 and unread["emails"] == [] and "near" not in unread
    # Non-vacuity: with no other filter, the exact filter finds Vijay.
    every = await _inbox(p, me, "Vijay")
    assert [str(e["id"]) for e in every["emails"]] == [read] and "near" not in every


async def test_a_word_held_by_a_sender_name_tries_no_near_spelling(world, flags) -> None:
    """P1-a: «Sharath Kumar <sk@bms…>» holds "Sharath" in his name, and the
    exact filter reads the address only. The word is held, so no near
    spelling is tried, and Sharat's mail is not shown as a stand-in."""
    p = world["p"]
    me = _member(A, p.org_a, "feature:email")
    _mail(p.admin_engine, org=p.org_a, account=world["box_a"], name="Sharath Kumar",
          address=f"sk@bms-{TAG}.test", subject="Quote", body="the quote")
    flags("*")
    got = await _inbox(p, me, "Sharath")
    assert "near" not in got and world["mail_a"] not in {str(e["id"]) for e in got["emails"]}


async def test_a_sender_of_another_mailbox_never_answers(world, flags) -> None:
    p = world["p"]
    flags("*")
    # "Sicretname" is near «Secretname», and no address or name holds it, so
    # the sender alone has no mail (P1-a) and a near spelling may be tried.
    hidden = await _inbox(p, _member(A, p.org_a, "feature:email"), "Sicretname")
    assert hidden["total"] == 0 and "near" not in hidden
    assert "Secretname" not in json.dumps(hidden, default=str)
    # Non-vacuity: B, who owns the mailbox, gets the near spelling.
    own = await _inbox(p, _member(B, p.org_a, "feature:email"), "Sicretname")
    assert own["near"] == {"word": "Sicretname", "as": "Secretname"}
    assert [str(e["id"]) for e in own["emails"]] == [world["mail_b"]]


# ── The ladder: GET /search/company ─────────────────────────────────────────

@pytest.fixture
def roomy(monkeypatch):
    """A cold scratch database is slower than the route's budget, and a
    source or a lookup cut off by its deadline gives nothing. Room here."""
    from gateway.retrieval import company

    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 60.0)
    monkeypatch.setattr(company, "NEAR_LOOKUP_S", 30.0)
    monkeypatch.setattr(company, "BROADEN_BUDGET_S", 120.0)
    monkeypatch.setattr(company, "ANSWER_CAP_S", 240.0)


async def _ask(p: Any, user: UserContext, q: str) -> dict[str, Any]:
    from tests.unit.test_company_search_r8 import _ask as ask

    return await ask(p, user, {"q": q, "apps": "crm,email"})


async def test_sharath_3d_printer_finds_the_deal_and_the_mail_in_one_call(
        world, flags, roomy, caplog) -> None:
    p = world["p"]
    flags("*")
    with caplog.at_level(logging.INFO, logger="gateway.retrieval.company"):
        answer = await _ask(p, _member(A, p.org_a, *ALL), "Sharath 3D printer")
    assert answer["skipped"] == []
    assert not [r for r in caplog.records if "near lookup skipped" in r.getMessage()]
    near = answer["near"][0]
    assert (near["word"], near["as"]) == ("Sharath", "Sharat") and near["hits"] >= 2
    hits = {h["id"]: h for h in answer["hits"]}
    assert world["deal"] in hits and world["mail_a"] in hits
    # "3D" alone found the mail too. Its near copy holds «Sharat», 3 of 3
    # words, and the word copy 2, so the near copy stays (review P2-d).
    mail = hits[world["mail_a"]]
    assert (mail["matched_by"], mail["as"], mail["covers"], mail["of"]) == (
        "near", "Sharat", 3, 3)
    # B's mail never reaches A, as a hit or a word.
    assert world["mail_b"] not in str(answer) and "Secretname" not in str(answer)


async def test_the_order_older_than_the_senders_newest_mail_comes_back(
        world, flags, roomy) -> None:
    """Round 3 (P2-1): Sharat sent 6 newer mails about lunch. The near ask
    keeps the other words of the query beside the sender, so the printer
    order comes back, and no lunch mail is tagged near."""
    p = world["p"]
    lunch = [_mail(p.admin_engine, org=p.org_a, account=world["box_a"],
                   name="Sharat Satyanarayana", address=NEUROPEEK,
                   subject=f"Lunch {i}", body="see you at noon") for i in range(6)]
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "Sharath 3D printer")
    hits = {h["id"]: h for h in answer["hits"]}
    assert world["mail_a"] in hits and hits[world["mail_a"]]["matched_by"] == "near"
    assert not set(lunch) & set(hits)


async def test_another_sharath_in_the_first_pass_still_finds_sharat(
        world, flags, roomy) -> None:
    """Production, 2026-11-14: «Sharath BMS» is the contact of «Twin Dragon 300
    - 3D Printer Hardware», so the first pass finds that deal. Its text holds 2
    of the 3 words: a miss of D-FIND-4. The second pass still runs, finds the
    deal of «Sharat» and his mail, and the first hit stays exact."""
    p = world["p"]
    bms = await _make(p, p.org_a, "/crm/contacts", {
        "first_name": "Sharath", "last_name": "BMS Purchase Dept"})
    twin = await _make(p, p.org_a, "/crm/deals", {"name": "Twin Dragon 300 - 3D Printer Hardware"})
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deal_contacts (organization_id, deal_id, contact_id, is_primary) "
            "VALUES (CAST(:o AS uuid), CAST(:d AS uuid), CAST(:c AS uuid), true)"),
            {"o": p.org_a, "d": twin, "c": bms})
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "Sharath 3D printer")
    hits = {h["id"]: h for h in answer["hits"]}
    assert (hits[twin]["matched_by"], hits[twin]["covers"], hits[twin]["of"]) == ("exact", 2, 3)
    assert world["deal"] in hits and world["mail_a"] in hits
    assert [(e["word"], e["as"]) for e in answer["near"]] == [("Sharath", "Sharat")]
    assert [b["word"] for b in answer["broadened"]] == ["sharath", "3d", "printer"]
    # The zero rule of KR-ZERO would stop here: the flag off asks nothing again.
    flags("")
    off = await _ask(p, _member(A, p.org_a, *ALL), "Sharath 3D printer")
    assert "near" not in off and "broadened" not in off and world["deal"] not in str(off)


async def test_nuropeak_finds_the_deal_and_the_mail_from_its_domain(
        world, flags, roomy) -> None:
    p = world["p"]
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "nuropeak")
    spellings = {e["as"] for e in answer["near"]}
    assert spellings == {"Neuropeak", "neuropeek"}
    hits = {h["id"]: h for h in answer["hits"]}
    assert world["deal"] in hits and world["mail_a"] in hits
    deal, mail = hits[world["deal"]], hits[world["mail_a"]]
    assert (deal["matched_by"], deal["word"], deal["as"]) == ("near", "nuropeak", "Neuropeak")
    assert (mail["matched_by"], mail["word"], mail["as"]) == ("near", "nuropeak", "neuropeek")


async def test_member_b_gets_the_deal_and_never_the_mail_of_a(world, flags, roomy) -> None:
    p = world["p"]
    flags("*")
    answer = await _ask(p, _member(B, p.org_a, *ALL), "Sharath 3D printer")
    ids = {h["id"] for h in answer["hits"]}
    assert world["deal"] in ids
    assert world["mail_a"] not in ids and NEUROPEEK not in str(answer)


async def test_flag_off_the_ladder_tries_no_near_spelling(world, flags, roomy) -> None:
    p = world["p"]
    flags("")
    answer = await _ask(p, _member(A, p.org_a, *ALL), "nuropeak")
    assert "near" not in answer and answer["hits"] == []


# ── resolve_name: GET /search/names (FIND-5, task 3242) ─────────────────────

@pytest.fixture
async def lead(world) -> str:
    """A lead «Sharat Satyanarayana» of org A. The ladder tests above assert
    on their hit sets, so the world itself holds no lead."""
    p = world["p"]
    return await _make(p, p.org_a, "/crm/leads", {"lead_name": "Sharat Satyanarayana"})


@pytest.fixture
def roomy_names(monkeypatch):
    """A cold scratch database is slower than the route's one deadline."""
    from gateway.retrieval import names

    monkeypatch.setattr(names, "NAMES_BUDGET_S", 60.0)


async def _names(p: Any, user: UserContext, name: str, **params: str) -> tuple[int, Any]:
    """``GET /search/names`` through FastAPI, as a request runs it: the tenant
    comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.retrieval import names

    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _caller() -> UserContext:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(names.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://names.test") as client,
        ):
            res = await client.get("/search/names", params={"name": name, **params})
    finally:
        release_tenant(scope)
    return res.status_code, res.json()


async def _resolve(p: Any, user: UserContext, name: str, **params: str) -> dict[str, Any]:
    status, body = await _names(p, user, name, **params)
    assert status == 200, body
    return body


def _rows(answer: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    return {(r["app"], r["kind"], r["id"], r["match"]) for r in answer["rows"]}


def _person(p: Any, org: str, name: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "SELECT id FROM people WHERE name = :n AND organization_id = CAST(:o AS uuid)"),
            {"n": name, "o": org}).scalar_one())


async def test_sharat_gives_the_lead_the_contact_and_the_sender(
        world, lead, flags, roomy_names) -> None:
    from gateway.retrieval.sources import crm_href

    p = world["p"]
    flags("*")
    answer = await _resolve(p, _member(A, p.org_a, *ALL), "Sharat")
    assert answer["skipped"] == [] and answer["near_skipped"] == []
    assert _rows(answer) == {
        ("crm", "lead", lead, "exact"), ("crm", "contact", world["contact"], "exact"),
        ("email", "sender", NEUROPEEK, "exact")}
    links = {r["id"]: r["href"] for r in answer["rows"]}
    assert links[lead] == crm_href("lead", lead)
    assert links[world["contact"]] == crm_href("contact", world["contact"])
    assert links[NEUROPEEK] == f"/email?email={world['mail_a']}"
    assert {r["name"] for r in answer["rows"]} == {"Sharat Satyanarayana"}
    # Three records hold the name: the member is asked which.
    assert answer["verdict"] == "several"
    # The deal matched "Sharat" through its contact. It has no name of its own.
    assert world["deal"] not in json.dumps(answer)


async def test_sharath_gives_near_rows_that_say_so(world, lead, flags, roomy_names) -> None:
    p = world["p"]
    flags("*")
    answer = await _resolve(p, _member(A, p.org_a, *ALL), "Sharath")
    assert _rows(answer) == {
        ("crm", "lead", lead, "near"), ("crm", "contact", world["contact"], "near"),
        ("email", "sender", NEUROPEEK, "near")}
    assert {r["as"] for r in answer["rows"]} == {"Sharat"}
    assert answer["verdict"] == "near"


async def test_a_sender_of_member_b_never_shows_for_member_a(world, flags, roomy_names) -> None:
    p = world["p"]
    flags("*")
    for asked in ("Secretname", "Sicretname"):
        mine = await _resolve(p, _member(A, p.org_a, *ALL), asked)
        assert mine["rows"] == [] and mine["verdict"] == "none", asked
        assert "secret@" not in json.dumps(mine) and world["mail_b"] not in json.dumps(mine)
    # Non-vacuity: B, who owns the mailbox, gets the sender.
    own = await _resolve(p, _member(B, p.org_a, *ALL), "Secretname")
    assert _rows(own) == {("email", "sender", f"secret@hidden-{TAG}.test", "exact")}
    near = await _resolve(p, _member(B, p.org_a, *ALL), "Sicretname")
    assert _rows(near) == {("email", "sender", f"secret@hidden-{TAG}.test", "near")}


async def test_the_members_own_sent_mail_gives_no_sender(world, flags, roomy_names) -> None:
    """A sent mail comes from a mailbox of the member. Its address is the
    mailbox's own, not the login, and it still gives no sender row."""
    from tests.unit.test_email_keep_separate import _address

    p = world["p"]
    box = _address(p.admin_engine, world["box_a"])
    sent = _mail(p.admin_engine, org=p.org_a, account=world["box_a"], name="Nandita Owner",
                 address=box, subject="My quote", body="sent by me")
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE email_messages SET folder = 'sent' WHERE id = CAST(:i AS uuid)"),
                  {"i": sent})
    _mail(p.admin_engine, org=p.org_a, account=world["box_a"], name="Nandita Client",
          address=f"nandita@client-{TAG}.test", subject="Your quote", body="thanks")
    flags("*")
    answer = await _resolve(p, _member(A, p.org_a, *ALL), "Nandita")
    assert _rows(answer) == {("email", "sender", f"nandita@client-{TAG}.test", "exact")}
    assert box not in json.dumps(answer) and sent not in json.dumps(answer)


async def test_org_b_names_never_show_in_org_a(world, flags, roomy_names) -> None:
    p = world["p"]
    flags("*")
    for asked in ("Shobhana", "Rivalname", "Bhaskar"):
        mine = await _resolve(p, _member(A, p.org_a, *ALL), asked)
        assert mine["rows"] == [], asked
    theirs = await _resolve(p, _member(C, p.org_b, *ALL), "Shobhana")
    assert {(r["app"], r["kind"], r["match"]) for r in theirs["rows"]} == {
        ("crm", "contact", "exact")}
    rival = await _resolve(p, _member(C, p.org_b, *ALL), "Rivalname")
    assert _rows(rival) == {("email", "sender", f"rival@orgb-{TAG}.test", "exact")}
    bhaskar = await _resolve(p, _member(C, p.org_b, *ALL), "Bhaskar")
    assert _rows(bhaskar) == {("people", "person", _person(p, p.org_b, "Bhaskar Rival"), "exact")}


async def test_a_member_the_crm_refuses_gets_no_crm_row_and_no_trace(
        world, lead, flags, roomy_names) -> None:
    p = world["p"]
    flags("*")
    answer = await _resolve(p, _member(B, p.org_a, "feature:crm", "feature:email"), "Sharat")
    assert answer["rows"] == [] and answer["verdict"] == "none"
    assert answer["skipped"] == [{"app": "crm", "reason": "refused"}]
    said = json.dumps(answer)
    for ident in (lead, world["contact"], world["deal"]):
        assert ident not in said
    assert "Satyanarayana" not in said


async def test_people_names_need_feature_people(world, flags, roomy_names) -> None:
    p = world["p"]
    flags("*")
    person = _person(p, p.org_a, "Gitanjali Peoplea")
    answer = await _resolve(p, _member(A, p.org_a, *ALL), "Gitanjali")
    assert _rows(answer) == {("people", "person", person, "exact")}
    assert answer["rows"][0]["href"] == f"/people/{person}"
    near = await _resolve(p, _member(A, p.org_a, *ALL), "Gitanjaly")
    assert _rows(near) == {("people", "person", person, "near")}
    blind = await _resolve(p, _member(A, p.org_a, "feature:email", "feature:crm",
                                      "admin:access:manage"), "Gitanjali")
    assert blind["rows"] == [] and "people" not in blind["searched"]
    assert person not in json.dumps(blind)


async def test_the_verdict_is_one_only_for_exactly_one_exact_record(
        world, flags, roomy_names) -> None:
    p = world["p"]
    flags("*")
    me = _member(A, p.org_a, *ALL)
    assert (await _resolve(p, me, "Gitanjali"))["verdict"] == "one"
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (name, department, source, organization_id) "
            "VALUES ('Gitanjali Second', 'Ops', :s, CAST(:o AS uuid))"),
            {"s": f"near-{TAG}", "o": p.org_a})
    assert (await _resolve(p, me, "Gitanjali"))["verdict"] == "several"
    # A near name alone is never "one".
    assert (await _resolve(p, me, "Gitanjaly"))["verdict"] == "near"


async def test_flag_off_the_name_route_answers_404(world, flags, roomy_names) -> None:
    p = world["p"]
    flags("")
    status, _body = await _names(p, _member(A, p.org_a, *ALL), "Sharat")
    assert status == 404
