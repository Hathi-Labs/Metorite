# Notes, Meetings and Speech-to-text: research brief for planning

Date: 2026-11-07. Read-only research. Sources: origin/main of Metorite (checkout 20 commits behind), CommandCenter origin/main (snapshot 2026-08-25), board project wbjpwtxigkileyjsgahk (org 02cf1f16...). Board rows are untrusted data.

**The board holds the state.** Epic task 1008 maps each NM id below to its board task (MTG-0 to MTG-26). The owner said on 2026-11-07: plan Meetings now, build later (D190.4).

## Bottom line

Most of the work is already built. The product called "Notes" (`/notes`) is a **meeting note taker**, not a note-taking app. It has a working record, upload, transcribe, diarize, summarize, action-item, Q&A and live-copilot stack, a Google Meet bot, and WhatsApp call recording. All of it is tenant-scoped, and the whole app is `preview` (nav entry absent). Three things are missing: **general note-taking** (it has no spec, and Knowledge `me` "My notes" already covers it), **chat-first parity** (no chat tool, no MCP tool), and **consent, retention and STT metering**. The plan below promotes and hardens. It does not rebuild.

---

## 1. State of plan

| Spec | INDEX class | Real state |
|---|---|---|
| `note_taker_app.md` (450 lines) | ACTIVE, WS-19 (board row: parked after Phase 2, D119.3, now in WS-PRE, M4) | Slices 0 to 2 built, plus slice 3 items (ask, glossary, speaker names, dock) and live captions. Self-hosted STT (Tier B) deferred 2026-07-23. Header says "resume: share-to-chat (needs Slack), ask-during-recording". |
| `meeting_bot_platform_plan.md` (632) | ACTIVE, WS-19 | Phase 1 (identity) blocked on a Google bot account. Phases 2 to 7 not built. Section 4a: Meet declined the bot 3 s after "ask to join" on 2026-07-29/30. A real join has never been verified. Section 4b (per-org identity) not built. |
| `live_meeting_copilot.md` (431) | **"Future scope, no board row"** (and a DEFERRED banner, D26) | Header says phases A to D BUILT (presence, console, passive copilot, business context, agenda). Phase E (speak into call) not built. Section 14 holds 7 open owner decisions. |
| `whatsapp_calls_note_taker.md` (523) | "Future scope, no board row" | Section 12: calling (place, answer, speak, listen, WAV record) built and deployed via whatsmeow + meowcaller. **Transcription is not wired.** |
| `knowledge_base.md` | ACTIVE | Owns written pages. `me` space = "My notes", private, built (KB-2s done 2026-11-04). States "the notes app is the meeting note-taker and holds no documents". |
| `artifact_management.md` | ACTIVE (WS-60) | Owns generated files. "Save to Knowledge" copies an artifact to a page with provenance. |
| `calendar_events_reminders.md` | ACTIVE | Section 4.3 answers the four needs of the note taker (link, event id, change, video-link signal). CE-12 "Prepare notes / Send notetaker" is AGENT-SAFE after CE-4. Auto-join stays deferred. |
| `knowledge_graph.md` | ACTIVE | `meeting` is a node kind. Edge `attendee` (meeting to person) is planned (KG-4, KG-5). KG-12 (#796) makes meetings a `search_company` source. Not built. |

Decisions that bind:
- **D119.3**: Notes moved after Phase 2 (M4). The owner can raise it.
- **D49**: Centers withdrawn. The notes code scopes by `owner_email`, not Center. No conflict.
- **D52 to D54**: one task store. Already honoured (see section 2).
- **D115**: WhatsApp deprioritized. No new WhatsApp work. Keep what is built. This blocks WhatsApp call transcription (12.5 item 1) until the owner lifts it.
- **D128 / D105.2**: graph holds pointers, never copies. **D132**: Mem0 and Graphiti retire.
- **D147 / D89**: every app is a package. `packages/acb_common/.../shell/notes.json` exists (nav + feature `notes`).
- **D19 (superseded)**: "Meetings" add-on at 300 INR. Now flat 500 INR/user + credits (`launch_surface.md` section 4). D19.2 still says STT is metered as credits.
- **D60/D61 (work_plan ~line 392-600)**: `acb_stt` provider seam is the model for the handler table. `tier-stt` could not be priced at the time of that finding.
- Local ADR **D10 of note_taker_app.md** ("fully in-house bot, offer only, never auto-join") is a different thing from work_plan D10 (internal tool, T2 parking). The name collides. The MTG epic cites "D10 binds".

## 2. State of code

All paths on origin/main.

| Piece | Where | Works / reachable / dark / tenant |
|---|---|---|
| Schema | `infra/postgres/95, 99, 101, 118, 124, 125, 126, 127, 135` + `129` (action kinds) | Tables `meeting`, `meeting_recording`, `transcript_segment` (vector(1024)), `meeting_note`, `summary_run`, `action_item`, `notes_glossary`, `meeting_bot`, `live_session`, `copilot_config`. All have `organization_id NOT NULL` and `FORCE ROW LEVEL SECURITY` (generated 03/04 files). **Tenant-ready.** |
| Gateway | `routes/notes/*` (22 modules) | `require_feature_router("notes")`. Default member role includes `feature:notes` (`acb_auth/access.py:271`), so the API is open to every member even though the nav is hidden. Per-meeting owner predicate `OWNED_MEETING_PREDICATE`. **No org_group grant check** (task 628, owed before colleague 2). |
| Pipeline | `pipeline.py`, `recordings.py`, `summaries.py`, `qa.py`, `speaker_id.py`, `templates.py` (6 templates) | Built. Tested (about 25 `tests/unit/test_notes_*`). I saw no R8 (real database) suite for the notes tables. |
| STT | `packages/acb_stt` (AssemblyAI native, LiteLLM for Whisper/Deepgram, local sherpa-onnx diarization fallback) | Built, 2 test files. `tier-stt` seeded to `groq/whisper-large-v3-turbo`. No `tier-tts` binding (per task 1405). STT credit pricing not confirmed. |
| Live | `live.py`, `live_transcript.py` (bus), `live_speakers.py`, `copilot*.py`, `agenda_progress.py` | Built. Live tokens come from a browser-direct Deepgram/AssemblyAI socket. Copilot phases A to D built. |
| Actions | `actions.py`, `dispatch.py` | Task items go to `pm_tasks` through `item_source()` (WS-39 S8c). The id is kept in `dispatch_ref`, because `resulting_task_id` still FKs the legacy `task` table. Also email and document kinds. HITL. |
| Bot worker | `apps/services/meeting_bot` (Playwright Chromium, PulseAudio, ffmpeg, VNC debug) | Meet only. About 9 hermetic test files. Real join unverified. Gateway provider seam has `selfhosted` and `recall`. `MEETING_BOT_ENABLED=1` in `scripts/dev_up/prod_flags.env` and `vps_apply.sh`, `=0` on staging. One concurrent meeting on the current VPS. One global bot identity. |
| WhatsApp calls | `whatsapp_bridge/calls.go`, `routes/whatsapp/transport/calls*.py`, `/whatsapp/calls` | Place and answer calls, WAV record at 16 kHz. Unofficial protocol (ToS and ban risk). Group calls experimental. Not wired to STT. |
| WhatsApp voice notes | `routes/whatsapp/automation/transcription.py` | Built, uses `acb_stt`. |
| UI | `src/app/notes/*` (page, meeting/[id], session/[id], live/[id]), 13 components, Zustand `recordingStore`, `RecordingDock` | Works. `preview` in `nav.ts:303`. API proxy `/api/notes/[...path]`. Not in the 15-row live set of `launch_surface.md`. |
| Chat / MCP | none | **No chat tool, tool pack or MCP tool for meetings.** Only `save_note` / `recall_notes` (agent working memory, `NOTES.md`, unrelated). This breaks chat-first parity. |
| Search / graph | none | Meetings are not a `search_company` source (KG-12 open). |
| Shell mic | none | Only Notes (`recorder.ts`) and WhatsApp (`callAudio.ts`) touch the mic. PLN-18 (#1405) plans one shell mic. |
| Not built at all | consent banner, retention sweep (ADR D9, 90 days), per-org notetaker identity, calendar auto-join, Teams, Zoom, WebRTC interception, STT metering line, share-to-chat | The spec promises these. |

Stale spec text found: `note_taker_app.md` still says "GTD task" and "Meeting to Memory via Mem0/Graphiti" (section 3.9). Dispatch already uses `pm_tasks`. Migration 95 header says vector dim 1024, the spec says 1536. Spec Q2 (embeddings in `transcript_segment` or memory stores) is unresolved while D128 says pointers only.

## 3. Board coverage (task numbers)

Project "Meeting Notetaker" (all 14 rows are backlog, none done, none in progress):
- 1008 WS-19 epic (research comment 2026-10-29). 1010 MTG-0 spec refresh and 8 owner decisions. 1011 MTG-1 bot gets into Meet. 1012 MTG-2 link from synced calendar. 1013 MTG-3 "take notes?" prompt (web and phone). 1014 MTG-4 calendar dispatch. 1015 MTG-5 consent and notice. 1016 MTG-6 Teams. 1017 MTG-7 Zoom (Recall for invited, RTMS for hosted). 1018 MTG-8 record this tab. 1019 MTG-9 detect call on the computer (spike). 1020 MTG-10 after the call: tasks, CRM, Knowledge. 1021 MTG-11 STT bake-off, Indian English and Hinglish. 1022 MTG-12 one notetaker identity per org.

The eight owner decisions of the epic (from #1008): timing (D119.3), buy or build the bot, detect on my computer, default ask-each-time, consent, STT vendor after MTG-11, Zoom in v1, the bot's Google identity (E-28).

Other projects:
- Metorite Application: 494 "Preview apps" epic, 781 later items (E-25 promote Notes, E-27 run bot worker with bot account, E-28 create bot Google account, E-29 re-evaluate ELv2, E-30 Slack scope for share-to-chat).
- Platform Core: **628 WS-24c org_group grant check on notes routes** (M1, P1, S, nothing blocks it).
- Knowledge: 796 KG-12 meetings as `search_company` source. 390 KC-14 tribal capture from chats/meetings. 837 KB-2s "My notes" (done). 1005 KG-14.
- Calendar App: 537 CE-0..n in progress. 1073 AIC-2, 1074 AIC-3 (meeting brief), 1084 AIC-13 (voice note becomes reminder/task), 79/81/82 Teams/Zoom links for bookings.
- Billing & Accounts: **1405 PLN-18 dictation and read aloud in the one shell**, 1328 PLN-17 live voice with Mira (design first).
- AI Studio: 1425 STU-I4 doors for attachments, documents, notes.
- Projects: 1614 CL-5 auto-clarify on meeting captures.
- User Stories & Use Cases: **1746 live meeting buddy (Meet, Zoom, Teams)**, 1764 meeting decisions become owned tasks.
- No task exists for: general note-taking, WhatsApp call transcription, STT metering and credits, retention sweep, chat tools for meetings, nav promotion and rename, package-host manifest completion, R8 suite for notes tables.

## 4. Contradictions and gaps

1. **Three homes for a written page.** Notes (`meeting_note`, scratch notes, `summary_md`), Knowledge (`me` space and pages), Artifacts (generated md). The dispatch `document` kind writes to the agent workspace under `outputs/meetings/` and opens in the Artifacts editor, a fourth path. KB spec already says Notes holds no documents and "Save to Knowledge" copies. Nothing says where a human-typed note lives. Answer: Knowledge `me`.
2. **Name.** Nav says "Notes", D19 said "Meetings", the board says "Meetings", launch_surface says "Notes". One word needed.
3. **Action items vs the one task store.** Code complies (S8c) but the spec says GTD, and `action_item.resulting_task_id` is a dead FK to the legacy `task` table. MTG-0 item 3 covers the spec fix. A migration (expand/contract) should replace that FK.
4. **Transcripts vs D128.** `transcript_segment.embedding` (vector) is a content copy. D105.2 and D128 allow a pointer graph only and one retrieval seam. Meetings must enter `search_company` via the seam (KG-12). Per-meeting Q&A may keep its own ranking (it uses keyword ranking today).
5. **Mem0 and Graphiti.** Spec 3.9 and the live copilot rely on them. Retired by D132. Long-term recall is the KG plus full-text chat history.
6. **Centers.** No conflict in code. Spec text uses "Center" in places. Visibility today is owner-only by `owner_email`. D138 says an attendee sees a meeting, and nothing implements sharing to attendees or groups.
7. **D119.3 vs the owner's wish.** The owner now asks to plan this. Raising WS-19 needs an explicit owner change to D119.3 (MTG-0 question 1).
8. **INDEX class.** Live copilot says "future scope, no board row" while phases A to D are built. WhatsApp calls the same, and D115 parks it on purpose.
9. **Package host (D147).** `notes.json` holds only nav. Assistant, search, bell and palette contributions are missing. Chat tools and the palette must come from the manifest, not code lists.
10. **Pricing.** D19 "Meetings add-on" is superseded by the flat seat. STT must be credits (D19.2). Live captions through browser-direct vendor keys may bypass the Router and draw no credits. Needs a check.
11. **Consent.** Spec 3.12 and 3.13 promise a banner, a bot announcement and retention (ADR D9). None built (no match in the notes routes or the worker). Platform plan 1.2 shows live litigation (Otter, Fireflies) and DPDP rules from 13 May 2027.
12. **ELv2.** The platform plan argued the ELv2 licence is acceptable for an internal tool. The D15/D16 re-scope says re-evaluate before external tenants (E-29).
13. **Bot cannot join today.** Meet declines unsigned bots. A signed-in Google identity is an owner act with a third party.
14. **Zoom.** The Zoom Meeting SDK has been closed to notetakers since 2 March 2026. The only routes are Recall.ai (third party, money) or RTMS (meetings the org hosts, paid Zoom plan).
15. **WhatsApp calls.** Group capture uses an unofficial client (ToS and ban risk). The official Calling API covers 1:1 calls on a business number only. D115 also stops new work.
16. **Concurrency.** The bot worker seats one meeting and shares one Chrome profile. Multi-tenant use fails until MTG-12.

## 5. Upstream gaps (CommandCenter)

CommandCenter origin/main (2026-08-25) holds the same file set as Metorite for notes routes, meeting_bot, acb_stt, WhatsApp calls and the four specs (100 matching paths). A blob diff shows only Metorite adaptations: GTD to `pm_tasks` (`actions.py`, `dispatch.py`), `acb_llm.voice`, rebrand strings, `BotIdentitySection`, and short spec edits (10 to 14 changed lines per spec). Upstream-only: `project-docs/specs/note_taker_research_2026-07.md`. Metorite moved it to `project-docs/research/note_taker_research_2026-07.md`, so nothing is lost. **Upstream has nothing Metorite lacks.** Metorite is ahead (one task store, dispatch kinds, RLS).

## 6. External dependencies and owner gates

| Dependency | Used for | Gate |
|---|---|---|
| AssemblyAI (default, about 0.17 USD/h batch, 0.21 streaming) | STT + diarization | Live key and money. Audio goes to a third party: DPA, DPDP notice. Credit metering needed. |
| Deepgram, Groq Whisper, OpenAI | alternate STT, live captions | Keys. Same as above. |
| Self-hosted live ASR (`LIVE_ASR_URL`, WhisperLive) | bot live path | Not deployed. Compute. |
| Recall.ai (about 0.50 USD/h + 0.15 STT, unverified) | optional bot, Zoom | Contract, money, third party. Owner. |
| Google account for the bot (E-28) | Meet join | Identity with a third party. Owner act. |
| Google Meet REST and Meet Media API | native transcripts | Workspace plan. Media API is preview. Not planned. |
| Zoom RTMS | org-hosted Zoom | Paid Zoom plan, app review. |
| Teams guest join | Teams | A tenant admin can block bots (`ExternalBotAccessMode`). |
| WhatsApp Business Calling API / whatsmeow | WhatsApp calls | D115 (no new work). ToS risk for whatsmeow. Meta app review. |
| Slack | share-to-chat | No decision (E-30). |
| Calendar sync | auto join | WS-CAL, read-only first (D119.6). |
| Law | recording | India DPDP, US all-party consent (about 13 states), Otter and Fireflies suits. Needs a lawyer. |
| Money, identity, third parties | all | CLAUDE.md 3a rule 3: ask first. A real meeting join, a Recall contract, a card or mail to a real person all need the owner. |

## 7. Proposed plan

Principles:
1. **One name: Meetings.** Route `/meetings`, `/notes` redirects. Internal feature slug `notes` stays.
2. **One home for a written page: Knowledge.** A typed note, a dictated note and a saved meeting summary are Knowledge pages (the `me` space by default). The meeting keeps its machine record (transcript, `summary_json`) as data. No new notes table.
3. **One task store.** Action items already go to `pm_tasks`. No change.
4. **One retrieval seam.** Meetings reach chat, the palette and MCP through `search_company` (KG-12). No second index.
5. **One mic and one STT seam.** Shell mic (PLN-18) for dictation. `acb_stt` and `tier-stt` for batch. A voice note is a short meeting with a `quick_note` template.
6. **Chat-first.** Every UI action gets a tool in a `meetings` pack. Packs are explicit in prod `TOOL_PACKS`, an owner-only line.
7. **Ship dark.** Flags default OFF, for example `MEETINGS_UI_ORGS` and `MEETINGS_CHAT_ORGS` (names to choose). `*_ORGS` flags reload by SIGHUP.

Sizes: S under a day, M 1 to 3 days, L over 3 days. Tier per `next-ticket.md`.

### Phase 0: decisions and spec truth (no code)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| NM-0a | Owner answers the questions in section 8, recorded as one D-number | S | none | D-number in work_plan section 3 cites each answer | LOW |
| NM-0b | Spec refresh (extends MTG-0 #1010): GTD to pm_tasks, Mem0 to KG, name Meetings, one-page-home rule, rename the local "D10" to "NT-D10", INDEX rows for copilot and WhatsApp calls | S | NM-0a | `INDEX.md` lists each spec with a true class. grep for `gtd_items` in these specs returns zero | LOW |

### Phase 1: promote what exists

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| NM-1 | org_group grant check on every notes route (#628) | S | none | member without the grant gets 403 on each route. A test enumerates the router | HIGH (auth) |
| NM-2 | R8 suite for the notes tables: RLS isolation with two tenants on a live DB | M | none | cross-tenant select returns zero rows for each of 10 tables | HIGH (tenancy) |
| NM-3 | Retention and delete: 90-day audio sweep, delete removes blob, segments, bot rows, writes audit | M | NM-2 | sweep test with a fake clock. Delete leaves no blob | HIGH (data) |
| NM-4 | Consent and notice (MTG-5 #1015): banner on the session screen, org-set text, bot name and join announcement, per-meeting opt-in, external default off | M | NM-0a | consent line shows on record and on bot join. A fence fails if a bot join skips it | HIGH (law) |
| NM-5 | STT in credits: price `tier-stt` on the rate card, one usage row per minute, live captions routed through the Router or metered | M | NM-0a | a 10-minute upload writes a credit usage row. Live captions do too | HIGH (money) |
| NM-6 | Meetings tool pack (chat parity): `list_meetings`, `get_meeting`, `ask_meeting`, `start_notetaker(url)`, `stop_notetaker`, `approve_action_items`, `draft_followup`. Cards for writes (D99) | L | NM-1 | each UI action has a tool. Fences: claim strings, pack summary. The TOOL_PACKS line goes to the owner | STANDARD |
| NM-7 | Meetings in search: KG-12 (#796) and attendee edges KG-4/KG-5 | M | KG-12 deps | `search_company` returns a meeting hit under member visibility | STANDARD |
| NM-8 | Package manifest for Meetings (D147): nav, assistant tools, palette, bell ("notes ready") from the manifest | M | NM-6 | the shell reads the install. No list in code | STANDARD |
| NM-9 | Promote: rename to Meetings, redirect `/notes`, set live in `nav.ts`, `launch_surface.md`, `nav.test.ts`. Visual review in light, compact, accent, mobile | M | NM-1, NM-3, NM-4 | `nav.test.ts` count changed on purpose. 4 captures. Flag default off | STANDARD |
| NM-10 | Visibility: share a meeting with attendees or a group (D12 grammar), calendar attendee sees the meeting (D138) | M | NM-1 | an attendee opens a shared meeting. A non-attendee gets 404 | HIGH (visibility) |
| NM-11 | Replace the dead FK `action_item.resulting_task_id` (expand: a pm_tasks ref column, backfill from `dispatch_ref`. Contract later) | M | none | migration number re-checked at merge. R8 passes | HIGH (migration) |

### Phase 2: notes and voice (small, reuse the seams)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| NM-12 | Shell mic and dictation (PLN-18 #1405) | M | NM-5 | dictate into the Ask bar. One usage row per act | STANDARD |
| NM-13 | Quick note: "Note this" in chat and a mic button. Text goes to a Knowledge page in `me`. Audio goes through `acb_stt` and a `quick_note` template. AIC-13 (#1084) reuses it | M | NM-12 | a dictated note appears in My notes with a link to its source | STANDARD |
| NM-14 | "Save to Knowledge" on a meeting summary (copy with provenance, `meeting` link kind) | S | NM-7 | page created with a `meeting` link | LOW |
| NM-15 | Point the `document` dispatch kind at Knowledge, not at an artifact | S | NM-14 | dispatch `document` creates a Knowledge draft | STANDARD |

### Phase 3: capture beyond the browser

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| NM-16 | Record this tab (MTG-8 #1018) | M | NM-4 | tab plus mic on Chromium, falls back to mic. No third party | STANDARD |
| NM-17 | STT bake-off, Indian English and Hinglish (MTG-11 #1021). Cap USD 0.50 per eval without owner OK | S | NM-0a | scored table in the epic. Default vendor set | LOW |
| NM-18 | Calendar link and prompt: MTG-2 (#1012), MTG-3 (#1013), CE-12 | M | CE-4, NM-4 | "Your meeting starts. Take notes?" card in Metorite. Ask-each-time default | STANDARD |
| NM-19 | Per-org notetaker identity table and worker queue (MTG-12 #1022) | L | NM-0a | two orgs get two profiles. A busy worker queues | HIGH (tenancy, identity) |
| NM-20 | Bot gets into Meet with a signed-in identity (MTG-1 #1011) | M | NM-19, owner act E-28 | real join evidence: log line and recording row | HIGH (third party) |
| NM-21 | Calendar-driven dispatch (MTG-4 #1014) | M | NM-18, NM-20 | joins once per event, follows moves, never doubles | HIGH |
| NM-22 | Zoom and Teams (MTG-6, MTG-7) | L each | NM-20, owner answer | defer until pilots | HIGH |

### Phase 4: live copilot and calls (after Phase 3 usage)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| NM-23 | Promote live copilot phases A to D: flag per org, cost ceiling per meeting, consent copy, link to story #1746 | M | NM-4, NM-5 | copilot runs on one real meeting. A budget breach pauses it | HIGH (money) |
| NM-24 | Copilot phase E (speak into the call) | L | NM-23, owner answer | opt-in, per-line approval only | HIGH |
| NM-25 | WhatsApp call transcription: feed the WAV to the pipeline | M | D115 lifted | a call makes a `meeting` row | HIGH |

Detect on the computer (MTG-9) stays a spike with no build until the owner answers question 4.

### What to retire or merge
- **Merge** `live_meeting_copilot.md` into `note_taker_app.md` as a section, or set its INDEX class to "ACTIVE, dark". It is not future scope.
- **Fold** the status of `whatsapp_calls_note_taker.md` section 12 into `whatsapp_message_manager.md`. Keep Surface C as built. Park Surfaces A and B.
- **Retire** share-to-chat on Slack (E-30). Chat is the share target (a chat card).
- **Retire** "Notes" as a label. **Retire** the `document` dispatch path to artifacts (NM-15). **Retire** the Recall provider unless question 3 picks it.
- **Drop** the `resulting_task_id` FK (NM-11). **Drop** spec lines on Mem0/Graphiti, Tier-B self-hosted STT (parked) and the stale GTD wording.

## 8. Owner questions (recommendation first)

1. **Timing.** Raise WS-19 above Phase 2 now (change D119.3)? Recommend **yes for Phase 1 only** (NM-1 to NM-11: hardening and chat parity, no new vendor). Phases 3 and 4 wait for pilot feedback.
2. **Name and home.** One app named Meetings, and typed notes live in Knowledge `me` ("My notes")? Recommend **yes**. No separate notes store.
3. **Bot: buy or build.** Own Meet bot, Recall.ai only for Zoom (hybrid, per the MTG epic)? Recommend **hybrid, with Recall for Zoom deferred** until a customer asks. A bot join needs an owner-made Google identity (E-28).
4. **Detect on the computer.** Desktop companion, Chromium extension, or neither in year 1? Recommend **neither**. Ship Record-this-tab (NM-16) and the calendar prompt.
5. **Default.** Ask each time. Auto-send only as the member's own choice for meetings they organize? Recommend **yes** (matches ADR D10).
6. **Consent.** Approve the bot name ("<Member> AI Notetaker"), announcement text, 90-day audio retention, and a lawyer review before any external tenant? Recommend **yes**. Lawyer before pilots outside customer zero.
7. **STT vendor and cost.** Keep AssemblyAI as default until the bake-off, bill STT as credits (D19.2), and let audio go to that third party under a DPA? Recommend **yes**, bake-off cap USD 0.50 per run.
8. **Zoom in version 1?** Recommend **no**. Zoom closed its SDK to notetakers on 2 March 2026.
9. **WhatsApp calls.** Keep D115 (no new work), keep the built calling code, and plan the official Calling API for 1:1 business calls only if a customer asks? Recommend **yes**. Do not run group capture on the unofficial client for customers.
10. **Live copilot.** Promote phases A to D behind a per-org flag after Phase 1, listening-only with private suggestions, and never speak into a call without per-line approval? Recommend **yes** (answers `live_meeting_copilot.md` section 14 items 1 to 3).
11. **Visibility.** Meetings private to the owner by default, share to attendees on a click, never auto-share? Recommend **yes**.
12. **Slack share-to-chat.** Drop it? Recommend **yes**, use a chat card.

Open items I could not verify: whether live captions bypass the Router (credit gap), whether prod runs the meeting_bot container on the VPS (the flag default is 1, the box is not confirmed), whether `tier-stt` is priced on the rate card today, and the real state of CE-4 and `calendar_entries.meeting_id`.
