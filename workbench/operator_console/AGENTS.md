# Operator Console

## Purpose
The staff-only, cross-organization customer-management surface (CP-8, spec
`project-docs/specs/customer_console.md` §4.1a / the CP-8 row; D35). A **separate
Next.js app** from `control_plane` — *shares the Customer Console's tables, never
its routes* (D35.2), enforced by the deployment boundary, not a guard.

## Non-negotiables (do not "fix" these — they are recorded decisions)
- **THEMING-EXEMPT (D35.4).** This app is deliberately NOT on the customer
  design system. Do **not** import `workbench/control_plane`'s DESIGN_SYSTEM,
  `@base-ui/react`, the `Icon`/`Button` primitives, the `--cat-*` ramp or the
  conformance suite. "One product, one look" is for surfaces customers see; a
  plain, clean staff UI is correct here. Plain CSS in `src/app/globals.css`.
- **Staff directory is OURS (D35.3).** Auth pins our own Entra staff directory —
  the inverse of the customer product's multi-directory rule. Never gate on "any
  org owner": a customer's org-owner is not a platform operator.
- **Cross-org reads live ONLY here + on the Console.** No route of the customer
  workbench may reach one. The `GET /orgs` cross-org list is Operator-scheme on
  the Console; this app is its only consumer.

## Structure
- `src/lib/console.ts` — SERVER-ONLY Console client. Holds
  `CUSTOMER_CONSOLE_OPERATOR_TOKEN`; the token goes in the OUTGOING request's
  `Authorization` header and **never** into a browser response. No `use client`
  file may import it (fenced by `console.test.ts`).
- `src/lib/tenantDoor.ts` — SERVER-ONLY gateway operator-door client (CP-2g),
  the ONE sanctioned exception to "Console is the only upstream". It exists
  for exactly one act — destroying an organization's tenant plane after the
  Console holds it at `deleted` — and holds TWO more server credentials
  (`GATEWAY_INTERNAL_TOKEN` + `GATEWAY_OPERATOR_TOKEN`; both fenced by the
  same `console.test.ts` scan). A second gateway call added here is a defect
  until a spec says otherwise.
- `src/lib/staff.ts` — the **INTERIM** staff-secret gate
  (`OPERATOR_CONSOLE_STAFF_SECRET`), marked for replacement by staff Entra.
  Fails closed when unset.
- `src/lib/format.ts` — pure display helpers. MRR/prices are integer PAISE and
  are only formatted, never summed (mirrors the customer client's "names no
  price, sums no basket").
- `src/lib/route.ts` / `src/lib/session.ts` — BFF gate + relay helpers, and the
  page-side staff gate.
- `src/app/api/operator/*` — the BFF routes; each gates on staff and relays the
  Console's status + body verbatim (a refusal stays a refusal).
- `src/app/page.tsx` — customers list; `src/app/customers/[slug]/` — detail +
  `Actions.tsx` (the client action forms); `src/app/login/` — interim sign-in.
- `src/app/login/page.tsx` — **ONE door at a time.** `usesSessions()` picks the
  path, and the gate refuses the interim cookie while `OPERATOR_IDENTITY_ENABLED`
  is on (`operator_identity_and_access.md` §8 done-when 29). So the identity
  path carries a recovery NOTE and never a passphrase form. The note names
  the variable to unset, and it shows only when sign-in is not configured or
  Microsoft refused the caller. A form there would answer 400 on submit, because
  `POST /api/operator/session` wants a Supabase `access_token`. Fence:
  `src/app/login/login.test.ts`, which walks the returned element tree because
  vitest here is node-env and renders nothing.

## Rules
- Every Console call is server-side, through `src/lib/console.ts`. Never fetch
  the Console from a client component, and never expose the operator token.
- Reuse the Console's existing operator routes; reimplement none of them. The
  new backend surfaces this app consumes are `GET /orgs` + `POST /orgs/purge`
  (on the Console) and the gateway's CP-2g operator door (via
  `src/lib/tenantDoor.ts`, its one permitted use).
- Everything server-shaped or rule-shaped is a pure `src/lib/*` module,
  unit-tested; `src/app` is composition.

## Ship dark
Not deployed, no hostname/Caddy route, no secrets set. Deploy, hostname, the
operator-token env and the staff Entra app are all OWNER-GATE.

## Verify
`npm run typecheck` (whole app) · `npm run typecheck:lib` (pure lib only) ·
`npm run test` (vitest fences: token-server-side, staff gate, paise formatting).
