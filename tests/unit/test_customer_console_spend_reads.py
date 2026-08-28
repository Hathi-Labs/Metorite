"""The customer's spend surface never names a model. CP-7 slice 1 · D66.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-7 · **D32.7** · **D66**.

⚠️ **Hermetic ON PURPOSE, and it is the one file in this slice that is.** The
R8 suite beside it (``test_customer_console_sql.py::TestSpendReads``) skips
when no Postgres is configured, and this rule must never skip: it is the rule
a well-meaning future agent breaks by adding one obviously-useful column.

⚠️ **These assertions read the AST, never the raw file text.** The repo has
burned five fences that matched their own explanatory prose — a comment saying
"never select `model`" satisfies a `"model" not in source` check written the
lazy way. So the check below extracts SQL **string literals** from inside the
two functions and looks only at those. Docstrings are excluded explicitly,
because a docstring is a string literal too.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "apps/services/customer_console/customer_console"
STORE = SRC / "store.py"
MAIN = SRC / "main.py"

#: The two reads D66 (a) and (b) are served by. Both are customer-facing.
SPEND_READS = ("usage_by_activity", "usage_by_member")

#: Column names that would put a model, a provider or a tier on a customer's
#: screen. `tier` is here too: D32.7 lets a customer NAME a tier when calling,
#: and that is not the same as itemising their bill by one.
FORBIDDEN = ("model", "provider", "tier")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"{name} is gone from store.py. If it was renamed, rename it here too "
        "— do not delete this fence."
    )


def _sql_literals(fn: ast.FunctionDef) -> str:
    """Every string literal in the body EXCEPT the docstring.

    The docstring is dropped because it is where the reasoning lives, and
    reasoning about the word "model" must not trip a check about selecting it.
    """
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    return " ".join(
        node.value
        for stmt in body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


@pytest.fixture(scope="module")
def store_tree() -> ast.Module:
    return ast.parse(STORE.read_text(encoding="utf-8"))


class TestNoModelReachesTheCustomer:
    @pytest.mark.parametrize("fn_name", SPEND_READS)
    @pytest.mark.parametrize("column", FORBIDDEN)
    def test_the_spend_reads_select_no_model(self, store_tree, fn_name, column):
        """🔴 D32.7/D66: the customer sees the activity and the cost. Not this.

        A model column here is not a cosmetic slip. It re-teaches customers a
        vocabulary the product spent CP-5 deleting, and the Console answers a
        bare model id with 400 — so the name shown would be a name they cannot
        use.
        """
        sql = _sql_literals(_function(store_tree, fn_name))

        assert not re.search(rf"\b{column}\b", sql), (
            f"{fn_name} references {column!r} in its SQL. The customer's spend "
            f"surface names the ACTIVITY and the COST only (D66). If a model "
            f"breakdown is genuinely needed, it belongs on the OPERATOR "
            f"console, which already has one."
        )

    def test_the_extractor_reads_sql_and_ignores_prose(self):
        """The fence's own fence. A checker that cannot fail is not a check.

        Five source-reading fences in this repo passed while asserting nothing,
        because they matched their own explanatory comments. Proved here on a
        SYNTHETIC pair rather than on the real functions, so the proof does not
        break the day somebody rewords a docstring.
        """
        clean = ast.parse(
            'def f():\n'
            '    """This docstring says model, and that must not count."""\n'
            '    q = "SELECT agent, billed_credits FROM usage_event"\n'
        )
        dirty = ast.parse(
            'def f():\n'
            '    """Innocent prose."""\n'
            '    q = "SELECT model, billed_credits FROM usage_event"\n'
        )

        clean_sql = _sql_literals(_function(clean, "f"))
        dirty_sql = _sql_literals(_function(dirty, "f"))

        # It reads the query...
        assert "billed_credits" in clean_sql
        # ...it drops the docstring, so prose about a model is not a match...
        assert not re.search(r"\bmodel\b", clean_sql)
        # ...and it still catches a real one. Both directions, or it proves
        # nothing.
        assert re.search(r"\bmodel\b", dirty_sql)

    @pytest.mark.parametrize("view", ("ActivitySpendRow", "MemberSpendRow"))
    def test_the_wire_shape_has_no_model_field(self, view):
        """The other door. A field on the response model is how it ships even
        when the SQL is clean."""
        tree = ast.parse(MAIN.read_text(encoding="utf-8"))
        cls = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.ClassDef) and n.name == view), None,
        )
        assert cls is not None, f"{view} is gone from main.py"

        fields = [
            n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
        ]
        assert not [f for f in fields if f in FORBIDDEN], (
            f"{view} exposes {fields}. See D66."
        )
