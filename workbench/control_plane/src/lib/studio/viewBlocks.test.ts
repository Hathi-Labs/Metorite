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
import { VIEW_LIMIT, mapView, type ViewMapContext } from "./viewBlocks";

const visit = {
  name: "visit", label: "Visit", rules: [],
  fields: [
    { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
    { key: "status", type: "select", label: "Status", required: false, many: false, options: ["open", "done"], to: null, hot: false },
    { key: "notes", type: "longText", label: "Notes", required: false, many: false, options: null, to: null, hot: false },
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

describe("mapView: kinds that are not built, and the door guard", () => {
  it("gives null for a kind that is not built, so StudioPage shows the not-built card", () => {
    for (const block of ["calendar", "timeline", "mindMap", "processFlow", "checklist", "map", "markdown"] as const) {
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

describe("the ready apps map with no problem view", () => {
  const dir = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_templates");
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));

  it("finds the six ready apps", () => {
    expect(files.sort()).toEqual([
      "expense_claims.json", "field_visits.json", "hiring.json", "purchase_requests.json", "spares.json", "support_desk.json",
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
