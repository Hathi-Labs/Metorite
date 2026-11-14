/**
 * STU-D5-W2 fence: the Calendar block. The vitest env has no DOM, so the block draws from the
 * cache with `renderToStaticMarkup`. The pure window rules have their own fence in
 * `lib/studio/calendarWindow.test.ts`.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/package-preview",
}));

import StudioCalendar from "@/components/studio/StudioCalendar";
import { clearAll, put } from "@/lib/dataCache";
import { periodSource } from "@/lib/studio/calendarWindow";
import { sourceKey, withMeta, type Row, type StudioDataDoor } from "@/lib/studio/dataDoor";
import type { ViewCalendar } from "@/lib/studio/viewBlocks";

beforeEach(() => clearAll());

const NOW = new Date(2026, 10, 11, 10, 30); // Wednesday 2026-11-11
const door: StudioDataDoor = { scope: "t:draft", read: async () => [], run: async () => ({ ok: true }) };
const MODEL: ViewCalendar = {
  kind: "calendar", object: "visit", start: "visit_at", startType: "datetime", end: "visit_end", titleField: "title",
  source: { action: "object:visit.list", args: { limit: 100 } }, openBase: "?ro=visit&rec=", title: "Field visits",
};
const iso = (d: number, h: number, mi = 0) => new Date(2026, 10, d, h, mi).toISOString();
const ROWS: Row[] = [
  { id: "v1", title: "Pump check", visit_at: iso(11, 9), visit_end: iso(11, 10) },
  { id: "v2", title: "Audit", visit_at: iso(12, 14) },
];
/** Seed the cache with the week of `NOW`, the key that the block reads first. */
function seed(rows: Row[], model: ViewCalendar = MODEL) {
  const src = periodSource(model.source, model.start, model.startType, "week", NOW);
  put(sourceKey(door, src), rows);
}
const draw = (model: ViewCalendar = MODEL, props: Record<string, unknown> = {}) =>
  renderToStaticMarkup(createElement(StudioCalendar, { model, door, now: NOW, ...props }));

describe("StudioCalendar: the frame", () => {
  it("opens in the week, with the title, the arrows, Today and the Month, Week and Day switch", () => {
    seed(ROWS);
    const html = draw();
    expect(html).toContain("Field visits");
    expect(html).toContain('aria-label="Previous period"');
    expect(html).toContain('aria-label="Next period"');
    expect(html).toContain(">Today<");
    for (const m of ["Month", "Week", "Day"]) expect(html).toContain(`>${m}<`);
    expect(html).toMatch(/aria-selected="true"[^>]*>(<[^>]*>)*Week/);
    expect(html).toContain('data-week-grid="week"');
    expect(html).toContain("Nov 9 – 15, 2026");
  });

  it("draws the records of the period as links to the record page", () => {
    seed(ROWS);
    const html = draw();
    expect(html).toContain('href="?ro=visit&amp;rec=v1"');
    expect(html).toContain("Pump check");
    expect(html).toContain("Audit");
  });

  it("draws plain chips when the app declares no get", () => {
    seed(ROWS, { ...MODEL, openBase: null });
    const html = draw({ ...MODEL, openBase: null });
    expect(html).toContain("Pump check");
    expect(html).not.toContain("rec=v1");
  });
});

describe("StudioCalendar: the states", () => {
  it("says Loading while nothing is known, and draws no grid", () => {
    const html = draw();
    expect(html).toContain("Loading…");
    expect(html).not.toContain("data-week-grid");
  });

  it("says that nothing is scheduled for a period with no record", () => {
    seed([]);
    expect(draw()).toContain("Nothing is scheduled in this period.");
  });

  it("says the first 100 are shown when the read hit the cap", () => {
    const many = Array.from({ length: 100 }, (_, i) => ({ id: `r${i}`, title: `T${i}`, visit_at: iso(11, 8) }));
    seed(many);
    expect(draw()).toContain("Showing the first 100 records of this period. Narrow the view to see the rest.");
    seed(withMeta([...ROWS], { atLeast: 400 }));
    expect(draw()).toContain("Showing the first 100 records of this period.");
    seed(ROWS);
    expect(draw()).not.toContain("Showing the first");
  });
});

describe("StudioCalendar: source pins", () => {
  const src = readFileSync(new URL("./StudioCalendar.tsx", import.meta.url), "utf-8");
  it("reads through the door with the window of the period and the cached key", () => {
    expect(src).toContain("periodSource(model.source, model.start, model.startType, mode, anchor)");
    expect(src).toContain("useCachedResource<Row[]>(sourceKey(door, source), () => door.read(source))");
  });
  it("uses the shared controls and the two grids", () => {
    expect(src).toContain('from "@/components/Tabs"');
    expect(src).toContain('from "@/components/ui/Button"');
    expect(src).toContain('from "@/components/calendar/MonthGrid"');
    expect(src).toContain('from "@/components/calendar/WeekGrid"');
  });
  it("hand-rolls no control, imports no icon pack, no app and no flag", () => {
    expect(src).not.toMatch(/<button\b/);
    expect(src).not.toContain("onKeyDown");
    expect(src).not.toContain("lucide-react");
    expect(src).not.toMatch(/from "@\/app\//);
    expect(src).not.toMatch(/NEXT_PUBLIC_STUDIO_/);
    expect(src).not.toContain("dangerouslySetInnerHTML");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
  it("gives each icon-only button an aria-label", () => {
    expect(src.match(/size="icon-sm"/g)?.length).toBe(2);
    expect(src.match(/size="icon-sm"[^>]*aria-label=/g)?.length).toBe(2);
  });
});
