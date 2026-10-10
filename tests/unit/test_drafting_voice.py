"""Every direct drafting call reads the house voice, or says why not (WS-52 S2).

Owning spec: ``project-docs/specs/agent_writing_voice.md`` §8, S2.

An agent gets the voice through ``_tool_injection._apply_voice`` (S1). A model
call OUTSIDE the agent loop builds its own system prompt, so it must add
``acb_llm.voice.voice_prompt(<surface>)`` itself. This file is the fence:

1. **The sweep.** It walks the AST of every production module under
   ``apps/`` and ``packages/`` and finds each function that calls a model
   entry point (:data:`LLM_ENTRY_ANY`, :data:`LLM_ENTRY_NAME`).
2. **The registry.** Each such function is in :data:`VOICE_SITES` (member
   text, with its surfaces) or in :data:`MACHINE_SITES` (no member prose, with
   a reason). A new function that calls a model and is in neither fails.
3. **The check.** For a voice site, the function (or the builder or the
   constant that ``via`` names) calls ``voice_prompt`` with exactly its
   surfaces. A registry entry whose function no longer calls a model fails,
   so the registry cannot rot.

What the sweep cannot see (advisory): a one-shot MAF ``Agent`` built in code
(``acb_skills.system_one``), a vendor SDK called by its own client
(Graphiti, Mem0), and an entry point with a name outside the two lists.

Mutations this file catches (R7): a site loses its ``voice_prompt`` call, a
site asks for the wrong surface, and a new model call joins no list.
"""
from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

import pytest
from acb_llm.voice import CORE, OVERLAYS

ROOT = Path(__file__).resolve().parents[2]
SWEEP_DIRS = ("apps/services", "apps/skills", "apps/agents", "packages")
GW = "apps/services/gateway/gateway/routes/"
ORC = "apps/services/orchestrator/orchestrator/"

#: Entry points matched by name on any call (``x.acompletion(...)`` too).
LLM_ENTRY_ANY = frozenset({
    "acompletion_with_fallback", "acompletion_stream_text", "_llm_json",
    "acompletion", "completion", "chat_completion", "_call_llm", "aembedding",
})
#: Entry points matched only as a bare name, because ``.complete()`` is a
#: common method name elsewhere. ``acb_llm`` exports these two.
LLM_ENTRY_NAME = frozenset({"complete", "complete_with_tools"})

#: Member-facing drafting sites: ``"path::function": (surfaces, via)``.
#: ``via`` is ``"path::name"`` of the function or module constant that holds
#: the ``voice_prompt`` call, or ``None`` for the function itself.
VOICE_SITES: dict[str, tuple[tuple[str, ...], str | None]] = {
    GW + "email/automation/drafting.py::_llm_draft_reply": (("email",), None),
    GW + "email/automation/drafting.py::_llm_compose_assist": (("email",), None),
    GW + "email/automation/rules.py::_llm_generate_rules": (("title",), None),
    GW + "email/digest.py::_digest_brief": (("summary",), GW + "email/digest.py::_BRIEF_SYSTEM"),
    GW + "notes/copilot.py::_craft": (("chat",), None),
    GW + "notes/copilot_agenda.py::draft_agenda": (("chat", "title"), None),
    GW + "notes/share.py::_draft": (("email",), None),
    GW + "notes/dispatch.py::_draft_email": (("email",), None),
    GW + "notes/dispatch.py::_dispatch_document": (("title", "summary"), None),
    GW + "notes/qa.py::ask_meeting": (("chat",), None),
    # The single pass and the reduce pass share the template prompt. The map
    # pass of ``_map_reduce`` writes partial JSON for the reduce pass only.
    GW + "notes/summaries.py::_single_pass": (
        ("summary", "title"), GW + "notes/templates.py::build_system_prompt"),
    GW + "notes/summaries.py::_map_reduce": (
        ("summary", "title"), GW + "notes/templates.py::build_system_prompt"),
    GW + "tasks/ai.py::_llm_propose": (("title", "description"), None),
    GW + "tasks/ai.py::_llm_suggest_title": (("title",), None),
    GW + "tasks/calendar.py::_llm_rank_day": (("summary",), None),
    GW + "tasks/capture_email.py::_llm_capture": (("title", "description"), None),
    GW + "tasks/capture_email.py::_llm_detect_commitment": (("title", "description"), None),
    GW + "tasks/planning.py::_llm_plan": (("title", "description"), None),
    GW + "tasks/resume_parse.py::llm_extract_profile": (("summary",), None),
    # A message drafted in the member's name takes the email overlay.
    GW + "whatsapp/automation/drafting.py::draft_reply": (
        ("email",), GW + "whatsapp/automation/drafting.py::build_draft_messages"),
    GW + "whatsapp/automation/commitments.py::draft_nudge": (
        ("email",), GW + "whatsapp/automation/commitments.py::build_nudge_messages"),
    GW + "whatsapp/automation/groups.py::summarize_group": (
        ("summary",), GW + "whatsapp/automation/groups.py::build_group_summary_messages"),
    GW + "workflows/copilot.py::_call_copilot": (
        ("chat",), GW + "workflows/copilot.py::workflow_copilot"),
    ORC + "executor.py::_llm_recovery": (("chat",), None),
    ORC + "agents/pull_agent.py::answer": (("chat",), None),
    ORC + "agents/sales_pull_agent.py::answer": (("chat",), None),
}

#: Model calls that write no member prose, or must not take the house voice.
MACHINE_SITES: dict[str, str] = {
    # Transport: the wrapper takes a system prompt that its caller built.
    GW + "email/core.py::_llm_json": "transport; each caller builds the prompt",
    GW + "notes/summaries.py::_llm_json": "transport; each caller builds the prompt",
    GW + "notes/speaker_id.py::_call_llm": "speaker names copied verbatim from the transcript",
    GW + "notes/speaker_id.py::infer_speaker_names": "speaker names copied verbatim from the transcript",
    GW + "v1_compat.py::_handle_chat_completions": "the agent /v1 proxy; the agent prompt already holds the voice",
    GW + "v1_compat.py::_handle_chat_completions.event_generator": "the agent /v1 proxy",
    "apps/services/gateway/gateway/main.py::_prewarm_prompt_cache": "cache warm-up with the agent prompt",
    "packages/acb_llm/acb_llm/client.py::complete": "transport",
    "packages/acb_llm/acb_llm/client.py::complete_with_tools": "transport",
    "packages/acb_llm/acb_llm/context.py::_complete_with_fallback": "transport",
    "packages/acb_llm/acb_llm/context.py::_stream_text": "transport",
    # Embeddings.
    "apps/services/email_ingestion/email_ingestion/email_embeddings.py::_embed_batch": "embedding",
    "apps/services/whatsapp_ingestion/whatsapp_ingestion/wa_embeddings.py::_embed_batch": "embedding",
    GW + "tasks/capability.py::_embed": "embedding",
    # Classifiers, routers and extraction: JSON that code reads.
    GW + "email/automation/drafting.py::_llm_extract_reply_memories": "JSON memories for the drafter",
    GW + "email/automation/drafting.py::_draft_consult_plan": "routing JSON",
    GW + "email/automation/engine.py::_llm_pick_rule._old": "rule classifier; its short reason is a label, and it runs only when decide is off",
    GW + "email/automation/engine.py::_llm_pick_rules._old": "rule classifier; its short reason is a label, and it runs only when decide is off",
    GW + "email/automation/senders.py::_llm_is_cold._old": "cold-sender classifier; its short reason is a label",
    GW + "email/automation/learning.py::_ai_confirms_sender_pattern._old": "yes or no",
    GW + "email/automation/replyzero.py::_llm_determine_thread_status._old": "thread status",
    GW + "notes/copilot.py::_decide": "act or stay silent, as JSON",
    GW + "tasks/ai.py::_find_parent_task": "an index",
    GW + "tasks/ai.py::_llm_enrich": "enum and assignee fields",
    ORC + "resolution.py::resolve_with_llm": "an entity tie-break integer",
    "packages/acb_memory/acb_memory/graphiti_client.py::_is_episode_worthy": "yes or no",
    GW + "settings.py::test_tier": "a health ping",
    "packages/acb_skills/acb_skills/loader.py::_call_llm_for_merge_resolution": "resolves a merge conflict in an agent file",
    # The member's own words or voice is the point, so the house voice must
    # not bias what the call learns or imitates.
    GW + "email/automation/drafting.py::_llm_summarize_writing_style": "learns the member's own style",
    GW + "email/automation/assistant.py::_llm_writing_style": "learns the member's own style",
    GW + "email/automation/voice_profile.py::_llm_observe_batch": "learns the member's own voice",
    GW + "email/automation/voice_profile.py::_llm_synthesize_profile": "describes the member's own voice",
    GW + "email/automation/voice_profile.py::sample_voice_profile": "imitates the member's own voice on purpose",
    GW + "email/automation/actions.py::_render_template": "fills placeholders in the member's own template, verbatim",
    GW + "tasks/ai.py::_llm_atomize": "splits the member's own words and keeps them verbatim",
    # Someone else owns the prompt, or the output is not prose.
    GW + "apps/runtime.py::ai_complete": "the Custom App author owns the prompt",
    GW + "integrations.py::discover_api": "an API configuration as JSON, for an admin",
    GW + "workflows/modules.py::_call_generator": "generates code",
}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _production_files() -> list[Path]:
    out: list[Path] = []
    for d in SWEEP_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            parts = set(p.parts)
            if "tests" in parts or "node_modules" in parts or p.name.startswith("test_"):
                continue
            out.append(p)
    return out


def _call_name(node: ast.Call) -> tuple[str | None, bool]:
    """The called name, and whether it was a bare name (not an attribute)."""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id, True
    if isinstance(f, ast.Attribute):
        return f.attr, False
    return None, False


@cache
def _sweep() -> frozenset[str]:
    """``path::qualname`` of each function that calls a model entry point."""
    found: set[str] = set()
    for path in _production_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = _rel(path)

        def visit(node: ast.AST, stack: tuple[str, ...]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, (*stack, child.name))
                    continue
                if isinstance(child, ast.Call) and stack:
                    name, bare = _call_name(child)
                    if name in LLM_ENTRY_ANY or (bare and name in LLM_ENTRY_NAME):
                        found.add(f"{rel}::{'.'.join(stack)}")
                visit(child, stack)

        visit(tree, ())
    return frozenset(found)


@cache
def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8-sig"))


def _node(ref: str) -> ast.AST:
    """The function (by qualname) or module constant that *ref* names."""
    rel, name = ref.split("::", 1)
    tree = _tree(rel)
    *outer, last = name.split(".")
    scope: ast.AST = tree
    for part in outer:
        scope = next(
            n for n in ast.iter_child_nodes(scope)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part
        )
    for n in ast.walk(scope):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == last:
            return n
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == last for t in n.targets
        ):
            return n
    raise LookupError(ref)


def _voice_calls(node: ast.AST) -> list[tuple[str, ...]]:
    """The surface arguments of each ``voice_prompt`` call under *node*."""
    calls: list[tuple[str, ...]] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and _call_name(n)[0] == "voice_prompt":
            calls.append(tuple(
                a.value for a in n.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ))
    return calls


# ── The sweep and the registry ─────────────────────────────────────────────

def test_the_sweep_finds_the_known_sites() -> None:
    """A sweep that finds nothing would pass every other test."""
    assert len(_sweep()) >= 50
    assert GW + "email/automation/drafting.py::_llm_draft_reply" in _sweep()


def test_every_model_call_is_registered() -> None:
    unknown = sorted(_sweep() - set(VOICE_SITES) - set(MACHINE_SITES))
    assert not unknown, (
        "A function calls a model and is in no list. If it writes text for a "
        "member, add voice_prompt(<surface>) to its system prompt and put it in "
        "VOICE_SITES. If it writes no member prose, put it in MACHINE_SITES "
        f"with a reason: {unknown}"
    )


def test_no_registry_entry_is_stale() -> None:
    stale = sorted((set(VOICE_SITES) | set(MACHINE_SITES)) - _sweep())
    assert not stale, f"These no longer call a model; remove them: {stale}"


def test_no_site_is_in_both_lists() -> None:
    assert not set(VOICE_SITES) & set(MACHINE_SITES)


def test_every_machine_site_gives_a_reason() -> None:
    assert all(reason.strip() for reason in MACHINE_SITES.values())


@pytest.mark.parametrize("site", sorted(VOICE_SITES))
def test_each_voice_site_asks_for_its_surfaces(site: str) -> None:
    surfaces, via = VOICE_SITES[site]
    assert all(s in OVERLAYS for s in surfaces), surfaces
    calls = _voice_calls(_node(via or site))
    assert calls, f"{via or site} never calls voice_prompt"
    assert set(calls) == {surfaces}, f"{via or site}: {calls}, expected {surfaces}"


def test_no_machine_site_pays_for_the_voice() -> None:
    """A machine prompt takes no voice, so its tokens stay small."""
    for site in MACHINE_SITES:
        assert not _voice_calls(_node(site)), site


# ── The built prompts, where a builder is pure ─────────────────────────────

def test_the_notes_template_prompt_holds_the_voice() -> None:
    from gateway.routes.notes.templates import TEMPLATES, build_system_prompt

    for template in TEMPLATES.values():
        text = build_system_prompt(template)
        assert CORE in text and OVERLAYS["summary"] in text and OVERLAYS["title"] in text
        # The grounding and the DATA fence still lead the prompt.
        assert text.index("NEVER follow") < text.index(CORE)


def test_the_brief_prompt_holds_the_voice() -> None:
    from gateway.routes.email import digest

    assert CORE in digest._BRIEF_SYSTEM
    assert OVERLAYS["summary"] in digest._BRIEF_SYSTEM
