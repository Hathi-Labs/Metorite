/**
 * The plan card's markup at rest (owner, 2026-10-18): read first.
 *
 * The node suite cannot click, so this file holds what the card draws
 * before anybody touches it. `e2e/plan-card.spec.ts` holds the clicks: a row
 * that opens on a click and on Enter, the ticks, the bulk edits, the fold
 * and the submit. `app/projects/lib/planCardReadFirst.test.ts` holds the pure
 * decisions and the golden test of the payload.
 */
import { createHash } from "node:crypto";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TEMPLATE_REGISTRY } from "./genUITemplates";
import { RowEditor } from "./PlanCardBody";
import { planRowsFrom, planSubmit } from "@/app/projects/lib/planCard";

const TASKS = [
  { key: "t1", title: "Build the memory store", owner: "ayush@x.io", effort_mins: 240,
    start: "2026-10-22", due: "2026-10-24", phase: "Foundation" },
  { key: "t2", title: "Design the recall API", owner: "", effort_mins: 120,
    start: "2026-10-23", due: "2026-10-23", phase: "Foundation" },
  { key: "t3", title: "Define the schema", owner: "nithin@x.io", effort_mins: 360,
    start: "2026-10-22", due: "2026-10-23", phase: "Foundation", marks: ["short of hours"],
    fit: "Postgres", hours: "short 4 h" },
  { key: "t4", title: "Load test the retrieval path", owner: "nithin@x.io", effort_mins: 480,
    start: "2026-10-27", due: "2026-10-28", after: ["t1"], phase: "Rollout", marks: ["no skill match"] },
];

const card = (data: Record<string, unknown>, onAction?: (m: string) => void) =>
  renderToStaticMarkup(createElement(() => TEMPLATE_REGISTRY.planCard(data, { onAction })));
const plan = (tasks: unknown[] = TASKS) =>
  card({ title: "Plan · Memory", project: { name: "Memory", parent: "Metorite" }, tasks }, () => {});

describe("the plan card at rest", () => {
  it("draws each task as a line of text, with no input box (change 1)", () => {
    const html = plan();
    // The only inputs are the four ticks: no text, number or date field.
    const inputs = html.match(/<input[^>]*>/g) ?? [];
    expect(inputs).toHaveLength(4);
    for (const i of inputs) expect(i).toContain('type="checkbox"');
    expect(html).not.toContain('type="date"');
    expect(html).not.toContain("Effort (min)");
    expect(html).not.toContain(">Project name<");
    expect(html).not.toContain("data-plan-open");
  });

  it("gives each line a real button that opens it, and a labelled pencil", () => {
    const html = plan();
    expect(html.match(/data-plan-line="/g) ?? []).toHaveLength(4);
    expect(html).toContain('aria-label="Edit Build the memory store: Ayush, 4h, 22 Oct → 24 Oct"');
    expect(html).toContain('aria-label="Edit Build the memory store"');
    expect(html).toContain('aria-label="Create Build the memory store"');
  });

  it("draws the owner as a chip, the effort in h and d, and short dates (change 2)", () => {
    const html = plan();
    expect(html).toContain('<span class="truncate">Ayush</span>');
    expect(html).toContain('<span class="truncate">Unassigned</span>');
    expect(html).toContain(">6h<");
    expect(html).toContain(">1d<");
    expect(html).not.toContain(">360<");
    expect(html).toContain(">23 Oct<");
  });

  it("leads with the summary line, the name as its title (change 3)", () => {
    const html = plan();
    expect(html).toContain('aria-label="Project name: Memory. Rename"');
    expect(html).toContain("4 tasks · 2 owners · 22 Oct → 28 Oct · 2.5d");
    // The header is the short title of a card that waits.
    expect(html).toMatch(/font-weight:600;color:var\(--foreground\)">Plan<\/span>/);
  });

  it("draws the timeline strip, one bar per ticked task (change 4)", () => {
    const html = plan();
    expect(html).toContain('aria-label="Timeline: 4 tasks, 22 Oct to 28 Oct"');
    expect(html.match(/rounded-full bg-/g) ?? []).toHaveLength(4);
  });

  it("groups the rows under folding phases (change 5)", () => {
    const html = plan();
    expect(html).toContain("<span>Foundation</span>");
    expect(html).toContain("<span>Rollout</span>");
    expect(html.match(/aria-expanded="true"/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    // No phase on any row: one flat list, no fold.
    const flat = plan(TASKS.map((t) => ({ ...t, phase: "" })));
    expect(flat).not.toContain("<span>Foundation</span>");
  });

  it("shows a mark as a small badge, and the fit only when a row opens (change 6)", () => {
    const html = plan();
    expect(html).toContain(">Overloaded</span>");
    expect(html).toContain(">Skill gap</span>");
    expect(html).not.toContain("Fit:");
    expect(html).not.toContain("short 4 h");
  });

  it("offers Edit several (change 7)", () => {
    expect(plan()).toContain(">Edit several</button>");
  });

  it("ends with one footer: Create N tasks, and Cancel (change 8)", () => {
    const html = plan();
    expect(html).toContain(">Create 4 tasks</button>");
    expect(html).toContain(">Cancel</button>");
    expect(html).toContain("Next, one approval creates all 4 tasks.");
    expect(html).not.toContain("Review plan</button>");
  });

  it("draws in the chat card's frame, with a container for the narrow layout (change 9)", () => {
    const html = plan();
    expect(html.startsWith('<div data-plan-card="" class="@container min-w-0"')).toBe(true);
    expect(html).toContain("@[34rem]:flex-row");
  });

  it("names what a ticked row lacks, and turns the button off", () => {
    const html = plan([{ ...TASKS[0], title: "" }]);
    expect(html).toContain(">Needs a title</span>");
    expect(html).toMatch(/data-plan-submit="" disabled=""/);
  });

  it("cannot send with no chat to send to", () => {
    const html = card({ project: { name: "Memory" }, tasks: TASKS });
    expect(html).toMatch(/data-plan-submit="" disabled=""/);
  });
});

// ── Review of #1136 ─────────────────────────────────────────────────────────

describe("the open row and the answered card", () => {
  const scored = { ...TASKS[0], impact: 4, urgency: 3, effort: 2, priority: 24 };

  it("shows the priority score in the open row, never on the line at rest", () => {
    const [row] = planRowsFrom([scored]);
    const open = renderToStaticMarkup(createElement(RowEditor, {
      row, rows: [row], kept: [row], ticked: true, ownersList: "o",
      onTick: () => {}, onCell: () => {}, onClose: () => {},
    }));
    expect(open).toContain('title="Priority score, a sorting aid"');
    expect(open).toContain("Priority 24 · a sorting aid");
    expect(plan([scored])).not.toContain("Priority 24");
  });

  it("names a row on a circle and turns the button off", () => {
    const html = plan([
      { ...TASKS[0], after: ["t2"] }, { ...TASKS[1], after: ["t1"] },
    ]);
    expect(html.match(/>Waits in a circle<\/span>/g) ?? []).toHaveLength(2);
    expect(html).toMatch(/data-plan-submit="" disabled=""/);
  });

  const answeredCard = (answered: string) =>
    renderToStaticMarkup(createElement(() =>
      TEMPLATE_REGISTRY.planCard(
        { title: "Plan · Memory", project: { name: "Memory" }, tasks: TASKS },
        { onAction: () => {}, answered },
      )));

  it("draws a submitted card locked after a reload, with the rows it left out unticked", () => {
    const sent = planSubmit({ name: "Memory" }, "Memory", planRowsFrom(TASKS.slice(0, 3)));
    const html = answeredCard(`Review plan — ${JSON.stringify(sent)}`);
    expect(html).toContain('data-plan-done="sent"');
    expect(html).not.toContain("data-plan-submit");
    expect(html).not.toContain(">Edit several<");
    expect(html.match(/data-plan-off=""/g) ?? []).toHaveLength(1);
    for (const line of html.match(/<button data-plan-line[^>]*>/g) ?? []) expect(line).toContain('disabled=""');
  });

  it("draws a cancelled card locked after a reload", () => {
    const html = answeredCard("Cancel");
    expect(html).toContain('data-plan-done="cancelled"');
    expect(html).toContain("Nothing was created.");
  });
});

// ── Other forms draw as they did ────────────────────────────────────────────

/** A form card and an option picker. Their markup must not move with the
 *  plan card's redesign. The hashes were taken from `origin/main` before
 *  the change, and the change matched them byte for byte. */
export const NON_PLAN_SAMPLES: ReadonlyArray<[string, Record<string, unknown>]> = [
  ["formCard", {
    title: "Edit task",
    description: "Change any field, then save.",
    submitLabel: "Save",
    fields: [
      { name: "title", label: "Title", type: "text", value: "Weld the frame", required: true },
      { name: "estimate", label: "Estimate", type: "number", value: 90, unit: "min" },
      { name: "status", label: "Status", type: "select", options: ["To do", "Done"], value: "To do" },
      { name: "due", label: "Due", type: "date", value: "2026-10-22" },
      { name: "important", label: "Important", type: "toggle", value: true },
      { name: "notes", label: "Notes", type: "textarea", placeholder: "Anything else" },
    ],
  }],
  ["optionPicker", {
    title: "How should we split the plan?",
    options: [
      { id: "a", label: "By team", description: "One project per team.", recommended: true },
      { id: "b", label: "By month" },
    ],
  }],
];

const HASHES: Record<string, string> = {
  formCard: "d4beb33a4b8f6cf694d87069f7d2096e8518fec2e7b33062eb73f861063e6884",
  optionPicker: "6aa5a27afd7fe91f4573b18db35dac31879d9b42fa87b021c274409de347f2b3",
};

describe("a form that is not a plan", () => {
  for (const [name, data] of NON_PLAN_SAMPLES) {
    it(`${name} renders unchanged`, () => {
      const html = renderToStaticMarkup(createElement(() => TEMPLATE_REGISTRY[name](data, { onAction: () => {} })));
      expect(createHash("sha256").update(html).digest("hex")).toBe(HASHES[name]);
      expect(html).not.toContain("data-plan");
    });
  }
});
