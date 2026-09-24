/**
 * WS-27bn R1 — the report builder's choices (`projects_reports.md` §8 R1).
 *
 * The builder's claims are pure, so they are pinned here without a DOM:
 * the saved `config` holds exactly the chosen chips, the scope picker offers
 * only the tree and "Whole organization", and an edit sends the whole config.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { peek, put } from "@/lib/dataCache";

import { type ProjectRow, type ReportRow, isReadOnlyPost, projectsApi, projectsKey } from "./api";
import {
  DEFAULT_REPORT_SECTIONS,
  PERIODS,
  REPORT_SECTIONS,
  SAVED_PERIOD,
  WHOLE_ORGANIZATION,
  builderStateFrom,
  configFor,
  createPayload,
  newBuilderState,
  patchPayload,
  periodKey,
  periodOptions,
  saveRefusal,
  scopeOptions,
  toggleSection,
  withPeriod,
} from "./reportBuilder";

const TREE: ProjectRow[] = [
  {
    id: "space-1",
    name: "Hardware",
    kind: "project",
    children: [
      { id: "folder-1", name: "Printers", kind: "folder", parent_project_id: "space-1" },
      {
        id: "proj-1",
        name: "Printer v3",
        kind: "project",
        parent_project_id: "space-1",
        children: [
          { id: "proj-2", name: "Firmware", kind: "project", parent_project_id: "proj-1" },
        ],
      },
    ],
  },
  { id: "space-2", name: "Design", kind: "project" },
];

describe("the create payload holds exactly the chosen chips", () => {
  it("scope, period, subtree and sections land in config as chosen", () => {
    let state = newBuilderState();
    state = { ...state, projectId: "proj-1", name: "  Printer month  " };
    state = withPeriod(state, "last_4_weeks");
    state = { ...state, includeSubtree: false };
    state = { ...state, sections: toggleSection(state.sections, "throughput") };
    state = { ...state, sections: toggleSection(state.sections, "conflicts") };
    state = { ...state, sections: toggleSection(state.sections, "capacity") };

    expect(createPayload(state)).toEqual({
      name: "Printer month",
      project_id: "proj-1",
      config: {
        weeks: 4,
        skip_current_week: true,
        include_subtree: false,
        // The server's SECTIONS order, never the order of the clicks.
        sections: ["finished", "load", "capacity", "stuck", "conflicts"],
      },
    });
  });

  it("the whole organization is project_id null", () => {
    const state = newBuilderState();
    expect(state.projectId).toBeNull();
    expect(createPayload(state).project_id).toBeNull();
  });

  it("a new report starts from the server's defaults", () => {
    expect(configFor(newBuilderState())).toEqual({
      weeks: 1,
      skip_current_week: true,
      include_subtree: true,
      sections: [...DEFAULT_REPORT_SECTIONS],
    });
  });
});

describe("the scope picker offers the tree and the whole organization only", () => {
  it("lists Whole organization, then every tree node, and nothing else", () => {
    const options = scopeOptions(TREE);
    expect(options[0]).toEqual({
      value: WHOLE_ORGANIZATION,
      label: "Whole organization",
      depth: 0,
    });
    expect(options.map((o) => o.value)).toEqual([
      WHOLE_ORGANIZATION,
      "space-1",
      "folder-1",
      "proj-1",
      "proj-2",
      "space-2",
    ]);
    // No people and no teams: those are R5, behind may_report_on (§7.1).
    for (const o of options) {
      expect(o.value).not.toContain("@");
      expect(o.value).not.toMatch(/^group:/);
    }
  });

  it("indents by depth, so the tree still reads as a tree", () => {
    const depth = Object.fromEntries(scopeOptions(TREE).map((o) => [o.value, o.depth]));
    expect(depth["space-1"]).toBe(0);
    expect(depth["proj-1"]).toBe(1);
    expect(depth["proj-2"]).toBe(2);
  });

  it("an empty tree still offers the whole organization", () => {
    expect(scopeOptions([]).map((o) => o.label)).toEqual(["Whole organization"]);
  });
});

describe("the period chip offers only what the config can express", () => {
  it("offers three periods and their exact config", () => {
    expect(PERIODS.map((p) => [p.label, p.weeks, p.skip_current_week])).toEqual([
      ["Last week", 1, true],
      ["This week", 1, false],
      ["The last 4 weeks", 4, true],
    ]);
  });

  it("keeps a saved period that no option matches, and changes nothing", () => {
    const state = { ...newBuilderState(), weeks: 2, skipCurrentWeek: true };
    expect(periodKey(state)).toBeNull();
    const values = periodOptions(state).map((o) => o.value);
    expect(values).toContain(SAVED_PERIOD);
    expect(withPeriod(state, SAVED_PERIOD)).toBe(state);
  });

  it("offers no saved option when an offered period matches", () => {
    expect(periodOptions(newBuilderState()).map((o) => o.value)).not.toContain(SAVED_PERIOD);
  });
});

describe("sections", () => {
  it("mirrors the server's SECTIONS order", () => {
    expect(REPORT_SECTIONS.map((s) => s.key)).toEqual([
      "finished",
      "throughput",
      "load",
      "capacity",
      "stuck",
      "conflicts",
    ]);
  });

  it("the last section stays, because the server refuses an empty list", () => {
    expect(toggleSection(["load"], "load")).toEqual(["load"]);
  });
});

describe("an edit sends the WHOLE config", () => {
  const row: ReportRow = {
    id: "r-1",
    project_id: "proj-1",
    scope: "node",
    name: "Printer",
    config: {
      weeks: 2,
      skip_current_week: false,
      include_subtree: false,
      sections: ["stuck", "load"],
    },
    created_by: "a@example.test",
    created_at: "2026-09-24T00:00:00Z",
  };

  it("a rename alone still carries every config field", () => {
    const state = { ...builderStateFrom(row), name: "Printer, renamed" };
    expect(patchPayload(state)).toEqual({
      name: "Printer, renamed",
      config: {
        weeks: 2,
        skip_current_week: false,
        include_subtree: false,
        sections: ["load", "stuck"],
      },
    });
  });
});

describe("saveRefusal", () => {
  it("refuses a blank name and a name over 120 characters", () => {
    expect(saveRefusal({ ...newBuilderState(), name: "   " })).toMatch(/name/);
    expect(saveRefusal({ ...newBuilderState(), name: "x".repeat(121) })).toMatch(/120/);
    expect(saveRefusal(newBuilderState())).toBeNull();
  });
});

describe("a preview drops no cached read, and a write does", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch(): void {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    );
  }

  it("isReadOnlyPost names only a POST to a preview", () => {
    expect(isReadOnlyPost("POST", "reports/preview")).toBe(true);
    expect(isReadOnlyPost("POST", "tasks/move/preview")).toBe(true);
    expect(isReadOnlyPost("POST", "reports")).toBe(false);
    expect(isReadOnlyPost("PATCH", "reports/preview")).toBe(false);
  });

  it("the preview keeps the tree in the cache", async () => {
    stubFetch();
    const key = projectsKey("tree");
    put(key, { rows: [], total: 0 });
    await projectsApi.previewReport({
      project_id: null,
      name: "",
      config: configFor(newBuilderState()),
    });
    expect(peek(key)).toBeDefined();
  });

  it("a PATCH still drops it", async () => {
    stubFetch();
    const key = projectsKey("tree");
    put(key, { rows: [], total: 0 });
    await projectsApi.patchReport("r-1", patchPayload(newBuilderState()));
    expect(peek(key)).toBeUndefined();
  });
});
