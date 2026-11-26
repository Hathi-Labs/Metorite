#!/usr/bin/env bash
# The host firewall rule of sandbox egress (WS-43g1, maf_coding_engine.md §7.3).
#
# An --internal Docker network still gives the host an address on its bridge,
# and the gateway listens on every host address (§4.5). This rule drops every
# packet that comes in from a sandbox egress bridge to an address of this host.
# The broker names each such bridge mtr-eg-<8 hex> (sandbox_broker.py,
# egress_bridge_name), so one interface pattern covers every organization.
#
# The proxy is on the default bridge, not on an mtr-eg- bridge, so it can still
# reach the door upstream. A sandbox reaches nothing but the proxy.
#
# Usage:
#   host_firewall.sh            print the commands, change nothing (the default)
#   host_firewall.sh --print    the same
#   host_firewall.sh --check    exit 0 when the rules are in place and the
#                               Docker daemon is not hit by CVE-2024-29018
#   host_firewall.sh --check-docker-version <v>
#                               exit 0 when daemon version <v> is fixed
#   host_firewall.sh --apply    add the rules once (needs root)
#   host_firewall.sh --remove   remove the rules (needs root)
#
# To apply it on a box is owner gate WS43-G1. Until the rule is on the box,
# sandbox_egress_enabled stays off there. Nothing in the repo runs --apply.
# Fence: tests/unit/test_sandbox_network_grant.py (WS43-F9).
set -euo pipefail

readonly IFACE="mtr-eg-+"
readonly COMMENT="metorite-sandbox-egress"
readonly TOOLS=(iptables ip6tables)

# CVE-2024-29018: before 26.0.0, 25.0.5 and 23.0.11, the embedded DNS of an
# --internal network forwards outside names, so a sandbox can send data out
# by DNS. The 24.x line has no fix.
docker_version_ok() {
  local version="${1%%[-+]*}" major minor patch
  if [[ ! "$version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    return 1
  fi
  major=$((10#${BASH_REMATCH[1]}))
  minor=$((10#${BASH_REMATCH[2]}))
  patch=$((10#${BASH_REMATCH[3]}))
  if (( major >= 26 )); then return 0; fi
  if (( major == 25 )); then (( minor > 0 || patch >= 5 )); return; fi
  if (( major == 23 )); then (( minor > 0 || patch >= 11 )); return; fi
  return 1
}

rule_args() {
  printf '%s\n' INPUT -i "$IFACE" -m comment --comment "$COMMENT" -j DROP
}

print_rules() {
  local tool
  for tool in "${TOOLS[@]}"; do
    echo "$tool -I $(rule_args | tr '\n' ' ' | sed 's/ $//')"
  done
}

has_rule() {
  local tool="$1"
  mapfile -t args < <(rule_args)
  "$tool" -C "${args[@]}" 2>/dev/null
}

need_root() {
  if [[ "$(id -u)" != "0" ]]; then
    echo "host_firewall.sh: --$1 needs root" >&2
    exit 2
  fi
}

mode="${1:---print}"
case "$mode" in
  --print)
    print_rules
    ;;
  --check)
    for tool in "${TOOLS[@]}"; do
      has_rule "$tool" || { echo "missing: $tool rule" >&2; exit 1; }
    done
    daemon="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
    if ! docker_version_ok "$daemon"; then
      echo "docker daemon ${daemon:-unknown} is hit by CVE-2024-29018" >&2
      exit 1
    fi
    echo "ok (docker $daemon)"
    ;;
  --check-docker-version)
    if docker_version_ok "${2:-}"; then echo "ok"; else echo "affected" >&2; exit 1; fi
    ;;
  --apply)
    need_root apply
    for tool in "${TOOLS[@]}"; do
      if ! has_rule "$tool"; then
        mapfile -t args < <(rule_args)
        chain="${args[0]}"
        "$tool" -I "$chain" 1 "${args[@]:1}"
      fi
    done
    echo "applied"
    ;;
  --remove)
    need_root remove
    for tool in "${TOOLS[@]}"; do
      while has_rule "$tool"; do
        mapfile -t args < <(rule_args)
        "$tool" -D "${args[@]}"
      done
    done
    echo "removed"
    ;;
  *)
    echo "usage: host_firewall.sh [--print|--check|--check-docker-version <v>|--apply|--remove]" >&2
    exit 64
    ;;
esac
