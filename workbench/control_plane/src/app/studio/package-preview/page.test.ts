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
  VIEWER_NO_OBJECT, fieldSpecs, formFields, missingObjectNote, pickObject, previewControls, previewHref, recordTitle,
  tableFields,
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

/**
 * J2a: a viewer reads an app that another member owns, through an install.
 * Mutation 1: build the door from `detail.data.actions`. "the door of the page holds
 * the actions of previewControls" turns red. Mutation 2: drop one `controls.owner`
 * guard. "each owner control sits behind controls.owner" turns red.
 */
describe("J2a: a viewer gets the reads only", () => {
  const ACTIONS = [
    { id: "find", uses: "object:visit.list" }, { id: "peek", uses: "object:visit.get" },
    { id: "add", uses: "object:visit.create" }, { id: "change", uses: "object:visit.update" },
    { id: "remove", uses: "object:visit.delete" },
  ];
  it("the owner keeps every action, and a viewer keeps the list and the get", () => {
    expect(previewControls("owner", ACTIONS)).toEqual({ owner: true, actions: ACTIONS });
    const viewer = previewControls("viewer", ACTIONS);
    expect(viewer.owner).toBe(false);
    expect(viewer.actions.map((a) => a.id)).toEqual(["find", "peek"]);
    expect(previewControls("viewer", undefined).actions).toEqual([]);
  });
  it("the door of the page holds the actions of previewControls", () => {
    expect(page).toContain("keeper(pkg, controls.actions)");
    expect(page).not.toContain("keeper(pkg, detail.data.actions)");
    expect(page).toContain("verbMap(controls.actions)[`object:${object.name}.update`]");
    expect(page).not.toContain("verbMap(detail.data?.actions)");
  });
  it("each owner control sits behind controls.owner", () => {
    // The nearest guard before each control is an open `controls.owner ? (`, with no close between.
    const guarded = (marker: string) => {
      const at = page.indexOf(marker);
      expect(at, marker).toBeGreaterThan(0);
      const before = page.slice(0, at);
      const open = before.lastIndexOf("controls.owner ? (");
      expect(open, marker).toBeGreaterThan(0);
      expect(before.slice(open), marker).not.toMatch(/\) : /);
    };
    for (const marker of ['icon="Code"', "<DeletePackageButton", '<section aria-label="Form"', '"Edit in form"',
      "<DeleteRecordButton", "Click a cell to edit it.", "Drag a card to another lane"]) {
      guarded(marker);
    }
    expect(page.match(/<DeletePackageButton/g)).toHaveLength(1);
    expect(page.match(/Open in Studio IDE/g)).toHaveLength(1);
  });
  it("a viewer reads one plain line, and an empty app names the owner", () => {
    expect(page).toContain("{controls.owner ? null : <span role=\"status\"");
    expect(page).toContain("{READ_ONLY_SENTENCE}");
    expect(page).toContain("VIEWER_NO_OBJECT");
    expect(VIEWER_NO_OBJECT).toMatch(/owner/);
  });
});
