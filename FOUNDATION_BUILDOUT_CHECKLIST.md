# Foundation Build‑Out Checklist — Metorite

**Date:** 2026-07-11 · **Deploy status updated:** 2026-07-13 · **Competitive refs added:** 2026-07-13
**§BO‑20 rewritten and verified against code: 2026-08-02** (WS‑4 audit remediation). Verified this pass: the ingestion package contents (no `worker.py`), the ClickUp → `event_hooks.emit_event` → `workflows.triggers.dispatch_event` → `start_run` fan-out, the Gmail/Zoho `TODO` stubs, the repo-wide absence of `xreadgroup`/`xgroup`/`xack`, the four checked-in systemd units, the already-provisioned Redis compose service, `uv.lock`'s lack of any job-queue library, and the gateway lifespan's supervised loops (five wired, **all five** stopped on shutdown, four actually started in the default config — WhatsApp enrichment is flag-gated off). §BO‑20 now carries acceptance criteria, verification commands, gate labels, and one named owner decision (§BO‑20.0). **That decision was answered on 2026-08-02 — `BO‑20 = Option A (in‑process)`** — so nothing in §BO‑20 is blocked on a decision any more. **BO‑20f and BO‑20a are BUILT (both 2026-08-02) and BO‑20b slice 1 (`emit_event` strict mode) 2026-08-03, moving §BO‑20 ☐ → ◑; BO‑20b slice 2 and BO‑20c–e are open and dispatchable.** ⚠️ **Slice 1 is necessary but NOT sufficient** (adversarial review 2026-08-03): the only sink production registers, `workflows.triggers.dispatch_event`, swallows every exception, so `raise_on_error=True` is a no‑op on the real registry — **slice 2's scope grew** to include a matching strict path in `dispatch_event`, and the §BO‑20 non‑goal "Not a change to `dispatch_event`" is struck and qualified. Two claims in the stamp above were made false by BO‑20a and are corrected in place below: `xreadgroup`/`xgroup`/`xack` now exist (in `ingestion/consumer.py` only), and the lifespan's supervised loops are **six** wired / **all six** stopped on shutdown (how many actually *start* is data‑dependent — see §BO‑20 "What is true today" §7, which counts it honestly; the new ingestion consumer joins WhatsApp enrichment as flag‑gated **off**, so it starts nowhere today). `INGESTION_CONSUMER` is registered in `work_plan.md` §6. **Other sections carry no such stamp** — BO‑1/BO‑19 were stamped by the 2026-08-01 doc-truth pass; the rest are as-authored.
**§BO‑10 / §BO‑13 / §BO‑14 / §BO‑15 / §BO‑19 re-measured and corrected, §BO‑23 added, and the "can we go app by app?" verdict block added: 2026-08-03** (WS‑0 truth pass, second commit on PR #344). Verified this pass by measuring, not by reading the prior doc: the **12** `create_async_engine` call sites across 10 modules and the fact that the 8 cached singletons are never disposed (BO‑10 said "three+"); `executor.py` at **5,010** lines and `run_agent_stream` at **~1,942** (BO‑13 claimed 4,069 / ~1,600 — both July numbers the file has since grown past); `permission_policy.decide`'s two hard‑veto return paths and `install_dependency`'s `destructive: True` annotation (BO‑14 claimed the gate "can never deny" and the registry was "empty" — **both false**); `model_limits.py` as the retired‑five‑sources context‑window SoT versus `_TIER_DEFAULTS` + three still‑on‑disk config files (BO‑15 is **half** closed, not closed and not untouched); the root `AGENTS.md`'s deleted version table and `infra/AGENTS.md`'s corrected proxy/Langfuse lines (BO‑19 → ✅); `dump_schema.sh`'s `--schema-only`, `apply_migrations.sh`'s 140‑file `ON_ERROR_STOP=1` replay, the absence of any WAL/pgbackrest/wal‑g config, and `deploy/hostinger/README.md:115` (→ **BO‑23**); and, live against GitHub, `branches/main/protection` → 404 with `rulesets` → `[]`. **Two audit claims handed to this pass were wrong and are not transcribed:** BO‑14/BO‑15 were described as fully closed (BO‑14's defects are closed but its *residual* is real and different; BO‑15 is half). Tenancy/visibility architecture from the same day lives in `project-docs/specs/tenancy_and_visibility.md`, not here. *(Amended 2026-08-09: the tenancy half of that architecture now lives in `project-docs/specs/saas_multitenancy.md` — D15, 2026-08-08; visibility stays with `tenancy_and_visibility.md` §2–§5.)*
**Companion to:** `FOUNDATION_AUDIT_REPORT.md` · handoff details in `FOUNDATION_CONTINUATION.md` (see its "LATEST STATUS" block) · competitive learnings (proven reference implementations from Hermes Agent & OpenClaw) in `project-docs/specs/competitive_hardening_2026-07.md` (`CH-*`) and `COMPETITIVE_COMPARISON.md` · tenancy architecture of record in `project-docs/specs/saas_multitenancy.md` (D15, 2026-08-08) · visibility architecture of record in `project-docs/specs/tenancy_and_visibility.md` §2–§5.

> **🚀 Deploy status:** read live deploy state from `gh run list` and `git log origin/main` — not from this doc. Next recommended P0: **BO‑8** (secret rotation + history purge — owner‑gated); BO‑1's approval loop has since shipped (see §BO‑1).
> **Update 2026-08-01 (doc-truth pass):** the previous pinned‑commit claim here (`origin/main = ccccdc8`, unpushed `1684e1a`) was a 2026‑07‑13 snapshot and went stale; this doc no longer tracks deploy state.

This is the list of foundational capabilities that are **missing, partially implemented, or not yet wired up**. It excludes application features. Each item states what is missing, why it matters, what it depends on, a suggested approach, and a recommended priority. Items already addressed in the review pass are marked **✅ done (Fx)** and are retained here for completeness with any residual follow‑up.

**Priority legend:** **P0** = do before any new feature work · **P1** = next hardening sprint · **P2** = scheduled tech‑debt · **P3** = opportunistic.

**Status legend:** ☐ not started · ◑ partial · ✅ done this pass.

---

## Verdict — can we go app by app? *(2026‑08‑03)*

**Yes, with three exceptions.** The owner asked whether the foundation is complete
enough to stop doing platform work and start doing app work. It is: auth is
default‑deny and enforced by construction (BO‑2 ✅), the Action Broker is a live
audited chokepoint (BO‑1 ◑ — its two flip‑blockers a+b closed 2026‑08‑11, c open), the runtime story is
settled (BO‑12 ✅), the permission gate denies the two things that matter
(BO‑14, corrected below), and the event intake substrate is half built and
sequenced (BO‑20 ◑). None of that blocks the next app.

**Three items are exceptions.** They are not app work, they do not improve by
being deferred behind app work, and **one of them gets worse with every app
added**:

| # | Exception | Item | The one‑line reason |
|---|---|---|---|
| 1 | ~~**`main` has no branch protection**~~ **CLOSED 2026‑08‑03** | §BO‑17 / `work_plan.md` WS‑5 | Was 404 / rulesets `[]` for months. **Now enabled:** PRs required (`required_approving_review_count: 0` — a sole maintainer must still be able to land work), `enforce_admins: true` (without it the protection is decorative for the only person who pushes), force‑pushes and deletions **blocked**. ⚠️ **`required_status_checks` is deliberately `null`** — `pr-check.yml` carries `paths-ignore: ["**.md", "project-docs/**"]`, so a docs‑only PR runs **no** checks at all; requiring those contexts would leave every docs PR permanently unmergeable. To require them, first give `pr-check` an always‑runs sentinel job, then add that job as the only required context. |
| 2 | **No *scheduled* backup; no restore ever exercised** — ◑ **both halves now addressed in-repo 2026‑08‑07; one owner step left** | **§BO‑23** (below) | The `acb-backup.service`/`.timer` units now exist under `deploy/hostinger/`, and `deploy.sh` installs and enables every unit in that directory on each deploy — so the schedule arrives with the code instead of waiting for someone to remember. **A restore has now been executed**, and keeps being executed: `scripts/rehearse_restore.sh` does a real round trip (seed → dump → DROP → restore → md5 compare) and runs on every PR against a real Postgres. Verified to fail when the restore does not restore, not only to pass when it does. **Left for the owner:** run one restore against a *production* backup — the rehearsal proves the tooling, not that this box's data and volume are what you think. Recovery position otherwise unchanged: Hostinger VM images, weekly, 2 retained, ~58 min, whole-machine. |
| 3 | ~~**DB engine sprawl**~~ **CLOSED 2026‑08‑06** | §BO‑10 | Was **12 `create_async_engine` call sites across 10 modules** (+ one sync engine), 8 of them undisposed process‑lifetime singletons, one arriving per app — the only item whose cost compounded per app. **Now one engine and one pool for every async caller**, in `acb_common/db.py` (not the gateway: `acb_auth.access` runs in the gateway process and cannot import it). `acb_audit.record()` is non-blocking on the loop, drained at shutdown. A new engine now fails `tests/unit/test_db_engine_seam.py`. Remaining by design: `acb_graph`'s **sync** engine and `email_ingestion`'s per‑run engines. |

Nothing else on this list needs to be closed first. Items 1 and 2 are risk
containment the owner must action; item 3 was the one an agent should fix before
the next app opened engine number 13 — **closed 2026‑08‑06, and the seam is now
guarded by a test rather than by a note in this file.** Item 2's agent-safe half
is likewise closed and guarded by CI (2026‑08‑07); what is left there is one
restore against real production data, which no test can stand in for.

---

## A. Security & trust boundaries

### BO‑1 — Action Broker: real approval‑gated write path *(P0)* ◑ — **BO‑1a + BO‑1b BUILT 2026‑08‑11 (WS‑1); BO‑1d + BO‑1c open. ⚠️ THE `ACTION_BROKER_ENFORCE` FLIP IS STILL UNSAFE — it is blocked on BO‑1d.**

> **Status: rewritten and verified against code 2026-08-03** (WS‑0 truth pass). Verified this pass by reading the source, not the prior doc: the six broker‑gated action names and their line numbers in `routes/tasks/providers.py`; the four entries in `broker_handlers._WRITERS`; the existence of `_raw_delete_task`/`_raw_archive_task`; `broker.execute()`'s no‑handler branch; **five** handler‑registration sites (the doc previously named three); `items.py`'s unconditional `sync_state='synced'` on both the parent and subtask push paths; `GtdItemModel.sync_state` being a bare `str`; the `sync_state` column being bare `TEXT` with no CHECK; the Zoho client being read‑only (`list_*` only, two `GET`s to `/crm/v2/*`, zero writes repo‑wide); the absence of any `action_broker` import under `email_ingestion/`; and the 14 outward‑write verbs on the email provider base class. Two bullets that claimed the broker is "inert" were **false since 2026‑07‑13** and are struck below; the same falsehood was live in three code docstrings and is corrected there in the same change. "Remaining: Zoho handlers" was **fiction** and is struck. §BO‑1 now carries three lettered tickets with acceptance criteria, a verification command, gate labels, and one named decision (BO‑1c).

**What is true today (verified against code, 2026-08-03).** The broker is **live and writing**, not inert:
- `apps/services/action_broker/action_broker/broker.py` — the real component: `decide_disposition(authority, destructive)` (READ→rejected, AUTONOMOUS→auto, SUGGEST→needs‑approval, SUGGEST_APPLY→auto for reversible / needs‑approval for destructive, i.e. FAIL CLOSED); `propose()` computes + audits the disposition (defaults `destructive=True`); `register_action_handler` / `execute` is a fail‑closed executor registry — a source‑of‑truth write happens ONLY inside a registered handler, and an action with no handler is REFUSED (`broker.py:179-189`), never silently applied. Persistence (`enqueue` / `list_pending` / `approve` / `reject` / `submit`) landed with `66_pending_actions.sql` in commit `e59cc6a`.
- The Control Plane approval inbox is bound via gateway `routes/actions.py` (`GET /actions/pending`, `POST …/approve`, `POST …/reject`, all behind `require_internal_auth`).
- **Handlers are registered at SIX sites, not three, and not five.** *(Anchors re-measured 2026-08-11 — the four in the previous version of this table had all drifted, and the CRM site was missing entirely since 2026-08-05.)* The complete list:
  | Site | Registers | When |
  |---|---|---|
  | `gateway/main.py:1073` → `routes/tasks/broker_handlers.register_task_broker_handlers()` | `clickup.create_task`, `clickup.update_task`, **`clickup.delete_task`, `clickup.archive_task`** (both added by BO‑1a, 2026‑08‑11), `clickup.create_project`, `clickup.create_folder` | startup |
  | `gateway/main.py:1084` → `routes/crm/broker_handlers.register_crm_broker_handlers()` | `crm.zoho_create`, `crm.zoho_update`, `crm.zoho_delete` | startup |
  | `gateway/main.py:1168` → `routes/workflows/broker_handlers.register_handlers()` | `workflow.resume_run` | startup |
  | `routes/whatsapp/scheduler_hooks.py:30` → `routes/whatsapp/automation/outbound.register_whatsapp_handlers()` | `whatsapp.broadcast` | startup |
  | `routes/apps/tools.py:212` | `app.clickup_create_task` | module import |
  | `routes/apps/tools.py:273` | `app.publish_review` | module import |
- The previously bypassing ClickUp task writes route through `BaseTaskProvider._broker_gate` (`routes/tasks/providers.py:154-201`), which reads `_broker_enforced()` (`:92-136`) at call time. **Build/flip are separable:** with `ACTION_BROKER_ENFORCE` unset the gate audits and returns `await do_write()` unchanged — zero behaviour change. Verified in code, so BO‑1a/b/d are safe to build with the kill‑switch off. *(Anchors re‑measured after the last edit of this pass, 2026‑08‑11 — the `:129-175`/`:92-111` pair here was written from the base commit by the very change that shifted them.)*

**Struck as false (historical, do not act on):**
- ~~"Ships with **zero** handlers so it cannot write anything yet — inert + non‑breaking."~~ *(historical — untrue since 2026‑07‑13; see the five registration sites above.)*
- ~~"No live path rerouted, so still inert."~~ *(historical — untrue since 2026‑07‑13; ClickUp task writes, WhatsApp broadcast, workflow resume and app publish‑review all route through the broker.)* The same wording was live in `broker.py`, `routes/actions.py` and `routes/tasks/providers.py` docstrings and was corrected in the same change as this rewrite.
- ~~"Remaining: **Zoho** handlers."~~ **Struck as BO‑1 work, and it stays struck — but the reason below has itself expired.** ⚠️ **"There is no Zoho write path anywhere in the repo" is FALSE since 2026‑08‑05**: `apps/services/ingestion/ingestion/sources/zoho/writer.py` exists (WS‑26b, spec `crm_app.md` §7.1 / D‑CRM‑7), its one caller is `gateway/routes/crm/sync_zoho.py::execute_push`, and every push crosses `routes/crm/broker_handlers.py::broker_gate` first — so the Zoho handlers exist, they are **WS‑26b's and not BO‑1's**, and all three (`crm.zoho_create`/`_update`/`_delete`) are registered. The paragraph below is preserved as the 2026‑08‑03 measurement of the *read* client only. That client is still read‑only (ten `list_*` readers; its one `POST` is the OAuth refresh) — but "the only CRM HTTP calls are `GET /crm/v2/{module}` and `GET /crm/v2/users`" is also stale: WS‑26f's two settings readers deliberately do not use `/crm/v2`. `apps/services/ingestion/ingestion/sources/zoho/client.py` was, as measured then, read‑only: `list_accounts` / `list_deals` / `list_contacts` / `list_notes` / `list_tasks` / `list_users`, and the only CRM HTTP calls are `GET /crm/v2/{module}` (`:109`) and `GET /crm/v2/users` (`:152`) — repo‑wide grep for `crm/v2` returns exactly those two lines. (The one `http.post` at `:58` is the OAuth token refresh to the accounts host, not a CRM write.) The `"zoho.email"` example in `broker.py:78`'s comment is illustrative, not a pointer to real code (the module docstring's own note about it is at `:41-45`). **A Zoho broker handler is not BO‑1 work until a Zoho write client is specced and built elsewhere.** The recent Zoho *webhook* 500 fix is inbound‑only (WS‑4 / §BO‑20f) and has zero bearing here.
- **Stale anchor corrected:** the old text cited `routes/tasks/providers.py:365` as the bypassing write. That line is now a `t.get("name", "Workspace")` in the workspace lister — nothing to do with a write. The broker gate is `_broker_enforced` at `:92-136` and `_broker_gate` at `:154-201`.

**Resolved / not a done‑when:**
- **Integration‑verify the queue SQL against a live Postgres — OWNER‑GATE, not agent work.** `66_pending_actions.sql` is committed to `main` and migrations auto‑apply on deploy, so prod almost certainly has the table; but `FOUNDATION_CONTINUATION.md:145` recorded this as still‑outstanding on 2026‑07‑13 and nothing since records it as executed. **No agent may claim this as done, and no agent may reach prod to do it.** It is not an acceptance criterion for BO‑1a/b/c — all three are hermetic.
- **Flipping `ACTION_BROKER_ENFORCE` — OWNER‑GATE, and STILL UNSAFE.** Default OFF: every write auto‑applies, audited. ⚠️ **Corrected 2026‑08‑11 (repair round 1). The previous version of this line said the flip's blockers were closed. That was false and it is the most dangerous kind of false — the two reasons *it* named are closed, but a third class was never ticketed.** BO‑1a cleared the **handler‑routing** blocker and BO‑1b the **sync‑state** blocker; **BO‑1d below is the blocking ticket that actually unblocks the flip.** Three live endpoints hard‑**500** under enforcement and one write is silently swallowed, because their callers never read the gate's pending marker — see BO‑1d for the four sites with their failure modes. **Do not flip until BO‑1d is in.** Three further riders, none of which BO‑1d clears:
  - the variable is set **nowhere** — not in `.env.example`, not under `deploy/`, no compose file, no systemd unit — so today the gate takes its auto‑apply path and `broker.execute()` is unreachable from the ClickUp write path;
  - a `pending_actions` row queued *before* a flip is still approvable *after* one, so **check `SELECT action, status FROM pending_actions` first**;
  - an approved write is never reconciled back onto its `gtd_items` row (BO‑1b done‑when 6), so the row stays `awaiting_approval` and the next `/tasks/sync` pull inserts the task a second time.

#### BO‑1a — every gated action name has a registered handler ✅ **BUILT 2026‑08‑11** *(AGENT‑SAFE · 1 small PR)*
**The bug, undocumented until it was fixed.** `providers.py` routes **six** action names through `_broker_gate`: `clickup.create_task` (`:473`), `clickup.update_task` (`:549`), **`clickup.delete_task` (`:577`)**, **`clickup.archive_task` (`:601`)**, `clickup.create_project` (`:691`), `clickup.create_folder` (`:703`). `broker_handlers._WRITERS` registered **four**. So with `ACTION_BROKER_ENFORCE=all`, approving a queued delete or archive fell into `broker.execute()`'s no‑handler branch (`broker.py:179-189`), returned `{"ok": False, "error": "no handler registered…"}` and marked the row **`failed`** — **the two UNROUTED verbs, one of which (`archive_task`) is the one live purge/archive path.** ⚠️ *Correction 2026‑08‑11: the old wording, "the two irreversible ones", was wrong on both counts.* `archive_task` is documented **reversible** in its own docstring (`providers.py:596-599`, and `archive_task(tid, False)` un‑archives), and `ClickUpProvider.delete_task` has **zero production callers** — `items._delete_upstream` archives instead, pinned by `test_tasks_gtd.py:544`. The live one is the archive. `tests/unit/test_task_broker_handlers.py` asserted against a hard‑coded four‑element literal and was therefore structurally blind to this.

⚠️ **Anchor discipline, learned twice in this section.** Every `:line` in §BO‑1a/b/d above was written *after* the last source edit of its pass and re‑checked immediately before commit. The previous version was written from the base commit **by the same change that shifted the lines** — it sat under a note claiming "anchors re‑measured 2026‑08‑11" and every one of them was off by one to twenty‑six. Re‑measure last, not first.

**Done when:**
1. ✅ `_WRITERS` covers `clickup.delete_task` → `_raw_delete_task` (args `("provider_task_id",)`) and `clickup.archive_task` → `_raw_archive_task` (args `("provider_task_id", "archived")` — note the second arg). ⚠️ The rider "the gate's `audit_payload` must carry it" needed **no `providers.py` edit**: `:602-604` already put `archived` in `args`. Asserting the payload shape was the whole of it — `test_the_gated_payload_carries_every_arg_its_writer_reads`. ⚠️ *Repair round 1:* that test now sweeps **all six** gated actions, not just the two this ticket added. The four pre‑existing ones were only ever "checked" by the dispatch tests, which hand‑write the `args` dict on both sides and would therefore agree with a wrong key; here one side is the real gate call site and the other is `_WRITERS`, with `set(seen) == set(_WRITERS)` as the vacuity guard.
2. ✅ The literal set is replaced by two derived tests: `test_register_wires_every_writer_action` (`set(_HANDLERS) == set(_WRITERS)`) and `test_every_gated_provider_action_has_a_writer`, an `ast` walk of `providers.py` collecting the first positional `ast.Constant` string of every `Call` whose func is an `Attribute` named `_broker_gate`, asserting that set ⊆ `set(_WRITERS)`. **Its three limits are written into its docstring** and bind anyone reading it as coverage: it is blind to **reachability**, blind to a **non‑literal** action name (variable / f‑string), blind to an **aliased or indirect gate call** (`g = self._broker_gate; await g("clickup.x", …)` is an `ast.Name`, silently skipped), and **scoped to `providers.py` only** — so a **second connector module** (`providers.py`'s own header invites "Asana/Jira/Linear … slot in beside it") satisfies it vacuously, as do `routes/crm/broker_handlers.py`'s separate `broker_gate` and `routes/apps/tools.py`'s `_broker_action_name`. *(The last two blind spots were added in repair round 1 — the enumerated list had omitted the two most likely.)* A vacuity guard (`assert gated`) fails the test if the walk stops matching; measured red against a renamed gate.
3. ✅ `test_approving_a_queued_delete_ends_applied_not_failed` plus the archive twin (`test_approving_a_queued_archive_carries_the_archived_flag`, asserting `("archive_task", "T1", True)` reaches the provider — that arg is the one the four‑entry map never carried). Both measured red with the `_WRITERS` entry removed.
4. ✅ `uv run ruff check <the files you touched>` is clean. ⚠️ Do **not** write "`uv run ruff check .` clean" as a criterion — that command reports **2115 pre‑existing errors** on this tree (re‑measured 2026‑08‑11 at base `a06fa6a` **and** at this branch's tip: identical, so WS‑1 adds none. The "**1983**" this line carried was a 2026‑08‑03 figure that had drifted by 132 and was still wearing a ✅), so it can never pass and is not a signal. Lint the paths you changed, and re‑measure the figure rather than copying it.

**What this does NOT change today:** `ACTION_BROKER_ENFORCE` is set **nowhere** — not in `.env.example`, not under `deploy/`, no compose file, no systemd unit — so `_broker_gate` takes its auto‑apply path and never reaches `broker.execute()`. The honest claim is *no behaviour change absent an approval click on a pre‑existing queued `pending_actions` row*, not "zero behaviour change". That residual is real: check `SELECT action, status FROM pending_actions` before any flip.

**Non‑goals:** do not touch the apps‑tool broker surface (`routes/apps/tools.py`). It has the same structural shape — `_run_destructive_tool` proposes `_broker_action_name(tool)` for any `ToolSpec(destructive=True)`, and only `clickup.create_task` has a handler — but `_TOOL_REGISTRY` currently holds exactly one tool, which *is* handled, so there is no live hole. Note it; do not widen this PR.

#### BO‑1b — a queued write never reports as synced ✅ **BUILT 2026‑08‑11** *(AGENT‑SAFE · 1 medium PR)*
**The second flip‑blocker.** When `_broker_gate` queues instead of writing, it returns `{"pending": True, "pending_action_id": …, "provider_task_id": ""}` (`providers.py:196-197`). `items._push_pending_item` (`:1301-1403`) ignored the marker entirely and unconditionally set `sync_state='synced', provider_task_id=''`. The subtask writer `_push_child_subtasks` (`:1500-1552`) did the same. Under enforcement, items would be marked **synced to nothing** — the user sees a green "synced" task that exists in no workspace. **The subtask hole is UNREACHABLE through `POST /push` and the old parenthetical had the reason backwards:** parent and child share the action name `clickup.create_task`, so enforcement cannot queue the child without queueing the parent, and a queued parent returns before the loop is reached (the `if parent_tid:` guard at `:1400` sits behind that). It is fixed as defence in depth and tested by calling `_push_child_subtasks` directly. ⚠️ **This ticket only taught these TWO call sites to read the marker — four others still do not, which is BO‑1d and is why the flip is still blocked.**

**DECISION (agent‑proposed, owner may overrule): the new value is `sync_state = 'awaiting_approval'`.** A third value is required because `'pending'` is already taken to mean "staged, awaiting the *user's* push" (written by `/items/{id}/organize` at `items.py:1468` and required by `_push_pending_item`'s entry guard at `:1323`; the UI's Push affordance at `ItemDetail.tsx:757` keys off it, now through `canPush`) — reusing it would make the Push button reappear on a task already queued in the broker. `'awaiting_approval'` matches `Disposition.NEEDS_APPROVAL` and the `/actions` inbox's own language. **No migration:** `sync_state` is bare `TEXT DEFAULT 'local'` with no CHECK constraint (`infra/postgres/48_task_manager_gtd.sql:109`), and `GtdItemModel.sync_state` is a bare `str` (`routes/tasks/core.py:183`), so the value passes through the API unchanged. (Migration 48's inline comment already calls `'pending'` "queued push, Action‑Broker‑gated" — that comment is itself imprecise and should be corrected to distinguish the two states.)

**Done when:**
1. ✅ With `ACTION_BROKER_ENFORCE=all` (set hermetically via `monkeypatch.setenv`), the push leaves `provider_task_id` untouched and sets `sync_state='awaiting_approval'` — it never writes `'synced'`. Tested by calling `items._push_pending_item` directly against a fake session and asserting the captured `UPDATE`; the pending marker comes from the **real** `_broker_gate` (env var set, `action_broker.enqueue` stubbed, `httpx.AsyncClient` replaced by a raiser so a queued write that reached HTTP would fail loudly), never from a hand-written dict.
2. ✅ The subtask loop applies the same rule — as **defence in depth**, since the branch is unreachable via `POST /push` (see above). Its test says so, and says why, so nobody later "fixes" the `if parent_tid:` guard (`items.py:1400`) to make it fire: a queued parent has no upstream id, so its children would be created top-level.
3. ✅ `GtdItemModel` needed **no change** (`sync_state` is a bare `str`, `core.py:183`) — pinned by `test_gtd_item_model_projects_awaiting_approval_unchanged`. `types.ts` widens its union onto the new `SyncState` type; `lib/api.ts:109` passes it through unchanged.
4. **Rewritten 2026‑08‑11 — the original was unsatisfiable.** `workbench/control_plane` has **zero** `*.test.tsx`, no `@testing-library`, no jsdom/happy-dom; all frontend tests are pure-function `lib/*.test.ts`, so "the tasks UI renders a distinct badge" could not be asserted by anything. ✅ *Built instead:* the "may this item be pushed?", "is there an upstream task?" and "which sync badge?" decisions are exported pure functions in `src/app/tasks/lib/syncState.ts`, covered by `syncState.test.ts`; `ItemDetail.tsx` (Push affordance, stage-options effect) and `taskStore.ts` (the auto-push retry) call them instead of comparing to the `"pending"` literal, and the queued state renders its own section with **no Push button**. **What this proves:** the decision table is correct and locked. **What it does not prove:** that the TSX renders it — there is no component-test mechanism in this repo and none was minted for this ticket; that the three call sites were converted is verified by reading the diff, not by a green suite. The badge is a `MetaChip` descriptor carrying a `MetaTone` NAME (`lib/taskCard.ts`'s vocabulary, rendered by `components/TaskMeta.tsx`, the one tone→class table) — no ad‑hoc hex, no raw Tailwind palette class, no second tone table. ⚠️ *Repair round 1:* `syncBadge("pending")` has **no live render site** — `ItemDetail.tsx:746` renders the badge at `sync && !pushable` and a `pending` row is by definition `canPush`, so the staged case is drawn by the Push affordance section (`:757`) with its own label. The branch is kept **deliberately, as a total table over `SyncState`**, and now says so in its own docstring rather than reading as accidentally dead. Wiring it into the affordance is a UI change, not a correctness one. `taskStore.ts`'s `targetFields` return type is narrowed with `Extract<SyncState, "local" | "pending">` instead of re‑declaring a local union — one vocabulary, machine‑checked.
5. ◑ Hermetic unit tests, no live DB, no migration ✅. `uv run ruff check <files you touched>` — **the two Python files carry 7 findings and all 7 are pre‑existing and identical at the base commit** (`items.py` C901 `_build_item_update` + I001 at `:669`; five RUF001 `›` in `test_tasks_gtd.py:824-827`), none on a line this PR wrote. Every other touched file is clean. Fixing them would mean refactoring `_build_item_update`, which this PR does not do.
6. ✅ **New and mandatory.** The accepted gap is written down rather than left to be found: an approved-then-executed write leaves the original row at `awaiting_approval` with **no** `provider_task_id`, so the next `/tasks/sync` pull inserts the task as a **second** row (`sync.py`'s upsert conflicts on `(account_id, provider_task_id)`). Recorded in `_push_pending_item`'s docstring and in the PR body. It follows from this ticket's own non-goal below.
7. ✅ **Added in repair round 1 — every consumer of the widened vocabulary is swept, not just the UI's.** Widening `sync_state` creates readers that silently answer the new value wrong, and one was missed: the GTD **skill**'s `_fmt_item` (`apps/skills/skill-task-gtd/skill_task_gtd/core.py:170`, `:163` before this fix) tested only for `'pending'`, so an `awaiting_approval` item rendered into the agent's context with **no marker at all** — and no provider link either, since nothing exists upstream — making a queued task indistinguishable from a normal one. Fixed with an explicit `elif`, fenced by `test_agent_item_format_distinguishes_the_two_waiting_states`. (`core.py:465`, the organize confirmation, is unaffected: it is reached only immediately after organize, where the state is always `pending`.) ⚠️ The lesson generalises to BO‑1d and beyond: **when a state vocabulary grows, grep every `== "<old value>"` in the tree, not just the surface you are editing.**
8. ✅ **Repair round 1 — `provider_task_id` is left untouched for a reason that is TRUE.** The shipped comment justified it with "every downstream reader treats a set-but-empty id the same as a real one", which is false — `_push_patch_upstream` (`items.py:769`) and the purge guard (`:445`) both test truthiness, so `''` reads as *absent*. The decision is right and stands; the real reason is that a staged item is `source='SYNCED'`, hence inside `uq_gtd_items_provider ON gtd_items(account_id, provider_task_id) WHERE source <> 'LOCAL'` (`48_task_manager_gtd.sql:122`) — NULL is exempt there, `''` is not, so the **second** queued push on one account would fail the `UPDATE` on a unique violation.

**Non‑goals:** no reconciliation job that later flips `awaiting_approval` → `synced` when the approval lands. The approve path already runs the handler; wiring its result back onto the `gtd_items` row is separate work and is **not** in this PR — see done-when 6 for the duplicate-row consequence that non-goal buys.

**Still open, deliberately not done here:** migration 48's inline comment still calls `'pending'` "queued push, Action‑Broker‑gated", which is now the *other* state's description. Correcting a comment inside an already-applied migration is not this PR's business (R6: applied files are not edited in place) — it wants its own decision about where the vocabulary is documented.

#### BO‑1d — the queued‑marker readers: four callers that treat a pending marker as a result ☐ **NOT STARTED** *(AGENT‑SAFE · 1 small–medium PR)*
**Minted 2026‑08‑11 in WS‑1's repair round 1. This is the ticket that actually unblocks the `ACTION_BROKER_ENFORCE` flip** — BO‑1a and BO‑1b cleared the handler‑routing and sync‑state blockers and *nothing else*; the branch briefly claimed the flip was safe, and it was not.

`_broker_gate` returns `{"pending": True, "pending_action_id": …, "provider_task_id": ""}` when enforcement queues a write. BO‑1b taught `items._push_pending_item` / `_push_child_subtasks` to read that marker. **Four other callers of a gated ClickUp write still do not**, measured against the real `ClickUpProvider` with `ACTION_BROKER_ENFORCE` set:

| # | Call site | Endpoint | Failure mode under enforcement |
|---|---|---|---|
| 1 | `routes/tasks/accounts.py:335` (`created["id"]`, and again at `:345`, `:355`, `:367`) | `POST /tasks/accounts/{account_id}/projects` (`:299`) | `create_project` returns the marker, which has no `"id"` key → **`KeyError` → HTTP 500**. Nothing is created and the caller gets no marker either. |
| 2 | `routes/tasks/accounts.py:403` (`created["id"]` / `created["name"]`, and again at `:413`) | `POST /tasks/accounts/{account_id}/folders` (`:377`) | Same shape → **`KeyError` → HTTP 500**. |
| 3 | `routes/tasks/planning.py:377` (`list_ref = created["id"]`) | `POST /tasks/plan/apply` with `target: "clickup"` (`:286`) | Same shape → **`KeyError` → HTTP 500**, before any task in the plan is attempted. ⚠️ **The same file already defends against this exact marker** at `:405` (`ptid = res.get("provider_task_id") or ""` → `continue`) for the per‑task create — so the gap is a *known* one in that file that was never extended upward to the list create. |
| 4 | `routes/tasks/items.py:790` (`_push_patch_upstream`) | `PATCH /tasks/items/{item_id}` on a SYNCED, pushed task | **No 500 — worse in a different way.** The marker carries no `provider_status`/`provider_url`, so the mirror `UPDATE` list is empty and the function returns cleanly. The member's edit saves locally, the API reports success, **nothing reaches ClickUp and no state records that a write is queued.** |

**Done when:**
1. Each of the four call sites reads the marker before indexing the result, and the endpoint's outcome is a *reported queued state*, not a 500 and not a silent success. Sites 1–3 own a product decision the spec does not yet record — **what a queued create returns to the caller** (a 202 with the `pending_action_id`? a 200 with a `pending` flag on the response model? no `gtd_projects` mirror row at all until approval, since there is no `provider_ref` to key it on?). **Name that decision in this ticket before building**; do not let each site answer it differently.
2. Site 4 records the queued update somewhere the member can see — the same class of fix as BO‑1b's `awaiting_approval`, and the reason it is in this ticket rather than BO‑1b's.
3. A test per site drives the **real** `_broker_gate` with `ACTION_BROKER_ENFORCE` set (`monkeypatch.setenv`, `action_broker.enqueue` stubbed, `httpx.AsyncClient` replaced by a raiser so a queued write that reached HTTP fails loudly) — never a hand‑written marker dict. That is the idiom BO‑1b's tests already use.
4. A fence that fails when a **fifth** unguarded reader appears, in the shape of BO‑1a's AST fence: the honest version scans the callers of the six gated `ClickUpProvider` methods for a subscript of the result that no `pending` check guards. State its limits in its docstring, as BO‑1a's does.
5. `uv run ruff check <files you touched>` clean (see BO‑1a's note — repo‑wide ruff is **2115** pre‑existing errors and is not a signal).

**Non‑goals:** the reconciliation job (still BO‑1b's recorded non‑goal — nothing carries an approved write's `provider_task_id` back onto the row); `routes/crm/*` and `routes/apps/tools.py`, which have their own gate surfaces; and the flip itself, which stays OWNER‑GATE §6 even once this lands.

#### BO‑1c — email handlers *(AGENT‑SAFE to build, but BLOCKED on the decision below · 1–2 medium PRs)*
Confirmed real remaining work: there is **zero** `action_broker` wiring anywhere under `apps/services/email_ingestion/` — every outward email write bypasses the broker today. But the ticket is not dispatchable until §BO‑1 names *which* verbs are broker actions, because the provider base class (`email_ingestion/providers/base.py`) exposes **14** mutating verbs: `send_message` (`:264`), `modify_message` (`:289`), `trash_message` (`:299`), `apply_flags` (`:307`), `move_to_folder` (`:322`), `bulk_apply` (`:339`), `create_folder` (`:387`), `create_filter` (`:398`), `delete_filter` (`:416`), `set_labels` (`:446`), `set_label_color` (`:484`), `create_draft` (`:492`), `update_draft` (`:521`), `send_draft` (`:551`). Brokering all 14 would put a human approval in front of every label click.

**DECISION (agent‑proposed, owner may overrule) — broker the destructive/outward set only: `send_message`, `send_draft`, `trash_message`, `delete_filter`.** Rationale: these are the four that either leave the system (a recipient sees it) or destroy state a user cannot trivially restore. **Explicit non‑goal:** label, flag, folder‑move, draft‑create/update and filter‑create operations are **not** brokered — they are reversible, in‑mailbox, high‑frequency, and Metorite is an internal Fracktal tool used by trusted colleagues, so a per‑click approval would be pure friction with no trust gain. *(Premise dated 2026-08-09: true until the first external tenant — WS-29/D15 retires it; the `ACTION_BROKER_ENFORCE` posture must be re-decided before customer #1.)* `bulk_apply` is a fan‑out over `move_to_folder`/`trash_message` (`base.py:339-386`), so it inherits the gate only through `trash_message`.

**Done when (once the decision above is confirmed or overruled):**
1. The four chosen verbs route through a gate of the same shape as `BaseTaskProvider._broker_gate` — audit + auto‑apply by default, queue only under `ACTION_BROKER_ENFORCE`, and a broker‑layer error never blocks a user‑approved write.
2. Registered handlers exist for all four action names, so approving a queued proposal actually executes (the BO‑1a failure mode must not be reproduced here); the handler re‑resolves the account's credentials from the stored account id, and the token is never persisted in the proposal payload.
3. A test derives the expected handler set from the gate call sites, as in BO‑1a.
4. The non‑brokered verbs are asserted to be untouched by a test, so a later change cannot silently widen the gate.
5. Hermetic; `uv run ruff check <files you touched>` clean (see the BO‑1a note — repo‑wide ruff is not a signal).

**Verification (all three tickets):**
```
uv run pytest tests/unit/test_action_broker.py tests/unit/test_actions_routes.py \
  tests/unit/test_provider_broker_gate.py tests/unit/test_task_broker_handlers.py -q
```
Measured on this branch, 2026-08-03: **`32 passed in 1.95s`** — hermetic, no live DB, no network. This is the regression floor; each ticket adds to it.

- **Why needed:** It is non‑negotiable #4 ("no autonomous writes to source systems until the Action Broker is live") and the single control point for HITL over all outward writes. The chokepoint now exists and is audited for ClickUp tasks, WhatsApp broadcast, workflow resume and app publish‑review; it does **not** yet cover email (BO‑1c). ~~"two of its own gated ClickUp actions cannot execute after approval"~~ **closed 2026‑08‑11 by BO‑1a** — all six gated ClickUp action names now have handlers, and a derived test fails if a seventh arrives without one. That closed the *execution* half; the *caller* half is BO‑1d and is open, which is why the gate is still audit‑only.
- **Dependencies:** `pending_actions` (exists, `66_pending_actions.sql`); `acb_audit`; the Control Plane approval inbox (exists, `routes/actions.py`); BO‑2 (authenticated approvals — ✅, the routes are behind `require_internal_auth`).
- **Note:** With enforcement OFF (the default) writes auto‑apply and are audited, so #4 is satisfied by audit-and-chokepoint rather than by human approval. That is the deliberate posture for an internal tool; flipping `ACTION_BROKER_ENFORCE` on is the OWNER‑GATE that turns it into a true HITL gate. **Two of its blockers landed on 2026‑08‑11 (BO‑1a, BO‑1b) and a third — BO‑1d, the queued‑marker readers — is open, so the flip is NOT yet safe**; plus the standing residuals (the variable is set in no environment, and a `pending_actions` row queued before a flip stays approvable after one). *(Premise dated 2026-08-09: true until the first external tenant — WS-29/D15 retires it; the `ACTION_BROKER_ENFORCE` posture must be re-decided before customer #1.)*
- **Competitive ref (CH‑2):** Hermes Agent routes every risky action through a **single fail‑closed approval gate** — the pattern to copy is "one choke point that a write physically cannot bypass," which is exactly what `execute()`‑only‑writes enforces. See `specs/competitive_hardening_2026-07.md`.

### BO‑2 — Enforceable authentication + authorization *(P0)* ✅
- **Authorization (2026‑07‑29):** org access control shipped — DB‑backed roles + per‑user allow/deny overrides (`acb_auth.permissions`/`access`), `require_permission` on every feature router, per‑agent run gating, and per‑member integration credentials. Spec: `project-docs/specs/org_access_control.md`. The remaining BO‑2 work is the *authentication* posture (residual 1), not the permission model.
- **Missing (historical — resolved):** `get_current_user` never rejects (`acb_auth/deps.py:76`); it only labels. So mutation‑approve (`agent.py:1852`, `git push`), the memory API (`memory.py`, IDOR), and `/agent/webhook/{source}` (`agent.py:3428`; the long‑standing `:2522` here
was stale — corrected 2026-08-02 to match §BO‑20) were anonymous‑reachable. `/v1` had no auth at all. **Update 2026-08-01 (doc-truth pass):** this paragraph is resolved history — every systemic item in the Residual list below is ✅; the honest remaining residual is that in‑process agents can read the identity token until BO‑7.
- **Done this pass:** **✅ F1** authenticates `/v1`; **✅ F7** adds `acb_auth.require_internal_auth` and gates the state‑changing mutation routes + the whole `/memory` router (401 anonymous). This closes C1/C2/C6 for the specific dangerous endpoints.
- **Why needed:** Prevents anonymous code‑push, cross‑tenant memory read/delete, and unauthenticated agent triggering.
- **Dependencies:** Confirm each protected endpoint's caller sends the internal token or a real user session (the Next.js server routes and `memory.ts` already send `Bearer LITELLM_MASTER_KEY` — verified).
- **Residual (the systemic fix):** (1) Add `acb_auth.require_authenticated` and make it the DEFAULT posture rather than opt‑in per route — **✅ done**: attached once at `FastAPI(dependencies=[...])`, so every route is covered by construction and a new one needs no opt‑in. `gateway.main.PUBLIC_ROUTES` is the complete anonymous‑reachable list (health, provider webhooks, OAuth callbacks, bridge/bot callbacks — each self‑authenticating). Closed two live holes: `/agent/workspace/{id}/history` (anonymous read of an agent's file history) and `/promote` (anonymous write). Swagger/ReDoc cannot carry the guard structurally, so they are dev‑only now. A coverage test fails if any route ever bypasses the guard. (2) Cover the remaining `agent.py` routes and `oauth.py` — **✅ done**: agent registry writes now need `agents:manage`, all three run endpoints call `assert_can_run_agent`, and `oauth.py` authorize/refresh need `feature:integrations` (the callback stays open by design — HMAC‑signed `state`). (3) Sign/verify `/agent/webhook/{source}` — **✅ done**: HMAC‑SHA256 over the raw body in `X-CC-Signature`, per‑source secrets override a global one, and it FAILS CLOSED (503) when unconfigured. (4) Split the service‑identity token from `LITELLM_MASTER_KEY` — **✅ done**: `GATEWAY_INTERNAL_TOKEN` is identity‑only and `LITELLM_MASTER_KEY` is the `/v1` key checked by the new `require_llm_api_auth`; every `/v1` client reads `settings.llm_api_key`, with a test asserting none resolves the identity token. Residual: in‑process agents can still read it from `get_settings()` until **BO‑7**. See `project-docs/specs/org_access_control.md` §8b.

### BO‑3 — Self‑mutation governance: human gate + real test gate + attempt counter *(P0)* ◑
- **Done this pass:** **✅ F8** — auto‑push is now opt‑in (`MUTATION_AUTO_PUSH`, default off) so a green commit stages in the approval inbox by default; `_tests_passed("")`/"no tests" now returns False (closes H3). **✅ H4** — `max_mutation_attempts` is now a REAL enforced counter: `mutation._register_mutation_attempt(run_id)` keeps a per‑run tally and refuses a second attempt for the same run (previously both call sites passed 0, so the `0 >= 1` guard was dead). 5 unit tests added (helper + the real entry point's early‑skip path).
- **Residual:** (1) Optionally define sandbox "success" as "a test command ran and exited 0 with ≥1 test" at the runner level (`mutation_runner.py:151`). (2) Wire mutation into the streaming path (H5) or explicitly scope it to structural failures and document that. (3) If cross‑restart durability is wanted, back the counter with Redis/Postgres instead of the in‑process dict (current scope — a restart is a fresh slate — is intentional and adequate given the human‑merge gate).
- **Dependencies:** `pending_commit` table (exists); Control Plane approval inbox; BO‑2 (authenticated approvals — the approve endpoint is now gated by F7).

### BO‑7 — Sandbox for dynamic agent execution (HH‑6) *(P1)* ☐
- **Missing:** cloned agent code runs in‑process (`loader.py:1247`) and installs deps into the shared gateway venv (`:1095`). No isolation.
- **Why needed:** Any compromised/malicious `agent-*` or `skill-*` repo (cross‑org clones allowed, `loader.py:1504`) gets arbitrary in‑process execution with access to all injected secrets and the DB. The mutation path is containerised; execution is not.
- **Dependencies:** the mutation sandbox image (`acb-mutation-runner`) as a reusable execution substrate; an IPC/result protocol; integration‑secret scoping so only the running agent's creds are exposed.
- **Approach:** Run each agent in the mutation‑style container (or a `nsjail`/subprocess with a per‑run venv and a dropped‑privilege user), stream results back over the existing event protocol. Interim mitigation: pin allowed orgs to `github_org`, and install deps into a per‑agent venv rather than the shared one.
- **Competitive ref (CH‑1):** Hermes Agent's container sandbox runs with `--cap-drop ALL` + `no-new-privileges` + pids/mem/disk limits — a concrete flag set to adopt for the `acb-mutation-runner`‑as‑execution‑substrate work. OpenClaw's CVE‑2026‑25253 (42K+ exposed host‑level panels) is the cautionary case for *not* doing this. See `specs/competitive_hardening_2026-07.md`.

### BO‑8 — Secret hygiene: rotate, purge history, fail closed *(P0)* ◑
- **Missing:** committed live Zoho token + 1.7 MB DB dump (**✅ F2** removes from tree + gitignore); but they remain in **git history**, and the token is (was) live. Weak in‑code secret defaults fail open (M4).
- **Why needed:** Files deleted from HEAD are still recoverable from history; a committed DB dump is a data‑breach vector.
- **Dependencies:** repo‑admin coordination (history rewrite forces a re‑clone for all clients); secret‑rotation access.
- **Approach:** (1) **Revoke/rotate** the Zoho token and any credential in `acb_dump.bak`. (2) `git filter-repo --path .zoho_token_cache.json --path acb_dump.bak --invert-paths` and force‑push (coordinate). (3) Make signing/DB/master keys raise on empty in non‑dev (`settings.py`). (4) Add a `gitleaks`/`detect-secrets` pre‑commit + CI hook.
- **Residual after F2:** history purge + rotation + fail‑closed defaults.

---

## B. Observability & operability

### BO‑5 — Real distributed tracing + honest cost tracking *(P1)* ◑
- **Done this pass:** **✅ F4** (unpriced models report *unknown*, not `$0`); tier label is now populated on agent‑traffic usage events (was blank, so per‑tier cost was empty); `/v1/embeddings` zero‑vector fallback now warns loudly (M13) instead of silently disabling semantic search.
- **Missing:** OTel is disabled and exports nowhere (H9); the OTLP exporter isn't installed; no collector in infra.
- **Why needed:** Production requires trace‑level debugging of multi‑agent runs and trustworthy spend numbers; today neither exists end‑to‑end.
- **Dependencies:** `opentelemetry-exporter-otlp` dep; an `otel-collector` (or Langfuse, already half‑present) service in `docker-compose.yml`; a real price map for the tier models.
- **Approach:** (1) Add the exporter dep + a collector service (Langfuse or Tempo/Jaeger). (2) Re‑enable MAF instrumentation once a backend exists and fix the ContextVar‑reset bug the kill‑switch was hiding (`executor.py:311`). (3) Set `OTEL_EXPORTER_OTLP_ENDPOINT` in deploy env. (4) Seed real per‑model prices for the tier models (or wire a pricing source) so cost is populated, and stamp the tier label on agent‑path usage (`_emit_usage(model, "", …)` → real tier, `v1_compat.py:245`).
- **Competitive ref (CH‑8):** Hermes surfaces **per‑turn cost** + `/usage`/`/insights` as a first‑class user‑visible feature; both competitors lean on third‑party OTel (SigNoz/Langfuse). Either finish the collector or formalize the bespoke Redis feed — do not keep advertising a disabled OTel. See `specs/competitive_hardening_2026-07.md`.

### BO‑9 — Resource lifecycle in the gateway shell *(P2)* ☐
- **Missing:** fire‑and‑forget `ensure_future` warmups are untracked and never cancelled on shutdown (`main.py:104,167,216`); no DB `engine.dispose()` / Neo4j `close()` on shutdown; Redis opened per‑call in ingestion (`queue.py:48`).
- **Why needed:** Clean shutdown, no leaked pools/tasks, testability.
- **Dependencies:** none.
- **Approach:** Hold task references and cancel them after `yield`; create/dispose the DB engine and a shared Redis pool in `lifespan`; inject them via `Depends`.

---

## C. Data layer

### BO‑6 — Migration framework + auto‑apply *(P1)* ◑ — **ledger done 2026‑08‑07**
- **Update 2026‑08‑07 — the ledger exists, and the ladder is now exercised.**
  `schema_migrations` (migration 152) records filename + sha256 + timing, and
  `apply_migrations.sh` skips what is already recorded. A steady‑state deploy
  applies **0 files instead of 152**. That is not only speed:
  `ALTER TABLE … IF NOT EXISTS` still takes ACCESS EXCLUSIVE when it changes
  nothing, and a queued ALTER behind a stale reader is exactly what froze the
  app on 2026‑08‑06 — asking for 150 locks we do not need was the exposure.
  - A file whose checksum changed after it was applied is **re‑applied and
    reported loudly**, not refused: every file here is idempotent and the
    repo's workflow assumes an edit re‑runs, so a hard failure the day the
    ledger arrived would have broken deploys for a legitimate fix. It is still
    a real bug class — a box that has not deployed since the edit is running
    the original — so it is named in the output.
  - `MIGRATION_REPLAY_ALL=1` restores the pre‑ledger behaviour for recovery.
  - **Verified on a real Postgres 16:** fresh replay applies 150 and creates
    139 tables; the second and third runs apply 0. Change detection and the
    replay switch both exercised.
  - **Now guarded by CI** (`pr-check.yml` → "Migration ladder replay"): the
    ladder is replayed from empty on every PR and the repeat must be a no‑op.
    "152 files are idempotent" was a claim about work nobody had checked
    together; it is a build step now. This also catches a migration that only
    fails on a FRESH database — the state a rebuilt stack or a restore lands
    in, and previously undetectable until someone hit it.
  - **Found while testing:** with `shopt -s nullglob`, a pattern matching
    nothing is *removed*, so `ls "$DIR"/[0-9][0-9]*_*.sql` collapsed to a bare
    `ls` — listing the CURRENT directory and feeding psql whatever it found
    (first symptom: a syntax error in `AGENTS.md`). A mis‑set `APP_DIR` would
    have done that in production. Now fails with the directory it looked in.
- **Still missing:** Alembic itself — autogenerate, down‑migrations, and
  auto‑apply on `docker compose up` (H12). The ledger is the piece Alembic
  needs anyway (`alembic_version` is the same idea) and the piece that paid
  for itself immediately, which is why BO‑6 was taken in this order.
- **Done earlier:** **✅ F5** resolves the duplicate #50; **✅ M7** writes `agent_run.started_at` at true run start.
- **Was missing:** 60+ raw numbered SQL files, no ledger/down‑migrations, not auto‑applied on `docker compose up` (H12).
- **Why needed:** At 60+ files with hand‑idempotency and no ledger, a migration incident is a matter of time; a fresh stack silently lacks most tables.
- **Dependencies:** Alembic; a one‑time baseline of the current schema (`schema.generated.sql` exists as a start).
- **Approach:** Adopt Alembic (autogenerate baselined against `schema.generated.sql`), run it in `lifespan`/entrypoint, keep the raw files as historical. Add a CI check for unique numeric prefixes until then.

### BO‑10 — Consolidate DB access to one engine/pool *(P2)* ✅ **CLOSED 2026‑08‑06**
- **Closed (2026‑08‑06).** Every async caller now resolves to ONE engine and ONE pool, and `acb_audit.record()` no longer blocks the loop. What actually shipped, and the two places it departs from the "Approach" line written below in July:
  - **The seam lives in `packages/acb_common/acb_common/db.py`, not in `acb_graph`.** `acb_graph` was the wrong home twice over: the gateway does not depend on it (nominating it would drag pgvector/AGE into a process that needs neither), and its own engine is **sync**. `acb_common` is the one package every service and every `acb_*` library already imports. `gateway/db.py` remains as a re-export because that is the import path the route packages use.
  - **`acb_auth.access` is why the seam had to leave the gateway.** It resolves a member's permissions from Postgres *on the request path*, runs inside the gateway process, and cannot import `gateway`. While the seam lived in `gateway/db.py` the gateway had two pools no matter how many route packages were converted — so "one engine" was unreachable by converting routes alone. Its engine had also never carried the connect-phase or `idle in transaction` bounds added after the 2026‑08‑06 outage; it inherits both now.
  - **Converted:** the six remaining route packages (`admin`, `apps`, `email`, `notes`, `whatsapp`, `workflows`) joining `tasks` and `crm`, plus `acb_auth.access`. Each kept its historical `get_db` / `_get_db` / `_get_session_factory` name as a re-export, so ~50 call sites and every `monkeypatch.setattr(<sibling>, "_get_db", …)` in the test suite are untouched. Verified live: nine consumers, one engine object, real queries.
  - **Pool ceiling: 30** (`settings.db_pool_size` 10 + `db_max_overflow` 20, both now tunable), unchanged from the pre-consolidation seam. Deliberately *not* raised to the old sum: the twelve pools summed to ~165 connections from one process against a stock `max_connections` of 100 that Langfuse, LiteLLM and the ingestion services also draw from — a budget that could not be spent, only exceeded.
  - **`dispose()` in the lifespan was considered and rejected**, contradicting the July approach line. The pool's lifetime *is* the process's; a dispose seam is a way to close connections other in-flight handlers are still using. BO‑9's "nothing disposes anything" observation is correct about the fact and wrong about the remedy for this engine.
  - **`acb_audit.record()`** keeps its sync signature (25-odd call sites, most of them sync) and dispatches the write to `asyncio.to_thread` **only when called from a running event loop**; sync callers still write inline, which is what they expect. `acb_audit.drain()` is awaited last in the gateway lifespan, so shutdown cannot cancel an in-flight row — without it, non-blocking would have been a regression against the old behaviour, where the write completed before the handler returned.
  - **Ratchet:** `tests/unit/test_db_engine_seam.py` fails the build on a new `create_async_engine` call site (AST-parsed, not grepped — every one of these modules mentions the name in prose saying it does *not* call it) and separately fails when an allowlist entry stops creating an engine, so the allowlist cannot rot into blanket permission. Plus `tests/unit/test_audit_non_blocking.py` (loop not blocked, write genuinely off-thread, sync callers still inline, drain waits, drain is bounded).
- **Still open, deliberately:** `packages/acb_graph/acb_graph/db.py:32`'s **sync** `create_engine`. It serves a different (sync) caller set — including `acb_audit`'s own write — and folding it in is an `acb_graph` rewrite, not this ticket. The four `email_ingestion` engines also stay: separate process, per-run lifetime, disposed when the run ends. Both are recorded in the test's allowlist with those reasons.
- **Done earlier (Session 2, 2026‑07‑13):** **every** engine now bounds the CONNECT phase so a slow/unreachable DB can't hang callers — `settings.db_connect_timeout` (default 10s) on `acb_graph.get_engine()` (`ccccdc8`, live in prod), the two gateway asyncpg engines (`1684e1a`), and the four `email_ingestion` async engines (`1ff6c0d`, local, unpushed) via `connect_args={"timeout": …}`. This makes `acb_audit.record()`'s "never block the caller" guarantee real against a hung connect. Test: `tests/unit/test_db_connect_timeout.py`.
- **Missing — the "three+" above was written in July and is now materially wrong; re‑measured 2026‑08‑03.** It is **12 `create_async_engine(...)` call sites across 10 modules**, plus a 13th **sync** `create_engine` in `acb_graph/db.py:32`:
  | Module | Sites | Shape |
  |---|---|---|
  | `packages/acb_auth/acb_auth/access.py:69` | 1 | cached `_ENGINE` singleton |
  | `gateway/routes/admin/_common.py:62` | 1 | cached `_ENGINE` singleton |
  | `gateway/routes/apps/_common.py:89` | 1 | cached `_ENGINE` singleton |
  | `gateway/routes/email/core.py:404` | 1 | cached `_ENGINE` singleton |
  | `gateway/routes/notes/core.py:160` | 1 | cached `_ENGINE` singleton |
  | `gateway/routes/tasks/core.py:161` | 1 | cached `_ENGINE` singleton |
  | `gateway/routes/whatsapp/core.py:135` | 1 | cached `_ENGINE` singleton |
  | `gateway/routes/workflows/core.py:64` | 1 | cached `_ENGINE` singleton |
  | `email_ingestion/inbound.py:273` | 1 | per‑call, disposed at `:288` |
  | `email_ingestion/scheduler.py:142, 527, 560` | 3 | per‑call, disposed at `:424`/`:540`/`:590` |
  | `packages/acb_graph/acb_graph/db.py:32` | (1 sync) | `create_engine`, a different flavour again |
  **The eight cached singletons are never disposed** — repo‑wide, the only `engine.dispose()` calls are the four `email_ingestion` per‑call engines cleaning up after themselves, and nothing in the gateway lifespan disposes anything (BO‑9). Also still open: sync `acb_audit.record()` blocks the async loop (H11) — connect_timeout bounds the hang but the call is still synchronous.
  *(The table above is the 2026‑08‑03 measurement, kept as the record of what was found. Every async row in it is closed as of 2026‑08‑06 — see the top of this section for what replaced them and why the July approach line was not followed literally.)*
- **Why it moved up the list:** the count grew by *one engine per app* — `notes`, `whatsapp`, `workflows` and `apps` all arrived with their own. This is the only foundation item whose cost **compounds per app**, so it is the one to fix before the next app rather than after (see `work_plan.md` §2's "Can we go app by app?" block, exception 3).

### BO‑11 — Decide `acb_schemas`: wire in or delete *(P2)* ✅
- **Done:** deleted the package (0 production importers, drifted from the ORM — H10). Removed its 7 `pyproject` dependency declarations + `tool.uv.sources` entry, the smoke‑test import, and the stale "wire/API surface" comment in `acb_graph/models.py`; re‑locked. Bonus: this exposed a latent under‑declared dependency — `orchestrator/triage/schema.py` uses pydantic `EmailStr` (needs `email‑validator`) but only got it transitively via `acb_schemas`; now declared explicitly as `pydantic[email]` on the orchestrator.

### BO‑21 — Activate memory by default + local‑embeddings fallback *(P2)* ☐ *(new — competitive‑informed, CH‑6)*
- **Missing:** all three memory layers are real code but **default‑OFF and inert out of the box** — `mem0_enabled=False`, `graphiti_enabled=False` (both in `packages/acb_common/acb_common/settings.py`; line numbers drift — search the setting names). Worse, `/v1/embeddings` returns a **zero‑vector** when `OPENAI_API_KEY` is unset (BO‑5 made this warn loudly, M13), so even if mem0 were enabled without an embeddings provider it would store facts with **no usable semantic search**.
- **Why needed:** Persistent cross‑session memory is a headline capability we advertise but ship disabled; a platform whose memory does nothing until an operator finds two hidden flags + an embeddings key is effectively memory‑less in practice.
- **Dependencies:** a local‑embeddings path (e.g. a small sentence‑transformer / `fastembed` served via the gateway, or an Ollama embeddings model) so semantic search works without a cloud key; `acb_memory` clients (exist).
- **Approach:** (1) Provide a **local‑embeddings fallback** wired into `/v1/embeddings` so the zero‑vector landmine is gone. (2) Flip mem0 on by default once (1) lands (graphiti stays opt‑in — it needs Neo4j). (3) Add a **human‑readable memory layer** (a curated `MEMORY.md`‑style artefact per subject) so stored memory is auditable, not just an opaque vector table.
- **Competitive ref (CH‑6):** **Hermes** memory works day one — SQLite + FTS5 full‑text over past sessions + a human‑readable `MEMORY.md` the agent curates + Honcho user‑modeling — one memory across all channels. The lesson: a *simple always‑on auditable* memory beats a *sophisticated disabled* one. See `specs/competitive_hardening_2026-07.md`.


### BO‑22 — Platform semantic‑search service *(P2)* ☐ *(new — requested 2026‑07‑30, Workflows app)*
- **Missing:** every surface that needs "find by meaning" either rolls its own or goes without. Memory has mem0's private embedding path (BO‑21); the Workflows capability catalog ships **keyword‑only search by explicit decision** (`gateway/routes/workflows/search.py` — an embedding‑backed variant was built and then deliberately removed in favour of this item); email/notes/tasks search is lexical. There is no shared embed‑and‑retrieve seam.
- **Why needed:** Semantic search is a platform capability, not a per‑app feature — N apps each bolting on their own embeddings means N index tables, N sync loops, N provider‑key fallbacks, and rankings that disagree. One service (index + query API in a shared package, pgvector‑backed, content‑hash keyed sync) lets the workflow palette/copilot, email, notes, tasks, and App Workshop all rank by meaning consistently — and BO‑21's local‑embeddings fallback makes it work without a cloud key.
- **Dependencies:** BO‑21 (a real `/v1/embeddings` path with local fallback — kills the zero‑vector landmine first); pgvector (present); a home in `packages/` (e.g. `acb_search`) per Place‑Before‑Building.
- **Approach:** (1) Land BO‑21's embeddings path. (2) `acb_search`: `index(namespace, key, text, metadata)` + `query(namespace, text, k, filter)` over one pgvector table, hash‑keyed lazy re‑embed, hybrid keyword+cosine ranking with an honest keyword‑only degrade. (3) First consumer: the Workflows catalog search swaps its ranking backend behind the same API shape (`search.py` is written for exactly this swap). (4) Migrate email/notes/tasks search opportunistically.
- **Note:** until this lands, new apps needing search should copy the Workflows stance — deterministic keyword ranking, no private embedding stacks.


### BO‑23 — Backup, restore, and point‑in‑time recovery *(P0)* ◐ *(new — 2026‑08‑03, WS‑0 truth pass; tooling SHIPPED 2026‑08‑03, execution still owner‑gated)*

> **This is the largest uncovered risk on the platform, and it scales with app count.** Every app that ships adds tables whose only copy is one Postgres volume on one VPS. It is filed P0 rather than P2 because unlike every other item here, the failure mode is *unrecoverable* — there is nothing to fix afterwards.

**Update 2026‑08‑03 — tooling shipped, nothing is scheduled yet.** Runbook and
full rationale: `project-docs/specs/backup_and_restore.md`.

Done‑when 1 ✅ (`scripts/backup_db.sh`), 2 ✅ (`scripts/restore_db.sh`),
3 ✅ *(runbook is at `project-docs/specs/backup_and_restore.md`, **not**
`deploy/hostinger/RESTORE.md` — `plan-guard` blocks agent writes under
`deploy/`; the clause's intent, a verification step rather than only commands,
is met)*, 4 ✅ (`apply_migrations.sh` now takes a dump before replaying the
ladder and **fails closed** if it cannot), 5 ☐ and 6 ☐ *(both require writes
under `deploy/` — owner)*.

**Update 2026‑08‑07 — done-when 5 ✅ and 6 ✅-in-repo. One owner step left.**

- **5 ✅ Something runs automatically.** `deploy/hostinger/acb-backup.service`
  and `.timer` now exist (verbatim from §5 — the `deploy/` gate that blocked
  the earlier pass was not active), and `deploy.sh` syncs *every* unit in that
  directory into `/etc/systemd/system`, reloads on change, and `enable --now`s
  the timers on each deploy. Files only for `.service` units — restarting the
  gateway mid-deploy is the compose stack's job, not this loop's. This also
  fixes `acb-health-watchdog.timer`, which was in the repo and likewise
  depended on somebody having installed it by hand.
- **6 ✅ A restore has been executed — and keeps being.**
  `scripts/rehearse_restore.sh` runs the real round trip: seed a database with
  known rows, run `backup_db.sh --verify-restore` (the exact command the
  systemd unit runs), `DROP` the table, run `restore_db.sh`, and compare an
  **md5 of the restored rows against the originals**. That last comparison is
  the whole point — every other step passes against an empty database. It also
  asserts the live DB is untouched by a default restore, and that a truncated
  dump is *rejected*, because a verifier that passes on garbage is worse than
  none. Run here on a real Postgres 16, and **verified in both directions**: it
  exits 1 with `restored 0 rows, backed up 250` when the restore is sabotaged.
  Wired into `pr-check.yml` so it runs on every PR — "we tested it once in
  August" decays, and a `pg_dump` format change on a Postgres upgrade would
  otherwise break it silently.
- **What made either possible:** both scripts reached Postgres only through
  `docker exec acb-postgres`, so they could not run anywhere but the VPS —
  which is precisely why they had never been run. They now go through a two
  function `pg`/`pgi` seam with `PG_MODE=local` for a cluster reached over
  libpq. The VPS path is byte-for-byte what it was.

**Still ☐ — and it needs the owner, not an agent:**

- **One restore against a *production* backup.** The rehearsal proves the
  tooling is correct; it cannot prove that this box's dump contains what you
  think, that `/opt/acb/backups` has room, or that `BACKUP_REMOTE` is set to
  anywhere. That is §6 of the spec, and it is ten minutes with a terminal.
- **The gate note in the old §BO‑23 was right and worth keeping:** the
  prediction that `plan-guard` might block the intended paths was correct in
  the opposite direction from the one anticipated. Authoring `scripts/*_db.sh`
  was permitted; writing the systemd units under `deploy/` was not, and the
  live Caddy/VPS changes were refused a *second* time by the runtime
  classifier, independently of the hook.

**What is true today (each claim measured 2026‑08‑03, not inherited):**

1. **There is no data backup.** The only Postgres dump script in the repo is `scripts/dump_schema.sh`, and it runs `pg_dump --schema-only --no-owner --no-privileges` (`:52`) writing `infra/postgres/schema.generated.sql`. That is **structure with zero rows** — it exists so humans and agents can read the current schema shape in one file, and it is explicitly *not* a backup.
2. **There is no restore path.** Repo‑wide there is no `pg_restore`, no logical data dump, no `--data-only`, and no restore runbook. A grep for `pg_dump|pg_restore|pgbackrest|wal-g|barman` across `*.sh|*.yml|*.yaml|*.py` returns exactly two files: `scripts/apply_migrations.sh` and `scripts/dump_schema.sh`.
3. **There is no PITR.** `archive_mode`, `wal_level` and `archive_command` appear nowhere in `infra/` or `deploy/`; the Postgres compose service (`infra/docker-compose.yml:34-37`) mounts one named volume, `acb-postgres-data`, with default settings.
4. **The only backup that exists is outside the repo and outside our control.** `deploy/hostinger/README.md:115`: *"Hostinger takes weekly backups of the whole VPS automatically (included in plan). For Postgres‑level point‑in‑time recovery later, add `pgbackrest` or a `pg_dump` cron job."* Honest, but it means the worst case is **up to seven days of data loss**, from an image whose restore has never been exercised.
5. **Migrations auto‑apply on every deploy, forward‑only.** `scripts/apply_migrations.sh` replays every numbered file from `02_` upward, in `sort -V` order, through `psql -v ON_ERROR_STOP=1` (`:59-74`), exiting non‑zero on the first failure. **140** files are replayed today (**142** numbered files on disk; `00_`/`01_` are initdb‑only). There is no ledger, no down‑migration, and no dry run. A migration that is idempotent in intent but not in fact takes the deploy down mid‑ladder with the database in a partially‑migrated state — and item 1 means there is nothing to roll back to.
6. **Redis has no persistence configured either.** `infra/` sets no `appendonly`, which `work_plan.md`'s WS‑4 row already records from the other direction: BO‑20b's retry counter *"survives a gateway restart but not a Redis one"*.

**Done when:**

1. `scripts/backup_db.sh` exists: a **data‑inclusive** `pg_dump -Fc` of the application database to a timestamped file, with the same `.env`/container‑name resolution shape as `dump_schema.sh` and `apply_migrations.sh` (so the three are operationally consistent), plus a retention sweep and a non‑zero exit on any failure.
2. `scripts/restore_db.sh` exists and is the **documented inverse** — `pg_restore` into a named database, refusing by default to target the live one without an explicit `--force`‑style flag.
3. A runbook (`deploy/hostinger/RESTORE.md` or a section in that README) states, in order: how to take an ad‑hoc backup before a risky deploy, how to restore into a scratch database, how to verify the restore (a row‑count or checksum assertion against a known table), and how to cut over. **A backup nobody has restored is not a backup** — the runbook must contain the verification step, not just the commands.
4. A pre‑migration hook: `apply_migrations.sh` (or the deploy step that calls it) takes a backup **before** replaying the ladder, or refuses to run without one. This is the clause that makes items 1 and 5 above stop compounding.
5. The `deploy/hostinger/README.md:115` "add it later" note is replaced by a pointer to what now exists.
6. Whatever ships is reflected in `infra/AGENTS.md` and `deploy/AGENTS.md` (DOX pass).

**Gate labels — read this before dispatching:**

- **AGENT‑SAFE:** writing the two scripts, the runbook, and the doc updates. These are files in the repo.
- **OWNER‑GATE:** *executing* any of it. Running a backup, running a restore, configuring WAL archiving, provisioning off‑box storage, and changing the deploy pipeline all reach the VPS and the production database — `work_plan.md` §6, and the `plan-guard.mjs` hook enforces it independently. An agent must write the tooling, verify it only by reading it, and hand execution to the owner. ⚠️ **Also verify the hook's posture on the paths you intend to write** before promising a PR: `plan-guard` blocks agent *commands* that reach the VPS, and an implementer should confirm rather than assume that authoring a new `scripts/*_db.sh` is permitted.
- **Explicit non‑goal:** do not "test" the restore against the live database. The scratch‑database path in done‑when 3 is the whole point.

**Dependencies:** none in code. It does *not* wait on BO‑6 (Alembic) — a backup is useful under raw numbered migrations and more useful, not less, because of them.


---

## D. Orchestration & runtime

### BO‑12 — Reconcile the runtime story (MAF vs Copilot) *(P1)* ✅
- **Done (path a):** `AGENTS.md` reconciled to reality — runtime line, Purpose, and non‑negotiables **#6/#9** now describe MAF as the PRIMARY native runtime and the Copilot SDK as the supported second runtime for interactive coworker chat (Tier 1.5, `/copilot/chat`, BYOK‑routed) + the mutation sandbox, rather than "MAF sole / Copilot sandbox‑only" (closed H6). The unused **`WorkflowBuilder`** import + its "used for pipelines" docstring claim were removed from `orchestrator/agents.py` (closed M2 — it was imported, never instantiated). `as_tool()` is genuinely used, so that claim stays.
- **Competitive ref (CH‑5):** Hermes's multi‑agent layer (orchestrator + isolated sub‑agents exchanging **typed result objects**, resource‑aware concurrency limits, Kanban dispatch) is more built‑out than ours on coordination mechanics — the reference when we finally instantiate the Workflow engine and replace bare‑string sub‑agent handoffs (ties to HH‑7). See `specs/competitive_hardening_2026-07.md`.

### BO‑13 — Break up the executor monolith *(P2)* ◑
> ⚠️ **The line counts below are stale in the optimistic direction — re‑measured 2026‑08‑03 (WS‑0 truth pass).** `executor.py` is **5,010 lines** (`wc -l`), not 4,069, and `run_agent_stream` is **~1,942 lines** (`:2139` to the next top‑level `def` at `:4081`), not ~1,600. The four extractions below did happen and did work; the file has since **grown back past its pre‑extraction size minus 84 lines** because features kept landing in it. Read the July numbers as a record of what the extractions removed, not as the current state. **The honest headline: the extractions netted ‑84 lines against the original 5,094, and the residual function is larger than when the residual was written.**

- **Done this pass (behaviour‑preserving extractions, each verified green):** the 5,094‑line file was taken down to **4,069 lines** *at the time of that pass* via four cohesive‑concern extractions, each re‑exported from `executor` so no importer changed:
  - `orchestrator/_todo_tracker.py` — todo‑SQL parsing.
  - `orchestrator/_copilot_session.py` — Copilot permission handler + infinite‑session policy.
  - `orchestrator/_tool_injection.py` — platform tool injection + system‑prompt addendum (~630 lines, the biggest cohesive concern).
  - `orchestrator/_model_resolution.py` — BYOK model resolution.
- **Regression net (`tests/unit/test_run_agent_stream_e2e.py`):** drives `run_agent_stream` end‑to‑end with mocked agents/loader (no git clone, no LLM, no Redis) and now covers BOTH tiers:
  - **Tier‑2 batch:** envelope contract (`RUN_STARTED` first → text streamed → `RUN_FINISHED` terminal), run_id/thread_id propagation, agent‑exception → `RUN_ERROR` (not a crash).
  - **Tier‑1 native streaming:** a mock agent that yields MAF‑shaped `run(..., stream=True)` updates → asserts the `TEXT_MESSAGE_START/CONTENT/END` lifecycle and `TOOL_CALL_START/ARGS/RESULT` events (via the real event_translator).
  - **HITL parking (new this pass):** `resolve_user_input` (found / not‑found) and the full `_make_user_input_handler` round‑trip — emits the `user_input_requested` frame to the relay, parks a Future, and returns the answer once `resolve_user_input` fires. Locks the ask_user → prompt → resolve contract.
- **Residual:** the Tier‑1.5 Copilot‑SDK tier and the idle‑timeout / fall‑through control‑flow branches are still not covered (the Copilot/full‑stream branches can't be exercised on the Windows dev box — they hit the same multi‑point infra hang that deselects this file locally — so they need a Linux/CI‑run harness to add safely); and `run_agent_stream` is still one **~1,942‑line** function (`executor.py:2139-4080`, measured 2026‑08‑03 — the "~1,600" this bullet used to claim was a July number that has since grown).
- **Approach for the residual:** (1) extend the harness to the Copilot tier + HITL/idle branches. (2) THEN extract the native / Copilot / batch tiers behind a `Runtime` strategy interface — the `return`‑to‑end vs fall‑through‑to‑batch control flow is the delicate part, so it needs those branches covered first — and move HITL/session‑store/cleanup into collaborators, guarded by this net + the trajectory evals. (3) Ratchet the xenon absolute ceiling down from F.

### BO‑14 — Enforce the permission/risk model *(P1)* ◑
- **Done this pass:** **workspace‑path containment** shipped — `write_artifact`/`save_note`/`recall_notes` routed every caller path through a single `write_artifact.resolve_in_workspace` guard that fails closed on an embedded `..` or an absolute path resolving outside the workspace (previously `write_artifact` could write, and `recall_notes` could READ, arbitrary files). Also fixed a latent bug: `recall_notes` now applies the same `agent-data/` prefixing as `save_note`, so the documented `recall_notes("NOTES.md")` round‑trip actually works. 7 unit tests added.
- ~~**Missing:** the injected‑tool gate still can never deny (M5) and the destructive platform registry is empty.~~ **Both halves struck 2026‑08‑03 (WS‑0 truth pass) — verified false against the code:**
  - **The gate CAN deny.** `acb_skills/permission_policy.py::decide` (`:127`) returns `False` on two hard vetoes that run **before** the annotation lookup: `("shell_denied", …)` when the command text matches a deny pattern (`:166-169`) and `("write_out_of_workspace", …)` when a write resolves outside the agent workspace (`:171-176`). Both fail closed and a tool's own annotation cannot waive either.
  - **The registry is not empty.** `acb_skills/tool_annotations.TOOL_ANNOTATIONS` carries `install_dependency` with `"destructive": True`, with the rationale in place (it installs into the *shared* gateway venv).
- **The real residual, stated accurately.** `decide()` still **approves** annotated‑destructive tools — the `tool_destructive_defer` branch at `:193-197` returns `True` on purpose, deferring to each tool's own `request_confirmation`. The gap is that, per the annotation registry's own comment on `install_dependency`, **no tool in this codebase yet calls `request_confirmation` on its own behalf before running**, so "defer to the tool's confirmation" defers to a card that never fires. That is BO‑14's job. It stays deferred deliberately: forcing denials risks false‑blocking legitimate tool use across every agent, and it needs a product decision on which tools hard‑block plus the confirmation UX. **Do not read "the gate can never deny" anywhere; it is wrong. Read: the gate denies two things and defers the third.**
- **Approach for the residual:** annotate the genuinely destructive platform tools (`install_dependency`, outward‑write tools) as `destructive`, pass full call context (not just the name) to `decide`, and make `enforce` mode block destructive/out‑of‑policy calls with a real confirmation card.
- **Competitive ref (CH‑1):** Hermes ships an always‑on **hardline blocklist** (`rm -rf /`, fork bombs, `mkfs`, disk‑zeroing `dd`) that no mode can override, plus **fail‑closed timeout→deny** on the approval prompt — both worth adopting as the floor. NVIDIA **NemoClaw**'s key idea for OpenClaw is **out‑of‑process policy enforcement**: evaluate the gate *outside* the agent's own tool surface so a prompt‑injected agent can't route around it. See `specs/competitive_hardening_2026-07.md`.

### BO‑20 — Event‑bus consumer + durable job queue *(P1)* ◑ *(competitive‑informed, CH‑3)*

> **Verified against code on 2026-08-02.** This section was rewritten after the
> WS‑4 dispatch audit returned **NO‑GO**: the previous body had no acceptance
> criteria, no verification commands, no gate labels, pre‑restructure paths, and
> — decisively — rested on a premise that **stopped being true** when commit
> `e20ea830` (Workflows Slice 2) shipped the event‑sink registry. An implementer
> handed the old row would have built a second, parallel dispatch path for work
> that already has one.
>
> **✅ The owner decision (§BO‑20.0) was taken on 2026-08-02: Option A** — an
> in‑process supervised asyncio consumer started from the gateway lifespan.
> Nothing below is blocked on a decision any more. BO‑20f needed no consumer,
> no flag and no decision and shipped first; **BO‑20a is BUILT** (2026-08-02,
> pending review) with the consumer shipped **OFF** behind `INGESTION_CONSUMER`.
> **BO‑20b slice 1 (the `emit_event` strict mode) is BUILT 2026-08-03; its
> slice 2 and BO‑20c–e remain open.** ⚠️ **Slice 1 is *necessary but not
> sufficient*** — the only registered sink swallows everything, so slice 2 also
> owns a strict path in `workflows.triggers.dispatch_event` (scope grew
> 2026-08-03; see §BO‑20b's Blocker bullet). The item is
> ◑ as of **2026-08-03**: two and a half of six sub‑items built, the flag
> unflipped in every environment. The *reason* the row existed has changed twice now, so
> read "What is true today" before anything else.
> (`work_plan.md`'s WS‑4 State cell mirrors this split.)
>
> **Hardened 2026-08-02 after adversarial review** (that pass shipped no code;
> BO‑20f's code landed later the same day): BO‑20f no
> longer claims to unblock WS‑11 Slice 4 (it unblocks multi‑channel event
> triggers; Slice 4 needs BO‑20a–e + BO‑7); Option B's undefined dispatch step is
> stated; BO‑20b/d/e now prescribe their constants as literals so a "retry"
> ticket cannot close green with `MAX_ATTEMPTS = 1`. *(BO‑20b's prescribed
> `_backoff` was struck 2026-08-03: it contradicted that same section's
> `_RECLAIM_MIN_IDLE_MS`, and a prescribed literal does not help when nothing
> pins the **call site** — see the DECISION in §BO‑20b.)*

#### What is true today (each claim re‑verified against the tree 2026-08-02)

1. **Webhook → run is ALREADY wired for ClickUp, and it does not go through
   Redis.** `apps/services/ingestion/ingestion/sources/clickup/webhook.py::receive`
   verifies the HMAC (`:91`), best‑effort `enqueue`s to `ingestion:clickup`
   (`:102`, warn‑and‑continue if Redis is down, `:103‑106`), schedules inline
   normalisation for task events (`:110`), and schedules
   `emit_event("clickup", event_type, payload)` (`:114‑116`). The gateway
   registers `workflows.triggers.dispatch_event` into that sink registry at
   import time (`apps/services/gateway/gateway/main.py:1070‑1076`; the
   `register_event_sink` call is `:1074`), and
   `dispatch_event` (`apps/services/gateway/gateway/routes/workflows/triggers.py:40`)
   calls `start_run(...)` for every **published** workflow whose `kind='event'`
   trigger matches `(source, event_type)`.
   **The old body's "trigger no agent" was true when written and is false now.**
   It is struck, not softened.
2. **There is a second live webhook→run path.** The signed generic webhook
   `POST /agent/webhook/{source}` (`gateway/routes/agent.py:3428`) routes to a
   MAF agent *and* calls `dispatch_event` (`:3476‑3478`) — the two fan out
   independently from the same event. **BO‑20 must not add a third dispatch
   path**; it changes how events *reach* `dispatch_event`, nothing downstream.
3. ~~**Gmail and Zoho are stubs.**~~ **Closed 2026-08-02 by BO‑20f.** It was
   true as written: both receivers carried a `TODO`, only audit‑logged, and
   neither enqueued nor emitted — so "multi‑channel triggers" was blocked on
   these two receivers at least as much as on any consumer, while
   `TriggerPanel.tsx:266‑269` had been offering `gmail` and `zoho` in the
   event‑trigger source dropdown all along (a dead switch in the UI). Both now
   `enqueue` to `ingestion:{gmail,zoho}` and schedule
   `emit_event(source, event_type, payload)` on `BackgroundTasks`, in ClickUp's
   shape. **All three receivers now emit inline**, which is what makes BO‑20a's
   Q1 cutover a three‑receiver change (see BO‑20a). ⚠️ **Closed in code, not in
   any environment:** `zoho_webhook_secret` and `gmail_pubsub_token` are both
   `""` everywhere today, so both receivers 401 every push and the switch stays
   dark until an owner provisions the secrets **and** points the provider at the
   route — see the OWNER‑GATE on the BO‑20f ticket. Bonus findings recorded so
   they are not re‑discovered: the Zoho receiver had been returning **500 on
   every authenticated push** (structlog `event=` kwarg collision) until BO‑20f's
   first `TestClient` drive‑through exposed it, and the same collision in
   `clickup/webhook.py::_normalise_task` had been **dead‑lettering every
   successfully‑normalised task**.
4. ~~**The `ingestion:*` streams are write‑only.**~~ **Half‑closed 2026-08-02 by
   BO‑20a.** It was true as written: `xadd` was the only stream verb in the
   ingestion package and `xreadgroup` / `xgroup` / `xack` appeared **nowhere in
   the repo** (the only `xread` callers being unrelated transports —
   `gateway/room_stream.py:133,161`, `orchestrator/stream_relay.py:221,285,306`,
   `acb_common/activity.py:257`). `ingestion/consumer.py` now issues all three.
   ⚠️ **Still true in every environment:** the loop is gated OFF
   (`INGESTION_CONSUMER` unset everywhere), so the streams are *today* still
   capped at `maxlen=10_000, approximate=True` (`queue.py:46,72‑73`) and
   **trimmed unread**. The `$` group start means the flip does not change that
   for the already‑buffered entries either — see BO‑20a.
5. **`ingestion:dlq` is worse: written and never drained.** `enqueue_dlq`
   (`queue.py:79`) is called from exactly two sites (`clickup/webhook.py:48`
   fetch failure, `:69` normalise failure). Nothing reads, displays, replays or
   alerts on it, so a ClickUp normalisation failure today is **silently
   invisible** and is eventually trimmed away.
6. **No job framework exists to reuse.** `uv.lock` contains no
   celery / arq / rq / dramatiq / taskiq — only `apscheduler` and `redis`.
   APScheduler is used **two** ways, and the second one matters here: as a cron
   *parser* inside the gateway's supervised loops
   (`gateway/routes/workflows/scheduler.py:7,59‑61` —
   `CronTrigger.from_crontab`, docstring "parser only — no scheduler process"),
   **plus one checked‑in but undeployed process**,
   `apps/services/ingestion/ingestion/scheduler.py`: `build_scheduler()`
   (`:101`) returns an `AsyncIOScheduler`, and `_serve()` (`:109‑130`) calls
   `sched.start()` then `await asyncio.Event().wait()`, run as
   `uv run python -m ingestion.scheduler` (docstring `:3‑7`: *"Run as a
   foreground process; long‑lived. In production deploy as a systemd service"*).
   **No systemd unit runs it** (see §8) — so the `python -m ingestion.X` shape
   Option B needs already exists in this exact package, *and* that package is
   already carrying one process that merged and does nothing. Both facts are
   load‑bearing in §BO‑20.0. (`workflows_app.md:226` D6's "no APScheduler
   *process*" is the **gateway** rule; it is not a repo‑wide fact, and D6's own
   "already in the dependency tree via ingestion" is why.)
7. **In‑process supervised asyncio loops are the established shape.** Five are
   wired into the gateway lifespan — email sync (`main.py:230`), WhatsApp
   enrichment (`:253`), tasks provider‑sync (`:265`), calendar auto‑rollover
   (`:275`), workflow schedule scanner (`:294`) — and **all five** are explicitly
   stopped on shutdown (`:324`, `:333`, `:340`, `:347`, `:354`; the cited lines
   are the `await stop_*()` calls, as the start lines are the `await start_*()`
   calls). **BO‑20a made it six** — the ingestion consumer starts at `:307` and
   stops at `:364`, which is why the shutdown line numbers here moved (they were
   `:311`–`:341` before that PR; the start lines did not move). How many actually
   *run* is data‑dependent, and **on an empty DB it is two**: only the calendar auto‑rollover (`routes/tasks/calendar.py:1543` — a
   single loop) and the workflow scanner start unconditionally, while email sync
   and tasks provider‑sync launch **one loop per enabled account row**
   (`routes/tasks/scheduler.py:181‑210`,
   `email_ingestion/scheduler.py:546,593`) and create nothing when there are no
   rows. WhatsApp enrichment is cost‑gated **off** unless `WHATSAPP_ENRICHMENT` is set
   (`routes/whatsapp/scheduler.py::enrichment_enabled` `:36`,
   `start_whatsapp_enrichment` returns `False` and creates no task) — an
   owner‑gated flip per `work_plan.md` §6. Its stop call still runs
   unconditionally, which is the shape a flag‑gated loop should copy.
8. **Redis needs no provisioning; a new *process* does.** `redis:7-alpine` is
   already a compose service (`infra/docker-compose.yml:44`) with the
   `acb-redis-data` volume (`:230`) and a healthcheck (`:61`).
   `deploy/hostinger/` carries exactly four checked‑in units
   (`acb-gateway`, `acb-workbench`, `acb-whatsapp-bridge`, `acb-health-watchdog`);
   `bootstrap.sh:123‑142` additionally generates `acb.service` for the Docker
   infra. **None of them is a worker.**
9. **The code already points here.** `gateway/main.py:280‑283` ("Workflow runs
   are in-process asyncio tasks (BO-20 pending)") and
   `gateway/routes/workflows/service.py:14` both cite this item by name.
10. ~~**Latent packaging defect this item must fix.**~~ **Closed 2026-08-02 by
    BO‑20a**, which added `"ingestion"` to
    `apps/services/gateway/pyproject.toml` `dependencies` and regenerated
    `uv.lock` (it now appears in the `name = "gateway"` package block and in its
    `requires-dist`). It was true as written: the gateway declared
    `email-ingestion` and `whatsapp-ingestion` but **not `ingestion`** — which
    is why the sink registration sits in a `try/except` (the `try` at
    `main.py:1070`, the `register_event_sink` call at `:1074`; comment
    "ingestion optional in some deploys"). It resolved only because the root
    umbrella `pyproject.toml` lists `ingestion` and every environment
    `uv sync`s the whole workspace. The `try/except` itself is left in place —
    it is now belt‑and‑braces rather than the load‑bearing conditionality it
    was.

**Why the item still matters:** retry via PEL reclaim (BO‑20b), a drainable dead‑letter
path (BO‑20c), per‑source rate limiting (BO‑20d), and bounded concurrency
(BO‑20e). Two of the six reasons are **closed**: **coverage** — "only one of
three receivers emits anything at all" — by BO‑20f, and **durability/replay** —
"the buffer is written and never read" — by BO‑20a, *in code*. The second one is
closed conditionally and the condition is unmet: with `INGESTION_CONSUMER` unset
in every environment the streams are still trimmed unread today.

#### Scope and non‑goals

**In scope:** BO‑20a–f below, and nothing else.

**Non‑goals (each of these belongs to a named owner — do not build it here):**
- **Not a general background‑job framework.** No new queue dependency; Redis
  Streams consumer groups + the existing `redis` pin only.
- **Not the Action Broker.** `apps/services/action_broker/action_broker/broker.py`
  is already the repo's durable queue — `pending_actions` with `enqueue` (`:187`),
  `list_pending` (`:238`), `approve` (`:332`), `reject` (`:320`), `submit`
  (`:353`). It queues outward **writes awaiting a human**; BO‑20 queues inbound
  **events awaiting a worker**. Two different queues on purpose. Do not merge
  them, do not re‑implement either inside the other.
- **Not workflow‑run durability.** Resuming a workflow run interrupted by a
  restart is `specs/workflows_app.md` **Slice 4** — verbatim at
  `workflows_app.md:217`: *"Slice 4 (post‑BO‑20/**BO‑7**): durable queued runs;
  sandboxed module execution; MCP exposure; retention policies"* — as
  `routes/workflows/service.py:14` already states. BO‑20 delivers the
  intake substrate; Slice 4 consumes it. It consumes **BO‑20a–e** specifically
  (durable queued runs = consumer + retry + DLQ), so BO‑20f alone does not
  release it — see the Tickets sequence below.
- **Not a new inbound channel.** Slack/Telegram ingress is WBS 3.3 / CH‑4.
- ~~**Not a change to `dispatch_event`.**~~ — **QUALIFIED 2026‑08‑03. BO‑20b
  slice 2 now owns a narrow, additive change to it.**
  **DECISION (agent‑proposed 2026‑08‑03, owner may overrule):** `dispatch_event`
  gains a keyword‑only strict path, because without one a strict `emit_event`
  has nothing that can fail — `dispatch_event` is the **only** sink production
  registers (`main.py:1074`) and it swallows everything (`triggers.py:98‑104`),
  so `raise_on_error=True` is a no‑op on the real registry. The failure boundary
  is prescribed in §BO‑20b's Blocker bullet below (propagate the `_get_db`/query
  failure and `RunRejected`; **never** the per‑run execution failures, which are
  fire‑and‑forget by design at `service.py:226`).
  *Alternative rejected:* leave `dispatch_event` untouched and accept that the
  consumer cannot distinguish "dispatched" from "swallowed". That is not a
  smaller version of BO‑20b — it is BO‑20b delivering nothing: the retry/DLQ
  machinery would be dead code against the only sink that exists, with every
  test green, which is exactly the silent drop this item exists to abolish.
  **Still non‑goals, unchanged:** the trigger matcher (`event_trigger_matches`),
  `start_run` and the concurrency caps, and the agent‑routing half of
  `/agent/webhook/{source}` — which calls `dispatch_event` **directly**
  (`routes/agent.py:3476‑3478`) and must keep receiving today's best‑effort
  default (an HTTP receiver must never 5xx because a workflow sink failed). The
  strict path is opt‑in per call site, never the default.
- **Not BO‑9.** See the dependency resolution below.

#### BO‑20.0 — OWNER DECISION: the process model — ✅ **ANSWERED 2026-08-02**

> ## `BO‑20 = Option A (in‑process)`
>
> **Decided by the owner on 2026-08-02.** BO‑20a–e are dispatchable as written
> from that date; BO‑20a was built the same day. Option B is **rejected** — its
> analysis is kept below, in full, because a future reader deciding whether to
> move the consumer into its own process needs to know what was weighed and why
> it was refused, not merely that it was.

*Both options are stated with their consequences; the recommendation carried its
evidence; the acceptance criteria below are written for the chosen option.*

**Option A — an in‑process supervised asyncio consumer, started from the gateway
lifespan. ✅ RECOMMENDED → ✅ CHOSEN 2026-08-02.**
Code lives in `apps/services/ingestion/ingestion/consumer.py` (the package that
owns the producer), exposing `start_ingestion_consumer()` /
`stop_ingestion_consumer()` / `consumer_status()`, called from the gateway
lifespan beside the five loops already there — exactly the
`email_ingestion.scheduler.start_background_sync` precedent (a loop that lives in
a service package and is started by the gateway).
- *Consequence:* ships by ordinary merge; **no VPS action, no OWNER‑GATE for
  deployment.** It dies with the gateway and comes back with it. Multiple gateway
  workers cooperate rather than duplicate — that is precisely what a Redis
  consumer group is for.
- *Evidence:* `specs/workflows_app.md` §9 **D6** records this as the house
  style and says so about this very item: *"APScheduler's `CronTrigger` as a
  parser inside a supervised asyncio loop (the canonical gateway scheduler shape
  — no APScheduler process) … **Revisit under BO‑20.**"* Five such loops are
  already **wired** (see "What is true today" §7 for how many actually start).
  The canonical shape to copy verbatim is
  `gateway/routes/workflows/scheduler.py` (`_scheduler_loop` `:272`,
  `start_workflow_scheduler` `:289`, `stop_workflow_scheduler` `:300`,
  `scheduler_status` `:309`).

**Option B — a separate `python -m ingestion.worker` process. ❌ REJECTED
2026-08-02** (what the previous §Approach literally specified, and what
`queue.py`'s own docstring still promises at `:11‑14`). *The reasons, kept
verbatim — this is the record of why, not a live option:*
- *Consequence:* an independent failure domain and an independent restart —
  genuinely better isolation. But it **cannot be shipped by an agent**: it needs a
  new systemd unit on the VPS plus a deploy change, and there is no worker unit
  today (see §8 above). **A PR that ships `worker.py` this way merges and does
  nothing** — dead code until someone SSHes in. This is not hypothetical: the
  same package already carries `ingestion/scheduler.py`, a complete
  `python -m` long‑lived APScheduler process (§6 above), checked in and deployed
  nowhere. The upside of that precedent is that B's *code* shape is cheap here;
  the downside is that it has already merged‑and‑done‑nothing once.
- 🚩 ***B is not answerable in one line as written — picking it costs a design
  round before BO‑20a can dispatch.*** BO‑20a's core mechanism is "hands it to
  the sink registry", and its done‑when asserts delivery to a sink registered via
  `register_event_sink`. But `_SINKS` (`event_hooks.py:23`) is a **module‑level
  list in the registering process**, and the only registration of a real sink
  happens at **gateway import time** (`main.py:1070‑1076`). A separate
  `python -m ingestion.worker` starts with `_SINKS == []` — it would
  `XREADGROUP`, `XACK`, and dispatch to **nothing**. So under B the *dispatch*
  step is not "unchanged", it is **undefined**, and the owner is implicitly
  choosing one of three mechanisms, none of them free:
  1. **The worker registers `dispatch_event` itself.** Inverts the layering
     `event_hooks.py`'s docstring exists to prevent ("without ingestion importing
     upward", `:1‑9`) and requires `gateway` in
     `apps/services/ingestion/pyproject.toml` `dependencies`, which today lists
     only fastapi / uvicorn / httpx / redis / apscheduler / acb‑common /
     acb‑graph.
  2. **The worker POSTs back into the gateway.** A new authenticated internal
     endpoint — i.e. precisely the **third dispatch path** the non‑goals forbid.
  3. **The worker runs the workflow engine itself.** The engine lives in the
     gateway package (`gateway/routes/workflows/`); this is a package move, not a
     ticket.
- *Had the owner picked B:* BO‑20a's "start from the lifespan" half would have
  been replaced by a `__main__` entrypoint + a unit file + a `deploy.sh` install
  step **and** a written answer to the dispatch question above; **the deployment
  half of BO‑20a–e would have become OWNER‑GATE**; and **BO‑20a would have had
  to be re‑written before it was dispatchable at all**. The
  drain/retry/DLQ/limiter/concurrency logic and all of BO‑20f were unchanged
  either way — **the dispatch step was not**. That asymmetry is what decided it:
  A shipped by ordinary merge with the risk parked behind a flag, B bought a
  design round first.

**Not closed forever.** Choosing A does not assert that the consumer must live
in the gateway process for all time — it asserts that isolation is not worth a
design round and an undeployable PR *today*. Moving it later is a re‑opening of
this decision with a named dispatch mechanism attached (one of the three above),
not a refactor.

**Risk note that made Option A cheap, and still governs:** BO‑20a ships the
consumer **OFF** behind `INGESTION_CONSUMER` (default off), so merging it
changed nothing at runtime. Turning it on in prod is a **🔒 OWNER‑GATE** env flip
in the same family as `SKILLS_INDEX_ONLY` / `SKILLS_FAIL_CLOSED`, and is
**registered as one** in `work_plan.md` §6 (added 2026-08-02). It is not merely
"start a loop": the same flag cuts all three receivers over to enqueue‑only
(Q1 below), so it should be flipped when someone is watching, and reverted by
unsetting it — the receivers read it per request, so a revert takes effect on
the next webhook without a restart (the loop needs one).

#### Open question Q1 — what happens to the inline `emit_event` once a consumer exists? — **ANSWERED, and shipped in BO‑20a**

*Recorded so nobody decides it silently in a PR description.*
If the consumer emits to the sinks **and** the receivers keep emitting inline,
every event fires its workflows **twice**. So the consumer's arrival forces a
cutover: receivers become `enqueue`‑only and the consumer becomes the **only
provider‑webhook path into the sink registry** — precisely, the only caller of
`ingestion.event_hooks.emit_event`.
- ⚠️ **Not "the single dispatch path", full stop** — that phrasing was loose and
  is corrected here. `gateway/routes/agent.py:3476‑3478` calls
  `workflows.triggers.dispatch_event` **directly** from the signed generic
  webhook `POST /agent/webhook/{source}`; it does not go through the sink
  registry, it is a deliberate second path (see "What is true today" §2), and it
  is **untouched by the cutover**. The cutover is about the three provider
  receivers only.
- *Consequence to accept honestly:* before the cutover, if Redis is down
  provider events still dispatch (the enqueue is best‑effort — ClickUp's
  `try`/`except` at `clickup/webhook.py:101‑106`, and the same shape in
  `gmail/webhook.py` and `zoho/webhook.py`). After it, Redis down = events
  buffered nowhere and **dropped**. That is a real regression in one axis traded
  for durability/replay in another. BO‑20a makes it **loud rather than silent**:
  each receiver logs `<source>.queue.dropped` at warning when — and only when —
  the flag is on and the enqueue failed. It must **not** be "fixed" by falling
  back to an inline emit; that is double dispatch by another name.
- **Decision (shipped):** do the cutover, **gated on the same
  `INGESTION_CONSUMER` flag** — flag OFF ⇒ receivers emit inline exactly as
  before and the consumer never starts; flag ON ⇒ receivers only `enqueue` and
  the consumer is the sole emitter. One flag, one path, never both. Each
  receiver reads the flag **per request** (`consumer.consumer_enabled()`), which
  is what makes BO‑20a's "no double dispatch" done‑when checkable — see its
  acceptance E (three receivers × two modes).

#### Dependencies (resolved, not dangling)

- **BO‑9 (§B, ☐) is NOT blocking.** BO‑9's ingestion clause is "Redis opened
  per‑call in ingestion (`queue.py:48`)" — verified: `queue._client()`
  (`apps/services/ingestion/ingestion/queue.py:49‑51`) builds a fresh **sync**
  `redis.from_url` on every `enqueue`. A consumer needs a **long‑lived async**
  client to hold a blocking `XREADGROUP`; that is a different object, and the
  precedent for owning one already ships:
  `packages/acb_common/acb_common/activity.py:66‑76` (`_get_client` at `:66`,
  the pooled `from_url` at `:70‑75`; the previously‑cited `:54‑66` was stale and
  was corrected 2026-08-02) — a module‑level pooled
  `aioredis.from_url(..., max_connections=16, health_check_interval=30)` behind
  `_get_client()` (same pattern in `orchestrator/stream_relay.py` and
  `gateway/room_stream.py`). **BO‑20a reused that shape verbatim**
  (`ingestion/consumer.py::_get_client`). Consolidating the *producer's*
  per‑call sync client into a shared pool remains BO‑9's work — BO‑20a did not
  touch `queue.py`.
- **Redis** — already provisioned (`infra/docker-compose.yml:44`). No action.
- **Verified anchors** (all re‑checked 2026-08-02):
  producer `apps/services/ingestion/ingestion/queue.py`
  (`enqueue` `:54`, `enqueue_dlq` `:79`, stream constants `:40‑43`) ·
  sink registry `apps/services/ingestion/ingestion/event_hooks.py`
  (`register_event_sink` `:26`, `emit_event` `:37`) ·
  receivers `apps/services/ingestion/ingestion/sources/{clickup,gmail,zoho}/webhook.py` ·
  gateway wiring `apps/services/gateway/gateway/main.py` (the `try` at `:1070`,
  `register_event_sink(_wf_dispatch)` at `:1074`) ·
  dispatcher `apps/services/gateway/gateway/routes/workflows/triggers.py:40`
  (`event_trigger_matches` `:32`) ·
  MAF executor entry point **`apps/services/orchestrator/orchestrator/executor.py::run_agent` (`:1683`
  — the cited `:1640` was stale, corrected 2026-08-02)** ·
  canonical supervised loop `apps/services/gateway/gateway/routes/workflows/scheduler.py` ·
  hermetic test pattern `tests/unit/test_clickup_ingestor.py:158‑181`.

#### Tickets

**Sequence: f → a → b → c → (d, e).** BO‑20f needed no consumer, no owner
decision and no flag; it is what **multi‑channel event triggers** actually need,
and it **shipped 2026-08-02**. **BO‑20a shipped the same day**, once §BO‑20.0
was answered `Option A`. **BO‑20b is next; its slice 1 (`emit_event` strict
mode) shipped 2026-08-03 and its slice 2 (the loop change) is dispatchable** —
nothing in b–e waits on an *owner* decision any more; each waits only on its
predecessor. Slice 2 does carry **three** agent‑proposed engineering decisions
recorded in §BO‑20b (the retry mechanism; whether a timeout is a failure; and —
added 2026-08-03 — giving `dispatch_event` a matching strict path, which struck
a §BO‑20 non‑goal and **grew slice 2's scope**, because without it slice 1's
`raise_on_error=True` is a no‑op against the only sink production registers);
an owner may overrule any, but none blocks dispatch.

⚠️ **Neither BO‑20f nor BO‑20a unblocks WS‑11 Slice 4.**
`specs/workflows_app.md:217` defines Slice 4 as *"(post‑BO‑20/BO‑7): durable
queued runs; sandboxed module execution; MCP exposure; retention policies"* —
none of which Gmail/Zoho receiver parity delivers, and **durable queued runs
means a–e, not a alone**: without BO‑20b a failed dispatch is acked and lost,
which is the opposite of durable. **WS‑11 Slice 4 needs BO‑20a–e plus BO‑7.**
(Same statement as the "Not workflow‑run durability" non‑goal above; if these
two ever disagree, the non‑goal is right.)

---

**BO‑20f — Gmail + Zoho receivers reach ClickUp parity (enqueue + emit).** ✅ **BUILT 2026-08-02** *(code‑side; **inert in prod** — see the gate below)* · *no owner decision, no flag, no migration*
> **Shipped.** Both receivers now `enqueue` to their own stream and schedule
> `emit_event` on `BackgroundTasks`, mirroring `clickup/webhook.py` including
> warn‑and‑continue. `tests/unit/test_ingestion_receiver_parity.py` (22 tests)
> and `tests/unit/test_clickup_normalise_dlq.py` (4 tests) are green, and
> `test_clickup_ingestor.py` is untouched at 10 passed.
>
> ⚠️ **OWNER‑GATE — this closes the *code‑side* dead switch only; the feature
> fires in NO environment today.** `zoho_webhook_secret` and
> `gmail_pubsub_token` both default to `""`, and `_verify` / `_verify_bearer`
> return `False` on unset, so **every push 401s** — `xadd` never runs and
> `emit_event` is never scheduled. Neither key is in `.env.example` (writes to
> it are OWNER‑GATE under WS‑2 and are blocked by the plan‑guard hook) nor on
> the VPS, and no Zoho webhook / Pub/Sub subscription points at these routes.
> **Two owner actions, neither of which an agent can perform:**
> 1. Provision `ZOHO_WEBHOOK_SECRET=` and `GMAIL_PUBSUB_TOKEN=` (they map to
>    `acb_common/settings.py:100,106`) in `.env.example` and in the VPS env.
> 2. Point the Zoho webhook at `POST /webhooks/zoho?token=<secret>` and the
>    Pub/Sub push subscription at `POST /webhooks/gmail` with that bearer.
>
> Until both are done, a user who binds a workflow to `source=gmail` sees
> nothing, forever, with **no signal inside Metorite** — the failure is
> visible only in Google's / Zoho's own delivery dashboard. The fail‑closed
> posture (refuse everything when the secret is unset) is deliberate and must
> not be "fixed".
>
> Decisions the ticket left open, plus the latent defects the drive‑through
> exposed — all settled and pinned by tests:
> 1. **Empty / non‑object Gmail decode** (`_decode_envelope` → `{}` on a
>    dataless, malformed, or valid‑JSON‑but‑not‑an‑object push): **ack 200,
>    enqueue nothing, emit nothing.** Such a decode carries neither mailbox nor
>    `historyId`, so enqueueing it replays nothing and emitting it would fire
>    user‑visible workflow triggers with a payload no run can act on; a non‑2xx
>    would only make Pub/Sub retry a push that can never decode. `[1,2]`,
>    `null` and `"hi"` previously raised `AttributeError` → 500 for the same
>    reason the guard exists, so `_decode_envelope` now genuinely honours its
>    `-> dict[str, Any]` annotation (the `# type: ignore` is gone).
> 2. **A latent 500 in the Zoho receiver, found by the first test that drove it
>    through `TestClient` and fixed here** because acceptance is otherwise
>    unreachable: `_log.info("zoho.webhook", event=event, …)` collided with
>    structlog's own `event` message parameter, so **every authenticated Zoho
>    push raised `TypeError` and returned 500** — before this ticket added a
>    line. Now `zoho_event=`, the key `clickup/webhook.py` already uses.
> 3. **The same bug class, last instance in the repo, folded in:**
>    `clickup/webhook.py::_normalise_task` logged `event=event_type` *after* the
>    graph session had committed and the `task_normalised` audit row was
>    written, so the `TypeError` was caught by the enclosing `except` and **every
>    successfully‑normalised ClickUp task was logged as a failure and
>    dead‑lettered.** `test_clickup_ingestor.py` masks it (it stubs
>    `_normalise_task` wholesale), so `tests/unit/test_clickup_normalise_dlq.py`
>    drives the real function and asserts `enqueue_dlq.call_count == 0` on the
>    success path, with the failure paths pinned so it cannot pass by neutering
>    the DLQ. That file also carries an **AST guard over `apps/` + `packages/`**
>    asserting no logger call passes `event=` — empirically the only structlog
>    kwarg that raises.
> 4. **Zoho's `event` is coerced with `str(...)`.** It is a wire token (the
>    Redis Streams field and the `event_type` triggers match on); a push
>    carrying `{"event": {…}}` made the real `xadd` raise
>    `DataError: Invalid input of type: 'dict'`, which the receiver logged as
>    `zoho.queue.unavailable` — blaming Redis for a payload‑shape problem —
>    while the fan‑out still started a run.
> 5. **Both credential checks compare bytes.** `hmac.compare_digest` raises
>    `TypeError` on a non‑ASCII `str`, so `?token=%C3%A9` (and a non‑ASCII
>    bearer) returned **500 instead of 401** on a `PUBLIC_ROUTES` endpoint. It
>    failed closed, so this was noise, not a bypass.
>
> Original ticket text follows.

Give both stub receivers the two lines ClickUp already has: a best‑effort
`enqueue(...)` to their own stream constant, and a `BackgroundTasks`‑scheduled
`emit_event(source, event_type, payload)`. Mirror
`clickup/webhook.py:100‑116` exactly, including the warn‑and‑continue on a Redis
failure (`:103‑106`) — a provider webhook must never 5xx because Redis is down, or
the provider retries and makes the backlog worse.
- **Event‑type vocabulary is prescribed here, not invented in the PR** (it is
  user‑visible: workflow event triggers match on this string,
  `triggers.py::event_trigger_matches` `:32`):
  Gmail → source `"gmail"`, event type `"historyUpdated"` (the Pub/Sub push
  carries only `emailAddress` + `historyId`, `gmail/webhook.py:55‑56` — there is
  no provider event name to pass through), payload = the **decoded**
  `_decode_envelope` notification (`:54`), never the base64 Pub/Sub envelope.
  Zoho → source `"zoho"`, event type =
  the `event` value the receiver **already computes** at `zoho/webhook.py:49`
  (`payload.get("event") or payload.get("operation") or "unknown"`), payload =
  the parsed request body (`:43`), as ClickUp does.
- **Done when:** `tests/unit/test_ingestion_receiver_parity.py` drives each
  receiver through `TestClient` with a valid credential. **Fake only Redis** —
  `monkeypatch.setattr(queue, "_client", lambda: mock_redis)`, the
  `test_clickup_ingestor.py:158‑181` pattern, which works regardless of how the
  receiver imports `enqueue`. Use the **real** `event_hooks` registry with a
  recording sink registered via `register_event_sink`, torn down with
  `clear_event_sinks()` (`event_hooks.py:32`); **do not monkeypatch
  `emit_event`** — faking it would make the sink assertion vacuous, and the
  receiver imports it inside the function body (`clickup/webhook.py:114`) so
  patching the receiver module's attribute would not take effect anyway. Assert
  for **both** Gmail and Zoho: (i) exactly one `mock_redis.xadd`, to
  `STREAM_GMAIL` / `STREAM_ZOHO`, carrying the prescribed event type; (ii) the
  registered sink is invoked **exactly once** with `(source, event_type,
  payload)` equal to the prescribed source string, event type and payload above
  (`TestClient` runs `BackgroundTasks` before returning, so no sleep is needed);
  (iii) with `mock_redis.xadd` raising, the endpoint still returns **200**
  `{"status": "accepted"}` **and the sink is still invoked** — a Redis failure
  must not suppress the event fan‑out.
- **Done when:** an invalid credential still returns **401** for both receivers —
  the existing auth behaviour is unchanged (`gmail/webhook.py:51`,
  `zoho/webhook.py:41`).
- **Verify:** `uv run pytest tests/unit/test_ingestion_receiver_parity.py tests/unit/test_clickup_normalise_dlq.py tests/unit/test_clickup_ingestor.py -q`
  → the new files green **and** `test_clickup_ingestor.py` still exactly **10
  passed**, byte‑identical (the fence around the ClickUp path is behavioural:
  the `clickup_event=` fix in item 3 above is invisible to every assertion in
  that file).
- **Files:** `apps/services/ingestion/ingestion/sources/gmail/webhook.py`,
  `apps/services/ingestion/ingestion/sources/zoho/webhook.py`,
  `apps/services/ingestion/ingestion/sources/clickup/webhook.py` (item 3 only),
  `tests/unit/test_ingestion_receiver_parity.py`,
  `tests/unit/test_clickup_normalise_dlq.py`.

---

**BO‑20a — Consumer group + `XREADGROUP` drain loop.** ✅ **BUILT 2026-08-02** *(under §BO‑20.0 Option A; ships **OFF** behind `INGESTION_CONSUMER`)* · *the flag flip is 🔒 OWNER‑GATE*
> **Shipped.** `apps/services/ingestion/ingestion/consumer.py` declares the
> `cc-ingest` group on all three streams and drains them into the existing sink
> registry; the gateway lifespan starts it (`main.py:307`) and stops it
> unconditionally (`:364`); all three receivers are cut over behind the flag;
> `ingestion` is now a declared gateway dependency. Pinned by
> `tests/unit/test_ingestion_consumer.py` (**41 tests**, no Redis/DB/network).
> ⚠️ **Inert in every environment:** `INGESTION_CONSUMER` is unset everywhere, so
> the loop does not start and the receivers still emit inline — **dispatch‑identical**
> to before the PR until an owner flips it. *(Not "byte‑identical": each receiver
> now does one extra function‑body import of `ingestion.consumer` plus one
> `os.environ` read per request. Nothing observable changes; the wording is
> corrected because a claim that strong is either exact or it is decoration.)*
> **BO‑20b is the next ticket and needs no new *owner* decision** — its slice 1
> shipped 2026-08-03 and its slice 2 carries three agent‑proposed decisions
> (retry mechanism; timeout‑is‑a‑failure; a strict path in `dispatch_event`,
> which grew slice 2's scope) recorded in §BO‑20b.

`apps/services/ingestion/ingestion/consumer.py`: `XGROUP CREATE <stream>
cc-ingest $ MKSTREAM` per stream (idempotent — swallow `BUSYGROUP` **only**;
any other `ResponseError` fails the cycle rather than being hidden), then a
supervised `XREADGROUP GROUP cc-ingest <consumer-name> BLOCK … COUNT …` loop
across `ingestion:{clickup,zoho,gmail}` that decodes each entry
(`event_type` + JSON `data`, the shape `queue.enqueue` writes at `:66‑74`),
hands `(source, event_type, payload_as_dict)` to `event_hooks.emit_event` — a
**decoded dict, never the JSON string** — and `XACK`s it.
**`$` is deliberate, not an oversight:** the group starts at the stream tail, so
everything already buffered is **skipped** — the "audit buffer nobody reads"
(§4 above) stays unread at cutover and is trimmed as before. The alternative,
`0`, would replay up to 10 000 buffered entries per stream into a dispatch storm
of real workflow runs the moment the flag flips. Accept the skip; do not
"fix" it. A one‑off replay, if ever wanted, is BO‑20c's `replay_dlq` shape
applied by hand, not a startup behaviour. Lifecycle
`start_ingestion_consumer()` / `stop_ingestion_consumer()` / `consumer_status()`
copied in shape from `routes/workflows/scheduler.py:272‑313`, wired into the
gateway lifespan next to the existing five, **and stopped on shutdown** beside
all five existing stop calls — stopped **unconditionally**, exactly as
`stop_whatsapp_enrichment` is called even when its loop never started.

**Constants — prescribed by the dispatch audit, not PR‑author choices.** A
deliberate change to any of these is a doc change here, in the same way BO‑20b's
`MAX_ATTEMPTS` is:
- `_GROUP = "cc-ingest"`.
- `_BLOCK_MS = 5_000` — finite, so a cancelled task returns promptly instead of
  parking on a read that never times out.
- `_READ_COUNT = 8` — at or below BO‑20e's `INGESTION_MAX_CONCURRENCY = 8`, so
  that ticket bounds a batch this one already produces.
- consumer name = `f"gw-{socket.gethostname()}-{os.getpid()}"`, **not a
  constant**: BO‑20b's `XAUTOCLAIM` identifies a dead worker by its name in the
  PEL, so two workers sharing one name make a crashed worker's backlog
  unreclaimable.
- `_ERROR_BACKOFF_SECS = 1.0` *(added by the implementation, recorded here)* —
  the pause after a failed cycle. Without it a Redis outage turns the supervised
  loop into a hot loop, because `xreadgroup` raises immediately and no `BLOCK`
  elapses.
- `_DISPATCH_TIMEOUT_SECS = 30.0` *(added by the implementation, recorded here)*
  — the ceiling on **one entry's whole sink fan‑out**, applied as
  `async with asyncio.timeout(…)` around `emit_event`. **It is not retry
  machinery** (that is BO‑20b); it exists because this ticket changes the blast
  radius of a hung sink. Before the cutover a sink that never returns hangs one
  request's `BackgroundTasks` and the other two sources keep flowing; here one
  serial loop drains all three streams, so an unbounded `await` converts a
  per‑event hang into a **bus‑wide stall** — and a silent one, since
  `emit_event` swallows sink exceptions and it could never surface as
  `cycle_failed`. 30.0 is far above any legitimate sink: the registered one,
  `workflows/triggers.dispatch_event`, is DB‑bound (it inserts the run row and
  `create_task`s `_execute_run` at `service.py:226`; it never awaits the run
  itself), so this cannot truncate a workflow. A timed‑out entry is still
  `XACK`'d — interim BO‑20a semantics, below. Logged on its own key
  `consumer.dispatch_timeout`.

**Flag home — `consumer.py`, not `Settings`.** `INGESTION_CONSUMER` is read
through a pure `consumer_enabled(env: dict[str, str] | None = None) -> bool`
that reads `os.environ` **at call time**, truthy set `{"1","true","yes","on"}` —
a verbatim mirror of `gateway/routes/whatsapp/scheduler.py::enrichment_enabled`
(`:36`). It is deliberately **not** a `Settings` field: `get_settings` is
`@lru_cache(maxsize=1)` (`packages/acb_common/acb_common/settings.py:384`),
which freezes the flag for the process and makes it untestable per call. The
receivers call `consumer_enabled()` per request via a function‑body import, the
same shape as their `emit_event` import. (`settings.py` was on this ticket's
Files list until 2026-08-02; it was wrong and is struck.)

**Ack semantics are interim by design:** BO‑20a acks after dispatch **regardless
of outcome**. Honest `XACK` + retry + DLQ is BO‑20b. Its slice 1 shipped the
`emit_event(..., raise_on_error=True)` strict mode this loop needs to *observe*
a failure at all (2026-08-03) — `consumer.py` is unchanged by it and still acks
regardless of outcome; slice 2 is what changes that. ⚠️ Slice 1 alone is **not
enough** for the loop to observe anything: `workflows.triggers.dispatch_event`,
the only sink the gateway registers, swallows every exception, so slice 2 must
also give *it* a strict path. Do not pre‑build slice 2. One deliberate exception: an
entry whose `data` does not decode to a JSON **object** is logged
(`consumer.entry_undecodable`) and acked **without** dispatch — there is nothing
a sink could act on, and re‑delivering it forever would wedge the group.

**Three "enqueued but never dispatched" states exist in BO‑20a, not one.** The
first is the accepted §BO‑20 Q1 drop at the receiver (Redis down ⇒ the enqueue
fails ⇒ nothing to dispatch). The other two are consequences of `XACK` being
**unguarded** in `_dispatch_entry` while the loop only ever reads `">"`, never
`"0"` — they are recorded here so BO‑20b does not rediscover them:
1. **Ack failure mid‑batch.** A connection reset on `client.xack` propagates
   through `_drain_once` into the supervised loop's `except` → backoff. The
   entries **already returned by that `XREADGROUP`** (up to `_READ_COUNT` = 8
   per stream) were never dispatched, sit in the PEL, and this process will not
   re‑read them. The only log is `consumer.cycle_failed`.
2. **SIGTERM mid‑batch.** Same shape — and the restarted process has a new pid,
   so the stranded entries sit under a `gw-<host>-<pid>` name it never uses.

The unguarded `XACK` is **deliberate, not an oversight**: a raising `xack` means
Redis is gone, and swallowing it would hot‑loop against a dead server instead of
reaching `_ERROR_BACKOFF_SECS`. Both states are recoverable — BO‑20b's reclaim
pass (`XAUTOCLAIM` / `XPENDING` + `XCLAIM`) reclaims **any** consumer name in the
group, including a dead pid's — **but the window is finite**: `queue._MAXLEN`
(`queue.py:46`) trims each stream at 10 000 entries, and a trimmed entry is
beyond the PEL's reach for good. That is why BO‑20b's done‑when below requires
the reclaim pass to run **at startup**, not only on a periodic cadence — and,
since 2026-08-03, why it *also* requires the **periodic** pass to exist as a
prescribed cadence constant: state 1 above strands entries with **no restart**
at all, so a startup‑only reclaim never reaches them. A reclaim over a trimmed
entry surfaces as `XAUTOCLAIM`'s **third** reply element (deleted ids), which
BO‑20b must log rather than unpack away — that is the only report those events
ever get.

- **Done when (A — delivery + ack):** ✅ `tests/unit/test_ingestion_consumer.py`
  asserts, with the consumer's Redis client faked at `consumer._get_client` per
  the `test_clickup_ingestor.py:158‑181` pattern (**no Redis, no DB, no VPS**)
  and the **real** sink registry (a recording sink via `register_event_sink`,
  never a monkeypatched `emit_event`), that an entry returned by the fake's
  `xreadgroup` **in the shape the real `queue.enqueue` produces** is delivered as
  `("clickup", event_type, payload)` with `payload` a **dict** equal to what the
  producer encoded, and is then `xack`'d **exactly once** on `ingestion:clickup`
  with group `cc-ingest`. **The ack must be asserted to happen *after* the
  dispatch, on one shared ordered timeline** — the recording sink and the fake's
  `xack` append to the *same* list. Two independent lists satisfy every other
  word of this criterion with the `XACK` hoisted **above** `emit_event`, which is
  precisely the line BO‑20b edits.
  → `test_entry_is_delivered_to_the_sink_and_acked_once`,
  `test_read_uses_the_prescribed_group_and_constants`.
- **Done when (B — the group starts at the tail, on all three streams):** ✅
  `xgroup_create` is asserted for **each** of `ingestion:{clickup,zoho,gmail}`
  with `id="$"` and `mkstream=True`. *A test that does not pin `id="$"` does not
  close this ticket* — the difference between `$` and `0` is a quiet startup and
  a dispatch storm.
  → `test_group_created_on_every_stream_at_the_tail`,
  `test_ensure_groups_called_directly_pins_the_same_contract`.
- **Done when (C — idempotence):** ✅ a fake whose `xgroup_create` raises
  `redis.ResponseError("BUSYGROUP …")` does not fail startup (the entry after it
  still dispatches and acks), a non‑BUSYGROUP `ResponseError` is **not**
  swallowed, and a second `start_ingestion_consumer()` while running is a no‑op —
  one live task named `ingestion-consumer`, not two.
  → `test_busygroup_does_not_fail_startup`,
  `test_non_busygroup_response_error_is_not_swallowed`,
  `test_second_start_while_running_is_a_no_op`.
- **Done when (D — flag OFF):** ✅ with `INGESTION_CONSUMER` unset,
  `start_ingestion_consumer()` creates no task, `consumer_status()["running"] is
  False`, no Redis verb is issued at all, and the receivers still emit inline —
  `test_clickup_ingestor.py` stays **10 passed, unmodified** and
  `tests/unit/test_ingestion_receiver_parity.py` (BO‑20f) stays **22 passed**.
  → `test_flag_off_starts_no_task`, `test_consumer_enabled_truthy_set`,
  `test_consumer_enabled_reads_os_environ_at_call_time`.
- **Done when (E — no double dispatch, per receiver, per Q1):** ✅ with the flag
  ON, **each** of the three receivers is driven through `TestClient` with a valid
  credential and the sink is invoked **zero** times while `xadd` still happens
  exactly once; with the flag OFF, exactly once. Six assertions (3 receivers × 2
  modes). **The cutover is three receivers wide, not one**: BO‑20f gave Gmail and
  Zoho the same inline emit, so a ClickUp‑only test would leave the other two
  double‑dispatching.
  → `test_receiver_emits_inline_when_flag_off[clickup|gmail|zoho]`,
  `test_receiver_does_not_emit_when_flag_on[clickup|gmail|zoho]`.
- **Done when (F — flag ON + Redis down):** ✅ with `xadd` raising, each receiver
  still returns **200** `{"status": "accepted"}`, the sink is **not** invoked
  (the event is dropped — the accepted Q1 regression), and the drop is logged at
  warning on its own key `<source>.queue.dropped`, so it is loud rather than
  silent. The mirror case is pinned too: with the flag OFF, Redis down still
  dispatches inline and claims **no** drop. Do **not** "fix" the drop by
  re‑emitting inline.
  → `test_flag_on_redis_down_drops_the_event_but_says_so[…]`,
  `test_flag_off_redis_down_still_dispatches_inline[…]`.
- **Done when (G — supervision):** ✅ a sink that raises does not kill the loop
  (the next entry is still delivered and acked), a failing `xreadgroup` does not
  either, and `asyncio.CancelledError` propagates so `stop_ingestion_consumer()`
  returns — the same two guarantees `_scheduler_loop` (`:272‑286`) makes.
  The cancellation half needs **both** halves of a fix that is easy to get
  half-right: (i) assert `task.cancelled()`, not `task.done()` — a loop that
  caught `CancelledError` and returned normally is `done()` too; **and (ii) let
  the loop actually reach its blocking read before calling `stop`.** (ii) is
  load-bearing and is not obvious: cancelling a task that has never been stepped
  makes asyncio throw `CancelledError` in at the coroutine's *first instruction*
  — above the `try` — so the task is marked cancelled whatever the `except`
  clause does, and even `task.cancelled()` passes against a swallowing loop.
  Verified both ways against a deliberately-swallowing `_consumer_loop`: with
  the wait, red (`Task finished … result=None`); without it, green. A test that
  stops before the first read pins asyncio's `_must_cancel` fast path, not this
  loop.
  → `test_a_raising_sink_does_not_kill_the_loop`,
  `test_a_failing_read_does_not_kill_the_loop`,
  `test_cancellation_propagates_so_stop_returns`,
  `test_stop_when_never_started_is_a_no_op`,
  `test_undecodable_entry_is_acked_not_dispatched`.
- **Done when (G2 — a hung sink cannot stall the bus):** ✅ with
  `_DISPATCH_TIMEOUT_SECS` monkeypatched small and a registered sink that never
  returns, **both** queued entries still reach the recording sink, both are
  `xack`'d, and `consumer.dispatch_timeout` is logged **once per entry** — the
  regression this ticket would otherwise introduce is that one serial loop
  drains all three streams, so an unbounded `await emit_event` stalls **every**
  source, silently. Without the timeout this test hangs until `_drain`'s
  `wait_for` fires; it is a real pin, not a tautology.
  → `test_a_hung_sink_times_out_and_the_bus_keeps_draining`.
- **Done when (H — packaging):** ✅ `ingestion` appears in
  `apps/services/gateway/pyproject.toml` `dependencies` **and** in `uv.lock`'s
  `name = "gateway"` package block (both `dependencies` and `requires-dist`), and
  `uv run python -c "import ingestion.consumer"` succeeds — closing the silent
  `try/except` conditionality at `main.py:1070‑1076` (§10 above).
  → `test_gateway_declares_the_ingestion_dependency`,
  `test_uv_lock_records_ingestion_under_gateway`,
  `test_consumer_module_imports_and_exposes_its_lifecycle`.
- **Done when (I — the lifespan wiring is pinned, not just asserted in prose):**
  ✅ the "stopped **unconditionally**" contract stated above (and in
  `apps/services/gateway/AGENTS.md` §1) is covered by a test, or deleting the
  stop call leaves every other test in this file green. Text‑level over
  `main.py`, for the same reason the two packaging tests above are: importing
  `gateway.main` pulls in the whole app. Asserts both names appear, that the
  start precedes the `yield` and the stop follows it, and that the shutdown
  half contains no `consumer_enabled` guard.
  → `test_gateway_lifespan_starts_the_consumer_and_stops_it_after_yield`.
- **Constraint (kept):** no log call in `consumer.py` may pass `event=` —
  `tests/unit/test_clickup_normalise_dlq.py` carries an AST guard over `apps/` +
  `packages/` (`_SCANNED` at `:119`). Use `clickup_event=` / `zoho_event=` /
  `source=` style keys.
- **Verify:** `uv run pytest tests/unit/test_ingestion_consumer.py tests/unit/test_clickup_ingestor.py tests/unit/test_ingestion_receiver_parity.py tests/unit/test_clickup_normalise_dlq.py -q`
  → **77 passed** (41 + 10 + 22 + 4; the last three unmodified — that is the
  regression fence). The single‑file form this ticket used to carry is
  insufficient: it cannot see the Gmail/Zoho halves of the cutover.
  Plus `uv run ruff check --select F821,F601,F602,F502,F7,B006 apps/services/ingestion/ingestion apps/services/gateway/gateway/routes/workflows`
  → `All checks passed!`
- **Files:** `apps/services/ingestion/ingestion/consumer.py` (new),
  `apps/services/ingestion/ingestion/sources/{clickup,gmail,zoho}/webhook.py`
  (flag‑gated cutover), `apps/services/gateway/gateway/main.py`,
  `apps/services/gateway/pyproject.toml` + `uv.lock`,
  `tests/unit/test_ingestion_consumer.py` (new).
  ~~`packages/acb_common/acb_common/settings.py`~~ — struck; the flag lives in
  `consumer.py` (see "Flag home" above). `queue.py` is deliberately untouched
  (its per‑call sync client is BO‑9's).

---

**BO‑20b — Retry via PEL reclaim + honest `XACK` semantics + DLQ hand‑off.** 🔄 **slice 1 shipped, slice 2 open (scope grew 2026‑08‑03)** · ✅ **AGENT‑SAFE** · *after BO‑20a*
A failed dispatch **must not** be `XACK`'d — the entry stays in the group's PEL
and a reclaim pass (`XPENDING` for the counter, then `XAUTOCLAIM` with a
`min-idle-time`) re‑delivers it. After `MAX_ATTEMPTS` **deliveries** the entry is
written to `ingestion:dlq` with the four fields `queue.enqueue_dlq` writes
(`:79`) plus `times_delivered`, and `XACK`'d exactly once, so it leaves the PEL
and never re‑delivers. **"A failed dispatch" is the load‑bearing phrase:** the
consumer can only see one if *both* `emit_event` (slice 1, done) and the sink the
gateway registers (`dispatch_event`, slice 2) have a strict path — see the
Blocker bullet.

**Slice status (2026‑08‑03).** **Slice 1 — the `emit_event` half of the blocker
— is BUILT** (`raise_on_error`, keyword‑only, default `False`; pinned by three
tests in `tests/unit/test_ingestion_consumer.py` §J). `consumer.py` is
**untouched** by that slice and still acks regardless of outcome — BO‑20a's
interim semantics. **Slice 2 is everything else below** and is the open half.
⚠️ **Slice 2's scope GREW on 2026‑08‑03** (adversarial review): slice 1 is
*necessary but not sufficient* — the only sink production registers,
`workflows.dispatch_event`, swallows every exception, so `raise_on_error=True`
is a **no‑op on the real registry**. Slice 2 therefore also owns a matching
strict path in `dispatch_event`, with a prescribed failure boundary; see the
Blocker bullet below and the qualified non‑goal in §"Scope and non‑goals".
- **DECISION (agent‑proposed 2026‑08‑03, owner may overrule) — the retry
  mechanism is (b) PEL‑and‑reclaim, NOT an in‑loop sleep.** The two constants
  this ticket used to prescribe were mutually incoherent: a reclaim's
  `min-idle-time` of 60 s is the floor on the interval between two deliveries of
  the same entry and it **dominates every value** in the old `1, 2, 4, 8, 16 s`
  schedule, so under the reclaim model `_backoff` could not govern anything. The
  only model in which that schedule was reachable was
  `await asyncio.sleep(_backoff(n))` inside `_dispatch_entry` — which
  reintroduces exactly what BO‑20a added `_DISPATCH_TIMEOUT_SECS` to prevent:
  one serial loop drains all three streams, so five attempts against one poison
  entry would block **every** source for up to 5 × 30 s + 15 s ≈ **165
  contiguous seconds**. Under (b) the head‑of‑line cost of a poison entry stays
  exactly BO‑20a's — one `_DISPATCH_TIMEOUT_SECS` per delivery — and unrelated
  entries drain between attempts. One property falls out for free and one only
  looks like it does — together they are why this is not a close call, but read
  both qualifications before relying on either:
  - **The attempt counter is Redis's, so it survives a *consumer* restart.**
    `XPENDING`'s `times_delivered` is the counter. An in‑process
    `dict[entry_id, int]` resets on every restart, so a poison entry would loop
    forever and **never reach the DLQ** — this ticket's headline guarantee,
    defeated with every test green. (`XREADGROUP >` reports no delivery count
    and neither does `XAUTOCLAIM`; only `XPENDING` carries it. Budget one
    `XPENDING` per reclaim pass, read **before** the `XAUTOCLAIM` — see the
    done‑when that pins the sequence.)
    - ⚠️ **Qualified:** it survives a **gateway** restart, not a **Redis** one.
      `_ensure_groups` re‑runs whenever a cycle fails (`consumer.py:295‑297,
      304`) and `xgroup_create(..., id="$")` after a Redis flush or restart
      re‑creates the group **at the tail** — PEL, pending entries and every
      `times_delivered` gone, silently. `infra/docker-compose.yml:51` runs
      `redis:7-alpine` with a `/data` volume and **no `appendonly`** anywhere in
      `infra/`, so durability is default RDB save points only. Redis losing its
      state is an event‑loss window this ticket does not close and does not
      claim to.
    - ⛔ **`JUSTID` is FORBIDDEN in the reclaim call.** Redis's contract is that
      `XAUTOCLAIM … JUSTID` does **not** increment the delivery counter — which
      under this design *is* the entire retry mechanism: `times_delivered`
      freezes at 1, `MAX_ATTEMPTS` is never reached, no entry ever reaches the
      DLQ, **and every criterion below still passes**. It is also not
      self‑announcing: `redis-py` 7.1.1 maps `justid=True` → `parse_justid=True`
      → `parse_xautoclaim` returns `response[1]`, a *bare list of ids*, and with
      exactly three pending ids `cursor, entries, deleted = <bare id list>`
      **unpacks successfully** and silently mis‑binds — so the three‑element
      unpack done‑when is not a reliable tripwire for it. Never pass
      `justid=True`.
  - **`_RECLAIM_MIN_IDLE_MS = 60_000` is a necessary per‑ENTRY bound, and it is
    not what makes a reclaim safe today.** It exceeds `_DISPATCH_TIMEOUT_SECS`
    (30 s), the ceiling on how long **one** entry can legitimately be in flight,
    so a reclaim cannot steal the entry currently being dispatched. What
    actually makes the batch safe today is that **the loop is serial**:
    `_consumer_loop` is a single task (`consumer.py:317‑320`), `_drain_once`
    dispatches entries one at a time in reply order (`:282‑284`), and the
    reclaim pass is prescribed to run *at the top of a `_consumer_loop`
    iteration* — so while a batch is in flight **no reclaim runs in this process
    at all**, whatever the idle times are. (`deploy/hostinger/acb-gateway.service:13`
    starts uvicorn with no `--workers`, so there is one process.) The safety is
    therefore **accidental, and the arithmetic alone does not carry it**: the
    bound holds per entry, not per batch. Entry *k* of a `_READ_COUNT` batch has
    already been idle up to (k−1) × 30 s while queued behind its predecessors,
    so with 8 entries the last one can be idle ~210 s — well past 60 s — the
    moment anything else is able to reclaim concurrently. Two things would make
    that reachable, and both are planned: a **second gateway worker** (the group
    is explicitly "shared by every gateway worker", `consumer.py:93‑95`) and
    **BO‑20e**'s `INGESTION_MAX_CONCURRENCY`. Either one turns this into "same
    event, two runs". See the constraint added to BO‑20e.
  **Accepted costs** (all four; the first was the only one recorded before
  2026‑08‑03):
  1. **Retry latency is quantised by the reclaim cadence** — an entry that fails
     four times and succeeds on the fifth takes ~5 minutes, not ~15 seconds, and
     a permanently‑failing entry reaches the DLQ in ~6 minutes. The happy path
     is unaffected.
  2. **Per‑stream ordering is given up.** BO‑20a preserves it — `_drain_once`
     iterates the reply serially (`consumer.py:277‑284`). Under PEL‑and‑reclaim
     a failed `taskUpdated` for task T is re‑delivered 60–90 s later while the
     *next* `taskUpdated` for the same T dispatches immediately, so a **stale
     payload can start a workflow run after a fresher one**. For a trigger
     payload carrying mutable provider state that is a wrong outcome, not merely
     added latency. The rejected in‑loop‑sleep model preserved order; BO‑20e
     breaks it too. Accepted: provider payloads are already re‑fetchable by id,
     and the alternative costs a bus‑wide stall.
  3. **`times_delivered` counts DELIVERIES, not FAILURES** — so restarts burn
     retry budget on a healthy event. §BO‑20a state 2 strands a batch remainder
     in the PEL after a SIGTERM, and those entries were already counted at
     `XREADGROUP` time; a crash‑loop or a run of deploys consumes attempts with
     no sink ever rejecting anything, and the event lands in the DLQ during
     exactly the incident where you least want silent loss. Mitigation (not a
     fix): record `times_delivered` on the DLQ row so an operator can tell a
     poison entry from a churned one — see the DLQ done‑when.
  4. A third mechanism — retrying *off* the drain path — was considered and
     rejected as out of scope here: concurrent dispatch is BO‑20e
     (`INGESTION_MAX_CONCURRENCY`), and a private in‑process delay queue would
     duplicate the PEL while losing its restart durability.
- **The constants are prescribed here, not chosen in the PR** (same reason the
  event‑type vocabulary is prescribed in BO‑20f: without literals this ticket's
  done‑when is satisfiable with `MAX_ATTEMPTS = 1`, i.e. a ticket titled "retry"
  closing green with no retry):
  - `MAX_ATTEMPTS = 5` — module‑level in `consumer.py`. Read from `XPENDING`'s
    `times_delivered`, **never** from process‑local state.
  - `_RECLAIM_MIN_IDLE_MS = 60_000` ms — module‑level. Must be **> 0** (a
    reclaim with `min-idle-time=0` re‑claims entries the loop is still working
    on and hot‑loops) and **> `_DISPATCH_TIMEOUT_SECS × 1000`**, per the
    decision above.
  - `_RECLAIM_EVERY_SECS = 30.0` — module‑level. The periodic cadence: the loop
    runs a reclaim pass when this much wall time has elapsed since the last one,
    checked at the top of a `_consumer_loop` iteration (an iteration is
    ≤ `_BLOCK_MS` = 5 s even when idle, so no second task is needed). With the
    60 s min‑idle an entry becomes eligible for re‑delivery 60–90 s after its
    last one.
  - ~~`_backoff(attempt) = min(2.0 ** attempt, 60.0)`~~ — **struck, do not
    build.** Under (b) the inter‑delivery interval is set by
    `_RECLAIM_MIN_IDLE_MS` + `_RECLAIM_EVERY_SECS`; a backoff function would
    govern nothing, and a *defined but never called* one closed the old
    done‑when green. If a per‑entry delay is ever reintroduced, name it
    `_ENTRY_RETRY_*` — never `_backoff`, which reads as a sibling of the
    unrelated `_ERROR_BACKOFF_SECS` (`consumer.py:108`, which paces failed
    *cycles*, not failed entries).
  A deliberate change to any of these is a doc change here, not a PR detail.
- 🚩 **Blocker — half closed. Slice 1's `emit_event` change is NECESSARY BUT NOT
  SUFFICIENT; do not rebuild it, and do not read it as "the blocker is gone".**
  `event_hooks.emit_event` swallowed every sink exception by design ("a sink
  error never propagates back into a provider webhook response"), so a consumer
  calling it could never observe a failure. It now takes a **keyword‑only**
  `raise_on_error: bool = False`: the default is today's best‑effort behaviour
  unchanged (log `event_hooks.sink_failed`, run the next sink — a webhook must
  never 5xx), and `raise_on_error=True` propagates the **first** sink exception
  without logging and does not invoke the remaining sinks. Keyword‑only so the
  three receivers' three‑positional‑arg
  `add_task(emit_event, source, event_type, payload)` can never reach it. Slice
  2 calls it with `raise_on_error=True` from `_dispatch_entry`. Do **not** make
  the default strict; that would change provider‑facing behaviour.
  - ⛔ **The blocker moved one layer down — it did not close.** `emit_event` can
    now propagate a sink exception, but **the only sink production registers
    cannot raise one.** Traced end to end 2026‑08‑03:
    - `main.py:1074` registers exactly one sink, `dispatch_event` (imported as
      `_wf_dispatch`). Repo‑wide `register_event_sink` call sites: that line, the
      definition, and tests. Nothing else.
    - `triggers.py:45‑46` — docstring: *"Best‑effort and **never raises** — event
      delivery must not break the webhook receivers that call it."* The entire
      body sits inside `try:` / `except Exception as exc: _log.warning(
      "workflows.event_dispatch_failed", …)` (`:49`, `:98‑104`); `RunRejected` is
      separately swallowed per row at `:90‑95`. Only the trailing
      `if started: _log.info(...)` is outside the `try`.
    - The return value carries no signal either: `started` is `[]` both when no
      workflow matched and when the DB threw (`:48`, `:98`) — and `emit_event`
      discards it regardless (it returns `None`).
    **Consequence if slice 2 ships without the change below:** flag on, Postgres
    unreachable (or `MAX_CONCURRENT_RUNS = 8` saturated), `emit_event(...,
    raise_on_error=True)` **returns normally**, the loop reads that as success
    and `XACK`s — no retry, no PEL entry, no DLQ row. The event is gone, and the
    whole retry/DLQ suite is green, because the tests register a *raising* fake
    sink (as the `two_sinks` fixture does — `tests/unit/test_ingestion_consumer.py:720‑731`,
    §J) — a sink shape that does not exist in production.
- **Done when (slice 2) — `dispatch_event` gains a matching strict path**, per
  the qualified non‑goal in §"Scope and non‑goals" above. Keyword‑only
  `raise_on_error: bool = False`, same shape and same reasoning as
  `emit_event`'s: the default stays best‑effort for
  `/agent/webhook/{source}` (`routes/agent.py:3476‑3478`), and only the consumer
  opts in. **The failure boundary is prescribed here, not chosen in the PR** —
  an over‑broad strict path makes one failing workflow run poison the whole
  event, which is worse than the drop it replaces:
  - **PROPAGATE** — `await _get_db()` failing, the trigger `SELECT` /
    `.fetchall()` failing, and `load_version_serialized` raising. In all three
    the event was never matched against the triggers: nothing ran, so
    re‑delivery is exactly right.
  - **PROPAGATE** — `RunRejected` from `start_run`. It is raised at
    `service.py:193‑196` **before** the `workflow_runs` INSERT and before the
    `create_task`, so the run definitively did not start; its own message says
    *"retry shortly"*, which is precisely what the PEL gives it. Any other
    exception out of `start_run` (the INSERT or `commit` failing, `:201‑219`)
    propagates for the same reason — no row, no task.
  - **DO NOT PROPAGATE** — anything that happens inside `_execute_run`. It is
    launched fire‑and‑forget by `asyncio.get_running_loop().create_task(...)` at
    `service.py:226`, after the row is committed; `start_run` returns the
    `run_id` as soon as the task is scheduled, so these failures cannot reach
    `dispatch_event` and **must not** be made to. A node failing is a *run*
    outcome, recorded on the `workflow_runs` row; re‑delivering the event would
    start a **second** run of the same workflow on the same payload.
  - **DO NOT PROPAGATE** — `load_version_serialized` returning `None`
    (`triggers.py:72‑73`) or a trigger not matching. Those are legitimate
    non‑matches, not failures.
  - **DO NOT PROPAGATE** — `await db.close()` in the `finally` (`:96‑97`)
    failing after runs have started. The dispatch succeeded; log it, do not
    convert it into a re‑delivery.
  - **Partial dispatch:** raise **after** the row loop finishes, not at the
    first failing row, so the remaining matched workflows still get their
    chance; the raised error must name the workflows that already started
    (today's `started` list) so the log line — and the DLQ row, if it gets that
    far — records that the retry is knowingly duplicative. Per‑workflow
    idempotency is now part of the event‑trigger contract; see the sink‑contract
    note below.
- **Done when (slice 2) — the retry path is proved through the REAL sink, not a
  fake.** At least one test must register `gateway.routes.workflows.triggers
  .dispatch_event` itself via `register_event_sink`, make its `_get_db` fail
  (and, separately, make `start_run` raise `RunRejected`), and assert the entry
  is **not** acked. A suite that only ever registers a raising fake sink proves
  nothing about production, since production's sink cannot raise. Keep the fake
  sinks too — they pin `emit_event`'s own contract — but they are not sufficient
  evidence for this ticket.
- **Done when (slice 2) — "nobody listened" is distinguishable from "dispatched
  successfully".** With `_SINKS == []` a strict `emit_event` returns `None`,
  byte‑identical to a successful fan‑out. That state is reachable today, not
  hypothetical: the registration at `main.py:1070‑1076` is wrapped in
  `except Exception: pass` ("ingestion optional in some deploys"), so an import
  error inside the workflows package leaves the registry empty and the consumer
  acking events nobody consumed. Assert that with no sinks registered the
  consumer logs `consumer.no_sinks` (warning) and does **not** record the entry
  as dispatched.
  - ⚠️ **Open sub‑question for slice 2 to answer and record here (do not
    guess):** whether an empty registry **withholds** the `XACK` (→ re‑delivery,
    then DLQ after `MAX_ATTEMPTS`: loud and recoverable through BO‑20c's replay,
    during exactly the misconfiguration that caused it) or **acks with a
    warning** (→ the event is gone, but a gateway legitimately running without
    the workflows router does not fill the DLQ). Recommendation: **withhold** —
    an ack is unrecoverable, a DLQ row is not.
  - *Mechanism:* prefer an additive read‑only accessor in `event_hooks.py`
    (e.g. `sink_count() -> int`) over changing `emit_event`'s return type;
    `emit_event`'s signature and its `False` default are pinned by
    `test_raise_on_error_defaults_to_false_and_is_keyword_only` and must not
    move.
- **Sink contract (new, one line, applies from slice 2 on):** because
  `raise_on_error=True` stops at the **first** failing sink, a retry re‑runs
  every sink that already succeeded and still never reaches the ones after the
  failure. **Every sink must therefore be idempotent per `(source, event_type,
  payload)`** — that is now part of the registry's contract, not an
  implementation detail. It is free today (one sink), and it is the thing that
  breaks silently the day a second one is registered.
- **Done when:** `MAX_ATTEMPTS == 5`, `_RECLAIM_MIN_IDLE_MS == 60_000` and
  `_RECLAIM_EVERY_SECS == 30.0` are asserted against those literals, and the
  reclaim call is asserted to pass `_RECLAIM_MIN_IDLE_MS` (not `0`) and
  `justid=False`/omitted as its arguments. `_RECLAIM_MIN_IDLE_MS >
  _DISPATCH_TIMEOUT_SECS * 1000` is asserted too — that inequality is a
  **necessary** per‑entry condition, **not** what makes the batch safe (the
  serial loop is; see the decision above). Do not sell it as the latter in a
  comment.
- **Done when — the read sequence of a reclaim pass is pinned, in this order.**
  Per pass, per stream: **(1)** one
  `xpending_range(stream, _GROUP, min="-", max="+", count=<n>, idle=_RECLAIM_MIN_IDLE_MS)`
  → entries as dicts with `message_id` / `consumer` / `time_since_delivered` /
  `times_delivered` (`redis-py` `parse_xpending_range`); **(2)** then the
  `XAUTOCLAIM`. The order is load‑bearing and is why it is prescribed rather
  than left to the PR: `XPENDING` **before** the claim reports the
  *pre‑increment* count, after it reports the *post‑increment* one, and the
  observable DLQ threshold is **6 deliveries in one reading and 5 in the
  other** — a fake‑backed test passes either way, so nothing else pins it.
  With the prescribed order, the rule is: **an entry is DLQ'd when its dispatch
  fails and `times_delivered + 1 >= MAX_ATTEMPTS`** (the `+ 1` is the delivery
  the claim is about to make). `MAX_ATTEMPTS == 5` therefore means five
  deliveries total, and the test that asserts the literal must also assert the
  observed delivery count, or the literal and the behaviour it names drift by
  one.
- **Done when — the reclaim pass runs at STARTUP, before the first `">"` read**,
  not only on the periodic cadence, and a test asserts that ordering. *Reason
  (recorded from the BO‑20a review, §BO‑20a "Three enqueued but never dispatched
  states"):* BO‑20a strands a batch's remainder in the PEL on an `xack` failure
  or a SIGTERM mid‑batch, under the **previous pid's** consumer name — which
  only a reclaim reaches, and only until `queue._MAXLEN` trims the entry out of
  the stream. Without a startup pass, every restart while the flag is on adds to
  a backlog that nothing drains until the next periodic tick.
  - ⚠️ **Open sub‑question for this ticket to answer (do not guess):** at
    startup, entries stranded by a *fast* restart have been idle for less than
    `_RECLAIM_MIN_IDLE_MS`, so the min‑idle filter excludes exactly the case the
    startup pass exists for. Decide and record one of: **(i)** the startup pass
    uses a lower idle bound **restricted to consumer names that are not this
    process** (a dead pid cannot have work in flight), or **(ii)** it uses the
    same 60 s bound and accepts one periodic tick of latency.
    `_RECLAIM_MIN_IDLE_MS > 0` remains binding for the periodic pass either way.
    *(Relabelled (a)/(b) → (i)/(ii) on 2026-08-03 so it cannot be confused with
    the mechanism decision above, which owns the letters (a)/(b).)*
- **Done when — the PERIODIC pass exists and is driven by the loop**, not only
  the startup one. *Reason:* BO‑20a's "ack failure mid‑batch" state (§BO‑20a,
  state 1) strands entries **without a restart** — `_dispatch_entry`'s unguarded
  `xack` raises into `_consumer_loop`'s `except`, the loop continues under its
  own live consumer name, and it only ever reads `">"`. A startup‑only reclaim
  never fires for it, so those entries sit in the PEL until `queue._MAXLEN`
  trims them away. Assert it against a monotonic clock the test controls (never
  a real sleep): with `_RECLAIM_EVERY_SECS` monkeypatched small, a loop left to
  spin issues **more than one** reclaim call, each carrying
  `_RECLAIM_MIN_IDLE_MS`; with the clock frozen it issues no second one.
- **Done when:** with a faked Redis and a sink that raises on the first **4**
  deliveries and succeeds on the **5th**, the entry is `xack`'d **exactly once**
  and **never** written to `ingestion:dlq` — i.e. the retry path is exercised
  four times, not zero. Deliveries 2–5 arrive from the **reclaim pass**, not
  from an in‑loop retry: that is the mechanism decision above, and a test that
  loops inside `_dispatch_entry` would pin the rejected design. **The fake must
  surface the rising count through `xpending_range`, not `xautoclaim`** — an
  `XAUTOCLAIM` reply entry is `(id, fields)` and carries no counter at all, so a
  fake that attaches one there invents a Redis that does not exist and lets the
  implementation read the count from the wrong call.
- **Done when:** with a sink that always raises, after exactly **5**
  (`MAX_ATTEMPTS`) deliveries there is **exactly one** `xadd` to `ingestion:dlq`
  carrying `origin_stream="ingestion:clickup"` and the error string, followed by
  **exactly one** `xack` — and no further re‑delivery. The row should also carry
  `times_delivered` as a **fifth** field (accepted cost 3 above: the counter
  counts deliveries, not failures, so a crash‑loop can DLQ a healthy event —
  an operator needs to tell a poison entry from a churned one). The four fields
  `enqueue_dlq` writes stay **required**; BO‑20c's reader must tolerate extra
  fields rather than assume exactly four.
  - ⚠️ **The DLQ write must not call the sync `queue.enqueue_dlq` from the async
    loop.** It builds a fresh **synchronous** `redis.Redis` per call
    (`queue.py:49`, called at `:81`), which blocks the event loop and is
    invisible to the `consumer._get_client` fake every test in this file uses —
    so the assertion above would be untestable and the drain would stall on a
    network round trip. Use an `await client.xadd(STREAM_DLQ, …)` on the
    consumer's own async client replicating `enqueue_dlq`'s four fields
    (`origin_stream`, `event_type`, `data`, `error[:500]`) with
    `maxlen=queue._MAXLEN, approximate=True`, or `asyncio.to_thread`. `queue.py`
    stays off this ticket's Files list either way (its per‑call sync client is
    BO‑9's to consolidate).
- **Done when — a dispatch `TimeoutError` counts as a FAILED dispatch**, i.e. it
  is not `XACK`'d, it is re‑delivered, and it reaches the DLQ after
  `MAX_ATTEMPTS` like any other failure. **DECISION (agent‑proposed 2026‑08‑03,
  owner may overrule):** BO‑20a acks a timed‑out entry (`consumer.py:233‑240`
  then `:257`) only because it had no retry path to hand it to; from the event's
  point of view a timeout and an exception are the same fact — the workflow did
  not run — so acking it is a silent drop, which is the thing this ticket
  exists to abolish. Consequence to budget for: **BO‑20a's
  `test_a_hung_sink_times_out_and_the_bus_keeps_draining`
  (`tests/unit/test_ingestion_consumer.py:601`) must be rewritten by this
  ticket** — its `xack_calls` assertion inverts to "neither entry is acked" —
  while its other half, that the *next* stream still drains, is preserved
  unchanged and is the property that must not regress.
- **Done when — `XAUTOCLAIM`'s third reply element is logged, not dropped.** On
  `redis:7-alpine` (`infra/docker-compose.yml:51`) the reply is
  `[next-cursor, entries, deleted-ids]`; the third element is the ids whose
  stream entry was **trimmed away by `_MAXLEN`** while pending — the PEL record
  is deleted and those events are gone with no dispatch and no DLQ row. Unpack
  all three and log a count on its own key (e.g. `consumer.reclaim_trimmed`)
  so the loss is at least as loud as the accepted Q1 drop
  (`<source>.queue.dropped`). Note the failure mode of getting this wrong is
  **not** silence: `redis-py` 7.1.1 returns the raw 3‑element reply
  (`_parsers/helpers.py::parse_xautoclaim`), so the common two‑element idiom
  `cursor, entries = await client.xautoclaim(...)` raises
  `ValueError: too many values to unpack`, which `_consumer_loop` catches as
  `consumer.cycle_failed` — loud, but it wedges the **entire drain loop**, not
  merely the reclaim pass: the `try` at `consumer.py:294‑298` spans both
  `_ensure_groups` and `_drain_once`, so a `ValueError` raised by a
  top‑of‑iteration reclaim aborts the iteration **before `_drain_once` runs**,
  then sleeps `_ERROR_BACKOFF_SECS` and repeats — the bus stops draining
  entirely, at ~1 Hz, forever. Assert the three‑element unpack against a fake
  that returns a non‑empty third element.
  - **Done when — the reclaim pass is wrapped so its failure degrades to "no
    reclaim this cycle", not "no drain this cycle".** Its own
    `try/except Exception` (logging e.g. `consumer.reclaim_failed`, re‑raising
    `CancelledError`) inside the loop iteration, asserted by a test where the
    reclaim call raises and `_drain_once` still handles the batch. A reclaim is
    a recovery mechanism; it must never be able to take the primary path down
    with it.
  - ⛔ **Reminder, because this is the second place it bites:** do **not** pass
    `justid=True` to work around the three‑element reply. `redis-py` returns
    `response[1]` — a bare list of ids — which with exactly three pending ids
    unpacks into `cursor, entries, deleted` **without raising**, and `JUSTID`
    suppresses the delivery‑counter increment that the whole retry design rests
    on. See the mechanism decision above.
- ~~**Done when:** backoff is a pure function `_backoff(attempt) -> float`…~~ —
  **struck** with `_backoff` itself (see the decision above). The hole it left
  is closed by the periodic‑pass done‑when: that one pins a **call site**, which
  the old criterion never did — `_backoff` could be defined, satisfy all four
  asserted properties, never be called from the loop, and close green.
- ✅ **Done (slice 1):** `emit_event(..., raise_on_error=True)` propagates the
  first sink exception and skips the remaining sinks, while the default call
  remains swallow‑and‑log and returns `None`; the default is pinned as the
  literal `False` via `inspect.signature`. →
  `test_strict_emit_propagates_the_first_sink_error_and_skips_the_rest`,
  `test_default_emit_still_swallows_logs_and_continues`,
  `test_raise_on_error_defaults_to_false_and_is_keyword_only`. Regression fence
  held: `test_clickup_ingestor.py` **10 passed**,
  `test_ingestion_receiver_parity.py` **22 passed**,
  `test_clickup_normalise_dlq.py` **4 passed**, all unmodified.
- **Constraint (kept):** no log call in the touched files may pass `event=` —
  `tests/unit/test_clickup_normalise_dlq.py` carries an AST guard over `apps/` +
  `packages/` (`_SCANNED` at `:119`). Use `source=` / `event_type=` style keys.
- **Verify:** `uv run pytest tests/unit/test_ingestion_consumer.py tests/unit/test_clickup_ingestor.py tests/unit/test_ingestion_receiver_parity.py tests/unit/test_clickup_normalise_dlq.py -q`
  → **80 passed** after slice 1 (44 + 10 + 22 + 4). The two‑file form this
  ticket used to carry is insufficient for the same reason it was in BO‑20a:
  `event_hooks.py` is imported by all three receivers, and the parity file is
  what catches a non‑additive signature change (it asserts the `BackgroundTasks`
  entries as `(emit_event, args)` tuples).
  **Slice 2 must append `tests/unit/test_workflows_slice2.py` to that command**
  (→ **90 passed** expected, 80 + 10) — it now edits `triggers.py`, and that
  file is the only unit coverage of the trigger matcher it must not disturb.
  Plus `uv run ruff check --select F821,F601,F602,F502,F7,B006 apps/services/ingestion/ingestion`
  → `All checks passed!`
- **Files:** `apps/services/ingestion/ingestion/consumer.py` (slice 2),
  `apps/services/ingestion/ingestion/event_hooks.py` (slice 1 done; slice 2 may
  add the read‑only `sink_count()` accessor — nothing else),
  `apps/services/gateway/gateway/routes/workflows/triggers.py` (slice 2, the
  strict path — **added 2026‑08‑03** with the qualified non‑goal),
  `tests/unit/test_ingestion_consumer.py`.
  Slice 2's regression fence gains `tests/unit/test_workflows_slice2.py`
  (**10 passed** today; it pins `event_trigger_matches`, which is still a
  non‑goal and must not move).

---

**BO‑20c — Make the dead‑letter queue drainable and visible.** ✅ **AGENT‑SAFE** · *after BO‑20b*
`ingestion:dlq` is written by two live call sites today
(`clickup/webhook.py:48`, `:76`) and read by nothing, so a normalisation failure
is invisible and is eventually trimmed. (Both are genuine failure paths again as
of BO‑20f's repair round — until then the `:76` site also fired on **every
success**; see BO‑20f item 3.) Add, in `ingestion/queue.py`:
`read_dlq(limit, start) -> list[dict]` (decoded entries, newest‑first) and
`replay_dlq(entry_id) -> str | None` (re‑`xadd` to `origin_stream`, then `XDEL`
the DLQ entry — one hop, no silent duplication). Expose read + replay behind an
admin route reusing the **existing** permission `admin:access:manage`
(`packages/acb_auth/acb_auth/permissions.py:99`) — do not mint a new permission.
- **Done when:** `read_dlq` against a faked Redis returns entries with
  `origin_stream`, `event_type`, decoded `data`, and `error` — the four fields
  `enqueue_dlq` writes (`queue.py:82‑92`) — and an empty stream returns `[]`,
  not an error. Those four are **required**, not exhaustive: the reader must
  **tolerate and surface extra fields** rather than assume exactly four, because
  BO‑20b's consumer‑side DLQ write adds `times_delivered` (see BO‑20b accepted
  cost 3). Assert an entry carrying a fifth field is returned, not dropped or
  rejected.
- **Done when:** `replay_dlq(entry_id)` issues exactly one `xadd` to the entry's
  `origin_stream` with the original `event_type` and payload, and exactly one
  `xdel` on `ingestion:dlq` for that id; a non‑existent id returns `None` and
  issues **no** `xadd`.
- **Done when:** the admin route returns **403** for a member without
  `admin:access:manage` and the DLQ listing for a member with it — asserted via
  `TestClient` with the permission check faked, no live DB.
- **Verify:** `uv run pytest tests/unit/test_ingestion_dlq.py tests/unit/test_clickup_ingestor.py -q`
  → the new file green **and** `test_clickup_ingestor.py` still **10 passed,
  unmodified** — this ticket edits `queue.py`, which the ClickUp receiver imports
  (`enqueue`, `enqueue_dlq`), so the regression net applies here as it does to
  BO‑20a/b/f. (BO‑20d/e touch only `consumer.py` and do not need it.)
- **Files:** `apps/services/ingestion/ingestion/queue.py`,
  `apps/services/gateway/gateway/routes/admin/` (new module),
  `tests/unit/test_ingestion_dlq.py`.

---

**BO‑20d — Per‑source rate limiting.** ✅ **AGENT‑SAFE** · *after BO‑20a*
A token‑bucket limiter keyed by source, consulted by the drain loop before each
dispatch, so a provider burst is **paced, never dropped** (the entry stays in the
PEL until it is dispatched and acked).
- **Defaults are prescribed here, not chosen in the PR** (same reason as
  BO‑20b): `INGESTION_RATE_PER_SEC = 10.0` and `INGESTION_RATE_BURST = 20` per
  source, module‑level in `consumer.py` and env‑overridable. Rationale: 10/s is
  an order of magnitude above any observed provider webhook rate here, so the
  limiter is a burst brake rather than a throughput cap; a burst of 2× the rate
  absorbs a normal provider batch without deferring anything.
- **Done when:** the two defaults are asserted against those literals, so a
  later change is visible in a diff.
- **Done when:** `RateLimiter(rate_per_sec, burst)` takes an **injectable clock**
  and is asserted directly with a frozen clock — the first `burst` acquisitions
  are granted immediately and the next is deferred by `1/rate_per_sec`; the test
  **never sleeps**.
- **Done when:** the drain loop with a 1/s limiter and a frozen clock dispatches
  at most one entry per simulated second and **zero entries are lost** — every
  entry offered is eventually dispatched and acked, none discarded.
- **Verify:** `uv run pytest tests/unit/test_ingestion_consumer.py -q`
- **Files:** `apps/services/ingestion/ingestion/consumer.py`,
  `tests/unit/test_ingestion_consumer.py`.

---

**BO‑20e — Bounded concurrency.** ✅ **AGENT‑SAFE** · *after BO‑20a*
An `asyncio.Semaphore(INGESTION_MAX_CONCURRENCY)` bounds in‑flight dispatches;
`XACK` happens only after a dispatch completes; `stop_ingestion_consumer()`
drains in‑flight work before returning.
- **Default is prescribed here, not chosen in the PR** (same reason as BO‑20b):
  `INGESTION_MAX_CONCURRENCY = 8`, module‑level in `consumer.py` and
  env‑overridable. Rationale: each in‑flight dispatch can reach `start_run`, so
  this is the ceiling on concurrent workflow runs a provider burst can start
  inside the gateway process; 8 sits under the pooled async Redis client's
  `max_connections=16` precedent (`acb_common/activity.py:54‑66`). It must be
  **≥ 2** — a value of 1 makes the "peak equals the bound" assertion below true
  trivially and turns the ticket into a no‑op.
- 🚩 **Constraint inherited from BO‑20b (added 2026‑08‑03) — per‑entry idle time
  must be bounded before concurrency is enabled.** BO‑20b's
  `_RECLAIM_MIN_IDLE_MS = 60_000` is safe today only because the drain loop is
  serial: while a batch is in flight, no reclaim runs in this process
  (`consumer.py:282‑284, 317‑320`). It is a **per‑entry** bound, not a per‑batch
  one — entry *k* of a `_READ_COUNT` batch has already been idle up to
  (k−1) × `_DISPATCH_TIMEOUT_SECS` while queued, so with 8 entries the last can
  be ~210 s idle. The moment dispatches overlap (here) or a second gateway
  worker exists (the group is shared by design, `consumer.py:93‑95`), a reclaim
  can take an entry that is still being worked: **same event, two runs.** This
  ticket must close it before raising concurrency above 1 — either bound the
  in‑flight window (a smaller `_READ_COUNT`, so no entry can wait past
  `_RECLAIM_MIN_IDLE_MS`) or re‑`XCLAIM`/touch each entry immediately before its
  dispatch so its idle clock restarts. **Done when:** a test drives a batch of
  `_READ_COUNT` entries with `INGESTION_MAX_CONCURRENCY > 1` and a concurrent
  reclaim pass, and asserts **no entry is dispatched twice**.
- **Done when:** the default is asserted against the literal `8`.
- **Done when:** with a sink that blocks on an `asyncio.Event`, the observed peak
  of concurrently in‑flight sinks equals `INGESTION_MAX_CONCURRENCY` and never
  exceeds it, and the loop does not read more than that many un‑acked entries
  ahead.
- **Done when:** `stop_ingestion_consumer()` awaits in‑flight dispatches, so no
  entry is left both un‑acked and abandoned by the stopping process — asserted by
  releasing the blocked sink after `stop` is called and observing the `xack`.
- **Verify:** `uv run pytest tests/unit/test_ingestion_consumer.py -q`
- **Files:** `apps/services/ingestion/ingestion/consumer.py`,
  `tests/unit/test_ingestion_consumer.py`.

---

#### Verification (Windows; run these and quote the output)

⚠️ **Nothing below may require a live Redis or the VPS** — every consumer test
fakes the Redis client, per `tests/unit/test_clickup_ingestor.py:158‑181`
(`monkeypatch.setattr(queue, "_client", lambda: mock_redis)`).
⚠️ **Never run the full `uv run pytest` suite or a bare `tests/unit/` on this
machine** — it hangs against the live DB. Name test files.

```
uv run pytest tests/unit/test_clickup_ingestor.py -q
```
Baseline confirmed 2026-08-02 on a clean tree: **10 passed** (~0.5–0.8s). Every
BO‑20 PR must keep this green **and unmodified** — it is the regression net that
proves the ClickUp path did not change.

Since BO‑20a the fence is four files wide, because the cutover touches all three
receivers and the consumer:
```
uv run pytest tests/unit/test_ingestion_consumer.py tests/unit/test_clickup_ingestor.py \
  tests/unit/test_ingestion_receiver_parity.py tests/unit/test_clickup_normalise_dlq.py -q
```
Measured after BO‑20b slice 1 (2026-08-03): **80 passed** — 44 + 10 + 22 + 4,
with the last three **unmodified**. (BO‑20a closed at **77** — 41 + 10 + 22 + 4;
an earlier "75 / 39" here predated that ticket's own review repairs and was
stale.) BO‑20b–e must keep that shape: their own new file grows, the other three
do not move.

```
uv run ruff check --select F821,F601,F602,F502,F7,B006 \
  apps/services/ingestion/ingestion \
  apps/services/gateway/gateway/routes/workflows
```
Baseline confirmed 2026-08-02: **All checks passed!** This is the fast local
proxy for the CI correctness gate narrowed to this item's paths; the CI command
is the same select‑list over the whole repo
(`uv run ruff check . --select F821,F601,F602,F502,F7,B006`). Do **not** use a
bare `uv run ruff check <paths>` as a gate — the full rule set is deliberately
non‑blocking style backlog.

- **Competitive ref (CH‑3):** **OpenClaw's job queue** (automatic backoff, retry,
  rate‑limit + concurrent‑job handling) is repeatedly cited as its single
  hardest‑to‑replicate strength — it is the reference design here. This queue is
  also the substrate the messaging‑channel work (WBS 3.3 / CH‑4) needs. See
  `specs/competitive_hardening_2026-07.md`.
- **Cross‑ref:** "BO‑4" in `FOUNDATION_AUDIT_REPORT.md` §5 and the **BO‑4** row in
  `FOUNDATION_CONTINUATION.md` §D both refer to this item; **this section is the
  single owner** (`work_plan.md` §1 point 6) and they defer to it.

---

## E. LLM configuration

### BO‑15 — Single source of truth for tier→model + context windows *(P1)* ◑
> **Split verdict, verified against code 2026‑08‑03 (WS‑0 truth pass).** This item bundled two problems and they are now in different states, so "BO‑15's defects are closed" and "BO‑15 is untouched" are **both wrong**. The **context‑window** half is done; the **tier→model** half is not. The ◑ is honest; what follows says which half is which.

- **Done this pass:** the two hand‑synced tier‑alias maps are collapsed — `v1_compat` now imports `acb_llm.client._TIER_ALIAS_MAP` (the map `context.py` and the tests already use) instead of duplicating it.
- ~~**Missing:** `_TIER_CONTEXT_WINDOWS` a stale second copy of what `context.py` computes.~~ **✅ CLOSED — struck 2026‑08‑03.** `packages/acb_llm/acb_llm/model_limits.py` is now the single source of truth for "how big is this model?" (`get_limits()`), and its module docstring enumerates the five disagreeing sources it retired — including `_TIER_CONTEXT_WINDOWS` "duplicated verbatim in two packages". `settings.py:1494` is now `_TIER_CONTEXT_WINDOWS = FALLBACK_CONTEXT_WINDOWS` — an alias with **the tier aliases deliberately absent** so the dynamic resolution stands (the comment at `:1485-1493` records the bug this fixed: the stale pin was applied *after* dynamic resolution and overwrote it, under‑reporting the UI's context ring by ~7.6×).
- **Still missing — the tier→model half (M3), re‑verified on disk 2026‑08‑03:** `acb_llm/client.py:37` still defines `_TIER_DEFAULTS` as a hardcoded fallback map, populated at import time from `config.yaml` + `tier_overrides.yaml`; **all three of the files this item wanted deleted still exist** — `infra/litellm/tier_overrides.yaml`, `infra/enabled_models.json`, `infra/provider_models_cache.json` — and the DB `model_config` table is not authoritative over them. This half is unchanged since July.
- **Approach (for the remaining half only):** make the DB `model_config` table authoritative; delete `tier_overrides.yaml`, `enabled_models.json`, `provider_models_cache.json`, and the proxy directives in `config.yaml` once seeded. **Do not** re‑open the context‑window work — route every "how big is this model" question through `model_limits.get_limits()`.

### BO‑16 — Retire the vestigial LiteLLM proxy config *(P3)* ☐
- **Missing:** `infra/litellm/config.yaml` is a full proxy config but no proxy runs; only its tier rows are read (M6). `provider_models_cache.json` is a rotting committed cache.
- **Approach:** Reduce `config.yaml` to the tier map (or move fully to DB); delete `provider_models_cache.json`; align `infra/AGENTS.md` (which already claims the proxy files are gone).

---

## F. CI/CD & quality gates

### BO‑17 — Make the claimed gates real *(P1)* ☐
- **Missing:** mypy and full‑ruff are report‑only; evals are path‑gated (skip gateway/ingestion/reconciler); `deploy.yml` allows `skip_tests`; no coverage threshold (M10).
- **Approach:** Ratchet mypy/ruff to blocking per the existing plan; broaden the eval trigger paths or run a fast eval subset on every PR; remove `skip_tests` from production deploy; add `--cov-fail-under` for foundation packages. Reconcile README's CI claims.

### BO‑18 — Secret‑scanning + large‑file gates that actually catch history *(P1)* ◑
- **Done:** **gitleaks secret scanner** wired into CI — `.gitleaks.toml` (default rules + dev‑placeholder allowlist) + a `secret-scan` job in `pr-check.yml` that scans each PR's NEW commits (report‑only initially, per the ratchet; scoped to the PR range so it doesn't trip on the historical leak). Plus `scripts/scan_secrets_history.sh` for the one‑time full‑history audit around the purge. `.gitignore` rules for `*.pid`/`*.bak`/`*token_cache*` shipped earlier (**✅ F2**).
- **Missing:** graduate `secret-scan` to **blocking** after a few green PRs; a CI job that fails on any tracked file > 1 MB; and the actual **history purge + token rotation** (BO‑8, owner‑gated).

---

## G. Documentation

### BO‑19 — Doc↔code reconciliation *(P1)* ✅

> **Closed 2026‑08‑03 (WS‑0 truth pass).** Both residuals were re‑checked against the files, not against the previous doc, and **both are done**. Marking it ✅ does *not* claim the corpus is drift‑free — it claims this item's two named residuals are closed. Ongoing doc truth is `work_plan.md` §5's remediation backlog, which is where new drift belongs.

- **Missing (historical):** README described LangGraph/Theia/PostgresSaver/escalation_ui and had a garbled layout (**✅ F3** rewrites it); stale "placeholder"/LangGraph docstrings across packages (**✅ F6** sweeps the worst); `AGENTS.md` version pins lag.
- **Done (earlier pass):** `AGENTS.md` Python‑version mismatch fixed — "Python 3.11+" → "3.12+" to match `pyproject` (`>=3.12,<3.14`) and CI/prod (3.12).
- **Residual 1 — `AGENTS.md` package pins → ✅ closed, and better than asked.** The ask was "update the pins to the lockfile". The root `AGENTS.md` **deleted the hand‑copied table instead**, replacing it with *"`uv.lock` is the single source of truth for pinned versions — do not maintain a hand‑copied table here (it drifts: the previous snapshot was stale on 3 of 6 pins)"* plus `uv tree` / `uv pip list` as the check. A table that cannot drift beats a table that is currently accurate.
- **Residual 2 — `infra/AGENTS.md`'s "no proxy files / no Langfuse" claims → ✅ closed.** It now reads *"The legacy proxy files `litellm/config.yaml` + `litellm/tier_overrides.yaml` are **still on disk but vestigial** — only their tier rows are read; retiring them is tracked as BO‑16"* (`:4`) and *"Langfuse container is defined but **opt‑in behind `--profile obs`** and dormant … Distributed tracing is tracked as BO‑5"* (`:18`). Both match the tree: the two YAMLs exist, and Langfuse is a `--profile obs` compose service with the Python package uninstalled.

---

## Suggested sequencing

1. **P0 hardening sprint (do first):** **BO‑23 (backup/restore — scripts + runbook are AGENT‑SAFE; it is P0 because it is the only unrecoverable failure mode here)**, BO‑8 (rotate+purge secrets), BO‑2 (auth enforcement — ✅ since), BO‑1 (Action Broker), BO‑3 (mutation governance). These close the Critical trust‑boundary and governance gaps that everything else sits on.
2. **P1 sprint:** BO‑7 (sandbox), BO‑5 (observability+cost), BO‑6 (migrations), BO‑12/BO‑14 (runtime + permission model), BO‑15 (LLM config SoT — **tier→model half only**), BO‑17/BO‑18 (gates), **BO‑20 (event‑bus consumer + job queue)**. *(BO‑19 closed 2026‑08‑03.)*
3. **P2/P3:** BO‑9, ~~BO‑10~~ **(✅ closed 2026‑08‑06 — one engine/pool + non-blocking audit; it was the one item that compounded per app)**, BO‑11, BO‑13, BO‑16, **BO‑21 (memory activation)**.

**Competitive‑informed items** (proven reference implementations from Hermes Agent / OpenClaw — full mapping in `project-docs/specs/competitive_hardening_2026-07.md`): CH‑1→BO‑7/BO‑14, CH‑2→BO‑1, CH‑3→BO‑20, CH‑4→WBS 3.3, CH‑5→BO‑12, CH‑6→BO‑21, CH‑7→Phase‑5 Annealer, CH‑8→BO‑5. These do not change the sequencing above — they attach a "what good looks like" reference to items we already have, plus the two new items (BO‑20/BO‑21) the comparison surfaced.

The review pass already delivered F1–F6 (see report §6), which knock out the open LLM proxy, the on‑disk secret/junk exposure, the false‑$0 cost bug, the migration‑number collision, and the worst doc drift — clearing the cheapest Critical/High items so the P0 sprint can focus on the architectural ones.

---

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-1 — **Action Broker truth + completion** (BO-1)

**State cell (as of the move):** 🟢

**Narrative (verbatim):** Broker loop LIVE and writing (inbox, `/actions`, ClickUp + WhatsApp + workflow + app-publish handlers). **Handlers register at SIX sites, not the three this row claimed** (five measured 2026-08-03, plus the CRM's on 2026-08-05): `gateway/main.py` registers the four ClickUp task actions, `workflow.resume_run`, and — new — the three `crm.zoho_*` sync pushes; `routes/whatsapp/scheduler_hooks.py` registers `whatsapp.broadcast`; and `routes/apps/tools.py` registers two app-tool actions **at module import**, not startup. ~~"Remaining: **Zoho** handlers"~~ **struck as BO-1 work and it stays struck — the Zoho handlers now exist and are WS-26b's, not this workstream's.** `apps/services/ingestion/ingestion/sources/zoho/client.py` **stays** read-only — as of 2026-08-07 it is TEN read functions (WS-26b added `list_leads` and the deleted-records reader `list_deleted`; WS-26f added `list_deal_layouts` and `list_deal_pipelines`), all `GET`, and its one `POST` is still the OAuth token refresh. ⚠️ The claim "still all `GET /crm/v2/*`" is no longer true and must not be restored: WS-26f's two settings readers are the one deliberate exception (`settings/pipeline` does not exist on v2), version named once as `client.SETTINGS_API_VERSION` with a refusal reported rather than retried downward. Line numbers deliberately dropped: they drifted the first time anybody touched the file. ~~"There is no Zoho write path anywhere in the repo to route through the broker"~~ **corrected 2026-08-05 — there is one now, and it is NOT BO-1's.** WS-26b built `apps/services/ingestion/ingestion/sources/zoho/writer.py` (create/update/upsert/delete) on branch `ws-26b-zoho-sync`, per spec `crm_app.md` D-CRM-7/D-CRM-8. It has exactly ONE caller — `gateway/routes/crm/sync_zoho.py::execute_push`, grep-asserted in `tests/unit/test_crm_zoho_sync.py` — and every push crosses `routes/crm/broker_handlers.py::broker_gate` first. Its three actions (`crm.zoho_create`/`_update`/`_delete`) are registered from `main.py` alongside the ClickUp set, so the handler-registration count is now SIX sites, not five, and **all three CRM actions have handlers** (BO-1a's gap is ClickUp-only). Nothing has run against the tenant: `CRM_ZOHO_SYNC` ships OFF and enabling it is OWNER-GATE §6. The whole write path retires with WS-26e. ~~"verify vs live DB"~~ → **OWNER-GATE, and the "already done 2026-07-13" claim is UNSUPPORTED** — `FOUNDATION_CONTINUATION.md:145` records it outstanding and nothing since records it executed; no agent may claim it done or reach prod to do it, and it is not an acceptance criterion for anything below. **Three new tickets in §BO-1, all AGENT-SAFE, one PR each — the first two are flip-blockers, both new findings:** **BO-1a** — `providers.py` routes **six** ClickUp action names through `_broker_gate` but `broker_handlers._WRITERS` registers **four**, and the two missing are the two *irreversible* ones (`clickup.delete_task` `:551`, `clickup.archive_task` `:575`); under enforcement, approving one falls into `broker.execute()`'s no-handler branch (`broker.py:155-166`) and the row is marked **`failed`**. **BO-1b** — `_broker_gate` returns `{"pending": True, …, "provider_task_id": ""}` (`providers.py:171-172`) and `items._push_pending_item` ignores the marker, writing `sync_state='synced'` with an empty `provider_task_id` — under enforcement the user sees a green "synced" task that exists in no workspace. **BO-1c** — email handlers (zero `action_broker` wiring under `email_ingestion/`), buildable but blocked on §BO-1's recorded decision naming which of the base class's **14** mutating verbs are broker actions. **OWNER-GATE:** flipping `ACTION_BROKER_ENFORCE` on — **not until BO-1a and BO-1b are both in**, for the two reasons above.

**Corrections applied 2026-08-09:**
- Current as moved; the Zoho sync loop is RUNNING (owner-enabled 2026-08-06) — any "`CRM_ZOHO_SYNC` ships OFF / never run" phrasing inside is historical.

### WS-4 — **Event-bus consumer + durable queue** (BO-20)

**State cell (as of the move):** 🟢 a+f built · b slice 1 built · b slice 2 + c–e open

**Narrative (verbatim):** **§BO-20.0 IS ANSWERED — `BO-20 = Option A (in-process)`, owner, 2026-08-02.** Nothing in this row is blocked on a decision any more; the recorded rejection of Option B (a separate `python -m ingestion.worker`: needs a systemd unit no agent can deploy, and a separate process starts with an empty `event_hooks._SINKS`, so it would `XREADGROUP`, `XACK` and dispatch to nothing) is kept in §BO-20.0 as the reasoning, not deleted. **BO-20a BUILT 2026-08-02, pending review:** `apps/services/ingestion/ingestion/consumer.py` — `XGROUP CREATE <stream> cc-ingest $ MKSTREAM` on all three streams (`$` = tail, so the ~10k buffered entries per stream are skipped, not replayed into real workflow runs), a supervised `XREADGROUP` drain loop (`_GROUP="cc-ingest"`, `_BLOCK_MS=5_000`, `_READ_COUNT=8`, per-worker consumer name `gw-<host>-<pid>` because BO-20b's `XAUTOCLAIM` identifies a dead worker by it) decoding `{event_type, JSON data}` into `event_hooks.emit_event(source, event_type, dict)` and `XACK`ing, a long-lived pooled `redis.asyncio` client per `acb_common/activity.py:66-76`, `start/stop_ingestion_consumer()` + `consumer_status()` in the gateway lifespan (start `main.py:307`, stop `:364` — **unconditional**, like `stop_whatsapp_enrichment`), and the **§BO-20 Q1 cutover in all three receivers**: flag ON ⇒ enqueue-only, flag OFF ⇒ **dispatch-identical** to before (not byte-identical — each receiver now also does one function-body import + one `os.environ` read per request). Packaging defect closed: `ingestion` is now a declared gateway dependency (`pyproject.toml` + `uv.lock`), not an inheritance from the root workspace umbrella. Pinned by `tests/unit/test_ingestion_consumer.py` (41 tests; **77 passed** across the four-file fence — 41 + 10 + 22 + 4, the other three unmodified), no Redis/DB/network. **Adversarial review 2026-08-03 → APPROVE, no P0/P1;** the four P2s were repaired in-branch: a `asyncio.timeout(_DISPATCH_TIMEOUT_SECS=30.0)` around `emit_event` (one serial loop drains all three streams, so an unbounded await turned a per-event hang into a **bus-wide, silent** stall — strictly worse than the pre-cutover `BackgroundTasks` hang it replaces), a test pinning the lifespan start/stop wiring itself, one shared ordered timeline so criterion A can tell ack-after-dispatch from ack-before-dispatch (the line BO-20b edits), and `assert task.cancelled()` instead of the weaker `task.done()` — **the reviewer's last item was half a fix**: cancelling a task that has never been stepped makes asyncio raise `CancelledError` above the loop's `try`, so `task.cancelled()` passes against a swallowing loop too; the test now waits for the loop to reach its first read before stopping, and was verified red against a deliberately-swallowing `_consumer_loop`. ⚠️ **Ships OFF and is inert in every environment:** `INGESTION_CONSUMER` is unset everywhere, so the loop never starts and the receivers still emit inline. **OWNER-GATE:** flipping `INGESTION_CONSUMER=1` (registered in §6) — it is not just "start a loop": the same flag cuts the three provider receivers over to enqueue-only, so **Redis down = provider events dropped** rather than dispatched inline. That drop is now logged loudly (`<source>.queue.dropped`, warning) instead of being silent, and must not be "fixed" by re-emitting inline. **Interim semantics, deliberate:** BO-20a acks after dispatch regardless of outcome — honest `XACK` + retry + DLQ is **BO-20b**, now split in two. **BO-20b slice 1 BUILT 2026-08-03:** `event_hooks.emit_event` gained a **keyword-only** `raise_on_error: bool = False` — the strict mode the consumer needs to observe a failure at all, since `emit_event` swallowed every sink exception by design and BO-20b's retry logic is dead code without it. Default unchanged (swallow, log `event_hooks.sink_failed`, run the next sink — a webhook must never 5xx); `raise_on_error=True` propagates the **first** sink exception and skips the remaining sinks. Keyword-only so the three receivers' three-positional-arg `add_task(emit_event, source, event_type, payload)` can never reach it, and the default is pinned as the literal `False` via `inspect.signature` so a later PR cannot flip provider-facing behaviour silently. `consumer.py` is **untouched** — it still acks regardless of outcome. Three new tests (`tests/unit/test_ingestion_consumer.py` §J), four-file fence **80 passed** (44 + 10 + 22 + 4, the last three unmodified); both mutants (drop the `raise`, flip the default) verified red. **BO-20b slice 2 is open, and its SCOPE GREW on 2026-08-03** (adversarial review, repair round 1): slice 1 is *necessary but not sufficient*. `main.py:1074` registers exactly **one** sink, `workflows.triggers.dispatch_event`, and its whole body sits inside a `try/except Exception` that logs `workflows.event_dispatch_failed` and returns `[]` (`triggers.py:45-46`, `:90-104`) — so `raise_on_error=True` is a **no-op on the real registry**: slice 2 would have called it, `dispatch_event` would have swallowed, `emit_event` would have returned normally, the loop would have `XACK`ed, and the event would be **gone** with no retry, no PEL entry and no DLQ row — with every test green, because the suite registers a *raising fake* sink, a shape production does not have. Slice 2 therefore also owns a keyword-only strict path in `dispatch_event` (`triggers.py` joins its Files list; `tests/unit/test_workflows_slice2.py` joins its regression fence, 80 → 90 passed), with the failure boundary prescribed in §BO-20b: **propagate** the `_get_db`/trigger-query failure and `RunRejected` (raised at `service.py:193-196` *before* the run row and the task, so nothing ran), **never** the per-run execution failures (fire-and-forget via `create_task` at `service.py:226` — re-delivering would start a *second* run of the same workflow on the same payload), and raise **after** the row loop so a partial dispatch is not made worse. §BO-20's non-goal "Not a change to `dispatch_event`" is **struck and qualified** accordingly — that is a third `DECISION (agent-proposed, owner may overrule)` on this row; the rejected alternative was to leave `dispatch_event` untouched and accept that the consumer cannot distinguish "dispatched" from "swallowed", i.e. BO-20b cannot deliver its guarantee. Slice 2 also carries two `DECISION (agent-proposed, owner may overrule)` entries recorded in §BO-20b, because the ticket as written was *satisfiable while doing nothing*: (i) **retry is PEL-and-reclaim, not an in-loop `asyncio.sleep`** — the prescribed `_backoff` schedule (1,2,4,8,16 s) was dominated by the same section's `_RECLAIM_MIN_IDLE_MS = 60_000`, so the two constants could not both be true; `_backoff` is **struck** (it was also unpinned at its *call site*, so it could be defined, satisfy all four asserted properties, never be called, and close green), a `_RECLAIM_EVERY_SECS = 30.0` periodic cadence is prescribed with a done-when that the periodic pass **exists**, and the attempt counter is `XPENDING`'s `times_delivered` (an in-process dict resets on restart ⇒ a poison entry never reaches the DLQ). The rejected in-loop model would have blocked **all three streams for ~165 contiguous seconds** per poison entry, reintroducing exactly what BO-20a added `_DISPATCH_TIMEOUT_SECS` to prevent; the accepted cost of the chosen model is retry latency quantised to the reclaim cadence (~5 min to succeed on the 5th attempt, ~6 min to DLQ). (ii) **a dispatch `TimeoutError` is a FAILED dispatch** (retry, then DLQ) — acking it is a silent drop, which is the thing this ticket abolishes; consequence: BO-20a's `test_a_hung_sink_times_out_and_the_bus_keeps_draining` must be **rewritten** by slice 2 (its ack assertion inverts; its bus-keeps-draining half is preserved). Also recorded: the DLQ write must **not** call the sync `queue.enqueue_dlq` from the async loop (fresh sync client per call at `queue.py:49`, blocks the loop, invisible to the `consumer._get_client` fake), and `XAUTOCLAIM`'s **third** reply element — ids whose stream entry `_MAXLEN` trimmed away — must be unpacked and logged, because on redis-py 7.1.1 the common two-element unpack raises `ValueError` and wedges the whole **drain loop** every cycle — the `try` at `consumer.py:294-298` spans `_ensure_groups` *and* `_drain_once`, so a failing top-of-iteration reclaim stops the bus draining entirely, at ~1 Hz, forever (the reclaim pass must be wrapped so its failure degrades to "no reclaim this cycle"). Also newly recorded in §BO-20b: `JUSTID` is **forbidden** (it suppresses the very delivery-counter increment the retry design rests on, and `redis-py` returns a bare id list that unpacks into three names *without raising*); the `XPENDING`-before-`XAUTOCLAIM` read order is pinned (the other order moves the observable DLQ threshold from 5 deliveries to 6 and no fake-backed test can tell); `times_delivered` counts **deliveries, not failures**, so a crash-loop burns retry budget on a healthy event (mitigated by recording it on the DLQ row); the reclaim's 60 s min-idle bound is **per entry, not per batch** and is safe today only because the loop is serial — a constraint now sits on **BO-20e** to bound per-entry idle before concurrency is enabled, or the same event runs twice; **per-stream ordering is given up** by PEL-and-reclaim and is now listed as an accepted cost (a stale `taskUpdated` can start a run after a fresher one); and the attempt counter survives a *gateway* restart but **not a Redis** one (`xgroup_create(id="$")` re-creates the group at the tail after a flush, and `infra/` sets no `appendonly`). ⚠️ **Two further "enqueued but never dispatched" states are now recorded in §BO-20a** beyond that accepted drop: the `XACK` is deliberately unguarded (a raising `xack` means Redis is gone and must reach the backoff, not hot-loop), and the loop only ever reads `">"`, so an ack failure or a SIGTERM **mid-batch** strands the rest of that `XREADGROUP` reply in the PEL under the old pid's consumer name. Only BO-20b's reclaim pass recovers them, and only until `queue._MAXLEN` trims — so **BO-20b's done-when now requires the reclaim pass to run at startup**, not only on the periodic cadence, and carries an explicit open sub-question about the min-idle bound at startup. **BO-20f (Gmail + Zoho receivers reach ClickUp enqueue+emit parity) shipped 2026-08-02** and is what multi-channel event triggers actually needed; it is still **inert in prod** — `zoho_webhook_secret` and `gmail_pubsub_token` default to `""`, both receivers fail closed, and **OWNER-GATE (an agent can do neither):** provision `ZOHO_WEBHOOK_SECRET` + `GMAIL_PUBSUB_TOKEN` on the VPS (`.env.example` is itself OWNER-GATE under WS-2 — the plan-guard hook blocks agent writes to it) **and** point the provider subscription/webhook at `/webhooks/{zoho,gmail}`. The fail-closed posture is correct and must not be changed. ⚠️ **Not a greenfield build:** webhook→run was ALREADY wired — ClickUp → `ingestion/event_hooks.emit_event` → `workflows/triggers.dispatch_event` → `start_run` since commit `e20ea830`, and `/agent/webhook/{source}` (`routes/agent.py:3476-3478`) is a second live path that calls `dispatch_event` **directly** and is **untouched by the cutover** — so §BO-20 Q1's old "the consumer becomes the single dispatch path" was loose and is corrected there to "the only caller of `emit_event`". **Remaining: BO-20b slice 2 → c → (d, e)** — retry via PEL reclaim + honest `XACK` + DLQ hand-off, a drainable/visible DLQ, per-source rate limiting, bounded concurrency; all ✅ AGENT-SAFE, each waiting only on its predecessor. **WS-11 Slice 4 still waits**: `workflows_app.md:217` defines it as "(post-BO-20/BO-7): durable queued runs; …", and durable means a–e — without BO-20b a failed dispatch is acked and lost. BO-9 resolved as **not blocking** (the consumer owns its own long-lived async client; the producer's per-call sync `queue._client` stays BO-9's, untouched here).

**Corrections applied 2026-08-09:**
- Current as moved; D15 coda added on the board: webhook secrets become per-org at MT-1a+.

### WS-5 — **CI gates real** (BO-17/BO-18)

**State cell (as of the move):** 🟡 Docs

**Narrative (verbatim):** Un-gate evals, blocking gitleaks, coverage floor. ~~AGENT-SAFE~~ → **mixed: the highest-value item is a GitHub *settings* change an agent cannot make.** **Audited 2026-08-01 → NO-GO**: §F has zero testable "done when" ("per the existing plan", "a few green PRs", "for foundation packages"), its ratchet-plan anchor points at a path that moved to `specs/archive/` (3 stale citations live *in the workflow files*), and BO-17 reads ☐ while half of it shipped (blocking ruff-correctness + xenon, a frontend tsc/vitest job, gitleaks, per-PR health). **THE MISSING ITEM — why the 2026-08-01 F821 escape happened, in no doc today:** (1) `main` has **no branch protection** (`gh api …/branches/main/protection` → 404) — every "blocking" gate in these YAMLs is decorative; (2) commits pushed straight to main get **zero check-runs** (`15c8933f` had none); (3) `deploy.yml:56-58` lints with the *non-blocking full* `ruff check .`, **not** the `--select F821,…` correctness gate, so deploy went green over a broken tree; (4) PR #318's `pr-check` **failed on that exact F821 and merged anyway**. **Slice when specced (BO-17a "main-guard"):** add a `correctness` job to `deploy.yml` on push-to-main running the `--select` gate, deliberately NOT in the deploy job's `needs:` — loud, not blocking. AGENT-SAFE. **OWNER-GATE:** enabling branch protection / required checks, wiring any gate into `needs:`, removing `skip_tests`; BO-18's purge+rotation is WS-2's, not this row's. Refuted two long-standing beliefs: pr-check **does** cover the frontend, and it **does** run on non-main branches.

**Corrections applied 2026-08-09:**
- The row's claim that `main` has no branch protection was FALSE when moved — protection enabled 2026-08-03 (`enforce_admins: true`; `required_status_checks` deliberately `null`, so docs-only PRs run zero checks).
