# HANDOFF — what the last session left unfinished

**This file is a QUEUE OF ACTIONS, not a status board.** It is injected into
every session by `.claude/hooks/session-handoff.mjs` (D39) so nobody has to
remember what was in flight.

---

## The one rule that keeps this file honest

> ⚠️ **Never restate state here. Point at it, and carry the command that
> re-derives it.**

**Metorite holds the current state** (D118, CLAUDE.md §1). A
handoff file that also described state would be a second board, and a second
board is the CLAUDE.md §5 defect by construction: two descriptions of one truth,
one of which stops being updated first, and the stale one is trusted because it
is the one loaded into the prompt.

So every entry below carries a **Check** — a command whose output tells you
whether the entry is still real. You do not trust this file. You run the Check.

That inverts the usual failure. A stale entry here costs one command and gets
deleted; it can never *quietly* be believed, because believing it requires
running something that would have contradicted it.

## The protocol

**At the start of a session** — before any other work, and before picking up
whatever you were asked to do:

1. Run the **Check** for every entry.
2. **Delete every entry whose Check shows it is done**, and move its Metorite
   task to done. In your first commit.
   Deleting a finished entry is not optional housekeeping — an entry that
   outlives its work is how this file starts lying.
3. Report what is left to the user in one short list, then proceed.

**During a session** — the moment you create an obligation, create its Metorite
task (`create_task`) with the why and the steps. Then add the four-line entry
here. Do it then, not at the end when context is short. The entry you write while you still remember why
is worth five you reconstruct.

**Before ending a session** — add an entry for anything you are handing over:
work you started, an owner gate you hit, a finding you scoped out, a decision
you are owed. If you are not sure it matters, add it. A one-line entry costs
nothing; the thing nobody wrote down costs a session.

**`/handoff`** does all of this — see `.claude/commands/handoff.md`.

## The shape of an entry

The full text of an entry lives on its Metorite task (D118). This file keeps
four lines: the title, the pointer, the Check and the date. The hook lists the
titles, and a session runs each Check.

```
### H-<n> · <one line, imperative> · [AGENT|OWNER]
- **Metorite:** #<task number>. The task holds the full entry.
- **Check:** `<command>` → <what output means STILL PENDING>
- **Added:** <date> · <session or PR that created it>
```

An entry with no Metorite task yet keeps its full text here (Why, Do this,
Authority). When you next touch it, create its task, move the text there, and
shrink the entry to the four lines.

`[OWNER]` marks an entry an agent **must refuse by name** (`work_plan.md` §6).
An agent may verify an OWNER entry's Check and report it; it may never do it.

Ids are never reused. Delete the whole block — do not tick it off in place, or
this file grows a graveyard and the graveyard is what goes stale.

**Fence: `tests/unit/test_handoff_queue.py` (R7).** Mint the next id against
**`origin/main`**, not against your branch — two branches in flight each pick
"the next free id" from the base they were cut from, and whichever merges second
carries an id `main` has since taken. That merge is CLEAN, so nothing surfaces
it: it happened to H-27 and again to H-28 on 2026-08-25. If the fence fails,
renumber the entry that merged **second** and note the move in its `Added:`
line — never reclaim a number by deleting the other entry.

---

# OPEN

### H-433 · Check the chat prompt for notifications on a real phone, on dev.metorite.com · [OWNER]
- **Metorite:** #600 (OB-8). The task holds the checks and the state.
- **Check:** `ssh metorite 'sudo -n grep -c "^PUSH_PROMPT_ORGS=" /opt/acb/app/.env'`. A count of 0 means production still waits for the owner's check. An agent sets the flag only after the owner says yes.
- **Added:** 2026-10-30 · WS-OB OB-8 session

### H-434 · Check the assistant's offer and approval cards on dev.metorite.com · [OWNER]
- **Metorite:** #1114. The task holds the full entry.
- **Check:** `ssh metorite 'sudo -n grep -c "^ASSISTANT_PROFILE_CARD_WRITES_ORGS=" /opt/acb/app/.env'`. A count of 0 means production still waits for the owner's check. An agent sets the flag only after the owner says yes.
- **Added:** 2026-10-30 · WS-63 PZ-32 session

### H-436 · Decide whether work words stop tripping the sensitive-fact card · [OWNER]
- **Metorite:** #1116. The task holds the question and a recommendation.
- **Check:** task #1116 is not Done → still pending.
- **Added:** 2026-10-30 · WS-63 PZ-32 session

### H-416 · Decide whether the staging apply installs the staging flag tool itself · [OWNER]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** on the dev box (srv1747539), run
  `cmp /opt/acb/app/deploy/hostinger/staging/acb-staging-flag /usr/local/sbin/acb-staging-flag`.
  A difference means the installed tool is behind the repo copy. If it exits 0, or the owner has said no, delete this entry.
- **Added:** 2026-10-26 · owner, in chat, 2026-10-25

### H-431 · Look at Settings → Assistant settings, and say what still feels stiff · [OWNER]
- **Metorite:** #261 (PZ-11). The task holds the checks and the state.
- **Check:** task #261 is not Done → still pending. On app.metorite.com: reload, open the account menu, choose Settings → Assistant settings, then start an empty chat and press "Yes, start". Tell the agent what looks wrong and what feels stiff.
- **Added:** 2026-10-27 · WS-63 session end (first minted as H-419, renumbered: main took it)

### H-335 · Finish the staging box: DNS, two OAuth clients, one sudo run · [OWNER]
- **Metorite:** #640. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `curl -s https://api.dev.metorite.com/version` answers a `sha` equal
  to `git rev-parse origin/staging`. If it does, delete this entry.
- **Added:** 2026-10-16 · WS-38 staging session

### H-361 · Raise the WSL2 CPU cap, so local CI runs in about 10 minutes · [OWNER]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -E "^processors=" ~/.wslconfig` on the owner's PC reads 16
  or more. If it does, delete this entry.
- **Added:** 2026-10-19 · CI speed-up session

### H-283 · Prove the first `credit_draw` row on production · [AGENT]
- **Metorite:** #516. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** on the box, query the Console database:
  `SELECT count(*), max(created_at) FROM credit_draw;`. A count above zero
  means the write path runs in production, and this entry is done.
- **Added:** 2026-10-09 · WS-50

### H-284 · Two revenue figures the Money page cannot show yet · [AGENT]
- **Metorite:** #532. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "price_paid_inr" apps/services/customer_console/customer_console/main.py`
  inside `ManualActivationRequest`. No hit means gap 1 is open.
  `rg -n "def all_lots|credits_paid_total" apps/services/customer_console`
  with no hit means gap 2 is open.
- **Added:** 2026-10-09 · WS-50

### H-150 · The right-click menu on a task cannot be opened on a phone · [AGENT]
- **Metorite:** #486. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** open `/projects` at 390px, pick a project, and long-press a
  card. If no menu appears, this is open.

### H-417 · Build WS-67 OA-7b to OA-10: one chat, tool packs, dynamic sub-agents · [AGENT]
- **Metorite:** #452. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** the WS-67 row of `project-docs/work_plan.md` says OA-10 is BUILT. Then delete this entry. OA-1 to OA-5, OA-6 (D117), OA-7a, OA-7g and the design are done.
- **Added:** 2026-10-26 · chat-speed session

### H-415 · After single mailbox: the shared mailbox SM-3 to SM-9, then multiple mailboxes · [AGENT]
- **Metorite:** #733. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** the WS-65 row of `work_plan.md` says SM-9 is BUILT. Then delete this entry.
- **Added:** 2026-10-25 · email single-mailbox session

### H-412 · The chat-speed work: build the delegated-path slices for the paths that stay after WS-67 · [AGENT]
- **Metorite:** #26. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** the next chat-speed PR after #1323 names WS-67 and `specs/one_assistant_context.md` §7. When it does, delete this entry.
- **Added:** 2026-10-25 · email single-mailbox session

### H-366 · Harden the CRM UX batch of 2026-10-20 (CRM-U6 to U9) · [AGENT] · security
- **Metorite:** #662. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `git log --oneline origin/main | grep -c "harden.*CRM-U[6-9]"` is 0. Then this is open.
- **Added:** 2026-10-20 · `crm-module-filters`

### H-365 · Replay the CRM answer check on stored answers, then decide `on` · [OWNER]
- **Metorite:** #735. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the production box, `grep -E '^CRM_ANSWER_CHECK=' /opt/acb/app/.env`
  reads `shadow`, and no replay result is in the round 8 PR. Then this is open.
- **Added:** 2026-10-20 · `crm-check-explain`

### H-294 · BH-6 moved the root units to /usr/local/lib/acb, and the runbooks still name the checkout · [AGENT]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "opt/acb/app/scripts/backup_db.sh|compose -f infra/docker-compose.yml" deploy/hostinger/README.md project-docs/specs/backup_and_restore.md README.md project-docs/specs/development_and_delivery_framework.md`. A hit means this is open.
- **Added:** 2026-10-10 · branch `sec-bh6-build`. It was H-288, and main took
  H-288 to H-293 first, so it moved to H-294 at the merge.

### H-295 · Remove the one-release belt of `backup_db.sh` after BH-6 · [AGENT]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "belt for ONE release" scripts/backup_db.sh`. A hit means this is open.
- **Added:** 2026-10-10 · branch `sec-bh6-build`

### H-292 · Show the org's plan, credits and AI usage in Settings → Organization · [AGENT] · OWNER note
- **Metorite:** #518. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "usage|credit|billing" workbench/control_plane/src/app/settings/organization/OrganizationAdmin.tsx`.
  No tab that names billing or usage means this is open.
- **Added:** 2026-10-10 · branch `ws51-run-cap-and-todos`

### H-291 · Settle a parked question when its move to `answered` fails · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n -B2 -A4 'to="answered"' apps/services/orchestrator/orchestrator/pending_ask.py`.
  If the move after `_answered(fut)` is still inside
  `contextlib.suppress(Exception)` with no retry, this is open.
- **Added:** 2026-10-10 · branch `ws51-run-cap-and-todos`

### H-289 · Send WS-51 S7's two chat events, "needs your answer" and "finished", over web push · [AGENT]
- **Metorite:** #761. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "send_to_member" apps/services/gateway/gateway --glob '!gateway/push/**'`.
  No hit outside `gateway/push/` means no chat event sends a push yet.
- **Added:** 2026-10-10 · branch `ws51-run-cap-and-todos`

### H-288 · Build WS-51 S6, the runs of the other signed-in accounts · [AGENT]
- **Metorite:** #761. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "slot|accounts" workbench/control_plane/src/app/api/chat/active-sessions/route.ts`.
  No read of a second account's cookie means S6 is open.
- **Added:** 2026-10-10 · branch `ws51-run-cap-and-todos`

### H-287 · Three AI paths spend money and write no usage row · [AGENT]
- **Metorite:** #517. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "litellm|_litellm" packages/acb_llm/acb_llm/context.py apps/services/gateway/gateway/routes/integrations.py`
  and `rg -n "v1/embeddings" apps/services/gateway/gateway/main.py`. If
  `acompletion_stream_text` still calls `acompletion` directly, this is open.
- **Added:** 2026-10-10 · branch `ai-call-attribution`

### H-285 · Stop the webhook classify from running beside the scheduler classify · [AGENT]
- **Metorite:** #731. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "H-285" apps/services/email_ingestion apps/services/gateway/gateway/routes/email`.
  No hit means this is open.
- **Added:** 2026-10-09 · branch `email-em-t16-pra` (EM-T16 PR-A). It was
  H-283. #789 took H-283 and H-284 on `main` first, so this entry moved to
  H-285 at the merge.

### H-282 · Remove the retired email model fields, then their columns · [AGENT]
- **Metorite:** #774. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "draft_model|compose_model|chat_model" apps/services/gateway/gateway/routes/email/automation/assistant.py`,
  then `rg -n "^    (draft|compose|chat|rule)_model" infra/postgres/schema.generated.sql`.
  A hit in either means this is open.
- **Added:** 2026-10-09 · branch `email-no-tier-settings` (EM-T15).

### H-275 · Measure the WhatsApp narrowing on a real run · [OWNER]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "compare run" project-docs/specs/data_narrowing_pipeline.md`.
  No line with a measured recall and a date under §9 N4 means this is open.
- **Added:** 2026-10-08 · branch `ws48-n4-whatsapp` (WS-48 N4).

### H-278 · The cost check of PICK uses the eval card, not the Router's prices · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "TODO\(H-278\)" packages/acb_skills/acb_skills/narrowing.py`.
  A hit means this is open.
- **Added:** 2026-10-08 · branch `ws48-pick-cost` (H-276).

### H-281 · Old auto-synced tool wrappers in the agent clones still give a script the whole env · [AGENT] · security
- **Metorite:** #691. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, run
  `ssh metorite 'grep -l "\*\*_os.environ" /home/acb/.acb/agents/repos/*/agents.py | wc -l'`.
  A count above 0 means this is open.
- **Added:** 2026-10-08 · branch `sec-bh1-child-env` (WS-49 BH-1, fix round 1).

### H-280 · A single route param can still move a gateway call to another route under its prefix · [AGENT]
- **Metorite:** #621. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n '\$\{(sessionId|subject|agentName|provider|threadId)\}' workbench/control_plane/src/app/api`.
  A hit that puts the raw param into a gateway URL, with no `refuseUnsafePath([...])`
  in that handler, means this is open.
- **Added:** 2026-10-08 · branch `proxy-path-guard`.

### H-274 · The chat has no batch tool for several new statuses · [AGENT]
- **Metorite:** #499. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "create_statuses" apps/skills/skill-projects/skill_projects/__init__.py`.
  While it prints nothing, this is open.
- **Added:** 2026-10-08 · H-273, branch `projects-vocab-batch`

### H-272 · plan-guard cannot see the secrets drop folder · [AGENT] · security
- **Metorite:** #726. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -c "metorite\[" .claude/hooks/plan-guard.mjs` → no hit
  means the guard does not know the drop folder, and this is open.
- **Added:** 2026-10-08 · secrets drop, security review of fix round 1

### H-271 · acb has the same power as root, so an app compromise can delete the off-box backups · [OWNER] · security
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, run each of these:
  `sudo cat /etc/sudoers.d/acb` · `id acb` · `stat -c '%U' /opt/acb/app/scripts/backup_offbox.sh`.
  While any one shows `NOPASSWD:ALL`, the `docker` group, or the owner `acb`, this is open.
- **Added:** 2026-10-08 · off-box backup, security review of fix round 2

### H-269 · Make the long-header test of the data engine pass on a busy CI runner · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `gh run list --workflow pr-check.yml --limit 50 --json databaseId,conclusion`,
  then search the logs of the failed runs for
  `test_a_long_header_keeps_each_answer_under_1_mb`. Each new failure with `error: memory` means
  this is open. If the test still loads 400 header names of 5000 characters with no change to
  the memory cap of the engine, this is open too.
- **Added:** 2026-10-08 · branch `email-s2-reading-pane` (WS-17 EM-S2 fix round 2). PR #722
  added H-268.

### H-267 · Stop an Outlook rule move from making a second copy of a nested folder · [AGENT]
- **Metorite:** #729. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "mailFolders" apps/services/email_ingestion/email_ingestion/providers/outlook.py`.
  If `_get_or_create_folder_id` still searches only `/me/mailFolders` with a
  `displayName` filter, this is open.
- **Added:** 2026-10-07 · branch `email-s10-rules-estimate` (WS-17 EM-S10
  fix round 1). PR #719 added H-266.

### H-268 · Measure the email narrowing saving on a real run · [OWNER]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "compare run" project-docs/specs/data_narrowing_pipeline.md`.
  No line with a measured recall and a date under §9 N2 means this is open.
- **Added:** 2026-10-07 · branch `ws48-n2-email` (WS-48 N2). Renumbered from
  H-267 at merge, because main took H-267.

### H-266 · Redis has no maxmemory policy, and the email HTML and attachment caches are bounded only by TTL · [AGENT]
- **Metorite:** #620. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `grep -rn "maxmemory" infra/ deploy/`. No `maxmemory` with a
  `maxmemory-policy` for the Redis of the gateway means this is open.
- **Added:** 2026-10-07 · branch `email-s1-html-route` (WS-17 EM-S1)

### H-265 · Measure the agreement of the two System-1 engines before `SYSTEM_ONE_ON_DECIDE` goes on · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "engine agreement" project-docs/specs/data_narrowing_pipeline.md`.
  No line with a measured percent under §9 N3 means this is open.
- **Added:** 2026-10-07 · branch `ws48-n3-decide-tier` (WS-48 N3). It was
  H-264, and it moved to H-265 because N1 (#711) merged first with H-264.

### H-264 · Let a decide request say that the narrowing pipeline sent it · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run
  `grep -n "X-CC-Source" packages/acb_auth/acb_auth/console_resolve.py`.
  No match in `_attribution_headers` means this is open.

### H-263 · Start `acb-pull.timer` again on the box, and prove that #702 serves · [AGENT]
- **Metorite:** #629. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `ssh metorite 'systemctl is-active acb-pull.timer'`. Any
  output other than `active` means this is open. Then run
  `curl -fsS https://api.metorite.com/version` and read `sha` and
  `applied_sha`. For each, run `git merge-base --is-ancestor 82d09830b <sha>`.
  A non-zero exit means #702 does not serve, and this is open. Last, run
  `ssh metorite 'docker image inspect pgvector/pgvector:pg17 >/dev/null && echo present'`.
  No `present` means the image is not on the box, and this is open.
- **Added:** 2026-10-07 · branch `ops-backup-io` (the backup I/O fix)

### H-262 · Decide if the Supabase compute size is large enough · [OWNER]
- **Metorite:** #508. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "H-262" project-docs/work_plan.md project-docs/specs/backup_and_restore.md`
  → no hit means the owner has not recorded a decision, and this is open.
- **Added:** 2026-10-07 · branch `ops-backup-io` (the backup I/O fix)

### H-261 · Stop the email sync from holding a transaction open while it waits on the provider (EM-T4a-4) · [AGENT]
- **Metorite:** #599. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "EM-T4a-4( PR-[A-Z0-9]+)? MERGED" project-docs/specs/email_app_master_plan.md`
  → no hit means this is open. The pattern also matches a part, for example
  `EM-T4a-4 PR-A MERGED`. If the slice ships in parts, the section
  "EM-T4a-4 — the request jobs" lists them. Then this stays open until each
  listed part has a hit. On the box, this query shows the symptom:
  `SELECT pid, now() - state_change AS age, query FROM pg_stat_activity WHERE state = 'idle in transaction' AND wait_event = 'ClientRead' ORDER BY age DESC;`
  A row older than a few seconds is a session that holds a transaction
  across I/O. The split of `_compose_assist_run` left EM-T4a-4 on 2026-10-12
  (§10.4.6 item 6). EM-T17 PR-A1 owns it, so this entry does not wait on it.
- **Added:** 2026-10-07 · branch `ops-backup-io` (the incident record)

### H-260 · Bind the gateway to 127.0.0.1, so the box cannot reach it on a public address · [AGENT]
- **Metorite:** #619. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "\-\-host 0.0.0.0" deploy/hostinger/acb-gateway.service`
  → a hit means this is open.
- **Added:** 2026-10-07 · WS-17 EM-T13b-2 review round 1 (branch
  `email-t13b-2`)

### H-257 · Keep a node with a hidden parent out of the top level of the tree drag · [AGENT]
- **Metorite:** #483. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "parentId === null\) return \[\.\.\.roots\]" workbench/control_plane/src/app/projects/lib/treeDrop.ts`
  → a hit means this is open.
- **Added:** 2026-10-06 · WS-46 P7 review round 1

### H-256 · Show on production that `user_settings` has a key for each organization · [AGENT]
- **Metorite:** #690. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on production, as an owner role, run
  `SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'user_settings'::regclass AND contype = 'p'`.
  The answer `PRIMARY KEY (organization_id, user_id)`, and a ledger line for
  `239_user_settings_per_org.sql`, close this entry. Delete it then.
- **Added:** 2026-10-06 · WS-46 P7 · built 2026-10-10

### H-255 · Check the pop-out and the discard of a reply by eye (EM-G3c-3) · [OWNER]
- **Metorite:** #658. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "EM-G3c-3 visual check passed" project-docs/specs/email_app_master_plan.md`
  → no hit means open. The owner reports the result. An agent then writes that
  line, with the date, under §12.3.3c. Then it deletes this entry.
- **Added:** 2026-10-06 · the EM-G3c-3 session

### H-254 · Count a task's subtasks with one gateway read, as the cascade counts them · [AGENT]
- **Metorite:** #498. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "_subtask_counts" apps/skills/skill-projects/skill_projects/writes.py`
  and read the function. A loop over `/projects/tasks/{tid}/relations` means
  this is open.
- **Added:** 2026-10-06 · WS-46 P6 review round 2

### H-253 · One member can still hold both parse slots of the gateway (EM-T11) · [AGENT]
- **Metorite:** #659. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "EM-T11 slot gap closed" project-docs/specs/email_app_master_plan.md`
  → no hit means open.
- **Added:** 2026-10-06 · the EM-T11 session

### H-252 · Ask a chat to summarise a PDF that came in a mail (EM-T11) · [OWNER]
- **Metorite:** #658. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "EM-T11 live check passed" project-docs/specs/email_app_master_plan.md`
  → no hit means open. The owner reports the result. An agent then writes that
  line, with the date, under §10.4.12. Then it deletes this entry.
- **Added:** 2026-10-06 · the EM-T11 session

### H-251 · Set up the Metorite WhatsApp bot number at Meta · [OWNER]
- **Metorite:** #741. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, `grep -c '^WHATSAPP_ASSISTANT_PHONE_NUMBER_ID=.' /opt/acb/app/.env`.
  An output of 0 means this is open. Do not print the line, because the
  file holds secrets.
- **Added:** 2026-10-06 · the WS-47 spec session.

### H-250 · Bring the last database connections inside the pool budget · [AGENT]
- **Metorite:** #699. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "psycopg.connect\(" packages/acb_common/acb_common/org_settings.py packages/acb_llm/acb_llm/model_config.py packages/acb_llm/acb_llm/key_store.py`.
  A hit means part 1 is open.
- **Added:** 2026-10-06 · the NS-11 session.

### H-245 · Close the four P3 edges of skill privacy that the PR #635 review left · [AGENT]
- **Metorite:** #618. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run these four checks. Each one stays open until its step closes.
  1. `rg -n "SKILL_UNCLAIMED" packages/acb_skills/acb_skills/tenant_file_store.py`.
     No hit means step 1 is open.
  2. `rg -n "acb_memory" packages/acb_skills/acb_skills/agent_paths.py`.
     No import of the store in `claim_skill` means step 2 is open.
  3. `rg -n "skill_owner" packages/acb_skills/acb_skills/note_tools.py`.
     No hit means step 3 is open.
  4. `rg -n "_odd_name_form" apps/services/gateway/gateway/routes/workspace.py`.
     Step 4 is open while no hit lies inside `get_workspace_tree`.
- **Added:** 2026-10-05 · the approval review of PR #635 (four P3s).

### H-244 · Decide whether an agent may set an env var through a SETUP token · [OWNER]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "<<<SETUP" apps/services/orchestrator/orchestrator/executor.py workbench/control_plane/src/app/api/agent/chat/route.ts`.
  A hit means the model-output path to configure still exists, and the
  decision is open.
- **Added:** 2026-10-05 · branch `integrations-env-hardening`. The box results
  are from 2026-10-05.

### H-243 · Answer WS-44's open questions before a shell flag goes on · [OWNER]
- **Metorite:** #766. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** read `project-docs/specs/navigation_shell.md` §13.3. A row that
  still shows only a default means that question is open.
- **Added:** 2026-10-05 · the session that wrote `navigation_shell.md`.

### H-228 · Give `get_errors` and `run_diagnostics` a containment check · [AGENT]
- **Metorite:** #768. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "resolve_in_workspace|relative_to" packages/acb_skills/acb_skills/error_tools.py`.
  No hit means the tool still has no containment check, and this is open.

### H-237 · Close what H-227 left: the member purge, the shared `agent-data/`, and a live check · [AGENT]
- **Metorite:** #617. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "purge_thread_files" apps/services/gateway/gateway/routes/admin/members.py`.
  No hit means step 1 is open. Steps 2 and 3 stay open until a decision
  and a live check are on record.
- **Added:** 2026-10-04 · the H-227 PR. Fix round 1 of PR #616 closed its
  first scope, `save_note` and `recall_notes`. These three items replaced it.

### H-239 · Count the loose files with no history row, and decide on an admin view · [AGENT]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** count the loose files of the tenant dirs on production that
  have no row in `agent_file_history`. A loose file lies in `inputs/` or
  `outputs/`, in no thread folder. Read `agent_blob` for the store side, and
  walk `state/*/o_*` for the disk side. Until a count and a decision are on
  record, this is open.
- **Added:** 2026-10-04 · fix round 1 of PR #616. The batch decision gave the
  document of an assigned task's run a home (rule 8). So this id now holds
  the count.

### H-242 · Close the three edges of the H-227 new-chat purge · [AGENT]
- **Metorite:** #689. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run these three searches.
  1. `rg -n "room is None or not thread_id" apps/services/gateway/gateway/routes/agent.py`.
     A hit means step 1 is open.
  2. `rg -n "_last_write" apps/services/gateway/gateway/routes/workspace.py`.
     A hit means step 2 is open.
  3. `rg -n "pg_advisory_xact_lock" apps/services/gateway/gateway/routes/chat.py`.
     No hit means step 3 is open, unless the run doors insert the row
     before the run (the second form of Do 3).
- **Added:** 2026-10-05 · the round 3 review of PR #616 (approved with nits).

### H-214 · Add a plan-guard rule `ws43-sandbox-flip` for the three WS-43 flags · [OWNER]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "ws43-sandbox-flip|MAF_CODING_SCOPE|SANDBOX_EGRESS_ENABLED|MAF_NATIVE_SESSIONS" .claude/hooks/plan-guard.mjs`.
  No hit, or a hit with no `MAF_NATIVE_SESSIONS`, means this is open.
- **Added:** 2026-10-03 · PR #584 fix round 1

### H-211 · Remove `approve_all` from the root `metorite` agent · [AGENT]
- **Metorite:** #560. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "approve_all" agents.py` at the repo root. A hit on the
  `on_permission_request` line means this is open.
- **Added:** 2026-10-03 · the WS-43 spec session

### H-213 · The WS-3 board row says WS-3a and WS-3b shipped, and the code has neither · [AGENT]
- **Metorite:** #578. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "IsolationTierUnavailable|ISOLATION_TIER_ENFORCE" apps packages`.
  No hit means WS-3a is still unbuilt. Then run
  `rg -n -e '--read-only' -e '"--network"' apps/services/orchestrator/orchestrator/copilot_sandbox.py apps/services/orchestrator/orchestrator/mutation.py`.
  No hit means WS-3b is still unbuilt.
- **Added:** 2026-10-03 · the WS-43 spec session

### H-198 · 🟡 DEFERRED — agent webhooks need an owner before they can use AI · [OWNER+AGENT]
- **Metorite:** #777. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "^AGENT_WEBHOOK_SECRET" /opt/acb/app/.env` on the box. No
  hit means the door is still closed, and this stays deferred.
- **Added:** 2026-09-28 · the deployment-key switch session

### H-208 · Keep a copy of `ACB_MASTER_KEY` off the box · [OWNER]
- **Metorite:** #614. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** ask the owner whether the password manager holds the current
  `ACB_MASTER_KEY` of `/opt/acb/app/.env`. If it does not, this is open.
- **Added:** 2026-10-02 · the Outlook onboarding session

### H-207 · 🔴 Rotate the Microsoft app secret that reached a chat transcript · [OWNER]
- **Metorite:** #613. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** in Entra, open the app `3bfeff54-14fb-4cee-8b17-2c8d41cad6a8` →
  Certificates & secrets. If a secret whose value ends `…scaAH` still exists,
  this is open.
- **Added:** 2026-10-01 · the Outlook onboarding session

### H-241 · Register the Metorite Google mail app, so Gmail can connect · [OWNER]
- **Metorite:** #601. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "^\*\*Step 9 done \(20[0-9][0-9]-" project-docs/specs/email_app_master_plan.md`
  → no hit means open. Only §12.4 step 9 writes that line, after an agent puts
  the client of the owner on the box and both values measure non-empty.
- ⚠️ **The Check changed on 2026-10-12.** The old Check keyed on the answer to
  Q-GM-6. PR-D0 answered Q-GM-6 on 2026-10-12: the box holds no client. So the
  old Check would delete this OWNER entry while the app still does not exist.
- ⚠️ **Do not count the key lines on the box.** The box holds both
  `GMAIL_OAUTH_*` names with EMPTY values (measured by length, 2026-10-12,
  §12.1). A count of the names gives 1 with no client. Count by length only,
  with the command of §12.4 step 9, and never print a value.
- **Added:** 2026-10-04 · the EM-G0 spec session

### H-249 · The tenant session commits an aborted transaction with no error · [AGENT]
- **Metorite:** #561. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "aborted" packages/acb_common/acb_common/db.py` → no hit
  in `tenant_session` means this entry is still open.
- **Added:** 2026-10-06 · the EM-T4a-2 PR-B1 session

### H-248 · Check that a reopened Outlook draft keeps its recipients (EM-T10) · [OWNER]
- **Metorite:** #658. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "EM-T10 live check passed" project-docs/specs/email_app_master_plan.md`
  → no hit means open. The owner reports the result. An agent then writes that
  line, with the date, under §10.4.11. Then it deletes this entry.
- **Added:** 2026-10-05 · the EM-T10 session

### H-247 · Send three test mails from Outlook, to prove the fix of a lost file (EM-T9) · [OWNER]
- **Metorite:** #658. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "EM-T9 live check passed" project-docs/specs/email_app_master_plan.md`
  → no hit means open. The owner reports the result of the three mails. An agent
  then writes that line, with the date, under §10.4.10. Then it deletes this entry.
- **Added:** 2026-10-05 · the EM-T9 session

### H-180 · Carry reasoning on the STREAM path too · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "publish_reasoning_alias" apps/services/customer_console`
  → no hit in the stream relay means this entry is still open.

### H-191 · Prove the 2026-09-28 dependency upgrade on the box · [AGENT]
- **Metorite:** #715. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, read `usage_event` for a Router chat, a transcription
  and a picture made after the deploy. Then export one PDF and open one call
  audio socket. → A row with no tokens, or a failed PDF or socket, means this
  is still open.
- **Added:** 2026-09-28 · the dependency upgrade

### H-192 · The WhatsApp call-audio socket fails on every connection · [AGENT]
- **Metorite:** #776. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** open a websocket to `/whatsapp/calls/audio` on a dev gateway.
  A `TypeError` naming `require_authenticated.<locals>._check()` and a
  missing `request` means this is open.
- **Added:** 2026-09-28 · the review of PR #500

### H-190 · A streamed OpenRouter call records our computed cost, not the vendor's · [AGENT]
- **Metorite:** #531. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "usage.cost|\"cost\"" apps/services/customer_console/customer_console/router.py`
  → no reader of `usage.cost` means this is still open.
- **Added:** 2026-09-28 · the dependency upgrade

### H-181 · Prove the upgraded Copilot path on the live Router · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run one `task-manager` chat on the box. Then read its
  `usage_event` rows. → A row with an empty member, app or run means this is
  still open.

### H-177 · Nobody can prove an organization owns the domain it claims · [AGENT]
- **Metorite:** #684. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "domain" infra/postgres/*.sql | rg -i "verif|token|txt"`
  → no hit means verification is unbuilt, and this is open. Also
  `SELECT slug, domain FROM organization WHERE domain IS NOT NULL;` on the
  TENANT database: every row there routes sign-in knocks on trust alone.
- **Added:** 2026-09-24 · the H-118 session

### H-176 · An unrouted sign-in reaches nobody, and only journald remembers · [AGENT]
- **Metorite:** #711. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "access_request_no_matching_org" packages/acb_auth/` → the
  log line exists. Then look for a READER: `rg -n "unrouted|no_matching_org"
  apps/services/customer_console/ workbench/operator_console/` → no hit means
  nothing but journald holds it, and this is open.
- **Added:** 2026-09-24 · the H-118 session

### H-175 · 🟡 OPTIONAL — a tenant org's DOMAIN is opt-in, and nothing is blocked without it · [OWNER]
- **Metorite:** #768. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** ask the TENANT database, and not the Console:
  `SELECT slug, domain FROM organization ORDER BY created_at;`
  A NULL `domain` is a legitimate resting state, so this entry never becomes
  "overdue". Read it when somebody asks why a colleague's knock did not appear.
- **Added:** 2026-09-24 · the H-118 session, re-scoped the same day

### H-172 · The shared scratch DB cannot replay the migration ladder any more · [AGENT]
- **Metorite:** #724. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the scratch DB, `SELECT max(attnum) FROM pg_attribute WHERE
  attrelid = 'email_assistant_settings'::regclass` → 1600 means this is open.
- **Added:** 2026-09-23 · the S7a build

### H-170 · A Console migration runs with NO backup of the Console database · [AGENT]
- **Metorite:** #507. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, run
  `for d in $(ls -1t /opt/acb/backups | head -14); do ls /opt/acb/backups/$d; done | grep -ci console`.
  A zero means no backup on the box holds the Console database, and this is
  open.
- **Added:** 2026-09-23 · the CP-13a deploy check

### H-165 · CP-13 is built through CP-13d. Stop the gateway taking the member from the body, so `decide` serves a shared box · [AGENT]
- **Metorite:** #559. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n 'member=None' packages/acb_skills/acb_skills/decide_tools.py`
  → a hit means the `decide` tool still sends no member, and this is open.
  CP-13a to CP-13d are BUILT (2026-09-23 and 2026-09-24).
- **Added:** 2026-09-23 · the Jev planning session · **rewritten 2026-09-24**
  when CP-13d was built

### H-163 · My Tasks: a signed-in member checks capture into Projects · [OWNER]
- **Metorite:** #549. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** a signed-in member opens My Tasks on app.metorite.com. The
  member picks a project with the capture chip, or types `#<project>`, and
  captures a task. The task shows on that project's board in Projects without
  a page reload. Delete this entry when the member sees it.
- **Added:** 2026-09-23 · the My Tasks planning session. **Re-cut**
  2026-09-24 when S6g merged. Only the signed-in check is left.

### H-104 · The generated tenancy files are NOT on the migration ladder · [AGENT]
- **Metorite:** #608. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `ls infra/postgres/generated/*.sql`, and read the glob in
  `scripts/apply_migrations.sh` (it matches numbered files in `infra/postgres`
  only). Files in that subdirectory, with a glob that does not reach them, means
  this is open.
- **Added:** 2026-09-06, from the WS-27 status-sets deploy · **narrowed
  2026-09-15** when the provisioning half was fixed.

### H-99 · ⏳ `bypassPermissions` has NO EXPIRY, and everything around it does · [OWNER]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep defaultMode .claude/settings.local.json` on the owner's
  machine. `bypassPermissions` means this is live. **Due 2026-09-30**, with the
  rest of the dev-phase window.
- **Added:** 2026-09-02 · guardrail-relaxation session, at close-out

### H-97 · 🔴 FOUR database passwords have reached agent transcripts · [OWNER]
- **Metorite:** #626. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** has somebody rotated all four credentials below since 2026-09-02?
  ⚠️ The original entry named the OLD Tokyo project, and the owner deleted it
  on 2026-09-02. That half is now moot. The three from the Mumbai bring-up are
  live and they are the ones that matter.
- **Added:** 2026-09-01 · guardrail-relaxation session, self-reported

### H-93 · 🔴 DEF-1's trigger has FIRED — a second operator `admin` exists · [OWNER]
- **Metorite:** #713. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run this on the Console database.

  ```sql
  SELECT email, role, status FROM operator
  WHERE role = 'admin' AND status = 'active';
  ```

  **Two or more rows means
  four-eyes approval is no longer deferrable**, and this entry is still real.
  One row, or none, would mean somebody deactivated an admin and the trigger
  un-fired.
- **Added:** 2026-09-01 · WS-31 D70 documentation session

### H-92 · A Projects test passes alone and fails in the suite · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `uv run pytest tests/unit/ -k "(project or tree or task or
  personal) and not memory and not calendar" -q` **three times**. A failure on
  `test_projects_hardening.py::test_an_intervening_activity_breaks_the_run` on
  ANY run means this is open. Then run that test on its own. It passes.
  ⚠️ Keep the `not memory and not calendar` half. Without it the run hangs and
  never reports (WS-9, and [[H-88]] carries the same trap).
  ⚠️ Do not use `-k "projects"` alone. It has stopped failing, and it now reads
  green while the defect is live.
- **Added:** 2026-09-01 · WS-27bk Wave 1 · minted as H-91 and renumbered to
  H-92 the same day. H-91 merged first, from the WS-31 fixture work, and
  `test_handoff_ids_are_unique` caught the collision.

### H-142 · The deploy goes red on a 30-second ssh blip, and the retry cannot outlast it · [AGENT]
- **Metorite:** #723. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** find the next deploy run that shows the warning "Deploy
  runner could not reach the box". Use `gh run list --workflow deploy`,
  then `gh run view <id> --log | grep -F "could not reach the box"`. If the
  confirm job in that run shows the served SHA and no false red, delete
  this entry. Also delete it if no such run occurs by 2026-11-30, because
  the tests in `tests/unit/test_deploy_reach.py` then carry the fence.

### H-89 · Something on the box writes into the checkout as root · [OWNER]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, run `sudo find /opt/acb/app -name .next -prune -o
  ! -user acb -print | head`. Any line means a root-owned path is back in the
  checkout, and the cause is still there.
- 🔴 **The Check PRUNED `node_modules` until 2026-09-20, and that is where
  the damage is.** Root-owned directories under
  `workbench/control_plane/node_modules` make `npm install` fail with EACCES,
  which kills `vps_apply.sh` before it syncs systemd units. The old Check
  stepped over that directory, so this entry read clear while every deploy
  was truncating. H-137 records the truncation.
- **Added:** 2026-08-31 · projects UI/UX session, on the PR #198 deploy ·
  rewritten the same day, after the block cleared and the repair landed

### H-87 · Give the vendor image price a SIZE dimension, before we offer sizes · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** the DELIVERABLE first, and the request body only after it.
  *(Rewritten 2026-08-31. The old Check read the body field alone. So anybody
  who restored `size` with no size dimension behind it flipped this entry to
  "done". That is the exact P1 below, and the Check deleted its own guard.)*
  1. **The price.** `rg -c 'size' apps/services/customer_console/customer_console/feed.py`
     reads **0** today, and `rg -n 'vendor_per_image' infra/customer_console/`
     shows ONE image column on `model_profile` and one on `vendor_price_feed`.
     Together that means the vendor image price still holds one number per
     model and carries NO size axis. This entry stays real while that holds,
     whatever the request body does.
  2. **The body.** `rg -n 'size' apps/services/customer_console/customer_console/main.py | rg 'ImageRequest|"size"|req\.size'`.
     A hit here while step 1 still reads one bare column is the P1 back. Reopen
     it, and never close this entry on it.
  🔴 **Close this entry only when step 1 shows a size dimension on
  `model_profile` and a feed that fills it.** The body field is the last step
  of the work, and never the measure of it.
- **Added:** 2026-08-31 · WS-31 H-46 review round 2 · **renumbered from H-84 on
  2026-08-31**. The router-guards slice took that id first.

### H-81 · Decide the TWO open resolution rules for the vision chain · [OWNER]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "def resolve_vision_chain" -A3 apps/services/customer_console/customer_console/router.py`
  and `grep -n "if not attempts:" -A7 apps/services/customer_console/customer_console/main.py`
  and `grep -n "_models_that_read_images" apps/services/customer_console/customer_console/router.py`.
  This entry is still real on three conditions. The resolver takes
  `(conn, tier)` alone, the empty-`attempts` branch answers 503 with no second
  resolve, and the declared-task resolve carries no `reads_images` filter.
  ⚠️ **The third grep keys on the SYMBOL, and never on a whole source line.**
  A literal match of the `return resolve_chain(conn, tier, VISION_TASK)` line
  read as done the moment somebody reformatted it, while shape 2 still stood.
  `_models_that_read_images` is the ONE reader of the flag, so any filter must
  call it. Today the grep answers three lines: the definition, one docstring
  reference, and ONE call inside the LIFT path. The declared-task resolve
  calls it NOWHERE. A second call means somebody filtered another chain, and
  shape 2 may be closed.
  ⚠️ Do NOT grep `provider_credential` on its own. `router.py:535`
  already reads that table for the SERVING path, so that grep hits today and
  reads as done.
- ⚠️ **This entry carries TWO shapes** *(the second one arrived 2026-08-31)*.
  Shape 1 costs a correct 200. Shape 2 lets a blind model answer. Close them
  together, because both add a resolution rule to the same function.
- **Added:** 2026-08-31 · WS-31 router-guards repair round · **shape 2 added
  2026-08-31**, WS-31 router-guards final repair round

### H-80 · Decide the thread budget for the stream walk · [OWNER]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "CapacityLimiter\|Semaphore\|total_tokens" apps/services/customer_console/customer_console/main.py`.
  No hit means nobody has capped the stream walk, and this entry is still real.
  ⚠️ **The grep reads `main.py` alone** *(named 2026-08-31)*. A cap that lands
  in a NEW module still reads as no hit, so this entry would say STILL PENDING
  after the repair. Widen the path to the package when you close it.
- **Added:** 2026-08-31 · WS-31 slice 11 review round 2 · **rewritten
  2026-08-31**. The WS-31 router-guards slice withdrew the fix shape and moved
  the label to OWNER.

### H-55 · Decide whether `pr-check.yml` runs the STE gate, and blocks · [OWNER]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n ste-lint .github/workflows/pr-check.yml`. A hit means the
  owner decided and this entry is dead.
- **Added:** 2026-08-26 · STE harness session

### H-20 · WS-34 LS-11: decide the fate of seats held on plans D49 retired · [OWNER]
- **Metorite:** #534. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `SELECT o.slug, sa.plan_slug, count(*) FROM seat_assignment sa
  JOIN organization o ON o.id = sa.organization_id
  WHERE sa.released_at IS NULL AND sa.plan_slug <> 'core' GROUP BY 1, 2;` on the
  Console database. Any row means a customer holds a seat on a retired plan and the
  decision is still owed. Zero rows → delete this entry.
- **Added:** 2026-08-24 · WS-34 build session

### H-3 · Rotate the production SSH credentials pasted into a session · [OWNER]
- **Metorite:** #622. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** can the old password still authenticate? If nobody has rotated it,
  it can. Treat as pending until rotation is confirmed.
- **Added:** 2026-08-14 · carried from the session that refused them

### H-7 · `now()` can move backwards, and migration 168's keyset cursor assumes it cannot · [AGENT]
- **Metorite:** #698. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "updated_at, id" infra/postgres/168*.sql` → the delta feed's
  cursor still ordering on `(updated_at, id)` with no monotonic guarantee means
  still pending. Needs its own board row and a decision before anyone builds.
- **Added:** 2026-08-14 · PR #439

### H-121 · 🟡 DOCUMENTED, DEFERRED — a READ grant can destroy a space · [OWNER]
- **Metorite:** #575. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -rn "require_permission" apps/services/gateway/gateway/routes/projects/`
  → no output means no Projects route gates a write, and this is still open.
- **Added:** 2026-09-19 · found by the adversarial review of the H-8 diff.

### H-128 · A fence that counts `text(` sites cannot see a statement change · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "pm_tags" tests/unit/test_projects_move_sql_asyncpg.py`
  → it exercises `WHERE project_id = CAST(:root AS uuid)`. Now
  `grep -n "pm_tags" apps/services/gateway/gateway/routes/projects/move.py`
  → the code runs `WHERE {vocabulary_scope()}`, a two-arm predicate with a
  correlated subquery. Two different statements.
- **Added:** 2026-09-20 · found by the round-2 verifier on PR #301.

### H-129 · A hand-supplied field map can still pick its winner by JSONB order · [AGENT]
- **Metorite:** #496. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "claimed" apps/services/gateway/gateway/routes/projects/move.py`
  → `resolve_field_map` keeps a `claimed` dict. The caller-supplied branch in
  `_plan` checks `compatible()` for each entry and never checks `claimed`.
- **Added:** 2026-09-20 · found by the round-2 verifier on PR #301.

### H-127 · The move has no end-to-end test, and that is where its P0s live · [AGENT]
- **Metorite:** #485. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -rn "move_tasks" tests/unit/test_projects_move_routes.py`
  → only the refusals. No test drives a cross-status-set move to completion.
- **Added:** 2026-09-19 · filed by the session that shipped the defects.
  ⚠️ **Minted as H-122 and renumbered to H-127 on merge.** PR #302 merged
  first and took 122 for the Operator Console rig. This is the collision
  `test_handoff_queue.py` exists to catch, and it caught it.

### H-120 · "Move to…" opens NOTHING on a phone · [AGENT]
- **Metorite:** #548. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "if (isMobile) {" workbench/control_plane/src/app/projects/page.tsx`
  → note the line. Then find `movingNode ? (`. It sits **after** that early
  return, so the phone branch never renders it.
- **Added:** 2026-09-19 · found while building the Delete affordance (H-8).

### H-8 · Still owed on WS-27bg slice 2, and WS-27bg slice 3 / WS-27bh unbuilt · [AGENT]
- **Metorite:** #502. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** the WS-27 row in `work_plan.md` §2 — it names what is built. Read
  it rather than this entry; this entry only says *look there*.
- **Added:** 2026-08-14 · session that built WS-27bj. *(Rewritten 2026-09-19:
  rename and delete are done, and the bulk close turned out to be blocked.)*

### H-10 · HALF the R1 blind window is still open — the cross-branch collision · [AGENT]
- **Metorite:** #639. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "merge_group|merge-base origin/main" .github/workflows/pr-check.yml`
  → only the secret-scan's `merge-base` hit (no `merge_group:` trigger, and no job
  that checks out the head ref and merges the base before running the fence) means
  the remaining half is still open.
- **Added:** 2026-08-14 · session that built WS-27bj · **halved 2026-08-25** by the
  push trigger (PR #46); re-pointed at T-6

### H-14 · Create the Razorpay TEST-mode account; set the three payment env vars · [OWNER]
- **Metorite:** #513. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** ask the owner whether a Razorpay test account exists; on the box or
  in CI, `env | grep -c CUSTOMER_CONSOLE_RAZORPAY` → `3` means done. (No repo
  command can see this — it is an external account plus deployment env.)
- **Added:** 2026-08-19 · CP-9 substrate session

### H-12 · Decide fate of FracktalWorks satellite-repo references · [OWNER]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "FracktalWorks/" apps/services/gateway/agents.json README.md`
  → hits mean still pending.
- **Added:** 2026-08-16 · rebrand session (branch `rebrand/metorite`)

### H-16 · Rotate the go-live secret set; put GITHUB_TOKEN on the box · [OWNER]
- **Metorite:** #611. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** ask the owner — no repo command can see this. The set: both
  Supabase database passwords (Console + tenant planes) · the Google OAuth
  client secret · the DeepSeek API key. All four transited agent chat
  2026-08-18/19. Plus: `grep -c GITHUB_TOKEN /opt/acb/app/.env` on the box →
  `0` means still pending.
- **Added:** 2026-08-19 · VPS bring-up session

### H-32 · Revoke the ClickUp tokens at ClickUp · [OWNER]
- **Metorite:** #693. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, **both** credential homes plus the vendor:
  `SELECT count(*) FROM provider_keys WHERE credential_type = 'integration' AND service = 'clickup';`
  **and** `SELECT count(*) FROM task_accounts WHERE provider = 'clickup';` —
  that second table's `credentials_encrypted` is where the real per-workspace
  ClickUp tokens live (`48_task_manager_gtd.sql:24-44`), so a check that asks
  only `provider_keys` can read `0` while live tokens remain. Then whether the
  token still authenticates against `https://api.clickup.com/api/v2/user`. Any
  one alive → still pending.
  ⚠️ There is no `integration_credentials` table — migration
  `11_integration_credentials.sql` **adds columns to `provider_keys`**
  (`credential_type`, `service`). An earlier draft of this entry queried the
  non-existent table and would have errored rather than answered.
  ⚠️ A repo-side grep cannot answer this and never could: WS-39 S1 deleted every
  *reader* of the token, which is not the same as the token being dead. A
  credential nobody reads is still a live credential at the vendor.
- **Added:** 2026-08-24 · WS-39 S1 session *(renumbered H-27→H-32 on 2026-08-25:
  `main` took H-27 for the e2e entry via PR #47; ids are never reused)*

### H-164 · The deploy restarts the workbench while npm is still installing · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, grep the workbench journal for `next: not found`.
  Any hit means this is still real:

      journalctl -u acb-workbench --since "7 days ago" | grep -c "next: not found"
- 🟢 **2026-09-26 — the deploy-serialize PR closes the race. Keep this entry
  until seven days of journal read zero.**
  - **The cause was the OTHER deploy path.** Root's pull ran `npm ci` while
    the CI path restarted the unit, and the reverse. Run 36166015861 is the
    build half of the same race: `Cannot find module 'next/server.js'`.
  - **What it changes.** Both paths take one lock for the whole apply, so no
    install runs during another path's build or restart. Inside one apply
    the order is install, build, swap, restart. `require_next_bin` checks
    `node_modules/.bin/next` before the workbench and Console restarts, and
    stops the apply when it is missing. The running process keeps serving.
  - ⚠️ **One gap remains.** `acb-workbench.service` has
    `Requires=acb-gateway.service`, so the gateway restart also restarts the
    workbench, BEFORE the install. The apply now prints a warning there when
    `next` is missing, and it does not stop.
    🟢 **2026-09-28 — the H-60 no-502 PR closes this gap.** The unit now has
    `Wants=`, and the apply installs it before the gateway restart. So the
    workbench restarts once, after its install and build.
- 🔴 **MEASURED 2026-09-23, and it turned a deploy RED across all three
  rounds.** PR #406's run 35855523275 ended `WORKBENCH FAILED TO START`. The
  box was healthy the whole time and the next deploy started it fine.
- **Added:** 2026-09-23 · the H-152 gateway slice.

### H-195 · Two Caddy follow-ups that only the owner can do · [OWNER]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** (1) GitHub branch protection on `main` lists
  "Caddy config (validate)" as a required check. (2) The owner decides the
  identity-header strip, and the live `/etc/caddy/Caddyfile` matches that
  decision. Delete this entry when both are done.
- **Added:** 2026-09-28 · the H-60 close-out

### H-31 · Re-home the `event=` structlog AST guard · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "ast\." tests/unit/test_ingestion_receiver_parity.py` → no AST walk
  asserting that no receiver passes a bare `event=` to a structlog logger means it is
  still advisory. ⚠️ **Corrected 2026-08-26: the old Check was**
  `rg -n "clickup_event|zoho_event" tests/` **and it answers the wrong question.**
  Run today it returns a hit — `test_zoho_event_type_falls_back_to_unknown`, a test
  **named** after the convention, which asserts fallback behaviour and not the rule.
  A Check that matches a function name rather than the guard reads *done* while the
  guard is still missing, which is the one failure this file's protocol exists to
  make impossible.
- **Added:** 2026-08-24 · WS-39 S1 session

### H-52 · Phase 0 (D55.2) has ALREADY ENDED by trigger (b) — decide which way · [OWNER]
- **Metorite:** #720. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `gh api repos/Hathi-Labs/Metorite/collaborators --jq '.[] | select(.type=="User") | select(.permissions.push or .permissions.admin) | .login'`
  → more than one login means trigger (b) holds. Two today: `vjvarada`, `nithinjak`.
- **Added:** 2026-08-26 · guardrails/CI session

### H-49 · D63's seal and dialog are BUILT. The watched door is not · [AGENT]
- **Metorite:** #697. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "unseal_personal_tree" apps/services/gateway --include=*.py`.
  The entry is open while the only caller is the reactivation branch of
  `PATCH /admin/members/{email}` (`admin/members.py:362`). That branch gives
  the tree back to its owner. No owner-only, logged route lets somebody ELSE
  open a sealed tree yet.
- ✅ **SLICE 1 LANDED 2026-09-23.** Migration **215** adds
  `pm_projects.sealed_at`. Both visibility clauses filter it on both arms, so
  a sealed tree leaves the grant closure AND the `data:org:read` answer. Both
  off-boarding doors — `PATCH /admin/members/{email}` and the DELETE — stamp
  the tree inside the same transaction as the status write. `active` unseals.
  `tests/live/live_member_seal.sql`: 14 checks on real Postgres.
- ⚠️ **The hand-over needed NO code, and that is a finding.**
  `task_visibility_clause` has a second arm over `pm_task_assignees` that never
  consults the project. Sealing removes the grant arm and leaves that one, so a
  task the leaver assigned outward stays visible to its assignee. No row moves,
  and there is no half-finished state to recover from.
- **Added:** 2026-08-26 · WS-39 personal-tree session *(minted as H-35; renumbered to H-49 the same session — `test_handoff_ids_are_unique` caught the collision with the WS-36 restore-spec entry. Ids are never reused.)*

### H-27 · 33 browser tests are red, and CI gates only the half that is green · [AGENT]
- **Metorite:** #725. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "project=ci" .github/workflows/pr-check.yml` → no hit means
  CI has stopped running the browser suite. Then read `testIgnore` in
  `workbench/control_plane/playwright.config.ts` → a non-empty list means this
  entry is still open. Run `npx playwright test --project=chromium` for the
  true picture, which is always wider than what CI gates.
  ⚠️ **The old second check was wrong and is dropped.**
  `rg -n "127.0.0.1" playwright.config.ts` was supposed to catch the hydration
  trap coming back. It matches the `webServer.url` readiness probe, which is
  correct and harmless — `baseURL` is what must stay `localhost`, and it is.
  As written that check cried wolf on a healthy config.
- **Added:** 2026-08-21 · session that built WS-27bg slice 2's rename

### H-35 · Write the owning spec for WS-36 (per-tenant restore) · [AGENT]
- **Metorite:** #631. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "WS-36" project-docs/INDEX.md` → a hit in the **"BOARD ROWS WITH
  NO OWNING SPEC"** section (rather than in ACTIVE) means the spec is still unwritten
  and the row is still 🔴 not dispatchable.
- **Added:** 2026-08-26 · multi-tenancy product pass

### H-36 · Write the owning spec for WS-37 (trust & compliance) — and take the positions it needs · [OWNER]
- **Metorite:** #635. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "WS-37" project-docs/INDEX.md` → a hit in the **"BOARD ROWS WITH
  NO OWNING SPEC"** section means it is still unwritten.
- **Added:** 2026-08-26 · multi-tenancy product pass

### H-38 · Decide D-D (where staging runs, and what it costs) · [OWNER]
- **Metorite:** #853. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "D-D" project-docs/work_plan.md` → a hit only inside **D55.9's
  "still owed"** clause (rather than a recorded answer in §3) means it is still open.
- **Added:** 2026-08-26 · delivery + model-management decision session

### H-39 · Decide D-F (the CODEOWNERS map) — when there is a second person to name · [OWNER]
- **Metorite:** #719. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `ls .github/CODEOWNERS` → absent means still open. ⚠️ This entry is
  **deliberately not actionable yet** and should not be closed by writing a CODEOWNERS
  file naming one person.
- **Added:** 2026-08-26 · delivery + model-management decision session

### H-42 · Price the AI rate card, then flip the spend gate — in that order · [OWNER]
- **Metorite:** #524. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the Console database,
  `SELECT count(*) FROM tier_rate_card WHERE pricing_mode = 'priced';` → `0` means the
  card is still unpriced and nothing draws credits down. Then
  `SELECT count(*) FROM credit_price;` → `0` means the credit itself has no
  rupee price, so a bank transfer has no official conversion. Then
  `env | grep -c CUSTOMER_CONSOLE_SPEND_GATE` on the box.
  *(D67, 2026-08-30: the card billing reads is `tier_rate_card`, keyed on
  the tier. `model_rate_card` is read-only history. The console's /pricing
  page sets both numbers and shows the margin live. Migration `017` added
  `credit_price` for the rupee side.)*
- **Added:** 2026-08-26 · AI credits + keys session

### H-79 · Flip the `/me/billing` money fields to strings (two releases, R6) · [AGENT]
- **Metorite:** #543. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -c "float(balance)" apps/services/customer_console/customer_console/main.py`
  → `1` means the Console still sends floats.
- **Added:** 2026-08-30 · console-review session

### H-43 · Close the process-global credential injection (D58.2) · [AGENT]
- **Metorite:** #625. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "os.environ\[" packages/acb_llm/acb_llm/key_store.py` → hits inside
  `configure_litellm` mean the tenant path still writes process-global credentials.
- **Added:** 2026-08-26 · AI architecture session

### H-44 · Feature→tier binding is hardcoded at 80+ call sites · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -c '"tier-(fast|balanced|powerful|stt)"' --glob '*.py' --glob '*.ts' apps/ packages/ workbench/`
  → any file with a count means that feature's tier is still a literal, not a
  configuration. Measured 2026-08-26: `assistant.py` 21 · `settings.py` 8 · `_common.py` 8
  · `drafting.py` 7 · `tasks/ai.py` 6 · `notes/summaries.py` 5 · `taskStore.ts` 4 · plus
  five more files.
- **Added:** 2026-08-26 · AI architecture session

### H-47 · Widen `acb_stt`'s provider pattern instead of inventing a handler abstraction (G-2) · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "class SttProvider|resolve_stt_provider" packages/acb_stt/` → present
  and still STT-only means the generalisation has not happened.
  ⚠️ **Repaired 2026-08-30. This Check read two things as one.** A DATA READ of
  `model_capability.invocation` is **ALLOWED**. `resolve_invocation`
  (`customer_console/router.py:269`) is that read, and §6A.10a clause 6 gives it
  its first caller on the serving path. The defect this entry guards is a second
  handler-OBJECT seam — a second provider hierarchy beside `acb_stt`'s. Read the
  two apart before you call a hit a defect.
- **Added:** 2026-08-26 · AI design audit · **amended 2026-08-30** with a
  done-when section and a repaired Check

### H-54 · Configure the Supabase GOOGLE WORKSPACE staff provider, the SIX `OPERATOR_*` values, and turn identity linking OFF · [OWNER]
- **Metorite:** #708. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `ssh` to the box and read **TWO** env files, not one.
  1. The API file, `/opt/acb/app/apps/services/customer_console/.env`
     (`acb-customer-console.service`, `EnvironmentFile`). It must hold
     `OPERATOR_SIGNIN_PROVIDER=google` and `OPERATOR_GOOGLE_HD`.
  2. The operator console env, which the separate Next process reads. It must
     hold `OPERATOR_SIGNIN_PROVIDER=google` too.

  Anything other than `google` in either file means still pending. An unset
  value is the same as `azure`. An unset `OPERATOR_GOOGLE_HD` means still
  pending too. **Set all three lines, or this entry stays open.**
  ⚠️ **This Check read ONE file, and the wrong one, until 2026-09-01.** It
  asked for `OPERATOR_GOOGLE_HD` in the console env. The API reads that value,
  and the console never does. The table below is the placement of record.
  ⚠️ **`deploy/` names no unit file for the operator console**, so this entry
  cannot give you a path for file 2. `deploy/hostinger/` holds a service for
  the gateway, the Customer Console, the workbench and the WhatsApp bridge, and
  none for this app. Find where the running console reads its env before you
  start. `workbench/operator_console/AGENTS.md` still says "not deployed",
  and this entry says the box has been reachable since 2026-08-22. One of the
  two is stale, and only the box can settle it.
  ⚠️ From the repo alone you can no longer tell.
  `rg -n "OPERATOR_GOOGLE_HD" workbench/operator_console/ apps/services/customer_console/`
  returned nothing before CP-12h and returns hits now, so it says only that the
  CODE half is built. It says nothing about the box.
- **Added:** 2026-08-26 · operator-identity spec session · **rewritten
  2026-09-01** for D70 · **amended 2026-09-01** by the CP-12h repair round,
  which found the sixth value and the false Check.
  **Amended again the same day** by the second repair round. That round added
  the placement table, the two-container switch and
  `OPERATOR_CONSOLE_ORIGIN`.

### H-58 · Name the first operators and their roles · [OWNER]
- **Metorite:** #712. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "OPERATOR_BOOTSTRAP_EMAIL" deploy/ .env.example` → no hit
  means nobody has been named yet.
- **Added:** 2026-08-26 · operator-identity spec session · **renumbered from
  H-55 on 2026-08-26**, because the STE session minted its own H-55 against a
  different base and merged first. `test_handoff_ids_are_unique` caught it. Ids
  are never reused, so H-55 stays with the STE entry.

### H-56 · **CP-12g slice 2** — delete the passphrase, AFTER one real sign-in · [AGENT+OWNER]
- **Metorite:** #709. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "OPERATOR_CONSOLE_STAFF_SECRET" workbench/` → a hit means
  the deletion has not run. That is the CORRECT state until the owner has
  flipped the flag and signed in once.
- **Added:** 2026-08-26 · operator-identity spec session

### H-65 · plan-guard cannot see a write made by an interpreter reading a heredoc · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** read `.claude/hooks/plan-guard.mjs` near the `scanned` constant.
  A regex still strips every heredoc body before the protected-path scan, and
  `plan-guard.test.mjs` names no interpreter case. Both mean still open.
- **Added:** 2026-08-27 · WS-31 CP-12g session

### H-69 · Flip `ROUTER_SERVING_ENABLED` — after three prerequisites · [OWNER]
- **Metorite:** #510. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, `grep ROUTER_SERVING_ENABLED /opt/acb/app/.env`. No
  line, or `0`, means the hop is inert and this is still pending.
- **Added:** 2026-08-27 · WS-31 CP-11 slice 3 session

---


---


---

### H-73 · CP-7's per-member cap rests on an identity the member controls · [AGENT+OWNER]
- **Metorite:** #12. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "x-cc-member" apps/services/gateway/gateway/routes/v1_compat.py`
  → a `request.headers.get(...)` hit means the member identity is still taken
  from the inbound request, and this is open. A hit that reads it from a
  verified session means somebody fixed it, and CP-7's engine can proceed.
- **Added:** 2026-08-28 · WS-31 CP-7 slice 1. **Updated:** 2026-09-23.

### H-74 · mypy is strict over a tree nobody has swept — 1508 errors · [AGENT]
- **Metorite:** #270. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `uv run mypy apps packages --exclude '^apps/agents/' 2>&1 | tail -1`
  → a count above zero means the sweep has not happened and this is open.
- **Added:** 2026-08-28 · H-28 fix session

### H-76 · `usage_by_org` sorts by spend, so the quiet funded customer falls off the cap · [AGENT]
- **Metorite:** #533. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** read the docstring of `usage_by_org` in
  `apps/services/customer_console/customer_console/store.py` and the `ORDER BY`
  under it. An order that still sorts on credits alone means this is open.
- **Added:** 2026-08-30 · WS-31 spec remediation session

### H-77 · Set the vendor feed's clock on the box · [OWNER]
- **Metorite:** #541. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, `grep CUSTOMER_CONSOLE_FEED_SYNC_HOURS /opt/acb/app/.env`.
  No line, or `0`, means the feed only updates when somebody presses the
  button, and this is still open.
- **Added:** 2026-08-30 · vendor-feed session

### H-88 · A field-change coalescing test flakes on a UUID tiebreak · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `uv run pytest tests/unit/ -k "(project or tree or task or
  personal) and not memory and not calendar" -q` three times. If
  `test_projects_hardening.py::test_an_intervening_activity_breaks_the_run`
  fails on some runs and passes on others, this is still open. Measured
  2026-08-31: it failed on 2 runs out of 5. Measured again 2026-09-22 with the
  corrected command: it failed on run 1, passed run 2, and run 3 failed a
  DIFFERENT test — see [[H-92]], which now carries the sharper evidence.
  ⚠️ **`-k "projects"` alone no longer reproduces it** — six green runs while
  the defect is live. Do not narrow this selection.
- **Added:** 2026-08-31 · projects UI/UX session · **minted as H-80, renumbered
  to H-88 on 2026-08-31** — `main` had meanwhile taken H-80 for "Decide the
  thread budget for the stream walk" and run on to H-87. Two branches minted
  the same next-free id against different bases, which is R1 one level up. This
  entry merged second, so this entry moved.

### H-111 · Arm the weekly report send. The audience is DECIDED · [OWNER]
- **Metorite:** #482. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** on the box, `grep -c '^PROJECT_REPORT_EMAIL_ENABLED=true' /opt/acb/app/.env`.
  A zero means this is open.
  ⚠️ **The second half of this Check was wrong within one day of being
  written.** It read `grep -c recipients infra/postgres/204_projects_reports.sql`
  — and the recipients table landed in **205**, so it would have reported the
  audience unbuilt forever. Corrected 2026-09-17, and it is the same drift
  H-105 carried for eleven days. A Check that names a file is a Check that
  rots when the work moves.

### H-112 · `test_tenant_coverage`'s live checks are invisible to CI · [AGENT]
- **Metorite:** #609. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `uv run pytest tests/unit/test_tenant_coverage.py -q` with
  `DATABASE_URL` unset. Two tests SKIP. Their own skip message says why that
  matters: *"a green run without it proves the SQL was written, not that it
  works."*

### H-114 · R8 suites still run psycopg. The gateway runs asyncpg · [AGENT]
- **Metorite:** #717. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `uv run pytest tests/unit/test_projects_sql_asyncpg.py
  tests/unit/test_auth_sql_asyncpg.py -q` with `TENANT_LADDER_DATABASE_URL`
  set. If either SKIPS in CI, the strong half of the fence does not fire
  there — which is H-112's gap, on two files now.

### H-110 · Operator OTP sends now. Two dashboard acts are still unverified · [OWNER]
- **Metorite:** #710. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** ask Supabase project `uttxlicdccfkramtjfpi` for an OTP at an address
  that is NOT a project member. `POST /auth/v1/otp {"email":"…","create_user":true}`.
  A **200** means the send half is fixed. Then ask an operator whether the email
  carried a six-digit code, and whether the link signed them in.
- **Added:** 2026-09-16 · signup-flow session, after the owner asked why operator
  OTP cannot reuse the customer app's mechanism. **Reduced the same day**, once
  the owner fixed the send half and a probe proved it.

### H-108 · 🔴 The Console's database auto-pauses, which is a total onboarding outage · [OWNER]
- **Metorite:** #553. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** ask Supabase for project `uttxlicdccfkramtjfpi`. Anything other than
  `ACTIVE_HEALTHY`, or a free tier that pauses on inactivity, means this is open.
- **Added:** 2026-09-15 · signup-flow session, found by surveying the box

### H-109 · One word, "tenant", names both planes and points at each · [OWNER]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** ask whether `saas_multitenancy.md` still calls
  `wbjpwtxigkileyjsgahk` the *tenant plane* while the Supabase project named
  *"Metorite Tenant Database"* is `uttxlicdccfkramtjfpi`. Both true means this
  is open.
- **Added:** 2026-09-15 · signup-flow session · **corrected 2026-09-16** when
  the owner challenged the "inverted" claim and was right.

### H-116 · plan-guard reads a MENTION of a commit as a commit on main · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "test(cmd)" .claude/hooks/plan-guard.mjs`. A hit means
  this is open. No hit means somebody applied the repair.
- **Added:** 2026-09-18 · operator console workspace session

### H-137 · 🔴 The deploy reports SUCCESS while `vps_apply.sh` dies half way · [AGENT]
- **Metorite:** #630. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** open the newest green `deploy.yml` run, job *Deploy to Hostinger*,
  and search the log for `ssh exited non-zero`. A hit inside a run marked
  success means this is open.
- 🔴 **Measured on 2026-09-20, run 35513962455, which is marked SUCCESS.**
  `vps_apply.sh` reached `==> Rebuilding + restarting workbench (Next.js)` and
  died there with `npm error code EACCES` on
  `workbench/control_plane/node_modules/react-pdf/node_modules/@napi-rs/canvas-android-arm64`.
  The workflow printed `(ssh exited non-zero - verifying by health regardless)`,
  polled the gateway, found it healthy, and reported a verified deploy.
- 🔴 **Everything after that step silently does not run.** In file order the
  casualties are `==> Installing health watchdog (systemd timer)` at line 865
  and `==> Syncing systemd units (BO-23)` at line 885. So a systemd unit added
  to the repo never reaches the box, and no timer is enabled — which is the
  loop BO-23 exists to be.
- ⚠️ **This is how H-132 was found, and it is the larger half.** Removing the
  backup carve-out changed nothing on the box. The loop that would arm the
  timer sits past the point where the script dies. The timer was armed by hand
  at 13:47 UTC and now prints NEXT `Mon 2026-09-21 02:32:46 UTC`.
- 🔴 **H-89's Check cannot see this, and that is why both look clear.** It
  prunes `node_modules`, and `node_modules` is exactly where the root-owned
  paths that break the install live. Corrected below.
- ⚠️ **The health probe is not wrong. It answers a different question.**
  A healthy gateway says the services that were ALREADY running still run. It
  cannot say whether the steps after the failure ran. Root `CLAUDE.md` §3 rule
  8 names this. Verify by evidence, never by a green job.
- **Added:** 2026-09-20 · credit and usage review session

### H-136 · Every AI-spend signal is PULL-ONLY — nobody is told anything · [AGENT+OWNER]
- **Metorite:** #514. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** grep the Customer Console for a scheduled report or digest. None,
  and no timer on the box, means this is open.
- 🔴 **The board computes the alarms and then waits to be visited.** `/usage`
  already knows which customer is past zero. It knows which margin has
  inverted, and which organization is funded and silent. It counts the calls
  served and never billed. Each is a fact somebody should be TOLD. Today each
  one waits for an operator to open a page.
- ⚠️ **The spend gate ships OFF, which makes this sharper, not softer.** With
  no wall at zero a balance keeps falling, so the cost of nobody looking grows
  with time instead of stopping at zero. The backup timer closed on
  2026-09-20 was the same shape of failure. It ran only when a human
  remembered, and for 25 days nobody did.
- **Added:** 2026-09-20 · credit and usage review session

### H-123 · Backups exist now, and live only on the box they protect · [OWNER]
- **Metorite:** #545. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `ssh metorite "sudo journalctl -u acb-backup --since -26h --no-pager | grep -c 'off-box copy ok'"`.
  A zero means no night left the box in the last day, and this is open.
- ✅ **The code is BUILT (2026-10-08, branch `ops-offbox-backup`).** The
  owner chose a private Supabase Storage bucket, through its S3 endpoint.
  Each night, `acb-backup.service` runs `backup_db.sh --offbox`. It encrypts
  the dumps, the manifest, the file data and the meeting-bot volume to your
  PUBLIC key. Then rclone sends them. No other caller uploads. The copy stays
  OFF until you do the four steps below.
- **Added:** 2026-09-19 · operator console session, after the backup repair.
  Rewritten 2026-10-08, when the off-box copy was built.

### H-141 · An admin cannot hide an app from a ROLE · [AGENT]
- **Metorite:** #768. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "is a system role and cannot be edited" apps/services/gateway/gateway/routes/admin/roles.py`
  → a hit means `PATCH /admin/roles/{slug}` still refuses every built-in
  role, and this is open.
- **Added:** 2026-09-21 · the every-app-by-default session

### H-152 · A SELF-SERVE customer can never be served AI · [AGENT]
- **Metorite:** #515. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY" /opt/acb/app/.env`
  on the box. No hit means the shared-box arm is built and not turned on.
- 🔴 **Why this exists.** `CUSTOMER_CONSOLE_ORG_KEY` is ONE value naming ONE
  tenant. A box serving several had one slot and N tenants. It served the
  first and was dark for the rest, and it never failed while doing so.
  Minting more organization keys does not help, because the second has
  nowhere to live.
- **Added:** 2026-09-22 · the auto-mint session. **Updated:** 2026-09-28.

### H-126 · `build_sha()` returns None in EVERY git worktree · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** from a worktree, `uv run pytest tests/unit/test_build_info.py -q`
  → a failure that names `build_sha() disagrees with git rev-parse HEAD` means
  this is open. The same command passes in the main checkout.
- **Added:** 2026-09-20 · the My Profile session

### H-139 · 🔴 A cleared `organization` row silently destroys the Projects data · [AGENT]
- **Metorite:** #610. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** read the delete rule on the organization key.

      select confdeltype from pg_constraint
       where conrelid = 'pm_projects'::regclass;

  → `c` is the cascade. This entry is then open.
- **Added:** 2026-09-20 · the task lifecycle session

### H-161 · The seats matrix cannot PROPOSE, because the queue is the wrong shape · [OWNER]
- **Metorite:** #773. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "CREATE TABLE IF NOT EXISTS access_request" -A 12
  infra/postgres/143_access_request.sql` → a unique index on `lower(email)`
  and no column naming what is asked means this is still open.
- **Added:** 2026-09-22 · the seats-and-roles session

### H-162 · Two Projects chat follow-ups from the S2 review · [AGENT]
- **Metorite:** #487. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n 'if "@" in raw' apps/skills/skill-projects/skill_projects/writes.py`
  → a hit means the first item is still open.
- **Added:** 2026-09-22 · the Projects chat S2 session. Minted as H-161, renumbered to H-162 because main took H-161 first

### H-184 · Four small follow-ups from the R2b review of the report visuals · [AGENT]
- **Metorite:** #497. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "current_week_partial\|so far" workbench/control_plane/src/lib/reportEmail.ts`
  → no hit means item 1 is still open. Check each other item by hand.
- **Added:** 2026-09-24 · the WS-27bn R2b session

### H-199 · A lead who left a team cannot remove their old report through the chat · [AGENT]
- **Metorite:** #497. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "refusal=" apps/skills/skill-projects/skill_projects/guarded.py apps/skills/skill-projects/skill_projects/writes.py`
  → no hit means the chat still reads the report through the read rule.
- **Added:** 2026-09-28 · the WS-27bn R5d session

### H-201 · Bind the tenant in the other readers that still open an unbound session · [AGENT]
- **Metorite:** #612. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -rn "with get_session() as" apps/services/gateway/gateway/routes/observability.py apps/services/gateway/gateway/routes/debug.py apps/services/gateway/gateway/routes/integrations_skills.py`.
  A hit means this is open.
- **Added:** 2026-09-29 · the WS-27bm S15 session. Part 1 built 2026-09-30.

### H-202 · Two small follow-ups from the Reports UX pass · [AGENT]
- **Metorite:** #497. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n '"Project"' workbench/control_plane/src/app/projects/components/ReportsView.tsx`
  → a hit in `scopeName` means item 2 is still open.
- **Added:** 2026-09-29 · the WS-27bn R5b-2 and UX pass session

### H-206 · A browser that blocks storage breaks the whole app · [AGENT]
- **Metorite:** #685. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "window.localStorage" workbench/control_plane/src/components/OrgBrandLockup.tsx`
  → an access with no try/catch around it means the entry is still open.
- **Added:** 2026-10-01 · the WS-27bn R5g session

### H-210 · Drop the old thread index `idx_email_messages_thread` in a migration of its own · [AGENT]
- **Metorite:** #774. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -rn "DROP INDEX IF EXISTS idx_email_messages_thread;" infra/postgres/`
  → no hit means the entry is still open.
- **Added:** 2026-10-03 · the EM-T4e review (branch `email-t4e`)

### H-215 · Keep tool results across turns on Tier 1, then move task-manager to MAF · [AGENT]
- **Metorite:** #765. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n -A 25 '"name": "task-manager"' apps/services/gateway/gateway/routes/agent.py | grep agent_runtime`
  → `github-copilot` means the entry is still open.
- **Added:** 2026-10-03 · the WS-8 task-manager and apis-config MAF move

### H-219 · A steer note shows as extra detail rows on a tool card · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -rn "arrived from a participant" workbench/control_plane/src/`
  → no hit means the cards still show the note as rows, and the entry is still open.
- **Added:** 2026-10-03 · the WS-8 task-manager and apis-config MAF move

### H-220 · With the Router off, Thinking and Max do nothing on the local /v1 path · [AGENT]
- **Metorite:** #530. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "reasoning_effort" apps/services/gateway/gateway/routes/v1_compat.py`
  → no hit means the local path still drops it, and the entry is still open.
- **Added:** 2026-10-03 · the WS-8 task-manager and apis-config MAF move

### H-221 · On DeepSeek, Max is the same as Thinking, and a plain model can refuse both · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -rn "drop_params" apps/services/customer_console/`
  → no hit means the Console still refuses the field for a model with no reasoning support.
- **Added:** 2026-10-03 · the WS-8 task-manager and apis-config MAF move

### H-222 · A native sub-agent takes the parent's steer note · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "decorate_tool_result(result)" apps/services/orchestrator/orchestrator/_tool_injection.py`
  → a hit means the entry is still open.
- **Added:** 2026-10-03 · the WS-8 task-manager and apis-config MAF move

### H-223 · The sandbox quota flag lives on one handle, and two handles can share one dir · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "handle.over_quota = used" apps/services/orchestrator/orchestrator/sandbox_broker.py`
  → a hit means the entry is still open.
- **Added:** 2026-10-03 · WS-43c, the sandbox broker

### H-224 · A cancelled `docker run` can leave a Created sandbox container · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n "_remove_container(handle.container_id, handle.start_id)" apps/services/orchestrator/orchestrator/sandbox_broker.py`
  → a hit, and no retry of the start-label removal, means the entry is still open.
- **Added:** 2026-10-03 · WS-43c, the sandbox broker

### H-225 · Take the shell tools from personal agents too, once the sandbox covers them · [AGENT]
- **Metorite:** #808. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n 'return instancing == "personal"' apps/services/orchestrator/orchestrator/_tool_injection.py`
  → a hit means a personal agent still keeps its host shell tools.
- **Added:** 2026-10-03 · the D85 interim block

### H-230 · Put the first-party check in front of `spawn_copilot_agent`'s mutation sandbox · [AGENT]
- **Metorite:** #615. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "_self_mutation_permitted\|_read_first_party" apps/services/orchestrator/orchestrator/agents.py`
  → no hit means `spawn_copilot_agent` still starts the sandbox for any organization.
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-231 · Confirm that `metorite` is registered in production, so its exempt name is taken · [AGENT]
- **Metorite:** #686. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** read-only, on the box: `SELECT name FROM dynamic_agents WHERE lower(name)='metorite';`
  → no row means the name is free, and an `agents:manage` holder could register another agent under it.
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-232 · Compare the member's organization with the run's in `_first_party_admin_runs` · [AGENT]
- **Metorite:** #616. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "member_org\|membership" apps/services/orchestrator/orchestrator/executor.py`
  → no hit means the gate does not compare the two organizations.
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-233 · Make the refused root agent look absent everywhere · [AGENT]
- **Metorite:** #768. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n '"code": type(exc).__name__' apps/services/orchestrator/orchestrator/executor.py`
  → a hit means a refused stream still says `AgentNotFound`, a code that no unknown agent gives.
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-234 · Run a real Copilot CLI session, to see whether it writes outside its workspace · [AGENT]
- **Metorite:** #716. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -c "write_out_of_workspace" <the log of a real task-manager turn on the local stack>`
  → no run on record means this is still unverified.
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-235 · Finish chat attachments: `.xlsx`, and one live read · [AGENT]
- **Metorite:** #782. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -c '".xlsx"' packages/acb_skills/acb_skills/attachment_text.py`
  → `0` means `.xlsx` is still unread. Step 2 stays open until a live read is on record.
- **Added:** 2026-10-04 · the H-229 slice

### H-238 · Add an app-wide CSP that limits `img-src` and `script-src` · [AGENT]
- **Metorite:** #687. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -rn "Content-Security-Policy" workbench/control_plane/next.config.ts workbench/control_plane/src/proxy.ts`
  → no hit means no app-wide CSP is set.
- **Added:** 2026-10-04 · the chat click-to-load PR

### H-240 · Give a dispatched agent run the member who assigned the task · [AGENT]
- **Metorite:** #688. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -c "session_user" apps/services/gateway/gateway/routes/projects/agent_dispatch.py`
  → `0` means the gap is open. The sink passes no member to `run_agent`.
- **Added:** 2026-10-04 · PR #622

### H-297 · Register Metorite's Zoho OAuth client, and drop its two values · [OWNER]
- **Metorite:** #736. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -cE '^CRM_ZOHO_CLIENT_(ID|SECRET)=.+' "$HOME/.metorite/secrets/app.env"`
  on the owner's PC. A count below 2, or no file, means this is open. When it
  prints 2, an agent pushes the values and runs
  `scripts/secrets.sh status app-env`. `in-sync` closes the entry. Neither
  command prints a value.
- **Added:** 2026-10-11 · branch `crm-z0`

### H-298 · Nine directory names are email addresses, and two paths still write more · [AGENT]
- **Metorite:** #773. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `SELECT count(*) FROM people WHERE name LIKE '%@%'` on production
  → above 0 means this is open. 9 of 38 on 2026-10-10.
- **Added:** 2026-10-10 · the person names session

### H-301 · Drop `mcp_servers` one release after IN-4 · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -c "FROM mcp_servers" apps/services/gateway/gateway/connections/mcp.py`
  → a count above `0` means this is open.
- **Added:** 2026-10-12 · IN-4

### H-302 · Every page mounts twice on a cold load (OrgLinkGate's Suspense fallback) · [AGENT]
- **Metorite:** #779. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** in `workbench/control_plane/e2e/shell-bar.spec.ts`, remove
  `.fixme` from "a cold load of /tasks mounts the app bar once". Run that
  test. "Expected: 1, Received: 2" means this entry is open.
- **Added:** 2026-10-12 · shell follow-ups of #918

### H-304 · A full run of `gen_tenant_migration.py` renames 12 tables' keys and policies · [AGENT]
- **Metorite:** #768. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** run `uv run python scripts/gen_tenant_migration.py`, then
  `git diff --stat infra/postgres/generated`. A diff means this is open. Put
  the files back with `git checkout -- infra/postgres/generated` after.
- **Added:** 2026-10-14 · WS-58 MD-2a

### H-306 · Seal removals in the delta feed wait for a caller that reads `removed[]` · [AGENT]
- **Metorite:** #698. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "delta/tasks" -- workbench apps packages`. Look for a
  hit outside `routes/projects/` and outside the `X` row of
  `skill_projects/manifest.py`. Also look for `delta` in `MCP_ROUTES` in
  `packages/acb_auth/acb_auth/agent_oauth.py`. No new hit means the trigger
  has not come, and this entry stays.
- **Added:** 2026-10-14 · closed PR #1010

### H-307 · Delete the orphan Mem0 memories of mailboxes that no longer exist · [OWNER]
- **Metorite:** #774. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "def reconcile_orphan" -- apps/services/gateway/gateway/routes/email`.
  No hit means no reconcile exists, and this entry stays.
- **Added:** 2026-10-14 · PR-G8 (`gmail-purge-durable`), fix round 2

### H-309 · Create the WS-59 tasks in the Metorite project "Proactive Intelligence" · [AGENT]
- **Metorite:** #854. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** in a session with the Metorite MCP, run `search_tasks` with the
  query `RE-1`. A hit in the project "Proactive Intelligence" means the tasks
  exist, and this entry goes.
- **Added:** 2026-10-14 · WS-59 RE-0

### H-310 · A WhatsApp Confirm refuses a name that the `decide` engine resolved at the ask · [AGENT]
- **Metorite:** #742. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "bind_run_context|set_run_context|artifact_context" apps/services/gateway/gateway/routes/whatsapp_channel/acts.py`.
  No hit means this is open.
- **Added:** 2026-10-15 · branch `wa-calendar-blocks`

### H-311 · One WhatsApp message can ask for only one change · [AGENT]
- **Metorite:** #776. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "One change waits at a time" apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py`.
  A hit means this is open.
- **Added:** 2026-10-15 · branch `wa-calendar-blocks`

### H-312 · Five reads of a member's settings name no tenant · [AGENT]
- **Metorite:** #562. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n -A2 "WHERE user_id = :uid" -- apps/services/gateway/gateway/routes/tasks`.
  A statement that names no `organization_id` means this entry stays.
- **Added:** 2026-10-15 · MD-3c review

### H-313 · An operator viewer can reach the tenant org purge · [AGENT]
- **Metorite:** #523. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -c "operators/session" workbench/operator_console/src/app/api/operator/purge/route.ts`.
  No hit means this is open.
- **Added:** 2026-10-15 · branch `member-remove`

### H-314 · A feed approvals test fails at one time of day · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** run `tests/unit/test_feed_handled_r8.py::TestApprovals::test_iii_org_bs_admin_sees_no_approval_of_org_a`
  with the clock frozen at 2026-10-15 07:14 UTC. A failure means this is open.
- **Added:** 2026-10-15 · branch `member-remove`

### H-315 · Submit the two Reach templates at Meta, then name them on the box · [OWNER]
- **Metorite:** #855. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** on the box, `grep -E "^REACH_(BRIEF|NEEDS_YOU)_TEMPLATE=" /opt/acb/app/.env`
  shows `metorite_daily_brief` and `metorite_needs_you`. Then this entry goes.
- **Added:** 2026-10-15 · WS-59 RE-2a

### H-323 · A DSML stream shows a first tag that a delta cut before its `>` · [AGENT]
- **Metorite:** #771. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** in `tests/unit/test_router_dsml_tool_calls.py`, compare
  `_events(TASK_TOOLS, deltas)` with `_events(TASK_TOOLS, [whole])` for the
  three cases in `specs/customer_console.md` §6A.15, "The known gap". A
  difference in any case means this is open.
- **Added:** 2026-10-15 · PR #1047

### H-316 · The member writers of assignee rows take the row and the key in different orders · [AGENT]
- **Metorite:** #500. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n -e "ids = dedupe_ids(payload.task_ids)" -e "await _move_satellites(db, str(source.id), task_id)" -- apps/services/gateway/gateway/routes/projects/bulk.py apps/services/gateway/gateway/routes/projects/merge.py`. The first anchor is the unsorted selection of the bulk edit. The second is the merge, which moves the assignee keys before `update_row` of the target. Two hits mean this entry stays. With fewer hits, read the changed code, and delete this entry when the orders agree.
- **Added:** 2026-10-15 · EA-6b fix round 3 review (taken as H-313, renumbered to H-316 at merge because main took H-313 to H-315)

### H-317 · Reach must send the needs-you template for an approval only · [AGENT]
- **Metorite:** #743. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "needs_you_template" -- apps/services/gateway/gateway/reach/loop.py`.
  Read the branch that sends the template. No test of the `approval` kind
  beside it means this is open.
- **Added:** 2026-10-15 · H-315 template submission

### H-318 · A preview app's jobs reach the tier-2 coordinator · [AGENT]
- **Metorite:** #768. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "launch" apps/services/gateway/gateway/routes/shell/intent.py`.
  No hit inside `held_jobs` means this is open.
- **Added:** 2026-10-15 · WS-61 KB-0 audit

### H-319 · Two spellings of the subject grammar: a bare email and `user:<email>` · [AGENT]
- **Metorite:** #692. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "user:" infra/postgres/268_artifacts.sql packages/acb_auth/acb_auth/permissions.py apps/services/gateway/gateway/routes/projects/core.py`.
  A `user:` prefix in the artifacts CHECK and a bare email in
  `validate_grant_subject` means this is open.
- **Added:** 2026-10-15 · WS-61 KB-0 audit

### H-328 · The workflow run lifecycle outside the agent tools names no tenant (H4 slice 6c) · [AGENT]
- **Metorite:** #856. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "await _get_db()" -- apps/services/gateway/gateway/routes/workflows/`.
  Each hit is an unbound read or write on a FORCE RLS table. This entry is
  open until the only hits left are tenant-discovery reads.
- **Added:** 2026-10-15 · PR #1052 review round 1

### H-320 · An unticked checkbox has no visible edge · [AGENT]
- **Metorite:** #754. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "border-border" workbench/control_plane/src/components/ui/Checkbox.tsx`.
  A hit in the `BASE` string means the defect is open.
- **Added:** 2026-10-15 · WS-53 CRM-Q16

### H-321 · A generic module held for a long time keeps its deleted rows · [AGENT]
- **Metorite:** #603. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "def _module_reread" apps/services/gateway/gateway/crm_sync/engine.py`.
  If that function still runs a full re-read only for a module that hides
  `Modified_Time`, this entry is open.
- **Added:** 2026-10-15 · WS-53 CRM-Q16 review

### H-322 · An artifact on a Knowledge page must count as kept · [AGENT]
- **Metorite:** #746. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `rg -n "kb_page_files" apps/services/gateway packages/acb_memory infra/postgres`.
  No hit in the artifact sweep's `keep` rule means this is open.
- **Added:** 2026-10-15 · WS-61 KB-1 audit

### H-325 · Three calls on the CRM mapping design · [OWNER]
- **Metorite:** #775. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** read `crm_platform.md` §18.9.1, "Open, with a recommendation".
  If it still lists three open calls, this entry is open.
- **Added:** 2026-10-16 · WS-53 §18.9 spec

### H-326 · A Zoho module count is higher than the records the sync keeps · [AGENT]
- **Metorite:** #551. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** on prod, compare `crm_sync_runs.estimate` with the rows in
  `crm_module_records` for DEWiN (`f01d029f-…`). If Payment_Made still holds
  3,003 rows against an estimate of 4,947, this entry is open.
- **Added:** 2026-10-16 · WS-53, DEWiN import

### H-327 · A Zoho task on a custom-module record is skipped · [AGENT]
- **Metorite:** #604. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** on prod, read the counts of DEWiN's last run. If `tasks` shows
  `unlinked` equal to `seen` (1,145 on 2026-10-15), this entry is open.
- **Added:** 2026-10-16 · WS-53, DEWiN import

### H-331 · `test_fifteen_for_each_source` fails in the full unit run, and passes alone · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** in a CI log of the whole `tests/unit` run, search for
  `test_feed_handled.py::TestTheCap::test_fifteen_for_each_source FAILED`.
  A hit in a run whose diff touches no My Day file means this is open.
- **Added:** 2026-10-16 · WS-61 KB-2a CI

### H-332 · With two panes open, the chat column on `/chat` is too narrow for a card · [AGENT]
- **Metorite:** #757. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** open `/chat` at a width of 1440 px. Open the Documents pane and
  the Files pane. Measure the message column. A width under 400 px means
  this is open.
- **Added:** 2026-10-16 · WS-27bm §14.10.9 B3 review

### H-334 · The email owner check names no tenant by default · [AGENT]
- **Metorite:** #563. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "this_tenant: bool = False" -- apps/services/gateway/gateway/routes/email/core.py`.
  A hit means this entry stays.
- **Added:** 2026-10-15 · MD-3d build · rewritten 2026-10-16, MD-3d review

### H-336 · W10: the chat's unmount save can copy mail into Mem0, so WS-7 keeps Mem0 off · [AGENT]
- **Metorite:** #694. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** run the two commands. Either one closes this entry.
  1. `git grep -c "run_holds_mail" -- apps/services/gateway/gateway/routes/memory.py`
     gives a count: the save route asks the mail predicate.
  2. `git grep -n "saveConversationOnUnmount" -- workbench/control_plane/src ":!*.test.ts" ":!*.test.tsx"`
     gives no hit: the unmount save is gone.
  While both fail, this is open. ⚠️ While it is open, WS-7 must not turn
  Mem0 on for any organization. The WS-7 row of `work_plan.md` names it.
- **Added:** 2026-10-16 · PR-G8b (`gmail-chat-copies`), fix round 1

### H-352 · Email work holds a pool connection across provider and model calls · [AGENT] · finding
- **Metorite:** #660. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** in `apps/services/gateway/gateway/routes/email/automation/runner.py`,
  find "Block W" in `_run_rules_job`. If `_apply_matches` still runs inside
  that `async with _tenant_session()`, this is open. Also check
  `_ensure_subscription` in `transport/sync.py`, and the hydrate in
  `get_message` in `transport/messages.py`.
- **Added:** 2026-10-18 · email route errors (gateway log, 7 days)

### H-354 · A refusal looks wrong in two chat places · [AGENT] · finding
- **Metorite:** #782. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** (1) In `ProjectToolCards.tsx`, if `ProjectEvidenceBody` and
  `InfoCard` still branch only on `status === "error"`, a refused Projects
  read is red. (2) In `acb_common/tool_refusals.py`, if `is_refusal` reads
  the result of every tool, this is open.
- **Added:** 2026-10-18 · review of #1126

### H-355 · Email request bodies still pass a raw account id to SQL · [AGENT] · finding
- **Metorite:** #732. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** grep the request models under
  `apps/services/gateway/gateway/routes/email/` for an `account_id` field.
  If a route passes that field to SQL without `resolve_account_filter`,
  this is open.
- **Added:** 2026-10-18 · PR #1128 and its review

### H-367 · No R8 test drives a connection with no slug through branch 0 · [AGENT]
- **Metorite:** #695. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "slugless\|no_slug" tests/unit/test_agent_client_stamp.py`.
  When an R8 test there signs a call for a DCR connection row and gets 200,
  delete this entry.
- **Added:** 2026-10-20 · PR #1184 review, P3

### H-409 · Harden EA-14a: a task that closes, and two small gaps · [AGENT]
- **Metorite:** #748. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "organization_id" apps/services/gateway/gateway/routes/projects/filters.py`
  near `unheld_clause`. When the clause, `_HOLDER_SQL` and `_RUN_SELECT` all
  carry the org predicate, item 3 is done. Read items 1 and 2 against the code.
- **Added:** 2026-10-20 · PR #1192 review, P2 and P3

### H-357 · 🔴 A key that a chat saw must be rotated, and old rows scrubbed · [OWNER] · security
- **Metorite:** #624. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** ask each org whose chats set up a key, a GitHub token first.
  Did it rotate that key after 2026-10-18? Then count the rows that still
  hold a value, on production and on staging. In `chat_message`, test
  `content`, `reasoning`, `tool_events`, `progress_lines`, `agent_state` and
  `custom_events` for `<<<SETUP:` and for `ghp_`, `gho_`, `github_pat_` and
  `ghs_`. Test `chat_session.last_preview` and `chat_session.title` too. Do
  the same for `maf_agent_session.session_json` and `agent_run.trace`. If
  every key is rotated and every count is 0, delete this entry.
- **Added:** 2026-10-18 · WS-54 Batch A fix round 2, item 5. Widened in fix round 3.

### H-359 · Three staging file races that the acb-staging-flag review left · [AGENT] · security
- **Metorite:** #772. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** in `scripts/staging_refresh.sh`, the write of `$file.tmp` runs as
  `acb` (through `setpriv` or `runuser`), and `acb-staging-flag` checks the pin
  file before it writes `.env`. If both hold, delete this entry.
- **Added:** 2026-10-18 · H-358 security review

### H-370 · Set the date the repo takes AGPL-3.0, as ported Docmost code ships · [OWNER]
- **Metorite:** #700. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** the repo root holds a `LICENSE` file that names AGPL-3.0, or the
  owner has written the date in `work_plan.md` D107. If either holds, delete
  this entry.
- **Added:** 2026-10-19

### H-360 · Widen acb-staging-flag: NEXT_PUBLIC_ rebuilds, a production mode, and an unset that lasts · [AGENT]
- **Metorite:** #772. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `deploy/hostinger/staging/acb-staging-flag` (or its successor) can set a
  `NEXT_PUBLIC_*` flag on staging and start a rebuild, and can set a flag on
  production with `--production --reason`. If both hold, delete this entry.
- **Added:** 2026-10-18

### H-364 · Harden the CRM consistency batch of §18.16 · [AGENT] · security
- **Metorite:** #738. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** each item below has its own test or review record, or a later
  entry owns it. If all hold, delete this entry.
- **Added:** 2026-10-19

### H-368 · A host shell can still reach a GitHub token outside the clone · [OWNER] · security
- **Metorite:** #565. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** on the box, run
  `grep -cE '^(GITHUB_READ_TOKEN|COPILOT_GITHUB_TOKEN)=.' /opt/acb/app/.env`.
  A count below 2 means steps 1 and 2 are open.
- **Added:** 2026-10-20 · H-363

### H-372 · Move file bytes out of Postgres and off the box's disk (MT-1g) · [AGENT] · data
- **Metorite:** #701. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -l "object_store" origin/main -- packages/acb_common`
  prints a file, and `git grep -n "BYTEA" origin/main -- infra/postgres/*.sql`
  shows `agent_blob.content` dropped or unused. If both hold, delete this entry.
- **Added:** 2026-10-20 · artifacts session

### H-373 · Try the email chat fixes of #1201, #1202 and #1203 on real mail · [OWNER]
- **Metorite:** #552. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** an agent cannot run this. It cannot sign in as the owner. The
  entry stays open until the owner says in a session that the three tries
  below worked, or names the one that failed.
- **Added:** 2026-10-21 · email chat collaboration session

### H-376 · Turn on images in reply drafts when a tier reads images · [OWNER]
- **Metorite:** #774. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** an agent cannot read the box's settings without the `secrets`
  grant. The owner confirms that `EMAIL_DRAFT_READS_IMAGES` is on in the
  gateway's environment, and that the AI Router serves the email calls.
- **Added:** 2026-10-21 · email chat collaboration session

### H-379 · Turn on send later: staging first, then production · [OWNER]
- **Metorite:** #730. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** an agent cannot read the box's settings without the `secrets`
  grant. The owner confirms that `EMAIL_SEND_LATER` is `true` in the gateway's
  environment on production. Until then, the chat says that send later is
  not on.
- **Added:** 2026-10-21 · email chat collaboration session

### H-381 · A chat plan cannot make subprojects or repeat rules (WS-46 P8, G19, G20) · [AGENT]
- **Metorite:** #490. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `uv run python -m evals.projects_ops.run --scripted`. PO-3 reads
  `xfail` while P8 and P9 are open.
- **Added:** 2026-10-23 · Projects chat parity session

### H-383 · The Projects chat has no agent-run tools (WS-55 EA-12e, G23) · [AGENT]
- **Metorite:** #490. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "EA-12e" apps/skills/skill-projects/skill_projects/manifest.py`.
  A hit in `PLANNED` means the four tools are not built.
- **Added:** 2026-10-23 · Projects chat parity session

### H-384 · Run the Projects eval with the live model, and add tasks for delete and show_view · [OWNER]
- **Metorite:** #488. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `ls evals/projects_ops/results/`. No run made with `--tier` means
  the sweep has never run. `grep -n "show_view\|delete_task" evals/projects_ops/tasks.py`
  shows whether the new tasks exist.
- **Added:** 2026-10-23 · Projects chat parity session

### H-385 · Decide which excluded Projects acts move into the chat · [OWNER]
- **Metorite:** #501. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `uv run python -c "import skill_projects.manifest as m; print(sum(r.cls=='X' for r in m.MANIFEST))"`
  from `apps/skills/skill-projects`. It printed 38 on 2026-10-22.
- **Added:** 2026-10-23 · Projects chat parity session

### H-386 · Try the three Projects chat changes of 2026-10-22 in a browser · [OWNER]
- **Metorite:** #554. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** the owner confirms each item below on `app.metorite.com`. An agent
  cannot sign in with Google or Microsoft.
- **Added:** 2026-10-23 · Projects chat parity session

### H-387 · Check that the Projects tools run from the assistant outside /projects · [AGENT]
- **Metorite:** #484. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** nobody has measured it. Delete this entry when the answer is
  recorded in `projects_agent_parity.md`.
- **Added:** 2026-10-23 · Projects chat parity session

### H-390 · Email reads answer 503 when the gateway's database pool is full · [AGENT]
- **Metorite:** #633. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `ssh metorite "sudo journalctl -u acb-gateway --since -24h -o cat
  | grep -c 'QueuePool limit'"` prints 0. If it does for a week, delete this
  entry.
- **Added:** 2026-10-23 · email chat session

### H-391 · An email write over WhatsApp says "ask in the app" · [AGENT]
- **Metorite:** #744. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** in a WhatsApp chat, ask the assistant to archive one mail. If it
  does so after a confirm, delete this entry.
- **Added:** 2026-10-23 · email chat session

### H-396 · Try the chat fixes of 2026-10-22 on a phone · [OWNER]
- **Metorite:** #758. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** the owner says each item below works. Then delete this entry.
- **Added:** 2026-10-23 · email chat session

### H-397 · Turn on AI proposals in Knowledge once the Review view is live · [OWNER]
- **Metorite:** #370. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `ssh metorite 'sudo grep -c "^KNOWLEDGE_PROPOSALS=true" /opt/acb/app/.env'` prints 1, and so does the staging `.env`. If both print 1, delete this entry.
- **Added:** 2026-10-23 · Knowledge session

### H-398 · Turn on knowledge gaps and the kb_gap My Day card · [OWNER]
- **Metorite:** #371. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `KNOWLEDGE_GAPS` names the org and `MY_DAY_PROVIDERS` holds `knowledge` in the production `.env`. If both hold, delete this entry.
- **Added:** 2026-10-23 · Knowledge session

### H-399 · Build the next Knowledge AI slices of §22 · [AGENT]
- **Metorite:** #366. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -nE "KB-AI-2|KB-AI-3|KB-AI-4|KB-AI-6" project-docs/specs/knowledge_base.md`. Delete this entry when each row reads BUILT.
- **Added:** 2026-10-23 · Knowledge session

### H-400 · Finish the Notion and Confluence parity of wave 2 · [AGENT]
- **Metorite:** #366. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** the wave 2 table of `knowledge_base.md` §21.7 marks KB-2j, KB-2o and KB-2c BUILT. If all three are BUILT, delete this entry.
- **Added:** 2026-10-23 · Knowledge session

### H-401 · Run the hardening pass of the Knowledge build · [AGENT]
- **Metorite:** #410. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -c "^| K-" project-docs/hardening_backlog.md` prints 0. If it prints 0, delete this entry.
- **Added:** 2026-10-23 · Knowledge session

### H-403 · Check that the last two Knowledge PRs of 2026-10-23 merged and reached staging · [AGENT]
- **Metorite:** #745. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `gh pr view 1241 --json state` reads MERGED, and `curl -s https://api.dev.metorite.com/version` shows a SHA at or after its merge. If both hold, delete this entry.
- **Added:** 2026-10-23 · Knowledge session

### H-407 · Mem0 memory reads a member's private facts into an org-visible room of one · [AGENT] · security
- **Metorite:** #696. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `grep -n "shared=_room_is_shared\|_clearance.write if _room_is_shared" apps/services/gateway/gateway/routes/agent.py` prints nothing, because the memory pre-read and the extraction use `not room_is_solo(room)` from `gateway/rooms.py`. If it holds, delete this entry.
- **Added:** 2026-10-24 · WS-63 PZ-2 repair round

### H-418 · Finish PR #1400, the chat wave 2 of WS-64 · [AGENT]
- **Metorite:** #874. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `gh pr view 1400 --json state -q .state` prints OPEN. If it prints MERGED, check that staging and production serve its SHA, then delete this entry.
- **Added:** 2026-10-27 · WS-64 session

### H-419 · Merge PR #1399, the manifest decisions for the OP-11 routes · [AGENT]
- **Metorite:** #875. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `gh pr view 1399 --json state -q .state` prints OPEN.
- **Added:** 2026-10-27 · WS-64 session

### H-420 · Build wave 3: task ids with a space prefix, such as OPS-447 · [AGENT]
- **Metorite:** #876. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git log origin/main --oneline -i --grep "prefixed task id"` prints nothing.
- **Added:** 2026-10-27 · WS-64 session

### H-421 · Finish the My tasks leftovers of OP-11 · [AGENT]
- **Metorite:** #877. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "MY_TASKS_MAX_ROWS" origin/main -- workbench/control_plane/src/app/projects/lib/myTasks.ts` prints a line. The 2000-row cap still exists.
- **Added:** 2026-10-27 · WS-64 session

### H-422 · OP-5: retire the old Tasks code and merge the backend · [AGENT]
- **Metorite:** #878. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `test -d apps/services/gateway/gateway/routes/tasks && echo PENDING`
- **Added:** 2026-10-27 · WS-64 session

### H-423 · Decide how a space summary counts what a member cannot see · [OWNER]
- **Metorite:** #879. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** read `fold_node_summary` in `apps/services/gateway/gateway/routes/projects/tree.py`. If its child walk has no `project_clause`, this is pending.
- **Added:** 2026-10-27 · WS-64 session

### H-424 · Two loose ends of the chat timeout fix · [AGENT]
- **Metorite:** #880. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "run_task = asyncio.create_task" origin/main -- apps/services/orchestrator` prints `executor.py`. The Tier-2 fallback still starts a task that nothing cancels.
- **Added:** 2026-10-27 · WS-64 session

### H-425 · Move six Fracktal tasks out of the space Company Operations · [OWNER]
- **Metorite:** #881. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** ask the chat in Fracktal to list the tasks of the space Company Operations. Six tasks (#447 to #452) mean it is pending. An admin may read `pm_tasks` through the ap-south-1 pooler instead.
- **Added:** 2026-10-27 · WS-64 session

### H-426 · Decide how a repeating task behaves when it leaves Done · [OWNER]
- **Metorite:** #882. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** Metorite task #49 is not Done.
- **Added:** 2026-10-27 · WS-64 session

### H-427 · Find the cause of the duplicate board names, and look at the chat task table · [AGENT]
- **Metorite:** #883. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** Metorite tasks #48 and #50 are not Done.
- **Added:** 2026-10-27 · WS-64 session

### H-428 · Pin the other CI jobs to ubuntu-24.04, and look at one flaky browser test · [AGENT]
- **Metorite:** #884. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "ubuntu-latest" origin/main -- .github/workflows` prints lines.
- **Added:** 2026-10-27 · WS-64 session

### H-429 · Small gaps that wave 2 left · [AGENT]
- **Metorite:** #885. The task holds the full entry (moved 2026-10-28, D118).
- **Check:** `git grep -n "no-offset\|without an offset" origin/main -- apps/services/gateway/gateway/routes/projects/due.py` prints nothing.
- **Added:** 2026-10-27 · WS-64 session

### H-437 · Give a web chat run the credential filter of its member · [AGENT] · security
- **Metorite:** #1120. The task holds the full entry.
- **Check:** in `apps/services/orchestrator/orchestrator/executor.py`, `async def _integration_authorizer` takes only `event_payload` and `thread_id`, and reads the member from the payload → still open. That one read sets two things: the credential filter, and the memory permission gate through `_set_memory_permission`.
- **Added:** 2026-10-30 · H-337 session

### H-438 · Take the /pull member from the session before Mem0 turns on · [AGENT] · security
- **Metorite:** #1127. The task holds the full entry.
- **Check:** `grep -n "req.user_email or" apps/services/gateway/gateway/main.py`. A hit in the `/pull` handler (2026-10-30: `user_id: str = req.user_email or ...`) means still open.
- **Added:** 2026-10-30 · H-337 session
