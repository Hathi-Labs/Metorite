# Flip a per-org flag with no restart

A flip of a per-org flag reloads the gateway. It does not restart it. A
restart ends the chat runs in flight, and members see "Gateway error 502"
for 10 to 25 s. Every other flag still needs a restart.

The code is `apps/services/gateway/gateway/flag_reload.py`. The fence is
`tests/unit/test_flag_reload.py`.

## The flags that reload

A reload applies only the `*_ORGS` and `*_MODES` fields of `Settings`. These
are the per-org rollout lists, such as `CRM_ORGS`, `TOOL_PACKS_ORGS`,
`AGENT_QUESTIONS_ORGS` and `DECIDE_FEATURE_MODES`. Each reader of them reads
the value again on each call.

`AGENTS_VIEW_ORGS` opens the member view of agents in Projects
(`agents_view.md` §6, AGA-6). Off, `GET /projects/agents/overview` answers
404. Its one reader is
`gateway.routes.projects.agent_summary.agents_view_allowed`. A reload applies
a change. The flip stays OWNER-GATE (`enforcement-flip`).

`AGENT_QUESTIONS_ORGS` changes who may answer an agent's question
(`external_agent_access.md` §12.8). A reload changes how the flip applies, and
it does not change who may flip it. The flip stays OWNER-GATE
(`enforcement-flip`).

`AGENT_WRITE_CAP` and `AGENT_WRITE_WINDOW_S` set the write cap of each agent
connection (`external_agent_access.md` §9.2, MCP-G20). The defaults are 120
writes in 600 s. They are not `*_ORGS` or `*_MODES` keys, so a reload does
NOT apply them. The gateway counts the writes, and the agent edge names the
numbers in each write tool. Set both units to the same value. Then restart
`acb-gateway` and `acb-agent-edge`. A change stays OWNER-GATE
(`enforcement-flip`).

`AI_TURN_KIND_NOWAIT_ORGS` stops the turn-kind wait of a delegated run
(`ai_tier_routing.md` §4.3, task 3592). For each listed org, a delegated run
asks no turn-kind question. It starts at once on the `chat` kind, and it does
not wait up to 3 s. A `plan`, `analysis` or `code` task then keeps the
agent's own tier. `*` lists every org, and the default is empty. Its one
reader is `acb_skills.tier_policy.turn_kind_nowait_on`. Each delegated run asks
it at its start, so a reload applies to the next run. A flip stays OWNER-GATE
(`enforcement-flip`).

`API_CONNECTIONS_ORGS` turns on the API connections of the chat
(`api_connections.md` §4, D-API-8). API-1 declares it, and the rung 1 slices
read it. `*` lists every org, and the default is empty. Empty gives no org an
API connection in the chat. Its one reader is
`acb_common.api_connections_flag.api_connections_allowed`. A reload applies a
change. The flip waits for the rung 1 demo, and it stays OWNER-GATE
(`enforcement-flip`).

`CHAT_CARD_PRECONDITION_ORGS` turns on the version check of a card
(`chat_failure_modes.md` §6 row HRD-10a). For each listed org, `update_task`
sends the `updated_at` of the read that the card shows as `If-Match`. A task
that changed while the card waited gets a 412, and the tool writes nothing. The
tool then answers "Changed since the card", with the old and new values of the
fields on the card. For a deal, the CRM tool `crm_update_record` does the
same (HRD-10c). The email tool `edit_draft` sends the digest of its read of
the draft as `expect_digest`, and a changed draft gets no save (HRD-10d-1).
`*` lists every org, and the default is empty.

Empty gives the PATCH of before, with no `If-Match`. Its one reader is
`acb_skills.ask_tools.card_precondition_on`. It reads the org from the run
binding, and a fault reads as off. The tool asks it at each call, so a reload
applies to the next call. A flip stays OWNER-GATE (`enforcement-flip`).

`CHAT_CARD_RECORD_ORGS` turns on the card record (`chat_organization.md`
§16.3, CO-17). For each listed org, a write card that a member approves leaves
one row in `chat_card_record`: the chat, the member, the card id, the outcome
key, the act kind and the link of the record made. The row holds no text of
the card. A declined card writes none. `*` lists every org, and the default is
empty. Empty means nothing is written.

Its one reader is `orchestrator.card_record.record_allowed`. The card gate asks
it on each approved card, so a reload applies at once. The flip stays
OWNER-GATE (`enforcement-flip`).

The same flag turns on the sent-mail record (task 3594). A send tool sets the
provider id, the approved addresses and the time on the row of its card. Each
later turn of the chat then gets one line for each of the last 5 sent mails,
in its run context. The run asks the flag at the start of each turn, so a
reload applies at the next turn.

`CHAT_CARD_REVISE_ORGS` turns on the revise of a mailbox card
(`chat_failure_modes.md` §4.3 row C7, task 3814). For each listed org, a
chat message of the member closes each card of the email assistant's
`_confirm_destructive` gate, such as "Save this new email in Drafts?". The
tool then tells the model how to read the message. A new draft of a chat
also updates the draft of the same email that the chat saved before, so
Drafts keeps one copy. `*` lists every org, and the default is empty. Empty
means each card and each draft save is as before.

Its one reader is `acb_skills.card_revise.card_revise_on`. The email tool asks
it at each card and each save, and the gateway asks it at each save of a
new-mail card. So a reload applies at once. A flip stays OWNER-GATE
(`enforcement-flip`).

`CHAT_CLAIM_CHECK_ORGS` turns on the claim check of an answer
(`chat_failure_modes.md` §6 row HRD-13). For each listed org, the run of the
orchestrator logs one line, `answer_claims.checked`, at its end. The line
counts the claims of acts and promises that no tool outcome of the run backs.
The check is shadow only, and it never changes the answer (D-HRD-6).

`*` lists every org, and the default is empty. Its one reader is
`acb_skills.answer_claims.claim_check_on`. The injection asks it at the start
of each run, so a reload applies to the next run. A flip stays OWNER-GATE
(`enforcement-flip`).

`CHAT_FOCUS_ORGS` turns on the item in focus of a chat
(`chat_failure_modes.md` §6 row HRD-11a). For each listed org, the chat keeps
the email or the Projects task that a tool call read or acted on last. The
per-turn context then shows its kind, its id and its link, with the rule for
"it", "this" and "mark it done". `*` lists every org, and the default is
empty. Empty gives each prompt of before, byte for byte.

Its one reader is `acb_skills.chat_focus.focus_on`. The executor asks it at
the start of each turn, and each write and each block ask it again. So a
reload applies to the next turn. A flip stays OWNER-GATE (`enforcement-flip`).

`CHAT_JOIN_SHADOW_ORGS` turns on the shadow join log
(`chat_organization.md` §7, CO-4). For each listed org, the first message of
a new web chat with Mira writes one row of `chat_join_log`. The row holds the
outcome that §7.1 would take, as codes and ids only. The member sees nothing.
`*` lists every org, and the default is empty. Empty means the run route
schedules no match.

Its one reader is `gateway.chat_join_shadow.shadow_allowed`. The run route
asks it at each first message, so a reload applies to the next new chat. A
flip is the gate `enforcement-flip`.

`CHAT_LATE_APPROVE_ORGS` turns on the late-approve grant
(`chat_run_continuity.md` S2 item 8, HRD-22). For each listed org, a late
Approve of a parked plain card approves the same card once in the next run.
`*` lists every org, and the default is empty. Its one reader is
`acb_skills.late_approve.late_approve_on`, and the park, the late answer and
the card gate each ask it. So a reload applies at once, and an org that leaves
the list uses no grant. A flip stays OWNER-GATE (`enforcement-flip`).

`CHAT_NO_SHRINK_ORGS` turns on the guard of a chat save
(`chat_failure_modes.md` §6 row HRD-17e-1). For each listed org, a client
save that makes an agent row shorter before the fold seals it keeps the stored
text. The save logs one `chat_no_shrink_refused` line, with lengths and no
text. `*` lists every org, and the default is empty.

Its one reader is `gateway.chat_no_shrink.no_shrink_mode`, and each save asks
it. So a reload applies at the next save. A flip stays OWNER-GATE
(`enforcement-flip`). Do not flip it before the owner reads the counts of
`CHAT_NO_SHRINK_SHADOW_ORGS`.

`CHAT_NO_SHRINK_SHADOW_ORGS` turns on the shadow of the same guard. For each
listed org, the save is the save of before. A client save that makes an agent
row shorter before the seal logs one `chat_no_shrink_shadow` line, with
lengths and no text. An org in both lists enforces.

`*` lists every org, and the default is empty. It has the same reader. A flip
stays OWNER-GATE (`enforcement-flip`).

`CHAT_PACKS_BATCH_ORGS` gives the member's own WhatsApp turn of each listed
org the read tools of its packs (`chat_reach.md` G2, task 3494). The turn holds
no write tool, and the specialist of each pack stays. It acts only where
`TOOL_PACKS` gives a pack. `*` lists every org, and the default is empty.

Its one reader is `acb_skills.tool_packs.packs_batch_on`. The batch run asks it
at the start of each WhatsApp run, so a reload applies to the next run. Any
value other than empty is OWNER-ONLY (`work_plan.md` §6 D4).

`CHAT_PASTE_FRAME_ORGS` turns on the paste frame (`chat_failure_modes.md`
§4.7b, HRD-14b). For each listed org, a native chat run puts each pasted block
of 8 or more lines in ONE `PASTED TEXT` block, in a copy of each model
request. The saved row and the stored session stay raw. `*` lists every org,
and the default is empty. Its one reader is
`acb_skills.paste_frame.paste_frame_on`. The injection asks it at the start
of each run, so a reload applies to the next run. Only `*` lets the `/v1` trim
of a dev box restore a cut block, because that door binds no tenant. The flip
is HRD-14e, and it is owner-only (`work_plan.md` §6.0 D4).

`CHAT_PIN_ORGS` turns on the pin of a chat (`chat_organization.md` §18,
CO-11). For each listed org, a member may pin a chat, and the chat list shows a
Pinned group. `*` lists every org, and the default is empty. Empty means the
list sends no pin key, and PUT and DELETE `/chat/sessions/{id}/pin` answer
404.

Its one reader is `gateway.chat_session_member.pin_allowed`. The chat list,
the pin routes and `GET /auth/me/shell` ask it on each call. The shell read
gives the answer to the client as `chat_pin`. So a tab shows a change at its
next shell read. A reload applies a change. The flip stays OWNER-GATE
(`enforcement-flip`), after the owner preview.

`CHAT_PIN_TOOL_ORGS` gives the chat of each listed org the tool `pin_chat`
(`chat_organization.md` §18, CO-11b). The owner gave the tool its own flag on
2026-11-21 (task 3563). `*` lists every org, and the default is empty. Empty
means no chat holds the tool, and the tool count does not move. The tool acts
only for an org on the pin list too, because the pin routes answer 404 with
the pin off.

Its one reader is `gateway.chat_session_member.pin_tool_allowed`, and it asks
`pin_allowed` first. The build of the orchestrator asks it at the start of
each run, so a reload applies to the next run. Any value other than empty is
OWNER-GATE, and only the owner flips it. The tool joins each request, so the
flip takes the trade of the tool cap in §18.

`CHAT_PROGRESS_NOTICE_ORGS` turns on the line of a slow tool
(`chat_run_continuity.md` §4.11, HRD-6). For each listed org, a tool call that
runs past 6 s sends the member one line. A WhatsApp run of a listed org
also gets the long cap of 7 minutes, and its sweep waits 8 minutes
(`bot_run.long_reads_on`). `*` lists every org, and the default is empty. Its
one reader is `acb_skills.progress_notice.progress_notice_on`. The injection
asks it at the start of each run, so a reload applies to the next run. A
reload that takes an org off the list while a long WhatsApp run works gives
that run the short stale time. A flip stays OWNER-GATE (`enforcement-flip`).

`CHAT_RESUME_ORGS` turns on the stored call of a card
(`chat_run_continuity.md` §4.12.4 item 1, RESUME-1a). For each listed org, the
row of a plain confirmation card of a listed write keeps the call that the
card guards. RESUME-2a: a late Approve of such a card then runs that call
once, in a new reply, with no member turn. RESUME-4a-2: a late decline
(`declined: true`) of such a card starts a short reply that runs no tool. The
model then says that nothing was done. This part is server only: no client
sends that request until RESUME-7. `*` lists every org, and the default is
empty. A flip stays OWNER-GATE (`enforcement-flip`).

RESUME-2b: when the parked turn is the last turn, the call goes on in the
same reply. For an org on both lists, a park keeps the stream of the run 24
hours (D-RES-7). The same flag turns on this case A, so it has two more
preconditions.

RESUME-5: the stop of a release parks each run that waits on a card
(`POST /internal/drain/park`). It parks only the runs of an org on both
lists, with `CHAT_DURABLE_ASKS` on. A wait reads the two lists when it starts, and
the park reads them again. So a reload applies to the next card, and a
removal also applies to a card that waits now.

⚠️ Do not add an org to `CHAT_RESUME_ORGS` before both of these merge:

1. RESUME-3b (task 3475), the chat side of the same reply.
2. RESUME-2d, the reconnect replay that obeys the history waterline and the
   label (`chat_run_continuity.md` §4.12.7). Before it,
   `GET /agent/run/{thread_id}/reconnect` replays a parked stream from `0-0`
   for 24 hours. It checks no waterline and no caps of the reader.

⚠️ RESUME-2a needs `CHAT_LATE_APPROVE_ORGS` for the same org too. Without it,
a late Approve takes the path of before. The SPENT mark stops the stored call
of a twin card from writing twice. Since task 3467, either flag writes that
mark. No client reads the answer `resumed` before RESUME-3, so do not add an
org to `CHAT_RESUME_ORGS` before RESUME-3 merges.

⚠️ Keep this order for each org (`chat_run_continuity.md` §4.12.4 item 2).
Put the org in `CHAT_LATE_APPROVE_ORGS` BEFORE you put it in
`CHAT_RESUME_ORGS`. Remove the org from `CHAT_LATE_APPROVE_ORGS` only
`chat_ask_ttl_hours` (168 h by default) after you remove it from
`CHAT_RESUME_ORGS`. A box that runs code from before task 3467 writes the mark
only under `CHAT_LATE_APPROVE_ORGS`. Without the order, an old card can then
write its act a second time.

Its one reader is `acb_skills.stored_call.chat_resume_on`. The card gate asks
it at each card, and the injection asks it at the start of each run. The late
answer route asks it at each late Approve, and the park asks it at each park.
So a reload applies to the next card, park and Approve. The call id applies
from the next run.

`CHAT_RUN_PHASE_ORGS` turns on the phase of a streamed run
(`chat_failure_modes.md` §6 row CS-3, CS-3a). For each listed org, a
streamed run sends the CUSTOM event `run_phase`: `gathering` when its setup
starts, and `asking` when the model request of a native run opens. No client
draws it yet, and no saved message keeps it. `*` lists every org, and the
default is empty. Its one reader is `acb_skills.run_phase.run_phase_on`. The
executor asks it once, right after `RUN_STARTED`, so a reload applies to the
next run. A flip stays OWNER-GATE (`enforcement-flip`).

`CHAT_SEARCH_ORGS` opens the past chats of `search_company`
(`find_engine.md` §3.1 rung 6, §6, HRD-21). For each listed org, the search
reads the member's own solo chats with Mira, and never the current chat. `*`
lists every org, and the default is empty. Empty gives the answer of before
HRD-21, byte for byte. That includes the 422 of an unknown app: `apps=chat`
gets it, and its list of keys has no `chat`.

The search never shows a chat that holds the egress mark: the mark of an
email-assistant run or another untrusted read. The SQL does not test the
mark. `egress.marked_sessions` drops each marked chat after the read. So a
chat that read mail with no mark can show (`find_engine.md` §6, limit 1).

Its one reader is
`gateway.retrieval.find.chat_search_named`, and each search asks it. A reload
applies a change. The flip waits for the owner's preview (task 3225).

`CHAT_TOPICS_ORGS` turns on the topics of the chat list
(`chat_organization.md` §6, CO-2a). For each listed org, `/chat/topics`
lists the company topics and the member's own topics. A member makes a
private topic, and an admin renames, hides or merges a company topic. `*`
lists every org, and the default is empty. Empty means each topic route
answers 404.

Its one reader is `gateway.chat_topic.topics_allowed`, and each topic route
asks it first. So a reload applies at the next request. The flip stays
OWNER-GATE (`enforcement-flip`), after the owner preview.

CO-2b adds three readers of the same flag. `PUT /chat/sessions/{id}/topic`
moves a chat, and answers 404 with the org off. The chat list sends
`topicId` and `topicName` only with the org on. `GET /auth/me/shell` gives
the answer to the client as `chat_topics`, so a tab shows a change at its
next shell read.

`COMPUTE_RUN_ORGS` is the kill switch of the `compute` target of the sandbox
broker (`agent_sandbox.md` §0c rung 6a', SBX-18). It does not turn compute on.
Compute needs `compute:<org>` in `MAF_CODING_SCOPE`. That is a sealed owner
gate, and a change needs a restart. Never put `compute:*` there while
`SELF_SERVE_SIGNUP_ENABLED` is true. It is true on production today.

While it is true, a `compute:*` scope entry or a `*` in `COMPUTE_RUN_ORGS`
refuses compute for every org, also an org that the list names. This is
intended, and it is not a fault.

For each org in both lists, the broker runs Python over data that the chat
holds. The container has no network, no secret and no mount. `*` in this flag
lists every org, and the default is empty. Empty means the broker refuses
every compute run.

The one reader is `orchestrator.sandbox_broker.compute_enabled`.
The reaper asks it too, so a flip to off stops a running compute container at
the next tick. In production the broker also needs a remote gVisor backend. A
reload applies a change. The flip stays OWNER-GATE (`enforcement-flip`).

`CONNECTED_SYSTEMS_ORGS` turns on the "Connected systems" block of the chat
(`chat_connected_systems.md` §3.2, CSY-1). For each listed org, a chat that
attached an MCP server gets one block at the end of the per-turn context: the
brief of each attached server and the standing rule. `*` lists every org, and
the default is empty. Empty means the context keeps its bytes. Its one reader
is `orchestrator._maf_mcp.connected_systems_org_allowed`, which
`executor._connected_systems_block` asks on each turn, on the run's org. A
reload applies a change. The flip stays OWNER-GATE (`enforcement-flip`), after
the owner preview.

`CONNECTION_MODEL_ORGS` turns on the store of the semantic model of a
connection (`semantic_model.md` §10.4, SEM-12). For each listed org, the one
writer `gateway.connections.semantic_model` may write a draft, accept a part,
approve a version and read the approved version. `*` lists every org, and the
default is empty. Empty means each write refuses with "off", and the read
returns nothing.

It has two readers. `gateway.connections.semantic_model.connection_model_allowed`
gates the model. `decide_features.mode_for` gates the decide step of the
profiler, through the prefix `connection.` (SEM-13b). Both use the one rule
`decide_features.org_listed`, and
`test_connection_profiler.py::test_the_two_readers_of_connection_model_orgs_agree`
fences it.

Each reader takes the org of the bound tenant, on each call. So a
reload applies to the next call. A flip is the gate `enforcement-flip`.

`CORE_EVENTS_ORGS` turns on the events of the core apps on the one event bus
(`ai_studio.md` §14 E12a). For each listed org, a person edit emits one
`people.person.updated` event after the commit. `*` lists every org, and the
default is empty. Its one reader is `gateway.core_events.core_events_allowed`,
and each emit asks it. A
workflow that listens still needs its org on `WORKFLOW_UNATTENDED_ORGS`. A
reload applies a change, and the flip stays OWNER-GATE (`enforcement-flip`).

`CRM_MIRROR_AGE_ORGS` turns on the age line of the CRM read tools
(`crm_platform.md` §18.11.2, CRM-Q30c). For each listed org, a read of one
module or core entity adds one line when its Zoho mirror is held, not
available in Zoho, or last read 2 hours ago or more. `*` lists every org, and
the default is empty. Empty keeps each answer byte for byte, and the tools
send no request for the age.

Its one reader is `gateway.routes.crm.flags.mirror_age_allowed`.
`GET /crm/sync/mirror-age` asks it, and the tools ask it through
`crm_tools.mirror_age_on`. A reload applies a change. A name in the list
spends nothing, so the flip is `enforcement-flip`.

`CRM_MODULE_FIND_ORGS` turns on `q` across a module of the CRM
(`crm_platform.md` §18.17, CRM-Q34-7). For each listed org, `q` works on a
module list and on a module total. It finds each word in the record name, a
lookup name or a text value, and it marks a near spelling. `*` lists every org, and the
default is empty. Empty keeps the 422 of `q` on a module total, and the list
matches the record name only, byte for byte.

Its one reader is `gateway.routes.crm.flags.module_find_allowed`, and each
module read asks it. A reload applies a change. A name in the list spends
nothing, so the flip is `enforcement-flip`.

`CRM_MODULE_REREAD_ORGS` turns on the weekly full read of the generic modules
of the Zoho mirror (`crm_platform.md` §18.7, task 3869). For each listed org,
the sync reads each module that a cursor reads (Invoices, Quotes, a custom
module) again in full once in 7 days. Discovery also queues one full read of a
module whose stored pick-list values left the current options. `*` lists every
org, and the default is empty. Empty keeps the read of changes only, byte for
byte.

Its one reader is `gateway.routes.crm.flags.module_reread_allowed`. The re-read
rule of the engine and the drift check ask it on each run. A reload applies a
change. A full read spends Zoho API credits inside the budget of the sync run,
so the flip is `enforcement-flip`.

`FIND_CLAIM_CHECK` turns on the "not found" check of a chat answer
(`find_engine.md` §5.5, FIND-7). Its values are `off`, `shadow` and `on`, and
the default is `off`. The flag is global, but the check acts only for an org
of `FIND_ORGS`, because pass 2 exists only there. It is not a `*_ORGS` or
`*_MODES` key, so a reload does NOT apply it. Set the value, then restart
`acb-gateway`. Its one reader is `acb_common.settings.find_claim_check_mode`.

In `shadow`, the first answer of a run that says a record does not exist
writes one `answer_check.claim` line. The outcome is `shadow_miss` when no
`search_company` answer of the turn shows pass 2, and `shadow_pass` when one
does. The line holds `mode`, `outcome`, `rule`, `searches`, `finds` and
`armed`, and no text. `armed` is true when a `search_company` or
`resolve_name` answer came before the claim, and false when only an app's
search tool ran, or none.

In `on`, the check holds an answer after the first result of any find tool
in the turn, an app's search tool too. Then a claim with no pass 2 gets one
more pass. That pass is the one retry of the run, which the number check of
`CRM_ANSWER_CHECK` spends too. A claim before any find result is not held,
and the line says `unheld_miss`. `shadow` is AGENT-SAFE, and `on` is an
`enforcement-flip` after 7 days of shadow (D-FIND-8).

`FIND_ORGS` turns on the new shape of a `search_company` hit
(`find_engine.md` §3.4, §3.5, §8, FIND-2). For each listed org, each hit says
how it matched (`matched_by`, `covers`, `of`), and the hit with the most of the
query's words comes first. `*` lists every org, and the default is empty. Empty
gives the answer of KR-ZERO, byte for byte. It acts only with `CONTEXT_RECENCY`
on.

The flag also opens the near spelling step of HRD-18b (`find_engine.md` §3.1
rung 3). On a miss, the best hit holds fewer than all the words (§3.3). Then
the search tries each word alone, and the near spelling of up to 3 words. The
near names come from rows that the member can open.

The flag also opens the related records of FIND-3 (`find_engine.md` §3.1
rung 4). The search follows the links of its first 5 hits that are a deal,
lead, contact, person or company. It shows at most 3 records for each hit,
in the answer key `related`. Each record is one that the member can open.

The flag also opens the honest zero, the explain mode and the log line of
FIND-4 (`find_engine.md` §3.6, §3.7). The answer names why the search skipped
each app. A zero answer adds at most 3 "Where else" lines from a closed list,
and no line names a record. No line says that a record does not exist.

`search_company(explain=true)` answers a trace of each step, with no record.
Each search writes one `search.company.ladder` log line. It holds counts,
times, `miss` and `explain`, and no word.

The flag also opens the sender near spelling of `GET /email/messages`, for a
caller that sends `near_sender`. Only `query_inbox` sends it. When
`from_email` finds no mail, the tool tries the near spelling of the sender and
names it. That part needs no `CONTEXT_RECENCY`.

The flag also opens the chat tool `resolve_name` and its route
`GET /search/names` (FIND-5, `find_engine.md` §5.2). The route gives each
person, contact, lead, company and mail sender that holds one name. An org
off the list gets 404. The orchestrator gives the tool only to an org on the
list, so the tool list of each other org does not change. That part needs no
`CONTEXT_RECENCY`. The next chat turn after a reload reads the change.

The flag also opens the family of FIND-6 (`find_engine.md` §5.3, §5.4). Each
find tool of the core list answers a failure as text, and that part needs no
`CONTEXT_RECENCY`. Two parts act only with `CONTEXT_RECENCY` on, because the
search runs its ladder only then. The persona ends its grounding paragraph
with the standing rule. A zero of `search_crm`, `query_inbox` and
`find_tasks` ends with a pointer to `search_company`. The next chat turn
after a reload reads the change.

Its one reader is `gateway.retrieval.find.find_allowed`, and each search
asks it. A reload applies a change. The flip is AGENT-SAFE under the
`enforcement-flip` grant, after the FIND-1 eval.

`GITHUB_APP_ORGS` turns the Metorite GitHub App on for each named org
(`agent_coordination.md` SW-53). `GITHUB_APP_ENABLED` stays the master, and a
reload does not apply the master. With the master on, an empty list turns
every org off. Its one reader is
`gateway.connections.github_app.github_app_org_allowed`. The webhook, the
connect routes, the poll and `github.installation_of` ask it, so the lazy
readers follow the list too. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`HOME_TOOLS_ORGS` gives the chat of each listed org the five Home tools
(`chat_parity.md` §6.4, PAR-8b, task 3903). They read the Home feed, Needs
you, the brief, the quick find and the Home settings. They change a Home card,
a Home setting and the default sidebar, and send a test notification. `*`
lists every org, and the default is empty. Empty means no chat holds the
tools, and the tool count does not move.

Its one reader is `acb_skills.home_tools.home_tools_on`. The build of the
orchestrator asks it at the start of each run, so a reload applies to the next
run. Any value other than empty is OWNER-GATE, and only the owner flips it.
The five tools join each request, so the flip takes the trade of the tool cap
in `chat_parity.md`, PAR-8b facts for the owner.

`INTEGRATION_ACCESS_ORGS` opens the two admin routes that set who can use each
integration (`integrations_console.md` §3.9.4, ACC-1). Off,
`GET /admin/integration-access` and `PUT /admin/integration-access/{service}`
answer 404, and the console shows no control. `*` lists every org, and the
default is empty. The flag gates the routes only. The access read applies a
stored rule whatever the flag says. So when the flag goes off, no member gets
an integration back.

Its one reader is `gateway.routes.admin._common.integration_access_allowed`.
Each request asks it, so a reload applies at once. The flip waits for the
owner preview, and it stays OWNER-GATE (`enforcement-flip`).

`MCP_IMAGE_UPLOAD_ORGS` lets an outside agent upload an image to a task and
put images on a question (`external_agent_access.md` §12.9, CIV-14b). Off, the
upload of a signed agent answers 404 and `attachment_ids` answers 422, and the
MCP edge maps both to one fixed text (`tools.TEXT_IMAGES_OFF`). A person's upload
in the app does not read it. Its one reader is
`acb_auth.agent_oauth.image_upload_allowed`. The gateway asks it at each call,
so a reload applies a change there. The agent edge asks it too (CIV-14b fix
round 1). It hides `upload_task_image` for an org off the flag, and
`ask_on_task` checks it before a run starts. Since fix round 2, a body over
1 MiB of an org off the flag gets the 413 of the old cap, with no slot and no
read. The edge reads its flags at
start. So set the same value in the env file of both units, reload
`acb-gateway`, and restart `acb-agent-edge`. The flip stays OWNER-GATE
(`enforcement-flip`).

`MCP_MAIL_PRECEDENCE_ORGS` turns on the precedence rule of a connected server
over a sticky mail pack (`chat_connected_systems.md` §3.4, CSY-4). For each
listed org, a turn whose words name an attachable MCP server evicts each sticky
mail pack, so the server attaches. It acts only when the chat's mail mark reads
set. `*` lists every org, and the default is empty. Empty keeps the choice of
main.

Its one reader is `acb_skills.egress.mcp_mail_precedence_on`, which
`tool_packs.choose` and `_tool_injection._with_server_words` ask on each turn,
on the run's org. A reload applies a change. Any value other than empty is
owner-only (`work_plan.md` §6 D4), and the §3a window does not open it.

`MY_DAY_LEARN_ORGS` lets the My Day feed of each listed org learn from the
member (`proactive_loop.md` §7.2, LOOP-4a). The feed reads the kind mutes of
the member, and it adds the affinity of each kind to the score. A needs card
goes to Later at worst, and the bell does not change. `*` lists every org, and
the default is empty. Empty gives the answer of before, byte for byte.

The reach loop, the briefs and the chat brief read the same learned feed. So
a kind that the member mutes leaves them too. A muted needs card in Later is
no interrupt, and it is in no brief. LOOP-5 owns the rest of the effect on
reach.

Its one reader is `gateway.routes.shell.state.learn_allowed`, and each feed
read asks it. The read cache keys on the flag, so a flip misses at once. A
reload applies a change. The flip is `enforcement-flip`, and it waits for the
owner preview (D218).

`PACKAGE_HOST_ORGS` opens `GET /shell/manifest` (`package_host.md` §10). It
also selects the orgs where the daily expiry tick may delete installs
(PKG-10b). A reload that widens it to `*` widens the delete to every org at
the next tick. The owner must treat a change of this list as part of the
expiry decision. `PACKAGE_EXPIRY_ENABLED` does not reload. Only a restart
changes it.

`PROJECT_EDIT_CARD_ORGS` turns on the card of a new task that the member
edits (`projects_ai_chat.md` §32, EAC-1). For each listed org, the card of
`create_task` carries a form of its values, and the write takes the values
that the member approved. `*` lists every org, and the default is empty. Its
one reader is `acb_skills.task_card.task_edit_card_on`. The tool and the card
gate ask it at each card, so a reload applies to the next card. A flip stays
OWNER-GATE (`enforcement-flip`, EAC-7).

`PROVIDER_ALERT_ORGS` names the org whose admins hear when an AI vendor runs
out of credit or runs low (`customer_console.md` §6A.17b, AO-2). They get one
bell row and one phone push. Set it to the operator org id. The default is
empty, and empty sends nothing. The reader refuses `*`, because a customer's
admins must never get our vendor alerts. Its one reader is
`gateway.provider_alerts.alert_orgs`. The seed hook, the job and the bell
source ask it on each call, so a reload applies at the next 15-minute bucket.
It needs `JOB_QUEUE` on, and the deployment key needs `serve`. A flip stays
OWNER-GATE (`enforcement-flip`).

`SEMANTIC_MODEL_ORGS` turns on the semantic kinds of the CRM plan
(`semantic_model.md` §3.4, SEM-7). For each listed org, the plan checks the
kinds `measure`, `join` and `query`, and `org_facts` serves the approved
ones. `*` lists every org, and the default is empty. Empty keeps each item of
these kinds as an unknown kind, and serves none.

The same flag turns on `query` on a module total (`semantic_model.md` §5.1,
SEM-1b). For each listed org, `GET /crm/aggregate` on a module takes one JSON
query of conditions and `any` groups. Off the list, a `query` is a 422 that
names `field_filters`, and each other answer stays byte for byte.

Its one reader is `gateway.routes.crm.flags.semantic_model_allowed`. Each
plan write, each facts read and each module total ask it. A reload applies a
change. The flip stays OWNER-GATE (`enforcement-flip`).

`SEQUENCE_MARKS_ORGS` lets an outside agent mark each number that its own run reserved
`used` or `released` (`mark_number`, `agent_coordination.md` SW-27c1). Off, the
mark route answers 404 for a signed agent, and the edge hides the tool. Its one
reader is `acb_auth.agent_oauth.sequence_marks_allowed`. The gateway asks it at
each call, so a reload applies a change there. The edge reads its flags at
start, so set the same value for both units and restart the agent edge. Any
value other than empty is gate `enforcement-flip`.

`STUDIO_CHAT_TOOLS_ORGS` opens the catalog source `tool` of AI Studio
(`ai_studio.md` §14 TOF-1a). For each listed org, `GET /studio/actions` serves
one `tool:<name>` row for each chat tool of the CRM, Email, Projects and
Calendar packs. A member can then put that tool in a skill. `*` lists every
org, and the default is empty. Its one reader is
`gateway.routes.studio.flags.chat_tools_allowed`, and each catalog read asks
it. The catalog also needs `STUDIO_ORGS`.

The flag also gates the skill load (TOF-1b). The run reads it once a turn,
through `_tool_injection._chat_tools_open`. For an org that the flag leaves
out, each skill with a `tool:` action fails to load with `chat_tools_off`. So
an operator who removes an org stops every such skill of that org at the next
turn. For a listed org, the skill brings the code pack of each such tool that
the run base does not hold.

A row runs nothing itself. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`STUDIO_CONNECTION_GATE_ORGS` makes the `connections` of a Studio app bind
(`ai_studio.md` §14 I1a). For each listed org, a missing connection makes the
install `needs_setup`, and the action door refuses each action with 409
`needs_connection`. `*` lists every org, and the default is empty. Empty
means the connections change nothing. Its one reader is
`acb_common.studio_flags.connection_gate_on`, and each call asks it. A reload
applies a change. A flip stays OWNER-GATE (`enforcement-flip`), after the
pre-check of the I1a row.

Since I9a, an `org` need also fails closed with a connected row in four cases. A name with no `as` is an `org` need, unless its connector is a `member` connector (mail, WhatsApp, the calendars and Drive). A `member` need is met only by a connected row of the viewer.

An `org` need fails in an org with no approved org install. It fails in a private install and in a group install. It fails in an org install of a version with `review_status` `auto`, which migration 114 sets by default. It fails for the owner of the app, who runs the draft.

Before a flip, count three things with read-only SQL. Count the active org installs whose version is not `approved`. Count the private and group installs whose version names a `connections` list. Count the connected rows of each provider, as org rows and as member rows.

`STUDIO_DOOR_OPS_ORGS` opens the door op of the Studio action door
(`ai_studio.md` §14 B2b-2). For each listed org, a class A `<app>.<op>`
action of a package runs that op of the app, as the caller. `*` lists every
org, and the default is empty. Its one reader is
`gateway.routes.studio.flags.door_ops_allowed`, and each request asks it. The
door also needs `STUDIO_ORGS`. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`STUDIO_LIVE_PREVIEW_ORGS` lets a preview of the Studio action door read a
class A MCP action live (`ai_studio.md` §14 I11a). For each listed org, a
`preview=1` call of an `mcp:<server>.<tool>` action calls that tool of the
org's server once, and the cut answer stays in the cache for 60 s. `*` lists
every org, and the default is empty. Empty means each MCP action of a preview
stays `source_not_open`. Its one reader is
`acb_common.studio_flags.live_preview_on`, and each call asks it. The door
also needs `STUDIO_ORGS`. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`STUDIO_LOGIC_ORGS` opens the logic module of the Studio action door
(`ai_studio.md` §14 B4a). For each listed org, a `logic:<name>` action of a
package runs a first-party module of the gateway. `*` lists every org, and the
default is empty. Its one reader is `gateway.routes.studio.flags.logic_allowed`,
and each request asks it. The registry of modules ships empty, so the flag opens
nothing until a module registers. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`STUDIO_NUM_F8_ORGS` adds the float8 test to a number filter of the Studio
object query (`ai_studio.md` §14 C2c). For each listed org, the query tests
`studio_record_hot.v_num_f8` beside the exact `v_num` test, so the app role
can use the index. `*` lists every org, and the default is empty. Its one
reader is `acb_common.studio_flags.num_f8_allowed`, and each query asks
it. The query honours it for an org only after the backfill of migration 433 ends (`_warm_f8`), so the records are right at every step. Runbook: after a restore or any trigger work (`DISABLE TRIGGER`, `session_replication_role = replica`, `pg_restore --disable-triggers`), restart the gateway before you trust the flag, because a ready answer is cached for up to 600 seconds.
A reload applies a change, and the flip stays OWNER-GATE (`enforcement-flip`).

`STUDIO_SEARCH_ORGS` opens the `studio` source of company search
(`ai_studio.md` §14 C5a). For each listed org, `search_company` reads the
objects that a package names in `shell.search`, for the packages that the
member owns or has installed. `*` lists every org, and the default is empty.
Its one reader is `gateway.routes.studio.flags.search_allowed`, and each search
asks it. The source also needs `STUDIO_ORGS`. A reload applies a change, and
the flip stays OWNER-GATE (`enforcement-flip`).

`STUDIO_SKILL_OFFER_ORGS` opens the card "Save this as a skill?" in the chat
(`ai_studio.md` §14 TOF-1d). For each listed org, a finished turn that passes
the rule of the offer draws the card, and Save makes a private draft skill. `*`
lists every org, and the default is empty. Empty means the chat draws no card
and reads no catalog for it. Its one reader is
`gateway.routes.studio.flags.skill_offer_allowed`, and each shell read asks it.
`GET /auth/me/shell` serves the answer to the chat as `skill_offer`. The offer
also needs `STUDIO_ORGS`, and it maps a chat tool to a catalog row only for an
org on `STUDIO_CHAT_TOOLS_ORGS`. A reload applies a change, and a tab shows it
at its next shell read. The flip stays OWNER-GATE (`enforcement-flip`), after
the owner preview.

`STUDIO_SKILL_REPAIR_ORGS` opens the record of skill runs
(`ai_studio_rows/F12.md`, TOF-3a F12b). For each listed org, a chat run that
loaded a saved skill writes one row in `studio_skill_runs` for each skill. A
test run writes one row for each test that failed with a fault of the skill.
`*` lists every org, and the default is empty. Empty means that no writer
writes a row. Its one reader is
`gateway.routes.studio.flags.skill_repair_allowed`.

Each writer of skill runs also needs `STUDIO_ORGS`. A reload applies a change. Any value other than empty is
OWNER-ONLY, because the repair of F12c spends credits.

`STUDIO_SKILL_TESTS_ORGS` opens the test run of a skill
(`ai_studio_rows/F4.md`, TOF-3b F4b). For each listed org, a member may call
`POST /studio/skills/{id}/tests/run`. Each test is a real model turn in a dry
run, so a run SPENDS CREDITS. `*` lists every org, and the default is empty.
Empty means the route answers 404 and runs nothing. Its one reader is
`gateway.routes.studio.flags.skill_tests_allowed`, and each request asks it.

The route also needs `STUDIO_ORGS`, and it refuses each run until the owner
sets `STUDIO_SKILL_TESTS_CAP_CREDITS`. That cap has no default, and it needs a
restart, because it is no `*_ORGS` key. A reload applies a change to this
flag. Any value other than empty is OWNER-ONLY, because it spends credits.

`TOOL_FAILURE_LOG_ORGS` turns on the record of tool failures for each listed
org (`tool_failure_log.md` §6, TOF-2b). A capture site writes one row in the
org and adds one count to the platform rows of the failure store. `*` lists
every org, and the default is empty. Its one reader is
`acb_common.failure_log.failure_log_on`, and each record asks it. A reload
applies a change, and the flip stays OWNER-GATE (`enforcement-flip`). Flip
`TOOL_FAILURE_TASKS` with it and restart, or the event rows stay with no prune.

`TOOL_SEARCH_ORGS` gives the chat of each listed org the tool `find_tools`
(`one_assistant_context.md` §4.2.9, TS-2a). It acts only where `TOOL_PACKS`
gives a pack. `*` lists every org, and the default is empty. Its one reader is
`acb_skills.tool_packs.tool_search_on`. The apply of the tool packs asks it at
the start of each run, so a reload applies to the next run. Any value other
than empty is OWNER-ONLY (`work_plan.md` §6 D4), and the §3a window does not
open it.

`TOOL_TURN_BUDGET_ORGS` turns on the per-turn budget of a read tool
(`chat_failure_modes.md` §4 row B5, HRD-5). It covers `search_company`
and `find_tasks`. For each listed org, one of these tools may give
3 empty or refused answers in a turn. Its next call in that turn does not run.
The answer tells the chat to ask the member one question.

`*` lists every org for this flag,
and the default is empty. Its one reader is
`orchestrator.tool_turn_budget.tool_turn_budget_on`. A flip stays OWNER-GATE
(`enforcement-flip`).

`WAITING_ORGS` opens `GET /shell/waiting` and `GET /shell/waiting/count`
(`waiting_on_me.md` §7.3). Its one reader is
`gateway.routes.shell.waiting.waiting_allowed`. A reload applies a change. The
flip stays OWNER-GATE (`enforcement-flip`).

`WORKFLOW_STUDIO_GATE_ORGS` turns on the authoring gate of `/workflows`
(`ai_studio.md` §14 J1-3). For each listed org, a workflow write needs
`feature:studio` on top of `feature:workflows`. `*` lists every org. Its one
reader is `gateway.routes.workflows.core.author_gate_on`, and each request
asks it. A reload applies a change, and the flip stays OWNER-GATE
(`enforcement-flip`).

A reload does NOT apply any other key. The log names each one under
`restart`. That includes:

- an on/off switch such as `CALENDAR_REMINDERS`, `JOB_QUEUE` or
  `WEB_PUSH_ENABLED`. A background loop decides at start whether it runs.
- a security gate such as `V1_ALLOW_CALLER_ENDPOINT_OVERRIDE`. A module
  copies it at import.
- each secret, URL, port and password, including `PGPASSWORD`.
- each `DB_` and `REDIS_` setting.

## The procedure

1. Make sure that the gateway has the reload. If `flag_reload` is not
   `true`, stop. A gateway without the handler ends on SIGHUP, and systemd
   then starts it again with no drain.

   ```bash
   curl -s http://127.0.0.1:8080/version
   ```

2. Make a backup of `/opt/acb/app/.env`. Then change one `*_ORGS` or
   `*_MODES` line.
3. Send SIGHUP to the main process only:

   ```bash
   sudo systemctl kill -s HUP --kill-whom=main acb-gateway
   ```

   ⚠️ Keep `--kill-whom=main`. Without it, systemd sends the signal to each
   process of the unit, and a child process of an agent ends on SIGHUP.

4. Read the result. The line names keys, never values.

   ```bash
   sudo journalctl -u acb-gateway -n 50 -o cat | grep flags.reload
   ```

   - `flags.reloaded applied=[...]`: those flags are live.
   - `restart=[...]`: those keys changed in the file and need a restart. Do
     the restart at a quiet time, and say why in the same message.
   - `flags.reload_refused`: the new settings did not validate. Nothing
     changed. Fix the line and send SIGHUP again.

5. Check that the process did not change
   (`systemctl show acb-gateway -p MainPID`). `flags_reloaded_at` in
   `/version` moves only when a flag was applied.

## How the reload keeps other keys as they are

The reload builds the new settings from the RUNNING settings plus the
applied flags. It does not read the environment or the env file again. So a
key that the file gained after the start stays dormant until a restart.

## Fences

- No module reads a reloadable flag at import.
- No start function of the gateway lifespan reads one. The fence resolves
  calls as Python does, three calls deep. A loop that a start function
  spawns reads its flags on each tick, so the fence does not enter it.
- No module keeps `get_settings()` in a module name, and no new cached
  function reads the settings.

The reload covers the gateway only. The workbench, the agent edge and the
customer console still read their flags at start.
