"use client";

/**
 * Connect Google Calendar and Outlook (WS-CAL CE-10c, task 66).
 *
 * One component set draws every face of the feature, so the settings sheet and the Calendar page
 * show the SAME buttons:
 *
 * - `ConnectedCalendarsView` is the pure face. It takes the list and the handlers as props, so a
 *   render test can drive each state.
 * - `ConnectedCalendarsSection` is the "Connected calendars" section of the settings sheet.
 * - `CalendarConnectPrompt` is the one-line offer on the Calendar page when no calendar is
 *   connected.
 * - `CalendarConnectReturn` reads `calendar_connected`, `calendar_error` and `settings` from the
 *   URL once, then cleans the address.
 *
 * Nothing draws while the list answers "not enabled" (a 404 from the gateway), so the feature
 * ships dark with no public flag. The words live in `../lib/calendarConnections.ts` and
 * `../lib/connectGuide.ts`. State of the data: `useCachedResource`, refreshed after each write.
 *
 * Fence: `CalendarConnections.test.tsx`.
 */

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";

import AlsoSyncCalendar from "./AlsoSyncCalendar";
import { useCalendarConnections, useConnect } from "../lib/useCalendarConnect";

export { useCalendarConnections, useConnect };

import {
  CONNECT_LABEL,
  PROVIDER_NAME,
  cleanedHref,
  connectable,
  connectedLine,
  disconnect,
  disconnectCopy,
  errorCopy,
  hasSyncError,
  isEnabled,
  lastSyncLine,
  reconnectLine,
  recallStarted,
  refreshConnections,
  returnNotice,
  rowState,
  showsPrompt,
  visibleOffers,
  syncNow,
  syncResultLine,
  wantsSettings,
  type CalendarConnection,
  type ConnectionsList,
  type Provider,
  type ReturnNotice,
} from "../lib/calendarConnections";
import {
  ADMIN_NOTE,
  ALSO_SYNC_STEPS,
  ALSO_SYNC_TITLE,
  FULL_GUIDE_LINE,
  GUIDE_LISTS,
  SYNC_NOTES,
  SYNC_TITLE,
} from "../lib/connectGuide";

const SECTION_CLS =
  "mb-1.5 mt-3 border-t border-border pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/80";

// ── The How to connect panel ────────────────────────────────────────────────

export function HowToConnectPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <CollapsibleSection label="How to connect" defaultOpen={defaultOpen} keepMounted className="mb-2">
      <div className="space-y-3 text-[11px] leading-snug text-muted-foreground">
        {GUIDE_LISTS.map((list) => (
          <div key={list.id}>
            <p className="mb-0.5 font-medium text-foreground">{list.title}</p>
            <ol className="list-decimal space-y-0.5 pl-4">
              {list.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            {list.id === "microsoft-365" && (
              <div className="mt-1.5 rounded border border-border bg-background/60 p-2">
                <p className="select-all break-words text-foreground">{ADMIN_NOTE}</p>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  icon={copied ? "Check" : "Copy"}
                  className="mt-1.5"
                  onClick={() => {
                    void navigator.clipboard?.writeText(ADMIN_NOTE).then(
                      () => setCopied(true),
                      () => setCopied(false),
                    );
                  }}
                >
                  {copied ? "Copied" : "Copy note for your administrator"}
                </Button>
              </div>
            )}
          </div>
        ))}
        <div>
          <p className="mb-0.5 font-medium text-foreground">{SYNC_TITLE}</p>
          <ul className="list-disc space-y-0.5 pl-4">
            {SYNC_NOTES.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-0.5 font-medium text-foreground">{ALSO_SYNC_TITLE}</p>
          <ol className="list-decimal space-y-0.5 pl-4">
            {ALSO_SYNC_STEPS.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ol>
        </div>
        <p className="text-[10px]">{FULL_GUIDE_LINE}</p>
      </div>
    </CollapsibleSection>
  );
}

// ── The buttons ─────────────────────────────────────────────────────────────

export function ConnectButtons({
  providers,
  connecting,
  onConnect,
}: {
  providers: Provider[];
  connecting: Provider | null;
  onConnect: (p: Provider) => void;
}) {
  if (providers.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {providers.map((p) => (
        <Button
          key={p}
          type="button"
          variant="secondary"
          size="sm"
          icon="Calendar"
          loading={connecting === p}
          disabled={connecting !== null}
          onClick={() => onConnect(p)}
        >
          {CONNECT_LABEL[p]}
        </Button>
      ))}
    </div>
  );
}

// ── The pure face of the section ────────────────────────────────────────────

export interface SectionViewProps {
  list: ConnectionsList | null | undefined;
  /** What the redirect back said, or null. */
  notice: ReturnNotice | null;
  /** A failed Connect press, as a sentence. */
  connectError: string | null;
  connecting: Provider | null;
  /** The id of the row that syncs now. */
  syncingId: string | null;
  /** The line under each row after a Sync now. */
  syncLines: Record<string, string>;
  now: number;
  onConnect: (p: Provider) => void;
  onSync: (row: CalendarConnection) => void;
  onAskDisconnect: (row: CalendarConnection) => void;
  onDismissNotice: () => void;
  /** Open the How to connect guide expanded. */
  guideOpen?: boolean;
}

function Banner({ tone, children, onDismiss }: { tone: "success" | "error"; children: React.ReactNode; onDismiss: () => void }) {
  const cls =
    tone === "success"
      ? "border-success/30 bg-success/10 text-success"
      : "border-destructive/30 bg-destructive/10 text-destructive";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`mb-2 flex items-start gap-2 rounded-md border px-2 py-1.5 text-[11px] ${cls}`}>
      <span className="min-w-0 flex-1">{children}</span>
      <Button type="button" variant="text" size="icon-xs" radius="keep" layout="" aria-label="Dismiss" icon="X" onClick={onDismiss} />
    </div>
  );
}

function Row({
  row,
  now,
  syncing,
  connecting,
  line,
  onConnect,
  onSync,
  onAskDisconnect,
}: {
  row: CalendarConnection;
  now: number;
  syncing: boolean;
  connecting: Provider | null;
  line: string | undefined;
  onConnect: (p: Provider) => void;
  onSync: (row: CalendarConnection) => void;
  onAskDisconnect: (row: CalendarConnection) => void;
}) {
  const state = rowState(row);
  const failing = hasSyncError(row);
  return (
    <li className="mb-2 rounded-md border border-border bg-background/60 p-2">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-medium text-foreground">{PROVIDER_NAME[row.provider]}</span>
        <Badge tone={state === "needs_reconnect" || failing ? "warning" : "success"}>
          {state === "needs_reconnect" ? "Sign in again" : failing ? "Sync problem" : "Connected"}
        </Badge>
      </div>
      {state === "needs_reconnect" ? (
        <p className="mb-1 text-[11px] text-warning">{reconnectLine(row.provider)}</p>
      ) : (
        <p className={`mb-1 break-words text-[11px] ${failing ? "text-warning" : "text-muted-foreground"}`}>{connectedLine(row)}</p>
      )}
      <p className="mb-1.5 text-[10px] text-muted-foreground">{lastSyncLine(row, now)}</p>
      <div className="flex flex-wrap gap-2">
        {state === "needs_reconnect" ? (
          <Button
            type="button"
            variant="primary"
            size="sm"
            loading={connecting === row.provider}
            disabled={connecting !== null}
            onClick={() => onConnect(row.provider)}
          >
            Reconnect
          </Button>
        ) : (
          <Button type="button" variant="secondary" size="sm" icon="RefreshCw" loading={syncing} onClick={() => onSync(row)}>
            Sync now
          </Button>
        )}
        <Button type="button" variant="destructive" size="sm" onClick={() => onAskDisconnect(row)}>
          Disconnect
        </Button>
      </div>
      {line && (
        <p role="status" className="mt-1 text-[10px] text-muted-foreground">
          {line}
        </p>
      )}
    </li>
  );
}

export function ConnectedCalendarsView(p: SectionViewProps) {
  if (!isEnabled(p.list)) return null;
  const list = p.list;
  const offer = connectable(list);
  const err = p.notice?.kind === "error" ? errorCopy(p.notice.code, p.notice.provider) : null;
  return (
    <section aria-label="Connected calendars">
      <p className={SECTION_CLS}>Connected calendars</p>
      {p.notice?.kind === "connected" && (
        <Banner tone="success" onDismiss={p.onDismissNotice}>
          {PROVIDER_NAME[p.notice.provider]} is connected. Your events appear in about a minute.
        </Banner>
      )}
      {err && (
        <Banner tone="error" onDismiss={p.onDismissNotice}>
          {err.text}
        </Banner>
      )}
      {p.connectError && (
        <p role="alert" className="mb-2 text-[11px] text-destructive">
          {p.connectError}
        </p>
      )}
      {list.connections.length > 0 && (
        <ul>
          {list.connections.map((row) => (
            <Row
              key={row.id}
              row={row}
              now={p.now}
              syncing={p.syncingId === row.id}
              connecting={p.connecting}
              line={p.syncLines[row.id]}
              onConnect={p.onConnect}
              onSync={p.onSync}
              onAskDisconnect={p.onAskDisconnect}
            />
          ))}
        </ul>
      )}
      {list.connections.length === 0 && offer.length > 0 && (
        <p className="mb-2 text-[11px] text-muted-foreground">
          Bring your Google or Outlook events into Calendar. Metorite writes your timed tasks to a calendar named Metorite.
        </p>
      )}
      <AlsoSyncCalendar className="mb-2" />
      <div className="mb-2">
        <ConnectButtons
          providers={offer.filter((pr) => !list.connections.some((r) => r.provider === pr))}
          connecting={p.connecting}
          onConnect={p.onConnect}
        />
      </div>
      <HowToConnectPanel defaultOpen={p.guideOpen} />
    </section>
  );
}

// ── The shared wiring ───────────────────────────────────────────────────────

/** The "Connected calendars" section of the settings sheet. */
export function ConnectedCalendarsSection({
  notice,
  onDismissNotice,
  guideOpen = false,
}: {
  notice: ReturnNotice | null;
  onDismissNotice: () => void;
  guideOpen?: boolean;
}) {
  const res = useCalendarConnections();
  const { connecting, error, connect } = useConnect();
  const toast = useToast();
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [syncLines, setSyncLines] = useState<Record<string, string>>({});
  const [asking, setAsking] = useState<CalendarConnection | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // The "Last sync" line ages while the sheet stays open.
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  async function sync(row: CalendarConnection) {
    setSyncingId(row.id);
    const r = await syncNow(row.id);
    setSyncLines((m) => ({ ...m, [row.id]: r.kind === "ok" ? syncResultLine(r.result) : r.message }));
    setSyncingId(null);
    setNow(Date.now());
    refreshConnections();
  }

  async function confirmDisconnect() {
    if (!asking) return;
    const row = asking;
    setBusy(true);
    const r = await disconnect(row.id);
    setBusy(false);
    setAsking(null);
    if (r.kind === "ok") {
      // The "is connected" banner would contradict the toast.
      onDismissNotice();
      toast.show({ key: "calendar-disconnect", variant: "success", title: `${PROVIDER_NAME[row.provider]} is disconnected.` });
    } else {
      toast.show({ key: "calendar-disconnect", variant: "error", title: r.message });
    }
    refreshConnections();
  }

  const copy = asking ? disconnectCopy(asking.provider) : null;
  return (
    <>
      <ConnectedCalendarsView
        list={res.data}
        notice={notice}
        connectError={error}
        connecting={connecting}
        syncingId={syncingId}
        syncLines={syncLines}
        now={now}
        onConnect={(p) => void connect(p)}
        onSync={(row) => void sync(row)}
        onAskDisconnect={setAsking}
        onDismissNotice={onDismissNotice}
        guideOpen={guideOpen}
      />
      <ConfirmDialog
        open={asking !== null}
        title={copy?.title ?? "Disconnect?"}
        body={copy?.body ?? ""}
        confirmLabel="Disconnect"
        confirmVariant="destructive"
        busy={busy}
        onConfirm={() => void confirmDisconnect()}
        onCancel={() => setAsking(null)}
      />
    </>
  );
}

/**
 * The offer on the Calendar page: the same buttons, only when enabled and nothing is connected.
 * A provider with a mailbox offer shows the offer (CE-10d) and not the generic prompt.
 */
export function CalendarConnectPrompt({ onOpenGuide }: { onOpenGuide?: () => void }) {
  const res = useCalendarConnections();
  const { connecting, error, connect } = useConnect();
  if (!showsPrompt(res.data)) return null;
  const offered = new Set(visibleOffers(res.data).map((o) => o.provider));
  const generic = connectable(res.data).filter((p) => !offered.has(p));
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
      <AlsoSyncCalendar className="basis-full" />
      {generic.length > 0 && (
        <>
          <span className="min-w-0 flex-1 basis-48">
            {offered.size > 0
              ? `Bring your ${generic.map((p) => PROVIDER_NAME[p]).join(" or ")} events into Calendar.`
              : "Bring your Google or Outlook events into Calendar."}
          </span>
          <ConnectButtons providers={generic} connecting={connecting} onConnect={(p) => void connect(p)} />
        </>
      )}
      {onOpenGuide && (
        <Button type="button" variant="text" size="sm" onClick={onOpenGuide}>
          How to connect
        </Button>
      )}
      {error && (
        <p role="alert" className="basis-full text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * Read the return of the consent once: `?calendar_connected=` or `?calendar_error=`, and
 * `?settings=1` from the Integrations console. Hand the notice to the page, open the settings
 * sheet, refresh the list, and clean the address. Anybody can craft the link, so the notice is
 * only words. It starts no act.
 */
export function CalendarConnectReturn({
  onNotice,
  onOpenSettings,
}: {
  onNotice: (n: ReturnNotice) => void;
  onOpenSettings: () => void;
}) {
  const params = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    const notice = returnNotice(params, params.get("calendar_error") !== null ? recallStarted() : null);
    const settings = wantsSettings(params);
    if (!notice && !settings) return;
    done.current = true;
    if (notice) onNotice(notice);
    onOpenSettings();
    refreshConnections();
    router.replace(cleanedHref(pathname || "/calendar", params), { scroll: false });
  }, [params, pathname, router, onNotice, onOpenSettings]);
  return null;
}
