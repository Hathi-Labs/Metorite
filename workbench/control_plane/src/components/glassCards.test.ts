/**
 * GL-10a: the AG-UI cards in glass (D143, `glass_look.md` GL-4).
 *
 * Every class this slice added sits behind `glass:`, so classic is byte for
 * byte what it was. The hashes below were taken from the markup BEFORE the
 * slice, and each card passes through `classicMarkup()` before it hashes. A
 * glass class that leaks into classic, or a classic class that moves, fails
 * here. `glassLook.test.ts` holds the class-level fence.
 */
import { createHash } from "node:crypto";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TEMPLATE_REGISTRY } from "./genUITemplates";
import { classicMarkup } from "@/lib/theme/classicMarkup";

const stats = [
  { label: "Open", value: 12, unit: "tasks", delta: 3, icon: "trending-up" },
  { label: "Done", value: 40, delta: -2 },
];
const gridCols = ["Name", "Owner", "Due"];
const gridRows = [
  { id: "a", cells: ["Ship cards", "ayush@x.io", "2026-10-30"] },
  { id: "b", cells: ["Write spec", "nithin@x.io", "2026-11-02"] },
];

const SAMPLES: Record<string, Record<string, unknown>> = {
  weatherCard: { location: "Pune", tempC: 27, condition: "sunny", highC: 30, lowC: 21, humidity: 40, wind: "8 km/h",
    forecast: [{ day: "Mon", condition: "rain", high: 28, low: 20 }] },
  statDashboard: { title: "Week", stats },
  barChart: { title: "Load", unit: "h", bars: [{ label: "A", value: 4, tone: "primary" }, { label: "B", value: 7, tone: "danger" }] },
  sparkTrend: { label: "Signups", value: 120, unit: "/d", delta: 4, series: [1, 3, 2, 5, 4, 6] },
  comparison: { title: "Plans", options: [
    { name: "Core", recommended: true, rows: [{ label: "Seats", value: "10" }] },
    { name: "Pro", rows: [{ label: "Seats", value: "50" }] } ] },
  progressTracker: { title: "Launch", steps: [{ label: "Build", state: "done" }, { label: "Test", state: "active" }, { label: "Ship", state: "pending" }] },
  recipeCard: { title: "Dal", description: "Quick", servings: 2, prepMinutes: 5, cookMinutes: 20, calories: 300,
    ingredients: [{ item: "Lentils", amount: "1 cup" }], steps: ["Boil", "Season"], tags: ["veg"], tip: "Add ghee" },
  flightStatus: { airline: "Air", flightNo: "AI101", status: "delayed", from: { code: "PNQ", city: "Pune", time: "10:00" },
    to: { code: "DEL", city: "Delhi", time: "12:00" }, progressPct: 40, note: "Late" },
  trainStatus: { operator: "IR", trainNo: "12123", status: "departed", from: { station: "Pune", time: "06:00" },
    to: { station: "Mumbai", time: "09:00" }, stops: [{ station: "Lonavala", state: "done" }, { station: "Kalyan", state: "active" }] },
  formCard: { title: "Details", description: "Fill in", submitLabel: "Send", fields: [
    { name: "n", label: "Name", type: "text", required: true }, { name: "k", label: "Kind", type: "select", options: ["a", "b"] },
    { name: "s", label: "Size", type: "slider", min: 0, max: 10 }, { name: "t", label: "On", type: "toggle" },
    { name: "c", label: "Tick", type: "checkbox" }, { name: "d", label: "Day", type: "date" }, { name: "x", label: "Note", type: "textarea" } ] },
  optionPicker: { title: "Pick", description: "One", options: [
    { id: "a", label: "Alpha", description: "First", badge: "new", recommended: true }, { id: "b", label: "Beta" } ] },
  timeline: { title: "History", rows: [{ at: "2026-10-30T10:00:00Z", type: "comment", actor: "Ayush", body: "Hi" },
    { at: "2026-10-30T11:00:00Z", type: "field", actor: "Nithin", field: "status", before: "open", after: "done" }] },
  taskBoard: { title: "Board", columns: [{ name: "Todo", category: "todo", tasks: [{ id: "0f8fad5b-d9cb-469f-a165-70867728950e", number: 1, title: "A", assignees: ["Ayush"], due: "2026-11-01" }] },
    { name: "Done", category: "done", tasks: [{ id: "1f8fad5b-d9cb-469f-a165-70867728950e", title: "B", done: true }] }] },
  dataGrid: { title: "Grid", columns: gridCols, rows: gridRows },
  reportCard: { title: "Weekly", period: "Oct", stats, tables: [{ title: "T", columns: gridCols, rows: gridRows.map((r) => ({ cells: r.cells })) }], pills: ["Tap"], note: "n" },
  planCard: { title: "Plan", project: { name: "Memory", parent: "Metorite" }, tasks: [
    { key: "t1", title: "Build", owner: "a@x.io", effort_mins: 240, start: "2026-10-22", due: "2026-10-24", phase: "F" },
    { key: "t2", title: "Test", effort_mins: 120, start: "2026-10-23", due: "2026-10-25", after: ["t1"], phase: "F", marks: ["short"] } ] },
  chart: { type: "line", title: "Trend", labels: ["a", "b", "c"], values: [1, 3, 2] },
  chartDonut: { type: "donut", title: "Share", labels: ["a", "b"], values: [3, 1] },
  report: { title: "Report", subtitle: "Q3", summary: "**Good**", kpis: [{ label: "Rev", value: 10 }],
    sections: [{ heading: "H", text: "t", bullets: ["x"] }, { heading: "V", visual: { kind: "table", columns: gridCols, rows: gridRows.map((r) => r.cells) } }] },
  reminderCard: { reminderId: "r1", title: "Call", when: "2026-11-01T09:00:00Z", zone: "Asia/Kolkata", state: "scheduled", kind: "action" },
};

const hash = (s: string) => createHash("sha256").update(s).digest("hex").slice(0, 16);

/** Taken before GL-10a, from the classic markup. */
const GOLD: Record<string, string> = {
  weatherCard: "bf5e3bc66d35c135",
  statDashboard: "c962447efeb2b5d8",
  barChart: "f07ad3d4e73aa2ce",
  sparkTrend: "d712c8ea13e2211b",
  comparison: "8d33cbf8f6478256",
  progressTracker: "bd22a789f544f2bf",
  recipeCard: "0111dd27c7de0bfd",
  flightStatus: "37cf437eacedc924",
  trainStatus: "11891b07b0b88009",
  formCard: "47811dbd32ec302d",
  optionPicker: "e5425e399af64f03",
  timeline: "a4f3165e4a695ca2",
  taskBoard: "22da494a643934f3",
  dataGrid: "e5d6a1ba22cfef84",
  reportCard: "c7f5bde5f12df13f",
  planCard: "80786b1da39c83ae",
  chart: "f07ad3d4e73aa2ce",
  chartDonut: "f07ad3d4e73aa2ce",
  report: "1a6177e2d25b720c",
  reminderCard: "a825a475c17d90e4",
};

describe("GL-10a: classic markup of the AG-UI cards did not change", () => {
  for (const [key, data] of Object.entries(SAMPLES)) {
    it(key, () => {
      const name = key === "chartDonut" ? "chart" : key;
      const html = renderToStaticMarkup(createElement(() => TEMPLATE_REGISTRY[name](data, { onAction: () => {} })));
      expect(html.length).toBeGreaterThan(50);
      const got = hash(classicMarkup(html));
      if (!GOLD[key]) console.log(`GOLD ${key}: "${got}"`);
      else expect(got).toBe(GOLD[key]);
    });
  }
});
