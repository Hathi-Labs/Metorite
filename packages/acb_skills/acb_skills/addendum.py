"""Generated injected-tools addendum — WS-23 S3 (specs/skills_registry.md §3, §4).

ONE renderer for the system-prompt addendum that describes the platform tools
``orchestrator._tool_injection`` injects. Two consumers, same code path (the
spec's single-source rule):

* injection — ``_tool_injection._build_injected_tools_addendum`` is now a thin
  cached wrapper around :func:`render_injected_tools_addendum`;
* the S1 catalog's token-cost measurement — ``GET /integrations/skills`` wires
  that same wrapper as ``addendum_for_scope``, so the measured cost IS the real
  cost by construction.

The prose is declared as DATA: :data:`FULL_SECTIONS`, :data:`COMPACT_SECTIONS`
and :data:`MANDATORY_LINES` are ordered section registries where every entry is
tagged with the ``SKILL_FAMILIES`` slug that owns it plus the tool names that
gate it. ``tests/unit/test_generated_addendum.py`` is the drift gate: a gate
tool must belong to the section's declared family, and every toggleable static
family with an addendum presence must keep it. Because
``_resolve_injected_scope`` removes a disabled family's tools from the
effective scope, that family's sections drop from the rendered prose — the S3
acceptance ("addendum prose for a scoped agent contains no disabled-family
section") holds by construction at the renderer, not by hand-maintained
``if`` branches scattered through the orchestrator.

Gating stays PER TOOL (not per family) deliberately: injection itself is
per-tool (a ``tool_scope`` naming ``install_dependency`` but not
``github_search`` injects only the former), and describing a tool the agent
does not carry re-creates the misinformed-model bug (multi_agent_orchestration
Phase 0.2). Family membership is the *provenance* tag the drift tests check.

The text itself is preserved verbatim from the S2-era hand-maintained builder
— agents depend on this guidance, so S3 changed the SOURCE of the prose, never
its content: rendering is byte-identical to the pre-S3 builder for every
``(is_sub_agent, effective_scope)`` pair.

Known, deliberate gap carried over from the hand-maintained builder: the
``workflows`` family's trio (list_workflows / run_workflow / get_workflow_run)
is injected per-agent but has NEVER had an addendum section — the tools carry
their own docstrings. Adding prose for it would change content, which S3 must
not do; it is pinned by the drift test and flagged in the spec for a later
content decision.

This module is pure data + text: it never imports the orchestrator or gateway
(packages sit below apps in the import graph). The agent-registry block is a
parameter; the risk block defaults to ``acb_skills.tool_annotations``.
"""
from __future__ import annotations

from typing import Callable, NamedTuple, Union

from acb_skills.skill_families import SKILL_FAMILIES

# ---------------------------------------------------------------------------
# Shared directive blocks (moved verbatim from orchestrator._tool_injection —
# they are platform prose, not orchestrator logic; the injection module
# re-imports them for the native-MAF instruction appends).
# ---------------------------------------------------------------------------


def build_output_discipline_block(*, compact: bool = False) -> str:
    """The 'all generated files live under outputs/' + design-language rule.

    Injected into every agent (both runtimes). Two concerns, one block:
      1. File discipline — EVERY file the agent generates goes under ``outputs/``
         (logical subfolders encouraged), for both MAF and Copilot agents. This
         keeps the workspace tidy and, crucially, persistent: ``outputs/`` is the
         durable, redeploy-surviving home for deliverables.
      2. Design language — a pointer to the on-demand design system
         (``load_design_system()``) so any heavier document, report, or custom
         HTML matches the Metorite look.
    """
    if compact:
        return (
            "FILES: write files with write_artifact(path, content) — pass the "
            "content directly; do NOT build files with shell heredocs / echo / "
            "printf / base64 (fragile quoting truncates large writes). Put "
            "every file under outputs/ (logical subfolders, e.g. "
            "outputs/reports/); never the working-dir root. For a full-page "
            "report or bespoke custom HTML, call load_design_system() first to "
            "match the Metorite look (named genUI templates are already "
            "on-brand)."
        )
    return (
        "### Output discipline (REQUIRED)\n"
        "- **To write a file, call `write_artifact(path, content)`** — pass the "
        "file's content directly as the `content` argument. Do NOT assemble "
        "files with shell heredocs, `echo`, `printf`, `cat <<EOF`, or `base64`: "
        "that fragile quoting breaks on quotes/backticks, wastes your output "
        "budget, and can truncate a large write mid-file. Use the shell to RUN "
        "things, not to author file content.\n"
        "- Write EVERY file you generate under **outputs/** — reports, docs, "
        "HTML, data, scripts, images, everything. Use logical subfolders to "
        "stay organised (e.g. `outputs/reports/q3.md`, `outputs/data/rows.csv`, "
        "`outputs/site/index.html`). `write_artifact` already defaults bare "
        "paths to `outputs/`; when you use your own shell/editor tools, still "
        "target `outputs/` explicitly, never the working-directory root.\n"
        "- `outputs/` is the durable deliverables home — it persists across "
        "redeploys and reboots. Treat it as the one place a user looks for what "
        "you made.\n"
        "- Prefer Markdown (`.md`) for written deliverables and HTML (`.html`) "
        "for rich/interactive reports — both get a live preview in the side "
        "panel. Before writing a full-page report or bespoke custom HTML, call "
        "**`load_design_system()`** to load the Metorite design language "
        "and follow it (named `emit_generative_ui` templates are already "
        "on-brand and need no extra step)."
    )


def ui_first_directive(*, compact: bool = False) -> str:
    """The proactive 'render UI by default' rule (generative_ui_2 Phase 2).

    Static, byte-stable text delivered at the SYSTEM-PROMPT level to BOTH
    runtimes (Copilot via the addendum, native MAF via an instructions append)
    so the model actually REACHES FOR ``emit_generative_ui`` at answer time —
    the tool's own docstring alone was too weak a signal, and MAF agents never
    saw the addendum's genUI guidance at all. Template-first ordering keeps it
    token-cheap and on-brand by construction (custom HTML is the costly last
    resort). Gate rendering on the agent actually having ``emit_generative_ui``.
    """
    if compact:
        return (
            "Rich UI by default: when the answer is data / a metric / a status "
            "/ a comparison / steps / a choice / a value to set, call "
            "emit_generative_ui with a named TEMPLATE (supply data only — "
            "on-brand and cheapest: weatherCard, statDashboard, barChart, "
            "sparkTrend, comparison, progressTracker, recipeCard, flightStatus, "
            "trainStatus, formCard, optionPicker) rather than describing it in "
            "prose; use a component tree for simple structured data and custom "
            "html only as a last resort; pair any pick/set with \"hitl\":true. "
            "Skip UI only for a trivial one-liner or a long narrative."
        )
    return (
        "### Rich UI by default for structured answers "
        "(call emit_generative_ui)\n"
        "Before you answer in prose, ask: is the reply data, numbers/metrics, "
        "a status, a comparison, steps or a checklist, or a value the user "
        "should pick or set? If yes, RENDER it — a card is clearer than a "
        "paragraph and can be interactive. This is expected behaviour, not "
        "decoration. Pick the CHEAPEST mode that fits (which also keeps you "
        "on-brand):\n"
        "- **Template first** — `{\"type\":\"template\",\"props\":{\"name\":…,"
        "\"data\":{…}}}`. You supply DATA ONLY; fonts, colours, Lucide icons, "
        "motion and light/dark are built in and correct every time. Names: "
        "weatherCard, statDashboard, barChart, sparkTrend, comparison, "
        "progressTracker, recipeCard, flightStatus, trainStatus, formCard, "
        "optionPicker.\n"
        "- **Component tree** next — cards / tables / keyValue / badges / "
        "callouts / buttons / icons for simple structured data.\n"
        "- **Custom html LAST** — only when nothing above fits; it costs the "
        "most tokens and you must match the brand yourself via the `--cc-*` "
        "CSS variables. Never hand-roll HTML for something a template covers.\n"
        "When the user must choose or set something, use formCard / "
        "optionPicker (or buttons) with top-level `\"hitl\":true` so their "
        "answer returns to you in the same turn. Skip UI only for a trivial "
        "one-liner or a long narrative explanation."
    )


# ---------------------------------------------------------------------------
# The section registries (ordered; every entry family-tagged + tool-gated)
# ---------------------------------------------------------------------------

class RenderContext(NamedTuple):
    """Dynamic inputs a section's text may interpolate."""

    registry_block: str
    risk_block: str


SectionText = Union[str, Callable[[RenderContext], str]]


class Section(NamedTuple):
    """One addendum section.

    ``family`` — the ``SKILL_FAMILIES`` slug that owns this prose (drift-gated).
    ``gate``   — tool names; the section renders iff the effective scope is
                 ``None`` (unscoped) or contains AT LEAST ONE of them. Empty
                 tuple = always rendered (core-floor framing/closing prose).
    ``text``   — the verbatim prose, or a callable taking :class:`RenderContext`
                 for sections that interpolate the registry / risk blocks.
    """

    family: str
    gate: tuple[str, ...]
    text: SectionText


#: Delegation trio — gates the delegation section AND the registry block.
_DELEGATION = ("call_agent", "call_agents_parallel", "call_agent_background")

#: All eight memory tools — one family, one section.
_MEMORY = tuple(SKILL_FAMILIES["memory"]["tools"])


FULL_SECTIONS: tuple[Section, ...] = (
    Section("core", (), """
---
## Metorite Platform Tools (injected at runtime)
"""),
    # Proactive UI rule leads the addendum — it governs how the agent ANSWERS
    # (render vs. prose), so it must be prominent, not buried among tool specs
    # (Phase 2). Gated so a scoped-out agent isn't told to use a tool it lacks.
    Section("core", ("emit_generative_ui",), lambda ctx: ui_first_directive()),
    Section("core", _DELEGATION, lambda ctx: f"""### Inter-agent delegation
- **call_agent(agent_name, message)** — Delegate to another agent; waits for its response.
- **call_agents_parallel(tasks)** — Run multiple agents concurrently (JSON array of {{"agent","message"}} objects).
- **call_agent_background(agent_name, message)** — Fire-and-forget; use when result is not needed now.

{ctx.registry_block}
"""),
    Section("core", ("web_search", "fetch_page"), """### Web access (no API key required)
- **web_search(query, max_results=5)** — Web search (SerpAPI/Google first when configured, free engines as fallback). Use for current info, news, company research.
- **fetch_page(url, max_chars=8000)** — Fetch a public URL as clean text via Jina Reader.
"""),
    Section("memory", _MEMORY, """### Memory & knowledge graph
- **remember(query)** — Search episodic memory for past facts about the user. Call before making claims about history or preferences.
- **recall_timeline(entity, query)** — Bi-temporal knowledge graph: "when did X happen?" or entity history.
- **save_memory(fact)** — Persist a high-signal fact about THIS USER. Routine turns are handled automatically.
- **recall_agent(query)** / **save_agent_memory(fact)** — This AGENT's memory, shared across EVERY user who talks to it. Use for durable knowledge the agent should carry regardless of who's asking (procedures, domain facts, decisions) — not one user's private preference.
- **recall_org(query)** / **save_org_memory(fact)** — ORGANISATION-WIDE memory shared by every agent and user. Read when a question touches company-level context; write only genuinely company-level facts (structure, policy, standing priorities), and confirm before writing.
- **save_episode(name, content, source?)** — Record a time-stamped episode; Graphiti extracts entities & relationships.
"""),
    Section("core", (), lambda ctx: f"""### Workspace & file writing
{build_output_discipline_block()}

Workspace folders visible in the Files Viewer: **outputs/** (default for generated files), **inputs/** (user uploads, read-only), **agent-data/** (reusable reference data).
- **write_artifact(path, content, encoding?, overwrite?)** — Write a file to outputs/ (if path has no prefix). The chat shows a Download/preview card **automatically** — you do NOT need to build or paste any URL; just say what the file is. It never clobbers an existing file by default (it auto-versions to ``name (1).ext`` and returns the real ``path``); pass ``overwrite=true`` only when you deliberately want to replace a file in place. For **.html** files the result may include a ``warnings`` list — the sandbox fails silently, so these are real defects (a CDN URL the CSP blocks, an unknown ``cc-`` class, a chart block missing its ``--v``). Fix them and re-write with ``overwrite=true`` in the same turn; never leave a warned artifact for the user to find. Writing **`outputs/<name>.jsx`** (or `.tsx`) creates a FULL-PAGE, immersive **React** artifact that opens in the side panel — same rules as the `emit_generative_ui` react node (default-export a component, import only from react/react-dom/client, style with the cc-* kit). Use it for a substantial interactive tool or dashboard the user will keep; use the inline react node for something compact in the chat itself.
- **share_artifact(path)** — If you created a file with your OWN tools (shell, editor, a script you ran) instead of write_artifact, call this with that file's path (or a folder) to surface it as a Download/preview card. The card appears automatically; do NOT hand-construct links.
- **emit_generative_ui(ui)** — Render a rich, interactive, animated UI element inline in the chat, on the fly. REACH FOR THIS EAGERLY: when the answer is data, a metric, a status, a comparison, a checklist, or a value the user should pick or set, render UI instead of a paragraph — it is clearer and often interactive. Do not be trivial about it (a one-line factual reply or a long narrative stays as text), but whenever there is a genuine chance to let the user adjust a value, pick an option, or confirm a choice, prefer an interactive UI over asking in prose. All three modes follow the Metorite design language automatically (blue primary, warm-orange accent, rounded cards, subtle motion). `ui` is a JSON object discriminated by its top-level `type`. Three modes, prefer them in this order:
    1. **Named template** (best-looking, use first when one fits) — a node with type "template" and props holding `name` plus a `data` object. Pre-designed animated cards; supply data only. Available names: weatherCard, statDashboard, barChart, sparkTrend, comparison, progressTracker, recipeCard, flightStatus, trainStatus, formCard, optionPicker (see the emit_generative_ui tool doc for each one's data shape). statDashboard stats accept an optional `icon` (a Lucide name). formCard and optionPicker COLLECT user input — pair them with top-level `"hitl": true` so the submitted values return as this tool call's result, and use top-level `"surface": "panel"` to open any big UI as an immersive side-panel view instead of an inline card.
    2. **Component tree** (structured, safe) — a whitelist tree of card / table / keyValue / badge / callout / list / button / icon nodes; data, not code. The icon node takes a `name` = any Lucide icon (e.g. cloud-sun, check-circle, trending-up). A button node has a `label` plus an `action` string sent back when clicked. Good for summaries, tables, labelled rows, and action buttons.
    3. **React component** (for anything genuinely INTERACTIVE or stateful — filterable/sortable tables, multi-step forms, calculators, live dashboards, small tools) — a node with type "react" and props holding `code`: ordinary modern React that DEFAULT-EXPORTS a component (`export default function App(){{...}}`). Hooks all work; JSX/TypeScript are compiled for you. You may import from "@cc/ui" (the prebuilt design kit — call load_artifact_kit() first) and from "react" / "react-dom/client". Nothing else: the sandbox has NO network, so no npm packages, no CDNs, no icon libraries; inline your helpers and seed the data in the file. Style it with the SAME cc-* classes and --cc-* tokens as custom HTML (start with <div className="cc-report">). Send data back with window.ccSubmit('Label', value) or window.ccAction('message') — both work from first mount. If the build fails, the tool result carries the compiler errors: fix them and emit again.
    4. **Custom HTML** (escape hatch, only when no template/tree fits, and the place for genuinely interactive controls) — a node with type "html" and props holding `code` (a full HTML/CSS/JS snippet) plus an optional `icons` array of Lucide names. Runs in an isolated sandbox for bespoke animation/layout. No external network/CDNs — inline everything; use declared icons via ccIcon('Name') or a span with data-cc-icon='Name'. DESIGN: use the pre-defined CSS variables so it matches the app — --cc-primary, --cc-accent, --cc-fg, --cc-muted, --cc-card, --cc-secondary, --cc-border, --cc-success, --cc-warning, --cc-danger, --cc-radius, --cc-ease — instead of hard-coded colors; native button/input/select/textarea and range sliders are already styled on-brand (add class cc-primary for a filled blue button, cc-card for a panel). INTERACTIVITY: put data-cc-action='<follow-up message>' on a clickable element (or call ccAction('...') in script) to fire a fixed follow-up like a button; put data-cc-submit='<label>' on a button to harvest every named input/select/textarea in its enclosing form (or a data-cc-form container) and submit their VALUES back — or call ccSubmit('Temperature', 22) directly — so when the user sets a slider/number/option the agent actually receives what they chose.

**Choosing the surface — decide this BEFORE you start writing the UI.** Let the shape and VOLUME of the answer pick, not whichever tool came to mind first:
- **Prose** — a one-line fact, or a genuinely narrative explanation.
- **Inline card** (`emit_generative_ui`) — anything the user just needs to SEE at a glance: roughly ≤8 table rows, ≤4 metrics, one chart, a status, a comparison. If you are about to type a markdown table of data, render it instead.
- **Side panel** — anything SUBSTANTIAL: more than ~12 rows, several sections or charts, or something the user will scroll, sort, filter, return to, or keep. Use `"surface": "panel"` for a live view, or `write_artifact("outputs/<name>.jsx")` when it should persist. Rule of thumb: an inline card that would need its own scrollbar belongs in the panel.
- **React + `@cc/ui`** — whenever the user should DO something with the data (filter, sort, pick, set a value), or there is simply too much of it for a static table to stay readable. At that point call **`load_artifact_kit()`** and compose the prebuilt components; do not hand-roll the markup.
- **When genuinely in doubt** — a big result that could reasonably be a saved document OR a live dashboard — ask which they'd prefer rather than guessing (an `optionPicker` template with `"hitl": true` settles it in one turn).

**Delivering files to the user:** to give the user a downloadable/previewable file, call ``write_artifact`` (for content you generate) or ``share_artifact`` (for a file you already wrote). That is ALL you need — the card renders itself. Never try to guess or assemble a download URL yourself.
"""),
    Section("core", ("manage_todo_list",), """### Task planning & progress tracking
- **manage_todo_list(todoList)** — Update the live "Todos (n/m)" panel above the chat input.  Takes a JSON object with ``"todoList"`` (the COMPLETE array of all items) and optional ``"operation"`` (``"write"`` or ``"read"``).  Each item: ``id`` (number, sequential from 1), ``title`` (string, 3-7 words), ``status`` (``"not-started"``, ``"in-progress"``, or ``"completed"``).  Use this tool VERY frequently.  CRITICAL workflow: 1) Plan tasks with specific items. 2) Mark ONE as ``"in-progress"`` before starting. 3) Mark it ``"completed"`` immediately after finishing. 4) Move to next.  Do NOT use for trivial single-step requests.  The user sees this panel update in real time.
"""),
    # The MANDATORY block is assembled from MANDATORY_LINES below (same gating
    # rules) — declared as its own registry so each line keeps a family tag.
    Section("core", (), "__MANDATORY__"),
    Section("core", ("ask_questions",), """### Human-in-the-Loop (HITL) elicitation
- **ask_user(question, choices?, allowFreeform?)** — Pause the run and ask the user ONE clarifying question via an interactive card.  This BLOCKS the agent turn: execution truly pauses and resumes automatically with the user's answer (no separate message turn).  ``choices`` is an optional list of suggested answers (rendered as buttons); ``allowFreeform`` (default true) lets the user type a custom answer.  Use when you need to disambiguate, a parameter is missing, or a decision has important implications.  Prefer ONE focused question per call.
- **ask_questions(questions)** — Alternative for asking SEVERAL questions at once via a multi-question card (JSON object with a ``"questions"`` array; each has ``header``, ``question``, optional ``options``/``multiSelect``/``allowFreeformInput``).  Prefer ``ask_user`` for single blocking questions.
"""),
    Section("core", ("run_diagnostics", "get_errors"), """### Code quality & error checking
- **run_diagnostics(filePaths?)** (alias **get_errors**) — Run code diagnostics: check Python files for syntax, type, and lint errors.  Call after editing or creating files, before committing, or to diagnose a failed run.  Pass a JSON array of file paths (e.g. ``'["executor.py"]'``) or ``'[]'`` to auto-discover recently changed files.  Runs ``py_compile`` (syntax) and ``ruff`` (lint, if installed).  Returns structured errors or ``"No errors found."``.  Both names call the same tool.
"""),
    Section("coding", ("install_dependency",), """### Runtime dependencies
- **install_dependency(packages)** — Install Python package(s) into the agent runtime so your imports/tools work.  Use this when you hit a ``ModuleNotFoundError`` or know a task needs a package that isn't installed.  Pass space- or comma-separated specs, e.g. ``"pandas openpyxl"`` or ``"requests==2.31.0"`` (plain names + optional version; no flags/URLs).  Installs into the shared agent venv via ``uv``; the package is importable immediately.  Prefer this over shell ``pip install`` (the venv has no pip).
"""),
    Section("core", ("run_script", "code_task"), """### Coding skill (durable scripts)
When your built-in tools can't do something, WRITE A PROGRAM for it — and keep it, so next time is instant.
- **code_task(task)** — Delegate a coding job to the platform's coding engine: it writes, edits, runs, and debugs scripts inside YOUR workspace in one bounded session. Describe what to build (inputs, expected output, and the existing script's name if changing one). It follows the script contract: reusable scripts live under ``agent-data/scripts/``, the catalog lives in ``agent-data/SCRIPTS.md``, and existing scripts are edited in place rather than duplicated. Scripts persist durably — they survive restarts and redeploys, so a capability you build once stays yours.
- **run_script(path, args?)** — Execute a script that already exists (e.g. ``agent-data/scripts/report.py``, ``.py`` or ``.sh``) and get its output. No reasoning step — much faster and cheaper than code_task. Check ``recall_notes("SCRIPTS.md")`` for your script catalog.
This also covers your BUILT-IN skills: if one of your repo-baked skill scripts (under ``skills/``) misbehaves, call ``code_task`` describing the problem — it fixes the source in place, and the change is committed locally and queued for HUMAN APPROVAL in the inbox (live once approved). Workspace scripts under ``agent-data/`` need no approval.
- **list_integrations()** — See which platform integrations (ClickUp, Zoho CRM, Gmail, SerpAPI, …) are configured for you and the env-var NAMES your scripts can read for each (e.g. ``CLICKUP_API_TOKEN`` via ``os.getenv``). Call this BEFORE writing a script against an external service: scripts receive exactly your declared integrations' credentials at run time — nothing else — so never hard-code keys or ask the user to paste one. If an integration you need is listed as unavailable, tell the user what needs configuring.
Workflow: need a new capability → ``code_task``; repeat a known job → ``run_script``; small tweak to an existing script → ``code_task`` naming the script. Files a script writes under ``outputs/`` are persisted and appear in the Files panel automatically.
"""),
    Section("core", ("save_note", "recall_notes"), """### Working memory (repo-scoped notes)
- **save_note(path, fact)** — Append a dated bullet to a markdown notes file under ``agent-data/``.  Your canonical working memory is ``agent-data/NOTES.md`` — read it at session start with ``recall_notes("NOTES.md")``.
- **recall_notes(path, query?)** — Read back a notes file, optionally filtered by a search query.  Use to restore context from previous sessions.
"""),
    Section("history", ("query_history",), """### Conversation history
- **query_history(search?, thread_id?, agent_name?, since_days?, limit?)** — Recall past conversations by search criteria.  Use to remember what was discussed in prior sessions, find a decision that was made before, or resume work on a known thread.  Takes **search terms, not SQL** — e.g. ``query_history(search="pricing", since_days=30)``.
"""),
    Section("coding", ("github_search", "github_repo_search"), """### GitHub code search
- **github_search(query, scope?, maxResults?)** — Lexical search across public GitHub repositories.  Supports ``language:python``, ``repo:owner/name``, ``path:src/`` filters.
- **github_repo_search(repo, query?)** — Search within a specific GitHub repository for code snippets and implementation patterns.
"""),
    Section("core", ("save_note", "recall_notes"), """### Working memory (NOTES.md pattern)
Maintain **`agent-data/NOTES.md`** as your cross-session working memory.
- At the START of each session: read this file if it exists to restore context.
- After each significant discovery, decision, or milestone: append a dated bullet (e.g. `- 2026-06-16: Closed ABC Corp deal at ₹50L`).
- Keep entries concise — one line per fact. This file survives context compaction and session resets.
"""),
    Section("core", (), lambda ctx: f"""{ctx.risk_block}
Tools marked DESTRUCTIVE are irreversible or outward-facing: never call one
without an explicit user instruction. If it renders its own confirmation
card, let that do the confirming (do not also double-confirm in prose) —
not every destructive tool has one yet, so treat the instruction above as
the real requirement, not the card's presence.

### Self-improvement & committing
To persist changes to your own repo: `git add -A`, then `git commit -m "feat: ..."`, then print `COMMIT_SHA: <git rev-parse HEAD>`. Never amend; one commit per task.
- **By default, do NOT push** — direct pushes are blocked and the commit queues for human approval in the Control Plane inbox.
- **If the user explicitly tells you to push (e.g. "commit and push") in this conversation**, then after committing run `git push --no-verify origin HEAD`. The `--no-verify` flag is required to bypass the approval hook. That commit is then recorded as already-approved — the user does not need to approve it again on the Agents page.

### Design language — load on demand
Named `emit_generative_ui` templates are already on-brand (supply data only), and the `--cc-*` CSS tokens above cover simple custom HTML. For anything HEAVIER — a full-page HTML or Markdown **report**, or a bespoke custom-HTML `emit_generative_ui` card — call **`load_design_system()`** FIRST — it returns the core guide (palette, type, spacing, motion, surface choice); ask for a section (`"blocks"`, `"charts"`) rather than `"all"` when you need a block reference. Don't guess the styling for a report. For a **React** artifact call **`load_artifact_kit()`** instead — it lists the prebuilt `@cc/ui` components (stat tiles, bar/donut/trend charts, data tables, timelines, decision boxes, buttons that submit back to you) in a few hundred tokens; then `load_artifact_kit("Stat,Table")` for the exact props of just the ones you use. Importing them is far cheaper and more reliable than hand-writing the markup.
---"""),
)


class MandatoryLine(NamedTuple):
    """One line of the MANDATORY block (same family/gate semantics)."""

    family: str
    gate: tuple[str, ...]
    text: str


MANDATORY_LINES: tuple[MandatoryLine, ...] = (
    MandatoryLine("core", ("manage_todo_list",), (
        "- For todo/task tracking: call ``manage_todo_list`` — do NOT use "
        "``remember``, ``task_manager``, ClickUp, or any other tool."
    )),
    MandatoryLine("core", ("ask_questions",), (
        "- For clarifying questions: call ``ask_user`` (native, blocking) "
        "or ``ask_questions`` — do NOT just ask in text."
    )),
    MandatoryLine("core", ("run_diagnostics", "get_errors"), (
        "- For checking code / diagnostics: call ``run_diagnostics`` "
        "(alias ``get_errors``)."
    )),
    MandatoryLine("core", ("save_note", "recall_notes"),
                  "- For notes: call ``save_note`` / ``recall_notes``."),
    MandatoryLine("history", ("query_history",),
                  "- For history: call ``query_history``."),
    MandatoryLine("coding", ("github_search", "github_repo_search"), (
        "- For code search: call ``github_search`` / ``github_repo_search``."
    )),
)


COMPACT_SECTIONS: tuple[Section, ...] = (
    Section("core", (), "\n---"),
    Section("core", (), "## Metorite Platform Tools"),
    Section("core", (), build_output_discipline_block(compact=True)),
    # Proactive UI rule first — it changes how the agent ANSWERS, so it
    # leads (Phase 2). Gated: only agents that actually have the tool.
    Section("core", ("emit_generative_ui",),
            ui_first_directive(compact=True)),
    Section("core", _DELEGATION, (
        "call_agent(name,msg), call_agents_parallel(tasks), "
        "call_agent_background(name,msg)"
    )),
    Section("core", ("web_search", "fetch_page"),
            "web_search(query), fetch_page(url)"),
    Section("core", ("write_artifact",),
            "write_artifact(path,content) — files go to outputs/"),
    Section("core", ("share_artifact",), (
        "share_artifact(path) — show a file you already wrote as a "
        "download/preview card"
    )),
    Section("core", ("emit_generative_ui",), (
        "emit_generative_ui(ui) — render rich UI inline; reach for it EAGERLY when the answer is data/status/comparison/a checklist/a value to pick or set (not for trivial one-liners). On-brand automatically. Optional top-level fields: surface:'panel' opens the UI as an immersive side-panel view (use for big dashboards/itineraries/long recipes/multi-section forms); hitl:true BLOCKS this call until the user interacts and returns their values as the tool result (use for forms/pickers you need answered). 3 modes: (1) component tree card/table/keyValue/badge/callout/button(label+action) + an icon node (type:icon, name=any Lucide icon e.g. 'cloud-sun'); (2) a template node (type:template, name= weatherCard/statDashboard/barChart/sparkTrend/comparison/progressTracker/recipeCard/flightStatus/trainStatus/formCard/optionPicker) pre-designed animated cards, supply data only (formCard+optionPicker collect user input — pair with hitl:true); (3) an html node (type:html, props code + optional icons list of Lucide names) custom animated HTML/CSS/JS in a sandbox — style with the pre-set CSS vars --cc-primary/--cc-accent/--cc-fg/--cc-card/--cc-border/--cc-radius/--cc-ease (native inputs+sliders pre-styled), use icons via ccIcon('Name') or a span with data-cc-icon='Name', wire interactivity via data-cc-action='msg' (fixed follow-up) or data-cc-submit='label'/ccSubmit('label',value) to send user-set slider/input/select VALUES back. (4) a react node (type:react, props code) — a REAL React component (hooks, state) for interactive/stateful artifacts; default-export it; import prebuilt components from @cc/ui (run load_artifact_kit() to see them) plus react/react-dom/client — nothing else (no network, no npm); call ccSubmit/ccAction to reach the agent. Prefer template over tree over react over html."
    )),
    Section("memory", _MEMORY, (
        "remember(query)/save_memory(fact) = THIS USER's private memory; recall_agent(query)/save_agent_memory(fact) = shared across ALL users of this agent; recall_org(query)/save_org_memory(fact) = organisation-wide (every agent+user); recall_timeline(entity,query), save_episode(name,content)"
    )),
    Section("core", ("manage_todo_list",), (
        "manage_todo_list(todoList) — structured task tracking panel (JSON array)"
    )),
    Section("core", ("ask_questions",), (
        "ask_user(question,choices?) — HITL: pause and ask the user one question (blocking; the run resumes with their answer)"
    )),
    Section("core", ("run_diagnostics", "get_errors"), (
        "run_diagnostics(filePaths?) — run code diagnostics: check Python files for syntax/lint errors (alias: get_errors)"
    )),
    Section("coding", ("install_dependency",), (
        "install_dependency(packages) — install Python package(s) into the agent venv at runtime"
    )),
    Section("core", ("run_script", "code_task"), (
        "run_script(path,args?) — run a saved workspace script "
        "(agent-data/scripts/*.py|.sh) directly, cheap; "
        "code_task(task) — bounded coding session that writes/edits/"
        "tests scripts in your workspace (check agent-data/SCRIPTS.md "
        "first; prefer run_script for re-runs); "
        "list_integrations() — which platform integrations your "
        "scripts can use (env var names, values injected at run time)"
    )),
    Section("core", ("save_note", "recall_notes"), (
        "save_note(path,fact), recall_notes(path,query?) — repo-scoped working memory"
    )),
    Section("history", ("query_history",), (
        "query_history(search?,thread_id?,agent_name?,since_days?,limit?) — recall past conversations"
    )),
    Section("coding", ("github_search", "github_repo_search"), (
        "github_search(q,scope?,max?), github_repo_search(repo,q?) — code search"
    )),
    Section("core", (), lambda ctx: f"\n{ctx.risk_block}\n"),
    Section("core", _DELEGATION, lambda ctx: ctx.registry_block),
    Section("core", (), "---"),
)


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------

def _default_risk_block() -> str:
    """Byte-stable risk-annotation block (HH-2) — static registry, so the
    system-prompt prefix stays cache-friendly."""
    try:
        from acb_skills.tool_annotations import risk_summary_block  # noqa: PLC0415
        return risk_summary_block()
    except ImportError:
        return ""


def _wants(gate: tuple[str, ...],
           effective_scope: frozenset[str] | None) -> bool:
    return (
        not gate
        or effective_scope is None
        or any(name in effective_scope for name in gate)
    )


def _mandatory_block(effective_scope: frozenset[str] | None) -> str | None:
    lines = [
        line.text for line in MANDATORY_LINES
        if _wants(line.gate, effective_scope)
    ]
    if not lines:
        return None
    return (
        "**MANDATORY — You MUST call these functions. Do NOT use other "
        "tools for these purposes.**\n"
        + "\n".join(lines)
        + "\nFunction calls trigger real-time UI (TodoPanel, "
        "ElicitationCard) — text does nothing.\n"
    )


def rendered_parts(
    *,
    is_sub_agent: bool = False,
    effective_scope: frozenset[str] | None = None,
    registry_block: str = "",
    risk_block: str | None = None,
) -> list[tuple[str, str]]:
    """The addendum's rendered pieces, each paired with its owning family.

    THE one place section text is produced. :func:`render_injected_tools_addendum`
    joins these with ``"\\n"`` (so the classic addendum is exactly
    ``"\\n".join(text for _, text in rendered_parts(...))``), and
    ``acb_skills.skill_index`` regroups the SAME pieces by family to build the
    on-demand body files — byte-preservation of the guidance is therefore a
    property of the code path, not of a copy someone keeps in sync.

    The ``__MANDATORY__`` block is emitted under the ``core`` family: its lines
    carry their own family tags (``MANDATORY_LINES``) and are gated
    individually, but the assembled block is one indivisible piece of prose.
    """
    ctx = RenderContext(
        registry_block=registry_block,
        risk_block=risk_block if risk_block is not None else _default_risk_block(),
    )
    sections = COMPACT_SECTIONS if is_sub_agent else FULL_SECTIONS
    parts: list[tuple[str, str]] = []
    for section in sections:
        if not _wants(section.gate, effective_scope):
            continue
        if section.text == "__MANDATORY__":
            rendered = _mandatory_block(effective_scope)
            if rendered is not None:
                parts.append((section.family, rendered))
            continue
        text = section.text
        parts.append(
            (section.family, text(ctx) if callable(text) else text)
        )
    return parts


def render_injected_tools_addendum(
    *,
    is_sub_agent: bool = False,
    effective_scope: frozenset[str] | None = None,
    registry_block: str = "",
    risk_block: str | None = None,
) -> str:
    """Assemble the injected-tools addendum from the section registries.

    ``effective_scope`` is the agent's RESOLVED injected-tool set (declared
    ``tool_scope`` ∪ core floor, ∩ enabled families — what
    ``_resolve_injected_scope`` returned); ``None`` means an unscoped agent
    and yields the full addendum. A disabled family's tools are absent from
    the scope, so its sections (and MANDATORY lines) are not rendered.

    ``registry_block`` is the live agent-registry listing (dynamic — the
    orchestrator passes its cached block); ``risk_block`` defaults to the
    static ``tool_annotations`` summary. Callers should cache per
    ``(is_sub_agent, effective_scope)`` — ``_build_injected_tools_addendum``
    keeps its lru_cache — so system-prompt prefixes stay byte-stable (KV
    cache).

    **QM-2 index mode (``SKILLS_INDEX_ONLY``, ships OFF).** When the switch is
    ON this returns the one-line-per-family INDEX from
    ``acb_skills.skill_index`` instead of the bodies; the bodies are
    materialized into the agent workspace and read on demand. Branching HERE
    (rather than at the injection seam) keeps the single-source rule: the S1
    catalog measures through this same renderer, so the measured cost is the
    real cost in either position. OFF ⇒ byte-identical to today.
    """
    if _index_only():
        from acb_skills.skill_index import render_skill_index  # noqa: PLC0415
        return render_skill_index(
            is_sub_agent=is_sub_agent,
            effective_scope=effective_scope,
        )
    return "\n".join(
        text for _, text in rendered_parts(
            is_sub_agent=is_sub_agent,
            effective_scope=effective_scope,
            registry_block=registry_block,
            risk_block=risk_block,
        )
    )


def _index_only() -> bool:
    """Read the QM-2 switch without importing skill_index at module scope."""
    try:
        from acb_skills.skill_index import skills_index_only  # noqa: PLC0415
        return skills_index_only()
    except ImportError:  # pragma: no cover — module always ships together
        return False
