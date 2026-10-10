"""CRM · lead → deal conversion (§3.7) — dedup, provenance, and the 409.

Spec: ``project-docs/specs/crm_app.md`` §3.7 · WS-26a done-when 5.

Conversion is the one place this app decides that two records are the same
person or the same company. Each of those decisions has exactly one rule and
each rule is one test here:

* the contact is the caller's choice, else an **email** match, else new;
* the organization is the caller's choice, else an **exact name** match, else
  new — and is skipped entirely when the lead names no company;
* the deal carries ``lead_id`` provenance and the contact as primary;
* the lead is stamped, moved to its won status, and can never convert twice.

Hermetic: no Postgres, no network, no TestClient.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import pipeline as crm_pipeline
from gateway.routes.crm import records as crm_records
from gateway.routes.crm.core import LEADS
from gateway.routes.crm.pipeline import ConvertDeal, ConvertRequest, convert_lead

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

USER = crm_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(
        monkeypatch, fake,
        (crm_core, crm_records, crm_pipeline, crm_activities, crm_admin),
    )
    fake.seed(
        "crm_lead_statuses", name="New", position=10, type="open", is_default=True,
    )
    fake.seed(
        "crm_lead_statuses", name="Qualified", position=40, type="won",
    )
    fake.seed(
        "crm_deal_statuses", name="Qualification", position=10, type="open",
        is_default=True, probability=10,
    )
    return fake


def _lead(db: FakeCrmDB, **over):
    columns = {
        "lead_name": "Anitha Kumar",
        "first_name": "Anitha",
        "last_name": "Kumar",
        "email": "anitha@bosch.in",
        "phone": "+91 90000 00000",
        "organization_name": "Bosch India",
        "website": "bosch.in",
        "owner_email": "vjvarada@fracktal.in",
        "status_id": db.rows("crm_lead_statuses")[0]["id"],
        "converted_at": None,
        "source": "manual",
    }
    return db.seed(LEADS.table, **{**columns, **over})


# ── Contact resolution ──────────────────────────────────────────────────────

async def test_a_lead_with_no_match_creates_a_contact(db: FakeCrmDB) -> None:
    lead = _lead(db)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.contact["first_name"] == "Anitha"
    assert result.contact["last_name"] == "Kumar"
    assert result.contact["email"] == "anitha@bosch.in"
    assert len(db.rows("crm_contacts")) == 1


async def test_an_existing_contact_is_matched_by_email_not_duplicated(
    db: FakeCrmDB,
) -> None:
    """The one dedup rule: an email address identifies a person. Without it,
    every re-enquiry from the same buyer mints another contact card."""
    existing = db.seed(
        "crm_contacts", first_name="Anitha", last_name="Kumar",
        email="anitha@bosch.in",
    )
    lead = _lead(db)

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.contact["id"] == str(existing.id)
    assert len(db.rows("crm_contacts")) == 1


async def test_the_email_match_is_case_insensitive(db: FakeCrmDB) -> None:
    """R10, and the reason the index is on `lower(email)`: the Zoho export
    carries both spellings of the same address."""
    existing = db.seed(
        "crm_contacts", first_name="Anitha", email="Anitha@Bosch.IN",
    )
    lead = _lead(db, email="anitha@bosch.in")

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.contact["id"] == str(existing.id)
    assert any("lower(email) = :email" in s for s in db.statements)


async def test_a_caller_chosen_contact_wins_over_the_email_match(
    db: FakeCrmDB,
) -> None:
    """The convert modal resolves ambiguity interactively (§5 surface 4); a
    server that overrode the human's choice would make the modal decorative."""
    db.seed("crm_contacts", first_name="Anitha", email="anitha@bosch.in")
    chosen = db.seed("crm_contacts", first_name="Ravi", email="ravi@bosch.in")
    lead = _lead(db)

    result = await convert_lead(
        str(lead.id), ConvertRequest(contact_id=str(chosen.id)), USER,
    )
    assert result.contact["id"] == str(chosen.id)


async def test_a_caller_chosen_contact_that_does_not_exist_is_404(
    db: FakeCrmDB,
) -> None:
    lead = _lead(db)
    with pytest.raises(HTTPException) as exc:
        await convert_lead(
            str(lead.id),
            ConvertRequest(contact_id="00000000-0000-0000-0000-000000000000"),
            USER,
        )
    assert exc.value.status_code == 404


async def test_a_lead_with_no_email_still_produces_a_contact(
    db: FakeCrmDB,
) -> None:
    """`first_name` is NOT NULL. A lead with no person name still has a display
    name, and using it beats inventing 'Unknown'."""
    lead = _lead(db, email=None, first_name=None, last_name=None,
                 lead_name="Bosch India")

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.contact["first_name"] == "Bosch India"


# ── Organization resolution ─────────────────────────────────────────────────

async def test_an_organization_is_created_from_the_lead_company(
    db: FakeCrmDB,
) -> None:
    lead = _lead(db)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.organization["name"] == "Bosch India"
    assert result.organization["website"] == "bosch.in"


async def test_an_existing_organization_is_matched_by_exact_name(
    db: FakeCrmDB,
) -> None:
    existing = db.seed("crm_companies", name="Bosch India")
    lead = _lead(db)

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.organization["id"] == str(existing.id)
    assert len(db.rows("crm_companies")) == 1


async def test_the_organization_match_is_exact_not_fuzzy(db: FakeCrmDB) -> None:
    """§3.7 says exact name. Fuzzy matching a company is how two real
    customers get merged, which is unrecoverable without an audit trail."""
    db.seed("crm_companies", name="Bosch India Pvt Ltd")
    lead = _lead(db)

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.organization["name"] == "Bosch India"
    assert len(db.rows("crm_companies")) == 2


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_a_lead_with_no_company_gets_no_organization(
    db: FakeCrmDB, blank,
) -> None:
    """An individual buyer is not a company; minting an organization named
    after a person is how a CRM fills with noise."""
    lead = _lead(db, organization_name=blank)

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.organization is None
    assert db.rows("crm_companies") == []
    # ...and the deal still gets made, named off the lead.
    assert result.deal["name"] == "Anitha Kumar"


async def test_conversion_is_never_refusable_by_a_settings_grid_edit(
    db: FakeCrmDB,
) -> None:
    """WS-26h2 done-when 4 — **D-CRM-13**, at the case that decided the rule.

    `_create_deal` always calls `load_default_status` and `ConvertDeal` has no
    `status_id` field at all, so a converted deal ALWAYS lands in the default
    lane. Gate the defaulted path and an owner requiring `organization_id`
    there makes every lead that names no company permanently unconvertible —
    `ConvertModal.tsx` renders "This lead names no company, so no organization
    is created" and offers no picker, so there is no request the user could
    send instead. The requirement below is exactly that configuration, and the
    conversion must still succeed.
    """
    for row in db.rows("crm_deal_statuses"):
        row["required_fields"] = ["organization_id", "amount"]
    lead = _lead(db, organization_name=None)

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.organization is None
    assert result.deal["organization_id"] is None
    assert result.deal["amount"] is None
    assert len(db.rows("crm_deals")) == 1


async def test_a_caller_chosen_organization_wins(db: FakeCrmDB) -> None:
    db.seed("crm_companies", name="Bosch India")
    chosen = db.seed("crm_companies", name="Bosch Rexroth")
    lead = _lead(db)

    result = await convert_lead(
        str(lead.id), ConvertRequest(organization_id=str(chosen.id)), USER,
    )
    assert result.organization["id"] == str(chosen.id)


# ── The deal ────────────────────────────────────────────────────────────────

async def test_the_deal_carries_its_lead_provenance(db: FakeCrmDB) -> None:
    """§3.4's `lead_id` — what lets the deal's timeline union the lead's
    history instead of losing everything said before conversion."""
    lead = _lead(db)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.deal["lead_id"] == str(lead.id)
    assert result.deal["organization_id"] == result.organization["id"]


async def test_the_deal_is_named_after_the_company_then_the_lead(
    db: FakeCrmDB,
) -> None:
    named = await convert_lead(str(_lead(db).id), ConvertRequest(), USER)
    assert named.deal["name"] == "Bosch India"

    unnamed = await convert_lead(
        str(_lead(db, organization_name=None).id), ConvertRequest(), USER,
    )
    assert unnamed.deal["name"] == "Anitha Kumar"


async def test_a_caller_supplied_deal_name_and_amount_are_carried(
    db: FakeCrmDB,
) -> None:
    lead = _lead(db)
    result = await convert_lead(
        str(lead.id),
        ConvertRequest(deal=ConvertDeal(name="Bosch — 6 printers", amount=780_000.0)),
        USER,
    )
    assert result.deal["name"] == "Bosch — 6 printers"
    assert result.deal["amount"] == 780_000.0


async def test_the_deal_lands_in_the_default_status_with_its_probability(
    db: FakeCrmDB,
) -> None:
    lead = _lead(db)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    status = db.rows("crm_deal_statuses")[0]
    assert result.deal["status_id"] == str(status["id"])
    assert result.deal["probability"] == 10
    assert result.deal["currency"] == "INR"


async def test_the_contact_is_linked_to_the_deal_as_primary(
    db: FakeCrmDB,
) -> None:
    lead = _lead(db)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    [link] = db.rows("crm_deal_contacts")
    assert link["deal_id"] == result.deal["id"]
    assert link["contact_id"] == result.contact["id"]
    assert link["is_primary"] is True


async def test_the_deal_inherits_the_leads_owner(db: FakeCrmDB) -> None:
    lead = _lead(db, owner_email="colleague@fracktal.in")
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)
    assert result.deal["owner_email"] == "colleague@fracktal.in"


async def test_an_unowned_lead_hands_its_deal_to_the_converter(
    db: FakeCrmDB,
) -> None:
    """A deal nobody owns is nobody's follow-up."""
    lead = _lead(db, owner_email=None)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)
    assert result.deal["owner_email"] == USER.email


# ── The lead's own provenance ───────────────────────────────────────────────

async def test_the_lead_is_stamped_with_everything_it_became(
    db: FakeCrmDB,
) -> None:
    lead = _lead(db)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.lead["converted_at"] is not None
    assert result.lead["converted_contact_id"] == result.contact["id"]
    assert result.lead["converted_organization_id"] == result.organization["id"]
    assert result.lead["converted_deal_id"] == result.deal["id"]


async def test_the_lead_moves_to_its_won_status(db: FakeCrmDB) -> None:
    """§3.7 step 4 — and through the transition machinery, so the conversion
    is countable in the funnel rather than an invisible column write."""
    lead = _lead(db)
    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    won = next(s for s in db.rows("crm_lead_statuses") if s["type"] == "won")
    assert result.lead["status_id"] == str(won["id"])

    [entry] = db.rows("crm_status_changes")
    assert entry["entity_type"] == "lead"
    assert entry["to_status"] == "Qualified"
    assert entry["changed_by"] == USER.email


async def test_the_conversion_appears_on_the_leads_timeline(
    db: FakeCrmDB,
) -> None:
    lead = _lead(db)
    await convert_lead(str(lead.id), ConvertRequest(), USER)

    [entry] = db.rows("crm_activities")
    assert entry["type"] == "status_change"
    assert entry["lead_id"] == str(lead.id)


async def test_a_pipeline_with_no_won_lead_status_still_converts(
    db: FakeCrmDB,
) -> None:
    """An owner who deletes the 'Qualified' lane must not lose Convert."""
    db.tables["crm_lead_statuses"] = [
        s for s in db.rows("crm_lead_statuses") if s["type"] != "won"
    ]
    lead = _lead(db)

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.lead["converted_at"] is not None
    assert db.rows("crm_status_changes") == []


# ── Re-conversion ───────────────────────────────────────────────────────────

async def test_converting_an_already_converted_lead_is_409(
    db: FakeCrmDB,
) -> None:
    """The stamps are provenance: a lead that produced two deals cannot say
    which one it became. The guard keys on the deal LINK, not the timestamp
    (B6)."""
    deal = db.seed("crm_deals", name="The deal it became")
    lead = _lead(
        db,
        converted_at="2026-08-01T00:00:00+00:00",
        converted_deal_id=str(deal.id),
    )

    with pytest.raises(HTTPException) as exc:
        await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert exc.value.status_code == 409


async def test_a_lead_whose_deal_was_deleted_can_convert_again(
    db: FakeCrmDB,
) -> None:
    """B6 (review finding 2026-08-05): deleting the deal SET-NULLs
    converted_deal_id while converted_at survives as history. The old
    converted_at guard made this state a permanent 409 — a lead with zero
    deals refused because of one it no longer has."""
    lead = _lead(db, converted_at="2026-08-01T00:00:00+00:00")

    result = await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert result.deal["id"]
    assert result.lead["converted_deal_id"] == result.deal["id"]


async def test_a_refused_re_conversion_creates_nothing(db: FakeCrmDB) -> None:
    """The 409 lands before any write, so a double-click does not leave an
    orphan deal and a second contact behind."""
    prior = db.seed("crm_deals", name="The deal it became")
    lead = _lead(
        db,
        converted_at="2026-08-01T00:00:00+00:00",
        converted_deal_id=str(prior.id),
    )

    with pytest.raises(HTTPException):
        await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert len(db.rows("crm_deals")) == 1  # only the seeded prior deal
    assert db.rows("crm_contacts") == []
    assert db.rows("crm_companies") == []
    assert db.committed == 0


async def test_converting_twice_in_sequence_is_refused_the_second_time(
    db: FakeCrmDB,
) -> None:
    """The realistic shape of the bug: convert, then convert again."""
    lead = _lead(db)
    await convert_lead(str(lead.id), ConvertRequest(), USER)

    with pytest.raises(HTTPException) as exc:
        await convert_lead(str(lead.id), ConvertRequest(), USER)

    assert exc.value.status_code == 409
    assert len(db.rows("crm_deals")) == 1


async def test_converting_an_unknown_lead_is_404(db: FakeCrmDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await convert_lead(
            "00000000-0000-0000-0000-000000000000", ConvertRequest(), USER,
        )
    assert exc.value.status_code == 404


# ── WS-26c · the primary contact goes through the shared seam ──────────────

async def test_the_converted_deals_contact_is_primary_through_the_seam(
    db: FakeCrmDB,
) -> None:
    """Convert was the ONLY writer of crm_deal_contacts in 26a, and it wrote
    ``is_primary`` straight into an INSERT. Now that the deal-contacts
    endpoints exist there are two writers, so the rule moved to
    ``core.link_deal_contact`` and this asserts convert still reaches it — the
    demote-first UPDATE it issues is the tell."""
    lead = _lead(db)
    await convert_lead(str(lead.id), ConvertRequest(), USER)

    links = db.rows("crm_deal_contacts")
    assert len(links) == 1
    assert links[0]["is_primary"] is True
    assert db.statements_touching(
        "UPDATE crm_deal_contacts SET is_primary = false",
    ), "convert bypassed core.link_deal_contact and wrote the link directly"


async def test_converting_two_leads_onto_one_contact_keeps_one_primary_each(
    db: FakeCrmDB,
) -> None:
    """The demotion is scoped to the DEAL, not to the contact: one person can
    be the primary contact on every deal they are on."""
    first = _lead(db, email="anitha@bosch.in")
    second = _lead(db, email="anitha@bosch.in", organization_name="Bosch Pune")
    await convert_lead(str(first.id), ConvertRequest(), USER)
    await convert_lead(str(second.id), ConvertRequest(), USER)

    links = db.rows("crm_deal_contacts")
    assert len(links) == 2
    assert [link["is_primary"] for link in links] == [True, True]
    assert len({str(link["deal_id"]) for link in links}) == 2
