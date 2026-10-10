"""WS-26i-export — the CRM filtered-list CSV export.

Spec: ``project-docs/specs/crm_app.md`` §9 (WS-26i-export) and §10.

⚠️ **Never run this file alone.** Every CRM route test shares
``tests/unit/_crm_fakes.py``; a fake that drifts shows up as another suite
failing, not as this one passing. §10 names the eight-file block.

The claims worth pinning are the ones where the wrong implementation still
produces a file somebody opens and believes:

* **the export is the caller's FILTER**, built by the same ``core.list_contract``
  the list endpoint uses. A second filter parser drifts, and then the file and
  the screen disagree about what "Priya's open leads" means.
* **it is never truncated.** ``list_contract`` clamps ``page_size`` to
  ``MAX_PAGE_SIZE = 100``; reused verbatim it would export the first 100 rows of
  a 1,516-row lead list and say nothing. A short page announces itself, a short
  CSV does not — so the cap is a REFUSAL naming the real count.
* **the columns are the list's own vocabulary**, read out of
  ``lib/columns.ts`` rather than mirrored, the mechanism
  ``test_crm_stage_discipline_parity.py`` already uses for ``board.ts``.
* **it writes NOTHING.** The Zoho sync loop is running (§ the file header's
  2026-08-11 correction): a row this endpoint dirtied would be pushed to the
  live tenant within one 600s cycle. That is asserted structurally *and*
  behaviourally, never assumed.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.csv_export import BOM
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import export as crm_export
from gateway.routes.crm import pipeline as crm_pipeline
from gateway.routes.crm import records as crm_records
from gateway.routes.crm.core import ENTITIES, MAX_PAGE_SIZE
from gateway.routes.crm.export import COLUMNS, MAX_EXPORT_ROWS

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

USER = crm_user()

_ROOT = Path(__file__).resolve().parents[2]
SOURCE = _ROOT / "apps/services/gateway/gateway/routes/crm/export.py"
COLUMNS_TS = (
    _ROOT / "workbench/control_plane/src/app/crm/lib/columns.ts"
)

MODULES = (crm_core, crm_records, crm_activities, crm_admin, crm_pipeline,
           crm_export)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


def _params(**over) -> crm_export.ExportParams:
    """An ExportParams built without FastAPI, with the same defaults."""
    base = {
        "q": None, "sort": None, "direction": "desc", "status_id": None,
        "owner": None, "source": None, "include_converted": False,
    }
    return crm_export.ExportParams(**{**base, **over})


async def _export(slug: str, **over) -> tuple[str, dict[str, str]]:
    """Run one entity's endpoint → ``(csv_text_without_BOM, headers)``."""
    handler = crm_export.HANDLERS[slug]
    response = await handler(params=_params(**over), user=USER)
    body = response.body.decode("utf-8")
    assert body.startswith(BOM), "the UTF-8 BOM is what makes Excel read it"
    return body[len(BOM):], dict(response.headers)


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


# ── The column vocabulary, read out of the TypeScript ───────────────────────
#
# The same mechanism `test_crm_stage_discipline_parity.py` uses for `board.ts`
# and `test_projects_migration.py` for `CATEGORY_HUES`: the client's list is
# READ, never mirrored, because a mirror goes stale and then lies.

#: ``  deals: [ … ],`` — one entity's column list inside `COLUMNS`.
_SECTION_RE = re.compile(r"^  (\w+): \[\n(.*?)^  \],", re.M | re.S)
#: One column object literal. `[^{}]` spans newlines, so the formatting of
#: `columns.ts` is free to change without moving this fence.
_ENTRY_RE = re.compile(r"\{([^{}]*)\}")


def _typescript_columns() -> dict[str, list[tuple[str, str]]]:
    """``columns.ts``'s COLUMNS as ``{slug: [(key, csv_header), …]}``.

    The CSV header is ``exportLabel`` when the column declares one and ``label``
    otherwise. The one column that needs the fallback is the leads list's
    converted badge, whose ``<th>`` is deliberately blank — a blank header in a
    spreadsheet is a column nobody can name.
    """
    source = COLUMNS_TS.read_text(encoding="utf-8")
    body = source[source.index("export const COLUMNS"):]
    out: dict[str, list[tuple[str, str]]] = {}
    for slug, section in _SECTION_RE.findall(body):
        columns: list[tuple[str, str]] = []
        for entry in _ENTRY_RE.findall(section):
            key = re.search(r'\bkey:\s*"([^"]*)"', entry)
            label = re.search(r'\blabel:\s*"([^"]*)"', entry)
            export_label = re.search(r'\bexportLabel:\s*"([^"]*)"', entry)
            assert key is not None and label is not None, (
                f"columns.ts has a column this fence cannot read: {entry!r}"
            )
            columns.append(
                (key.group(1), (export_label or label).group(1)),
            )
        out[slug] = columns
    return out


def test_the_columns_are_the_lists_own_vocabulary_in_declaration_order() -> None:
    """⚠️ R7 fence, done-when 4. The file's columns must be the ones the list
    draws, in the order it draws them — so this READS ``lib/columns.ts`` rather
    than restating it. Order counts as much as membership: a spreadsheet whose
    columns are shuffled relative to the screen is a second opinion about the
    same rows."""
    assert _typescript_columns() == {
        slug: list(cols) for slug, cols in COLUMNS.items()
    }, (
        "routes/crm/export.COLUMNS and app/crm/lib/columns.ts have drifted — "
        "the CSV would no longer be the list you were looking at"
    )


def test_every_entity_has_a_column_vocabulary() -> None:
    assert set(COLUMNS) == set(ENTITIES)


def test_no_exported_column_has_a_blank_header() -> None:
    """A blank header is a column nobody can name in a formula or a pivot —
    which is why the leads list's badge column carries an ``exportLabel``."""
    for slug, columns in COLUMNS.items():
        for key, header in columns:
            assert header.strip(), f"{slug}.{key} exports under a blank header"


def test_every_exported_column_is_a_real_field_of_the_entitys_model() -> None:
    """A key that is not a model field reads as ``None`` on every row — a
    column of empties that looks like missing data rather than a broken map."""
    for slug, columns in COLUMNS.items():
        fields = ENTITIES[slug].model.model_fields
        unknown = [key for key, _ in columns if key not in fields]
        assert unknown == [], f"{slug} exports non-fields {unknown}"


# ── Wiring and the parameter surface ────────────────────────────────────────

@pytest.mark.parametrize("slug", sorted(ENTITIES))
def test_the_export_is_mounted_for_every_entity(slug: str) -> None:
    from gateway.routes.crm import router

    assert f"/crm/export/{slug}.csv" in {r.path for r in router.routes}


@pytest.mark.parametrize("slug", sorted(ENTITIES))
def test_the_path_cannot_be_shadowed_by_the_single_record_route(slug: str) -> None:
    """⚠️ ``/crm/leads/export.csv`` is the obvious spelling and it would be
    answered by ``/crm/leads/{record_id}`` or not, depending on registration
    order — the trap ``records.py``'s docstring says this package does not
    have. A literal ``export/`` segment in front keeps that true."""
    from gateway.routes.crm import router

    paths = {r.path for r in router.routes}
    assert f"/crm/{slug}/export.csv" not in paths
    assert f"/crm/{slug}/{{record_id}}" in paths


#: List parameters the export deliberately does NOT take, and why.
NOT_ON_THE_EXPORT = {
    # A page is a screen's unit. Half a filter's records in a file IS the
    # silent truncation this endpoint refuses; the cap is a refusal instead.
    "page", "page_size",
}


def _query_params(dependant) -> set[str]:
    """Every query parameter a route accepts, **including sub-dependencies**.

    ⚠️ ``dependant.query_params`` is the SHALLOW list — parameters declared on
    the handler itself. Both the CRM list and the export take their filters via
    a class ``Depends()`` (``records.ListParams`` / ``export.ExportParams``), so
    that shallow set is **empty on both sides for all four entities** and any
    comparison of it is ``set() - set() == set()``: a fence that passes whatever
    happens. That is how it shipped, and adding a ``city`` filter to
    ``ListParams`` — the exact drift this is here to catch — left every test in
    this file green. Recursing into ``dependant.dependencies`` is what makes the
    comparison see the parameters that actually exist.
    """
    found = {p.name for p in dependant.query_params}
    for sub in dependant.dependencies:
        found |= _query_params(sub)
    return found


@pytest.mark.parametrize("slug", sorted(ENTITIES))
def test_every_list_filter_reaches_the_export_or_is_named_as_excluded(
    slug: str,
) -> None:
    """⚠️ FastAPI ignores an unknown query parameter, so a filter the list
    sends and the export does not declare is not an error — it is a filter that
    stops applying the moment you press Export, and the file looks fine."""
    from gateway.routes.crm import router

    def params(path: str) -> set[str]:
        route = next(r for r in router.routes if r.path == path)
        return _query_params(route.dependant)

    listed = params(f"/crm/{slug}")
    # The fence's own precondition: if the flattening ever stops finding the
    # class dependency's parameters, this comparison silently becomes vacuous
    # again — so assert it found them before trusting the difference.
    assert {"q", "sort", "owner"} <= listed, (
        f"the {slug} list route exposes {sorted(listed)} — the parameter "
        f"flattening is not seeing ListParams, so the comparison below would "
        f"pass no matter what drifted"
    )

    exported = params(f"/crm/export/{slug}.csv")
    missing = listed - exported
    assert missing - NOT_ON_THE_EXPORT == set(), (
        f"the {slug} list accepts {sorted(missing - NOT_ON_THE_EXPORT)} and "
        f"the export does not — FastAPI will drop them silently"
    )
    # And the other direction: pagination is EXCLUDED, not merely absent by
    # accident. An export that grew a `page` would be the partial file
    # done-when 3 refuses, arriving as a feature.
    assert exported & NOT_ON_THE_EXPORT == set(), (
        f"the {slug} export accepts {sorted(exported & NOT_ON_THE_EXPORT)} — "
        f"a page is a screen's unit and half a filter's records in a file is "
        f"the silent truncation this endpoint refuses"
    )


def test_the_filters_come_from_the_one_shared_builder() -> None:
    """⚠️ Done-when 1. A second filter parser drifts, and the export and the
    screen then disagree about what the caller was looking at.

    Two anchors, the shape ``test_projects_export.py`` uses: naming the builder
    is not the same as handing it the caller's values, and a source grep for
    ``list_contract(`` alone would pass on a call that reparsed ``params`` into
    its own vocabulary first.
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "list_contract(" in source
    for field in ("q", "sort", "direction", "status_id", "owner", "source"):
        assert f"{field}=params.{field}" in source, (
            f"`{field}` is not handed to `list_contract` off the caller's "
            f"params — the export and the screen would disagree about it"
        )
    assert "extra_where=extra" in source


def test_there_is_no_partial_file_path_at_all() -> None:
    """⚠️ The structural half of the never-truncated rule: ``OFFSET`` would page
    the export, and the row query's LIMIT is ``cap + 1`` purely so the render
    can refuse — no branch renders fewer rows than matched."""
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("async def _export"):]
    assert "OFFSET" not in body
    assert "query.limit" not in body, (
        "`ListQuery.limit` is clamped to MAX_PAGE_SIZE — binding it here is "
        "exactly the silent 100-row truncation this endpoint refuses"
    )
    assert "MAX_EXPORT_ROWS + 1" in body, (
        "the row query must fetch one past the cap: under READ COMMITTED the "
        "count and the render take different snapshots, so a `LIMIT :cap` of "
        "exactly the cap can return exactly the cap and ship a partial file"
    )
    assert "len(rows) > MAX_EXPORT_ROWS" in body, (
        "fetching cap + 1 without checking it renders the extra row instead of "
        "refusing — the probe is only worth anything with the refusal attached"
    )


def test_the_page_clamp_is_never_the_export_limit() -> None:
    """⚠️ Trap 1, stated as a fence. ``MAX_EXPORT_ROWS`` and ``MAX_PAGE_SIZE``
    are different numbers for different jobs; an export capped at the page size
    is the bug arriving through the door marked "reuse the shared builder"."""
    assert MAX_EXPORT_ROWS > MAX_PAGE_SIZE
    # 3,993 records were measured across the four entities at the backfill.
    assert MAX_EXPORT_ROWS >= 4000


def test_the_session_comes_from_the_package_seam() -> None:
    """R5/H2: no ``get_db()``, no engine, no second idiom."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "_tenant_session()" in source
    assert not re.search(r"await _?get_db\(\)", source)
    assert "create_async_engine" not in source


# ── Read-only, asserted rather than assumed (done-when 7) ───────────────────

#: Names that make a CRM row dirty, tombstoned or written. The sync loop is
#: RUNNING: a row this endpoint dirtied reaches the live Zoho tenant inside one
#: 600s cycle, which is why this is a structural fence and not a review note.
WRITE_SEAMS = (
    "insert_row", "update_row", "mark_dirty_on_insert", "mark_dirty_on_update",
    "record_zoho_tombstone", "record_activity", "apply_status_transition",
    "coerce_write_values", "savepoint",
)


def test_the_export_imports_no_writer() -> None:
    """⚠️ Done-when 7, structurally. The module cannot dirty a row it has no
    way to write — so the fence is on the IMPORTS, which a future edit has to
    change before it can change the behaviour."""
    import ast

    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not imported & set(WRITE_SEAMS), (
        f"the export imports a writer: {sorted(imported & set(WRITE_SEAMS))}"
    )


def test_the_export_source_contains_no_write_verb() -> None:
    """The other half: a hand-written ``text("UPDATE …")`` needs no import."""
    source = SOURCE.read_text(encoding="utf-8").upper()
    body = source[source.index("ASYNC DEF _EXPORT"):]
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "ZOHO_DIRTY", "TOMBSTONE"):
        assert verb not in body, f"the export body names {verb.strip()}"


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", sorted(ENTITIES))
async def test_an_export_issues_only_reads(db, slug: str) -> None:
    """⚠️ Done-when 7, behaviourally, over every entity. Every statement the
    endpoint issued is a SELECT — the assertion a future edit that reached for
    a write would fail, whatever it imported."""
    db.seed(ENTITIES[slug].table, name="Acme", first_name="A", lead_name="L")

    await _export(slug)

    assert db.statements, "the export issued no statements at all"
    assert [s for s in db.statements if not s.upper().startswith("SELECT")] == []
    assert db.rows("crm_zoho_tombstones") == []


@pytest.mark.asyncio
async def test_an_export_leaves_no_row_dirty(db) -> None:
    """The property that actually costs money: `zoho_dirty` is what the running
    sync loop pushes upstream."""
    db.seed("crm_leads", lead_name="Asha", email="asha@x.com")

    await _export("leads")

    assert [r["zoho_dirty"] for r in db.rows("crm_leads")] == [False]


# ── The filter is the caller's ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_export_carries_every_matching_row(db) -> None:
    db.seed("crm_companies", name="Acme", industry="Manufacturing",
            phone="123", owner_email="priya@fracktal.in")
    db.seed("crm_companies", name="Globex", industry="Retail",
            phone="456", owner_email="asha@fracktal.in")

    text, headers = await _export("organizations", sort="name", direction="asc")

    assert _rows(text) == [
        ["Organization", "Industry", "Phone", "Owner"],
        ["Acme", "Manufacturing", "123", "priya@fracktal.in"],
        ["Globex", "Retail", "456", "asha@fracktal.in"],
    ]
    assert headers["content-type"].startswith("text/csv")
    assert headers["content-disposition"] == (
        'attachment; filename="crm-organizations.csv"'
    )
    assert headers["x-export-rows"] == "2"


@pytest.mark.asyncio
async def test_the_owner_filter_reaches_the_export(db) -> None:
    """⚠️ The acceptance criterion. An export that silently means "every record
    in the CRM" is the failure this ticket was written against."""
    db.seed("crm_companies", name="Mine", owner_email="Priya@Fracktal.in")
    db.seed("crm_companies", name="Theirs", owner_email="asha@fracktal.in")

    text, _ = await _export("organizations", owner="priya@fracktal.in")

    assert [row[0] for row in _rows(text)[1:]] == ["Mine"]


@pytest.mark.asyncio
async def test_a_text_filter_reaches_the_export_too(db) -> None:
    db.seed("crm_leads", lead_name="Asha Rao", email="asha@x.com")
    db.seed("crm_leads", lead_name="Bala Singh", email="bala@x.com")

    text, _ = await _export("leads", q="Asha")

    assert [row[0] for row in _rows(text)[1:]] == ["Asha Rao"]


@pytest.mark.asyncio
async def test_converted_leads_are_hidden_unless_asked_for(db) -> None:
    """§3.3/B6 — the list's default, and therefore the export's. Keyed on the
    FK, exactly as ``records._list`` keys it."""
    db.seed("crm_leads", lead_name="Working", converted_deal_id=None)
    db.seed("crm_leads", lead_name="Converted", converted_deal_id="d-1")

    text, _ = await _export("leads")
    assert [row[0] for row in _rows(text)[1:]] == ["Working"]

    text, _ = await _export("leads", include_converted=True)
    assert sorted(row[0] for row in _rows(text)[1:]) == ["Converted", "Working"]


@pytest.mark.asyncio
async def test_a_deal_carries_its_organization_name(db) -> None:
    """The joined display column ``core.project_joined`` projects. Without the
    join the Organization column would be empty on every row — a column of
    nothing that reads as missing data."""
    org = db.seed("crm_companies", name="Acme")
    db.seed("crm_deals", name="Big one", company_id=org.id, amount=100)

    text, _ = await _export("deals")

    assert _rows(text)[1][:2] == ["Big one", "Acme"]


@pytest.mark.asyncio
async def test_a_contacts_name_column_carries_both_halves(db) -> None:
    """The list draws "Asha Rao" under one header and the file must not carry
    less than the row: a "Name" column holding only the first name is data
    loss, not a formatting difference."""
    db.seed("crm_contacts", first_name="Asha", last_name="Rao",
            email="asha@x.com")

    text, _ = await _export("contacts")

    assert _rows(text)[1][0] == "Asha Rao"


# ── Refusals ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["contacts", "organizations"])
async def test_a_status_filter_on_an_unpipelined_entity_is_a_422(
    db, slug: str,
) -> None:
    """Done-when 2 — the existing refusal, not a silent ignore. Ignoring it
    would answer with the WHOLE table for a caller who asked for one lane."""
    with pytest.raises(HTTPException) as caught:
        await _export(slug, status_id="s-1")
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_sort_key_is_a_422_naming_the_allowlist(db) -> None:
    with pytest.raises(HTTPException) as caught:
        await _export("leads", sort="whenever")
    assert caught.value.status_code == 422
    assert "leads" in caught.value.detail
    assert "lead_name" in caught.value.detail


@pytest.mark.asyncio
async def test_an_unknown_direction_is_a_422(db) -> None:
    with pytest.raises(HTTPException) as caught:
        await _export("leads", direction="sideways")
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_the_cap_refuses_rather_than_handing_back_a_partial_file(
    db, monkeypatch,
) -> None:
    """⚠️ Done-when 3, the whole design decision in one assertion. A truncated
    CSV looks exactly like a complete one — nobody scrolls to the bottom of a
    spreadsheet to check whether it ended early — so the refusal names the real
    count and the cap, and no file is produced at all."""
    monkeypatch.setattr(crm_export, "MAX_EXPORT_ROWS", 2)
    for n in range(3):
        db.seed("crm_leads", lead_name=f"Lead {n}")

    with pytest.raises(HTTPException) as caught:
        await _export("leads")

    assert caught.value.status_code == 422
    assert "3 leads match" in caught.value.detail
    assert "2" in caught.value.detail


@pytest.mark.asyncio
async def test_exactly_the_cap_is_exported_in_full(db, monkeypatch) -> None:
    """The boundary itself, so an off-by-one shows up as a refusal of a set
    that fits."""
    monkeypatch.setattr(crm_export, "MAX_EXPORT_ROWS", 2)
    for n in range(2):
        db.seed("crm_leads", lead_name=f"Lead {n}")

    text, _ = await _export("leads")

    assert len(_rows(text)) == 3  # header + two


@pytest.mark.asyncio
async def test_a_race_past_the_cap_refuses_rather_than_shipping_exactly_the_cap(
    db, monkeypatch,
) -> None:
    """⚠️ Done-when 3 against a database that moves under the reader.

    ``_tenant_session()`` opens ONE transaction, but Postgres defaults to READ
    COMMITTED — the ``count(*)`` and the row query see different snapshots. So
    the count can answer "under the cap", a concurrent insert can land (the
    auto-lead path inserts leads on an ingest, unprompted), and a ``LIMIT`` of
    exactly the cap then returns exactly the cap: a partial file, a 200, and
    nobody told. The counted total is not the invariant; the rendered set is.

    This asserts the version that cannot be fooled — the row query fetches one
    past the cap and refuses on what came back. Measured red against the
    ``LIMIT :cap = MAX_EXPORT_ROWS`` version, which exported the two rows as a
    complete file.
    """
    monkeypatch.setattr(crm_export, "MAX_EXPORT_ROWS", 2)
    for n in range(2):
        db.seed("crm_leads", lead_name=f"Lead {n}")

    real_execute = db.execute
    raced: list[str] = []

    async def racing_execute(sql, params=None):
        result = await real_execute(sql, params)
        if "count(*)" in str(sql) and not raced:
            raced.append("committed by somebody else")
            db.seed("crm_leads", lead_name="Raced in")
        return result

    monkeypatch.setattr(db, "execute", racing_execute)

    with pytest.raises(HTTPException) as caught:
        await _export("leads")

    assert raced, "the race never fired — the count statement was not seen"
    assert caught.value.status_code == 422
    # "at least", because the endpoint deliberately did not count past the cap.
    assert "at least 3 leads match" in caught.value.detail


@pytest.mark.asyncio
async def test_a_filter_that_narrows_under_the_cap_exports(db, monkeypatch) -> None:
    """The refusal's own advice has to work: narrowing is what the 422 says."""
    monkeypatch.setattr(crm_export, "MAX_EXPORT_ROWS", 2)
    for n in range(3):
        db.seed("crm_leads", lead_name=f"Lead {n}", owner_email="a@x.com")
    db.seed("crm_leads", lead_name="Mine", owner_email="priya@fracktal.in")

    text, _ = await _export("leads", owner="priya@fracktal.in")

    assert [row[0] for row in _rows(text)[1:]] == ["Mine"]


@pytest.mark.asyncio
async def test_a_filter_wider_than_a_page_is_exported_whole(db) -> None:
    """⚠️ Trap 1, end to end and the reason this file exists. ``list_contract``
    clamps ``page_size`` to 100; an export that bound ``ListQuery.limit`` would
    hand back 100 of these 150 leads, with a 200 and no warning — which is
    exactly what the 1,516-row lead list would have done in production."""
    for n in range(MAX_PAGE_SIZE + 50):
        db.seed("crm_leads", lead_name=f"Lead {n:03d}")

    text, headers = await _export("leads")

    assert len(_rows(text)) == MAX_PAGE_SIZE + 51  # header + every lead
    assert headers["x-export-rows"] == str(MAX_PAGE_SIZE + 50)


# ── The bytes ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_hostile_name_arrives_intact_and_inert(db) -> None:
    """⚠️ `=cmd|'/c calc'!A0` executes on the machine of whoever double-clicks
    the file, with their credentials, from a file this company's own tool
    produced — and 3,993 of these rows came out of Zoho, typed by counterparties.
    The guard is `gateway.csv_export.csv_cell`, shared with Projects; this is
    the end-to-end proof it is actually applied here."""
    nasty = '=SUM(A1),"drop"\nline two'
    db.seed("crm_companies", name=nasty)

    text, _ = await _export("organizations")

    assert _rows(text)[1][0] == f"'{nasty}"


@pytest.mark.asyncio
async def test_an_empty_result_is_a_header_row_not_an_error(db) -> None:
    """An empty file with no header is indistinguishable from a failed
    download; the header row says "this filter matched nothing"."""
    text, headers = await _export("deals")

    assert _rows(text) == [["Deal", "Organization", "Amount", "Closing", "Owner"]]
    assert headers["x-export-rows"] == "0"


@pytest.mark.asyncio
async def test_the_line_terminator_is_rfc_4180(db) -> None:
    db.seed("crm_companies", name="Acme")

    text, _ = await _export("organizations")

    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")


@pytest.mark.asyncio
async def test_the_filename_is_the_slug_and_never_free_text(db) -> None:
    """Done-when 5. A filename built from a record name would be a
    header-injection surface: names here are free text out of Zoho."""
    db.seed("crm_companies", name='ev"il\r\nX-Injected: 1')

    _, headers = await _export("organizations")

    assert headers["content-disposition"] == (
        'attachment; filename="crm-organizations.csv"'
    )


@pytest.mark.asyncio
async def test_the_session_is_opened_once_and_committed(db) -> None:
    """H2: one bound session for the whole read, the ambient form every other
    request handler in this package uses."""
    db.seed("crm_leads", lead_name="Asha")

    await _export("leads")

    assert db.committed == 1
