/**
 * Fences for the self-serve `/signup` surface (CP-2c slice 4, done-when 1; R7).
 *
 * Source-level pins in `signin.test.ts`'s established style, and for the same
 * measured reason (`signin.test.ts:38-45`): vitest in this tree is node-env, and
 * importing `page.tsx` here would drag `next-auth` / `next/navigation` into a
 * node test that cannot render the page. So the two behaviours no node test in
 * this tree can execute — the flag-off REDIRECT and the flag-on RENDER — are
 * pinned by their SHAPE, exactly as the CP-2b resolve hop above them is.
 *
 * What no test in this tree can prove is that the shape BEHAVES — that the flag
 * being off issues no render and the redirect actually fires — so that is the
 * reviewer's manual gate (the DESIGN_SYSTEM §8 theme-switch is its cousin),
 * written down here rather than assumed. The BEHAVIOURAL POST fence lives where
 * it can RUN: `tests/unit/test_signup_provision_route.py` against the real
 * gateway route.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { signInErrorMessage } from "../signin/errorCopy";

const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf-8");
const form = readFileSync(new URL("./SignUpForm.tsx", import.meta.url), "utf-8");
const hop = readFileSync(new URL("../api/signup/route.ts", import.meta.url), "utf-8");

describe("the signup segment", () => {
  it("is dynamic — env is read per request, never baked at build", () => {
    // Statically prerendered, the flag freezes at `next build` time — the same
    // trap the signin page carries `force-dynamic` against.
    expect(page).toContain('export const dynamic = "force-dynamic"');
  });

  it("derives providers from the authPosture seam, not a parallel env read", () => {
    expect(page).toContain("configuredProviders(");
    expect(page).not.toContain("process.env.AUTH_GOOGLE_ID");
    expect(page).not.toContain("process.env.AUTH_MICROSOFT_ENTRA_ID_ID");
  });
});

describe("the flag gates the whole surface — both positions (done-when 1)", () => {
  it('redirects to /signin when SELF_SERVE_SIGNUP_ENABLED is not exactly "true"', () => {
    // Ships dark. The ruling is a REDIRECT, not a 404 (audit B4a): an
    // un-opted-in deployment sends the caller to /signin, it does not pretend
    // the route is absent. `=== "true"` EXACTLY (auth.ts:163's idiom), never
    // truthiness — an operator who writes `SELF_SERVE_SIGNUP_ENABLED=false`
    // while debugging must get OFF, and every truthy-string reading arms it.
    expect(page).toContain('import { redirect } from "next/navigation"');
    expect(page).toMatch(
      /if \(process\.env\.SELF_SERVE_SIGNUP_ENABLED !== "true"\) redirect\("\/signin"\);/,
    );
  });

  it('renders the form when the flag is "true", and the gate is read FIRST', () => {
    // The on-position: the page renders the client form. The gate is read
    // before both the redirect it drives and the render it guards, so a box
    // that has not opted in never reaches the form.
    expect(page).toMatch(/<SignUpForm\b/);
    const gate = page.indexOf("SELF_SERVE_SIGNUP_ENABLED");
    const redirect = page.indexOf('redirect("/signin")');
    const render = page.indexOf("<SignUpForm");
    expect(gate).toBeGreaterThan(-1);
    expect(gate).toBeLessThan(redirect);
    expect(redirect).toBeLessThan(render);
  });
});

describe("the form renders outcome codes through the ONE errorCopy seam", () => {
  it("imports signInErrorMessage from the signin errorCopy module, never a copy", () => {
    expect(form).toMatch(
      /import \{ signInErrorMessage \} from "\.\.\/signin\/errorCopy"/,
    );
    expect(form).toContain("signInErrorMessage(");
  });

  it("errorCopy speaks the three CP-2c signup codes, and D33.1-safely", () => {
    const disabled = signInErrorMessage("SignupDisabled");
    const already = signInErrorMessage("AlreadyMember");
    const taken = signInErrorMessage("SlugTaken");

    expect(disabled).toBeTruthy();
    expect(already).toBeTruthy();
    expect(taken).toBeTruthy();

    // D33.1: none blames the person for a state they did not create — no
    // reused Auth.js `AccessDenied` phrasing.
    for (const copy of [disabled, already, taken]) {
      expect(copy).not.toMatch(/access denied|isn't authorized/i);
    }

    // Each names its own cause, distinguishable to a reader.
    expect(disabled).toMatch(/not available|invitation|administrator/i);
    expect(already).toMatch(/already belong|sign in/i);
    expect(taken).toMatch(/already taken|different/i);

    // SlugTaken names nothing beyond "unavailable" — no owner, no cross-tenant
    // oracle (§6 CP-2c item 5).
    expect(taken).not.toMatch(/owned by|belongs to|held by/i);
  });
});

describe("the /api/signup Next hop is the one door to the gateway (R11)", () => {
  it("posts the four signup fields to the gateway provision route", () => {
    expect(hop).toContain("/signup/provision");
    for (const key of ["slug", "display_name", "registered_state", "gstin"]) {
      expect(hop).toContain(key);
    }
  });

  it("forwards NONE of the tenant/identity claims a caller must not assert", () => {
    // R11. The owner is the SESSION email; the deployment is the box's own. A
    // body email/org/deployment_label is what the gateway 400s as InvalidBody
    // (signup.py:111), and this door does not relay any of them into the
    // forwarded object — the identity comes from the session, server-side.
    const forward = hop.slice(
      hop.indexOf("const forward"),
      hop.indexOf("fetch("),
    );
    expect(forward.length).toBeGreaterThan(0);
    for (const forbidden of ["email", '"org"', "deployment_label"]) {
      expect(forward).not.toContain(forbidden);
    }
  });

  it("derives identity server-side and mints no bearer of its own", () => {
    // The hop resolves the signed-in member (401 when nobody is) and attaches
    // the internal bearer through the single door (lib/gateway.ts). It never
    // reads GATEWAY_INTERNAL_TOKEN itself — gateway.test.ts fences that too.
    expect(hop).toContain("requireIdentity(");
    expect(hop).toContain("gatewayHeaders(");
    expect(hop).not.toContain("GATEWAY_INTERNAL_TOKEN");
  });
});
