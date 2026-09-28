# shellcheck shell=bash
# H-142 — how the CI deploy reaches the box, and what it does when it cannot.
#
# SOURCED, never executed. `.github/workflows/deploy.yml` sources this file in
# two jobs: `deploy`, which connects and applies, and `pull-delivery`, which
# runs on a FRESH runner when `deploy` could not connect. A file, not YAML,
# for the reason WS-25 D1 gave for `vps_apply.sh`: shellcheck reads it, and
# `tests/unit/test_deploy_reach.py` runs it against a fake `ssh` and `curl`.
#
# ── What was measured (2026-09-20 to 2026-09-28, 200 deploy runs) ───────────
#
#   * 9 runs went red on `ssh exited 255`, `Connection timed out`. In every
#     one, ALL THREE rounds failed the same way, 27 of 27 connects.
#   * 163 green runs in the same window hold ZERO `ssh exited 255` rounds. So
#     the retry ladder has never once outlasted a connect failure.
#   * The box's sshd logged NO connection from the runner in the window we
#     read (21:04 to 21:09, 2026-09-27). The packets did not arrive.
#   * A DIFFERENT runner connected 10 s after the last failed round, twice
#     (21:08:58 on 2026-09-27, and 10:48 on 2026-09-28). The fault follows the
#     runner, not the box.
#   * The box served the commit anyway in all 9 cases. `acb-pull.timer`
#     applied it 7 times, and finished 3 to 4 minutes after it saw the commit.
#     In the other 2, an apply of a later commit already carried it.
#
# So a longer wait on the SAME runner buys little, and the job's verdict must
# not rest on it. The design is two parts:
#
#   1. `wait_for_ssh` — a cheap `ssh … true` probe with backoff, bounded by
#      CONNECT_BUDGET for the WHOLE job. A connect failure never spends an
#      apply round. The apply itself is unchanged, and it still takes the
#      deploy lock on the box (#484).
#   2. `await_pull_delivery` — when no connect succeeds, a FRESH runner asks
#      the box over HTTPS whether it delivered this commit by itself. It
#      accepts only the served evidence. See that function.
#
# Knobs, with the defaults the workflow uses. Tests set smaller values.
: "${CONNECT_TIMEOUT:=30}"      # ssh ConnectTimeout, for the probe and the apply
: "${CONNECT_BUDGET:=300}"      # seconds of connect trouble one job may spend
: "${CONNECT_BACKOFF_MAX:=60}"  # the longest wait between two probes
: "${PULL_WAIT:=900}"           # how long the fresh runner waits for the pull path
: "${PULL_POLL:=30}"            # seconds between two reads of /version
: "${SSH_KEY_FILE:=$HOME/.ssh/deploy_key}"
: "${SSH_PORT:=22}"

CONNECT_SPENT=0

# The apply script's FIRST line. `vps_apply.sh` prints it before it does
# anything, so its absence proves the apply never started.
APPLY_START_LINE="==> Taking the deploy lock"
# What `vps_apply.sh` prints when it waited for the deploy lock and gave up.
# Nothing was applied in that round, and another deploy held the box.
APPLY_LOCK_BUSY_LINE="and it is still busy. This round did NOTHING"

# One set of ssh options for the probe AND the apply, so the two cannot drift.
# ServerAlive keeps a held session alive through NAT and idle culling.
CI_SSH_OPTS=(
  -i "$SSH_KEY_FILE" -p "$SSH_PORT"
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout="$CONNECT_TIMEOUT" -o ServerAliveInterval=15
  -o ServerAliveCountMax=8 -o BatchMode=yes
)

_now() { date +%s; }

# True when ssh's own words (on stdin) name a NETWORK fault: the packets did
# not get through. An auth failure or a bad host key is NOT one of these, and
# no amount of waiting fixes it, so it must go red at once.
ssh_network_fault() {
  grep -qiE 'connection timed out|operation timed out|connection refused|no route to host|network is unreachable|connection reset|connection closed by|kex_exchange_identification|could not resolve hostname|temporary failure in name resolution'
}

# True when the apply log ($1) shows that `vps_apply.sh` started.
apply_started() { grep -qF "$APPLY_START_LINE" "$1" 2>/dev/null; }

# True when the apply log ($1) shows a round that waited for the lock and gave up.
apply_lock_busy() { grep -qF "$APPLY_LOCK_BUSY_LINE" "$1" 2>/dev/null; }

# Add $1 seconds to the job's connect spend. The apply calls this when its own
# ssh failed to connect, so a refunded round still costs budget and the loop
# in deploy.yml cannot spin for ever.
charge_connect() {
  local s="${1:-0}"
  [ "$s" -lt "$CONNECT_TIMEOUT" ] && s="$CONNECT_TIMEOUT"
  CONNECT_SPENT=$((CONNECT_SPENT + s))
}

connect_budget_left() { [ "$CONNECT_SPENT" -lt "$CONNECT_BUDGET" ]; }

# Probe until a no-op ssh succeeds.
#   0  ssh works now
#   1  the budget ran out, and every failure was a network fault
#   2  ssh failed for a reason that is NOT the network (key, host key, shell)
# The probe always runs once, even with no budget left, so a job that spent
# its budget early can still connect for a later round.
wait_for_ssh() {
  local attempt=0 delay=10 rc t0 err
  while :; do
    attempt=$((attempt + 1))
    t0=$(_now)
    # `|| rc=$?`, not a bare `rc=$?`: the workflow step runs under bash -e.
    rc=0
    # SSH_HOST and SSH_USER come from the workflow step's env.
    # shellcheck disable=SC2153
    err=$(timeout -k 5 $((CONNECT_TIMEOUT + 15)) \
      ssh "${CI_SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" true 2>&1 >/dev/null) || rc=$?
    CONNECT_SPENT=$((CONNECT_SPENT + $(_now) - t0))
    if [ "$rc" = 0 ]; then
      echo "  ssh connects (probe $attempt, ${CONNECT_SPENT}s of ${CONNECT_BUDGET}s connect budget spent)"
      return 0
    fi
    err=$(printf '%s' "$err" | tail -n 3)
    if ! printf '%s' "$err" | ssh_network_fault; then
      echo "  ❌ ssh FAILED for a reason that is not the network (exit $rc):"
      printf '%s\n' "$err" | sed 's/^/       /'
      echo "     Waiting cannot fix this. Check the deploy key and the box's sshd."
      return 2
    fi
    if ! connect_budget_left; then
      echo "  ssh probe $attempt: ${err:-exit $rc}"
      echo "  ⏹  connect budget spent (${CONNECT_SPENT}s of ${CONNECT_BUDGET}s)"
      return 1
    fi
    # Never sleep past the budget. The last probe then lands on its edge.
    local nap=$((CONNECT_BUDGET - CONNECT_SPENT))
    [ "$nap" -gt "$delay" ] && nap="$delay"
    echo "  ssh probe $attempt: ${err:-exit $rc} — next probe in ${nap}s (${CONNECT_SPENT}s of ${CONNECT_BUDGET}s spent)"
    sleep "$nap"
    CONNECT_SPENT=$((CONNECT_SPENT + nap))
    delay=$((delay * 2))
    [ "$delay" -gt "$CONNECT_BACKOFF_MAX" ] && delay="$CONNECT_BACKOFF_MAX"
  done
}

# True when commit $1 is commit $2, or contains it. An empty $1 is false.
commit_contains() {
  [ -n "$1" ] && [ -n "$2" ] || return 1
  [ "$1" = "$2" ] && return 0
  git merge-base --is-ancestor "$2" "$1" 2>/dev/null
}

# One field of a JSON object on stdin, or nothing. json.load rejects an HTML
# error page, so "no answer" never reads as "some commit".
json_field() {
  python3 -c 'import json,sys
try:
    v = json.load(sys.stdin).get(sys.argv[1])
except Exception:
    v = None
print(v if isinstance(v, str) else "")' "$1" 2>/dev/null
}

# The fresh runner's question: did the box deliver $1 by itself?
#
# ⚠️ RULE 8 — the served evidence, never a green tick. It passes only when ALL
# of these hold on one read:
#   1. `GET /version` `sha` is $1 or contains it. That is the checkout the
#      GATEWAY was started from, and nothing more.
#   2. `GET /version` `applied_sha` is $1 or contains it. That field is the
#      box's deploy marker, which `vps_apply.sh` writes ONLY at its last line,
#      after the workbench build and its restart. It is what speaks for the
#      WEB app. The gateway restarts BEFORE the workbench builds, so (1) alone
#      stays true when the build fails and the old web app keeps serving.
#   3. The public workbench answers 2xx or 3xx, through Caddy.
#
# What it does NOT prove: that migrations ran (the apply stops red before its
# last line if they fail, so the marker would not move), that the Operator
# Console rebuilt, or anything about a later apply that failed. It proves "a
# complete apply of this commit, or of one that contains it, finished".
#
#   0  delivered, with the evidence above
#   1  not delivered within PULL_WAIT
#   3  the box never answered /version from this runner either
await_pull_delivery() {
  local want="$1" waited=0 body sha applied wb answered=0
  echo "── Waiting up to ${PULL_WAIT}s for the box's own pull to deliver ${want:0:12} ──"
  while :; do
    git fetch -q --no-tags origin main 2>/dev/null || true
    body=$(curl -fsS --max-time 10 "$GATEWAY_URL/version" 2>/dev/null) || body=""
    sha=$(printf '%s' "$body" | json_field sha)
    applied=$(printf '%s' "$body" | json_field applied_sha)
    wb=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$WORKBENCH_URL/" 2>/dev/null) || true
    [ -n "$sha" ] && answered=1
    if commit_contains "$sha" "$want" \
       && commit_contains "$applied" "$want" \
       && printf '%s' "$wb" | grep -qE '^[23][0-9][0-9]$'; then
      echo "  ✅ delivered by the pull path: gateway on $sha, a complete apply of $applied, workbench / -> HTTP $wb"
      return 0
    fi
    echo "  not yet (gateway sha=${sha:-none} applied=${applied:-none} workbench=${wb:-000}, ${waited}s of ${PULL_WAIT}s)"
    [ "$waited" -ge "$PULL_WAIT" ] && break
    sleep "$PULL_POLL"
    waited=$((waited + PULL_POLL))
  done
  if [ "$answered" = 0 ]; then
    return 3
  fi
  return 1
}
