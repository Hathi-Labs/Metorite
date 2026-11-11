"""The runner of the email-to-Projects eval: ``python -m evals.email_to_projects.run``.

Task 2976. It reuses three harnesses and copies none:

* ``evals.routing.harness.RoutingHarness`` drives the REAL ``orchestrator`` run
  (the chat assistant, "Mira") with its real factory, tools, delegation and cards.
* ``evals.one_assistant_measure.mailbox.Mailbox`` is the fake email gateway
  (``read_email``, ``search_emails``). The run reuses its install pattern.
* ``evals.one_assistant_measure.workgateway.WorkGateway`` is the Projects fake.
  :class:`~evals.email_to_projects.gateway.ChatGateway` extends it with the
  company search and the fixture tree.

Two modes:

* ``--scripted`` (the default): a known-good plan plays through ``ReplyModel``.
  No model call, no network. CI runs this mode.
* ``--live``: the real model answers through the Router. It spends credits, so
  it needs ``--budget-usd`` above zero and an owner OK (spend rule). It is off
  unless you pass the flag. Without ``--live`` the runner never reads a key.

Exit codes: 0 every case passed, 1 a case failed, 2 NO-GO (live mode refused).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acb_skills import tool_packs

from evals.email_to_projects import checkers as ck
from evals.email_to_projects import fixtures as fx
from evals.email_to_projects.cases import PROJECTS_AGENT, PROJECTS_PAGE, Case, select
from evals.email_to_projects.gateway import ChatGateway
from evals.one_assistant_measure import mailbox as mb
from evals.one_assistant_measure import pack, spend
from evals.one_assistant_measure import run as measure
from evals.routing import dataset as ds
from evals.routing.harness import ORCHESTRATOR, ReplyModel, RoutingHarness, RunRecord

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2
HERE = Path(__file__).resolve().parent


def scripted_models(case: Case) -> dict[str, ReplyModel]:
    orch, sub = case.plan()
    models = {ORCHESTRATOR: ReplyModel(orch)}
    if sub:
        models[PROJECTS_AGENT] = ReplyModel(sub)
    return models


def evidence_of(case: Case, record: RunRecord, gateway: ChatGateway) -> ck.Evidence:
    return ck.Evidence(
        case_id=case.id, names=case.names, calls=ck.calls_of(record.events),
        answer=record.answer, searches=list(gateway.searches), found=list(gateway.found),
        writes=[w for w in gateway.writes() if w[1].endswith(("/updates", "/nodes"))],
        cards=list(record.cards), node_names={i: n["name"] for i, n in gateway.nodes.items()},
        error=record.error,
    )


def check(case: Case, ev: ck.Evidence) -> list[ck.Rule]:
    return [*ck.common(ev), *case.extra(ev)]


def run_case(case: Case, *, router: tuple[str, str] | None = None, tier: str | None = None,
             timeout: float = 120.0) -> dict[str, Any]:
    """One case through the real orchestrator run. *router* None is a scripted run."""
    from acb_common import get_settings

    from tests.unit._native_maf_harness import load_agent_module

    live = router is not None
    with RoutingHarness(live=live, real_specialists=live) as harness:
        if live:
            measure._use_router(harness, router)
        harness.mailboxes = mb.BOXES
        measure.no_local_database(harness._mp)
        # The email tools, in the module of the agent and in the copy of the pack.
        module = load_agent_module(harness._registry[pack.EA]["local_path"])
        harness._modules[pack.EA] = module
        mailbox = mb.Mailbox(fx.MAILS)
        mb.install(harness._mp, module, mailbox)
        from orchestrator import _tool_injection as ti

        owner_module = ti._pack_owner_module(pack.EA)
        if owner_module is not None and owner_module is not module:
            mb.install(harness._mp, owner_module, mailbox)
        gateway = ChatGateway(updates_on=case.updates_on)
        gateway.install(harness._mp)
        settings = get_settings()
        for name, value in {**measure.PRODUCTION, **measure.pack_flag(pack.OWNERS),
                            "company_search_enabled": True}.items():
            harness._mp.setattr(settings, name, value, raising=False)
        from tests.unit._egress_mark_store import install_memory_mark_store

        assert install_memory_mark_store(harness._mp) is not None
        if not live:
            harness.models = scripted_models(case)
        member = measure.Member()
        with tool_packs.surface_scope("projects"):
            record = asyncio.run(harness.drive(
                case.prompt, PROJECTS_PAGE, member=ds.MEMBER, org=ds.ORG, tier=tier,
                on_card=member, timeout=timeout, history=[dict(h) for h in case.history] or None,
                typed=case.prompt,
            ))
        rows = [measure.call_row(c) for c in harness.model_calls]
        sent = list(harness.sent)
    ev = evidence_of(case, record, gateway)
    rules = check(case, ev)
    passed = ck.passed(rules) and record.error is None and not sent
    return {
        "case": case.id, "title": case.title, "mode": "live" if live else "scripted",
        "passed": passed, "error": record.error, "unexpected_sends": sent,
        "rules": [r.as_dict() for r in rules], "answer": record.answer,
        "calls": [{"agent": c.agent, "name": c.name, "args": c.args} for c in ev.calls],
        "searches": ev.searches, "writes": [f"{m} {p}" for m, p, _ in ev.writes],
        "cards": len(ev.cards), "model_calls": len(rows),
        "cost_usd": round(measure.turn_totals({"model_calls": rows})["cost_usd"], 4),
        "results": [c.result[:200] for c in ev.calls],
        "unknown_routes": gateway.unknown,
    }


def _line(rec: dict[str, Any]) -> str:
    bad = [r["rule"] + (": " + r["why"] if r["why"] else "") for r in rec["rules"] if not r["pass"]]
    extra = f" error={rec['error']}" if rec["error"] else ""
    return (f"{rec['case']} {'pass' if rec['passed'] else 'FAIL'} ({rec['mode']}) "
            f"{rec['title']}" + (f" | fails: {bad}" if bad else "") + extra)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.email_to_projects.run", description=__doc__)
    p.add_argument("--cases", default="all", help="all, or a comma list such as EP-1,EP-4")
    p.add_argument("--live", action="store_true",
                   help="OFF by default. Ask the real model through the Router. Spends credits")
    p.add_argument("--budget-usd", type=float, default=0.0,
                   help="the spend cap of a live run, in USD. A live run needs a value above zero")
    p.add_argument("--tier", default="tier-balanced", help="the Router tier of a live run")
    p.add_argument("--allow-peak", action="store_true", help="live: run in a DeepSeek peak band")
    p.add_argument("--timeout", type=float, default=180.0, help="seconds for one case")
    p.add_argument("--out", default="", help="the results folder (default: results/<stamp>)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        chosen = select(args.cases)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    router = None
    if args.live:
        if args.budget_usd <= 0:
            print("NO-GO: a live run needs --budget-usd above zero.", file=sys.stderr)
            return EXIT_NO_GO
        ok, why = spend.peak_gate(datetime.now(UTC), args.allow_peak)
        if not ok:
            print(why, file=sys.stderr)
            return EXIT_NO_GO
        router = measure.router_env()
        if router is None:
            print("NO-GO: no Router address and key (LITELLM_BASE_URL, LITELLM_API_KEY).",
                  file=sys.stderr)
            return EXIT_NO_GO
        ok, why = measure._preflight(router, args.tier)
        print(why)
        if not ok:
            return EXIT_NO_GO
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else HERE / "results" / stamp
    out.mkdir(parents=True, exist_ok=True)
    records, spent = [], 0.0
    for case in chosen:
        if router is not None and spent >= args.budget_usd:
            print(f"the budget: USD {spent:.3f} of {args.budget_usd:.2f} spent before {case.id}")
            break
        rec = run_case(case, router=router, tier=args.tier if router else None,
                       timeout=args.timeout)
        spent += rec["cost_usd"]
        records.append(rec)
        (out / f"{case.id}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        print(_line(rec) + (f" | spent USD {spent:.3f} of {args.budget_usd:.2f}" if router else ""),
              flush=True)
    (out / "summary.json").write_text(json.dumps(
        {"mode": "live" if router else "scripted", "spent_usd": round(spent, 4),
         "budget_usd": args.budget_usd, "cases": [r["case"] for r in records],
         "passed": [r["case"] for r in records if r["passed"]]}, indent=2), encoding="utf-8")
    print(f"results: {out}")
    done = len(records) == len(chosen) and all(r["passed"] for r in records)
    return EXIT_PASS if done else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
