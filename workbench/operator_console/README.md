# Operator Console (CP-8)

The **staff-only, cross-organization** customer-management surface. A **separate
Next.js app** from the customer workbench (D35): it *shares the Customer
Console's tables, never its routes*, and that separation is a deployment
boundary rather than a guard.

**Ships DARK.** Not deployed, no hostname, no Caddy route, no secrets set.
Building it is agent-safe; every go-live step is OWNER-GATE.

## What slice 1 does

- **Customers list** (`/`) — every organization with plan/MRR, seats
  (assigned/purchased), credit balance, lifecycle + subscription status, trial
  expiry. Backed by the Console's `GET /orgs` (§4.1a, added with this app).
- **Customer detail** (`/customers/[slug]`) — the same numbers plus management
  **actions**: activate a subscription (pick a catalog plan, seats, credits,
  bank reference), assign/release seats, add AI credits, suspend/resume.

## How it talks to the Console (BFF)

Every call to the Customer Console goes through a server-side `/api/operator/*`
route (or a server component) that holds `CUSTOMER_CONSOLE_OPERATOR_TOKEN` and
`CUSTOMER_CONSOLE_URL`. **The operator token never reaches the browser** — same
rule as the deployment key. The routes reuse the Console's proven operator API
(`/orgs`, `/billing/summary`, `/billing/catalog`,
`/billing/subscriptions/activate`, `/billing/seats[/release]`, `/credits/grant`,
`/orgs/lifecycle`); this app reimplements none of it.

## Auth

The spec pins **our own Entra staff directory** (D35.3). That staff Entra app is
an **owner dependency not yet set up**, so access is gated behind an **interim
server-side staff secret** (`OPERATOR_CONSOLE_STAFF_SECRET`, marked
`INTERIM — replace with staff Entra, CP-8` in `src/lib/staff.ts`). It fails
closed when unset. A customer's own org-owner is **not** a platform operator —
the gate is platform-staff identity.

## Environment (all OWNER-set at go-live)

| Variable | Purpose |
|---|---|
| `CUSTOMER_CONSOLE_URL` | The Customer Console operator API base URL |
| `CUSTOMER_CONSOLE_OPERATOR_TOKEN` | The staff operator token (server-side only) |
| `OPERATOR_CONSOLE_STAFF_SECRET` | Interim staff gate; replaced by staff Entra |
| `GATEWAY_INTERNAL_URL` | CP-2g purge only: the gateway's loopback base URL (e.g. `http://127.0.0.1:8080`) |
| `GATEWAY_INTERNAL_TOKEN` | CP-2g purge only: the gateway's ordinary machine bearer (clears its app-level gate) |
| `GATEWAY_OPERATOR_TOKEN` | CP-2g purge only: the operator door's own credential (`X-Operator-Token`) |

The three `GATEWAY_*` variables arm exactly one action — destroying an
organization's tenant plane from the DangerPanel. All server-side only
(fenced by `console.test.ts`); unset, the purge fails CLOSED with a 503.

## Verify

```bash
npm install
npm run typecheck        # whole app (needs the Next deps)
npm run typecheck:lib    # pure lib only (typescript + @types/node)
npm run test             # vitest — the token-server-side, staff-gate, format fences
```

## Owner follow-ups (go-live is OWNER-GATE)

1. Stand up the **staff Entra app** and replace the interim secret gate.
2. **Deploy** the app and wire its **hostname** + Caddy route.
3. Set `CUSTOMER_CONSOLE_OPERATOR_TOKEN` + `CUSTOMER_CONSOLE_URL` in its env.

## Deferred to later CP-8 slices

Payment reconciliation queue (NULL-`order_id` + capture-after-terminal
`payment_event` rows), the nightly seat/subscription drift job, invoice
rendering.
