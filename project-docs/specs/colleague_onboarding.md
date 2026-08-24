# Colleague onboarding — the readiness gate, the runbook, and the capability matrix

**Status:** 🔴 NOT READY — **2 gates + 1 decision open** (G1 Caddy strip ·
G2 GATEWAY_INTERNAL_TOKEN · N5 notes-routes decision) — G3 backups CLOSED 2026-08-07;
updated 2026-08-09. **G4 is
CLOSED: all four owner-scoping tickets shipped 2026-08-04** — N4 (Tasks people
directory: directory open, HR fields restricted, writes admin-only) and N1–N3
(Notes: sixteen routes in six files, single-item approve/reject, and the
bot_join recording hijack). **G1 and G2 remain open** (G3 closed 2026-08-07 —
backups scheduled + restore-verified, see BO-23), both OWNER-GATE, plus the N5
decision — and **it is still not safe to invite anybody.** G4 closed the holes
that survive a *correct* identity; G1/G2 are about the identity itself, and an
owner predicate applied to a forged one is not a control. §4 also mints **N5**
(the rest of `routes/notes` — nine modules N1's table did not enumerate),
deliberately outside G4 and awaiting an owner call. **§6 is new (2026-08-04):
N6, the provisioning gap** — an unprovisioned sign-in was logged and then
discarded, so the owner could not see who was knocking. Motivated by a measured
production incident: 53 refusals for one colleague over 18 hours, invisible in
the UI. **N6a is BUILT + REPAIRED TWICE (2026-08-04, branch `ws-24-n6-signin-requests`)** —
the knock is persisted, the owner answers it from a Requests tab, and approving
provisions *and* activates in one action; a same-day adversarial review found a
**P1 cross-gate escalation** (approve could reinstate an off-boarded member on
the weaker `admin:members:invite`) and proved the test that claimed to fence it
was a mirror of the code, not a check on it — both fixed, and the fence is now
a structural assertion against the SQL itself (§6 *Repair round*). A second
pass found the other half: the guard *declined* silently, so approve still
answered **200**, marked the request `approved`, and re-granted roles — which
took the still-locked-out person out of a queue that renders only `pending`.
**Approve now either fully succeeds or refuses with a 409, per the approve
matrix in §6 *Repair round 2*.** A third pass found the same shape once more —
this time a race, not a sequence — and with it the reason it kept recurring:
**approve verified by prediction, never by reading back what it wrote.** It now
requires the member to be `active` before the decision is stamped (§6 *Repair
round 3*).
**N6b's open owner question is ANSWERED 2026-08-24 (R4, decision D50.3):
(b) auto-promote on first sign-in — on BOTH planes** *(the registry half in the
first build; the tenant half landed the same day in review-round-1 repair)*. The
Customer Console `org_membership` is promoted at first resolve inside the
deployment arm (`store.activate_invited_member`), and the TENANT plane's
`app_user.status` + identity-shadow row are promoted by
`acb_auth.access.promote_invited_member`, called from exactly ONE site — the
gateway's `POST /signin/resolve`, only after `decision.admit` — so an invited
colleague lands in a working app instead of the AccessGate dead end. Both
promotions carry the `AND status = 'invited'` guard in the UPDATE's own `WHERE`
(the un-suspension trap §6 warned about is the mutation each fence is shown red
with; tenant fence `tests/unit/test_invited_member_promotion.py`, R8).
**§2 Step 1b is therefore no longer required for an INVITED member** — it
remains the path for reactivating a suspended one. Two more corrections in the same pass: **§2's "Sign-in is Entra
ID SSO — there is no invitation email" was false on both halves** (sign-in is
Google / email OTP; an invitation email exists as `subscription_console.md`
SC-2c, a NOTIFICATION with no accept-token, D50.1), and **invite now provisions
on TWO planes** (`app_user` plus the Console's `org_membership`,
`customer_console.md` CP-2f) — without the second, an invited colleague with
registry resolve armed is funnelled into creating their own organization. **Merging N6a is
OWNER-GATE**: `deploy.yml:202-203` replays every migration on deploy, so the
merge arms an auth-behaviour deploy.
**§2 Step 5 also gained N7, BUILT 2026-08-04 (`ws-24-n7-self-removal-guard`):
off-boarding *yourself*.** `DELETE` refused the caller; `PATCH {"status":
"suspended"}` reached the identical `is_active=False` with **no self-check at
all**, and refused only by the accident of `assert_owner_survives` firing in a
one-owner org — a second owner (which §2 Step 2 exists to create) opened it.
The Members page drew the button, because it never learned who the viewer was.
Both doors now call one guard (`_common.assert_not_self_lockout`), the rule is
"any status that is not `active`" so `invited` is covered too, and the roster
renders **This is you** where the destructive controls were. **No migration, no
new slug** — so unlike N6a this one is not a deploy-behaviour gate.
**§2 Step 5 gained N8, BUILT + REPAIRED 2026-08-05 (`ws-24-n8-purge-member`):
deleting a member permanently.** Remove is soft by design and stays so; `DELETE
/admin/members/{email}/purge` is a second, harder action beside it that
destroys the identity and every credential and access grant keyed to it,
**keeps what the person authored, and keeps the audit trail** — the table map,
the FK cascade map, and the one place the person/content split is not clean are
all recorded in §2 Step 5. It goes through the *same* self-guard (a fourth
door) plus `assert_owner_survives`, runs in one transaction, and reports a
count per table so the irreversible half is auditable. ⚠️ **The first version
reported destroyed rows as *kept*** — `task_accounts` cascades the SYNCED half
of `gtd_items`, which the keep clause counted anyway, so 847 synced tasks came
back as `kept: {"tasks": 847}` with all 847 gone; `gtd_projects` was on neither
list. Fixed, and three fences that did not exist were the reason: the delete
route's permission, the type-to-confirm rule, and any cross-table claim at all
(§2 Step 5 *Repair round 1*). **No migration, no new slug.** ·
**Board row:** WS-24 ·
**Owner:** vjvarada · **Date:** 2026-08-05 ·
**Verified against code on 2026-08-05** (branch `ws-24-onboarding-readiness`,
cut from `ws-14-doc-remediation` @ `ed785bea`; repair round @ `8b6dcdd3`;
N4 built on `ws-24-n4-people-scoping`, cut from `007caae2`; N1–N3 on
`ws-24-n1n3-notes-scoping`, cut from `891903de`; N6a on
`ws-24-n6-signin-requests`, cut from `5beeabbe`; N7 on
`ws-24-n7-self-removal-guard`, cut from `2a41099b`; N8 on
`ws-24-n8-purge-member`, cut from `e911e9d2`).

**What this doc is for.** Exactly one person is signed in to this deployment
(**owner-reported, not measured** — see the note below). The question "is it
safe to invite colleagues yet" has been re-derived in conversation more than
once and answered nowhere durable. This spec is the durable answer, in three
parts:

| § | Section | Kind |
|---|---|---|
| §1 | **The readiness gate** — what must be true before colleague #1 | checklist with per-item done-whens + gate labels |
| §2 | **The onboarding runbook** — invite → role → Center group → verify, and off-boarding (Step 5, incl. **N7 and N8 — both built**) | procedure, grounded in real endpoints |
| §3 | **The capability matrix** — what a colleague on each role can actually see | evidence table, every cell carries `file:line` |
| §4 | **The four open owner-scoping holes** — blocking items with sizes | tickets |
| §5 | **Verification** — the exact commands, and what must never be run | commands |
| §6 | **The provisioning gap** — nobody could see who was knocking (N6) | ticket — **N6a built 2026-08-04**, N6b needs no code |

**Executable half.** `scripts/onboarding_preflight.py` implements §1's
machine-checkable criteria. Run it **on the box** before inviting anyone; run
it `--mode local` anywhere else and it refuses the box-only checks rather than
guessing. If a criterion changes here, change it there in the same PR.

> The rule the script now holds to: **a check never PASSes on evidence it could
> not actually see.** Anything that is a claim about *the deployment* — the two
> secrets (checks 1–2), the live Caddyfile's half of check 3, the backups that
> landed (check 4's box half), the database (checks 5–7) — SKIPs in local mode
> rather than reporting a laptop's answer as the box's. What a local run *does*
> answer is the repository: the repo Caddyfile's strip directives, BO-23's four
> repo-side done-whens, and the Centers feature vocabulary. So a local run can
> legitimately reach exit 0 once those repo defects are fixed, and today it does
> not, because two of them are real.

**Scope.** This doc owns the *gate* and the *matrix*. It does not own the
access model (`org_access_control.md`), the visibility doctrine
(`tenancy_and_visibility.md` **§3–§5 / D12** — still binding), the **tenancy
boundary** (`saas_multitenancy.md` **§1 / D15**, which re-took D11 on 2026-08-08),
or the Centers IA (`department_centers.md`). Where they disagree with a cell here, re-measure and
fix whichever is stale.

**Non-goals.** Not an HR onboarding process. Not a rollout plan for a second
tenant — that is **`saas_multitenancy.md` §5.1 / WS-29** now. ⚠️ **The old text
here cited D11 ("the tenant boundary is the deployment"), which was re-taken as
**D15** on 2026-08-08: a tenant is an `organization_id` row. This doc's gate is
about **colleague #1 inside one org** and is unaffected — but do not cite D11
from here. Not a fix for §4's open
holes — this is the gate that says they must be fixed, and sizes them.

> **Two facts in this doc are OWNER-REPORTED, not measured.** *"Exactly one
> member is signed in"* and *"there is exactly one email account"* (§3.3) are
> live-database facts, and this document forbids an agent from running the one
> tool that could measure them (§5: never point the preflight at production).
> They were not verified in the session that wrote this. Treat them as the
> owner's statement of the current state, re-check them on the box before
> relying on either, and do not cite them as `file:line` evidence — everything
> else in §3 is code, these two are not.

**Everything else here was verified against code**, on the branch and commit in
the status header.

---

## 1. The readiness gate

**The verdict today: NOT READY.** Three items block colleague #1 (G4 closed
2026-08-04). One is AGENT-SAFE and can be built now; two are OWNER-GATE and an
agent must refuse them by name.

Nothing in this section is a preference. Each one is a live path by which a
colleague sees, changes, or destroys something that is not theirs, or by which
the owner loses work that has no backup.

### 1.1 The blocking items

| # | Item | Gate | Done when |
|---|---|---|---|
| **G1** | **Caddy strips inbound identity headers on the API vhost** | 🔴 **OWNER-GATE** (installing it on the box changes auth behaviour — `work_plan.md` §6) | `deploy/hostinger/caddy/Caddyfile`'s `api.*` `reverse_proxy` block contains **both** `header_up -X-User-Email` and `header_up -X-User-Role`; the same is true of `/etc/caddy/Caddyfile`; and `scripts/onboarding_preflight.py` reports `[PASS] Caddy strips inbound identity headers`. Writing the repo file is AGENT-SAFE; installing + reloading is not. **⚠️ HALF-DONE 2026-08-04:** the owner applied both directives to `/etc/caddy/Caddyfile` and they are live — verified by `caddy adapt` per vhost (`api.*` strips, the UI vhost correctly does not, since it *sets* them). **`deploy/hostinger/caddy/Caddyfile` is still unpatched, so the two have drifted**, and `deploy.yml:496-501` reinstalls the repo copy only when the live one fails validation — meaning a future config break silently removes the protection. G1 is not green until the repo file matches. Note also that `systemctl reload caddy` **has never worked on this box** (`admin off` ⇒ no admin API on :2019); only `restart` applies config. 🔴 **AND A BIGGER ONE, measured 2026-08-05: the strip can be walked around entirely.** Gateway `:8080` and workbench `:3001` answer from the public internet (probed from outside; `5432`/`6379` are correctly closed). Caddy only strips what passes *through* Caddy, so a request straight to `:8080` never meets the directive. **G1 is not a boundary until those UFW rules are closed** — that is an owner action, registered in `work_plan.md` §6, and it is the half of G1 that actually matters. |
| **G2** | **`GATEWAY_INTERNAL_TOKEN` is provisioned in BOTH files and is not `LITELLM_MASTER_KEY`** | 🔴 **OWNER-GATE** (a credential provisioned on the box, in two places) | The preflight reports `[PASS] Service identity is its own secret` in **box** mode, which now requires all three of: set in `/opt/acb/app/.env`; set in `workbench/control_plane/.env.local`; **the two byte-identical**; and different from the LLM key. ⚠️ **Setting only the first is a total lockout, not a partial fix** — see the warning below. 🔴 **MEASURED 2026-08-05 — this is not merely unprovisioned, it is provisioned to the WRONG VALUE.** `GATEWAY_INTERNAL_TOKEN` is set on the box and is **byte-identical** to `LITELLM_MASTER_KEY`: same 64-char length, same sha256 (`720659eb…`). So a "is the token configured" check reads green while the service identity *is* the LLM key every agent's BYOK client holds. `.env.local` does carry the token, so the lockout mode below is not currently active — the fix is a rotation to a **distinct** value, done by redeploying. |
| **G3** | **A restore path exists** (BO-23) | 🟢 **AGENT-SAFE** to write the scripts + runbook; 🔴 **OWNER-GATE** to run any of them, install a schedule, or point anything at prod data | The preflight reports `[PASS] A restore path exists and backups are recent`. Its repo half is **BO-23's own done-when 1–4, verbatim** (`FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-23): `scripts/backup_db.sh` taking a data-inclusive `pg_dump -Fc` (**not** `--schema-only`), `scripts/restore_db.sh` calling `pg_restore`, a runbook that states the *verification* step, and a pre-migration hook. Its box half: an artefact under `/opt/acb/backups` newer than 48h containing a dump above the size floor. **Nothing of this exists on this branch** — the only DB script that dumps anything is `scripts/dump_schema.sh` (`pg_dump --schema-only` — structure, zero rows). ✅ **PR #347 MERGED and DEPLOYED 2026-08-05, and the gate has now fired for real:** the pre-migration hook ran ahead of migration 143 and left `/opt/acb/backups/2026-08-05T044202Z` — `acb.dump` **22 MB** (data, not schema-only), plus `globals.sql`, `litellm_proxy.dump` and `MANIFEST.txt`. That is the first backup this deployment has ever taken. **Two things still owed before G3 is green:** the `acb-backup.timer` is **not installed**, so today's backup happened because a migration ran and not because anything is scheduled; and **no restore has ever been performed** — `restore_db.sh --verify-restore` has never been run, so the dump is untested as a recovery path. Historical note: `scripts/backup_db.sh` and `restore_db.sh` were proposed on the **independent** PR #347 (`ws-0-bo23-backup-restore`) and are not here; G3 goes green when that lands and is scheduled, not before. See `work_plan.md` §2 exception 2. |
| **G4** | **§4's four owner-scoping holes are closed** | 🟢 **AGENT-SAFE**, four tickets (§4) — ✅ **4 of 4 CLOSED 2026-08-04** | Each of §4's **four** tickets meets its own done-when; `tests/unit/test_notes_owner_scoping.py` (57 cases) covers N1–N3 and `tests/unit/test_tasks_people_scoping.py` (35) covers N4. **N4** — the Tasks people directory is "directory open, HR fields restricted" with all four writes on `admin:members:manage` (§4 N4's DECISION block). **N1–N3** (`ws-24-n1n3-notes-scoping`) — fifteen of sixteen Notes routes in the six named files load through `core.load_owned_meeting` / `OWNED_MEETING_PREDICATE` and answer 404 never 403, the sixteenth (`live.py:256`) is machine-authed by recorded decision; `actions._load_action` joins `meeting` so a member can no longer approve a colleague's item into their own GTD list; `bot_join`'s attach branch binds the predicate into the `UPDATE`. ⚠️ **This gate going green does not make WS-24 green** — G1/G2/G3 are untouched, and G4 closes the holes that survive a *correct* identity, not the ones that begin with a forged one. ⚠️ §4's new **N5** (nine further `routes/notes` modules N1's table never enumerated) is deliberately **outside** this gate and needs an owner call. |

> ### ⚠️ G2 has a lockout mode. Read this before provisioning the token.
>
> The token lives in **two** files and the second one is easy to miss, because
> the natural instruction — "set it and restart the gateway and the workbench" —
> produces the failure. Concretely: `.env` has the token, `.env.local` does not,
> so the Next.js BFF keeps sending its `sk-local-dev-change-me` default
> (`workbench/control_plane/src/lib/gateway.ts:58-61`). Every proxied browser
> call then arrives with a bad Bearer **and** a real `X-User-Email` while an
> internal token *is* configured — and `acb_auth/deps.py:356-361` resolves
> exactly that shape to `NO_ACCESS`. **Every signed-in member, including the
> owner, is locked out**, and it is the mechanically-correct outcome, so there
> is nothing to debug.
>
> **Do it by redeploying.** `.github/workflows/deploy.yml:166-187` reconciles
> `.env.local`'s `GATEWAY_INTERNAL_TOKEN` from `.env` on every deploy, in place
> and idempotently, preserving the file's other keys. That is the only path
> that cannot leave the two disagreeing. If it must be done by hand, write both
> files in the same sitting, then restart both processes.
>
> The preflight now reads `workbench/control_plane/.env.local` as well as
> `.env` and FAILs naming the lockout when the two disagree
> (`scripts/onboarding_preflight.py`, check 1). Before that change it read only
> `.env` and would have certified the lockout state green.

### 1.2 Already true — do not re-litigate

| Item | Evidence |
|---|---|
| PR #348 (the WS-13 Centers feature-vocabulary fix) is in this branch's ancestry | `acb_auth/permissions.py:95-100` carries the six `center.*` slugs; the preflight's check 6 passes locally against `140_center_features.sql`. |
| PR #346 (the Notes owner filter) is merged as `d2ef7fa0` | `routes/notes/core.py:192-217` — `OWNED_MEETING_PREDICATE` + `load_owned_meeting`. |
| A bare `X-User-Email` with no Bearer establishes nothing | `acb_auth/deps.py:356-361` — with an internal token configured, an unaccompanied identity header resolves to `NO_ACCESS`. The exposure G1/G2 close is narrower and specific: the token that *is* configured may be the LLM key every agent holds. |
| Default-deny for an unknown email | `acb_auth/access.py:257-263` — an authenticated stranger with no `app_user` row resolves to `is_active=False`. Pinned by `tests/unit/test_default_deny_auth.py`. |
| The six system roles seed themselves idempotently | `infra/postgres/130_org_access_control.sql:180-260`, extended by `131_integration_memory_permissions.sql`. |

### 1.3 Known-and-accepted, not blocking

| Item | Why it does not block |
|---|---|
| Workflows are org-wide | A recorded v1 decision (`routes/workflows/crud.py:1-5`, spec Q3). It is not a defect; it is a **consequence of granting `feature:workflows`** — see §3.4. `member` does not hold it. |
| ~~`main` has no branch protection~~ **CLOSED 2026-08-03** *(row corrected 2026-08-09)* | `work_plan.md` §2 exceptions row 1: protection enabled with `enforce_admins: true`; `required_status_checks` deliberately `null` (docs-only PRs run zero checks). |
| Custom-App grants do not honour `group:` | WS-14; `routes/apps/grants.py:68-85`. Narrower access than intended, not wider. |

---

## 2. The onboarding runbook

Prerequisite: §1 is green (run `scripts/onboarding_preflight.py` on the box).
Every step below is a real, shipped endpoint.

> ⚠️ **CORRECTED 2026-08-24 (R4, decision D50).** This paragraph used to read
> *"Sign-in is Entra ID SSO — there is no invitation email."* **Both halves are
> now false.** Sign-in is Google / email OTP through Auth.js
> (`customer_console.md` CP-0, CP-2d) — Entra is gone — and an invitation email
> exists: `subscription_console.md` **SC-2c**, dark behind
> `MEMBER_INVITE_EMAIL_ENABLED`. It is a **notification, not an acceptance flow**
> (D50.1): no token, no link that grants anything, no query-string secret.
> Identity is still proven at sign-in by the IdP, so "inviting" still means
> **provisioning the row that turns a verified identity into a member** — and
> since **D50.2** it provisions that row on **two** planes: the tenant's
> `app_user` and the Customer Console's `org_membership` (`status='invited'`,
> `customer_console.md` **CP-2f**). The second is not optional bookkeeping —
> without it the colleague is invisible to the seats grid, cannot be assigned a
> seat, and with registry resolve armed their first sign-in funnels them into
> creating **their own** organization instead of joining yours.

### Step 1 — Invite

    POST /admin/members     { "email": "…", "display_name": "…", "roles": ["member"] }

* Gate: `admin:members:invite` — `routes/admin/members.py:145-146`.
* Package floor: the whole `/admin` surface additionally requires
  `admin:members:read` (`routes/admin/_common.py:77-91`).
* Behaviour: inserts (or re-activates) `app_user` in the `invited` state and
  assigns the requested roles (`members.py:158-192`). Roles default to
  `["member"]` when omitted (`:165`).
* UI: `/settings/members`.

> **Choose the role deliberately.** `member` is the default and is the right
> answer for a new employee. Read §3 first — `manager` is not "member plus a
> bit"; it hands over the entire member directory and both org-memory rights.

### Step 1b — Activate · ⚠️ **the step this runbook was missing, and its absence is the 2026-08-04 incident**

    PATCH /admin/members/{email}     { "status": "active" }

* Gate: `admin:members:manage` — `members.py:202-203`.
* UI: the **Activate** button on any `invited` row —
  `settings/members/page.tsx:252-258`.
* Behaviour: sets `status` and stamps `joined_at` with
  `COALESCE(joined_at, now())` (`members.py:227-230`), so re-activating a
  returning member keeps their original join date.
* Allow up to 60 seconds before they retry — their refusal is cached
  (`access.py:34`).

> **Step 1 alone does not let anybody in, and nothing says so at the time.**
> Invite writes `status = 'invited'` (`members.py:172`); `is_active` is
> `status == "active"` **exactly** (`access.py:288`). An invited colleague sees
> the identical "Your account is not active" screen as a total stranger, while
> the admin who invited them sees a row in the Members list and reasonably
> believes the job is done. ~~Sign-in is Entra SSO — there is no invitation email
> and therefore no acceptance event that could promote them.~~ **Steps 1 and 1b
> are one operation performed in two clicks.**
>
> ⚠️ **The struck sentence was rewritten 2026-08-24 (R4, D50), and the
> correction is narrower than it first looks.** There *is* now an invitation
> email (SC-2c) — but it is a **notification**, so it is still not an acceptance
> event, and it still cannot promote anybody: D50.1 refuses an accept-token by
> name. D50.3 promotes on BOTH planes at first sign-in: the Console
> `org_membership` inside the resolve's deployment arm, and the tenant
> `app_user.status` + identity shadow via `promote_invited_member` (one call
> site, `POST /signin/resolve`, admitted decisions only — review-round-1
> repair, same day). **Step 1b is therefore NOT required for an invited
> member**; it remains the reactivation path for a suspended one, and §6
> records why the guard lives in each UPDATE's own `WHERE`.

### Step 2 — Assign the role (if it is not the default)

    PUT /admin/members/{email}/roles     { "roles": ["member"] }

* Gate: `admin:members:manage` — `members.py:370-371`.
* Assignable set excludes `agent_service` — `permissions.py:146-148`.

### Step 3 — Assign the Center group

    POST /admin/groups/{slug}/members    { "email": "…", "role": "member",
                                           "grant_center_access": true }

* Gate: `admin:members:manage`, **plus** `admin:access:manage` when
  `grant_center_access` is true — `routes/admin/groups.py:336-337, 358-367`.
* What it writes: the membership row in `org_group_member`
  (`groups.py:375-383`) and, for one of the six Center slugs, an
  `allow feature:center.<slug>` row in `user_permission_override`
  (`groups.py:386-397`).
* `ON CONFLICT DO NOTHING` is deliberate: an existing override — **including an
  explicit deny** — is an admin decision this shortcut must not silently flip
  (`groups.py:350-352`). The response's `center_access_granted` is `false` in
  that case, and in two others (not a Center group; the admin opted out), so
  do not read `false` as failure without checking which.
* Removing the membership does **not** revoke the override
  (`groups.py:417-425`) — revoke it on the member's access screen.

### Step 4 — Verify, before telling them it is ready

1. **Read back the resolved answer, not the inputs:**

        GET /admin/members/{email}/access

   `routes/admin/members.py:512`. This returns the same computation
   `/auth/me` performs for the member themselves — roles, granted patterns,
   overrides, and the resolved yes/no per capability. Comparing it against §3's
   row for that role is the check.

2. **Confirm the nav they will see.** `/auth/me` returns
   `list(access.allowed_features())` (`routes/admin/me.py:84`), and
   `allowed_features()` iterates the literal tuple
   `acb_auth.permissions.FEATURES` — *not* the `feature_catalog` table. A slug
   seeded in SQL but absent from that tuple is invisible even to an owner
   holding `*`. The preflight's check 6 is that invariant.

3. **Confirm what they can run.** `/auth/me`'s `agents` array is the list of
   agents `assert_can_run_agent` will accept (`acb_auth/deps.py:613-626`). A
   `guest` gets `feature:chat` and **no** `agents:run:*`, so the chat pane opens
   and every run 403s — see §3.

4. **Confirm what they should NOT see.** Sign in as them (or ask them) and open
   `/notes`, `/tasks`, `/artifacts`. §3.3 says what each of those actually
   scopes and what it does not. **Hiding a control in the UI is a courtesy, not
   a boundary** — `workbench/control_plane/src/lib/access.ts:126-129` says so in
   its own comment. Verify against the API, not the sidebar.

### Step 5 — Off-boarding (the other half, recorded here so it is not invented later)

    PATCH  /admin/members/{email}   { "status": "suspended" }   # reversible
    DELETE /admin/members/{email}                               # soft: status → removed
    DELETE /admin/members/{email}/purge                         # hard: N8, irreversible

`resolve_access` treats status as a property of the *result*, not a filter on
the query, so a suspended member resolves to no access within the 60s cache TTL
at worst (`acb_auth/access.py:209-215`).

**Three actions, and the difference between them is the point.** Suspend is
reversible from this screen. Remove drops every role grant and keeps the
`app_user` row, because ~every user-scoped table refers to people by address —
the way back is a fresh invite plus an activation. **Delete permanently (N8)**
destroys the identity and every credential and grant keyed to it, and cannot be
undone by anything short of a database restore.

**You cannot off-board yourself, by any door.** All three call the one shared
guard `_common.assert_not_self_lockout` — a caller may not put their own row
into any status other than `active`, and the purge passes the outcome name
`_common.PURGE_OUTCOME` through the same helper. The rule is stated as "anything
that is not `active`", rather than as a list of destructive statuses, because
`is_active` is `status == "active"` exactly — so `invited` locks you out exactly
as `suspended` does, and a door that *deletes* the row falls under the same
sentence as the two that write the column. Renaming yourself and re-activating
your own row are unaffected. This is **not** `assert_owner_survives`: that one
is about the *org* keeping an owner and would let either of two owners suspend
themselves. The Members page reads `/auth/me`'s `email` and renders **This is
you** where the destructive controls would be
(`settings/members/selfGuard.ts`) — a courtesy, since the guard above is the
boundary.

**Bringing somebody back is `admin:members:manage`, and only that.** Neither
Invite nor approving a sign-in request can turn a `removed` or `suspended` row
back into `active` — both hold the weaker `admin:members:invite`. Invite
returns a removed member to `invited`; the activation is still Step 1b. See §6
*Repair round* for why that boundary is enforced in two places.

### N7 — off-boarding yourself · size: S (two routes + one page) · ✅ **BUILT 2026-08-04**

> **Built** on `ws-24-n7-self-removal-guard` (cut from `main` @ `2a41099b`).
> All seven done-whens met; fenced by `tests/unit/test_admin_member_offboarding.py`
> (22 cases) and `settings/members/selfGuard.test.ts` (8). The problem statement
> below is kept in the past tense it describes — it is the reason the guard is
> shaped the way it is.
>
> **One deliberate widening.** Done-when 2 named `suspended` and `removed`; the
> shipped rule is **"any status that is not `active`"**, which also covers
> `invited` — a status this same route accepts and which sets `is_active` to
> false just as surely (`EffectiveAccess.is_active` is `status == "active"`
> exactly). An enumeration would have had to remember the third door; the rule
> does not. It satisfies done-when 2 as written (fires on `suspended` and
> `removed`, not on `active`, not on `display_name`).

**Two doors reach the same outcome and only one of them is guarded.**
`DELETE /admin/members/{email}` refuses to remove the caller
(`members.py:269-272`, *"You cannot remove yourself."*). `PATCH
/admin/members/{email} {"status": "suspended"}` — which produces the same
`is_active=False` by the same mechanism — has **no self-check at all**; its only
invariant is `assert_owner_survives`.

That invariant is what has been standing in for one. With exactly one owner it
happens to refuse, so the hole is invisible. **Add a second owner — which §2
Step 2 exists to do — and either owner can suspend themselves out of the admin
surface in one click.** `admin:members:manage` is the floor for restoring it, so
the recovery is the other owner, or hand-run SQL if both do it.

**And the UI offers exactly that click.** `settings/members/page.tsx` renders
**Suspend** on every active row; it never learns who the viewer is, so it draws
the button on the owner's own row like any other. Meanwhile it exposes **no
removal control at all** — the shipped `DELETE` route has no caller in the
product, so the only off-boarding an owner can perform from the UI is the
unguarded one.

**Done when** — all seven ✅

1. ✅ A single shared guard — one helper, called by **both** `update_member` and
   `remove_member` — refuses a caller acting destructively on their own row.
   One rule with two call sites, not two rules: the old split is exactly
   how the two doors came to disagree.
   → `_common.assert_not_self_lockout` (`_common.py:248`), called from
   `members.py:214` (PATCH) and `:283` (DELETE). Neither route compares
   addresses itself any more, and
   `test_both_off_boarding_doors_call_the_one_shared_guard` finds them from
   the **router**. ⚠️ **This bullet first claimed "a third one added later is
   included automatically." It does not** — the test looked at a single path,
   which is exactly why the third door below was invisible to it. It now
   enumerates `PUT …/roles` as well, and the claim is retracted rather than
   repaired: enumerate the doors, do not trust a test to find them.
2. ✅ It fires on `PATCH` for **`suspended` and `removed`**, and not on
   `active` — an owner re-activating their own row is harmless — nor on
   `display_name`. → Shipped as the wider rule (see the note above);
   `status=None` returns early, so a display-name-only patch is safe by
   construction rather than by the caller remembering to skip the check.
3. ✅ The comparison is case-insensitive on both sides. An IdP that changes UPN
   casing between sessions must not turn the guard off (the same property
   `load_owned_meeting` preserves for Notes). → Both sides `.strip().lower()`;
   four parametrised cases, and an empty address on either side matches
   nothing. Mutation-checked: dropping `.lower()` fails **three** of the four —
   the fourth is a whitespace case and dies only when `.strip()` goes. (The
   original "exactly those four" was measured wrong.)
4. ✅ It is independent of `assert_owner_survives`: a test seeds **two** owners and
   asserts self-suspension is still refused, because the old refusal was that
   invariant firing by accident and would disappear the moment a second owner
   exists.
   → `test_self_suspension_is_refused_even_when_another_owner_survives`, paired
   with `test_that_same_world_still_lets_the_other_owner_be_suspended`: same
   world, same route, only the identity differs. The two 409s are told apart by
   their detail text *and* by what was written — deleting the guard from
   `update_member` fails the first test (the PATCH goes through).
5. ✅ The Members page learns the viewer's identity (**`GET /auth/me`** already
   returns `email` — `routes/admin/me.py:76`, mounted at `/auth`, **not**
   `/admin/me` as this bullet first said) and renders **no destructive
   control** on that row, labelling it as the viewer's own instead. →
   `access.email` → `rowActions()`; the row renders **This is you** in place
   of Suspend/Remove. ⚠️ The wiring test first asserted only that the guard was
   *mentioned before* the call site — which `void actions.canSuspend;` above a
   `{true && (` satisfies while the control renders on your own row. It now
   asserts the JSX condition itself. That is the **fourth** test in this
   workstream to assert less than its docstring claimed; see §6 *Repair round
   3*.
6. ✅ A **Remove** control appears on other rows, calling the existing `DELETE`
   route, with a confirmation step naming the person — it drops every role
   assignment and is not a toggle. → `RemoveDialog`; the row only *opens* it,
   the `fetch` lives in `removeMember`, and the copy points at Suspend for
   anybody who wanted the reversible act.
7. ✅ Refusals surface in the UI rather than failing silently. Hiding the control
   is a courtesy; the server is the boundary (`lib/access.ts:126-129`).
   → `removeMember` sets `error` from the gateway's `detail` and re-reads the
   roster on **every** response, refusals included (the N6a rule: a refusal is
   about the row that was clicked, so that row is the stale one).

#### The third door — `PUT /admin/members/{email}/roles` · ✅ **CLOSED 2026-08-04**

**Done-whens 1–7 close the two doors that set `status`. There is a third, and
it was reachable — verified by driving the real route, not by reading it.**

In the two-owner world §2 Step 2 exists to create:

    PUT /admin/members/owner@fracktal.in/roles  {"roles": ["member"]}
    as owner@fracktal.in                        -> ACCEPTED

The caller kept `status = 'active'` and lost `admin:members:manage` — which is
the floor for undoing it, so recovery was the other owner or hand-run SQL on
prod. With a *single* owner the same call was refused, but by
`assert_owner_survives` and in its wording: the same coincidence of org size
that invariant 4 exists to stop relying on.

This route never touches `status`, so `assert_not_self_lockout` cannot see it.
The lockout is reached by taking the **permission** instead of the state.

**`{"roles": []}` is not the sequence** — a separate check rejects an empty
set with *"At least one role is required."* The reachable one is a
self-demotion to any role that does not carry the recovery permission.

**Closed by `_common.assert_not_self_demotion`**, called from
`set_member_roles` **before** `assert_owner_survives`, so the caller hears the
true reason rather than being told to assign another owner and then hitting the
real wall.

It decides on **the permissions the new roles actually grant**
(`_ROLE_PERMISSIONS_SQL` → `permission_matches`), not on an allowlist of role
slugs. A slug allowlist would refuse a custom role that legitimately carries
`admin:members:manage`, and would enforce a rule nobody wrote down. An owner
holding `*` passes through `permission_matches`.

The distinction that keeps it honest: **the guard is about the permission, not
about self-editing.** An owner moving themselves to `admin` still holds the
recovery permission and is allowed — pinned by
`test_a_self_edit_that_keeps_the_rights_is_allowed`. A blanket "you may not
touch your own roles" would have been simpler and wrong.

⚠️ The fake answers `_ROLE_PERMISSIONS_SQL` from a Python mapping
(`_FakeDB.ROLE_PERMISSIONS`), so every behavioural case here would stay green
if the real statement started reading slugs. The structural assertion is the
fence — the same lesson as `_PROVISION_MEMBER_SQL` in §6.

Mutation: deleting the call from `set_member_roles` fails **3** —
`test_self_demotion_is_refused_even_when_another_owner_survives`,
`test_the_last_owner_demoting_themselves_hears_the_self_refusal`, and the
door-enumeration test.

**A fourth door exists and is already guarded — measured, not assumed.**
`PUT /admin/members/{email}/overrides` (`members.py:524-575`) refuses a caller
who denies themselves `admin:access:manage`: *"This would revoke your own
access management permission."* So the self-lockout rule now holds on all four
routes that can reach it.

⚠️ **But it holds by a fourth, independent inline comparison**, not through the
shared helper — the exact shape that let the first two doors disagree, and it
is invisible to the door-enumeration test because that test covers three
specific paths. It also guards a *different* permission (`admin:access:manage`,
its own gate) than the roles door does (`admin:members:manage`), which may be
right and has never been stated as a decision. **Worth one consolidating pass**
— four guards, one rule — before a second admin exists. Not done here: it is a
refactor of working code, and this ticket's scope was the doors that were open.

**Verification**

    uv run pytest tests/unit/test_admin_member_offboarding.py -q
    uv run pytest tests/unit/test_signin_requests.py \
                  tests/unit/test_org_access_control.py \
                  tests/unit/test_org_access_enforcement.py -q
    uv run ruff check . --select F821,F601,F602,F502,F7,B006
    cd workbench/control_plane && npx tsc --noEmit
    cd workbench/control_plane && npx vitest run src/app/settings/members/selfGuard.test.ts

Measured 2026-08-04: **22 passed**, **133 passed** (52 + 50 + 31 — unchanged
from `main`), `All checks passed!`, tsc silent, **8 passed**.

**The test fake is shared, not copied.** `tests/unit/_admin_fakes.py` holds the
`app_user`/`user_role`/`access_request` mirror that `test_signin_requests.py`
grew; both files import it. Its warning travels with it — the mirror can only
agree with itself about anything that lives *inside* a SQL statement. That does
not apply to this ticket's guard, which is Python in the route, so the
behavioural cases here really do exercise it; what does apply is that
`assert_not_self_lockout` and `assert_owner_survives` **both answer 409**, so no
test may assert the bare status code.

**No migration.** No new permission slug — both routes keep
`admin:members:manage`.

### N8 — deleting a member permanently · size: M (one route + one page + the table map) · ✅ **BUILT + REPAIRED 2026-08-05**

> **Built** on `ws-24-n8-purge-member` (cut from `main` @ `e911e9d2`), then
> **repaired on the same branch** after verification returned FAIL — see
> *Repair round 1* at the end of this section, which is where the numbers and
> the new fences are.
> Fenced by `tests/unit/test_admin_member_purge.py` (39 cases), one new case in
> `test_admin_member_offboarding.py`, 2 new cases in `selfGuard.test.ts`, and
> 5 in `confirmPurge.test.ts`.

**The owner's words:** *"allow the owner to be able to remove and completely
delete everyone else apart from themselves."* Remove was the only off-boarding
that existed, and it is soft by design.

**A second, harder action beside Remove — not a flag on it.** `DELETE
/admin/members/{email}` is unchanged, and its reasoning stands: the `app_user`
row is kept because ~every user-scoped table refers to people by address, and
what matters for access is that the member resolves to nothing. A `?hard=true`
on that route would have put the irreversible path one typo from the reversible
one, so the purge is its own route: `DELETE /admin/members/{email}/purge`, on
the same `admin:members:manage`.

#### The decision: purge the person, keep their work

Delete the identity and everything that exists only to grant access or let the
platform act as them; leave what they authored readable, and leave the audit
trail alone. **Purging their content too was considered and rejected** — it is
unrecoverable and, in this schema, it silently takes shared artefacts (a room
with other participants) along with the private ones.

⚠️ **"Delete the identity" cannot mean "erase the address everywhere."** The
email address IS the join key across ~50 tables (`apps.owner_email`,
`gtd_items.user_id`, `workflows.owner_email`, `app_audit.user_email`), so
scrubbing it is not a redaction — it is a deletion of the rows it keys. **That
is why nothing is anonymised:** an anonymised `owner_email` would not hide a
person, it would orphan their apps.

**The table map**, enumerated from `infra/postgres/` — every FK to
`app_user(id)` and every email-string column — and held in
`members._PURGE_DELETES` / `_PURGE_KEEPS`:

| Verdict | Tables | Why |
|---|---|---|
| **deleted** | `user_role`, `user_permission_override`, `org_group_member` | access grants (FK to `app_user.id`, all `ON DELETE CASCADE`; deleted explicitly anyway so the counts are real and the purge does not depend on a cascade a later migration could drop) |
| **deleted** | `chat_session_participant`, `app_grants`, `app_tool_grants` | access grants keyed by address; a remembered "always allow this app to use this tool" is a standing authorization to act as them |
| **deleted** | `email_accounts`, `wa_accounts`, `task_accounts` | `credentials_encrypted NOT NULL` — the live OAuth/API tokens. See the cascade note below |
| **deleted** | `gtd_items`, `gtd_projects` **where `account_id IS NOT NULL`** | the SYNCED half, which `task_accounts` cascades away regardless. Deleted *explicitly* and counted on this side, because the alternative — letting the cascade take them silently — is what made the response report them as kept |
| **deleted** | `chat_session` **where `visibility = 'private'`** | their own conversations |
| **deleted** | `access_request` | they become a stranger again. Leaving an `approved` row for somebody with no `app_user` row means their next sign-in bumps a row the Requests tab never renders (it shows `pending` only) — the invisible lockout §6 exists to end |
| **deleted** | `app_user` | the member record, last |
| **kept** | `app_audit`, `audit_event`, `agent_run`, `agent_file_history`, `pending_actions`, `pending_commit` | **an audit trail that disappears when you delete the person is not an audit trail.** `app_audit` already says so in its own schema: `app_id UUID` with *no* FK, commented "audit survives hard delete" |
| **kept** | `apps`, `app_versions`, `app_data`, `app_pins`, `workflows*`, `meeting*`, `notes_glossary`, `gtd_people`, `org_group.created_by`, `person` | authored work and org records |
| **kept** | `gtd_items`, `gtd_projects` **where `account_id IS NULL`** | the LOCAL half — outcomes and actions they wrote here, not a mirror of anybody's provider. ⚠️ **The `account_id` predicate is load-bearing, not decoration:** without it this row counts the SYNCED rows the line above destroys, and reports them as survivors |
| **kept** | `chat_session` **where `visibility <> 'private'`** | a shared room has other participants and cascades `chat_message`; one person's off-boarding must not take a shared transcript |
| **anonymised** | *(none)* | see above — the address is the join key, not a display attribute |

#### ⚠️ The cascade map — a naive delete takes far more than it looks like

Three of the deleted rows own subtrees the schema already declares
`ON DELETE CASCADE`. **Counts below are derived from the numbered migrations,
not remembered** — the first version of this list understated every one of them
(see *Repair round 1*), which on a route whose whole safety argument is "the
admin is told the blast radius before clicking" is the wrong direction of
error. The map now lives as data in `members._CREDENTIAL_CASCADES` and is
pinned against `infra/postgres/` by
`test_the_cascade_map_is_the_one_the_schema_declares`; it is hand-maintained
and says so.

* **`email_accounts` → 17 direct children, 20 with transitives.** `email_actions`,
  `email_ai_drafts`, `email_assistant_settings`, `email_attachments`†,
  `email_cold_senders`, `email_contacts`, `email_embeddings`†,
  `email_executed_rules`, `email_folders`, `email_knowledge`,
  `email_learned_patterns`, `email_messages`, `email_newsletters`,
  `email_rule_guidance`, `email_rule_patterns`, `email_rules`, `email_senders`,
  `email_sync_log`, `email_thread_status`, `email_voice_profiles`.
  **The whole mirrored mailbox.** († via `email_messages`; `email_actions` via
  `email_rules`.)
* **`wa_accounts` → 14 direct, 16 with transitives.** `wa_ai_drafts`,
  `wa_categories`, `wa_chat_avatars`, `wa_chat_labels`, `wa_chat_status`,
  `wa_chats`, `wa_commitments`, `wa_contacts`, `wa_group_summaries`,
  `wa_labels`, `wa_media`†, `wa_message_embeddings`†, `wa_messages`,
  `wa_saved_replies`, `wa_sync_log`, `wa_templates`. († via `wa_messages` —
  `wa_media` is **not** a direct child, and the earlier list said it was.)
* **`task_accounts` → `gtd_items`, `gtd_projects` (2 direct) + `gtd_waiting`.**
  The **SYNCED** half only: rows with `account_id` set. LOCAL rows carry
  `account_id IS NULL` and survive.

⚠️ **`gtd_items` and `gtd_projects` are the ONLY tables in that whole blast
radius that carry a `user_id` of their own.** Everything else is keyed by
`account_id` alone — it is mirror-of-a-credential and nothing more, which is
why "the whole mailbox goes" is an honest summary for it and a per-table count
would be noise. The two GTD tables are *dual-source*: the same table holds rows
the person wrote here, which the purge is not entitled to take. That is the
rule the fence enforces, and it is what forces both halves of both tables onto
the report (`test_every_person_keyed_cascade_child_is_reported_on_one_side_or_other`).

**The credential cannot be deleted without the row** — it is a `NOT NULL`
column on it — and leaving a departed colleague's live tokens in the database
is precisely the hole a purge exists to close. The mirrors are mirrors: source
systems stay authoritative (root `AGENTS.md`, global constraint 8).

> **The one place the person/content split is NOT clean, recorded rather than
> hidden:** `task_accounts → gtd_items` means a purge deletes the person's
> *SYNCED* tasks while keeping their *LOCAL* ones. Unlike a mailbox, GTD items
> are the product's own work objects, and that asymmetry was not decided by
> anybody — it falls out of where the FK happens to sit. It is made visible
> rather than resolved: **counts on both sides of the response**
> (`synced_tasks` / `synced_projects` deleted, `tasks` / `projects` kept) and
> named in the confirmation, which says the mirrored rows go with the account
> and the ones created here stay. ⚠️ **That sentence was false when it was
> first written** — the response reported the destroyed rows as kept and the
> confirmation's Kept list said "tasks they authored" without qualification.
> **Open owner question, unchanged:** should a purge disconnect the account and
> null the credential instead of deleting the row, so the synced mirror
> survives?

#### The guards, and the report

1. **Invariant 4 through the shared helper.** `assert_not_self_lockout` is
   called with `_common.PURGE_OUTCOME` (`"purged"`), which is deliberately *not*
   a member of `VALID_STATUSES` — a purge deletes the row rather than writing
   the column. The helper's rule ("anything that is not `active`") is what lets
   a delete-shaped door fall under a sentence written for update-shaped ones,
   and it is the reason the rule was never written as a list.
2. **Invariant 1.** `assert_owner_survives` — purging the last owner is not a
   recoverable mistake; there would be no owner left to invite anybody and no
   row to promote back.
3. **The response says what happened, per table**: `{"deleted": {...},
   "kept": {...}}`. A purge that answers `{"status": "ok"}` is unauditable — the
   admin cannot tell one that destroyed a live OAuth token from one that matched
   nothing. `count_sql` and `delete_sql` are derived from the **same** `where`
   clause, so the number reported and the rows destroyed are one predicate by
   construction.
4. **Audited before it commits.** `acb_audit.record` opens its own session, so
   the entry survives a rollback of the purge. The trade-off is deliberate and
   this way round: an audit line for a purge that then failed is a false
   positive an admin can reconcile against a roster that still shows the
   person; a *completed* purge with no audit line is unreconcilable, because
   every row that could say who it was is gone.
   ⚠️ **"The audit entry survives a rollback" is true. "A completed purge
   always leaves an audit row" is NOT.** `acb_audit/log.py:49` wraps the write
   in `except Exception` and logs `audit.persist_failed`, so an audit DB that
   is down turns the call into a no-op and the purge still commits. That
   default is right for every other caller (a webhook must not 5xx over an
   audit write) and is deliberately not overridden here, because refusing
   would leave the credentials in place — but it means the only **guaranteed**
   record of a purge is the `member_purged` structlog line. Recorded, not
   fixed: making one caller strict is a change to `acb_audit`'s contract.
5. **One transaction**, one `commit()`, at the end. A half-purge that deleted
   the credentials but left the account active is worse than either outcome.
6. **The permission is `admin:members:manage`, and it is pinned.** Deleting the
   `require_permission` dependency leaves `Depends(require_admin_user)`, whose
   floor is `admin:members:read` — a permission `manager` holds (D14, §3.0) —
   so the mutation silently hands hard-delete of any member to every manager.
   The slug is read out of the dependency's closure and asserted exactly, so
   *widening* it fails too.
7. **UI:** a filled-destructive **Delete permanently** beside the outlined
   **Remove**, gated by `rowActions().canPurge` (never on the viewer's own row;
   *offered* on an already-removed row, because "remove, then delete once you
   are sure" is the ordinary sequence). The confirmation names both halves and
   requires the address to be typed; the counts come back and are shown. **The
   typed-confirm rule is `confirmPurge.ts`**, not an inline comparison: it is
   unit-tested by vitest (case-insensitive, whitespace-tolerant, and an empty
   address confirms *nothing* rather than everything) and the page's wiring is
   asserted from pytest. Inline, it was fenced only by a grep for the copy
   around it.

#### The door enumeration, again

`test_both_off_boarding_doors_call_the_one_shared_guard` is now
`test_every_off_boarding_door_calls_the_one_shared_guard` and lists **four**
doors explicitly. N7 already retracted "a third one added later is included
automatically" — and N8 proved the retraction right by adding a fourth door on
a *fifth path*, which the old test could not have seen. **Enumerate the doors;
nothing will do it for you.** The fifth route that reaches this outcome (`PUT
…/overrides`) still guards it with its own inline comparison and is
deliberately not listed — that inconsistency is recorded above, not asserted as
correct.

#### Repair round 1 (2026-08-05) — the response reported destroyed rows as kept

Verification returned **FAIL**. Every finding below was established by reading
the schema and running mutations, not by review.

**F1 — the hard one, and the shape is worth keeping.** `_PURGE_KEEPS` counted
`gtd_items` on `lower(user_id) = :email` with no exclusion, while
`_PURGE_DELETES` took `task_accounts`, whose FK cascades that very table
(`48_task_manager_gtd.sql:93`) — and `sync.py:141-149` stamps **both**
`user_id` and `account_id` on every SYNCED row, so those rows matched the keep
clause *and* were destroyed. A member with 847 synced tasks got
`kept: {"tasks": 847}` with all 847 gone. **The response did not merely miss a
destruction — it reported it as a survival**, in the half an admin reads for
reassurance. `gtd_projects` (same cascade, `:73`) was on neither list.

> The discipline already existed one line up: `shared_rooms` carries
> `AND visibility <> 'private'` precisely so the keep count excludes what the
> delete side takes. It simply was not applied to the table where the taking
> is done by a *cascade* three entries away rather than by a statement.

**Why nothing caught it — the missing fence.** Every structural assertion
compared a row-spec to itself (`count_sql` vs `delete_sql`) or to its own
table's other half. Nothing asserted that a KEEP clause's row set is disjoint
from what the DELETE side *cascades* away, and **`_FakeDB` models no foreign
keys at all**, so no behavioural case over it could see a cascade either. The
gap was cross-table by nature and is now fenced structurally:

* `tests/unit/_schema_cascade.py` derives the FK cascade graph from the
  numbered migrations (`schema.generated.sql` is skipped — it predates
  migration 130).
* `test_no_keep_clause_survives_a_cascade_on_the_delete_side` — a KEEP clause
  inside the delete side's blast radius must be the **exact complement** of a
  matching DELETE clause on the same table.
* `test_every_person_keyed_cascade_child_is_reported_on_one_side_or_other` —
  every cascade descendant carrying its own person column must appear on the
  report at all. This is what forces `gtd_projects` on.
* `test_the_cascade_map_is_the_one_the_schema_declares` — `_CREDENTIAL_CASCADES`
  is re-derived and compared.
* `_admin_fakes._FakeDB` now states in its own docstring that it models no
  cascades, and why that makes the claim structural rather than behavioural.

**F2 — the gate on the most destructive route was unfenced.** Deleting
`dependencies=[require_permission("admin:members:manage")]` left **162 tests
green**; the fallback floor is `admin:members:read`, which a seeded `manager`
holds. The nearest existing wiring test filters on
`path.startswith("/admin/members/requests")` and could not see this route.
Pinned now, by reading the slug out of the dependency's closure.

**F3 — the type-to-confirm gate was unfenced.** `const confirmed = true;` left
**32 pytest + 173 vitest green**; done-when 6 was tested by grepping the dialog
for the word "Type". The rule moved to `confirmPurge.ts` with its own vitest
cases and a pytest wiring assertion.

**F4 — the cascade map was understated, in the dangerous direction.** 15 of 20
email tables named; `wa_media` listed as a direct child of `wa_accounts` when
it hangs off `wa_messages`; `wa_chat_status` and `wa_sync_log` missing;
"labels" standing in for two tables. Corrected against the migrations, moved
out of prose into `_CREDENTIAL_CASCADES`, marked hand-maintained, and pinned.

**F5 — recorded, not fixed.** `acb_audit/log.py:49` swallows every exception,
so a failed audit write still lets the purge commit. Stated at guard 4 above
and in the route docstring.

**Mutants measured red and reverted** (`git status` clean after each):

| Mutation | Result |
|---|---|
| `tasks` KEEP clause back to `lower(user_id) = :email` | **5 red** — incl. the complement fence naming `gtd_items` and its cascade parent |
| `synced_projects` delete-side row-spec removed | **5 red** — the complement fence: "reported as KEPT, but the purge cascades it away" |
| `gtd_projects` removed from **both** lists (the shipped state) | **5 red** — the person-keyed-child fence names it |
| `require_permission(...)` deleted from the purge route | **1 red** (of 219 across all six admin files; previously **0**) |
| purge route widened to `admin:members:read` | **1 red** |
| `const confirmed = true;` in `PurgeDialog` | **1 red** pytest; vitest stays 178 green — no jsdom, the wiring test is the fence |
| `purgeConfirmed` empty-address guard dropped | **1 red** vitest |
| `email_embeddings` removed from `_CREDENTIAL_CASCADES` | **1 red** — the map vs the migrations, both lists printed |
| `_FakeDB` stops modelling `IS [NOT] NULL` | **3 red** — the fake cannot silently widen a clause |

**Verification** (measured 2026-08-05, branch `ws-24-n8-purge-member`, after
the repair)

    uv run pytest tests/unit/test_admin_member_purge.py -q          # 39 passed (was 32)
    uv run pytest tests/unit/test_admin_member_offboarding.py -q    # 28 passed
    uv run pytest tests/unit/test_signin_requests.py \
                  tests/unit/test_org_access_control.py \
                  tests/unit/test_org_access_enforcement.py \
                  tests/unit/test_admin_groups.py -q               # 152 passed, = main
    uv run ruff check . --select F821,F601,F602,F502,F7,B006        # All checks passed!
    cd workbench/control_plane && npx tsc --noEmit && npx vitest run # clean, 178 passed (was 173)

⚠️ `tsc` fails on stale `.next/types` / `.next/dev/types` validator files left
by another branch's routes. `rm -rf` those two directories first; it is not a
defect in this change.

**No migration, no new permission slug.** The purge is `admin:members:manage`,
the same gate Remove holds — deleting somebody who is already off-boarded is
not a *stronger* decision than off-boarding them, and a new slug is nobody's
grant until an admin creates it.

⚠️ **`infra/postgres/schema.generated.sql` is stale** — it predates migration
130 and contains no `user_role`, `org_group`, or `access_request`. The table map
above was derived from the numbered migrations, which are the schema of record.
Re-running `scripts/dump_schema.sh` is owed, independently of this ticket.

---

## 3. THE CAPABILITY MATRIX

> **How to read this.** Every cell was verified against code on 2026-08-04 and
> carries the `file:line` that settles it. Where a cell could not be
> established, it says **UNVERIFIED** and why. A confident wrong cell here is
> worse than an admitted gap — this is what the owner relies on when deciding
> to let real people in.

### 3.0 Two corrections to the received account — read these first

**(a) Role grants come from TWO migrations, not one.** Every summary of these
roles circulating in the corpus quotes `130_org_access_control.sql` alone.
`131_integration_memory_permissions.sql` adds more, and it changes the answer:

| Role | 130 | 131 adds |
|---|---|---|
| `owner` | `*` (`130:183-190`) | nothing — `*` already covers it (`131:42`) |
| `admin` | `130:198-207` | `integrations:use:*`, `memory:read_org`, `memory:write_org` (`131:45-53`) |
| `manager` | `130:210-223` | `integrations:use:*`, `memory:read_org`, `memory:write_org` (`131:57-65`) |
| `member` | `130:228-239` | `integrations:use:*`, **`memory:read_org`** (`131:70-78`) |
| `guest` | `130:242-249` | **nothing** (`131:80`) |
| `agent_service` | `130:252-259` | `integrations:use:*`, `memory:read_org`, `memory:write_org` (`131:85-93`) |

So a `member` **can read organisation memory** and cannot write it — the exact
split `131:67-69` argues for. Quoting `130` alone gets that cell wrong.

**(b) `data:org:read` grants nothing. It has zero consumers.** It is declared
in `permissions.py:132`, granted to `admin`, `manager` and `agent_service`, and
referenced in `access.py:148`'s legacy-fallback list — and **no route, query or
predicate in the repository ever checks it.** A repo-wide search for
`data:org:read` outside the vocabulary, the seed migrations and the specs
returns nothing.

This matters because `manager` is routinely described as "the role with org-wide
visibility, which contradicts department privacy". The contradiction is real but
it is **not** `data:org:read` — that permission is a name with no mechanism.
What actually widens a manager is: the whole `/admin` read surface
(`admin:members:read`), `feature:approvals`, `feature:observability`,
`feature:whatsapp`, and `memory:write_org`. See **D14** in `work_plan.md` §3.

### 3.1 Where a feature is actually enforced

A `feature:` grant is only a boundary where a route checks it. Measured:

| Surface | Server-side gate | Anchor |
|---|---|---|
| Chat + Rooms | `require_feature_router("chat")` | `routes/chat.py:36`; `routes/rooms.py:56` |
| Email | `require_feature_router("email", exempt=[…])` | `routes/email/core.py:39` |
| Tasks | `require_feature_router("tasks")` | `routes/tasks/core.py:29` |
| Notes | `require_feature_router("notes", exempt=[2 bot-token routes])` | `routes/notes/core.py:33-40` |
| WhatsApp | `require_feature_router("whatsapp", exempt=[…])` | `routes/whatsapp/core.py:35` |
| Workflows | `require_feature_router("workflows", exempt=EXEMPT_ROUTES)` | `routes/workflows/core.py:34` |
| Approvals | `require_feature_router("approvals")` | `routes/actions.py:34` |
| Integrations | `require_feature_router("integrations")` | `routes/integrations.py:37`; `routes/integrations_skills.py:31` |
| Custom Apps | **not** a feature gate — `apps:use:*` to open, `feature:build.apps` to author | `routes/apps/_common.py:32, 129-146, 149-169` |
| **Memory** | ⚠️ **no `feature:memory` check anywhere.** Router requires the internal Bearer; authorization is per-scope | `routes/memory.py:45-48`, `_authorize_scope` `:128-167` |
| **Artifacts / workspace** | ⚠️ **no `feature:artifacts` check anywhere.** `get_current_user` only | `routes/workspace.py:53` |
| **Observability** | ⚠️ **no `feature:observability` check.** Any authenticated caller | `routes/observability.py:46-51` — the comment says this is deliberate: operational metadata, never message content |
| **Dashboard** | no backend at all | `workbench/control_plane/src/app/dashboard/page.tsx:1-14` is a `ComingSoon` stub |

**Consequence, and it is the single most load-bearing line in this document:**
for Memory, Artifacts and Observability the `feature:` grant hides the nav pane
and nothing more. A member denied `feature:memory` can still reach `/api/memory/…`
through the BFF; what stops them reading a colleague's memories is
`_authorize_scope`, not the feature. Scope everything you reason about here on
the per-object rule, not on the nav.

### 3.2 Role × surface

`✅` = reachable · `—` = not granted · `⚠️` = reachable but see the note.

| Surface (feature) | owner | admin | manager | member | guest | Settles it |
|---|---|---|---|---|---|---|
| Chat / Rooms (`chat`) | ✅ | ✅ | ✅ | ✅ | ✅ | `130:189, 203, 217, 235, 248` |
| Email (`email`) | ✅ | ✅ | ✅ | ✅ | — | `130:203, 217, 235`; guest list `130:248` |
| Tasks (`tasks`) | ✅ | ✅ | ✅ | ✅ | — | `130:217, 235` |
| Notes (`notes`) | ✅ | ✅ | ✅ | ✅ | — | `130:218, 235` |
| Memory pane (`memory`) | ✅ | ✅ | ✅ | ✅ | — | `130:218, 236` |
| Dashboard (`dashboard`) | ✅ | ✅ | ✅ | ✅ | — | `130:218, 236` — **a stub page**, `dashboard/page.tsx:1-14` |
| Artifacts (`artifacts`) | ✅ | ✅ | ✅ | ✅ | — | `130:219, 236` |
| Observability (`observability`) | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | Feature at `130:219` (manager) — but the API is open to **any authenticated caller**, `observability.py:51` |
| Approvals (`approvals`) | ✅ | ✅ | ✅ | — | — | `130:219`; absent from member `130:234-237` |
| WhatsApp (`whatsapp`) | ✅ | ✅ | ✅ | — | — | `130:217`; member list omits it, and `130:226-227` says so explicitly |
| **Workflows** (`workflows`) | ✅ | ✅ | — | — | — | Only `feature:*` covers it: `130:203`. Absent from manager `130:216-221` and member `130:234-237`. **Granting it has a consequence — §3.4.** |
| Integrations (`integrations`) | ✅ | ✅ | — | — | — | `130:203`; `integrations:manage` at `130:205` |
| Models (`models`) | ✅ | ✅ | — | — | — | `feature:*` only |
| Agent Registry (`agents`) | ✅ | ✅ | — | — | — | `feature:*` only; `agents:manage` at `130:203` |
| Build panes (`build.agents`, `build.apps`) | ✅ | ✅ | — | — | — | `feature:*` only; `130:226-227` names both as deliberate member omissions |
| Centers (`center.*`) | ✅ | ✅ | — | — | — | `feature:*` covers them since `permissions.py:95-100`; nobody else gets one without a grant (`140_center_features.sql:19-25`, all `is_default=false`) |
| Custom Apps — **use** (`apps:use:*`) | ✅ | ✅ | ✅ | ✅ | ✅ | `130:204, 220, 237, 248`; enforced `_common.py:129-146` |
| Custom Apps — **author** (`feature:build.apps`) | ✅ | ✅ | — | — | — | `_common.py:149-169` |
| Run any agent (`agents:run:*`) | ✅ | ✅ | ✅ | ✅ | **—** | `130:203, 220, 237`. **Guest has none** (`130:248`), so a guest opens `/chat` and every run 403s at `deps.py:613-626` |
| Admin surface (`admin:members:read` floor) | ✅ | ✅ | ✅ | — | — | `130:200, 221`; floor at `admin/_common.py:77-91`. **A manager reads the whole member directory** and `/auth/me` returns `is_admin: true` for them (`me.py:96`) |
| Admin writes (`admin:*:manage`) | ✅ | ✅ | — | — | — | `130:200-202`; manager's list `130:216-221` has only `admin:members:read` |
| Org memory — read (`memory:read_org`) | ✅ | ✅ | ✅ | ✅ | — | `131:50, 62, 75`; guest excluded `131:80` |
| Org memory — write (`memory:write_org`) | ✅ | ✅ | ✅ | — | — | `131:50, 62` vs member's `131:75` |
| `data:org:read` | ✅ | ✅ | ✅ | — | — | `130:205, 221` — **grants nothing; zero consumers.** §3.0(b) |

### 3.3 What a grant actually exposes, per app

This is the half that matters. A `feature:` grant opens a surface; the
per-object rule decides whose rows you see through it.

| App | The scoping rule | Verified at | What a `member` sees |
|---|---|---|---|
| **Notes** | Owner-scoped. One predicate, written once, case-insensitive; `NULL` owner = pre-migration-95 legacy, visible to all | `routes/notes/core.py:192-194` (`OWNED_MEETING_PREDICATE`), `:197-217` (`load_owned_meeting`, 404-not-403), bound in `routes/notes/meetings.py:77` | **Their own meetings only** — on the list/get/patch/delete/dispatch paths. ⚠️ **Six other route families are NOT owner-scoped** — §4. |
| **Tasks — items and accounts** | Owner-scoped. Per-user rows: **27** `user_id = :uid` predicates on items; accounts asserted separately | `routes/tasks/items.py` (measured: 27 occurrences of the exact predicate); `routes/tasks/core.py:189-197` (`_assert_account_owner`), bound at `accounts.py:190, 259, 285, 325, 406` | Their own items only, and only task accounts they own. No team/Center sharing exists yet — that is WS-14 C1 / D13 (`gtd_project_grant`). |
| **Tasks — the people directory** | ✅ **Field-level projection on read, `admin:members:manage` on every write** (N4, closed 2026-08-04). The directory itself is deliberately org-wide | `routes/tasks/core.py` — `can_read_hr_fields` (`admin:members:read`) + `require_people_write()` (`admin:members:manage`); `routes/tasks/people.py` — `HR_FIELDS`, `_row_to_person(row, *, include_hr)`, and the four write routes' `dependencies=[…]` (the fourth is `capability.py`'s `POST /people/embed`). Pinned by `tests/unit/test_tasks_people_scoping.py` | **The basic org chart** — name, email, role, title, department, team, manager, ClickUp id — and **nothing else**: skills, `skills_source`, résumé summary, years of experience and capacity/load/available all come back null/empty, and `?q=` will not match a skill. No writes at all: all four 403. A `manager` (`admin:members:read`) sees the HR half but still cannot write it. |
| **Email** | Per-account ownership, asserted on both the message-scoped and account-scoped loaders | `routes/email/core.py:168-180` (`_provider_for_account`), `:576-580` (`_assert_account_owner`) | Only accounts they own. There is (**owner-reported, not measured** — see the note in the preamble) exactly one account, `vjvarada@fracktal.in`, so a colleague's `/email` is empty until they connect their own. Shared mailboxes are ownerless work — `work_plan.md` §4. |
| **Memory** | One rule per scope shape; an unrecognised shape is refused | `routes/memory.py:128-167` | Their own `<email>` scope (`_authorize_person` `:112-125` — **explicitly not readable by admins**); their own `prefs:` (`:95-100`); rooms they can read (`:82-92`); org memory read-only (`:73-79` + `131:75`); and — see below — **every shared agent's compartment**. |
| **Memory, the wide edge** | `agent:<name>` is gated on `can_run_agent(name)`, and `member` holds `agents:run:*` | `routes/memory.py:103-109` + `130:237` | **A member can read and write the memory compartment of every agent they can run**, which is every agent. Documented as by-design ("shared across the agent's users"), but it means an agent that remembers something from the owner's conversation is readable by any member. |
| **Chat sessions / Rooms** | `visibility` defaults to `private`; five explicit ways in | `138_groups_and_session_participants.sql:71` (default `'private'`); `gateway/rooms.py:368-403` (`SESSION_VISIBLE_SQL`) | Their own sessions, plus any where they are a participant directly, via a `group:` they belong to, via an `org` participant row, or where `visibility='org'`. Default is closed. |
| **Custom Apps** | `visibility` defaults to `private`; org-visible **and** live apps are open to every app viewer | `114_custom_apps.sql:30`; `routes/apps/_common.py:290-315` (`can_view`) | Their own + apps explicitly granted to their email + **every org-visible published app**. Note `guest` is in that last set too. |
| **Artifacts** | Partitioned by the agent's own `sharing.instancing`, resolved per viewer | `routes/workspace.py:230-260` (`_agent_instance_for`) → `acb_skills/manifest.py:235-246` (`instance_key`) | **Depends on the agent, and for most agents it is shared.** Four of the six first-party agents declare `instancing: "shared"` (`agent-orchestrator`, `agent-task-manager`, `agent-app-builder`, `agent-apis-config`), which yields `''` — **one workspace for everybody**. Only `agent-email-assistant` and `agent-whatsapp-assistant` declare `personal` (→ `u:<email>`). So a colleague with `feature:artifacts` sees the owner's orchestrator outputs. |
| **Dashboard** | none — there is nothing to scope | `dashboard/page.tsx:1-14` | A "coming soon" card. Granting or denying `feature:dashboard` changes nothing but the sidebar. |
| **Observability** | none — any authenticated caller | `routes/observability.py:46-51` | Run metadata for the whole deployment: agent, model, tokens, cost, status, duration. **Not** message content (the comment at `:46-50` states the split deliberately). The `feature:observability` grant is nav-only. |
| **Workflows** | **org-wide by design** | `routes/workflows/crud.py:1-5` verbatim: "any member holding the `workflows` feature sees and edits every workflow … `owner_email` is attribution, not access" | Nothing — `member` does not hold the feature. See §3.4 before granting it. |
| **Admin** | `admin:members:read` floor for the whole package | `routes/admin/_common.py:77-91` | Nothing. A **manager** sees the entire member directory, the role catalogue and the group list; writes stay behind the `*:manage` permissions (`members.py:203, 264, 371`; `roles.py:147, 226, 293`; `groups.py:199, 244, 281, 337, 419`). |

### 3.4 Granting `feature:workflows` hands over a permanent unauthenticated trigger

Recorded as a **labelled consequence, not a defect** — the org-wide read is a
recorded v1 decision (`workflows_app.md` Q3), and the hook design is deliberate.

The chain, verified:

1. `feature:workflows` ⇒ see and edit **every** workflow, whoever made it —
   `routes/workflows/crud.py:1-5`.
2. The detail response returns the workflow's `hook_token` in the body —
   `crud.py:230`.
3. `POST /workflows/hooks/{hook_token}` is **unauthenticated by design**: it is
   in the router's exempt set (`routes/workflows/core.py:29`) and in
   `main.PUBLIC_ROUTES`. `routes/workflows/hooks.py:3` states the model: *"the
   token IS the credential"*.

So granting `feature:workflows` to a colleague gives them a **permanent,
copyable, unauthenticated trigger credential for every workflow in the
deployment**, which survives suspending or removing them, because revoking a
member does not rotate a token they already read. Rotating hook tokens is
therefore part of off-boarding anyone who ever held this feature — and there is
no rotate endpoint today.

**Before granting it:** decide whether that is acceptable, or mint a ticket for
per-workflow ACLs + hook-token rotation first. This spec does not decide it.

### 3.5 UNVERIFIED cells — admitted gaps

| Cell | Why it is not settled |
|---|---|
| Whether `feature:models`, `feature:agents`, `feature:build.agents` are enforced server-side | `routes/settings.py:23` and `routes/agent.py:49` carry no router-level feature dependency, and I did not enumerate every route under them. The **role** answer is certain (only `feature:*` covers them, so only owner/admin hold them); the **enforcement** answer is not. Treat these as nav-only until someone measures them. |
| Whether the BFF blocks a member from calling an un-gated gateway route directly | The BFF forwards without re-checking features (`lib/gateway.ts:217-240`, `proxyToGateway`), and `lib/access.ts:126-129` says route-level `require_permission` is the boundary. I did not test the full `/api/[...path]` set for a route that gates in the UI and not at the gateway. Assume nav-only gating is not a boundary. |
| Per-Center data scoping | `140_center_features.sql:9-12` is explicit that Center features gate **navigation and the landing pages**, not data. There is no per-Center data predicate anywhere yet (that is WS-14 / WS-15). A `center.finance` grant does not hide anything from anyone. |
| `/debug` routes | Described as EXECUTIVE-only in `observability.py:46-50`'s comment; not measured here. |
| What a **suspended** member can still reach within the 60s access cache | `access.py:209-215` describes the TTL bound; not exercised. |

---

## 4. The open owner-scoping holes — blocking items

Four route families reached somebody else's rows with no owner predicate. Three
(N1–N3) are the ones PR #346 (`d2ef7fa0`) **explicitly named** rather than
fixed, in its own commit body and in `apps/services/gateway/AGENTS.md:30`; they
require `feature:notes`. The fourth (**N4**, found 2026-08-04 while building
this matrix) is in Tasks, requires `feature:tasks`, and was the sharpest of the
four because it is HR data. `member` holds **both** features by default
(`130:235`).

**All four are CLOSED (N4 and N1–N3, both 2026-08-04.)** They were latent with
one user and live the moment a colleague signed in.

**These are the gate's G4. They were sized here and built elsewhere — this
document does not fix them.** All four were 🟢 **AGENT-SAFE**.

> ⚠️ **G4 closing does not make WS-24 green.** G1 and G2 are untouched and
> OWNER-GATE *(G3 closed 2026-08-07 — noted 2026-08-09)*. **It is still not safe
> to invite anybody.**
> Specifically: without G1 the reverse proxy does not strip inbound
> `X-User-Email` / `X-User-Role`, and without G2 the service identity may still
> be the LLM key every agent holds — an identity forgery reaches *any* member's
> data, and an owner predicate applied to a forged identity is not a control.
> The four tickets below close the holes that survive a correct identity. They
> do not close the ones that begin with the wrong one.
>
> A second qualifier, recorded in **N5** below: N1's table is a list of six
> files, not a proof of exhaustiveness over `routes/notes`, and it was not
> exhaustive.

### N1 — Notes read paths outside the owner predicate · size: M (one PR, ~6 files) · ✅ **CLOSED 2026-08-04**

> **CLOSED** on `ws-24-n1n3-notes-scoping`. Fifteen of the sixteen routes in
> the six named files now load their meeting through `core.load_owned_meeting`
> or bind `core.OWNED_MEETING_PREDICATE`; all answer **404, never 403**. The
> sixteenth (`live.py:256`) is deliberately left machine-authed — see the row
> below.
>
> | File | What shipped |
> |---|---|
> | `recordings.py` | `upload_recording` and `start_recording` call `load_owned_meeting` (and unlink the file they had already written on refusal, so no bytes land in a colleague's media dir); `_recording_path` — the loader `/chunk` and `/complete` share — joins `meeting` and binds the predicate, so neither route can acquire the hole separately and the per-chunk path pays no extra round trip; `get_audio` calls `load_owned_meeting` first |
> | `qa.py` | `ask_meeting` loads the meeting **before** the transcript, so a colleague's meeting cannot be told apart by the 409 "no transcript yet" answer |
> | `share.py` | `draft_followup_email` loads through `load_owned_meeting`. Checked first, as the brief required: there is no sharing mechanism in the module to preserve — no grant, no token, no redemption route; the send is a separate `/email/send` call under the caller's own account, so the whole route is a *read* |
> | `copilot.py` | both routes scoped; the stream checks **before** the `StreamingResponse` starts, since a 404 raised inside a started stream arrives as a broken connection rather than a refusal. The bus replays its ring to late subscribers and `refs.window` carries up to 400 chars of speech, so the stream was a live transcript feed |
> | `live.py` | `POST /stt/live-token` is owner-scoped **when it names a meeting** (its 409 reason distinguished "the copilot is off for this meeting" from "live transcription is off", a presence oracle over a colleague's calendar); `GET /meetings/{id}/live/wanted` and `POST /stt/bot-live-token` stay machine-authed — see below |
> | `actions.py` | see N2 |
>
> **`live.py:256` — the decision, recorded.** It stays bot-token authed. Its
> caller is the meeting-bot worker in its own container: it holds
> `MEETING_BOT_TOKEN` and no member identity, so an owner predicate has no
> owner to use, and both ways to invent one (trust a client-supplied email, or
> fall back to the meeting's own `owner_email`) turn the bot token into a way
> to *assert* an identity. The token is the authority, exactly as it is for
> `/live/segment`, which posts the transcript this route only decides whether
> to keep paying for. What it discloses without a member check is one boolean
> and a settings-derived sentence — no content, no title, and the same answer
> for an id that does not exist (`live_wanted` never raises and defaults to
> `True`). Pinned by
> `test_notes_owner_scoping.py::test_the_two_bot_token_routes_stay_machine_authed`.
>
> ⚠️ **Found while doing this, NOT fixed here** (it would *open* a route, the
> opposite of this change's direction, and no done-when asks for it):
> `/notes/meetings/{meeting_id}/live/wanted` is absent from **both**
> `gateway/main.py`'s `PUBLIC_ROUTES` **and** `routes/notes/core.py`'s
> `require_feature_router(exempt=…)` list, while its two siblings are in both.
> So the app-wide `require_authenticated` and then the feature gate 401 the
> worker before `_check_bot_auth` ever runs, and the poll that decides whether
> to keep streaming ASR is dead. `tests/unit/test_org_access_enforcement.py`
> lists the path in its own `GATED_ROUTERS` registry, which is what let the
> drift pass unnoticed: that registry is the test's opinion, not the router's.
>
> Tests: `tests/unit/test_notes_owner_scoping.py` grew 36 cases (21 → 57).
> Every non-owner case was verified **red** against the pre-fix behaviour with
> the parameter renames already applied, so each red is the security claim and
> not a `TypeError`.

The six route families as measured on 2026-08-04, before the fix:

| File | Route(s) | The unguarded read |
|---|---|---|
| `recordings.py` | `POST /meetings/{id}/upload` `:64`, `POST /meetings/{id}/recordings/start` `:159`, `POST …/chunk` `:228`, `POST …/complete` `:267`, `GET /meetings/{id}/audio` `:377` | `SELECT id FROM meeting WHERE id = :id` (`:101`, `:183`) and `SELECT * FROM meeting_recording WHERE meeting_id = :id` (`:386`) — existence only. `retranscribe` `:321` is the one that *is* guarded (`load_owned_meeting` at `:337`), which is the shape the rest should copy. |
| `qa.py` | `POST /meetings/{id}/ask` `:82` | Reads the **whole transcript** — `SELECT … FROM transcript_segment WHERE meeting_id=:id` `:95-99` — and answers questions about it. |
| `share.py` | `POST /meetings/{id}/share/email/draft` `:25` | `SELECT title, summary_md, attendees FROM meeting WHERE id=:id` `:33-36` — drafts a recap of a colleague's meeting. |
| `copilot.py` | `GET /meetings/{id}/copilot/stream` `:484`, `GET …/copilot/events` `:501` | Live copilot stream for any meeting id. |
| `live.py` | `GET /meetings/{id}/live/wanted` `:256` | Bot-token authed (`_check_bot_auth`), not member-authed — a different shape from the rest; confirm before "fixing" it into member auth. |
| `actions.py` | see N2 | |

**Done when:** every route above loads its meeting through
`core.load_owned_meeting` (or binds `OWNED_MEETING_PREDICATE`) and returns
**404, never 403**, for a meeting the caller does not own; `live.py:256` is
either left machine-authed with a comment saying why or moved to the same rule;
and `tests/unit/test_notes_owner_scoping.py` gains one red-first case per route
family asserting 404 for a non-owner. — **all met.**

### N2 — `actions.py` single-item approve / reject · size: S (one file) · ✅ **CLOSED 2026-08-04**

> **CLOSED** on `ws-24-n1n3-notes-scoping`. `_load_action(db, action_id,
> owner_email)` now joins `meeting m ON m.id = action_item.meeting_id` and
> binds `OWNED_MEETING_PREDICATE`, raising 404 with the same "action item not
> found" detail either way — the convention `dispatch.dispatch_action`
> (`dispatch.py:614-622`) already used. Both routes inherit it from the shared
> loader, so neither can acquire the hole separately. An `action_item` has no
> owner column of its own; it inherits the one on its meeting, so the scope is
> a join, not a predicate on the row.
>
> Both harms are pinned separately, because a 404 alone would not have proved
> the second: `test_approving_a_colleagues_action_creates_no_task_in_your_list`
> asserts that **no** `INSERT INTO gtd_items` and **no** `UPDATE action_item`
> are issued, and that the colleague's description text never reaches a bound
> parameter.
>
> **`approve-all` was aligned, not left alone.** It was already safe at the
> seam — every item goes through `_dispatch`, which refuses a cross-owner actor
> — but it answered **200 with an empty list**, which says "your meeting,
> nothing qualified" where the truth is "not your meeting", and it read the
> colleague's draft rows to get there. It now loads through
> `load_owned_meeting` first, so the answer matches the single-item routes and
> the item rows are never read. The `_dispatch` refusal is untouched: it is the
> seam, and this is a second lock on one of its doors.

`_load_action` (`actions.py:62-75`) selects `FROM action_item WHERE id = :id`
with no join to `meeting` and no owner predicate. Both callers act on it:

* `POST /actions/{id}/approve` `:78-111` — creates a `gtd_items` row with
  `user_id` set to **the caller's** email (`_create_task_from_action` `:46-58`,
  bound at `:90`) and copies `action.description` into its title (`:54`). So
  any member can lift a colleague's action item into their own GTD list, and
  the colleague's item flips to `status='created'` (`:91-97`).
* `POST /actions/{id}/reject` `:114-130` — flips a colleague's item to
  `rejected` (`:125-128`).

`approve-all` (`:141`) was *not* in scope: it goes through
`dispatch.cross_owner_refusal`, which is the seam PR #346 hardened. (It was
aligned anyway — see the CLOSED block above for why a 200-with-empty-list was
still an answer worth removing.)

**Done when:** `_load_action` joins `meeting m ON m.id = action_item.meeting_id`
and binds `OWNED_MEETING_PREDICATE`, raising 404 for a non-owner; both routes
inherit it; and two tests (one approve, one reject) fail red before the change.
— **all met.**

### N3 — `meeting_bot.bot_join` with a `meeting_id` · size: S (one route) · ✅ **CLOSED 2026-08-04**

> **CLOSED** on `ws-24-n1n3-notes-scoping`, with one deliberate departure from
> the done-when below, stated here because it is a design choice and not an
> oversight: **the scope is bound INTO the `UPDATE`** rather than checked by a
> preceding `load_owned_meeting`. A load-then-write leaves a window in which
> the row can change between the two statements, and this statement *is* the
> mutation — one statement, one decision:
>
> ```sql
> UPDATE meeting AS m SET status='recording', platform=:p, start_at=now(),
>        title=COALESCE(:t, title)
>  WHERE m.id = CAST(:id AS UUID)
>    AND (lower(m.owner_email) = lower(:owner) OR m.owner_email IS NULL)
> RETURNING m.id
> ```
>
> No row back ⇒ the existing 404 "unknown meeting", which is now the answer for
> both "no such meeting" and "not yours".
>
> **The acting principal is the CALLER (`user.email`), and that is the whole
> ticket.** It has to be: the caller is the only identity the request carries,
> and resolving the check against the row's own `owner_email` would compare the
> meeting to itself and pass every time. This is the same rule as PR #346's
> `requested_by` choice, applied at the other end — authority follows the
> person who asked and is never laundered through the row being acted on. After
> the check the two coincide for an attach, which is the point; the ingest side
> still reads `meeting_bot.requested_by` and
> `test_the_requester_not_the_owner_is_what_the_ingest_carries` pins that it is
> not "simplified" to the owner later.
>
> The create branch is **unchanged** — it stamps `owner_email = user.email`, so
> there is no one else's row to reach — and a test asserts that.

### N3 — the ticket as written

`POST /meetings/bot-join` (`meeting_bot.py:691`) accepts an optional
`body.meeting_id` and, when present, runs
`UPDATE meeting SET status='recording', platform=…, start_at=now(), title=… WHERE id = CAST(:id AS UUID)`
(`:728-737`) with **no owner predicate**. Any member holding `feature:notes`
who knows a colleague's prepared meeting id can flip it into `recording`, mutate
its title and start time, attach a bot to it, and register live presence under
their own identity (`live_session.begin(meeting_id, "bot", user.email)` `:817`).

Note the deliberate asymmetry that must be preserved: the bot pipeline carries
`meeting_bot.requested_by` — the member who sent the notetaker — as
`triggered_by`, **not** the meeting's owner, precisely so the requester's
authority is not laundered into the owner's (PR #346's commit body;
`gateway/AGENTS.md:30`).

**Done when:** the attach branch loads the meeting through
`load_owned_meeting` first and 404s for a non-owner; the create branch
(`:741-752`, which already stamps `owner_email = user.email` at `:749`) is
unchanged; and a test asserts 404 when a non-owner supplies another member's
`meeting_id`. — **met, with the loader replaced by the predicate bound into
the `UPDATE` itself** (same predicate, same 404, no TOCTOU window; see the
CLOSED block).

### N5 — the rest of `routes/notes` · size: M · 🔴 **OPEN** · ⚠️ **NOT part of G4**

Found while closing N1, and recorded rather than absorbed: **N1's table was a
list of six files, not a proof of exhaustiveness.** `routes/notes` has 24
modules. After N1–N3, `load_owned_meeting` / `OWNED_MEETING_PREDICATE` appear
in `meetings.py`, `recordings.py`, `summaries.py`, `actions.py`, `qa.py`,
`share.py`, `copilot.py`, `live.py` and `meeting_bot.py` — and in **nine**
other modules the count is still zero:

| File | Routes still reaching a meeting by caller-supplied id |
|---|---|
| `summaries.py` | `GET`/`PUT /meetings/{id}/note`, `GET /meetings/{id}/actions` (`summarize` *is* scoped — PR #346) |
| `copilot_context.py` | `PUT /meetings/{id}/brief`, `GET /meetings/{id}/context`, `POST /meetings/{id}/context/deep` |
| `copilot_agenda.py` | `GET`/`PUT /meetings/{id}/agenda`, `POST /meetings/{id}/agenda/chat` |
| `meeting_bot.py` | `GET /meetings/{id}/bot`, `/bot/diagnostics`, `/bot/screenshot`, `POST /meetings/{id}/bot/stop` (`bot-join` *is* scoped — N3) |
| `live_transcript.py` | `/live/browser-segment`, `/live/roster`, `GET /meetings/{id}/live` (SSE), `POST /meetings/{id}/say` |
| `live_session.py` | `GET /live/sessions`, `GET /meetings/{id}/live/session`, `POST /meetings/{id}/live/copilot` |
| `speaker_id.py` | `POST /meetings/{id}/identify-speakers` |
| `agenda_progress.py` | `GET /meetings/{id}/agenda/progress` |
| `events.py` | `GET /meetings/{id}/events` (SSE progress) |

Two of these are as sharp as anything in N1 — `GET /meetings/{id}/note` serves
the generated notes, and `GET /meetings/{id}/live` is the live caption stream —
and `POST /meetings/{id}/say` makes the notetaker *speak a line into somebody
else's call*.

**This is deliberately NOT added to G4.** G4's done-when is "each of §4's four
tickets meets its own done-when", and all four do; re-scoping an owner-facing
gate is the owner's call, not an implementer's. **Owner decision needed:** does
N5 block colleague #1, or does it ship after? Note that G4 going green is
already qualified — G1/G2/G3 keep WS-24 red regardless, so nothing turns on
this today.

**Done when:** every route in the table loads through `core.load_owned_meeting`
or binds `OWNED_MEETING_PREDICATE`, 404 never 403; the two machine entrypoints
(`/live/segment`, and `/live/browser-segment`'s user-authed twin) keep their
existing trust models; and `tests/unit/test_notes_owner_scoping.py` gains a
red-first case per route family. 🟢 **AGENT-SAFE.**

### N4 — the Tasks people directory is org-wide read **and write** · size: M (one file + a decision) · ✅ **CLOSED 2026-08-04**

> **CLOSED** on `ws-24-n4-people-scoping`. What shipped, and where:
>
> | Rule | Where | Anchor |
> |---|---|---|
> | Write gate — all **four** routes | `require_people_write()` as a route `dependencies=[…]` entry | `routes/tasks/core.py` (`PEOPLE_WRITE_PERMISSION = "admin:members:manage"`, `require_people_write`); bound at `people.py` `POST /people`, `PATCH /people/{person_id}`, `POST /people/{person_id}/resume` and `capability.py` `POST /people/embed` |
> | Read projection | `_row_to_person(row, *, include_hr)` — keyword-only, **no default**, so a route added later cannot inherit the permissive answer by omission | `people.py` (`HR_FIELDS`, `_blank_hr`, `_row_to_person`); the predicate is `core.can_read_hr_fields` on `PEOPLE_HR_READ_PERMISSION = "admin:members:read"` |
> | Search may not become an oracle | `GET /people?q=` drops the `unnest(skills)` clause for a caller who cannot see skills — matching on a column that is then stripped would leak it back | `people.py::list_people` |
> | Delegation untouched | `fetch_people_for_clarify` still takes `db` only and returns full rows; the projection is at the **serialization** layer, never in the SQL | `people.py::fetch_people_for_clarify` |
>
> Tests: `tests/unit/test_tasks_people_scoping.py` (35 cases). Three mutants
> verified red first — always-include-HR, a removed write dependency, and an
> unconditional skill-search clause.
>
> **Superseded 2026-08-04:** this block used to end "Still open on this gate:
> N1–N3 (Notes), G1, G2 and G3." N1–N3 closed the same day, so G4 **is** green;
> G1, G2 and G3 are not, and the readiness verdict is unchanged.

**This was the sharpest item on the gate.** It is HR data, it was readable and
writable by the default role, and it is the exact surface D12's department
privacy exists to protect.

`routes/tasks/people.py` mounts on the shared `/tasks` router
(`people.py:22` ← `routes/tasks/core.py:27-30`). That router's only dependency
is `require_feature_router("tasks")` (`core.py:29`). The people routes add
**no** owner predicate, no admin permission and no group scope on top of it:

| Route | Anchor | What it does with no scope |
|---|---|---|
| `GET /tasks/people` | `people.py:80-84` | Takes `_user` and never reads it; runs `SELECT * FROM gtd_people WHERE status='active'` at `:98` and returns every column through `_row_to_person` (`:56-77`) — name, email, role, title, department, team, `reports_to`, `manager_id`, skills **and `skills_source`**, `resume_summary`, `years_experience`, capacity / current load / available hours, and `clickup_user_id`. |
| `POST /tasks/people` | `:190` | Creates a person. `user` is used only for `_uid(user)` → `updated_by` (`:233`). |
| `PATCH /tasks/people/{person_id}` | `:241` | Edits **any** person: name, email, manager, skills, capacity, ClickUp link. Loads the row via `_get_person_row` (`:181-187`), which is `SELECT * FROM gtd_people WHERE id = :id` — id only. |
| `POST /tasks/people/{person_id}/resume` | `:303` | Uploads a PDF/DOCX/TXT onto **any** person, parses it, writes `gtd_person_resumes` with up to 200 kB of parsed text (`:351-360`) and merges the extracted skills, summary, years and domain into the record (`:363-375`). |
| `POST /tasks/people/embed` | `capability.py:225-228` | Backfills the roster's capability embeddings; `_user` is unused. |

**The contrast that sizes it.** This is *not* a blanket gap in the Tasks
package. Task accounts are scoped — `_assert_account_owner`
(`routes/tasks/core.py:189-197`) is bound at `accounts.py:190, 259, 285, 325,
406` — and items carry 27 `user_id = :uid` predicates. The people layer is the
one that never got a rule, so the fix is local to it rather than a rewrite.

#### DECISION — directory open, HR fields restricted `owner-answered 2026-08-04`

`gtd_people` is an *org* roster imported from `agent-project-manager` HR data
(`people.py:1-10`); it is not per-user rows, so "owner-scope it" was the wrong
shape and the ticket could not be built until the owner said which shape it is.
The answer:

* **Read** stays available to anyone holding `feature:tasks` — an org chart a
  colleague cannot open is not an org chart — **but the HR-sensitive fields are
  stripped for non-admins**: `resume_summary`, `skills`, `skills_source`,
  `years_experience`, and capacity / current-load / available hours. The basic
  directory stays visible to all: name, email, role, title, department, team,
  `reports_to` / `manager_id`, `clickup_user_id`.
* **Writes are admin-only** — `POST /people`, `PATCH /people/{person_id}`,
  `POST /people/{person_id}/resume` and `POST /people/embed`.

**Rejected alternatives, recorded so they are not re-proposed:**

1. **Fully org-wide read** (status quo, made explicit). Rejected: it is the
   exposure this ticket exists to close. A résumé summary and a per-person load
   figure are HR records, not directory entries, and D12's department privacy
   is written against exactly this surface.
2. **Center-scoped read** (a colleague sees their own Center's people). Rejected
   for two reasons: it needs **D13's grant shape**, which is unbuilt, so the
   ticket would block on another workstream; and it **breaks cross-team
   delegation**, which is the roster's whole purpose — you delegate *out* of
   your Center more often than within it.

**Implementation notes that are part of the decision, not incidental:**

* The two permissions are the **existing** admin vocabulary, not new slugs.
  `admin:members:read` is the same floor the whole `/admin` package uses
  (`admin/_common.py:77-91`) and the same predicate `/auth/me` reports as
  `is_admin` (`admin/me.py:96`), so "may read the member directory" and "may
  read the HR half of the people directory" are one answer that cannot drift.
  `admin:members:manage` already governs member records. A **new** slug would
  be nobody's grant until an admin created it — which would switch HR features
  off for the owner too, and is the failure mode this decision avoids.
  Consequence, stated so it is not a surprise: a `manager` (who holds
  `admin:members:read` but not `:manage`) **can** see the HR fields and
  **cannot** write them. That is consistent with §3.2 — a manager already reads
  the whole member directory.
* The projection sets fields to `null` / `[]` / `{}`; it never removes keys.
  The response shape, the TS `OrgPerson` type and `mapOrgPerson`
  (`workbench/control_plane/src/app/tasks/lib/api.ts:301-327`) are unchanged.
* **`domain` and `status` are deliberately NOT restricted.** They are outside
  both lists above. `status` drives the directory's inactive badge; `domain` is
  a coarse field of the same kind as `department` (`robotics`, `firmware`).
  `domain` is résumé-fillable, so if the owner wants it restricted it is a
  one-line addition to `people.HR_FIELDS` + `_blank_hr()`.
* The search box is part of the boundary: `GET /people?q=` matches skills only
  for a caller who may see skills, or the strip is cosmetic.
* ⚠️ The delegation path must keep working, and does: `fetch_people_for_clarify`
  is an in-process helper called server-side from `ai.py`, `capture_email.py`
  and `planning.py` — never through the router — and the projection is applied
  at the **serialization** layer, not in the SQL, precisely so it is untouched.
  Do not "fix" it too; that is the capability-aware delegation the roster
  exists for.
* Consequence on the agent path, and it is the right one: the `gtd_people`
  skill tool (`apps/skills/skill-task-gtd/skill_task_gtd/core.py`) calls
  `GET /tasks/people` **as the acting member**, so an agent run for a non-admin
  now sees the same restricted directory that member sees. An agent must never
  exceed the person it acts for; the in-process clarify path is what keeps
  delegation capability-aware.

**Done when** *(all met — see the CLOSED banner at the head of this ticket)*:
the read decision is recorded (above); the four write routes require a
permission strictly above `feature:tasks` and 403 for a plain `member`;
`GET /people` enforces the read rule; `tests/unit/test_tasks_people_scoping.py`
carries a case per route; and `fetch_people_for_clarify` is unchanged.

---

## 5. Verification

    # The gate's machine-checkable half. On the box:
    cd /opt/acb/app && uv run python scripts/onboarding_preflight.py
    # Anywhere else (box-only checks report SKIP, never PASS):
    uv run python scripts/onboarding_preflight.py --mode local
    # Expected on this branch: 1 pass, 2 FAIL, 4 skip, exit 1. The two FAILs
    # are repository defects, not environment ones -- the api vhost in
    # deploy/hostinger/caddy/Caddyfile:15-17 has no `header_up -` deletions
    # (G1), and there is no scripts/backup_db.sh (G3, PR #347).

    # The access model this document describes:
    uv run pytest tests/unit/test_org_access_control.py \
                  tests/unit/test_admin_groups.py \
                  tests/unit/test_default_deny_auth.py -q
    # Baseline on ws-24-onboarding-readiness: 85 passed.

    # §4's holes. All four tickets are closed and green:
    uv run pytest tests/unit/test_tasks_people_scoping.py -q
    # 35 passed (N4).
    uv run pytest tests/unit/test_notes_owner_scoping.py -q
    # 57 passed (N1-N3; 21 of them are PR #346's).

    # Route wiring, because N1-N3 changed route dependencies and signatures:
    uv run pytest tests/unit/test_org_access_enforcement.py -q
    # 31 passed.
    # The whole Notes suite, by file (never the directory):
    uv run pytest tests/unit/test_notes_agenda_progress.py \
      tests/unit/test_notes_bot_identity.py tests/unit/test_notes_copilot.py \
      tests/unit/test_notes_copilot_agenda.py \
      tests/unit/test_notes_copilot_context.py \
      tests/unit/test_notes_copilot_policy.py tests/unit/test_notes_dispatch.py \
      tests/unit/test_notes_glossary.py tests/unit/test_notes_live.py \
      tests/unit/test_notes_live_http.py tests/unit/test_notes_live_session.py \
      tests/unit/test_notes_live_speakers.py \
      tests/unit/test_notes_live_transcript.py \
      tests/unit/test_notes_meeting_bot.py tests/unit/test_notes_meeting_prep.py \
      tests/unit/test_notes_owner_scoping.py tests/unit/test_notes_qa.py \
      tests/unit/test_notes_settings.py tests/unit/test_notes_speaker_id.py \
      tests/unit/test_notes_summaries.py -q
    # 280 passed on ws-24-n1n3-notes-scoping (36 of them new here). The "242
    # at #346" figure is not reproduced by this file list -- treat 280 as the
    # baseline and this list as the definition of "the Notes suite".

    # ⚠️ NEVER `uv run pytest tests/unit/` as a directory on this box -- it
    # hangs against the live DB. Name the files.

    uv run ruff check . --select F821,F601,F602,F502,F7,B006
    python -m py_compile scripts/onboarding_preflight.py

**Never** run the preflight against production from an agent session. The DB
checks read the live database; `--mode local` is the agent's only mode.

---

## 6. The provisioning gap — nobody can see who is knocking

§2's runbook is **push-only**: the only way an `app_user` row is ever created is
an admin typing an address into Invite (`routes/admin/members.py:168-183`).
Somebody arriving at the front door creates nothing an admin can see. The
refusal *is* logged — `access.py:264-272` emits `access_unprovisioned_signin`
with the email — but it goes to journald, nothing reads it back, and it is gone
at the next rotation. So the owner's only way to learn that a colleague is
locked out is for that colleague to tell them out of band.

**Measured on the box 2026-08-04, and this is the ticket's motivation rather
than a hypothetical:** `journalctl -u acb-gateway` (retained back to
2026-07-28) carries **53** `access_unprovisioned_signin` events for exactly one
address, `ishaanpilar@fracktal.in`, first at `2026-08-03T16:21:15Z` and still
recurring at `2026-08-04T10:46:56Z`. One colleague spent eighteen hours telling
the system he wanted in, fifty-three times, and the system told nobody.

### N6 — capture the knock, and let the owner answer it · size: M (migration + one route file + one page) · 🟢 **AGENT-SAFE**

**Status: N6a ✅ BUILT 2026-08-04, + TWO repair rounds the same day**
(`ws-24-n6-signin-requests`) — all eleven done-whens met, fenced by
`tests/unit/test_signin_requests.py` (50 cases). Round 1 closed a **P1
cross-gate escalation** (approve reinstated an off-boarded member) and replaced
the fence that had missed it. Round 2 closed the half it left: the refusal was
**silent**, so approve still answered **200**, still marked the request
`approved`, and still re-granted roles — taking the locked-out person out of a
queue that renders only `pending`. **Read *Repair round 2* before reading dw7:
it carries the approve matrix, which is the binding contract for what approve
does about an address that already has an `app_user` row, and dw7's
"idempotent: approving twice is not an error" is superseded.** Round 3 closed
the concurrent instance of the same shape and named its structural cause —
approve predicted the write instead of verifying it. **52 cases.**
**N6b: nothing to build**; one owner question remains open below. **Merging is
OWNER-GATE** — see the note at the end of this section.

Two defects, deliberately in one ticket because shipping either alone leaves a
half-working door.

**N6a — the request queue.** Persist the unprovisioned sign-in instead of
discarding it, and give `/settings/members` a **Requests** tab where the owner
approves with roles in one action.

**N6b — invite does not admit anybody, and the runbook never said so.**
🔴 **NOT DISPATCHABLE — held for an owner re-read.** `POST /admin/members`
inserts `status = 'invited'` (`members.py:172`) and `is_active` is
`status == "active"` exactly (`access.py:288`), so Step 1 on its own leaves the
colleague at the same dead-end screen as a stranger. That much is real, and it
is what the owner hit on 2026-08-04.

> ### ⚠️ RETRACTED — this ticket was first written on a false premise
>
> The first draft of N6b claimed *"there is no `invited → active` transition
> anywhere in the codebase."* **That is wrong, and the spec-auditor caught it.**
> `PATCH /admin/members/{email}` accepts `status: "active"` from
> `VALID_STATUSES` (`members.py:50`) and stamps `joined_at` with
> `COALESCE(joined_at, now())` (`members.py:224-233`) — the exact behaviour the
> retracted draft proposed to build. It is already surfaced as an **Activate**
> button on every `invited` row (`settings/members/page.tsx:252-258`).
>
> The real defect is **documentary**: §2's runbook went Invite → Roles → Group
> → Verify and never once said "activate". Step 4 then verifies with
> `GET /admin/members/{email}/access`, which faithfully reports `is_active=false`
> while the row is still `invited` — so the runbook could be followed exactly,
> the verification step could be performed exactly, and the colleague would
> still be locked out with nothing appearing to be wrong. **That omission is
> now fixed: §2 Step 1b.** No code was needed for it.

What remains genuinely open is narrower, and is a **question, not a ticket**:

**Should activation be automatic on first sign-in?** An invited member could be
promoted on their first IdP-verified resolve — a guarded
`UPDATE … WHERE status = 'invited'` in `access.py` — which would make Invite
mean "let them in" and reduce the two clicks to one. Against it: it puts a
second write on the auth path for a problem a documentation fix has already
solved, and `invited` currently carries real information ("provisioned, never
signed in") that the Members UI could surface instead of discarding.

🔴 **OWNER DECISION:** (a) leave it at two clicks, now that the runbook says so;
(b) auto-promote on first sign-in; or (c) make Invite insert `active` directly
(a one-line change at `members.py:172`). **This spec recommends (a)** — the
measured failure was a missing sentence, and (b) and (c) both spend an auth-path
change on it. Nothing downstream is blocked either way; **N6a does not depend on
this.**

If (b) is ever chosen, one non-obvious guard must come with it: `suspended` and
`removed` must **not** be promoted. The natural implementation
(`status != 'active'` → activate) silently un-suspends people.

> ### ✅ ANSWERED 2026-08-24 — **(b), on the REGISTRY plane only** *(decision D50.3, owner-ratified; built on `ws-30-invites`)*
>
> The owner chose **(b) auto-promote on first sign-in**, and the guard this
> section warned about is the one that was built: the promotion is
> `UPDATE org_membership SET status='active', joined_at = COALESCE(joined_at,
> now()) WHERE … AND status = 'invited'` — **the guard is in the statement's own
> `WHERE`**, not an `if` beside it, so `suspended` / `removed` / `active` are
> untouched by construction rather than by a comparison a later edit can widen.
> The exact failure named above ("`status != 'active'` → activate silently
> un-suspends people") is the mutation its fence is shown red with.
>
> ⚠️ **Scope — read this before citing N6b as closed.** The promotion landed on
> the **Customer Console's** `org_membership`, inside the deployment arm of
> `POST /registry/resolve` (`customer_console.md` **CP-2f**). It did **not** land
> on `access.py` and it does **not** touch the tenant plane's `app_user.status`,
> so the objection this section raised against (b) — *"it puts a second write on
> the auth path"* — was **avoided rather than overruled**: the write is on the
> registry service, which the sign-in path was already calling and already
> writing to (the Core-seat allocation). What that closes is the far worse
> failure the invite audit found: with resolve armed, an invited colleague with
> no Console membership resolved `console-empty` and was funnelled into creating
> **their own organization**. What it does **not** close is this section's
> original complaint — **Step 1b is still required**, because `is_active` is
> still `app_user.status == "active"` exactly.
>
> **N6b's remaining half — BUILT the same day (review-round-1 repair).**
> `acb_auth.access.promote_invited_member` promotes the tenant `app_user` row
> and the identity-shadow membership `invited → active`, with the same
> `WHERE status = 'invited'` guard in each UPDATE, from exactly ONE call site
> (the gateway's `POST /signin/resolve`, after `decision.admit` — never the
> per-request path, the same farmable-surface rule the resolve itself follows).
> Best-effort: a failed promotion never changes the resolve answer and fails
> CLOSED (the person stays `invited`). The app_user write runs inside
> `tenant_session` (RLS-forced in production). Fences:
> `tests/unit/test_invited_member_promotion.py` (R8 — the guard shown red by
> parametrised suspended/removed rows, idempotence, both-tables promotion) plus
> the structural pins (the write inside the GUC seam; the call under
> `decision.admit`). So this section now closes: (b), on both planes.

#### DECISION — a separate table `agent-proposed, owner may overrule`

**Chosen: a new `access_request` table, not a fifth `app_user.status`.** The
one-table version is tempting — the Members page already lists `app_user` — but
an `app_user` row is *the org's member record*: it carries `org_id`, it is what
`user_role` and the member/people listings join against, and `is_active=False`
protects the **auth** path only, not every query that reads the roster. A
stranger who merely knocked must not acquire a row that a future join can
surface. Approval creates the real `app_user` through the **same helper**
`POST /admin/members` uses, so there is one provisioning path, not two.

**Precondition that makes auto-capture safe, recorded so it is re-checked:**
`AUTH_MICROSOFT_ENTRA_ID_TENANT` is set to the Fracktal directory GUID on the
box (verified 2026-08-04 in both `/opt/acb/app/.env` and
`workbench/control_plane/.env.local`), so `auth.ts`'s issuer is tenant-pinned
and only **directory members** can reach the branch that writes. **If that
variable is ever unset the issuer falls back to `organizations`**
(`src/auth.ts:22`) and any Microsoft work account on earth can append a row.

> ### ⚠️ CORRECTED (repair round, 2026-08-04) — "directory member" ≠ "colleague"
>
> This block previously implied the tenant pin bounds the table to people who
> work here. **It does not, and two separate mechanisms say so:**
>
> 1. **Entra B2B guests are directory members.** A guest invited into the
>    Fracktal tenant authenticates against the pinned issuer exactly like an
>    employee. The pin bounds who can **authenticate**; it says nothing about
>    who **belongs to the company**.
> 2. **`ALLOWED_EMAIL_DOMAIN` does not refuse them either.** On the branch that
>    matters (`acb_auth/deps.py`, branch 1a — Bearer + identity headers, i.e.
>    every browser call), an off-domain address is **logged**
>    (`auth.identity_domain_mismatch`) and passed through, deliberately: the
>    internal-token holder is trusted to say who it is acting for, and refusing
>    would lock out any member whose sign-in address is off-domain.
>
> So an off-domain identity can and does reach the write, Approve provisions
> `active` immediately, and the row was the only place left where the
> difference could be seen. **Fixed:** the queue entry now carries
> `is_external` — resolved by the gateway through
> `acb_auth.is_company_email()`, because the domain is server policy and the
> browser must not re-derive it — and the Requests tab marks such a row
> "outside the company domain". It is a **label for the admin**, not a
> refusal; refusing here would re-create the lockout branch 1a avoids.

Rows stay bounded by the directory either way, but the table is a list of
everyone who knocked, not a list of colleagues.

**Done when — N6a**

1. A migration at **the next free number at build time** creates
   `access_request`: `email` (unique on `lower(email)`), `display_name`,
   `first_seen_at`, `last_seen_at`, `attempt_count`, `status`
   (`pending` | `approved` | `denied`), `decided_by`, `decided_at`. Idempotent
   (`IF NOT EXISTS`), like every migration in `infra/postgres/`.
2. `resolve_access`'s `row is None` branch (`access.py:257-276`) upserts the
   request — insert on first sight, otherwise bump `attempt_count` and
   `last_seen_at`. **Best-effort:** the upsert is wrapped so that a failing
   write changes neither the returned `EffectiveAccess(is_active=False)` nor
   the log line, and never raises into the request. Pinned by a test that makes
   the write raise and asserts the refusal is still returned unchanged.
3. **🔴 The write fires only on the sign-in path.** `resolve_access` is **not**
   a sign-in-only function: `routes/rooms.py:215` calls it in a fan-out over
   *room participants'* emails, and `access.py:433` folds it over session
   subjects. Neither is a knock, and dw4 does not exclude them — a participant
   with no `app_user` row would silently be filed as a "sign-in request",
   putting people in the queue who never tried to sign in, which is precisely
   the harm the DECISION cites to justify a separate table. **Done when** the
   upsert is reached only from the request path — e.g. a keyword-only
   `record_request: bool = False` passed solely by `acb_auth/deps.py:231` — and
   a test asserts `rooms.py`'s fan-out over three unknown emails writes
   **nothing**.
4. It never records an email that already has an `app_user` row in **any**
   status — the branch only runs when the row is absent, and a test pins that a
   suspended member does not generate a request.
5. Write volume is bounded by the existing 60-second resolution cache
   (`access.py:34`), not per-request. A test asserts a second resolve inside
   the TTL performs no second write.
6. `GET /admin/members/requests` lists pending requests, newest `last_seen_at`
   first. **⚠️ The `/admin` floor is per-route, not a package property** —
   `_common.py:31` creates the router with **no** `dependencies=`, and every
   existing route declares `Depends(require_admin_user)` in its own signature
   (`_common.py:77-91`). A new route that omits it inherits no floor at all.
   Each new route below must declare it explicitly, and a wiring test must pin
   that. **No new permission slug** — a new slug is nobody's grant until an
   admin creates it (the N4 lesson).
7. `POST /admin/members/requests/{email}/approve` with an optional `roles` body
   (defaulting to `["member"]`, as `members.py:165` does) **provisions the
   member and activates them in one action** — `status = 'active'`, not
   `'invited'`, because an approval *is* the admin's decision to let them in
   and the person is already at the door (this is where §2's two-click problem
   must not be re-created). It marks the request `approved` with
   `decided_by`/`decided_at` and calls `invalidate_for(email)` so the cached
   refusal does not outlive the approval. **`invalidate_for` lives at
   `admin/_common.py:218`, not in `acb_auth`** — `members.py:43` already
   imports it from there. Gated on `admin:members:invite`. ~~Idempotent:
   approving twice is not an error and does not create two members.~~
   ⚠️ **SUPERSEDED by the repair rounds — do not quote this sentence in
   isolation.** A second approve is now a **409** naming the decision; the
   invariant it cared about (never two members) is unchanged and stronger.
   ⚠️ **This done-when also says nothing about an address that already has an
   `app_user` row, which is the gap both repair rounds were spent closing.**
   The answer is the approve matrix in *Repair round 2* below; it is the
   binding contract, not this line.
8. The provisioning insert is **extracted from `invite_member`
   (`members.py:168-183`) into a plain `async def _provision_member(db, org_id,
   *, email, display_name, roles, admin, status)` helper that both routes
   call** — one provisioning path, not two. Left outside the helper:
   `invalidate_for`, `record_admin_change` (the audit action differs —
   `org.member_invited` vs an approve action), and the `MemberEntry`
   construction. ⚠️ **`invite_member` has no test coverage today.** Write the
   characterisation test for the existing invite behaviour **first**, watch it
   pass, then refactor — this is an unfenced route on the auth path.
9. `POST /admin/members/requests/{email}/deny` sets `denied`. A denied address
   that keeps signing in updates `last_seen_at`/`attempt_count` but **does not**
   return to `pending`.
10. `/settings/members` grows a **Requests** tab showing address, first seen,
    last seen, attempt count, with Approve (role picker) and Deny. Approved and
    denied rows leave the tab. The tab shows a count badge when anything is
    pending — the whole point is that the owner learns without being told.
11. The existing Members list labels an `invited` row **"invited — never signed
    in"** rather than rendering it as though it were live. (Carried over from
    the retracted N6b; it is a label change, independent of the open question.)

**Done when — N6b:** ~~nothing to build. §2 Step 1b shipped the fix; the
remaining question is the owner's (a)/(b)/(c) above.~~ **UPDATED 2026-08-24
(R4):** the owner answered **(b), on the registry plane only** — see the ANSWERED
box above and **D50.3**. The Console-plane `invited → active` promotion is BUILT
(`customer_console.md` CP-2f); the tenant-plane `app_user` half is explicitly
**out of D50** and is now a named ticket rather than an open question. §2 Step 1b
is unchanged and still required.

#### As built (2026-08-04) — where each done-when landed, and what the ticket got wrong

| dw | Where it landed |
|---|---|
| 1 | `infra/postgres/143_access_request.sql` (143 was the next free number at build time; 142 was the highest) |
| 2 | `acb_auth/access.py` — `_ACCESS_REQUEST_UPSERT_SQL` + `_record_signin_request`, called AFTER the log line inside the `row is None` branch |
| 3 | `resolve_access(..., record_request: bool = False)`; the only caller passing `True` is `acb_auth/deps.py::_with_resolved_access` |
| 4/5 | Properties of the existing branch + cache; pinned, not coded |
| 6/7/9 | `gateway/routes/admin/access_requests.py` — three routes, each declaring `Depends(require_admin_user)`; both writes on `admin:members:invite`. `_load_request` takes the statuses it may act on as a required keyword (repair round); `APPROVE_MATRIX` + `_disposition_for` decide what approve does about an address that already has a row, and `_DECIDE_SQL` binds the same status filter into the write (repair round 2) |
| 8 | `_common.provision_member` (see below), which also enforces invariant 1 (repair round) |
| 10/11 | `settings/members/page.tsx` — `Tabs` (Members · Requests + badge), `RequestsTab`/`RequestRow`, `STATUS_LABELS` |

Four things this ticket stated imprecisely, corrected here rather than
silently deviated from:

* **dw1's column list has no primary key.** The table ships with
  `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, matching every other table
  in `infra/postgres/`. Uniqueness is still the `lower(email)` index the
  upsert's `ON CONFLICT` infers on.
* **dw3's list of non-knock callers is incomplete — and so was the correction.**
  The first draft named two callers; the as-built note added a third
  (`routes/chat.py:605`) and there is a **fourth**,
  `apps/services/orchestrator/orchestrator/executor.py:1644`. That is twice this
  list has been wrong, which is the point: **the safety property is "the default
  is `False`", enforced by the tree-scan test — it is not "we enumerated the
  callers".** `test_the_signin_path_is_the_only_caller_that_opts_in` reads every
  `.py` in the tree and asserts exactly one file contains `record_request=True`,
  so a fifth caller cannot appear quietly and no enumeration needs maintaining.
  Any caller list in this spec or in an `AGENTS.md` is illustrative.
* **dw8 puts the helper in `members.py`; it ships in `_common.py` as
  `provision_member`.** `admin/__init__.py`'s contract is that feature modules
  import from `_common`, never from each other, and `access_requests.py` is a
  feature module. `resolve_assignable_roles` and `set_roles` moved with it —
  they implement invariant 2 ("nobody grants above themselves"), which
  `_common.py` already documents as its own. `MemberEntry` stayed in
  `members.py`; approve returns its own `ApproveResult`.
* **dw7 does not say what approve does to somebody who already has a row.**
  It matters: approve is gated on `admin:members:invite`, which is **weaker**
  than the `admin:members:manage` needed to suspend or off-board. The general
  rule this ticket needed and never stated: **no path holding the weaker
  permission may reverse a decision taken under the stronger one.** See the
  repair block below for how the first attempt at that guard failed.

#### Repair round (2026-08-04) — the guard was half a guard

An adversarial review of the built branch found a **P1** and proved the fence
around it was not one. Both are fixed on `ws-24-n6-signin-requests`; recorded
here because the failure is more instructive than the fix.

**The P1 — approve reinstated a REMOVED member.** The claim "approve can never
reverse a decision taken under the stronger permission" was true for
`suspended` and false for `removed`:

1. Someone knocks → is approved → becomes a member.
2. They are off-boarded via `DELETE /admin/members/{email}`
   (`members.py:251-252`, gated `admin:members:manage`): `status='removed'`,
   `user_role` rows dropped.
3. Their `access_request` row **survives** — decided rows are kept on purpose
   (dw9) and the tab renders only `pending`, so the row is invisible in the UI
   *and* still addressable by the API.
4. A caller holding only `admin:members:invite` re-POSTs approve. The old
   `CASE WHEN app_user.status IN ('removed','invited')` matched `'removed'`,
   applied `:status='active'`, `set_roles` re-granted, `joined_at` was
   re-stamped and `invalidate_for` made it immediate.

On `main` that transition always required `admin:members:manage`. No seeded
role splits invite from manage today — but the Roles page exists to create
exactly such a role, at which point it is a straight cross-gate escalation.

**Fixed with two independent locks**, because each covers a sequence the other
does not:

| Lock | Where | Covers |
|---|---|---|
| A decided request cannot be decided again | `access_requests._load_request(db, email, *, allowed_statuses)` — keyword-only, **no default**, the `_row_to_person` shape from N4 | the sequence above (the request row is `approved`) |
| Provisioning never activates a row that is not `invited` | `_common._PROVISION_MEMBER_SQL`'s `ON CONFLICT` arms | a **pending** request whose address was invited and then removed in between — lock A does not fire there |

The SQL rule, stated so it is not "tidied" back: **the guard names the statuses
it rewrites and never negates.** `invited` → the caller's status (the one door
to `active`); `removed` → the caller's status **only when it is not `'active'`**
(so invite still returns an off-boarded person as `invited`, byte-for-byte as
before, while approve cannot reinstate them); `active` and `suspended` are never
touched. `IN (…)` and `<> 'active'` are both "…and everything else that
qualifies", which is how a row set under a stronger permission gets rewritten.

**dw7's "idempotent: approving twice is not an error" is superseded.** A second
approve now answers **409**, naming the decision and who made it. The invariant
dw7 actually cared about — never two members — is unchanged and stronger; what
went away is a 200 that silently did nothing, which was indistinguishable from
a 200 that provisioned, and which *was* the vehicle for the escalation above.
Deny stays replayable (`("pending", "denied")`): it provisions nobody and
revokes nothing, so re-denying changes only `decided_by`/`decided_at`. Denying
an *approved* request is refused — it cannot touch the live member it created,
so it could only produce a queue record that contradicts the roster.

**The fence that let it through, and what replaced it.**
`tests/unit/test_signin_requests.py`'s `_FakeDB` re-implements the
`ON CONFLICT DO UPDATE` arms in Python. **A mirror can only agree with itself:**
the verifier mutated `_common.py`'s guard to `app_user.status <> 'active'` —
the exact escalation — and all 28 tests passed.
`test_approving_never_un_suspends_somebody` did not test what its docstring
said. The repair adds `test_provisioning_only_ever_rewrites_a_status_it_names`,
which asserts **against the statement string**: every comparison against
`app_user.status` must be `=` against a literal we chose, the only two literals
are `'invited'` and `'removed'`, the `removed` arm must carry
`AND :status <> 'active'`, and no unaccounted reference to `app_user.status`
may appear. Re-applying the verifier's mutation now fails that test **and only
that test** — 39 behavioural cases stayed green, which is the demonstration.

> **⚠️ What the fake-DB suite does and does not prove — read before adding a
> case.** These tests never touch Postgres: routes are called directly with the
> DB seam monkeypatched, SQL is matched on normalised substrings, `ORDER BY` is
> ignored and `ON CONFLICT` is re-implemented. A behavioural case therefore
> proves *the route issues the right statement with the right parameters* — it
> cannot prove the statement says what we think. **These claims rest on
> structural assertions instead, and would be unfalsifiable without them:**
>
> | Claim | Asserted by |
> |---|---|
> | Which statuses provisioning may rewrite (both halves of the P1) | `test_provisioning_only_ever_rewrites_a_status_it_names` |
> | dw6's "newest `last_seen_at` first" | `test_the_pending_list_asks_the_database_for_the_order_it_promises` — the behavioural case compares a **set** and was renamed to say so |
> | dw9's "a denial is not undone by the next sign-in" | `test_a_denied_address_that_keeps_knocking_never_returns_to_pending` (reads `_ACCESS_REQUEST_UPSERT_SQL`; this one always worked and is the model the others copy) |
>
> Everything else here — the auth floor, the permission gates, invariants 1
> and 2, the decided-row refusal — is real Python and is genuinely exercised.

**Also fixed in the same round:**

* **`provision_member` never called `assert_owner_survives`** while `set_roles`
  wipes assignments first, so inviting *or approving* the address that is the
  last `owner` deleted the org's only owner grant and left it ownerless —
  recoverable only with SQL on the box. Every sibling write in `members.py`
  checks (`:209-210`, `:273`, `:311-315`); the helper now does too, narrowed to
  fire only when the target actually holds `owner` and the new set does not, so
  it cannot block provisioning in an org that has no owner yet. Pre-existing
  inside invite on `main`; the extraction hung a second route off it.
* **The queue could fail and look like success.**
  `setRequests(q.ok ? await q.json() : [])` swallowed a failed fetch, so a
  deployment where migration 143 had not run rendered *"Nobody is waiting."* —
  **the broken state was indistinguishable from the working one, which is the
  exact failure this ticket exists to fix.** (`_record_signin_request` swallows
  the same error into a journald warning nobody reads, by dw2's design.) The
  page now keeps a separate `queueError`, shows a non-dismissible warning
  banner without taking the roster down, drops the badge count rather than
  showing a false `0`, and never renders the empty-state copy unless the queue
  actually answered.
* **`143_access_request.sql` gained `CHECK (status IN (…))`.** The vocabulary
  was enforced only in `_decide`. Safe to edit in place *only* because the
  migration has never been applied anywhere — `CREATE TABLE IF NOT EXISTS`
  silently skips a constraint added after the fact.

#### Repair round 2 (2026-08-04) — a 200 that did not admit anybody

The first repair round stopped approve from **activating** a row it must not
touch. It did not stop approve from **reporting success** about it. A second
adversarial pass measured the built branch by executing it, and found the
refusal was silent on both sides:

    AFTER OFFBOARD:   status=removed  roles=[]         request=pending
    APPROVE RETURNED: HTTP 200        status=removed   roles=['member']
    AFTER APPROVE:    status=removed  roles=['member'] request=approved

`_load_request` admitted the row (it is `pending`, so lock A does not fire),
`_PROVISION_MEMBER_SQL` correctly declined the activation (lock B) — and then
`_decide(…, "approved")` ran anyway. Three consequences, in order of severity:

1. **The person was lost from the queue for good.** `_PENDING_REQUESTS_SQL`
   filters `status = 'pending'`, and the resolver's upsert deliberately never
   rewrites `status` (dw9), so every subsequent knock bumped `last_seen_at` on
   an `approved` row the tab will never render again. **That is the 53-knock
   incident, recreated by its own fix.**
2. The owner saw success, the row left the tab, and the colleague was still
   locked out with no message.
3. `set_roles` re-granted `['member']` to an off-boarded member on
   `admin:members:invite`, partially undoing the role-strip that
   `DELETE /admin/members/{email}` performs under `admin:members:manage`.

The same shape applied to a **pending** request over a live member: approve
replaced an `admin`'s entire role set with the picker's default on the weaker
permission, while roles are otherwise governed by
`PUT /admin/members/{email}/roles` on `admin:members:manage`.

**THE APPROVE MATRIX — as built.** `access_requests.APPROVE_MATRIX`, read by
`_disposition_for` **before anything is written**, so a refusal leaves both the
member row and the request untouched:

| existing `app_user` | approve does | request row |
|---|---|---|
| **absent** | provisions `active` with the chosen roles — the normal path | → `approved` |
| **`invited`** | activates them and assigns the chosen roles — that is what approving means (and it is §2 Step 1b in one click) | → `approved` |
| **`active`** | **nothing.** They already have access; their roles are left exactly as they are, and `ApproveResult.detail` says so | → `approved` (truthful: they *do* have access) |
| **`suspended`** | **409.** Lifting a suspension is `admin:members:manage` | stays `pending` |
| **`removed`** | **409.** Reinstating an off-boarded member is `admin:members:manage` | stays `pending` |

The invariant, stated so it is not tidied away: **approve never rewrites the
roles of a member who already exists in a state other than `invited`.**
`provision_member` ends in `set_roles`, which REPLACES assignments wholesale.

Two properties of the refusals are load-bearing and easy to lose:

* **The request stays `pending`.** Filing it as `approved` is what took the
  person out of the owner's sight permanently. A refused approval must leave
  them visible in the queue.
* **The refusal is a 409 that names the off-boarding or the suspension and
  sends the admin to the roster** — a silent decline is what let a 200 stand
  for it. An `app_user.status` nobody has decided about also refuses
  (`APPROVE_MATRIX.get(status, "refuse")`): fail closed is the only direction
  that cannot let somebody in by accident, and
  `test_the_approve_matrix_answers_every_member_status` pins the matrix
  against `members.VALID_STATUSES` so a fifth status cannot appear without
  somebody deciding what approving one means.

**The non-atomicity is closed, not just recorded.** `_load_request` read and
`_decide` wrote as two statements, so two concurrent approves both passed the
read. `_DECIDE_SQL` now carries `AND status = ANY(:allowed) … RETURNING id`,
binding the read's own filter into the write; zero rows updated raises 409
**before `db.commit()`**, so the loser's provisioning is discarded with its
transaction rather than half-applied. Each route hands `_decide` the same tuple
it handed `_load_request` (`("pending",)` for approve, `("pending","denied")`
for deny), and a test asserts that from the source, because the two agreeing is
the whole of the lock.

**Also in this round:**

* **The `assert_owner_survives` narrowing was unfenced.** The `db` fixture
  always seeds `u-owner`, so deleting the `"owner" in roles_for_user(…)`
  condition at `_common.provision_member` left all 40 tests green.
  `test_provisioning_is_not_blocked_in_an_org_that_has_no_owner` deletes the
  owner and mirrors that probe.
* **`allowed_email_domain()`'s docstring overclaimed.** It said "one reader for
  `ALLOWED_EMAIL_DOMAIN`"; `acb_common/settings.py:78` independently declares
  `allowed_email_domain: str = "fracktal.in"` with zero consumers. Corrected to
  "the one **live** reader", naming the duplicate.
* **The tab did not reload on a 409**, so a refused row lingered until Refresh.
  With three new refusal paths that is now the common case: `decide()` has no
  early return and `load()` runs on every response. A 200 carrying `detail`
  (the `already-a-member` path) is rendered as a notice, because an approval
  that ignored the role picker must not look like one that honoured it.
* **Two tests were rewritten because they asserted less than they claimed.**
  `test_approving_never_reinstates_a_removed_member` checked only `out.status`
  and `joined_at` — never the request row, never the role grant — which is why
  it was green against the trace above. Same for
  `test_approving_never_un_suspends_somebody`. Both now assert the refusal, the
  role set, the request status, and (for `removed`) that no audit record was
  written.

**Verification**

    uv run pytest tests/unit/test_signin_requests.py -q
    # 50 passed after repair round 2 (40 after round 1, 28 as first built).
    # Every case red-first against pre-fix behaviour.
    uv run pytest tests/unit/test_default_deny_auth.py \
                  tests/unit/test_org_access_control.py \
                  tests/unit/test_org_access_enforcement.py -q
    # test_org_access_enforcement.py is the route-wiring sweep -- run it
    # because N6a adds routes under /admin, where the auth floor is per-route.
    uv run ruff check . --select F821,F601,F602,F502,F7,B006
    cd workbench/control_plane && npx tsc --noEmit

✅ **The unprovisioned branch now HAS a DB-free regression fence.** Before N6a
the only test exercising `access.py`'s `row is None` branch was
`test_owner_bootstrap.py::test_unprovisioned_signin_is_cached`, which is
`@_needs_db` and unrunnable by an agent (`work_plan.md` §6 forbids pointing it
at prod). `test_signin_requests.py` builds the fence it was missing:
`resolve_access` runs against a fake session factory, so the whole branch —
refusal, log line, write, and the four cases where there must be **no** write —
is covered without Postgres. An earlier draft of this block claimed
`test_default_deny_auth.py` pinned the branch; it does not — that file never
calls `resolve_access`.

**Measured runs (2026-08-04, `ws-24-n6-signin-requests`, after repair round
2):**

    tests/unit/test_signin_requests.py ................. 50 passed  (was 40)
    the three sweeps above ............................. 97 passed
    ruff (blocking select) ............................. All checks passed!
    npx tsc --noEmit ................................... clean

Mutation evidence, taken in this tree and restored byte-identically afterwards
(sha256 verified each time). Round 1's two guards:

    _common.py CASE -> `app_user.status <> 'active'`
      => 1 failed, 39 passed — and the ONLY failure is the structural test.
         The behavioural cases cannot see it; that is why it is there.
    access_requests._load_request status guard disabled
      => 3 failed, 37 passed (approve-twice, the decided-row replay, deny-an-
         approved-request). With lock A disabled, lock B still held the member
         at `removed` — verified by probe, then discarded.

Round 2, one mutation per claim:

    APPROVE_MATRIX["removed"] -> "provision"           (the F1 defect, exactly)
      => 2 failed, 48 passed. `..._never_reinstates_a_removed_member` and the
         matrix test. The log line under the failure is the original trace:
         `access_request_approved … roles=['member'] email=gone@fracktal.in`.
    APPROVE_MATRIX["active"|"suspended"] -> "provision"       (the F2 defect)
      => 4 failed, 46 passed: the active-roles case (`['admin']` rewritten to
         `['member']`), the un-suspend case, the active-owner case, the matrix.
    _DECIDE_SQL drops `AND status = ANY(:allowed)`
      => 1 failed, 49 passed — and ONLY the structural test, because the fake
         re-implements the condition in Python. The mirror problem again; the
         structural assertion is the fence, exactly as for the CASE arms.
    _decide swallows a zero-row update (the 409 removed)
      => 1 failed, 49 passed (the lost-race case).
    _common.provision_member's `"owner" in roles_for_user(…)` deleted
      => 1 failed, 49 passed (the ownerless-org case, which did not exist
         before this round — the probe that motivated it left 40/40 green).
    page.tsx `decide()` returns early on !res.ok
      => 2 failed, 48 passed. ⚠️ Only ONE failed on the first attempt: the
         notice test asserted `"setNotice" in page`, which the declaration
         satisfies on its own. Strengthened to assert `setNotice(body.detail)`
         inside `decide` and `{notice && (` in the render, then re-measured.

Red-first, at commit `cf28f4af` (signatures scaffolded so each red is the claim
and not a `TypeError`): **16 failed, 12 passed.** The 12 already-green are dw8's
characterisation of the pre-existing `invite_member` — written and watched pass
*before* the extraction, because it was an unfenced route on the auth path —
plus the four dw3/dw4 no-write guards, which assert the ABSENCE of a write and
therefore cannot be red before the write exists.

⚠️ **OWNER-GATE is the merge, not a separate apply step.**
`scripts/apply_migrations.sh` replays every numbered migration on **every**
deploy (`deploy.yml:202-203`), so there is no agent-reachable "apply" to gate —
merging this arms a deploy that changes auth behaviour (`work_plan.md` §6,
supervised window). Writing the migration and the routes is AGENT-SAFE.

**Not in scope:** notifying the owner out-of-band (email/push on a new
request), self-service role requests, and any auto-approval rule. Capture and
answer, nothing else.

#### Repair round 3 (2026-08-04) — approve predicted the write instead of verifying it

**The same shape, a third time — and this round found *why* it kept coming
back.** Rounds 1 and 2 each fixed one instance of "approve returns 200 for
somebody it never let in". Round 2's fix was `APPROVE_MATRIX`, read **before**
the write. That closes the two *sequential* holes. It does not close the
*concurrent* one:

`_PROVISION_MEMBER_SQL`'s `CASE` arms are re-evaluated by Postgres against the
latest **committed** row — `ON CONFLICT DO UPDATE` waits on a concurrent writer
and then re-reads. So a second admin off-boarding or suspending the same person
between approve's `find_member` and its upsert lands every arm on
`ELSE app_user.status`. The provisioning declines **in silence**, and approve
went on to stamp `approved` and record an `org.access_request_approved` for it.
Measured, with the SUT unpatched — only the world underneath it moved:

    RESULT         : HTTP 200  status='removed'  roles=['member']  detail=''
    app_user.status: removed        can they sign in? NO
    request.status : approved       still in the owner's queue? NO, GONE

Three consequences, the same three as both earlier rounds: the owner is shown
success, the person is still locked out, and — because the queue renders only
`pending` and the resolver's upsert deliberately never rewrites `status`
(done-when 9) — **they can never reappear.** Every future knock bumps
`last_seen_at` on a row nobody will ever see again.

**The structural cause, stated plainly so it is not rediscovered a fourth
time:** `_DECIDE_SQL` was made race-safe in round 2, but that hardened the
`access_request` row only. The `app_user` row stayed a read-then-write with no
write-side guard. Approve verified by **prediction** — it read the row, decided
what *would* happen, and never read back what *did*. Every instance of this bug
has been a variation on that one omission.

**The fix:** before `_decide` stamps anything, the member must actually be
`active`, or the whole transaction is abandoned (nothing has been committed
yet, so the provisioning goes with it). Predicting is now only an optimisation;
the read-back is the authority.

**Also fenced:** the matrix's sixth row — "an unrecognised `app_user.status`
fails closed" — had no test. Flipping `APPROVE_MATRIX.get(status, "refuse")` to
`"provision"` left all 50 cases green.

> ⚠️ **A note on the fence for that one, because the first attempt at it was
> wrong in the way this section keeps warning about.** Asserting only
> `status_code == 409` is *not* enough: with the fallback flipped to
> `provision`, the provisioning runs, declines the unknown status silently, and
> the **new terminal read-back check** then raises its own 409 — so the test
> passed while the matrix was wide open. The two refusals are told apart by
> *when* they fire: the matrix refuses before anything is written, so the
> discriminator is that no role was granted (`set_roles` runs inside
> `provision_member`). Both assertions are now in the test. This is the third
> time in this ticket that a test asserted less than its docstring claimed;
> treat a bare status-code assertion here as a smell.

**Mutation evidence** (each applied, measured, reverted; the tree restored
byte-identical by sha256 — `250ab021…`):

| Mutation | Result |
|---|---|
| terminal read-back check disabled | **1 failed** — and the captured log under it is the trace above verbatim, `access_request_approved … disposition=provision` |
| `APPROVE_MATRIX.get(status, "refuse")` → `"provision"` | **1 failed**, on the "came from somewhere further down" assertion — the weak first version of the same test **survived** this |

`tests/unit/test_signin_requests.py`: **50 → 52 passed.**

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-24 — **Colleague onboarding readiness** — the gate, the runbook, and the capability matrix *(minted 2026-08-04)*
**State cell (as of the move):** 🔴 **NOT READY — but the shape changed on 2026-08-05: every AGENT-SAFE item is now BUILT, MERGED and DEPLOYED, and what remains is two owner actions on the identity boundary plus two on backups.** `main` @ `74082882` is live on the box: migration 143 applied (`access_request` exists), both services active, and the first real backup this deployment has ever taken landed at `/opt/acb/backups/2026-08-05T044202Z` (22 MB data dump) because #347's pre-migration gate fired. **N6a** (sign-in queue), **N7** (self-lockout guards on three doors + a Remove control), **N8** (hard delete) and the **OAuth connect-flow P0** all shipped. ⚠️ **Two findings measured against the running deployment, both OWNER-GATE, both in §6:** `GATEWAY_INTERNAL_TOKEN` is **byte-identical** to `LITELLM_MASTER_KEY` (same sha256), and gateway `:8080` + workbench `:3001` answer from the public internet, so Caddy's identity strip can be walked around. Until both are closed, every owner predicate in this plan is applied to an identity that can be forged. **The build rules an app must not deviate from now live in `specs/user_management_contract.md`** (§4 registry). Historical state below. ✅ G4 CLOSED 2026-08-04 — all FOUR tickets shipped:** N4 (`ws-24-n4-people-scoping`) the Tasks people directory is *directory open, HR fields restricted* with all four writes on `admin:members:manage`; **N1–N3** (`ws-24-n1n3-notes-scoping`) the Notes owner-scoping remainder. **G1/G2/G3 unchanged — inviting anybody is still unsafe.** **✅ N6a BUILT + REPAIRED 2026-08-04** (`ws-24-n6-signin-requests`, spec §6) — the sign-in queue: migration 143 `access_request`, `resolve_access(record_request=)` gated to the request path only, `GET/POST /admin/members/requests…`, a Requests tab, and `invited` rows now labelled "never signed in". ⚠️ **A same-day adversarial review found a P1 cross-gate escalation** — approve could reinstate an off-boarded member on the weaker `admin:members:invite`, because a decided `access_request` row outlives the decision and the `ON CONFLICT` guard matched `removed`; and the test that claimed to fence it was a Python mirror of the same SQL, so the exact mutation passed all 28 cases. Both fixed: a decided request cannot be re-decided, provisioning never activates a row that is not `invited`, and the fence is now a structural assertion against the statement string. ⚠️ **A SECOND pass then found the half that fix left open, and it was the more damaging one:** the provisioning guard declines *silently*, so approve still returned **200**, still marked the request `approved`, and still re-granted `['member']` to the off-boarded member — which removed the still-locked-out person from a tab that renders only `pending`, permanently, since the resolver's upsert never rewrites `status`. **That is the 53-knock incident recreated by its own fix.** Closed by `APPROVE_MATRIX` (spec §6 *Repair round 2*), read before anything is written: absent/`invited` → provision; `active` → leave their roles alone and say so; `suspended`/`removed` → **409 and the request stays `pending`**. The invariant now stated and fenced: **approve never rewrites the roles of a member who already exists in a state other than `invited`** — the same defect demoted a live `admin` to `member` on `admin:members:invite`. `_DECIDE_SQL` also binds the read's status filter into the write, so a lost race discards its own provisioning. ⚠️ **A THIRD pass found the same shape once more — a race this time, not a sequence — and with it the reason it kept recurring.** `APPROVE_MATRIX` is read *before* the write, which closes the sequential holes but not the concurrent one: `_PROVISION_MEMBER_SQL`'s `CASE` arms are re-evaluated by Postgres against the latest **committed** row, so a second admin off-boarding the same person between approve's `find_member` and its upsert lands every arm on `ELSE app_user.status` — the provisioning declines silently and approve stamps `approved` over it, losing the still-locked-out person from the queue permanently. **The structural cause, now stated in spec §6 so it is not rediscovered a fourth time: approve verified by *prediction* — it read the row, decided what would happen, and never read back what did.** Fixed by requiring the member to be `active` before the decision is stamped; nothing is committed until then, so a refusal abandons the provisioning with its transaction. Also fenced the matrix's fail-closed default, which nothing pinned. ⚠️ **The first version of that fence was itself too weak** — asserting only `409` passed while the matrix was wide open, because the new read-back check raised its own 409; the discriminator is that a matrix refusal grants no role. **That is the third test in this ticket to assert less than its docstring claimed.** 52 tests, eight mutants measured red and reverted sha256-identical. N6a is **not a gate** and does not move this row's colour; **merging it IS an owner gate** (§6 of this plan — `deploy.yml:202-203` replays migrations, so the merge arms an auth-behaviour deploy). N6b needs no code; one owner question (auto-promote on first sign-in?) is recorded in spec §6. **✅ N7 BUILT 2026-08-04** (`ws-24-n7-self-removal-guard`, spec §2 Step 5) — **off-boarding yourself.** `DELETE /admin/members/{email}` refused the caller; `PATCH /admin/members/{email} {"status": "suspended"}` reaches the identical `is_active=False` and had **no self-check at all** — it refused only because `assert_owner_survives` happens to fire in a one-owner org, so **adding the second owner §2 Step 2 exists to create opened it**, and `admin:members:manage` is the floor for undoing it. The Members page rendered the button, because it never learned who the viewer was. One shared guard now (`_common.assert_not_self_lockout`) called by **both** doors; the rule is **"any status that is not `active`"** rather than a list, so `invited` — equally a lockout, since `is_active` is `status == "active"` exactly — is covered by construction; comparison case-insensitive and empty-safe. The roster reads `access.email` and renders **This is you** where Suspend/Remove were, and the shipped-but-uncalled `DELETE` finally has a UI behind a confirmation that names the person. ⚠️ **Both guards answer 409**, so every refusal test discriminates on the detail text *and* on what was written; the dw4 pair seeds **two** owners so only the self-guard can be answering. 22 new cases + 8 vitest; six mutants measured red and reverted (PATCH guard deleted → 8 red incl. dw4; DELETE guard deleted → 2; `.lower()` dropped → the 4 casing cases; rule narrowed to an enumeration → the `invited` fence; browser guard ignoring self → vitest; Suspend rendered unconditionally → the page-wiring case). Test fake extracted to `tests/unit/_admin_fakes.py` and shared with `test_signin_requests.py` (52 passed, unchanged) rather than copied. **No migration and no new slug, so unlike N6a merging it is not an auth-behaviour deploy gate**; it is not a §1.1 gate item and does not move this row's colour. **✅ N8 BUILT + REPAIRED 2026-08-05** (`ws-24-n8-purge-member`, spec §2 Step 5) — **deleting a member permanently.** Remove was the only off-boarding and it is soft by design (status → `removed`, grants dropped, `app_user` kept because ~every user-scoped table keys people by address); that stays. `DELETE /admin/members/{email}/purge` is a **second, harder action beside it, never a `?hard=` flag** — a flag would put the irreversible path one typo from the reversible one. Decision: **purge the person, keep their work.** The identity, every grant, every credential, their private sessions and their `access_request` row go; what they authored and **the audit trail** stay, and nothing is anonymised (the address is the join key across ~50 tables, so scrubbing `owner_email` orphans their apps rather than hiding them). Fourth door on the one shared `assert_not_self_lockout`, plus `assert_owner_survives`; one transaction, audited before the commit, a count per table in the response. ⚠️ **Verification returned FAIL and the headline defect was a count that lied in the reassuring direction.** `task_accounts` cascades the SYNCED half of `gtd_items`, and the KEEP clause counted those rows anyway — 847 synced tasks came back as `kept: {"tasks": 847}` with all 847 destroyed; `gtd_projects` (same cascade) was on neither list. **The response did not miss a destruction, it reported it as a survival.** Fixed by splitting both tables on `account_id` the way `chat_session` is split on `visibility`. **Why nothing caught it is the durable lesson: every structural assertion compared a row-spec to itself, and the test fake models no foreign keys — so no cross-table claim was checked by anything.** `tests/unit/_schema_cascade.py` now derives the FK cascade graph from the numbered migrations and three fences use it (no KEEP clause inside the delete side's blast radius unless it is the exact complement of a DELETE clause; every cascade child with its own person column must be reported; the hand-maintained cascade map is compared to the schema). Two more gates were unfenced and are now pinned: **deleting the route's `require_permission` left 162 tests green** (the fallback floor is `admin:members:read`, which `manager` holds — hard-delete for every manager), and **`const confirmed = true;` in the confirmation left 32 pytest + 173 vitest green** (done-when 6 was tested by grepping for copy; the rule now lives in `confirmPurge.ts`). The cascade map was also understated in the dangerous direction — 15 of 20 email tables, `wa_media` one hop too high — now derived and pinned. Recorded not fixed: `acb_audit/log.py:49` swallows every exception, so "the audit entry survives a rollback" is true but "a completed purge always leaves an audit row" is not. 39 + 28 + 152 pytest, 178 vitest; nine mutants measured red and reverted. **No migration and no new slug**, so like N7 and unlike N6a, merging it is not an auth-behaviour deploy gate; it is not a §1.1 gate item and does not move this row's colour.
**Narrative (verbatim):** **Read this row before inviting anybody, and before assuming any other row's access work is safe to demonstrate with a second person.** Exactly one member is signed in (`vjvarada@fracktal.in`, §4). The question "is it safe to invite colleagues" had been re-derived in conversation repeatedly and recorded nowhere; the spec is the durable answer and `scripts/onboarding_preflight.py` is its executable half (**agent-safe to write, NOT to run against prod — `--mode local` is an agent's only mode**; it refuses the box-only checks rather than guessing, because `resolve_access` degrades to `is_active=False` on an unreachable DB too, so a local PASS on default-deny would be vacuous). **The blockers, each with a done-when in §1.1 — G4 is the one that closed: G1** the Caddy strip — `deploy/hostinger/caddy/Caddyfile:13-18` has **no** `header_up -X-User-Email` / `-X-User-Role`, and `acb_auth/deps.py:27-35` says in its own docstring that the reverse proxy IS the boundary, because nothing in that module can tell a forwarded identity header from a forged one. 🔴 OWNER-GATE to install (writing the repo file is agent-safe). **G2** `GATEWAY_INTERNAL_TOKEN` unprovisioned ⇒ service identity falls back to `LITELLM_MASTER_KEY` (`deps.py:108-117`), the key every agent's BYOK client holds; `GATEWAY_REFUSE_LLM_KEY_IDENTITY` (PR #346) makes that refusable and **ships OFF**, and is inert once the token is set. 🔴 OWNER-GATE (a credential, in two places — the Next BFF mirrors the same fallback at `lib/gateway.ts:58-61`, so flipping the flag with the token unset 401s every signed-in member). ⚠️ **G2 has a LOCKOUT mode, repaired in the preflight 2026-08-04.** Setting the token in `/opt/acb/app/.env` only — which is what "restart the gateway and the workbench" invites — leaves the BFF sending `sk-local-dev-change-me`, so every proxied browser call carries a bad Bearer with a real `X-User-Email` while an internal token *is* configured, and `deps.py:356-361` returns **NO_ACCESS for every signed-in member**. Check 1 read only `.env` and would have certified that state green; it now reads `workbench/control_plane/.env.local` too and FAILs naming the lockout when the two disagree. Do it by **redeploying** — `.github/workflows/deploy.yml:166-187` reconciles `.env.local` from `.env` in place on every deploy, so the only dangerous window is "provisioned by hand without a redeploy", which is exactly what a hand-run owner gate looks like. **G3** a restore path — **BO-23 is unbuilt**: there is no data-inclusive dump, no `pg_restore` inverse, no restore runbook and no pre-migration hook; `scripts/dump_schema.sh` is `--schema-only` (structure, zero rows). `scripts/backup_db.sh` and `restore_db.sh` are proposed on the **independent** PR #347 (`ws-0-bo23-backup-restore`) and are **not on this branch**. 🟢 agent-safe to write, 🔴 owner-gate to run or schedule. ⚠️ **Repaired 2026-08-04:** the preflight's check 4 used to assert an `acb-backup.timer` unit and a `MANIFEST.txt` that **BO-23's own done-when never specifies**, while testing no dump format, size or restore script — so a schema-only dump printed "Backups run, land, and are recent" over zero rows, and G3 could not have gone green even after BO-23 shipped exactly what it promised. It is now measured against `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-23 done-when 1-4 verbatim, plus a size floor on the newest dump; the timer is probed as a note, never asserted by name. **G4** the four owner-scoping holes (below) — **all four closed 2026-08-04, so this gate IS green. WS-24 is not**: G4 closes the holes that survive a *correct* identity, and G1/G2 are about the identity itself — an owner predicate applied to a forged `X-User-Email` is not a control.** **PR #348 IS in this branch's ancestry** — `permissions.py:95-100` carries the six `center.*` slugs, so the preflight's Centers check passes here. **✅ G4's N4 CLOSED 2026-08-04** (`ws-24-n4-people-scoping`, spec §4 N4's `owner-answered` DECISION block): **directory open, HR fields restricted.** `GET /tasks/people` still serves the org chart to any `feature:tasks` holder, but `skills`, `skills_source`, `resume_summary`, `years_experience` and capacity/current-load/available are projected to null/empty for a caller without `admin:members:read` (`routes/tasks/people.py` — `HR_FIELDS`, `_row_to_person(row, *, include_hr)` with **no default**, so a future route cannot inherit the permissive answer), and `?q=` drops its `unnest(skills)` clause for that caller so the search box cannot become an oracle for the field the strip exists to hide. All **four** write routes — `POST /people`, `PATCH /people/{person_id}`, `POST /people/{person_id}/resume`, and `capability.py`'s `POST /people/embed` — carry `require_people_write()` = `admin:members:manage` as a route dependency (`routes/tasks/core.py`). **No new permission slug** was minted, deliberately: a new slug is nobody's grant until an admin creates it, which would switch HR features off for the owner too; both permissions are existing `CAPABILITIES` entries and the owner's `*` matches both. Consequence recorded, not a defect: a `manager` (holds `admin:members:read`, not `:manage`) sees the HR half and cannot write it — consistent with the matrix. `fetch_people_for_clarify` is **unchanged** and still returns full rows: the projection is at the serialization layer, never in the SQL, so in-process agent delegation (`ai.py`, `capture_email.py`, `planning.py`) is untouched. `tests/unit/test_tasks_people_scoping.py`, 35 cases, three mutants verified red first. **✅ G4's N1–N3 CLOSED 2026-08-04** (`ws-24-n1n3-notes-scoping`, cut from `891903de`), all three reachable until then with the default `member` role because it holds `feature:notes` (`130:235`). **N1** — fifteen of the sixteen routes in the six named files (`recordings.py` upload/start/chunk/complete/audio, `qa.py`, `share.py`, `copilot.py` ×2, `live.py`'s `/stt/live-token`, `actions.py` ×3) now load through `core.load_owned_meeting` or bind `core.OWNED_MEETING_PREDICATE` and answer **404, never 403**. `_recording_path` — the loader `/chunk` and `/complete` share — carries the join, so neither can acquire the hole separately and the per-chunk path pays no extra round trip; `qa` loads the meeting **before** the transcript so the 409 "no transcript yet" stops being an oracle; the copilot **stream** checks before the `StreamingResponse` starts, because a 404 raised inside a started stream is a broken connection, not a refusal; `share.py` was read first and has no sharing mechanism to preserve (no grant, no token, no redemption — the send is a separate `/email/send` under the caller's own account), so the whole route is a read. **`live.py:256` stays machine-authed by recorded decision** — the caller is the bot worker with `MEETING_BOT_TOKEN` and no member identity, so an owner predicate has no owner, and both ways to invent one turn the bot token into a way to *assert* an identity; it discloses one boolean plus a settings-derived sentence, and the same answer for an id that does not exist. **N2** — `actions._load_action` joins `meeting` and binds the predicate, so both single-item routes inherit it; the test pins **both** harms separately (no `INSERT INTO gtd_items`, no `UPDATE action_item`, and the colleague's description never reaches a bound parameter), because a 404 alone would not have proved the exfiltration half. `approve-all` was *aligned* rather than left alone: already safe at the `_dispatch` seam, it answered **200 with an empty list** — "your meeting, nothing qualified" where the truth was "not your meeting" — and read the colleague's draft rows to get there. **N3** — the attach branch binds the predicate **into the `UPDATE`** (`UPDATE meeting AS m … WHERE m.id = … AND (lower(m.owner_email)=lower(:owner) OR m.owner_email IS NULL) RETURNING m.id`) rather than loading first: a load-then-write leaves a window, and this statement *is* the mutation. The acting principal is the **caller**, necessarily — it is the only identity the request carries, and checking the row against its own `owner_email` would compare the meeting to itself and pass every time; the asymmetry is preserved, not collapsed, and a test pins that the ingest side still reads `meeting_bot.requested_by`. Evidence: `tests/unit/test_notes_owner_scoping.py` 21 → **57 passed**, every non-owner case verified **red** against pre-fix behaviour *with the parameter renames already applied* (so each red is the security claim, not a `TypeError`), plus four mutants — drop the audio guard, drop the action-item predicate, drop the `bot_join` predicate, and compare against the wrong identity — each red on exactly its own cases with the tree byte-identical after revert. Notes suite **280 passed**, `test_org_access_enforcement.py` **31 passed**. ⚠️ **TWO findings recorded, neither fixed here.** (a) **N1's table was not exhaustive** — `routes/notes` has 24 modules and **nine** still carry zero owner predicates after this change (`summaries.py`'s `GET`/`PUT /meetings/{id}/note` + `GET .../actions`, `copilot_context.py`, `copilot_agenda.py`, `meeting_bot.py`'s four `/bot/*` routes, `live_transcript.py` incl. `POST /meetings/{id}/say` — which makes the notetaker *speak into somebody else's call* — `live_session.py`, `speaker_id.py`, `agenda_progress.py`, `events.py`). Minted as spec §4 **N5**, deliberately **outside G4**: G4's done-when is "each of §4's four tickets meets its own done-when" and all four do, and re-scoping an owner-facing gate is the owner's call. **Owner decision needed:** does N5 block colleague #1? (b) `/notes/meetings/{meeting_id}/live/wanted` is in **neither** `main.PUBLIC_ROUTES` **nor** `core.router`'s `exempt` list while both its siblings are in both — so `require_authenticated` and then the feature gate 401 the worker before `_check_bot_auth` runs, and the poll that decides whether to keep paying for streaming ASR is dead. Not fixed here because the fix *opens* a route, the opposite of this change's direction. `test_org_access_enforcement.py`'s own `GATED_ROUTERS` lists the path, which is how the drift stayed invisible — that registry is the test's opinion, not the router's. **Two findings that correct the received account of the roles, both in spec §3.0 — anything quoting `130` alone is wrong:** (a) role grants come from **two** migrations — `131_integration_memory_permissions.sql` additionally gives `member` `integrations:use:*` **and `memory:read_org`** (`131:70-78`), gives `manager`/`admin` `memory:write_org` too, and gives `guest` **nothing** (`131:80`); (b) **`data:org:read` grants nothing — it has zero consumers.** It is declared (`permissions.py:132`), granted to admin/manager/agent_service (`130:205, 221`) and listed in the legacy fallback (`access.py:148`), and **no route, query or predicate in the tree ever checks it**. So "manager has org-wide visibility" is a name, not a mechanism; what actually widens a manager is `admin:members:read` (the floor for the **whole** `/admin` package, `admin/_common.py:77-91`, and `is_admin: true` at `me.py:96`), plus `feature:approvals`/`observability`/`whatsapp` and `memory:write_org`. That is **D14**. **Three more measured cells worth carrying up here** (full matrix in spec §3): `feature:memory`, `feature:artifacts` and `feature:observability` are enforced **nowhere server-side** (`memory.py:45-48` gates on the internal Bearer then per-scope; `workspace.py:53` and `observability.py:46-51` gate on nothing beyond authentication) — they hide a nav pane and the per-object rule is the boundary, exactly as `lib/access.ts:126-129` says; **artifacts are shared for most agents**, because 4 of the 6 first-party `config.json`s declare `instancing: "shared"` ⇒ `instance_key()` = `''` ⇒ one workspace for everybody (`workspace.py:230-260` → `manifest.py:235-246`); and a **member can read/write every agent's memory compartment**, since `_authorize_agent` (`memory.py:103-109`) gates on `can_run_agent` and member holds `agents:run:*`. **Granting `feature:workflows` is a labelled consequence, not a defect** (spec §3.4): org-wide read is a recorded v1 decision (`crud.py:1-5`), the detail response returns `hook_token` (`crud.py:230`), and the hook route is unauthenticated by design (`core.py:29`, `hooks.py:3` — "the token IS the credential"), so the grant hands over a permanent copyable trigger for **every** workflow that survives off-boarding, and there is no rotate endpoint. **Not in this row:** building spec §4's new **N5** (the nine further `routes/notes` modules) until the owner says whether it blocks colleague #1, per-Center *data* scoping (WS-14/WS-15 — `140_center_features.sql:9-12` is explicit that Center features gate navigation and the landing pages, not data), and shared mailboxes (ownerless, §4).

**Corrections applied 2026-08-09:**
- G3 (backups) is CLOSED — BO-23 nightly timer verified scheduled 2026-08-07, restore rehearsed 2026-08-05 (live=228 restored=228)
- the ports-open claim closed 2026-08-05 (UFW rules removed, verified from outside)
- 'backup_db.sh/restore_db.sh not on this branch' is stale — both plus rehearse_restore.sh are in scripts/
- G2's rotation is unblocked (delivery recovered 2026-08-06/07 UTC, see deploy_delivery_path.md)
- D14's zero-consumer measurement for data:org:read is retired (WS-27d is its first consumer — re-verify the capability matrix before member #2).
