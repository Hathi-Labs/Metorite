"""CRM · records, activities and admin — the list contract and the write paths.

Spec: ``project-docs/specs/crm_app.md`` §3.8, §4 · ticket WS-26a done-when
4 and 6.

Hermetic: no Postgres, no network, no TestClient. Route functions are called
directly with ``_get_db`` monkeypatched onto each SUT submodule
(``test_tasks_people_scoping.py``'s convention), against the shared
``_crm_fakes.FakeCrmDB``.

Where a rule is decided **in SQL** the assertion is structural, against the
statement text: the fake re-implements clauses in Python and a mirror can only
agree with itself. Where a rule is decided in Python — the sort allowlist, the
required-field check, the loggable-type gate — the SUT runs for real and the
behavioural assertion is a genuine check on it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import deal_contacts as crm_deal_contacts
from gateway.routes.crm import records as crm_records
from gateway.routes.crm.core import CONTACTS, DEALS, ENTITIES, LEADS, ORGANIZATIONS

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

USER = crm_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(
        monkeypatch, fake,
        (crm_core, crm_records, crm_activities, crm_admin, crm_deal_contacts),
    )
    return fake


def _params(**over) -> crm_records.ListParams:
    """A ListParams built without FastAPI, with the same defaults."""
    base = {
        "q": None, "sort": None, "direction": "desc", "page": 1,
        "page_size": 50, "status_id": None, "owner": None, "source": None,
        "include_converted": False,
    }
    return crm_records.ListParams(**{**base, **over})


def _seed_pipeline(db: FakeCrmDB) -> tuple:
    lead_status = db.seed(
        "crm_lead_statuses", name="New", position=10, type="open", is_default=True,
    )
    deal_status = db.seed(
        "crm_deal_statuses", name="Qualification", position=10, type="open",
        is_default=True, probability=10,
    )
    return lead_status, deal_status


# ── The list contract ───────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", sorted(ENTITIES))
async def test_the_list_shape_is_rows_and_total(db: FakeCrmDB, slug: str) -> None:
    """One contract, four entities. A per-entity response shape is how a
    shared list component grows four branches."""
    entity = ENTITIES[slug]
    db.seed(entity.table, **{f: "x" for f in ("name", "first_name", "lead_name")
                             if f in entity.model.model_fields})
    result = await crm_records._list(entity, _params())

    assert result.total == 1
    assert len(result.rows) == 1
    assert set(result.model_dump()) == {"rows", "total"}


@pytest.mark.parametrize("slug", sorted(ENTITIES))
async def test_an_unknown_sort_key_is_422(db: FakeCrmDB, slug: str) -> None:
    """trycompai's resolveOrderBy rule. Not a silent fall back to the default:
    a client sorting by a column it thinks exists and quietly getting
    created_at is a bug that survives review."""
    with pytest.raises(HTTPException) as exc:
        await crm_records._list(ENTITIES[slug], _params(sort="password"))

    assert exc.value.status_code == 422
    assert "password" in str(exc.value.detail)


async def test_an_unknown_sort_direction_is_422(db: FakeCrmDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await crm_records._list(LEADS, _params(direction="sideways"))
    assert exc.value.status_code == 422


async def test_an_allowlisted_sort_key_reaches_the_order_by(db: FakeCrmDB) -> None:
    """Structural: the ORDER BY is decided in SQL, so read the statement.

    Also fences the injection: the ORDER BY carries the ALLOWLIST's value, not
    the caller's string, so the two must be checked separately from each other.
    """
    db.seed(DEALS.table, name="Printer order")
    await crm_records._list(DEALS, _params(sort="amount", direction="asc"))

    [listing] = [s for s in db.statements if "ORDER BY" in s]
    assert "ORDER BY amount ASC" in listing
    assert "LIMIT :limit OFFSET :offset" in listing


async def test_the_sort_allowlist_never_interpolates_caller_text(
    db: FakeCrmDB,
) -> None:
    """The injection attempt is refused before any SQL is built at all."""
    with pytest.raises(HTTPException):
        await crm_records._list(DEALS, _params(sort="amount; DROP TABLE crm_deals"))
    assert not db.statements


async def test_page_size_is_capped_at_one_hundred(db: FakeCrmDB) -> None:
    """§4's `page_size≤100`. The route signature enforces it with `le=`; this
    pins the kernel too, so a caller reaching `list_contract` another way
    (an agent tool, WS-26d) cannot ask for the whole table."""
    for n in range(3):
        db.seed(DEALS.table, name=f"deal-{n}")
    query = crm_core.list_contract(DEALS, page_size=10_000)
    assert query.limit == crm_core.MAX_PAGE_SIZE


async def test_paging_offsets_by_whole_pages(db: FakeCrmDB) -> None:
    for n in range(5):
        db.seed(DEALS.table, name=f"deal-{n}")
    page2 = await crm_records._list(DEALS, _params(page=2, page_size=2))

    assert page2.total == 5
    assert len(page2.rows) == 2


async def test_leads_hide_converted_rows_by_default(db: FakeCrmDB) -> None:
    """§3.3/B6 — a lead is converted while the deal it became still EXISTS.

    The filter keys on the FK link, not the timestamp: if the deal is deleted,
    SET NULL clears the link and the lead returns to the working list instead
    of being stranded invisible (review finding, 2026-08-05).
    """
    deal = db.seed(DEALS.table, name="Printer order")
    db.seed(LEADS.table, lead_name="Open one", converted_deal_id=None)
    db.seed(
        LEADS.table, lead_name="Already a deal",
        converted_at="2026-08-01", converted_deal_id=str(deal.id),
    )

    default = await crm_records._list(LEADS, _params())
    included = await crm_records._list(LEADS, _params(include_converted=True))

    assert default.total == 1
    assert included.total == 2
    assert any("converted_deal_id IS NULL" in s for s in db.statements)


async def test_a_lead_whose_deal_was_deleted_returns_to_the_list(
    db: FakeCrmDB,
) -> None:
    """The post-delete state: converted_at survives as history, the FK link is
    NULL. Keying the filter on converted_at would hide this lead forever."""
    db.seed(
        LEADS.table, lead_name="Deal was deleted",
        converted_at="2026-08-01", converted_deal_id=None,
    )
    default = await crm_records._list(LEADS, _params())
    assert default.total == 1


async def test_the_converted_filter_is_leads_only(db: FakeCrmDB) -> None:
    """Deals have no `converted_deal_id`; a shared clause applied to all four
    would be a column that does not exist."""
    db.seed(DEALS.table, name="Printer order")
    await crm_records._list(DEALS, _params())
    assert not any("converted_deal_id" in s for s in db.statements)


async def test_the_owner_filter_compares_case_insensitively(db: FakeCrmDB) -> None:
    """R10 — an IdP that re-cases a UPN must not empty somebody's own list."""
    db.seed(DEALS.table, name="Printer order", owner_email="VJVarada@Fracktal.in")
    result = await crm_records._list(DEALS, _params(owner="vjvarada@FRACKTAL.IN"))

    assert result.total == 1
    assert any("lower(owner_email) = :owner" in s for s in db.statements)


async def test_search_matches_the_declared_columns(db: FakeCrmDB) -> None:
    db.seed(LEADS.table, lead_name="Anitha Kumar", email="anitha@acme.in")
    db.seed(LEADS.table, lead_name="Bosch India", email="p@bosch.in")
    result = await crm_records._list(LEADS, _params(q="anitha"))

    assert result.total == 1


# ── Create ──────────────────────────────────────────────────────────────────

async def test_a_missing_required_field_is_422(db: FakeCrmDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await crm_records.create_record(
            DEALS, crm_core.DealIn(amount=10.0), USER,
        )
    assert exc.value.status_code == 422
    assert "name" in str(exc.value.detail)


async def test_a_new_deal_lands_in_the_default_status(db: FakeCrmDB) -> None:
    _, deal_status = _seed_pipeline(db)
    deal = await crm_records.create_record(
        DEALS, crm_core.DealIn(name="Printer order"), USER,
    )
    assert deal["status_id"] == str(deal_status.id)


async def test_a_new_deal_inherits_its_stage_probability(db: FakeCrmDB) -> None:
    """§3.4 — auto-filled from the status default when the deal states none."""
    _seed_pipeline(db)
    deal = await crm_records.create_record(
        DEALS, crm_core.DealIn(name="Printer order"), USER,
    )
    assert deal["probability"] == 10


async def test_a_stated_probability_is_not_overwritten(db: FakeCrmDB) -> None:
    _seed_pipeline(db)
    deal = await crm_records.create_record(
        DEALS, crm_core.DealIn(name="Printer order", probability=90), USER,
    )
    assert deal["probability"] == 90


async def test_a_deal_created_directly_into_a_terminal_status_gets_closed_at(
    db: FakeCrmDB,
) -> None:
    """Verifier finding 2026-08-05: PATCH into Closed Won stamped closed_at
    while POST straight into it stored NULL — §3.6's rule belongs to the
    status type, so it must reach both verbs (the same reach as the lost gate).
    """
    _seed_pipeline(db)
    won = db.seed(
        "crm_deal_statuses", name="Closed Won", position=60, type="won",
        is_default=False, probability=100,
    )
    deal = await crm_records.create_record(
        DEALS,
        crm_core.DealIn(name="Printer order", status_id=str(won.id)),
        USER,
    )
    assert deal["closed_at"] is not None


async def test_an_absent_owner_defaults_to_the_acting_user(db: FakeCrmDB) -> None:
    """Identity comes from the authenticated context (R3), never a body field."""
    _seed_pipeline(db)
    deal = await crm_records.create_record(
        DEALS, crm_core.DealIn(name="Printer order"), USER,
    )
    assert deal["owner_email"] == USER.email


async def test_an_explicit_null_owner_stays_unassigned(db: FakeCrmDB) -> None:
    """"Deliberately unassigned" and "not mentioned" are different requests."""
    _seed_pipeline(db)
    deal = await crm_records.create_record(
        DEALS, crm_core.DealIn.model_validate(
            {"name": "Printer order", "owner_email": None},
        ), USER,
    )
    assert deal["owner_email"] is None


async def test_an_unknown_source_is_422(db: FakeCrmDB) -> None:
    """The migration's CHECK is the boundary of record; this is the same rule
    stated where it produces a 422 instead of a driver 500."""
    _seed_pipeline(db)
    with pytest.raises(HTTPException) as exc:
        await crm_records.create_record(
            DEALS,
            crm_core.DealIn.model_validate({"name": "x", "source": "telepathy"}),
            USER,
        )
    assert exc.value.status_code == 422


# ── WS-26h2 · entry requirements on the CHOSEN create stage ─────────────────
#
# D-CRM-13: entry requirements gate the stage a caller CHOSE, never the stage
# the server DEFAULTED to. `records._resolve_status` already distinguished the
# two — `values.get("status_id")` is the caller's claim, `load_default_status`
# is the server's — and only the first is a claim worth checking. The whole
# reason the rule is shaped this way is the second test below: every deal the
# PRODUCT creates lands in the default lane (`QuickCreateModal` sends no
# `status_id`), so gating the defaulted path would 422 quick-create for
# everyone the moment an owner put a requirement on the first lane.


def _gate_stage(db: FakeCrmDB, stage, *fields: str) -> None:
    """Put entry requirements on a seeded stage, as the settings grid would."""
    for row in db.rows("crm_deal_statuses"):
        if str(row["id"]) == str(stage.id):
            row["required_fields"] = list(fields)
            return
    raise AssertionError(f"no such seeded stage: {stage}")


def _late_stage(db: FakeCrmDB, *fields: str):
    """A non-default lane a caller may name explicitly, with requirements."""
    stage = db.seed(
        "crm_deal_statuses", name="Proposal", position=30, type="ongoing",
        is_default=False, probability=50,
    )
    _gate_stage(db, stage, *fields)
    return stage


async def test_creating_into_a_chosen_gated_stage_without_the_fields_is_422(
    db: FakeCrmDB,
) -> None:
    """Done-when 1 — and the refusal names EXACTLY what is missing: a stage
    demanding three fields, two of them absent, must not also name the one the
    body supplied, or the modal asks for something already filled in."""
    _seed_pipeline(db)
    stage = _late_stage(db, "amount", "expected_close_date", "organization_id")

    with pytest.raises(HTTPException) as exc:
        await crm_records.create_record(
            DEALS,
            crm_core.DealIn(
                name="Printer order", status_id=str(stage.id),
                organization_id=str(uuid4()),
            ),
            USER,
        )

    assert exc.value.status_code == 422
    detail = str(exc.value.detail)
    assert "Proposal" in detail
    assert "amount" in detail
    assert "expected_close_date" in detail
    assert "organization_id" not in detail


async def test_a_refused_create_writes_no_deal_at_all(db: FakeCrmDB) -> None:
    """Done-when 1's other half. Refused BEFORE `insert_row`, exactly as the
    move gate is refused before the transition's three effects — a 422 that
    still leaves a row is worse than no gate, because the caller believes
    nothing happened."""
    _seed_pipeline(db)
    stage = _late_stage(db, "amount")

    with pytest.raises(HTTPException):
        await crm_records.create_record(
            DEALS,
            crm_core.DealIn(name="Printer order", status_id=str(stage.id)),
            USER,
        )

    assert db.rows(DEALS.table) == []
    assert db.committed == 0


async def test_the_same_body_may_carry_the_missing_fields(db: FakeCrmDB) -> None:
    """Done-when 2 — one request, not "create it and then fill this in"."""
    _seed_pipeline(db)
    stage = _late_stage(db, "amount")

    deal = await crm_records.create_record(
        DEALS,
        crm_core.DealIn(
            name="Printer order", status_id=str(stage.id), amount=400000,
        ),
        USER,
    )

    assert deal["status_id"] == str(stage.id)
    assert deal["amount"] == 400000
    assert len(db.rows(DEALS.table)) == 1


async def test_a_requirement_on_the_DEFAULT_lane_never_blocks_a_create(
    db: FakeCrmDB,
) -> None:
    """Done-when 3 — **D-CRM-13**, and the reason this ticket is not "close the
    CREATE gap".

    A deal in the default lane has claimed nothing yet, so demanding proof of
    progress to enter it is a category error. Mechanically: `QuickCreateModal`
    sends no `status_id`, so gating the defaulted path would 422 the only
    create the product actually performs, for every user, the moment an owner
    saved a requirement on the first lane — which `admin.py` permits with no
    restriction at all.
    """
    _, default_stage = _seed_pipeline(db)
    _gate_stage(db, default_stage, "amount")

    deal = await crm_records.create_record(
        DEALS, crm_core.DealIn(name="Printer order"), USER,
    )

    assert deal["status_id"] == str(default_stage.id)
    assert deal["amount"] is None


async def test_naming_the_default_lane_explicitly_IS_a_choice(
    db: FakeCrmDB,
) -> None:
    """The condition is "did the caller supply `status_id`", not "is this lane
    the default" — a body that names a stage has made the claim whichever lane
    it names, and a rule keyed on the lane's `is_default` flag would flip for
    every deal in flight the moment somebody moved the default."""
    _, default_stage = _seed_pipeline(db)
    _gate_stage(db, default_stage, "amount")

    with pytest.raises(HTTPException) as exc:
        await crm_records.create_record(
            DEALS,
            crm_core.DealIn(name="Printer order", status_id=str(default_stage.id)),
            USER,
        )

    assert exc.value.status_code == 422


async def test_an_absent_owner_is_never_missing_on_a_gated_stage(
    db: FakeCrmDB,
) -> None:
    """Done-when 5 — `create_record` defaults an ABSENT `owner_email` to the
    acting user before the status is resolved, so an `owner_email` requirement
    is satisfied by every create that does not deliberately unassign."""
    _seed_pipeline(db)
    stage = _late_stage(db, "owner_email")

    deal = await crm_records.create_record(
        DEALS, crm_core.DealIn(name="Printer order", status_id=str(stage.id)),
        USER,
    )

    assert deal["owner_email"] == USER.email


async def test_an_explicit_null_owner_is_missing_on_a_gated_stage(
    db: FakeCrmDB,
) -> None:
    """The other half of done-when 5. "Deliberately unassigned" and "not
    mentioned" are different requests here too — and a lane that requires an
    owner is refusing exactly the first one."""
    _seed_pipeline(db)
    stage = _late_stage(db, "owner_email")

    with pytest.raises(HTTPException) as exc:
        await crm_records.create_record(
            DEALS,
            crm_core.DealIn.model_validate({
                "name": "Printer order",
                "status_id": str(stage.id),
                "owner_email": None,
            }),
            USER,
        )

    assert exc.value.status_code == 422
    assert "owner_email" in str(exc.value.detail)


async def test_a_zero_amount_satisfies_a_create_requirement(
    db: FakeCrmDB,
) -> None:
    """Done-when 6 — `0` is a number somebody typed, and `_is_blank`'s
    semantics are asserted on the create path rather than assumed to carry.
    (`Decimal('0.00')` and blank text are in `test_crm_pipeline.py`, at the
    gate: `DealIn.amount` is `float | None`, so neither survives the model.)"""
    _seed_pipeline(db)
    stage = _late_stage(db, "amount")

    deal = await crm_records.create_record(
        DEALS,
        crm_core.DealIn(name="Printer order", status_id=str(stage.id), amount=0),
        USER,
    )

    assert deal["amount"] == 0


async def test_a_blank_owner_does_not_satisfy_a_create_requirement(
    db: FakeCrmDB,
) -> None:
    """The opposite end of the same rule — an empty text box is not a filled
    one, on POST as on PATCH."""
    _seed_pipeline(db)
    stage = _late_stage(db, "owner_email")

    with pytest.raises(HTTPException) as exc:
        await crm_records.create_record(
            DEALS,
            crm_core.DealIn(
                name="Printer order", status_id=str(stage.id), owner_email="   ",
            ),
            USER,
        )

    assert exc.value.status_code == 422
    assert db.rows(DEALS.table) == []


async def test_creating_a_lead_into_a_chosen_lead_status_is_never_gated(
    db: FakeCrmDB,
) -> None:
    """Done-when 7 — asserted by running the path, not inferred from the absent
    column.

    `auto_lead.py` reaches `create_record` for LEADS on the mail hook, and this
    is the branch it takes: an explicitly chosen status, so the gate IS
    entered. It must read a status row that has no `required_fields` attribute
    at all as "nothing required" rather than raising — the same asymmetry
    `probability` and `status_changed_at` already have. That the column can
    never appear on `crm_lead_statuses` is pinned separately, in
    `test_crm_migration.py::test_lead_statuses_gain_nothing`.
    """
    lead_status, _ = _seed_pipeline(db)
    contacted = db.seed(
        "crm_lead_statuses", name="Contacted", position=20, type="ongoing",
        is_default=False,
    )

    lead = await crm_records.create_record(
        LEADS,
        crm_core.LeadIn(first_name="Asha", status_id=str(contacted.id)),
        USER,
    )

    assert lead["status_id"] == str(contacted.id)
    assert len(db.rows(LEADS.table)) == 1
    assert str(lead_status.id) != str(contacted.id)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"first_name": "Anitha", "last_name": "Kumar"}, "Anitha Kumar"),
        ({"organization_name": "Bosch India"}, "Bosch India"),
        ({"email": "anitha@acme.in"}, "anitha"),
        ({}, "Unnamed lead"),
    ],
)
async def test_the_lead_name_fallback_chain(
    db: FakeCrmDB, payload: dict, expected: str,
) -> None:
    """§3.3 — a NOT NULL display column with no computed fallback is how
    'Untitled' ends up in a pipeline."""
    _seed_pipeline(db)
    lead = await crm_records.create_record(
        LEADS, crm_core.LeadIn.model_validate(payload), USER,
    )
    assert lead["lead_name"] == expected


async def test_a_lead_name_is_re_derived_when_its_inputs_change(
    db: FakeCrmDB,
) -> None:
    lead_status, _ = _seed_pipeline(db)
    seeded = db.seed(
        LEADS.table, lead_name="Anitha", first_name="Anitha",
        status_id=lead_status.id,
    )
    patched = await crm_records.patch_record(
        LEADS, str(seeded.id), crm_core.LeadIn(last_name="Kumar"), USER,
    )
    assert patched["lead_name"] == "Anitha Kumar"


async def test_an_untouched_lead_name_is_left_alone(db: FakeCrmDB) -> None:
    """A hand-edited display name survives a patch that does not touch its
    inputs — otherwise every unrelated edit silently reverts it."""
    lead_status, _ = _seed_pipeline(db)
    seeded = db.seed(
        LEADS.table, lead_name="Bosch — printer RFQ", first_name="Anitha",
        status_id=lead_status.id,
    )
    patched = await crm_records.patch_record(
        LEADS, str(seeded.id), crm_core.LeadIn(description="called back"), USER,
    )
    assert patched["lead_name"] == "Bosch — printer RFQ"


# ── Driver-shaped writes ────────────────────────────────────────────────────
#
# These three are the class of defect a fake cannot find on its own: every case
# above passes with a dict bound straight at a jsonb column and an ISO string
# bound straight at a timestamptz, and both are driver errors against real
# Postgres. `coerce_write_values` / the jsonb cast are the one choke point, so
# they are pinned where they are decided.

async def test_a_jsonb_column_is_cast_and_serialized(db: FakeCrmDB) -> None:
    """asyncpg has no codec for a bare dict; raw `text()` declares no column
    type, so the statement has to say `jsonb` and the value has to be JSON."""
    await crm_records.create_record(
        ORGANIZATIONS,
        crm_core.OrganizationIn(
            name="Bosch India", address={"city": "Bengaluru", "pin": "560058"},
        ),
        USER,
    )
    [insert] = db.statements_touching("INSERT INTO crm_companies")
    assert "CAST(:address AS jsonb)" in insert
    [(_, params)] = [c for c in db.calls if "INSERT INTO crm_companies" in c[0]]
    assert isinstance(params["address"], str)


async def test_a_jsonb_column_round_trips_back_to_a_dict(db: FakeCrmDB) -> None:
    """The read half: jsonb comes back from the driver as a STRING, and a model
    field typed `dict` would reject it."""
    created = await crm_records.create_record(
        ORGANIZATIONS,
        crm_core.OrganizationIn(name="Bosch India", address={"city": "Bengaluru"}),
        USER,
    )
    assert created["address"] == {"city": "Bengaluru"}


async def test_an_iso_instant_is_parsed_into_a_datetime(db: FakeCrmDB) -> None:
    """asyncpg binds real datetimes. A string reaches timestamptz as text."""
    org = db.seed(ORGANIZATIONS.table, name="Bosch India")
    await crm_activities._log_activity(
        ORGANIZATIONS, str(org.id),
        crm_activities.ActivityIn(type="call", due_at="2026-09-01T10:30:00+00:00"),
        USER,
    )
    [(_, params)] = [c for c in db.calls if "INSERT INTO crm_activities" in c[0]]
    assert params["due_at"].year == 2026 and params["due_at"].month == 9


async def test_an_iso_date_is_parsed_into_a_date(db: FakeCrmDB) -> None:
    _seed_pipeline(db)
    await crm_records.create_record(
        DEALS,
        crm_core.DealIn(name="Printer order", expected_close_date="2026-09-30"),
        USER,
    )
    [(_, params)] = [c for c in db.calls if "INSERT INTO crm_deals" in c[0]]
    assert str(params["expected_close_date"]) == "2026-09-30"


async def test_a_malformed_instant_is_422_naming_the_column(db: FakeCrmDB) -> None:
    """A 422 a caller can act on, rather than a driver error they cannot."""
    org = db.seed(ORGANIZATIONS.table, name="Bosch India")
    with pytest.raises(HTTPException) as exc:
        await crm_activities._log_activity(
            ORGANIZATIONS, str(org.id),
            crm_activities.ActivityIn(due_at="next tuesday"), USER,
        )
    assert exc.value.status_code == 422
    assert "due_at" in str(exc.value.detail)


def test_every_jsonb_and_temporal_column_is_declared() -> None:
    """Structural: the coercion lists are hand-kept, so pin them against the
    migration. A column added there and forgotten here is a driver error on
    the first request that sets it."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2] / "infra" / "postgres" / "144_crm.sql"
    ).read_text(encoding="utf-8")

    declared = {
        "JSONB": crm_core.JSONB_COLUMNS,
        "TIMESTAMPTZ": crm_core.TIMESTAMP_COLUMNS,
        "DATE": crm_core.DATE_COLUMNS,
    }
    for sql_type, names in declared.items():
        for line in migration.splitlines():
            parts = line.strip().split()
            if len(parts) < 2 or parts[1].upper() != sql_type:
                continue
            column = parts[0]
            if column in ("created_at", "updated_at"):
                continue  # never settable from a request body
            assert column in names or any(
                column in group for group in declared.values()
            ), f"{column} is {sql_type} in 144_crm.sql but no list claims it"


# ── Read / update / delete ──────────────────────────────────────────────────

async def test_an_unknown_id_is_404(db: FakeCrmDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await crm_records._get(DEALS, "00000000-0000-0000-0000-000000000000")
    assert exc.value.status_code == 404
    assert "Deal" in str(exc.value.detail)


async def test_a_patch_with_no_fields_writes_nothing(db: FakeCrmDB) -> None:
    seeded = db.seed(ORGANIZATIONS.table, name="Bosch India")
    await crm_records.patch_record(
        ORGANIZATIONS, str(seeded.id), crm_core.OrganizationIn(), USER,
    )
    assert not db.statements_touching("UPDATE")


async def test_a_patch_touches_updated_at(db: FakeCrmDB) -> None:
    seeded = db.seed(ORGANIZATIONS.table, name="Bosch India")
    await crm_records.patch_record(
        ORGANIZATIONS, str(seeded.id), crm_core.OrganizationIn(industry="Auto"), USER,
    )
    [update] = db.statements_touching("UPDATE crm_companies SET")
    assert "updated_at = now()" in update


async def test_delete_reports_what_cascaded(db: FakeCrmDB) -> None:
    """R7/R8 — 'deleted' with no blast radius is the response that hides a
    cascade. Counted BEFORE the delete: afterwards the honest number is gone."""
    _, deal_status = _seed_pipeline(db)
    deal = db.seed(DEALS.table, name="Printer order", status_id=deal_status.id)
    db.seed("crm_activities", type="note", deal_id=deal.id, created_by="a@b.in")
    db.seed("crm_activities", type="call", deal_id=deal.id, created_by="a@b.in")
    db.seed("crm_deal_contacts", deal_id=deal.id, contact_id=str(deal.id))

    result = await crm_records.delete_record(DEALS, str(deal.id), USER)

    assert result.cascaded == {"crm_activities": 2, "crm_deal_contacts": 1}
    # WS-26b: a record Zoho has never seen leaves no tombstone — there is
    # nothing upstream to delete, and an un-pushable row would sit in the
    # backlog forever.
    assert result.zoho_delete_queued is False
    assert not db.rows("crm_zoho_tombstones")
    counts = [s for s in db.statements if s.startswith("SELECT count(*)")]
    deletes = [s for s in db.statements if s.startswith("DELETE")]
    assert db.statements.index(counts[-1]) < db.statements.index(deletes[0])


def test_every_entity_declares_the_tables_its_delete_cascades() -> None:
    """Structural, because the fake models no foreign keys and therefore no
    cascades: a behavioural case here would agree that nothing was destroyed.

    The registry's `cascades` is what the response reports, so it is pinned
    against the FK graph derived from the migrations themselves.
    """
    from tests.unit._schema_cascade import cascade_children

    for entity in ENTITIES.values():
        declared = {table for table, _ in entity.cascades}
        actual = cascade_children(entity.table)
        assert declared == actual, (
            f"{entity.table} declares cascades onto {sorted(declared)} but "
            f"infra/postgres/ says {sorted(actual)}. An UNDERSTATED map is the "
            "wrong direction of error: the delete response is what tells an "
            "operator the blast radius before they click."
        )


# ── Activities ──────────────────────────────────────────────────────────────

async def test_logging_an_activity_stamps_the_target_and_the_author(
    db: FakeCrmDB,
) -> None:
    """§3.8's CHECK requires at least one target. The target here is
    STRUCTURAL — it comes from the entity registry, not from the request — so
    the constraint cannot be reached with all four columns NULL."""
    _, deal_status = _seed_pipeline(db)
    deal = db.seed(DEALS.table, name="Printer order", status_id=deal_status.id)

    logged = await crm_activities._log_activity(
        DEALS, str(deal.id), crm_activities.ActivityIn(type="call", subject="Rang"),
        USER,
    )

    assert logged["deal_id"] == str(deal.id)
    assert logged["lead_id"] is None
    assert logged["created_by"] == USER.email


@pytest.mark.parametrize("slug", sorted(ENTITIES))
async def test_every_entity_logs_to_its_own_target_column(
    db: FakeCrmDB, slug: str,
) -> None:
    entity = ENTITIES[slug]
    seeded = db.seed(entity.table, **(
        {"lead_name": "x"} if entity is LEADS else
        {"first_name": "x"} if entity is CONTACTS else {"name": "x"}
    ))
    logged = await crm_activities._log_activity(
        entity, str(seeded.id), crm_activities.ActivityIn(body="note"), USER,
    )
    assert logged[entity.activity_column] == str(seeded.id)
    targets = {"lead_id", "deal_id", "contact_id", "organization_id"}
    assert [c for c in targets if logged[c] is not None] == [entity.activity_column]


async def test_an_activity_bumps_its_targets_last_activity_at(
    db: FakeCrmDB,
) -> None:
    """trycompai's denormalization discipline — the reason 'sort by last
    touched' is an index scan and not a correlated subquery."""
    org = db.seed(ORGANIZATIONS.table, name="Bosch India")
    await crm_activities._log_activity(
        ORGANIZATIONS, str(org.id), crm_activities.ActivityIn(body="met"), USER,
    )
    assert db.statements_touching(
        "UPDATE crm_companies SET last_activity_at = now()"
    )


@pytest.mark.parametrize("kind", ["status_change", "system"])
async def test_a_platform_activity_type_cannot_be_hand_logged(
    db: FakeCrmDB, kind: str,
) -> None:
    """A hand-written status_change is a funnel event with no transition
    behind it — the log would say something happened that did not."""
    org = db.seed(ORGANIZATIONS.table, name="Bosch India")
    with pytest.raises(HTTPException) as exc:
        await crm_activities._log_activity(
            ORGANIZATIONS, str(org.id), crm_activities.ActivityIn(type=kind), USER,
        )
    assert exc.value.status_code == 422


async def test_a_platform_activity_cannot_be_deleted(db: FakeCrmDB) -> None:
    entry = db.seed(
        "crm_activities", type="status_change", created_by="a@b.in",
        deal_id=str(db.seed(DEALS.table, name="d").id),
    )
    with pytest.raises(HTTPException) as exc:
        await crm_activities.delete_activity(str(entry.id), USER)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("kind", ["status_change", "system"])
async def test_a_platform_activity_cannot_be_edited_either(
    db: FakeCrmDB, kind: str,
) -> None:
    """Review finding 2026-08-05: one rule, both verbs. An edited
    status_change disagrees with crm_status_changes with no way to tell which
    one is lying — so PATCH refuses exactly what DELETE refuses."""
    entry = db.seed(
        "crm_activities", type=kind, created_by="a@b.in",
        deal_id=str(db.seed(DEALS.table, name="d").id),
    )
    with pytest.raises(HTTPException) as exc:
        await crm_activities.patch_activity(
            str(entry.id), crm_activities.ActivityPatch(subject="rewritten"), USER,
        )
    assert exc.value.status_code == 409
    assert not db.statements_touching("UPDATE crm_activities SET")


async def test_record_activity_bumps_the_target_itself(db: FakeCrmDB) -> None:
    """Review finding 2026-08-05: the docstring promised the bump and the body
    didn't do it — correct for today's callers, wrong for the next one (the
    WS-26b importer, WS-26d's agent tools). Now the function keeps its own
    promise."""
    deal = db.seed(DEALS.table, name="Printer order")
    await crm_core.record_activity(
        db, activity_type="note", created_by="a@b.in",
        target_column="deal_id", target_id=str(deal.id),
    )
    assert db.statements_touching("UPDATE crm_deals SET last_activity_at = now()")


async def test_completing_a_task_writes_completed_at(db: FakeCrmDB) -> None:
    entry = db.seed(
        "crm_activities", type="task", created_by="a@b.in", completed_at=None,
        deal_id=str(db.seed(DEALS.table, name="d").id),
    )
    done = await crm_activities.patch_activity(
        str(entry.id),
        crm_activities.ActivityPatch(completed_at="2026-08-05T10:00:00+00:00"),
        USER,
    )
    assert done["completed_at"] == "2026-08-05T10:00:00+00:00"


async def test_an_activity_update_does_not_touch_a_column_it_has_not_got(
    db: FakeCrmDB,
) -> None:
    """`crm_activities` carries no `updated_at` — it is a log entry, not a
    record. Writing one would be a 500 from the driver."""
    entry = db.seed(
        "crm_activities", type="note", created_by="a@b.in",
        deal_id=str(db.seed(DEALS.table, name="d").id),
    )
    await crm_activities.patch_activity(
        str(entry.id), crm_activities.ActivityPatch(body="edited"), USER,
    )
    [update] = db.statements_touching("UPDATE crm_activities SET")
    assert "updated_at" not in update


# ── Admin: statuses and lost reasons ────────────────────────────────────────

async def test_deleting_a_status_in_use_is_409_not_a_driver_error(
    db: FakeCrmDB,
) -> None:
    """The FK is ON DELETE RESTRICT, so Postgres refuses it either way — but as
    an IntegrityError and a 500. Counting first turns 'the database said no'
    into 'Proposal still has 1 deal in it'."""
    _, deal_status = _seed_pipeline(db)
    db.seed(DEALS.table, name="Printer order", status_id=deal_status.id)

    with pytest.raises(HTTPException) as exc:
        await crm_admin.delete_status("deal", str(deal_status.id), USER)

    assert exc.value.status_code == 409
    assert not db.statements_touching("DELETE FROM crm_deal_statuses")


async def test_deleting_an_unused_status_succeeds(db: FakeCrmDB) -> None:
    _, deal_status = _seed_pipeline(db)
    result = await crm_admin.delete_status("deal", str(deal_status.id), USER)
    assert result["deleted"] == str(deal_status.id)
    assert db.rows("crm_deal_statuses") == []


async def test_an_unknown_status_kind_is_404(db: FakeCrmDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await crm_admin.list_statuses("prospect", USER)
    assert exc.value.status_code == 404


async def test_a_new_lane_is_appended_not_prepended(db: FakeCrmDB) -> None:
    """Defaulting position to 0 would silently reorder every existing lane."""
    _seed_pipeline(db)
    created = await crm_admin.create_status(
        "deal", crm_admin.StatusIn(name="Pilot", type="ongoing"), USER,
    )
    assert created.position > 10


async def test_a_lead_status_cannot_carry_a_probability(db: FakeCrmDB) -> None:
    """That column is deal-only; accepting it would silently drop the value."""
    with pytest.raises(HTTPException) as exc:
        await crm_admin.create_status(
            "lead", crm_admin.StatusIn(name="Pilot", probability=50), USER,
        )
    assert exc.value.status_code == 422


async def test_an_unknown_status_type_is_422(db: FakeCrmDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await crm_admin.create_status(
            "deal", crm_admin.StatusIn(name="Pilot", type="maybe"), USER,
        )
    assert exc.value.status_code == 422


# ── WS-26f f2 · the D-CRM-10 probability clamp (done-when 5, server side) ───
#
# The rule: won-type lanes forecast 100 and lost-type lanes 0, refused as a
# 422 rather than silently rewritten. Probability *informs* the forecast and
# `type` *decides* what closes, so a stage typed won at 40% cannot close a deal
# — it can only halve the weighted pipeline every money surface reads.

@pytest.mark.parametrize("probability", [-1, 101, 1000])
async def test_a_probability_outside_zero_to_a_hundred_is_422(
    db: FakeCrmDB, probability: int,
) -> None:
    with pytest.raises(HTTPException) as exc:
        await crm_admin.create_status(
            "deal",
            crm_admin.StatusIn(name="Pilot", type="open", probability=probability),
            USER,
        )
    assert exc.value.status_code == 422
    assert "0-100" in str(exc.value.detail)
    assert db.rows("crm_deal_statuses") == []


@pytest.mark.parametrize(
    ("status_type", "probability"), [("won", 40), ("lost", 25), ("won", 0)],
)
async def test_a_terminal_stage_with_the_wrong_probability_is_422_on_create(
    db: FakeCrmDB, status_type: str, probability: int,
) -> None:
    with pytest.raises(HTTPException) as exc:
        await crm_admin.create_status(
            "deal",
            crm_admin.StatusIn(
                name="Delivered", type=status_type, probability=probability,
            ),
            USER,
        )
    assert exc.value.status_code == 422
    assert "D-CRM-10" in str(exc.value.detail)
    assert db.rows("crm_deal_statuses") == []


async def test_a_won_stage_created_without_a_probability_is_refused_too(
    db: FakeCrmDB,
) -> None:
    """The column is NOT NULL DEFAULT 0, so omitting it does not mean "leave
    it alone" — it means "land this won stage at 0%". The clamp reads the value
    the write will LEAVE, not the one the caller happened to mention."""
    with pytest.raises(HTTPException) as exc:
        await crm_admin.create_status(
            "deal", crm_admin.StatusIn(name="Delivered", type="won"), USER,
        )
    assert exc.value.status_code == 422
    assert "100" in str(exc.value.detail)


async def test_the_terminal_probabilities_are_still_settable(
    db: FakeCrmDB,
) -> None:
    """The other half — the clamp is a rule, not a ban on terminal stages."""
    won = await crm_admin.create_status(
        "deal",
        crm_admin.StatusIn(name="Delivered", type="won", probability=100),
        USER,
    )
    lost = await crm_admin.create_status(
        "deal", crm_admin.StatusIn(name="Dropped", type="lost", probability=0), USER,
    )
    assert (won.type, won.probability) == ("won", 100)
    assert (lost.type, lost.probability) == ("lost", 0)


# The clamp judges a TRANSITION, not a resting state. `import_zoho.ensure_status`
# mints an unseen Zoho stage at probability 0 and guesses its type from the name,
# so every pull can create a won-type lane at 0% — a row that already contradicts
# D-CRM-10 the moment it exists. Judging the resting state made those lanes
# unmanageable by the very grid that exists to fix them.

def _legacy_won_lane(db: FakeCrmDB):
    """The shape a Zoho pull mints: won by name-guess, probability 0."""
    return db.seed(
        "crm_deal_statuses", name="Order Won", position=70, type="won",
        probability=0,
    )


async def test_a_position_only_patch_never_trips_the_clamp(
    db: FakeCrmDB,
) -> None:
    """The one that was broken: reordering is the settings grid's whole job,
    and it PATCHes `position` alone on every lane that moved. A 422 about a
    probability the caller never mentioned aborts the reorder loop partway and
    leaves duplicate positions — a worse state than either order."""
    seeded = _legacy_won_lane(db)

    patched = await crm_admin.patch_status(
        "deal", str(seeded.id), crm_admin.StatusIn(position=20), USER,
    )

    assert patched.position == 20
    assert (patched.type, patched.probability) == ("won", 0)


async def test_a_rename_only_patch_on_a_contradictory_lane_succeeds(
    db: FakeCrmDB,
) -> None:
    """Same rule, the other everyday edit: the first thing an owner does to an
    importer-minted lane is give it the team's name for it."""
    seeded = _legacy_won_lane(db)

    patched = await crm_admin.patch_status(
        "deal", str(seeded.id),
        crm_admin.StatusIn(name="Order Delivered", color="green"), USER,
    )

    assert patched.name == "Order Delivered"
    assert patched.color == "green"


async def test_is_default_and_colour_are_not_forecast_decisions(
    db: FakeCrmDB,
) -> None:
    seeded = _legacy_won_lane(db)
    patched = await crm_admin.patch_status(
        "deal", str(seeded.id), crm_admin.StatusIn(is_default=True), USER,
    )
    assert patched.is_default is True


async def test_the_clamp_still_fires_when_the_payload_names_the_probability(
    db: FakeCrmDB,
) -> None:
    """The narrowing must not become a hole: touching the FORECAST on the same
    contradictory lane is still judged, because that is a transition."""
    seeded = _legacy_won_lane(db)

    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "deal", str(seeded.id), crm_admin.StatusIn(probability=40), USER,
        )

    assert exc.value.status_code == 422
    assert "D-CRM-10" in str(exc.value.detail)
    assert db.rows("crm_deal_statuses")[0]["probability"] == 0


async def test_the_way_out_of_a_contradictory_lane_is_still_open(
    db: FakeCrmDB,
) -> None:
    """And the repair itself lands: 0 → 100 on a won lane is exactly the edit
    f1's apply performs and f2's grid offers."""
    seeded = _legacy_won_lane(db)
    patched = await crm_admin.patch_status(
        "deal", str(seeded.id), crm_admin.StatusIn(probability=100), USER,
    )
    assert patched.probability == 100


async def test_retyping_a_stage_to_won_without_moving_its_probability_is_422(
    db: FakeCrmDB,
) -> None:
    """A PATCH naming only ``type`` contradicts the rule in combination with
    what is already stored — which is why the row is loaded BEFORE the payload
    is validated. Reading only the payload would wave this through."""
    seeded = db.seed(
        "crm_deal_statuses", name="Proposal", position=30, type="ongoing",
        probability=50,
    )
    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "deal", str(seeded.id), crm_admin.StatusIn(type="won"), USER,
        )
    assert exc.value.status_code == 422
    assert db.rows("crm_deal_statuses")[0]["type"] == "ongoing"


async def test_retyping_a_stage_that_already_forecasts_a_hundred_is_allowed(
    db: FakeCrmDB,
) -> None:
    """The positive half of the same rule, and the case that proves the check
    reads the ROW: a stage already at 100 becoming won is not a contradiction,
    and refusing it would mean the clamp were really "any PATCH naming type"."""
    seeded = db.seed(
        "crm_deal_statuses", name="Order Delivered", position=70, type="ongoing",
        probability=100,
    )
    patched = await crm_admin.patch_status(
        "deal", str(seeded.id), crm_admin.StatusIn(type="won"), USER,
    )
    assert (patched.type, patched.probability) == ("won", 100)


async def test_lowering_a_won_stages_probability_is_422(db: FakeCrmDB) -> None:
    """The mirror image: a PATCH naming only ``probability`` on a stage that is
    already won."""
    seeded = db.seed(
        "crm_deal_statuses", name="Closed Won", position=50, type="won",
        probability=100,
    )
    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "deal", str(seeded.id), crm_admin.StatusIn(probability=80), USER,
        )
    assert exc.value.status_code == 422
    assert db.rows("crm_deal_statuses")[0]["probability"] == 100


async def test_type_and_probability_may_move_together(db: FakeCrmDB) -> None:
    """Explicit beats silent rewrite: the way through the clamp is to state
    both, which is exactly what the settings grid sends."""
    seeded = db.seed(
        "crm_deal_statuses", name="Order Received", position=70, type="open",
        probability=0,
    )
    patched = await crm_admin.patch_status(
        "deal", str(seeded.id),
        crm_admin.StatusIn(type="won", probability=100), USER,
    )
    assert (patched.type, patched.probability) == ("won", 100)


async def test_reordering_a_terminal_lane_does_not_trip_the_clamp(
    db: FakeCrmDB,
) -> None:
    """The reorder grid PATCHes ``position`` alone, on every lane including the
    won one. A clamp that read a missing probability as 0 would make the
    pipeline un-reorderable — the exact surface f2 exists to provide."""
    seeded = db.seed(
        "crm_deal_statuses", name="Closed Won", position=50, type="won",
        probability=100,
    )
    patched = await crm_admin.patch_status(
        "deal", str(seeded.id), crm_admin.StatusIn(position=90), USER,
    )
    assert patched.position == 90


async def test_a_lead_status_never_reaches_the_probability_clamp(
    db: FakeCrmDB,
) -> None:
    """``crm_lead_statuses`` has no probability column, so a won lead status is
    not a contradiction — it is a lane with no forecast at all."""
    created = await crm_admin.create_status(
        "lead", crm_admin.StatusIn(name="Qualified", type="won"), USER,
    )
    assert created.type == "won"
    assert created.probability is None


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_the_router_is_gated_on_feature_crm() -> None:
    """Router-level, so an endpoint added tomorrow is covered by default —
    the failure mode of per-route gating is the route someone forgets."""
    names = [
        getattr(getattr(d, "dependency", None), "__qualname__", "")
        for d in crm_core.router.dependencies
    ]
    assert any(n.startswith("require_feature_router") for n in names)


def test_no_crm_module_opens_its_own_engine() -> None:
    """D-CRM-4 / BO-10, grep-assertable: this package consumes `gateway.db` and
    adds no thirteenth engine. Read off the source, because an engine created
    lazily inside a function would never show up in a behavioural test."""
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    checked = 0
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        checked += 1
        # The CALL, not the name: the module docstrings discuss the seam by
        # name, and a check that cannot survive its own explanation is one
        # somebody deletes.
        assert "create_async_engine(" not in source, (
            f"{path.name} creates its own engine — routes/crm must consume "
            "gateway.db (specs/crm_app.md D-CRM-4)."
        )
    assert checked >= 5, "the crm package lost modules — this check went vacuous"
    assert "from gateway.db import" in (package / "core.py").read_text(
        encoding="utf-8",
    )


def test_tasks_consumes_the_shared_engine_seam() -> None:
    """The other half of D-CRM-4: `routes/tasks/core.py` was converted as the
    proof the seam works, so the seam has a consumer that predates it."""
    from pathlib import Path

    import gateway.routes.tasks.core as tasks_core

    source = Path(tasks_core.__file__).read_text(encoding="utf-8")
    assert "create_async_engine" not in source
    assert "from gateway.db import" in source


def test_every_entity_sort_allowlist_names_real_columns() -> None:
    """An allowlisted key pointing at a column that does not exist is a 500
    waiting for the first person who sorts by it."""
    for entity in ENTITIES.values():
        fields = set(entity.model.model_fields)
        unknown = {v for v in entity.sorts.values()} - fields
        assert not unknown, f"{entity.slug} sorts by unknown column(s): {unknown}"
        assert entity.default_sort in entity.sorts


# ── WS-26c · review residuals (spec §9 WS-26c done-when 3) ──────────────────
#
# Three ways a request is answered wrongly-but-quietly today. Each is a 200 or
# a driver 500 before these, and a 422 after.

@pytest.mark.parametrize("slug", ["contacts", "organizations"])
async def test_a_status_filter_on_an_entity_without_a_pipeline_is_422(
    db: FakeCrmDB, slug: str,
) -> None:
    """Not silently ignored: ignoring it answers 200 with the WHOLE table to a
    caller who asked for one lane — and the shared list component the UI
    reuses across all four entities is exactly what sends it."""
    entity = ENTITIES[slug]
    db.seed(entity.table, **{f: "x" for f in ("name", "first_name")
                             if f in entity.model.model_fields})
    with pytest.raises(HTTPException) as raised:
        await crm_records._list(entity, _params(status_id="not-a-lane"))
    assert raised.value.status_code == 422
    assert "status" in str(raised.value.detail).lower()


@pytest.mark.parametrize("slug", ["leads", "deals"])
async def test_a_status_filter_still_works_where_there_is_a_pipeline(
    db: FakeCrmDB, slug: str,
) -> None:
    """The other half of the rule — a guard that refuses everything is not a
    guard, and this pair is what tells the two apart."""
    lead_status, deal_status = _seed_pipeline(db)
    entity = ENTITIES[slug]
    wanted = lead_status if slug == "leads" else deal_status
    db.seed(
        entity.table, name="Bosch RFQ", lead_name="Bosch RFQ",
        status_id=wanted.id,
    )
    db.seed(
        entity.table, name="Other", lead_name="Other", status_id=str(uuid4()),
    )
    result = await crm_records._list(entity, _params(status_id=str(wanted.id)))

    assert result.total == 1


@pytest.mark.parametrize("slug", sorted(ENTITIES))
async def test_an_explicit_null_source_is_422_not_a_driver_error(
    db: FakeCrmDB, slug: str,
) -> None:
    """``source`` is NOT NULL DEFAULT 'manual'. Omitting it asks for the
    default; sending ``null`` asks Postgres to violate the constraint — and
    ``clean_payload`` keeps an explicit null on purpose, because that is how a
    client clears a NULLABLE field. Without the guard the caller reads their
    own bad request as a server fault."""
    _seed_pipeline(db)
    entity = ENTITIES[slug]
    payload = entity.payload.model_validate(
        {"source": None, **{f: "Bosch" for f in entity.required}}
    )
    with pytest.raises(HTTPException) as raised:
        await crm_records.create_record(entity, payload, USER)
    assert raised.value.status_code == 422
    assert "source" in str(raised.value.detail)


async def test_an_explicit_null_status_colour_is_422(db: FakeCrmDB) -> None:
    """The same rule reached from the admin surface — ``color`` is NOT NULL
    DEFAULT 'gray'. One guard, on the shared write path, so both reach it."""
    with pytest.raises(HTTPException) as raised:
        await crm_admin.create_status(
            "deal",
            crm_admin.StatusIn.model_validate(
                {"name": "Proposal", "color": None},
            ),
            USER,
        )
    assert raised.value.status_code == 422
    assert "color" in str(raised.value.detail)


async def test_the_null_guard_reaches_the_patch_path_too(
    db: FakeCrmDB,
) -> None:
    seeded = db.seed(
        "crm_deal_statuses", name="Proposal", position=30, type="ongoing",
    )
    with pytest.raises(HTTPException) as raised:
        await crm_admin.patch_status(
            "deal", str(seeded.id),
            crm_admin.StatusIn.model_validate({"color": None}), USER,
        )
    assert raised.value.status_code == 422


async def test_a_nullable_column_can_still_be_cleared(db: FakeCrmDB) -> None:
    """The guard must not become a blanket refusal of ``null``: clearing
    ``next_step`` is the ordinary way a client empties a field, and collapsing
    'unset' and 'clear' is the bug ``clean_payload`` exists to avoid."""
    _, deal_status = _seed_pipeline(db)
    seeded = db.seed(
        DEALS.table, name="Bosch printer", status_id=deal_status.id,
        next_step="call Anitha",
    )
    patched = await crm_records.patch_record(
        DEALS, str(seeded.id),
        crm_core.DealIn.model_validate({"next_step": None}), USER,
    )
    assert patched["next_step"] is None


async def test_a_lead_status_rejects_a_null_probability_too(
    db: FakeCrmDB,
) -> None:
    """``crm_lead_statuses`` has no probability column at all, so an explicit
    null would reach an INSERT naming a column that does not exist. The rule
    is keyed on the field being PRESENT, not on it being non-null."""
    with pytest.raises(HTTPException) as raised:
        await crm_admin.create_status(
            "lead",
            crm_admin.StatusIn.model_validate(
                {"name": "Nurture", "probability": None},
            ),
            USER,
        )
    assert raised.value.status_code == 422
    assert "deal-only" in str(raised.value.detail)


def test_the_null_guard_sits_on_the_shared_write_path() -> None:
    """Structural, because a behavioural case only ever proves the two routes
    it exercised. The claim is that a route added tomorrow inherits the guard,
    and that is a property of insert_row/update_row, not of any test."""
    from pathlib import Path

    source = Path(crm_core.__file__).read_text(encoding="utf-8")
    for function in ("async def insert_row(", "async def update_row("):
        body = source.split(function, 1)[1].split("\nasync def ", 1)[0]
        assert "reject_null_on_defaulted(table, values)" in body, (
            f"{function.strip()} no longer rejects an explicit null on a "
            "defaulted NOT NULL column — every write in the package goes "
            "through this pair, which is why the guard lives here."
        )


async def test_a_hand_edited_lead_name_survives_a_name_field_change(
    db: FakeCrmDB,
) -> None:
    """The residual: WS-26a re-derived on ANY name-input change, so correcting
    a lead's email address silently renamed the lead."""
    lead_status, _ = _seed_pipeline(db)
    seeded = db.seed(
        LEADS.table, lead_name="Bosch — printer RFQ", first_name="Anitha",
        email="anitha@bosch.in", status_id=lead_status.id,
    )
    patched = await crm_records.patch_record(
        LEADS, str(seeded.id),
        crm_core.LeadIn(email="anitha.kumar@bosch.in"), USER,
    )
    assert patched["lead_name"] == "Bosch — printer RFQ"
    assert patched["email"] == "anitha.kumar@bosch.in"


async def test_a_derived_lead_name_still_follows_its_inputs(
    db: FakeCrmDB,
) -> None:
    """The other half — a name nobody typed belongs to the chain that made it,
    so renaming the company renames the lead."""
    lead_status, _ = _seed_pipeline(db)
    seeded = db.seed(
        LEADS.table, lead_name="Bosch India", organization_name="Bosch India",
        status_id=lead_status.id,
    )
    patched = await crm_records.patch_record(
        LEADS, str(seeded.id),
        crm_core.LeadIn(organization_name="Bosch Rexroth"), USER,
    )
    assert patched["lead_name"] == "Bosch Rexroth"


async def test_an_explicit_lead_name_always_wins(db: FakeCrmDB) -> None:
    lead_status, _ = _seed_pipeline(db)
    seeded = db.seed(
        LEADS.table, lead_name="Anitha", first_name="Anitha",
        status_id=lead_status.id,
    )
    patched = await crm_records.patch_record(
        LEADS, str(seeded.id),
        crm_core.LeadIn(lead_name="Bosch — printer RFQ"), USER,
    )
    assert patched["lead_name"] == "Bosch — printer RFQ"


# ── WS-26c · organization_name on deal payloads (done-when 2) ───────────────

async def test_a_listed_deal_carries_its_organization_name(
    db: FakeCrmDB,
) -> None:
    """A kanban card prints the account name. Serving it from the join is the
    difference between a board that renders and a browser that client-side
    joins a ≤100-row page of organizations against it."""
    _, deal_status = _seed_pipeline(db)
    org = db.seed(ORGANIZATIONS.table, name="Bosch India")
    db.seed(
        DEALS.table, name="Bosch printer", status_id=deal_status.id,
        organization_id=org.id,
    )
    result = await crm_records._list(DEALS, _params())

    assert result.rows[0]["organization_name"] == "Bosch India"


async def test_a_deal_with_no_organization_still_lists(db: FakeCrmDB) -> None:
    """LEFT, not INNER: an individual buyer has no company and must not drop
    out of the pipeline because of it."""
    _, deal_status = _seed_pipeline(db)
    db.seed(DEALS.table, name="Walk-in filament", status_id=deal_status.id)
    result = await crm_records._list(DEALS, _params())

    assert result.total == 1
    assert result.rows[0]["organization_name"] is None


async def test_the_join_wraps_the_page_rather_than_the_table(
    db: FakeCrmDB,
) -> None:
    """Structural: filter, order and limit stay INSIDE the derived table the
    join wraps. Inlining the join would put crm_companies' own
    ``owner_email``, ``source`` and ``name`` in scope for every unqualified
    predicate list_contract renders — in all four entities, to serve one."""
    _, deal_status = _seed_pipeline(db)
    db.seed(DEALS.table, name="Bosch printer", status_id=deal_status.id)
    await crm_records._list(DEALS, _params(owner="VJ@Fracktal.in"))

    joined = db.statements_touching("LEFT JOIN crm_companies")
    assert joined, "the deal list lost its organization-name projection"
    inner = joined[0].split(") base")[0]
    assert "lower(owner_email) = :owner" in inner
    assert "LIMIT :limit OFFSET :offset" in inner
    # A join over an ordered subquery does not preserve its order.
    assert joined[0].rstrip().endswith("base.id DESC")


def test_only_deals_declare_a_projection() -> None:
    """The other three statements stay byte-identical to the un-joined form —
    a projection nobody asked for is a join on every list in the app."""
    for slug in ("leads", "contacts", "organizations"):
        assert not ENTITIES[slug].joined_columns, f"{slug} grew a join"
    assert DEALS.joined_columns == (("organization_name", "org.name"),)


# ── WS-26c · deal contacts (done-when 2) ────────────────────────────────────
#
# `crm_deal_contacts` shipped in 26a with exactly one writer (convert) and no
# reader at all, so "at most one primary per deal" held only because there was
# never a second row.

def _deal_with_contacts(db: FakeCrmDB) -> tuple:
    _, deal_status = _seed_pipeline(db)
    deal = db.seed(DEALS.table, name="Bosch printer", status_id=deal_status.id)
    anitha = db.seed(CONTACTS.table, first_name="Anitha", last_name="Kumar")
    priya = db.seed(CONTACTS.table, first_name="Priya", last_name="Nair")
    return deal, anitha, priya


async def _link(db: FakeCrmDB, deal: object, contact: object, **over) -> object:
    return await crm_deal_contacts.add_deal_contact(
        str(deal.id),
        crm_deal_contacts.DealContactIn(contact_id=str(contact.id), **over),
        USER,
    )


async def test_a_contact_can_be_added_to_a_deal(db: FakeCrmDB) -> None:
    deal, anitha, _ = _deal_with_contacts(db)
    added = await _link(db, deal, anitha, role="Procurement", is_primary=True)

    assert added.contact["first_name"] == "Anitha"
    assert added.role == "Procurement"
    assert added.is_primary is True


async def test_the_deal_contacts_read_returns_whole_contacts(
    db: FakeCrmDB,
) -> None:
    """Ids would make the record sheet issue one request per person just to
    print a name and a phone number."""
    deal, anitha, priya = _deal_with_contacts(db)
    await _link(db, deal, anitha)
    await _link(db, deal, priya, is_primary=True)

    listed = await crm_deal_contacts.list_deal_contacts(str(deal.id), USER)

    assert [r.contact["first_name"] for r in listed.rows] == ["Priya", "Anitha"]
    assert listed.rows[0].is_primary is True
    assert listed.rows[0].contact["last_name"] == "Nair"


async def test_promoting_a_primary_demotes_the_incumbent(
    db: FakeCrmDB,
) -> None:
    """THE rule §3.5 delegates to code — and the one 26a had by convention
    only. The migration deliberately carries no partial unique index (the
    WS-26b importer sets primaries in a pass that would fight one), so this
    path IS the enforcement."""
    deal, anitha, priya = _deal_with_contacts(db)
    await _link(db, deal, anitha, is_primary=True)
    await _link(db, deal, priya, is_primary=True)

    primaries = [r for r in db.rows("crm_deal_contacts") if r.get("is_primary")]
    assert len(primaries) == 1
    assert str(primaries[0]["contact_id"]) == str(priya.id)


async def test_the_demotion_precedes_the_promotion(db: FakeCrmDB) -> None:
    """Order matters: demote-then-promote passes through "no primary";
    the other way round passes through "two", and a concurrent reader sees a
    deal with two primary contacts."""
    deal, anitha, _ = _deal_with_contacts(db)
    await _link(db, deal, anitha, is_primary=True)

    writes = [
        statement for statement, _ in db.calls
        if "crm_deal_contacts" in statement
        and statement.split()[0] in ("UPDATE", "INSERT")
    ]
    assert "SET is_primary = false" in writes[0]


async def test_re_adding_a_linked_contact_updates_the_link(
    db: FakeCrmDB,
) -> None:
    """The PK is (deal_id, contact_id): "make Priya the primary" is the same
    request whether or not she is already on the deal, so this is an update
    rather than a 409 the UI has to special-case."""
    deal, anitha, _ = _deal_with_contacts(db)
    await _link(db, deal, anitha, role="Procurement")
    await _link(db, deal, anitha, role="Procurement", is_primary=True)

    links = db.rows("crm_deal_contacts")
    assert len(links) == 1
    assert links[0]["is_primary"] is True
    assert links[0]["role"] == "Procurement"


async def test_a_role_only_update_leaves_the_primary_standing(
    db: FakeCrmDB,
) -> None:
    """``is_primary`` is tri-state: an ABSENT field is "leave it alone".

    It defaulted to ``False``, so "set Anitha's role to Procurement" silently
    demoted the deal's primary — a field the caller never mentioned deciding
    something. ``role`` was tri-state from the start; both are now.
    """
    deal, anitha, _ = _deal_with_contacts(db)
    await _link(db, deal, anitha, is_primary=True)
    await _link(db, deal, anitha, role="Procurement")

    link = db.rows("crm_deal_contacts")[0]
    assert link["is_primary"] is True
    assert link["role"] == "Procurement"


async def test_a_role_only_update_does_not_demote_somebody_else_either(
    db: FakeCrmDB,
) -> None:
    """The same rule read from the other side of the deal: touching contact B
    must not disturb contact A's flag."""
    deal, anitha, priya = _deal_with_contacts(db)
    await _link(db, deal, anitha, is_primary=True)
    await _link(db, deal, priya, role="Finance")

    primaries = [r for r in db.rows("crm_deal_contacts") if r.get("is_primary")]
    assert [str(r["contact_id"]) for r in primaries] == [str(anitha.id)]


async def test_an_explicit_false_still_demotes(db: FakeCrmDB) -> None:
    """Tri-state means unstated is a no-op, not that demotion is unreachable:
    a deal can legitimately end up with no primary."""
    deal, anitha, _ = _deal_with_contacts(db)
    await _link(db, deal, anitha, is_primary=True)
    await _link(db, deal, anitha, is_primary=False)

    assert db.rows("crm_deal_contacts")[0]["is_primary"] is False


async def test_a_new_link_defaults_to_not_primary(db: FakeCrmDB) -> None:
    """An unstated flag on a NEW row takes the column's own default rather
    than being sent as null, which the NOT NULL guard refuses."""
    deal, anitha, _ = _deal_with_contacts(db)
    added = await _link(db, deal, anitha, role="Procurement")

    assert added.is_primary is False
    insert = db.statements_touching("INSERT INTO crm_deal_contacts")[0]
    assert "is_primary" not in insert


async def test_removing_a_contact_says_whether_it_was_the_primary(
    db: FakeCrmDB,
) -> None:
    """A deal whose primary was just removed has none; saying so lets the
    sheet prompt for a new one instead of showing a deal nobody owns."""
    deal, anitha, _ = _deal_with_contacts(db)
    await _link(db, deal, anitha, is_primary=True)

    removed = await crm_deal_contacts.remove_deal_contact(
        str(deal.id), str(anitha.id), USER,
    )
    assert removed["was_primary"] is True
    assert db.rows("crm_deal_contacts") == []


async def test_removing_a_contact_that_is_not_on_the_deal_is_404(
    db: FakeCrmDB,
) -> None:
    """A cheerful 200 makes the UI re-render the person it just removed."""
    deal, anitha, _ = _deal_with_contacts(db)
    with pytest.raises(HTTPException) as raised:
        await crm_deal_contacts.remove_deal_contact(
            str(deal.id), str(anitha.id), USER,
        )
    assert raised.value.status_code == 404


async def test_the_deal_contact_routes_404_on_an_unknown_deal(
    db: FakeCrmDB,
) -> None:
    _deal_with_contacts(db)
    with pytest.raises(HTTPException) as raised:
        await crm_deal_contacts.list_deal_contacts(str(uuid4()), USER)
    assert raised.value.status_code == 404
    with pytest.raises(HTTPException) as raised:
        await crm_deal_contacts.remove_deal_contact(
            str(uuid4()), str(uuid4()), USER,
        )
    assert raised.value.status_code == 404


async def test_adding_an_unknown_contact_is_404(db: FakeCrmDB) -> None:
    deal, _, _ = _deal_with_contacts(db)
    with pytest.raises(HTTPException) as raised:
        await crm_deal_contacts.add_deal_contact(
            str(deal.id),
            crm_deal_contacts.DealContactIn(contact_id=str(uuid4())),
            USER,
        )
    assert raised.value.status_code == 404
    assert "Contact" in str(raised.value.detail)


#: The one module allowed to write ``crm_deal_contacts`` outside ``core.py``,
#: and the reason it is not a hole. WS-26b's importer COMPUTES ``is_primary``
#: inside its statement (``NOT EXISTS (… AND is_primary)``) under
#: ``ON CONFLICT DO NOTHING``, so a re-import can neither create a second
#: primary nor demote one the team set by hand. ``core.link_deal_contact`` is
#: the opposite seam by design — it PROMOTES, demoting the incumbent first —
#: so routing the backfill through it would hand the primary back to Zoho on
#: every cycle. Two writers, one invariant, and each direction is deliberate.
_DEAL_CONTACT_WRITERS_OUTSIDE_CORE: frozenset[str] = frozenset({"import_zoho.py"})


def test_the_primary_rule_has_exactly_one_implementation() -> None:
    """Structural, and the point of the whole addendum: a second writer of
    ``is_primary`` is a second opinion about how many primaries a deal has.

    Both write forms are hunted — the helper call and raw ``INSERT INTO`` —
    because a module that reaches the table with its own statement escapes a
    check written against the helper alone.
    """
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    assert "async def link_deal_contact(" in (
        package / "core.py"
    ).read_text(encoding="utf-8")

    offenders = []
    for path in sorted(package.glob("*.py")):
        if path.name == "core.py" or path.name in _DEAL_CONTACT_WRITERS_OUTSIDE_CORE:
            continue
        source = path.read_text(encoding="utf-8")
        if (
            'insert_row(db, "crm_deal_contacts"' in source
            or "INSERT INTO crm_deal_contacts" in source
        ):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} write crm_deal_contacts directly — every writer must go "
        "through core.link_deal_contact so there is one primary rule."
    )


def test_the_one_excepted_writer_still_cannot_demote_a_primary() -> None:
    """The exception above is only sound while the importer's ``is_primary``
    stays computed. A literal ``true`` there is how a nightly backfill takes
    the primary back off whoever the team put it on (WS-26b, §7.1)."""
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    for name in sorted(_DEAL_CONTACT_WRITERS_OUTSIDE_CORE):
        source = (package / name).read_text(encoding="utf-8")
        statement = source.split("INSERT INTO crm_deal_contacts", 1)[1][:600]
        assert "NOT EXISTS" in statement, name
        assert "ON CONFLICT DO NOTHING" in statement, name
