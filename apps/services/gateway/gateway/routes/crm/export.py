"""CRM · the filtered-list CSV export (WS-26i-export).

Spec: ``project-docs/specs/crm_app.md`` §9 (WS-26i-export) and §10.

    GET /crm/export/{leads,deals,contacts,organizations}.csv?…the list's filters…

**It exports what you were looking at, or it exports nothing.** Synchronous, on
the sibling ``routes/projects/export.py`` (WS-27ae) exactly: one bounded query,
one ``text/csv`` response, no job row, no polling, no worker. Real need: ~4,000
records whose only export route today is the Zoho UI we are retiring.

Five rules, each because the obvious implementation is wrong:

1. **The filters are the caller's, built by the SAME contract the list endpoint
   uses** (:func:`core.list_contract`). A second filter parser would drift, and
   then the file and the screen would disagree about what "Priya's open leads"
   means. The refusals come with it: ``?status_id`` on contacts or
   organizations is a 422 rather than a silent whole-table answer, and an
   unknown sort key is a 422 naming the entity's allowlist.

2. ⚠️ **The page clamp is NOT the export limit.** ``list_contract`` clamps
   ``page_size`` to ``MAX_PAGE_SIZE = 100``. Binding ``ListQuery.limit`` here
   would export the first 100 rows of a 1,516-row lead list with a 200 and no
   warning — the exact silent truncation rule 3 forbids, arriving through the
   door marked "reuse the shared builder". The contract is reused for the WHERE
   clause and the ORDER BY; the LIMIT is :data:`MAX_EXPORT_ROWS`.
   Fence: ``test_crm_export.py::test_a_filter_wider_than_a_page_is_exported_whole``.

3. **It is never truncated. It is complete, or it is refused.** The cap is
   checked with a ``count(*)`` over the same WHERE *before* a single row is
   rendered, and a filter matching more than that answers **422 naming the real
   count and the cap**. A short page announces itself; a short CSV does not —
   nobody scrolls to the bottom of a spreadsheet to check whether it ended
   early.
   ⚠️ **The count alone is not sufficient, so the render re-checks.**
   ``_tenant_session()`` opens one transaction, but Postgres defaults to READ
   COMMITTED: the ``count(*)`` and the row query take different snapshots.
   Count 9,998 → no refusal → a concurrent auto-lead insert adds five matching
   rows → a ``LIMIT`` of exactly :data:`MAX_EXPORT_ROWS` returns exactly
   :data:`MAX_EXPORT_ROWS` → a partial file with a 200 and nobody told. So the
   row query binds ``cap + 1`` and refuses when it comes back with more than the
   cap: the complete-or-refused invariant becomes a property of the bytes about
   to be written rather than of a count taken a moment earlier, and the endpoint
   no longer has to trust that the two agree. The extra row is evidence for a
   refusal and is never rendered — this is not the calendar's
   truncate-and-say-so probe.
   Fence: ``test_crm_export.py::test_a_race_past_the_cap_refuses_rather_than_shipping_exactly_the_cap``.

4. **The columns are the list's own vocabulary** (:data:`COLUMNS`), in the order
   the table draws them. Hand-kept here and READ from
   ``app/crm/lib/columns.ts`` by ``test_crm_export.py``, the mechanism
   ``test_crm_stage_discipline_parity.py`` already uses for ``board.ts``: a
   mirror with no fence goes stale and then lies.

5. **This endpoint writes NOTHING.** Not a nicety — the Zoho sync loop is
   RUNNING (spec header, corrected 2026-08-11) and ``core.mark_dirty_on_*``
   stamps every native write to the four tracked tables, so a row dirtied here
   would be pushed to the live Zoho tenant within one 600s cycle. It therefore
   imports no writer at all, and ``test_crm_export.py`` asserts both the absent
   imports and that every statement an export issues is a ``SELECT``.

**The path is ``/crm/export/leads.csv``, not ``/crm/leads/export.csv``.** The
obvious spelling would be *shadowed*: ``/crm/leads/{record_id}`` is a template,
so which handler answered would depend on router registration order — the trap
``records.py``'s own docstring says this package does not have. A literal
segment of its own keeps that true, and it is why the four paths are written out
one per entity rather than served by a ``/crm/export/{entity}.csv`` template.

**Tenancy (R5/R11).** A read of tenant data through ``_tenant_session()`` like
every other request handler in this package — no new DB-connection site, no
``get_db()``. The tenant comes from the ambient binding the request already
carries and never from request input. CRM data is org-visible to
``feature:crm`` holders (D-CRM-3), so there is deliberately no owner predicate
here either — the same decision ``core``'s docstring records for the list.

**CSV injection is neutralised, not documented away** — by the shared
``gateway.csv_export`` seam, not a second copy of it.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query, Response
from gateway.csv_export import csv_document
from gateway.routes.crm.core import (
    CONTACTS,
    DEALS,
    LEADS,
    ORGANIZATIONS,
    Entity,
    _tenant_session,
    list_contract,
    project_joined,
    qualify,
    router,
    row_to_dict,
)
from sqlalchemy import text

#: The most rows one export may contain.
#:
#: Reached, the request is **refused** (rule 3 above): this endpoint never hands
#: back a partial file. Ten thousand is "the whole CRM, twice over" — the
#: measured backfill is 3,993 records across all four entities and the largest
#: single list is 1,516 leads — so the cap can only ever refuse a request that
#: is already not what anybody wanted, while never refusing a real one. It is
#: emphatically NOT ``MAX_PAGE_SIZE``; see rule 2.
MAX_EXPORT_ROWS = 10_000

#: Entity slug → the file's columns as ``(model field, header)``, in the order
#: the list draws them.
#:
#: The twin of ``app/crm/lib/columns.ts::COLUMNS``, which is what the screen
#: renders. Held together by ``test_crm_export.py``, which parses the
#: TypeScript rather than restating it — order included, because a spreadsheet
#: whose columns are shuffled relative to the screen is a second opinion about
#: the same rows.
#:
#: The leads list's converted column is a badge with a blank ``<th>``; it
#: exports under the ``exportLabel`` that side declares, because a blank header
#: is a column nobody can name in a formula.
COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "deals": (
        ("name", "Deal"),
        ("organization_name", "Company"),
        ("amount", "Amount"),
        ("expected_close_date", "Closing"),
        ("owner_email", "Owner"),
    ),
    "leads": (
        ("lead_name", "Lead"),
        ("organization_name", "Company"),
        ("email", "Email"),
        ("owner_email", "Owner"),
        ("converted_deal_id", "Converted"),
    ),
    "contacts": (
        ("first_name", "Name"),
        ("title", "Title"),
        ("email", "Email"),
        ("phone", "Phone"),
        ("owner_email", "Owner"),
    ),
    "organizations": (
        ("name", "Company"),
        ("industry", "Industry"),
        ("phone", "Phone"),
        ("owner_email", "Owner"),
    ),
}


class ExportParams:
    """The export's query parameters — ``ListParams`` minus pagination.

    A FastAPI class dependency for the same reason ``records.ListParams`` is
    one: the contract is supposed to be the same for every entity, and the way
    that stops being true is one endpoint quietly growing a different filter.

    ``page``/``page_size`` are absent on purpose. A page is a screen's unit, and
    half a filter's records in a file IS the silent truncation this endpoint
    refuses; the cap is a refusal instead. ``sort``/``dir`` are kept because the
    order on screen is part of what somebody is exporting.

    Fence: ``test_crm_export.py::
    test_every_list_filter_reaches_the_export_or_is_named_as_excluded`` compares
    this parameter set against the list route's, per entity — FastAPI ignores an
    unknown query parameter, so a filter the list gains and this does not would
    silently stop applying the moment somebody pressed Export.
    """

    def __init__(
        self,
        q: str | None = None,
        sort: str | None = None,
        direction: str = Query("desc", alias="dir"),
        status_id: str | None = None,
        owner: str | None = None,
        source: str | None = None,
        include_converted: bool = False,
    ) -> None:
        self.q = q
        self.sort = sort
        self.direction = direction
        self.status_id = status_id
        self.owner = owner
        self.source = source
        self.include_converted = include_converted


def _cell(entity: Entity, key: str, row: dict[str, Any]) -> Any:
    """One row's value for one column, before ``csv_cell``.

    Almost every column is the stored value, deliberately: ``amount`` exports as
    the number rather than ``format.money``'s "₹1.2L" and ``owner_email`` as the
    whole address rather than ``format.shortEmail``'s initial, because those
    vocabularies live in the browser and copying either here would be a second
    vocabulary that drifts. A spreadsheet wants the number and the full address
    anyway — ``120000`` sums and ``priya@fracktal.in`` is what you paste into a
    mail merge.

    Contacts' ``Name`` is the one exception, and it is not a formatting one: the
    list draws ``first_name last_name`` under that single header, so exporting
    ``first_name`` alone would drop half the value rather than render it
    differently.
    """
    if entity is CONTACTS and key == "first_name":
        return " ".join(
            part for part in (row.get("first_name"), row.get("last_name")) if part
        )
    return row.get(key)


def _too_many(entity: Entity, total: int, *, at_least: bool = False) -> HTTPException:
    """The refusal, and the reason it is a refusal — see rule 3.

    It names the real number so the caller can judge how much narrowing is
    needed rather than guessing. ``at_least`` is the render-side form: there the
    endpoint knows the set overflowed but deliberately did not count past the
    cap, and "at least 10001" is honest where a bare "10001" would be a figure
    somebody could quote.
    """
    counted = f"at least {total}" if at_least else str(total)
    return HTTPException(
        status_code=422,
        detail=(
            f"{counted} {entity.slug} match these filters and the export cap "
            f"is {MAX_EXPORT_ROWS}. Narrow the filter (a stage, an owner, a "
            f"search) and export again — this endpoint will not hand back a "
            f"partial file."
        ),
    )


async def _export(entity: Entity, params: ExportParams) -> Response:
    """The caller's current filter, as a CSV file. One implementation, four
    entities — the shape ``records._list`` uses for the same reason."""
    extra: tuple[str, ...] = ()
    if entity.hides_converted and not params.include_converted:
        # §3.3/B6, `records._list`'s clause verbatim: a lead is "converted"
        # while the deal it became still exists.
        extra = ("converted_deal_id IS NULL",)
    # Reused for the WHERE clause and the ORDER BY — and for the 422s it raises
    # on an unknown sort key, an unknown direction and a status filter on an
    # entity with no pipeline. Its `limit` is the PAGE clamp and is never read
    # here; see rule 2 in the module docstring.
    query = list_contract(
        entity,
        q=params.q, sort=params.sort, direction=params.direction,
        status_id=params.status_id, owner=params.owner, source=params.source,
        extra_where=extra,
    )

    async with _tenant_session() as db:
        total = int((await db.execute(
            text(f"SELECT count(*) FROM {entity.table}{query.where}"),
            query.params,
        )).scalar() or 0)
        if total > MAX_EXPORT_ROWS:
            # Cheap and early: refused before a single row is rendered, and the
            # count is exact so the message can name it. It is not the only
            # gate — see the render below.
            raise _too_many(entity, total)

        inner = (
            f"SELECT * FROM {entity.table}{query.where} "
            f"ORDER BY {query.order_by} LIMIT :cap"
        )
        rows = (await db.execute(
            # The same `project_joined` wrapper `core.run_list` uses, so a deal
            # carries its organization's name here exactly as it does on the
            # board. Without it the Company column would be empty on every
            # row, which reads as missing data rather than a missing join.
            text(project_joined(entity, inner, qualify(query.order_by, "base"))),
            # ⚠️ **cap + 1, and the check below is what makes it a refusal.**
            # `_tenant_session()` opens one transaction but Postgres defaults to
            # READ COMMITTED, so the count and this render take DIFFERENT
            # snapshots: count answers 9,998 (no 422), the auto-lead path
            # inserts five matching rows, and a `LIMIT :cap` of exactly
            # MAX_EXPORT_ROWS then returns exactly MAX_EXPORT_ROWS — a partial
            # file with a 200 and no warning, which is the one outcome rule 3
            # forbids. Fetching one row past the cap makes "did this overflow"
            # a property of the rendered set rather than of a count taken
            # earlier, so the invariant no longer depends on the two agreeing.
            # This is NOT the calendar's truncate-and-say-so probe: the extra
            # row is never rendered, it is the evidence for a refusal.
            {**query.params, "cap": MAX_EXPORT_ROWS + 1},
        )).fetchall()
        if len(rows) > MAX_EXPORT_ROWS:
            raise _too_many(entity, len(rows), at_least=True)
        exported = [row_to_dict(r, entity.model) for r in rows]

    columns = COLUMNS[entity.slug]
    return Response(
        content=csv_document(
            [header for _, header in columns],
            (
                [_cell(entity, key, row) for key, _ in columns]
                for row in exported
            ),
        ),
        media_type="text/csv; charset=utf-8",
        headers={
            # The server owns the filename and builds it from the entity SLUG
            # only — never from a record name, which is free text out of Zoho
            # (quotes, newlines, non-ASCII) and therefore a header-injection
            # surface in a response header.
            "Content-Disposition": (
                f'attachment; filename="crm-{entity.slug}.csv"'
            ),
            # An honest count beside the file, for anything reading this
            # programmatically. The file itself is complete by construction.
            #
            # ⚠️ A response header is only real if the caller can reach it: the
            # BFF proxy forwards a fixed set, and for the first cut of this
            # endpoint that set was `Content-Type` + `Content-Disposition`, so
            # this header existed and nothing could ever read it.
            # `api/crm/[...path]` and `api/projects/[...path]` now forward it
            # too (fence: `src/lib/export.test.ts`, "forwards the server's
            # filename and its row count"). Anything else that fronts this
            # endpoint has to, or the header is a no-op again.
            "X-Export-Rows": str(len(exported)),
        },
    )


@router.get("/export/leads.csv")
async def export_leads_csv(
    params: ExportParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> Response:
    return await _export(LEADS, params)


@router.get("/export/deals.csv")
async def export_deals_csv(
    params: ExportParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> Response:
    return await _export(DEALS, params)


@router.get("/export/contacts.csv")
async def export_contacts_csv(
    params: ExportParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> Response:
    return await _export(CONTACTS, params)


@router.get("/export/organizations.csv")
async def export_organizations_csv(
    params: ExportParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> Response:
    return await _export(ORGANIZATIONS, params)


#: Slug → handler, for the tests that exercise all four without a TestClient.
#:
#: The parametrised suites index this by every key of :data:`core.ENTITIES`, so
#: an entity added to the registry without an export handler fails as a
#: ``KeyError`` rather than as a collection that quietly cannot be exported.
HANDLERS = {
    "leads": export_leads_csv,
    "deals": export_deals_csv,
    "contacts": export_contacts_csv,
    "organizations": export_organizations_csv,
}


__all__ = ["COLUMNS", "HANDLERS", "MAX_EXPORT_ROWS", "ExportParams"]
