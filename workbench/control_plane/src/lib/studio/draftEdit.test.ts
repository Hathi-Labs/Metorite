/**
 * STU-G3c fence: the direct edits of a draft keep the manifest valid, never change their input, and
 * say the truth about a delete.
 *
 * Mutation 1: let `removeObject` keep the record actions of the object. "takes its actions, its views and their page cells" turns red.
 * Mutation 2: let `removeObject` go on when another object links to it. "refuses while another object links to it" turns red.
 * Mutation 3: let `removeField` leave the name in `fields`, `columns`, `group_by` and the card. "takes the field out of the views" turns red.
 * Mutation 4: offer `relation` or `money` in `SAFE_TYPES`. "offers only types that need no more than a label" turns red.
 * Mutation 5: let `updateField` keep `options` when the type leaves select. "drops the options when the type leaves select" turns red.
 * Mutation 6: let `addView` make a Board for an object with no select field. "makes a Board only over an object with lanes" turns red.
 * Mutation 7: write a key that breaks the name pattern in `nameFrom`. "makes names the manifest accepts" turns red.
 * Mutation 8: mutate the input in `addField`. "never changes its input" turns red.
 * Mutation 9: say a live delete changes the live app in `publishEffect`. "tells the true effect of a delete" turns red.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { checkDraft } from "./draftProblems";
import {
  SAFE_TYPES, rulesNaming, commitText, addField, addObject, addView, isLiveField, laneField, linksTo, nameFrom, objectCut, objectsFor, publishEffect,
  removeField, removeObject, removeView, renameObject, updateField, updateView, viewUses,
} from "./draftEdit";

const base = () => ({
  studio: 1, id: "visits-app", name: "Visits", purpose: "Track visits.",
  objects: [
    { name: "visit", label: "Visit", fields: [
      { key: "title", type: "text", label: "Title" },
      { key: "status", type: "select", options: ["open", "done"] },
      { key: "site", type: "relation", to: "site" },
    ] },
    { name: "site", fields: [{ key: "city", type: "text" }] },
  ],
  actions: [
    { id: "visit_list", uses: "object:visit.list" }, { id: "visit_get", uses: "object:visit.get" },
    { id: "site_list", uses: "object:site.list" }, { id: "site_get", uses: "object:site.get" },
  ],
  views: [
    { id: "visit-table", block: "table", title: "Visits", source: { object: "visit" }, columns: ["title", "status"], sort: "status" },
    { id: "visit-board", block: "board", source: { object: "visit" }, group_by: "status", card: { title: "title", meta: ["status"] }, actions: ["visit_get"] },
    { id: "site-table", block: "table", source: { object: "site" } },
  ],
  pages: [{ id: "home", cells: [{ view: "visit-table" }, { view: "site-table" }], tabs: [{ id: "t", title: "T", cells: [{ view: "visit-board" }] }], side: { view: "visit-table" } }],
}) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("names", () => {
  it("makes names the manifest accepts", () => {
    const taken = new Set(["phone"]);
    expect(nameFrom("Phone", taken, "field")).toBe("phone_2");
    expect(nameFrom("  Due date!! ", new Set(), "field")).toBe("due_date");
    expect(nameFrom("123", new Set(), "field")).toBe("field");
    expect(nameFrom("", new Set(), "field")).toBe("field");
    expect(nameFrom("Visits table", new Set(), "view", "-")).toBe("visits-table");
    expect(nameFrom("x".repeat(90), new Set(), "f")).toMatch(/^[a-z][a-z0-9_]{0,47}$/);
  });
});

describe("objects", () => {
  it("adds an object with one field, and the draft stays valid", () => {
    const r = addObject(base(), "Service call");
    expect(r.name).toBe("service_call");
    const m = r.manifest as ReturnType<typeof base>;
    expect(m.objects.at(-1)).toEqual({ name: "service_call", label: "Service call", fields: [{ key: "name", type: "text", label: "Name" }] });
    expect(checkDraft(m).filter((p) => p.severity === "error")).toEqual([]);
  });

  it("sets and clears the label and keeps the name", () => {
    const set = renameObject(base(), "site", "Customer site") as ReturnType<typeof base>;
    expect(set.objects[1]).toMatchObject({ name: "site", label: "Customer site" });
    const cleared = renameObject(set, "site", "  ") as ReturnType<typeof base>;
    expect(cleared.objects[1]).toEqual({ name: "site", fields: [{ key: "city", type: "text" }] });
  });

  it("names what a delete takes with it", () => {
    expect(objectCut(base(), "visit")).toEqual({ actions: ["visit_list", "visit_get"], views: ["visit-table", "visit-board"] });
    expect(linksTo(base(), "site")).toEqual(["visit.site"]);
    expect(linksTo(base(), "visit")).toEqual([]);
  });

  it("takes its actions, its views and their page cells", () => {
    const m = removeObject(base(), "visit") as ReturnType<typeof base>;
    expect(m.objects.map((o: { name: string }) => o.name)).toEqual(["site"]);
    expect(m.actions.map((a: { id: string }) => a.id)).toEqual(["site_list", "site_get"]);
    expect(m.views.map((v: { id: string }) => v.id)).toEqual(["site-table"]);
    expect(m.pages[0].cells).toEqual([{ view: "site-table" }]);
    expect(m.pages[0].tabs[0].cells).toEqual([]);
    expect("side" in m.pages[0]).toBe(false);
  });

  it("refuses while another object links to it", () => {
    const m = base();
    expect(removeObject(m, "site")).toBe(m);
  });

  it("leaves no dangling name in the views that stay", () => {
    const m = base();
    m.views[2].actions = ["site_get", "visit_get"];
    m.views[2].submit = "visit_list";
    const out = removeObject(m, "visit") as ReturnType<typeof base>;
    expect(out.views[0].actions).toEqual(["site_get"]);
    expect(out.views[0].submit).toBeUndefined();
  });
});

describe("fields", () => {
  it("offers only types that need no more than a label", () => {
    for (const t of ["relation", "money", "file", "person", "geo"]) expect(SAFE_TYPES).not.toContain(t);
    expect(SAFE_TYPES).toContain("select");
  });

  it("adds a field with a key, and a select with options so it validates", () => {
    const a = addField(base(), "site", "Zip code", "text");
    expect(a.key).toBe("zip_code");
    const b = addField(a.manifest, "site", "Kind", "select").manifest as ReturnType<typeof base>;
    expect(b.objects[1].fields.at(-1)).toEqual({ key: "kind", type: "select", label: "Kind", options: ["Option 1", "Option 2"] });
    expect(checkDraft(b).filter((p) => p.path.startsWith("$.objects[1]"))).toEqual([]);
  });

  it("never changes its input", () => {
    const m = base();
    const before = JSON.stringify(m);
    addField(m, "site", "Zip", "text");
    updateField(m, "visit", "title", { label: "X", required: true });
    removeField(m, "visit", "status");
    removeObject(m, "visit");
    addView(m, "table", "site", "T");
    removeView(m, "site-table");
    expect(JSON.stringify(m)).toBe(before);
  });

  it("changes the label, the required flag and the options", () => {
    let m: unknown = updateField(base(), "visit", "title", { label: "Subject", required: true });
    m = updateField(m, "visit", "status", { options: [" open ", "", "blocked"] });
    const f = (m as ReturnType<typeof base>).objects[0].fields;
    expect(f[0]).toEqual({ key: "title", type: "text", label: "Subject", required: true });
    expect(f[1].options).toEqual(["open", "blocked"]);
    const cleared = updateField(m, "visit", "title", { label: "", required: false }) as ReturnType<typeof base>;
    expect(cleared.objects[0].fields[0]).toEqual({ key: "title", type: "text" });
  });

  it("drops the options when the type leaves select, and seeds them when it joins", () => {
    const text = updateField(base(), "visit", "status", { type: "text" }) as ReturnType<typeof base>;
    expect(text.objects[0].fields[1]).toEqual({ key: "status", type: "text" });
    const sel = updateField(base(), "visit", "title", { type: "multiSelect" }) as ReturnType<typeof base>;
    expect(sel.objects[0].fields[0].options).toEqual(["Option 1", "Option 2"]);
  });

  it("takes the field out of the views", () => {
    const out = removeField(base(), "visit", "status") as ReturnType<typeof base>;
    expect(out.objects[0].fields.map((f: { key: string }) => f.key)).toEqual(["title", "site"]);
    expect(out.views[0].columns).toEqual(["title"]);
    expect(out.views[0].sort).toBeUndefined();
    expect(out.views[1].group_by).toBeUndefined();
    expect(out.views[1].card).toEqual({ title: "title", meta: [] });
  });
});

describe("views", () => {
  it("finds the lane field, and offers a Board only for an object with lanes", () => {
    expect(laneField(base().objects[0])).toBe("status");
    expect(objectsFor(base(), "board")).toEqual(["visit"]);
    expect(objectsFor(base(), "table")).toEqual(["visit", "site"]);
  });

  it("makes a Board only over an object with lanes", () => {
    const m = base();
    expect(addView(m, "board", "site", "Sites").id).toBeNull();
    const ok = addView(m, "board", "visit", "By status");
    expect(ok.id).toBe("by-status");
    expect((ok.manifest as ReturnType<typeof base>).views.at(-1)).toEqual({
      id: "by-status", block: "board", title: "By status", source: { object: "visit" }, group_by: "status", card: { title: "title" },
    });
  });

  it("makes a Table with a unique id", () => {
    const r = addView(base(), "table", "site", "");
    expect(r.id).toBe("site-table-2");
    expect((r.manifest as ReturnType<typeof base>).views.at(-1)).toEqual({ id: "site-table-2", block: "table", source: { object: "site" } });
  });

  it("changes the title, and the object resets what named the old one", () => {
    let m: unknown = updateView(base(), "visit-table", { title: "All visits" });
    expect((m as ReturnType<typeof base>).views[0].title).toBe("All visits");
    m = updateView(m, "visit-table", { object: "site" });
    const v = (m as ReturnType<typeof base>).views[0];
    expect(v.source).toEqual({ object: "site" });
    expect(v.columns).toBeUndefined();
    expect(v.sort).toBeUndefined();
    // A Board cannot move to an object with no lanes.
    expect((updateView(base(), "visit-board", { object: "site" }) as ReturnType<typeof base>).views[1].source).toEqual({ object: "visit" });
  });

  it("deletes a view and the page cells that show it", () => {
    expect(viewUses(base(), "visit-table")).toBe(2);
    const out = removeView(base(), "visit-table") as ReturnType<typeof base>;
    expect(out.views.map((v: { id: string }) => v.id)).toEqual(["visit-board", "site-table"]);
    expect(out.pages[0].cells).toEqual([{ view: "site-table" }]);
    expect("side" in out.pages[0]).toBe(false);
  });
});

describe("what a publish does", () => {
  const live = [{ name: "visit", fields: [{ key: "title" }] }];
  it("knows a live field", () => {
    expect(isLiveField(live, "visit", "title")).toBe(true);
    expect(isLiveField(live, "visit", "status")).toBe(false);
  });

  it("tells the true effect of a delete", () => {
    expect(publishEffect("object", true)).toContain("Publish is refused");
    expect(publishEffect("object", true)).toContain("A published object cannot go yet");
    expect(publishEffect("object", true)).toContain("records stay");
    expect(publishEffect("field", true)).toContain("A published field cannot go yet");
    expect(publishEffect("field", false)).toContain("only changes this draft");
  });
});

describe("every reference that the validator checks (P2-b)", () => {
  const rich = () => {
    const m = base();
    m.views[2].source = { object: "site", action: "visit_get" };
    m.skill = { actions: ["visit_get", "site_get"], examples: [{ prompt: "p", action: "visit_list" }, { prompt: "q", action: "site_get" }], cards: ["visit-table", "site-table"] };
    m.shell = { jobs: [{ id: "j1", view: "visit-board" }, { id: "j2", view: "site-table" }], needs: [{ object: "visit" }, { object: "site" }], search: ["visit", "site"] };
    m.workflows = [{ id: "w1", trigger: { record: "visit" } }, { id: "w2", trigger: { record: "site" } }];
    return m;
  };

  it("removeObject clears the skill, the shell, the workflows and a view source", () => {
    const out = removeObject(rich(), "visit") as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(out.skill.actions).toEqual(["site_get"]);
    expect(out.skill.examples).toEqual([{ prompt: "p" }, { prompt: "q", action: "site_get" }]);
    expect(out.skill.cards).toEqual(["site-table"]);
    expect(out.shell.jobs).toEqual([{ id: "j2", view: "site-table" }]);
    expect(out.shell.needs).toEqual([{ object: "site" }]);
    expect(out.shell.search).toEqual(["site"]);
    expect(out.workflows).toEqual([{ id: "w1", trigger: {} }, { id: "w2", trigger: { record: "site" } }]);
    expect(out.views[0].source).toEqual({ object: "site" });
  });

  it("removeView clears the shell jobs and the skill cards that show it", () => {
    const out = removeView(rich(), "site-table") as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(out.shell.jobs).toEqual([{ id: "j1", view: "visit-board" }]);
    expect(out.skill.cards).toEqual(["visit-table"]);
  });

  it("removeField takes the key out of the set of an action of that object", () => {
    const m = base();
    m.actions.push({ id: "visit_done", uses: "object:visit.update", set: { status: "done", title: "x" } }, { id: "only", uses: "object:visit.update", set: { status: "done" } });
    const out = removeField(m, "visit", "status") as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(out.actions.find((a: { id: string }) => a.id === "visit_done").set).toEqual({ title: "x" });
    expect("set" in out.actions.find((a: { id: string }) => a.id === "only")).toBe(false);
  });

  it("a type change drops many and hot that the new type cannot hold", () => {
    const m = base();
    m.objects[0].fields[1] = { key: "status", type: "select", options: ["a"], hot: true, many: true };
    const multi = updateField(m, "visit", "status", { type: "multiSelect" }) as ReturnType<typeof base>;
    expect(multi.objects[0].fields[1]).toEqual({ key: "status", type: "multiSelect", options: ["a"] });
    const url = updateField(multi, "visit", "status", { type: "url" }) as ReturnType<typeof base>;
    expect(url.objects[0].fields[1]).toEqual({ key: "status", type: "url" });
  });

  it("a Board loses group_by when its lane field stops being a select", () => {
    const out = updateField(base(), "visit", "status", { type: "text" }) as ReturnType<typeof base>;
    expect(out.views[1].group_by).toBeUndefined();
    expect(out.views[1].block).toBe("board");
    const keep = updateField(base(), "visit", "title", { type: "number" }) as ReturnType<typeof base>;
    expect(keep.views[1].group_by).toBe("status");
  });
});

describe("a label keeps its spaces until it is committed (P1-e)", () => {
  it("keeps the inner space of a label", () => {
    const m = renameObject(base(), "site", "Site visit") as ReturnType<typeof base>;
    expect(m.objects[1].label).toBe("Site visit");
    const f = updateField(base(), "visit", "title", { label: "Due date" }) as ReturnType<typeof base>;
    expect(f.objects[0].fields[0].label).toBe("Due date");
  });

  it("commits the trimmed text, and only when it changed", () => {
    expect(commitText("Site", "Site visit ")).toBe("Site visit");
    expect(commitText("Site visit", " Site visit ")).toBeNull();
    expect(commitText("Site", "")).toBe("");
  });
});

// ── Fix round 2: the cascade output must pass the validator ─────────────────

const ops = (op: string) => `__${op}`;
void ops;
const acts = (o: string) => ["list", "get", "create", "update", "delete"].map((v) => ({ id: `${o}_${v}`, uses: `object:${o}.${v}` }));
const validBase = () => ({
  studio: 1, id: "field-visits", name: "Field visits", purpose: "Plan and close site visits.",
  objects: [
    { name: "visit", label: "Visit", fields: [
      { key: "title", type: "text", label: "Title" },
      { key: "status", type: "select", options: ["planned", "done"] },
    ], rules: [{ name: "visit_flow", kind: "state_machine", field: "status", initial: "planned", moves: [{ from: "planned", to: "done" }] }] },
    { name: "site", fields: [{ key: "city", type: "text" }, { key: "zip", type: "text" }] },
  ],
  actions: [...acts("visit"), ...acts("site")],
  views: [
    { id: "visit-table", block: "table", title: "Visits", source: { object: "visit" }, columns: ["title", "status"] },
    { id: "site-table", block: "table", source: { object: "site" } },
  ],
  pages: [{ id: "home", cols: 2, cells: [{ view: "visit-table" }] }, { id: "sites", cols: 2, cells: [{ view: "site-table" }] }],
  skill: { description: "Answers about visits", actions: ["visit_list", "object:visit.create", "site_get"], examples: [{ prompt: "p", action: "object:visit.update" }, { prompt: "q", action: "site_get" }], cards: ["visit-table", "site-table"] },
  shell: { jobs: [{ verb: "open", view: "visit-table" }, { verb: "open sites", view: "site-table" }], needs: [{ object: "visit" }, { object: "site" }], search: ["visit", "site"] },
  workflows: [{ id: "w1", trigger: { record: "visit" } }],
});

describe("the cascade output passes the validator (P2 round 2)", () => {
  const FIXTURE = new URL("../../../../../tests/fixtures/studio_cascade_cases.json", import.meta.url);
  const cases = () => ({
    base: validBase(),
    cases: [
      { name: "delete the visit object", output: removeObject(validBase(), "visit") },
      { name: "delete the visit-table view", output: removeView(validBase(), "visit-table") },
      { name: "delete the zip field", output: removeField(validBase(), "site", "zip") },
    ],
  });

  it("writes the fixture that the Python validator reads, and keeps it equal to the output", () => {
    const now = cases();
    if (process.env.G3C_WRITE_FIXTURE) writeFileSync(FIXTURE, JSON.stringify(now, null, 2) + "\n");
    expect(JSON.parse(readFileSync(FIXTURE, "utf8"))).toEqual(JSON.parse(JSON.stringify(now)));
  });

  it("drops the skill actions and examples that name the deleted object by their uses", () => {
    const out = removeObject(validBase(), "visit") as ReturnType<typeof validBase>;
    expect(out.skill.actions).toEqual(["site_get"]);
    expect(out.skill.examples).toEqual([{ prompt: "p" }, { prompt: "q", action: "site_get" }]);
  });

  it("drops a page that is left with no tabs and no cells", () => {
    const out = removeView(validBase(), "visit-table") as ReturnType<typeof validBase>;
    expect(out.pages.map((p) => p.id)).toEqual(["sites"]);
    const keep = removeObject(validBase(), "site") as ReturnType<typeof validBase>;
    expect(keep.pages.map((p) => p.id)).toEqual(["home"]);
  });

  it("blocks the delete of a field that a rule names, and names the rule", () => {
    const m = validBase();
    expect(rulesNaming(m, "visit", "status")).toEqual(["visit: visit_flow"]);
    expect(rulesNaming(m, "visit", "title")).toEqual([]);
    expect(removeField(m, "visit", "status")).toBe(m);
    const formula = { ...m, objects: [m.objects[0], { ...m.objects[1], rules: [{ name: "lbl", kind: "formula", target: "city", expr: "zip + 1" }] }] };
    expect(rulesNaming(formula, "site", "zip")).toEqual(["site: lbl"]);
    expect(rulesNaming(formula, "site", "zi")).toEqual([]);
    const rollup = { ...m, objects: [{ ...m.objects[0], rules: [{ name: "n", kind: "rollup", target: "title", source: "site", via: "x", agg: "sum", field: "zip" }] }, m.objects[1]] };
    expect(rulesNaming(rollup, "site", "zip")).toEqual(["visit: n"]);
  });
});
