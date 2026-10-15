# Staging and the daily release — a second deployment that takes every merge

**Status:** 🟠 **PROPOSED, 2026-10-15.** Nothing here is built. · **Owner:** vjvarada ·
**Board row:** WS-38 (this is the concrete answer to its **D-D**, and a proposed
amendment to its **D-E**) · **Parent spec:** `specs/development_and_delivery_framework.md`

> **The answer.** Put a second deployment, `dev.metorite.com`, on a second small VPS.
> Every merge to `main` deploys there, as every merge deploys to production today.
> Production moves **once a day**, at a fixed time, to the newest commit that staging
> has run and passed. Both deployments use the **same sign-in providers** (the one
> Google app and the one Microsoft app) and the **same AI Router** (the Customer
> Console). They never share a database, a session secret or a seat.

**What this document owns.** The topology, the shared-versus-separate table, the
promotion clock and the build tickets. **What it does not own.** The branching model,
the parity rule and the anonymised nightly rebuild already live in the parent spec
(§2, §3.2, §3.4). This document builds on them and does not restate them. If the two
disagree, the parent spec wins until the owner records a decision.

---

## 0. Why now — measured 2026-10-15

| # | Fact | Command |
|---|---|---|
| 0.1 | `main` took **480 merges** from 2026-10-01 to 2026-10-15. That is about **32 a day**, and **57** on 2026-10-09 | `git log origin/main --first-parent --since=2026-10-01 --oneline \| wc -l` |
| 0.2 | **Every merge is a production deploy.** `deploy.yml` runs on each push to `main`, and `publish-release` moves `release` on each green run | `.github/workflows/deploy.yml` (the `on:` block and the `publish-release` job) |
| 0.3 | **48 new tenant migrations** landed in the same 15 days. Each one reached production data on the day it merged, with no rehearsal on production-shaped rows | `git diff --name-only --diff-filter=A <sha at 2026-10-01> origin/main -- infra/postgres` |
| 0.4 | **A deploy restart ends every live agent run**, because the runs execute inside `acb-gateway` | `HANDOFF.md` H-293 · `specs/chat_run_continuity.md` §5 |
| 0.5 | **The gateway cold start takes 15 to 23 seconds.** Caddy holds a request for 30 seconds, and after that a member sees "Metorite is updating" | `deploy/hostinger/caddy/Caddyfile` (the H-60 comment) |
| 0.6 | **A deploy with a pending migration takes a full `pg_dump`** of the production database. That load helped drain the free-plan database on 2026-10-07 | the incident record behind `#707` |
| 0.7 | The pull script already reads the ref it applies from an environment variable, `RELEASE_REF`. A staging box needs no new script, only `RELEASE_REF=staging` | `scripts/vps_pull.sh` line 42 |

**Read 0.1 with 0.4 and 0.5.** About 32 times a day, a customer can lose a running
agent and can wait up to 30 seconds for a page. The customer gets nothing in return,
because most of those merges carry work that ships dark behind a flag. The cost is
real and the benefit goes to us, not to the customer.

⚠️ **This request is also a Phase 0 end-trigger.** The parent spec §3.5 says we work
directly in production until customer #2, a second contributor, or H3. A complaint
about the customer experience means somebody other than us now pays for a deploy.
The owner records the end of Phase 0. An agent does not.

---

## 1. The topology

```
                     ┌──────────── GitHub ─────────────┐
 feature branch ─PR─►│ main ──CI green──► staging ref  │──── every merge ───┐
                     │                     │           │                    │
                     │      daily, 21:30 UTC (03:00 IST)                    │
                     │                     ▼           │                    ▼
                     │                release ref      │        staging VPS (new, small)
                     └─────────────────────┼───────────┘        dev.metorite.com
                                           │                    api.dev.metorite.com
                                           ▼                    own Postgres, own Redis
                                 production VPS (today's box)   own .env, own AUTH_SECRET
                                 app.metorite.com                        │
                                 api.metorite.com                        │
                                 production database                     │
                                           │                             │
                                           └──────────┬──────────────────┘
                                                      ▼
                                 SHARED: Google + Microsoft sign-in apps,
                                         the AI Router (Customer Console),
                                         the DNS zone, the GitHub repository
```

**Three refs, and none of them takes a commit of its own** (parent spec §2.1).
`main` is the trunk. `staging` and `release` are fast-forward-only pointers at
commits that already exist on `main`. Nothing merges into them, and nothing is
cherry-picked onto them (D55.4).

**The production box does not change.** Its pull timer still polls `release` every
5 minutes. What changes is how often `release` moves: once a day, not 32 times.

---

## 2. What the two deployments share, and what they must not

The rule from the parent spec §3.4 holds: everything is identical except data,
secrets, scale and outward-facing integrations. This table applies that rule to the
two things the owner asked to share.

| Part | Shared? | How |
|---|---|---|
| **Code** | Same repository, different ref | staging runs `staging`, production runs `release` |
| **Edge router (DNS and Caddy)** | Same DNS zone, separate Caddy | Two explicit `A` records, `dev` and `api.dev`, point at the staging VPS. ⚠️ The zone has a `*.metorite.com` wildcard that sends every unknown name to the production box today. An explicit record wins over the wildcard. Each box runs its own Caddy with its own hostnames |
| **Sign-in providers** | ✅ **Shared** | The same Google OAuth client and the same Microsoft Entra app. Add one redirect URI to each: `https://dev.metorite.com/api/auth/callback/google` and `…/microsoft-entra-id` |
| **Session secret (`AUTH_SECRET`)** | ❌ **Separate** | A staging cookie must never open a production session. The cookies are host-only, so they do not cross `dev.` and `app.` in the browser either |
| **Membership (who may sign in)** | ❌ **Separate rows, same people** | The nightly rebuild copies production's `app_user` and membership rows. The anonymiser keeps the real email for the staff allowlist (§3) and scrambles every other address. So staff sign in to staging with their own Google account, and a customer cannot |
| **AI Router (Customer Console)** | ✅ **Shared** | Staging calls the same Router URL with its **own** `cc_live_` org key, for an internal organization named `Metorite Staging`. That organization has a credit cap. Spend on staging is metered, and it never bills a customer |
| **Console sign-in resolve and seats** | ❌ **Off on staging** | `CUSTOMER_CONSOLE_RESOLVE_ENABLED` stays unset, and staging holds no `CUSTOMER_CONSOLE_DEPLOYMENT_KEY`. A staging sign-in must not allocate a seat on the production Console. This is a named parity exception |
| **Console schema** | ❌ **Production clock only** | Staging never runs `apply_customer_console_migrations.sh`. One Console serves both deployments, so its ladder moves with `release` and with nothing else |
| **Tenant database** | ❌ **Never shared** | A forward-only ladder (R6) on a shared database means staging writes production's schema 32 times a day. That is today's problem with one more step |
| **Redis** | ❌ Separate | A container on the staging VPS |
| **LLM provider keys** | Through the Router | Staging holds no direct provider key that the Router path does not need |
| **Outbound to people** (invite mail, WhatsApp, Gmail and Outlook sync, calendar sync) | ❌ **Off** | Off is the safe direction. A staging mail to a real customer is the §3a rule 3 failure |
| **Payments** | Razorpay TEST mode | as the parent spec §3.4 |
| **Feature flags** | Production's set, plus the flag on soak | as the parent spec §3.4, clause 3 |

⚠️ **The tempting shortcut is to point staging at the production database.** It
gives "real data" for free. It also removes the only thing staging exists to add,
which is a place for a migration to fail before it meets customers. Do not do it.

---

## 3. Who can sign in to staging

**Recommendation: staff only, by an allowlist of organizations.** The nightly
rebuild keeps real emails for the members of the listed organizations and
scrambles all other emails.

The first allowlist holds two organizations: Hathi Labs and Fracktal (customer
zero, D36). A Fracktal person can then try a change on staging a day before it
reaches production. That is Ring 0 of `engineering_practice.md` §2, moved one step
earlier.

**Why scramble the rest.** Staging holds production-shaped data. Under DPDP it is
customer data until the identifiers are gone (parent spec §3.4, clause 1). A
scrambled email also locks that member out of staging, which is the correct result.

**What shows that you are on staging.** Staging serves a fixed band at the top of
every page that reads "Staging". The band comes from one environment variable,
`NEXT_PUBLIC_DEPLOY_ENV=staging`. Without it, a person can act on staging and think
the act reached production.

---

## 4. The daily promotion

### 4.1 The clock

**Recommendation: one scheduled promotion a day, at 21:30 UTC (03:00 IST).** Our
members are in India. That hour has the least traffic, so the one restart costs the
fewest runs. The production backup timer fires at 02:30 UTC, five hours later, so
the two never overlap.

The cost of 03:00 IST is that nobody watches it. So the workflow must report its own
result as a push to the owner's ntfy topic. The push names the promoted SHA, the
migrations it applied, and the `/version` SHA it read back. If the owner wants a watched release,
use 10:30 IST and accept one restart in working hours. This is the owner's call
(§7, decision S-B).

### 4.2 What the promotion picks

The `release-promote` workflow takes the **newest commit on `staging` that meets all
four conditions:**

1. CI was green on it.
2. Staging serves it. Staging's `/version` reads that SHA back.
3. It has run on staging for at least **4 hours** (the soak floor).
4. The staging smoke checks passed against it after the deploy. There are four:
   `/health`, one sign-in, one chat turn through the Router, and one tenant read
   under RLS.

If no commit meets all four, the workflow promotes nothing. It reports why, and
production stays on yesterday's release. A skipped day is a safe result.

⚠️ **The soak floor here is 4 hours, and D-E records "one working day".** Under D-E, a
commit that merges at 18:00 IST waits about 33 hours. With a floor of 4 hours, a
commit that merges before 23:00 IST ships that night. Changing D-E is the owner's act
(§7, decision S-A).

### 4.3 The hold switch

A repository variable, `RELEASE_HOLD`, stops the daily promotion while it is set.
Set it when staging shows a defect that nobody has fixed yet, or during a customer
demo. The workflow reports "held" and moves nothing. Clear it to resume.

### 4.4 Hotfixes take the same road, faster

There is no second lane (parent spec §2.1). A hotfix is a PR to `main` like any
other. It deploys to staging within minutes. Then a person runs `release-promote`
by hand, with that SHA and a written reason. The run still requires conditions 1,
2 and 4. It waives only the soak floor, and it writes the reason into the run
summary.

### 4.5 Migrations ship in a daily batch, and that adds one rule

R6 still binds: expand first, and contract in a later release. Today "a later
release" means "a later merge", which can be minutes later. Under a daily clock, an
expand and its contract can merge on the same day and reach production in the
**same** release. The old code then never runs against the expanded schema, and
the contract can break the code that was running before the release.

**The new rule: a contract migration may ship only when its expand is already on
`release`.** Fence (R7): a CI check reads each new migration's header line
`-- contracts: <expand migration number>` and fails when that number is not
an ancestor of `origin/release`. Until the check exists, this rule is advisory.

**What the batch gains.** Staging applies each migration on yesterday's
production-shaped data first (T-9). That is the rehearsal that the 148 cast and the
`LATERAL` scope error needed, and nothing gives it today. Production also takes one
pre-migration dump a day, not one for each migration merge.

### 4.6 Release is still not deploy

A promotion moves code. It flips no flag. A feature still ships dark and still
reaches customers by a flag flip that a person chooses (`engineering_practice.md`
§2). So staging is where a flag gets its soak, and production is where the flag
flips, on any day, independent of the clock.

---

## 5. Where staging runs, and what it costs

This is **D-D**, which has waited since 2026-08-26 (`HANDOFF.md` H-38).

| Option | Monthly cost | For | Against |
|---|---|---|---|
| **A. Same box, second set of units** | about ₹0 | No new account. The DNS stays simple | ⚠️ **It does not fix the problem.** Every staging build runs `next build` on the production CPU about 32 times a day. The box build race (`.next.staging`) is already a recorded hazard on one deployment |
| **B. A second Hostinger VPS, KVM 2** (2 vCPU, 8 GB) **(recommended)** | about $15 (about ₹1,300) | Builds and restarts on staging never touch the production box. The same `bootstrap.sh` sets it up. One more box gives a second place to prove a box change | A second box to patch and monitor. `vps-health.yml` must learn a second host |
| **C. Option B, with a second Supabase project for the staging database** | B plus about $10 | The database matches production's pooler (Supavisor), which the 2026-10-07 incident showed matters | One more external account. Supabase limits free projects for each organization |

**Recommendation: B now, and C later if a pooler defect slips past staging.** The
staging database is a Postgres 16 container on the staging VPS. It is rebuilt every
night, so it holds nothing worth keeping. The restore path already exists: `#707`
restores the nightly dump into a local container for `--verify-restore`. Staging
reuses that path and keeps the result.

**What B does not match:** the Supabase pooler and its pool limits. Record that gap
in the parent spec's §3.4 table as a named difference. Then "it passed on staging"
keeps a defined meaning.

---

## 6. The build tickets

These extend WS-38. T-7, T-8 and T-9 are the parent spec's tickets. The `S-` rows
are new. The order matters, because each row needs the row above it.

| # | Ticket | Done when | Gate |
|---|---|---|---|
| **S-1** | **GitHub Environments.** Create `staging` and `production`. Move `HOSTINGER_HOST`, `HOSTINGER_USER`, `HOSTINGER_SSH_KEY` and the public URLs into each | `gh api repos/Hathi-Labs/Metorite/environments` lists both, and each holds its own host secret | 🔴 owner (repository settings and a new SSH key) |
| **S-2** | **One deploy job, two targets.** Turn the `deploy` job into a reusable workflow that takes an environment name. A push to `main` calls it with `staging`. Remove the hardcoded `GATEWAY_URL` and `WORKBENCH_URL` from `deploy.yml` | A merge deploys to staging and production does not move. The production box's `/version` still serves the earlier SHA | 🟢 build · 🔴 first run |
| **T-7** | **The `staging` ref.** `publish-release` becomes `publish-staging` and fast-forwards `staging` | as the parent spec §8 T-7 | 🟢 |
| **T-8** | **`release-promote.yml`.** A `schedule:` trigger at 21:30 UTC plus `workflow_dispatch` with a SHA. It applies §4.2, §4.3 and §4.4, fast-forwards `release`, and calls the S-2 job with `production` | `release` moves only by this workflow. A SHA that is not on `staging` is refused. A held day moves nothing | 🟢 build · 🔴 enable the schedule |
| **S-3** | **Host names from configuration.** 23 files outside the tests name `app.metorite.com` or `api.metorite.com` directly. They include `vps_apply.sh`, `health-watchdog.sh`, `inviteEmail.ts` and the WhatsApp views. Read each name from one setting, `PUBLIC_APP_URL` and `PUBLIC_API_URL` | `rg -l "app\.metorite\.com\|api\.metorite\.com"` outside tests and docs returns only the production `.env` template and the production Caddyfile | 🟢 |
| **S-4** | **A Caddyfile for each environment.** `deploy/hostinger/caddy/Caddyfile.staging` holds `dev.metorite.com` and `api.dev.metorite.com`, and no marketing or operator vhost. `vps_apply.sh` installs the file that matches `DEPLOY_ENV` | `caddy validate` passes on both files. `test_deploy_serialize.py` covers both | 🟢 |
| **S-5** | **Bootstrap the staging VPS** with `bootstrap.sh`, `RELEASE_REF=staging`, its own `.env`, its own `AUTH_SECRET` and the outbound integrations off | `https://dev.metorite.com/api/health` answers, and `/version` serves the `staging` SHA | 🔴 owner (buys the box, DNS records, OAuth redirect URIs) |
| **T-9** | **The nightly rebuild**, with the §3 staff allowlist in the anonymiser | as the parent spec §8 T-9. A staff member signs in to staging with their own Google account. A scrambled member cannot | 🟢 build · 🔴 run |
| **S-6** | **The staging Router key.** Create the `Metorite Staging` organization on the Console, with a credit cap, and put its `cc_live_` key on the staging box only | A chat turn on staging writes a usage row against `Metorite Staging` and no other organization | 🔴 owner (a write to the live Console) |
| **S-7** | **The staging band.** `NEXT_PUBLIC_DEPLOY_ENV=staging` draws a fixed band on every page | Visible on staging in light mode and at compact density. Absent on production | 🟢 |
| **S-8** | **Smoke checks and report.** The four §4.2 checks run after each staging deploy. The promotion posts its result to ntfy | A failed check blocks that SHA from promotion, and the owner gets the push | 🟢 |
| **S-9** | **The contract-after-expand fence** (§4.5) | A test PR whose contract names an expand that is not on `release` turns CI red | 🟢 |

**What to do first if the owner wants relief this week, before the staging box
exists.** Change `publish-release` and the `deploy` job to run on a `schedule:` once a
day, and not on every push. Production then moves once a day with no staging at all.
It is a two-line change and it removes the 32 daily restarts at once. It removes no
risk, because each release is then a larger batch that nothing rehearsed. Treat it as
a bridge to S-2 and T-8, not as the result.

---

## 7. Decisions this needs from the owner

| # | Decision | Recommendation | Why an agent cannot take it |
|---|---|---|---|
| **S-A** | **Amend D-E** from "on demand, soak floor one working day" to "daily at a fixed time, soak floor 4 hours, manual dispatch for a hotfix" | Yes | D-E is recorded. An agent does not reopen a decision (CLAUDE.md §5) |
| **S-B** | **The release hour** | 21:30 UTC (03:00 IST), unwatched, with the ntfy report | It sets when a customer meets a change, which is a business call |
| **D-D** | **Where staging runs** | §5 option B, a KVM 2, about $15 a month | Money and an external account |
| **S-C** | **The staging allowlist** | Hathi Labs and Fracktal | It decides who can see production-shaped data |
| **S-D** | **Record the end of Phase 0** (parent spec §3.5) | Yes, trigger (a) in spirit: the cost of a deploy now falls on a customer | The owner records a phase change |
| **S-E** | **May an agent run the hotfix dispatch?** | Yes during the dev-phase window, with the written reason. Owner-only after it | It moves production outside the clock |

---

## 8. What this does not fix

- **A daily restart still ends the live agent runs at that moment.** The fix is to
  run agents in their own service (H-293, decision D-1 of `chat_run_continuity.md`).
  The daily clock cuts the count from about 32 a day to 1, at the quietest hour.
- **A staging box that nobody opens soaks nothing.** The four smoke checks are the
  floor. The real soak is staff using `dev.metorite.com` during the day, which is
  why §3 lets Fracktal in.
- **The pooler gap of option B** (§5). Name it in the parity table.
- **The Console plane still has one environment.** Staging shares the production
  Console by design. A Console change still needs the lifecycle rehearsal of the
  parent spec §6.3 before it ships.

---

## 9. Verification commands

```bash
# 0.1 — merges a day
git log origin/main --first-parent --since=2026-10-01 --format=%cd --date=short | sort | uniq -c

# how far production is behind staging, after T-7
git fetch origin main staging release
git rev-list --count origin/release..origin/staging

# which migrations the next promotion carries
git diff --name-only origin/release origin/staging -- infra/postgres/

# what each box serves
curl -s https://api.metorite.com/version
curl -s https://api.dev.metorite.com/version

# S-3 — host names still in code
rg -l "app\.metorite\.com|api\.metorite\.com" --glob '!**/*test*' --glob '!**/*.md'

# the environments, after S-1
gh api repos/Hathi-Labs/Metorite/environments --jq '.environments[].name'
```

---

## 10. References

- `specs/development_and_delivery_framework.md` — §2.1 the refs, §3.2 the nightly
  rebuild, §3.4 the parity table, §3.5 Phase 0, §8 T-7 to T-9, §9 D-D and D-E
- `specs/engineering_practice.md` — §2 deploy is not release, and the rings · §3 R6
- `specs/deploy_delivery_path.md` — WS-25, the pull timer and `release`
- `specs/customer_console.md` — §4 the AI Router · §6 the sign-in resolve
- `specs/chat_run_continuity.md` — §5 D-1, agent runs in their own service
- `HANDOFF.md` — H-38 (D-D), H-293 (agent runs and deploys)
- `deploy/hostinger/README.md` — the box plans and `bootstrap.sh`
