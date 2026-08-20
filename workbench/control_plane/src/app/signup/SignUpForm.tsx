"use client";

import { signIn } from "next-auth/react";
import { useState } from "react";

import type { ConfiguredProvider } from "@/authPosture";
import Button from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";

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

// Client-side MIRRORS of the gateway's shapes (advisory UX only — `signup.py`
// `_SLUG_RE`:124 / `_GSTIN_RE`:116 is the real fence). Kept byte-identical so a
// typo is caught before the round-trip, never to REPLACE the server check.
const SLUG_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

export default function SignUpForm({
  providers,
}: {
  providers: ConfiguredProvider[];
}) {
  const [displayName, setDisplayName] = useState("");
  const [slug, setSlug] = useState("");
  const [state, setState] = useState("");
  const [gstin, setGstin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsSignIn, setNeedsSignIn] = useState(false);
  const [pending, setPending] = useState(false);
  const [providerPending, setProviderPending] = useState<string | null>(null);

  const trimmedSlug = slug.trim();
  const trimmedGstin = gstin.trim().toUpperCase();
  const slugOk = SLUG_RE.test(trimmedSlug);
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
        window.location.assign("/");
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
    <div className="flex min-h-screen items-center justify-center p-10">
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-8">
        <h1 className="text-center text-xl font-semibold">
          Create your organization
        </h1>
        <p className="mt-2 text-center text-sm text-muted-foreground">
          Set up a new workspace on Metorite.
        </p>

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
                onChange={(e) => setDisplayName(e.target.value)}
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
                onChange={(e) => setSlug(e.target.value)}
                placeholder="acme"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
              {trimmedSlug !== "" && !slugOk && (
                <span className="text-xs text-destructive">
                  Lowercase letters, numbers and hyphens only — no leading or
                  trailing hyphen, up to 63 characters.
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
              Create organization
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
