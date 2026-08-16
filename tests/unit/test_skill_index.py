"""WS-23 successor (QM-2) — skills as an index, bodies on demand.

The core-floor diet ``specs/skills_scope_out.md`` §5/§6 called for. Four
claims, all of them things a future edit could silently break:

1. **The switch ships OFF and OFF changes nothing.** No env var ⇒ the
   addendum is byte-identical to the generated S3 prose and the agent's
   workspace is never touched.
2. **ON, the prompt carries an INDEX and only an index** — one line per
   family, each naming the exact tool call that returns the body, plus the
   MANDATORY contract (deliberately pinned inline).
3. **The bodies are the SAME text, not a copy.** Every piece of a body comes
   from ``acb_skills.addendum.rendered_parts`` — so guidance content is
   unchanged and only its delivery is deferred — and it round-trips through
   the real ``recall_notes`` tool the index points at.
4. **Materialization is content-hash idempotent** — an unchanged body is not
   rewritten on the next run.

Conventions match ``test_generated_addendum.py``: import the SUT modules
directly, no live DB, env flipped per test with ``monkeypatch``.
"""
from __future__ import annotations

import pytest

import orchestrator._tool_injection as ti
from acb_skills import addendum as ad
from acb_skills import skill_families as sf
from acb_skills import skill_index as sx

MEMORY_TOOLS = frozenset(sf.SKILL_FAMILIES["memory"]["tools"])
CORE = frozenset(ti._CORE_STANDARD_TOOL_NAMES)

#: Headings that only exist in the per-tool BODY prose — their presence in a
#: rendered addendum means the bodies did not defer.
_BODY_ONLY_HEADINGS = (
    "### Web access",
    "### Workspace & file writing",
    "### Inter-agent delegation",
    "### Memory & knowledge graph",
    "### Conversation history",
    "### GitHub code search",
    "### Runtime dependencies",
    "### Coding skill",
)


@pytest.fixture()
def _index_off(monkeypatch):
    monkeypatch.delenv(sx.SKILLS_INDEX_ONLY_ENV, raising=False)
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


@pytest.fixture()
def _index_on(monkeypatch):
    monkeypatch.setenv(sx.SKILLS_INDEX_ONLY_ENV, "1")
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


# ── 1. The switch ships OFF; OFF is a no-op ────────────────────────────────

def test_switch_ships_off(monkeypatch) -> None:
    monkeypatch.delenv(sx.SKILLS_INDEX_ONLY_ENV, raising=False)
    assert sx.skills_index_only() is False
    assert ti._skills_index_only() is False
    for falsy in ("", "0", "false", "off", "no"):
        monkeypatch.setenv(sx.SKILLS_INDEX_ONLY_ENV, falsy)
        assert sx.skills_index_only() is False, falsy
    for truthy in ("1", "true", "YES", "On"):
        monkeypatch.setenv(sx.SKILLS_INDEX_ONLY_ENV, truthy)
        assert sx.skills_index_only() is True, truthy
        assert ti._skills_index_only() is True, truthy


def test_switch_off_addendum_is_byte_identical_to_the_generated_prose(
    _index_off,
) -> None:
    """The S3 guarantee is untouched while the switch is off: the addendum is
    exactly the section registries joined — same bytes, same order."""
    for is_sub in (False, True):
        for scope in (None, CORE, frozenset(CORE | MEMORY_TOOLS)):
            expected = "\n".join(
                text for _, text in ad.rendered_parts(
                    is_sub_agent=is_sub,
                    effective_scope=scope,
                    registry_block=ti._build_registry_block(),
                )
            )
            got = ti._build_injected_tools_addendum(
                is_sub_agent=is_sub, effective_scope=scope,
            )
            assert got == expected
            # …and it is the BODY prose, not an index.
            assert sx.body_read_call("core") not in got


def test_switch_off_renders_the_full_body_sections(_index_off) -> None:
    text = ti._build_injected_tools_addendum(is_sub_agent=False)
    for heading in _BODY_ONLY_HEADINGS:
        assert heading in text, f"{heading} vanished with the switch OFF"


def test_switch_off_writes_nothing_to_the_workspace(_index_off, tmp_path) -> None:
    assert sx.materialize_skill_bodies(tmp_path) == {}
    assert not (tmp_path / "agent-data").exists()
    assert ti.materialize_skill_bodies_for_agent(
        "fake-agent", str(tmp_path), tool_scope=None,
    ) == {}
    assert not (tmp_path / "agent-data").exists()


# ── 2. ON: an index, and only an index ─────────────────────────────────────

def test_switch_on_renders_the_index_not_the_bodies(_index_on) -> None:
    text = ti._build_injected_tools_addendum(is_sub_agent=False)
    for heading in _BODY_ONLY_HEADINGS:
        assert heading not in text, (
            f"{heading} is still inline — the body did not defer"
        )
    assert "## Metorite Platform Tools" in text
    for slug in ("core", "memory", "history", "coding"):
        assert sx.body_read_call(slug) in text


def test_index_has_one_line_per_family_with_summary_and_pointer(
    _index_on,
) -> None:
    text = sx.render_skill_index(effective_scope=None)
    lines = [ln for ln in text.splitlines() if ln.startswith("- **")]
    assert len(lines) == len(sf.SKILL_FAMILIES), (
        "an unscoped agent's index must list every family, once"
    )
    for fam in sf.SKILL_FAMILIES.values():
        line = next(ln for ln in lines if ln.startswith(f"- **{fam['label']}**"))
        assert str(fam["summary"]) in line
        assert "Full guide:" in line


def test_every_family_declares_a_one_sentence_summary() -> None:
    """Drift gate: the index line comes from the registry, so a new family
    without a ``summary`` would silently print its long catalog description."""
    for slug, fam in sf.SKILL_FAMILIES.items():
        summary = fam.get("summary")
        assert isinstance(summary, str) and summary.strip(), slug
        assert summary.count(".") <= 1, f"{slug}: summary is not one sentence"


def test_index_omits_a_family_the_agent_did_not_receive(_index_on) -> None:
    """Relevance gating (QM-2 part 3): never advertise a guide for tools the
    agent does not carry."""
    scope = frozenset(CORE | MEMORY_TOOLS)
    text = sx.render_skill_index(effective_scope=scope)
    assert sx.body_read_call("memory") in text
    assert sx.body_read_call("history") not in text
    assert sx.body_read_call("coding") not in text
    # …and the core floor always survives (rule 2).
    assert sx.body_read_call("core") in text


def test_index_keeps_the_mandatory_contract_inline(_index_on) -> None:
    """Deliberate exception: the MANDATORY block must be obeyed without being
    asked for, so it is the one piece that does NOT defer."""
    for is_sub in (False, True):
        text = ti._build_injected_tools_addendum(is_sub_agent=is_sub)
        assert "**MANDATORY" in text
        assert "manage_todo_list" in text and "ask_user" in text


def test_index_never_points_at_a_body_that_does_not_exist(_index_on) -> None:
    """workflows / apps have no addendum prose — the index must name their
    tools' own docstrings instead of a file nobody writes."""
    text = sx.render_skill_index(effective_scope=None)
    bodies = sx.family_bodies()
    for slug in sf.SKILL_FAMILIES:
        if bodies.get(slug, "").strip():
            assert sx.body_read_call(slug) in text
        else:
            assert sx.body_read_call(slug) not in text


# ── 3. Bodies are the SAME text (byte preservation) ────────────────────────

def test_body_text_equals_the_generated_sections() -> None:
    """A body is the addendum's own pieces filtered to one family — the
    guidance content is unchanged, only its delivery is deferred."""
    for scope in (None, frozenset(CORE | MEMORY_TOOLS)):
        parts = ad.rendered_parts(effective_scope=scope, registry_block="R")
        bodies = sx.family_bodies(effective_scope=scope, registry_block="R")
        for slug in bodies:
            expected = "\n".join(t for fam, t in parts if fam == slug)
            assert bodies[slug] == expected, slug


def test_bodies_partition_the_full_addendum_losslessly() -> None:
    """Every piece the classic addendum renders lands in exactly one body:
    nothing is dropped and nothing is invented."""
    parts = ad.rendered_parts(effective_scope=None, registry_block="R")
    bodies = sx.family_bodies(effective_scope=None, registry_block="R")
    # Nothing dropped: every rendered piece is inside its family's body.
    for family, text in parts:
        assert text in bodies[family], f"{family}: a section went missing"
    # Nothing invented: re-interleaving the bodies in registry order
    # reproduces exactly the pieces the classic addendum renders.
    per_family: dict[str, list[str]] = {}
    for family, text in parts:
        per_family.setdefault(family, []).append(text)
    assert bodies == {
        slug: "\n".join(texts) for slug, texts in per_family.items()
    }


def test_bodies_use_the_full_sections_even_for_a_sub_agent() -> None:
    """The INDEX differs between full/compact; the BODY does not — a body
    costs nothing until it is read, so a sub-agent gets the real guidance."""
    bodies = sx.family_bodies(effective_scope=None, registry_block="R")
    joined = "\n".join(bodies.values())
    # A heading only FULL_SECTIONS emits…
    assert "### Workspace & file writing" in bodies["core"]
    assert "### Memory & knowledge graph" in bodies["memory"]
    # …and a line only COMPACT_SECTIONS emits.
    assert "write_artifact(path,content) — files go to outputs/" not in joined
    assert "query_history(sql) — SELECT-only query" not in joined


# ── 4. Materialization: idempotent, and readable by a core-floor tool ───────

def test_materialize_is_content_hash_idempotent(tmp_path) -> None:
    first = sx.materialize_skill_bodies(tmp_path, force=True)
    assert first and set(first.values()) == {"written"}
    target = tmp_path / "agent-data" / "skills" / "core.md"
    stamp = target.stat().st_mtime_ns
    second = sx.materialize_skill_bodies(tmp_path, force=True)
    assert set(second.values()) == {"unchanged"}
    assert target.stat().st_mtime_ns == stamp, "an unchanged body was rewritten"
    # A changed body IS rewritten.
    target.write_text("stale", encoding="utf-8")
    third = sx.materialize_skill_bodies(tmp_path, force=True)
    assert third["core"] == "written"


def test_materialized_file_is_exactly_the_body(tmp_path) -> None:
    sx.materialize_skill_bodies(tmp_path, force=True, registry_block="R")
    bodies = sx.family_bodies(registry_block="R")
    for slug, body in bodies.items():
        path = tmp_path / "agent-data" / "skills" / f"{slug}.md"
        assert path.read_text(encoding="utf-8") == body


def test_the_read_path_is_a_core_floor_tool_and_round_trips(tmp_path) -> None:
    """The index points at ``recall_notes("skills/<slug>.md")`` — a tool the
    core floor guarantees (rule 2), reading a path it already resolves."""
    import asyncio

    from acb_skills.note_tools import recall_notes
    from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT

    assert "recall_notes" in ti._CORE_STANDARD_TOOL_NAMES

    sx.materialize_skill_bodies(tmp_path, force=True, registry_block="R")
    prev = _WRITE_ARTIFACT_CONTEXT.get("workspace_root", "")
    _WRITE_ARTIFACT_CONTEXT["workspace_root"] = str(tmp_path)
    try:
        got = asyncio.run(recall_notes(sx.body_read_argument("memory")))
    finally:
        _WRITE_ARTIFACT_CONTEXT["workspace_root"] = prev
    assert got == sx.family_bodies(registry_block="R")["memory"]
    assert "recall_agent" in got  # it really is the memory guidance


def test_body_paths_live_in_a_blob_store_backed_folder() -> None:
    """agent-data/ is one of the three durable workspace folders — the bodies
    reuse that convention rather than inventing a filesystem layout."""
    from acb_memory.blob_store import STORE_FOLDERS, folder_of
    for slug in sf.SKILL_FAMILIES:
        assert folder_of(sx.body_workspace_path(slug)) == "agent-data"
    assert "agent-data" in STORE_FOLDERS


def test_materialize_for_agent_uses_the_resolved_scope(
    monkeypatch, _index_on, tmp_path
) -> None:
    """The injection seam's helper materializes exactly what the agent got:
    a memory-only scope gets no history/coding body."""
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families", lambda name: frozenset(),
    )
    written = ti.materialize_skill_bodies_for_agent(
        "fake-agent", str(tmp_path), tool_scope=["remember"],
    )
    assert "memory" in written and "core" in written
    assert "history" not in written and "coding" not in written
    assert not (tmp_path / "agent-data" / "skills" / "history.md").exists()


def test_materialize_for_agent_honours_admin_disables(
    monkeypatch, _index_on, tmp_path
) -> None:
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families", lambda name: frozenset({"memory"}),
    )
    written = ti.materialize_skill_bodies_for_agent(
        "fake-agent", str(tmp_path), tool_scope=None,
    )
    assert "memory" not in written
    assert "core" in written
