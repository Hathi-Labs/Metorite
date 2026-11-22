"""The seed of the synthetic set: the rows of one org as SQL (SEM-6).

:func:`statements` turns ``fixtures/synthetic.json`` into ``(sql, rows)`` pairs for
one org. Every value is a bound parameter, and every row carries
``organization_id``. The id of a core contact is :func:`questions.core_id`, so the
fixture knows it before the seed runs.

This module opens no connection. The R8 test runs the statements on the admin
connection of its fixture. ``run.py --scripted`` runs them in
``acb_common.db.tenant_session(org)``, the one seam.

The seed holds a mirror org: one Zoho profile, the field definitions of each
module, the module records and the core contacts. No catalogue entry and no fill
row exist, so a module has no key word such as ``@status``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from evals.semantic.questions import Fixture, core_id

Statement = tuple[str, list[dict[str, Any]]]

#: The tables that the seed writes, children first. :func:`purge` clears them.
TABLES = (
    "crm_module_records",
    "crm_field_defs",
    "crm_source_profiles",
    "integration_connections",
    "crm_contacts",
    "crm_settings",
)


def organization(org: str, label: str) -> Statement:
    """The org row, for a database that does not hold it yet (the CLI)."""
    return (
        "INSERT INTO organization (id, slug, display_name) "
        "VALUES (CAST(:o AS uuid), :s, :s) ON CONFLICT DO NOTHING",
        [{"o": org, "s": f"sem6-{label}-{org[:8]}"}],
    )


def _module(api_name: str) -> dict[str, Any]:
    """One generic module row of a profile, in the shape of the sync writer."""
    return {
        "api_name": api_name,
        "tab": api_name,
        "label": api_name,
        "singular_label": api_name.rstrip("s"),
        "custom": False,
        "generated_type": "default",
        "api_supported": True,
        "visible": True,
        "mirrored": True,
        "generic": True,
        "scope": f"ZohoCRM.modules.{api_name.lower()}.READ",
        "display_field": "Name",
    }


def connection_id(org: str) -> str:
    return str(uuid.uuid5(uuid.UUID(org), "connection"))


def statements(org: str, label: str, fx: Fixture) -> list[Statement]:
    """The rows of org *label* (``a`` or ``b``) in the order that the keys need."""
    raw = fx.raw
    out: list[Statement] = [
        (
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), 'mirror', 'everyone') "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = 'mirror', access = 'everyone'",
            [{"o": org}],
        ),
        (
            "INSERT INTO integration_connections (id, organization_id, provider, owning_app) "
            "VALUES (CAST(:c AS uuid), CAST(:o AS uuid), 'zoho', 'crm')",
            [{"c": connection_id(org), "o": org}],
        ),
        (
            "INSERT INTO crm_source_profiles (organization_id, connection_id, version, "
            "profile, profile_hash) VALUES (CAST(:o AS uuid), CAST(:c AS uuid), 1, "
            "CAST(:p AS jsonb), :h)",
            [
                {
                    "o": org,
                    "c": connection_id(org),
                    "p": json.dumps({"modules": [_module(m) for m in raw["modules"][label]]}),
                    "h": f"sem6-{org}",
                }
            ],
        ),
    ]
    defs = [
        {
            "o": org,
            "e": f"module:{m}",
            "k": f["key"],
            "l": f["key"].replace("_", " "),
            "t": f["type"],
            "opt": json.dumps({"lookup_module": f["lookup_module"]})
            if f.get("lookup_module")
            else None,
            "pos": i,
        }
        for m in raw["modules"][label]
        for i, f in enumerate(raw["fields"][m])
    ]
    out.append(
        (
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, options, "
            "source, provider_api_name, position, is_system) VALUES (CAST(:o AS uuid), :e, :k, "
            ":l, :t, CAST(:opt AS jsonb), 'zoho', :k, :pos, false)",
            defs,
        )
    )
    out.append(
        (
            "INSERT INTO crm_contacts (id, organization_id, first_name, last_name, ext_source, "
            "ext_id) VALUES (CAST(:i AS uuid), CAST(:o AS uuid), :f, :l, :s, :e)",
            [
                {
                    "i": core_id(org, c["key"]),
                    "o": org,
                    "f": c["first_name"],
                    "l": c["last_name"],
                    "s": "zoho" if c["zoho_id"] else None,
                    "e": c["zoho_id"],
                }
                for c in raw["contacts"]
                if c["org"] == label
            ],
        )
    )
    out.append(
        (
            "INSERT INTO crm_module_records (organization_id, module, ext_source, ext_id, name, "
            "owner_email, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :n, :w, "
            "CAST(:f AS jsonb))",
            [
                {
                    "o": org,
                    "m": r["module"],
                    "e": r["ext_id"],
                    "n": r["name"],
                    "w": r["owner"],
                    "f": json.dumps(r["fields"]),
                }
                for r in fx.records
                if r["org"] == label
            ],
        )
    )
    return [(sql, rows) for sql, rows in out if rows]


def purge(org: str, *, with_org: bool = False) -> list[Statement]:
    """The deletes that remove every row of *org*. *with_org* also drops the org row."""
    out: list[Statement] = [
        (f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)", [{"o": org}])
        for table in TABLES
    ]
    if with_org:
        out.append(("DELETE FROM organization WHERE id = CAST(:o AS uuid)", [{"o": org}]))
    return out
