"""The runner of the scripted replay: ``python -m evals.semantic`` (SEM-6).

It plays the query shapes that the CRM refused on 2026-11-17 against the REAL
``/crm`` routes, on a synthetic org, and checks each answer. It plays 20 fixed
questions the same way. It also calls the real ``crm_tools`` functions with a
stub gateway, to check the text that the chat reads. NO model runs. No vendor
key is read, and no call leaves this machine.

* ``--check`` loads the fixture, checks that it is whole, recomputes each
  expected value and prints the replay plan. It needs no database.
* ``--scripted`` seeds two fresh orgs in the database of
  ``TENANT_LADDER_DATABASE_URL``, replays everything, prints one PASS or FAIL
  line for each case, and removes the rows. It needs a local ladder database
  (``scripts/dev_db.sh``).

A scripted run proves the routes and the checks. It cannot prove the choice of
a model. Exit codes: 0 all pass, 1 a case failed, 2 the run cannot start.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from evals.semantic import checks, seed
from evals.semantic.questions import (
    QUESTIONS,
    Fixture,
    Step,
    load_fixture,
    org_map,
    params_of,
)

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

#: One answer of the route: the HTTP status and the JSON body.
Ask = Callable[[str, dict[str, str]], Awaitable[tuple[int, dict[str, Any]]]]


@dataclass(frozen=True)
class Result:
    id: str
    kind: str
    text: str
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


# ── The replay of the routes ────────────────────────────────────────────────


async def _safe(ask: Ask, org: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """One call. A route that raises is a 500 with the type of the error, never a crash."""
    try:
        return await ask(org, params)
    except Exception as exc:
        return 500, {"detail": f"the route raised {type(exc).__name__}"}


async def replay(ask: Ask, fx: Fixture, orgs: dict[str, str]) -> list[Result]:
    """Play the five cases, then the 20 questions, against the route behind *ask*."""
    out: list[Result] = []
    for case in fx.cases:
        failures: list[str] = []
        for i, step in enumerate(case.steps, start=1):
            status, body = await _safe(ask, orgs[step.query.org], params_of(step.query, orgs))
            failures += [f"step {i}: {f}" for f in checks.check_step(step, status, body)]
        out.append(Result(case.id, case.cls, case.text, tuple(failures)))
    for q in fx.questions:
        status, body = await _safe(ask, orgs[q.query.org], params_of(q.query, orgs))
        step = Step(q.query, 200, q.expect)
        out.append(Result(q.id, q.kind, q.text, tuple(checks.check_step(step, status, body))))
    return out


# ── The replay of the tool text ─────────────────────────────────────────────


@contextlib.contextmanager
def _stub_gateway(crm_tools: Any, answer: Callable[[str], Any]) -> Any:
    """Point ``crm_tools._get`` at *answer*, open a budget for one run, and put all back."""
    before = (crm_tools._get, crm_tools._short_note)

    async def _get(path: str, params: dict | None = None) -> Any:
        return answer(path)

    async def _no_note(_keys: Any) -> str:
        return ""

    crm_tools._get, crm_tools._short_note = _get, _no_note
    token = crm_tools.open_asks_for_run()
    crm_tools.mark_answer_tool(True)
    try:
        yield crm_tools.run_asks().reads
    finally:
        crm_tools.release_asks(token)
        crm_tools._get, crm_tools._short_note = before


async def tool_replay() -> list[Result]:
    """CRM-Q34-3: the text of a zero, the budget of a refusal, and the status step.
    A tool that raises fails its case with the type of the error."""
    out: list[Result] = []
    for case in (_t1_zero, _t2_budget, _t3_status):
        try:
            out.append(await case())
        except Exception as exc:
            name, text = _TOOL_CASES[case.__name__]
            out.append(Result(name[0], name[1], text, (f"raised {type(exc).__name__}",)))
    return out


_TOOL_CASES = {
    "_t1_zero": (("T1", "tool_zero"), "A total that matched nothing says so"),
    "_t2_budget": (("T2", "tool_budget"), "A refused total spends no total budget"),
    "_t3_status": (("T3", "tool_status"), "status= on a module names the working form"),
}


def _empty(path: str) -> Any:
    if path == "/crm/aggregate":
        return {
            "entity": "module:Invoices",
            "measure": "count",
            "groups": [],
            "total": 0,
            "records": 0,
        }
    return {}


async def _t1_zero() -> Result:
    from acb_skills import crm_tools

    with _stub_gateway(crm_tools, _empty):
        text = await crm_tools.crm_aggregate(
            "module:Invoices", field_filters={"Client": "Acme Ltd"}
        )
    return Result(
        "T1", "tool_zero", _TOOL_CASES["_t1_zero"][1], tuple(checks.check_zero_text(text))
    )


async def _t3_status() -> Result:
    from acb_skills import crm_tools

    with _stub_gateway(crm_tools, _empty):
        text = await crm_tools.crm_aggregate("module:Invoices", status="Paid")
    return Result(
        "T3",
        "tool_status",
        _TOOL_CASES["_t3_status"][1],
        tuple(checks.check_status_text(text, "Paid")),
    )


async def _t2_budget() -> Result:
    from acb_skills import crm_tools

    def refuse(path: str) -> Any:
        if path == "/crm/aggregate":
            raise crm_tools._GatewayError(
                "CRM GET /crm/aggregate failed (422)",
                detail="Unknown field 'Nope'.",
                status=422,
                route="/crm/aggregate",
            )
        return {}

    with _stub_gateway(crm_tools, refuse) as reads:
        refused = [
            await crm_tools.crm_aggregate("module:Sites", measure="sum", field="Nope")
            for _ in range(3)
        ]
        spent = reads.aggregates
        crm_tools._get = _answering(crm_tools)
        await crm_tools.crm_aggregate("module:Sites")
        answered = reads.aggregates - spent
    return Result(
        "T2",
        "tool_budget",
        _TOOL_CASES["_t2_budget"][1],
        tuple(checks.check_refusal_budget(refused, spent, answered)),
    )


def _answering(crm_tools: Any) -> Callable[..., Awaitable[Any]]:
    async def _get(path: str, params: dict | None = None) -> Any:
        return {"total": 4, "records": 4, "groups": []} if path == "/crm/aggregate" else {}

    return _get


# ── The report ──────────────────────────────────────────────────────────────


def render(results: list[Result]) -> str:
    lines = []
    for r in results:
        lines.append(f"{'PASS' if r.ok else 'FAIL'} {r.id} {r.kind}: {r.text}")
        lines += [f"     - {f}" for f in r.failures]
    bad = sum(1 for r in results if not r.ok)
    lines.append(f"{len(results) - bad} of {len(results)} pass")
    return "\n".join(lines)


def verdict(results: list[Result]) -> int:
    return EXIT_PASS if results and all(r.ok for r in results) else EXIT_FAIL


# ── --check ─────────────────────────────────────────────────────────────────


def check() -> int:
    """The set is whole, each expected value recomputes, and the plan is printed."""
    try:
        fx = load_fixture()
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return EXIT_FAIL
    steps = sum(len(c.steps) for c in fx.cases)
    print(
        f"synthetic set whole: {len(fx.questions)} questions, {len(fx.cases)} cases "
        f"({steps} steps), {len(fx.records)} records, {len(fx.raw['contacts'])} contacts"
    )
    probe = org_map("00000000-0000-4000-8000-00000000000a", "00000000-0000-4000-8000-00000000000b")
    for case in fx.cases:
        print(f"plan {case.id} {case.cls}: {len(case.steps)} calls")
        for step in case.steps:
            print(
                f"     GET /crm/aggregate?{urlencode(params_of(step.query, probe))} -> {step.status}"
            )
    print(f"plan {QUESTIONS} questions, plan 3 tool checks (T1 to T3)")
    return EXIT_PASS


# ── --scripted ──────────────────────────────────────────────────────────────


@contextlib.contextmanager
def crm_open() -> Any:
    """Open the CRM switch for every org of this process, and put it back."""
    from acb_common import get_settings

    settings = get_settings()
    missing = object()
    wanted = {"crm_enabled": True, "crm_orgs": "*"}
    before = {k: getattr(settings, k, missing) for k in wanted}
    for key, value in wanted.items():
        object.__setattr__(settings, key, value)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is missing:
                with contextlib.suppress(AttributeError):
                    object.__delattr__(settings, key)
            else:
                object.__setattr__(settings, key, value)


def member(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="sem6@semantic-eval.test",
        role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


@contextlib.asynccontextmanager
async def inprocess_ask() -> AsyncIterator[Ask]:
    """The real ``/crm`` router in this process. The tenant comes from the user.
    The caller has pointed ``acb_common.db`` at the database already."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    from tests.unit._crm_app import crm_app

    who: dict[str, Any] = {}

    async def _caller() -> Any:
        bind_tenant(who["user"].organization_id)
        return who["user"]

    app = crm_app()
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://sem6.test"
        ) as client:

            async def ask(org: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
                who["user"] = member(org)
                res = await client.get("/crm/aggregate?" + urlencode(params))
                return res.status_code, res.json()

            yield ask
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        release_tenant(scope)


async def _run_sql(org: str, plan: list[seed.Statement]) -> None:
    from acb_common.db import tenant_session
    from sqlalchemy import text

    async with tenant_session(org) as db:
        for sql, rows in plan:
            await db.execute(text(sql), rows)


class RoleBypassesRls(RuntimeError):
    """The database role skips row level security, so no isolation case could pass."""


async def _bypasses_rls(org: str) -> bool:
    from acb_common.db import tenant_session
    from sqlalchemy import text

    async with tenant_session(org) as db:
        return bool(
            (
                await db.execute(
                    text(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).scalar()
        )


async def scripted_run(url: str) -> list[Result]:
    """Seed two fresh orgs, replay the routes and the tool text, remove the rows.
    The role of *url* must be subject to row level security: the isolation cases
    (org B never reaches org A) rest on it."""
    from tests.unit._tenant_ladder import tenant_engine_scope

    fx = load_fixture()
    ids = {"a": str(uuid.uuid4()), "b": str(uuid.uuid4())}
    async with tenant_engine_scope(url):
        if await _bypasses_rls(ids["a"]):
            raise RoleBypassesRls
        try:
            for label, org in ids.items():
                await _run_sql(
                    org, [seed.organization(org, label), *seed.statements(org, label, fx)]
                )
            with crm_open():
                async with inprocess_ask() as ask:
                    results = await replay(ask, fx, org_map(ids["a"], ids["b"]))
        finally:
            for org in ids.values():
                await _run_sql(org, seed.purge(org, with_org=True))
    return [*results, *await tool_replay()]


def is_loopback(url: str) -> bool:
    host = urlsplit(url.replace("+psycopg", "").replace("+asyncpg", "")).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def scripted(url: str) -> int:
    if not url:
        print(
            "NO-GO: TENANT_LADDER_DATABASE_URL is not set. "
            "Run scripts/dev_db.sh and export it. --check needs no database."
        )
        return EXIT_NO_GO
    if not is_loopback(url):
        print("NO-GO: the database is not on this machine. The replay seeds a local ladder only.")
        return EXIT_NO_GO
    try:
        results = asyncio.run(scripted_run(url))
    except RoleBypassesRls:
        print(
            "NO-GO: the database role is a superuser or has BYPASSRLS, so org B would read "
            "org A. Use a role that row level security binds (see README.md, 'Run it')."
        )
        return EXIT_NO_GO
    print(render(results))
    return verdict(results)


# ── the command line ────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evals.semantic", description=__doc__.splitlines()[0]
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="the set is whole (no database)")
    mode.add_argument(
        "--scripted",
        action="store_true",
        help="replay the routes on a synthetic org (needs a local ladder database)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return check()
    return scripted(os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip())


if __name__ == "__main__":
    sys.exit(main())
