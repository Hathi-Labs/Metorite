#!/usr/bin/env bash
# H-123 — send ONE night off the box, to Supabase Storage through S3, encrypted.
#
# backup_db.sh runs this ONLY when it was given --offbox, and only
# acb-backup.service passes that flag. backup_db.sh runs it under `timeout`
# (BACKUP_S3_TIMEOUT_SECS), so a hung upload cannot hold the unit past its
# TimeoutStartSec. `timeout` signals the whole process group, rclone included.
#
# Usage: backup_offbox.sh <tonight's backup dir> <stamp> <app dir> [<key file>]
#        <key file> defaults to /etc/acb/backup-offbox.env. backup_db.sh
#        passes its pinned path. No env name can move it.
# Env:   BACKUP_FILE_DIRS and BACKUP_MEETING_BOT_VOLUME, both VALIDATED.
#        Every BACKUP_S3_* and BACKUP_GPG_* name comes from the key file ONLY,
#        never from the env (BH-6a). backup_db.sh's header describes each one.
# Exit:  0 when the night is up AND the bucket retention ran clean, else 1.
#
# 🔴 **The bucket key is for root ONLY.** A Supabase S3 key is PROJECT-WIDE: it
# passes Row Level Security and can read or delete every object in every
# bucket of the project. So it lives in /etc/acb/backup-offbox.env (root:root,
# 0600), and only acb-backup.service loads that file. /opt/acb/app/.env is the
# env file of acb-gateway (User=acb) and of the WhatsApp bridge, and the
# gateway's in-process Copilot CLI inherits that env (H-270). This script
# refuses to run as any user but root, and refuses a key file that is not
# root:root 0600. It reads the key file itself, and drops the same names from
# the env it inherits first: the unit also loads /opt/acb/app/.env, which acb
# can write (BH-6a). backup_db.sh turns a BACKUP_S3_*, BACKUP_GPG_* or
# BACKUP_OFFBOX_ENV_FILE line in that file into an ERROR.
# ⚠️ That closes the PASSIVE paths only: the inherited env, /proc/<pid>/environ
# and env dumps in logs or crash reports. `acb` is equivalent to root on this
# box: passwordless sudo, the docker group, and it owns this very script. So
# an ACTIVE compromise of the app can still read the key and delete every
# off-box night. The off-box copy protects against the loss of the VPS, the
# disk or the provider account, NOT against a compromise of the app. H-271.
#
# 🔴 **Encrypted on the box, or not sent at all.** zstd compresses each item,
# and gpg encrypts it to the owner's PUBLIC key. The box never holds the
# private key. The key is checked in full BEFORE anything is staged.
set -euo pipefail
# PATH first, before any external command (BH-6a). Up to the clean start
# below, this script runs builtins only.
PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
export PATH
unset CDPATH

dest="${1:?usage: backup_offbox.sh <backup dir> <stamp> <app dir> [<key file>]}"
stamp="${2:?usage: backup_offbox.sh <backup dir> <stamp> <app dir> [<key file>]}"
app_dir="${3:?usage: backup_offbox.sh <backup dir> <stamp> <app dir> [<key file>]}"
key_env_file="${4:-/etc/acb/backup-offbox.env}"
case "${BASH_SOURCE[0]}" in
  */*) here="${BASH_SOURCE[0]%/*}" ;;
  *) here=. ;;
esac
here="$(cd "$here" && pwd)"
# shellcheck source=scripts/offbox_lib.sh
. "$here/offbox_lib.sh"
# 🔴 The ALLOW list (fix round 1). This script runs as root, as the child of
# backup_db.sh (whose env is already clean) or by hand. Either way it starts
# again under `env -i` with only the two names it reads, so tar, gpg, zstd and
# docker see no TAR_OPTIONS, proxy or loader name. rclone gets its own, still
# smaller, allow list (offbox_env_only_rclone).
if [ "$EUID" = "0" ] || [ "${BACKUP_ENV_GUARD:-0}" = "1" ]; then
  offbox_clean_env_reexec "$here/backup_offbox.sh" BACKUP_FILE_DIRS BACKUP_MEETING_BOT_VOLUME \
    -- "$dest" "$stamp" "$app_dir" "$key_env_file"
fi
offbox_rclone_allowlist=1

# 🔴 The ONE shape of the dir this script stages in and deletes (BH-6a):
# <absolute dir>/<stamp>/offbox.work. Checked before the trap is set, so a
# bad argument can never reach the `rm -rf` of the cleanup.
if ! [[ "$stamp" =~ $offbox_stamp_re ]] || [ "${dest#/}" = "$dest" ] \
   || [ "${dest%/"$stamp"}/$stamp" != "$dest" ] || [ "${dest%/"$stamp"}" = "" ]; then
  echo "ERROR: refusing to stage in '$dest'. It must be <absolute dir>/<stamp>." >&2
  echo "       Nothing was uploaded." >&2
  exit 1
fi
work="$dest/offbox.work"
gpg_home="$work/gnupg"
# rm_work — `rm -rf` of the staging dir, and of nothing else.
rm_work() {
  case "$work" in
    /*/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z/offbox.work)
      rm -rf -- "$work" ;;
    *) echo "ERROR: refusing to delete '$work'." >&2; return 1 ;;
  esac
}
cleanup() {
  gpgconf --homedir "$gpg_home" --kill all >/dev/null 2>&1 || true
  rm_work
}
trap cleanup EXIT
# A signal from `timeout` or systemd becomes an exit, so the trap runs.
trap 'exit 143' TERM INT HUP

fail() {
  echo "ERROR: $*" >&2
  echo "       Nothing was uploaded." >&2
  exit 1
}

# ── 1. Only root holds the bucket key ─────────────────────────────────────────
# FIRST, before any setting is read. The key file is the ONLY source of the
# BACKUP_S3_* and BACKUP_GPG_* names: offbox_load_root_file drops them from
# the env, then reads the file. The env came in part from /opt/acb/app/.env,
# which acb can write, so a name there must not fall through (BH-6a).
uid="$(id -u)"
if [ "$uid" != "0" ]; then
  fail "the off-box copy runs as uid $uid. Only root may hold the bucket key."
fi
offbox_root_file_ok "$key_env_file" || fail "the key file is not trusted (see above)."
offbox_load_root_file "$key_env_file"

# ── 2. The settings ───────────────────────────────────────────────────────────
offbox_settings || fail "the BACKUP_S3_* settings are not valid (see above)."
if ! keep="$(offbox_uint "${BACKUP_S3_KEEP-14}")" || [ "$keep" -lt 1 ]; then
  fail "BACKUP_S3_KEEP '${BACKUP_S3_KEEP-}' must be a whole number of 1 or more."
fi

for tool in rclone gpg zstd tar sha256sum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    fail "the off-box copy needs '$tool', and it is not on PATH. scripts/vps_apply.sh installs it."
  fi
done

# ── 3. The gpg key, checked in full before any data is touched ────────────────
rm_work
mkdir -p "$gpg_home"
chmod 700 "$work" "$gpg_home"
key_fail() {
  echo "ERROR: $*" >&2
  echo "       Nothing was uploaded. The off-box copy never sends plaintext." >&2
  exit 1
}
fpr="$(printf '%s' "${BACKUP_GPG_RECIPIENT:-}" | tr -d ' ' | tr 'a-f' 'A-F')"
key_file="${BACKUP_GPG_PUBLIC_KEY_FILE:-}"
if ! [[ "$fpr" =~ ^([0-9A-F]{40}|[0-9A-F]{64})$ ]]; then
  key_fail "BACKUP_GPG_RECIPIENT must be the full fingerprint of a key (40 or 64 hex digits)."
fi
if [ -z "$key_file" ] || [ ! -r "$key_file" ]; then
  key_fail "BACKUP_GPG_PUBLIC_KEY_FILE '$key_file' is not a readable file."
fi
if ! shown="$(gpg --homedir "$gpg_home" --batch --with-colons \
                --import-options show-only --import "$key_file" 2>/dev/null)"; then
  key_fail "the file $key_file holds no OpenPGP key that gpg can read."
fi
if grep -qE '^(sec|ssb):' <<< "$shown"; then
  key_fail "the file $key_file holds a PRIVATE key. Put only the PUBLIC key on the box."
fi
if ! gpg --homedir "$gpg_home" --batch --quiet --import "$key_file" >/dev/null 2>&1; then
  key_fail "gpg could not import $key_file."
fi
listing="$(gpg --homedir "$gpg_home" --batch --with-colons --with-fingerprint \
             --with-subkey-fingerprint --list-keys "$fpr" 2>/dev/null || true)"
if ! grep -q "^fpr:::::::::$fpr:" <<< "$listing"; then
  key_fail "no key in $key_file has the exact fingerprint $fpr."
fi
pub="$(grep -m1 '^pub:' <<< "$listing" || true)"
case "$(cut -d: -f2 <<< "$pub")" in
  r|e|d|i|n) key_fail "the key $fpr is revoked, expired or not valid." ;;
esac
if [[ "$(cut -d: -f12 <<< "$pub")" != *E* ]]; then
  key_fail "the key $fpr has no usable encryption subkey."
fi
echo "    encrypting to $fpr"

# ── 4. Stage. Compress, then encrypt, each item ───────────────────────────────
up="$work/up"
mkdir -p "$up"
sums="$work/SHA256SUMS"
: > "$sums"
# enc <plain file> — add its checksum, and write <name>.zst.gpg to $up.
# SHA256SUMS itself gets no line: a file cannot hold its own checksum.
enc() {
  if [ "$1" != "$sums" ]; then
    (cd "$(dirname "$1")" && sha256sum "$(basename "$1")") >> "$sums"
  fi
  zstd -q -c "$1" \
    | gpg --homedir "$gpg_home" --batch --yes --quiet --trust-model always \
          --compress-algo none --recipient "$fpr" \
          --output "$up/$(basename "$1").zst.gpg" --encrypt
}
# tar_items <out> <dir> <path...>. GNU tar exits 1 when a file changed while
# it read it. That is a live box, and the copy holds the rest.
tar_items() {
  local out="$1" dir="$2" rc=0
  shift 2
  tar -C "$dir" -cf "$out" "$@" || rc=$?
  if [ "$rc" = "1" ]; then
    echo "    note: a file changed while tar read it, for $(basename "$out")."
    rc=0
  fi
  return "$rc"
}

for f in "$dest"/*.dump "$dest/globals.sql" "$dest/MANIFEST.txt"; do
  if [ -f "$f" ]; then
    enc "$f"
  fi
done

# The file data: Tasks and Projects attachments, meeting audio and the agent
# workspaces. Paths are absolute, and the tar keeps them under /.
# 🔴 VALIDATED (BH-6a). Root tars each dir, so the env must not name /etc or
# /root. The two ROOTS are LITERAL, and an entry must sit under one of them
# as written, before any symlink is followed (fix round 1, P2: a symlinked
# root once moved the allow list itself). Then:
#   - the root must not be a symlink, and no dir above it, up to /, may be a
#     symlink that a user other than root owns
#   - no part of the entry BELOW the root may be a symlink
# An entry outside both roots means the default list, with a WARN. An entry
# that fails a symlink rule is skipped, with a WARN. So tar gets a path that
# is exactly where it says, and a link cannot lead it out.
# The agents root follows the layout of <app dir>: /opt/acb/app gives
# /home/acb/.acb/agents, and a test layout <R>/opt/acb/app gives
# <R>/home/acb/.acb/agents.
layout_root="${app_dir%/opt/acb/app}"
if [ "$layout_root/opt/acb/app" = "$app_dir" ]; then
  agents_root="$layout_root/home/acb/.acb/agents"
else
  agents_root=/home/acb/.acb/agents
fi
file_roots=("$app_dir/data" "$agents_root")
default_dirs="$app_dir/data/gtd_attachments $app_dir/data/notes_media $agents_root"
# root_trusted <root> — the root is no symlink, and no dir above it is a
# symlink that a non-root user owns.
root_trusted() {
  local p="$1"
  if [ -L "$p" ]; then return 1; fi
  while [ "$p" != "/" ] && [ -n "$p" ]; do
    p="${p%/*}"
    if [ -z "$p" ]; then p=/; fi
    if [ -L "$p" ] && [ -n "$(find "$p" -maxdepth 0 ! -user 0 -print 2>/dev/null)" ]; then
      return 1
    fi
  done
  return 0
}
# dir_allowed <dir> — print the path to tar, and return 0. Return 1 when the
# dir is outside both roots, and 2 when a symlink rule fails.
dir_allowed() {
  local d="$1" lexical real root
  [ "${d#/}" != "$d" ] || return 1
  lexical="$(realpath -m -s -- "$d")" || return 1
  real="$(realpath -m -- "$d")" || return 1
  for root in "${file_roots[@]}"; do
    case "$lexical/" in
      "$root"/*) ;;
      *) continue ;;
    esac
    root_trusted "$root" || return 2
    [ "$real" = "$(realpath -m -- "$root")${lexical#"$root"}" ] || return 2
    echo "$real"
    return 0
  done
  return 1
}
read -r -a file_dirs <<< "${BACKUP_FILE_DIRS:-$default_dirs}"
for d in "${file_dirs[@]}"; do
  rc=0
  dir_allowed "$d" >/dev/null || rc=$?
  if [ "$rc" = "1" ]; then
    echo "    !! BACKUP_FILE_DIRS names a directory outside ${file_roots[*]}." >&2
    echo "    !! The copy uses the default list instead." >&2
    read -r -a file_dirs <<< "$default_dirs"
    break
  fi
done
present=()
for d in "${file_dirs[@]}"; do
  rc=0
  real="$(dir_allowed "$d")" || rc=$?
  if [ "$rc" = "1" ]; then
    echo "    skip $d (outside ${file_roots[*]})"
  elif [ "$rc" != "0" ]; then
    echo "    !! skip $d: it, or its root, goes through a symlink." >&2
  elif [ -d "$real" ]; then
    present+=("${real#/}")
  else
    echo "    skip $d (no such directory)"
  fi
done
if [ "${#present[@]}" -gt 0 ]; then
  tar_items "$work/files.tar" / "${present[@]}"
  enc "$work/files.tar"
  rm -f "$work/files.tar"
fi

# The meeting bot's volume, read in place through its mount point. The bot
# keeps running. Compose may prefix the name with its project.
# VALIDATED (BH-6a): Docker's own volume-name shape, so a name cannot start a
# docker option. Else the default names.
default_volumes="acb-meeting-bot-data acb_acb-meeting-bot-data"
read -r -a volumes <<< "${BACKUP_MEETING_BOT_VOLUME:-$default_volumes}"
for vol in "${volumes[@]}"; do
  if ! [[ "$vol" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    echo "    !! BACKUP_MEETING_BOT_VOLUME is not a list of volume names. Using the default." >&2
    read -r -a volumes <<< "$default_volumes"
    break
  fi
done
vol_path=""
if command -v docker >/dev/null 2>&1; then
  for vol in "${volumes[@]}"; do
    p="$(docker volume inspect -f '{{.Mountpoint}}' "$vol" 2>/dev/null || true)"
    if [ -n "$p" ] && [ -d "$p" ]; then
      vol_path="$p"
      echo "    meeting-bot volume $vol at $p"
      break
    fi
  done
fi
if [ -n "$vol_path" ]; then
  tar_items "$work/meeting-bot-volume.tar" "$vol_path" .
  enc "$work/meeting-bot-volume.tar"
  rm -f "$work/meeting-bot-volume.tar"
else
  echo "    skip the meeting-bot volume (none of: ${volumes[*]})"
fi

# The checksums go last, as the mark of a complete night.
enc "$sums"

# ── 5. Upload ─────────────────────────────────────────────────────────────────
offbox_rclone_env
night="$offbox_base/$stamp"
n="$(find "$up" -type f | wc -l | tr -d ' ')"
size="$(du -sh "$up" | cut -f1)"
echo "    uploading $n encrypted files ($size) -> $night"
if ! offbox_rclone copy --exclude SHA256SUMS.zst.gpg "$up" "$night" >&2; then
  echo "ERROR: the upload to $night FAILED. The night stays incomplete." >&2
  exit 1
fi
if ! offbox_rclone copyto "$up/SHA256SUMS.zst.gpg" "$night/SHA256SUMS.zst.gpg" >&2; then
  echo "ERROR: the upload of SHA256SUMS.zst.gpg to $night FAILED." >&2
  exit 1
fi
if ! listed="$(offbox_rclone lsf --files-only "$night")"; then
  echo "ERROR: could not list $night after the upload:" >&2
  printf '%s\n' "$listed" | sed 's/^/    /' >&2
  exit 1
fi
for f in "$up"/*; do
  if ! grep -qxF "$(basename "$f")" <<< "$listed"; then
    echo "ERROR: $(basename "$f") is not in $night after the upload." >&2
    exit 1
  fi
done
echo "    off-box copy ok ($night, $n files, $size)"

# ── 6. Retention in the bucket ────────────────────────────────────────────────
# Runs only after a good upload. offbox_plan_prune counts COMPLETE nights only,
# and offbox_delete_night is the one delete, with its own guard.
echo "    off-box retention: keeping the newest $keep complete nights under $offbox_base"
if ! listing="$(offbox_listing)"; then
  echo "ERROR: could not list the nights under $offbox_base." >&2
  exit 1
fi
mapfile -t doomed < <(printf '%s\n' "$listing" | offbox_plan_prune "$keep")
pruned=0
prune_failed=0
for old in "${doomed[@]}"; do
  [ -n "$old" ] || continue
  if offbox_delete_night "$old" >&2; then
    echo "    pruned $old"
    pruned=$((pruned + 1))
  else
    prune_failed=1
  fi
done
total="$(printf '%s\n' "$listing" | offbox_nights_in | grep -c . || true)"
echo "    $((total - pruned)) night(s) in the bucket ($pruned pruned)"
if [ "$prune_failed" != "0" ]; then
  echo "ERROR: could not prune every old night under $offbox_base. Tonight's copy IS up." >&2
  exit 1
fi
