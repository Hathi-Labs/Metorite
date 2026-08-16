"use client";

import Icon from "@/components/Icon";
import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";

/**
 * Normalise the post-auth redirect target to a safe internal path.
 *
 * `redirect_after` arrives as the full URL the user came from (e.g.
 * "https://metorite.fracktal.in/integrations").  Reduce it to a
 * same-origin path so router.push() navigates correctly; reject anything
 * cross-origin or unparseable to avoid an open-redirect and the 404 that
 * results from pushing an absolute/encoded string as a relative path.
 */
function safeRedirectTarget(raw: string | null): string {
  if (!raw) return "/email";
  try {
    const u = new URL(raw, window.location.origin);
    if (u.origin !== window.location.origin) return "/email";
    return u.pathname + u.search + u.hash;
  } catch {
    return raw.startsWith("/") ? raw : "/email";
  }
}

function CallbackContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const error = searchParams.get("error");
  const accountId = searchParams.get("account_id");
  const email = searchParams.get("email");
  const provider = searchParams.get("provider");
  const redirectAfter = searchParams.get("redirect_after");

  const [countdown, setCountdown] = useState(5);
  const success = !error && !!accountId;

  // Auto-redirect after success
  useEffect(() => {
    if (!success) return;
    const target = safeRedirectTarget(redirectAfter);
    if (countdown <= 0) {
      router.push(target);
      return;
    }
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [success, countdown, redirectAfter, router]);

  const providerLabel = provider === "gmail" ? "Google / Gmail" : provider === "microsoft" ? "Microsoft / Outlook" : provider || "Unknown";
  const errorLabel =
    error === "invalid_state" ? "Invalid OAuth state — please try again." :
    error === "token_exchange_failed" ? "Failed to exchange authorization code. Check your OAuth credentials." :
    error === "email_fetch_failed" ? "Could not retrieve email address from provider." :
    error === "duplicate" ? `Account ${email || ""} is already connected.` :
    error ? `An unexpected error occurred: ${error}` : "";

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md">
        {/* Card */}
        <div className="bg-card border border-border rounded-2xl shadow-xl p-8 chat-fade-in">
          {!error && !accountId && (
            /* Loading state */
            <div className="flex flex-col items-center gap-4 py-8">
              <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full animate-spin" />
              <p className="text-sm text-muted-foreground">Completing authentication…</p>
            </div>
          )}

          {success && (
            /* Success */
            <div className="flex flex-col items-center text-center gap-4">
              <div className="w-14 h-14 rounded-full bg-emerald-500/15 flex items-center justify-center">
                <Icon name="CheckCircle2" className="w-8 h-8 text-emerald-400" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">Account Connected</h2>
                <p className="text-sm text-muted-foreground mt-1">
                  {providerLabel}
                </p>
              </div>
              {email && (
                <div className="bg-secondary rounded-lg px-4 py-2 text-sm font-mono text-foreground">
                  {email}
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                Redirecting in {countdown}s…
              </p>
              <div className="flex gap-2 mt-2">
                <a
                  href="/email"
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
                >
                  <Icon name="Mail" size={14} /> Open Email
                </a>
                <a
                  href="/integrations"
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-border text-sm text-foreground hover:bg-secondary transition-colors"
                >
                  <Icon name="Settings" size={14} /> Integrations
                </a>
              </div>
            </div>
          )}

          {error && (
            /* Error */
            <div className="flex flex-col items-center text-center gap-4">
              <div className="w-14 h-14 rounded-full bg-destructive/15 flex items-center justify-center">
                <Icon name="XCircle" className="w-8 h-8 text-destructive" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">Connection Failed</h2>
                <p className="text-sm text-muted-foreground mt-1">{errorLabel}</p>
              </div>
              <div className="flex gap-2 mt-2">
                <a
                  href="/integrations"
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
                >
                  <Icon name="ArrowRight" size={14} /> Try Again
                </a>
                <a
                  href="/email"
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-border text-sm text-foreground hover:bg-secondary transition-colors"
                >
                  <Icon name="Mail" size={14} /> Email Client
                </a>
              </div>
            </div>
          )}
        </div>

        <p className="text-[10px] text-muted-foreground text-center mt-4">
          Credentials are encrypted at rest with AES-256-GCM
        </p>
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      }
    >
      <CallbackContent />
    </Suspense>
  );
}
