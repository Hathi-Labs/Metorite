"""The source scans of the AI Studio route gate (STU-J1-1).

CIV-33f (task 3223). These tests read the files of ``routes/studio``, so a file that
holds them is a scanner file. They live apart from ``test_studio_route_gate.py``,
which keeps the slow route cases. The bodies are unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from acb_auth.permissions import CAPABILITIES, FEATURES
from gateway.routes import studio as studio_pkg
from gateway.routes.studio import actions

_ROOT = Path(__file__).resolve().parents[2]
_STUDIO_DIR = _ROOT / "apps/services/gateway/gateway/routes/studio"


def test_the_package_exports_the_one_router() -> None:
    assert studio_pkg.router is actions.router
    sources = {p.name: p.read_text(encoding="utf-8") for p in _STUDIO_DIR.glob("*.py")}
    assert [n for n, s in sources.items() if "APIRouter(" in s] == ["actions.py"]


def test_no_studio_file_names_the_workflows_gate() -> None:
    gate = re.compile(
        r"""(?:require_feature(?:_router)?|require_permission|require_any_permission)"""
        r"""\(\s*["'](?:feature:)?workflows["']""")
    for path in sorted(_STUDIO_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not gate.search(text), path.name
        assert "feature:workflows" not in text, path.name
    actions_src = (_STUDIO_DIR / "actions.py").read_text(encoding="utf-8")
    assert 'dependencies=[require_feature_router("studio")]' in actions_src


def _owners(tree: Any) -> dict[int, str]:
    """The top-level function that holds each node, by ``id(node)``. A node
    outside every function maps to ``<module>``."""
    import ast

    out: dict[int, str] = {}
    for top in tree.body:
        name = top.name if isinstance(top, ast.AsyncFunctionDef | ast.FunctionDef) else "<module>"
        for node in ast.walk(top):
            out[id(node)] = name
    return out


def test_the_share_verb_is_read_by_the_install_only() -> None:
    """J1-1 adds `studio:publish` to the vocabulary. Version publish of a
    member's own app needs `feature:studio` and ownership (STU-Q4b). The one
    readers in `routes/studio` are the org and group install of PKG-8b, a
    share to others (D180.3, `package_host.md` §6.1), and its uninstall of
    PKG-8e. WS-STU B6a adds the third reader: `set_install_run_as`, because the
    right that installs the app names the member of a run with no viewer.

    J2a (the read of a viewer, `ai_studio.md` §14 J2a) adds no reader. A
    viewer reads through an install that a holder of the verb made, and the
    read checks no right of its own. The new module `package_host/viewer.py`
    must not read the verb either, so the scan covers it too.

    WS-STU J2b-2 adds one reader: `share_package`, the share with people
    (D180.3), with one `user.has_permission(SHARE_RIGHT)`. The test takes
    out its one entry of each list first, so the lists below stay those of
    the install.

    The check reads the syntax tree, so the quote style does not matter. The
    string constant `studio:publish` occurs once in `routes/studio`: the
    definition of `packages.SHARE_RIGHT`. The name `SHARE_RIGHT` is read in
    `install_package`, `uninstall_package` and `set_install_run_as` (WS-STU
    B6a), each with one `user.has_permission(SHARE_RIGHT)`. WS-STU J2b-1 adds
    three readers, each with one `user.has_permission(SHARE_RIGHT)`:
    `list_approvals` (the list of an approver, 403 for each other caller), and
    `approve_approval` and `reject_approval`, which pass the answer to the
    store as `can_publish`. The ask of the owner (`request_approval`) reads no
    right. Since A5c-2,
    `user.has_permission(ADMIN_RIGHT)` in `_delete_mode`: the admin
    path of a delete, which reads `admin:members:manage` (the seam of
    `routes/admin/members.py`) and no other right. No route gains a
    dependency, so the gate stays one gate. No file reads `.access.has(`,
    the other door to the same check."""
    import ast

    from gateway.routes.studio import packages

    assert "studio:publish" in CAPABILITIES
    assert "studio" in FEATURES
    assert packages.SHARE_RIGHT == "studio:publish"
    verbs: list[tuple[str, str, str]] = []
    names: list[tuple[str, str, str]] = []
    calls: list[tuple[str, str, str]] = []
    viewer = _ROOT / "apps/services/gateway/gateway/package_host/viewer.py"
    assert viewer.is_file()
    for path in [*sorted(_STUDIO_DIR.glob("*.py")), viewer]:
        text = path.read_text(encoding="utf-8")
        assert ".access.has(" not in text, path.name
        tree = ast.parse(text)
        owner = _owners(tree)
        for node in ast.walk(tree):
            where = (path.name, owner.get(id(node), "<module>"))
            if isinstance(node, ast.Constant) and node.value == "studio:publish":
                verbs.append((*where, "constant"))
            if isinstance(node, ast.Name) and node.id == "SHARE_RIGHT":
                names.append((*where, type(node.ctx).__name__))
            if isinstance(node, ast.Attribute) and node.attr == "SHARE_RIGHT":
                names.append((*where, "attribute"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    node.func.attr in ("has_permission", "has"):
                calls.append((*where, ast.unparse(node)))
    # WS-STU J2b-2: the share with people reads the verb exactly once.
    share_name = ("packages.py", "share_package", "Load")
    share_call = ("packages.py", "share_package", "user.has_permission(SHARE_RIGHT)")
    assert names.count(share_name) == 1 and calls.count(share_call) == 1, (names, calls)
    names.remove(share_name)
    calls.remove(share_call)

    assert verbs == [("packages.py", "<module>", "constant")], verbs
    assert sorted(names) == [("packages.py", "<module>", "Store"),
                             ("packages.py", "approve_approval", "Load"),
                             ("packages.py", "install_package", "Load"),
                             ("packages.py", "list_approvals", "Load"),
                             ("packages.py", "reject_approval", "Load"),
                             ("packages.py", "set_install_run_as", "Load"),
                             ("packages.py", "uninstall_package", "Load")], names
    assert sorted(calls) == [("packages.py", "_delete_mode", "user.has_permission(ADMIN_RIGHT)"),
                             ("packages.py", "approve_approval",
                              "user.has_permission(SHARE_RIGHT)"),
                             ("packages.py", "install_package",
                              "user.has_permission(SHARE_RIGHT)"),
                             ("packages.py", "list_approvals",
                              "user.has_permission(SHARE_RIGHT)"),
                             ("packages.py", "reject_approval",
                              "user.has_permission(SHARE_RIGHT)"),
                             ("packages.py", "set_install_run_as",
                              "user.has_permission(SHARE_RIGHT)"),
                             ("packages.py", "uninstall_package",
                              "user.has_permission(SHARE_RIGHT)")], calls
    assert packages.ADMIN_RIGHT == "admin:members:manage"


#: The one right of the admin path of a delete (STU-A5c-2).
_ADMIN_TEXT = "admin:members:manage"
#: Each read of that right in ``routes/studio``: the definition of
#: ``packages.ADMIN_RIGHT`` (its name and its text), and one load in
#: ``_delete_mode``.
_ADMIN_READS = [("packages.py", "<module>", "Store"),
                ("packages.py", "<module>", "constant"),
                ("packages.py", "_delete_mode", "Load")]


def _docstrings(tree: Any) -> set[int]:
    """The ids of the docstring nodes of a module and of each def in it."""
    import ast

    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def admin_right_reads(sources: dict[str, str]) -> list[tuple[str, str, str]]:
    """Each read of ``ADMIN_RIGHT``, and each string that holds the text of
    the right, in *sources* (a file name -> its text), as ``(file, top-level
    function, kind)``. A docstring may name the right. Any other string that
    holds it counts, an f-string part too."""
    import ast

    found: list[tuple[str, str, str]] = []
    for name, text in sorted(sources.items()):
        tree = ast.parse(text)
        owner, docs = _owners(tree), _docstrings(tree)
        for node in ast.walk(tree):
            where = (name, owner.get(id(node), "<module>"))
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and _ADMIN_TEXT in node.value and id(node) not in docs):
                found.append((*where, "constant"))
            if isinstance(node, ast.Name) and node.id == "ADMIN_RIGHT":
                found.append((*where, type(node.ctx).__name__))
            if isinstance(node, ast.Attribute) and node.attr == "ADMIN_RIGHT":
                found.append((*where, "attribute"))
    return sorted(found)


def _studio_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(_STUDIO_DIR.glob("*.py"))}


def test_the_admin_right_is_read_by_the_delete_mode_only() -> None:
    """STU-A5c-2 fix round 1. The admin path reads ``admin:members:manage``
    in ``_delete_mode`` and nowhere else in ``routes/studio``. So no other
    route grows an admin path by a second read, and no route names the right
    in its own text. Mutation M6 -> red."""
    from gateway.routes.studio import packages

    assert packages.ADMIN_RIGHT == _ADMIN_TEXT
    assert admin_right_reads(_studio_sources()) == _ADMIN_READS


def test_a_second_read_of_the_admin_right_fails() -> None:
    """M6, run on the real sources: a read of ``ADMIN_RIGHT`` in
    ``install_package``, and the text of the right in a function of another
    module, each give one more read."""
    sources = _studio_sources()
    share = "    if not user.has_permission(SHARE_RIGHT):\n"
    text = sources["packages.py"].replace("\r\n", "\n")
    assert text.count(share) == 4, (
        "non-vacuity: the share checks of the install, the uninstall, the run_as (B6a) "
        "and the share of J2b-2")
    mutated = {**sources, "packages.py": text.replace(
        share, "    if not (user.has_permission(SHARE_RIGHT) or user.has_permission(ADMIN_RIGHT)):\n", 1)}
    assert admin_right_reads(mutated) == sorted(
        [*_ADMIN_READS, ("packages.py", "install_package", "Load")])
    other = {**sources, "skills.py": sources["skills.py"] + (
        "\n\ndef _peek(user):\n    return user.has_permission(f\"admin:members:manage\")\n")}
    assert admin_right_reads(other) == sorted([*_ADMIN_READS, ("skills.py", "_peek", "constant")])
