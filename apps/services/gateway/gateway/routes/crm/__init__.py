"""CRM route package — the gateway `/crm` API (native CRM, Zoho's successor).

Spec: ``project-docs/specs/crm_app.md`` (WS-26a builds §3 + §4).

Same layout as ``routes/tasks`` and ``routes/notes``: ``core`` is the leaf and
the feature modules register their routes on the shared ``router`` as an import
side effect.

Unlike ``routes/workflows``, **import order here is NOT load-bearing**: every
path in this package is a literal per entity (``/crm/leads/{id}``, never
``/crm/{entity}/{id}``), so no route can shadow another and alphabetising this
list can never change which handler answers a request. ``records`` imports
``pipeline`` for the status-transition semantics, which is why Python loads
``pipeline`` first either way.

``import_zoho`` (the Zoho backfill + the shared Zoho→native mapping) and
``sync_zoho`` (the two-way sync engine and its ``POST /crm/sync/zoho``) are
WS-26b. ``broker_handlers`` is deliberately NOT imported here: it registers no
routes, and ``main.py`` calls its ``register_crm_broker_handlers()`` directly
at startup exactly the way it calls the tasks app's.

``deal_contacts.py`` is WS-26c's API addendum — the read/write surface for
``crm_deal_contacts``, which 26a created and only the convert path ever wrote
to.

``stage_metadata.py`` is WS-26f — ``POST /crm/import/zoho/stages``, the
pipeline repair that reads the tenant's real lane order out of Zoho's settings
API. It is a sibling of ``import_zoho`` rather than part of it because it
imports records nowhere: it rewrites native config, and its one deal-row write
(the ``closed_at`` proxy) deliberately bypasses ``core.update_row``.

``export.py`` is WS-26i-export — ``GET /crm/export/{entity}.csv``, the
filtered-list CSV export. Read-only, no migration, and the one WS-26i item that
touches the live Zoho tenant not at all. Its ``export/`` segment comes FIRST for
the shadowing reason above: ``/crm/leads/export.csv`` would sit under
``/crm/leads/{record_id}``.

``fields.py`` is WS-53 CRM-Q4 — ``GET /crm/fields/{entity}``, the field
definitions that the sync and the field studio write. Read only. Since CRM-C4a,
``?layout=1`` returns the sections, the system fields and the custom fields.

``field_studio.py`` is WS-53 CRM-C4a — the field studio of a Native CRM:
``POST``, ``PATCH`` and ``DELETE`` on ``/crm/fields/{entity}[/{key}]``, the
restore, ``PUT /crm/field-layout/{entity}`` and ``/crm/field-sections/{entity}``.
Admin only, behind ``CRM_FIELD_STUDIO_ORGS``, and 409 in Mirror mode. It also
holds the rules of the ``custom`` write path, which ``records.py`` calls.

``modules.py`` is WS-53 CRM-Q6b — ``GET /crm/modules`` and the read-only
record routes of each mirrored generic Zoho module (``crm_module_records``).
Its paths start with the literal ``modules/`` segment, so no route shadows it.

``field_rules.py`` is WS-53 CRM-Q7a — ``GET``, ``POST`` and ``PATCH`` on
``/crm/field-rules``, the Field map of the mirror. Admin only, behind
``CRM_MIRROR``, and Mirror mode only. It writes ``crm_field_rules`` and never a
CRM record. The sync applies an approved rule.

``mapping_plans.py`` is WS-53 CRM-Q18-1 — ``GET`` and ``POST
/crm/mapping/plans`` and the approve and restore of a version (table
``crm_mapping_plans``). It has the guards of the Field map. Its ``commit`` is
the one approve, and ``field_rules.py`` calls it too.

``suggestions.py`` is WS-53 CRM-Q20-2 — ``GET /crm/mapping/suggestions`` and
the approve, the Undo and the dismiss of one slot. It has the guards of the
Field map, and it approves through ``mapping_plans.commit``.

``reports.py`` is WS-26g — the four read-only ``/crm/reports/*`` blocks
(forecast by stage, funnel, win/loss, owner leaderboard) over
``crm_status_changes`` and ``crm_deals``. It writes nothing and adds no
migration; the weighted-₹ expression it shares with ``pipeline`` lives in
``core.WEIGHTED_SQL`` so there is exactly one copy of the formula.

``settings.py`` is WS-53 CRM-T5 — ``GET`` and ``PATCH /crm/settings``, the
organization's CRM mode and its access rule, admin only. Its two helpers
register no routes: ``access.py`` holds the router dependency that ``core``
runs third, and ``seed.py`` holds the starting pipeline that the change to
Native mode writes.

``connection.py`` is WS-53 CRM-Z2 — ``GET /crm/connection``, the Zoho
connect (``POST /crm/connection/zoho/start`` and the callback
``GET /crm/oauth/zoho/callback``) and ``POST /crm/connection/disconnect``. Each
route is behind ``CRM_MIRROR``, default OFF. The credential lives in
``gateway/connections/store.py``, the only reader of the blob.

``pipelines.py`` is WS-53 CRM-Q1 and CRM-Q2 — ``GET /crm/pipelines``, the three
Native-mode writes on a pipeline and its stages, and ``GET /crm/source-profile``
(behind ``CRM_MIRROR``).

``sync.py`` is WS-53 CRM-Z5 — ``GET /crm/sync/status`` and ``POST /crm/sync/zoho``
(Sync now). Both are behind ``CRM_MIRROR``. The engine they start lives in
``gateway/crm_sync/``, which imports ``routes.crm.core``, so ``sync.py`` imports
it inside its handlers.

``views.py`` is WS-53 CRM-Q8 — ``GET``, ``POST``, ``PATCH`` and ``DELETE`` on
``/crm/views``, the saved views of the lists and boards (table ``crm_views``).
A view is not a CRM record, so the routes work in Mirror mode as well.

``aggregate.py`` is WS-53 CRM-C7 — ``GET /crm/aggregate`` and
``GET /crm/aggregate/stage-dwell``, the totals that an agent reads. Read only.
It builds its filters with ``records.list_query``, the one list builder.
``settings.py`` also holds ``GET /crm/mode``, the mode for any admitted member.

``convert_mapping.py`` is WS-53 CRM-C6a — ``GET`` and ``PUT
/crm/conversion-mapping``, the lead-conversion mapping of a Native CRM (table
``crm_convert_mappings``). Admin only, behind ``CRM_FIELD_STUDIO_ORGS``, and
409 in Mirror mode. ``pipeline.convert_lead`` applies the map.

``note_search.py`` is WS-53 CRM-Q27 — ``GET /crm/notes/search``, the notes
nearest to a query by meaning (table ``crm_note_vectors``). Read only. An org
outside ``CRM_NOTE_SEARCH_ORGS`` gets ``degraded`` with no embed call. Its
``notes/`` segment is a literal, and no other route uses it.
"""

from gateway.routes.crm import activities as _activities  # noqa: F401
from gateway.routes.crm import admin as _admin  # noqa: F401
from gateway.routes.crm import aggregate as _aggregate  # noqa: F401
from gateway.routes.crm import connection as _connection  # noqa: F401
from gateway.routes.crm import convert_mapping as _convert_mapping  # noqa: F401
from gateway.routes.crm import deal_contacts as _deal_contacts  # noqa: F401
from gateway.routes.crm import export as _export  # noqa: F401
from gateway.routes.crm import field_rules as _field_rules  # noqa: F401
from gateway.routes.crm import field_studio as _field_studio  # noqa: F401
from gateway.routes.crm import fields as _fields  # noqa: F401
from gateway.routes.crm import import_zoho as _import_zoho  # noqa: F401
from gateway.routes.crm import mapping_plans as _mapping_plans  # noqa: F401
from gateway.routes.crm import modules as _modules  # noqa: F401
from gateway.routes.crm import note_search as _note_search  # noqa: F401
from gateway.routes.crm import pipeline as _pipeline  # noqa: F401
from gateway.routes.crm import pipelines as _pipelines  # noqa: F401
from gateway.routes.crm import records as _records  # noqa: F401
from gateway.routes.crm import reports as _reports  # noqa: F401
from gateway.routes.crm import settings as _settings  # noqa: F401
from gateway.routes.crm import stage_metadata as _stage_metadata  # noqa: F401
from gateway.routes.crm import suggestions as _suggestions  # noqa: F401
from gateway.routes.crm import sync as _sync  # noqa: F401
from gateway.routes.crm import sync_zoho as _sync_zoho  # noqa: F401
from gateway.routes.crm import views as _views  # noqa: F401
from gateway.routes.crm.core import router

__all__ = ["router"]
