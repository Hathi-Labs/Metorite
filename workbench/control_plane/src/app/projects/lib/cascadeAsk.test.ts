/**
 * D-PM-38 decision 2 (Subtasks S5) — when a Projects status change asks.
 *
 * Every Projects door that can complete a task reads `completionAsk`: the
 * board's tick, its status menu, a drag into Done, the panel's status menu
 * and the table's status cell.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { completionAsk, writeCompletion } from "./cascadeAsk";

const LANES = [
  { id: "todo", category: "todo" },
  { id: "doing", category: "in_progress" },
  { id: "done", category: "done" },
  { id: "shipped", category: "done" },
  { id: "dropped", category: "cancelled" },
];

const parent = (status_id: string, subtasks = { done: 1, total: 4 }) => ({
  status_id,
  subtasks,
});

describe("completionAsk", () => {
  it("asks with the open count for a move into a done lane", () => {
    expect(completionAsk(parent("todo"), "done", LANES)).toBe(3);
    expect(completionAsk(parent("doing"), "shipped", LANES)).toBe(3);
  });

  it("does not ask when every subtask is closed, or there are none", () => {
    expect(completionAsk(parent("todo", { done: 2, total: 2 }), "done", LANES)).toBe(0);
    expect(completionAsk({ status_id: "todo" }, "done", LANES)).toBe(0);
  });

  it("does not ask for a move into cancelled, or into an open lane", () => {
    expect(completionAsk(parent("todo"), "dropped", LANES)).toBe(0);
    expect(completionAsk(parent("todo"), "doing", LANES)).toBe(0);
  });

  it("does not ask for a task that is already closed", () => {
    expect(completionAsk(parent("done"), "shipped", LANES)).toBe(0);
    expect(completionAsk(parent("done"), "done", LANES)).toBe(0);
  });

  it("takes a better count when the caller has one (the panel)", () => {
    expect(completionAsk({ status_id: "todo" }, "done", LANES, 2)).toBe(2);
  });
});

describe("every Projects door routes a status change through the prompt", () => {
  const SRC = fileURLToPath(new URL("..", import.meta.url));
  const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8");

  it.each([
    ["the board (tick, status menu, drag)", "page.tsx"],
    ["the task panel", "components/TaskBody.tsx"],
    ["the table's status cell", "components/TableView.tsx"],
  ])("%s", (_door, file) => {
    const src = read(file);
    expect(src).toMatch(/subtaskComplete\.changeStatus\(/);
    expect(src).toMatch(/\{subtaskComplete\.dialog\}/);
  });
});

describe("writeCompletion — the prior status comes from the server (D79)", () => {
  it("reads the server BEFORE the write, and records that status", async () => {
    const calls: string[] = [];
    const got = await writeCompletion(
      {
        // A teammate moved it to Review since the board loaded To do.
        read: async (id) => {
          calls.push(`read ${id}`);
          return { status_id: "review" };
        },
        write: async (id, sid, include) => {
          calls.push(`write ${id} ${sid} ${include}`);
          return {
            subtask_changes: [{ task_id: "k", from_status_id: "todo", to_status_id: "done" }],
          };
        },
      },
      "p",
      "done",
      true,
    );
    expect(calls).toEqual(["read p", "write p done true"]);
    expect(got.changes).toEqual([
      { task_id: "p", from_status_id: "review", to_status_id: "done" },
      { task_id: "k", from_status_id: "todo", to_status_id: "done" },
    ]);
  });

  it("records no parent entry when the server already held that status", async () => {
    const got = await writeCompletion(
      { read: async () => ({ status_id: "done" }), write: async () => ({}) },
      "p",
      "done",
      false,
    );
    expect(got.changes).toEqual([]);
  });

  it("the Projects hook takes its Undo record from it, never the board row", () => {
    const SRC = fileURLToPath(new URL("..", import.meta.url));
    const hook = readFileSync(`${SRC}/components/useSubtaskComplete.tsx`, "utf8");
    expect(hook).toMatch(/await writeCompletion\(io, task\.id, statusId, include\)/);
    expect(hook).not.toMatch(/from_status_id: task\.status_id/);
  });
});
