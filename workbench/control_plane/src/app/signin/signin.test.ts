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

  it("is inert until CUSTOMER_CONSOLE_URL is configured", () => {
    // Ships dark. The gate is read FIRST, before anything else in the
    // callback, so an unwired deployment signs people in with no fetch, no
    // latency and no new failure mode — byte-identical to the behaviour
    // before CP-2b. Wiring a live box is OWNER-GATE (§8 gate 7).
    const signIn = callbackBody(authSrc, "signIn");
    const gate = signIn.indexOf("process.env.CUSTOMER_CONSOLE_URL");
    const call = signIn.indexOf("/signin/resolve");
    expect(gate).toBeGreaterThan(-1);
    expect(gate).toBeLessThan(call);
    expect(signIn).toMatch(/if \(!process\.env\.CUSTOMER_CONSOLE_URL\) return true;/);
  });

  it("refuses with the two codes and never with a bare false", () => {
    const signIn = callbackBody(authSrc, "signIn");
    // Returning `false` yields Auth.js's `AccessDenied` and nothing else,
    // which is the copy D33.1 forbids for both of these cases. The string
    // return is the mechanism that carries a distinguishable reason.
    expect(signIn).toContain('"/signin?error=ConsoleUnavailable"');
    expect(signIn).toContain("/signin?error=${answer?.code");
    expect(signIn).not.toMatch(/\breturn false\b/);
  });
});
