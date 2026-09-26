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
  it('refuses the surface when SELF_SERVE_SIGNUP_ENABLED is not exactly "true"', () => {
    // Ships dark. `=== "true"` EXACTLY (auth.ts:163's idiom), never truthiness
    // — an operator who writes `SELF_SERVE_SIGNUP_ENABLED=false` while
    // debugging must get OFF, and every truthy-string reading arms it.
    //
    // ⚠️ **The off-position answer CHANGED 2026-09-15: an explanation, not a
    // redirect to /signin.** The redirect was a silent dead end, and a reachable
    // one — `AccessGate`'s org-less card offers every org-less member a "Create
    // a new organization" button that knows nothing about this flag, so on
    // today's default (unset) that button landed them back on a sign-in form
    // they had already passed, with nothing said.
    //
    // The signed-out guarantee is UNCHANGED, and is pinned below: the session
    // check now runs first, so a stranger never reaches this line at all.
    expect(page).toMatch(
      /if \(process\.env\.SELF_SERVE_SIGNUP_ENABLED !== "true"\) return <SignupOff \/>;/,
    );
  });

  it("says WHY it is off, in the same words as the SignupDisabled code", () => {
    // One refusal, one phrasing. A second sentence for the same fact is how two
    // surfaces come to disagree about what the deployment is doing.
    expect(page).toContain('signInErrorMessage("SignupDisabled")');
    expect(page).toContain('from "../signin/errorCopy"');
    // And it must not re-spell that copy locally — the mirror this reuse avoids.
    expect(page).not.toContain("Self-serve signup is not available on this");
  });

  it('renders the form when the flag is "true", and the gate is read BEFORE it', () => {
    // The on-position: the page renders the client form, and only after the
    // gate has been consulted — so a box that has not opted in never reaches it.
    expect(page).toMatch(/<SignUpForm\b/);
    const gate = page.indexOf("process.env.SELF_SERVE_SIGNUP_ENABLED");
    const render = page.indexOf("<SignUpForm providers");
    expect(gate).toBeGreaterThan(-1);
    expect(render).toBeGreaterThan(-1);
    expect(gate).toBeLessThan(render);
  });
});

describe("a signed-out visitor is sent to /signin, not to a dead form (8a)", () => {
  it("resolves the session server-side and redirects when there is none", () => {
    // The owner of the new organization is the SESSION email (R11), and the
    // `/api/signup` hop 401s without one. ⚠️ DEFENCE IN DEPTH, stated
    // accurately since 2026-08-24: `/signup` is not in `proxy.ts`'s
    // `PUBLIC_PAGES`, so the proxy already redirects a signed-out navigation —
    // this check is what stops that guarantee from living in another file's
    // set, which is one line away from changing.
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

  it("checks the SESSION before the FLAG, and still tells a stranger nothing", () => {
    // ⚠️ **REVERSED 2026-09-15, and the property the old order protected is
    // intact.** It used to read flag-first, so "an un-opted-in deployment must
    // not disclose that this surface exists behind a sign-in".
    //
    // A signed-out visitor still learns nothing, and for a STRONGER reason than
    // before: they are redirected at the session check, which now comes first,
    // so the flag's off-position page is unreachable without a session. What
    // changed is only what a SIGNED-IN person gets — a sentence instead of a
    // loop back to the page they just came from. The disclosure argument never
    // applied to them: they are already inside, and the button that sent them
    // here already told them the surface exists.
    const session = page.indexOf(
      "currentIdentity()",
      page.indexOf("export default"),
    );
    const flag = page.indexOf("process.env.SELF_SERVE_SIGNUP_ENABLED");
    expect(session).toBeGreaterThan(-1);
    expect(flag).toBeGreaterThan(session);
    // The off-position page is a RENDER, never a redirect — a second
    // `redirect("/signin")` after the session check would rebuild the loop.
    expect(page).not.toMatch(
      /SELF_SERVE_SIGNUP_ENABLED !== "true"\) redirect/,
    );
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
    for (const key of [
      "slug",
      "display_name",
      "registered_state",
      "gstin",
      "team_size",
    ]) {
      expect(hop).toContain(key);
    }
  });

  it("relays team_size UNCOERCED — the gateway owns the range", () => {
    // A second opinion in this hop is a second fence that can drift from the
    // real one (`signup.py`'s `_team_size`). So the value passes through as the
    // caller sent it, and an absent field stays absent so the gateway's own
    // default applies.
    expect(hop).toMatch(/team_size: body\.team_size,/);
    expect(hop).not.toContain("Number(body.team_size)");
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

describe("the form ASKS how many people, instead of silently meaning one", () => {
  // ⚠️ The defect this closes. `_post_provision` never sent `core_seats`, so
  // the Console applied its default of 1. The founder took that seat at their
  // own first resolve. The WelcomeDialog then said "invite your team", the
  // colleague's first sign-in hit `_allocate_core_seat`'s cap 409, and
  // `resolve_for_signin` rendered it as *"ask your admin for an invite"* — to
  // the admin who had just invited them. A dead end with friendly copy.

  it("carries a team-size field and submits it as a number", () => {
    expect(form).toContain("How many people will use Metorite?");
    expect(form).toMatch(/team_size: Number\(teamSize\.trim\(\)\),/);
  });

  it("defaults to ONE, so a solo founder buys nothing extra", () => {
    // The floor was never wrong — only the silence was. A default above 1 would
    // hand every solo signup seats they did not ask for.
    expect(form).toMatch(/useState\("1"\)/);
  });

  it("blocks submit on a team size the gateway would refuse", () => {
    // Advisory UX in front of the real fence, exactly as GSTIN_RE sits in front
    // of `_GSTIN_RE`: a typo is caught before the round trip, never instead of
    // the server check.
    expect(form).toContain("teamSizeOk");
    expect(form).toMatch(/teamSizeOk &&/);
    expect(form).toContain("MAX_TEAM_SIZE");
  });

  it("states the bound as a constant, not a literal buried in the markup", () => {
    // It is a MIRROR of `signup.py`'s `MAX_TEAM_SIZE`, and naming it is what
    // lets a reader find the fence it mirrors.
    expect(form).toMatch(/const MAX_TEAM_SIZE = 10;/);
  });
});

describe("a signed-in visitor can change the address", () => {
  // 🔴 Measured 2026-09-26: the owner was signed in with an address that
  // already owned an organization, and this page offered no way to change it.
  it("offers a sign-out that returns to this page", () => {
    expect(form).toContain("Use a different email");
    expect(form).toMatch(/signOut\(\{\s*callbackUrl:\s*"\/signup"\s*\}\)/);
  });
});

describe("the AlreadyMember refusal names the address and the organization", () => {
  it("says which email is linked to which organization, and what to do", async () => {
    const { alreadyMemberMessage } = await import("./SignUpForm");
    const m = alreadyMemberMessage("a@hathilabs.com", "Hathi Labs LLP");
    expect(m).toContain("a@hathilabs.com");
    expect(m).toContain("Hathi Labs LLP");
    expect(m).toContain("use a different email");
  });

  it("still reads well with neither value", async () => {
    const { alreadyMemberMessage } = await import("./SignUpForm");
    expect(alreadyMemberMessage(null, null)).toBe(
      "This email address is already linked to another organization. One email " +
        "address can belong to only one organization. To create a new " +
        "organization, use a different email. To use the existing one, sign in.",
    );
  });

  it("is what the form shows for the code", () => {
    expect(form).toMatch(/data\.code === "AlreadyMember"[\s\S]{0,400}alreadyMemberMessage\(/);
  });
});
