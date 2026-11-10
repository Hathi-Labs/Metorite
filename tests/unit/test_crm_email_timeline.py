"""CRM · the email→CRM timeline join (WS-26d-email).

Spec: ``project-docs/specs/crm_app.md`` §9 "WS-26d-email" · done-when 1-6.

**The rule this file exists to defend.** The CRM is org-visible to every
``feature:crm`` holder (D-CRM-3) while a mailbox is owner-scoped, so an
unscoped join publishes one member's inbox to the whole company. The join is
therefore **caller-scoped, never record-scoped**: the timeline shows email *the
caller can already read*, not email *the record has*.

Two of the cases below (`test_a_holder_with_no_mailbox_sees_no_email` and
`test_two_holders_each_see_only_their_own_account`) are the mutation fence for
that rule. Delete the ``_email_account_scope(...)`` call from
``activities._email_entries`` and **both must go red** — one because the
mailboxless caller starts seeing mail, the other because each holder starts
seeing the other's. If a change to this file or to ``_crm_fakes.py`` leaves
either of them green under that mutation, the fence is gone and the file is
worth less than nothing: it would certify the hole.

Hermetic, in ``test_crm_routes.py``'s convention: no Postgres, no TestClient,
route functions called directly with ``_get_db`` monkeypatched onto the SUT
submodule, against the shared ``_crm_fakes.FakeCrmDB``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import pipeline as crm_pipeline
from gateway.routes.crm import records as crm_records
from gateway.routes.crm.core import CONTACTS, DEALS, LEADS, ORGANIZATIONS

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

#: Two ``feature:crm`` holders. Both may read every CRM record; neither may
#: read the other's mail. That asymmetry is the whole slice.
PRIYA = crm_user("priya@fracktal.in")
ARJUN = crm_user("arjun@fracktal.in")

#: Somebody who holds the feature and owns no mailbox at all.
NOMAIL = crm_user("nomail@fracktal.in")

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake, (crm_core, crm_records, crm_activities, crm_pipeline))
    return fake


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 8, day, hour, 0, tzinfo=UTC)


def _account(db: FakeCrmDB, owner: str, account_id: str) -> str:
    """One mailbox, owned by one person — what ``_email_account_scope`` reads."""
    db.seed("email_accounts", id=account_id, user_id=owner)
    return account_id


def _message(
    db: FakeCrmDB,
    account_id: str,
    address: str,
    *,
    thread_id: str | None = "t-1",
    received_at: datetime | None = None,
    subject: str = "Quote please",
    name: str = "Sender",
    snippet: str = "…",
    folder: str = "INBOX",
) -> Any:
    return db.seed(
        "email_messages",
        account_id=account_id,
        thread_id=thread_id,
        from_address={"name": name, "email": address},
        subject=subject,
        snippet=snippet,
        folder=folder,
        received_at=received_at or _at(3),
    )


def _lead(db: FakeCrmDB, email: str | None = "buyer@acme.example") -> Any:
    return db.seed("crm_leads", lead_name="Acme", email=email)


def _emails(response: Any) -> list[dict]:
    return [e.email_thread for e in response.entries if e.kind == "email_thread"]


# ── done-when 1 and 2 — the scoping rule, and the mutation fence ────────────

async def test_a_holder_with_no_mailbox_sees_no_email(db: FakeCrmDB) -> None:
    """A ``feature:crm`` holder who owns NO email account sees ZERO email
    entries on every CRM timeline.

    ⚠️ MUTATION FENCE. The mail below is real, in scope by address, and sitting
    in somebody else's account. Deleting the scope predicate makes this case
    return it.
    """
    priya_box = _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    _message(db, priya_box, "buyer@acme.example")

    for entity, record in (
        (LEADS, lead),
        (CONTACTS, db.seed("crm_contacts", first_name="Buyer",
                           email="buyer@acme.example")),
        (ORGANIZATIONS, db.seed("crm_companies", name="Acme",
                                email="buyer@acme.example")),
        (DEALS, db.seed("crm_deals", name="Acme deal", lead_id=lead.id)),
    ):
        timeline = await crm_activities._timeline(
            entity, str(record.id), 100, NOMAIL,
        )
        assert _emails(timeline) == [], entity.slug


async def test_two_holders_each_see_only_their_own_account(db: FakeCrmDB) -> None:
    """Two accounts, two holders, ONE record: each sees only their own threads.

    ⚠️ MUTATION FENCE, and the reason a single-account test cannot stand in for
    it — with one mailbox seeded, "scoped to the caller" and "scoped to
    nothing" give the same answer.
    """
    priya_box = _account(db, PRIYA.email, "acct-priya")
    arjun_box = _account(db, ARJUN.email, "acct-arjun")
    lead = _lead(db)
    _message(db, priya_box, "buyer@acme.example", thread_id="thread-priya")
    _message(db, arjun_box, "buyer@acme.example", thread_id="thread-arjun")

    priya_sees = _emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    ))
    arjun_sees = _emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, ARJUN,
    ))

    assert [t["thread_id"] for t in priya_sees] == ["thread-priya"]
    assert [t["thread_id"] for t in arjun_sees] == ["thread-arjun"]
    assert priya_sees[0]["account_id"] == "acct-priya"
    assert arjun_sees[0]["account_id"] == "acct-arjun"


async def test_the_scope_predicate_is_the_email_app_s_own(db: FakeCrmDB) -> None:
    """The copied predicate is byte-identical to the email app's fragment.

    §9 decided to COPY rather than import (D-CRM-4) — which is only safe while
    the copy stays a copy. Compared against the origin's own output rather than
    against a string literal, so a change over there fails here.
    """
    from gateway.routes.email.core import _account_scope

    ours: dict[str, Any] = {"uid": PRIYA.email}
    theirs: dict[str, Any] = {"uid": PRIYA.email}
    assert crm_activities._email_account_scope(None, ours) == _account_scope(
        None, theirs,
    )
    assert crm_activities._email_account_scope("a-1", ours) == _account_scope(
        "a-1", theirs,
    )
    assert ours == theirs


async def test_the_query_carries_the_scope_and_the_caller(db: FakeCrmDB) -> None:
    """Structural: the statement names the scope subquery and binds the CALLER.

    A behavioural case cannot see *whose* identity was bound — the fake would
    agree with a query that hardcoded an address.
    """
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    _message(db, "acct-priya", "buyer@acme.example")

    await crm_activities._timeline(LEADS, str(lead.id), 100, PRIYA)

    [(statement, params)] = [
        call for call in db.calls if "email_messages" in call[0]
    ]
    assert "SELECT id FROM email_accounts WHERE user_id = :uid" in statement
    assert params["uid"] == PRIYA.email


# ── done-when 3 — the third kind, with a payload of its own ─────────────────

async def test_the_entry_is_a_third_kind_with_its_own_payload(db: FakeCrmDB) -> None:
    """``kind='email_thread'`` and a populated ``email_thread`` field.

    The payload half is not decoration: the browser's ``ActivityEntry`` opens
    with ``entry.activity!``, so an email entry that arrived with ``kind`` set
    and no payload of its own would render an empty row at best and throw at
    worst — and the ``!`` hides that from tsc.
    """
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    _message(
        db, "acct-priya", "Buyer@Acme.Example", thread_id="thread-1",
        subject="Re: quote", name="Buyer", snippet="Can you do 40 units",
        received_at=_at(4, 14),
    )
    db.seed(
        "email_thread_status", account_id="acct-priya", thread_id="thread-1",
        status="NEEDS_REPLY",
    )

    [entry] = [
        e for e in (await crm_activities._timeline(
            LEADS, str(lead.id), 100, PRIYA,
        )).entries
        if e.kind == "email_thread"
    ]

    assert entry.activity is None and entry.status_change is None
    assert entry.at == _at(4, 14).isoformat()
    assert entry.email_thread == {
        "thread_id": "thread-1",
        "account_id": "acct-priya",
        "message_id": entry.email_thread["message_id"],
        "subject": "Re: quote",
        "snippet": "Can you do 40 units",
        "folder": "INBOX",
        "from_name": "Buyer",
        "from_email": "Buyer@Acme.Example",
        "received_at": _at(4, 14).isoformat(),
        "status": "NEEDS_REPLY",
    }


async def test_a_thread_nobody_classified_has_no_status(db: FakeCrmDB) -> None:
    """The status badge is a LEFT join — absent, not an error and not a guess."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    _message(db, "acct-priya", "buyer@acme.example", thread_id="thread-1")
    # A status row for a DIFFERENT account, same thread id: the join is
    # composite, so this must not attach.
    _account(db, ARJUN.email, "acct-arjun")
    db.seed(
        "email_thread_status", account_id="acct-arjun", thread_id="thread-1",
        status="DONE",
    )

    [thread] = _emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    ))
    assert thread["status"] is None


# ── The unit is the THREAD, not the message ────────────────────────────────

async def test_a_conversation_is_one_entry_showing_its_newest_message(
    db: FakeCrmDB,
) -> None:
    """Five messages in one thread → ONE entry, carrying the newest.

    A row-per-message timeline double-counts every conversation, and the
    conversation is already the unit of classification and snooze across the
    email app.
    """
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    for day, subject in ((1, "first"), (5, "newest"), (3, "middle")):
        _message(
            db, "acct-priya", "buyer@acme.example", thread_id="thread-1",
            subject=subject, received_at=_at(day),
        )

    threads = _emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    ))
    assert len(threads) == 1
    assert threads[0]["subject"] == "newest"


async def test_messages_with_no_thread_id_stand_for_themselves(
    db: FakeCrmDB,
) -> None:
    """``thread_id`` is nullable. Grouping on it raw would fold every
    un-threaded message in an account into a single entry, because Postgres
    treats NULLs as equal for ``DISTINCT ON`` — so the key coalesces to the
    message id, exactly as the email app's contact card does."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    for day in (1, 2, 3):
        _message(
            db, "acct-priya", "buyer@acme.example", thread_id=None,
            subject=f"day {day}", received_at=_at(day),
        )

    threads = _emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    ))
    assert [t["subject"] for t in threads] == ["day 3", "day 2", "day 1"]


async def test_the_same_thread_in_two_accounts_is_two_entries(
    db: FakeCrmDB,
) -> None:
    """The grouping key is ``(account_id, thread)``, not the thread alone: two
    colleagues on the same conversation hold two different copies of it."""
    _account(db, PRIYA.email, "acct-priya")
    db.seed("email_accounts", id="acct-priya-2", user_id=PRIYA.email)
    lead = _lead(db)
    _message(db, "acct-priya", "buyer@acme.example", thread_id="shared")
    _message(db, "acct-priya-2", "buyer@acme.example", thread_id="shared")

    threads = _emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    ))
    assert sorted(t["account_id"] for t in threads) == ["acct-priya", "acct-priya-2"]


# ── Which records join, and by what ────────────────────────────────────────

@pytest.mark.parametrize(
    ("entity", "table", "columns"),
    [
        (LEADS, "crm_leads", {"lead_name": "Acme"}),
        (CONTACTS, "crm_contacts", {"first_name": "Buyer"}),
        (ORGANIZATIONS, "crm_companies", {"name": "Acme"}),
    ],
)
async def test_records_with_an_email_column_join_by_it(
    db: FakeCrmDB, entity: Any, table: str, columns: dict,
) -> None:
    _account(db, PRIYA.email, "acct-priya")
    record = db.seed(table, email="buyer@acme.example", **columns)
    _message(db, "acct-priya", "buyer@acme.example")

    assert len(_emails(await crm_activities._timeline(
        entity, str(record.id), 100, PRIYA,
    ))) == 1


async def test_a_deal_reaches_email_through_its_lead_and_its_contacts(
    db: FakeCrmDB,
) -> None:
    """``crm_deals`` has no ``email`` column. v1 takes BOTH routes to one —
    the originating ``lead_id`` and the linked contacts — unioned, matching how
    the timeline already inherits the lead's activity history (§5.3). The lead's
    mail is labelled ``lead`` so the UI can say where it came from."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db, "lead@acme.example")
    deal = db.seed("crm_deals", name="Acme deal", lead_id=lead.id)
    contact = db.seed(
        "crm_contacts", first_name="Priya", email="champion@acme.example",
    )
    db.seed("crm_deal_contacts", deal_id=deal.id, contact_id=contact.id)
    _message(db, "acct-priya", "lead@acme.example", thread_id="from-lead")
    _message(db, "acct-priya", "champion@acme.example", thread_id="from-contact")
    # Same domain, nobody's address on this deal — organizations deliberately
    # do NOT join by domain in v1 and neither does anything else here.
    _message(db, "acct-priya", "stranger@acme.example", thread_id="unrelated")

    entries = [
        e for e in (await crm_activities._timeline(
            DEALS, str(deal.id), 100, PRIYA,
        )).entries
        if e.kind == "email_thread"
    ]
    assert {e.email_thread["thread_id"]: e.origin for e in entries} == {
        "from-lead": "lead",
        "from-contact": "own",
    }


async def test_an_inherited_thread_is_not_counted_twice(db: FakeCrmDB) -> None:
    """A deal's timeline unions its lead's history. Email is resolved once for
    the whole record, so the lead's threads arrive once — not once for the deal
    and again for the inherited lead source."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db, "lead@acme.example")
    deal = db.seed("crm_deals", name="Acme deal", lead_id=lead.id)
    _message(db, "acct-priya", "lead@acme.example", thread_id="from-lead")

    threads = _emails(await crm_activities._timeline(
        DEALS, str(deal.id), 100, PRIYA,
    ))
    assert [t["thread_id"] for t in threads] == ["from-lead"]


async def test_v1_matches_inbound_only(db: FakeCrmDB) -> None:
    """Outbound matching (``to_addresses``) is deferred: it needs a GIN index
    that does not exist. A message merely ADDRESSED to the record must not
    appear, or "deferred" would quietly be "half-built"."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    db.seed(
        "email_messages", account_id="acct-priya", thread_id="outbound",
        from_address={"name": "Priya", "email": PRIYA.email},
        to_addresses=[{"name": "Buyer", "email": "buyer@acme.example"}],
        subject="Our quote", snippet="", folder="SENT", received_at=_at(6),
    )

    assert _emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    )) == []


# ── done-when 4 — nothing to match is not an error ─────────────────────────

@pytest.mark.parametrize("address", [None, ""])
async def test_a_record_with_no_email_returns_the_same_shape(
    db: FakeCrmDB, address: str | None,
) -> None:
    """NULL email → the same ``TimelineResponse``, no email entries, and — the
    part that matters — **no query at all**. An empty ``IN ()`` is a syntax
    error, and the failure mode a fallback would produce is the whole mailbox
    on a record that names nobody."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db, address)
    _message(db, "acct-priya", "buyer@acme.example")
    db.seed(
        "crm_activities", type="note", lead_id=lead.id, created_by="a@b.in",
        created_at=_at(2), occurred_at=_at(2),
    )

    timeline = await crm_activities._timeline(LEADS, str(lead.id), 100, PRIYA)

    assert [e.kind for e in timeline.entries] == ["activity"]
    assert not db.statements_touching("email_messages")


async def test_an_address_that_matches_nothing_is_not_an_error(
    db: FakeCrmDB,
) -> None:
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db, "nobody@nowhere.example")
    _message(db, "acct-priya", "buyer@acme.example")

    timeline = await crm_activities._timeline(LEADS, str(lead.id), 100, PRIYA)
    assert timeline.entries == []


async def test_the_address_comparison_is_case_insensitive(db: FakeCrmDB) -> None:
    """Case is not normalised on write (``email_ingestion/persist.py`` stamps
    the provider's spelling), so both sides lower at query time."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db, "Buyer@Acme.Example")
    _message(db, "acct-priya", "buyer@ACME.example")

    assert len(_emails(await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    ))) == 1


# ── done-when 5 — bounded per source, before the merge ─────────────────────

async def test_a_chatty_mailbox_cannot_evict_the_record_s_own_history(
    db: FakeCrmDB,
) -> None:
    """Email gets at most HALF the merged page, rounded up.

    ⚠️ This case previously asserted the bug. Bounding each source at ``limit``
    reads like enough, but the merge sorts newest-first and THEN truncates to
    ``limit`` — so ten recent threads at limit 3 returned three threads and the
    record's note vanished. Ten emails is an ordinary week on an active deal
    and the CRM agent's `get_timeline` defaults to 20, so "what's the story
    with this deal?" answered with nothing but mail.

    Ten threads (all newer) + one older note, at limit 3 → 2 threads + the note.
    """
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    for index in range(10):
        _message(
            db, "acct-priya", "buyer@acme.example", thread_id=f"thread-{index}",
            received_at=_at(20 - index),
        )
    db.seed(
        "crm_activities", type="note", subject="Rang them", lead_id=lead.id,
        created_by="a@b.in", created_at=_at(2), occurred_at=_at(2),
    )

    timeline = await crm_activities._timeline(LEADS, str(lead.id), 3, PRIYA)

    assert len(timeline.entries) == 3
    # The note survives ten newer emails — that is the property.
    assert [e.kind for e in timeline.entries] == [
        "email_thread", "email_thread", "activity",
    ]
    assert timeline.entries[2].activity["subject"] == "Rang them"
    # Two threads, and they are the NEWEST two, not an arbitrary pair.
    assert [t["thread_id"] for t in _emails(timeline)] == ["thread-0", "thread-1"]
    # The cap is applied in SQL, not by slicing a full read in Python.
    [(statement, params)] = [c for c in db.calls if "email_messages" in c[0]]
    assert "LIMIT :limit" in statement
    assert params["limit"] == 2


@pytest.mark.parametrize(
    ("limit", "budget"), [(1, 1), (2, 1), (3, 2), (20, 10), (100, 50), (500, 250)],
)
def test_the_email_budget_is_half_the_page_rounded_up(
    limit: int, budget: int,
) -> None:
    """Rounded UP, so a limit of 1 still admits email at all, and so the half
    that may end up empty is the RESERVED one — a record with no activity still
    fills the page with mail, because the other sources just return less and
    the truncation takes what is there."""
    assert crm_activities._email_budget(limit) == budget


async def test_a_record_with_no_activity_still_fills_the_page_with_email(
    db: FakeCrmDB,
) -> None:
    """The reserve is a floor for the other sources, never a ceiling the email
    half is padded up to. Nothing is held back for history that does not
    exist."""
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    for index in range(6):
        _message(
            db, "acct-priya", "buyer@acme.example", thread_id=f"thread-{index}",
            received_at=_at(20 - index),
        )

    timeline = await crm_activities._timeline(LEADS, str(lead.id), 4, PRIYA)
    assert len(_emails(timeline)) == 2


# ── done-when 6 — the index ────────────────────────────────────────────────

def test_a_migration_indexes_the_sender_address_expression() -> None:
    """Some migration indexes ``LOWER(from_address->>'email')`` on
    ``email_messages``, scoped by account.

    Deliberately does NOT assert a migration NUMBER — parallel sessions land
    migrations on the same day, and a pinned "highest migration" assertion is a
    known deploy-blocker in this repo (R1).
    """
    wanted = re.compile(
        r"CREATE\s+INDEX[^;]*?\bON\s+email_messages\s*\(\s*account_id\s*,\s*"
        r"LOWER\(\s*from_address->>'email'\s*\)\s*\)",
        re.I | re.S,
    )
    migrations = sorted((REPO_ROOT / "infra" / "postgres").glob("*.sql"))
    assert migrations, "no migrations found — is REPO_ROOT wrong?"
    assert any(
        wanted.search(path.read_text(encoding="utf-8")) for path in migrations
    ), (
        "no migration indexes (account_id, LOWER(from_address->>'email')) on "
        "email_messages — the CRM timeline runs that predicate on every record "
        "open and the two FTS GINs bury the address inside a to_tsvector"
    )


# ── ADDED 8 — the `_timeline` regression floor ─────────────────────────────
#
# `_timeline` had ZERO direct coverage before this file (§10's claim that
# test_crm_routes.py pins it was not true — that file exercises _log_activity,
# patch_activity and delete_activity only). The email join changes its
# signature and its body, so the behaviour it already had is pinned here.

async def test_activities_and_status_changes_still_merge(db: FakeCrmDB) -> None:
    lead = _lead(db)
    db.seed(
        "crm_activities", type="note", subject="Rang them", lead_id=lead.id,
        created_by="priya@fracktal.in", created_at=_at(2), occurred_at=_at(2),
    )
    db.seed(
        "crm_status_changes", entity_type="lead", entity_id=lead.id,
        from_status="New", to_status="Qualified", changed_by="priya@fracktal.in",
        changed_at=_at(3), dwell_seconds=86_400,
    )

    entries = (await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    )).entries

    assert [e.kind for e in entries] == ["status_change", "activity"]
    assert entries[0].status_change["to_status"] == "Qualified"
    assert entries[0].status_change["dwell_seconds"] == 86_400
    assert entries[1].activity["subject"] == "Rang them"
    assert all(e.origin == "own" for e in entries)


async def test_a_deal_inherits_its_lead_s_history_labelled(db: FakeCrmDB) -> None:
    """§5.3 — everything said before conversion was said about this deal, and
    losing it at the conversion boundary is what ``lead_id`` exists to prevent.
    The inherited rows are LABELLED rather than passed off as the deal's own."""
    lead = _lead(db)
    deal = db.seed("crm_deals", name="Acme deal", lead_id=lead.id)
    db.seed(
        "crm_activities", type="note", subject="from the lead", lead_id=lead.id,
        created_by="a@b.in", created_at=_at(1), occurred_at=_at(1),
    )
    db.seed(
        "crm_activities", type="note", subject="from the deal", deal_id=deal.id,
        created_by="a@b.in", created_at=_at(2), occurred_at=_at(2),
    )
    db.seed(
        "crm_status_changes", entity_type="lead", entity_id=lead.id,
        from_status="New", to_status="Qualified", changed_by="a@b.in",
        changed_at=_at(3), dwell_seconds=None,
    )

    entries = (await crm_activities._timeline(
        DEALS, str(deal.id), 100, PRIYA,
    )).entries

    assert [(e.kind, e.origin) for e in entries] == [
        ("status_change", "lead"),
        ("activity", "own"),
        ("activity", "lead"),
    ]


async def test_the_merged_stream_is_newest_first(db: FakeCrmDB) -> None:
    _account(db, PRIYA.email, "acct-priya")
    lead = _lead(db)
    db.seed(
        "crm_activities", type="note", subject="oldest", lead_id=lead.id,
        created_by="a@b.in", created_at=_at(1), occurred_at=_at(1),
    )
    _message(db, "acct-priya", "buyer@acme.example", received_at=_at(2))
    db.seed(
        "crm_status_changes", entity_type="lead", entity_id=lead.id,
        from_status="New", to_status="Qualified", changed_by="a@b.in",
        changed_at=_at(3), dwell_seconds=None,
    )

    entries = (await crm_activities._timeline(
        LEADS, str(lead.id), 100, PRIYA,
    )).entries
    assert [e.kind for e in entries] == ["status_change", "email_thread", "activity"]
    assert [e.at for e in entries] == sorted(
        [e.at for e in entries], reverse=True,
    )


async def test_the_merged_stream_is_truncated_to_the_limit(db: FakeCrmDB) -> None:
    lead = _lead(db)
    for day in range(1, 6):
        db.seed(
            "crm_activities", type="note", subject=f"day {day}", lead_id=lead.id,
            created_by="a@b.in", created_at=_at(day), occurred_at=_at(day),
        )

    timeline = await crm_activities._timeline(LEADS, str(lead.id), 2, PRIYA)
    assert [e.activity["subject"] for e in timeline.entries] == ["day 5", "day 4"]


async def test_a_missing_record_is_a_404_not_an_empty_timeline(
    db: FakeCrmDB,
) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await crm_activities._timeline(
            LEADS, "00000000-0000-0000-0000-000000000000", 100, PRIYA,
        )
    assert raised.value.status_code == 404


# ── The plumbing: the four routes stop dropping `user` ─────────────────────

def _record_reaching(db: FakeCrmDB, table: str) -> Any:
    """One record of *table* that reaches ``buyer@acme.example``.

    ``crm_deals`` carries no ``email`` column, so a deal reaches an address the
    only way a deal can — through a linked contact.
    """
    if table == "crm_deals":
        deal = db.seed("crm_deals", name="A")
        contact = db.seed(
            "crm_contacts", first_name="Buyer", email="buyer@acme.example",
        )
        db.seed("crm_deal_contacts", deal_id=deal.id, contact_id=contact.id)
        return deal
    columns = {
        "crm_leads": {"lead_name": "A"},
        "crm_contacts": {"first_name": "A"},
        "crm_companies": {"name": "A"},
    }[table]
    return db.seed(table, email="buyer@acme.example", **columns)


@pytest.mark.parametrize(
    ("route", "table"),
    [
        (crm_activities.lead_timeline, "crm_leads"),
        (crm_activities.deal_timeline, "crm_deals"),
        (crm_activities.contact_timeline, "crm_contacts"),
        (crm_activities.organization_timeline, "crm_companies"),
    ],
)
async def test_every_timeline_route_passes_the_caller_through(
    db: FakeCrmDB, route: Any, table: str,
) -> None:
    """All four routes already had ``user`` and dropped it. A route that still
    drops it would compile, return a timeline, and leak: ``_email_entries``
    would have no identity to scope by."""
    _account(db, PRIYA.email, "acct-priya")
    record = _record_reaching(db, table)
    _message(db, "acct-priya", "buyer@acme.example")

    response = await route(str(record.id), 100, PRIYA)
    assert len(_emails(response)) == 1

    [(_, params)] = [c for c in db.calls if "email_messages" in c[0]]
    assert params["uid"] == PRIYA.email
