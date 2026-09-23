"""Skill-family registry — WS-23 S1 (specs/skills_registry.md §3, §4).

``SKILL_FAMILIES`` is the single code-declared mapping of family slug →
{label, description, tool names} covering EVERY platform tool the injection
seam (``orchestrator._tool_injection``) offers an agent. One registry, three
consumers (per the spec's single-source rule): the read-only catalog API
(``GET /integrations/skills``), the S2 toggle enforcement (future), and the
S3 generated addendum (future). ``tests/unit/test_skills_registry.py`` is the
drift gate: it fails CI if a tool is injected but missing here, appears in two
families, or is registered here but no longer injectable.

Family-set reality check (S1 acceptance says follow the CODE, not the spec's
provisional list): the spec named files-artifacts / email / whatsapp /
tasks-gtd / calendar / notes / web-research / diagrams families, but

* files-artifacts, notes, web-research and most of coding are inside the
  guaranteed ``_CORE_STANDARD_TOOL_NAMES`` floor (which grew after the spec's
  list was drafted) — they are the non-toggleable ``core`` family;
* email / whatsapp / tasks-gtd / calendar tools are agents' OWN repo-baked
  tools (governed by ``own_tool_scope``), never injected by
  ``_tool_injection.py`` — so they have no place in an *injected*-skills
  registry until the WS-8 manifest work lands;
* ``diagrams`` was explicitly "(future)" in the spec and stays out.

What remains outside the core floor: memory, history, coding extras,
workflows, and the dynamic Custom-App action tools.

This module is pure data + dependency-injected helpers: it never imports the
orchestrator or gateway (packages sit below apps in the import graph). The
gateway route wires in the real addendum renderer, tool callables, and the
``assemble_run_context`` tokenizer (``acb_llm.context.count_message_tokens``).
"""
from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, Callable, Mapping

# ---------------------------------------------------------------------------
# The registry (code-declared data; keep alphabetised inside each family)
# ---------------------------------------------------------------------------
# ``scope_governed`` — whether a ``config.json: tool_scope`` narrows this
# family today. The core floor is always injected (never narrowable), and the
# workflow trio + granted-app tools are added AFTER scope filtering in
# ``_inject_agent_tools`` (deliberately: a grant is a per-agent permission,
# not a platform tool a static scope names).
# ``dynamic`` — tools exist only at run time (per-grant ``app_<slug>_<action>``
# functions); the static ``tools`` tuple is empty and drift-checking skips it.

SKILL_FAMILIES: dict[str, dict[str, Any]] = {
    "core": {
        "label": "Core floor",
        # ``summary`` — ONE sentence, the line the QM-2 skill INDEX carries in
        # the prompt when ``SKILLS_INDEX_ONLY`` is on (acb_skills.skill_index).
        # It must be enough to decide "do I need this family right now?"; the
        # full guidance is the on-demand body. ``description`` stays the
        # longer admin-facing catalog text.
        "summary": (
            "Web access, file writing and rich generative UI, todo tracking, "
            "asking the user a question, diagnostics, working notes, the "
            "coding skill, and delegation to other agents."
        ),
        "description": (
            "The guaranteed baseline every agent receives regardless of "
            "tool_scope: web access, file writing & generative UI, task "
            "tracking, HITL questions, diagnostics, working notes, the "
            "coding skill, and inter-agent delegation. Not toggleable "
            "(skills_registry.md rule 2)."
        ),
        "tools": (
            "ask_questions",
            "call_agent",
            "call_agent_background",
            "call_agents_parallel",
            "code_task",
            "emit_generative_ui",
            "fetch_page",
            "get_errors",
            "list_integrations",
            "load_artifact_kit",
            "load_design_system",
            "manage_todo_list",
            "recall_notes",
            "run_diagnostics",
            "run_script",
            "save_note",
            "share_artifact",
            "web_search",
            "write_artifact",
        ),
        "core": True,
        "scope_governed": False,
        "dynamic": False,
    },
    "memory": {
        "label": "Memory & knowledge graph",
        "summary": (
            "Read and write durable memory in three scopes (this user, this "
            "agent, the whole organisation) plus timeline recall and episode "
            "capture."
        ),
        "description": (
            "Episodic + bi-temporal memory across the three Mem0 scopes: "
            "this user's private memory, the agent's shared memory, and "
            "organisation-wide memory, plus timeline recall and episode "
            "capture."
        ),
        "tools": (
            "recall_agent",
            "recall_org",
            "recall_timeline",
            "remember",
            "save_agent_memory",
            "save_episode",
            "save_memory",
            "save_org_memory",
        ),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "history": {
        "label": "Conversation history",
        "summary": (
            "Recall what was discussed in earlier sessions by searching the "
            "chat history."
        ),
        "description": (
            "Search past conversations by text, thread, agent or recency — "
            "recall what was discussed in prior sessions."
        ),
        "tools": ("query_history",),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "coding": {
        "label": "Coding extras",
        "summary": (
            "Install a Python package into the agent runtime mid-task, and "
            "search public GitHub repositories for code."
        ),
        "description": (
            "Additions beyond the core coding floor (code_task/run_script "
            "are core): runtime Python package install and GitHub code "
            "search."
        ),
        "tools": ("github_repo_search", "github_search", "install_dependency"),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "workflows": {
        "label": "Workflows",
        "summary": (
            "List the organisation's published workflows, start a run, and "
            "check a run's status."
        ),
        "description": (
            "Run governed automations from the Workflows app: list published "
            "workflows, start a run, check a run's status. Injected for "
            "every agent regardless of tool_scope (writes inside a workflow "
            "are already broker-gated)."
        ),
        "tools": ("get_workflow_run", "list_workflows", "run_workflow"),
        "core": False,
        "scope_governed": False,
        "dynamic": False,
    },
    "apps": {
        "label": "Custom Apps",
        "summary": (
            "Actions of the Custom Apps granted to you, one tool per declared "
            "manifest action (app_<slug>_<action>)."
        ),
        "description": (
            "Actions of Custom Apps granted to an agent (app_grants), "
            "injected as one tool per declared manifest action "
            "(app_<slug>_<action>). Per-grant and resolved at run time — "
            "no static tool list."
        ),
        "tools": (),
        "core": False,
        "scope_governed": False,
        "dynamic": True,
        "tool_pattern": "app_<slug>_<action>",
    },
}


# ---------------------------------------------------------------------------
# The named default profile (WS-23 S3 — prepared, NOT active)
# ---------------------------------------------------------------------------
# The GENERAL skill set an agent without a ``config.json: tool_scope`` receives
# WHEN the owner-gated fail-closed flip is ON (``SKILLS_FAIL_CLOSED`` in
# ``orchestrator._tool_injection`` — ships OFF; flipping it is an OWNER
# decision, work_plan.md §6). Membership is evidence-based, from the WS-23
# scope-out (specs/skills_scope_out.md):
#
# * ``core``      — the guaranteed floor; automatic, listed for completeness.
# * ``memory``    — the one toggleable family that is genuinely general: 5 of
#   the 6 registered agents declare memory tools in their tool_scope, and the
#   orchestrator/email-assistant prompts instruct calling them; cross-session
#   personalisation is platform-wide behaviour, not a specialty.
# * ``workflows`` — injected scope-independently by design (spec §2 decision
#   note: org-governed automations offered to every agent); listed so the
#   profile documents the real default surface.
# * ``apps``      — dynamic, governed by app_grants (its own surface), never
#   by profiles; listed so "what does a default agent get" reads complete.
#
# Deliberately EXCLUDED (specialised — per-agent tool_scope/toggle only):
# ``history`` (only the orchestrator declares query_history) and ``coding``
# extras (only apis-config declares install_dependency; nobody declares the
# GitHub search pair).

DEFAULT_PROFILE: tuple[str, ...] = ("core", "memory", "workflows", "apps")


def default_profile_tools() -> frozenset[str]:
    """Static tool names of the DEFAULT_PROFILE families.

    What the fail-closed resolve unions with the core floor for an unscoped
    agent. Dynamic families (``apps``) contribute nothing here — grants are
    appended at their own site; the ``workflows`` trio is likewise appended
    per-agent after scope filtering, so listing it here is documentation, not
    the mechanism that injects it.
    """
    return frozenset(
        name
        for slug in DEFAULT_PROFILE
        for name in SKILL_FAMILIES[slug]["tools"]
    )


# ---------------------------------------------------------------------------
# Pure lookups
# ---------------------------------------------------------------------------

def family_of(tool_name: str) -> str | None:
    """Family slug a statically-registered tool belongs to (or None)."""
    for slug, fam in SKILL_FAMILIES.items():
        if tool_name in fam["tools"]:
            return slug
    return None


def all_registered_tool_names() -> frozenset[str]:
    """Every statically-declared tool name across all families."""
    return frozenset(
        name for fam in SKILL_FAMILIES.values() for name in fam["tools"]
    )


def registry_content_hash() -> str:
    """Deterministic registry-content hash (the token-cost cache key)."""
    canon = json.dumps(
        {slug: {**fam, "tools": list(fam["tools"])}
         for slug, fam in SKILL_FAMILIES.items()},
        sort_keys=True, ensure_ascii=True,
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Token-cost measurement (measured, not estimated — spec §3)
# ---------------------------------------------------------------------------

def tool_json_schema(fn: Any) -> dict[str, Any]:
    """Derive the OpenAI-style function schema an LLM runtime would see.

    Both runtimes build their tool schema from the callable's signature +
    docstring, so rendering the same shape here prices what the model context
    actually pays per tool.
    """
    name = getattr(fn, "__name__", None) or getattr(fn, "name", str(fn))
    doc = inspect.getdoc(fn) or ""
    props: dict[str, Any] = {}
    required: list[str] = []
    try:
        sig = inspect.signature(fn)
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls") or param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            props[pname] = {"type": _schema_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
    except (TypeError, ValueError):
        pass
    return {
        "type": "function",
        "function": {
            "name": str(name),
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


def _schema_type(annotation: Any) -> str:
    txt = str(annotation)
    if annotation is inspect.Parameter.empty:
        return "string"
    if "int" in txt:
        return "integer"
    if "float" in txt:
        return "number"
    if "bool" in txt:
        return "boolean"
    if "list" in txt or "List" in txt:
        return "array"
    if "dict" in txt or "Dict" in txt:
        return "object"
    return "string"


# In-process cost cache: (family slug | "__total__", registry hash) → tokens.
# NOTE: the key deliberately does NOT include the injected dependencies — in a
# gateway process they are process-stable. Tests that swap fakes in must call
# :func:`clear_cost_cache` first.
_COST_CACHE: dict[tuple[str, str], int] = {}


def clear_cost_cache() -> None:
    """Reset the in-process token-cost cache (tests / registry hot-reload)."""
    _COST_CACHE.clear()


def _schema_tokens(
    names: tuple[str, ...] | frozenset[str],
    tools_by_name: Mapping[str, Any],
    count_tokens: Callable[[str], int],
) -> int:
    total = 0
    for n in sorted(names):
        fn = tools_by_name.get(n)
        if fn is not None:
            total += count_tokens(json.dumps(tool_json_schema(fn)))
    return total


def build_catalog(
    *,
    tools_by_name: Mapping[str, Any],
    addendum_for_scope: Callable[[frozenset[str] | None], str],
    count_tokens: Callable[[str], int],
    core_tool_names: frozenset[str],
) -> dict[str, Any]:
    """The full catalog with per-family MEASURED token cost.

    Cost per family = the marginal system-prompt addendum the family adds on
    top of the core floor (rendered with the real addendum builder) + the JSON
    schemas of its tools, counted with the injected tokenizer (the same one
    ``assemble_run_context`` uses). Cached per (family, registry-content hash)
    in-process.

    Dependencies are parameters so this package never imports the orchestrator
    (the gateway route composes the real ones; tests inject fakes).
    """
    rhash = registry_content_hash()
    core_floor = frozenset(core_tool_names)
    baseline_key = ("__core_addendum__", rhash)
    if baseline_key not in _COST_CACHE:
        _COST_CACHE[baseline_key] = count_tokens(
            addendum_for_scope(core_floor)
        )
    baseline = _COST_CACHE[baseline_key]

    families: list[dict[str, Any]] = []
    for slug, fam in SKILL_FAMILIES.items():
        key = (slug, rhash)
        if key not in _COST_CACHE:
            if fam.get("dynamic"):
                cost = 0
            elif fam.get("core"):
                cost = baseline + _schema_tokens(
                    fam["tools"], tools_by_name, count_tokens,
                )
            else:
                with_fam = count_tokens(
                    addendum_for_scope(core_floor | frozenset(fam["tools"]))
                )
                cost = max(0, with_fam - baseline) + _schema_tokens(
                    fam["tools"], tools_by_name, count_tokens,
                )
            _COST_CACHE[key] = cost
        families.append({
            "slug": slug,
            "label": fam["label"],
            "description": fam["description"],
            "tools": list(fam["tools"]),
            "tool_count": len(fam["tools"]),
            "core": bool(fam.get("core")),
            "scope_governed": bool(fam.get("scope_governed")),
            "dynamic": bool(fam.get("dynamic")),
            **({"tool_pattern": fam["tool_pattern"]}
               if "tool_pattern" in fam else {}),
            "token_cost": _COST_CACHE[key],
        })

    total_key = ("__total__", rhash)
    if total_key not in _COST_CACHE:
        # The WS-12 baseline: the full unscoped addendum + every static schema.
        _COST_CACHE[total_key] = count_tokens(
            addendum_for_scope(None)
        ) + _schema_tokens(
            all_registered_tool_names(), tools_by_name, count_tokens,
        )
    return {
        "registry_hash": rhash,
        "families": families,
        "total_injected_tokens": _COST_CACHE[total_key],
    }


def families_for_scope(
    tool_scope: list[str] | None,
    *,
    resolved_scope: frozenset[str] | None,
) -> list[str]:
    """Which family slugs an agent's resolved injected scope intersects.

    ``resolved_scope`` is what ``_resolve_injected_scope`` returned (already
    core-floor-unioned); ``None`` means the agent declares no ``tool_scope``
    and — injection being fail-open today (spec rule 3) — receives every
    family. Families that are not scope-governed (core floor, workflows,
    apps) are always present.
    """
    del tool_scope  # reserved for S2 (declared-vs-effective display)
    out: list[str] = []
    for slug, fam in SKILL_FAMILIES.items():
        if not fam.get("scope_governed"):
            out.append(slug)
        elif resolved_scope is None:
            out.append(slug)
        elif resolved_scope & frozenset(fam["tools"]):
            out.append(slug)
    return out
