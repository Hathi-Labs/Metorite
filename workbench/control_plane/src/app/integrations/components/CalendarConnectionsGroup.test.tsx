// WS-CAL CE-10c (D96.1): the read-only Calendar group of the Connections view.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CalendarConnection } from "@/app/calendar/lib/calendarConnections";

import { CALENDAR_SETTINGS_HREF, CalendarConnectionsGroupView } from "./CalendarConnectionsGroup";

const row = (over: Partial<CalendarConnection> = {}): CalendarConnection => ({
  id: "3f1c2a9e-1b2d-4c3e-8f4a-5b6c7d8e9f01",
  provider: "outlook",
  account_email: "ada@example.com",
  status: "connected",
  last_sync_at: null,
  last_error: null,
  metorite_calendar: true,
  entry_count: 2,
  ...over,
});

describe("the Calendar row group", () => {
  it("draws nothing without a row", () => {
    expect(renderToStaticMarkup(createElement(CalendarConnectionsGroupView, { rows: [] }))).toBe("");
  });

  it("lists each row and links to Calendar settings", () => {
    const html = renderToStaticMarkup(createElement(CalendarConnectionsGroupView, { rows: [row(), row({ id: "x", provider: "google", status: "needs_reconnect" })] }));
    expect(html).toContain("Outlook");
    expect(html).toContain("ada@example.com");
    expect(html).toContain("Connected");
    expect(html).toContain("Sign in again");
    expect(html).toContain(`href="${CALENDAR_SETTINGS_HREF}"`);
    expect(CALENDAR_SETTINGS_HREF).toBe("/calendar?settings=1");
  });
});
