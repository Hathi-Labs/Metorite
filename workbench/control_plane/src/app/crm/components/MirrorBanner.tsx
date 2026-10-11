"use client";

/**
 * The strips between the title bar and the pipeline in Mirror mode
 * (WS-53 CRM-Z7, spec §8.3).
 *
 * - The import progress, while a sync runs: a bar and one count for each kind
 *   of record, against the estimate when Zoho gave one.
 * - The warning when the consent has lapsed. An admin gets one button that
 *   starts the same flow as Connect. A member is told whom to ask.
 *
 * Both sit in the page. The shell bar carries nothing of the app.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import ProgressBar from "@/components/ui/ProgressBar";

import { progressDetail, progressPercent, type SyncStatus } from "../lib/connect";

export function ReconnectBanner({
  isAdmin,
  busy,
  onReconnect,
}: {
  isAdmin: boolean;
  busy: boolean;
  onReconnect: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-warning/30 bg-warning/10 px-4 py-2"
    >
      <Icon name="AlertCircle" size={14} className="shrink-0 text-warning" />
      <p className="min-w-0 flex-1 text-xs text-foreground">
        {isAdmin
          ? "Zoho stopped sharing data with Metorite. Records stay here, but they are not updating."
          : "Zoho stopped sharing data with Metorite. Records are not updating."}
      </p>
      {isAdmin ? (
        <Button variant="primary" size="sm" icon="RefreshCw" loading={busy} onClick={onReconnect}>
          Connect Zoho again
        </Button>
      ) : (
        <span className="text-xs text-muted-foreground">Ask an admin to connect Zoho again</span>
      )}
    </div>
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
