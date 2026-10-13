"""R8 — the field studio of a Native CRM, against a real Postgres (WS-53
CRM-C4a, ``crm_platform.md`` §20.7).

Migration 258 adds ``crm_field_sections`` and seven columns on
``crm_field_defs``. The routes of ``routes/crm/field_studio.py`` let an admin
of a Native CRM create, shape, retire and restore a field. The record routes
write ``custom`` per key, with the type, "required" and the default of each
field.

Each test runs through the real ``/crm`` router, as the non-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations.
One test, or a group, for each "Done when" item of CRM-C4a:

1.  a field of each type: create, rename, order, section, hide, retire and
    restore, and the values in ``custom`` survive each step. Org B sees none;
2.  the type matrix: a refused change gives 422 and changes no row;
3.  "required" and the default on ``POST`` and ``PATCH /crm/deals``;
4.  a ``PATCH`` of one key keeps the others, for the four entities;
5.  a bad value gives 422 that names the label;
6.  a lookup stores ``{"id", "name"}``, and an id of org B gives 422;
7.  a multi-select stores an array, and the custom filter finds it;
8.  a new option takes a slug value, ``-2`` on a clash, and a rename keeps it;
9.  ``PUT /crm/field-layout/{entity}`` in one transaction;
10. the readers leave out ``is_system`` rows, and a retired key is refused;
11. a CRM-Q7a rule on a Zoho ``double`` field survives the type change;
12. the sync leaves a ``source = 'metorite'`` row alone, and the start over
    deletes it;
13. 403 for a member, 404 for an org off the flag, 409 in Mirror mode;
14. a sync leaves the seven new columns unchanged.

``tests/unit/test_crm_field_studio.py`` holds the rules that need no database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _age_profile,
    _imported,
    _leads,
    _post,
    _rule_status,
    _run,
    _seed,
    fr,
)
from tests.unit.test_crm_pipelines_r8 import _req
from tests.unit.test_crm_pipelines_r8 import _admin as _admin_of
from tests.unit.test_crm_pipelines_r8 import _member as _member_of
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _assert_non_priv,
    _q,
    _the_loop_is_not_stopping,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _import_again(p: Any, org: str) -> None:
    """Make the next run an import, so it writes the field definitions again.

    An incremental run writes no field definition. An import and a
    reconcile do (``engine._execute``).
    """
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_cursors SET initial_done_at = NULL "
                       "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'"),
                  {"o": org})


def _clean_studio(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            for table in ("crm_field_defs", "crm_field_sections"):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


# ── 11. A CRM-Q7a rule on a Zoho double survives the type change ────────────


async def test_an_approved_rule_on_a_double_field_survives_the_type_change(fr) -> None:
    """The sync now writes ``decimal`` for a Zoho ``double``. A rule that an
    older build approved names ``number``. The next discovery reads the new
    word, and the rule stays ``approved`` (not ``type_mismatch``)."""
    p, a, _b = fr
    a.fields["Leads"].append(
        {"api_name": "Score", "field_label": "Score", "data_type": "double"})
    _seed(a)
    next(r for r in a.data["Leads"] if r["id"] == "l1")["Score"] = 12.5
    from tests.unit.test_crm_sync_engine_r8 import _connect

    await _connect(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert _q(p, "SELECT type FROM crm_field_defs WHERE organization_id = "
                 "CAST(:o AS uuid) AND key = 'Score'", o=p.org_a) == [("decimal",)]

    # What an older build stored: the word `number`, in the row and the profile.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_field_defs SET type = 'number' WHERE "
                       "organization_id = CAST(:o AS uuid) AND key = 'Score'"),
                  {"o": p.org_a})
        c.execute(text(
            "UPDATE crm_source_profiles SET profile = jsonb_set(profile, "
            "'{custom_field_types,lead,Score}', '\"number\"') "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    made = await _post(p.org_a, "Score", "annual_revenue")
    assert made.status_code == 201, made.text
    assert made.json()["field_type"] == "number"

    _age_profile(p, p.org_a)
    _import_again(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert _rule_status(p, made.json()["id"]) == "approved", "not type_mismatch"
    assert _q(p, "SELECT type FROM crm_field_defs WHERE organization_id = "
                 "CAST(:o AS uuid) AND key = 'Score'", o=p.org_a) == [("decimal",)]
    assert float(_leads(p, p.org_a)["l1"][4]["Score"]) == 12.5
    _clean_studio(p)
