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

`CHAT_LATE_APPROVE_ORGS` turns on the late-approve grant
(`chat_run_continuity.md` S2 item 8, HRD-22). For each listed org, a late
Approve of a parked plain card approves the same card once in the next run.
`*` lists every org, and the default is empty. Its one reader is
`acb_skills.late_approve.late_approve_on`, and the park, the late answer and
the card gate each ask it. So a reload applies at once, and an org that leaves
the list uses no grant. A flip stays OWNER-GATE (`enforcement-flip`).

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
once, in a new reply, with no member turn. `*` lists every org, and the
default is empty. A flip stays OWNER-GATE (`enforcement-flip`).

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
answer route asks it at each late Approve. So a reload applies to the next
card and the next Approve, and the call id applies from the next run.

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
reads the member's own solo chats with Mira, and never the current chat or a
chat that read mail. `*` lists every org, and the default is empty. Empty
gives the answer of before HRD-21, byte for byte. That includes the 422 of an
unknown app: `apps=chat` gets it, and its list of keys has no `chat`.

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

`CORE_EVENTS_ORGS` turns on the events of the core apps on the one event bus
(`ai_studio.md` §14 E12a). For each listed org, a person edit emits one
`people.person.updated` event after the commit. `*` lists every org, and the
default is empty. Its one reader is `gateway.core_events.core_events_allowed`,
and each emit asks it. A
workflow that listens still needs its org on `WORKFLOW_UNATTENDED_ORGS`. A
reload applies a change, and the flip stays OWNER-GATE (`enforcement-flip`).

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

`TOOL_SEARCH_ORGS` gives the chat of each listed org the tool `find_tools`
(`one_assistant_context.md` §4.2.9, TS-2a). It acts only where `TOOL_PACKS`
gives a pack. `*` lists every org, and the default is empty. Its one reader is
`acb_skills.tool_packs.tool_search_on`. The apply of the tool packs asks it at
the start of each run, so a reload applies to the next run. Any value other
than empty is OWNER-ONLY (`work_plan.md` §6 D4), and the §3a window does not
open it.

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
