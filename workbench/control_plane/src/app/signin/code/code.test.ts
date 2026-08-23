/**
 * Fences for the numeric-code entry page (CP-2d slice 2, clause 14).
 *
 * Source-level, the measured reason `signin.test.ts:38-45` records: vitest here
 * is node-env, so a page cannot be rendered and `import("@/auth")` dies inside
 * next-auth. What CAN be pinned is the wire contract — which URL the typed code
 * is submitted to, under which parameter names, and that the page is reachable
 * by somebody who is not signed in. Every one of those is a silent failure when
 * wrong: the form still renders, the button still clicks, and nothing verifies.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { EMAIL_OTP_PROVIDER_ID, OTP_EMAIL_STORAGE_KEY } from "@/lib/emailOtp";

const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf-8");
const form = readFileSync(new URL("./CodeForm.tsx", import.meta.url), "utf-8");
const proxy = readFileSync(
  new URL("../../../proxy.ts", import.meta.url),
  "utf-8",
);
const signInForm = readFileSync(
  new URL("../SignInForm.tsx", import.meta.url),
  "utf-8",
);

describe("the code page submits to Auth.js's own callback", () => {
  it("posts the typed code to /api/auth/callback/<provider> as a GET", () => {
    // `@auth/core` verifies at
    // `GET /api/auth/callback/<provider>?token=…&email=…` — byte-for-byte the
    // URL `lib/actions/signin/send-token.js` puts in a magic link. A native GET
    // form produces exactly that navigation, so the session cookie and the
    // post-sign-in redirect are the browser's job rather than this page's.
    expect(form).toContain(
      "action={`/api/auth/callback/${EMAIL_OTP_PROVIDER_ID}`}",
    );
    expect(form).toContain('method="get"');
    // The provider id is IMPORTED from the one seam, never transcribed — a
    // literal here would keep passing after the provider was renamed.
    expect(form).toContain('from "@/lib/emailOtp"');
    expect(EMAIL_OTP_PROVIDER_ID).toBe("resend");
  });

  it("names the two parameters @auth/core actually reads", () => {
    // `callback/index.js:132-133` reads `query.token` and `query.email`.
    // Renaming either breaks verification with no visible symptom until
    // somebody tries to sign in.
    expect(form).toContain('name="token"');
    expect(form).toContain('name="email"');
  });

  it("reads no secret and asserts no identity of its own", () => {
    // The address is user input here and that is fine — ownership is proven by
    // the code round-trip and the session email is Auth.js's verified value
    // (R11 is about what the SERVER trusts). What must never appear is a
    // credential or a gateway call.
    expect(form).not.toContain("process.env");
    expect(form).not.toContain("RESEND_API_KEY");
    expect(form).not.toContain("GATEWAY_INTERNAL_TOKEN");
    expect(form).not.toContain("headersActingAs");
  });
});

describe("the code page is reachable, and only while OTP is armed", () => {
  it("is in proxy.ts's PUBLIC_PAGES", () => {
    // Leaving it out does not fail safe, it fails BROKEN: a person asking for a
    // sign-in code is by definition not signed in, so the proxy would bounce
    // them back to /signin and the code could never be entered.
    expect(proxy).toContain('"/signin/code"');
  });

  it("redirects to /signin when the feature is dark — not a 404", () => {
    // The same ruling CP-2c slice 4 made for `/signup`: a redirect to the page
    // that can actually sign you in, rather than a not-found on a route that
    // demonstrably exists in the bundle.
    expect(page).toContain("isEmailOtpProviderReady(process.env as EmailOtpEnv)");
    expect(page).toContain('redirect("/signin")');
    expect(page).not.toContain("notFound(");
    // Read per request, never baked at build — the `signin/page.tsx` lesson.
    expect(page).toContain('export const dynamic = "force-dynamic"');
  });

  it("the address is handed over by the sign-in form, through the one key", () => {
    // Auth.js's verify-request redirect carries `provider` and `type` and NOT
    // the address, so without this hand-off the page has nothing to prefill.
    // Both sides import the key rather than spelling it, which is the only way
    // a rename stays a rename.
    expect(signInForm).toContain("OTP_EMAIL_STORAGE_KEY");
    expect(form).toContain("OTP_EMAIL_STORAGE_KEY");
    expect(OTP_EMAIL_STORAGE_KEY).toBe("cc-otp-email");
    // …and it is read defensively: storage can be unavailable, and a missing
    // value must cost a retype rather than a dead end.
    expect(form).toContain("window.sessionStorage.getItem(");
    expect(form).toMatch(/} catch \{/);
  });
});
