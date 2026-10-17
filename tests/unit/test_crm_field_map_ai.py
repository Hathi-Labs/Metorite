"""The Field map makes no model call (WS-53 CRM-Q20-2 item 9).

``crm_platform.md`` §18.9.6: "The CRM-Q7b route and its proposer are
removed." The CRM-Q7b proposer asked ``tier-fast`` for a column for each
field, from ``POST /crm/field-rules/suggest``. The mapping pipeline of
CRM-Q20-1 (``crm_sync/map_pipeline.py``) now makes each suggestion, on the
decide tier, as a draft of the plan.

What this proves:

1. no route of the gateway answers ``/crm/field-rules/suggest``;
2. ``field_rules.py`` names no model call, no proposer feature and no write
   of a ``proposed`` row;
3. a ``proposed`` row that the proposer wrote earlier still takes an
   approve, and an approve still refuses a row that changed after the read
   (the guard of CRM-Q7b, which the PATCH keeps).
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _admin_of,
    _imported,
    _req,
    fr,
)
from tests.unit.test_crm_sync_engine_r8 import _q, ns  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

FIELD_RULES = (
    Path(__file__).resolve().parents[2]
    / "apps/services/gateway/gateway/routes/crm/field_rules.py"
)


def test_1_no_route_answers_the_proposer_path() -> None:
    import gateway.routes.crm  # noqa: F401 - registers each route
    from gateway.routes.crm.core import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert any(p.endswith("/field-rules") for p in paths), "the scan sees the Field map"
    assert not [p for p in paths if p.endswith("/field-rules/suggest")]


def test_2_the_field_map_names_no_model_call() -> None:
    source = FIELD_RULES.read_text(encoding="utf-8")
    for word in ("acompletion_with_fallback", "crm.field_map", "tier-fast",
                 "'proposed', 'ai'", "suggest_field_rules"):
        assert word not in source, word


@_DB_GATE
async def test_3_an_old_proposal_still_takes_an_approve_and_the_race_guard_holds(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``proposed`` row from before. Its approve refuses with 409 when the
    row changed between the read and the update, as in CRM-Q7b."""
    from gateway.routes.crm import field_rules

    p, a, _b = fr
    await _imported(p, p.org_a, a)
    with p.admin_engine.begin() as c:
        rule_id = c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by, confidence) VALUES (CAST(:o AS uuid), 'zoho', 'lead', "
            "'Custom_Industry', 'text', 1, 'industry', 'proposed', 'ai', 0.62) "
            "RETURNING id"), {"o": p.org_a}).scalar()
    original = field_rules._check_free

    async def racing(db: Any, entity: str, column: str, field: str, rid: Any) -> None:
        await original(db, entity, column, field, rid)
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE crm_field_rules SET target_column = 'lost_note' "
                           "WHERE id = CAST(:i AS uuid)"), {"i": str(rule_id)})

    monkeypatch.setattr(field_rules, "_check_free", racing)
    res = await _req(_admin_of(p.org_a), "PATCH", f"/crm/field-rules/{rule_id}",
                     {"status": "approved"})
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "rule_changed"
    row = _q(p, "SELECT status, target_column FROM crm_field_rules "
                "WHERE id = CAST(:i AS uuid)", i=str(rule_id))[0]
    assert tuple(row) == ("proposed", "lost_note")

    monkeypatch.setattr(field_rules, "_check_free", original)
    res = await _req(_admin_of(p.org_a), "PATCH", f"/crm/field-rules/{rule_id}",
                     {"status": "approved"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
