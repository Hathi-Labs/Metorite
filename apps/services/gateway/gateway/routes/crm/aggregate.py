"""CRM · aggregate — totals and stage dwell for an agent (WS-53 CRM-C7, CRM-Q25).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 2, and §18.11
CRM-Q25 (the typed aggregate).

    GET /crm/aggregate               one measure, by one group or overall
    GET /crm/aggregate/stage-dwell   days in each deal stage, and the deals
                                     that sit past their limit

**Read only.** Any member that the CRM access rule admits may call them: the
routes sit on the shared ``core.router``, so the kill switch, ``feature:crm``
and the access rule apply. The tenant comes from the session that the router
binds, never from the request (R5).

**The entity.** A core entity (``leads``, ``deals`` …), or a mirrored generic
module as ``module:<ApiName>`` (CRM-Q25). A module that the profile does not
mirror is a 404 (``modules._require_module``), before any read of a record.

**The measure.** ``count``, ``sum_amount`` and ``weighted`` (deals), or
``sum``, ``avg``, ``min`` and ``max`` of one number field, given as
``field=<key>``. On a module, ``sum_amount`` is the sum of the amount that
the catalogue names, and ``weighted`` is a 422 (CRM-Q31-2). A module also
takes the key words of ``field_sql`` as a field name. This module holds no
read of the catalogue and no alias rule: both live in ``field_sql.py`` and
``catalogue.py``.

**The group.** ``group_by`` names a fixed key of :data:`GROUPS`, or of
:data:`MODULE_GROUPS` for a module. ``group_field=<key>`` groups by one field
instead, with ``bucket`` for a date. A call gives one of the two, or neither.
With neither, the answer is the measure over every filtered record: no
group, the ``total``, and the count of records in ``records``. A model asks
"the sum of the invoices of October" with no group, and a 422 on that taught
it nothing.

**One filter builder.** A core entity takes the filters of the list routes,
through the same ``records.ListParams`` and ``records.list_query``. So an
aggregate counts exactly the rows that the list with the same filters shows.
Do not copy a filter here. Fence:
``tests/unit/test_crm_aggregate.py::test_the_aggregate_reuses_the_list_filter_builder``.
A module takes only the field filters ``field.<key>=<value>`` and
``field.<key>.from`` / ``.to``.

**One query** (WS-SEM SEM-1b-1, ``semantic_model.md`` §5.1). For an org on
``SEMANTIC_MODEL_ORGS``, a module total also takes ``query``: a JSON list of
conditions and ``any`` groups. ``field_sql.parse_query`` checks it, and
``field_sql.query_where`` builds its clause with the builder of each
``field.<key>`` filter. The clause joins the old filters by AND. A ``query``
on a core entity, and from an org off the list, is a 422 that names a step
that works. This module holds none of its SQL. Fence:
``tests/unit/test_crm_query_grammar.py``.

**The field SQL lives in ``field_sql.py``.** Each field name is checked
against the field definitions there, and every key and value is bound. This
module holds no jsonb operator. Fence:
``tests/unit/test_crm_aggregate.py::test_the_aggregate_holds_no_field_sql``.

**Fixed SQL.** Every identifier in a statement comes from a literal of this
module or of ``field_sql``, and every caller value is a bound parameter. The
statement runs under a LOCAL ``statement_timeout`` of 10 s.

**The overlay of an empty core slot** (CRM-Q20-3a, ``crm_platform.md``
§18.13.5). When an approved ``role`` names a module for the slot of a core
entity (``overlay.overlay_of``), the total reads the records of that module
through the projection table of §18.13.5. The SQL of it lives in
``field_sql.py``. Each refusal is a 422 that names an ``overlay_*`` code. A
status group follows the lane order, with one "Unsorted" group last. The
answer carries ``overlay``. With no overlay, the answer is the answer of
today, byte for byte.

**At most 50 groups.** The answer keeps the 50 largest groups. ``other`` holds
the measure over the rest, and ``omitted`` counts them. The order stays: the
status groups in board order, the month and date buckets in time order, and
each other group largest first.

**Two group fields, and pages of groups** (CRM-Q34-10, ``crm_platform.md``
§18.17). On a module, ``group_field2`` names a second field, so a group is a
pair, such as a site and a month. At most one of the two is a date, and
``bucket`` goes to it. Such a call is paged IN SQL with ``group_page`` and
``group_page_size`` (1 to :data:`GROUP_PAGE_MAX`, default :data:`MAX_GROUPS`),
largest value first, then by each key. With ONE field, only a page past 1 of
a value group is paged. Page 1 is the answer of one page. On the list, for a
value group of a module with MORE than 50 groups, it cuts its 50 groups in
the order of the paged read (``cut_key``, ``field_sql.one_group_order``), so
page 2 starts where it stops, and it carries ``next_group_page``. There, two
groups whose values differ by under half a cent can change places, and so
can the set of groups at the cut and ``other``. Every other answer of one
page (50 groups or fewer, off the list, a core entity, a date group, a
split, a ``group_by`` key word, ``q``) keeps the order of before, byte for
byte. A paged answer adds ``group_count``, ``group_page`` and
``group_pages``. ``total`` and ``records`` stay the answer over every group.
It is dark behind ``CRM_MODULE_FIND_ORGS`` (``module_find_allowed``). Off the
list, ``group_field2`` is a 422 that names the way without it, and the page
parameters are ignored, so every other call is the answer of today.

**One zone for each month** (CRM-Q25b). ``created_month``, ``updated_month``
and a ``datetime`` bucket take the month on the clock of the CRM zone,
``field_sql.crm_zone``: the zone of the Zoho org, or UTC. The zone is a bound
value, and the answer names it in ``zone``. ``close_month`` and a ``date``
field keep their day, because a date has no time.

**One currency at a time** (HANDOFF H-333, task 888). A deal carries a
``currency`` and the app holds no exchange rate. So ``sum_amount`` and
``weighted`` on DEALS never add two currencies. The statement runs once for each
currency present in the filtered rows, with a bound ``currency = :agg_currency``
predicate. The ``value`` of each group and the ``total`` hold the PRIMARY
currency (INR first, then the other codes in code order), named in ``currency``.
The other currencies sit in ``others``, with a code on each: in each group, and
in the response for the ``total`` and the ``other``. One currency gives no
``others`` on the wire, so the answer of today does not change. ``count`` is not
money and counts every currency. ``currency.py`` normalises the code
(``upper(trim(currency))``, with ``''`` and NULL read as INR). The
``sum_amount`` of a MODULE (the catalogue amount, CRM-Q31-2) and the
``weighted`` of an overlay have NO currency column, so they stay as they were:
one sum, and no ``currency``. The weekly digest and the AI tool read this
split, so they follow the same rule.

**Weighted ₹ reuses ``core.WEIGHTED_SQL``.** That expression binds the lane's
own probability as ``:stage_probability``, so the weighted measure runs one
grouped statement for each open or ongoing deal lane and adds the results.
The deal's own probability wins, and the stage probability fills a NULL. That
is the effective value of today. CRM-C3 changes the stage half.

**Stage dwell reads ``crm_status_changes``.** A Mirror org has no such rows,
because the sync writes none. So in Mirror mode the route answers an empty
result with a ``reason``, and never a number that looks measured.

**One body for the route and the digest** (CRM-Q28). :func:`compute` is
the body of ``GET /crm/aggregate``. The weekly digest
(``crm_sync/digest.py``) calls the same function for each of its fixed
queries, with the org of its run bound. So a digest number and a route
number are one answer. Fence: ``tests/unit/test_crm_digest.py``.

**Numbers that make sense** (NUM-2, ``project-docs/specs/number_sense.md``
§4.2). A module answer over 1 to 3 whole months of a date field also reads
the same measure for the 6 periods before it, in the SAME statement: only
the start bound widens, and each row names its period. The answer carries
them in ``baseline``. ``coverage`` counts the records with no value in the
measure field, and the drafts and cancelled records where the module has a
catalogue status. ``checks`` holds the ``outlier`` and ``coverage`` findings
of ``acb_common.number_sense``. A ``concentration`` stays in ``findings``.

**Two reads for My Day** (WS-58 MD-3e). ``my_overdue_deals`` and
``my_close_passed_deals`` are not routes. The feed's ``crm`` provider calls
them with its own session, after the kill switch and the access rule. Each
reads only the member's own deals, and each statement names the tenant.
Fence: ``tests/unit/test_feed_crm_kinds_r8.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from acb_common.number_sense import (
    CONCENTRATION_MIN_RECORDS,
    COVERAGE_STATUSES,
    coverage,
    outlier,
    part_period,
)
from acb_common.number_sense import findings as number_findings
from fastapi import Depends, HTTPException, Query, Request
from gateway.routes.crm.core import (
    CLOSING_TYPES,
    DEAL_PIPELINE_SQL,
    ENTITIES,
    MAX_PAGE_SIZE,
    WEIGHTED_SQL,
    WEIGHTED_TYPES,
    Entity,
    _tenant_session,
    current_tenant,
    is_mirror_mode,
    resolve_owner,
    router,
)
from gateway.routes.crm.core import _now as _crm_now
from gateway.routes.crm.currency import CURRENCY_SQL, ordered_currencies
from gateway.routes.crm.field_sql import (
    BASELINE_MAX_RECORDS,
    BASELINE_PERIODS,
    DATE_TYPES,
    FIELD_MEASURES,
    FIELD_PREFIX,
    GROUP2_KEY,
    STATUS_WORD,
    ZONE_PARAM,
    Baseline,
    FieldRef,
    FieldScope,
    QueryNode,
    TextNumbers,
    admit_text_numbers,
    baseline_range,
    check_group,
    check_groups,
    check_measure,
    check_split,
    core_scope,
    crm_zone,
    day_field,
    day_sql,
    field_hint,
    field_pairs,
    filter_probe,
    find_near,
    find_where,
    find_words,
    from_source,
    group_sql,
    lookup_ids,
    matched_values,
    measure_sql,
    module_measure,
    module_scope,
    module_where,
    one_group_order,
    overlay_amount,
    overlay_close,
    overlay_filters,
    overlay_probability,
    overlay_refusal,
    overlay_stage,
    overlay_weighted,
    overlay_where,
    parse_query,
    period_sql,
    q_refusal,
    query_core_refusal,
    query_off_refusal,
    query_ranges,
    query_where,
    split_sql,
    status_field,
    status_sql,
    text_matches,
    text_number_keys,
    used_keys,
    widen,
    word_names,
    zoned,
)
from gateway.routes.crm.flags import (
    answer_first_allowed,
    long_read_allowed,
    module_find_allowed,
    semantic_model_allowed,
)
from gateway.routes.crm.modules import _require_module, mirrored_modules
from gateway.routes.crm.overlay import (
    SLOT_OF,
    UNSORTED,
    UNSORTED_LABEL,
    Overlay,
    lanes,
    overlay_of,
)
from gateway.routes.crm.records import ListParams, list_query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_log = get_logger("crm.aggregate")

#: ``measure`` → the SQL aggregate. ``weighted`` is not here: it reuses
#: ``core.WEIGHTED_SQL`` lane by lane (see the module doc). The field measures
#: are ``field_sql.FIELD_MEASURES``.
MEASURES: dict[str, str] = {
    "count": "count(*)",
    "sum_amount": "COALESCE(SUM(amount), 0)",
    "weighted": "",
}

#: The measures that read ``amount``, which only a deal has.
DEAL_MEASURES = frozenset({"sum_amount", "weighted"})

#: The measures whose groups add up to the total.
SUMMING_MEASURES = frozenset({"count", "sum_amount", "weighted", "sum"})

#: ``group_by`` → the SQL key. Each value is a literal of this module.
GROUPS: dict[str, str] = {
    "status": "status_id",
    "owner": "lower(owner_email)",
    "pipeline": "pipeline_id",
    "source": "source",
    # CRM-Q25b: a month on the clock of the CRM zone, the bound ZONE_PARAM.
    "created_month": f"to_char(date_trunc('month', {zoned('created_at')}), 'YYYY-MM')",
    # The provider's modified time when the row has one, as the Q8 filter.
    "updated_month": (
        "to_char(date_trunc('month', "
        f"{zoned('coalesce(ext_modified_at, updated_at)')}), 'YYYY-MM')"
    ),
    # A date has no time, so the close month takes no zone.
    "close_month": (
        "to_char(date_trunc('month', expected_close_date), 'YYYY-MM')"
    ),
}

#: ``group_by`` on a generic module (CRM-Q25). A module record keeps Zoho's
#: created time in ``ext_created_at``, so its created month is a real one.
#: The owner is the member email of the Zoho owner, NULL for a non-member.
MODULE_GROUPS: dict[str, str] = {
    "owner": "lower(owner_email)",
    "created_month": (
        f"to_char(date_trunc('month', {zoned('ext_created_at')}), 'YYYY-MM')"
    ),
    "updated_month": (
        "to_char(date_trunc('month', "
        f"{zoned('coalesce(ext_modified_at, updated_at)')}), 'YYYY-MM')"
    ),
}

#: The table of every generic module record.
MODULE_TABLE = "crm_module_records"

#: The entity word of a generic module: ``module:<ApiName>``.
MODULE_PREFIX = "module:"

#: The query parameters a module aggregate takes, beside ``field.<key>``. A
#: list filter on a module is a 422, never ignored.
MODULE_PARAMS = frozenset({
    "entity", "measure", "group_by", "group_field", "field", "bucket",
    "page", "page_size", "sort", "dir", "fields", "long_read",
    "group_field2", "group_page", "group_page_size",
    # SEM-1b-1: `_compute` refuses it off SEMANTIC_MODEL_ORGS, never ignores it.
    "query",
})

#: The groups that only a deal has.
DEAL_GROUPS = frozenset({"pipeline", "close_month"})

#: The month groups sort by month. Every other group sorts by value.
MONTH_GROUPS = frozenset({"created_month", "updated_month", "close_month"})

#: The most groups one answer holds. The rest fold into ``other``.
MAX_GROUPS = 50

#: CRM-Q34-10: the most groups on one page of a paged answer.
GROUP_PAGE_MAX = 200
#: CRM-Q34-10: the highest page number. ``page * size`` stays far inside a
#: bigint, so a huge page is a 422 and never a 500.
GROUP_PAGES_MAX = 1_000_000

#: CRM-Q34-10: what an org off ``CRM_MODULE_FIND_ORGS`` reads for a second
#: group field.
TWO_GROUP_OFF = (
    "A second group field is not on for this organization. "
    "Group by one field, and filter by the other: group_field=<a> with "
    "field.<b>=<value>."
)

#: The limit of each statement of the aggregate, in milliseconds (CRM-Q25).
STATEMENT_TIMEOUT_MS = 10_000

#: ``SET LOCAL``: it ends with the read's transaction. ``set_config`` takes a
#: bind, and ``SET`` does not (``shell/feed_measure.py``).
_TIMEOUT_SQL = "SELECT set_config('statement_timeout', :ms, true)"

#: The SQLSTATE of a statement that the timeout cancelled.
_QUERY_CANCELED = "57014"

#: What a member reads when the statement runs past the timeout.
TIMEOUT_DETAIL = {
    "code": "aggregate_timeout",
    "message": "The total took longer than 10 seconds. Narrow the filter.",
}

# ── The long read (CRM-Q25e part 5, task 2985 HRD-6) ────────────────────────
#
# Owner, 2026-11-13: "large CRM queries might take even minutes". For an org
# on CRM_LONG_READ_ORGS, the 503 of a timed-out total offers ONE long read
# (``long_read: true`` in the detail), and the chat tool asks for it with
# ``long_read=true``. The guard keeps long statements from filling the pool:
# one long statement for each org at a time, and LONG_READ_SLOTS in one
# process. The gateway runs one process (``acb-gateway.service``). Its pool
# is db_pool_size 7 + db_max_overflow 2 = 9 connections, and background work
# may hold 4 of them (db_background_max). 2 long reads leave 3 for pages.
# With the list empty, no 503 offers it and no statement runs past 10 s.

#: The whole budget of one long read, in seconds: the wait for the guard and
#: the LOCAL ``statement_timeout`` of its statements together.
LONG_READ_SECONDS = 180
#: The most long statements that one gateway process runs at one time.
LONG_READ_SLOTS = 2

#: What a member reads when a long read runs past its budget.
LONG_TIMEOUT_DETAIL = {
    "code": "aggregate_timeout",
    "message": "The total took longer than 3 minutes, so the CRM stopped it. Narrow the "
               "date range, or split it into shorter ranges.",
}
#: The fewest seconds that a long read starts with. A read that waited for
#: the guard past this point gets 503 ``aggregate_busy``, never a timeout.
LONG_READ_MIN_SECONDS = 30
#: What a member reads when the long reads of the org or of the gateway took
#: the whole budget of this one.
BUSY_DETAIL = {
    "code": "aggregate_busy",
    "message": "Other long totals took the whole wait. Ask again in a few minutes, "
               "or narrow the filter.",
}

#: The guard of each event loop: the slots of the process, and one lock for
#: each org. Only an org on CRM_LONG_READ_ORGS adds a lock, so the map stays
#: as small as that list. It holds no tenant data.
_LONG_GATES: dict[Any, tuple[asyncio.Semaphore, dict[str, asyncio.Lock]]] = {}


def _long_gate() -> tuple[asyncio.Semaphore, dict[str, asyncio.Lock]]:
    loop = asyncio.get_running_loop()
    gate = _LONG_GATES.get(loop)
    if gate is None:
        _LONG_GATES.clear()  # a loop that ended (tests) never returns
        gate = _LONG_GATES[loop] = (asyncio.Semaphore(LONG_READ_SLOTS), {})
    return gate


async def _take(lock: Any, start: float, budget: float) -> None:
    """Take *lock* inside what is left of *budget*, or 503 ``aggregate_busy``.
    Waiting holds no connection, because the session opens after."""
    left = budget - (time.monotonic() - start)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=max(0.001, left))
    except TimeoutError:
        raise HTTPException(status_code=503, detail=BUSY_DETAIL) from None


@asynccontextmanager
async def long_read_slot(org: str, budget: float) -> AsyncIterator[float]:
    """Hold the ONE long read of *org* and one slot of the process, and give
    the seconds left of *budget* for the statements. A second long read of
    the same org waits inside its own budget, before it opens a session. A
    read that the wait left with less than :data:`LONG_READ_MIN_SECONDS`
    (or half of a small budget) gets 503 ``aggregate_busy``: the wait took
    its time, not the total."""
    start = time.monotonic()
    slots, orgs = _long_gate()
    lock = orgs.setdefault(org, asyncio.Lock())
    await _take(lock, start, budget)
    try:
        await _take(slots, start, budget)
        try:
            left = budget - (time.monotonic() - start)
            if left < min(LONG_READ_MIN_SECONDS, budget / 2):
                raise HTTPException(status_code=503, detail=BUSY_DETAIL)
            yield left
        finally:
            slots.release()
    finally:
        lock.release()

#: What a Mirror org gets from the dwell route.
MIRROR_DWELL_REASON = (
    "This CRM mirrors Zoho, and the sync records no stage changes. "
    "So the days in each stage are not known here."
)


class CurrencyValue(BaseModel):
    """The value of ONE group in ONE currency that is not the primary one."""

    currency: str
    value: float | None


class CurrencyTotal(BaseModel):
    """The ``total`` and the ``other`` of ONE currency that is not the primary."""

    currency: str
    total: float | None
    other: float | None = 0


class AggregateGroup(BaseModel):
    #: The raw key: a status id, an email, a month (YYYY-MM), a quarter
    #: (YYYY-Qn), a year, a field value or a lookup id.
    key: str | None
    #: What a person calls it: the stage, pipeline, option or lookup name.
    label: str
    #: None when no record of the group holds a value (``avg``, ``min``, ``max``).
    value: float | None
    #: CRM-Q20-3a: the lane type of a status group of an overlay: ``open``,
    #: ``won``, ``lost`` or ``unsorted``. Absent from every other answer.
    type: str | None = Field(default=None, exclude_if=lambda value: value is None)
    #: The value in each currency that is not the response's ``currency``.
    #: Absent with one currency (task 888).
    others: list[CurrencyValue] = Field(
        default_factory=list, exclude_if=lambda value: not value)
    #: CRM-Q25e part 2: each field of a split → its value in this group.
    #: Absent with no split.
    split: dict[str, float | None] = Field(
        default_factory=dict, exclude_if=lambda value: not value)
    #: CRM-Q34-10: the key and the name of the second group field. Absent
    #: with one group field. ``key2`` is also absent for the group of the
    #: records with no value there, and ``label2`` then reads "(none)".
    key2: str | None = Field(default=None, exclude_if=lambda value: value is None)
    label2: str | None = Field(default=None, exclude_if=lambda value: value is None)


class SplitTotal(BaseModel):
    """CRM-Q25e part 2: the total of ONE field of a split."""

    field: str
    label: str
    type: str
    #: None when no record holds a value of the field (CRM-Q25c item 3).
    total: float | None
    #: The count of values that the measure read.
    values: int
    #: The measure of this field over the groups past :data:`MAX_GROUPS`.
    other: float | None = 0


class AggregateResponse(BaseModel):
    entity: str
    measure: str
    #: The fixed key, or None when the answer groups by ``group_field``.
    group_by: str | None = None
    group_field: str | None = None
    #: The field of a ``sum``, ``avg``, ``min`` or ``max``, and its type.
    field: str | None = None
    field_type: str | None = None
    #: The label of that field, and of the group field (CRM-Q31-2 item 5).
    field_label: str | None = None
    group_field_label: str | None = None
    #: Each key word that the call used, with the real key that it read. A
    #: sum of fields (CRM-Q32-9) gives the list of its keys.
    keys: dict[str, str | list[str]] = {}
    #: The stored type of ``group_field``, so a caller can tell a ``datetime``
    #: bucket, which reads the zone, from a ``date`` bucket, which does not.
    group_field_type: str | None = None
    bucket: str | None = None
    groups: list[AggregateGroup]
    #: The measure over every group, the omitted ones too.
    total: float | None
    #: The measure over the groups past :data:`MAX_GROUPS`.
    other: float | None = 0
    #: ``sum_amount`` and ``weighted`` on deals only: the currency that
    #: ``total``, ``other`` and each group ``value`` are in. Absent otherwise.
    currency: str | None = Field(default=None, exclude_if=lambda value: value is None)
    #: The ``total`` and ``other`` of each other currency. Absent with one.
    others: list[CurrencyTotal] = Field(
        default_factory=list, exclude_if=lambda value: not value)
    #: The count of groups past :data:`MAX_GROUPS`.
    omitted: int = 0
    #: The CRM zone of every month and datetime bucket (CRM-Q25b): the zone
    #: of the Zoho org, or UTC.
    zone: str = "UTC"
    #: The count of field values that a ``sum``, ``avg``, ``min`` or ``max``
    #: read (CRM-Q25c item 3). 0 means the field is empty in every row, and
    #: then ``total`` and each group value are None. None for ``count``,
    #: ``sum_amount`` and ``weighted``, which read no field.
    values: int | None = None
    #: The count of records that the filters matched, over every group. None
    #: for ``weighted``, which reads only the open and ongoing deal lanes.
    records: int | None = None
    #: CRM-Q20-3a: the slot and the module of an overlay, for example
    #: ``{"slot": "deal", "module": "Commercials"}``. Absent with no overlay.
    overlay: dict[str, str] | None = Field(default=None, exclude_if=lambda value: value is None)
    #: WS-53 §18.16 part 6: each text filter of a module → the stored values
    #: that it counted ("in any case, and each short form"), so the answer
    #: can say which ones. Absent with no text filter.
    matched: dict[str, list[str]] | None = Field(
        default=None, exclude_if=lambda value: value is None)
    #: CRM-Q25e part 2: the total of each field of ``fields``, in order. The
    #: first one is also ``field`` and ``total``. Absent with no split.
    split: list[SplitTotal] = Field(default_factory=list, exclude_if=lambda value: not value)
    #: CRM-Q34-11: each text field of a module that the call read as
    #: numbers → its counts over the whole module (``numbers``, ``no_value``
    #: and ``not_numbers``) and the most common ``shapes`` of the values that
    #: are not numbers (``field_sql.TextNumbers.wire``). Absent when the call
    #: read no text field, so the wire is the one of before.
    text_numbers: dict[str, dict[str, Any]] | None = Field(
        default=None, exclude_if=lambda value: not value)
    #: CRM-Q25e part 4: what the reader must know before the total
    #: (``acb_common.number_sense``), for example one day that holds most
    #: records. Each has ``kind``, ``key``, ``records``, ``share`` and one
    #: ``sentence``. Absent with none.
    findings: list[dict[str, Any]] = Field(
        default_factory=list, exclude_if=lambda value: not value)
    #: CRM-Q34-7: no record holds each word of ``q``, so the total counts
    #: the records that hold one of these stored names, near spellings of a
    #: word. Absent when the exact words matched.
    near: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
    #: CRM-Q34-7: ``q`` counted no record. At most 3 stored names nearest to
    #: ``q``, for the next call. Absent otherwise.
    nearest: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
    #: CRM-Q34-10: the second group field, its label and its stored type.
    #: Absent with one group field.
    group_field2: str | None = Field(default=None, exclude_if=lambda value: value is None)
    group_field2_label: str | None = Field(
        default=None, exclude_if=lambda value: value is None)
    group_field2_type: str | None = Field(
        default=None, exclude_if=lambda value: value is None)
    #: CRM-Q34-10: a paged answer only. The count of every group, this page
    #: (from 1), and the count of pages. Absent from every other answer.
    group_count: int | None = Field(default=None, exclude_if=lambda value: value is None)
    group_page: int | None = Field(default=None, exclude_if=lambda value: value is None)
    group_pages: int | None = Field(default=None, exclude_if=lambda value: value is None)
    #: CRM-Q34-10: 2 when the answer of one page left groups out and a
    #: ``group_page=2`` continues it (:func:`_paging`). Absent otherwise.
    next_group_page: int | None = Field(
        default=None, exclude_if=lambda value: value is None)
    #: CRM-Q34-5: each ``field.<key>`` filter name that the route read as a
    #: filter word (``field_sql.word_names``), so the tool words it. Absent
    #: with none, so the answer of each older call stays the same.
    filter_words: list[str] | None = Field(
        default=None, exclude_if=lambda value: value is None)
    #: NUM-2 (``number_sense.md`` §4.2): the findings of the kinds ``outlier``
    #: and ``coverage``, as ``Finding.wire()``. A ``concentration`` stays in
    #: ``findings``, so no finding is in both lists. Absent with none.
    checks: list[dict[str, Any]] = Field(
        default_factory=list, exclude_if=lambda value: not value)
    #: NUM-2: the same measure for each of the 6 periods before the asked
    #: date range, oldest first, from the same scan. Each has ``period``,
    #: ``value`` and ``records``. Absent with no date range.
    baseline: list[dict[str, Any]] = Field(
        default_factory=list, exclude_if=lambda value: not value)
    #: NUM-2: ``records``, the records with ``no_value`` in the measure field,
    #: and the count of each status that a total usually leaves out
    #: (``statuses``). Absent with no measure field and no status field.
    coverage: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None)


class StageDwell(BaseModel):
    status_id: str
    name: str
    type: str
    max_dwell_days: int | None = None
    #: Measured departures from this stage. None of the numbers below is a
    #: number without it.
    samples: int
    median_days: float | None = None
    p90_days: float | None = None


class OverdueDeal(BaseModel):
    id: str
    name: str
    stage: str
    owner_email: str | None = None
    amount: float | None = None
    currency: str | None = None
    days_in_stage: float
    limit_days: int


class StageDwellResponse(BaseModel):
    stages: list[StageDwell]
    overdue: list[OverdueDeal]
    overdue_total: int
    #: Set when the numbers are not known, for example in Mirror mode.
    reason: str | None = None


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _check_fields(measure: str, field: str | None, fields: str | None) -> str | None:
    """The field that :func:`_check` checks: *fields* of a split (CRM-Q25e
    part 2) stands for it. A split goes with a field measure, never with
    ``field`` too. Each key is checked later, in ``field_sql.check_split``."""
    if fields is None:
        return field
    if field:
        raise _refuse("Give field or fields, not both.")
    if measure not in FIELD_MEASURES:
        raise _refuse(f"'fields' goes with {list(FIELD_MEASURES)}, not with '{measure}'.")
    return fields


def _check(
    entity: str, measure: str, group_by: str | None, group_field: str | None,
    field: str | None, bucket: str | None, fields: str | None = None,
) -> tuple[Entity | None, str | None]:
    """The core entity or the module name, or a 422 for an unknown or unfit
    value. A field name is checked later, against the field definitions.
    *fields* is the comma list of a split (CRM-Q25e part 2)."""
    field = _check_fields(measure, field, fields)
    module: str | None = None
    found: Entity | None = None
    if entity.startswith(MODULE_PREFIX):
        module = entity[len(MODULE_PREFIX):]
        if not module:
            raise _refuse("Name the module: 'module:<ApiName>'.")
    else:
        found = ENTITIES.get(entity)
        if found is None:
            raise _refuse(
                f"Unknown entity '{entity}'. One of: {sorted(ENTITIES)}, "
                "or 'module:<ApiName>'.",
            )
    if measure not in MEASURES and measure not in FIELD_MEASURES:
        raise _refuse(
            f"Unknown measure '{measure}'. One of: "
            f"{[*MEASURES, *FIELD_MEASURES]}.",
        )
    if measure in FIELD_MEASURES and not field:
        raise _refuse(f"'{measure}' needs a number field, given as field=<key>.")
    if field and measure not in FIELD_MEASURES:
        raise _refuse(
            f"'field' goes with {list(FIELD_MEASURES)}, not with '{measure}'.",
        )
    if group_by is not None and group_field is not None:
        raise _refuse(
            "Give group_by or group_field, not both. Give neither for one "
            "total over every record.",
        )
    if bucket is not None and group_field is None:
        raise _refuse("'bucket' goes with group_field.")
    allowed = MODULE_GROUPS if module is not None else GROUPS
    # CRM-Q34-2: `status` on a module groups by its status field (`@status`).
    status_group = module is not None and group_by == "status"
    if group_by is not None and group_by not in allowed and not status_group:
        raise _refuse(
            f"Unknown group_by '{group_by}'. One of: {list(allowed)}. To group "
            "by a field, give group_field=<key> instead, or give no group for "
            "one total.",
        )
    if module is not None:
        if measure == "weighted":
            raise _refuse("'weighted' needs deals. A module has no probability.")
        return None, module
    assert found is not None
    if found.table != "crm_deals" and (
        measure in DEAL_MEASURES or group_by in DEAL_GROUPS
    ):
        raise _refuse(
            f"'{measure}' by '{group_by}' needs deals. "
            f"{entity} have no amount, pipeline or close date.",
        )
    if group_by == "status" and not found.status_table:
        raise _refuse(f"{entity} have no status pipeline. Group by another key.")
    return found, None


def _core_pairs_refusal() -> HTTPException:
    """A core entity with no overlay takes no ``field.<key>`` filter."""
    return _refuse(
        "A field.<key> filter takes a module entity. A core entity "
        "takes the list filters, and custom.<key>=<value> for a "
        "custom field.",
    )


def _check_filters(module: str | None, request: Request) -> list[tuple[str, str]]:
    """The ``field.<key>`` filters. A core entity takes none, and a module
    takes nothing else. The names are checked against the fields later."""
    pairs = field_pairs(request.query_params)
    if module is None:
        if pairs:
            raise _core_pairs_refusal()
        return pairs
    # CRM-Q34-2: `q` passes here, and `_read` refuses it after it reads the
    # fields of the module, so its 422 names the fields that work instead.
    other = sorted({
        k for k in request.query_params
        if k not in MODULE_PARAMS and k != "q" and not k.startswith(FIELD_PREFIX)
    })
    if other:
        raise _refuse(
            f"A module aggregate takes field.<key> filters only, not {other}.",
        )
    return pairs


def _and(where: str, clause: str) -> str:
    """*where* with one more clause. Both are SQL of this package."""
    return f"{where} AND {clause}" if where else f" WHERE {clause}"


async def _labels(db: Any, entity: Entity, group_by: str) -> dict[str, str]:
    """Key → name for the status and pipeline groups."""
    if group_by == "status" and entity.status_table:
        rows = (await db.execute(
            text(f"SELECT id, name FROM {entity.status_table}"), {},
        )).fetchall()
        return {str(r.id): str(r.name) for r in rows}
    if group_by == "pipeline":
        rows = (await db.execute(
            # CRM-M4a: a deal names a deal pipeline only.
            text(f"SELECT id, name FROM crm_pipelines WHERE {DEAL_PIPELINE_SQL}"), {},
        )).fetchall()
        return {str(r.id): str(r.name) for r in rows}
    return {}


async def _status_order(db: Any, entity: Entity) -> dict[str, int]:
    if not entity.status_table:
        return {}
    rows = (await db.execute(
        text(f"SELECT id, position FROM {entity.status_table}"), {},
    )).fetchall()
    return {str(r.id): int(r.position or 0) for r in rows}


@dataclass
class _Acc:
    """One group as the statement gave it: the value, the field's sum and
    count of values (for an ``avg`` total), and a name."""

    value: float | None
    s: float = 0.0
    n: int = 0
    name: str | None = None
    #: The count of records in the group (``count(*)``).
    rows: int = 0
    #: The value in each currency that is not the primary one (task 888).
    others: dict[str, float] = dc_field(default_factory=dict)
    #: CRM-Q25e part 2: the group of each field of a split after the first.
    parts: list[_Acc] = dc_field(default_factory=list)
    #: NUM-2: the count of each status of :data:`COVERAGE_STATUSES` in the
    #: group. Empty when the statement counts no status.
    statuses: dict[str, int] = dc_field(default_factory=dict)
    #: CRM-Q34-10: the value as the row gave it, before any float: the
    #: ``numeric`` of the statement as a ``Decimal``. The cut of page 1
    #: rounds it (:func:`cents`) as SQL rounds it. None when the row gave
    #: no exact number, and then the cut reads ``value``.
    raw: Decimal | None = None


def _exact(value: Any) -> Decimal | None:
    """CRM-Q34-10: a value of a row as an exact ``Decimal``: a ``numeric``
    as it came, an integer (a count) as it is, anything else None."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return None


def _key(value: Any) -> str | None:
    return None if value is None else str(value)


async def _weighted(
    db: Any, key_sql: str, label_sql: str, where: str, params: dict[str, Any],
    group_clause: str = " GROUP BY 1", *, deadline: float | None = None,
) -> dict[str | None, _Acc]:
    """Weighted ₹ for each key, one statement per open or ongoing lane.
    With no group, *group_clause* is empty and each lane gives one row. A
    long read gives its one *deadline* (:func:`_keep_deadline`)."""
    lanes = (await db.execute(
        text("SELECT id, type, probability FROM crm_deal_statuses"), {},
    )).fetchall()
    out: dict[str | None, _Acc] = {}
    for lane in lanes:
        if getattr(lane, "type", "open") not in WEIGHTED_TYPES:
            continue
        await _keep_deadline(db, deadline)
        rows = (await db.execute(
            text(
                f"SELECT {key_sql} AS key, {label_sql} AS label, {WEIGHTED_SQL} "
                f"FROM crm_deals{_and(where, 'status_id = CAST(:agg_lane AS uuid)')}"
                f"{group_clause}"
            ),
            {
                **params,
                "agg_lane": str(lane.id),
                "stage_probability": int(getattr(lane, "probability", 0) or 0),
            },
        )).fetchall()
        for row in rows:
            acc = out.setdefault(_key(row.key), _Acc(value=0.0))
            acc.value = (acc.value or 0.0) + float(row.weighted or 0)
            acc.name = acc.name or getattr(row, "label", None)
    return out


async def _currencies_of(db: Any, where: str, params: dict[str, Any]) -> list[str]:
    """The currencies that the filtered deals carry, normalised, INR first.
    Read as rows and tallied here. Never empty: no deal reads as the default."""
    rows = (await db.execute(
        text(f"SELECT DISTINCT {CURRENCY_SQL} AS currency FROM crm_deals{where}"),
        params,
    )).fetchall()
    return (
        ordered_currencies(getattr(r, "currency", None) for r in rows)
        or ordered_currencies([None])
    )


def split_currencies(
    per: dict[str, dict[str | None, _Acc]],
) -> tuple[dict[str | None, _Acc], str, list[str]]:
    """One answer from the per-currency reads (task 888).

    *per* maps each currency to its groups, in the order of
    ``ordered_currencies`` (INR first, then code order), which is the order of
    ``reports.py``. The PRIMARY currency is the FIRST one, even when all its
    values are 0. The groups of the primary keep their value. Every other
    currency with a value other than 0 lands in ``acc.others``. The record
    counts of EVERY currency add up, so ``records`` stays the count of the
    matched deals. Pure, so the hermetic tests call it."""
    primary = next(iter(per))
    live = [
        c for c, groups in per.items()
        if c != primary and any((a.value or 0) != 0 for a in groups.values())
    ]
    merged = dict(per[primary])
    for code, groups in per.items():
        if code == primary:
            continue
        for key, acc in groups.items():
            mine = merged.setdefault(key, _Acc(value=0.0, name=acc.name))
            mine.rows += acc.rows
            mine.name = mine.name or acc.name
            if code in live and acc.value:
                mine.others[code] = float(acc.value)
    return merged, primary, live


def _combine(measure: str, accs: list[_Acc]) -> float | None:
    """The measure over *accs*: a sum, the mean of every value, or the least
    or greatest group value."""
    if measure in SUMMING_MEASURES:
        return sum(a.value or 0.0 for a in accs)
    if measure == "avg":
        n = sum(a.n for a in accs)
        return sum(a.s for a in accs) / n if n else None
    values = [a.value for a in accs if a.value is not None]
    if not values:
        return None
    return min(values) if measure == "min" else max(values)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


@dataclass
class _Read:
    """What the statements of one aggregate gave."""

    values: dict[str | None, _Acc]
    labels: dict[str, str]
    order: dict[str, int]
    measured: FieldRef | None
    grouped: FieldRef | None
    zone: str
    keys: dict[str, str | list[str]]
    #: CRM-Q20-3a: the overlay of the answer, and the lane type of each
    #: status group.
    overlay: dict[str, str] | None = None
    types: dict[str, str] = dc_field(default_factory=dict)
    #: §18.16 part 6: the stored values that each text filter counted.
    matched: dict[str, list[str]] = dc_field(default_factory=dict)
    #: Task 888: the currency of ``values`` for a money measure on deals, and
    #: the other currencies that the accs hold in ``others``.
    currency: str | None = None
    codes: list[str] = dc_field(default_factory=list)
    #: CRM-Q25e part 2: each field of a split, the first one ``measured``.
    split: list[FieldRef] = dc_field(default_factory=list)
    #: CRM-Q25e part 4: the date field of the check of one day, and its
    #: ``(day, value, records)`` rows from the same statement.
    day: FieldRef | None = None
    days: list[tuple[Any, Any, int]] = dc_field(default_factory=list)
    #: CRM-Q34-7: the stored names that the near pass of ``q`` counted, and
    #: the nearest stored names of a ``q`` that counted no record.
    near: list[str] | None = None
    nearest: list[str] | None = None
    #: CRM-Q34-11: each text field that the read took as a number, with its
    #: counts (``field_sql.admit_text_numbers``).
    text_numbers: dict[str, TextNumbers] = dc_field(default_factory=dict)
    #: CRM-Q34-5: each filter name that the read took as a filter word.
    filter_words: list[str] = dc_field(default_factory=list)
    #: NUM-2: the asked date range in whole months, and the groups of each
    #: period before it (-1 to -6) from the same statement.
    base: Baseline | None = None
    periods: dict[int, list[_Acc]] = dc_field(default_factory=dict)
    #: NUM-2: the statuses that the statement counted in each group.
    statuses: tuple[str, ...] = ()


def _is_timeout(exc: DBAPIError) -> bool:
    """True when the LOCAL ``statement_timeout`` cancelled the statement."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == _QUERY_CANCELED or "statement timeout" in str(exc)


def _split_columns(scope: FieldScope | None, split: list[FieldRef], measure: str,
                   bound: dict[str, Any]) -> tuple[str, str]:
    """CRM-Q25e part 2: the more columns of each field of a split after the
    first (``v1, s1, n1`` ...), and their FROM text. The SQL is the one of
    ``field_sql.split_sql``. Each key goes into *bound*."""
    more, source = "", ""
    if scope is None or len(split) < 2:
        return more, source
    for i, part in enumerate(split_sql(scope, split[1:], measure), start=1):
        more += f", {part.value} AS v{i}, {part.sum} AS s{i}, {part.count} AS n{i}"
        bound.update(part.params)
        source = from_source(source, part.source)
    return more, source


async def _keep_deadline(db: Any, deadline: float | None) -> None:
    """A long read has ONE deadline for all its statements (CRM-Q25e). Before
    each heavy statement, the LOCAL ``statement_timeout`` takes the time
    left, at least 1 ms. A normal read (*deadline* None) keeps the 10 s of
    each statement, as before."""
    if deadline is not None:
        left_ms = max(1, int((deadline - time.monotonic()) * 1000))
        await db.execute(text(_TIMEOUT_SQL), {"ms": str(left_ms)})


def _day_of(scope: FieldScope | None, module: str | None, grouped: FieldRef | None,
            pairs: list[tuple[str, str]], zone: str, bound: dict[str, Any], *,
            reads: bool) -> tuple[FieldRef | None, str, str]:
    """CRM-Q25e part 4: the date field of the check of one day, its day key
    and its FROM text (``field_sql.day_field`` and ``day_sql``), or no field.
    A core entity has none. *reads* says that the statement reads the
    detoasted copy already. With no such read (a plain count, a count by
    owner, a fixed month) the check adds no detoast, so it adds nothing."""
    day = day_field(scope, grouped, pairs) if module is not None and reads else None
    if day is None or scope is None:
        return None, "", ""
    sql = day_sql(scope, day, zone=zone)
    bound.update(sql.params)
    return day, sql.key, sql.source


def number_sense_on() -> bool:
    """NUM-2 ships dark (CLAUDE.md §4, "Ship dark"). The baseline, the
    coverage and the ``checks`` come only for an org that the answer-first
    flag of CRM-Q33 serves (``flags.answer_first_allowed``: the questions, the
    catalogue and ``CRM_ANSWER_FIRST_ORGS``). The org is the bound tenant,
    never a request field (R5). An org outside it gets the statement and the
    answer of before NUM-2, byte for byte."""
    return answer_first_allowed(str(current_tenant() or ""))


async def _module_size(db: Any, module: str) -> int | None:
    """NUM-2: the records of *module* as the last fill step counted them
    (``crm_field_fill.total``, the same number for each key), or None with
    no fill row. One read of a few rows by the key of the table, never a
    scan of the module. The org is the bound tenant (R5), and RLS holds the
    session too."""
    row = (await db.execute(text(
        "SELECT max(total) AS n FROM crm_field_fill "
        "WHERE organization_id = CAST(:agf_org AS uuid) AND entity = :agf_entity"),
        {"agf_org": str(current_tenant()), "agf_entity": f"{MODULE_PREFIX}{module}"},
    )).first()
    n = getattr(row, "n", None) if row is not None else None
    return None if n is None else int(n)


async def _sense_columns(
    db: Any, scope: FieldScope | None, module: str | None, day: FieldRef | None,
    day_key: str, pairs: list[tuple[str, str]], bound: dict[str, Any], *, reads: bool,
    sense: bool, nodes: tuple[QueryNode, ...] | None = None, words: bool = False,
) -> tuple[Baseline | None, str, tuple[str, ...], str]:
    """NUM-2: the baseline, its period SQL, the statuses that the statement
    counts, and their columns. Each bound value goes into *bound*. *sense*
    is the ONE read of :func:`number_sense_on` for the request.

    The baseline: a module answer over 1 to 3 whole months of its day field
    reads the 6 periods before it in the SAME scan. Only the start bound
    widens, and each row names its period. The wider window groups more
    rows, so a module with more than :data:`BASELINE_MAX_RECORDS` records,
    or with no size in ``crm_field_fill``, gets no baseline.

    The statuses: one count for each status that a total usually leaves
    out, as more columns of the same statement. Only when the statement
    reads the detoasted copy already (*reads*), as the day set."""
    if not sense:
        return None, "", (), ""  # dark: the statement is the one of before NUM-2
    base = (baseline_range(scope, day, pairs, nodes or (), words=words)
            if module is not None and day_key else None)
    if base is not None and module is not None:
        size = await _module_size(db, module)
        if size is None or size > BASELINE_MAX_RECORDS:
            base = None  # too large, or no size: the window stays as asked
    if base is not None and not widen(bound, base):
        base = None  # the start is not bound as asked, so the window stays
    period = ""
    if base is not None:
        period, period_params = period_sql(day_key, base)
        bound.update(period_params)
    statuses: tuple[str, ...] = ()
    counted = ""
    if module is not None and reads:
        columns, status_params = status_sql(scope, COVERAGE_STATUSES)
        if columns:
            statuses = tuple(COVERAGE_STATUSES)
            bound.update(status_params)
            counted = "".join(f", {column} AS agf_s{i}" for i, column in enumerate(columns))
    return base, period, statuses, counted


def _acc_of(r: Any, fields: int, statuses: tuple[str, ...] = ()) -> _Acc:
    """One group of a row, with the part of each other field of a split, and
    the count of each of *statuses* (NUM-2)."""
    acc = _Acc(
        value=None if r.value is None else float(r.value),
        s=float(r.s or 0), n=int(r.n or 0), name=r.label,
        rows=int(getattr(r, "c", 0) or 0), raw=_exact(r.value),
    )
    for i in range(1, fields):
        v = getattr(r, f"v{i}", None)
        acc.parts.append(_Acc(value=None if v is None else float(v),
                              s=float(getattr(r, f"s{i}", 0) or 0),
                              n=int(getattr(r, f"n{i}", 0) or 0)))
    for i, status in enumerate(statuses):
        acc.statuses[status] = int(getattr(r, f"agf_s{i}", 0) or 0)
    return acc


def _rows_of(rows: Any, fields: int, days: list[tuple[Any, Any, int]],
             periods: dict[int, list[_Acc]] | None = None,
             statuses: tuple[str, ...] = ()) -> dict[str | None, _Acc]:
    """The groups of the rows of one statement. A row of the day set
    (``GROUPING()`` is 0 there) goes to *days* as ``(day, value, records)``.
    NUM-2: with a baseline, each row names its period in ``agf_p``. Only the
    rows of period 0, the asked range, are the answer and its days. A group
    of an earlier period goes to *periods*, and its days are dropped."""
    out: dict[str | None, _Acc] = {}
    for r in rows:
        period = getattr(r, "agf_p", None)
        before = period is not None and int(period) != 0
        if int(getattr(r, "agf_gd", 1) or 0) == 0:
            if not before:
                days.append((r.agf_day, r.value, int(r.c or 0)))
        elif before and periods is not None:
            periods.setdefault(int(period), []).append(_acc_of(r, fields, statuses))
        elif not before:
            out[_key(r.key)] = _acc_of(r, fields, statuses)
    return out


async def _read(
    db: Any, found: Entity | None, module: str | None, *,
    measure: str, group_by: str | None, field: str | None,
    group_field: str | None, bucket: str | None,
    pairs: list[tuple[str, str]], params: ListParams, user: UserContext | None,
    fields: str | None = None, timeout_ms: int | None = None,
    status_group: bool = False, nodes: tuple[QueryNode, ...] | None = None,
    sense: bool = False,
) -> _Read:
    """Every statement of one aggregate, in the caller's tenant session.
    *fields* is the comma list of a split (CRM-Q25e part 2), and
    *timeout_ms* the budget of a long read: ONE deadline for all its
    statements (:func:`_keep_deadline`). *nodes* are the checked ``query``
    of a module (SEM-1b-1), or None. *sense* is the one read of NUM-2's
    flag for the request (:func:`number_sense_on`)."""
    ms = STATEMENT_TIMEOUT_MS if timeout_ms is None else timeout_ms
    deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000
    await db.execute(text(_TIMEOUT_SQL), {"ms": str(ms)})
    scope: FieldScope | None = None
    zone = ""
    field_measure = measure
    text_numbers: dict[str, TextNumbers] = {}
    filter_words: list[str] = []
    if module is not None:
        mod = await _module_filters(
            db, module, measure=measure, field=field, pairs=pairs, params=params,
            status_group=status_group, fields=fields, nodes=nodes)
        scope, field_measure, field, zone = mod.scope, mod.field_measure, mod.field, mod.zone
        matched, text_numbers, words = mod.matched, mod.text_numbers, mod.words
        ranges, filter_words, finding = mod.ranges, mod.filter_words, mod.finding
        table, where_sql, bound = MODULE_TABLE, mod.where_sql, mod.bound
        source = mod.source
        # CRM-Q34-7: the near pass reads under the filters of the call, with no q.
        plain = (where_sql, dict(bound))
        where_sql, source = _with_words(scope, words, where_sql, bound, source)
    else:
        assert found is not None
        matched, words, plain, ranges = {}, [], ("", {}), pairs
        finding = False
        if group_by == "created_month" and await is_mirror_mode(db, current_tenant()):
            # The created date of a mirrored row is the import time (Q8).
            raise HTTPException(status_code=422, detail={
                "code": "created_range_mirror",
                "message": "The created date of a synced record is not kept. "
                           "Group by the updated month instead.",
            })
        query = await list_query(db, found, params, user)
        table, where_sql, bound = found.table, query.where, dict(query.params)
        source = ""
        if field or group_field or fields:
            scope = await core_scope(db, found.slug)
        zone = await crm_zone(db, current_tenant())
    # CRM-Q25e part 2: a split reads its first field as `field`, and the
    # rest as more columns of the same statement.
    split = check_split(scope, fields) if fields and scope else []
    if split:
        field = split[0].key
    measured = (split[0] if split else check_measure(scope, field)) if field and scope else None
    grouped = check_group(scope, group_field, bucket) if group_field and scope else None
    # No group: one row over every filtered record. The statement then has
    # no GROUP BY, so an empty set still gives its one row (a count of 0).
    group_clause = " GROUP BY 1"
    if grouped is not None and scope is not None:
        group = group_sql(scope, grouped, bucket, zone=zone)
        key_sql, label_sql = group.key, group.label
        bound.update(group.params)
        # Task 3127: a module group reads the one detoasted copy too.
        source = from_source(source, group.source)
    elif group_by is not None:
        key_sql = (MODULE_GROUPS if module is not None else GROUPS)[group_by]
        label_sql = "NULL"
    else:
        key_sql, label_sql, group_clause = "NULL", "NULL", ""
    # The fixed month groups read the zone too. A bound value, never text.
    bound[ZONE_PARAM] = zone
    money = module is None and found is not None and measure in DEAL_MEASURES
    codes: list[str] = []
    currency: str | None = None
    value_sql, sum_sql, count_sql = MEASURES.get(measure, ""), "0", "0"
    if measured is not None and scope is not None:
        sql = measure_sql(scope, measured, field_measure)
        value_sql, sum_sql, count_sql = sql.value, sql.sum, sql.count
        bound.update(sql.params)
        source = from_source(source, sql.source)
    more, more_source = _split_columns(scope, split, field_measure, bound)
    source = from_source(source, more_source)
    # CRM-Q25e part 4: the days of one date field come from the SAME scan, as
    # a second grouping set. A module only, never a money read of deals, and
    # only when the statement reads the detoasted copy already: a count with
    # no field read never pays for a detoast of each row.
    day, day_key, day_source = _day_of(scope, module, grouped, ranges, zone, bound,
                                       reads=bool(source))
    source = from_source(source, day_source)
    days: list[tuple[Any, Any, int]] = []
    base, period, statuses, counted = await _sense_columns(
        db, scope, module, day, day_key, pairs, bound, reads=bool(source), sense=sense,
        nodes=nodes, words=finding)
    more += counted  # the status counts are more columns of the ONE statement
    periods: dict[int, list[_Acc]] = {}
    sets, grouping = _grouping_sql(key_sql, group_clause, day_key, period)

    async def fetch(where_sql: str, args: dict[str, Any]) -> dict[str | None, _Acc]:
        await _keep_deadline(db, deadline)
        rows = (await db.execute(
            text(
                f"SELECT {key_sql} AS key, {label_sql} AS label, "
                f"{value_sql} AS value, {sum_sql} AS s, {count_sql} AS n, "
                f"count(*) AS c{more}{grouping} FROM {table}{source}{where_sql}{sets}"
            ),
            args,
        )).fetchall()
        return _rows_of(rows, len(split), days, periods, statuses)

    if money:
        # Task 888: one read for each currency. Never one sum over all of them.
        per: dict[str, dict[str | None, _Acc]] = {}
        for code in await _currencies_of(db, where_sql, bound):
            scoped = _and(where_sql, f"{CURRENCY_SQL} = :agg_currency")
            args = {**bound, "agg_currency": code}
            per[code] = (
                await _weighted(db, key_sql, label_sql, scoped, args, group_clause,
                                deadline=deadline)
                if measure == "weighted" else await fetch(scoped, args)
            )
        values, currency, codes = split_currencies(per)
    elif measure == "weighted":
        values = await _weighted(db, key_sql, label_sql, where_sql, bound, group_clause,
                                 deadline=deadline)
    else:
        values = await fetch(where_sql, bound)
    near: list[str] | None = None
    nearest: list[str] | None = None
    if words and scope is not None and not sum(acc.rows for acc in values.values()):
        kept = _set_aside(periods, days)
        values, near, nearest = await _near_pass(db, scope, words, plain, bound, fetch,
                                                  values)
        _take_back(kept, near, periods, days)
    labels = await _labels(db, found, group_by) if found and group_by else {}
    order = (
        await _status_order(db, found) if found and group_by == "status" else {}
    )
    keys = (
        used_keys(scope, [field, group_field, *(r.key for r in split[1:]),
                          *(name for name, _value in pairs),
                          *(leaf.key for node in nodes or () for leaf in node)])
        if module is not None and scope is not None else {}
    )
    return _Read(values, labels, order, measured, grouped, zone, keys, matched=matched,
                 currency=currency, codes=codes, split=split, day=day, days=days,
                 near=near, nearest=nearest, text_numbers=text_numbers,
                 filter_words=filter_words, base=base, periods=periods,
                 statuses=statuses)


async def _with_query(
    db: Any, scope: FieldScope, module: str, nodes: tuple[QueryNode, ...] | None,
    zone: str, where_sql: str, bound: dict[str, Any], source: str,
    matched: dict[str, list[str]],
) -> tuple[str, str]:
    """SEM-1b-1: *where_sql* AND the clause of the checked ``query``
    (``field_sql.query_where``), with its keys in *bound* and its counted
    variants in *matched*, and the FROM text. With no query, both stay as
    they are, so an answer with no query does not change."""
    if not nodes:
        return where_sql, source
    found = await query_where(db, str(current_tenant()), scope, module, nodes, zone=zone)
    bound.update(found.where.params)
    for key, values in found.matched.items():
        matched.setdefault(key, values)
    # SEM-1b-2: an alias set of a text leaf is the org's fact, as for a pair.
    await _seed_aliases(db, module, found.pairs, found.matches)
    return f"{where_sql} AND {found.where.sql}", from_source(source, found.where.source)


def _with_words(scope: FieldScope, words: list[str], where_sql: str,
                bound: dict[str, Any], source: str) -> tuple[str, str]:
    """CRM-Q34-7: *where_sql* AND the predicate of ``q`` across the module
    (``field_sql.find_where``), with its keys in *bound*, and the FROM text.
    With no word, both stay as they are."""
    if not words:
        return where_sql, source
    found = find_where(scope, words)
    bound.update(found.params)
    return f"{where_sql} AND {found.sql}", from_source(source, found.source)


def _grouping_sql(key_sql: str, group_clause: str, day_key: str,
                  period: str) -> tuple[str, str]:
    """The GROUP BY text and the more columns of the day set (CRM-Q25e part
    4) and of the period of a baseline (NUM-2).

    One scan, two groupings: the answer's own, and the day. With no group,
    the empty set gives its one row even when no record matches, as the
    plain statement does. With a baseline, both sets also group by the
    period, and only period 0 is the answer (:func:`_rows_of`). With no
    day key, the text is *group_clause*, as before."""
    if not day_key:
        return group_clause, ""
    lead = f"{period}, " if period else ""
    answer_set = (f"({lead}{key_sql})" if group_clause
                  else f"({period})" if period else "()")
    grouping = f", {day_key} AS agf_day, GROUPING({day_key}) AS agf_gd"
    if period:
        grouping += f", {period} AS agf_p"
    return f" GROUP BY GROUPING SETS ({answer_set}, ({lead}{day_key}))", grouping


def _set_aside(periods: dict[int, list[_Acc]], days: list[tuple[Any, Any, int]],
               ) -> tuple[dict[int, list[_Acc]], list[tuple[Any, Any, int]]]:
    """NUM-2: the baseline periods and the days of the exact pass of ``q``,
    set aside before the near pass fills both again. Both start empty."""
    kept = (dict(periods), list(days))
    periods.clear()
    days.clear()
    return kept


def _take_back(kept: tuple[dict[int, list[_Acc]], list[tuple[Any, Any, int]]],
               near: list[str] | None, periods: dict[int, list[_Acc]],
               days: list[tuple[Any, Any, int]]) -> None:
    """NUM-2: the baseline and the days are of the pass whose groups the
    answer keeps. With no *near* name, the answer keeps the exact pass, so
    its own periods and days come back (:func:`_set_aside`)."""
    if near is None:
        periods.clear()
        periods.update(kept[0])
        days[:] = kept[1]


async def _near_pass(
    db: Any, scope: FieldScope, words: list[str], plain: tuple[str, dict[str, Any]],
    bound: dict[str, Any], fetch: Any, exact: dict[str | None, _Acc],
) -> tuple[dict[str | None, _Acc], list[str] | None, list[str] | None]:
    """CRM-Q34-7: no row holds each word of ``q``, so the near pass of
    ``name_match`` (``field_sql.find_near``) reads under *plain*, the filters
    with no ``q``. With near names, ONE more read of the same statement
    counts their rows. The groups, the near names counted, and the nearest
    names when still no row matches. *exact* is the answer of the exact
    pass, which counted no row."""
    hit = await find_near(db, scope, words, *plain)
    if hit.near:
        again = find_where(scope, words, hit.near)
        values = await fetch(f"{plain[0]} AND {again.sql}", {**bound, **again.params})
        if sum(acc.rows for acc in values.values()):
            return values, hit.names(), None
    return exact, None, list(hit.nearest)


@dataclass
class _ModuleRead:
    """What :func:`_module_filters` gives: the scope, the field measure and
    field, the zone, the WHERE with its bound values and FROM text, the
    matched text values, the text fields read as numbers, the words of
    ``q``, the ranges of the pairs and the query, and the filter words."""

    scope: FieldScope
    field_measure: str
    field: str | None
    zone: str
    where_sql: str
    bound: dict[str, Any]
    source: str
    matched: dict[str, list[str]]
    text_numbers: dict[str, TextNumbers]
    words: list[str]
    ranges: list[tuple[str, str]]
    filter_words: list[str]
    #: CRM-Q34-5: the flag of the filter words, as ``module_where`` read it.
    #: NUM-2 parses the pairs with the same flag (``baseline_range``).
    finding: bool = False


async def _module_filters(
    db: Any, module: str, *, measure: str, field: str | None,
    pairs: list[tuple[str, str]], params: ListParams, status_group: bool,
    fields: str | None = None, nodes: tuple[QueryNode, ...] | None = None,
) -> _ModuleRead:
    """The steps of ONE module read. The one place that builds the filters
    of a module total, so :func:`_read` and :func:`_read_pages` count the
    same records (CRM-Q34-10): the pairs with their filter words (CRM-Q34-5)
    and the query (SEM-1b). The caller adds the words of ``q``
    (:func:`_with_words`)."""
    _require_module(await mirrored_modules(db), module)
    scope = await module_scope(db, module)
    # CRM-Q34-2: `q` on a module is a 422 that names the fields to use.
    # CRM-Q34-7: an org on CRM_MODULE_FIND_ORGS finds `q` across the module.
    finding = module_find_allowed(current_tenant())
    if params.q is not None and not finding:
        raise q_refusal(scope)
    words = find_words(params.q if finding else None)
    # CRM-Q34-2: `group_by=status` reads `@status`, or names the selects.
    if status_group:
        status_field(scope)
    field_measure, field = module_measure(measure, field)
    # SEM-1b-2: each range of `query` in the pair form, so the pair
    # readers below (the text numbers, the day check) read it too.
    queried = query_ranges(scope, nodes)
    ranges = [*pairs, *queried]
    # CRM-Q34-11: a measure or a range on a text field that holds
    # numbers reads them. With no text field named, no statement runs.
    # A key that only the query names gets the words of a query leaf.
    scope, text_numbers = await admit_text_numbers(
        db, scope, text_number_keys(field_measure, field, fields, ranges),
        measured=text_number_keys(field_measure, field, fields, []),
        queried=set(text_number_keys("count", None, None, queried))
        - set(text_number_keys("count", None, None, pairs)))
    # CRM-Q25b: one zone for each bucket and each range of the answer.
    zone = await crm_zone(db, current_tenant())
    # CRM-Q34-5: an org on CRM_MODULE_FIND_ORGS takes the filter words.
    # The probe holds the pairs that the two reads below take. Without
    # the words, the probe IS the pairs.
    probe, owners = filter_probe(scope, pairs, finding)
    filter_words = word_names(scope, pairs, finding)
    # §18.16 part 6: a text filter counts each case and short form.
    matches = await text_matches(db, str(current_tenant()), scope, module, probe)
    # Task 3128: a lookup filter by a Metorite id also matches its Zoho id.
    ids = await lookup_ids(db, str(current_tenant()), scope, probe)
    where = module_where(scope, module, pairs, zone=zone, matches=matches, ids=ids,
                         words=finding)
    where_sql, bound, source = where.sql, dict(where.params), where.source
    matched = matched_values(pairs, owners, matches)
    await _seed_aliases(db, module, probe, matches)
    # SEM-1b-1: the query joins the old filters by AND.
    where_sql, source = await _with_query(db, scope, module, nodes, zone, where_sql,
                                          bound, source, matched)
    return _ModuleRead(scope, field_measure, field, zone, where_sql, bound, source, matched,
                       text_numbers, words, ranges, filter_words, finding)


# ── Two group fields, and pages of groups (CRM-Q34-10) ─────────────────────

#: The measure over every group, from the column ``value`` of each group.
#: ``avg`` reads ``s`` and ``n`` instead. Each value is a literal.
_WHOLE: dict[str, str] = {"min": "min(value)", "max": "max(value)"}

#: The order of the groups of a page of TWO group fields: the largest value
#: first, then each key. (key, key2) is unique for each group, so the order
#: is TOTAL, and two pages never share a group or skip one.
GROUP_ORDER = "value DESC NULLS LAST, key ASC NULLS LAST, key2 ASC NULLS LAST"

#: A later page of ONE group field takes ``field_sql.one_group_order``: the
#: order in which :func:`_compute` keeps its 50 largest groups.


@dataclass
class _Pages:
    """One page of a paged read, and the measure over every group."""

    rows: list[Any]
    whole: Any
    measured: FieldRef | None
    grouped: FieldRef
    grouped2: FieldRef | None
    zone: str
    keys: dict[str, str | list[str]]
    matched: dict[str, list[str]]
    text_numbers: dict[str, TextNumbers] = dc_field(default_factory=dict)
    #: CRM-Q34-5: the filter names that the route read as filter words.
    filter_words: list[str] = dc_field(default_factory=list)


def paged_sql(inner: str, whole: str, order: str = GROUP_ORDER) -> str:
    """ONE statement over the groups of *inner*: a first row (``agf_pos`` 0)
    with the measure over every group and the count of groups, then the rows
    of one page (``agf_pos`` from ``:agf_lo`` + 1 to ``:agf_hi``). The page is
    cut in SQL, so the answer never holds every group. *inner* is SQL of this
    module, *whole* a value of :data:`_WHOLE`, *order* :data:`GROUP_ORDER`
    or ``field_sql.one_group_order``, and the page bounds are bound
    values."""
    cols = "key, label, key2, label2, value, s, n, c"
    return (
        f"WITH agf_groups AS MATERIALIZED ({inner}) "
        "SELECT 0 AS agf_pos, NULL AS key, NULL AS label, NULL AS key2, NULL AS label2, "
        f"{whole} AS value, sum(s) AS s, sum(n) AS n, sum(c) AS c, "
        "count(*) AS agf_count FROM agf_groups "
        f"UNION ALL SELECT * FROM (SELECT row_number() OVER (ORDER BY {order}) "
        f"AS agf_pos, {cols}, 0 AS agf_count FROM agf_groups) agf_page "
        "WHERE agf_pos > :agf_lo AND agf_pos <= :agf_hi"
    )


async def _read_pages(
    db: Any, module: str, *, measure: str, field: str | None, group_field: str,
    group_field2: str | None, bucket: str | None, pairs: list[tuple[str, str]],
    params: ListParams, page: int, size: int, timeout_ms: int | None,
    status_group: bool, nodes: tuple[QueryNode, ...] | None = None,
) -> _Pages | None:
    """One page of the groups of a module total, by one or two fields, in
    the caller's tenant session (CRM-Q34-10). The filters are the ones of
    :func:`_read` (:func:`_module_filters`), and every key is bound."""
    ms = STATEMENT_TIMEOUT_MS if timeout_ms is None else timeout_ms
    deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000
    await db.execute(text(_TIMEOUT_SQL), {"ms": str(ms)})
    mod = await _module_filters(
        db, module, measure=measure, field=field, pairs=pairs, params=params,
        status_group=status_group, nodes=nodes)
    scope, field_measure, field, zone = mod.scope, mod.field_measure, mod.field, mod.zone
    matched, text_numbers = mod.matched, mod.text_numbers
    measured = check_measure(scope, field) if field else None
    if group_field2 is None:
        grouped, grouped2 = check_group(scope, group_field, bucket), None
        if grouped.type in DATE_TYPES:
            # A date group of one field keeps time order, so no later page
            # can continue it: the caller gives the answer of one page.
            return None
    else:
        grouped, grouped2 = check_groups(scope, group_field, group_field2, bucket)
    bound = dict(mod.bound)
    # CRM-Q34-7: `q` filters by its exact words. A paged answer runs no near
    # pass, so a `q` that counts nothing gives no group.
    where_sql, where_source = _with_words(scope, mod.words, mod.where_sql, bound,
                                          mod.source)
    first = group_sql(scope, grouped, bucket if grouped.type in DATE_TYPES else None,
                      zone=zone)
    bound.update(first.params)
    sources = [where_source, first.source]
    key2_sql, label2_sql, group_by_sql = "NULL", "NULL", " GROUP BY 1"
    if grouped2 is not None:
        second = group_sql(scope, grouped2,
                           bucket if grouped2.type in DATE_TYPES else None,
                           zone=zone, prefix=GROUP2_KEY)
        key2_sql, label2_sql, group_by_sql = second.key, second.label, " GROUP BY 1, 3"
        bound.update(second.params)
        sources.append(second.source)
    value_sql, sum_sql, count_sql = MEASURES["count"], "0", "0"
    if measured is not None:
        sql = measure_sql(scope, measured, field_measure)
        value_sql, sum_sql, count_sql = sql.value, sql.sum, sql.count
        bound.update(sql.params)
        sources.append(sql.source)
    bound[ZONE_PARAM] = zone
    bound.update(agf_lo=(page - 1) * size, agf_hi=page * size)
    inner = (
        f"SELECT {first.key} AS key, {first.label} AS label, {key2_sql} AS key2, "
        f"{label2_sql} AS label2, {value_sql} AS value, {sum_sql} AS s, "
        f"{count_sql} AS n, count(*) AS c "
        f"FROM {MODULE_TABLE}{from_source(*sources)}{where_sql}{group_by_sql}"
    )
    whole = _WHOLE.get(field_measure, "sum(value)")
    order = GROUP_ORDER
    if grouped2 is None:
        order, order_params = one_group_order(grouped)
        bound.update(order_params)
    await _keep_deadline(db, deadline)
    rows = (await db.execute(
        text(paged_sql(inner, whole, order)), bound,
    )).fetchall()
    head = next(r for r in rows if int(r.agf_pos) == 0)
    page_rows = sorted((r for r in rows if int(r.agf_pos) > 0), key=lambda r: int(r.agf_pos))
    keys = used_keys(scope, [field, group_field, group_field2,
                             *(name for name, _value in pairs),
                             *(leaf.key for node in nodes or () for leaf in node)])
    return _Pages(page_rows, head, measured, grouped, grouped2, zone, keys, matched,
                  text_numbers, mod.filter_words)


def _paging(
    module: str | None, group_field: str | None, fields: str | None,
    group_field2: str | None, group_page: int | None, group_page_size: int | None,
    *, status_group: bool = False, q: str | None = None,
) -> tuple[int, int] | None:
    """CRM-Q34-10: the page and its size for a paged answer, None for the
    answer of today, or a 422.

    ``group_field2`` is new, so a core entity and an org off
    ``CRM_MODULE_FIND_ORGS`` refuse it. On the list, a call with it is paged.
    With no ``group_field2``, the paged answer serves ONLY what the answer
    of today cannot: a page past 1 of one field of a module. A split, a
    ``group_by`` key word and ``q`` (its near pass) keep the answer of today,
    and so does a date group (:func:`_read_pages`). Off the list and on a
    core entity, the page parameters are ignored."""
    if group_field2 is None and group_page is None and group_page_size is None:
        return None
    if group_field2 is not None and module is None:
        raise _refuse("group_field2 takes a module entity: 'module:<ApiName>'.")
    on = module is not None and module_find_allowed(current_tenant())
    if group_field2 is not None and not on:
        raise _refuse(TWO_GROUP_OFF)
    if not on:
        return None
    if group_field2 is None and (
            (group_page or 1) <= 1 or group_field is None or fields or status_group
            or q is not None):
        return None
    if group_field is None:
        raise _refuse("group_field2 goes with group_field.")
    if fields:
        raise _refuse("A split takes one group field. Give field=<key>.")
    page = 1 if group_page is None else group_page
    if not 1 <= page <= GROUP_PAGES_MAX:
        raise _refuse(f"group_page is 1 to {GROUP_PAGES_MAX:,}.")
    size = MAX_GROUPS if group_page_size is None else group_page_size
    if group_field2 is None and size != MAX_GROUPS:
        # Page 1 of one field cuts 50 groups, so each later page holds 50.
        raise _refuse(f"One group field pages by {MAX_GROUPS} groups. Give no "
                      "group_page_size, or give group_field2.")
    if not 1 <= size <= GROUP_PAGE_MAX:
        raise _refuse(f"group_page_size is 1 to {GROUP_PAGE_MAX}.")
    return page, size


async def _compute_pages(
    module: str, *, measure: str, field: str | None, group_field: str,
    group_field2: str | None, bucket: str | None, pairs: list[tuple[str, str]],
    params: ListParams, paging: tuple[int, int], timeout_ms: int | None,
    timed_out: dict[str, Any], status_group: bool,
    nodes: tuple[QueryNode, ...] | None = None,
) -> AggregateResponse | None:
    """The paged answer of :func:`_compute` (CRM-Q34-10), or None for a date
    group of one field, which takes the answer of one page. A field that is
    not on the module names the modules that hold it (task 3690), and a
    statement past the timeout is the 503 of today."""
    try:
        async with _tenant_session() as db:
            try:
                got = await _read_pages(
                    db, module, measure=measure, field=field, group_field=group_field,
                    group_field2=group_field2, bucket=bucket, pairs=pairs,
                    params=params, page=paging[0], size=paging[1],
                    timeout_ms=timeout_ms, status_group=status_group, nodes=nodes,
                )
            except HTTPException as exc:
                raise await field_hint(db, module, exc) from None
    except DBAPIError as exc:
        if _is_timeout(exc):
            raise HTTPException(status_code=503, detail=timed_out) from None
        raise
    if got is None:
        return None
    return _paged_answer(f"{MODULE_PREFIX}{module}", measure, group_field, bucket,
                         got, *paging)


def _paged_answer(
    entity: str, measure: str, group_field: str, bucket: str | None, got: _Pages,
    page: int, size: int,
) -> AggregateResponse:
    """The answer of one page (CRM-Q34-10). A group is named as in the answer
    of today: the lookup name, the option label, the key, or "(none)".
    ``omitted`` counts the groups that are not on this page. ``other`` is
    None: the read does not measure them apart."""
    measured, grouped, grouped2 = got.measured, got.grouped, got.grouped2
    head = got.whole
    values = int(head.n or 0) if measured is not None else None
    count = int(head.agf_count or 0)

    def named(key: Any, name: Any, ref: FieldRef | None) -> str:
        options = ref.options if ref is not None else {}
        return name or options.get(key or "") or key or "(none)"

    groups = [
        AggregateGroup(
            key=_key(r.key), label=named(_key(r.key), r.label, grouped),
            value=None if values == 0 or r.value is None else _rounded(float(r.value)),
            key2=_key(r.key2) if grouped2 is not None else None,
            label2=named(_key(r.key2), r.label2, grouped2) if grouped2 is not None else None,
        )
        for r in got.rows
    ]
    if values == 0:
        total = None
    elif measure == "avg":
        n = int(head.n or 0)
        total = _rounded(float(head.s or 0) / n) if n else None
    else:
        total = None if head.value is None else _rounded(float(head.value))
        if total is None and measure in SUMMING_MEASURES:
            total = 0.0
    dated = (grouped.type in DATE_TYPES
             or (grouped2 is not None and grouped2.type in DATE_TYPES))
    return AggregateResponse(
        entity=entity, measure=measure, group_by=None, group_field=group_field,
        field=measured.key if measured is not None else None,
        field_type=measured.type if measured is not None else None,
        field_label=measured.label if measured is not None else None,
        group_field_label=grouped.label, keys=got.keys,
        group_field_type=grouped.type, bucket=bucket if dated else None,
        groups=groups, total=total, other=None,
        omitted=max(0, count - len(groups)), zone=got.zone, values=values,
        records=int(head.c or 0), matched=got.matched or None,
        group_field2=grouped2.key if grouped2 is not None else None,
        group_field2_label=grouped2.label if grouped2 is not None else None,
        group_field2_type=grouped2.type if grouped2 is not None else None,
        group_count=count, group_page=page, group_pages=-(-count // size),
        text_numbers={key: found.wire() for key, found in got.text_numbers.items()} or None,
        filter_words=got.filter_words or None,
    )


async def _seed_aliases(db: Any, module: str, pairs: list[tuple[str, str]],
                        matches: dict[int, list[str]]) -> None:
    """Keep each alias set of two or more values as the org's value-alias
    fact (WS-53 §18.16 part 6), in ``zoho:questions`` under ``aliases``, so
    ``org_facts`` names it. Only for an org on ``CRM_QUESTIONS_ORGS``. A
    savepoint holds the write, so a fault never fails the total."""
    from gateway.crm_sync import runs
    from gateway.crm_sync.questions import QUESTIONS_ROW
    from gateway.crm_sync.terms import ALIASES_BUCKET
    from gateway.routes.crm.flags import questions_allowed

    org = str(current_tenant() or "")
    found = {i: v for i, v in matches.items() if len(v) > 1}
    if not org or not found or not questions_allowed(org):
        return
    try:
        async with db.begin_nested():
            for index, values in found.items():
                name = f"{module}.{pairs[index][0]}.{values[0]}"
                await runs.merge_cursor_entry(db, org, QUESTIONS_ROW, ALIASES_BUCKET, name,
                                              values)
    except Exception as exc:  # the fact is advice: the total stands
        _log.warning("crm.aggregate.alias_seed_failed", error_type=type(exc).__name__)


#: The groups that an overlay has no source for.
_UNFIT_GROUPS = frozenset({"pipeline", "source"})


def _fold(measure: str, accs: list[_Acc]) -> _Acc:
    """One group of *accs*: the Unsorted lane of an overlay."""
    return _Acc(value=_combine(measure, accs), s=sum(a.s for a in accs),
                n=sum(a.n for a in accs), rows=sum(a.rows for a in accs))


async def _read_overlay(
    db: Any, found: Entity, overlay: Overlay, *,
    measure: str, group_by: str | None, field: str | None,
    group_field: str | None, bucket: str | None,
    pairs: list[tuple[str, str]], params: ListParams, user: UserContext,
    timeout_ms: int | None = None,
) -> _Read:
    """Every statement of one aggregate of an overlaid core entity, in the
    caller's tenant session (§18.13.5, the projection table)."""
    overlay_filters(found.label, overlay, params)
    if group_by in _UNFIT_GROUPS:
        raise overlay_refusal(
            "overlay_unfit", f"{found.label}s come from the module {overlay.module}, "
            f"which has no {group_by}. Group by another key.")
    ms = STATEMENT_TIMEOUT_MS if timeout_ms is None else timeout_ms
    deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000
    await db.execute(text(_TIMEOUT_SQL), {"ms": str(ms)})
    scope = await module_scope(db, overlay.module)
    zone = await crm_zone(db, current_tenant())
    # Task 3128: a lookup filter by a Metorite id also matches its Zoho id.
    ids = await lookup_ids(db, str(current_tenant()), scope, pairs)
    where = overlay_where(scope, overlay, pairs, zone=zone,
                          owner=resolve_owner(params.owner, user), params=params, ids=ids)
    bound = dict(where.params)
    measured: FieldRef | None = None
    value_sql, sum_sql, count_sql = MEASURES["count"], "0", "0"
    if measure == "sum_amount":
        measured = overlay_amount(scope, overlay)
        sql = measure_sql(scope, measured, "sum")
    elif measure == "weighted":
        sql = overlay_weighted(
            scope, overlay_amount(scope, overlay), overlay_probability(scope, overlay),
            overlay_stage(scope, overlay),
            sorted(v for v, word in overlay.values.items() if word == "open"))
    elif measure in FIELD_MEASURES:
        measured = check_measure(scope, field)
        sql = measure_sql(scope, measured, measure)
    else:
        sql = None
    source = where.source
    if sql is not None:
        value_sql, sum_sql, count_sql = sql.value, sql.sum, sql.count
        bound.update(sql.params)
        source = from_source(source, sql.source)
    grouped: FieldRef | None = None
    stage: FieldRef | None = None
    label_sql = "NULL"
    if group_field:
        grouped = check_group(scope, group_field, bucket)
        group = group_sql(scope, grouped, bucket, zone=zone)
    elif group_by == "status":
        stage = overlay_stage(scope, overlay)
        group = group_sql(scope, stage, None, zone=zone)
    elif group_by == "close_month":
        group = group_sql(scope, overlay_close(scope, overlay), "month", zone=zone)
    else:
        group = None
    # No group: one row over every record of the module, as in `_read`.
    group_clause = " GROUP BY 1"
    if group is not None:
        key_sql, label_sql = group.key, group.label
        bound.update(group.params)
        source = from_source(source, group.source)
    elif group_by is not None:
        key_sql = MODULE_GROUPS[group_by]
    else:
        key_sql, group_clause = "NULL", ""
    bound[ZONE_PARAM] = zone
    await _keep_deadline(db, deadline)
    rows = (await db.execute(
        text(
            f"SELECT {key_sql} AS key, {label_sql} AS label, "
            f"{value_sql} AS value, {sum_sql} AS s, {count_sql} AS n, "
            f"count(*) AS c FROM {MODULE_TABLE}{source}{where.sql}{group_clause}"
        ),
        bound,
    )).fetchall()
    values = {
        _key(r.key): _Acc(
            value=None if r.value is None else float(r.value),
            s=float(r.s or 0), n=int(r.n or 0), name=r.label,
            rows=int(getattr(r, "c", 0) or 0),
        )
        for r in rows
    }
    labels: dict[str, str] = {}
    order: dict[str, int] = {}
    types: dict[str, str] = {}
    if stage is not None:
        # The lane order of §18.13.5: open, won, lost, then one Unsorted lane
        # for each value with no entry, a value that is not an option, and a
        # blank value. The Unsorted lane has the key None.
        kept = lanes(stage.options, overlay.values)
        lane_keys = {value for value, _word in kept}
        rest = [acc for key, acc in values.items() if key not in lane_keys]
        values = {key: acc for key, acc in values.items() if key in lane_keys}
        if rest:
            values[None] = _fold(measure, rest)
        for index, (value, word) in enumerate(kept):
            order[value], types[value] = index, word
            labels[value] = stage.options.get(value) or value
        order[""], types[""], labels[""] = len(kept), UNSORTED, UNSORTED_LABEL
    keys = used_keys(scope, [field, group_field, *(name for name, _value in pairs)])
    return _Read(values, labels, order, measured, grouped, zone, keys,
                 overlay=overlay.wire(), types=types)


@router.get("/aggregate", response_model=AggregateResponse)
async def aggregate(
    request: Request,
    entity: str,
    group_by: str | None = None,
    measure: str = "count",
    field: str | None = None,
    group_field: str | None = None,
    bucket: str | None = None,
    fields: str | None = None,
    long_read: bool = False,
    group_field2: str | None = None,
    group_page: int | None = None,
    group_page_size: int | None = None,
    query: str | None = None,
    params: ListParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> AggregateResponse:
    """One measure of one entity or module, by one group or over every
    record, over the filters.

    ``group_field2``, ``group_page`` and ``group_page_size`` give two group
    fields and pages of groups on a module (CRM-Q34-10, the module doc).

    ``fields=a,b,c`` reads 2 to 5 number fields in ONE statement (CRM-Q25e
    part 2). ``long_read=true`` asks for the long read of an org on
    ``CRM_LONG_READ_ORGS`` (part 5). Off the list it changes nothing.
    ``query`` is the one query grammar of a module (SEM-1b-1).

    The NUL guard of CRM-Q25c runs in ``ListParams``, which this route takes,
    before this body runs. A second call here would be a copy."""
    # An empty optional value is an absent one, the same for each of the four
    # (CRM-Q25 fix round). `group_field=` alone is then "no group": one total.
    group_by, field = group_by or None, field or None
    group_field, bucket = group_field or None, bucket or None
    fields, group_field2 = fields or None, group_field2 or None
    query = query or None
    _found, module = _check(entity, measure, group_by, group_field, field, bucket, fields)
    # CRM-Q20-3a: a core entity passes its field.<key> filters on. With an
    # overlay they filter the module. With none, compute refuses them.
    pairs = (field_pairs(request.query_params) if module is None
             else _check_filters(module, request))
    return await compute(
        entity, measure=measure, group_by=group_by, field=field,
        group_field=group_field, bucket=bucket, pairs=pairs, params=params, user=user,
        fields=fields, long_read=long_read, group_field2=group_field2,
        group_page=group_page, group_page_size=group_page_size, query=query,
    )


async def compute(
    entity: str, *, measure: str = "count", group_by: str | None = None,
    field: str | None = None, group_field: str | None = None,
    bucket: str | None = None, pairs: list[tuple[str, str]] | None = None,
    params: ListParams, user: UserContext | None = None,
    fields: str | None = None, long_read: bool = False,
    group_field2: str | None = None, group_page: int | None = None,
    group_page_size: int | None = None, query: str | None = None,
) -> AggregateResponse:
    """The ONE body of an aggregate. ``GET /crm/aggregate`` calls it, and so
    does the weekly digest of CRM-Q28 (``crm_sync/digest.py``), so a digest
    number and the number of the route are one answer.

    The tenant is the bound one (``current_tenant``): the route binds it from
    the session, and the digest step binds the org of its run. A caller never
    names an org here (R5). ``pairs`` are the checked ``field.<key>``
    filters of a module. Each refusal is an ``HTTPException``: 422 for a bad
    name, 503 ``aggregate_timeout`` past the timeout, and 503
    ``aggregate_busy`` when a long read waited past its budget.

    CRM-Q25e part 5: for an org on ``CRM_LONG_READ_ORGS``, the 503 of the
    10 s read carries ``long_read: true``, and a call with *long_read* runs
    under :func:`long_read_slot` with up to :data:`LONG_READ_SECONDS`.

    SEM-1b-1: *query* is the JSON text of the one query grammar, for a
    module of an org on ``SEMANTIC_MODEL_ORGS``. The digest passes none."""
    org = current_tenant()
    allowed = bool(org) and long_read_allowed(str(org))
    if long_read and allowed:
        async with long_read_slot(str(org), LONG_READ_SECONDS) as left:
            return await _compute(
                entity, measure=measure, group_by=group_by, field=field,
                group_field=group_field, bucket=bucket, pairs=pairs, params=params,
                user=user, fields=fields, timeout_ms=int(left * 1000),
                timed_out=LONG_TIMEOUT_DETAIL, group_field2=group_field2,
                group_page=group_page, group_page_size=group_page_size, query=query,
            )
    return await _compute(
        entity, measure=measure, group_by=group_by, field=field, group_field=group_field,
        bucket=bucket, pairs=pairs, params=params, user=user, fields=fields,
        timeout_ms=None,
        timed_out={**TIMEOUT_DETAIL, "long_read": True} if allowed else TIMEOUT_DETAIL,
        group_field2=group_field2, group_page=group_page, group_page_size=group_page_size,
        query=query,
    )


def _query_nodes(query: str | None, module: str | None) -> tuple[QueryNode, ...] | None:
    """SEM-1b-1: the checked nodes of *query*, or None with no query. From an
    org off ``SEMANTIC_MODEL_ORGS``, and on a core entity, a query is a 422
    that names a step that works, never a filter dropped in silence."""
    if not query:
        return None
    if not semantic_model_allowed(current_tenant()):
        raise query_off_refusal()
    if module is None:
        raise query_core_refusal()
    return parse_query(query)


async def _compute(
    entity: str, *, measure: str, group_by: str | None, field: str | None,
    group_field: str | None, bucket: str | None, pairs: list[tuple[str, str]] | None,
    params: ListParams, user: UserContext | None, fields: str | None,
    timeout_ms: int | None, timed_out: dict[str, Any], query: str | None = None,
    group_field2: str | None = None, group_page: int | None = None,
    group_page_size: int | None = None,
) -> AggregateResponse:
    """The body of :func:`compute`, with the statement timeout of the read
    (None for the 10 s of :data:`STATEMENT_TIMEOUT_MS`) and the detail of
    its 503."""
    group_by, field = group_by or None, field or None
    group_field, bucket = group_field or None, bucket or None
    fields = fields or None
    found, module = _check(entity, measure, group_by, group_field, field, bucket, fields)
    pairs = list(pairs or [])
    # SEM-1b-1: a query is checked before any session opens.
    nodes = _query_nodes(query, module)
    # CRM-Q34-2: `group_by=status` on a module is `group_field=@status`.
    status_group = module is not None and group_by == "status"
    if status_group:
        group_by, group_field = None, STATUS_WORD
    group_field2 = group_field2 or None
    # NUM-2: the flag is read ONCE for the request. The read and the answer
    # take this one value, so a reload between them never mixes the two.
    sense = number_sense_on()
    paging = _paging(module, group_field, fields, group_field2, group_page, group_page_size,
                     status_group=status_group, q=params.q)
    if paging is not None and module is not None and group_field is not None:
        paged = await _compute_pages(
            module, measure=measure, field=field, group_field=group_field,
            group_field2=group_field2, bucket=bucket, pairs=pairs, params=params,
            paging=paging, timeout_ms=timeout_ms, timed_out=timed_out,
            status_group=status_group, nodes=nodes)
        if paged is not None:
            return paged
    try:
        async with _tenant_session() as db:
            # CRM-Q20-3a: a core entity whose slot has an approved overlay
            # reads the module. With none, the read of today, unchanged.
            overlay = (
                await overlay_of(db, current_tenant(), SLOT_OF.get(found.slug))
                if found is not None else None
            )
            if overlay is not None and found is not None:
                if fields:
                    raise _refuse(
                        f"{found.label}s come from the module {overlay.module}. A split "
                        f"takes the module: give entity 'module:{overlay.module}'.")
                read = await _read_overlay(
                    db, found, overlay, measure=measure, group_by=group_by,
                    field=field, group_field=group_field, bucket=bucket,
                    pairs=pairs, params=params, user=user, timeout_ms=timeout_ms,
                )
            else:
                if module is None and pairs:
                    raise _core_pairs_refusal()
                try:
                    read = await _read(
                        db, found, module, measure=measure, group_by=group_by,
                        field=field, group_field=group_field, bucket=bucket,
                        pairs=pairs, params=params, user=user, fields=fields,
                        timeout_ms=timeout_ms, status_group=status_group, nodes=nodes,
                        sense=sense,
                    )
                except HTTPException as exc:
                    # Task 3690: a field that is not on this module names
                    # the other modules that hold it.
                    if module is None:
                        raise
                    raise await field_hint(db, module, exc) from None
    except DBAPIError as exc:
        if _is_timeout(exc):
            raise HTTPException(status_code=503, detail=timed_out) from None
        raise
    measured, grouped = read.measured, read.grouped
    overall = group_by is None and group_field is None
    bucketed = grouped is not None and grouped.type in DATE_TYPES
    options = grouped.options if grouped is not None else {}
    # CRM-Q25c item 3: the count of field values that the measure read. With
    # none, the field is empty, so no group and no total holds a number. A
    # sum of nothing is NULL in SQL, and an answer of 0 would say "zero".
    values = sum(acc.n for acc in read.values.values()) if measured else None
    if values == 0:
        for acc in read.values.values():
            acc.value = None
    # CRM-Q25e part 2: the same rule for each other field of a split.
    empty = [
        sum(acc.parts[i].n for acc in read.values.values() if i < len(acc.parts)) == 0
        for i in range(len(read.split) - 1)
    ]
    groups = [
        (
            AggregateGroup(
                key=key,
                label=(
                    read.labels.get(key or "") or acc.name or options.get(key or "")
                    or key or "(none)"
                ),
                value=_rounded(acc.value),
                type=read.types.get(key or "") if read.types else None,
                others=[
                    CurrencyValue(currency=code, value=_rounded(acc.others[code]))
                    for code in read.codes if code in acc.others
                ],
                split=_group_split(read.split, acc, empty),
            ),
            acc,
        )
        for key, acc in read.values.items()
    ]
    # The 50 largest groups stay. The rest fold into `other`. CRM-Q34-10:
    # only where page 2 can continue this cut does it take the exact order
    # of the paged read. Every other answer keeps the order of before.
    groups.sort(key=_page1_key(len(groups) > MAX_GROUPS and _pages_on(
        module, group_field, fields, bucketed, status_group, params.q)))
    kept, rest = groups[:MAX_GROUPS], groups[MAX_GROUPS:]
    # With no group, the one row IS the total, so no group is shown.
    shown = [] if overall else [g for g, _acc in kept]
    if group_by == "status":
        shown.sort(key=lambda g: (read.order.get(g.key or "", 1 << 30), g.label))
    elif bucketed or group_by in MONTH_GROUPS:
        shown.sort(key=lambda g: (g.key is None, g.key or ""))
    total = None if values == 0 else _rounded(_combine(measure, [acc for _g, acc in groups]))
    records = (
        None if measure == "weighted" else sum(acc.rows for acc in read.values.values())
    )
    covered = _coverage(read, values, records) if sense else None
    return AggregateResponse(
        entity=found.slug if found is not None else f"{MODULE_PREFIX}{module}",
        measure=measure, group_by=group_by, group_field=group_field,
        field=measured.key if measured is not None else None,
        field_type=measured.type if measured is not None else None,
        field_label=measured.label if measured is not None else None,
        group_field_label=grouped.label if grouped is not None else None,
        keys=read.keys,
        group_field_type=grouped.type if grouped is not None else None,
        bucket=bucket if bucketed else None,
        groups=shown,
        total=total,
        other=(
            (None if values == 0 else _rounded(_combine(measure, [acc for _g, acc in rest])))
            if rest else 0
        ),
        omitted=len(rest),
        currency=read.currency,
        others=[
            CurrencyTotal(
                currency=code,
                total=_rounded(sum(a.others.get(code, 0.0) for _g, a in groups)),
                other=(
                    _rounded(sum(a.others.get(code, 0.0) for _g, a in rest))
                    if rest else 0
                ),
            )
            for code in read.codes
        ],
        zone=read.zone,
        values=values,
        records=records,
        overlay=read.overlay,
        matched=read.matched or None,
        near=read.near,
        nearest=read.nearest,
        filter_words=read.filter_words or None,
        split=_split_totals(read, measure, total, values, empty, [acc for _g, acc in rest]),
        text_numbers={key: found.wire() for key, found in read.text_numbers.items()} or None,
        findings=_findings(
            read, measure,
            (bucket or "month") if bucketed else ("month" if group_by in MONTH_GROUPS else None),
            group_by),
        next_group_page=_next_page(module, group_field, fields, rest, bucketed,
                                   status_group, params.q),
        checks=_checks(read, measure, covered, today=_today(read.zone)) if sense else [],
        baseline=[
            {"period": label, "value": _rounded(value), "records": rows}
            for period, label, value, rows in _periods(read, measure) if period < 0
        ] if sense else [],
        coverage=covered,
    )


#: The cents of :func:`cents`. The context grows with the value
#: (:func:`cents`), from at least 1,000 digits.
_CENT = Decimal("0.01")
_WIDE_DIGITS = 1000

#: What :func:`cut_key` reads when no raw value is given.
_SHOWN = object()


def cents(value: Any) -> Decimal | None:
    """CRM-Q34-10: *value* rounded to 2 places as Postgres rounds a
    ``numeric``: half away from zero, on the exact digits. A ``Decimal`` (the
    ``numeric`` of the row) and an integer round as they are. A float has
    lost digits already, so it reads ``repr``, its shortest text: only a
    value with no exact number takes that way. None, NaN and an infinity
    give None. A value of any size rounds: the context holds every digit."""
    if value is None:
        return None
    try:
        exact = _exact(value)
        if exact is None:
            exact = Decimal(repr(float(value)))
        if not exact.is_finite():
            return None
        digits = max(_WIDE_DIGITS, exact.adjusted() + 10)
        return exact.quantize(_CENT, rounding=ROUND_HALF_UP,
                              context=Context(prec=digits, Emax=digits * 2))
    except (TypeError, ValueError, InvalidOperation, OverflowError):
        return None


def _cut_value(acc: _Acc) -> Any:
    """The value that the cut of page 1 rounds: the exact ``raw`` of the row
    when the answer still shows a value, else ``value`` (None when the field
    is empty in every row)."""
    if acc.value is None:
        return None
    return acc.raw if acc.raw is not None else acc.value


def cut_key(group: AggregateGroup, raw: Any = _SHOWN) -> tuple[Any, ...]:
    """The order in which an answer of one page keeps its 50 largest groups:
    the value down (None last), then the label. CRM-Q34-10: then the key by
    code point, None last, so a tie of value and label has one order. It is
    the order of ``field_sql.one_group_order``, so page 2 continues this cut.

    *raw* is the value before the answer rounds it (``_Acc.value``). The
    order rounds it as SQL does (:func:`cents`), never as ``round(float)``
    does, so the two pages agree on a half cent. The shown value does not
    change."""
    value = cents(group.value if raw is _SHOWN else raw)
    return (value is None, -(value or 0), group.label, group.key is None, group.key or "")


def _page1_key(exact: bool) -> Callable[[tuple[AggregateGroup, _Acc]], tuple[Any, ...]]:
    """The sort key of the answer of one page. *exact* takes the order of the
    paged read (:func:`cut_key`): only for more than :data:`MAX_GROUPS`
    groups where page 2 can continue the cut (:func:`_pages_on`). Else the
    key of before, on the shown value, byte for byte."""
    if exact:
        return lambda g: cut_key(g[0], _cut_value(g[1]))
    return lambda g: (g[0].value is None, -(g[0].value or 0.0), g[0].label)


def _pages_on(module: str | None, group_field: str | None, fields: str | None,
              bucketed: bool, status_group: bool, q: str | None) -> bool:
    """CRM-Q34-10: True when ``group_page=2`` can continue the answer of one
    page (:func:`_paging`): a module on ``CRM_MODULE_FIND_ORGS``, and a value
    group of one field with no split, no key word and no ``q``. Only then
    does page 1 take the exact order of the paged read (:func:`cut_key`)."""
    if (module is None or group_field is None or fields or bucketed or status_group
            or q is not None):
        return False
    return module_find_allowed(current_tenant())


def _next_page(module: str | None, group_field: str | None, fields: str | None,
               rest: list[Any], bucketed: bool, status_group: bool,
               q: str | None) -> int | None:
    """CRM-Q34-10: 2 when ``group_page=2`` continues this answer of one page
    and groups were left out (:func:`_pages_on`)."""
    if rest and _pages_on(module, group_field, fields, bucketed, status_group, q):
        return 2
    return None


def _group_split(split: list[FieldRef], acc: _Acc, empty: list[bool]) -> dict[str, float | None]:
    """CRM-Q25e part 2: each field of a split → its value in the group of
    *acc*. Empty with no split. A field that no record fills is None."""
    if not split:
        return {}
    out: dict[str, float | None] = {split[0].key: _rounded(acc.value)}
    for i, ref in enumerate(split[1:]):
        part = acc.parts[i] if i < len(acc.parts) else _Acc(value=None)
        out[ref.key] = None if empty[i] else _rounded(part.value)
    return out


def _split_totals(read: _Read, measure: str, total: float | None, values: int | None,
                  empty: list[bool], rest: list[_Acc]) -> list[SplitTotal]:
    """CRM-Q25e part 2: the total of each field of a split, from the one
    statement, and its ``other``: the measure over the groups past
    :data:`MAX_GROUPS` (*rest*). The first is the answer's own ``total``."""
    if not read.split:
        return []
    first = read.split[0]
    out = [SplitTotal(
        field=first.key, label=first.label, type=first.type, total=total,
        values=int(values or 0),
        other=(None if total is None else _rounded(_combine(measure, rest))) if rest else 0,
    )]
    accs = list(read.values.values())
    for i, ref in enumerate(read.split[1:]):
        parts = [a.parts[i] for a in accs if i < len(a.parts)]
        past = [a.parts[i] for a in rest if i < len(a.parts)]
        out.append(SplitTotal(
            field=ref.key, label=ref.label, type=ref.type, values=sum(p.n for p in parts),
            total=None if empty[i] else _rounded(_combine(measure, parts)),
            other=(None if empty[i] else _rounded(_combine(measure, past))) if rest else 0,
        ))
    return out


#: The words of a fixed month group in a finding: "one month of the created date".
_MONTH_LABELS: dict[str, str] = {
    "created_month": "the created date", "updated_month": "the modified date",
    "close_month": "the close date",
}


def _findings(read: _Read, measure: str, bucket: str | None,
              group_by: str | None) -> list[dict[str, Any]]:
    """CRM-Q25e part 4: what the reader must know before the total
    (``acb_common.number_sense``, the one rule). First the days of the date
    field, from the same statement. When the day set ran, its answer is the
    answer, also when it is empty: a batch on the last day of a month that
    rule 5 held back must not come back as a month. Else, for an answer by a
    date bucket or a month (*bucket* is its word), its own groups, each
    named as a bucket and never as a day. The thresholds live in
    ``number_sense``."""
    summed = measure in SUMMING_MEASURES and not read.codes
    if read.day is not None and read.days:
        found = number_findings(
            [(None if d is None else str(d), v, c) for d, v, c in read.days],
            label=read.day.label, summed=summed)
        return [f.wire() for f in found]
    if bucket is None:
        return []
    label = (read.grouped.label if read.grouped is not None
             else _MONTH_LABELS.get(group_by or "", "the date"))
    found = number_findings(
        [(key, acc.value, acc.rows) for key, acc in read.values.items()],
        label=label, summed=summed, bucket=bucket)
    return [f.wire() for f in found]


# ── NUM-2: the baseline, the coverage and the checks (number_sense.md §4.2) ──


def _periods(read: _Read, measure: str) -> list[tuple[int, str, float | None, int]]:
    """``(period, label, value, records)`` for each period of the baseline,
    oldest first, and the asked range last (period 0). Empty with no
    baseline. A field measure with no value in a period has the value None,
    as the total of an empty field has (CRM-Q25c item 3)."""
    if read.base is None:
        return []
    out = []
    for period in range(-BASELINE_PERIODS, 1):
        accs = list(read.values.values()) if period == 0 else read.periods.get(period, [])
        empty = read.measured is not None and sum(a.n for a in accs) == 0
        value = None if empty else _combine(measure, accs)
        out.append((period, read.base.label(period), value, sum(a.rows for a in accs)))
    return out


def _coverage(read: _Read, values: int | None, records: int | None) -> dict[str, Any] | None:
    """The records of the answer, the records with no value in the measure
    field, and the count of each status that a total usually leaves out,
    all from the same statement. None with no measure field and no status
    field, and for ``weighted``, which counts no records."""
    if records is None or (read.measured is None and not read.statuses):
        return None
    out: dict[str, Any] = {"records": records}
    if read.measured is not None and values is not None:
        out["no_value"] = max(0, records - values)
    if read.statuses:
        out["statuses"] = {
            status: sum(acc.statuses.get(status, 0) for acc in read.values.values())
            for status in read.statuses
        }
    return out


#: NUM-2: the measures whose period figure an ``outlier`` reads. A count and
#: a sum grow with the records of a period. A min, a max or an average of a
#: period is no figure of the same kind, so it gets no outlier. ``weighted``
#: reads deals, which get no baseline.
OUTLIER_MEASURES = frozenset({"count", "sum", "sum_amount"})
#: NUM-2: the fewest records of an answer that a ``coverage`` check reads,
#: the minimum that NUM-1 sets for a concentration
#: (``number_sense.CONCENTRATION_MIN_RECORDS``). With fewer, a share of a few
#: records is no signal. The ``coverage`` counts stay in the answer.
COVERAGE_MIN_RECORDS = CONCENTRATION_MIN_RECORDS


def _clock() -> datetime:
    """NUM-2: now, from the one clock of the CRM routes (``core._now``).
    The tests patch this function."""
    return _crm_now()


def _today(zone: str) -> date:
    """NUM-2: the day of now in the CRM zone *zone*, the zone of each range
    of the answer (CRM-Q25b). A zone that does not load reads as UTC."""
    try:
        tz = ZoneInfo(zone) if zone else ZoneInfo("UTC")
    except (ValueError, KeyError, OSError):
        tz = ZoneInfo("UTC")
    return _clock().astimezone(tz).date()


def _checks(read: _Read, measure: str, covered: dict[str, Any] | None, *,
            today: date) -> list[dict[str, Any]]:
    """NUM-2: the ``outlier`` of the asked range against the periods before
    it, and the ``coverage`` of the answer (``acb_common.number_sense``, the
    one rule and its thresholds). Each key is a period label that this
    module makes from the bound dates, or a fixed word, never a record
    value. A ``concentration`` stays in ``findings`` (:func:`_findings`).

    A range that is not over on *today* (the CRM zone) has only part of
    its records, so it gets no ``outlier``. A month or a quarter gets the
    ``part_period`` of NUM-1 in its place, an ``info`` that says so. Only a
    measure of :data:`OUTLIER_MEASURES` gets an outlier. An answer of fewer
    than :data:`COVERAGE_MIN_RECORDS` records gets no coverage check."""
    found = []
    base = read.base
    buckets = _periods(read, measure) if base is not None else []
    if base is not None and buckets and measure in OUTLIER_MEASURES:
        word = ("month" if base.months == 1
                else "quarter" if base.months == 3 and base.start.month % 3 == 1
                else "period")
        if today <= base.end:
            found.extend(part_period(word, base.start, today))
        else:
            asked = base.label(0)
            found.extend(
                f for f in outlier([(label, value, rows) for _p, label, value, rows in buckets],
                                   bucket=word)
                if f.key == asked)
    if covered is not None and covered["records"] >= COVERAGE_MIN_RECORDS:
        found.extend(coverage(
            covered["records"], covered.get("no_value"), covered.get("statuses"),
            measure=read.measured.label if read.measured is not None else "value"))
    return [f.wire() for f in found]


# ── Stage dwell (the read half of CRM-I1) ───────────────────────────────────

#: Median and 90th percentile of the measured departures from each stage.
#: The log stores status NAMES (``reports.py`` fact 2), so the join is by name.
_DWELL_SQL = (
    "SELECT from_status AS name, count(*) AS samples, "
    "percentile_cont(0.5) WITHIN GROUP (ORDER BY dwell_seconds) AS p50, "
    "percentile_cont(0.9) WITHIN GROUP (ORDER BY dwell_seconds) AS p90 "
    "FROM crm_status_changes "
    "WHERE entity_type = 'deal' AND from_status IS NOT NULL "
    "AND dwell_seconds IS NOT NULL GROUP BY from_status"
)

#: The open deals past their limit. ``:days`` replaces each stage's
#: ``max_dwell_days`` when the caller gives it. A stage with no limit, and a
#: deal with no stage clock, is never overdue (``board.ts`` rule).
_OVERDUE_FROM = (
    "FROM crm_deals d JOIN crm_deal_statuses s ON s.id = d.status_id "
    "WHERE s.type <> ALL(CAST(:closing AS text[])) "
    "AND d.status_changed_at IS NOT NULL "
    "AND COALESCE(CAST(:days AS int), CAST(s.max_dwell_days AS int)) IS NOT NULL "
    "AND d.status_changed_at < now() - make_interval(days => "
    "COALESCE(CAST(:days AS int), CAST(s.max_dwell_days AS int)))"
)

_OVERDUE_SQL = (
    "SELECT d.id, d.name, d.owner_email, d.amount, d.currency, "
    "s.name AS stage, "
    "EXTRACT(EPOCH FROM (now() - d.status_changed_at)) / 86400.0 AS days_in, "
    "COALESCE(CAST(:days AS int), CAST(s.max_dwell_days AS int)) AS limit_days "
    f"{_OVERDUE_FROM} "
    "ORDER BY d.status_changed_at ASC, d.id LIMIT :limit OFFSET :offset"
)

_SECONDS_PER_DAY = 86_400.0


# ── My deals, for the My Day feed (WS-58 MD-3e) ─────────────────────────────
#
# Two reads of the deals that I own, for `deal_quiet` and `deal_close_passed`
# (`my_day_feed.md` §5.9 item 13). The caller gives the session, so the feed's
# three CRM reads share one. ⚠️ Each statement names the bound tenant AND the
# member. RLS holds the gateway role (`acb_app`), so the predicate is defence
# in depth, and an R8 fence proves it on a role that RLS does not hold. "I
# own" is `lower(owner_email)`, which reads
# through `idx_crm_deals_owner_email`. Never `stage_dwell`: that read covers
# the whole organization.

#: The tenant and the owner, for each read below. Both sides of the join name
#: the tenant, so no stage of another organization joins.
_MINE = (
    " AND d.organization_id = CAST(:org AS uuid)"
    " AND s.organization_id = CAST(:org AS uuid)"
    " AND lower(d.owner_email) = :member"
)

#: My open deals past the limit of their own stage. ``_OVERDUE_FROM`` with
#: no ``:days`` override, so a stage with no limit never fires.
_MY_OVERDUE_SQL = (
    "SELECT d.id, d.name, s.name AS stage, d.status_changed_at, "
    "CAST(s.max_dwell_days AS int) AS limit_days "
    f"{_OVERDUE_FROM}{_MINE} "
    "ORDER BY d.status_changed_at ASC, d.id LIMIT :limit"
)

#: My open deals whose expected close date is before my local today.
_MY_CLOSE_PASSED_SQL = (
    "SELECT d.id, d.name, s.name AS stage, d.expected_close_date "
    "FROM crm_deals d JOIN crm_deal_statuses s ON s.id = d.status_id "
    "WHERE s.type <> ALL(CAST(:closing AS text[])) "
    "AND d.expected_close_date < :today"
    f"{_MINE} "
    "ORDER BY d.expected_close_date ASC, d.id LIMIT :limit"
)


def _mine(user: UserContext) -> dict[str, Any] | None:
    """The binds of the tenant and the member, or None when one is missing."""
    org = current_tenant()
    member = (getattr(user, "email", None) or "").strip().lower()
    if not org or not member:
        return None
    return {"org": str(org), "member": member, "closing": list(CLOSING_TYPES)}


async def my_overdue_deals(db: Any, user: UserContext, *, limit: int) -> list[dict[str, Any]]:
    """My open deals that passed the ``max_dwell_days`` of their own stage,
    the longest in the stage first. Empty in Mirror mode, because the sync
    writes no stage change, so the stage clock is not known (the reason of
    ``stage_dwell``). Each row is ``{id, name, stage, status_changed_at,
    limit_days}``."""
    binds = _mine(user)
    if binds is None or await is_mirror_mode(db, binds["org"]):
        return []
    rows = (await db.execute(text(_MY_OVERDUE_SQL), {
        **binds, "days": None, "limit": max(1, int(limit)),
    })).fetchall()
    return [{"id": str(r.id), "name": r.name, "stage": r.stage,
             "status_changed_at": r.status_changed_at, "limit_days": int(r.limit_days)}
            for r in rows]


async def my_close_passed_deals(
    db: Any, user: UserContext, *, today: Any, limit: int,
) -> list[dict[str, Any]]:
    """My open deals whose ``expected_close_date`` is before ``today``, the
    member's local date. A won or lost deal is closed, so it never returns.
    Each row is ``{id, name, stage, expected_close_date}``."""
    binds = _mine(user)
    if binds is None:
        return []
    rows = (await db.execute(text(_MY_CLOSE_PASSED_SQL), {
        **binds, "today": today, "limit": max(1, int(limit)),
    })).fetchall()
    return [{"id": str(r.id), "name": r.name, "stage": r.stage,
             "expected_close_date": r.expected_close_date} for r in rows]


async def my_day_rows(user: UserContext, *, now: datetime, limit: int) -> dict[str, Any]:
    """The rows of the feed's ``crm`` source, in ONE tenant session (WS-58
    MD-3e, ``my_day_feed.md`` §5.9 item 13): the member's zone, then my due
    follow-ups, my quiet deals and my deals past their close date.

    ``now`` is the feed's one clock. The zone comes from
    ``personal.stored_zone``, so "today" is the member's own, and the
    follow-ups follow the one CRM day rule (``activities._MY_DUE_SQL``). The caller has applied the kill switch and the access rule
    before it calls this. Each read takes ``limit`` rows at most.

    ``digest`` is the newest weekly digest of the org that was stored inside
    the last day (CRM-Q28 item 14), or None. The read runs only for an org
    on ``CRM_DIGEST_ORGS``, so a dark box sends no new statement.

    ``questions`` is the card of the open questions (CRM-Q32-4), or None. It
    reads ONE cursor row, ``zoho:questions``, and builds no question. The
    read runs only for a CRM admin of an org on ``CRM_QUESTIONS_ORGS``.
    """
    from gateway.routes.crm import activities, digests, flags
    from gateway.routes.crm.access import ADMIN_PERMISSION
    from gateway.routes.crm.questions import questions_card
    from gateway.routes.projects.personal import stored_zone, zone_name

    async with _tenant_session() as db:
        zone = zone_name(await stored_zone(db, user.email))
        tz = ZoneInfo(zone)
        today = now.astimezone(tz).date()
        org = current_tenant()
        return {
            "zone": zone,
            "today": today,
            "due": await activities.my_due_activities(db, user, today=today, tz=tz,
                                                      limit=limit),
            "quiet": await my_overdue_deals(db, user, limit=limit),
            "passed": await my_close_passed_deals(db, user, today=today, limit=limit),
            "digest": (
                await digests.ready_digest(db, str(org), now)
                if org and flags.digest_allowed(org) else None
            ),
            "questions": (
                await questions_card(db, str(org), now)
                if org and flags.questions_allowed(org)
                and user.has_permission(ADMIN_PERMISSION) else None
            ),
        }


def _days(seconds: Any) -> float | None:
    return None if seconds is None else round(float(seconds) / _SECONDS_PER_DAY, 1)


@router.get("/aggregate/stage-dwell", response_model=StageDwellResponse)
async def stage_dwell(
    days: int | None = Query(None, ge=1, le=32767),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    user: UserContext = Depends(get_current_user),
) -> StageDwellResponse:
    """Days in each deal stage, and the open deals past their limit.

    ``days`` replaces the limit of each stage (``max_dwell_days``). With no
    ``days``, a stage with no limit lists no deal.
    """
    async with _tenant_session() as db:
        if await is_mirror_mode(db, current_tenant()):
            return StageDwellResponse(
                stages=[], overdue=[], overdue_total=0,
                reason=MIRROR_DWELL_REASON,
            )
        lanes = (await db.execute(
            text("SELECT * FROM crm_deal_statuses ORDER BY position, name"), {},
        )).fetchall()
        measured = {
            str(r.name): r
            for r in (await db.execute(text(_DWELL_SQL), {})).fetchall()
        }
        bound = {"days": days, "closing": list(CLOSING_TYPES)}
        total = (await db.execute(
            text(f"SELECT count(*) {_OVERDUE_FROM}"), bound,
        )).scalar() or 0
        rows = (await db.execute(text(_OVERDUE_SQL), {
            **bound, "limit": page_size, "offset": (page - 1) * page_size,
        })).fetchall()
    stages = []
    for lane in lanes:
        seen = measured.get(str(lane.name))
        stages.append(StageDwell(
            status_id=str(lane.id), name=str(lane.name),
            type=str(getattr(lane, "type", "open") or "open"),
            max_dwell_days=getattr(lane, "max_dwell_days", None),
            samples=int(seen.samples) if seen is not None else 0,
            median_days=_days(seen.p50) if seen is not None else None,
            p90_days=_days(seen.p90) if seen is not None else None,
        ))
    return StageDwellResponse(
        stages=stages,
        overdue=[
            OverdueDeal(
                id=str(r.id), name=str(r.name), stage=str(r.stage),
                owner_email=r.owner_email,
                amount=None if r.amount is None else float(r.amount),
                currency=r.currency,
                days_in_stage=round(float(r.days_in or 0), 1),
                limit_days=int(r.limit_days),
            )
            for r in rows
        ],
        overdue_total=int(total),
    )
