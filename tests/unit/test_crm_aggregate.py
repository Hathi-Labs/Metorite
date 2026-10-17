"""The CRM aggregate routes have ONE filter builder (WS-53 CRM-C7, D10).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 2. The aggregate
takes the filters of the list routes. A second copy of them would drift, and
the totals of an agent would then disagree with the list on the screen. So
``aggregate.py`` must build its WHERE clause through ``records.list_query``
and nothing else. Database-free, so it never skips. The behaviour is proved
in ``test_crm_aggregate_r8.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

_CRM = (
    Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"
)

#: Text that only a copy of the list filters would hold.
_FILTER_TEXT = (
    "created_from", "updated_from", "close_from", "custom ->>",
    "lower(owner_email) = :owner", "OPEN_LEAD_SQL", "company_id = CAST",
)

#: The builders a second filter path would call.
_BUILDERS = frozenset({
    "extra_filters", "list_contract", "related_filters", "ExtraFilterInput",
})


def _offenders(source: str) -> list[str]:
    found = [f"text {t!r}" for t in _FILTER_TEXT if t in source]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _BUILDERS:
            found.append(f"name {node.id}")
        if isinstance(node, ast.alias) and node.name in _BUILDERS:
            found.append(f"import {node.name}")
    calls = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    if "list_query" not in calls:
        found.append("no call of list_query")
    return found


def test_the_aggregate_reuses_the_list_filter_builder() -> None:
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    assert _offenders(source) == []


def test_the_fence_sees_a_second_copy() -> None:
    """The fence on the fence: each kind of copy turns it red."""
    good = "from x import list_query\nasync def f():\n    q = await list_query(1)\n"
    assert _offenders(good) == []
    assert _offenders(good + "W = 'created_at >= CAST(:created_from AS date)'\n")
    assert _offenders(good + "from gateway.routes.crm.core import extra_filters\n")
    assert _offenders("def f():\n    return 1\n") == ["no call of list_query"]


# ── The field SQL has one home (WS-53 CRM-Q25, crm_platform.md §18.11) ──────

#: Text that only field SQL holds: a jsonb operator, a jsonb function, or the
#: guarded cast of a field value.
_FIELD_SQL_TEXT = ("->", "jsonb", "AS numeric")


def _field_sql_offenders(source: str) -> list[str]:
    """Each string constant of *source*, docstrings left out, that holds field
    SQL. Field SQL belongs in ``field_sql.py``, never in ``aggregate.py``."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value) for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef
                      | ast.ClassDef)
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            found.extend(
                f"text {t!r} in {node.value[:40]!r}"
                for t in _FIELD_SQL_TEXT if t in node.value
            )
    imports = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    if "gateway.routes.crm.field_sql" not in imports:
        found.append("no import of gateway.routes.crm.field_sql")
    return found


def test_the_aggregate_holds_no_field_sql() -> None:
    """Done-when 5: the field SQL lives in ``field_sql.py``, and the
    aggregate reaches it through that module only."""
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    assert _field_sql_offenders(source) == []
    assert (_CRM / "field_sql.py").is_file()


def test_the_field_sql_fence_sees_a_copy() -> None:
    """The fence on the fence: each kind of field SQL turns it red."""
    good = '"""Doc -> it."""\nfrom gateway.routes.crm.field_sql import x\n'
    assert _field_sql_offenders(good) == []
    assert _field_sql_offenders(good + "S = 'fields ->> :k'\n")
    assert _field_sql_offenders(good + "S = f\"jsonb_typeof({c})\"\n")
    assert _field_sql_offenders(good + "S = 'CAST(v AS numeric)'\n")
    assert _field_sql_offenders("X = 1\n") == [
        "no import of gateway.routes.crm.field_sql",
    ]


#: A name and a value that try to inject SQL. Each one must reach the
#: statement as a bound parameter and never as text.
_HOSTILE_KEY = "x') OR 1=1; DROP TABLE crm_deals; --"
_HOSTILE_VALUE = "v' OR '1'='1"


def test_the_field_sql_binds_every_name_and_value() -> None:
    """Done-when 5: even a key that passed the check never enters the SQL
    text. The builders bind it, for the measure, the group and each filter."""
    from gateway.routes.crm import field_sql as fs

    def ref(key: str, kind: str) -> fs.FieldRef:
        return fs.FieldRef(key=key, type=kind, label=key, options={})

    pieces: list[tuple[str, dict]] = []
    for kind in ("number", "currency"):
        scope = fs.FieldScope(column="fields", owner="module:X",
                              fields={_HOSTILE_KEY: ref(_HOSTILE_KEY, kind)})
        for measure in fs.FIELD_MEASURES:
            sql = fs.measure_sql(scope, scope.fields[_HOSTILE_KEY], measure)
            pieces.append((f"{sql.value} {sql.sum} {sql.count}", sql.params))
    for kind, bucket in (("select", None), ("boolean", None), ("lookup", None),
                         ("date", "month"), ("datetime", "quarter"),
                         ("date", "year")):
        scope = fs.FieldScope(column="custom", owner="deals",
                              fields={_HOSTILE_KEY: ref(_HOSTILE_KEY, kind)})
        sql = fs.group_sql(scope, scope.fields[_HOSTILE_KEY], bucket, zone="UTC")
        pieces.append((f"{sql.key} {sql.label}", sql.params))
    for kind, pairs in (
        ("select", [(_HOSTILE_KEY, _HOSTILE_VALUE)]),
        ("lookup", [(_HOSTILE_KEY, _HOSTILE_VALUE)]),
        ("multiselect", [(_HOSTILE_KEY, _HOSTILE_VALUE)]),
        ("number", [(_HOSTILE_KEY, "12.5")]),
        ("date", [(_HOSTILE_KEY + ".from", "2026-01-01"),
                  (_HOSTILE_KEY + ".to", "2026-12-31")]),
        ("datetime", [(_HOSTILE_KEY + ".from", "2026-01-01")]),
    ):
        scope = fs.FieldScope(column="fields", owner="module:X",
                              fields={_HOSTILE_KEY: ref(_HOSTILE_KEY, kind)})
        where = fs.module_where(scope, _HOSTILE_VALUE, pairs, zone="UTC")
        pieces.append((where.sql, where.params))
    for sql, params in pieces:
        assert _HOSTILE_KEY not in sql and _HOSTILE_VALUE not in sql, sql
        assert "x')" not in sql and "--" not in sql, sql
        assert _HOSTILE_KEY in params.values(), params


def test_the_field_scope_takes_only_its_two_columns() -> None:
    """The jsonb column is a literal of ``field_sql``, never caller text."""
    import pytest
    from gateway.routes.crm import field_sql as fs

    for column in ("custom", "fields"):
        fs.FieldScope(column=column, owner="deals", fields={})
    with pytest.raises(ValueError):
        fs.FieldScope(column="name; DROP TABLE x", owner="deals", fields={})


def test_the_list_routes_use_the_same_builder() -> None:
    """``records._list`` builds its query through ``list_query`` too."""
    tree = ast.parse((_CRM / "records.py").read_text(encoding="utf-8"))
    body = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_list"
    )
    calls = {
        n.func.id for n in ast.walk(body)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "list_query" in calls
    assert "extra_filters" not in calls and "list_contract" not in calls


# ── One zone for each month bucket (WS-53 CRM-Q25b, crm_platform.md §18.11) ──

_HOSTILE_ZONE = "Asia/Kolkata'; DROP TABLE crm_deals; --"


def test_each_month_group_reads_the_bound_zone() -> None:
    """Done-when 2: created_month and updated_month, of a module and of a
    core entity, take the month of ``<column> AT TIME ZONE :zone``. The
    close month is a date, and it takes no zone (done-when 7)."""
    from gateway.routes.crm import aggregate as agg
    from gateway.routes.crm import field_sql as fs

    bound = f"AT TIME ZONE CAST(:{fs.ZONE_PARAM} AS text)"
    for groups in (agg.GROUPS, agg.MODULE_GROUPS):
        for key in ("created_month", "updated_month"):
            assert bound in groups[key], (key, groups[key])
    assert "ZONE" not in agg.GROUPS["close_month"]


def test_a_datetime_bucket_and_range_read_the_zone_and_a_date_does_not() -> None:
    """Done-when 3 and 4: a datetime field reads its value with its offset
    and takes its day in the zone, for the bucket and for the range. A date
    keeps the day that Zoho wrote. The zone is a bound value, never text."""
    from gateway.routes.crm import field_sql as fs

    def scope(kind: str) -> fs.FieldScope:
        return fs.FieldScope(column="fields", owner="module:X", fields={
            "When": fs.FieldRef(key="When", type=kind, label="When", options={}),
        })

    stamp = fs.group_sql(scope("datetime"), scope("datetime").fields["When"],
                         "month", zone=_HOSTILE_ZONE)
    assert "AS timestamptz" in stamp.key and "AT TIME ZONE" in stamp.key
    assert stamp.params[fs.ZONE_PARAM] == _HOSTILE_ZONE
    day = fs.group_sql(scope("date"), scope("date").fields["When"], "month",
                       zone=_HOSTILE_ZONE)
    assert "timestamptz" not in day.key and "TIME ZONE" not in day.key
    ranged = fs.module_where(scope("datetime"), "X", [("When.from", "2026-11-01")],
                             zone=_HOSTILE_ZONE)
    assert "AT TIME ZONE" in ranged.sql and "AS timestamptz" in ranged.sql
    assert ranged.params[fs.ZONE_PARAM] == _HOSTILE_ZONE
    plain = fs.module_where(scope("date"), "X", [("When.from", "2026-11-01")],
                            zone=_HOSTILE_ZONE)
    assert "TIME ZONE" not in plain.sql
    for sql in (stamp.key, day.key, ranged.sql, plain.sql):
        assert "Kolkata" not in sql and "DROP" not in sql, sql


def test_the_aggregate_reads_one_zone_from_crm_zone() -> None:
    """Done-when 1: one function gives the zone, and it lives in
    ``field_sql.py``. ``aggregate.py`` calls it and names no zone itself."""
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "crm_zone" in calls
    # No second read of the profile's zone.
    assert "'time_zone'" not in source and '"time_zone"' not in source
    field_sql = (_CRM / "field_sql.py").read_text(encoding="utf-8")
    assert field_sql.count("async def crm_zone(") == 1


async def test_the_tool_names_the_zone_of_a_month(monkeypatch) -> None:
    """Done-when 5: the answer carries ``zone``, and crm_aggregate names it
    in one line when the answer counts months."""
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None) -> dict:
        return {
            "entity": "module:Products", "measure": "count", "zone": "Asia/Kolkata",
            "groups": [{"key": "2026-11", "label": "2026-11", "value": 1}],
            "total": 1, "other": 0, "omitted": 0,
        }

    monkeypatch.setattr(tools, "_get", _get)
    month = await tools.crm_aggregate("module:Products", group_by="created_month")
    assert "Months count in the CRM time zone, Asia/Kolkata." in month.splitlines()
    owner = await tools.crm_aggregate("module:Products", group_by="owner")
    assert "time zone" not in owner


@pytest.mark.parametrize(("field_type", "named"), [("datetime", True), ("date", False)])
async def test_the_tool_names_the_zone_only_for_a_datetime_bucket(
    monkeypatch, field_type: str, named: bool,
) -> None:
    """CRM-Q25b fix round, item 8. A ``date`` takes no zone, so the line
    would be false for a ``date`` bucket. The answer names the type of the
    group field, and the tool reads it."""
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None) -> dict:
        return {
            "entity": "module:Products", "measure": "count", "zone": "Asia/Kolkata",
            "group_field": "Day", "group_field_type": field_type, "bucket": "month",
            "groups": [{"key": "2026-11", "label": "2026-11", "value": 1}],
            "total": 1, "other": 0, "omitted": 0,
        }

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Products", group_field="Day", bucket="month")
    assert ("Months count in the CRM time zone, Asia/Kolkata." in out.splitlines()) is named


async def test_the_tool_says_an_empty_field_has_no_values(monkeypatch) -> None:
    """CRM-Q25c item 3: with ``values`` 0 the tool says the field is empty,
    and never "0"."""
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None) -> dict:
        return {
            "entity": "module:Products", "measure": "sum", "field": "Discount",
            "field_type": "currency", "values": 0, "total": None,
            "groups": [{"key": "a", "label": "a@x.test", "value": None}],
            "other": 0, "omitted": 0,
        }

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Products", group_by="owner",
                                    measure="sum", field="Discount")
    assert out == "Products, sum of Discount by owner: no values recorded for Discount."
    assert "0" not in out


# ── The key words of a module (WS-53 CRM-Q31-2, crm_platform.md §18.13.4) ───


def _entry(**parts: Any) -> Any:
    """A catalogue entry that names a key field for each part."""
    from gateway.crm_sync import catalogue as cat

    return cat.Entry(
        module="Invoices", entity="module:Invoices", kind="module", label="Invoices",
        records=3, purpose="invoice",
        fields={part: cat.Field(key=key, label=key, type="x") for part, key in parts.items()},
    )


def _invoice_scope(entry: Any) -> Any:
    from gateway.routes.crm import field_sql as fs

    real = {
        "Billed_Value": fs.FieldRef(key="Billed_Value", type="currency", label="Billed Value"),
        "Invoice_Date": fs.FieldRef(key="Invoice_Date", type="date", label="Invoice Date"),
        "Status": fs.FieldRef(key="Status", type="select", label="Status"),
        "Buyer": fs.FieldRef(key="Buyer", type="lookup", label="Buyer"),
        "Memo": fs.FieldRef(key="Memo", type="text", label="Memo"),
    }
    return fs.FieldScope(column="fields", owner="module:Invoices",
                         fields={**real, **fs.key_words(real, entry)})


def test_a_key_word_is_an_alias_of_the_real_field_when_its_type_fits() -> None:
    """Items 1 and 2: each key word is the real FieldRef, and joins only
    when its type fits."""
    scope = _invoice_scope(_entry(amount="Billed_Value", date="Invoice_Date",
                                  status="Status", parent="Buyer"))
    assert scope.fields["@amount"] is scope.fields["Billed_Value"]
    assert scope.fields["@date"] is scope.fields["Invoice_Date"]
    assert scope.fields["@status"] is scope.fields["Status"]
    assert scope.fields["@parent"] is scope.fields["Buyer"]
    wrong = _invoice_scope(_entry(amount="Memo", date="Status", status="Buyer",
                                  parent="Billed_Value"))
    assert not [k for k in wrong.fields if k.startswith("@")]
    # A field the module does not have, and no entry at all, give no word.
    assert not [k for k in _invoice_scope(_entry(amount="Gone")).fields if k.startswith("@")]
    assert not [k for k in _invoice_scope(None).fields if k.startswith("@")]


def test_a_key_word_with_no_key_field_is_a_422_that_names_the_module() -> None:
    """Item 3, for a measure, a group and a range filter."""
    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    scope = _invoice_scope(_entry(amount="Billed_Value"))
    for call in (
        lambda: fs.check_measure(scope, "@date"),
        lambda: fs.check_group(scope, "@status", None),
        lambda: fs.module_where(scope, "Invoices", [("@date.from", "2026-01-01")],
                                zone="UTC"),
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "no_key_field"
        assert "module:Invoices" in exc.value.detail["message"]
        assert "crm_list_modules" in exc.value.detail["message"]
    # A name that only looks like a key word is an unknown field.
    with pytest.raises(HTTPException) as exc:
        fs.check_measure(scope, "@amount'--")
    assert exc.value.detail["code"] == "unknown_field"


def test_sum_amount_on_a_module_is_the_sum_of_the_amount_word() -> None:
    """Item 4: the one rule of the measure lives in field_sql."""
    from gateway.routes.crm import field_sql as fs

    assert fs.module_measure("sum_amount", None) == ("sum", "@amount")
    assert fs.module_measure("avg", "Qty") == ("avg", "Qty")
    assert fs.module_measure("count", None) == ("count", None)


def test_a_key_word_never_reaches_the_sql_text() -> None:
    """Item 9: the builders bind the real key, never the key word."""
    from gateway.routes.crm import field_sql as fs

    scope = _invoice_scope(_entry(amount="Billed_Value", date="Invoice_Date",
                                  status="Status", parent="Buyer"))
    measure = fs.measure_sql(scope, fs.check_measure(scope, "@amount"), "sum")
    group = fs.group_sql(scope, fs.check_group(scope, "@date", "quarter"), "quarter",
                         zone="UTC")
    where = fs.module_where(scope, "Invoices", [
        ("@date.from", "2026-04-01"), ("@date.to", "2026-06-30"),
        ("@status", "Paid"), ("@parent", "x1"),
    ], zone="UTC")
    for sql, params in ((f"{measure.value} {measure.sum}", measure.params),
                        (group.key, group.params), (where.sql, where.params)):
        assert "@" not in sql, sql
        assert not [v for v in params.values() if isinstance(v, str) and v.startswith("@")]
    assert measure.params["agf_m"] == "Billed_Value"
    assert group.params["agf_g"] == "Invoice_Date"
    assert fs.used_keys(scope, ["@amount", "@date", "@date.from", "Memo", None]) == {
        "@amount": "Billed_Value", "@date": "Invoice_Date",
    }


#: Text that only a read of the catalogue or an alias rule would hold.
_ALIAS_NAMES = frozenset({
    "entry_of", "key_words", "KEY_WORDS", "MODULE_MEASURES", "AMOUNT_WORD", "catalogue",
})


def _alias_offenders(source: str) -> list[str]:
    """Each read of the catalogue or alias rule in *source*: a name of
    :data:`_ALIAS_NAMES`, or a string constant (docstrings left out) that
    holds a key word."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value) for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef
                      | ast.ClassDef)
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _ALIAS_NAMES:
            found.append(f"name {node.id}")
        if isinstance(node, ast.alias) and (
                node.name in _ALIAS_NAMES or "catalogue" in node.name):
            found.append(f"import {node.name}")
        if isinstance(node, ast.ImportFrom) and "catalogue" in (node.module or ""):
            found.append(f"import from {node.module}")
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings and "@" in node.value):
            found.append(f"text {node.value[:40]!r}")
    return found


def test_the_aggregate_holds_no_read_of_the_catalogue_and_no_alias_rule() -> None:
    """Item 8: both live in field_sql.py and catalogue.py. The aggregate
    reaches them through ``module_scope``, ``module_measure`` and
    ``used_keys`` only."""
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    assert _alias_offenders(source) == []
    calls = {
        n.func.id for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert {"module_scope", "module_measure", "used_keys"} <= calls


def test_the_alias_fence_sees_a_copy() -> None:
    """The fence on the fence."""
    good = '"""Doc @amount."""\nx = module_measure(1, 2)\n'
    assert _alias_offenders(good) == []
    assert _alias_offenders(good + "W = '@amount'\n")
    assert _alias_offenders(good + "from gateway.routes.crm.catalogue import entry_of\n")
    assert _alias_offenders(good + "from gateway.routes.crm.field_sql import KEY_WORDS\n")


# ── The tool lines of CRM-Q31-2 (items 6 and 7) ─────────────────────────────


async def test_the_tool_names_the_label_of_the_real_field(monkeypatch) -> None:
    """Item 6: the tool says which real field each key word used."""
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None) -> dict:
        return {
            "entity": "module:Invoices", "measure": "sum_amount",
            "field": "Billed_Value", "field_type": "currency",
            "field_label": "Billed Value", "group_field": "@date",
            "group_field_label": "Invoice Date", "group_field_type": "date",
            "bucket": "quarter", "keys": {"@amount": "Billed_Value",
                                          "@date": "Invoice_Date"},
            "groups": [{"key": "2026-Q2", "label": "2026-Q2", "value": 1250000}],
            "total": 1250000, "other": 0, "omitted": 0, "values": 7,
        }

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum_amount",
                                    group_field="@date", bucket="quarter")
    assert out.splitlines()[0] == (
        "Invoices, sum of Billed Value (the amount) by Invoice Date (quarter):"
    )
    assert "- 2026-Q2: INR 1,250,000" in out


def _empty_deals(alike: list[str], records: int = 0) -> Any:
    async def _get(path: str, params: dict | None = None) -> dict:
        if path == "/crm/catalogue":
            return {"mode": "mirror", "entries": [
                {"entity": "deals", "module": "Deals", "kind": "entity",
                 "label": "Deals", "records": records, "purpose": "deal",
                 "alike": alike},
            ]}
        return {"entity": "deals", "measure": "count", "groups": [], "total": 0,
                "other": 0, "omitted": 0}
    return _get


async def test_an_empty_core_entity_points_at_the_module(monkeypatch) -> None:
    """Item 7: a total of 0 on an empty core entity, in Mirror mode."""
    import acb_skills.crm_tools as tools

    monkeypatch.setattr(tools, "_get", _empty_deals(["Commercials", "Invoices"]))
    out = await tools.crm_aggregate("deals", group_by="owner")
    assert out.splitlines()[-1] == (
        "Deals holds no records. Commercials and Invoices hold sales."
    )
    monkeypatch.setattr(tools, "_get", _empty_deals(["Invoices"]))
    assert (await tools.crm_aggregate("deals", group_by="owner")).endswith(
        "Invoices holds sales.")
    # No line when the entity holds records, or no module fits.
    for getter in (_empty_deals(["Invoices"], records=4), _empty_deals([])):
        monkeypatch.setattr(tools, "_get", getter)
        assert "holds no records" not in await tools.crm_aggregate("deals", group_by="owner")


async def test_no_empty_core_line_when_the_catalogue_is_off(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None) -> dict:
        if path == "/crm/catalogue":
            raise tools._GatewayError("off", "catalogue_off")
        return {"entity": "deals", "measure": "count", "groups": [], "total": 0,
                "other": 0, "omitted": 0}

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("deals", group_by="owner")
    assert out == "Deals, count by owner:\nTotal: 0."


def test_the_catalogue_names_the_modules_that_fit_an_empty_core_entity() -> None:
    """Item 7, the data: ``alike`` holds each module with a fitting purpose
    and records. A module with no record is never named."""
    from gateway.crm_sync import catalogue as cat

    def module(api: str, label: str, records: int) -> cat.Source:
        return cat.Source(module=api, entity=f"module:{api}", kind="module",
                          label=label, records=records, custom=api != "Invoices")

    entries = {e.module: e for e in cat.build([
        cat.Source(module="Deals", entity="deals", kind="entity", label="Deals"),
        cat.Source(module="Accounts", entity="organizations", kind="entity",
                   label="Accounts", records=3),
        module("Invoices", "Invoices", 9), module("Quotes", "Quotes", 0),
        module("Buyer_Clients", "Clients", 2),
    ])}
    assert entries["Deals"].alike == ["Invoices"]
    assert entries["Accounts"].alike == ["Clients"]
    assert not entries["Invoices"].alike
