"""The template catalog and the tool docstring name the same templates.

``genUITemplates.tsx::TEMPLATE_CATALOG`` is the source of truth for the
generative UI templates, and ``emit_generative_ui``'s docstring is the copy
the model reads. ``generative_ui_2.md`` §3 says "keep them in lockstep" and
nothing held them so until WS-27bm S4 added five templates and this fence.
A name in one and not the other is a template the model emits and the
renderer drops, or one the renderer draws and the model never learns.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TSX = REPO_ROOT / "workbench" / "control_plane" / "src" / "components" / "genUITemplates.tsx"
PY = REPO_ROOT / "packages" / "acb_skills" / "acb_skills" / "write_artifact.py"


def _catalog() -> set[str]:
    tsx = TSX.read_text(encoding="utf-8")
    block = tsx.split("TEMPLATE_CATALOG: TemplateSpec[] = [", 1)[1].split("\n];", 1)[0]
    return set(re.findall(r'name: "([A-Za-z]+)"', block))


def _registry() -> set[str]:
    tsx = TSX.read_text(encoding="utf-8")
    block = tsx.split("TEMPLATE_REGISTRY: Record<string, TemplateRenderer> = {", 1)[1]
    block = block.split("\n};", 1)[0]
    return set(re.findall(r"^\s*([A-Za-z]+):", block, re.M))


def _docstring() -> set[str]:
    src = PY.read_text(encoding="utf-8")
    body = src.split("Available templates and their ``data`` shapes:", 1)[1]
    body = body.split("2. COMPONENT TREE", 1)[0]
    return set(re.findall(r"^\s*• ([A-Za-z]+) —", body, re.M))


def test_the_catalog_the_registry_and_the_docstring_name_the_same_templates() -> None:
    catalog, registry, doc = _catalog(), _registry(), _docstring()
    assert catalog, "TEMPLATE_CATALOG not found"
    assert catalog == registry, f"catalog vs registry: {catalog ^ registry}"
    assert catalog == doc, f"catalog vs docstring: {catalog ^ doc}"


def test_the_projects_views_emit_catalog_templates_only() -> None:
    """Every template name a Projects view or form emits is in the catalog."""
    skill = REPO_ROOT / "apps" / "skills" / "skill-projects" / "skill_projects"
    src = (skill / "views.py").read_text(encoding="utf-8")
    src += (skill / "forms.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'_template\(\s*"([A-Za-z]+)"', src))
    assert emitted, "no template emitted"
    assert emitted <= _catalog(), f"emitted but not in the catalog: {emitted - _catalog()}"


def test_the_plan_card_shape_in_the_docstring_names_every_catalog_field() -> None:
    """WS-27bm S7d review round 1 (F3). The planCard shape the model reads
    drifted from the catalog's, so the model never learned the fields the
    card draws. Every field name in the catalog's planCard ``data`` must be in
    the docstring's planCard bullet."""
    tsx = TSX.read_text(encoding="utf-8")
    entry = tsx.split('name: "planCard",', 1)[1]
    catalog = entry.split('data: "', 1)[1].split('",', 1)[0]
    src = PY.read_text(encoding="utf-8")
    bullet = src.split("• planCard —", 1)[1].split("2. COMPONENT TREE", 1)[0]
    bullet = bullet.split("• ", 1)[0]
    fields = set(re.findall(r"([a-z_]+)\??:", catalog)) | set(
        re.findall(r"[{,]\s*([a-z_]+)\??(?=[,}\s])", catalog)
    )
    fields.discard("string")
    missing = sorted(f for f in fields if not re.search(rf"\b{f}\b", bullet))
    assert fields and not missing, f"planCard fields the docstring omits: {missing}"


# ── The kinds the tool refuses (2026-10-07) ────────────────────────────────
#
# A model emitted ``{"type":"tree"}`` and a member saw "unsupported UI
# element: tree". ``write_artifact.GENUI_NODE_TYPES`` existed, said it was
# "to reject obviously-wrong ones early", and nothing read it. Now
# ``emit_generative_ui`` refuses through ``genui_refusal``, and these tests
# hold the Python sets to the renderer's.

def _wa():
    """The module, not the function: the package re-exports
    ``write_artifact`` under the module's own name."""
    import importlib

    return importlib.import_module("acb_skills.write_artifact")


NODE_TSX = REPO_ROOT / "workbench" / "control_plane" / "src" / "components" / "GenerativeUINode.tsx"


def _renderer_kinds() -> set[str]:
    tsx = NODE_TSX.read_text(encoding="utf-8")
    block = tsx.split("const KNOWN_TYPES = new Set([", 1)[1].split("]);", 1)[0]
    return set(re.findall(r'"([A-Za-z]+)"', block))


def test_the_tool_refuses_the_kinds_the_renderer_does_not_draw() -> None:
    wa = _wa()

    kinds = _renderer_kinds()
    assert kinds, "KNOWN_TYPES not found"
    assert set(wa.GENUI_NODE_TYPES) == kinds, f"tool vs renderer: {set(wa.GENUI_NODE_TYPES) ^ kinds}"
    assert set(wa.GENUI_TEMPLATES) == _catalog(), (
        f"tool vs catalog: {set(wa.GENUI_TEMPLATES) ^ _catalog()}"
    )


def test_the_docstring_names_every_tree_kind() -> None:
    """A kind the renderer draws and the docstring omits is one the model
    never learns. ``template``, ``html`` and ``react`` are modes of their own."""
    src = PY.read_text(encoding="utf-8")
    body = src.split("2. COMPONENT TREE", 1)[1].split("3. REACT COMPONENT", 1)[0]
    missing = sorted(
        k for k in _renderer_kinds() - {"template", "html", "react"}
        if not re.search(rf"\b{k}\b", body)
    )
    assert not missing, f"tree kinds the docstring omits: {missing}"


def _emit(ui: str) -> dict:
    import asyncio

    wa = _wa()

    return asyncio.run(wa.emit_generative_ui(ui))


def test_an_unknown_kind_is_refused_with_the_allowed_kinds_named() -> None:
    res = _emit('{"type":"tree","props":{"items":[{"label":"Firmware"}]}}')
    assert res["ok"] is False
    assert "'tree'" in res["error"]
    for kind in ("card", "markdown", "list", "template"):
        assert kind in res["error"], kind
    assert "nested list" in res["error"]


def test_an_unknown_kind_deep_in_the_tree_is_refused_with_its_path() -> None:
    res = _emit(
        '{"type":"card","children":[{"type":"text","props":{"text":"a"}},'
        '{"type":"stack","children":[{"type":"gauge"}]}]}'
    )
    assert res["ok"] is False
    assert res["error"].startswith("ui.children[1].children[0]:"), res["error"]


def test_an_unknown_template_is_refused_and_a_wrapped_root_is_read() -> None:
    res = _emit('{"surface":"panel","root":{"type":"template","props":{"name":"ganttChart"}}}')
    assert res["ok"] is False
    assert "'ganttChart'" in res["error"] and "planCard" in res["error"]


def test_a_known_tree_is_not_refused() -> None:
    """It passes the check and stops later, at the missing run stream."""
    wa = _wa()

    ok = '{"type":"card","children":[{"type":"template","props":{"name":"planCard"}}]}'
    assert wa.genui_refusal(__import__("json").loads(ok)) is None
    assert _emit(ok)["error"] == "no active run stream to render into"
