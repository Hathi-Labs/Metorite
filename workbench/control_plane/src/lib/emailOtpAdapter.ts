/**
 * The Auth.js database adapter for CP-2d's email OTP — **with no database in
 * it** (`customer_console.md` §CP-2d clauses 6 and 10, ruling H-A).
 *
 * ## Why this is a hand-written object and not `@auth/pg-adapter`
 *
 * R5: the control plane has never held a database driver and must not start.
 * One engine, one pool, one tenant-binding seam — all of them in Python
 * (`acb_common.db`) — and a `pg` client here would be a second connection site
 * outside that seam, opened from a tier that has no way to bind a tenant. The
 * off-the-shelf adapters all take a connection string.
 *
 * So this is a thin object whose three persisting methods are HTTP calls to
 * `gateway/routes/email_otp.py`, which calls `acb_auth.email_otp`, which is
 * where every decision actually lives — canonical form, both rate limits,
 * single use, expiry. Nothing here decides anything; if it looks like it does,
 * that is a bug.
 *
 * ⚠️ **The bearer is NOT minted here.** The call goes through the ONE seam,
 * `lib/gateway.ts::headersActingAs`, exactly as `auth.ts`'s CP-2b resolve hop
 * does — the session-resolving helper beside it would call `auth()` and find
 * nothing, because all of this happens before a session exists, which is exactly
 * why `headersActingAs` (the "already-verified member" seam) is the one used.
 * Inlining the gateway's internal bearer
 * (or an Authorization header built from any secret) here is forbidden and
 * fenced from both sides — `gateway.test.ts`'s sweep and
 * `emailOtpAdapter.test.ts`. The env variable is deliberately NOT named in this
 * comment: those fences are plain substring scans over this file, and a fence
 * you have to teach about prose is a fence somebody eventually widens. Same
 * discipline as `auth.ts`.
 *
 * ⚠️ **The identity asserted is the address being verified, and it is
 * pre-verification.** That is the accepted residual owner answer B2 names and
 * `gateway/routes/email_otp.py`'s docstring states in full: a holder of the
 * internal token can drive these routes for any address. It is bounded by
 * single-use tokens, a ten-minute expiry, and a rate limit that has no
 * privileged path past it.
 *
 * ## Why the method set is exactly five (ruling H-E)
 *
 * Measured against the pinned `@auth/core@0.41.2` in `node_modules`, not against
 * documentation:
 *
 * - `lib/utils/assert.js:18-22` — for `type: "email"` the REQUIRED set is
 *   `createVerificationToken`, `useVerificationToken`, `getUserByEmail`.
 * - `lib/actions/callback/handle-login.js:29-88` — under the **jwt** strategy
 *   (pinned in `auth.ts`; see there for why that pin is not optional) the email
 *   branch additionally calls `getUser` (only when a session cookie is already
 *   present) and then EITHER `updateUser` (when `getUserByEmail` answered) or
 *   `createUser` (when it did not). `derivedOtpUser` is total, so it always
 *   answers, so `updateUser` is the reachable one.
 * - Everything else — `createUser`, `linkAccount`, `createSession`,
 *   `getSessionAndUser`, `deleteSession`, `getUserByAccount`,
 *   `createAuthenticator` — is reachable ONLY from the `!useJwtSession` branches
 *   or from the webauthn/oauth arms, none of which this configuration can enter.
 *
 * They are omitted rather than stubbed on purpose: a stub is a method a future
 * reader believes is exercised. `emailOtpAdapter.test.ts` pins the set both
 * ways, so a strategy change that makes one of them reachable turns the fence
 * red rather than turning sign-in into a `TypeError`.
 */
import type { Adapter, AdapterUser } from "next-auth/adapters";

import {
  EMAIL_OTP_CONSUME_PATH,
  EMAIL_OTP_SEND_PATH,
  EMAIL_OTP_TOKEN_PATH,
  derivedOtpUser,
} from "@/lib/emailOtp";
import { GATEWAY_URL, headersActingAs } from "@/lib/gateway";

/** What the gateway answers with for a token record. */
interface OtpTokenWire {
  identifier: string;
  token: string | null;
  expires: string | null;
}

/**
 * One call to the OTP routes, as the address being verified.
 *
 * Refusals are NOT swallowed. A 429 (rate limited), a 400 (shape) or a 5xx has
 * to reach `@auth/core` as a throw, because the alternative is an adapter that
 * quietly answers "nothing here" — which reads to a person as "your code is
 * wrong" and to an operator as "OTP is flaky". The 404 that means *this code
 * does not match* is the one non-throwing refusal, and it is handled at its own
 * call site rather than here.
 */
async function call(
  path: string,
  identifier: string,
  body: Record<string, unknown>,
): Promise<Response> {
  return fetch(`${GATEWAY_URL}${path}`, {
    method: "POST",
    headers: headersActingAs(identifier, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify(body),
    cache: "no-store",
  });
}

/** Throw with the gateway's own code, so the reason survives to the log. */
async function refuse(path: string, res: Response): Promise<never> {
  const detail = await res.text().catch(() => "");
  throw new Error(
    `email OTP: ${path} refused (${res.status})${detail ? `: ${detail}` : ""}`,
  );
}

/**
 * Ask permission to send a code, and record the send.
 *
 * ⚠️ Not an adapter method — it is called from the provider's
 * `sendVerificationRequest` in `auth.ts`, **before** the mail is handed to
 * Resend. `@auth/core`'s `lib/actions/signin/send-token.js` starts the send and
 * `createVerificationToken` concurrently and only hands the hash to the second,
 * so a send budget enforced in the adapter alone would refuse the token while
 * the email went out anyway. Both legs name their shared send with the same
 * `expires` instant, which is how the gateway counts one send once.
 */
export async function reserveOtpSend(
  identifier: string,
  expires: Date,
): Promise<void> {
  const res = await call(EMAIL_OTP_SEND_PATH, identifier, {
    expires: expires.toISOString(),
  });
  if (!res.ok) await refuse(EMAIL_OTP_SEND_PATH, res);
}

/**
 * The adapter Auth.js is handed. Five methods, and see the header for why each
 * of the others is absent.
 */
export function emailOtpAdapter(): Adapter {
  return {
    async createVerificationToken(token) {
      const res = await call(EMAIL_OTP_TOKEN_PATH, token.identifier, {
        token: token.token,
        expires: token.expires.toISOString(),
      });
      if (!res.ok) await refuse(EMAIL_OTP_TOKEN_PATH, res);
      const wire = (await res.json()) as OtpTokenWire;
      return {
        identifier: wire.identifier,
        token: wire.token ?? token.token,
        expires: wire.expires ? new Date(wire.expires) : token.expires,
      };
    },

    async useVerificationToken({ identifier, token }) {
      const res = await call(EMAIL_OTP_CONSUME_PATH, identifier, { token });
      // 404 is the ONE refusal that is an answer rather than an error: the code
      // is wrong, already used, or expired. `@auth/core` turns a null into its
      // `Verification` error page, which is the correct outcome for all three —
      // and telling them apart would tell a guesser whether a live code exists.
      if (res.status === 404) return null;
      if (!res.ok) await refuse(EMAIL_OTP_CONSUME_PATH, res);
      const wire = (await res.json()) as OtpTokenWire;
      return {
        identifier: wire.identifier,
        token: wire.token ?? token,
        expires: wire.expires ? new Date(wire.expires) : new Date(0),
      };
    },

    // ── The three user methods, all pure. No row is written anywhere. ────────
    async getUserByEmail(email) {
      return derivedOtpUser(email) as AdapterUser;
    },

    async updateUser(user) {
      // `@auth/core` calls this with `{ id, emailVerified }` only, to stamp the
      // verification. The id IS the address (`derivedOtpUser`), so the answer is
      // reconstructible without a store — which is the whole point.
      return { ...derivedOtpUser(String(user.id)), emailVerified: new Date() };
    },

    async getUser() {
      // Reached only when a session cookie is already present
      // (`handle-login.js:34-42`), and under the jwt strategy its result is
      // discarded immediately: the only use is
      // `user?.id !== userByEmail.id && !useJwtSession`, whose second term is
      // false here. Answering null is therefore honest — there is no user store
      // to look in — rather than a stub pretending to have looked.
      return null;
    },
  };
}
