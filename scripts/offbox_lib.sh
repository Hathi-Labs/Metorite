# shellcheck shell=bash
# H-123 — the off-box copy in Supabase Storage, through its S3 endpoint.
#
# SOURCED, never run. Two scripts share it, so they cannot disagree about where
# a night lives or how rclone reaches the bucket:
#   scripts/backup_db.sh       uploads one encrypted night, then prunes old ones
#   scripts/restore_offbox.sh  lists the nights, downloads one, decrypts it
#
# ── The layout in the bucket ──────────────────────────────────────────────────
#   <bucket>/<prefix>/<stamp>/<name>.zst.gpg
# <stamp> is backup_db.sh's own STAMP, for example 2026-10-08T023012Z. Every
# object is compressed with zstd and then encrypted with gpg to a PUBLIC key.
# SHA256SUMS.zst.gpg goes up LAST, so a night without it is incomplete.
#
# ── rclone reads its config from the ENVIRONMENT only ────────────────────────
# No config file holds a secret, and no secret is on argv. `offbox_rclone_env`
# maps the BACKUP_S3_* names onto rclone's own names for a remote called
# `offbox`. It first unsets every inherited RCLONE_* name, so a value in the
# env file cannot redirect the copy or change a flag.
#
# ── The one guard on a delete ────────────────────────────────────────────────
# `offbox_delete_night` is the ONLY call that deletes in the bucket. It takes a
# night stamp, checks it, and builds the path itself from the checked bucket
# and prefix. So a delete cannot reach a path outside <bucket>/<prefix>/<stamp>.
# `tests/unit/test_backup_deploy_wiring.py` executes it with hostile input.
#
# ⚠️ Lower-case names on purpose, as in backup_db.sh. The env-hardening test
# reads every UPPER-CASE `$NAME` in backup_db.sh as an env name of the box.

offbox_stamp_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z$'
offbox_bucket_re='^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'
# Each segment starts with a letter or a digit, so `.` and `..` cannot be one.
offbox_prefix_re='^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$'
offbox_remote='offbox'

# offbox_prefix_ok <prefix> — true when the prefix is safe to scope a delete to.
offbox_prefix_ok() {
  local p="${1:-}"
  [ -n "$p" ] || return 1
  [[ "$p" =~ $offbox_prefix_re ]] || return 1
  case "/$p/" in */../*|*/./*|*//*) return 1 ;; esac
  return 0
}

# offbox_settings — read and check BACKUP_S3_*. Sets offbox_bucket,
# offbox_prefix and offbox_base. Prints each problem as an ERROR and returns 1.
offbox_settings() {
  local missing="" v
  for v in BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET \
           BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY; do
    [ -n "${!v:-}" ] || missing="$missing $v"
  done
  if [ -n "$missing" ]; then
    echo "ERROR: the off-box copy is configured in part. Missing:$missing" >&2
    return 1
  fi
  offbox_bucket="$BACKUP_S3_BUCKET"
  offbox_prefix="${BACKUP_S3_PREFIX:-nightly}"
  offbox_prefix="${offbox_prefix#/}"
  offbox_prefix="${offbox_prefix%/}"
  if ! [[ "$offbox_bucket" =~ $offbox_bucket_re ]]; then
    echo "ERROR: BACKUP_S3_BUCKET '$offbox_bucket' is not a valid bucket name." >&2
    return 1
  fi
  if ! offbox_prefix_ok "$offbox_prefix"; then
    echo "ERROR: BACKUP_S3_PREFIX '$offbox_prefix' is not a safe prefix." >&2
    echo "       Use letters, digits, '.', '_' and '-', in segments split by '/'." >&2
    return 1
  fi
  offbox_base="$offbox_remote:$offbox_bucket/$offbox_prefix"
  return 0
}

# offbox_rclone_env — point rclone at the bucket through env names only.
offbox_rclone_env() {
  local v
  while IFS= read -r v; do
    unset "$v"
  done < <(compgen -e | grep '^RCLONE_' || true)
  # /dev/null keeps the config in memory. A config file on the box is ignored.
  export RCLONE_CONFIG=/dev/null
  export RCLONE_CONFIG_OFFBOX_TYPE=s3
  export RCLONE_CONFIG_OFFBOX_PROVIDER=Other
  export RCLONE_CONFIG_OFFBOX_ENDPOINT="$BACKUP_S3_ENDPOINT"
  export RCLONE_CONFIG_OFFBOX_REGION="$BACKUP_S3_REGION"
  export RCLONE_CONFIG_OFFBOX_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
  export RCLONE_CONFIG_OFFBOX_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_OFFBOX_FORCE_PATH_STYLE=true
  # The owner makes the bucket. rclone must not try to create it, because the
  # access key may not hold that right.
  export RCLONE_CONFIG_OFFBOX_NO_CHECK_BUCKET=true
}

# offbox_redact — mask the two key values in text on stdin.
offbox_redact() {
  local text secret
  text="$(cat)"
  for secret in "${BACKUP_S3_SECRET_ACCESS_KEY:-}" "${BACKUP_S3_ACCESS_KEY_ID:-}"; do
    if [ -n "$secret" ]; then
      text="${text//"$secret"/***}"
    fi
  done
  printf '%s\n' "$text"
}

# offbox_rclone <args...> — rclone with the common flags. Its output goes
# through offbox_redact, and its exit code is kept.
offbox_rclone() {
  local out rc=0
  out="$(rclone --retries 3 --low-level-retries 10 --stats 0 --log-level ERROR "$@" 2>&1)" || rc=$?
  if [ -n "$out" ]; then
    printf '%s\n' "$out" | offbox_redact
  fi
  return "$rc"
}

# offbox_list_nights — the night stamps under the prefix, oldest first.
# Anything else under the prefix is not a night, and nothing here touches it.
offbox_list_nights() {
  local listing
  listing="$(offbox_rclone lsf --dirs-only "$offbox_base")" || {
    printf '%s\n' "$listing" | sed 's/^/    /' >&2
    return 1
  }
  printf '%s\n' "$listing" | sed 's#/$##' | grep -E "$offbox_stamp_re" | sort || true
}

# offbox_delete_night <stamp> — delete ONE night. The only delete in the bucket.
offbox_delete_night() {
  local night="${1:-}"
  if ! [[ "${offbox_bucket:-}" =~ $offbox_bucket_re ]] || ! offbox_prefix_ok "${offbox_prefix:-}"; then
    echo "ERROR: refusing to delete in the bucket. The bucket or the prefix is not checked." >&2
    return 1
  fi
  if ! [[ "$night" =~ $offbox_stamp_re ]]; then
    echo "ERROR: refusing to delete '$night'. It is not a night stamp ($offbox_stamp_re)." >&2
    return 1
  fi
  offbox_rclone delete --rmdirs "$offbox_remote:$offbox_bucket/$offbox_prefix/$night"
}
