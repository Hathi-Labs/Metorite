"use client";

import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import type { ConfiguredProvider } from "@/authPosture";
import Button from "@/components/ui/Button";

import { signInErrorMessage } from "./errorCopy";

function Form({ providers }: { providers: ConfiguredProvider[] }) {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  const errorMessage = signInErrorMessage(searchParams.get("error"));
  const [pending, setPending] = useState<string | null>(null);

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
