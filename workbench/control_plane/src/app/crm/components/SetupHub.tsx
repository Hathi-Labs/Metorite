"use client";

/**
 * The setup hub — the first card of CRM settings (WS-53 CRM-X0, spec
 * `crm_platform.md` §19.11).
 *
 * The ONE place where the setup changes. It shows the mode, the connection,
 * the last sync and the record counts, and it offers only the actions that
 * fit the state. Each action says in one sentence what it does. An action that
 * removes the records opens `StartOverDialog`, which offers a download first.
 *
 * `SetupHubView` only draws, so a test can render it. `SetupHubSection` owns
 * the reads, the writes and the dialogs. The words and the decisions are in
 * `lib/setup.ts` and `lib/startOver.ts`. Admins only: the page gates it, and
 * the gateway refuses anyone else.
 */

import { useState } from "react";

import Badge, { type BadgeTone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";
import { relativeTime } from "@/lib/taskCard";

import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import type { ConnectionInfo, CrmMode, SyncStatus } from "../lib/connect";
import {
  ACTIONS,
  SETUP_ANCHOR,
  connectionLabel,
  countLines,
  formatCount,
  hubActions,
  hubNote,
  hubState,
  modeLabel,
  type HubAction,
  type HubState,
  type RecordCounts,
} from "../lib/setup";
import { invalidateCrmMode, useRecordCounts } from "../lib/useCrmMode";
import StartOverDialog from "./StartOverDialog";

const STATUS_TONE: Record<string, BadgeTone> = {
  connected: "success",
  needs_reconnect: "warning",
  needs_reset: "warning",
  disconnected: "neutral",
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 px-4 py-2">
      <dt className="w-32 shrink-0 pt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 break-words text-xs text-foreground">{children}</dd>
    </div>
  );
}

/** "547 deals · 1 lead …", the non-zero entities only. */
export function countsSummary(counts: RecordCounts | null): string {
  if (!counts) return "—";
  if (counts.total === 0) return "No records";
  const parts = countLines(counts.counts)
    .filter((line) => line.count > 0)
    .map((line) => `${formatCount(line.count)} ${line.label.toLowerCase()}`);
  return `${formatCount(counts.total)} in all: ${parts.join(" · ")}`;
}

export type HubBusy = {
  syncing: boolean;
  connecting: boolean;
};

export function SetupHubView({
  state,
  mode,
  connection,
  sync,
  counts,
  busy,
  onAction,
}: {
  state: HubState;
  mode: CrmMode;
  /** `null` when the mirror is off for this organization. */
  connection: ConnectionInfo | null;
  sync: SyncStatus | null;
  counts: RecordCounts | null;
  busy: HubBusy;
  onAction: (action: HubAction) => void;
}) {
  const plan = hubActions(state, connection !== null);
  const mirror = mode === "mirror";
  const status = connection?.status ?? null;
  const lastError = sync?.error ?? connection?.last_error ?? null;
  const running = sync?.state === "running";

  return (
    <section
      id={SETUP_ANCHOR}
      className="rounded-xl border border-border bg-card/40"
      aria-label="CRM setup"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">Setup and sync</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{hubNote(state)}</p>
          {plan.main.map((action) => (
            <p key={action.id} className="mt-0.5 text-[11px] text-muted-foreground">
              {action.explain}
            </p>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {plan.main.map((action) => (
            <Button
              key={action.id}
              variant="primary"
              size="sm"
              loading={
                (action.id === "sync-now" && (busy.syncing || running)) ||
                ((action.id === "reconnect-zoho" || action.id === "connect-zoho") &&
                  busy.connecting)
              }
              disabled={busy.syncing || busy.connecting || (action.id === "sync-now" && running)}
              onClick={() => onAction(action)}
            >
              {action.label}
            </Button>
          ))}
        </div>
      </header>

      <dl className="divide-y divide-border/60">
        <Row label="Mode">{modeLabel(mode)}</Row>
        {mirror && (
          <Row label="Zoho">
            <Badge tone={STATUS_TONE[status ?? "disconnected"] ?? "neutral"}>
              {connectionLabel(state, connection)}
            </Badge>
          </Row>
        )}
        {mirror && (
          <Row label="Last sync">
            {running
              ? "Running now"
              : relativeTime(sync?.last_success_at ?? connection?.last_sync_at ?? null) || "Never"}
          </Row>
        )}
        {mirror && connection?.connected_by && (
          <Row label="Connected by">{connection.connected_by}</Row>
        )}
        {mirror && connection?.location && <Row label="Data centre">{connection.location}</Row>}
        {mirror && lastError && <Row label="Last error">{lastError}</Row>}
        <Row label="Records">{countsSummary(counts)}</Row>
      </dl>

      {plan.other.length > 0 && (
        <ul className="divide-y divide-border/60 border-t border-border" aria-label="Other actions">
          {plan.other.map((action) => (
            <li key={action.id} className="flex flex-wrap items-center gap-3 px-4 py-2">
              <p className="min-w-0 flex-1 basis-60 text-[11px] text-muted-foreground">{action.explain}</p>
              <Button
                variant="secondary"
                size="sm"
                disabled={busy.syncing || busy.connecting}
                onClick={() => onAction(action)}
              >
                {action.label}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The hub with its reads, its writes and its dialogs. */
export default function SetupHubSection({
  mode,
  connection,
  sync,
  syncing,
  connecting,
  onSyncNow,
  onConnect,
  onDisconnect,
}: {
  mode: CrmMode;
  connection: ConnectionInfo | null;
  sync: SyncStatus | null;
  syncing: boolean;
  connecting: boolean;
  onSyncNow: () => void;
  /** Start the Zoho consent. The callback keeps the records of the same account. */
  onConnect: () => void;
  onDisconnect: () => Promise<void>;
}) {
  const toast = useToast();
  const { counts } = useRecordCounts(true);
  const [dialog, setDialog] = useState<HubAction | null>(null);
  const [disconnecting, setDisconnecting] = useState<"ask" | "busy" | null>(null);
  const [choosing, setChoosing] = useState(false);
  const state = hubState(mode, connection);

  async function chooseMetorite() {
    setChoosing(true);
    try {
      await api.chooseNativeMode();
    } catch (err) {
      toast.show({
        key: "crm-mode",
        variant: "error",
        title: err instanceof CrmError ? err.message : "The CRM did not start. Try again.",
      });
    } finally {
      setChoosing(false);
      invalidateCrmMode();
    }
  }

  function act(action: HubAction) {
    if (action.dialog) {
      setDialog(action);
      return;
    }
    switch (action.id) {
      case "sync-now":
        onSyncNow();
        return;
      case "disconnect":
        setDisconnecting("ask");
        return;
      case "reconnect-zoho":
      case "connect-zoho":
        onConnect();
        return;
      case "use-metorite":
        void chooseMetorite();
        return;
      default:
        return;
    }
  }

  return (
    <>
      <SetupHubView
        state={state}
        mode={mode}
        connection={connection}
        sync={sync}
        counts={counts}
        busy={{ syncing: syncing || choosing, connecting }}
        onAction={act}
      />
      <ConfirmDialog
        open={disconnecting !== null}
        title="Disconnect Zoho?"
        body={ACTIONS.disconnect.explain}
        note="Reconnect the same Zoho account later to update them again."
        confirmLabel="Disconnect"
        confirmVariant="destructive"
        busy={disconnecting === "busy"}
        onCancel={() => setDisconnecting(null)}
        onConfirm={async () => {
          setDisconnecting("busy");
          try {
            await onDisconnect();
          } finally {
            setDisconnecting(null);
          }
        }}
      />
      {dialog?.dialog && (
        <StartOverDialog
          open
          title={dialog.dialog.title}
          explain={dialog.explain}
          then={dialog.dialog.then}
          counts={counts}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}
