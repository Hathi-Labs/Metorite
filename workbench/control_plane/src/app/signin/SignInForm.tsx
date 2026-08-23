"use client";

import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import type { ConfiguredProvider } from "@/authPosture";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { OTP_EMAIL_STORAGE_KEY, canonicalOtpIdentifier } from "@/lib/emailOtp";

import { signInErrorMessage } from "./errorCopy";

function Form({ providers }: { providers: ConfiguredProvider[] }) {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  const errorMessage = signInErrorMessage(searchParams.get("error"));
  const [pending, setPending] = useState<string | null>(null);
  // CP-2d: the address the email-OTP provider sends the code to. It is USER
  // INPUT here on purpose and that is not an R11 breach — it is not trusted as
  // identity; ownership is proven by the code round-trip, and the SESSION email
  // is Auth.js's verified value (`auth.ts`'s jwt callback), never this field.
  const [email, setEmail] = useState("");

  return (
    <div className="flex min-h-screen items-center justify-center p-10">
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-8 text-center">
        <h1 className="text-xl font-semibold">Metorite</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Sign in with your work account.
        </p>

        {errorMessage && (
          <div className="mt-4 rounded-md border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {errorMessage}
          </div>
        )}

        {providers.length === 0 ? (
          <p className="mt-6 text-sm text-muted-foreground">
            No sign-in provider is configured for this deployment.
          </p>
        ) : (
          <div className="mt-6 flex flex-col gap-2">
            {providers.map((p) =>
              p.kind === "email" ? (
                <form
                  key={p.id}
                  className="flex flex-col gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    setPending(p.id);
                    // CP-2d slice 2: hand the address to `/signin/code`, which
                    // Auth.js redirects to WITHOUT it (its verify-request
                    // redirect carries only `provider` and `type`). A
                    // convenience for the prefill and nothing more — the code
                    // round-trip is what proves ownership, so a tampered or
                    // absent value costs a retype, never a wrong sign-in.
                    //
                    // ⚠️ **Canonical, not as typed** (repair of review finding
                    // P1a). `@auth/core`'s send leg normalises the address
                    // before minting the token, and the completion leg compares
                    // the two VERBATIM — after consuming the code. Stashing
                    // `Ada@Customer.Example` therefore burned the person's code
                    // and then told them it was wrong. `canonicalOtpIdentifier`
                    // mirrors that normaliser, so the value handed on is already
                    // the one the token was minted for.
                    const canonical = canonicalOtpIdentifier(email);
                    try {
                      window.sessionStorage.setItem(
                        OTP_EMAIL_STORAGE_KEY,
                        canonical,
                      );
                    } catch {
                      // Private mode / a strict storage policy. The code page
                      // shows the field instead.
                    }
                    signIn(p.id, { email: canonical, callbackUrl });
                  }}
                >
                  <Input
                    type="email"
                    required
                    autoComplete="email"
                    inputSize="lg"
                    placeholder="you@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    disabled={pending !== null}
                  />
                  <Button
                    type="submit"
                    size="lg"
                    className="w-full"
                    loading={pending === p.id}
                    disabled={pending !== null || email.trim() === ""}
                  >
                    {p.label}
                  </Button>
                </form>
              ) : (
                <Button
                  key={p.id}
                  size="lg"
                  className="w-full"
                  loading={pending === p.id}
                  disabled={pending !== null}
                  onClick={() => {
                    setPending(p.id);
                    signIn(p.id, { callbackUrl });
                  }}
                >
                  {p.label}
                </Button>
              ),
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function SignInForm({
  providers,
}: {
  providers: ConfiguredProvider[];
}) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center p-10">
          <div className="text-muted-foreground text-sm">Loading...</div>
        </div>
      }
    >
      <Form providers={providers} />
    </Suspense>
  );
}
