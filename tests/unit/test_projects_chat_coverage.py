"""D-PM-37 — every Projects route is mapped or excluded, and this test says so.

Spec: ``project-docs/specs/projects_ai_chat.md`` §7.1.

The chat follows the app by a fence, not by memory. This file walks the REAL
``gateway.routes.projects.router`` and holds ``skill_projects.manifest`` to it
in both directions:

1. a route the manifest does not carry fails, naming the route, so a pull
   request that adds a Projects endpoint cannot merge until its author decides
   what the chat does with it;
2. a manifest row naming a route the router no longer serves fails, because a
   stale tool is a lie the agent will tell.

Then the manifest is held to the skill: every exported tool is in the
manifest, every manifest tool is exported OR planned, and the risk annotation
on each built tool agrees with its class. And the client is held to the
manifest: a verb-plus-path the manifest excludes never builds a request.

No database and no gateway process: the router imports, the manifest is a
tuple, and the client is patched at httpx.
"""

from __future__ import annotations

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

import skill_projects
import skill_projects.client as client
from skill_projects import manifest as m

from tests.unit._projects_agent_fakes import fake_gateway


def _router_routes() -> set[tuple[str, str]]:
    from gateway.routes.projects import router

    out: set[tuple[str, str]] = set()
    for route in router.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            out.add((method, path))
    return out


def _manifest_routes() -> set[tuple[str, str]]:
    return {(r.method, r.path) for r in m.MANIFEST}


# ── The two-way fence between the router and the manifest ───────────────────


def test_every_projects_route_is_mapped_or_excluded() -> None:
    missing = sorted(_router_routes() - _manifest_routes())
    assert not missing, (
        "These Projects routes are neither mapped to a chat tool nor excluded. "
        "Add one row each to apps/skills/skill-projects/skill_projects/"
        "manifest.py — a tool and a class, or class X with a reason:\n  "
        + "\n  ".join(f"{method} {path}" for method, path in missing)
    )


def test_no_manifest_row_names_a_route_the_router_no_longer_serves() -> None:
    stale = sorted(_manifest_routes() - _router_routes())
    assert not stale, (
        "These manifest rows name routes the Projects router does not serve. "
        "A stale row is a tool that will lie. Remove or repoint them:\n  "
        + "\n  ".join(f"{method} {path}" for method, path in stale)
    )


def test_the_manifest_has_no_duplicate_rows() -> None:
    seen = [(r.method, r.path) for r in m.MANIFEST]
    assert len(seen) == len(set(seen))


# ── The manifest against the skill's exported surface ───────────────────────


def _manifest_tools() -> set[str]:
    """Every tool the manifest names: a row's tool, or a composite over one."""
    return {r.tool for r in m.MANIFEST if r.tool} | set(m.COMPOSITE)


def test_every_composite_reaches_a_manifest_tool() -> None:
    direct = {r.tool for r in m.MANIFEST if r.tool}
    for tool, reached in m.COMPOSITE.items():
        assert reached, f"{tool} is a composite over nothing"
        for other in reached:
            assert other in direct or other in m.COMPOSITE, f"{tool} reaches unknown {other}"
        assert m.tool_class(tool) is not None, tool


def test_every_read_only_post_is_a_manifest_route() -> None:
    rows = {(r.method, r.path) for r in m.MANIFEST}
    for pair in m.READ_ONLY_POSTS:
        assert pair in rows, f"{pair} is not a manifest route"
        assert pair[1].endswith("/preview"), f"{pair} does not look like a preview"


def test_every_exported_tool_is_in_the_manifest() -> None:
    orphans = sorted(set(skill_projects.__all__) - _manifest_tools())
    assert not orphans, f"exported but reaching no route in the manifest: {orphans}"


def test_every_manifest_tool_is_built_or_planned_never_both() -> None:
    built = set(skill_projects.__all__)
    planned = set(m.PLANNED)
    both = sorted(built & planned)
    assert not both, f"built AND still listed as planned — drop from PLANNED: {both}"
    neither = sorted(_manifest_tools() - built - planned)
    assert not neither, (
        f"manifest tools that are neither exported nor in PLANNED with a slice: {neither}"
    )
    unused = sorted(planned - _manifest_tools())
    assert not unused, f"PLANNED names no manifest row reaches: {unused}"


def test_a_tool_has_one_class() -> None:
    classes: dict[str, set[str]] = {}
    for row in m.MANIFEST:
        if row.tool:
            classes.setdefault(row.tool, set()).add(row.cls)
    mixed = {tool: sorted(c) for tool, c in classes.items() if len(c) > 1}
    assert not mixed, f"a tool with two classes has two ceremonies: {mixed}"


def test_built_read_tools_are_annotated_read_only() -> None:
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    for name in skill_projects.__all__:
        cls = m.tool_class(name)
        assert cls is not None, f"{name} has no class in the manifest"
        hints = TOOL_ANNOTATIONS.get(name)
        assert hints is not None, f"{name} carries no risk annotation"
        if cls == "A":
            assert hints["read_only"] is True, f"{name} is class A and not read_only"
            assert hints["destructive"] is False
        elif cls == "C":
            assert hints["destructive"] is True, f"{name} is class C and not destructive"
        else:
            assert hints["read_only"] is False, f"{name} is class B and claims read_only"


def test_every_excluded_route_carries_a_reason() -> None:
    for row in m.MANIFEST:
        if row.cls == "X":
            assert row.reason.strip(), f"{row.method} {row.path} is excluded with no reason"


def test_the_two_hard_deletes_are_excluded_by_d_pm_35() -> None:
    """D-PM-35. The day WS-40 lands, this test is what somebody edits."""
    for path in ("/projects/nodes/{project_id}", "/projects/tasks/{task_id}"):
        row = next(r for r in m.MANIFEST if r.method == "DELETE" and r.path == path)
        assert row.cls == "X"
        assert "D-PM-35" in row.reason


# ── The client against the manifest ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/projects/nodes/0f8fad5b-d9cb-469f-a165-70867728950e"),
        ("DELETE", "/projects/tasks/0f8fad5b-d9cb-469f-a165-70867728950e"),
        ("POST", "/projects/nodes/0f8fad5b-d9cb-469f-a165-70867728950e/grants"),
        ("PATCH", "/projects/reports/0f8fad5b-d9cb-469f-a165-70867728950e/schedule"),
    ],
)
async def test_an_excluded_route_never_builds_a_request(
    method: str,
    path: str,
    monkeypatch,
) -> None:
    calls = fake_gateway(monkeypatch, {})
    with pytest.raises(client.GatewayRefusal):
        await client.request(method, path)
    assert calls == []


async def test_a_route_outside_the_manifest_never_builds_a_request(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, {})
    with pytest.raises(client.GatewayRefusal):
        await client.request("GET", "/admin/members")
    assert calls == []


def test_route_for_matches_a_concrete_path_to_its_template() -> None:
    row = m.route_for("GET", "/projects/tasks/0f8fad5b-d9cb-469f-a165-70867728950e/timeline?page=2")
    assert row is not None
    assert row.tool == "task_detail"
    assert m.route_for("GET", "/projects/tasks/x/y/z/w") is None
