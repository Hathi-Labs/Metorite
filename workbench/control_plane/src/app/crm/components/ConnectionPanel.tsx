"use client";

/**
 * The admin's view of the CRM connection (WS-53 CRM-Z7, spec §8.3).
 *
 * It sits at the top of the Settings view of the CRM rail, above the pipeline
 * settings, so the rail keeps its one Settings row. Admins only: the buttons
 * call admin routes, and the gateway refuses anyone else.
 */

import { useState } from "react";

import Badge, { type BadgeTone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { relativeTime } from "@/lib/taskCard";

import type { ConnectionInfo, CrmMode, SyncStatus } from "../lib/connect";

const STATUS_TONE: Record<string, BadgeTone> = {
  connected: "success",
  needs_reconnect: "warning",
  disconnected: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  connected: "Connected",
  needs_reconnect: "Needs a new consent",
  disconnected: "Disconnected",
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

export default function ConnectionPanel({
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
  connection: ConnectionInfo;
  sync: SyncStatus | null;
  syncing: boolean;
  connecting: boolean;
  onSyncNow: () => void;
  onConnect: () => void;
  onDisconnect: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const mirror = mode === "mirror";
  const status = connection.status;
  const running = sync?.state === "running";
  const lastError = sync?.error ?? connection.last_error;

  return (
    <section className="rounded-xl border border-border bg-card/40" aria-label="CRM connection">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">Connection</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {mirror
              ? "Metorite shows your Zoho CRM. Records stay read-only here."
              : mode === "native"
                ? "Metorite is your CRM."
                : "No mode is chosen yet."}
          </p>
        </div>
        {mirror && status === "connected" && (
          <>
            <Button
              variant="secondary"
              size="sm"
              icon="RefreshCw"
              loading={syncing || running}
              disabled={syncing || running}
              onClick={onSyncNow}
            >
              Sync now
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
              Disconnect
            </Button>
          </>
        )}
        {mirror && status !== "connected" && (
          <Button variant="primary" size="sm" loading={connecting} onClick={onConnect}>
            {status === "disconnected" ? "Connect Zoho" : "Connect Zoho again"}
          </Button>
        )}
      </header>

      <dl className="divide-y divide-border/60">
        <Row label="Mode">{mirror ? "Mirror" : mode === "native" ? "Native" : "Not chosen"}</Row>
        {mirror && (
          <>
            <Row label="Provider">Zoho CRM</Row>
            <Row label="Status">
              {status ? (
                <Badge tone={STATUS_TONE[status] ?? "neutral"}>{STATUS_LABEL[status] ?? status}</Badge>
              ) : (
                "—"
              )}
            </Row>
            <Row label="Data centre">{connection.location ?? "—"}</Row>
            <Row label="Connected by">{connection.connected_by ?? "—"}</Row>
            <Row label="Last sync">
              {relativeTime(sync?.last_success_at ?? connection.last_sync_at) || "Never"}
            </Row>
            <Row label="Next sync">
              {running ? "Running now" : relativeTime(sync?.next_run_at) || "—"}
            </Row>
            {lastError && <Row label="Last error">{lastError}</Row>}
          </>
        )}
      </dl>

      <ConfirmDialog
        open={confirming}
        title="Disconnect Zoho?"
        body="The synced records stay, read-only."
        confirmLabel="Disconnect"
        confirmVariant="destructive"
        busy={disconnecting}
        onCancel={() => setConfirming(false)}
        onConfirm={async () => {
          setDisconnecting(true);
          try {
            await onDisconnect();
          } finally {
            setDisconnecting(false);
            setConfirming(false);
          }
        }}
      />
    </section>
  );
}
