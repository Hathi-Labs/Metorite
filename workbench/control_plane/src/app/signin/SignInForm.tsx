"use client";

import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import Button from "@/components/ui/Button";

export interface SignInProvider {
  /** NextAuth provider id — must match a provider `src/auth.ts` registers. */
  id: string;
  label: string;
}

function Form({ providers }: { providers: SignInProvider[] }) {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  const errorParam = searchParams.get("error");
  const [pending, setPending] = useState<string | null>(null);

  const errorMessage =
    errorParam === "OAuthSignin"
      ? "Could not start sign-in. Try again."
      : errorParam === "OAuthCallback"
        ? "Sign-in was cancelled or failed."
        : errorParam === "AccessDenied"
          ? "Your account isn't authorized for this workspace. Ask your admin for an invite."
          : errorParam
            ? `Authentication error: ${errorParam}`
            : null;

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
            {providers.map((p) => (
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
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function SignInForm({
  providers,
}: {
  providers: SignInProvider[];
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
