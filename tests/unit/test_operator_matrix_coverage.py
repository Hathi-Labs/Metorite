"""Every route an operator may call has a row in the role matrix.

Spec: ``customer_console.md`` §8 (CP-12, the operator roles).

🔴 **A missing row is a 403 for every signed-in operator, and nothing else
notices.** ``operator_roles.MATRIX`` fails closed, which is right. But the
shared operator token BYPASSES the matrix, and most route tests authenticate
with that token. So a new ``Operator`` route with no row passes its own tests
and refuses every real operator in production. Usage slice 2 shipped exactly
that for ``GET /admin/usage/breakdown``, and review caught it.

⚠️ **Hermetic, and it must stay so.** It reads ``main.py``'s AST and the
matrix constant. It needs no database, so it can never skip.
"""
from __future__ import annotations

import ast
import pathlib

MAIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "apps/services/customer_console/customer_console/main.py"
)
_VERBS = {"get", "post", "put", "patch", "delete"}

#: Every annotation whose dependency calls `require_operator` on its operator
#: arm, and so meets the matrix. Review found the first version scanned only
#: `Operator`, and five dual-arm aliases in `auth.py` open the same door.
OPERATOR_DOORS = {
    "Operator",
    "CatalogCaller",
    "ResolveCaller",
    "ProvisionCaller",
    "SeatAdminCaller",
    "MemberAdminCaller",
}


def _operator_routes() -> list[tuple[str, str]]:
    """Every ``(VERB, path)`` whose handler takes an ``Operator`` parameter."""
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        takes_operator = any(
            isinstance(a.annotation, ast.Name) and a.annotation.id in OPERATOR_DOORS
            for a in [*node.args.args, *node.args.kwonlyargs]
        )
        if not takes_operator:
            continue
        for d in node.decorator_list:
            if (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr in _VERBS
                and d.args
                and isinstance(d.args[0], ast.Constant)
                and isinstance(d.args[0].value, str)
            ):
                out.append((d.func.attr.upper(), d.args[0].value))
    return out


def test_the_scan_finds_the_operator_routes():
    """A fence that finds nothing passes for the wrong reason."""
    routes = _operator_routes()
    assert len(routes) > 20, f"found only {len(routes)} Operator routes"
    assert ("GET", "/admin/usage/orgs") in routes


def test_every_operator_route_has_a_matrix_row():
    from customer_console import operator_roles

    missing = [r for r in _operator_routes() if r not in operator_roles.MATRIX]
    assert not missing, (
        "Operator routes with no role row answer 403 to every signed-in "
        f"operator: {missing}. Add each to operator_roles.MATRIX."
    )


def test_every_door_name_is_still_a_real_alias():
    """A renamed alias would silently drop its routes out of the scan."""
    src = (MAIN.parent / "auth.py").read_text(encoding="utf-8")
    missing = [d for d in OPERATOR_DOORS if f"{d} =" not in src and f"{d}=" not in src]
    assert not missing, f"no longer defined in auth.py: {missing}"
