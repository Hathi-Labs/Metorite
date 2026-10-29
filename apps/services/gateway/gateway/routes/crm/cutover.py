"""CRM · the cutover from Zoho to Metorite (WS-53 CRM-Z10a and CRM-Z10b).

Spec: ``crm_platform.md`` §3.4 and §3.4.1. Decisions: D133 (with its two
amendments) and D137.

    GET  /crm/cutover/preview   what moves to Metorite, and what does not (Z10a)
    POST /crm/cutover/start     phase 1: the checks, then the final sync (Z10b)
    GET  /crm/cutover/status    the final sync, and whether the switch is done
    POST /crm/cutover           phases 2 and 3: the switch, then close Zoho

**The preview (CRM-Z10a).** An admin reads it, and the owner reads it with a
customer. ⚠️ It writes nothing, and it calls no source. The R8 test hashes
every ``crm_*`` table and the connections table before and after the call, and
a fake source records no call. The counts in Zoho come from the last good sync
run (``crm_sync_runs.counts`` and ``estimate``), shown "as of <time>". A live
Zoho count would read a credential, and a token refresh is a write. It needs
the CRM kill switch (at the router), ``CRM_MIRROR`` (404 otherwise) and
``admin:access:manage`` (403 otherwise). Another mode gets 409 ``not_mirror``.

⚠️ **The Zoho count of a key is its own.** An incremental run carries no core
estimate, and its ``seen`` is the number of changed records. So the count of a
key comes from the newest good run that HAS an estimate for it, with that run's
time. It never falls back to ``seen``. When no good run has an estimate for the
key, the count is ``None`` and the card shows a dash.

**Not complete (one rule, :func:`module_state`).** The preview and the switch
call the same function. A count key is not complete when it is held
(CRM-Q16), unavailable (CRM-Q15) in its cursor or in the run, still importing
(``in_progress``), has records to retry (the cursor's ``failed`` map), reads
short (``sync.short_reads``), or the run counted ``failed`` for it. The run
is the newest good run, or the final run in phase 2. The switch waits for
such a key unless the admin leaves it out (D133).

⚠️ **The delete check of a walk** (``engine._drop_stale``) counts
``refused`` for four causes, and they count apart (round 6). A walk that
read fewer records than Zoho holds (``empty_walk``, ``short_walk``) is no
complete read: the engine flags ``walk_short``, and the key waits (reason
``walk_short``). A refusal after a complete read (``count_unknown`` with
records read, ``large_delete``) only kept rows that Zoho may have
deleted, which D133 accepts: the engine flags ``delete_kept``, and nothing
waits. ``refused`` itself is no reason, and the preview keeps its number.

⚠️ **Phase 2 also needs the final run to have READ each key** (reason
``not_read``, review P1, verifier P3-1, round 4). The rule takes each key
whose cursor has finished its first import: each core key, and each generic
module of the newest profile. The proof of a read is its END in this run:
each read stamps ``checked_at`` at its end, and the final run notes the time
of its plan (``counts["run_final"]["planned_ms"]``, one clock). A key in
``counts`` proves nothing. A run that reached ``engine.MODULES_RESERVE``
still ends ``ok``, and an import-kind run inherits old module keys. A
one-module run (``counts[engine.RUN_ONLY_KEY]``) reads no other key, and a
module that waits for its fields (``fields_pending``) is not read either.

⚠️ **Phase 2 also waits for the contacts of the deals** (``deal_contacts``,
reason ``roles_waiting``, round 4). The roles step reads one deal for each
credit and stops at ``engine.ROLES_RESERVE``. While deals wait in its queue,
phase 2 answers 409 with a row that says how many. After the switch no run reads them, so the admin
waits for more syncs or leaves them out. The preview and phase 1 apply
neither rule, but phase 1 takes such a Leave out tick.

**The switch (CRM-Z10b).** ⚠️ Dark behind ``CRM_CUTOVER_ORGS`` (404 off the
list), admin only (403), and the admin types ``SWITCH`` (422
``confirm_required``). The org comes from the session (R5, R11), and each body
refuses an unknown field.

1. ``POST /crm/cutover/start`` checks the mode, the connection, the open
   import and each key, then starts the final sync through the normal engine,
   each page in its own transaction. ⚠️ It ALWAYS starts its own full run and
   returns the ``run_id``. A good run that is fresh does not count: a "Sync
   it on its own" run reads one module only, and the run row does not say
   so. It starts NO label, vector, mapping, question or digest step
   (``scheduler.spawn`` would). ⚠️ It starts the run as the final sync
   (``engine.acquire(final=True)``): the run reads each generic module now,
   also one that a run read less than ``engine.MODULE_READ_EVERY`` ago, and an
   offset-paged one in full. It walks each offset-paged core entity now
   (the 24 h walk), and a stopped read by offset starts again from page 1.
   The row says so in ``counts["run_final"]``.
2. ``POST /crm/cutover`` runs phase 2 in ONE transaction. The lock order is the
   order of the start over (``settings._start_over``): the advisory connect
   lock, then ``crm_settings`` ``FOR UPDATE``. It checks the mode, no open
   run, and that the newest run IS the run of phase 1 (the ``run_id`` of the
   body), ended well in the last 15 minutes by the database clock. Any other
   newest run is 409 ``final_sync_missing``, also a scheduled run that ended
   after the final run. A run with no ``counts["run_final"]`` is 409
   ``final_sync_missing`` too. Then it checks each key again. Then
   :func:`fold_cutover_overlay` (THE FOLD),
   the labels, :func:`own_cutover_fields`, the watermark rewind (§3.4.1) and
   the mode. A write that meets the switch is 409 ``crm_changed``. One audit
   line, ``crm.cutover``, after the commit.
3. After the commit, :func:`connection.disconnect_zoho_reporting` revokes the
   token and deletes the blob, under the connect lock. A failure there never
   undoes phase 2: the admin presses again, and a second call answers
   ``already_done`` and closes Zoho. A Native org with a cutover stamp skips
   the checks of the run, so that retry needs no ``run_id``. A second audit
   line, ``crm.cutover.disconnect``, records what Zoho said, only when the
   call changed the row.

⚠️ It never calls ``seed_native_pipeline`` (a fence reads the AST), and
``ALLOWED_MODE_CHANGES`` gains no Mirror-to-Native entry. The records, the
pipelines, the stages of each pipeline, the lost reasons and the sync history
stay as they are.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.connections import store
from gateway.routes.crm.access import ADMIN_PERMISSION, read_settings
from gateway.routes.crm.connection import (
    _CONNECT_LOCK_SQL,
    OWNING_APP,
    PROVIDER,
    disconnect_zoho_reporting,
)
from gateway.routes.crm.core import (
    ADMIN_ORDER_TAIL,
    _tenant_session,
    count_records,
    from_jsonb,
    router,
)
from gateway.routes.crm.fill_rank import labels_of
from gateway.routes.crm.flags import (
    cutover_allowed,
    require_crm_cutover,
    require_crm_mirror,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_log = get_logger("crm.cutover")

_GUARDS = [Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)]
#: The switch routes. The flag runs first, so an org off the list gets 404.
_SWITCH_GUARDS = [Depends(require_crm_cutover), require_permission(ADMIN_PERMISSION)]

MODULE_KEY_PREFIX = "module:"

#: The one word the admin types. The dialog shows it, and both POST routes
#: check it, case and spaces included (as ``settings.RESET_CONFIRM``).
CUTOVER_CONFIRM = "SWITCH"

#: The final sync must have ended this many minutes ago or less.
FRESH_MINUTES = 15

#: The five core keys of a sync run, with the table of each and the word that a
#: member reads when the source gave no label.
_CORE: tuple[tuple[str, str, str, str], ...] = (
    ("organizations", "crm_companies", "Companies", "organizations"),
    ("contacts", "crm_contacts", "Contacts", "contacts"),
    ("leads", "crm_leads", "Leads", "leads"),
    ("deals", "crm_deals", "Deals", "deals"),
    ("activities", "crm_activities", "Activities", ""),
)

#: The count keys that the switch reads, apart from ``module:<ApiName>``. The
#: step keys ``converted_leads`` and ``deal_contacts`` are not records.
_KEYS: frozenset[str] = frozenset({k for k, *_ in _CORE} | {"tasks"})

#: The step key of the contacts of each deal (``engine.ROLES_MODULE`` holds
#: its queue). It is no record key, but phase 2 can find it waiting
#: (``roles_waiting``, round 4), and the admin can leave it out.
ROLES_KEY = "deal_contacts"

#: The reasons a key is not complete. When a key has two, the first wins.
#: ``not_read`` and ``roles_waiting`` are for phase 2 only: the final run did
#: not read the key, or deals still wait for their contacts.
Reason = Literal[
    "held", "unavailable", "importing", "failed", "short", "refused", "walk_short",
    "not_read",
    "roles_waiting",
]

#: The tables that carry an owner and a ``custom`` column.
_OWNED_TABLES: tuple[str, ...] = ("crm_companies", "crm_contacts", "crm_leads", "crm_deals")

_ACTIVITY_LABELS = {"note": "Notes", "call": "Calls", "meeting": "Meetings", "task": "Tasks"}

#: The most owner names that the preview lists.
_OWNER_LIMIT = 100

# Every statement below reads. A name that reaches an f-string comes from the
# constants above, never from a request (R5).
_SPLIT_SQL = (
    "SELECT count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM {table} WHERE organization_id = CAST(:org AS uuid)"
)
_ACTIVITY_SQL = (
    "SELECT type, count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_activities WHERE organization_id = CAST(:org AS uuid) GROUP BY type"
)
_MODULE_SQL = (
    "SELECT module, count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_module_records WHERE organization_id = CAST(:org AS uuid) "
    "GROUP BY module"
)
_OWNER_SQL = (
    "SELECT name, sum(n)::bigint AS records FROM ("
    + " UNION ALL ".join(
        f"SELECT custom ->> '_owner_name' AS name, count(*) AS n FROM {t} "
        "WHERE organization_id = CAST(:org AS uuid) AND owner_email IS NULL "
        "AND custom ->> '_owner_name' IS NOT NULL GROUP BY 1"
        for t in _OWNED_TABLES
    )
    + ") u GROUP BY name ORDER BY records DESC, name LIMIT :lim"
)
_OWNER_TOTAL_SQL = (
    "SELECT count(DISTINCT name) AS names FROM ("
    + " UNION ALL ".join(
        f"SELECT custom ->> '_owner_name' AS name FROM {t} "
        "WHERE organization_id = CAST(:org AS uuid) AND owner_email IS NULL "
        "AND custom ->> '_owner_name' IS NOT NULL"
        for t in _OWNED_TABLES
    )
    + ") u"
)
_PIPELINES_SQL = (
    "SELECT count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_pipelines WHERE organization_id = CAST(:org AS uuid)"
)
_STAGES_SQL = (
    "SELECT count(*) AS n FROM crm_pipeline_stages "
    "WHERE organization_id = CAST(:org AS uuid)"
)
_LINKS_SQL = (
    "SELECT count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_deal_contacts WHERE organization_id = CAST(:org AS uuid)"
)
_FIELDS_SQL = (
    "SELECT count(*) FILTER (WHERE source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_field_defs WHERE organization_id = CAST(:org AS uuid)"
)
_VIEWS_SQL = (
    "SELECT count(*) AS n FROM crm_views WHERE organization_id = CAST(:org AS uuid)"
)
_COLUMNS = "id, kind, status, started_at, finished_at, heartbeat_at, counts, estimate, credits_used, error"
#: The newest good run, however old.
_NEWEST_OK_SQL = (
    f"SELECT {_COLUMNS} FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) AND status = 'ok' "
    "ORDER BY started_at DESC LIMIT 1"
)
#: The newest good run that holds an estimate for one key.
_NEWEST_ESTIMATE_SQL = (
    f"SELECT {_COLUMNS} FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) AND status = 'ok' "
    "AND estimate ->> CAST(:key AS text) IS NOT NULL "
    "ORDER BY started_at DESC LIMIT 1"
)
_PROFILE_SQL = (
    "SELECT profile -> 'modules' AS modules FROM crm_source_profiles "
    "WHERE organization_id = CAST(:org AS uuid) "
    "ORDER BY fetched_at DESC, version DESC LIMIT 1"
)

# ── THE FOLD (CRM-Z10b, D137) ───────────────────────────────────────────────
#
# The board must look the same after the switch as just before it. Mirror
# mode draws the columns in the order of ``orderLanes(mode "mirror")`` in
# ``app/crm/lib/board.ts``. Native mode draws them by ``position``, then the
# name, with a hidden or a gone row last. So the fold writes ``position`` in
# the Mirror order, and the two orders are equal.
#
# Deal stages: a hidden or a gone stage goes last (``isRetired``). With a saved
# order (any ``ui_position`` set), ``compareAdminOrder``: each row by its
# ``ui_position``, then each row with none, in the Zoho picklist order. With no
# saved order, the place in the default pipeline (``placesOf``), then each
# stage that the pipeline does not list, in the picklist order. ⚠️ NEVER fold a
# NULL ``ui_position`` into ``position`` alone: that is the picklist order, and
# it changes the board.
#
# Lead statuses: the Mirror board is the effective position of
# ``core.EFFECTIVE_STATUS_COLUMNS`` (``toLeadLanes``), the retired rows last.
#
# Colour: Mirror draws ONLY ``ui_color``, then the stage type, then a name
# keyword, then gray (``stageAccent`` and ``resolveHue`` in
# ``src/lib/statusAccent.ts``). A stored ``gray`` would win in Native and paint
# a won lane gray. So the fold writes the hue that Mirror drew. The keyword
# regexes are those of ``keywordHue``, in its order. Its fourth regex gives
# gray, which the ELSE gives too. ``tests/fixtures/crm_cutover_board_order.json``
# runs through the SQL (R8) and through ``orderLanes`` and ``stageAccent``
# (vitest).

#: The rank of each deal stage in the Mirror board order, from 0.
_DEAL_RANK_CTE = (
    "flags AS (SELECT EXISTS (SELECT 1 FROM crm_deal_statuses o "
    "WHERE o.organization_id = CAST(:o AS uuid) AND o.ui_position IS NOT NULL) "
    "AS admin_order, (SELECT p.id FROM crm_pipelines p "
    "WHERE p.organization_id = CAST(:o AS uuid) "
    "ORDER BY p.is_default DESC, p.position, p.created_at, p.id LIMIT 1) AS pid), "
    "ranked AS (SELECT s.id, row_number() OVER (ORDER BY "
    "(s.hidden OR s.source_gone_at IS NOT NULL), "
    "CASE WHEN f.admin_order THEN s.ui_position IS NULL "
    "ELSE ps.position IS NULL END, "
    "CASE WHEN f.admin_order THEN s.ui_position ELSE ps.position END, "
    "s.position, s.name, s.id) - 1 AS rank "
    "FROM crm_deal_statuses s CROSS JOIN flags f "
    "LEFT JOIN crm_pipeline_stages ps ON ps.status_id = s.id "
    "AND ps.pipeline_id = f.pid "
    "WHERE s.organization_id = CAST(:o AS uuid))"
)

#: The rank of each lead status in the Mirror board order, from 0.
_LEAD_RANK_CTE = (
    "flags AS (SELECT EXISTS (SELECT 1 FROM crm_lead_statuses o "
    "WHERE o.organization_id = CAST(:o AS uuid) AND o.ui_position IS NOT NULL) "
    "AS admin_order), "
    "ranked AS (SELECT s.id, row_number() OVER (ORDER BY "
    "(s.hidden OR s.source_gone_at IS NOT NULL), "
    "CASE WHEN s.ui_position IS NOT NULL THEN s.ui_position "
    f"WHEN f.admin_order THEN {ADMIN_ORDER_TAIL} + s.position "
    "ELSE s.position END, s.name, s.id) - 1 AS rank "
    "FROM crm_lead_statuses s CROSS JOIN flags f "
    "WHERE s.organization_id = CAST(:o AS uuid))"
)

#: The hue that Mirror drew (``stageAccent(mode "mirror")``), over the alias
#: ``s``. It reads the OLD row, as each SET expression of an UPDATE does.
_HUE_SQL = (
    "COALESCE(NULLIF(s.ui_color, ''), CASE s.type "
    "WHEN 'won' THEN 'green' WHEN 'lost' THEN 'red' "
    "WHEN 'on_hold' THEN 'amber' WHEN 'ongoing' THEN 'blue' ELSE CASE "
    "WHEN lower(s.name) ~ '(done|complete|closed|finished|shipped)' THEN 'green' "
    "WHEN lower(s.name) ~ '(wait|block|hold|paused|stuck)' THEN 'amber' "
    "WHEN lower(s.name) ~ "
    "'(progress|doing|active|working|review|in[[:space:]-]?process)' THEN 'blue' "
    "ELSE 'gray' END END)"
)

#: The pipelines whose Mirror board shows two stages in the other order than
#: the one Native order. A saved order shows on every pipeline, so it is 0.
_OTHER_ORDER_SQL = (
    f"WITH {_DEAL_RANK_CTE}, members AS (SELECT ps.pipeline_id, "
    "ps.position AS place, r.rank FROM crm_pipeline_stages ps "
    "JOIN ranked r ON r.id = ps.status_id "
    "JOIN crm_deal_statuses s ON s.id = ps.status_id "
    "WHERE ps.organization_id = CAST(:o AS uuid) "
    "AND NOT (s.hidden OR s.source_gone_at IS NOT NULL)) "
    "SELECT count(DISTINCT a.pipeline_id) AS n FROM members a "
    "JOIN members b ON b.pipeline_id = a.pipeline_id "
    "AND a.place < b.place AND a.rank > b.rank "
    "WHERE NOT (SELECT admin_order FROM flags)"
)


class Split(BaseModel):
    """Rows that came from Zoho, and rows that members made in Metorite."""

    from_zoho: int = 0
    native: int = 0


class PartRow(BaseModel):
    label: str
    metorite: Split


class RecordRow(BaseModel):
    key: str
    label: str
    #: Zoho's count in the newest good run that counted this key. ``None`` when
    #: no good run did.
    zoho: int | None = None
    #: The age of that run, in seconds. ``None`` with ``zoho``.
    zoho_age_seconds: int | None = None
    metorite: Split
    #: The parts of the row, for the activities (notes, calls …).
    parts: list[PartRow] = []
    #: ``complete``, or the reason of :func:`module_state`.
    state: Literal[
        "complete", "held", "unavailable", "importing", "failed", "short", "refused",
    ] = "complete"
    #: The records that the newest good run did not write (``failed``).
    failed: int = 0
    refused: int = 0


class OtherRow(BaseModel):
    key: str
    label: str
    metorite: Split


class OwnerRow(BaseModel):
    name: str
    records: int


class LabelRow(BaseModel):
    key: str
    label: str
    singular: str | None = None


class IncompleteRow(BaseModel):
    """One count key that is not complete. The dialog offers "Leave out"."""

    key: str
    label: str
    reason: Reason


class SyncFacts(BaseModel):
    #: The end of the last good run. ``None`` when no run ended well.
    last_good_at: datetime | None = None
    age_seconds: int | None = None
    #: A run is open now.
    running: bool = False


class CutoverPreview(BaseModel):
    """``GET /crm/cutover/preview``. Counts, names and times. No token."""

    mode: Literal["mirror"] = "mirror"
    sync: SyncFacts
    records: list[RecordRow]
    other: list[OtherRow]
    unmatched_owners: list[OwnerRow]
    unmatched_owner_names: int = 0
    labels_kept: list[LabelRow]
    #: The label of each key that is not complete. The switch waits for them.
    not_complete: list[str]
    #: What stays in Zoho. Attachments first.
    stays_in_zoho: list[str]
    #: CRM-Z10b: each key that is not complete, with its reason.
    incomplete: list[IncompleteRow] = []
    #: CRM-Z10b: the org is on ``CRM_CUTOVER_ORGS``. The card shows the switch
    #: button only then.
    can_switch: bool = False
    #: CRM-Z10b: the pipelines that list stages in another order. Native mode
    #: has one order (D137).
    pipelines_in_another_order: int = 0


def _int(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


def _step(entry: Any) -> dict[str, int]:
    """The counts of one key. A run may store a plain number or an object."""
    if isinstance(entry, dict):
        return {k: v for k, v in ((k, _int(v)) for k, v in entry.items()) if v is not None}
    value = _int(entry)
    return {"seen": value} if value is not None else {}


def _is_count_key(key: str) -> bool:
    return key in _KEYS or key.startswith(MODULE_KEY_PREFIX)


def _count_key_of(module: str) -> str | None:
    """The count key of a changed cursor ``zoho:<entity>``, or None.

    A deleted cursor and every other cursor row (the field rules, the module
    counts, the deal roles …) give None.
    """
    from gateway.crm_sync import engine

    prefix = f"{engine.PROVIDER}:"
    if not module.startswith(prefix) or module.endswith(":deleted"):
        return None
    entity = module[len(prefix):]
    if entity in engine.ENTITY_ORDER:
        return engine.count_key(entity)
    return entity if entity.startswith(MODULE_KEY_PREFIX) else None


async def _newest_good_run(db: Any, org: str) -> Any:
    from gateway.crm_sync import runs

    row = (await db.execute(text(_NEWEST_OK_SQL), {"org": org})).first()
    return runs._row(row) if row is not None else None


async def module_state(
    db: Any, org: str, run: Any = None, *, final: bool = False,
) -> dict[str, Reason]:
    """Each count key that is not complete, with its reason. THE one rule.

    The preview and both POST routes call it. ``run`` is the run whose counts
    decide ``failed`` and a run's ``unavailable``: the final run in phase 2,
    else the newest good run. A key with two reasons gets the first of:
    held, unavailable, importing, failed, short, walk_short, not_read,
    roles_waiting.
    ``final`` is phase 2 only, and it adds ``not_read`` (:func:`_not_read`)
    and ``roles_waiting`` (:func:`roles_waiting`). It reads and writes
    nothing else.
    """
    from gateway.crm_sync import engine, holds, runs
    from gateway.routes.crm.sync import short_reads

    if run is None:
        run = await _newest_good_run(db, org)
    cursors = await runs.read_cursors(db, org, f"{engine.PROVIDER}:")
    states = [
        (key, row.state or {})
        for module, row in sorted(cursors.items())
        if (key := _count_key_of(module)) is not None
    ]
    counts = [
        (str(key), _step(entry))
        for key, entry in sorted(((run.counts or {}) if run is not None else {}).items())
        if _is_count_key(str(key))
    ]
    held = sorted(holds.held_keys(await holds.read(db, org)))
    short = [item.key for item in await short_reads(db, org)]
    not_read = (
        _not_read(run, cursors, await _profile_modules(db, org))
        if final and run is not None else []
    )
    roles = [ROLES_KEY] if final and run is not None and roles_waiting(run, cursors) else []
    # Each reason, in the order that decides which one a key shows.
    found: dict[str, Reason] = {}
    for reason, keys in (
        ("held", held),
        ("unavailable", [k for k, s in states if s.get("unavailable")]),
        ("unavailable", [k for k, c in counts if c.get("unavailable")]),
        ("importing", [k for k, s in states if s.get("in_progress")]),
        ("failed", [k for k, s in states if s.get("failed")]),
        ("failed", [k for k, c in counts if c.get("failed", 0) > 0]),
        ("short", short),
        # Round 6: a walk that read fewer records than Zoho holds is no
        # complete read (engine.WALK_SHORT_FLAG). A refusal after a complete
        # read (engine.DELETE_KEPT_FLAG) waits for nothing.
        ("walk_short", [k for k, c in counts if c.get("walk_short", 0) > 0]),
        # No "refused" here: see the module doc (round 6).
        ("not_read", not_read),
        ("roles_waiting", roles),
    ):
        for key in keys:
            found.setdefault(key, reason)  # type: ignore[arg-type]
    return found


def _not_read(run: Any, cursors: dict[str, Any], modules_body: Any) -> list[str]:
    """Phase 2: each key that the final *run* did not read (the module doc).

    A key counts when its cursor finished the first import: each core key,
    and each generic module of the newest profile. A held or an unavailable
    key keeps its own reason, because it comes first.

    ⚠️ The proof of a read is its END in this run (round 4), never a key in
    ``counts``: every core key is always in ``counts``, and an import-kind
    run inherits the module keys of the import before it. Each read that
    ends stamps ``checked_at`` in its cursor by the gateway's clock. The
    final run notes the time of its plan by the same clock
    (``counts[engine.RUN_FINAL_KEY]["planned_ms"]``). So a cursor whose
    ``checked_at`` is older, or missing, was not read in this run. A run
    with no plan time read nothing, as far as the switch knows.

    Two more rules. With ``counts[engine.RUN_ONLY_KEY]`` (a one-module run),
    each key outside it was not read. A generic module that waits for its
    fields (``fields_pending`` in the profile) was not read either: its
    fields wait when discovery ran short of credits and the previous profile
    did not mirror it, for example after a reconnect that lost its scope.
    """
    from gateway.crm_sync import engine

    run_counts = run.counts or {}
    only = run_counts.get(engine.RUN_ONLY_KEY)
    only_keys = set(only) if isinstance(only, dict) else None
    planned = _planned_at(run_counts.get(engine.RUN_FINAL_KEY))
    rows = [
        m for m in (modules_body if isinstance(modules_body, list) else [])
        if isinstance(m, dict) and m.get("generic") and m.get("api_name")
    ]
    generic = {f"{MODULE_KEY_PREFIX}{m['api_name']}" for m in rows}
    pending = {f"{MODULE_KEY_PREFIX}{m['api_name']}" for m in rows if m.get("fields_pending")}
    missed: set[str] = set()
    for module, row in cursors.items():
        key = _count_key_of(module)
        if key is None or row.initial_done_at is None:
            continue
        if key.startswith(MODULE_KEY_PREFIX) and key not in generic:
            continue  # the profile no longer lists it, so no run reads it
        if (
            (only_keys is not None and key not in only_keys)
            or not _read_ended(row, planned)
            or key in pending
        ):
            missed.add(key)
    return sorted(missed)


def _planned_at(mark: Any) -> datetime | None:
    """The plan time of a final run, from ``counts[engine.RUN_FINAL_KEY]``."""
    ms = mark.get("planned_ms") if isinstance(mark, dict) else None
    if not isinstance(ms, int) or isinstance(ms, bool):
        return None
    return datetime.fromtimestamp(ms / 1000, UTC)


def _read_ended(row: Any, planned: datetime | None) -> bool:
    """True when a read of this cursor ended at or after *planned*."""
    if planned is None:
        return False
    raw = (row.state or {}).get("checked_at")
    try:
        checked = datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return False
    if checked is None:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return checked >= planned


def roles_waiting(run: Any, cursors: dict[str, Any]) -> int:
    """Phase 2: the deals that still wait for their contacts from Zoho
    (round 4, P2-3). The roles step reads one deal for each credit, and it
    stops at ``engine.ROLES_RESERVE``. The rest wait in the queue of
    ``engine.ROLES_MODULE``. After the switch no run reads them, so such a
    deal keeps no linked contacts from Zoho.

    Round 5: the queue only. ``deal_contacts.skipped`` also counts a deal
    dropped after three failed reads and a deal deleted since, and neither
    waits. A deal that the budget skipped stays in the queue."""
    from gateway.crm_sync import engine

    queue = cursors.get(engine.ROLES_MODULE)
    return len((queue.state or {}).get("pending") or ()) if queue is not None else 0


async def _profile_modules(db: Any, org: str) -> Any:
    profile = await _one(db, _PROFILE_SQL, org)
    return from_jsonb(profile.modules) if profile is not None else None


def _key_labels(modules_body: Any) -> dict[str, str]:
    """The word of each count key: the source's label, else the CRM's word."""
    labels = labels_of(modules_body)
    words = {
        key: (labels[slug].label if slug in labels else fallback)
        for key, _table, fallback, slug in _CORE
    }
    words["tasks"] = "Tasks"
    words[ROLES_KEY] = "Contacts linked to deals"
    for m in modules_body if isinstance(modules_body, list) else ():
        if isinstance(m, dict) and m.get("api_name"):
            words[f"{MODULE_KEY_PREFIX}{m['api_name']}"] = str(m.get("label") or m["api_name"])
    return words


def _label_of(key: str, words: dict[str, str]) -> str:
    if key in words:
        return words[key]
    return key[len(MODULE_KEY_PREFIX):] if key.startswith(MODULE_KEY_PREFIX) else key


async def _counting_run(db: Any, org: str, key: str) -> Any:
    """The newest good run with an estimate for *key*, or ``None``."""
    from gateway.crm_sync import runs

    row = (await db.execute(text(_NEWEST_ESTIMATE_SQL), {"org": org, "key": key})).first()
    return runs._row(row) if row is not None else None


def _zoho_count(key: str, run: Any) -> int | None:
    """Zoho's count from a run that holds an estimate. Never ``seen``."""
    if run is None:
        return None
    return _int((run.estimate or {}).get(key))


def _split(row: Any) -> Split:
    return Split(from_zoho=int(row.from_zoho or 0), native=int(row.native or 0))


async def _record(
    db: Any, org: str, key: str, good: Any, incomplete: dict[str, Reason], now: datetime,
) -> dict[str, Any]:
    """The Zoho count, its age and the state of one key."""
    counting = await _counting_run(db, org, key)
    step = _step(good.counts.get(key)) if good is not None else {}
    age = None
    if counting is not None and counting.finished_at is not None:
        age = max(int((now - counting.finished_at).total_seconds()), 0)
    zoho = _zoho_count(key, counting)
    return {
        "zoho": zoho, "zoho_age_seconds": age if zoho is not None else None,
        "state": incomplete.get(key, "complete"),
        "failed": step.get("failed", 0), "refused": step.get("refused", 0),
    }


async def _one(db: Any, sql: str, org: str) -> Any:
    return (await db.execute(text(sql), {"org": org})).first()


async def preview(db: Any, org: str) -> CutoverPreview:
    """Build the preview from reads only. ``org`` is the bound tenant."""
    from gateway.crm_sync import runs

    due = await runs.due_info(db, org)
    # The newest run that ended well, however far back it is.
    good = await _newest_good_run(db, org)
    incomplete = await module_state(db, org, good)
    modules_body = await _profile_modules(db, org)
    labels = labels_of(modules_body)
    words = _key_labels(modules_body)

    activity_rows = (await db.execute(text(_ACTIVITY_SQL), {"org": org})).fetchall()
    records: list[RecordRow] = []
    for key, table, _fallback, _slug in _CORE:
        facts = await _record(db, org, key, good, incomplete, due.db_now)
        if key == "activities":
            parts = [
                PartRow(label=_ACTIVITY_LABELS.get(str(r.type), str(r.type).title()),
                        metorite=_split(r))
                for r in sorted(activity_rows, key=lambda r: str(r.type))
            ]
            split = Split(
                from_zoho=sum(p.metorite.from_zoho for p in parts),
                native=sum(p.metorite.native for p in parts),
            )
        else:
            parts = []
            split = _split(await _one(db, _SPLIT_SQL.format(table=table), org))
        records.append(RecordRow(
            key=key, label=words[key], metorite=split, parts=parts, **facts,
        ))

    module_rows = {
        f"{MODULE_KEY_PREFIX}{r.module}": _split(r)
        for r in (await db.execute(text(_MODULE_SQL), {"org": org})).fetchall()
    }
    module_keys = set(module_rows) | {
        k for k in incomplete if k.startswith(MODULE_KEY_PREFIX)
    } | {
        k for k in (good.counts if good is not None else {})
        if k.startswith(MODULE_KEY_PREFIX)
    }
    for key in sorted(module_keys):
        facts = await _record(db, org, key, good, incomplete, due.db_now)
        records.append(RecordRow(
            key=key, label=_label_of(key, words),
            metorite=module_rows.get(key, Split()), **facts,
        ))

    stages = await _one(db, _STAGES_SQL, org)
    views = await _one(db, _VIEWS_SQL, org)
    other = [
        OtherRow(key="pipelines", label="Pipelines",
                 metorite=_split(await _one(db, _PIPELINES_SQL, org))),
        OtherRow(key="stages", label="Pipeline stages",
                 metorite=Split(native=int(stages.n or 0))),
        OtherRow(key="deal_contacts", label="Contacts linked to deals",
                 metorite=_split(await _one(db, _LINKS_SQL, org))),
        OtherRow(key="fields", label="Fields",
                 metorite=_split(await _one(db, _FIELDS_SQL, org))),
        OtherRow(key="views", label="Saved views",
                 metorite=Split(native=int(views.n or 0))),
    ]

    owners = (await db.execute(
        text(_OWNER_SQL), {"org": org, "lim": _OWNER_LIMIT})).fetchall()
    names = await _one(db, _OWNER_TOTAL_SQL, org)

    mirrored = {
        f"{MODULE_KEY_PREFIX}{m['api_name']}"
        for m in (modules_body if isinstance(modules_body, list) else [])
        if isinstance(m, dict) and m.get("mirrored") and m.get("api_name")
    }
    core_modules = {e.zoho_module for e in _core_entities()}
    unread = sorted(
        str(m.get("label") or m["api_name"])
        for m in (modules_body if isinstance(modules_body, list) else [])
        if isinstance(m, dict) and m.get("api_name") and m.get("api_supported")
        and m.get("visible") is not False
        and m["api_name"] not in core_modules
        and f"{MODULE_KEY_PREFIX}{m['api_name']}" not in mirrored
    )
    other_order = (await db.execute(text(_OTHER_ORDER_SQL), {"o": org})).first()

    last_good = good.finished_at if good is not None else None
    age = None
    if last_good is not None:
        age = max(int((due.db_now - last_good).total_seconds()), 0)
    gaps = [
        IncompleteRow(key=key, label=_label_of(key, words), reason=reason)
        for key, reason in incomplete.items()
    ]
    return CutoverPreview(
        sync=SyncFacts(last_good_at=last_good, age_seconds=age, running=due.running),
        records=records,
        other=other,
        unmatched_owners=[OwnerRow(name=str(r.name), records=int(r.records)) for r in owners],
        unmatched_owner_names=int(names.names or 0) if names is not None else 0,
        labels_kept=[
            LabelRow(key=k, label=v.label, singular=v.singular_label)
            for k, v in sorted(labels.items())
        ],
        not_complete=[row.label for row in gaps],
        stays_in_zoho=["Attachments", *unread],
        incomplete=gaps,
        can_switch=cutover_allowed(org),
        pipelines_in_another_order=int(other_order.n or 0) if other_order else 0,
    )


def _core_entities() -> list[Any]:
    from gateway.routes.crm.core import ENTITIES

    return [e for e in ENTITIES.values() if e.zoho_module]


def _organization(user: UserContext) -> str:
    if not user.organization_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: you are in no organization.",
        )
    return str(user.organization_id)


@router.get(
    "/cutover/preview", response_model=CutoverPreview, dependencies=_GUARDS,
)
async def get_cutover_preview(
    user: UserContext = Depends(get_current_user),
) -> CutoverPreview:
    """What moves to Metorite when the org leaves Zoho, and what does not."""
    org = _organization(user)
    async with _tenant_session() as db:
        settings = await read_settings(db, org)
        if getattr(settings, "mode", None) != "mirror":
            raise HTTPException(status_code=409, detail={
                "code": "not_mirror",
                "message": "The cutover is for a CRM that mirrors Zoho.",
            })
        return await preview(db, org)


# ── The switch (CRM-Z10b) ───────────────────────────────────────────────────


class StartIn(BaseModel):
    """The body of ``POST /crm/cutover/start``. No other field is allowed (R11)."""

    model_config = ConfigDict(extra="forbid")

    #: The word the admin typed. Only :data:`CUTOVER_CONFIRM` passes.
    confirm: str = ""
    #: The count keys that the admin ticked in "Leave out". Each must be a
    #: key that is not complete.
    leave_out: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=200,
    )


class SwitchIn(StartIn):
    """The body of ``POST /crm/cutover``. No other field is allowed (R11)."""

    #: The run that phase 1 started. Phase 2 switches only when the newest run
    #: of the org IS this run. A retry after the switch needs none.
    run_id: str | None = Field(default=None, max_length=64)


class StartOut(BaseModel):
    #: The final run that this call started. The dialog sends it to phase 2.
    run_id: str


class StatusOut(BaseModel):
    """``GET /crm/cutover/status``. The dialog reads it every 3 seconds."""

    state: Literal["idle", "running", "ready", "failed", "done"]
    run_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: The safe code of a run that failed or stopped (``budget``,
    #: ``needs_reconnect``, ``mode_changed``, ``shutdown`` …). Never free text.
    error: str | None = None
    #: Seconds since the newest run ended. ``None`` while it runs.
    age_seconds: int | None = None
    #: The status of the Zoho connection, or ``none``. ``done`` with a status
    #: other than ``disconnected`` means that phase 3 waits.
    zoho_connection: str = "none"


class SwitchOut(BaseModel):
    status: Literal["switched", "already_done"]
    #: What Zoho said to the revoke. ``None`` when this call closed nothing
    #: (Zoho was already closed), or when the close failed.
    revoked: Literal["confirmed", "not_confirmed", "not_configured"] | None = None
    zoho_connection: str = "none"


def _refuse(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _check_confirm(confirm: str) -> None:
    if confirm != CUTOVER_CONFIRM:
        raise _refuse(
            422, "confirm_required",
            f"Type {CUTOVER_CONFIRM} to confirm. Nothing changed.",
        )


_NOT_MIRROR = ("not_mirror", "The switch is for a CRM that mirrors Zoho.")


def _check_leave_out(
    incomplete: dict[str, Reason], leave_out: frozenset[str], words: dict[str, str],
    waiting_deals: int = 0,
) -> None:
    """422 for a key that is not on the list, 409 for a key that waits.

    The row of ``deal_contacts`` also says how many deals wait (``count``).
    """
    unknown = sorted(leave_out - set(incomplete))
    if unknown:
        raise _refuse(
            422, "unknown_leave_out",
            "Only a module that is not complete can be left out. Load the "
            "preview again.",
            keys=unknown,
        )
    waiting = [k for k in incomplete if k not in leave_out]
    if waiting:
        raise _refuse(
            409, "module_incomplete",
            "Some modules are not complete. Fix them, or tick them in Leave out.",
            modules=[
                {
                    "key": k, "label": _label_of(k, words), "reason": incomplete[k],
                    **({"count": waiting_deals} if k == ROLES_KEY else {}),
                }
                for k in waiting
            ],
        )


#: The generic modules that hold records here.
_RECORD_MODULES_SQL = (
    "SELECT DISTINCT module FROM crm_module_records "
    "WHERE organization_id = CAST(:org AS uuid)"
)


async def _known_keys(db: Any, org: str, words: dict[str, str]) -> frozenset[str]:
    """Each count key that the org knows: the core keys, each module of the
    profile, each module with records, and each key of a cursor."""
    from gateway.crm_sync import engine, runs

    cursors = await runs.read_cursors(db, org, f"{engine.PROVIDER}:")
    records = (await db.execute(text(_RECORD_MODULES_SQL), {"org": org})).fetchall()
    return frozenset(words) | {
        f"{MODULE_KEY_PREFIX}{r.module}" for r in records
    } | {
        key for module in cursors if (key := _count_key_of(module)) is not None
    }


async def _phase_two_keys(db: Any, org: str, modules_body: Any) -> frozenset[str]:
    """The keys that only phase 2 can find not complete: each key whose
    first import is done (``not_read``), and ``deal_contacts``
    (``roles_waiting``). Phase 1 takes a Leave out tick for each of them,
    so the admin can tick it after a 409 of phase 2 and press again."""
    from gateway.crm_sync import engine, runs

    cursors = await runs.read_cursors(db, org, f"{engine.PROVIDER}:")
    # A run with no plan time read nothing, so each such key is "not read".
    return frozenset(_not_read(SimpleNamespace(counts={}), cursors, modules_body)) | {ROLES_KEY}


async def _import_open(db: Any, org: str, skip: frozenset[str]) -> bool:
    """True while the first import of a key that the switch reads is open."""
    from gateway.crm_sync import engine, holds, runs

    cursors = await runs.read_cursors(db, org, f"{engine.PROVIDER}:")
    held = holds.held_keys(await holds.read(db, org)) | skip
    last_ok = await runs.last_success_at(db, org)
    kind, _due = engine.plan(cursors, datetime.now(UTC), last_ok, held)
    if kind == runs.KIND_IMPORT:
        return True
    for module, row in cursors.items():
        key = _count_key_of(module)
        if key is None or not key.startswith(MODULE_KEY_PREFIX) or key in held:
            continue
        if (row.state or {}).get("unavailable"):
            continue
        if row.initial_done_at is None:
            return True
    return False


async def _check_keys(
    db: Any, org: str, leave_out: frozenset[str], run: Any = None,
) -> frozenset[str]:
    """Phase 1 steps 3 to 5, and phase 2 step 6: the same checks.

    Phase 1 (no *run*) refuses a left-out key that is complete with 422. In
    phase 2 (the final *run*), a known key that is complete is one that the
    final sync made complete, so it is dropped and not refused. A key that
    the org does not know is a 422 in both. Returns the keys left out in fact.
    Only phase 2 adds ``not_read`` and ``roles_waiting`` (``final``). So
    phase 1 also takes a tick for such a key (:func:`_phase_two_keys`): the
    admin ticks it after a 409 of phase 2, and presses again.
    """
    from gateway.crm_sync import engine, runs

    modules_body = await _profile_modules(db, org)
    incomplete = await module_state(db, org, run, final=run is not None)
    words = _key_labels(modules_body)
    waiting_deals = 0
    if run is not None and ROLES_KEY in incomplete:
        cursors = await runs.read_cursors(db, org, f"{engine.PROVIDER}:")
        waiting_deals = roles_waiting(run, cursors)
    extra = leave_out - set(incomplete)
    if run is not None:
        extra -= await _known_keys(db, org, words)
    else:
        extra -= await _phase_two_keys(db, org, modules_body)
    if extra:
        _check_leave_out(incomplete, leave_out, words, waiting_deals)
    left_out = leave_out & frozenset(incomplete)
    if await _import_open(db, org, left_out):
        raise _refuse(
            409, "import_open",
            "The first import from Zoho has not finished. Wait for it, then "
            "switch.",
        )
    _check_leave_out(incomplete, left_out, words, waiting_deals)
    return left_out


def _check_connection(connection: Any) -> None:
    status = getattr(connection, "status", None)
    if status == store.STATUS_CONNECTED:
        return
    if status == store.STATUS_NEEDS_RECONNECT:
        raise _refuse(
            409, "needs_reconnect",
            "Zoho asks you to connect again. Reconnect Zoho, then switch.",
        )
    raise _refuse(
        409, "not_connected",
        "Zoho is not connected, so the final sync cannot run. Connect Zoho first.",
    )


def _start_final_run(handle: Any) -> asyncio.Task[Any]:
    """Run the final sync in the background, and nothing after it.

    ⚠️ Not ``scheduler.spawn``: that helper starts the label, vector,
    mapping, question and digest steps, and the final run must not.
    """
    from gateway.crm_sync import engine, scheduler

    return scheduler._track(asyncio.get_running_loop().create_task(
        engine.execute(handle, should_stop=scheduler._stopping.is_set),
        name="crm-cutover-final-run",
    ))


@router.post(
    "/cutover/start", response_model=StartOut, status_code=202,
    dependencies=_SWITCH_GUARDS,
)
async def start_cutover(
    payload: StartIn, user: UserContext = Depends(get_current_user),
) -> StartOut:
    """Phase 1: the checks, then the final sync. See the module doc.

    ⚠️ No shortcut for a fresh run. A one-module run ("Sync it on its own")
    is as fresh as a full one, and phase 2 would take it as the final sync.
    """
    from gateway.crm_sync import engine, runs

    org = _organization(user)
    _check_confirm(payload.confirm)
    leave_out = frozenset(payload.leave_out)
    async with _tenant_session() as db:
        settings = await read_settings(db, org)
        if getattr(settings, "mode", None) != "mirror":
            raise _refuse(409, *_NOT_MIRROR)
        _check_connection(await store.get(db, org, PROVIDER, OWNING_APP))
        # Round 6: only the ticks that phase 1 takes (a key not complete).
        left_out = await _check_keys(db, org, leave_out)
    try:
        # The final sync: each module now, with no wait of an hour (round 3).
        # Round 5: the final sync skips the keys that the admin left out.
        handle = await engine.acquire(org, final=True, leave_out=left_out)
    except runs.SyncRunning as exc:
        raise _refuse(
            409, "sync_running", "A sync runs now. Wait for it to finish.",
        ) from exc
    except engine.NotConnected as exc:
        if exc.args and exc.args[0] == "mode":
            raise _refuse(409, *_NOT_MIRROR) from exc
        raise _refuse(
            409, "not_connected",
            "Zoho is not connected, so the final sync cannot run.",
        ) from exc
    _start_final_run(handle)
    _log.info("crm.cutover.final_run_started run=%s", handle.run_id)
    return StartOut(run_id=handle.run_id)


@router.get(
    "/cutover/status", response_model=StatusOut, dependencies=_SWITCH_GUARDS,
)
async def get_cutover_status(
    user: UserContext = Depends(get_current_user),
) -> StatusOut:
    """The newest run and the switch. It writes nothing."""
    from gateway.crm_sync import runs

    org = _organization(user)
    async with _tenant_session() as db:
        switched = (await db.execute(text(_STATE_SQL), {"o": org})).first()
        connection = await store.get(db, org, PROVIDER, OWNING_APP)
        latest = await runs.latest(db, org)
        db_now = (await db.execute(text("SELECT now() AS n"))).scalar()
    zoho = connection.status if connection is not None else "none"
    base: dict[str, Any] = {"zoho_connection": zoho}
    if latest is not None:
        base.update(
            run_id=latest.id, started_at=latest.started_at,
            finished_at=latest.finished_at,
            age_seconds=(
                max(int((db_now - latest.finished_at).total_seconds()), 0)
                if latest.finished_at is not None else None
            ),
        )
    if switched is not None and switched.mode == "native" and switched.cutover_at:
        return StatusOut(state="done", **base)
    if latest is None:
        return StatusOut(state="idle", **base)
    if latest.status == runs.STATUS_RUNNING:
        beat = latest.heartbeat_at or latest.started_at
        stale = beat is not None and (
            (db_now - beat).total_seconds() > runs.STALE_AFTER_SECONDS
        )
        if stale:
            return StatusOut(state="failed", error="stale", **base)
        return StatusOut(state="running", **base)
    if latest.status == runs.STATUS_OK:
        fresh = (base.get("age_seconds") or 0) <= FRESH_MINUTES * 60
        return StatusOut(state="ready" if fresh else "idle", **base)
    return StatusOut(state="failed", error=latest.error or "failed", **base)


# ── Phase 2: one transaction ────────────────────────────────────────────────

#: The mode and the stamp, for the status read.
_STATE_SQL = (
    "SELECT mode, cutover_at FROM crm_settings WHERE organization_id = CAST(:o AS uuid)"
)
#: Phase 2 step 2. After the advisory connect lock, as the start over.
_SWITCH_LOCK_SQL = _STATE_SQL + " FOR UPDATE"
#: Step 4: any open run, a stale one too. A phase 1 retry takes it over.
_OPEN_RUN_SQL = (
    "SELECT 1 FROM crm_sync_runs WHERE organization_id = CAST(:o AS uuid) "
    "AND status = 'running' LIMIT 1"
)
#: Step 5: the newest run, and whether it ended well in the window, by the
#: database clock. The caller also checks that its id is the run of phase 1.
_FINAL_RUN_SQL = (
    f"SELECT {_COLUMNS}, (status = 'ok' AND finished_at IS NOT NULL AND "
    "finished_at >= now() - make_interval(mins => :m)) AS fresh "
    "FROM crm_sync_runs WHERE organization_id = CAST(:o AS uuid) "
    "ORDER BY started_at DESC LIMIT 1"
)
_DEAL_CONTACTS_COUNT_SQL = (
    "SELECT count(*) FROM crm_deal_contacts WHERE organization_id = CAST(:o AS uuid)"
)

#: The SQLSTATEs of a write that runs into the switch: a foreign key (23503)
#: and a deadlock (40P01). The transaction rolls back, as the start over does.
_CHANGED_STATES = frozenset({"23503", "40P01"})


@dataclass
class SwitchFacts:
    """What phase 2 did, for the one audit line."""

    run_id: str
    t0: datetime
    counts: dict[str, int]
    leave_out: list[str]
    folded: dict[str, int] = field(default_factory=dict)
    labels: bool = False
    fields: int = 0
    rewound: int = 0


async def fold_cutover_overlay(db: Any, org: str) -> dict[str, int]:
    """THE FOLD (see the comment above :data:`_DEAL_RANK_CTE`). Step 7.

    One UPDATE for each status table. ``position`` gets the Mirror board
    order, ``probability`` and ``color`` get the values that Mirror showed,
    and every ``ui_*`` value goes to NULL. ``hidden``, ``ext_id``,
    ``source_gone_at``, ``required_fields`` and ``max_dwell_days`` stay.

    ⚠️ A raw write to two guarded tables, on purpose: it runs inside phase 2,
    after the mode check under ``FOR UPDATE``. Only this module names it
    (``test_crm_upsert_from_source_fence.py``).
    """
    folded: dict[str, int] = {}
    for table, ranked in (
        ("crm_deal_statuses", _DEAL_RANK_CTE), ("crm_lead_statuses", _LEAD_RANK_CTE),
    ):
        result = await db.execute(text(
            f"WITH {ranked} UPDATE {table} s SET position = r.rank * 10, "
            "probability = COALESCE(s.ui_probability, s.probability), "
            f"color = {_HUE_SQL}, ui_position = NULL, ui_color = NULL, "
            "ui_probability = NULL FROM ranked r WHERE s.id = r.id "
            "AND s.organization_id = CAST(:o AS uuid)"
        ), {"o": org})
        folded[table] = int(getattr(result, "rowcount", 0) or 0)
    return folded


async def _store_labels(db: Any, org: str) -> bool:
    """Step 8: the source's names of the core records (D133.1).

    The shape of ``PUT /crm/settings/labels``. No profile, or no label,
    stores NULL. It reads the profile, never ``source_labels``, which needs
    ``CRM_FILL_RANK_ORGS`` and Mirror mode.
    """
    labels = labels_of(await _profile_modules(db, org))
    stored = (
        json.dumps({slug: name.model_dump() for slug, name in labels.items()})
        if labels else None
    )
    await db.execute(text(
        "UPDATE crm_settings SET labels = CAST(:labels AS jsonb) "
        "WHERE organization_id = CAST(:o AS uuid)"), {"o": org, "labels": stored})
    return stored is not None


async def own_cutover_fields(db: Any, org: str) -> int:
    """Step 9: each field becomes a Metorite field. ``provider_api_name``
    stays. ⚠️ A raw write to a guarded table, inside phase 2. Only this
    module names it."""
    result = await db.execute(text(
        "UPDATE crm_field_defs SET source = 'metorite', updated_at = now() "
        "WHERE organization_id = CAST(:o AS uuid) "
        "AND source IS DISTINCT FROM 'metorite'"), {"o": org})
    return int(getattr(result, "rowcount", 0) or 0)


async def _rewind_watermarks(db: Any, org: str, t0: datetime) -> int:
    """Step 10 (§3.4.1): mail after ``T0`` is considered again in Native mode.

    Only a watermark past ``T0`` moves, and never ``last_run_at`` or
    ``activated_at``.
    """
    result = await db.execute(text(
        "UPDATE crm_auto_lead_cursors SET processed_watermark = :t0, "
        "updated_at = now() WHERE organization_id = CAST(:o AS uuid) "
        "AND processed_watermark > :t0"), {"o": org, "t0": t0})
    return int(getattr(result, "rowcount", 0) or 0)


async def _set_native(db: Any, org: str, admin: str | None) -> None:
    """Step 11. ``access``, ``access_groups`` and ``reset_at`` stay."""
    await db.execute(text(
        "UPDATE crm_settings SET mode = 'native', cutover_at = now(), "
        "cutover_by = :admin, connection_id = NULL, updated_by = :admin, "
        "updated_at = now() WHERE organization_id = CAST(:o AS uuid)"),
        {"o": org, "admin": admin})


async def _switch(
    org: str, admin: str | None, leave_out: frozenset[str], run_id: str | None,
) -> SwitchFacts | None:
    """Phase 2. Returns None when the org switched before (``already_done``).

    *run_id* is the run that phase 1 started. The newest run must be that
    run, so a one-module run or a scheduled run that ended after it fails
    closed with ``final_sync_missing``. It must also carry
    ``counts[engine.RUN_FINAL_KEY]``, so a run that phase 1 did not start
    as the final sync fails closed in the same way.
    """
    from gateway.crm_sync import engine, runs

    async with _tenant_session() as db:
        await db.execute(text(_CONNECT_LOCK_SQL), {"key": f"crm.connect:{org}:{PROVIDER}"})
        row = (await db.execute(text(_SWITCH_LOCK_SQL), {"o": org})).first()
        if row is None:
            raise _refuse(409, "settings_missing", "This CRM has no setup to switch.")
        # Before the run checks: a retry after the commit needs no run.
        if row.mode == "native" and row.cutover_at is not None:
            return None
        if row.mode != "mirror":
            raise _refuse(409, *_NOT_MIRROR)
        if (await db.execute(text(_OPEN_RUN_SQL), {"o": org})).first() is not None:
            raise _refuse(
                409, "run_open", "A sync runs now. Wait for it to finish. Nothing changed.",
            )
        final = (await db.execute(text(_FINAL_RUN_SQL), {"o": org, "m": FRESH_MINUTES})).first()
        run = runs._row(final) if final is not None else None
        if (
            final is None or run is None or not final.fresh or not run_id
            or str(final.id) != run_id
            # Round 3: only a run that phase 1 started as the final sync
            # (``engine.acquire(final=True)``) read each module now.
            or not (run.counts or {}).get(engine.RUN_FINAL_KEY)
        ):
            raise _refuse(
                409, "final_sync_missing",
                "The final sync of this switch must be the newest sync, and it "
                f"must end well in the last {FRESH_MINUTES} minutes. Start the "
                "switch again. Nothing changed.",
            )
        left_out = await _check_keys(db, org, leave_out, run)
        counts = await count_records(db, org)
        counts["deal_contacts"] = int(
            (await db.execute(text(_DEAL_CONTACTS_COUNT_SQL), {"o": org})).scalar() or 0
        )
        facts = SwitchFacts(
            run_id=run.id, t0=run.started_at, counts=counts, leave_out=sorted(left_out),
        )
        facts.folded = await fold_cutover_overlay(db, org)
        facts.labels = await _store_labels(db, org)
        facts.fields = await own_cutover_fields(db, org)
        facts.rewound = await _rewind_watermarks(db, org, run.started_at)
        await _set_native(db, org, admin)
    return facts


# ── Phase 3 and the audit ───────────────────────────────────────────────────


def _audit(admin: str | None, org: str, action: str, payload: dict[str, Any]) -> None:
    """One audit line. A backend that is down never undoes the switch."""
    try:
        from acb_audit import AuditEvent, record

        record(AuditEvent(
            actor=f"user:{admin or ''}",
            action=action,
            target=f"crm:{org}",
            payload={"organization_id": org, **payload},
            organization_id=org,
        ))
    except Exception as exc:
        _log.warning("crm.cutover.audit_failed action=%s error=%s", action, type(exc).__name__)


async def _connection_status(org: str) -> str:
    try:
        async with _tenant_session() as db:
            row = await store.get(db, org, PROVIDER, OWNING_APP)
    except Exception:
        return "unknown"
    return row.status if row is not None else "none"


async def _close_zoho(org: str, admin: str | None) -> tuple[str | None, str]:
    """Phase 3. It never raises: a failure leaves a Native org and a token."""
    try:
        connection, outcome = await disconnect_zoho_reporting(org)
    except Exception as exc:
        _log.warning("crm.cutover.phase3_pending error=%s", type(exc).__name__)
        return None, await _connection_status(org)
    if outcome is not None:
        _audit(admin, org, "crm.cutover.disconnect", {"revoked": outcome})
    return outcome, connection.status if connection is not None else "none"


@router.post(
    "/cutover", response_model=SwitchOut, dependencies=_SWITCH_GUARDS,
)
async def switch_to_metorite(
    payload: SwitchIn, user: UserContext = Depends(get_current_user),
) -> SwitchOut:
    """Phases 2 and 3: the switch, then close Zoho. See the module doc."""
    org = _organization(user)
    _check_confirm(payload.confirm)
    admin = (user.email or "").strip().lower() or None
    try:
        facts = await _switch(
            org, admin, frozenset(payload.leave_out), payload.run_id,
        )
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code in _CHANGED_STATES:
            raise _refuse(
                409, "crm_changed",
                "A change was saved while the CRM switched. Nothing changed. Try again.",
            ) from exc
        raise
    if facts is not None:
        _audit(admin, org, "crm.cutover", {
            "counts": facts.counts,
            "leave_out": facts.leave_out,
            "t0": facts.t0.isoformat() if facts.t0 else None,
            "final_run": facts.run_id,
            "folded": facts.folded,
            "labels": facts.labels,
            "fields": facts.fields,
            "watermarks_rewound": facts.rewound,
        })
        _log.info("crm.cutover.switched")
    revoked, zoho = await _close_zoho(org, admin)
    return SwitchOut(
        status="switched" if facts is not None else "already_done",
        revoked=revoked, zoho_connection=zoho,
    )
