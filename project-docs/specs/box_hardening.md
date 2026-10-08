# Box hardening — an app compromise must not become root

**Status: ACTIVE, specified 2026-10-08, nothing built.** Board row
**WS-49**. The owner ruled on 2026-10-08 to close this gap first, before the
data of the beta customers arrives. This spec owns H-270 and H-271 in
`HANDOFF.md`.

Verified against code on 2026-10-08 at `main` `50ea81cbb`. Verified against
the box `srv1914284` on 2026-10-08 between 16:15 and 16:40 UTC, with
read-only commands. Every file path and line in this spec was read at that
commit. Re-verify each anchor at dispatch, because the code is the fact.

**Ids in this spec.** The decisions are BH-D1 to BH-D9. The slices are BH-1
to BH-8, one PR each. The fences are BH-F1 to BH-F7. Outside this file,
write `WS-49 BH-2`, so that no id collides with an id of another spec (R2).

**Owner of what.** This spec owns five things. They are the env of every
child process, the systemd hardening of the units, and the Docker access of
the app user. They are also the off-box backup credential and the sudo
rights of `acb`.
`backup_and_restore.md` §4.2 keeps the backup run itself.
`maf_coding_engine.md` keeps the sandbox broker design, and its gate WS43-G1
("change the box's Docker access") is built by BH-3 here.

---

## 0. The answer in one screen

The app user `acb` can become root in **five** ways, not three. H-271 named
three. The audit found two more, and one of them takes 10 minutes:

| # | Path from the gateway to root | Measured | Closed by |
|---|---|---|---|
| P1 | `sudo` with no password | `sudo -l -U acb` shows `(ALL) NOPASSWD: ALL` and `(ALL : ALL) ALL` | BH-2 (NoNewPrivileges), BH-5 |
| P2 | The `docker` group | the gateway PID runs with supplementary groups `27,100,988,1000`. 988 is `docker` | BH-2 (socket hidden), BH-3 |
| P3 | A root unit runs a file that `acb` can write | `acb-health-watchdog.service` (root, every 10 min) runs `/opt/acb/app/deploy/hostinger/health-watchdog.sh`. `acb-backup.service` and `acb.service` (both root) load `/opt/acb/app/.env`, which the gateway writes at run time | BH-2 (scripts read-only), BH-6 (.env) |
| P4 | The user systemd manager | `/run/user/1000` exists while any `acb` SSH session is open. `systemd-run --user` starts a process that is not a child of the gateway, so NoNewPrivileges does not reach it | BH-2 (`InaccessiblePaths=/run/user`) |
| P5 | Plant code that a sudo-capable `acb` process runs later | `acb-pull` runs as `acb` every 5 min, with sudo. It runs `uv run` from `/opt/acb/app/.venv`, which the gateway writes (agent installs) | BH-7, BH-5 |

**The H-271 "cheap partial fix" is weaker than it says.** NoNewPrivileges on
the gateway alone stops P1 only for a child that calls `sudo` directly. P3,
P4 and P5 go around it. So BH-2 is a full sandbox, not one line.

**The secrets path (H-270) is real and wider.** The Copilot CLI inherits the
whole gateway env. So do 47 other spawn calls in the gateway, the
orchestrator and `packages/` (§2.2). BH-1 gives all of them one allowlisted
env, from one helper.

**The off-box credential can be write-only.** This is BH-4. Supabase Storage
takes a user JWT as an S3 session token, and RLS then applies. Give that
user INSERT and SELECT, with no UPDATE and no DELETE. Then the box can add a
night, and it cannot delete or overwrite one. The project has pg_cron and
pg_net, so retention runs on the Supabase side.

**One finding outside the brief (BH-8).** Neo4j answers on the public
internet. `http://187.127.172.200:7474/` returned HTTP 200 from outside the
box, and TCP port 7687 accepted a connection. Docker publishes
`0.0.0.0:7474` and `0.0.0.0:7687`, and Docker rules bypass `ufw`.

**The order.** BH-1, BH-2, BH-6 and BH-8 first. They are cheap, they need no
owner money, and together they close P1 to P4 for the gateway. Then BH-3,
BH-7 and BH-4. BH-5 is recorded for later.

---

## 1. The problem

The gateway reads email, documents and web pages for a member. A prompt
injection in that content can steer an agent. The Copilot CLI agent has a
shell tool, and that tool runs as a child of the gateway, as `acb`.

From that shell, an attacker can do three things today:

1. Read every secret in the gateway env. That holds the 162 keys of
   `/opt/acb/app/.env` on the box. It also holds each provider key that the
   credential store loads into `os.environ` at start.
2. Become root, by any of P1 to P5.
3. As root, read `/etc/acb/backup-offbox.env` and use its project-wide S3 key
   to delete every off-box night. Then the attacker can damage the database,
   and no backup remains.

Item 3 is not live yet. `/etc/acb` does not exist on the box (measured), so
the off-box copy of H-123 is not on. The gap opens on the day the owner does
the H-123 steps. BH-4 must land before, or with, that day.

---

## 2. Measured facts — the audit

### 2.1 What the gateway process needs

**Identity.** `id acb` gives
`uid=1000(acb) gid=1000(acb) groups=1000(acb),27(sudo),100(users),988(docker)`.
`getent group docker` gives `docker:x:988:acb`. The socket is
`srw-rw---- root docker /run/docker.sock`. The daemon runs with
`-H fd://` only, and `DOCKER_HOST` is not set in `.env`.

**The gateway processes.** `ps -o user,group,supgid` shows PID 2403959
(`uv run uvicorn gateway.main:app`) and its child PID 2403965 (`python …
uvicorn`), both `acb acb 27,100,988,1000`. A Copilot CLI child inherits the
same groups. No CLI process ran at the time of the check.
`systemctl show acb-gateway` gives `NoNewPrivileges=no`, `ProtectSystem=no`
and an empty `SupplementaryGroups=`, so the groups come from `/etc/group`.

**The exposure score.** `systemd-analyze security` gives **9.2 UNSAFE** for
each of acb-gateway, acb-workbench, acb-customer-console,
acb-operator-console, acb-smoke-chat and acb-pull. The box runs systemd 255.

**Sudo.** No code under `apps/`, `packages/` or `workbench/*/src` calls
`sudo`, `systemctl`, `pkexec` or `setuid`. The only hits are prose
(`acb_llm/ride_through.py:45`, `mutation.py:892`, `runErrors.ts:6`,
`agents/page.tsx:1857`). `deploy/smoke_chat.sh` calls no `sudo`. So every
`User=acb` unit except `acb-pull` can take NoNewPrivileges.

**Docker.** The gateway runs Docker in-process. There is no separate broker
unit.

- `gateway/main.py:153-178` starts the Copilot sandbox sweep and
  `start_sandbox_broker()` at every start.
- `orchestrator/sandbox_broker.py:880-900` (`DockerCLI`) runs the `docker`
  binary as a child of the gateway, with no `env=`.
- `copilot_sandbox.py:94` and `mutation.py:921,1004` run `docker` too.
- On the box, `MAF_CODING_SCOPE` and `SANDBOX_IMAGE` are set (a count of 1
  each, values not read). `COPILOT_SANDBOX_SCOPE` is not set.
- **No sandbox image exists on the box.** `docker images` lists only
  `pgvector:pg17`, `pgvector:pg16`, `acb-meeting-bot`, `neo4j:5-community`
  and `redis:7-alpine`. So the broker cannot start a container today, and to
  hide the socket from the gateway breaks no live feature.

**Where the gateway writes.** Measured on the box and in code:

| Path | Why | Source |
|---|---|---|
| `/opt/acb/app/.env` | Integrations, OAuth and Models routes rewrite it in place with `write_bytes` | `routes/integrations.py:533`, `routes/settings.py:186-214`, `acb_common/env_guard.py:1-20` |
| `/opt/acb/app/.venv` | Agent installs go into the shared venv | `acb_skills/loader.py:1137-1226`, `acb_skills/dep_tools.py:79` |
| `/opt/acb/app/data` | `gtd_attachments`, resumes | `ls -l /opt/acb/app/data` |
| `/home/acb/.acb/agents` | Agent clones, Custom Apps, broker state (74 MB) | `settings.py:589-606,726` |
| `/home/acb/.copilot`, `/home/acb/.cache/copilot`, `/home/acb/.cache/github-copilot-sdk` | The Copilot CLI state and binary 1.0.79 | `ls -ld` on the box |
| `/home/acb/.cache/uv` | `uv pip install` from the loader | `loader.py:1209` |

### 2.2 What the Copilot CLI needs from the env

**The launch.** `copilot_agent.py:208-216` reads `COPILOT_GITHUB_TOKEN`,
then `GITHUB_COPILOT_TOKEN`, then `GITHUB_TOKEN`. Lines 219-226 build
`client_options` with `github_token`, an optional `connection` and
`log_level`, and pass no `env`.

**The SDK** (`github-copilot-sdk` 1.0.11, `copilot/client.py`):

- `__init__` takes `env: dict[str, str] | None` (line 1428).
- Lines 1597-1601 pick the env by identity: the connection env, else
  `options.env`, else `os.environ`.
- `_start_cli_server` (lines 4125-4135) copies that env, and then sets
  `COPILOT_SDK_AUTH_TOKEN` from `github_token`. Line 4095 passes
  `--auth-token-env COPILOT_SDK_AUTH_TOKEN`. So the CLI needs no
  `GITHUB_TOKEN`, `GH_TOKEN` or `COPILOT_GITHUB_TOKEN` in its env.
- The SDK also sets `COPILOT_CONNECTION_TOKEN`, `COPILOT_HOME` (from
  `base_directory`) and the `OTEL_*` names, when they apply.
- The CLI binary lives at `~/.cache/github-copilot-sdk/cli/<version>/copilot`
  (`_cli_download.py:57-87`). It uses `HOME` or `XDG_CACHE_HOME` to find it.

**What the CLI reads.** We read the bundle of CLI 1.0.66 (`app.js`) on the
dev box. These are the env names in it that matter here:

- `PATH`, `HOME`, `USER`, `SHELL` and `TERM`.
- `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_RUNTIME_DIR`.
- `COPILOT_HOME`, `COPILOT_CACHE_HOME`, `HTTPS_PROXY` and `HTTP_PROXY`.
- The token names above, and many `COPILOT_*` debug names.

The box runs 1.0.79. Re-read the list at dispatch.

**The minimal allowlist (BH-D2).** `PATH`, `HOME`, `USER`, `LOGNAME`,
`LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `TMPDIR`, `XDG_CACHE_HOME`,
`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `HTTPS_PROXY`,
`HTTP_PROXY`, `NO_PROXY` and their lower-case forms, `SSL_CERT_FILE`,
`SSL_CERT_DIR` and `NODE_EXTRA_CA_CERTS`. On Windows dev boxes, also
`SYSTEMROOT`, `WINDIR`, `TEMP`, `TMP`, `PATHEXT` and `COMSPEC`. Each name
passes only when it is set. No proxy name is set in `.env` on the box
(measured: a count of 0 for each).

**The residual of BH-1.** The CLI still holds `COPILOT_SDK_AUTH_TOKEN`,
because it needs it. A shell under the CLI can read that token. On the box,
`GITHUB_TOKEN` is not in `.env`, so the token comes from the credential store
at run time. Its scope is the blast radius. D92 removes the Copilot SDK, and
that closes this residual.

**Every other spawn.** An AST scan of `apps/` and `packages/` (tests out)
found 62 spawn calls. 9 pass `env=`, and 53 do not. Of the 53, 45 run in the
gateway, and 8 run in a container. The 53 that inherit the full env:

| File | Lines | What it runs | Where it runs |
|---|---|---|---|
| `gateway/routes/agent.py` | 1465, 1491, 1507, 3163, 3180, 3194, 3332, 3435, 3529, 3679, 3691, 3710, 3740, 3762, 3840, 3861 | `git` | gateway |
| `gateway/routes/apps/durability.py` | 294 | `git` | gateway |
| `gateway/routes/apps/lifecycle.py` | 171 | `args` | gateway |
| `gateway/routes/integrations.py` | 2304, 2346, 2370 | `gh auth` | gateway (`gh` is not installed on the box) |
| `gateway/routes/monorepo_pr.py` | 120 | `*args` | gateway |
| `orchestrator/executor.py` | 1951, 1972, 2137, 2158, 2189 | `git` | gateway |
| `orchestrator/mutation.py` | 193, 739, 921, 1004, 1022 | `git`, `docker` | gateway |
| `orchestrator/copilot_sandbox.py` | 94 | `docker` | gateway |
| `orchestrator/sandbox_broker.py` | 894 | `docker` | gateway |
| `acb_skills/code_tools.py` | 320 | `git` | gateway |
| `acb_skills/dep_tools.py` | 86 | `uv pip install` | gateway |
| `acb_skills/error_tools.py` | 96, 120 | `python -m`, `ruff` | gateway |
| `acb_skills/loader.py` | 136, 140, 148, 154, 1226 | `git`, `uv pip install` | gateway |
| `acb_stt/local_diarization.py` | 82 | `ffmpeg` | gateway |
| `orchestrator/mutation_runner.py` | 171 | `git` | mutation container |
| `orchestrator/sandbox/data_engine.py` | 2690, 2728 | `taskkill`, `argv` | sandbox container |
| `meeting_bot/app/live.py`, `meet.py` | 115, 242, 548, 558, 477 | `ffmpeg`, `paplay`, shell | meeting-bot container |

Two more sites pass a copy of the full env with one change, which is the
same leak: `routes/apps/files.py:215-222` (`dict(os.environ)`) and
`routes/agent.py:3794` (`{**os.environ, …}`). Three sites already allowlist,
each in its own way: `pdf_render.py:666-696` (`CHILD_ENV_KEYS`),
`code_tools.py:62-100` (`_ENV_ALLOW` and `_script_env`) and
`loader.py:91` (`_GIT_ENV`). That is three rules for one job. BH-1 makes one.

**The other in-process Copilot clients.** `gateway/main.py:134` and
`main.py:1915` build a `CopilotClient` to list models, with no `env`. The
wrapper `agent_framework_github_copilot/_agent.py:772` builds its own client
when `MetoriteCopilotAgent.start` set none (no token). All three spawn the
CLI with the full env.

**Git authentication.** The `git push` sites pass no token in the env. The
clone's `.git/config` holds it (`maf_coding_engine.md` WS43-S6). So `git`
needs only the base allowlist.

### 2.3 Which units load `/opt/acb/app/.env`

`systemctl cat` on the box, comments removed:

| Unit | User | EnvironmentFile | Writes | Runs | State |
|---|---|---|---|---|---|
| `acb-gateway` | acb | `/opt/acb/app/.env` | §2.1 table | `uv run uvicorn`, git, docker, the Copilot CLI | enabled, running |
| `acb-whatsapp-bridge` | acb | `/opt/acb/app/.env` | its `StateDirectory` and `/opt/acb/data/whatsapp_bridge` (absent) | one Go binary | **disabled** |
| `acb.service` | **root** | `/opt/acb/app/.env` | Docker state | `docker compose -f infra/docker-compose.yml` from the checkout | enabled |
| `acb-backup` | **root** | `/opt/acb/app/.env`, the Console `.env`, `/etc/acb/backup-offbox.env` | `/opt/acb/backups` | `/opt/acb/app/scripts/backup_db.sh` (acb, 0775) | timer 02:30 |
| `acb-workbench` | acb | `workbench/control_plane/.env.local` | `.next/cache` | `npm start` | running |
| `acb-operator-console` | acb | `workbench/operator_console/.env.local` | `.next/cache` | `npm start` | running, **not in the repo** |
| `acb-customer-console` | acb | `customer_console/.env` | nothing measured | `uv run uvicorn` | running |
| `acb-pull` | acb | none | the checkout, `/var/lib/acb` | `scripts/vps_pull.sh`, which runs `vps_apply.sh` with sudo | timer 5 min |
| `acb-smoke-chat` | acb | none | nothing measured | `deploy/smoke_chat.sh` | timer 6 h |
| `acb-health-watchdog` | **root** | none | logs | `/opt/acb/app/deploy/hostinger/health-watchdog.sh` (acb-owned) | timer 10 min |

There is no orchestrator unit and no agents unit. The orchestrator and the
agents run inside acb-gateway.

`acb-operator-console.service` was written by hand on 2026-08-23 and is not
in `deploy/hostinger/`. A unit that the repo does not hold is a unit that
no fence reads. BH-2 brings it into the repo.

**What each unit can take.**

| Unit | NoNewPrivileges | PrivateTmp | ProtectSystem=strict | ProtectHome | RestrictSUIDSGID, CapabilityBoundingSet= |
|---|---|---|---|---|---|
| acb-gateway | yes | yes | yes, with the §2.1 write list | read-only, with 4 write paths | yes |
| acb-whatsapp-bridge | yes | yes | yes (`StateDirectory` only) | yes | yes |
| acb-workbench, acb-operator-console | yes | yes | yes, with `.next/cache` writable | read-only | yes |
| acb-customer-console | yes | yes | yes | read-only | yes |
| acb-smoke-chat | yes | yes | not measured, so not in BH-2 | not in BH-2 | yes |
| acb-pull | **no** — it needs sudo | no | no | no | no |
| acb, acb-backup, acb-health-watchdog | root units, BH-6 | — | — | — | — |

### 2.4 The deploy path's use of sudo and Docker

`deploy.yml:321-322` connects as `acb` and runs `vps_apply.sh` with
`bash -s`. `acb-pull.service` runs as `acb` (not root), and runs
`vps_pull.sh`, which runs `vps_apply.sh` (`acb-pull.service` lines 37-38).
`vps_apply.sh` holds 56 `sudo` lines:

| Need | `vps_apply.sh` lines |
|---|---|
| Repair ownership: `find`, `chown -R` of `.venv`, `node_modules` and build trees | 434, 729, 1245, 1286, 1289 |
| Run as the service user: `sudo -u acb -H uv run … copilot download-runtime` | 755 |
| Packages: `apt-get update`, `apt-get install` of ffmpeg, rclone, gnupg, zstd | 782, 1533, 1539 |
| Go toolchain: `rm -rf /usr/local/go`, `tar -C /usr/local` | 853 |
| Unit files: `cp` and `install -m 0644` into `/etc/systemd/system` | 857, 1065, 1066, 1502, 1503, 1563, 1564 |
| `systemctl daemon-reload` | 858, 1067, 1504, 1570 |
| `systemctl enable`, `restart`, `disable --now` of acb-whatsapp-bridge, caddy, acb-gateway, acb-customer-console, acb-workbench, acb-operator-console and every `acb-*.timer` | 859, 860, 872, 1023, 1026, 1045, 1068, 1069, 1089, 1373, 1378, 1420, 1484, 1505, 1591 |
| Logs: `journalctl -u`, `systemctl status` | 1041, 1096, 1424, 1431, 1488, 1511 |
| Caddy: `caddy validate`, `cmp`, `test -e`, `cp -a`, `rm -f`, `install` into `/etc/caddy` | 993, 995, 1001, 1004, 1008, 1016, 1019, 1044 |
| Remove a tree: `rm -rf` | 1195 |
| `chmod 0755` of the watchdog script | 1501 |

Docker calls as `acb`, through the group, not sudo:

| Script | Lines | Calls |
|---|---|---|
| `scripts/vps_apply.sh` | 544, 549, 554 | `docker compose up` (core), `docker ps` |
| `scripts/vps_apply.sh` | 936, 937, 942, 951, 961 | `docker images`, `docker compose build/up` (meetingbot), `docker logs` |
| `scripts/apply_migrations.sh` | 73, 77 | `docker exec`, `docker ps` (only when `PG_MODE=docker`) |
| `deploy/hostinger/deploy.sh` | 21, 66, 71, 76 | `docker compose pull/up`, `docker ps` (the old path) |

Docker calls as root: `scripts/backup_db.sh` 144-145, 154, 463-617 (the
verify container) and `scripts/backup_offbox.sh:197`.

`deploy/hostinger/deploy.sh:65` runs `set -a && source /opt/acb/app/.env`.
A line in `.env` is then shell code that runs as `acb` with sudo.

### 2.5 Can the off-box credential be write-only

**Yes.** Sources: the Supabase docs pages "S3 Authentication" and "Storage
Access Control", read through the Supabase MCP `search_docs` on 2026-10-08.

- **The session token.** The access key ID is the project ref, the secret is
  the anon key, and the session token is a user JWT. The docs say that
  Storage then acts as that user, and applies the RLS policies of the
  storage schema. They also say that the
  publishable key is "not yet supported" there. So the legacy anon key is
  needed.
- **The project-wide key.** The docs say that an S3 access key gives full
  access to every bucket and bypasses RLS. That is the key that H-123 puts on
  the box today.
- **Overwrite.** The docs say that an overwrite with `upsert` needs `SELECT`
  and `UPDATE`. So with no UPDATE policy, a PUT to a key that exists fails.
- **Delete.** A delete needs a `delete` policy. With none, it fails.
- **rclone.** The box has `rclone 1.60.1+dfsg-3ubuntu0.24.04.6`
  (`dpkg-query`, Ubuntu 24.04.4). The rclone S3 backend has a
  `session_token` option, so `RCLONE_CONFIG_OFFBOX_SESSION_TOKEN` works next
  to the four names that `offbox_lib.sh:94-97` sets.
- **A fresh JWT.** The box asks `POST /auth/v1/token?grant_type=password`
  with the anon key as `apikey`, just before the upload. The project JWT
  expiry (default 3600 s) is longer than the unit budget (1800 s,
  `acb-backup.service` `TimeoutStartSec`). The password of the writer user
  lives in `/etc/acb/backup-offbox.env`. That user can only add objects, so
  the stored password costs little.
- **Retention without the box.** `list_extensions` on project
  `wbjpwtxigkileyjsgahk` shows `pg_cron` 1.6.4 and `pg_net` 0.20.4. Both are
  available and not installed. `supabase_vault` 0.3.1 is installed. The docs
  say to delete objects through the Storage API and never with SQL, because
  SQL leaves the file behind. So an Edge Function with the service role does
  the delete, and pg_cron with pg_net calls it. The Supabase MCP in this
  session has `deploy_edge_function`.
- **Two traps that the design must close.** (a) An attacker with the writer
  credential can add fake nights. A rule that keeps "the newest N nights"
  then deletes the real ones. So retention goes by server age only. (b) An
  attacker can add an object at tomorrow's key. With no UPDATE, tomorrow's
  real upload then fails. So each night's prefix holds a random part that
  the attacker cannot guess.
- **What the box does today that BH-4 removes.** `backup_offbox.sh:230` lists
  the night (`lsf`, needs SELECT), and `offbox_lib.sh:217` deletes old nights
  (`rclone delete`, needs DELETE). The delete moves to the server.

---

## 3. Scope and non-goals

**In scope.**

- The env of every child process that the gateway starts.
- The systemd units in `deploy/hostinger/`, plus the operator console unit.
- The Docker access of the gateway.
- The root units that read files that `acb` can write.
- The off-box backup credential and its retention.
- The public ports of the compose stack.
- The sudo rights of `acb` (recorded only, BH-5).

**Non-goals.**

- Removing the Copilot SDK. D92 and `maf_coding_engine.md` §15-§17 own it.
  BH-1 closes the env leak until then.
- gVisor or rootless Docker for the sandbox containers (WS43-G5).
- Per-request provider credentials, in place of keys in `os.environ`
  (`work_plan.md` §6 (f)).
- A second backup provider with Object Lock. It is the fallback of BH-D7, and
  it needs the owner's money and account.
- Separate Unix users for the workbench, the consoles and the gateway. Today
  they share `acb`, so each one can read the others' `/proc/<pid>/environ`.
  systemd 255 has no `PrivatePIDs=`. BH-5 records the user split.
- The existing grandfathered findings of R6, R7 and R8 elsewhere in the tree.

---

## 4. Decisions

**BH-D1. One seam for the env of a child process.** A new module
`packages/acb_common/acb_common/child_env.py` holds `child_env()` and
`copilot_env()`. Every spawn in the gateway, the orchestrator and
`packages/` passes `env=` from it. The three existing allowlists
(`pdf_render.CHILD_ENV_KEYS`, `code_tools._ENV_ALLOW`, `loader._GIT_ENV`)
become callers of it. A second allowlist is a defect.

**BH-D2. The base allowlist is §2.2's list.** A caller adds a name only by
value, with `extra={"NAME": value}`, at the call site, in code that a
reviewer reads. `child_env()` never copies a name by pattern. Some names
hold `TOKEN`, `SECRET`, `KEY`, `PASSWORD`, `CREDENTIAL` or `DSN`. Such a
name enters only through `extra`, and only from a named list in the module.

**BH-D3. The Copilot CLI gets `copilot_env()`.** That is `child_env()` with
no token name, and the SDK adds `COPILOT_SDK_AUTH_TOKEN` itself. Every
`CopilotClient(` in our tree passes `env=copilot_env()`.

`MetoriteCopilotAgent.start` always builds the client, so the wrapper never
builds one with no env. With no token, it raises.

**BH-D4. The gateway runs in a systemd sandbox.** NoNewPrivileges by itself
is not enough (§0). The gateway gets the full set of §5 BH-2, and its write
list is the measured list of §2.1 and nothing more.

**BH-D5. The gateway cannot reach the Docker socket.** BH-2 hides the socket
from the gateway with `InaccessiblePaths=`. This costs no live feature,
because no sandbox image is on the box (§2.1). The WS-43 sandbox goes live
only after BH-3 gives the broker its own unit. That makes BH-3 a gate on
WS43-G1.

**BH-D6. A root unit runs only root-owned files.** No root unit loads an
`EnvironmentFile` that `acb` can write, and no root unit runs a script from
the checkout. The deploy installs a root-owned copy. A root unit reads the
keys it needs from a root-owned file that the deploy writes through a key
allowlist. No script runs `source` on `.env`.

**BH-D7. The off-box credential is write-only, and retention runs on the
server.** The design is §2.5's. If the drill of BH-4 shows that Supabase
cannot refuse the overwrite or the delete, BH-4 stops. The fallback is a
second provider with Object Lock, which is an owner decision with money.

**BH-D8. Every published port of the compose stack binds 127.0.0.1.**
`redis`, `postgres` and `meeting-bot` already do. `neo4j` does not. The app
reaches Neo4j at `bolt://localhost:7687` (`settings.py:839`), so the change
breaks nothing in the app.

**BH-D9. The runtime user does not deploy.** This is the later shape of BH-5.
A deploy user holds the SSH key and narrowed sudo, and owns the checkout.
`acb` runs the services and has no sudo and no `docker` group.

A deploy user that installs unit files is root by design. The goal is that
the RUNTIME user is not.

---

## 5. Slices

Each slice is one PR. "The gate" names the §3a grant of `CLAUDE.md` that
covers the act until 2026-11-30, or OWNER-GATE.

### BH-1 — One allowlisted env for every child process (H-270)

**Scope.**

- Add `acb_common/child_env.py` with `child_env(*, extra=None)` and
  `copilot_env()` (BH-D1, BH-D2, BH-D3).
- Pass `env=` at each of the 45 sites of §2.2 that run in the gateway, and at
  `files.py:215` and `agent.py:3794`. Container-side sites
  (`mutation_runner.py`, `sandbox/data_engine.py`, `meeting_bot/`) go on a
  named exempt list, each with a reason.
- Pass `env=copilot_env()` at `copilot_agent.py:219-226`, `main.py:134`,
  `main.py:1915` and `mutation_runner.py:114`.
- Make `MetoriteCopilotAgent.start` refuse when no token is set, so that
  `_agent.py:772` never runs.
- Move `pdf_render`, `code_tools` and `loader` onto the helper.

**Acceptance.**

1. The stub CLI is a Python script that writes its env names to a file.
   Put `DATABASE_URL`, `GATEWAY_INTERNAL_TOKEN` and a canary
   `BH1_CANARY_SECRET` in `os.environ`. Start the stub through
   `MetoriteCopilotAgent.start`. The child env holds none of the three. It
   holds `PATH`, `HOME` and `COPILOT_SDK_AUTH_TOKEN`.
2. The same stub, through `main.py`'s model listing path, gives the same
   result.
3. Every spawn of §2.2's gateway rows passes `env=` from the helper (BH-F1).
4. The test of item 1 FAILS on `main` before the change (verified red first).
5. On the box, after the deploy, a Copilot child shows no secret name (live
   check below).

**Verification.**

```bash
uv run pytest tests/unit/test_child_env_seam.py tests/unit/test_copilot_child_env.py -q
uv run pytest tests/unit/test_integrations_env_hardening.py tests/unit/test_sandbox_broker_seam.py -q
uv run ruff check packages/acb_common apps/services/gateway apps/services/orchestrator
# Live, after the deploy. Names only, never values:
ssh metorite 'for p in $(pgrep -u acb -f "github-copilot-sdk/cli"); do tr "\0" "\n" < /proc/$p/environ | cut -d= -f1 | sort | tr "\n" " "; echo; done'
```

A CLI child exists only while a Copilot session runs. Start one Copilot agent
run from the app, then run the check. The name list must hold no name from
`.env` except the allowlist.

**Fences.** BH-F1 is `tests/unit/test_child_env_seam.py`. It is an AST scan
of `apps/services/gateway`, `apps/services/orchestrator/orchestrator` and
`packages/`. It reads each `subprocess.run`, `Popen`, `call`, `check_call`
and `check_output`. It also reads each `asyncio.create_subprocess_exec` and
`create_subprocess_shell`. Each one must pass `env=` from `child_env` or
`copilot_env`, or from a name bound to one in the same function.

`os.system` and `os.popen` fail the test. Every `CopilotClient(` passes
`env=`. The exempt list names each file and its reason. BH-F2 is
`tests/unit/test_copilot_child_env.py`, the stub CLI test of acceptance 1
and 2.

**Risks.**

- A tool that needs a name we did not list fails. The likely ones are a
  proxy name or a CA bundle on a customer silo. The allowlist holds them
  when set.
- `git` over HTTPS needs no env token (§2.2). If a clone uses a credential
  helper that reads `GH_TOKEN`, a push fails. `gh` is not installed on the
  box. Watch `git push` in the journal for one day.
- The `uv pip install` of the loader may need `UV_*` names. Pass
  `UV_CACHE_DIR` by value when BH-2 sets it.

**Rollback.** Revert the PR. The deploy restores the old code. No data
changes.

**The gate.** AGENT-SAFE. The deploy and the live check are under §3a
`deploy`.

### BH-2 — The systemd sandbox for the gateway, and NoNewPrivileges for the rest

**Scope.** Edit `deploy/hostinger/acb-gateway.service`:

```ini
ExecStart=/opt/acb/app/.venv/bin/uvicorn gateway.main:app --host 0.0.0.0 --port 8080
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/opt/acb/app/.env /opt/acb/app/.venv /opt/acb/app/data
ProtectHome=read-only
ReadWritePaths=-/home/acb/.acb -/home/acb/.copilot -/home/acb/.cache/copilot -/home/acb/.cache/github-copilot-sdk
CacheDirectory=acb-gateway
Environment=UV_CACHE_DIR=/var/cache/acb-gateway/uv
InaccessiblePaths=-/run/user -/run/docker.sock -/var/run/docker.sock -/etc/acb -/etc/sudoers.d
ProtectProc=invisible
RestrictSUIDSGID=yes
CapabilityBoundingSet=
AmbientCapabilities=
LockPersonality=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictRealtime=yes
```

- `ExecStart` uses the venv binary, not `uv run`. `uv run` syncs the venv on
  start, and the gateway must not write the venv for that.
- Four more units get `NoNewPrivileges=yes`, `PrivateTmp=yes` and
  `RestrictSUIDSGID=yes`. They are acb-workbench, acb-customer-console,
  acb-smoke-chat and acb-whatsapp-bridge.
- The workbench and the customer console also get `ProtectSystem=strict`,
  `ProtectHome=read-only` and `CapabilityBoundingSet=`. The workbench keeps
  `.next/cache` writable.
- The customer console also starts from the venv binary, for the same
  reason as the gateway.
- Copy the box's `acb-operator-console.service` into `deploy/hostinger/`
  with the workbench set.
- `acb-pull` gets nothing. It needs sudo until BH-5.
- Add `scripts/box_hardening_probe.sh`. It reads the `[Service]` lines of a
  unit file and runs probes in a transient unit with the same properties
  (`systemd-run --wait --pipe --uid=acb -p …`). It prints PASS or FAIL for
  each probe and no secret.

**Acceptance.**

1. `systemctl show acb-gateway -p NoNewPrivileges -p ProtectSystem -p ProtectHome`
   gives `yes`, `strict` and `read-only`.
2. The probe, with the gateway's properties, gives:
   - `sudo -n true` fails.
   - `touch /opt/acb/app/scripts/bh2` and `touch /opt/acb/app/deploy/hostinger/bh2`
     fail with "Read-only file system".
   - `touch /opt/acb/app/.git/hooks/bh2` fails.
   - `test -w /opt/acb/app/.env` succeeds.
   - `ls /run/user/1000` fails.
   - `docker ps` fails (no socket).
   - `crontab -l` fails (setgid is refused).
3. `systemd-analyze security acb-gateway` falls from 9.2 to 5.0 or lower.
   Record the number in this spec.
4. Count the "Read-only file system" and "Permission denied" lines in
   `journalctl -u acb-gateway`. The 24 hours after the deploy hold no more
   than the 24 hours before it.
5. One member flow for each write path works:
   - Save an integration key. This writes `.env`.
   - Upload an attachment. This writes `data/`.
   - Run an agent. This writes the clone.
   - Start a Copilot run. This writes the CLI state.

**Verification.**

```bash
uv run pytest tests/unit/test_unit_hardening.py tests/unit/test_deploy_serialize.py tests/unit/test_backup_deploy_wiring.py -q
# Live, after the deploy:
ssh metorite 'systemctl show acb-gateway -p NoNewPrivileges -p ProtectSystem -p ProtectHome -p MainPID'
ssh metorite 'systemd-analyze security acb-gateway --no-pager | tail -1'
ssh metorite 'bash /opt/acb/app/scripts/box_hardening_probe.sh /etc/systemd/system/acb-gateway.service'
ssh metorite 'journalctl -u acb-gateway --since "-24h" --no-pager | grep -cE "Read-only file system|Permission denied"'
curl -s -o /dev/null -w "%{http_code}\n" https://api.metorite.com/health
```

**Fences.** BH-F3 (`tests/unit/test_unit_hardening.py`): every unit in
`deploy/hostinger/` with `User=acb`, except `acb-pull.service`, has
`NoNewPrivileges=yes`. `acb-gateway.service` has each line of the BH-2 set.
Its `ReadWritePaths` entries are on one allowlist constant in the test, so a
new write path needs a reviewed change. No unit names
`/etc/acb/backup-offbox.env` except `acb-backup.service` (the existing
`test_only_the_backup_unit_loads_the_offbox_key_file` stays).

**Risks.**

- A write path that the audit missed fails with EROFS. Acceptance 4 and 5
  catch it. The likely misses are the Copilot CLI's state under XDG names
  and a Custom Apps build output. The T2 vendor dir (`settings.py:606`) is
  under `~/.acb/agents` by default, so it stays writable.
- `PrivateTmp=yes` breaks a Docker bind mount of a path under `/tmp`. No
  measured path does that. The fallback clone dir `/tmp/acb_agents`
  (`copilot_sandbox.py:85`) applies only when `agents_clone_dir` is empty.
- `ProtectProc=invisible` hides processes of other users only. Same-user
  processes stay visible. It is still worth the line.
- The Copilot CLI may need `/run/user/1000` for a keychain probe. With
  `XDG_RUNTIME_DIR` not in `copilot_env()`, it must not look there.
- The startup sweeps of `main.py:153-178` log one line when Docker is absent,
  and the gateway starts. That is the documented behaviour.

**Rollback.** Revert the PR. To restore service in minutes, write a drop-in:
`sudo systemctl edit acb-gateway`, with `[Service]`, `NoNewPrivileges=no`,
`ProtectSystem=no`, `ProtectHome=no` and `InaccessiblePaths=`, then restart.
A drop-in in `/etc/systemd/system/acb-gateway.service.d/` survives every
deploy, so delete it in the fix PR's deploy, and say so in the PR.

**The gate.** AGENT-SAFE to build. The deploy and the unit install are §3a
`deploy` and `deploy-write`.

### BH-6 — Root units run only root-owned files

**Scope.**

- `vps_apply.sh` installs `scripts/backup_db.sh`, `scripts/backup_offbox.sh`,
  `scripts/offbox_lib.sh`, `deploy/hostinger/health-watchdog.sh` and the
  `infra/` dir into `/usr/local/lib/acb/` with
  `sudo install -o root -g root -m 0755` (dirs `0755`, files `0644` for data).
- `acb-backup.service`, `acb-health-watchdog.service` and `acb.service` run
  from `/usr/local/lib/acb/`.
- They stop loading `/opt/acb/app/.env`. `vps_apply.sh` writes
  `/etc/acb/root.env` (root:root 0600) from `.env` through a key allowlist:
  the `PG*` names, `PG_MODE`, `POSTGRES_*`, `DATABASE_URL`, `NEO4J_PASSWORD`
  and the names that `backup_db.sh` reads. A key off the list never enters
  the file. That keeps out `LD_PRELOAD`, `BASH_ENV`, `PATH` and their kind.
- `acb.service` runs `docker compose --env-file /etc/acb/root.env -f
  /usr/local/lib/acb/infra/docker-compose.yml`.
- `backup_db.sh` reads the Console DSN the same way, from a root-owned
  extract.
- `deploy/hostinger/deploy.sh:65` stops `source`-ing `.env`. It reads named
  keys with `grep`, as `vps_apply.sh:581` does.

**Acceptance.**

1. `systemctl cat acb-backup acb-health-watchdog acb` names no path under
   `/opt/acb` or `/home/acb` in `ExecStart`, `EnvironmentFile` or
   `WorkingDirectory`.
2. `stat -c '%U %a' /usr/local/lib/acb/*.sh /etc/acb/root.env` gives `root`
   for each file.
3. A `.env` line `LD_PRELOAD=/tmp/x.so` (on a test box or in the fence test)
   does not reach `/etc/acb/root.env`.
4. The next nightly backup logs its normal success lines, and the next
   watchdog tick runs. Check `systemctl list-timers acb-backup.timer --all`
   for a NEXT date (the H-123 lesson).

**Verification.**

```bash
uv run pytest tests/unit/test_root_units_root_owned.py tests/unit/test_backup_deploy_wiring.py -q
ssh metorite 'systemctl cat acb-backup acb-health-watchdog acb | grep -E "^(ExecStart|EnvironmentFile|WorkingDirectory)="'
ssh metorite 'sudo stat -c "%U %a %n" /usr/local/lib/acb/*.sh /etc/acb/root.env'
ssh metorite 'sudo systemctl start acb-backup.service; sudo journalctl -u acb-backup --since -40min --no-pager | tail -5'
ssh metorite 'systemctl list-timers acb-backup.timer acb-health-watchdog.timer --all --no-pager'
```

**Fences.** BH-F4 (`tests/unit/test_root_units_root_owned.py`): every unit
in `deploy/hostinger/` with `User=root` or no `User=` has no `ExecStart`,
`EnvironmentFile` or `WorkingDirectory` under `/opt/acb` or `/home`. No
tracked shell script runs `source` or `.` on a path that ends in `.env`.
The key allowlist of `root.env` is one constant, and the test fails on
`LD_PRELOAD`, `BASH_ENV`, `ENV`, `PATH` or `PYTHONPATH` in it.

**Risks.**

- The backup reads a key that the allowlist misses, and the night fails. The
  unit exits 1 and `vps-health.yml` shows it. Run one manual backup in the
  deploy (acceptance 4).
- `acb.service` uses relative bind mounts (`./postgres/00_create_databases.sql`).
  The copy must hold the whole `infra/` dir, or a fresh `up` fails. Postgres
  runs managed on prod, so the local `postgres` container matters for the
  backup verify only.
- The deploy runs as `acb` with sudo. Until BH-5, an `acb` that is not the
  gateway can still edit the copy step. BH-6 closes P3 for the gateway, which
  BH-2 cannot write into the checkout.

**Rollback.** Revert the PR and redeploy. The old units point at the
checkout again. Leave `/usr/local/lib/acb` in place. It does no harm.

**The gate.** AGENT-SAFE to build. The deploy is §3a `deploy`. The new
`/etc/acb/root.env` is §3a `env-write`.

### BH-8 — Neo4j leaves the public internet

**Scope.** `infra/docker-compose.yml:81-82` become `"127.0.0.1:7474:7474"`
and `"127.0.0.1:7687:7687"`.

**Acceptance.** From outside the box, port 7474 and port 7687 refuse or time
out. On the box, `ss -ltn` shows `127.0.0.1:7474` and `127.0.0.1:7687`.

**Verification.**

```bash
uv run pytest tests/unit/test_compose_ports_local.py -q
curl -s -m 6 -o /dev/null -w "%{http_code}\n" http://187.127.172.200:7474/   # expect 000
timeout 6 bash -c 'echo > /dev/tcp/187.127.172.200/7687' && echo OPEN || echo CLOSED   # expect CLOSED
ssh metorite 'ss -ltn | grep -E ":7474|:7687"'
```

**Fences.** BH-F5 (`tests/unit/test_compose_ports_local.py`): every `ports:`
entry in `infra/docker-compose.yml` starts with `127.0.0.1:`.

**Risks.** A tool off the box that reads Neo4j stops. None is known. Use an
SSH tunnel for an operator who needs the browser.

**Rollback.** Revert the PR, then `docker compose up -d neo4j`.

**The gate.** AGENT-SAFE. The deploy is §3a `deploy`. ⚠️ Whether the Neo4j
password on the box is the compose default is NOT checked. The owner checks
it (§7).

### BH-3 — The sandbox broker gets its own unit and user

**Scope.** This builds the "later hardening" of `maf_coding_engine.md`
WS43-S2, and it is the gate WS43-G1.

- A new system user `acb-sandbox`, in the `docker` group. It is not in
  `sudo`.
- A new unit `acb-sandbox-broker.service`, `User=acb-sandbox`, with the BH-2
  set except the socket line. It listens on a Unix socket under
  `RuntimeDirectory=acb-sandbox` (`/run/acb-sandbox/broker.sock`, mode
  `0660`, group `acb`).
- The API is the broker's own verbs, not Docker's: acquire, exec, read
  status, release and sweep. The request carries a binding id and a working
  dir. The broker builds the `docker run` argv itself
  (`sandbox_broker.py:723-737`), and it refuses a mount outside the sandbox
  root. No caller sends a raw Docker argument.
- `DockerCLI` in the gateway becomes a client of that socket. The startup
  sweeps of `main.py:153-178` move into the broker unit.
- `copilot_sandbox.py` and `mutation.py` stay OFF. They gain the broker path
  only when someone turns them on, and that is out of scope.
- `acb` stays in the `docker` group for the deploy until BH-5. BH-2 already
  hides the socket from the gateway.

**Why this shape (BH-D5).**

| Option | Closes P2 | Cost | Verdict |
|---|---|---|---|
| Broker unit with its own user | yes. The gateway sends verbs, not a container spec | one unit, one user, one socket API | **chosen** |
| Socket proxy with a path allowlist | no. `POST /containers/create` with `Binds: ["/:/host"]` passes a path filter | one container | rejected |
| Rootless Docker | yes, for the host root | a second daemon, new images, WS43-G5 | later |
| Leave the sandbox off | yes | the WS-43 terminal never ships | the state today |

**Acceptance.**

1. `ps -o user,supgid -p $(systemctl show -p MainPID --value acb-gateway)`
   shows the gateway. `docker ps` from the gateway's probe fails.
2. A covered agent's `run_script` runs in a sandbox, through the broker
   socket (needs the sandbox image on the box).
3. A request to the broker with a mount outside the sandbox root is refused,
   and the journal names the refusal.

**Verification.**

```bash
uv run pytest tests/unit/test_sandbox_broker_seam.py tests/unit/test_sandbox_broker_socket.py tests/unit/test_unit_hardening.py -q
ssh metorite 'systemctl show acb-sandbox-broker -p User -p ActiveState; ls -l /run/acb-sandbox/broker.sock'
ssh metorite 'bash /opt/acb/app/scripts/box_hardening_probe.sh /etc/systemd/system/acb-gateway.service'
```

**Fences.** WS43-F1 (`tests/unit/test_sandbox_broker_seam.py`) changes:
the only module that runs `docker` is the broker's server module, and the
gateway imports only the client. BH-F3 asserts the socket line on the
gateway unit.

**Risks.** A broker that is down stops every covered run. The broker unit
has `Restart=always`, and the gateway reports `SandboxUnavailable` as it
does today. The broker restart ends every run (§7.1 rule 13 of the WS-43
spec), as a gateway restart does today.

**Rollback.** Revert the PR. With the socket still hidden by BH-2, the
sandbox is down again, which is the state of today.

**The gate.** OWNER-GATE: WS43-G1 is the owner's gate on the box's Docker
access, and the owner picks the option (§7, OD-2). The build is
AGENT-SAFE after that. The deploy, the user and the unit are §3a `deploy`.

### BH-7 — Agent installs leave the shared venv

**Scope.** `loader._install_agent_deps` (`loader.py:1137-1226`) and
`dep_tools.install_dependency` (`dep_tools.py:49-86`) install with
`uv pip install --target /var/lib/acb-gateway/agent-site` (a
`StateDirectory=` of the gateway). The loader appends that dir to
`sys.path`, after the venv, so a venv package always wins. Then BH-2's
`ReadWritePaths` drops `/opt/acb/app/.venv`.

**Acceptance.**

1. An agent with a `requirements.txt` loads, and its package imports.
2. A package that the venv also holds stays at the venv's version.
3. `test -w /opt/acb/app/.venv` fails in the gateway probe.

**Verification.**

```bash
uv run pytest tests/unit/test_agent_deps_target.py tests/unit/test_unit_hardening.py -q
ssh metorite 'bash /opt/acb/app/scripts/box_hardening_probe.sh /etc/systemd/system/acb-gateway.service'
```

**Fences.** BH-F6 (`tests/unit/test_agent_deps_target.py`): no install
command in `acb_skills` holds `--python sys.executable` with no `--target`.
BH-F3 drops `.venv` from the write allowlist.

**Risks.** An agent that needs a newer version of a shared library gets the
venv's version. The install log says so. This is an owner decision on the
product side (§7, OD-5).

**Rollback.** Revert the PR. The agent-site dir stays and does no harm.

**The gate.** AGENT-SAFE to build after OD-5. The deploy is §3a `deploy`.

### BH-4 — A write-only backup credential, with retention on the server

**Scope.** The box half and the Supabase half.

The box half (AGENT-SAFE):

- `offbox_lib.sh` reads `BACKUP_SUPABASE_URL`, `BACKUP_AUTH_EMAIL` and
  `BACKUP_AUTH_PASSWORD`. It asks `/auth/v1/token?grant_type=password` for a
  JWT just before the upload, and exports
  `RCLONE_CONFIG_OFFBOX_SESSION_TOKEN`. `BACKUP_S3_ACCESS_KEY_ID` holds the
  project ref, and `BACKUP_S3_SECRET_ACCESS_KEY` holds the anon key.
- The JWT goes through the same redaction as the key today
  (`offbox_lib.sh:108`).
- The night prefix becomes `nightly/<UTC stamp>-<16 random hex>/`.
- The box no longer deletes (`offbox_lib.sh:217` goes). It still lists one
  night to check the upload.

The Supabase half (OWNER-GATE), as SQL in `deploy/supabase/backup_writer.sql`
and one Edge Function in `deploy/supabase/functions/backup-retention/`:

- One Auth user, the backup writer, with a long random password.
- On `storage.objects`: one INSERT policy and one SELECT policy, `to
  authenticated`. Each one checks `bucket_id = 'metorite-backups'` and
  `name like 'nightly/%'`. Each one also checks that `auth.uid()` is the
  writer's id. There is no UPDATE policy and no DELETE policy.
- `backup-retention` deletes, through the Storage API with the service role,
  each night whose newest object is older than `BACKUP_S3_KEEP_DAYS`
  (default 14) by server `created_at`. It never deletes by count. It deletes
  nothing when the newest night is older than 2 days, because then the
  backups have stopped and every old night matters.
- `pg_cron` and `pg_net`, installed, call the function each day at 04:15
  UTC. The call carries a shared secret from `vault`.
- After one good night, the owner revokes the project-wide S3 key of H-123.

**Acceptance.** Run with the writer's JWT against the real bucket:

1. A new object uploads.
2. A PUT to that object's key again fails, and the object's bytes do not
   change.
3. A DELETE of that object fails, and the object still exists.
4. An upload of 300 MiB (above rclone's 200 MiB multipart cutoff) works.
5. One nightly run logs `off-box copy ok`.
6. The retention function, run by hand with `BACKUP_S3_KEEP_DAYS=0` against a
   test prefix, deletes the test night and nothing else.
7. `cron.job` holds the schedule, and `cron.job_run_details` shows a success
   the next day.

**Verification.**

```bash
uv run pytest tests/unit/test_backup_offbox_session_token.py tests/unit/test_backup_deploy_wiring.py -q
ssh metorite 'sudo journalctl -u acb-backup --since -26h --no-pager | grep -c "off-box copy ok"'
# In SQL, through the Supabase MCP execute_sql:
#   select jobname, schedule from cron.job where jobname = 'backup-retention';
#   select status, start_time from cron.job_run_details order by start_time desc limit 3;
#   select policyname, cmd from pg_policies where schemaname = 'storage' and tablename = 'objects';
```

**Fences.** BH-F7 (`tests/unit/test_backup_offbox_session_token.py`):
`offbox_lib.sh` holds no `rclone delete` and no `rclone purge`, and it
exports `RCLONE_CONFIG_OFFBOX_SESSION_TOKEN`. `backup_writer.sql` holds no
`for update` and no `for delete` policy. The retention function source
holds no sort-by-name-and-keep-N rule. The policy half is advisory until a
test can read the live project, and the acceptance drill is its check.

**Risks.**

- Supabase may treat an S3 PUT to an existing key as an UPDATE that RLS
  checks in a way the docs do not cover. Acceptance 2 decides. If it fails,
  BH-D7's fallback applies.
- A multipart upload may need more than INSERT. Acceptance 4 decides.
- If the project turns off the legacy anon key, the session token stops. The
  docs say the publishable key is "not yet supported". Watch the Supabase
  changelog.
- The writer user is a row in `auth.users` of the production project. The
  app does not use Supabase Auth for members, so the row has no app meaning.
- An attacker with root on the box can still add junk and run up storage
  cost. The attacker cannot delete or overwrite a night, and cannot read one,
  because each night is encrypted to the owner's public key.

**Rollback.** Put the project-wide key back in `/etc/acb/backup-offbox.env`
and revert the box half. Drop the two policies and unschedule the job.

**The gate.** The Supabase half is OWNER-GATE, because each of its acts
writes the owner's Supabase account (§3a rule 3, third party). These acts
are the Auth user, the policies, the function, the cron job and the key
revoke. The box half is
AGENT-SAFE, and its deploy is §3a `deploy`.

### BH-5 — Narrowed sudo and a deploy user (LATER, recorded only)

**Not dispatchable now.** This records the shape (BH-D9) and its input.

- A new user `acb-deploy` owns `/opt/acb/app` and holds the CI SSH key.
  `deploy.yml` and `acb-pull.service` use it.
- `acb` leaves the `sudo` and `docker` groups, and `/etc/sudoers.d/acb` goes.
  The runtime units keep `User=acb` and read the checkout as a group member.
- `/etc/sudoers.d/acb-deploy` lists the commands of §2.4, with full paths and
  fixed arguments where the script allows. The deploy's Docker calls become
  `sudo docker …` or move to a root helper.
- `.env` becomes `acb-deploy:acb 0640`. Then the gateway cannot write it, and
  the writes of the Integrations, OAuth and Models routes need a new home.
  That is the owner gate §6 (f), per-request credentials.

**Acceptance (for later).** `sudo -l -U acb` lists nothing. `id acb` shows no
`sudo` and no `docker`. A full deploy from CI and from `acb-pull` succeeds.

**The gate.** OWNER-GATE. The CI secret `SSH_USER` and the key change are the
owner's, and a wrong sudoers file can lock everyone out of the box.

---

## 6. The order

1. **BH-1** — closes H-270 for every child, and needs no box change.
2. **BH-2** — closes P1, P2 (for now), P3 for scripts, and P4, for the
   gateway. Run it right after BH-1, in the same day if the deploy is clean.
3. **BH-6** — closes P3 through `.env`, the one path that BH-2 leaves open.
4. **BH-8** — one line, and it ships with any of the above.
5. **BH-3** — before the WS-43 sandbox image goes on the box.
6. **BH-7** — closes P5 for the gateway, after OD-5.
7. **BH-4** — before, or on the same day as, the H-123 owner steps. Until
   then, do NOT put the project-wide key on the box.
8. **BH-5** — later.

After steps 1 to 4, a shell under the gateway can still read two things. It
can read the gateway's own secrets, which the gateway needs to run. It can
read the env of the other `acb` processes. It cannot become root, and it
cannot reach the off-box key.

---

## 7. Owner decisions

| Id | Decision | Recommendation | Blocks |
|---|---|---|---|
| OD-1 | Keep `copilot_sandbox_scope` empty by default (H-270 item 3) | Keep it empty. BH-1 and BH-2 close the leak, and D92 removes the SDK | nothing |
| OD-2 | The shape of the broker's Docker access (WS43-G1) | Its own unit and user (BH-3) | BH-3 |
| OD-3 | The off-box credential: Supabase session token, or a second provider with Object Lock | The session token (BH-4). It costs no money | BH-4 |
| OD-4 | Run the Supabase half of BH-4: the writer user, the policies, the function, the cron job, and the revoke of the old key | Yes, after the box half deploys | BH-4 |
| OD-5 | Agent installs go to a separate dir, or the `install_dependency` tool goes | A separate dir (BH-7) | BH-7 |
| OD-6 | The scope of the GitHub token that the Copilot CLI holds | A fine-grained token, limited to the repos the agents need | the BH-1 residual |
| OD-7 | Check the Neo4j password on the box against the compose default, and rotate it if it matches | Check it now. The port was public | BH-8 |
| OD-8 | The deploy user and the narrowed sudo (BH-5) | Later, after BH-1 to BH-4 | BH-5 |

---

## 8. Fences (R7)

| Id | Test | What breaks it |
|---|---|---|
| BH-F1 | `tests/unit/test_child_env_seam.py` | a spawn with no `env=` from the helper, a new `os.system`, a `CopilotClient(` with no `env=` |
| BH-F2 | `tests/unit/test_copilot_child_env.py` | a secret in the env of a stub CLI |
| BH-F3 | `tests/unit/test_unit_hardening.py` | an `acb` unit with no NoNewPrivileges, a gateway line removed, a new write path |
| BH-F4 | `tests/unit/test_root_units_root_owned.py` | a root unit that runs or loads a file under `/opt/acb` or `/home`, a `source` of `.env` |
| BH-F5 | `tests/unit/test_compose_ports_local.py` | a public port in the compose file |
| BH-F6 | `tests/unit/test_agent_deps_target.py` | an install into the shared venv |
| BH-F7 | `tests/unit/test_backup_offbox_session_token.py` | a delete from the box, an UPDATE or DELETE policy, a keep-newest-N rule |

Each fence is verified red first: run it on the tree before the slice, and it
must fail.

---

## 9. Verification commands, all slices

```bash
uv run pytest tests/unit/test_child_env_seam.py tests/unit/test_copilot_child_env.py \
  tests/unit/test_unit_hardening.py tests/unit/test_root_units_root_owned.py \
  tests/unit/test_compose_ports_local.py tests/unit/test_agent_deps_target.py \
  tests/unit/test_backup_offbox_session_token.py tests/unit/test_backup_deploy_wiring.py \
  tests/unit/test_deploy_serialize.py tests/unit/test_sandbox_broker_seam.py -q
# The H-271 Check, which must pass after BH-5 only:
ssh metorite 'sudo -l -U acb; id acb; stat -c %U /usr/local/lib/acb/backup_offbox.sh'
# The live probe, after each of BH-2, BH-3 and BH-7:
ssh metorite 'bash /opt/acb/app/scripts/box_hardening_probe.sh /etc/systemd/system/acb-gateway.service'
```
