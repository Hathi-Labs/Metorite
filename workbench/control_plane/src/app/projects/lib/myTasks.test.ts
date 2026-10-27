/**
 * Projects · My tasks, the roll-up space (WS-64 OP-11, `one_projects_app.md`
 * §9 OP-11).
 *
 * Fences: the old ids open My tasks with a chip, each chip follows the
 * server's rules, the board writes the first status of a stage in the task's
 * OWN project, each row names its project, the phone tab, the page facts,
 * the address, and the rule that My tasks reads no client counter and no
 * whole-list hydrate.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import type { StatusRow, TaskRow } from "./api";
import { MY_TASKS_FACT, projectsAssistantFacts } from "./assistantFacts";
import { VIEW_MODES } from "./commands";
import {
  BOARD_STAGES,
  LEGACY_CHIP,
  MY_TASKS_CHIPS,
  MY_TASKS_MODES,
  type ChipRow,
  type MyTasksChip,
  asChip,
  chipCount,
  chipForApp,
  chipRows,
  doneThisWeek,
  droppedStage,
  inChip,
  mineStatuses,
  myTasksHref,
  myTasksQuery,
  openCount,
  MY_TASKS_MAX_PAGES,
  MY_TASKS_MAX_ROWS,
  MY_TASKS_PAGE,
  STATUS_READ_FAILED,
  phoneMyTasksTap,
  planMineDrop,
  projectChipText,
  quickAddHome,
  stageDrop,
  stageGroups,
  truncationNotice,
  withoutMyTasks,
} from "./myTasks";
import { projectAppSections, resolveAppChip, resolveAppLink } from "./projectApps";

const ON = { NEXT_PUBLIC_PROJECTS_MY_WORK: "1" };
const ROOT = path.join(__dirname, "..");
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), "utf8");
const code = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("the old ids open My tasks with a chip", () => {
  const sections = projectAppSections(ON);
  const CASES: [string, MyTasksChip][] = [
    ["my-today", "today"],
    ["my-next", "next"],
    ["my-waiting", "waiting"],
    ["my-someday", "someday"],
    ["my-done", "done"],
  ];

  it("names the five ids, and no other", () => {
    expect(Object.entries(LEGACY_CHIP)).toEqual(CASES);
  });

  it.each(CASES)("%s opens my-tasks on the %s chip", (id, chip) => {
    expect(resolveAppLink(id, sections)).toBe("my-tasks");
    expect(resolveAppChip(id)).toBe(chip);
    expect(chipForApp(id.toUpperCase())).toBe(chip);
  });

  it("my-tasks takes its chip from the link, and an unknown chip is All", () => {
    expect(resolveAppChip("my-tasks", "waiting")).toBe("waiting");
    expect(resolveAppChip("my-tasks", "bogus")).toBe("all");
    expect(resolveAppChip("my-tasks", null)).toBeNull();
    expect(resolveAppChip("reports", "next")).toBeNull();
    expect(asChip(undefined)).toBe("all");
    expect(asChip(" Next ")).toBe("next");
  });

  it("the chips are All, Today, Next, Waiting, Someday and Done", () => {
    expect(MY_TASKS_CHIPS.map((c) => c.label)).toEqual(["All", "Today", "Next", "Waiting", "Someday", "Done"]);
  });
});

// ── The chip rules, case by case, against `mine.fold_dispositions` ─────────

const TODAY = "2026-10-27";
const row = (over: Partial<ChipRow>): ChipRow => ({
  disposition: "NEXT",
  is_mine: true,
  completed_at: null,
  archived_at: null,
  defer_until: null,
  start_date: null,
  due_at: null,
  ...over,
});

/**
 * The table of the server's rules. Each case names the chips a row is in,
 * with the rule of `mine.fold_dispositions` that puts it there. "all" holds
 * every row but TRASH. INBOX is the inbox badge's, so no chip of My tasks
 * holds it but All.
 */
const RULES: [string, ChipRow, MyTasksChip[]][] = [
  ["NEXT and mine, no date", row({}), ["all", "next"]],
  ["NEXT and mine, due today", row({ due_at: "2026-10-27" }), ["all", "next", "today"]],
  ["NEXT and mine, overdue", row({ due_at: "2026-10-01" }), ["all", "next", "today"]],
  ["NEXT and mine, started today", row({ start_date: "2026-10-27" }), ["all", "next", "today"]],
  ["NEXT and mine, starts tomorrow", row({ start_date: "2026-10-28", due_at: "2026-10-01" }), ["all", "next"]],
  ["NEXT and mine, deferred to tomorrow", row({ defer_until: "2026-10-28", due_at: "2026-10-27" }), ["all", "next"]],
  ["NEXT and mine, due next week", row({ due_at: "2026-11-03" }), ["all", "next"]],
  ["NEXT and not mine", row({ is_mine: false, due_at: "2026-10-27" }), ["all"]],
  ["WAITING", row({ disposition: "WAITING", is_mine: false }), ["all", "waiting"]],
  ["SOMEDAY", row({ disposition: "SOMEDAY" }), ["all", "someday"]],
  ["DONE, due today", row({ disposition: "DONE", due_at: "2026-10-27", completed_at: "2026-10-26T10:00:00Z" }), ["all", "done"]],
  ["INBOX", row({ disposition: "INBOX", is_mine: false }), ["all"]],
  ["TRASH", row({ disposition: "TRASH" }), []],
];

describe("each chip is the server's rule", () => {
  const ids = MY_TASKS_CHIPS.map((c) => c.id);

  it.each(RULES)("%s", (_name, given, chips) => {
    const got = ids.filter((chip) => inChip(given, chip, TODAY, "UTC"));
    expect(got.sort()).toEqual([...chips].sort());
  });

  it("chipRows keeps the order the rows came in", () => {
    const rows = RULES.map(([name, r]) => ({ ...r, id: name }));
    expect(chipRows(rows, "next", TODAY, "UTC").map((r) => r.id)).toEqual(
      RULES.filter(([, , chips]) => chips.includes("next")).map(([name]) => name),
    );
  });

  it("the server's fold counts the same five keys, by the same rules", () => {
    // The table above mirrors these lines. A change on either side fails here.
    const mine = fs.readFileSync(
      path.join(ROOT, "..", "..", "..", "..", "..", "apps", "services", "gateway", "gateway", "routes", "projects", "mine.py"),
      "utf8",
    );
    expect(mine).toContain('DISPOSITION_KEYS: tuple[str, ...] = ("inbox", "today", "next", "waiting", "someday", "done")');
    expect(mine).toContain('elif effective == "NEXT" and mine:');
    expect(mine).toContain('elif effective == "WAITING":');
    expect(mine).toContain('elif effective == "SOMEDAY":');
    expect(mine).toContain('elif effective == "DONE":');
    expect(mine).toMatch(/if completed_at is not None or effective != "NEXT" or not is_mine:/);
    for (const chip of ids.filter((c) => c !== "all")) expect(mine).toContain(`"${chip}"`);
  });

  it("the Today chip calls the one Today rule", () => {
    expect(code("lib/myTasks.ts")).toContain("isInToday(");
  });

  it("the chip counts come from the server summary, and All shows none", () => {
    const summary = { by_disposition: { today: 2, next: 5, waiting: 0 } };
    expect(chipCount(summary, "next")).toBe(5);
    expect(chipCount(summary, "waiting")).toBe(0);
    expect(chipCount(summary, "someday")).toBeNull();
    expect(chipCount(summary, "all")).toBeNull();
    expect(chipCount(null, "next")).toBeNull();
  });
});

// ── The board ───────────────────────────────────────────────────────────────

const status = (id: string, project: string, category: string, position: number): StatusRow => ({
  id,
  project_id: project,
  name: id,
  color: "",
  position,
  category,
  is_default: false,
});

/** Two projects with their own lanes. Project B's "todo" sorts first in a merged set. */
const A = [status("a-back", "pa", "backlog", 10), status("a-todo", "pa", "todo", 20), status("a-doing", "pa", "in_progress", 30), status("a-review", "pa", "in_progress", 40), status("a-done", "pa", "done", 50)];
const B = [status("b-todo", "pb", "todo", 1), status("b-done", "pb", "done", 2)];

const task = (over: Partial<TaskRow>): TaskRow =>
  ({ id: "t1", project_id: "pa", root_project_id: "pa", status_id: "a-todo", title: "T", ...over }) as TaskRow;

describe("a board drop writes the first status of the stage in the task's OWN project", () => {
  it("picks the task's own project's lane, not the merged set's", () => {
    const t = task({ status_id: "a-todo", status_category: "todo" });
    const plan = stageDrop(t, "in_progress", A);
    expect(plan).toEqual({ kind: "write", patch: { status_id: "a-doing" }, stage: "in_progress" });
    // The first `todo` of the merged set is B's. The drop never takes it.
    const merged = [...B, ...A];
    expect(stageDrop(task({ status_id: "a-done", status_category: "done" }), "todo", A)).toEqual({
      kind: "write",
      patch: { status_id: "a-todo" },
      stage: "todo",
    });
    expect(merged[0].id).toBe("b-todo");
  });

  it("writes nothing inside the task's own stage", () => {
    expect(stageDrop(task({ status_id: "a-review", status_category: "in_progress" }), "in_progress", A)).toEqual({
      kind: "none",
    });
    expect(stageDrop(task({}), null, A)).toEqual({ kind: "none" });
  });

  it("refuses a stage the project has no status in, and says so", () => {
    const plan = stageDrop(task({ project_id: "pb", status_id: "b-todo", status_category: "todo" }), "backlog", B);
    expect(plan.kind).toBe("refuse");
    if (plan.kind === "refuse") expect(plan.reason).toMatch(/Backlog/);
  });

  it("reads the stage from the dragged task's position write", () => {
    expect(droppedStage("t1", [{ task_id: "t0", position: 1, group_key: "todo" }, { task_id: "t1", position: 2, group_key: "done" }])).toBe("done");
    expect(droppedStage("t1", [])).toBeNull();
  });

  it("calls the one lane rule of `lib/statusCategory.ts`", () => {
    const source = code("lib/myTasks.ts");
    expect(source).toContain("landingLane(ownStatuses, stage)");
  });

  it("draws the four stages always, and Cancelled only while a row sits there", () => {
    const rows = [task({ id: "1", status_category: "todo" }), task({ id: "2", status_category: "done" })];
    expect(stageGroups(rows).map((g) => g.key)).toEqual(BOARD_STAGES);
    const withCancelled = [...rows, task({ id: "3", status_category: "cancelled" })];
    expect(stageGroups(withCancelled).map((g) => g.key)).toEqual([...BOARD_STAGES, "cancelled"]);
    expect(stageGroups(rows).find((g) => g.key === "todo")?.tasks.map((t) => t.id)).toEqual(["1"]);
  });

  it("gives a row of an unknown project a status in its stage", () => {
    const rows = [task({ id: "x", project_id: "hidden", status_id: "s-x", status_category: "in_progress" })];
    const merged = mineStatuses(rows, A);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({ id: "s-x", category: "in_progress", name: "In progress" });
    // A status the client holds keeps its own name.
    expect(mineStatuses([task({ status_id: "a-review" })], A)[0].name).toBe("a-review");
  });
});

// ── The project chip ────────────────────────────────────────────────────────

describe("each row names its project", () => {
  it("shows two levels whole, and keeps both ends of a deeper path", () => {
    expect(projectChipText(["Ops"])).toEqual({ label: "Ops", title: "Ops" });
    expect(projectChipText(["Ops", "Hiring"])).toEqual({ label: "Ops › Hiring", title: "Ops › Hiring" });
    expect(projectChipText(["Ops", "People", "Hiring"])).toEqual({
      label: "Ops › … › Hiring",
      title: "Ops › People › Hiring",
    });
    expect(projectChipText([])).toBeNull();
    expect(projectChipText(["  "])).toBeNull();
  });

  it("the list, the board and the table draw the chip from the same prop", () => {
    for (const file of ["components/TaskList.tsx", "components/TaskBoard.tsx", "components/TableView.tsx"]) {
      const source = code(file);
      expect(source, file).toContain("projectPath?: (task: TaskRow) => readonly string[] | null;");
      expect(source, file).toMatch(/projectPath \? \([\s\S]{0,80}<ProjectPathChip path=\{projectPath\(task\)\} \/>|projectPath \? <ProjectPathChip path=\{projectPath\(task\)\} \/>/);
    }
    expect(code("components/MyTasksSpace.tsx").match(/projectPath=\{pathOf\}/g)).toHaveLength(3);
  });

  it("the chip wears the muted meta tone and no colour of its own", () => {
    const source = code("components/ProjectPathChip.tsx");
    expect(source).toContain("text-muted-foreground");
    expect(source).toContain("truncate");
    expect(source).not.toMatch(/bg-|border-/);
  });
});

describe("the task panel keeps a disposition control", () => {
  it("writes it to the server when the store does not hold the task", () => {
    const panel = code("components/TaskPanel.tsx");
    expect(panel).toContain("above={strip ?? serverStrip}");
    expect(panel).toMatch(/!strip && enabled && myWork && mine \?/);
    expect(panel).toContain("lensPatchItem(task.id, { disposition: next })");
    const strip = code("components/MineDispositionStrip.tsx");
    expect(strip).toContain("STRIP_DISPOSITIONS");
    expect(strip).not.toMatch(/useTaskStore/);
  });
});

// ── The tabs, the phone, the facts and the address ─────────────────────────

describe("the tabs", () => {
  it("are a project's canvases with the same ids and icons, less the Timeline", () => {
    expect(MY_TASKS_MODES).toEqual(
      VIEW_MODES.filter((m) => m.id !== "timeline").map((m) => ({ id: m.id, icon: m.icon })),
    );
  });
});

describe("the phone tab", () => {
  it("opens My tasks, and on My tasks opens the Projects sheet", () => {
    expect(phoneMyTasksTap(null)).toBe("open");
    expect(phoneMyTasksTap("my-inbox")).toBe("open");
    expect(phoneMyTasksTap("my-tasks")).toBe("sheet");
  });
});

describe("the page facts", () => {
  it("say My tasks is the member's roll-up and not a project", () => {
    const facts = projectsAssistantFacts({
      app: "my-tasks",
      chip: "next",
      view: "board",
      node: { id: "p1", name: "Last project", level: "project", archived: false, personal: false },
    });
    expect(facts.facts[0]).toBe(MY_TASKS_FACT);
    expect(MY_TASKS_FACT).toContain("My tasks roll-up (everything assigned to them), not a project");
    expect(MY_TASKS_FACT).toContain("goes to their task inbox unless they name a project");
    expect(facts.facts.join(" ")).toContain("board view of My tasks");
    expect(facts.facts.join(" ")).toContain("next chip");
    // The project still held by the page is not "here".
    expect(facts.facts.join(" ")).not.toContain("Last project");
    expect(facts.label).toBe("My tasks");
  });

  it("change nothing on a project", () => {
    const facts = projectsAssistantFacts({
      node: { id: "p1", name: "Ops", level: "project", archived: false, personal: false },
      view: "board",
    });
    expect(facts.facts.join(" ")).not.toContain(MY_TASKS_FACT);
    expect(facts.facts[0]).toContain("Ops");
  });
});

describe("the address", () => {
  it("writes the chip after the app, keeps the other keys, and omits All", () => {
    expect(myTasksHref("", "next")).toBe("/projects?app=my-tasks&chip=next");
    expect(myTasksHref("?app=my-today", "today")).toBe("/projects?app=my-tasks&chip=today");
    expect(myTasksHref("?task=t1&chip=done", "all")).toBe("/projects?app=my-tasks&task=t1");
  });

  it("takes My tasks out when the member leaves it", () => {
    expect(withoutMyTasks("?app=my-tasks&chip=next")).toBe("/projects");
    expect(withoutMyTasks("?app=my-tasks&chip=next&task=t1")).toBe("/projects?task=t1");
    expect(withoutMyTasks("?app=reports")).toBe("/projects?app=reports");
  });

  it("the page keeps the address in step with the chip", () => {
    const page = code("page.tsx");
    expect(page).toContain("myTasksHref(search, myTasks.chip)");
    expect(page).toContain("withoutMyTasks(search)");
    expect(page).toContain('resolveAppChip(appLink, searchParams.get("chip"))');
  });
});

// ── The reads ───────────────────────────────────────────────────────────────

describe("the reads", () => {
  it("one page of the list is scope=mine with the overlay", () => {
    expect(myTasksQuery({ q: "x" }, 2)).toEqual({ q: "x", scope: "mine", with_overlay: 1, page: 2, page_size: 100 });
  });

  it("My tasks reads no client counter of /tasks and runs no whole-list hydrate", () => {
    for (const file of ["components/MyTasksSpace.tsx", "lib/myTasks.ts", "lib/myWork.ts"]) {
      const source = code(file);
      expect(source, file).not.toMatch(/viewCounts|inboxCount|myWorkCounts/);
      expect(source, file).not.toMatch(/useTaskStore|taskStore|hydrate\(/);
    }
    const page = code("page.tsx");
    expect(page).not.toMatch(/viewCounts|myWorkCounts/);
  });

  it("the Overview asks Analytics with subject=me, and never the Outlook", () => {
    const source = code("components/MyTasksSpace.tsx");
    for (const read of ["stuck(undefined, \"me\")", "load(undefined, \"me\")", "throughput(undefined, undefined, \"me\")", "finished(undefined, undefined, \"me\")"]) {
      expect(source).toContain(read);
    }
    expect(source).not.toMatch(/outlook\(/);
    expect(source).toContain("attentionApi");
    expect(source).toContain('scope: "mine"');
  });

  it("the Overview figures come from the server", () => {
    expect(openCount({ tasks: 10, by_category: { done: 3, cancelled: 1, todo: 6 } })).toBe(6);
    expect(doneThisWeek({ series: [{ week_start: "a", completed: 1 }, { week_start: "b", completed: 4 }], current_week_partial: true })).toBe(4);
    expect(doneThisWeek({ series: [], current_week_partial: false })).toBeNull();
    expect(doneThisWeek(null)).toBeNull();
  });
});

// ── OP-11 fix round ─────────────────────────────────────────────────────────

describe("one write of the My tasks board (items 1 to 3)", () => {
  const own = { rows: A, failed: false };
  const t = task({ status_id: "a-todo", status_category: "todo" });

  it("the Mark-done tick and the Status menu write their patch (item 1)", () => {
    // TaskBoard sends a tick and a menu pick with NO position writes. The old
    // drop read only the stage of a position write, so it sent nothing.
    expect(planMineDrop(t, [], { status_id: "a-done" }, "category", own)).toEqual({
      kind: "write",
      patch: { status_id: "a-done" },
      stage: "done",
    });
    expect(planMineDrop(t, [], { status_id: "a-todo" }, "category", own)).toEqual({ kind: "none" });
  });

  it("a drag sends the stage half from the task's OWN project and the lane half, in one patch (item 3)", () => {
    const writes = [{ task_id: "t1", position: 1, group_key: "in_progress" }];
    // The board's own status_id came from the merged set: B's, so wrong here.
    const plan = planMineDrop(t, writes, { status_id: "b-todo", type_id: "ty2" }, "category", own);
    expect(plan).toEqual({
      kind: "write",
      patch: { status_id: "a-doing", type_id: "ty2" },
      stage: "in_progress",
    });
  });

  it("a drag inside the same stage still sends the lane half (item 3)", () => {
    const writes = [{ task_id: "t1", position: 1, group_key: "todo" }];
    expect(planMineDrop(t, writes, { type_id: "ty2" }, "category", own)).toEqual({
      kind: "write",
      patch: { type_id: "ty2" },
      stage: null,
    });
    expect(planMineDrop(t, writes, null, "category", own)).toEqual({ kind: "none" });
  });

  it("says a failed status read failed, not that the status is missing (item 7)", () => {
    const writes = [{ task_id: "t1", position: 1, group_key: "in_progress" }];
    const plan = planMineDrop(t, writes, null, "category", { rows: [], failed: true });
    expect(plan).toEqual({ kind: "refuse", reason: STATUS_READ_FAILED });
    expect(STATUS_READ_FAILED).not.toMatch(/has no/);
  });

  it("another axis sends the board's patch as a project board does", () => {
    expect(planMineDrop(t, [], { project_id: "pb" }, "project", own)).toEqual({
      kind: "write",
      patch: { project_id: "pb" },
      stage: null,
    });
  });

  it("the space routes a status write through the subtask question (item 2)", () => {
    const space = code("components/MyTasksSpace.tsx");
    expect(space).toContain("planMineDrop(");
    expect(space).toContain("subtaskComplete.changeStatus(task, status, own.rows");
    expect(space).toContain("{subtaskComplete.dialog}");
    // The board's menu and tick read the card's own statuses.
    expect(space).toMatch(/<TaskBoard[\s\S]*statusesFor=\{statusesFor\}/);
    const table = code("components/TableView.tsx");
    expect(table).toContain("statusesFor?.(task) ?? statuses, () => onReload?.()");
  });
});

describe("the cap of a chip read (item 5)", () => {
  it("is one constant", () => {
    expect(MY_TASKS_MAX_ROWS).toBe(MY_TASKS_PAGE * MY_TASKS_MAX_PAGES);
    expect(MY_TASKS_MAX_ROWS).toBe(2000);
  });

  it("says so past the cap, and says nothing for a whole read", () => {
    expect(truncationNotice(2000, 2500)).toBe("Showing the first 2,000 of 2,500. Narrow the filter or use All.");
    expect(truncationNotice(300, 300)).toBeNull();
    expect(truncationNotice(0, null)).toBeNull();
  });
});

describe("a quick add of My tasks (item 10)", () => {
  it("files to the personal root, never to an id no server row has", () => {
    expect(quickAddHome("root-1")).toBe("root-1");
    for (const bad of ["", "  ", "mine", "my-tasks", null, undefined]) {
      expect(quickAddHome(bad)).toBeNull();
    }
  });

  it("draws no per-group add on List, Board or Table, and a day add only with a home", () => {
    const space = code("components/MyTasksSpace.tsx");
    expect(space.match(/canQuickAdd=\{false\}/g)).toHaveLength(3);
    expect(space).toContain("canQuickAdd={home !== null}");
    expect(space).not.toContain('personalRootId ?? ""');
  });
});

describe("Today in the member's stored zone (item 8)", () => {
  // Stored zone IST, browser UTC. Due 2026-10-27T20:00Z is 28 October
  // 01:30 in IST, so on the server's 27 October it is NOT in Today. The UTC
  // day of the same instant is the 27th, which said it was.
  const r = row({ disposition: "NEXT", is_mine: true, due_at: "2026-10-27T20:00:00Z" });

  it("the chip agrees with the server when the browser sits in another zone", () => {
    expect(inChip(r, "today", TODAY, "Asia/Kolkata")).toBe(false);
    expect(inChip(r, "today", TODAY, "UTC")).toBe(true);
    expect(chipRows([r], "today", TODAY, "Asia/Kolkata")).toEqual([]);
  });

  it("the space passes the summary's zone to every chip filter", () => {
    const space = code("components/MyTasksSpace.tsx");
    expect(space).toContain("summary.data?.time_zone");
    expect(space).toContain("chipRows(list.rows, chip, today, zone)");
    expect(space).toContain("chipRows(month.rows, chip, today, zone)");
  });
});

describe("errors of My tasks (items 6 and 7)", () => {
  const space = code("components/MyTasksSpace.tsx");

  it("a successful read clears only a READ error", () => {
    expect(space).not.toMatch(/setError\(/);
    expect(space).toContain("setReadError(null)");
    // The write error clears at the start of a write, or on Dismiss.
    expect(space).toContain("setWriteError(null)");
  });

  it("the Overview offers Retry when the summary read failed", () => {
    expect(space).toContain("!summary.data && summary.error");
    expect(space).toContain("onClick: summary.refresh");
  });
});
