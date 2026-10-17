"""The runner of the founder question set: ``python -m evals.crm_founder_questions`` (WS-53 CRM-Q30).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 items 7 to 9.

``--scripted`` needs no model, no network and no database, and CI runs it.
For each question it runs the REAL tool functions of
``acb_skills/crm_tools.py`` with the arguments of ``scripted_answers.json``.
The one GET of the tools (``crm_tools._get``) answers from
``recorded_routes.json``, the answers that the real routes gave on the
seeded database (the R8 test records them). Then the checkers score the
requests, the answer and the agreement of each fact with its route answer.
It checks the checkers, and never the model.

``--live`` asks the real assistant, on a local stack, for each question. The
member talks to ``orchestrator``, as the chat does (D99), and the run is the
real ``run_agent_stream``, with the real specialists. Each CRM tool reads the
real gateway, which reads the seeded org. The runner records each GET of the
tools and the answer, and the checkers score them. It is a MANUAL run, and
CI never runs it. The README says how to prepare the stack.

``--seed`` writes the dataset into one org of a database on this machine,
through ``acb_common.db.tenant_session``. It refuses a remote database and
an org that already holds CRM module records.

Exit codes: 0 pass, 1 a bar failed, 2 the run cannot start (NO-GO).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from acb_common.number_check import MONEY, format_figure

from evals.crm_founder_questions import checkers
from evals.crm_founder_questions.checkers import RouteCall
from evals.crm_founder_questions.dataset import build
from evals.crm_founder_questions.questions import NOTES, Question, facts_of, questions

HERE = Path(__file__).resolve().parent
ANSWERS = HERE / "scripted_answers.json"
ROUTES = HERE / "recorded_routes.json"
EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2


def load_answers(path: Path = ANSWERS) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["answers"]


def load_routes(path: Path = ROUTES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))["routes"]


def route_key(path: str, params: dict[str, Any] | None) -> str:
    """One key for one GET: the path and the sorted parameters."""
    clean = sorted((k, str(v)) for k, v in (params or {}).items() if v is not None)
    return f"{path}?{urlencode(clean)}" if clean else path


# ── The formats of a scripted answer ────────────────────────────────────────


def _round(value: Any, places: str = "1") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


#: The forms of a figure in a scripted answer. Money is whole rupees, the
#: precision that ``crm_aggregate`` prints, so an answer never states more
#: than a tool gave. The facts keep their paise.
FORMATS = {
    "inr": lambda v: format_figure(v, MONEY),
    "lakh": lambda v: f"₹{_round(Decimal(str(v)) / 100_000, '0.01')} lakh",
    "crore": lambda v: f"₹{_round(Decimal(str(v)) / 10_000_000, '0.01')} crore",
    "n": lambda v: format_figure(v),
    "name": str,
}

_PLACEHOLDER = re.compile(r"\{(\w+):([^}]+)\}")


def render(template: str, facts: dict[str, Any]) -> str:
    """The scripted answer: each ``{format:fact}`` takes the fact's value."""
    return _PLACEHOLDER.sub(lambda m: FORMATS[m.group(1)](facts[m.group(2)]), template)


# ── The GET of the tools ────────────────────────────────────────────────────


@contextlib.contextmanager
def tools_get(answer: Any, calls: list[RouteCall]) -> Iterator[None]:
    """Route each GET of ``crm_tools`` to *answer*, and record it in *calls*.

    *answer* is ``async (path, params) -> data``. The tools build the
    request, so the request that the checkers score is the real one."""
    import acb_skills.crm_tools as crm_tools

    real = crm_tools._get

    async def recording(path: str, params: dict[str, Any] | None = None) -> Any:
        clean = {k: str(v) for k, v in (params or {}).items() if v is not None}
        data = await answer(path, clean)
        calls.append(RouteCall(path, clean, data))
        refusal = checkers.refused(data)
        if refusal is not None:
            # The error that crm_tools._get raises for this answer.
            raise crm_tools._GatewayError(
                f"CRM GET {path} failed ({refusal.get('status')})", refusal.get("code"))
        return data

    crm_tools._get = recording
    try:
        yield
    finally:
        crm_tools._get = real


def synthetic_note_search(params: dict[str, str]) -> dict[str, Any]:
    """A note search answer from the dataset, for ``--scripted`` only.

    The real search (CRM-Q27) needs the embed door, so no route of this
    machine records it. The hits are the notes that hold each word of the
    query, newest first, in the shape of ``GET /crm/notes/search``."""
    ds = build()
    words = [w for w in params.get("q", "").lower().split() if len(w) > 3]
    hits = [n for n in ds.notes if words and all(w in f"{n.subject} {n.body}".lower()
                                                 for w in words)]
    hits.sort(key=lambda n: n.occurred, reverse=True)
    return {"degraded": False, "hits": [
        {"activity_id": n.ext_id, "at": n.occurred.isoformat(),
         "parent": {"module": n.module, "module_record_id": n.record_ext_id},
         "snippet": f"{n.subject}\n{n.body}"[:200]}
        for n in hits[: int(params.get("limit") or 10)]
    ]}


def recorded(routes: dict[str, Any]) -> Any:
    async def answer(path: str, params: dict[str, str]) -> Any:
        if path == NOTES:
            return synthetic_note_search(params)
        key = route_key(path, params)
        if key not in routes:
            raise LookupError(f"no recorded answer for {key}. Record the routes again (README).")
        return routes[key]
    return answer


async def run_tools(entry: dict[str, Any], answer: Any) -> tuple[list[RouteCall], list[str]]:
    """Each scripted tool call through the real tool. Gives the requests and
    the text of each tool answer."""
    import acb_skills.crm_tools as crm_tools

    calls: list[RouteCall] = []
    texts: list[str] = []
    with tools_get(answer, calls):
        for call in entry["calls"]:
            tool = getattr(crm_tools, call["tool"])
            texts.append(await tool(**call["args"]))
    return calls, texts


# ── --scripted ──────────────────────────────────────────────────────────────


async def run_scripted(answers: dict[str, dict[str, Any]] | None = None,
                       routes: dict[str, Any] | None = None) -> checkers.Score:
    """Each question, replayed. *answers* and *routes* are parameters so a
    test can pass a changed set and see a bar fail (R7)."""
    answers = answers if answers is not None else load_answers()
    routes = routes if routes is not None else load_routes()
    score = checkers.Score()
    for q in questions():
        entry = answers.get(q.id)
        if entry is None:
            qs = checkers.QuestionScore(q.id, q.category)
            qs.problems["tools"].append("no scripted entry")
            score.questions.append(qs)
            continue
        try:
            calls, texts = await run_tools(entry, recorded(routes))
        except LookupError as exc:
            qs = checkers.QuestionScore(q.id, q.category)
            qs.problems["agree"].append(str(exc))
            score.questions.append(qs)
            continue
        text = render(entry["answer"], facts_of(q))
        score.questions.append(checkers.score_question(q, calls, text, agree=True,
                                                       texts=texts))
    return score


# ── --live ──────────────────────────────────────────────────────────────────


def _live_gate(org: str, member: str, tier: str) -> tuple[bool, str]:
    from acb_common import get_settings

    from evals.coding_engine import preflight

    if not org or not member:
        return False, "NO-GO: give --org and --member, a member of the seeded org."
    settings = get_settings()
    gateway = os.environ.get("GATEWAY_URL", "http://localhost:8080")
    if not preflight.is_local(gateway):
        return False, f"NO-GO: the gateway {gateway} is not on this machine."
    check = preflight.router(settings.litellm_base_url, settings.llm_api_key, tier, member)
    return check.ok, check.reason


async def _live(qs: list[Question], org: str, member: str, tier: str) -> list[dict[str, Any]]:
    from acb_skills import crm_tools
    from orchestrator import executor

    from evals.routing import shell
    from evals.routing.harness import RoutingHarness

    rows: list[dict[str, Any]] = []
    # Taken before the harness patches them. The routing harness stands in
    # for each specialist and calls the CRM Native. This run needs the real
    # specialist and the real mode of the org.
    real_get, real_mode = crm_tools._get, crm_tools.crm_mode
    real_sub = executor._run_sub_agent_streaming
    with RoutingHarness(live=True) as harness:
        harness._mp.setattr(executor, "_run_sub_agent_streaming", real_sub)
        harness._mp.setattr(crm_tools, "crm_mode", real_mode)
        for q in qs:
            calls: list[RouteCall] = []

            async def answer(path: str, params: dict[str, str]) -> Any:
                return await real_get(path, params)

            with tools_get(answer, calls):
                record = await harness.drive(q.words, shell.system_context(None), member=member,
                                             org=org, tier=tier, timeout=300.0)
            texts = [str(ev.get("content") or "") for ev in record.events
                     if ev.get("type") in ("TOOL_CALL_RESULT", "SUB_AGENT_TOOL_CALL_RESULT")]
            s = checkers.score_question(q, calls, record.answer, agree=False, texts=texts)
            rows.append({**s.summary(), "words": q.words, "answer": record.answer[:3000],
                         "requests": [route_key(c.path, c.params) for c in calls],
                         "tool_calls": [n for n, _ in record.tool_calls],
                         "delegated_to": record.routed, "error": record.error})
    return rows


# ── --seed ──────────────────────────────────────────────────────────────────


async def seed_org(org: str) -> str:
    """Write the dataset into *org*, through the one seam, on this machine only."""
    from acb_common import get_settings
    from acb_common.db import tenant_session
    from sqlalchemy import text

    from evals.coding_engine import preflight
    from evals.crm_founder_questions.seed import statements

    url = str(get_settings().database_url)
    if not preflight.is_local(url.replace("+asyncpg", "").replace("+psycopg", "")):
        return "NO-GO: the database is not on this machine. The seed runs on a local stack only."
    async with tenant_session(org) as db:
        held = (await db.execute(text(
            "SELECT count(*) FROM crm_module_records WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org})).scalar()
        if held:
            return f"NO-GO: the org holds {held} CRM module records. Seed an empty org."
        for sql, rows in statements(org):
            await db.execute(text(sql), rows)
    return f"seeded {build().size()} rows into {org}"


# ── the command line ────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.crm_founder_questions",
                                description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true", help="no model, no network (CI)")
    mode.add_argument("--live", action="store_true", help="the real assistant, on a local stack")
    mode.add_argument("--seed", action="store_true", help="write the dataset into --org")
    p.add_argument("--org", default="", help="live and seed: the org of the dataset")
    p.add_argument("--member", default="", help="live: a member of that org")
    p.add_argument("--tier", default="tier-balanced", help="live: the Router tier")
    p.add_argument("--questions", default="", help="a comma list of question ids")
    p.add_argument("--out", default=str(HERE / "live_results.json"),
                   help="live: the JSON file of the results")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.seed:
        if not args.org:
            print("NO-GO: give --org.", file=sys.stderr)
            return EXIT_NO_GO
        note = asyncio.run(seed_org(args.org))
        print(note)
        return EXIT_NO_GO if note.startswith("NO-GO") else EXIT_PASS
    if args.scripted:
        score = asyncio.run(run_scripted())
        report = {"mode": "scripted", **score.summary()}
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        return EXIT_PASS if score.passed() else EXIT_FAIL
    ok, reason = _live_gate(args.org, args.member, args.tier)
    print(reason)
    if not ok:
        return EXIT_NO_GO
    wanted = {w.strip() for w in args.questions.split(",") if w.strip()}
    qs = [q for q in questions() if not wanted or q.id in wanted]
    rows = asyncio.run(_live(qs, args.org, args.member, args.tier))
    passed = sum(r["passed"] for r in rows)
    summary = {"date": datetime.now(UTC).date().isoformat(), "tier": args.tier,
               "passed": passed, "questions": len(rows),
               "rate": round(passed / len(rows), 3) if rows else None,
               "bar": checkers.LIVE_BAR, "rows": rows}
    Path(args.out).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    for r in rows:
        print(f"{r['id']:<5}{r['category']:<14}{'pass' if r['passed'] else 'FAIL'}  "
              + "; ".join(f"{b}: {p[0]}" for b, p in r["problems"].items()))
    print(f"{passed}/{len(rows)} passed on {args.tier}. Results: {args.out}")
    return EXIT_PASS if rows and passed / len(rows) >= checkers.LIVE_BAR else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
