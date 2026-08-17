#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# WS-25 D1 — proving the two-stage bootstrap.
#
# The hazard: scripts/vps_apply.sh's first act is `git fetch && git reset --hard`
# on the very checkout it lives in. A box that runs it FROM the checkout has the
# file replaced underneath a bash process that is still reading it.
#
# bash reads a script incrementally: it parses one command, lseek()s the fd to
# just past that command, runs it, then reads onward FROM THAT OFFSET. So what
# happens next depends entirely on HOW the file was replaced:
#
#   A1  replaced by RENAME (git's own method) -> the fd still points at the old
#       inode. bash reads v1 to the end. No error, no garbage: the box runs the
#       OLD deploy steps against the NEW tree and reports success.
#   A2  replaced IN PLACE (truncate+write, same inode) -> bash resumes at v1's
#       byte offset inside v2's bytes. Steps are skipped or torn in half.
#
# A1 is the quieter of the two and is the one git actually produces. Both are
# defeated by the same fix, which is why the two-stage bootstrap is not optional.
#
#   B   the REAL scripts/vps_pull.sh: read the target's copy out of the object
#       database with `git show`, run it from a temp path nothing will touch.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)/bootstrap-proof"
REAL_PULL="/home/user/Metorite/scripts/vps_pull.sh"
rm -rf "$ROOT"; mkdir -p "$ROOT"
export GIT_AUTHOR_NAME=proof GIT_AUTHOR_EMAIL=p@x GIT_COMMITTER_NAME=proof GIT_COMMITTER_EMAIL=p@x

# ── The two versions ─────────────────────────────────────────────────────────
# v1 carries a long comment banner that v2 deletes. Same 12 steps either way;
# only the byte offsets differ. Sized ~20 KB to match the real vps_apply.sh
# (26 KB) — comfortably past bash's read buffer, so re-reads definitely occur.
make_apply() {  # $1=version $2=outfile $3=first-act(reset|inplace)
  local v="$1" out="$2" act="$3" i
  {
    echo "# apply script v$v — stands in for scripts/vps_apply.sh"
    if [ "$v" = 1 ]; then
      for i in $(seq 1 300); do
        echo "# banner line $i — 300 lines of WHY that exist in v1 and are deleted in v2."
      done
    fi
    echo 'set -e'
    echo 'cd "$APP_DIR"'
    echo 'echo "  [apply] STEP 0: synchronising the checkout (this rewrites me)"'
    if [ "$act" = reset ]; then
      echo 'git fetch --quiet origin release'
      echo 'git reset --hard --quiet origin/release'
    else
      # Same net effect, but truncate-in-place instead of rename.
      echo 'git fetch --quiet origin release'
      echo 'git show origin/release:scripts/vps_apply.sh > scripts/vps_apply.sh'
    fi
    echo 'echo "  [apply] inode after rewrite: $(stat -c %i scripts/vps_apply.sh)"'
    for i in $(seq 1 12); do
      echo "echo \"  [apply] STEP $i of 12 — vVER\""
    done
    echo 'echo "  [apply] DONE — all 12 steps ran"'
  } | sed "s/vVER/v$v/" > "$out"
}

build_world() {  # $1=first-act
  rm -rf "$ROOT/origin.git" "$ROOT/seed" "$ROOT/box" "$ROOT/state"
  git init -q --bare "$ROOT/origin.git"
  git clone -q "$ROOT/origin.git" "$ROOT/seed" 2>/dev/null
  mkdir -p "$ROOT/seed/scripts"
  make_apply 1 "$ROOT/seed/scripts/vps_apply.sh" "$1"
  git -C "$ROOT/seed" checkout -q -b release
  git -C "$ROOT/seed" add -A; git -C "$ROOT/seed" commit -qm v1
  git -C "$ROOT/seed" push -q origin release
  V1=$(git -C "$ROOT/seed" rev-parse HEAD)
  git clone -q -b release "$ROOT/origin.git" "$ROOT/box"
  make_apply 2 "$ROOT/seed/scripts/vps_apply.sh" "$1"
  git -C "$ROOT/seed" commit -qam "v2 — banner deleted, steps relabelled"
  git -C "$ROOT/seed" push -q origin release
  V2=$(git -C "$ROOT/seed" rev-parse HEAD)
}

hr() { printf '\n════════ %s ════════\n' "$*"; }

# ═════════════════════════════════════════════════════════════════════════════
hr "A1 — naive single-stage, rewrite by git reset --hard (git's real method)"
build_world reset
echo "v1=${V1:0:8} (20 KB)   v2=${V2:0:8} (0.8 KB)"
echo "inode before: $(stat -c %i "$ROOT/box/scripts/vps_apply.sh")"
( cd "$ROOT/box" && APP_DIR="$ROOT/box" bash scripts/vps_apply.sh ) 2>&1 | sed 's/^/  A1| /'
echo "  A1| exit=${PIPESTATUS[0]}"
echo "  A1| checkout now at $(git -C "$ROOT/box" rev-parse --short HEAD) — but which version's steps ran?"

# ═════════════════════════════════════════════════════════════════════════════
hr "A2 — naive single-stage, rewrite IN PLACE (same inode)"
build_world inplace
echo "v1=${V1:0:8}   v2=${V2:0:8}"
echo "inode before: $(stat -c %i "$ROOT/box/scripts/vps_apply.sh")"
( cd "$ROOT/box" && APP_DIR="$ROOT/box" bash scripts/vps_apply.sh ) 2>&1 | sed 's/^/  A2| /'
echo "  A2| exit=${PIPESTATUS[0]}"

# ═════════════════════════════════════════════════════════════════════════════
hr "B — two-stage: the REAL /home/user/Metorite/scripts/vps_pull.sh"
build_world reset
rm -f /tmp/acb-vps-pull.lock
APP_DIR="$ROOT/box" RELEASE_REF=release STATE_DIR="$ROOT/state" \
  bash "$REAL_PULL" 2>&1 | sed 's/^/  B| /'
echo "  B| exit=${PIPESTATUS[0]}"
echo "  B| marker: $(cat "$ROOT/state/last-pull-ok" 2>/dev/null || echo MISSING) / $(cut -c1-8 "$ROOT/state/last-pull-sha" 2>/dev/null || echo MISSING)"

# ═════════════════════════════════════════════════════════════════════════════
# A3 — the spec's "executes garbage" case. Same in-place rewrite as A2, but v2
# is SHIFTED rather than shortened: a few bytes inserted near the top push every
# later offset along, so bash resumes mid-line instead of past EOF.
hr "A3 — naive single-stage, in-place rewrite, v2 byte-SHIFTED"
rm -rf "$ROOT/origin.git" "$ROOT/seed" "$ROOT/box"
git init -q --bare "$ROOT/origin.git"; git clone -q "$ROOT/origin.git" "$ROOT/seed" 2>/dev/null
mkdir -p "$ROOT/seed/scripts"
gen() { # $1=shift-prefix
  { echo "# apply script"
    [ -n "$1" ] && echo "$1"
    for i in $(seq 1 300); do echo "# banner line $i — 300 lines of WHY."; done
    echo 'set -e'; echo 'cd "$APP_DIR"'
    echo 'echo "  [apply] STEP 0: synchronising the checkout (this rewrites me)"'
    echo 'git fetch --quiet origin release'
    echo 'git show origin/release:scripts/vps_apply.sh > scripts/vps_apply.sh'
    for i in $(seq 1 12); do echo "echo \"  [apply] STEP $i of 12\""; done
    echo 'echo "  [apply] DONE — all 12 steps ran"'
  } > "$ROOT/seed/scripts/vps_apply.sh"; }
gen ""
git -C "$ROOT/seed" checkout -q -b release; git -C "$ROOT/seed" add -A
git -C "$ROOT/seed" commit -qm v1 >/dev/null; git -C "$ROOT/seed" push -q origin release
git clone -q -b release "$ROOT/origin.git" "$ROOT/box"
gen "# one extra comment line, inserted at the top of v2 — shifts every later byte offset"
git -C "$ROOT/seed" commit -qam v2 >/dev/null; git -C "$ROOT/seed" push -q origin release
( cd "$ROOT/box" && APP_DIR="$ROOT/box" bash scripts/vps_apply.sh ) 2>&1 | sed 's/^/  A3| /'
echo "  A3| exit=${PIPESTATUS[0]}"
