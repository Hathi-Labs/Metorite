"use client";

/**
 * The strips between the title bar and the pipeline in Mirror mode
 * (WS-53 CRM-Z7, spec §8.3).
 *
 * - The import progress, while a sync runs: a bar and one count for each kind
 *   of record, against the estimate when Zoho gave one.
 * - The warning when the records stop updating: the consent lapsed, an admin
 *   disconnected, or the new consent is for another account (CRM-X0). An
 *   admin gets one button, to the setup hub, which offers what fits. A member
 *   is told whom to ask.
 * - `SetupLink`: the mode part of the title-bar line, as a link to the hub.
 *
 * Both strips sit in the page. The shell bar carries nothing of the app.
 */

import type { ReactNode } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import ProgressBar from "@/components/ui/ProgressBar";

import {
  progressDetail,
  progressPercent,
  type ConnectionInfo,
  type SyncStatus,
} from "../lib/connect";
import { pushOnPlainClick } from "../lib/history";
import { SETUP_HREF } from "../lib/setup";

/** The banner's sentence for each stalled status. */
export function stalledSentence(status: ConnectionInfo["status"], isAdmin: boolean): string {
  if (status === "needs_reset") {
    return "The new Zoho account is not the one these records came from. Nothing syncs until an admin chooses.";
  }
  if (status === "disconnected") {
    return "Zoho is disconnected. Records stay here, but they are not updating.";
  }
  return isAdmin
    ? "Zoho stopped sharing data with Metorite. Records stay here, but they are not updating."
    : "Zoho stopped sharing data with Metorite. Records are not updating.";
}

export function StalledBanner({
  isAdmin,
  status,
  onOpenSetup,
}: {
  isAdmin: boolean;
  status: ConnectionInfo["status"];
  onOpenSetup: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-warning/30 bg-warning/10 px-4 py-2"
    >
      <Icon name="AlertCircle" size={14} className="shrink-0 text-warning" />
      <p className="min-w-0 flex-1 text-xs text-foreground">{stalledSentence(status, isAdmin)}</p>
      {isAdmin ? (
        <Button variant="primary" size="sm" icon="Settings" onClick={onOpenSetup}>
          Open CRM setup
        </Button>
      ) : (
        <span className="text-xs text-muted-foreground">Ask an admin to connect Zoho again</span>
      )}
    </div>
  );
}

/**
 * The mode part of the title-bar line. For an admin it is a link to the setup
 * hub (CRM-X0), so "Zoho disconnected" leads to the fix. A member gets text.
 *
 * A plain click calls `pushView` (CRM-C1). A `next/link` `<Link>` waits on a
 * server request before the view changes, as `router.push` does
 * (`lib/history.ts`). The `href` stays, so a click with a modifier key opens
 * a new tab, and the browser shows the address.
 */
export function SetupLink({ isAdmin, children }: { isAdmin: boolean; children: ReactNode }) {
  if (!isAdmin) return <>{children}</>;
  return (
    <a
      href={SETUP_HREF}
      onClick={(event) => pushOnPlainClick(event, SETUP_HREF)}
      className="underline underline-offset-2 hover:text-foreground"
      title="Open CRM setup"
    >
      {children}
    </a>
  );
}

export function ImportProgress({ status }: { status: SyncStatus }) {
  const percent = progressPercent(status);
  const detail = progressDetail(status);
  return (
    <div className="shrink-0 border-b border-border bg-card/40 px-4 py-3" aria-live="polite">
      <p className="mb-1.5 text-xs font-medium text-foreground">
        {status.kind === "import" || status.kind === null
          ? "Importing from Zoho. You can keep working."
          : "Syncing from Zoho."}
      </p>
      {percent !== null ? (
        <ProgressBar percent={percent} label="Zoho import" detail={detail} />
      ) : (
        <p className="text-[11px] text-muted-foreground">{detail || "Starting…"}</p>
      )}
    </div>
  );
}
