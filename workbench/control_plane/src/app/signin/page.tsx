"use client";

import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

function SignInForm() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  const errorParam = searchParams.get("error");
  const [loading, setLoading] = useState(false);

  const errorMessage =
    errorParam === "OAuthSignin"
      ? "Could not start Microsoft sign-in. Try again."
      : errorParam === "OAuthCallback"
        ? "Microsoft sign-in was cancelled or failed."
        : errorParam === "AccessDenied"
          ? "Only @fracktal.in accounts are allowed."
          : errorParam
            ? `Authentication error: ${errorParam}`
            : null;

  return (
    <div className="flex min-h-screen items-center justify-center p-10">
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-8 text-center">
        <h1 className="text-xl font-semibold">Metorite Control Plane</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Sign in with your Fracktal Microsoft 365 account.
        </p>

        {errorMessage && (
          <div className="mt-4 rounded-md border border-red-800 bg-red-950/50 px-4 py-3 text-sm text-red-300">
            {errorMessage}
          </div>
        )}

        <button
          onClick={() => {
            setLoading(true);
            signIn("microsoft-entra-id", { callbackUrl });
          }}
          disabled={loading}
          className="mt-6 w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium
                     hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed
                     transition-colors"
        >
          {loading ? "Redirecting to Microsoft..." : "Sign in with Microsoft"}
        </button>

        <p className="mt-4 text-xs text-muted-foreground">
          Only <code>@fracktal.in</code> addresses are accepted.
        </p>
      </div>
    </div>
  );
}

export default function SignIn() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center p-10">
          <div className="text-muted-foreground text-sm">Loading...</div>
        </div>
      }
    >
      <SignInForm />
    </Suspense>
  );
}