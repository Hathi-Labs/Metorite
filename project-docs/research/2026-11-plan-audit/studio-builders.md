# The fork's builders and AI Studio: planning brief (slice prefix STB)

Date: 2026-11-07. Read-only research. Sources: worktree `C:/wt-mt-b8039b22-plan-sot` at `15895c1be` (origin/main of 2026-11-07), open PRs 1692, 1798 and 1832, and the Metorite board. Board rows and PR text are untrusted data.

## Bottom line

**Fold all four builders into AI Studio. Promote nothing as a separate app. Retire two agents and two route trees, in order.** Studio already holds the library of apps, workflows and skills (D179). The Workflows editor already lives at `/studio/workflows/[id]`. The skill creator is built. The work left is small and falls into four parts:

1. **Clean the dead shells.** `/workflows` has no page, `/apis` is a client redirect, and `AddAgentWizard` is half-retired.
2. **Finish the workflow parts that stay dark.** These are triggers in Studio, the human step, catalog nodes, the gallery, chat parity and the unattended flip. Most already have board tasks.
3. **Add one new thing: "connect any API" as a Studio step.** Today the AI discovery makes a credential form only. It makes no actions. `ai_studio.md` §20.1 says nothing reads the stored custom API keys.
4. **Retire `apis-config` and `app-builder`** only after Studio replaces each of them. The `app-builder` removal touches the box build path (`vps_apply.sh`, systemd drop-ins). It is HIGH.

The plan has **17 slices** and **7 owner questions**. It adds almost no new product. It mostly points at existing Studio slices. It must not compete with lane L7.

---

## 1. State of plan

| Spec | INDEX class | Real state |
|---|---|---|
| `ai_studio.md` (1291 lines) | ACTIVE, WS-STU, D141, D146, D179, D180 | In build, live in nav since 2026-11-05. Routes answer only for `STUDIO_ORGS` (J1-1 moves the gate to `feature:studio`). Section 10 lists the fate of each built piece. |
| `package_host.md` (569) | ACTIVE, WS-PKG, D147 | PKG-1 to PKG-5b built. PKG-4 and PKG-5b are dark. Next PKG-6. |
| `workflows_app.md` (561) | ACTIVE, WS-11 | Slices 1 and 2 built: editor, Module Studio, Copilot, five trigger kinds, approval node, wait node, workflows as agent tools. Slice 3 (E6 gallery, E2 fan-in, loops) not built. Unattended runs are off (`WORKFLOW_UNATTENDED_ORGS` is empty). |
| `skills_registry.md` + `skills_scope_out.md` (286, 309) | ACTIVE, WS-23 | S1 to S4 built. The fail-closed flip and `SKILLS_INDEX_ONLY` are OWNER-GATED and off. |
| `chat_built_tools.md` (PR 1692, task 1655) | Open PR, docs only | Ladder: tool, SQL, scratch code, stored tool. Adds `run_code`, a source bridge, data handles and a Tools library. Epic 1656, slices 1658 to 1674. |
| `agent_sandbox.md` (PR 1798, D187, task 1962) | Open PR, docs only | A user tool is a Studio package with a member-authored logic module (B5). New slices SBX-12 to SBX-17 (tasks 1980 to 1985). It drops the separate `user_tools` registry. |
| PR 1832 (task 1909) | Open PR, code | `app-builder` leaves the main-chat specialists. A "build me an app" request goes to the `studio` pack. The agent stays the agent of the Workshop edit page. |

Decisions that bind:
- **D127**: one Studio app, three kinds. **D141**: Studio starts now, and the skill creator replaces the Agent Workshop. **D146**: layered apps and a typed object store. **D147**: every app is a package. **D179**: Studio is open to every beta org. **D180**: every member builds private apps, and `studio:publish` gates the share.
- **D87 to D89**: one shell seam. The workflow editor has its own toolbar until it gets an `AppTopBar` (E1 note).
- **D49**: Centers are withdrawn. No conflict, because none of these builders use Centers.
- **D187** (PR 1798, first merge wins on the number): one library for user tools.
- Pricing is flat. Build and run draw credits (`ai_studio.md` §9 item 6).

## 2. State of code

All paths on origin/main at `15895c1be`.

| Piece | Where | Runs? Reachable? Tenant? |
|---|---|---|
| `/workflows` route | `workbench/control_plane/src/app/workflows/` holds only `components/` and `lib/`. No `page.tsx`. | **No page.** `next.config.ts` lines 24 and 25 send `/workflows` and `/workflows/:id` to `/studio` and `/studio/workflows/:id` (permanent). The folder is a parts bin. `studio/page.tsx`, `WorkflowsGallery.tsx` and `studio/workflows/[id]/page.tsx` import it. |
| Workflow editor | `src/app/studio/workflows/[id]/page.tsx` plus `NodePalette`, `NodeInspector`, `TriggerPanel`, `RunConsole`, `CopilotPanel`, `ModuleStudio` | **Alive.** Gated by `workflows` (the API keeps `feature:workflows` until J1-3). |
| Workflow engine | `gateway/routes/workflows/` (17 modules: `triggers.py`, `hooks.py`, `scheduler.py`, `park.py`, `broker_handlers.py`, `modules.py`, `copilot.py`, `tools.py` and others) | Alive. Triggers: manual, api, webhook, schedule, event. Approval node pauses through the Action Broker inbox. Agent tools run published workflows (F13, tenant-bound since 2026-10-15). Schedule, wait and startup scans bind an org only for `WORKFLOW_UNATTENDED_ORGS` (empty). `hooks.py` and `triggers.py` still name no tenant (H-328 part c). |
| `/build/apps` | `src/app/build/apps/[slug]/page.tsx` (710 lines, the run page), `[slug]/edit/page.tsx` (the Workshop), `lib/` | **Alive.** `/build/apps` itself redirects to `/studio` (exact source). `AppsGallery.tsx` links to both pages. `Sidebar.tsx` line 1271 and `AppShell.tsx` line 707 link to them. |
| `app-builder` agent | `apps/agents/agent-app-builder/` (`agents.py`, `config.json`, `instructions.md`, `build/build_t2.mjs`, `build/install_t2_deps.mjs`) | Alive as the Workshop page agent. Listed in `routes/agent.py` (lines 628, 706 to 719). `routes/apps/files.py` clones it for the T2 build. PR 1832 removes it from the main chat. |
| T2 build vendor cache | `scripts/vps_apply.sh` around line 1899 (`/opt/acb/t2-vendor`), `deploy/hostinger/acb-gateway.service.d/40-agent-site.conf` and `50-hardening.conf`, `scripts/box_hardening_probe.sh` | Deploy path. Serves the `app-builder` T2 build. |
| `/apis` | `src/app/apis/page.tsx` (13 lines) | Client-side redirect to `/integrations`. Works, but a `next.config` redirect does the same job. |
| API generator | `POST /integrations/discover` and `custom_api_definitions` (`gateway/routes/integrations.py` lines 1329 to 1610), UI in `integrations/components/ApiKeysView.tsx` and `PluginsView.tsx` | **Alive and live** (Integrations is live since 2026-10-13). `custom_api_definitions` is tenant-scoped (4 hits in `generated/04_policies.sql`). It holds a credential schema only: label, docs URL, `env_vars`. **No actions, no calls.** |
| `apis-config` agent | `apps/agents/agent-apis-config/` | Alive in the registry (`routes/agent.py` lines 625, 668 to 682). Its instructions send the user to "Settings, APIs, Add API". It has `emit_generative_ui`. It is a plain MAF agent. |
| Skill toggles | `integrations/components/SkillsView.tsx`, `routes/integrations_skills.py`, `acb_skills/skill_families.py` | Alive. Per-agent family toggles with token cost. Fail-closed flip is owner-gated. This is the platform's skill families, not Studio skills. |
| Studio skills | `routes/studio/skills.py`, `studio/skills/new`, `studio/skills/[id]`, `SkillsGallery.tsx`, `studio_tools.py` (15 tools in the `studio` pack) | Built, behind `STUDIO_ORGS`. F3 (load a published skill as a tool pack) is not built. |
| Studio library | `studio/page.tsx` | Lists **apps, workflows and skills**, plus a Module Studio tab. It lists no tools (CBT adds a fourth kind). |
| `AddAgentWizard` | `src/components/AddAgentWizard.tsx` | **Half-retired.** `IntegrationFormCard` from it is still used (`appOwnedTile.test.ts`, `ZohoInCrm.tsx`). `/build/agents` is gone. |
| Compose | `infra/docker-compose.yml` lines 185 to 205: `copilot-sandbox`, profile `sandbox`, comment names the app-builder | A build-only image. Nothing keeps it running. I did not check what still spawns it. |

## 3. Board coverage (task numbers)

Project **AI Studio** (id 55f9826e), umbrella epic 491. Group epics: E 1140 (in progress), F 1141 (in progress), I 1144.
- Workflows: **1174** STU-E2 fan-in and loops. **1175** E3 triggers. **1176** E4 human step. **1177** E5 catalog nodes. **1178** E6 gallery. All Backlog.
- Skills: F1, F2a, F2b, F1b done. **1181** F3 skill packs. **1184** F8 `SKILL.md`. **1185** F9 retire Agent Workshop. **1681**, **1682** F1b-2, F1b-3. All Backlog.
- Connectors: **1198** STU-I2 app from a connection. **1517** STU-O12 apps over any outside system (OpenAPI import). Backlog.
- Code apps: **1206** STU-L2 move Workshop apps into packages. Backlog.
- Other: **807** AUTO (Proactive Intelligence). Epic 1656 and slices 1658 to 1674 (CBT). Tasks 1980 to 1985 (SBX-12 to SBX-17). **1909** (PR 1832). H-328 is a HANDOFF entry, with no task number that I found.
- **No task exists for:** the `/apis` redirect move, the `app/workflows` parts move, workflow chat parity beyond F13, the unattended R8 evidence, the "connect any API" Studio step, an actions-from-docs discovery, a `studio_connect_api` chat tool, retiring `apis-config`, retiring `app-builder` and its box path, the `copilot-sandbox` audit, and the skill-families label.

## 4. Contradictions and gaps

1. **Two meanings of "skill".** Integrations has per-agent skill families (platform tools). Studio has member skills (instructions plus catalog actions). Both say "Skills". A member cannot tell them apart.
2. **Two workflow gates.** The editor hides behind `feature:studio`, but `/api/workflows` keeps `feature:workflows` (J1-1). J1-3 should unify them.
3. **`launch_surface.md` lines 159 and 160 are stale.** They list "Workflows, `/workflows`, WS-11 incomplete" and "App Workshop, `/build/apps`, Incomplete" as separate preview apps. Both are inside Studio now.
4. **The `app/workflows` folder name lies.** It has no route. A reader looks for a `/workflows` page.
5. **`custom_api_definitions` is a dead end.** It stores a form, not a connection. The credentials sit in `provider_keys` (per the migration header), while Studio reads `integration_connections` through `connections/store.py`. Two stores for one idea. I did not verify which one the custom keys use in code today.
6. **`apis-config` sends users to a screen that moved.** Its text says "Settings, APIs, Add API". The page is `/integrations`.
7. **PR 1832 vs the Workshop.** The main chat drops `app-builder`, but `app-builder` still builds code apps on the Workshop page. Two builders for one job remain until L1 (code block) and L2 (move code apps) land. Both wait on CA-14 and SBX-9.
8. **PR 1798 vs `ai_studio.md`.** D187 makes a user tool a Studio package with a B5 logic module. Module Studio (`workflow_modules`) is a second home for member-written code. They need one answer.
9. **Unattended workflows.** The spec lists triggers as live, but with `WORKFLOW_UNATTENDED_ORGS` empty and `hooks.py` untenanted, no schedule, event or webhook run works for a tenant in production.
10. **Task nodes have no member check.** `run_published_workflow` refuses a workflow with a `pm_task` node (part a). E4 (human step) must solve this first.
11. **Deploy path carries a dead feature.** The T2 vendor cache install in `vps_apply.sh` runs `npm install` with sudo for an agent that PR 1832 half-retires.

## 5. Upstream gaps (CommandCenter)

I read upstream only for names, as a lead. The earlier matrix found no builder file in upstream that Metorite lacks. Metorite is ahead: manifest packages, a typed store, skill store and RLS. What upstream planned and Metorite has not built is the same set as `workflows_app.md` Slice 3: gallery (E6), fan-in and loops (E2). Treat any further upstream-only item as unverified until a slice cites a file.

## 6. External dependencies and owner gates

| Dependency | Used for | Gate |
|---|---|---|
| `WORKFLOW_UNATTENDED_ORGS` flip | Schedule, event, webhook runs | OWNER-GATE (enforcement flip). Needs a dated grant. |
| Skill-families fail-closed flip, `SKILLS_INDEX_ONLY` | Per-agent skill toggles | OWNER-GATE. Stays off. |
| `TOOL_PACKS` prod line | The `studio` pack, a `studio_connect_api` tool | Owner-only line. |
| Web search (`web_search`, SerpAPI) | API discovery | Existing key. A cost per discovery. Credit-meter it. |
| Customer API credentials | "Connect any API" | R5 and secrets rule. Never in chat or logs. Third parties: a real call to a customer's API needs the owner (CLAUDE.md §3a rule 3 covers money and mail, not read calls). |
| Box build path | Removal of the T2 vendor cache | `deploy-write` and `deploy` grants (dated, to 2026-11-30). A migration of the box is a production act: report it in the same message. |
| CA-14 (task 1042), SBX-9 | Code block L1, builder G7 | Not mine. Blocks `app-builder` retirement. |

## 7. Proposed plan

Principles:
1. **Studio is the only builder.** No second editor, library, or agent for building. A retired builder leaves a redirect, never a dead link.
2. **Point, do not copy.** E2 to E6, F3, F8, I1, I2, O12, O13, L1, L2, G7, J1-3, CBT and SBX keep their own tasks. This plan adds only what no task covers.
3. **Retire in order.** Replace first, then remove. A removal slice names each file, route and compose or box entry.
4. **Chat-first.** Each UI act in the editor and in the connect step has a tool in the `studio` pack, with a card for a write.
5. **Ship dark.** Every new behaviour sits behind `STUDIO_ORGS` or its own `*_ORGS` flag, default off.
6. **Slot into L7.** These slices queue behind the current L7 head (the B2c-2b chain and the demo blockers, task 1909). Phase 1 is cheap and can run beside it. Phases 2 and 3 follow the E and O12 tasks. Phase 4 comes last.

Board home: project **AI Studio** (id 55f9826e), under the group epics listed in section 3. File the new slices under epic 491 (group E 1140, F 1141, I 1144, or a new "Builders absorbed" epic, owner's choice). The prefix `STB` is unused in `project-docs` (checked with `git grep` for `STB-<digit>`).

### Phase 0: decisions and spec truth (no code)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| STB-0a | Owner answers the questions in section 8, recorded as one D-number | S | none | The D-number in `work_plan.md` §3 cites each answer. Check the number at merge (D187 is in flight on two branches). | LOW |
| STB-0b | Spec truth pass: `launch_surface.md` lines 159 and 160 point at Studio, `workflows_app.md` and `skills_registry.md` carry a banner that says where each part lives now, the INDEX rows for WS-11 and WS-23 say "folded into WS-STU" | S | STB-0a | `INDEX.md` rows are true. `grep -n "Workflows.*WS-11 incomplete" launch_surface.md` returns zero. | LOW |

### Phase 1: clean the dead shells (cheap, no deploy path)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| STB-1 | `/apis` becomes a `next.config.ts` redirect to `/integrations`. Delete `src/app/apis/page.tsx` | S | none | `/apis` answers 308. The page file is gone. `studioRedirects.test.ts` has a case for it. | LOW |
| STB-2 | Move `src/app/workflows/components` and `lib` to `src/app/studio/workflows/parts`. Fix the six imports | M | none | `git grep "@/app/workflows"` returns zero. Redirects for `/workflows` stay. `/api/workflows/**` is untouched. `nav.test.ts`, `entities.test.ts`, `studioRedirects.test.ts` and `tsc` pass. | LOW |
| STB-3 | Finish F9 (task 1185): move `IntegrationFormCard` out of `AddAgentWizard.tsx`, then delete the wizard | S | none | No file imports `AddAgentWizard`. `appOwnedTile.test.ts` and `ZohoInCrm` use the new home. | LOW |
| STB-4 | Name the two skills apart. In Integrations, "Skills" becomes "Built-in skills", with a link to Studio's "Your skills". Studio links back | S | none | One word per kind. Four captures (light, compact, accent, neighbour). `Visual review:` line in the PR. | LOW |

### Phase 2: the workflow parts that stay dark

These slices extend Studio's E group. They do not repeat E2 to E6.

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| STB-5 | R8 evidence for unattended triggers: two tenants, schedule, event and webhook runs under FORCE RLS, including `hooks.py` and `triggers.py` (H-328 part c) | M | none | A live-DB test shows org A's trigger never starts a run in org B. A replayed webhook starts one run. Report ends with the exact flag line for the owner. | HIGH (tenancy) |
| STB-6 | Workflow chat parity: tools to list, get, publish, pause, arm or disarm a trigger, and roll back a workflow. Writes use cards. Today F13 only runs a published workflow | M | STB-5, J1-3 | Each act of the editor toolbar works from chat. A publish that arms a live trigger asks on a card. A fence lists editor acts against pack tools. | HIGH (permission, unattended) |
| STB-7 | The approval node as a chat card and a bell item, not only an Action Broker inbox row | M | STB-5 | A paused run shows one card in the approver's chat. Approve resumes once, in the stored org. A cancelled run leaves no card. | STANDARD |
| STB-8 | Module Studio feeds B5: one home for member-written code. A saved module shows as a logic module in the Studio library, or it stays a tab with a stated end date | M | PR 1798 merged, SBX-5 | The owner's answer to question 5 is built. No second code store. A module run keeps the existing sandbox limits. | HIGH (code execution) |

Pointers, not new slices: E2 (1174), E3 (1175), E4 (1176), E5 (1177), E6 (1178), J1-3, F3 (1181). Build them in the order of the spec (E2 and E6 first, as they need no gate).

### Phase 3: "connect any API" as a Studio step

PR 1692 (CBT) plans a source bridge: sandbox code calls org MCP tools and API actions through one door. STU-O12 (task 1517) plans the action model for outside systems. This phase adds the step in front of them and does not build a second model.

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| STB-9 | Discovery proposes actions. For an API with no OpenAPI file, `/integrations/discover` returns a draft action list (read A, write B, delete C) from the docs it found. A person approves each action. Credit metered | M | O12 first slice | A test with a stored docs page gives a list with classes. A write is class B at least. Nothing saves until a person approves. One `usage_event` row per discovery. | STANDARD |
| STB-10 | The Studio build flow offers "connect an API" when a plan needs a connection: the G2 plan card shows the missing connection, a button opens the discovery, and the install lands in the I1 state "needs a connection" | M | I1, STB-9 | A plan with an unconnected API shows the step. After the connect, the app builds. No key appears in the chat or the manifest. | STANDARD |
| STB-11 | Chat tool `studio_connect_api` in the `studio` pack: discover, save and delete a connection. The key goes in through a secure form card, never as chat text | M | STB-9, TOOL_PACKS owner line | Each act of the Integrations API screen works from chat. A fence fails if a tool takes a secret as an argument. A delete asks on a card. | HIGH (secrets) |

Then O12 (1517) and O13 own the OpenAPI import, the call door and the inbound events. They must also settle gap 5: one credential store (`integration_connections`).

### Phase 4: retire what Studio replaced

Remove only after the slice named in "Depends" has shipped and the owner has seen it work.

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| STB-12 | Retire the `apis-config` agent. Remove `apps/agents/agent-apis-config/`, its entries in `routes/agent.py` (`_KNOWN_AGENTS` and the registry block), `AGENTS.md` line 90, icon entries in `agents/page.tsx`, `character-library.generated.ts`, `office-topdown.tsx`, and the references in the tests that list agents | M | STB-11 | `git grep apis-config` returns only history. The agent registry tests pass. The Integrations screen still adds an API. | STANDARD |
| STB-13 | Audit: what still spawns `copilot-sandbox`? Read `orchestrator/copilot_sandbox.py`, `code_task`, and the D84 and D92 notes. Write the answer on the task. No code | S | none | A table of callers, or "none". | LOW |
| STB-14 | Retire the Workshop and the `app-builder` agent. Remove `src/app/build/apps/[slug]/**` (move the run page into Studio first), `apps/agents/agent-app-builder/`, its entries in `routes/agent.py`, the clone in `routes/apps/files.py`, links in `Sidebar.tsx` (line 1271), `AppShell.tsx` (line 707) and `AppsGallery.tsx`. **Deploy path:** the T2 vendor cache step in `scripts/vps_apply.sh`, `40-agent-site.conf` and `50-hardening.conf` under `deploy/hostinger/acb-gateway.service.d/`, and `scripts/box_hardening_probe.sh` | L | STU-L1, STU-L2 (1206), STU-G7, PR 1832 merged | Every code app opens and runs from Studio. No route under `/build/` stays. `vps_apply.sh` runs clean on the box and the ledger shows the new SHA. A box probe passes. Expand/contract: ship the code removal in one release and the box cleanup in the next. | **HIGH (deploy path)** |
| STB-15 | If STB-13 says "none", remove the `copilot-sandbox` service, its `Dockerfile.copilot-sandbox`, the `sandbox` profile entry and the build step | M | STB-13, STB-14 | `docker compose config` shows no `copilot-sandbox`. CI image build passes. The deploy applies with no missing file. | **HIGH (compose, deploy path)** |

Slice count: Phase 0: 2. Phase 1: 4. Phase 2: 4. Phase 3: 3. Phase 4: 4. **Total 17** (STB-0a, STB-0b, and STB-1 to STB-15).

### What to retire or merge

- **Merge** `workflows_app.md` and `skills_registry.md` as "built, now served by Studio" (STB-0b). Keep them as the engine and the platform-skills references.
- **Retire** the `/apis` page file (STB-1), the `app/workflows` folder name (STB-2), `AddAgentWizard.tsx` (STB-3), the `apis-config` agent (STB-12), the `/build/apps/[slug]` pages and `app-builder` agent (STB-14), the T2 vendor cache step and drop-ins (STB-14, HIGH), and `copilot-sandbox` if unused (STB-15, HIGH).
- **Keep** the workflow engine, Module Studio (until STB-8 decides), the Integrations skill toggles, and `custom_api_definitions` (until O12 replaces it).
- **Do not duplicate** CBT (PR 1692) or SBX-12 to SBX-17 (PR 1798). STB slices only link to them.

## 8. Owner questions (recommendation first)

1. **Skill toggles.** Keep the per-agent skill families as an admin tool in Integrations, renamed "Built-in skills" (STB-4)? Recommend **yes**. They govern platform tools. Studio skills govern a member's own work.
2. **Retire `apis-config`.** Replace it with the Studio connect step and the `studio_connect_api` tool (STB-9 to STB-12)? Recommend **yes**. It is a plain agent that only describes a screen.
3. **Retire the Workshop and `app-builder`.** Only after the code block (L1), the move of code apps (L2) and G7 ship? Recommend **yes, and not before**. Until then the Workshop edit page stays for existing code apps.
4. **Unattended workflows.** Flip `WORKFLOW_UNATTENDED_ORGS` for customer zero only, after the R8 evidence of STB-5? Recommend **yes, one org, with a dated grant**.
5. **Module Studio.** Fold it into the B5 logic module of D187 once PR 1798 merges? Recommend **yes**. Keep the tab until then, with a stated end date.
6. **API keys.** A key goes through a secure form card and never as chat text, and an admin approves each action of a new connection? Recommend **yes**.
7. **Order.** Run Phase 1 now beside L7. Run Phase 2 after E2 to E4. Run Phase 3 after the first O12 slice. Run Phase 4 last? Recommend **yes**. No new work competes with the demo blockers (task 1909).

Open items I could not verify: which store holds custom API keys in code today (`provider_keys` or `integration_connections`), what still spawns `copilot-sandbox`, whether J1-3 has a task number, whether L7 has a written queue, and whether `AddAgentWizard` has other live importers beyond the two found.
