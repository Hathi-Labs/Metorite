// HDR-1 (`one_projects_app.md` §9e.6) — the summary line of a description.
// The two long descriptions are the real briefs of the board, read through the
// MCP on 2026-11-28. Each is the start of the stored text.
import { describe, expect, it } from "vitest";

import { projectSummary, SUMMARY_MAX } from "./projectSummary";

const METORITE_APPLICATION = `## Start here: the project map (2026-11-11, by the gratification ladder)

Metorite is one platform with one task store. Each app below is a project with its own Ladder and Delegation brief in its description. **We plan by the gratification ladder (D211, \`project-docs/specs/orchestration_playbook.md\` §6d):** each rung ends in a demo task that the owner sees and uses on production. Take rung 0 first, then the lowest open rung. A slice that is already in progress finishes first. All owner slots are unassigned. Names come later.

## The order across apps (waves)
- **Wave A, now:** the rung 0 work of every project. AI Studio rung 1 (demo 2892) and MCP rung 1 (demo 2895). LAD-1 (task 2888), so that every connected agent plans in rungs.
- **Wave B:** AI Studio rung 2 (2893) and MCP rung 2 (2896).

## The map
| Wave | Project | What it is | Rung 1 demo | Rung 2 demo | Depends on (projects) |
|---|---|---|---|---|---|
| A | AI Studio | Company-built skills, workflows, apps | 2892 | 2893 (rung 3: 2894) | Platform Core, Platform & CI/CD, MCP, CRM |`;

const PROJECTS_TASKS_APP = `## Brief (replan of 2026-11-09)

**What this app is.** The Projects and Tasks app: spaces, projects, tasks, the board canvases, planning (cycles, epics, Goals), templates, and the same acts in chat and over the MCP.

**Stage.** Milestones M1 to M2. M2 has no date (D190.2). **Owner.** Not named yet.

**Specs.** \`project_management_app.md\` (sections 13 and 14), \`one_projects_app.md\`, \`project_planning.md\`, \`project_alignment.md\`, \`navigation_shell.md\` (the team scope).

## Epics, in the order to read them
Read the plan of an epic with \`get_plan\` on its task.

| Epic | Task | What the critical path is |
|---|---|---|
| Cycles, epic page and Goals (E3) | 2476 | WM-5 (984) then PJP-6c (2508) |`;

const PROJECTS_TASKS_LINE =
  "The Projects and Tasks app: spaces, projects, tasks, the board canvases, planning (cycles, epics, Goals), templates, and the same acts in chat and over the MCP.";

describe("the real descriptions of the board", () => {
  it("Metorite Application gives its first sentence", () => {
    expect(projectSummary(METORITE_APPLICATION)).toEqual({
      line: "Metorite is one platform with one task store.",
      more: true,
    });
  });

  it("Projects/Tasks App drops the bold label and keeps the whole sentence", () => {
    const got = projectSummary(PROJECTS_TASKS_APP);
    expect(got?.line).toBe(PROJECTS_TASKS_LINE);
    expect(got?.more).toBe(true);
  });

  it("the second line is at the limit, so it is not cut", () => {
    expect(PROJECTS_TASKS_LINE.length).toBeLessThanOrEqual(SUMMARY_MAX);
    expect(PROJECTS_TASKS_LINE.endsWith("…")).toBe(false);
  });
});

describe("what gives no line", () => {
  it("an empty description gives nothing, and so does a blank one", () => {
    expect(projectSummary("")).toBeNull();
    expect(projectSummary("  \n \n")).toBeNull();
    expect(projectSummary(null)).toBeNull();
    expect(projectSummary(undefined)).toBeNull();
  });

  it("a description that is only a heading gives that heading as the line, and no More", () => {
    expect(projectSummary("## Start here")).toEqual({ line: "Start here", more: false });
  });

  it("a heading with more after it gives the heading, and offers More", () => {
    expect(projectSummary(["# **The map** (2026)", "", "- one", "- two"].join("\n"))).toEqual({ line: "The map (2026)", more: true });
  });

  it("a list, a table, a quote and a code block give no line", () => {
    const text = "- one\n- two\n\n| a | b |\n|---|---|\n\n> quoted\n\n```\ncode here. More code.\n```";
    expect(projectSummary(text)).toEqual({ line: "", more: true });
  });
});

describe("the cut", () => {
  it("a long first sentence stops at a word end with an ellipsis, in 160 characters", () => {
    const words = Array.from({ length: 60 }, (_, i) => `word${i}`).join(" ");
    const got = projectSummary(`${words}.`);
    expect(got?.line.endsWith("…")).toBe(true);
    expect(got?.line.length).toBeLessThanOrEqual(SUMMARY_MAX);
    // It cuts at a word end: the text before the mark is whole words.
    const head = got!.line.slice(0, -1);
    expect(words.startsWith(head)).toBe(true);
    expect(words[head.length]).toBe(" ");
    expect(got?.more).toBe(true);
  });

  it("a short first sentence takes the next one", () => {
    const got = projectSummary("Plan the year. Every team adds its goals here. A third sentence stays out.");
    expect(got).toEqual({ line: "Plan the year. Every team adds its goals here.", more: true });
  });

  it("a description that is one sentence of its own has no More", () => {
    expect(projectSummary("The company wiki.")).toEqual({ line: "The company wiki.", more: false });
  });
});

describe("the marks", () => {
  it("removes bold, italic, code and links", () => {
    const got = projectSummary("The **core** app with `code`, an _idea_ and a [link](https://example.com) inside it.");
    expect(got?.line).toBe("The core app with code, an idea and a link inside it.");
  });

  it("keeps a bold word that is part of the sentence", () => {
    expect(projectSummary("**Metorite** runs the company, and it is long enough.")?.line).toBe(
      "Metorite runs the company, and it is long enough.",
    );
  });

  it("takes the first paragraph after a heading and a list", () => {
    const got = projectSummary("# Title\n\n- a\n- b\n\nThe real summary of this project.\n\nAnother paragraph.");
    expect(got).toEqual({ line: "The real summary of this project.", more: true });
  });
});
