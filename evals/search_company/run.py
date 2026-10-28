"""The runner of the retrieval eval: ``python -m evals.search_company`` (WS-KG KG-1).

It asks ``GET /search/company`` the questions of a set and scores where the
gold record lands (:mod:`evals.search_company.score`). No model runs. No
embedding call goes out, because ``KNOWLEDGE_EMBED`` stays off.

* ``--check`` reads the synthetic set and checks that it is whole. It needs no
  database. CI runs it (``skill-eval.yml``).
* ``--fixture`` seeds the synthetic set into a fresh org of the database in
  ``TENANT_LADDER_DATABASE_URL``, runs the route in this process, prints the
  score and removes the rows. The R8 suite
  (``tests/unit/test_search_company_eval_r8.py``) runs the same code on the
  non-privileged role, and pr-check runs it.
* ``--live URL --as EMAIL`` asks a running gateway over HTTP, as the member
  EMAIL, with the internal bearer from ``GATEWAY_INTERNAL_TOKEN`` (never
  printed). The set is a file off the repo: ``--set PATH`` or ``KG1_REAL_SET``.
  The URL must be a loopback address. The report holds counts, ratios,
  seconds, source names and question ids, and no words of a question.

Exit codes: 0 pass, 1 a bar failed, 2 the run cannot start.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import stat
import sys
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from evals.search_company import score
from evals.search_company.questions import (
    PER_SOURCE,
    SOURCES,
    Question,
    load_fixture,
    load_real,
    resolve,
)
from evals.search_company.score import EXIT_FAIL, EXIT_NO_GO, EXIT_PASS, K, Outcome

Ask = Callable[[str], Awaitable[dict[str, Any]]]

#: The path of the real set. It stays out of every worktree and every commit.
REAL_SET_ENV = "KG1_REAL_SET"
TOKEN_ENV = "GATEWAY_INTERNAL_TOKEN"


def registry_names() -> tuple[str, ...]:
    """Every source the route can ask, in the order of the route."""
    from gateway.retrieval.sources import REGISTRY

    return tuple(REGISTRY)


async def run_set(ask: Ask, questions: list[Question]) -> list[Outcome]:
    """Ask each question once, in order, and place its gold in the answer."""
    out: list[Outcome] = []
    for q in questions:
        started = time.perf_counter()
        try:
            answer = await ask(q.text)
        except Exception as exc:
            # The type only. The text of an error can carry the words.
            out.append(Outcome(q.id, q.source, None, None, time.perf_counter() - started,
                               error=type(exc).__name__))
            continue
        seconds = time.perf_counter() - started
        hits = list(answer.get("hits") or [])
        rank, place = score.locate(hits, q.source, q.gold)
        out.append(Outcome(
            q.id, q.source, rank, place, seconds,
            hits=dict(Counter(str(h.get("app")) for h in hits)),
            skipped=tuple(str(s) for s in answer.get("skipped") or ()),
        ))
    return out


# ── live: HTTP ──────────────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def http_asker(url: str, member: str, token: str) -> AsyncIterator[Ask]:
    """Ask a running gateway as *member*. The bearer goes in a header, and nowhere else."""
    import httpx

    headers = {"Authorization": f"Bearer {token}", "X-User-Email": member}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=30.0) as client:
        async def ask(words: str) -> dict[str, Any]:
            res = await client.get("/search/company", params={"q": words, "k": K})
            res.raise_for_status()
            return res.json()

        yield ask


def is_loopback(url: str) -> bool:
    from evals.coding_engine import preflight

    return preflight.is_local(url)


def read_real_set(path: str) -> tuple[list[Question] | None, str]:
    """The real set, or ``None`` and the reason. It must be a private file."""
    if not path or not Path(path).is_file():
        return None, "no real set"
    mode = Path(path).stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return None, "the real set is readable by others: chmod 600 it"
    return load_real(Path(path)), ""


def live(url: str, member: str, set_path: str, told: dict[str, str]) -> int:
    questions, why = read_real_set(set_path)
    if questions is None:
        print(why)
        return EXIT_NO_GO
    if not is_loopback(url):
        print("NO-GO: the live run asks a gateway on this machine only (loopback URL).")
        return EXIT_NO_GO
    token = os.environ.get(TOKEN_ENV, "")
    if not token or "@" not in member:
        print(f"NO-GO: set {TOKEN_ENV} and give --as a member address.")
        return EXIT_NO_GO

    async def go() -> list[Outcome]:
        async with http_asker(url, member, token) as ask:
            return await run_set(ask, questions)

    summary = score.summarize(asyncio.run(go()), registry_names())
    print(score.render(summary, mode="live", flags=told))
    # A live run reports. A gated-off source (Fracktal's CRM or Knowledge) is
    # an empty source in the report, and it is not a fault of the build.
    return EXIT_FAIL if summary.errors else EXIT_PASS


# ── fixture: in this process ────────────────────────────────────────────────


@contextlib.contextmanager
def switches(org: str) -> Any:
    """Open CRM and Knowledge for *org*, widen the time budgets, and put all back.

    A cold scratch database is slower than the route's budget, and a source cut
    off by the deadline also gives nothing. ``KNOWLEDGE_EMBED`` stays off.
    """
    from acb_common import get_settings
    from gateway.retrieval import company

    settings = get_settings()
    wanted = {"crm_enabled": True, "crm_orgs": org, "knowledge_enabled": True,
              "knowledge_orgs": org, "knowledge_embed": ""}
    missing = object()
    before = {k: getattr(settings, k, missing) for k in wanted}
    budgets = (company.SOURCE_TIMEOUT_S, company.TOTAL_BUDGET_S)
    for key, value in wanted.items():
        object.__setattr__(settings, key, value)
    company.SOURCE_TIMEOUT_S, company.TOTAL_BUDGET_S = 30.0, 60.0
    try:
        yield
    finally:
        company.SOURCE_TIMEOUT_S, company.TOTAL_BUDGET_S = budgets
        for key, value in before.items():
            if value is missing:
                with contextlib.suppress(AttributeError):
                    object.__delattr__(settings, key)
            else:
                object.__setattr__(settings, key, value)


def flags_of_settings() -> dict[str, str]:
    from acb_common import get_settings

    settings = get_settings()
    return {
        "CONTEXT_RECENCY": "on" if getattr(settings, "context_recency", False) is True else "off",
        "COMPANY_SEARCH_ENABLED":
            "on" if getattr(settings, "company_search_enabled", False) is True else "off",
    }


def member_of(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    from evals.search_company.seed import PERMISSIONS, member

    return UserContext(email=member(org), role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(list(PERMISSIONS)))


@contextlib.asynccontextmanager
async def inprocess_asker(user: Any) -> AsyncIterator[Ask]:
    """The route through FastAPI in this process. The tenant comes from the user.

    The caller has pointed ``acb_common.db`` at the database already
    (``tests.unit._tenant_ladder.tenant_engine_scope``).
    """
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.retrieval import company

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(company.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://kg1.test") as client:
            async def ask(words: str) -> dict[str, Any]:
                res = await client.get("/search/company", params={"q": words, "k": K})
                res.raise_for_status()
                return res.json()

            yield ask
    finally:
        release_tenant(scope)


async def run_fixture_in(org: str) -> list[Outcome]:
    """Run the 50 synthetic questions for a seeded *org*, in this process."""
    questions = resolve(load_fixture(), org)
    with switches(org):
        async with inprocess_asker(member_of(org)) as ask:
            return await run_set(ask, questions)


async def fixture_run(url: str) -> tuple[score.Summary, dict[str, str]]:
    """Seed a fresh org, run the questions, remove the rows."""
    from acb_common.db import tenant_session
    from sqlalchemy import text

    from evals.search_company import seed
    from tests.unit._tenant_ladder import tenant_engine_scope

    org = str(uuid.uuid4())
    fixture = load_fixture()
    async with tenant_engine_scope(url):
        async with tenant_session(org) as db:
            column = (await db.execute(text(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'email_messages' "
                "AND column_name = 'organization_id'"))).scalar()
            plan = seed.statements(org, fixture, message_org=bool(column))
            for sql, rows in [seed.organization(org), *plan]:
                await db.execute(text(sql), rows)
        try:
            outcomes = await run_fixture_in(org)
            flags = flags_of_settings()
        finally:
            async with tenant_session(org) as db:
                for sql, rows in seed.purge(org):
                    await db.execute(text(sql), rows)
    return score.summarize(outcomes, registry_names()), flags


def fixture(url: str) -> int:
    if not url:
        print("NO-GO: TENANT_LADDER_DATABASE_URL is not set. "
              "Run scripts/dev_db.sh and export it. --check needs no database.")
        return EXIT_NO_GO
    if not is_loopback(url.replace("+psycopg", "").replace("+asyncpg", "")):
        print("NO-GO: the database is not on this machine. The fixture seeds a local ladder only.")
        return EXIT_NO_GO
    summary, flags = asyncio.run(fixture_run(url))
    print(score.render(summary, mode="fixture", flags=flags, floor=score.FLOOR))
    return score.verdict(summary, score.FLOOR)


def check() -> int:
    """The synthetic set is whole. No database, no gateway."""
    try:
        fx = load_fixture()
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return EXIT_FAIL
    per = {s: sum(1 for q in fx.questions if q["source"] == s) for s in SOURCES}
    print(f"synthetic set whole: {len(fx.questions)} questions ({PER_SOURCE} per source: "
          + ", ".join(f"{s} {n}" for s, n in per.items()) + f"), {len(fx.records)} records")
    return EXIT_PASS


# ── the command line ────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.search_company",
                                description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="the synthetic set is whole (no database)")
    mode.add_argument("--fixture", action="store_true", help="seed the synthetic set and run it in process")
    mode.add_argument("--live", metavar="URL", help="ask a gateway on this machine over HTTP")
    p.add_argument("--as", dest="member", default="", help="live: the member address to act as")
    p.add_argument("--set", dest="set_path", default="",
                   help=f"live: the real set (default: ${REAL_SET_ENV})")
    p.add_argument("--flag", action="append", default=[], metavar="NAME=VALUE",
                   help="live: a flag value of the gateway, as the caller reads it (repeatable)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return check()
    if args.fixture:
        return fixture(os.environ.get("TENANT_LADDER_DATABASE_URL", ""))
    told = dict(f.split("=", 1) for f in args.flag if "=" in f)
    path = args.set_path or os.environ.get(REAL_SET_ENV, "")
    if not urlsplit(args.live).scheme:
        print("NO-GO: --live takes a URL, for example http://127.0.0.1:8080")
        return EXIT_NO_GO
    return live(args.live, args.member, path, told)


if __name__ == "__main__":
    sys.exit(main())
