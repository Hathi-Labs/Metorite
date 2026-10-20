#!/usr/bin/env bash
# Run the pr-check suite in the preset `metorite-ci` distro, all jobs at once.
#
#   bash scripts/ci_pc/run_suite.sh [--e2e] [--no-e2e] [--shards N]
#
# SHARDS. pr-check splits the unit suite 6 ways, one runner each. This PC can
# run more at once, so the default here is 12 when the distro has 16 or more
# cores (6 below that). The split is the same pytest-split by measured time,
# only finer. Measured 2026-10-20: 6 shards on 8 cores took about 16 minutes
# each. Each shard is a pod of two Postgres containers and one job, about
# 2 GB, so 12 shards need about 24 GB of the distro's memory.
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

E2E=auto
SHARDS=$(( $(nproc) >= 16 ? 12 : 6 ))
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
echo "local CI for $SHA, $SHARDS unit shards. Logs: $RUN"

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
  docker run -d --name "$1" "${net[@]}" -e POSTGRES_USER="$4" -e POSTGRES_PASSWORD="$5" -e POSTGRES_DB="$6" \
    --tmpfs /var/lib/postgresql/data "$2" -c port="$3" >/dev/null || return 1
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
    docker run --rm --name "ci-job-$label-$TAG" "${net[@]}" \
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

if [ "$E2E" = auto ]; then
  git diff --quiet "$(git merge-base origin/main HEAD)" HEAD -- workbench/control_plane && E2E=no || E2E=yes
fi
if [ "$E2E" = yes ]; then
  # One process, not three shards: three `next dev` servers in one app folder
  # fight over `.next`. CI=true (set in the image) turns on the one retry.
  printf 'cd workbench/control_plane && npx playwright test --project=ci\n' > "$RUN/e2e.sh"
  ( s=$(date +%s)
    docker run --rm --name "ci-job-e2e-$TAG" -v "$HOME:$HOME" "$IMAGE" bash "$RUN/e2e.sh" > "$RUN/e2e.log" 2>&1
    echo "$? $(( $(date +%s) - s ))" > "$RUN/e2e.rc" ) &
fi
wait

# The guard reads every shard's junit, so it runs after them.
shards_ok=success
for i in $(seq 1 "$SHARDS"); do [ "$(cut -d' ' -f1 "$RUN/shard-$i.rc")" = 0 ] || shards_ok=failure; done
job unit-tests "" unit-tests --ctx "needs.test.result=$shards_ok"
wait
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
    grep -hE "^FAILED |^ERROR |::error::" "$RUN/$name.log" 2>/dev/null | cut -c1-160 | head -4 | sed "s/^/    /"
  fi
  # Advisory steps inside a job (process fences) print ::warning:: and pass.
  grep -h "^::warning::" "$RUN/$name.log" 2>/dev/null | cut -c1-160 | head -2 | sed "s/^/    /"
done
echo
echo "total $(( $(date +%s) - T0 ))s. Logs: $RUN"
[ $fail = 0 ] && echo "local CI: PASS ($SHA)" || echo "local CI: FAIL ($SHA). Read the log of each FAIL job above."
exit $fail
