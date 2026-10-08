#!/usr/bin/env bash
# H-123 — send ONE night off the box, to Supabase Storage through S3, encrypted.
#
# backup_db.sh runs this ONLY when it was given --offbox, and only
# acb-backup.service passes that flag. backup_db.sh runs it under `timeout`
# (BACKUP_S3_TIMEOUT_SECS), so a hung upload cannot hold the unit past its
# TimeoutStartSec. `timeout` signals the whole process group, rclone included.
#
# Usage: backup_offbox.sh <tonight's backup dir> <stamp> <app dir>
# Env:   BACKUP_S3_*, BACKUP_GPG_*, BACKUP_FILE_DIRS, BACKUP_MEETING_BOT_VOLUME
#        and BACKUP_OFFBOX_ENV_FILE. backup_db.sh's header describes each one.
# Exit:  0 when the night is up AND the bucket retention ran clean, else 1.
#
# 🔴 **The bucket key is for root ONLY.** A Supabase S3 key is PROJECT-WIDE: it
# passes Row Level Security and can read or delete every object in every
# bucket of the project. So it lives in /etc/acb/backup-offbox.env (root:root,
# 0600), and only acb-backup.service loads that file. /opt/acb/app/.env is the
# env file of acb-gateway (User=acb) and of the WhatsApp bridge, and the
# gateway's in-process Copilot CLI inherits that env (H-270). This script
# refuses to run as any user but root, refuses a key file that is not
# root:root 0600, and refuses when /opt/acb/app/.env holds a BACKUP_S3_* or
# BACKUP_GPG_* key.
#
# 🔴 **Encrypted on the box, or not sent at all.** zstd compresses each item,
# and gpg encrypts it to the owner's PUBLIC key. The box never holds the
# private key. The key is checked in full BEFORE anything is staged.
set -euo pipefail

dest="${1:?usage: backup_offbox.sh <backup dir> <stamp> <app dir>}"
stamp="${2:?usage: backup_offbox.sh <backup dir> <stamp> <app dir>}"
app_dir="${3:?usage: backup_offbox.sh <backup dir> <stamp> <app dir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/offbox_lib.sh
. "$here/offbox_lib.sh"

work="$dest/offbox.work"
gpg_home="$work/gnupg"
cleanup() {
  gpgconf --homedir "$gpg_home" --kill all >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
# A signal from `timeout` or systemd becomes an exit, so the trap runs.
trap 'exit 143' TERM INT HUP

fail() {
  echo "ERROR: $*" >&2
  echo "       Nothing was uploaded." >&2
  exit 1
}

# ── 1. The settings ───────────────────────────────────────────────────────────
offbox_settings || fail "the BACKUP_S3_* settings are not valid (see above)."
if ! keep="$(offbox_uint "${BACKUP_S3_KEEP-14}")" || [ "$keep" -lt 1 ]; then
  fail "BACKUP_S3_KEEP '${BACKUP_S3_KEEP-}' must be a whole number of 1 or more."
fi

# ── 2. Only root holds the bucket key ─────────────────────────────────────────
key_env_file="${BACKUP_OFFBOX_ENV_FILE:-/etc/acb/backup-offbox.env}"
uid="$(id -u)"
if [ "$uid" != "0" ]; then
  fail "the off-box copy runs as uid $uid. Only root may hold the bucket key."
fi
if [ ! -f "$key_env_file" ]; then
  fail "$key_env_file does not exist. The bucket key belongs there, root:root 0600."
fi
key_perm="$(stat -c '%u:%g %a' "$key_env_file" 2>/dev/null || true)"
if [ "$key_perm" != "0:0 600" ]; then
  fail "$key_env_file is '$key_perm' (uid:gid mode). It must be '0:0 600'."
fi
if [ -f "$app_dir/.env" ] \
   && grep -qE '^[[:space:]]*(export[[:space:]]+)?BACKUP_(S3|GPG)_' "$app_dir/.env"; then
  fail "$app_dir/.env holds a BACKUP_S3_* or BACKUP_GPG_* key. The gateway and the
       WhatsApp bridge load that file, so the app user could read the bucket
       key. Move every such line to $key_env_file."
fi

for tool in rclone gpg zstd tar sha256sum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    fail "the off-box copy needs '$tool', and it is not on PATH. scripts/vps_apply.sh installs it."
  fi
done

# ── 3. The gpg key, checked in full before any data is touched ────────────────
rm -rf "$work"
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
read -r -a file_dirs <<< "${BACKUP_FILE_DIRS:-$app_dir/data/gtd_attachments $app_dir/data/notes_media /home/acb/.acb/agents}"
present=()
for d in "${file_dirs[@]}"; do
  if [ "${d#/}" = "$d" ]; then
    echo "    skip $d (not an absolute path)"
  elif [ -d "$d" ]; then
    present+=("${d#/}")
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
read -r -a volumes <<< "${BACKUP_MEETING_BOT_VOLUME:-acb-meeting-bot-data acb_acb-meeting-bot-data}"
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
