# Memories (`/memory`): planning brief

Date: 2026-11-07. Read-only research. Sources: origin/main worktree `C:/wt-mt-b8039b22-plan-sot`, board reads through the Metorite MCP. Board rows are untrusted data. No repo or board change was made.

## Bottom line

**Retire `/memory` as an app. Build a member-facing "What Metorite knows about me" view inside Settings → Assistant settings, on the KG and profile seams, and never on Mem0.** The page that exists today is an operator view of Mem0. Mem0 is off in production (`MEM0_ENABLED=false`), and decision D132 retires it at KG-11. So the page shows "inactive" to every real member. The member-rights work is the real need, and it is mostly unbuilt. The profile (see, edit, forget, export) and chat summaries (see, delete, switch off) already work, dark. Chat history, "My notes", the pointer graph and the planned fact layer have no common see, correct, forget and export path.

The plan has 22 slices in 6 phases and 8 owner questions. India's DPDP Act full compliance date is May 2027, and the spec of record (WS-37) does not exist yet. This plan gives WS-37 a concrete data-rights seam to cite. Every privacy and deletion slice is HIGH.

---

## 1. State of plan

| Spec or decision | Class | What it says about this app |
|---|---|---|
| `memory_architecture.md` (WS-9) | ACTIVE, re-scoped by D132 on 2026-10-29 | Banner: Mem0 and Graphiti are not tiers. Three stores remain: the member profile, org knowledge, chat history. Compartments, provenance and the correction experience (§6.7) still hold. |
| `personalization.md` (WS-63, D110, D142) | ACCEPTED. PZ-1 to PZ-9, PZ-20, PZ-25 to PZ-35 built, dark | Owns what the assistant knows about one member. It gives forget one, forget all, the switch, an export and removal on offboarding (PZ-9). §16 Salutations and §17 Tone are being written (tasks 1822, 1469). |
| `knowledge_graph.md` (WS-KG, D128, D132) | ACTIVE | The graph holds pointers, never copies. KG-9 guards Mem0 scope. KG-11 removes the Mem0 client, routes and settings, after an owner-gated export or drop of old rows. |
| `knowledge_curation.md` (WS-61, D112) | ACTIVE | KC-9 plans `kb_episodes` and `kb_facts` with an audience. KC-10 plans Confirm, Correct and Hide on a fact. KC-13 makes Knowledge the org memory of agents. |
| `knowledge_base.md` (D104) | ACTIVE | The `me` space ("My notes") is private. A member's `me` space is removed on offboarding (CP-2g). One org export route is planned in KB-11 as "the DPDP answer to give me my data". No member export exists. |
| `saas_operations_doctrine.md` §3.3 | doctrine | DPDP: plain notice, separate consent for training and improvement, a breach clock, sub-processor disclosure. Consent Manager duties start Nov 2026. Full compliance May 2027. |
| WS-37 Trust and compliance | `work_plan.md` §2 row: "none yet" (D119.4) | No spec. No sub-processor list. No member-rights text. |
| `launch_surface.md` | ACTIVE | Row `/memory` reads "operator-grade surface, not customer-grade". It is not in the 15 live rows. |

Binding decisions: D49, D87 to D89 (the shell is one seam), D110 and D142 (personalization), D128 and D132 (graph, Mem0 retires), D147 (package per app), D152 (one person, many orgs).

## 2. State of code

| Piece | Where | Works, reachable, dark, tenant-scoped |
|---|---|---|
| Nav entry | `workbench/control_plane/src/lib/nav.ts:306` | "Memories", Brain icon, `launch: "preview"`. Comment: held back as operator-grade. Note text says "Facts · episodic · knowledge graph". |
| Page | `src/app/memory/page.tsx` (420 lines) | Lists Mem0 rows for `session.user.email`, search, delete one, clear all, status banner. Falls back to the literal email `dev@fracktal.in` when no session loads. It shows Mem0 rows only. It shows no profile, no graph and no episodes. |
| Proxy | `src/app/api/memory/[...path]/route.ts` | Calls `requireIdentity`, then forwards to the gateway. The member email sits in the URL as the scope. |
| Gateway | `gateway/routes/memory.py` (302 lines) | `require_internal_auth` plus `_authorize_scope`: a person scope must equal the caller's own email. Unknown shapes fail closed. Delete confirms the id lives in the scope. **Scope is correct per person.** |
| Tenant scope | `acb_memory.scope_key` | **The Mem0 key has no organization part.** The key is the bare email, or `org:global`. One person in two orgs (D152) would share one pool. KG-9 (task 786) adds a startup refusal. Q2 of the KG spec asks if a real org key is worth building. |
| Second reader | `src/app/api/chat/memories/route.ts`, `hooks/useChatMemories.ts` | The chat page, the email assistant and the Projects rail read Mem0 through this route. The scope is the session member only. |
| Feature | `packages/acb_auth/acb_auth/access.py:272` | `feature:memory` is in the default member role. The API is open to every member even though the nav is hidden. |
| Prod state | `scripts/dev_up/prod_flags.env:14` | `MEM0_ENABLED=false`. The page works only on a dev box that turns it on. |
| Profile | `user_settings.assistant_profile`, `/auth/me/assistant-profile` and `/export` | Built, dark. Server switch plus `ASSISTANT_PROFILE_ORGS`. A fence keeps it out of staff routes. Purge and removal clear it. |
| Profile UI | `src/app/settings/assistant/*` | Built. It has the text the assistant reads, facts with edit and delete, names, "Download my profile", "Forget everything", "Keep chat summaries", delete one or all summaries, interview areas. |
| Chat summaries | `chat_session.member_summary` (migrations 319, 337) | Built, dark. Settings controls built (PZ-35, task 1133, done). |
| Chat history | `DELETE /chat/sessions/{id}` | One session delete exists. I found no "delete all" and no export. |
| Facts and episodes | `kb_facts`, `kb_episodes` | **No migration and no code.** KC-9 is open. |
| Org purge | `packages/acb_auth/acb_auth/offboard.py` | An org-level purge exists, with a Mem0 purge. No member-level erase covers all stores. |
| Chat tools for my data | none | The behaviour "what do you know about me" exists (PZ-20). No tool forgets, corrects or exports across stores. |

## 3. Board coverage (task numbers)

Project "Personalization & Memory System" (id `3cd344f2-00cb-4659-82c9-1ff177cf281c`).

Done: 243 to 247, 249, 250 (Mem0 recall safe, closed), 251 to 253, 256 to 259 (PZ-9 forget controls), 315 (PZ-20 read as text), 435 to 437, 462, 820 (MEM-3, memory shape decided), 831, 832, 1024, 1061, 1130 to 1133 (PZ-33 to PZ-35).

Open and relevant:
- 1006: owner decisions from the memory audit (5 items). Item 5 covers nine pre-D132 chat-memory tasks (116, 133 to 140, project AI Chat Interface).
- 316 PZ-21 private chat without the profile. 317 PZ-22 review dates. 318 PZ-23 one-app fact. 319 PZ-24 org switch for the profile. 1129 epic, with 1640, 1134 and 1639.
- Salutations 1820 (1822 and 1823 in progress, 1824 to 1826, 1894). Tone 1469 to 1475.
- Knowledge project: 786 KG-9 (in progress), 795 KG-11, 389 KC-13, 796 KG-12, 821 MEM-2 (search past chats).
- Platform Core: 1127, "/pull: a body user_email chooses whose Mem0 facts the agent reads".
- Onboarding: 1364 OB-23 (import what another assistant knows). 1468 GD-13 (practice runs teach nothing).
- Billing & Accounts: 1300 PLN-31 (privacy review before the first invite).
- AI Chat Interface: 138 "Govern agent memory: access, retention and deletion", 139 "one master memory per member", 134 org versus member memory.

**No task exists for:** a member data view, a cross-store forget, a cross-store export, a registry of stores that hold member data, the DPDP notice text, correction of non-profile stores, retiring `/memory` and its nav row, or an R8 proof that Forget leaves no copy (summaries, the Redis briefing block, embeddings, graph pointers). WS-37 has no task and no spec.

## 4. Contradictions and gaps

1. **An operator page for a retired store.** The page edits Mem0. Mem0 is off in prod and retires at KG-11. A member never sees "what Metorite knows" there.
2. **The name promises more than it shows.** The nav note says "Facts, episodic, knowledge graph". The page shows Mem0 rows only.
3. **Four homes for what is known about me.** The profile and chat summaries (Settings), chat history (the chat page), notes (Knowledge `me`). Each has its own control, or none. No place lists all.
4. **No export beyond the profile.** The right of access needs the whole set. KB-11 plans an org export only.
5. **Forget is per store.** "Forget everything" clears the profile column. I found no test that it reaches summaries, the Redis briefing block (PZ-34), chat history, `me` pages, embeddings in `kb_chunks`, or later graph edges and facts.
6. **The Mem0 key has no org part.** The same email in two orgs shares rows (D152). Task 1127 shows a body field can choose whose Mem0 facts the agent reads. Mem0 is off, so the risk is dormant. It goes live if anyone turns the flag on.
7. **A dev fallback email in a shipped page.** `dev@fracktal.in` is the default user id. The gateway check blocks misuse, but the string belongs in no member page. Retiring the page removes it.
8. **The memory feature slug is open to every member.** `feature:memory` is in the default role. Keep it (never revoke a feature to hide an app). The new routes must not rely on it for safety.
9. **Spec text lags.** `memory_architecture.md` sections marked "no longer hold" still read as live. `personalization.md` line 207 lists the Mem0 API as built.
10. **Nine old chat-memory tasks** (116, 133 to 140) predate D132. Tasks 138 and 139 describe this plan's job in old words.
11. **WS-37 gap.** No spec, no sub-processor list, no retention window. The `me` space rule says pages "archive, not delete, for the retention window WS-37 sets". That clashes with a member's right to erase unless WS-37 says what the window covers.
12. **Role of the customer.** Under DPDP the customer org is the fiduciary and Metorite is the processor (doctrine §3.3). An erasure request may go through the org. The plan must decide what the member does alone and what the org admin does.

## 5. Upstream gaps (CommandCenter)

I compared the upstream clone read-only.
- Upstream has the `/memory` page, `routes/memory.py` and `acb_memory`. This is the Mem0 operator view. The Metorite copy adds the scope check (`_authorize_scope`) and the identity requirement at the proxy.
- Upstream has no profile, no summaries, no personalization spec, no KG spec and no member data view.
- **Upstream has nothing Metorite lacks.** Port nothing.

## 6. External dependencies and owner gates

| Item | Used for | Gate |
|---|---|---|
| Law: DPDP Act and Rules | notice, access, correction, erasure, consent per purpose | Lawyer review of the notice and the processor/fiduciary split before the second customer. Consent Manager duties Nov 2026. Full compliance May 2027. |
| WS-37 spec | retention window, sub-processor list | Not written (D119.4). The owner starts it. |
| Model vendors (sub-processors) | the assistant reads the profile in each turn | Disclosure in the notice. A DPA. Owner act. |
| Mem0 rows in prod and dev | KG-11 export or drop | **Owner gate** in the KG spec. |
| `TOOL_PACKS` line for `my_data` | chat parity | An owner-only prod line. |
| Flag `MEMBER_DATA_ORGS` (new, default empty) | dark rollout | Flip under an `enforcement-flip` grant. Name the flag and the box. |
| Backups | erased data lingers until rotation | State the real window in the notice. It needs the backup retention from the box. |
| Money, identity, third parties | all | Do not mail a real member or write a live org without asking (CLAUDE.md 3a rule 3). |

## 7. Proposed plan

Prefix `WKM` (What Metorite Knows about me). Proof it is free: `git grep -nE '\bWKM-[0-9]' -- project-docs` returns no match. Board home: project **Personalization & Memory System** (id `3cd344f2-00cb-4659-82c9-1ff177cf281c`). Create one epic "WKM · What Metorite knows about me" there, linked to WS-63 and WS-9. Cross-link KG-11 (task 795) and WS-37.

Principles:
1. **One home: Settings → Assistant settings.** It already holds see, edit, forget and export for the profile. No new nav pane. No Center (D49).
2. **One registry of member-data stores.** Each store registers `describe`, `export` and `forget`. The view, forget, export, offboarding and the chat tools all read the registry. The registry wraps the existing profile and summary code. It does not copy it. No second path.
3. **Pointers, not copies (D128).** The graph adapter lists links. It stores no member text.
4. **Self-scoped by the session.** Member and org come from the session, never from input (R5). A person in two orgs sees one org at a time (D152).
5. **Hard delete in primary stores. A receipt without content.** The audit row holds who, store, count and time. It holds no text.
6. **Chat-first with full parity.** Every button has a tool in the `my_data` pack. Writes use cards. Forget-all asks a second yes.
7. **Ship dark.** `MEMBER_DATA_ORGS`, default empty. `*_ORGS` flags reload on SIGHUP.
8. **The org admin never reads content.** At most an admin sees request counts. The PZ-9 fence already keeps the profile off staff routes.

Sizes: S under a day, M 1 to 3 days, L over 3 days.

### Phase 0: decisions and spec truth (no code)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| WKM-0a | Owner answers section 8, recorded as one D-number | S | none | D-number in `work_plan.md` §3 cites each answer | LOW |
| WKM-0b | Spec: a new section in `personalization.md`, "What Metorite knows about me": the store inventory, the registry contract, the forget and export rules. Fix stale Mem0 text in `memory_architecture.md` and `personalization.md` line 207. Update the `launch_surface.md` row for `/memory` | S | WKM-0a | `test_index_completeness.py` and `test_doc_names_resolve.py` pass. The `launch_surface.md` row shows the new status | LOW |
| WKM-0c | Give WS-37 its first input: member-rights duties, retention questions and a sub-processor list, filed as a comment on the WS-37 epic | S | WKM-0b | The comment exists and cites WKM-0b | LOW |
| WKM-0d | Triage the nine old chat-memory tasks (116, 133 to 140) and task 1127. Propose closures: 139 and 138 fold into WKM-4 and WKM-12 | S | WKM-0a | Each task has a comment: close, keep or fold. The owner approves on task 1006 | LOW |

### Phase 1: retire the operator page, count the legacy

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| WKM-1 | Redirect `/memory` to the new Settings section. Remove the nav entry in `nav.ts`. Keep the `memory` feature slug and the gateway routes until KG-11. The `dev@fracktal.in` fallback leaves with the page | S | WKM-6 | `nav.test.ts` count changed on purpose. `/memory` answers a redirect. `feature:memory` stays in the default role | STANDARD |
| WKM-2 | Read-only count of Mem0 rows per member, dev and prod. Write it as a comment on KG-11 (task 795). Copy no row text | S | none | The comment names the counts and the date | LOW |
| WKM-3 | A fence test: no route reads Mem0 for a scope taken from request input. KG-9 (task 786) stays the only startup guard. Task 1127 closes here or in KG-11 | S | KG-9 | `test_memory_tenant_guard.py` holds. The new test fails on a body-chosen scope | HIGH (tenancy) |

### Phase 2: see (the view)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| WKM-4 | The member-data registry seam: `describe`, `export`, `forget` per store, in `acb_common`. First adapters: the profile and chat summaries (they wrap PZ-9 and PZ-35 code) | M | WKM-0b | R8 with two tenants and one email in both: each adapter returns only the session org. An advisory fence (R7) lists registered stores | HIGH (identity, privacy) |
| WKM-5 | `GET /auth/me/knows`: per store a count, the last change, the source and "why we keep it". Self-scoped. Behind `MEMBER_DATA_ORGS` | M | WKM-4 | A member sees only own rows. A foreign email in any parameter is ignored. Staff routes cannot read it (the PZ-9 fence) | HIGH (privacy) |
| WKM-6 | UI: the section "What Metorite knows about me" in Settings → Assistant settings. One card per store, a drill-in, empty states that explain. A palette entry and a Settings entry come from the shell manifest (D87 to D89) | M | WKM-5 | 4 captures (light, compact, changed accent, beside its neighbour) and a `Visual review:` line. Copy says what happened and the next step. No "Jev" | STANDARD |
| WKM-7 | Chat history adapter: count sessions and messages, list titles, delete one, delete all | M | WKM-4 | R8: delete-all removes that member's sessions and messages in that org only. A shared room keeps other members' messages | HIGH (data deletion) |
| WKM-8 | Knowledge `me` space adapter: count, link to My notes, delete through the Knowledge API, including page history and `kb_chunks` embeddings | M | WKM-4 | R8: after forget, no `kb_chunks` row remains for a deleted `me` page | HIGH (data deletion) |
| WKM-9 | Pointer graph adapter: "Metorite links you to N records". Read-only | M | WKM-4, KG-4, KG-5 | Only edges the member may already open appear (KG-6 visibility rule) | HIGH (visibility) |
| WKM-10 | Facts adapter: private facts and episodes with a member audience, with source and date | M | WKM-4, KC-9, KC-10 | A private fact shows only to its owner. Correct and Hide reuse KC-10 | HIGH (privacy) |

### Phase 3: correct and forget

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| WKM-11 | Correct: edit a profile fact (exists), edit a summary line, and "that is wrong" on a fact (KC-10 Correct closes the old fact and adds the new) | M | WKM-6, WKM-10 | The old value stays only as history the member can see and delete (PZ-22 rule) | HIGH (privacy) |
| WKM-12 | Forget one item, one store, or all, through the registry. A receipt row without content | L | WKM-4, WKM-7, WKM-8 | R8 on a seeded member: after forget-all every adapter reports zero. Another member and the other org stay intact. A forget never turns an opt-out of the brief back on (PZ-9 fence) | HIGH (data deletion) |
| WKM-13 | Evict derived copies: the Redis briefing block (PZ-34), cached prompt blocks, summary relays | S | WKM-12 | After a forget, the next turn carries no old fact. A test reads Redis through the prefix wrapper and finds no key | HIGH (data deletion) |
| WKM-14 | Offboarding parity: member removal and the org purge (CP-2g) call the same registry | M | WKM-12 | `test_org_purge_tenant.py` seeds each adapter. A fence fails when an adapter is missing from the purge | HIGH (data deletion) |
| WKM-15 | Backups and the retention statement: record the real backup window. Align the `me` "archive for the window" rule with erase (WS-37 input) | S | WKM-0c | The notice text names the window with a number taken from the box. Owner and lawyer approve | HIGH (law) |

### Phase 4: export, notice, chat parity

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| WKM-16 | Export: one zip with JSON and readable Markdown per store. Built async. The link expires in 24 hours. An access-log row | M | WKM-4 | A seeded member gets every store. The zip holds no other member's data. The link fails after expiry and for another session | HIGH (privacy) |
| WKM-17 | `my_data` tool pack: `what_do_you_know_about_me` (reuse PZ-20), `forget_item`, `forget_store`, `forget_all`, `correct_fact`, `export_my_data`. Cards for writes. Forget-all asks a second yes. The `TOOL_PACKS` line goes to the owner | L | WKM-12, WKM-16 | Each button in WKM-6 has a tool. Fences: claim strings, pack summary, write-origin (only the member's typed words ground a forget, PZ-31 rule) | HIGH (deletion by chat) |
| WKM-18 | Package manifest (D147): each app that keeps member data declares its adapter in its manifest. The WKM-4 fence now blocks | M | WKM-4 | The shell reads the declaration. The fence fails on an app with a member table and no adapter | STANDARD |
| WKM-19 | Plain-language notice: what is kept, why, how long, who processes it, the sub-processors, how to ask. Shown in the section and at first use. It says what is org work product and not the member's to erase | M | WKM-0c, WKM-15 | Lawyer sign-off is recorded on the task. The notice shows before any second-customer invite | HIGH (law) |
| WKM-20 | Tie-ins: PZ-21 private chat (task 316), PZ-24 org switch (task 319) and OB-23 import (task 1364) write through the registry and show in the view | S | WKM-6 | Each has a line in the view and a test that its write lands in a registered store | HIGH (privacy) |

### Phase 5: remove Mem0

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| WKM-21 | Before KG-11: if WKM-2 counts rows, export each member's rows to that member, or drop them. Owner gate. Report the act and the evidence | S | WKM-2, owner | Counts are zero, or each member got an export. The owner approved | HIGH (data) |
| WKM-22 | With KG-11 (task 795): delete `app/memory`, `api/memory`, `api/chat/memories`, `lib/memory.ts` and `useChatMemories`, with its three callers (chat page, email assistant, Projects rail) | M | KG-11, WKM-1 | No import of the Mem0 client. `test_no_second_memory_store.py` passes. The three callers still load | STANDARD |

Order of work (owner decisions of 2026-11-07): WKM-0a to 0d and WKM-2 first, beside the cheap isolation items. WKM-4 to WKM-6 and WKM-12 next, because they back the DPDP answer. Phases 4 and 5 follow. No date is set for the first paying customer. The notice (WKM-19) and the lawyer review gate the second customer, not the first.

### What to retire or merge

- **Retire** the `/memory` page, its nav row, and the label "Memories" (WKM-1, WKM-22).
- **Retire** the Mem0 routes and `useChatMemories` with KG-11. The registry serves the member's need.
- **Merge** the member-data work into `personalization.md` as one section. Write no new spec file.
- **Fold** old task 138 into WKM-12 and WKM-15. Fold 139 into the profile, as D132 decided. Close 116, 133 to 137 and 140 as superseded after the owner agrees (task 1006 item 5).
- **Keep** the `memory` feature slug in the default role until KG-11. Never revoke it to hide an app.

## 8. Owner questions (recommendation first)

1. **Retire `/memory` and put the view in Settings → Assistant settings?** Recommend **yes**. It is already the member's home for the profile. The nav row goes.
2. **How wide is "what Metorite knows"?** Recommend the registry covers the profile, chat summaries, chat history and My notes in version 1. The graph and fact adapters join when KG-4/5 and KC-9 land.
3. **Does forget mean hard delete?** Recommend **yes** in primary stores, at once, with a receipt that holds no text. Erased data ages out of backups. The notice states the real window.
4. **Export format and safety?** Recommend one zip (JSON plus Markdown), async, link valid 24 hours, own session only. Ask for a fresh sign-in before download, because the zip can hold sensitive facts.
5. **What does the org admin see?** Recommend **request counts only**, never content. This keeps the PZ-9 privacy fence.
6. **Controller and processor split.** Recommend that the member handles personal stores alone. Work product that the org owns (CRM notes, tasks, shared pages) stays with the org, and the notice says so. A lawyer confirms before the second customer.
7. **Training and improvement.** Does Metorite use member data to train or improve models? Recommend the notice says **no** and WS-37 records it. If the answer is not "no", DPDP needs a separate consent first.
8. **Start WS-37 now?** Recommend **yes, as a short spec** (notice, retention window, sub-processor list). WKM-0c gives it its first input, and the May 2027 date leaves little room.

Not verified: whether chat history has an export route (I found none), the backup retention window on the box, whether Mem0 holds any rows in prod (WKM-2 counts them), and whether the Copilot-SDK chat path reads Mem0 (task 1271 hints it differs).
