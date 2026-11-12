/**
 * Connect Google Calendar and Outlook (WS-CAL CE-10c, task 66).
 *
 * One typed client for the calendar connection routes. The browser calls the BFF under
 * `/api/calendar/...`. The BFF calls the gateway under `/tasks/calendar/...`. Every route answers
 * 404 while the gateway flag is off, and a 404 on the list means "not enabled": the whole Connect
 * UI draws nothing.
 *
 * This module is pure of React. The fetch layer takes an injectable `fetch`, as
 * `src/lib/studio/packageLifetime.ts` does. The helpers (row state, copy for each error code,
 * relative time, id and provider checks) have no side effects.
 *
 * Fence: `calendarConnections.test.ts`.
 */
import { invalidate } from "@/lib/dataCache";

import type { Provider } from "./calendarIds";
export type { Provider };
export type RowStatus = "connected" | "needs_reconnect" | "disconnected";

export interface CalendarConnection {
  id: string;
  provider: Provider;
  account_email: string;
  status: RowStatus;
  last_sync_at: string | null;
  last_error: string | null;
  metorite_calendar: boolean;
  entry_count: number;
}

export interface ConnectionsList {
  enabled: boolean;
  providers: Record<Provider, { available: boolean }>;
  connections: CalendarConnection[];
}

export interface SyncResult {
  status: "ok" | "needs_reconnect" | "busy" | "error";
  added: number;
  updated: number;
  removed: number;
}

export const CONNECTIONS_KEY = "calendar:connections";
export const PROVIDERS: readonly Provider[] = ["google", "outlook"];

/** Drop the cached list, so the next read asks the gateway. */
export function refreshConnections(): void {
  invalidate(CONNECTIONS_KEY);
}

export { isConnectionId, isProvider, gatewayProviderOf } from "./calendarIds";
import { isConnectionId, isProvider } from "./calendarIds";

// ── Words ───────────────────────────────────────────────────────────────────

export const PROVIDER_NAME: Record<Provider, string> = { google: "Google", outlook: "Outlook" };
export const CONNECT_LABEL: Record<Provider, string> = {
  google: "Connect Google Calendar",
  outlook: "Connect Outlook",
};

/** How a row reads: its state, never a guess. */
export function rowState(row: Pick<CalendarConnection, "status">): RowStatus {
  return row.status === "needs_reconnect" || row.status === "disconnected" ? row.status : "connected";
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Does the row carry a failed sync? The engine keeps the status `connected` and sets `last_error`. */
export function hasSyncError(row: Pick<CalendarConnection, "last_error" | "status">): boolean {
  return rowState(row) === "connected" && !!row.last_error;
}

/** The line of a row whose last sync failed: the fact, then the cause and fix. */
export function syncFailedLine(row: CalendarConnection): string {
  return `Last sync failed. ${errorCopy(row.last_error, row.provider, "sync").text}`;
}

/** The line of a connected row. The wire gives an event count and no calendar count. */
export function connectedLine(row: CalendarConnection): string {
  if (hasSyncError(row)) return `Connected as ${row.account_email}. ${syncFailedLine(row)}`;
  const reading = `Reading your calendars, ${plural(row.entry_count, "event", "events")} so far.`;
  const tasks = row.metorite_calendar
    ? "Your tasks go to the 'Metorite' calendar."
    : "Your tasks go to a 'Metorite' calendar when you schedule one.";
  return `Connected as ${row.account_email}. ${reading} ${tasks}`;
}

export function reconnectLine(provider: Provider): string {
  return `${PROVIDER_NAME[provider]} needs you to sign in again. Press Reconnect.`;
}

const ACCOUNT_PAGE: Record<Provider, string> = {
  google: "myaccount.google.com/permissions",
  outlook: "myapps.microsoft.com",
};

/**
 * Cause and fix for a code the gateway sends. The same table serves the redirect
 * (`calendar_error`, `context: "connect"`) and a row's `last_error` (`context: "sync"`).
 * The provider is null when the redirect does not say which one. Real codes: see
 * `calendar_connections.py` (callback) and `calendar_sync/google.py` and `outlook.py`.
 */
export function errorCopy(
  code: string | null | undefined,
  provider: Provider | null = null,
  context: "connect" | "sync" = "connect",
): { text: string } {
  const who = provider ? PROVIDER_NAME[provider] : "Your calendar provider";
  const page = provider ? ACCOUNT_PAGE[provider] : "your Google or Microsoft account page";
  const c = (code ?? "").toLowerCase();
  const text = (t: string) => ({ text: t });
  if (c === "consent_declined" || c === "access_denied" || c === "needs_admin") {
    const admin =
      provider === "google"
        ? "A company Google account may need an administrator to allow Metorite."
        : provider === "outlook"
          ? "A work or school account may need an administrator to approve Metorite."
          : "A work or school account may need an administrator to allow Metorite.";
    return text(`The sign-in was cancelled or blocked. ${admin} See How to connect. Then press Connect.`);
  }
  if (c === "scope_missing") return text("The consent did not include calendar access. Press Connect and allow every box.");
  if (c === "scope_extra") {
    return text(`${who} gave Metorite more access than it asks for. Remove Metorite at ${page}, then connect again.`);
  }
  if (c === "no_refresh_token") {
    return text(`${who} did not send a long-lived sign-in. Remove Metorite at ${page}, then connect again.`);
  }
  if (c === "needs_reconnect" || c === "invalid_grant" || c === "consent_required" || c === "interaction_required" || /^token_\d+$/.test(c)) {
    return text(`${who} needs you to sign in again. Press Reconnect.`);
  }
  if (c === "forbidden" || c === "unauthorized_client" || c === "invalid_scope") {
    return text(`${who} does not let Metorite read this calendar. Ask your administrator to allow Metorite. See How to connect.`);
  }
  if (/^rate_limited_\d+$/.test(c)) return text(`${who} asks Metorite to slow down. The next sync tries again on its own.`);
  if (c === "server_error" || /^graph_\d+$/.test(c) || c === "bad_reply" || c === "bad_answer" || c === "bad_request") {
    return text(`${who} had a problem. The next sync tries again in a minute.`);
  }
  if (["too_many_pages", "response_too_large", "delta_incomplete", "delta_paging", "no_sync_token"].includes(c)) {
    return text("This calendar is too large to read in one pass. Press Sync now. If it repeats, contact support.");
  }
  if (["no_calendar", "no_calendar_id", "calendar_not_created", "event_not_saved", "no_event_id", "not_found"].includes(c)) {
    return text("Metorite could not reach the 'Metorite' calendar. Press Sync now. If it repeats, press Reconnect.");
  }
  if (c === "identity_missing" || c === "account_unreadable") {
    return text(`Metorite could not read your ${who} account address. Press Connect again.`);
  }
  return text(context === "sync" ? "The last sync did not work. Press Sync now. Try again in a minute." : "Connecting did not work. Try again.");
}

export function disconnectCopy(provider: Provider): { title: string; body: string } {
  const name = PROVIDER_NAME[provider];
  return {
    title: `Disconnect ${name}?`,
    body: `The ${name} events leave Metorite. Your tasks stay. The Metorite calendar in ${name} stays until you delete it.`,
  };
}

/** "5 min ago". The clock is a parameter, so a test can fix it. */
export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "never";
  const secs = Math.max(0, Math.round((now - at) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export function lastSyncLine(row: CalendarConnection, now: number = Date.now()): string {
  return `Last sync ${relativeTime(row.last_sync_at, now)}`;
}

/** The line under a Sync now press. */
export function syncResultLine(r: SyncResult): string {
  switch (r.status) {
    case "ok":
      return `Added ${r.added}, changed ${r.updated}, removed ${r.removed}`;
    case "needs_reconnect":
      return "The sign-in ended. Press Reconnect.";
    case "busy":
      return "A sync is already running. Try again in a minute.";
    default:
      return "The sync did not work. Try again in a minute.";
  }
}

// ── The page the browser returns to ─────────────────────────────────────────

export type ReturnNotice =
  | { kind: "connected"; provider: Provider }
  | { kind: "error"; code: string; provider: Provider | null };

const CODE = /^[a-z0-9_]{1,64}$/;

/** Read `calendar_connected` or `calendar_error` from the URL. Anything odd gives null or "failed". */
export function returnNotice(search: URLSearchParams, started: Provider | null = null): ReturnNotice | null {
  const ok = search.get("calendar_connected");
  if (isProvider(ok)) return { kind: "connected", provider: ok };
  const err = search.get("calendar_error");
  if (err !== null) return { kind: "error", code: CODE.test(err) ? err : "failed", provider: started };
  return null;
}

const RETURN_KEYS = ["calendar_connected", "calendar_error", "settings"];

/** The path and query that remain once the return keys are gone. */
export function cleanedHref(pathname: string, search: URLSearchParams): string {
  const rest = new URLSearchParams(search);
  for (const k of RETURN_KEYS) rest.delete(k);
  const qs = rest.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

/** Does the URL ask for the settings sheet? (`/calendar?settings=1`, from the Integrations console.) */
export function wantsSettings(search: URLSearchParams): boolean {
  return search.get("settings") === "1";
}

// ── What the section shows ──────────────────────────────────────────────────

/** The providers that can show a Connect button now. Empty when the feature is off. */
export function connectable(list: ConnectionsList | null | undefined): Provider[] {
  if (!list || !list.enabled) return [];
  return PROVIDERS.filter((p) => list.providers[p]?.available);
}

/** Show the Connect UI at all? */
export function isEnabled(list: ConnectionsList | null | undefined): list is ConnectionsList {
  return !!list && list.enabled === true;
}

/** Show the prompt on the Calendar page: enabled, a button to offer and no live row. */
export function showsPrompt(list: ConnectionsList | null | undefined): boolean {
  return connectable(list).length > 0 && (list?.connections.length ?? 0) === 0;
}

// ── The wire ────────────────────────────────────────────────────────────────

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

function parseRow(v: unknown): CalendarConnection | null {
  if (!isObject(v) || !isConnectionId(v.id) || !isProvider(v.provider)) return null;
  const status = v.status;
  if (status !== "connected" && status !== "needs_reconnect" && status !== "disconnected") return null;
  return {
    id: v.id,
    provider: v.provider,
    account_email: typeof v.account_email === "string" ? v.account_email : "",
    status,
    last_sync_at: typeof v.last_sync_at === "string" ? v.last_sync_at : null,
    last_error: typeof v.last_error === "string" ? v.last_error : null,
    metorite_calendar: v.metorite_calendar === true,
    entry_count: typeof v.entry_count === "number" ? v.entry_count : 0,
  };
}

/** The list of the wire, or null when the body is not one. A disconnected row is dropped. */
export function parseList(body: unknown): ConnectionsList | null {
  if (!isObject(body) || typeof body.enabled !== "boolean") return null;
  const p = isObject(body.providers) ? body.providers : {};
  const avail = (k: string) => isObject(p[k]) && (p[k] as Record<string, unknown>).available === true;
  const rows = Array.isArray(body.connections) ? body.connections : [];
  return {
    enabled: body.enabled,
    providers: { google: { available: avail("google") }, outlook: { available: avail("outlook") } },
    connections: rows.map(parseRow).filter((r): r is CalendarConnection => !!r && r.status !== "disconnected"),
  };
}

/**
 * Read the list. A 404 gives null ("not enabled"). Any other failure throws, so the cache keeps
 * the last good list on screen.
 */
export async function getConnections(fetchImpl: typeof fetch = fetch): Promise<ConnectionsList | null> {
  const res = await fetchImpl("/api/calendar/connections", { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Calendar connections ${res.status}`);
  const list = parseList(await res.json().catch(() => null));
  if (!list) throw new Error("Calendar connections: bad answer");
  return list;
}

export type StartResult = { kind: "ok"; url: string } | { kind: "error"; message: string };

export const CONNECT_FAILED_LINE = "Connecting did not work. Try again.";
export const OFFLINE_LINE = "The request did not go through. Check your connection and try again.";
const SYNC_FAILED_LINE = "The sync did not work. Try again in a minute.";
const DISCONNECT_FAILED_LINE = "Disconnect did not work. Try again.";

/** Ask for the consent URL. The caller sends the browser there. */
export async function startConnect(provider: Provider, fetchImpl: typeof fetch = fetch): Promise<StartResult> {
  try {
    const res = await fetchImpl(`/api/calendar/connections/${provider}/connect`, { method: "POST" });
    if (!res.ok) return { kind: "error", message: CONNECT_FAILED_LINE };
    const body: unknown = await res.json().catch(() => null);
    const url = isObject(body) && typeof body.url === "string" ? body.url : "";
    return url ? { kind: "ok", url } : { kind: "error", message: CONNECT_FAILED_LINE };
  } catch {
    return { kind: "error", message: OFFLINE_LINE };
  }
}

export type SyncOutcome = { kind: "ok"; result: SyncResult } | { kind: "error"; message: string };

export async function syncNow(id: string, fetchImpl: typeof fetch = fetch): Promise<SyncOutcome> {
  if (!isConnectionId(id)) return { kind: "error", message: SYNC_FAILED_LINE };
  try {
    const res = await fetchImpl(`/api/calendar/connections/${id}/sync`, { method: "POST" });
    const body: unknown = await res.json().catch(() => null);
    if (!res.ok || !isObject(body) || typeof body.status !== "string") {
      return { kind: "error", message: SYNC_FAILED_LINE };
    }
    const n = (k: string) => (typeof body[k] === "number" ? (body[k] as number) : 0);
    return {
      kind: "ok",
      result: {
        status: body.status as SyncResult["status"],
        added: n("added"),
        updated: n("updated"),
        removed: n("removed"),
      },
    };
  } catch {
    return { kind: "error", message: OFFLINE_LINE };
  }
}

export type DisconnectOutcome = { kind: "ok"; removed: number } | { kind: "error"; message: string };

export async function disconnect(id: string, fetchImpl: typeof fetch = fetch): Promise<DisconnectOutcome> {
  if (!isConnectionId(id)) return { kind: "error", message: DISCONNECT_FAILED_LINE };
  try {
    const res = await fetchImpl(`/api/calendar/connections/${id}`, { method: "DELETE" });
    if (!res.ok) return { kind: "error", message: DISCONNECT_FAILED_LINE };
    const body: unknown = await res.json().catch(() => null);
    return { kind: "ok", removed: isObject(body) && typeof body.removed_entries === "number" ? body.removed_entries : 0 };
  } catch {
    return { kind: "error", message: OFFLINE_LINE };
  }
}

const STARTED_KEY = "calendar:connect-started";

/** Remember which provider the member started, so the return can word its error. */
export function rememberStarted(p: Provider): void {
  try {
    sessionStorage.setItem(STARTED_KEY, p);
  } catch {
    /* private mode: the error text is then provider-neutral */
  }
}

export function recallStarted(): Provider | null {
  try {
    const v = sessionStorage.getItem(STARTED_KEY);
    sessionStorage.removeItem(STARTED_KEY);
    return isProvider(v) ? v : null;
  } catch {
    return null;
  }
}
