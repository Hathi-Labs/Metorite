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
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { authPosture, configuredProviders } from "@/authPosture";

import { signInErrorMessage } from "./errorCopy";

const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf-8");

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
});
