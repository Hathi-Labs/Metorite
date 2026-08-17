/**
 * Whether authentication is configured, bypassed, or enforced — as a pure
 * function of the environment.
 *
 * Spec: `project-docs/specs/platform_control_plane.md` CP-0 ·
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

/** The environment fields the posture depends on. Nothing else is read. */
export interface AuthEnv {
  AUTH_MICROSOFT_ENTRA_ID_ID?: string;
  AUTH_GOOGLE_ID?: string;
  NODE_ENV?: string;
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
  id: "google" | "microsoft-entra-id";
  label: string;
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
 */
export function configuredProviders(env: AuthEnv): ConfiguredProvider[] {
  const out: ConfiguredProvider[] = [];
  if (env.AUTH_GOOGLE_ID) {
    out.push({ id: "google", label: "Continue with Google" });
  }
  if (env.AUTH_MICROSOFT_ENTRA_ID_ID) {
    out.push({ id: "microsoft-entra-id", label: "Continue with Microsoft" });
  }
  return out;
}

const posture = authPosture(process.env as AuthEnv);

export const isAuthConfigured = posture.isAuthConfigured;
export const isDevBypass = posture.isDevBypass;
export const isAuthEnabled = posture.isAuthEnabled;
