/**
 * S6g — one inbox (my_tasks_cutover.md §5 S6g).
 *
 * The fences for the unified list: what it holds, that the badge and the
 * header count agree, the empty state, the source filter's counts, the order,
 * the actions per kind ("Not mine" never deletes), the origin marker in place
 * of `SourceBadge`, and that the Inbox binds no letter keys.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { submitCaptureBox } from "./quickAdd";
import { viewCounts } from "./taskStore";
import {
  assignedByLabel,
  inboxCount,
  inboxKind,
  inboxRowActions,
  inboxRows,
  orderInbox,
} from "./inbox";
import type { MyTask } from "./types";

const ROOT = "root-1";
const AREA = "area-1";
const scope = { personalRootId: ROOT, areaIds: [AREA] };

const task = (id: string, over: Partial<MyTask> = {}): MyTask => ({
  id,
  source: "LOCAL",
  title: `Task ${id}`,
  disposition: "INBOX",
  isMine: true,
  createdAt: "2026-09-20T09:00:00Z",
  updatedAt: "2026-09-20T09:00:00Z",
  projectId: ROOT,
  ...over,
});

const read = (rel: string) =>
  readFileSync(resolve(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

// A capture, a capture in an Area, an assigned untriaged board row (NEXT by
// derivation), a capture filed to a company board with its INBOX overlay, a
// tickled capture, an archived capture, and a clarified capture.
const ITEMS: MyTask[] = [
  task("c1", { createdAt: "2026-09-21T09:00:00Z" }),
  task("c2", { projectId: AREA, createdAt: "2026-09-19T09:00:00Z" }),
  task("b1", { projectId: "board-1", disposition: "NEXT", createdAt: "2026-09-18T09:00:00Z" }),
  task("b2", { projectId: "board-2", createdAt: "2026-09-22T09:00:00Z" }),
  task("t1", { deferUntil: "2999-01-01T00:00:00Z" }),
  task("a1", { archivedAt: "2026-09-20T10:00:00Z" }),
  task("n1", { disposition: "NEXT" }),
];

describe("the inbox holds captures only (D170, INB-1)", () => {
  it("holds the INBOX rows, never a tickled, archived or clarified row", () => {
    const ids = inboxRows(ITEMS).map((i) => i.id).sort();
    expect(ids).toEqual(["b2", "c1", "c2"]);
  });

  it("a captured task filed to a company board stays in the inbox", () => {
    expect(inboxRows(ITEMS).map((i) => i.id)).toContain("b2");
  });

  it("an assigned, untriaged company task is not in the inbox", () => {
    expect(inboxRows(ITEMS).map((i) => i.id)).not.toContain("b1");
    // The module takes no `fromProjectIds`: the set cannot widen the list.
    expect(read("lib/inbox.ts")).not.toMatch(/fromProjectIds\.has/);
  });

  it("the count is one rule, read by the badge and by the header", () => {
    expect(inboxCount(ITEMS)).toBe(3);
    expect(inboxCount(ITEMS)).toBe(inboxRows(ITEMS).length);
    // The sidebar badge reads `inboxCount`, and the header says the length
    // of `inboxRows` before any filter. Neither may count by its own rule.
    const sidebar = read("components/ListsSidebar.tsx");
    expect(sidebar).toMatch(/scoped\.inbox = inboxCount\(items\)/);
    const view = read("components/InboxView.tsx");
    expect(view).toMatch(/const allRows = useMemo\(\(\) => inboxRows\(items\), \[items\]\)/);
    expect(view).toMatch(/\{allRows\.length\} to process/);
    // The view-count rule of the store agrees: INBOX, not tickled, not archived.
    expect(viewCounts(ITEMS).inbox).toBe(inboxCount(ITEMS));
  });

  it("kind is `isPersonalTask`: my root and my Areas are personal, a board is a board", () => {
    expect(inboxKind(task("x"), scope)).toBe("personal");
    expect(inboxKind(task("x", { projectId: AREA }), scope)).toBe("personal");
    expect(inboxKind(task("x", { projectId: undefined }), scope)).toBe("personal");
    expect(inboxKind(task("x", { projectId: "board-1" }), scope)).toBe("board");
  });

  it("draws the member's sort, newest or oldest capture first", () => {
    const rows = inboxRows(ITEMS);
    expect(orderInbox(rows, "newest").map((i) => i.id)).toEqual(["b2", "c1", "c2"]);
    expect(orderInbox(rows, "oldest").map((i) => i.id)).toEqual(["c2", "c1", "b2"]);
  });

  it("Inbox zero only when the list is empty, and the controls whenever it has rows", () => {
    const view = read("components/InboxView.tsx");
    expect(view).toMatch(/const hasRows = allRows\.length > 0;/);
    // Continuity P3: one shared box draws both empty states, and `empty` is
    // what picks "Inbox zero" over "the filter hid everything".
    expect(view).toMatch(/\) : !hasRows \|\| visible\.length === 0 \? \(/);
    expect(view).toMatch(/<InboxEmptyState empty=\{!hasRows\}/);
    expect(view).toMatch(/\{\(hasRows \|\| tickler\.length > 0\) && \(/);
    expect(view).not.toMatch(/FromProjectsGroup/);
  });

  it("the From Projects pill and the source filter are gone", () => {
    const view = read("components/InboxView.tsx");
    expect(view).not.toMatch(/SOURCE_PILLS|sourcePills|From Projects|fromProjectIds/);
    expect(read("lib/inbox.ts")).not.toMatch(/From Projects/);
  });
});

describe("the origin marker (S6g)", () => {
  it("no SourceBadge on an inbox row", () => {
    for (const rel of ["components/InboxCard.tsx", "components/InboxTable.tsx"]) {
      const src = read(rel);
      expect(src, rel).not.toMatch(/SourceBadge/);
      expect(src, rel).toMatch(/<InboxOrigin item=\{item\} kind=\{kind\} \/>/);
    }
  });

  it("the Clarify header wears the same marker, not SourceBadge", () => {
    const src = read("components/ClarifyPanel.tsx");
    expect(src).not.toMatch(/SourceBadge/);
    expect(src).toMatch(/<InboxOrigin item=\{item\} kind=\{personalTask \? "personal" : "board"\} \/>/);
  });

  it("personal is a lock badge, a board row names its project and who assigned it", () => {
    const src = read("components/InboxOrigin.tsx");
    expect(src).toMatch(/<Badge tone="neutral" icon="Lock"[^>]*>\s*Personal\s*<\/Badge>/);
    expect(src).toMatch(/<ProjectLabel/);
    expect(src).toMatch(/assignedByLabel\(item\.assignedBy\)/);
    // D78's level badge, with the card face's rule: Low draws nothing.
    expect(src).toMatch(/<PriorityBadge item=\{item\} hideLowPriority \/>/);
    expect(assignedByLabel("priya@fracktal.in")).toBe("from priya");
    expect(assignedByLabel("")).toBeNull();
  });

  it("the table's Where column replaces Source", () => {
    const src = read("components/InboxTable.tsx");
    expect(src).toMatch(/>Where<\/th>/);
    expect(src).not.toMatch(/>Source<\/th>/);
  });
});

describe("the actions per kind (S6g)", () => {
  const spies = () => ({
    move: vi.fn(),
    notMine: vi.fn(),
    remove: vi.fn(),
    openBoard: vi.fn(),
  });

  it("a capture offers Move to project and Delete", () => {
    const s = spies();
    const ids = inboxRowActions({ kind: "personal", canPromote: true, ...s }).map((a) => a.id);
    expect(ids).toEqual(["move", "remove"]);
    // An archived capture is not promotable (`promoteAllowed`).
    expect(
      inboxRowActions({ kind: "personal", canPromote: false, ...s }).map((a) => a.id),
    ).toEqual(["remove"]);
  });

  it("a board row offers Remove from my lists and Open on board, and no Delete", () => {
    const s = spies();
    const actions = inboxRowActions({ kind: "board", canPromote: true, ...s });
    expect(actions.map((a) => a.id)).toEqual(["notMine", "openBoard"]);
    // One name per act (2026-09-24): the label is REMOVE_LABEL, the name
    // every other My Tasks surface gives this act. It read "Not mine".
    expect(actions.map((a) => a.label)).toEqual(["Remove from my lists", "Open on board"]);
    actions.find((a) => a.id === "notMine")!.run();
    expect(s.notMine).toHaveBeenCalledTimes(1);
    expect(s.remove).not.toHaveBeenCalled();
  });

  it('"Not mine" writes TRASH on my overlay, never the delete path', () => {
    // The delete path PURGES when its undo window closes (`dismissUndo` →
    // `apiPurgeItem` → DELETE /projects/tasks/{id}). On a board task that is
    // the team's task. Every "Not mine" is the one-tap dispose instead.
    for (const rel of ["components/InboxCard.tsx", "components/InboxTable.tsx"]) {
      expect(read(rel), rel).toMatch(/notMine: \(\) => quickDispose\(item\.id, "TRASH"\)/);
    }
    // The `t` key's own "Not mine" went with the letter keys (2026-10-13).
    // The bulk bar is the other way to remove a board row.
    const view = read("components/InboxView.tsx");
    expect(view).toMatch(/if \(selectedBoard\.length\) bulkDispose\(selectedBoard, "TRASH"\);/);
    // …and the dispose path writes the overlay only.
    const lens = readFileSync(resolve(__dirname, "lens.ts"), "utf-8");
    expect(lens).toMatch(/action: "personal",\s*personal: \{ disposition \}/);
  });

  it("the kind's actions are always visible, never hover-gated", () => {
    const card = read("components/InboxCard.tsx");
    const block = card.slice(card.indexOf("{actions.map((a) => ("));
    expect(block.slice(0, 600)).not.toMatch(/reveal-on-hover/);
  });
});

describe("the keyboard (S6g)", () => {
  const view = read("components/InboxView.tsx");

  it("answers the shared cursor and Escape, and no letter key (owner, 2026-10-13)", () => {
    // The triage keys `e x t s r 2 m o` went with the shortcuts sheet. Each
    // is a button on the row. The repo-wide fence is `noLetterKeys.test.ts`.
    expect(view).toMatch(/const moved = stepCursor\(/);
    expect(view).toMatch(/if \(e\.key === "Escape"\) \{\s*setSelection\(NO_SELECTION\);/);
    expect(view).not.toMatch(/case "[a-z0-9]":/);
  });

  it("the walk covers the captures: the keyboard reads `visible`, built from the one list", () => {
    expect(view).toMatch(/return orderInbox\(filtered, sortOrder\);/);
    expect(view).toMatch(/const activeInbox = allRows;/);
  });
});

describe("the capture chip (S6g repair P1-b)", () => {
  const PRINTER = { id: "p1", name: "Printer v3", kind: "project" as const };

  it("a chip pick is for one capture: submit sends it, then puts the chip back on Inbox", () => {
    const captureLine = vi.fn();
    const next = submitCaptureBox(
      { value: "Fix the jam", dest: PRINTER, attachments: [], chipOpen: true },
      captureLine,
      [PRINTER],
    );
    expect(captureLine).toHaveBeenCalledWith("Fix the jam", {
      targets: [PRINTER],
      dest: PRINTER,
      attachments: undefined,
    });
    expect(next).toEqual({ value: "", dest: null, attachments: [], chipOpen: false });
    // The next capture goes to the Inbox, not to the board picked before.
    submitCaptureBox({ ...next, value: "Buy milk" }, captureLine, [PRINTER]);
    expect(captureLine).toHaveBeenLastCalledWith("Buy milk", expect.objectContaining({ dest: null }));
  });

  it("a blank line sends nothing and keeps the pick", () => {
    const captureLine = vi.fn();
    const box = { value: "  ", dest: PRINTER, attachments: [], chipOpen: false };
    expect(submitCaptureBox(box, captureLine, [PRINTER])).toBe(box);
    expect(captureLine).not.toHaveBeenCalled();
  });

  it("the Inbox box submits through it, and applies EVERY field of the next state", () => {
    // Round 4: dropping `setCaptureDest(next.dest)` kept the other tests
    // green, and the chip then kept its pick after a capture. Each field of
    // `CaptureBoxState` must reach its setter.
    const view = read("components/InboxView.tsx");
    const submit = view.slice(view.indexOf("const submit = () => {"), view.indexOf("const onKeyDown"));
    expect(submit).toMatch(/const next = submitCaptureBox\(/);
    for (const [setter, field] of [
      ["setValue", "value"],
      ["setCaptureDest", "dest"],
      ["setPendingAtts", "attachments"],
      ["setChipOpen", "chipOpen"],
    ]) {
      expect(submit, setter).toContain(`${setter}(next.${field});`);
    }
    // ...and those are all the fields there are.
    const next = submitCaptureBox(
      { value: "x", dest: null, attachments: [], chipOpen: true },
      () => undefined,
      [],
    );
    expect(Object.keys(next).sort()).toEqual(["attachments", "chipOpen", "dest", "value"]);
  });
});

describe("the capture chip's list (S6g repair, advisory)", () => {
  it("hangs from the chip through the shared AnchoredPanel, aligned to its right edge", () => {
    const chip = read("components/CaptureProjectChip.tsx");
    expect(chip).toMatch(/<AnchoredPanel[\s\S]*?anchor=\{chip\}[\s\S]*?align="end"/);
    expect(chip).not.toMatch(/absolute right-0 top-full/);
    expect(chip).toMatch(/shouldDismiss\(/);
  });
});
