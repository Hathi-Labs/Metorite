"""Skills as an INDEX, bodies on demand — WS-23 successor (QM-2).

The core-floor diet the WS-23 measurement made necessary
(``specs/skills_scope_out.md`` §5: family toggles bottom out at ≈15.4k tokens
of tool context per run; the target is ≤2k). ``§6`` names the mechanism and
``specs/multiplayer_prior_art_qm_2026-08.md`` §QM-2 records the external
reference implementation. This module is our own build of the same four ideas:

1. **Index, not body.** The system prompt carries ONE LINE PER FAMILY — label,
   a one-sentence ``summary`` from ``SKILL_FAMILIES``, and the exact tool call
   that returns the full guidance. The bodies never enter the prompt.
2. **Bodies where the agent can already read them.** Each family's body is
   written to ``agent-data/skills/<slug>.md`` in the agent's workspace — the
   durable, blob-store-backed folder ``save_note``/``recall_notes`` already
   own — and read back with ``recall_notes("skills/<slug>.md")``, which is a
   CORE-FLOOR tool every agent has by rule 2. No new tool, no new schema, no
   invented filesystem layout. Writes are content-hash idempotent: an
   unchanged body is not rewritten.
3. **Gating by relevance.** A family whose tools are not in this agent's
   effective scope is absent from the index entirely rather than listed and
   then unusable — the same instinct as the S2 intersection, applied to
   relevance. (Our equivalent of qm's connector gating.)
4. **Cache placement.** The index is rendered by the same
   ``render_injected_tools_addendum`` seam the classic addendum used, so it
   lands in exactly the same place: appended to the system message by
   ``_inject_agent_tools`` BEFORE the executor inserts
   ``acb_llm.prompt_cache.CACHE_BREAK`` at the memory boundary — i.e. inside
   the cache-stable prefix. It is derived only from static registries + the
   process-cached agent-registry block, so it is byte-stable across turns.

**Ships OFF.** ``SKILLS_INDEX_ONLY`` (same shape as ``SKILLS_FAIL_CLOSED``)
defaults off; with it off every byte of the addendum, and every injected tool,
is exactly what shipped. Flipping it changes what EVERY agent sees in its
system prompt and is an OWNER-GATE (``work_plan.md`` §6).

**Byte preservation.** Bodies are not a second copy of the guidance: both the
classic addendum and the bodies come from
``acb_skills.addendum.rendered_parts`` — the addendum is those pieces joined,
a body is the same pieces filtered to one family. Content is unchanged; only
its delivery is deferred.

This module is pure data + text + one filesystem write: it never imports the
orchestrator or gateway (packages sit below apps in the import graph).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from acb_skills.addendum import rendered_parts
from acb_skills.skill_families import SKILL_FAMILIES

# ---------------------------------------------------------------------------
# The switch (mirrors orchestrator._tool_injection._skills_fail_closed)
# ---------------------------------------------------------------------------

#: Env var name — kept as a constant so tests and the spec cite one spelling.
SKILLS_INDEX_ONLY_ENV = "SKILLS_INDEX_ONLY"

_TRUTHY = ("1", "true", "yes", "on")


def skills_index_only() -> bool:
    """The OWNER-GATED index-mode switch — ships OFF.

    OFF (default): ``render_injected_tools_addendum`` returns the full
    generated addendum, byte-identical to what shipped with S3.
    ON: it returns :func:`render_skill_index` and the bodies are materialized
    into the workspace for :func:`body_read_call` to fetch.

    Read per call (never cached at import) so tests can cover both positions
    without a process restart. NOTE for callers that cache the rendered
    addendum — ``_build_injected_tools_addendum`` keeps an ``lru_cache`` —
    flipping this mid-process requires a ``cache_clear()``.
    """
    return os.environ.get(SKILLS_INDEX_ONLY_ENV, "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Where a body lives, and the call that reads it
# ---------------------------------------------------------------------------

#: Sub-folder of ``agent-data/`` holding the materialized bodies.
#: ``agent-data`` is one of the three blob-store-backed visible folders
#: (``acb_memory.blob_store.STORE_FOLDERS``) — the existing workspace convention
#: rather than a new layout.
BODY_SUBDIR = "skills"


def body_read_argument(slug: str) -> str:
    """The ``path`` argument ``recall_notes`` takes for a family's body.

    ``recall_notes`` prefixes a bare path with ``agent-data/`` itself, so the
    agent-facing argument stays short (``"skills/core.md"``).
    """
    return f"{BODY_SUBDIR}/{slug}.md"


def body_workspace_path(slug: str) -> str:
    """Workspace-relative path of a family's body file."""
    return f"agent-data/{BODY_SUBDIR}/{slug}.md"


def body_read_call(slug: str) -> str:
    """The literal tool call to put in the index line (not a bare path)."""
    return f'recall_notes("{body_read_argument(slug)}")'


# ---------------------------------------------------------------------------
# The bodies (the SAME text the classic addendum renders)
# ---------------------------------------------------------------------------

def family_bodies(
    *,
    effective_scope: frozenset[str] | None = None,
    registry_block: str = "",
    risk_block: str | None = None,
) -> dict[str, str]:
    """Family slug → the full guidance text for that family.

    The pieces come from :func:`acb_skills.addendum.rendered_parts` unchanged
    and are regrouped by their family tag, so ``"\\n".join`` of every body's
    pieces is exactly the classic addendum's pieces — same text, different
    delivery. Only families with prose appear (``workflows``/``apps`` have
    none today; their tools carry their own docstrings).

    Bodies are always rendered from the FULL section registry, never the
    compact sub-agent one: a body costs nothing until it is read, so a
    sub-agent that does read one should get the real guidance. The INDEX is
    what differs between the two variants.
    """
    grouped: dict[str, list[str]] = {}
    for family, text in rendered_parts(
        is_sub_agent=False,
        effective_scope=effective_scope,
        registry_block=registry_block,
        risk_block=risk_block,
    ):
        grouped.setdefault(family, []).append(text)
    return {slug: "\n".join(parts) for slug, parts in grouped.items()}


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

_HEADER = (
    "\n---\n"
    "## Metorite Platform Tools (injected at runtime)\n"
    "Your platform tool families are indexed below. This prompt carries only "
    "the index — each family's FULL usage guidance (argument shapes, "
    "templates, conventions, worked patterns) lives in a file in your "
    "workspace. Before the first time you use a family's tools in this "
    "session, read its guide with the call shown; it is cheap, it is the "
    "authoritative spec, and guessing an argument shape without it wastes a "
    "turn."
)

_COMPACT_HEADER = (
    "\n---\n"
    "## Metorite Platform Tools\n"
    "Index only — read a family's guide before first using its tools."
)


def _indexed_families(
    effective_scope: frozenset[str] | None,
) -> list[str]:
    """Family slugs to list, in registry order.

    Relevance gating (QM-2 part 3): a scope-governed family whose tools the
    agent did not receive is omitted rather than advertised — describing a
    tool the agent does not carry is the misinformed-model bug
    (multi_agent_orchestration Phase 0.2) that the classic addendum's per-tool
    gates already avoid.
    """
    return [
        slug for slug, fam in SKILL_FAMILIES.items()
        if not fam.get("scope_governed")
        or effective_scope is None
        or (effective_scope & frozenset(fam["tools"]))
    ]


def _has_body(slug: str, bodies: dict[str, str]) -> bool:
    return bool(bodies.get(slug, "").strip())


def render_skill_index(
    *,
    is_sub_agent: bool = False,
    effective_scope: frozenset[str] | None = None,
) -> str:
    """One line per family + the MANDATORY contract, and nothing else.

    Line shape::

        - **Memory & knowledge graph** — <one sentence>. Full guide:
          `recall_notes("skills/memory.md")`

    A family with no body file (``workflows``, ``apps`` — never had addendum
    prose) is still listed, with its tools' own docstrings named as the
    reference, so the index never points at a file that does not exist.

    The MANDATORY block stays INLINE deliberately. It is the one piece of the
    addendum that must be obeyed without being asked for (call
    ``manage_todo_list``/``ask_user``/``run_diagnostics``/``save_note`` rather
    than improvising), so deferring it behind a read would trade ~100 tokens
    for a behaviour regression. Everything else defers.
    """
    from acb_skills.addendum import _mandatory_block  # noqa: PLC0415

    bodies = family_bodies(effective_scope=effective_scope)
    lines: list[str] = [_COMPACT_HEADER if is_sub_agent else _HEADER]
    for slug in _indexed_families(effective_scope):
        fam = SKILL_FAMILIES[slug]
        summary = str(
            fam.get("summary") or fam.get("description") or ""
        ).strip()
        if _has_body(slug, bodies):
            pointer = f"Full guide: `{body_read_call(slug)}`"
        else:
            pointer = "Full guide: the tools' own descriptions."
        lines.append(f"- **{fam['label']}** — {summary} {pointer}")
    mandatory = _mandatory_block(effective_scope)
    if mandatory:
        lines.append("")
        lines.append(mandatory)
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Materialization (content-hash idempotent)
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def materialize_skill_bodies(
    workspace_root: str | Path,
    *,
    effective_scope: frozenset[str] | None = None,
    registry_block: str = "",
    risk_block: str | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Write each family's body under ``agent-data/skills/``; return a report.

    Returns ``{slug: "written" | "unchanged"}``. No-op (empty dict) when the
    switch is OFF unless *force* is set — the OFF path must not touch an
    agent's workspace at all.

    **Idempotence** is by content hash: an existing file whose bytes already
    equal the freshly rendered body is left alone, so a long-lived workspace
    is not rewritten on every run (and its mtime stays meaningful).

    **Not mirrored to the blob store, deliberately.** Bodies are derived data
    — regenerated deterministically from ``acb_skills.addendum`` on the next
    run — unlike ``NOTES.md``, which is accumulated state and must survive a
    wiped volume. Pushing derived text into the authoritative store would let
    a stale rehydrate overwrite a fresh body.

    Best-effort: a filesystem failure returns what succeeded rather than
    raising, exactly like the rest of the injection path.
    """
    if not (force or skills_index_only()):
        return {}
    root = Path(workspace_root)
    out: dict[str, str] = {}
    bodies = family_bodies(
        effective_scope=effective_scope,
        registry_block=registry_block,
        risk_block=risk_block,
    )
    for slug, text in bodies.items():
        if not text.strip():
            continue
        rel = body_workspace_path(slug)
        try:
            from acb_skills.write_artifact import (  # noqa: PLC0415
                resolve_in_workspace,
            )
            target = resolve_in_workspace(root, rel)
            if target is None:  # pragma: no cover — rel is a literal constant
                continue
            fresh = _sha256(text)
            if (
                target.exists()
                and _sha256(target.read_text(encoding="utf-8")) == fresh
            ):
                out[slug] = "unchanged"
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            out[slug] = "written"
        except Exception:  # noqa: BLE001 — never block a run over a body file
            continue
    return out
