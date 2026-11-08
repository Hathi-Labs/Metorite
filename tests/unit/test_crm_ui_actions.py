"""WS-CRM CRM-M2a — every CRM route has a row, and the planned rows only go down.

Spec: ``project-docs/specs/crm_conversational.md`` §8 (CRM-M2a) and
``external_agent_access.md`` §19.3 and §19.9. The model is
``tests/unit/test_projects_ui_actions.py`` (the ratchet) and
``tests/unit/test_knowledge_chat_coverage.py`` (the router walk).

``acb_common.app_manifests.crm`` is the one route manifest of the CRM. This
file walks the REAL ``gateway.routes.crm.router`` and the CRM client files of
the control plane, and holds the manifest to both:

1. A route with no row fails, and the message names it.
2. A row that names a route the router does not serve fails.
3. A request of a CRM client file (``app/crm/lib``) that lands on no row
   fails, and the message names the file and the function. So a new client
   method cannot hide.
4. A row that claims a chat tool fails when the source of the tool does not
   send that route. A row that names a planned slice fails when the slice is
   not a row of the slice table of the spec.
5. The count of planned rows equals its ceiling. A new gap fails, and a paid
   gap must lower the ceiling in the same change. So the count only goes down.
6. Every row is ``off_door`` (CRM-M2a). MCP-P2a sets the first door op and
   changes this test with it.

No database, no gateway process and no node runtime.

Mutations this file catches (R7). Each has a test below that drives the check
on altered data, so the red is proven on every run:

* a route added to the router and not to the table -> ``test_a_new_route_fails``;
* a row for a route that is gone -> ``test_a_stale_row_fails``;
* a new client method with no row -> ``test_a_new_client_method_fails``;
* a tool that does not send the route of its row -> ``test_a_false_claim_fails``;
* a new planned row -> ``test_a_new_planned_row_fails``.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("gateway.routes.crm", reason="gateway not installed")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_common.app_manifests.base import Route
from acb_common.app_manifests.crm import CRM
from acb_skills import chat_parity as cp

from tests.unit import _chat_parity_reader as r

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "project-docs" / "specs" / "crm_conversational.md"

#: The most planned rows the manifest may hold. A paid row lowers it in the
#: same change. CRM-M2a set it from the count of the router (2026-11-08).
PLANNED_CEILING = 90

#: The directory of the CRM client files, relative to ``src``.
CLIENT_DIR = "app/crm/lib/"

#: A planned slice is a row of the slice table of the spec.
PLANNED_ID = re.compile(r"CRM-M2[c-l]")


# ── The readers ─────────────────────────────────────────────────────────────


def _router_routes() -> set[tuple[str, str]]:
    from gateway.routes.crm import router

    out: set[tuple[str, str]] = set()
    for route in router.routes:
        for method in getattr(route, "methods", None) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            out.add((method, getattr(route, "path", "")))
    return out


def _rows() -> tuple[Route, ...]:
    return CRM.routes


def _pairs(rows: tuple[Route, ...]) -> set[tuple[str, str]]:
    return {(row.method, row.path) for row in rows}


@functools.cache
def _client_actions() -> dict[str, r.Action]:
    """Each request of the CRM client files, by its action key."""
    acts, _ = r.inventory(r.Project(), cp.F3_FILES)
    return {
        key: act
        for key, act in acts.items()
        if act.app == "crm" and any(site.file.startswith(CLIENT_DIR) for site in act.sites)
    }


def _slices() -> set[str]:
    """The slice ids of the table of CRM-M2 in the spec."""
    text = SPEC.read_text(encoding="utf-8")
    return set(re.findall(r"^\| (CRM-M2[a-z]) \|", text, flags=re.MULTILINE))


def _shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


# ── The checks, as pure functions, so a mutation can drive each one ─────────


def unrowed(routes: set[tuple[str, str]], rows: tuple[Route, ...]) -> list[str]:
    return sorted(f"{m} {p}" for m, p in routes - _pairs(rows))


def stale(routes: set[tuple[str, str]], rows: tuple[Route, ...]) -> list[str]:
    return sorted(f"{m} {p}" for m, p in _pairs(rows) - routes)


def unrouted(acts: dict[str, r.Action], rows: tuple[Route, ...]) -> list[str]:
    """Each client request that lands on no row. The rows are matched as
    ``r.crm_rows`` matches them, so the check reads the table it is given."""
    keep = {(row.method, row.path) for row in rows}
    out: list[str] = []
    for key, act in sorted(acts.items()):
        found = [
            row
            for gw in act.gateway or ()
            for row in r.crm_rows(act.verb, gw)
            if (row.method, row.path) in keep
        ]
        if not found:
            out.append(f"{key} ({act.where()}) -> {act.gateway}")
    return out


def false_claims(rows: tuple[Route, ...]) -> list[str]:
    """Each tool of a row whose own source does not send the route."""
    registered = r.registered_tools()
    bad: list[str] = []
    for row in rows:
        for tool in (row.tool, *row.also):
            if not tool:
                continue
            where = f"{row.method} {row.path}"
            if tool not in registered:
                bad.append(f"{where}: {tool} is not a registered chat tool")
            elif r.tool_routes(tool) is None:
                bad.append(f"{where}: no file of TOOL_SOURCES defines {tool}")
            elif not r.reaches(tool, row.method, _shape(row.path)):
                bad.append(f"{where}: {tool} does not send this route")
    return bad


def bad_planned(rows: tuple[Route, ...], slices: set[str]) -> list[str]:
    out: list[str] = []
    for row in rows:
        if not row.planned:
            continue
        where = f"{row.method} {row.path}"
        if not PLANNED_ID.fullmatch(row.planned):
            out.append(f"{where}: {row.planned!r} is not a slice from CRM-M2c to CRM-M2l")
        elif f"CRM-M2{row.planned[-1]}" not in slices:
            out.append(f"{where}: {row.planned} is not a row of the slice table of the spec")
    return out


def planned_count(rows: tuple[Route, ...]) -> int:
    return sum(1 for row in rows if row.planned)


def ratchet(count: int, ceiling: int) -> list[str]:
    if count > ceiling:
        return [f"{count} planned rows, over the ceiling of {ceiling}"]
    if count < ceiling:
        return [f"{count} planned rows, under the ceiling of {ceiling}. Lower it to {count}."]
    return []


# ── 1 and 2. The router and the table agree ─────────────────────────────────


def test_every_crm_route_has_a_row() -> None:
    missing = unrowed(_router_routes(), _rows())
    assert not missing, (
        "These CRM routes have no row. Add one row each to "
        "packages/acb_common/acb_common/app_manifests/crm.py: a class, and a "
        "chat tool, a planned slice (CRM-M2c to CRM-M2l) or a reason:\n  "
        + "\n  ".join(missing)
    )


def test_no_row_names_a_route_the_router_does_not_serve() -> None:
    gone = stale(_router_routes(), _rows())
    assert not gone, "These rows name routes that the CRM router does not serve:\n  " + "\n  ".join(gone)


def test_the_router_is_read_and_the_table_has_no_duplicate() -> None:
    """Non-vacuity: a reader that found nothing would pass every check."""
    assert len(_router_routes()) >= 140
    pairs = [(row.method, row.path) for row in _rows()]
    assert len(pairs) == len(set(pairs)) == len(_router_routes())


def test_a_new_route_fails() -> None:
    """Mutation: a route in the router and not in the table."""
    routes = _router_routes() | {("POST", "/crm/zz-new")}
    assert unrowed(routes, _rows()) == ["POST /crm/zz-new"]


def test_a_stale_row_fails() -> None:
    """Mutation: a row whose route is gone."""
    routes = _router_routes() - {("GET", "/crm/owners")}
    assert stale(routes, _rows()) == ["GET /crm/owners"]


# ── 3. Each client request lands on a row ───────────────────────────────────


def test_the_client_files_are_read() -> None:
    acts = _client_actions()
    assert len(acts) >= 80, len(acts)
    files = {site.file for act in acts.values() for site in act.sites}
    assert "app/crm/lib/api.ts" in files


def test_every_client_request_lands_on_a_row() -> None:
    bad = unrouted(_client_actions(), _rows())
    assert not bad, (
        "These requests of the CRM client have no row in "
        "packages/acb_common/acb_common/app_manifests/crm.py:\n  " + "\n  ".join(bad)
    )


def test_a_new_client_method_fails() -> None:
    """Mutation: a new client function for a route with no row."""
    rel = "app/crm/lib/api.ts"
    src = (r.SRC / rel).read_text(encoding="utf-8")
    fake = (
        "\nexport async function zzFakeMergeAll(): Promise<void> {\n"
        '  await call("/zz-fake/merge-all", { method: "POST" });\n'
        "}\n"
    )
    project = r.Project({rel: src + fake}, base=r.Project())
    acts, _ = r.inventory(project, cp.F3_FILES)
    acts = {
        k: a
        for k, a in acts.items()
        if a.app == "crm" and any(s.file.startswith(CLIENT_DIR) for s in a.sites)
    }
    bad = unrouted(acts, _rows())
    assert len(bad) == 1 and bad[0].startswith("POST /api/crm/zz-fake/merge-all "), bad


# ── 4. Each claim is true ───────────────────────────────────────────────────


def test_every_tool_claim_is_true() -> None:
    bad = false_claims(_rows())
    assert not bad, "These tool claims are false:\n  " + "\n  ".join(bad)


def test_a_false_claim_fails() -> None:
    """Mutation: a tool that sends another route, and a name that is no tool."""
    wrong = Route("GET", "/crm/owners", "crm_change_history", "A", off_door="x")
    nobody = Route("GET", "/crm/owners", "zz_no_such_tool", "A", off_door="x")
    assert false_claims((wrong,)) == ["GET /crm/owners: crm_change_history does not send this route"]
    assert false_claims((nobody,)) == ["GET /crm/owners: zz_no_such_tool is not a registered chat tool"]


def test_every_planned_slice_is_a_row_of_the_spec() -> None:
    bad = bad_planned(_rows(), _slices())
    assert not bad, "\n  ".join(["These planned rows name no slice:", *bad])


def test_a_planned_slice_must_exist() -> None:
    """Mutation: a slice that the table of the spec does not hold."""
    row = Route("GET", "/crm/owners", "", "A", planned="CRM-M2z", off_door="x")
    assert len(bad_planned((row,), _slices())) == 1
    row = Route("GET", "/crm/owners", "", "A", planned="CRM-M2c", off_door="x")
    assert bad_planned((row,), _slices()) == []


def test_every_class_x_row_says_why_and_names_no_tool() -> None:
    for row in _rows():
        if row.cls == "X":
            assert row.reason and not row.tool and not row.planned, (row.method, row.path)


# ── 5. The ratchet ──────────────────────────────────────────────────────────


def test_the_planned_rows_equal_their_ceiling() -> None:
    bad = ratchet(planned_count(_rows()), PLANNED_CEILING)
    assert not bad, "\n  ".join(bad)


def test_a_new_planned_row_fails() -> None:
    """Mutation: one more planned row than the ceiling holds."""
    extra = Route("GET", "/crm/zz", "", "A", planned="CRM-M2l", off_door="x")
    assert ratchet(planned_count((*_rows(), extra)), PLANNED_CEILING) != []
    assert ratchet(planned_count(_rows()), PLANNED_CEILING + 1) != []


# ── 6. The door is off ──────────────────────────────────────────────────────


def test_no_crm_row_has_a_door_op_yet() -> None:
    """CRM-M2a builds the table with the door off. MCP-P2a sets the first op,
    adds ``crm`` to ``APPS`` and changes this test with it."""
    from acb_common import app_manifests as am

    assert [row.op for row in _rows() if row.op] == []
    assert all(row.off_door for row in _rows() if row.cls != "X")
    assert CRM.id not in {app.id for app in am.APPS}


def test_the_classes_are_spread() -> None:
    """Non-vacuity: the table holds reads, writes, hard writes and exclusions."""
    seen = Counter(row.cls for row in _rows())
    assert set(seen) == {"A", "B", "C", "X"}, seen
