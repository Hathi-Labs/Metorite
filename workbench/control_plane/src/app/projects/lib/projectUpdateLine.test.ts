/**
 * WS-39 LP-1a — the line of the newest project update (§14.2a).
 *
 * The server half is `tests/unit/test_pm_project_updates_r8.py`. This file
 * holds what the Overview draws from `summary.latest_update`, the three
 * health hues of the one colour vocabulary, and the open state of the line.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  PROJECT_HEALTHS,
  accentForHue,
  projectHealth,
  projectHealthChip,
} from "@/lib/statusAccent";

import { ProjectUpdateLine } from "../components/ProjectUpdateLine";
import type { ProjectUpdate } from "./api";
import { UPDATE_LINE_MAX, firstLine, projectUpdateLine, updateDay } from "./projectUpdateLine";

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
  it("draws the update: the health, the first line, the author and the day", () => {
    const line = projectUpdateLine(update(), "UTC");
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

  it("draws nothing with no update, or a health it does not know", () => {
    expect(projectUpdateLine(null)).toBeNull();
    expect(projectUpdateLine(undefined)).toBeNull();
    expect(
      projectUpdateLine(update({ health: "sideways" as ProjectUpdate["health"] }))
    ).toBeNull();
  });

  it("skips a heading line and shows the first line of real text", () => {
    expect(firstLine("## Week 45\nThe tiles slipped a week.\nMore")).toBe(
      "The tiles slipped a week."
    );
    expect(firstLine("\n\n# Summary #\n\n- shipped the frame")).toBe("shipped the frame");
    expect(firstLine("> quoted note")).toBe("quoted note");
    expect(firstLine("1. first item")).toBe("first item");
    expect(firstLine("   ")).toBe("");
  });

  it("shows the heading text with no marks when the body is only headings", () => {
    expect(firstLine("## Week 45")).toBe("Week 45");
    expect(firstLine("### Week 45 ###\n#### Later")).toBe("Week 45");
  });

  it("cuts a long first line at a word, with an ellipsis", () => {
    const long = "word ".repeat(60).trim();
    const shown = firstLine(long);
    expect(shown.endsWith("…")).toBe(true);
    expect(shown.length).toBeLessThanOrEqual(UPDATE_LINE_MAX + 1);
    expect(shown).not.toMatch(/\s…$/);
  });

  it("writes the day in the reader's zone, not the UTC day", () => {
    // 20:00 UTC on 8 Nov is already 9 Nov in India.
    expect(updateDay("2026-11-08T20:00:00Z", "Asia/Kolkata")).toBe("9 Nov 2026");
    expect(updateDay("2026-11-08T20:00:00Z", "UTC")).toBe("8 Nov 2026");
    expect(updateDay("2026-11-09T02:00:00Z", "America/New_York")).toBe("8 Nov 2026");
    expect(updateDay(null)).toBe("—");
    expect(updateDay("not a date")).toBe("—");
  });
});

describe("the line opens the whole update in place", () => {
  const full = "## Week 45\nThe tiles slipped a week.\nWe fit them on Friday.";

  it("draws the text as a closed button that names what it opens", () => {
    const html = renderToStaticMarkup(
      createElement(ProjectUpdateLine, { update: update({ body: full }) })
    );
    expect(html).toMatch(/<button type="button"[^>]*aria-expanded="false"/);
    expect(html).toMatch(/aria-controls="[^"]+"/);
    expect(html).toContain("The tiles slipped a week.");
    expect(html).toContain("Show the whole update");
    expect(html).not.toContain("We fit them on Friday.");
  });

  it("shows the whole text below the line when open", () => {
    const html = renderToStaticMarkup(
      createElement(ProjectUpdateLine, { update: update({ body: full }), defaultOpen: true })
    );
    expect(html).toMatch(/aria-expanded="true"/);
    const panel = /<div id="([^"]+)"[^>]*>([\s\S]*?)<\/div>/.exec(html);
    expect(panel?.[2]).toContain("We fit them on Friday.");
    expect(html).toContain(`aria-controls="${panel?.[1]}"`);
    expect(html).toContain("Hide the whole update");
  });

  // Coordinator review: the open text showed "## Week 45" and "- The counter
  // top…" as marks. It draws with the renderer of a task comment now.
  // Mutation: draw `line.full` as plain text -> red.
  it("draws a heading and a list as elements, not as Markdown marks", () => {
    const body = [
      "## Week 45",
      "The tiles slipped a week.",
      "",
      "- The counter top is in.",
      "- The lights arrive on Wednesday.",
      "",
      "<script>alert(1)</script>",
    ].join("\n");
    const html = renderToStaticMarkup(
      createElement(ProjectUpdateLine, { update: update({ body }), defaultOpen: true })
    );
    const at = html.indexOf('id="');
    const panel = html.slice(at);
    expect(panel).toMatch(/<h2[^>]*>Week 45<\/h2>/);
    expect(panel).toMatch(/<ul[^>]*>[\s\S]*<li[^>]*>The counter top is in\.<\/li>/);
    expect(panel).toMatch(/<li[^>]*>The lights arrive on Wednesday\.<\/li>/);
    expect(panel).not.toContain("## Week 45");
    expect(panel).not.toContain("- The counter top");
    // The same sanitizing as a comment: HTML in the text never becomes an element.
    expect(panel).not.toContain("<script");
  });
});
