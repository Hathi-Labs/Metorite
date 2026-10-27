# Skills Registry + per-agent skill toggles (WS-23)

**Status:** S1 + S2 shipped pending review 2026-08-01 (catalog + toggles + enforcement); S3 generation half + scope-out + prepared default profile shipped pending review 2026-08-01 — **the fail-closed flip itself remains OWNER-GATED and OFF**; **S4 core-floor diet built 2026-08-01** (schema trim live; skills-index mode shipped OFF behind `SKILLS_INDEX_ONLY`, OWNER-GATED) · **Owner:** vjvarada · verified against code 2026-08-01
**Owning workstream:** `work_plan.md` WS-23 (sequenced with WS-12 context discipline)

## 0. Thesis

Integrations settings answer "what can agents *reach*"; nothing answers "what
can agents *do*". The platform injects tool families into every agent from
`_tool_injection.py`, governed only by code-authored `config.json: tool_scope`
— invisible to admins, and the injected tools addendum costs ~7,800 tokens of
every run's context (WS-12 Phase 1's target: under 2,000). This feature makes
the injected skill surface **visible, toggleable per agent, and priced in
tokens** — turning the context-discipline work into a product surface instead
of a one-off trim.

## 1. Scope and non-goals

**In scope:** a read-only skills catalog grouped into named **skill families**
with measured token cost; per-agent enable/disable of families stored in
Postgres; enforcement at the existing injection seam; the tools addendum
generated from the enabled set (kills the hand-maintained-prose drift flagged
in `core_module_map.md` B2).

**Non-goals (v1):** not a skill marketplace or community intake (CH-9 gate
stands); no per-user toggles (per-*instance* profiles arrive with Centers,
§6); does not replace the B6 runtime permission policy (risk gating at call
time stays); does not touch agents' repo-baked own tools beyond the existing
`own_tool_scope`; **never widens** an agent's surface beyond what its code or
manifest declares.

## 2. The three rules

1. **Intersection, never union.** Effective injected set =
   `(declared tool_scope ∪ CORE) ∩ (tools of enabled families ∪ CORE)`.
   A toggle can only narrow within what the agent's repo/manifest declares —
   a UI switch must never grant a tool the code never asked for
   (`agent_platform_hardening_2026-07.md` C2: manifest *requests*, a grant
   table *authorizes*).
2. **The core floor is not toggleable.** `_CORE_STANDARD_TOOL_NAMES`
   (delegation/reporting family) is injected regardless — a toggle that can
   strand an agent unable to delegate or report is a debugging trap, not a
   setting.
3. **No rows ⇒ today's behavior.** Absent settings change nothing. The
   separate decision to flip fail-open (`no tool_scope` ⇒ everything, C1) to
   fail-closed rides this feature *deliberately* as S3, never implicitly.

**Decision note — scope-independent injections (S2, decided default, for
owner review 2026-08-01).** Two families are injected after (and regardless
of) `tool_scope` filtering; S1 flagged the question of whether toggles govern
them. The S2 default: the **`workflows`** family toggle IS honored — an
explicit admin disable removes the list/run/status trio at its append site
(that is narrowing, which rule 1 permits). Granted **Custom-App action tools**
(`app_<slug>_<action>`) are NOT governed by skill toggles: an app grant is an
explicit per-agent permission with its own management surface (`app_grants`),
so the `apps` family is excluded from toggling in the API (422) and shown as
"managed via App grants" in the UI.

## 3. Design

**Families are code-declared data.** A `SKILL_FAMILIES` registry in
`packages/acb_skills/` maps family slug → {label, description, tool names}.
Families: `core` (floor), `files-artifacts`, `memory`, `email`, `whatsapp`,
`tasks-gtd`, `calendar`, `notes`, `workflows`, `apps`, `diagrams` (future),
`web-research`, `coding` (`code_task`/`run_script`). One registry, consumed by
injection, the catalog API, and addendum generation — the single-source rule
that killed the six history-slicers (C2 precedent).

**Token cost is measured, not estimated.** At catalog build, render each
family's addendum section + JSON schemas and count tokens (same tokenizer as
`assemble_run_context`); cache per (family, registry hash). The UI shows
"WhatsApp · 14 tools · ~1.9k tokens".

**Settings table** (migration: next free number at build time), mirroring the
org-access override shape — the *why* is a column:

```sql
CREATE TABLE agent_skill_setting (
    agent_name  TEXT NOT NULL,
    instance    TEXT NOT NULL DEFAULT '',   -- '' = agent-wide; 't:<slug>'/'u:<email>' later (§6)
    family      TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    set_by      TEXT NOT NULL,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_name, instance, family)
);
```

**API** (gateway, `require_permission` per org-access seams; admin write):
`GET /integrations/skills` (catalog: families, tools, token costs, per-agent
enabled matrix) · `GET/PUT /agent/{name}/skills` (settings; PUT is
`admin:access:manage`-gated).

**Enforcement seam:** `_resolve_injected_scope()` in `_tool_injection.py`
gains the family intersection (one function, both runtimes — same choke-point
principle as D8). Settings resolved once at run start alongside integrations.

**UI:** (a) Integrations → new **Skills** tab: the catalog with token costs
and a which-agent-has-what matrix; (b) Agent Registry (`/agents`) per-agent
panel: family checklist with a live context-cost meter ("enabled: 5 families ·
~3.1k tokens of tool context").

## 4. Phases + acceptance

- **S1 — catalog (read-only).** Registry + token measurement + GET API + tab.
  *Done when:* every injected platform tool appears in exactly one family; the
  tab renders costs; a drift test fails CI if a tool is injected but missing
  from the registry (extends `test_tool_addendum_drift_trajectory.py`).
  **SHIPPED pending review 2026-08-01.** `acb_skills/skill_families.py`
  (registry + DI'd `build_catalog`), `_collect_injectable_platform_tools()`
  extracted verbatim in `_tool_injection.py` (injection unchanged), gateway
  `routes/integrations_skills.py` (`GET /integrations/skills`, same gate as
  the sibling endpoints), Integrations → Skills tab, drift + route tests in
  `tests/unit/test_skills_registry.py` / `test_integrations_skills_route.py`.
  *Family set corrected to the code* (§3's list predated the grown core
  floor): `core` (= the 19-tool `_CORE_STANDARD_TOOL_NAMES` floor — it
  subsumes files-artifacts / notes / web-research / most coding), `memory`
  (8), `history` (1), `coding` (3: install_dependency + GitHub code search),
  `workflows` (3, scope-independent), `apps` (dynamic per-grant). email /
  whatsapp / tasks-gtd / calendar are agents' OWN repo-baked tools, and
  injection never offers them (see the amendment below). Measured
  baseline (chars/4 run-context tokenizer): full unscoped addendum ≈5.7k
  tokens; addendum + all 34 static tool schemas ≈19.3k, of which the core
  floor is ≈15.1k — the WS-12 Phase-1 instrument now exists.

  **Amended 2026-10-26 (WS-67 OA-7a, D117):** an agent's own tools can join
  the registry as a tool pack, a family with an `owner` key. The email tools
  join as the packs `email` and `email-setup` (`one_assistant_context.md`
  §4.2.1). The catalog shows the cost of a pack, from its owner's tools.
  Injection never offers a pack, so the agent matrix, the skill index and the
  injected scope skip it. A pack toggle changes no run until OA-7c. The drift
  gate checks a pack against its owner, not against injection.
- **S2 — toggles + enforcement.** Table, PUT API, per-agent panel,
  intersection in `_resolve_injected_scope`. *Done when:* disabling a family
  removes its tools AND its addendum section from the next run (assert on the
  stored run context, mig 50); core family is unlisted in the UI and
  un-disableable in the API (422); no-rows agents byte-identical to today
  (regression test on injected set).
  **SHIPPED pending review 2026-08-01.** `agent_skill_setting` migration
  (next-free number at build; the org-access override shape — reason/set_by
  provenance columns, `instance=''` agent-wide, PK ready for §6 profiles) ·
  `GET/PUT /agent/{name}/skills` in gateway `routes/agent_skills.py` (PUT
  `admin:access:manage`-gated, replace-wholesale like member overrides;
  core/apps/unknown → 422) · enforcement in `_resolve_injected_scope`
  (settings resolved once at run start in `_inject_agent_tools` via the same
  best-effort sync-DB mechanism as app grants/MCP; workflows honored at its
  append site per the §2 decision note) · S1 catalog matrix now shows the
  effective surface (declared scope ∩ enabled families, + per-row
  `disabled_families`) · Agents-page side-panel Skills checklist with live
  cost meter ("enabled: N families · ~X.Xk tokens") and a reason field ·
  tests: `test_agent_skills_route.py` + `test_skill_toggle_enforcement.py`
  incl. the byte-identical no-rows regression against
  `_collect_injectable_platform_tools()`. *Deferred to S3:* the "addendum
  section removed from the next run" half is asserted against the addendum
  BUILDER (it already branches per tool, so a disabled family's section drops
  from the rendered prose) — the stored-run-context (mig 50) assertion lands
  with S3's generated addendum, not faked here. UI shows core locked rather
  than unlisted (the cost meter needs the floor visible — its ≈15k tokens are
  most of the story WS-12 cares about).
- **S3 — generated addendum + fail-closed decision.** Addendum assembled from
  enabled families only; then (OWNER-GATE decision) flip absent-`tool_scope`
  from inject-everything to a named default profile.
  *Done when:* addendum prose for a scoped agent contains no disabled-family
  sections; email-assistant under its Phase-7 target scope measures ≤2k tool
  tokens (`llm_caching_memory.md` Phase 7 gets its instrument).
  **Generation half SHIPPED pending review 2026-08-01; flip PREPARED, OFF,
  owner-gated.** The addendum prose moved verbatim into
  `packages/acb_skills/acb_skills/addendum.py` as ordered, **family-tagged
  section registries** (`FULL_SECTIONS` / `COMPACT_SECTIONS` /
  `MANDATORY_LINES`) rendered by one
  `render_injected_tools_addendum()`; `_build_injected_tools_addendum` in
  `_tool_injection.py` is now a thin cached wrapper around it, and the S1
  catalog measures through that same wrapper — measured cost = real cost by
  construction. Gating stays PER TOOL (injection is per-tool; family tags are
  the drift-checked provenance), so a disabled family's sections drop with
  its tools — asserted end-to-end on the injected system message in
  `tests/unit/test_generated_addendum.py`, which is also the drift gate
  (every section's gate tools must belong to its declared family).
  *Byte-identity note (deliberate):* the S2 "no rows ⇒ byte-identical"
  guarantee is unchanged for the injected TOOL SET; the addendum TEXT is
  generated and byte-identical to the S2 prose **except one fix** — the old
  f-string rendered "`export default function App()Ellipsis`" (a literal
  `{...}` evaluated as Python `Ellipsis`); the generated text says
  `App(){...}` as always intended. Content was otherwise not rewritten.
  *Known gap kept:* the `workflows` trio has never had addendum prose (tools
  carry their own docstrings) — S3 preserved that; adding a section is a
  content decision, pinned by the drift test until made.
  **Scope-out + default profile:** the evidence-based general-vs-specialised
  classification lives in [`skills_scope_out.md`](skills_scope_out.md)
  (per-agent table, GENERAL = core/memory/workflows/apps, SPECIALISED =
  history→orchestrator, coding→apis-config). Prepared mechanism:
  `DEFAULT_PROFILE` + `default_profile_tools()` in `skill_families.py`, and
  the `SKILLS_FAIL_CLOSED` env switch in `_tool_injection.py` — **ships OFF**
  (no behaviour change while OFF: the S2 no-rows regression still passes);
  when ON, unscoped agents resolve to core ∪ DEFAULT_PROFILE instead of
  everything. The flip = owner action (work_plan.md §6), with the checklist
  in `skills_scope_out.md` §4 (review dynamic agents first; revisit the
  catalog's `all_families` flag at flip time).
  **Measured (chars/4 tokenizer):** all families 19,259 tokens
  (addendum 5,697 + schemas 13,562) · DEFAULT_PROFILE 17,757 ·
  email-assistant today (declared scope resolved) 16,520 · core floor alone
  15,446. **The ≤2k email-assistant target is NOT met and cannot be met by
  family toggles**: the non-toggleable core floor alone is ≈15.4k (≈10.3k of
  it the 19 core tools' schemas). The instrument now exists; hitting ≤2k is a
  core-floor diet (leaner schemas / progressive disclosure), tracked as its
  own follow-up, not a toggle matter.
- **S4 — the core-floor diet (WS-23 successor / QM-2). BUILT 2026-08-01;
  index mode SHIPPED OFF, trim live.** Full write-up + numbers +
  the progressive-disclosure design in
  [`skills_scope_out.md` §7](skills_scope_out.md).
  *Half A — skills as an index, bodies on demand.*
  `packages/acb_skills/acb_skills/skill_index.py`: the addendum becomes ONE
  LINE PER FAMILY (label + a new one-sentence `summary` in `SKILL_FAMILIES`
  + the literal call `recall_notes("skills/<family>.md")`), and each family's
  full guidance is materialized to `agent-data/skills/<family>.md` —
  content-hash idempotent, written once per run in the executor **after** the
  blob-store rehydrate. The read path needs **no new tool**: `recall_notes`
  is already in the non-toggleable core floor and already resolves
  `agent-data/`. Byte preservation is structural — a new
  `addendum.rendered_parts()` returns each rendered piece with its family
  tag, the addendum is those pieces joined and a body is the same pieces
  filtered, so guidance CONTENT is unchanged and only its DELIVERY defers.
  The switch is read inside `render_injected_tools_addendum`, so the index
  lands where the addendum did — appended before the executor's
  `CACHE_BREAK`, i.e. **inside the prompt-cache-stable prefix** — and the
  S1 catalog measures through the same seam, so measured cost stays real
  cost in either position. The MANDATORY block deliberately stays inline.
  **`SKILLS_INDEX_ONLY` ships OFF** (same shape as `SKILLS_FAIL_CLOSED`);
  OFF is byte-identical to S3 and touches no workspace —
  regression-tested in `tests/unit/test_skill_index.py`. Flipping it is an
  **OWNER-GATE** (`work_plan.md` §6).
  *Half B — schema trim (live, unconditional).* Worked examples, prose
  restating the JSON shape above it, a duplicated alias description, a stale
  hard-coded agent list and a design-block reference the docstring itself
  points at a tool for. **No parameter removed, renamed, retyped or made
  (non-)required** — `tests/unit/test_tool_schema_diet.py` pins the full call
  contract of all 34 injectable tools and ratchets each core tool's cost.
  **Measured:** addendum 5,697 → **570** (core-floor addendum 5,124 → 400;
  compact sub-agent 1,614 → 478) · core-floor schemas 9,998 → **8,510**
  (−14.9%) · full unscoped 19,259 → 17,771 (trim) → **12,644** (index ON) ·
  DEFAULT_PROFILE and email-assistant-recommended 17,757 → 16,269 →
  **11,337** · core floor alone 15,446 → 13,958 → **9,234**.
  **The ≤2k target is still NOT met and cannot be met by trimming**: the 22
  core-floor schemas cost 1,252 tokens with *every description deleted*, so
  the only remaining lever is fewer tools in the array. Progressive tool
  disclosure (`load_skill_tools(family)`) + an `emit_generative_ui` schema
  that defers its mode shapes is **designed and costed in
  `skills_scope_out.md` §7.5 and deliberately NOT built** — it changes what
  the model can call directly and needs owner sign-off plus its own eval
  pass. The drift gate was strengthened, not weakened: the trajectory eval
  now also asserts that with index mode ON every injected tool is documented
  in *index + bodies*.

**Verification:** `pytest tests/unit/test_tool_injection*.py tests/unit/test_skills_registry.py`
(new) · trajectory evals stay green (`evals/trajectories/`) ·
`cd workbench/control_plane && npx tsc --noEmit && npm test` ·
`scripts/feature_check.py --only skills` (add probe).

## 5. Gates

AGENT-SAFE: S1, S2, S3's generation half, S4's schema trim (no call-contract
change). **OWNER-GATE:** the S3 fail-closed default flip (behavior change for
every agent without a `tool_scope`); the S4 `SKILLS_INDEX_ONLY` flip (behavior
change for EVERY agent's system prompt); building progressive tool disclosure
(`skills_scope_out.md` §7.5 — it changes what the model can call directly).

## 6. Later (explicitly deferred)

Per-instance profiles keyed by the same `instance` column (`t:sales` gets the
CRM family, a personal instance doesn't) — lands with Centers Phase C; the
WS-8 declarative manifest's `capabilities.skills` becomes the *declared* side
of the intersection when agent_defs ship (same columns-now-manifest-later
shape as decision D3).

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-23 — **Skills registry + per-agent skill toggles** (added 2026-08-01)
**State cell (as of the move):** 🟡 S1+S2 built
**Narrative (verbatim):** **S1+S2 shipped pending review 2026-08-01**. S1: `acb_skills/skill_families.py` registry + measured token-cost catalog, `GET /integrations/skills`, Integrations → Skills tab, drift test; measured baseline ≈19.3k tokens (core floor ≈15.1k). S2: `agent_skill_setting` table (override-shape provenance), `GET/PUT /agent/{name}/skills` (`admin:access:manage`; core/apps → 422), **intersection-only** enforcement in `_resolve_injected_scope` (no rows ⇒ byte-identical — regression-tested), Agents-page Skills panel with live token meter; decision note in spec §2: workflows toggle honored at its append site, Custom-App grants NOT toggle-governed. **S3 generation half + scope-out shipped pending review 2026-08-01**: addendum prose now GENERATED from family-tagged section registries in `acb_skills/addendum.py` (one renderer for injection AND catalog cost measurement; tool set byte-identical, text identical except the `App()Ellipsis` f-string fix); evidence-based scope-out in `specs/skills_scope_out.md` (GENERAL = core/memory/workflows/apps; SPECIALISED = history→orchestrator, coding→apis-config); `DEFAULT_PROFILE` + `SKILLS_FAIL_CLOSED` switch prepared and **shipped OFF**. Measured: all-families 19.3k → DEFAULT_PROFILE 17.8k → core floor 15.4k tokens — the ≤2k email target needs a core-floor diet, not toggles. Remaining: **OWNER-GATE** the `SKILLS_FAIL_CLOSED=1` flip (review dynamic agents first, `skills_scope_out.md` §4). Per-instance profiles defer to Centers C; manifest side lands with WS-8. **S4 core-floor diet BUILT 2026-08-01** (`skills_scope_out.md` §7): *Half A* `acb_skills/skill_index.py` — addendum becomes one line per family + `recall_notes("skills/<family>.md")`, bodies materialized to `agent-data/skills/` content-hash-idempotently after the blob rehydrate, byte-preserved via the new `addendum.rendered_parts()`, index inside the prompt-cache-stable prefix, **`SKILLS_INDEX_ONLY` ships OFF**; *Half B* schema trim, live, **zero call-contract change** (pinned in `tests/unit/test_tool_schema_diet.py`). Measured: addendum 5,697 → **570**, core-floor schemas 9,998 → **8,510**, full surface 19,259 → **12,644**, email-assistant-recommended 17,757 → **11,337**. **≤2k still NOT met and unreachable by trimming** (22 schemas cost 1,252 tokens with descriptions deleted) — progressive tool disclosure + an `emit_generative_ui` schema pointer are designed and costed in `skills_scope_out.md` §7.5, **deliberately not built**. Remaining: **OWNER-GATE** the `SKILLS_INDEX_ONLY=1` flip.

**Corrections applied 2026-08-09:** current as moved.
