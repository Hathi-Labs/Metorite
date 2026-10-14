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
 *
 * WS-54 IN-8c (D98): Connect and Reconnect of an OAuth MCP server open a
 * dialog first. It says that the connection acts as this admin for the
 * agents of every member. Its confirm asks the BFF for the consent URL, and
 * the browser goes there. The gateway sends the browser back to
 * `?mcp_connected=` or `?mcp_connect_error=`, and this view shows a toast and
 * clears the query.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import FilterPills from "@/components/FilterPills";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import * as api from "../lib/api";
import {
  APP_LABEL,
  appPills,
  connectDialogCopy,
  consentReturn,
  disconnectCopy,
  disconnectFailure,
  filterConnections,
  mcpConnect,
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
import { viewHref } from "../lib/views";

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
  // IN-8c: the dialog of a consent, and the start that it runs.
  const [askingConnect, setAskingConnect] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const { access } = useAccess();
  const searchParams = useSearchParams();
  const returned = useRef(false);

  // The browser comes back from the consent with one of two params. Show
  // the outcome once, then clear the address so a reload shows nothing.
  // Review of #1023: anybody can craft the link, so wait for the list. The
  // toast shows only while the gateway serves MCP OAuth, and a success names
  // a row of the list (`consentReturn`).
  const loadedRows = res.data;
  useEffect(() => {
    if (returned.current || loadedRows === undefined) return;
    const outcome = consentReturn(searchParams, loadedRows);
    if (!outcome) return;
    returned.current = true;
    toast.show({
      key: "integrations-mcp-connect",
      variant: outcome.ok ? "success" : "error",
      title: outcome.title,
    });
    invalidateConnections();
    router.replace(viewHref("connections"), { scroll: false });
  }, [searchParams, router, toast, loadedRows]);

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
      // IN-8c: the Test of an OAuth server refreshes as a run does, and a
      // dead token turns the row to `needs_reconnect`. Read the list again.
      if (r.error === "token_expired") invalidateConnections();
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
        // A live run keeps the flag it read at the attach, so the words
        // speak of new chats only (H-300).
        title: next
          ? "New chats will ask before any tool runs."
          : "New chats will ask only before tools that can change data.",
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

  async function startConnect() {
    const name = open ? mcpName(open) : null;
    if (!open || !name) return;
    setConnecting(true);
    setConnectError(null);
    try {
      const url = await api.startMcpOAuth(name);
      // The URL is the gateway's, checked by the BFF: https, no user info.
      window.location.assign(url);
    } catch (err) {
      setConnectError(err instanceof Error ? err.message : "Metorite cannot start the sign-in.");
      setConnecting(false);
    }
  }

  const copy = open ? disconnectCopy(open) : null;
  const connectMode = open ? mcpConnect(open, canManage) : null;
  const connectCopy =
    open && connectMode ? connectDialogCopy(open, access.email, connectMode) : null;

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
            if (confirming || askingConnect) return;
            setOpenId(null);
          }}
          onNavigate={(href) => router.push(href)}
          onDisconnect={() => setConfirming(true)}
          onTest={test}
          onAlwaysConfirm={setAlwaysConfirm}
          savingAlwaysConfirm={savingConfirm}
          onConnect={() => {
            setConnectError(null);
            setAskingConnect(true);
          }}
          connecting={connecting}
        />
      )}
      {open && connectCopy && (
        <ConfirmDialog
          open={askingConnect}
          title={connectCopy.title}
          body={connectCopy.body}
          note={connectCopy.note}
          confirmLabel={connectCopy.confirmLabel}
          confirmVariant="primary"
          icon="LogIn"
          busy={connecting}
          onConfirm={startConnect}
          onCancel={() => {
            if (connecting) return;
            setAskingConnect(false);
            setConnectError(null);
          }}
        >
          {connectError ? (
            <p role="alert" className="text-xs text-destructive">
              {connectError}
            </p>
          ) : null}
        </ConfirmDialog>
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
