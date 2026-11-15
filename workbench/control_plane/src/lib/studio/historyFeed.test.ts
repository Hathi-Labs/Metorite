/**
 * STU-C4b fence: the model of the Activity tab.
 *
 * Mutation 1: draw a key that is not in `changes` (fill a key from the fields). "draws only the
 *   keys that are in changes" turns red.
 * Mutation 2: drop the `next` cursor in `parseHistory` or `historySource`. "keeps paging on next"
 *   turns red.
 */
import { describe, expect, it } from "vitest";

import type { FieldSpec } from "./fieldCheck";
import {
  EMPTY_VALUE, MAX_VALUE_CHARS, changeLines, historySource, joinPages, parseHistory, valueWords, whoWords,
} from "./historyFeed";

const FIELDS: FieldSpec[] = [
  { key: "title", type: "text", label: "Title" },
  { key: "status", type: "select", label: "Status", options: ["planned", "done"] },
  { key: "fee", type: "money", label: "Fee" },
  { key: "notes", type: "longText", label: "Notes" },
  { key: "salary", type: "money", label: "Salary" },
];

const row = (over: Record<string, unknown> = {}) => ({
  id: 1, op: "update", actor: "ann@acme.test", via_kind: null, via_id: null,
  changes: { title: { old: "A", new: "B" } }, at: "2026-11-15T10:05:00Z", ...over,
});

describe("parseHistory", () => {
  it("reads an empty history as no entry and no next", () => {
    expect(parseHistory({ history: [], next: null }, FIELDS)).toEqual({ entries: [], next: null });
  });
  it("fails on an answer with no history list", () => {
    expect(parseHistory({ records: [] }, FIELDS)).toBeNull();
    expect(parseHistory(null, FIELDS)).toBeNull();
  });
  it("reads one create as Created, with the address of the actor", () => {
    const page = parseHistory({ history: [row({ op: "create", changes: { title: { new: "A" } } })], next: null }, FIELDS)!;
    expect(page.entries).toHaveLength(1);
    expect(page.entries[0]).toMatchObject({ opWord: "Created", who: "ann@acme.test" });
    expect(page.entries[0].lines).toEqual([{ key: "title", label: "Title", old: null, next: "A" }]);
  });
  it("reads an update with old and new", () => {
    const [e] = parseHistory({ history: [row()], next: null }, FIELDS)!.entries;
    expect(e.opWord).toBe("Changed");
    expect(e.lines).toEqual([{ key: "title", label: "Title", old: "A", next: "B" }]);
  });
  it("accepts a boundary change with no old key: no from, no crash", () => {
    const [e] = parseHistory({ history: [row({ changes: { status: { new: "done" } } })], next: null }, FIELDS)!.entries;
    expect(e.lines).toEqual([{ key: "status", label: "Status", old: null, next: "done" }]);
  });
  it("keeps an old key whose value is null as empty, and not as no old key", () => {
    const [e] = parseHistory({ history: [row({ changes: { title: { old: null, new: "B" } } })], next: null }, FIELDS)!.entries;
    expect(e.lines[0].old).toBe(EMPTY_VALUE);
  });
  it("keeps paging on next: the cursor is the next of the answer", () => {
    expect(parseHistory({ history: [row({ id: 4 })], next: 4 }, FIELDS)!.next).toBe(4);
    expect(parseHistory({ history: [row({ id: 4 })], next: null }, FIELDS)!.next).toBeNull();
    expect(historySource("visit", "r1", null)).toEqual({ action: "object:visit.history", args: { id: "r1" } });
    expect(historySource("visit", "r1", 4)).toEqual({ action: "object:visit.history", args: { id: "r1", after: 4 } });
  });
  it("joins the pages in order, with the next of the last, and shows no id twice", () => {
    const a = parseHistory({ history: [row({ id: 1 }), row({ id: 2 })], next: 2 }, FIELDS)!;
    const b = parseHistory({ history: [row({ id: 2 }), row({ id: 3 })], next: null }, FIELDS)!;
    const all = joinPages(a, [b]);
    expect(all.entries.map((e) => e.id)).toEqual([1, 2, 3]);
    expect(all.next).toBeNull();
    expect(joinPages(a, []).next).toBe(2);
  });
  it("leaves out a row with no numeric id", () => {
    expect(parseHistory({ history: [{ op: "update", changes: {} }], next: null }, FIELDS)!.entries).toEqual([]);
  });
});

describe("the keys", () => {
  it("draws only the keys that are in changes (a field that C8 hides shows no line)", () => {
    // `salary` is a field of the object, and the door dropped its key from this change.
    const lines = changeLines({ title: { old: "A", new: "B" } }, FIELDS);
    expect(lines.map((l) => l.key)).toEqual(["title"]);
    const none = changeLines({}, FIELDS);
    expect(none).toEqual([]);
  });
  it("orders the lines by the fields of the object, then the keys with no field", () => {
    const lines = changeLines({ gone: { new: "x" }, fee: { new: { minor: 100, currency: "INR" } }, title: { new: "T" } }, FIELDS);
    expect(lines.map((l) => l.key)).toEqual(["title", "fee", "gone"]);
    expect(lines[2].label).toBe("gone");
  });
  it("takes a bare value as the new value", () => {
    expect(changeLines({ title: "B" }, FIELDS)).toEqual([{ key: "title", label: "Title", old: null, next: "B" }]);
  });
});

describe("the words of a value", () => {
  it("words money in minor units with its currency", () => {
    const words = valueWords(FIELDS[2], { minor: 125050, currency: "INR" });
    expect(words).toContain("1,250.50");
    expect(words).toMatch(/₹|INR/);
  });
  it("words a money change from old to new", () => {
    const [l] = changeLines({ fee: { old: { minor: 100, currency: "INR" }, new: { minor: 250, currency: "INR" } } }, FIELDS);
    expect(l.old).toContain("1.00");
    expect(l.next).toContain("2.50");
  });
  it("words a date and a select as the Table does", () => {
    expect(valueWords({ key: "d", type: "date" }, "2026-10-07")).toBe("7 Oct 2026");
    expect(valueWords(FIELDS[1], "done")).toBe("done");
  });
  it("cuts a long text value", () => {
    const words = valueWords(FIELDS[3], "x".repeat(MAX_VALUE_CHARS * 3));
    expect(words.length).toBe(MAX_VALUE_CHARS);
    expect(words.endsWith("…")).toBe(true);
  });
  it("shows an empty value as the word empty", () => {
    expect(valueWords(FIELDS[0], null)).toBe(EMPTY_VALUE);
    expect(valueWords(FIELDS[0], "")).toBe(EMPTY_VALUE);
  });
  it("never shows raw JSON", () => {
    for (const v of [{ a: 1 }, [{ b: 2 }], { minor: "x" }]) {
      expect(valueWords(undefined, v)).not.toMatch(/[{}[\]"]/);
    }
    expect(valueWords(FIELDS[2], { nope: true })).toBe("a value");
  });
});

describe("the writer", () => {
  it("shows the address, else the via, else Someone", () => {
    expect(whoWords("ann@acme.test", "agent")).toBe("ann@acme.test");
    expect(whoWords(null, "workflow_run")).toBe("a workflow run");
    expect(whoWords(null, "agent")).toBe("an agent");
    expect(whoWords(null, null)).toBe("Someone");
    expect(whoWords("", "bogus")).toBe("Someone");
  });
});
