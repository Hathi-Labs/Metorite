"""R8 — ``q`` across a module, on the list and the total (WS-53 CRM-Q34-7).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-7.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. The seed goes in as the
superuser, so each read below is the first thing the app role does.

1. The list and the total agree: the count of the total is the total of the
   list, for one word, two words, a lookup name, a text value, a near
   spelling and a zero.
2. Org B stays out: no record and no name of org B in an answer of org A,
   in its near names or in its nearest names.
3. ``%``, ``_``, ``\\`` and a quote are literal. An accent and any case
   match. An empty ``q`` and a lone ``&`` are no ``q``. ``&``, ``/`` and
   ``-`` between two words are no word, as in the core lists. 201
   characters and a NUL are a 422. 9 words are one phrase.
4. The flag: off, the total keeps the 422 of CRM-Q34-2 and the list keeps its
   answer, byte for byte. On, the new answer.
5. A near spelling is marked, and a zero names at most 3 nearest names.
6. 30,000 rows of about 3 KB: the total and the list page answer inside the
   10 s statement timeout, with one statement for the total and the count and
   the page for the list. The plan of the total is printed.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_aggregate_fields_r8 import (
    _TABLES,
    _admin,
    _call,
    _defs,
    _mirror_org,
    _module,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: The LOCAL statement_timeout of the aggregate, in seconds.
TIMEOUT_S = 10.0
#: The bound of a whole answer with a near hit over 30,000 rows of 3 KB.
#: Measured on the box with the first fix round, then doubled and more,
#: and it stays under the timeout. See the report of task 3700.
NEAR_HIT_BOUND_S = 5.0

_DEFS = [
    ("Customer", "lookup", {"lookup_module": "Contacts"}, False, False),
    ("Subject", "text", None, False, False),
    ("Notes", "textarea", None, False, False),
    ("Amount", "currency", None, False, False),
    ("Status", "select", None, False, False),
    ("Meta", "text", None, False, False),
]

#: Org A: (name, customer, subject, notes).
_ROWS_A = [
    ("INV-001", "Sharat Kumar", "Fiber rent 50% off", None),
    ("INV-002", "Acme Ltd", "under_score item 500", None),
    ("INV-003", "Acme West", "back\\slash path", "Café opening"),
    ("INV-004", "Acme East", "It's a quote", None),
    ("INV-005", "Acme North", "underXscore backslash plain", None),
    ("Monthly bill", "Gamma Traders", "Fiber", None),
    # No hyphen, slash or ampersand in this row (fix round, F1).
    ("Ltd bill", "Acme Ltd", "Plain", None),
    ("Freight job", "O'Brien Logistics", "Plain", None),
    # A text field that holds an object, and a lookup that holds an array
    # (fix round, P2-2 and F4).
    ("Meta row", ["Array lookup"], "Plain", None),
]
_ROWS_B = [("Secret B Name", "Sharath Secret", "Fiber rent", "Café opening")]


def _seed(p: Any, org: str, rows: list[tuple]) -> None:
    with p.admin_engine.begin() as c:
        for i, (name, customer, subject, notes) in enumerate(rows):
            fields: dict[str, Any] = {
                "Customer": ({"id": f"z{i}", "name": customer} if isinstance(customer, str)
                             else customer),
                "Subject": subject, "Amount": 100 * (i + 1),
            }
            if name == "Meta row":
                fields["Meta"] = {"id": "m1", "name": "Ravi", "email": "x@y.test"}
            if notes:
                fields["Notes"] = notes
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Invoices', 'zoho', "
                ":e, :n, CAST(:f AS jsonb))"),
                {"o": org, "e": f"e{i}", "n": name, "f": json.dumps(fields)})


def _flag(monkeypatch: Any, orgs: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_module_find_orgs", orgs, raising=False)


@pytest.fixture
def ns(promoted, monkeypatch):
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


@pytest.fixture
def inv(ns):
    a, b = ns.org_a, ns.org_b
    _mirror_org(ns, a, [_module("Invoices")])
    _mirror_org(ns, b, [_module("Invoices")])
    _defs(ns, a, "module:Invoices", _DEFS)
    _defs(ns, b, "module:Invoices", _DEFS)
    _seed(ns, a, _ROWS_A)
    _seed(ns, b, _ROWS_B)
    return ns


async def _total(p: Any, org: str, **query: Any) -> Any:
    return await _call(p, _admin(org), "GET", "/crm/aggregate?" + urlencode(
        {"entity": "module:Invoices", **query}))


async def _list(p: Any, org: str, **query: Any) -> Any:
    return await _call(p, _admin(org), "GET", "/crm/modules/Invoices/records?" + urlencode(
        {"page_size": 100, **query}))


async def _both(p: Any, org: str, q: str) -> tuple[dict, dict]:
    total, page = await _total(p, org, q=q), await _list(p, org, q=q)
    assert total.status_code == 200, total.text
    assert page.status_code == 200, page.text
    return total.json(), page.json()


def _names(page: dict) -> list[str]:
    return sorted(r["name"] for r in page["rows"])


# ── 1. The list and the total agree ──────────────────────────────────────────


@pytest.mark.parametrize(("q", "names"), [
    ("fiber", ["INV-001", "Monthly bill"]),
    ("fiber rent", ["INV-001"]),
    ("acme", ["INV-002", "INV-003", "INV-004", "INV-005", "Ltd bill"]),
    ("opening", ["INV-003"]),
    ("Sharath", ["INV-001"]),
    ("Acmo", []),
])
async def test_the_total_counts_the_rows_of_the_list(inv, app_engine, q: str,
                                                     names: list[str]) -> None:
    total, page = await _both(inv, inv.org_a, q)
    assert _names(page) == names
    assert total["records"] == page["total"] == len(names)


async def test_q_with_the_same_subject_filter_counts_one_row_on_both(inv, app_engine) -> None:
    """The same filter on both sides: Subject is "under_score item 500".
    The total takes it as ``field.Subject``, and the list as
    ``custom.Subject``. The total takes no ``custom.<key>`` (a 422 of
    ``MODULE_PARAMS``), and the list takes no ``field.<key>`` (CRM-Q34-4)."""
    value = "under_score item 500"
    total = await _total(inv, inv.org_a, q="acme", **{"field.Subject": value})
    page = await _list(inv, inv.org_a, q="acme", **{"custom.Subject": value})
    assert total.json()["records"] == page.json()["total"] == 1
    assert [r["name"] for r in page.json()["rows"]] == ["INV-002"]


@pytest.mark.parametrize("q", ["Acme & Ltd", "Acme / Ltd", "Acme - Ltd"])
async def test_a_mark_between_two_words_is_no_word(inv, app_engine, q: str) -> None:
    """F1: the words of ``core.search_words``. «Ltd bill» holds "Acme Ltd"
    and no mark, so it matches only when the mark is dropped."""
    total, page = await _both(inv, inv.org_a, q)
    assert _names(page) == ["INV-002", "Ltd bill"]
    assert total["records"] == page["total"] == 2


@pytest.mark.parametrize(("q", "names"), [
    ("id", []), ("name", []), ("email", []), ("array", []), ("Ravi", ["Meta row"]),
])
async def test_a_json_value_is_never_text(inv, app_engine, q: str, names: list[str]) -> None:
    """P2-2 and F4: a text field that holds an object reads its name only,
    and a lookup that holds an array reads nothing."""
    total, page = await _both(inv, inv.org_a, q)
    assert _names(page) == names
    assert total["records"] == page["total"] == len(names)


async def test_the_offered_nearest_name_finds_the_record(inv, app_engine) -> None:
    """P2-1: a nearest name with an apostrophe, given back as q, finds it."""
    total, page = await _both(inv, inv.org_a, "Obrein Logistix")
    assert total["records"] == page["total"] == 0
    assert "O'Brien Logistics" in total["nearest"]
    assert total["nearest"] == page["nearest"]
    found, page = await _both(inv, inv.org_a, "O'Brien Logistics")
    assert found["records"] == page["total"] == 1 and _names(page) == ["Freight job"]


# ── 2. Org B stays out ───────────────────────────────────────────────────────


async def test_org_b_stays_out_of_each_answer_of_org_a(inv, app_engine) -> None:
    total, page = await _both(inv, inv.org_a, "Secret")
    assert total["records"] == page["total"] == 0
    for body in (total, page):
        assert not any("Secret" in n or "Sharath" in n for n in body.get("nearest") or [])
    total, page = await _both(inv, inv.org_a, "Sharath")
    assert total["near"] == page["near"] == ["Sharat Kumar"]
    assert [r["name"] for r in page["rows"]] == ["INV-001"]
    total_b, page_b = await _both(inv, inv.org_b, "fiber")
    assert total_b["records"] == 1 and _names(page_b) == ["Secret B Name"]


# ── 3. Literal wildcards, quotes, accents and case ───────────────────────────


@pytest.mark.parametrize(("q", "names"), [
    ("50%", ["INV-001"]),
    ("under_score", ["INV-002"]),
    ("back\\slash", ["INV-003"]),
    ("It's", ["INV-004"]),
    ("café", ["INV-003"]),
    ("OPENING", ["INV-003"]),
    ("FiBeR", ["INV-001", "Monthly bill"]),
])
async def test_each_character_of_q_is_literal(inv, app_engine, q: str,
                                              names: list[str]) -> None:
    total, page = await _both(inv, inv.org_a, q)
    assert _names(page) == names
    assert total["records"] == page["total"]


@pytest.mark.parametrize("q", ["", "&", " / "])
async def test_an_empty_q_is_no_q(inv, app_engine, q: str) -> None:
    total, page = await _both(inv, inv.org_a, q)
    assert total["records"] == page["total"] == len(_ROWS_A)


async def test_nine_words_are_one_phrase(inv, app_engine) -> None:
    total, page = await _both(inv, inv.org_a, "a b c d e f g h i")
    assert total["records"] == page["total"] == 0


@pytest.mark.parametrize("q", ["x" * 201, "a\x00b"])
async def test_an_unfit_q_is_a_422(inv, app_engine, q: str) -> None:
    assert (await _total(inv, inv.org_a, q=q)).status_code == 422
    assert (await _list(inv, inv.org_a, q=q)).status_code == 422


# ── 4. The flag ──────────────────────────────────────────────────────────────


async def test_the_flag_off_keeps_the_answers_of_before(inv, app_engine, monkeypatch) -> None:
    _flag(monkeypatch, "")
    refused = await _total(inv, inv.org_a, q="fiber")
    assert refused.status_code == 422
    assert refused.json()["detail"]["message"].startswith("A module total takes no q.")
    plain = await _list(inv, inv.org_a, q="fiber")
    assert plain.status_code == 200 and plain.json()["total"] == 0
    assert "nearest" not in plain.json() and "near" not in plain.json()
    before = (await _list(inv, inv.org_a, q="INV-001")).content
    _flag(monkeypatch, "*")
    after = (await _list(inv, inv.org_a, q="INV-001")).content
    assert before == after
    assert (await _total(inv, inv.org_a, q="fiber")).json()["records"] == 2
    _flag(monkeypatch, inv.org_b)
    assert (await _total(inv, inv.org_a, q="fiber")).status_code == 422


# ── 5. A near spelling, and the nearest names ────────────────────────────────


async def test_a_near_spelling_is_marked(inv, app_engine) -> None:
    total, page = await _both(inv, inv.org_a, "Sharath")
    assert total["near"] == ["Sharat Kumar"] and "nearest" not in total
    row = page["rows"][0]
    assert row["near"] is True and row["matched"] == "Sharat Kumar"
    exact, _page = await _both(inv, inv.org_a, "Sharat")
    assert "near" not in exact and exact["records"] == 1


async def test_a_zero_names_at_most_three_nearest_names(inv, app_engine) -> None:
    total, page = await _both(inv, inv.org_a, "Acmo")
    assert total["records"] == page["total"] == 0
    assert total["nearest"] == page["nearest"] == ["Acme East", "Acme Ltd", "Acme North"]
    nothing, _page = await _both(inv, inv.org_a, "zzqqxx")
    assert nothing["nearest"] == []


# ── 6. 30,000 rows of 3 KB ───────────────────────────────────────────────────

_BIG = 30_000
#: 60 text values of 32 characters each, and a lookup: about 3 KB a row.
#: Each text value holds the letters a to p only, so a digit or the word
#: "party" is only in the name or the lookup name.
_BIG_SQL = (
    "INSERT INTO crm_module_records (organization_id, module, ext_source, ext_id, name, "
    "fields) SELECT CAST(:o AS uuid), 'Ledger', 'zoho', 'l' || g, 'Ledger row ' || g, "
    "(SELECT jsonb_object_agg('Field_' || k, translate(md5(g::text || '-' || k), "
    "'0123456789', 'ghijklmnop')) "
    " FROM generate_series(1, 60) k) || jsonb_build_object("
    "'Party', jsonb_build_object('id', 'p' || (g % 500), 'name', 'Party ' || (g % 500)), "
    "'Amount', g % 1000) FROM generate_series(1, :n) g"
)
_BIG_DEFS = [("Party", "lookup", {"lookup_module": "Accounts"}, False, False)] + [
    (f"Field_{k}", "text", None, False, False) for k in range(1, 46)
] + [("Amount", "currency", None, False, False)]


@pytest.fixture
def big(ns):
    a = ns.org_a
    _mirror_org(ns, a, [_module("Ledger")])
    _defs(ns, a, "module:Ledger", _BIG_DEFS)
    with ns.admin_engine.begin() as c:
        c.execute(text(_BIG_SQL), {"o": a, "n": _BIG})
        c.execute(text("ANALYZE crm_module_records"))
    return ns


async def _timed(p: Any, org: str, paths: dict[str, str]) -> dict[str, tuple[int, dict, float]]:
    """Each path through ONE app, engine and client, after one warm-up call."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    user = _admin(org)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    from tests.unit._crm_app import crm_app

    app = crm_app()
    app.dependency_overrides[get_current_user] = _caller
    out: dict[str, tuple[int, dict, float]] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            warm = await client.get("/crm/aggregate?entity=module:Ledger")
            assert warm.status_code == 200, warm.text
            for name, path in paths.items():
                start = time.perf_counter()
                res = await client.get(path)
                out[name] = (res.status_code, res.json(), time.perf_counter() - start)
    finally:
        release_tenant(scope)
    return out


async def test_thirty_thousand_rows_answer_inside_the_timeout(big, app_engine) -> None:
    from gateway.routes.crm import field_sql as fs
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    a = big.org_a
    seen: list[str] = []

    def _record_sql(_conn, _cursor, statement, *_args) -> None:
        seen.append(statement)

    agg = "/crm/aggregate?entity=module:Ledger&"
    lst = "/crm/modules/Ledger/records?page_size=50&"
    paths = {
        "total": agg + urlencode({"q": "Party 42"}),
        "list": lst + urlencode({"q": "Party 42"}),
        "total_zero": agg + urlencode({"q": "Pardy zzqq"}),
        "list_zero": lst + urlencode({"q": "Pardy zzqq"}),
        "total_near": agg + urlencode({"q": "Partyy"}),
        "list_near": lst + urlencode({"q": "Partyy"}),
    }
    event.listen(Engine, "before_cursor_execute", _record_sql)
    try:
        timed = await _timed(big, a, {"total": paths["total"]})
        total_sql = [s for s in seen if "ILIKE ALL" in s]
        seen.clear()
        timed.update(await _timed(big, a, {"list": paths["list"]}))
        list_sql = [s for s in seen if "ILIKE ALL" in s]
        seen.clear()
        timed.update(await _timed(big, a, {k: paths[k] for k in ("total_zero", "list_zero")}))
        near_sql = [s for s in seen if "unnest(ARRAY[" in s]
        seen.clear()
        timed.update(await _timed(big, a, {"total_near": paths["total_near"]}))
        hit_total = [s for s in seen if "crm_module_records" in s]
        seen.clear()
        timed.update(await _timed(big, a, {"list_near": paths["list_near"]}))
        hit_list = [s for s in seen if "crm_module_records" in s]
        hit_timeout = [s for s in seen if "statement_timeout" in s]
    finally:
        event.remove(Engine, "before_cursor_execute", _record_sql)
    print("\nCRM-Q34-7 30,000 rows of 3 KB, seconds:",
          {name: round(t, 3) for name, (_s, _b, t) in timed.items()})
    for name, (status, _body, seconds) in timed.items():
        assert status == 200, (name, _body)
        assert seconds < TIMEOUT_S, (name, seconds)
    # The count of plain SQL over the stored rows, as the superuser.
    with big.admin_engine.begin() as c:
        expected = c.execute(text(
            "SELECT count(*) FROM crm_module_records WHERE organization_id = "
            "CAST(:o AS uuid) AND module = 'Ledger' AND (name ILIKE '%42%' OR "
            "fields -> 'Party' ->> 'name' ILIKE '%42%')"), {"o": a}).scalar()
    total_body, list_body = timed["total"][1], timed["list"][1]
    assert expected > 50
    assert total_body["records"] == list_body["total"] == expected
    assert len(list_body["rows"]) == 50
    assert timed["total_zero"][1]["records"] == timed["list_zero"][1]["total"] == 0
    # The warm-up has no q. ONE statement of the total reads q, and the list
    # reads it in its count and its page. Each joins the detoasted copy once.
    assert len(total_sql) == 1 and len(list_sql) == 2, (len(total_sql), len(list_sql))
    assert all(s.count("CROSS JOIN LATERAL") == 1 for s in total_sql + list_sql)
    assert len(near_sql) == 2  # one bounded near read for each zero answer
    # A near HIT (fix round, P3): "Partyy" is in no row, and the 10 first
    # "Party N" names by name are near, each on 60 rows. The whole answer
    # is the exact read, the near read and one more read of the rows.
    print("CRM-Q34-7 near hit, statements on crm_module_records:",
          {"total": len(hit_total), "list": len(hit_list)})
    near_total, near_list = timed["total_near"][1], timed["list_near"][1]
    assert near_total["records"] == near_list["total"] == 10 * (_BIG // 500)
    assert near_total["near"] == near_list["near"] and len(near_total["near"]) == 10
    assert all(r["near"] is True for r in near_list["rows"])
    for name in ("total_near", "list_near"):
        assert timed[name][2] < NEAR_HIT_BOUND_S, (name, timed[name][2])
    # F2: the list's find reads run under the LOCAL statement timeout.
    # The warm-up total sets it once, and the list sets it once.
    assert len(hit_timeout) >= 2, hit_timeout
    # The plan of the total, as the superuser, with the same predicate.
    scope = fs.FieldScope(column="fields", owner="module:Ledger", fields={
        key: fs.FieldRef(key=key, type=kind, label=key) for key, kind, *_r in _BIG_DEFS})
    found = fs.find_where(scope, ["Party", "42"])
    with big.admin_engine.begin() as c:
        plan = [r[0] for r in c.execute(text(
            "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM crm_module_records"
            f"{found.source} WHERE organization_id = CAST(:o AS uuid) AND module = 'Ledger' "
            f"AND {found.sql}"), {"o": a, **found.params})]
    print("\n".join(plan))
    took = next(line for line in plan if line.startswith("Execution Time"))
    assert float(took.split()[2]) < TIMEOUT_S * 1000, took
