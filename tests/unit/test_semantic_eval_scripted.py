"""SEM-6: the scripted replay of the CRM refusal shapes, and the 20 fixed questions.

Owner: ``evals/semantic/`` (README.md names this file as its fence, R7).
Spec: ``project-docs/specs/crm_platform.md`` §18.17 (CRM-Q34-1 to CRM-Q34-3).

Two halves.

1. **No database, never skips.** The fixture is whole. Each expected value
   recomputes from the rows of the fixture. A row that looks real is refused.
   Each case has a check that FAILS on a broken answer (a 422 where a count was
   due, a silent zero, a refusal with no working step). No module of the eval
   reads a vendor key or calls a model. The tool text of CRM-Q34-3 passes
   against a stub gateway.
2. **R8, a real Postgres.** The replay runs on a seeded synthetic org and an
   org B, through the real ``/crm`` router, as the non-privileged role. Each case
   and each question must PASS. A skip here is not a pass.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from sqlalchemy import text

from evals.semantic import checks, run, seed
from evals.semantic.questions import (
    CASES,
    CLASSES,
    FIXTURE,
    QUESTIONS,
    Step,
    check_fixture,
    core_id,
    load_fixture,
    looks_real,
    org_map,
    params_of,
    parse,
    recompute,
    same,
)
from tests.unit.test_crm_aggregate_fields_r8 import _admin, _call, ns  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "evals" / "semantic"
FX = load_fixture()
ORGS = org_map("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")


def _raw() -> dict[str, Any]:
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))


# ── 1. The set is whole, and its values recompute ───────────────────────────


def test_the_set_is_whole() -> None:
    assert len(FX.questions) == QUESTIONS == 20
    assert [c.cls for c in FX.cases] == list(CLASSES) and len(FX.cases) == CASES == 5
    assert {q.query.org for q in FX.questions} == {"a", "b"}
    assert {q.kind for q in FX.questions} >= {
        "count",
        "sum",
        "month",
        "range",
        "lookup_name",
        "lookup_core",
        "status",
        "zero",
        "isolation",
    }


@pytest.mark.parametrize("q", FX.questions, ids=lambda q: q.id)
def test_each_expected_value_recomputes_from_the_rows(q) -> None:
    assert same(q.expect, recompute(FX, q.query)), (q.expect, recompute(FX, q.query))


def test_a_drifted_expected_value_fails_the_whole_set_check() -> None:
    raw = _raw()
    raw["questions"][0]["expect"]["total"] += 1
    with pytest.raises(ValueError, match="Q01"):
        check_fixture(parse(raw))
    raw = _raw()
    raw["records"].append(copy.deepcopy(raw["records"][0]) | {"ext_id": "i9"})
    with pytest.raises(ValueError, match="Q01"):
        check_fixture(parse(raw))


def test_the_empty_by_design_questions_are_zero_and_org_b_never_reaches_org_a() -> None:
    zero = next(q for q in FX.questions if q.kind == "zero")
    assert recompute(FX, zero.query) == {"total": 0.0}
    asked_in_b = next(q for q in FX.questions if q.id == "Q20")
    assert recompute(FX, asked_in_b.query) == {"total": 0.0}
    # Org A holds 8 invoices and org B 1: the totals never mix.
    totals = {q.id: recompute(FX, q.query)["total"] for q in FX.questions if q.id in ("Q01", "Q19")}
    assert totals == {"Q01": 8.0, "Q19": 1.0}


# ── 2. Nothing real in the set ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "a.person@gmail.com",
        "someone@company.in",
        "+91 98765 43210",
        "(415) 555 0132",
        "415-555-0132",
        "9876543210",
    ],
)
def test_a_value_that_looks_real_is_refused(text: str) -> None:
    assert looks_real(text)


@pytest.mark.parametrize(
    "text",
    [
        "x@semantic-eval.test",
        "a@b.example",
        "2026-04-02T10:00:00+05:30",
        "2026-01-15",
        "INV-001",
        "z-ct-100",
        "3280.5",
        "Acme Ltd",
    ],
)
def test_an_invented_value_passes(text: str) -> None:
    assert looks_real(text) is None


@pytest.mark.parametrize("bad", ["ravi@gmail.com", "+91 98765 43210"])
def test_a_fixture_row_that_looks_real_fails_the_load(bad: str) -> None:
    raw = _raw()
    raw["contacts"].append(
        {"org": "a", "key": "real", "first_name": bad, "last_name": "x", "zoho_id": None}
    )
    with pytest.raises(ValueError, match="looks real"):
        check_fixture(parse(raw))
    raw = _raw()
    raw["records"][0]["fields"]["Memo"] = bad
    with pytest.raises(ValueError, match="looks real"):
        check_fixture(parse(raw))


def test_the_shipped_fixture_holds_no_real_looking_value() -> None:
    assert [s for s in json.dumps(_raw(), ensure_ascii=False).split('"') if looks_real(s)] == []


# ── 3. Each check fails on a broken answer (mutations) ──────────────────────

STEPS = [(c.id, i, s) for c in FX.cases for i, s in enumerate(c.steps, start=1)] + [
    (q.id, 1, Step(q.query, 200, q.expect)) for q in FX.questions
]
_IDS = [f"{a}.{b}" for a, b, _ in STEPS]


@pytest.mark.parametrize("step", [s for _, _, s in STEPS], ids=_IDS)
def test_a_good_answer_passes_its_check(step: Step) -> None:
    status, body = checks.good_answer(step)
    assert checks.check_step(step, status, body) == []


@pytest.mark.parametrize("step", [s for _, _, s in STEPS], ids=_IDS)
def test_a_server_error_or_a_cast_error_fails_every_check(step: Step) -> None:
    assert checks.check_step(step, 500, {"detail": "boom"})
    status, _ = checks.good_answer(step)
    cast = {"detail": {"code": "x", "message": "invalid input syntax for type numeric: NA"}}
    assert checks.check_step(step, status, cast)


@pytest.mark.parametrize(
    "step",
    [s for _, _, s in STEPS if s.status == 200],
    ids=[i for i, (_, _, s) in zip(_IDS, STEPS, strict=True) if s.status == 200],
)
def test_a_422_where_an_answer_was_due_fails(step: Step) -> None:
    refusal = {"detail": {"code": "unfit_field", "message": "A range takes a date field."}}
    assert checks.check_step(step, 422, refusal)


@pytest.mark.parametrize(
    "step",
    [
        s
        for _, _, s in STEPS
        if s.status == 200 and (s.expect.get("total") or s.expect.get("groups"))
    ],
    ids=[
        i
        for i, (_, _, s) in zip(_IDS, STEPS, strict=True)
        if s.status == 200 and (s.expect.get("total") or s.expect.get("groups"))
    ],
)
def test_a_silent_zero_fails_where_a_count_was_due(step: Step) -> None:
    body: dict[str, Any] = {"total": 0, "records": 0, "groups": []}
    assert checks.check_step(step, 200, body)


@pytest.mark.parametrize(
    "step",
    [s for _, _, s in STEPS if s.status == 422],
    ids=[i for i, (_, _, s) in zip(_IDS, STEPS, strict=True) if s.status == 422],
)
def test_a_refusal_with_no_working_step_fails(step: Step) -> None:
    """The refusal of 2026-11-17: a 422 whose text names no field, module or filter."""
    bare = {"detail": {"code": step.expect["code"], "message": "The CRM refused an argument."}}
    assert checks.check_step(step, 422, bare)
    # The right words in the wrong order, and a 200 where a refusal was due, fail too.
    assert checks.check_step(step, 200, {"total": 0})
    wrong = {"detail": {"code": "other", "message": " ".join(step.expect["has"])}}
    assert checks.check_step(step, 422, wrong)
    if step.expect.get("order"):
        flipped = {
            "detail": {
                "code": step.expect["code"],
                "message": " ".join(reversed(step.expect["order"])),
            }
        }
        assert checks.check_step(step, 422, flipped)


def test_the_named_mutations_of_the_four_classes_and_the_silent_zero() -> None:
    case = {c.id: c for c in FX.cases}
    # Case 5: the silent zero for a contact that holds records.
    assert checks.check_step(case["C5"].steps[0], 200, {"total": 0, "records": 0})
    # Case 1: a 422 for a number range, and a cast error.
    assert checks.check_step(
        case["C1"].steps[0], 422, {"detail": {"code": "unfit_field", "message": "x"}}
    )
    # Case 1, step 3: a refusal that names no working step.
    assert checks.check_step(
        case["C1"].steps[2],
        422,
        {"detail": {"code": "unfit_field", "message": "A range takes a date or a number field."}},
    )
    # Case 2: the refusal names no module. Case 3: the list holds no near field.
    assert checks.check_step(
        case["C2"].steps[0],
        422,
        {"detail": {"code": "unknown_field", "message": "Its fields are: 'Opened'."}},
    )
    assert checks.check_step(
        case["C3"].steps[0],
        422,
        {"detail": {"code": "unknown_field", "message": "Its fields are: 'Opened', 'Site_Note'."}},
    )
    # Case 4: group_by=status refused with no select field named.
    assert checks.check_step(
        case["C4"].steps[0],
        422,
        {"detail": {"code": "unfit_field", "message": "Unknown group_by 'status'."}},
    )


# ── 4. The replay machinery, with a fake route ──────────────────────────────


def _fake(good: bool, *, silent_zero: bool = False):
    known = {
        (ORGS[s.query.org], tuple(sorted(params_of(s.query, ORGS).items()))): s for _, _, s in STEPS
    }

    async def ask(org: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        step = known[(org, tuple(sorted(params.items())))]
        status, body = checks.good_answer(step)
        if silent_zero and status == 200 and "total" in body:
            return 200, {"total": 0, "records": 0}
        return (status, body) if good else (500, {"detail": "boom"})

    return ask


async def test_the_replay_passes_on_a_route_that_answers_as_expected() -> None:
    results = await run.replay(_fake(True), FX, ORGS)
    assert len(results) == CASES + QUESTIONS and all(r.ok for r in results), run.render(results)


async def test_the_replay_fails_on_a_route_that_answers_a_silent_zero() -> None:
    results = {r.id: r for r in await run.replay(_fake(True, silent_zero=True), FX, ORGS)}
    assert not results["C5"].ok and not results["Q15"].ok and not results["Q10"].ok
    assert results["Q18"].ok, "an empty-by-design question passes when it answers zero"
    assert run.verdict(list(results.values())) == run.EXIT_FAIL


async def test_the_replay_fails_on_a_route_that_raises() -> None:
    async def boom(org: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("secret detail")

    results = await run.replay(boom, FX, ORGS)
    assert not any(r.ok for r in results)
    assert "secret detail" not in run.render(results)


def test_a_core_value_is_the_metorite_id_of_the_contact_in_its_own_org() -> None:
    c5 = next(c for c in FX.cases if c.cls == "core_lookup_silent_zero")
    mine = params_of(c5.steps[0].query, ORGS)["field.Contact"]
    theirs = params_of(c5.steps[1].query, ORGS)["field.Contact"]
    assert mine == core_id(ORGS["a"], "ravi") and theirs == core_id(ORGS["b"], "ravi")
    assert mine != theirs


# ── 5. The tool text of CRM-Q34-3, against a stub gateway ───────────────────


async def test_the_tool_text_passes_against_a_stub_gateway() -> None:
    results = await run.tool_replay()
    assert [r.id for r in results] == ["T1", "T2", "T3"]
    assert all(r.ok for r in results), run.render(results)


def test_each_tool_check_fails_on_a_broken_text() -> None:
    assert checks.check_zero_text("Invoices, count: no values recorded for Total.")
    assert checks.check_zero_text("Invoices, count: No record matches these filters.") == []
    assert checks.check_zero_text("No record matches these filters. no values recorded for X")
    assert checks.check_refusal_budget(["Refused: x"] * 3, 3, 1)
    assert checks.check_refusal_budget(["Refused: x"] * 3, 0, 1) == []
    assert checks.check_refusal_budget(["Invoices total"], 0, 1)
    assert checks.check_status_text("Refused: Drop the status filter.", "Paid")
    assert (
        checks.check_status_text(
            "Refused: call crm_aggregate with entity 'module:X' and field_filters "
            "{'@status': 'Paid'}.",
            "Paid",
        )
        == []
    )


# ── 6. No model, no vendor key, no connection site ──────────────────────────

_BANNED = re.compile(
    r"litellm|anthropic|openai|acb_llm|api_key|apikey|bearer|secret_key", re.IGNORECASE
)
_ENV_ALLOWED = {"TENANT_LADDER_DATABASE_URL"}


def _sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(EVAL.glob("*.py"))}


def test_no_module_of_the_eval_names_a_model_or_a_vendor_key() -> None:
    sources = _sources()
    assert {"run.py", "seed.py", "questions.py", "checks.py"} <= set(sources)
    for name, code in sources.items():
        assert not _BANNED.search(code), (name, _BANNED.search(code))


def test_the_only_environment_read_is_the_database_url() -> None:
    for name, code in _sources().items():
        tree = ast.parse(code)
        for node in ast.walk(tree):
            names = {getattr(node, "attr", None), getattr(node, "id", None)}
            if not names & {"environ", "getenv"}:
                continue
            literals = {
                n.value
                for n in ast.walk(tree)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and n.value.isupper()
                and "_" in n.value
            }
            assert literals & _ENV_ALLOWED, name
            assert not [v for v in literals if v.endswith(("_KEY", "_TOKEN", "_SECRET"))], name


def test_the_seed_and_the_checks_open_no_connection() -> None:
    sources = _sources()
    for name in ("seed.py", "checks.py", "questions.py"):
        assert not re.search(
            r"create_engine|create_async_engine|\.connect\(|psycopg|asyncpg|sessionmaker",
            sources[name],
        ), name


def test_the_readme_names_this_fence() -> None:
    readme = (EVAL / "README.md").read_text(encoding="utf-8")
    assert "tests/unit/test_semantic_eval_scripted.py" in readme


def test_the_check_mode_needs_no_database(capsys) -> None:
    assert run.main(["--check"]) == run.EXIT_PASS
    out = capsys.readouterr().out
    assert "synthetic set whole: 20 questions, 5 cases" in out


def test_the_scripted_mode_without_a_database_is_no_go(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TENANT_LADDER_DATABASE_URL", raising=False)
    assert run.main(["--scripted"]) == run.EXIT_NO_GO
    assert "NO-GO" in capsys.readouterr().out


def test_the_scripted_mode_refuses_a_database_that_is_not_local(monkeypatch) -> None:
    monkeypatch.setenv("TENANT_LADDER_DATABASE_URL", "postgresql+psycopg://u:p@db.example.com/x")
    assert run.main(["--scripted"]) == run.EXIT_NO_GO


# ── 7. R8: the replay on a real database ────────────────────────────────────
# ⚠️ No module-level pytestmark: the half above must never skip.


def _seed_both(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for label, org in (("a", p.org_a), ("b", p.org_b)):
            for sql, rows in seed.statements(org, label, FX):
                c.execute(text(sql), rows)


def _ask(p: Any):
    async def ask(org: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        res = await _call(p, _admin(org), "GET", "/crm/aggregate?" + urlencode(params))
        return res.status_code, res.json()

    return ask


@_DB_GATE
async def test_the_scripted_replay_passes_on_a_real_database(ns, app_engine) -> None:
    """Each of the 5 cases and the 20 questions answers as the fixture says, through
    the real router, as a role that row level security binds."""
    _seed_both(ns)
    results = await run.replay(_ask(ns), FX, org_map(ns.org_a, ns.org_b))
    assert len(results) == CASES + QUESTIONS
    failed = [r for r in results if not r.ok]
    assert not failed, "\n" + run.render(results)


@_DB_GATE
async def test_a_replay_with_the_orgs_swapped_fails_the_isolation_checks(ns, app_engine) -> None:
    """The checks are not blind: ask as the wrong org and the isolation cases FAIL."""
    _seed_both(ns)
    swapped = {"a": ns.org_b, "b": ns.org_a}
    results = {r.id: r for r in await run.replay(_ask(ns), FX, swapped)}
    assert not results["Q19"].ok or not results["Q01"].ok
    assert not results["C5"].ok


@_DB_GATE
def test_the_purge_removes_every_row_of_the_org(ns, app_engine) -> None:
    _seed_both(ns)
    with ns.admin_engine.begin() as c:
        for sql, rows in seed.purge(ns.org_a):
            c.execute(text(sql), rows)
        left = {
            t: c.execute(
                text(f"SELECT count(*) FROM {t} WHERE organization_id = CAST(:o AS uuid)"),
                {"o": ns.org_a},
            ).scalar()
            for t in seed.TABLES
        }
        other = c.execute(
            text(
                "SELECT count(*) FROM crm_module_records WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": ns.org_b},
        ).scalar()
    assert left == dict.fromkeys(seed.TABLES, 0)
    assert other > 0, "the purge of org A took a row of org B"
