#!/usr/bin/env bash
# WS-25 D1 — the deploy steps, as a versioned file.
#
# This file was lifted BYTE-IDENTICALLY out of `.github/workflows/deploy.yml`'s
# `env.DEPLOY_SCRIPT` (437 lines, sha256 a779724d089319f6…). It is the single
# copy both delivery paths run:
#
#   push path  `.github/workflows/deploy.yml` — `ssh 'bash -s' < this file`
#   pull path  `scripts/vps_pull.sh`          — `git show <sha>:this file | bash`
#
# One file, so the two paths cannot drift. Drift between them would only ever
# surface during an incident, which is the worst moment to discover it.
#
# ── The shebang is new, and it is the only thing that is ─────────────────────
# The extraction left line 1 as `set -e`, because inside a YAML `env:` value fed
# to `bash -s` there was nothing to declare a shell TO. That cost D1 half of its
# stated payoff: with no shebang and no `shell` directive, shellcheck refuses to
# analyse the file at all (SC2148, error) and exits 1 having checked nothing.
# The line is inert on both delivery paths — both invoke `bash <file>` or pipe
# into `bash -s`, where a `#!` is just a comment — so this buys the analysis for
# no behaviour change whatsoever.
#
# The file is deliberately left NON-executable (0644), matching vps_pull.sh.
# Nothing execs it by path; both callers name the interpreter. Marking it +x
# would advertise a fourth way to start a deploy that neither path uses.
#
# ── Running it by hand during an incident ────────────────────────────────────
#   cd /opt/acb/app && APP_DIR=/opt/acb/app bash scripts/vps_apply.sh
#
# ⚠️ but NOT from a checkout you are about to have rewritten. Step 0 below is
# `git reset --hard origin/main` — it replaces THIS FILE while bash is still
# reading it, and bash reads a script incrementally by byte offset. Measured,
# all three outcomes, none of which raise an alarm you would notice:
#   • git replaces by RENAME, so the open fd keeps the old inode: every step
#     runs, but they are the OLD file's steps against the NEW tree. Exit 0.
#   • an in-place rewrite to a SHORTER file: bash resumes past EOF and the
#     remaining steps silently do not happen at all. Exit 0.
#   • an in-place rewrite that merely SHIFTS bytes: bash resumes mid-token
#     (`--quiet` → `iet: command not found`). Exit 127.
# Copy it out first — `git show origin/main:scripts/vps_apply.sh > /tmp/a.sh`
# — and run THAT. This is what vps_pull.sh does, and why.
set -e
APP_DIR="${APP_DIR:-/opt/acb/app}"
cd "$APP_DIR"

echo "==> Pulling latest from origin/main"

# 🔴 **REPAIR THE CHECKOUT'S OWNERSHIP FIRST (H-89).** Same bug as the `.venv`
# one below, one directory over — and this one is worse, because it blocks the
# `git reset` that would have delivered its own fix.
#
# `git reset --hard` UNLINKS a tracked file to rewrite it, and unlinking needs
# write permission on the CONTAINING DIRECTORY. A directory owned `root:root`
# with `drwxr-xr-x` therefore stops the app user dead:
#
#   error: unable to unlink old
#   'workbench/operator_console/src/app/models/ModelDetails.tsx':
#   Permission denied
#
# Measured 2026-08-31: that killed the deploys of PR #190 and PR #198, three
# rounds each, both with green CI. The box sat on 3ad494bd for a day while
# `main` moved two merges ahead — and stayed UP the whole time, serving old
# code, so nothing alarmed. 113 root-owned paths in the tracked tree, created
# 2026-08-30 16:49 by something in that deploy running as root.
#
# `.venv` gets this treatment at line ~270 and the source tree never did, which
# is why the earlier fix could not save this case: `uv sync` is far downstream
# of the checkout that now fails.
#
# ⚠️ Scoped to what git must rewrite. `.next` is ~66k root-owned build files and
# is gitignored, so `git reset` never touches it — chowning it here would turn
# a fast repair into a minutes-long one for no benefit. `node_modules` likewise.
#
# `find -exec … +` rather than `chown $(find …)`: the command substitution
# splits on whitespace, so it breaks on any path with a space in it, and a tree
# this size can overflow the argument list. `find` under `sudo` also keeps the
# traversal quiet on directories the app user cannot read.
CHECKOUT_OWNER="$(stat -c '%U:%G' "$APP_DIR")"
CHECKOUT_USER="${CHECKOUT_OWNER%%:*}"
if sudo find "$APP_DIR" \( -name .next -o -name node_modules \) -prune -o \
     ! -user "$CHECKOUT_USER" -exec chown "$CHECKOUT_OWNER" {} + 2>/dev/null; then
  echo "    checkout ownership normalised to $CHECKOUT_OWNER before reset"
else
  echo "    WARNING: could not repair checkout ownership — git reset may fail"
  echo "             with 'unable to unlink old' (see H-89)."
fi

# Preserve runtime-managed state that lives in tracked files but is
# mutated on the VPS (agents.json = Control-Plane agent registry).
# git reset --hard would otherwise wipe agents registered via the UI.
cp apps/services/gateway/agents.json /tmp/acb-agents.json.bak 2>/dev/null || true
git fetch origin main
git reset --hard origin/main
if [ -s /tmp/acb-agents.json.bak ]; then
  cp /tmp/acb-agents.json.bak apps/services/gateway/agents.json
  echo "    restored runtime agents.json ($(wc -l < apps/services/gateway/agents.json) lines)"
fi

echo "==> Skipping deprecated LiteLLM proxy cleanup (already removed)"

echo "==> Ensuring memory-layer env vars (Neo4j disabled for low-memory VPS)"
# ⚠️ Hardcoded, while APP_DIR above is overridable — so is WB_ENV below. Noticed
# during WS-25 D1 and DELIBERATELY LEFT AS IS: on both delivery paths APP_DIR is
# /opt/acb/app, so "$APP_DIR/.env" would be the identical string today and
# changing it is a behaviour change, not a refactor. Named because D1's whole
# point is that this file can now be hand-run: `APP_DIR=/some/other/checkout`
# would git-reset one tree and then rewrite a DIFFERENT tree's .env, generating
# secrets into the live box while you thought you were in a sandbox. Until this
# is unified (owner's call), hand-run it only with APP_DIR=/opt/acb/app.
ENV_FILE="/opt/acb/app/.env"
for _var in MEM0_ENABLED GRAPHITI_ENABLED; do
  if ! grep -qE "^${_var}=" "$ENV_FILE" 2>/dev/null; then
    case "$_var" in
      MEM0_ENABLED)       echo "MEM0_ENABLED=true" >> "$ENV_FILE" ;;
      GRAPHITI_ENABLED)   echo "GRAPHITI_ENABLED=false" >> "$ENV_FILE" ;;
    esac
    echo "    + added $_var to .env"
  fi
done
# Ensure GRAPHITI stays off for this VPS size (idempotent)
if grep -qE '^GRAPHITI_ENABLED=true' "$ENV_FILE" 2>/dev/null; then
  sed -i 's/^GRAPHITI_ENABLED=true/GRAPHITI_ENABLED=false/' "$ENV_FILE"
  echo "    Disabled GRAPHITI (Neo4j) — saves ~500MB RAM"
fi

echo "==> Ensuring public-URL env vars"
# This block used to also seed MICROSOFT_TENANT_ID / AUTH_MICROSOFT_ENTRA_ID_TENANT
# with one company's directory GUID — wrong on every deployment except that one
# company's. Directory pinning is a per-deployment decision: set those keys by
# hand in .env for a single-tenant silo; leave them unset for the multi-directory
# default (`organizations`, see workbench auth.ts and email transport oauth.py).
for _var in GATEWAY_PUBLIC_URL WORKBENCH_PUBLIC_URL; do
  if ! grep -qE "^${_var}=" "$ENV_FILE" 2>/dev/null; then
    case "$_var" in
      GATEWAY_PUBLIC_URL)              echo "GATEWAY_PUBLIC_URL=https://api.metorite.com" >> "$ENV_FILE" ;;
      WORKBENCH_PUBLIC_URL)            echo "WORKBENCH_PUBLIC_URL=https://app.metorite.com" >> "$ENV_FILE" ;;
    esac
    echo "    + added $_var to .env"
  fi
done

# The workbench (Next.js, systemd unit acb-workbench) is a SEPARATE
# service that reads its OWN env file (control_plane/.env.local) via
# process.env — NOT the shared .env above. Its server routes forward
# GATEWAY_INTERNAL_TOKEN to the gateway's require_internal_auth. If the
# two files' tokens drift, every workbench->gateway internal call
# (/memory, /v1/chat/completions for orchestrator chat) 401s while the
# backend agents (which share .env) keep working — a confusing
# partial outage. So make .env the single source of truth: reconcile
# GATEWAY_INTERNAL_TOKEN in .env.local to EXACTLY match .env here,
# in-place, idempotently, preserving every other key in .env.local.
echo "==> Reconciling workbench internal token (.env.local <- .env)"
WB_ENV="/opt/acb/app/workbench/control_plane/.env.local"
SHARED_TOKEN="$(grep -E '^GATEWAY_INTERNAL_TOKEN=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
if [ -z "$SHARED_TOKEN" ]; then
  echo "    ! GATEWAY_INTERNAL_TOKEN not set in $ENV_FILE — skipping (backend also relies on it; fix the secret)"
elif [ ! -f "$WB_ENV" ]; then
  echo "    ! $WB_ENV missing — creating with the token only (other workbench keys must be provisioned separately)"
  printf 'GATEWAY_INTERNAL_TOKEN=%s\n' "$SHARED_TOKEN" > "$WB_ENV"
else
  CURRENT="$(grep -E '^GATEWAY_INTERNAL_TOKEN=' "$WB_ENV" 2>/dev/null | head -1 | cut -d= -f2-)"
  if [ "$CURRENT" = "$SHARED_TOKEN" ]; then
    echo "    already in sync (sha8=$(printf %s "$SHARED_TOKEN" | sha256sum | cut -c1-8))"
  else
    cp -a "$WB_ENV" "$WB_ENV.bak.$(date +%s)"
    # Rewrite the line without a sed s/// (token may contain / & etc.):
    # drop any existing line, then append the authoritative value.
    grep -vE '^GATEWAY_INTERNAL_TOKEN=' "$WB_ENV" > "$WB_ENV.tmp" || true
    printf 'GATEWAY_INTERNAL_TOKEN=%s\n' "$SHARED_TOKEN" >> "$WB_ENV.tmp"
    mv "$WB_ENV.tmp" "$WB_ENV"
    echo "    updated .env.local token to match .env (sha8=$(printf %s "$SHARED_TOKEN" | sha256sum | cut -c1-8)); backup written"
  fi
fi

echo "==> Bootstrapping Docker Compose stack (core only)"
docker compose -f infra/docker-compose.yml --profile core up -d --remove-orphans

echo "==> Waiting for healthchecks (up to 90s)"
deadline=$(( $(date +%s) + 90 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  unhealthy=$(docker ps --filter "label=com.docker.compose.project=acb" --format '{{.Names}}\t{{.Status}}' \
    | awk '$0 ~ /unhealthy|starting/ {print $1}')
  if [ -z "$unhealthy" ]; then break; fi
  sleep 3
done
docker ps --filter "label=com.docker.compose.project=acb" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo "==> Applying database migrations (02+ — init only mounts 00/01)"
# `< /dev/null` is LOAD-BEARING, not tidiness.
#
# This whole file is delivered as `ssh 'bash -s' < vps_apply.sh`, so the script
# IS stdin. `apply_migrations.sh` calls `docker exec -i`, which attaches and
# DRAINS stdin — swallowing every line of this script that has not been read
# yet. Bash then reaches EOF and exits 0, so the deploy reports success having
# silently skipped the gateway restart and the workbench rebuild below.
#
# It bit on 2026-08-07 and it bit invisibly: six consecutive deploys went green
# while the box kept serving an old bundle, because `verify()` health-checks the
# STILL-RUNNING previous deployment and cannot tell it apart from a new one.
# The trigger was a new ledger query (`-c "SELECT filename ..."`) — before it,
# every psql call piped its own input and stdin was never touched.
#
# External-database seam: a box whose Postgres is managed elsewhere (e.g.
# Supabase) sets PG_MODE=local — plus PGHOST/PGPORT/PGUSER/PGPASSWORD/
# PGSSLMODE for libpq, and deliberately SKIP_PRE_MIGRATION_BACKUP=1, because
# the local dump path needs superuser and the provider's PITR replaces it
# (docs/EXTERNAL_POSTGRES.md). Those keys live in .env, not this shell's
# environment, so lift them across before the runner starts.
# PGUSER is deliberately NOT lifted: the runner passes -U "$PG_USER" computed
# from POSTGRES_USER in .env, and an explicit -U outranks PGUSER anyway — set
# POSTGRES_USER/POSTGRES_DB to the managed values instead.
for _k in PG_MODE PGHOST PGPORT PGPASSWORD PGSSLMODE SKIP_PRE_MIGRATION_BACKUP; do
  _v="$(grep -E "^${_k}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
  if [ -n "$_v" ]; then export "${_k}=${_v}"; fi
done
APP_DIR="$APP_DIR" bash scripts/apply_migrations.sh < /dev/null

echo "==> Applying the Customer Console ladder (D47 · H-24)"
# ⚠️ This ran NOWHERE until 2026-08-27, and that is the board's own
# "platform_api is on the box but inert". `infra/customer_console/` is the
# Console's ladder against the Console's OWN Supabase project (D34); the applier
# above is bolted to the local docker Postgres and cannot reach it. So the
# Console got a dedicated DSN-driven applier and nothing ever invoked it: the
# deploy shipped Console CODE expecting a schema its database did not have, and
# reported success. CP-12 made it visible — migration 009 creates the `operator`
# tables and, unapplied, `GET /operators` answers 500 rather than 404 (H-64).
#
# R6 puts this BEFORE the Console restart further down, so old code never meets
# new schema. `< /dev/null` is the same load-bearing redirect as the tenant call
# above, for the identical reason: this whole file is delivered ON STDIN.
#
# ⚠️ FAIL-CLOSED, and the exact condition is deliberate. H-24 says "fail the
# deploy when its DSN is unset rather than skipping". Read literally that would
# brick every TENANT deploy on a box that runs no Console at all, so the test is
# provisioned-and-misconfigured rather than merely unset:
#   • a DSN is present         -> apply the ladder.
#   • no DSN, unit ENABLED     -> the Console is live and misconfigured. FAIL.
#   • no DSN, unit not enabled -> not provisioned here. Say so LOUDLY, continue.
# The thing H-24 closes is the SILENT skip. A loud, reasoned skip is not one.
CC_ENV="$APP_DIR/apps/services/customer_console/.env"
CC_DSN="$(grep -E '^CUSTOMER_CONSOLE_DATABASE_URL=' "$CC_ENV" 2>/dev/null | tail -1 | cut -d= -f2-)"
# systemd's EnvironmentFile accepts quoted values; psql would take the quotes
# literally and try to resolve them as a hostname.
CC_DSN="${CC_DSN%\"}"; CC_DSN="${CC_DSN#\"}"
CC_DSN="${CC_DSN%\'}"; CC_DSN="${CC_DSN#\'}"
if [ -n "$CC_DSN" ]; then
  CUSTOMER_CONSOLE_DATABASE_URL="$CC_DSN" \
    bash scripts/apply_customer_console_migrations.sh < /dev/null
elif systemctl is-enabled --quiet acb-customer-console 2>/dev/null; then
  echo "    !! acb-customer-console is ENABLED, but $CC_ENV carries no"
  echo "       CUSTOMER_CONSOLE_DATABASE_URL. The service would serve new code"
  echo "       against an unmigrated schema. Refusing to continue."
  exit 1
else
  echo "    no Console DSN and acb-customer-console is not enabled here"
  echo "    -> Console ladder SKIPPED (this box does not run the Console)"
fi

echo "==> Syncing Python deps"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 🔴 **TWO DELIVERY PATHS, TWO UIDs, ONE VENV.** This aborted every push-path
# deploy from 2026-08-26 to 2026-08-29, and it aborted them RIGHT HERE — before
# the workbench build and before the Operator Console block. So the box took
# each new checkout and none of the new builds, which is why `git log` on the
# box read current while every compiled surface stayed days behind.
#
# The header of this file says "one file, so the two paths cannot drift". The
# FILE does not drift. The UID does:
#
#   pull path   acb-pull.service runs `User=root`  → writes root-owned files
#   push path   deploy.yml SSHes as the app user   → cannot remove them
#
# Measured 2026-08-29: 36 files of 9883 under `.venv` were `root:root` in an
# otherwise `acb:acb` tree, and `uv sync` stopped on the first one it met:
#
#   error: failed to remove file `…/sherpa_onnx-1.13.6.dist-info/INSTALLER`:
#          Permission denied (os error 13)
#
# `set -e` (line 42) then took the remaining 480 lines with it. The job polled
# 24 times x 3 rounds for a SHA no process was working toward any more, and
# reported the box unreachable — so for three days this read as a network or
# health fault, which is where the diagnosis kept going.
#
# ⚠️ **Normalise AFTER the sync, and only as root.** Root can always remove
# root's files, so the root path never fails — it only leaves the mess that
# makes the NEXT app-user deploy fail. Deriving the owner from $APP_DIR rather
# than naming `acb` keeps this correct on a box that installs somewhere else.
#
# 🔴 **REPAIR BEFORE, NORMALISE AFTER — and the BEFORE half is the one that
# matters.** The first version of this fix (PR #155) only chowned as root,
# after the sync, and it never once ran. The reason is a race nobody had to
# lose deliberately:
#
#   1. a merge moves `release`;
#   2. the PUSH path (app user) reaches the box first and checks out the SHA;
#   3. it dies here at `uv sync`, because the root files are still there;
#   4. `acb-pull` wakes, compares SHAs, says "already current" — and never
#      applies. So ROOT NEVER GETS A TURN, and the repair never executes.
#
# Measured 2026-08-29: the box sat at 16f5dccd with the chown present at line
# 254 of its own checkout, 39 root-owned files under `.venv`, and `/providers`
# still 404. The fix was on the box and could not reach itself.
#
# So repair up front, with `sudo`, whoever is running. The app user already
# holds passwordless sudo — this script uses `sudo systemctl` throughout — and
# without it the app-user path has no way out of a hole only root can dig it
# out of.
VENV_OWNER="$(stat -c '%U:%G' "$APP_DIR")"
if [ -d "$APP_DIR/.venv" ]; then
  if sudo chown -R "$VENV_OWNER" "$APP_DIR/.venv" 2>/dev/null; then
    echo "    venv ownership repaired to $VENV_OWNER before sync"
  else
    echo "    WARNING: could not chown $APP_DIR/.venv — uv sync may fail on"
    echo "             files this user cannot remove (see PR #155/#156)."
  fi
fi
uv sync
if [ "$(id -u)" = "0" ] && [ -d "$APP_DIR/.venv" ]; then
  chown -R "$VENV_OWNER" "$APP_DIR/.venv"
  echo "    venv normalised to $VENV_OWNER — root ran this apply"
fi

# ── [TRIAL] Free local diarization (sherpa-onnx) ──────────────────
# Adds free CPU-only speaker separation for Whisper transcripts.
# Fully reversible: set LOCAL_DIAR=0 below (or NOTES_LOCAL_DIARIZATION=0
# in .env) + redeploy to fall back to the Deepgram/Whisper path. The
# app is fail-safe — if any of this is missing it silently no-ops.
LOCAL_DIAR="1"
MODELS_DIR="$APP_DIR/models/sherpa"
SEG_MODEL="$MODELS_DIR/segmentation.onnx"
EMB_MODEL="$MODELS_DIR/embedding.onnx"
upsert_env() {  # key value — set-or-replace in $ENV_FILE (paths ok)
  if grep -qE "^$1=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    echo "$1=$2" >> "$ENV_FILE"
  fi
}
echo "==> [trial] Local diarization (sherpa-onnx), LOCAL_DIAR=$LOCAL_DIAR"
if [ "$LOCAL_DIAR" = "1" ]; then
  mkdir -p "$MODELS_DIR"
  # ffmpeg decodes the meeting audio → 16kHz PCM for sherpa. The VPS
  # never needed it before (cloud STT decodes server-side), so install
  # it on demand; without it the diarization pass silently no-ops.
  if ! command -v ffmpeg >/dev/null; then
    echo "    installing ffmpeg (needed to decode audio for diarization)…"
    (sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg) >/dev/null 2>&1 \
      && echo "    + ffmpeg installed" || echo "    ! ffmpeg install failed — decode will no-op"
  fi
  uv pip install -q 'sherpa-onnx>=1.10' 'numpy>=1.24' \
    || echo "    ! sherpa-onnx install failed — local diar will no-op"
  if [ ! -f "$SEG_MODEL" ]; then
    curl -fsSL -o /tmp/seg.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" \
      && tar xjf /tmp/seg.tar.bz2 -C /tmp \
      && cp /tmp/sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx "$SEG_MODEL" \
      && echo "    + segmentation model (int8, ~6MB)" || echo "    ! seg download failed"
  fi
  if [ ! -f "$EMB_MODEL" ]; then
    curl -fsSL -o "$EMB_MODEL" "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx" \
      && echo "    + speaker-embedding model (CAM++, ~27MB)" || echo "    ! emb download failed"
  fi
  if [ -f "$SEG_MODEL" ] && [ -f "$EMB_MODEL" ]; then
    upsert_env NOTES_LOCAL_DIARIZATION 1
    upsert_env SHERPA_SEG_MODEL "$SEG_MODEL"
    upsert_env SHERPA_EMB_MODEL "$EMB_MODEL"
    grep -qE "^SHERPA_DIAR_THRESHOLD=" "$ENV_FILE" || echo "SHERPA_DIAR_THRESHOLD=0.7" >> "$ENV_FILE"
    echo "    local diarization ENABLED (Whisper transcripts get free speakers)"
  else
    upsert_env NOTES_LOCAL_DIARIZATION 0
    echo "    models missing — left OFF; Deepgram/Whisper path unaffected"
  fi
else
  upsert_env NOTES_LOCAL_DIARIZATION 0
  echo "    LOCAL_DIAR=0 — local diarization OFF"
fi

# ── WhatsApp bridge (whatsmeow, personal-number QR) ───────────────
# A localhost-only Go service that links a PERSONAL number by QR and
# streams messages to the gateway's /whatsapp/bridge/ingest (same
# triage brain as a Cloud API number). Env is added BEFORE the gateway
# restart below so the gateway picks up WHATSAPP_BRIDGE_URL/SECRET.
# Opt out any time with WHATSAPP_BRIDGE_ENABLED=0 in .env. Fail-safe:
# a build/start hiccup only skips the bridge, never the whole deploy.
# NOTE: unofficial multi-device is outside WhatsApp's ToS — the number
# can be banned; idle is harmless, risk begins when a number is paired.
echo "==> WhatsApp bridge (whatsmeow, personal-number QR)"
grep -qE '^WHATSAPP_BRIDGE_ENABLED=' "$ENV_FILE" || echo "WHATSAPP_BRIDGE_ENABLED=1" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_URL='         "$ENV_FILE" || echo "WHATSAPP_BRIDGE_URL=http://localhost:8790" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_GATEWAY_URL=' "$ENV_FILE" || echo "WHATSAPP_BRIDGE_GATEWAY_URL=http://localhost:8080" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_ADDR='        "$ENV_FILE" || echo "WHATSAPP_BRIDGE_ADDR=127.0.0.1:8790" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_STORE='       "$ENV_FILE" || echo "WHATSAPP_BRIDGE_STORE=/opt/acb/data/whatsapp_bridge/bridge-store.db" >> "$ENV_FILE"
# Voice calls: recorded audio lands beside the session store and is
# swept after RETENTION_DAYS (recording runs ~115 MB per call-hour,
# so unbounded would fill the VPS). Blank RECORD_DIR = no recording.
grep -qE '^WHATSAPP_BRIDGE_CALL_RECORD_DIR='    "$ENV_FILE" || echo "WHATSAPP_BRIDGE_CALL_RECORD_DIR=/opt/acb/data/whatsapp_bridge/call-recordings" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_CALL_RETENTION_DAYS=' "$ENV_FILE" || echo "WHATSAPP_BRIDGE_CALL_RETENTION_DAYS=7" >> "$ENV_FILE"
# Generate a strong shared secret once (used by BOTH gateway + bridge).
if ! grep -qE '^WHATSAPP_BRIDGE_SECRET=.+' "$ENV_FILE"; then
  _wbsecret="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  grep -vE '^WHATSAPP_BRIDGE_SECRET=' "$ENV_FILE" > "$ENV_FILE.wb.tmp" && mv "$ENV_FILE.wb.tmp" "$ENV_FILE"
  echo "WHATSAPP_BRIDGE_SECRET=$_wbsecret" >> "$ENV_FILE"
  echo "    + generated WHATSAPP_BRIDGE_SECRET"
fi

WB_ENABLED="$(grep -E '^WHATSAPP_BRIDGE_ENABLED=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
if [ "$WB_ENABLED" = "1" ]; then
  mkdir -p /opt/acb/data/whatsapp_bridge
  # Ensure a Go >=1.24 toolchain (pure-Go / CGO-free build). Install to
  # /usr/local/go on demand; cached across deploys.
  GO=""
  if command -v go >/dev/null 2>&1 && go version 2>/dev/null | grep -qE 'go1\.(2[4-9]|[3-9][0-9])'; then
    GO="$(command -v go)"
  elif [ -x /usr/local/go/bin/go ] && /usr/local/go/bin/go version | grep -qE 'go1\.(2[4-9]|[3-9][0-9])'; then
    GO="/usr/local/go/bin/go"
  else
    echo "    installing Go 1.24.7…"
    if curl -fsSL -o /tmp/go.tgz https://go.dev/dl/go1.24.7.linux-amd64.tar.gz; then
      sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf /tmp/go.tgz && GO="/usr/local/go/bin/go" && echo "    + Go installed"
    fi
  fi
  if [ -n "$GO" ] && ( cd "$APP_DIR/apps/services/whatsapp_bridge" && GOFLAGS=-mod=mod CGO_ENABLED=0 "$GO" build -o whatsapp_bridge . ); then
    sudo cp "$APP_DIR/deploy/hostinger/acb-whatsapp-bridge.service" /etc/systemd/system/acb-whatsapp-bridge.service || true
    sudo systemctl daemon-reload || true
    sudo systemctl enable acb-whatsapp-bridge >/dev/null 2>&1 || true
    sudo systemctl restart acb-whatsapp-bridge || true
    sleep 2
    if systemctl is-active --quiet acb-whatsapp-bridge; then
      echo "    WhatsApp bridge is active (localhost:8790)"
    else
      echo "    ! WhatsApp bridge not active (non-fatal) — journalctl -u acb-whatsapp-bridge"
    fi
  else
    echo "    ! bridge build/toolchain unavailable (non-fatal) — personal-number linking stays offline"
  fi
else
  echo "    WHATSAPP_BRIDGE_ENABLED != 1 — ensuring bridge is stopped"
  sudo systemctl disable --now acb-whatsapp-bridge >/dev/null 2>&1 || true
fi

# ── Self-hosted meeting bot (Note Taker §3.13) ────────────────────
# A headless-Chrome participant that joins a Meet link, records the
# call, and feeds the normal transcribe → diarize → notes pipeline.
# Runs as a Docker service (profile "meetingbot") because each in-call
# bot is a real Chrome (~1-3 GB RAM + up to 2 CPU) — it needs a box
# with headroom, which is why this is opt-out-able rather than core.
# Env is written BEFORE the gateway restart so the gateway picks up
# MEETING_BOT_URL/TOKEN. Fail-safe: a build or start hiccup skips the
# bot only — the rest of the deploy (and the whole app) is unaffected.
echo "==> Meeting bot (self-hosted, headless Chrome)"
grep -qE '^MEETING_BOT_ENABLED=' "$ENV_FILE" || echo "MEETING_BOT_ENABLED=1" >> "$ENV_FILE"
# The gateway talks to the worker over the host-published port; the
# worker calls back to the gateway from inside its container.
upsert_env MEETING_BOT_URL "http://127.0.0.1:8095"
upsert_env NOTES_BOT_PROVIDER "selfhosted"
upsert_env NOTES_LIVE_CALLBACK_BASE "http://host.docker.internal:8080"
# Where the worker fetches streaming-ASR credentials. Without this
# the bot records fine but produces no live captions — the failure
# mode is silent, so it is wired by default rather than left opt-in.
upsert_env NOTES_LIVE_TOKEN_URL \
  "http://host.docker.internal:8080/notes/stt/bot-live-token"
# Persistent Chrome profile for the bot. Meet AUTO-DECLINES
# anonymous participants (they never get to knock) whenever the
# host isn't in the call yet or link-guests can't ask in — a
# signed-in profile is the only unattended fix. The dir is inside
# the container (its own volume); sign in once via
# POST /google-login. Defaults preserved on re-deploy.
grep -qE '^MEET_PROFILE_DIR=' "$ENV_FILE" || \
  echo "MEET_PROFILE_DIR=/profile" >> "$ENV_FILE"
# Live VNC view of the bot's browser (loopback only). Default off.
grep -qE '^MEET_VNC=' "$ENV_FILE" || echo "MEET_VNC=0" >> "$ENV_FILE"
# One shared secret, both directions (gateway -> worker, and the
# worker's live-segment callback). Generated once, then reused.
if ! grep -qE '^MEETING_BOT_TOKEN=.+' "$ENV_FILE"; then
  _mbtoken="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  grep -vE '^MEETING_BOT_TOKEN=' "$ENV_FILE" > "$ENV_FILE.mb.tmp" && mv "$ENV_FILE.mb.tmp" "$ENV_FILE"
  echo "MEETING_BOT_TOKEN=$_mbtoken" >> "$ENV_FILE"
  echo "    + generated MEETING_BOT_TOKEN"
fi

MB_ENABLED="$(grep -E '^MEETING_BOT_ENABLED=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
# Never recreate the worker while a bot is in a live call — each
# bot is a Chrome inside the container, so `up --build` mid-meeting
# kills the notetaker someone is relying on (this happened). The
# skipped rebuild simply lands on the next deploy.
MB_ACTIVE="$(curl -fsS --max-time 5 http://127.0.0.1:8095/health 2>/dev/null \
  | grep -o '"active":[0-9]*' | cut -d: -f2 || true)"
if [ "$MB_ENABLED" = "1" ] && [ -n "$MB_ACTIVE" ] && [ "$MB_ACTIVE" -gt 0 ] 2>/dev/null; then
  echo "    ~ $MB_ACTIVE bot(s) in a call right now — keeping the running worker; rebuild deferred to the next deploy"
elif [ "$MB_ENABLED" = "1" ]; then
  # --build is cheap after the first run (layer cache); the first
  # deploy pulls the Playwright base image, which is large.
  # --env-file is explicit on purpose: compose resolves a bare .env
  # against the project directory (infra/), not the app root.
  #
  # `timeout` and </dev/null are both scar tissue: an apt postinst
  # that prompts (tzdata asking "Geographic area:") hung this build
  # for 15 minutes until the deploy's SSH session died, and the
  # deploy then reported success from gateway health while the OLD
  # bot image stayed live. Bounded + no tty means a prompt fails
  # fast instead of eating the deploy.
  MB_IMAGE_BEFORE="$(docker images -q acb-meeting-bot:latest 2>/dev/null || true)"
  if timeout 900 docker compose --env-file "$ENV_FILE" \
       -f infra/docker-compose.yml --profile meetingbot \
       up -d --build meeting-bot </dev/null 2>&1 | tail -5; then
    sleep 5
    if curl -fsS --max-time 10 http://127.0.0.1:8095/health >/dev/null 2>&1; then
      MB_IMAGE_AFTER="$(docker images -q acb-meeting-bot:latest 2>/dev/null || true)"
      if [ -n "$MB_IMAGE_BEFORE" ] && \
         [ "$MB_IMAGE_BEFORE" = "$MB_IMAGE_AFTER" ]; then
        echo "    meeting bot UP on 127.0.0.1:8095 (image unchanged — nothing to rebuild)"
      else
        echo "    meeting bot UP on 127.0.0.1:8095 (image rebuilt)"
      fi
    else
      echo "    !! meeting bot started but /health not answering — join-by-link is DOWN"
      docker logs --tail 20 acb-meeting-bot 2>&1 | sed 's/^/      /' || true
    fi
  else
    # Loud and specific: the old container is probably still serving,
    # which is exactly the state that reads as "deployed" but isn't.
    echo "    !! meeting bot build/start FAILED or timed out — join-by-link is running the PREVIOUS image"
    tail -20 /tmp/mb-build.log 2>/dev/null | sed 's/^/      /' || true
  fi
else
  echo "    MEETING_BOT_ENABLED=0 — meeting bot OFF (join-by-link unavailable)"
  docker compose --env-file "$ENV_FILE" -f infra/docker-compose.yml \
    --profile meetingbot rm -sf meeting-bot >/dev/null 2>&1 || true
fi

echo "==> Restarting gateway (systemd)"
# `restart` alone does NOT survive a reboot — without the enable
# symlink the box comes back with the gateway down until the next
# deploy. Install + enable the unit every time so boot is covered.
sudo cp "$APP_DIR/deploy/hostinger/acb-gateway.service" /etc/systemd/system/acb-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable acb-gateway >/dev/null 2>&1 || true
sudo systemctl restart acb-gateway
sleep 3
systemctl is-active --quiet acb-gateway || { echo "GATEWAY FAILED TO START"; exit 1; }
echo "Gateway is active"

echo "==> Restarting the Customer Console (systemd)"
# The unit FILE arrives via the BO-23 sync loop below, which deliberately does
# not restart services. That is correct for the loop and wrong for the Console:
# `git reset --hard` above moves files, it does not restart a running Python
# process, so without this step the Console serves whatever code it started with
# until somebody notices. Its ladder is already applied, so R6 holds.
#
# Conditional on the unit being enabled — the same "does this box run a Console"
# test as the ladder step. Failure is LOUD: a dead service behind a green deploy
# is the WS-25 failure mode this file carries the most scar tissue about.
if systemctl is-enabled --quiet acb-customer-console 2>/dev/null; then
  sudo systemctl restart acb-customer-console
  sleep 3
  if systemctl is-active --quiet acb-customer-console; then
    echo "    Customer Console is active (127.0.0.1:8090)"
  else
    echo "CUSTOMER CONSOLE FAILED TO START"
    sudo journalctl -u acb-customer-console --no-pager -n 40 || true
    exit 1
  fi
else
  echo "    acb-customer-console is not enabled here — skipping restart"
fi

# ── App Workshop T2 (React) build vendor cache ────────────────────
# Shared, pinned react/react-dom/esbuild/lucide-react the
# app-builder agent's build script
# (apps/agents/agent-app-builder/build/build_t2.mjs) resolves
# against via esbuild's nodePaths — installed ONCE here, never
# per-app. Same default-resolution as CUSTOM_APPS_ROOT (already
# proven in production for the App Workshop): read an override
# from .env, else {AGENTS_CLONE_DIR:-$HOME/.acb/agents}.
# lucide-react: zero runtime deps of its own (only a react peer
# dep, already here) — pinned to the exact version
# workbench/control_plane itself uses, so every T2 app gets real
# icon components for free instead of hand-rolled SVGs.
echo "==> Provisioning T2 (React) vendor cache for the App Workshop builder"
T2_VENDOR_DIR="$(grep -E '^CUSTOM_APPS_T2_VENDOR_DIR=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
if [ -z "$T2_VENDOR_DIR" ]; then
  CLONE_DIR="$(grep -E '^AGENTS_CLONE_DIR=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
  T2_VENDOR_DIR="${CLONE_DIR:-$HOME/.acb/agents}/vendor/t2-react"
fi
mkdir -p "$T2_VENDOR_DIR"
printf '%s\n' \
  '{ "name": "cc-app-workshop-t2-vendor", "private": true,' \
  '  "dependencies": { "react": "18.3.1", "react-dom": "18.3.1", "esbuild": "0.24.0", "lucide-react": "1.17.0" } }' \
  > "$T2_VENDOR_DIR/package.json"
if [ ! -d "$T2_VENDOR_DIR/node_modules/react" ] || [ ! -d "$T2_VENDOR_DIR/node_modules/esbuild" ] || [ ! -d "$T2_VENDOR_DIR/node_modules/lucide-react" ]; then
  (cd "$T2_VENDOR_DIR" && npm install --no-audit --no-fund --omit=dev) \
    && echo "    + T2 vendor cache installed ($T2_VENDOR_DIR)" \
    || echo "    ! T2 vendor install failed — T2 (React) apps will fail to build; T1 apps unaffected"
else
  echo "    vendor cache already present, skipping install ($T2_VENDOR_DIR)"
fi

# ── Build a Next.js app WITHOUT taking it down ────────────────────
#
# 🔴 **`rm -rf .next` BEFORE a build is an outage, and after a failed build it
# is a permanent one.** This function replaced that pattern on 2026-09-01.
#
# Measured that morning: `app.metorite.com` answered **HTTP 500 on every route**
# — including `/` — while `acb-workbench` restart-looped every 5 seconds with
# "Could not find a production build in the '.next' directory". The build had
# not failed. It was simply still running, and the directory the live server
# serves from had already been deleted to make room for it. Two Next builds run
# per deploy, so that window is minutes.
#
# ⚠️ **Nothing alarmed.** `systemctl is-active` was true the whole time (the
# unit restarts, so it is always "starting"), the gateway answered 200, and
# `vps-health.yml` counted an HTTP 500 as proof of life. This is the same
# green-signal-about-the-machinery failure the Operator Console block below
# describes, wearing its fourth hat.
#
# The fix: build into a staging directory, and rename it onto `.next` only
# after the build produces a BUILD_ID. Downtime becomes one restart instead of
# one build, and a FAILED build changes nothing at all — the app keeps serving
# the previous build while the deploy exits non-zero and says so.
#
# ⚠️ The swap is a RENAME, never a copy. A rename is atomic within a
# filesystem; a copy is not, and a server that reloads mid-copy reads half a
# build.
#
# ⚠️ The clean-build requirement has NOT gone away — a kept `.next` produces
# stale client-reference-manifest errors under Turbopack. The staging directory
# satisfies it for free: it is removed before every build, so it is always
# empty, and `.next` is never written in place.
#
# Requires `distDir: process.env.NEXT_DIST_DIR || ".next"` in the app's
# next.config — both apps carry it, and `test_deploy_next_build_swap.py`
# fails if either loses it.
NEXT_BUILD_HEAP_MB="${NEXT_BUILD_HEAP_MB:-1024}"
build_next_staged() {
  name="$1"
  rm -rf .next.staging .next.previous
  # A non-zero exit here propagates under `set -e` with `.next` untouched.
  NEXT_DIST_DIR=".next.staging" \
    NODE_OPTIONS="--max-old-space-size=$NEXT_BUILD_HEAP_MB" npm run build
  # BUILD_ID is the file `next start` looks for and fails on. Checking it
  # rather than only the exit code is the difference between "the build
  # command returned 0" and "there is a build here" — this repo has been
  # burned by that distinction three times.
  if [ ! -f .next.staging/BUILD_ID ]; then
    echo "    ! $name: no BUILD_ID in .next.staging — keeping the running build"
    return 1
  fi
  if [ -d .next ]; then mv .next .next.previous; fi
  mv .next.staging .next
  rm -rf .next.previous
  echo "    $name: new build swapped in"
}

echo "==> Rebuilding + restarting workbench (Next.js)"
cd "$APP_DIR/workbench/control_plane"
if [ -f package-lock.json ] || [ -f package.json ]; then
  npm ci --prefer-offline 2>/dev/null || npm install
  build_next_staged "workbench"
fi
# Reload systemd unit in case acb-workbench.service changed (adds PATH for uv etc.)
sudo cp "$APP_DIR/deploy/hostinger/acb-workbench.service" /etc/systemd/system/acb-workbench.service
sudo systemctl daemon-reload
# See the gateway note above — enable so the workbench survives reboots.
sudo systemctl enable acb-workbench >/dev/null 2>&1 || true
sudo systemctl restart acb-workbench
sleep 3
systemctl is-active --quiet acb-workbench || { echo "WORKBENCH FAILED TO START"; exit 1; }
echo "Workbench is active"

# ── Operator Console (Next.js, staff-only) ────────────────────────
#
# 🔴 **THIS BLOCK EXISTS BECAUSE THE CONSOLE DRIFTED FOR TWO DAYS.** Measured
# 2026-08-28: `operator.metorite.com` served, and both `/models` (merged
# 2026-08-27) and `/providers` (merged 2026-08-28) answered **404**. The site
# was up, Caddy routed it, and nothing in this script rebuilt it — so every
# operator feature merged to `main` stayed on `main`.
#
# ⚠️ **The unit file is NOT in this repo.** Every other service here is copied
# from `deploy/hostinger/*.service`; this one was stood up by hand on the box,
# so there is nothing to `cp`. That is a real gap and it is recorded in the
# handoff queue — until it closes, this block manages an artefact it cannot
# reproduce.
#
# ⚠️ **Deliberately AFTER the workbench.** Customer surfaces come up first, so
# a failure here fails the job loudly without having delayed a single customer
# request. That ordering is the whole reason this is safe to fail hard on.
#
# Conditional on the unit being enabled — the same test the Console block uses,
# so a box that does not run the operator console skips this silently rather
# than failing. Override the name if it runs under a different one.
OC_UNIT="${OPERATOR_CONSOLE_UNIT:-acb-operator-console}"
OC_DIR="$APP_DIR/workbench/operator_console"

if systemctl is-enabled --quiet "$OC_UNIT" 2>/dev/null; then
  echo "==> Rebuilding + restarting Operator Console ($OC_UNIT)"
  cd "$OC_DIR"
  if [ -f package-lock.json ] || [ -f package.json ]; then
    npm ci --prefer-offline 2>/dev/null || npm install
    # Same staged build as the workbench, for the same reason and with the same
    # guarantee: the console keeps serving its previous build until a new one
    # exists. See `build_next_staged` above.
    build_next_staged "operator console"
  fi
  sudo systemctl restart "$OC_UNIT"
  sleep 3
  if ! systemctl is-active --quiet "$OC_UNIT"; then
    echo "OPERATOR CONSOLE FAILED TO START"
    sudo journalctl -u "$OC_UNIT" --no-pager -n 40 || true
    exit 1
  fi
  echo "    Operator Console is active"

  # 🔴 **ACTIVE IS NOT SERVED, AND THE DIFFERENCE COST TWO DAYS.**
  #
  # Measured 2026-08-28 and again 2026-08-29: the unit was `active (running)`
  # for the whole period `/providers` and `/models` answered 404. `is-active`
  # reports that a process holds the port. It reports NOTHING about which build
  # that process loaded, and a Next.js server started against a stale `.next`
  # holds the port perfectly while serving last week's routes.
  #
  # That is this repo's most expensive recurring failure wearing its third hat:
  # a green signal that describes the machinery instead of the delivery. So
  # probe the routes, and fail the deploy when one is missing.
  #
  # ⚠️ The route list is DERIVED FROM THE SOURCE TREE, never written down here.
  # The whole defect is a thing that was correct when written and silently
  # stopped matching the tree. A hardcoded list would rot the same way.
  #
  # ⚠️ Only 404 is a failure. `/providers` with no session answers a redirect to
  # `/login`, and that IS a pass — it proves the route compiled. Treating a
  # redirect as failure would make this gate refuse a correct deploy.
  OC_PORT="${OPERATOR_CONSOLE_PORT:-3002}"
  oc_stale=""
  for oc_page in "$OC_DIR"/src/app/*/page.tsx; do
    [ -f "$oc_page" ] || continue
    oc_route="$(basename "$(dirname "$oc_page")")"
    oc_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
      "http://127.0.0.1:$OC_PORT/$oc_route" 2>/dev/null || echo 000)"
    [ "$oc_code" = "404" ] && oc_stale="$oc_stale /$oc_route"
  done
  if [ -n "$oc_stale" ]; then
    echo "OPERATOR CONSOLE IS SERVING A STALE BUILD — 404 on:$oc_stale"
    echo "    The unit is active and these routes exist in src/app/."
    echo "    The build did not take. Do NOT record this deploy as successful."
    exit 1
  fi
  echo "    Operator Console serves every route in src/app/"
else
  echo "    $OC_UNIT is not enabled here — skipping the Operator Console."
  echo "    If it runs under another name, set OPERATOR_CONSOLE_UNIT in .env"
  echo "    on the box. If it runs on this host at all, it is NOT being"
  echo "    rebuilt by this script and it WILL drift (see HANDOFF H-75)."
fi

echo "==> Ensuring Caddy is serving"
# Caddy fronts BOTH public hostnames — if it is down, the whole app
# is unreachable no matter how healthy gateway/workbench are. The
# old `reload || true` silently swallowed a chronically failing
# reload (broken on-disk config), which turns any later Caddy
# restart into a full outage. Recover deterministically instead:
#   1. surface Caddy's current state + recent journal in the log;
#   2. validate the live config — if invalid, back it up and
#      reinstall the repo's known-good Caddyfile;
#   3. reload if running, restart if dead;
#   4. fail LOUDLY if Caddy still is not active.
CADDY_LIVE=/etc/caddy/Caddyfile
CADDY_REPO="$APP_DIR/deploy/hostinger/caddy/Caddyfile"
echo "    caddy state: $(systemctl is-active caddy 2>&1 || true)"
sudo journalctl -u caddy --no-pager -n 25 || true
if ! sudo caddy validate --config "$CADDY_LIVE"; then
  echo "    ! live Caddyfile INVALID — reinstalling repo config (backup kept)"
  sudo cp -a "$CADDY_LIVE" "$CADDY_LIVE.broken.$(date +%s)" || true
  sudo install -m 0644 "$CADDY_REPO" "$CADDY_LIVE"
  sudo caddy validate --config "$CADDY_LIVE"
fi
sudo systemctl enable caddy >/dev/null 2>&1 || true
if systemctl is-active --quiet caddy; then
  sudo systemctl reload caddy || sudo systemctl restart caddy
else
  echo "    caddy is DOWN — restarting"
  sudo systemctl restart caddy
fi
sleep 2
systemctl is-active --quiet caddy || {
  echo "CADDY FAILED TO START"
  sudo journalctl -u caddy --no-pager -n 40 || true
  exit 1
}
echo "Caddy is active"

echo "==> Installing health watchdog (systemd timer)"
# Self-heals services between deploys and captures network forensics
# every 10 min. Deliberately installed here so it can never drift
# from the repo. See deploy/hostinger/health-watchdog.sh.
# The script is already in place via git reset; just make sure it is
# executable. (Do NOT `install` it onto itself — src == dest is an
# error, which is how it ended up non-executable the first time.)
sudo chmod 0755 "$APP_DIR/deploy/hostinger/health-watchdog.sh" || true
sudo cp "$APP_DIR/deploy/hostinger/acb-health-watchdog.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/hostinger/acb-health-watchdog.timer"   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now acb-health-watchdog.timer || true
if systemctl is-active --quiet acb-health-watchdog.timer; then
  echo "    watchdog timer active — next: $(systemctl show acb-health-watchdog.timer -p NextElapseUSecRealtime --value 2>/dev/null || echo '?')"
else
  # Non-fatal: a missing watchdog must never block shipping the app.
  echo "    ! watchdog timer NOT active (non-fatal)"
  sudo systemctl status acb-health-watchdog.timer --no-pager 2>&1 | head -15 || true
fi

echo "==> Syncing systemd units (BO-23)"
# The repo is the source of truth for the box's units; without this step a
# unit added there only reaches the machine if somebody remembers to copy it
# by hand — which is how acb-backup.timer sat unscheduled while the tooling
# existed. PR #380 added this loop to deploy/hostinger/deploy.sh, but that is
# the MANUAL runbook script: both automated delivery paths (the workflow and
# the box's poller) run THIS file, so the loop must live here to run at all.
#
# Files only, plus `enable --now` for TIMERS. Services are deliberately left
# alone: restarting the gateway is this script's own earlier step, and a
# surprise restart here would be harder to explain than a stale unit file.
UNITS_CHANGED=0
for unit in "$APP_DIR"/deploy/hostinger/*.service "$APP_DIR"/deploy/hostinger/*.timer; do
  [ -e "$unit" ] || continue
  name="$(basename "$unit")"
  if ! sudo cmp -s "$unit" "/etc/systemd/system/$name"; then
    sudo install -m 0644 "$unit" "/etc/systemd/system/$name"
    echo "    installed $name"
    UNITS_CHANGED=1
  fi
done
if [ "$UNITS_CHANGED" = "1" ]; then
  sudo systemctl daemon-reload
fi
for timer in "$APP_DIR"/deploy/hostinger/*.timer; do
  [ -e "$timer" ] || continue
  # A managed-DB box (PG_MODE=local, lifted from .env above) must not run the
  # nightly local dump: with no EnvironmentFile the unit defaults to
  # PG_MODE=docker and dumps the EMPTY local container — which passes
  # --verify-restore and becomes a green false restore point. Provider PITR
  # is the restore path there (docs/EXTERNAL_POSTGRES.md). Actively disable
  # rather than skip, or the next hand-enable survives every deploy.
  if [ "$(basename "$timer")" = "acb-backup.timer" ] && [ "${PG_MODE:-docker}" = "local" ]; then
    sudo systemctl disable --now acb-backup.timer >/dev/null 2>&1 || true
    echo "    acb-backup.timer left disabled (managed database; provider PITR is the restore path)"
    continue
  fi
  sudo systemctl enable --now "$(basename "$timer")" >/dev/null 2>&1 \
    || echo "    !! could not enable $(basename "$timer") — check: systemctl status $(basename "$timer")"
done
systemctl list-timers --no-pager 'acb-*' 2>/dev/null | head -5 || true

echo "==> Running infra health probe"
cd "$APP_DIR"
uv run python scripts/check_infra.py || {
  echo "INFRA PROBE FAILED — check logs: docker compose -f infra/docker-compose.yml logs --tail=100"
  exit 1
}

echo "==> Deployment complete"
