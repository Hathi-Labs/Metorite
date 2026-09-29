#!/usr/bin/env bash
# Every deploy proves that chat saves (WS-27bm S16).
#
# Spec: project-docs/specs/projects_ai_chat.md §21.10.
#
# Run ON THE BOX, as the app user (acb). `.github/workflows/deploy.yml` pipes
# this file to `bash -s` over ssh after the deploy is verified. By hand:
#
#     cd /opt/acb/app && bash deploy/smoke_chat.sh
#
# It does three things:
#
#   1. It mints a SHORT-LIVED Auth.js session for the smoke member (900 s).
#      The secret is the workbench AUTH_SECRET from .env.local.
#   2. It waits for the workbench to answer /api/auth/me as that member in the
#      smoke org. A restart gives a short 502 window (H-60), so it retries.
#   3. It runs scripts/smoke_chat_persist.py, and exits with its code.
#
# Steps 2 and 3 hold a SHARED lock on the deploy lock. `vps_apply.sh` takes
# that same file EXCLUSIVE for the whole apply (`deploy_lock_acquire`), on the
# push path and on the pull path. So no restart can land in the middle of the
# smoke, and a second deploy cannot make a false "chat does not save" red.
#
# 🔴 THE COOKIE NEVER LEAVES THIS PROCESS. It lives in one shell variable. It
# goes to each child through the ENVIRONMENT only. It is never written to a
# file, never put on a command line (`ps` shows every argument to every user),
# and never printed. The same holds for AUTH_SECRET. The fence is
# tests/unit/test_deploy_smoke_wiring.py.
#
# ⚠️ EVERY CHILD GETS `< /dev/null`. This file arrives on stdin over
# `bash -s`, as vps_apply.sh does. A child that reads stdin eats the rest of
# the script, and the smoke then ends early with no error.
#
# No cookie file exists on the box. The operator deleted the old static
# cookie on 2026-09-29. A stored cookie expires, and a deploy check that
# expires on a date is a red run with no fault in it.
#
# Exit codes are the smoke's own:
#   0  all four steps passed
#   1  a step failed. Chat does not save. Do NOT retry, look at the box.
#   2  the environment is wrong: no AUTH_SECRET, no node, no smoke org, or a
#      session that the workbench does not accept.
#   75 BUSY. Another deploy held the lock for SMOKE_LOCK_WAIT seconds, so
#      nothing was checked. That deploy runs its own smoke. Not a red.
#
# ⏱ THE TIME BOUND, with the defaults. deploy.yml sizes its ssh timeout from it.
#   the mint           timeout 30 s, +5 s kill grace                    35 s
#   the lock wait      SMOKE_LOCK_WAIT                                 240 s
#   the identity wait  12 probes x (timeout 20 s + 5 s) + 11 naps x 10 410 s
#   the persist run    SMOKE_RUN_S 240 s + 10 s kill grace             250 s
#   worst case                                                         935 s
#
# Overrides, all optional:
#   APP_DIR              /opt/acb/app
#   SMOKE_BASE_URL       https://app.metorite.com (the AUTH_URL host, and NOT
#                        metorite.com, which is a different site)
#   SMOKE_MEMBER_EMAIL   smoke-chat@smoke.metorite.invalid
#   SMOKE_ORG_SLUG       smoke-chat
#   SMOKE_WAIT_TRIES     12
#   SMOKE_WAIT_NAP       10 (seconds)
#   DEPLOY_LOCK          /opt/acb/acb-deploy.lock (the file vps_apply.sh locks)
#   SMOKE_LOCK_WAIT      240 (seconds)
set -uo pipefail
set +x   # never trace. A trace prints the secret and the cookie.

APP_DIR="${APP_DIR:-/opt/acb/app}"
SMOKE_BASE_URL="${SMOKE_BASE_URL:-https://app.metorite.com}"
SMOKE_MEMBER_EMAIL="${SMOKE_MEMBER_EMAIL:-smoke-chat@smoke.metorite.invalid}"
SMOKE_ORG_SLUG="${SMOKE_ORG_SLUG:-smoke-chat}"
SMOKE_WAIT_TRIES="${SMOKE_WAIT_TRIES:-12}"
SMOKE_WAIT_NAP="${SMOKE_WAIT_NAP:-10}"
# The same default as vps_apply.sh: the lock sits beside the checkout.
DEPLOY_LOCK="${DEPLOY_LOCK:-$(dirname "$APP_DIR")/acb-deploy.lock}"
SMOKE_LOCK_WAIT="${SMOKE_LOCK_WAIT:-240}"
# The org id of record, for the message only. The check is by slug.
SMOKE_ORG_ID="${SMOKE_ORG_ID:-2df62642-751d-4ddd-a079-f643ea544c74}"
# On HTTPS, Auth.js v5 names the cookie with the __Secure- prefix, and the
# cookie name is also the salt of the JWE.
COOKIE_NAME="__Secure-authjs.session-token"
# The session must outlive everything after the mint: the mint 35 s, the
# identity wait 410 s and the persist run 250 s, so 695 s. 900 s is the margin.
MAX_AGE_S=900
# The time bound of the persist run: the sweep (one list and at most five
# deletes) plus the four steps, each call at most 15 s. 11 calls = 165 s.
SMOKE_RUN_S="${SMOKE_RUN_S:-240}"

WB_DIR="$APP_DIR/workbench/control_plane"
WB_ENV="$WB_DIR/.env.local"
PY="$APP_DIR/.venv/bin/python"
SMOKE_PY="$APP_DIR/scripts/smoke_chat_persist.py"

# The commit the box holds. A failure prints it, so an overlap with another
# deploy is easy to see.
served_sha() {
  git -C "$APP_DIR" rev-parse HEAD </dev/null 2>/dev/null || echo unknown
}

env_fail() {
  echo "smoke_chat: $*" >&2
  echo "smoke_chat: the box holds $(served_sha)" >&2
  exit 2
}

cd "$APP_DIR" || env_fail "no app checkout at $APP_DIR"
[ -r "$WB_ENV" ] || env_fail "cannot read $WB_ENV. Run this as the app user (acb)."
[ -x "$PY" ] || env_fail "no python at $PY"
[ -f "$SMOKE_PY" ] || env_fail "no $SMOKE_PY in this checkout"
command -v node >/dev/null 2>&1 || env_fail "node is not on PATH"

# The shared lock, on fd 9, until this script exits. It comes BEFORE the
# mint, so a long lock wait cannot use up the life of the session. `flock` ignores the
# open mode, so a read-only open is enough (vps_pull.sh does the same).
if [ -r "$DEPLOY_LOCK" ] && exec 9<"$DEPLOY_LOCK"; then
  if ! flock -s -w "$SMOKE_LOCK_WAIT" 9 </dev/null; then
    echo "smoke_chat: BUSY. Another deploy held $DEPLOY_LOCK for ${SMOKE_LOCK_WAIT}s, so chat was not checked." >&2
    echo "smoke_chat: the box holds $(served_sha). That deploy runs its own smoke." >&2
    exit 75
  fi
else
  echo "smoke_chat: no deploy lock at $DEPLOY_LOCK. The smoke runs without it." >&2
fi

# Read the secret. A command substitution prints nothing. The last line wins,
# as it does for the dotenv loader, and one pair of quotes is removed.
auth_secret="$(sed -n 's/^AUTH_SECRET=//p' "$WB_ENV" </dev/null | tail -n 1 | tr -d '\r')"
auth_secret="${auth_secret#\"}"; auth_secret="${auth_secret%\"}"
auth_secret="${auth_secret#\'}"; auth_secret="${auth_secret%\'}"
[ -n "$auth_secret" ] || env_fail "AUTH_SECRET is not set in $WB_ENV"

# Mint the session in node. The secret goes in through the environment, and
# the JWE comes out on stdout into a variable. `next-auth/jwt` resolves from
# the workbench's own node_modules, so node runs in that directory.
MINT_JS='
import { encode } from "next-auth/jwt";
const email = process.env.SMOKE_MEMBER_EMAIL;
const jwe = await encode({
  token: { email, name: "Smoke chat", sub: email, provider: "smoke" },
  secret: process.env.AUTH_SECRET,
  salt: process.env.SMOKE_COOKIE_NAME,
  maxAge: Number(process.env.SMOKE_MAX_AGE_S),
});
process.stdout.write(jwe);
'
jwe="$(cd "$WB_DIR" && AUTH_SECRET="$auth_secret" SMOKE_MEMBER_EMAIL="$SMOKE_MEMBER_EMAIL" \
  SMOKE_COOKIE_NAME="$COOKIE_NAME" SMOKE_MAX_AGE_S="$MAX_AGE_S" \
  timeout -k 5 30 node --input-type=module -e "$MINT_JS" </dev/null 2>/dev/null)" || jwe=""
unset auth_secret
[ -n "$jwe" ] || env_fail "node could not mint a session (next-auth/jwt in $WB_DIR?)"
SMOKE_COOKIE="$COOKIE_NAME=$jwe"
unset jwe

# Ask /api/auth/me who the session is. Prints one word:
#   ok          the smoke member in the smoke org
#   nobody      no identity. The workbench or the gateway is not ready yet,
#               or the session is refused.
#   org:<slug>  the smoke member, in another org or in none
#   who:<email> another member
#   down:<code> no answer, or not HTTP 200
PROBE_PY='
import json, os, urllib.request, urllib.error
base = os.environ["SMOKE_BASE_URL"].rstrip("/")
req = urllib.request.Request(base + "/api/auth/me", headers={
    "Cookie": os.environ["SMOKE_COOKIE"], "Accept": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as res:
        me = json.loads(res.read().decode("utf-8") or "null")
except urllib.error.HTTPError as err:
    print(f"down:{err.code}"); raise SystemExit(0)
except Exception:
    print("down:000"); raise SystemExit(0)
me = me if isinstance(me, dict) else {}
who = str(me.get("email") or "").lower()
org = (me.get("organization") or {}).get("slug") if isinstance(me.get("organization"), dict) else None
if not who:
    print("nobody")
elif who != os.environ["SMOKE_MEMBER_EMAIL"].lower():
    print("who:" + who)
elif org != os.environ["SMOKE_ORG_SLUG"]:
    print("org:" + (org or "none"))
else:
    print("ok")
'
export SMOKE_BASE_URL SMOKE_MEMBER_EMAIL SMOKE_ORG_SLUG
state=""
for try in $(seq 1 "$SMOKE_WAIT_TRIES" </dev/null); do
  state="$(SMOKE_COOKIE="$SMOKE_COOKIE" timeout -k 5 20 "$PY" -c "$PROBE_PY" </dev/null 2>/dev/null)" || state="down:000"
  case "$state" in
    ok) break ;;
    org:*|who:*) break ;;
  esac
  if [ "$try" -lt "$SMOKE_WAIT_TRIES" ]; then
    echo "smoke_chat: waiting for $SMOKE_BASE_URL/api/auth/me ($state, try $try/$SMOKE_WAIT_TRIES)" >&2
    sleep "$SMOKE_WAIT_NAP"
  fi
done

case "$state" in
  ok) ;;
  org:*)
    env_fail "the smoke org is missing. $SMOKE_MEMBER_EMAIL signs in, but in org '${state#org:}', not '$SMOKE_ORG_SLUG' ($SMOKE_ORG_ID). Nothing was written." ;;
  who:*)
    env_fail "the session is ${state#who:}, not $SMOKE_MEMBER_EMAIL. Nothing was written." ;;
  nobody)
    env_fail "the workbench gives no identity for $SMOKE_MEMBER_EMAIL after $SMOKE_WAIT_TRIES tries. The smoke member or the smoke org '$SMOKE_ORG_SLUG' is missing, AUTH_SECRET does not match, or the gateway is down. Nothing was written." ;;
  *)
    env_fail "$SMOKE_BASE_URL/api/auth/me did not answer 200 after $SMOKE_WAIT_TRIES tries ($state). Nothing was written." ;;
esac

# The smoke. Its step lines are the only lines on stdout.
rc=0
SMOKE_COOKIE="$SMOKE_COOKIE" timeout -k 10 "$SMOKE_RUN_S" "$PY" "$SMOKE_PY" </dev/null || rc=$?
unset SMOKE_COOKIE
[ "$rc" = 0 ] || echo "smoke_chat: the box holds $(served_sha)" >&2
case "$rc" in
  0|1|2) exit "$rc" ;;
  124|137) echo "smoke_chat: the smoke did not finish in ${SMOKE_RUN_S}s (exit $rc)" >&2; exit 1 ;;
  *) echo "smoke_chat: the smoke exited $rc" >&2; exit 1 ;;
esac
