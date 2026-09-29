/**
 * WS-27bn R5f: Overview, the start of the one Reports app.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R5f, done-when (c), (d)
 * and (e). The functions are pure, so each rule is pinned here. No test
 * renders the builder, because the vitest environment has no DOM.
 */
import { describe, expect, it } from "vitest";

import {
  OVERVIEW_SECTIONS,
  PERIODS,
  REPORT_SECTIONS,
  configFor,
  newBuilderState,
  overviewState,
  overviewTableShown,
  periodKey,
  saveAsReportState,
  withSubject,
} from "./reportBuilder";

const SEVEN = ["finished", "throughput", "outlook", "load", "capacity", "stuck", "conflicts"];

describe("(c) overviewState", () => {
  const state = overviewState();

  it("holds the whole organization, no subject and the subtree", () => {
    expect(state.projectId).toBeNull();
    expect(state.subject).toBeNull();
    expect(state.includeSubtree).toBe(true);
    expect(state.template).toBeNull();
  });

  it("holds the Analytics routes' period: 12 weeks with the running week", () => {
    expect(state.weeks).toBe(12);
    expect(state.skipCurrentWeek).toBe(false);
    expect(periodKey(state)).toBe("last_12_weeks");
  });

  it("holds the seven sections, in SECTIONS order", () => {
    expect(state.sections).toEqual(SEVEN);
    expect([...OVERVIEW_SECTIONS]).toEqual(SEVEN);
    const order = REPORT_SECTIONS.map((s) => s.key);
    const at = state.sections.map((k) => order.indexOf(k));
    expect(at).toEqual([...at].sort((a, b) => a - b));
    // pulse, hygiene and rebalance start off. A reader turns them on.
    for (const off of ["pulse", "hygiene", "rebalance"]) expect(state.sections).not.toContain(off);
  });

  it("sends the config the server renders", () => {
    expect(configFor(state)).toEqual({
      weeks: 12,
      skip_current_week: false,
      include_subtree: true,
      sections: SEVEN,
    });
  });

  it("the period chip names it", () => {
    const hit = PERIODS.find((p) => p.key === "last_12_weeks");
    expect(hit).toEqual({
      key: "last_12_weeks",
      label: "The last 12 weeks",
      weeks: 12,
      skip_current_week: false,
    });
  });
});

describe("(d) saveAsReportState", () => {
  it("keeps the scope, the subject, the period, the subtree and the sections", () => {
    const changed = {
      ...withSubject(overviewState(), { kind: "team", slug: "hardware" }),
      projectId: "0f8fad5b-d9cb-469f-a165-70867728950e",
      includeSubtree: false,
      weeks: 4,
      skipCurrentWeek: true,
      sections: ["finished", "load", "pulse"],
    };
    const saved = saveAsReportState(changed);
    expect(saved.projectId).toBe(changed.projectId);
    expect(saved.subject).toEqual({ kind: "team", slug: "hardware" });
    expect(saved.weeks).toBe(4);
    expect(saved.skipCurrentWeek).toBe(true);
    expect(saved.includeSubtree).toBe(false);
    expect(saved.sections).toEqual(["finished", "load", "pulse"]);
  });

  it("has no template and an untouched name, so builderName names it", () => {
    const saved = saveAsReportState(overviewState());
    expect(saved.template).toBeNull();
    expect(saved.nameTouched).toBe(false);
    expect(saved.name).toBe(newBuilderState().name);
    expect(configFor(saved)).toEqual(configFor(overviewState()));
  });
});

describe("(e) overviewTableShown", () => {
  it("is true for Everyone with the subtree", () => {
    expect(overviewTableShown(overviewState())).toBe(true);
    expect(overviewTableShown({ subject: null, includeSubtree: true })).toBe(true);
  });

  it("is false with a subject", () => {
    expect(
      overviewTableShown({ subject: { kind: "person", email: "a@x.io" }, includeSubtree: true })
    ).toBe(false);
    expect(overviewTableShown({ subject: { kind: "team", slug: "hw" }, includeSubtree: true })).toBe(
      false
    );
  });

  it("is false without the subtree", () => {
    expect(overviewTableShown({ subject: null, includeSubtree: false })).toBe(false);
  });
});
