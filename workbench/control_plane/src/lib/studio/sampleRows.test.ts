/**
 * STU-G3a fence (sample data): the Preview of a draft reads SAMPLE rows and writes nothing.
 *
 * Mutation 1: make `makeDraftDoor().run` return `{ ok: true }`. "refuses every write" turns red.
 * Mutation 2: use `Math.random` or `Date.now` in `sampleRowsFor`. "is fixed" and "calls no network" turn red.
 * Mutation 3: drop a field type from `one()`. "gives a value for each of the 22 types" turns red.
 * Mutation 4: call `fetch` in `sampleRows.ts`. "calls no network" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { FIELD_TYPE_LIST } from "./blocks";
import type { FieldSpec } from "./fieldCheck";
import { validateRecord } from "./fieldCheck";
import { SAMPLE_COUNT, SAMPLE_LABEL, PREVIEW_KEPT, SAMPLE_REFUSAL, listedObject, makeDraftDoor, sampleRowsFor } from "./sampleRows";

const all: FieldSpec[] = FIELD_TYPE_LIST.map((type) => ({
  key: `f_${type}`, type, ...(type === "select" || type === "multiSelect" ? { options: ["Open", "Done"] } : {}),
  ...(type === "relation" ? { to: "site" } : {}),
  ...(type === "quantity" ? { units: ["kg", "t"] } : {}),
  ...(type === "barcode" ? { format: "ean13" } : {}),
}));

describe("sampleRowsFor", () => {
  it("gives a value for each of the 22 types", () => {
    const [row] = sampleRowsFor(all, 1);
    for (const f of all) expect(row[f.key], f.type).not.toBeUndefined();
    expect(all).toHaveLength(22);
  });

  it("is fixed: the same fields give the same rows", () => {
    expect(sampleRowsFor(all)).toEqual(sampleRowsFor(all));
    expect(sampleRowsFor(all)).toHaveLength(SAMPLE_COUNT);
  });

  it("gives each row an id, and cycles the options of a select", () => {
    const rows = sampleRowsFor([{ key: "st", type: "select", options: ["Open", "Done"] }], 3);
    expect(rows.map((r) => r.id)).toEqual(["s1", "s2", "s3"]);
    expect(rows.map((r) => r.st)).toEqual(["Open", "Done", "Open"]);
  });

  it("makes a value that the field check accepts", () => {
    const [row] = sampleRowsFor(all, 1);
    const { id: _id, ...data } = row;
    void _id;
    expect(validateRecord(all, data).ok).toBe(true);
  });

  it("holds a list in a field of many values, and no row for no count", () => {
    expect(sampleRowsFor([{ key: "t", type: "text", many: true }], 1)[0].t).toEqual(["t 1"]);
    expect(sampleRowsFor([], 2)).toEqual([{ id: "s1" }, { id: "s2" }]);
    expect(sampleRowsFor(all, 0)).toEqual([]);
  });
});

describe("makeDraftDoor", () => {
  const door = makeDraftDoor("p1:h1", { visit: [{ key: "title", type: "text" }] });

  it("reads the sample rows of the object in a list action", async () => {
    expect(listedObject("object:visit.list")).toBe("visit");
    expect(listedObject("object:visit.create")).toBeNull();
    expect(await door.read({ action: "object:visit.list" })).toHaveLength(SAMPLE_COUNT);
    expect(await door.read({ action: "object:other.list" })).toEqual([]);
    expect(await door.read({ action: "nope" })).toEqual([]);
  });

  it("scopes its cache by the package and the draft hash", () => {
    expect(door.scope).toBe("sample:p1:h1");
  });

  it("refuses an action that is not a write of a known object, with a sentence", async () => {
    for (const a of ["object:other.create", "send:mail", "object:visit.list"]) {
      expect(await door.run(a, { id: "s1", data: { title: "x" } })).toEqual({ ok: false, message: SAMPLE_REFUSAL });
    }
    expect(SAMPLE_LABEL).toBe("Sample data. Changes stay in this session only.");
  });

  it("calls no network", () => {
    const src = readFileSync(new URL("./sampleRows.ts", import.meta.url), "utf8");
    expect(src).not.toMatch(/\bfetch\(|XMLHttpRequest|Math\.random|Date\.now/);
  });
});

describe("makeDraftDoor preview store (G4b)", () => {
  const objects = {
    visit: [
      { key: "title", type: "text", required: true },
      { key: "status", type: "select", options: ["Open", "Done"] },
    ] as FieldSpec[],
  };
  const list = { action: "object:visit.list" };

  it("creates a record that the next read shows", async () => {
    const door = makeDraftDoor("p:h", objects);
    expect((await door.run("object:visit.create", { data: { title: "New", status: "Open" } })).ok).toBe(true);
    const rows = await door.read(list);
    expect(rows).toHaveLength(SAMPLE_COUNT + 1);
    expect(rows[rows.length - 1]).toMatchObject({ id: `s${SAMPLE_COUNT + 1}`, title: "New" });
  });

  it("updates a record, and a patch joins the stored record (a Board move)", async () => {
    const door = makeDraftDoor("p:h", objects);
    const before = (await door.read(list))[0];
    const r = await door.run("object:visit.update", { id: "s1", data: { status: before.status === "Open" ? "Done" : "Open" } });
    expect(r).toEqual({ ok: true, message: PREVIEW_KEPT });
    const after = (await door.read(list))[0];
    expect(after.status).not.toBe(before.status);
    expect(after.title).toBe(before.title);
    await door.run("object:visit.update", { id: "s1", data: { title: "Edited" } });
    expect((await door.read(list))[0].title).toBe("Edited");
  });

  it("deletes a record, and says so for a record that is gone", async () => {
    const door = makeDraftDoor("p:h", objects);
    expect((await door.run("object:visit.delete", { id: "s2" })).ok).toBe(true);
    const rows = await door.read(list);
    expect(rows).toHaveLength(SAMPLE_COUNT - 1);
    expect(rows.some((r) => r.id === "s2")).toBe(false);
    expect(await door.run("object:visit.delete", { id: "s2" })).toEqual({ ok: false, message: "That record is gone." });
    expect((await door.run("object:visit.update", { id: "nope", data: { title: "x" } })).ok).toBe(false);
  });

  it("refuses an invalid record with the validator's sentence, and keeps the rows", async () => {
    const door = makeDraftDoor("p:h", objects);
    const before = await door.read(list);
    const bad = await door.run("object:visit.create", { data: { status: "Open" } });
    expect(bad.ok).toBe(false);
    const check = validateRecord(objects.visit, { status: "Open" });
    expect(check.ok).toBe(false);
    if (!check.ok) expect(bad.message).toBe(check.problems[0].message);
    const unknown = await door.run("object:visit.update", { id: "s1", data: { status: "Nope" } });
    expect(unknown.ok).toBe(false);
    expect(await door.read(list)).toEqual(before);
  });

  it("keeps the store inside one door instance", async () => {
    const a = makeDraftDoor("p:h", objects);
    const b = makeDraftDoor("p:h", objects);
    await a.run("object:visit.delete", { id: "s1" });
    expect(await a.read(list)).toHaveLength(SAMPLE_COUNT - 1);
    expect(await b.read(list)).toHaveLength(SAMPLE_COUNT);
  });

  it("returns copies, so a caller cannot change the store", async () => {
    const door = makeDraftDoor("p:h", objects);
    (await door.read(list))[0].title = "Changed";
    expect((await door.read(list))[0].title).not.toBe("Changed");
  });

  it("uses no browser storage", () => {
    const src = readFileSync(new URL("./sampleRows.ts", import.meta.url), "utf8");
    expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
  });
});
