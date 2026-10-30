"""Measure the email tool pack against delegation: ``python -m evals.one_assistant_measure.run``.

WS-67 OA-5 (``project-docs/specs/one_assistant_context.md`` §4 and §5, D116).
Two arms run the same email chats through the REAL orchestrator run of
``evals.routing.harness`` (H-412: one harness):

* ``delegation``: today's code. The orchestrator hands each email task to
  the real email assistant, which runs as a sub-agent.
* ``pack``: the eval fixture of :mod:`.pack`. The orchestrator holds the
  email assistant's own tools and a short rules block, and no delegation.

The email gateway is the one fake (:mod:`.mailbox`). Three modes:

``--scripted`` calls no model and no network, and CI can run it. Each
scenario plays its known-good plan on both arms, and every check must pass.
A scripted run proves the path and the checks, never the model's choice.

``--live`` is a MANUAL run through the Router. It reads ``LITELLM_BASE_URL``
and ``LITELLM_API_KEY`` from the environment, else from
``~/.metorite/secrets/eval.env``, and it never prints the key. Each run
spends credits, so ``--budget-usd`` stops it, and each turn prints the
spend so far. It writes ``--out`` after each chat.

``--report`` prints the tables of ``--out``.

Exit codes: 0 pass (or a live run that finished), 1 a scripted check
failed, 2 the live run cannot start.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.one_assistant_measure import checks as ck
from evals.one_assistant_measure import fanout, pack, spend
from evals.one_assistant_measure import mailbox as mb
from evals.one_assistant_measure import workgateway as wg
from evals.one_assistant_measure.scenarios import SCENARIOS, Scenario, all_scenarios
from evals.routing import dataset as ds
from evals.routing.harness import (
    ORCHESTRATOR,
    ModelCall,
    ReplyModel,
    RoutingHarness,
    RunRecord,
    text_turn,
    tool_turn,
)
from tests.unit._egress_mark_store import install_memory_mark_store

HERE = Path(__file__).resolve().parent
EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2
ARMS = ("delegation", "pack")
#: The third arm of the spec (OA-8e): the packs with ``spawn_subagents`` on
#: (OA-9b). It sets ``SPAWN_SUBAGENTS_ORGS`` for the eval org, in the eval
#: process only. The fan-out chats run on it and on ``pack``.
SPAWN_ARM = ck.SPAWN_ARM
ALL_ARMS = (*ARMS, SPAWN_ARM)
PACK_ARMS = ck.PACK_ARMS


def arms_for(sc: Scenario, asked: tuple[str, ...]) -> tuple[str, ...]:
    """The arms that run *sc*: a fan-out chat runs on ``pack`` and ``packs+spawn``
    (delegation holds no spawn tool), any other chat on ``delegation`` and ``pack``."""
    want = (ARMS[1], SPAWN_ARM) if sc.set == "fanout" else ARMS
    return tuple(a for a in want if a in asked)
EA = pack.EA
CRM = pack.CRM
PROJECTS = pack.PROJECTS
#: The settings of production that the measure sets in its own process (OA-7h).
#: They change how the run behaves, so both arms get them. The measure never
#: writes them on a box.
PRODUCTION: dict[str, Any] = {
    "email_held_card_send": True,
    "ai_tier_routing": "orchestrator",
    "ai_reasoning_off_agents": "orchestrator",
    "maf_native_sessions": True,
    "narrowing_agents": "email-assistant",
    # Run 2 of OA-8e (OA-8f): the base trim we would ship. BOTH arms hold it,
    # so the comparison is fair.
    "orchestrator_floor_trim": True,
}


def pack_flag(owners: tuple[str, ...] = (pack.EA,)) -> dict[str, Any]:
    """The real flag of the ``pack`` arm: ``TOOL_PACKS`` and ``TOOL_PACKS_ORGS``.

    EVERY pack of each owner in *owners*, for the eval org only. OA-7i removes
    the specialist tool only when the loader can reach every pack of the owner
    (``tool_packs.plan_loader``). With ``email`` alone, run 2 of 2026-10-28
    kept ``email_assistant`` in the run and handed off mail work in 31 turns.
    The email chats turn on the email owner alone, as before. The CRM,
    Projects and mixed chats turn on all three owners (:func:`owners_of`):
    ``crm-records`` for the CRM assistant, and the four packs of the Projects
    assistant. A pack left out keeps its specialist in the run (OA-8e)."""
    from acb_skills import tool_packs

    slugs = [slug for owner in owners for slug in tool_packs.owner_packs(owner)]
    return {"tool_packs": ",".join(slugs), "tool_packs_orgs": ds.ORG}


def owners_of(sc: Scenario) -> tuple[str, ...]:
    """The owners whose packs the ``pack`` arm turns on for *sc*."""
    return (EA,) if sc.set == "email" else pack.OWNERS


EVAL_ENV = Path.home() / ".metorite" / "secrets" / "eval.env"
RATE_CARD = json.loads((HERE / "rate_card.json").read_text(encoding="utf-8"))


# ── cost ─────────────────────────────────────────────────────────────────────


def cached_of(usage: dict[str, Any]) -> int:
    details = usage.get("prompt_tokens_details") or {}
    return int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)


def cost_of(usage: dict[str, Any], card: dict[str, Any] = RATE_CARD) -> tuple[float, str]:
    """The USD cost of one call, and its source: ``vendor`` or ``card``."""
    if isinstance(usage.get("cost"), (int, float)):
        return float(usage["cost"]), "vendor"
    rate = card["usd_per_1m"]
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = min(cached_of(usage), prompt)
    out = int(usage.get("completion_tokens") or 0)
    usd = ((prompt - hit) * rate["input_miss"] + hit * rate["input_hit"]
           + out * rate["output"]) / 1_000_000
    return usd, "card"


def call_row(c: ModelCall) -> dict[str, Any]:
    """One row per model call, with its counted USD.

    Three corrections to the vendor figure (2026-10-30 drain). A call with no
    usage frame that was not refused is priced from its request size, as the
    worst case (``cost_source`` ``estimate``). A call that starts in a DeepSeek
    peak band counts double (``peak``), because ``usage.cost`` does not know
    the band. A refused call (status 4xx or 5xx) costs nothing."""
    usd, source = cost_of(c.usage)
    if not c.usage and (c.status is None or c.status < 400):
        usd, source = spend.unseen_usage_usd(c.request_chars, RATE_CARD), "estimate"
    peak = bool(c.wall) and spend.is_peak(datetime.fromtimestamp(c.wall, UTC))
    if peak:
        usd *= spend.PEAK_FACTOR

    def since(t: float | None) -> float | None:
        return round(t - c.started, 3) if t is not None else None

    return {
        "agent": c.agent, "model": c.model, "served": c.served, "stream": c.stream,
        "messages": c.messages, "tools": c.tools, "request_chars": c.request_chars,
        "status": c.status, "ttfb_s": since(c.first_byte), "ttft_s": since(c.first_token),
        "dur_s": since(c.ended), "prompt": int(c.usage.get("prompt_tokens") or 0),
        "cached": cached_of(c.usage), "completion": int(c.usage.get("completion_tokens") or 0),
        "reasoning": int((c.usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                         or 0),
        "cost_usd": round(usd, 6), "cost_source": source, "peak": peak,
    }


# ── the scripted models ──────────────────────────────────────────────────────


#: Tools of the orchestrator's base that no pack owner holds.
BASE_DIRECT = frozenset({"web_search", "fetch_page", "spawn_subagents"})


def pack_tool_names() -> frozenset[str]:
    """The tools of the real ``email`` pack, from its skill family."""
    from acb_skills.skill_families import SKILL_FAMILIES

    return frozenset(SKILL_FAMILIES["email"]["tools"])


def owner_of_tool(name: str) -> str:
    """The owner of tool *name*: the CRM or the Projects assistant, else email."""
    if name in pack.owner_tool_names(CRM):
        return CRM
    if name in pack.owner_tool_names(PROJECTS):
        return PROJECTS
    return EA


def pack_of_tool(name: str) -> str:
    """The slug of the pack of a CRM or Projects tool."""
    from acb_skills import tool_packs

    owner = owner_of_tool(name)
    for slug in tool_packs.owner_packs(owner):
        if name in tool_packs.PACKS[slug].tools:
            return slug
    raise KeyError(name)


def scripted_models(sc: Scenario, arm: str) -> dict[str, ReplyModel]:  # noqa: C901 - one plan per arm
    """The known-good models of *sc* on *arm*, from the plan of each turn.

    On the ``pack`` arm a call of the ``email`` pack runs in the orchestrator's
    own run. A call that the pack does not hold (a rule, a setting) goes to the
    email assistant by ``call_agent``, as the rules of the pack say (H-417).
    The model loads the pack first when the member's words do not (layer (b)).
    A CRM or Projects call runs in the orchestrator's own run too: its pack
    loads by the surface (layer a), by a word of the pack (layer a), because it
    is sticky from an earlier turn, or by the model's own load (layer b).
    On the ``delegation`` arm each owner gets one hand-off for its calls.
    """
    from acb_skills import tool_packs

    in_pack = pack_tool_names()
    orch: list[Any] = []
    subs: dict[str, list[Any]] = {}
    n = 0
    used = False
    held: set[str] = set()
    for turn in sc.turns:
        plan = turn.plan
        calls_of = plan.calls if (arm == SPAWN_ARM or not plan.solo) else plan.solo
        # The web tools and the spawn tool belong to no owner: the orchestrator
        # calls them itself, in every arm that holds them.
        direct = [c for c in calls_of if c[0] in BASE_DIRECT]
        calls_of = tuple(c for c in calls_of if c[0] not in BASE_DIRECT)
        if arm == "delegation":
            by_owner: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for call in calls_of:
                by_owner.setdefault(owner_of_tool(call[0]), []).append(call)
            for owner, calls in by_owner.items():
                n += 1
                orch.append(tool_turn(pack.DELEGATE_TOOLS[owner], {"task": turn.words},
                                      call_id=f"call_{n}"))
                for name, args in calls:
                    n += 1
                    subs.setdefault(owner, []).append(tool_turn(name, args, call_id=f"call_{n}"))
                subs[owner].append(text_turn(plan.answer))
        elif arm in PACK_ARMS:
            for name, args in direct:
                n += 1
                orch.append(tool_turn(name, args, call_id=f"call_{n}"))
            mine = [c for c in calls_of if c[0] in in_pack]
            setup = [c for c in calls_of
                     if c[0] not in in_pack and owner_of_tool(c[0]) == EA]
            work = [c for c in calls_of if owner_of_tool(c[0]) != EA]
            if mine and not (used or tool_packs.matched_layer("email", turn.words)):
                n += 1
                orch.append(tool_turn(tool_packs.LOAD_TOOL, {"pack": "email"}, call_id=f"call_{n}"))
            for name, args in mine:
                n += 1
                orch.append(tool_turn(name, args, call_id=f"call_{n}"))
            if work and plan.handoff:
                owner = owner_of_tool(work[0][0])
                n += 1
                orch.append(tool_turn("call_agent", {"agent_name": owner, "message": turn.words},
                                      call_id=f"call_{n}"))
                for name, args in work:
                    n += 1
                    subs.setdefault(owner, []).append(tool_turn(name, args, call_id=f"call_{n}"))
                subs[owner].append(text_turn(plan.answer))
                work = []
            for name, args in work:
                slug = pack_of_tool(name)
                if slug not in held and not (
                        tool_packs.matched_layer(slug, turn.words)
                        or sc.surface in tool_packs.PACKS[slug].surface_slugs):
                    n += 1
                    orch.append(tool_turn(tool_packs.LOAD_TOOL, {"pack": slug},
                                          call_id=f"call_{n}"))
                held.add(slug)
                n += 1
                orch.append(tool_turn(name, args, call_id=f"call_{n}"))
            if setup:
                # With the base trim on (run 2 onward) the ``email-setup`` pack
                # fits beside ``email``, so the loader reaches the email owner and
                # a hand-off to it is refused. The model loads the setup pack and
                # calls the tools itself.
                if "email-setup" not in held and not tool_packs.matched_layer(
                        "email-setup", turn.words):
                    n += 1
                    orch.append(tool_turn(tool_packs.LOAD_TOOL, {"pack": "email-setup"},
                                          call_id=f"call_{n}"))
                held.add("email-setup")
                for name, args in setup:
                    n += 1
                    orch.append(tool_turn(name, args, call_id=f"call_{n}"))
            used = used or bool(mine)
        orch.append(text_turn(plan.answer))
    models = {ORCHESTRATOR: ReplyModel(orch)}
    models.update({owner: ReplyModel(turns) for owner, turns in subs.items()})
    if arm == SPAWN_ARM:
        models["worker"] = fanout.WorkerModel()
    return models


# ── the log tap ──────────────────────────────────────────────────────────────

#: The events of the pack code that the report reads (OA-7h duties 4 and 5):
#: the turn-kind request with its time, and each load of a pack, with the
#: number of model calls that came before it in the turn.
WATCHED = ("ai_route.turn_kind", "tool_packs.", "executor.pack")
#: The event that the measure itself adds: the time of layer (a) in ms.
LAYER_A_EVENT = "measure.layer_a"
_DROPPED_FIELDS = frozenset({"run_id", "thread_id", "user", "member_verified", "instance", "app",
                             "agent", "source_id"})


class _TappedLog:
    """A logger that passes each call on, and keeps the watched events."""

    def __init__(self, log: Any, tap: LogTap) -> None:
        self._log = log
        self._tap = tap

    def __getattr__(self, level: str) -> Any:
        method = getattr(self._log, level)
        if level not in ("debug", "info", "warning", "error", "exception"):
            return method

        def call(event: Any = None, *args: Any, **fields: Any) -> Any:
            self._tap.note(level, event, fields)
            return method(event, *args, **fields)

        return call


class LogTap:
    """Keeps the watched log events of the pack code, in order (OA-7h).

    The code under test logs them already. The tap reads, and changes nothing:
    the log call goes on to the real logger."""

    def __init__(self, calls: list[ModelCall]) -> None:
        self.calls = calls
        self.events: list[dict[str, Any]] = []

    def note(self, level: str, event: Any, fields: dict[str, Any]) -> None:
        name = str(event or "")
        if not name.startswith(WATCHED):
            return
        keep = {k: (v if isinstance(v, (int, float, bool, str, type(None))) else str(v))
                for k, v in fields.items() if k not in _DROPPED_FIELDS}
        self.events.append({"event": name, "level": level, "calls_before": len(self.calls), **keep})

    def install(self, mp: Any) -> None:
        """Wrap the logger of each module that logs a watched event."""
        from acb_skills import tool_packs
        from orchestrator import _tool_injection as ti
        from orchestrator import executor

        for module in (executor, ti, tool_packs):
            mp.setattr(module, "_log", _TappedLog(module._log, self))
        real = ti._apply_tool_packs
        tap = self

        async def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return await real(*args, **kwargs)
            finally:
                tap.events.append({"event": LAYER_A_EVENT, "level": "info",
                                   "calls_before": len(tap.calls),
                                   "ms": round((time.monotonic() - started) * 1000, 2)})

        mp.setattr(ti, "_apply_tool_packs", timed)


# ── the cards ────────────────────────────────────────────────────────────────


class Member:
    """Answers each card as the member does, and keeps each card's value."""

    def __init__(self) -> None:
        self.cards: list[dict[str, Any]] = []

    def __call__(self, _record: RunRecord, value: dict[str, Any]) -> str:
        self.cards.append(dict(value))
        form = value.get("reply")
        if isinstance(form, dict):
            mode = "reply_all" if form.get("reply_all") else "reply"
            ticked = (form.get("ticked") or {}).get(mode) or {}
            return "APPROVE " + json.dumps({"reply": {
                "action": "save", "reply_all": bool(form.get("reply_all")),
                "body": form.get("body") or "Thanks", "to": list(ticked.get("to") or []),
                "cc": list(ticked.get("cc") or []), "message_id": form.get("message_id") or "",
            }})
        rows = value.get("rows")
        if rows:
            return "APPROVE " + json.dumps({"rows": [r["id"] for r in rows]})
        return "APPROVE"


# ── one chat ─────────────────────────────────────────────────────────────────


def router_env() -> tuple[str, str] | None:
    """``(base, key)`` of the Router: the environment, else ``eval.env``."""
    base = os.environ.get("LITELLM_BASE_URL", "")
    key = os.environ.get("LITELLM_API_KEY", "")
    if not (base and key) and EVAL_ENV.is_file():
        for line in EVAL_ENV.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if not sep or name.strip().startswith("#"):
                continue
            value = value.strip().strip('"').strip("'")
            if name.strip() == "LITELLM_BASE_URL":
                base = base or value
            elif name.strip() == "LITELLM_API_KEY":
                key = key or value
    return (base.rstrip("/"), key) if base and key else None


def _use_router(harness: RoutingHarness, router: tuple[str, str]) -> None:
    from acb_common import get_settings

    base, key = router
    settings = get_settings()
    mp = harness._mp
    mp.setattr(settings, "litellm_base_url", base, raising=False)
    # ``llm_api_key`` is a property that reads this field.
    mp.setattr(settings, "litellm_master_key", key, raising=False)
    mp.setenv("LITELLM_BASE_URL", base)
    mp.setenv("LITELLM_MASTER_KEY", key)
    # Fix round 1: Mem0 and Graphiti call the Router through their own
    # clients, which the tap does not see and the card does not price. They
    # are off by default, and the live run pins them off.
    mp.setattr(settings, "mem0_enabled", False, raising=False)
    mp.setattr(settings, "graphiti_enabled", False, raising=False)
    harness.allow = (base + "/",)
    _system_one_seam(mp)
    tap_system_one(mp, harness.model_calls)


def tap_system_one(mp: Any, sink: list[ModelCall]) -> None:
    """Put the wire tap on the System-1 client, so its turn-kind call counts.

    ``system_one._build_agent`` builds its own client for each call. The tap
    wrapped the chat agents only, so this call reached the Router unseen: the
    rows of run 5 (2026-10-30) show one send more than model calls in each turn
    that asked the turn kind. The call is a buffered ``tier-fast`` request, so
    the card prices it."""
    from acb_skills import system_one

    from evals.routing.harness import _tap

    real = system_one._build_agent

    def build(*args: Any, **kwargs: Any) -> Any:
        agent, async_client = real(*args, **kwargs)
        _tap([agent], "system-one", sink)
        return agent, async_client

    mp.setattr(system_one, "_build_agent", build)


#: The tools of the orchestrator that read the LOCAL database of the machine
#: that runs the eval, in process. The first live run of OA-8e let them answer,
#: and records of the local dev database reached the Router as tool results.
#: ``retrieve_entity_context`` and ``retrieve_sales_context`` read the entity
#: graph through ``executor._graph_session_opener_current``. The others reach a
#: store that the live runner pins off (Mem0, Graphiti) or send an httpx request
#: that the guard of the harness refuses (``search_company``, ``org_facts``).
LOCAL_DB_TOOLS = frozenset({"retrieve_entity_context", "retrieve_sales_context"})


def no_local_database(mp: Any) -> None:
    """Close every in-process read of the local database (fix round 2, item 5).

    The graph opener answers None, so each retrieval tool answers "no match"
    and opens no session. It applies to every run, scripted or live."""
    from orchestrator import executor

    mp.setattr(executor, "_graph_session_opener_current", lambda: None)


def _system_one_seam(mp: Any) -> None:
    """The EVAL-ONLY System-1 seam: the turn-kind request goes to the Router.

    Production runs the turn-kind request, because ``routing_is_on()`` is true
    there (the owner's serving flag and a wired Console). The eval has neither,
    so ``system_one._ask`` answered ``router_off`` and the request never went
    out (the run of 2026-10-27). This seam makes ``system_one._ask`` read True
    and nothing else: any other caller of ``routing_is_on`` reads the real
    answer. The client already reads the same Router base and key that
    :func:`_use_router` set (``system_one._gateway``), on ``tier-fast``. It
    sets no ``ROUTER_SERVING_ENABLED`` and changes no product code. It
    BYPASSES Console billing for that one call, as the rest of the eval does.
    """
    import sys

    from acb_llm import routed

    real = routed.routing_is_on

    def routing_is_on() -> bool:
        caller = sys._getframe(1)
        if caller.f_code.co_name == "_ask" and caller.f_code.co_filename.endswith("system_one.py"):
            return True
        return real()

    mp.setattr(routed, "routing_is_on", routing_is_on)


def run_chat(sc: Scenario, arm: str, *, router: tuple[str, str] | None = None,
             tier: str | None = None, timeout: float = 60.0, repeat: int = 1,
             models: dict[str, ReplyModel] | None = None,
             on_turn: Any = None, should_stop: Any = None) -> list[dict[str, Any]]:
    """One chat of *sc* on *arm*: each turn through the real orchestrator run.

    *router* None is a scripted run: the plans of *sc* play, or *models*.
    *should_stop* is called before each turn after the first: when it answers
    True the chat ends there, so a budget stops the run inside a long chat.
    Returns one row for each turn.
    """
    from acb_common import get_settings

    from tests.unit._native_maf_harness import load_agent_module

    live = router is not None
    rows: list[dict[str, Any]] = []
    with RoutingHarness(live=live, real_specialists=live) as harness:
        if live:
            _use_router(harness, router)
        harness.mailboxes = mb.BOXES
        no_local_database(harness._mp)
        # The worker of ``spawn_subagents`` is an internal agent: the harness
        # registry lists the public agents only, so the stand-in loader gets it here.
        from gateway.routes.agent import _INTERNAL_AGENTS

        for entry in _INTERNAL_AGENTS:
            harness._registry.setdefault(str(entry["name"]), dict(entry))
        module = load_agent_module(harness._registry[EA]["local_path"])
        harness._modules[EA] = module
        gateway = mb.Mailbox(sc.mails)
        mb.install(harness._mp, module, gateway)
        # The real pack builds its tools from its own copy of the email module
        # (``_pack_owner_module``), so that copy gets the fake gateway too.
        from orchestrator import _tool_injection as ti

        owner_module = ti._pack_owner_module(EA)
        if owner_module is not None and owner_module is not module:
            mb.install(harness._mp, owner_module, gateway)
        # OA-8e: the CRM and Projects tools reach the gateway through httpx,
        # so a fake answers at the transport. CRM Mirror mode is read from
        # ``crm_mode``, which the harness pins to ``native``.
        work = wg.WorkGateway(mode=sc.mode)
        work.install(harness._mp)
        from acb_skills import crm_tools, tool_packs

        async def crm_mode() -> str:
            return sc.mode

        harness._mp.setattr(crm_tools, "crm_mode", crm_mode)
        settings = get_settings()
        flag = pack_flag(owners_of(sc)) if arm in PACK_ARMS else {}
        if arm == SPAWN_ARM:
            # In the eval process only. The tool leaves the request of any
            # other org (``test_spawn_subagents.py``).
            flag["spawn_subagents_orgs"] = ds.ORG
        web = fanout.WebFixtures()
        web.install(harness._mp)
        for name, value in {**PRODUCTION, **flag}.items():
            harness._mp.setattr(settings, name, value, raising=False)
        # OA-7h duty 1: the mark of a chat that read mail lives in Redis. The
        # harness installs the in-memory store, and the measure installs it
        # again to keep the handle. Without a store the mark read fails
        # closed, and every turn reads as held.
        store = install_memory_mark_store(harness._mp)
        assert store is not None, "the in-memory mark store did not install"
        tap = LogTap(harness.model_calls)
        tap.install(harness._mp)
        fanout.install_timer(harness._mp, tap.events, harness.model_calls)
        if not live:
            harness.models = models if models is not None else scripted_models(sc, arm)
        member = Member()
        history = [dict(h) for h in sc.history]
        thread: str | None = None
        for index, turn in enumerate(sc.turns):
            if index and should_stop is not None and should_stop():
                break
            marks = (len(gateway.calls), len(member.cards), len(harness.model_calls),
                     len(tap.events), len(harness.sent), len(harness.passed),
                     len(store.calls), len(work.calls))
            # The gateway binds the app that the member is in, for this turn.
            with tool_packs.surface_scope(sc.surface):
                record = asyncio.run(harness.drive(
                    turn.words, sc.page, member=ds.MEMBER, org=ds.ORG, tier=tier,
                    on_card=member, timeout=timeout, thread_id=thread,
                    history=history or None, typed=turn.words,
                ))
            thread = record.thread_id
            main_calls, sub_calls = ck.work_calls_of(record.events, ck.work_tool_names())
            # OA-8i: a hand-off that the product refused ran no work. It is
            # counted on its own, and only the hand-offs that ran are checked.
            refused_handoffs = ck.refused_handoffs_of(record.events)
            used_calls = harness.model_calls[marks[2]:]
            obs = ck.Observation(
                arm=arm, answer=record.answer, email_calls=ck.email_calls_of(record.events),
                tasks=ck.tasks_of(record.tool_calls),
                refused=[],
                cards=member.cards[marks[1]:], gateway=gateway.calls[marks[0]:],
                error=record.error, turn=index + 1,
                work_calls=main_calls, sub_work_calls=sub_calls,
                work_writes=work.writes(marks[7]),
                handoffs=ck.without(ck.handoffs_of(record.tool_calls), refused_handoffs),
                handoffs_refused=refused_handoffs,
                loaded=ck.loads_of(record.tool_calls),
                owners=owners_of(sc) if arm in PACK_ARMS and sc.set != "email" else (),
                mirror=sc.mode == "mirror",
                max_tools=max((c.tools for c in used_calls if c.agent == ORCHESTRATOR),
                              default=0) if arm in PACK_ARMS else 0,
                spawned=sum(1 for name, _ in record.tool_calls if name == "spawn_subagents"),
                spawn_results=ck.spawn_results_of(record.events),
                needs=next((str(c.arg) for c in turn.checks if c.kind == "right_pack"), ""),
                reachable=reachable_of(tap.events[marks[3]:]),
                write_cards=ck.write_cards_of(record.events),
            )
            result = ck.check_turn(turn, obs)
            calls = [call_row(c) for c in harness.model_calls[marks[2]:]]
            # A send that reached the Router and that no tap saw is counted at
            # the worst case, never at zero (2026-10-30 drain).
            unseen = max(0, len(harness.passed) - marks[5] - len(calls))
            events = [{**e, "calls_before": e["calls_before"] - marks[2]}
                      for e in tap.events[marks[3]:]]
            row = {
                "scenario": sc.id, "turn": index + 1, "arm": arm, "repeat": repeat,
                "words": turn.words, "checks": result, "passed": ck.passed(result),
                "answer": record.answer, "error": record.error,
                "email_tools": [name for name, _, _ in obs.email_calls],
                "tasks": obs.tasks, "refused": len(obs.refused), "cards": len(obs.cards),
                "first_text_s": _r(record.first_text_s), "total_s": _r(record.total_s),
                "model_calls": calls,
                "sent": harness.sent[marks[4]:],
                "router_sends": len(harness.passed) - marks[5],
                "unseen_sends": unseen,
                "unseen_usd": round(spend.unseen_sends_usd(unseen, RATE_CARD), 6),
                "handoffs": [ck.handoff_kind(t) for t in obs.tasks],
                "set": sc.set, "surface": sc.surface,
                "work_tools": [n for n, _, _ in [*obs.work_calls, *obs.sub_work_calls]],
                "specialist_handoffs": {k: len(v) for k, v in obs.handoffs.items()},
                "handoffs_refused": {k: len(v) for k, v in obs.handoffs_refused.items()},
                "loaded_by_model": obs.loaded,
                "handoff_reasons": {
                    owner: ck.handoff_reason(owner, obs.needs, obs.reachable)
                    for owner in obs.handoffs} if arm in PACK_ARMS and obs.owners else {},
                "spawned": obs.spawned,
                "research": any(c.kind == "spawned" for c in turn.checks),
                "items_problems": ck.items_contract_problems(obs.spawn_results),
                "web_fetches": len(web.fetches), "web_searches": len(web.searches),
                "web_unknown": web.unknown[:],
                "work_writes": [f"{m} {p}" for m, p, _ in obs.work_writes],
                "unknown_routes": work.unknown[:],
                "max_tools": max((c["tools"] for c in calls if c["agent"] == ORCHESTRATOR),
                                 default=0),
                "pack_events": events,
                "mark_writes": sum(1 for kind, _ in store.calls[marks[6]:] if kind == "setex"),
            }
            rows.append(row)
            if on_turn is not None:
                on_turn(row)
            history += [{"role": "user", "content": turn.words},
                        {"role": "assistant", "content": record.answer}]
    return rows


def reachable_of(events: list[dict[str, Any]]) -> frozenset[str] | None:
    """The packs that the run held or could load in a turn, from the log of the
    pack code: the packs it loaded (layer a), the packs it offered to the loader,
    and the packs that the model loaded (layer b).

    None when the log has no ``executor.pack_plan`` line. The code logs it only
    when it builds a pack or offers the loader, so a run where the flag did
    nothing logs none. The checks read None as "unknown" and fail."""
    if not any(e["event"] == "executor.pack_plan" for e in events):
        return None
    from acb_skills import tool_packs

    known = {slug for slug in tool_packs.PACKS}
    out: set[str] = set()
    for e in events:
        if e["event"] == "executor.pack_plan":
            for key in ("offered", "packs"):
                out |= set(re.findall(r"[a-z][a-z0-9-]*", str(e.get(key) or ""))) & known
        elif e["event"] == "executor.packs_loaded":
            out |= set(re.findall(r"[a-z][a-z0-9-]*", str(e.get("packs") or ""))) & known
        elif e["event"] == "tool_packs.loaded" and e.get("pack") in known:
            out.add(str(e["pack"]))
    return frozenset(out)


def _r(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


# ── the scripted run ─────────────────────────────────────────────────────────


def scripted_problems(rows: list[dict[str, Any]]) -> list[str]:
    """Each way a scripted run differs from a pass. Empty is a pass."""
    out: list[str] = []
    for r in rows:
        where = f"{r['arm']} {r['scenario']} turn {r['turn']}"
        fails = sorted(k for k, v in r["checks"].items() if v is False)
        if fails:
            out.append(f"{where}: fails {fails}")
        if r["sent"]:
            out.append(f"{where}: reached the network {r['sent']}")
        if not r["model_calls"]:
            out.append(f"{where}: the tap recorded no model call")
        if r.get("unknown_routes"):
            out.append(f"{where}: a route has no fixture {r['unknown_routes']}")
        if r.get("web_unknown"):
            out.append(f"{where}: a web page has no fixture {r['web_unknown']}")
    return out


def runnable(scenarios: tuple[Scenario, ...]) -> tuple[Scenario, ...]:
    """*scenarios* without the chats that wait on something (``Scenario.skip``)."""
    return tuple(sc for sc in scenarios if not sc.skip)


def run_scripted(scenarios: tuple[Scenario, ...] = SCENARIOS,
                 arms: tuple[str, ...] = ALL_ARMS) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    for sc in runnable(scenarios):
        for arm in arms_for(sc, arms):
            rows += run_chat(sc, arm)
    return rows, scripted_problems(rows)


# ── the summary ──────────────────────────────────────────────────────────────


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))], 3)


def turn_totals(row: dict[str, Any]) -> dict[str, Any]:
    calls = row["model_calls"]
    return {
        "calls": len(calls),
        "calls_orchestrator": sum(1 for c in calls if c["agent"] == ORCHESTRATOR),
        "calls_email": sum(1 for c in calls if c["agent"] == EA),
        "prompt": sum(c["prompt"] for c in calls),
        "cached": sum(c["cached"] for c in calls),
        "completion": sum(c["completion"] for c in calls),
        "cost_usd": sum(c["cost_usd"] for c in calls) + float(row.get("unseen_usd") or 0.0),
        # The calls of one turn run one after the other, so this is the part
        # of the turn that the model took. The rest is the platform's.
        "model_s": sum(c["dur_s"] or 0.0 for c in calls),
    }


def _turn_kind_ms(row: dict[str, Any]) -> float | None:
    """The time of the turn-kind request in *row*, from its log event."""
    for e in row.get("pack_events") or []:
        if e["event"] == "ai_route.turn_kind" and isinstance(e.get("latency_ms"), (int, float)):
            return float(e["latency_ms"])
    return None


def _asked_pack_items(row: dict[str, Any]) -> bool:
    """True when the turn-kind request of *row* carried pack items (OA-7f)."""
    return any(e["event"] == "executor.packs_turn_kind" and e.get("asked")
               for e in row.get("pack_events") or [])


def _packs_before_first_call(row: dict[str, Any]) -> bool:
    """True when a pack loaded before the first model call of the turn (OA-7h
    duty 5): the load of layer (a) or (c) logs with no model call before it."""
    return any(e["event"] in ("executor.packs_loaded", "tool_packs.loaded",
                              "executor.packs_turn_kind") and e["calls_before"] == 0
               for e in row.get("pack_events") or [])


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    out: dict[str, Any] = {}
    for arm, rs in by_arm.items():
        totals = [turn_totals(r) for r in rs]
        per_check: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in rs:
            for k, v in r["checks"].items():
                if v is not None:
                    per_check[k][1] += 1
                    per_check[k][0] += int(bool(v))
        by_scenario: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in rs:
            by_scenario[r["scenario"]][1] += 1
            by_scenario[r["scenario"]][0] += int(r["passed"])
        handoffs = [(r, t) for r, t in zip(rs, totals, strict=True) if r.get("handoffs")]
        setup_turns = [(r, t) for r, t in handoffs if "setup" in r["handoffs"]
                       and "mail" not in r["handoffs"]]
        with_items = [v for r in rs if _asked_pack_items(r) and (v := _turn_kind_ms(r)) is not None]
        without_items = [v for r in rs if not _asked_pack_items(r)
                         and (v := _turn_kind_ms(r)) is not None]
        first = [r["first_text_s"] for r in rs if r["first_text_s"] is not None]
        total = [r["total_s"] for r in rs if r["total_s"] is not None]
        out[arm] = {
            "turns": len(rs),
            "turns_passed": sum(int(r["passed"]) for r in rs),
            "checks_passed": sum(p for p, _ in per_check.values()),
            "checks": sum(n for _, n in per_check.values()),
            "errors": sum(1 for r in rs if r["error"]),
            "first_text_s": {"mean": _mean(first), "median": _median(first), "p90": _p90(first),
                             "turns_with_text": len(first)},
            "total_s": {"mean": _mean(total), "median": _median(total), "p90": _p90(total)},
            "model_s_per_turn": _mean([t["model_s"] for t in totals]),
            "other_s_per_turn": _mean([(r["total_s"] or 0.0) - t["model_s"]
                                       for r, t in zip(rs, totals, strict=True)]),
            "calls_per_turn": _mean([t["calls"] for t in totals]),
            "calls_orchestrator_per_turn": _mean([t["calls_orchestrator"] for t in totals]),
            "calls_email_per_turn": _mean([t["calls_email"] for t in totals]),
            "prompt_per_turn": _mean([t["prompt"] for t in totals]),
            "cached_per_turn": _mean([t["cached"] for t in totals]),
            # The prompt-cache share (OA-7h duty 6): cache-read tokens over
            # input tokens. A pack load changes the tool list, and the tool
            # list is the start of the cached prefix.
            "cache_share": round(sum(t["cached"] for t in totals)
                                 / sum(t["prompt"] for t in totals), 4)
            if sum(t["prompt"] for t in totals) else None,
            # The gap of the email-setup pack (duty 3): turns that hand off
            # for setup work, and what the hand-off costs.
            "setup_handoff_turns": len(setup_turns),
            "setup_handoff_cost_per_turn_usd": _mean([t["cost_usd"] for _, t in setup_turns]),
            "setup_handoff_total_s": _mean([r["total_s"] or 0.0 for r, _ in setup_turns]),
            "mail_handoff_turns": sum(1 for r, _ in handoffs if "mail" in r["handoffs"]),
            # The turn-kind request with and without pack items (duty 4), ms.
            "turn_kind_ms_with_items": _mean(with_items),
            "turn_kind_ms_without_items": _mean(without_items),
            "turn_kind_turns_with_items": len(with_items),
            "turn_kind_turns_without_items": len(without_items),
            # Duty 5: turns where a pack loaded before the first model call.
            "packs_before_first_call_turns": sum(1 for r in rs if _packs_before_first_call(r)),
            "completion_per_turn": _mean([t["completion"] for t in totals]),
            "cost_per_turn_usd": round(statistics.fmean([t["cost_usd"] for t in totals]), 5)
            if totals else None,
            "cost_usd": round(sum(t["cost_usd"] for t in totals), 4),
            "per_check": {k: f"{p}/{n}" for k, (p, n) in sorted(per_check.items())},
            "by_scenario": {k: f"{p}/{n}" for k, (p, n) in by_scenario.items()},
            **extra_summary(rs),
        }
    return out


# ── the OA-8e columns ────────────────────────────────────────────────────────

#: The checks that guard a rule. Stop result 1 of §4.3.3: one failure on any
#: arm, in any run, stops OA-10.
GUARD_CHECKS = frozenset({
    "held_card", "one_card", "one_card_per_write", "mirror_respected",
    "no_unasked_write", "tools_per_call",
})


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _layers_of(row: dict[str, Any]) -> dict[str, str]:
    """``{slug: layer}`` of the packs that the run loaded before the model (a, c)."""
    out: dict[str, str] = {}
    for e in row.get("pack_events") or []:
        if e["event"] == "executor.packs_loaded":
            out.update(dict(re.findall(r"'([a-z][a-z0-9-]*)': '([a-z_]*)'", str(e.get("layers")))))
    return out


def _unused_pack(row: dict[str, Any]) -> bool:
    """True when a pack loaded before the model and no tool of it ran."""
    from acb_skills import tool_packs

    ran = set(row.get("work_tools") or []) | set(row.get("email_tools") or [])
    return any(not (set(tool_packs.PACKS[slug].tools) & ran)
               for slug in _layers_of(row) if slug in tool_packs.PACKS)


def _is_app_turn(row: dict[str, Any]) -> bool:
    """A turn that needed a pack: it has a ``right_pack`` check (any value)."""
    return "right_pack" in row["checks"]


def extra_summary(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """The columns that OA-8e adds to the summary of one arm."""
    app = [r for r in rs if _is_app_turn(r)]
    layer_b = [r for r in app if r.get("loaded_by_model")]
    packed = [r for r in rs if _layers_of(r)]
    load_ms = [e["ms"] for r in rs for e in r.get("pack_events") or []
               if e["event"] == LAYER_A_EVENT]
    per_call = [c["tools"] for r in rs for c in r["model_calls"]
                if c["agent"] == ORCHESTRATOR and c.get("tools")]
    fan_ms = [e["ms"] for r in rs for e in r.get("pack_events") or []
              if e["event"] == "measure.fanout"]
    not_spawned = [r for r in rs if r["checks"].get("not_spawned") is not None]
    loaded_turns = [turn_totals(r) for r in rs if r.get("loaded_by_model")]
    other_turns = [turn_totals(r) for r in rs if not r.get("loaded_by_model")]

    def share(ts: list[dict[str, Any]]) -> float | None:
        prompt = sum(t["prompt"] for t in ts)
        return round(sum(t["cached"] for t in ts) / prompt, 4) if prompt else None

    kinds: dict[str, int] = defaultdict(int)
    refused: dict[str, int] = defaultdict(int)
    for r in rs:
        for agent, n in (r.get("specialist_handoffs") or {}).items():
            kinds[agent] += n
        for agent, n in (r.get("handoffs_refused") or {}).items():
            refused[agent] += n
    return {
        "app_turns": len(app),
        "layer_b_turns": len(layer_b),
        "layer_b_share_pct": _pct(len(layer_b), len(app)),
        "pack_turns": len(packed),
        "unused_pack_turns": sum(1 for r in packed if _unused_pack(r)),
        "unused_pack_share_pct": _pct(sum(1 for r in packed if _unused_pack(r)), len(rs)),
        "layer_a_ms_mean": _mean(load_ms), "layer_a_ms_p90": _p90(load_ms),
        "tools_per_call_max": max(per_call, default=None),
        "tools_per_call_mean": _mean([float(n) for n in per_call]),
        "cache_share_load_turns": share(loaded_turns),
        "cache_share_other_turns": share(other_turns),
        "specialist_handoffs": dict(kinds),
        "handoffs_refused": dict(refused),
        "app_handoff_turns": app_handoff_turns(rs),
        "fanout_ms_mean": _mean(fan_ms), "fanout_ms_p90": _p90(fan_ms),
        "fanout_calls": len(fan_ms),
        "research_total_median": _median([r["total_s"] for r in rs
                                          if "spawned" in r["checks"] and r["total_s"] is not None
                                          and r.get("set") == "fanout"
                                          and r["checks"].get("spawned") is not False
                                          and r.get("research")]),
        "not_spawned_turns": len(not_spawned),
        "spawned_on_not_spawned": sum(1 for r in not_spawned if r["checks"]["not_spawned"] is False),
        "guard_failures": sorted({f"{r['scenario']}#{r['turn']} {k}" for r in rs
                                  for k in GUARD_CHECKS if r["checks"].get(k) is False}),
        "by_set": {name: set_summary([r for r in rs if r.get("set", "email") == name])
                   for name in sorted({r.get("set", "email") for r in rs})},
    }


def app_handoff_turns(rs: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Fix round 1 (P2-b). The pack-arm turns that handed work to an owner whose
    packs are on, by owner and by reason (:func:`checks.handoff_reason`). A hand-off
    that passes its check is still a turn that the pack did not serve."""
    out: dict[str, dict[str, int]] = {}
    for r in rs:
        for owner, why in (r.get("handoff_reasons") or {}).items():
            out.setdefault(owner, {}).setdefault(why, 0)
            out[owner][why] += 1
    return out


def set_summary(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers of one set of chats on one arm (spec §4.3.3, "Numbers to report")."""
    totals = [turn_totals(r) for r in rs]
    first = [r["first_text_s"] for r in rs if r["first_text_s"] is not None]
    total = [r["total_s"] for r in rs if r["total_s"] is not None]
    prompt = sum(t["prompt"] for t in totals)
    return {
        "turns": len(rs), "turns_passed": sum(int(r["passed"]) for r in rs),
        "pass_pct": _pct(sum(int(r["passed"]) for r in rs), len(rs)),
        "first_text_median": _median(first), "first_text_p90": _p90(first),
        "total_median": _median(total), "total_p90": _p90(total),
        "calls_per_turn": _mean([t["calls"] for t in totals]),
        "prompt_per_turn": _mean([t["prompt"] for t in totals]),
        "cache_share": round(sum(t["cached"] for t in totals) / prompt, 4) if prompt else None,
        "cost_per_turn_usd": round(statistics.fmean([t["cost_usd"] for t in totals]), 5)
        if totals else None,
    }


def stop_results(summary: dict[str, Any]) -> list[tuple[int, str, str]]:
    """The ten stop results of §4.3.3 that one results file can show, as
    ``(number, state, why)``. State: ``STOP``, ``ok`` or ``n/a``. Results 3, 4 and
    9 need the ``general`` set, a run with no packs, and the spawn arm. Result 10
    needs the spawn arm. They show ``n/a`` until those exist."""
    out: list[tuple[int, str, str]] = []
    guards = sorted({g for v in summary.values() for g in v.get("guard_failures", [])})
    out.append((1, "STOP" if guards else "ok", ", ".join(guards) or "no guard check failed"))
    dele, packs = summary.get("delegation"), summary.get("pack")
    if dele and packs:
        def app_rate(v: dict[str, Any]) -> float | None:
            sets = [s for n, s in v["by_set"].items() if n not in ("email", "fanout")]
            turns = sum(s["turns"] for s in sets)
            return 100.0 * sum(s["turns_passed"] for s in sets) / turns if turns else None

        a, b = app_rate(dele), app_rate(packs)
        if a is None or b is None:
            out.append((2, "n/a", "no app turn outside email"))
        else:
            out.append((2, "STOP" if b < a - 2 else "ok", f"packs {b:.1f}% vs delegation {a:.1f}%"))
        cost = [v["by_set"][n]["cost_per_turn_usd"] for v in (dele, packs)
                for n in v["by_set"] if n not in ("email", "fanout")]
        out.append((7, "n/a" if None in cost else (
            "STOP" if packs["cost_per_turn_usd"] > dele["cost_per_turn_usd"] else "ok"),
            f"packs USD {packs['cost_per_turn_usd']} vs delegation {dele['cost_per_turn_usd']}"))
    if packs:
        share = packs.get("layer_b_share_pct")
        out.append((5, "n/a" if share is None else ("STOP" if share > 15 else "ok"),
                    f"layer (b) on {share}% of app turns"))
        unused = packs.get("unused_pack_share_pct")
        out.append((6, "n/a" if unused is None else ("STOP" if unused > 20 else "ok"),
                    f"an unused pack on {unused}% of turns"))
        from acb_skills.tool_packs import MAX_REQUEST_TOOLS

        top = packs.get("tools_per_call_max")
        out.append((8, "n/a" if top is None else ("STOP" if top > MAX_REQUEST_TOOLS else "ok"),
                    f"at most {top} tools in a request, cap {MAX_REQUEST_TOOLS}"))
    if packs:
        by = packs.get("app_handoff_turns") or {}
        total = sum(n for reasons in by.values() for n in reasons.values())
        fault = sum(reasons.get("model_choice", 0) for reasons in by.values())
        # OA-8i: a refused hand-off ran no work, so it does not count here.
        tried = sum((packs.get("handoffs_refused") or {}).values())
        out.append((11, "STOP" if fault else ("REVIEW" if total else "ok"),
                    f"{total} hand-off turns on a packed app, {fault} by model choice: {by}. "
                    f"Refused hand-offs, not counted: {tried}"))
    spawn = summary.get(SPAWN_ARM)
    base = summary.get("pack")
    slow = (spawn or {}).get("research_total_median"), (base or {}).get("research_total_median")
    if None in slow:
        out.append((9, "n/a", "needs the research chats on both the pack and the spawn arm"))
    else:
        out.append((9, "STOP" if slow[0] > slow[1] else "ok",
                    f"research turn median {slow[0]} s with spawn, {slow[1]} s without"))
    if spawn and spawn.get("not_spawned_turns"):
        bad, total = spawn["spawned_on_not_spawned"], spawn["not_spawned_turns"]
        out.append((10, "STOP" if bad * 9 > total else "ok",
                    f"the main agent spawned on {bad} of {total} turns that must not spawn"))
    else:
        out.append((10, "n/a", "needs the not_spawned turns on the spawn arm"))
    for n, why in ((3, "needs the general set and a run with no packs"),
                   (4, "needs the general set")):
        out.append((n, "n/a", why))
    return sorted(out)


def report(data: dict[str, Any]) -> str:
    """The tables of a results file, in Markdown."""
    s = data["summary"]
    arms = [a for a in ALL_ARMS if a in s]
    head = "| Measure | " + " | ".join(arms) + " |\n|---" + "|---" * len(arms) + "|"
    lines = [head]

    def row(label: str, get: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(str(get(s[a])) for a in arms) + " |")

    row("Turns that pass every check", lambda v: f"{v['turns_passed']}/{v['turns']}")
    row("Checks that pass", lambda v: f"{v['checks_passed']}/{v['checks']}")
    row("Turns that ended in an error", lambda v: v["errors"])
    row("First text, median s", lambda v: v["first_text_s"]["median"])
    row("First text, mean s", lambda v: v["first_text_s"]["mean"])
    row("First text, p90 s", lambda v: v["first_text_s"]["p90"])
    row("Turn total, median s", lambda v: v["total_s"]["median"])
    row("Turn total, mean s", lambda v: v["total_s"]["mean"])
    row("Turn total, p90 s", lambda v: v["total_s"]["p90"])
    row("Of the total, in model calls, mean s", lambda v: v.get("model_s_per_turn"))
    row("Of the total, outside the model, mean s", lambda v: v.get("other_s_per_turn"))
    row("Model calls per turn", lambda v: v["calls_per_turn"])
    row("Of them, the orchestrator", lambda v: v["calls_orchestrator_per_turn"])
    row("Of them, the email assistant", lambda v: v["calls_email_per_turn"])
    row("Input tokens per turn", lambda v: v["prompt_per_turn"])
    row("Of them, from the cache", lambda v: v["cached_per_turn"])
    row("Cache share (cache read / input tokens)", lambda v: v.get("cache_share"))
    row("Turns that hand off for setup work", lambda v: v.get("setup_handoff_turns"))
    row("Of them, cost per turn, USD", lambda v: v.get("setup_handoff_cost_per_turn_usd"))
    row("Turns that hand off mail work", lambda v: v.get("mail_handoff_turns"))
    row("Turn-kind request with pack items, mean ms",
        lambda v: v.get("turn_kind_ms_with_items"))
    row("Turn-kind request without pack items, mean ms",
        lambda v: v.get("turn_kind_ms_without_items"))
    row("Turns with a pack loaded before the first model call",
        lambda v: v.get("packs_before_first_call_turns"))
    row("Output tokens per turn", lambda v: v["completion_per_turn"])
    row("Cost per turn, USD", lambda v: v["cost_per_turn_usd"])
    row("Cost of the arm, USD", lambda v: v["cost_usd"])
    if data.get("rows"):
        lines += ["", "Counted spend by agent (USD):", "", spend.spend_table(data["rows"])]
    scen = sorted({k for a in arms for k in s[a]["by_scenario"]})
    lines += ["", "| Scenario | " + " | ".join(arms) + " |", "|---" + "|---" * len(arms) + "|"]
    for k in scen:
        lines.append(f"| {k} | " + " | ".join(s[a]["by_scenario"].get(k, "-") for a in arms) + " |")
    names = sorted({k for a in arms for k in s[a]["per_check"]})
    lines += ["", "| Check | " + " | ".join(arms) + " |", "|---" + "|---" * len(arms) + "|"]
    for k in names:
        lines.append(f"| `{k}` | " + " | ".join(s[a]["per_check"].get(k, "-") for a in arms) + " |")
    if any("by_set" in s[a] for a in arms):
        lines += ["", "OA-8e: the numbers of each set, on each arm.", "",
                  "| Set | Arm | Pass | First text median / p90 s | Total median / p90 s "
                  "| Calls | Input tokens | Cache share | USD a turn |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for name in sorted({n for a in arms for n in s[a].get("by_set", {})}):
            for a in arms:
                v = s[a].get("by_set", {}).get(name)
                if v:
                    lines.append(
                        f"| {name} | {a} | {v['turns_passed']}/{v['turns']} "
                        f"| {v['first_text_median']} / {v['first_text_p90']} "
                        f"| {v['total_median']} / {v['total_p90']} | {v['calls_per_turn']} "
                        f"| {v['prompt_per_turn']} | {v['cache_share']} | {v['cost_per_turn_usd']} |")
        lines += ["", "| Loader and cap | " + " | ".join(arms) + " |",
                  "|---" + "|---" * len(arms) + "|"]
        for label, key in (
                ("Turns that needed a pack", "app_turns"),
                ("Layer (b) turns (the model loaded a pack)", "layer_b_turns"),
                ("Layer (b), percent of those turns", "layer_b_share_pct"),
                ("Turns with a pack loaded before the model", "pack_turns"),
                ("Of them, a pack no tool of which ran", "unused_pack_turns"),
                ("Layer (a) time, mean ms", "layer_a_ms_mean"),
                ("Layer (a) time, p90 ms", "layer_a_ms_p90"),
                ("Tools in a request, mean", "tools_per_call_mean"),
                ("Tools in a request, most", "tools_per_call_max"),
                ("Cache share, turns with a model load", "cache_share_load_turns"),
                ("Cache share, other turns", "cache_share_other_turns"),
                ("Hand-offs to an owner", "specialist_handoffs"),
                ("Hand-offs that the product refused", "handoffs_refused"),
                ("Hand-off turns on a packed app, by owner and reason", "app_handoff_turns")):
            lines.append(f"| {label} | " + " | ".join(str(s[a].get(key)) for a in arms) + " |")
        lines += ["", "| Stop result of §4.3.3 | State | Why |", "|---|---|---|"]
        lines += [f"| {n} | {state} | {why} |" for n, state, why in stop_results(s)]
    return "\n".join(lines)


# ── the live run ─────────────────────────────────────────────────────────────


def _sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=HERE, check=False).stdout.strip()
    except OSError:
        return ""


def chunk_spend(data: dict[str, Any]) -> float:
    """The USD that one chunk spent by itself. ``spent_usd`` of a chunk counts
    from its ``spent_before_usd``, so the chunk's own figure is the difference."""
    if isinstance(data.get("spent_this_chunk_usd"), (int, float)):
        return float(data["spent_this_chunk_usd"])
    return max(0.0, float(data.get("spent_usd") or 0.0) - float(data.get("spent_before_usd") or 0.0))


def spent_so_far(paths: list[Path]) -> float:
    """The ``--spent-before`` for the chunk that follows the chunks in *paths*:
    the sum of what each chunk spent by itself. Never the sum of their
    ``spent_usd``, which already holds the chunks before it (the first live run
    of OA-8e counted that twice)."""
    return round(sum(chunk_spend(json.loads(p.read_text(encoding="utf-8"))) for p in paths), 4)


def _write(out: Path, data: dict[str, Any]) -> None:
    data["spent_this_chunk_usd"] = round(
        float(data.get("spent_usd") or 0.0) - float(data.get("spent_before_usd") or 0.0), 4)
    data["summary"] = summarise(data["rows"])
    data["spend_by_agent"] = {k: round(v, 4) for k, v in spend.spend_by_agent(data["rows"]).items()}
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def utc_now() -> datetime:
    return datetime.now(UTC)


def router_up(base: str, timeout: float = 3.0) -> bool:
    """True when the Router's port accepts a connection. It costs nothing.

    The live run reaches the Router through an SSH tunnel. On 2026-10-25 the
    tunnel dropped during a chat, and that chat hung far past its turn
    timeout. So the run checks the port before each chat, and drops a chat
    that the Router left during it (:func:`run_live`).
    """
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    try:
        with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 80),
                                      timeout=timeout):
            return True
    except OSError:
        return False


def _lost_the_router(rows: list[dict[str, Any]]) -> bool:
    """True when a turn of the chat failed to reach the Router."""
    words = ("Connection error", "ConnectError", "connection attempts failed")
    return any(any(w in str(r.get("error") or "") for w in words) for r in rows)


def run_live(router: tuple[str, str], scenarios: tuple[Scenario, ...], arms: tuple[str, ...],
             *, repeat: int, tier: str, budget: float, out: Path, timeout: float,
             first_repeat: int = 1, spent_before: float = 0.0,
             allow_peak: bool = True, clock: Any = None) -> dict[str, Any]:
    """The live chats. *budget* caps the spend, counted from *spent_before*,
    so a run in chunks keeps one cap. The check runs before each chat AND
    before each turn after the first, so one long chat cannot pass the cap by
    more than one turn. A chat that lost the Router is dropped, and the run
    stops. Unless *allow_peak*, the run also stops before a chat that would
    start in a DeepSeek peak band (*clock* gives the time, for a test)."""
    clock = clock or utc_now
    data: dict[str, Any] = {
        "date": datetime.now(UTC).date().isoformat(), "sha": _sha(), "tier": tier,
        "email_tier": "tier-powerful (its own default, the floor of a delegated run)",
        "repeat": repeat, "budget_usd": budget, "rate_card": RATE_CARD["usd_per_1m"],
        "rows": [], "stopped": None, "dropped": [], "spent_before_usd": spent_before,
    }
    spent = spent_before

    def on_turn(row: dict[str, Any]) -> None:
        nonlocal spent
        t = turn_totals(row)
        spent += t["cost_usd"]
        print(f"[{row['arm']:<10}] {row['scenario']}#{row['turn']} r{row['repeat']}: "
              f"{'pass' if row['passed'] else 'FAIL'} first {row['first_text_s']} s, "
              f"total {row['total_s']} s, {t['calls']} calls, in {t['prompt']} "
              f"(cached {t['cached']}), out {t['completion']}, USD {t['cost_usd']:.4f}"
              f" | spent USD {spent:.3f} of {budget:.2f}", flush=True)
        bad = [k for k, v in row["checks"].items() if v is False]
        if bad:
            print(f"      fails {bad}: {row['answer'][:240]!r}", flush=True)

    def stop(why: str) -> dict[str, Any]:
        data["stopped"] = why
        data["spent_usd"] = round(spent, 4)
        print(why, flush=True)
        _write(out, data)
        return data

    for rep in range(first_repeat, first_repeat + repeat):
        for i, sc in enumerate(scenarios):
            mine = arms_for(sc, arms)
            order = mine if (rep + i) % 2 else tuple(reversed(mine))
            for arm in order:
                if spent >= budget:
                    return stop(f"the budget: USD {spent:.3f} spent before {sc.id} {arm} r{rep}")
                ok, why = spend.peak_gate(clock(), allow_peak)
                if not ok:
                    return stop(f"peak band before {sc.id} {arm} r{rep}. {why}")
                if not router_up(router[0]):
                    return stop(f"the Router does not answer before {sc.id} {arm} r{rep}")
                rows = run_chat(sc, arm, router=router, tier=tier, timeout=timeout,
                                repeat=rep, on_turn=on_turn,
                                should_stop=lambda: spent >= budget)
                if _lost_the_router(rows) or not router_up(router[0]):
                    data["dropped"].append({"scenario": sc.id, "arm": arm, "repeat": rep,
                                            "usd": round(sum(turn_totals(r)["cost_usd"]
                                                             for r in rows), 4)})
                    return stop(f"the Router went away during {sc.id} {arm} r{rep}: "
                                "the chat is dropped")
                data["rows"] += rows
                data["spent_usd"] = round(spent, 4)
                if len(rows) < len(sc.turns):
                    return stop(f"the budget: USD {spent:.3f} spent inside {sc.id} {arm} r{rep}, "
                                f"after turn {len(rows)} of {len(sc.turns)}")
                _write(out, data)
    data["spent_usd"] = round(spent, 4)
    _write(out, data)
    return data


def merge(paths: list[Path], out: Path) -> dict[str, Any]:
    """One results file from the files of several chunks of one live run.

    Each chunk keeps its own SHA, in ``chunks_run`` and on each of its rows,
    because the chunks of one run can run on different code (fix round 1).
    ``sha`` is the one SHA when all chunks agree, else ``"mixed"``."""
    parts = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    data = {k: v for k, v in parts[0].items()
            if k not in ("rows", "summary", "stopped", "sha")}
    for p in parts:
        for r in p["rows"]:
            r.setdefault("sha", p.get("sha") or "")
    data["chunks_run"] = [{"chunk": path.stem, "sha": p.get("sha") or "",
                           "rows": len(p["rows"]),
                           "repeats": sorted({r["repeat"] for r in p["rows"]}),
                           "scenarios": sorted({r["scenario"] for r in p["rows"]})}
                          for path, p in zip(paths, parts, strict=True)]
    shas = sorted({c["sha"] for c in data["chunks_run"]})
    data["sha"] = shas[0] if len(shas) == 1 else "mixed"
    data["rows"] = [r for p in parts for r in p["rows"]]
    data["dropped"] = [d for p in parts for d in p.get("dropped") or []]
    # Rows that a person took out of a chunk, with the reason. The report
    # names them, so a reader sees what the numbers do not hold.
    data["replaced"] = [d for p in parts for d in p.get("replaced") or []]
    data["stopped"] = [p["stopped"] for p in parts if p.get("stopped")]
    data["chunks"] = len(parts)
    data["spent_usd"] = round(sum(chunk_spend(p) for p in parts), 4)
    data["repeat"] = max((r["repeat"] for r in data["rows"]), default=0)
    _write(out, data)
    return data


#: The checks that read only the stored answer and tasks of a turn. A
#: re-score runs these again. Each other check keeps its stored value,
#: because a results file keeps no tool arguments, cards or gateway calls.
RESCORED = frozenset({
    "answer_all", "answer_some", "no_name_claim", "no_reference_subject",
    "admits_unreadable", "no_content_claim", "task_no_guess", "answer_none", "figure",
    "count_stated", "answer_not_mine",
})
GUARDS_NO_GATEWAY_WRITE = ("no_unasked_write", "one_card_per_write", "mirror_respected")


def _rescore_stored(r: dict[str, Any], turn: Any, changes: list[str]) -> None:
    """Fix round 3. The checks that a row can score again from what it stores:
    the names of the work tools it ran and the gateway writes it made. It only
    ever turns a failure into a pass (never the other way), because the stored
    names include calls that the run refused.

    * ``ran_tool`` passes when a tool of its list is in the stored names.
    * The three guard checks pass when the turn had no write at the gateway: a
      call that wrote nothing is no write (item 3)."""
    where = f"{r['arm']} {r['scenario']}#{r['turn']} r{r['repeat']}"
    names = list(r.get("work_tools") or [])
    for c in turn.checks:
        if c.kind == "ran_tool" and r["checks"].get(c.name) is False and any(
                n in c.arg for n in names):
            r["checks"][c.name] = True
            changes.append(f"{where}: {c.name} False -> True")
    if r.get("work_writes"):
        return
    for name in GUARDS_NO_GATEWAY_WRITE:
        old = r["checks"].get(name)
        if old is False:
            new = None if name == "one_card_per_write" else True
            r["checks"][name] = new
            changes.append(f"{where}: {name} False -> {new}")


def rescore(data: dict[str, Any], when: str = "") -> list[str]:
    """Run the checks of :data:`RESCORED` again on each stored row, with the
    checks of today's scenarios. Each change goes in ``rescored``, and the
    summary is computed again. Returns the changes."""
    from evals.one_assistant_measure.scenarios import scenario

    changes: list[str] = []
    for r in data["rows"]:
        turn = scenario(r["scenario"]).turns[r["turn"] - 1]
        obs = ck.Observation(arm=r["arm"], answer=r["answer"], tasks=list(r.get("tasks") or []))
        for c in turn.checks:
            if c.kind not in RESCORED:
                continue
            new = ck.KINDS[c.kind](obs, c.arg)
            old = r["checks"].get(c.name)
            if new != old:
                changes.append(f"{r['arm']} {r['scenario']}#{r['turn']} r{r['repeat']}: "
                               f"{c.name} {old} -> {new}")
                r["checks"][c.name] = new
        _rescore_stored(r, turn, changes)
        if r["arm"] == "pack" and "no_email_delegation" in r["checks"]:
            # OA-7h fix round 1: the hand-off check reads the stored tasks.
            now = ck._no_email_delegation(obs, None)
            was = r["checks"]["no_email_delegation"]
            if now != was:
                changes.append(f"{r['arm']} {r['scenario']}#{r['turn']} r{r['repeat']}: "
                               f"no_email_delegation {was} -> {now}")
                r["checks"]["no_email_delegation"] = now
            r["handoffs"] = [ck.handoff_kind(t) for t in obs.tasks]
        r["passed"] = ck.passed(r["checks"])
    data["rescored"] = {"when": when or datetime.now(UTC).date().isoformat(),
                        "sha": _sha(), "checks": sorted(RESCORED), "changes": changes}
    data["summary"] = summarise(data["rows"])
    return changes


def _preflight(router: tuple[str, str], tier: str) -> tuple[bool, str]:
    import httpx

    base, key = router
    try:
        resp = httpx.post(f"{base}/v1/chat/completions", timeout=60.0,
                          headers={"Authorization": f"Bearer {key}", "X-CC-Source": "eval"},
                          json={"model": tier, "max_tokens": 4, "stream": False,
                                "messages": [{"role": "user", "content": "Say OK."}]})
    except httpx.HTTPError as exc:
        return False, f"NO-GO: the Router at {base} does not answer: {type(exc).__name__}"
    if resp.status_code != 200:
        return False, f"NO-GO: one call on {tier} answered {resp.status_code}"
    return True, f"the Router at {base} answers on {tier}"


# ── the command line ─────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true", help="no model, no network (CI)")
    mode.add_argument("--live", action="store_true", help="the real model, through the Router")
    mode.add_argument("--report", action="store_true", help="print the tables of --out")
    mode.add_argument("--merge", default="", help="a comma list of chunk files, into --out")
    mode.add_argument("--rescore", action="store_true",
                      help="run the answer checks again on the stored rows of --out")
    parser.add_argument("--arms", default=",".join(ALL_ARMS))
    parser.add_argument("--scenarios", default="", help="a comma list of scenario ids")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--first-repeat", type=int, default=1,
                        help="live: the number of the first repeat, for a run in chunks")
    parser.add_argument("--spent-before", type=float, default=0.0,
                        help="live: USD that earlier chunks spent, counted against the budget")
    parser.add_argument("--spent-before-from", default="",
                        help="live: a comma list of earlier chunk files. The runner sums what each "
                             "chunk spent by itself and uses it as --spent-before")
    parser.add_argument("--tier", default="tier-balanced", help="the orchestrator's tier")
    parser.add_argument("--budget-usd", type=float, default=5.0)
    parser.add_argument("--allow-peak", action="store_true",
                        help="live: run inside a DeepSeek peak band (twice the price)")
    parser.add_argument("--timeout", type=float, default=300.0, help="live: seconds a turn")
    parser.add_argument("--out", default=str(HERE / "results.json"))
    args = parser.parse_args(argv)
    # An answer can hold a rupee sign, and a Windows console is cp1252.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

    asked = [a.strip() for a in args.arms.split(",") if a.strip()]
    arms = tuple(a for a in asked if a in ALL_ARMS)
    wanted = {s.strip() for s in args.scenarios.split(",") if s.strip()}
    chosen = tuple(sc for sc in all_scenarios() if not wanted or sc.id in wanted)
    for sc in chosen:
        if sc.skip:
            print(f"SKIP: {sc.id} ({sc.set}): {sc.skip}")
    scenarios = runnable(chosen)
    out = Path(args.out)

    if args.report:
        print(report(json.loads(out.read_text(encoding="utf-8"))))
        return EXIT_PASS
    if args.merge:
        data = merge([Path(p) for p in args.merge.split(",") if p.strip()], out)
        print(report(data))
        return EXIT_PASS
    if args.rescore:
        data = json.loads(out.read_text(encoding="utf-8"))
        changes = rescore(data)
        out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(changes)} checks changed")
        for line in changes:
            print(f"  {line}")
        print(report(data))
        return EXIT_PASS
    if args.scripted:
        rows, problems = run_scripted(scenarios, arms)
        for r in rows:
            print(f"  {'pass' if r['passed'] else 'FAIL'}  {r['arm']:<10} {r['scenario']}#{r['turn']}"
                  f"  {len(r['model_calls'])} calls  {r['email_tools']}")
        for p in problems:
            print(f"  FAIL {p}")
        print("FAIL" if problems else f"PASS: {len(rows)} turns on {len(arms)} arms")
        return EXIT_FAIL if problems else EXIT_PASS

    router = router_env()
    if router is None:
        print("NO-GO: set LITELLM_BASE_URL and LITELLM_API_KEY, or write them in eval.env")
        return EXIT_NO_GO
    from evals.coding_engine.preflight import is_local

    if not is_local(router[0]):
        # Fix round 1: the Router is reached through a tunnel on this machine.
        print(f"NO-GO: {router[0]} is not on this machine. Open the tunnel to the Console.")
        return EXIT_NO_GO
    peak_ok, peak_why = spend.peak_gate(utc_now(), args.allow_peak)
    if peak_why:
        print(peak_why)
    if not peak_ok:
        return EXIT_NO_GO
    ok, why = _preflight(router, args.tier)
    print(why)
    if not ok:
        return EXIT_NO_GO
    before = args.spent_before + spent_so_far(
        [Path(p) for p in args.spent_before_from.split(",") if p.strip()])
    data = run_live(router, scenarios, arms, repeat=max(1, args.repeat), tier=args.tier,
                    budget=args.budget_usd, out=out, timeout=args.timeout,
                    first_repeat=args.first_repeat, spent_before=before,
                    allow_peak=args.allow_peak)
    print(report(data))
    print(f"results: {out}")
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
