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
 * ACC-2 (task 3988): each MCP and API row of the organization also shows "Who
 * can use". The control draws only when `GET /admin/integration-access`
 * answers. For an org off the flag (404) and for a viewer with no right (403)
 * nothing of it draws. `WhoCanUse.tsx` holds the parts. On a cold load the
 * list keeps its skeleton until the rules answer, for `ACCESS_WAIT_MS` at
 * most, so no row grows under the pointer.
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
import { GitHubAppReturn } from "@/components/GitHubAppConnect";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import FilterPills from "@/components/FilterPills";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import { ACCESS_KEY, clearAccessRule } from "../lib/accessSave";
import * as api from "../lib/api";
import {
  APP_LABEL,
  appPills,
  connectDialogCopy,
  consentReturn,
  disconnectCopy,
  disconnectFailure,
  filterConnections,
  heldSwitches,
  mcpConnect,
  mcpName,
  rowLabel,
  statusPills,
  switchesSavedText,
  testMessage,
  type AppFilter,
  type HeldSwitches,
  type McpSwitches,
  type StatusFilter,
} from "../lib/connections";
import {
  ACCESS_WAIT_MS,
  CLEAR_CHANGED,
  RULE_GONE,
  SAVE_FAILED,
  clearCopy,
  rowRule,
  savedTitle,
  unmatchedRules,
  waitsForRules,
  type AccessItem,
} from "../lib/whoCanUse";
import { ApiConnectReturn } from "./ApiConnect";
import ApiKeysView from "./ApiKeysView";
import CalendarConnectionsGroup from "./CalendarConnectionsGroup";
import ConnectionList from "./ConnectionList";
import ConnectionSheet from "./ConnectionSheet";
import WhoCanUseDialog, { AccessNotice, UnmatchedRules, WhoCanUseRow } from "./WhoCanUse";
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
  const [savingDescription, setSavingDescription] = useState(false);
  // MCP-C2 review: a save of the switches that the list may not show yet.
  const [held, setHeld] = useState<HeldSwitches | null>(null);
  // The list on screen now, so a save that ends records the list it saw.
  const listRef = useRef<unknown>(res.data);
  useEffect(() => {
    listRef.current = res.data;
  }, [res.data]);
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

  // ACC-2: the access rules. `null` for an org off the flag, and for a viewer
  // with no right: then no control draws.
  const rules = useCachedResource(ACCESS_KEY, api.getIntegrationAccess);
  const [ruling, setRuling] = useState<string | null>(null);
  const [clearing, setClearing] = useState<AccessItem | null>(null);
  const [clearBusy, setClearBusy] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  // The bound of the wait for the rules. It starts when the list arrives.
  const listArrived = Boolean(res.data);
  const [waitOver, setWaitOver] = useState(false);
  useEffect(() => {
    if (!listArrived) return;
    const timer = setTimeout(() => setWaitOver(true), ACCESS_WAIT_MS);
    return () => clearTimeout(timer);
  }, [listArrived]);
  const holding = !waitOver && waitsForRules(res.data, rules.data, rules.error);

  const all = res.data ?? [];
  const rows = useMemo(() => filterConnections(all, app, status), [all, app, status]);
  const open = all.find((r) => r.id === openId) ?? null;
  const ruled = rules.data?.services.find((i) => i.service === ruling) ?? null;
  const ruledRow = all.find((r) => rowRule(rules.data, r)?.service === ruling) ?? null;
  // A stored rule with no row shows under the whole list, never under a filter.
  const unfiltered = app === "all" && status === "all";
  const unmatched = unfiltered && !res.loading ? unmatchedRules(rules.data, all) : [];
  // The pair the panel draws while a save is not in the list yet. The hold
  // ends when the row shows the saved pair, after a newer read of a failed
  // save, or when the panel closes.
  const heldView = open ? heldSwitches(open, held, res.data) : null;
  const heldDone = held !== null && (heldView === null || heldView.done);
  useEffect(() => {
    if (heldDone) setHeld(null);
  }, [heldDone]);

  // The read model is not built yet: show the keys, not a blank page.
  if (res.data === null) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <p className="shrink-0 border-b border-border px-4 py-2 text-xs text-muted-foreground">
          The connections list is not ready yet. Your API keys are below.
        </p>
        {/* P3 of the API-5 review: a Zoho Books return never lands in silence. */}
        <ApiConnectReturn onConnected={invalidateConnections} />
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

  async function setSwitches(next: McpSwitches) {
    const name = open ? mcpName(open) : null;
    if (!open || !name) return;
    const id = open.id;
    setSavingConfirm(true);
    try {
      // H-300 and MCP-C2: both switches go in one PATCH. The panel draws the
      // pair that the gateway saved until the list shows it.
      const saved = await api.setMcpSwitches(name, next);
      setHeld({ id, pair: saved, list: listRef.current });
      toast.show({
        key: "integrations-always-confirm",
        variant: "success",
        // A live run keeps the switches it read at the attach, so the words
        // speak of new chats only.
        title: switchesSavedText(saved),
      });
    } catch (err) {
      // The gateway can save before the answer fails (a BFF timeout), so the
      // stored pair is not known. The ticks wait for the next read.
      setHeld({ id, pair: null, list: listRef.current });
      toast.show({
        key: "integrations-always-confirm",
        variant: "error",
        title: err instanceof Error ? err.message : "The request failed.",
      });
    } finally {
      // Both ways: read the list again, so the panel shows what is stored.
      invalidateConnections();
      setSavingConfirm(false);
    }
  }

  async function saveDescription(text: string) {
    const name = open ? mcpName(open) : null;
    if (!open || !name) return;
    setSavingDescription(true);
    try {
      await api.setMcpDescription(name, text);
      toast.show({
        key: "integrations-description",
        variant: "success",
        title: "Description saved. New chats read it.",
      });
    } catch (err) {
      toast.show({
        key: "integrations-description",
        variant: "error",
        title: err instanceof Error ? err.message : "The request failed.",
      });
    } finally {
      // Both ways: read the list again, so the panel shows what is stored.
      invalidateConnections();
      setSavingDescription(false);
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

  // `lib/accessSave.ts` has already put the saved rule on screen.
  function ruleSaved(saved: AccessItem) {
    const label = ruledRow ? rowLabel(ruledRow) : saved.service;
    setRuling(null);
    toast.show({ key: "integrations-access", variant: "success", title: savedTitle(label, saved) });
  }

  async function clearRule() {
    if (!clearing) return;
    setClearBusy(true);
    setClearError(null);
    try {
      const outcome = await clearAccessRule(clearing);
      if (outcome.kind !== "saved") {
        // The confirm stays. Only a Cancel and a new open can pass this.
        setClearError(outcome.kind === "changed" ? CLEAR_CHANGED : RULE_GONE);
        return;
      }
      setClearing(null);
      toast.show({
        key: "integrations-access",
        variant: "success",
        title: savedTitle(outcome.item.service, outcome.item),
      });
    } catch (err) {
      setClearError(err instanceof api.IntegrationsError ? err.message : SAVE_FAILED);
    } finally {
      setClearBusy(false);
    }
  }

  const clearWords = clearing ? clearCopy(clearing) : null;
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
      {/* WS-54 GH-3: GitHub sends the browser back here after the install. */}
      <GitHubAppReturn onConnected={invalidateConnections} />
      {/* API-5: Zoho sends the browser back here after the Books consent. */}
      <ApiConnectReturn onConnected={invalidateConnections} />
      {/* CE-10c (D96.1): the calendar connections, read-only, with a link to Calendar settings. */}
      <CalendarConnectionsGroup />
      {res.error && res.data === undefined ? (
        <p role="alert" className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {res.error}
        </p>
      ) : null}
      <AccessNotice list={rules.data} error={rules.error} onRetry={rules.refresh} />
      <ConnectionList
        rows={rows}
        total={all.length}
        loading={res.loading || holding}
        selectedId={openId}
        onOpen={setOpenId}
        onClearFilters={() => {
          setApp("all");
          setStatus("all");
        }}
        rowExtra={(row) => {
          const item = rowRule(rules.data, row);
          return item ? (
            <WhoCanUseRow
              item={item}
              listReadable={rules.data?.readable !== false}
              label={rowLabel(row)}
              onOpen={() => setRuling(item.service)}
            />
          ) : null;
        }}
        footer={
          unmatched.length ? (
            <UnmatchedRules
              items={unmatched}
              onChange={(item) => setRuling(item.service)}
              onClear={(item) => {
                setClearError(null);
                setClearing(item);
              }}
            />
          ) : undefined
        }
      />
      {ruled && (
        <WhoCanUseDialog
          key={ruled.service}
          item={ruled}
          label={ruledRow ? rowLabel(ruledRow) : ruled.service}
          onClose={() => setRuling(null)}
          onSaved={ruleSaved}
        />
      )}
      {clearing && clearWords && (
        <ConfirmDialog
          open
          title={clearWords.title}
          body={clearWords.body}
          confirmLabel={clearWords.confirmLabel}
          confirmVariant="destructive"
          icon="RotateCcw"
          busy={clearBusy}
          onConfirm={clearRule}
          onCancel={() => {
            if (!clearBusy) setClearing(null);
          }}
        >
          {clearError ? (
            <p role="alert" className="break-words text-xs text-destructive">
              {clearError}
            </p>
          ) : null}
        </ConfirmDialog>
      )}
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
          onSwitches={setSwitches}
          savingSwitches={savingConfirm || Boolean(heldView?.wait)}
          switches={heldView?.switches ?? null}
          onDescription={saveDescription}
          savingDescription={savingDescription}
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
