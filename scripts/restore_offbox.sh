#!/usr/bin/env bash
# H-123 — fetch one night of the off-box copy, decrypt it, and verify it.
#
# backup_offbox.sh uploads each night to Supabase Storage, encrypted to the
# owner's PUBLIC key. This script is the other half. It needs the PRIVATE key,
# so it runs where the owner brings that key: the owner's own machine, or the
# box for the length of one restore. See project-docs/specs/backup_and_restore.md
# §4.2 for the exact steps.
#
# Usage:
#   scripts/restore_offbox.sh --list
#   scripts/restore_offbox.sh --night <stamp|latest> --key <private key file> \
#                             --out <dir> [--passphrase-file <file>]
#   --env-file <path>     read the BACKUP_S3_* keys from an env file, for
#                         example /etc/acb/backup-offbox.env. It reads ONLY
#                         those keys, and a key already in the environment wins.
#   --keyring-parent <d>  where the keyring of this run goes (default /dev/shm,
#                         memory only). On a machine with no /dev/shm, name a
#                         RAM disk. The private key must not reach a disk.
#   --allow-no-manifest   accept a night with no MANIFEST.txt (it is refused
#                         by default, because only the manifest checks a dump
#                         against the moment it was taken)
#
# What it does:
#   1. lists the nights under <bucket>/<prefix>. A night with no
#      SHA256SUMS.zst.gpg is INCOMPLETE, and `latest` skips it.
#   2. checks that <out> has room: 5 times the size of the encrypted night
#   3. downloads one night into <out>/<stamp>.enc
#   4. decrypts each <name>.zst.gpg into <out>/<stamp>/<name>, with a keyring
#      made for this run only, in memory, which it deletes on every exit
#   5. checks every file against SHA256SUMS, refuses a file that SHA256SUMS
#      does not list, and checks each dump against the `# sha256` part of
#      MANIFEST.txt. Any failure exits 1.
#
# ⚠️ Integrity, not authenticity. The nights are encrypted and NOT signed: the
# box holds no signing key, by design. SHA256SUMS proves that a night is whole.
# It cannot prove who wrote it, because anyone with the bucket key and the
# public key can write a whole night. The spec records this trade-off (§4.2).
#
# <out>/<stamp>/ then has the layout of /opt/acb/backups/<stamp>/, so
# restore_db.sh reads it as it is:
#   BACKUP_DIR=<out> scripts/restore_db.sh --from <stamp>
#
# Needs: bash, rclone, gpg, zstd, sha256sum, df.
set -euo pipefail

LIST=0; NIGHT=""; KEY=""; OUT=""; PASSFILE=""; ENVFILE=""
KEYRING_PARENT="/dev/shm"; ALLOW_NO_MANIFEST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --list)              LIST=1; shift ;;
    --night)             NIGHT="${2:-}"; shift 2 ;;
    --key)               KEY="${2:-}"; shift 2 ;;
    --out)               OUT="${2:-}"; shift 2 ;;
    --passphrase-file)   PASSFILE="${2:-}"; shift 2 ;;
    --env-file)          ENVFILE="${2:-}"; shift 2 ;;
    --keyring-parent)    KEYRING_PARENT="${2:-}"; shift 2 ;;
    --allow-no-manifest) ALLOW_NO_MANIFEST=1; shift ;;
    -h|--help)           sed -n '2,45p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

say() { printf "\n==> %s\n" "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# Read only the BACKUP_S3_* keys from an env file. Never source it: an env
# file is not a shell script, and sourcing it would run whatever it holds.
if [ -n "$ENVFILE" ]; then
  [ -r "$ENVFILE" ] || die "cannot read the env file $ENVFILE"
  for k in BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET \
           BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY BACKUP_S3_PREFIX; do
    [ -n "${!k:-}" ] && continue
    v="$(grep -E "^$k=" "$ENVFILE" | tail -1 | cut -d= -f2- || true)"
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    if [ -n "$v" ]; then
      export "$k=$v"
    fi
  done
fi

for tool in rclone gpg zstd sha256sum df; do
  command -v "$tool" >/dev/null 2>&1 || die "this needs '$tool' on PATH"
done

# shellcheck source=scripts/offbox_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/offbox_lib.sh"
offbox_settings || exit 1
offbox_rclone_env

# One listing of every object under the prefix: "<stamp>/<name>" per line.
listing="$(offbox_listing)" || die "could not list $offbox_base"
nights="$(printf '%s\n' "$listing" | offbox_nights_in)"
complete_nights="$(printf '%s\n' "$listing" | offbox_complete_in)"
complete() { grep -qxF "$1" <<< "$complete_nights"; }

if [ "$LIST" = "1" ]; then
  say "Nights in $offbox_base"
  [ -n "$nights" ] || { echo "    (none)"; exit 0; }
  while IFS= read -r n; do
    files="$(grep -c "^$n/" <<< "$listing" || true)"
    if complete "$n"; then
      printf "    %s  %s files\n" "$n" "$files"
    else
      printf "    %s  %s files  INCOMPLETE (no SHA256SUMS.zst.gpg)\n" "$n" "$files"
    fi
  done <<< "$nights"
  exit 0
fi

[ -n "$NIGHT" ] || die "name a night with --night <stamp|latest>, or use --list"
if [ -z "$KEY" ] || [ ! -r "$KEY" ]; then
  die "--key must name a readable private key file"
fi
[ -n "$OUT" ] || die "--out must name a directory"
if [ -n "$PASSFILE" ] && [ ! -r "$PASSFILE" ]; then
  die "cannot read the passphrase file $PASSFILE"
fi
if [ ! -d "$KEYRING_PARENT" ] || [ ! -w "$KEYRING_PARENT" ]; then
  die "the keyring goes in $KEYRING_PARENT (memory only), and it is not a writable
       directory. Name a RAM disk with --keyring-parent. The private key must not
       reach a disk."
fi

if [ "$NIGHT" = "latest" ]; then
  NIGHT="$(printf '%s\n' "$complete_nights" | sed '/^$/d' | tail -1)"
  [ -n "$NIGHT" ] || die "no complete night under $offbox_base"
fi
[[ "$NIGHT" =~ $offbox_stamp_re ]] || die "'$NIGHT' is not a night stamp"
grep -qxF "$NIGHT" <<< "$nights" || die "no night $NIGHT under $offbox_base"
complete "$NIGHT" || die "night $NIGHT is INCOMPLETE (no SHA256SUMS.zst.gpg). Pick another."

enc_dir="$OUT/$NIGHT.enc"
plain_dir="$OUT/$NIGHT"
[ ! -e "$plain_dir" ] || die "$plain_dir exists already. Move it, or pick another --out."
mkdir -p "$OUT"
chmod 700 "$OUT" 2>/dev/null || true

# ── Room on the disk, before one byte is written ─────────────────────────────
# The night is held twice: encrypted, then plain. A dump barely shrinks under
# zstd, and a tar can grow back by more. 5 times the encrypted size is the
# floor. A disk that fills half way through leaves a plain night that fails
# its checks, and a box with a full disk stops serving.
sizes="$(offbox_rclone lsf --files-only --format sp "$offbox_base/$NIGHT")" \
  || die "could not read the sizes of $NIGHT"
enc_bytes="$(printf '%s\n' "$sizes" | awk -F';' '$1 ~ /^[0-9]+$/ {s += $1} END {printf "%d", s}')"
need_kb=$(( (enc_bytes * 5) / 1024 + 1 ))
free_kb="$(df -Pk "$OUT" | awk 'NR == 2 {print $4}')"
[[ "$free_kb" =~ ^[0-9]+$ ]] || die "could not read the free space of $OUT"
if [ "$free_kb" -lt "$need_kb" ]; then
  die "$OUT has ${free_kb} KB free, and this night needs ${need_kb} KB (5 times its
       encrypted size of ${enc_bytes} bytes). Free some space, or pick another --out."
fi
echo "    room: ${free_kb} KB free, ${need_kb} KB needed"
mkdir -p "$enc_dir" "$plain_dir"
chmod 700 "$enc_dir" "$plain_dir" 2>/dev/null || true

# The keyring of this run holds the PRIVATE key. It lives in memory, and it is
# deleted on every exit.
gpg_home="$(mktemp -d -p "$KEYRING_PARENT")"
chmod 700 "$gpg_home"
cleanup() {
  gpgconf --homedir "$gpg_home" --kill all >/dev/null 2>&1 || true
  rm -rf "$gpg_home"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

say "Downloading $offbox_base/$NIGHT"
offbox_rclone copy "$offbox_base/$NIGHT" "$enc_dir" >&2 || die "the download FAILED"

say "Decrypting into $plain_dir"
gpg --homedir "$gpg_home" --batch --quiet --import "$KEY" >/dev/null 2>&1 \
  || die "gpg could not import the key file $KEY"
gpg_dec=(gpg --homedir "$gpg_home" --quiet --decrypt)
if [ -n "$PASSFILE" ]; then
  gpg_dec=(gpg --homedir "$gpg_home" --batch --quiet --pinentry-mode loopback
           --passphrase-file "$PASSFILE" --decrypt)
fi
for f in "$enc_dir"/*; do
  [ -f "$f" ] || die "the night $NIGHT holds no file"
  case "$f" in
    *.zst.gpg) ;;
    *) die "the night holds $(basename "$f"), which is not a .zst.gpg object. Do NOT restore from it." ;;
  esac
  name="$(basename "$f" .zst.gpg)"
  "${gpg_dec[@]}" "$f" | zstd -q -d -c > "$plain_dir/$name" \
    || die "could not decrypt $(basename "$f"). Is --key the private half of the backup key?"
  echo "    $name"
done

say "Verifying"
[ -f "$plain_dir/SHA256SUMS" ] || die "SHA256SUMS is missing from the night"
listed="$(awk '{sub(/^\*/, "", $2); print $2}' "$plain_dir/SHA256SUMS")"
for f in "$plain_dir"/*; do
  name="$(basename "$f")"
  [ "$name" = "SHA256SUMS" ] && continue
  grep -qxF "$name" <<< "$listed" \
    || die "$name is in the night, and SHA256SUMS does not list it. Do NOT restore from this night."
done
(cd "$plain_dir" && sha256sum -c --quiet SHA256SUMS) \
  || die "a file does not match SHA256SUMS. Do NOT restore from this night."
echo "    every file matches SHA256SUMS, and SHA256SUMS lists every file"
if [ -f "$plain_dir/MANIFEST.txt" ]; then
  manifest_sums="$(sed -n '/^# sha256$/,$p' "$plain_dir/MANIFEST.txt" | tail -n +2)"
  [ -n "$manifest_sums" ] || die "MANIFEST.txt has no '# sha256' part"
  (cd "$plain_dir" && sha256sum -c --quiet <<< "$manifest_sums") \
    || die "a dump does not match MANIFEST.txt. Do NOT restore from this night."
  echo "    every dump matches MANIFEST.txt"
elif [ "$ALLOW_NO_MANIFEST" = "1" ]; then
  echo "    !! the night has no MANIFEST.txt. --allow-no-manifest accepts that."
else
  die "the night has no MANIFEST.txt, so no dump can be checked against it.
       Pass --allow-no-manifest to accept the night on SHA256SUMS alone."
fi
rm -rf "$enc_dir"

say "Night $NIGHT verified: $plain_dir"
cat <<EOF
    Next steps, all on a SCRATCH target first:
      database   BACKUP_DIR=$OUT scripts/restore_db.sh --from $NIGHT
      file data  mkdir -p <scratch> && tar -C <scratch> -xf $plain_dir/files.tar
      bot volume mkdir -p <scratch> && tar -C <scratch> -xf $plain_dir/meeting-bot-volume.tar
    The plain files hold customer data. Delete $plain_dir when you are done.
EOF
