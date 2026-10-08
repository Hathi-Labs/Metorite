#!/usr/bin/env bash
# H-123 — fetch one night of the off-box copy, decrypt it, and verify it.
#
# backup_db.sh uploads each night to Supabase Storage, encrypted to the
# owner's PUBLIC key. This script is the other half. It needs the PRIVATE key,
# so it runs where the owner brings that key: the owner's own machine, or the
# box for the length of one restore. See project-docs/specs/backup_and_restore.md
# §4.2 for the exact steps.
#
# Usage:
#   scripts/restore_offbox.sh --list
#   scripts/restore_offbox.sh --night <stamp|latest> --key <private key file> \
#                             --out <dir> [--passphrase-file <file>]
#   Add --env-file <path> to read the BACKUP_S3_* keys from an env file. It
#   reads ONLY those keys, and a key already in the environment wins.
#
# What it does:
#   1. lists the nights under <bucket>/<prefix>. A night with no
#      SHA256SUMS.zst.gpg is INCOMPLETE, and `latest` skips it.
#   2. downloads one night into <out>/<stamp>.enc
#   3. decrypts each <name>.zst.gpg into <out>/<stamp>/<name>, with a keyring
#      made for this run only, which it deletes at the end
#   4. checks every file against SHA256SUMS, and each dump against the
#      `# sha256` part of MANIFEST.txt. Any mismatch exits 1.
#
# <out>/<stamp>/ then has the layout of /opt/acb/backups/<stamp>/, so
# restore_db.sh reads it as it is:
#   BACKUP_DIR=<out> scripts/restore_db.sh --from <stamp>
#
# Needs: bash, rclone, gpg, zstd, sha256sum.
set -euo pipefail

LIST=0; NIGHT=""; KEY=""; OUT=""; PASSFILE=""; ENVFILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --list)            LIST=1; shift ;;
    --night)           NIGHT="${2:-}"; shift 2 ;;
    --key)             KEY="${2:-}"; shift 2 ;;
    --out)             OUT="${2:-}"; shift 2 ;;
    --passphrase-file) PASSFILE="${2:-}"; shift 2 ;;
    --env-file)        ENVFILE="${2:-}"; shift 2 ;;
    -h|--help)         sed -n '2,32p' "$0"; exit 0 ;;
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

for tool in rclone gpg zstd sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "this needs '$tool' on PATH"
done

# shellcheck source=scripts/offbox_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/offbox_lib.sh"
offbox_settings || exit 1
offbox_rclone_env

# One listing of every object under the prefix: "<stamp>/<name>" per line.
listing="$(offbox_rclone lsf -R --files-only "$offbox_base")" \
  || die "could not list $offbox_base: $listing"
nights="$(printf '%s\n' "$listing" | cut -d/ -f1 | grep -E "$offbox_stamp_re" | sort -u || true)"
complete() { grep -qxF "$1/SHA256SUMS.zst.gpg" <<< "$listing"; }

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

if [ "$NIGHT" = "latest" ]; then
  NIGHT=""
  while IFS= read -r n; do
    if [ -n "$n" ] && complete "$n"; then NIGHT="$n"; fi
  done <<< "$nights"
  [ -n "$NIGHT" ] || die "no complete night under $offbox_base"
fi
[[ "$NIGHT" =~ $offbox_stamp_re ]] || die "'$NIGHT' is not a night stamp"
grep -qxF "$NIGHT" <<< "$nights" || die "no night $NIGHT under $offbox_base"
complete "$NIGHT" || die "night $NIGHT is INCOMPLETE (no SHA256SUMS.zst.gpg). Pick another."

enc_dir="$OUT/$NIGHT.enc"
plain_dir="$OUT/$NIGHT"
[ ! -e "$plain_dir" ] || die "$plain_dir exists already. Move it, or pick another --out."
mkdir -p "$enc_dir" "$plain_dir"
chmod 700 "$OUT" "$enc_dir" "$plain_dir" 2>/dev/null || true

# The keyring of this run holds the PRIVATE key. It is deleted on every exit.
gpg_home="$(mktemp -d)"
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
for f in "$enc_dir"/*.zst.gpg; do
  [ -f "$f" ] || die "the night $NIGHT holds no .zst.gpg file"
  name="$(basename "$f" .zst.gpg)"
  "${gpg_dec[@]}" "$f" | zstd -q -d -c > "$plain_dir/$name" \
    || die "could not decrypt $(basename "$f"). Is --key the private half of the backup key?"
  echo "    $name"
done

say "Verifying"
[ -f "$plain_dir/SHA256SUMS" ] || die "SHA256SUMS is missing from the night"
(cd "$plain_dir" && sha256sum -c --quiet SHA256SUMS) \
  || die "a file does not match SHA256SUMS. Do NOT restore from this night."
echo "    every file matches SHA256SUMS"
if [ -f "$plain_dir/MANIFEST.txt" ]; then
  manifest_sums="$(sed -n '/^# sha256$/,$p' "$plain_dir/MANIFEST.txt" | tail -n +2)"
  [ -n "$manifest_sums" ] || die "MANIFEST.txt has no '# sha256' part"
  (cd "$plain_dir" && sha256sum -c --quiet <<< "$manifest_sums") \
    || die "a dump does not match MANIFEST.txt. Do NOT restore from this night."
  echo "    every dump matches MANIFEST.txt"
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
