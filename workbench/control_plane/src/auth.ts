import NextAuth from "next-auth";
import type { Provider } from "next-auth/providers";
import MicrosoftEntraId from "next-auth/providers/microsoft-entra-id";
import Google from "next-auth/providers/google";
import Resend from "next-auth/providers/resend";

import {
  DEFAULT_OTP_MAX_AGE_S,
  type EmailOtpEnv,
  emailOtpFrom,
  generateOtp,
  isEmailOtpProviderReady,
  otpWireHash,
  resendSender,
  sendOtpEmail,
} from "@/lib/emailOtp";
import { emailOtpAdapter, reserveOtpSend } from "@/lib/emailOtpAdapter";

/**
 * Sign-in for Metorite — **a platform, not one company's app** (WS-31 CP-0,
 * decision D33.1).
 *
 * ## What this used to be, and why it had to change
 *
 * This file used to carry one provider and this comment: *"the tenant-level app
 * registration ensures only users in the Fracktal Microsoft 365 directory can
 * sign in — no domain check needed."* That was correct while Metorite was
 * one company's brain and every user was a colleague. It is a **defect** for a
 * SaaS product, for the plainest possible reason: **a paying customer's staff
 * are not in our directory.** As written it could onboard exactly one customer.
 *
 * Two things follow, and both are implemented here:
 *
 * 1. **Identities arrive from directories we do not control.** Entra's issuer
 *    defaults to `organizations` (any work/school tenant, not just ours) and
 *    Google is offered beside it for customers who are not on Microsoft.
 *    ⚠️ The *code* being multi-directory is necessary and not sufficient — the
 *    Entra **app registration must itself be multi-tenant**, which is Azure
 *    configuration and 🔴 OWNER-GATE (`saas_operations_doctrine.md` §8).
 *
 * 2. **Auth fails CLOSED.** It used to fail OPEN: `isAuthEnabled` was false
 *    whenever no client id was set, and everything downstream read that as
 *    "run wide open". A production box that lost its auth env was therefore
 *    fully public *and reported itself healthy*. Now the bypass requires
 *    `NODE_ENV !== "production"` as well, so an unconfigured production
 *    deployment refuses traffic (`proxy.ts`) instead of admitting everyone as
 *    `DEV_IDENTITY` (`lib/gateway.ts`).
 *
 * ## The three exported predicates, which are NOT interchangeable
 *
 * - {@link isAuthConfigured} — a real provider exists. False on a laptop.
 * - {@link isDevBypass}      — no provider AND not production, i.e. the laptop
 *                              case, and the ONLY case that may run open.
 * - {@link isAuthEnabled}    — **auth is enforced.** This is what call sites
 *                              want, and it is deliberately `!isDevBypass`
 *                              rather than `hasProvider`: an unconfigured
 *                              production box must enforce, even though it has
 *                              no provider to enforce *with*. That combination
 *                              is a misconfiguration, and `proxy.ts` answers it
 *                              with a 503 rather than a login loop.
 *
 * Fenced by `src/lib/authPosture.test.ts`.
 */
// The three predicates live in `@/authPosture` — a pure, framework-free module —
// because they decide whether anonymous callers get in and must be testable
// without standing up NextAuth. Re-exported here so every call site keeps
// importing `@/auth`. See that file for why each is shaped as it is.
export { isAuthConfigured, isDevBypass } from "@/authPosture";
import { isAuthConfigured as hasProvider } from "@/authPosture";
// Hop 2 of CP-2b's chain. `headersActingAs` is the EXISTING "already-verified
// member" seam — `gatewayHeaders()` calls `auth()` and would find nothing here,
// because the `signIn` callback fires BEFORE a session exists.
//
// ⚠️ This closes an import cycle (`lib/gateway.ts` already imports `@/auth`),
// and the cycle is taken deliberately: ESM hoists both module records and
// neither module uses the other's binding at TOP LEVEL — gateway.ts's `auth()`
// calls are inside `currentIdentity()`, and the call below is inside the
// callback — so whichever the bundler evaluates first finds a live binding by
// the time either function runs.
//
// ⚠️ **FORBIDDEN: inlining the gateway's internal bearer (or an Authorization
// header built from any secret) here to back away from the cycle.** That is the
// tempting fix, it works, and `gateway.test.ts` could not see it until this
// ticket extended its sweep to this file. If the cycle ever does bite, the
// sanctioned answer is to lift `headersActingAs` and the token into a leaf
// module both sides import — one seam, moved; never a second copy of the
// bearer.
//
// The env variable is deliberately NOT named in this comment: the fences that
// forbid it here are plain substring scans over this file, and a fence you have
// to teach about prose is a fence somebody eventually widens.
import { GATEWAY_URL, headersActingAs } from "@/lib/gateway";

const providers: Provider[] = [];
if (process.env.AUTH_MICROSOFT_ENTRA_ID_ID) {
  providers.push(
    MicrosoftEntraId({
      clientId: process.env.AUTH_MICROSOFT_ENTRA_ID_ID,
      clientSecret: process.env.AUTH_MICROSOFT_ENTRA_ID_SECRET ?? "",
      // `organizations` = any Entra work/school directory. Pin to a single
      // tenant id ONLY for a single-customer silo deployment; the pooled tier
      // must stay multi-directory or it cannot serve a second customer.
      issuer: `https://login.microsoftonline.com/${process.env.AUTH_MICROSOFT_ENTRA_ID_TENANT ?? "organizations"}/v2.0`,
    }),
  );
}
if (process.env.AUTH_GOOGLE_ID) {
  providers.push(
    Google({
      clientId: process.env.AUTH_GOOGLE_ID,
      clientSecret: process.env.AUTH_GOOGLE_SECRET ?? "",
    }),
  );
}
/**
 * The NextAuth signing secret, named ONCE.
 *
 * ⚠️ It is a const rather than an inline `process.env` read at the `secret:`
 * option because `sendVerificationRequest` below must recompute the exact hash
 * `@auth/core` will store for the same send (`send-token.js:46,61` —
 * `createHash(\`${token}${provider.secret ?? options.secret}\`)`). Two copies of
 * "what the secret is" that drifted would make the send leg claim a slot with a
 * hash the person's mailed code can never produce, which is a code that never
 * verifies — the failure this recomputation exists to remove, reintroduced from
 * the other end. Fence: `emailOtp.test.ts` compares `otpWireHash` against the
 * pinned `@auth/core`'s own `createHash`; `signin.test.ts` pins that both the
 * `secret:` option and the hash call read this one name.
 */
const AUTH_SECRET = process.env.AUTH_SECRET ?? "dev-local-insecure-change-me";

// ── CP-2d · passwordless email OTP via Resend — ships DARK ──────────────────
//
// Registered ONLY when `isEmailOtpProviderReady` — env configured
// (`EMAIL_OTP_ENABLED === "true"` AND a Resend key, the Google
// `if (process.env.AUTH_GOOGLE_ID)` idiom one notch stricter) **AND** the
// Auth.js database adapter is wired (`EMAIL_OTP_ADAPTER_READY`, `false` today).
// With any of those absent the provider is never in the array and NextAuth
// initialises **byte-identical** to today: no send, no crash,
// `/api/auth/providers` unchanged. It rides the ONE NextAuth session below —
// same handlers, same `jwt`/`session` callbacks, JWT strategy unchanged — never
// a second auth path.
//
// 🛑 **The adapter half is not a nicety — it is a site-safety gate.**
// `@auth/core`'s `assertConfig` returns `MissingAdapter` for ANY email provider
// registered without an adapter, and it does so on **every** `/api/auth/*`
// request — so an email provider without an adapter **500s ALL sign-in, Google
// and Microsoft included**, not merely OTP. Gating on the env flag alone would
// make `EMAIL_OTP_ENABLED=true` a one-line documented owner action that takes
// the whole site's auth down. `isEmailOtpProviderReady` is what makes that
// impossible: with the adapter unwired (today) the flag+key are inert.
//
// **OTP, not magic-link** (owner's ask, D46.3): `generateVerificationToken`
// mints a 6-digit numeric code and `sendVerificationRequest` emails THAT code
// via the injectable Resend transport, so the person types a code rather than
// following a link. Auth.js v5's built-in `Resend` provider is magic-link by
// default; the two overrides below turn it into numeric OTP without leaving the
// one provider mechanism. The verified address is Auth.js's `identifier`, never
// a request body field (R11).
//
// ✅ **Slice 2 (2026-08-23) ARMED it**: the adapter below is wired in as
// NextAuth's `adapter`, `EMAIL_OTP_ADAPTER_READY` is now `true`, and the
// code-entry page is `pages.verifyRequest`. So the gate is once again exactly
// "the owner set the flag and the key" — which is the point, and why the rate
// limit (gateway-side, `routes/email_otp.py`) is now what carries the safety
// rather than the adapter guard.
const emailOtpArmed = isEmailOtpProviderReady(process.env as EmailOtpEnv);
if (emailOtpArmed) {
  const apiKey = process.env.RESEND_API_KEY as string;
  const from = emailOtpFrom(process.env as EmailOtpEnv);
  const send = resendSender(apiKey);
  providers.push(
    Resend({
      apiKey,
      from,
      maxAge: DEFAULT_OTP_MAX_AGE_S,
      generateVerificationToken: generateOtp,
      // ⚠️ **The send budget is reserved BEFORE the mail goes out, and it has
      // to be here** (customer_console.md §CP-2d clause 11). `@auth/core`'s
      // `lib/actions/signin/send-token.js` starts THIS function and
      // `adapter.createVerificationToken` concurrently in one `Promise.all`,
      // and hands the token hash only to the second — so a budget enforced in
      // the adapter alone would refuse the token while this function had
      // already mailed a code. Both legs name the same send by its `expires`
      // instant, which is how the gateway counts one send once. A refusal
      // throws, so nothing is sent and the sign-in fails closed.
      //
      // ⚠️ **The claim carries the hash of the code THIS leg is about to mail**
      // (repair of review finding P2, round 2, 2026-08-23). Stopping the
      // duplicate's *mail* was only half of it: `send-token.js` starts both legs
      // in one `Promise.all`, so a rejected `sendVerificationRequest` cancels
      // nothing and the loser's `createVerificationToken` still landed on the
      // shared `(identifier, expires)` row — overwriting the winner's hash, so
      // the one email that DID go out carried a code that could never verify.
      // `@auth/core` hands this leg the raw code and the other leg the hash, so
      // this is the only place the two can be reconciled: recompute the same
      // `createHash(`${token}${secret}`)` (`send-token.js:46,61`) and hand it to
      // the claim, which writes it atomically. `record_token` server-side then
      // refuses to overwrite. `provider.secret` first, exactly as `send-token.js`
      // resolves it — this deployment sets none, but a copy that ignored it would
      // be wrong the day one is set.
      sendVerificationRequest: async ({ identifier, token, expires, provider }) => {
        const tokenHash = await otpWireHash(
          token,
          (provider as { secret?: string }).secret ?? AUTH_SECRET,
        );
        await reserveOtpSend(identifier, expires, tokenHash);
        await sendOtpEmail(send, {
          to: identifier,
          from: provider.from ?? from,
          code: token,
          maxAgeS: DEFAULT_OTP_MAX_AGE_S,
        });
      },
    }),
  );
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  secret: AUTH_SECRET,
  providers,
  // ⚠️ **CP-2d slice 2 (ruling H-D): the adapter and the strategy pin are ONE
  // change and must never be separated.** `@auth/core@0.41.2`'s `lib/init.js:74`
  // derives `strategy: config.adapter ? "database" : "jwt"`, so merely PASSING
  // an adapter silently converts the whole product — Google and Microsoft
  // included — to database sessions. `assertConfig` does not catch it: with an
  // email provider present it demands only the three `emailMethods` and never
  // the ten `sessionMethods` (`lib/utils/assert.js:129-145`), so the failure is
  // not a config error at boot but a `createSession is not a function` on every
  // sign-in. Pinning `jwt` is also what keeps this adapter's method set minimal
  // and keeps permissions OUT of the token (see `workbench/AGENTS.md`: a JWT
  // outlives an access change, so authorization stays server-resolved). Fenced
  // both ways by `signin.test.ts`.
  session: { strategy: "jwt" },
  // Passed as `undefined` when OTP is not armed, which `@auth/core` treats
  // exactly as absent (`assert.js:157` guards on truthiness) — so an
  // OAuth-only box is unchanged, no adapter method is ever reachable, and the
  // dark-ship promise holds.
  //
  // 🛑 **When it IS armed, this adapter is on Google's and Microsoft's path
  // too, not only OTP's** (repair of review finding P0, 2026-08-23).
  // `lib/actions/callback/index.js:56-62` destructures and calls
  // `getUserByAccount` for every OAuth callback the moment an adapter is
  // present, and `handle-login.js:175-250` calls it again and then consults
  // `getUserByEmail`. So the adapter is written to be behaviourally
  // TRANSPARENT — stateless user methods that reproduce the adapter-less path
  // — rather than minimal. See `lib/emailOtp.ts::emailOtpAdapterOver`; the
  // reachable set is derived from the pinned source by
  // `emailOtpAdapter.test.ts` and replayed against the real object by
  // `emailOtp.test.ts`, so a version bump that adds a call site reds instead of
  // taking every sign-in down on the day the flag is flipped.
  adapter: emailOtpArmed ? emailOtpAdapter() : undefined,
  callbacks: {
    /**
     * **Ask the registry whether this sign-in may proceed** (WS-31 CP-2b,
     * `customer_console.md` §6(g), clause 11).
     *
     * This is the only callback whose return value can both admit and refuse,
     * so the resolve call and the decision it drives are ONE function rather
     * than two that have to agree. The `jwt` callback's `account`-present
     * branch was the proposed home and was overturned: it runs *after* the
     * sign-in decision and cannot refuse, and returning `null` from it drops
     * the person back on the sign-in page with no explanation at all — the
     * wrong-looking denial D33.1 exists to remove.
     *
     * Returning a STRING redirects (`@auth/core` 0.41.2 — `handleAuthorized`
     * returns `redirect({url: authorized})` and the caller returns before the
     * session cookie is written), which is what carries a distinguishable
     * reason. Returning `false` would yield `AccessDenied` and nothing else.
     *
     * ⚠️ **Ships dark, behind a flag of its own —
     * `CUSTOMER_CONSOLE_RESOLVE_ENABLED`, default unset = OFF.** Anything but
     * the exact string `"true"` returns `true` before doing anything at all,
     * so a deployment that has not opted in signs people in exactly as it did
     * before CP-2b: no fetch, no latency, no new failure mode.
     *
     * ⚠️ **Why a dedicated flag rather than the obvious one-liner** (repair of
     * finding F1, independent verification 2026-08-18; `customer_console.md`
     * §6(g)). The gate used to be `CUSTOMER_CONSOLE_URL` alone, where the
     * gateway's `console_resolve.is_wired()` requires **both** that URL and the
     * deployment key. But `CUSTOMER_CONSOLE_URL` is already a live Next-side
     * variable — the billing proxy (`app/api/billing/summary/route.ts`) reads
     * it — so "URL set, key unset" is what a Console-connected box looks like
     * the moment this merges, and measured, that combination fetched and then
     * refused sign-in (`ConsoleUnavailable`) whenever the gateway was
     * momentarily unreachable. The hop armed itself with nobody flipping
     * anything. Checking the deployment **key** here is the other obvious fix
     * and is FORBIDDEN by §6(f): that credential is gateway-only and must
     * never be readable from the browser tier. Hence one variable, and it is
     * the flag. `CUSTOMER_CONSOLE_URL` keeps serving the billing surface and
     * is **not read by this callback at all** — fenced by
     * `signin.test.ts`'s *"does not arm itself off CUSTOMER_CONSOLE_URL (F1)"*,
     * which scans this callback's BODY, which is why the variable may be named
     * in this comment while the deployment key may not be named anywhere in
     * this file.
     *
     * ⚠️ **Flag ON means the refusals are intended, including during a deploy
     * window.** A box that says it is wired fails CLOSED on every transport or
     * provisioning failure — gateway unreachable, or gateway env half-filled —
     * because a box claiming to be wired while half-provisioned is a
     * provisioning error and must not silently admit. Flipping the flag on a
     * live deployment is 🔴 OWNER-GATE (§8 gate 7).
     *
     * ⚠️ The email comes from the **provider-verified profile**, never from
     * request input (R11) — the same value the `jwt` callback below already
     * trusts.
     */
    // `email` is Auth.js's own parameter name for the email-flow marker; it is
    // renamed on binding because the resolved address below is called `email`
    // and two things called that in one function is how the wrong one gets read.
    async signIn({ profile, user, email: emailFlow }) {
      // ── CP-2d slice 2 · the email provider calls this callback TWICE ───────
      //
      // `@auth/core`'s `lib/actions/signin/send-token.js` invokes it once at
      // "send me a code", with `email.verificationRequest` set and BEFORE the
      // person has proved anything, and `lib/actions/callback/index.js` invokes
      // it again after the code round-trip. **Only the second leg may resolve.**
      //
      // ⚠️ A resolve on the first leg would allocate a SEAT for an address
      // typed by an anonymous visitor on the sign-in form — the farmable seat
      // cap `gateway/routes/signin.py`'s docstring exists to prevent, which it
      // does by reducing resolve from *every authenticated call* to *the
      // completion of a sign-in*. A send is not a completion.
      //
      // The code is still emailed (the refusal is the answer, delivered once
      // the code is entered) and the volume is bounded by the gateway's
      // three-sends-an-hour budget. Beyond that, the OTP path is byte-for-byte
      // the Google path from here down: no branch of its own, the same
      // `console-empty` → `signup_eligible` → limbo funnel, the same refusal
      // codes. See `customer_console.md` §CP-2d clause 13.
      if (emailFlow?.verificationRequest) return true;

      if (process.env.CUSTOMER_CONSOLE_RESOLVE_ENABLED !== "true") return true;

      const email =
        (typeof profile?.email === "string" && profile.email) ||
        (typeof user?.email === "string" && user.email) ||
        "";
      // Fail CLOSED. A provider that returned no address gives us nothing to
      // resolve, and admitting an unidentified person is the posture CP-0
      // removed.
      if (!email) return "/signin?error=ConsoleUnavailable";

      try {
        const res = await fetch(`${GATEWAY_URL}/signin/resolve`, {
          method: "POST",
          headers: headersActingAs(email, {
            "Content-Type": "application/json",
          }),
          body: JSON.stringify({
            display_name: typeof user?.name === "string" ? user.name : "",
          }),
          cache: "no-store",
        });
        if (!res.ok) return "/signin?error=ConsoleUnavailable";
        const answer = (await res.json()) as {
          admit?: boolean;
          code?: string | null;
          signup_eligible?: boolean;
        };
        if (answer?.admit) return true;
        // WS-31 CP-2c §6(j) row vii — the self-serve-signup "limbo" branch.
        // Under the flag, the ZERO-ORG refusal alone admits an ORG-LESS session
        // so the ordinary redirect below lands the person where `/signup` is
        // reachable instead of on an error page. It keys on the gateway's
        // load-bearing `signup_eligible` signal — TRUE for outcome iv, the
        // genuinely org-less person, and FALSE for every other refusal — NOT on
        // `code`. `AccessDenied` is shared by a SUSPENDED / non-paying org
        // (`console-refused`, `cache-dead`) and a seat-capped one
        // (`console-error`); registry suspension is enforced ONLY at this resolve
        // door, so re-keying the funnel on the code would readmit a non-paying
        // customer into an org-less session and hand back the access the
        // suspension exists to deny. Those refusals must keep falling through to
        // the redirect below. `source` is log-only and MUST NOT be branched on.
        //
        // Admitting caches NOTHING — this callback writes no cache, and the
        // zero-org path's invalidate already fired gateway-side, so row iv's
        // cache-nothing rule is intact and every tenant-bound surface stays
        // behind the gateway's per-call resolution and fail-closed tenant
        // binding.
        //
        // Ships dark, and fails closed: an absent/false `signup_eligible` opens
        // no funnel. The flag is an EQUALITY against "true", the same discipline
        // as the resolve gate above — unset, or any other value, skips the
        // branch and the refusal below is byte-identical to today. Flipping it
        // on a live deployment is OWNER-GATE (§8 gate 8).
        if (
          answer?.signup_eligible === true &&
          process.env.SELF_SERVE_SIGNUP_ENABLED === "true"
        ) {
          return true;
        }
        // `code` comes from ANOTHER service and lands in a URL, so it is
        // encoded rather than trusted to be URL-safe: a code carrying `&`,
        // `#` or a space would otherwise truncate the query string or arrive
        // as a second parameter, and the sign-in page would render the wrong
        // copy for a refusal the person cannot act on anyway. The three codes
        // shipped today are alphanumeric; this is for the fourth.
        return `/signin?error=${encodeURIComponent(
          answer?.code || "ConsoleUnavailable",
        )}`;
      } catch {
        // The gateway is unreachable from here. The person has done nothing
        // wrong and the copy says so; "access denied" would be a lie that
        // generates a support ticket and a password reset that fix nothing.
        return "/signin?error=ConsoleUnavailable";
      }
    },
    async jwt({ token, profile, account, user }) {
      if (profile?.email) {
        token.email = profile.email as string;
      } else if (typeof user?.email === "string") {
        // CP-2d: the email OTP provider carries no OAuth `profile`. The verified
        // address rides `user.email` from Auth.js's completed round-trip — the
        // code proved ownership — never from request input (R11).
        token.email = user.email;
      }
      if (account?.provider) {
        token.provider = account.provider;
      }
      return token;
    },
    async session({ session, token }) {
      if (token?.email) {
        session.user.email = token.email as string;
      }
      if (token?.name) {
        session.user.name = token.name as string;
      }
      return session;
    },
  },
  pages: {
    signIn: "/signin",
    // CP-2d slice 2. Without this, a person who asked for a code lands on
    // `@auth/core`'s built-in "check your email" page — correct for a magic
    // link, a dead end for an OTP, because there is nowhere to type the code.
    // `lib/pages/index.js:107-110` redirects here carrying the original query.
    // The route is in `proxy.ts`'s PUBLIC_PAGES: it is reached by somebody who
    // is, by definition, not signed in yet.
    verifyRequest: "/signin/code",
  },
});

/**
 * **Authentication is enforced.** The predicate every call site should use.
 *
 * Note it is `!isDevBypass`, NOT "a provider is configured": an unconfigured
 * *production* deployment enforces. See `@/authPosture` for why that combination
 * exists and `proxy.ts` for how it is answered.
 */
export { isAuthEnabled } from "@/authPosture";