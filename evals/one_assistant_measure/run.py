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
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.one_assistant_measure import checks as ck
from evals.one_assistant_measure import mailbox as mb
from evals.one_assistant_measure import pack
from evals.one_assistant_measure.scenarios import SCENARIOS, Scenario
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
EA = pack.EA
#: The settings of production that the measure sets in its own process (OA-7h).
#: They change how the run behaves, so both arms get them. The measure never
#: writes them on a box.
PRODUCTION: dict[str, Any] = {
    "email_held_card_send": True,
    "ai_tier_routing": "orchestrator",
    "ai_reasoning_off_agents": "orchestrator",
    "maf_native_sessions": True,
    "narrowing_agents": "email-assistant",
}


def pack_flag() -> dict[str, Any]:
    """The real flag of the ``pack`` arm: ``TOOL_PACKS`` and ``TOOL_PACKS_ORGS``.

    ``email`` alone, for the eval org only. The ``email-setup`` pack does not
    fit beside ``email`` at the real base of the run (H-417)."""
    return {"tool_packs": "email", "tool_packs_orgs": ds.ORG}
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
    usd, source = cost_of(c.usage)

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
        "cost_usd": round(usd, 6), "cost_source": source,
    }


# ── the scripted models ──────────────────────────────────────────────────────


def pack_tool_names() -> frozenset[str]:
    """The tools of the real ``email`` pack, from its skill family."""
    from acb_skills.skill_families import SKILL_FAMILIES

    return frozenset(SKILL_FAMILIES["email"]["tools"])


def scripted_models(sc: Scenario, arm: str) -> dict[str, ReplyModel]:
    """The known-good models of *sc* on *arm*, from the plan of each turn.

    On the ``pack`` arm a call of the ``email`` pack runs in the orchestrator's
    own run. A call that the pack does not hold (a rule, a setting) goes to the
    email assistant by ``call_agent``, as the rules of the pack say (H-417).
    The model loads the pack first when the member's words do not (layer (b)).
    """
    from acb_skills import tool_packs

    in_pack = pack_tool_names()
    orch: list[Any] = []
    email: list[Any] = []
    n = 0
    used = False
    for turn in sc.turns:
        plan = turn.plan
        if arm == "delegation" and plan.calls:
            n += 1
            orch.append(tool_turn(pack.DELEGATE_TOOL, {"task": turn.words}, call_id=f"call_{n}"))
            for name, args in plan.calls:
                n += 1
                email.append(tool_turn(name, args, call_id=f"call_{n}"))
            email.append(text_turn(plan.answer))
        elif arm == "pack":
            mine = [c for c in plan.calls if c[0] in in_pack]
            setup = [c for c in plan.calls if c[0] not in in_pack]
            if mine and not (used or tool_packs.matched_layer("email", turn.words)):
                n += 1
                orch.append(tool_turn(tool_packs.LOAD_TOOL, {"pack": "email"}, call_id=f"call_{n}"))
            for name, args in mine:
                n += 1
                orch.append(tool_turn(name, args, call_id=f"call_{n}"))
            if setup:
                n += 1
                orch.append(tool_turn("call_agent", {"agent_name": EA, "message": turn.words},
                                      call_id=f"call_{n}"))
                for name, args in setup:
                    n += 1
                    email.append(tool_turn(name, args, call_id=f"call_{n}"))
                email.append(text_turn(plan.answer))
            used = used or bool(mine)
        orch.append(text_turn(plan.answer))
    models = {ORCHESTRATOR: ReplyModel(orch)}
    if email:
        models[EA] = ReplyModel(email)
    return models


# ── the log tap ──────────────────────────────────────────────────────────────

#: The events of the pack code that the report reads (OA-7h duties 4 and 5):
#: the turn-kind request with its time, and each load of a pack, with the
#: number of model calls that came before it in the turn.
WATCHED = ("ai_route.turn_kind", "tool_packs.", "executor.pack")
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


def run_chat(sc: Scenario, arm: str, *, router: tuple[str, str] | None = None,
             tier: str | None = None, timeout: float = 60.0, repeat: int = 1,
             models: dict[str, ReplyModel] | None = None,
             on_turn: Any = None) -> list[dict[str, Any]]:
    """One chat of *sc* on *arm*: each turn through the real orchestrator run.

    *router* None is a scripted run: the plans of *sc* play, or *models*.
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
        settings = get_settings()
        for name, value in {**PRODUCTION, **(pack_flag() if arm == "pack" else {})}.items():
            harness._mp.setattr(settings, name, value, raising=False)
        # OA-7h duty 1: the mark of a chat that read mail lives in Redis. The
        # harness installs the in-memory store, and the measure installs it
        # again to keep the handle. Without a store the mark read fails
        # closed, and every turn reads as held.
        store = install_memory_mark_store(harness._mp)
        assert store is not None, "the in-memory mark store did not install"
        tap = LogTap(harness.model_calls)
        tap.install(harness._mp)
        if not live:
            harness.models = models if models is not None else scripted_models(sc, arm)
        member = Member()
        history = [dict(h) for h in sc.history]
        thread: str | None = None
        for index, turn in enumerate(sc.turns):
            marks = (len(gateway.calls), len(member.cards), len(harness.model_calls),
                     len(tap.events), len(harness.sent), len(harness.passed),
                     len(store.calls))
            record = asyncio.run(harness.drive(
                turn.words, sc.page, member=ds.MEMBER, org=ds.ORG, tier=tier,
                on_card=member, timeout=timeout, thread_id=thread,
                history=history or None, typed=turn.words,
            ))
            thread = record.thread_id
            obs = ck.Observation(
                arm=arm, answer=record.answer, email_calls=ck.email_calls_of(record.events),
                tasks=ck.tasks_of(record.tool_calls),
                refused=[],
                cards=member.cards[marks[1]:], gateway=gateway.calls[marks[0]:],
                error=record.error, turn=index + 1,
            )
            result = ck.check_turn(turn, obs)
            calls = [call_row(c) for c in harness.model_calls[marks[2]:]]
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
                "handoffs": [ck.handoff_kind(t) for t in obs.tasks],
                "pack_events": events,
                "mark_writes": sum(1 for kind, _ in store.calls[marks[6]:] if kind == "setex"),
            }
            rows.append(row)
            if on_turn is not None:
                on_turn(row)
            history += [{"role": "user", "content": turn.words},
                        {"role": "assistant", "content": record.answer}]
    return rows


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
    return out


def run_scripted(scenarios: tuple[Scenario, ...] = SCENARIOS,
                 arms: tuple[str, ...] = ARMS) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    for sc in scenarios:
        for arm in arms:
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
        "cost_usd": sum(c["cost_usd"] for c in calls),
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
        }
    return out


def report(data: dict[str, Any]) -> str:
    """The tables of a results file, in Markdown."""
    s = data["summary"]
    arms = [a for a in ARMS if a in s]
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
    scen = sorted({k for a in arms for k in s[a]["by_scenario"]})
    lines += ["", "| Scenario | " + " | ".join(arms) + " |", "|---" + "|---" * len(arms) + "|"]
    for k in scen:
        lines.append(f"| {k} | " + " | ".join(s[a]["by_scenario"].get(k, "-") for a in arms) + " |")
    names = sorted({k for a in arms for k in s[a]["per_check"]})
    lines += ["", "| Check | " + " | ".join(arms) + " |", "|---" + "|---" * len(arms) + "|"]
    for k in names:
        lines.append(f"| `{k}` | " + " | ".join(s[a]["per_check"].get(k, "-") for a in arms) + " |")
    return "\n".join(lines)


# ── the live run ─────────────────────────────────────────────────────────────


def _sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=HERE, check=False).stdout.strip()
    except OSError:
        return ""


def _write(out: Path, data: dict[str, Any]) -> None:
    data["summary"] = summarise(data["rows"])
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


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
             first_repeat: int = 1, spent_before: float = 0.0) -> dict[str, Any]:
    """The live chats. *budget* caps the spend, counted from *spent_before*,
    so a run in chunks keeps one cap. A chat that lost the Router is dropped,
    and the run stops."""
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
            order = arms if (rep + i) % 2 else tuple(reversed(arms))
            for arm in order:
                if spent >= budget:
                    return stop(f"the budget: USD {spent:.3f} spent before {sc.id} {arm} r{rep}")
                if not router_up(router[0]):
                    return stop(f"the Router does not answer before {sc.id} {arm} r{rep}")
                rows = run_chat(sc, arm, router=router, tier=tier, timeout=timeout,
                                repeat=rep, on_turn=on_turn)
                if _lost_the_router(rows) or not router_up(router[0]):
                    data["dropped"].append({"scenario": sc.id, "arm": arm, "repeat": rep,
                                            "usd": round(sum(turn_totals(r)["cost_usd"]
                                                             for r in rows), 4)})
                    return stop(f"the Router went away during {sc.id} {arm} r{rep}: "
                                "the chat is dropped")
                data["rows"] += rows
                data["spent_usd"] = round(spent, 4)
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
    data["spent_usd"] = max(float(p.get("spent_usd") or 0.0) for p in parts)
    data["repeat"] = max((r["repeat"] for r in data["rows"]), default=0)
    _write(out, data)
    return data


#: The checks that read only the stored answer and tasks of a turn. A
#: re-score runs these again. Each other check keeps its stored value,
#: because a results file keeps no tool arguments, cards or gateway calls.
RESCORED = frozenset({
    "answer_all", "answer_some", "no_name_claim", "no_reference_subject",
    "admits_unreadable", "no_content_claim", "task_no_guess",
})


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
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--scenarios", default="", help="a comma list of scenario ids")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--first-repeat", type=int, default=1,
                        help="live: the number of the first repeat, for a run in chunks")
    parser.add_argument("--spent-before", type=float, default=0.0,
                        help="live: USD that earlier chunks spent, counted against the budget")
    parser.add_argument("--tier", default="tier-balanced", help="the orchestrator's tier")
    parser.add_argument("--budget-usd", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=300.0, help="live: seconds a turn")
    parser.add_argument("--out", default=str(HERE / "results.json"))
    args = parser.parse_args(argv)
    # An answer can hold a rupee sign, and a Windows console is cp1252.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

    arms = tuple(a for a in args.arms.split(",") if a in ARMS)
    wanted = {s.strip() for s in args.scenarios.split(",") if s.strip()}
    scenarios = tuple(s for s in SCENARIOS if not wanted or s.id in wanted)
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
    ok, why = _preflight(router, args.tier)
    print(why)
    if not ok:
        return EXIT_NO_GO
    data = run_live(router, scenarios, arms, repeat=max(1, args.repeat), tier=args.tier,
                    budget=args.budget_usd, out=out, timeout=args.timeout,
                    first_repeat=args.first_repeat, spent_before=args.spent_before)
    print(report(data))
    print(f"results: {out}")
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
