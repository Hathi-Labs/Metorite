import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  CATEGORY_HINT,
  CATEGORY_LABEL,
  CLOSING_CATEGORIES,
  EDITABLE_CATEGORIES,
  STATUS_CATEGORIES,
  categoryLabel,
  closesTask,
  groupByCategory,
  landingLane,
  needsChoice,
  reopenLane,
  stageLanes,
} from "./statusCategory";

/**
 * The fence for `statusCategory.ts` (R7).
 *
 * The interesting half of this suite is the FIRST describe block, which reads
 * the gateway's Python and fails when this client mirror and the server's
 * vocabulary disagree. Everything else is behaviour.
 *
 * `core.py` says it best, about its own hand-mirrored tuple: "a tuple that
 * mirrors a migration by hand needs a test that reads the migration; anything
 * else is a comment claiming to be an invariant." That warning is there because
 * the activity vocabulary drifted once and every file upload answered 422 while
 * 25 tests passed. This file is a hand mirror one layer further out, so it gets
 * the same treatment.
 */

const CORE_PY = fileURLToPath(
  new URL(
    "../../../../apps/services/gateway/gateway/routes/projects/core.py",
    import.meta.url
  )
);

/** The `STATUS_CATEGORIES` tuple as the gateway declares it. */
function serverCategories(): string[] {
  const source = readFileSync(CORE_PY, "utf8");
  const block = source.match(
    /^STATUS_CATEGORIES:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\(([\s\S]*?)\)/m
  );
  if (!block) throw new Error(`No STATUS_CATEGORIES tuple found in ${CORE_PY}`);
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

/** The `CLOSING_CATEGORIES` frozenset as the gateway declares it. */
function serverClosing(): string[] {
  const source = readFileSync(CORE_PY, "utf8");
  const block = source.match(
    /^CLOSING_CATEGORIES:\s*frozenset\[str\]\s*=\s*frozenset\(\{([\s\S]*?)\}\)/m
  );
  if (!block) throw new Error(`No CLOSING_CATEGORIES set found in ${CORE_PY}`);
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

describe("the client mirror agrees with the gateway", () => {
  it("finds the gateway source at all", () => {
    // A wrong relative path would make every assertion below vacuous, so the
    // path is asserted before it is trusted.
    expect(serverCategories().length).toBeGreaterThan(0);
  });

  it("covers every category the server accepts", () => {
    // Order is deliberately NOT compared: the server's tuple order is its own
    // and lifecycle order is ours (see the module header). Membership is the
    // invariant — a value the server accepts and this file has never heard of
    // is a status that renders with no label.
    const server = serverCategories();
    const missing = server.filter((c) => !STATUS_CATEGORIES.includes(c as never));
    expect(
      missing,
      `the gateway accepts ${missing.join(", ")}, which statusCategory.ts does not list`
    ).toEqual([]);
  });

  it("invents no category the server would refuse", () => {
    const server = new Set(serverCategories());
    const invented = STATUS_CATEGORIES.filter((c) => !server.has(c));
    expect(
      invented,
      `statusCategory.ts lists ${invented.join(", ")}, which the gateway would 422`
    ).toEqual([]);
  });

  it("agrees about which categories close a task", () => {
    // This one matters more than the vocabulary: `closesTask` decides whether
    // the editor warns that a lane completes everything reaching it. Saying no
    // when the server says yes is a control that lies about a one-way action.
    expect([...CLOSING_CATEGORIES].sort()).toEqual(serverClosing().sort());
  });

  it("offers only categories that exist, and every editable one has a label", () => {
    for (const category of EDITABLE_CATEGORIES) {
      expect(STATUS_CATEGORIES).toContain(category);
      expect(CATEGORY_LABEL[category], `no label for ${category}`).toBeTruthy();
      expect(CATEGORY_HINT[category], `no hint for ${category}`).toBeTruthy();
    }
  });

  it("keeps triage displayable but does not offer it", () => {
    // Owner directive 2026-09-03: no triage on the create path. It stays in the
    // vocabulary because the intake rail writes it and `triage_exclusion_clause`
    // keys off it, so a stored row must still render.
    expect(STATUS_CATEGORIES).toContain("triage");
    expect(EDITABLE_CATEGORIES as readonly string[]).not.toContain("triage");
    expect(CATEGORY_LABEL.triage).toBe("Triage");
  });
});

describe("closesTask", () => {
  it("is true for the two closing categories and false for the rest", () => {
    expect(closesTask("done")).toBe(true);
    expect(closesTask("cancelled")).toBe(true);
    expect(closesTask("in_progress")).toBe(false);
    expect(closesTask("backlog")).toBe(false);
    expect(closesTask("triage")).toBe(false);
  });

  it("does not throw on a value it has never seen", () => {
    expect(closesTask("something_new")).toBe(false);
  });
});

describe("categoryLabel", () => {
  it("labels the known values", () => {
    expect(categoryLabel("in_progress")).toBe("In progress");
  });

  it("shows an unknown value rather than an empty chip", () => {
    // A server ahead of this file should read as an unfamiliar word, not as a
    // status that looks broken.
    expect(categoryLabel("archived_soon")).toBe("archived_soon");
  });

  it("names the absence when a task has no status at all", () => {
    expect(categoryLabel(null)).toBe("No status");
    expect(categoryLabel(undefined)).toBe("No status");
    expect(categoryLabel("")).toBe("No status");
  });
});

describe("groupByCategory", () => {
  const row = (name: string, category: string, position = 0) => ({
    name,
    category,
    position,
  });

  it("returns all five editable groups even when empty", () => {
    const groups = groupByCategory([]);
    expect(groups.map((g) => g.category)).toEqual([...EDITABLE_CATEGORIES]);
    // An empty group is how a member finds out they have no Cancelled lane.
    expect(groups.every((g) => g.rows.length === 0)).toBe(true);
  });

  it("puts the groups in lifecycle order, not alphabetical", () => {
    const groups = groupByCategory([row("Done", "done"), row("Backlog", "backlog")]);
    expect(groups.map((g) => g.category).slice(0, 5)).toEqual([
      "backlog",
      "todo",
      "in_progress",
      "done",
      "cancelled",
    ]);
  });

  it("orders rows inside a group by position, then name", () => {
    const groups = groupByCategory([
      row("Zebra", "todo", 1),
      row("Apple", "todo", 5),
      row("Banana", "todo", 1),
    ]);
    const todo = groups.find((g) => g.category === "todo")!;
    expect(todo.rows.map((r) => r.name)).toEqual(["Banana", "Zebra", "Apple"]);
  });

  it("shows a triage lane rather than dropping it", () => {
    // The regression this guards: omitting triage from the editor's groups
    // while a root still has a triage status would hide a lane that the board
    // still draws, on the one screen that could rename it.
    const groups = groupByCategory([row("Intake", "triage")]);
    const triage = groups.find((g) => g.category === "triage");
    expect(triage, "a stored triage status vanished from the editor").toBeTruthy();
    expect(triage!.rows.map((r) => r.name)).toEqual(["Intake"]);
    // Shown, but not offered on the create path.
    expect(triage!.editable).toBe(false);
  });

  it("shows a category neither list knows, under its raw name", () => {
    const groups = groupByCategory([row("Parked", "on_hold")]);
    const stray = groups.find((g) => g.category === "on_hold");
    expect(stray, "an unknown category's rows were dropped").toBeTruthy();
    expect(stray!.editable).toBe(false);
    expect(stray!.label).toBe("on_hold");
  });

  it("lists an unknown category's rows once, not once per row", () => {
    // The stray branch filters the whole sorted list per row, so a second row
    // in the same unknown category must not add a second group.
    const groups = groupByCategory([
      row("Parked", "on_hold", 0),
      row("Frozen", "on_hold", 1),
    ]);
    expect(groups.filter((g) => g.category === "on_hold")).toHaveLength(1);
    expect(
      groups.find((g) => g.category === "on_hold")!.rows.map((r) => r.name)
    ).toEqual(["Parked", "Frozen"]);
  });

  it("does not mutate the array it was handed", () => {
    const rows = [row("B", "todo", 2), row("A", "todo", 1)];
    groupByCategory(rows);
    expect(rows.map((r) => r.name)).toEqual(["B", "A"]);
  });
});

// ── D79: stages group, statuses write ──────────────────────────────────────

const lane = (id: string, category: string, position: number, name = id) => ({
  id,
  name,
  category,
  position,
});

/** A set with two In progress statuses and two Done statuses, out of order. */
const RELAUNCH = [
  lane("shipped", "done", 35, "Shipped"),
  lane("review", "in_progress", 30, "In review"),
  lane("todo", "todo", 20, "To do"),
  lane("doing", "in_progress", 25, "Doing"),
  lane("backlog", "backlog", 10, "Backlog"),
  lane("done", "done", 40, "Done"),
];

describe("which status a stage resolves to (D79)", () => {
  it("lists a stage's statuses in board order: position, then name", () => {
    expect(stageLanes(RELAUNCH, "in_progress").map((r) => r.id)).toEqual([
      "doing",
      "review",
    ]);
    const tied = [lane("b", "todo", 1, "B"), lane("a", "todo", 1, "A")];
    expect(stageLanes(tied, "todo").map((r) => r.id)).toEqual(["a", "b"]);
  });

  it("lands on the FIRST status of the stage, and on nothing for an empty one", () => {
    expect(landingLane(RELAUNCH, "done")?.id).toBe("shipped");
    expect(landingLane(RELAUNCH, "cancelled")).toBeNull();
  });

  it("asks only when a stage holds two or more statuses", () => {
    expect(needsChoice(RELAUNCH, "in_progress")).toBe(true);
    expect(needsChoice(RELAUNCH, "done")).toBe(true);
    expect(needsChoice(RELAUNCH, "todo")).toBe(false);
    expect(needsChoice(RELAUNCH, "cancelled")).toBe(false);
  });
});

/**
 * One reopen rule (D79). The gateway's is `personal.reopen_if_closed`: the
 * first `todo` status of the set, else `load_default_status(db, owner)`,
 * which is the first status that is not triage, and the task stays where it
 * is when that one closes too. These cases are that rule. The source fence
 * below fails if the gateway changes its shape without this file.
 */
describe("one reopen rule, the gateway's (D79)", () => {
  it("reopens into the first To do status, past a Backlog status above it", () => {
    expect(reopenLane(RELAUNCH)?.id).toBe("todo");
  });

  it("falls back to the first status that is not triage", () => {
    const noTodo = [
      lane("triage", "triage", 1),
      lane("doing", "in_progress", 5),
      lane("done", "done", 9),
    ];
    expect(reopenLane(noTodo)?.id).toBe("doing");
  });

  it("answers null when that first status closes too", () => {
    const closedFirst = [lane("done", "done", 1), lane("doing", "in_progress", 5)];
    expect(reopenLane(closedFirst)).toBeNull();
  });

  it("matches the shape of the gateway's reopen", () => {
    const personal = readFileSync(
      fileURLToPath(
        new URL(
          "../../../../apps/services/gateway/gateway/routes/projects/personal.py",
          import.meta.url
        )
      ),
      "utf8"
    );
    const body = personal.slice(
      personal.indexOf("async def reopen_if_closed"),
      personal.indexOf("OVERLAY_KEYS", personal.indexOf("async def reopen_if_closed"))
    );
    expect(body).toContain('lane = await load_default_status(db, owner, "todo")');
    expect(body).toContain("lane = await load_default_status(db, owner)");
    expect(body).toMatch(/in CLOSING_CATEGORIES:\s*\n\s*return None/);
    const core = readFileSync(CORE_PY, "utf8");
    const pick = core.slice(core.indexOf("async def load_default_status"));
    expect(pick).toContain('clauses.append("category <> :triage")');
    expect(pick).toContain('" ORDER BY position, name LIMIT 1"');
  });
});
