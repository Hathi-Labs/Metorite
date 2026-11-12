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
    const html = draw({ notice: { kind: "error", code: "scope_missing" } });
    expect(html).toContain('role="alert"');
    expect(html).toContain("The consent did not include calendar access. Press Connect and allow every box.");
    expect(draw({ notice: { kind: "error", code: "needs_admin" } })).toContain("Your administrator blocks this app.");
    expect(draw({ notice: { kind: "error", code: "other" } })).toContain("Connecting did not work. Try again.");
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
