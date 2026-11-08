/**
 * WS-39 LP-1a — the line of the newest project update (§14.2a).
 *
 * The server half is `tests/unit/test_pm_project_updates_r8.py`. This file
 * holds what the Overview draws from its answer, and the three health hues of
 * the one colour vocabulary.
 */
import { describe, expect, it } from "vitest";

import {
  PROJECT_HEALTHS,
  accentForHue,
  projectHealth,
  projectHealthChip,
} from "@/lib/statusAccent";

import type { ProjectUpdate, ProjectUpdatesPage } from "./api";
import { UPDATE_LINE_MAX, firstLine, projectUpdateLine } from "./projectUpdateLine";

function update(over: Partial<ProjectUpdate> = {}): ProjectUpdate {
  return {
    id: "u1",
    project_id: "p1",
    author: "lead@acme.test",
    health: "at_risk",
    body: "The vendor slipped a week.\nWe ship on Friday instead.",
    posted_at: "2026-11-08T10:30:00+00:00",
    drafted_by_ai: false,
    via: null,
    ...over,
  };
}

function page(rows: ProjectUpdate[]): ProjectUpdatesPage {
  return { rows, next_before: null };
}

describe("the health of a project update", () => {
  it("maps the three values onto existing hues, with words and a glyph", () => {
    expect(PROJECT_HEALTHS.on_track).toMatchObject({ label: "On track", hue: "green" });
    expect(PROJECT_HEALTHS.at_risk).toMatchObject({ label: "At risk", hue: "amber" });
    expect(PROJECT_HEALTHS.off_track).toMatchObject({ label: "Off track", hue: "red" });
    for (const visual of Object.values(PROJECT_HEALTHS)) {
      expect(visual.icon).toMatch(/^[A-Z][A-Za-z]+$/);
    }
    expect(projectHealthChip("on_track")).toBe(accentForHue("green").chip);
    expect(projectHealthChip("at_risk")).toBe(accentForHue("amber").chip);
    expect(projectHealthChip("off_track")).toBe(accentForHue("red").chip);
  });

  it("is a closed lookup: an unknown value or a prototype key is no health", () => {
    expect(projectHealth("great")).toBeNull();
    expect(projectHealth("constructor")).toBeNull();
    expect(projectHealth("__proto__")).toBeNull();
    expect(projectHealth(null)).toBeNull();
    expect(projectHealthChip("great")).toBe(accentForHue("gray").chip);
  });
});

describe("the Overview line", () => {
  it("draws the newest update: the health, the first line, the author and the date", () => {
    const line = projectUpdateLine(page([update(), update({ id: "u0", health: "on_track" })]));
    expect(line).toEqual({
      health: "at_risk",
      label: "At risk",
      icon: PROJECT_HEALTHS.at_risk.icon,
      text: "The vendor slipped a week.",
      full: "The vendor slipped a week.\nWe ship on Friday instead.",
      author: "lead@acme.test",
      date: "8 Nov 2026",
    });
  });

  it("draws nothing with no update, no answer, or a health it does not know", () => {
    expect(projectUpdateLine(page([]))).toBeNull();
    expect(projectUpdateLine(null)).toBeNull();
    expect(projectUpdateLine(undefined)).toBeNull();
    expect(
      projectUpdateLine(page([update({ health: "sideways" as ProjectUpdate["health"] })]))
    ).toBeNull();
  });

  it("drops a Markdown mark at the start and skips blank lines", () => {
    expect(firstLine("\n\n## Summary of the week\nmore")).toBe("Summary of the week");
    expect(firstLine("- shipped the frame")).toBe("shipped the frame");
    expect(firstLine("   ")).toBe("");
  });

  it("cuts a long first line at a word, with an ellipsis", () => {
    const long = "word ".repeat(60).trim();
    const shown = firstLine(long);
    expect(shown.endsWith("…")).toBe(true);
    expect(shown.length).toBeLessThanOrEqual(UPDATE_LINE_MAX + 1);
    expect(shown).not.toMatch(/\s…$/);
  });
});
