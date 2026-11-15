/**
 * STU-AF1 fence: the mapper from a manifest view to its block. Each built kind maps, chart and
 * stat read through the `aggregate` op, a kind that is not built gives null, and a source that
 * names an undeclared action is refused with the sentence of the door guard.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { ViewDef } from "./manifest";
import { NOT_DECLARED_SENTENCE, READ_ONLY_SENTENCE, type DeclaredAction, type ObjectRow } from "./realDoor";
import { CALENDAR_START_SENTENCE, MAX_FILTERS, TREE_RULE_SENTENCE, VIEW_LIMIT, mapView, type ViewMapContext } from "./viewBlocks";
import { MAX_TREE_NODES } from "./treeModel";

const visit = {
  name: "visit", label: "Visit", rules: [],
  fields: [
    { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
    { key: "status", type: "select", label: "Status", required: false, many: false, options: ["open", "done"], to: null, hot: false },
    { key: "notes", type: "longText", label: "Notes", required: false, many: false, options: null, to: null, hot: false },
  ],
} as unknown as ObjectRow;

/** The visit object with the date fields that a calendar needs. */
const dated = {
  ...visit,
  fields: [
    ...(visit.fields as unknown as object[]),
    { key: "visit_at", type: "datetime", label: "Visit at", required: false, many: false, options: null, to: null, hot: true },
    { key: "visit_end", type: "datetime", label: "Visit end", required: false, many: false, options: null, to: null, hot: false },
    { key: "visit_day", type: "date", label: "Visit day", required: false, many: false, options: null, to: null, hot: true },
  ],
} as unknown as ObjectRow;

const ACTIONS: DeclaredAction[] = [
  { id: "list_visits", uses: "object:visit.list" },
  { id: "update_visit", uses: "object:visit.update" },
  { id: "create_visit", uses: "object:visit.create" },
  { id: "count_visits", uses: "object:visit.aggregate" },
];
const READS = ACTIONS.filter((a) => /\.(list|aggregate)$/.test(a.uses));

const ctx = (over: Partial<ViewMapContext> = {}): ViewMapContext => ({
  actions: ACTIONS, objects: [visit], owner: true, ...over,
});
const view = (v: Partial<ViewDef> & { id: string; block: ViewDef["block"] }): ViewDef => v;

describe("mapView: table", () => {
  it("draws the dataGrid on the list of the object, and edits when update is declared", () => {
    const m = mapView(view({ id: "v", block: "table", object: "visit", title: "All visits" }), ctx());
    expect(m?.kind).toBe("node");
    if (m?.kind !== "node") return;
    expect(m.node.props.name).toBe("dataGrid");
    expect(m.node.props.source).toEqual({ action: "object:visit.list", args: { limit: VIEW_LIMIT } });
    const data = m.node.props.data as { title: string; fields: { key: string }[]; editable?: boolean };
    expect(data.title).toBe("All visits");
    expect(data.editable).toBe(true);
    // A long text stays out of the Table.
    expect(data.fields.map((f) => f.key)).toEqual(["title", "status"]);
  });

  it("lists the fields that the view names, in its order", () => {
    const m = mapView(view({ id: "v", block: "table", object: "visit", fields: ["status", "title"] }), ctx());
    const data = (m as unknown as { node: { props: { data: { fields: { key: string }[] } } } }).node.props.data;
    expect(data.fields.map((f) => f.key)).toEqual(["status", "title"]);
  });

  it("does not edit for a viewer", () => {
    const m = mapView(view({ id: "v", block: "table", object: "visit" }), ctx({ actions: READS, owner: false }));
    const data = (m as unknown as { node: { props: { data: { editable?: boolean } } } }).node.props.data;
    expect(data.editable).toBeUndefined();
  });

  it("reads the object from the action that the source names", () => {
    const m = mapView(view({ id: "v", block: "table", source: { action: "list_visits" } }), ctx());
    expect(m?.kind).toBe("node");
  });

  it("says so for an object that the app does not have", () => {
    const m = mapView(view({ id: "v", block: "table", object: "ghost" }), ctx());
    expect(m).toMatchObject({ kind: "problem" });
  });
});

describe("mapView: board", () => {
  it("draws the board with lanes by the select field", () => {
    const m = mapView(view({ id: "b", block: "board", object: "visit" }), ctx());
    expect(m?.kind).toBe("node");
    expect((m as unknown as { node: { props: { name: string } } }).node.props.name).toBe("board");
  });

  it("says why an object with no select field has no board", () => {
    const flat = { ...visit, fields: visit.fields.filter((f) => f.type !== "select") } as ObjectRow;
    const m = mapView(view({ id: "b", block: "board", object: "visit" }), ctx({ objects: [flat] }));
    expect(m).toMatchObject({ kind: "problem" });
    expect((m as { reason: string }).reason).toMatch(/select field/);
  });
});

describe("mapView: chart and stat read through the aggregate op", () => {
  it("maps a chart view to the chart template on object:<x>.aggregate", () => {
    const m = mapView(view({
      id: "c", block: "chart", title: "By status",
      source: { action: "count_visits", args: { kind: "bar", group_by: "status", measures: [{ op: "count" }] } },
    }), ctx());
    expect(m?.kind).toBe("node");
    const p = (m as unknown as { node: { props: Record<string, unknown> } }).node.props;
    expect(p.name).toBe("chart");
    expect((p.source as { action: string }).action).toBe("object:visit.aggregate");
    expect((p.data as { type: string }).type).toBe("bar");
  });

  it("maps a stat view to the sparkTrend template on object:<x>.aggregate", () => {
    const m = mapView(view({
      id: "s", block: "stat", title: "Visits", source: { action: "count_visits", args: { measures: [{ op: "count" }] } },
    }), ctx());
    const p = (m as unknown as { node: { props: Record<string, unknown> } }).node.props;
    expect(p.name).toBe("sparkTrend");
    expect((p.source as { action: string }).action).toBe("object:visit.aggregate");
  });

  it("gives a plain sentence for a chart with no kind", () => {
    const m = mapView(view({ id: "c", block: "chart", source: { action: "count_visits" } }), ctx());
    expect(m).toMatchObject({ kind: "problem" });
  });
});

describe("mapView: form and detail", () => {
  it("gives the form its fields without a computed one, and its create action", () => {
    const m = mapView(view({ id: "f", block: "form", object: "visit", submit: "create_visit" }), ctx());
    expect(m).toMatchObject({ kind: "form", action: "object:visit.create", title: "New Visit" });
  });

  it("gives a viewer the read-only sentence in place of the form", () => {
    const m = mapView(view({ id: "f", block: "form", object: "visit" }), ctx({ actions: READS, owner: false }));
    expect(m).toEqual({ kind: "problem", reason: READ_ONLY_SENTENCE });
  });

  it("gives null for a form whose declared submit is not a create, so the not-built card shows", () => {
    const m = mapView(view({ id: "f", block: "form", object: "visit", submit: "update_visit" }), ctx());
    expect(m).toBeNull();
  });

  it("gives the detail the list that holds its record", () => {
    const m = mapView(view({ id: "d", block: "detail", object: "visit" }), ctx());
    expect(m).toMatchObject({ kind: "detail", source: { action: "object:visit.list" } });
  });
});

describe("mapView: calendar", () => {
  const cctx = (over: Partial<ViewMapContext> = {}) => ctx({ objects: [dated], ...over });
  const cal = (v: Partial<ViewDef> = {}) =>
    view({ id: "c", block: "calendar", object: "visit", start: "visit_at", ...v });

  it("maps to the calendar model with the start, the end, the list and the link base", () => {
    const m = mapView(cal({ end: "visit_end", title: "Field visits" }), cctx({ recordBase: (o) => `?ro=${o}&rec=` }));
    expect(m).toEqual({
      kind: "calendar", object: "visit", start: "visit_at", startType: "datetime", end: "visit_end", titleField: "title",
      source: { action: "object:visit.list", args: { limit: VIEW_LIMIT } }, openBase: "?ro=visit&rec=", title: "Field visits",
    });
  });

  it("takes a date field as the start, and gives no link when the frame has no record page", () => {
    const m = mapView(cal({ start: "visit_day" }), cctx());
    expect(m).toMatchObject({ kind: "calendar", start: "visit_day", startType: "date", end: null, openBase: null });
  });

  it("keeps the own filter of the view in the source, before the window", () => {
    const filter = [{ field: "status", op: "eq", value: "open" }];
    const m = mapView(cal({ source: { action: "list_visits", args: { filter } } }), cctx());
    expect(m).toMatchObject({ source: { args: { limit: VIEW_LIMIT, filter } } });
  });

  describe("the me token (STU-AF3b)", () => {
    const personField = { key: "owner", type: "person", label: "Owner", required: false, many: false, options: null, to: null, hot: true };
    const withPerson = { ...dated, fields: [...dated.fields, personField] } as unknown as ObjectRow;
    const meView = (filter: unknown[], extra: Record<string, unknown> = {}) =>
      cal({ source: { action: "list_visits", args: { filter, ...extra } } as ViewDef["source"] });
    const mineF = [{ field: "owner", op: "eq", value: "me" }];

    it("waits for the session email and shows the loading model", () => {
      expect(mapView(meView(mineF), cctx({ objects: [withPerson], email: null }))).toEqual({ kind: "loading" });
    });
    it("carries the lower-cased email in the source filter", () => {
      const m = mapView(meView(mineF), cctx({ objects: [withPerson], email: "Ann@X.io" }));
      expect(m).toMatchObject({ kind: "calendar", source: { args: { filter: [{ field: "owner", op: "eq", value: "ann@x.io" }] } } });
    });
    it("a view with no source args reads as before", () => {
      expect(mapView(cal(), cctx({ objects: [withPerson], email: null }))).toMatchObject({
        kind: "calendar", source: { args: { limit: VIEW_LIMIT } },
      });
    });
    it("a me string on a field that is not a person stays a literal", () => {
      const lit = [{ field: "status", op: "eq", value: "me" }];
      const m = mapView(meView(lit), cctx({ objects: [withPerson], email: null }));
      expect(m).toMatchObject({ kind: "calendar", source: { args: { filter: lit } } });
    });
    it("never takes the sort of the view", () => {
      const m = mapView(meView([], { sort: { field: "status", dir: "desc" } }), cctx({ objects: [withPerson], email: "a@b.c" }));
      expect(m).toMatchObject({ kind: "calendar" });
      expect((m as { source: { args: Record<string, unknown> } }).source.args).not.toHaveProperty("sort");
    });
    it("a missing start field still gives its sentence while the email is unknown", () => {
      expect(mapView(meView(mineF, {}), cctx({ objects: [withPerson], email: null }))).toEqual({ kind: "loading" });
      const bad = cal({ start: null, source: { action: "list_visits", args: { filter: mineF } } as ViewDef["source"] });
      expect(mapView(bad, cctx({ objects: [withPerson], email: null }))).toEqual({ kind: "problem", reason: CALENDAR_START_SENTENCE });
    });
  });

  it("gives a plain sentence when the view has no start field", () => {
    expect(mapView(cal({ start: null }), cctx())).toEqual({ kind: "problem", reason: CALENDAR_START_SENTENCE });
    expect(mapView(cal({ start: "no_such" }), cctx())).toEqual({ kind: "problem", reason: CALENDAR_START_SENTENCE });
    expect(CALENDAR_START_SENTENCE).toBe("This calendar needs a start field that is a date or datetime.");
  });

  it("gives that sentence when the start is a text field", () => {
    expect(mapView(cal({ start: "title" }), cctx())).toEqual({ kind: "problem", reason: CALENDAR_START_SENTENCE });
  });

  it("ignores an end that is not a date or datetime", () => {
    expect(mapView(cal({ end: "title" }), cctx())).toMatchObject({ kind: "calendar", end: null });
  });

  it("refuses a source that names an undeclared action", () => {
    const m = mapView(cal({ source: { action: "no-such-action" } }), cctx());
    expect(m).toEqual({ kind: "problem", reason: NOT_DECLARED_SENTENCE });
  });

  it("reads for a viewer, and a viewer without a list gets the not-declared sentence", () => {
    expect(mapView(cal(), cctx({ actions: READS, owner: false }))).toMatchObject({ kind: "calendar" });
    expect(mapView(cal(), cctx({ actions: [] }))).toEqual({ kind: "problem", reason: NOT_DECLARED_SENTENCE });
  });

  it("names the record by card.title, else the first text field, else the id", () => {
    expect(mapView(cal({ card: { title: "status" } }), cctx())).toMatchObject({ titleField: "status" });
    expect(mapView(cal({ card: { title: "no_such" } }), cctx())).toMatchObject({ titleField: "title" });
    const noText = { ...dated, fields: (dated.fields as unknown as { type: string }[]).filter((f) => f.type !== "text") } as unknown as ObjectRow;
    expect(mapView(cal(), ctx({ objects: [noText] }))).toMatchObject({ titleField: "id" });
  });
});

describe("mapView: kinds that are not built, and the door guard", () => {
  it("gives null for a kind that is not built, so StudioPage shows the not-built card", () => {
    for (const block of ["timeline", "mindMap", "processFlow", "checklist", "map", "markdown"] as const) {
      expect(mapView(view({ id: "x", block, object: "visit" }), ctx())).toBeNull();
    }
  });

  it("refuses a source that names an undeclared action, for each built kind", () => {
    for (const block of ["table", "board", "chart", "stat", "detail"] as const) {
      const m = mapView(view({ id: "x", block, object: "visit", source: { action: "no-such-action" } }), ctx());
      expect(m, block).toEqual({ kind: "problem", reason: NOT_DECLARED_SENTENCE });
    }
  });

  it("refuses a form whose submit is not declared", () => {
    const m = mapView(view({ id: "f", block: "form", object: "visit", submit: "ghost" }), ctx());
    expect(m).toEqual({ kind: "problem", reason: NOT_DECLARED_SENTENCE });
  });

  it("refuses a list when the manifest declares no list action", () => {
    const m = mapView(view({ id: "v", block: "table", object: "visit" }), ctx({ actions: [] }));
    expect(m).toEqual({ kind: "problem", reason: NOT_DECLARED_SENTENCE });
  });
});

describe("mapView: tree (D18-2b)", () => {
  const field = (key: string, type: string, extra: Record<string, unknown> = {}) =>
    ({ key, type, label: key, required: false, many: false, options: null, to: null, hot: false, ...extra });
  const part = {
    name: "part", label: "Part",
    fields: [field("code", "text"), field("name", "text"), field("parent", "relation", { to: "part" }), field("notes", "longText"),
      field("qty", "number"), field("unit", "text")],
    rules: [{ kind: "hierarchy", name: "tree", field: "parent" }],
  } as unknown as ObjectRow;
  const TREE_ACTIONS: DeclaredAction[] = [
    { id: "tree_parts", uses: "object:part.tree" },
    { id: "update_part", uses: "object:part.update" },
  ];
  const tctx = (over: Partial<ViewMapContext> = {}): ViewMapContext => ({
    actions: TREE_ACTIONS, objects: [part], owner: true, ...over,
  });
  const tv = (over: Partial<ViewDef> = {}) => view({ id: "bom", block: "tree", object: "part", ...over });

  it("maps to a tree on the tree read, with the cap of one read", () => {
    const m = mapView(tv({ title: "Bill of materials" }), tctx());
    expect(m).toEqual({
      kind: "tree", title: "Bill of materials", object: "part", field: "parent", labelField: "code",
      metaFields: [expect.objectContaining({ key: "name" }), expect.objectContaining({ key: "qty" })],
      source: { action: "object:part.tree", args: { limit: MAX_TREE_NODES } },
      canMove: true, update: "object:part.update",
    });
  });

  it("is a plain problem sentence when the object has no hierarchy rule", () => {
    const flat = { ...part, rules: [] } as unknown as ObjectRow;
    expect(mapView(tv(), tctx({ objects: [flat] }))).toEqual({ kind: "problem", reason: TREE_RULE_SENTENCE });
    expect(TREE_RULE_SENTENCE).toBe("This tree needs an object with a hierarchy rule.");
  });

  it("takes the label and the meta from the fields of the view, in its order", () => {
    const m = mapView(tv({ fields: ["name", "code", "unit", "qty", "notes"] }), tctx());
    expect(m).toMatchObject({ labelField: "name" });
    const keys = (m as unknown as { metaFields: { key: string }[] }).metaFields.map((f) => f.key);
    // Two meta fields at most. A long text and the parent field never show.
    expect(keys).toEqual(["code", "unit"]);
  });

  it("falls back to the id when the view lists no text field", () => {
    const m = mapView(tv({ fields: ["qty", "parent"] }), tctx());
    expect(m).toMatchObject({ kind: "tree", labelField: null });
  });

  it("moves only when the app declares the update and the member may write", () => {
    expect(mapView(tv(), tctx())).toMatchObject({ canMove: true, update: "object:part.update" });
    expect(mapView(tv(), tctx({ actions: [TREE_ACTIONS[0]] }))).toMatchObject({ canMove: false, update: null });
    // A viewer holds the reads only, and the tree read stays.
    const viewer = mapView(tv(), tctx({ actions: [TREE_ACTIONS[0]], owner: false }));
    expect(viewer).toMatchObject({ kind: "tree", canMove: false, update: null });
    // An owner flag false hides the move even when the update is declared.
    expect(mapView(tv(), tctx({ owner: false }))).toMatchObject({ canMove: false, update: null });
  });

  it("follows the table path for a viewer without the read", () => {
    expect(mapView(tv(), tctx({ actions: [], owner: false }))).toEqual({ kind: "problem", reason: NOT_DECLARED_SENTENCE });
    expect(mapView(tv(), tctx({ actions: [TREE_ACTIONS[1]] }))).toEqual({ kind: "problem", reason: NOT_DECLARED_SENTENCE });
  });

  it("ignores a filter of the view, and does not wait for the email", () => {
    const filter = [{ field: "owner", op: "eq", value: "me" }];
    const m = mapView(tv({ source: { args: { filter } } }), tctx({ email: null }));
    expect(m).toMatchObject({ kind: "tree", source: { action: "object:part.tree", args: { limit: MAX_TREE_NODES } } });
    expect(JSON.stringify(m)).not.toContain("filter");
  });
});

describe("the ready apps map with no problem view", () => {
  const dir = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_templates");
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));

  it("finds the eight ready apps", () => {
    expect(files.sort()).toEqual([
      "expense_claims.json", "field_visits.json", "hiring.json", "purchase_requests.json",
      "shipment_tracker.json", "site_survey.json", "spares.json", "support_desk.json",
    ]);
  });

  for (const file of files) {
    it(`${file}: no view gives a problem, for the owner and for a viewer`, () => {
      const m = JSON.parse(readFileSync(resolve(dir, file), "utf-8"));
      const objects = (m.objects ?? []).map((o: Record<string, unknown>) => ({ rules: [], ...o }));
      const actions: DeclaredAction[] = m.actions ?? [];
      for (const owner of [true, false]) {
        const acts = owner ? actions : actions.filter((a) => /\.(list|get|aggregate)$/.test(a.uses));
        for (const v of m.views as ViewDef[]) {
          const out = mapView(v, { actions: acts, objects, owner });
          // A viewer sees the read-only sentence in place of a form, and that is the one allowed problem.
          const allowed = !owner && v.block === "form";
          if (!allowed) expect(out?.kind, `${file} ${v.id} owner=${owner}`).not.toBe("problem");
        }
      }
    });
  }

  it("field visits: the check_in form with an update submit is not built yet", () => {
    const m = JSON.parse(readFileSync(resolve(dir, "field_visits.json"), "utf-8"));
    const v = (m.views as ViewDef[]).find((x) => x.id === "check_in") as ViewDef;
    const objects = (m.objects ?? []).map((o: Record<string, unknown>) => ({ rules: [], ...o }));
    expect(mapView(v, { actions: m.actions, objects, owner: true })).toBeNull();
  });
});

describe("mapView: the me token and the own filter of a view (STU-AF3b)", () => {
  const person = { ...visit, fields: [...visit.fields, { key: "owner", type: "person", label: "Owner", required: false, many: false, options: null, to: null, hot: true }] } as unknown as ObjectRow;
  const mine = view({ id: "v", block: "table", object: "visit", source: { action: "list_visits", args: {
    kind: "x", filter: [{ field: "owner", op: "eq", value: "me" }], sort: { field: "status", dir: "desc" } } } });
  const list = (m: ReturnType<typeof mapView>) => (m?.kind === "node" ? m.node.props.source : null);

  it("the table source carries the email of the member, as the count of the rail does", () => {
    expect(list(mapView(mine, ctx({ objects: [person], email: "Ann@X.io" })))).toEqual({
      action: "object:visit.list",
      args: { limit: VIEW_LIMIT, filter: [{ field: "owner", op: "eq", value: "ann@x.io" }], sort: { field: "status", dir: "desc" } },
    });
  });
  it("the board source carries it too", () => {
    const board = { ...mine, block: "board" as const };
    const src = list(mapView(board, ctx({ objects: [person], email: "ann@x.io" }))) as { args: { filter: unknown[] } };
    expect(src.args.filter).toEqual([{ field: "owner", op: "eq", value: "ann@x.io" }]);
  });
  it("a token on a field that is not a person stays as written", () => {
    const text = view({ id: "v", block: "table", object: "visit", source: { action: "list_visits", args: { filter: [{ field: "title", op: "eq", value: "me" }] } } });
    const src = list(mapView(text, ctx({ email: "ann@x.io" }))) as { args: { filter: unknown[] } };
    expect(src.args.filter).toEqual([{ field: "title", op: "eq", value: "me" }]);
  });
  it("the list bar adds to the filter of the view, and its sort wins", () => {
    const lens = { view: "v", args: { filter: [{ field: "status", op: "eq", value: "open" }], sort: { field: "title", dir: "asc" as const } } };
    const src = list(mapView(mine, ctx({ objects: [person], email: "a@b.c", lens }))) as { args: { filter: unknown[]; sort: unknown } };
    expect(src.args.filter).toHaveLength(2);
    expect(src.args.sort).toEqual({ field: "title", dir: "asc" });
  });
  it("a view with no source args reads as before", () => {
    expect(list(mapView(view({ id: "v", block: "table", object: "visit" }), ctx()))).toEqual({ action: "object:visit.list", args: { limit: VIEW_LIMIT } });
  });
  it("the cap is the door limit of 8", () => {
    expect(MAX_FILTERS).toBe(8);
  });
  it("the join holds 8 conditions at most, and the view's own come first", () => {
    const c = (n: string, i: number) => ({ field: "status", op: "eq", value: `${n}${i}` });
    const own5 = view({ id: "v", block: "table", object: "visit", source: { action: "list_visits", args: {
      filter: [0, 1, 2, 3, 4].map((i) => c("v", i)) } } });
    const lens = { view: "v", args: { filter: [0, 1, 2, 3, 4].map((i) => c("b", i)) } };
    const src = list(mapView(own5, ctx({ lens }))) as { args: { filter: { value: string }[] } };
    expect(src.args.filter).toHaveLength(8);
    expect(src.args.filter.slice(0, 5).map((f) => f.value)).toEqual(["v0", "v1", "v2", "v3", "v4"]);
  });
  it("a me view does not read until the email is known", () => {
    const m = mapView(mine, ctx({ objects: [person], email: null }));
    expect(m).toEqual({ kind: "loading" });
    expect(list(m)).toBeNull();
    expect(list(mapView(mine, ctx({ objects: [person], email: "Ann@X.io" })))).not.toBeNull();
  });
});
