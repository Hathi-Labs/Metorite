import { redirect } from "next/navigation";

import { configuredProviders, type AuthEnv } from "@/authPosture";
import { currentIdentity } from "@/lib/gateway";

import { signInErrorMessage } from "../signin/errorCopy";
import SignUpForm from "./SignUpForm";

/**
 * The self-serve signup page — the FORM over CP-2a's provision API (CP-2c
 * slice 4). A person who authenticated with the IdP but belongs to no
 * organization lands here (the `signIn` callback's zero-org limbo branch admits
 * that session and the ordinary redirect brings them where `/signup` is
 * reachable), names their organization, and this surface POSTs it — through the
 * `/api/signup` Next hop — to the gateway's `POST /signup/provision`.
 *
 * Server component on purpose, mirroring `signin/page.tsx`: the flag and the
 * provider env are server-only, and the client half (`SignUpForm`) receives the
 * derived provider list as props (it offers them when a caller reaches the form
 * without a session and the hop answers 401).
 */

// Without this the route is STATICALLY PRERENDERED and the flag freezes at
// `next build` time — an env-plus-restart flip would then never reach this
// surface. Same measured trap the signin page carries `force-dynamic` against.
// Fence: `signup.test.ts`.
export const dynamic = "force-dynamic";

/**
 * What a signed-in member sees when this deployment has not opted in.
 *
 * It replaces a redirect to `/signin` — a page they had already passed, which
 * said nothing and looked like a bug. The copy is the same sentence
 * `errorCopy`'s `SignupDisabled` gives, because it is the same fact, and a
 * second phrasing of one refusal is how two surfaces come to disagree.
 */
function SignupOff() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-10">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-8 text-center">
        <h1 className="text-base font-semibold text-foreground">
          Self-serve signup is turned off here
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {signInErrorMessage("SignupDisabled")}
        </p>
        <p className="mt-4 text-sm text-muted-foreground">
          If your company already uses Metorite, ask your organization&apos;s
          admin to invite your email address. Then sign in as usual.
        </p>
      </div>
    </div>
  );
}

export default async function SignUp() {
  // ── done-when 8a (2026-08-24) · no session ⇒ /signin, not a dead form ──────
  //
  // Identity is IdP-attested BEFORE the form (CP-2c item 1): the owner of the
  // new organization is the SESSION email, resolved server-side, and the
  // `/api/signup` hop 401s without one.
  //
  // ⚠️ **This is DEFENCE IN DEPTH, not a repair of a reachable dead end**
  // (corrected 2026-08-24 — the original comment here claimed signed-out
  // visitors were being shown the form, and they were not). `/signup` is absent
  // from `proxy.ts`'s `PUBLIC_PAGES`, so a signed-out page navigation is already
  // redirected to `/signin` before this component runs. What the check buys is
  // that the guarantee stops depending on a set in another file: add `/signup`
  // to `PUBLIC_PAGES` — a one-line edit somebody will make the day the marketing
  // CTA lands — and without this the form renders to a visitor who can only be
  // 401'd at submit.
  //
  // `currentIdentity()` and not a second session read: it is the same seam the
  // hop's `requireIdentity()` sits on, so "may this render" and "will the
  // submit work" cannot drift — and it carries the laptop bypass, so an
  // unconfigured dev box is unchanged.
  //
  // ⚠️ **REORDERED 2026-09-15 to session-first, flag-second — and the property
  // the old order protected is INTACT.** It read flag-first, so that "an
  // un-opted-in deployment must not disclose that this surface exists behind a
  // sign-in". A signed-out visitor still learns nothing: they are redirected
  // HERE, by the check below, before the flag is ever read.
  //
  // What changed is what a SIGNED-IN person gets when the flag is off. They
  // used to be redirected to `/signin` — a page they had already passed — which
  // was a silent dead end, and a reachable one: `AccessGate`'s org-less card
  // shows every org-less member a "Create a new organization" button, and that
  // button does not know about the flag. Click it on today's default (unset)
  // and you land back on a sign-in form with no explanation.
  //
  // The disclosure argument does not apply to them. They are already inside the
  // deployment, and the button that sent them here already told them the
  // surface exists. So the honest answer is a sentence, not a loop.
  //
  // `SignUpForm`'s own `needsSignIn` arm STAYS: it is the answer to a session
  // that expired between render and submit, which this check cannot see.
  if (!(await currentIdentity())) redirect("/signin");

  // Ships dark. `=== "true"` EXACTLY, not truthiness (auth.ts:163's idiom): an
  // operator who sets `SELF_SERVE_SIGNUP_ENABLED=false` while debugging must
  // get OFF, and every truthy-string reading would arm it instead. Flipping it
  // live is OWNER-GATE (§8 gate 8). Nothing is provisioned on this path — the
  // gateway reads its OWN copy of the flag and answers `SignupDisabled`, so
  // this surface refusing is belt-and-braces rather than the only fence.
  if (process.env.SELF_SERVE_SIGNUP_ENABLED !== "true") return <SignupOff />;

  return <SignUpForm providers={configuredProviders(process.env as AuthEnv)} />;
}
