"""CRM · the Zoho backfill — the mapping, the floor, and what dry-run does.

Spec: ``project-docs/specs/crm_app.md`` §7.1 (the mapping table) · ticket
WS-26b done-when 2 and 5.

Hermetic: no Postgres, no network, no Zoho. The route function is called
directly with ``core._get_db`` monkeypatched onto each SUT submodule (the
``_crm_fakes`` convention) and ``import_zoho._client`` bound to
:class:`FakeZoho`. Every ``zoho_*`` setting defaults to ``""`` and
``get_access_token()`` raises without credentials, so nothing here needs one.

⚠️ **Idempotency is pinned against the STATEMENT TEXT, not against the fake.**
``_crm_fakes.FakeCrmDB`` models no ``ON CONFLICT`` — a second insert simply
appends — so a behavioural "the second run created nothing" would be the
mirror agreeing with itself. The upserts are therefore asserted by reading the
SQL the SUT emits, the ``test_crm_migration.py`` technique.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import import_zoho as crm_import
from gateway.routes.crm import records as crm_records
from gateway.routes.crm.core import CONTACTS, DEALS, LEADS, ORGANIZATIONS

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

USER = crm_user()

CRM_MODULES = (crm_core, crm_records, crm_activities, crm_admin, crm_import)


# ── The fake Zoho tenant ────────────────────────────────────────────────────

class FakeZoho:
    """The six read functions ``import_zoho`` calls, and nothing else.

    Deliberately shaped as the client's PUBLIC surface: if the importer ever
    reaches past ``list_*`` into ``_list_module``, this fake stops answering
    and the test fails rather than silently exercising a different path.
    """

    def __init__(self, **modules: list[dict[str, Any]]) -> None:
        self.data: dict[str, list[dict[str, Any]]] = {
            "Accounts": [], "Contacts": [], "Leads": [], "Deals": [],
            "Notes": [], "Tasks": [],
        }
        self.data.update(modules)
        self.users: list[dict[str, Any]] = []
        self.deleted: dict[str, list[dict[str, Any]]] = {}
        #: ``(module, modified_since)`` per read, so a test can prove the pull
        #: was incremental rather than a full re-read.
        self.reads: list[tuple[str, Any]] = []

    async def list_users(self) -> list[dict[str, Any]]:
        return list(self.users)

    def _reader(self, module: str):
        async def _read(*, modified_since: Any = None) -> list[dict[str, Any]]:
            self.reads.append((module, modified_since))
            return list(self.data[module])
        return _read

    def __getattr__(self, name: str):
        modules = {
            "list_accounts": "Accounts", "list_contacts": "Contacts",
            "list_leads": "Leads", "list_deals": "Deals",
            "list_notes": "Notes", "list_tasks": "Tasks",
        }
        if name in modules:
            return self._reader(modules[name])
        raise AttributeError(name)

    async def list_deleted(self, module: str, *, since: Any = None, **_: Any):
        self.reads.append((f"{module}/deleted", since))
        return list(self.deleted.get(module, []))


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake, CRM_MODULES)
    return fake


@pytest.fixture
def zoho(monkeypatch: pytest.MonkeyPatch) -> FakeZoho:
    tenant = FakeZoho()
    monkeypatch.setattr(crm_import, "_client", lambda: tenant)
    return tenant


def _seed_pipeline(db: FakeCrmDB) -> tuple:
    lead_status = db.seed(
        "crm_lead_statuses", name="New", position=10, type="open", is_default=True,
    )
    deal_status = db.seed(
        "crm_deal_statuses", name="Qualification", position=10, type="open",
        is_default=True, probability=10,
    )
    return lead_status, deal_status


async def _run(dry_run: bool = False) -> Any:
    return await crm_import.import_from_zoho(
        crm_import.ImportRequest(dry_run=dry_run), USER,
    )


# ── The permission floor (§4's warning, done-when 2) ────────────────────────

def _route(path: str) -> Any:
    from gateway.routes.crm.core import router

    return next(r for r in router.routes if getattr(r, "path", None) == path)


def permission_checks(path: str) -> list[Any]:
    """The ``require_permission`` dependencies on one route.

    Filtered by qualname because the ROUTER also carries
    ``require_feature_router("crm")``, which takes a ``Request``. Both gates
    are real and both must hold; this helper is about the extra CAPABILITY
    floor a Zoho route adds on top of the feature.
    """
    return [
        d.dependency for d in _route(path).dependencies
        if "require_permission" in getattr(d.dependency, "__qualname__", "")
    ]


async def test_the_backfill_floor_is_an_admin_capability_not_the_integration() -> None:
    """§4 — ``integrations:use:zoho-crm`` was the first choice and is WRONG.

    ``131_integration_memory_permissions.sql`` grants ``member``
    ``integrations:use:*``, so under ``permission_matches`` every member would
    hold the integration slug and it would gate nothing. Asserted by running
    the route's own dependency against a principal holding exactly that
    family — if the floor were the integration slug this would be allowed.
    """
    checks = permission_checks("/crm/import/zoho")
    assert checks, "the backfill route carries no permission dependency at all"

    integration_holder = crm_user(features="integrations:use:*")
    for check in checks:
        with pytest.raises(HTTPException) as exc:
            await check(user=integration_holder)
        assert exc.value.status_code == 403
        assert "admin:access:manage" in str(exc.value.detail)


async def test_an_admin_capability_holder_passes_the_floor() -> None:
    """The other half: the named capability really is sufficient."""
    admin = crm_user(features="admin:access:manage")
    checks = permission_checks("/crm/import/zoho")
    assert checks
    for check in checks:
        assert await check(user=admin) is admin


# ── dry_run (done-when 2) ───────────────────────────────────────────────────

async def test_dry_run_writes_nothing_at_all(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """Not "no rows" — no STATEMENTS. A dry run that creates a status lane or
    moves a cursor has already changed the thing it was asked to preview."""
    zoho.users = [{"id": "u1", "email": "vjvarada@fracktal.in"}]
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    zoho.data["Deals"] = [
        {"id": "z-deal-1", "Deal_Name": "Printer", "Stage": "Brand New Stage"},
    ]

    report = await _run(dry_run=True)

    assert report.dry_run is True
    assert db.statements == []
    assert report.modules["Accounts"].fetched == 1
    assert report.modules["Accounts"].created == 0


async def test_dry_run_still_reports_what_it_would_touch(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    zoho.data["Leads"] = [{"id": "z-1"}, {"id": "z-2"}]
    report = await _run(dry_run=True)
    assert report.modules["Leads"].fetched == 2


# ── Idempotency, pinned against the statement text (done-when 2) ────────────

async def test_every_record_upsert_carries_on_conflict_zoho_id(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """§7.1's ``ON CONFLICT (organization_id, zoho_id)``. Static, because the fake models no
    conflict arm: a second run against it appends rather than converging, so
    only the SQL can say whether a replayed import duplicates the tenant."""
    _seed_pipeline(db)
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    zoho.data["Contacts"] = [{"id": "z-con-1", "First_Name": "Vijay"}]
    zoho.data["Leads"] = [{"id": "z-lead-1", "Last_Name": "Rao"}]
    zoho.data["Deals"] = [{"id": "z-deal-1", "Deal_Name": "Printer"}]

    await _run()

    for table in ("crm_companies", "crm_contacts", "crm_leads", "crm_deals"):
        [insert] = [
            s for s in db.statements if s.startswith(f"INSERT INTO {table} (")
        ]
        assert "ON CONFLICT (organization_id, zoho_id) DO UPDATE SET" in insert, table
        # The conflict arm must not rewrite the key it matched on.
        assert "zoho_id = EXCLUDED.zoho_id" not in insert


async def test_the_upsert_never_bumps_updated_at(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """``updated_at`` is what last-writer-wins compares against Zoho's
    ``Modified_Time``, so a pull that bumped it would make the native side
    look permanently newer and turn LWW into always-native-wins."""
    _seed_pipeline(db)
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    await _run()

    [insert] = db.statements_touching("INSERT INTO crm_companies (")
    assert "updated_at" not in insert


async def test_the_status_upsert_carries_on_conflict(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """done-when 2 names this one explicitly: two modules (or two cycles) can
    reach the same unseen stage at once, and the loser must not abort the
    whole import with a unique violation."""
    _seed_pipeline(db)
    zoho.data["Deals"] = [
        {"id": "z-deal-1", "Deal_Name": "Printer", "Stage": "Awaiting PO"},
    ]
    await _run()

    [insert] = db.statements_touching("INSERT INTO crm_deal_statuses (")
    assert "ON CONFLICT (organization_id, name) DO NOTHING" in insert


async def test_the_activity_upsert_carries_on_conflict_zoho_id(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    _seed_pipeline(db)
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    zoho.data["Notes"] = [{
        "id": "z-note-1", "Note_Title": "Called", "Note_Content": "Wants a quote",
        "Parent_Id": {"name": "Fracktal", "id": "z-acc-1"}, "$se_module": "Accounts",
    }]
    await _run()

    [insert] = db.statements_touching("INSERT INTO crm_activities (")
    assert "ON CONFLICT (organization_id, zoho_id) DO UPDATE SET" in insert


# ── §7.1's mapping table ────────────────────────────────────────────────────

async def test_accounts_map_onto_organizations(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    zoho.data["Accounts"] = [{
        "id": "z-acc-1",
        "Account_Name": "Fracktal Works",
        "Website": "https://fracktal.in",
        "Industry": "Manufacturing",
        "Employees": "42",
        "Annual_Revenue": "12500000.50",
        "Phone": "+91 80 1234",
        "Description": "3D printers",
        "Billing_Street": "MG Road", "Billing_City": "Bengaluru",
        "Billing_Code": "560001", "Billing_Country": "India",
    }]
    await _run()

    [row] = db.rows(ORGANIZATIONS.table)
    assert row["name"] == "Fracktal Works"
    assert row["website"] == "https://fracktal.in"
    assert row["no_of_employees"] == 42
    assert row["annual_revenue"] == 12500000.50
    assert row["zoho_id"] == "z-acc-1"
    # §7.1 — Billing_* folds into the address JSONB, and the blank column is
    # simply absent rather than stored as an empty string.
    assert row["address"] == (
        '{"street": "MG Road", "city": "Bengaluru", "code": "560001", '
        '"country": "India"}'
    )


async def test_imported_rows_carry_the_existing_import_source(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """§7.1 — pulled rows use ``source='import'``: the CHECK vocabulary in
    migration 144 needs NO new value and NO ALTER."""
    from gateway.routes.crm.core import SOURCES

    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    await _run()

    [row] = db.rows(ORGANIZATIONS.table)
    assert row["source"] == "import"
    assert "import" in SOURCES


async def test_contacts_map_and_link_to_their_account(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    zoho.data["Contacts"] = [{
        "id": "z-con-1", "First_Name": "Vijay", "Last_Name": "Varada",
        "Email": "v@fracktal.in", "Phone": "+91 1", "Mobile": "+91 2",
        "Title": "Founder", "Account_Name": {"id": "z-acc-1", "name": "Fracktal"},
    }]
    await _run()

    [org] = db.rows(ORGANIZATIONS.table)
    [contact] = db.rows(CONTACTS.table)
    assert contact["first_name"] == "Vijay"
    assert contact["last_name"] == "Varada"
    assert contact["title"] == "Founder"
    # Accounts import before Contacts precisely so this link resolves.
    assert contact["company_id"] == str(org["id"])


async def test_a_surname_only_contact_still_satisfies_not_null(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """``first_name`` is NOT NULL and Zoho exports are full of surname-only
    rows. Promoting the name we have beats inventing 'Unknown'."""
    zoho.data["Contacts"] = [{"id": "z-con-1", "Last_Name": "Varada"}]
    await _run()

    [contact] = db.rows(CONTACTS.table)
    assert contact["first_name"] == "Varada"
    assert contact["last_name"] is None


async def test_leads_map_company_to_free_text_not_an_organization(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """§3.3 — a lead's company is denormalized text until conversion. An
    organization row per raw lead is how a CRM fills with noise."""
    _seed_pipeline(db)
    zoho.data["Leads"] = [{
        "id": "z-lead-1", "First_Name": "Asha", "Last_Name": "Rao",
        "Company": "Acme Labs", "Email": "asha@acme.example",
        "Lead_Source": "Website", "No_of_Employees": "7",
    }]
    await _run()

    [lead] = db.rows(LEADS.table)
    assert lead["organization_name"] == "Acme Labs"
    assert lead["lead_name"] == "Asha Rao"
    assert lead["lead_source"] == "Website"
    assert lead["no_of_employees"] == 7
    assert db.rows(ORGANIZATIONS.table) == []


async def test_deals_map_amount_stage_and_close_date(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    _, deal_status = _seed_pipeline(db)
    zoho.data["Deals"] = [{
        "id": "z-deal-1", "Deal_Name": "10 printers",
        "Amount": "450000", "Stage": "Qualification",
        "Closing_Date": "2026-09-30", "Probability": 40,
    }]
    await _run()

    [deal] = db.rows(DEALS.table)
    assert deal["name"] == "10 printers"
    assert deal["amount"] == 450000.0
    assert deal["probability"] == 40
    assert str(deal["expected_close_date"]) == "2026-09-30"
    assert str(deal["status_id"]) == str(deal_status.id)


async def test_a_deals_contact_becomes_its_primary_deal_contact(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    _seed_pipeline(db)
    zoho.data["Contacts"] = [{"id": "z-con-1", "First_Name": "Asha"}]
    zoho.data["Deals"] = [{
        "id": "z-deal-1", "Deal_Name": "Printer",
        "Contact_Name": {"id": "z-con-1"},
    }]
    await _run()

    [link] = db.rows("crm_deal_contacts")
    [contact] = db.rows(CONTACTS.table)
    assert link["contact_id"] == str(contact["id"])
    [insert] = db.statements_touching("INSERT INTO crm_deal_contacts")
    # Re-importing a deal must not fail on a link it already made, and must
    # not demote a primary the team has since changed by hand.
    assert "ON CONFLICT DO NOTHING" in insert


async def test_the_import_can_never_create_a_second_primary_contact(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """`is_primary` is COMPUTED inside the INSERT, never the literal `true`.

    The failure this closes: a deal already carries a hand-set primary
    (contact A), its Zoho record names contact B, and the import adds B as a
    *second* primary. "At most one primary per deal" is enforced by code — no
    constraint catches it — so the row simply exists and both cards render as
    primary.

    Structural, against the statement text, for the reason this package
    documents everywhere: the fake models no foreign keys, no CHECKs and no
    computed columns, so it would happily record two primaries and agree that
    nothing was wrong. Deciding it inside the statement rather than reading
    first also means two racing cycles cannot both observe "no primary yet".
    """
    _seed_pipeline(db)
    zoho.data["Contacts"] = [{"id": "z-con-1", "First_Name": "Asha"}]
    zoho.data["Deals"] = [{
        "id": "z-deal-1", "Deal_Name": "Printer",
        "Contact_Name": {"id": "z-con-1"},
    }]
    await _run()

    [insert] = db.statements_touching("INSERT INTO crm_deal_contacts")
    assert "NOT EXISTS" in insert
    assert "SELECT 1 FROM crm_deal_contacts" in insert
    assert "AND is_primary" in insert
    # The scope of the NOT EXISTS is THIS deal — a primary on some other deal
    # must not stop this one acquiring its first.
    assert "WHERE deal_id = CAST(:deal_id AS uuid)" in insert
    # …and the literal it replaced is gone: no hardcoded `true`, and no
    # is_primary bound from Python either — the database decides it.
    assert ", true)" not in insert.lower()
    [(_, params)] = [
        (s, p) for s, p in db.calls if s.startswith("INSERT INTO crm_deal_contacts")
    ]
    assert set(params) == {"deal_id", "contact_id"}


# ── Statuses auto-created (§7.1: vocabulary flows DOWN) ─────────────────────

async def test_an_unseen_stage_becomes_a_new_lane_appended_at_the_end(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """Appended, never inserted at 0 — inserting would silently reorder the
    owner's whole pipeline on every import."""
    _seed_pipeline(db)
    zoho.data["Deals"] = [
        {"id": "z-deal-1", "Deal_Name": "Printer", "Stage": "Awaiting PO"},
    ]
    report = await _run()

    lanes = {r["name"]: r for r in db.rows("crm_deal_statuses")}
    assert "Awaiting PO" in lanes
    assert lanes["Awaiting PO"]["position"] > lanes["Qualification"]["position"]
    assert report.statuses_created == 1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Closed Won", "won"),
        ("Deal Won", "won"),
        ("Closed Lost", "lost"),
        ("Junk Lead", "lost"),
        ("Not Qualified", "lost"),
        ("Awaiting PO", "open"),
        ("Negotiation", "open"),
    ],
)
def test_the_stage_type_is_guessed_from_the_name(name: str, expected: str) -> None:
    """§7.1 — "type guessed: name ~ won/lost, else open". Guessing *open* for
    anything unrecognised is the conservative direction: it never auto-closes
    a deal, and the owner can retype a lane in the admin UI."""
    assert crm_import.guess_status_type(name) == expected


async def test_a_lead_with_no_status_lands_in_the_default_lane(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    lead_status, _ = _seed_pipeline(db)
    zoho.data["Leads"] = [{"id": "z-lead-1", "Last_Name": "Rao"}]
    report = await _run()

    [lead] = db.rows(LEADS.table)
    assert str(lead["status_id"]) == str(lead_status.id)
    assert report.statuses_created == 0


# ── Owner mapping (§7.1, the Users row) ─────────────────────────────────────

def test_the_owner_index_is_keyed_on_the_zoho_user_id() -> None:
    """Names repeat and get edited; ids do not."""
    index = crm_import.owner_index([
        {"id": "u1", "email": "Vjvarada@Fracktal.in", "full_name": "V"},
        {"id": "u2", "email": "asha@fracktal.in"},
        {"id": "u3"},  # no address — unmappable, and silently dropped
    ])
    # R10 — email comparisons are case-insensitive, so the index stores folded.
    assert index == {"u1": "vjvarada@fracktal.in", "u2": "asha@fracktal.in"}


async def test_a_records_owner_resolves_through_list_users(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    zoho.users = [{"id": "u7", "email": "asha@fracktal.in"}]
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Fracktal", "Owner": {"id": "u7"},
    }]
    report = await _run()

    [row] = db.rows(ORGANIZATIONS.table)
    assert row["owner_email"] == "asha@fracktal.in"
    assert report.unmatched_owners == 0


async def test_an_unmatched_owner_falls_back_to_the_actor_and_is_counted(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """§7.1 — "unmatched → import actor". Counted because "every record is now
    owned by whoever ran the import" must not be a silent outcome."""
    zoho.users = []
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Fracktal", "Owner": {"id": "stranger"},
    }]
    report = await _run()

    [row] = db.rows(ORGANIZATIONS.table)
    assert row["owner_email"] == USER.email
    assert report.unmatched_owners == 1


async def test_an_owner_lookups_embedded_address_is_the_second_answer(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    zoho.users = []
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Fracktal",
        "Owner": {"id": "u9", "email": "Sales@Fracktal.in"},
    }]
    report = await _run()

    [row] = db.rows(ORGANIZATIONS.table)
    assert row["owner_email"] == "sales@fracktal.in"
    assert report.unmatched_owners == 0


# ── Notes and Tasks → the activity spine ────────────────────────────────────

async def test_a_note_lands_on_its_parent_and_bumps_its_recency(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """``Parent_Id`` here is the LOOKUP OBJECT the live API actually sends
    (verified against the tenant 2026-08-06). The first real backfill skipped
    all 1,909 notes because the fixtures used a bare string id, which the API
    never returns — so the dict-handling path had no coverage. Do not simplify
    this fixture back to a string."""
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    zoho.data["Notes"] = [{
        "id": "z-note-1", "Note_Title": "Called", "Note_Content": "Wants a quote",
        "Parent_Id": {"name": "Fracktal", "id": "z-acc-1"}, "$se_module": "Accounts",
    }]
    await _run()

    [org] = db.rows(ORGANIZATIONS.table)
    [note] = db.rows("crm_activities")
    assert note["type"] == "note"
    assert note["subject"] == "Called"
    assert note["company_id"] == str(org["id"])
    # §3.8's discipline — an imported record must not sort as never-touched on
    # the day it arrives.
    assert org["last_activity_at"] is not None


async def test_a_zoho_task_becomes_a_task_activity_with_its_due_date(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    _seed_pipeline(db)
    zoho.data["Deals"] = [{"id": "z-deal-1", "Deal_Name": "Printer"}]
    zoho.data["Tasks"] = [{
        "id": "z-task-1", "Subject": "Send quote", "Due_Date": "2026-08-20",
        "What_Id": {"id": "z-deal-1"}, "Status": "Not Started",
    }]
    await _run()

    [task] = db.rows("crm_activities")
    assert task["type"] == "task"
    assert task["subject"] == "Send quote"
    assert str(task["due_at"])[:10] == "2026-08-20"
    # An open task never NAMES completed_at in the INSERT, so the column keeps
    # its NULL default rather than being written as an explicit NULL.
    assert task.get("completed_at") is None


async def test_a_completed_zoho_task_arrives_completed(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    _seed_pipeline(db)
    zoho.data["Deals"] = [{"id": "z-deal-1", "Deal_Name": "Printer"}]
    zoho.data["Tasks"] = [{
        "id": "z-task-1", "Subject": "Send quote", "Status": "Completed",
        "What_Id": {"id": "z-deal-1"},
        "Closed_Time": "2026-08-01T10:00:00+05:30",
    }]
    await _run()

    [task] = db.rows("crm_activities")
    assert task["completed_at"] is not None


async def test_an_activity_with_no_imported_parent_is_skipped_not_orphaned(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """``crm_activities`` CHECKs that at least one target is set. A note
    attached to nothing is invisible in every timeline while still counting
    against the numbers, so it is skipped and REPORTED as skipped."""
    zoho.data["Notes"] = [{
        "id": "z-note-1", "Note_Title": "Orphan",
        "Parent_Id": {"name": "Gone", "id": "z-missing"},
        "$se_module": "Accounts",
    }]
    report = await _run()

    assert db.rows("crm_activities") == []
    assert report.modules["Notes"].skipped == 1
    assert report.modules["Notes"].created == 0


async def test_a_bare_string_parent_id_still_resolves(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """The fallback contract: the live API sends lookup objects, but a bare
    string id (the shape the old fixtures invented) must keep working —
    ``_lookup_id`` first, ``_text`` second."""
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Fracktal"}]
    zoho.data["Notes"] = [{
        "id": "z-note-1", "Note_Title": "Called",
        "Parent_Id": "z-acc-1", "$se_module": "Accounts",
    }]
    await _run()

    [org] = db.rows(ORGANIZATIONS.table)
    [note] = db.rows("crm_activities")
    assert note["company_id"] == str(org["id"])


# ── Reporting ───────────────────────────────────────────────────────────────

async def test_the_report_counts_created_versus_updated_per_module(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    """Decided by a read BEFORE the upsert: ``ON CONFLICT`` cannot say which
    arm it took, and reporting every row as "created" on a re-run makes the
    report useless exactly when somebody is checking idempotency."""
    _seed_pipeline(db)
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    zoho.data["Accounts"] = [
        {"id": "z-acc-1", "Account_Name": "Fracktal Works"},
        {"id": "z-acc-2", "Account_Name": "Acme"},
    ]
    report = await _run()

    assert report.modules["Accounts"].fetched == 2
    assert report.modules["Accounts"].updated == 1
    assert report.modules["Accounts"].created == 1


async def test_a_record_with_no_zoho_id_is_skipped(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    zoho.data["Accounts"] = [{"Account_Name": "No id"}]
    report = await _run()
    assert report.modules["Accounts"].skipped == 1
    assert db.rows(ORGANIZATIONS.table) == []


async def test_one_bad_record_does_not_lose_the_batch(
    db: FakeCrmDB, zoho: FakeZoho, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-record failure is counted in ``errors[]`` and the pass continues.
    An import that aborts on row 900 of 3 000 is one nobody can finish."""
    real = crm_import.map_account

    def _explode(record: dict) -> dict:
        if record.get("id") == "z-acc-bad":
            raise ValueError("bad account")
        return real(record)

    monkeypatch.setitem(crm_import.MAPPERS, "Accounts", _explode)
    zoho.data["Accounts"] = [
        {"id": "z-acc-bad", "Account_Name": "Bad"},
        {"id": "z-acc-ok", "Account_Name": "Good"},
    ]
    report = await _run()

    assert len(db.rows(ORGANIZATIONS.table)) == 1
    assert report.modules["Accounts"].created == 1
    assert len(report.modules["Accounts"].errors) == 1
    assert "z-acc-bad" in report.modules["Accounts"].errors[0]


async def test_the_users_module_is_reported_too(
    db: FakeCrmDB, zoho: FakeZoho,
) -> None:
    zoho.users = [{"id": "u1", "email": "a@fracktal.in"}]
    report = await _run()
    assert report.modules["Users"].fetched == 1


# ── The read surface (done-when 1) ──────────────────────────────────────────

def test_the_client_gained_list_leads_and_a_deleted_records_reader() -> None:
    """done-when 1 — Leads were never mirrored (§2), so a backfill without
    ``list_leads`` starts the funnel at conversion; and a Zoho delete can
    never be inferred from a list call, so the tombstone reader is the only
    honest way to propagate one."""
    from ingestion.sources.zoho import client

    assert callable(client.list_leads)
    assert callable(client.list_deleted)
    assert "list_leads" in client.__all__
    assert "list_deleted" in client.__all__


def test_the_pull_asks_for_a_stable_ascending_order() -> None:
    """Zoho's default order is ``Modified_Time`` DESCENDING, so a record edited
    between page 1 and page 2 — by anyone, our own push included — jumps to the
    front and shifts every later record back one slot. The record that was at
    the page boundary is then never returned at all. Sorting ASCENDING by the
    same key we cursor on makes the sequence append-only for the duration of
    the pull: a concurrent edit lands past the pages already read."""
    import inspect

    from ingestion.sources.zoho import client

    source = inspect.getsource(client._list_module)
    assert '"sort_by": "Modified_Time"' in source
    assert '"sort_order": "asc"' in source


def test_the_read_client_still_contains_no_write_verbs() -> None:
    """§7.1's boundary: ``client.py`` reads, ``writer.py`` writes. The one
    POST in the client is the OAuth token refresh, which is why the check is
    on the CRM path rather than on the verb alone."""
    import inspect

    from ingestion.sources.zoho import client

    source = inspect.getsource(client)
    for verb in ("http.put(", "http.delete(", "http.patch("):
        assert verb not in source, f"{verb} appeared in the READ client"
    posts = [line for line in source.splitlines() if "http.post(" in line]
    assert len(posts) == 1, "the read client should have exactly one POST"
    assert "oauth/v2/token" in source
