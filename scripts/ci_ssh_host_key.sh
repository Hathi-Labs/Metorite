# shellcheck shell=bash
# H-200 — every CI ssh to the box checks the box's host key against a pin.
#
# SOURCED, never executed. This file is the ONE place that writes the pinned
# known_hosts file and the ONE place that holds the host-key options. Three
# workflows use it: `deploy.yml` (through scripts/ci_deploy_reach.sh),
# `vps-health.yml` and `vps-forensics.yml`.
#
# Before H-200 every one of them ran ssh with `StrictHostKeyChecking=no` and
# `UserKnownHostsFile=/dev/null`. So ssh accepted ANY host key, and a host that
# answered at the box's address got the deploy session and ran the apply.
#
# The keys are PUBLIC, so they are committed, not kept in a secret: a reviewer
# can read deploy/hostinger/known_hosts and check its fingerprints. The host is
# the secret HOSTINGER_HOST, so ci_pin_host_key adds the host field at run time.
#
# ── The host field, and why it is the exact form ─────────────────────────────
#
# ssh looks up the name the caller typed. For port 22 that is `HOST`. For any
# other port it is `[HOST]:PORT`. (OpenSSH `put_host_port`.) ci_pin_host_key
# writes exactly that form for the port the caller connects to, so the pin
# names ONE host. A `*` pattern also matches both forms, but it trusts these
# keys for every name, and a later caller that reuses the file for another
# host would then fail with an unclear "changed" message. The exact form fails
# with "No ... host key is known for", which names the host it wanted.
#
# CheckHostIP=no: the pin is for the name, and an IP lookup would only add a
# second entry to trust. GlobalKnownHostsFile=/dev/null: the runner's own
# /etc/ssh/ssh_known_hosts must not add a key. UpdateHostKeys=no: the server
# must not rotate keys into the pinned file.

_CI_SSH_HOST_KEY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${CI_PINNED_KEYS:=$_CI_SSH_HOST_KEY_DIR/deploy/hostinger/known_hosts}"
: "${CI_KNOWN_HOSTS:=$HOME/.ssh/metorite_known_hosts}"

# The ONE host-key option set. Every CI ssh to the box uses it.
# shellcheck disable=SC2034  # the sourcing workflow or ci_deploy_reach.sh uses it
CI_SSH_HOST_KEY_OPTS=(
  -o StrictHostKeyChecking=yes
  -o UserKnownHostsFile="$CI_KNOWN_HOSTS"
  -o GlobalKnownHostsFile=/dev/null
  -o CheckHostIP=no
  -o UpdateHostKeys=no
)

# Write $CI_KNOWN_HOSTS for host $1 (default $SSH_HOST) and port $2 (default
# $SSH_PORT, else 22). Returns 1, with a reason, when it cannot. The caller must
# stop then. ssh would fail closed anyway, but with a less clear message.
ci_pin_host_key() {
  local host="${1:-${SSH_HOST:-}}" port="${2:-${SSH_PORT:-22}}" name n
  if [ -z "$host" ]; then
    echo "::error title=No host to pin::ci_pin_host_key needs SSH_HOST (the secret HOSTINGER_HOST)."
    return 1
  fi
  if [ "$port" = 22 ]; then name="$host"; else name="[$host]:$port"; fi
  mkdir -p "$(dirname "$CI_KNOWN_HOSTS")" && chmod 700 "$(dirname "$CI_KNOWN_HOSTS")"
  # Strip CR, comments and blank lines. Each key line is `type base64`.
  tr -d '\r' < "$CI_PINNED_KEYS" 2>/dev/null \
    | awk -v n="$name" '!/^[[:space:]]*(#|$)/ { print n, $1, $2 }' > "$CI_KNOWN_HOSTS"
  n=$(wc -l < "$CI_KNOWN_HOSTS")
  if [ "$n" -lt 1 ]; then
    echo "::error title=No pinned host key::$CI_PINNED_KEYS holds no key. See H-200."
    return 1
  fi
  chmod 600 "$CI_KNOWN_HOSTS"
  echo "  ssh host key pinned: $n key(s) for $name, from ${CI_PINNED_KEYS#"$_CI_SSH_HOST_KEY_DIR"/}"
}

# True when ssh's own words (on stdin) say the HOST KEY did not verify. That
# is never a network blip, and no retry or hand-off may hide it.
ssh_host_key_fault() {
  grep -qiE 'host key verification failed|remote host identification has changed|host key for .* has changed|host key is known for'
}

# The red message for a host-key failure. It names what to do and what never
# to do. $1 is ssh's own output, printed below the message.
host_key_red() {
  echo "::error title=ssh host key did not verify (H-200)::The box's host key changed, or something else answered at this address. Do not deploy to it."
  echo "  ❌ THE BOX'S HOST KEY CHANGED, OR SOMETHING ELSE ANSWERED AT THIS ADDRESS."
  echo "     ssh refused the host key, so nothing was sent to it and nothing ran."
  echo "     Read H-200 in project-docs/HANDOFF.md. Then check the box itself:"
  echo "     was it rebuilt, or did its address or the HOSTINGER_HOST secret change?"
  echo "     Re-pin ONLY by the procedure in deploy_delivery_path.md section 8.5:"
  echo "     read the new keys on the box, compare them with ssh-keyscan, commit."
  echo "     NEVER disable the check. Without it, the deploy session goes to"
  echo "     whatever answers at this address."
  if [ -n "${1:-}" ]; then
    echo "     ssh said:"
    printf '%s\n' "$1" | tail -n 8 | sed 's/^/       /'
  fi
}
