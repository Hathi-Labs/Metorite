# Deploy delivery path — getting merged code onto the box

**Status: 🟡 RECOVERED (re-measured 2026-08-09) — deploys landing again since
2026-08-06 (migrations 144/145 applied on prod that day); six green runs on
2026-08-07 UTC alone, the last being #400's log-verified deploy `31217978773`
(2026-08-08 IST, which is how `crm_app.md` dates it). Open: the tip run
(`b09093a8`, docs-only) failed health-verify ×3 rounds, 21:21→22:16 UTC
2026-08-07 — box one docs-only commit behind, cause unresolved. Everything below
this header is the 2026-08-05 diagnosis, kept as the record; re-measure before
quoting it.**

`main` is `d7d5c79b`; the box is `74082882` (#347). *(2026-08-05 measurement —
stale: as of 2026-08-09 `main` is `b09093a8` and the box sits at `affe0647`; see
the Board record below.)*

> **⚠️ CORRECTED 2026-08-05, same day.** This spec first claimed five PRs were
> stranded, "including #355, the OAuth authorize fix, which is why mailbox
> connection still fails for a second member." **That was wrong.** #354, #355 and
> #356 are all **live**: the deploy does `git reset --hard origin/main`, so #347's
> successful 04:40 run carried everything merged before it — and those three had
> merged by 01:18. Only what merged *after* 04:40 was stranded.
>
> The error came from reading the box's HEAD (`#347`'s merge commit) as if delivery
> were PR-by-PR. It is not: **one successful deploy lands every commit merged up to
> that instant**, so "the box is on PR n" says nothing about PR n+1 — only about
> *when* the last success ran. Verified per PR with `git log --grep`, and by the
> BFF OAuth route existing on disk dated 04:42.

**Actually stranded: #357 and #358 — eight files, all documentation plus
`scripts/backup_db.sh`. Zero executable app code, zero migrations.** So the ~~broken~~
*(2026-08-05; recovered since — see header)* delivery path has had **no production
impact** to date. Its cost was entirely forward-looking — and the forward-looking cost
did not materialise: delivery recovered before any app change was stranded.

This spec owns the *delivery path* only: how a commit on `main` becomes running code
on the VPS. It does not own what the deploy script does once it runs
(`.github/workflows/deploy.yml` `DEPLOY_SCRIPT`), nor backup/restore
(`backup_and_restore.md`), nor identity (`user_management_contract.md`).

---

## 1. Scope and non-goals

**In scope:** the transport that triggers a deploy, and its failure modes.

**Non-goals:** changing deploy *steps*; migration policy; adding a staging
environment; CI test gating. Those are separate concerns and mixing them into this
change is how a transport fix becomes an outage.

---

## 2. The measurement

Deploy runs since 2026-08-04 (`gh run list --workflow deploy.yml`):

| Run | PR | Result | Duration |
|---|---|---|---|
| 30887875428 | #352 | success | 4m17s |
| 30920215465 | #353 | cancelled | 54m14s |
| 30924245024 | #354 | failure | 54m07s |
| 30965985816 | #355 | failure | 54m09s |
| 30965990418 | #356 | failure | 54m14s |
| 30975815621 | #347 | success | 4m05s |
| 30981508246 | #358 | failure | 54m04s |

Successes take ~4 minutes. Failures take ~54 — the retry ladder running to
exhaustion. The failures are intermittent, not a permanent block.

From the failing run's log:

```
ssh: connect to host ***: Connection timed out
not healthy yet (gateway_ok=0 workbench=000000, poll 24/24)
```

`000000` is curl's no-response code: the runner's **HTTPS** probe also got nothing.
So this is not SSH-specific.

**The box was healthy throughout.** During the 55-minute window 06:28–07:23 UTC,
`journalctl -u ssh` logged **four** lines total — one accepted key login from the
operator at 06:25, and two immediately-closed scans. Load average 0.16, uptime
7 days, no reboot. Simultaneously the box answered the operator's machine in 240 ms.

**Conclusion: GitHub's packets do not arrive.** The drop is upstream of the VPS and
affects every port. Nothing on the machine causes it — no fail2ban (not installed),
no iptables rules beyond UFW's own chains, no rate limiting.

Confirmed asymmetry — the box reaches GitHub *outbound* fine:

```
git ls-remote origin HEAD  -> d7d5c79b…   (instant)
curl https://api.github.com -> 200 in 0.029s
```

**Inbound is broken; outbound works.** Every option below follows from that one fact.
*(2026-08-05 measurement. By 2026-08-07 UTC inbound was reaching the box again — six
green runs — with one health-verify failure at tip; the fact this section rests on is
dated, and options A–C remain worth building for the next outage, not this one.)*

### 2.1 Why the existing retry logic cannot save this

`deploy.yml:546-559` already documents Hostinger network flakiness, but it models
the *wrong* failure: it assumes the deploy **ran** and only the SSH teardown flaked,
so it ignores the SSH exit code and verifies by health probe instead. That is a
sound design for a teardown blip. It is useless here, because the session never
establishes — the deploy genuinely never runs, and the health probe then fails from
the runner even though the app is serving users normally.

The retry ladder therefore turns a 4-minute no-op into a 54-minute no-op.

---

## 3. The structural obstacle

`DEPLOY_SCRIPT` is a **435-line shell script defined as a workflow `env:` value**
(`deploy.yml:107-544`) and piped over SSH with `bash -s`. **The box never holds a
copy.** It exists only inside the workflow run.

Any pull-based scheme must therefore either duplicate those 435 lines on the box —
producing two deploy paths that silently drift, which is worse than the outage this
spec is fixing — or the script must first be extracted into a real file in the repo.

**D1 — extract `DEPLOY_SCRIPT` to a versioned file. ✅ DONE — `scripts/vps_apply.sh`,
byte-identical (sha256 prefix `a779724d089319f6` before and after).** OWNER-GATE to
merge. This pays for itself regardless of which option below is chosen: a 437-line
script embedded in YAML cannot be shellchecked, cannot be run by hand during an
incident, and cannot be diffed meaningfully.

**Trap:** the script's first act is `git fetch && git reset --hard origin/main`
(`deploy.yml:117-118`). If the box runs the script *from the checkout*, the reset
rewrites the file while bash is still reading it — bash reads scripts incrementally
by byte offset, so this executes garbage. The extraction must be two-stage: a small
stable bootstrap that fetches, then `exec`s the fresh script.

⚠️ **"Executes garbage" was the optimistic guess. Measured 2026-08-08** — build a
throwaway origin with a 20 KB apply script whose first act resets its own
checkout, publish a second version, and run it both ways. The trap has *three*
outcomes and only one of them makes a noise:

| How the file is replaced | What bash does next | Exit |
|---|---|---|
| **rename** — what `git reset --hard` actually does | the open fd keeps the OLD inode; every step runs, but they are the **old script's** steps against the **new** tree | **0** |
| in-place rewrite, new file shorter | resumes past EOF — the remaining steps **silently do not happen** | **0** |
| in-place rewrite, bytes merely shifted | resumes mid-token: `--quiet` → `iet: command not found` | 127 |

So the failure git actually produces is the **quietest** one: exit 0, `HEAD`
correct, deploy steps stale. That is Defect 3 (§8.3) one level down — the tree
says it converged while the work never happened — and it is why no exit-code
check and no health probe can catch this. Only not running from the checkout can.

---

## 4. Options

### Option A — pull-based deploy timer (RECOMMENDED — **BUILT, see §8**)

A systemd timer on the box fetches a **`release` ref** and, when it differs from
local `HEAD`, applies it. Depends only on outbound git, which is proven working.

**Not `main`.** The deploy job runs only after `lint` and `test` pass; a poller
watching `main` would install commits whose tests failed — trading an outage for a
worse and quieter one. CI publishes `release` once the gates pass, so gating
survives the inversion and the box needs no GitHub credential to check it.
(Earlier drafts of this section said "polls `git ls-remote origin main`". That was
the naive version and it silently dropped CI gating; corrected when built.)

- **For:** no new inbound dependency; no daemon executing remote-authored jobs on
  the production host; short enough for the operator to read in full; survives
  GitHub Actions outages as well as this network fault.
- **Against:** deploys lag by the poll interval; loses the Actions log as the
  audit trail (mitigate: log to journald, which is where every other box-side unit
  already reports); requires D1 first.

### Option B — self-hosted GitHub runner on the box

The runner connects *outbound* to GitHub and long-polls for jobs, so inbound
reachability stops mattering. `deploy.yml` changes `runs-on` and replaces the SSH
invocation with local execution — roughly a one-line change to the deploy step, and
`DEPLOY_SCRIPT` stays as-is (D1 not strictly required).

- **For:** far less bespoke code; keeps the existing workflow, logs, and audit trail.
- **Against:** puts a job executor holding repo credentials on the production host.
  GitHub explicitly warns against self-hosted runners where untrusted code can reach
  them; that is acceptable only while this repo stays private and no forked PR can
  target the runner — a property that must then be *maintained*, not assumed.
- **Also:** the health verification would run *from the box*, so it can no longer
  prove the app is reachable from outside. That is a real loss of signal, and today
  it is unavoidable either way — GitHub cannot reach the box to check.

### Option C — Hostinger support ticket

The drop is in their network. Costs nothing to file in parallel, but nothing here
should wait on it: the same symptom recurred across two days and the workflow's own
comments show it predates this week.

**Recommendation: A, with C filed alongside.** B is the faster path and a defensible
choice if the operator would rather not maintain bespoke deploy code — but it trades
a network problem for a standing security property that has to hold forever.

---

## 5. Acceptance

| # | Done when | Gate |
|---|---|---|
| D1 | `DEPLOY_SCRIPT` lives in a versioned file; `deploy.yml` references it; a deploy runs green through the new path; the two-stage bootstrap is proven by deploying a commit that *modifies the deploy script itself* | OWNER-GATE |
| D2 | Chosen option installed; a push to `main` reaches the box with no human action; `git -C /opt/acb/app rev-parse HEAD` equals `origin/main` within the stated interval | OWNER-GATE |
| D3 | Failure is visible: a deploy that does not land raises something the operator sees, rather than a workflow that goes red where nobody looks | OWNER-GATE |
| D4 | The stranded commits are live: box `HEAD` == `d7d5c79b` or later, and `/health` answers 200. **Today this needs no deploy** — the eight files are documentation plus `scripts/backup_db.sh`, no service reads them at runtime, so a `git fetch && git reset --hard origin/main` in `/opt/acb/app` is sufficient and needs no restart (`agents.json` is clean; only untracked `models/` is present, which a reset leaves alone) | OWNER-GATE |

D3 is not optional. The reason this ran for two days is that the only signal was a
red tick on a page nobody was watching, while the app stayed up and looked fine.

---

## 6. Stopgap — landing the five stranded PRs now

Independent of the options above, and the most urgent item here. The operator's own
machine reaches the box; only GitHub's runners cannot. So the existing deploy can be
driven by hand.

**Preconditions — both already true as of 2026-08-05 09:29:**
- a verified restorable backup exists (`live=228 restored=228`, `Result=success`)
- the nightly backup timer is installed and enabled

```bash
ssh acb@187.127.179.143
cd /opt/acb/app

# What is about to change:
git fetch origin main
git log --oneline HEAD..origin/main
git diff --stat HEAD origin/main -- infra/postgres/   # migrations that will apply
```

Then run the deploy exactly as CI would, so the box takes the same path it always
does rather than a hand-rolled variant:

```bash
gh run view 30975815621 --log | sed -n '/Pulling latest from origin/,/Deployment complete/p'
```

…or, more simply, re-run the workflow's script by copying `DEPLOY_SCRIPT` from
`.github/workflows/deploy.yml` to the box and running it under `bash`.

**This is OWNER-GATE.** It applies migrations forward-only and it ships auth
behaviour changes (#354, #355, #356). Rollback is the 09:29 dump, restored per
`backup_and_restore.md` §3 — which, unusually, has actually been tested.

---

## 7. Verification commands

```bash
# delivery works end to end
git -C /opt/acb/app -c safe.directory=/opt/acb/app rev-parse HEAD
git ls-remote https://github.com/Hathi-Labs/Metorite main

# the app is serving, from OUTSIDE the box
curl -s -o /dev/null -w '%{http_code}\n' https://api.metorite.com/health
curl -s -o /dev/null -w '%{http_code}\n' https://app.metorite.com

# recent deploy outcomes
gh run list --workflow deploy.yml --limit 10
```

### 7.1 `GET /version` — the deployed SHA, from outside the box

**Built 2026-08-25.** This discharges the "SHA-in-`/health`" item the board
record below has carried since 2026-08-09, as a **separate route** rather than
inside `/health`: a liveness probe that grows fields is a liveness probe that
monitoring starts parsing, and `/health` is deliberately minimal.

```bash
curl -s https://api.metorite.com/version
# {"sha":"5b1cdcca…","env":"prod"}
```

Compare against `git rev-parse origin/main` and the answer is one line instead
of an inference. **Unauthenticated**, matching `/health` — the moment the answer
is wanted is mid-deploy or mid-incident, often from a machine holding no
session, and an endpoint that needs a credential then is one that does not get
used. What it discloses is a hash of a private repository against which there is
no public changelog.

⚠️ **`sha` is `null`, never a placeholder**, when it cannot be determined.
"Cannot report a version" and "running a commit called unknown" are different
facts, and the person reading this output is verifying a deploy.

**Why this route exists at all, recorded because the gap was invisible until it
bit:** CLAUDE.md §3.8 requires delivery to be verified by evidence and names
*the deployed SHA* as one of three. That evidence was not obtainable. On
2026-08-25 the question "is production on the latest code?" was answerable only
by recognising a known icon bug in a screenshot of the running app — the box
was serving pre-`3b6d3b42` assets and nothing it exposed could say so. Route
probing could not substitute: middleware redirects every unauthenticated path to
`/signin`, so a route that does not exist is indistinguishable from one that
does.

**It speaks for the frontend too.** The deploy builds the gateway and the
workbench from one checkout, so one SHA covers both — which is the case that
matters, since the bug that prompted this was a static asset. A version endpoint
answering only for the API would have reported "current" while stale icons were
still being served.

⚠️ **Still missing, and it is the other half of §3.8's evidence:** the migration
ledger is not exposed anywhere. `SELECT max(filename) FROM schema_migrations` on
the box remains the only way to confirm migrations actually applied, and it needs
box access. `/version` proves which code is running; it says nothing about
whether the schema moved with it. Worth closing before the next large batch —
there are 19 migrations newer than 170 on `main` as of 2026-08-25.

---

## 8. Option A as built

Three parts. The first two are in the repo; the third is an owner install.

### 8.1 `scripts/vps_apply.sh` — D1, the extraction

The 437-line script moved out of `deploy.yml`'s `env:` block into a versioned
file, **byte-identical** (sha256 prefix `a779724d089319f6` before and after — a
move, not a rewrite). `deploy.yml` gained `actions/checkout` and now does
`cp scripts/vps_apply.sh /tmp/deploy_remote.sh`; everything downstream is
unchanged.

This is what makes one script serve both delivery paths. A poller that carried
its own copy would drift from the workflow's, and the drift would only surface
during an incident.

**Amended 2026-08-08 — the byte-identical move left the file unshellcheckable.**
Line 1 was `set -e`, because a YAML `env:` value fed to `bash -s` has no shell to
declare. With no shebang and no `shell` directive, `shellcheck scripts/vps_apply.sh`
refuses to analyse the file at all — SC2148, *error* level, exit 1, nothing
checked — so half of what D1 was for was not actually delivered. Added
`#!/usr/bin/env bash` plus the WHY header; the line is inert on both delivery
paths (each names the interpreter, so `#!` is a comment) and buys the analysis
for no behaviour change. One real finding then fell out and is fixed: SC2046 at
the healthcheck wait loop, `[ $(date +%s) -lt $deadline ]` unquoted.
**`shellcheck scripts/vps_apply.sh` and `shellcheck scripts/vps_pull.sh` are now
clean at default severity, invoked with no flags.**

The file stays **non-executable** (0644, matching `vps_pull.sh`): nothing execs
it by path, and a `+x` bit would advertise a fourth way to start a deploy that
no delivery path uses. Hand-run it as
`cd /opt/acb/app && APP_DIR=/opt/acb/app bash scripts/vps_apply.sh` — but see
§3's table first, and copy it out of the object database before you do.

### 8.2 `scripts/vps_pull.sh` — the poller

Three decisions in it are load-bearing:

**It polls `release`, never `main`.** The deploy job runs only after `lint` and
`test` pass; a poller watching `main` would install commits whose tests failed,
trading an outage for a worse and quieter one. So `publish-release` fast-forwards
a `release` ref once the gates pass, and the box applies only that.

**`publish-release` is gated on lint+test and NOT on the deploy job.** This looks
wrong until you remember the failure: when GitHub cannot reach the VPS the deploy
job *fails*. Gating the ref on deploy success would withhold it exactly when the
box's own pull is the only path left. `release` asserts "this commit passed the
gates and is safe to install", not "GitHub managed to install it".

**Two-stage bootstrap.** `vps_apply.sh`'s first act is to synchronise the
checkout, which rewrites files under `$APP_DIR` — including `scripts/`. bash
reads a script incrementally by byte offset, so a script that rewrites itself
mid-run executes garbage from that offset on. The poller therefore reads the
target's copy out of the object database with `git show "$TARGET:…"`, which does
not touch the working tree, and runs it from a temp path nothing is about to
overwrite.

Also: `flock` so a slow apply cannot have the next tick start a second one;
`safe.directory` because the unit runs as root against an `acb`-owned checkout;
and a non-zero exit on failure so `systemctl --failed` shows it.

**`paths-ignore` was removed from `deploy.yml`** in the same change. It skipped
the whole workflow for `**.md` and `project-docs/**` — and a workflow-level
path filter skips the jobs too, so a docs-only merge would never move `release`
and the box would never converge. It also made this outage harder to see: #357
was documentation-only, so **no run was ever queued for it**, and "no failed run"
read as "nothing to do" rather than "never attempted".

### 8.3 The timer — OWNER-GATE to install

⚠️ **The first version of this unit was wrong twice, and both failures only
appeared on a real run.** Corrected version below; the two defects are recorded
because each looks fine on the page.

**Defect 1 — it could not bootstrap.** `ExecStart` installed the poller *from the
checkout*: `install /opt/acb/app/scripts/vps_pull.sh …`. A box older than the
poller has no such file — it arrives only via the update the poller is supposed
to perform. First run: `install: cannot stat … No such file or directory`. The
fix reads the script from `origin/release` with `git show`, which needs nothing
in the working tree and *also* solves the self-rewrite problem the original
comment was worried about.

**Defect 2 — `User=root` breaks git auth.** Second run:
`Host key verification failed`. The remote is `git@github.com:…`; the deploy key
and `known_hosts` belong to `acb`, and root has neither. The push path has always
run this script as `ssh acb@host 'bash -s'`, and the applied script calls `sudo`
~30 times — it is written to *start unprivileged and elevate*. `acb` has
passwordless sudo. So `User=acb` is not a workaround; running it as root was the
deviation.

⚠️ **Corrections exist on paper until they are installed.** The corrected unit
below sat in this spec while the box kept running the `User=root` version — the
poller failed every five minutes from its first tick until 2026-08-06, when the
WS-26 activation found it. `.claude/` and `/etc/systemd/system/` share the same
failure mode: not in the repo, so no PR can fix them; every correction here is
also a hand-carried box change.

**Defect 3 (2026-08-06) — the SHA can be current while the APPLY never
happened.** The poller skips when `HEAD == origin/release`. But the push path's
`git reset --hard` moves HEAD *before* migrations and restarts — so when the
push-path SSH session was killed mid-apply (Defect 4b), the tree said `8d83ca10`
while the DB had no migration 144/145 and both services still ran old code. The
poller then reported `already current` and stood down: both delivery paths
converged on believing a deploy that had not happened. `--force` is the manual
escape; the durable fix would be gating the skip on the `/var/lib/acb`
last-success marker (recorded at the END of an apply), not on git state.

**Defect 4 (2026-08-06) — two `deploy.yml` failures, one green run.**
(a) `publish-release` used the default depth-1 checkout; `git push` proves
fast-forward client-side, so every publish after the ref-CREATING one was
rejected with "fetch first" — `release` sat at #360 for three merges. Fixed:
`fetch-depth: 0`. (b) `ssh_deploy`'s `timeout 900` is shorter than the
pre-migration backup alone (~11 min on the 4GB box); the session was killed
mid-apply and `verify()` blessed the still-healthy OLD deployment. Fixed:
`timeout 1800`, matched to the pull unit's `TimeoutStartSec`. Residual hole,
accepted and named: health-verify cannot distinguish "deploy succeeded" from
"deploy never finished but yesterday's app is healthy" — only an
identity-bearing health signal (commit SHA in `/health`) closes it.

```ini
# /etc/systemd/system/acb-pull.service
[Unit]
Description=Metorite pull-based delivery (apply origin/release)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# NOT root — see Defect 2 above.
User=acb
# Read from origin/release, not the working tree — see Defects 1 and 2.
ExecStart=/bin/bash -c 'set -e; cd /opt/acb/app; git fetch -q origin release; git show origin/release:scripts/vps_pull.sh > /tmp/acb-pull-run.sh; chmod 700 /tmp/acb-pull-run.sh; exec /tmp/acb-pull-run.sh'
TimeoutStartSec=1800
```

The state directory must exist and be writable by `acb`, or the poller silently
skips its last-success marker (it degrades with `|| true` rather than failing):

```bash
sudo mkdir -p /var/lib/acb && sudo chown acb:acb /var/lib/acb
```

```ini
# /etc/systemd/system/acb-pull.timer
[Unit]
Description=Poll for released Metorite commits

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s
Unit=acb-pull.service

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now acb-pull.timer
sudo systemctl start acb-pull.service    # prove it now — see BO-23's lesson
journalctl -u acb-pull -n 40 --no-pager
```

⚠️ **Start it by hand before trusting the timer.** BO-23's timer was installed,
looked correct, and failed on its first real run for a reason no amount of
reading would have found. A timer that has never fired is not a schedule.

### 8.4 What is still unverified

`vps_apply.sh` cannot be proven by the push path while the network fault
persists — but it does not need to be: **the poller is what verifies it.** The
first successful `acb-pull.service` run exercises the identical file the
workflow would have piped.

Until `publish-release` runs once, `origin/release` does not exist and the poller
exits 1 saying so. That is the intended first-run state, not a fault.

---

## 9. Related

- `backup_and_restore.md` — the rollback path this spec's stopgap depends on
- `user_management_contract.md` — what #354/#355/#356 change, and why shipping them
  matters beyond "the board is out of date"
- `colleague_onboarding.md` §2 — blocked at its final step until D4 lands

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-25 — **Deploy delivery path** — getting merged code onto the box *(minted 2026-08-05)*
**State cell (as of the move):** 🔴 **BROKEN — but no production impact to date**
**Narrative (verbatim):** **`main` is `d7d5c79b`; the box is `74082882` (#347).** ⚠️ **This row first claimed five PRs were stranded "including #355, the OAuth authorize fix" — CORRECTED the same day: #354, #355 and #356 are all LIVE.** The deploy does `git reset --hard origin/main`, so #347's successful 04:40 run carried everything merged before it, and those three merged by 01:18. **The error was reading box HEAD as if delivery were PR-by-PR — it is not: one successful deploy lands every commit merged up to that instant, so "the box is on PR n" says nothing about PR n+1, only about when the last success ran.** Verified per PR with `git log --grep` and by the BFF OAuth route on disk dated 04:42. **Actually stranded: #357 + #358 — eight files, all documentation plus `scripts/backup_db.sh`; zero executable app code, zero migrations, and nothing reads them at runtime**, so today's remediation is a `git fetch && git reset --hard origin/main` with **no deploy and no restart**, not the §6 stopgap. **The cost of this defect is therefore entirely forward-looking: the next app change to merge will not ship, and nothing will say so.** **Measured 2026-08-05:** deploy runs since 2026-08-04 alternate ~4-minute successes with **~54-minute failures** (the retry ladder running to exhaustion) — `ssh: connect to host ***: Connection timed out`, and `workbench=000000`, curl's no-response code, so the runner's **HTTPS** probe got nothing either. **The box was healthy the whole time:** across the 55-minute window 06:28–07:23 UTC `journalctl -u ssh` logged **four** lines — one operator key login and two immediately-closed scans — at load average 0.16, uptime 7 days, no reboot, while answering the operator's machine in 240 ms. No fail2ban (not installed), no iptables rules beyond UFW's own chains. **GitHub's packets do not arrive; the drop is upstream of the VPS and affects every port.** The asymmetry is the whole design input: the box reaches GitHub **outbound** fine (`git ls-remote` instant, `api.github.com` 200 in 29 ms). ⚠️ **`deploy.yml:546-559`'s existing retry logic cannot save this — it models the wrong failure.** It assumes the deploy *ran* and only the SSH teardown flaked, so it ignores the SSH exit code and verifies by health probe; sound for a teardown blip, useless when the session never establishes, and it converts a 4-minute no-op into a 54-minute one. ⚠️ **The structural obstacle, and why the obvious fix is a trap: `DEPLOY_SCRIPT` is a 435-line shell script defined as a workflow `env:` value (`deploy.yml:107-544`) and piped over SSH with `bash -s` — the box never holds a copy.** So a pull-based scheme must either duplicate 435 lines on the box, producing two deploy paths that silently drift (worse than the outage), or the script must first be extracted to a versioned file (**D1**) — which pays for itself anyway, since a script embedded in YAML cannot be shellchecked, hand-run during an incident, or diffed. **Second-order trap recorded in the spec §3:** the script's first act is `git fetch && git reset --hard origin/main`, so a box running it *from the checkout* has the file rewritten while bash is still reading it by byte offset; extraction must be two-stage — a small stable bootstrap that fetches, then `exec`s the fresh script. **Options in spec §4, recommendation A:** (A) a pull timer polling `git ls-remote`, depending only on the outbound path that is proven working, no daemon executing remote-authored jobs on the production host; (B) a self-hosted GitHub runner — far less bespoke code and keeps the Actions audit trail, but puts a job executor holding repo credentials on the prod box, acceptable only while the repo stays private and no forked PR can target it, a property that must then be *maintained*; (C) a Hostinger ticket, worth filing in parallel, worth waiting on for nothing. Under **both** A and B the health check can no longer prove external reachability — unavoidable today, since GitHub cannot reach the box to check. **D3 (failure is visible) is not optional:** this ran two days because the only signal was a red tick on a page nobody watches while the app stayed up and looked fine. **All four acceptance items are OWNER-GATE** (they change the deploy path and apply migrations forward-only). **§6 stopgap: the operator's own machine reaches the box, so the existing deploy can be driven by hand** — and the preconditions are already true as of 2026-08-05 09:29 (a verified restorable backup, `live=228 restored=228`, plus the nightly timer installed and enabled), which makes this the safest moment this deployment has had for it. **Blocks:** §6's `GATEWAY_INTERNAL_TOKEN` rotation, whose prescribed method *is* a redeploy — rotating before delivery works writes the new value into `.env` with no reconcile of `.env.local`, the exact lockout that item warns about; and `colleague_onboarding.md` §2's final step.

**Corrections applied 2026-08-09:**
- the 🔴 BROKEN state is superseded by re-measurement 2026-08-09: deploys landing since 2026-08-06 (migs 144/145 applied on prod), six green runs on 2026-08-07 UTC alone, the last = #400's log-verified deploy 31217978773 (2026-08-08 IST; c1eba71f fixed the apply script git-resetting itself mid-read)
- the tip run (b09093a8, docs-only) failed health-verify ×3 rounds 21:21→22:16 UTC 2026-08-07 — box at affe0647, cause unresolved
- the row's main/box SHA pointers are stale
- D1 (extract DEPLOY_SCRIPT), SHA-in-/health and failure-visibility remain live
- under D15 delivery must become placement-parameterised (saas_multitenancy.md §5.1 condition 3).
