// The edit card of a new Projects task, the client half (`projects_ai_chat.md` §32, EAC-2, task 3469).
//
// R7 fences named here. Each test names the mutation that turns it red.
//
//   * `answer-is-plain-json`: the answer is `APPROVE ` and plain `JSON.stringify` of `{edit: {...}}`, with the
//     seven keys and no other. Mutation: add `project_id` to the object, or wrap the body in `reply` → red.
//   * `answer-order-free`: a state that carries extra keys, in any key order, gives the same answer.
//     Mutation: `JSON.stringify({ edit: edits })` → red.
//   * `unchanged-is-plain-approve`: no change sends the bare word. Mutation: always send the edit answer → red.
//   * `unknown-shape-no-form`: an event that the client cannot read gives no form. Mutation: accept any
//     object with a `values` key → red.
//   * `limits-match-the-server`: the numbers and the key set equal those of `task_card.py`. Mutation: change
//     `MAX_ASSIGNEES` here → red.
//   * `exact-name-only`: a typed name adds a person only when it matches one person exactly. Mutation: match
//     by substring → red.
//   * `warn-before-approve`: an address that the directory does not know is named, and a known one is not.
//     Mutation: drop the `known` test → red.

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  ASSIGNEE_MAX,
  DESCRIPTION_MAX,
  MAX_ASSIGNEES,
  STATUS_MAX,
  TASK_EDIT_KEYS,
  addAssignee,
  assigneeChanges,
  changedSummary,
  fullLine,
  isIsoDate,
  isTaskCardEdits,
  isUnchanged,
  matchTyped,
  newStrangers,
  pendingLine,
  problemOf,
  removeAssignee,
  strangerLine,
  strangersOf,
  taskApproval,
  taskEditAnswer,
  taskEditFromEvent,
  typedLine,
  valuesOk,
  type DirectoryRow,
  type TaskCardEdits,
} from "@/lib/taskEditCard";

const here = path.dirname(fileURLToPath(import.meta.url));
const PY = readFileSync(path.join(here, "../../../../packages/acb_skills/acb_skills/task_card.py"), "utf8").replace(/\r\n/g, "\n");

const ORIGINAL: TaskCardEdits = {
  due: "2026-12-01",
  start: "",
  status: "Doing",
  important: null,
  leveraged: false,
  description: "Ship the pack.\nThen call Kim.",
  assignees: ["ann@acme.test", "bob@acme.test"],
};
const PID = "11111111-2222-4333-8444-555555555555";
const event = (values: unknown = ORIGINAL, rest: Record<string, unknown> = {}) => ({
  kind: "create_task", project_id: PID, values, ...rest,
});

const body = (answer: string) => JSON.parse(answer.slice("APPROVE ".length)) as Record<string, unknown>;

describe("the answer (EAC-2)", () => {
  it("answer-is-plain-json: `APPROVE ` and the JSON of {edit}, with the seven keys and no other", () => {
    const answer = taskEditAnswer({ ...ORIGINAL, due: "2027-01-31", assignees: ["bob@acme.test", "cat@acme.test"] });
    expect(answer.startsWith('APPROVE {"edit":{')).toBe(true);
    const parsed = body(answer);
    expect(Object.keys(parsed)).toEqual(["edit"]);
    const edit = parsed.edit as Record<string, unknown>;
    expect(Object.keys(edit).sort()).toEqual([...TASK_EDIT_KEYS].sort());
    expect(edit).toEqual({
      due: "2027-01-31", start: "", status: "Doing", important: null, leveraged: false,
      description: ORIGINAL.description, assignees: ["bob@acme.test", "cat@acme.test"],
    });
    // Never an id, and never the reply key of the email card.
    expect(answer).not.toContain("project_id");
    expect(answer).not.toContain('"reply"');
    // It is plain JSON.stringify, so the server decodes it with the one decode of its parser.
    expect(answer).toBe(`APPROVE ${JSON.stringify({ edit: edit })}`);
  });

  it("answer-order-free: extra keys and any key order of the state give the same answer", () => {
    const shuffled = {
      assignees: [...ORIGINAL.assignees], description: ORIGINAL.description, project_id: PID,
      leveraged: false, important: null, status: "Doing", start: "", due: "2026-12-01", note: "x",
    } as unknown as TaskCardEdits;
    expect(taskEditAnswer(shuffled)).toBe(taskEditAnswer(ORIGINAL));
    expect(taskEditAnswer(shuffled)).not.toContain("note");
  });

  it("the text is not trimmed or changed: a description keeps its line breaks and spaces", () => {
    const edit = body(taskEditAnswer({ ...ORIGINAL, description: "  a\n\n b  " })).edit as { description: string };
    expect(edit.description).toBe("  a\n\n b  ");
  });

  it("the answer of a valid state passes the same check as the card (`valuesOk`)", () => {
    const edit = body(taskEditAnswer(ORIGINAL)).edit;
    expect(valuesOk(edit)).toBe(true);
  });

  it("unchanged-is-plain-approve: no change sends APPROVE, and a change sends the edit answer", () => {
    expect(taskApproval(ORIGINAL, { ...ORIGINAL, assignees: [...ORIGINAL.assignees] })).toBe("APPROVE");
    expect(taskApproval(ORIGINAL, { ...ORIGINAL, due: "" })).toMatch(/^APPROVE \{"edit":/);
    expect(taskApproval(ORIGINAL, { ...ORIGINAL, assignees: ["ann@acme.test"] })).toMatch(/^APPROVE \{"edit":/);
  });

  it("a list of people in another order is a change, as the server compares in order", () => {
    // `task_card.changed_values`: "A list of people compares in order and as sent."
    const swapped = { ...ORIGINAL, assignees: ["bob@acme.test", "ann@acme.test"] };
    expect(isUnchanged(ORIGINAL, swapped)).toBe(false);
    expect(changedSummary(ORIGINAL, swapped)).toContain("new order");
  });

  it("a remove then an add of the same person is a change in order, never a silent no-op", () => {
    const back = addAssignee(removeAssignee(ORIGINAL.assignees, "ann@acme.test"), "ann@acme.test");
    expect(back).toEqual(["bob@acme.test", "ann@acme.test"]);
    expect(isUnchanged(ORIGINAL, { ...ORIGINAL, assignees: back })).toBe(false);
  });
});

describe("the event (EAC-2)", () => {
  it("reads the form of a card, and keeps the project for later", () => {
    const form = taskEditFromEvent(event());
    expect(form?.kind).toBe("create_task");
    expect(form?.projectId).toBe(PID);
    expect(form?.values).toEqual(ORIGINAL);
    // A copy: an edit of the list never changes the card's own values.
    expect(form?.values.assignees).not.toBe((event().values as TaskCardEdits).assignees);
  });

  it("unknown-shape-no-form: anything the client cannot read gives no form", () => {
    const bad: unknown[] = [
      undefined, null, "edit", 3, [], {},
      event(ORIGINAL, { kind: "update_task" }),
      event(ORIGINAL, { project_id: "" }),
      { kind: "create_task", values: ORIGINAL },
      event(null),
      event({ ...ORIGINAL, due: "2026-02-30" }),
      event({ ...ORIGINAL, due: "12/01/2026" }),
      event({ ...ORIGINAL, due: "٢٠٢٦-١٢-٠١" }),
      event({ ...ORIGINAL, important: "yes" }),
      event({ ...ORIGINAL, leveraged: 1 }),
      event({ ...ORIGINAL, status: "x".repeat(STATUS_MAX + 1) }),
      event({ ...ORIGINAL, description: "x".repeat(DESCRIPTION_MAX + 1) }),
      event({ ...ORIGINAL, assignees: "ann@acme.test" }),
      event({ ...ORIGINAL, assignees: ["ann@acme.test", "ANN@acme.test"] }),
      event({ ...ORIGINAL, assignees: [""] }),
      event({ ...ORIGINAL, assignees: ["a".repeat(ASSIGNEE_MAX + 1)] }),
      event({ ...ORIGINAL, assignees: Array.from({ length: MAX_ASSIGNEES + 1 }, (_, i) => `p${i}@acme.test`) }),
      event((({ start: _s, ...rest }) => rest)(ORIGINAL)),
      event({ ...ORIGINAL, extra: 1 }),
    ];
    for (const raw of bad) expect(taskEditFromEvent(raw), JSON.stringify(raw)).toBeNull();
  });

  it("a card at each limit still reads", () => {
    const edge = {
      ...ORIGINAL,
      status: "s".repeat(STATUS_MAX),
      description: "d".repeat(DESCRIPTION_MAX),
      assignees: Array.from({ length: MAX_ASSIGNEES }, (_, i) => `p${i}@acme.test`),
    };
    expect(taskEditFromEvent(event(edge))).not.toBeNull();
  });

  it("isTaskCardEdits tells a task card's edits from a new mail's", () => {
    expect(isTaskCardEdits(ORIGINAL)).toBe(true);
    expect(isTaskCardEdits({ subject: "s", body: "b", ticks: {}, fileTicks: [] })).toBe(false);
    expect(isTaskCardEdits(undefined)).toBe(false);
  });
});

describe("limits-match-the-server (EAC-2)", () => {
  const number = (name: string) => {
    const m = new RegExp(`^${name} = ([0-9_]+)`, "m").exec(PY);
    expect(m, name).not.toBeNull();
    return Number(m![1].replace(/_/g, ""));
  };

  it("the four numbers equal those of task_card.py", () => {
    expect(DESCRIPTION_MAX).toBe(number("DESCRIPTION_MAX"));
    expect(STATUS_MAX).toBe(number("STATUS_MAX"));
    expect(ASSIGNEE_MAX).toBe(number("ASSIGNEE_MAX"));
    expect(MAX_ASSIGNEES).toBe(number("MAX_ASSIGNEES"));
  });

  it("the key set equals TASK_EDIT_KEYS of task_card.py", () => {
    const block = /TASK_EDIT_KEYS: frozenset\[str\] = frozenset\(\{([\s\S]*?)\}\)/.exec(PY);
    expect(block).not.toBeNull();
    const keys = [...block![1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
    expect([...TASK_EDIT_KEYS].sort()).toEqual(keys);
  });

  it("the head and the key of the answer equal those of task_card.py", () => {
    expect(PY).toContain('EDIT_ANSWER_PREFIX = "APPROVE "');
    expect(PY).toContain('EDIT_KEY = "edit"');
  });
});

describe("the date (EAC-2)", () => {
  it("takes an empty date and a real ASCII date, and nothing else", () => {
    expect(isIsoDate("")).toBe(true);
    expect(isIsoDate("2024-02-29")).toBe(true);
    expect(isIsoDate("2025-02-29")).toBe(false);
    expect(isIsoDate("12345-01-01")).toBe(false);
    expect(isIsoDate(" 2026-01-01")).toBe(false);
    expect(isIsoDate(undefined)).toBe(false);
  });

  it("a due date that the server refuses turns Approve off with a reason", () => {
    expect(problemOf(ORIGINAL)).toBe("");
    expect(problemOf({ ...ORIGINAL, due: "12345-01-01" })).toMatch(/due date/);
  });
});

describe("the people (EAC-2)", () => {
  const DIR: DirectoryRow[] = [
    { assignee: "priya@acme.test", name: "Priya Shah" },
    { assignee: "priya.n@acme.test", name: "Priya Nair" },
    { assignee: "sam@acme.test", name: "Sam" },
    { assignee: "agent:scout", name: "Scout" },
    { assignee: "sam2@acme.test", name: "sam" },
  ];

  it("exact-name-only: an address and agent: pass as typed, in lower case", () => {
    expect(matchTyped(" Kim@Acme.Test ", DIR)).toEqual({ kind: "add", assignee: "kim@acme.test" });
    expect(matchTyped("agent:Scout", DIR)).toEqual({ kind: "add", assignee: "agent:scout" });
    expect(matchTyped("   ", DIR)).toEqual({ kind: "empty" });
  });

  it("exact-name-only: a name must be one person, whole, with case ignored", () => {
    expect(matchTyped("priya shah", DIR)).toEqual({ kind: "add", assignee: "priya@acme.test" });
    expect(matchTyped("Scout", DIR)).toEqual({ kind: "add", assignee: "agent:scout" });
    // No part of a name counts. The server refuses it after the Approve, so the form says no first.
    expect(matchTyped("Priya", DIR)).toEqual({ kind: "none", typed: "Priya" });
    expect(matchTyped("Sh", DIR)).toEqual({ kind: "none", typed: "Sh" });
    // Two people with the same name: the form asks which one.
    expect(matchTyped("Sam", DIR)).toEqual({ kind: "many", typed: "Sam" });
  });

  it("a name that did not match has a line that says what to do", () => {
    expect(typedLine({ kind: "none", typed: "Priya" })).toBe(
      "Nobody is called exactly “Priya”. Pick a person from the list, or type an address.");
    expect(typedLine({ kind: "many", typed: "Sam" })).toContain("More than one person");
    expect(typedLine({ kind: "empty" })).toBe("");
  });

  it("adding takes a person once, in any case, and at most the server's cap", () => {
    const one = addAssignee(["ann@acme.test"], " ANN@acme.test ");
    expect(one).toEqual(["ann@acme.test"]);
    expect(addAssignee(["ann@acme.test"], "cat@acme.test")).toEqual(["ann@acme.test", "cat@acme.test"]);
    const full = Array.from({ length: MAX_ASSIGNEES }, (_, i) => `p${i}@acme.test`);
    expect(addAssignee(full, "extra@acme.test")).toBe(full);
    expect(fullLine(MAX_ASSIGNEES)).toMatch(/at most 50/);
    expect(fullLine(MAX_ASSIGNEES - 1)).toBe("");
    expect(addAssignee(["a@x.test"], `${"a".repeat(ASSIGNEE_MAX)}@x.test`)).toEqual(["a@x.test"]);
  });

  it("removing keeps the order and the text of the others", () => {
    expect(removeAssignee(["Ann@acme.test", "bob@acme.test"], "bob@acme.test")).toEqual(["Ann@acme.test"]);
    expect(removeAssignee(["ann@acme.test"], "nobody@acme.test")).toEqual(["ann@acme.test"]);
  });

  it("assigneeChanges names who came and who went", () => {
    const now = { ...ORIGINAL, assignees: ["bob@acme.test", "cat@acme.test"] };
    expect(assigneeChanges(ORIGINAL, now)).toEqual({ added: ["cat@acme.test"], removed: ["ann@acme.test"] });
    expect(changedSummary(ORIGINAL, { ...now, due: "" })).toBe(
      "Edited: no due date · added cat@acme.test · removed ann@acme.test.");
    expect(changedSummary(ORIGINAL, ORIGINAL)).toBe("");
  });

  it("warn-before-approve: an address that the directory does not know is named, a known one is not", () => {
    const known = new Set(["ann@acme.test", "bob@acme.test"]);
    const edits = { ...ORIGINAL, assignees: ["ann@acme.test", "typo@acme.tset", "agent:scout"] };
    expect(strangersOf(edits.assignees, known)).toEqual(["typo@acme.tset"]);
    expect(newStrangers(ORIGINAL, edits, known)).toEqual(["typo@acme.tset"]);
    // An original address that the directory does not know was on the card before: it is no new stranger.
    expect(newStrangers({ ...ORIGINAL, assignees: ["old@gone.test"] }, { ...ORIGINAL, assignees: ["old@gone.test"] }, known)).toEqual([]);
    expect(strangerLine(["typo@acme.tset"])).toBe("Not in the directory: typo@acme.tset. Check the address before you approve.");
    expect(strangerLine([])).toBe("");
  });

  it("a person typed but not added holds Approve, and says why", () => {
    expect(pendingLine("  Kim ")).toBe("Press Enter to add “Kim”, or clear the field.");
    expect(pendingLine("   ")).toBe("");
  });
});
