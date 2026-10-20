#!/usr/bin/env bash
# Watch the dev server of the browser suite, and stop the run when it stalls.
#
#   bash scripts/e2e_watchdog.sh <url> <pid of the playwright run>
#
# Why: on 2026-10-18 and 2026-10-19, three runs of "Browser suite (e2e) 1/3"
# stalled. The dev server stopped answering. Every later test then waited
# 2.2 minutes for page.goto, and the 40-minute job cap cancelled the job with
# no log line and no report. This script finds the stall in about one minute,
# prints what the machine was doing, and ends the run so that the report step
# can keep the evidence.
#
# It prints one heartbeat line every 5 minutes. A stall then has a baseline.
url="${1:?url}"
pid="${2:?pid}"
fails=0
beat=0

snapshot() {
  echo "--- watchdog $(date -u +%H:%M:%S) ---"
  free -m 2>/dev/null | sed -n 1,2p
  df -h / 2>/dev/null | tail -1
  ps -eo pid,pcpu,pmem,rss,etime,args --sort=-rss 2>/dev/null | cut -c1-140 | head -8
}

while kill -0 "$pid" 2>/dev/null; do
  sleep 20
  beat=$((beat + 1))
  if curl -s -o /dev/null -m 15 "$url"; then
    fails=0
    [ $((beat % 15)) -eq 0 ] && snapshot
  else
    fails=$((fails + 1))
    echo "::warning::The dev server did not answer $url in 15 s ($fails of 3)."
    snapshot
    if [ "$fails" -ge 3 ]; then
      echo "::error::The dev server stopped answering. The watchdog ends the run now."
      kill -TERM "$pid" 2>/dev/null
      exit 0
    fi
  fi
done
