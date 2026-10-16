#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# The nightly export that the staging box rebuilds from (WS-38 T-9,
# project-docs/specs/staging_and_daily_release.md §3, §5.10 and §5.11).
#
# WHERE IT RUNS: on the PRODUCTION box, as root, from the BH-6 root copy
# (/usr/local/lib/acb/staging_export.sh). `backup_db.sh --verify-restore` calls
# it after its deep verify passes, against the SAME throwaway container: the
# nightly backup is already restored there, on a box with no network for that
# container. So the export costs no second restore, and no load on Supabase.
#
#   staging_export.sh <verify-container> <verify-database>
#
# WHAT LEAVES THE BOX: only what infra/staging/minimise.sql keeps. That is the
# organizations named in STAGING_EXPORT_SLUGS, with every credential blanked.
# The full copy dies with the container (parent spec §3.4, clause 1).
#
# OUTPUT: <out-dir>/current (default /var/lib/acb-staging-export, a ROOT-owned
# parent: root never writes inside a folder that acb owns, BH-6),
# group acb, mode 0640:
#   staging.dump     pg_dump -Fc of public, no owner and no ACL
#   roles.txt        role names that the dump's policies may name
#   extensions.txt   name|schema of each extension the dump needs
#   flags.env        production's feature switches (§3.4 flag parity), no secret
#   SHA256SUMS       of the four files above
# The staging box pulls this folder with a key that can read it and nothing
# else (rrsync -ro). A partial export never replaces a good one: the new files
# are written beside it, and one rename of the `current` symlink swaps them in.
#
# OFF BY DEFAULT: with STAGING_EXPORT_SLUGS unset, it exits 0 and does nothing.
# The caller treats a failure as a warning: the backup's own result never
# depends on staging.
#
# Fence: tests/unit/test_staging_export.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ctr="${1:?usage: staging_export.sh <verify-container> <verify-database> [out-dir] [app-dir] [group]}"
db="${2:?usage: staging_export.sh <verify-container> <verify-database> [out-dir] [app-dir] [group]}"
out="${3:-/var/lib/acb-staging-export}"
app_dir="${4:-/opt/acb/app}"
group="${5:-acb}"
slugs="${STAGING_EXPORT_SLUGS:-}"
lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The root copy holds the SQL flat, as staging_minimise.sql (root_lib_files.txt).
# A run from the checkout finds it in infra/.
sql="$lib_dir/staging_minimise.sql"
[ -f "$sql" ] || sql="$lib_dir/../infra/staging/minimise.sql"

say() { printf '    staging export: %s\n' "$*"; }

if [ -z "$slugs" ]; then
  say "STAGING_EXPORT_SLUGS is unset, so nothing is exported"
  exit 0
fi
if ! [[ "$slugs" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]]; then
  echo "staging export: STAGING_EXPORT_SLUGS must be slugs separated by commas" >&2
  exit 2
fi
[ -f "$sql" ] || { echo "staging export: staging_minimise.sql not found beside $0" >&2; exit 2; }

vpsql() { docker exec -i "$ctr" psql -X -q -v ON_ERROR_STOP=1 -U postgres -d "$db" "$@"; }

[ -L "$out" ] && { echo "staging export: $out is a symlink. Refusing." >&2; exit 2; }
install -d -m 0750 -g "$group" "$out"   # root runs this, so root owns it
next="$out/r.$(date -u +%Y%m%dT%H%M%SZ).$$"
rm -rf "$next"
install -d -m 0750 -g "$group" "$next"
trap 'rm -rf "$next" "$out/.current.new"' EXIT

say "reducing the restored copy to: $slugs"
vpsql -v keep_slugs="$slugs" < "$sql" > "$next/minimise.log" 2>&1 || {
  echo "staging export: minimise.sql REFUSED. Its log:" >&2
  tail -20 "$next/minimise.log" >&2
  exit 1
}
grep -E 'kept|deleted' "$next/minimise.log" | sed 's/^ *//; s/^/    staging export: /' || true
rm -f "$next/minimise.log"

docker exec "$ctr" pg_dump -U postgres -d "$db" -Fc --no-owner --no-acl --schema=public \
  > "$next/staging.dump"
vpsql -tA -c "select rolname from pg_roles where rolname !~ '^pg_' and rolname <> 'postgres' order by 1" \
  > "$next/roles.txt" < /dev/null
vpsql -tA -c "select e.extname || '|' || n.nspname from pg_extension e
                join pg_namespace n on n.oid = e.extnamespace
               where e.extname <> 'plpgsql' order by 1" \
  > "$next/extensions.txt" < /dev/null

# ── Production's feature switches, and nothing else ─────────────────────────
# Read as DATA, never sourced: the files belong to acb, and this runs as root.
# A name must look like a switch and never like a secret, and a value must be
# a plain word. Anything else is skipped.
flags_from() {
  local scope="$1" file="$2"
  [ -r "$file" ] || return 0
  grep -E '^(NEXT_PUBLIC_[A-Z0-9_]+|[A-Z0-9_]+_ENABLED|[A-Z0-9_]+_LENS|IDENTITY_CUTOVER|ACB_GRAPH_TENANT_BIND)=' "$file" \
    | grep -vE '^[A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|PASSWD|DSN|URL|PASS)[A-Z0-9_]*=' \
    | grep -E '^[A-Z0-9_]+=[A-Za-z0-9_.:-]{0,64}$' \
    | sed "s/^/$scope:/" || true
}
{
  flags_from root "$app_dir/.env"
  flags_from workbench "$app_dir/workbench/control_plane/.env.local"
} > "$next/flags.env"

(cd "$next" && sha256sum staging.dump roles.txt extensions.txt flags.env > SHA256SUMS)
chgrp "$group" "$next"/* && chmod 0640 "$next"/*

# ── Swap it in with one rename ──────────────────────────────────────────────
# `current` is a symlink to one r.<stamp> folder. `mv -T` of a new link over it
# is a single rename(2), so a pull never meets a missing `current`.
[ -d "$out/current" ] && [ ! -L "$out/current" ] && rm -rf "$out/current"
ln -sfn "$(basename "$next")" "$out/.current.new"
mv -T "$out/.current.new" "$out/current"
trap - EXIT
for old in "$out"/r.*; do
  [ "$old" = "$next" ] || rm -rf "$old"
done
say "wrote $out/current ($(du -h "$out/current/staging.dump" | cut -f1), $(wc -l < "$out/current/flags.env") switch(es))"
