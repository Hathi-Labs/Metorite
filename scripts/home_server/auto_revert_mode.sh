#!/usr/bin/env bash
# CIV-31d (H1): the ONE reader of the auto-revert mode.
#
# The mode has one source: AUTO_REVERT_MODE in the Environment= of the user
# unit metorite-auto-revert.service. `systemctl --user show` merges the unit
# file and each drop-in into one value. This script prints that value in lower
# case. It prints an empty line when systemctl fails or the unit sets no mode.
#
# Three callers read the mode here, and nothing reads it another way:
#   - scripts/home_server/auto_revert.py (the pass). It does not read its env.
#   - scripts/home_server/merge_bot.sh (revert_tested). It holds an
#     auto-revert PR unless this script prints "on".
#   - scripts/home_server/lanes/lane-merge.md step g.
# The caller decides what a value means: only "on" is on, and only on, dry
# and off are modes. Anything else is off.
#
# How a person sets the mode: docs/home_box.md section 3.8.
# Fence: tests/unit/test_auto_revert.py and tests/unit/test_merge_bot.py.
set -uo pipefail
systemctl --user show -p Environment --value metorite-auto-revert.service 2>/dev/null \
  | tr ' ' '\n' | sed -n 's/^AUTO_REVERT_MODE=//p' | tail -n 1 | tr -d '\r' | tr '[:upper:]' '[:lower:]'
exit 0
