"""The seed of the founder question set: the dataset as SQL rows (WS-53 CRM-Q30).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 items 8 and 11.

:func:`statements` turns :func:`dataset.build` into the rows of one org: the
Mirror settings, a Zoho connection, the source profile, the field
definitions, the module records, the core contacts and the notes. Each value
is a bound parameter.

This module opens no connection. The R8 test runs the statements on the
connection of its fixture. ``run.py --seed`` runs them in
``acb_common.db.tenant_session(org)``, the one seam, and only on a database
of this machine.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Any

from evals.crm_founder_questions.dataset import (
    Dataset,
    FieldDef,
    Record,
    build,
    profile,
)

#: The author of each seeded note.
SEED_AUTHOR = "seed@founder-evals.test"

Statement = tuple[str, list[dict[str, Any]]]

_SETTINGS = (
    "INSERT INTO crm_settings (organization_id, mode, access) "
    "VALUES (CAST(:o AS uuid), 'mirror', 'everyone') "
    "ON CONFLICT (organization_id) DO UPDATE SET mode = 'mirror', access = 'everyone'"
)

_PROFILE = (
    "WITH conn AS (INSERT INTO integration_connections (organization_id, provider, "
    "owning_app) VALUES (CAST(:o AS uuid), 'zoho', 'crm') RETURNING id) "
    "INSERT INTO crm_source_profiles (organization_id, connection_id, version, profile, "
    "profile_hash) SELECT CAST(:o AS uuid), conn.id, 1, CAST(:p AS jsonb), :h FROM conn"
)

#: The cursor of a module whose full read ended short (CRM-Q24): Zoho
#: counts more records than the connected user can read.
_SHORT = (
    "INSERT INTO crm_sync_cursors (organization_id, module, last_run_at, last_status, "
    "cursor_state) VALUES (CAST(:o AS uuid), :m, now(), 'ok', CAST(:s AS jsonb))"
)

_DEF = (
    "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, options, "
    "source, provider_api_name, position, is_system) VALUES (CAST(:o AS uuid), :e, :k, "
    ":l, :t, CAST(:opt AS jsonb), 'zoho', :k, :pos, false)"
)

_RECORD = (
    "INSERT INTO crm_module_records (organization_id, module, ext_source, ext_id, name, "
    "owner_email, fields, ext_created_at) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :n, "
    ":w, CAST(:f AS jsonb), CAST(:t AS timestamptz))"
)

_CONTACT = (
    "INSERT INTO crm_contacts (organization_id, first_name, owner_email, custom, "
    "ext_source, ext_id) VALUES (CAST(:o AS uuid), :n, :w, CAST(:f AS jsonb), 'zoho', :e)"
)

_NOTE = (
    "INSERT INTO crm_activities (organization_id, type, subject, body, occurred_at, "
    "module_record_id, created_by, ext_source, ext_id, zoho_id) "
    "SELECT CAST(:o AS uuid), 'note', :s, :b, CAST(:t AS timestamptz), r.id, :a, 'zoho', "
    ":e, :e FROM crm_module_records r WHERE r.organization_id = CAST(:o AS uuid) "
    "AND r.module = :m AND r.ext_id = :r"
)


def _options(defn: FieldDef) -> str | None:
    if not defn.options:
        return None
    return json.dumps([{"label": v, "value": v} for v in defn.options])


def _defs(org: str, entity: str, defs: tuple[FieldDef, ...]) -> list[dict[str, Any]]:
    return [
        {"o": org, "e": entity, "k": d.key, "l": d.label, "t": d.type, "opt": _options(d),
         "pos": position}
        for position, d in enumerate(defs)
    ]


def _utc(rec: Record) -> str:
    return rec.created.astimezone(UTC).isoformat()


def statements(org: str, ds: Dataset | None = None) -> list[Statement]:
    """Every statement of the seed of *org*, in order, each with its rows."""
    ds = ds or build()
    prof = profile(ds)
    digest = hashlib.sha256(json.dumps(prof, sort_keys=True).encode("utf-8")).hexdigest()
    out: list[Statement] = [
        (_SETTINGS, [{"o": org}]),
        (_PROFILE, [{"o": org, "p": json.dumps(prof), "h": digest}]),
        (_DEF, _defs(org, "contact", ds.contact_fields)),
    ]
    for module in ds.modules:
        out.append((_DEF, _defs(org, f"module:{module.api_name}", module.fields)))
    for module in ds.modules:
        rows = ds.records[module.api_name]
        if rows:
            out.append((_RECORD, [
                {"o": org, "m": r.module, "e": r.ext_id, "n": r.name, "w": r.owner,
                 "f": json.dumps(r.fields), "t": _utc(r)}
                for r in rows
            ]))
    out.append((_CONTACT, [
        {"o": org, "n": c.name, "w": c.owner, "f": json.dumps(c.fields), "e": c.ext_id}
        for c in ds.contacts
    ]))
    out.append((_SHORT, [
        {"o": org, "m": f"zoho:module:{name}", "s": json.dumps({"short_by": count - len(ds.records[name])})}
        for name, count in sorted(ds.zoho_counts.items()) if count > len(ds.records[name])
    ]))
    out.append((_NOTE, [
        {"o": org, "s": n.subject, "b": n.body, "t": n.occurred.isoformat(), "a": SEED_AUTHOR,
         "e": n.ext_id, "m": n.module, "r": n.record_ext_id}
        for n in ds.notes
    ]))
    return out


#: The tables that the seed writes, children first, for a clean-up.
TABLES: tuple[str, ...] = (
    "crm_sync_cursors", "crm_activities", "crm_module_records", "crm_contacts", "crm_field_defs",
    "crm_source_profiles", "integration_connections", "crm_settings",
)

__all__ = ["SEED_AUTHOR", "TABLES", "statements"]
