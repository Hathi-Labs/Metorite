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

describe("a signed-out visitor is sent to /signin, not to a dead form (8a)", () => {
  it("resolves the session server-side and redirects when there is none", () => {
    // The owner of the new organization is the SESSION email (R11), and the
    // `/api/signup` hop 401s without one — so rendering the four-field form to
    // a signed-out visitor asked them to name an organization, a slug, a state
    // and a GSTIN before telling them the only thing that mattered.
    expect(page).toContain('import { currentIdentity } from "@/lib/gateway"');
    expect(page).toMatch(/if \(!\(await currentIdentity\(\)\)\) redirect\("\/signin"\);/);
    // A server component that awaits must be async, or the check is a promise
    // and `!promise` is always false — a guard that reads correct and passes
    // everybody.
    expect(page).toMatch(/export default async function SignUp\(\)/);
  });

  it("uses the ONE identity seam, not a second session read", () => {
    // `currentIdentity()` is what the hop's `requireIdentity()` sits on, so
    // "may this render" and "will the submit work" cannot drift — and it
    // carries the laptop bypass, so an unconfigured dev box is unchanged.
    expect(page).not.toContain('from "next-auth"');
    expect(page).not.toMatch(/await auth\(\)/);
  });

  it("checks the FLAG before the SESSION", () => {
    // An un-opted-in deployment must not disclose that this surface exists
    // behind a sign-in. Both gates land on /signin; only one of them may be
    // reached by somebody who has not signed in.
    const flag = page.indexOf("SELF_SERVE_SIGNUP_ENABLED");
    const session = page.indexOf("currentIdentity()", page.indexOf("export default"));
    expect(flag).toBeGreaterThan(-1);
    expect(session).toBeGreaterThan(flag);
  });

  it("keeps the form's own needsSignIn arm, which answers a DIFFERENT case", () => {
    // A session that expired between render and submit is invisible to a
    // server-component check. Deleting the arm would turn that into a silent
    // failure at the one button on the screen.
    expect(form).toContain("setNeedsSignIn(true)");
    expect(form).toContain("res.status === 401");
  });
});

describe("the form renders outcome codes through the ONE errorCopy seam", () => {
  it("imports signInErrorMessage from the signin errorCopy module, never a copy", () => {
    expect(form).toMatch(
      /import \{ signInErrorMessage \} from "\.\.\/signin\/errorCopy"/,
    );
    expect(form).toContain("signInErrorMessage(");
  });

  it("errorCopy speaks the four CP-2c signup codes, and D33.1-safely", () => {
    const disabled = signInErrorMessage("SignupDisabled");
    const already = signInErrorMessage("AlreadyMember");
    const taken = signInErrorMessage("SlugTaken");
    const reserved = signInErrorMessage("ReservedSlug");

    expect(disabled).toBeTruthy();
    expect(already).toBeTruthy();
    expect(taken).toBeTruthy();
    expect(reserved).toBeTruthy();

    // D33.1: none blames the person for a state they did not create — no
    // reused Auth.js `AccessDenied` phrasing.
    for (const copy of [disabled, already, taken, reserved]) {
      expect(copy).not.toMatch(/access denied|isn't authorized/i);
    }

    // Each names its own cause, distinguishable to a reader.
    expect(disabled).toMatch(/not available|invitation|administrator/i);
    expect(already).toMatch(/already belong|sign in/i);
    expect(taken).toMatch(/already taken|different/i);
    expect(reserved).toMatch(/reserved/i);

    // SlugTaken names nothing beyond "unavailable" — no owner, no cross-tenant
    // oracle (§6 CP-2c item 5).
    expect(taken).not.toMatch(/owned by|belongs to|held by/i);

    // ⚠️ ReservedSlug and SlugTaken must NOT collapse into one string. They are
    // different facts — one is a static platform rule, the other is about an
    // organization that exists — and only the first may be said out loud.
    // Merging them would either leak "taken" as "reserved" or teach a customer
    // that `api` is somebody's workspace (WS-29 MT-1f, owner ruling B7).
    expect(reserved).not.toBe(taken);
    expect(reserved).not.toMatch(/taken|owned by|belongs to/i);
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
