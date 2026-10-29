# Workbench

History of this file is in git (git log -p).

## Purpose
Control Plane (Next.js browser UI) and local development tools.

## Structure
- control_plane/ -- Next.js app (chat, email, agents, integrations, settings). Its own `AGENTS.md` and `DESIGN_SYSTEM.md` carry the UI contract.
- operator_console/ -- **SEPARATE** Next.js app (CP-8, D35): the staff-only, cross-org customer-management console. It shares the Customer Console's tables, NEVER `control_plane`'s routes, because the separation is a deployment boundary. **THEMING-EXEMPT (D35.4)**: do not apply DESIGN_SYSTEM, `@base-ui` or the eight rules there. Its own `AGENTS.md` carries the local contract.

## Module map (`control_plane/src/`)

Paths below are under `control_plane/src/` unless they say otherwise. Where an
entry says ONE or THE, a second copy is a defect.

### Email (`app/email/`)
4-panel email client (accounts sidebar, email list, email detail, AI chat) with multi-account Gmail and Microsoft support.
- `lib/mailbox.ts`: `pooledMailboxes` is the one rule of All inboxes, and `applyPoolChange` is the one reconciliation of the pool. Only `api.ts`, `mailbox.ts` and `types.ts` name `in_all_inboxes`. A bulk act reads `checkedRows`, never the raw `selectedIds`.
- `mapEmail` reads `also_in`. The gateway decides the pairs, so the row never filters on the separate flag. Fence: `lib/alsoIn.test.ts`.
- A new load in a view of one mailbox uses `guardedLoad`. Fence: `lib/mailboxSettings.test.ts`.
- `lib/connect.ts` holds the connect flow as pure decisions, and the components only draw it. ⚠️ No file under `app/email` links to `/integrations`. The admin-consent client ID comes from the gateway, never from a constant. Fence: `lib/connect.test.ts`.
- `lib/onboarding.ts` holds the guided setup and the sync banner rows. Fence: `lib/onboarding.test.ts`.
- `lib/htmlPrefetch.ts`: `fetchMessageHtml` is the one fetch of a message's HTML. Fence: `lib/htmlPrefetch.test.ts`.
- `lib/draftAutosave.ts`: the three composers import each rule from here, and none keeps a copy. Each autosave carries every recipient. Fence: `draftAutosave.test.ts`.
  - ⚠️ Read the draft id inside the save, never when you schedule it. Do not clear the timer in the cleanup again. Do not call `cancel` on a send.
- `lib/draftSave.ts`: the gateway owns the HTML of a Gmail-web draft. ⚠️ Keep the HTML read in its own effect, behind `needsHtmlRead`. Fence: `draftSave.test.ts`.
- `lib/useDraftSession.ts`: ⚠️ an abort never falls back to `composeAssist`. `createDraftRounds` owns the rounds, and each composer ends its round before it shows other mail. Fences: `lib/useDraftSession.test.ts`, `e2e/email-draft-switch.spec.ts`.
- `lib/composerSize.ts`: ⚠️ do not put `min-h-0` on the body wrapper or `overscroll-contain` on the textarea. Fence: `lib/composerSize.test.ts`.
- `lib/storage.ts` and `lib/cleanOlderMail.ts`: ⚠️ a removal deletes the copy in Metorite only (D-EM-14), and no word may say that Metorite deletes mail in Outlook. `removalReducer` is the one state machine of the dialog. Fences: `storage.test.ts`, `removeOlderMailDialog.test.ts`.
- `lib/assistantSettings.ts`: `autoDraftRepliesOn` is true only for an explicit `draft_replies: true`. `lib/chatScope.ts`: a pick in the chat never moves the page.
- No file in `app/email/lib/` imports from `components/`.
- `ContactTrigger` (`components/ContactCard.tsx`) is a real `<button>`, so a row that holds one must be a div.
- `lib/sendCard.ts` (shared): the server classes each person, and the card only draws it. A send card needs a ticked To row (`sendCanGo`). Fence: `lib/sendCard.test.ts`.

### Shell and shared UI
- `components/EmptyState.tsx`: THE empty-state box. It has three states: filtered, empty, and no-permission, whose CTA is disabled rather than hidden and gives its reason as text.
- `components/board/`: THE Kanban lane. Projects, My Tasks and CRM draw lanes with `BoardColumn` and cards with `TaskCardShell`. The hue comes from `statusAccent` only, as a chip, never a stripe. Add a new board to `BOARDS` in `lib/boards.test.ts` in the same PR.
- `components/LayoutBoundary.tsx`: THE error boundary, mounted per layout around the untrusted part. ⚠️ Retry re-mounts by bumping a key, and `caught()` returns ONLY `error`. Fence: `lib/layoutBoundary.test.ts`. Containment of a throwing child is a review check.
- `components/ContextMenu.tsx`: THE right-click menu, flat, with no flyout submenus. The copy in `EmailList.tsx` is a recorded exemption, and `lib/sharedTaskUi.test.ts` fences a fourth.
- `components/AppShell.tsx`: the responsive shell. No ancestor of the shell scrolls. Do not call `scrollIntoView` in the email app, the shell or the chat. Use `revealWithin` or `scrollToEnd` from `lib/scrollWithin.ts`. Fence: `lib/scrollWithin.test.ts`.
- `lib/nav.ts`: `visibleSections(allowedFeatures)` drops panes the member cannot reach.
- `lib/shell/`: `needs.ts`, `feed.ts` and `feedState.ts` are the one browser clients of their routes. ⚠️ An account switch never clears the read cache. Fences: `feed.test.ts`, `accountSwitchLeave.test.ts`.
- `lib/shell/reminderOpen.ts`: a reminder link (`/chat?reminder=<id>`, RM-4). It is the one client of `/reminder-open` and of the reminder chips. The server builds every word, and the body holds the id only. No chip calls `/promote`. Fence: `app/chat/reminderOpen.test.ts`.
- `lib/shell/navNeeds.ts`: the sidebar row counts. Do not add a second fetch of `/api/shell/needs` or a second poll (`joinNeedsPoll`). Fence: `navNeeds.test.ts`.
- `lib/shell/presets.ts` + `shellPrefs.ts`: a star never grants, because `starredHrefs` picks only from `visibleSections`. `shellPrefs.ts` is the one client of `/api/auth/me/shell`. Fence: `presets.test.ts`.
- `lib/centers.ts`: THE Center registry. A slug here needs its feature in `acb_auth.permissions.FEATURES` (checklist: `department_centers.md` §2). Fences: `tests/unit/test_org_access_control.py`, `lib/centers.test.ts`.
- `components/ControlLink.tsx` + `lib/controlLink.ts`: THE clickable thing that is also a real link. It goes in the title CELL, never around a row, and it stops propagation. Use a plain `<a>`, never `next/link`. Fence: `lib/controlLink.test.ts`.
- `components/ui/Modal.tsx`: THE dialog primitive, and the only file allowed to import `@base-ui/react` (conformance rule 8). ⚠️ It is strictly CONTROLLED and holds no open state. Fences: `conformance.test.ts`, `e2e/modal.spec.ts`.
  - ⚠️ `app/email/components/automation/ui.tsx` holds a second `Modal`. Do not add a consumer to it.
- `lib/outsideClick.ts`: THE outside-click walker for a popover not built on the substrate. A new popover uses it, never a hand-rolled `contains()`. Fence: `lib/outsideClick.test.ts`.
- `components/AgentChat.tsx`: the main chat. `lib/tierRouting.ts` holds every rule of the model picker. Fences: `AgentChat.picker.test.ts`, `lib/tierRouting.test.ts`.
- `components/ui/EntityPill.tsx`: a chat pill links only on exactly one match.
- `components/genUITemplates.tsx` `ReportView`: THE report renderer, in the chat card, the side panel, the file viewer and `/artifacts`. A report adds no second chart, table or board. Fences: `lib/report.test.ts`, `components/reportView.test.ts`.
- `lib/chatMemorySave.ts`: `AgentChat` saves on unmount only the default agent's turns, by each turn's own author stamp. `isDefaultAgent` holds the one alias list. Fence: `lib/chatMemorySave.test.ts`.

### CRM (`app/crm/`)
The native CRM (spec `crm_app.md` §5). Everything server-shaped is pure and lives in `lib/`, each file unit-tested, and `components/` is composition only. Three rules here were learned by breaking them:
1. **Every list filter, `sort` and `dir` included, lives in `CrmView` and therefore in the URL.**
2. **A write re-reads what is on screen, whatever the response was** (`refreshCollection()`). Each write in `lib/api.ts` calls `invalidate("crm:")`, also on a refusal.
3. **A post-write re-read carries the filters the view was loaded with.**
- A non-collection tab (`settings`, `reports`) must join `NON_ENTITY_TABS`, the `page.tsx` branch chain, the load effect and `refreshCollection()`. `isEntity()` is the guard. `store.test.ts` pins which request each write issues.
- The reports surface computes nothing. Weighted ₹ is never recomputed from `lane.rows`, and `tests/fixtures/crm_weighted_parity.json` holds `board.ts` to the gateway SQL.
- Every CRM dialog is a `Modal` or a `ConfirmDialog`. `components/SetupHub.tsx` is the ONE place where the setup changes. An in-app `<a href>` calls `pushOnPlainClick`, never `next/link` (`lib/history.test.ts`).
- The names of the record types come from `GET /crm/mode` in both modes (CRM-Z10g). `lib/recordNames.ts` holds the rules of "Record names". ⚠️ `useRecordNames` keeps its own key, and a failed read stays a failure. Empty boxes after a failed read would let a Save clear the stored names. Fence: `components/crmZ10g.test.ts`.
- Fences: the `components/crm*.test.ts` files. `components/crmX4.test.ts` fails a raw `<input>`, `<textarea>` or `<select>`.

### Knowledge (`app/knowledge/`)
The Knowledge app (spec `knowledge_base.md` §11.1 and §21). Only the first `GET /knowledge/spaces` decides the phase.
- `highlight` returns text parts and never HTML. Each write calls `invalidate("knowledge/")`.
- `lib/editor/schema.ts` is the one schema list and `lib/editor/markdown.ts` the one Markdown manager. Do not load the `Markdown` extension of `@tiptap/markdown` in an editor. `body_md` stays Markdown.
- Every way out of a dirty page asks through `requestLeave`. A file adapted from Docmost carries the `Adapted from Docmost` header and a line in `THIRD_PARTY_NOTICES.md` (D107).
- Fences: `lib/**/*.test.ts`, `tests/unit/test_kb_editor_golden.py`, `tests/unit/test_third_party_notices.py`.

### Tasks and Projects
- `app/tasks/` -- My Tasks (spec `task_manager_app.md`), a projection of the same product as `/projects`. The detail surface is DOCKED on desktop.
  - `openFocus` sets `selectedItemId` and `focusedItemId`. A new full-width surface docks a pane or leaves the overlay store-driven, or its rows become dead clicks.
  - A shared interaction lives once in `lib/` (`selection.ts`, `cursor.ts`, `statusAccent.ts`, `boardDrop.ts`), and `lib/sharedTaskUi.test.ts` fails a second copy.
  - Selection is unconditional, and `selectMode` is derived. Do not gate an affordance on it again. Fence: `selectionParity.test.ts`.
- `app/projects/` -- Projects (spec `project_management_app.md`), the canonical task surface (D-PM-6).
  - Single-source modules: `lib/grouping.ts` (`viewDivergence`), `lib/shownFields.ts`, `lib/commands.ts`, `lib/panelMode.ts`, `lib/taskMenu.ts`, `lib/rowState.ts`, `lib/pagedPicker.ts`.
  - `lib/taskMenu.ts` and `lib/commands.ts` stay disjoint in both directions (`taskMenu.test.ts`).

### Settings, identity and access
- `app/settings/billing/` -- Settings → Billing (spec `subscription_console.md`). `Checkout.tsx` mounts only inside `data.purchaseEnabled`.
  - ⚠️ The BFF is the ONLY gate. `app/api/billing/_purchase.ts::requirePurchaser()` checks `billing:purchase` and compares the caller's org with the key's org, before the Console is touched. Fence: `checkout.test.ts`.
  - ⚠️ The browser never names a price and sums no basket. The order proxy rebuilds the body as slug and quantity.
  - Re-read the order before and after every act. An upstream 401 becomes a 502. `lib/manage.ts` holds no seat arithmetic.
  - 🔎 Known-open finding: any signed-in member can reach `GET /api/billing/summary`.
- `app/settings/organization/` -- the roster, invites, requests and seats (`app/settings/members/` now redirects there, except `[email]`).
  - ⚠️ A failed requests fetch is never swallowed into `[]`. A decision re-reads the list on every response, refusals included.
  - `lib/selfGuard.ts` (`rowActions`) is the one place that decides what a row offers, and it is presentation only. The type-to-confirm rule lives in `lib/confirmPurge.ts`, never inline.
  - An admin sees mailbox counts and never mail (D-EM-4). Fences: `selfGuard.test.ts`, `confirmPurge.test.ts`, `lib/emailConnections.test.ts`, `tests/unit/test_admin_member_purge.py`.
- `lib/access.ts`, `AccessProvider.tsx` and `AccessGate.tsx` are presentation only, and `lib/access.ts` does not re-implement wildcard matching. The gateway re-authorizes every request.
- `components/OrgLinkGate.tsx` + `lib/orgLink.ts`: a copied link carries `?org=<uuid>`, never `?account=`. `decideOrgLink` is the one rule. Fence: `lib/orgLink.test.ts`.
- `lib/gateway.ts`: THE gateway proxy helper (`gatewayHeaders`, `proxyToGateway`, `headersActingAs`). New routes use it.
- `app/api/email/oauth/[provider]/authorize/route.ts`: the mailbox-connect navigation target. It calls the gateway with `redirect: "manual"`, re-issues the `Location` and drops `user_email`. Keep it a sibling of the catch-all. `bounce.ts` builds each failure query. Fences: `tests/unit/test_email_oauth_authorize_wiring.py`, `route.test.ts`.
- `app/integrations/` and `app/connect/authorize/`: each BFF write refuses a `Sec-Fetch-Site` that is not `same-origin`, never follows a gateway redirect, and builds no URL. `/connect/*` carries `frame-ancestors 'none'`. Fences: their `*.test.ts` files and `relay.test.ts`.
- `lib/push.ts` + `public/sw.js`: web push. The ONE switch is the gateway's `WEB_PUSH_ENABLED`, with no build-time flag. `enablePush` is the one caller of `Notification.requestPermission`.
  - Every sign-out path calls `forgetThisDevice()` before `signOut`. Never pass the lenient `fetchAccounts` to `otherAccountsStay`. Fence: `lib/push.test.ts`.
- `proxy.ts`: route protection, and ⚠️ THE ONLY READER OF THE REQUEST HOST in this tier. Fence: `lib/subdomain.test.ts`.
- `lib/subdomain.ts`: per-tenant hostnames, the pure half. `RESERVED_LABELS` is canonical, and the gateway's `routes/signup.py` holds the same set (`tests/unit/test_subdomain_host_vocabulary.py`).
  - The signed-out path does NO LOOKUP and redirects to `app.<domain>`. Nothing here binds a tenant. Fence: `subdomain.test.ts`.
  - It ships dark behind `SUBDOMAIN_WORKSPACE_ENABLED`. The DNS, the certificate, the `AUTH_URL` pin and the flip are 🔴 OWNER-GATE.
- `auth.ts`: NextAuth v5 config. The `signIn` hop is gated by `CUSTOMER_CONSOLE_RESOLVE_ENABLED` (exact `"true"`), never by `CUSTOMER_CONSOLE_URL`. Never read `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` here.
  - `SELF_SERVE_SIGNUP_ENABLED` admits an org-less session only on `signup_eligible === true`, never on the `AccessDenied` code. Flipping either flag live is 🔴 OWNER-GATE.
  - Never separate `session: { strategy: "jwt" }` from `adapter:`. Fences: `app/signin/signin.test.ts`, `tests/unit/test_deployment_resolve_cache.py`.
- `lib/emailOtpAdapter.ts` + `app/signin/code/`: the Auth.js adapter for email OTP. ⚠️ The adapter contains no database. Its behaviour lives in `lib/emailOtp.ts` (`emailOtpAdapterOver`).
  - The adapter is stateless-transparent, and `getUserByEmail` is `NO_STORED_USER` by reference. The code page decides nothing (`codeEntryState`).
  - Fences: `lib/emailOtpAdapter.test.ts`, `lib/emailOtp.test.ts`, `app/signin/code/code.test.ts`.
- `app/signup/` + `app/api/signup/route.ts`: the self-serve form. It redirects to `/signin` while `SELF_SERVE_SIGNUP_ENABLED` is not `"true"`. The hop forwards ONLY `{slug, display_name, registered_state, gstin}`. Fences: `signup.test.ts`, `tests/unit/test_signup_provision_route.py`.
- `app/workflows/` streams a run through its own SSE relay, because the catch-all buffers bodies. `app/api/agent/chat/route.ts` sends all agents through `/agent/run/stream`.

## Conventions
- Next.js App Router, SSE streaming for chat, AG-UI translation in route.ts
- Microsoft Entra ID SSO (NextAuth v5) restricted to org domain
- Route protection through proxy.ts (auth-gated when Entra ID credentials are set)
- Identity chain: NextAuth session → X-User-Email / X-User-Role headers → gateway UserContext
- Role resolution: DB-backed org roles and per-user overrides, resolved server-side per request by the gateway (spec: `org_access_control.md`). Permissions are deliberately NOT put in the NextAuth JWT, because a JWT outlives an access change.
- All API routes that proxy to gateway forward user identity headers alongside Bearer token
- ⚠️ A `[...path]` proxy that serves a non-JSON route forwards the gateway's own content type and `Content-Disposition`. It reads `res.arrayBuffer()`, never `res.text()`, which strips a BOM. Fence: `lib/export.test.ts`.
- Agent-generated files (artefacts) are proxied through /api/agent/workspace/{sessionId}/file?path=
- Agents SHOULD write generated files to .tmp/ or outputs/ for discoverability
- **Design System**: ALL UI must follow `control_plane/DESIGN_SYSTEM.md`. Use shared components from `src/components/`, never ad-hoc tab bars, filter pills or page headers. Use semantic color tokens from `globals.css`, never arbitrary hex values.

## Thinking timeline (VS Code parity)
- ThinkingContainer.tsx renders reasoning and tool calls as ONE timeline, ordered by `ToolEvent.reasoningCutoff`.
- Restore paths split reasoning on "\n---\n" WITHOUT filter(Boolean). Keep the split and the fold logic in step across useAgentChat.ts and route.ts.

## Responsive / mobile layout
- AppShell picks the layout from useViewMode(): mobile at 767px or less, desktop otherwise.
- ⚠️ **The drawer holds a SNAPSHOT.** A page whose sheet reads local state must re-inject it when that state changes. Dismissing the drawer from outside must clear the state that says which sheet is open (see `app/projects/page.tsx`).
- **Adding an app to the bottom bar** is two additive edits in `AppShell.tsx`, never an edit inside another app's branch. The `cc-mobile-nav` detail strings agree by convention only, so a typo is a dead tab.

## Artifact rendering
- MarkdownMessage resolves relative image paths through the workspace proxy. ArtifactCard, ArtifactViewerModal and ArtifactSidebar show the files.
- A `.report.json` file draws with `ReportView` in every viewer. Its PDF is the file beside it, never a conversion.

## Verification
- npm run dev starts on port 3001
- Chat UI connects to gateway at localhost:8000
- `npx tsc --noEmit && npx vitest run` in `control_plane`

## Identity — the BFF is not optional, it is where identity comes from

Contract: `project-docs/specs/user_management_contract.md` (eleven rules). Two
rules bind every page in this app, and a third arrives with WS-29: **R11, never
take the acting tenant from input.** The tenant rides the authenticated session.
A subdomain is a *lookup to be verified against the session*, never an assertion
to be trusted. See `saas_multitenancy.md` §1.5.

**Never point the browser at the gateway.** A top-level navigation to
`api.metorite.com` carries **no** Bearer and **no** `X-User-Email`. Session
cookies live on the workbench origin, and a navigation cannot add headers.

A flow that needs a real navigation (OAuth consent, a provider redirect, a
download) gets a route under `src/app/api/**`. That route authenticates, calls
the gateway with `gatewayHeaders()` from `src/lib/gateway.ts`, and re-issues the
redirect itself. Read that file's "THE RULE" comment first.
`gatewayHeaders()` needs a person and throws without one. `serviceHeaders()` is
bearer-only and takes a written reason.

⚠️ **A `fetch` through the `[...path]` catch-all cannot replace it** for a
redirecting route. The catch-all *follows* the redirect server-side and returns
the provider's consent page as HTML instead of navigating to it.

**Hiding a control is a courtesy, not a boundary** (`lib/access.ts` says so).
Ship the server-side refusal first, then hide the button. A refusal must surface
in the UI and re-read the list. Why: a control that silently no-ops reads as broken,
and a stale row after a 409 reads as success.

## HITL (Human-in-the-Loop)
- ConfirmationCard.tsx — Approve/Reject prompt for agent confirmation requests
- ElicitationCard.tsx — structured question card. It never blocks Submit without a reason (`submitBlockedReason` in `lib/elicitationAnswer.ts`). Fences: `lib/elicitationAnswer.test.ts`, `e2e/genui-option-picker.spec.ts`.
- A blocking card answers through `/api/agent/respond-input`. A 4xx sends the answer as a message, so it is never lost.
- The Email app's chat pane draws each card because it renders the shared `AgentChat` on `/agent/run/stream`. Do not give the pane a chat of its own. Fences: `app/email/components/emailPaneCards.test.ts`, `e2e/email-pane-confirm-card.spec.ts`.
