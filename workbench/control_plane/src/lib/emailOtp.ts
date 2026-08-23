/**
 * Email one-time-code (OTP) — the pure half of CP-2d's passwordless email
 * sign-in (`customer_console.md` §CP-2d).
 *
 * ## Why this module is framework-free
 *
 * It imports no `next-auth` and no `node:*`, for the same measured reason
 * `authPosture.ts` does not: vitest in this tree is node-env, and anything that
 * drags `next-auth` in dies inside its `next/server` dependency before a test
 * can run (`signin.test.ts:38-45`). So the code generator, the message, the
 * Resend transport and the config gate all live here where they can be executed
 * directly; the Auth.js provider WIRING lives in `auth.ts` and is fenced by
 * source-regex.
 *
 * ## What it is, and is not
 *
 * Passwordless by decision **D46.3**: there is **no password system in the
 * tree**, so this verifies **email ownership** (the anti-spam gate) with a
 * numeric code the caller must read out of their own inbox — never a stored
 * secret, never a second identity store. The provider it feeds rides the ONE
 * NextAuth session (`auth.ts`), never a parallel auth path.
 *
 * The transport is **Resend**, injected as a plain function so tests mock the
 * send and never touch the network — with the flag off or the key absent the
 * provider is not registered at all, so nothing here is ever reached in that
 * state.
 */

/** Auth.js's built-in id for the Resend email provider — the id `auth.ts` registers and `signIn(id)` calls. */
export const EMAIL_OTP_PROVIDER_ID = "resend" as const;

/** The label the sign-in surface renders for the email-code option. */
export const EMAIL_OTP_LABEL = "Email me a sign-in code";

/** How long a code is valid, in seconds. Short by design — a code is a step, not a session. */
export const DEFAULT_OTP_MAX_AGE_S = 10 * 60;

/** Digits in the numeric code. */
export const OTP_CODE_LENGTH = 6;

/** The `from` used when the deployment does not set its own verified sender. */
export const EMAIL_OTP_FROM_DEFAULT = "Metorite <no-reply@metorite.com>";

/**
 * Where the sign-in form leaves the address for the code-entry page.
 *
 * Auth.js's verify-request redirect carries `provider` and `type` and **not**
 * the address (`@auth/core/lib/actions/signin/send-token.js` builds it), so
 * without this hand-off `/signin/code` has nothing to prefill and has to ask
 * again. Session-scoped, same-origin, and read defensively — it is a
 * convenience, never an input to any decision.
 */
export const OTP_EMAIL_STORAGE_KEY = "cc-otp-email";

/** Resend's transactional-email endpoint. */
export const RESEND_ENDPOINT = "https://api.resend.com/emails";

/** The subset of the environment this feature reads. Nothing else is touched. */
export interface EmailOtpEnv {
  EMAIL_OTP_ENABLED?: string;
  RESEND_API_KEY?: string;
  EMAIL_OTP_FROM?: string;
}

/**
 * Whether passwordless email OTP is configured on this deployment.
 *
 * Ships **DARK**: the flag is compared to the exact string `"true"` — the
 * `auth.ts:163` idiom — never truthiness, so an operator who writes
 * `EMAIL_OTP_ENABLED=false` while debugging a sign-in outage gets OFF. The
 * Resend key must ALSO be present, or the provider would register with nothing
 * to send through. Anything but both ⇒ the provider is never registered and
 * behaviour is byte-identical to today (no send, no crash).
 *
 * This is the **environment half** of the gate. It is NOT sufficient on its own
 * to register the provider — see {@link isEmailOtpProviderReady}, which ANDs the
 * adapter-ready invariant on top. Both `configuredProviders` (the sign-in
 * surface's seam) and `auth.ts` (the provider registration) read that combined
 * gate, so the button and the provider can never disagree.
 */
export function isEmailOtpConfigured(env: EmailOtpEnv): boolean {
  return env.EMAIL_OTP_ENABLED === "true" && Boolean(env.RESEND_API_KEY);
}

/**
 * Whether the Auth.js **database adapter** an email provider requires is wired
 * into NextAuth. The single source of truth for CP-2d's real safety gate.
 *
 * ✅ **`true` since slice 2 (2026-08-23)** — `auth.ts` passes
 * `emailOtpAdapter()` as NextAuth's `adapter` in the same change that flipped
 * this, which is what the constant is for. Flipping it is therefore what
 * converted `EMAIL_OTP_ENABLED` from an inert documented flag into a real owner
 * switch; the flag and the key are still unset everywhere, so the feature is
 * still dark.
 *
 * ⚠️ **Why the env flag alone is NOT a safe gate**, kept because the constant
 * only makes sense with it. `@auth/core`'s `assertConfig` returns
 * **`MissingAdapter`** for ANY email provider registered without an adapter, and
 * it returns it on **every** `/api/auth/*` request — so an email provider
 * without an adapter **500s ALL sign-in, Google and Microsoft included**, not
 * merely OTP. Gating registration on `EMAIL_OTP_ENABLED` alone would therefore
 * turn one documented owner flag into a site-wide auth outage. The provider (and
 * its button) register only when this is ALSO true, so the two can never
 * disagree — and setting this back to `false` without also removing the
 * `adapter` option would leave a live adapter with no provider, which is
 * harmless, while the reverse is the outage.
 */
export const EMAIL_OTP_ADAPTER_READY = true;

/**
 * The REAL gate the provider and the sign-in button both use: configured in the
 * environment **AND** the adapter is present. `isEmailOtpConfigured` (the env
 * half) stays separate and fully tested; this ANDs the adapter-ready invariant
 * on top, so a half-armed flag (key set, adapter absent) registers nothing —
 * no provider, no button, no `MissingAdapter`, no outage.
 *
 * `adapterReady` is a parameter ONLY so tests can exercise the would-be-lit
 * positive path; the default is the module constant, so every real call site
 * reads the one source of truth.
 */
export function isEmailOtpProviderReady(
  env: EmailOtpEnv,
  adapterReady: boolean = EMAIL_OTP_ADAPTER_READY,
): boolean {
  return isEmailOtpConfigured(env) && adapterReady;
}

/** The verified sender address, deployment-overridable via `EMAIL_OTP_FROM`. */
export function emailOtpFrom(env: EmailOtpEnv): string {
  return env.EMAIL_OTP_FROM || EMAIL_OTP_FROM_DEFAULT;
}

/**
 * A fresh numeric one-time code.
 *
 * Cryptographically strong via the Web Crypto global — isomorphic, so this
 * module needs no `node:crypto` import and stays safe to reach from any tier.
 * Zero-padded to a fixed width so a leading-zero code is still six digits.
 */
export function generateOtp(): string {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  const n = buf[0] % 10 ** OTP_CODE_LENGTH;
  return String(n).padStart(OTP_CODE_LENGTH, "0");
}

/** Subject/text/html for the code email. No colours, so it stays out of the theme conformance sweep. */
export function otpEmail(
  code: string,
  maxAgeS: number = DEFAULT_OTP_MAX_AGE_S,
): { subject: string; text: string; html: string } {
  const minutes = Math.max(1, Math.round(maxAgeS / 60));
  const subject = "Your Metorite sign-in code";
  const text =
    `Your Metorite sign-in code is ${code}.\n\n` +
    `It expires in ${minutes} minutes. If you did not try to sign in, ignore this email.`;
  const html =
    `<div><p>Your Metorite sign-in code is:</p>` +
    `<p style="font-size:28px;font-weight:700;letter-spacing:6px">${code}</p>` +
    `<p>It expires in ${minutes} minutes. ` +
    `If you did not try to sign in, you can ignore this email.</p></div>`;
  return { subject, text, html };
}

/** The wire shape Resend's `POST /emails` expects. */
export interface ResendSendArgs {
  to: string;
  from: string;
  subject: string;
  html: string;
  text: string;
}

/** A transport that delivers one email. Injected so tests never hit the network. */
export type ResendSender = (args: ResendSendArgs) => Promise<void>;

/**
 * The real Resend transport.
 *
 * `fetchImpl` defaults to the platform `fetch` and is a parameter ONLY so tests
 * can pass a fake and assert the request shape without a network call. Fails
 * **CLOSED**: a non-2xx means the code was not delivered, so the sign-in attempt
 * must throw rather than appear to succeed.
 */
export function resendSender(
  apiKey: string,
  fetchImpl: typeof fetch = fetch,
): ResendSender {
  return async (args) => {
    const res = await fetchImpl(RESEND_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(args),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error(`Resend send failed (${res.status}): ${detail}`);
    }
  };
}

/**
 * Build the code email and hand it to the injected transport.
 *
 * `to` is the address Auth.js resolved for the verification request; the CODE is
 * the token the provider generated. Nothing here trusts a request body — the
 * verified identity is Auth.js's, not this call's (R11).
 */
export async function sendOtpEmail(
  send: ResendSender,
  opts: { to: string; from: string; code: string; maxAgeS?: number },
): Promise<void> {
  const { subject, text, html } = otpEmail(
    opts.code,
    opts.maxAgeS ?? DEFAULT_OTP_MAX_AGE_S,
  );
  await send({ to: opts.to, from: opts.from, subject, text, html });
}

// ── Slice 2 · the adapter's pure half ───────────────────────────────────────
//
// The WIRED adapter lives in `emailOtpAdapter.ts`, which imports
// `lib/gateway.ts` and therefore drags `next/server` — unrunnable in this
// tree's node-env vitest, so it is source-fenced. Everything about it that CAN
// be executed lives here instead, where the existing matrix already runs.

/** The route the gateway mounts the OTP adapter's server half on. */
export const EMAIL_OTP_ROUTE_PREFIX = "/signin/otp";

/** Ask permission to send a code (the hourly budget). Called BEFORE the mail. */
export const EMAIL_OTP_SEND_PATH = `${EMAIL_OTP_ROUTE_PREFIX}/send`;

/** Persist the verification-token hash for a permitted send. */
export const EMAIL_OTP_TOKEN_PATH = `${EMAIL_OTP_ROUTE_PREFIX}/token`;

/** Spend one verification attempt and consume the token if it matches. */
export const EMAIL_OTP_CONSUME_PATH = `${EMAIL_OTP_ROUTE_PREFIX}/consume`;

/** The subset of Auth.js's `AdapterUser` this adapter ever produces. */
export interface DerivedOtpUser {
  id: string;
  email: string;
  emailVerified: Date | null;
}

/**
 * The user object the adapter answers with — **derived, never stored**.
 *
 * ⚠️ **This adapter persists no user, account or session rows, and that is the
 * design.** Identity in this product is the tenant plane's (`app_user` /
 * `user_identity`, reached through CP-2b's resolve); an Auth.js identity table
 * set beside it would be the second identity store root `CLAUDE.md` §5 forbids.
 * It is also exactly what the OAuth path already does: with no adapter,
 * `@auth/core`'s `handleLoginOrRegister` returns the profile unpersisted, so the
 * email path is its twin rather than a new kind of thing.
 *
 * The id is the address itself, which makes the JWT's `sub` stable across
 * sign-ins the way an IdP's subject is. Nothing in this product reads `sub` — it
 * reads `session.user.email` — but an id that changed every sign-in would be a
 * lie about what it identifies.
 *
 * ⚠️ It is **total**: it never returns null. That is load-bearing, because it is
 * what makes `@auth/core`'s `createUser` branch unreachable and lets the adapter
 * carry the minimal method set (see `emailOtpAdapter.ts`).
 */
export function derivedOtpUser(identifier: string): DerivedOtpUser {
  const email = (identifier ?? "").trim().toLowerCase();
  return { id: email, email, emailVerified: null };
}
