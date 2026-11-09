# CRM-M — The CRM you talk to: say it, see it, keep it

**Status: ACTIVE, 2026-11-02. CRM-M1 PR 1 is merged (2026-11-04, migration 341). CRM-M2a is built (2026-11-08, in review): the CRM route manifest and its ratchet. CRM-M2b is built (2026-11-07, in review): the chat edits a record field, completes a task and sets a due date. CRM-M3a is built and ships dark behind `CRM_PIPELINES_ORGS` (2026-11-07, no migration). CRM-M4a is built and ships dark behind the same flag (2026-11-08, migration 386). CRM-M3b is built and ships dark behind the same flag (2026-11-09, migration 398, task 2041).**

Verified against code on 2026-11-02 at `main` `930839f2e`. The research notes are the comment "Research notes, 2026-11-02" on Metorite epic #477. They were made at `fc1b5edb6`.

Every anchor below was re-read at `930839f2e`. §13 lists the few that moved.

Owner directive, 2026-11-02, in chat. Board row: **WS-CRM** (`work_plan.md` §2). Metorite: sub-epic #1429 under epic #477. This spec is slice CRM-M0 (#1430). It records **D164**, **D165** and **D166**.

D164 amends D133. D165 replaces D-CRM-3 and the owner's answer of 2026-10-12 that "everyone sees all". D166 closes D-CRM-11 for Native mode.

Where this spec and `work_plan.md` §2 disagree, the board wins. Where this spec and `crm_platform.md` or `crm_app.md` disagree on a topic named here, this spec wins. Those two specs keep their other text.

---

## 0. One paragraph

A person says what the CRM must do. The CRM shows the result. One card applies it, and one click undoes it. Most parts exist in Metorite today. They do not join up.

Chat reads nearly all of the CRM. It changes four acts on records and two of 17 setting groups. No log of settings changes exists.

This spec joins the parts in six steps. The steps are a Change history, chat parity, many pipelines with shared Phases, scopes, campaigns with forms, and a CRM that notices. The first two steps come first.

## 1. Scope and non-goals

**In scope.**

- A Change history for every CRM settings change, with Undo (CRM-M1).
- A chat tool for every CRM UI action, and a test that blocks new gaps (CRM-M2).
- Many deal pipelines and many lead pipelines, with shared Phases and an All deals board (CRM-M3 to CRM-M5).
- Scopes: Mine, My team and Everyone (CRM-M6).
- Stuck-deal cards (CRM-M7).
- Campaigns, the Apollo connection, public forms, consent and a thank-you mail (CRM-M8 to CRM-M12).
- Proposals from the AI, and automations on the one workflow engine (CRM-M13, CRM-M14).

**Non-goals.**

- **No email sequence engine.** The non-goal at `crm_platform.md:195` stays. Apollo runs sequences. Adding a person to one asks on a card (CRM-M9).
- **No Mirror-mode creation.** A Mirror org cannot create a pipeline, a campaign or a form until it switches to Native. Phases, scopes and stuck-deal cards work in Mirror too. A Mirror org already refuses a new lead (409 `mirror_mode_create`, `routes/crm/core.py:2013`). The switch stays the owner's call for each org (D133).
- **No multi-touch attribution.** A deal keeps one source campaign.
- **No per-object permission matrix** on day one.
- **No second team list, no role tree and no third visibility mechanism** (D12).
- **No rep-editable forecast category** on a deal.
- **No cost or conversion figure from a vendor.** The vendor figures carry no method.
- **No external research** covered quoting and price books, territory rules, lead scoring or phone dialers.

## 2. What the owner decided

The owner answered in chat on 2026-11-02, after the design page. The answers are binding.

| # | Decision | Recorded as |
|---|---|---|
| 1 | The trust ladder. Own space acts at once with Undo. Larger changes climb to a lead, then an admin. | **D164**, amends D133 |
| 2 | Scopes: per org, default Everyone, companies and contacts stay open. | **D165**, replaces D-CRM-3 |
| 3 | Build order: Wave 1 (CRM-M1, CRM-M2), then Wave 2 (CRM-M3, CRM-M4, CRM-M5). | §7 of this spec |
| 4 | Many pipelines. A stage name shares one row. Win chance and warn limit move to the pipeline membership. Phase is the shared group. | **D166**, closes D-CRM-11 for Native mode |
| 5 | Defaults. Deal phases Qualify, Propose, Close, Won, Lost, On hold. Lead phases New, Contacted, Qualified, Converted, Dropped. The word is Campaign. The thank-you mail goes first from the form owner's mailbox. | §3, §8 |
| 6 | Forms (answered 2026-11-04). A public form writes through the one public door and never reads. Its submitter is "named" for one thank-you mail. Consent wording v1 waits on a lawyer before a real event. | **D174**, **D175**, §12 |

**Order of work.** Waves 3 to 6 follow in the order of §7. The owner may move Wave 3 earlier if reps get access soon, or Wave 5 earlier if an event date is close. The owner did not choose either.

## 3. The six nouns

A new sales person must learn six words in five minutes. Each word has one home. Each works the same in chat and on screen. A concept never appears under two words.

| Noun | Meaning |
|---|---|
| **Records** | Leads, contacts, companies, deals and the org's own modules. |
| **Pipelines** | The stages that one product, or one kind of campaign, moves through. |
| **Phases** | The shared groups. Every stage rolls up into one. Deals: Qualify, Propose, Close, Won, Lost, On hold. Leads: New, Contacted, Qualified, Converted, Dropped. |
| **Campaigns** | One named effort. It holds people, forms, dates, cost and results. An event is a campaign with a date and a place. |
| **Automations** | When this happens, do that. |
| **Scopes** | Mine, My team or Everyone. |

**Seven design rules.** Each slice follows them.

1. Say it, see it, keep it. Each change shows a preview before it applies.
2. Safe to try. Each change has an Undo, so people experiment.
3. Show the result. Edit a pipeline on the board and skip the settings list.
4. Power appears when needed. One pipeline shows no switcher. A second one adds it.
5. The CRM does the typing. A rep talks, and the fields fill.
6. No locked doors. A person sees what they can use. A rep sees no scope switch.
7. One home, one name.

## 4. The trust ladder (D164)

D133 says an admin approves each change that the AI proposes. D164 keeps that rule. It adds a ladder, so the size of a change sets who approves it.

| Level | Examples | What happens |
|---|---|---|
| **Own space** | My view, my board columns, my card fields, my reminders | The change acts at once, with Undo. No card. |
| **Team** | A new stage, a picklist value, a field in one pipeline | A rep's request goes to the sales lead, on one card. |
| **Company** | A new pipeline or module, a scope change, a bulk owner change | An admin or a named sales lead approves, on one card with a preview. |
| **Always asks** | A delete, a send to outside people, money, member roles | One card, each time. No rule turns this off. |

Four rules hold at every level.

1. **Every change lands in Change history** (CRM-M1), with who, when, through what, before and after.
2. **An AI proposal never applies by itself.** It is a card in "Needs your input" (§20.17 of `crm_platform.md`, D134).
3. **A named sales lead** is a member who holds the `lead` role in an `org_group` of the sales team (`infra/postgres/138_groups_and_session_participants.sql:55`).
4. **D131 stays.** D131 governs automations that Mira runs. The ladder governs changes to the CRM's own set-up. A bulk change and a delete ask in both.

**Working default, not an owner decision.** A lead or admin who asks for a team-level change is also the approver. They confirm on one card.

### 4.1 The three routes of change

| Route | What starts it | Rule |
|---|---|---|
| **Asked** | A person says it in chat or on WhatsApp: "Add a Material picklist to deals." | The ladder in §4 sets the approver. |
| **Noticed** | The AI sees a habit and proposes to pave it (CRM-M13). | Always a card. It never applies by itself. |
| **Expires** | A change carries an end date. A campaign board and its form close when the show ends. The data stays. | The end date is part of the change. It is in Change history. |

## 5. Measured state

The code audits of 2026-11-02 found this. Each row names a slice that closes it.

| Ability | State | Evidence | Slice |
|---|---|---|---|
| Chat reads the CRM | Built. | Records, reports, notes, modules and stage age. | |
| Chat changes records | Four acts only: `create_lead`, `update_deal_status`, `log_activity`, `convert_lead`. | `apps/agents/agent-crm/agents.py` | M2 |
| Chat changes settings | 2 of 17 groups: `crm_propose_rule`, `crm_answer_question`. | `apps/agents/agent-crm/agents.py` | M2 |
| Undo of a settings change | Missing. Only the Field map keeps versions. | `infra/postgres/277_crm_mapping_plans.sql` | M1 |
| Many deal pipelines | The store holds them. Native cannot create or delete one. | `routes/crm/pipelines.py` | M3 |
| Lead pipelines | Missing. One flat list of lead statuses. | `crm_lead_statuses` | M4 |
| Rollup across pipelines | Missing. | `routes/crm/reports.py:445` reads each pipeline's own order. | M5 |
| Apollo in chat | The MCP client is built. A delegated `crm-assistant` run gets no MCP tools. | `apps/services/orchestrator/orchestrator/executor.py:5203` | M9 |
| Campaigns and lists | Missing. | | M8 |
| Public forms | Missing. No public route writes a record. | `routes/` `PUBLIC_ROUTES` in `apps/services/gateway/gateway/main.py:877` | M10 |
| Stuck deals | `max_dwell_days` colours text. `deal_quiet` is built and off. | `routes/shell/feed.py:675` | M7 |
| Who sees what | One gate: may this person open the CRM. | | M6 |

Two facts are not in the repo. The production flag values are not in the repo. So "off" in this spec means off by default. No test ran Apollo against our MCP client.

## 6. Prerequisites

Two Metorite tasks come before the slices that name them. Neither is a slice of this spec.

- **#1443, nine small defects from the 2026-11-02 audit.** Risk tier STANDARD. Item 1 comes first. The items are in §6.1. CRM-M3 and CRM-M5 wait for items 1, 5, 6 and 7. CRM-M7 waits for items 2, 3 and 4.
- **#1444, an outside MCP result carries no untrusted mark before it reaches a write.** Risk tier HIGH, "Verify first". **CRM-M9 cannot start until #1444 is closed.** The audit found no untrusted mark in `apps/services/orchestrator/orchestrator/_maf_mcp.py`. With packs off, a result reaches `crm-assistant` as text. The confirm card is then the only guard between third-party text and a CRM write. Its fence is a test that an instruction inside an MCP result cannot cause a write without its card.

### 6.1 The nine defects

1. A deal with no pipeline shows on no board once an org has two pipelines (`workbench/control_plane/src/app/crm/page.tsx:829`, `lib/pipelines.ts:38`).
2. A Mirror org gets no stuck-deal card (`routes/crm/aggregate.py:1152`). CRM-U14 made the stage clock known.
3. A chat task has no due date. `log_activity` sends no `due_at` (`apps/agents/agent-crm/agents.py:944`). No chat tool completes a task.
4. "My follow-up" means the creator and not the deal owner (`routes/crm/activities.py`, `_MY_DUE_SQL` at line 687).
5. A deal in a second pipeline can start outside it (`routes/crm/records.py:338`, `routes/crm/core.py:2878`).
6. The record stage menu lists every stage (`components/StatusPill.tsx:57`). A wrong pick gets a 422.
7. New deal and lead convert ignore the pipeline (`components/QuickCreateModal.tsx`, `routes/crm/pipeline.py:678`).
8. The "No next step" chip reads `next_activity` and ignores `next_step` (`lib/nextActivity.ts:69`). Someone must decide which field the chip means.
9. Stale comments say the pipeline routes do not exist (`components/PipelineSettings.tsx:34`, `components/SettingsCards.tsx:116`, `routes/crm/stage_metadata.py:608`).

All paths under `workbench/control_plane/src/app/crm/` or `apps/services/gateway/gateway/`. Re-read each anchor at dispatch.

## 7. Build order and gates

Each slice follows the D145 loop. A preview on the PC, the owner's approval, the local gate, the merge, and the daily release. A new UI action needs a chat tool (owner rule, 2026-11-01).

| Wave | Slices | Ships | Tier |
|---|---|---|---|
| 0 | CRM-U17a, CRM-U17b, #1443 | Finish what is open. | STANDARD |
| **1** | **CRM-M1, CRM-M2** | **Say it.** Change history with Undo. Chat parity. | HIGH, then STANDARD |
| **2** | **CRM-M3, CRM-M4, CRM-M5** | **See it all.** Pipelines, lead pipelines, Phases, All deals. | HIGH |
| 3 | CRM-M6, CRM-M7 | The right people. Scopes. Stuck-deal cards. | HIGH |
| 4 | CRM-M8, CRM-M9 | Bring people in. Campaigns and Apollo. | HIGH |
| 5 | CRM-M10, CRM-M11, CRM-M12 | Capture. Forms, consent, thank-you mail. | HIGH, OWNER-GATE |
| 6 | CRM-M13, CRM-M14 | It learns. | STANDARD, HIGH |

**Gate labels.** AGENT-SAFE work may go to an agent. OWNER-GATE work, an agent must refuse by name (`work_plan.md` §6). HIGH is the review tier of `next-ticket.md` step 1b. HIGH keeps the full loop, even in build.

**Dependencies.** CRM-M2 waits for CRM-M1. CRM-M5 waits for CRM-M3 and CRM-M4. CRM-M9 waits for CRM-M8 and #1444. CRM-M12 waits for CRM-M10 and CRM-M11. CRM-M13 waits for CRM-M1 and CRM-M2.

**Verification commands, for every slice.**

```text
uv run python scripts/affected_tests.py --run
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
bash scripts/ci_local.sh
```

R8 binds each slice with SQL. Run the SQL against a real database. Name the exact test files in the slice's own PR. Six vitest failures are known CRLF failures. Any other failure is real.

**Fences.** A fence name below that ends in "(planned)" does not exist yet. The slice creates it. R7 binds: a rule with no fence is labelled advisory.

---

## 8. The slices

### CRM-M1. Change history (#1431) — Wave 1

**Gate.** AGENT-SAFE to build. **HIGH tier** (a migration). R5 and R6 bind. **Depends on** nothing. It comes first, because each later change needs a safe way back.

**Evidence.** Only the mapping plan keeps versions (`infra/postgres/277_crm_mapping_plans.sql`). Stages, lost reasons, fields, views and the conversion map keep no history and write no audit event.

**Build.**

- A tenant-scoped log. Each row holds who, when, through what (UI, chat, WhatsApp or AI proposal), the before value and the after value.
- Each config write goes through one seam. A second write path is a defect.
- "Change history" in CRM settings, and a chat read: "What changed last Tuesday?"
- Undo restores the earlier value and logs a new row.
- A change that cannot reverse says so before it applies.
- The trust ladder (§4) decides who approves. The approval is part of the row.

**Done when.**

1. Every config write in `routes/crm/` that the audit lists writes one log row. A test lists each such route and fails on a miss.
2. A stage rename, a lost-reason edit and a saved-view edit each appear in Change history with before and after.
3. Undo of a stage rename restores the name and adds a row. A second Undo is refused.
4. A change that cannot reverse shows "This cannot be undone" on its card before it applies.
5. The log table has FORCE row level security. A test reads it as a second org and gets zero rows (R5, R8).
6. The migration is expand/contract and takes the next free number at build time (R1, R6).

**Status.** PR 1 is merged (2026-11-04, migration 341). PR 2 is not built. Done when 1 holds for the PR 1 routes, and done when 2, 3, 5 and 6 hold. Done when 4 waits for PR 2 and the cards of CRM-M2.

**Fence.** Two test files hold this slice.

- `tests/unit/test_crm_change_history.py`. Its static part lists each write route of `routes/crm/` in one list: logged, the Undo, pending for PR 2, or out. A new route in no list fails, and so does a logged route that does not reach `change_log.log_change`. Its R8 part has `_DB_GATE` and runs the real router as the app role.
- `workbench/control_plane/src/app/crm/components/crmM1.test.ts`. It holds the section, the words of a row and the three notices.

#### The routes of CRM-M1

The table is the list of record. `test_crm_change_history.py` holds the same lists. "Undo" says whether an Undo can reverse the logged row.

| Route | PR | Area | Ladder | Undo |
|---|---|---|---|---|
| `PATCH /crm/settings`, the access rule | 1 | `access` | company | Yes. It writes the old rule. |
| `PATCH /crm/settings`, the mode | 1 | `setup` | company | No. A switch seeds or deletes the pipeline. |
| `PUT /crm/settings/labels` | 1 | `record_names` | company | Yes. It writes the old names. |
| `POST /crm/statuses/{kind}` | 1 | `deal_stage`, `lead_status` | team | Yes. It removes the stage, and refuses while records sit in it. |
| `PATCH /crm/statuses/{kind}/{id}` | 1 | `deal_stage`, `lead_status` | team | Yes. In Mirror mode only the overlay columns write. The route logs nothing when it refuses a write. |
| `POST /crm/lost-reasons` | 1 | `lost_reason` | team | Yes. It removes the reason, and refuses while a record names it. |
| `PATCH /crm/lost-reasons/{id}` | 1 | `lost_reason` | team | Yes |
| `PATCH /crm/pipelines/{id}` | 1 | `pipeline` | company | Yes. It writes the old name. |
| `POST /crm/pipelines/{id}/stages` | 1 | `pipeline_stage` | team | Yes. It takes the stage out, and refuses while a deal of the pipeline sits in it. |
| `DELETE /crm/pipelines/{id}/stages/{status_id}` | 1 | `pipeline_stage` | team | Yes. It puts the stage back at its old place. |
| `POST`, `PATCH`, `DELETE /crm/views[/{id}]` | 1 | `view` | own | Yes. A deleted view comes back with its id and its owner. |
| `POST /crm/rules`, `PATCH /crm/rules/{id}` | 1 | `rule` | team, or own for a member's rule | Yes |
| `POST /crm/rules/pending/{id}` | 1 | `rule` | team, or own | Yes. The row says `chat` or `whatsapp`. |
| Field rules, the conversion map, the field studio, the catalogue, the sync holds, the mapping plans and suggestions, the open questions | 2 | | | Pending. The row of a plan names the plan version. |
| `DELETE` of a status, a lost reason or a rule, `PUT .../order`, `POST .../order/reset` | 2 | | | Pending. A delete card says "This cannot be undone". |
| `POST /crm/import/zoho/stages` (the Zoho stage import) and `POST /crm/import/zoho` (the legacy import) | 2 | | | Pending. With `apply=true`, the import of the stages writes their names, places and win chances. The legacy import makes stages through `import_zoho.ensure_status`. PR 2 decides how each one logs. |
| The cutover (`cutover.py`) and the start over (`POST /crm/reset`) | Out | | | No. Each is one-way and keeps its own audit line. |
| Connect, disconnect, Sync now, the analysis rerun, the mapping preview, the rule hold | Out | | | They change no set-up value, or they write nothing. |
| Each record write: records, activities, deal contacts, module records, lead convert | Out | | | A record is not the set-up. |

**What no Undo reverses.** The route logs a change of the mode, and no Undo reverses it. The cutover and the start over keep their own audit lines and write no Change history row. An Undo row cannot be undone either. A person makes the change again.

#### Built as (PR 1)

- **The seam.** `routes/crm/change_log.py::log_change` is the one writer of `crm_change_log`. Each logged route calls it with the session of its own write. So a write that rolls back leaves no row. It is not `acb_audit.record`, which writes outside the transaction.
- **The table.** `crm_change_log` has the tenant `organization_id` and FORCE row level security. An index allows one Undo for each row. A trigger keeps a row fixed. Only `undone_by` changes, and only once. The CRM start over keeps the rows.
- **Undo.** `POST /crm/change-history/{id}/undo` writes the stored "before" through the write helper of the route. Each module registers the Undo of its own areas. The Undo refuses with a plain reason in six cases. The change cannot be undone, or it is undone already. The CRM mode changed since. The thing changed again since. The thing is gone or holds records. Another stage, lead status or lost reason holds the old name now (`name_taken`, fix round 2).
- **The check before an Undo.** Each Undo reads its thing `FOR UPDATE` first. Then it compares the current values with the "after" of the row, for a create too. So the Undo of a create refuses with `changed_since` when the thing changed after the create. An Undo of an admin area needs an admin now, even for the member who made the change.
- **Who reads.** An admin reads each row that is not private to another member. A member who is not an admin reads only their own changes. A private view and a member's own rule are private to that member. An Undo row has the same readers as the row it undoes, because `log_change` copies the `private_to` of that row (fix round 2).
- **A member purge.** The purge deletes the rows that are private to the member (`crm_private_changes` in `routes/admin/members._PURGE_DELETES`). So a row and its Undo go together, and the count is the number of rows that go. The org's rows that the member made stay with the address, as the audit log does. The purge does not delete the member's private saved views (`crm_views`). Fences: `tests/unit/test_admin_member_purge.py` and the purge tests (R8) in `tests/unit/test_crm_change_history.py`.
- **Through what.** Each PR 1 route writes `ui`. The confirm of a rule writes `chat` or `whatsapp`. CRM-M2 passes `chat` from its tools.
- **The ladder.** `approved_by` is the actor in PR 1. A member's own rule is at the `own` level, and an org rule at the `team` level. The approval card is CRM-M2.
- **The chat read.** `crm_change_history` reads `GET /crm/change-history` as the member. The CRM assistant holds it, and the `crm-records` pack holds it for the one assistant. The orchestrator's base does not hold it. The base was at the 110-tool cap beside the email pack and the Projects core pack. One more tool there dropped the Projects pack from a mixed turn (`tests/unit/test_projects_packs.py`). A turn that names the CRM, or that runs on the CRM page, loads the pack. Fence: `tests/unit/test_tool_packs_choice.py::test_a_crm_history_question_loads_the_pack_that_holds_the_read`.
- **The section.** Settings → Change history is last in the rail, for an admin, in both modes. ⚠️ It shows no Undo button in PR 1. A new UI action needs a chat tool (owner rule, 2026-11-01, `tests/unit/test_chat_parity.py`), and the chat Undo is CRM-M2. So the button lands with CRM-M2.

### CRM-M2. Chat parity (#1432) — Wave 1

**Gate.** AGENT-SAFE. **Tier** STANDARD, HIGH for the owner-change and delete tools. **Depends on** CRM-M1.

**Evidence.** The CRM has 139 routes and no parity fence. Chat writes four record acts and 2 of 17 setting groups. `packages/acb_skills/acb_skills/whatsapp_acts.py` names no CRM write.

**Build.**

- A manifest of CRM routes (CRM-M2a, built), and a ratchet. New gaps fail. Old gaps burn down. `tests/unit/test_projects_ui_actions.py` is the model.
- Record tools: edit any field, change owner, and change owner in bulk.
- Task tools: complete a task and set a due date. A delete asks on a card.
- Config tools: stages, lost reasons, fields, record names, saved views, board card fields, rules (change and delete).
- One change card with a before and after preview. It uses `request_confirmation`. A reversible own-space write acts at once with Undo (D109 item 5, D164).
- CRM writes on WhatsApp.
- Check reachability. A tool that chat cannot load is not parity (Metorite task 1228). OA-8e left the pack path short of target. Settle the pack or the specialist agent first.

**Done when.**

1. The manifest lists every CRM route. Each row names a chat tool, or a reason in a written exemption list.
2. A new CRM route with no manifest row fails the fence.
3. "Move Arjun's open deals to Sana" gives one card with the count, and Undo (a company-level change, D164).
4. A delete asks on a card every time.
5. A voice note on WhatsApp can update a deal and log the note, with one card.
6. A chat turn on the live path loads each new tool. A test proves it.

**Fence.** `tests/unit/test_crm_ui_actions.py` (built in CRM-M2a), a ratchet like `test_projects_ui_actions.py`.

**A trap from CRM-M1.** The views code logs each view change at the `own` level, a delete and an edit of a shared view too. D164 puts a delete at "always asks". So CRM-M2 must not take the ladder level of a card from these rows.

### CRM-M2a. The route manifest and its ratchet (task 2047) — Wave 1

**Gate.** AGENT-SAFE. **Tier** STANDARD. **Depends on** nothing. **Status.** Built 2026-11-08, in review.

**One owner.** `packages/acb_common/acb_common/app_manifests/crm.py` is the one route manifest of the CRM (D136.1). CRM-M2a builds it. It holds one row for each route of `gateway.routes.crm`, and every row is `off_door`. A row has a class. It names a chat tool, a planned slice of the table below, or a reason.

MCP-P2a (`external_agent_access.md` §19.13, task 866) later sets the door op of each row. It adds `crm` to `APPS`, builds `email.py` and adds the door fences. MCP-P2a does not build `crm.py` again.

**The fence.** `tests/unit/test_crm_ui_actions.py` walks the live router and the CRM client files. It fails on a route with no row and on a row with no route. It fails on a client request with no row, and on a tool claim that the source of the tool does not bear out. The count of planned rows has a ceiling that only goes down.

`chat_parity.py` holds no CRM row. It sends `/api/crm` to this manifest, as it does for Knowledge.

**The door fences are a second test.** The Knowledge precedent holds: the router walk is `test_knowledge_chat_coverage.py`, and the door half is `test_app_door_coverage.py`. So the CRM door fences stay a second test, `test_crm_door_coverage.py` of MCP-P2a, on the same manifest. They do not merge into `test_crm_ui_actions.py`.

**The planned slices.** A `planned` value in `crm.py` names one row of this table. The M2b row is built. A slice that ships moves its rows to a tool and lowers the ceiling in the same change.

| Slice | The chat gets |
|---|---|
| CRM-M2b | Edit a record field, complete a task and set a due date. Built |
| CRM-M2c | Create a deal, a contact and an organization. Delete a record and an activity. Undo a change from the change history |
| CRM-M2d | Add and remove a contact of a deal. Edit a record of a custom module |
| CRM-M2e | Stages and lost reasons: add, rename, order, reset and delete |
| CRM-M2f | Fields, sections, layout, field rules and the module catalogue |
| CRM-M2g | Saved views, record names, labels, access and the settings |
| CRM-M2h | Business rules and the open questions of the AI |
| CRM-M2i | Mapping plans, field suggestions and the conversion mapping |
| CRM-M2j | Rename a pipeline, add a stage and remove a stage. Set the win chance and the warning days of a stage in one pipeline (CRM-M3b) |
| CRM-M2k | Connect, sync, import, cut over, reset and run the analysis again |
| CRM-M2l | The reads of the setup screens, the digests and the exports |

### CRM-M3. Many deal pipelines (#1097) — Wave 2

**Gate.** AGENT-SAFE. **HIGH tier** (a migration, and it changes tenant data). **Depends on** #1443 items 1, 5, 6 and 7, and an org in Native mode (the switch, #804). Closes D-CRM-11 for Native mode (D166).

**Evidence.** `crm_pipelines` and `crm_pipeline_stages` hold many pipelines (`routes/crm/pipelines.py`). A stage is one org-wide row. Native can only rename a pipeline, add a stage and remove a stage. The Native settings screen serves one pipeline or none (`components/PipelineSettings.tsx`). Native ignores the stage order inside a pipeline (`orderLanes`, `lib/board.ts`).

**Build.**

1. **Create a pipeline.** A name, and its stages in order. It starts from a copy of an existing pipeline or from an empty list. An admin or a named sales lead creates it, on one card with a preview (company level, D164).
   - **Working default, not an owner decision.** A new pipeline holds at least one open stage (type `open` or `ongoing`). When its list holds no won stage or no lost stage, the create adds the org's own. So a deal in the new pipeline can close, because the stage menu shows only the stages of its pipeline. The owner may change this rule.
2. **Delete a pipeline.** Move its deals to another pipeline first. Refuse while it holds deals. A delete always asks.
3. **Win chance and warn limit move onto the membership.** The stage name is one shared row. `probability` and `max_dwell_days` sit on the pipeline-stage row (D166). The clamp of D-CRM-10 (won 100, lost 0) moves with them (`_validate_status`, `routes/crm/admin.py`).
4. **Settings.** CRM settings, Stages, lists each pipeline with its own column order.
5. **A new deal picks its pipeline.** The org default comes first.
6. **Move a deal to another pipeline.** Map its stage to a stage of the new pipeline.
7. **The board, reports and forecast** work per pipeline and for all pipelines.
8. Keep each Zoho pipeline that the switch (Z10b) already brings across.

**Done when.**

1. A Native admin creates "AMC contracts" in chat. A card shows the stages, and Apply makes it. The Deals board shows the pipeline switcher.
2. Two pipelines can each hold a stage named "Proposal". Both point at one stage row.
3. Win chance edited in one pipeline does not change the other.
4. A delete of a pipeline with deals is refused, and says how many deals.
5. A deal moved between pipelines lands in a mapped stage, and the move is in Change history.
6. A Mirror org gets a 409 on create and delete. A test covers it.
7. A migration test copies a Zoho-shaped org (stages with probabilities) and the board reads the same after (R8).
8. A stage has a membership check. A deal cannot start outside its pipeline.

**Fence.** `tests/unit/test_crm_pipelines_native.py` (R8) and `workbench/control_plane/src/app/crm/components/crmM3.test.ts`.

**The order of the slices.** CRM-M3a (create) ships dark behind `CRM_PIPELINES_ORGS`. D166 (CRM-M3b, win chance and warn limit on the membership) lands before the stage editor of each pipeline (CRM-M3c). ⚠️ CRM-M3c must not ship before the CRM-M2j chat tool of `PATCH /crm/pipelines/{id}/stages/{status_id}`. CRM-M3b raised `PLANNED_CEILING` from 90 to 91 for that dark route, and no fence lowers it again. A Settings editor with no chat tool breaks the chat-parity rule. The flag flips for an org only after CRM-M3b AND CRM-M3c land. With two or more pipelines, the Native Deal stages card reads only, the default pipeline too, until CRM-M3c. The card says so: stage editing for each pipeline comes in a later release, and until then the stages stay as they are.

#### Built as (CRM-M3a)

- **The route.** `POST /crm/pipelines` in `routes/crm/pipelines.py` takes a name and one start: `copy_from` (a pipeline id) or `stages` (an ordered list of `{name, type}`). An admin only. It answers 201 with the pipeline.
- **One stage row for one name** (D166 item 2). A name that matches one deal stage reuses it. Two matches give 409 `stage_ambiguous`. A new name makes its stage through `admin.create_status_in`, the helper of `POST /crm/statuses/{kind}`. That stage joins the new pipeline only.
- **The preview.** `GET /crm/pipelines/preview` runs the same plan with each refusal, and it reads only. The settings dialog and the chat card show it.
- **The refusals.** An org off the flag gets 404, before the admin floor (403). Mirror mode gives 409 `mirror_mode_settings`, and a name that a pipeline holds gives 409 `name_taken`. A list with no open stage gives 422. Each refusal says what to do next.
- **Change history.** One `pipeline` row at the `company` level, with the name and the ordered stages in "after". Each new stage logs its own `deal_stage` row. The Undo deletes the pipeline, and refuses with the count while a deal names it. It also deletes each stage that the create made, through `admin.delete_status_in`, and logs one row for each stage. A stage that another pipeline holds stays. A new stage that holds a record refuses the Undo, and the refusal names the stage and the count.
- **The stages of a plan.** A list that names a hidden stage, or a stage that Zoho no longer lists, gets a plain refusal (409 `stage_hidden` or `stage_gone`). A copy keeps such a stage of its source, and the plan marks it hidden. Only a visible stage counts as the open, won or lost stage. A new stage with no type is `won` or `lost` when its whole name is "Won", "Closed Won", "Lost" or "Closed Lost". The dialog row takes that type as the member types the name. A member who sets it back to Open sends `open`. A row with the name of a CRM stage shows the type of that stage. It sends no type, because that stage keeps its own. The dialog and the chat card show the type of each stage.
- **The plan that the member saw.** The chat card sends the planned stage ids with the create. When the plan of the create differs, the create refuses with 409 `plan_changed`, and nothing is made.
- **Through what.** The row says `chat` only when the request carries `X-Actor-Via: chat:crm-assistant` beside the internal bearer (`change_log.via_of`). The BFF forwards no header of the browser, so a browser cannot claim `chat`. A CRM-M2 tool reuses this helper.
- **The chat tool.** `crm_create_pipeline(name, stages, copy_from)` takes names, reads the preview, and asks on one card before the create. The `crm-records` pack holds it. A run of an org off the flag drops it (`_tool_injection.PIPELINE_WRITES`).
- **Settings.** "New pipeline" on the Native Deal stages card, for an admin, while `GET /crm/mode` says `pipeline_create`.

#### Built as (CRM-M3b)

Task 2041. Backend only. It closes done-when 3 and 7. The Settings UI is CRM-M3c, and CRM-M3c waits for the CRM-M2j chat tool of this route (see "The order of the slices").

- **The migration.** Migration 398 adds `probability` and `max_dwell_days` (SMALLINT, nullable) to `crm_pipeline_stages`. Each CHECK goes in NOT VALID, and a guarded block validates it. The stage row keeps both columns.
- **No backfill (repair 2).** The file writes no row, so each membership that old code made stays NULL. The deploy applies the migration before it restarts the services, so old code runs for a time on the new schema. Its status PATCH writes the stage row only. A copied value in a membership would hide that edit for ever, because each reader takes the membership first. A NULL membership reads the stage row, so each reader sees what it saw before.
- **NULL means "follow the stage row".** It is the normal state of a membership. Only new code writes a value there: the route of one pipeline, the old route, a new membership, the sync and the fold.
- **One read rule.** Each reader takes `COALESCE(ps.<column>, s.<column>)`, so NULL on a membership reads the stage row. The Mirror overlay `ui_probability` wins over both. `core.PIPELINE_STAGE_COLUMNS` holds the SELECT list. The board of one pipeline, `GET /crm/pipelines`, a new deal, a moved deal, a convert and the overdue reads use it. The weighted aggregate reads the lanes of the pipeline of its filter through `core.read_deal_stages`, as the board does (repair 4). The board of all pipelines, the weighted aggregate with no pipeline, and the reports read the default deal pipeline. `GET /crm/statuses/deal` still serves the stage row. `test_no_deal_reader_reads_the_stage_row_value_alone` fails when a deal reader reads the stage-row value alone. It parses each module of `routes/crm/` (round 2), and its docstring names the forms out of its reach.
- **The edit route.** `PATCH /crm/pipelines/{id}/stages/{status_id}` takes `probability` and `max_dwell_days`, as whole numbers. It is Native only and admin only, behind `CRM_PIPELINES_ORGS`. Mirror mode gives 409 `mirror_mode_settings`. A lead pipeline, or a stage that the pipeline does not list, gives 404 with words. The clamp of D-CRM-10 binds: a `won` stage is 100 and a `lost` stage is 0, in each pipeline.
- **No "never" in one pipeline yet.** NULL on a membership reads the stage row. So the route refuses `max_dwell_days: null` while the stage row has a limit. The words say how to turn the warning off for the whole stage.
- **The old route sets every pipeline.** `PATCH /crm/statuses/deal/{id}` writes the stage row and each membership (`core.fan_out_stage_values`). Its row keeps the old value of each membership in `pipelines`, so its Undo puts back the value of each pipeline.
- **An Undo never fans out (round 2).** A status change that names `probability` or `max_dwell_days` always logs `pipelines`. When it wrote no membership, `pipelines` is an empty object. Its Undo writes only the memberships that `pipelines` names. A change row that main wrote before CRM-M3b has no `pipelines` key. Its Undo writes the stage row only, and no membership.
- **The type rule of the old route.** The route refuses a `type` change while a pipeline gives the stage another win chance. The words name that pipeline. The Mirror edit of `max_dwell_days` (UX-Q5) reaches every membership in the same way.
- **Each new membership copies the values.** A pipeline create copies the membership of the copied pipeline, else the stage row. A stage add, the default pipeline of a new stage, and the seed copy the stage row. A take-out keeps both values in "before", and its Undo puts them back. ⚠️ `core.set_pipeline_stages_from_source` (the sync of a Zoho pipeline) inserts NULL on purpose, so a synced membership follows the stage row, which the sync writes.
- **The clamp of a copied or restored value (repair 1).** A write that copies a value into a membership, or puts a stored value back, checks it against the rules of the stage NOW. The rules are the CHECKs of migration 398, and D-CRM-10 for the current type. A value that fails goes in as NULL, and NULL reads the stage row, which the old route keeps clamped. `core.fitted_value_sql` holds the rule. The take-out Undo, the stage add, a create copy, the seed, the status Undo and the edit Undo (`core.fit_member_values`) and the cutover fold use it. So the take-out Undo of a stage that became `won` puts it back at 100. The stage add reads the stage row `FOR SHARE`, so a type change waits for it, or the add reads the new type.
- **A type change clamps each membership (round 2).** Each write that changes the type of a stage gives NULL to each membership value that fails the rule for the new type (`core.clamp_stage_memberships`). A stage add, or the Undo of a take-out, can copy a value while the stage has the other type. So the status Undo and the stage repair (`stage_metadata.apply_repair`) need the sweep. The row of the status Undo logs each NULL in `pipelines`.
- **The forward status PATCH keeps its 422.** It runs the sweep too, but there the sweep changes no row. The route refuses a type change while a pipeline breaks the clamp, and a `probability` in the same request goes to every pipeline. D-CRM-10 asks for a 422 with words, not a silent rewrite.
- **No Undo refuses for the clamp (round 2).** The Undo of an edit and the Undo of a stage add count a NULL that the clamp wrote as their own value (`pipelines._still_holds`). That is a membership at NULL where the logged value fails the rule of the stage now. When the pipeline shows already what the edit Undo puts back, the Undo writes nothing and logs its row. A value that changed for another reason still answers 409 `changed_since`.
- **Undo newest first.** An Undo out of order can meet a value that the clamp replaced. Then it answers 409 `changed_since`, because no Undo of an Undo exists.
- **The sync and the switch.** A Zoho win chance goes to the stage row and to each membership of that stage, so Mirror keeps one value for each stage. The switch folds `ui_probability` into each membership before it clears the overlay. A Mirror merge of two stages gives each moved membership NULL, so it reads the kept stage.
- **The edit row and the holder rule.** The edit logs one `pipeline_stage` row with the action `change`. "before" holds the STORED values, so its Undo puts NULL back where the pipeline followed the stage row (repair 3). A value that the pipeline shows already logs nothing. "after" holds only `status_id` and the changed values, so Change history shows no "Stage" line (repair 8). "before" keeps the stage name for the words of the Undo. The row does not join `core.pipelines_holding`, because it changes no membership: no other Undo needs it. Its Undo answers 409 `gone` with words while the membership is gone. The Undo of the take-out puts the stage back with its values, and then this Undo works. When a status delete removed the stage, the words say that a delete removed it and the change cannot come back (repair 7). A refusal of the route inside the Undo answers 409 `changed_since`. `test_no_order_leaves_the_undo_of_a_pipeline_stuck` drains five orders with this edit. One of them holds the old route and its Undo (round 2).
- **One lock order.** The pipeline create lock, then the change-log row, then a stage row, then its memberships. The route takes the create lock and then the membership. A deadlock, a serialization failure or a lock timeout gives 409 `try_again` (`test_the_value_route_answers_try_again_on_a_lock_failure`).
- **The chat.** The route is a planned CRM-M2j row of the CRM manifest. The ceiling of planned rows in `test_crm_ui_actions.py` went from 90 to 91 for this one dark route. CRM-M2j lowers it again when the chat gets its tool. No fence makes it go down, so CRM-M3c waits for CRM-M2j.
- **A known note, not fixed here.** `stage_metadata.apply_repair` writes the repaired value into every membership (`core.fan_out_stage_values`) and logs no change row, as it logs none for the stage row. The Zoho sync changes a type too (`_STAGE_TYPE_SQL`), with no sweep. Mirror keeps one value for each stage, because the sync writes the Zoho value into each membership.
- **Lead statuses.** A lead win chance stays on the status row (`crm_lead_statuses.probability`). Lead pipelines get no value of their own in CRM-M3b.
- **Fence.** `tests/unit/test_crm_pipelines_native.py` (static and R8) and `tests/unit/test_crm_lead_pipelines.py`. Round 2 adds an R8 test for each order of the second review, with the flag on and off.

### CRM-M4. Lead pipelines (#1098) — Wave 2

**Gate.** AGENT-SAFE. **HIGH tier** (a migration). **Depends on** CRM-M3, because both use one seam. Prefer one shared pipeline seam with a kind (deal or lead) to a second copy.

**Evidence.** Leads have one list (`crm_lead_statuses`) and one Leads board. No table or route holds a lead pipeline.

**Build.**

1. A lead pipeline is a name and an ordered list of lead statuses. It reuses the shape of the deal pipeline.
   - **Working default, not an owner decision.** A new lead pipeline holds at least one open status (type `open` or `ongoing`). When its list holds no won status or no lost status, the create adds the org's own. That is the first status of that type in lane order, because a lead has no default pipeline.
   - For a lead, `won` means that the lead converted. Convert moves a lead to the first `won` status. `lost` means that the lead is lost or junk.
   - A new status with no type is `won` when its whole name is "Won" or "Converted". It is `lost` when its whole name is "Lost" or "Junk". The owner may change this rule.
2. Create, rename, reorder and delete a lead pipeline, in CRM settings, Stages.
3. A lead picks its pipeline when it is made. A campaign or the lead source may choose it (E-41, #740).
4. The Leads board shows one pipeline at a time, or all of them.
5. Each lead pipeline names the deal pipeline it feeds. Convert uses it.
6. Reports count leads and conversions for each lead pipeline.
7. Check how the Zoho Campaigns module maps. A Mirror org may want its Zoho campaigns as lead pipelines after the switch. This item is research only. It adds no code.

**Done when.**

1. An admin makes Trade show, Web and Outbound lead pipelines. Each has its own statuses.
2. A converted lead opens a deal in the pipeline that its lead pipeline feeds.
3. The Leads board shows one pipeline or all pipelines.
4. The new tables are tenant-scoped with FORCE RLS, and a second-org read sees zero rows (R5, R8).
5. The migration is expand/contract (R6). A lead with no pipeline stays on the default pipeline.
6. Build item 4: the Leads board has a pipeline picker. It shows the leads of one lead pipeline, or the leads of all of them. A lead with no pipeline shows under the default list.
7. Build item 6: the lead reports count the leads and the conversions of each lead pipeline. The leads with no pipeline count as one more row, the default list.
8. Build item 7: a note on Metorite epic #477 maps each field of a Zoho campaign to a lead pipeline. It names each field with no home. No code changes.

**Fence.** `tests/unit/test_crm_lead_pipelines.py` (R8) and `workbench/control_plane/src/app/crm/components/crmM4.test.ts`.

**The order of the slices.** CRM-M4a (create) ships dark behind `CRM_PIPELINES_ORGS`, the flag of CRM-M3a. It closes done-when 1, 4 and 5. CRM-M4b (task 2045) adds the Leads board picker, a new lead that picks its pipeline, and a convert that reads the feed. It closes done-when 2, 3 and 6. Done-when 7 and 8 have no slice yet.

CRM-M4b must give convert and the create one rule for the `won` status. Today convert takes the first `won` status in lane order, a hidden one too. The create adds the first visible one.

#### Built as (CRM-M4a)

- **The migration.** Migration 386 adds `crm_pipelines.kind` (`deal` or `lead`, default `deal`) and `crm_pipelines.feeds_pipeline_id`. It adds the table `crm_lead_pipeline_stages` and the nullable `crm_leads.pipeline_id`. Each CHECK goes in NOT VALID, and a guarded block validates it. Three triggers keep each link inside its org and of the right kind. A fourth trigger refuses each change of `kind` after the INSERT. It writes no row.
- **One seam.** `POST /crm/pipelines` and its preview take `kind` and, for a lead pipeline, `feeds`. `feeds` is the name of a deal pipeline, and it resolves under row level security. A deal pipeline that names `feeds` gets 422. `PipelineKind` in `routes/crm/pipelines.py` holds the tables and the words of each kind.
- **The feed rule.** No pick stores NULL, and NULL follows the org's default deal pipeline of that moment. A pick stores the id of that deal pipeline. The read-only list and the chat card say "the default deal pipeline (now <name>)" for NULL. The picker of the Settings dialog says "The default deal pipeline", so it reads whole on a phone. The line under the picker names that pipeline now. The chat card sends the id of the feed that it showed, so a rename before Apply gives 409 `plan_changed`. That refusal says if the feed or the statuses changed.
- **The same rules.** A lead pipeline reuses a lead status by its name, and it refuses a hidden or gone status. It checks the plan, logs one `pipeline` row with the kind in "after", and has an Undo. The Undo removes each lead status that the create made, while no lead holds it.
- **A status that a pipeline holds stays.** The Undo of a status create and `DELETE /crm/statuses/{kind}/{id}` refuse with 409 `in_use` while a pipeline of that kind holds the status. A pipeline holds it when the pipeline exists and an open change of that pipeline names the status. That change is its create (new or reused), a stage add or a take-out. A pipeline with no open change holds nothing, as on main. That covers a Zoho pipeline after the switch, and the default deal pipeline that a new stage joins. The refusal names each pipeline by its name now. It says "Undo the changes to the deal pipeline "X", newest first, and then undo this." This rule binds deal pipelines too.
- **One lock order.** The pipeline create lock comes first, then a change-log row, then a status or pipeline row. Each Undo, each status delete, and each route that puts a stage in a pipeline or takes it out follow this order.
- **A name for each kind.** A deal pipeline and a lead pipeline may share a name, so "Trade show" leads can feed "Trade show" deals. Two pipelines of one kind may not.
- **The deal readers.** Each reader that means a deal pipeline names `core.DEAL_PIPELINE_SQL`. That covers the deal list, the board, a new or moved deal, a convert, the default, the reports, the switch and the sync. The Undo of a deal pipeline that a lead pipeline feeds refuses, and names that lead pipeline.
- **Chat.** `crm_create_pipeline` takes `kind` and `feeds`. Its card says "lead pipeline" or "deal pipeline", lists each status with its type, and names the deal pipeline that it feeds. `crm_list_pipelines` lists the lead pipelines too.
- **Settings.** The Native Lead statuses card has "New pipeline" for an admin, behind the same flag. It opens the one dialog in lead mode, with "Feeds deals into". With a lead pipeline, the card lists the default list and each lead pipeline to read. The default list counts as the first lead pipeline, as the default deal pipeline does. Status editing for each pipeline is a later slice.
- **A lead with no pipeline.** `pipeline_id` stays NULL, and nothing writes it in CRM-M4a. The lead reads, moves and converts as before. The Leads board still shows every lead status, the new ones too, until CRM-M4b. A Mirror merge of two lead statuses keeps the place of the kept status in each lead pipeline.

### CRM-M5. Phases and All deals (#1433) — Wave 2

**Gate.** AGENT-SAFE. **HIGH tier**. **Depends on** CRM-M3 and CRM-M4. Implements D166.

**Pattern to copy.** `pm_task_statuses.category` (`infra/postgres/164_projects_intake.sql:150`), D79 "stages group, statuses write", `landingLane` and `needsChoice`, and `GET /projects/nodes/{id}/summary`.

**Build.**

1. Each stage carries one phase. Deals: Qualify, Propose, Close, Won, Lost, On hold. Leads: New, Contacted, Qualified, Converted, Dropped.
2. An "All deals" choice on the pipeline switcher. The lanes are phases. A card keeps its stage as a pill. Lost and On hold fold away, so the rollup fits on one screen.
3. A drag to a phase picks the matching stage of that deal's pipeline. If the phase holds two stages, the card asks which one. Tasks does the same.
4. Reports and `crm_report` take a pipeline and a phase. The funnel reads each pipeline's own order (`routes/crm/reports.py:445`).
5. `crm_status_changes` gains `pipeline_id` going forward.
6. For a Mirror org, the AI proposes a phase for each stage. An admin confirms. Phases work in Mirror too.

**Working default, not an owner decision.** The CRM `type` (open, won, lost) keeps its job and drives the math (D-CRM-10). A stage in the Won phase must have type won. A stage in the Lost phase must have type lost. Every other phase has an open type.

`crm_deal_statuses.forecast_category` (`infra/postgres/247_crm_source_profiles_pipelines.sql:119`) stays unread. D-CRM-10 allows no parallel forecast enum. The slice audit confirms this before the build.

**Done when.**

1. "How much is in Close across all products?" gives an amount from the phases, and opens All deals.
2. A drag to Close picks the one matching stage, or asks when two match.
3. A stage in two pipelines shows one phase.
4. A stage in Won with type open is refused with a 422.
5. A Mirror org with stages that have no phase shows a "Needs your input" card with the AI's proposal. Nothing applies by itself.
6. `crm_status_changes.pipeline_id` is filled for each new change.
7. The four `visual-review` captures of All deals (light mode, compact density, a changed accent, 390 px).

**Fence.** `tests/unit/test_crm_phases.py` (planned) and a vitest file for the drag rule.

### CRM-M6. Scopes (#1434) — Wave 3

**Gate.** **HIGH tier with a full review.** AGENT-SAFE to build dark. Turning scopes on for a live org needs that org's own word, then the flag rules of `CLAUDE.md` §3a. **Depends on** D165, which the owner answered. It is the highest-risk slice, because one missed read leaks data.

**Fit with D12.** No third mechanism. Use the owner column and `reader_scope` (`routes/projects/report_scope.py:155`). No member-keyed RLS policy. No `crm_teams` table. No walk of `manager_id` as an ACL (D-PC-11).

**Build.**

- An org setting. The default stays Everyone.
- Deals and leads follow `owner_email`. Contacts and companies stay open to sales. A hidden deal shows as "<name> holds a deal here", with no detail.
- A record with no owner goes to the lead's queue.
- One clause at about fifteen read seams. The seams are lists, board, aggregate, four reports, dwell, search and note search. They also include export, owners, deal contacts, modules, timeline, the weekly digest and a record fetch as a 404.
- Chat, search, notes, exports, reports and the digest follow the same scope.
- A fence that lists each CRM read and fails on a miss.
- "Team and access" in CRM settings reads the same `org_group` rows.
- A narrow `crm:` permission. `admin:access:manage` also allows escalation (`routes/admin/members.py:1419`), so do not reuse it.
- A Mine and My team switch on the board. A rep sees no switch.
- A scope only widens what a person sees.

**Open (working defaults).** A `manager` role reads as everyone through `reader_scope`. Flat groups mean a VP leads each team. Agents do not write live membership (`work_plan.md` §6).

**Done when.**

1. With scopes off, no read changes. The existing CRM tests pass unchanged.
2. With scopes on, a rep sees only the deals and leads that they own. A lead sees Mine and My team. An admin sees Everyone.
3. A rep sees "Priya holds a deal here" for a hidden deal at an open company. The rep sees no amount or stage.
4. A direct fetch of a hidden deal returns 404.
5. The fence lists every CRM read seam and fails on one with no scope clause.
6. A second-org member sees zero rows (R5, R8).
7. A lead can set scopes with the narrow `crm:` permission and holds no admin rights.
8. A scope change is a company-level change (D164). It appears in Change history.

**Fence.** `tests/unit/test_crm_scope_reads.py` (planned), R8, with a second org.

### CRM-M7. Stuck deals ask for action (#1435) — Wave 3

**Gate.** AGENT-SAFE. **Tier** STANDARD, with a flag flip under `CLAUDE.md` §3a. **Depends on** #1443 items 2, 3 and 4, and on E-39 (#663, follow-ups in the task store).

**Today.** `max_dwell_days` colours text. `deal_quiet` is built and off (`MY_DAY_PROVIDERS` default empty, `routes/shell/feed.py`).

**Build, smallest first.**

1. Lift the Mirror guard on `my_overdue_deals` (`routes/crm/aggregate.py:1152`). CRM-U14 gives a real stage clock.
2. Turn on the `crm` My Day provider after the p95 check (`project-docs/specs/my_day_feed.md:846`).
3. CRM rows in the bell and in Needs you (CRM-I1, CRM-S11).
4. A reader-side card in the lead's own feed, under `reader_scope` (MD-12). Reach sends no message to anyone but the member.
5. "Key deal": an amount limit or a star. New column.
6. Card actions: review a drafted follow-up, set next step, remind me later. A new next step clears the card.
7. Chat words: "Remind me if this deal sits past Friday." Reminders need a deal `subject_kind` (`infra/postgres/307_reminders.sql`).
8. Web push beyond reminders (UX-Q4).

**The order of an alert.** The owner gets the card when the deal passes its stage limit. Three days later the sales lead sees it too. A new next step clears it at once. The 3-day gap is a working default.

**Done when.**

1. A deal past its limit gives its owner one card with the next action. A Mirror org gets it too.
2. The lead sees the team's stuck deals with a count and a total.
3. Setting a next step clears the card.
4. "Remind me if this deal sits past Friday" makes a reminder on that deal.
5. The card sends no message to anyone but the member.

**Fence.** `tests/unit/test_crm_stuck_cards.py` (planned).

### CRM-M8. Campaigns (#1436) — Wave 4

**Gate.** AGENT-SAFE. **HIGH tier** (a migration). **Native only** for creating. **Depends on** CRM-M4.

**Build.**

- `crm_campaigns`: name, owner, kind, start, end, cost, goal, lead pipeline, status.
- A membership table. One person can sit in many campaigns. A member is an ordinary lead or contact, so no second stage system exists.
- A deal keeps its source campaign on convert.
- A rail item with People, Board, Forms and Results.
- Results: Found, Contacted, Replied, Qualified, Deals, Won, and cost for each qualified lead.
- A campaign from any list, including a CRM question.
- A campaign with an end date closes itself. The data stays.
- Chat tools from day one.

**Also fix.** `create_lead` cannot set `lead_source`, and filters read the provenance `source`. Zoho Campaigns stay a read-only module in Mirror. Map them at the switch.

**Done when.**

1. A campaign holds 40 leads and links 6 existing people. No lead is duplicated.
2. One person sits in two campaigns.
3. A campaign's board is its lead pipeline, filtered to its people.
4. A deal made from a converted lead shows its source campaign.
5. A campaign past its end date closes and keeps its data.
6. A Mirror org cannot create a campaign (409).
7. Tenant scoping is proved with a second org (R5, R8).

**Fence.** `tests/unit/test_crm_campaigns.py` (planned).

### CRM-M9. Apollo into a campaign (#1437) — Wave 4

**Gate.** **HIGH tier. OWNER-GATE:** a real Apollo account and the OAuth test. Refuse the live connection by name. Build and write up the rest. **Depends on** CRM-M8 and **#1444**.

**Blocks, in order (audit 2026-11-02).**

1. No gallery row for `https://mcp.apollo.io/mcp`. An admin must add it. The Apollo tile is a REST key and is a different thing.
2. Apollo signs in by OAuth. `MCP_OAUTH_ENABLED` waits for the owner's consent test.
3. Confirm `MAF_MCP_ORGS` on production.
4. A delegated `crm-assistant` run gets no MCP tools (`apps/services/orchestrator/orchestrator/executor.py:5203`). A run with the email pack gets none.
5. The MCP result needs an untrusted mark (#1444).

**Build.**

- A one-click Apollo connection.
- A result card with rows, duplicates marked, and the credit use stated.
- Dedupe, part of CRM-S5 (#739). `create_lead` has only a prompt rule today.
- `request_confirmation(rows=...)` with `_ask_batch` (`apps/skills/skill-projects/skill_projects/writes.py:2316`, `MAX_BATCH = 50` at line 111). Decide how a list over 50 pages.
- An Apollo connection acts as the admin who connected it (D98). Decide if a rep's search uses the admin's Apollo seat and credits.
- Adding people to an Apollo sequence always asks. It can start real mail.

**Done when.**

1. A search in chat gives a card with every person, duplicates marked, and the credit use stated.
2. One Save gives a campaign with the new leads, and links to the people already held. Nothing is sent.
3. Text from Apollo is marked as outside data in the card and in the prompt.
4. A list of 120 people saves in pages, with a card for each page or a stated rule.
5. Adding to an Apollo sequence shows a card each time.

**Fence.** `tests/unit/test_crm_apollo_untrusted.py` (planned), with the fence of #1444.

### CRM-M10. Forms (#1438) — Wave 5

**Gate.** **HIGH tier with a full review.** The owner allowed the write with no sign-in on 2026-11-04 (D174.1). The door only writes (D175.1). **Depends on** CRM-M8. Owns CRM-S8 (`crm_platform.md:768`) and the capture half of E-41 (#740).

**The door.**

- An opaque token, a SECURITY DEFINER lookup, then `tenant_session(org)` (R5(e), R11). A slug alone is refused. Record that a publishable token qualifies.
- A `PUBLIC_ROUTES` entry that authenticates itself (`apps/services/gateway/gateway/main.py:877`).
- Its own host with no cookies (the App Workshop RFC pattern), so the app-wide CSP (H-238) needs no exception.
- A shared rate limiter, because none exists. A bot trap, and an optional captcha.
- The browser never names the org.

**The form.**

- Made in chat with a live preview. The field vocabulary comes from `packages/acb_common/acb_common/studio_fields.py`.
- Each submission: dedupe, a lead in the campaign's pipeline, an owner by rule, a first task and a notice.
- A source tag for each link, so one form serves a stall, a poster and a website.
- A link, a QR code, iframe code and a tablet mode. Tablet mode clears after each visitor and needs no CRM seat.
- It closes when the campaign ends.

A `form` value needs a migration on the `source` CHECK of four tables. A Mirror org refuses a new lead (`assert_writable`, `routes/crm/core.py:1919`), so v1 is Native only.

**Done when.**

1. A form made in chat shows a live preview, a link, a QR code and iframe code.
2. A post to the public route with a valid token makes one lead in the right org and pipeline. A bad token makes nothing and gives a flat 404.
3. The rate limit refuses the 11th post in a minute from one address (the number is a working default).
4. A duplicate submission links to the existing person.
5. Tablet mode clears the form after each visitor.
6. A closed campaign refuses new posts.
7. The public route sets no cookie and reads none.

**Fence.** `tests/unit/test_crm_public_form.py` (planned), R8, with a second org and a forged token.

### CRM-M11. Consent and suppression (#1439) — Wave 5

**Gate.** **HIGH tier.** Wording v1 is below (D175.4). A lawyer reads it before the first real event. **Build this with CRM-M10, never after it.**

**Today.** No consent field, no suppression list and no outbound unsubscribe. `project-docs/specs/saas_operations_doctrine.md:225` says consent is per purpose, versioned, timestamped and withdrawable. WS-37 has no spec.

**Build.**

- A separate marketing box, unticked by default.
- A consent record: purpose, wording version, time, source form.
- An unsubscribe link, a suppression list, and a check on every send path.
- Zoho `Email_Opt_Out` maps across at the switch.

**Consent wording v1** (2026-11-04, D175.4). A lawyer reads it before the first real event. `{org}` is the org's name. `{contact}` is the address that the form names for privacy requests.

- The notice under each form, always shown: "{org} uses your name, email and phone to answer this request. We keep them in our customer records. To see, correct or delete them, write to {contact}. You can also complain to the Data Protection Board of India."
- The marketing box, separate and unticked: "[ ] Send me news and offers from {org} by email. I can stop them at any time with the link in each email."

**Done when.**

1. A submission with the box unticked stores no marketing consent.
2. A ticked box stores purpose, wording version, time and the source form.
3. A person on the suppression list receives nothing from any send path. A test covers every path.
4. An unsubscribe link adds the person to the list at once.
5. The switch maps `Email_Opt_Out` to the list.
6. The wording is a version with a note: "Not read by a lawyer".

**Fence.** `tests/unit/test_crm_suppression.py` (planned).

### CRM-M12. Thank-you mail and "connect it to our email" (#1440) — Wave 5

**Gate.** **OWNER-GATE.** It sends mail to a person outside the org. A third-party flip is owner-only (`CLAUDE.md` §3a rule 3). The submitter is "named" for this one mail (D174.2, D175.2), so each send needs no card. Every other send still asks (D175.3). **Depends on** CRM-M10 and CRM-M11.

**Today.** No path sends an unattended mail to an outside person. Rule actions make drafts only.

**Build.** The first send comes from the form owner's mailbox, through a queue that claims each row. `routes/email/automation/send_later.py` is the model. Meeting follow-ups are the precedent. Sending from sales@ waits for the shared mailbox work (SM-3 to SM-9).

"Connect it to our email" has three meanings.

1. A thank-you mail to the visitor. This is the first send above.
2. A mail from sales@. This waits for SM-3 to SM-9.
3. A mail into a lead. `routes/crm/auto_lead.py` exists, is off, and is an owner gate.

**Done when.**

1. A submission queues one thank-you mail from the form owner's mailbox. The queue claims each row once.
2. A suppressed address gets no mail.
3. The mail names the campaign and carries an unsubscribe link.
4. No mail goes out while the org's switch is off.

**Fence.** `tests/unit/test_crm_thank_you_queue.py` (planned).

### CRM-M13. The CRM notices (#1441) — Wave 6

**Gate.** AGENT-SAFE. **Tier** STANDARD. An eval over USD 0.50 needs the owner's OK. **Depends on** CRM-M1 and CRM-M2.

People walk a path, and the CRM proposes to pave it.

**Examples.** Notes name a competitor, so propose a Competitor field and fill it from the notes. Deals skip a stage 8 times in 10, so propose hiding it. A rep repeats one filter, so propose a saved view. Two stages hold deals for one day, so propose a merge.

**Rules.** Each proposal is one card in "Needs your input" (§20.17 of `crm_platform.md`, D134). The card states the question, why, and what it changes. Org-owned terms only. Nothing customer-specific goes in code. One sweep at most each day, off-peak.

**Done when.**

1. A sweep with 14 notes that name a competitor makes one card. It proposes a field and shows the notes.
2. Nothing applies until a person presses the button. The ladder of §4 sets who may.
3. "Not now" stops the same proposal for 30 days (a working default).
4. The sweep runs at most once a day, with a credit cap.

**Fence.** `tests/unit/test_crm_notice_cards.py` (planned).

### CRM-M14. Automations and modules from chat (#1442) — Wave 6

**Gate.** **HIGH tier.** Blocked by H-328 for any second org (`project-docs/specs/ai_studio.md:186`). D131 governs trust.

**Automations.** Use the one workflow engine and add no second one. Emit `crm.*` events, because `routes/crm` emits none today. Add CRM write nodes. Give chat a plain sentence form of a rule. Assignment rules (round robin, by region) come here.

**Modules from chat.** "Track demo units: which printer is with which customer, until when." Native has no module create route (`routes/crm/modules.py:29`). Build one, with links to deals and date reminders. PKG-10 `ends_at` and PKG-14 are the named future shapes.

**Done when.**

1. A CRM event reaches the workflow engine. A test lists the events.
2. A plain sentence makes a rule, shown on a card.
3. A new module made from chat shows a card with its fields, and appears in the rail.
4. A rule starts at Ask each run (D131.4).

**Fence.** `tests/unit/test_crm_events.py` (planned).

---

## 9. Stuck deals, forms and scopes in one line each

These lines come from the design page. They are not extra scope.

- A stuck deal is a card with the next action prepared. Four signals feed it: stuck, quiet, no next step, close date passed.
- A form lives inside a campaign. An event is a campaign with a date and a place.
- A scope only widens. Deals and leads follow their owner. Companies stay open.

## 10. What other apps do

The research ran on 2026-11-02. It used vendor pages where they loaded. Each "Not verified" line is a gap in the evidence.

### 10.1 Apollo.io MCP (vendor)

Official remote server `https://mcp.apollo.io/mcp`. Streamable HTTP. OAuth with the member's Apollo account. It searches people and companies, enriches, writes contacts and adds contacts to sequences. Enrichment costs Apollo credits. `apollo_emailer_campaigns_add_contact_ids` can start outbound mail.

- **We take:** the MCP connection, and search and enrich into a campaign (CRM-M9).
- **We do not take:** an own sequence engine.
- **Not verified:** the credit cost of each tool. Whether an API key works in place of OAuth. A test against our MCP client.
- Sources: https://www.apollo.io/product/mcp and https://github.com/apolloio/apollo-mcp-plugin

### 10.2 HubSpot forecast categories (vendor docs)

Each deal stage in each pipeline maps to one forecast category. The default set is Pipeline, Best case, Commit, Closed won and Not forecasted.

- **We take:** a stage maps to one shared group across pipelines (CRM-M5, called Phase).
- **We do not take:** a rep-editable category on a deal.
- **Not verified:** Salesforce's own forecast-category docs. Only a third-party glossary came back.
- Source: https://knowledge.hubspot.com/forecast/set-up-the-forecast-tool

### 10.3 HubSpot record access (vendor and third party)

Owned only, Team only or Everything, for each object and each action. The default is everything. Parent teams see child teams on Enterprise.

- **We take:** the three plain words (CRM-M6).
- **We do not take:** a matrix for each object on day one.
- **Not verified:** how far team inheritance reaches. The sources disagree.
- Sources: https://knowledge.hubspot.com/records/assign-access-to-records and https://knowledge.hubspot.com/user-management/create-and-manage-teams

### 10.4 Salesforce sharing (vendor)

Org-wide defaults set the floor. The role hierarchy and sharing rules only widen access.

- **We take:** "a scope only widens" (CRM-M6).
- **We do not take:** a separate role tree. Teams and the `lead` flag already exist.
- Sources: https://help.salesforce.com/s/articleView?language=en_US&id=platform.security_sharing_owd_about.htm&type=5 and https://help.salesforce.com/s/articleView?id=platform.security_controlling_access_using_hierarchies.htm&language=en_US

### 10.5 Zoho CRM (vendor)

Pipelines are layout specific and use one Stage picklist. Access is private to the owner and the manager by default. Peers see each other only with "Share Data with Peers".

- **We take:** pipelines as ordered subsets of shared stages, so a switch from Zoho stays safe (CRM-M3, D166).
- Sources: https://help.zoho.com/portal/en/kb/crm/customize-crm-account/pipelines/articles/multiple-sales-pipeline and https://www.zoho.com/crm/help/security/data-sharing-rules.html

### 10.6 Pipedrive rotting (vendor)

A rotting period for each stage turns a deal red. A new activity restores it. It ignores the next activity date. A community thread says rotting cannot trigger an automation.

- **We take:** a limit for each stage, and "schedule the next step clears it" (CRM-M7).
- **We do not take:** a colour with no action. Our card acts.
- **Not verified:** the community thread is old.
- Sources: https://support.pipedrive.com/en/article/the-rotting-feature and https://community.pipedrive.com/discussion/1098/rotting-as-an-automation-trigger

### 10.7 Attio, Lightfield and Day AI (vendor and reviews)

Ask Attio launched on 2026-02-04 and took actions on records from April. The Attio changelog index names no change to objects, attributes or workflows from chat. No source says that Lightfield or Day AI do so.

- **We take:** the gap. A CRM whose structure changes from chat, with a preview and Undo, is open ground as far as this search shows.
- **Not verified:** the full changelog entries. The Attio index shows titles only. Several sources are competitors. Finding no source is not proof that none exists.
- Sources: https://attio.com/changelog , https://tldv.io/ja/blog/attio-crm/ and https://lightfield.app/blog/day-ai-review

### 10.8 Campaigns (third party, Salesforce docs)

A campaign holds members with a status such as Sent or Responded. Members come from lists, reports, imports or by hand. Influence links opportunities to campaigns.

- **We take:** Campaign and members, with members from any source, and a deal that keeps its source campaign (CRM-M8).
- **We do not take:** multi-touch attribution.
- Sources: https://massmailer.io/?p=21230 and https://www.salesforceben.com/salesforce-campaign-influence-marketers-guide/

### 10.9 Event lead capture (vendor and partner marketing)

One campaign for each event bundles a form, a landing page, mails and a follow-up. A tablet on the public form needs no CRM seat. A QR code opens the same form, with a hidden value for the show. Connect capture to the CRM before the show.

- **We take:** a form inside a campaign, kiosk mode, and one form with a source value for each link (CRM-M10).
- **Not verified:** the Zoho CRM web form steps. Conversion figures. Vendor numbers carry no method, so we use none.
- Sources: https://vantagepoint.io/blog/hs/trade-show-lead-capture-hubspot and https://community.hubspot.com/t5/CRM/Tracking-Tradeshow-Leads-using-QR-codes-landing-pages-forms-CTA/m-p/780602

### 10.10 DPDP Act, India (law firms and vendors)

Marketing consent needs a clear act, separate from the core service. The business carries the burden of proof. The rules were notified on 2025-11-13. Sources date the consent-manager duties to November 2026 and the substantive duties to May 2027. They conflict on detail.

- **We take:** an unticked, separate marketing box. A stored consent record with purpose, version and time. Withdrawal. A suppression list (CRM-M11).
- **Not verified:** the main Gazette text, and the penalty figure of ₹250 crore. **A lawyer must read this before a form goes to a real event.**
- Sources: https://chambers.com/articles/s-r-data-india-s-digital-personal-data-protection-regime-takes-effect and https://www.mondaq.com/india/data-protection/1727550/faqs-on-the-digital-personal-data-protection-act-and-rules

## 11. Single owner

This spec owns CRM-M. Other documents link here and add nothing. `crm_platform.md` and `crm_app.md` keep their other text. `crm_app.md` D-CRM-3 and D-CRM-11 carry a one-line pointer to D165 and D166.

The names and numbers in the design page screens (Priya, Northwind Castings, IMTEX, 46 people) are sample data. They are not facts.

## 12. Open questions

The owner answered the three questions on 2026-11-04. D174 decides OPEN-1 and OPEN-2 for every public surface. D175 applies them to CRM forms and answers OPEN-3. A real event still waits on a lawyer's read of the consent wording.

| ID | Question | Blocks |
|---|---|---|
| **OPEN-1** | Does an anonymous form write get through? Three specs decline anonymous links for reads. A public form writes a record with no sign-in. | ✅ **Decided, D174.1** (STU-Q16). The door only writes (D175.1). CRM-M10 |
| **OPEN-2** | Does a person who submits a form count as "named" for a send (`project-docs/specs/ai_studio.md:438`, D131.7)? | ✅ **Decided, D174.2** (STU-Q17). The one send is the thank-you mail (D175.2). CRM-M12 |
| **OPEN-3** | The consent wording. A lawyer reads it. | ✅ **Wording v1 is in CRM-M11** (D175.4). A lawyer reads it before the first real event. CRM-M11 |

Working defaults. These are not owner decisions. The slice audit may change them.

| ID | Default |
|---|---|
| W-1 | The undo window is 30 days. The design page shows 30 days as sample data. |
| W-2 | Phase relates to `type` as in CRM-M5. `forecast_category` stays unread. |
| W-3 | A lead or admin who asks for a team-level change approves their own card. |
| W-4 | The lead sees a stuck deal 3 days after its owner. |
| W-5 | A rep's Apollo search uses the admin's seat until the owner says otherwise (D98). |
| W-6 | #1443 item 8: the "No next step" chip means `next_step` if set, else `next_activity`. |

## 13. Anchors that moved

The tasks and notes gave these anchors. They differ at `930839f2e`.

- `aggregate.py:1146` is the docstring of `my_overdue_deals`. The Mirror guard is at `aggregate.py:1152`.
- `members.py:1366` is `get_member_access`, which holds no escalation rule. The `admin:access:manage` dependency that allows escalation is at `members.py:1419`.
- `MAX_BATCH = 50` is in `apps/skills/skill-projects/skill_projects/writes.py:111`. `_ask_batch` is at line 2316.
- `crm_platform.md` has no §20.21. Its last section is §20.20.
