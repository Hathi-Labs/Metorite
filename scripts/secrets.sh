#!/usr/bin/env bash
# scripts/secrets.sh — the secrets drop (dev phase). Reference: docs/secrets_drop.md
#
# The owner puts each secret in a file in the DROP FOLDER, $HOME/.metorite/secrets
# (on Windows Git Bash, %USERPROFILE%\.metorite\secrets). That folder is outside
# every git checkout. deploy/secrets/manifest.json says where each file goes on
# the box, which keys it may hold, and how to check them. This script checks the
# file, backs up the old one on the box, writes the new one, verifies it by hash,
# and restarts the unit that the manifest names.
#
#   scripts/secrets.sh init                     make the folder, the README and the templates
#   scripts/secrets.sh status [name]            missing-local | local-only | in-sync | differs | remote-only
#   scripts/secrets.sh diff <name>              the key NAMES that a push would add, change or remove
#   scripts/secrets.sh push <name> [--yes]      check, back up, write, verify, restart, roll back
#   scripts/secrets.sh push --all [--yes]       push each entry that has its values, in manifest order
#   scripts/secrets.sh push ... --verify        also run the post-push check (a full backup: minutes)
#   scripts/secrets.sh gen backup-gpg [--force] make the backup key pair with local gpg
#   scripts/secrets.sh forget <key> [--yes]     shred a local file
#
# 🔴 A VALUE NEVER LEAVES THIS SCRIPT except as the content of the file on the box.
#   - It never goes to stdout or stderr. A message names the key, never the value.
#   - It never goes on argv, here or on the box. The content goes over ssh STDIN,
#     and the remote script reads it from there. The remote script itself is on
#     argv, and it holds no value. printf is a bash builtin, so it makes no argv.
#   - On the box it goes to a temp file IN THE TARGET DIRECTORY (mode 0600), and
#     `mv` swaps it into place.
#   tests/unit/test_secrets_drop.py runs every command with a canary value and
#   fails if the canary shows in the output, in any argv, or in a log.
#
# The ssh target is METORITE_SSH_HOST, and `metorite` when it is not set.
set -euo pipefail
set +x
umask 077
export LC_ALL=C

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
manifest_file="$here/../deploy/secrets/manifest.json"
drop="${HOME:?HOME is not set}/.metorite/secrets"
host="${METORITE_SSH_HOST:-metorite}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
say() { printf '%s\n' "$*"; }

if ! [[ "$host" =~ ^[A-Za-z0-9_][A-Za-z0-9@._-]*$ ]]; then
  die "METORITE_SSH_HOST is not a host name or an ssh alias."
fi

# ── The shared library. It runs on this PC, and the same text runs on the box. ──
# So the parse of an env file and the hash of a value are the same on both ends.
read -r -d '' sd_lib <<'LIB' || true
declare -a sd_keys=() sd_bad=() sd_dup=() m_keys=()
declare -A sd_val=() m_val=()
sd_k=""; sd_v=""; sd_pre=""

# sd_sha — the sha256 of stdin, as 64 hex digits.
sd_sha() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -c1-64
  else shasum -a 256 | cut -c1-64; fi
}

# sd_line_key LINE — 0 for KEY=value, 1 for a blank line or a comment, 2 for a
# bad line. On 0 it sets sd_k (the key), sd_v (the value) and sd_pre (the text
# before the key: the indent and an `export `). A trailing CR is not part of
# the value, so a file that a Windows editor saved reads the same.
sd_line_key() {
  local l="${1%$'\r'}" t
  sd_k=""; sd_v=""; sd_pre=""
  t="${l#"${l%%[![:space:]]*}"}"
  case "$t" in ''|'#'*) return 1 ;; esac
  if [[ "$t" =~ ^export[[:space:]]+ ]]; then t="${t#"${BASH_REMATCH[0]}"}"; fi
  if [[ "$t" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
    sd_k="${BASH_REMATCH[1]}"
    sd_v="${t#*=}"
    sd_pre="${l%"$t"}"
    return 0
  fi
  return 2
}

# sd_parse FILE — read an env file into sd_keys (in order), sd_val, sd_bad (line
# numbers) and sd_dup (keys set more than one time; the last line wins).
sd_parse() {
  local line n=0 rc
  sd_keys=(); sd_bad=(); sd_dup=(); sd_val=()
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    if [ "$n" -eq 1 ]; then line="${line#$'\xef\xbb\xbf'}"; fi
    rc=0; sd_line_key "$line" || rc=$?
    if [ "$rc" -eq 0 ]; then
      if [ -n "${sd_val[$sd_k]+x}" ]; then sd_dup+=("$sd_k"); else sd_keys+=("$sd_k"); fi
      sd_val[$sd_k]="$sd_v"
    elif [ "$rc" -eq 2 ]; then
      sd_bad+=("$n")
    fi
  done < "$1"
}

# sd_merge OLD — print OLD with each key of m_keys set to its m_val. Every
# other line comes out byte for byte, and so does the end of the file. The
# first line of a managed key gets the new value. A LATER line of the same key
# is dropped, because the last line wins in systemd and in bash. A key that
# OLD does not hold goes at the end.
sd_merge() {
  local line nl ended=1 rc cr k
  local -A seen=()
  while :; do
    if IFS= read -r line; then nl=$'\n'; else
      [ -n "$line" ] || break
      nl=""
    fi
    rc=0; sd_line_key "$line" || rc=$?
    if [ "$rc" -eq 0 ] && [ -n "${m_val[$sd_k]+x}" ]; then
      if [ -z "${seen[$sd_k]+x}" ]; then
        seen[$sd_k]=1
        cr=""; if [ "${line%$'\r'}" != "$line" ]; then cr=$'\r'; fi
        printf '%s%s=%s%s%s' "$sd_pre" "$sd_k" "${m_val[$sd_k]}" "$cr" "$nl"
        if [ -n "$nl" ]; then ended=1; else ended=0; fi
      fi
    else
      printf '%s%s' "$line" "$nl"
      if [ -n "$nl" ]; then ended=1; else ended=0; fi
    fi
    [ -n "$nl" ] || break
  done < "$1"
  for k in "${m_keys[@]}"; do
    if [ -z "${seen[$k]+x}" ]; then
      if [ "$ended" -eq 0 ]; then printf '\n'; ended=1; fi
      printf '%s=%s\n' "$k" "${m_val[$k]}"
    fi
  done
}
LIB
eval "$sd_lib"

# ── The remote half. It runs as root on the box, through `sudo -n bash -c`. ──
# It prints only `R <tag> ...` lines: names, hashes, paths and modes.
read -r -d '' sd_remote <<'REMOTE' || true
set -euo pipefail
set +x
umask 077
export LC_ALL=C

# The seconds a restarted unit must stay up before it counts as healthy, and
# the tries (2 seconds apart) that its health URL gets.
r_settle=10
r_health_tries=30

# r_no_link PATH — refuse a symbolic link. The script runs as root, and a link
# in a directory that acb can write would send a root write somewhere else.
r_no_link() {
  if [ -L "$1" ]; then echo "ERROR: $1 is a symbolic link. The script refuses it." >&2; exit 3; fi
}

# r_probe PATH KIND KEY... — KIND is env-file, env-merge or file. A hash goes
# out for the listed keys ONLY, so a probe of the app env file never hashes a
# value that the manifest does not manage. For env-file, the names of the
# other keys go out too, with no hash, because a push removes them.
r_probe() {
  local path="$1" kind="$2" k
  shift 2
  local -A want=()
  for k in "$@"; do want[$k]=1; done
  r_no_link "$path"
  if [ ! -e "$path" ]; then echo "R exists 0"; return 0; fi
  if [ ! -f "$path" ]; then echo "ERROR: $path is not a regular file." >&2; exit 3; fi
  echo "R exists 1"
  echo "R meta $(stat -c '%U:%G %a' "$path")"
  echo "R sha $(sd_sha < "$path")"
  if [ "$kind" != file ]; then
    sd_parse "$path"
    for k in "${sd_keys[@]}"; do
      if [ -n "${want[$k]+x}" ]; then
        echo "R key $k $(printf '%s' "${sd_val[$k]}" | sd_sha)"
      elif [ "$kind" = env-file ]; then
        echo "R other $k"
      fi
    done
  fi
}

# r_prune PATH KEEP — keep the newest KEEP backups of PATH, and delete the
# rest. Only a name of the form PATH.bak-<YYYYMMDDTHHMMSSZ> counts. The glob
# sorts in C order, and the stamp sorts by time.
r_prune() {
  local path="$1" keep="$2" f n=0 i
  local -a all=()
  for f in "$path".bak-*; do
    [[ "${f#"$path".bak-}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || continue
    if [ -f "$f" ] && [ ! -L "$f" ]; then all+=("$f"); fi
  done
  for ((i = 0; i < ${#all[@]} - keep; i++)); do rm -f -- "${all[$i]}"; n=$((n + 1)); done
  echo "R pruned $n"
}

# r_put SRC PATH — put the bytes of SRC at PATH, and remove SRC.
# WS-49 BH-2: a writer of /opt/acb/app/.env must keep its inode. The gateway
# sandbox bind-mounts that inode at start, and a rename leaves the running
# gateway on the old, unlinked copy until its restart. So when PATH is a
# regular file with the owner, group and mode of SRC, the bytes go INTO it.
# dd opens PATH with O_NOFOLLOW, so a link that appears after r_no_link fails
# the write. It never sends a root write somewhere else. Any other case (no
# file yet, other meta) renames, as before. The restart list of the entry
# then gives each unit the new inode.
# Fence: tests/unit/test_env_inode.py and tests/unit/test_secrets_drop.py.
r_put() {
  local src="$1" path="$2"
  if [ -f "$path" ] && [ ! -L "$path" ] \
     && [ "$(stat -c '%U:%G %a' "$src")" = "$(stat -c '%U:%G %a' "$path")" ]; then
    dd if="$src" of="$path" oflag=nofollow conv=fsync status=none
    rm -f -- "$src"
    echo "R put in-place"
  else
    mv -f -T "$src" "$path"
    echo "R put renamed"
  fi
}

r_write() {
  local path="$1" owner="$2" group="$3" mode="$4" how="$5" dir bak k btmp
  dir="$(dirname "$path")"
  r_no_link "$path"
  r_no_link "$dir"
  if [ -e "$path" ] && [ ! -f "$path" ]; then
    echo "ERROR: $path is not a regular file." >&2; exit 3
  fi
  if [ "$how" = merge ] && [ ! -f "$path" ]; then
    echo "ERROR: $path does not exist. A merge needs the env file to exist." >&2; exit 3
  fi
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir"
    chown root:root "$dir"
    chmod 0755 "$dir"
    echo "R made-dir $dir"
  fi
  r_tmp="$(mktemp "$dir/.secrets-drop.XXXXXX")"
  btmp=""
  trap 'rm -f "$r_tmp" ${btmp:+"$btmp"}' EXIT
  if [ "$how" = merge ]; then
    sd_parse /dev/stdin
    if [ "${#sd_bad[@]}" -ne 0 ] || [ "${#sd_dup[@]}" -ne 0 ]; then
      echo "ERROR: the merge input is not clean KEY=value lines." >&2; exit 3
    fi
    m_keys=("${sd_keys[@]}")
    for k in "${m_keys[@]}"; do m_val[$k]="${sd_val[$k]}"; done
    sd_merge "$path" > "$r_tmp"
  else
    cat > "$r_tmp"
  fi
  if [ -e "$path" ]; then
    # A name that exists means a push in this same second. Wait for the next
    # stamp, and never invent a second name. A link at the name is refused.
    for k in 1 2 3; do
      bak="$path.bak-$(date -u +%Y%m%dT%H%M%SZ)"
      r_no_link "$bak"
      [ -e "$bak" ] || break
      command sleep 1
    done
    if [ -e "$bak" ]; then
      echo "ERROR: the backup $bak exists. Wait one second, then push again." >&2; exit 3
    fi
    # Copy into a fresh temp file, then rename. A rename never follows a link.
    btmp="$(mktemp "$dir/.secrets-drop.XXXXXX")"
    cp -p "$path" "$btmp"
    mv -f -T "$btmp" "$bak"
    btmp=""
    echo "R backup $bak"
  else
    echo "R backup none"
  fi
  chown "$owner:$group" "$r_tmp"
  chmod "$mode" "$r_tmp"
  echo "R meta $(stat -c '%U:%G %a' "$r_tmp")"
  # A merge edits the app env file that running units hold, so it keeps the
  # inode (r_put). A whole-file push renames.
  if [ "$how" = merge ]; then
    r_put "$r_tmp" "$path"
  else
    mv -f -T "$r_tmp" "$path"
  fi
  trap - EXIT
  echo "R sha $(sd_sha < "$path")"
  if [ "$how" = merge ]; then
    sd_parse "$path"
    for k in "${m_keys[@]}"; do echo "R key $k $(printf '%s' "${sd_val[$k]-}" | sd_sha)"; done
  fi
  r_prune "$path" 3
}

# r_rollback PATH BACKUP — put BACKUP back at PATH. BACKUP is `none` when the
# push made a new file, and then the new file goes.
r_rollback() {
  local path="$1" bak="$2" dir tmp
  dir="$(dirname "$path")"
  r_no_link "$path"
  r_no_link "$dir"
  if [ "$bak" = none ]; then
    rm -f -- "$path"
    echo "R rolled-back removed"
    return 0
  fi
  case "$bak" in "$path".bak-*) ;; *) echo "ERROR: $bak is not a backup of $path." >&2; exit 3 ;; esac
  r_no_link "$bak"
  if [ ! -f "$bak" ]; then echo "ERROR: the backup $bak is gone." >&2; exit 3; fi
  tmp="$(mktemp "$dir/.secrets-drop.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  cp -p "$bak" "$tmp"
  r_put "$tmp" "$path"
  trap - EXIT
  echo "R rolled-back restored"
  echo "R sha $(sd_sha < "$path")"
}

# r_restart UNIT [HEALTH_URL] — restart UNIT and prove that it stays up.
# `is-active` alone is not proof: a Type=simple unit with Restart=always reads
# active between two crashes. So NRestarts must not grow after the restart,
# and the health URL, when there is one, must answer 200.
r_restart() {
  local unit="$1" url="${2:-}" n0 n1 code ok=0 i
  systemctl restart "$unit"
  n0="$(systemctl show -p NRestarts --value "$unit")"
  sleep "$r_settle"
  if ! systemctl is-active --quiet "$unit"; then echo "R fail $unit is not active"; exit 4; fi
  n1="$(systemctl show -p NRestarts --value "$unit")"
  if [ "$n1" != "$n0" ]; then echo "R fail $unit restarted by itself ($n0 to $n1)"; exit 4; fi
  if [ -n "$url" ]; then
    for ((i = 0; i < r_health_tries; i++)); do
      code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || true)"
      if [ "$code" = 200 ]; then ok=1; break; fi
      sleep 2
    done
    if [ "$ok" != 1 ]; then echo "R fail $unit did not answer 200 at $url"; exit 4; fi
    n1="$(systemctl show -p NRestarts --value "$unit")"
    if [ "$n1" != "$n0" ]; then echo "R fail $unit restarted by itself ($n0 to $n1)"; exit 4; fi
  fi
  echo "R healthy $unit"
}

# r_postcheck UNIT MATCH SECS ENVFILE — start UNIT, wait for it (SECS at most),
# and look for MATCH in its journal since the start. The last 5 relevant lines
# go out, with every value of ENVFILE replaced by [REDACTED:<key>].
r_postcheck() {
  local unit="$1" match="$2" secs="$3" envf="$4" since rc=0 lines line k v lc
  local -a keep=()
  since="$(date '+%Y-%m-%d %H:%M:%S')"
  timeout "$secs" systemctl start "$unit" || rc=$?
  lines="$(journalctl -u "$unit" --since "$since" --no-pager -o cat 2>/dev/null || true)"
  sd_keys=(); sd_val=()
  if [ -f "$envf" ] && [ ! -L "$envf" ]; then sd_parse "$envf"; fi
  while IFS= read -r line; do
    lc="${line,,}"
    if [[ "$line" == *"$match"* ]] || [[ "$lc" =~ error|warn|fail ]]; then keep+=("$line"); fi
  done <<< "$lines"
  if [ "${#keep[@]}" -eq 0 ]; then mapfile -t keep < <(printf '%s\n' "$lines" | tail -n 5); fi
  k=$(( ${#keep[@]} > 5 ? ${#keep[@]} - 5 : 0 ))
  for line in "${keep[@]:k}"; do
    for k in "${sd_keys[@]}"; do
      v="${sd_val[$k]}"
      if [ "${#v}" -ge 4 ]; then line="${line//"$v"/[REDACTED:$k]}"; fi
    done
    echo "R log $line"
  done
  if [ "$rc" -eq 0 ] && [[ "$lines" == *"$match"* ]]; then echo "R postcheck pass"; return 0; fi
  echo "R postcheck fail (start exit $rc)"
  exit 5
}

verb="${1:?}"; shift
case "$verb" in
  probe) r_probe "$@" ;;
  write) r_write "$@" ;;
  rollback) r_rollback "$@" ;;
  restart) r_restart "$@" ;;
  postcheck) r_postcheck "$@" ;;
  *) echo "ERROR: unknown remote verb." >&2; exit 2 ;;
esac
REMOTE

# `set +x` comes FIRST, before the library, so an inherited xtrace traces nothing.
sd_remote_b64="$(printf 'set +x\n%s\n%s\n' "$sd_lib" "$sd_remote" | base64 | tr -d '\n')"

# remote VERB ARGS... — run the remote half on the box. Stdin goes through.
# Each argument is a path, a name or a mode from the checked manifest.
remote() {
  local cmd a
  cmd="sudo -n bash -c \"\$(printf %s $sd_remote_b64 | base64 -d)\" secrets-drop"
  for a in "$@"; do cmd+=" $(printf '%q' "$a")"; done
  # ServerAlive keeps a long post-push check (a full backup) on the line.
  ssh -T -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 "$host" "$cmd"
}

# ── The manifest ──────────────────────────────────────────────────────────────
# A small JSON reader in awk, so the script needs no jq and no Python. It prints
# one line per value: <path> TAB <type> TAB <value>. A path is a dotted list of
# object keys and array indexes, for example entries.0.allowed_keys.3.
# test_secrets_drop.py checks that it agrees with Python's json module.
read -r -d '' json_awk <<'AWK' || true
function fail(m) { printf "manifest: %s near byte %d\n", m, pos > "/dev/stderr"; bad = 1; exit 2 }
function ws() { while (pos <= n && index(" \t\r\n", substr(s, pos, 1))) pos++ }
function pj(p, k) { return (p == "" ? k : p "." k) }
function str(   out, c, e) {
  pos++
  out = ""
  while (pos <= n) {
    c = substr(s, pos, 1)
    if (c == "\"") { pos++; return out }
    if (c == "\\") {
      e = substr(s, pos + 1, 1)
      if (e == "\"" || e == "\\" || e == "/") { out = out e; pos += 2; continue }
      fail("an escape other than \\\" \\\\ \\/")
    }
    if (c == "\t" || c == "\n" || c == "\r") fail("a control character in a string")
    out = out c
    pos++
  }
  fail("a string with no end")
}
function val(p,   c, k, i) {
  ws()
  c = substr(s, pos, 1)
  if (c == "{") {
    pos++; print p "\tobject\t"; ws()
    if (substr(s, pos, 1) == "}") { pos++; return }
    while (1) {
      ws()
      if (substr(s, pos, 1) != "\"") fail("expected a key")
      k = str()
      if (k !~ /^[A-Za-z0-9_-]+$/) fail("a key that is not A-Z, a-z, 0-9, _ or -")
      ws()
      if (substr(s, pos, 1) != ":") fail("expected :")
      pos++
      val(pj(p, k))
      ws(); c = substr(s, pos, 1)
      if (c == ",") { pos++; continue }
      if (c == "}") { pos++; return }
      fail("expected , or }")
    }
  }
  if (c == "[") {
    pos++; print p "\tarray\t"; ws(); i = 0
    if (substr(s, pos, 1) == "]") { pos++; return }
    while (1) {
      val(pj(p, i)); i++
      ws(); c = substr(s, pos, 1)
      if (c == ",") { pos++; continue }
      if (c == "]") { pos++; return }
      fail("expected , or ]")
    }
  }
  if (c == "\"") { k = str(); print p "\tstring\t" k; return }
  if (substr(s, pos, 4) == "true") { pos += 4; print p "\tbool\ttrue"; return }
  if (substr(s, pos, 5) == "false") { pos += 5; print p "\tbool\tfalse"; return }
  if (substr(s, pos, 4) == "null") { pos += 4; print p "\tnull\t"; return }
  if (match(substr(s, pos), /^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?/)) {
    print p "\tnumber\t" substr(s, pos, RLENGTH); pos += RLENGTH; return
  }
  fail("an unexpected character")
}
{ s = s $0 "\n" }
END {
  if (bad) exit 2
  n = length(s); pos = 1
  val("")
  ws()
  if (pos <= n) fail("text after the end")
}
AWK

declare -A mf=() mft=()
ent_count=0
lo_count=0

# ef I FIELD — one scalar field of entry I.
ef() { printf '%s' "${mf[entries.$1.$2]-}"; }
# el I FIELD — the items of an array field of entry I, one per line.
el() {
  local j=0
  while [ -n "${mft[entries.$1.$2.$j]+x}" ]; do printf '%s\n' "${mf[entries.$1.$2.$j]}"; j=$((j + 1)); done
}
# vl I KEY — the validators of KEY in entry I, one per line.
vl() {
  local j=0
  while [ -n "${mft[entries.$1.validators.$2.$j]+x}" ]; do
    printf '%s\n' "${mf[entries.$1.validators.$2.$j]}"; j=$((j + 1))
  done
}

env_validators="non_empty https_url gpg_fingerprint absolute_path s3_bucket_name"
file_validators="non_empty gpg_public_key_only"

load_manifest() {
  local flat p t v i j k name kind errs=() seen=" "
  [ -f "$manifest_file" ] || die "the manifest $manifest_file is missing."
  flat="$(awk "$json_awk" "$manifest_file")" || die "the manifest is not valid JSON. See the line above."
  while IFS=$'\t' read -r p t v; do mf["$p"]="$v"; mft["$p"]="$t"; done <<< "$flat"
  while [ -n "${mft[entries.$ent_count]+x}" ]; do ent_count=$((ent_count + 1)); done
  while [ -n "${mft[local_only.$lo_count]+x}" ]; do lo_count=$((lo_count + 1)); done
  [ "$ent_count" -gt 0 ] || die "the manifest holds no entry."

  for ((i = 0; i < ent_count; i++)); do
    name="$(ef "$i" name)"
    kind="$(ef "$i" kind)"
    [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || errs+=("entry $i: the name is not a-z, 0-9 and -.")
    case "$seen" in *" $name "*) errs+=("$name: the name is used two times.") ;; esac
    seen+="$name "
    case "$kind" in env-file|env-merge|file) ;; *) errs+=("$name: the kind is not env-file, env-merge or file.") ;; esac
    [[ "$(ef "$i" local_file)" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || errs+=("$name: local_file must be a plain file name.")
    p="$(ef "$i" remote_path)"
    if ! [[ "$p" =~ ^/[A-Za-z0-9._/-]+$ ]] || [[ "/$p/" == */../* ]] || [[ "$p" == */ ]]; then
      errs+=("$name: remote_path must be an absolute path to a file.")
    fi
    [[ "$(ef "$i" owner)" =~ ^[a-z_][a-z0-9_-]*$ ]] || errs+=("$name: owner is not a user name.")
    [[ "$(ef "$i" group)" =~ ^[a-z_][a-z0-9_-]*$ ]] || errs+=("$name: group is not a group name.")
    [[ "$(ef "$i" mode)" =~ ^0?[0-7]{3}$ ]] || errs+=("$name: mode must be octal, for example 0600.")
    if [ "${mft[entries.$i.restart]-}" != array ]; then
      errs+=("$name: restart must be a list of unit names. Use [] for none.")
    fi
    while IFS= read -r v; do
      [ -n "$v" ] || continue
      [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9@._-]*$ ]] || errs+=("$name: restart $v is not a unit name.")
    done <<< "$(el "$i" restart)"
    for p in "${!mft[@]}"; do
      [[ "$p" =~ ^entries\.$i\.health\.([A-Za-z0-9_-]+)$ ]] || continue
      k="${BASH_REMATCH[1]}"
      grep -qxF -- "$k" <<< "$(el "$i" restart)" || errs+=("$name: health names $k, which restart does not list.")
      [[ "${mf[$p]}" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]{1,5})?/[A-Za-z0-9._/-]*$ ]] \
        || errs+=("$name: the health URL of $k must be http://127.0.0.1 or localhost.")
    done
    if [ -n "${mft[entries.$i.post_push]+x}" ]; then
      [[ "${mf[entries.$i.post_push.start_unit]-}" =~ ^[A-Za-z0-9][A-Za-z0-9@._-]*$ ]] \
        || errs+=("$name: post_push.start_unit is not a unit name.")
      [[ "${mf[entries.$i.post_push.journal_match]-}" =~ ^[A-Za-z0-9][A-Za-z0-9\ ._:\(\)-]*$ ]] \
        || errs+=("$name: post_push.journal_match must be letters, digits, spaces and . _ : ( ) -")
      [[ "${mf[entries.$i.post_push.timeout_secs]-}" =~ ^[1-9][0-9]{0,4}$ ]] \
        || errs+=("$name: post_push.timeout_secs must be a whole number of seconds.")
    fi
    while IFS= read -r k; do
      [ -n "$k" ] || continue
      [[ "$k" =~ ^[A-Z][A-Z0-9_]{1,100}$ ]] || errs+=("$name: allowed key $k is not A-Z, 0-9 and _.")
    done <<< "$(el "$i" allowed_keys)"
    while IFS= read -r k; do
      [ -n "$k" ] || continue
      grep -qxF -- "$k" <<< "$(el "$i" allowed_keys)" || errs+=("$name: required key $k is not an allowed key.")
    done <<< "$(el "$i" required_keys)"
    if [ "$kind" = file ] && [ -n "$(el "$i" allowed_keys)" ]; then
      errs+=("$name: a file entry has no keys.")
    fi
    for p in "${!mft[@]}"; do
      [[ "$p" =~ ^entries\.$i\.validators\.([A-Za-z0-9_-]+)\.[0-9]+$ ]] || continue
      k="${BASH_REMATCH[1]}"
      v="${mf[$p]}"
      if [ "$kind" = file ]; then
        [ "$k" = file ] || errs+=("$name: a file entry takes validators under \"file\" only.")
        [[ " $file_validators " == *" $v "* ]] || errs+=("$name: $v is not a validator for a file.")
      else
        grep -qxF -- "$k" <<< "$(el "$i" allowed_keys)" || errs+=("$name: validator key $k is not an allowed key.")
        [[ " $env_validators " == *" $v "* ]] || errs+=("$name: $v is not a validator for an env key.")
      fi
    done
  done
  for ((j = 0; j < lo_count; j++)); do
    name="${mf[local_only.$j.name]-}"
    [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || errs+=("local_only $j: the name is not a-z, 0-9 and -.")
    case "$seen" in *" $name "*) errs+=("$name: the name is used two times.") ;; esac
    seen+="$name "
  done
  if [ "${#errs[@]}" -gt 0 ]; then
    printf 'ERROR: deploy/secrets/manifest.json: %s\n' "${errs[@]}" >&2
    exit 1
  fi
}

# entry NAME — print the index of the entry, or fail with the list of names.
entry() {
  local i
  for ((i = 0; i < ent_count; i++)); do
    if [ "$(ef "$i" name)" = "$1" ]; then printf '%s' "$i"; return 0; fi
  done
  printf 'ERROR: no entry is called "%s". The entries are:' "$1" >&2
  for ((i = 0; i < ent_count; i++)); do printf ' %s' "$(ef "$i" name)" >&2; done
  printf '\n' >&2
  exit 1
}

is_env_kind() { [ "$(ef "$1" kind)" != file ]; }
mode_digits() { local m="$1"; m="${m#0}"; printf '%s' "$m"; }

# ── Checks on the local file ──────────────────────────────────────────────────

# env_value_problem VALUE — print why the env file cannot hold VALUE, or print
# nothing. The same rule as value_problem() in acb_common/env_guard.py, and a
# test feeds both the same values. The reason never holds the value.
env_value_problem() {
  local v="$1"
  if [ "${#v}" -gt 4096 ]; then echo "it is longer than 4096 bytes"; return 0; fi
  if ! [[ "$v" =~ ^[!-~]*$ ]]; then
    echo "it holds a space, a control character or a character that is not ASCII"; return 0
  fi
  case "$v" in
    *[\$\`\\\'\"\;\&\|\<\>\(\)]*) echo "it holds a character that the shell reads as code"; return 0 ;;
  esac
  case "$v" in
    '~'*|'#'*|*':~'*) echo "it starts with ~ or #, or holds :~"; return 0 ;;
  esac
}

# run_validator NAME VALUE — true when VALUE passes.
run_validator() {
  local v="$2"
  case "$1" in
    non_empty) [ -n "$v" ] ;;
    https_url) [[ "$v" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%/-]*)?$ ]] ;;
    # 40 hex digits (a v4 key) or 64 (a v5 key), as backup_offbox.sh accepts.
    gpg_fingerprint) [[ "$v" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]] ;;
    absolute_path) [[ "$v" =~ ^/[A-Za-z0-9._/-]+$ ]] && [[ "/$v/" != */../* ]] ;;
    s3_bucket_name) [[ "$v" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] ;;
    *) return 1 ;;
  esac
}

# gpg_public_only FILE — true when FILE holds an armored PUBLIC key and no
# private key. The text check always runs. gpg runs too when it is installed.
gpg_public_only() {
  local f="$1" gh shown rc=0
  if grep -q 'PRIVATE KEY BLOCK' "$f"; then return 1; fi
  grep -q '^-----BEGIN PGP PUBLIC KEY BLOCK-----' "$f" || return 1
  if ! command -v gpg >/dev/null 2>&1; then
    warn "gpg is not installed, so only the text check of $(basename "$f") ran."
    return 0
  fi
  gh="$(mktemp -d "$drop/.gnupg-check.XXXXXX")"
  shown="$(gpg --homedir "$gh" --batch --with-colons --import-options show-only --import "$f" 2>/dev/null)" || rc=1
  gpgconf --homedir "$gh" --kill all >/dev/null 2>&1 || true
  rm -rf "$gh"
  [ "$rc" -eq 0 ] || return 1
  if grep -qE '^(sec|ssb):' <<< "$shown"; then return 1; fi
  grep -q '^pub:' <<< "$shown"
}

declare -a errs=() push_keys=()
declare -A lh=() rh=()

# read_local_env I — parse the local file of entry I. Sets push_keys (the
# allowed keys with a value, in manifest order), sd_val, and lh (key -> hash).
read_local_env() {
  local i="$1" f k
  f="$drop/$(ef "$i" local_file)"
  push_keys=(); lh=()
  sd_keys=(); sd_val=(); sd_bad=(); sd_dup=()
  [ -f "$f" ] || return 0
  sd_parse "$f"
  while IFS= read -r k; do
    [ -n "$k" ] || continue
    if [ -n "${sd_val[$k]-}" ]; then
      push_keys+=("$k")
      lh[$k]="$(printf '%s' "${sd_val[$k]}" | sd_sha)"
    fi
  done <<< "$(el "$i" allowed_keys)"
}

# validate_local I — check the local file of entry I against the manifest.
# Prints each problem by key name, and fails when there is one.
validate_local() {
  local i="$1" name f k v n problem allowed
  name="$(ef "$i" name)"
  f="$drop/$(ef "$i" local_file)"
  errs=()
  if [ ! -f "$f" ]; then
    die "$name: the local file $f does not exist. Run: scripts/secrets.sh init"
  fi
  if is_env_kind "$i"; then
    allowed="$(el "$i" allowed_keys)"
    read_local_env "$i"
    for n in "${sd_bad[@]}"; do errs+=("line $n is not KEY=value, a comment or a blank line."); done
    for k in "${sd_dup[@]}"; do errs+=("$k is set more than one time."); done
    for k in "${sd_keys[@]}"; do
      if ! grep -qxF -- "$k" <<< "$allowed"; then
        errs+=("$k is not an allowed key of $name. Check the spelling, or add the key to deploy/secrets/manifest.json.")
      fi
    done
    while IFS= read -r k; do
      [ -n "$k" ] || continue
      [ -n "${sd_val[$k]-}" ] || errs+=("$k is required, and it is missing or empty.")
    done <<< "$(el "$i" required_keys)"
    for k in "${push_keys[@]}"; do
      v="${sd_val[$k]}"
      problem="$(env_value_problem "$v")"
      [ -z "$problem" ] || errs+=("$k: $problem.")
      while IFS= read -r n; do
        [ -n "$n" ] || continue
        run_validator "$n" "$v" || errs+=("$k fails the check $n.")
      done <<< "$(vl "$i" "$k")"
    done
    if [ "${#errs[@]}" -eq 0 ] && [ "${#push_keys[@]}" -eq 0 ]; then
      errs+=("the file holds no value to push.")
    fi
  else
    [ -s "$f" ] || errs+=("the file is empty.")
    while IFS= read -r n; do
      [ -n "$n" ] || continue
      case "$n" in
        non_empty) [ -s "$f" ] || errs+=("the file fails the check non_empty.") ;;
        gpg_public_key_only)
          gpg_public_only "$f" || errs+=("the file fails the check gpg_public_key_only. It must hold one PUBLIC key and no private key.") ;;
      esac
    done <<< "$(vl "$i" file)"
  fi
  if [ "${#errs[@]}" -gt 0 ]; then
    f="$(ef "$i" local_file)"
    for k in "${errs[@]}"; do printf 'ERROR: %s (%s): %s\n' "$name" "$f" "$k" >&2; done
    exit 1
  fi
}

# check_local_perms FILE — on POSIX, refuse a file that the group or others
# can read. Windows has no such mode, so there it warns.
check_local_perms() {
  local f="$1" m
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      warn "Windows has no file mode to check. Only your user profile protects $(basename "$f")."
      return 0 ;;
  esac
  m="$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f")"
  if (( (8#$m & 8#077) != 0 )); then
    die "$f has mode $m, so the group or others can read it. Run: chmod 600 \"$f\""
  fi
}

# ── The remote state ──────────────────────────────────────────────────────────
r_exists=""; r_meta=""; r_sha=""; r_backup=""; r_fail=""; r_post=""; r_pruned=""
declare -a r_other=() r_logs=()

parse_remote() {
  local line tag a b c
  rh=(); r_other=(); r_logs=()
  r_exists=""; r_meta=""; r_sha=""; r_backup=""; r_fail=""; r_post=""; r_pruned=""
  while IFS= read -r line; do
    case "$line" in
      "R log "*) r_logs+=("${line#R log }"); continue ;;
      "R fail "*) r_fail="${line#R fail }"; continue ;;
      "R postcheck "*) r_post="${line#R postcheck }"; continue ;;
    esac
    read -r tag a b c <<< "$line"
    [ "$tag" = R ] || continue
    case "$a" in
      exists) r_exists="$b" ;;
      meta) r_meta="$b $c" ;;
      sha) r_sha="$b" ;;
      key) rh[$b]="$c" ;;
      other) r_other+=("$b") ;;
      backup) r_backup="$b" ;;
      pruned) r_pruned="$b" ;;
    esac
  done <<< "$1"
}

# probe I — read the remote state of entry I. False when ssh fails. The box
# hashes only the allowed keys of the entry.
probe() {
  local i="$1" out k
  local -a keys=()
  while IFS= read -r k; do [ -z "$k" ] || keys+=("$k"); done <<< "$(el "$i" allowed_keys)"
  out="$(remote probe "$(ef "$i" remote_path)" "$(ef "$i" kind)" "${keys[@]}" < /dev/null)" || return 1
  parse_remote "$out"
}

# combo — one hash over a set of key hashes, for a short summary.
combo() {
  local -n m="$1"
  local k
  for k in $(printf '%s\n' "${!m[@]}" | sort); do printf '%s %s\n' "$k" "${m[$k]}"; done | sd_sha | cut -c1-8
}

declare -a d_add=() d_chg=() d_rem=()
d_same=0

# compute_diff I — compare lh (local) and rh (remote) for entry I.
compute_diff() {
  local i="$1" k
  d_add=(); d_chg=(); d_rem=(); d_same=0
  for k in "${push_keys[@]}"; do
    if [ -z "${rh[$k]+x}" ]; then d_add+=("$k")
    elif [ "${rh[$k]}" != "${lh[$k]}" ]; then d_chg+=("$k")
    else d_same=$((d_same + 1)); fi
  done
  if [ "$(ef "$i" kind)" = env-file ]; then
    for k in $(printf '%s\n' "${!rh[@]}" "${r_other[@]}" | sort -u); do
      [ -n "${lh[$k]+x}" ] || d_rem+=("$k")
    done
  fi
}

# ── Commands ──────────────────────────────────────────────────────────────────

ensure_drop() {
  [ -d "$drop" ] || die "the drop folder $drop does not exist. Run: scripts/secrets.sh init"
}

write_template() {
  local i="$1" f="$2" k note
  {
    printf '# %s: %s\n' "$(ef "$i" name)" "$(ef "$i" notes)"
    printf '# A push writes this to %s on the box (%s:%s %s).\n' \
      "$(ef "$i" remote_path)" "$(ef "$i" owner)" "$(ef "$i" group)" "$(ef "$i" mode)"
    if [ "$(ef "$i" kind)" = env-merge ]; then
      printf '# A push changes ONLY the keys below in that file. It keeps every other line.\n'
    fi
    printf '# Write each line as KEY=value, with no quotes and no spaces.\n'
    printf '# The push refuses a key that is not in this template. An empty value is not sent.\n'
    if [ -z "$(el "$i" allowed_keys)" ]; then
      printf '#\n# The manifest allows no key here yet. To add one, put its name in\n'
      printf '# allowed_keys of %s in deploy/secrets/manifest.json, in a reviewed change.\n' "$(ef "$i" name)"
    fi
    while IFS= read -r k; do
      [ -n "$k" ] || continue
      note="${mf[entries.$i.key_notes.$k]-}"
      printf '\n'
      if [ -n "$note" ]; then printf '# %s\n' "$note"; fi
      printf '%s=%s\n' "$k" "${mf[entries.$i.prefill.$k]-}"
    done <<< "$(el "$i" allowed_keys)"
  } > "$f"
}

write_readme() {
  local f="$1" i j k
  {
    printf 'The Metorite secrets drop\n=========================\n\n'
    printf 'This folder holds secrets for the box in plain text. Only your user\n'
    printf 'account protects it. Do not copy it into a git checkout, a chat or a\n'
    printf 'shared drive. scripts/secrets.sh reads it. docs/secrets_drop.md in the\n'
    printf 'repository explains each command.\n\n'
    printf 'Steps:\n'
    printf '  1. Fill the values in each .env file here.\n'
    printf '  2. Run: scripts/secrets.sh status\n'
    printf '  3. Run: scripts/secrets.sh diff <name>\n'
    printf '  4. Run: scripts/secrets.sh push <name>\n\n'
    printf 'Files:\n'
    for ((i = 0; i < ent_count; i++)); do
      printf '\n  %s  (entry "%s", kind %s)\n' "$(ef "$i" local_file)" "$(ef "$i" name)" "$(ef "$i" kind)"
      printf '    Goes to %s on the box, %s:%s %s.\n' \
        "$(ef "$i" remote_path)" "$(ef "$i" owner)" "$(ef "$i" group)" "$(ef "$i" mode)"
      printf '    %s\n' "$(ef "$i" notes)"
    done
    for ((j = 0; j < lo_count; j++)); do
      for ((k = 0; ; k++)); do
        [ -n "${mft[local_only.$j.files.$k]+x}" ] || break
        printf '\n  %s  (local only, "%s")\n' "${mf[local_only.$j.files.$k]}" "${mf[local_only.$j.name]}"
      done
      printf '    %s\n' "${mf[local_only.$j.notes]-}"
    done
  } > "$f"
}

cmd_init() {
  local i f
  mkdir -p "$drop"
  chmod 700 "$HOME/.metorite" "$drop" 2>/dev/null || warn "could not set mode 700 on $drop."
  if command -v git >/dev/null 2>&1 && git -C "$drop" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "$drop is inside a git checkout. The drop folder must be outside every repository."
  fi
  say "drop folder: $drop"
  f="$drop/README.txt"
  if [ -e "$f" ]; then say "  kept     README.txt"; else write_readme "$f"; say "  created  README.txt"; fi
  for ((i = 0; i < ent_count; i++)); do
    f="$drop/$(ef "$i" local_file)"
    if [ -e "$f" ]; then
      say "  kept     $(ef "$i" local_file)"
    elif is_env_kind "$i"; then
      write_template "$i" "$f"
      say "  created  $(ef "$i" local_file)"
    else
      say "  absent   $(ef "$i" local_file)  (put the file here; README.txt says what it is)"
    fi
  done
}

cmd_status() {
  local only="${1:-}" i name f state lsum rsum fail=0 rc
  ensure_drop
  for ((i = 0; i < ent_count; i++)); do
    name="$(ef "$i" name)"
    [ -z "$only" ] || [ "$only" = "$name" ] || continue
    f="$drop/$(ef "$i" local_file)"
    local have_local=0 have_remote=0 same=0
    lsum="-"; rsum="-"
    if is_env_kind "$i"; then
      read_local_env "$i"
      if [ "${#push_keys[@]}" -gt 0 ]; then have_local=1; lsum="$(combo lh)"; fi
    elif [ -s "$f" ]; then
      have_local=1; lsum="$(sd_sha < "$f" | cut -c1-8)"
    fi
    rc=0; probe "$i" || rc=$?
    if [ "$rc" -ne 0 ]; then
      printf '%-20s %-14s local %-8s\n' "$name" "unreachable" "$lsum"
      fail=1; continue
    fi
    if [ "$r_exists" = 1 ]; then
      if is_env_kind "$i"; then
        if [ "$(ef "$i" kind)" = env-merge ]; then
          local k; local -A rsub=()
          while IFS= read -r k; do
            [ -n "$k" ] || continue
            if [ -n "${rh[$k]+x}" ]; then rsub[$k]="${rh[$k]}"; fi
          done <<< "$(el "$i" allowed_keys)"
          if [ "${#rsub[@]}" -gt 0 ]; then have_remote=1; rsum="$(combo rsub)"; fi
        elif [ "${#rh[@]}" -gt 0 ] || [ "${#r_other[@]}" -gt 0 ]; then
          have_remote=1; rsum="$(combo rh)"
        fi
        if [ "$have_local" = 1 ] && [ "$have_remote" = 1 ]; then
          compute_diff "$i"
          if [ "${#d_add[@]}" -eq 0 ] && [ "${#d_chg[@]}" -eq 0 ] && [ "${#d_rem[@]}" -eq 0 ]; then same=1; fi
        fi
      else
        have_remote=1; rsum="${r_sha:0:8}"
        if [ "$have_local" = 1 ] && [ "$(sd_sha < "$f")" = "$r_sha" ]; then same=1; fi
      fi
    fi
    if [ "$have_local" = 0 ] && [ "$have_remote" = 0 ]; then state="missing-local"
    elif [ "$have_local" = 0 ]; then state="remote-only"
    elif [ "$have_remote" = 0 ]; then state="local-only"
    elif [ "$same" = 1 ]; then state="in-sync"
    else state="differs"; fi
    printf '%-20s %-14s local %-8s  remote %-8s\n' "$name" "$state" "$lsum" "$rsum"
  done
  return "$fail"
}

# show_diff I — print the change, by key name. Needs probe and read_local_env.
show_diff() {
  local i="$1" k f
  say "diff $(ef "$i" name): local -> $host:$(ef "$i" remote_path)"
  if is_env_kind "$i"; then
    compute_diff "$i"
    for k in "${d_add[@]}"; do say "  + $k"; done
    for k in "${d_chg[@]}"; do say "  ~ $k"; done
    for k in "${d_rem[@]}"; do say "  - $k"; done
    say "  = $d_same key(s) the same"
    if [ "$(ef "$i" kind)" = env-merge ]; then say "  (a merge never removes a key, and it keeps every other line)"; fi
  else
    f="$drop/$(ef "$i" local_file)"
    k="$(sd_sha < "$f")"
    if [ "$r_exists" != 1 ]; then say "  + file (new)  local ${k:0:8}"
    elif [ "$k" = "$r_sha" ]; then say "  = file the same  ${k:0:8}"
    else say "  ~ file  local ${k:0:8}  remote ${r_sha:0:8}"; fi
  fi
}

has_change() {
  local i="$1"
  if is_env_kind "$i"; then
    [ "${#d_add[@]}" -gt 0 ] || [ "${#d_chg[@]}" -gt 0 ] || [ "${#d_rem[@]}" -gt 0 ]
  else
    [ "$r_exists" != 1 ] || [ "$(sd_sha < "$drop/$(ef "$i" local_file)")" != "$r_sha" ]
  fi
}

cmd_diff() {
  local i
  [ -n "${1:-}" ] || die "usage: scripts/secrets.sh diff <name>"
  ensure_drop
  i="$(entry "$1")"
  validate_local "$i"
  probe "$i" || die "the probe on $host failed. Read the line above."
  show_diff "$i"
}

log_push() {
  printf '%s push %s host=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$host" "$2" >> "$drop/push.log"
}

confirm() {
  local ans
  if [ ! -t 0 ]; then die "$1 needs a yes. There is no terminal to ask, so add --yes."; fi
  printf '%s Type yes to go on: ' "$1"
  read -r ans
  [ "$ans" = yes ] || die "you did not type yes. Nothing changed."
}

unit_fail=""

# restart_units I — restart each unit of entry I, in order, and prove each one
# healthy on the box (r_restart). False at the first failure, with unit_fail set.
restart_units() {
  local i="$1" u url out rc
  unit_fail=""
  while IFS= read -r u; do
    [ -n "$u" ] || continue
    url="${mf[entries.$i.health.$u]-}"
    rc=0; out="$(remote restart "$u" "$url" < /dev/null)" || rc=$?
    parse_remote "$out"
    if [ "$rc" -ne 0 ]; then
      unit_fail="${r_fail:-$u failed its check (exit $rc)}"
      return 1
    fi
    if [ -n "$url" ]; then say "restart: $u is healthy, and $url answers 200"
    else say "restart: $u is healthy"; fi
  done <<< "$(el "$i" restart)"
  return 0
}

# redact LINE — replace each local value of the entry with [REDACTED:<key>].
# The box does the same with its own values first. This is the second layer.
redact() {
  local line="$1" k v
  for k in "${!sd_val[@]}"; do
    v="${sd_val[$k]}"
    if [ "${#v}" -ge 4 ]; then line="${line//"$v"/[REDACTED:$k]}"; fi
  done
  printf '%s\n' "$line"
}

# post_step I NAME VERIFY — the post-push check of entry I, when it has one.
# It runs only with --verify, because it can take many minutes.
post_step() {
  local i="$1" name="$2" verify="$3" unit match secs about out rc line
  [ -n "${mft[entries.$i.post_push]+x}" ] || return 0
  unit="${mf[entries.$i.post_push.start_unit]}"
  match="${mf[entries.$i.post_push.journal_match]}"
  secs="${mf[entries.$i.post_push.timeout_secs]}"
  about="${mf[entries.$i.post_push.about_minutes]-some}"
  if [ "$verify" != 1 ]; then
    say "post-push check: not run. It starts $unit and takes about $about minutes. To run it:"
    say "  scripts/secrets.sh push $name --verify"
    return 0
  fi
  say "post-push check: start $unit and wait for it (about $about minutes, $secs seconds at most)."
  rc=0; out="$(remote postcheck "$unit" "$match" "$secs" "$(ef "$i" remote_path)" < /dev/null)" || rc=$?
  parse_remote "$out"
  for line in "${r_logs[@]}"; do say "  | $(redact "$line")"; done
  if [ "$rc" -eq 0 ] && [ "$r_post" = pass ]; then
    log_push "$name" "result=post-push-pass"
    say "post-push check: PASS. The journal of $unit holds '$match'."
    return 0
  fi
  log_push "$name" "result=post-push-fail"
  die "post-push check: FAIL. The journal of $unit does not hold '$match'. Run on the box: sudo journalctl -u $unit -n 80"
}

# roll_back I NAME PRE_SHA BACKUP WANT — after a failed restart: put the old
# file back, verify it, restart again, and fail with ROLLED BACK.
roll_back() {
  local i="$1" name="$2" pre="$3" bak="$4" want="$5" rp out rc first again
  rp="$(ef "$i" remote_path)"
  first="$unit_fail"
  warn "$first"
  say "rollback: put the old file back on $host:$rp."
  rc=0; out="$(remote rollback "$rp" "$bak" < /dev/null)" || rc=$?
  parse_remote "$out"
  if [ "$rc" -ne 0 ]; then
    log_push "$name" "sha=$want result=rollback-failed"
    die "ROLLBACK FAILED (exit $rc). The new file is still on the box. Backup: $bak"
  fi
  if [ "$bak" != none ] && [ "$r_sha" != "$pre" ]; then
    log_push "$name" "sha=$want result=rollback-failed"
    die "ROLLBACK FAILED: the file on the box does not match the old file. Backup: $bak"
  fi
  if [ "$bak" = none ]; then say "rollback: the new file is gone. There was no old file."
  else say "rollback: the old file is back, hash ${pre:0:8} verified."; fi
  if restart_units "$i"; then again="the units are healthy with the old file"
  else again="the units are NOT healthy with the old file either: $unit_fail"; fi
  log_push "$name" "sha=$want result=rolled-back"
  printf 'ERROR: ROLLED BACK. %s. The old file is back on %s, and %s.\n' "$first" "$host" "$again" >&2
  exit 2
}

cmd_push() {
  local name="" yes=0 verify=0 all=0 a i f kind rp owner group mode payload="" k out rc want bad=0 pre bak
  local -a flags=()
  for a in "$@"; do
    case "$a" in
      --yes) yes=1; flags+=("$a") ;;
      --verify) verify=1; flags+=("$a") ;;
      --all) all=1 ;;
      -*) die "push: unknown option $a" ;;
      *) [ -z "$name" ] || die "usage: scripts/secrets.sh push <name>|--all [--yes] [--verify]"; name="$a" ;;
    esac
  done
  if [ "$all" = 1 ]; then
    [ -z "$name" ] || die "push: give a name or --all, not both."
    cmd_push_all "${flags[@]}"
    return
  fi
  [ -n "$name" ] || die "usage: scripts/secrets.sh push <name>|--all [--yes] [--verify]"
  ensure_drop
  i="$(entry "$name")"
  f="$drop/$(ef "$i" local_file)"
  kind="$(ef "$i" kind)"
  rp="$(ef "$i" remote_path)"
  owner="$(ef "$i" owner)"; group="$(ef "$i" group)"; mode="$(ef "$i" mode)"

  validate_local "$i"
  check_local_perms "$f"
  probe "$i" || die "the probe on $host failed. Read the line above. Nothing changed."
  pre="$r_sha"
  if [ "$kind" = env-merge ] && [ "$r_exists" != 1 ]; then
    die "$rp does not exist on $host. A merge needs the env file to exist. Nothing changed."
  fi
  show_diff "$i"
  if ! has_change "$i"; then
    say "in-sync: nothing to push."
    post_step "$i" "$name" "$verify"
    return 0
  fi
  if [ "$yes" != 1 ]; then confirm "Push $name to $host:$rp?"; fi

  if [ "$kind" = file ]; then
    rc=0; out="$(remote write "$rp" "$owner" "$group" "$mode" replace < "$f")" || rc=$?
    want="$(sd_sha < "$f")"
  else
    if [ "$kind" = env-file ]; then
      payload="# Written by scripts/secrets.sh push $name. Edit the drop folder, not this file."$'\n'
    fi
    for k in "${push_keys[@]}"; do payload+="$k=${sd_val[$k]}"$'\n'; done
    if [ "$kind" = env-file ]; then
      want="$(printf '%s' "$payload" | sd_sha)"
      rc=0; out="$(printf '%s' "$payload" | remote write "$rp" "$owner" "$group" "$mode" replace)" || rc=$?
    else
      rc=0; out="$(printf '%s' "$payload" | remote write "$rp" "$owner" "$group" "$mode" merge)" || rc=$?
    fi
    payload=""
  fi
  parse_remote "$out"
  if [ "$rc" -ne 0 ]; then
    log_push "$name" "result=write-failed"
    die "the write on $host failed (exit $rc). Read the lines above. Backup: ${r_backup:-none}."
  fi
  say "backup: ${r_backup:-none} (older backups pruned: ${r_pruned:-0}, the newest 3 stay)"

  if [ "$kind" = env-merge ]; then
    for k in "${push_keys[@]}"; do
      [ "${rh[$k]-}" = "${lh[$k]}" ] || { warn "$k: the hash on the box does not match."; bad=1; }
    done
    want="$(combo lh)"
  else
    [ "$r_sha" = "$want" ] || { warn "the file hash on the box does not match."; bad=1; }
    want="${want:0:8}"
  fi
  if [ "$r_meta" != "$owner:$group $(mode_digits "$mode")" ]; then
    warn "the file on the box is '$r_meta', and the manifest says '$owner:$group $(mode_digits "$mode")'."
    bad=1
  fi
  if [ "$bad" -ne 0 ]; then
    log_push "$name" "sha=$want result=verify-failed"
    die "the verify failed, so no unit was restarted. The old file is at ${r_backup:-none}."
  fi
  say "wrote: $rp ($r_meta), hash $want verified"

  # Keep the backup path now: each remote call below resets r_backup.
  bak="${r_backup:-none}"
  if [ -n "$(el "$i" restart)" ]; then
    restart_units "$i" || roll_back "$i" "$name" "$pre" "$bak" "$want"
  else
    say "restart: none"
  fi
  log_push "$name" "sha=$want result=ok"
  say "OK"
  post_step "$i" "$name" "$verify"
}

# cmd_push_all [--yes] [--verify] — push every entry that has a local value,
# in manifest order. Each push runs in its own process, so a failure stops
# the run before the next entry.
cmd_push_all() {
  local i name f k rc pushed=0 missing
  ensure_drop
  for ((i = 0; i < ent_count; i++)); do
    name="$(ef "$i" name)"
    f="$drop/$(ef "$i" local_file)"
    if is_env_kind "$i"; then
      if [ ! -f "$f" ]; then say "skip $name: there is no local file."; continue; fi
      read_local_env "$i"
      missing=""
      while IFS= read -r k; do
        [ -n "$k" ] || continue
        [ -n "${sd_val[$k]-}" ] || missing+=" $k"
      done <<< "$(el "$i" required_keys)"
      if [ -n "$missing" ]; then say "skip $name: these required keys are empty:$missing"; continue; fi
      if [ "${#push_keys[@]}" -eq 0 ]; then say "skip $name: the file holds no value to push."; continue; fi
    elif [ ! -s "$f" ]; then
      say "skip $name: there is no local file."; continue
    fi
    say "== push $name"
    rc=0; bash "${BASH_SOURCE[0]}" push "$name" "$@" || rc=$?
    if [ "$rc" -ne 0 ]; then
      printf 'ERROR: push --all stopped at %s (exit %s). The entries after it were not pushed.\n' "$name" "$rc" >&2
      exit "$rc"
    fi
    pushed=$((pushed + 1))
  done
  say "push --all: $pushed entries pushed or already in sync."
}

# shred_file FILE — overwrite FILE, then delete it. Sets shred_how.
shred_how=""
shred_file() {
  local f="$1" size
  if command -v shred >/dev/null 2>&1; then
    shred -u -z -n 3 -- "$f"
    shred_how="shred"
  else
    size="$(wc -c < "$f" | tr -d ' ')"
    dd if=/dev/urandom of="$f" bs=1 count="$size" conv=notrunc 2>/dev/null
    rm -f -- "$f"
    shred_how="overwrite, then delete"
  fi
}

# shred_tree DIR — shred each file in DIR, then remove DIR.
shred_tree() {
  local x
  while IFS= read -r -d '' x; do shred_file "$x"; done < <(find "$1" -type f -print0)
  rm -rf -- "$1"
}

cmd_gen() {
  local what="${1:-}" force=0 a pub priv pass gh pw fpr stamp i f
  shift || true
  for a in "$@"; do
    case "$a" in --force) force=1 ;; *) die "gen: unknown option $a" ;; esac
  done
  [ "$what" = backup-gpg ] || die "usage: scripts/secrets.sh gen backup-gpg [--force]"
  command -v gpg >/dev/null 2>&1 || die "gpg is not installed. Install GnuPG, then run this again."
  ensure_drop
  pub="$drop/backup-gpg-public.asc"
  priv="$drop/backup-gpg-PRIVATE.asc"
  pass="$drop/backup-gpg-passphrase.txt"
  if [ -s "$pub" ] || [ -e "$priv" ] || [ -e "$pass" ]; then
    if [ "$force" != 1 ]; then
      die "a backup key pair is already in the drop folder. --force makes a new one and SHREDS the old private key and passphrase. Save them in your password manager first."
    fi
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    if [ -e "$pub" ]; then
      mv "$pub" "$pub.old-$stamp"
      say "kept the old backup-gpg-public.asc as backup-gpg-public.asc.old-$stamp"
    fi
    for f in "$priv" "$pass"; do
      if [ -e "$f" ]; then shred_file "$f"; say "shredded the old $(basename "$f") ($shred_how)"; fi
    done
  fi

  gh="$(mktemp -d "$drop/.gnupg-gen.XXXXXX")"
  chmod 700 "$gh"
  # shellcheck disable=SC2064  # expand $gh now: it is a fixed path.
  trap "gpgconf --homedir '$gh' --kill all >/dev/null 2>&1 || true; rm -rf '$gh'" EXIT
  pw="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
  printf '%s\n' "${pw:0:40}" > "$gh/pass"
  pw=""
  gpg --homedir "$gh" --batch --quiet --pinentry-mode loopback --passphrase-file "$gh/pass" \
    --quick-gen-key "Metorite off-box backup" ed25519 cert never >/dev/null 2>&1 \
    || die "gpg could not make the key. Nothing was written."
  fpr="$(gpg --homedir "$gh" --batch --with-colons --list-keys 2>/dev/null | awk -F: '/^fpr:/ && !d { print $10; d = 1 }')"
  [[ "$fpr" =~ ^([0-9A-F]{40}|[0-9A-F]{64})$ ]] || die "gpg did not give a full fingerprint. Nothing was written."
  gpg --homedir "$gh" --batch --quiet --pinentry-mode loopback --passphrase-file "$gh/pass" \
    --quick-add-key "$fpr" cv25519 encr never >/dev/null 2>&1 \
    || die "gpg could not add the encryption subkey. Nothing was written."
  gpg --homedir "$gh" --batch --armor --export "$fpr" > "$gh/pub.asc" 2>/dev/null
  gpg --homedir "$gh" --batch --pinentry-mode loopback --passphrase-file "$gh/pass" \
    --armor --export-secret-keys "$fpr" > "$gh/priv.asc" 2>/dev/null \
    || die "gpg could not export the private key. Nothing was written."
  [ -s "$gh/pub.asc" ] && [ -s "$gh/priv.asc" ] || die "gpg wrote an empty key file. Nothing was written."
  mv "$gh/pub.asc" "$pub"
  mv "$gh/priv.asc" "$priv"
  mv "$gh/pass" "$pass"

  i="$(entry backup-offbox)"
  f="$drop/$(ef "$i" local_file)"
  if [ ! -e "$f" ]; then write_template "$i" "$f"; fi
  # shellcheck disable=SC2034  # sd_merge (in sd_lib) reads both.
  m_keys=(BACKUP_GPG_RECIPIENT)
  # shellcheck disable=SC2034
  m_val=([BACKUP_GPG_RECIPIENT]="$fpr")
  sd_merge "$f" > "$f.tmp"
  mv "$f.tmp" "$f"

  say "fingerprint: $fpr"
  say "wrote: backup-gpg-public.asc, backup-gpg-PRIVATE.asc, backup-gpg-passphrase.txt"
  say "set: BACKUP_GPG_RECIPIENT in $(basename "$f")"
  say "Copy backup-gpg-PRIVATE.asc and backup-gpg-passphrase.txt into your password manager, then run \`scripts/secrets.sh forget backup-gpg-private\`."
}

# cmd_forget KEY [--yes] — shred the local files of KEY, every <file>.old-*
# copy of them, and any .gnupg-gen.* folder that a stopped `gen` left.
cmd_forget() {
  local key="" yes=0 a i j k f o d
  local -a files=() targets=() gens=()
  for a in "$@"; do
    case "$a" in
      --yes) yes=1 ;;
      -*) die "forget: unknown option $a" ;;
      *) [ -z "$key" ] || die "usage: scripts/secrets.sh forget <key> [--yes]"; key="$a" ;;
    esac
  done
  [ -n "$key" ] || die "usage: scripts/secrets.sh forget <key> [--yes]"
  ensure_drop
  for ((j = 0; j < lo_count; j++)); do
    if [ "${mf[local_only.$j.name]}" = "$key" ]; then
      for ((k = 0; ; k++)); do
        [ -n "${mft[local_only.$j.files.$k]+x}" ] || break
        files+=("${mf[local_only.$j.files.$k]}")
      done
    fi
  done
  if [ "${#files[@]}" -eq 0 ]; then
    for ((i = 0; i < ent_count; i++)); do
      if [ "$(ef "$i" name)" = "$key" ]; then files+=("$(ef "$i" local_file)"); fi
    done
  fi
  if [ "${#files[@]}" -eq 0 ]; then
    printf 'ERROR: "%s" is not a name in the manifest. Use an entry name or a local_only name:' "$key" >&2
    for ((i = 0; i < ent_count; i++)); do printf ' %s' "$(ef "$i" name)" >&2; done
    for ((j = 0; j < lo_count; j++)); do printf ' %s' "${mf[local_only.$j.name]}" >&2; done
    printf '\n' >&2
    exit 1
  fi
  for f in "${files[@]}"; do
    if [ -f "$drop/$f" ]; then targets+=("$drop/$f"); else say "absent: $f"; fi
    for o in "$drop/$f".old-*; do
      if [ -f "$o" ]; then targets+=("$o"); fi
    done
  done
  for d in "$drop"/.gnupg-gen.*; do
    if [ -d "$d" ]; then gens+=("$d"); fi
  done
  if [ "${#targets[@]}" -eq 0 ] && [ "${#gens[@]}" -eq 0 ]; then say "nothing to forget."; return 0; fi
  if [ "$yes" != 1 ]; then
    confirm "Delete ${#targets[*]} file(s) and ${#gens[*]} leftover gpg folder(s) from $drop for good?"
  fi
  for f in "${targets[@]}"; do shred_file "$f"; say "forgot: $(basename "$f") ($shred_how)"; done
  for d in "${gens[@]}"; do shred_tree "$d"; say "forgot: $(basename "$d")/ (a leftover gpg folder)"; done
}

usage() {
  grep '^#   scripts/secrets.sh' "${BASH_SOURCE[0]}" | sed 's/^#   //'
}

main() {
  local cmd="${1:-help}"
  shift || true
  case "$cmd" in
    help|-h|--help) usage; return 0 ;;
  esac
  load_manifest
  case "$cmd" in
    init) cmd_init ;;
    status) cmd_status "$@" ;;
    diff) cmd_diff "$@" ;;
    push) cmd_push "$@" ;;
    gen) cmd_gen "$@" ;;
    forget) cmd_forget "$@" ;;
    *) usage >&2; die "unknown command: $cmd" ;;
  esac
}

main "$@"
