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
### H-416 · Decide whether the staging apply installs the staging flag tool itself · [OWNER]
- **Check:** on the dev box (srv1747539), run
  `cmp /opt/acb/app/deploy/hostinger/staging/acb-staging-flag /usr/local/sbin/acb-staging-flag`.
  A difference means the installed tool is behind the repo copy. If it exits 0, or the owner has said no, delete this entry.
- **Why:** each change to the allowlist needs a root `install` by the owner. On 2026-10-25 the profile switch waited for that step, and on 2026-10-27 the copy differs again. The installed copy still takes `ASSISTANT_PROFILE_ENABLED`.
- **Decide (owner), once:** let the root staging apply of `vps_apply.sh` install the tool when the file in the checkout differs (`cmp`, then `install`). The cost: root then copies a file that `acb` can write. `acb` is already root-equivalent through the `docker` group (see the README of `deploy/hostinger/staging/`), so the cost is small today. Say yes or no, and an agent builds it.
- **Added:** 2026-10-26 · owner, in chat, 2026-10-25

### H-430 · Build PZ-31: a member answer that becomes a steer cannot be saved · [AGENT] · security
- **Metorite:** #832. The task holds the full entry.
- **Check:** `grep -n '_typed = ""' apps/services/gateway/gateway/routes/agent.py` → still shows the steer follow-up binding empty typed words (about line 2856), and task #832 is not Done
- **Added:** 2026-10-27 · found by the review of the interview save fix (first minted as H-418, renumbered: main took it)

### H-431 · Look at Settings → Assistant settings, and say what still feels stiff · [OWNER]
- **Metorite:** #261 (PZ-11). The task holds the checks and the state.
- **Check:** task #261 is not Done → still pending. On app.metorite.com: reload, open the account menu, choose Settings → Assistant settings, then start an empty chat and press "Yes, start". Tell the agent what looks wrong and what feels stiff.
- **Added:** 2026-10-27 · WS-63 session end (first minted as H-419, renumbered: main took it)

### H-335 · Finish the staging box: DNS, two OAuth clients, one sudo run · [OWNER]
- **Check:** `curl -s https://api.dev.metorite.com/version` answers a `sha` equal
  to `git rev-parse origin/staging`. If it does, delete this entry.
- **Why:** WS-38 phase 4. The code that makes the dev box the staging box is on
  `main` (`specs/staging_and_daily_release.md` §5.11). Four steps need the owner,
  because they are outside accounts and root on the dev box.
- **Do this (owner):** follow `project-docs/STAGING_SETUP_RUNBOOK.md` parts A
  to E. A browser session on the owner's PC can run them. They are the DNS
  records, a Google client, a Microsoft app, a second Router key on
  `hathi-labs-llp`, and the setup run in the hPanel browser terminal.
- **Then (agent):** part F of the runbook. Add the key line that the bootstrap prints to
  `/home/acb/.ssh/authorized_keys` on the production box. Add
  `Match User acb` / `AllowStreamLocalForwarding no` to its sshd_config, run
  `sshd -t`, then reload ssh. Set
  `STAGING_EXPORT_SLUGS=default,fracktalworks,hathi-labs-llp,smoke-chat` in the
  production `.env`. Wait for one nightly backup, or run it, and then
  `sudo systemctl start acb-staging-refresh.service` on the dev box. Report each
  production act in the same message (CLAUDE.md §3a rule 2).
- **Authority:** `specs/staging_and_daily_release.md` §5.10 and §5.11
- **Added:** 2026-10-16 · WS-38 staging session

### H-361 · Raise the WSL2 CPU cap, so local CI runs in about 10 minutes · [OWNER]
- **Check:** `grep -E "^processors=" ~/.wslconfig` on the owner's PC reads 16
  or more. If it does, delete this entry.
- **Why:** local CI (`bash scripts/ci_local.sh`) passed in 17.7 minutes on
  2026-10-19. Each of the six shards took about 16 minutes, because
  `.wslconfig` caps ALL of WSL2 at 8 processors and 16 GB. The PC has 28
  cores and 64 GB.
- **Do this (owner):** in `C:\Users\<you>\.wslconfig`, set `processors=20`
  and `memory=32GB`. Then run `wsl --shutdown` at a quiet moment, because it
  stops Docker Desktop and every WSL window for a few seconds.
- **Authority:** `specs/engineering_practice.md` §7a
- **Added:** 2026-10-19 · CI speed-up session

### H-283 · Prove the first `credit_draw` row on production · [AGENT]
- **Check:** on the box, query the Console database:
  `SELECT count(*), max(created_at) FROM credit_draw;`. A count above zero
  means the write path runs in production, and this entry is done.
- **Why.** Migration 036 (WS-50, PR #779) applied on 2026-10-09 at 14:46 UTC,
  and the table exists. No billed AI call happened before the session ended,
  so no row proves the write path yet. The R8 suite proves it on a scratch
  database only.
- **Do this:** after the first billed call, run the Check. If `usage_event`
  has billed rows after 2026-10-09 14:46 UTC and `credit_draw` has none, the
  write path is broken: read `store.record_draws` and the charge path.
- **Authority:** `CLAUDE.md` §3 rule 8 (verify by evidence)
- **Added:** 2026-10-09 · WS-50

### H-284 · Two revenue figures the Money page cannot show yet · [AGENT]
- **Check:** `rg -n "price_paid_inr" apps/services/customer_console/customer_console/main.py`
  inside `ManualActivationRequest`. No hit means gap 1 is open.
  `rg -n "def all_lots|credits_paid_total" apps/services/customer_console`
  with no hit means gap 2 is open.
- **Gap 1.** Start paid plan (`/billing/subscriptions/activate`) can include
  credits, and it writes a `manual` purchase lot with no price. PR #786 fixed
  the same gap on `/credits/grant` only. The Money page values those credits
  at the current price and labels them an estimate.
- **Gap 2.** "Cash received" (D94 rule 3) needs every lot, spent or not.
  `/billing/summary` returns only open lots (`store.open_lots`), so the
  Billing tab cannot total what a customer has paid us.
- **Authority:** `specs/operator_console_money.md` §3, D94
- **Added:** 2026-10-09 · WS-50

### H-150 · The right-click menu on a task cannot be opened on a phone · [AGENT]

- **What happens.** At 390px the board draws its cards, and a right-click
  on one opens nothing. Measured 2026-09-21 in the visual rig: the card is
  in the DOM, and `Copy link` — the second entry in the menu — has a count
  of zero after the gesture.
- **⚠️ It is EVERY entry, not one.** Open, Copy link, Select, Move to
  project, Merge into, Change status, Rename, Add subtask, Archive, Restore
  and Delete are all in that menu and all unreachable. Six of them have no
  other door on a phone at all.
- **It is pre-existing**, and predates the Merge entry that found it. The
  entry itself is fine — it draws and works at every desktop width.
- **Why a phone has no right-click, and what that means.** A touch device
  does not fire `contextmenu` from a tap, and a long press raises the
  platform's own menu instead. So the surface needs a different door: the
  card's overflow button, or a long press the component handles itself.
  `TaskCardActions` already draws a "more" button on hover, and hover does
  not exist on a phone either — so the two gaps are one gap.
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

### H-293 · Decide whether agent runs move to their own service, so a deploy stops none · [OWNER]
- **Metorite:** #769. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** run `rg -n "D-1" project-docs/specs/chat_run_continuity.md`. If
  the §5 row still says **OWNER-GATE**, the decision is open.
- **Added:** 2026-10-10 · branch `ws51-run-cap-and-todos`

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

### H-259 · Make a failed nightly backup reach a person · [AGENT]
- **Metorite:** #550. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `rg -n "acb-backup" .github/workflows/vps-health.yml`
  → no hit means this is open.
- **Added:** 2026-10-06 · the backup verify-drop fix (branch
  `backup-verify-drop`)

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

### H-178 · An operator can take a job off the air. Two callers cannot yet · [AGENT]
- **Check:** `rg -c "model IS NOT NULL" packages/acb_llm apps/services/gateway`
  → a zero in a file that reads `tier_binding` means a reader was added
  without the tombstone filter, and this is open again.
- 📌 **BUILT 2026-09-24.** `DELETE /catalog/bindings` takes a job off the air
  by APPENDING a row whose `model` is NULL. Migration 034 makes the column
  nullable. The tiers board offers it where the dead Save used to be.
- 🔴 **Why it was needed.** `tier-stt` pointed at a Groq model on a box
  holding only a DeepSeek credential, so every transcription failed at the
  provider and billed zero. The answer was to unbind it and the console could
  not: `POST /catalog/bindings` refuses an empty chain.
- ⚠️ **A SEPARATE verb, and that is the safety of it.** Letting `POST` accept
  an empty `models` list would mean a caller that simply forgot the field
  takes a tier off the air. A different verb cannot be reached by forgetting.
- 🔴 **Two things the tests caught that reading did not.**
  `resolve_tier` took the newest ROW that had a model, so it stepped over a
  tombstone and kept serving the superseded binding — unbinding would have
  changed nothing the Router does. And migration 010 backfills
  `model_capability.model` from `tier_binding.model`, which is NOT NULL, so a
  ladder replay against a database holding one tombstone died outright.
- ⚠️ **Two fences moved from a VERB to the rule they stood for.**
  `test_no_route_updates_or_deletes_a_binding_or_a_rate` and its frontend twin
  banned the `DELETE` method. The rule is *no row is destroyed*, and this
  route destroys none. Both now read the SQL, which is where the rule can be
  true or false. `PATCH` and `PUT` stay banned on their names.
- ⚠️ **WHAT IS LEFT: two readers outside the Console.** The check above names
  them. `packages/acb_llm` and the gateway do not read `tier_binding` today,
  and if either starts, it needs the filter or it will resolve a model named
  NULL.
- **Fences:** `tests/unit/test_unbind_a_tier.py` (11, R8).
- **Authority:** owner report, 2026-09-24 — *"Shouldn't you be able to allow
  me to remove a model from a tier?"* · §6A.5
- **Added:** 2026-09-24 · the unbind session.

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

### H-166 · Turn on `decide` for email on the box · [OWNER]
- **Check:** on the box, run `grep -E 'DECIDE_ENABLED|CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY' /opt/acb/app/.env`. Two lines with `true` mean steps 2 and 3 are done. Then look for one `decide.decided` line in the gateway journal. In `shadow` the line is `decide.shadow`. A line means the box gets answers.
- **Done, by owner report on 2026-10-02 (not measured):** the AI/ML API account, the key, and `tier-decide` bound to `aimlapi/typesafe/jev`. D-EM-9 answers residency for email triage.
- **Done, by owner report on 2026-10-02 (decision (a), `email_app_master_plan.md` §10.2):** `DECIDE_ENABLED=true` is ON in production since 12:16 UTC. One smoke `decide` call from the box reached Jev, with a probability of 0.99 in 1.5 s. So step 3 below is done.
- **Done, by orchestrator report on 2026-10-02:** `DECIDE_FEATURE_MODES=email.rule_match=on` and `DECIDE_FEATURE_ORGS=*` are on the box since 16:31 UTC. The first live `decide.decided` line came at 16:50:47 UTC.
- **Measured on the box, 2026-10-09:** `DECIDE_ENABLED=true`, `DECIDE_FEATURE_MODES=email.rule_match=on,email.thread_status=on` and `DECIDE_FEATURE_ORGS=*`. So `email.thread_status` is already `on`, and it is no longer next.
- ⚠️ **Drift (D-EM-62).** The thread status went `on` before PR-B3 of EM-T4a-2 merged. Until PR-B3 merges, the status ask of `on` runs inside a block (`email_app_master_plan.md` §10.4.6). EM-T16 PR-0 is PR-B3, and its merge closes the drift (§10.4.17).
- **Next, after EM-T16 PR-C merges:** the orchestrator sets `DECIDE_FEATURE_MODES=email.rule_match=on,email.thread_status=on,email.cold_check=on,email.sender_pin=on` and restarts the gateway. D-EM-62 approves this step. Then it reports one `decide.decided` line for each feature, each with a `request_id`. The names are the names in `decide_features.FEATURES`. A misspelt name logs `decide.mode_refused` and stays `off`.
- **Do this, in order:**
  1. Give the deployment key of the box the `serve` capability. It is a hand edit (§8 gate 7), as H-152 says.
  2. Set `CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY=true`. Leave `ROUTER_SERVING_ENABLED` unset, so chat stays on its current path.
  3. Set `DECIDE_ENABLED=true`. No agent sets it (§6.1 WS-31 (i)).
  4. Bind a backup step on `tier-decide` when a second decision model exists. Today one vendor outage stops triage for every mailbox (D-EM-8).
  5. Say whether D-EM-9 also covers the chat `decide` tool. On an org key, the switch opens that tool too. On the deployment key, the tool sends no member and stays dark.
  6. Accept or reject the D61.1 amendment (D75 clause 3). The build does not wait.
  7. Say whether the shadow window may start before EM-T4b, the shared cap. The 2026-10-02 note of this entry tied each mode to that cap. With one live mailbox, the runner asks about one email at a time.
- ⚠️ **Add AI/ML API AND TypeSafe to the sub-processor list** when WS-37 writes one (H-36). A reseller call passes through both.
- **Then:** the agents run the shadow window of `email_app_master_plan.md` §10.4.8.
- **Authority:** D-EM-7 to D-EM-9 · D-EM-62 · `work_plan.md` §6.1 WS-31 (i) · `customer_console.md` §6A.14
- **Added:** 2026-09-23 · **rewritten 2026-10-02** by the EM-T5b audit · **corrected 2026-10-09** by the EM-T16 spec

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

- 🟢 **2026-09-29 — the deploy-ssh-resilience PR changes the deploy. Keep
  this entry until a real blip proves the change.**
  - **What we measured.** 200 deploy runs from 2026-09-20 to 2026-09-28.
    9 runs went red on `ssh exited 255`. Each one lost all three rounds,
    so 27 of 27 connects failed. The 163 green runs hold no 255 round. A
    different runner connected 10 s after the last failed round, twice.
    The box served the commit in all 9 cases. The pull timer applied it 7
    times, and an apply of a later commit carried the other 2.
  - **What the PR changes.** A cheap `ssh … true` probe runs before each
    round, with backoff, inside one connect budget of 300 s for the job. A
    connect failure does not spend a round. When the budget runs out, the
    `deploy` job hands off. It says whether an apply started from its runner
    and then lost ssh. In that case the box can hold a half-done apply, and
    the pull timer applies it again because the marker was not written. A
    new job, `Confirm delivery by the pull path`, then runs on a fresh
    runner. It reads `/version` over HTTPS for up to 1500 s. An auth failure
    goes red at once and does not hand off.
  - **The evidence it accepts.** `/version` now also reports `applied_sha`,
    the box's deploy marker. `vps_apply.sh` writes that marker only at its
    last line, after the web build. The job is green only when `sha` and
    `applied_sha` both contain this commit and the web app answers. `sha`
    alone speaks for the gateway only, because the gateway restarts before
    the web build.
  - **How the next blip reads in CI.** The `deploy` job is green with the
    warning "Deploy runner could not reach the box". Then the confirm job
    is green with the warning "Delivered, confirmed by the served SHA", or red with
    "Not delivered" or "Box unreachable from GitHub". A forced rerun never
    hands off, and it goes red.
- **Check:** find the next deploy run that shows the warning "Deploy
  runner could not reach the box". Use `gh run list --workflow deploy`,
  then `gh run view <id> --log | grep -F "could not reach the box"`. If the
  confirm job in that run shows the served SHA and no false red, delete
  this entry. Also delete it if no such run occurs by 2026-11-30, because
  the tests in `tests/unit/test_deploy_reach.py` then carry the fence.
- **What happened, 2026-09-21.** The deploy for #344 failed. All three
  rounds ended `ssh: connect to host *** port ***: Connection timed out`,
  30 seconds each. A plain re-run minutes later succeeded with no change,
  so the box was reachable throughout from elsewhere — I held an
  interactive ssh session to it during the failure and ran commands.
- **The retry window is too short for the thing it retries.** Rounds are
  60s and 120s apart, so the whole ladder finishes inside about 4.5
  minutes and every try carries the same 30-second connect timeout.
  A blip that lasts five minutes defeats it, and the job then reports a
  failed RELEASE for what was a failed CONNECTION.
- **⚠️ The two are not the same failure and must not read the same.**
  H-137 made a half-finished apply go red, which was right. This is the
  opposite case: nothing was applied, nothing is half-done, and the
  last build is still serving. Telling those apart is the difference
  between "re-run this" and "go and look at the box".
- **Why it did not cost a release this time**, which is its own problem:
  `acb-pull.timer` delivered the commit anyway. See [[H-89]].
- **What to do.** Give the connect failure its own exit path and message,
  separate from an apply that started and stopped. Then widen the backoff,
  or raise `ConnectTimeout`, so a minutes-long blip does not read as a
  broken deploy.
- **The old Check, now passed:** the deploy step prints "THE APPLY NEVER
  STARTED" when ssh never connects. It prints "NOTHING WAS APPLIED" when the
  round waited for the lock and gave up.
- 📌 **2026-09-26 — a third shape of the same sentence.** After the
  deploy-serialize PR, a CI round can connect, wait 900s for the box's
  deploy lock, and stop with nothing applied. The apply log then says
  `another deploy holds the lock since <time>, pid <pid>`, and the workflow
  still prints "THE APPLY DID NOT REACH ITS FINAL LINE". The fix for this
  entry must separate that case too. The round applied nothing, and the
  box's own pull held the lock to deliver the release.

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
- **Check:** `grep -n "CapacityLimiter\|Semaphore\|total_tokens" apps/services/customer_console/customer_console/main.py`.
  No hit means nobody has capped the stream walk, and this entry is still real.
  ⚠️ **The grep reads `main.py` alone** *(named 2026-08-31)*. A cap that lands
  in a NEW module still reads as no hit, so this entry would say STILL PENDING
  after the repair. Widen the path to the package when you close it.
- **Why:** WS-31 slice 11 moved the provider stream open into the route. A
  `def` route now holds one of anyio's 40 DEFAULT threadpool tokens for the
  length of the walk. That is up to `3 x 120` seconds. The `asyncify` helper in
  litellm borrows from the SAME default pool, because `asyncify.py:60` and
  `:66` pass `limiter=None`. Then anyio resolves `limiter or
  cls.current_default_thread_limiter()` (`anyio/_backends/_asyncio.py:2480`),
  and that factory builds `CapacityLimiter(40)` (`:2957`). Put 40 stream walks
  beside one asyncify-bound model, and nothing recovers.
- ⛔ **The fix shape this entry carried is WITHDRAWN** *(2026-08-31)*. It read
  "a bounded semaphore in front of `_open_stream_chain`". FastAPI takes the
  threadpool token BEFORE the route body runs. `run_endpoint_function` calls
  `run_in_threadpool(dependant.call, **values)` (`fastapi/routing.py:315`). So
  a semaphore inside the route blocks a thread that ALREADY holds a token. It
  reserves no headroom, and the deadlock stands.
- 📌 **The scope is narrower than it looks.** Both buffered paths call
  `asyncio.run` (`main.py:5398`, `:5733`), which builds a PRIVATE loop. A
  `RunVar` (`_asyncio.py:2085`) keys anyio's default limiter, so a private loop
  gets its own 40 tokens. Only `_open_stream_chain` shares the serving loop's
  limiter, because it alone calls `anyio.from_thread.run`.
- 🔴 **Two shapes WOULD work, and both change throughput.** An `async def`
  dependency runs on the loop BEFORE FastAPI takes the token, so it can cap
  stream requests. That cap makes the 9th concurrent stream WAIT. The other shape
  raises `total_tokens` on the default limiter at startup, which changes the
  thread budget of EVERY route. §3.6 records no decision on either, so this
  entry is the owner's.
- 📌 **Latent, and one row from live.** Every model bound today (deepseek,
  groq) reaches httpx and borrows no thread. Two request-serving `asyncify(`
  sites exist: Vertex AI (`vertex_llm_base.py:718`) and SageMaker
  (`sagemaker/completion/handler.py:428`, `:499`). A vendor swap is one
  `tier_binding` row, which is an operator act that no code review sees.
- **Authority:** `ai_metering_and_analytics.md` §8.6 "The threadpool hazard"
  · board row WS-31
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

### H-37 · §2 rows have re-grown past the size D18 was minted to fix · [AGENT]
- **Check:** `awk '{ print length($0) }' project-docs/work_plan.md | sort -rn | head -1`
  → anything above ~20,000 means a single board row is still carrying a session's
  narrative. Measured 2026-08-26: **71,702**.
- **Why:** D18 (2026-08-09) moved row narrative into the owning specs' *"Board record"*
  sections because §2 had reached ~77k tokens and was *"unreadable in one pass by the
  dispatch loop it serves"*. Measured 2026-08-26 the rows are **past where they were**:
  WS-31 71,702 characters on one line, WS-29 46,587, WS-27 41,319, WS-26 20,779,
  WS-30 12,607 — roughly 190k characters of narrative in five cells.
  **The mechanism is not carelessness and naming it matters:** a build session appends
  its findings to the row because the row is where it is already looking, and each
  append is individually correct. D18 is a rule with no fence, so under R7 it is
  advisory — which is precisely why it decayed twice.
  **What to do:** relocate each row's narrative into its owning spec's Board-record
  section, leaving state, gates and pointers (D18's own shape), **and give the rule a
  fence** — a test asserting no line in `work_plan.md` §2 exceeds a stated length is
  cheap, structural, and is the thing that stops a third recurrence.
  ⚠️ **Do it as its own PR.** It was deliberately not folded into the 2026-08-26
  product pass: a ~190k-character move inside a diff that also changes states is how
  a real correction gets lost.
- **Authority:** `work_plan.md` §5 residual 8 item 9 · D18 · R7
- **Added:** 2026-08-26 · multi-tenancy product pass

### H-38 · Decide D-D (where staging runs, and what it costs) · [OWNER]
- **Check:** `rg -n "D-D" project-docs/work_plan.md` → a hit only inside **D55.9's
  "still owed"** clause (rather than a recorded answer in §3) means it is still open.
- **Why:** D55 adopted the delivery framework and answered five of its seven open
  decisions. **D-D is money plus an external account, which is owner-side by
  `customer_console_infrastructure.md` §7** — a second VPS, a second Supabase pair, or
  both. It is the one thing between here and the staging half of WS-38: **T-7
  (`staging` ref), T-8 (`release-promote`) and T-9 (the nightly anonymised rebuild) all
  wait on it.** Everything else on WS-38 — T-1, T-2, T-3, T-6, T-5 — is buildable during
  Phase 0 and needs no staging box, which is why the order puts them first.
  📌 **Worth knowing before deciding:** staging is a *nightly re-derivation* of
  production, not a maintained copy (D55.5), so it can be smaller than production and can
  be torn down and rebuilt. It is a cost that scales with nothing.
  🆕 **2026-10-15:** read `specs/staging_and_daily_release.md` §5 and §7 first. It
  recommends the dev box `srv1747539` for staging and a daily release at 21:30 UTC.
- **Authority:** `work_plan.md` D55.9 · §2 WS-38 ·
  `specs/development_and_delivery_framework.md` §9 D-D · §3.5
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
- **Check:** `uv run pytest tests/unit/test_projects_sql_asyncpg.py
  tests/unit/test_auth_sql_asyncpg.py -q` with `TENANT_LADDER_DATABASE_URL`
  set. If either SKIPS in CI, the strong half of the fence does not fire
  there — which is H-112's gap, on two files now.
- **What is CLOSED — TWO modules now.**
  1. **Projects.** `test_projects_sql_asyncpg.py` runs every `analytics.py`
     builder and both `tree.py` reads on **asyncpg**, the driver
     `acb_common.db` rewrites every production DSN onto.
  2. **The sign-in path, added 2026-09-18.** `test_auth_sql_asyncpg.py` runs
     all **38** SQL statements in `acb_auth` — `access.py`, `console_resolve.py`
     and `email_otp.py` — each with the parameter TYPES its real call site
     binds. It went ahead of the route modules on blast radius: a broken route
     breaks one pane, and a broken statement in `access.py` breaks sign-in for
     everybody, because every request resolves identity and access before it
     reaches any route.
  3. `test_sql_interval_shape.py` refuses `CAST(:param AS interval)` anywhere
     under `gateway/` or `packages/`, needs no database, and so runs in CI
     today.
- **⚠️ The auth suite carries its OWN completeness fence.** A new `_SQL`
  constant in any of those three modules fails `test_every_SQL_constant_in_
  these_modules_is_covered` by name. Without it the suite stops being complete
  the first time somebody adds a statement, and a partial fence reads exactly
  like a whole one.
- **Measured, not assumed.** Retyping one `datetime` bind to an ISO string —
  the precise psycopg-accepts / asyncpg-refuses divergence — turns **5** of the
  38 red, and all five are `CAST(… AS TIMESTAMPTZ)` statements the lint below
  cannot cover. Dropping one case from the list turns the completeness fence
  red with that statement's name.
- **⚠️ What is STILL OPEN.** The route modules — **CRM, email, tasks, people,
  notes, chat, admin, workflows, whatsapp** — still prove their SQL on psycopg
  alone. The pattern to copy is now in two files: a parametrised `_cases()`
  list, a module-scoped `apply_ladder` fixture, and a **function-scoped** async
  engine with `NullPool`. ⚠️ A module-scoped engine binds its pool to the first
  test's event loop and every later test fails with *"another operation is in
  progress"*, which reads exactly like a SQL fault and is not one.
  ⚠️ Many route modules hold their SQL **inline inside route functions** rather
  than as module constants, so they cannot be run without the route. Extracting
  those is the real cost of the remaining work, and it is worth doing anyway:
  an unextractable statement is also an untestable one.
  ⚠️ **Roll back.** Half of the auth statements are writes. `begin()` as a
  context manager COMMITS on a clean exit, so the transaction is driven by hand
  and rolled back in a `finally`.
- **Not linted: `CAST(:x AS timestamptz)`.** Binding a real `datetime`
  through it is correct, and `email/automation/followups.py` does that. A
  lint there would fail correct code and grow an allowlist. The asyncpg
  suites are the answer for that half.

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
- **The fix, in two parts.** Make the workflow FAIL when the apply exits
  non-zero, instead of falling through to the health probe. Then repair the
  ownership so the install stops failing — H-89 owns the cause.
- ✅ **PART ONE SHIPPED, PR #343, 2026-09-21.** The workflow now greps the
  apply output for the script's own last line, and a round needs BOTH a
  finished apply and a passing verify. A non-zero ssh AFTER the marker is
  still tolerated, because a teardown flake is not a failed release.
  `test_deploy_pipeline.py::TestTheApplyMustReachItsEnd` fences both halves of
  that string contract. Verified on its own deploy: the marker check ran and
  the tail step was reached.
  ⚠️ **The Check above is now the WRONG question.** A green run may hold
  `ssh exited non-zero` legitimately. Ask instead whether a run that did not
  finish went red.
- ⚠️ **PART TWO IS STILL OPEN**, and it is the cause. `npm ci` hits EACCES on
  root-owned paths inside `node_modules`. The ownership repair at the top of
  `vps_apply.sh` deliberately prunes that directory. **H-89** owns it. Until it
  closes, some deploys go red — correctly, and visibly.
- 🟢 **2026-09-26 — part two is in the deploy-serialize PR.** The pull path
  runs as `acb`, so no root-owned path reaches `node_modules` again, and both
  paths take one lock. This PR keeps the success gate. The final line
  `==> Deployment complete` now also prints when the apply SKIPS, because a
  complete apply of that same sha already finished. The apply writes the
  marker file `/opt/acb/acb-deploy.applied` only just before that line.
  ⚠️ A CI round that waits 900s for the lock and does not get it prints
  `another deploy holds the lock since …` and exits 1 with nothing changed.
  That round is red on purpose, and the next round then usually skips.
- **Authority:** `.github/workflows/deploy.yml` · `scripts/vps_apply.sh` · H-89
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

### H-168 · The gateway refuses the LLM key on `/v1/embeddings` · [AGENT]
- **Metorite:** #683. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** `grep -n '"/v1/embeddings"' apps/services/gateway/gateway/main.py`
  → one hit, on the route only, and not in `PUBLIC_ROUTES`, means this is open.
- **Added:** 2026-09-23 · the review of PR #407

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
- **Check:** `grep -n "handle.over_quota = used" apps/services/orchestrator/orchestrator/sandbox_broker.py`
  → a hit means the entry is still open.
- **Why:** `measure_workspace` sets `over_quota` on the handle of the exec
  that measured. Two threads of one organization on one shared agent mount
  the same `o:<org>` dir in two containers (review P2-b of PR #591). So each
  other handle on that dir runs one more exec past the quota before its own
  measure sets its flag. Keep the flag per mount source, as the lock is.
- **Authority:** `specs/maf_coding_engine.md` §7.1 rule 10 · PR #591 review, round 2
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
- **Check:** `grep -n "_self_mutation_permitted\|_read_first_party" apps/services/orchestrator/orchestrator/agents.py`
  → no hit means `spawn_copilot_agent` still starts the sandbox for any organization.
- **What happens.** `spawn_copilot_agent` (`orchestrator/agents.py` ~162)
  calls `mutation._run_mutation_sandbox` (~222) directly.
  `attempt_self_mutation` applies the MT-0b first-party check first
  (`mutation.py` ~390), and this path does not. So a tenant run that reaches
  `spawn_copilot_agent` can start a mutation container.
- **Do.** Call `mutation._self_mutation_permitted(org)` before any mutation
  image is built, with the org from the run binding. Refuse with the same
  reason text that MT-0b gives.
- **Test plan.** A test in `tests/unit/test_mt0b_self_mutation_containment.py`
  shows that a customer org's `spawn_copilot_agent` starts no sandbox. Prove
  it with a mutation that removes the check.
- **Authority:** root `AGENTS.md` non-negotiable 3 · `saas_multitenancy.md`
  §6.2 (MT-0b) · PR #598 review, P2
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-231 · Confirm that `metorite` is registered in production, so its exempt name is taken · [AGENT]
- **Metorite:** #686. The task holds the full entry (moved 2026-10-27, D118).
- **Check:** read-only, on the box: `SELECT name FROM dynamic_agents WHERE lower(name)='metorite';`
  → no row means the name is free, and an `agents:manage` holder could register another agent under it.
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-232 · Compare the member's organization with the run's in `_first_party_admin_runs` · [AGENT]
- **Check:** `grep -n "member_org\|membership" apps/services/orchestrator/orchestrator/executor.py`
  → no hit means the gate does not compare the two organizations.
- **What happens.** `_first_party_admin_runs` (`executor.py` ~1391) checks
  that the run's org is first-party, and that the member holds
  `admin:members:manage`. It does not check that the member's own org is the
  run's org. `resolve_access` returns no organization, and with
  `IDENTITY_CUTOVER` off it reads `app_user` by email only.
- **Do.** Resolve the member's membership in the run's org (the identity
  seam, `acb_auth.access`), and refuse when the two disagree. It is defence in
  depth: today the run's org comes from the member's own session.
- **Test plan.** A member who is an admin of a customer org, on a run bound to
  the first-party org, is refused. Prove it with a mutation.
- **Authority:** `specs/maf_coding_engine.md` §15.4 · the PR #598 verifier
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-233 · Make the refused root agent look absent everywhere · [AGENT]
- **Check:** `grep -n '"code": type(exc).__name__' apps/services/orchestrator/orchestrator/executor.py`
  → a hit means a refused stream still says `AgentNotFound`, a code that no unknown agent gives.
- **What happens.** §15.4 says "as if it did not exist". Two leaks remain:
  1. A refused stream ends with `RUN_ERROR` code `AgentNotFound`. An agent
     that truly fails to load gives `AgentLoadError`. So the code tells the
     two apart.
  2. `GET /agent` lists `metorite` to every member who may list agents. A
     customer org's owner holds `*`, so that owner sees it.
- **Do.** Emit `AgentLoadError` as the code for `AgentNotFound`. Filter the
  agents in `executor._FIRST_PARTY_ADMIN_ONLY_AGENTS` out of every registry
  list for a caller who is not a first-party admin. The 422 "Unknown agent …
  Registered: …" text names them too.
- **Authority:** `specs/maf_coding_engine.md` §15.4 · the PR #598 verifier
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-234 · Run a real Copilot CLI session, to see whether it writes outside its workspace · [AGENT]
- **Check:** `grep -c "write_out_of_workspace" <the log of a real task-manager turn on the local stack>`
  → no run on record means this is still unverified.
- **What happens.** PR #598 made `decide()` read the SDK 1.0 write target
  `file_name`. Production runs the enforcing mode, because its `.env` sets no
  permission mode. So a CLI write outside the run's workspace is now refused.
  The CLI may ask to write its own session-state files, for example under its
  home dir. If it does, the refusal can break task-manager, which is live.
- **Do.** On the local stack, leave the permission mode unset, so it reads as
  enforcing, as on production. Run one real My Tasks turn through the Copilot
  CLI, and read every `permission.decision` line. If the CLI asks for its own
  state files, allow its state dir by name in `decide()`, and add a test.
- **Authority:** `specs/maf_coding_engine.md` §7.9 · the PR #598 verifier
- **Added:** 2026-10-04 · fix round 2 of PR #598

### H-235 · Finish chat attachments: `.xlsx`, and one live read · [AGENT]
- **Check:** `grep -c '".xlsx"' packages/acb_skills/acb_skills/attachment_text.py`
  → `0` means `.xlsx` is still unread. Step 2 stays open until a live read is on record.
- **What happens.** H-229 built `read_attachment` for `.docx`, PDF, `.txt`,
  `.md` and `.csv` (`projects_ai_chat.md` §22). It opens through the safe
  opener, and under the dir lock in a covered run. Two items stay open.
  1. H-229 asked for `.xlsx` too. The supervisor narrowed the slice, so the
     tool refuses a `.xlsx` with one sentence.
  2. No live read is on record. The test plan of H-229 needs production.
- **Do.**
  1. Read `.xlsx` in `acb_skills/attachment_text.py`: the zip, the shared
     strings and each sheet, with the same caps and a test for each cap.
  2. After the deploy, upload a `.docx` in the Projects chat of the
     smoke-chat org, and ask for its contents. The answer must quote the
     file, and the gateway log must show `attachment.read`.
- **Authority:** `specs/projects_ai_chat.md` §22.7 ·
  `specs/maf_coding_engine.md` §7.9 · the H-229 slice
- **Added:** 2026-10-04 · the H-229 slice

### H-238 · Add an app-wide CSP that limits `img-src` and `script-src` · [AGENT]
- **Check:** `grep -rn "Content-Security-Policy" workbench/control_plane/next.config.ts workbench/control_plane/src/proxy.ts`
  → no hit means no app-wide CSP is set.
- **What happens.** The control plane sends no CSP header. Each renderer
  must gate a remote fetch by itself. The chat PR "a remote image in an
  agent message loads only on a click" gates the Markdown renderers. A
  renderer that a later change adds, and that forgets the gate, leaks
  again. A CSP is the second wall behind the gate.
- **Do.**
  1. List every remote host the app loads on purpose. Avatars, org logos,
     the integration logos and the OAuth images are the known ones.
  2. Set the header in `src/proxy.ts` or in `next.config.ts` `headers()`.
     Start with `Content-Security-Policy-Report-Only`, and read the reports.
  3. Enforce `img-src 'self' data: blob:` plus the listed hosts, and a
     `script-src` with no remote host.
- **Test plan.** A Playwright spec loads `/chat` and reads the response
  header. A second case renders a remote image after the click, and expects
  the browser to refuse a host that is not on the list.
- **Why not in the chat PR.** An enforced CSP can break avatars and other
  surfaces at once. It needs its own list and its own report window.
- **Authority:** `specs/projects_ai_chat.md` §14.8 ·
  `src/lib/markdownMedia.ts`
- **Added:** 2026-10-04 · the chat click-to-load PR

### H-240 · Give a dispatched agent run the member who assigned the task · [AGENT]
- **Check:** `grep -c "session_user" apps/services/gateway/gateway/routes/projects/agent_dispatch.py`
  → `0` means the gap is open. The sink passes no member to `run_agent`.
- **What happens.** `pm.task.assigned` carries the task, the new assignees
  and the org, and no assigner. So `agent_dispatch` calls `run_agent` with no
  `session_user`, and the executor logs `executor.run_has_no_acting_user`.
  A tool that calls the gateway as the member refuses. The agent can reply on
  the timeline, and it cannot comment on, move or edit the task.
- **What the spec wants.** §6.4: the agent works the task under its own
  `agent:<name>` identity, limited to the acting member through
  `EffectiveAccess.intersect()`.
- **Do.**
  1. `set_assignees` puts the signed-in member on the event, from the
     session and never from the body (R11).
  2. The sink passes that member to `run_agent` as `session_user`, never as
     a payload key.
  3. Fence it on the R8 database: the run binds the member as verified, and
     a `skill-projects` write in that run is limited to what the member may do.
- **Two more items for the flip (no fix now).**
  1. With the flag ON, no cap limits the number of dispatched runs at one
     time. Only the Router's credit cap stops them.
  2. A workflow node that assigns an agent no longer waits for the run,
     because the sink starts each run in the background.
- **Why it waits.** The run is dark (`PROJECTS_AGENT_DISPATCH`, PR #622).
  This gap must close before the owner flips the flag.
- **Authority:** `specs/project_management_app.md` §6.4 and §9.12.10 ·
  `work_plan.md` §6.1
- **Added:** 2026-10-04 · PR #622

### H-297 · Register Metorite's Zoho OAuth client, and drop its two values · [OWNER]
- **Check:** `grep -cE '^CRM_ZOHO_CLIENT_(ID|SECRET)=.+' "$HOME/.metorite/secrets/app.env"`
  on the owner's PC. A count below 2, or no file, means this is open. When it
  prints 2, an agent pushes the values and runs
  `scripts/secrets.sh status app-env`. `in-sync` closes the entry. Neither
  command prints a value.
- **Why.** Metorite owns one Zoho OAuth client, and every customer connects
  through it with one click. Only the owner can make it, because it lives in a
  Zoho account of the company. CRM-Z2 cannot run a consent without it.
- **Do this:** follow `docs/ZOHO_OAUTH_SETUP.md`. Make a server-based app.
  Turn on every data centre with one secret for all. Set the redirect URI to
  `https://app.metorite.com/api/crm/oauth/zoho/callback`. Put
  `CRM_ZOHO_CLIENT_ID` and `CRM_ZOHO_CLIENT_SECRET` in `app.env` of the drop
  folder.
- **Then the agent:** adds the two names to `allowed_keys` of `app-env` in
  `deploy/secrets/manifest.json`, and runs `scripts/secrets.sh push app-env`.
- **Authority:** `specs/crm_platform.md` §12 and §13.5 CRM-Z0 · `work_plan.md` WS-53
- **Added:** 2026-10-11 · branch `crm-z0`

### H-298 · Nine directory names are email addresses, and two paths still write more · [AGENT]
- **Check:** `SELECT count(*) FROM people WHERE name LIKE '%@%'` on production
  → above 0 means this is open. 9 of 38 on 2026-10-10.
- **Why:** the owner saw their own address where a name belongs (Projects,
  "Who has the hours"). The Projects app and its report email no longer
  print an address as a name (`src/lib/personName.ts`, `PersonName`, fenced
  by a sweep of `src/app/projects`), but the rows are still wrong. Readers
  outside that sweep still print them: the server's conflict text
  (`analytics_conflicts._label`), the chat tools and the People app.
  Three causes, all in the code:
  - The founder bootstrap (`acb_auth/access.py`, `_BOOTSTRAP_OWNER_SQL`)
    writes the address into `app_user.display_name`, which is `NOT NULL`.
  - The directory trigger (migration 206, restated in 209) copies any
    non-empty `display_name` into `people.name`, an address included.
  - The trigger only inserts (`ON CONFLICT DO NOTHING`). So a later rename
    through `PATCH /admin/members/{email}` never reaches `people.name`.
- **Do:** one migration. The trigger function treats a `display_name` that
  holds an "@" as empty, so it falls back to the address's left half, and it
  updates `people.name` while that row's name is still the member-derived one.
  Verify it on a real database (R8). Then decide the nine rows: a member or an
  admin sets the real name in People. Do not invent names.
- **Authority:** owner ask, 2026-10-10 · `src/lib/personName.ts`
- **Added:** 2026-10-10 · the person names session

### H-301 · Drop `mcp_servers` one release after IN-4 · [AGENT]
- **Check:** `grep -c "FROM mcp_servers" apps/services/gateway/gateway/connections/mcp.py`
  → a count above `0` means this is open.
- **Why:** IN-4 (WS-54) moved the org MCP servers into
  `integration_connections`, with the headers in the encrypted blob. For one
  release `mcp_servers` stays readable (R6 expand), and an old row there still
  holds its headers as plain JSONB. `connections/mcp.py` moves an old row on
  the first read of its org, and leaves the old row.
- **Do:** the contract step, after one release on production.
  1. Count the rows of `mcp_servers` on production again. On 2026-10-12 it
     held 0 rows.
  2. Remove `_LEGACY_SQL`, `_LEGACY_DELETE_SQL`, `migrate_legacy` and the
     old-row delete in `remove` from `connections/mcp.py`.
  3. Add a migration that drops `mcp_servers`. Take its number at build time,
     and check it again at merge (R1).
  4. In the same PR, remove `mcp_servers` from the `EXEMPT` list of
     `scripts/gen_tenant_migration.py` (:105).
  5. Update the suites that seed an old row: the F tests of
     `test_mcp_registry_r8.py` and the seed of
     `test_integrations_connections_r8.py`.
- **When:** one release after IN-4 merges.
- **Authority:** `specs/integrations_console.md` §7.2 (IN-4) · R6
- **Added:** 2026-10-12 · IN-4

### H-302 · Every page mounts twice on a cold load (OrgLinkGate's Suspense fallback) · [AGENT]
- **Check:** in `workbench/control_plane/e2e/shell-bar.spec.ts`, remove
  `.fixme` from "a cold load of /tasks mounts the app bar once". Run that
  test. "Expected: 1, Received: 2" means this entry is open.
- **Cause.** `OrgLinkGate` wraps every page in
  `<Suspense fallback={<>{children}</>}>`
  (`workbench/control_plane/src/components/OrgLinkGate.tsx:168`). On a cold
  load, React draws the fallback copy of the page. Then it throws that copy
  away and draws the real one. The title bar flickers, and the first copy
  loses its state and its requests.
- **Measured on a production build** (2026-10-12, `next build` and
  `next start`, a minted session, stubbed APIs, a fresh context per run).
  "Empty main" is the time from when `<main>` appears to when it gets
  content. The sidebar draws at the same moment as `<main>`.

  | Fallback | Page | CPU | Empty main | First paint to app bar | Mounts |
  |---|---|---|---|---|---|
  | page (today) | /tasks | 1× | 0 ms | 429 ms | 2, the swap 50 ms later |
  | page (today) | /projects | 1× | 0 ms | 466 ms | 1 |
  | page (today) | /tasks | 4× | 0 ms | 2582 ms | 2, the swap 620 ms later |
  | `null` | /tasks | 1× | 310 ms | 715 ms | 1 |
  | `null` | /projects | 1× | 304 ms | 736 ms | 1 |
  | `null` | /tasks | 4× | 1099 ms | 3800 ms | 1 |
  | `null` | /projects | 4× | 381 ms | 3258 ms | 1 |

- **What suspends is not known.** In production, `useSearchParams` does not
  suspend on the client. Its `use()` call runs only when `NODE_ENV` is not
  production (`next/dist/client/components/navigation.js:118`). The boundary
  still shows its fallback, so something else in the subtree suspends. The
  page's own lazy chunk is a likely suspect. The steady 300 ms at 1× matches
  the 300 ms reveal throttle of React's Suspense.
- **Refused fixes.**
  1. `fallback={null}` removes the second mount. It leaves `<main>` empty for
     300 ms and more, and the content arrives about 290 ms later than today.
     The orchestrator refused it on 2026-10-12.
  2. A param lifted into state removes the Suspense. An in-app `?org=` link
     then mounts the page as the wrong account for one commit. The review of
     2026-10-10 refused it.
- **An idea that nobody has tested.** Read `window.location.search` through
  `useSyncExternalStore`, and remove the Suspense. The gate does not render
  on the server, so the first client render sees the correct `org`. ⚠️ An
  in-app navigation to a `?org=` link fires no event that this store hears,
  so it needs its own subscription. Without one, that link skips the gate.
- **Do this:** find the suspender first, for example with the React profiler
  on a production build. Then fix it with no added blank in `<main>`, and
  remove the `fixme`.
- **Done when:** the suspender is known, a fix lands that adds no blank, and
  the cold-load test runs without `fixme`.
- **Authority:** `specs/saas_multitenancy.md` MT-1k "Org-aware links" ·
  `navigation_shell.md` §5.2
- **Added:** 2026-10-12 · shell follow-ups of #918

### H-304 · A full run of `gen_tenant_migration.py` renames 12 tables' keys and policies · [AGENT]
- **Check:** run `uv run python scripts/gen_tenant_migration.py`, then
  `git diff --stat infra/postgres/generated`. A diff means this is open. Put
  the files back with `git checkout -- infra/postgres/generated` after.
- **Why.** The generator writes the FK, index and policy names from today's
  table names. Production keeps the old names on purpose (CLAUDE.md §2):
  the `gtd_*` family, and `crm_organizations` for `crm_companies`. A full
  run renames them in phases 3 and 4. A replay then adds a second FK, index
  and permissive policy on each of the 12 tables.
  `test_crm_rename_upgrade.py::test_144_and_241_apply_again_on_the_promoted_catalog`
  fails on it. MD-2a of WS-58 found this, and added its table by hand.
- **Do this:** teach the generator the old name of each renamed table, so a
  full run on `main` is a no-op. Fence it with the Check above.
- **Until then:** add each new table to the four phase files by hand, as
  migrations 251, 255 and 258 did. Do not run the generator in full.
- **Authority:** `specs/saas_multitenancy.md` (the generated set) ·
  `tests/unit/test_tenant_coverage.py` · `specs/my_day_feed.md` MD-2a
- **Added:** 2026-10-14 · WS-58 MD-2a

### H-306 · Seal removals in the delta feed wait for a caller that reads `removed[]` · [AGENT]
- **Check:** `git grep -n "delta/tasks" -- workbench apps packages`. Look for a
  hit outside `routes/projects/` and outside the `X` row of
  `skill_projects/manifest.py`. Also look for `delta` in `MCP_ROUTES` in
  `packages/acb_auth/acb_auth/agent_oauth.py`. No new hit means the trigger
  has not come, and this entry stays.
- **The trigger.** The first caller of `/projects/delta/tasks` that reads
  `removed[]`. Today no caller exists. The web client does not call the feed.
  The manifest classes it `X` ("A chat holds no cache"). The MCP edge does not
  route it.
- **What waits.** A D63 seal sends no removal through the feed. So a cached
  client keeps a sealed tree on screen until its next full pull. PR #1010
  built seal removals, and the coordinator closed it without merge. Its code
  is on branch `sealed-tree-tombstones`, which stays.
- **Fix these before you revive #1010:**
  1. **Bound stream A2 to recent seals** with
     `sealed_at > now() - RECONCILE_AFTER` (24 h). `sealed_loss_clause` reads
     the reader's reach NOW, without the seal. A reader who gets reach after a
     seal, and then polls with `since=1970`, gets every sealed task id. That
     is a P2 id leak.
  2. **Fence the member arm** of the pre-seal reach, and fence `_without_seal`.
     The R8 suite proves the member arm only for a member with no grant.
  3. **Remove the agent branch, or state why it stays.** The MCP edge does not
     route the feed, so no agent can reach `pre_seal=True`.
  4. **Fix the shadowing in `_admin_fakes.py:768`.** The new tree-task branch
     matches `WITH tree AS`, and the seal-preview branch below it matches the
     same text.
  5. **Weigh the bump.** The bump of `updated_at` on every task in the tree
     gives a 412 to each pending If-Match edit, and it resets the stale bands.
- **Authority:** D63 (`work_plan.md` §3) · `specs/project_management_app.md`
  §11.27 · PR #1010 and its review
- **Added:** 2026-10-14 · closed PR #1010

### H-307 · Delete the orphan Mem0 memories of mailboxes that no longer exist · [OWNER]
- **Check:** `git grep -n "def reconcile_orphan" -- apps/services/gateway/gateway/routes/email`.
  No hit means no reconcile exists, and this entry stays.
- **Why.** Two kinds of Mem0 key outlive every purge of WS-17 EM-G11f2. The
  first is `default#acct:<uuid>`, which the learning job wrote before PR-G8.
  The second is `<owner>#acct:<uuid>` of a mailbox whose disconnect task
  stopped before migration 264. Mem0 stores no organization, so a read under
  one tenant cannot tell an orphan from a live mailbox of another tenant.
- **The safe design** (the PR-G8 reviewer):
  1. Bind each organization in turn with `tenant_session`, and collect its
     live `email_accounts` ids. Take the union. Account ids are uuid4, so
     they are unique across tenants.
  2. Delete a `#acct:<uuid>` key or a `default#acct:` key only when no live
     account holds that uuid.
  3. Delete a key only when its newest memory is older than the start of the
     reconcile. A mailbox connected during the run keeps its memories.
  4. Run a dry run first. It prints counts only, never a key or a memory.
- **Why OWNER.** The delete is one-way, and it spans each tenant. The owner
  reads the dry-run output and signs off before the real run.
- **Authority:** `specs/email_app_master_plan.md` §12.3.13.9, the EM-G11f2
  as-built block · PR-G8 fix round 1
- **Added:** 2026-10-14 · PR-G8 (`gmail-purge-durable`), fix round 2

### H-309 · Create the WS-59 tasks in the Metorite project "Proactive Intelligence" · [AGENT]
- **Check:** in a session with the Metorite MCP, run `search_tasks` with the
  query `RE-1`. A hit in the project "Proactive Intelligence" means the tasks
  exist, and this entry goes.
- **Why.** The owner asked for one Metorite task for each WS-59 ticket, and
  for the status to move as the work moves. The MCP had no `create_task` on
  2026-10-14, because EA-6d was not built.
- **Do this:** when `create_task` is live, create one task for each of RE-1
  to RE-10 of `specs/proactive_reach.md` §16. Use the project id
  `55c1755e-b867-43ce-afba-ae3b641a0451`, the tag "Feature", and the title
  "WS-59 RE-n: <what>". Then link each PR to its task with
  `add_task_reference`.
- **Authority:** `specs/proactive_reach.md` §16 · owner directive 2026-10-14
- **Added:** 2026-10-14 · WS-59 RE-0

### H-310 · A WhatsApp Confirm refuses a name that the `decide` engine resolved at the ask · [AGENT]
- **Check:** `rg -n "bind_run_context|set_run_context|artifact_context" apps/services/gateway/gateway/routes/whatsapp_channel/acts.py`.
  No hit means this is open.
- **Why.** At the ask, `choices.resolve_name` can ask `decide` to pick a person or a
  project for a name that is not exact. At Confirm, `acts._run` calls the tool with
  no run bound, so `typed_choices_on()` answers False and the tool refuses the
  same name. The act closes `failed`, and the member gets a refusal after the
  tap. The tool writes nothing wrong. WAC-4 had it for `create_task` assignees, and
  WAC-4b made it reachable through `waiting_on` of `set_my_overlay`.
- **Do this:** at the ask, bind the resolved ids with `card_facts`, and pass
  them to the stored call. Or bind the run context of the act's agent at
  Confirm. Then add a run test where `decide` picks "Priya Sharma" for "Priya", and a
  Confirm writes once.
- **Authority:** `specs/whatsapp_assistant_channel.md` §14.9 (review round 1, item 4)
- **Added:** 2026-10-15 · branch `wa-calendar-blocks`

### H-311 · One WhatsApp message can ask for only one change · [AGENT]
- **Check:** `rg -n "One change waits at a time" apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py`.
  A hit means this is open.
- **Why.** The owner asked for two time blocks in one message (2026-10-15). A run
  parks one act (§14.3), so the bot prepares the first block, and the member
  must ask again for the second after the Confirm.
- **Do this:** let one run park up to 3 acts of `ALLOWED_ACTS`. Send one summary
  that lists each. One Confirm runs each stored call once, in order. Each
  call gets its own rebuilt card check. A failed act must not stop the acts after
  it, and the reply names each result. Keep one row in `whatsapp_pending_acts`
  for each act, under one offer.
- **Authority:** `specs/whatsapp_assistant_channel.md` §14.9 (known limit)
- **Added:** 2026-10-15 · branch `wa-calendar-blocks`

### H-312 · Five reads of a member's settings name no tenant · [AGENT]
- **Check:** `git grep -n -A2 "WHERE user_id = :uid" -- apps/services/gateway/gateway/routes/tasks`.
  A statement that names no `organization_id` means this entry stays.
- **Why.** Migration 239 keys `user_settings` on `(organization_id, user_id)`.
  The gateway connects as `acb_app`, which does not bypass RLS, and FORCE RLS
  is on (measured on the box, 2026-10-16). So RLS keeps another org's row
  out, and the org predicate is defence in depth (R5). A read by `user_id`
  alone returns another org's row only for a role that bypasses RLS. MD-3c
  fixed `stored_zone`, `tasks.settings._load` and the work-schedule seed.
  These reads still name no tenant:
  1. `routes/tasks/settings.py` `task_models` (about :134).
  2. `routes/tasks/settings.py` `task_toggles` (about :156).
  3. `routes/tasks/calendar.py` (about :571, :608 and :1171).
- **Do this:** add `AND organization_id = {_BOUND_ORG}` to each statement, as
  `prefs.py` and `admin/me.py` do. Add one owner-role R8 case with a member in
  two orgs, modelled on `test_feed_task_kinds_r8.py::TestTheTenantUnderTheOwnerRole`.
- **Authority:** R5 · `specs/my_day_feed.md` §16 MD-3c (fix round 1)
- **Added:** 2026-10-15 · MD-3c review

### H-313 · An operator viewer can reach the tenant org purge · [AGENT]
- **Check:** `rg -c "operators/session" workbench/operator_console/src/app/api/operator/purge/route.ts`.
  No hit means this is open.
- **Why.** The org purge BFF reads only `GET /orgs` (viewer rank) before it calls
  the gateway door. The Console checks the operator's rank only at
  `POST /orgs/purge`, which runs after the tenant purge. So a viewer can destroy
  the tenant plane of an org that is already `deleted`, and then get a 403.
- **Do this:** copy the rank check of `api/operator/members/remove/route.ts`. Read
  `GET /operators/session` and `GET /operators/elevate`, and refuse anything below
  ADMIN with an open window BEFORE the tenant act. Fence it in `route.test.ts`.
- **Authority:** `specs/customer_console.md` §CP-2g (the operator matrix row for
  `/orgs/purge`)
- **Added:** 2026-10-15 · branch `member-remove`

### H-314 · A feed approvals test fails at one time of day · [AGENT]
- **Check:** run `tests/unit/test_feed_handled_r8.py::TestApprovals::test_iii_org_bs_admin_sees_no_approval_of_org_a`
  with the clock frozen at 2026-10-15 07:14 UTC. A failure means this is open.
- **Why.** On 2026-10-15 the test failed in two CI runs of PR #1038, at about
  07:14 UTC both times: `assert [] == [{'kind': 'you', ...}]`. The rerun at
  08:07 UTC passed. The PR touches no feed code. The test picks its zone with
  `_a_daytime_zone(NOW)` and puts a decision at `NOW - 2 minutes`, so a day
  boundary is the likely cause.
- **Do this:** find which zone and day start the test picks at 07:14 UTC. Make
  the zone choice keep the decision inside the member's day, and pin the
  clock in the test.
- **Authority:** R7 (a fence that fails by the clock is not a fence)
- **Added:** 2026-10-15 · branch `member-remove`

### H-315 · Submit the two Reach templates at Meta, then name them on the box · [OWNER]
- **Check:** on the box, `grep -E "^REACH_(BRIEF|NEEDS_YOU)_TEMPLATE=" /opt/acb/app/.env`
  shows `metorite_daily_brief` and `metorite_needs_you`. Then this entry goes.
- **Why.** Free text reaches a member only within 24 hours of their last
  message to the bot. On 2026-10-15 Reach dropped one brief and one interrupt
  for that reason. A template can open the window. The auto-mode classifier
  refused a template submission from the box, so the owner's PC does it.
- **Where.** On the owner's PC, signed in to Meta as the owner. Use the
  browser. The account is the test WhatsApp Business Account
  `1151567084229350` in the portfolio Hathi Labs `1586671155804254`.
- **Done 2026-10-15.** An agent submitted both templates from the owner's
  browser. Both show **In review** as Utility:
  - `metorite_daily_brief`, with the body of §8.1 and the button `Show my brief`.
  - `metorite_needs_you`, with the APPROVAL body of §8.1 and the button
    `Show me`. Meta's pre-check refused three general bodies as utility, so
    the owner narrowed it to approvals (§8.1, "Measured at submission").
- **Do this:**
  1. Open `https://business.facebook.com/wa/manage/message-templates/?waba_id=1151567084229350`.
     Wait until both show **Active** or **Approved**. If Meta rejects one or
     moves it to **Marketing**, do not use it. Tell an agent.
  2. Set `REACH_BRIEF_TEMPLATE=metorite_daily_brief` on the box after the
     RE-2a code is live. An agent adds the line to `/opt/acb/app/.env` and
     restarts `acb-gateway`.
  3. Set `REACH_NEEDS_YOU_TEMPLATE=metorite_needs_you` only after H-317 is
     live. Before that, RE-2a sends the approval words for every interrupt.
- **Do not** use `metorite_task_reminder` or `metorite_meeting_reminder`.
  They are approved, but their words do not fit, and they have no button.
- **Authority:** `specs/proactive_reach.md` §8.1 and §8.1a
- **Added:** 2026-10-15 · WS-59 RE-2a

### H-323 · A DSML stream shows a first tag that a delta cut before its `>` · [AGENT]
- **Check:** in `tests/unit/test_router_dsml_tool_calls.py`, compare
  `_events(TASK_TOOLS, deltas)` with `_events(TASK_TOOLS, [whole])` for the
  three cases in `specs/customer_console.md` §6A.15, "The known gap". A
  difference in any case means this is open.
- **Why.** `_TAG_HEAD_RE` refuses a `/` or a `'` before the `>`. When a delta
  ends there, `_not_a_tag` releases the region as text. A whole feed never
  takes that path. In all three cases, the stream shows raw markup to the
  member. In case C, the stream runs no call, but a whole feed runs the call.
  No case runs MORE calls, so the gap fails closed. The independent review of
  PR #1047 found it.
- **Do this:** let `_TAG_HEAD_RE` accept what `_FIRST_TAG_RE` accepts before
  the `>`. Or test the head with `_FIRST_TAG_RE` when the `>` arrives. Add the
  three cases to `_REPLIES`, so that the every-cut test fences them. Keep the
  linear fences.
- **Authority:** `specs/customer_console.md` §6A.15 (contract items 1 and 4)
- **Added:** 2026-10-15 · PR #1047

### H-316 · The member writers of assignee rows take the row and the key in different orders · [AGENT]
- **Check:** `git grep -n -e "ids = dedupe_ids(payload.task_ids)" -e "await _move_satellites(db, str(source.id), task_id)" -- apps/services/gateway/gateway/routes/projects/bulk.py apps/services/gateway/gateway/routes/projects/merge.py`. The first anchor is the unsorted selection of the bulk edit. The second is the merge, which moves the assignee keys before `update_row` of the target. Two hits mean this entry stays. With fewer hits, read the changed code, and delete this entry when the orders agree.
- **Why.** The reviewer and the verifier of WS-55 EA-6b found these orders already disagree on main. A PUT of the assignees writes the key and then bumps the row. A move with assignees writes the row and the destination counter, then the key. A merge moves the assignee and watcher keys, then writes the target row. A bulk edit locks rows in request order. So a PUT against a move that assigns the same member, a merge against a PUT on the watcher keys, and two bulk edits with the same ids in another order can each deadlock. A merge against a PUT of the same member also fails with a unique violation of `pm_task_assignees_pkey` (10 times in 10).
- **Do.** Sort the ids of every bulk write. Give every member writer one helper that writes the assignee key, then the row. Fix the merge's `NOT EXISTS` race. Prove each pair with a looped R8 race test. The claim (`claim_task`) already writes the key before the row, so it needs no change.
- **Authority:** R8 · `specs/external_agent_access.md` §17 (EA-6b fix round 3)
- **Added:** 2026-10-15 · EA-6b fix round 3 review (taken as H-313, renumbered to H-316 at merge because main took H-313 to H-315)

### H-317 · Reach must send the needs-you template for an approval only · [AGENT]
- **Check:** `git grep -n "needs_you_template" -- apps/services/gateway/gateway/reach/loop.py`.
  Read the branch that sends the template. No test of the `approval` kind
  beside it means this is open.
- **Why.** Meta accepted `metorite_needs_you` as utility only with the words
  "your approval is pending in Metorite". RE-2a (PR #1042, as of
  2026-10-15) sends it for every interrupt in a closed window, which
  includes `needs_reply` and `overdue` cards. Those would arrive with the
  wrong words.
- **Do this:** in `reach/loop.py`, use the template only when every
  candidate card has `kind == "approval"`. Hold every other interrupt in a
  closed window, as when no template is set. Add a test for each path in
  `tests/unit/test_reach_loop.py`. `needs_you_params` keeps two parameters.
- **Authority:** `specs/proactive_reach.md` §8.1 and §8.1a
- **Added:** 2026-10-15 · H-315 template submission

### H-318 · A preview app's jobs reach the tier-2 coordinator · [AGENT]
- **Check:** `rg -n "launch" apps/services/gateway/gateway/routes/shell/intent.py`.
  No hit inside `held_jobs` means this is open.
- **Why.** `navigation_shell.md` §5.2 rule 8 says a `preview` app may carry a
  manifest and nothing renders it. The bar's `buildItems` honours that,
  because `visibleSections` drops a preview pane. The gateway's `held_jobs`
  filters a job by feature only, and every member holds `feature:*` since
  migration 207. So a job of a preview app would be offered to every member
  before the owner promotes the app. Found by the WS-61 KB-0 audit on
  2026-10-15, and it is why KB-0 carries no job.
- **Do this:** either `held_jobs` reads the launch status of the job's app
  from one list the gateway owns, or rule 8 changes to "a preview app
  carries no job". Pin it in `tests/unit/test_shell_intent.py`.
- **Authority:** `specs/navigation_shell.md` §5.2 rule 8 and §5.3
- **Added:** 2026-10-15 · WS-61 KB-0 audit

### H-319 · Two spellings of the subject grammar: a bare email and `user:<email>` · [AGENT]
- **Check:** `rg -n "user:" infra/postgres/268_artifacts.sql packages/acb_auth/acb_auth/permissions.py apps/services/gateway/gateway/routes/projects/core.py`.
  A `user:` prefix in the artifacts CHECK and a bare email in
  `validate_grant_subject` means this is open.
- **Why.** `pm_project_grants` and `validate_grant_subject`
  (`routes/projects/core.py:3383`) take a bare email as a subject. AM-1
  (`268_artifacts.sql`, 2026-10-15) takes `user:<email>` and adds
  `project:<uuid>`. CLAUDE.md §4 names one subject-grammar validator as a
  seam. `knowledge_base.md` §5.1 (WS-61 KB-1) must use the shared one, so
  the spelling has to be decided before KB-1 and AM-3 both write grants.
- **Do this:** KB-1 settled the spelling on 2026-10-15: the bare email of
  Projects, with the validator moved to `acb_auth/permissions.py`
  (`knowledge_base.md` §5.1). AM-3 calls that validator, or records why
  Artifacts keeps `user:`. A migration that widens the artifacts CHECK is
  expand-only (R6).
- **Authority:** CLAUDE.md §4 (respect the seams), `tenancy_and_visibility.md` §3
- **Added:** 2026-10-15 · WS-61 KB-0 audit

### H-328 · The workflow run lifecycle outside the agent tools names no tenant (H4 slice 6c) · [AGENT]
- **Check:** `git grep -n "await _get_db()" -- apps/services/gateway/gateway/routes/workflows/`.
  Each hit is an unbound read or write on a FORCE RLS table. This entry is
  open until the only hits left are tenant-discovery reads.
- **Why.** PR #1052 binds the F13 agent path: the list, the run, the run
  rows, an approval resume and the status read. The rest of the lifecycle
  still opens `get_db()` with no tenant. In production FORCE RLS gives that
  read zero rows, and that write fails.
- **The unbound sites, as of 2026-10-15:**
  - `service.py` `_get_module_code`. A module node reads `workflow_modules` and fails.
  - `service.py` `_pm_task_updater`. A Projects node writes `pm_tasks` unbound.
  - `service.py` `_run_session` with no org. It serves the manual Run, the schedule, the webhook and the event runs.
  - `service.py` `record_skipped_run`, `reconcile_orphaned_runs` and `evaluate_automation_health`.
  - `service.py` `_workflow_billing_member`. It is a tenant-discovery read (H-152), so it stays unbound.
  - `scheduler.py` `_scan_once` and `scan_due_waits`. Because of this, an agent cannot start a workflow with a long wait yet.
  - `triggers.py` (the event dispatch) and `hooks.py` (the webhook route).
  - `runs.py` calls `start_run` with no org (the manual Run button).
- **Do this:** take the org from a stored fact, never from the ambient
  context: the workflow row for a trigger, the run row for the sweeps. Then
  pass it to `start_run` and `resume_run`. Enumerate the RLS-exempt
  `organization` table for the scans, as `crm_sync/scheduler.py` does. Then
  remove the long-wait refusal in `run_published_workflow`
  (`_long_wait_nodes`), and lower `H2_BASELINE_ELSEWHERE`.
- **Authority:** `specs/workflows_app.md` F13 · `routes/workflows/service.py`
  header note · R5
- **Added:** 2026-10-15 · PR #1052 review round 1

### H-320 · An unticked checkbox has no visible edge · [AGENT]
- **Check:** `rg -n "border-border" workbench/control_plane/src/components/ui/Checkbox.tsx`.
  A hit in the `BASE` string means the defect is open.
- **Why.** The visual review of CRM-Q16 (2026-10-15) measured the unticked box
  of the shared `Checkbox` primitive. Its edge is about 1.2:1 against the
  background, in dark mode and in light mode. WCAG 1.4.11 asks 3:1 for the edge of a control.
  The Sync health card is the first screen where the one unticked box carries
  the meaning (a held module), so it showed there first. Every checkbox in the
  product has the same edge.
- **Do this:** give the unticked box a stronger border in the primitive.
  `border-muted-foreground/60` or an input-border token from `globals.css`
  will do. Then run the conformance suite and look at one checkbox in
  both colour modes. Do not fix it in the card.
- **Authority:** `workbench/control_plane/DESIGN_SYSTEM.md` (one look, one primitive)
- **Added:** 2026-10-15 · WS-53 CRM-Q16

### H-321 · A generic module held for a long time keeps its deleted rows · [AGENT]
- **Check:** `rg -n "def _module_reread" apps/services/gateway/gateway/crm_sync/engine.py`.
  If that function still runs a full re-read only for a module that hides
  `Modified_Time`, this entry is open.
- **Why.** A generic module (`module:<ApiName>`, CRM-Q6a) is never walked. Its
  deletes come from the deleted list only, and Zoho keeps that list for 60
  days. A module that CRM-Q16 holds for longer than that, and is then
  released, keeps every row that Zoho deleted in the gap. The core entities
  get a full compare after such a gap (`plan()`). Generic modules do not.
- **Do this:** when a release follows a gap longer than `GAP_RECONCILE`,
  re-read the module in full, with a stamp. Then drop the rows that the
  re-read did not see. Or show the gap on the Sync health card, and offer a
  start over of that one module.
- **Authority:** `crm_platform.md` §18.7 (the generic store) and §18.8
- **Added:** 2026-10-15 · WS-53 CRM-Q16 review

### H-322 · An artifact on a Knowledge page must count as kept · [AGENT]
- **Check:** `rg -n "kb_page_files" apps/services/gateway packages/acb_memory infra/postgres`.
  No hit in the artifact sweep's `keep` rule means this is open.
- **Why.** WS-61 KB-2a makes a page file an artifact, joined through
  `kb_page_files` with no foreign key. The artifact sweep
  (`artifact_management.md` §7.4) deletes a registry row that is not kept.
  A file on a published page would then vanish from the page with no record.
- **Do this:** in the WS-60 `keep` rule (§7.1), count an artifact as kept
  while a `kb_page_files` row names it. Add a test beside the sweep's own.
  It must land before KB-2a turns on for any org.
- **Authority:** `specs/artifact_management.md` §7.1, `specs/knowledge_base.md` §4.3
- **Added:** 2026-10-15 · WS-61 KB-1 audit

### H-325 · Three calls on the CRM mapping design · [OWNER]
- **Check:** read `crm_platform.md` §18.9.1, "Open, with a recommendation".
  If it still lists three open calls, this entry is open.
- **Why.** The v4 design (§18.9) needs three owner answers before CRM-Q20
  ships its rules. The design page "Source Mapping Studio" v4 shows each one.
- **Do this:** answer each call in chat.
  1. May a choice that changes only the view apply with no approval?
     Recommended: yes, with Undo.
  2. May Metorite ask for the Zoho scope that reads list views? Each
     connected org must reconnect once. Recommended: new connections only.
  3. May one org's approved choices help another org? Recommended: no.
- **Authority:** `crm_platform.md` §18.9.1
- **Added:** 2026-10-16 · WS-53 §18.9 spec

### H-326 · A Zoho module count is higher than the records the sync keeps · [AGENT]
- **Check:** on prod, compare `crm_sync_runs.estimate` with the rows in
  `crm_module_records` for DEWiN (`f01d029f-…`). If Payment_Made still holds
  3,003 rows against an estimate of 4,947, this entry is open.
- **Why.** The run of 2026-10-15 20:24 marked Payment_Made done with 3,003 of
  4,947 records. Products kept 9 of 13, and Operator_Associates 871 of 873.
  Outstanding (3,623) matched. **Cause found 2026-10-16** (§18.10.1). The
  connected Zoho user cannot see the other records, and Zoho's count ignores
  that. A probe ruled out approval and a v2 or v8 difference.
- **Do this:** build CRM-Q24, so the Sync health card shows the short read.
  The owner asks DEWiN to reconnect with a Zoho admin user. That may also
  open their fields, layouts and pipelines to discovery.
- **Authority:** `crm_platform.md` §18.7 (the generic store) and §18.9.2
- **Added:** 2026-10-16 · WS-53, DEWiN import

### H-327 · A Zoho task on a custom-module record is skipped · [AGENT]
- **Check:** on prod, read the counts of DEWiN's last run. If `tasks` shows
  `unlinked` equal to `seen` (1,145 on 2026-10-15), this entry is open.
- **Why.** DEWiN keeps its work in custom modules. A Zoho task whose parent
  (`What_Id`) is a custom-module record finds no core parent, so the sync
  skips it. The member never sees those tasks. The 18,674 held activities
  will likely do the same when they sync.
- **Do this:** build CRM-Q23a, then CRM-Q23b and CRM-Q23c (§18.10). Keep
  DEWiN's Activities held until they ship. Notes are hit too: the import
  linked 1 of 3,848. The notes already read come back only through the
  one-time full read of CRM-Q23b item 5. DEWiN's 1,145 tasks have no parent,
  and they stay out (owner, 2026-10-16).
- **Authority:** `crm_platform.md` §18.10
- **Added:** 2026-10-16 · WS-53, DEWiN import

### H-329 · The email embedder sends a key that the gateway refuses · [AGENT]
- **Check:** `rg -n "llm_api_key" apps/services/email_ingestion/email_ingestion/email_embeddings.py`.
  A hit in the request headers means this is open.
- **Why.** `email_embeddings.py:80-112` posts to `{litellm_base_url}/v1/embeddings`
  with `Settings.llm_api_key`. On production `litellm_base_url` is the gateway
  itself, and its `/v1/embeddings` route is not public. So the app-wide
  `require_authenticated` refuses the `/v1` key once `GATEWAY_INTERNAL_TOKEN`
  is set (measured 2026-10-16). So every email
  embedding would fail, and `email_embeddings` holds 0 rows. It stays hidden
  while `EMAIL_SEMANTIC_SEARCH_ENABLED` is off. WS-61 KB-3 met the same
  defect and sends the service token from `gateway/kb_index/embed.py`.
- **Do this:** pick one fix for both callers. Either the route takes
  `require_llm_api_auth`, as the other `/v1` doors do, or each platform
  caller sends the service token. Prove it with one embedding on the box
  before the email flag turns on.
- **Authority:** `specs/knowledge_base.md` §6.4, `specs/email_app_master_plan.md`
- **Added:** 2026-10-16 · WS-61 KB-3

### H-331 · `test_fifteen_for_each_source` fails in the full unit run, and passes alone · [AGENT]
- **Check:** in a CI log of the whole `tests/unit` run, search for
  `test_feed_handled.py::TestTheCap::test_fifteen_for_each_source FAILED`.
  A hit in a run whose diff touches no My Day file means this is open.
- **Why.** On 2026-10-16 the test failed with `assert 0 == 15` in the unit job
  of PR #1074, which changed no Python file (run 38842018493, first try). The
  rerun passed. Alone, the suite passes 52 of 52 three times in a row. The
  test's clock is fixed (`NOW` in `test_shell_needs.py`), so an earlier test
  in the same process most likely leaves a patched read, a cache or a clock
  that `handled_run` then sees. A flaky required check costs every PR an
  hour.
- **Do this:** run `uv run pytest tests/unit -k "feed or my_day or shell" -p
  no:randomly` in file order until it fails, then bisect the files before
  it. Fix the leak with a fixture that restores what it patches, not with a
  retry.
- **Authority:** `specs/my_day_feed.md` (WS-58), `CLAUDE.md` §3 rule 8
- **Added:** 2026-10-16 · WS-61 KB-2a CI

### H-332 · With two panes open, the chat column on `/chat` is too narrow for a card · [AGENT]
- **Check:** open `/chat` at a width of 1440 px. Open the Documents pane and
  the Files pane. Measure the message column. A width under 400 px means
  this is open.
- **Why.** The review of the report slices (B2, B3) saw the column fall to
  about 128 to 160 px with both panes open. So the column crushes every card
  in it, the report card too. The layout is in `src/app/chat/page.tsx`, near the
  `flex-1 min-w-0` row at line 1258. No test holds a minimum width for the
  column.
- **Do this:** give the message column a minimum width. When the two panes
  do not fit beside it, close one pane or show it over the column. Add a
  test that measures the column at 1440 px with both panes open. Then look
  at the result in light mode, at compact density and under a changed
  accent.
- **Authority:** `specs/projects_ai_chat.md` §14.9 and §14.10,
  `workbench/control_plane/DESIGN_SYSTEM.md`
- **Added:** 2026-10-16 · WS-27bm §14.10.9 B3 review

### H-334 · The email owner check names no tenant by default · [AGENT]
- **Check:** `git grep -n "this_tenant: bool = False" -- apps/services/gateway/gateway/routes/email/core.py`.
  A hit means this entry stays.
- **Why.** The gateway connects as `acb_app`. That role is not a superuser,
  has no BYPASSRLS and does not own the tables, and FORCE RLS is on (measured
  on the box, 2026-10-16). So RLS keeps another org's rows out, and a missing
  org predicate is defence in depth (R5), not a live leak. It leaks only if
  a path reads with a role that bypasses RLS. The gap is wide. The default of
  `email/core.py` `_assert_account_owner` (about :870) is
  `this_tenant=False`, and about 62 callers use it, some of them writes. An
  example is `create_rule` (`automation/rules.py` :637). Only MD-3d's two
  wrappers pass `this_tenant=True`.
- **Do this:**
  1. Read each caller of `_assert_account_owner`. Then make `this_tenant`
     default to True, and pass False only where a reason is written.
  2. Name the tenant in the list reads: `transport/accounts.py`
     `feed_mailboxes` (about :361) and `list_accounts` (about :302), and
     `automation/identity.py` `resolve_self_addresses` (about :395).
  3. Add one R8 case as a role that bypasses RLS, with one mailbox in two
     orgs, modelled on
     `test_feed_mail_kinds_r8.py::TestTheTenantUnderTheOwnerRole`.
- **Authority:** R5 · `specs/my_day_feed.md` §16 MD-3d (what the build found)
- **Added:** 2026-10-15 · MD-3d build · rewritten 2026-10-16, MD-3d review

### H-336 · W10: the chat's unmount save can copy mail into Mem0, so WS-7 keeps Mem0 off · [AGENT]
- **Check:** run the two commands. Either one closes this entry.
  1. `git grep -c "run_holds_mail" -- apps/services/gateway/gateway/routes/memory.py`
     gives a count: the save route asks the mail predicate.
  2. `git grep -n "saveConversationOnUnmount" -- workbench/control_plane/src ":!*.test.ts" ":!*.test.tsx"`
     gives no hit: the unmount save is gone.
  While both fail, this is open. ⚠️ While it is open, WS-7 must not turn
  Mem0 on for any organization. The WS-7 row of `work_plan.md` names it.
- **Why.** `AgentChat` posts the turns of the default agent to
  `/api/chat/memories` when its panel closes (`lib/chatMemorySave.ts`). The
  BFF route calls the gateway's `POST /memory/{scope}/add`, and that route
  calls the Mem0 client with the text of its caller (`routes/memory.py`).
  An orchestrator turn that delegated to the email assistant can quote a
  mail. No mailbox purge reaches the member key. PR-G8b (EM-G11f3) closed
  W1 to W8 and left this writer, because the route has no session that the
  server bound, so it cannot read the mail mark. It is latent while Mem0 is
  off on production.
- **Do this:** give the save a session that the server binds, and refuse the
  write when `acb_skills.egress.run_holds_mail` holds for it. Or remove the
  unmount save, because the gateway already extracts each turn at the run
  end. Never take the session from the request body. Add a test to
  `tests/unit/test_mail_copies_in_chat_stores.py`.
- **Authority:** `specs/email_app_master_plan.md` §12.3.13.9.1 (W10) · PR-G8b review, fix round 1
- **Added:** 2026-10-16 · PR-G8b (`gmail-chat-copies`), fix round 1

### H-337 · A chat body can rebind the memory id in a shared room · [AGENT] · security
- **Check:** `rg -n "user_email|user_id" apps/services/gateway/gateway/routes/agent.py`
  near the call to `run_agent_stream` (~:3080). No strip of those two keys
  from `req.payload` means this is open.
- **Why.** In a shared room the chat route binds the memory id to
  `room:<thread>`, so the Knowledge tools and the index block refuse there.
  The route passes the client body on to `run_agent_stream`, and
  `executor.py:2969` binds `_payload_user(payload)` over that scope. A member
  who sends `payload.user_email` set to their own address rebinds the run
  to themselves, and the room then sees that member's own space names and
  pages. It reveals only the sender's own data, and it predates WS-61. Found
  by the KB-4a re-review on 2026-10-16.
- **Do this:** in the chat route, drop `user_email` and `user_id` from
  `req.payload` before the call, because the session already names the
  member. Add a test that a room run with those keys keeps the `room:` id.
- **Authority:** `docs/multiplayer/memory-clearance.md`, `specs/knowledge_base.md` §6.5
- **Added:** 2026-10-16 · WS-61 KB-4a review

### H-352 · Email work holds a pool connection across provider and model calls · [AGENT] · finding
- **Check:** in `apps/services/gateway/gateway/routes/email/automation/runner.py`,
  find "Block W" in `_run_rules_job`. If `_apply_matches` still runs inside
  that `async with _tenant_session()`, this is open. Also check
  `_ensure_subscription` in `transport/sync.py`, and the hydrate in
  `get_message` in `transport/messages.py`.
- **Why.** Production logged a 503 from `QueuePool limit of size 7 overflow 2
  reached` during sync. The gateway pool holds 9 connections. Block W of the
  rules job holds one for each new mail across the Graph actions, the AI
  draft of a REPLY rule and `_reconcile_thread_labels`. Its own comment says
  "EM-T4 owns the model and provider I/O that stays inside". The subscription
  renew holds one across three Graph calls. The `get_message` hydrate and the
  `update_message` and `delete_message` pushes hold one across a provider
  call. A few mailboxes that sync at once take the pool. The phases of
  `email_ingestion.scheduler._sync_account` are short, and so is the sweep.
  The route of the full body held one too. PR `email-route-errors` fixed it.
- **Do this:** move the provider and model calls of Block W out of the block,
  as the sweep does (provider first, then a short write block). Do the same
  for `_ensure_subscription`. Do not change the pool size.
- **Authority:** `specs/engineering_practice.md` §1.1, the EM-T4 notes in `runner.py`
- **Added:** 2026-10-18 · email route errors (gateway log, 7 days)

### H-353 · 🔴 The CRM overlay read applies no member visibility · [AGENT] · finding
- **Check:** in `apps/services/gateway/gateway/routes/crm/aggregate.py`, read
  `_read_overlay` and `overlay_where`. If the overlay query filters by the
  `owner` parameter only, and applies none of the visibility that
  `list_query` applies to a member, this is open.
- **Why.** #1122 (CRM-Q20-3a) reads the module of an approved overlay in
  place of a core entity. The review of #1126 found that this path checks
  the tenant (FORCE RLS) and the `owner` filter, but not the visibility of
  each record to the member. So a member can get totals over records that
  the normal CRM path hides from them. No flag gates the path. It goes live
  for an org when that org's overlay plan is approved. On 2026-10-18 no org
  had one, because the DEWiN sync is paused.
- **Do this:** before the first overlay plan is approved, give the overlay
  query the same member visibility as `list_query`, grouped and ungrouped.
  Add an R8 test where a member who cannot see a record gets a total
  without it.
- **Authority:** `specs/crm_platform.md` §18.13.5, R5
- **Added:** 2026-10-18 · review of #1126

### H-354 · A refusal looks wrong in two chat places · [AGENT] · finding
- **Check:** (1) In `ProjectToolCards.tsx`, if `ProjectEvidenceBody` and
  `InfoCard` still branch only on `status === "error"`, a refused Projects
  read is red. (2) In `acb_common/tool_refusals.py`, if `is_refusal` reads
  the result of every tool, this is open.
- **Why.** Since #1126, a result that starts with `Refused:` streams as
  `success: false`. A refused Projects WRITE shows a muted "Not done". A
  refused READ or VIEW (`find_tasks` 404, `render_board`) shows red text and
  the "Next:" line written for the model. Also, `fetch_page` and
  `recall_notes` return raw text. A page or a note file whose text starts
  with "Refused:" marks its step failed. Only the step label changes.
- **Do this:** (1) show a refused read or view with the muted "Not done"
  look, and hide the "Next:" line from the member. (2) run `is_refusal` only
  for a tool that carries `WRAPPED_ATTR`.
- **Authority:** `specs/projects_agent_parity.md` §3.4
- **Added:** 2026-10-18 · review of #1126

### H-355 · Email request bodies still pass a raw account id to SQL · [AGENT] · finding
- **Check:** grep the request models under
  `apps/services/gateway/gateway/routes/email/` for an `account_id` field.
  If a route passes that field to SQL without `resolve_account_filter`,
  this is open.
- **Why.** #1128 sends every `account_id` in a query or a path through
  `resolve_account_filter`. About 35 request models carry `account_id` in
  the body, for example `POST /email/rules/run`, and still pass the raw
  value. A value that is not a UUID there gives a 500. Two more items stay
  from #1128. The attachment download still gives a 500 for a provider
  fault. A revoked token now shows as a 503 "try again", because both
  `authenticate()` methods return False for a revoked token and for a
  throttled probe alike.
- **Do this:** read each body `account_id` through `resolve_account_filter`,
  and extend its route-walk fence to the body models. Make `authenticate()`
  tell a revoked token (409, reconnect) from a throttled probe (429 or 503).
- **Authority:** `specs/email_app_master_plan.md`, R7
- **Added:** 2026-10-18 · PR #1128 and its review

### H-367 · No R8 test drives a connection with no slug through branch 0 · [AGENT]
- **Check:** `grep -n "slugless\|no_slug" tests/unit/test_agent_client_stamp.py`.
  When an R8 test there signs a call for a DCR connection row and gets 200,
  delete this entry.
- **Why.** For a row whose client id gives no slug, the stamp is
  `mcp:<host>`. The edge and branch 0 must both compute that same value. Until
  H-356 the legacy arm hid a drift between them. Now a drift gives every DCR
  client a 401, and no R8 test would catch it. N3 does not touch the database,
  and the edge-test world uses a client id that gives a slug.
- **Do this:** add an R8 test in `test_agent_client_stamp.py`. Use a DCR
  connection row, sign through the real edge client, and check that the call
  gets 200. Name the mutation (branch 0 computes the stamp with
  `client_id=None` dropped, or with a forced slug) and show it goes red.
- **Authority:** `specs/external_agent_access.md` §10.1 step 5, R7
- **Added:** 2026-10-20 · PR #1184 review, P3

### H-409 · Harden EA-14a: a task that closes, and two small gaps · [AGENT]
- **Check:** `grep -n "organization_id" apps/services/gateway/gateway/routes/projects/filters.py`
  near `unheld_clause`. When the clause, `_HOLDER_SQL` and `_RUN_SELECT` all
  carry the org predicate, item 3 is done. Read items 1 and 2 against the code.
- **Why.** The review of PR #1192 found no blocker. It left four items. The
  landing of #1192 fixed a fifth, the reopen race (two prompts at once gave
  a 500), with `test_x13b`.
- **Do this:**
  1. A task that goes to done, cancelled or archived keeps its open run until
     the lease ends. End the run on that status change, or let a closed task
     not hold. **SW-10a (spec §12.6, step 2) covers the run side:** the sweep
     closes a run of a closed task once its lease ended, while `agent_run_sweep_enabled` is
     on. The task side stays open here: an end of the run at the moment the
     task closes, with the flag off too.
  2. The Release button uses `access.is_admin`. The server uses
     `admin:members:read`. Make them the same rule, and ask whether a read
     permission should release another member's run.
  3. Add `organization_id` to `_HOLDER_SQL`, `_RUN_SELECT` and
     `unheld_clause` (R5). Add a cross-org test for `held_by`. Decide whether
     a released or abandoned run counts as "agent worked" in `agent_worked.py`.
  4. **A delete ignores the hold, and it can deadlock** (review of the landing,
     2026-10-25, P2). `agent_delete_task` (`routes/projects/tasks.py`, about
     L1475) reads no run. Another session can delete a task that a live run
     holds, and the cascade removes the run. The delete also locks the task
     row first and the runs second, through the cascade. A holder's activity
     takes the run first and the task row second. The two can deadlock, and
     that was on main before EA-14a. Fix both doors (`DELETE` and
     `agent-delete`): lock the task's open runs `FOR UPDATE` before
     `lock_task`, and answer 409 `task_held` when another member or client
     holds a fresh run. Or write in §12.3 that a delete overrides a hold.
- **Authority:** `specs/external_agent_access.md` §12.3
- **Added:** 2026-10-20 · PR #1192 review, P2 and P3

### H-357 · 🔴 A key that a chat saw must be rotated, and old rows scrubbed · [OWNER] · security
- **Check:** ask each org whose chats set up a key, a GitHub token first.
  Did it rotate that key after 2026-10-18? Then count the rows that still
  hold a value, on production and on staging. In `chat_message`, test
  `content`, `reasoning`, `tool_events`, `progress_lines`, `agent_state` and
  `custom_events` for `<<<SETUP:` and for `ghp_`, `gho_`, `github_pat_` and
  `ghs_`. Test `chat_session.last_preview` and `chat_session.title` too. Do
  the same for `maf_agent_session.session_json` and `agent_run.trace`. If
  every key is rotated and every count is 0, delete this entry.
- **Why.** Before the merge of WS-54 Batch A, three stores kept the model's
  `<<<SETUP:service:KEY=value>>>` marker as raw text. They are the native
  session, the chat transcript and the run trace. Fix round 2
  removes the marker on every write, and changes no row that exists. ⚠️ A
  member who pastes a key as a plain chat message is stored as typed. The
  text reaches `chat_message` (also the user rows), the run input, and
  memory: Mem0 and the Graphiti `add_episode`. Nothing detects that. So
  **rotation is the real remedy**, and the scrub only limits the copies.
- **Do this:**
  1. Ask each org to rotate each key that a chat saw.
  2. Take a backup.
  3. For each table, read each row that holds a value. Edit the text in
     code: run `acb_common.setup_markers.redact_tree` on the decoded JSON of
     each JSON column, and remove a pasted token by its exact value. Cast
     the result back to `jsonb`, and check that it parses before the update.
     ⚠️ Never run an in-place `regexp_replace` on a `::text` cast of a JSON
     column. A pattern can eat a closing quote and break the row.
  4. Check the user rows of `chat_message` too, not only the agent rows.
  5. Search Mem0 and the Graphiti episodes of each such org for the same
     values, and delete each memory or episode that holds one.
  6. A production write needs the `deploy` grant and the backup evidence
     (CLAUDE.md §3a rule 1).
- **Authority:** `specs/github_integration.md` §7.6
- **Added:** 2026-10-18 · WS-54 Batch A fix round 2, item 5. Widened in fix round 3.

### H-359 · Three staging file races that the acb-staging-flag review left · [AGENT] · security
- **Check:** in `scripts/staging_refresh.sh`, the write of `$file.tmp` runs as
  `acb` (through `setpriv` or `runuser`), and `acb-staging-flag` checks the pin
  file before it writes `.env`. If both hold, delete this entry.
- **Why.** The security review of H-358 on 2026-10-18 approved the script and
  named three left-overs. First, `staging_refresh.sh:165-167` writes
  `$file.tmp` as root inside `/opt/acb/app`, which `acb` can write. That is the
  same symlink race that the script fixed. Second, when `.staging-flags` is a
  symlink, `acb-staging-flag` writes `.env`, then refuses on the pin file. So
  the flag changes with no log line, no restart and no pin, and the next
  refresh reverts it. Third, the sudoers line lets `dev` flip
  `enforcement-flip` switches after the dev-phase grant ends on 2026-11-30.
- **Do this:** run the refresh's file work as `acb`, as `as_owner` does in
  the script. In the script, read and check the pin file before the `.env`
  write. Say in the README that the sudoers line outlives the grant, so the
  owner decides on 2026-11-30 whether it stays.
- **Authority:** `deploy/hostinger/staging/README.md`, HANDOFF H-358
- **Added:** 2026-10-18 · H-358 security review

### H-370 · Set the date the repo takes AGPL-3.0, as ported Docmost code ships · [OWNER]
- **Check:** the repo root holds a `LICENSE` file that names AGPL-3.0, or the
  owner has written the date in `work_plan.md` D107. If either holds, delete
  this entry.
- **Why.** On 2026-10-19 the owner said the product will release under AGPL
  and that builders may port Docmost core code (D107). AGPL-3.0 section 13
  gives each user of a network service the right to the Corresponding Source
  of the modified AGPL code. That duty starts with the first release that
  carries a ported file. The repo has no `LICENSE` today.
- **Decide.** The date of the licence change. On 2026-10-19 the owner ruled
  that ported code may merge to staging, and that production waits for the
  owner's test (D107 part 5). The licence date still binds the production
  release. Ask counsel about the build tooling and the parts of the repo
  that join the ported code.
- **⚠️ Now urgent (2026-10-22).** Production serves ported Docmost files
  since `44ade7130` (wave 1, #1210) and `1429df4a9` (#1217). The agent
  shipped them on the owner's word to deploy, before this date was set. Two
  ways out: set the date now (add `LICENSE` and the source offer), or revert
  the ported files from production. `THIRD_PARTY_NOTICES.md` lists them.
- **Authority:** owner, 2026-10-19. `specs/knowledge_base.md` §21.1
- **Added:** 2026-10-19

### H-360 · Widen acb-staging-flag: NEXT_PUBLIC_ rebuilds, a production mode, and an unset that lasts · [AGENT]
- **Check:** `deploy/hostinger/staging/acb-staging-flag` (or its successor) can set a
  `NEXT_PUBLIC_*` flag on staging and start a rebuild, and can set a flag on
  production with `--production --reason`. If both hold, delete this entry.
- **Why.** On 2026-10-18 the owner asked that the dev box set flags on staging
  and, when a task needs it, on production, and deploy to production. #1145's
  tool covers nine named staging flags and nothing else. Today a production
  flag is a hand edit over `ssh metorite`, which the box's auto-mode rules
  allow (backup first, one flag line). A tool is safer than a hand edit.
- **Reuse.** Closed PR #1146 (branch `acb-flag`) holds a design that passed two
  review rounds and eight mutations: the switch-name patterns, a forced
  re-apply in `acb-staging.slice` that waits for the deploy lock, a
  `--production --reason` mode that refuses outbound switches, an absent
  `DEPLOY_ENV` read as production (as `vps_pull.sh` reads it), and an `unset`
  record that the nightly refresh honours. Port those into the existing tool.
  Do not add a second tool.
- **Reinstall for `CONTEXT_RECENCY` (2026-10-18).** WS-61 CX-1 added
  `CONTEXT_RECENCY` to `ALLOWED_KEYS` in the repo copy. The installed copy is
  root's, so it still refuses that key. Copy the repo file over
  `/usr/local/sbin/acb-staging-flag` as root, then run
  `sudo -n /usr/local/sbin/acb-staging-flag --list` as `dev`.
- **Already done (2026-10-18).** #1145's tool is installed: `/usr/local/sbin/acb-staging-flag`
  and `/etc/sudoers.d/acb-staging-flag` (checked with `visudo`). Knowledge is on
  in staging (`KNOWLEDGE_ENABLED=true`, `KNOWLEDGE_ORGS=*`). The box's
  `~/.claude/settings.json` holds the owner-authorized `autoMode` rules.
- **Authority:** owner, 2026-10-18. `specs/staging_and_daily_release.md`, CLAUDE.md §3a
- **Added:** 2026-10-18

### H-364 · Harden the CRM consistency batch of §18.16 · [AGENT] · security
- **Check:** each item below has its own test or review record, or a later
  entry owns it. If all hold, delete this entry.
- **Why.** The owner asked to ship §18.16 fast on 2026-10-19, and to
  harden it later. No adversarial review and no mutation test ran.
- **Do this:**
  1. Run a mutation round on `crm_sync/terms.py`, `crm_tools._term_total`
     and the early park of `executor.wait_user_future`.
  2. Review `GET /crm/terms/resolve`. It writes the asked term on a GET.
     Bound the count of asked terms of one org.
  3. Review `aggregate._seed_aliases`. It writes the alias fact on a GET.
     Bound the count of alias sets, and let an admin remove a set.
  4. Check that a figure of a WhatsApp option comes only from the tool
     (`text_asks.figures_of` takes a shape, not a source).
  5. Check the late pick of a term card across a parked run.
  6. Give a batch run off `CRM_ANSWER_FIRST_ORGS` a holder, so the read
     budget covers it.
  7. Add an org zone, and read it in `acb_common.today`.
- **Authority:** `specs/crm_platform.md` §18.16.10, owner 2026-10-19
- **Added:** 2026-10-19

### H-368 · A host shell can still reach a GitHub token outside the clone · [OWNER] · security
- **Check:** on the box, run
  `grep -cE '^(GITHUB_READ_TOKEN|COPILOT_GITHUB_TOKEN)=.' /opt/acb/app/.env`.
  A count below 2 means steps 1 and 2 are open.
- **Why.** H-363 took the token out of each clone's `.git/config`, and out of
  the env of every agent child. Three paths stay. They are not in the clone,
  so H-363 could not close them.
  1. A read falls back to `GITHUB_TOKEN`, which has `repo` scope, when
     `GITHUB_READ_TOKEN` is not set. The read runs in a gateway temp dir,
     but the token in that one process can push.
  2. The Copilot CLI holds `COPILOT_SDK_AUTH_TOKEN`. The SDK reads
     `COPILOT_GITHUB_TOKEN` first, then `GITHUB_TOKEN`. So with no
     `COPILOT_GITHUB_TOKEN`, a shell under the CLI can read the `repo` token
     (the BH-1 residual in `specs/box_hardening.md` §5).
  3. A host shell runs as `acb`. It can read `/opt/acb/app/.env` and
     `/proc/<gateway pid>/environ`. H-271, H-225 and BH-5 own that path.
- **Do this:**
  1. [OWNER] Make a fine-grained token with read access to the agent and
     skill repos only. An agent adds `GITHUB_READ_TOKEN` to `allowed_keys`
     of the `app-env` entry in `deploy/secrets/manifest.json`, and pushes it
     with `scripts/secrets.sh`.
  2. [OWNER] Make a token with the Copilot permission only, and push it as
     `COPILOT_GITHUB_TOKEN` in the same way.
  3. After 1 and 2, the gateway uses `GITHUB_TOKEN` for a gated push only
     (`acb_common/git_credentials.py`, rule 5). Path 3 still reaches it, so
     this entry stays open until H-271 or BH-5 closes that path.
- **A reviewer's push can carry a commit of a concurrent run.** The scenario:
  a reviewer's turn starts on a shared clone at HEAD `A`. During the turn, a
  second run of the same agent commits `B` in the same clone. The reviewer's
  turn commits `C` and asks for a push. At the end of the turn, the push step
  sees `A..C`, which holds `B` and `C`, and it pushes both. So `B` goes out
  with no reviewer. The push step cannot tell the two runs apart. The
  post-commit hook writes only `git rev-parse HEAD` to `.git/cc-commits-queue`,
  with no run id, and any shell of any run can write that file. To close it,
  tag each commit with its run, from a source that a run cannot forge (for
  example, the gateway makes the commit, or a per-run work tree), and refuse
  any commit in the window that does not carry this run's tag. A partial
  fix that reads the queue file would trust a file that a run can write.
- **The mutation container is closed.** It gets `COPILOT_GITHUB_TOKEN` only,
  by name, and never `GITHUB_TOKEN` (`mutation._copilot_only_token`). With no
  Copilot-only token and no `GATEWAY_API_KEY`, it does not start.
- **Authority:** H-363 · `specs/box_hardening.md` §5 BH-1 · H-271
- **Added:** 2026-10-20 · H-363

### H-372 · Move file bytes out of Postgres and off the box's disk (MT-1g) · [AGENT] · data
- **Check:** `git grep -l "object_store" origin/main -- packages/acb_common`
  prints a file, and `git grep -n "BYTEA" origin/main -- infra/postgres/*.sql`
  shows `agent_blob.content` dropped or unused. If both hold, delete this entry.
- **Why:** the owner asked on 2026-10-20 that every place Metorite stores file
  bytes in Postgres, or keeps the only copy on the box's disk, be written down
  to pick up later. `saas_multitenancy.md` MT-1g holds the inventory of that
  day. Task files, meeting audio, project imports and Custom App binaries exist
  only on one disk, and two of them are outside the off-box backup.
- **Do this:** build the seam `acb_common.object_store` on the Supabase
  Storage S3 endpoint that the backup already uses. Then move class B, then
  `agent_blob`, as the MT-1g inventory orders them. Each move adds a nullable
  key column, reads the store first and the old place second, and backfills.
- **Decided (D108, 2026-10-20):** Supabase Storage, one bucket for each
  environment, and the app signs in as its own Auth user (the BH-4 pattern).
  MT-1g now lists the slices MT-1g-1 to MT-1g-6.
- **Authority:** `specs/saas_multitenancy.md` MT-1g · `specs/backup_and_restore.md`
- **Added:** 2026-10-20 · artifacts session

### H-373 · Try the email chat fixes of #1201, #1202 and #1203 on real mail · [OWNER]
- **Check:** an agent cannot run this. It cannot sign in as the owner. The
  entry stays open until the owner says in a session that the three tries
  below worked, or names the one that failed.
- **Why:** the three PRs reached production on 2026-10-21 with green tests
  and an Opus review each. No person has tried them on real mail.
- **Do this (owner):** in the chat, try these:
  - Agree a reply, and check that the card shows that text.
  - While the card is open, type "make it shorter", and check that a new card
    shows the shorter text.
  - Reply to a mail that has a PDF, and check that the draft uses the PDF.
  - Say "snooze this till tomorrow 9am", and say "add Priya to the cc of my
    draft".
- **Authority:** `email_app_master_plan.md` §18 · PRs #1201, #1202, #1203
- **Added:** 2026-10-21 · email chat collaboration session

### H-376 · Turn on images in reply drafts when a tier reads images · [OWNER]
- **Check:** an agent cannot read the box's settings without the `secrets`
  grant. The owner confirms that `EMAIL_DRAFT_READS_IMAGES` is on in the
  gateway's environment, and that the AI Router serves the email calls.
- **Why:** `draft_files.images_on()` sends the images of a mail to the model
  only when the switch is on AND the Router serves the call with
  `task: "vision"`. Until then, the card names each image that the draft did
  not read.
- **Authority:** `acb_common/settings.py` `email_draft_reads_images` ·
  `gateway/routes/email/automation/draft_files.py`
- **Added:** 2026-10-21 · email chat collaboration session

### H-379 · Turn on send later: staging first, then production · [OWNER]
- **Check:** an agent cannot read the box's settings without the `secrets`
  grant. The owner confirms that `EMAIL_SEND_LATER` is `true` in the gateway's
  environment on production. Until then, the chat says that send later is
  not on.
- **Why:** #1209 built send later dark (H-375, `email_app_master_plan.md` §19).
  It sends mail at a later time, so a person turns it on and watches the
  first send.
- **Do this (owner):** set `EMAIL_SEND_LATER=true` on staging and restart the
  gateway. In the chat, schedule a draft for two minutes ahead, and check
  Sent. Then do the same on production.
- **Authority:** `email_app_master_plan.md` §19 · `acb_common/settings.py`
  `email_send_later`
- **Added:** 2026-10-21 · email chat collaboration session

### H-381 · A chat plan cannot make subprojects or repeat rules (WS-46 P8, G19, G20) · [AGENT]
- **Check:** `uv run python -m evals.projects_ops.run --scripted`. PO-3 reads
  `xfail` while P8 and P9 are open.
- **Why:** `propose_plan` writes one node, and `capture_intake` and
  `create_personal_task` take no repeat rule.
- **Do this:** build slice P8 of `projects_agent_parity.md` §12.
- **Authority:** `projects_agent_parity.md` §3.3 G19 and G20, §12 P8
- **Added:** 2026-10-23 · Projects chat parity session

### H-383 · The Projects chat has no agent-run tools (WS-55 EA-12e, G23) · [AGENT]
- **Check:** `grep -n "EA-12e" apps/skills/skill-projects/skill_projects/manifest.py`.
  A hit in `PLANNED` means the four tools are not built.
- **Why:** an outside agent can start, report and finish a run on a task over
  MCP. The in-product chat cannot.
- **Do this:** build `agent_runs`, `start_agent_run`, `post_agent_activity` and
  `finish_agent_run` as chat tools. Clear their `PLANNED` and `FIELD_PLANNED`
  rows. `approve_task_archive` stays out: only the member answers it.
- **Authority:** `external_agent_access.md` §12.1 · `projects_agent_parity.md` §3.3 G23
- **Added:** 2026-10-23 · Projects chat parity session

### H-384 · Run the Projects eval with the live model, and add tasks for delete and show_view · [OWNER]
- **Check:** `ls evals/projects_ops/results/`. No run made with `--tier` means
  the sweep has never run. `grep -n "show_view\|delete_task" evals/projects_ops/tasks.py`
  shows whether the new tasks exist.
- **Why:** the scripted run proves the tools. Only the model sweep proves that
  the model picks them. The spec makes the sweep on the production Router
  owner-gated, because it costs money.
- **Do this (agent):** add eval tasks for "delete this task" (one card, one
  `DELETE`), "remove it" (asks archive or delete) and "show overdue by owner"
  (one `show_view`, no write). **Do this (owner):** choose the tier, then run
  `uv run python -m evals.projects_ops.run --tier <chosen> --repeat 3`.
- **Authority:** `evals/projects_ops/README.md` · `projects_agent_parity.md` §11
- **Added:** 2026-10-23 · Projects chat parity session

### H-385 · Decide which excluded Projects acts move into the chat · [OWNER]
- **Check:** `uv run python -c "import skill_projects.manifest as m; print(sum(r.cls=='X' for r in m.MANIFEST))"`
  from `apps/skills/skill-projects`. It printed 38 on 2026-10-22.
- **Why:** the owner wants the chat to do all that the screens do. Some acts
  are left out of the chat by a past decision. Each one needs the owner's
  answer before an agent builds it.
- **Do this (owner):** answer yes or no for each. Grant writes (D-PM-40).
  Report recipients and schedule (§9.12.8). The nudge (Q3). The CSV export.
  The file import. The day planner (Calendar's). Your own areas in My Tasks:
  create, rename, delete. Bulk delete.
- **Authority:** `skill_projects/manifest.py` class X rows · `projects_agent_parity.md` §3.3
- **Added:** 2026-10-23 · Projects chat parity session

### H-386 · Try the three Projects chat changes of 2026-10-22 in a browser · [OWNER]
- **Check:** the owner confirms each item below on `app.metorite.com`. An agent
  cannot sign in with Google or Microsoft.
- **Why:** #1218, #1223 and #1234 shipped with green tests, but nobody looked
  at them on a real page.
- **Do this (owner):** (1) archive a project. It leaves the tree and shows
  under Archived at the foot of the rail. (2) Ask the chat to delete a task. An
  approval card shows, and Approve deletes it. (3) Ask "show overdue tasks
  grouped by owner on the board". The page switches.
- **Authority:** PRs #1218, #1223 and #1234
- **Added:** 2026-10-23 · Projects chat parity session

### H-387 · Check that the Projects tools run from the assistant outside /projects · [AGENT]
- **Check:** nobody has measured it. Delete this entry when the answer is
  recorded in `projects_agent_parity.md`.
- **Why:** the one assistant sits in the top bar of every app (D99). A member
  on `/tasks` or `/calendar` may not reach the Projects tools, and
  `show_view` only acts on an open Projects page.
- **Do this:** find which agent the top-bar assistant runs on each page, and
  whether it holds the `skill_projects` tools. Record the answer, and open a
  slice for any gap.
- **Authority:** D99 in `work_plan.md` §3 · `projects_agent_parity.md` §1
- **Added:** 2026-10-23 · Projects chat parity session

### H-390 · Email reads answer 503 when the gateway's database pool is full · [AGENT]
- **Check:** `ssh metorite "sudo journalctl -u acb-gateway --since -24h -o cat
  | grep -c 'QueuePool limit'"` prints 0. If it does for a week, delete this
  entry.
- **Why:** on 2026-10-22 the logs held `QueuePool limit of size 7 overflow 2
  reached`. `query_inbox` and `get_account_overview` answered 503 to a
  member. Sync, rule, classify and embed jobs share the same pool
  (`packages/acb_common/acb_common/db.py` near line 153).
- **Do this:** measure which jobs hold the connections. Then raise the pool
  size, or cap the jobs' concurrency. Check the Postgres connection limit
  first.
- **Built (2026-10-24, branch `email-single-mailbox-b`):** the pooler has no
  spare slot, so there is no second pool. A task marked background (the
  mailbox sync loop, and each post-sync job through `as_mailbox_owner`) holds
  at most `DB_BACKGROUND_MAX` (default 4) of the 9 connections. Members keep
  5. Fence: `tests/unit/test_db_background_lane.py`. Not measured on
  production. Keep this entry until the Check holds for a week.
- **Authority:** `engineering_practice.md` §1
- **Added:** 2026-10-23 · email chat session

### H-391 · An email write over WhatsApp says "ask in the app" · [AGENT]
- **Check:** in a WhatsApp chat, ask the assistant to archive one mail. If it
  does so after a confirm, delete this entry.
- **Why:** WhatsApp had no card channel, so each email write answered
  `_NO_CARD_CHANNEL`. The branch `email-whatsapp-confirm` builds the reply
  code flow (`whatsapp_assistant_channel.md` §18). It is dark behind
  `WHATSAPP_ASSISTANT_WRITES`.
- **Do this:** after the merge, check that migration 298 is in the ledger.
  Then the owner turns on `WHATSAPP_ASSISTANT_WRITES` for one org (WAC-9).
  Run the Check from a linked phone, and reply with the code.
- **Authority:** `whatsapp_assistant_channel.md` §18 ·
  `email_app_master_plan.md` §21.13 · the owner rule "a channel changes
  delivery, not function"
- **Added:** 2026-10-23 · email chat session

- **Added:** 2026-10-23 · email chat session

### H-396 · Try the chat fixes of 2026-10-22 on a phone · [OWNER]
- **Check:** the owner says each item below works. Then delete this entry.
- **Why:** these shipped without a phone test:
  - #1222: the reply card stays above the message box, and the status line
    shows once.
  - #1228: the chat stays open while a run works in an app.
  - #1226: one draft for each email in Drafts.
  - #1229: Cc and Bcc in the chat.
  - #1237: send a saved draft from any chat.
- **Do this (owner):** use the assistant on a phone for a real reply with a
  Cc. Report each step that leaves the chat.
- **Added:** 2026-10-23 · email chat session

### H-397 · Turn on AI proposals in Knowledge once the Review view is live · [OWNER]
- **Check:** `ssh metorite 'sudo grep -c "^KNOWLEDGE_PROPOSALS=true" /opt/acb/app/.env'` prints 1, and so does the staging `.env`. If both print 1, delete this entry.
- **Why:** KB-5a (#1236) and KB-5b (the Review view) let the assistant file a page or an edit as a proposal, and a person accepts it. The tools and `POST /knowledge/proposals` stay dark behind `KNOWLEDGE_PROPOSALS`, default off.
- **Do this:** when KB-5b is merged, turn the flag on in staging first, with `sudo acb-staging-flag` or the staging `.env`. Test a proposal in the chat. Then accept it in Knowledge → Review. Production waits for the owner's word.
- **Authority:** owner, 2026-10-22. `specs/knowledge_base.md` §8.1 (F1, F2), §13
- **Added:** 2026-10-23 · Knowledge session

### H-398 · Turn on knowledge gaps and the kb_gap My Day card · [OWNER]
- **Check:** `KNOWLEDGE_GAPS` names the org and `MY_DAY_PROVIDERS` holds `knowledge` in the production `.env`. If both hold, delete this entry.
- **Why:** KB-9a (branch `kb-gaps`, migration 293) writes a `kb_gaps` row when the assistant's company search finds no Knowledge page. A question that two members ask within 30 days shows a "Write a page" card to a Knowledge manager. It is dark by default.
- **Do this:** after KB-9a merges, set both on staging and check the Gaps view in Knowledge. Production waits for the owner's word.
- **Authority:** `specs/knowledge_base.md` §8.4 door 3, §9, §13
- **Added:** 2026-10-23 · Knowledge session

### H-399 · Build the next Knowledge AI slices of §22 · [AGENT]
- **Check:** `grep -nE "KB-AI-2|KB-AI-3|KB-AI-4|KB-AI-6" project-docs/specs/knowledge_base.md`. Delete this entry when each row reads BUILT.
- **Why:** §22 of `knowledge_base.md` is the AI roadmap of 2026-10-22. It ranks the slices by value and names the tier of each.
- **Do this, in order:**
  1. KB-AI-2: "Tidy with AI" on `tier-fast`. It files the result as a KB-5a edit proposal, never a direct write.
  2. KB-AI-3: "Draft with AI" from a gap, on `tier-balanced`, as a proposal.
  3. KB-AI-4: verified sources (owner, review interval, demote or flag an expired page in AI answers).
  4. CX-3 page digests (`KNOWLEDGE_DISTILL`), then KB-AI-6 rerank and "Ask this space".
  5. KB-10 `kb:read` on MCP (KB-4b is built).
  Each paid pass has its own flag, default off, which the owner turns on.
- **Authority:** owner request, 2026-10-22. `specs/knowledge_base.md` §22
- **Added:** 2026-10-23 · Knowledge session

### H-400 · Finish the Notion and Confluence parity of wave 2 · [AGENT]
- **Check:** the wave 2 table of `knowledge_base.md` §21.7 marks KB-2j, KB-2o and KB-2c BUILT. If all three are BUILT, delete this entry.
- **Why:** KB-2h, KB-2k, KB-3d and KB-2m are built. Three rows remain.
- **Do this:** KB-2j (math, mermaid, image resize and captions, allowlisted embeds), then KB-2o (labels). KB-2c (files) waits for Artifacts AM-5 and H-322. The `kb_links` row for a person mention (KB-2k1) waits for a reader that needs it.
- **Authority:** `specs/knowledge_base.md` §21.7
- **Added:** 2026-10-23 · Knowledge session

### H-401 · Run the hardening pass of the Knowledge build · [AGENT]
- **Check:** `grep -c "^| K-" project-docs/hardening_backlog.md` prints 0. If it prints 0, delete this entry.
- **Why:** many WS-61 slices landed in build mode with the verifier only (engineering_practice.md §7a). The backlog holds K-1 to K-8.
- **Do this:** open one `harden` PR. Run `diff-reviewer` on the whole wave 2 diff. Add the fences that K-4 to K-8 name. Run the visual review. Then close each K row.
- **Authority:** owner directive, 2026-10-19. `specs/engineering_practice.md` §7a
- **Added:** 2026-10-23 · Knowledge session

### H-403 · Check that the last two Knowledge PRs of 2026-10-23 merged and reached staging · [AGENT]
- **Check:** `gh pr view 1241 --json state` reads MERGED, and `curl -s https://api.dev.metorite.com/version` shows a SHA at or after its merge. If both hold, delete this entry.
- **Why:** the session closed while PR #1241 (KB-9a, migration 293) waited on CI with auto-merge on. PR #1240 (KB-5b) had merged.
- **Do this:** if #1241 is still open, read its failed checks, fix forward, and merge it. Re-check that migration 293 is free on main first (R1). Then confirm staging serves it and the staging log shows `293_kb_gaps.sql ... ok`.
- **Authority:** `specs/knowledge_base.md` §17 KB-9a
- **Added:** 2026-10-23 · Knowledge session

### H-407 · Mem0 memory reads a member's private facts into an org-visible room of one · [AGENT] · security
- **Check:** `grep -n "shared=_room_is_shared\|_clearance.write if _room_is_shared" apps/services/gateway/gateway/routes/agent.py` prints nothing, because the memory pre-read and the extraction use `not room_is_solo(room)` from `gateway/rooms.py`. If it holds, delete this entry.
- **Why:** `_room_is_shared` is `room.is_shared`, which counts participants only. A room of one with visibility `org` or `people` is readable by other members, but it reads the owner's private Mem0 facts and writes its turns to the owner's store. Mem0 is off in production, so nothing leaks today. Found by the WS-63 PZ-2 review on 2026-10-24. PZ-2 fixed the same rule for the profile block with `room_is_solo`.
- **Do this:** before anyone turns Mem0 on (task #250, WS-7), pass `shared=not room_is_solo(room)` to `resolve_clearance` and `memory_preread`, and use it for `_extract_user`. Add a route test for an org-visible room of one, after the shape of `tests/unit/test_profile_block_room_route.py`.
- **Authority:** `docs/multiplayer/memory-clearance.md` · `specs/personalization.md` §5.1
- **Added:** 2026-10-24 · WS-63 PZ-2 repair round


### H-418 · Finish PR #1400, the chat wave 2 of WS-64 · [AGENT]
- **Check:** `gh pr view 1400 --json state -q .state` prints OPEN. If it prints MERGED, check that staging and production serve its SHA, then delete this entry.
- **Why:** the build is done and pushed on branch `ws64-chat-wave2`. The verifier and the reviewer were stopped before they reported. CI failed one job: the PR body has no `Visual review:` line.
- **Do this:** run `ws-verifier` and `diff-reviewer` on the branch. Fix what they find. Take the four captures of the new `move_projects` card (light, compact, accent, 390 px). Add the `Visual review:` line to the PR body and push a commit. Merge. Wait for staging to serve it. Then release to production.
- **Decision to confirm:** a member's re-parent that names no project now moves the task into the parent's project (`carry_to_parent`). Clarify "file under" needs this. The owner may choose a plain refusal.
- **Authority:** `specs/one_projects_app.md` §9 "Chat wave 2" · owner word, 2026-10-27, "go next"
- **Added:** 2026-10-27 · WS-64 session

### H-419 · Merge PR #1399, the manifest decisions for the OP-11 routes · [AGENT]
- **Check:** `gh pr view 1399 --json state -q .state` prints OPEN.
- **Why:** four parity tests are red on main. They are `test_projects_field_parity.py` (two) and `test_projects_ui_actions.py` (two). The PR adds the three missing manifest rows and passes all 350 parity tests. These tests warn and do not block.
- **Do this:** wait for CI. Merge. Run `uv run pytest tests/unit/test_projects_field_parity.py tests/unit/test_projects_ui_actions.py -q` on main to see green.
- **Authority:** `engineering_practice.md` §7a
- **Added:** 2026-10-27 · WS-64 session

### H-420 · Build wave 3: task ids with a space prefix, such as OPS-447 · [AGENT]
- **Check:** `git log origin/main --oneline -i --grep "prefixed task id"` prints nothing.
- **Why:** a task number is unique inside one space only. On 2026-10-27 Fracktal held 3091 tasks and 1305 distinct numbers. A move gives the task a new number. So a bare "#447" can name two tasks, and an old mention stops pointing at its task.
- **Do this:** give each space an automatic prefix that an admin can edit (`pm_projects.task_prefix` exists and is empty). Show `OPS-447` everywhere. Keep the old number as an alias after a move. Make the chat and the MCP accept the prefixed id. When a bare number matches several tasks, make them ask. The migration must be expand-only (R6). Take a backup first.
- **Authority:** owner word, 2026-10-27, "go ahead with your recommendations" · `project_management_app.md`
- **Added:** 2026-10-27 · WS-64 session

### H-421 · Finish the My tasks leftovers of OP-11 · [AGENT]
- **Check:** `git grep -n "MY_TASKS_MAX_ROWS" origin/main -- workbench/control_plane/src/app/projects/lib/myTasks.ts` prints a line. The 2000-row cap still exists.
- **Why:** the UI half is live (`6c0f5a8`). These parts were left out on purpose.
- **Do this:** build the Timeline tab. Add the disposition, context, energy and defer facets and columns. Add the bulk selection bar. Add the project chip to Calendar pills. Add a server `disposition=` filter on `/projects/tasks?scope=mine`. Put its rule in SQL, equal to `fold_dispositions` in `mine.py`, and cover it with an R8 test. Then the chips page on the server and the cap goes. `/my/inbox` takes 1.5 to 2.3 s at 5000 tasks. Add keyset paging and an index on `lower(member_email)`. The phone View sheet clips the Calendar tab.
- **Authority:** `specs/one_projects_app.md` OP-11 slice table, "Not built in U"
- **Added:** 2026-10-27 · WS-64 session

### H-422 · OP-5: retire the old Tasks code and merge the backend · [AGENT]
- **Check:** `test -d apps/services/gateway/gateway/routes/tasks && echo PENDING`
- **Why:** the Tasks rows are merged into `pm_tasks`. The old Tasks backend, the old store and two empty tables remain. The nav flip was on 2026-10-25, so this may start.
- **Do this:** move the `/tasks` routes (`ai`, `attachments`, `calendar`, `capture_email`, `people`, `capability`, `planning`, `settings`) to `/projects/my/*` and `/people/*`. Delete the second day planner. Retire `skill-my-tasks` into `skill-projects`. Drop `my_tasks_horizons` and `my_tasks_reviews` in a migration. Drop `task_accounts` when email capture reads the email accounts. Remove the mock fallback in `taskStore.ts`. Delete `app/tasks/page.tsx` and `TaskToolbar`. Keep `InboxView` and Clarify. A tasks-only member still needs the old page until their grants change. Back up before the migration.
- **Authority:** `specs/one_projects_app.md` OP-5 · owner default of 2026-10-27, drop the unused tables
- **Added:** 2026-10-27 · WS-64 session

### H-423 · Decide how a space summary counts what a member cannot see · [OWNER]
- **Check:** read `fold_node_summary` in `apps/services/gateway/gateway/routes/projects/tree.py`. If its child walk has no `project_clause`, this is pending.
- **Why:** a space dashboard can name a sibling project the member cannot open. Filtering the children breaks `own + children == tasks`.
- **Recommend:** filter the children by visibility. Count the hidden tasks under `own`, as `GET /projects/my/summary` does. Then build it.
- **Authority:** the audit of 2026-10-26, finding P1-8 · `specs/one_projects_app.md` OP-11
- **Added:** 2026-10-27 · WS-64 session

### H-424 · Two loose ends of the chat timeout fix · [AGENT]
- **Check:** `git grep -n "run_task = asyncio.create_task" origin/main -- apps/services/orchestrator` prints `executor.py`. The Tier-2 fallback still starts a task that nothing cancels.
- **Why:** on 2026-10-26 a chat run hung for 10 minutes and saved an empty reply. Wave 2 saves a notice. The Next route (`route.ts`) can still persist empty content over it, because the gateway upsert writes `content = EXCLUDED.content` with no condition. The Tier-2 task may outlive its parent.
- **Do this:** make the upsert refuse an empty write over a non-empty message. Cancel the Tier-2 task when its generator closes. Add a test for each.
- **Authority:** the incident of 2026-10-26 · `specs/one_projects_app.md` §9
- **Added:** 2026-10-27 · WS-64 session

### H-425 · Move six Fracktal tasks out of the space Company Operations · [OWNER]
- **Check:** ask the chat in Fracktal to list the tasks of the space Company Operations. Six tasks (#447 to #452) mean it is pending. An admin may read `pm_tasks` through the ap-south-1 pooler instead.
- **Why:** the chat filed them on the space on 2026-10-26. The server now refuses new ones. The owner approved the move. My Metorite connection reaches the Hathi Labs organisation and not Fracktal, so an agent could not do it.
- **Do this:** the owner asks the chat to move them, or uses Move. Suggested: #447 to Operations, #448 and #451 to Founders Office, #449 and #450 to Finance & Accounting, #452 to HR.
- **Authority:** owner approval, 2026-10-26
- **Added:** 2026-10-27 · WS-64 session

### H-426 · Decide how a repeating task behaves when it leaves Done · [OWNER]
- **Check:** Metorite task #49 is not Done.
- **Why:** closing a repeating task makes the next copy. Reopening the old task leaves both, and the old one keeps its old date.
- **Recommend:** give the reopened task the new date and delete the copy, but only when nobody has touched the copy. Otherwise keep both and link them.
- **Authority:** the audit of 2026-10-25
- **Added:** 2026-10-27 · WS-64 session

### H-427 · Find the cause of the duplicate board names, and look at the chat task table · [AGENT]
- **Check:** Metorite tasks #48 and #50 are not Done.
- **Why:** nothing stops two sibling projects from sharing a name. The report of #48 has no example. #50 is fixed in code and only needs a look in the browser.
- **Do this:** ask the member for a real example of #48. It may come from an import or a folder conversion. A server refusal of a duplicate sibling name is small. Look at #50 in the chat and in the Projects rail.
- **Authority:** the audit of 2026-10-25
- **Added:** 2026-10-27 · WS-64 session

### H-428 · Pin the other CI jobs to ubuntu-24.04, and look at one flaky browser test · [AGENT]
- **Check:** `git grep -n "ubuntu-latest" origin/main -- .github/workflows` prints lines.
- **Why:** `ubuntu-latest` became Ubuntu 26.04 and Playwright cannot install Chromium there. PR #1382 pinned `pr-check.yml` only. `deploy.yml` and the other workflows still use `ubuntu-latest`. `shell-bar.spec.ts` "the shell bar is the same on every app" fails with the My work flag on, and it failed on main.
- **Do this:** pin every workflow, or unpin when Playwright supports the image. Find why the shell-bar test fails with the flag on.
- **Authority:** PR #1382
- **Added:** 2026-10-27 · WS-64 session

### H-429 · Small gaps that wave 2 left · [AGENT]
- **Check:** `git grep -n "no-offset\|without an offset" origin/main -- apps/services/gateway/gateway/routes/projects/due.py` prints nothing.
- **Why:** a due time with no zone offset, such as `2026-10-31T17:00`, is read as UTC. A subtask that moves to a new project keeps its parent in the old one, and neither the app nor the chat checks it. `move_projects` reads the tree once for each row. The `fold_node_summary` leak is H-423.
- **Do this:** read a no-offset time in the member's zone. Refuse a subtask move that splits it from its parent, or carry the parent. Read the tree once in `move_projects`.
- **Authority:** the wave 2 report, 2026-10-27
- **Added:** 2026-10-27 · WS-64 session
