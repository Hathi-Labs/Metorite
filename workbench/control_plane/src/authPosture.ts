/**
 * Whether authentication is configured, bypassed, or enforced — as a pure
 * function of the environment.
 *
 * Spec: `project-docs/specs/customer_console.md` CP-0 ·
 * `project-docs/specs/saas_operations_doctrine.md` §4 findings 1–2 · D33.1.
 *
 * ## Why this is its own module
 *
 * These three booleans decide whether anonymous callers get in. They are a pure
 * function of `process.env` and nothing else — but they used to live in
 * `auth.ts`, behind NextAuth's import graph, which drags in `next/server` and so
 * cannot be loaded in a plain node test. A security predicate that is awkward to
 * test is a security predicate that goes untested, and this one was: the
 * fail-open path (D33.1) shipped and survived because no test could reach it
 * without standing up the framework.
 *
 * `auth.ts` re-exports all three, so every call site keeps importing `@/auth`
 * and nothing else moves.
 */

import {
  EMAIL_OTP_ADAPTER_READY,
  EMAIL_OTP_LABEL,
  EMAIL_OTP_PROVIDER_ID,
  isEmailOtpProviderReady,
} from "@/lib/emailOtp";

/** The environment fields the posture depends on. Nothing else is read. */
export interface AuthEnv {
  AUTH_MICROSOFT_ENTRA_ID_ID?: string;
  AUTH_GOOGLE_ID?: string;
  NODE_ENV?: string;
  // CP-2d · passwordless email OTP. Read only through the emailOtp gate, so the
  // gate lives in one place and this list stays the whole surface.
  EMAIL_OTP_ENABLED?: string;
  RESEND_API_KEY?: string;
  EMAIL_OTP_FROM?: string;
}

export interface AuthPosture {
  /** A real identity provider is configured. */
  isAuthConfigured: boolean;
  /**
   * The laptop case — no provider AND not production — and the **only** case
   * permitted to serve anonymous callers.
   */
  isDevBypass: boolean;
  /**
   * **Authentication is enforced.** What call sites should branch on.
   *
   * Deliberately `!isDevBypass` rather than `isAuthConfigured`: an unconfigured
   * *production* deployment must enforce, even though it has no provider to
   * enforce with. That combination is a misconfiguration — a bad deploy, a lost
   * secret — and the honest answer to it is a refusal (`proxy.ts` returns 503),
   * not the open door the old `Boolean(client_id)` gate produced.
   */
  isAuthEnabled: boolean;
}

/**
 * Derive the posture from an environment.
 *
 * Keyed on `NODE_ENV !== "production"` rather than an opt-in bypass flag: a flag
 * that defaults to open is the same defect wearing a different name, and
 * `next build && next start` already sets production for every real deployment.
 */
export function authPosture(env: AuthEnv): AuthPosture {
  const isAuthConfigured = configuredProviders(env).length > 0;
  const isDevBypass = !isAuthConfigured && env.NODE_ENV !== "production";
  return { isAuthConfigured, isDevBypass, isAuthEnabled: !isDevBypass };
}

/** One configured identity provider, as the signin surface renders it. */
export interface ConfiguredProvider {
  /** NextAuth provider id — must match what `auth.ts` registers. */
  id: "google" | "microsoft-entra-id" | "resend";
  label: string;
  /**
   * How the surface starts the flow. Omitted (⇒ `"oauth"`) is a one-click
   * redirect to the IdP; `"email"` needs an address first, so the surface
   * renders a field before it can call `signIn(id, { email })` (CP-2d).
   */
  kind?: "oauth" | "email";
}

/**
 * The configured providers, in display order (Google first — D40.4).
 *
 * The ONE place "which IdPs exist" is derived from env: `auth.ts` registers
 * providers from the same keys, {@link authPosture}'s `isAuthConfigured` is
 * defined as "this list is non-empty", and the signin page renders exactly
 * this list — so adding a third provider updates the 503 posture and the
 * sign-in button in the same edit, instead of leaving a reachable sign-in
 * page with no working button (the parallel-env-read defect this replaces).
 *
 * `adapterReady` gates ONLY the CP-2d email-OTP entry and defaults to the
 * `EMAIL_OTP_ADAPTER_READY` source of truth, so the email button appears only
 * when the provider will actually register (env configured AND adapter wired) —
 * never while a half-armed flag would otherwise advertise a door that 500s. The
 * parameter exists so tests can drive the would-be-lit path.
 */
export function configuredProviders(
  env: AuthEnv,
  adapterReady: boolean = EMAIL_OTP_ADAPTER_READY,
): ConfiguredProvider[] {
  const out: ConfiguredProvider[] = [];
  if (env.AUTH_GOOGLE_ID) {
    out.push({ id: "google", label: "Continue with Google" });
  }
  if (env.AUTH_MICROSOFT_ENTRA_ID_ID) {
    out.push({ id: "microsoft-entra-id", label: "Continue with Microsoft" });
  }
  // CP-2d — passwordless email OTP via Resend, last so the OAuth options lead.
  // The SAME combined gate `auth.ts` registers the provider from — env
  // configured AND the adapter wired — so a box that has the provider also has
  // the button, and a box without a usable provider (flag off, key absent, OR
  // adapter unwired) has neither. Only appears once the provider genuinely
  // registers, so the button never advertises a door that would 500 sign-in.
  if (isEmailOtpProviderReady(env, adapterReady)) {
    out.push({ id: EMAIL_OTP_PROVIDER_ID, label: EMAIL_OTP_LABEL, kind: "email" });
  }
  return out;
}

const posture = authPosture(process.env as AuthEnv);

export const isAuthConfigured = posture.isAuthConfigured;
export const isDevBypass = posture.isDevBypass;
export const isAuthEnabled = posture.isAuthEnabled;
