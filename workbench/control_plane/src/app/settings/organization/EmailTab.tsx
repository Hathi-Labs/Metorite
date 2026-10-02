"use client";

/**
 * Organisation → Email (WS-17 EM-T3d).
 *
 * Owning spec: `project-docs/specs/email_app_master_plan.md` §10.4.3,
 * "EM-T3d — pre-approval and the connected-member count". Also
 * `launch_surface.md` §6.2, the fifth tab.
 *
 * Two parts:
 *
 * 1. **Pre-approval.** A link to the Microsoft admin-consent page of the mail
 *    app, so an admin can approve Metorite before a member meets the consent
 *    error. The link and the app facts come from the Email app's own helpers
 *    (`adminConsentUrl`, `getMailAppInfo`), never from a copy. Microsoft then
 *    returns the admin to `/oauth/approved` (EM-T3c), which says "You can
 *    close this page", so the link opens a new tab.
 * 2. **The count.** Seven integers from the admin-only
 *    `GET /email/admin/connections`, through the email BFF catch-all. D-EM-4:
 *    an admin sees how many members connected a mailbox, and never their mail.
 *
 * The parent refuses a non-admin before this renders. That is a courtesy. The
 * gateway's `admin:members:read` gate is the boundary.
 *
 * `EmailTab` fetches. `EmailTabView` draws, from props only, so
 * `emailTab.test.ts` can render it in the node test runner.
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { getMailAppInfo } from "@/app/email/lib/api";
import { adminConsentUrl, type MailAppInfo } from "@/app/email/lib/connect";

import {
  EMAIL_TAB_COPY,
  countTiles,
  readConnectionCounts,
  type CountsRead,
} from "./lib/emailConnections";

const COUNTS_PATH = "/api/email/admin/connections";

async function fetchCounts(): Promise<CountsRead> {
  try {
    const r = await fetch(COUNTS_PATH, { cache: "no-store" });
    const payload = await r.json().catch(() => null);
    return readConnectionCounts(r.ok, payload);
  } catch {
    return { state: "failed" };
  }
}

export default function EmailTab() {
  // undefined: loading. null: the deployment has no app, or the read failed.
  const [app, setApp] = useState<MailAppInfo | null | undefined>(undefined);
  const [counts, setCounts] = useState<CountsRead>({ state: "loading" });

  useEffect(() => {
    let live = true;
    void getMailAppInfo().then((found) => {
      if (live) setApp(found);
    });
    void fetchCounts().then((next) => {
      if (live) setCounts(next);
    });
    return () => {
      live = false;
    };
  }, []);

  const retry = useCallback(() => {
    setCounts({ state: "loading" });
    void fetchCounts().then(setCounts);
  }, []);

  return <EmailTabView app={app} counts={counts} onRetry={retry} />;
}

export function EmailTabView({
  app,
  counts,
  onRetry,
}: {
  app: MailAppInfo | null | undefined;
  counts: CountsRead;
  onRetry: () => void;
}) {
  const copy = EMAIL_TAB_COPY;
  return (
    <div className="flex flex-col gap-4">
      {/* ── Pre-approval ──────────────────────────────────────────────────── */}
      {/* No heading icon: Seats and Branding beside it draw none. */}
      <section className="rounded-xl border border-border bg-card/40 p-4">
        <h2 className="text-sm font-semibold text-foreground">{copy.approval.title}</h2>
        <p className="mt-1 text-xs text-muted-foreground">{copy.approval.body}</p>

        {app === undefined ? (
          <div className="mt-3" role="status" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-4 w-56" />
          </div>
        ) : app === null ? (
          <p className="mt-3 rounded-md border border-border bg-secondary/50 px-3 py-2 text-xs text-muted-foreground">
            {copy.approval.unavailable}
          </p>
        ) : (
          <ApprovalLink link={adminConsentUrl(app)} />
        )}
      </section>

      {/* ── The count ─────────────────────────────────────────────────────── */}
      <section className="rounded-xl border border-border bg-card/40 p-4">
        <h2 className="text-sm font-semibold text-foreground">{copy.counts.title}</h2>
        <p className="mt-1 text-xs text-muted-foreground">{copy.counts.intro}</p>

        {counts.state === "loading" ? (
          <div
            className="mt-3 flex flex-wrap gap-6"
            role="status"
            aria-busy="true"
            aria-label="Loading"
          >
            {Array.from({ length: 7 }, (_, i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <Skeleton className="h-6 w-8" />
                <Skeleton className="h-2.5 w-16" />
              </div>
            ))}
          </div>
        ) : counts.state === "failed" ? (
          // Never a row of zeros. "0 members" is an answer, and this is not one.
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2">
            <Icon name="AlertTriangle" size={14} className="shrink-0 text-warning" />
            <span className="flex-1 text-xs text-warning">{copy.counts.failed}</span>
            <Button size="sm" variant="secondary" onClick={onRetry}>
              {copy.counts.retry}
            </Button>
          </div>
        ) : (
          <>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-3">
              {countTiles(counts.counts).map((t) => (
                <div key={t.key}>
                  <div className="text-lg font-semibold text-foreground">{t.value}</div>
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {t.label}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-3 text-[11px] text-muted-foreground">{copy.counts.removed}</p>
          </>
        )}
      </section>
    </div>
  );
}

function ApprovalLink({ link }: { link: string }) {
  const copy = EMAIL_TAB_COPY.approval;
  return (
    <div className="mt-3 flex flex-col gap-2">
      <a
        href={link}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-primary hover:opacity-80"
      >
        {copy.link}
        <Icon name="ExternalLink" size={14} />
      </a>
      <p className="text-[11px] text-muted-foreground">{copy.note}</p>
      <p className="text-[11px] text-muted-foreground">{copy.notAdmin}</p>
      <p
        className="select-all break-all rounded-md border border-border bg-secondary/50 px-2.5 py-2 font-mono text-[10px] text-muted-foreground"
        aria-label="Approval link"
      >
        {link}
      </p>
    </div>
  );
}
