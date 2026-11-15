import { describe, expect, it } from "vitest";
import { layoutSpans } from "./layout";
import { rowsToEvents } from "./objectEvents";
import type { GridEvent } from "./types";

const at = (h: number, m = 0) => new Date(2026, 10, 12, h, m);
const ev = (id: string, s: Date, e: Date, allDay = false): GridEvent => ({
  id,
  title: id,
  start: s,
  end: e,
  allDay,
  tone: "primary",
});

describe("rowsToEvents", () => {
  const src = { dateField: "due", titleField: "name" };
  it("reads a date as an all-day event", () => {
    const [e] = rowsToEvents([{ id: "a", values: { due: "2026-11-12", name: "Ship" } }], src);
    expect(e.allDay).toBe(true);
    expect(e.start).toEqual(new Date(2026, 10, 12));
    expect(e.end).toEqual(new Date(2026, 10, 13));
    expect(e.title).toBe("Ship");
  });
  it("reads a datetime as a timed event of the default length", () => {
    const [e] = rowsToEvents([{ id: "a", values: { due: "2026-11-12T10:00:00Z", name: "Call" } }], src);
    expect(e.allDay).toBe(false);
    expect(e.end.getTime() - e.start.getTime()).toBe(60 * 60000);
  });
  it("takes the length from the source", () => {
    const [e] = rowsToEvents([{ id: "a", values: { due: "2026-11-12T10:00:00Z", name: "x" } }], { ...src, durationMins: 30 });
    expect(e.end.getTime() - e.start.getTime()).toBe(30 * 60000);
  });
  it("leaves out a row with no readable date", () => {
    const rows = [
      { id: "a", values: { due: null, name: "a" } },
      { id: "b", values: { due: "not a date", name: "b" } },
      { id: "c", values: { name: "c" } },
      { id: "d", values: { due: "2026-11-12", name: "d" } },
    ];
    expect(rowsToEvents(rows, src).map((e) => e.id)).toEqual(["d"]);
  });
  it("names an untitled row and keeps the row in data", () => {
    const row = { id: "a", values: { due: "2026-11-12", name: "  " } };
    const [e] = rowsToEvents([row], src);
    expect(e.title).toBe("Untitled");
    expect(e.data).toBe(row);
  });
  describe("endField", () => {
    const withEnd = { ...src, endField: "until" };
    const row = (values: Record<string, unknown>) => [{ id: "a", values: { name: "x", ...values } }];
    it("uses an end after the start", () => {
      const [e] = rowsToEvents(row({ due: "2026-11-12T10:00:00Z", until: "2026-11-12T12:30:00Z" }), withEnd);
      expect(e.end.getTime() - e.start.getTime()).toBe(150 * 60000);
    });
    it("falls back to the length when the end is before the start", () => {
      const [e] = rowsToEvents(row({ due: "2026-11-12T10:00:00Z", until: "2026-11-12T09:00:00Z" }), withEnd);
      expect(e.end.getTime() - e.start.getTime()).toBe(60 * 60000);
    });
    it("falls back to the length when the end is missing or not a date", () => {
      for (const until of [undefined, null, "", "soon"]) {
        const [e] = rowsToEvents(row({ due: "2026-11-12T10:00:00Z", until }), { ...withEnd, durationMins: 45 });
        expect(e.end.getTime() - e.start.getTime()).toBe(45 * 60000);
      }
    });
    it("keeps a date-only start all day", () => {
      const [e] = rowsToEvents(row({ due: "2026-11-12", until: "2026-11-12T12:30:00Z" }), withEnd);
      expect(e.allDay).toBe(true);
      expect(e.end).toEqual(new Date(2026, 10, 13));
    });
    it("leaves out a row with no date, even with an end", () => {
      expect(rowsToEvents(row({ until: "2026-11-12T12:30:00Z" }), withEnd)).toEqual([]);
    });
  });
});

describe("layoutSpans", () => {
  it("puts overlapping spans in lanes and leaves a free span alone", () => {
    const a = ev("a", at(9), at(10));
    const b = ev("b", at(9, 30), at(10, 30));
    const c = ev("c", at(12), at(13));
    const out = layoutSpans([c, b, a]);
    const by = (id: string) => out.find((o) => o.block.id === id)!;
    expect([by("a").lane, by("a").lanes]).toEqual([0, 2]);
    expect([by("b").lane, by("b").lanes]).toEqual([1, 2]);
    expect([by("c").lane, by("c").lanes]).toEqual([0, 1]);
  });
});
