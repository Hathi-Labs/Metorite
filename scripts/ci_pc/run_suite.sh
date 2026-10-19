#!/usr/bin/env bash
# Run the pr-check suite in the preset `metorite-ci` distro, all jobs at once.
#
#   bash scripts/ci_pc/run_suite.sh [--e2e] [--no-e2e]
#
# You do not call this by hand. `scripts/ci_local.sh` (from Windows) sends the
# commit here, checks it out, and runs this file FROM THAT COMMIT.
#
# Every job's commands come from .github/workflows/pr-check.yml, through
# steps.py. This file adds only what GitHub's runners give each job for free:
# its own databases, on ports that do not collide, because here every job
# shares one network.
#
# Ports: shard N uses 66N0+ (console) and 67N0+ (tenant). The ladder replay
# uses 6800 and the backup rehearsal 6801. All bind to 127.0.0.1 only.
set -u
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"

E2E=auto
for a in "$@"; do
  case "$a" in
    --e2e) E2E=yes ;;
    --no-e2e) E2E=no ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

SHA="$(git rev-parse --short HEAD)"
RUN="$HOME/runs/$(date +%Y%m%d-%H%M%S)-$SHA"
mkdir -p "$RUN"
T0=$(date +%s)
echo "local CI for $SHA. Logs: $RUN"

# ── Dependencies: only when a lockfile moved ──────────────────────────────
uv sync -q || { echo "uv sync failed"; exit 1; }
export UV_NO_SYNC=1   # the parallel jobs below must not race to re-sync
for app in control_plane operator_console; do
  lock="$(sha256sum "workbench/$app/package-lock.json" | cut -d' ' -f1)"
  if [ "$(cat "workbench/$app/node_modules/.ci-lock" 2>/dev/null)" != "$lock" ]; then
    echo "npm ci: $app (its lockfile moved)"
    (cd "workbench/$app" && npm ci --no-audit --no-fund --loglevel=error && echo "$lock" > node_modules/.ci-lock) \
      || { echo "npm ci failed for $app"; exit 1; }
  fi
done
# The browser revision follows the lockfile. A no-op when it is present.
(cd workbench/control_plane && npx playwright install chromium >/dev/null 2>&1)

# ── Databases: one private server per job, on tmpfs (fast, and thrown away) ─
CONTAINERS=()
pg() {  # name image port user password db
  docker rm -f "$1" >/dev/null 2>&1
  docker run -d --name "$1" -e POSTGRES_USER="$4" -e POSTGRES_PASSWORD="$5" -e POSTGRES_DB="$6" \
    -p "127.0.0.1:$3:5432" --tmpfs /var/lib/postgresql/data "$2" >/dev/null || return 1
  CONTAINERS+=("$1")
}
cleanup() { [ ${#CONTAINERS[@]} -gt 0 ] && docker rm -f "${CONTAINERS[@]}" >/dev/null 2>&1; }
trap cleanup EXIT

for i in 1 2 3 4 5 6; do
  pg "ci-cc-$i" postgres:16 $((6600 + i)) cc cc cc_platform
  pg "ci-tn-$i" pgvector/pgvector:pg16 $((6700 + i)) acb acb acb_tenant
done
pg ci-ladder pgvector/pgvector:pg16 6800 postgres ladder postgres
pg ci-backup postgres:16 6801 postgres rehearsal postgres
for c in "${CONTAINERS[@]}"; do
  for _ in $(seq 1 60); do docker exec "$c" pg_isready -q >/dev/null 2>&1 && break; sleep 1; done
done

# ── Jobs ──────────────────────────────────────────────────────────────────
STEPS=".venv/bin/python scripts/ci_pc/steps.py"
job() {  # name, then the steps.py arguments
  local name="$1"; shift
  $STEPS "$@" > "$RUN/$name.sh" || { echo "1 0" > "$RUN/$name.rc"; return; }
  ( s=$(date +%s); bash "$RUN/$name.sh" > "$RUN/$name.log" 2>&1; echo "$? $(( $(date +%s) - s ))" > "$RUN/$name.rc" ) &
}

rm -rf junit
for i in 1 2 3 4 5 6; do
  job "shard-$i" test --ctx matrix.shard=$i \
    --set "CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:$((6600 + i))/cc_platform" \
    --set "TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:$((6700 + i))/acb_tenant"
done
job lint lint
job migrations migrations --set PGHOST=127.0.0.1 --set PGPORT=6800
job backup-restore backup-restore --set PGHOST=127.0.0.1 --set PGPORT=6801
job frontend frontend
job operator-console frontend-operator
job caddy caddy
job secret-scan secret-scan --ctx "github.event.pull_request.base.sha=$(git merge-base origin/main HEAD)"

if [ "$E2E" = auto ]; then
  git diff --quiet "$(git merge-base origin/main HEAD)" HEAD -- workbench/control_plane && E2E=no || E2E=yes
fi
if [ "$E2E" = yes ]; then
  # One process, not three shards: three `next dev` servers in one app folder
  # fight over `.next`. CI=1 turns on the one retry pr-check allows.
  ( s=$(date +%s); (cd workbench/control_plane && CI=1 npx playwright test --project=ci) > "$RUN/e2e.log" 2>&1
    echo "$? $(( $(date +%s) - s ))" > "$RUN/e2e.rc" ) &
fi
wait

# The guard reads every shard's junit, so it runs after them.
shards_ok=success
for i in 1 2 3 4 5 6; do [ "$(cut -d' ' -f1 "$RUN/shard-$i.rc")" = 0 ] || shards_ok=failure; done
job unit-tests unit-tests --ctx "needs.test.result=$shards_ok"
wait

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
  # Advisory steps inside a job (process fences) print ::warning:: and pass.
  grep -h "::warning::" "$RUN/$name.log" 2>/dev/null | sed "s/^/  $name: /" | head -3
done
echo
echo "total $(( $(date +%s) - T0 ))s. Logs: $RUN"
[ $fail = 0 ] && echo "local CI: PASS ($SHA)" || echo "local CI: FAIL ($SHA). Read the log of each FAIL job above."
exit $fail
