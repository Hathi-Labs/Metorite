/**
 * WS-39 S6e — one task panel composition (my_tasks_cutover.md §4.8 point 3).
 *
 * A member who opens a task in Projects and the same task in My Tasks must
 * meet the same fields in the same order. The way that stays true is that
 * the field blocks exist ONCE — `app/projects/components/TaskBody.tsx` — and
 * both panels host it. This fence reads the source and refuses a second
 * field list in `ItemDetail.tsx`:
 *
 *   1. it imports the body from `app/projects/components/`;
 *   2. it defines none of the shared blocks — no `AssigneePicker`, no
 *      `CustomFieldValues`, no `TagPicker`, no comments list, no timeline;
 *   3. it no longer reads the ClickUp-era `/items/{id}/detail`
 *      (`apiItemDetail`), whose comments/attachments/subtasks were the
 *      second composition.
 *
 * And the mirror: `TaskPanel.tsx` hosts the same body and defines no block
 * either, so the two panels cannot drift apart one file at a time.
 *
 * Source, not render: the failure this defends is a block re-drawn in one
 * host, which an example render of the other host cannot see.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const APP = path.resolve(__dirname, "..", "..");
const read = (rel: string) => fs.readFileSync(path.join(APP, rel), "utf8");

/** Strip comments, so a comment naming a block cannot trip the fence. */
function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:\\])\/\/[^\n]*/g, "$1");
}

const HOSTS = {
  "tasks/components/ItemDetail.tsx": code(read("tasks/components/ItemDetail.tsx")),
  "projects/components/TaskPanel.tsx": code(read("projects/components/TaskPanel.tsx")),
};
const BODY = code(read("projects/components/TaskBody.tsx"));

/** The blocks that belong to the body, as the names a second copy would need. */
const BLOCKS = [
  "AssigneePicker",
  "AssigneeChips",
  "CustomFieldValues",
  "TagPicker",
  "RepeatEditor",
  "RelationsBlock",
  "AttachmentViewer",
  "threadComments",
  "describeActivity",
  "rollUp",
];

describe("one task panel composition (S6e)", () => {
  it("ItemDetail hosts the shared body from app/projects/components/", () => {
    const src = HOSTS["tasks/components/ItemDetail.tsx"];
    expect(src).toMatch(/from "@\/app\/projects\/components\/TaskBody"/);
    expect(src).toContain("<TaskBody");
  });

  it("TaskPanel hosts the same body", () => {
    const src = HOSTS["projects/components/TaskPanel.tsx"];
    expect(src).toMatch(/from "\.\/TaskBody"/);
    expect(src).toContain("<TaskBody");
  });

  it.each(Object.entries(HOSTS))("%s defines no field block of its own", (_file, src) => {
    for (const name of BLOCKS) {
      // Neither a definition nor a direct import: a host that imported
      // `TagPicker` would be drawing tags beside the body's tags.
      expect(src, `defines ${name}`).not.toMatch(new RegExp(`function ${name}\\b`));
      expect(src, `imports ${name}`).not.toMatch(new RegExp(`import[^;]*\\b${name}\\b`));
      expect(src, `draws <${name}`).not.toContain(`<${name}`);
    }
    // A comments list or a timeline of its own — the two names the
    // ClickUp-era composition used, and the routes only the body reads.
    expect(src).not.toMatch(/function CommentRow\b/);
    expect(src).not.toMatch(/function ProviderDetailSections\b/);
    expect(src).not.toContain("apiItemDetail");
    expect(src).not.toContain("/timeline");
    expect(src).not.toContain("attachmentsApi");
  });

  it("the body is where every block lives", () => {
    for (const name of BLOCKS) {
      expect(BODY, name).toMatch(new RegExp(`\\b${name}\\b`));
    }
    expect(BODY).toContain('label="Status"');
    expect(BODY).toContain('label="Priority"');
    expect(BODY).toContain('label="Assignees"');
    expect(BODY).toContain('label="Due"');
    expect(BODY).toContain('label="Discussion"');
    expect(BODY).toContain('label="Files"');
  });

  it("the move dialog draws the body's assignee chips, not a copy", () => {
    const dialog = code(read("projects/components/MoveTasksDialog.tsx"));
    expect(dialog).toContain("<AssigneeChips");
    // The copied block's own tell: classifying an address to pick a tone.
    expect(dialog).not.toMatch(/classify\(who\)/);
  });

  it("My Tasks' strip keeps the member's own facts above the body", () => {
    const src = HOSTS["tasks/components/ItemDetail.tsx"];
    // D76: Estimate left this list — it is the task's ONE estimate, and the
    // body draws it. Context, energy and defer are how I hold the work.
    for (const label of ['label="Context"', 'label="Energy"', 'label="Defer until"']) {
      expect(src, label).toContain(label);
    }
    expect(src).toContain("above={strip}");
  });
});

// ── No two labels alike in one host (S6e repair) ────────────────────────────
//
// D53.8: `GtdItem.important` (the member's Eisenhower pair) is NOT
// `pm_tasks.importance` (the task's shared Priority integer). The first S6e
// draw put "Priority" on both — the strip's matrix section and the body's
// cell — with two different values a hand's width apart. Every label a
// member reads in the lens host has to be unique, case-insensitive, across
// the strip and the body together.

/** The labels the lens host draws: the strip's, minus its lens-off blocks. */
function lensHostLabels(): string[] {
  let strip = HOSTS["tasks/components/ItemDetail.tsx"];
  // The legacy-only blocks (`{!lens && (<> … </>)}`) never draw under the
  // lens; their Due, Stage and Assignee cells are the body's job there.
  strip = strip.replace(/\{!lens && \(<>[\s\S]*?<\/>\)\}/g, "");
  const found: string[] = [];
  for (const m of strip.matchAll(/<MetaEdit label="([^"]+)"/g)) found.push(m[1]);
  for (const m of strip.matchAll(/<SectionLabel[^>]*>\s*([A-Za-z][^<{]*?)\s*<\/SectionLabel>/g)) {
    found.push(m[1]);
  }
  // The uppercase span labels the strip draws by hand (the matrix card, the
  // project cell): a `text-[10px] font-semibold uppercase` span's text.
  for (const m of strip.matchAll(
    /<(?:span|div) className="text-\[10px\] font-semibold uppercase[^"]*">\s*([A-Za-z][^<{]*?)\s*<\//g,
  )) {
    found.push(m[1]);
  }
  // The strip's section heading under the lens.
  found.push("My planning");
  for (const m of BODY.matchAll(/(?:<FieldCell|<CollapsibleSection)[^>]*?\blabel="([^"]+)"/g)) {
    found.push(m[1]);
  }
  return found.map((l) => l.trim()).filter(Boolean);
}

describe("no two labels alike in the lens host (D53.8)", () => {
  const labels = lensHostLabels();

  it("reads both halves", () => {
    expect(labels).toContain("Focus matrix");
    expect(labels).toContain("Priority");
    expect(labels).toContain("Context");
    expect(labels).toContain("Due");
  });

  it("refuses two section or cell labels that are equal, case-insensitive", () => {
    const seen = new Map<string, string>();
    const clashes: string[] = [];
    for (const label of labels) {
      const key = label.toLowerCase();
      const prior = seen.get(key);
      if (prior !== undefined) clashes.push(`${prior} / ${label}`);
      else seen.set(key, label);
    }
    expect(
      clashes,
      "Two controls with one name and two values on one panel. The body's " +
        "'Priority' is pm_tasks.importance; the strip's matrix is 'Focus matrix'.",
    ).toEqual([]);
  });

  it("draws every WORK fact in the body and none of them in the strip (D76)", () => {
    // The owner directive: one set of fields. Each of these is a fact about
    // the task, so it has one control, in the body both apps host. A label
    // in both halves is two editors for one fact, a hand's width apart.
    const strip = HOSTS["tasks/components/ItemDetail.tsx"].replace(
      /\{!lens && \(<>[\s\S]*?<\/>\)\}/g,
      "",
    );
    const stripLabels = new Set(
      [
        ...strip.matchAll(/<MetaEdit label="([^"]+)"/g),
        ...strip.matchAll(/<SectionLabel[^>]*>\s*([A-Za-z][^<{]*?)\s*<\/SectionLabel>/g),
      ].map((m) => m[1].trim().toLowerCase()),
    );
    for (const label of ["Priority", "Due", "Start", "Estimate", "Description", "Watch", "Time spent"]) {
      expect(BODY, `the body draws ${label}`).toMatch(
        new RegExp(`(?:<FieldCell|<CollapsibleSection)[^>]*?\\blabel="${label}"`),
      );
      expect(stripLabels.has(label.toLowerCase()), `the strip draws ${label}`).toBe(false);
    }
    // The strip's Notes editor was the description's only writer. Under the
    // lens it is gone; the body's Description is the one editor.
    expect(stripLabels.has("notes")).toBe(false);
    // The Projects header lost its watch toggle to the body.
    expect(HOSTS["projects/components/TaskPanel.tsx"]).not.toContain("watchersApi");
  });

  it("never calls the member's matrix 'Priority'", () => {
    const strip = HOSTS["tasks/components/ItemDetail.tsx"].replace(
      /\{!lens && \(<>[\s\S]*?<\/>\)\}/g,
      "",
    );
    expect(strip).not.toMatch(/uppercase[^"]*">\s*Priority\s*</);
  });
});
