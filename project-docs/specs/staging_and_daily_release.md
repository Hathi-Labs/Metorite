# Staging and the daily release — a second deployment that takes every merge

**Status:** 🟠 **PROPOSED, 2026-10-15. Revised 2026-10-16:** staging goes on the dev box (§5).

**Built:** 🔨 phase 1, the daily clock (§5.7), 🔨 phase 2 (§5.8 and §5.9), and 🔨 the code of phases 4 and 5 (§5.11).
All on 2026-10-16, on the owner's go-ahead. Phase 3 and the first run of phase 4 are the owner's (HANDOFF H-335). Nothing of phases 4 and 5 has run yet.

**Owner:** vjvarada ·
**Board row:** WS-38 (this is the concrete answer to its **D-D**, and a proposed
amendment to its **D-E**) · **Parent spec:** `specs/development_and_delivery_framework.md`

> **The answer.** Put a second deployment, `dev.metorite.com`, on the dev box that
> already runs the agent sessions (`srv1747539`), and buy nothing. Every merge to
> `main` deploys there. Production moves **once a day**, at a fixed time, to the newest
> commit that staging has run and passed. Both deployments use the **same sign-in
> providers** (Google and Microsoft) and the **same AI Router** (the Customer Console). They never share a database, a session secret or a seat.

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
                     │                release ref      │        dev box srv1747539     
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
| **Sign-in providers** | ✅ **Shared** | The same Google Cloud project and the same Microsoft Entra tenant, so a member sees the same sign-in. Staging gets its **own OAuth client** in each, with the redirect URI `https://dev.metorite.com/api/auth/callback/google` or `…/microsoft-entra-id`. §5.5 gives the reason |
| **Session secret (`AUTH_SECRET`)** | ❌ **Separate** | A staging cookie must never open a production session. The cookies are host-only, so they do not cross `dev.` and `app.` in the browser either |
| **Membership (who may sign in)** | ❌ **Separate rows, same people** | The nightly rebuild copies the `app_user` and membership rows of the kept organizations only (§5.11). Every other organization leaves no row. So staff sign in to staging with their own Google account, and a customer cannot |
| **AI Router (Customer Console)** | ✅ **Shared** | Staging reaches the same Router through a private tunnel (§5.3), with its **own** `cc_live_` key. The runbook mints it on our own organization `hathi-labs-llp` (S-6). Spend on staging is metered, and it never bills a customer |
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

**What shows that you are on staging.** Staging draws a 4px warning stripe across
the top of every page, and its tab title reads `[Staging] Metorite`. Both come from
`DEPLOY_ENV=staging` in the box's root `.env` (§5.9). Without them, a person can act
on staging and think the act reached production.

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

## 5. Where staging runs — the two boxes we already pay for

**Revised 2026-10-16. Recommendation: put staging on the dev box, `srv1747539`,
and buy nothing.** The owner pays for two KVM 2 boxes. The production box serves
customers, and the dev box runs the Claude Code sessions. Staging joins the dev box,
and the production box loses work rather than gains it.

### 5.1 The two boxes, measured 2026-10-16

| | Production — `srv1914284`, `187.127.172.200` | Dev — `srv1747539`, `187.127.179.143` |
|---|---|---|
| Plan | KVM 2: 2 vCPU, 8 GB RAM, no swap | KVM 2: 2 vCPU, 8 GB RAM, 8 GB swap |
| Disk | 96 GB, 17 GB used | 96 GB, 47 GB used |
| Runs today | gateway (about 600 MB), workbench, Console, operator console, agent edge, Redis, meeting bot | Claude Code sessions, two scratch Postgres containers on 5433 and 5434 |
| Tenant and Console databases | **on Supabase**, not on the box. The local `acb-postgres` container carries no gateway connection | none |
| Restarts | **36 gateway starts in the 24 hours to 05:58 UTC** | not applicable |
| Builds | every deploy runs `next build` on the production CPUs. One deploy of one commit took 3 min 32 s (04:07:23 to 04:10:55 UTC) | many builds and test runs a day, by agents |
| Root access for an agent | `ssh metorite` with passwordless `sudo` | **no passwordless `sudo`** for the `dev` user |

### 5.2 What each box does after the change

**Production box: customers only.** It keeps every unit it has. Its `acb-pull`
timer keeps polling `release`. The only change is that `release` moves once a day,
so the box restarts once a day and builds once a day. Nothing new is installed on it.

**Dev box: agents plus staging.** Staging runs as a second system user, `acb`, in
`/opt/acb/app`, the same path and user as production. The scripts then need no
path change. The agent sessions keep running as `dev` in `~/Metorite`.

| Part on the dev box | How |
|---|---|
| Checkout | `/opt/acb/app`, owned by `acb`. The `acb-pull` timer runs `vps_pull.sh` every 5 minutes, the production interval. `DEPLOY_ENV=staging` selects the `staging` ref |
| Services | the same units as production, less the Console, the operator console, the WhatsApp bridge, the meeting bot and the production timers (§5.4) |
| Ports | the production ports: gateway `8080`, workbench `3001`, agent edge `8091`. ⚠️ An agent's local dev server must then use other ports. The local recipe already puts the gateway on `8010`. Move its workbench from `3001` to `3101` |
| Database | a Postgres 16 container on `127.0.0.1:5432`, rebuilt every night (T-9). The scratch containers stay on 5433 and 5434 |
| Redis | a container on `127.0.0.1:6379` |
| Web proxy | Caddy on ports 80 and 443. Both are free on the dev box today |
| Limits | every staging unit sits in one systemd slice, `acb-staging.slice`, with `MemoryMax=3G` and `CPUWeight=50`. A runaway staging build then cannot take the memory the agent sessions need, and the reverse holds too |
| Delivery | **pull only.** GitHub never opens SSH to the dev box. The box polls `staging`, so it needs no new GitHub secret and no inbound port |

**Why staging must not go on the production box.** The production box has the same
2 vCPU as the dev box and no swap. A staging stack there would run `next build`
about 32 times a day on the CPUs that serve customers. That is today's problem in a
new place. The table in the earlier draft named this as option A and rejected it.

### 5.3 How staging reaches the shared AI Router

The production Console listens on `127.0.0.1:8090` only, and the production firewall
opens ports 22, 80 and 443 only. So staging cannot reach the Router today. That is
correct, and it must stay correct for the internet.

**Recommendation: an SSH tunnel, not a new public host name.**

1. Make a new key pair on the dev box, owned by `acb`. It is not the `dev` user's
   key.
2. On the production box, add its public half to `~acb/.ssh/authorized_keys` with
   these options: `restrict,port-forwarding,permitopen="127.0.0.1:8090",command="/bin/false"`.
   That key can open one port and do nothing else.
3. On the dev box, a unit `acb-console-tunnel.service` holds
   `ssh -N -L 127.0.0.1:18090:127.0.0.1:8090 acb@187.127.172.200`, with
   `Restart=always`.
4. Staging sets `CUSTOMER_CONSOLE_URL=http://127.0.0.1:18090` and its own `cc_live_`
   key (S-6).

**What this costs.** If the tunnel drops, a staging chat turn fails until the unit
reconnects. Staging is where that failure belongs. The production Console gets no
new listener and no new public name.

### 5.4 What the apply script must learn first

⚠️ **Do not run `vps_apply.sh` on the dev box as it is today.** It installs
`deploy/hostinger/caddy/Caddyfile`, which claims `app.metorite.com` and
`api.metorite.com`. It also enables every `*.timer` in `deploy/hostinger/`, which
includes the production backup timer and the 6-hourly chat smoke check. It seeds
`GATEWAY_PUBLIC_URL=https://api.metorite.com` when the `.env` has none.

So the apply script needs one setting, `DEPLOY_ENV`, read from the box's `.env`,
with two values:

| `DEPLOY_ENV` | Caddyfile | Timers | Units |
|---|---|---|---|
| `production` (the default) | `caddy/Caddyfile` | all, as today | all, as today |
| `staging` | `caddy/Caddyfile.staging`: `dev.metorite.com` and `api.dev.metorite.com` only | `acb-pull` and `acb-health-watchdog` only | gateway, workbench, agent edge, `acb-console-tunnel` |

The default is `production`, so the production box needs no edit. ✅ One part is
already safe: the Console ladder is skipped on a box that holds no Console DSN and
does not enable `acb-customer-console`. Measured in `vps_apply.sh`, the block after
`CC_ENV=`.

### 5.5 The security line between agents and staging

The agents on the dev box are the main risk to staging, and staging is a risk to
production through the dev box. Four rules hold the line:

1. **Staging secrets belong to `acb`, mode 0600.** The `dev` user has no
   passwordless `sudo`, so an agent cannot read them.
2. **Use a separate OAuth client for staging**, in the same Google Cloud project and
   the same Microsoft Entra tenant. Members see the same "Sign in with Google". A
   leak of the staging client secret then does not expose the production client.
   This changes the earlier draft, which shared one client.
3. **The `acb` user on the dev box holds one key to the production box.** It is
   the tunnel key of §5.3, and it can open one port only.
4. ⚠️ **The `dev` user's own `ssh metorite` key reaches production with `sudo`.**
   That is true today and this design does not change it. Record it as a finding.
   Staging does not make it worse, because staging runs as another user.

### 5.6 The rollout, in order

Each phase stands alone. Production gets more stable after phase 1, before any
staging exists.

| Phase | What | Who | Production effect |
|---|---|---|---|
| **1. The daily clock** | `deploy.yml` stops deploying on each push and publishes `staging` only. A new `release-promote.yml` moves `release` at 21:30 UTC and deploys production. A person can run it by hand for a hotfix | an agent builds the PR. The owner approves S-A and S-B | **36 restarts a day becomes 1** |
| **2. Make the scripts environment-aware** | `DEPLOY_ENV` in `vps_apply.sh`, `Caddyfile.staging`, the host names from configuration (S-3), the staging band (S-7) | an agent | none. The default is `production` |
| **3. Prepare the outside accounts** | DNS: `A` records `dev` and `api.dev` to `187.127.179.143`. Google and Entra: one new OAuth client each, with the `dev.metorite.com` redirect URI. The Console: a staging key on `hathi-labs-llp` (S-6). The steps are `STAGING_SETUP_RUNBOOK.md` | **the owner**, about 30 minutes | none |
| **4. Bootstrap staging on the dev box** | one root script, `deploy/hostinger/bootstrap_staging.sh`, that an agent writes. It makes the `acb` user, the checkout, the containers, the slice, the tunnel unit and Caddy. The owner drops the staging secrets through `scripts/secrets.sh` | an agent writes it. **The owner runs it once with `sudo`** | none |
| **5. Staging gates the clock** | the nightly anonymised rebuild (T-9), the smoke checks (S-8), and the §4.2 conditions in `release-promote.yml` | an agent builds. The owner approves the allowlist (S-C) | each release is now one that staging ran first |

**Phase 1 alone is the quick win.** Without phases 2 to 5, it removes 35 restarts a
day and adds no safety, because each daily batch reaches production untried. With
phase 5, the batch has run on production-shaped data for at least 4 hours first.


### 5.7 Phase 1 as built, 2026-10-16

The owner said "go ahead with the development" on 2026-10-16. Phase 1 uses the
21:30 UTC hour of §4.1. It is one line to change, in the `schedule:` block of
`deploy.yml`.

| Part | What it does | Fence |
|---|---|---|
| `deploy.yml`, a push | lint, then the unit tests as a signal, then `publish-staging`. No deploy | `tests/unit/test_daily_release.py` |
| `deploy.yml`, the schedule | lint, then the unit tests as a **gate**, then `publish-release`, then the deploy of that exact commit | same |
| `deploy.yml`, a dispatch | as the schedule, plus a `reason` input. `skip_tests` is the one way past the tests | same |
| `RELEASE_HOLD=1` | a repository variable. The schedule moves nothing, and a `Release held` job says so. A dispatch still runs | same |
| `vps_apply.sh` | deploys `DEPLOY_REF` when a caller names it, else `origin/release`. It refuses a commit that is not on `main` | `tests/unit/test_deploy_target_ref.py` |
| `vps-health.yml`, `delivery` | compares the box with `release`. It fails when `main` holds a commit that has waited 30 hours for a release | `test_daily_release.py` |

⚠️ **A defect that this phase found and fixed.** `vps_apply.sh` deployed
`origin/main` on both paths. `vps_pull.sh` passed `DEPLOY_REF=<release sha>`, and
the apply never read it. While `release` moved on every merge, the two were seconds
apart. Under a daily clock, the 5-minute pull timer would have shipped the untried
tip of `main` at the next tick after any release. A mutation that puts
`origin/main` back turns `test_deploy_target_ref.py` red.

**The cutover, once.** Until `release` holds this change, the box's pull timer
runs the OLD apply from `release`, and that copy ships the tip of `main`. So:

1. Merge only when no `deploy` run is in flight (`gh run list --workflow deploy.yml --status in_progress`).
2. At once, run `gh workflow run deploy.yml --ref main -f reason="WS-38 cutover"`.
3. Confirm that `GET /version` serves the merge commit, and that `release` points at it.

After step 3, `release` holds the new apply and the window is closed.

⚠️ **Do not rerun a `deploy` run that started before this change.** It uses the old
workflow and the old apply, so it resets the box to the tip of `main`.

**A rerun cannot roll production back.** A rerun of an old release run keeps its
old SHA and sends `DEPLOY_FORCE=1`. The deploy job skips a commit that `release` has
passed, and `vps_apply.sh` refuses one, forced or not.

**What phase 1 does not do.** No box polls `staging` yet. The ntfy report of §4.1
needs a topic secret in GitHub, which the owner holds. Until then a failed release
shows as a failed `deploy` run, and GitHub mails the owner.

---

### 5.8 Phase 2 slice S-4 as built, 2026-10-16

One setting, `DEPLOY_ENV`, in the box's own `.env`, makes the box production or
staging. Absent means production, so the production box needs no edit. Any other
value stops the round before the lock. Fence: `tests/unit/test_deploy_env.py`.

| On a staging box | Where |
|---|---|
| The pull and the apply default to the `staging` ref | the same block in `vps_pull.sh` and `vps_apply.sh`. It survives `set -euo pipefail` when the line is absent |
| `Caddyfile.staging` is installed. It serves `api.dev.metorite.com` and `dev.metorite.com` only, and its two blocks equal production's line for line | `vps_apply.sh`, the Caddy step. CI validates both files |
| `GATEWAY_PUBLIC_URL` and `WORKBENCH_PUBLIC_URL` default to the staging hosts | the public-URL step |
| Two timers run: `acb-pull` and `acb-health-watchdog`. Each other timer is disabled and logged | a staging branch beside H-132's loop. The production loop is unchanged |
| The watchdog probes the `api.` host of the Caddyfile its box serves | `health-watchdog.sh` |

**What stays in `.env`, and needs no code.** The bootstrap (S-5) writes
`WHATSAPP_BRIDGE_ENABLED=0` and `MEETING_BOT_ENABLED=0`. The Customer Console and
the operator console run only when their units are enabled, and the bootstrap
enables neither. The Console ladder is then skipped (§5.4).

### 5.9 S-3 and S-7, 2026-10-16

**S-3 needs no code.** A re-measure found seven places in code that name a
production host. Five of them no setting moves, and two are settings already.
None of them reaches a person from staging:

- The WhatsApp link host (`whatsapp_ui.py` `LINK_HOST`, `views.py` `APP_URL`,
  and the `bot_run.py` prompt) is a security allowlist against a phishing link.
  WhatsApp is off on staging. A setting there would widen the allowlist for no
  gain.
- `INVITE_SIGNIN_FALLBACK_ORIGIN` applies when the WORKBENCH has no
  `WORKBENCH_PUBLIC_URL` in its own `.env.local`. The S-4 default writes that key
  to the root `.env` only, so S-5 must write it to `.env.local` too (§5.10). No
  invite mail goes out from staging in any case: it needs
  `MEMBER_INVITE_EMAIL_ENABLED=true` and a Resend key.
- `inviteSeat.ts` shows an admin the words "sign in at app.metorite.com". It is
  on screen only, and it sends nothing.
- `agent_authorize_url` and `SMOKE_BASE_URL` are settings already. S-5 writes
  the staging values (§5.10).

**S-7 is built.** `src/components/StagingBand.tsx` draws a 4px `bg-warning`
stripe across the top of every page when `DEPLOY_ENV=staging`. The tab title
reads `[Staging] Metorite`. Both read the setting on the server, and production
renders nothing new.

The stripe is an overlay that takes no space and no clicks, because the shell
fills 100dvh. Fence:
`src/lib/deploy/deployEnv.test.ts`. A visual review on 2026-10-16 captured it in
eight contexts on two pages, and it found no P0 and no P1.

### 5.10 What the S-5 bootstrap must do, collected

Each line below is a finding of the S-4 and S-7 reviews. S-5 is done only when
its script does every one, and refuses to finish when one is missing.

1. **Turn on the firewall first.** Allow 22, 80 and 443, and deny the rest. The
   gateway listens on `0.0.0.0:8080`. Without the firewall, a request reaches it
   directly, past the header strip of `Caddyfile.staging`.
2. **Write `DEPLOY_ENV=staging`** in `/opt/acb/app/.env`, in that exact form.
3. **Write the outbound switches off:** `WHATSAPP_BRIDGE_ENABLED=0` and
   `MEETING_BOT_ENABLED=0`. Without them, the apply seeds 1.
4. **Write the staging hosts** to both env files: `WORKBENCH_PUBLIC_URL` in
   `.env.local` too, `AGENT_AUTHORIZE_URL=https://dev.metorite.com/connect/authorize`
   and `SMOKE_BASE_URL=https://dev.metorite.com`.
5. **Hold no production secret.** No production `ACB_MASTER_KEY`, `AUTH_SECRET`,
   Resend key, SMTP password, WhatsApp token or mail OAuth secret. Without the
   production `ACB_MASTER_KEY`, the restored provider keys stay ciphertext, and
   that is the intent.
6. **Keep port 3001 for staging.** The local recipe runs `next dev -p 3001`. On
   the dev box, an agent's dev server on 3001 during a staging restart would be
   served at `dev.metorite.com`. Refuse to start when another user holds 3001,
   and record the rule for the agents on that box.
7. **Build the database from a restore of production (T-9), never from the
   ladder alone.** The production RLS policies came from the H3 cutover, outside
   the numbered migrations. A ladder-only database has no tenant isolation, and
   R5 forbids that on staging. The gateway connects as a non-owner role, as
   `acb_app` does on production.

### 5.11 The staging box and its nightly copy, as built 2026-10-16

**Built, and not yet run.** The owner runs the bootstrap once (HANDOFF H-335).
The steps, for a person or a browser session, are `STAGING_SETUP_RUNBOOK.md`.
Its part D gives staging a second Router key on `hathi-labs-llp`, not a new
`Metorite Staging` customer: the form for a new customer also grants seats and
a trial.
Each part below has its fence.

| Part | Where | What it does | Fence |
|---|---|---|---|
| The reduction | `infra/staging/minimise.sql` | On a restore of production, keeps the organizations in `keep_slugs`, deletes every row of every other one, keeps an identity only for a kept member, and blanks each credential. It refuses on an unknown slug, on an unclassified table, and on a credential left behind | `test_staging_minimise_r8.py` |
| The export | `scripts/staging_export.sh`, called by `backup_db.sh --verify-restore` | Runs the reduction in the nightly verify container on the production box, then dumps it with the roles, the extensions, production's feature switches and checksums. Off while `STAGING_EXPORT_SLUGS` is unset. Its failure never fails the backup | `test_staging_export.py` |
| The rebuild | `scripts/staging_refresh.sh`, `acb-staging-refresh.timer` at 03:30 UTC | Pulls the export, checks it, rebuilds the Postgres 17 database, gives the gateway the non-owner `acb_app` role, copies the switches with every outbound one held off, and deploys the `staging` ref | `test_staging_box.py` |
| The box | `deploy/hostinger/bootstrap_staging.sh` | Firewall first, the packages, the `acb` user, Node 20.20.2 private to `acb`, the checkout, the env files with fresh secrets, the database, and the first rebuild | `test_staging_box.py` |
| The units | `deploy/hostinger/staging/` | The database, the Console tunnel, the rebuild, and a slice that caps the staging services at 3 GB. Production never reads this folder | `test_deploy_env.py`, `test_staging_box.py` |

**What leaves the production box.** Only the kept organizations, measured
2026-10-16 as `default`, `fracktalworks` and `hathi-labs-llp` (Fracktal Works
and Hathi Labs), plus the internal `smoke-chat`. The four customer
organizations (Prad Automotive, DEWiN, and both Axiom Aerials entries) leave no
row. In production, 185 of the 191 public tables carry `organization_id`. The
reduction names a rule for each of the other six.

**One key, two uses.** The staging box holds one key to the production box. Its
line in `authorized_keys` (`deploy/hostinger/staging/authorized_keys.line`)
lets it forward the Console port and read the export folder. It gives no shell.

**Known gaps.**

- The `dev` user is in the `docker` group, so an agent on the dev box can read
  the staging database. The reduction is what bounds that, and it is why only
  our own organizations travel.
- The pooler gap of §5 stays: staging uses plain Postgres 17, not Supavisor.

## 6. The build tickets

These extend WS-38. T-7, T-8 and T-9 are the parent spec's tickets. The `S-` rows
are new. The phase column maps each row to §5.6.

| # | Phase | Ticket | Done when | Gate |
|---|---|---|---|---|
| **T-7** | 1 | **The `staging` ref.** `publish-release` becomes `publish-staging` and fast-forwards `staging` on each green `main` | as the parent spec §8 T-7 | 🟢 |
| **T-8** | 1 | **`release-promote.yml`.** A `schedule:` trigger at 21:30 UTC plus `workflow_dispatch` with a SHA and a reason. It fast-forwards `release` and runs the production deploy job, which moves here from `deploy.yml`. In phase 1 it promotes the tip of `staging`. In phase 5 it applies §4.2 | `release` moves only by this workflow. A SHA that is not on `staging` is refused. A held day (§4.3) moves nothing. The ntfy report names the SHA and the `/version` it read back | 🟢 build · 🔴 enable the schedule |
| **S-3** | 2 | **Host names from configuration.** ✅ **Closed 2026-10-16 with no code** (§5.9). Re-measured: no hard-coded host reaches a person from staging. The settings that S-5 must write are in §5.10 | §5.9 records each site and its reason | ✅ no code |
| **S-4** | 2 | **`DEPLOY_ENV` in the apply script** (§5.4), with `Caddyfile.staging` | `caddy validate` passes on both files. `test_deploy_env.py` proves that `staging` installs neither the production Caddyfile nor the backup timer | 🟢 |
| **S-7** | 2 | **The staging band.** ✅ **Built 2026-10-16** (§5.9). `DEPLOY_ENV=staging` in the root `.env`, which `vps_apply.sh` copies into the workbench `.env.local` before the build, draws a 4px `bg-warning` stripe and the `[Staging]` tab title | Visible on staging in light mode and at compact density. Absent on production. Fences: `deployEnv.test.ts` and `test_deploy_env.py` | ✅ built |
| **S-6** | 3 | **The staging Router key.** A second `cc_live_` key on `hathi-labs-llp`, labelled `staging box (dev.metorite.com)` (runbook part D). Its key goes on the dev box only. ⚠️ Staging spend then lands in Hathi Labs' ledger with no cap of its own. A separate `Metorite Staging` customer would give one, and costs a seat grant and a trial. The owner decides which | A chat turn on staging writes a usage row against `hathi-labs-llp`, under the staging key's label, and against no customer | 🔴 owner (an admin operator mints the key) |
| **S-5** | 4 | **`bootstrap_staging.sh`** (§5.2, §5.3, §5.5, and the duty list in §5.10) | `https://api.dev.metorite.com/health` answers. `/version` serves the `staging` SHA. `sudo -u dev cat /opt/acb/app/.env` is refused | 🟢 write · 🔴 owner runs it with `sudo` |
| **T-9** | 5 | **The nightly rebuild**, with the §3 staff allowlist in the anonymiser. The anonymised dump moves from the production box to the dev box over a second forced-command key | as the parent spec §8 T-9. A staff member signs in to staging. A scrambled member cannot | 🟢 build · 🔴 run |
| **S-8** | 5 | **Smoke checks.** The four §4.2 checks run after each staging deploy | A failed check blocks that SHA from promotion | 🟢 |
| **S-9** | 5 | **The contract-after-expand fence** (§4.5) | A test PR whose contract names an expand that is not on `release` turns CI red | 🟢 |

**One more use of the dev box, outside this spec.** The off-box backup has never run,
because the S3 keys are missing (WS-49). The dev box is a second machine at the same
provider, so it is a weak off-box target: one Hostinger account failure takes both.
It is still better than none. If the owner wants it, encrypt each dump with `age` to a
public key whose private half the owner holds. The dev box then holds ciphertext only,
and no agent on it can read customer data.

---

## 7. Decisions this needs from the owner

| # | Decision | Recommendation | Why an agent cannot take it |
|---|---|---|---|
| **S-A** | **Amend D-E** from "on demand, soak floor one working day" to "daily at a fixed time, soak floor 4 hours, manual dispatch for a hotfix" | Yes | D-E is recorded. An agent does not reopen a decision (CLAUDE.md §5) |
| **S-B** | **The release hour** | 21:30 UTC (03:00 IST), unwatched, with the ntfy report | It sets when a customer meets a change, which is a business call |
| **D-D** | **Where staging runs** | The dev box, `srv1747539` (§5). No new cost | It places customer-shaped data on the box where agents run |
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
- **The pooler gap.** Staging uses a plain Postgres container and production uses the Supabase pooler. Name it in the parent spec's §3.4 table.
- **Agents and staging share 2 vCPU.** A heavy agent job slows staging down. The slice of §5.2 caps the damage. A slow staging hurts no customer.
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
