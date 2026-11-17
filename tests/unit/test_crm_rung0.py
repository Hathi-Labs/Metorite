"""CRM Rung 0 (CRM-Q34-1 and CRM-Q34-2, tasks 3693 and 3694): a number
range, a lookup by the id of a core record, and refusals that name a step
that works. Database-free, so it never skips.

Spec: ``project-docs/specs/crm_platform.md`` §18.17. The behaviour is proved
in ``test_crm_rung0_r8.py``.

The fence: ``aggregate._read`` reads the lookup ids, refuses ``q`` after it
reads the fields of the module, and reads ``@status`` for a status group.
``aggregate._compute`` adds the field hint to each 422 of a module read.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

_CRM = (
    Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"
)

#: function → the calls it must make, in ``aggregate.py``.
_PINS: dict[str, set[str]] = {
    "_read": {"lookup_ids", "module_where", "q_refusal", "status_field"},
    "_compute": {"field_hint"},
}


def _calls(source: str, name: str) -> set[str] | None:
    """The plain-name calls of the function *name* of *source*, or None."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return {
                n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    return None


def _offenders(source: str) -> list[str]:
    found = []
    for function, wanted in _PINS.items():
        calls = _calls(source, function)
        if calls is None:
            found.append(f"no function {function}")
            continue
        found.extend(f"{function} does not call {w}" for w in sorted(wanted - calls))
    return found


def test_the_module_read_calls_each_rung0_step() -> None:
    assert _offenders((_CRM / "aggregate.py").read_text(encoding="utf-8")) == []


def test_the_fence_sees_a_missing_step() -> None:
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    assert "_read does not call q_refusal" in _offenders(
        source.replace("raise q_refusal(scope)", "pass"))
    assert "_compute does not call field_hint" in _offenders(
        source.replace("raise await field_hint(", "raise (lambda *a: a)("))


# ── The pure parts ───────────────────────────────────────────────────────────


def _scope():
    from gateway.routes.crm import field_sql as fs

    def ref(key: str, kind: str, label: str | None = None) -> fs.FieldRef:
        return fs.FieldRef(key=key, type=kind, label=label or key)

    return fs.FieldScope(column="fields", owner="module:Clients", fields={
        "Fee": ref("Fee", "currency", "Annual Fee"), "Qty": ref("Qty", "number", "Quantity"),
        "Note": ref("Note", "text"), "Start": ref("Start", "date"),
        "Flag": ref("Flag", "boolean"),
        "Long": ref("Long", "text", "L'" + "x" * 200),
    })


def test_a_number_range_reads_the_guarded_number() -> None:
    from gateway.routes.crm import field_sql as fs

    where = fs.module_where(_scope(), "Clients", [("Fee.from", "1"), ("Qty.to", " 2.50 ")],
                            zone="UTC")
    assert ">= CAST(:agf_v0 AS numeric)" in where.sql
    assert "<= CAST(:agf_v1 AS numeric)" in where.sql
    # The number is the guarded one of the measure: a plain decimal or a JSON number.
    assert where.sql.count("jsonb_typeof") == 4
    assert (where.params["agf_v0"], where.params["agf_v1"]) == ("1", "2.50")
    assert (where.params["agf_k0_0"], where.params["agf_k1_0"]) == ("Fee", "Qty")
    assert where.source == fs.DETOAST_JOIN


@pytest.mark.parametrize(("name", "value", "words"), [
    ("Note.from", "1", "A range takes a date or a number field."),
    ("Flag.to", "1", "The date and number fields of module:Clients are: Fee, Qty, Start."),
    ("Fee.from", "abc", "'field.Fee.from' must be a number."),
    ("Fee.to", "1e1000000", "'field.Fee.to' must be a number of at most 60 digits"),
    ("Fee.from", "NaN", "'field.Fee.from' must be a number of at most"),
])
def test_an_unfit_range_is_a_422(name: str, value: str, words: str) -> None:
    from gateway.routes.crm import field_sql as fs

    with pytest.raises(HTTPException) as exc:
        fs.module_where(_scope(), "Clients", [(name, value)], zone="UTC")
    assert words in exc.value.detail["message"]


def test_the_unknown_field_names_the_close_fields() -> None:
    scope = _scope()
    for asked, close in (("Fees", "'Fee' (label 'Annual Fee')"),
                         ("quantity", "'Qty' (label 'Quantity')")):
        with pytest.raises(HTTPException) as exc:
            scope.ref(asked)
        detail = exc.value.detail
        assert detail["key"] == asked
        assert f"The closest fields of module:Clients are: {close}." in detail["message"]
    with pytest.raises(HTTPException) as exc:
        scope.ref("Xyzzy_Plugh")
    assert "closest" not in exc.value.detail["message"]
    # A label is cut to 60 characters, and its own quote is dropped.
    assert scope.closest("Long").startswith("'Long' (label 'L" + "x" * 59 + "')")
    # At most three, and a long name reads no match at all.
    assert len(scope.closest("F").split(", ")) <= 3
    assert scope.closest("x" * 101) == ""


def test_the_quote_of_org_text_is_cut_and_closed() -> None:
    from gateway.routes.crm import field_sql as fs

    quoted = fs._quoted("It's\n" + "y" * 100)
    assert quoted.startswith("'Its y") and quoted.endswith("'")
    assert len(quoted) == fs.HINT_CHARS + 2


async def test_the_hint_passes_each_other_refusal_through() -> None:
    from gateway.routes.crm import field_sql as fs

    class _NoDb:
        async def execute(self, *_a, **_k):
            raise AssertionError("no read for another refusal")

    for exc in (HTTPException(status_code=422, detail="plain"),
                HTTPException(status_code=404, detail={"code": "unknown_module"}),
                HTTPException(status_code=422, detail={"code": "unfit_field", "message": "m"}),
                HTTPException(status_code=422, detail={"code": "unknown_field", "key": "",
                                                       "message": "m"})):
        assert await fs.field_hint(_NoDb(), "Clients", exc) is exc


def test_q_stays_out_of_the_module_params() -> None:
    """``q`` on a module total is Rung 1 (CRM-Q34-7). Tonight it is a 422."""
    from gateway.routes.crm import aggregate as agg

    assert "q" not in agg.MODULE_PARAMS


def _lookup_scope(catalogue: bool = False, status: bool = False):
    from gateway.routes.crm import field_sql as fs

    def ref(key: str, kind: str, target: str | None = None) -> fs.FieldRef:
        return fs.FieldRef(key=key, type=kind, label=key, lookup_module=target)

    fields = {
        "Client": ref("Client", "lookup", "Contacts"),
        "Project": ref("Project", "lookup", "Projects"),
        "Remarks": ref("Remarks", "text"),
        "Stage": ref("Stage", "select"), "Kind": ref("Kind", "select"),
        "Fee": ref("Fee", "currency"),
    }
    if status:
        fields["@status"] = fields["Stage"]
    return fs.FieldScope(column="fields", owner="module:Bills", fields=fields,
                         catalogue=catalogue)


def test_q_on_a_module_names_the_step_and_the_fields() -> None:
    """CRM-Q34-2 item 1: the step that works comes first, then each lookup
    with its target module, then the text fields."""
    from gateway.routes.crm import field_sql as fs

    message = fs.q_refusal(_lookup_scope()).detail["message"]
    assert message.startswith(
        "A module total takes no q. To count the records of one name, give "
        "field.<lookup>=<the exact name>.")
    assert ("The lookup fields of module:Bills are: 'Client' (to 'Contacts'), "
            "'Project' (to 'Projects').") in message
    assert "The text fields of module:Bills are: Remarks." in message
    assert len(message) < 800


def test_q_on_a_wide_module_keeps_the_step_inside_the_cut() -> None:
    from gateway.routes.crm import field_sql as fs

    wide = {f"L{i:02d}": fs.FieldRef(key=f"L{i:02d}", type="lookup", label="x",
                                     lookup_module="M" * 80) for i in range(40)}
    scope = fs.FieldScope(column="fields", owner="module:Bills", fields=wide)
    message = fs.q_refusal(scope).detail["message"]
    assert message[:800].count("field.<lookup>=<the exact name>") == 1
    assert "and 30 more" in message
    assert ("M" * 61) not in message


def test_group_by_status_reads_the_status_field() -> None:
    """CRM-Q34-2 item 2: with a status key field, ``@status``. With none, a
    422 that names the select fields. An open question keeps its detail."""
    from gateway.routes.crm import field_sql as fs

    assert fs.status_field(_lookup_scope(status=True)) == "@status"
    for catalogue, code in ((False, "unfit_field"), (True, "no_key_field")):
        with pytest.raises(HTTPException) as exc:
            fs.status_field(_lookup_scope(catalogue=catalogue))
        detail = exc.value.detail
        assert detail["code"] == code
        assert detail["message"].startswith(
            "module:Bills names no status field, so group_by=status cannot group it. "
            "Give group_field=<key> with one of its select fields: Kind, Stage.")


def test_a_module_takes_group_by_status_at_the_check() -> None:
    from gateway.routes.crm import aggregate as agg

    assert agg._check("module:Bills", "count", "status", None, None, None) == (None, "Bills")
    with pytest.raises(HTTPException):
        agg._check("module:Bills", "count", "pipeline", None, None, None)
