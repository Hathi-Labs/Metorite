#!/usr/bin/env bash
# H-123 — prove the off-box round trip with real tools, on any machine.
#
# The unit tests in tests/unit/test_backup_deploy_wiring.py run backup_db.sh
# with STUB rclone and gpg. Stubs agree with whatever they are handed, so this
# is the R8 half: a real Postgres, real pg_dump, real gpg, real zstd, real
# rclone, and a real S3 server (MinIO stands in for Supabase Storage).
#
# What it proves
# --------------
#   1. backup_db.sh uploads one night, and every object is an OpenPGP message
#      encrypted to the test PUBLIC key. No object is plaintext.
#   2. restore_offbox.sh lists the night, downloads it, decrypts it with the
#      PRIVATE key and a passphrase, and both checksum lists verify.
#   3. The restored dump gives back the SAME rows (an md5 over them), and the
#      file-data tar gives back the same file.
#   4. With BACKUP_S3_KEEP=2, three complete nights become two. An old
#      incomplete night goes, and the newest incomplete night stays. A folder
#      under the prefix that is not a night, and another prefix, stay.
#   5. Without --offbox nothing goes up. A key file in mode 0644 is refused.
#      With the key missing, the run fails. No night reaches the bucket.
#
# It must run as ROOT, because backup_offbox.sh refuses any other user.
#
# Usage:
#   BACKUP_S3_ENDPOINT=http://minio:9000 BACKUP_S3_ACCESS_KEY_ID=... \
#   BACKUP_S3_SECRET_ACCESS_KEY=... PGHOST=... PGPASSWORD=... \
#     bash scripts/rehearse_offbox.sh
#
# Env: PGHOST/PGPORT/PGUSER/PGPASSWORD (libpq), BACKUP_S3_ENDPOINT,
#      BACKUP_S3_ACCESS_KEY_ID, BACKUP_S3_SECRET_ACCESS_KEY, and optionally
#      BACKUP_S3_REGION (default us-east-1) and BACKUP_S3_BUCKET (default
#      offbox-rehearsal, made here). Needs psql, pg_dump, pg_restore, rclone,
#      gpg, zstd, tar and sha256sum on PATH.
# ⚠️ It DELETES every night under <bucket>/nightly. Point it at a scratch
# bucket only, never at the real one.
set -euo pipefail

export PG_MODE=local
export PGUSER="${PGUSER:-postgres}"
export BACKUP_S3_REGION="${BACKUP_S3_REGION:-us-east-1}"
export BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-offbox-rehearsal}"
export BACKUP_S3_PREFIX=nightly
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'gpgconf --homedir "$work/gnupg" --kill all >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

say()  { printf "\n==> %s\n" "$*"; }
pass() { printf "  ok   %s\n" "$*"; }
die()  { printf "  FAIL %s\n" "$*" >&2; exit 1; }

for tool in psql pg_dump pg_restore rclone gpg zstd tar sha256sum; do
  command -v "$tool" >/dev/null || die "$tool is not on PATH"
done
for v in BACKUP_S3_ENDPOINT BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY; do
  [ -n "${!v:-}" ] || die "$v is not set"
done
psql -d postgres -tAc 'select 1' >/dev/null 2>&1 || die "no Postgres answers as '$PGUSER'"

# shellcheck source=scripts/offbox_lib.sh
. "$here/offbox_lib.sh"
offbox_settings || die "the BACKUP_S3_* settings are not valid"

say "Seeding the database 'acb'"
psql -d postgres -qc 'DROP DATABASE IF EXISTS acb' >/dev/null
psql -d postgres -qc 'CREATE DATABASE acb' >/dev/null
psql -d acb -qc "CREATE TABLE offbox_rows AS
  SELECT g AS id, 'offbox-rehearsal-row-' || g AS body FROM generate_series(1, 250) g" >/dev/null
seed_md5="$(psql -d acb -tAc "select md5(string_agg(id || ':' || body, ',' order by id)) from offbox_rows")"
pass "250 rows, md5 $seed_md5"

say "A test key pair (the private half stays in this rehearsal)"
mkdir -m 700 "$work/gnupg"
printf 'rehearsal-passphrase\n' > "$work/pass.txt"
gpg --homedir "$work/gnupg" --batch --quiet --pinentry-mode loopback \
    --passphrase-file "$work/pass.txt" \
    --quick-gen-key "offbox rehearsal <rehearsal@example.invalid>" ed25519 cert 1d
fpr="$(gpg --homedir "$work/gnupg" --batch --with-colons --list-keys | awk -F: '/^fpr:/ {print $10; exit}')"
gpg --homedir "$work/gnupg" --batch --quiet --pinentry-mode loopback \
    --passphrase-file "$work/pass.txt" --quick-add-key "$fpr" cv25519 encr 1d
gpg --homedir "$work/gnupg" --batch --armor --export "$fpr" > "$work/public.asc"
gpg --homedir "$work/gnupg" --batch --armor --pinentry-mode loopback \
    --passphrase-file "$work/pass.txt" --export-secret-keys "$fpr" > "$work/private.asc"
pass "key $fpr"

say "The bucket $BACKUP_S3_BUCKET, and things retention must not touch"
offbox_rclone_env
# The owner makes the real bucket, so the scripts never try (no_check_bucket).
# This scratch bucket is made here, with that option off for one call.
RCLONE_CONFIG_OFFBOX_NO_CHECK_BUCKET=false offbox_rclone mkdir "offbox:$BACKUP_S3_BUCKET" \
  || die "could not make the scratch bucket $BACKUP_S3_BUCKET"
offbox_rclone purge "offbox:$BACKUP_S3_BUCKET/nightly" >/dev/null 2>&1 || true
printf 'keep me\n' > "$work/keep.txt"
offbox_rclone copyto "$work/keep.txt" "offbox:$BACKUP_S3_BUCKET/nightly/not-a-stamp/keep.txt"
offbox_rclone copyto "$work/keep.txt" "offbox:$BACKUP_S3_BUCKET/other/2026-01-01T000000Z/keep.txt"
pass "made"

mkdir -p "$work/app" "$work/backups" "$work/files/attachments"
printf 'POSTGRES_USER=%s\n' "$PGUSER" > "$work/app/.env"
printf 'resume of a candidate, rehearsal\n' > "$work/files/attachments/cv.txt"
# The key file, as on the box: root:root 0600. This runs as root, so the file
# is root's. backup_offbox.sh refuses it with any other owner or mode.
[ "$(id -u)" = "0" ] || die "run this as root: the off-box copy refuses any other user"
env | grep -E '^BACKUP_S3_' > "$work/backup-offbox.env"
chmod 600 "$work/backup-offbox.env"
export BACKUP_OFFBOX_ENV_FILE="$work/backup-offbox.env"
backup() {
  APP_DIR="$work/app" BACKUP_DIR="$work/backups" \
  BACKUP_FILE_DIRS="$work/files/attachments $work/files/absent" \
  BACKUP_MEETING_BOT_VOLUME=offbox-rehearsal-no-such-volume \
    bash "$here/backup_db.sh" --offbox
}

say "Night 1: backup_db.sh with the off-box copy"
BACKUP_GPG_RECIPIENT="$fpr" BACKUP_GPG_PUBLIC_KEY_FILE="$work/public.asc" \
  backup > "$work/night1.log" 2>&1 || { cat "$work/night1.log"; die "backup_db.sh failed"; }
grep -q "off-box copy ok" "$work/night1.log" || { cat "$work/night1.log"; die "no 'off-box copy ok'"; }
grep -E "uploading|off-box copy ok|skip " "$work/night1.log" | sed 's/^/    /'
pass "uploaded"

say "Every object in the bucket is encrypted"
night1="$(offbox_list_nights | tail -1)"
offbox_rclone copy "offbox:$BACKUP_S3_BUCKET/nightly/$night1" "$work/raw" >/dev/null
for f in "$work"/raw/*; do
  case "$f" in *.zst.gpg) ;; *) die "$(basename "$f") is not a .zst.gpg object" ;; esac
  gpg --homedir "$work/gnupg" --batch --list-packets --list-only "$f" 2>/dev/null \
    | grep -q '^:pubkey enc packet:' || die "$(basename "$f") is not encrypted to a public key"
  if zstd -q -d -c "$f" >/dev/null 2>&1; then die "$(basename "$f") is plain zstd"; fi
  printf "    %-36s encrypted\n" "$(basename "$f")"
done
pass "$(find "$work/raw" -type f | wc -l | tr -d ' ') objects, none plaintext"

say "restore_offbox.sh --list"
bash "$here/restore_offbox.sh" --list | sed 's/^/  /'

say "restore_offbox.sh --night latest"
bash "$here/restore_offbox.sh" --night latest --key "$work/private.asc" \
  --passphrase-file "$work/pass.txt" --out "$work/restore" > "$work/restore.log" 2>&1 \
  || { cat "$work/restore.log"; die "restore_offbox.sh failed"; }
grep -E "matches|verified" "$work/restore.log" | sed 's/^/  /'

say "The restored data equals the seed"
psql -d postgres -qc 'DROP DATABASE IF EXISTS acb_offbox_restored' >/dev/null
psql -d postgres -qc 'CREATE DATABASE acb_offbox_restored' >/dev/null
pg_restore -d acb_offbox_restored --no-owner --no-acl "$work/restore/$night1/acb.dump"
got_md5="$(psql -d acb_offbox_restored -tAc "select md5(string_agg(id || ':' || body, ',' order by id)) from offbox_rows")"
psql -d postgres -qc 'DROP DATABASE acb_offbox_restored' >/dev/null
[ "$got_md5" = "$seed_md5" ] || die "restored md5 $got_md5, seeded $seed_md5"
pass "rows: md5 $got_md5"
tar -C "$work/restore" -xf "$work/restore/$night1/files.tar"
cmp -s "$work/files/attachments/cv.txt" "$work/restore${work}/files/attachments/cv.txt" \
  || die "the file in files.tar differs from the source"
pass "files.tar gives back the attachment"

say "Two INCOMPLETE nights from before (no SHA256SUMS.zst.gpg)"
offbox_rclone copyto "$work/keep.txt" "offbox:$BACKUP_S3_BUCKET/nightly/2026-01-04T000000Z/acb.dump.zst.gpg"
offbox_rclone copyto "$work/keep.txt" "offbox:$BACKUP_S3_BUCKET/nightly/2026-01-05T000000Z/acb.dump.zst.gpg"
pass "planted 2026-01-04 and 2026-01-05"

say "Nights 2 and 3 with BACKUP_S3_KEEP=2"
for n in 2 3; do
  sleep 1
  BACKUP_S3_KEEP=2 BACKUP_GPG_RECIPIENT="$fpr" BACKUP_GPG_PUBLIC_KEY_FILE="$work/public.asc" \
    backup > "$work/night$n.log" 2>&1 || { cat "$work/night$n.log"; die "night $n failed"; }
done
grep -E "pruned|night\(s\) in the bucket" "$work/night3.log" | sed 's/^/    /'
mapfile -t left < <(offbox_list_nights)
mapfile -t left_complete < <(offbox_listing | offbox_complete_in)
[ "${#left_complete[@]}" = 2 ] || die "expected 2 complete nights, got: ${left_complete[*]}"
case " ${left[*]} " in *" $night1 "*) die "night 1 survived the retention" ;; esac
case " ${left[*]} " in *" 2026-01-04T000000Z "*) die "an old incomplete night survived" ;; esac
case " ${left[*]} " in *" 2026-01-05T000000Z "*) ;; *) die "the NEWEST incomplete night was deleted" ;; esac
pass "2 complete nights kept, the old incomplete night went, the newest incomplete stayed"
offbox_rclone lsf "offbox:$BACKUP_S3_BUCKET/nightly/not-a-stamp" | grep -qx keep.txt \
  || die "retention deleted a folder that is not a night"
offbox_rclone lsf "offbox:$BACKUP_S3_BUCKET/other/2026-01-01T000000Z" | grep -qx keep.txt \
  || die "retention reached outside the prefix"
pass "2 nights left, and nothing outside them was touched"

say "Without --offbox, nothing is uploaded"
before="$(offbox_list_nights | wc -l)"
sleep 1
BACKUP_GPG_RECIPIENT="$fpr" BACKUP_GPG_PUBLIC_KEY_FILE="$work/public.asc" \
APP_DIR="$work/app" BACKUP_DIR="$work/backups" bash "$here/backup_db.sh" > "$work/noflag.log" 2>&1 \
  || { cat "$work/noflag.log"; die "a run without --offbox failed"; }
grep -q "Only acb-backup.service passes --offbox" "$work/noflag.log" || die "no 'not in this run' line"
[ "$before" = "$(offbox_list_nights | wc -l)" ] || die "a run without --offbox uploaded"
pass "no upload, $before nights before and after"

say "A key file that is not 0600 is refused"
chmod 644 "$work/backup-offbox.env"
sleep 1
if BACKUP_GPG_RECIPIENT="$fpr" BACKUP_GPG_PUBLIC_KEY_FILE="$work/public.asc" \
   backup > "$work/mode.log" 2>&1; then die "a 0644 key file was accepted"; fi
grep -q "It must be '0:0 600'" "$work/mode.log" || { cat "$work/mode.log"; die "no mode refusal"; }
chmod 600 "$work/backup-offbox.env"
[ "$before" = "$(offbox_list_nights | wc -l)" ] || die "a 0644 key file uploaded a night"
pass "refused, and nothing uploaded"

say "With no key, the run fails and uploads nothing"
before="$(offbox_list_nights | wc -l)"
sleep 1
if backup > "$work/nokey.log" 2>&1; then die "a run with no key exited 0"; fi
grep -q "Nothing was uploaded" "$work/nokey.log" || { cat "$work/nokey.log"; die "no refusal line"; }
after="$(offbox_list_nights | wc -l)"
[ "$before" = "$after" ] || die "a night reached the bucket with no key"
pass "refused, $after nights before and after"

say "Off-box rehearsal PASSED"
