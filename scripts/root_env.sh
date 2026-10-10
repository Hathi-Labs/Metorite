#!/usr/bin/env bash
# WS-49 BH-6 — write /etc/acb/root.env, the one env file of the root units.
#
# acb-backup.service loads it, and acb.service and every compose call of the
# deploy read it with --env-file. It holds ONLY the names of
# root_env_names.txt. Each line comes byte for byte from the acb-writable app
# .env, then from the Console .env, so systemd and compose parse the same
# bytes as before. For a name in both files the Console wins, and for a name
# with two lines the last line wins. A missing Console file is not an error.
# An `export NAME=` line counts, as compose reads it, and root.env holds it
# as `NAME=`, so systemd and compose read the same line.
#
# BH-6 filters NAMES, not values. A value still comes from a file that acb
# can write. The backup validates each value it takes, and compose puts each
# value under `environment:` of a container only (box_hardening.md §5 BH-6).
#
# It runs as root, from the root copy only: /usr/local/lib/acb/root_env.sh.
# Its paths come from where it is, and a run from any other place is refused.
# scripts/vps_apply.sh runs it with sudo, and the `rootenv` verb of
# scripts/secrets.sh runs it on the box. It prints names and counts, never a
# value.
# Fence: tests/unit/test_root_units_root_owned.py (BH-F4).
set -euo pipefail
PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
export PATH
unset CDPATH
umask 077
export LC_ALL=C

case "${BASH_SOURCE[0]}" in
  */*) here="${BASH_SOURCE[0]%/*}" ;;
  *) here=. ;;
esac
here="$(cd "$here" && pwd)"
layout_root="${here%/usr/local/lib/acb}"
if [ "$layout_root/usr/local/lib/acb" != "$here" ]; then
  echo "ERROR: root_env.sh runs from /usr/local/lib/acb only, the root copy." >&2
  exit 2
fi
names_file="$here/root_env_names.txt"
out_dir="$layout_root/etc/acb"
out="$out_dir/root.env"
app_env="$layout_root/opt/acb/app/.env"
console_env="$layout_root/opt/acb/app/apps/services/customer_console/.env"

# The names, in list order. A line that is not a plain name stops the run.
if [ -L "$names_file" ] || [ ! -f "$names_file" ]; then
  echo "ERROR: $names_file is missing or is not a plain file." >&2
  exit 2
fi
declare -a names=()
declare -A wanted=() line=()
while IFS= read -r n || [ -n "$n" ]; do
  n="${n%$'\r'}"
  case "$n" in ''|'#'*) continue ;; esac
  if ! [[ "$n" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
    echo "ERROR: $names_file holds a line that is not a plain NAME." >&2
    exit 2
  fi
  if [ -z "${wanted[$n]+x}" ]; then
    wanted[$n]=1
    names+=("$n")
  fi
done < "$names_file"

# read_source FILE — keep the last line of each wanted name. A symlink or a
# file that is not regular is refused, because root reads it.
read_source() {
  local f="$1" l k
  if [ -L "$f" ] || [ ! -f "$f" ]; then
    echo "ERROR: $f is a symlink or not a regular file. root.env is NOT written." >&2
    exit 1
  fi
  while IFS= read -r l || [ -n "$l" ]; do
    if [[ "$l" =~ ^export[[:space:]]+(.*)$ ]]; then l="${BASH_REMATCH[1]}"; fi
    k="${l%%=*}"
    [ "$k" != "$l" ] || continue
    [[ "$k" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    if [ -n "${wanted[$k]+x}" ]; then line[$k]="$l"; fi
  done < "$f"
}

read_source "$app_env"
if [ -e "$console_env" ] || [ -L "$console_env" ]; then
  read_source "$console_env"
fi

if [ ! -d "$out_dir" ]; then
  mkdir -m 0700 "$out_dir"
fi
tmp="$(mktemp "$out_dir/.root.env.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
{
  echo "# Written by /usr/local/lib/acb/root_env.sh (WS-49 BH-6). Do not edit."
  echo "# The next deploy writes it again from the app .env and the Console .env."
  for n in "${names[@]}"; do
    if [ -n "${line[$n]+x}" ]; then printf '%s\n' "${line[$n]}"; fi
  done
} > "$tmp"

held=0
missing=""
for n in "${names[@]}"; do
  if [ -n "${line[$n]+x}" ]; then held=$((held + 1)); else missing="$missing $n"; fi
done

state="unchanged"
if [ -L "$out" ] || [ ! -f "$out" ] || ! cmp -s "$tmp" "$out" \
   || [ "$(stat -c '%u:%g %a' "$out" 2>/dev/null || true)" != "0:0 600" ]; then
  if [ -L "$out" ]; then rm -f "$out"; fi
  install -m 0600 -o root -g root "$tmp" "$out"
  state="written"
fi
echo "    root.env: $out holds $held of ${#names[@]} names ($state)"
if [ -n "$missing" ]; then
  echo "    root.env: not set in either source:$missing"
fi
