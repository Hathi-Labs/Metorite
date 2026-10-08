#!/usr/bin/env bash
# The gateway's stop step: let the running work finish BEFORE the stop signal.
#
# acb-gateway.service runs this as ExecStop=. systemd runs it before it sends
# SIGTERM, and the old process keeps its port the whole time. So every member
# is served as normal while this waits. See gateway/routes/drain.py.
#
# It polls GET /internal/drain until `runs` is 0, or until the bound passes.
# It ALWAYS exits 0. A stop that this script cannot read goes ahead at once:
# no answer, a refused port, a 404 from a gateway older than the route, or a
# body it cannot parse. A crashed gateway is the common case of the first one.
#
# Fence: tests/unit/test_gateway_drain.py runs this file against a real local
# HTTP server.
set -u

url="${GATEWAY_DRAIN_URL:-http://127.0.0.1:8080/internal/drain}"
wait_s="${GATEWAY_DRAIN_WAIT_SECONDS:-60}"
poll_s="${GATEWAY_DRAIN_POLL_SECONDS:-1}"
token="${GATEWAY_INTERNAL_TOKEN:-}"

# One read of the count. Prints the number, or nothing when it cannot read one.
# The token goes to curl on stdin, never on the command line, so `ps` on the
# box cannot show it.
runs_now() {
  local body
  body="$(printf 'header = "Authorization: Bearer %s"\n' "$token" \
    | curl -s -m 2 -f -K - "$url" 2>/dev/null)" || return 0
  printf '%s' "$body" | grep -o '"runs": *[0-9]*' | grep -o '[0-9]*$' | head -n 1
}

# Milliseconds since the epoch. `date +%s` counts whole seconds, so a bound read
# with it can end up to 1 s early. Bash 5 gives EPOCHREALTIME, with a point or a
# comma by locale.
now_ms() {
  local t="${EPOCHREALTIME//[.,]/}"
  echo $(( t / 1000 ))
}

start=$(now_ms)
runs="$(runs_now)"
if [ -z "$runs" ]; then
  echo "gateway-drain: no count from $url, so the stop goes ahead now"
  exit 0
fi
if [ "$runs" -eq 0 ]; then
  echo "gateway-drain: 0 runs, so the stop goes ahead now"
  exit 0
fi
echo "gateway-drain: $runs run(s) still going, waiting up to ${wait_s}s"

while :; do
  sleep "$poll_s"
  runs="$(runs_now)"
  waited_ms=$(( $(now_ms) - start ))
  waited=$(( waited_ms / 1000 ))
  if [ -z "$runs" ]; then
    echo "gateway-drain: the count stopped answering after ${waited}s, so the stop goes ahead"
    exit 0
  fi
  if [ "$runs" -eq 0 ]; then
    echo "gateway-drain: every run ended after ${waited}s"
    exit 0
  fi
  if [ "$waited_ms" -ge $(( wait_s * 1000 )) ]; then
    echo "gateway-drain: $runs run(s) still going after ${waited}s, so the stop goes ahead"
    exit 0
  fi
done
