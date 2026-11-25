"""OB-1a: one ``setup_status``, and no second status function (spec §7.1, §11).

Hermetic. The R8 half is ``test_setup_status_r8.py``. This file proves the
fence of ``onboarding.md`` §11 "One setup_status, and no second status
function", and the shape of the registry.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"

#: A function that reports the state of setup or onboarding.
_STATUS_DEF = re.compile(
    r"^\s*(?:async\s+)?def\s+(\w*(?:setup|onboarding)_(?:status|steps|state|progress)\w*)\s*\(",
    re.MULTILINE,
)


def _sources() -> list[Path]:
    out: list[Path] = []
    for base in (_ROOT / "apps", _ROOT / "packages"):
        out += [p for p in base.rglob("*.py")
                if "node_modules" not in p.parts and ".venv" not in p.parts]
    return out


def test_one_setup_status_and_no_second_status_function() -> None:
    found: list[tuple[str, str]] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for m in _STATUS_DEF.finditer(text):
            found.append((path.relative_to(_ROOT).as_posix(), m.group(1)))
    assert found == [
        ("apps/services/gateway/gateway/setup_status.py", "setup_status"),
    ], found


def test_the_route_runs_no_sql_of_its_own() -> None:
    src = (_GATEWAY / "routes" / "shell" / "setup.py").read_text(encoding="utf-8")
    assert "text(" not in src and "tenant_session" not in src and "get_db" not in src
    assert "setup_status(" in src


def test_the_route_is_registered_on_the_shell_router() -> None:
    init = (_GATEWAY / "routes" / "shell" / "__init__.py").read_text(encoding="utf-8")
    assert "shell import setup as _setup" in init


def test_the_registry_has_the_steps_of_the_spec() -> None:
    from gateway.setup_status import REGISTRY

    ids = [s.id for s in REGISTRY]
    assert ids == ["invite_team", "connect_email", "first_project", "ask_assistant",
                   "org_profile", "phone_push", "know_me"]
    assert "connect_calendar" not in ids  # waits on CE-10
    assert len(set(ids)) == len(ids)


def test_each_query_names_the_bound_tenant() -> None:
    from gateway.setup_status import _SKIPPED_SQL, REGISTRY

    for step in REGISTRY:
        assert "app.tenant_id" in step.done_sql, step.id
    assert "app.tenant_id" in _SKIPPED_SQL


def test_audience_filters_by_permission_and_feature() -> None:
    from gateway.setup_status import visible_steps

    assert len(visible_steps()) == 7
    member = visible_steps(permission=lambda p: False, feature=lambda f: True)
    assert [s.id for s in member] == [
        "connect_email", "first_project", "ask_assistant", "phone_push", "know_me"]
    no_features = visible_steps(permission=lambda p: True, feature=lambda f: False)
    assert [s.id for s in no_features] == ["invite_team", "org_profile", "phone_push"]


def test_time_reading_is_lenient() -> None:
    from gateway.setup_status import _iso, _skipped_ids

    assert _iso(None) is None
    assert _iso("not a time") is None
    assert _iso("2026-11-02T10:00:00Z") == "2026-11-02T10:00:00+00:00"
    assert _skipped_ids([None, "x", {"a": "t", "b": None}, {"c": "t"}]) == {"a", "c"}
