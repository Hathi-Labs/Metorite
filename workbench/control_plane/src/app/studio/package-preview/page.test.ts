/**
 * STU-D2b-1 fence: the package preview route is gated, and no pane lists it.
 *
 * A route with no entry in `HREF_FEATURES` is unguarded (`access.ts`), so the
 * longest-prefix match of `/studio` has to reach it.
 *
 * Mutation 1: give `/studio/package-preview` a nav entry in `nav.ts`. "is in no
 * nav pane" turns red.
 * Mutation 2: add a feature override that maps the route to no feature, or move
 * the route out of `/studio`. "is gated by the feature of /studio" turns red.
 * Mutation 3: import `buildPage` or `pageModel` into the page. "builds no
 * view-to-node mapper" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { featureForPath } from "@/lib/access";
import { PANES } from "@/lib/nav";
import { computedKeys } from "@/lib/studio/inlineEdit";
import {
  fieldSpecs, formFields, missingObjectNote, pickObject, previewHref, recordTitle, tableFields,
} from "@/lib/studio/packagePreview";

const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");

describe("/studio/package-preview", () => {
  it("is gated by the feature of /studio", () => {
    const studio = featureForPath("/studio");
    expect(studio).toBeTruthy();
    expect(featureForPath("/studio/package-preview")).toBe(studio);
  });

  it("is in no nav pane", () => {
    expect(PANES.some((p) => p.href.startsWith("/studio/package-preview"))).toBe(false);
  });

  it("builds no view-to-node mapper and reads no page of the manifest", () => {
    expect(page).not.toMatch(/pageModel|buildPage|renderView|manifest\.(views|pages)/);
  });

  it("mounts no shell part of its own", () => {
    expect(page).not.toMatch(/NotificationBell|<AgentChat|keydown|CommandPalette/);
  });

  it("puts Delete in the Detail sheet and no delete in the Table", () => {
    const detail = page.slice(page.indexOf('aria-label="Detail"'));
    expect(detail).toContain("<DeleteRecordButton");
    const table = page.slice(page.indexOf('aria-label="Table"'), page.indexOf('aria-label="Form"'));
    expect(table).not.toContain("DeleteRecordButton");
  });
});

describe("the preview model", () => {
  const object = {
    id: "o", name: "visit", label: "Visit", rules: [],
    fields: [
      { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
      { key: "tags", type: "multiSelect", label: null, required: false, many: false, options: ["A"], to: null, hot: false },
      { key: "bad", type: "formula", label: null, required: false, many: false, options: null, to: null, hot: false },
    ],
  };
  it("reads the fields of an object row, and drops a type it does not know", () => {
    const f = fieldSpecs(object);
    expect(f.map((x) => x.key)).toEqual(["title", "tags"]);
    expect(f[0]).toMatchObject({ key: "title", type: "text", label: "Title", required: true });
    expect(f[1].options).toEqual(["A"]);
  });
  it("cuts the Table to eight columns", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ key: `f${i}`, type: "text" as const }));
    expect(tableFields(many)).toHaveLength(8);
  });
  it("keeps a long text out of the Table columns", () => {
    const f = [{ key: "a", type: "text" as const }, { key: "n", type: "longText" as const }];
    expect(tableFields(f).map((x) => x.key)).toEqual(["a"]);
  });
  it("names a record by its first text field, else its id", () => {
    const f = fieldSpecs(object);
    expect(recordTitle(f, { id: "r1", title: "Press" })).toBe("Press");
    expect(recordTitle(f, { id: "r1" })).toBe("r1");
  });
  it("picks the named object, else the first", () => {
    const a = { ...object, name: "a" };
    const b = { ...object, name: "b" };
    expect(pickObject([a, b], "b")?.name).toBe("b");
    expect(pickObject([a, b], "zzz")?.name).toBe("a");
    expect(pickObject([], null)).toBeUndefined();
  });
  it("builds the href with the query", () => {
    expect(previewHref("p1", "visit")).toBe("/studio/package-preview?pkg=p1&object=visit");
    expect(previewHref("p1")).toBe("/studio/package-preview?pkg=p1");
  });
});

describe("fix round 1: the Forms, the door and the object pick", () => {
  const fields = [
    { key: "qty", type: "number" as const }, { key: "price", type: "number" as const },
    { key: "total", type: "number" as const },
  ];
  const rules = [{ name: "t", kind: "formula", target: "total", expr: {} }, { kind: "rollup", target: "sum" }];
  it("a Form never holds a computed field, and the Detail sheet keeps it", () => {
    expect(formFields(fields, computedKeys(rules)).map((f) => f.key)).toEqual(["qty", "price"]);
    expect(formFields(fields, computedKeys([])).map((f) => f.key)).toEqual(["qty", "price", "total"]);
  });
  it("both Form mounts take the filtered fields and the Detail takes all but a lines field", () => {
    const forms = page.match(/<StudioForm[^>]*?fields=\{[^}]*\}/g) ?? [];
    expect(forms).toHaveLength(2);
    for (const f of forms) expect(f).toContain("fields={formFields(");
    // C13: the one Detail mount skips a lines relation through detailFields.
    expect(page).toContain("<StudioDetail fields={detailFields(fields)}");
  });
  it("the page keeps one door through a re-read of the package", () => {
    expect(page).toContain("createDoorKeeper");
    expect(page).not.toMatch(/useMemo\(\s*\(\) => \(detail\.data \? createRealDoor/);
  });
  it("the Table header does not say the read error a second time", () => {
    expect(page).not.toContain("error={rows.error}");
  });
  it("says so when the query names no object", () => {
    const objects = [{ name: "visit", label: "Visit" }, { name: "site", label: null }] as never;
    expect(missingObjectNote(objects, "nope")).toBe("No object named nope. Showing Visit.");
    expect(missingObjectNote(objects, "site")).toBeNull();
    expect(missingObjectNote(objects, null)).toBeNull();
    expect(page).toContain("missingObjectNote(");
  });
});
