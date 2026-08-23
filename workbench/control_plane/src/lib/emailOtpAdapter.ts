/**
 * The Auth.js database adapter for CP-2d's email OTP — **with no database in
 * it** (`customer_console.md` §CP-2d clauses 6 and 10, ruling H-A).
 *
 * ## What is here, and what deliberately is not
 *
 * This file is **wiring only**: it builds the one outbound call out of the ONE
 * gateway seam and hands it to `emailOtpAdapterOver`. Every decision the adapter
 * makes — which methods exist, what each answers, which refusals throw — lives
 * in `lib/emailOtp.ts`, because that module is framework-free and therefore
 * *executable* in this tree's node-env vitest. That split is the repair of
 * review finding P0 (2026-08-23): the method set had been justified in a
 * docstring, the docstring was wrong (`getUserByAccount` is reached on every
 * OAuth callback once an adapter is present), and no source-regex fence could
 * have seen it. Behaviour that decides whether Google sign-in works must be
 * runnable, so it was moved somewhere it can run.
 *
 * ## Why this is a hand-written object and not `@auth/pg-adapter`
 *
 * R5: the control plane has never held a database driver and must not start.
 * One engine, one pool, one tenant-binding seam — all of them in Python
 * (`acb_common.db`) — and a `pg` client here would be a second connection site
 * outside that seam, opened from a tier that has no way to bind a tenant. The
 * off-the-shelf adapters all take a connection string.
 *
 * So the persisting methods are HTTP calls to `gateway/routes/email_otp.py`,
 * which calls `acb_auth.email_otp`, which is where every server-side decision
 * actually lives — canonical form, both rate limits, single use, expiry.
 *
 * ⚠️ **The bearer is NOT minted here.** The call goes through the ONE seam,
 * `lib/gateway.ts::headersActingAs`, exactly as `auth.ts`'s CP-2b resolve hop
 * does — the session-resolving helper beside it would call `auth()` and find
 * nothing, because all of this happens before a session exists, which is exactly
 * why `headersActingAs` (the "already-verified member" seam) is the one used.
 * Inlining the gateway's internal bearer (or an Authorization header built from
 * any secret) here is forbidden and fenced from **both** sides:
 * `gateway.test.ts`'s bearer sweep names this file in
 * `NON_ROUTE_GATEWAY_CALLERS` (it did not until the F1 repair of 2026-08-23 —
 * the claim that it did was wrong, and a fence nobody had checked is how a
 * second bearer would have landed), and `emailOtpAdapter.test.ts` scans this
 * file directly. The env variable is deliberately NOT named in this comment:
 * those fences are plain substring scans over this file, and a fence you have to
 * teach about prose is a fence somebody eventually widens. Same discipline as
 * `auth.ts`.
 *
 * ⚠️ **The identity asserted is the address being verified, and it is
 * pre-verification.** That is the accepted residual owner answer B2 names and
 * `gateway/routes/email_otp.py`'s docstring states in full: a holder of the
 * internal token can drive these routes for any address. It is bounded by
 * single-use tokens, a ten-minute expiry, and a rate limit that has no
 * privileged path past it.
 */
import {
  type OtpGatewayCall,
  emailOtpAdapterOver,
  reserveOtpSendVia,
} from "@/lib/emailOtp";
import { GATEWAY_URL, headersActingAs } from "@/lib/gateway";
import type { Adapter } from "next-auth/adapters";

/**
 * The ONE outbound call, as the address being verified.
 *
 * Path-agnostic on purpose: the three OTP routes are named once, in
 * `emailOtp.ts`, and reached only through the object built below. A second
 * `fetch` to the gateway from this tier — with a header set of its own — is the
 * thing both fences exist to catch.
 */
const call: OtpGatewayCall = (path, identifier, body) =>
  fetch(`${GATEWAY_URL}${path}`, {
    method: "POST",
    headers: headersActingAs(identifier, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify(body),
    cache: "no-store",
  });

/**
 * Ask permission to send a code, and record the send. See
 * `emailOtp.ts::reserveOtpSendVia` for why this is not an adapter method and why
 * a refusal has to throw.
 */
export async function reserveOtpSend(
  identifier: string,
  expires: Date,
): Promise<void> {
  await reserveOtpSendVia(call, identifier, expires);
}

/** The adapter Auth.js is handed. Its behaviour, and its shape, are in `emailOtp.ts`. */
export function emailOtpAdapter(): Adapter {
  return emailOtpAdapterOver(call);
}
