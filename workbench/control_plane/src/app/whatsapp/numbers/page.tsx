"use client";

// Numbers — manage the connected WhatsApp numbers from inside the app: see their
// status, disconnect one, or connect another. Disconnect was previously only
// reachable from the Integrations page; this brings account management into the
// WhatsApp app itself (via the shared nav's "Numbers" tab).

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  disconnectAccount,
  fetchAccounts,
  startHistoryImport,
} from "../lib/api";
import { accountHistoryLine, type HistoryTone } from "../lib/historySync";
import type { WaAccount } from "../lib/types";

/** The token class of each history tone. No new colour (WS-20 WA-C3 P12). */
const HISTORY_TONE: Record<HistoryTone, string> = {
  muted: "text-muted-foreground",
  success: "text-success",
  destructive: "text-destructive",
};

export default function NumbersPage() {
  const [accounts, setAccounts] = useState<WaAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setAccounts(await fetchAccounts());
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onDisconnect = useCallback(
    async (a: WaAccount) => {
      const label = a.display_name || a.phone_number || "this number";
      if (
        !confirm(
          `Disconnect ${label}? Its chats stay stored, but no new messages will ` +
            `sync until you reconnect.`
        )
      )
        return;
      setBusy(a.id);
      setError(null);
      const res = await disconnectAccount(a.id);
      setBusy(null);
      if (res.ok) await load();
      else setError(res.error ?? "Couldn't disconnect that number.");
    },
    [load]
  );

  // WS-20 WA-C3 P4: start the coexistence history import again, inside
  // Meta's 24-hour window. The server decides, and the list reloads.
  const onStartHistory = useCallback(
    async (a: WaAccount) => {
      setStarting(a.id);
      setError(null);
      const res = await startHistoryImport(a.id);
      setStarting(null);
      if (!res.ok) setError(res.error ?? "Couldn't start the history import.");
      await load();
    },
    [load]
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Icon name="Loader2" className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  return (
    <div className="mx-auto h-full max-w-2xl overflow-y-auto p-4 md:p-6">
      <div className="mb-1 flex items-center gap-2">
        <Icon name="Smartphone" className="h-4 w-4 text-primary" />
        <h1 className="text-[15px] font-semibold">Connected numbers</h1>
      </div>
      <p className="mb-5 text-[12.5px] text-muted-foreground">
        Manage the WhatsApp numbers linked to this workspace. Disconnecting keeps
        the history you&apos;ve synced but stops new messages until you reconnect.
      </p>

      {error && (
        <div className="mb-3 rounded-md bg-red-500/10 px-3 py-1.5 text-[11px] text-red-500">
          {error}
        </div>
      )}

      {accounts.length === 0 ? (
        <div className="rounded-xl border border-border p-8 text-center">
          <p className="text-[13px] text-muted-foreground">
            No numbers connected yet.
          </p>
          <Link
            href="/whatsapp/connect"
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-[12.5px] font-semibold text-primary-foreground hover:opacity-90"
          >
            <Icon name="Plus" className="h-3.5 w-3.5" /> Connect a number
          </Link>
        </div>
      ) : (
        <div className="space-y-2">
          {accounts.map((a) => (
            <div
              key={a.id}
              className="flex items-center gap-3 rounded-xl border border-border bg-background p-3"
            >
              <span
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-white"
                style={{ backgroundColor: a.avatar_color || "#25D366" }}
              >
                <Icon name="Smartphone" className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-semibold">
                    {a.display_name || a.phone_number || "WhatsApp number"}
                  </span>
                  {a.is_default && (
                    <span className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wide text-primary">
                      Default
                    </span>
                  )}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                  <span>{a.phone_number || "—"}</span>
                  <span aria-hidden>·</span>
                  <SyncBadge status={a.sync_status} />
                </div>
                <HistoryLine
                  account={a}
                  starting={starting === a.id}
                  onStart={() => void onStartHistory(a)}
                />
              </div>
              <button
                onClick={() => void onDisconnect(a)}
                disabled={busy === a.id}
                title="Disconnect this number"
                className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[12px] font-semibold text-muted-foreground hover:border-red-500/40 hover:text-red-500 disabled:opacity-50"
              >
                {busy === a.id ? (
                  <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Icon name="Trash2" className="h-3.5 w-3.5" />
                )}
                Disconnect
              </button>
            </div>
          ))}

          <Link
            href="/whatsapp/connect"
            className="mt-2 flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2.5 text-[12.5px] font-semibold text-muted-foreground hover:border-primary/50 hover:text-foreground"
          >
            <Icon name="Plus" className="h-3.5 w-3.5" /> Connect another number
          </Link>
        </div>
      )}
    </div>
  );
}

function HistoryLine({
  account,
  starting,
  onStart,
}: {
  account: WaAccount;
  starting: boolean;
  onStart: () => void;
}) {
  const line = accountHistoryLine(account, new Date());
  if (!line) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
      <span className={HISTORY_TONE[line.tone]}>{line.text}</span>
      {line.canStart && (
        <Button
          variant="secondary"
          size="sm"
          icon="History"
          loading={starting}
          onClick={onStart}
        >
          Start history import
        </Button>
      )}
    </div>
  );
}

function SyncBadge({ status }: { status: string }) {
  const live = status === "live";
  return (
    <span
      className={`inline-flex items-center gap-1 ${
        live ? "text-success" : "text-muted-foreground"
      }`}
    >
      {live ? <Icon name="Check" className="h-3 w-3" /> : null}
      {live ? "Live" : status || "idle"}
    </span>
  );
}
