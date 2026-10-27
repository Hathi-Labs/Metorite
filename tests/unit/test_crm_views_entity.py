"""CRM-U8: a saved view may be scoped to a generic module list.

The scope key is ``module:<api_name>``. This file needs no database. The R8
suite ``test_crm_views_r8.py`` runs the routes against Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway.routes.crm.views import ViewIn, check_entity

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


@pytest.mark.parametrize(
    "entity",
    ["leads", "deals", "contacts", "organizations", "module:Products", "module:Custom_1"],
)
def test_a_known_scope_passes(entity: str) -> None:
    assert check_entity(entity) == entity
    assert ViewIn(entity=entity, name="x").entity == entity


@pytest.mark.parametrize(
    "entity",
    ["widgets", "module:", "module:1bad", "module:a b", "module:a;drop", "Module:Products", "", "module:" + "a" * 101],
)
def test_a_bad_scope_is_refused(entity: str) -> None:
    with pytest.raises(ValueError):
        check_entity(entity)
    with pytest.raises(ValidationError):
        ViewIn(entity=entity, name="x")


def test_the_check_constraint_admits_the_module_form() -> None:
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "DROP CONSTRAINT IF EXISTS crm_views_entity_known"
        in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    sql = found[0].read_text(encoding="utf-8")
    assert "module:[A-Za-z][A-Za-z0-9_]{0,99}" in sql
    for core in ("leads", "deals", "contacts", "organizations"):
        assert f"'{core}'" in sql
