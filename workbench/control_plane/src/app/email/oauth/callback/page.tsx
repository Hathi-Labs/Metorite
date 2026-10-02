"use client";

/**
 * Where a mailbox connect lands, success or failure (WS-17 EM-T3b).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.3 and §10.4.3.
 *
 * The words for each result come from `callbackView` in
 * `app/email/lib/connect.ts`, so a vitest can read them. This page only
 * draws them. Two results get a guided page instead of an error:
 *
 * - `admin_consent_required`: the organization of the member lets only an IT
 *   admin approve new apps. The page offers a prefilled email to the admin
 *   and a copy of the admin-consent link. The client ID in that link comes
 *   from the gateway (`GET /email/oauth/microsoft/app`), never from a
 *   constant here. A member who is the admin opens the link here with
 *   "I am the admin" (EM-T3c), and Microsoft returns to `/oauth/approved`.
 * - `consent_declined`: the member said no. A friendly retry.
 *
 * ⚠️ No link on this page goes to Integrations. A member has nothing to
 * configure there (EM-T3b done-when 1).
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { getMailAppInfo } from "../../lib/api";
import {
  adminConsentMailto,
  adminConsentUrl,
  callbackView,
  retryTarget,
  type CallbackView,
  type ConnectProviderId,
} from "../../lib/connect";

/**
 * Normalise the post-auth redirect target to a safe internal path.
 *
 * `redirect_after` arrives as the full URL the user came from (e.g.
 * "https://app.metorite.com/email"). Reduce it to a same-origin path so
 * router.push() navigates correctly; reject anything cross-origin or
 * unparseable to avoid an open-redirect and the 404 that results from
 * pushing an absolute/encoded string as a relative path.
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

/**
 * Start the connect again, through the range step in Email (EM-T6d fix
 * round 1). This is a first connect, so the member's range must go with it.
 */
function connectAgain(provider: ConnectProviderId): void {
  window.location.href = retryTarget(provider);
}

/** The icon and the status tone of each result. Tokens only. */
const TONE: Record<CallbackView["kind"], { icon: string; className: string }> = {
  loading: { icon: "Loader2", className: "bg-primary/10 text-primary" },
  connected: { icon: "CheckCircle2", className: "bg-success/10 text-success" },
  admin_consent_required: { icon: "ShieldCheck", className: "bg-warning/10 text-warning" },
  consent_declined: { icon: "Undo2", className: "bg-muted text-muted-foreground" },
  duplicate: { icon: "Info", className: "bg-info/10 text-info" },
  retry: { icon: "RefreshCw", className: "bg-warning/10 text-warning" },
  unknown: { icon: "AlertCircle", className: "bg-destructive/10 text-destructive" },
};

function AdminConsentSteps() {
  // undefined: loading. null: the deployment has no app, or the read failed.
  const [link, setLink] = useState<string | null | undefined>(undefined);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    void getMailAppInfo().then((app) => {
      if (live) setLink(app ? adminConsentUrl(app) : null);
    });
    return () => {
      live = false;
    };
  }, []);

  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      setCopied(false);
    }
  };

  if (link === undefined) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon name="Loader2" size={14} className="animate-spin" /> Getting the approval link…
      </p>
    );
  }
  if (link === null) {
    return (
      <p className="rounded-md border border-border bg-secondary/50 px-3 py-2 text-xs text-muted-foreground">
        Metorite could not load the approval link. Ask your Metorite admin to send it to your IT
        admin.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <ol className="space-y-1.5 text-xs text-muted-foreground">
        <li className="flex gap-2">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
            1
          </span>
          <span>Send the approval link to your IT admin.</span>
        </li>
        <li className="flex gap-2">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
            2
          </span>
          <span>Your admin opens it and approves Metorite for the company.</span>
        </li>
        <li className="flex gap-2">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
            3
          </span>
          <span>You connect your mailbox again.</span>
        </li>
      </ol>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button
          variant="primary"
          icon="Send"
          className="flex-1"
          onClick={() => {
            window.location.href = adminConsentMailto(link);
          }}
        >
          Email your IT admin
        </Button>
        <Button
          variant="secondary"
          icon={copied ? "Check" : "Copy"}
          className="flex-1"
          onClick={() => void copy()}
        >
          {copied ? "Link copied" : "Copy approval link"}
        </Button>
      </div>
      {/* EM-T3c: a member who is also the IT admin approves here, in the
          same tab. Microsoft then returns to `/oauth/approved`. */}
      <p className="text-center text-xs text-muted-foreground">
        <a href={link} className="text-primary font-medium hover:opacity-80">
          I am the admin
        </a>
        {": approve Metorite for the company now."}
      </p>
      <p
        className="select-all break-all rounded-md border border-border bg-secondary/50 px-2.5 py-2 font-mono text-[10px] text-muted-foreground"
        aria-label="Approval link"
      >
        {link}
      </p>
    </div>
  );
}

function CallbackContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const error = searchParams.get("error");
  const accountId = searchParams.get("account_id");
  const email = searchParams.get("email");
  const providerParam = searchParams.get("provider");
  const redirectAfter = searchParams.get("redirect_after");
  const provider: ConnectProviderId = providerParam === "gmail" ? "gmail" : "microsoft";

  const view = callbackView({ error, accountId, email });
  const tone = TONE[view.kind];

  const [countdown, setCountdown] = useState(3);
  const success = view.kind === "connected";

  // Into Email after a short pause, so the member reads which mailbox it was.
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

  const openEmail = () => router.push(success ? safeRedirectTarget(redirectAfter) : "/email");

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md">
        <div className="rounded-lg border border-border bg-card p-6 shadow-lg chat-fade-in">
          <div className="flex flex-col items-center text-center gap-3">
            <span className={`flex h-12 w-12 items-center justify-center rounded-full ${tone.className}`}>
              <Icon
                name={tone.icon}
                size={24}
                className={view.kind === "loading" ? "animate-spin" : undefined}
              />
            </span>
            <div>
              <h2 className="text-base font-semibold text-foreground break-words">{view.title}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{view.body}</p>
            </div>
          </div>

          {view.kind === "admin_consent_required" && (
            <div className="mt-5 border-t border-border pt-4">
              <AdminConsentSteps />
            </div>
          )}

          {view.kind !== "loading" && (
            <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:justify-center">
              {success && (
                <Button variant="primary" icon="Mail" onClick={openEmail}>
                  Open Email
                </Button>
              )}
              {(view.kind === "consent_declined" ||
                view.kind === "retry" ||
                view.kind === "unknown") && (
                <Button variant="primary" icon="RefreshCw" onClick={() => connectAgain(provider)}>
                  Try again
                </Button>
              )}
              {view.kind === "admin_consent_required" && (
                <Button variant="secondary" icon="RefreshCw" onClick={() => connectAgain(provider)}>
                  Approved? Connect again
                </Button>
              )}
              {!success && (
                <Button variant="secondary" icon="ArrowLeft" onClick={openEmail}>
                  Back to Email
                </Button>
              )}
            </div>
          )}

          {success && (
            <p className="mt-3 text-center text-xs text-muted-foreground">
              Opening Email in {countdown}s…
            </p>
          )}

          {view.reference && (
            <p className="mt-4 text-center text-[11px] text-muted-foreground">
              Reference: <span className="font-mono">{view.reference}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <Icon name="Loader2" size={32} className="animate-spin text-primary" />
        </div>
      }
    >
      <CallbackContent />
    </Suspense>
  );
}
