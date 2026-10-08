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
#   scripts/secrets.sh push <name> [--yes]      check, back up, write, verify, restart
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

r_probe() {
  local path="$1" how="$2" k
  if [ ! -e "$path" ]; then echo "R exists 0"; return 0; fi
  if [ ! -f "$path" ]; then echo "ERROR: $path is not a regular file." >&2; exit 3; fi
  echo "R exists 1"
  echo "R meta $(stat -c '%U:%G %a' "$path")"
  echo "R sha $(sd_sha < "$path")"
  if [ "$how" = env ]; then
    sd_parse "$path"
    for k in "${sd_keys[@]}"; do echo "R key $k $(printf '%s' "${sd_val[$k]}" | sd_sha)"; done
  fi
}

r_write() {
  local path="$1" owner="$2" group="$3" mode="$4" how="$5" dir bak k
  dir="$(dirname "$path")"
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
  trap 'rm -f "$r_tmp"' EXIT
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
    bak="$path.bak-$(date -u +%Y%m%dT%H%M%SZ)"
    if [ -e "$bak" ]; then bak="$bak.$$"; fi
    cp -p "$path" "$bak"
    echo "R backup $bak"
  else
    echo "R backup none"
  fi
  chown "$owner:$group" "$r_tmp"
  chmod "$mode" "$r_tmp"
  echo "R meta $(stat -c '%U:%G %a' "$r_tmp")"
  mv -f "$r_tmp" "$path"
  trap - EXIT
  echo "R sha $(sd_sha < "$path")"
  if [ "$how" = merge ]; then
    sd_parse "$path"
    for k in "${m_keys[@]}"; do echo "R key $k $(printf '%s' "${sd_val[$k]-}" | sd_sha)"; done
  fi
}

r_restart() {
  local unit="$1" n
  systemctl restart "$unit"
  for n in $(seq 1 30); do
    if systemctl is-active --quiet "$unit"; then echo "R active yes"; return 0; fi
    sleep 1
  done
  echo "R active no"
  exit 4
}

verb="${1:?}"; shift
case "$verb" in
  probe) r_probe "$@" ;;
  write) r_write "$@" ;;
  restart) r_restart "$@" ;;
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
  ssh -T -o ConnectTimeout=15 "$host" "$cmd"
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

env_validators="non_empty https_url fingerprint_40_hex absolute_path s3_bucket_name"
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
    v="$(ef "$i" restart)"
    if [ -n "$v" ] && ! [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9@._-]*$ ]]; then
      errs+=("$name: restart is not a unit name.")
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
    fingerprint_40_hex) [[ "$v" =~ ^[0-9A-Fa-f]{40}$ ]] ;;
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
r_exists=""; r_meta=""; r_sha=""; r_backup=""; r_active=""

parse_remote() {
  local tag a b c
  rh=(); r_exists=""; r_meta=""; r_sha=""; r_backup=""; r_active=""
  while read -r tag a b c; do
    [ "$tag" = R ] || continue
    case "$a" in
      exists) r_exists="$b" ;;
      meta) r_meta="$b $c" ;;
      sha) r_sha="$b" ;;
      key) rh[$b]="$c" ;;
      backup) r_backup="$b" ;;
      active) r_active="$b" ;;
    esac
  done <<< "$1"
}

# probe I — read the remote state of entry I. False when ssh fails.
probe() {
  local i="$1" out how=file
  if is_env_kind "$i"; then how="env"; fi
  out="$(remote probe "$(ef "$i" remote_path)" "$how" < /dev/null)" || return 1
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
    for k in $(printf '%s\n' "${!rh[@]}" | sort); do
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
        elif [ "${#rh[@]}" -gt 0 ]; then
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
    if [ "$have_local" = 0 ] && [ "$have_remote" = 0 ]; then state=missing-local
    elif [ "$have_local" = 0 ]; then state=remote-only
    elif [ "$have_remote" = 0 ]; then state=local-only
    elif [ "$same" = 1 ]; then state=in-sync
    else state=differs; fi
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
  probe "$i" || die "ssh to $host failed."
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

cmd_push() {
  local name="" yes=0 a i f kind rp owner group mode unit payload="" k out rc want bad=0
  for a in "$@"; do
    case "$a" in
      --yes) yes=1 ;;
      -*) die "push: unknown option $a" ;;
      *) [ -z "$name" ] || die "usage: scripts/secrets.sh push <name> [--yes]"; name="$a" ;;
    esac
  done
  [ -n "$name" ] || die "usage: scripts/secrets.sh push <name> [--yes]"
  ensure_drop
  i="$(entry "$name")"
  f="$drop/$(ef "$i" local_file)"
  kind="$(ef "$i" kind)"
  rp="$(ef "$i" remote_path)"
  owner="$(ef "$i" owner)"; group="$(ef "$i" group)"; mode="$(ef "$i" mode)"
  unit="$(ef "$i" restart)"

  validate_local "$i"
  check_local_perms "$f"
  probe "$i" || die "ssh to $host failed. Nothing changed."
  if [ "$kind" = env-merge ] && [ "$r_exists" != 1 ]; then
    die "$rp does not exist on $host. A merge needs the env file to exist. Nothing changed."
  fi
  show_diff "$i"
  if ! has_change "$i"; then say "in-sync: nothing to push."; return 0; fi
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
  say "backup: ${r_backup:-none}"

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

  if [ -n "$unit" ]; then
    rc=0; out="$(remote restart "$unit" < /dev/null)" || rc=$?
    parse_remote "$out"
    if [ "$rc" -ne 0 ] || [ "$r_active" != yes ]; then
      log_push "$name" "sha=$want result=restart-failed unit=$unit"
      die "$unit is not active after the restart. Run on the box: sudo journalctl -u $unit -n 50"
    fi
    say "restart: $unit is active"
  else
    say "restart: none"
  fi
  log_push "$name" "sha=$want result=ok"
  say "OK"
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
      die "a backup key pair is already in the drop folder. To make a new one, add --force. The old files are kept as .old-<stamp>."
    fi
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    for f in "$pub" "$priv" "$pass"; do
      if [ -e "$f" ]; then mv "$f" "$f.old-$stamp"; say "kept the old $(basename "$f") as $(basename "$f").old-$stamp"; fi
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
  [[ "$fpr" =~ ^[0-9A-F]{40}$ ]] || die "gpg did not give a 40-digit fingerprint. Nothing was written."
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

cmd_forget() {
  local key="" yes=0 a i j k files=() f size
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
  if [ "$yes" != 1 ]; then confirm "Delete ${files[*]} from $drop for good?"; fi
  for f in "${files[@]}"; do
    if [ ! -e "$drop/$f" ]; then say "absent: $f"; continue; fi
    if command -v shred >/dev/null 2>&1; then
      shred -u -z -n 3 "$drop/$f"
      say "forgot: $f (shred)"
    else
      size="$(wc -c < "$drop/$f" | tr -d ' ')"
      dd if=/dev/urandom of="$drop/$f" bs=1 count="$size" conv=notrunc 2>/dev/null
      rm -f "$drop/$f"
      say "forgot: $f (overwrite, then delete)"
    fi
  done
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
