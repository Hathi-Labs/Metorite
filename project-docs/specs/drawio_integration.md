# draw.io Integration — Architecture, Plan & Roadmap

> ⚠️ **⏸ PARKED BY OWNER (D25.7) (2026-08-10 consolidation, D26).** No work dispatches from
> this document — it re-enters the board when a real need pulls it; re-verify anchors at un-park. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Type:** Master design + ticketed implementation plan (merged from the former
> `drawio_integration_review.md` + `drawio_integration_plan.md`).
> **Date:** 2026-06-29 · **Status:** Proposed — ready to schedule · **Owner:** TBD (backend + frontend)
> **Interface contract (separate, freeze-gated):** [`drawio_diagram_svc_contract.md`](./drawio_diagram_svc_contract.md)
> — the precise wire spec for `diagram-svc`, the `create_diagram` tool, and `DrawioEditor`.
> **Related specs:** [`archive/artifact_viewer.md`](./archive/artifact_viewer.md), `chat_ux.md`, [`mcp_plugin_integration.md`](./mcp_plugin_integration.md).
> **Reference repos studied:** `jgraph/drawio`, `jgraph/docker-drawio`, `jgraph/drawio-mcp`
> (+ `lgazo/drawio-mcp-server`), draw.io embed-mode & diagram-generation docs.
>
> **Goal:** Every agent (chat + email, MAF + Copilot) can **create clean, editable `.drawio`
> files headlessly** by authoring **Mermaid** as the interim format; the operator can **open and
> edit those files in the browser** as if in the native draw.io editor — from the artifact viewer,
> a full-screen tab, and inline in chat.

> **Update 2026-08-01 (doc-truth pass):** **nothing here is built** (verified — no `diagram-svc`
> service, no `DrawioEditor.tsx`, no `drawio` compose service, no `create_diagram` tool). Owner is
> still TBD. All file:line anchors in this document predate the `apps/` → `apps/services/`
> restructure and the executor extractions (e.g. `_inject_agent_tools` / `_inject_mcp_servers` now
> live in `apps/services/orchestrator/orchestrator/_tool_injection.py`, and MCP injection targets
> `agent._mcp_servers`, not `_default_options` as §1.4 states) — **re-verify every anchor at
> dispatch**. Tracked as `work_plan.md` WS-22.

---

## 0. TL;DR

Do **not** make the draw.io MCP server the backbone. Both the official `@drawio/mcp` and
`lgazo/drawio-mcp-server` drive a *live* draw.io browser tab through a browser extension over
WebSocket — they cannot run headless on the VPS, and (decisively) MCP servers are only injected
into **Copilot-SDK** agents, never into **native-MAF** agents like the email assistant (§3).
Instead, add a **platform-injected `create_diagram` tool** that sits next to `write_artifact`, so it
reaches *every* agent. Agents author in **Mermaid** (or simplified mxGraph XML); a small **headless
draw.io "render oracle"** (`diagram-svc`) converts that into a properly laid-out `.drawio` plus an
`.svg`/`.png` preview — this is where the "MMD-in-between for clean arrow routing" instinct is exactly
right: draw.io's own layout engine does the routing. The generated `.drawio` flows through the
**already-built** artifact pipeline (`write_artifact` → `artifact_created` SSE → `ArtifactCard` →
`ArtifactViewerModal`). For editing, self-host **docker-drawio** and mount its **embed editor**
(`embed=1&proto=json`) as a new `.drawio` branch in `ArtifactViewerModal`, saving edited XML back
through the *existing* `PUT /agent/workspace/{session}/file` endpoint. The chat AG-UI gets the `.svg`
preview inline for free. **~70% of the plumbing already exists**; the net-new work is the generation
tool, the render oracle, and one editor branch in the viewer.

---

## 1. What already exists (reusable as-is)

The platform is further along than the planning specs imply — three subsystems are shipped and
directly load-bearing.

### 1.1 Artifact pipeline — fully built, end-to-end
| Stage | Where | Status |
|---|---|---|
| Generate file | `acb_skills/write_artifact.py` — `write_artifact(path, content, encoding)` + `share_artifact(path)`, **injected into every agent** | ✅ |
| Storage | `{agents_clone_dir}/repos/{agent}/{inputs,outputs,agent-data}/`; metadata + `workspace_path` on `chat_session` | ✅ |
| Notify UI | fire-and-forget `CUSTOM` SSE `artifact_created`/`artifact_updated` `{path,size,sha256,mime_type}` | ✅ |
| Serve bytes | `GET /agent/workspace/{session}/file?path=` (50 MB cap, traversal-guarded, MIME-typed) | ✅ |
| **Edit-in-place** | `PUT /agent/workspace/{session}/file?path=` `{content, encoding}` (restricted to `inputs/outputs/agent-data/`) | ✅ |
| Browse | `GET /agent/workspace/{session}` tree + global `GET /agent/artifacts` | ✅ |

→ An agent that produces a `.drawio` needs *zero* new backend plumbing to surface and persist it; saving operator edits back already works.

### 1.2 Artifact viewer — a typed dispatcher ready for a new branch
- `ArtifactViewerModal.tsx` has a `classify(file)` (~`:64`) → `ViewerState` union with per-type renderers (markdown/code/PDF/DOCX/**SVG images**/CSV/TXT/binary) and **inline editing** (markdown/code) wired to `PUT`.
- `ArtifactCard.tsx` renders inline-in-chat cards on `artifact_created` and **already previews SVG inline** → a generated diagram's `.svg` sibling previews for free.
- `ArtifactSidebar.tsx` lists the workspace tree and merges live `artifact_*` events.

→ `.drawio` view/edit is a *new branch* in an existing dispatcher, not a new subsystem.

### 1.3 Sandboxed-iframe precedent — `MessageContent.tsx`
The email client already renders untrusted HTML in a sandboxed `<iframe srcDoc>` with CSP, height auto-sizing via `ResizeObserver`, and an image proxy — the exact pattern (minus the script ban) for embedding the draw.io editor iframe over `postMessage`.

### 1.4 MCP facility — scaffolded (~60%), but the wrong tool here
- `mcp_servers` table (`infra/postgres/13_mcp_servers.sql`), CRUD at `/integrations/mcp`, runtime injection via `_inject_mcp_servers()`; stdio + http-sse; UI tab is a skeleton.
- **Critical limitation (verified):** injection writes only to `agent._default_options["mcp_servers"]` (`executor.py:849-858`), a **Copilot-SDK** field. Native-MAF agents (the email assistant uses `OpenAIChatCompletionClient` + `agent_framework.Agent`) have no such dict, so the `isinstance(opts, dict)` guard makes MCP injection a **silent no-op** for them.

---

## 2. What the draw.io ecosystem offers (external facts)

- **2.1 The draw.io MCP servers are live-editor remote controls, not generators.** `@drawio/mcp` and `lgazo/drawio-mcp-server` connect to an open draw.io tab via a browser extension over WebSocket and CRUD shapes on the *visible* canvas. They require a human-driven browser, are **not headless**, and (per §1.4) wouldn't reach MAF agents anyway → **not the backbone**; possible later add-on for desktop power users (§13).
- **2.2 Recommended generation formats (official docs):** emit **uncompressed, simplified `<mxGraphModel>` XML** (never compressed — token waste + unvalidatable); full `<mxfile>` only for multi-page. Also accepts **CSV import** (data-driven) and **Mermaid**. **Routing/layout is applied by draw.io, not the LLM** — pass a `layout` hint (`verticalFlow`, `horizontalFlow`, `verticalTree`, `organic`, …); the agent never hand-places coordinates.
- **2.3 Embed mode is the linchpin for editing.** `…/?embed=1&proto=json` exchanges JSON over `postMessage`:
  - Editor → host: `{event:"init"}`, `{event:"save", xml}`, `{event:"autosave", xml}`, `{event:"exit"}`.
  - Host → editor: `{action:"load", xml}` **or** `{action:"load", descriptor:{format:"mermaid", data}}`, `{action:"export", format:"xmlsvg"|"png"}`, `{action:"layout", layouts:[…]}`.
  - Runs **entirely client-side** (XML stays in the browser) — but the app JS/origin is third-party, so **self-host `docker-drawio`** for privacy, offline reliability, and CSP control. The `descriptor:{format:"mermaid"}` load means **draw.io itself converts Mermaid and lays it out** — the cleanest routing, the same engine the operator edits in.

---

## 3. Core architectural decision

> **Generation = a platform-injected tool. Conversion/routing = draw.io's own layout engine, run
> headless. Edit/view = self-hosted draw.io embed in the existing artifact surfaces.**

- **Why a platform tool, not MCP (decisive):** `_inject_agent_tools` (`executor.py:504-756`) appends to **both** MAF `agent.tools` *and* Copilot `agent._tools`, so `create_diagram` reaches **all** agents incl. the native-MAF email assistant. MCP reaches only Copilot agents and needs a live browser. The email-agent requirement alone rules MCP out as the backbone.
- **Why Mermaid-first authoring:** LLMs emit Mermaid reliably (constrained grammar, abundant training data); handing it to draw.io's layout engine yields clean orthogonal routing with no model-computed coordinates. Keep a direct **mxGraph-XML** path for specific stencils (AWS/GCP/Azure) or exact styling, and **CSV** for large data-driven graphs (org charts, dependency lists).

---

## 4. Architecture & generation pipeline

```
 AUTHOR (any agent)            CONVERT (headless, server-side)            STORE                 EDIT (browser)
 ───────────────────          ────────────────────────────────          ─────                 ──────────────
 create_diagram(              ┌───────── diagram-svc ──────────┐         write_artifact:       ArtifactViewerModal
   spec=<mermaid>,    ──────► │ headless Chromium + self-hosted │ ──────► outputs/x.mmd   ────► .drawio branch →
   format="mermaid",          │ draw.io (embed proto=json):     │         outputs/x.drawio       <DrawioEditor> iframe
   layout=…, title=…)         │  load{descriptor:mermaid}       │         outputs/x.svg          (embed=1&proto=json)
                              │  → draw.io lays out + routes    │              │                      │  save/autosave
        ▲                     │  export{xmlsvg} → xml + svg     │              │                      ▼  {event:"save",xml}
        │ self-heal on        │  export{png}    → png           │       artifact_created SSE     PUT /agent/workspace/
        │ validation fail     └─────────────────────────────────┘       → ArtifactCard (svg)    {session}/file?path=…
        └──────────────────────────── validate (well-formed; edges resolve) ◄──┘                 (existing endpoint)
```

**Two consumers, one draw.io deployment.** The self-hosted `jgraph/drawio` web app serves *both* the
headless render oracle (server-side conversion) *and* the browser embed editor (operator editing) →
identical layout/routing, no "re-layout surprise" on open.

### 4.1 The render oracle — two implementation tiers
- **Recommended (high fidelity): `diagram-svc` sidecar = headless Chromium (Playwright/Puppeteer) + self-hosted docker-drawio.** Loads `?embed=1&proto=json`, sends `{action:"load", descriptor:{format:"mermaid"}}` (draw.io converts + lays out), then `{action:"export", format:"xmlsvg"}` and reads back mxGraph XML + SVG. One engine for generation *and* editing → generated and hand-edited diagrams look identical. Exposed as a tiny internal HTTP API (see the contract doc).
- **MVP fallback (lighter): `mermaid-cli` (mmdc) for `.svg`/`.png` + `mmd2drawio` (Python) for `.drawio`.** Faster to ship; routing fidelity "good" not "draw.io-native." Decided at the ST-DRW-02 gate; **the `diagram-svc` API stays identical so the swap is internal**.

### 4.2 Validation / self-heal
Before `write_artifact`, the tool confirms the XML is well-formed and every edge `source`/`target` resolves to an existing node id; on conversion error it **returns the error to the agent to retry** rather than emit a broken `.drawio`. (Mirrors draw.io's own "generate & validate" guidance.)

### 4.3 Tool surface
`create_diagram(spec, *, format="mermaid", layout=None, title, page_title=None) -> {drawio_path, svg_path, mmd_path}` — injected in `_inject_agent_tools` alongside `write_artifact`. Optional `edit_diagram(path, instructions)` later. Precise signature/return/self-heal semantics: **see the contract doc §2**.

---

## 5. View & edit surface (operator)

- **5.1 `ArtifactViewerModal` `.drawio` branch:** extend `classify()` → `ViewerState.drawio`; render `<DrawioEditor>` (`<iframe src="{SELF_HOSTED_DRAWIO}/?embed=1&proto=json&…">`). On `{event:"init"}` → fetch bytes + `postMessage {action:"load", xml}`; on `{event:"save"|"autosave", xml}` → `PUT /api/agent/workspace/{session}/file?path=…` (**existing save path, no new backend**), debounced. Unlike the email iframe, the editor **needs scripts** — isolate by **origin** with `sandbox="allow-scripts allow-same-origin"` scoped to the self-hosted draw.io domain (not `script-src 'none'`).
- **5.2 Full-screen `/diagram` route:** `/diagram?session=…&path=…` mounts the same `<DrawioEditor>` chromeless ("native-feel" full editor); the modal/card "Open in new tab" links here.
- **5.3 Read-only fast path:** the stored `.svg` renders instantly in the modal's image branch and in `ArtifactCard` — boot the embed editor only on "Edit".

`DrawioEditor` props, embed URL, message-bridge table, and origin check: **see the contract doc §3**.

---

## 6. Chat AG-UI embedding
- **Inline preview (free today):** because `create_diagram` also writes a `.svg`, the existing `ArtifactCard` SVG branch previews the diagram the moment `artifact_created` fires. Make the card `.drawio`-aware so its actions are **Edit** (§5.1 modal) and **Open** (§5.2 route).
- **Inline embedded editor (optional):** an "Expand" affordance mounts the embed iframe in the message for quick tweaks without leaving chat — same `<DrawioEditor>`, lightbox/read-only by default.

---

## 7. Email-agent specifics
Recipients can't open `.drawio`, so the email path embeds an **image**: the email assistant calls `create_diagram(...)`, takes the `.svg`/`.png`, and embeds it **inline** in the draft (cid attachment / data URI) via the existing compose/draft attachment flow (`ComposePanel.tsx`, `drafting.py`), optionally attaching the `.drawio` for recipients who want to edit. Because `create_diagram` is platform-injected, the MAF email agent gets it with **no agent-specific code** — the whole reason we reject the MCP path.

---

## 8. Components & reuse matrix

| # | Component | New/Reuse | Location |
|---|---|---|---|
| C1 | **Self-hosted draw.io** (embed editor + oracle target) | New (infra) | `infra/docker-compose.yml` — `drawio` service, `diagrams` profile |
| C2 | **`diagram-svc`** — headless render oracle (`POST /render`, `/validate`) | New (service) | `apps/diagram-svc/` (Node + Playwright) |
| C3 | **`create_diagram` tool** (+ optional `edit_diagram`) | New (tool) | `packages/acb_skills/acb_skills/create_diagram.py`; injected in `executor.py` |
| C4 | **`DrawioEditor`** React component (embed iframe + postMessage bridge) | New (frontend) | `workbench/control_plane/src/components/DrawioEditor.tsx` |
| C5 | **`ArtifactViewerModal` `.drawio` branch** + `/diagram` full-screen route | Modify + new route | `src/components/ArtifactViewerModal.tsx`, `src/app/diagram/page.tsx` |
| C6 | **`ArtifactCard` `.drawio` awareness** (svg preview + Edit/Open) | Modify | `src/components/ArtifactCard.tsx` |
| C7 | **Email inline-image embed** | Modify | email draft/compose path (`drafting.py`, `ComposePanel.tsx`) |
| — | Artifact storage, SSE, `PUT` save, workspace API | **Reuse as-is** | `write_artifact.py`, `routes/workspace.py`, `api/agent/workspace/...` |

---

## 9. Tickets

### Phase 0 — Infra & conversion spike
**ST-DRW-01 · Self-host draw.io (infra, 0.5d)** — Add a `drawio` service (`jgraph/drawio:<pinned>`) to `infra/docker-compose.yml` under a `diagrams` profile; internal origin (reverse-proxied); pin the image; disable external plugin fetch; `DRAWIO_*` config so it runs offline (no `*.diagrams.net` calls). **Done when:** `…/?embed=1&proto=json` loads from the self-hosted origin and emits `{event:"init"}`.

**ST-DRW-02 · Render-oracle spike + path decision (backend, 1d)** — Prototype `diagram-svc`: a warm headless Chromium (Playwright) page against C1's embed. Prove `load{descriptor:mermaid}` → `export{xmlsvg}` → laid-out mxGraph XML + SVG. **Decision gate:** confirm fidelity/perf on the 4 GB VPS; if headless is too heavy for early delivery, fall back to the **MVP converter** (`mmd2drawio` + `mmdc`) and revisit — keeping the `diagram-svc` API identical so the swap is internal. **Freezes the contract doc.**

### Phase 1 — Headless generation (the core unlock)
**ST-DRW-03 · `diagram-svc` render API (backend, 2d)** — `POST /render {spec, format:"mermaid"|"xml"|"csv", layout?, page_title?} → {drawio, svg, png}`; `POST /validate {drawio} → {ok, errors[]}` (well-formed XML + every edge resolves). Page pool + concurrency cap + per-request timeout + healthcheck. **Done when:** a Mermaid flowchart returns a clean, validated `.drawio` + SVG. *(Wire spec: contract doc §1.)*

**ST-DRW-04 · `create_diagram` platform tool (backend, 1.5d)** — calls `diagram-svc`, **validates**, self-heals on failure (never emits a broken file), then `write_artifact`s `outputs/<title>.{mmd,drawio,svg}`. Register in `_inject_agent_tools`. **Done when:** chat *and* email agents can call it and an `artifact_created` card appears. *(Signature: contract doc §2.)*

**ST-DRW-05 · Agent guidance + tests (backend, 0.5d)** — docstring/system-prompt: prefer **Mermaid**; **mxGraph XML** only for cloud-architecture stencils; **CSV** for large data-driven graphs; **never hand-place coordinates** (pass a `layout` hint). Unit tests (mock `diagram-svc`) + conversion/validation tests.

### Phase 2 — Browser editing
**ST-DRW-06 · `DrawioEditor` component (frontend, 1.5d)** — iframe to C1 `?embed=1&proto=json&noSaveBtn=1&noExitBtn=1`; bridge `{event:"init"}`→`load`, `{event:"save"|"autosave"}`→`onSave(xml)` (debounced); origin-checked handler; responsive height (reuse the `MessageContent.tsx` `ResizeObserver`). **Done when:** an existing `.drawio` loads, edits render, `onSave` fires.

**ST-DRW-07 · `ArtifactViewerModal` `.drawio` branch (frontend, 1d)** — extend `classify()` (~`:64`) → `ViewerState.drawio`; mount `<DrawioEditor>`; wire `onSave` to the existing `PUT …/file`; sandbox to the draw.io origin (`allow-scripts allow-same-origin`). **Done when:** open → edit → save persists and re-open shows the edit.

**ST-DRW-08 · Full-screen `/diagram` route + "Open in new tab" (frontend, 0.5d)** — `src/app/diagram/page.tsx?session=…&path=…` mounts a chromeless `<DrawioEditor>`; modal/card link here. **Done when:** the new tab opens the diagram editable and save-back works.

### Phase 3 — Chat AG-UI embedding + email
**ST-DRW-09 · `.drawio`-aware `ArtifactCard` (frontend, 1d)** — detect `.drawio`; render the sibling `.svg` inline with **Edit** (→ST-DRW-07) and **Open** (→ST-DRW-08); optional "Expand" mounts `<DrawioEditor>` read-only inline. **Done when:** a generated diagram previews in chat and both actions work.

**ST-DRW-10 · Email inline-image embed (backend + frontend, 1d)** — after `create_diagram`, embed `.svg`/`.png` inline in the draft (cid/data URI) via the existing compose path; optionally attach the `.drawio`. **Done when:** "email me an architecture diagram of X" yields a draft with the rendered image inline.

### Phase 4 — Optional / polish
**ST-DRW-11 · `edit_diagram(path, instructions)` NL-tweak (backend, 1d)** — regenerate from stored `.mmd` (or merge mxGraph) for "make the DB node red / add a cache layer."
**ST-DRW-12 · Versioning (backend, 1d)** — snapshot `.drawio` on each save (optional `44_diagram_versions.sql`); diff/restore in the viewer.
**ST-DRW-13 · drawio-MCP for desktop power users (infra, 0.5d)** — register the official drawio-MCP (Copilot-SDK agents only) for live in-browser canvas co-editing — strictly additive to the headless path.

---

## 10. Sequencing & MVP slice

```
ST-DRW-01 ─▶ ST-DRW-02 ─▶ ST-DRW-03 ─▶ ST-DRW-04 ─▶ ST-DRW-05
                    │                         │
                    └────────────▶ ST-DRW-06 ─▶ ST-DRW-07 ─▶ ST-DRW-08
                                                      │
ST-DRW-04 ───────────────────────────────────────────┴▶ ST-DRW-09
ST-DRW-04 ─────────────────────────────────────────────▶ ST-DRW-10
(Phase 4: ST-DRW-11/12/13 independent, after Phase 2)
```

**MVP slice (ships independently):** ST-DRW-01 → 02 → 03 → 04 → 05 → 06 → 07 = agents generate clean
`.drawio` from Mermaid **and** the operator edits them in the browser. Cards-in-chat (09), email embed
(10), polish (11–13) layer on after. **Effort:** ~12 engineer-days to end of Phase 3 (~7.5d for the MVP
slice). Backend (diagram-svc + tool) and frontend (editor + viewer) run in parallel once ST-DRW-02
freezes the `diagram-svc` API contract.

---

## 11. Source-of-truth & data-flow rules
- **`.drawio` is canonical once it exists.** The `.mmd` is the *generation* source; after the first **manual browser edit**, regeneration from `.mmd` must **warn before overwrite** (hand-edited geometry would be lost).
- **Three files per diagram**, same basename, in `outputs/`: `.mmd` (regen), `.drawio` (edit), `.svg` (preview/email); `.png` on demand for email.
- **Validation is mandatory** before persisting (ST-DRW-03/04): no broken-edge or malformed `.drawio` ever reaches the workspace.
- **Saving reuses the existing write path** — no new persistence code; the `PUT` endpoint already restricts to `inputs/outputs/agent-data/`.

---

## 12. Infrastructure & security
| Item | Decision |
|---|---|
| draw.io hosting | **Self-host `jgraph/drawio`** (offline, private, CSP-controlled); do **not** use `embed.diagrams.net`. |
| Render oracle weight | Headless Chromium ≈ 300–500 MB on a 4 GB VPS → **warm page pool, capped concurrency, short timeouts**; MVP fallback (`mmd2drawio` + `mmdc`) if needed (ST-DRW-02 gate). |
| Editor iframe CSP | Scope to the self-hosted draw.io origin only; `allow-scripts allow-same-origin` (editor needs JS — unlike the email iframe). Validate `event.origin` in the bridge. |
| Autosave load | Debounce `autosave → PUT`; consider draw.io diff-sync later for large diagrams. |
| Deploy | `diagram-svc` + `drawio` join the compose `diagrams` profile; deploy stays `git push` → `git reset --hard` + migrations + restart (no new manual steps); **runtime-mutable state stays in Postgres/workspace, not tracked files**. |
| Secret hygiene | Rotate the plaintext Hostinger token in `.vscode/mcp.json` (unrelated, but live). **Update 2026-08-01 (doc-truth pass):** moved to the owner-gate secrets list (`work_plan.md` §6, WS-2) — tracked there; line kept for context. |

---

## 13. Risks & open questions
1. **Oracle vs MVP converter** — decided at the ST-DRW-02 gate; the API stays identical so the swap is internal and non-breaking.
2. **Headless Mermaid→drawio** — the draw.io CLI doesn't expose Mermaid import; the embed-protocol-over-headless-browser is the supported way to use draw.io's own converter + layout headlessly.
3. **MAF vs Copilot tool reach** — resolved by the platform-tool choice. (If we *also* want MCP for Copilot agents later, MCP injection needs a MAF path — §1.4.)
4. **Mobile editing** — the editor is heavy on phones; default **view-only (SVG) on mobile**, full edit on desktop (matches current modal/drawer behavior).
5. **Large diagrams / autosave churn** — throttle; revisit diff-sync if needed.
6. **Concurrency on the VPS** — cap `diagram-svc` parallel renders; queue beyond the cap. Confirm headroom in ST-DRW-02.
7. **Auth on the draw.io origin** — reachable only from the gateway/control-plane network, not publicly.
8. **Versioning/history** — none today; consider snapshotting `.drawio` on save (cheap; `outputs/` is mutable) — ST-DRW-12.

---

## 14. Acceptance criteria (end of Phase 3)
- [ ] A chat agent, asked for a diagram, calls `create_diagram` with **Mermaid** and a clean, validated `.drawio` (+ `.svg`) appears as a chat card — **no browser tab required during generation** (fully headless).
- [ ] The **email** agent embeds a rendered diagram **image** inline in a draft.
- [ ] Generation also persists the interim **`.mmd`** and a **`.svg`** preview.
- [ ] Opening the `.drawio` in the artifact viewer launches a **native-feel draw.io editor**; edits **save back** and survive re-open.
- [ ] **"Open in new tab"** gives a full-screen editor for the same file.
- [ ] Broken/malformed diagrams are **never** persisted (validation + self-heal).
- [ ] draw.io is **self-hosted**; no diagram content leaves the platform.
