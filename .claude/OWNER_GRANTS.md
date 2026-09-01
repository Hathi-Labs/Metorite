# Owner grants (D45)

Grant lines are written by the OWNER, by hand, in their own editor — never by
an agent. `plan-guard.mjs` refuses every agent write to this file, and that
refusal is itself **not grantable**: a grant can only originate here, from a
human. In-chat permission is NOT a grant; this file is.

Format — one line per grant, valid for the stated **local day** only:

    ALLOW YYYY-MM-DD <gate-id> — free-text reason

Gate ids (defined in `.claude/hooks/plan-guard.mjs`):

| id | unlocks |
|---|---|
| `deploy` | deploy scripts, migration runner, SSH to a host |
| `secrets` | reading `.env` files |
| `env-write` | writing `.env` files |
| `deploy-write` | writing under `deploy/` |
| `force-push` | force push |
| `history-rewrite` | filter-branch / hard reset to origin |
| `enforcement-flip` | flag flips registered in work_plan §6 |
| `permission-mode-flip` | AGENT_PERMISSION_MODE |

Example (expires at the end of that local day; stale lines are inert —
delete them when convenient):

    ALLOW 2026-01-01 deploy — example only, this date is long past

`work_plan.md` §6 remains the registry of what is gated and why; **D45**
records this protocol; `plan-guard.test.mjs` is the fence.

ALLOW 2026-08-19 deploy — new VPS bring-up

ALLOW 2026-08-21 deploy — apex landing-page go-live

ALLOW 2026-08-21 deploy-write — customer console deploy wiring

ALLOW 2026-08-21 secrets — read box .env for the tenant Supabase DSN + config

ALLOW 2026-08-21 env-write — write the console + gateway .env for setup

ALLOW 2026-08-21 enforcement-flip — mint+land cc_depl key, arm resolve, flip SELF_SERVE_SIGNUP for customer-#1 go-live

ALLOW 2026-08-22 deploy — merge/deploy PRs, onboard customer #1, verify on box
ALLOW 2026-08-22 deploy-write — deploy the CP-8 operator console unit
ALLOW 2026-08-22 env-write — CP-8 operator-console env on the box
ALLOW 2026-08-22 secrets — copy console operator token to wire the operator console
ALLOW 2026-08-23 deploy — go-live Phase 0 + H3 rehearsal on a prod-dump restore
ALLOW 2026-08-23 secrets — read tenant DATABASE_URL for the migration/rehearsal run
ALLOW 2026-08-23 enforcement-flip
ALLOW 2026-08-23 env-write — go-live Phase 1: repoint DATABASE_URL to acb_app + set IDENTITY_CUTOVER

ALLOW 2026-08-24 deploy — merge + deploy CP-2d email OTP to prod
ALLOW 2026-08-24 secrets — read box env to verify OTP wiring
ALLOW 2026-08-24 env-write — set EMAIL_OTP_ENABLED on the box
ALLOW 2026-08-24 enforcement-flip — arm email OTP sign-in

ALLOW 2026-08-24 deploy — rebuild operator console for H-17 (PR #71 banner + PR #72 button)

ALLOW 2026-08-25 deploy — CP-2h slice 

ALLOW 2026-08-25 deploy — go-live
ALLOW 2026-08-26 deploy-write — pull unit for WS-25
