#!/usr/bin/env bash
# Run a check that is ADVISORY while a feature is in build, and BLOCKING in
# the hardening pass (two-speed CI, owner directive 2026-10-19).
#
#   bash scripts/ci_advisory.sh "<what>" -- <command...>
#
# The command passes: exit 0.
# The command fails, and the PR carries the `harden` label: exit 1.
# The command fails, and there is no such label: a warning, and exit 0.
#
# The label is read LIVE through `gh`, because a rerun replays the event of
# the first run. Outside a PR (no $PR), a failure is advisory.
# Needs GH_TOKEN and PR in the environment, and `pull-requests: read`.
# Fence: tests/unit/test_fence_lists.py.
set -u
what="$1"; shift
[ "${1:-}" = "--" ] && shift

if "$@"; then exit 0; fi

if [ -n "${PR:-}" ] && gh pr view "$PR" --repo "$GITHUB_REPOSITORY" --json labels -q '.labels[].name' 2>/dev/null | grep -qx harden; then
  echo "::error::A ${what} failed, and this PR carries the harden label, so it blocks."
  exit 1
fi
echo "::warning::A ${what} failed. It is advisory while the feature is in build. Fix it in the hardening pass (label the PR harden)."
exit 0
