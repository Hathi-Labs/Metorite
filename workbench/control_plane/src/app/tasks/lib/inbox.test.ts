/**
 * S6g — one inbox (my_tasks_cutover.md §5 S6g).
 *
 * The fences for the unified list: what it holds, that the badge and the
 * header count agree, the empty state, the source filter's counts, the order,
 * the actions per kind ("Not mine" never deletes), the origin marker in place
 * of `SourceBadge`, and the `m` / `o` keys.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  SOURCE_PILLS,
  assignedByLabel,
  filterBySource,
  inboxCount,
  inboxKind,
  inboxKindCounts,
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

// A capture, a capture in an Area, an untriaged board row (NEXT by
// derivation), a stated-INBOX board row, a tickled capture, an archived
// capture, and a clarified capture.
const ITEMS: MyTask[] = [
  task("c1", { createdAt: "2026-09-21T09:00:00Z" }),
  task("c2", { projectId: AREA, createdAt: "2026-09-19T09:00:00Z" }),
  task("b1", { projectId: "board-1", disposition: "NEXT", createdAt: "2026-09-18T09:00:00Z" }),
  task("b2", { projectId: "board-2", createdAt: "2026-09-22T09:00:00Z" }),
  task("t1", { deferUntil: "2999-01-01T00:00:00Z" }),
  task("a1", { archivedAt: "2026-09-20T10:00:00Z" }),
  task("n1", { disposition: "NEXT" }),
];
const FROM_PROJECTS = new Set(["b1"]);

describe("the unified list (S6g)", () => {
  it("holds the captures and the board rows, never a tickled, archived or clarified row", () => {
    const ids = inboxRows(ITEMS, FROM_PROJECTS).map((i) => i.id).sort();
    expect(ids).toEqual(["b1", "b2", "c1", "c2"]);
  });

  it("the count is one rule, read by the badge and by the header", () => {
    expect(inboxCount(ITEMS, FROM_PROJECTS)).toBe(4);
    // The sidebar badge reads `inboxCount`, and the header says the length
    // of `inboxRows` before any filter. Neither may count by its own rule.
    const sidebar = read("components/ListsSidebar.tsx");
    expect(sidebar).toMatch(/scoped\.inbox = inboxCount\(items, fromProjectIds\)/);
    const view = read("components/InboxView.tsx");
    expect(view).toMatch(/const allRows = useMemo\(\(\) => inboxRows\(items, fromProjectIds\)/);
    expect(view).toMatch(/\{allRows\.length\} to process/);
  });

  it("kind is `isPersonalTask`: my root and my Areas are personal, a board is a board", () => {
    expect(inboxKind(task("x"), scope)).toBe("personal");
    expect(inboxKind(task("x", { projectId: AREA }), scope)).toBe("personal");
    expect(inboxKind(task("x", { projectId: undefined }), scope)).toBe("personal");
    expect(inboxKind(task("x", { projectId: "board-1" }), scope)).toBe("board");
  });

  it("draws board rows first, then captures, each block in the member's sort", () => {
    const rows = inboxRows(ITEMS, FROM_PROJECTS);
    expect(orderInbox(rows, scope, "newest").map((i) => i.id)).toEqual(["b2", "b1", "c1", "c2"]);
    expect(orderInbox(rows, scope, "oldest").map((i) => i.id)).toEqual(["b1", "b2", "c2", "c1"]);
  });

  it("Inbox zero only when both kinds are empty, and the controls whenever either has rows", () => {
    const view = read("components/InboxView.tsx");
    expect(view).toMatch(/const hasRows = allRows\.length > 0;/);
    expect(view).toMatch(/\) : !hasRows \? \(/);
    expect(view).toMatch(/\{\(hasRows \|\| tickler\.length > 0\) && \(/);
    // The old second group is gone: one row component draws both kinds.
    expect(view).not.toMatch(/FromProjectsGroup/);
  });
});

describe("the source filter (S6g)", () => {
  const rows = inboxRows(ITEMS, FROM_PROJECTS);

  it("counts each pill", () => {
    expect(inboxKindCounts(rows, scope)).toEqual({ all: 4, personal: 2, board: 2 });
  });

  it("narrows to one kind", () => {
    expect(filterBySource(rows, "personal", scope).map((i) => i.id).sort()).toEqual(["c1", "c2"]);
    expect(filterBySource(rows, "board", scope).map((i) => i.id).sort()).toEqual(["b1", "b2"]);
    expect(filterBySource(rows, "all", scope)).toHaveLength(4);
  });

  it("draws All, Mine, From Projects as the shared FilterPills, and the stale chip is gone", () => {
    expect(SOURCE_PILLS.map((p) => p.label)).toEqual(["All", "Mine", "From Projects"]);
    const view = read("components/InboxView.tsx");
    expect(view).toMatch(/items=\{sourcePills\}/);
    expect(view).toMatch(/setSourceFilter\(id as InboxSource\)/);
    expect(view).not.toMatch(/"Mine" : "Team"/);
    expect(read("components/ItemList.tsx")).not.toMatch(/"Mine" : "Team"/);
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

  it("a board row offers Not mine and Open on board, and no Delete", () => {
    const s = spies();
    const actions = inboxRowActions({ kind: "board", canPromote: true, ...s });
    expect(actions.map((a) => a.id)).toEqual(["notMine", "openBoard"]);
    expect(actions.map((a) => a.label)).toEqual(["Not mine", "Open on board"]);
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
    const view = read("components/InboxView.tsx");
    expect(view).toMatch(/if \(curKind === "board"\) quickDispose\(cur\.id, "TRASH"\);/);
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

  it("m moves a capture, o opens a board row, and each refuses the other kind", () => {
    expect(view).toMatch(
      /case "m":\s*\/\/[^\n]*\n\s*if \(curKind !== "personal" \|\| !promoteAllowed\(cur\)\) break;[\s\S]*?openPromote\(cur\.id\)/,
    );
    expect(view).toMatch(
      /case "o":\s*\/\/[^\n]*\n\s*if \(curKind !== "board"\) break;[\s\S]*?router\.push\(taskDeepLink\(cur\)\)/,
    );
  });

  it("the legend names both keys", () => {
    expect(view).toMatch(/<Sc k="m">move to project<\/Sc>/);
    expect(view).toMatch(/<Sc k="o">open on board<\/Sc>/);
  });

  it("the walk covers both kinds: the keyboard reads `visible`, built from the union", () => {
    expect(view).toMatch(/return orderInbox\(filtered, scope, sortOrder\);/);
    expect(view).toMatch(/filterBySource\(allRows, sourceFilter, scope\)/);
  });
});

describe("the capture chip (S6g repair P1-b)", () => {
  it("a chip pick is for one capture: submit puts the chip back on Inbox", () => {
    const view = read("components/InboxView.tsx");
    const submit = view.slice(view.indexOf("const submit = () => {"), view.indexOf("const onKeyDown"));
    expect(submit).toMatch(/captureLine\(raw, \{[\s\S]*dest: captureDest,/);
    expect(submit).toMatch(/setCaptureDest\(null\);/);
  });
});
