/**
 * The feed apps of MD-3b (`my_day_feed.md` §5.8 item 6 and §14.1a, WS-58).
 *
 * The client knows `crm`, `people` and `chat` before any of their kinds
 * ships (MD-3c to MD-3f). Each takes the label and the icon of its own pane
 * in the manifest (`NAV_SECTIONS`). Before MD-3b, a card of these apps drew
 * a lower-case source line and the "Circle" icon.
 *
 * Mutations, run by hand (2026-10-14):
 *   - drop `crm` from `FEED_ONLY_APP_HREF`: the icon case fails;
 *   - drop `people: "People"` from `FEED_APP_NAMES`: tsc and the name case fail;
 *   - make `sourceApp` ignore `source_apps`: the three source map cases fail.
 */
import { describe, expect, it } from "vitest";

import { NAV_SECTIONS } from "@/lib/nav";

import {
  FEED_APP_NAMES,
  cappedLinks,
  feedAppIcon,
  feedFailedLines,
  handledClaim,
  readFeedAnswer,
  sourceApp,
  sourceLine,
} from "./feed";

const PANES = NAV_SECTIONS.flatMap((s) => s.items);

const NEW_APPS = [
  ["crm", "CRM", "/crm", "KanbanSquare"],
  ["people", "People", "/people", "Users"],
  ["chat", "Chat", "/chat", "MessageCircle"],
] as const;

describe("the feed apps of MD-3b", () => {
  it.each(NEW_APPS)("names %s as its pane does", (app, name, href) => {
    expect(FEED_APP_NAMES[app]).toBe(name);
    expect(PANES.find((p) => p.href === href)?.label).toBe(name);
    // A kind with no label yet: the source line is the app's name alone.
    expect(sourceLine({ app, kind: "not_yet_a_kind" })).toBe(name);
  });

  it.each(NEW_APPS)("draws the icon of the %s pane", (app, _name, href, icon) => {
    expect(PANES.find((p) => p.href === href)?.icon).toBe(icon);
    expect(feedAppIcon(app)).toBe(icon);
    expect(feedAppIcon(app)).not.toBe("Circle");
  });

  it("keeps the apps it knew, and the fallback for an app it does not know", () => {
    expect(feedAppIcon("calendar")).toBe("Calendar");
    expect(FEED_APP_NAMES.tasks).toBe("My Tasks");
    expect(feedAppIcon("unknown")).toBe("Circle");
    expect(feedAppIcon("constructor")).toBe("Circle");
    expect(sourceLine({ app: "unknown" as never, kind: "overdue" })).toBe("unknown · Overdue");
  });

  it("links Show all in CRM to the CRM pane", () => {
    const group = { cards: [{ app: "crm" }] } as unknown as Parameters<typeof cappedLinks>[0];
    expect(cappedLinks(group, ["crm"])).toEqual([
      { app: "crm", label: "Show all in CRM", href: "/crm" },
    ]);
  });
});

describe("the source map of MD-3b (source_apps, §14.1a)", () => {
  const MAP = { crm_followups: "crm", crm_deals: "crm", tasks: "tasks" };
  const group = { cards: [{ app: "crm" }, { app: "tasks" }] } as unknown as Parameters<typeof cappedLinks>[0];

  it("draws the CRM line for a failed crm_followups source, once for two", () => {
    expect(feedFailedLines({ crm_followups: "failed" } as never, MAP)).toEqual([
      "CRM did not answer, so its follow-ups and deals may be missing.",
    ]);
    expect(feedFailedLines({ crm_followups: "failed", crm_deals: "failed" } as never, MAP)).toHaveLength(1);
    expect(feedFailedLines({ crm_followups: "ok", crm_deals: "absent" } as never, MAP)).toEqual([]);
  });

  it("links Show all in CRM for a capped crm_followups source", () => {
    expect(cappedLinks(group, ["crm_followups"], MAP)).toEqual([
      { app: "crm", label: "Show all in CRM", href: "/crm" },
    ]);
  });

  it("silences the honest summary for a failed crm_followups source", () => {
    const answer = {
      handled: [{ app: "tasks", title: "x", href: "/projects", by: { kind: "you" as const, name: null }, at: null }],
      handled_ok: true,
      sources: { crm_followups: "failed" } as never,
      source_apps: MAP,
      capped: [],
    };
    expect(handledClaim(answer)).toBe(false);
    expect(handledClaim({ ...answer, sources: { crm_followups: "ok" } as never })).toBe(true);
  });

  it("treats a source the map does not name as its own app, as before", () => {
    expect(sourceApp(undefined, "email")).toBe("email");
    expect(sourceApp({}, "calendar")).toBe("calendar");
    expect(sourceApp(MAP, "constructor")).toBe("constructor");
    expect(feedFailedLines({ calendar: "failed", email: "failed" })).toEqual([
      "Email did not answer, so its replies may be missing.",
      "Calendar did not answer, so its time blocks may be missing.",
    ]);
    expect(cappedLinks(group, ["tasks"])).toEqual([
      { app: "tasks", label: "Show all in My Tasks", href: expect.any(String) },
    ]);
    // With no map, an MD-3 source id names no app, so it draws nothing.
    expect(feedFailedLines({ crm_followups: "failed" } as never)).toEqual([]);
  });

  it("reads the map defensively", () => {
    expect(readFeedAnswer({ source_apps: { a: "crm", b: 3, c: "" } }).source_apps).toEqual({ a: "crm" });
    expect(readFeedAnswer({}).source_apps).toEqual({});
    expect(readFeedAnswer({ source_apps: ["crm"] }).source_apps).toEqual({});
  });
});
