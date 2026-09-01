"""The §5 role matrix, checked against the SOURCE — no database needed.

Spec: ``project-docs/specs/customer_console.md`` §5 · CP-12e.

🔴 **These three moved out of `test_operator_roles.py` on 2026-08-30, and the
reason is the whole point.** That module is R8-gated: a `pytestmark` skips
every test in it unless `CUSTOMER_CONSOLE_DATABASE_URL` names a real Postgres.
Correct for the forty-odd tests that drive the API. Wrong for these three,
which read a file and a dict and touch no database at all — so on a developer's
machine they could not run, and the only place they ever failed was CI, two and
a half minutes after a push.

That is exactly what happened: `GET /admin/usage/orgs` and
`GET /admin/usage/daily` shipped with an `Operator` gate and no matrix row, the
matrix failed closed, and a signed-in operator opening `/usage` would have been
refused with an unexplained 403. The fence caught it — one push too late.

⚠️ **Do not add a database-backed test to this file.** Its value is that it
runs everywhere, always. One `pytestmark` here would put all three back behind
the same gate they were just freed from.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]

_MAIN = (
    _ROOT / "apps" / "services" / "customer_console" / "customer_console"
    / "main.py"
)

#: The dependency names that mark a route as operator-reachable.
#: ⚠️ EVERY dual-arm gate belongs here. ResolveCaller was missing until
#: 2026-08-30, so this fence passed green while `/registry/resolve` had no
#: matrix row and every signed-in operator got 403 on it — the exact
#: "mystery 403" class the module docstring describes.
_GATES = (
    "Operator", "ProvisionCaller", "SeatAdminCaller", "MemberAdminCaller",
    "CatalogCaller", "ResolveCaller",
)

_ROUTE = re.compile(
    r'@app\.(get|post|patch|delete)\("([^"]+)"\)\s*\ndef \w+\(([^)]*)\)',
    re.S,
)


def test_every_operator_gated_route_has_a_matrix_row():
    """Done-when 14, as a SOURCE fence.

    ⚠️ This is the test that stops the matrix rotting. A new operator route
    added without a row is refused at RUNTIME (the matrix fails closed), which
    is safe but arrives as a mystery 403 in production. This makes it arrive
    when the developer runs the suite, which is what R7 asks a rule to do.
    """
    from customer_console import operator_roles

    src = _MAIN.read_text(encoding="utf-8")
    missing = []
    for m in _ROUTE.finditer(src):
        method, path, args = m.group(1).upper(), m.group(2), m.group(3)
        if not any(re.search(rf":\s*{g}\b", args) for g in _GATES):
            continue
        if operator_roles.rule_for(method, path) is None:
            missing.append(f"{method} {path}")

    assert not missing, (
        "these operator-reachable routes have no row in the §5 matrix, so a "
        f"signed-in operator is refused with an unexplained 403: {missing}"
    )


def test_the_matrix_names_no_route_that_does_not_exist():
    """The mirror of the above — a row for a deleted route is a lie."""
    from customer_console import operator_roles

    src = _MAIN.read_text(encoding="utf-8")
    declared = {
        (m.group(1).upper(), m.group(2))
        for m in re.finditer(r'@app\.(get|post|patch|delete)\("([^"]+)"\)', src)
    }
    # CP-12d and CP-12f add `/operators*`; a row may legitimately land first.
    stale = [
        f"{k[0]} {k[1]}"
        for k in operator_roles.MATRIX
        if k not in declared and not k[1].startswith("/operators")
    ]
    assert not stale, f"the matrix names routes that do not exist: {stale}"


def test_every_matrix_row_names_a_known_role():
    from customer_console import operator_roles
    from customer_console.operators import ROLES

    for key, rule in operator_roles.MATRIX.items():
        assert rule.min_role in ROLES, f"{key} demands an unknown role"
