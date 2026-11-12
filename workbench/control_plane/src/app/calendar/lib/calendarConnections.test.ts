// WS-CAL CE-10c: the pure helpers and the thin fetch layer of the calendar connections.
import { describe, expect, it } from "vitest";

import { safeConsentUrl } from "./calendarIds";
import {
  cleanedHref,
  connectable,
  connectedLine,
  hasSyncError,
  syncFailedLine,
  disconnect,
  disconnectCopy,
  errorCopy,
  gatewayProviderOf,
  getConnections,
  isConnectionId,
  isProvider,
  lastSyncLine,
  parseList,
  reconnectLine,
  relativeTime,
  returnNotice,
  rowState,
  showsPrompt,
  startConnect,
  syncNow,
  syncResultLine,
  wantsSettings,
  type CalendarConnection,
} from "./calendarConnections";

const ID = "3f1c2a9e-1b2d-4c3e-8f4a-5b6c7d8e9f01";
const NOW = Date.parse("2026-11-12T12:00:00Z");

function row(over: Partial<CalendarConnection> = {}): CalendarConnection {
  return {
    id: ID,
    provider: "google",
    account_email: "ada@example.com",
    status: "connected",
    last_sync_at: "2026-11-12T11:55:00Z",
    last_error: null,
    metorite_calendar: true,
    entry_count: 12,
    ...over,
  };
}

const wire = (over: Record<string, unknown> = {}) => ({
  enabled: true,
  providers: { google: { available: true }, outlook: { available: true } },
  connections: [row()],
  ...over,
});

const reply = (status: number, body: unknown) =>
  (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("id and provider checks", () => {
  it("accepts a UUID only", () => {
    expect(isConnectionId(ID)).toBe(true);
    for (const bad of ["", "google", "..", `${ID}/x`, `${ID}?a=1`, "1", null, 5]) expect(isConnectionId(bad)).toBe(false);
  });
  it("accepts the two provider words only", () => {
    expect(isProvider("google")).toBe(true);
    expect(isProvider("outlook")).toBe(true);
    for (const bad of ["microsoft", "Google", "", "google/", null]) expect(isProvider(bad)).toBe(false);
  });
  it("maps the browser word microsoft to outlook", () => {
    expect(gatewayProviderOf("google")).toBe("google");
    expect(gatewayProviderOf("microsoft")).toBe("outlook");
    expect(gatewayProviderOf("outlook")).toBeNull();
    expect(gatewayProviderOf("..")).toBeNull();
  });
});

describe("the state of a row", () => {
  it("reads the three states, and treats an odd value as connected", () => {
    expect(rowState({ status: "connected" })).toBe("connected");
    expect(rowState({ status: "needs_reconnect" })).toBe("needs_reconnect");
    expect(rowState({ status: "disconnected" })).toBe("disconnected");
  });
  it("words a connected row from the wire only", () => {
    expect(connectedLine(row())).toBe(
      "Connected as ada@example.com. Reading your calendars, 12 events so far. Your tasks go to the 'Metorite' calendar.",
    );
    expect(connectedLine(row({ entry_count: 1, metorite_calendar: false }))).toContain("1 event so far");
    expect(connectedLine(row({ metorite_calendar: false }))).toContain("when you schedule one");
  });
  it("words the sign-in again line", () => {
    expect(reconnectLine("google")).toBe("Google needs you to sign in again. Press Reconnect.");
    expect(reconnectLine("outlook")).toBe("Outlook needs you to sign in again. Press Reconnect.");
  });
});

describe("copy for each error code", () => {
  it("words consent_declined, with the admin steps for a work account", () => {
    const g = errorCopy("consent_declined", "google").text;
    const o = errorCopy("consent_declined", "outlook").text;
    expect(g).toContain("cancelled or blocked");
    expect(g).toContain("See How to connect");
    expect(o).toContain("work or school account may need an administrator to approve Metorite");
    expect(o).toContain("See How to connect");
    // The dead codes are aliases of the real one.
    expect(errorCopy("access_denied", "outlook").text).toBe(o);
    expect(errorCopy("needs_admin", "outlook").text).toBe(o);
  });
  it("names the right account page for each provider", () => {
    expect(errorCopy("no_refresh_token", "google").text).toBe(
      "Google did not send a long-lived sign-in. Remove Metorite at myaccount.google.com/permissions, then connect again.",
    );
    const o = errorCopy("no_refresh_token", "outlook").text;
    expect(o).toContain("Remove Metorite at myapps.microsoft.com, then connect again.");
    expect(o).not.toContain("google");
    expect(errorCopy("scope_extra", "outlook").text).not.toContain("google");
  });
  it("covers every real gateway code with a cause and a fix", () => {
    const codes = [
      "scope_missing", "scope_extra", "no_refresh_token", "no_code", "exchange_failed", "forbidden",
      "too_many_pages", "no_sync_token", "response_too_large", "delta_incomplete", "delta_paging",
      "rate_limited_429", "graph_503", "token_400", "invalid_grant", "server_error", "bad_request",
      "bad_answer", "bad_reply", "not_found", "no_calendar", "no_calendar_id", "calendar_not_created",
      "event_not_saved", "no_event_id", "identity_missing", "account_unreadable", "needs_reconnect",
    ];
    for (const code of codes) {
      const t = errorCopy(code, "outlook").text;
      expect(t.length, code).toBeGreaterThan(20);
      expect(t, code).not.toContain("google");
    }
    expect(errorCopy("scope_missing").text).toBe(
      "The consent did not include calendar access. Press Connect and allow every box.",
    );
    expect(errorCopy("forbidden", "google").text).toContain("Ask your administrator");
  });
  it("gives a short plain line for an unknown code", () => {
    for (const code of ["failed", "invalid_state", "", null, undefined, "<script>"]) {
      expect(errorCopy(code).text).toBe("Connecting did not work. Try again.");
    }
    expect(errorCopy("zzz", "google", "sync").text).toContain("Press Sync now");
  });
  it("words a row with a failed sync", () => {
    const bad = row({ last_error: "forbidden" });
    expect(hasSyncError(bad)).toBe(true);
    expect(hasSyncError(row())).toBe(false);
    expect(hasSyncError(row({ status: "needs_reconnect", last_error: "x" }))).toBe(false);
    expect(syncFailedLine(bad)).toBe(`Last sync failed. ${errorCopy("forbidden", "google", "sync").text}`);
    expect(connectedLine(bad)).not.toContain("Reading your calendars");
    expect(connectedLine(bad)).toContain("Last sync failed.");
  });
});

describe("the consent URL allowlist", () => {
  it("accepts the exact host of the provider only", () => {
    expect(safeConsentUrl("google", "https://accounts.google.com/o/oauth2/v2/auth?state=s")).toContain("accounts.google.com");
    expect(safeConsentUrl("outlook", "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?x=1")).toContain("login.microsoftonline.com");
  });
  it("refuses a look-alike host, a wrong-provider host, http, userinfo and a port", () => {
    for (const bad of [
      "https://accounts.google.com.evil.test/a",
      "https://evilaccounts.google.com/a",
      "https://accounts.google.com@evil.test/a",
      "https://user:pw@accounts.google.com/a",
      "https://accounts.google.com:8443/a",
      "http://accounts.google.com/a",
      "https://login.microsoftonline.com/a",
      "https://example.com/a",
      "javascript:alert(1)",
      "",
      5,
    ]) {
      expect(safeConsentUrl("google", bad), String(bad)).toBeNull();
    }
    expect(safeConsentUrl("outlook", "https://accounts.google.com/a")).toBeNull();
  });
});

describe("disconnect copy", () => {
  it("names the provider", () => {
    expect(disconnectCopy("google").body).toBe(
      "The Google events leave Metorite. Your tasks stay. The Metorite calendar in Google stays until you delete it.",
    );
    expect(disconnectCopy("outlook").body).toBe(
      "The Outlook events leave Metorite. Your tasks stay. The Metorite calendar in Outlook stays until you delete it.",
    );
  });
});

describe("relative time", () => {
  it("reads in seconds, minutes, hours and days", () => {
    const at = (ms: number) => new Date(NOW - ms).toISOString();
    expect(relativeTime(at(10_000), NOW)).toBe("just now");
    expect(relativeTime(at(5 * 60_000), NOW)).toBe("5 min ago");
    expect(relativeTime(at(3 * 3_600_000), NOW)).toBe("3 h ago");
    expect(relativeTime(at(30 * 3_600_000), NOW)).toBe("yesterday");
    expect(relativeTime(at(4 * 86_400_000), NOW)).toBe("4 days ago");
  });
  it("reads never for a missing or bad time, and just now for a future time", () => {
    expect(relativeTime(null, NOW)).toBe("never");
    expect(relativeTime("not a date", NOW)).toBe("never");
    expect(relativeTime(new Date(NOW + 60_000).toISOString(), NOW)).toBe("just now");
  });
  it("words the last sync line", () => {
    expect(lastSyncLine(row(), NOW)).toBe("Last sync 5 min ago");
    expect(lastSyncLine(row({ last_sync_at: null }), NOW)).toBe("Last sync never");
  });
});

describe("the Sync now result", () => {
  it("words the counts and each refusal", () => {
    expect(syncResultLine({ status: "ok", added: 3, updated: 1, removed: 0 })).toBe("Added 3, changed 1, removed 0");
    expect(syncResultLine({ status: "needs_reconnect", added: 0, updated: 0, removed: 0 })).toContain("Reconnect");
    expect(syncResultLine({ status: "busy", added: 0, updated: 0, removed: 0 })).toContain("already running");
    expect(syncResultLine({ status: "error", added: 0, updated: 0, removed: 0 })).toContain("did not work");
  });
});

describe("the return from the consent", () => {
  const q = (s: string) => new URLSearchParams(s);
  it("reads a connect and an error", () => {
    expect(returnNotice(q("calendar_connected=google"))).toEqual({ kind: "connected", provider: "google" });
    expect(returnNotice(q("calendar_connected=outlook"))).toEqual({ kind: "connected", provider: "outlook" });
    expect(returnNotice(q("calendar_error=needs_admin"))).toEqual({ kind: "error", code: "needs_admin", provider: null });
  });
  it("carries the provider the member started", () => {
    expect(returnNotice(q("calendar_error=consent_declined"), "outlook")).toEqual({ kind: "error", code: "consent_declined", provider: "outlook" });
  });
  it("ignores a crafted value", () => {
    expect(returnNotice(q("calendar_connected=evil"))).toBeNull();
    expect(returnNotice(q("calendar_error=<b>x</b>"))).toEqual({ kind: "error", code: "failed", provider: null });
    expect(returnNotice(q("date=2026-11-12"))).toBeNull();
  });
  it("cleans the three keys and keeps the rest", () => {
    expect(cleanedHref("/calendar", q("calendar_connected=google&settings=1"))).toBe("/calendar");
    expect(cleanedHref("/calendar", q("calendar_error=x&date=2026-11-12"))).toBe("/calendar?date=2026-11-12");
  });
  it("sees the settings link", () => {
    expect(wantsSettings(q("settings=1"))).toBe(true);
    expect(wantsSettings(q("settings=0"))).toBe(false);
  });
});

describe("the wire", () => {
  it("parses the list and drops a disconnected row", () => {
    const list = parseList(wire({ connections: [row(), row({ id: "3f1c2a9e-1b2d-4c3e-8f4a-5b6c7d8e9f02", status: "disconnected" })] }));
    expect(list?.connections).toHaveLength(1);
    expect(connectable(list)).toEqual(["google", "outlook"]);
  });
  it("drops a row with a bad id or provider", () => {
    const list = parseList(wire({ connections: [row({ id: "../x" }), row({ provider: "icloud" as never })] }));
    expect(list?.connections).toEqual([]);
  });
  it("offers nothing when disabled or unavailable", () => {
    expect(connectable(parseList(wire({ enabled: false })))).toEqual([]);
    expect(connectable(parseList(wire({ providers: { google: { available: false }, outlook: { available: true } } })))).toEqual(["outlook"]);
    expect(connectable(null)).toEqual([]);
  });
  it("shows the page prompt only when enabled with a button and no row", () => {
    expect(showsPrompt(parseList(wire({ connections: [] })))).toBe(true);
    expect(showsPrompt(parseList(wire()))).toBe(false);
    expect(showsPrompt(parseList(wire({ enabled: false, connections: [] })))).toBe(false);
    expect(showsPrompt(null)).toBe(false);
  });
  it("reads null on a 404 and throws on any other failure", async () => {
    expect(await getConnections(reply(404, { detail: "Not Found" }))).toBeNull();
    await expect(getConnections(reply(500, {}))).rejects.toThrow();
    await expect(getConnections(reply(200, { nope: 1 }))).rejects.toThrow();
    expect((await getConnections(reply(200, wire())))?.connections).toHaveLength(1);
  });
  it("starts a connect, and words each failure", async () => {
    expect(await startConnect("google", reply(200, { url: "https://x.test/a" }))).toEqual({ kind: "ok", url: "https://x.test/a" });
    expect((await startConnect("google", reply(404, {}))).kind).toBe("error");
    expect((await startConnect("google", reply(200, {}))).kind).toBe("error");
    const down = (async () => {
      throw new Error("x");
    }) as unknown as typeof fetch;
    expect(await startConnect("outlook", down)).toMatchObject({ kind: "error" });
  });
  it("syncs and disconnects only a UUID", async () => {
    const seen: string[] = [];
    const spy = (async (url: string) => {
      seen.push(String(url));
      return new Response(JSON.stringify({ status: "ok", added: 2, updated: 0, removed: 1, removed_entries: 4 }));
    }) as unknown as typeof fetch;
    expect(await syncNow(ID, spy)).toEqual({ kind: "ok", result: { status: "ok", added: 2, updated: 0, removed: 1 } });
    expect(await disconnect(ID, spy)).toEqual({ kind: "ok", removed: 4 });
    expect(seen).toEqual([`/api/calendar/connections/${ID}/sync`, `/api/calendar/connections/${ID}`]);
    expect((await syncNow("../x", spy)).kind).toBe("error");
    expect((await disconnect("a/b", spy)).kind).toBe("error");
    expect(seen).toHaveLength(2);
  });
});
