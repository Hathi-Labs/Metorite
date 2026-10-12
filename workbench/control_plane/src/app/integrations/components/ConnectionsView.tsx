"use client";

/**
 * The Connections view: every connection the company holds, in one list.
 *
 * The gateway builds the rows (`GET /integrations/connections`, IN-1). This
 * view filters them by app and status, opens one in a side panel, and runs the
 * acts the console owns: Disconnect, the MCP Test and the MCP "always
 * confirm" switch (H-300). Setup stays in the owning app (spec §3.1), so
 * every other action is a link.
 *
 * A 404 from the list route means the read model is not deployed yet. The
 * view then shows a line and the API keys view, so the page is never blank.
 */

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import ConfirmDialog from "@/components/ui/ConfirmDialog";
import FilterPills from "@/components/FilterPills";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import * as api from "../lib/api";
import {
  APP_LABEL,
  appPills,
  disconnectCopy,
  disconnectFailure,
  filterConnections,
  mcpName,
  rowLabel,
  statusPills,
  testMessage,
  type AppFilter,
  type StatusFilter,
} from "../lib/connections";
import ApiKeysView from "./ApiKeysView";
import ConnectionList from "./ConnectionList";
import ConnectionSheet from "./ConnectionSheet";

export const CONNECTIONS_KEY = "integrations:connections";

/** Wake every watcher of the list after a write. */
export function invalidateConnections(): void {
  invalidate("integrations:");
}

export default function ConnectionsView({ canManage }: { canManage: boolean }) {
  const router = useRouter();
  const toast = useToast();
  const res = useCachedResource(CONNECTIONS_KEY, api.getConnections);
  const [app, setApp] = useState<AppFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [savingConfirm, setSavingConfirm] = useState(false);

  const all = res.data ?? [];
  const rows = useMemo(() => filterConnections(all, app, status), [all, app, status]);
  const open = all.find((r) => r.id === openId) ?? null;

  // The read model is not built yet: show the keys, not a blank page.
  if (res.data === null) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <p className="shrink-0 border-b border-border px-4 py-2 text-xs text-muted-foreground">
          The connections list is not ready yet. Your API keys are below.
        </p>
        <ApiKeysView />
      </div>
    );
  }

  async function disconnect() {
    if (!open) return;
    setBusy(true);
    try {
      await api.disconnect(open.id);
      toast.show({ key: "integrations-disconnect", variant: "success", title: `${rowLabel(open)} is disconnected.` });
      setConfirming(false);
      setOpenId(null);
      invalidateConnections();
    } catch (err) {
      setConfirming(false);
      // The row stays: nothing here removes it from the list.
      const fail = disconnectFailure(err instanceof Error ? (err as api.IntegrationsError) : null, open);
      const href = fail.href;
      toast.show({
        key: "integrations-disconnect",
        variant: "error",
        title: fail.title,
        action: href
          ? { label: `Open in ${APP_LABEL[open.owning_app]}`, onClick: () => router.push(href) }
          : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    const name = open ? mcpName(open) : null;
    if (!open || !name) return;
    setTesting(true);
    try {
      const r = await api.testMcp(name);
      toast.show({
        key: "integrations-test",
        variant: r.ok ? "success" : "error",
        title: testMessage(r),
      });
    } catch (err) {
      toast.show({
        key: "integrations-test",
        variant: "error",
        title: err instanceof Error ? err.message : "The request failed.",
      });
    } finally {
      setTesting(false);
    }
  }

  async function setAlwaysConfirm(next: boolean) {
    const name = open ? mcpName(open) : null;
    if (!open || !name) return;
    setSavingConfirm(true);
    try {
      await api.setMcpAlwaysConfirm(name, next);
      toast.show({
        key: "integrations-always-confirm",
        variant: "success",
        title: next
          ? `${rowLabel(open)} now asks before any tool runs.`
          : `${rowLabel(open)} now asks only for tools that can change data.`,
      });
      invalidateConnections();
    } catch (err) {
      toast.show({
        key: "integrations-always-confirm",
        variant: "error",
        title: err instanceof Error ? err.message : "The request failed.",
      });
    } finally {
      setSavingConfirm(false);
    }
  }

  const copy = open ? disconnectCopy(open) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {all.length > 0 && (
        <>
          <FilterPills
            items={appPills(all)}
            activeId={app}
            onChange={(id) => {
              setApp(id as AppFilter);
              setStatus("all");
            }}
          />
          <FilterPills
            items={statusPills(all, app)}
            activeId={status}
            onChange={(id) => setStatus(id as StatusFilter)}
          />
        </>
      )}
      {res.error && res.data === undefined ? (
        <p role="alert" className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {res.error}
        </p>
      ) : null}
      <ConnectionList
        rows={rows}
        total={all.length}
        loading={res.loading}
        selectedId={openId}
        onOpen={setOpenId}
        onClearFilters={() => {
          setApp("all");
          setStatus("all");
        }}
      />
      {open && (
        <ConnectionSheet
          row={open}
          canManage={canManage}
          busy={busy}
          testing={testing}
          onClose={() => {
            if (confirming) return;
            setOpenId(null);
          }}
          onNavigate={(href) => router.push(href)}
          onDisconnect={() => setConfirming(true)}
          onTest={test}
          onAlwaysConfirm={setAlwaysConfirm}
          savingAlwaysConfirm={savingConfirm}
        />
      )}
      {open && copy && (
        <ConfirmDialog
          open={confirming}
          title={copy.title}
          body={copy.body}
          confirmLabel={copy.confirmLabel}
          confirmVariant="destructive"
          busy={busy}
          onConfirm={disconnect}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
