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
 *
 * ⚠️ The `import type` below is erased at compile time (it is the reason this
 * module still loads in a node-env test while `emailOtpAdapter.ts` does not).
 * Turning it into a value import would drag `next-auth` in and silence every
 * executed fence in `emailOtp.test.ts`.
 */
import type { Adapter, AdapterAccount, AdapterUser } from "next-auth/adapters";

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

// ── Slice 2 · the adapter itself, over an injected transport ────────────────
//
// The WIRED adapter is `emailOtpAdapter.ts`, which imports `lib/gateway.ts` and
// therefore drags `next/server` — unrunnable in this tree's node-env vitest. So
// the adapter's BEHAVIOUR lives here, parameterised by the one call it makes,
// and that file is reduced to supplying the call through the `headersActingAs`
// seam.
//
// ⚠️ That split was not cosmetic and it is the repair of review finding P0
// (2026-08-23): the adapter's method set had been argued to be minimal in a
// DOCSTRING, and the docstring was wrong in a way no source-regex fence could
// see. Behaviour that decides whether Google sign-in works has to be executable,
// so it is — `emailOtp.test.ts` replays both `@auth/core` arms against the real
// object. This is the same seam, moved so it can be tested; never a second one.

/** The route the gateway mounts the OTP adapter's server half on. */
export const EMAIL_OTP_ROUTE_PREFIX = "/signin/otp";

/** Ask permission to send a code (the hourly budget). Called BEFORE the mail. */
export const EMAIL_OTP_SEND_PATH = `${EMAIL_OTP_ROUTE_PREFIX}/send`;

/** Persist the verification-token hash for a permitted send. */
export const EMAIL_OTP_TOKEN_PATH = `${EMAIL_OTP_ROUTE_PREFIX}/token`;

/** Spend one verification attempt and consume the token if it matches. */
export const EMAIL_OTP_CONSUME_PATH = `${EMAIL_OTP_ROUTE_PREFIX}/consume`;

/**
 * Canonicalise an address the way `@auth/core` itself does on the send leg.
 *
 * ⚠️ **This exists because the two legs of an OTP sign-in did not agree, and a
 * disagreement here is worse than a refusal — it bricks the code AFTER spending
 * it** (repair of review finding P1a, 2026-08-23). The send leg runs the typed
 * address through `lib/actions/signin/send-token.js`'s `defaultNormalizer`
 * (`:74-98` in the pinned 0.41.2), so the token is minted for
 * `ada@customer.example`. The completion leg is a hand-built `GET` form, and it
 * used to submit whatever was typed — `Ada@Customer.Example`. `callback/
 * index.js:152` then compares `invite.identifier !== paramIdentifier`
 * **verbatim**, AFTER `useVerificationToken` has already consumed the row
 * server-side: the code is spent, the person is told it is wrong, and each retry
 * costs one of their five attempts. So both browser-side write sites — the
 * `sessionStorage` hand-off and the code form's submitted field — pass through
 * here first.
 *
 * It mirrors `defaultNormalizer`'s semantics deliberately: lowercase, trim,
 * exactly one `@`, and the domain truncated at the first comma (that last one is
 * not decoration — the normalizer really does `domain.split(",")[0]`, so
 * `a@b.com,x` is minted as `a@b.com` and a form that submitted the comma form
 * would mismatch exactly like the case form did).
 *
 * ⚠️ Unlike `defaultNormalizer` it **never throws**. The normalizer's refusals
 * (a quote, no `@`, two `@`s) are server-side input validation and are already
 * enforced where they belong; a browser-side mirror that threw would replace a
 * refusal the person can read with a blank page. An address it cannot normalise
 * comes back trimmed and lowercased, which is submitted, misses server-side, and
 * renders the ordinary "that code is not valid" — the correct outcome for an
 * address that could never have been sent a code.
 *
 * Fence: `emailOtp.test.ts` — *"canonicalises the identifier the way
 * @auth/core's send leg does"* (the `Ada@Customer.Example` case by name).
 */
export function canonicalOtpIdentifier(raw: string | null | undefined): string {
  const trimmed = (raw ?? "").toLowerCase().trim();
  if (trimmed.includes('"')) return trimmed;
  const parts = trimmed.split("@");
  if (parts.length !== 2) return trimmed;
  const [local, rest] = parts;
  const domain = rest.split(",")[0];
  if (!local || !domain) return trimmed;
  return `${local}@${domain}`;
}

/** What `/signin/code` renders and what it submits, decided in one pure place. */
export interface CodeEntryState {
  /**
   * Does the page already KNOW the address? Derived from *stash* alone —
   * never from what is being typed.
   */
  known: boolean;
  /** The canonical address the hidden `email` field carries. */
  submitEmail: string;
}

/**
 * Decide the code page's two variables: whether to ask for the address, and
 * which address to submit.
 *
 * ⚠️ **`known` derives from *stash* and from nothing else, and that is the whole
 * point of this function existing** (repair of review finding P1, round 2,
 * 2026-08-23). The page used to compute `known = canonicalOtpIdentifier(email)
 * !== ""` over the LIVE input value, so the no-stashed-address fallback flipped
 * to "known" on the first keystroke — `"a"` canonicalises to `"a"`, which is not
 * the empty string — and the visible field unmounted mid-typing. Nobody could
 * ever finish entering an address on that arm; the person was stuck on a page
 * whose only escape was *Start again*. The decision must therefore be taken ONCE
 * from the mount-time read (`?email=`, else the `sessionStorage` hand-off), and
 * the typed value may only ever influence what is SUBMITTED.
 *
 * *stash* is the address the page was handed: `null`/`""`/whitespace all mean
 * "nothing was handed over". It is canonicalised before the emptiness test so a
 * stash of `"   "` is unknown rather than a known blank.
 *
 * `submitEmail` is canonical on BOTH arms — `@auth/core`'s send leg minted the
 * token against `defaultNormalizer`'s form and `callback/index.js:151-152`
 * compares verbatim AFTER the consume has spent the token (finding P1a), so a
 * raw address submitted from the fallback arm would brick the code exactly as
 * the prefilled arm's used to.
 *
 * Fences: `emailOtp.test.ts` — *"the code page's state is decided once, from the
 * stash"* (the full typing sequence, both stash positions) — and
 * `src/app/signin/code/code.test.ts`, which pins that the component reads
 * `known` out of this helper rather than computing one of its own.
 */
export function codeEntryState(
  stash: string | null | undefined,
  typed: string,
): CodeEntryState {
  const stashed = canonicalOtpIdentifier(stash);
  if (stashed !== "") return { known: true, submitEmail: stashed };
  return { known: false, submitEmail: canonicalOtpIdentifier(typed) };
}

/**
 * The wire hash `@auth/core` stores for a code — recomputed on the SEND leg.
 *
 * ⚠️ **This exists so the send-slot CLAIM can carry the winning code's hash**
 * (repair of review finding P2, round 2, 2026-08-23). `send-token.js:61` hands
 * `createVerificationToken` `await createHash(\`${token}${secret}\`)` and hands
 * `sendVerificationRequest` the RAW code, so before this the two legs of one send
 * knew different halves: the leg that decided whether a mail goes out could not
 * name the hash that mail's code would need. In a same-`expires` burst the
 * *loser's* `createVerificationToken` then landed on the shared
 * `(identifier, expires)` row and overwrote the winner's hash — so the ONE email
 * that went out carried a code that could never verify. Recomputing the hash here
 * lets `reserve_send` write it inside the claim, and the row's hash is the slot
 * winner's under every interleaving.
 *
 * It mirrors `@auth/core/lib/utils/web.js::createHash` exactly: SHA-256 over
 * `` `${token}${secret}` `` through Web Crypto, lower-case hex. Web Crypto rather
 * than `node:crypto` because this module is imported by client components
 * (`CodeForm.tsx`) and a `node:*` import would break the browser bundle — the
 * same isomorphic reason `generateOtp` uses `crypto.getRandomValues`.
 *
 * *secret* is the caller's job to resolve, because `send-token.js:46` resolves it
 * as `provider.secret ?? options.secret` and only `auth.ts` can see both.
 *
 * Fence: `emailOtp.test.ts` — *"the recomputed wire hash EQUALS @auth/core's
 * own"*, which imports `createHash` out of the pinned `node_modules` and compares
 * outputs, plus a source pin that `send-token.js` still hashes that expression.
 */
export async function otpWireHash(
  rawToken: string,
  secret: string,
): Promise<string> {
  const data = new TextEncoder().encode(`${rawToken}${secret}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * The answer shape the adapter reads off one gateway call.
 *
 * Structural rather than the DOM `Response`, so a test can hand the adapter a
 * literal and so this module stays free of any platform global. A real
 * `Response` satisfies it.
 */
export interface OtpGatewayResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
  text(): Promise<string>;
}

/**
 * One call to the OTP routes, as the address being verified.
 *
 * Injected so the adapter's behaviour is testable without `next/server`; the
 * ONE real implementation is `emailOtpAdapter.ts`, which builds it out of
 * `lib/gateway.ts::headersActingAs`. A second implementation that mints its own
 * bearer is forbidden and fenced from both sides (`gateway.test.ts`'s widened
 * sweep and `emailOtpAdapter.test.ts`).
 */
export type OtpGatewayCall = (
  path: string,
  identifier: string,
  body: Record<string, unknown>,
) => Promise<OtpGatewayResponse>;

/** What the gateway answers with for a token record. */
interface OtpTokenWire {
  identifier?: string;
  token?: string | null;
  expires?: string | null;
}

/**
 * Throw with the gateway's own code, so the reason survives to the log.
 *
 * Refusals are NOT swallowed. A 429 (rate limited or a duplicate send), a 400
 * (shape) or a 5xx has to reach `@auth/core` as a throw, because the alternative
 * is an adapter that quietly answers "nothing here" — which reads to a person as
 * "your code is wrong" and to an operator as "OTP is flaky". The 404 that means
 * *this code does not match* is the one non-throwing refusal, and it is handled
 * at its own call site rather than here.
 */
async function refuse(path: string, res: OtpGatewayResponse): Promise<never> {
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
 * the email went out anyway.
 *
 * ⚠️ **A refusal here is what stops a second email, and it now covers the
 * concurrent case** (repair of review finding P1b). Both legs of one send name
 * it by the same `expires` instant, but so do two sends computed in the SAME
 * millisecond — which is why the gateway now *claims* the send slot atomically
 * and answers 429 `SendDuplicate` to every loser. That refusal throws here, and
 * the throw is what keeps the mail unsent.
 *
 * ⚠️ **`tokenHash` is REQUIRED and is what the claim writes** (repair of review
 * finding P2, round 2). Stopping the loser's *mail* was only half of P1b: its
 * `createVerificationToken` still ran — `send-token.js` starts both legs in one
 * `Promise.all` and a rejection in one cancels nothing — and landed on the
 * shared `(identifier, expires)` row, where the old `COALESCE(EXCLUDED.token,
 * …)` overwrote the winner's hash with the loser's. The one delivered email then
 * carried a code that could never verify. The winner's hash therefore travels
 * WITH the claim, and `record_token` may no longer overwrite an existing one.
 * It is {@link otpWireHash} of the raw code — the same value the token leg
 * sends, so nothing new crosses the wire.
 */
export async function reserveOtpSendVia(
  call: OtpGatewayCall,
  identifier: string,
  expires: Date,
  tokenHash: string,
): Promise<void> {
  const res = await call(EMAIL_OTP_SEND_PATH, identifier, {
    expires: expires.toISOString(),
    token: tokenHash,
  });
  if (!res.ok) await refuse(EMAIL_OTP_SEND_PATH, res);
}

/**
 * `getUserByEmail`, as a **named constant-null implementation** rather than an
 * inline method body.
 *
 * ⚠️ **It is exported so its fence can be STRUCTURAL** (repair of review finding
 * P2, round 2, 2026-08-23). `updateUser` below is a deliberate echo, and it is
 * only safe because `handle-login.js:68` reaches it *exclusively* when this
 * lookup answers — `@auth/core` calls it with `{ id, emailVerified }` alone, so
 * an echo from a reachable `updateUser` returns a user with **no address** and
 * the session comes out anonymous. The fence for that coupling used to assert
 * this function returns `null` for three literal addresses, which a
 * store-backed lookup that simply misses on those three would satisfy: the trap
 * would be armed and the fence green. `emailOtp.test.ts` now identity-compares
 * the assembled adapter's method against this constant, so **any** change away
 * from constant-null — a store, a cache, a conditional — reds instead.
 *
 * It MUST answer null on both arms, and a total version was finding P0: on the
 * OAuth arm a truthy answer means "an account already exists with this address",
 * and `handle-login.js:242-250` answers that with `OAuthAccountNotLinked` unless
 * `allowDangerousEmailAccountLinking` is set — which it is not, and must not be.
 * Null puts both arms on the create path, which is where the adapter-less
 * product already is. Reached from `index.js:156` (email arm),
 * `handle-login.js:57` (email arm), `:231-233` (OAuth arm) and
 * `send-token.js:14` (send leg).
 *
 * Giving this a store is therefore never a one-line change: it makes
 * `updateUser` reachable, and the two must be rewritten together.
 */
export const NO_STORED_USER: NonNullable<Adapter["getUserByEmail"]> =
  async () => null;

/**
 * The Auth.js database adapter for CP-2d's email OTP — **stateless, and
 * complete for every arm `@auth/core` can enter under the pinned config**.
 *
 * ## The rule this object obeys
 *
 * With the adapter present, behaviour must be **the same as with no adapter at
 * all**, except that the email arm gains a verification-token store. That is the
 * only shape that is safe, because the adapter is passed to the ONE `NextAuth`
 * config that also serves Google and Microsoft: the moment the owner flips
 * `EMAIL_OTP_ENABLED`, every OAuth sign-in starts running through
 * `lib/actions/callback/index.js`'s `if (adapter)` arms too.
 *
 * ⚠️ **This is the repair of review finding P0 (2026-08-23), and the previous
 * shape was a site-wide outage waiting on a flag flip.** The adapter used to
 * carry five methods, justified by "the oauth arms are unreachable in this
 * configuration". They are not:
 *
 * - `callback/index.js:56-62` destructures and calls `getUserByAccount` for
 *   **every** OAuth callback whenever an adapter is present — a missing method
 *   is a `TypeError`, wrapped as `CallbackRouteError`, on every Google sign-in.
 * - `handle-login.js:175-178` calls it again, and then — this is the subtle half
 *   — `:231-250` calls `getUserByEmail(profile.email)` and throws
 *   **`OAuthAccountNotLinked`** when it answers. The old `getUserByEmail` was
 *   deliberately TOTAL, so it always answered, so Google would have stayed
 *   broken even after `getUserByAccount` was added.
 *
 * So the user-side methods are **stateless and honest**: there is no user store,
 * `getUser*` therefore answer `null`, and `createUser`/`updateUser`/`linkAccount`
 * echo what they were handed and persist nothing. Walked against the pinned
 * `@auth/core@0.41.2` under `session.strategy: "jwt"`, that reproduces the
 * adapter-less path exactly:
 *
 * | arm | adapter-less | with this adapter |
 * |---|---|---|
 * | OAuth | `handleLoginOrRegister` returns `userFromProvider` unpersisted | `getUserByAccount`→null, `getUserByEmail`→null, `createUser` echoes `userFromProvider` |
 * | email | (impossible — `assertConfig` demands an adapter) | `getUserByEmail`→null, so `index.js:156` derives `{id: randomUUID(), email: identifier}` and `createUser` echoes it |
 *
 * The one measurable difference is `isNewUser`, which becomes `true` where the
 * adapter-less path left it `undefined`. Nothing in this product reads it: no
 * `events` are configured, `pages.newUser` is unset, and the `jwt` callback
 * ignores `trigger`. It is named here rather than left for somebody to discover.
 *
 * ⚠️ **No user, account or session row is written anywhere.** Identity in this
 * product is the tenant plane's (`app_user` / `user_identity`, reached through
 * CP-2b's resolve); an Auth.js identity table set beside it would be the second
 * identity store root `CLAUDE.md` §5 forbids by name.
 *
 * ⚠️ **Session methods are absent because they are unreachable under `jwt`, and
 * that pin is load-bearing** — `handle-login.js` reaches `createSession`,
 * `getSessionAndUser` and `deleteSession` only from `!useJwtSession` branches,
 * and `session.js` only under the database strategy. `auth.ts` pins the strategy
 * in the same config that takes this adapter; `signin.test.ts` fences the pin and
 * `emailOtpAdapter.test.ts` derives the reachable set from the pinned source, so
 * a version bump that adds a call site turns RED rather than 500ing sign-in.
 */
export function emailOtpAdapterOver(call: OtpGatewayCall): Adapter {
  return {
    // ── The two persisting methods: HTTP to `gateway/routes/email_otp.py` ────
    async createVerificationToken(token) {
      const res = await call(EMAIL_OTP_TOKEN_PATH, token.identifier, {
        token: token.token,
        expires: token.expires.toISOString(),
      });
      if (!res.ok) await refuse(EMAIL_OTP_TOKEN_PATH, res);
      const wire = (await res.json()) as OtpTokenWire;
      return {
        // The REQUESTED identifier, never the wire's echo — see
        // `useVerificationToken` for why that distinction is load-bearing.
        identifier: token.identifier,
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
      if (!wire.expires) {
        // A 200 with no expiry is our own route being wrong, and the row is
        // already consumed by the time we are here. Say so: the alternative
        // (a zero date) renders as `Verification` — "your code is wrong" for a
        // code that was right, which is the P1a failure mode in a second dress.
        throw new Error(
          `email OTP: ${EMAIL_OTP_CONSUME_PATH} answered 200 without an expiry`,
        );
      }
      return {
        // ⚠️ **The REQUESTED identifier, not the wire's canonical echo**
        // (repair of review finding P1a). `callback/index.js:151-152` compares
        // `invite.identifier !== paramIdentifier` verbatim and throws
        // `Verification` when they differ — AFTER this call has spent the token
        // and charged an attempt. Ownership is not what that comparison is
        // protecting here: the gateway scopes the consume to the caller's
        // `X-User-Email`, which IS this identifier, so a token minted for
        // another address cannot match in the first place. Echoing the request
        // makes the comparison structurally incapable of stranding a person on
        // a code they have already spent.
        identifier,
        token: wire.token ?? token,
        expires: new Date(wire.expires),
      };
    },

    // ── The user side: stateless. No store, so nothing is found and nothing
    //    is written. Each one names the arm that reaches it. ─────────────────
    //
    // ⚠️ Assigned BY REFERENCE, not written inline: `NO_STORED_USER` is exported
    // so `emailOtp.test.ts` can identity-compare it here, which is what makes
    // `updateUser`'s unreachability a structural fact rather than a value
    // coincidence. See that constant's own docstring.
    getUserByEmail: NO_STORED_USER,

    async getUserByAccount() {
      // `index.js:57-58` and `handle-login.js:175-178`, both OAuth-only, both
      // unconditional once an adapter is present. Null yields
      // `userByAccount ?? userFromProvider` — byte-identical to the
      // adapter-less path, where the whole `if (adapter)` block is skipped.
      return null;
    },

    async getUser() {
      // `handle-login.js:40`, and ONLY when a session cookie is already present
      // and decodes. Adapter-less, `user` is null there too, so null is both
      // honest (there is no user store to look in) and behaviour-preserving:
      // a truthy answer would take the OAuth arm's `if (user)` link-and-return
      // branch at `:206`, which returns THIS object as the signed-in user and
      // would silently drop the provider's name and picture from the session.
      return null;
    },

    async createUser(user) {
      // `handle-login.js:76` (email arm) and `:260` (OAuth arm). Derive and
      // echo: whatever `@auth/core` assembled IS the user, exactly as the
      // adapter-less `handleLoginOrRegister` returns `_profile` untouched. On
      // the email arm that is `index.js:156`'s `{ id: randomUUID(), email:
      // identifier }` — so the verified address reaches the jwt callback — and
      // on the OAuth arm it is the provider profile, name and image included.
      return user as AdapterUser;
    },

    async updateUser(user) {
      // `handle-login.js:68`, the email arm's `getUserByEmail` answered branch.
      // Unreachable while `getUserByEmail` above IS `NO_STORED_USER`, and that
      // coupling is fenced STRUCTURALLY — `emailOtp.test.ts` identity-compares
      // the assembled method against the exported constant, so any replacement
      // reds here rather than only when it happens to answer for the addresses
      // a value assertion picked (repair of review finding P2, round 2).
      //
      // ⚠️ If `getUserByEmail` is ever given a store, THIS method must be
      // rewritten in the same change: `@auth/core` calls it with `{ id,
      // emailVerified }` alone, so an echo would return a user with no address
      // and the session would come out anonymous. It is implemented rather than
      // omitted because an absent method that a future arm reaches is a
      // `TypeError` in production — which is exactly what P0 was.
      return user as AdapterUser;
    },

    async linkAccount(account) {
      // `handle-login.js:209` and `:264`, OAuth arm. There is no account store,
      // so this records nothing; the return value is discarded by both call
      // sites (only `events.linkAccount` would see it, and no events are
      // configured). Echoing keeps it a no-op that types.
      return account as AdapterAccount;
    },
  };
}
