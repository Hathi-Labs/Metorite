#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# prod_copy.sh: bring production's reduced copy to THIS PC (LD-8a).
#
#   bash scripts/prod_copy.sh           pull the newest copy, check it, keep 2
#   bash scripts/prod_copy.sh --help    this text
#
# WHERE THE COPY COMES FROM. Each night, scripts/staging_export.sh runs on the
# production box after the backup's restore check passes. It keeps only the
# organizations in STAGING_EXPORT_SLUGS, minimised by infra/staging/minimise.sql,
# and writes the copy to /var/lib/acb-staging-export/current. `current` is a
# symlink to one r.<stamp> folder. This script resolves it first, so the age
# and the files come from the same folder.
#
# OWNER APPROVAL: D145, 2026-10-31, in chat, for a copy with its credentials
# blanked. PRECONDITION: LD-8c is on production (minimise.sql blanks the push
# keys and the code hashes), AND a nightly backup logged "restore verified"
# after it. Before both, the copy still holds secrets. Do not run this then.
#
# WHAT IT DOES:
#   1. Refuses a target folder inside a git work tree.
#   2. Limits the base folder to the current user: icacls on Windows (no
#      inheritance, one principal), mode 700 and umask 077 elsewhere.
#   3. Reads the age from the remote `stat -c %Y` of staging.dump, and refuses
#      a copy more than 36 hours old (the rule of scripts/staging_refresh.sh).
#   4. Pulls the five files with `ssh metorite ... tar`, because Git Bash has
#      no rsync. They land in a .partial-<stamp> folder, limited first.
#   5. Checks SHA256SUMS. It must name the four data files and nothing else.
#   6. Renames the folder to <stamp>, then keeps the 2 newest copies.
#
# THE LAYOUT (LD-8b reads it): ~/.metorite/prod-copy/<stamp>/. <stamp> is the
# UTC time of the export, from the mtime of staging.dump, e.g. 20261101T023814Z.
# A folder holds exactly staging.dump, roles.txt, extensions.txt, flags.env and
# SHA256SUMS. A name that starts with "." is never a copy.
#
# IT PRINTS the age, the folder and the size. It never prints the content of a
# file, a DSN or a line of flags.env.
#
# EXIT 0: done, or the copy was already here. 1: refused (stale, a bad
# checksum, a git work tree). 2: a usage or environment error.
# ENV: PROD_COPY_DIR is the base folder (default ~/.metorite/prod-copy).
# PROD_COPY_SSH is the ssh command (default ssh). The tests set a stub.
#
# Fence: tests/unit/test_prod_copy.py.
# ════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail
# An error that no check names exits 2, so exit 1 always means a refusal.
trap 'exit 2' ERR

SSH="${PROD_COPY_SSH:-ssh}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20)
HOST=metorite
EXPORT=/var/lib/acb-staging-export/current
FILES=(staging.dump roles.txt extensions.txt flags.env)
KEEP=2
STAMP_RE='^[0-9]{8}T[0-9]{6}Z$'
SUM_RE='^[0-9a-f]{64} [ *](staging\.dump|roles\.txt|extensions\.txt|flags\.env)$'

# Git Bash says msys. A process costs about 0.1 s there, so the helpers below
# use bash itself where they can.
case "$OSTYPE" in msys*|cygwin*) WIN=1 ;; *) WIN=0 ;; esac

say()    { printf 'prod_copy: %s\n' "$*"; }
refuse() { printf 'prod_copy: REFUSED. %s\n' "$*" >&2; exit 1; }
die()    { printf 'prod_copy: %s\n' "$*" >&2; exit 2; }
usage()  { sed -n '3,/^# Fence:/p' "$0" | sed 's/^# \{0,1\}//'; }

case "$#:${1:-}" in
  0:) ;;
  1:-h|1:--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

# ── The tools, and who the current user is ──────────────────────────────────
for tool in "$SSH" tar sha256sum date du; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is not on PATH"
done
if [ "$WIN" = 1 ]; then
  command -v cygpath >/dev/null 2>&1 || die "cygpath is not on PATH. Run this from Git Bash."
  command -v icacls >/dev/null 2>&1 || die "icacls is not on PATH"
  WHOAMI="$(cygpath -u "${SYSTEMROOT:-C:\\Windows}")/System32/whoami.exe"
  [ -x "$WHOAMI" ] || die "$WHOAMI is missing"
  IFS=, read -r ME_NAME ME_SID < <(MSYS_NO_PATHCONV=1 "$WHOAMI" /user /fo csv /nh) || true
  ME_SID="${ME_SID:-}"; ME_SID="${ME_SID%$'\r'}"; ME_SID="${ME_SID//\"/}"
  ME_NAME="${ME_NAME:-}"; ME_NAME="${ME_NAME//\"/}"
  [[ "$ME_SID" =~ ^S-[0-9-]+$ ]] || die "whoami gave no SID for the current user"
fi

BASE="${PROD_COPY_DIR:-${HOME:?HOME is unset}/.metorite/prod-copy}"
if [ "$WIN" = 1 ]; then BASE="$(cygpath -u "$BASE")"; fi
case "$BASE" in /*) ;; *) BASE="$PWD/$BASE" ;; esac

# ── Helpers ─────────────────────────────────────────────────────────────────

# The nearest folder that exists, with each symlink resolved.
nearest() {
  local d="$1"
  while [ ! -d "$d" ]; do d="${d%/*}"; d="${d:-/}"; done
  (cd "$d" && pwd -P)
}

# True when $1 sits inside a git work tree. git answers first. The walk for a
# .git entry also catches a tree that git refuses to read (safe.directory).
in_git_tree() {
  local d="$1"
  if command -v git >/dev/null 2>&1; then
    [ "$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CEILING_DIRECTORIES \
          git -C "$d" rev-parse --is-inside-work-tree 2>/dev/null)" = true ] && return 0
  fi
  while :; do
    [ -e "$d/.git" ] && return 0
    [ "$d" = / ] && return 1
    d="${d%/*}"; d="${d:-/}"
  done
}

# True when only the current user can reach $1. On Windows, icacls must show
# one entry, for this user, and no inherited entry. Elsewhere, mode 700.
only_me() {
  if [ "$WIN" = 0 ]; then
    [ "$(stat -c '%a %u' "$1")" = "700 $EUID" ]
    return
  fi
  local win out line ace="" n=0 first=1
  win="$(cygpath -w "$1")"
  out="$(MSYS_NO_PATHCONV=1 icacls "$win" 2>/dev/null)" || return 1
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [ -n "$line" ] || break          # a blank line ends the list of entries
    if [ "$first" = 1 ]; then line="${line#"$win"}"; first=0; fi
    line="${line#"${line%%[! ]*}"}"  # drop the leading spaces
    ace="$line"
    n=$((n + 1))
  done <<<"$out"
  [ "$n" = 1 ] || return 1
  case "$ace" in *"(I)"*) return 1 ;; esac
  local who="${ace%%:(*}"
  [ "${who,,}" = "${ME_NAME,,}" ]
}

# Limit $1 to the current user, then prove it. Call it before data lands.
lock_dir() {
  if [ "$WIN" = 1 ]; then
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
      icacls "$(cygpath -w "$1")" /inheritance:r /grant:r "*$ME_SID:(OI)(CI)F" >/dev/null \
      || die "icacls could not limit $1 to the current user"
  else
    chmod 700 "$1" || die "could not set mode 700 on $1"
  fi
  only_me "$1" || die "$1 is not limited to the current user. Fix its ACL, then run this again."
}

# True when $1 holds the five files and nothing else, none of them a symlink,
# SHA256SUMS names each data file once, and every sum matches. It prints file
# names only, never a line of a file.
check_copy() {
  local dir="$1" f line res n=0 seen=" " ok=1
  local -a entries
  shopt -s nullglob dotglob
  entries=("$dir"/*)
  shopt -u nullglob dotglob
  [ "${#entries[@]}" = 5 ] || { say "$dir does not hold exactly the five files" >&2; return 1; }
  for f in "${FILES[@]}" SHA256SUMS; do
    if [ ! -f "$dir/$f" ] || [ -L "$dir/$f" ]; then
      say "$f is missing, or it is not a plain file" >&2
      return 1
    fi
  done
  # Each line is "<sum>  <name>", with a known name, and no name twice. Four
  # such lines name the four data files.
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    [[ "$line" =~ $SUM_RE ]] || { ok=0; break; }
    f="${line:66}"
    case "$seen" in *" $f "*) ok=0; break ;; esac
    seen="$seen$f "
  done <"$dir/SHA256SUMS"
  if [ "$ok" = 0 ] || [ "$n" != 4 ]; then
    say "SHA256SUMS does not name the four data files, each once" >&2
    return 1
  fi
  res="$(cd "$dir" && sha256sum -c SHA256SUMS 2>/dev/null)" && return 0
  while IFS= read -r line; do
    case "$line" in *": FAILED"*) say "the checksum of ${line%%: FAILED*} does not match" >&2 ;; esac
  done <<<"$res"
  return 1
}

# ── 1. Never inside a git work tree ─────────────────────────────────────────
in_git_tree "$(nearest "$BASE")" \
  && refuse "$BASE is inside a git work tree. A copy there could reach a commit."

# ── 2. The base folder, for the current user only ───────────────────────────
umask 077
mkdir -p "$BASE" || die "could not make $BASE"
lock_dir "$BASE"
LOCK="$BASE/.lock"
mkdir "$LOCK" 2>/dev/null || die "another pull holds $LOCK. If none runs, remove that folder."
partial=""
cleanup() { if [ -n "$partial" ]; then rm -rf "$partial"; fi; rm -rf "$LOCK"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
rm -rf "$BASE"/.partial-*   # left by a run that was killed

# ── 3. The age, from the remote folder that `current` points to ─────────────
probe='r="$(readlink -f '"$EXPORT"')" && [ -d "$r" ] && printf "dir=%s\n" "$r" && printf "time=%s\n" "$(stat -c %Y "$r/staging.dump")"'
out="$("$SSH" "${SSH_OPTS[@]}" "$HOST" "$probe")" \
  || die "ssh $HOST could not read $EXPORT. Is the key loaded, and did the export run?"
resolved="" mtime=""
while IFS= read -r line; do
  line="${line%$'\r'}"
  case "$line" in dir=*) resolved="${line#dir=}" ;; time=*) mtime="${line#time=}" ;; esac
done <<<"$out"
[[ "$resolved" =~ ^/[A-Za-z0-9/._~-]+$ ]] || die "the export folder on $HOST has an unexpected name"
[[ "$mtime" =~ ^[0-9]+$ ]] || die "the export on $HOST gave no time for staging.dump"
age_h=$(( ( $(date +%s) - mtime ) / 3600 ))
say "the copy on $HOST is ${age_h} hours old"
[ "$age_h" -le 36 ] || refuse "the copy is ${age_h} hours old, and the limit is 36. Did last night's backup and export run?"

stamp="$(date -u -d "@$mtime" +%Y%m%dT%H%M%SZ)"
final="$BASE/$stamp"

# ── 4 and 5. Pull into a limited .partial folder, then check it ─────────────
if [ -e "$final" ]; then
  check_copy "$final" || refuse "$final is already here, and it fails its checks. Remove it, then run this again."
  say "this copy is already here, so nothing was pulled"
else
  partial="$BASE/.partial-$stamp.$$"
  mkdir "$partial" || die "could not make $partial"
  lock_dir "$partial"
  say "pulling the copy of $stamp from $HOST"
  "$SSH" "${SSH_OPTS[@]}" "$HOST" "tar -C '$resolved' -cf - ${FILES[*]} SHA256SUMS" \
    | tar -C "$partial" -xf - "${FILES[@]}" SHA256SUMS \
    || die "the pull from $HOST failed. Nothing was kept."
  check_copy "$partial" || refuse "the copy fails its checks. Nothing was kept."
  mv -T "$partial" "$final" || die "could not move the copy to $final"
  partial=""
fi

# ── 6. Keep the 2 newest copies ─────────────────────────────────────────────
mapfile -t copies < <(
  for d in "$BASE"/*/; do
    n="${d%/}"; n="${n##*/}"
    if [[ "$n" =~ $STAMP_RE ]]; then printf '%s\n' "$n"; fi
  done | LC_ALL=C sort)
for (( i = 0; i < ${#copies[@]} - KEEP; i++ )); do
  # Never the copy that this run checked, even when 2 newer ones exist.
  [ "${copies[$i]}" = "$stamp" ] && continue
  rm -rf -- "${BASE:?}/${copies[$i]}"
  say "removed the older copy ${copies[$i]}"
done

say "the copy is in $final ($(du -sh "$final" | cut -f1))"
