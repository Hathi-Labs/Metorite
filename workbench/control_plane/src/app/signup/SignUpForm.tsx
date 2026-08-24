"use client";

import { signIn, useSession } from "next-auth/react";
import { useState } from "react";

import type { ConfiguredProvider } from "@/authPosture";
import Button from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import { RESERVED_LABELS, SLUG_RE, suggestSlug } from "@/lib/subdomain";

import { signInErrorMessage } from "../signin/errorCopy";

/**
 * India's GST-registered states and union territories, keyed by the two-letter
 * code the Console org row stores as `billing_state` (slice 2's fixtures use
 * `"KA"`).
 *
 * AGENT-PROPOSED DEFAULT (D16/D17 — the owner may overrule the exact codes and
 * list): the gateway (`signup.py`) is the fence for the value; this Select is
 * only the affordance, so a two-letter code is the pragmatic wire form. Kept a
 * plain data table, not chrome — no colour, no theme decision.
 */
const REGISTERED_STATES: readonly { code: string; name: string }[] = [
  { code: "AN", name: "Andaman and Nicobar Islands" },
  { code: "AP", name: "Andhra Pradesh" },
  { code: "AR", name: "Arunachal Pradesh" },
  { code: "AS", name: "Assam" },
  { code: "BR", name: "Bihar" },
  { code: "CH", name: "Chandigarh" },
  { code: "CT", name: "Chhattisgarh" },
  { code: "DN", name: "Dadra and Nagar Haveli and Daman and Diu" },
  { code: "DL", name: "Delhi" },
  { code: "GA", name: "Goa" },
  { code: "GJ", name: "Gujarat" },
  { code: "HR", name: "Haryana" },
  { code: "HP", name: "Himachal Pradesh" },
  { code: "JK", name: "Jammu and Kashmir" },
  { code: "JH", name: "Jharkhand" },
  { code: "KA", name: "Karnataka" },
  { code: "KL", name: "Kerala" },
  { code: "LA", name: "Ladakh" },
  { code: "LD", name: "Lakshadweep" },
  { code: "MP", name: "Madhya Pradesh" },
  { code: "MH", name: "Maharashtra" },
  { code: "MN", name: "Manipur" },
  { code: "ML", name: "Meghalaya" },
  { code: "MZ", name: "Mizoram" },
  { code: "NL", name: "Nagaland" },
  { code: "OD", name: "Odisha" },
  { code: "PY", name: "Puducherry" },
  { code: "PB", name: "Punjab" },
  { code: "RJ", name: "Rajasthan" },
  { code: "SK", name: "Sikkim" },
  { code: "TN", name: "Tamil Nadu" },
  { code: "TG", name: "Telangana" },
  { code: "TR", name: "Tripura" },
  { code: "UP", name: "Uttar Pradesh" },
  { code: "UK", name: "Uttarakhand" },
  { code: "WB", name: "West Bengal" },
];

// Client-side MIRROR of the gateway's GSTIN shape (advisory UX only —
// `signup.py`'s `_GSTIN_RE` is the real fence). Kept byte-identical so a typo is
// caught before the round-trip, never to REPLACE the server check.
const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

/**
 * The slug vocabulary — **both halves imported, neither mirrored** (repair round
 * 1, 2026-08-24). `@/lib/subdomain` owns the shape (`SLUG_RE`) *and* the
 * reserved set (`RESERVED_LABELS`, owner ruling B7); `proxy.ts`'s host parser
 * reads the same two, and `tests/unit/test_subdomain_host_vocabulary.py` pins
 * them to `signup.py`'s Python twins.
 *
 * ⚠️ This file used to re-declare `SLUG_RE` as a hand-copied literal. It was
 * byte-identical on the day it was written, which is the only day a copy ever
 * is — `workbench/control_plane/AGENTS.md` rule 5's reason: *a mirror goes stale
 * and then lies.* Both imports are advisory UX; `signup.py`'s
 * `InvalidSlug`/`ReservedSlug` refusals are the fence.
 */
const RESERVED = new Set<string>(RESERVED_LABELS);

/**
 * The onboarding stepper — narration, not navigation (owner directive
 * 2026-08-24). The steps are already REAL: email verification happens at
 * sign-in (Google attests it, or the OTP round-trip proves it) before this
 * page is reachable, and step 3 is the post-create redirect with the
 * WelcomeDialog. What was missing was the page SAYING so — a founder landing
 * here had no way to know their email was already verified or what happens
 * after the button.
 */
function Stepper({ email }: { email: string | null }) {
  const steps: { label: string; sub: string | null; state: "done" | "current" | "next" }[] = [
    { label: "Verify your email", sub: email, state: "done" },
    { label: "Your company", sub: null, state: "current" },
    { label: "Start using Metorite", sub: null, state: "next" },
  ];
  return (
    <ol className="mb-6 flex items-start justify-center gap-0">
      {steps.map((s, i) => (
        <li key={s.label} className="flex flex-1 flex-col items-center text-center">
          <div className="flex w-full items-center">
            <div
              className={`h-px flex-1 ${i === 0 ? "bg-transparent" : "bg-border"}`}
            />
            <div
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                s.state === "done"
                  ? "bg-primary text-primary-foreground"
                  : s.state === "current"
                    ? "border-2 border-primary text-primary"
                    : "border border-border text-muted-foreground"
              }`}
            >
              {s.state === "done" ? "✓" : i + 1}
            </div>
            <div
              className={`h-px flex-1 ${i === steps.length - 1 ? "bg-transparent" : "bg-border"}`}
            />
          </div>
          <div
            className={`mt-2 text-xs font-medium ${
              s.state === "next" ? "text-muted-foreground" : "text-foreground"
            }`}
          >
            {s.label}
          </div>
          {s.sub && (
            <div className="mt-0.5 max-w-[12rem] truncate text-xs text-muted-foreground">
              {s.sub}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

export default function SignUpForm({
  providers,
}: {
  providers: ConfiguredProvider[];
}) {
  const { data: session } = useSession();
  const [displayName, setDisplayName] = useState("");
  const [slug, setSlug] = useState("");
  // The address follows the company name until the founder edits it by hand —
  // clearing the field hands it back to the suggestion (owner feedback
  // 2026-08-24: the empty field made people guess what an "address" is).
  const [slugTouched, setSlugTouched] = useState(false);
  const [state, setState] = useState("");
  const [gstin, setGstin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsSignIn, setNeedsSignIn] = useState(false);
  const [pending, setPending] = useState(false);
  const [providerPending, setProviderPending] = useState<string | null>(null);

  const trimmedSlug = slug.trim();
  const trimmedGstin = gstin.trim().toUpperCase();
  const slugShapeOk = SLUG_RE.test(trimmedSlug);
  const slugReserved = RESERVED.has(trimmedSlug.toLowerCase());
  const slugOk = slugShapeOk && !slugReserved;
  const gstinOk = trimmedGstin === "" || GSTIN_RE.test(trimmedGstin);
  const canSubmit =
    displayName.trim() !== "" && slugOk && state !== "" && gstinOk && !pending;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      // POSTs to the NEXT hop, never the gateway directly: the gateway's
      // provision route is BFF-internal and session-email-only, so the hop is
      // what attaches the acting identity. The body carries only these four
      // fields — the owner is the session email, added server-side (R11).
      const res = await fetch("/api/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: trimmedSlug,
          display_name: displayName.trim(),
          registered_state: state,
          gstin: trimmedGstin,
        }),
      });

      // No session behind the form (e.g. reached directly) — the hop answers
      // 401. Offer the configured providers to sign in first, then return here.
      if (res.status === 401) {
        setNeedsSignIn(true);
        return;
      }

      const data: { admit?: boolean; code?: string | null } = await res
        .json()
        .catch(() => ({}));

      if (res.ok && data.admit) {
        // Owner on both planes; the tenant `app_user` admits them into the app
        // even while the resolve flag is off (the flow works dark). A full
        // navigation re-runs the auth path against the freshly created org.
        // `?welcome=new-org` arms the one-time WelcomeDialog — step 3 of the
        // onboarding narration ("you're in; here's where you add your team").
        window.location.assign("/?welcome=new-org");
        return;
      }

      // A 200 outcome refusal or a 4xx/5xx shape error — both carry a `code`,
      // rendered through the ONE errorCopy seam (SignupDisabled / AlreadyMember
      // / SlugTaken and the reused ConsoleUnavailable).
      setError(
        signInErrorMessage(
          typeof data.code === "string" ? data.code : "ConsoleUnavailable",
        ),
      );
    } catch {
      setError(signInErrorMessage("ConsoleUnavailable"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-10">
      <div className="w-full max-w-md">
        <Stepper email={session?.user?.email ?? null} />
        <div className="rounded-lg border border-border bg-card p-8">
        <h1 className="text-center text-xl font-semibold">
          Create a new organization
        </h1>
        <p className="mt-2 text-center text-sm text-muted-foreground">
          A brand-new workspace on Metorite, with you as its owner.
        </p>
        {/* D51 / WS-35 — the fork made explicit at the door. This page CREATES
            an organization; a person whose company already uses Metorite must
            not end up here thinking it is how you join one. */}
        <div className="mt-4 rounded-md border border-border bg-secondary px-4 py-3 text-sm text-muted-foreground">
          Does your company already use Metorite? Don&apos;t create a
          duplicate — ask your organization&apos;s admin to invite your email
          address, then simply sign in.
        </div>

        {error && (
          <div className="mt-4 rounded-md border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {needsSignIn ? (
          <div className="mt-6 flex flex-col gap-2">
            <p className="text-center text-sm text-muted-foreground">
              Sign in with your work account to create an organization.
            </p>
            {providers.length === 0 ? (
              <p className="text-center text-sm text-muted-foreground">
                No sign-in provider is configured for this deployment.
              </p>
            ) : (
              providers.map((p) => (
                <Button
                  key={p.id}
                  size="lg"
                  className="w-full"
                  loading={providerPending === p.id}
                  disabled={providerPending !== null}
                  onClick={() => {
                    setProviderPending(p.id);
                    signIn(p.id, { callbackUrl: "/signup" });
                  }}
                >
                  {p.label}
                </Button>
              ))
            )}
          </div>
        ) : (
          <form className="mt-6 flex flex-col gap-4" onSubmit={onSubmit}>
            <label className="flex flex-col gap-1 text-left">
              <span className="text-xs font-medium text-muted-foreground">
                Organization name
              </span>
              <Input
                inputSize="lg"
                value={displayName}
                onChange={(e) => {
                  setDisplayName(e.target.value);
                  if (!slugTouched) setSlug(suggestSlug(e.target.value));
                }}
                placeholder="Acme Inc"
                autoComplete="organization"
              />
            </label>

            <label className="flex flex-col gap-1 text-left">
              <span className="text-xs font-medium text-muted-foreground">
                Workspace address
              </span>
              <Input
                inputSize="lg"
                icon="Globe"
                value={slug}
                onChange={(e) => {
                  setSlug(e.target.value);
                  // Typing marks the field hand-edited; emptying it un-marks,
                  // so the suggestion resumes rather than leaving a hole.
                  setSlugTouched(e.target.value !== "");
                }}
                placeholder="acme"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
              {trimmedSlug !== "" && !slugShapeOk && (
                <span className="text-xs text-destructive">
                  Lowercase letters, numbers and hyphens only — no leading or
                  trailing hyphen, up to 63 characters.
                </span>
              )}
              {trimmedSlug !== "" && slugShapeOk && slugReserved && (
                // A reserved label is well-formed, so the shape message above
                // would be a lie. Two causes, two sentences — the same split
                // the route makes between `InvalidSlug` and `ReservedSlug`.
                <span className="text-xs text-destructive">
                  That address is reserved. Please choose a different one.
                </span>
              )}
              {(trimmedSlug === "" || slugOk) && (
                // The standing explanation, not an error: this field used to
                // sit silent until you typed something invalid, and a single
                // typed letter is valid — so nothing ever told the founder
                // what an "address" is (owner feedback, 2026-08-24).
                <span className="text-xs text-muted-foreground">
                  Your organization&apos;s short, permanent ID on Metorite —
                  filled in from your company name, e.g.{" "}
                  <span className="font-medium">acme</span> for Acme Inc.
                </span>
              )}
            </label>

            <label className="flex flex-col gap-1 text-left">
              <span className="text-xs font-medium text-muted-foreground">
                Registered state
              </span>
              <Select
                inputSize="lg"
                value={state}
                onChange={(e) => setState(e.target.value)}
                required
              >
                <option value="" disabled>
                  Select a state…
                </option>
                {REGISTERED_STATES.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </label>

            <label className="flex flex-col gap-1 text-left">
              <span className="text-xs font-medium text-muted-foreground">
                GSTIN <span className="opacity-70">(optional)</span>
              </span>
              <Input
                inputSize="lg"
                value={gstin}
                onChange={(e) => setGstin(e.target.value)}
                placeholder="27AAPFU0939F1ZV"
                autoCapitalize="characters"
                autoCorrect="off"
                spellCheck={false}
              />
              {trimmedGstin !== "" && !gstinOk && (
                <span className="text-xs text-destructive">
                  That GSTIN does not look valid.
                </span>
              )}
            </label>

            <Button
              type="submit"
              size="lg"
              className="w-full"
              loading={pending}
              disabled={!canSubmit}
            >
              Create new organization
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              Next: you land in your new workspace, ready to invite your team.
            </p>
          </form>
        )}
        </div>
      </div>
    </div>
  );
}
