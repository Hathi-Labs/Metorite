/**
 * Fences for the provider-driven sign-in surface (R7; PR #5 review round 1).
 *
 * Two of these are source-level assertions in the conformance suite's style,
 * because importing `page.tsx` here would drag `next-auth/react` into a node
 * test for no extra coverage:
 *
 *   1. The segment must stay DYNAMIC. Statically prerendered, the provider
 *      list freezes at `next build` while NextAuth reads the same env at
 *      runtime — an env-plus-restart change then registers a provider whose
 *      button never appears (measured: the baked signin.html carried only the
 *      build-time providers).
 *   2. The page must derive its buttons from the ONE provider seam
 *      (`@/authPosture.configuredProviders`), never a parallel env read — a
 *      third provider added to the seam but not here would leave a reachable
 *      sign-in page with no working button on a correctly configured box.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { authPosture, configuredProviders } from "@/authPosture";

import { signInErrorMessage } from "./errorCopy";

const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf-8");

// ── WS-31 CP-2b (customer_console.md §6(g), clause 11) ─────────────────────
//
// Source-level over `auth.ts`, in this file's established style, because the
// subject is WHERE a call may appear rather than what it returns. The Python
// fence (`tests/unit/test_console_dependency_boundary.py`) pins that
// `console_resolve` has exactly one caller; on its own that is satisfied by a
// BFF calling `POST /signin/resolve` from anywhere, which is what these close.
//
// ⚠️ **Source-regex is not a preference here, it is the only option, and the
// F1 repair did not change that** (measured 2026-08-18): vitest in this tree is
// node-env, and `import("@/auth")` dies inside `next-auth/lib/env.js` with
// *"Cannot find module …/node_modules/next/server"* before any callback can be
// called. So the gate below is pinned by its SHAPE. What no test in this tree
// can prove is that the shape behaves — that the flag being off issues no
// fetch — and the F1 finding is exactly what that blind spot cost once, so it
// is a review check and it is written down rather than assumed away.
const authSrc = readFileSync(new URL("../../auth.ts", import.meta.url), "utf-8");

/** The source of one `callbacks:` entry, from its name to the next one. */
function callbackBody(src: string, name: string): string {
  const start = src.indexOf(`async ${name}(`);
  if (start < 0) return "";
  const rest = src.slice(start + 1);
  const nextIdx = ["async signIn(", "async jwt(", "async session("]
    .map((marker) => rest.indexOf(marker))
    .filter((i) => i >= 0)
    .sort((a, b) => a - b)[0];
  return nextIdx === undefined ? rest : rest.slice(0, nextIdx);
}

const API_DIR = fileURLToPath(new URL("../api", import.meta.url));

function routeFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) routeFiles(full, out);
    else if (entry === "route.ts") out.push(full);
  }
  return out;
}

describe("the signin segment", () => {
  it("is dynamic — env is read per request, never baked at build", () => {
    expect(page).toContain('export const dynamic = "force-dynamic"');
  });

  it("derives buttons from the authPosture seam, not a parallel env read", () => {
    expect(page).toContain("configuredProviders(");
    expect(page).not.toContain("process.env.AUTH_GOOGLE_ID");
    expect(page).not.toContain("process.env.AUTH_MICROSOFT_ENTRA_ID_ID");
  });
});

describe("the provider seam", () => {
  it("renders one entry per configured provider, Google first (D40.4)", () => {
    expect(configuredProviders({})).toEqual([]);
    expect(configuredProviders({ AUTH_GOOGLE_ID: "g" })).toEqual([
      { id: "google", label: "Continue with Google" },
    ]);
    expect(
      configuredProviders({ AUTH_MICROSOFT_ENTRA_ID_ID: "m" }),
    ).toEqual([{ id: "microsoft-entra-id", label: "Continue with Microsoft" }]);
    expect(
      configuredProviders({
        AUTH_MICROSOFT_ENTRA_ID_ID: "m",
        AUTH_GOOGLE_ID: "g",
      }).map((p) => p.id),
    ).toEqual(["google", "microsoft-entra-id"]);
  });

  it("agrees with isAuthConfigured — one definition, not two", () => {
    for (const env of [
      {},
      { AUTH_GOOGLE_ID: "g" },
      { AUTH_MICROSOFT_ENTRA_ID_ID: "m" },
      { AUTH_GOOGLE_ID: "", AUTH_MICROSOFT_ENTRA_ID_ID: "" },
    ]) {
      expect(authPosture(env).isAuthConfigured).toBe(
        configuredProviders(env).length > 0,
      );
    }
  });
});

describe("error copy speaks Auth.js v5", () => {
  it("maps the v5 types a user can actually produce", () => {
    // v5 emits OAuthCallbackError — the v4 codes (OAuthSignin/OAuthCallback)
    // never arrive, which is how a consent-screen cancel reached the raw
    // fallback before.
    expect(signInErrorMessage("OAuthCallbackError")).toMatch(/cancelled/);
    expect(signInErrorMessage("Configuration")).toMatch(/misconfigured/);
    expect(signInErrorMessage("AccessDenied")).toMatch(/invite/);
    expect(signInErrorMessage("SomethingNew")).toBe(
      "Authentication error: SomethingNew",
    );
    expect(signInErrorMessage(null)).toBeNull();
  });

  it("errorCopy speaks the two CP-2b codes", () => {
    const unavailable = signInErrorMessage("ConsoleUnavailable");
    const chooser = signInErrorMessage("WorkspaceChooserRequired");

    expect(unavailable).toBeTruthy();
    expect(chooser).toBeTruthy();

    // D33.1: never blame the person for a state they did not create. A person
    // refused because a service is down has not been denied access, and a
    // person in two organizations has not failed an authorization check —
    // which is exactly what Auth.js's own `AccessDenied` copy would say, and
    // why these are custom codes rather than reused types.
    for (const copy of [unavailable, chooser]) {
      expect(copy).not.toMatch(/access denied|isn't authorized/i);
    }

    // Each names its own cause, so the two are distinguishable to a reader and
    // not merely to a switch statement.
    expect(unavailable).toMatch(/temporarily unavailable/i);
    expect(unavailable).toMatch(/not with your account/i);
    expect(chooser).toMatch(/more than one organization/i);

    // The multi-org copy names the CAUSE, never the COUNT: errorCopy maps a
    // code to a STATIC string, so a count would have to ride the query string
    // — a second field on a public URL for a number nobody can act on.
    expect(chooser).not.toMatch(/\b\d+\b/);
  });
});

describe("the sign-in resolve hop (CP-2b)", () => {
  it("resolve fires only from the signIn callback", () => {
    const signIn = callbackBody(authSrc, "signIn");
    expect(signIn).toContain("/signin/resolve");

    // Not from a callback that cannot refuse. `jwt` runs AFTER the decision is
    // made and has no return value that produces a renderable error code, so a
    // resolve there could satisfy clauses 6, 7 and 9 only by pretending to.
    expect(callbackBody(authSrc, "jwt")).not.toContain("/signin/resolve");
    expect(callbackBody(authSrc, "session")).not.toContain("/signin/resolve");

    // And from no route file: the Python fence pins ONE Python caller, and a
    // BFF route calling this endpoint would be a second door to the same
    // seat-allocating call that fence exists to keep singular.
    const offenders = routeFiles(API_DIR).filter((p) =>
      readFileSync(p, "utf8").includes("/signin/resolve"),
    );
    expect(offenders).toEqual([]);
  });

  it("the resolve email comes from the provider profile, never from request input", () => {
    const signIn = callbackBody(authSrc, "signIn");
    expect(signIn).toContain("profile?.email");

    // R11. There is no request in a `signIn` callback to take an identity
    // from, and the point of this assertion is that there must never be one
    // reached for — no headers(), no cookies(), no searchParams.
    for (const forbidden of [
      "searchParams",
      "headers()",
      "cookies()",
      "req.",
      "request.",
      "NextRequest",
    ]) {
      expect(signIn).not.toContain(forbidden);
    }
  });

  it("goes through headersActingAs, minting no bearer of its own", () => {
    const signIn = callbackBody(authSrc, "signIn");
    expect(signIn).toContain("headersActingAs(email");

    // `gatewayHeaders()` calls auth() and would find nothing — this callback
    // fires before a session exists. And inlining the internal token here is
    // the forbidden way around the auth.ts ⇄ lib/gateway.ts cycle; it is
    // fenced from the other side too, by gateway.test.ts's widened sweep.
    expect(signIn).not.toContain("gatewayHeaders(");
    expect(authSrc).not.toContain("GATEWAY_INTERNAL_TOKEN");
    expect(authSrc).not.toContain("CUSTOMER_CONSOLE_DEPLOYMENT_KEY");
  });

  it("is inert until CUSTOMER_CONSOLE_RESOLVE_ENABLED is exactly \"true\"", () => {
    // Ships dark. The gate is read FIRST, before anything else in the
    // callback, so a deployment that has not opted in signs people in with no
    // fetch, no latency and no new failure mode — byte-identical to the
    // behaviour before CP-2b. Flipping the flag on a live box is OWNER-GATE
    // (§8 gate 7).
    //
    // ⚠️ The gate is an EQUALITY against "true", not a truthiness test: an
    // operator who writes `CUSTOMER_CONSOLE_RESOLVE_ENABLED=false` while
    // debugging a sign-in outage must get OFF, and every truthy-string reading
    // of that value arms the hop instead.
    const signIn = callbackBody(authSrc, "signIn");
    const gate = signIn.indexOf("process.env.CUSTOMER_CONSOLE_RESOLVE_ENABLED");
    const call = signIn.indexOf("/signin/resolve");
    expect(gate).toBeGreaterThan(-1);
    expect(gate).toBeLessThan(call);
    expect(signIn).toMatch(
      /if \(process\.env\.CUSTOMER_CONSOLE_RESOLVE_ENABLED !== "true"\) return true;/,
    );
  });

  it("does not arm itself off CUSTOMER_CONSOLE_URL (F1)", () => {
    // The repair of finding F1 (independent verification, 2026-08-18), and the
    // reason this ticket has a flag of its own rather than the obvious
    // one-liner.
    //
    // The gate used to be `if (!process.env.CUSTOMER_CONSOLE_URL) return true;`
    // — ONE variable, where the gateway's `is_wired()` requires TWO (URL *and*
    // deployment key). `CUSTOMER_CONSOLE_URL` is already a live Next-side
    // variable on any Console-connected box (the billing proxy reads it,
    // `app/api/billing/summary/route.ts`), so "URL set, key unset" is exactly
    // what this deployment looks like the moment the hop merges: measured, that
    // combination issued one fetch and — with the gateway unreachable, i.e. an
    // ordinary deploy window — returned "/signin?error=ConsoleUnavailable".
    // The hop armed itself, and refused sign-in, with nobody flipping anything.
    //
    // Checking the deployment KEY here instead is forbidden (§6(f): that
    // credential is gateway-only and must never reach the browser tier — the
    // fence for it is three assertions up). So the callback reads ONE variable
    // and it is the flag; `CUSTOMER_CONSOLE_URL` keeps serving the billing
    // surface and is not an input to this decision at all.
    const signIn = callbackBody(authSrc, "signIn");
    expect(signIn).not.toContain("CUSTOMER_CONSOLE_URL");
  });

  it("refuses with the two codes and never with a bare false", () => {
    const signIn = callbackBody(authSrc, "signIn");
    // Returning `false` yields Auth.js's `AccessDenied` and nothing else,
    // which is the copy D33.1 forbids for both of these cases. The string
    // return is the mechanism that carries a distinguishable reason.
    expect(signIn).toContain('"/signin?error=ConsoleUnavailable"');
    expect(signIn).toContain("/signin?error=${encodeURIComponent(");
    expect(signIn).not.toMatch(/\breturn false\b/);
  });

  it("encodes the code it puts in the redirect URL", () => {
    // Review note 1 (2026-08-18). `answer.code` is a value from ANOTHER
    // service interpolated straight into a URL: unencoded, a code carrying
    // `&`, `#` or a space either truncates the query string or arrives as a
    // second parameter, and `signInErrorMessage` then renders the wrong copy
    // (or the raw fallback) for a refusal the person cannot act on anyway.
    // The three codes we ship are alphanumeric, so this is about the day the
    // Console adds a fourth — which is exactly when nobody is looking here.
    const signIn = callbackBody(authSrc, "signIn");
    expect(signIn).toMatch(
      /\/signin\?error=\$\{encodeURIComponent\(\s*answer\?\.code/,
    );
    // And no OTHER interpolation into that URL slips past unencoded.
    for (const interpolated of signIn.matchAll(/\/signin\?error=\$\{([^}]*)/g)) {
      expect(interpolated[1]).toContain("encodeURIComponent(");
    }
  });
});

// ── WS-31 CP-2c §6(j) row vii (customer_console.md done-when 7) ─────────────
//
// The self-serve-signup "limbo" branch, NARROWED to zero-org-only after review
// found a P1: keying on `code === "AccessDenied"` alone admitted a SUSPENDED /
// non-paying org and a seat-capped one into an org-less session, because the
// gateway emits that same code for `console-refused`, `cache-dead` and
// `console-error` as well as the genuinely org-less `console-empty`. Registry
// suspension is enforced ONLY at the resolve door, so that would have handed a
// non-paying customer back the access the suspension exists to deny. The funnel
// now keys on the gateway's load-bearing `signup_eligible` signal — True for
// the zero-org outcome ALONE — and `code` is no longer the trigger.
//
// Source-level over `auth.ts`, same reason the CP-2b hop above is: vitest in
// this tree is node-env and `import("@/auth")` dies inside next-auth before any
// callback can be called, so the branch is pinned by its SHAPE. What no test in
// this tree can prove is that the shape behaves — that the flag being off is
// byte-identical to today — so that is the review check (DESIGN_SYSTEM §8's
// cousin), written down here rather than assumed. The SECURITY half — that a
// suspended/seat-cap `AccessDenied` never gets `signup_eligible` — lives where
// it can RUN, in `tests/unit/test_deployment_resolve_cache.py` against the live
// DB; this file cannot execute the callback, so it pins the SHAPE only.
describe("the self-serve signup limbo branch (CP-2c §6(j) row vii)", () => {
  it('under SELF_SERVE_SIGNUP_ENABLED === "true", the zero-org signup_eligible case returns true', () => {
    const signIn = callbackBody(authSrc, "signIn");

    // The branch exists and keys on the gateway's `signup_eligible === true`
    // signal ANDed with the signup flag → `return true`.
    expect(signIn).toMatch(
      /answer\?\.signup_eligible === true\s*&&\s*[\s\S]*?SELF_SERVE_SIGNUP_ENABLED === "true"[\s\S]*?return true;/,
    );

    // EQUALITY against "true" for the flag, never truthiness — an operator
    // debugging a sign-in outage who sets the flag to "false" must get OFF, and
    // every truthy-string reading arms the limbo instead. A `truthy instead of
    // ===` mis-gate drops the `=== "true"` literal and fails this line RED.
    expect(signIn).toContain('SELF_SERVE_SIGNUP_ENABLED === "true"');

    // And `signup_eligible` is compared with `=== true`, not truthiness, so an
    // absent/undefined value fails closed (no funnel).
    expect(signIn).toContain("answer?.signup_eligible === true");
  });

  it("does NOT key the funnel on the AccessDenied code — suspended/seat-cap stay refused", () => {
    const signIn = callbackBody(authSrc, "signIn");
    // The P1 negative pin. `console-refused` (403 suspended), `cache-dead`
    // (cached suspended) and `console-error` (409 seat-cap) all carry
    // `AccessDenied`; only `console-empty` carries `signup_eligible`. If the
    // guard ever re-keyed on the code, a non-paying org would regain access by
    // re-signing-in. So the exact string `answer?.code === "AccessDenied"` must
    // be ABSENT from the callback — the code is never the trigger.
    expect(signIn).not.toContain('answer?.code === "AccessDenied"');
    expect(signIn).not.toMatch(/answer\?\.code[\s\S]*?===\s*"AccessDenied"/);
  });

  it("flag unset/other value → row iv verbatim: the refusal redirect is unchanged", () => {
    const signIn = callbackBody(authSrc, "signIn");
    // Row iv is byte-identical to today: the encoded refusal redirect is still
    // present and is still the fall-through below the limbo branch, never
    // replaced by it. Returning a bare `false` (Auth.js `AccessDenied` copy,
    // which D33.1 forbids) is still not the mechanism.
    expect(signIn).toContain("/signin?error=${encodeURIComponent(");
    expect(signIn).toContain('answer?.code || "ConsoleUnavailable"');
    expect(signIn).not.toMatch(/\breturn false\b/);

    // And the limbo branch sits AFTER the admit and BEFORE the redirect, so the
    // flag-off path (and every non-zero-org refusal) reaches row iv unchanged.
    const limbo = signIn.indexOf("answer?.signup_eligible === true");
    const admit = signIn.indexOf("if (answer?.admit) return true;");
    const redirect = signIn.indexOf("/signin?error=${encodeURIComponent(");
    expect(admit).toBeGreaterThan(-1);
    expect(limbo).toBeGreaterThan(admit);
    expect(redirect).toBeGreaterThan(limbo);
  });
});

// ── WS-31 CP-2d · passwordless email OTP via Resend (customer_console.md) ────
//
// Source-level over `auth.ts` and the sign-in surface, the same measured reason
// (`signin.test.ts:38-45`): vitest here is node-env and `import("@/auth")` dies
// inside `next-auth` before the config can be built, so the RUNTIME fact — that
// a dark box (flag off / key absent) registers no provider and `/api/auth/
// providers` is unchanged — is pinned by its SHAPE here and by the *executed*
// gate in `src/lib/emailOtp.test.ts` (which CAN run, being framework-free). That
// the shape then behaves is the reviewer's manual gate, written down rather than
// assumed.
const form = readFileSync(new URL("./SignInForm.tsx", import.meta.url), "utf-8");

describe("the email OTP provider ships dark and rides the one session (CP-2d)", () => {
  it("registers the Resend provider ONLY under the isEmailOtpProviderReady gate", () => {
    // The gate is the single source of truth: env configured (flag `=== \"true\"`
    // AND key) AND the adapter wired (`EMAIL_OTP_ADAPTER_READY`), all tested for
    // real in emailOtp.test.ts. Here we pin that the provider push is INSIDE it
    // and that no other `Resend(` call exists to register it unconditionally —
    // an unadapter-ed email provider 500s ALL /api/auth/* (not just OTP), so the
    // gate must not be the env flag alone.
    expect(authSrc).toContain(
      "if (isEmailOtpProviderReady(process.env as EmailOtpEnv)) {",
    );
    const gate = authSrc.indexOf("isEmailOtpProviderReady(process.env");
    const register = authSrc.indexOf("Resend({");
    const nextAuth = authSrc.indexOf("NextAuth({");
    expect(gate).toBeGreaterThan(-1);
    expect(register).toBeGreaterThan(gate);
    expect(nextAuth).toBeGreaterThan(register);
    // Exactly one provider construction, and it is the gated one.
    expect(authSrc.match(/Resend\(\{/g)?.length).toBe(1);
  });

  it("uses the Auth.js email provider, never a hand-rolled credentials path", () => {
    // Passwordless (D46.3): the provider is Auth.js's Resend email provider, so
    // the code round-trip verifies ownership. A `Credentials` provider would be
    // a second, unverified door and a parallel path — forbidden.
    expect(authSrc).toContain('from "next-auth/providers/resend"');
    expect(authSrc).not.toMatch(/\bCredentials\b/);
  });

  it("is NUMERIC OTP, not the default magic link (owner's ask)", () => {
    // The two overrides that turn the built-in magic-link Resend provider into a
    // 6-digit code the person types.
    expect(authSrc).toContain("generateVerificationToken: generateOtp");
    expect(authSrc).toContain("sendVerificationRequest:");
  });

  it("rides the ONE NextAuth session — no second config, no database strategy swap", () => {
    // One session idiom. A second `NextAuth(` or a `strategy: \"database\"`
    // introduced here would be a parallel auth path.
    expect(authSrc.match(/NextAuth\(\{/g)?.length).toBe(1);
    expect(authSrc).not.toContain('strategy: "database"');
  });

  it("takes the verified email from Auth.js, never from request input (R11)", () => {
    // In the send, the recipient is Auth.js's resolved `identifier`; in the jwt
    // callback, the session email falls back to the verified `user.email`. There
    // is no request body reached for either.
    expect(authSrc).toContain("to: identifier");
    expect(authSrc).toContain("token.email = user.email");
    const jwt = callbackBody(authSrc, "jwt");
    for (const forbidden of ["searchParams", "req.body", "request.body"]) {
      expect(jwt).not.toContain(forbidden);
    }
  });

  it("keeps the Resend key out of the browser tier — the surface derives from the seam", () => {
    // page.tsx derives buttons from `configuredProviders`; neither it nor the
    // client form may read the flag or the key directly (the parallel-env-read
    // defect `signin.test.ts` already fences for the OAuth providers).
    expect(page).not.toContain("RESEND_API_KEY");
    expect(page).not.toContain("EMAIL_OTP_ENABLED");
    expect(form).not.toContain("RESEND_API_KEY");
    expect(form).not.toContain("EMAIL_OTP_ENABLED");
    expect(form).not.toContain("process.env");
  });

  it("renders the email field only for the email-kind provider, through the shared Input", () => {
    // The surface reuses the ONE provider-rendering seam: an email-kind entry
    // draws a field and calls `signIn(id, { email })`; an OAuth entry stays a
    // one-click button. The field is the themed `Input` primitive, not a bare
    // hand-rolled control (DESIGN_SYSTEM rule 3).
    expect(form).toContain('p.kind === "email"');
    expect(form).toMatch(/signIn\(p\.id,\s*\{\s*email/);
    expect(form).toContain('from "@/components/ui/Input"');
  });
});
