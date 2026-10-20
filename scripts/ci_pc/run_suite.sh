#!/usr/bin/env bash
# Run the pr-check suite in the preset `metorite-ci` distro, all jobs at once.
#
#   bash scripts/ci_pc/run_suite.sh [--e2e] [--no-e2e] [--shards N]
#
# SHARDS. pr-check splits the unit suite 6 ways, one runner each. Here the
# count comes from FREE MEMORY, not from cores.
#
# 🔴 2026-10-20: 12 shards (picked from 20 cores) plus e2e and the dev stack
# ran the WSL2 VM out of its 32 GB. The VM stopped answering
# (HCS_E_CONNECTION_TIMEOUT), git died with "Bus error", and Docker Desktop
# hung, for every session on the PC. A shard is a pytest process and two
# Postgres servers whose data sits in RAM (tmpfs), about 2.5 GB. So:
#   * SHARDS = (MemAvailable - 10 GB) / 2.5 GB, from 2 to 12, and at most
#     half the cores. 10 GB stays for e2e, the other jobs and the dev stack;
#   * every container has a hard memory cap, so a runaway job is killed
#     and reported FAIL instead of freezing the VM.
# Measured: 6 shards took 17.6 min and 12 took 17.0. One 10-minute test file
# (test_crm_field_studio_r8.py) is the floor, so more shards buy little.
#
# You do not call this by hand. `scripts/ci_local.sh` (from Windows) sends the
# commit here, checks it out, and runs this file FROM THAT COMMIT.
#
# Every job's commands come from .github/workflows/pr-check.yml, through
# steps.py. This file adds only what GitHub gives each job for free:
#
# ⚠️ A PRIVATE NETWORK PER JOB. All WSL2 distros on this PC share one
# network, and Docker Desktop's dev stack publishes Postgres on *:5432 and
# Redis on *:6379 there. The first local run (2026-10-19) let tests that fall
# back to localhost:5432 reach the owner's real dev database. So each job runs
# in a container (scripts/ci_pc/Dockerfile), where localhost is private:
#   * a job with database services runs in a "pod": its Postgres containers
#     share one network namespace with the job, on the EXACT ports pr-check
#     names (5433 and 5434 for a shard, 5432 for the ladder). So `localhost`
#     means what it means on GitHub, and no DSN is rewritten;
#   * every other job runs in a container of its own.
set -u
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"

E2E=no   # opt-in with --e2e: see "The browser suite" below
avail_gb=$(awk '/^MemAvailable:/ {print int($2 / 1048576)}' /proc/meminfo)
# Below 8 GB free, even 2 shards plus the other jobs could stall the VM that
# Docker Desktop and every distro share. Refuse, and say why.
if [ "$avail_gb" -lt 8 ]; then
  echo "local CI: the WSL VM has only ${avail_gb} GB free. Something else holds it." >&2
  echo "Wait for it, or check with: bash scripts/ci_pc/doctor.sh (from Windows)" >&2
  exit 3
fi
SHARDS=$(( (avail_gb - 10) * 2 / 5 ))
[ "$SHARDS" -gt $(( $(nproc) / 2 )) ] && SHARDS=$(( $(nproc) / 2 ))
[ "$SHARDS" -gt 12 ] && SHARDS=12
[ "$SHARDS" -lt 2 ] && SHARDS=2
while [ $# -gt 0 ]; do
  case "$1" in
    --e2e) E2E=yes; shift ;;
    --no-e2e) E2E=no; shift ;;
    --shards) SHARDS="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ "$SHARDS" -ge 1 ] 2>/dev/null || { echo "--shards needs a number" >&2; exit 2; }

SHA="$(git rev-parse --short HEAD)"
TAG="$(date +%H%M%S)"
RUN="$HOME/runs/$(date +%Y%m%d-%H%M%S)-$SHA"
mkdir -p "$RUN"
T0=$(date +%s)
echo "local CI for $SHA, $SHARDS unit shards (${avail_gb} GB free). Logs: $RUN"
# Keep the last 10 runs. Their logs and reports grew the distro's disk.
ls -dt "$HOME"/runs/*/ 2>/dev/null | tail -n +11 | xargs -r rm -rf

# ── The job image: rebuilt only when its Dockerfile changes ────────────────
IMAGE="metorite-ci-job:$(sha256sum scripts/ci_pc/Dockerfile | cut -c1-12)-$(id -u)-$(id -g)"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "building $IMAGE (once per Dockerfile change)"
  docker build -q --build-arg CI_UID="$(id -u)" --build-arg CI_GID="$(id -g)" -t "$IMAGE" scripts/ci_pc >/dev/null \
    || { echo "image build failed"; exit 1; }
fi

# ── Dependencies: only when a lockfile moved ──────────────────────────────
uv sync -q || { echo "uv sync failed"; exit 1; }
for app in control_plane operator_console; do
  lock="$(sha256sum "workbench/$app/package-lock.json" | cut -d' ' -f1)"
  if [ "$(cat "workbench/$app/node_modules/.ci-lock" 2>/dev/null)" != "$lock" ]; then
    echo "npm ci: $app (its lockfile moved)"
    (cd "workbench/$app" && npm ci --no-audit --no-fund --loglevel=error && echo "$lock" > node_modules/.ci-lock) \
      || { echo "npm ci failed for $app"; exit 1; }
  fi
done
(cd workbench/control_plane && npx playwright install chromium >/dev/null 2>&1)

# ── Containers, and their cleanup ─────────────────────────────────────────
CONTAINERS=()
cleanup() { [ ${#CONTAINERS[@]} -gt 0 ] && docker rm -f "${CONTAINERS[@]}" >/dev/null 2>&1; true; }
trap cleanup EXIT

# pg <name> <image> <port> <user> <password> <db> [<pod>]
# The first Postgres of a pod owns the network. The next one joins it.
pg() {
  local net=() ; [ -n "${7:-}" ] && net=(--network "container:$7")
  # Capped: the data is in RAM, so an uncapped server can grow until the VM
  # stops. A ladder database is well under 1 GB.
  docker run -d --name "$1" "${net[@]}" -e POSTGRES_USER="$4" -e POSTGRES_PASSWORD="$5" -e POSTGRES_DB="$6" \
    --memory 1536m --tmpfs /var/lib/postgresql/data:size=1g "$2" -c port="$3" >/dev/null || return 1
  CONTAINERS+=("$1")
  for _ in $(seq 1 60); do docker exec "$1" pg_isready -q -p "$3" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "Postgres $1 did not start"; return 1
}

DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
# job <label> <pod or ""> <steps.py args...>
job() {
  local label="$1" pod="$2"; shift 2
  .venv/bin/python scripts/ci_pc/steps.py "$@" > "$RUN/$label.sh" || { echo "1 0" > "$RUN/$label.rc"; return; }
  local net=(); [ -n "$pod" ] && net=(--network "container:$pod")
  (
    s=$(date +%s)
    docker run --rm --name "ci-job-$label-$TAG" "${net[@]}" --memory 4g --memory-swap 4g \
      -v "$HOME:$HOME" -v /var/run/docker.sock:/var/run/docker.sock --group-add "$DOCKER_GID" \
      "$IMAGE" bash "$RUN/$label.sh" > "$RUN/$label.log" 2>&1
    echo "$? $(( $(date +%s) - s ))" > "$RUN/$label.rc"
  ) &
}

# ── Services, in pods, on the ports pr-check uses ─────────────────────────
for i in $(seq 1 "$SHARDS"); do
  pg "ci-cc-$i-$TAG" postgres:16 5433 cc cc cc_platform || exit 1
  pg "ci-tn-$i-$TAG" pgvector/pgvector:pg16 5434 acb acb acb_tenant "ci-cc-$i-$TAG" || exit 1
done
pg "ci-ladder-$TAG" pgvector/pgvector:pg16 5432 postgres ladder postgres || exit 1
pg "ci-backup-$TAG" postgres:16 5432 postgres rehearsal postgres || exit 1

# ── Jobs ──────────────────────────────────────────────────────────────────
rm -rf junit
for i in $(seq 1 "$SHARDS"); do
  job "shard-$i" "ci-cc-$i-$TAG" test --ctx matrix.shard=$i "--replace=--splits 6=>--splits $SHARDS"
done
job lint "" lint
job migrations "ci-ladder-$TAG" migrations
job backup-restore "ci-backup-$TAG" backup-restore
job frontend "" frontend
job operator-console "" frontend-operator
job secret-scan "" secret-scan --ctx "github.event.pull_request.base.sha=$(git merge-base origin/main HEAD)"
# Caddy validates in its own image through the Docker socket: no pod needed,
# and its `$PWD` mount must be a path the Docker engine sees (it is: /home/ci).
job caddy "" caddy

wait

# The guard reads every shard's junit, so it runs after them.
shards_ok=success
for i in $(seq 1 "$SHARDS"); do [ "$(cut -d' ' -f1 "$RUN/shard-$i.rc")" = 0 ] || shards_ok=failure; done
job unit-tests "" unit-tests --ctx "needs.test.result=$shards_ok"
wait

# ── The browser suite: opt-in, AFTER the unit phase, in three parts ────────
#
# 🔴 2026-10-20: one `next dev` for the whole suite grew past its 6 GB cap
# (kernel log: "Killed process next-server ... anon-rss 5.5 GB"). Every test
# after that read ERR_CONNECTION_REFUSED: 409 of 482 "failed" in four runs
# that passed on GitHub. GitHub splits the suite three ways, each on its own
# 16 GB runner. So here:
#   * three parts, one after the other. Each part starts a FRESH dev server
#     and stops it, so the memory never piles up. Three at once would fight
#     over one `.next` folder;
#   * after the unit phase, so the two never compete for memory;
#   * opt-in (`--e2e`). It adds about 20 minutes, and GitHub runs it on
#     every PR that touches workbench/control_plane.
if [ "$E2E" = yes ]; then
  printf '%s\n' 'cd workbench/control_plane' 'rc=0' \
    'for p in 1 2 3; do echo "== part $p of 3"; npx playwright test --project=ci --shard=$p/3 || rc=1; done' \
    'exit $rc' > "$RUN/e2e.sh"
  s=$(date +%s)
  docker run --rm --name "ci-job-e2e-$TAG" --memory 10g --memory-swap 10g -v "$HOME:$HOME" "$IMAGE" \
    bash "$RUN/e2e.sh" > "$RUN/e2e.log" 2>&1
  echo "$? $(( $(date +%s) - s ))" > "$RUN/e2e.rc"
fi
# Keep this run's reports with its logs. The next run cleans the checkout,
# and the reports are where the slow tests show (per-test time).
cp -r junit "$RUN/junit" 2>/dev/null || true

# ── Summary ───────────────────────────────────────────────────────────────
ADVISORY=" secret-scan "   # continue-on-error in pr-check
fail=0
printf '\n%-18s %-8s %s\n' JOB RESULT SECONDS
for f in "$RUN"/*.rc; do
  name="$(basename "$f" .rc)"; read -r rc secs < "$f"
  if [ "$rc" = 0 ]; then res=pass
  elif [[ "$ADVISORY" == *" $name "* ]]; then res=warn
  else res=FAIL; fail=1
  fi
  printf '%-18s %-8s %s\n' "$name" "$res" "$secs"
  if [ "$res" = FAIL ]; then
    # pytest -v prints the verdict at the END of a line ("... FAILED [ 40%]").
    # A test NAME can hold the word ("pg_restore FAILED in ..."), so match
    # the verdict only where pytest puts it. Skip the RERUN lines.
    grep -hE " (FAILED|ERROR)( \[ *[0-9]+%\])?$|::error::|^ +[0-9]+ failed$" "$RUN/$name.log" 2>/dev/null \
      | grep -v RERUN | cut -c1-160 | head -4 | sed "s/^/    /"
  fi
  # Advisory steps inside a job (process fences) print ::warning:: and pass.
  grep -h "^::warning::" "$RUN/$name.log" 2>/dev/null | cut -c1-160 | head -2 | sed "s/^/    /"
done
echo
echo "total $(( $(date +%s) - T0 ))s. Logs: $RUN"
[ $fail = 0 ] && echo "local CI: PASS ($SHA)" || echo "local CI: FAIL ($SHA). Read the log of each FAIL job above."
exit $fail
