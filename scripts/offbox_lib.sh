# shellcheck shell=bash
# H-123 — the off-box copy in Supabase Storage, through its S3 endpoint.
#
# SOURCED, never run. Two scripts share it, so they cannot disagree about where
# a night lives or how rclone reaches the bucket:
#   scripts/backup_offbox.sh   uploads one encrypted night, then prunes old ones
#                              (backup_db.sh runs it, only with --offbox)
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
#
# ── BH-6a: the root run takes no setting from the acb-writable env ───────────
# acb-backup.service runs as root and loads /opt/acb/app/.env, which the
# gateway can write. So a value in that env must never choose what root runs,
# reads, deletes or uploads. The helpers here, shared by backup_db.sh and
# backup_offbox.sh:
#   offbox_clean_env_reexec  an ALLOW list. The root run starts again under
#                            `env -i` with only the names it needs
#   offbox_env_only_rclone   the allow list of the rclone child
#   offbox_root_file_ok      the key file is root:root 0600, and not a symlink
#   offbox_load_root_file    reads the off-box names from that file ONLY. It
#                            unsets each one first, so an inherited value is gone
# The fence is tests/unit/test_backup_env_values.py.
# ⚠️ Fix round 1 (2026-10-09) replaced a DENY list here (offbox_scrub_env). A
# deny list misses each name nobody thought of: https_proxy, SSL_CERT_FILE,
# KRB5_TRACE, QUOTING_STYLE. An allow list cannot miss a new name.
# ⚠️ What this cannot reach. bash reads BASH_ENV and SHELLOPTS, and ld.so reads
# LD_PRELOAD, BEFORE the first line of backup_db.sh runs. The UnsetEnvironment=
# block of the unit keeps those from root (and box_hardening.md BH-6 removes
# the .env line).

# The names that come from the root key file and from nowhere else. The
# BACKUP_PG_* and BACKUP_CC_PG_* names pin the server that a root run dumps.
offbox_root_names=(
  BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET
  BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY
  BACKUP_S3_PREFIX BACKUP_S3_KEEP BACKUP_S3_TIMEOUT_SECS
  BACKUP_GPG_RECIPIENT BACKUP_GPG_PUBLIC_KEY_FILE BACKUP_REMOTE
  BACKUP_PG_HOST BACKUP_PG_USER_SUFFIX BACKUP_CC_PG_HOST BACKUP_CC_PG_USER_SUFFIX
)
offbox_root_path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

# offbox_clean_env_reexec <script> <allowed name>... -- <argument>...
# Start <script> again under `env -i`, with ONLY:
#   PATH (pinned), HOME=/root, LANG=C.UTF-8 and LC_ALL=C.UTF-8
#   each <allowed name> that is set, and BACKUP_ENV_GUARD
#   _ACB_CLEAN_ENV=<pid>, the mark of a run that already started again
# Every other name is gone, a name that nobody listed too. `exec` keeps the
# pid, so on the second pass the mark equals $$ and the function checks that
# the env holds no other name, then returns. A mark that the env file sets
# cannot match a pid it does not know, and a dirty env is refused anyway.
# Builtins only before the exec, so no binary that the env picks runs first.
# TEST MODE. A run that is NOT uid 0 (BACKUP_ENV_GUARD=1 in a test) also keeps
# its exported functions and the names that BACKUP_ENV_GUARD_KEEP lists, so a
# hermetic test can stub a tool. A run as uid 0 keeps neither: EUID is a bash
# variable, and no env file can set it.
offbox_clean_env_reexec() {
  local script="$1" n v f body bad="" keep=""
  local -a allowed=() env_args=()
  shift
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    allowed+=("$1")
    shift
  done
  if [ "${1:-}" = "--" ]; then shift; fi
  allowed+=(BACKUP_ENV_GUARD)
  if [ "$EUID" != "0" ]; then
    keep="${BACKUP_ENV_GUARD_KEEP:-}"
    allowed+=(BACKUP_ENV_GUARD_KEEP)
  fi
  if [ "${_ACB_CLEAN_ENV:-}" = "$$" ]; then
    while IFS= read -r v; do
      case " PATH HOME LANG LC_ALL PWD SHLVL _ OLDPWD _ACB_CLEAN_ENV ${allowed[*]} $keep " in
        *" $v "*) ;;
        *) bad="$bad $v" ;;
      esac
    done < <(compgen -e)
    if [ -n "$bad" ]; then
      echo "ERROR: after the clean start, the env still holds:$bad" >&2
      exit 2
    fi
    unset _ACB_CLEAN_ENV
    return 0
  fi
  env_args=("PATH=$offbox_root_path" HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 "_ACB_CLEAN_ENV=$$")
  for n in "${allowed[@]}" $keep; do
    [[ "$n" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [ -n "${!n+x}" ]; then
      env_args+=("$n=${!n}")
    fi
  done
  if [ "$EUID" != "0" ]; then
    while read -r _d _o f; do
      body="$(declare -f "$f")"
      env_args+=("BASH_FUNC_${f}%%=() ${body#*$'\n'}")
    done < <(declare -F -x)
  fi
  exec /usr/bin/env -i "${env_args[@]}" /bin/bash "$script" "$@"
}

# offbox_env_only_rclone — in a SUBSHELL, unset every exported name that the
# rclone child does not need: it keeps PATH, HOME, LANG, LC_ALL, RCLONE_CONFIG
# and the RCLONE_CONFIG_OFFBOX_* names that offbox_rclone_env sets from the
# root file. Test mode (not uid 0) also keeps BACKUP_ENV_GUARD_KEEP's names.
# backup_offbox.sh turns it on. restore_offbox.sh runs on an operator's own
# machine, behind its own proxy maybe, so it does not.
offbox_rclone_allowlist=0
offbox_env_only_rclone() {
  local v keep=""
  if [ "$EUID" != "0" ]; then keep="${BACKUP_ENV_GUARD_KEEP:-}"; fi
  while IFS= read -r v; do
    case "$v" in
      PATH|HOME|LANG|LC_ALL|RCLONE_CONFIG|RCLONE_CONFIG_OFFBOX_*) continue ;;
    esac
    case " $keep " in *" $v "*) continue ;; esac
    unset "$v" 2>/dev/null || true
  done < <(compgen -e)
}

# offbox_drop_root_names — unset every name of offbox_root_names.
offbox_drop_root_names() {
  local name
  for name in "${offbox_root_names[@]}"; do
    unset "$name"
  done
}

# offbox_root_file_ok <file> — true when <file> is a regular file, root:root,
# mode 0600, and readable. Else it prints an ERROR and returns 1.
offbox_root_file_ok() {
  local f="${1:-}" perm
  if [ -L "$f" ]; then
    echo "ERROR: $f is a symlink. The bucket key belongs in a plain file, root:root 0600." >&2
    return 1
  fi
  if [ ! -f "$f" ]; then
    echo "ERROR: $f does not exist. The bucket key belongs there, root:root 0600." >&2
    return 1
  fi
  perm="$(stat -c '%u:%g %a' "$f" 2>/dev/null || true)"
  if [ "$perm" != "0:0 600" ]; then
    echo "ERROR: $f is '$perm' (uid:gid mode). It must be '0:0 600'." >&2
    return 1
  fi
  if [ ! -r "$f" ]; then
    echo "ERROR: $f cannot be read by uid $(id -u). Only root reads the bucket key." >&2
    return 1
  fi
  return 0
}

# offbox_load_root_file <file> — set each name of offbox_root_names from
# <file>, and from nothing else. The format is the systemd one that
# scripts/secrets.sh writes: NAME=value, an optional `export `, and the last
# line of a name wins. A value in matching quotes loses the quotes. A name the
# file does not hold stays UNSET, so the script default applies. Nothing is
# run: the file is read with grep, never sourced.
offbox_load_root_file() {
  local f="${1:-}" name line value
  offbox_drop_root_names
  for name in "${offbox_root_names[@]}"; do
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}=" "$f" | tail -n 1 || true)"
    [ -n "$line" ] || continue
    value="${line#*=}"
    value="${value%$'\r'}"
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    printf -v "$name" '%s' "$value"
  done
}

offbox_stamp_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z$'
offbox_bucket_re='^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'
# Each segment starts with a letter or a digit, so `.` and `..` cannot be one.
offbox_prefix_re='^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$'
offbox_remote='offbox'

# offbox_uint <value> — print a whole number read in BASE 10, or return 1.
# `10#` matters: bash reads "08" and "09" as bad octal, and "010" as 8.
# Empty, signed, decimal or longer than 9 digits is refused.
offbox_uint() {
  local v="${1-}"
  [[ "$v" =~ ^[0-9]{1,9}$ ]] || return 1
  printf '%s\n' "$((10#$v))"
}

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
  out="$(
    if [ "$offbox_rclone_allowlist" = "1" ]; then offbox_env_only_rclone; fi
    rclone --retries 3 --low-level-retries 10 --stats 0 --log-level ERROR "$@" 2>&1
  )" || rc=$?
  if [ -n "$out" ]; then
    printf '%s\n' "$out" | offbox_redact
  fi
  return "$rc"
}

# offbox_listing — every object under the prefix, one "<dir>/<name>" a line.
# One call, so the night list and the "complete" mark come from one view.
offbox_listing() {
  local listing
  listing="$(offbox_rclone lsf -R --files-only "$offbox_base")" || {
    printf '%s\n' "$listing" | sed 's/^/    /' >&2
    return 1
  }
  printf '%s\n' "$listing"
}

# offbox_nights_in — the night stamps in a listing on stdin, oldest first.
# Anything else under the prefix is not a night, and nothing here touches it.
offbox_nights_in() {
  cut -d/ -f1 | grep -E "$offbox_stamp_re" | sort -u || true
}

# offbox_complete_in — the nights in a listing on stdin that hold the
# completion mark, SHA256SUMS.zst.gpg. It goes up last, so only a whole night
# has it.
offbox_complete_in() {
  grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z/SHA256SUMS\.zst\.gpg$' | cut -d/ -f1 | sort -u || true
}

# offbox_list_nights — the night stamps under the prefix, oldest first.
offbox_list_nights() {
  local listing
  listing="$(offbox_listing)" || return 1
  printf '%s\n' "$listing" | offbox_nights_in
}

# offbox_plan_prune <keep> — read a listing on stdin, and print the nights to
# delete, oldest first. Pure: it calls nothing, so a test can feed it anything.
#   1. BY UTC DAY. Group the COMPLETE nights by their day (the first 10
#      characters of the stamp). Keep the newest complete night of each of the
#      newest <keep> days that have one. Every other complete night goes: an
#      older run of a kept day, and every night of an older day. So a day of
#      many runs (each deploy, a hand run) costs ONE slot, and it can never
#      push the earlier days out.
#   2. An incomplete night goes only when it is older than the oldest kept
#      night. With no complete night kept, none goes.
#   3. The newest incomplete night never goes. Its upload may not be done yet.
# So a run of failed or partial nights can never push out the good copies.
offbox_plan_prune() {
  local keep="$1" listing all complete kept="" kept_oldest="" newest_incomplete=""
  local n day last_day="" days=0
  listing="$(cat)"
  all="$(printf '%s\n' "$listing" | offbox_nights_in)"
  complete="$(printf '%s\n' "$listing" | offbox_complete_in)"
  # Newest first: the first night seen of a day is that day's newest.
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    day="${n:0:10}"
    if [ "$day" != "$last_day" ]; then
      last_day="$day"
      if [ "$days" -lt "$keep" ]; then
        kept="$kept$n"$'\n'
        kept_oldest="$n"
        days=$((days + 1))
      fi
    fi
  done < <(printf '%s\n' "$complete" | sort -r)
  while IFS= read -r n; do
    if [ -n "$n" ] && ! grep -qxF "$n" <<< "$complete"; then
      newest_incomplete="$n"
    fi
  done <<< "$all"
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    if grep -qxF "$n" <<< "$complete"; then
      # A complete night that is not its day's kept night goes.
      if ! grep -qxF "$n" <<< "$kept"; then echo "$n"; fi
    else
      if [ "$n" = "$newest_incomplete" ]; then continue; fi
      if [ -n "$kept_oldest" ] && [[ "$n" < "$kept_oldest" ]]; then echo "$n"; fi
    fi
  done <<< "$all"
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
