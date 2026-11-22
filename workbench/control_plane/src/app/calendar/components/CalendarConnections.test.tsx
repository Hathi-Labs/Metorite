// WS-CAL CE-10c: the Connected calendars section, drawn. The runner has no DOM, so each face is
// rendered to a string. The helpers are fenced in `../lib/calendarConnections.test.ts`.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { parseList, type CalendarConnection, type ConnectionsList } from "../lib/calendarConnections";
import { ADMIN_NOTE } from "../lib/connectGuide";
import { ConnectedCalendarsView, HowToConnectPanel, type SectionViewProps } from "./CalendarConnections";

const ID = "3f1c2a9e-1b2d-4c3e-8f4a-5b6c7d8e9f01";
const NOW = Date.parse("2026-11-12T12:00:00Z");
const noop = () => undefined;

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
    push_tasks: true,
    ...over,
  };
}

function list(rows: CalendarConnection[], over: Partial<ConnectionsList> = {}): ConnectionsList {
  return {
    enabled: true,
    providers: { google: { available: true }, outlook: { available: true } },
    connections: rows,
    ...over,
  };
}

/** The section without the guide: the guide quotes the button names in its steps. */
const buttons = (html: string) => html.split("How to connect")[0];

function draw(over: Partial<SectionViewProps> = {}): string {
  return renderToStaticMarkup(
    createElement(ConnectedCalendarsView, {
      list: list([]),
      notice: null,
      connectError: null,
      connecting: null,
      syncingId: null,
      syncLines: {},
      now: NOW,
      onConnect: noop,
      onSync: noop,
      onAskDisconnect: noop,
      onDismissNotice: noop,
      ...over,
    }),
  );
}

describe("the Connected calendars section", () => {
  it("draws nothing when the list is not enabled", () => {
    expect(draw({ list: null })).toBe("");
    expect(draw({ list: undefined })).toBe("");
    expect(draw({ list: list([], { enabled: false }) })).toBe("");
  });

  it("offers both buttons when none is connected", () => {
    const html = draw();
    expect(html).toContain("Connected calendars");
    expect(html).toContain("Connect Google Calendar");
    expect(html).toContain("Connect Outlook");
  });

  it("offers only an available provider", () => {
    const html = buttons(draw({ list: list([], { providers: { google: { available: false }, outlook: { available: true } } }) }));
    expect(html).not.toContain("Connect Google Calendar");
    expect(html).toContain("Connect Outlook");
  });

  it("draws a connected row with its words, Sync now and Disconnect", () => {
    const full = draw({ list: list([row()]) });
    const html = buttons(full);
    expect(html).toContain("Connected as ada@example.com.");
    expect(html).toContain("Reading your calendars, 12 events so far.");
    expect(html).toContain("Your tasks go to the &#x27;Metorite&#x27; calendar.");
    expect(html).toContain("Last sync 5 min ago");
    expect(html).toContain("Sync now");
    expect(html).toContain("Disconnect");
    // The provider has a row, so its Connect button is gone. The other one stays.
    expect(html).not.toContain("Connect Google Calendar");
    expect(html).toContain("Connect Outlook");
  });

  it("shows the result line under a row after Sync now", () => {
    const html = draw({ list: list([row()]), syncLines: { [ID]: "Added 3, changed 1, removed 0" } });
    expect(html).toContain("Added 3, changed 1, removed 0");
  });

  it("draws needs_reconnect with Reconnect and no Sync now", () => {
    const html = draw({ list: list([row({ status: "needs_reconnect" })]) });
    expect(html).toContain("Google needs you to sign in again. Press Reconnect.");
    expect(html).toContain("Reconnect");
    expect(html).not.toContain("Sync now");
    expect(html).toContain("Disconnect");
  });

  it("draws the cause and fix of a calendar_error code", () => {
    const html = draw({ notice: { kind: "error", code: "scope_missing", provider: null } });
    expect(html).toContain('role="alert"');
    expect(html).toContain("The consent did not include calendar access. Press Connect and allow every box.");
    const o = draw({ notice: { kind: "error", code: "consent_declined", provider: "outlook" } });
    expect(o).toContain("work or school account may need an administrator to approve Metorite");
    expect(draw({ notice: { kind: "error", code: "no_refresh_token", provider: "outlook" } })).toContain("myapps.microsoft.com");
    expect(draw({ notice: { kind: "error", code: "other", provider: null } })).toContain("Connecting did not work. Try again.");
  });

  it("draws a failing row with the warning badge and the cause, not the green reading line", () => {
    const html = buttons(draw({ list: list([row({ last_error: "too_many_pages" })]) }));
    expect(html).toContain("Sync problem");
    expect(html).toContain("Last sync failed.");
    expect(html).toContain("too large to read in one pass");
    expect(html).not.toContain("Reading your calendars");
    expect(html).not.toContain(">Connected<");
  });

  it("gives Reconnect a loading and disabled state while a connect runs", () => {
    const idle = buttons(draw({ list: list([row({ status: "needs_reconnect" })]) }));
    const busy = buttons(draw({ list: list([row({ status: "needs_reconnect" })]), connecting: "google" }));
    expect(idle).not.toContain('disabled=""');
    expect(busy).toContain('disabled=""');
    expect(busy).not.toBe(idle);
  });

  it("opens the guide expanded when asked", () => {
    const closed = renderToStaticMarkup(createElement(HowToConnectPanel));
    const open = renderToStaticMarkup(createElement(HowToConnectPanel, { defaultOpen: true }));
    expect(open).not.toBe(closed);
    expect(open).toContain('aria-expanded="true"');
    expect(closed).toContain('aria-expanded="false"');
  });

  it("draws the success notice after a connect", () => {
    expect(draw({ notice: { kind: "connected", provider: "outlook" } })).toContain("Outlook is connected.");
  });

  it("draws a failed Connect press", () => {
    expect(draw({ connectError: "Connecting did not work. Try again." })).toContain("Connecting did not work. Try again.");
  });

  it("parses to the same section from the wire", () => {
    const parsed = parseList({ enabled: true, providers: { google: { available: true }, outlook: { available: false } }, connections: [] });
    const html = buttons(draw({ list: parsed }));
    expect(html).toContain("Connect Google Calendar");
    expect(html).not.toContain("Connect Outlook");
  });
});

describe("the How to connect panel", () => {
  const html = renderToStaticMarkup(createElement(HowToConnectPanel));
  it("holds the four lists, the admin note and the sync block", () => {
    expect(html).toContain("How to connect");
    for (const title of ["Google, personal account", "Google Workspace, company account", "Outlook, personal account", "Microsoft 365, work or school account"]) {
      expect(html).toContain(title);
    }
    expect(html).toContain("Manage third-party app access, allow Metorite.");
    expect(html).toContain("Entra admin centre, Enterprise applications, Metorite, Permissions, Grant admin consent.");
    expect(html).toContain("Calendars.ReadWrite, User.Read, offline_access.");
    expect(html).toContain(ADMIN_NOTE.split(":")[0]);
    expect(html).toContain("What syncs which way");
    expect(html).toContain("Full guide: docs/calendar_connect.md");
  });
});

describe("the disconnect dialog", () => {
  const src = readFileSync(new URL("./CalendarConnections.tsx", import.meta.url), "utf8");
  it("uses the shared ConfirmDialog with the disconnect copy", () => {
    expect(src).toContain('import ConfirmDialog from "@/components/ui/ConfirmDialog"');
    expect(src).toContain("disconnectCopy(asking.provider)");
    expect(src).toContain('confirmVariant="destructive"');
  });
});

describe("the shell seam", () => {
  const src = readFileSync(new URL("./CalendarConnections.tsx", import.meta.url), "utf8");
  it("builds no palette, no bell and no rail, and uses no raw palette class", () => {
    expect(src).not.toMatch(/NotificationBell|AgentChat|CommandPalette|keydown/);
    expect(src).not.toMatch(/\b(?:bg|text|border)-(?:red|green|amber|blue|yellow|gray|slate)-\d/);
  });
});

describe("the wiring", () => {
  const src = readFileSync(new URL("./CalendarConnections.tsx", import.meta.url), "utf8");
  it("checks the consent host in the client, and guards a second Connect press", () => {
    // CE-10d moved the hook to ../lib/useCalendarConnect.ts, shared with AlsoSyncCalendar.
    const hook = readFileSync(new URL("../lib/useCalendarConnect.ts", import.meta.url), "utf8");
    expect(hook).toContain("safeConsentUrl(p, r.url)");
    expect(hook).toContain("if (inFlight.current) return;");
    expect(src).toContain("useConnect");
  });
  it("clears the connected banner after a successful disconnect", () => {
    const at = src.indexOf("The \"is connected\" banner would contradict the toast.");
    expect(at).toBeGreaterThan(0);
    expect(src.slice(at, at + 120)).toContain("onDismissNotice();");
  });
});

describe("the task switch of a row (CE-10c follow-up)", () => {
  const withRows = (rows: CalendarConnection[], over: Partial<SectionViewProps> = {}) =>
    buttons(draw({ list: list(rows), onTogglePush: noop, ...over }));

  it("draws the switch on, named for the provider", () => {
    const html = withRows([row()]);
    expect(html).toContain('role="switch"');
    expect(html).toContain('aria-checked="true"');
    expect(html).toContain("Show my Metorite tasks in Google Calendar");
    expect(withRows([row({ provider: "outlook" })])).toContain("Show my Metorite tasks in Outlook");
  });

  it("draws the switch off, with the changed sentence and the note", () => {
    const html = withRows([row({ push_tasks: false })], {
      pushLines: { [ID]: "Your tasks no longer go to this calendar. The events already there stay." },
    });
    expect(html).toContain('aria-checked="false"');
    expect(html).toContain("Your tasks do not go to this calendar.");
    expect(html).not.toContain("go to the &#x27;Metorite&#x27; calendar");
    expect(html).toContain("Your tasks no longer go to this calendar. The events already there stay.");
  });

  it("draws no switch on a row that needs a sign-in", () => {
    const html = withRows([row({ status: "needs_reconnect", push_tasks: false })]);
    expect(html).not.toContain('role="switch"');
    expect(html).toContain("Reconnect");
  });

  it("disables the switch while the request runs", () => {
    expect(withRows([row()], { pushingId: ID })).toMatch(/role="switch"[^>]* disabled=""/);
    expect(withRows([row()])).not.toMatch(/role="switch"[^>]* disabled=""/);
  });

  it("guards one request at a time and rolls back on failure", () => {
    const src = readFileSync(new URL("./CalendarConnections.tsx", import.meta.url), "utf8");
    expect(src).toContain("if (pushInFlight.current) return;");
    expect(src).toContain("setPushLocal(row.id, !next);");
  });
});
